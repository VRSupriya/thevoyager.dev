#!/usr/bin/env python3
"""Collect candidate items for the AI Daily Brief.

Reads feeds/sources.yml, feeds/people.yml and feeds/youtube.yml, fetches every feed in
parallel, keeps items published within --since, removes duplicates and writes one JSON
file for the routine to rank. Each item gets a score (source weight + audience relevance
- noise penalty, see feeds/priorities.yml); irrelevant items are dropped and a shortlist with
one pick per slot, at most two from the same website, is prepared. A source that fails is listed under "failed" and skipped;
it never stops the run.

It also prepares:
- "newcomers": new models (Hugging Face, OpenRouter), trending repos, Show HN tools, Product Hunt
  products and library releases (feeds/sources.yml: discovery), plus names that appear in the
  news for the first time ("first_seen"), for the 🆕 New on the radar group.
- "quick_pool": good items that did not win a slot, grouped for ⚡ Also worth knowing.
- "deadlines_found": retirement dates published by OpenRouter, for the ⏰ calendar.
- "security_watch": every security incident or advisory in the window that touches AI tools or watched
  packages, whatever its score (feeds/priorities.yml: security_watch), plus OSV advisories (sources.yml: osv).

    pip install -r scripts/requirements.txt
    python3 scripts/fetch_feeds.py --since 36h --out /tmp/candidates.json
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import feedparser
import yaml

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "Mozilla/5.0 (compatible; TheVoyagerBriefBot/1.0; +https://thevoyager.dev)"
SUMMARY_CHARS = 400
DEFAULT_WEIGHT = 2


# ── Building the source list ─────────────────────────────────────────────────


def load_sources(feeds_dir: Path) -> tuple[list[dict], list[dict]]:
    """Return (feeds to fetch, pages the agent should check by hand). Discovery feeds have kind "discovery"."""
    sources = yaml.safe_load((feeds_dir / "sources.yml").read_text())
    people = yaml.safe_load((feeds_dir / "people.yml").read_text())
    youtube = yaml.safe_load((feeds_dir / "youtube.yml").read_text())

    feeds: list[dict] = []
    pages: list[dict] = []

    gn = sources.get("google_news", {})
    for q in gn.get("queries", []):
        ed = gn[q.get("edition", "global")]
        url = (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(q['q'])}&hl={ed['hl']}&gl={ed['gl']}&ceid={quote_plus(ed['ceid'])}"
        )
        feeds.append(
            {"name": f"Google News: {q['q']}", "url": url, "type": "rss", "section": q["section"], "kind": "news",
             "weight": q.get("weight", 1)}  # aggregator: low trust unless a query says otherwise
        )

    for s in sources.get("rss", []):
        entry = {
            "name": s["name"],
            "url": s["url"],
            "type": s.get("type", "rss"),
            "section": s.get("section", "core"),
            "kind": "source",
            "weight": s.get("weight", DEFAULT_WEIGHT),
            "require_ai": s.get("require_ai", False),
        }
        # Pages without a feed: the agent opens them with WebFetch instead.
        (pages if entry["type"] == "html" else feeds).append(entry)

    for p in people.get("people", []):
        if p.get("rss") and p.get("type") == "html":
            pages.append({"name": p["name"], "url": p["rss"], "type": "html", "section": "core", "kind": "people",
                          "weight": p.get("weight", DEFAULT_WEIGHT)})
        elif p.get("rss"):
            feeds.append(
                {"name": p["name"], "url": p["rss"], "type": "rss", "section": "core", "kind": "people", "x": p.get("x"),
                 "weight": p.get("weight", DEFAULT_WEIGHT)}
            )

    for c in youtube.get("channels", []):
        if c.get("tier") == "news":
            # YouTube's RSS feeds have returned 404 since mid-October 2026, so the channel's
            # videos page is read first and the RSS feed is only a fallback.
            feeds.append(
                {
                    "name": f"YouTube: {c['title']}",
                    "url": f"https://www.youtube.com/channel/{c['id']}/videos",
                    "rss_url": f"https://www.youtube.com/feeds/videos.xml?channel_id={c['id']}",
                    "type": "youtube",
                    "section": "core",
                    "kind": "youtube",
                    "weight": c.get("weight", DEFAULT_WEIGHT),
                }
            )

    for d in sources.get("discovery", []):
        feeds.append({"name": d["name"], "url": d["url"], "type": d.get("type", "rss"), "section": "discovery",
                      "kind": "discovery", "new_kind": d["kind"], "package": d.get("package"), "weight": 1})
    return feeds, pages


# ── Fetching and parsing ─────────────────────────────────────────────────────


def fetch(url: str, timeout: int) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def clean_text(value: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text[:SUMMARY_CHARS] + ("…" if len(text) > SUMMARY_CHARS else "")


def to_utc(struct) -> datetime | None:
    if not struct:
        return None
    return datetime(*struct[:6], tzinfo=timezone.utc)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


STABLE_MINOR = re.compile(r"^\d+\.\d+(\.0)?$")  # 1.4 or 1.4.0: a minor/major release, not a patch or pre-release


def parse_rss(body: bytes, src: dict) -> list[dict]:
    parsed = feedparser.parse(body)
    if not parsed.entries and (parsed.bozo or not parsed.version):
        raise ValueError(f"not a feed ({getattr(parsed, 'bozo_exception', 'no RSS/Atom found')})")
    items = []
    for e in parsed.entries:
        title = clean_text(e.get("title"))
        if src.get("package"):  # PyPI release feed: the title is just the version
            if not STABLE_MINOR.match(title):
                continue
            title = f"{src['package']} {title}"
        publisher = None
        if src["kind"] == "news":
            # Google News titles end with " - Publisher".
            publisher = (e.get("source") or {}).get("title")
            if publisher and title.endswith(f" - {publisher}"):
                title = title[: -len(publisher) - 3]
        items.append(
            {
                "title": title,
                "url": e.get("link"),
                "published": to_utc(e.get("published_parsed") or e.get("updated_parsed")),
                "summary": clean_text(e.get("summary") or e.get("description")),
                "publisher": publisher,
            }
        )
    return items


def parse_json(body: bytes, src: dict) -> list[dict]:
    data = json.loads(body)
    items = []
    if "hn.algolia.com" in src["url"]:
        for h in data.get("hits", []):
            items.append(
                {
                    "title": h.get("title"),
                    "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    "published": parse_iso(h.get("created_at")),
                    "summary": f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments on Hacker News",
                    "discussion": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                }
            )
    elif "huggingface.co/api/daily_papers" in src["url"]:
        for d in data:
            paper = d.get("paper", {})
            pid = paper.get("id")
            items.append(
                {
                    "title": d.get("title") or paper.get("title"),
                    "url": f"https://huggingface.co/papers/{pid}" if pid else None,
                    "published": parse_iso(d.get("publishedAt") or paper.get("publishedAt")),
                    "summary": clean_text(paper.get("summary")),
                    "upvotes": paper.get("upvotes"),
                    "arxiv": f"https://arxiv.org/abs/{pid}" if pid else None,
                }
            )
    elif "huggingface.co/api/models" in src["url"]:
        for m in data:
            mid = m.get("id") or m.get("modelId")
            items.append(
                {
                    "title": mid,
                    "url": f"https://huggingface.co/{mid}",
                    "published": parse_iso(m.get("createdAt")),
                    "summary": f"{m.get('pipeline_tag') or 'model'}; {m.get('likes', 0)} likes, "
                               f"{m.get('downloads', 0)} downloads (30 days) on Hugging Face",
                    "likes": m.get("likes", 0),
                    "hf_id": (mid or "").lower(),
                }
            )
    elif "openrouter.ai/api/v1/models" in src["url"]:
        seen = set()
        for m in data.get("data", []):
            mid = (m.get("id") or "").split(":")[0]  # "x:free" is the same model as "x"
            if not mid or mid in seen:
                continue
            seen.add(mid)
            price = m.get("pricing") or {}
            def per_m(v):
                try:
                    return f"${float(v) * 1e6:.2f}"
                except (TypeError, ValueError):
                    return "?"
            created = m.get("created")
            items.append(
                {
                    "title": re.sub(r"\s*\(free\)$", "", m.get("name") or mid),
                    "url": f"https://openrouter.ai/{mid}",
                    "published": datetime.fromtimestamp(created, timezone.utc) if created else None,
                    "summary": clean_text(m.get("description"))[:250]
                               + f" | context {m.get('context_length') or '?'} tokens; {per_m(price.get('prompt'))}/M input, "
                               f"{per_m(price.get('completion'))}/M output (OpenRouter)",
                    "hf_id": (m.get("hugging_face_id") or "").lower(),
                    "expiration_date": m.get("expiration_date"),
                    "model_id": mid,
                }
            )
    else:
        raise ValueError("no JSON parser for this source")
    return items


def fetch_source(src: dict, timeout: int) -> list[dict]:
    try:
        items = _fetch_and_parse(src, timeout)
    except Exception:
        time.sleep(3)  # one retry: feeds sometimes fail once and work a moment later
        items = _fetch_and_parse(src, timeout)
    for item in items:
        item.update(source=src["name"], section=src["section"], kind=src["kind"], weight=src.get("weight", DEFAULT_WEIGHT))
        if src.get("x"):
            item["x"] = src["x"]
        if src.get("new_kind"):
            item["new_kind"] = src["new_kind"]
    items = [i for i in items if i.get("title") and i.get("url")]
    if src.get("require_ai"):  # broad news source: only items about AI
        items = [i for i in items if AI_MENTION.search(f"{i['title']} {i.get('summary', '')}")]
    return items


AI_MENTION = re.compile(r"\b(AI|A\.I\.|GenAI|LLMs?|GPT|agents?|agentic|artificial intelligence|machine learning|"
                        r"generative|copilot|chatbot|model)\b", re.I)


def _fetch_and_parse(src: dict, timeout: int) -> list[dict]:
    if src["type"] == "youtube":
        try:
            items = parse_youtube_page(fetch(src["url"], timeout))
            if items:
                return items
        except Exception:
            pass
        return parse_rss(fetch(src["rss_url"], timeout), src)
    body = fetch(src["url"], timeout)
    return parse_json(body, src) if src["type"] == "json" else parse_rss(body, src)


AGO = re.compile(r"(\d+)\s*(seconds?|s|minutes?|mins?|m|hours?|h|days?|d|weeks?|w|months?|mo|years?|y)\b\s*ago", re.I)
AGO_UNITS = {"s": 1, "m": 60, "min": 60, "h": 3600, "d": 86400, "w": 604800, "mo": 2592000, "y": 31536000}


def ago_to_datetime(text: str | None) -> datetime | None:
    """'7h ago', '1d ago', '3 weeks ago', 'Streamed 2 days ago' -> an approximate UTC time."""
    m = AGO.search(text or "")
    if not m:
        return None
    unit = m.group(2).lower()
    key = "mo" if unit.startswith("mo") else "min" if unit.startswith("min") else unit[0]
    return datetime.now(timezone.utc) - timedelta(seconds=int(m.group(1)) * AGO_UNITS[key])


def parse_youtube_page(body: bytes) -> list[dict]:
    """Videos from a channel's /videos page (the ytInitialData JSON embedded in the HTML)."""
    m = re.search(rb"var ytInitialData = (\{.*?\});</script>", body, re.S)
    if not m:
        return []
    data = json.loads(m.group(1))
    items: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            if "lockupViewModel" in o:
                lock = o["lockupViewModel"]
                meta = lock.get("metadata", {}).get("lockupMetadataViewModel", {})
                parts = [p.get("text", {}).get("content", "")
                         for row in meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                         for p in row.get("metadataParts", [])]
                vid, title = lock.get("contentId"), meta.get("title", {}).get("content")
                if vid and title:
                    items.append({"title": clean_text(title), "url": f"https://www.youtube.com/watch?v={vid}",
                                  "published": next((ago_to_datetime(p) for p in parts if AGO.search(p or "")), None),
                                  "summary": f"{parts[0]} views" if parts else ""})
                return
            if "videoRenderer" in o:  # older page layout
                v = o["videoRenderer"]
                title = "".join(r.get("text", "") for r in v.get("title", {}).get("runs", []))
                items.append({"title": clean_text(title), "url": f"https://www.youtube.com/watch?v={v.get('videoId')}",
                              "published": ago_to_datetime(v.get("publishedTimeText", {}).get("simpleText")),
                              "summary": ""})
                return
            for val in o.values():
                walk(val)
        elif isinstance(o, list):
            for val in o:
                walk(val)

    walk(data)
    return items[:15]


# ── Filtering and de-duplication ─────────────────────────────────────────────

TRACKING = re.compile(r"^(utm_|ref$|ref_src$|fbclid$|gclid$|mc_[ce]id$|source$)")


def normalise_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."), path, query, ""))


def normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def load_covered(history_file: Path, days: int = 14) -> tuple[set[str], set[str]]:
    """URLs and titles the brief used in the last `days` days, so they are never offered again."""
    urls, titles = set(), set()
    if not history_file.exists():
        return urls, titles
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    for h in json.loads(history_file.read_text() or "[]"):
        if h.get("date", "") >= cutoff:
            if h.get("url"):
                urls.add(normalise_url(h["url"]))
            if h.get("title"):
                titles.add(normalise_title(h["title"]))
    return urls, titles


def select(items: list[dict], since: datetime, per_source: int) -> list[dict]:
    """Keep recent items (or undated ones, capped per source), newest first, without duplicates.

    Duplicates are removed within each section, so a story found by both a global and a
    Middle East query still reaches the regional block."""
    kept, seen_urls, seen_titles, per_source_count = [], set(), set(), {}
    undated_last = sorted(items, key=lambda i: i["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for item in undated_last:
        if item["published"] and item["published"] < since:
            continue
        url_key = (item["section"], normalise_url(item["url"]))
        title_key = (item["section"], normalise_title(item["title"]))
        if url_key in seen_urls or (len(title_key[1]) > 20 and title_key in seen_titles):
            continue
        count = per_source_count.get(item["source"], 0)
        if count >= per_source:
            continue
        per_source_count[item["source"]] = count + 1
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        kept.append(item)
    return kept


# ── Relevance scoring and the shortlist ──────────────────────────────────────


def load_priorities(feeds_dir: Path) -> dict:
    return yaml.safe_load((feeds_dir / "priorities.yml").read_text())


def hits(text: str, terms: list[str]) -> list[str]:
    """Terms found in text. Short terms (mcp, rag, moe, api...) must match as whole words."""
    found = []
    for t in terms:
        t = t.lower()
        pattern = rf"\b{re.escape(t)}\b" if len(t) <= 4 else re.escape(t)
        if re.search(pattern, text):
            found.append(t)
    return found


def site_of(item: dict) -> str:
    """The website an item really comes from (Google News links hide it behind a redirect)."""
    if item.get("publisher"):
        return item["publisher"].lower()
    return urlsplit(item["url"]).netloc.lower().removeprefix("www.")


def score_item(item: dict, prio: dict, now: datetime) -> None:
    text = f"{item['title']} {item.get('summary', '')}".lower()
    good = hits(text, prio["boost"]["terms"])
    bad = hits(text, prio["noise"]["terms"])
    score = item.get("weight", DEFAULT_WEIGHT) * prio["weight_points"]
    score += prio["boost"]["points"] * min(len(good), 5)
    score -= prio["noise"]["points"] * len(bad)
    news = hits(text, prio.get("news", {}).get("terms", []))
    score += prio.get("news", {}).get("points", 0) * min(len(news), 2)
    title = item["title"].lower()
    stale = [p for p in prio.get("evergreen", {}).get("patterns", []) if re.search(p, title)]
    score -= prio.get("evergreen", {}).get("points", 0) * min(len(stale), 1)
    if item.get("published"):  # up to +4 for items under a day old
        score += max(0.0, 4 - (now - item["published"]).total_seconds() / 21600)
    if item.get("upvotes"):  # Hugging Face daily papers
        score += min(item["upvotes"] / 20, 4)
    if item.get("discussion"):  # Hacker News points are in the summary text
        m = re.match(r"(\d+) points", item.get("summary", ""))
        if m:
            score += min(int(m.group(1)) / 100, 4)
    item["score"] = round(score, 1)
    item["relevance_hits"] = good[:5]
    if bad or stale:
        item["noise_hits"] = bad + ["evergreen"] * bool(stale)


def slot_bonus(item: dict, slot: dict) -> float:
    pref = slot.get("prefer", {})
    bonus = 0.0
    if item["kind"] in pref.get("kinds", []):
        bonus += 100  # a hard preference: "people" must come from followed people
    if any(s.lower() in item["source"].lower() for s in pref.get("sources", [])):
        bonus += 8
    text = f"{item['title']} {item.get('summary', '')}".lower()
    bonus += 3 * min(len(hits(text, pref.get("keywords", []))), 3)
    return bonus


def shortlist(candidates: list[dict], prio: dict) -> dict:
    """One pick per slot (at most domain_cap per website), plus a few runners-up each."""
    cap, n_alt = prio["domain_cap"], prio["alternates_per_slot"]
    taken: set[str] = set()
    offered: set[str] = set()  # urls already shown as a pick or an alternate in an earlier slot
    per_site: dict[str, int] = {}
    result: dict[str, dict] = {}
    # Strict slots (people, research) pick first, so "top" cannot take their only candidate.
    order = sorted(prio["slots"], key=lambda n: not prio["slots"][n].get("strict"))
    for name in order:
        slot = prio["slots"][name]
        ranked = sorted(
            (c for c in candidates if c["section"] == "core" and c["url"] not in taken),
            key=lambda c: c["score"] + slot_bonus(c, slot),
            reverse=True,
        )
        if slot.get("require_match"):  # a pick must at least match the slot's sources or keywords
            ranked = [c for c in ranked if slot_bonus(c, slot) > 0]
        if slot.get("strict"):  # no fallback: only items from the slot's own kinds/sources
            pref = slot.get("prefer", {})
            ranked = [
                c for c in ranked
                if c["kind"] in pref.get("kinds", [])
                or any(s.lower() in c["source"].lower() for s in pref.get("sources", []))
            ]
        unverifiable = set(prio.get("unverifiable_kinds", []))
        pick = next((c for c in ranked
                     if per_site.get(site_of(c), 0) < cap and c["kind"] not in unverifiable), None)
        entry: dict = {"pick": None, "alternates": []}
        if pick:
            taken.add(pick["url"])
            per_site[site_of(pick)] = per_site.get(site_of(pick), 0) + 1
            entry["pick"] = pick
            entry["alternates"] = [c for c in ranked
                                   if c is not pick and c["kind"] not in unverifiable and c["url"] not in offered][:n_alt]
            offered.update(a["url"] for a in entry["alternates"])
        result[name] = entry
    return {name: result[name] for name in prio["slots"]}  # keep the order from priorities.yml


# ── Trending topics ──────────────────────────────────────────────────────────
# A topic is "trending" when many different sources write about it, today or across
# several recent days, whatever the date of the original launch. Topics are names
# pulled from titles (capitalised words, product-like tokens and pairs of them), so
# nothing is hard-coded. Daily counts are kept in data/trend-history.json.

TREND_STOP = set("""
a an and are as at be by can could did do does for from has have how i in into is it its
new news not now of on or our out over than that the their they this to up us via vs was we
what when where which who why will with without you your after before more most just only
ai llm llms ml agent agents agentic model models open source data cloud api apis sdk tool tools
part one two three first using use used build building built make makes way ways guide intro
introducing introduces announcing announces launches launched launch release releases released
review update updates week today year day best top inside meet here every all still really
generally available preview beta presentation article podcast episode video interview
january february march april may june july august september october november december
monday tuesday wednesday thursday friday saturday sunday 2025 2026 2027
live ai-powered ai-driven ai-first powered
ipo series round funding valuation stake shares bank banks center centre studio concept module group
company companies startup startups government ministry minister partnership deal report reports
ceo cto founder president chairman executive
""".split())
# Names that appear every single day; they only count as part of a longer name ("Gemini 4 Argon").
TREND_ALWAYS = set("""
openai anthropic google microsoft meta amazon aws nvidia apple ibm oracle deepmind
claude chatgpt gpt gemini copilot llama hugging face huggingface github azure
india indian uae dubai abu dhabi saudi arabia riyadh qatar doha gcc mena middle east europe
european china chinese us usa uk america american africa asia global world
france french germany german japan japanese korea korean singapore canada brazil britain british
paris london berlin tokyo seoul beijing shanghai bengaluru bangalore mumbai delhi hyderabad
pro mini max ultra lite plus turbo nano flash air edge fast instant thinking reasoning beta preview
""".split())
# Product suffixes ("Pro", "Flash", "Mini") and places are not names on their own: they only count
# as part of a longer name ("Gemini 3.8 Flash", "MiniMax M3.1 Flash").


def generic_words(titles: list[str]) -> set[str]:
    """Ordinary vocabulary ("platform", "infrastructure"), not names ("DeepSeek", "Muse").

    Only sentence-case titles are informative (Title Case capitalises everything), so a word
    counts as generic when, inside those titles, it is written in lower case at least as often
    as capitalised (ignoring the first word)."""
    lower, upper = {}, {}
    for title in titles:
        words = re.findall(r"[A-Za-z][A-Za-z\-]+", title)
        if len(words) < 4 or sum(w[0].isupper() for w in words) / len(words) > 0.5:
            continue
        for w in words[1:]:
            bucket = lower if w.islower() else upper
            bucket[w.lower()] = bucket.get(w.lower(), 0) + 1
    return {w for w, n in lower.items() if n >= upper.get(w, 0)}


def story_clusters(window: list[dict], min_sites: int = 3) -> list[dict]:
    """Groups of items from different sites that share most of their key words: the same
    story covered by several outlets, which is itself a sign that it matters."""
    def key_words(title):
        return {w for w in re.findall(r"[a-z0-9][a-z0-9.\-]*", title.lower()) if len(w) >= 3 and w not in TREND_STOP}
    words = [key_words(i["title"]) for i in window]
    parent = list(range(len(window)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a in range(len(window)):
        for b in range(a + 1, len(window)):
            shared = words[a] & words[b]
            if len(shared) >= 3 and len(shared) / max(1, min(len(words[a]), len(words[b]))) >= 0.4:
                parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for i in range(len(window)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        sites = {site_of(window[i]) for i in members}
        if len(sites) >= min_sites:
            items = [window[i] for i in members]
            out.append({"story": items[0]["title"], "sites": len(sites), "items": items})
    return sorted(out, key=lambda c: -c["sites"])


_GENERIC: set[str] = set()


def title_terms(title: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+\-]*[A-Za-z0-9+]|[A-Za-z0-9]", title)
    keep = []
    for w in words:
        lw = w.lower()
        named = (w[0].isupper() and lw not in _GENERIC) or any(c.isdigit() for c in w) and any(c.isalpha() for c in w)
        keep.append(w if named and lw not in TREND_STOP and len(w) >= 3 or (named and any(c.isdigit() for c in w)) else None)
    terms = set()
    for i, w in enumerate(keep):
        if not w:
            continue
        if w.lower() not in TREND_ALWAYS and not w.isdigit():
            terms.add(w.lower())
        if i + 1 < len(keep) and keep[i + 1]:
            pair = f"{w} {keep[i + 1]}".lower()
            if not all(x in TREND_ALWAYS or x.isdigit() for x in pair.split()):
                terms.add(pair)
    return terms


def recent_brief_names(history_file: Path, days: int) -> set[str]:
    """Names from the titles the brief used in the last `days` days (today included)."""
    if not history_file.exists():
        return set()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date().isoformat()
    names: set[str] = set()
    for h in json.loads(history_file.read_text() or "[]"):
        # Quick hits (⚡) do not count: the vendor cap is about the six main items.
        if h.get("date", "") >= cutoff and h.get("title") and h.get("slot") != "quick":
            names |= {t for t in title_terms(h["title"]) if " " not in t}
    return names


# ── Newcomers and first-seen names ───────────────────────────────────────────


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def covered_keys(history_file: Path) -> list[str]:
    """Everything the brief has mentioned in its history (main items and quick hits), squashed for matching."""
    if not history_file.exists():
        return []
    return [squash(f"{h.get('title', '')} {h.get('url', '')}") for h in json.loads(history_file.read_text() or "[]")]


SIZE_TOKEN = re.compile(r"^(\d+(\.\d+)?[bmk]|a\d+(\.\d+)?b|\d+x\d+b)$")  # 27b, 501b, a23b: sizes, not versions


def mentioned_before(item: dict, covered: list[str]) -> bool:
    """True when the brief already named this model or project: the maker and the product name (and its
    version, when it has one, so "DeepSeek V4.2" is new after "DeepSeek V4.1") appear in one history entry."""
    full = item.get("hf_id") or item.get("model_id") or item["title"]
    owner, _, name = full.rpartition("/")
    if ": " in name:  # OpenRouter display names: "MiniMax: MiniMax M3.1 Flash"
        owner, name = name.split(": ", 1)
    tokens = [t for t in re.split(r"[^a-z0-9.]+", name.lower()) if t]
    if not tokens:
        return False
    first = squash(tokens[0])
    version = next((squash(t) for t in tokens[1:] if re.search(r"[a-z]", t) and re.search(r"\d", t)
                    and not SIZE_TOKEN.match(t)), "")
    owner_core = squash(re.split(r"[-_ ]", owner.lower())[0]) if owner else ""
    key = squash(name)
    for c in covered:
        if len(key) >= 8 and key[:24] in c:
            return True
        if first and len(first) >= 3 and first in c and (not owner_core or owner_core in c) and (not version or version in c):
            return True
    return False


def newcomer_key(item: dict) -> str:
    """'deepseek-ai/DeepSeek-V4.1-Flash-1017' and 'DeepSeek: DeepSeek V4.1 Flash' -> the part after the owner."""
    name = item.get("hf_id") or item.get("model_id") or item["title"]
    name = name.split("/")[-1].split(": ")[-1]
    return squash(name)


def build_newcomers(items: list[dict], cfg: dict, history_file: Path, seen_file: Path | None,
                    now: datetime, write_seen: bool) -> dict:
    """New models, repos, tools, products and releases that the brief has not covered yet."""
    ai_terms = cfg.get("ai_terms", [])
    skip = [re.compile(p, re.I) for p in cfg.get("skip_patterns", [])]
    max_age = {"model": cfg.get("model_max_age_days", 14), "model_api": cfg.get("model_max_age_days", 14),
               "release": cfg.get("release_max_age_days", 7)}
    other_age = cfg.get("other_max_age_days", 3)
    covered = covered_keys(history_file)
    seen = json.loads(seen_file.read_text()) if seen_file and seen_file.exists() else {}
    today = now.date().isoformat()

    by_kind: dict[str, list[dict]] = {}
    merged: dict[str, dict] = {}
    for it in items:
        kind = it.get("new_kind", "tool")
        text = f"{it['title']} {it.get('summary', '')}"
        if any(p.search(text) for p in skip):
            continue
        if kind in ("repo", "tool", "product") and not hits(text.lower(), ai_terms):
            continue
        if it["source"].startswith("Hugging Face") and (it.get("likes") or 0) < cfg.get("hf_min_likes", 60):
            continue  # tiny personal uploads
        published = it.get("published")
        age_limit = max_age.get(kind, other_age)
        if kind in ("model", "model_api", "release", "tool", "product"):
            if not published or published < now - timedelta(days=age_limit):
                continue
        key = newcomer_key(it)
        if kind == "release":
            key = squash(it["title"].rsplit(" ", 1)[0])  # one entry per package: the newest release
            if key in merged and (merged[key]["published"] or "") >= published.isoformat():
                continue
            if key in merged:
                by_kind[kind].remove(merged.pop(key))
        if len(key) < 3:
            continue
        # One entry per model even when Hugging Face and OpenRouter both list it.
        if key in merged:
            merged[key].setdefault("also_on", []).append({"source": it["source"], "url": it["url"],
                                                          "summary": it.get("summary", "")})
            continue
        entry = {
            "kind": kind, "name": it["title"], "url": it["url"], "source": it["source"],
            "published": published.isoformat() if published else None,
            "summary": it.get("summary", ""),
            "first_listed": seen.get(key, today),
            "already_in_brief": (squash(it["title"]) in " ".join(covered)) if kind == "release" else mentioned_before(it, covered),
        }
        if it.get("likes") is not None:
            entry["likes"] = it["likes"]
        merged[key] = entry
        by_kind.setdefault(kind, []).append(entry)

    for key, entry in merged.items():
        seen.setdefault(key, entry["first_listed"])
    if seen_file and write_seen:
        cutoff = (now - timedelta(days=45)).date().isoformat()
        seen_file.write_text(json.dumps({k: v for k, v in sorted(seen.items()) if v >= cutoff}, indent=0))

    out = {}
    per_kind = cfg.get("per_kind", 10)
    for kind, entries in by_kind.items():
        # Not yet in the brief first; then listed for the first time today; then the most liked
        # (models, on several platforms counts too) or the newest.
        def rank(e):
            newest = -datetime.fromisoformat(e["published"]).timestamp() if e["published"] else 0
            popularity = -((e.get("likes") or 0) + 200 * len(e.get("also_on", [])))
            return (e["already_in_brief"], e["first_listed"] < today, popularity if kind == "model" else newest)
        # For "model_api" (OpenRouter) there are no likes, so newest first.
        entries.sort(key=rank)
        out[kind] = entries[:per_kind]
    return out


def first_seen_names(window: list[dict], history: dict, today: str, query_words: set[str], top_n: int = 12) -> list[dict]:
    """Names that appear in the news today and on none of the previous days (up to 21): new players,
    new products, new models. Single words count only when they contain a digit ("M3.1") or appear
    on two or more sites; pairs ("Neural Concept") need just one site with a relevant item."""
    past = set()
    for d, terms in history.items():
        if d < today:
            past |= set(terms)
    found: dict[str, dict] = {}
    for it in window:
        if it["section"] not in ("core", "regional") or it["kind"] == "youtube":
            continue  # talk titles are mostly speaker names, not new players
        for term in title_terms(it["title"]):
            if term in past or any(w in query_words or w in TREND_STOP for w in term.split()):
                continue
            f = found.setdefault(term, {"term": term, "sites": set(), "items": [], "best": 0.0})
            f["sites"].add(site_of(it))
            f["best"] = max(f["best"], it.get("score", 0))
            if len(f["items"]) < 3:
                f["items"].append({"title": it["title"], "url": it["url"], "source": it.get("publisher") or it["source"]})
    out = []
    for term, f in found.items():
        multi = " " in term
        digits = any(c.isdigit() for c in term)
        if not multi and sum(c.isalpha() for c in term) < 2:
            continue  # "32b", "262k": sizes and context lengths, not names
        if (multi or digits) and (len(f["sites"]) >= 2 or f["best"] >= 15) or len(f["sites"]) >= 3:
            out.append({"term": term, "sites": len(f["sites"]), "items": f["items"], "best_score": f["best"]})
    # Prefer the longest name when a shorter one is part of it with the same reach.
    names = {o["term"]: o for o in out}
    out = [o for o in out if not any(x != o["term"] and o["term"] in x and names[x]["sites"] >= o["sites"] for x in names)]
    out.sort(key=lambda o: (-o["sites"], -o["best_score"]))
    return out[:top_n]


def quick_pool(candidates: list[dict], picks: dict, cfg: dict) -> dict:
    """⚡ Also worth knowing: relevant core items that did not win a slot, by category."""
    taken = {e["pick"]["url"] for e in picks.values() if e.get("pick")}
    used: set[str] = set()
    out: dict[str, dict] = {}
    for name, cat in cfg.get("categories", {}).items():
        rows = []
        for c in candidates:
            if c["section"] != "core" or c["url"] in taken or c["url"] in used or c["kind"] == "youtube":
                continue
            if c["score"] < cfg.get("min_score", 9) or not AI_MENTION.search(f"{c['title']} {c.get('summary', '')}"):
                continue
            if hits(f"{c['title']} {c.get('summary', '')}".lower(), cat.get("keywords", [])):
                rows.append(c)
                used.add(c["url"])
            if len(rows) >= cfg.get("per_category", 6):
                break
        out[name] = {"label": cat.get("label", name),
                     "items": [{"title": r["title"], "url": r["url"], "source": r.get("publisher") or r["source"],
                                "score": r["score"], "published": r["published"]} for r in rows]}
    return out


# ── Rising topics (concepts, not names) ──────────────────────────────────────


def concept_patterns(cfg: dict) -> dict[str, re.Pattern]:
    out = {}
    for label, phrases in (cfg.get("concepts") or {}).items():
        alts = "|".join(re.escape(p.lower()) for p in phrases)
        out[label] = re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])", re.I)
    return out


def rising_topics(items: list[dict], cfg: dict, history_file: Path | None, now: datetime, write: bool) -> dict:
    """Count, per concept, how many different sites mentioned it in the last `window_hours`, store the
    daily counts, and compare the last 7 days with the 7 before. Needs about two weeks of daily runs
    before "rising" means much; until then it still reports the most-discussed concepts."""
    pats = concept_patterns(cfg)
    since = now - timedelta(hours=cfg.get("window_hours", 24))
    today = now.date().isoformat()
    sites: dict[str, set[str]] = {}
    examples: dict[str, list[dict]] = {}
    for it in items:
        if not it.get("published") or it["published"] < since:
            continue
        text = f"{it['title']} {it.get('summary', '')}"
        for label, pat in pats.items():
            if pat.search(text):
                sites.setdefault(label, set()).add(site_of(it))
                ex = examples.setdefault(label, [])
                if len(ex) < 3 and all(e["url"] != it["url"] for e in ex):
                    ex.append({"title": it["title"], "url": it["url"], "source": it.get("publisher") or it["source"]})
    history = json.loads(history_file.read_text()) if history_file and history_file.exists() else {}
    history[today] = {label: len(s) for label, s in sorted(sites.items())}
    if history_file and write:
        keep = sorted(history)[-60:]
        history_file.write_text(json.dumps({d: history[d] for d in keep}, ensure_ascii=False, indent=0))

    days = sorted(d for d in history if d <= today)
    this_week = [d for d in days if d > (now - timedelta(days=7)).date().isoformat()]
    last_week = [d for d in days if (now - timedelta(days=14)).date().isoformat() < d <= (now - timedelta(days=7)).date().isoformat()]
    rows = []
    for label in pats:
        cur = sum(history[d].get(label, 0) for d in this_week)
        prev = sum(history[d].get(label, 0) for d in last_week)
        if cur == 0:
            continue
        status = "steady"
        if last_week:
            if prev == 0 and cur >= cfg.get("new_min_week", 4):
                status = "new"
            elif prev and cur >= cfg.get("min_week", 5) and cur >= cfg.get("growth", 1.5) * prev:
                status = "rising"
            elif prev and cur <= prev / cfg.get("growth", 1.5):
                status = "cooling"
        rows.append({"concept": label, "sites_this_week": cur, "sites_week_before": prev if last_week else None,
                     "sites_today": history[today].get(label, 0), "status": status, "examples_today": examples.get(label, [])})
    order = {"new": 0, "rising": 1, "steady": 2, "cooling": 3}
    rising = sorted((r for r in rows if r["status"] in ("new", "rising")),
                    key=lambda r: (order[r["status"]], -r["sites_this_week"]))
    top = sorted(rows, key=lambda r: -r["sites_this_week"])[: cfg.get("top_n", 10)]
    return {"days_of_history": len(days), "comparison_ready": len(last_week) >= 5,
            "rising": rising[: cfg.get("top_n", 10)], "most_discussed_this_week": top,
            "cooling": [r["concept"] for r in rows if r["status"] == "cooling"]}


def openrouter_deadlines(items: list[dict], now: datetime) -> list[dict]:
    out = []
    for it in items:
        exp = it.get("expiration_date")
        if exp and str(exp) >= now.date().isoformat():
            out.append({"date": str(exp)[:10], "what": f"{it['title']} leaves OpenRouter", "url": it["url"],
                        "source": "OpenRouter models API (check the provider's own page)"})
    return sorted(out, key=lambda d: d["date"])[:15]


def detect_trends(window: list[dict], everything: list[dict], history_file: Path | None,
                  today: str, query_words: set[str] = frozenset(), top_n: int = 8) -> list[dict]:
    history = json.loads(history_file.read_text()) if history_file and history_file.exists() else {}
    _GENERIC.clear()
    _GENERIC.update(generic_words([i["title"] for i in everything]))
    now_sources: dict[str, set[str]] = {}
    for it in window:
        for term in title_terms(it["title"]):
            now_sources.setdefault(term, set()).add(site_of(it))
    past_days = sorted(d for d in history if d < today)[-10:]
    trends = []
    for term, srcs in now_sources.items():
        if any(w in query_words for w in term.split()):
            continue  # the search query itself put this word in every result
        days = sum(1 for d in past_days if term in history[d])
        if len(past_days) >= 7 and days >= 0.9 * len(past_days):
            continue  # always there, so not a trend
        if len(srcs) >= 3 or (len(srcs) >= 2 and days >= 3):
            trends.append({"term": term, "sources_now": len(srcs), "days_seen_before": days,
                           "strength": len(srcs) * 2 + days})
    # Drop a single word when a longer name containing it is also trending with the same reach.
    names = {t["term"]: t for t in trends}
    trends = [t for t in trends if not any(o != t["term"] and t["term"] in o.split()
                                           and names[o]["sources_now"] >= t["sources_now"] for o in names)]
    trends.sort(key=lambda t: -t["strength"])
    trends = trends[:top_n]
    for t in trends:
        def match(it):
            return t["term"] in title_terms(it["title"])
        t["items"] = [{"title": i["title"], "url": i["url"], "source": i.get("publisher") or i["source"],
                       "published": i["published"].isoformat() if isinstance(i["published"], datetime) else i["published"]}
                      for i in window if match(i)][:5]
        # Older coverage from the feeds (outside the window) gives the background story.
        recent = datetime.fromisoformat(today).replace(tzinfo=timezone.utc) - timedelta(days=30)
        t["background"] = [{"title": i["title"], "url": i["url"], "source": i.get("publisher") or i["source"],
                            "published": i["published"].isoformat()}
                           for i in sorted((i for i in everything if i["published"] and i["published"] >= recent),
                                           key=lambda i: i["published"], reverse=True)
                           if match(i) and i not in window][:3]
    if history_file:
        history[today] = {term: sorted(srcs) for term, srcs in now_sources.items() if len(srcs) >= 1}
        keep_days = sorted(history)[-21:]
        history_file.write_text(json.dumps({d: history[d] for d in keep_days}, ensure_ascii=False, separators=(",", ":")))
    return trends


# ── Security watch ───────────────────────────────────────────────────────────
# Security incidents must never be lost to the relevance score: a supply-chain compromise reported by one
# low-weight outlet can score below the cut (LiteLLM 1.107.0 did on 2026-10-22). This list ignores the score.

OSV_QUERY = "https://api.osv.dev/v1/query"


def fetch_osv_package(ecosystem: str, name: str, since: datetime, timeout: int) -> list[dict]:
    body = json.dumps({"package": {"name": name, "ecosystem": ecosystem}}).encode()
    req = Request(OSV_QUERY, data=body, headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        vulns = json.loads(resp.read()).get("vulns", [])
    out = []
    for v in vulns:
        when = parse_iso(v.get("published")) or parse_iso(v.get("modified"))
        updated = parse_iso(v.get("modified"))
        if not when or max(when, updated or when) < since:
            continue
        ids = [v["id"], *v.get("aliases", [])]
        cve = next((i for i in ids if i.startswith("CVE-")), None)
        out.append({
            "title": f"{name} ({ecosystem}): {clean_text(v.get('summary') or v.get('details') or v['id'])[:160]}",
            "url": f"https://osv.dev/vulnerability/{v['id']}",
            "published": when.isoformat(),
            "modified": updated.isoformat() if updated else None,
            "id": v["id"], "cve": cve, "package": name, "ecosystem": ecosystem,
            "malicious": v["id"].startswith("MAL-"),
            "source": "OSV", "status": "advisory",
        })
    return out


def fetch_osv(cfg: dict, now: datetime, timeout: int, workers: int) -> tuple[list[dict], list[dict]]:
    """Advisories for watched packages published or updated in the last cfg['days'] days. Returns (items, failed)."""
    since = now - timedelta(days=cfg.get("days", 3))
    jobs = [(eco, name) for eco, names in (cfg.get("packages") or {}).items() for name in names]
    items, failed = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_osv_package, eco, name, since, timeout): (eco, name) for eco, name in jobs}
        for fut in as_completed(futures):
            eco, name = futures[fut]
            try:
                items.extend(fut.result())
            except Exception as exc:
                failed.append({"source": f"OSV {eco}/{name}", "url": OSV_QUERY, "error": f"{type(exc).__name__}: {exc}"[:200]})
    items.sort(key=lambda i: (i["malicious"], i["published"]), reverse=True)  # malicious first, then newest
    return items, failed


def security_watch(window: list[dict], scores: dict[str, tuple[float, str]], osv_items: list[dict],
                   cfg: dict, package_names: set[str]) -> list[dict]:
    """Incidents and advisories in the window, with their score and whether the score cut kept them."""
    patterns = [re.compile(p, re.I) for p in cfg.get("patterns", [])]
    pkg = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(package_names, key=len, reverse=True)) + r")\b", re.I) \
        if package_names else None
    seen: set[str] = set()
    out = []
    def when(i):
        v = i["published"]
        return parse_iso(v) if isinstance(v, str) else (v or datetime.min.replace(tzinfo=timezone.utc))
    for it in sorted(window, key=when, reverse=True):
        if any(it["source"].startswith(s) for s in cfg.get("skip_sources", [])):
            continue
        text = f"{it['title']} {it.get('summary', '')}"
        if not any(p.search(text) for p in patterns):
            continue
        if not (AI_MENTION.search(text) or (pkg and pkg.search(text)) or re.search(r"\bMCP\b|\bPyPI\b|\bnpm\b", text)):
            continue
        key = normalise_title(it["title"])
        if key in seen:
            continue
        seen.add(key)
        score, status = scores.get(it["url"], (None, "not scored"))
        out.append({"title": it["title"], "url": it["url"], "source": it.get("publisher") or it["source"],
                    "published": when(it).isoformat(),
                    "score": score, "status": status})
    return (osv_items + out)[: cfg.get("max_items", 25) + len(osv_items)]


# ── Main ─────────────────────────────────────────────────────────────────────


def parse_duration(value: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([hd])", value)
    if not m:
        raise argparse.ArgumentTypeError("use e.g. 36h or 2d")
    n = int(m.group(1))
    return timedelta(hours=n) if m.group(2) == "h" else timedelta(days=n)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=parse_duration, default="36h", help="how far back to look (36h, 2d)")
    ap.add_argument("--out", type=Path, default=Path("/tmp/candidates.json"))
    ap.add_argument("--feeds-dir", type=Path, default=ROOT / "feeds")
    ap.add_argument("--timeout", type=int, default=20, help="seconds per request")
    ap.add_argument("--per-source", type=int, default=15, help="max items kept from one source")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--history", type=Path, default=ROOT / "data" / "brief-history.json",
                    help="items already used in the last 14 days are dropped")
    ap.add_argument("--trend-history", type=Path, default=ROOT / "data" / "trend-history.json",
                    help="daily topic counts used to spot trends; pass 'none' to skip saving")
    ap.add_argument("--concept-history", type=Path, default=ROOT / "data" / "concept-history.json",
                    help="daily site counts per concept for rising topics; pass 'none' to skip saving")
    ap.add_argument("--newcomers-seen", type=Path, default=ROOT / "data" / "newcomers-seen.json",
                    help="first date each newcomer was listed; pass 'none' to skip saving")
    args = ap.parse_args(argv)

    started = time.time()
    now = datetime.now(timezone.utc)
    since = now - args.since
    feeds, pages = load_sources(args.feeds_dir)

    items, failed = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_source, src, args.timeout): src for src in feeds}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                items.extend(fut.result())
            except Exception as exc:  # one bad source never stops the run
                failed.append({"source": src["name"], "url": src["url"], "error": f"{type(exc).__name__}: {exc}"[:200]})

    sources_cfg = yaml.safe_load((args.feeds_dir / "sources.yml").read_text())
    osv_items, osv_failed = fetch_osv(sources_cfg.get("osv") or {}, now, args.timeout, args.workers) \
        if sources_cfg.get("osv") else ([], [])
    failed.extend(osv_failed)

    prio = load_priorities(args.feeds_dir)
    discovery = [i for i in items if i["section"] == "discovery"]
    items = [i for i in items if i["section"] != "discovery"]
    window = [i for i in items if i["published"] and i["published"] >= since]  # for the security watch (dates still datetimes)
    candidates = select(items, since, args.per_source)
    covered_urls, covered_titles = load_covered(args.history)
    already = [c for c in candidates
               if normalise_url(c["url"]) in covered_urls or normalise_title(c["title"]) in covered_titles]
    candidates = [c for c in candidates if c not in already]
    for c in candidates:
        score_item(c, prio, now)
    history_file = None if str(args.trend_history) == "none" else args.trend_history
    trend_hist_before = json.loads(args.trend_history.read_text()) if args.trend_history.exists() else {}
    gn = yaml.safe_load((args.feeds_dir / "sources.yml").read_text()).get("google_news", {})
    query_words = {w.lower() for q in gn.get("queries", []) for w in re.findall(r"[A-Za-z][A-Za-z\-]+", q["q"])} - {"or", "and", "when"}
    trends = detect_trends(candidates, items, history_file, now.date().isoformat(), query_words)
    bonus = prio.get("trend_bonus", 6)
    for c in candidates:
        hit = [tr["term"] for tr in trends if tr["term"] in title_terms(c["title"])]
        if hit:
            c["score"] = round(c["score"] + bonus, 1)
            c["trending"] = hit
    # Only stories that are relevant on their own merits get the coverage bonus.
    clusters = [cl for cl in story_clusters(candidates)
                if max(c["score"] for c in cl["items"]) >= prio["min_score"]
                and any(c.get("relevance_hits") for c in cl["items"])]
    for cl in clusters:
        best = max(cl["items"], key=lambda c: c["score"])
        for c in cl["items"]:
            c["covered_by_sites"] = cl["sites"]
        # The whole cluster's weight goes to its best item, so a widely covered story
        # from small outlets can still reach the shortlist.
        best["score"] = round(best["score"] + bonus + 2 * min(cl["sites"], 5), 1)
    clusters_out = [{"story": cl["story"], "sites": cl["sites"],
                     "items": [{"title": c["title"], "url": c["url"], "source": c["source"]} for c in cl["items"][:6]]}
                    for cl in clusters[:8]]
    dropped = [c for c in candidates if c["score"] < prio["min_score"]]
    candidates = sorted((c for c in candidates if c["score"] >= prio["min_score"]), key=lambda c: -c["score"])
    # Vendor cap: companies and products the brief featured in the last few days are pushed
    # down (after the relevance cut, so they stay visible), so one vendor can't fill the
    # brief day after day. Protocols and common words are exempt.
    recent_names = recent_brief_names(args.history, prio.get("repeat_days", 3)) - set(prio.get("repeat_exempt", []))
    for c in candidates:
        repeat = sorted(title_terms(c["title"]) & recent_names)
        if repeat:
            c["score"] = round(c["score"] - prio.get("repeat_penalty", 8), 1)
            c["recently_featured"] = repeat
    candidates.sort(key=lambda c: -c["score"])
    picks = shortlist(candidates, prio)
    regional = [c for c in candidates if c["section"] == "regional"]
    seen_file = None if str(args.newcomers_seen) == "none" else args.newcomers_seen
    newcomers = build_newcomers(discovery, prio.get("newcomers", {}), args.history, seen_file, now,
                                write_seen=seen_file is not None)
    newcomers["first_seen"] = first_seen_names(candidates, trend_hist_before, now.date().isoformat(), query_words)
    deadlines_found = openrouter_deadlines([d for d in discovery if d.get("expiration_date")], now)
    concept_file = None if str(args.concept_history) == "none" else args.concept_history
    rising = rising_topics(items, prio.get("rising", {}), concept_file or args.concept_history, now,
                           write=concept_file is not None)
    cov = prio.get("coverage", {})
    cov_since = now - timedelta(hours=cov.get("hours", 48))
    coverage_pool = [{"title": i["title"], "url": i["url"], "source": i["source"], "summary": i.get("summary", "")[:200],
                      "published": i["published"].isoformat()}
                     for i in sorted(items, key=lambda i: i["published"] or cov_since, reverse=True)
                     if i["source"] in cov.get("sources", []) and i["published"] and i["published"] >= cov_since
                     and (i["source"] != "Hacker News" or AI_MENTION.search(f"{i['title']} {i.get('summary', '')}"))]
    per_cov: dict[str, int] = {}
    coverage_pool = [c for c in coverage_pool
                     if per_cov.setdefault(c["source"], 0) < cov.get("per_source", 12)
                     and not per_cov.__setitem__(c["source"], per_cov[c["source"]] + 1)]
    for c in candidates + dropped:
        c["published"] = c["published"].isoformat() if c["published"] else None
    quick = quick_pool(candidates, picks, prio.get("quick_hits", {}))
    scores = {c["url"]: (c["score"], "candidate") for c in candidates}
    scores.update({d["url"]: (d["score"], "dropped by score") for d in dropped})
    scores.update({a["url"]: (None, "already in brief") for a in already})
    watched = {n for names in ((sources_cfg.get("osv") or {}).get("packages") or {}).values() for n in names}
    sec_watch = security_watch(window, scores, osv_items, prio.get("security_watch", {}), watched)

    by_section: dict[str, int] = {}
    for c in candidates:
        by_section[c["section"]] = by_section.get(c["section"], 0) + 1

    result = {
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "counts": {
            "sources": len(feeds),
            "sources_failed": len(failed),
            "items_fetched": len(items),
            "discovery_items": len(discovery),
            "candidates": len(candidates),
            "dropped_as_irrelevant": len(dropped),
            "dropped_already_covered": len(already),
            "by_section": by_section,
        },
        "trending": trends,
        "rising_topics": rising,
        "story_clusters": clusters_out,
        "shortlist": picks,
        "newcomers": newcomers,
        "quick_pool": quick,
        "coverage_pool": coverage_pool,
        "deadlines_found": deadlines_found,
        "security_watch": sec_watch,
        "regional": regional[:12],
        "candidates": candidates,
        "dropped_titles": [d["title"] for d in dropped][:40],
        "check_pages": pages,
        "deadline_pages": yaml.safe_load((args.feeds_dir / "sources.yml").read_text()).get("deadline_pages", []),
        "weekly_pages": yaml.safe_load((args.feeds_dir / "sources.yml").read_text()).get("weekly_pages", []),
        "failed": sorted(failed, key=lambda f: f["source"]),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1))

    print(
        f"{len(candidates)} candidates from {len(feeds) - len(failed)}/{len(feeds)} sources "
        f"({len(failed)} failed) in {time.time() - started:.1f}s → {args.out}",
        file=sys.stderr,
    )
    # Exit non-zero only when nothing at all worked, so the routine knows to rely on WebSearch.
    return 0 if candidates or len(failed) < len(feeds) else 2


if __name__ == "__main__":
    sys.exit(main())

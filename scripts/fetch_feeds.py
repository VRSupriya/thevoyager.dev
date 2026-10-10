#!/usr/bin/env python3
"""Collect candidate items for the AI Daily Brief.

Reads feeds/sources.yml, feeds/people.yml and feeds/youtube.yml, fetches every feed in
parallel, keeps items published within --since, removes duplicates and writes one JSON
file for the routine to rank. Each item gets a score (source weight + audience relevance
- noise penalty, see feeds/priorities.yml); irrelevant items are dropped and a shortlist with
one pick per slot, at most two from the same website, is prepared. A source that fails is listed under "failed" and skipped;
it never stops the run.

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
    """Return (feeds to fetch, pages the agent should check by hand)."""
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
            feeds.append(
                {
                    "name": f"YouTube: {c['title']}",
                    "url": f"https://www.youtube.com/feeds/videos.xml?channel_id={c['id']}",
                    "type": "rss",
                    "section": "core",
                    "kind": "youtube",
                    "weight": c.get("weight", DEFAULT_WEIGHT),
                }
            )
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


def parse_rss(body: bytes, src: dict) -> list[dict]:
    parsed = feedparser.parse(body)
    if not parsed.entries and (parsed.bozo or not parsed.version):
        raise ValueError(f"not a feed ({getattr(parsed, 'bozo_exception', 'no RSS/Atom found')})")
    items = []
    for e in parsed.entries:
        title = clean_text(e.get("title"))
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
    return [i for i in items if i.get("title") and i.get("url")]


def _fetch_and_parse(src: dict, timeout: int) -> list[dict]:
    body = fetch(src["url"], timeout)
    return parse_json(body, src) if src["type"] == "json" else parse_rss(body, src)


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
    if bad:
        item["noise_hits"] = bad


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

    prio = load_priorities(args.feeds_dir)
    candidates = select(items, since, args.per_source)
    covered_urls, covered_titles = load_covered(args.history)
    already = [c for c in candidates
               if normalise_url(c["url"]) in covered_urls or normalise_title(c["title"]) in covered_titles]
    candidates = [c for c in candidates if c not in already]
    for c in candidates:
        score_item(c, prio, now)
    dropped = [c for c in candidates if c["score"] < prio["min_score"]]
    candidates = sorted((c for c in candidates if c["score"] >= prio["min_score"]), key=lambda c: -c["score"])
    picks = shortlist(candidates, prio)
    regional = [c for c in candidates if c["section"] == "regional"]
    for c in candidates + dropped:
        c["published"] = c["published"].isoformat() if c["published"] else None

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
            "candidates": len(candidates),
            "dropped_as_irrelevant": len(dropped),
            "dropped_already_covered": len(already),
            "by_section": by_section,
        },
        "shortlist": picks,
        "regional": regional[:12],
        "candidates": candidates,
        "dropped_titles": [d["title"] for d in dropped][:40],
        "check_pages": pages,
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

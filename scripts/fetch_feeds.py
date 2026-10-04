#!/usr/bin/env python3
"""Collect candidate items for the AI Daily Brief.

Reads feeds/sources.yml, feeds/people.yml and feeds/youtube.yml, fetches every feed in
parallel, keeps items published within --since, removes duplicates and writes one JSON
file for the routine to rank. A source that fails is listed under "failed" and skipped;
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
            {"name": f"Google News: {q['q']}", "url": url, "type": "rss", "section": q["section"], "kind": "news"}
        )

    for s in sources.get("rss", []):
        entry = {
            "name": s["name"],
            "url": s["url"],
            "type": s.get("type", "rss"),
            "section": s.get("section", "core"),
            "kind": "source",
        }
        # Pages without a feed: the agent opens them with WebFetch instead.
        (pages if entry["type"] == "html" else feeds).append(entry)

    for p in people.get("people", []):
        if p.get("rss"):
            feeds.append(
                {"name": p["name"], "url": p["rss"], "type": "rss", "section": "core", "kind": "people", "x": p.get("x")}
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
    body = fetch(src["url"], timeout)
    items = parse_json(body, src) if src["type"] == "json" else parse_rss(body, src)
    for item in items:
        item.update(source=src["name"], section=src["section"], kind=src["kind"])
        if src.get("x"):
            item["x"] = src["x"]
    return [i for i in items if i.get("title") and i.get("url")]


# ── Filtering and de-duplication ─────────────────────────────────────────────

TRACKING = re.compile(r"^(utm_|ref$|ref_src$|fbclid$|gclid$|mc_[ce]id$|source$)")


def normalise_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."), path, query, ""))


def normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


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

    candidates = select(items, since, args.per_source)
    for c in candidates:
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
            "by_section": by_section,
        },
        "candidates": candidates,
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

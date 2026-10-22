#!/usr/bin/env python3
"""Compact, complete view of /tmp/candidates.json for the AI Daily Brief routine.

    python3 scripts/candidates_digest.py                 # the digest: every section, every candidate title
    python3 scripts/candidates_digest.py --item i42      # full record of one item (url, summary, all fields)
    python3 scripts/candidates_digest.py --search llm-d  # every item whose title/summary mentions the text
    python3 scripts/candidates_digest.py --section newcomers   # one section as full JSON

The raw file is ~100k tokens, mostly long Google News URLs and summaries repeated across sections.
Reading it whole makes every later step of the run re-read it. The digest keeps every section and
every item (ranked candidates as one line each), and gives each item an id: look up anything you
need in full with --item, --search or --section. Nothing is filtered out; only the detail moves
behind a lookup.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT = Path("/tmp/candidates.json")


def short(text: str | None, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def day(ts: str | None) -> str:
    return (ts or "")[5:10] or "--"


def is_gn(url: str) -> bool:
    return "news.google.com" in (url or "")


def origin(item: dict) -> str:
    pub = item.get("publisher") or ""
    src = item.get("source") or ""
    if src.startswith("Google News") or is_gn(item.get("url", "")):
        return short(re.sub(r"^https?://(www\.)?", "", pub).rstrip("/") or "Google News", 30) + " via GN"
    return short(src, 30)


class Registry:
    """Gives every item, in any section, a stable id (same URL = same id)."""

    def __init__(self) -> None:
        self.by_url: dict[str, str] = {}
        self.items: dict[str, dict] = {}

    def add(self, item: dict) -> str:
        url = item.get("url") or f"noURL:{item.get('title') or item.get('name')}"
        if url not in self.by_url:
            rid = f"i{len(self.by_url) + 1}"
            self.by_url[url] = rid
            self.items[rid] = item
        else:  # keep the richest record
            rid = self.by_url[url]
            if len(json.dumps(item)) > len(json.dumps(self.items[rid])):
                self.items[rid] = item
        return self.by_url[url]

    def line(self, item: dict, title_len: int = 110, with_url: bool = True) -> str:
        rid = self.add(item)
        title = item.get("title") or item.get("name") or ""
        parts = [f"[{rid}]"]
        if "score" in item:
            parts.append(f"{item['score']}")
        parts.append(day(item.get("published")))
        parts.append(short(title, title_len))
        parts.append(f"({origin(item)})")
        flags = []
        if item.get("trending"):
            flags.append("trend:" + ",".join(item["trending"]))
        if item.get("covered_by_sites"):
            flags.append(f"{item['covered_by_sites']} sites")
        if item.get("recently_featured"):
            flags.append("recently_featured:" + ",".join(item["recently_featured"]))
        if item.get("already_in_brief"):
            flags.append("already_in_brief")
        if flags:
            parts.append("{" + "; ".join(flags) + "}")
        url = item.get("url") or ""
        if with_url and url and not is_gn(url):
            parts.append(re.sub(r"[?&]utm_[^&]*", "", url).rstrip("?&"))
        return " ".join(parts)


def walk_items(d: dict):
    """Every item-like dict in the file, for the registry."""
    for c in d.get("candidates", []):
        yield c
    for slot in (d.get("shortlist") or {}).values():
        if slot.get("pick"):
            yield slot["pick"]
        yield from slot.get("alternates", [])
    for kind, group in (d.get("newcomers") or {}).items():
        for n in group:
            if "items" in n and "term" in n:  # first_seen: a new name with the items that mention it
                yield from n["items"]
            else:
                yield n
    for group in (d.get("quick_pool") or {}).values():
        yield from group.get("items", [])
    for key in ("trending", "story_clusters"):
        for t in d.get(key, []):
            yield from t.get("items", [])
            yield from t.get("background", [])
    for key in ("coverage_pool", "regional", "deadlines_found"):
        yield from d.get(key, [])


def digest(d: dict) -> str:
    reg = Registry()
    for it in walk_items(d):  # ids follow candidate rank first
        reg.add(it)
    out: list[str] = []
    c = d.get("counts", {})
    out.append(f"# Candidates digest (window since {d.get('since', '')[:16]} UTC). Read ALL of it, to the last line.")
    out.append(f"counts: {json.dumps(c)}")
    out.append("Ids work with --item/--search. URLs are shown except Google News redirect links (use --item, or find the primary source).")
    out.append("\n## failed (cover these with WebSearch)")
    out.append("; ".join(f"{f['source']} ({short(f['error'], 60)})" for f in d.get("failed", [])) or "none")

    out.append("\n## shortlist (pick, then alternates)")
    for name, slot in (d.get("shortlist") or {}).items():
        pick = slot.get("pick")
        out.append(f"{name}: " + (reg.line(pick) if pick else "-- no pick --"))
        for a in slot.get("alternates", []):
            out.append("   alt " + reg.line(a))

    out.append("\n## newcomers")
    for kind, group in (d.get("newcomers") or {}).items():
        out.append(f"### {kind} ({len(group)})")
        for n in group:
            if "items" in n and "term" in n:  # first_seen: a name new this window, with what mentions it
                out.append(f"**{n['term']}**: {n.get('sites')} sites, best score {n.get('best_score')}")
                out += ["  " + reg.line(i, 95) for i in n.get("items", [])]
                continue
            extra = short(n.get("summary"), 150)
            out.append(reg.line(n, 80) + (f" | {extra}" if extra else "") + f" | first_listed {n.get('first_listed', '')}")

    out.append("\n## trending (names many sites write about; background = older coverage)")
    for t in d.get("trending", []):
        out.append(f"**{t['term']}**: {t.get('sources_now')} sites now, seen {t.get('days_seen_before')} previous days")
        out += ["  " + reg.line(i, 95) for i in t.get("items", [])]
        out += ["  bg " + reg.line(i, 90) for i in t.get("background", [])]

    rt = d.get("rising_topics") or {}
    if rt:
        out.append("\n## rising_topics")
        out.append(json.dumps({k: v for k, v in rt.items()}, ensure_ascii=False))

    out.append("\n## story_clusters (one story, 3+ outlets)")
    for cl in d.get("story_clusters", []):
        out.append(f"- {cl.get('sites')} sites: {short(cl.get('story'), 110)}")
        out += ["  " + reg.line(i, 95) for i in cl.get("items", [])]

    out.append("\n## quick_pool (for ⚡)")
    for key, group in (d.get("quick_pool") or {}).items():
        out.append(f"### {group.get('label', key)}")
        out += [reg.line(i) for i in group.get("items", [])]

    out.append("\n## coverage_pool (step 6)")
    out += [reg.line(i) + (f" | {short(i.get('summary'), 80)}" if i.get("summary") else "") for i in d.get("coverage_pool", [])]

    out.append("\n## deadlines_found (confirm on the provider's page)")
    out += [f"- [{reg.add(x)}] {x.get('date')} {short(x.get('what'), 110)} {x.get('url', '')}" for x in d.get("deadlines_found", [])]

    out.append("\n## regional")
    out += [reg.line(i) for i in d.get("regional", [])]

    for key in ("check_pages", "deadline_pages", "weekly_pages"):
        if d.get(key):
            out.append(f"\n## {key}")
            out += [f"- {p['name']}: {p['url']}" for p in d[key]]

    cands = d.get("candidates", [])
    out.append(f"\n## all candidates, ranked ({len(cands)}; one line each, no URL: use --item for detail)")
    for cnd in cands:
        tag = "R " if cnd.get("section") == "regional" else ""
        out.append(tag + reg.line(cnd, 120, with_url=False))

    if d.get("dropped_titles"):
        out.append("\n## dropped_titles (scored irrelevant; skim for false negatives)")
        out += [f"- {short(t, 130)}" for t in d["dropped_titles"]]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=DEFAULT)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--item", help="id from the digest, e.g. i42")
    g.add_argument("--search", help="case-insensitive text to find in titles, names and summaries")
    g.add_argument("--section", help="print one top-level section as full JSON")
    args = ap.parse_args()
    d = json.loads(args.file.read_text())

    if args.section:
        print(json.dumps(d.get(args.section), ensure_ascii=False, indent=1))
        return 0
    reg = Registry()
    for it in walk_items(d):
        reg.add(it)
    if args.item:
        rec = reg.items.get(args.item)
        if not rec:
            print(f"no item {args.item}", file=sys.stderr)
            return 1
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    if args.search:
        q = args.search.lower()
        hits = [r for r in reg.items.values()
                if q in (" ".join(str(r.get(k, "")) for k in ("title", "name", "summary"))).lower()]
        for r in hits:
            print(reg.line(r, 140) + (f" | {short(r.get('summary'), 160)}" if r.get("summary") else ""))
        print(f"({len(hits)} match{'es' if len(hits) != 1 else ''})")
        return 0
    print(digest(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())

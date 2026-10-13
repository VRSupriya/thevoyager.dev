#!/usr/bin/env python3
"""Write a readable log of everything the AI Daily Brief considered on one day.

    python3 scripts/candidates_report.py DATE [--candidates /tmp/candidates.json] [--notes /tmp/editor-notes.md]

Output: data/candidates/DATE.md (logs older than 30 days are deleted).
It shows each slot's shortlist, the full ranked pool with scores, the regional pool,
what the scoring filtered out, and the editor's notes (why each pick won, what was
found by web search). Items that made it into the published brief are marked ✅.
Read it to see what the routine saw, then steer it in config/brief-feedback.md.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEEP_DAYS = 30
POOL_ROWS = 60


def cell(text: str, n: int = 90) -> str:
    text = re.sub(r"\s+", " ", text or "").replace("|", "/").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def row(c: dict, used: set[str]) -> str:
    mark = "✅" if c["url"] in used else ""
    when = (c.get("published") or "")[:16].replace("T", " ")
    hits = ", ".join(c.get("relevance_hits", [])[:4])
    noise = ", ".join(c.get("noise_hits", [])[:3])
    return (f"| {mark} | {c.get('score', '')} | [{cell(c['title'])}]({c['url']}) | {cell(c['source'], 40)} "
            f"| {when} | {hits}{' / −' + noise if noise else ''} |")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("date")
    ap.add_argument("--candidates", type=Path, default=Path("/tmp/candidates.json"))
    ap.add_argument("--notes", type=Path, default=Path("/tmp/editor-notes.md"))
    args = ap.parse_args()

    d = json.loads(args.candidates.read_text())
    post = ROOT / "src" / "content" / "brief" / f"{args.date}.md"
    used = set(re.findall(r"\]\((https?://[^)\s]+)\)", post.read_text())) if post.exists() else set()
    c = d["counts"]

    out = [
        f"# Candidates considered — {args.date}",
        "",
        f"Feeds: {c['sources'] - c['sources_failed']}/{c['sources']} sources loaded, {c['items_fetched']} items fetched, "
        f"{c['candidates']} kept after scoring, {c['dropped_as_irrelevant']} filtered as irrelevant, "
        f"{c['dropped_already_covered']} already covered. Window: since {d['since'][:16].replace('T', ' ')} UTC.",
        "",
        "✅ = used in the published brief. Score = source weight + relevance terms − noise terms (feeds/priorities.yml).",
        "To steer future picks, write in config/brief-feedback.md.",
        "",
    ]
    if d.get("failed"):
        out += ["**Sources that failed:** " + ", ".join(f["source"] for f in d["failed"]), ""]

    out += ["## Editor's notes", ""]
    out += [args.notes.read_text().strip() if args.notes.exists() else "_No notes were written for this run._", ""]

    out += ["## Shortlist per slot (the script's suggestion before editing)", ""]
    for name, entry in d["shortlist"].items():
        pick = entry.get("pick")
        out.append(f"**{name}**: " + (f"[{cell(pick['title'], 100)}]({pick['url']}) ({pick['source']}, {pick['score']})"
                                      + (" ✅" if pick["url"] in used else "") if pick else "_no pick_"))
        for a in entry.get("alternates", []):
            out.append(f"- alt: [{cell(a['title'], 100)}]({a['url']}) ({a['source']}, {a['score']})"
                       + (" ✅" if a["url"] in used else ""))
        out.append("")

    head = ["| | Score | Title | Source | Published (UTC) | Matched terms |", "|---|---|---|---|---|---|"]
    core = [x for x in d["candidates"] if x["section"] == "core"]
    out += [f"## Ranked pool: top {min(POOL_ROWS, len(core))} of {len(core)} core candidates", "", *head]
    out += [row(x, used) for x in core[:POOL_ROWS]] + [""]
    out += ["## Regional pool (Middle East and India)", "", *head]
    out += [row(x, used) for x in d.get("regional", [])] + [""]
    out += ["## Filtered out by the scoring (check for false negatives)", ""]
    out += [f"- {cell(t, 140)}" for t in d.get("dropped_titles", [])] + [""]
    if d.get("check_pages"):
        out += ["## Pages without a feed (checked by hand)", ""]
        out += [f"- [{p['name']}]({p['url']})" for p in d["check_pages"]] + [""]

    folder = ROOT / "data" / "candidates"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{args.date}.md").write_text("\n".join(out))
    cutoff = (date.fromisoformat(args.date) - timedelta(days=KEEP_DAYS)).isoformat()
    for old in folder.glob("*.md"):
        if old.stem < cutoff:
            old.unlink()
    print(f"wrote {folder / (args.date + '.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

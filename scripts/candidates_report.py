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

    if d.get("trending"):
        out += ["## Trending topics (many sources, or several days in a row; launch date doesn't matter)", ""]
        for tr in d["trending"]:
            out.append(f"**{tr['term']}**: {tr['sources_now']} sites in this window, seen on {tr['days_seen_before']} of the previous days")
            out += [f"- [{cell(i['title'], 110)}]({i['url']}) ({i['source']})" for i in tr.get("items", [])]
            out += [f"- background ({(i.get('published') or '')[:10]}): [{cell(i['title'], 100)}]({i['url']})" for i in tr.get("background", [])]
            out.append("")
    if d.get("story_clusters"):
        out += ["## Same story, several outlets", ""]
        for cl in d["story_clusters"]:
            out.append(f"- **{cl['sites']} sites**: {cell(cl['story'], 120)}")
        out.append("")

    out += ["## Shortlist per slot (the script's suggestion before editing)", ""]
    for name, entry in d["shortlist"].items():
        pick = entry.get("pick")
        out.append(f"**{name}**: " + (f"[{cell(pick['title'], 100)}]({pick['url']}) ({pick['source']}, {pick['score']})"
                                      + (" ✅" if pick["url"] in used else "") if pick else "_no pick_"))
        for a in entry.get("alternates", []):
            out.append(f"- alt: [{cell(a['title'], 100)}]({a['url']}) ({a['source']}, {a['score']})"
                       + (" ✅" if a["url"] in used else ""))
        out.append("")

    nc = d.get("newcomers") or {}
    if nc:
        out += ["## 🆕 Newcomers (new models, repos, tools, releases; ✅ = used, 📌 = the brief named it before)", ""]
        labels = {"model_api": "New hosted models (OpenRouter)", "model": "New open-weight models (Hugging Face)",
                  "repo": "Trending repos", "tool": "Show HN", "product": "Product Hunt (AI)", "release": "Library releases"}
        for kind, label in labels.items():
            rows = nc.get(kind) or []
            if not rows:
                continue
            out.append(f"**{label}**")
            for e in rows:
                mark = " ✅" if e["url"] in used else " 📌" if e.get("already_in_brief") else ""
                extra = f", {e['likes']} likes" if e.get("likes") else ""
                also = f", also on {', '.join(a['source'] for a in e['also_on'])}" if e.get("also_on") else ""
                out.append(f"- [{cell(e['name'], 90)}]({e['url']}) ({(e.get('published') or '')[:10]}{extra}{also}, "
                           f"first listed {e.get('first_listed')}){mark}")
            out.append("")
        if nc.get("first_seen"):
            out.append("**Names in the news for the first time** (not seen in the previous 3 weeks)")
            for f in nc["first_seen"]:
                first = f["items"][0] if f.get("items") else None
                out.append(f"- **{f['term']}**: {f['sites']} site(s)" + (f"; e.g. [{cell(first['title'], 90)}]({first['url']})" if first else ""))
            out.append("")

    qp = d.get("quick_pool") or {}
    if any(v.get("items") for v in qp.values()):
        out += ["## ⚡ Quick-hits pool (relevant items that did not win a slot)", ""]
        for v in qp.values():
            if not v.get("items"):
                continue
            out.append(f"**{v['label']}**")
            out += [f"- [{cell(i['title'], 100)}]({i['url']}) ({i['source']}, {i['score']})" + (" ✅" if i["url"] in used else "")
                    for i in v["items"]]
            out.append("")

    if d.get("deadlines_found"):
        out += ["## ⏰ Retirement dates seen on OpenRouter (confirm on the provider's page)", ""]
        out += [f"- {x['date']}: [{cell(x['what'], 90)}]({x['url']})" for x in d["deadlines_found"]] + [""]

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
    # Measured from today's real date, so a test run with an odd DATE never deletes real logs.
    cutoff = (min(date.fromisoformat(args.date), date.today()) - timedelta(days=KEEP_DAYS)).isoformat()
    for old in folder.glob("*.md"):
        if old.stem < cutoff:
            old.unlink()
    print(f"wrote {folder / (args.date + '.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

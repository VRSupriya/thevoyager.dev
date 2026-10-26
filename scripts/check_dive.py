#!/usr/bin/env python3
"""Check a Deep Dives post before it is committed.

    python3 scripts/check_dive.py src/content/dives/system/2026-10-28.md
    python3 scripts/check_dive.py src/content/dives/*/*.md      # several at once

Errors (exit 1) are rule breaks the routine must fix before committing.
Warnings (exit 0) are signals worth a second look.

Every track: front matter, three takeaways, at least one visual, valid widget JSON, related ids that exist,
no emoji in headings, no narration of the writer's own process.
Wednesday system posts also get the full structure check from config/dives-playbook.md: the 14 fixed
headings in order, both diagrams, the requirements and components tables, 3-4 ADRs, the cost calculator,
the exercise answer, the template, and a length that leaves nothing out.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DIVES = ROOT / "src" / "content" / "dives"

REQUIRED_FM = ["title", "date", "track", "week", "phase", "time_minutes", "summary", "telegram", "tags"]
# Widgets whose body is JSON (stats and terminal are plain text). Same list as src/plugins/widgets.mjs.
JSON_WIDGETS = {"steps", "flow", "calc", "chart", "compare", "timer", "curve", "quiz"}
VISUALS = JSON_WIDGETS | {"mermaid", "stats", "terminal"}
PROCESS_LEAKS = [r"\bI couldn'?t\b", r"\bI could (read|open|find|see)\b", r"\bI was unable\b",
                 r"\bwhen I opened\b", r"\ba search didn'?t\b", r"\bsearch snippets?\b",
                 r"\b(?:I|we) (?:used|ran) Web(?:Fetch|Search)\b", r"\breaches me\b", r"\bVendor note\b",
                 r"\bI (?:checked|searched)\b", r"\bdidn'?t turn up\b", r"\bwhen (?:preparing|writing) this\b"]
LEADING_EMOJI = re.compile(r"^[\U0001F000-\U0001FAFF☀-➿]")

# Wednesday system posts: (heading prefix, word guide). Order matters. From config/dives-playbook.md.
SYSTEM_SECTIONS = [
    ("The business case", 220),
    ("Do we need AI here?", 100),
    ("Requirements", 250),
    ("High-level design", 400),
    ("Components and alternatives", 300),
    ("Deep dive:", 800),
    ("Key decisions", 450),
    ("How we know it works", 250),
    ("Sizing, cost and value", 250),
    ("From design to delivery", 300),
    ("How others built it", 250),
    ("Explain it in 3 sentences", 0),
    ("Exercise: find the flaws", 200),
    ("Template:", 150),
]
SYSTEM_MIN_WORDS = 2600      # below this, something required is almost certainly missing
SYSTEM_MAX_WORDS = 4600      # a soft ceiling (warning only): completeness beats length
SECTION_THIN = 0.4           # warn when a section has under 40% of its word guide
MIN_COMPONENTS = 6


# ── Parsing ──────────────────────────────────────────────────────────────────


def split_post(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise ValueError("no front matter")
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


def blocks(body: str) -> list[tuple[str, str]]:
    """Fenced code blocks as (language, content)."""
    out, lang, buf = [], None, []
    for line in body.split("\n"):
        fence = re.match(r"^\s*(```|~~~)\s*([\w-]*)\s*(.*)$", line)
        if lang is None and fence:
            # Widgets may put their JSON on the fence line itself: ```chart {"title": ...}
            lang, buf = fence.group(2).lower(), [fence.group(3)] if fence.group(3) else []
        elif lang is not None and re.match(r"^\s*(```|~~~)\s*$", line):
            out.append((lang, "\n".join(buf)))
            lang = None
        elif lang is not None:
            buf.append(line)
    return out


def sections(body: str) -> list[tuple[str, str]]:
    """`## ` sections as (heading, text), ignoring headings inside code blocks. The hook comes first, as ("", text)."""
    out, heading, buf, in_fence = [], "", [], False
    for line in body.split("\n"):
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
            out.append((heading, "\n".join(buf)))
            heading, buf = line[3:].strip(), []
        else:
            buf.append(line)
    out.append((heading, "\n".join(buf)))
    return out


def prose_words(text: str) -> int:
    """Words a reader reads: code blocks, link URLs and Markdown symbols left out; table cells counted."""
    text = re.sub(r"(```|~~~).*?(```|~~~)", " ", text, flags=re.S)
    text = re.sub(r"\]\([^)]*\)", "]", text)
    return len(re.findall(r"[A-Za-z0-9][\w'’.,/-]*", text))


def tables(text: str) -> list[list[list[str]]]:
    """Markdown tables in a section, each as rows of cells (the |---| separator row removed)."""
    found, cur = [], []
    for line in text.split("\n") + [""]:
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                cur.append(cells)
        elif cur:
            found.append(cur)
            cur = []
    return found


# ── Checks ───────────────────────────────────────────────────────────────────


def check_common(path: Path, fm: dict, body: str, errors: list[str], warnings: list[str]) -> None:
    for key in REQUIRED_FM:
        if fm.get(key) in (None, "", []):
            errors.append(f"front matter: `{key}` is missing")
    folder = path.parent.name
    if fm.get("track") and folder in {p.name for p in DIVES.iterdir() if p.is_dir()} and fm["track"] != folder:
        errors.append(f"front matter: track is `{fm['track']}` but the file is in `{folder}/`")
    if fm.get("date") and str(fm["date"]) != path.stem:
        errors.append(f"front matter: date {fm['date']} does not match the file name {path.stem}")

    takeaways = fm.get("takeaways") or []
    if len(takeaways) != 3:
        errors.append(f"takeaways: {len(takeaways)} found, exactly 3 required")
    for t in takeaways:
        if len(str(t).split()) > 30:
            warnings.append(f"takeaway is {len(str(t).split())} words (aim for under ~25): {str(t)[:60]}…")

    for rid in fm.get("related") or []:
        if not (DIVES / f"{rid}.md").exists() and not (DIVES / f"{rid}.mdx").exists():
            errors.append(f"related: `{rid}` does not exist under src/content/dives/")
    if (fm.get("series") is None) != (fm.get("part") is None):
        errors.append("series and part must be set together (or both left out)")

    code = blocks(body)
    if not any(lang in VISUALS for lang, _ in code):
        errors.append("no visual: add a ```mermaid diagram or a widget (stats, steps, flow, calc, chart, …)")
    for lang, src in code:
        if lang in JSON_WIDGETS:
            try:
                json.loads(src)
            except json.JSONDecodeError as exc:
                errors.append(f"```{lang} block is not valid JSON ({exc.msg}, line {exc.lineno})")

    for heading, _ in sections(body):
        if heading and LEADING_EMOJI.match(heading):
            errors.append(f"heading starts with an emoji (the site adds its own icons): ## {heading}")
    for pat in PROCESS_LEAKS:
        m = re.search(pat, body, re.I)
        if m:
            errors.append(f"describes the writer's own process: “{m.group(0)}”")


def check_system(fm: dict, body: str, errors: list[str], warnings: list[str]) -> None:
    secs = [(h, t) for h, t in sections(body) if h]
    found: dict[str, str] = {}
    pos = -1
    for prefix, _ in SYSTEM_SECTIONS:
        idx = next((i for i, (h, _) in enumerate(secs) if h.lower().startswith(prefix.lower())), None)
        if idx is None:
            errors.append(f"missing section: ## {prefix}{' …' if prefix.endswith(':') else ''}")
            continue
        if idx < pos:
            errors.append(f"section out of order: ## {secs[idx][0]}")
        pos = max(pos, idx)
        found[prefix] = secs[idx][1]
    known = [p.lower() for p, _ in SYSTEM_SECTIONS]
    for h, _ in secs:
        if not any(h.lower().startswith(k) for k in known):
            warnings.append(f"extra section not in the playbook: ## {h} (fine if it earns its place)")

    words = prose_words(body)
    if words < SYSTEM_MIN_WORDS:
        errors.append(f"post is {words} words; a complete system design needs at least {SYSTEM_MIN_WORDS}")
    elif words > SYSTEM_MAX_WORDS:
        warnings.append(f"post is {words} words (guide 3,200–4,000): cut repetition, never required content")
    for prefix, guide in SYSTEM_SECTIONS:
        if prefix in found and guide and prose_words(found[prefix]) < SECTION_THIN * guide:
            warnings.append(f"## {prefix} is {prose_words(found[prefix])} words (guide ~{guide}): check nothing is missing")

    mermaids = [src for lang, src in blocks(body) if lang == "mermaid"]
    if len(mermaids) < 2:
        errors.append(f"{len(mermaids)} mermaid diagram(s); need the C4 container diagram and a sequence diagram")

    if "High-level design" in found:
        hld = [s for lang, s in blocks(found["High-level design"]) if lang == "mermaid"]
        if not hld:
            errors.append("## High-level design has no ```mermaid container diagram")
        elif not any("subgraph" in s for s in hld):
            warnings.append("high-level diagram has no subgraph: group it into AI, data, platform and security")

    if "Deep dive:" in found:
        dd = found["Deep dive:"]
        if not any(lang == "mermaid" and "sequenceDiagram" in s for lang, s in blocks(dd)):
            errors.append("## Deep dive has no ```mermaid sequenceDiagram")
        first = dd.strip().split("\n\n")[0] if dd.strip() else ""
        if "/deep-dives/models-agentic/" not in dd and "models-agentic/" not in " ".join(fm.get("related") or []):
            warnings.append("deep dive: no link to Monday's Part B post (fine only if there wasn't one this week)")
        if prose_words(first) < 40:
            warnings.append("deep dive should open with a concept recap paragraph (~80–100 words)")

    if "Requirements" in found and not tables(found["Requirements"]):
        errors.append("## Requirements has no non-functional requirements table")

    if "Components and alternatives" in found:
        tbs = tables(found["Components and alternatives"])
        if not tbs:
            errors.append("## Components and alternatives has no table")
        else:
            tb = max(tbs, key=len)
            header = " ".join(tb[0]).lower()
            for col in ("component", "choice", "alternative", "when to switch", "aws", "azure", "google"):
                if col not in header:
                    errors.append(f"components table: no `{col}` column")
            rows = tb[1:]
            if len(rows) < MIN_COMPONENTS:
                errors.append(f"components table has {len(rows)} rows; list every main component (at least {MIN_COMPONENTS})")
            for r in rows:
                if len(r) < len(tb[0]) or any(not c for c in r):
                    errors.append(f"components table: empty cell in row `{r[0] if r else '?'}`")

    if "Key decisions" in found:
        adrs = re.findall(r"^###\s+ADR\s*\d+", found["Key decisions"], re.M | re.I)
        if not 3 <= len(adrs) <= 4:
            errors.append(f"## Key decisions has {len(adrs)} ADRs (`### ADR <n>: …`); need 3–4")
        if not re.search(r"build.{0,12}buy|managed.{0,25}self.?host|self.?host.{0,25}managed",
                         found["Key decisions"], re.I | re.S):
            errors.append("## Key decisions needs a build-vs-buy or managed-vs-self-hosted ADR")
        if not re.search(r"exit|lock-?in", found["Key decisions"], re.I):
            errors.append("## Key decisions: the build-vs-buy ADR needs its lock-in and exit plan")

    if "Sizing, cost and value" in found and not any(l == "calc" for l, _ in blocks(found["Sizing, cost and value"])):
        errors.append("## Sizing, cost and value has no ```calc cost calculator")

    if "How others built it" in found and not re.search(r"\]\(https?://", found["How others built it"]):
        errors.append("## How others built it has no source link")

    if "Exercise: find the flaws" in found and "[!REVEAL]" not in found["Exercise: find the flaws"]:
        errors.append("## Exercise has no `> [!REVEAL]` answer box")

    if "Template:" in found and not blocks(found["Template:"]):
        errors.append("## Template has no copyable fenced block")

    if fm.get("time_minutes") not in (None, 50):
        warnings.append(f"time_minutes is {fm.get('time_minutes')} (Wednesday is 50)")
    callouts = len(re.findall(r"^>\s*\[!(TIP|WARNING|NOTE|TRY|WATCH)\]", body, re.M))
    if callouts > 3:
        warnings.append(f"{callouts} callout boxes (at most 3, not counting REVEAL)")


def check(path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        fm, body = split_post(path.read_text())
    except ValueError as exc:
        return [str(exc)], []
    check_common(path, fm, body, errors, warnings)
    if fm.get("track") == "system":
        check_system(fm, body, errors, warnings)
    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("posts", type=Path, nargs="+")
    args = ap.parse_args()
    failed = 0
    for path in args.posts:
        errors, warnings = check(path)
        for e in errors:
            print(f"ERROR: {e}")
        for w in warnings:
            print(f"warning: {w}")
        print(f"{path.name} ({path.parent.name}): {len(errors)} error(s), {len(warnings)} warning(s)")
        failed += bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

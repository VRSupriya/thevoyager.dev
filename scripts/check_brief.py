#!/usr/bin/env python3
"""Check an AI Daily Brief post before it is committed.

    python3 scripts/check_brief.py src/content/brief/2026-10-13.md [--candidates /tmp/candidates.json]

Errors (exit 1) are rule breaks the routine must fix before committing.
Warnings (exit 0) are style signals worth a second look.

Layout: the main sections (🔥 always, then any of the other five, in order; an empty slot is left out), then "## ⚡ Also worth knowing" (always), "## ⏰ Deadlines & deprecations"
(Mondays; other days only when a new deadline was found), "## 📅 Week in review" (Sundays), then the
closing line and the optional "## 🌍" regional block.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yaml

SLOTS = ["🔥", "🚀", "🤖", "🔬", "✍️", "🏗️"]
EXTRAS = ["⚡", "⏰", "📅"]     # after the six slots, in this order; ⚡ always, ⏰ on Mondays, 📅 on Sundays
QUICK_MIN, QUICK_MAX = 5, 15    # ⚡ bullets: hard limits (aim for 8-12)
QUICK_WORDS_WARN = 400
QUIET = "Quiet day"
DOMAIN_CAP = 2
TELEGRAM_MAX = 760          # whole public preview (six slot lines, ⚡ and 🌍)
TELEGRAM_LINE_MAX = 90      # one slot line, emoji included
PRIVATE_MAX = 700
WORDS_WARN = 950            # main body, without the regional block
# The writer must never narrate its own tools or browsing.
PROCESS_LEAKS = [r"\bI couldn'?t\b", r"\bI could (read|open|find|see)\b", r"\bI was unable\b",
                 r"\bexcerpt I\b", r"\bWebFetch\b", r"\bWebSearch\b", r"\bsearch snippets?\b"]
# From config/voice.md: words that make a post read like a bot.
AI_TELLS = ["delve", "tapestry", "game-changer", "game changer", "revolutionize", "revolutionise",
            "seamless", "in today's fast-paced", "it's important to note", "let's dive in",
            "in conclusion", "whether you're"]
HEDGES = r"vendor (claim|figure|number|expectation)s?|not independent|not peer|doesn'?t (say|state|give|list|name)|no (pricing|code|published)|treat (it|them|this|that) as"


def split_post(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise SystemExit("no front matter")
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


def site(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def closing_of(body: str) -> str:
    lines = re.findall(r"^\*([^*].*?)\*\s*$", body, re.M)
    return lines[-1].strip() if lines else ""


def bullets(section: str) -> list[str]:
    return [l.strip() for l in section.splitlines() if l.strip().startswith("- ")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("post", type=Path)
    ap.add_argument("--candidates", type=Path, default=Path("/tmp/candidates.json"),
                    help="used to warn when 🚀 is left out while new models were found")
    args = ap.parse_args()
    path = args.post
    fm, body = split_post(path.read_text())
    errors: list[str] = []
    warnings: list[str] = []

    for key in ("title", "date", "summary", "telegram", "tags"):
        if not fm.get(key):
            errors.append(f"front matter: `{key}` is missing")

    # C1: the title is a headline, not "AI Daily Brief — DATE".
    title = str(fm.get("title") or "")
    if re.match(r"\s*AI Daily Brief", title, re.I):
        errors.append("title must be a headline naming the top story, not 'AI Daily Brief — DATE'")
    elif len(title) > 90:
        errors.append(f"title is {len(title)} characters (keep it under 80)")
    elif len(title) > 80:
        warnings.append(f"title is {len(title)} characters (aim for under 80)")

    main_part = body.split("## 🌍")[0]
    all_sections = re.split(r"^## ", main_part, flags=re.M)[1:]
    found = [s.split()[0] if s.split() else "" for s in all_sections]
    n_main = 0
    while n_main < len(found) and found[n_main] in SLOTS:
        n_main += 1
    sections = all_sections[:n_main]
    extras = dict(zip(found[n_main:], all_sections[n_main:]))
    main_found = found[:n_main]
    if not main_found or main_found[0] != "🔥" or main_found != [x for x in SLOTS if x in main_found]:
        errors.append(f"main sections must start with 🔥 and keep the order {' '.join(SLOTS)}; found {' '.join(found) or 'none'}")
    elif len(main_found) < 3:
        warnings.append(f"only {len(main_found)} main items; check the slots you left out really had nothing")
    # C3: no empty "Quiet day" sections on the site.
    for sec in sections:
        if QUIET.lower() in sec.lower():
            errors.append(f"section '{sec.splitlines()[0][:40]}' says 'Quiet day': leave the slot out instead")
    for line in (fm.get("telegram") or "").splitlines():
        if QUIET.lower() in line.lower():
            errors.append("telegram preview has a 'Quiet day' line: leave that slot's line out")
    # C2: each main item stays short.
    for sec in sections:
        body_words = len(re.sub(r"\[[^\]]*\]\([^)]*\)", "", "\n".join(sec.splitlines()[1:])).split())
        if body_words > 160:
            warnings.append(f"section '{sec.splitlines()[0][:40]}' is {body_words} words (aim for about 130)")
    tail = found[n_main:]
    if tail != [e for e in EXTRAS if e in tail] or any(t not in EXTRAS for t in tail):
        errors.append(f"after the main items only {' '.join(EXTRAS)} may follow, in that order; found {' '.join(tail) or 'none'}")
    try:
        weekday = date.fromisoformat(str(fm.get("date"))).weekday()
    except ValueError:
        weekday = -1

    main_urls = set(re.findall(r"\]\((https?://[^)\s]+)\)", "".join(sections)))

    # ⚡ Also worth knowing: always there, 5-15 one-line items, each with a link, none repeating a main item.
    quick = extras.get("⚡")
    if quick is None:
        errors.append("missing '## ⚡ Also worth knowing' after the six items")
    else:
        items = bullets(quick)
        if not QUICK_MIN <= len(items) <= QUICK_MAX:
            errors.append(f"⚡ has {len(items)} items (need {QUICK_MIN}-{QUICK_MAX}, aim for 8-12)")
        elif not 8 <= len(items) <= 12:
            warnings.append(f"⚡ has {len(items)} items (aim for 8-12)")
        for b in items:
            links = re.findall(r"\]\((https?://[^)\s]+)\)", b)
            if not links:
                errors.append(f"⚡ item has no link: {b[:60]}")
            for u in links:
                if u in main_urls:
                    errors.append(f"⚡ item repeats a main item's source: {u}")
            if len(b.split()) > 45:
                warnings.append(f"⚡ item is {len(b.split())} words (keep it to one line): {b[:50]}…")
        if "🆕" not in quick:
            warnings.append("⚡ has no '🆕 New on the radar' group (fine only if nothing new and credible turned up)")
        if len(quick.split()) > QUICK_WORDS_WARN:
            warnings.append(f"⚡ is {len(quick.split())} words (aim for under {QUICK_WORDS_WARN})")

    # ⏰ Deadlines & deprecations: required on Mondays; each line starts with a bold ISO date.
    deadlines = extras.get("⏰")
    if weekday == 0 and deadlines is None:
        errors.append("Monday brief needs '## ⏰ Deadlines & deprecations' (the 30-60 day calendar)")
    if deadlines is not None:
        for b in bullets(deadlines):
            m = re.match(r"- \*\*(\d{4}-\d{2}-\d{2})\*\*", b)
            if not m:
                errors.append(f"⏰ line must start with a bold date like **2026-11-01**: {b[:60]}")
            elif not re.search(r"\]\(https?://", b):
                errors.append(f"⏰ line has no link to the official page: {b[:60]}")
            elif m.group(1) < str(fm.get("date")):
                warnings.append(f"⏰ date {m.group(1)} is already in the past")

    if deadlines is not None and len(bullets(deadlines)) > 5:
        warnings.append(f"⏰ has {len(bullets(deadlines))} items (keep it to 5)")
    if "📅" in extras and len(extras["📅"].split()) > 220:
        warnings.append(f"📅 Week in review is {len(extras['📅'].split())} words (aim for about 200)")
    if weekday == 6 and "📅" not in extras:
        errors.append("Sunday brief needs '## 📅 Week in review'")

    # 🚀 left out while new models sit unused in candidates.json is almost always a miss.
    if "🚀" not in main_found and args.candidates.exists():
        nc = json.loads(args.candidates.read_text()).get("newcomers", {})
        fresh = [e["name"] for k in ("model_api", "model") for e in nc.get(k, []) if not e.get("already_in_brief")]
        if fresh:
            warnings.append("🚀 is left out but candidates.json lists new models not yet covered: "
                            + ", ".join(fresh[:5]) + ". Use one, or say in the editor's notes why each was rejected.")

    sites: dict[str, int] = {}
    for sec in sections:
        head = sec.splitlines()[0][:60]
        links = re.findall(r"\[Source\]\((https?://[^)\s]+)\)", sec)
        if not links:
            errors.append(f"section '{head}' has no [Source](...) link")
        for u in {site(u) for u in links}:
            sites[u] = sites.get(u, 0) + 1
        hedges = len(re.findall(HEDGES, sec, re.I))
        if hedges > 2:
            warnings.append(f"section '{head}' stacks {hedges} caveats (one honest caveat is enough)")
    for s, n in sites.items():
        if n > DOMAIN_CAP:
            errors.append(f"{n} of the main items come from {s} (max {DOMAIN_CAP})")

    for pat in PROCESS_LEAKS:
        for m in re.finditer(pat, body):
            errors.append(f"process talk in the post: '{body[max(0, m.start()-30):m.end()+30].strip()}'")
    low = body.lower()
    for word in AI_TELLS:
        if word in low:
            errors.append(f"voice.md banned phrase: '{word}'")

    tg = (fm.get("telegram") or "").strip()
    if len(tg) > TELEGRAM_MAX:
        errors.append(f"telegram block is {len(tg)} chars (max {TELEGRAM_MAX})")
    for line in tg.splitlines()[1:]:
        if len(line) > TELEGRAM_LINE_MAX:
            errors.append(f"telegram line is {len(line)} chars (max {TELEGRAM_LINE_MAX}): {line[:50]}…")
    private = (fm.get("telegram_private") or "").strip()
    if len(private) > PRIVATE_MAX:
        errors.append(f"telegram_private is {len(private)} chars (max {PRIVATE_MAX})")

    words = len("".join(sections).split())
    if words > WORDS_WARN:
        warnings.append(f"main body is {words} words (aim for under {WORDS_WARN})")
    tests = len(re.findall(r"\b(pilot|compare|measure|benchmark it|try it|run it|to test)\b", main_part, re.I))
    if tests > 3:
        warnings.append(f"{tests} 'go and test this' phrases; keep the try-this line to at most 2 items")

    # The closing line must not echo the last 5 briefs.
    close = closing_of(body)
    if not close:
        errors.append("no italic closing line")
    else:
        opener = " ".join(close.lower().split()[:3])
        earlier = sorted(p for p in path.parent.glob("*.md") if p.name < path.name)[-5:]
        for prev in earlier:
            prev_close = closing_of(split_post(prev.read_text())[1])
            if prev_close and " ".join(prev_close.lower().split()[:3]) == opener:
                errors.append(f"closing starts like {prev.stem}'s: '{prev_close[:50]}'")
                break

    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    print(f"{path.name}: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

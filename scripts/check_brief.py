#!/usr/bin/env python3
"""Check an AI Daily Brief post before it is committed.

    python3 scripts/check_brief.py src/content/brief/2026-10-13.md

Errors (exit 1) are rule breaks the routine must fix before committing.
Warnings (exit 0) are style signals worth a second look.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

SLOTS = ["🔥", "🚀", "🤖", "🔬", "✍️", "🏗️"]
QUIET = "Quiet day"
DOMAIN_CAP = 2
TELEGRAM_MAX = 650          # whole public preview
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


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    fm, body = split_post(path.read_text())
    errors: list[str] = []
    warnings: list[str] = []

    for key in ("title", "date", "summary", "telegram", "tags"):
        if not fm.get(key):
            errors.append(f"front matter: `{key}` is missing")

    main_part = body.split("## 🌍")[0]
    sections = re.split(r"^## ", main_part, flags=re.M)[1:]
    found = [s.split()[0] if s.split() else "" for s in sections]
    if found != SLOTS:
        errors.append(f"main sections must be exactly {' '.join(SLOTS)} in order; found {' '.join(found) or 'none'}")

    sites: dict[str, int] = {}
    for sec in sections:
        head = sec.splitlines()[0][:60]
        links = re.findall(r"\[Source\]\((https?://[^)\s]+)\)", sec)
        if not links and QUIET not in sec:
            errors.append(f"section '{head}' has no [Source](...) link")
        for u in {site(u) for u in links}:
            sites[u] = sites.get(u, 0) + 1
        hedges = len(re.findall(HEDGES, sec, re.I))
        if hedges > 2:
            warnings.append(f"section '{head}' stacks {hedges} caveats (one honest caveat is enough)")
    for s, n in sites.items():
        if n > DOMAIN_CAP:
            errors.append(f"{n} of the 6 items come from {s} (max {DOMAIN_CAP})")

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

    words = len(main_part.split())
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

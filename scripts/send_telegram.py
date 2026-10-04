#!/usr/bin/env python3
"""Send a published post to Telegram.

    python3 scripts/send_telegram.py src/content/brief/2026-10-05.md
    python3 scripts/send_telegram.py src/content/dives/models-agentic/2026-10-12.md --dry-run

The `telegram:` text goes to the public channel (TELEGRAM_CHANNEL_ID) with a link to the post.
The `telegram_private:` text (Deep Dives only) goes to the author's own chat
(TELEGRAM_PRIVATE_CHAT_ID) and is never sent to the channel.

Environment variables (store them as secrets in the routine's environment, never in the repo):
    TELEGRAM_BOT_TOKEN         from @BotFather
    TELEGRAM_CHANNEL_ID        e.g. @thevoyager or -100123...  (bot must be a channel admin)
    TELEGRAM_PRIVATE_CHAT_ID   your own chat id with the bot (send it /start first)

Plain text only (no parse_mode), so emoji, underscores and brackets never break a message.
A failed send prints the error and exits 1; the routine should report it but not undo the post.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yaml

SITE = "https://thevoyager.dev"
LIMIT = 4096  # Telegram's hard limit per message


def read_post(path: Path) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    if not m:
        raise SystemExit(f"{path}: no frontmatter found")
    return yaml.safe_load(m.group(1))


def post_url(path: Path) -> str:
    parts = path.resolve().parts
    slug = path.stem
    if "brief" in parts and parts[-2] == "brief":
        return f"{SITE}/brief/{slug}/"
    if "dives" in parts:
        return f"{SITE}/deep-dives/{parts[-2]}/{slug}/"
    raise SystemExit(f"{path}: not under src/content/brief or src/content/dives/<track>")


def clip(text: str, room: int = LIMIT) -> str:
    return text if len(text) <= room else text[: room - 1].rstrip() + "…"


def send(token: str, chat_id: str, text: str, dry_run: bool) -> None:
    if dry_run:
        print(f"--- would send to {chat_id or '(chat id not set)'} ({len(text)} chars)\n{text}\n")
        return
    req = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "false"}).encode(),
    )
    try:
        with urlopen(req, timeout=20) as resp:
            ok = json.load(resp).get("ok")
    except HTTPError as e:  # Telegram explains the problem in the body
        raise SystemExit(f"Telegram error {e.code} for chat {chat_id}: {e.read().decode(errors='replace')[:300]}")
    except URLError as e:
        raise SystemExit(f"Could not reach Telegram (is api.telegram.org allowed in the network policy?): {e.reason}")
    if not ok:
        raise SystemExit(f"Telegram said not ok for chat {chat_id}")
    print(f"sent to {chat_id} ({len(text)} chars)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("post", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="print the messages instead of sending")
    args = ap.parse_args()

    fm = read_post(args.post)
    url = post_url(args.post)
    public = (fm.get("telegram") or "").strip()
    private = (fm.get("telegram_private") or "").strip()
    if not public:
        raise SystemExit(f"{args.post}: no `telegram:` text to send")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    channel = os.environ.get("TELEGRAM_CHANNEL_ID", "")
    me = os.environ.get("TELEGRAM_PRIVATE_CHAT_ID", "")
    if not args.dry_run and not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    failures = 0
    jobs = [(channel, clip(f"{public}\n\n{url}"), "channel")]
    if private:
        jobs.append((me, clip(f"🔒 Just for you\n{private}\n\n{url}"), "private"))
    for chat_id, text, label in jobs:
        if not chat_id and not args.dry_run:
            print(f"skipped {label}: chat id not set", file=sys.stderr)
            failures += 1
            continue
        try:
            send(token, chat_id, text, args.dry_run)
        except SystemExit as e:
            print(f"{label}: {e}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

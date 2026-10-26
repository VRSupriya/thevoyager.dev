#!/usr/bin/env python3
"""Submit your run of a lab, and let the Sunday routine review submissions.

Submit (you):
    cd labs/lab/2026-10-25 && python3 run.py --results results.json     # run the lab your way (laptop, Colab, GPU)
    python3 scripts/submit_lab.py submit labs/lab/2026-10-25 --results labs/lab/2026-10-25/results.json \\
        --notes "Tried no_warmup too: val 1.81. Curious why clipping fires on almost every step."
    git add labs/submissions && git commit -m "lab: submit 2026-10-25" && git push origin main

    It checks your results against the lab's pass gates (expected.json), then saves them to
    labs/submissions/<today>/<track>-<lab date>/ as results.json, check.json and notes.md.
    Changed settings are fine: they're recorded, and the review compares like with like.

Review (the Deep Dives routine, on Sundays):
    python3 scripts/submit_lab.py review --since 2026-10-25

    Prints every submission since that date: gates passed or missed, your numbers against the routine's own reference
    run (run.json), settings you changed, and your notes. The routine uses it for the private Telegram feedback and to
    suggest moving research_step on; it never moves research_step itself.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_lab import ROOT, check_gates, load_expected  # noqa: E402

SUBMISSIONS = ROOT / "labs" / "submissions"


def submit(args) -> int:
    lab = args.lab.resolve()
    exp = load_expected(lab)
    results = json.loads(args.results.read_text())
    if "metrics" not in results:
        print("results.json has no 'metrics': run the lab with --results FILE so it writes them")
        return 1
    gates = check_gates(exp, results)
    rel = lab.relative_to(ROOT / "labs")                      # e.g. lab/2026-10-25
    dest = SUBMISSIONS / (args.date or date.today().isoformat()) / f"{rel.parts[0]}-{rel.parts[1]}"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.results, dest / "results.json")
    check = {"lab": str(rel), "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "passed": all(g["passed"] for g in gates), "gates": gates}
    (dest / "check.json").write_text(json.dumps(check, indent=1))
    if args.notes:
        (dest / "notes.md").write_text(args.notes.strip() + "\n")
    for g in gates:
        print(f"{'PASS' if g['passed'] else 'FAIL'}  {g['label']}: {g['observed']} (needs {g['op']} {g['value']})")
    print(("All gates passed." if check["passed"] else "Some gates missed; submit anyway, the review says why.")
          + f"\nSaved to {dest.relative_to(ROOT)}. Commit it:\n"
          f"  git add {dest.relative_to(ROOT)} && git commit -m \"lab: submit {rel}\" && git push origin main")
    return 0


def review(args) -> int:
    since = args.since or "0000-00-00"
    found = False
    for day_dir in sorted(p for p in SUBMISSIONS.glob("*") if p.is_dir() and p.name >= since):
        for sub in sorted(p for p in day_dir.iterdir() if p.is_dir()):
            found = True
            res = json.loads((sub / "results.json").read_text())
            chk = json.loads((sub / "check.json").read_text()) if (sub / "check.json").exists() else {}
            lab = ROOT / "labs" / chk.get("lab", sub.name.replace("-", "/", 1))
            ref = json.loads((lab / "run.json").read_text()) if (lab / "run.json").exists() else {}
            print(f"## {day_dir.name} · {chk.get('lab', sub.name)} · {'PASSED' if chk.get('passed') else 'MISSED A GATE'}")
            for g in chk.get("gates", []):
                print(f"- {g['label']}: {g['observed']} (needs {g['op']} {g['value']}) {'ok' if g['passed'] else 'MISSED'}")
            m, rm = res.get("metrics", {}), ref.get("metrics", {})
            shared = [k for k in m if k in rm and isinstance(m[k], (int, float))]
            if shared:
                print("- against the reference run: " + ", ".join(f"{k} {m[k]} vs {rm[k]}" for k in shared))
            changed = {k: v for k, v in (res.get("config") or {}).items()
                       if (ref.get("config") or {}).get(k, v) != v} if ref.get("config") else {}
            if not changed and res.get("config"):
                ref_cfg = json.loads((lab / "expected.json").read_text()).get("reference_config", {})
                changed = {k: v for k, v in res["config"].items() if k in ref_cfg and ref_cfg[k] != v}
            if changed:
                print(f"- settings changed: {json.dumps(changed)}")
            env = res.get("environment") or {}
            if env:
                print(f"- ran on: {env.get('platform', '?')}, torch {env.get('torch', '?')}, {m.get('seconds', '?')} s")
            if (sub / "notes.md").exists():
                print(f"- notes: {(sub / 'notes.md').read_text().strip()}")
            print()
    if not found:
        print(f"No lab submissions since {since}.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit", help="check and save your results")
    s.add_argument("lab", type=Path, help="the lab folder, e.g. labs/lab/2026-10-25")
    s.add_argument("--results", type=Path, required=True, help="the JSON your run wrote with --results")
    s.add_argument("--notes", default="", help="what you tried, saw, or want explained")
    s.add_argument("--date", default=None, help="submission date (default: today)")
    r = sub.add_parser("review", help="list submissions for the routine")
    r.add_argument("--since", default=None, help="YYYY-MM-DD")
    args = ap.parse_args()
    return submit(args) if args.cmd == "submit" else review(args)


if __name__ == "__main__":
    sys.exit(main())

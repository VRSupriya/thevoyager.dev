#!/usr/bin/env python3
"""Run a hands-on lab and check its pass gates.

    python3 scripts/run_lab.py labs/lab/2026-10-25                 # run it, check the gates (prints PASS/FAIL)
    python3 scripts/run_lab.py labs/lab/2026-10-25 --record        # routine: also save output.txt and run.json in the lab
    python3 scripts/run_lab.py labs/lab/2026-10-25 --experiments   # also run the measured experiments in expected.json
    python3 scripts/run_lab.py labs/lab/2026-10-25 --no-install    # use the current Python instead of a fresh venv

A lab folder holds:
    run.py            the whole exercise, runnable on a laptop CPU; writes metrics with `--results FILE`
    requirements.txt  pinned dependencies
    expected.json     pass gates (and optional experiments), see EXPECTED below
    output.txt        the routine's own run, saved with --record (the post quotes it)
    run.json          that run's metrics, gate results and environment, saved with --record

Dependencies install into a cached virtualenv per requirements file (~/.cache/voyager-labs/<hash>), so a second
run starts at once. torch comes from PyTorch's CPU wheel index. Exit code 0 means every gate passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("VOYAGER_LAB_CACHE", Path.home() / ".cache" / "voyager-labs"))
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
EXPECTED = """{
  "title": "Lab, Step 1: a small GPT",          "post": "lab/2026-10-25",
  "command": ["run.py"],                          "timeout_minutes": 15,
  "gates": [{"metric": "val_loss", "op": "<=", "value": 1.95, "label": "val loss after 2,000 steps"}],
  "experiments": [{"name": "no_mask", "args": ["--break", "no_mask"], "label": "remove the causal mask"}]
}"""
OPS = {"<=": lambda x, v: x <= v, ">=": lambda x, v: x >= v, "<": lambda x, v: x < v, ">": lambda x, v: x > v,
       "==": lambda x, v: x == v, "between": lambda x, v: v[0] <= x <= v[1]}


def load_expected(lab: Path) -> dict:
    f = lab / "expected.json"
    if not f.exists():
        raise SystemExit(f"{f} is missing. Format:\n{EXPECTED}")
    exp = json.loads(f.read_text())
    for g in exp.get("gates", []):
        if g.get("op") not in OPS:
            raise SystemExit(f"gate {g} has op {g.get('op')!r}; use one of {list(OPS)}")
    if not exp.get("gates"):
        raise SystemExit("expected.json needs at least one gate: a lab without a pass criterion can't be verified")
    return exp


def check_gates(exp: dict, results: dict) -> list[dict]:
    """Evaluate each gate against results['metrics']."""
    metrics = results.get("metrics", {})
    out = []
    for g in exp.get("gates", []):
        x = metrics.get(g["metric"])
        ok = isinstance(x, (int, float)) and OPS[g["op"]](x, g["value"])
        out.append({**g, "observed": x, "passed": bool(ok)})
    return out


def venv_python(lab: Path) -> Path:
    req = lab / "requirements.txt"
    reqs = req.read_text() if req.exists() else ""
    env_dir = CACHE / hashlib.sha256(reqs.encode()).hexdigest()[:16]
    py = env_dir / "bin" / "python"
    if not (env_dir / ".ready").exists():
        print(f"installing {req.relative_to(ROOT) if req.exists() else 'no requirements'} into {env_dir} ...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(env_dir)], check=True)
        if reqs.strip():
            cmd = [str(py), "-m", "pip", "install", "-q", "-r", str(req)]
            if "torch" in reqs:
                cmd += ["--extra-index-url", TORCH_CPU_INDEX]
            subprocess.run(cmd, check=True)
        (env_dir / ".ready").write_text(datetime.now(timezone.utc).isoformat())
    return py


def run_once(py: Path, lab: Path, exp: dict, extra: list[str], results: Path, timeout_s: int) -> tuple[int, str, float]:
    cmd = [str(py), *exp.get("command", ["run.py"]), *extra, "--results", str(results)]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=lab, capture_output=True, text=True, timeout=timeout_s)
        code, out = proc.returncode, proc.stdout + (("\n--- stderr ---\n" + proc.stderr) if proc.stderr.strip() else "")
    except subprocess.TimeoutExpired as e:
        code, out = 124, (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        out += f"\n--- timed out after {timeout_s}s ---"
    return code, out, time.time() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lab", type=Path)
    ap.add_argument("--record", action="store_true", help="save output.txt and run.json in the lab folder")
    ap.add_argument("--experiments", action="store_true", help="also run expected.json's experiments")
    ap.add_argument("--no-install", action="store_true", help="use this Python, skip the virtualenv")
    ap.add_argument("--out", type=Path, default=None, help="where results go (default: a temp folder, or the lab with --record)")
    args = ap.parse_args()
    lab = args.lab.resolve()
    exp = load_expected(lab)
    py = Path(sys.executable) if args.no_install else venv_python(lab)
    out_dir = args.out or (lab if args.record else Path(os.environ.get("TMPDIR", "/tmp")) / f"lab-{lab.parent.name}-{lab.name}")
    out_dir.mkdir(parents=True, exist_ok=True)
    timeout_s = int(exp.get("timeout_minutes", 15) * 60)

    results_file = out_dir / "results.json"
    code, output, secs = run_once(py, lab, exp, [], results_file, timeout_s)
    print(output[-3000:])
    if code != 0 or not results_file.exists():
        print(f"FAIL: {' '.join(exp.get('command', ['run.py']))} exited with {code} after {secs:.0f}s and wrote no results")
        return 1
    results = json.loads(results_file.read_text())
    gates = check_gates(exp, results)

    experiments = []
    if args.experiments:
        for e in exp.get("experiments", []):
            f = out_dir / f"experiment-{e['name']}.json"
            c, o, s = run_once(py, lab, exp, e.get("args", []), f, timeout_s)
            m = json.loads(f.read_text())["metrics"] if c == 0 and f.exists() else None
            experiments.append({"name": e["name"], "label": e.get("label", e["name"]), "args": e.get("args", []),
                                "exit_code": c, "seconds": round(s, 1), "metrics": m})
            print(f"experiment {e['name']}: " + (f"val {m.get('val_loss')} train {m.get('train_loss')}" if m else f"failed ({c})"))

    passed = all(g["passed"] for g in gates)
    record = {"lab": str(lab.relative_to(ROOT)) if lab.is_relative_to(ROOT) else str(lab),
              "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seconds": round(secs, 1),
              "passed": passed, "gates": gates, "metrics": results.get("metrics", {}),
              "config": results.get("config"), "curve": results.get("curve"), "experiments": experiments,
              "environment": {**results.get("environment", {}), "runner_python": platform.python_version()}}
    if args.record:
        (lab / "output.txt").write_text(output)
        (lab / "run.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
        if results_file.exists() and results_file.parent == lab:
            results_file.unlink()  # run.json holds everything; keep the folder clean
        for e in exp.get("experiments", []):
            f = lab / f"experiment-{e['name']}.json"
            if f.exists():
                f.unlink()
    for g in gates:
        print(f"{'PASS' if g['passed'] else 'FAIL'}  {g['label']}: {g['observed']} (needs {g['op']} {g['value']})")
    print("PASS: every gate met" if passed else "FAIL: at least one gate missed")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

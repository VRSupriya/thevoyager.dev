# Runnable labs

Every Build and Lab post on The Voyager ships its exercise here as one runnable folder. The Deep Dives routine runs
each lab before the post goes out, and the post's "Expected results" come from that run, not from memory.

```
labs/<track>/<date>/          e.g. labs/lab/2026-10-25/
  run.py            the whole exercise in one file (CPU-friendly, downloads its own data)
  requirements.txt  pinned versions
  expected.json     the pass gates: what "done" means, as numbers
  output.txt        the routine's own run (what you should roughly see)
  run.json          that run's metrics, gate results, measured experiments and environment
labs/submissions/<date>/<track>-<lab date>/   your submitted runs
```

## Run a lab

```bash
cd labs/lab/2026-10-25
pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
python3 run.py --results results.json            # about 2 minutes on a 4-core laptop CPU
python3 run.py --break no_mask --results r.json  # Part 2's breakage experiments: no_mask, no_scale, no_resid, high_lr, no_warmup
```

Or let the runner install into a cached virtualenv and check the gates for you:

```bash
python3 scripts/run_lab.py labs/lab/2026-10-25               # PASS/FAIL per gate
python3 scripts/run_lab.py labs/lab/2026-10-25 --experiments # also runs the breakage experiments
```

Colab works too: upload `run.py`, run `!python run.py --results results.json`, and download `results.json`.

## Submit your run

```bash
python3 scripts/submit_lab.py submit labs/lab/2026-10-25 --results labs/lab/2026-10-25/results.json \
    --notes "What you tried, what surprised you, what you want explained"
git add labs/submissions && git commit -m "lab: submit lab/2026-10-25" && git push origin main
```

`submit` checks your results against the gates in `expected.json` and saves them, with your notes, under
`labs/submissions/<today>/lab-2026-10-25/`. Changed settings (a bigger model, another learning rate, a GPU) are
fine: they are recorded and compared like with like. You can also add the folder through GitHub's web upload.

## What happens next

On Sunday, before writing the next Lab post, the routine runs `python3 scripts/submit_lab.py review --since <last lab
date>`. It sends you, privately on Telegram:

- which gates you passed or missed, and the clearest difference from the reference run;
- an answer to the question in your notes;
- when every gate of the current step's lab has passed, a suggestion to move `research_step` on in
  `config/progress.yml` (only you change it).

The public post never mentions your results.

## Write a lab (for the routine)

`config/dives-playbook.md` ("Runnable hands-on") has the rules: one file, under 10 minutes on a CPU, `--results FILE`
writing `{"metrics", "config", "curve"}`, at least one gate, breakage curves measured through `experiments`, and
`python3 scripts/run_lab.py labs/<track>/<date> --record --experiments` before publishing.

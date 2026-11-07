#!/usr/bin/env python3
"""An eval harness for a support copilot: how far can you trust the judge, and how many labels do you need?

    python3 run.py --results results.json              # a few seconds, numpy only
    python3 run.py --seed 2 --results r.json           # another random world
    python3 run.py --verbosity 0 --results r.json      # judge with no length bias: what does the gap become?

Everything is generated (no download, no API key). The "judge" is a deterministic stand-in for an LLM judge: it reads
the policy, checks whether the answer states the policy number, and is nudged by length and confident tone. Its
biases are written down in `judge_score` so you can see them; swap that function for a real model call to test
your own judge. Human labels are the ground truth here (the synthetic answers know what they are).
"""
import argparse, json, math, platform, random, time
import numpy as np

# ---- synthetic retail support cases ----------------------------------------------------------------------------
POLICY = {
    "refund": ("Refunds are available within {n} days of delivery.", [14, 30, 45]),
    "delivery": ("Standard delivery takes {n} working days.", [3, 5, 7]),
    "warranty": ("Electronics carry a warranty of {n} months.", [12, 24, 36]),
    "exchange": ("Exchanges are accepted within {n} days with the receipt.", [14, 28, 60]),
    "account": ("Locked accounts are restored within {n} hours after identity checks.", [4, 24, 48]),
}
FILLER = ["Thanks so much for getting in touch.", "I completely understand how frustrating this can be.",
          "We really value you as a customer.", "I hope this helps with your order.",
          "Please let me know if there is anything else you need."]
CONFIDENT = ["Absolutely!", "Certainly!", "Of course!"]
INVENTED = ["We will also refund your return shipping.", "Delivery is free for any order above five items.",
            "The warranty covers accidental damage as well."]
OUTCOMES = ["correct", "wrong_number", "invented_promise", "refusal"]


def make_case(rng, p_correct):
    kind = rng.choice(list(POLICY))
    tmpl, values = POLICY[kind]
    n = rng.choice(values)
    rest = (1 - p_correct) / 3
    outcome = rng.choices(OUTCOMES, [p_correct, rest * 1.2, rest * 1.0, rest * 0.8])[0]
    stated = n if outcome != "wrong_number" else rng.choice([v for v in values if v != n])
    parts = []
    if rng.random() < 0.5:
        parts.append(rng.choice(CONFIDENT))
    if outcome == "refusal":
        parts.append("I'm sorry, I can't help with that request.")
    else:
        parts.append(tmpl.format(n=stated))
    if outcome == "invented_promise":
        parts.append(rng.choice(INVENTED))
    parts += rng.sample(FILLER, rng.choice([0, 0, 1, 2, 3, 4]))
    return {"kind": kind, "policy": tmpl.format(n=n), "n": n, "answer": " ".join(parts), "outcome": outcome,
            "words": len(" ".join(parts).split())}


def human_ok(c):
    return c["outcome"] == "correct"


def judge_score(c, nrng, verbosity, noise=0.14):
    """Stand-in for an LLM judge that is given the policy. Documented biases:
    it checks the number only loosely, rewards length (verbosity) and confident tone, and barely notices extras."""
    s = 0.30                                   # on-topic baseline
    stated = [int(w) for w in c["answer"].replace(".", " ").split() if w.isdigit()]
    if c["n"] in stated:
        s += 0.38                              # states the policy number
    if "can't help" in c["answer"]:
        s -= 0.35                              # refusals are spotted
    if any(x in c["answer"] for x in INVENTED):
        s -= 0.06                              # weak penalty: the judge barely notices unsupported promises
    s += verbosity * min(c["words"], 60) / 60  # length bias
    if any(x in c["answer"] for x in CONFIDENT):
        s += 0.05                              # tone bias
    return s + nrng.normal(0, noise)


def judge_pass(c, nrng, verbosity, thr=0.62):
    return judge_score(c, nrng, verbosity) >= thr


def kappa(h, j):
    h, j = np.asarray(h, bool), np.asarray(j, bool)
    po = (h == j).mean()
    pe = h.mean() * j.mean() + (1 - h.mean()) * (1 - j.mean())
    return float((po - pe) / (1 - pe)) if pe < 1 else 1.0


def rogan_gladen(judge_rate, tpr, tnr):
    denom = tpr + tnr - 1
    return float(np.clip((judge_rate + tnr - 1) / denom, 0, 1)) if denom > 0.05 else float("nan")


def corrected_estimate(labelled_h, labelled_j, judge_rate_all):
    h, j = np.asarray(labelled_h, bool), np.asarray(labelled_j, bool)
    if h.sum() == 0 or (~h).sum() == 0:
        return float("nan")
    return rogan_gladen(judge_rate_all, j[h].mean(), (~j[~h]).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--traffic", type=int, default=3000, help="production cases the judge scores")
    ap.add_argument("--labels", type=int, default=150, help="cases a human labels")
    ap.add_argument("--verbosity", type=float, default=0.18, help="length bias of the judge (0 = none)")
    ap.add_argument("--results", default="results.json")
    a = ap.parse_args()
    t0 = time.time()
    rng, nrng = random.Random(a.seed), np.random.default_rng(a.seed)

    cases = [make_case(rng, 0.68) for _ in range(a.traffic)]
    human = np.array([human_ok(c) for c in cases])
    judge = np.array([judge_pass(c, nrng, a.verbosity) for c in cases])
    m = {"traffic": a.traffic, "human_pass_rate": round(float(human.mean()), 3), "judge_pass_rate": round(float(judge.mean()), 3)}
    m["dashboard_gap"] = round(m["judge_pass_rate"] - m["human_pass_rate"], 3)
    m["agreement"] = round(float((human == judge).mean()), 3)
    m["kappa"] = round(kappa(human, judge), 3)
    m["tpr"] = round(float(judge[human].mean()), 3)
    m["tnr"] = round(float((~judge[~human]).mean()), 3)
    print(f"{a.traffic} cases | human pass {m['human_pass_rate']:.3f} | judge pass {m['judge_pass_rate']:.3f} "
          f"| agreement {m['agreement']:.3f} | kappa {m['kappa']:.3f} | TPR {m['tpr']:.3f} TNR {m['tnr']:.3f}")

    # 1. where the judge is wrong: by outcome and by answer length
    words = np.array([c["words"] for c in cases])
    out = np.array([c["outcome"] for c in cases])
    print("\njudge pass rate by what the answer really is (humans: only 'correct' passes)")
    for o in OUTCOMES:
        r = float(judge[out == o].mean())
        m[f"judge_pass/{o}"] = round(r, 3)
        print(f"  {o:<17}{r:6.3f}   n={int((out == o).sum())}")
    bad = ~human
    long_bad, short_bad = bad & (words >= 30), bad & (words < 12)
    m["bad_long_pass"] = round(float(judge[long_bad].mean()), 3)
    m["bad_short_pass"] = round(float(judge[short_bad].mean()), 3)
    m["verbosity_gap"] = round(m["bad_long_pass"] - m["bad_short_pass"], 3)
    print(f"  wrong answers of 30+ words pass {m['bad_long_pass']:.3f}, of under 12 words pass {m['bad_short_pass']:.3f}")

    # 2. position bias in a pairwise judge: same pair, order swapped
    flips, n_pairs = 0, 400
    goods = [c for c in cases if c["outcome"] == "correct"]
    bads = [c for c in cases if c["outcome"] == "wrong_number"]
    for _ in range(n_pairs):
        g, b = rng.choice(goods), rng.choice(bads)
        d1 = judge_score(g, nrng, a.verbosity) + 0.10 - judge_score(b, nrng, a.verbosity)   # good shown first
        d2 = judge_score(g, nrng, a.verbosity) - (judge_score(b, nrng, a.verbosity) + 0.10) # good shown second
        flips += (d1 > 0) != (d2 > 0)
    m["pairwise_flip_rate"] = round(flips / n_pairs, 3)
    print(f"\npairwise judge: the winner changed when the order was swapped in {m['pairwise_flip_rate']:.3f} of {n_pairs} pairs")

    # 3. how many labels? corrected pass rate with a bootstrap interval, by label count
    print("\nlabels -> corrected pass rate (Rogan-Gladen), mean 95% interval width and mean absolute error over 30 draws")
    truth = float(human.mean())
    for n in (50, 100, 200, 400):
        widths, errs = [], []
        for _ in range(30):
            idx = nrng.choice(a.traffic, n, replace=False)
            h, j = human[idx], judge[idx]
            est = corrected_estimate(h, j, judge.mean())
            boots = []
            for _ in range(200):
                b = nrng.integers(0, n, n)
                e = corrected_estimate(h[b], j[b], judge.mean())
                if not math.isnan(e):
                    boots.append(e)
            if boots and not math.isnan(est):
                widths.append(np.percentile(boots, 97.5) - np.percentile(boots, 2.5))
                errs.append(abs(est - truth))
        m[f"ci_width/{n}"], m[f"abs_err/{n}"] = round(float(np.mean(widths)), 3), round(float(np.mean(errs)), 3)
        print(f"  {n:>4} labels: width {m[f'ci_width/{n}']:.3f}   error {m[f'abs_err/{n}']:.3f}")
    m["ci_width_ratio_50_to_400"] = round(m["ci_width/50"] / m["ci_width/400"], 2)

    # the headline: one labelled sample of a.labels cases, raw judge rate vs corrected estimate
    idx = nrng.choice(a.traffic, a.labels, replace=False)
    est = corrected_estimate(human[idx], judge[idx], judge.mean())
    m["labels_used"] = a.labels
    m["corrected_pass_rate"] = round(est, 3)
    m["corrected_error"] = round(abs(est - truth), 3)
    m["raw_error"] = round(abs(float(judge.mean()) - truth), 3)
    print(f"\nwith {a.labels} labels: raw judge rate off by {m['raw_error']:.3f}, corrected estimate {est:.3f} off by {m['corrected_error']:.3f}")

    # 4. regression gate power: version B is 6 points worse; how often does a one-sided test (alpha 0.05) catch it?
    pa, pb = truth, truth - 0.06
    print(f"\nregression gate: B is truly {pa - pb:.2f} worse ({pa:.3f} -> {pb:.3f}); detection rate over 400 simulated nightly runs")
    for n in (100, 400, 1600):
        hits = 0
        for _ in range(400):
            xa, xb = nrng.binomial(n, pa) / n, nrng.binomial(n, pb) / n
            se = math.sqrt(xa * (1 - xa) / n + xb * (1 - xb) / n)
            hits += (xa - xb) / se > 1.645 if se > 0 else 0
        m[f"power/{n}"] = round(hits / 400, 3)
        print(f"  {n:>5} labelled cases per version: caught {m[f'power/{n}']:.3f}")

    m["seconds"] = round(time.time() - t0, 1)
    json.dump({"metrics": m, "config": {"seed": a.seed, "traffic": a.traffic, "labels": a.labels, "verbosity": a.verbosity},
               "environment": {"python": platform.python_version(), "numpy": np.__version__, "platform": platform.platform()}},
              open(a.results, "w"), indent=1)
    print(f"\nwrote {a.results} in {m['seconds']}s")


if __name__ == "__main__":
    main()

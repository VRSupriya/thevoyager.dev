#!/usr/bin/env python3
"""A retrieval lab you can break on purpose: quantization, hybrid fusion and filtered search on synthetic data.

    python3 run.py --results results.json          # about a minute, standard library only
    python3 run.py --seed 2 --results r.json       # another random world
    python3 run.py --n 2000 --results r.json       # a smaller corpus

Everything is generated (no download, no API key). The vectors are clustered Gaussians, not real embeddings, so the
absolute numbers will differ on your data: the point is the shape of each effect and a harness you can point at your own.

Part 1  Quantization: float32 vs int8 vs 1-bit vectors, recall@10 against exact search, with and without rescoring.
Part 2  Hybrid: vector-only vs BM25-only vs reciprocal rank fusion, on meaning queries and exact-identifier queries.
Part 3  Filters: an IVF index that filters after the search vs during it, at three filter selectivities.
"""
import argparse, json, math, platform, random, time
from collections import defaultdict


def unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def make_world(rng, n, dim, topics):
    centers = [unit([rng.gauss(0, 1) for _ in range(dim)]) for _ in range(topics)]
    items = []
    for i in range(n):
        t = rng.randrange(topics)
        items.append({"id": i, "topic": t, "vec": unit([c + rng.gauss(0, 0.45 / math.sqrt(dim) * 3) for c in centers[t]])})
    return centers, items


def noisy(vec, rng, scale):
    return unit([x + rng.gauss(0, scale / math.sqrt(len(vec))) for x in vec])


def exact_top(q, vecs, k, allow=None):
    scored = [(dot(q, v), i) for i, v in enumerate(vecs) if allow is None or i in allow]
    scored.sort(reverse=True)
    return [i for _, i in scored[:k]]


def recall(found, truth):
    return len(set(found) & set(truth)) / len(truth) if truth else 1.0


# ---------------------------------------------------------------- Part 1: quantization
def part1(rng, items, queries, k=10, over=3):
    vecs = [it["vec"] for it in items]
    dim = len(vecs[0])
    lo, hi = min(min(v) for v in vecs), max(max(v) for v in vecs)
    scale = 127 / max(abs(lo), abs(hi))
    q8 = [[round(x * scale) for x in v] for v in vecs]
    bits = [sum((1 << j) for j, x in enumerate(v) if x > 0) for v in vecs]
    res = defaultdict(list)
    for q in queries:
        truth = exact_top(q, vecs, k)
        qi = [round(x * scale) for x in q]
        qb = sum((1 << j) for j, x in enumerate(q) if x > 0)
        s8 = sorted(((dot(qi, v), i) for i, v in enumerate(q8)), reverse=True)
        sb = sorted((((qb ^ b).bit_count(), i) for i, b in enumerate(bits)))
        res["int8"].append(recall([i for _, i in s8[:k]], truth))
        res["binary"].append(recall([i for _, i in sb[:k]], truth))
        for name, cand in (("int8", [i for _, i in s8[:k * over]]), ("binary", [i for _, i in sb[:k * over]])):
            best = sorted(cand, key=lambda i: -dot(q, vecs[i]))[:k]          # rescore against full precision
            res[name + "+rescore"].append(recall(best, truth))
    out = {f"recall10/{n}": round(sum(v) / len(v), 3) for n, v in res.items()}
    out["bytes_per_vector/float32"], out["bytes_per_vector/int8"], out["bytes_per_vector/binary"] = dim * 4, dim, dim / 8
    out["compression/int8"], out["compression/binary"] = 4, 32
    return out


# ---------------------------------------------------------------- Part 2: hybrid
VOCAB = [f"w{i}" for i in range(60)]


def make_text(rng, it):
    words = rng.sample(VOCAB[it["topic"] % 20 * 3: it["topic"] % 20 * 3 + 3], 3) + rng.sample(VOCAB, 2)
    return words + [f"sku{it['id']:05d}"]


class BM25:
    def __init__(self, docs, k1=1.2, b=0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.avg = sum(len(d) for d in docs) / len(docs)
        self.df = defaultdict(int)
        self.post = defaultdict(list)
        for i, d in enumerate(docs):
            tf = defaultdict(int)
            for w in d:
                tf[w] += 1
            for w, c in tf.items():
                self.df[w] += 1
                self.post[w].append((i, c))

    def search(self, words, k):
        sc = defaultdict(float)
        for w in set(words):
            if w not in self.df:
                continue
            idf = math.log(1 + (len(self.docs) - self.df[w] + 0.5) / (self.df[w] + 0.5))
            for i, c in self.post[w]:
                sc[i] += idf * c * (self.k1 + 1) / (c + self.k1 * (1 - self.b + self.b * len(self.docs[i]) / self.avg))
        return [i for i, _ in sorted(sc.items(), key=lambda x: -x[1])[:k]]


def rrf(lists, k=60, top=10):
    sc = defaultdict(float)
    for lst in lists:
        for r, i in enumerate(lst, 1):
            sc[i] += 1 / (k + r)
    return [i for i, _ in sorted(sc.items(), key=lambda x: -x[1])[:top]]


def part2(rng, items, n_queries=200):
    vecs = [it["vec"] for it in items]
    texts = [make_text(rng, it) for it in items]
    bm = BM25(texts)
    hits = defaultdict(lambda: defaultdict(list))
    for qi in range(n_queries):
        target = rng.choice(items)
        kind = "meaning" if qi % 2 == 0 else "identifier"
        if kind == "meaning":     # a paraphrase: the vector stays close; one topic word survives, the rest are synonyms
            qv = noisy(target["vec"], rng, 2.2)
            qw = [rng.choice(texts[target["id"]][:3]), "syn1", "syn2"]
        else:                     # an exact id: the embedding blurs it away (we model that as a vector far from the target)
            qv = noisy(target["vec"], rng, 4.0)
            qw = [texts[target["id"]][-1]]
        v = exact_top(qv, vecs, 50)
        b = bm.search(qw, 50)
        for name, got in (("vector", v[:10]), ("bm25", b[:10]), ("rrf", rrf([v, b]))):
            hits[kind][name].append(target["id"] in got)
    out = {}
    for kind, d in hits.items():
        for name, h in d.items():
            out[f"hit10/{kind}/{name}"] = round(sum(h) / len(h), 3)
    return out


# ---------------------------------------------------------------- Part 3: filters
def kmeans(vecs, lists, rng, iters=6):
    cents = [vecs[i] for i in rng.sample(range(len(vecs)), lists)]
    for _ in range(iters):
        groups = [[] for _ in cents]
        for i, v in enumerate(vecs):
            groups[max(range(lists), key=lambda c: dot(v, cents[c]))].append(i)
        cents = [unit([sum(vecs[i][d] for i in g) for d in range(len(vecs[0]))]) if g else cents[c] for c, g in enumerate(groups)]
    groups = [[] for _ in cents]
    for i, v in enumerate(vecs):
        groups[max(range(lists), key=lambda c: dot(v, cents[c]))].append(i)
    return cents, groups


def part3(rng, items, queries, k=10, nprobe=4, ef=40):
    vecs = [it["vec"] for it in items]
    cents, groups = kmeans(vecs, max(8, int(math.sqrt(len(items)))), rng)
    u = [rng.random() for _ in items]
    out = {}
    for sel in (0.5, 0.1, 0.01):
        allow = {i for i in range(len(items)) if u[i] < sel}
        r_post, r_in, n_post, n_in = [], [], [], []
        for q in queries:
            truth = exact_top(q, vecs, k, allow)
            probe = sorted(range(len(cents)), key=lambda c: -dot(q, cents[c]))[:nprobe]
            cand = sorted(((dot(q, vecs[i]), i) for c in probe for i in groups[c]), reverse=True)
            post = [i for _, i in cand[:ef] if i in allow][:k]          # search first, drop non-matches (HNSW ef_search style)
            inscan = [i for _, i in cand if i in allow][:k]              # test the filter while scanning the probed lists
            r_post.append(recall(post, truth)); r_in.append(recall(inscan, truth))
            n_post.append(len(post)); n_in.append(len(inscan))
        tag = f"{int(sel * 100)}pct"
        out[f"filter/{tag}/postfilter/results"] = round(sum(n_post) / len(n_post), 2)
        out[f"filter/{tag}/postfilter/recall10"] = round(sum(r_post) / len(r_post), 3)
        out[f"filter/{tag}/inscan/results"] = round(sum(n_in) / len(n_in), 2)
        out[f"filter/{tag}/inscan/recall10"] = round(sum(r_in) / len(r_in), 3)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n", type=int, default=5000, help="number of items")
    ap.add_argument("--dim", type=int, default=64)
    ap.add_argument("--queries", type=int, default=100)
    ap.add_argument("--results", default="results.json")
    a = ap.parse_args()
    t0 = time.time()
    rng = random.Random(a.seed)
    centers, items = make_world(rng, a.n, a.dim, 40)
    queries = [noisy(rng.choice(items)["vec"], rng, 0.5) for _ in range(a.queries)]
    p1 = part1(rng, items, queries)
    p2 = part2(rng, items)
    p3 = part3(rng, items, queries)
    print(f"[1] quantization, {a.n} vectors x {a.dim} dims, recall@10 vs exact search ({a.queries} queries)")
    for n in ("int8", "binary"):
        print(f"    {n:7s} alone {p1[f'recall10/{n}']:.3f}   with rescoring (oversample 3) {p1[f'recall10/{n}+rescore']:.3f}"
              f"   bytes/vector {p1[f'bytes_per_vector/{n}']:g} (float32: {p1['bytes_per_vector/float32']})")
    print("[2] hybrid, share of queries whose target is in the top 10")
    for kind in ("meaning", "identifier"):
        print(f"    {kind:10s} " + "  ".join(f"{n} {p2[f'hit10/{kind}/{n}']:.2f}" for n in ("vector", "bm25", "rrf")))
    print("[3] filters, IVF with 4 probes: results returned (of 10) and recall@10 against exact filtered search")
    for tag in ("50pct", "10pct", "1pct"):
        print(f"    {tag:6s} post-filter {p3[f'filter/{tag}/postfilter/results']:5.2f} rows, recall {p3[f'filter/{tag}/postfilter/recall10']:.3f}"
              f"   in-scan {p3[f'filter/{tag}/inscan/results']:5.2f} rows, recall {p3[f'filter/{tag}/inscan/recall10']:.3f}")
    m = {"seconds": round(time.time() - t0, 1), **p1, **p2, **p3}
    cfg = {"seed": a.seed, "n": a.n, "dim": a.dim, "queries": a.queries, "python": platform.python_version()}
    json.dump({"metrics": m, "config": cfg, "curve": []}, open(a.results, "w"), indent=1)
    print(f"\nwrote {a.results} in {m['seconds']}s")


if __name__ == "__main__":
    main()

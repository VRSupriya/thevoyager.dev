#!/usr/bin/env python3
"""A retrieval harness you can believe: chunking x retriever x query type, on a labelled synthetic contract corpus.

    python3 run.py --results results.json            # about 20 seconds on a laptop CPU, numpy only
    python3 run.py --docs 144 --results r.json       # 4x the corpus: re-test at your own scale
    python3 run.py --seed 2 --results r.json         # another random corpus

The corpus is generated (no download, no API key). The "dense" retriever is a stand-in for an embedding model:
tf-idf over words and character trigrams, reduced by SVD (LSA). Swap `Dense` for your embedder to test the real thing.
"""
import argparse, json, math, platform, random, re, time
from collections import Counter, defaultdict
import numpy as np

# ---- corpus -----------------------------------------------------------------------------------------------------
# Each clause type: heading, key sentence (with {n}), a paraphrase used in queries, and generic filler sentences.
TYPES = {
    "termination": ("Termination for convenience", "Either party may end this agreement without cause by giving {n} days written notice.", "walk away early", "exit"),
    "liability": ("Limitation of liability", "Total liability under this agreement is capped at {n} percent of the fees paid in the prior twelve months.", "maximum damages", "cap"),
    "payment": ("Payment terms", "Undisputed invoices are payable within {n} days of receipt.", "when we have to settle bills", "settle"),
    "renewal": ("Renewal", "This agreement renews automatically for successive terms of {n} months unless either party objects.", "rolls over by itself", "rollover"),
    "confidentiality": ("Confidentiality", "Confidential information must be protected for {n} years after this agreement ends.", "keeping secrets after the deal", "secrecy"),
    "audit": ("Audit rights", "The customer may audit the supplier once every {n} months on ten days notice.", "inspect the books", "inspection"),
    "breach": ("Data breach notification", "The supplier must notify the customer of a personal data breach within {n} hours.", "tell us about a leak", "incident"),
    "sla": ("Service levels", "The supplier commits to {n} percent monthly availability, measured at the service boundary.", "uptime promise", "uptime"),
    "subcontract": ("Subcontracting", "The supplier may subcontract only with written consent, and remains liable for {n} percent of subcontractor failures.", "handing work to third parties", "outsourcing"),
    "governing": ("Governing law", "Disputes are resolved by arbitration seated in a jurisdiction chosen by the parties, with a {n} day cooling-off period.", "which courts decide fights", "venue"),
}
FILLER = [
    "The parties acknowledge that this clause forms part of the entire agreement and is read together with the schedules.",
    "Nothing in this clause limits any right or remedy that cannot be excluded by applicable law.",
    "Notices under this clause must be in writing and delivered to the addresses recorded in the order form.",
    "A failure to exercise a right under this clause is not a waiver of that right.",
    "Capitalised terms have the meanings given in the definitions section of the agreement.",
    "The parties will act in good faith when applying this clause and will document any agreed exception.",
]
SYL = ["or", "len", "va", "sk", "hal", "den", "mir", "tax", "bel", "cor", "dun", "fen", "gar", "jo", "kel", "lum", "nov", "pra", "quin", "ros", "sel", "tor", "ul", "vek", "wen", "yar", "zan"]


def party(rng):
    return (rng.choice(SYL) + rng.choice(SYL) + rng.choice(SYL)).capitalize()


def make_corpus(n_docs, seed):
    rng = random.Random(seed)
    names, docs = set(), []
    while len(docs) < n_docs:
        a, b = party(rng), party(rng)
        if a == b or a in names or b in names:
            continue
        names |= {a, b}
        did = f"MSA-{1000 + len(docs) * 7 + rng.randrange(7)}"
        kinds = rng.sample(list(TYPES), 8)
        clauses = []
        for i, k in enumerate(kinds, 1):
            head, key, _, _ = TYPES[k]
            n = rng.choice([7, 10, 14, 15, 30, 45, 60, 90, 12, 24, 36])
            sentence = key.format(n=n)
            body = sentence + " " + " ".join(rng.sample(FILLER, 2))
            clauses.append({"kind": k, "heading": f"{i}. {head}", "sentence": sentence, "body": body, "n": n})
        docs.append({"id": did, "a": a, "b": b, "date": f"202{rng.randrange(3, 6)}-0{rng.randrange(1, 10)}-1{rng.randrange(0, 9)}",
                     "clauses": clauses, "supersedes": None, "renews": any(c["kind"] == "renewal" for c in clauses)})
    # a third of the documents replace an earlier one: that is the multi-hop chain
    k = max(4, n_docs // 3)
    picks = rng.sample(range(n_docs), 2 * k)
    for oi, ni in zip(picks[:k], picks[k:]):
        old, d = docs[oi], docs[ni]
        d["supersedes"] = old["id"]
        s = f"This agreement replaces {old['id']} in full from the effective date."
        d["clauses"].append({"kind": "precedence", "heading": f"{len(d['clauses']) + 1}. Order of precedence", "sentence": s,
                             "body": s + " " + FILLER[0], "n": 0})
    return docs


def doc_text(d):
    """Full text with character spans for each clause sentence."""
    head = f"MASTER SERVICES AGREEMENT {d['id']} between {d['a']} and {d['b']}, effective {d['date']}.\n"
    text, spans = head, []
    for c in d["clauses"]:
        text += c["heading"] + "\n"
        s = text.index(c["sentence"]) if c["sentence"] in text else len(text)
        start = len(text)
        text += c["body"] + "\n"
        spans.append((start, start + len(c["sentence"])))
    return text, spans


# ---- chunking ----------------------------------------------------------------------------------------------------
def make_chunks(docs, scheme, window=45):
    """Returns chunks: {doc, text, a, b, clauses: set of clause indices fully contained}."""
    chunks = []
    for d in docs:
        text, spans = doc_text(d)
        ctx = f"{d['id']}, {d['a']} and {d['b']}, effective {d['date']}. "
        if scheme.startswith("fixed"):
            words = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
            for i in range(0, len(words), window):
                a, b = words[i][0], words[min(i + window, len(words)) - 1][1]
                body = text[a:b]
                chunks.append({"doc": d["id"], "text": (ctx if scheme.endswith("ctx") else "") + body, "a": a, "b": b})
        else:
            pos = text.index(d["clauses"][0]["heading"])
            for ci, c in enumerate(d["clauses"]):
                a = text.index(c["heading"], pos)
                b = a + len(c["heading"]) + 1 + len(c["body"])
                pos = b
                body = text[a:b]
                chunks.append({"doc": d["id"], "text": (ctx if scheme.endswith("ctx") else "") + body, "a": a, "b": b})
    return chunks


def relevant(chunk, doc_id, span):
    return chunk["doc"] == doc_id and chunk["a"] <= span[0] and span[1] <= chunk["b"]


# ---- retrievers --------------------------------------------------------------------------------------------------
tok = lambda s: re.findall(r"[a-z0-9\-]+", s.lower())


class BM25:
    def __init__(self, texts, k1=1.2, b=0.75):
        self.docs = [Counter(tok(t)) for t in texts]
        self.len = np.array([sum(c.values()) for c in self.docs], float)
        self.avg, self.k1, self.b = self.len.mean(), k1, b
        df = Counter(w for c in self.docs for w in c)
        n = len(texts)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def scores(self, q):
        out = np.zeros(len(self.docs))
        for w in set(tok(q)):
            if w not in self.idf:
                continue
            for i, c in enumerate(self.docs):
                f = c.get(w, 0)
                if f:
                    out[i] += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return out


class Dense:
    """tf-idf over words + character trigrams, then rank-48 SVD. A cheap stand-in for an embedding model."""
    def __init__(self, texts, rank=48):
        self.vocab = {}
        rows = [Counter(self._feats(t)) for t in texts]
        for r in rows:
            for f in r:
                self.vocab.setdefault(f, len(self.vocab))
        X = np.zeros((len(texts), len(self.vocab)))
        for i, r in enumerate(rows):
            for f, c in r.items():
                X[i, self.vocab[f]] = 1 + math.log(c)
        df = (X > 0).sum(0)
        self.idf = np.log((1 + len(texts)) / (1 + df)) + 1
        X = X * self.idf
        U, S, Vt = np.linalg.svd(X, full_matrices=False)
        k = min(rank, len(S))
        self.V = Vt[:k].T
        self.E = self._norm(X @ self.V)

    @staticmethod
    def _feats(t):
        ws = tok(t)
        return ws + [w[i:i + 3] for w in ws for i in range(max(1, len(w) - 2))]

    @staticmethod
    def _norm(M):
        return M / (np.linalg.norm(M, axis=-1, keepdims=True) + 1e-9)

    def scores(self, q):
        v = np.zeros(len(self.vocab))
        for f, c in Counter(self._feats(q)).items():
            if f in self.vocab:
                v[self.vocab[f]] = (1 + math.log(c)) * self.idf[self.vocab[f]]
        return self.E @ self._norm(v @ self.V)


def rank_of(scores):
    return list(np.argsort(-scores, kind="stable"))


def rrf(rankings, k=60, top=100):
    s = defaultdict(float)
    for r in rankings:
        for pos, i in enumerate(r[:top], 1):
            s[i] += 1 / (k + pos)
    return sorted(s, key=lambda i: -s[i])


# ---- queries -----------------------------------------------------------------------------------------------------
def make_queries(docs, seed):
    rng = random.Random(seed + 99)
    qs = []
    for d in docs:
        for ci in rng.sample(range(len(d["clauses"])), 2):
            c = d["clauses"][ci]
            if c["kind"] == "precedence":
                continue
            head, _, para, noun = TYPES[c["kind"]]
            h = head.lower()
            qs.append({"type": "exact", "q": f"{d['id']} {h}: what does it say?", "doc": d["id"], "ci": ci})
            qs.append({"type": "party", "q": f"In the {d['a']} and {d['b']} agreement, what is the {h} term?", "doc": d["id"], "ci": ci})
            qs.append({"type": "paraphrase", "q": f"For {d['a']}, how are we protected on {para}? the {noun} provision", "doc": d["id"], "ci": ci})
    return qs


def multihop_queries(docs, seed):
    rng = random.Random(seed + 5)
    out = []
    for d in docs:
        if d["supersedes"]:
            ci = rng.choice([i for i, c in enumerate(d["clauses"]) if c["kind"] != "precedence"])
            h = TYPES[d["clauses"][ci]["kind"]][0].lower()
            out.append({"q": f"What is the {h} term in the agreement that replaced {d['supersedes']}?",
                        "doc": d["id"], "ci": ci, "bridge": d["supersedes"], "h": h})
    return out


# ---- evaluation --------------------------------------------------------------------------------------------------
def ndcg(rels, k=10):
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rels[:k]))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, int(sum(rels)) or 1)))
    return dcg / ideal if sum(rels) else 0.0


def evaluate(docs, queries, scheme, retriever, spans_by_doc, cache):
    key = scheme
    if key not in cache:
        chunks = make_chunks(docs, scheme)
        texts = [c["text"] for c in chunks]
        cache[key] = (chunks, BM25(texts), Dense(texts))
    chunks, bm, de = cache[key]
    res = defaultdict(lambda: {"hit5": [], "ndcg10": []})
    for q in queries:
        span = spans_by_doc[q["doc"]][q["ci"]]
        if retriever == "bm25":
            order = rank_of(bm.scores(q["q"]))
        elif retriever == "dense":
            order = rank_of(de.scores(q["q"]))
        else:
            order = rrf([rank_of(bm.scores(q["q"])), rank_of(de.scores(q["q"]))])
        rels = [1 if relevant(chunks[i], q["doc"], span) else 0 for i in order[:10]]
        res[q["type"]]["hit5"].append(1 if any(rels[:5]) else 0)
        res[q["type"]]["ndcg10"].append(ndcg(rels))
    return {t: {m: float(np.mean(v)) for m, v in d.items()} for t, d in res.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=36)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--results", default="results.json")
    a = ap.parse_args()
    t0 = time.time()
    docs = make_corpus(a.docs, a.seed)
    spans = {d["id"]: doc_text(d)[1] for d in docs}
    queries = make_queries(docs, a.seed)
    print(f"corpus: {len(docs)} documents, {sum(len(d['clauses']) for d in docs)} clauses, {len(queries)} single-fact queries")

    cache, table = {}, {}
    schemes = ["fixed", "fixed_ctx", "clause", "clause_ctx"]
    print(f"\n{'chunking':<11}{'retriever':<8}" + "".join(f"{t:>20}" for t in ["exact", "party", "paraphrase"]) + "   (hit@5 / nDCG@10)")
    for s in schemes:
        for r in ["bm25", "dense", "hybrid"]:
            m = evaluate(docs, queries, s, r, spans, cache)
            table[f"{s}/{r}"] = m
            print(f"{s:<11}{r:<8}" + "".join(f"{m[t]['hit5']:>11.2f} /{m[t]['ndcg10']:>6.2f} " for t in ["exact", "party", "paraphrase"]))
    metrics = {"n_docs": len(docs), "n_queries": len(queries), "n_chunks_clause": len(cache["clause"][0]), "n_chunks_fixed": len(cache["fixed"][0])}
    for cfg, m in table.items():
        for t, v in m.items():
            metrics[f"{cfg}/{t}/hit5"] = round(v["hit5"], 3)
            metrics[f"{cfg}/{t}/ndcg10"] = round(v["ndcg10"], 3)
    single = lambda cfg, key: round(float(np.mean([table[cfg][t][key] for t in ("exact", "party", "paraphrase")])), 3)
    for cfg in table:
        metrics[f"{cfg}/single/hit5"] = single(cfg, "hit5")
        metrics[f"{cfg}/single/ndcg10"] = single(cfg, "ndcg10")
    metrics["context_lift_hit5"] = round(metrics["clause_ctx/hybrid/single/hit5"] - metrics["clause/hybrid/single/hit5"], 3)
    metrics["structure_lift_hit5"] = round(metrics["clause_ctx/hybrid/single/hit5"] - metrics["fixed_ctx/hybrid/single/hit5"], 3)

    # multi-hop: the answer chunk is only findable through the bridge document id
    chunks, bm, de = cache["clause_ctx"]
    mh = multihop_queries(docs, a.seed)
    one_shot = two_step = bridge_found = 0
    for q in mh:
        span = spans[q["doc"]][q["ci"]]
        order = rrf([rank_of(bm.scores(q["q"])), rank_of(de.scores(q["q"]))])
        one_shot += any(relevant(chunks[i], q["doc"], span) for i in order[:5])
        # hop 1: find the clause that mentions the old id; read the new id from the top hit's context line
        old_q = f"{q['bridge']} replaces"
        top = chunks[rrf([rank_of(bm.scores(old_q)), rank_of(de.scores(old_q))])[0]]
        m = re.match(r"(MSA-\d+)", top["text"])
        bridge_found += bool(m and m.group(1) == q["doc"])
        hop2 = f"{m.group(1) if m else ''} {q['h']}"
        order2 = rrf([rank_of(bm.scores(hop2)), rank_of(de.scores(hop2))])
        two_step += any(relevant(chunks[i], q["doc"], span) for i in order2[:5])
    n = max(len(mh), 1)
    metrics.update({"multihop_queries": len(mh), "multihop_one_shot_hit5": round(one_shot / n, 3),
                    "multihop_bridge_found": round(bridge_found / n, 3), "multihop_two_step_hit5": round(two_step / n, 3)})
    print(f"\nmulti-hop ({len(mh)} questions): one query {one_shot/n:.2f}, two queries (bridge found {bridge_found/n:.2f}) {two_step/n:.2f}")

    # aggregation: "how many agreements renew automatically?" is a count over documents, not a passage lookup
    gold = sum(d["renews"] for d in docs)
    rn = [i for i, c in enumerate(chunks) if "renews automatically" in c["text"]]
    order = rrf([rank_of(bm.scores("agreements that renew automatically")), rank_of(de.scores("agreements that renew automatically"))])
    in_top10 = len(set(order[:10]) & set(rn))
    metrics.update({"aggregation_gold_count": gold, "aggregation_found_in_top10": in_top10,
                    "aggregation_recall_at_10": round(in_top10 / max(gold, 1), 3)})
    print(f"aggregation: {gold} agreements auto-renew; the top 10 chunks contain {in_top10} of them (a count in code over a table is exact)")

    metrics["seconds"] = round(time.time() - t0, 1)
    out = {"metrics": metrics, "config": {"docs": a.docs, "seed": a.seed, "rank": 48, "window_words": 45, "rrf_k": 60},
           "environment": {"python": platform.python_version(), "numpy": np.__version__, "platform": platform.platform()}}
    json.dump(out, open(a.results, "w"), indent=1)
    print(f"\nwrote {a.results} in {metrics['seconds']}s")


if __name__ == "__main__":
    main()

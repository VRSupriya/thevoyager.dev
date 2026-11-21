#!/usr/bin/env python3
"""A document ingestion pipeline you can break on purpose: extraction with a validation gate, and an incremental
re-index that survives a crash.

    python3 run.py --results results.json            # a few seconds, standard library only
    python3 run.py --seed 2 --results r.json         # another random world
    python3 run.py --noise 0 --results r.json        # perfect scans: what does the gate cost when nothing is wrong?

Everything is generated (no download, no API key). Part 1 makes synthetic invoices, damages their text the way
OCR does (digit and letter confusions), extracts fields with regexes, and compares "trust the parser" with "accept
only what passes cheap checks, send the rest to review". Part 2 re-indexes a corpus after edits with three chunking
and ID schemes and counts how many chunks must be re-embedded. Part 3 kills the update job halfway and checks what
a query could see and what a retry leaves behind.
"""
import argparse, hashlib, json, platform, random, re, time

# ---------------------------------------------------------------- Part 1: extraction
LETTER = {"0": "O", "1": "l", "5": "S", "8": "B"}        # digit -> letter: visible to a pattern check
DIGIT = {"3": "8", "8": "3", "1": "7", "7": "1", "6": "5", "5": "6"}   # digit -> digit: invisible to it
VENDORS = ["Northwind Repairs", "Alder Glass", "Kestrel Motors", "Bluebird Clinics", "Harbour Plumbing"]


def make_invoice(rng):
    items = [(f"item {i+1}", rng.randint(1, 4), rng.randint(20, 900)) for i in range(rng.randint(2, 6))]
    return {"no": f"INV-{rng.randint(100000, 999999)}", "vendor": rng.choice(VENDORS),
            "date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}", "items": items,
            "total": sum(q * u for _, q, u in items)}


def render(inv):
    lines = [inv["vendor"], f"Invoice {inv['no']}", f"Date {inv['date']}"]
    lines += [f"{d} {q} x {u} = {q * u}" for d, q, u in inv["items"]]
    return lines + [f"Total due {inv['total']}"]


def damage(lines, rng, p):
    """OCR-style damage on digits only: some confusions become letters, some stay digits."""
    out = []
    for line in lines:
        s = []
        for ch in line:
            if ch.isdigit() and rng.random() < p:
                s.append(DIGIT.get(ch, ch) if rng.random() < 0.5 else LETTER.get(ch, DIGIT.get(ch, ch)))
            else:
                s.append(ch)
        out.append("".join(s))
    return out


def extract(lines):
    text = "\n".join(lines)
    no = re.search(r"Invoice (\S+)", text)
    tot = re.search(r"Total due (\S+)", text)
    amts = re.findall(r"= (\S+)", text)
    return {"no": no and no.group(1), "total": tot and tot.group(1), "amts": amts}


def passes_checks(e):
    """Cheap, deterministic checks. Note what each one can and cannot see."""
    if not (e["no"] and re.fullmatch(r"INV-\d{6}", e["no"])):
        return False
    if not (e["total"] and e["total"].isdigit() and all(a.isdigit() for a in e["amts"])):
        return False
    return sum(int(a) for a in e["amts"]) == int(e["total"])


def part1(rng, n, noise, p_scan):
    rows = []
    for _ in range(n):
        inv = make_invoice(rng)
        scan = rng.random() < p_scan
        lines = damage(render(inv), rng, noise) if scan else render(inv)
        e = extract(lines)
        rows.append({"scan": scan, "ok_no": e["no"] == inv["no"], "ok_total": e["total"] == str(inv["total"]),
                     "valid": passes_checks(e)})
    wrong = lambda r: not (r["ok_no"] and r["ok_total"])
    acc = [r for r in rows if r["valid"]]
    silent = [r for r in acc if wrong(r)]
    return {"docs": n, "scan_share": round(sum(r["scan"] for r in rows) / n, 3),
            "trust_parser/wrong_rate": round(sum(wrong(r) for r in rows) / n, 4),
            "gate/review_rate": round(1 - len(acc) / n, 4),
            "gate/silent_wrong_rate": round(len(silent) / n, 4),
            "gate/silent_wrong_on_invoice_no": round(sum(not r["ok_no"] for r in silent) / max(len(silent), 1), 3),
            "gate/silent_wrong_on_total": round(sum(not r["ok_total"] for r in silent) / n, 4),
            "gate/caught_of_wrong": round(1 - len(silent) / max(sum(wrong(r) for r in rows), 1), 3),
            "digital/wrong_rate": round(sum(wrong(r) for r in rows if not r["scan"]) / max(sum(not r["scan"] for r in rows), 1), 4)}


# ---------------------------------------------------------------- Part 2: incremental re-index
WORDS = "claim policy adjuster payout excess deductible vehicle repair estimate inspection invoice tax glass".split()


def make_doc(rng):
    return [" ".join(rng.choices(WORDS, k=rng.randint(25, 45))) + f" ref{rng.randint(0, 10**6)}." for _ in range(12)]


def h(*parts):
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:12]


def chunks(doc_id, paras, scheme):
    """Return {chunk_id: text}. fixed/position: windows of 3 paragraphs, id = doc + index.
    fixed/hash: the same windows, id = hash of text. para/hash: one chunk per paragraph, id = hash of text."""
    if scheme == "para/hash":
        return {h(doc_id, p): p for p in paras}
    wins = [" ".join(paras[i:i + 3]) for i in range(0, len(paras), 3)]
    if scheme == "fixed/position":
        return {f"{doc_id}:{i}": w for i, w in enumerate(wins)}
    return {h(doc_id, w): w for w in wins}


def edit(paras, kind, rng):
    p = list(paras)
    i = rng.randrange(1, len(p) - 1)
    if kind == "insert":
        p.insert(i, "new clause " + " ".join(rng.choices(WORDS, k=30)) + f" ref{rng.randint(0, 10**6)}.")
    elif kind == "change_word":
        p[i] = p[i].replace(p[i].split()[0], "amended", 1)
    else:
        del p[i]
    return p


def part2(rng, n_docs, edit_share):
    docs = {f"d{i}": make_doc(rng) for i in range(n_docs)}
    touched = rng.sample(sorted(docs), max(1, int(n_docs * edit_share)))
    out = {}
    for scheme in ["fixed/position", "fixed/hash", "para/hash"]:
        for kind in ["change_word", "insert", "delete"]:
            r2 = random.Random(rng.random())
            adds = dels = total = 0
            for d in touched:
                old, new = chunks(d, docs[d], scheme), chunks(d, edit(docs[d], kind, r2), scheme)
                # an id that exists in both with the same text needs nothing; a changed text under the same id is an upsert
                adds += sum(1 for k, v in new.items() if old.get(k) != v)
                dels += sum(1 for k in old if k not in new)
                total += len(new)
            out[f"{scheme}/{kind}"] = {"embeds": adds, "deletes": dels, "chunks_after": total,
                                       "embed_share": round(adds / total, 3)}
    out["_docs_edited"] = len(touched)
    return out


# ---------------------------------------------------------------- Part 3: crash during the update
class Index:
    def __init__(self):
        self.rows, self.ops, self.crash_at, self.dark = {}, 0, None, 0   # id -> (doc, text)

    def op(self):
        self.ops += 1
        if self.crash_at is not None and self.ops >= self.crash_at:
            raise RuntimeError("worker died")

    def put(self, i, doc, text): self.op(); self.rows[i] = (doc, text)
    def drop(self, i): self.op(); self.rows.pop(i, None)
    def of(self, doc): return {i: t for i, (d, t) in self.rows.items() if d == doc}


def delete_first(ix, doc, new_paras, uid, old_ids):
    """Delete the old rows, then insert the new ones under fresh ids."""
    for i in list(ix.of(doc)): ix.drop(i)
    for p in new_paras: ix.put(f"{doc}:{next(uid)}", doc, p)


def insert_first(ix, doc, new_paras, uid, old_ids):
    """Insert the new rows under fresh ids, then delete the old ids the queue message carried. A retry replays the
    same message: it inserts again and the first attempt's rows stay behind."""
    for p in new_paras: ix.put(f"{doc}:{next(uid)}", doc, p)
    for i in old_ids: ix.drop(i)


def reconcile(ix, doc, new_paras, uid, old_ids):
    """Diff desired against current under content-hash ids, add what is missing first, delete what is extra last.
    Replaying it any number of times leaves the same index."""
    want = {h(doc, p): p for p in new_paras}
    have = ix.of(doc)
    for i, t in want.items():
        if i not in have: ix.put(i, doc, t)
    for i in have:
        if i not in want: ix.drop(i)


def part3(rng, n_docs, crash_share):
    out = {}
    for name, fn in [("delete_first", delete_first), ("insert_first", insert_first), ("reconcile", reconcile)]:
        r = random.Random(rng.random())
        ix, uid = Index(), iter(range(10**9))
        docs = {f"d{i}": make_doc(r) for i in range(n_docs)}
        for d, ps in docs.items():
            (reconcile if name == "reconcile" else insert_first)(ix, d, ps, uid, [])
        new = {d: edit(ps, "insert", r) for d, ps in docs.items()}
        crashed = set(r.sample(sorted(docs), int(n_docs * crash_share)))
        empty = partial = 0
        for d in docs:
            old_ids = list(ix.of(d))
            ix.ops, ix.crash_at = 0, (r.randint(2, 24) if d in crashed else None)
            try:
                fn(ix, d, new[d], uid, old_ids)
            except RuntimeError:
                texts = set(ix.of(d).values())
                empty += (len(texts) == 0)                     # a query now finds nothing for this document
                partial += (len(ix.of(d)) < len(old_ids))        # fewer chunks searchable than before the update
                ix.ops, ix.crash_at = 0, None
                fn(ix, d, new[d], uid, old_ids)                # the worker restarts and retries the same message
        extra = sum(len(ix.of(d)) - len(new[d]) for d in docs)
        out[name] = {"crashed_docs": len(crashed), "empty_after_crash": empty, "fewer_chunks_than_before_after_crash": partial,
                     "extra_chunks_after_retry": extra}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--noise", type=float, default=0.02, help="per-digit damage probability on scanned pages")
    ap.add_argument("--invoices", type=int, default=3000)
    ap.add_argument("--scan-share", type=float, default=0.4)
    ap.add_argument("--docs", type=int, default=400)
    ap.add_argument("--results", default="results.json")
    a = ap.parse_args()
    t0 = time.time()
    rng = random.Random(a.seed)
    p1 = part1(rng, a.invoices, a.noise, a.scan_share)
    p2 = part2(rng, a.docs, 0.05)
    p3 = part3(rng, a.docs, 0.2)
    print(f"[1] {p1['docs']} invoices, {p1['scan_share']:.0%} scanned, noise {a.noise}")
    print(f"    trust the parser: {p1['trust_parser/wrong_rate']:.2%} wrong ({p1['digital/wrong_rate']:.2%} on digital PDFs)")
    print(f"    with checks: {p1['gate/review_rate']:.2%} to review, {p1['gate/silent_wrong_rate']:.2%} wrong and accepted "
          f"({p1['gate/silent_wrong_on_invoice_no']:.0%} of those in the invoice number)")
    print(f"[2] {p2['_docs_edited']} of {a.docs} documents edited; chunks re-embedded (share of the edited documents' chunks)")
    for k, v in p2.items():
        if k != "_docs_edited":
            print(f"    {k:26s} embeds {v['embeds']:4d} deletes {v['deletes']:4d} share {v['embed_share']:.0%}")
    print("[3] worker dies mid-update on 20% of documents, restarts and retries the same message")
    for k, v in p3.items():
        print(f"    {k:10s} {v}")
    m = {"seconds": round(time.time() - t0, 1), **p1}
    for k, v in p2.items():
        if k != "_docs_edited":
            m[f"embed_share/{k}"] = v["embed_share"]
    m["embed_ratio_insert_fixed_over_para"] = round(m["embed_share/fixed/hash/insert"] / m["embed_share/para/hash/insert"], 1)
    for k, v in p3.items():
        for kk, vv in v.items():
            m[f"crash/{k}/{kk}"] = vv
    cfg = {"seed": a.seed, "noise": a.noise, "invoices": a.invoices, "scan_share": a.scan_share, "docs": a.docs,
           "python": platform.python_version()}
    json.dump({"metrics": m, "config": cfg, "curve": [], "part2": p2}, open(a.results, "w"), indent=1)
    print(f"\nwrote {a.results} in {m['seconds']}s")


if __name__ == "__main__":
    main()

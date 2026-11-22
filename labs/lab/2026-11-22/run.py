"""Lab, Step 1 (part 5): generate from the small GPT, cache what it already computed, and see what sampling does.

    python3 run.py                         # train the part-4 baseline (~1.5 min on 4 CPU cores), then the three studies
    python3 run.py --results results.json  # also write the metrics the pass gates check (expected.json)
    python3 run.py --seed 2                # a second seed: how much do the sampling numbers move?

Studies after training:
  1. KV cache is exact: greedy decoding with and without the cache must give the same tokens and the same logits.
     A deliberately wrong position offset shows what the failure looks like.
  2. KV cache is fast: seconds to generate N tokens, cached vs recomputing the whole prefix, N = 64..512 (random weights;
     timing does not depend on training).
  3. Sampling: temperature, top-k and top-p, scored by the share of generated words that exist in the training text,
     the diversity of the samples, and how surprised the model is by its own output.
Same character-level model and recipe as part 4 (pre-norm blocks, AdamW, warmup + cosine, clipping, honest eval).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import time
import urllib.request
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

DATA_URL = "https://raw.githubusercontent.com/karpathy/char-rnn/master/data/tinyshakespeare/input.txt"
HERE = Path(__file__).resolve().parent

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("--n_layer", type=int, default=4)
p.add_argument("--n_head", type=int, default=4)
p.add_argument("--n_embd", type=int, default=128)
p.add_argument("--block_size", type=int, default=64)
p.add_argument("--batch", type=int, default=12)
p.add_argument("--iters", type=int, default=2000)
p.add_argument("--lr", type=float, default=1e-3)
p.add_argument("--warmup", type=int, default=100)
p.add_argument("--clip", type=float, default=1.0)
p.add_argument("--eval_iters", type=int, default=50)
p.add_argument("--seed", type=int, default=1337)
p.add_argument("--threads", type=int, default=4)
p.add_argument("--samples", type=int, default=200, help="samples per sampling setting")
p.add_argument("--data", type=Path, default=HERE / "input.txt", help="downloaded on first run")
p.add_argument("--results", type=Path, default=None, help="write metrics here (JSON)")
a = p.parse_args()
torch.manual_seed(a.seed)
torch.set_num_threads(a.threads)

# ── Data ─────────────────────────────────────────────────────────────────────
if not a.data.exists():
    urllib.request.urlretrieve(DATA_URL, a.data)
text = a.data.read_text()
chars = sorted(set(text))
V = len(chars)
stoi = {c: i for i, c in enumerate(chars)}
itos = {i: c for c, i in stoi.items()}
data = torch.tensor([stoi[c] for c in text], dtype=torch.long)
n = int(0.9 * len(data))
train_data, val_data = data[:n], data[n:]
train_text = text[:n]


def get_batch(split: str):
    src = train_data if split == "train" else val_data
    ix = torch.randint(len(src) - a.block_size - 1, (a.batch,))
    x = torch.stack([src[i:i + a.block_size] for i in ix])
    y = torch.stack([src[i + 1:i + 1 + a.block_size] for i in ix])
    return x, y


# ── Model: part 4's GPT, plus an optional KV cache ───────────────────────────
class CausalSelfAttention(nn.Module):
    def __init__(self, n_embd, n_head, block_size):
        super().__init__()
        self.n_head = n_head
        self.qkv = nn.Linear(n_embd, 3 * n_embd, bias=False)
        self.proj = nn.Linear(n_embd, n_embd, bias=False)

    def forward(self, x, cache=None):
        B, T, C = x.shape
        q, k, v = self.qkv(x).split(C, dim=2)
        hs = C // self.n_head
        q, k, v = (t.view(B, T, self.n_head, hs).transpose(1, 2) for t in (q, k, v))
        past = 0
        if cache is not None:                      # cache holds this layer's keys and values for all earlier tokens
            if "k" in cache:
                k, v = torch.cat([cache["k"], k], dim=2), torch.cat([cache["v"], v], dim=2)
            cache["k"], cache["v"] = k, v
            past = k.shape[2] - T
        att = (q @ k.transpose(-2, -1)) / math.sqrt(hs)
        # new token i may see every cached token plus new tokens up to itself
        allowed = torch.ones(T, past + T, dtype=torch.bool).tril(diagonal=past)
        att = att.masked_fill(~allowed, float("-inf"))
        y = F.softmax(att, dim=-1) @ v
        return self.proj(y.transpose(1, 2).contiguous().view(B, T, C))


class Block(nn.Module):
    def __init__(self, n_embd, n_head, block_size):
        super().__init__()
        self.ln1, self.ln2 = nn.LayerNorm(n_embd), nn.LayerNorm(n_embd)
        self.attn = CausalSelfAttention(n_embd, n_head, block_size)
        self.mlp = nn.Sequential(nn.Linear(n_embd, 4 * n_embd), nn.GELU(), nn.Linear(4 * n_embd, n_embd))

    def forward(self, x, cache=None):
        x = x + self.attn(self.ln1(x), cache)
        return x + self.mlp(self.ln2(x))


class GPT(nn.Module):
    def __init__(self, n_layer, n_head, n_embd, block_size):
        super().__init__()
        self.n_layer, self.n_head, self.n_embd, self.block_size = n_layer, n_head, n_embd, block_size
        self.tok, self.pos = nn.Embedding(V, n_embd), nn.Embedding(block_size, n_embd)
        self.blocks = nn.ModuleList([Block(n_embd, n_head, block_size) for _ in range(n_layer)])
        self.ln_f, self.head = nn.LayerNorm(n_embd), nn.Linear(n_embd, V, bias=False)
        self.apply(self._init)

    @staticmethod
    def _init(m):
        if isinstance(m, (nn.Linear, nn.Embedding)):
            nn.init.normal_(m.weight, std=0.02)

    def new_cache(self):
        return [{} for _ in range(self.n_layer)]

    def forward(self, idx, targets=None, cache=None, pos_bug=False):
        B, T = idx.shape
        past = 0 if cache is None or "k" not in cache[0] else cache[0]["k"].shape[2]
        start = 0 if pos_bug else past             # the bug: new tokens get positions 0.. instead of past..
        x = self.tok(idx) + self.pos(torch.arange(start, start + T))
        for i, blk in enumerate(self.blocks):
            x = blk(x, None if cache is None else cache[i])
        logits = self.head(self.ln_f(x))
        loss = None if targets is None else F.cross_entropy(logits.reshape(-1, V), targets.reshape(-1))
        return logits, loss


model = GPT(a.n_layer, a.n_head, a.n_embd, a.block_size)
n_params = sum(p.numel() for p in model.parameters())
decay = [p for p in model.parameters() if p.dim() >= 2]
no_decay = [p for p in model.parameters() if p.dim() < 2]
opt = torch.optim.AdamW([{"params": decay, "weight_decay": 0.1}, {"params": no_decay, "weight_decay": 0.0}], lr=a.lr, betas=(0.9, 0.99))


def lr_at(step):
    if step < a.warmup:
        return a.lr * (step + 1) / a.warmup
    progress = (step - a.warmup) / max(1, a.iters - a.warmup)
    return a.lr * 0.1 + 0.5 * (a.lr - a.lr * 0.1) * (1 + math.cos(math.pi * progress))


@torch.no_grad()
def evaluate():
    model.eval()
    out = {s: sum(model(*get_batch(s))[1].item() for _ in range(a.eval_iters)) / a.eval_iters for s in ("train", "val")}
    model.train()
    return out


# ── Train (same recipe as part 4's baseline) ─────────────────────────────────
t0 = time.time()
init = evaluate()
for step in range(a.iters):
    for g in opt.param_groups:
        g["lr"] = lr_at(step)
    _, loss = model(*get_batch("train"))
    opt.zero_grad(set_to_none=True)
    loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), a.clip)
    opt.step()
final = evaluate()
train_seconds = time.time() - t0
print(f"params {n_params:,} | starting val {init['val']:.3f} | final train {final['train']:.4f} val {final['val']:.4f} | {train_seconds:.0f}s")
model.eval()

# ── Study 1: is the KV cache exact? ──────────────────────────────────────────
PROMPT, NEW = 8, a.block_size - 8
prompt_ids = val_data[:PROMPT].unsqueeze(0)


@torch.no_grad()
def greedy(use_cache: bool, pos_bug: bool = False):
    """Greedy-decode NEW tokens; return the tokens and the last-position logits at every step."""
    idx, cache, all_logits = prompt_ids.clone(), model.new_cache() if use_cache else None, []
    feed = idx
    for _ in range(NEW):
        logits, _ = model(feed, cache=cache, pos_bug=pos_bug) if use_cache else model(idx)
        last = logits[:, -1]
        all_logits.append(last)
        nxt = last.argmax(-1, keepdim=True)
        idx = torch.cat([idx, nxt], dim=1)
        feed = nxt
    return idx, torch.stack(all_logits)


with torch.no_grad():
    ref_ids, ref_logits = greedy(False)
    kv_ids, kv_logits = greedy(True)
    bug_ids, bug_logits = greedy(True, pos_bug=True)
cache_max_diff = (ref_logits - kv_logits).abs().max().item()
cache_same = int(torch.equal(ref_ids, kv_ids))
bug_max_diff = (ref_logits - bug_logits).abs().max().item()
bug_first_diff = next((i for i in range(NEW) if ref_ids[0, PROMPT + i] != bug_ids[0, PROMPT + i]), -1)
bug_same_share = (ref_ids[0, PROMPT:] == bug_ids[0, PROMPT:]).float().mean().item()
print(f"cache exact: tokens identical={bool(cache_same)} max |logit diff|={cache_max_diff:.2e} | "
      f"position bug: max diff={bug_max_diff:.2f}, first different token at step {bug_first_diff}, {bug_same_share:.0%} of tokens still match")

# ── Study 2: how fast? (random weights, longer block; timing does not need training) ──
LENGTHS = [64, 128, 256, 512]
big = GPT(a.n_layer, a.n_head, a.n_embd, max(LENGTHS)).eval()


@torch.no_grad()
def time_generate(m, n_new: int, use_cache: bool) -> float:
    idx, cache = torch.zeros((1, 1), dtype=torch.long), m.new_cache() if use_cache else None
    feed = idx
    t = time.perf_counter()
    for _ in range(n_new - 1):
        logits, _ = m(feed, cache=cache) if use_cache else m(idx)
        nxt = logits[:, -1].argmax(-1, keepdim=True)
        idx = torch.cat([idx, nxt], dim=1)
        feed = nxt
    return time.perf_counter() - t


timing = {}
for L in LENGTHS:
    best = {}
    for mode in (False, True):
        best[mode] = min(time_generate(big, L, mode) for _ in range(3))  # best of 3 to cut scheduler noise
    timing[L] = {"no_cache_s": round(best[False], 3), "cache_s": round(best[True], 3), "speedup": round(best[False] / best[True], 1)}
    print(f"generate {L:3d} tokens: recompute {best[False]:.2f}s | cached {best[True]:.2f}s | {best[False] / best[True]:.1f}x")

# cache memory for this model, per sequence: K and V, every layer, 4 bytes (fp32)
head_dim = a.n_embd // a.n_head
kv_bytes_per_token = 2 * a.n_layer * a.n_head * head_dim * 4

# ── Study 3: sampling ────────────────────────────────────────────────────────
train_words = set(re.findall(r"[a-z']+", train_text.lower()))


def sample_batch(temperature=1.0, top_k=None, top_p=None, n=a.samples, seed=0):
    """Cached, batched sampling from a one-token prompt (newline). Returns n strings of block_size characters."""
    g = torch.Generator().manual_seed(seed)
    idx = torch.full((n, 1), stoi["\n"], dtype=torch.long)
    cache, feed = model.new_cache(), idx
    with torch.no_grad():
        for _ in range(a.block_size - 1):
            logits = model(feed, cache=cache)[0][:, -1]
            if temperature == 0:
                nxt = logits.argmax(-1, keepdim=True)
            else:
                logits = logits / temperature
                if top_k:
                    kth = logits.topk(top_k, dim=-1).values[:, -1:]
                    logits = logits.masked_fill(logits < kth, float("-inf"))
                if top_p:
                    sl, si = logits.sort(dim=-1, descending=True)
                    cum = F.softmax(sl, -1).cumsum(-1)
                    drop = (cum - F.softmax(sl, -1)) > top_p      # keep the smallest set whose mass reaches top_p
                    sl = sl.masked_fill(drop, float("-inf"))
                    logits = torch.full_like(logits, float("-inf")).scatter(-1, si, sl)
                nxt = torch.multinomial(F.softmax(logits, -1), 1, generator=g)
            idx = torch.cat([idx, nxt], dim=1)
            feed = nxt
    return idx


@torch.no_grad()
def score(idx):
    strs = ["".join(itos[i] for i in row.tolist()) for row in idx]
    words = []
    for s in strs:
        w = re.findall(r"[A-Za-z']+", s)
        words += [x.lower() for x in w[1:-1]]                    # drop first and last: they may be cut by the window
    valid = sum(w in train_words for w in words) / max(1, len(words))
    diversity = len(set(words)) / max(1, len(words))
    logits, loss = model(idx[:, :-1], idx[:, 1:])
    return {"valid_word_share": round(valid, 4), "unique_word_share": round(diversity, 4), "self_nll": round(loss.item(), 4),
            "distinct_samples": len({s for s in strs})}


settings = [("greedy", dict(temperature=0)), ("T=0.5", dict(temperature=0.5)), ("T=0.8", dict(temperature=0.8)),
            ("T=1.0", dict(temperature=1.0)), ("T=1.3", dict(temperature=1.3)),
            ("top-k=5", dict(top_k=5)), ("top-p=0.9", dict(top_p=0.9))]
sampling = {}
for name, kw in settings:
    sampling[name] = score(sample_batch(**kw, seed=a.seed))
    s = sampling[name]
    print(f"{name:10s} valid words {s['valid_word_share']:.1%} | unique words {s['unique_word_share']:.1%} | self-NLL {s['self_nll']:.3f} | distinct samples {s['distinct_samples']}/{a.samples}")
example = {name: "".join(itos[i] for i in sample_batch(**kw, n=1, seed=a.seed)[0].tolist()).replace("\n", " / ")
           for name, kw in [("T=0.5", dict(temperature=0.5)), ("T=1.0", dict(temperature=1.0)), ("T=1.3", dict(temperature=1.3))]}

metrics = {
    "params": n_params, "init_val_loss": round(init["val"], 4), "val_loss": final["val"], "train_val_gap": round(final["val"] - final["train"], 4),
    "cache_tokens_identical": cache_same, "cache_max_logit_diff": float(f"{cache_max_diff:.3e}"),
    "pos_bug_max_logit_diff": round(bug_max_diff, 3), "pos_bug_first_diff_step": bug_first_diff, "pos_bug_token_match": round(bug_same_share, 4),
    "speedup_256": timing[256]["speedup"], "speedup_512": timing[512]["speedup"],
    "kv_bytes_per_token": kv_bytes_per_token,
    "valid_words_T05": sampling["T=0.5"]["valid_word_share"], "valid_words_T13": sampling["T=1.3"]["valid_word_share"],
    "valid_word_gap_T05_T13": round(sampling["T=0.5"]["valid_word_share"] - sampling["T=1.3"]["valid_word_share"], 4),
    "greedy_distinct_samples": sampling["greedy"]["distinct_samples"],
    "train_seconds": round(train_seconds, 1),
}
print(json.dumps(metrics, indent=1))
if a.results:
    a.results.write_text(json.dumps({
        "lab": "lab/2026-11-22", "metrics": metrics,
        "config": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(a).items() if k not in ("results", "data")},
        "curve": [[L, timing[L]["no_cache_s"], timing[L]["cache_s"]] for L in LENGTHS],
        "timing": timing, "sampling": sampling, "examples": example,
        "environment": {"python": platform.python_version(), "torch": torch.__version__, "platform": platform.platform(),
                        "threads": a.threads, "cpu_count": os.cpu_count()},
    }, indent=1))

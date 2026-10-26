"""Lab, Step 1: a small GPT you can read in one sitting (parts 1-3 in one runnable file).

    python3 run.py                         # the reference run: ~0.8M params, 2,000 steps, a few minutes on a laptop CPU
    python3 run.py --results results.json  # also write the metrics the pass gates check (expected.json)
    python3 run.py --break no_mask         # Part 2's breakage experiments: no_mask, no_scale, no_resid, high_lr, no_warmup

Character-level model on Tiny Shakespeare, nanoGPT-style: pre-norm blocks, causal self-attention scaled by
1/sqrt(d_head), AdamW with weight decay on matrices only, linear warmup then cosine decay, gradient clipping,
and an honest eval (mean loss over many held-out batches). Everything a reader needs is in this file.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
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
p.add_argument("--eval_every", type=int, default=250)
p.add_argument("--eval_iters", type=int, default=50)
p.add_argument("--seed", type=int, default=1337)
p.add_argument("--threads", type=int, default=4)
p.add_argument("--break", dest="breakage", default=None,
               choices=["no_mask", "no_scale", "no_resid", "high_lr", "no_warmup"],
               help="run one of Part 2's deliberate breakages")
p.add_argument("--data", type=Path, default=HERE / "input.txt", help="downloaded on first run")
p.add_argument("--results", type=Path, default=None, help="write metrics here (JSON)")
a = p.parse_args()
if a.breakage == "high_lr":
    a.lr = 2e-2
if a.breakage == "no_warmup":
    a.warmup = 0
torch.manual_seed(a.seed)
torch.set_num_threads(a.threads)

# ── Data: characters as tokens, last 10% held out ────────────────────────────
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


def get_batch(split: str):
    src = train_data if split == "train" else val_data
    ix = torch.randint(len(src) - a.block_size - 1, (a.batch,))
    x = torch.stack([src[i:i + a.block_size] for i in ix])
    y = torch.stack([src[i + 1:i + 1 + a.block_size] for i in ix])  # the target is the next character
    return x, y


# The target-shift check from Part 1: the starting loss can't catch an off-by-one shift, this can.
_x, _y = get_batch("train")
assert torch.equal(_y[:, :-1], _x[:, 1:]), "targets must be the inputs shifted by one"


# ── Model ────────────────────────────────────────────────────────────────────
class CausalSelfAttention(nn.Module):
    def __init__(self):
        super().__init__()
        self.qkv = nn.Linear(a.n_embd, 3 * a.n_embd, bias=False)
        self.proj = nn.Linear(a.n_embd, a.n_embd, bias=False)
        self.register_buffer("mask", torch.tril(torch.ones(a.block_size, a.block_size)).bool())

    def forward(self, x):
        B, T, C = x.shape
        q, k, v = self.qkv(x).split(C, dim=2)
        hs = C // a.n_head
        q, k, v = (t.view(B, T, a.n_head, hs).transpose(1, 2) for t in (q, k, v))
        att = q @ k.transpose(-2, -1)
        if a.breakage != "no_scale":
            att = att / math.sqrt(hs)  # keeps softmax inputs at unit scale as d_head grows
        if a.breakage != "no_mask":
            att = att.masked_fill(~self.mask[:T, :T], float("-inf"))  # no peeking at future tokens
        y = F.softmax(att, dim=-1) @ v
        return self.proj(y.transpose(1, 2).contiguous().view(B, T, C))


class Block(nn.Module):
    def __init__(self):
        super().__init__()
        self.ln1, self.ln2 = nn.LayerNorm(a.n_embd), nn.LayerNorm(a.n_embd)
        self.attn = CausalSelfAttention()
        self.mlp = nn.Sequential(nn.Linear(a.n_embd, 4 * a.n_embd), nn.GELU(), nn.Linear(4 * a.n_embd, a.n_embd))

    def forward(self, x):
        if a.breakage == "no_resid":
            return self.mlp(self.ln2(self.attn(self.ln1(x))))
        x = x + self.attn(self.ln1(x))  # pre-norm: normalise the input to each sublayer, add back the residual
        return x + self.mlp(self.ln2(x))


class GPT(nn.Module):
    def __init__(self):
        super().__init__()
        self.tok = nn.Embedding(V, a.n_embd)
        self.pos = nn.Embedding(a.block_size, a.n_embd)
        self.blocks = nn.Sequential(*[Block() for _ in range(a.n_layer)])
        self.ln_f = nn.LayerNorm(a.n_embd)
        self.head = nn.Linear(a.n_embd, V, bias=False)
        self.apply(self._init)

    @staticmethod
    def _init(m):
        if isinstance(m, (nn.Linear, nn.Embedding)):
            nn.init.normal_(m.weight, std=0.02)  # small init keeps the starting loss near ln(V)
        if isinstance(m, nn.Linear) and m.bias is not None:
            nn.init.zeros_(m.bias)

    def forward(self, idx, targets=None):
        T = idx.shape[1]
        x = self.tok(idx) + self.pos(torch.arange(T))
        logits = self.head(self.ln_f(self.blocks(x)))
        loss = None if targets is None else F.cross_entropy(logits.view(-1, V), targets.view(-1))
        return logits, loss

    @torch.no_grad()
    def generate(self, idx, n_new):
        for _ in range(n_new):
            logits, _ = self(idx[:, -a.block_size:])
            idx = torch.cat([idx, torch.multinomial(F.softmax(logits[:, -1], -1), 1)], dim=1)
        return idx


model = GPT()
n_params = sum(p.numel() for p in model.parameters())
decay = [p for p in model.parameters() if p.dim() >= 2]      # weight decay on matrices only,
no_decay = [p for p in model.parameters() if p.dim() < 2]    # not on norms and biases
opt = torch.optim.AdamW([{"params": decay, "weight_decay": 0.1}, {"params": no_decay, "weight_decay": 0.0}],
                        lr=a.lr, betas=(0.9, 0.99))


def lr_at(step: int) -> float:
    if a.warmup and step < a.warmup:
        return a.lr * (step + 1) / a.warmup
    progress = (step - a.warmup) / max(1, a.iters - a.warmup)
    return a.lr * 0.1 + 0.5 * (a.lr - a.lr * 0.1) * (1 + math.cos(math.pi * progress))  # cosine to 10% of peak


@torch.no_grad()
def evaluate() -> dict:
    model.eval()
    out = {}
    for split in ("train", "val"):
        out[split] = sum(model(*get_batch(split))[1].item() for _ in range(a.eval_iters)) / a.eval_iters
    model.train()
    return out


# ── Train ────────────────────────────────────────────────────────────────────
start = time.time()
init = evaluate()
print(f"params {n_params:,} | vocab {V} | ln(V) {math.log(V):.3f} | starting loss train {init['train']:.3f} val {init['val']:.3f}")
curve = [[0, round(init["train"], 4), round(init["val"], 4)]]
clipped = 0
for step in range(a.iters):
    for g in opt.param_groups:
        g["lr"] = lr_at(step)
    _, loss = model(*get_batch("train"))
    opt.zero_grad(set_to_none=True)
    loss.backward()
    norm = torch.nn.utils.clip_grad_norm_(model.parameters(), a.clip)
    clipped += int(norm > a.clip)
    opt.step()
    if (step + 1) % a.eval_every == 0 or step + 1 == a.iters:
        e = evaluate()
        curve.append([step + 1, round(e["train"], 4), round(e["val"], 4)])
        print(f"step {step + 1:5d} | lr {lr_at(step):.2e} | train {e['train']:.4f} | val {e['val']:.4f}")

seconds = time.time() - start
sample = "".join(itos[i] for i in model.generate(torch.zeros((1, 1), dtype=torch.long), 300)[0].tolist())
print(f"\n{seconds:.0f}s · gradient clipped on {clipped} of {a.iters} steps\n--- sample ---\n{sample}")

if a.results:
    final = curve[-1]
    a.results.write_text(json.dumps({
        "lab": "lab/2026-10-25", "breakage": a.breakage,
        "metrics": {"params": n_params, "vocab": V, "ln_vocab": round(math.log(V), 4),
                    "init_val_loss": round(init["val"], 4), "train_loss": final[1], "val_loss": final[2],
                    "train_val_gap": round(final[2] - final[1], 4), "clipped_steps": clipped, "seconds": round(seconds, 1)},
        "config": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(a).items() if k not in ("results", "data")},
        "curve": curve,
        "sample": sample,
        "environment": {"python": platform.python_version(), "torch": torch.__version__, "platform": platform.platform(),
                        "threads": a.threads, "cpu_count": os.cpu_count()},
    }, indent=1))

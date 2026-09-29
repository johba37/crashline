"""K2 round 2: train the integer student for the whole life of the K1 note.

Product pinned (ki 6000, ac 10000, coupon 25 bps/week, total vol 5500, weekly
observations); the student covers spot 5000-12000, observationsRemaining 1-26,
timeToNextObs 0-604800 and both knockedIn states (tools/domains/k2.json).

Pipeline (docs/model-export-format.md):
  inputs  = pq normalization / qmax (bit-identical to the chain)
  target  = (priceBps - 10000) / 2000
  phase 1 = weighted MSE, Adam, cosine schedule
  phase 2 = tail phase from the best phase-1 checkpoint: weighted mean
            (|e|/50 bps)^p (p > 2) at a low learning rate, which spends
            capacity on the worst points instead of the average
  select  = the checkpoint with the lowest max |error| on V outside the
            exclusion bands (float), evaluated every --eval-every epochs;
            the chosen one is quantized (pq.quantize) and its INTEGER error
            on V is reported. Selection never touches the test set T.
Points inside the exclusion bands are refused on-chain, so they get weight
--band-weight (default 0.05): the student need not bend around the
observation-day jumps it will never be asked about.

Outputs in --out-dir: student.pt, student_export.json (uncertified, format
v2 with the k2 domain attached), report.json (V metrics), train.log is
whatever the caller redirects stdout to.

Example:
  python round2.py --train /opt/ai/cache/sp-student/train_s1.npz --val k2_val_labels.npz \\
      --widths 48,48,48,48 --seed 0 --out-dir runs/w48x4_s0
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np
import torch
import torch.nn as nn

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))

import pricer_quant as pq  # noqa: E402
import round2_sets as sets  # noqa: E402

OFFSET_BPS = 10000
PRICE_SCALE_BPS = 2000.0
QMAX = pq.qmax(16)
LO = np.array([f.lo for f in pq.FIELDS], np.int64)
HI = np.array([f.hi for f in pq.FIELDS], np.int64)


def norm_int(X: np.ndarray) -> np.ndarray:
    """pq.normalize, vectorized (operands non-negative inside the ranges)."""
    X = np.asarray(X, np.int64)
    assert (X >= LO).all() and (X <= HI).all(), "input outside normalization ranges"
    rng = HI - LO
    return ((X - LO) * 2 * QMAX + rng // 2) // rng - QMAX


def norm_float(X: np.ndarray) -> np.ndarray:
    return norm_int(X).astype(np.float64) / QMAX


class MLP(nn.Module):
    def __init__(self, widths: list[int]):
        super().__init__()
        dims = [10] + list(widths)
        mods: list[nn.Module] = []
        for a, b in zip(dims[:-1], dims[1:]):
            mods += [nn.Linear(a, b), nn.ReLU()]
        mods.append(nn.Linear(dims[-1], 1))
        self.net = nn.Sequential(*mods)

    def forward(self, x):
        return self.net(x).squeeze(-1)


def param_count(widths: list[int]) -> int:
    dims = [10] + list(widths) + [1]
    return sum(a * b + b for a, b in zip(dims[:-1], dims[1:]))


def to_numpy_layers(model: MLP):
    lin = [m for m in model.net if isinstance(m, nn.Linear)]
    return ([m.weight.detach().cpu().double().numpy() for m in lin],
            [m.bias.detach().cpu().double().numpy() for m in lin])


# ---------------------------------------------------------------------------
# integer forward, vectorized (bit-exact twin of pq.forward, checked below)
# ---------------------------------------------------------------------------

def _round_shift(p: np.ndarray, s: np.ndarray) -> np.ndarray:
    half = np.left_shift(np.int64(1), s - 1)
    pos = (p + half) >> s
    neg = -((-p + half) >> s)
    return np.where(p >= 0, pos, neg)


def forward_int(export: dict, X: np.ndarray) -> np.ndarray:
    """Integer student on many rows, without the domain check (callers mask).
    Bit-exact with pq.forward (asserted on samples by check_forward_int)."""
    q = pq.qmax(export["quantization"]["activationBits"])
    wdt = {8: "i1", 16: "<i2"}[export["quantization"]["weightBits"]]
    x = norm_int(X)  # (N, 10) int64
    arch = export["architecture"]["layers"]
    for spec, layer in zip(arch[:-1], export["layers"][:-1]):
        W = np.frombuffer(bytes.fromhex(layer["weightsHex"][2:]), dtype=wdt).reshape(spec["out"], spec["in"]).astype(np.int64)
        acc = x @ W.T + np.array(layer["bias"], np.int64)
        acc = np.maximum(acc, 0)
        M = np.array(layer["requantMultiplierQ16"], np.int64)
        S = 16 + np.array(layer["requantShift"], np.int64)
        x = np.clip(_round_shift(acc * M, S), -q - 1, q)
    spec, head = arch[-1], export["layers"][-1]
    W = np.frombuffer(bytes.fromhex(head["weightsHex"][2:]), dtype=wdt).reshape(1, spec["in"]).astype(np.int64)
    acc = (x @ W.T)[:, 0] + head["bias"][0]
    o = export["output"]
    p = _round_shift(acc * o["multiplierQ16"], np.int64(16 + o["shift"])) + o["offsetBps"]
    return np.clip(p, 0, pq.PRICE_MAX_BPS)


def check_forward_int(export: dict, X: np.ndarray, n: int = 500, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), min(n, len(X)), replace=False)
    vec = forward_int(export, X[idx])
    dom = export["certifiedDomain"]
    for i, v in zip(idx, vec):
        row = [int(a) for a in X[i]]
        try:
            pq.check_domain(dom, row)
        except pq.Uncertified:
            continue
        assert pq.forward(export, row) == v, f"forward_int != pq.forward at {row}"


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def gate_table(X: np.ndarray, err: np.ndarray, out_band: np.ndarray) -> list[tuple[str, int, float, float, float]]:
    rows = []

    def add(name, m):
        e = np.abs(err[m])
        if e.size:
            rows.append((name, int(e.size), float(e.max()), float(np.percentile(e, 99)), float(e.mean())))

    add("outside exclusions (GATE)", out_band)
    add("inside exclusions (refused on-chain)", ~out_band)
    for name, m in sets.region_masks(X).items():
        add(name, out_band & m)
    return rows


def print_table(rows, file=sys.stdout):
    print(f"{'region (outside exclusions unless stated)':42s} {'n':>7s} {'max':>8s} {'p99':>8s} {'mean':>7s}", file=file)
    for name, n, mx, p99, mean in rows:
        print(f"{name:42s} {n:7d} {mx:8.1f} {p99:8.1f} {mean:7.2f}", file=file)


def worst(X, err, mask, k=5):
    idx = np.flatnonzero(mask)
    order = idx[np.argsort(-np.abs(err[idx]))[:k]]
    return [{"spot": int(X[i, 0]), "tNext": int(X[i, 7]), "obs": int(X[i, 8]), "knockedIn": int(X[i, 9]),
             "err": float(err[i])} for i in order]


# ---------------------------------------------------------------------------
# training
# ---------------------------------------------------------------------------

def load_sets(paths: list[str]):
    Xs, ys, ses = [], [], []
    for p in paths:
        z = np.load(p)
        Xs.append(z["X"].astype(np.int64))
        ys.append(z["y"])
        ses.append(z["se"])
    return np.vstack(Xs), np.concatenate(ys), np.concatenate(ses)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", nargs="+", required=True)
    ap.add_argument("--val", required=True)
    ap.add_argument("--domain", default=str(sets.DOMAIN_PATH))
    ap.add_argument("--widths", default="48,48,48,48")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--epochs", type=int, default=1500, help="phase 1 epochs")
    ap.add_argument("--tail-epochs", type=int, default=500, help="phase 2 epochs")
    ap.add_argument("--tail-p", type=float, default=4.0)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--tail-lr", type=float, default=3e-5)
    ap.add_argument("--clip", type=float, default=1.0, help="gradient norm clip")
    ap.add_argument("--batch", type=int, default=8192)
    ap.add_argument("--band-weight", type=float, default=0.05)
    ap.add_argument("--eval-every", type=int, default=10)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--init", default=None, help="start from this student.pt (fine-tuning)")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    widths = [int(w) for w in args.widths.split(",")]
    domain = json.loads(pathlib.Path(args.domain).read_text())
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    dev = torch.device(args.device)
    t0 = time.time()

    X, y, se = load_sets(args.train)
    band = sets.excluded(X, domain)
    w = np.where(band, args.band_weight, 1.0)
    Xv, yv, sev = load_sets([args.val])
    vout = ~sets.excluded(Xv, domain)
    print(f"train {len(X)} rows ({band.mean():.1%} in bands, label se mean {se.mean():.1f} max {se.max():.1f}); "
          f"val {len(Xv)} rows ({vout.sum()} outside bands, se max {sev.max():.2f}); "
          f"params {param_count(widths)}; device {dev}", flush=True)

    Xt = torch.tensor(norm_float(X), dtype=torch.float32, device=dev)
    yt = torch.tensor((y - OFFSET_BPS) / PRICE_SCALE_BPS, dtype=torch.float32, device=dev)
    wt = torch.tensor(w / w.mean(), dtype=torch.float32, device=dev)
    Xvt = torch.tensor(norm_float(Xv), dtype=torch.float32, device=dev)
    vout_t = torch.tensor(vout, device=dev)
    yv_t = torch.tensor(yv, dtype=torch.float64, device=dev)

    model = MLP(widths).to(dev)
    if args.init:
        ck = torch.load(args.init, map_location=dev)
        model.load_state_dict(ck["state_dict"])
    n = len(Xt)
    steps_per_epoch = (n + args.batch - 1) // args.batch

    def val_max():
        model.eval()
        with torch.no_grad():
            pred = model(Xvt).double() * PRICE_SCALE_BPS + OFFSET_BPS
            e = (pred - yv_t).abs()[vout_t]
        model.train()
        return float(e.max()), float(torch.quantile(e.float(), 0.99)), float(e.mean())

    best = (float("inf"), None, None)
    history = []

    def run_phase(name, epochs, lr, loss_fn):
        nonlocal best
        if epochs <= 0:
            return
        opt = torch.optim.Adam(model.parameters(), lr=lr)
        if name == "mse":
            sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=epochs * steps_per_epoch,
                                                        pct_start=0.05, div_factor=10.0, final_div_factor=100.0)
        else:  # fine-tuning from the best checkpoint: no warm-up, cosine decay
            sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs * steps_per_epoch, eta_min=lr / 20)
        for ep in range(1, epochs + 1):
            perm = torch.randperm(n, device=dev)
            tot = torch.zeros((), device=dev)
            for lo in range(0, n, args.batch):
                i = perm[lo:lo + args.batch]
                e = model(Xt[i]) - yt[i]
                loss = loss_fn(e, wt[i])
                opt.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip)
                opt.step()
                sched.step()
                tot += loss.detach() * len(i)
            if ep % args.eval_every == 0 or ep == epochs:
                mx, p99, mean = val_max()
                history.append((name, ep, mx, p99, mean))
                if mx < best[0]:
                    best = (mx, {k: v.detach().clone() for k, v in model.state_dict().items()}, (name, ep))
                if ep % (args.eval_every * 10) == 0 or ep == epochs:
                    print(f"{name} ep {ep:5d} loss {float(tot) / n:.3e}  V out-band max {mx:7.1f} p99 {p99:6.1f} "
                          f"mean {mean:5.2f}  best {best[0]:7.1f}@{best[2]}  ({time.time() - t0:.0f}s)", flush=True)

    run_phase("mse", args.epochs, args.lr, lambda e, w: (w * e * e).mean())
    if best[1] is not None:
        model.load_state_dict(best[1])  # the tail phase refines the best MSE checkpoint
    p = args.tail_p
    # (|e| / 50 bps)^p: O(1) at the gate, so the batch loss is dominated by the worst points
    run_phase("tail", args.tail_epochs, args.tail_lr,
              lambda e, w: (w * (e.abs() * (PRICE_SCALE_BPS / 50.0)) ** p).mean())

    model.load_state_dict(best[1])
    model.cpu().eval()
    torch.save({"state_dict": best[1], "widths": widths, "offset_bps": OFFSET_BPS,
                "price_scale_bps": PRICE_SCALE_BPS, "selected": best[2], "args": vars(args)}, out / "student.pt")

    Wn, bn = to_numpy_layers(model)
    calib = norm_float(X[~band])
    export = pq.quantize(Wn, bn, calib, PRICE_SCALE_BPS, offset_bps=OFFSET_BPS, domain=domain)
    (out / "student_export.json").write_text(json.dumps(export, indent=1) + "\n")
    check_forward_int(export, Xv)

    with torch.no_grad():
        pf = model(torch.tensor(norm_float(Xv), dtype=torch.float32)).double().numpy() * PRICE_SCALE_BPS + OFFSET_BPS
    pi = forward_int(export, Xv).astype(np.float64)
    err_i, err_f = pi - yv, pf - yv
    print(f"\nselected {best[2]} (V float out-band max {best[0]:.1f}); quantization |int - float| "
          f"max {np.abs(pi - pf)[vout].max():.2f} bps", flush=True)
    print("== V, INTEGER student vs teacher ==")
    tab = gate_table(Xv, err_i, vout)
    print_table(tab)
    wst = worst(Xv, err_i, vout, 10)
    for r in wst:
        print("  worst", r)
    report = {"widths": widths, "params": param_count(widths), "selected": best[2],
              "val_float_max": float(np.abs(err_f[vout]).max()), "val_int_table": tab, "val_worst": wst,
              "history": history, "args": vars(args), "seconds": round(time.time() - t0)}
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(f"done in {time.time() - t0:.0f}s -> {out}", flush=True)


if __name__ == "__main__":
    main()

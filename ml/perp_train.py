"""P1: train the integer student of the perpetual note (docs/p1-perp-student.md).

The student learns what the closed form gets wrong: target = teacher principal
- formula principal, in bps (ml/perp_sets.py labels both). On chain

    principalBps = max(0, round(formula * 1e4) + correctionBps)

so every error here is |round(formula) + student - teacher|.

Pipeline, K2/K3's trainer (ml/round2.py) with five inputs and a signed head:
  inputs  = pq normalization / qmax (bit-identical to the chain)
  target  = (teacher - formula) / 100 bps
  loss    = weighted MSE, Adam one-cycle, EMA of the weights
  select  = the checkpoint with the lowest max error on V outside the
            exclusion bands (float), then quantized (pq.quantize, spec 2) and
            its INTEGER error on V reported. Selection never touches T.
Rows inside the bands are refused on chain; they get weight --band-weight.

  python ml/perp_train.py --train /opt/ai/cache/sp-perp/train_s1.npz --val /opt/ai/cache/sp-perp/p1_val_labels.npz \\
      --band 86400 --widths 64,48,40,40 --seed 0 --out-dir /opt/ai/cache/sp-perp/runs/a
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
# ml/ first: tools/ has a perp_formula.py of its own (the integer twin)
sys.path.insert(0, str(HERE.parent / "tools"))
sys.path.insert(0, str(HERE))

import pricer_quant as pq  # noqa: E402
import perp_sets as sets  # noqa: E402

sets.ensure_fields(pq)
OFFSET_BPS = 0
SCALE_BPS = 100.0
QMAX = pq.qmax(16)
LO = np.array([f[1] for f in sets.FIELDS], np.int64)
HI = np.array([f[2] for f in sets.FIELDS], np.int64)
N_IN = len(sets.FIELDS)
OUT_LO, OUT_HI = pq.OUTPUT_RANGE[2]


def product() -> dict:
    """The export's `product` section (docs/v2-spec.md section 5): integers only."""
    c = json.loads((HERE / "teacher_perp_config.json").read_text())

    def scaled(x: float, e: int) -> int:
        v = round(x * 10**e)
        assert abs(v / 10**e - x) < 1e-15 * max(1.0, abs(x)), f"{x} is not exact at 1e-{e}"
        return v

    return {
        "kiBarrierBps": 6000,
        "meltShareWad": 18_995_352_771_274_247,
        "fixingIntervalSecs": c["weekSecs"],
        "driftBps": scaled(c["rFree"], 4),
        "discountBps": scaled(c["rDiscount"], 4),
        "teacher": {
            "lambdaYearE6": scaled(c["jumps"]["lambdaYear"], 6),
            "muJE12": scaled(c["jumps"]["muJ"], 12),
            "sigmaJE9": scaled(c["jumps"]["sigmaJ"], 9),
            "sigmaEE9": scaled(c["earnings"]["sigmaE"], 9),
            "volRefE6": scaled(c["jumps"]["volRef"], 6),
            "earningsCycleSecs": c["earnings"]["cycleSecs"],
        },
    }


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
        dims = [N_IN] + list(widths)
        mods: list[nn.Module] = []
        for a, b in zip(dims[:-1], dims[1:]):
            mods += [nn.Linear(a, b), nn.ReLU()]
        mods.append(nn.Linear(dims[-1], 1))
        self.net = nn.Sequential(*mods)

    def forward(self, x):
        return self.net(x).squeeze(-1)


def param_count(widths: list[int]) -> int:
    dims = [N_IN] + list(widths) + [1]
    return sum(a * b + b for a, b in zip(dims[:-1], dims[1:]))


def to_numpy_layers(model: MLP):
    lin = [m for m in model.net if isinstance(m, nn.Linear)]
    return ([m.weight.detach().cpu().double().numpy() for m in lin],
            [m.bias.detach().cpu().double().numpy() for m in lin])


def _round_shift(p: np.ndarray, s: np.ndarray) -> np.ndarray:
    half = np.left_shift(np.int64(1), s - 1)
    return np.where(p >= 0, (p + half) >> s, -((-p + half) >> s))


def forward_int(export: dict, X: np.ndarray) -> np.ndarray:
    """Integer student on many rows, without the domain check (callers mask).
    Bit-exact with pq.forward (asserted on samples by check_forward_int)."""
    q = pq.qmax(export["quantization"]["activationBits"])
    wdt = {8: "i1", 16: "<i2"}[export["quantization"]["weightBits"]]
    x = norm_int(X)
    arch = export["architecture"]["layers"]
    for spec, layer in zip(arch[:-1], export["layers"][:-1]):
        W = np.frombuffer(bytes.fromhex(layer["weightsHex"][2:]), dtype=wdt).reshape(spec["out"], spec["in"]).astype(np.int64)
        acc = np.maximum(x @ W.T + np.array(layer["bias"], np.int64), 0)
        M = np.array(layer["requantMultiplierQ16"], np.int64)
        S = 16 + np.array(layer["requantShift"], np.int64)
        x = np.clip(_round_shift(acc * M, S), -q - 1, q)
    spec, head = arch[-1], export["layers"][-1]
    W = np.frombuffer(bytes.fromhex(head["weightsHex"][2:]), dtype=wdt).reshape(1, spec["in"]).astype(np.int64)
    acc = (x @ W.T)[:, 0] + head["bias"][0]
    o = export["output"]
    p = _round_shift(acc * o["multiplierQ16"], np.int64(16 + o["shift"])) + o["offsetBps"]
    return np.clip(p, OUT_LO, OUT_HI)


def check_forward_int(export: dict, X: np.ndarray, n: int = 500, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), min(n, len(X)), replace=False)
    vec = forward_int(export, X[idx])
    for i, v in zip(idx, vec):
        row = [int(a) for a in X[i]]
        try:
            assert pq.forward(export, row) == v, f"forward_int != pq.forward at {row}"
        except pq.Uncertified:
            continue


def onchain(fml: np.ndarray, corr: np.ndarray) -> np.ndarray:
    """The on-chain principal in bps from the formula (float bps) and a correction."""
    return np.maximum(np.rint(fml) + corr, 0.0)


def gate_table(X: np.ndarray, err: np.ndarray, out_band: np.ndarray):
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
    return [{"spot": int(X[i, 0]), "vol": int(X[i, 1]), "tau": int(X[i, 2]), "n": int(X[i, 3]),
             "knockedIn": int(X[i, 4]), "err": float(err[i])} for i in order]


def load_sets(paths: list[str]):
    Xs, ys, fs = zip(*(sets.load_labels(p) for p in paths))
    return np.vstack(Xs), np.concatenate(ys), np.concatenate(fs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", nargs="+", required=True)
    ap.add_argument("--val", required=True)
    ap.add_argument("--band", type=int, default=86_400, help="length of the two fixing bands, seconds")
    ap.add_argument("--widths", default="64,48,40,40")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--epochs", type=int, default=1500)
    ap.add_argument("--tail-epochs", type=int, default=0)
    ap.add_argument("--tail-p", type=float, default=4.0)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--tail-lr", type=float, default=3e-5)
    ap.add_argument("--clip", type=float, default=1.0)
    ap.add_argument("--ema", type=float, default=0.999)
    ap.add_argument("--batch", type=int, default=8192)
    ap.add_argument("--band-weight", type=float, default=0.05)
    ap.add_argument("--eval-every", type=int, default=10)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--gpu-mem-mb", type=int, default=400,
                    help="hard cap on this process's CUDA allocations (a shared GPU: fail here, not there)")
    ap.add_argument("--init", default=None)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    print("# python ml/perp_train.py " + " ".join(sys.argv[1:]), flush=True)
    if args.threads:
        torch.set_num_threads(args.threads)
    widths = [int(w) for w in args.widths.split(",")]
    domain = sets.domain(args.band)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    dev = torch.device(args.device)
    if dev.type == "cuda":
        index = dev.index if dev.index is not None else torch.cuda.current_device()
        total = torch.cuda.get_device_properties(index).total_memory
        torch.cuda.set_per_process_memory_fraction(args.gpu_mem_mb * 2**20 / total, index)
    t0 = time.time()

    X, y, f = load_sets(args.train)
    band = sets.excluded(X, domain)
    w = np.where(band, args.band_weight, 1.0)
    Xv, yv, fv = load_sets([args.val])
    vout = ~sets.excluded(Xv, domain)
    print(f"train {len(X)} rows ({band.mean():.1%} in bands; target min {np.min(y - f):.0f} max {np.max(y - f):.0f} bps); "
          f"val {len(Xv)} rows ({vout.sum()} outside bands); band {args.band} s; params {param_count(widths)}; "
          f"device {dev}", flush=True)

    Xt = torch.tensor(norm_float(X), dtype=torch.float32, device=dev)
    yt = torch.tensor((y - f - OFFSET_BPS) / SCALE_BPS, dtype=torch.float32, device=dev)
    wt = torch.tensor(w / w.mean(), dtype=torch.float32, device=dev)
    Xvt = torch.tensor(norm_float(Xv), dtype=torch.float32, device=dev)
    vout_t = torch.tensor(vout, device=dev)
    base_v = torch.tensor(np.rint(fv), dtype=torch.float64, device=dev)
    yv_t = torch.tensor(yv, dtype=torch.float64, device=dev)

    model = MLP(widths).to(dev)
    if args.init:
        model.load_state_dict(torch.load(args.init, map_location=dev)["state_dict"])
    n = len(Xt)
    steps_per_epoch = (n + args.batch - 1) // args.batch
    ema = {k: v.detach().clone() for k, v in model.state_dict().items()} if args.ema > 0 else None

    def weights():
        return ema if ema is not None else model.state_dict()

    def val_max():
        model.eval()
        with torch.no_grad():
            corr = torch.func.functional_call(model, weights(), (Xvt,)).double() * SCALE_BPS + OFFSET_BPS
            e = (torch.clamp(base_v + corr, min=0.0) - yv_t).abs()[vout_t]
        model.train()
        return float(e.max()), float(torch.quantile(e.float()[:: max(1, len(e) // 200_000)], 0.99)), float(e.mean())

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
        else:
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
                if ema is not None:
                    with torch.no_grad():
                        for k, v in model.state_dict().items():
                            ema[k].lerp_(v, 1.0 - args.ema)
                tot += loss.detach() * len(i)
            if ep % args.eval_every == 0 or ep == epochs:
                mx, p99, mean = val_max()
                history.append((name, ep, mx, p99, mean))
                if mx < best[0]:
                    best = (mx, {k: v.detach().clone() for k, v in weights().items()}, (name, ep))
                if ep % (args.eval_every * 10) == 0 or ep == epochs:
                    print(f"{name} ep {ep:5d} loss {float(tot) / n:.3e}  V out-band max {mx:7.1f} p99 {p99:6.1f} "
                          f"mean {mean:5.2f}  best {best[0]:7.1f}@{best[2]}  ({time.time() - t0:.0f}s)", flush=True)

    run_phase("mse", args.epochs, args.lr, lambda e, w: (w * e * e).mean())
    if best[1] is not None:
        model.load_state_dict(best[1])
        if ema is not None:
            ema = {k: v.clone() for k, v in best[1].items()}
    p = args.tail_p
    run_phase("tail", args.tail_epochs, args.tail_lr,
              lambda e, w: (w * (e.abs() * (SCALE_BPS / 50.0)) ** p).mean())

    model.load_state_dict(best[1])
    model.cpu().eval()
    torch.save({"state_dict": best[1], "widths": widths, "offset_bps": OFFSET_BPS, "scale_bps": SCALE_BPS,
                "selected": best[2], "args": vars(args)}, out / "student.pt")

    Wn, bn = to_numpy_layers(model)
    calib = norm_float(X[~band])
    export = pq.quantize(Wn, bn, calib, SCALE_BPS, offset_bps=OFFSET_BPS, domain=domain, spec=2, product=product())
    (out / "student_export.json").write_text(json.dumps(export, indent=1) + "\n")
    check_forward_int(export, Xv)

    with torch.no_grad():
        cf = model(torch.tensor(norm_float(Xv), dtype=torch.float32)).double().numpy() * SCALE_BPS + OFFSET_BPS
    ci = forward_int(export, Xv).astype(np.float64)
    err_i, err_f = onchain(fv, ci) - yv, onchain(fv, cf) - yv
    err_0 = onchain(fv, 0.0) - yv
    print(f"\nselected {best[2]} (V float out-band max {best[0]:.1f}); quantization |int - float| "
          f"max {np.abs(ci - cf)[vout].max():.2f} bps", flush=True)
    print("== V, formula + INTEGER student vs teacher ==")
    tab = gate_table(Xv, err_i, vout)
    print_table(tab)
    for r in worst(Xv, err_i, vout, 10):
        print("  worst", r)
    print("== V, formula alone vs teacher ==")
    tab0 = gate_table(Xv, err_0, vout)
    print_table(tab0[:2])
    for alt in sets.BAND_CANDIDATES:
        if alt != args.band:
            m = ~sets.excluded(Xv, sets.domain(alt))
            e = np.abs(err_i[m])
            print(f"== V with {alt} s bands instead: n {m.sum()} max {e.max():.1f} p99 {np.percentile(e, 99):.1f} "
                  f"mean {e.mean():.2f} ==")
    report = {"widths": widths, "params": param_count(widths), "selected": best[2], "band": args.band,
              "val_float_max": float(np.abs(err_f[vout]).max()), "val_int_table": tab, "val_formula_table": tab0[:2],
              "val_worst": worst(Xv, err_i, vout, 10), "history": history, "args": vars(args),
              "seconds": round(time.time() - t0)}
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(f"done in {time.time() - t0:.0f}s -> {out}", flush=True)


if __name__ == "__main__":
    main()

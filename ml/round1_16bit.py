"""Round 1 — retrain the fixed-terms student on the normative 16-bit export
format (docs/model-export-format.md v1, tools/pricer_quant.py) and evaluate the
INTEGER student against the cached K1 teacher labels.

Changes vs round 0 (ml/train_student.py):
  - Inputs: pq.normalized_float_inputs(raws, bits=16) — the integer
    normalization / qmax(15-bit) — instead of our own min-max scaling. Chain
    and training see identical inputs (no train/serve skew).
  - Target: (priceBps - offsetBps) / priceScaleBps with offsetBps = 10000 and
    priceScaleBps = 2000. Mapping to round-0 standardization: round 0 used
    (y - 8494.6) / 1981.8; this is the same affine family with the offset
    moved to the format-mandated 10000 and a round scale of 2000 bps
    (measured label std was 1981.8 bps). The scale is folded into the head
    requant multiplier by pq.quantize, so its exact value is not normative.
  - Output artifact: ml/student_export.json (exportFormatVersion 1) +
    integer-student fidelity metrics vs the cached teacher grid.

Environment note: pycryptodome is not installed here, so weightsHash is a
sha3_256 PLACEHOLDER (not keccak). The hash does not enter the fidelity
measurement; the canonical keccak hash is recomputed by the Rust build script
in the contracts lane anyway.

Reuses: ml/data_fixed.npz (200k cached teacher labels, fixed terms) and
ml/k1_eval_cache.npz (11,208 eval labels, 2^18 paths). No teacher re-run.
"""

import hashlib
import json
import sys
import time
import types

import numpy as np
import torch

# pycryptodome is not installed here; stub Crypto.Hash.keccak with sha3_256 so
# pricer_quant imports. weightsHash in student_export.json is therefore a
# PLACEHOLDER (sha3_256, not keccak256) — it does not enter the fidelity
# measurement; the normative keccak hash is recomputed by the Rust build
# script in the contracts lane.
class _FakeKeccak:
    @staticmethod
    def new(digest_bits=256, data=b""):
        h = hashlib.sha3_256()
        h.update(data)
        return types.SimpleNamespace(hexdigest=h.hexdigest, update=h.update)

_crypto = types.ModuleType("Crypto")
_crypto_hash = types.ModuleType("Crypto.Hash")
_crypto_hash.keccak = _FakeKeccak
_crypto.Hash = _crypto_hash
sys.modules.setdefault("Crypto", _crypto)
sys.modules.setdefault("Crypto.Hash", _crypto_hash)
sys.modules.setdefault("Crypto.Hash.keccak", _FakeKeccak)

sys.path.insert(0, "/home/johba/arb-hackathon/projects/surrogate-pricer/tools")
import pricer_quant as pq

import teacher
from train_student import Student

OFFSET_BPS = 10000
PRICE_SCALE_BPS = 2000.0
WIDTH = 24
MODEL_OUT = "student_k1_r1.pt"
EXPORT_OUT = "student_export.json"

TNEXTS = (0, 3600, 86400, 302400)


def train(seed=0):
    z = np.load("data_fixed.npz")
    X, y = z["X"], z["y"]
    raws = [list(r) for r in np.rint(X).astype(np.int64)]  # chain sees ints
    t0 = time.time()
    Xn = torch.from_numpy(pq.normalized_float_inputs(raws, bits=16).astype(np.float32))
    print(f"pq inputs: {Xn.shape} ({time.time()-t0:.1f}s); "
          f"range [{Xn.min():.4f}, {Xn.max():.4f}]", flush=True)

    torch.manual_seed(seed)
    yt = torch.from_numpy(((y - OFFSET_BPS) / PRICE_SCALE_BPS).astype(np.float32))
    perm = torch.randperm(len(Xn))
    vidx, tidx = perm[:10_000], perm[10_000:]
    Xv, yv = Xn[vidx], yt[vidx]
    Xt, yt_ = Xn[tidx], yt[tidx]

    model = Student(width=WIDTH)
    print(f"param count: {sum(p.numel() for p in model.parameters())}", flush=True)
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=250)
    loss_fn = torch.nn.MSELoss()
    best_val, best_state, bad = float("inf"), None, 0
    for epoch in range(1, 601):
        model.train()
        p = torch.randperm(len(Xt))
        for lo in range(0, len(Xt), 4096):
            i = p[lo:lo + 4096]
            opt.zero_grad()
            loss_fn(model(Xt[i]), yt_[i]).backward()
            opt.step()
        sched.step()
        model.eval()
        with torch.no_grad():
            val = loss_fn(model(Xv), yv).item()
        if val < best_val - 1e-8:
            best_val, best_state, bad = val, {k: v.clone() for k, v in
                                              model.state_dict().items()}, 0
        else:
            bad += 1
        if epoch % 50 == 0 or epoch == 1:
            print(f"epoch {epoch:4d}  val RMSE {val**0.5 * PRICE_SCALE_BPS:8.2f} bps",
                  flush=True)
        if bad >= 60:
            print(f"early stop at epoch {epoch}", flush=True)
            break
    model.load_state_dict(best_state)
    torch.save({"state_dict": best_state, "width": WIDTH,
                    "offset_bps": OFFSET_BPS, "price_scale_bps": PRICE_SCALE_BPS,
                    "norm": "pq.normalized_float_inputs(bits=16)",
                    "best_val_rmse_bps": float(best_val**0.5 * PRICE_SCALE_BPS)}, MODEL_OUT)
    print(f"best val RMSE {best_val**0.5 * PRICE_SCALE_BPS:.2f} bps "
          f"({time.time()-t0:.1f}s total)", flush=True)
    return model, Xn


def to_numpy_layers(model):
    lin = [m for m in model.net if isinstance(m, torch.nn.Linear)]
    weights = [m.weight.detach().numpy().astype(np.float64) for m in lin]
    biases = [m.bias.detach().numpy().astype(np.float64) for m in lin]
    return weights, biases


def build_grid_raws():
    spots = np.arange(5000, 12001, 5, dtype=np.int64)
    raws, meta = [], []
    for flag in (0, 1):
        for tn in TNEXTS:
            for s in spots:
                raws.append([int(s), int(s) - 6000, 5500, 6000, 10000, 25,
                             26 * teacher.WEEK_SECS + tn, tn, 26, flag])
                meta.append((int(s), tn, flag))
    return raws, np.array(meta)


def stats(err, mask, name):
    e = np.abs(err[mask])
    return (f"{name:38s} n={mask.sum():5d}  max {e.max():8.2f}  "
            f"p99 {np.percentile(e, 99):8.2f}  mean {e.mean():7.2f}")


def main():
    global MODEL_OUT, EXPORT_OUT
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--model-out", default=MODEL_OUT)
    ap.add_argument("--export-out", default=EXPORT_OUT)
    args = ap.parse_args()
    MODEL_OUT, EXPORT_OUT = args.model_out, args.export_out
    model, Xn = train(seed=args.seed)

    weights, biases = to_numpy_layers(model)
    calib_idx = torch.randperm(len(Xn))[:50_000]
    calib_x = Xn[calib_idx].numpy().astype(np.float64)
    export = pq.quantize(weights, biases, calib_x, PRICE_SCALE_BPS, offset_bps=OFFSET_BPS)
    with open(EXPORT_OUT, "w") as f:
        json.dump(export, f, indent=1)
    print(f"export written: {EXPORT_OUT} (weightsHash placeholder "
          f"{export['weightsHash'][:18]}…, parameterCount "
          f"{export['architecture']['parameterCount']})", flush=True)

    # Eval: cached teacher labels, float student, integer student.
    z = np.load("k1_eval_cache.npz")
    y = z["y"]
    raws, meta = build_grid_raws()
    spot, tnext, flag = meta[:, 0], meta[:, 1], meta[:, 2]

    t0 = time.time()
    Xg = torch.from_numpy(pq.normalized_float_inputs(raws, bits=16).astype(np.float32))
    with torch.no_grad():
        pred_float = model(Xg).numpy().astype(np.float64) * PRICE_SCALE_BPS + OFFSET_BPS
    pred_int = np.array([pq.forward(export, r) for r in raws], dtype=np.float64)
    print(f"grid forward passes done ({time.time()-t0:.1f}s)", flush=True)

    near_ac = (spot >= 9500) & (spot <= 10500)
    near_ki = (spot >= 5500) & (spot <= 6500)
    elsewhere = ~(near_ac | near_ki)
    at_obs = tnext == 0
    out_band = (tnext > 86400) | (np.abs(spot - 10000) > 500)

    err_f = pred_float - y
    err_i = pred_int - y
    err_q = pred_int - pred_float

    print("\n== quantization-only: |integer - float| (bps) ==")
    print(stats(err_q, np.ones_like(at_obs, bool), "overall"))
    print(stats(err_q, out_band, "outside AC eps-band"))

    print("\n== float student (pq-norm inputs) vs teacher ==")
    for name, m in [("overall", np.ones_like(at_obs, bool)), ("near AC", near_ac),
                    ("near KI", near_ki), ("elsewhere", elsewhere),
                    ("at-obs", at_obs), ("away", ~at_obs),
                    ("outside AC eps-band", out_band)]:
        print(stats(err_f, m, name))

    print("\n== INTEGER student (pq.forward, the on-chain number) vs teacher ==")
    for name, m in [("overall", np.ones_like(at_obs, bool)), ("near AC", near_ac),
                    ("near KI", near_ki), ("elsewhere", elsewhere),
                    ("at-obs", at_obs), ("away", ~at_obs),
                    ("outside AC eps-band", out_band)]:
        print(stats(err_i, m, name))

    i = int(np.argmax(np.abs(err_i)))
    print(f"\nworst integer point: spot {spot[i]} tNext {tnext[i]} knockedIn {flag[i]}  "
          f"teacher {y[i]:.2f}  int {pred_int[i]:.0f}  d {err_i[i]:+.2f}")


if __name__ == "__main__":
    main()

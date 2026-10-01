"""One teacher run for /verify-quote, in a subprocess.

ml/teacher.py imports torch (and scipy), which the service's venv doesn't
have, so the service runs this file with TEACHER_PYTHON (default: the CUDA
torch venv /opt/ai/cache/venv-cuda/bin/python), numpy or torch alike.

stdin:  {"features": {spot, dist, vol, ki, ac, coupon, ttm, tNext, obs, knockedIn},
         "paths": int, "seed": int, "device": "cuda" | "cpu", "teacher": "v2" | "v3" | "gbm"}
stdout: {"priceBps", "stdErrBps", "config", "teacher", "backend", "device", "secs"}

`teacher` picks the config the model was distilled from (api_model.TEACHER_OF_MODEL):
v2 = the pinned jump teacher (ml/teacher_config.json, model/k2), v3 = vol-scaled
jumps and rDiscount 0 (ml/teacher_config_v3.json, model/k3), gbm = K1's GBM
teacher (lambda = 0, model/k1-r1). Missing: v2, as before.
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ml"))

import numpy as np  # noqa: E402

import teacher as T  # noqa: E402

INT_KEYS = ("obs", "knockedIn")


def teacher_config(name: str) -> T.TeacherConfig:
    if name == "v2":
        return T.load_config()
    if name == "v3":
        return T.load_config(T.CONFIG_V3_PATH)
    if name == "gbm":
        return T.GBM
    raise SystemExit(f"unknown teacher {name!r} (v2, v3, gbm)")


def main() -> None:
    req = json.load(sys.stdin)
    F = {k: np.array([v], dtype=np.int64 if k in INT_KEYS else np.float64) for k, v in req["features"].items()}
    name = req.get("teacher", "v2")
    cfg = teacher_config(name)
    t0 = time.perf_counter()
    if req["device"] == "cuda":
        import teacher_torch as TT
        p, se = TT.price_batch(F, total_paths=req["paths"], seed=req["seed"], cfg=cfg, device="cuda")
        backend = "torch"
    else:
        p, se = T.price_batch(F, total_paths=req["paths"], seed=req["seed"], cfg=cfg)
        backend = "numpy"
    print(json.dumps({"priceBps": float(p[0]), "stdErrBps": float(se[0]), "config": cfg.name, "teacher": name,
                      "backend": backend, "device": req["device"], "secs": time.perf_counter() - t0}))


if __name__ == "__main__":
    main()

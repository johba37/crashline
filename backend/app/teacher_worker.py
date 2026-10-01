"""One teacher run for /verify-quote, in a subprocess.

ml/teacher.py imports torch (and scipy), which the service's venv doesn't
have, so the service runs this file with TEACHER_PYTHON (default: the CUDA
torch venv /opt/ai/cache/venv-cuda/bin/python), numpy or torch alike.

stdin:  {"features": {spot, dist, vol, ki, ac, coupon, ttm, tNext, obs, knockedIn},
         "paths": int, "seed": int, "device": "cuda" | "cpu"}
stdout: {"priceBps", "stdErrBps", "config", "backend", "device", "secs"}
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


def main() -> None:
    req = json.load(sys.stdin)
    F = {k: np.array([v], dtype=np.int64 if k in INT_KEYS else np.float64) for k, v in req["features"].items()}
    t0 = time.perf_counter()
    if req["device"] == "cuda":
        import teacher_torch as TT
        p, se = TT.price_batch(F, total_paths=req["paths"], seed=req["seed"], device="cuda")
        backend = "torch"
    else:
        p, se = T.price_batch(F, total_paths=req["paths"], seed=req["seed"])
        backend = "numpy"
    print(json.dumps({"priceBps": float(p[0]), "stdErrBps": float(se[0]), "config": T.load_config().name,
                      "backend": backend, "device": req["device"], "secs": time.perf_counter() - t0}))


if __name__ == "__main__":
    main()

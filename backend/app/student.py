"""The off-chain student: tools/pricer_quant.py (the bit-exact twin of the Stylus
contract), imported from the repo, plus `forward_batch`, the same integer
forward pass vectorized with numpy int64 for many rows at once.

int64 is exact here: inputs and activations are 16-bit, weights at most
16-bit and layers at most 64 wide, so an accumulator stays below 2^37 plus a
bias below 2^53 (pricer_quant asserts that), and a requantization product
below 2^53 as well. tests/test_wp3_history.py checks forward_batch against
pricer_quant.forward row by row.
"""

from __future__ import annotations

import sys

import numpy as np

from .config import ROOT

if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import pricer_quant as pq  # noqa: E402

FIELD_NAMES = pq.FIELD_NAMES


def domain_error(export: dict, values: list[int]) -> tuple[str, dict] | None:
    """None if the model accepts `values`, else (error name, args) as the contract reverts."""
    try:
        pq.check_domain(export.get("certifiedDomain") or pq.default_domain(), values)
    except pq.OutOfRange as e:
        return "OutOfRange", {"field": e.index, "value": str(e.value)}
    except pq.Inconsistent as e:
        return "Inconsistent", {"field": e.index}
    except pq.Uncertified as e:
        return "Uncertified", {"region": e.region}
    return None


def forward(export: dict, values: list[int]) -> int:
    """The reference: domain check + integer forward pass (raises like the contract)."""
    return pq.forward(export, values)


def _round_shift(p: np.ndarray, s: np.ndarray | int) -> np.ndarray:
    s = np.asarray(s, dtype=np.int64)
    half = np.left_shift(np.int64(1), s - 1)
    return np.where(p >= 0, (p + half) >> s, -((-p + half) >> s))


def forward_batch(export: dict, rows: list[list[int]]) -> np.ndarray:
    """Clean prices of rows that are inside the domain (check it first: no domain check here)."""
    bits = export["quantization"]["activationBits"]
    wdt = {8: "i1", 16: "<i2"}[export["quantization"]["weightBits"]]
    q = pq.qmax(bits)
    x = np.asarray(rows, dtype=np.int64)
    lo = np.array([f.lo for f in pq.FIELDS], dtype=np.int64)
    rng = np.array([f.hi - f.lo for f in pq.FIELDS], dtype=np.int64)
    x = ((x - lo) * 2 * q + rng // 2) // rng - q
    arch = export["architecture"]["layers"]
    for spec, layer in zip(arch[:-1], export["layers"][:-1]):
        w = np.frombuffer(bytes.fromhex(layer["weightsHex"][2:]), dtype=wdt).reshape(spec["out"], spec["in"])
        acc = x @ w.astype(np.int64).T + np.asarray(layer["bias"], dtype=np.int64)
        acc = np.maximum(acc, 0)
        m = np.asarray(layer["requantMultiplierQ16"], dtype=np.int64)
        sh = 16 + np.asarray(layer["requantShift"], dtype=np.int64)
        x = np.clip(_round_shift(acc * m, sh), -q - 1, q)
    head_spec, head = arch[-1], export["layers"][-1]
    w = np.frombuffer(bytes.fromhex(head["weightsHex"][2:]), dtype=wdt).reshape(head_spec["out"], head_spec["in"])
    acc = (x @ w.astype(np.int64).T)[:, 0] + np.int64(head["bias"][0])
    out = export["output"]
    price = _round_shift(acc * out["multiplierQ16"], 16 + out["shift"]) + out["offsetBps"]
    return np.clip(price, 0, pq.PRICE_MAX_BPS)

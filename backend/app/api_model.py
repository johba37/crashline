"""WP4 routes: /series/{addr}/curve (the off-chain student) and /verify-quote (the teacher).

Curve: the quoter's current inputs for the series (`NoteQuoter.inputs` at the
response's block, the listing's vol), one field varied over the listing
model's certified range, each point priced by tools/pricer_quant.forward
(bit-exact with the Stylus contract) plus the accrued coupon, exactly as
`notePriceBps` adds it. The current value of the varied field is always one
of the points (`current: true`), so that point reproduces /series/{addr}.mid.

Verify: the teacher (ml/teacher.py, Monte Carlo) at the inputs of a Desk
trade (or given inputs), next to the student and the price the trade paid.
The teacher config is the one the model was distilled from (TEACHER_OF_MODEL:
model/k3 -> teacher v3, model/k2 -> the pinned v2, model/k1-r1 -> GBM).
With a vol band the Desk priced the trade at one end of the band
(Desk._sidePrice): the check uses the quote at that end and the teacher at
that vol. Runs in a subprocess (app/teacher_worker.py) with TEACHER_PYTHON;
with TEACHER_DEVICE=cuda the torch backend with 2^18 paths, else numpy on the
CPU with 2^16 paths. One run at a time (429 Busy otherwise), cached by
(inputs, teacher, seed, paths, backend).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import OrderedDict

from fastapi import APIRouter, Body, Query

from .chain import Revert
from .config import ROOT
from .indexer import TRADE_KINDS
from .service import SERVICE, ApiError, addr, model_exports
from .student import FIELD_NAMES, domain_error, forward
from .views import ZERO, Ctx, max_bps

router = APIRouter()

TEACHER_PYTHON = os.environ.get("TEACHER_PYTHON", "/opt/ai/cache/venv-cuda/bin/python")
TEACHER_SEED = int(os.environ.get("TEACHER_SEED", 20260930))
WORKER = ROOT / "backend/app/teacher_worker.py"
FIELD_OF = {"spot": 0, "vol": 2, "weeks": 8}
# model dir -> the teacher it was distilled from (app/teacher_worker.py); anything else: v2
TEACHER_OF_MODEL = {"model/k3": "v3", "model/k2": "v2", "model/k1-r1": "gbm"}
TEACHERS = ("v2", "v3", "gbm")
# the student's certified error vs its teacher, rounded up (the check's allowance on top of 3 stdErr):
# k2 max 18.7 bps outside the bands (docs/k2-round2.md, the 15 bps WP4 used), k3 max 38.0 on T3
# (docs/k3-vol-input.md)
MODEL_ERROR_BPS = {"model/k3": 40}
DEFAULT_MODEL_ERROR_BPS = 15


def teacher_of(model_dir: str | None) -> str:
    return TEACHER_OF_MODEL.get(model_dir or "", "v2")


def _named(values: list[int]) -> dict:
    return dict(zip(FIELD_NAMES, values))


def _values(inputs: dict) -> list[int]:
    return [int(inputs[k]) for k in FIELD_NAMES]


def _listing_model(c: Ctx, series: str, block: int | None = None) -> tuple[dict, str, dict]:
    """(listing, weightsHash, export) of the series' listing at `block` (default: the response's)."""
    b = c.block if block is None else block
    lst = c.desk.call("listing", series, block=b)
    if lst["pricer"] == ZERO:
        raise ApiError(409, "NotListed", {"series": series})
    weights = c.chain.at("pricer", lst["pricer"]).call("weightsHash", block=b)
    return lst, weights, SERVICE.export_for(weights)[1]


def _accrued(r: dict, t: int) -> int:
    return r["coupon"] * (t - r["strike_time"]) // r["interval"]


# --- curve ------------------------------------------------------------------------------------

@router.get("/series/{address}/curve")
def curve(address: str, vs: str = Query("spot", pattern="^(spot|vol|weeks)$"), n: int = Query(41, ge=2, le=401)):
    c = Ctx.now()
    r = c.series_row(addr(address))
    lst, weights, export = _listing_model(c, r["address"])
    try:
        now = c.call(c.quoter, "inputs", r["address"], lst["volBpsAnnual"])
    except Revert as e:  # not live, fixing pending, stale feed: there are no current inputs
        raise ApiError(409, e.name, e.args_json()) from None
    base = [now[k] for k in FIELD_NAMES]
    field = FIELD_OF[vs]
    rng = export["certifiedDomain"]["ranges"][field]
    lo, hi = rng["min"], rng["max"]
    if vs == "weeks":
        hi = min(hi, r["count"])
    xs = sorted({lo + (hi - lo) * k // (n - 1) for k in range(n)} | {base[field]})
    mb = max_bps(r)
    t_now_since_strike = c.time - r["strike_time"]
    points = []
    for x in xs:
        v = list(base)
        v[field] = x
        accrued = _accrued(r, c.time)
        if vs == "spot":
            v[1] = x - v[3]  # distToKnockInBps stays consistent
        elif vs == "weeks":
            v[6] = v[7] + x * r["interval"]  # timeToMaturitySecs
            # the same note at that point of its life: coupon accrued since strike moves with it
            accrued = r["coupon"] * (t_now_since_strike + (base[8] - x) * r["interval"]) // r["interval"]
        err = domain_error(export, v)
        p = {"x": x, "noteBps": None, "coverBps": None, "cleanBps": None, "inDomain": err is None,
             "current": x == base[field]}
        if err:
            p["reason"] = {"error": err[0], "args": err[1]}
        else:
            clean = forward(export, v)
            note = clean + accrued
            p.update(cleanBps=clean, noteBps=note, coverBps=mb - min(note, mb))
        points.append(p)
    return c.out({"series": r["address"], "vs": vs, "field": FIELD_NAMES[field], "inputs": _named(base),
                  "accruedBps": _accrued(r, c.time), "weightsHash": weights, "points": points})


# --- verify-quote ---------------------------------------------------------------------------------

class Teacher:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache: OrderedDict[tuple, dict] = OrderedDict()

    @staticmethod
    def setup() -> tuple[str, int]:
        device = "cuda" if os.environ.get("TEACHER_DEVICE", "").lower() == "cuda" else "cpu"
        paths = int(os.environ.get("TEACHER_PATHS", 2**18 if device == "cuda" else 2**16))
        return device, paths

    def run(self, values: list[int], teacher: str = "v2") -> tuple[dict, bool, float]:
        device, paths = self.setup()
        key = (tuple(values), teacher, TEACHER_SEED, paths, device)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key], True, 0.0
        if not self.lock.acquire(blocking=False):
            raise ApiError(429, "Busy", {"message": "a teacher run is in progress; retry shortly"})
        try:
            t0 = time.monotonic()
            f = dict(zip(("spot", "dist", "vol", "ki", "ac", "coupon", "ttm", "tNext", "obs"), values[:9]))
            f["knockedIn"] = values[9] & 1
            req = {"features": f, "paths": paths, "seed": TEACHER_SEED, "device": device, "teacher": teacher}
            p = subprocess.run([TEACHER_PYTHON, str(WORKER)], input=json.dumps(req), capture_output=True, text=True,
                               timeout=600, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))  # no ml/__pycache__
            if p.returncode != 0:
                raise ApiError(500, "TeacherFailed", {"stderr": p.stderr[-2000:]})
            out = json.loads(p.stdout.strip().splitlines()[-1])
            out.update(paths=paths, seed=TEACHER_SEED)
            if device == "cpu":
                out["note"] = (f"numpy teacher on the CPU with {paths} paths (2^18 on the GPU: set "
                               "TEACHER_DEVICE=cuda)")
            self.cache[key] = out
            if len(self.cache) > 512:
                self.cache.popitem(last=False)
            return out, False, time.monotonic() - t0
        finally:
            self.lock.release()


TEACHER = Teacher()


def _trade_from_tx(c: Ctx, tx_hash: str, series: str | None) -> dict:
    receipt = c.chain.rpc("eth_getTransactionReceipt", [tx_hash])
    if receipt is None:
        raise ApiError(404, "UnknownTx", {"txHash": tx_hash})
    events = {e.topic0: e for e in (c.desk.event(n) for n in TRADE_KINDS)}
    for lg in receipt["logs"]:
        e = events.get(lg["topics"][0]) if lg["address"].lower() == c.desk.address else None
        if e is None:
            continue
        a = e.decode(lg)
        if series and a["series"] != series:
            continue
        return {"kind": TRADE_KINDS[e.name], "series": a["series"], "priceBps": a["priceBps"],
                "weightsHash": a["weightsHash"], "block": int(receipt["blockNumber"], 16), "txHash": tx_hash}
    raise ApiError(404, "NoTradeInTx", {"txHash": tx_hash, "series": series})


def _band_quotes(c: Ctx, series: str, lst: dict, spread: dict, block: int) -> list[dict]:
    """The quoter's NOTE quote and inputs at each vol the Desk asks the model (Desk._sidePrice):
    the listing's vol minus and plus the band, or the vol itself while the band is 0."""
    band = spread["volBandBps"]
    vols = [lst["volBpsAnnual"] - band, lst["volBpsAnnual"] + band] if band else [lst["volBpsAnnual"]]
    out = []
    for v in vols:
        try:
            note = c.quoter.call("notePriceBps", series, lst["pricer"], v, block=block)[0]
            inputs = c.quoter.call("inputs", series, v, block=block)
        except Revert as e:
            raise ApiError(409, e.name, e.args_json()) from None
        out.append({"volBps": v, "noteBps": note, "values": [inputs[k] for k in FIELD_NAMES]})
    return out


def _price_of_trade(kind: str, quotes: list[dict], spread: dict, mb: int) -> tuple[dict, int, int]:
    """(the band end the Desk used, the flat spread applied, the price IDeskCover's formulas give).
    lo / hi are the lower and the higher NOTE quote, capped at maxBps (`mb`):
      NOTE ask = min(hi + askBps, maxBps)   buy;   cover bid = maxBps - NOTE ask   sellCover
      NOTE bid = max(lo - bidBps, 0)        sell;  cover ask = maxBps - NOTE bid   buyCover"""
    lo = min(quotes, key=lambda q: (q["noteBps"], q["volBps"]))
    hi = max(quotes, key=lambda q: (q["noteBps"], -q["volBps"]))
    if kind in ("buy", "sellCover"):
        note = min(min(hi["noteBps"], mb) + spread["askBps"], mb)
        return hi, spread["askBps"], note if kind == "buy" else mb - note
    note = max(min(lo["noteBps"], mb) - spread["bidBps"], 0)
    return lo, spread["bidBps"], note if kind == "sell" else mb - note


@router.post("/verify-quote")
def verify_quote(body: dict = Body(...)):
    c = Ctx.now()
    t0 = time.monotonic()
    on_chain, accrued = None, int(body.get("accruedBps", 0))
    if body.get("txHash"):
        tx = body["txHash"]
        if not isinstance(tx, str) or len(tx) != 66 or not tx.startswith("0x"):
            raise ApiError(400, "BadTxHash", {"txHash": tx})
        tr = _trade_from_tx(c, tx.lower(), addr(body["series"], "series") if body.get("series") else None)
        r = c.series_row(tr["series"])
        b = tr["block"]
        lst = c.desk.call("listing", tr["series"], block=b)
        spread = c.desk.call("spread", tr["series"], block=b)
        quotes = _band_quotes(c, tr["series"], lst, spread, b)
        used, spread_bps, expected = _price_of_trade(tr["kind"], quotes, spread, max_bps(r))
        values = used["values"]  # the model's inputs at the band end the Desk priced at
        accrued = _accrued(r, c.chain.block(b)["time"])
        weights = tr["weightsHash"]
        on_chain = {"priceBps": tr["priceBps"], "weightsHash": weights, "kind": tr["kind"], "series": tr["series"],
                    "txHash": tr["txHash"], "block": b, "spreadBps": spread_bps, "midBps": used["noteBps"],
                    "volBpsAnnual": lst["volBpsAnnual"], "volBandBps": spread["volBandBps"],
                    "quoteVolBps": used["volBps"],
                    "bandQuotes": [{"volBps": q["volBps"], "noteBps": q["noteBps"]} for q in quotes],
                    "expectedPriceBps": expected, "priceMatches": expected == tr["priceBps"]}
    elif isinstance(body.get("inputs"), dict):
        try:
            values = _values(body["inputs"])
        except (KeyError, TypeError, ValueError):
            raise ApiError(400, "BadInputs", {"fields": list(FIELD_NAMES)}) from None
        weights = body.get("weightsHash") or c.chain.at("pricer", c.cfg["addresses"]["surrogatePricer"]).call(
            "weightsHash", block=c.block)
    else:
        raise ApiError(400, "BadRequest", {"expected": "{series, txHash} or {inputs}"})

    model_dir, export = model_exports().get(weights, (None, None))
    teacher_name = body.get("teacher") or teacher_of(model_dir)
    if teacher_name not in TEACHERS:
        raise ApiError(400, "BadTeacher", {"teacher": teacher_name, "expected": list(TEACHERS)})
    student = {"priceBps": None, "quoteBps": None, "weightsHash": weights, "model": model_dir}
    if export is None:
        student["error"] = {"error": "UnknownModel", "args": {"weightsHash": weights}}
    else:
        err = domain_error(export, values)
        if err:
            student["error"] = {"error": err[0], "args": err[1]}
        else:
            student["priceBps"] = forward(export, values)
            student["quoteBps"] = student["priceBps"] + accrued
    try:
        teacher, cached, secs = TEACHER.run(values, teacher_name)
    except subprocess.TimeoutExpired:
        raise ApiError(504, "TeacherTimeout", {}) from None
    teacher = dict(teacher, quoteBps=teacher["priceBps"] + accrued)
    check = None
    if on_chain:
        model_err = MODEL_ERROR_BPS.get(model_dir or "", DEFAULT_MODEL_ERROR_BPS)
        tol = 3 * teacher["stdErrBps"] + model_err
        diff = on_chain["midBps"] - teacher["quoteBps"]
        check = {"onChainMidBps": on_chain["midBps"], "teacherQuoteBps": teacher["quoteBps"], "diffBps": diff,
                 "toleranceBps": tol, "modelErrorBps": model_err, "within": abs(diff) <= tol,
                 "priceMatches": on_chain["priceMatches"]}
    return c.out({"inputs": _named(values), "accruedBps": accrued, "onChain": on_chain, "student": student,
                  "teacher": teacher, "check": check, "cached": cached, "teacherSecs": secs,
                  "secs": time.monotonic() - t0})

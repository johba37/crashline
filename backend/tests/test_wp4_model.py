"""WP4: model routes.

Acceptance: curve?vs=spot at the series' current inputs reproduces
/series/{addr}.mid bit-exactly at the current spot point; /verify-quote on the
e2e buy tx returns a teacher price (the model's own teacher: v3 for model/k3,
v2 for model/k2) within 3 stdErr + the model's error allowance (k3 40, k2 15 bps)
of the on-chain quote at the band end the Desk priced at.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from conftest import Service
from helpers import USDG, buy, default_series, sell, unique
from app.api_model import MODEL_ERROR_BPS, TEACHER_PYTHON
from app.chain import address_of
from app.student import FIELD_NAMES
from devnode import deploy as dp


def same_block(service, *paths):
    """GET several paths at one indexed block (retry if the head moved in between)."""
    for _ in range(10):
        out = [service.get(p) for p in paths]
        if len({o["block"] for o in out}) == 1:
            return out
    raise AssertionError("head kept moving")


def test_curve_spot_reproduces_mid(service, chain, cfg):
    s0 = default_series(chain, cfg)
    service.synced(chain)
    o, cv = same_block(service, f"/series/{s0}", f"/series/{s0}/curve?vs=spot")
    cur = [p for p in cv["points"] if p["current"]]
    assert len(cur) == 1 and cur[0]["x"] == cv["inputs"]["spotBpsOfInitial"]
    assert cur[0]["noteBps"] == o["mid"]["noteBps"] and cur[0]["coverBps"] == o["mid"]["coverBps"]
    assert cv["inputs"]["spotBpsOfInitial"] == 8500 or cv["inputs"]["spotBpsOfInitial"] > 0
    xs = [p["x"] for p in cv["points"]]
    assert xs == sorted(xs) and xs[0] == 5000 and xs[-1] == 12000 and len(xs) in (41, 42)
    assert all(p["inDomain"] for p in cv["points"])  # knocked in, 3 days out: no excluded band
    # the curve is the contract's answer: the Stylus pricer at a few points, at the same block
    pricer = chain.at("pricer", cfg["addresses"]["surrogatePricer"])
    for p in cv["points"][::10]:
        v = dict(cv["inputs"], spotBpsOfInitial=p["x"], distToKnockInBps=p["x"] - 6000)
        assert pricer.call("priceBps", v, block=cv["block"]) == p["cleanBps"]
        assert p["noteBps"] == p["cleanBps"] + cv["accruedBps"]
    # the quoter's own inputs
    q = chain.at("quoter", cfg["addresses"]["noteQuoter"]).call("inputs", s0, 5500, block=cv["block"])
    assert cv["inputs"] == {k: q[k] for k in FIELD_NAMES}


def test_curve_vol_and_weeks(service, chain, cfg):
    s0 = default_series(chain, cfg)
    service.synced(chain)
    o, vol, weeks = same_block(service, f"/series/{s0}", f"/series/{s0}/curve?vs=vol",
                               f"/series/{s0}/curve?vs=weeks&n=26")
    lo, hi = chain.at("pricer", cfg["addresses"]["surrogatePricer"]).call("certifiedRange", 2)
    xs = [p["x"] for p in vol["points"]]
    assert xs[0] == lo and xs[-1] == hi and 5500 in xs  # K2 is vol-pinned: [5500]; K3: 2000..9000
    cur = next(p for p in vol["points"] if p["current"])
    assert cur["x"] == 5500 and cur["noteBps"] == o["mid"]["noteBps"]
    xs = [p["x"] for p in weeks["points"]]
    assert xs == list(range(1, 27))
    cur = next(p for p in weeks["points"] if p["current"])
    assert cur["x"] == 16 and cur["noteBps"] == o["mid"]["noteBps"]
    pricer = chain.at("pricer", cfg["addresses"]["surrogatePricer"])
    p = weeks["points"][4]  # 5 observations left: timeToMaturity follows
    v = dict(weeks["inputs"], observationsRemaining=5,
             timeToMaturitySecs=weeks["inputs"]["timeToNextObsSecs"] + 5 * 604800)
    assert pricer.call("priceBps", v, block=weeks["block"]) == p["cleanBps"]


def _staged(chain, cfg, **kw):
    addrs = json.loads(json.dumps(cfg["addresses"]))
    args = dict(feed_name=unique("M"), path_bps=dp.PATH_BPS, spot_bps=8500, observations_done=10,
                lead_secs=2 * 86400, listing={})
    args.update(kw)
    return dp.stage(chain, dp.DEV_KEY, addrs, **args)


def test_curve_marks_the_excluded_bands(service, chain, cfg):
    path = [9400, 8800, 9000, 9100, 8700]  # no knock-in
    st = _staged(chain, cfg, path_bps=path, observations_done=5, spot_bps=8000, lead_secs=3 * 3600)
    service.synced(chain)
    cv = service.get(f"/series/{st['series']}/curve", vs="spot", n=71)
    by_x = {p["x"]: p for p in cv["points"]}
    assert by_x[6000]["inDomain"] is False and by_x[6000]["reason"] == {"error": "Uncertified",
                                                                         "args": {"region": 1}}
    assert by_x[10000]["reason"]["args"] == {"region": 0} and by_x[10000]["noteBps"] is None
    assert by_x[8000]["inDomain"] and by_x[8000]["current"]
    stale = _staged(chain, cfg, push_spot=False)
    service.synced(chain)
    r = service.client.get(f"/series/{stale['series']}/curve")
    assert r.status_code == 409 and r.json()["error"] == "FeedStale"


def _teacher_config_name(cfg) -> str:
    f = "teacher_config_v3.json" if cfg["modelDir"] == "model/k3" else "teacher_config.json"
    return json.loads((Path(__file__).parents[2] / "ml" / f).read_text())["name"]


def test_verify_quote_on_the_e2e_buy(service, chain, cfg, keys):
    s0 = default_series(chain, cfg)
    b = buy(chain, cfg, keys["buyer"], s0, 10_000 * USDG, fee_bps=20, fee_to=address_of(keys["dev"]))
    service.synced(chain)
    r = service.client.post("/verify-quote", json={"series": s0, "txHash": b["tx"]})
    assert r.status_code == 200, r.text
    v = r.json()
    oc, t, st = v["onChain"], v["teacher"], v["student"]
    assert oc["kind"] == "buy" and oc["priceBps"] == b["quote"][1] and oc["spreadBps"] == 30
    assert oc["priceMatches"] and oc["expectedPriceBps"] == oc["priceBps"]
    assert t["paths"] == 2**16 and t["backend"] == "numpy" and "note" in t and t["seed"] == v["teacher"]["seed"]
    assert t["config"] == _teacher_config_name(cfg)
    # the acceptance: teacher within 3 stdErr + the model's allowance of the price minus the spread
    allowance = MODEL_ERROR_BPS.get(cfg["modelDir"], 15)
    assert v["check"]["toleranceBps"] == 3 * t["stdErrBps"] + allowance
    assert abs((oc["priceBps"] - oc["spreadBps"]) - t["quoteBps"]) <= 3 * t["stdErrBps"] + allowance
    assert v["check"]["within"] is True
    # the student is the contract: its quote is the on-chain quote at the band end the buy used (the higher)
    assert st["quoteBps"] == oc["midBps"] == oc["priceBps"] - 30
    assert oc["midBps"] == max(q["noteBps"] for q in oc["bandQuotes"])
    assert v["inputs"]["volBpsAnnual"] == oc["quoteVolBps"]
    assert st["weightsHash"] == oc["weightsHash"]
    # cached on the second call
    v2 = service.client.post("/verify-quote", json={"series": s0, "txHash": b["tx"]}).json()
    assert v2["cached"] is True and v2["teacher"] == v["teacher"]


def test_verify_quote_band_ends(service, chain, cfg, keys):
    """With a vol band a buy is priced at the higher of the two quotes and a sell at the
    lower; the teacher runs at the vol of that end."""
    s0 = default_series(chain, cfg)
    band = chain.at("desk", cfg["addresses"]["desk"]).call("spread", s0)["volBandBps"]
    if band == 0:
        pytest.skip("the default listing has no vol band (model/k2)")
    b = buy(chain, cfg, keys["buyer"], s0, 500 * USDG)
    sl = sell(chain, cfg, keys["buyer"], s0, 200 * USDG)
    service.synced(chain)
    vb = service.client.post("/verify-quote", json={"series": s0, "txHash": b["tx"]}).json()
    vs = service.client.post("/verify-quote", json={"series": s0, "txHash": sl["tx"]}).json()
    for v, pick, kind in ((vb, max, "buy"), (vs, min, "sell")):
        oc = v["onChain"]
        assert oc["kind"] == kind and oc["volBandBps"] == band and oc["priceMatches"]
        assert sorted(q["volBps"] for q in oc["bandQuotes"]) == [5500 - band, 5500 + band]
        used = pick(oc["bandQuotes"], key=lambda q: q["noteBps"])
        assert oc["midBps"] == used["noteBps"] and oc["quoteVolBps"] == used["volBps"]
        assert v["inputs"]["volBpsAnnual"] == used["volBps"] and v["student"]["quoteBps"] == oc["midBps"]
        assert v["check"]["within"] is True
    assert vs["onChain"]["priceBps"] == vs["onChain"]["midBps"] - 20


def test_verify_quote_inputs_busy_and_errors(service):
    base = {"spotBpsOfInitial": 9000, "distToKnockInBps": 3000, "volBpsAnnual": 5500, "kiBarrierBps": 6000,
            "acBarrierBps": 10000, "couponBpsPerPeriod": 25, "timeToMaturitySecs": 200000 + 8 * 604800,
            "timeToNextObsSecs": 200000, "observationsRemaining": 8, "flags": 0}
    r = service.client.post("/verify-quote", json={"inputs": base})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["onChain"] is None and v["check"] is None and v["student"]["priceBps"] > 0
    assert abs(v["student"]["priceBps"] - v["teacher"]["priceBps"]) < 60
    # one run at a time: simultaneous requests with new inputs get 429
    reqs = [dict(base, timeToNextObsSecs=base["timeToNextObsSecs"] + k,
                 timeToMaturitySecs=base["timeToMaturitySecs"] + k) for k in range(1, 5)]
    with cf.ThreadPoolExecutor(4) as ex:
        codes = list(ex.map(lambda b: service.client.post("/verify-quote", json={"inputs": b}).status_code, reqs))
    assert 200 in codes and 429 in codes, codes
    r = service.client.post("/verify-quote", json={"inputs": {"spotBpsOfInitial": 1}})
    assert r.status_code == 400 and r.json()["error"] == "BadInputs"
    r = service.client.post("/verify-quote", json={"txHash": "0x" + "00" * 32})
    assert r.status_code == 404 and r.json()["error"] == "UnknownTx"
    r = service.client.post("/verify-quote", json={})
    assert r.status_code == 400
    r = service.client.post("/verify-quote", json={"inputs": base, "teacher": "v9"})
    assert r.status_code == 400 and r.json()["error"] == "BadTeacher"


@pytest.mark.skipif(not (shutil.which("nvidia-smi") and os.path.exists(TEACHER_PYTHON)), reason="no CUDA venv/GPU")
def test_verify_quote_on_the_gpu(chain, cfg, keys):
    """TEACHER_DEVICE=cuda: the torch backend with 2^18 paths."""
    s0 = default_series(chain, cfg)
    b = buy(chain, cfg, keys["buyer"], s0, 1_000 * USDG)
    tmp = tempfile.mkdtemp(prefix="sp-gpu-")
    os.environ["TEACHER_DEVICE"] = "cuda"
    svc = Service(8653, os.path.join(tmp, "g.sqlite"))
    try:
        svc.start()
        svc.synced(chain)
        r = svc.client.post("/verify-quote", json={"series": s0, "txHash": b["tx"]})
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["teacher"]["backend"] == "torch" and v["teacher"]["paths"] == 2**18
        assert "note" not in v["teacher"] and v["check"]["within"] is True
    finally:
        os.environ.pop("TEACHER_DEVICE", None)
        svc.stop()
        shutil.rmtree(tmp, ignore_errors=True)

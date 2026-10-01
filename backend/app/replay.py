"""The quoter's answer at a past time, computed off-chain.

For times before a series existed on-chain there is no block to `eth_call`
(the dev node's scenario stages a strike weeks before its first block).
This replays what NoteQuoter.notePriceBps would have returned at time t:

  - the note's state from its recorded fixings, taking each fixing as
    processed once its observation time has passed (AutocallPayout.observe:
    autocall checked before knock-in, exact cross-multiplied comparisons);
  - the spot from the feed round in force at t (the last with updatedAt <= t),
    refused as FeedStale when older than the quoter's MAX_FEED_STALENESS;
  - NoteQuoter.inputs' derivation, the model's domain check and its forward
    pass (the bit-exact student), plus the accrued coupon.

The refusals come out under the contract's error names.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from .student import domain_error, forward_batch

BPS = 10_000
UNIT = 10**6


@dataclass
class Terms:
    strike: int
    interval: int
    count: int
    ki: int
    ac: int
    coupon: int

    @classmethod
    def of_row(cls, r: dict) -> "Terms":
        return cls(r["strike_time"], r["interval"], r["count"], r["ki"], r["ac"], r["coupon"])


def note_state(t: int, terms: Terms, fixings: dict[int, int]) -> dict:
    """State at time t; fixings = {index: price} (0 = strike)."""
    st = {"phase": 0, "initial": fixings.get(0, 0), "done": 0, "knockedIn": False, "autocalled": False,
          "next": terms.strike, "pending": None, "payout": 0}
    if t <= terms.strike:
        return st
    if 0 not in fixings:
        st["pending"] = terms.strike
        return st
    st["phase"] = 1
    initial = fixings[0]
    for i in range(1, terms.count + 2):
        obs = terms.strike + i * terms.interval
        st["next"] = obs
        if obs >= t:
            return st
        if i not in fixings:
            st["pending"] = obs
            return st
        f = fixings[i] * BPS
        if i <= terms.count:
            st["done"] = i
            if f >= terms.ac * initial:
                st.update(phase=2, autocalled=True, next=0)
                return st
            if f < terms.ki * initial:
                st["knockedIn"] = True
        else:
            st.update(phase=2, next=0)
            return st
    return st


def spot_at(t: int, times: list[int], answers: list[int]) -> tuple[int, int] | None:
    """(answer, updatedAt) of the round in force at t, from rounds sorted by time."""
    k = bisect.bisect_right(times, t)
    if k == 0:
        return None
    return answers[k - 1], times[k - 1]


def quote_points(ts: list[int], terms: Terms, fixings: dict[int, int], rounds: list[tuple[int, int]],
                 export: dict | None, vol: int | None, max_staleness: int) -> list[dict]:
    """One point per time: {time, spot, spotBps, noteBps, quotable, reason, args}."""
    times = [u for u, _ in rounds]
    answers = [a for _, a in rounds]
    initial = fixings.get(0)
    pts, rows, idx = [], [], []
    for t in ts:
        sp = spot_at(t, times, answers)
        p = {"time": t, "spot": sp[0] if sp else None,
             "spotBps": sp[0] * BPS // initial if sp and initial else None,
             "noteBps": None, "quotable": False, "reason": None, "args": {}}
        pts.append(p)
        st = note_state(t, terms, fixings)
        if st["phase"] != 1:
            p["reason"] = "Settled" if st["phase"] == 2 else "NotLive"
            continue
        if st["pending"] is not None:
            p["reason"], p["args"] = "FixingPending", {"obsTime": st["pending"]}
            continue
        if sp is None or sp[0] <= 0:
            p["reason"] = "BadFeedAnswer"
            continue
        if sp[1] + max_staleness < t:
            p["reason"], p["args"] = "FeedStale", {"updatedAt": sp[1]}
            continue
        if export is None or vol is None:
            p["reason"] = "NotListed"
            continue
        spot_bps = p["spotBps"]
        rem = terms.count - st["done"]
        t_next = st["next"] - t
        values = [spot_bps, spot_bps - terms.ki, vol, terms.ki, terms.ac, terms.coupon,
                  t_next + rem * terms.interval, t_next, rem, 1 if st["knockedIn"] else 0]
        err = domain_error(export, values)
        if err:
            p["reason"], p["args"] = err
            continue
        rows.append(values)
        idx.append(len(pts) - 1)
    if rows:
        clean = forward_batch(export, rows)
        for k, c in zip(idx, clean):
            p = pts[k]
            accrued = terms.coupon * (p["time"] - terms.strike) // terms.interval
            p["noteBps"] = int(c) + accrued
            p["quotable"] = p["noteBps"] <= 65_535  # SafeCast.toUint16 in the quoter
    return pts

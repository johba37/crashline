"""Marks and cost basis.

`position()` is Desk._position in Python: what a NOTE/WRITER holding is worth at
the model's mid and what it can still lose, including the cases where the
payout is already certain (settled; final period, not knocked in).

`fifo()` is the per-account cost basis: lots are opened by the account's buys
(NoteBought / CoverBought with the account as recipient, at the USDG paid, fee
included) and closed first in, first out by its sells (NoteSold / CoverSold,
at the proceeds net of fee) and by its settlement redemptions (Redeemed, at
the payout of each leg). Tokens that came another way (mint, transfer) have no
lot: selling more than the lots hold realizes the excess at zero cost.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

UNIT = 10**6
UNIT_PER_BPS = 100
LIVE, SETTLED = 1, 2


def note_per_unit(state: dict, count: int, max_payout: int, note_bps) -> tuple[int | None, bool]:
    """(USDG base units per 1e6 NOTE, certain). None when it needs a quote and has none."""
    if state["phase"] == SETTLED:
        return state["payoutPerNote"], True
    if state["phase"] == LIVE and not state["knockedIn"] and state["observationsDone"] == count:
        return max_payout, True
    if note_bps is None:
        return None, False
    return min(note_bps * UNIT_PER_BPS, max_payout), False


def position(n: int, w: int, state: dict, count: int, max_payout: int, note_bps) -> dict:
    """Desk._position(series, strict = false): {ok, value, atRisk, noteValue, writerValue}."""
    if n == 0 and w == 0:
        return {"ok": True, "value": 0, "atRisk": 0, "noteValue": 0, "writerValue": 0}
    floor_value = n * (max_payout - UNIT) // UNIT  # an unsettled NOTE pays at least its coupons
    per, certain = note_per_unit(state, count, max_payout, note_bps)
    if per is None:
        return {"ok": False, "value": floor_value, "atRisk": n + w, "noteValue": None, "writerValue": None}
    nv = n * per // UNIT
    wv = w * (max_payout - per) // UNIT
    at_risk = 0 if certain else wv + nv - min(nv, floor_value)
    return {"ok": True, "value": nv + wv, "atRisk": at_risk, "noteValue": nv, "writerValue": wv}


@dataclass
class Book:
    """FIFO lots of one account in one series, per leg."""
    lots: dict[str, list[list[int]]] = field(default_factory=lambda: {"note": [], "writer": []})
    realized: int = 0

    def open(self, leg: str, amount: int, cost: int) -> None:
        if amount:
            self.lots[leg].append([amount, cost])

    def close(self, leg: str, amount: int, proceeds: int) -> None:
        basis = 0
        left = amount
        lots = self.lots[leg]
        while left and lots:
            lot = lots[0]
            if lot[0] <= left:
                basis += lot[1]
                left -= lot[0]
                lots.pop(0)
            else:
                part = lot[1] * left // lot[0]
                basis += part
                lot[0] -= left
                lot[1] -= part
                left = 0
        self.realized += proceeds - basis

    def basis(self, leg: str) -> int:
        return sum(c for _, c in self.lots[leg])

    def held(self, leg: str) -> int:
        return sum(a for a, _ in self.lots[leg])


def fifo(db, account: str, block: int) -> dict[str, Book]:
    """series -> Book, from the account's trades and redemptions up to `block`."""
    rows = db.query(
        "SELECT block, log_index, series, kind, account, recipient, amount, usdg FROM trades "
        "WHERE (account = ? OR recipient = ?) AND block <= ? "
        "UNION ALL "
        "SELECT block, log_index, series, name, sender, account, args, '' FROM events "
        "WHERE name = 'Redeemed' AND sender = ? AND block <= ? "
        "ORDER BY block, log_index", (account, account, block, account, block))
    settled = {r["series"]: json.loads(r["args"]) for r in db.query(
        "SELECT series, args FROM events WHERE name = 'Settled' AND block <= ?", (block,))}
    books: dict[str, Book] = {}
    for r in rows:
        b = books.setdefault(r["series"], Book())
        kind = r["kind"]
        if kind == "Redeemed":
            a = json.loads(r["amount"])
            st = settled.get(r["series"])
            if st is None:
                continue
            n, w = int(a["noteAmount"]), int(a["writerAmount"])
            b.close("note", n, n * int(st["payoutPerNote"]) // UNIT)
            b.close("writer", w, w * int(st["payoutPerWriter"]) // UNIT)
            continue
        amount, usdg = int(r["amount"]), int(r["usdg"])
        leg = "note" if kind in ("buy", "sell") else "writer"
        if kind in ("buy", "buyCover"):
            if r["recipient"] == account:
                b.open(leg, amount, usdg)
        elif r["account"] == account:  # the seller hands the tokens in
            b.close(leg, amount, usdg)
    return books

// The Desk's limits on a trade, with the numbers of its own tests (contracts/test/DeskCover.t.sol):
// a NOTE + WRITER pair locks 1.0675 USDG and the model prices the NOTE at 0.95, so a NOTE the Desk
// holds puts 0.8825 USDG at risk and a WRITER 0.1175.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { addsToDesk, riskRoom, roomFor, soldOut, usdgRoom, wholeUsdg } from '../src/market/format.ts'
import type { SeriesView, TradeKind } from '../src/market/types.ts'

const USDG = 1_000_000n
const price = (bps: number) => ({ ok: true, value: bps })

/** A series as the Desk holds it: its NOTE, its WRITER, and the risk budget of its stock. */
const series = ({ note = 0n, writer = 0n, atRisk = 0n, limit = 0n, done = 5 }) => ({
  maxPayoutPerNote: 1_067_500n,
  terms: { observationCount: 26 },
  state: { phase: 1, knockedIn: false, observationsDone: done },
  mid: { ok: true, value: { priceBps: 9500 } },
  noteInventory: note,
  listing: { soldNotional: writer },
  risk: { atRisk, limit, budgetBps: 1_000 },
  noteAsk: price(9500),
  noteBid: price(9500),
  coverAsk: price(1175),
  coverBid: price(1175),
}) as SeriesView

/** The Desk around it: the USDG it has free, and what paying the queue first would set aside. */
const desk = ({ idle = 1_000_000n * USDG, setAside = 0n } = {}) => ({ idle, queue: { waiting: 0n, setAside } })

/** The order form's check: do the Desk's limits let this amount through? */
const fits = (s: SeriesView, kind: TradeKind, amount: bigint, market = desk()) => {
  const room = roomFor(s, kind, market)
  return addsToDesk(s, kind, amount) === 0n || room === null || amount <= room
}

// test_risk_budget_limits_new_positions: 100,000 cover sold, 88,250 of a 100,000 budget used
const full = series({ note: 100_000n * USDG, atRisk: 88_250n * USDG, limit: 100_000n * USDG })

test('cover counts at what its NOTE can lose, not at its amount', () => {
  assert.equal(roomFor(full, 'buyCover', desk()), (11_750n * USDG * USDG) / 882_500n) // 13,314.44 USDG
  assert.ok(fits(full, 'buyCover', 12_000n * USDG))
  assert.ok(!fits(full, 'buyCover', 20_000n * USDG))
})

test('NOTE sold to the Desk is the same new position', () => {
  assert.ok(!fits(full, 'sell', 20_000n * USDG))
})

test('trades that shrink the position pass with the budget closed', () => {
  const closed = { ...full, risk: { ...full.risk, limit: 0n } }
  assert.ok(fits(closed, 'buy', 10_000n * USDG))
  assert.ok(fits(closed, 'sellCover', 10_000n * USDG))
})

// test_risk_budget_is_closed_until_set
test('a closed budget stops both legs on an empty Desk', () => {
  assert.ok(!fits(series({}), 'buyCover', USDG))
  assert.ok(!fits(series({}), 'buy', USDG))
})

test('cover is sold from the WRITER the Desk holds first, whatever the budget', () => {
  const s = series({ writer: 1_000n * USDG, atRisk: 117_500_000n })
  assert.equal(addsToDesk(s, 'buyCover', 1_000n * USDG), 0n)
  assert.ok(fits(s, 'buyCover', 1_000n * USDG))
  assert.ok(!fits(s, 'buyCover', 1_001n * USDG))
})

test('a note whose payout is certain has nothing at risk', () => {
  assert.equal(riskRoom(series({ done: 26 }), 'buyCover', 0n), null)
  assert.equal(riskRoom({ ...series({}), state: { ...series({}).state, phase: 2 } }, 'buy', 0n), null)
})

test('but it can’t add to the Desk while its stock is over budget', () => {
  assert.equal(riskRoom(series({ done: 26, atRisk: USDG }), 'buyCover', 0n), 0n)
})

test('paying the queue first lowers the limit', () => {
  // 50,000 USDG leave the vault, and the budget is 10% of it: 6,750 of room instead of 11,750
  const paid = desk({ setAside: 50_000n * USDG })
  assert.equal(roomFor(full, 'buyCover', paid), (6_750n * USDG * USDG) / 882_500n) // 7,648.72 USDG
  assert.ok(!fits(full, 'buyCover', 12_000n * USDG, paid))
  assert.ok(fits(full, 'buyCover', 7_000n * USDG, paid))
})

// The Desk puts up a pair's 1.0675 USDG for cover it sells at 0.1175: 0.95 of its own for each one.
const open = series({ limit: 1_000_000n * USDG })

test('the Desk can only sell the cover it has the USDG to back', () => {
  const short = desk({ idle: 50_000n * USDG }) // the rest is locked in other notes
  assert.equal(usdgRoom(open, 'buyCover', 50_000n * USDG), 52_631_578_946n) // 50,000 / 0.95, a base unit short for rounding
  assert.ok(fits(open, 'buyCover', 52_000n * USDG, short))
  assert.ok(!fits(open, 'buyCover', 53_000n * USDG, short))
})

test('USDG the queue is paid first is not there for the trade', () => {
  const paid = desk({ setAside: 900_000n * USDG }) // 100,000 left: 110,000 of cover needs 104,500
  assert.ok(!fits(open, 'buyCover', 110_000n * USDG, paid))
  assert.ok(fits(open, 'buyCover', 105_000n * USDG, paid))
})

test('a NOTE the Desk can’t pair is paid for out of its USDG', () => {
  const short = desk({ idle: 950n * USDG }) // it buys at 0.95
  assert.ok(fits(open, 'sell', 900n * USDG, short))
  assert.ok(!fits(open, 'sell', 1_100n * USDG, short))
})

test('what the Desk holds is traded without any USDG of its own', () => {
  const s = series({ writer: 1_000n * USDG, limit: 1_000_000n * USDG })
  assert.ok(fits(s, 'buyCover', 1_000n * USDG, desk({ idle: 0n })))
  assert.equal(usdgRoom(open, 'buyCover', -USDG), 0n) // nothing free and nothing held: no room
})

test('an "at most" amount is rounded down, so entering it goes through', () => {
  assert.equal(wholeUsdg(13_999_999n), '13')
})

// test_sellCover_unpaired_counts_against_the_cap: with its WRITER at the cap the Desk sells no NOTE
// and buys no cover back, while a NOTE sold to it still pairs with that WRITER
test('a note is sold out when its buying price is refused for the cap alone and it sells back', () => {
  const capFull = { ok: false, refusal: { error: 'CapExceeded', args: [USDG, 0n] } }
  const price = { ok: true, value: 9500 }
  const s = { ...series({}), noteAsk: capFull, noteBid: price, coverAsk: price, coverBid: capFull } as SeriesView
  assert.ok(soldOut(s, true))
  assert.ok(!soldOut(s, false)) // its cover can still be bought
  assert.ok(!soldOut({ ...s, noteAsk: { ok: false, refusal: { error: 'FeedStale', args: [] } } }, true)) // paused, not sold out
  assert.ok(!soldOut({ ...s, noteBid: capFull }, true)) // nothing to sell back either
})

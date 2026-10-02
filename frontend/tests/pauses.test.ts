// The two moments a price is not to be trusted: around a weekly check, where it pauses and then
// comes back at a new level, and between the price on screen and the one the Desk gives a moment later.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { paused, tradeAmounts, tradeLimit } from '../src/market/format.ts'
import type { Refusable } from '../src/market/types.ts'

const USDG = 1_000_000n
const refused = (error: string, ...args: unknown[]): Refusable<number> => ({ ok: false, refusal: { error, args } })

test('a price that comes back by itself is paused', () => {
  assert.ok(paused(refused('TooCloseToObservation', 1_790_000_000))) // the Desk, shortly before a weekly check
  assert.ok(paused(refused('FixingPending', 1_790_000_000))) // the check has passed, its price isn’t recorded
  assert.ok(paused(refused('Uncertified', 1))) // near the crash line ahead of a check
  assert.ok(paused(refused('FeedStale', 1_790_000_000))) // no fresh answer from the feed
})

test('a price that needs something else is not', () => {
  assert.ok(!paused({ ok: true, value: 640 }))
  assert.ok(!paused(refused('CapExceeded', USDG, 0n))) // sold out
  assert.ok(!paused(refused('NotLive'))) // ended
  assert.ok(!paused(refused('OutOfRange', 0, 4_800n))) // may stay outside what the model knows
})

test('an order is held to the amount on screen, with the slippage limit on it', () => {
  // cover for 1,000 USDG at 4.20%: 42.00 USDG on screen
  const shown = tradeAmounts('buyCover', 1_000n * USDG, 420, 0).total
  assert.equal(shown, 42n * USDG)
  assert.equal(tradeLimit(true, shown, 50), 42_210_000n) // 0.5% on top
  // the weekly check came in and the same cover is 18.00%: far past the limit
  assert.ok(tradeAmounts('buyCover', 1_000n * USDG, 1800, 0).total > tradeLimit(true, shown, 50))
  // the same price, or a better one, goes through
  assert.ok(shown <= tradeLimit(true, shown, 50))
  assert.ok(tradeAmounts('buyCover', 1_000n * USDG, 300, 0).total <= tradeLimit(true, shown, 50))
})

test('a sale must bring the amount on screen, less the slippage limit', () => {
  const shown = tradeAmounts('sell', 1_000n * USDG, 9120, 0).total // 912.00 USDG
  assert.equal(tradeLimit(false, shown, 50), 907_440_000n)
  assert.ok(tradeAmounts('sell', 1_000n * USDG, 3000, 0).total < tradeLimit(false, shown, 50)) // the price fell: stopped
  assert.ok(tradeAmounts('sell', 1_000n * USDG, 9200, 0).total >= tradeLimit(false, shown, 50))
})

test('the limits round against the trade, as the Desk’s own do', () => {
  assert.equal(tradeLimit(true, 1n, 50), 2n) // a most, rounded up
  assert.equal(tradeLimit(false, 1n, 50), 0n) // a least, rounded down
})

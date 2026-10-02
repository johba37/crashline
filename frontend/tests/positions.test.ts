// What a wallet holds, read from the backend's /accounts/{address} and /series/{address}/history
// (docs/backend.md). The answers below are the dev node's, after one buy and one buyCover.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { endedAt, fromAccount, fromHistory, reflects } from '../src/market/positions.ts'
import type { SeriesView } from '../src/market/types.ts'

const SERIES = '0x2d89f7f290d1a8bda710c66b08da108b3549be7b'
// As the chain reader has it: the same address with its checksum.
const listed = [{ address: '0x2D89F7f290D1a8bDa710C66B08Da108b3549bE7b' }] as SeriesView[]

const row = (note: string, writer: string, paidNote: string, paidWriter: string, series = SERIES) => ({
  series, note, writer, noteMark: '0', coverMark: '0', costBasis: { note: paidNote, writer: paidWriter }, realized: '0',
})

test('a NOTE and a cover holding each become a position with what was paid', () => {
  const [note] = fromAccount({ positions: [row('1000000', '0', '991000', '0')] }, listed)
  assert.deepEqual(note, { series: listed[0], side: 'note', amount: 1_000_000n, paid: 991_000n, path: [] })

  const [cover] = fromAccount({ positions: [row('0', '1000000', '0', '90500')] }, listed)
  assert.deepEqual(cover, { series: listed[0], side: 'cover', amount: 1_000_000n, paid: 90_500n, path: [] })
})

test('both sides of one note are two positions', () => {
  const held = fromAccount({ positions: [row('2000000', '500000', '1982000', '45250')] }, listed)
  assert.deepEqual(held.map((p) => [p.side, p.amount, p.paid]), [['cover', 500_000n, 45_250n], ['note', 2_000_000n, 1_982_000n]])
})

test('a note that was sold back, or that the market does not show, is no position', () => {
  assert.deepEqual(fromAccount({ positions: [row('0', '0', '0', '0')] }, listed), [])
  assert.deepEqual(fromAccount({ positions: [row('1000000', '0', '991000', '0', '0x' + '1'.repeat(40))] }, listed), [])
  assert.deepEqual(fromAccount({ positions: [] }, listed), [])
})

test('the history is the stock’s path in feed units', () => {
  const points = [
    { time: 1790493562, block: null, spotBps: 10000, spot: '270393000000', noteBps: null, coverBps: null, quotable: false, reason: 'NotLive', source: 'replay', observation: 0 },
    { time: 1790887878, block: 62, spotBps: 9954, spot: '269149192200', noteBps: 9837, coverBps: 838, quotable: true, reason: null, source: 'chain', observation: null },
  ]
  assert.deepEqual(fromHistory({ series: SERIES, step: 86400, points }, 270_393_000_000n), [
    { time: 1790493562, price: 270_393_000_000n },
    { time: 1790887878, price: 269_149_192_200n },
  ])
})

test('a point the feed had no price for is left out of the path', () => {
  assert.deepEqual(fromHistory({ points: [{ time: 1790493562, spot: '270393000000' }, { time: 1790887878, spot: null }] }, 270_393_000_000n), [
    { time: 1790493562, price: 270_393_000_000n },
  ])
})

// The dev node's answer for a note that ended in a crash, asked up to its end: the blocks that
// recorded its last two checks came after the end, so only the observations have those prices.
test('the weekly checks are on the path at their own time, so an ended note’s path reaches its end', () => {
  const points = [
    { time: 1790854442, spot: '20500000000' }, // a check the history already has a point for
    { time: 1790940844, spot: '20750000000' },
  ]
  const observations = [
    { obsTime: 1790854442, index: 18, fixingBps: 8200 },
    { obsTime: 1795692842, index: 26, fixingBps: 5200 },
    { obsTime: 1796297642, index: 27, fixingBps: 5400 },
  ]
  assert.deepEqual(fromHistory({ points, observations }, 25_000_000_000n), [
    { time: 1790854442, price: 20_500_000_000n },
    { time: 1790940844, price: 20_750_000_000n },
    { time: 1795692842, price: 13_000_000_000n },
    { time: 1796297642, price: 13_500_000_000n },
  ])
})

test('an answer reflects a trade once it was read at the trade\u2019s block or later', () => {
  assert.equal(reflects({ positions: [], block: 62 }, 63n), false)
  assert.equal(reflects({ positions: [], block: 63 }, 63n), true)
  assert.equal(reflects({ positions: [], block: 64 }, 63n), true)
})

test('a note that ended early stopped at that weekly check, else at its end date', () => {
  const WEEK = 604_800
  const ended = (autocalled: boolean) => ({
    terms: { strikeTime: 1_000_000, observationInterval: WEEK },
    state: { autocalled, observationsDone: 3, maturity: 1_000_000 + 27 * WEEK },
  }) as SeriesView
  assert.equal(endedAt(ended(true)), 1_000_000 + 3 * WEEK)
  assert.equal(endedAt(ended(false)), 1_000_000 + 27 * WEEK)
})

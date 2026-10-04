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
// The wallet, as the wallet connector has it: with its checksum.
const OWNER = '0x933a8C0f852f034f1f3b4a17Db4aD831eB3F67e3'

test('a NOTE and a cover holding each become a position with what was paid', () => {
  const [note] = fromAccount({ positions: [row('1000000', '0', '991000', '0')] }, listed, OWNER)
  assert.deepEqual(note, { series: listed[0], side: 'note', amount: 1_000_000n, paid: 991_000n, bought: [], exits: [], closed: false, path: [] })

  const [cover] = fromAccount({ positions: [row('0', '1000000', '0', '90500')] }, listed, OWNER)
  assert.deepEqual(cover, { series: listed[0], side: 'cover', amount: 1_000_000n, paid: 90_500n, bought: [], exits: [], closed: false, path: [] })
})

test('both sides of one note are two positions', () => {
  const held = fromAccount({ positions: [row('2000000', '500000', '1982000', '45250')] }, listed, OWNER)
  assert.deepEqual(held.map((p) => [p.side, p.amount, p.paid]), [['cover', 500_000n, 45_250n], ['note', 2_000_000n, 1_982_000n]])
})

test('a note that was sold back, or that the market does not show, is no position', () => {
  assert.deepEqual(fromAccount({ positions: [row('0', '0', '0', '0')] }, listed, OWNER), [])
  assert.deepEqual(fromAccount({ positions: [row('1000000', '0', '991000', '0', '0x' + '1'.repeat(40))] }, listed, OWNER), [])
  assert.deepEqual(fromAccount({ positions: [] }, listed, OWNER), [])
})

// The backend lists the trades the wallet made or received, newest first, in lower case.
const owner = OWNER.toLowerCase()
const trade = (kind: string, time: number, to = owner, series = SERIES, amount = '1000000', usdg = '90500') =>
  ({ series, kind, account: owner, to, time, amount, usdg })

test('a position knows when the wallet bought that side, oldest first', () => {
  const trades = [
    trade('buyCover', 1790900000),
    trade('sellCover', 1790800000), // a sale is no buy
    trade('buyCover', 1790700000),
    trade('buy', 1790600000, '0x' + '2'.repeat(40)), // bought for someone else
    trade('buyCover', 1790500000, owner, '0x' + '1'.repeat(40)), // another note
  ]
  const held = fromAccount({ positions: [row('1000000', '2000000', '991000', '181000')], trades }, listed, OWNER)
  assert.deepEqual(held.map((p) => [p.side, p.bought]), [['cover', [1790700000, 1790900000]], ['note', []]])
})

// A note that ended: NOTE paid 0.6075 USDG each, so cover 1.0675 - 0.6075 = 0.46.
const ended = [{ address: SERIES, maxPayoutPerNote: 1_067_500n, state: { payoutPerNote: 607_500n } }] as unknown as SeriesView[]

test('a side sold back in full is closed, with what its buys cost and what the sale brought', () => {
  const trades = [trade('sellCover', 1790900000, owner, SERIES, '1000000', '171000'), trade('buyCover', 1790500000)]
  const [cover, ...rest] = fromAccount({ positions: [row('0', '0', '0', '0')], trades }, listed, OWNER)
  assert.deepEqual(rest, [])
  assert.deepEqual(cover, {
    series: listed[0], side: 'cover', amount: 1_000_000n, paid: 90_500n, bought: [1790500000],
    exits: [{ time: 1790900000, kind: 'sold', amount: 1_000_000n, usdg: 171_000n }], closed: true, path: [],
  })
})

test('a side collected from a note that ended is closed at the note’s payout for that side', () => {
  const trades = [trade('buyCover', 1790500000), trade('buy', 1790500100, owner, SERIES, '2000000', '1975200')]
  // One collect of both legs, by the wallet; another wallet's collect of the same note is not the wallet's.
  const redeemed = { events: [
    { series: SERIES, time: 1796400000, args: { caller: owner, noteAmount: '2000000', writerAmount: '1000000' } },
    { series: SERIES, time: 1796400100, args: { caller: '0x' + '3'.repeat(40), noteAmount: '5000000', writerAmount: '0' } },
  ] }
  // The backend may no longer list a note the wallet emptied: its trades and collects still name it.
  const held = fromAccount({ positions: [], trades }, ended, OWNER, redeemed)
  assert.deepEqual(held.map((p) => [p.side, p.closed, p.amount, p.paid, p.exits]), [
    ['cover', true, 1_000_000n, 90_500n, [{ time: 1796400000, kind: 'collected', amount: 1_000_000n, usdg: 460_000n }]],
    ['note', true, 2_000_000n, 1_975_200n, [{ time: 1796400000, kind: 'collected', amount: 2_000_000n, usdg: 1_215_000n }]],
  ])
})

test('a side bought and sold twice is as much as it held at once, with both buys paid', () => {
  const trades = [
    trade('sellCover', 1790900000, owner, SERIES, '10000000', '1710000'), trade('buyCover', 1790800000, owner, SERIES, '10000000', '1797000'),
    trade('sellCover', 1790700000, owner, SERIES, '10000000', '1710000'), trade('buyCover', 1790600000, owner, SERIES, '10000000', '1797000'),
  ]
  const [cover] = fromAccount({ positions: [row('0', '0', '0', '0')], trades }, listed, OWNER)
  assert.deepEqual([cover.closed, cover.amount, cover.paid, cover.exits.length], [true, 10_000_000n, 3_594_000n, 2])
})

test('a side partly sold stays open, with the sale among its exits', () => {
  const trades = [trade('sell', 1790900000, owner, SERIES, '400000', '390000'), trade('buy', 1790500000, owner, SERIES, '1000000', '991000')]
  const [note] = fromAccount({ positions: [row('600000', '0', '594600', '0')], trades }, listed, OWNER)
  assert.deepEqual([note.closed, note.amount, note.paid, note.exits.map((e) => [e.kind, e.amount])], [false, 600_000n, 594_600n, [['sold', 400_000n]]])
})

test('the wallet’s own trade list wins over the account’s last 50, which may have lost the buy', () => {
  // The account's list kept only the sale; GET /trades still has the buy from before.
  const account = { positions: [row('0', '0', '0', '0')], trades: [trade('sellCover', 1790900000, owner, SERIES, '1000000', '171000')] }
  const all = { trades: [...account.trades, trade('buyCover', 1790500000)] }
  const [cover] = fromAccount(account, listed, OWNER, {}, all)
  assert.deepEqual([cover.closed, cover.amount, cover.paid, cover.bought], [true, 1_000_000n, 90_500n, [1790500000]])
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

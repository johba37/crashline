// Example market for building, testing and demoing the dashboard. It holds every kind of note the
// flow has to handle: three stocks, several lengths, several levels, a note past its crash line, a
// note the model gives no price for, and a stock the Desk is nearly full on. Shaped exactly like
// the chain reader's output. The numbers are illustrative, not model output; the UI says so.
import type { Address, Hex } from 'viem'
import { WEEK } from './format.ts'
import type { MarketData, Position, PricePoint, PricerInputs, Refusable, SeriesView } from './types.ts'

const now = Math.floor(Date.now() / 1000)
const DAY = 86400
const addr = (n: number) => `0x${n.toString(16).padStart(40, '0')}` as Address

const DESK = addr(0xde5c)
const USDG = '0x7E955252E15c84f5768B83c41a71F9eba181802F' as Address
const MODEL = addr(0x6b33)
const MODEL_HASH = '0x745cd5f814984c9433d1055e47248d2cfd6a5ecfe0ae5d5e24fbc8b21303523f' as Hex // model/k3

const SPREAD = { bidBps: 30, askBps: 30, volBandBps: 0 }

type Stock = { symbol: string; feed: Address; spot: bigint; risk: SeriesView['risk'] }
const TSLA: Stock = { symbol: 'TSLA', feed: addr(0xfeed01), spot: 25_130_000_000n, risk: { atRisk: 212_400_000_000n, limit: 250_000_000_000n } }
// 400 USDG of room left: an order above it shows "the Desk can take on 400 USDG more".
const NVDA: Stock = { symbol: 'NVDA', feed: addr(0xfeed02), spot: 13_480_000_000n, risk: { atRisk: 149_600_000_000n, limit: 150_000_000_000n } }
const AAPL: Stock = { symbol: 'AAPL', feed: addr(0xfeed03), spot: 22_750_000_000n, risk: { atRisk: 20_000_000_000n, limit: 150_000_000_000n } }

const ok = <T>(value: T): Refusable<T> => ({ ok: true, value })
const refused = <T>(error: string, ...args: unknown[]): Refusable<T> => ({ ok: false, refusal: { error, args } })

type Sample = {
  n: number
  stock: Stock
  initial: bigint
  count?: number // weekly checks; the note runs one week longer
  done: number
  sinceObs: number
  coverMid: number | null // null: the model gives no price
  knockedIn?: boolean
  inventory?: bigint
  ended?: boolean // ended early at check `done`: the stock was back at its starting price
}

function series({ n, stock, initial, count = 26, done, sinceObs, coverMid, knockedIn = false, inventory = 0n, ended = false }: Sample): SeriesView {
  const terms = { feed: stock.feed, observationInterval: WEEK, observationCount: count, kiBarrierBps: 6000, acBarrierBps: 10_000, couponBpsPerPeriod: 25 }
  const maxBps = 10_000 + terms.couponBpsPerPeriod * (count + 1) // 1 + 0.25% a week, per NOTE + WRITER
  const strikeTime = now - done * WEEK - sinceObs
  const nextObservation = strikeTime + (done + 1) * WEEK
  const spotBps = Number((stock.spot * 10_000n) / initial)
  const inputs: PricerInputs = {
    spotBpsOfInitial: spotBps,
    distToKnockInBps: spotBps - terms.kiBarrierBps,
    volBpsAnnual: 5500,
    kiBarrierBps: terms.kiBarrierBps,
    acBarrierBps: terms.acBarrierBps,
    couponBpsPerPeriod: terms.couponBpsPerPeriod,
    timeToMaturitySecs: nextObservation - now + (count - done) * WEEK,
    timeToNextObsSecs: nextObservation - now,
    observationsRemaining: count - done,
    flags: knockedIn ? 1 : 0,
  }
  // Near the knock-in barrier on observation day the model refuses (Uncertified region 1).
  const quote = <T>(value: (mid: number) => T): Refusable<T> =>
    ended ? refused('NotLive') : coverMid === null ? refused('Uncertified', 1) : ok(value(maxBps - coverMid))
  return {
    address: addr(0x5e0000 + n),
    symbol: stock.symbol,
    terms: { ...terms, strikeTime },
    state: {
      phase: ended ? 2 : 1,
      initialFixing: initial,
      observationsDone: done,
      knockedIn,
      autocalled: ended,
      nextObservation: ended ? 0 : nextObservation,
      maturity: strikeTime + (count + 1) * WEEK,
      // Ending early at check i pays a NOTE its amount plus the income so far: 1 + 0.25% x i.
      payoutPerNote: ended ? BigInt(10_000 + terms.couponBpsPerPeriod * done) * 100n : 0n,
    },
    listing: { active: true, pricer: MODEL, volBpsAnnual: 5500, capNotional: 500_000_000_000n, soldNotional: 180_000_000_000n },
    spread: SPREAD,
    maxPayoutPerNote: BigInt(maxBps) * 100n,
    note: addr(0xa000 + n),
    writer: addr(0xb000 + n),
    pendingObservation: { pending: false, obsTime: 0 },
    spot: stock.spot,
    spotUpdatedAt: now - 420,
    mid: quote((mid) => ({ priceBps: mid, weightsHash: MODEL_HASH })),
    noteAsk: quote((mid) => Math.min(mid + SPREAD.askBps, maxBps)),
    noteBid: quote((mid) => mid - SPREAD.bidBps),
    coverAsk: quote((mid) => maxBps - (mid - SPREAD.bidBps)),
    coverBid: quote((mid) => maxBps - Math.min(mid + SPREAD.askBps, maxBps)),
    inputs: ok(inputs),
    noteInventory: inventory,
    risk: stock.risk,
  }
}

export const fixtureMarket: MarketData = {
  source: 'fixtures',
  desk: DESK,
  usdg: USDG,
  // The menu a buyer chooses from. Struck at different prices, the knock-in sits at a different
  // distance from today's price (the level); with a different number of observations or an
  // earlier strike, the note ends on a different date (how long).
  series: [
    // TSLA: four lengths, two levels in two of them, and one note without a price.
    series({ n: 1, stock: TSLA, initial: 26_240_000_000n, done: 5, sinceObs: 2 * DAY, coverMid: 640, inventory: 12_500_000_000n }),
    series({ n: 2, stock: TSLA, initial: 28_190_000_000n, done: 6, sinceObs: 4 * DAY, coverMid: 1040 }),
    series({ n: 3, stock: TSLA, initial: 30_975_000_000n, done: 13, sinceObs: 3 * DAY, coverMid: 1340, inventory: 40_000_000_000n }),
    series({ n: 4, stock: TSLA, initial: 39_920_000_000n, done: 20, sinceObs: WEEK - 16 * 3600, coverMid: null }),
    series({ n: 5, stock: TSLA, initial: 25_480_000_000n, count: 13, done: 0, sinceObs: 2 * DAY, coverMid: 390 }),
    series({ n: 6, stock: TSLA, initial: 24_990_000_000n, count: 3, done: 0, sinceObs: DAY, coverMid: 35 }),
    series({ n: 7, stock: TSLA, initial: 25_260_000_000n, done: 0, sinceObs: DAY, coverMid: 870 }),
    // NVDA: a note already past its crash line, a fresh one, and a Desk that is nearly full.
    series({ n: 8, stock: NVDA, initial: 23_600_000_000n, done: 9, sinceObs: 3 * DAY, coverMid: 4420, knockedIn: true }),
    series({ n: 9, stock: NVDA, initial: 13_820_000_000n, done: 2, sinceObs: 3 * DAY, coverMid: 790, inventory: 5_000_000_000n }),
    // AAPL: one note, so one length and one level.
    series({ n: 10, stock: AAPL, initial: 22_910_000_000n, count: 12, done: 1, sinceObs: 2 * DAY, coverMid: 260 }),
  ],
  models: {
    [MODEL]: {
      address: MODEL,
      weightsHash: MODEL_HASH,
      certifiedRange: [
        [5000n, 12_000n], [-1000n, 6000n], [2000n, 9000n], [6000n, 6000n], [10_000n, 10_000n],
        [25n, 25n], [BigInt(WEEK), BigInt(27 * WEEK)], [0n, BigInt(WEEK)], [1n, 26n], [0n, 1n],
      ],
    },
  },
  queuedShares: 0n,
  fees: { maxFeeBps: 200, maxCoverFeeBps: 1000, backstopShareBps: 5000 },
  now,
}

/**
 * An example price path for a note, one point a day from its start to `end`: a smooth move from
 * the starting price to `last`, with dips and ripples that never rise above that line (so no
 * weekly check on the way would have ended the note early).
 */
function path(s: SeriesView, end: number, last: bigint): PricePoint[] {
  const from = Number(s.state.initialFixing)
  const days = Math.max(2, Math.round((end - s.terms.strikeTime) / DAY))
  return Array.from({ length: days + 1 }, (_, i) => {
    const u = i / days
    const dip = 1 - Math.sin(Math.PI * u) * (0.035 * (0.6 + 0.4 * Math.sin(11 * u)) + 0.014 * Math.abs(Math.sin(37 * u)) + 0.008 * Math.abs(Math.sin(91 * u)))
    const price = i === days ? last : BigInt(Math.round(from * (Number(last) / from) ** u * dip))
    return { time: i === days ? end : s.terms.strikeTime + i * DAY, price }
  })
}

const note = (n: number) => fixtureMarket.series[n - 1]
const USDG_UNIT = 1_000_000n
// A TSLA note that ended early three weeks ago: its cover gets back 0.25% for each week that was left.
const endedEarly = series({ n: 11, stock: TSLA, initial: 24_310_000_000n, done: 3, sinceObs: 3 * WEEK, coverMid: null, ended: true })

/**
 * What a wallet could hold, one of each kind: cover that is waiting, cover that is switched on,
 * a NOTE that is earning, and cover on a note that ended early and waits to be collected.
 */
export const fixturePositions: Position[] = [
  { series: note(1), side: 'cover', amount: 1_000n * USDG_UNIT, paid: 74n * USDG_UNIT, path: path(note(1), now, TSLA.spot) },
  { series: note(8), side: 'cover', amount: 500n * USDG_UNIT, paid: 61_500_000n, path: path(note(8), now, NVDA.spot) },
  { series: note(10), side: 'note', amount: 2_000n * USDG_UNIT, paid: 2_014n * USDG_UNIT, path: path(note(10), now, AAPL.spot) },
  { series: endedEarly, side: 'cover', amount: 1_000n * USDG_UNIT, paid: 88n * USDG_UNIT, path: path(endedEarly, now - 3 * WEEK, 24_780_000_000n) },
]

// Sample market for building and demoing the dashboard before the contracts are on testnet.
// Shaped exactly like the chain reader's output. The numbers are illustrative, not model output;
// the UI labels them as sample data.
import type { Address, Hex } from 'viem'
import { WEEK } from './format.ts'
import type { MarketData, PricerInputs, Refusable, SeriesView } from './types.ts'

const now = Math.floor(Date.now() / 1000)
const addr = (n: number) => `0x${n.toString(16).padStart(40, '0')}` as Address

const DESK = addr(0xde5c)
const USDG = '0x7E955252E15c84f5768B83c41a71F9eba181802F' as Address
const FEED = addr(0xfeed)
const K2 = addr(0x6b32)
const K2_HASH = '0x3f9a51c07e2d84b6a1f0c9e35d7b2a4e8c6f1d0b9a7e5c3f2d1b0a9e8c7d6f5e' as Hex

const SPOT = 25_130_000_000n // $251.30, 8 decimals
const TERMS = { feed: FEED, observationInterval: WEEK, observationCount: 26, kiBarrierBps: 6000, acBarrierBps: 10_000, couponBpsPerPeriod: 25 }
const MAX_PAYOUT = 1_067_500n // 1 + 0.25% x 27, per NOTE + WRITER
const MAX_BPS = 10_675
const SPREAD = { bidBps: 30, askBps: 30, volBandBps: 0 }

const ok = <T>(value: T): Refusable<T> => ({ ok: true, value })
const refused = <T>(error: string, ...args: unknown[]): Refusable<T> => ({ ok: false, refusal: { error, args } })

type Sample = { n: number; initial: bigint; done: number; sinceObs: number; coverMid: number | null; inventory: bigint }

function series({ n, initial, done, sinceObs, coverMid, inventory }: Sample): SeriesView {
  const strikeTime = now - done * WEEK - sinceObs
  const nextObservation = strikeTime + (done + 1) * WEEK
  const spotBps = Number((SPOT * 10_000n) / initial)
  const inputs: PricerInputs = {
    spotBpsOfInitial: spotBps,
    distToKnockInBps: spotBps - TERMS.kiBarrierBps,
    volBpsAnnual: 5500,
    kiBarrierBps: TERMS.kiBarrierBps,
    acBarrierBps: TERMS.acBarrierBps,
    couponBpsPerPeriod: TERMS.couponBpsPerPeriod,
    timeToMaturitySecs: nextObservation - now + (TERMS.observationCount - done) * WEEK,
    timeToNextObsSecs: nextObservation - now,
    observationsRemaining: TERMS.observationCount - done,
    flags: 0,
  }
  // Near the knock-in barrier on observation day the model refuses (Uncertified region 1).
  const quote = <T>(value: (mid: number) => T): Refusable<T> =>
    coverMid === null ? refused('Uncertified', 1) : ok(value(MAX_BPS - coverMid))
  return {
    address: addr(0x5e0000 + n),
    symbol: 'TSLA',
    terms: { ...TERMS, strikeTime },
    state: {
      phase: 1,
      initialFixing: initial,
      observationsDone: done,
      knockedIn: false,
      autocalled: false,
      nextObservation,
      maturity: strikeTime + (TERMS.observationCount + 1) * WEEK,
      payoutPerNote: 0n,
    },
    listing: { active: true, pricer: K2, volBpsAnnual: 5500, capNotional: 500_000_000_000n, soldNotional: 180_000_000_000n },
    spread: SPREAD,
    maxPayoutPerNote: MAX_PAYOUT,
    note: addr(0xa000 + n),
    writer: addr(0xb000 + n),
    pendingObservation: { pending: false, obsTime: 0 },
    spot: SPOT,
    spotUpdatedAt: now - 420,
    mid: quote((mid) => ({ priceBps: mid, weightsHash: K2_HASH })),
    noteAsk: quote((mid) => Math.min(mid + SPREAD.askBps, MAX_BPS)),
    noteBid: quote((mid) => mid - SPREAD.bidBps),
    coverAsk: quote((mid) => MAX_BPS - (mid - SPREAD.bidBps)),
    coverBid: quote((mid) => MAX_BPS - Math.min(mid + SPREAD.askBps, MAX_BPS)),
    inputs: ok(inputs),
    noteInventory: inventory,
    risk: { atRisk: 212_400_000_000n, limit: 250_000_000_000n },
  }
}

export const fixtureMarket: MarketData = {
  source: 'fixtures',
  desk: DESK,
  usdg: USDG,
  // Struck at different prices, so the knock-in sits at a different distance from today's price:
  // these are the protection levels a buyer can choose between.
  series: [
    series({ n: 1, initial: 26_240_000_000n, done: 5, sinceObs: 2 * 86400, coverMid: 640, inventory: 12_500_000_000n }),
    series({ n: 2, initial: 28_190_000_000n, done: 11, sinceObs: 4 * 86400, coverMid: 890, inventory: 0n }),
    series({ n: 3, initial: 30_975_000_000n, done: 17, sinceObs: 3 * 86400, coverMid: 1270, inventory: 40_000_000_000n }),
    series({ n: 4, initial: 39_920_000_000n, done: 20, sinceObs: WEEK - 16 * 3600, coverMid: null, inventory: 0n }),
  ],
  models: {
    [K2]: {
      address: K2,
      weightsHash: K2_HASH,
      certifiedRange: [
        [5000n, 12_000n], [-1000n, 6000n], [5500n, 5500n], [6000n, 6000n], [10_000n, 10_000n],
        [25n, 25n], [BigInt(WEEK), BigInt(27 * WEEK)], [0n, BigInt(WEEK)], [1n, 26n], [0n, 1n],
      ],
    },
  },
  queuedShares: 0n,
  fees: { maxFeeBps: 200, maxCoverFeeBps: 1000, backstopShareBps: 5000 },
  now,
}

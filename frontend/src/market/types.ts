// What the dashboard reads, shaped like the contract interfaces (docs/interfaces.md) so that
// fixtures and the chain reader return the same thing. bigint wherever viem returns bigint
// (integers wider than 48 bits), number otherwise.
import type { Address, Hex } from 'viem'

/** INoteSeries.SeriesTerms */
export type SeriesTerms = {
  feed: Address
  strikeTime: number // unix seconds
  observationInterval: number // seconds (604800 on the Desk grid)
  observationCount: number
  kiBarrierBps: number // of the initial fixing
  acBarrierBps: number
  couponBpsPerPeriod: number // of notional
}

export const Phase = { Pending: 0, Live: 1, Settled: 2 } as const

/** INoteSeries.SeriesState */
export type SeriesState = {
  phase: number // Phase
  initialFixing: bigint // feed decimals (8)
  observationsDone: number
  knockedIn: boolean
  autocalled: boolean
  nextObservation: number // unix seconds, 0 once settled
  maturity: number
  payoutPerNote: bigint // USDG base units per 1 NOTE, 0 until settled
}

/** IDesk.Listing */
export type Listing = {
  active: boolean
  pricer: Address
  volBpsAnnual: number
  capNotional: bigint // most WRITER the Desk may hold in the series
  soldNotional: bigint // WRITER it holds now
}

/** IDeskCover.Spread */
export type Spread = { bidBps: number; askBps: number; volBandBps: number }

/** ISurrogatePricer.PricerInputs: exactly what the model saw (quoter.inputs). */
export type PricerInputs = {
  spotBpsOfInitial: number
  distToKnockInBps: number
  volBpsAnnual: number
  kiBarrierBps: number
  acBarrierBps: number
  couponBpsPerPeriod: number
  timeToMaturitySecs: number
  timeToNextObsSecs: number
  observationsRemaining: number
  flags: number // bit0: knocked in
}

/** A contract revert, decoded against the merged ABIs: the error name and its arguments. */
export type Refusal = { error: string; args: readonly unknown[] }

/** A read that the contracts may refuse (quotes revert instead of guessing). */
export type Refusable<T> = { ok: true; value: T } | { ok: false; refusal: Refusal }

/** One listed series with everything the market list, the detail and the trade panel show. */
export type SeriesView = {
  address: Address
  symbol: string // underlying ticker, from the feed's description ('TSLA')
  terms: SeriesTerms
  state: SeriesState
  listing: Listing
  spread: Spread
  maxPayoutPerNote: bigint // USDG base units locked per NOTE + WRITER
  note: Address
  writer: Address
  pendingObservation: { pending: boolean; obsTime: number }
  spot: bigint | null // latest feed answer, 8 decimals
  spotUpdatedAt: number | null
  /** quoter.notePriceBps at the listing's vol: the mid, and the model version that priced it. */
  mid: Refusable<{ priceBps: number; weightsHash: Hex }>
  /** Desk prices applied for 1 unit (1e6 base units), spread included: the priceBps of each quote. */
  noteAsk: Refusable<number> // quoteBuy
  noteBid: Refusable<number> // quoteSell
  coverAsk: Refusable<number> // quoteBuyCover
  coverBid: Refusable<number> // quoteSellCover
  inputs: Refusable<PricerInputs>
  noteInventory: bigint // NOTE.balanceOf(desk)
  risk: { atRisk: bigint; limit: bigint } // desk.risk(terms.feed), USDG base units
}

/** A certified model (Stylus pricer): its version and where it answers. */
export type ModelView = {
  address: Address
  weightsHash: Hex
  /** certifiedRange(0..9), one [min, max] per PricerInputs field, in PricerInputs order. */
  certifiedRange: readonly (readonly [bigint, bigint])[]
}

export type MarketData = {
  source: 'fixtures' | 'chain'
  desk: Address
  usdg: Address
  series: SeriesView[] // active listings only
  models: Record<Address, ModelView>
  queuedShares: bigint // desk.queuedShares(): > 0 stops trades that add to the Desk's position
  fees: { maxFeeBps: number; maxCoverFeeBps: number; backstopShareBps: number }
  now: number // unix seconds the data was read at
}

/** The four Desk trades. */
export type TradeKind = 'buy' | 'sell' | 'buyCover' | 'sellCover'

export type TradeStep = 'idle' | 'quoting' | 'approving' | 'trading' | 'done' | 'failed'

export type TradeState = {
  step: TradeStep
  hash?: Hex // the trade transaction, once sent
  refusal?: Refusal // decoded revert, or { error: 'UserRejected' } when the wallet declined
}

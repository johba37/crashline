// Pure helpers for the dashboard: formatting, and the Desk's arithmetic as docs/interfaces.md
// states it, so the numbers on screen are the ones the contracts compute.
import { type MarketData, Phase, type SeriesView, type TradeKind } from './types.ts'

export const WEEK = 604800
export const UNIT = 1_000_000n // 1 NOTE / WRITER / USDG in base units (6 decimals)
const FEED_UNIT = 100_000_000n // feed answers have 8 decimals

const locale = undefined // the reader's locale

/** 640 -> "6.40%" */
export const pct = (bps: number, digits = 2) =>
  `${(bps / 100).toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`

/** Signed fraction -> "−26.0%", with a real minus sign. */
export const signedPct = (fraction: number, digits = 1) => {
  const s = Math.abs(fraction * 100).toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits })
  return fraction < 0 ? `−${s}%` : `+${s}%`
}

/** USDG base units -> "37,600.00" */
export const usdg = (base: bigint, digits = 2) =>
  (Number(base) / 1e6).toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits })

/** USDG base units -> "13,314", rounded down: an "at most" amount must itself go through. */
export const wholeUsdg = (base: bigint) => usdg(base - (base % UNIT), 0)

/** Feed price (8 decimals) -> "$251.30" (the narrow symbol: other locales would print "US$251.30") */
export const usd = (price: bigint) =>
  (Number(price) / 1e8).toLocaleString(locale, { style: 'currency', currency: 'USD', currencyDisplay: 'narrowSymbol' })

/** A barrier in bps of the initial fixing, as a feed price. */
export const level = (initialFixing: bigint, bps: number) => (initialFixing * BigInt(bps)) / 10_000n

/** How far the price must move from spot to reach `target`, as a signed fraction. */
export const moveTo = (spot: bigint, target: bigint) => Number(target - spot) / Number(spot)

export const shortHash = (hex: string) => `${hex.slice(0, 6)}…${hex.slice(-4)}`

export const date = (ts: number) =>
  new Date(ts * 1000).toLocaleDateString(locale, { month: 'short', day: 'numeric', year: 'numeric' })

export const dateTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString(locale, {
    weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
  })

/** "in 3 days", "in 16 hours", "in 40 minutes" */
export const fromNow = (ts: number, now: number) => {
  const s = ts - now
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  if (Math.abs(s) >= 2 * 86400) return rtf.format(Math.round(s / 86400), 'day')
  if (Math.abs(s) >= 2 * 3600) return rtf.format(Math.round(s / 3600), 'hour')
  return rtf.format(Math.round(s / 60), 'minute')
}

/** A length of time as people say it: whole weeks up to two months, then months. 14 weeks -> "About 3 months" */
export const span = (secs: number) => {
  const weeks = Math.max(1, Math.round(secs / WEEK))
  if (weeks <= 8) return weeks === 1 ? '1 week' : `${weeks} weeks`
  return `About ${Math.round(secs / (30.44 * 86400))} months`
}

/** "1,250.5" -> base units, or null when it isn't a positive amount with at most 6 decimals. */
export const parseAmount = (text: string): bigint | null => {
  const t = text.replace(/[\s,]/g, '')
  if (!/^\d*\.?\d{0,6}$/.test(t) || t === '' || t === '.') return null
  const [whole, frac = ''] = t.split('.')
  const base = BigInt(whole || '0') * UNIT + BigInt(frac.padEnd(6, '0'))
  return base > 0n ? base : null
}

export const maxBps = (s: SeriesView) => Number(s.maxPayoutPerNote / 100n)

const ceilDiv = (a: bigint, b: bigint) => (a + b - 1n) / b

/**
 * Cost of a buy or proceeds of a sell, rounded as the Desk rounds (IDesk / IDeskCover):
 * NOTE fee is a share of the notional, cover fee a share of the premium; a sell's fee is
 * capped at its gross proceeds.
 */
export function tradeAmounts(kind: TradeKind, amount: bigint, priceBps: number, feeBps: number) {
  const buying = kind === 'buy' || kind === 'buyCover'
  const gross = buying ? ceilDiv(amount * BigInt(priceBps), 10_000n) : (amount * BigInt(priceBps)) / 10_000n
  const feeBase = kind === 'buy' || kind === 'sell' ? amount : gross
  let fee = ceilDiv(feeBase * BigInt(feeBps), 10_000n)
  if (!buying && fee > gross) fee = gross
  return { gross, fee, total: buying ? gross + fee : gross - fee }
}

/**
 * How much of a trade adds to the Desk's own position, which quotes don't check
 * (docs/interfaces.md): the part the Desk can't serve from inventory or pair off.
 */
export function addsToDesk(s: SeriesView, kind: TradeKind, amount: bigint) {
  const excess = (have: bigint) => (amount > have ? amount - have : 0n)
  switch (kind) {
    case 'buy': return excess(s.noteInventory) // mints pairs, keeps WRITER
    case 'sell': return excess(s.listing.soldNotional) // NOTE it can't pair with WRITER it holds
    case 'buyCover': return excess(s.listing.soldNotional) // sells the WRITER it holds first, then mints pairs and keeps NOTE
    case 'sellCover': return excess(s.noteInventory) // WRITER it can't pair with its NOTE
  }
}

/** What one NOTE and one WRITER the Desk holds can still lose, as its risk budget counts them (Desk._position). */
function unitRisk(s: SeriesView) {
  const certain = s.state.phase === Phase.Settled || (s.state.phase === Phase.Live && !s.state.knockedIn && observationsLeft(s) === 0)
  if (certain) return { note: 0n, writer: 0n }
  if (!s.mid.ok) return { note: UNIT, writer: UNIT } // no price from the model: the most either can lose
  const max = s.maxPayoutPerNote
  const quote = BigInt(s.mid.value.priceBps) * 100n
  const mark = quote < max ? quote : max
  const income = max - UNIT // a NOTE is paid this in any case
  return { note: mark > income ? mark - income : 0n, writer: max - mark }
}

/** What the Desk holds of the leg a trade is served from first: its NOTE for buy and sellCover, else its WRITER. */
const heldFor = (s: SeriesView, kind: TradeKind) => (kind === 'buy' || kind === 'sellCover' ? s.noteInventory : s.listing.soldNotional)

/**
 * The largest amount of a trade the risk budget of the series' stock lets through, or null when
 * it sets no limit. What the Desk already holds (`have`) is traded without a check and takes
 * that leg's risk off; only the rest adds to the other leg and has to fit. `setAside` is the USDG
 * the redemption queue is paid first, which leaves the vault and so lowers the limit.
 * The Desk rounds on its total holdings, so what it counts at risk can be a couple of base units
 * off this. A trade also moves the limit itself by what the Desk earns on it (the spread and its
 * part of the fee, so nearly always up); that is left out, and the trade is simulated before it is sent.
 */
export function riskRoom(s: SeriesView, kind: TradeKind, setAside: bigint) {
  const risk = unitRisk(s)
  const have = heldFor(s, kind)
  const [shrinks, grows] = kind === 'buy' || kind === 'sellCover' ? [risk.note, risk.writer] : [risk.writer, risk.note]
  const limit = s.risk.limit - (setAside * BigInt(s.risk.budgetBps)) / 10_000n
  const room = (limit - s.risk.atRisk) * UNIT + have * shrinks // in millionths of a base unit
  if (room < 0n) return have // the stock is over its budget: nothing can be added
  return grows === 0n ? null : have + room / grows
}

/**
 * The largest amount of a trade the Desk has the USDG for (`free`: what it can use, less what
 * the queue is paid first), or null when that sets no limit or the trade has no price. What it
 * holds costs it nothing. Beyond that a buy has it lock a new pair's full collateral against the
 * price it is paid, and a sale has it pay the price for a leg it can't redeem; a pair it can
 * redeem brings the collateral back. One base unit is kept back for the Desk's rounding, and its
 * part of the fee is left out: both err on the safe side.
 */
export function usdgRoom(s: SeriesView, kind: TradeKind, free: bigint) {
  const quote = { buy: s.noteAsk, sell: s.noteBid, buyCover: s.coverAsk, sellCover: s.coverBid }[kind]
  if (!quote.ok) return null
  const have = heldFor(s, kind)
  const price = BigInt(quote.value) * 100n // base units per unit, like maxPayoutPerNote
  const each = kind === 'buy' || kind === 'buyCover' ? s.maxPayoutPerNote - price : price // its own USDG per unit, were nothing held
  if (each === 0n) return null
  const most = ((free - 1n) * UNIT + have * s.maxPayoutPerNote) / each
  return most > have ? most : have
}

/** The largest amount of a trade the Desk's limits let through, or null when they set none: the smaller of the two rooms above. */
export function roomFor(s: SeriesView, kind: TradeKind, market: Pick<MarketData, 'idle' | 'queue'>) {
  const byRisk = riskRoom(s, kind, market.queue.setAside)
  const byUsdg = usdgRoom(s, kind, market.idle - market.queue.setAside)
  return byRisk === null || (byUsdg !== null && byUsdg < byRisk) ? byUsdg : byRisk
}

/** NOTE the Desk can sell: its inventory plus what its WRITER cap still lets it mint. */
export const noteOnOffer = (s: SeriesView) =>
  s.noteInventory + (s.listing.capNotional > s.listing.soldNotional ? s.listing.capNotional - s.listing.soldNotional : 0n)

/**
 * Sold out: the Desk has a price but none left to sell (its selling quote is refused for that
 * alone), and it still buys back. `earn` says which side is asked about: NOTE, else cover.
 */
export const soldOut = (s: SeriesView, earn: boolean) => {
  const [ask, bid] = earn ? [s.noteAsk, s.noteBid] : [s.coverAsk, s.coverBid]
  return !ask.ok && ask.refusal.error === 'CapExceeded' && bid.ok
}

export const observationsLeft = (s: SeriesView) => s.terms.observationCount - s.state.observationsDone

/** Where cover starts paying: the knock-in level, and the move from today's price to reach it. */
export function trigger(s: SeriesView) {
  const price = level(s.state.initialFixing, s.terms.kiBarrierBps)
  return { price, move: s.spot ? moveTo(s.spot, price) : null }
}

/**
 * Worked example for the order summary, from the payout rules in INoteSeries: the crash line was
 * hit at a weekly check and the stock ends at `end`. Cover pays the fall below the starting
 * price; NOTE gets the stock's share back plus every week's income.
 */
export function crashPayout(s: SeriesView, amount: bigint, end: bigint) {
  const initial = s.state.initialFixing
  const share = end < initial ? (amount * end) / initial : amount
  const income = (amount * BigInt(s.terms.couponBpsPerPeriod * (s.terms.observationCount + 1))) / 10_000n
  return { cover: amount - share, note: share + income }
}

/** What a NOTE pays at the end when nothing goes wrong: the amount plus every week's income. */
export const bestCase = (s: SeriesView, amount: bigint) => (amount * s.maxPayoutPerNote) / UNIT

export const toUnits = (base: bigint) => Number(base) / Number(UNIT)
export const fromFeed = (price: bigint) => Number(price) / Number(FEED_UNIT)

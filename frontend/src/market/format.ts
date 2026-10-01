// Pure helpers for the dashboard: formatting, and the Desk's arithmetic as docs/interfaces.md
// states it, so the numbers on screen are the ones the contracts compute.
import type { SeriesView, TradeKind } from './types.ts'

export const WEEK = 604800
export const UNIT = 1_000_000n // 1 NOTE / WRITER / USDG in base units (6 decimals)
const FEED_UNIT = 100_000_000n // feed answers have 8 decimals

const locale = undefined // the reader's locale

/** 640 -> "6.40%" */
export const pct = (bps: number, digits = 2) =>
  `${(bps / 100).toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`

/** A price in bps of the amount, as USDG per 100 USDG: 1300 -> "13.00" */
export const per100 = (bps: number) =>
  (bps / 100).toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** Signed fraction -> "−26.0%", with a real minus sign. */
export const signedPct = (fraction: number, digits = 1) => {
  const s = Math.abs(fraction * 100).toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits })
  return fraction < 0 ? `−${s}%` : `+${s}%`
}

/** USDG base units -> "37,600.00" */
export const usdg = (base: bigint, digits = 2) =>
  (Number(base) / 1e6).toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits })

/** Feed price (8 decimals) -> "$251.30" */
export const usd = (price: bigint) =>
  (Number(price) / 1e8).toLocaleString(locale, { style: 'currency', currency: 'USD' })

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
    case 'buyCover': return amount // always ends up holding NOTE
    case 'sellCover': return excess(s.noteInventory) // WRITER it can't pair with its NOTE
  }
}

/** Room left in the risk budget of the series' stock, in USDG base units. */
export const coverRoom = (s: SeriesView) => (s.risk.limit > s.risk.atRisk ? s.risk.limit - s.risk.atRisk : 0n)

/** NOTE the Desk can sell: its inventory plus what its WRITER cap still lets it mint. */
export const noteOnOffer = (s: SeriesView) =>
  s.noteInventory + (s.listing.capNotional > s.listing.soldNotional ? s.listing.capNotional - s.listing.soldNotional : 0n)

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

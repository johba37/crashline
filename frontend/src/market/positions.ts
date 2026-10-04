// The backend's per-wallet answers (docs/backend.md), as the positions list reads them.
import { UNIT } from './format.ts'
import type { Exit, Position, PricePoint, SeriesView } from './types.ts'

/** GET /accounts/{address}, reduced to what we use. Amounts are base units as decimal strings. */
type Account = {
  positions?: { series: string; note: string; writer: string; costBasis: { note: string; writer: string } }[]
  trades?: { series: string; kind: string; account: string; to: string; time: number; amount: string; usdg: string }[]
  block: number
}

/** GET /trades?account={address}: the trades the wallet made or received, newest first. */
type Trades = { trades?: Account['trades'] }

/** GET /events?account={address}&name=Redeemed: what the wallet collected from notes that ended. */
type Redeemed = { events?: { series: string; time: number; args: { caller: string; noteAmount: string; writerAmount: string } }[] }

/**
 * One position per side of a note the wallet (`owner`) holds or held, on the notes it is given.
 * Held: its amount and cost basis, from the backend. Held before and no longer: `closed`, with
 * what its buys cost. `bought` are the times of the Desk buys of that side the wallet received;
 * `exits` its sales to the Desk and what it collected (`redeemed`, at the note's payout for that
 * side). The trades come from `trades` (GET /trades, up to 500); until that answers, from the
 * account's own list, which holds only the last 50. `path` is empty: it comes from the note's history.
 */
export function fromAccount(json: unknown, series: SeriesView[], owner: string, redeemed: unknown = {}, trades?: unknown): Position[] {
  const a = json as Account
  // The backend writes addresses in lower case, the chain reader with a checksum.
  const same = (x: string, y: string) => x.toLowerCase() === y.toLowerCase()
  const sum = (xs: bigint[]) => xs.reduce((total, x) => total + x, 0n)
  const rows = a.positions ?? []
  const all = [...((trades as Trades | undefined)?.trades ?? a.trades ?? [])].sort((x, y) => x.time - y.time)
  const collects = ((redeemed as Redeemed).events ?? []).filter((e) => same(e.args.caller, owner))
  // Every note the wallet holds or traded: its holdings first, in the backend's order.
  const notes = [...new Set([...rows, ...all, ...collects].map((x) => x.series.toLowerCase()))]
  return notes.flatMap((address) => {
    const s = series.find((v) => same(v.address, address))
    if (!s) return []
    const row = rows.find((p) => same(p.series, address))
    return (['cover', 'note'] as const).flatMap((side): Position[] => {
      const cover = side === 'cover'
      const mine = all.filter((t) => same(t.series, address))
      const buys = mine.filter((t) => t.kind === (cover ? 'buyCover' : 'buy') && same(t.to, owner))
      const exits: Exit[] = [
        ...mine.filter((t) => t.kind === (cover ? 'sellCover' : 'sell') && same(t.account, owner))
          .map((t) => ({ time: t.time, kind: 'sold' as const, amount: BigInt(t.amount), usdg: BigInt(t.usdg) })),
        ...collects.filter((e) => same(e.series, address)).flatMap((e) => {
          const amount = BigInt(cover ? e.args.writerAmount : e.args.noteAmount)
          const each = cover ? s.maxPayoutPerNote - s.state.payoutPerNote : s.state.payoutPerNote
          return amount > 0n ? [{ time: e.time, kind: 'collected' as const, amount, usdg: (amount * each) / UNIT }] : []
        }),
      ].sort((x, y) => x.time - y.time)
      const bought = buys.map((t) => t.time)
      const held = row ? BigInt(cover ? row.writer : row.note) : 0n
      if (row && held > 0n) {
        return [{ series: s, side, amount: held, paid: BigInt(cover ? row.costBasis.writer : row.costBasis.note), bought, exits, closed: false, path: [] }]
      }
      if (buys.length === 0 && exits.length === 0) return []
      // The most it held at once: buys add and exits take away, in time order (a buy first within a
      // second). With no buy on record, what it let go of.
      const moves: [number, bigint][] = [...buys.map((t): [number, bigint] => [t.time, BigInt(t.amount)]), ...exits.map((e): [number, bigint] => [e.time, -e.amount])]
      let level = 0n
      let most = 0n
      for (const [, change] of moves.sort((x, y) => x[0] - y[0] || (y[1] > x[1] ? 1 : -1))) {
        level += change
        if (level > most) most = level
      }
      const amount = buys.length > 0 ? most : sum(exits.map((e) => e.amount))
      return [{ series: s, side, amount, paid: sum(buys.map((t) => BigInt(t.usdg))), bought, exits, closed: true, path: [] }]
    })
  })
}

/** Whether an /accounts answer has reached `block`: every answer says which block it was read at. */
export const reflects = (json: unknown, block: bigint) => BigInt((json as Account).block) >= block

/** When a note that has ended stopped: the weekly check it ended early at, else its end date. */
export const endedAt = (s: SeriesView) =>
  s.state.autocalled ? s.terms.strikeTime + s.state.observationsDone * s.terms.observationInterval : s.state.maturity

/**
 * GET /series/{address}/history, as the stock's path: oldest first, the last point is now (or the
 * end, when asked only up to it). A point the feed had no price for is left out. The weekly checks
 * the note recorded are on the path at their own time: a check is recorded in a block after it,
 * so for a note that has ended the points alone stop before the price it ended at. A check comes
 * in bps of the starting price (`initial`), so its price is right to a hundredth of a percent.
 */
export function fromHistory(json: unknown, initial: bigint): PricePoint[] {
  const h = json as { points?: { time: number; spot: string | null }[]; observations?: { obsTime: number; fixingBps: number }[] }
  const points = (h.points ?? []).flatMap((p) => (p.spot === null ? [] : [{ time: p.time, price: BigInt(p.spot) }]))
  const times = new Set(points.map((p) => p.time))
  const checks = (h.observations ?? [])
    .filter((o) => !times.has(o.obsTime))
    .map((o) => ({ time: o.obsTime, price: (initial * BigInt(o.fixingBps)) / 10_000n }))
  return [...points, ...checks].sort((a, b) => a.time - b.time)
}

// The backend's per-wallet answers (docs/backend.md), as the positions list reads them.
import type { Position, PricePoint, SeriesView } from './types.ts'

/** GET /accounts/{address}, reduced to what we use. Amounts are base units as decimal strings. */
type Account = {
  positions?: { series: string; note: string; writer: string; costBasis: { note: string; writer: string } }[]
  block: number
}

/**
 * One position per side the wallet holds, on the notes it is given. A row with nothing left
 * (only trade history) gives none. `path` is empty: it comes from the note's history.
 */
export function fromAccount(json: unknown, series: SeriesView[]): Position[] {
  return ((json as Account).positions ?? []).flatMap((p) => {
    // The backend writes addresses in lower case, the chain reader with a checksum.
    const s = series.find((v) => v.address.toLowerCase() === p.series.toLowerCase())
    if (!s) return []
    const sides = [
      { side: 'cover' as const, amount: BigInt(p.writer), paid: BigInt(p.costBasis.writer) },
      { side: 'note' as const, amount: BigInt(p.note), paid: BigInt(p.costBasis.note) },
    ]
    return sides.filter((x) => x.amount > 0n).map((x) => ({ series: s, ...x, path: [] }))
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

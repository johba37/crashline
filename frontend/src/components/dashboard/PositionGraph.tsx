import { date, fromFeed, trigger, usd } from '../../market/format.ts'
import { Phase, type PricePoint, type SeriesView } from '../../market/types.ts'
import InfoTip from '../Tooltip.tsx'
import Term from './Term.tsx'

// The plot, in px. Prices are to scale between two insets: the top one leaves room for the words
// above the starting price, the bottom one for the words in the band under the crash line (two
// lines of them on a phone) with the dot's label above them.
const HEIGHT = 248
const TOP = 36
const BOTTOM = 80
const APART = 20 // between two prices in the gutter
const CLEAR = 12 // kept free above and below a line, for the dot's label
const WORDS = 26 // the words above the starting price or the crash line, with their gap to the line
const NEAR = 18 // from the dot's middle to its label's, when the label sits above or below it

/** When the reader got in or out of the position: a Desk buy, a sale back to the Desk, a collect. */
export type Mark = { time: number; kind: 'bought' | 'sold' | 'collected' }
const SAID: Record<Mark['kind'], string> = { bought: 'You bought', sold: 'You sold', collected: 'You collected' }

/**
 * One position as a price graph: the stock's path since the note started, against the note's
 * starting price (it ends early from there up) and its crash line. Price runs top to bottom and
 * is to scale; time runs from the note's start to its end date, so the path stops at today and
 * what's to the right of it stays empty: a line into the future would read as a forecast.
 */
export default function PositionGraph({ s, side, path, marks, now }: {
  s: SeriesView
  side: 'cover' | 'note'
  path: PricePoint[]
  marks: Mark[] // oldest first
  now: number
}) {
  const ended = s.state.phase === Phase.Settled
  const hit = s.state.knockedIn
  const start = s.state.initialFixing
  const crash = trigger(s).price
  const last = path.length > 1 ? path[path.length - 1] : null

  const prices = last ? path.map((p) => fromFeed(p.price)) : []
  const hi = Math.max(fromFeed(start), ...prices)
  const lo = Math.min(fromFeed(crash), ...prices)
  const y = (price: bigint) => TOP + ((hi - fromFeed(price)) / (hi - lo)) * (HEIGHT - TOP - BOTTOM)
  const x = (time: number) => Math.min(1, Math.max(0, (time - s.terms.strikeTime) / (s.state.maturity - s.terms.strikeTime)))
  const yStart = y(start)
  const yCrash = y(crash)
  const xLast = last ? x(last.time) : 0
  const yLast = last ? y(last.price) : 0

  // The gutter's prices sit at their lines, but close ones would print over each other: those
  // are moved apart, evenly up and down (halfway between pushing down and pushing up).
  const labels = [
    { key: 'start', price: start, y: yStart },
    { key: 'crash', price: crash, y: yCrash },
    ...(last ? [{ key: 'last', price: last.price, y: yLast }] : []),
  ].sort((a, b) => a.y - b.y)
  const down = labels.map((l) => l.y)
  const up = [...down]
  for (let i = 1; i < down.length; i++) down[i] = Math.max(down[i], down[i - 1] + APART)
  for (let i = up.length - 2; i >= 0; i--) up[i] = Math.min(up[i], up[i + 1] - APART)

  // The dot's label sits next to the dot while there's room to its right. In the right half it
  // goes above or below the dot instead, on the side the path didn't come from (judged by the
  // price about a label's width back): left of the dot it would lie on the path. Either way it
  // keeps clear of both lines and of the words above them, moving up or down,
  // whichever is nearer.
  const tight = xLast > 1 / 2
  const before = path.findLast((p) => x(p.time) < xLast - 0.15) ?? path[0]
  const away = last && before.price > last.price ? NEAR : -NEAR
  const taken = [[yStart - CLEAR - (ended ? 0 : WORDS), yStart + CLEAR], [yCrash - CLEAR - (ended ? 0 : WORDS), yCrash + CLEAR]]
  // Never across the crash line from the dot: below it lie the band's words.
  const spots = (tight ? [yLast + away, yLast - away] : [yLast]).filter((c) => c < yCrash === yLast < yCrash)
  let yName = spots.find((c) => taken.every(([from, to]) => c <= from || c >= to)) ?? spots[0]
  for (const [i, [from, to]] of taken.entries()) {
    if (yName <= from || yName >= to) continue
    // Out to the nearer side. At the crash line, to the dot's own side: the band's words lie below it.
    yName = (i === 1 ? yLast < yCrash : yName - from < to - yName) ? from : to
  }

  // Where the reader got in and out: a ring on the path at that day's price (the last point at or
  // before it; a collect, which comes after the end, at the end). Each is named on the side the path
  // doesn't go to next or, close to today's dot, on the side away from that dot's label. A name
  // that would lie on one already placed is left out; its ring says it when hovered or tapped. A
  // ring on today's dot is drawn wider and hollow, around the dot.
  const rings = last
    ? marks.filter((m) => m.time >= s.terms.strikeTime).map((m) => {
        const t = Math.min(m.time, last.time)
        const at = path.findLast((p) => p.time <= t) ?? path[0]
        const xb = x(t)
        const yb = y(at.price)
        return { ...m, price: at.price, xb, yb, atDot: xLast - xb < 0.02 && Math.abs(yb - yLast) < 8 }
      })
    : []
  const ring = (r: (typeof rings)[number]) => `absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-ink ${r.atDot ? 'size-5' : 'size-3 bg-plot'}`
  const placed = [{ x: xLast, y: yName }]
  const named = rings.flatMap((r) => {
    const next = path.find((p) => x(p.time) > r.xb + 0.1)
    const above = xLast - r.xb < 0.2 ? yName > r.yb : !(next && next.price > r.price)
    const yl = r.yb + (above ? -NEAR : NEAR)
    if (placed.some((q) => Math.abs(q.x - r.xb) < 0.2 && Math.abs(q.y - yl) < NEAR)) return []
    placed.push({ x: r.xb, y: yl })
    return [{ ...r, yl }]
  })
  const unnamed = rings.filter((r) => !named.some((n) => n.time === r.time && n.kind === r.kind))

  const checks = Array.from({ length: s.terms.observationCount }, (_, k) => s.terms.strikeTime + (k + 1) * s.terms.observationInterval)

  return (
    // On a ground of its own, darker than the card and with a rim: one picture, apart from the text around it.
    // On a phone it reaches into the card’s padding, so the plot keeps its width.
    <div className="-mx-3 grid grid-cols-[max-content_minmax(0,1fr)] gap-y-2 rounded-md bg-plot p-3 shadow-[inset_0_0_0_1px_var(--color-line)] sm:mx-0 sm:p-4">
      {/* The price column is as wide as its longest price, so the graph starts as close to the left edge as it ends at the right one. */}
      <div aria-hidden="true" className="invisible h-0 pr-3 type-data whitespace-nowrap">
        {labels.map((l) => <span key={l.key} className="block">{usd(l.price)}</span>)}
      </div>
      <div className="relative col-start-2" style={{ height: HEIGHT }}>
        <div
          role="img"
          aria-label={`${s.symbol} since ${date(s.terms.strikeTime)}: started at ${usd(start)}${last ? `, ${ended ? 'ended at' : 'now'} ${usd(last.price)}` : ''}. Crash line at ${usd(crash)}, ${hit ? 'crossed' : 'not crossed'}.${rings.map((r) => ` ${SAID[r.kind]} on ${date(r.time)} at ${usd(r.price)}.`).join('')}`}
          className="absolute inset-0"
        >
          {/* Below the crash line: where the cover pays, or where the money is at risk. */}
          <div aria-hidden="true" className="absolute inset-x-0 bottom-0 rounded-b-md bg-accent-soft" style={{ top: yCrash }} />
          {/* The price axis. */}
          <span aria-hidden="true" className="absolute inset-y-0 left-0 w-px bg-line-strong" />
          {labels.map((l, i) => (
            <span key={l.key} className="absolute right-full mr-3 -translate-y-1/2 type-data whitespace-nowrap text-ink" style={{ top: (down[i] + up[i]) / 2 }}>
              {usd(l.price)}
            </span>
          ))}

          <div className="absolute inset-0">
            <span aria-hidden="true" className="absolute inset-x-0 border-t border-dashed border-line-strong" style={{ top: yStart }} />
            <span aria-hidden="true" className="absolute inset-x-0 border-t-2 border-accent" style={{ top: yCrash }} />
            {last && (
              <>
                <svg aria-hidden="true" viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 size-full overflow-visible text-ink">
                  <polyline
                    points={path.map((p) => `${x(p.time) * 100},${(y(p.price) / HEIGHT) * 100}`).join(' ')}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    vectorEffect="non-scaling-stroke"
                  />
                </svg>
                {named.map((r, i) => (
                  <span key={i} aria-hidden="true" className={ring(r)} style={{ left: `${r.xb * 100}%`, top: r.yb }} />
                ))}
                {named.map((r, i) => (
                  <span key={i} className="absolute inset-x-0 flex -translate-y-1/2" style={{ top: r.yl }}>
                    <span style={{ flexBasis: `${r.xb * 100}%` }} />
                    <span className="-ml-1.5 shrink-0 type-label whitespace-nowrap text-ink">{SAID[r.kind]}</span>
                  </span>
                ))}
                <span aria-hidden="true" className="absolute size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-ink" style={{ left: `${xLast * 100}%`, top: yLast }} />
                {/* The empty box before the label gives way when the label wouldn't fit after it, so the label never runs off the right edge. */}
                <span className="absolute inset-x-0 flex -translate-y-1/2" style={{ top: yName }}>
                  <span style={{ flexBasis: `${xLast * 100}%` }} />
                  <span className={`shrink-0 type-label whitespace-nowrap text-ink ${tight ? '-ml-1.5' : 'ml-3'}`}>
                    {s.symbol} {ended ? 'at the end' : 'today'}
                  </span>
                </span>
              </>
            )}
          </div>
        </div>

        {/* The rings without a name stay outside the image too, as buttons that say it: on top of today's dot, which shows through a ring around it. */}
        {unnamed.map((r, i) => (
          <span key={i} className="absolute" style={{ left: `${r.xb * 100}%`, top: r.yb }}>
            <InfoTip label={SAID[r.kind]} mark={{ name: `${SAID[r.kind]} on ${date(r.time)}`, className: `${ring(r)} top-0 left-0 cursor-pointer transition-transform duration-160 hover:scale-150 aria-expanded:scale-150 after:absolute after:-inset-2` }}>
              {SAID[r.kind]} here, on {date(r.time)}.
            </InfoTip>
          </span>
        ))}

        {/* The words on the two lines stay outside the image: they're read out as text, and the info icon can be reached. */}
        {!ended && (
          <>
            <span className="absolute right-0 flex items-center gap-1 type-label whitespace-nowrap text-ink-muted" style={{ bottom: HEIGHT - yStart + 6 }}>
              <Term t="endsEarly">Ends early</Term> from here up
            </span>
            <span className="absolute right-0 flex items-center type-label whitespace-nowrap text-ink-muted" style={{ bottom: HEIGHT - yCrash + 6 }}>
              <Term t="crashLine">Crash Line</Term>
            </span>
          </>
        )}
        {/* A note that has ended says what did happen at the line, one that runs what does or would. */}
        <p className="absolute right-2 bottom-2 left-3 type-label text-ink">
          {ended && !hit ? `${s.symbol} never closed below this line at a weekly check.` : (
            <>
              {hit ? `${s.symbol} closed below this line` : 'Below this line at a weekly check'}:{' '}
              {side === 'note' ? `your money ${ended ? 'was' : 'is'} at risk.` : hit ? `your cover ${ended ? 'was' : 'is'} switched on.` : 'your cover switches on.'}
            </>
          )}
        </p>
      </div>

      {/* The time axis: the note's start to its end date, one mark per weekly check. */}
      <div className="col-start-2">
        <div aria-hidden="true" className="relative h-3 border-t border-line-strong">
          {checks.map((time) => (
            <span key={time} className={`absolute top-0 h-1.5 w-px ${time <= now ? 'bg-ink' : 'bg-line-strong'}`} style={{ left: `${x(time) * 100}%` }} />
          ))}
          <span className="absolute top-0 right-0 h-3 w-0.5 bg-ink" />
        </div>
        {/* On a phone the two don't fit side by side: the end date takes a line of its own, so no date breaks in two. */}
        <div className="flex flex-wrap gap-x-4 type-label whitespace-nowrap text-ink-muted">
          <span>Started {date(s.terms.strikeTime)}</span>
          <span className="ml-auto">Ends {date(s.state.maturity)}</span>
        </div>
      </div>
    </div>
  )
}

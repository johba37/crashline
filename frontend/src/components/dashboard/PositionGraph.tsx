import { date, fromFeed, trigger, usd } from '../../market/format.ts'
import { Phase, type PricePoint, type SeriesView } from '../../market/types.ts'
import Term from './Term.tsx'

// The plot, in px. Prices are to scale between two insets: the top one leaves room for the words
// above the starting price, the bottom one for the words in the band under the crash line (two
// lines of them on a phone) with the dot's label above them.
const HEIGHT = 248
const TOP = 36
const BOTTOM = 80
const APART = 20 // between two prices in the gutter
const CLEAR = 12 // kept free above and below a line, for the dot's label
const WORDS = 26 // the words above the starting price, with their gap to the line
const NEAR = 18 // from the dot's middle to its label's, when the label sits above or below it

/**
 * One position as a price graph: the stock's path since the note started, against the note's
 * starting price (it ends early from there up) and its crash line. Price runs top to bottom and
 * is to scale; time runs from the note's start to its end date, so the path stops at today and
 * what's to the right of it stays empty: a line into the future would read as a forecast.
 */
export default function PositionGraph({ s, side, path, now }: {
  s: SeriesView
  side: 'cover' | 'note'
  path: PricePoint[]
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
  const spots = tight ? [yLast + away, yLast - away] : [yLast]
  let yName = spots.find((c) => taken.every(([from, to]) => c <= from || c >= to)) ?? spots[0]
  for (const [from, to] of taken) if (yName > from && yName < to) yName = yName - from < to - yName ? from : to

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
          aria-label={`${s.symbol} since ${date(s.terms.strikeTime)}: started at ${usd(start)}${last ? `, ${ended ? 'ended at' : 'now'} ${usd(last.price)}` : ''}. Crash line at ${usd(crash)}, ${hit ? 'crossed' : 'not crossed'}.`}
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
          {ended && !hit ? <>{s.symbol} never closed below this line at a <Term t="weeklyCheck" />.</> : (
            <>
              {hit ? `${s.symbol} closed below this line` : <>Below this line at a <Term t="weeklyCheck" /></>}:{' '}
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

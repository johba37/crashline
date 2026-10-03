import { useRef, type KeyboardEvent } from 'react'
import type { Address } from 'viem'
import { date, observationsLeft, pct, soldOut, trigger, usd, usdg } from '../../market/format.ts'
import type { SeriesView } from '../../market/types.ts'
import Term from './Term.tsx'

export type Goal = 'protect' | 'earn'

/** -0.263 -> "26.3%": the size of a fall. */
const fall = (move: number) => pct(Math.abs(move) * 10_000, 1)

// The diagram, in px. It's a picture of the rules, not a price scale: rows sit in fixed slots ROW
// apart and only their order follows the prices (every row prints its price), so the diagram
// keeps its height and its lines stay put when another length or another line is picked.
const ROW = 46
const HEIGHT = 336
const LOWEST = 230 // where the lowest choice's line sits
const CLEAR = 14 // kept free above and below today's label

type Row = { kind: 'start' | 'today' | 'option'; price: bigint; y: number; index: number }

/**
 * The level as a price diagram: price runs top to bottom, time left to right. Today's price is
 * a dot and each choice is a line at its crash-line price. No line is picked for the reader: once
 * one is, the diagram also shows the price at which that note ends early and shades what lies
 * below the line. The crash line is fixed at 60% of a note's starting price (the one product the
 * model is certified for), so the choices are the open notes on the stock: started at different
 * prices, their crash line sits at a different distance from today's price. Each line says what
 * it means in money (`money`: the cost of cover, or the most a NOTE can earn).
 * The rows are in price order but not to scale: what's above the lines fills the top slots, and
 * the lines stack up from a fixed lowest slot. No price path is drawn: a line into the future
 * would read as a forecast.
 */
export default function LevelPicker({
  goal, stops, selected, money, now, onSelect,
}: {
  goal: Goal
  stops: SeriesView[]
  selected?: SeriesView
  money: (s: SeriesView) => bigint | null
  now: number
  onSelect: (a: Address) => void
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const points = stops
    .map((s) => ({ s, move: trigger(s).move ?? 0, price: trigger(s).price, money: money(s) }))
    .sort((a, b) => a.move - b.move)
  const index = points.findIndex((p) => p.s.address === selected?.address)
  const current = points[index]
  // Until a line is picked, today's price and the time axis come from the first note.
  const s = current?.s ?? stops[0]
  if (!s) return null
  const hit = s.state.knockedIn
  const spot = s.spot

  // Up is a higher line (a smaller fall), down a lower one. Until a line is picked the top one
  // holds the focus, so the keys start from there.
  const onKeyDown = (e: KeyboardEvent) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -1 : 0
    if (!step) return
    e.preventDefault()
    const next = Math.min(points.length - 1, Math.max(0, (index < 0 ? points.length - 1 : index) + step))
    onSelect(points[next].s.address)
    refs.current[next]?.focus()
  }

  // Rows from the highest price down; at the same price the starting price comes first, then
  // today, then the choices. The starting price is the picked note's, so it needs a pick.
  const rows: Row[] = [
    ...(current ? [{ kind: 'start' as const, price: s.state.initialFixing, y: 0, index: -1 }] : []),
    ...(spot === null ? [] : [{ kind: 'today' as const, price: spot, y: 0, index: -1 }]),
    ...points.map((p, i) => ({ kind: 'option' as const, price: p.price, y: 0, index: i })),
  ].sort((a, b) => (a.price === b.price ? 0 : a.price > b.price ? -1 : 1))
  // What's above every line fills the slots from the top. The rest hangs from the lowest line,
  // which never moves: only a fourth line and up push it down, and the diagram grows by as much.
  const first = rows.findIndex((r) => r.kind === 'option')
  const last = rows.findLastIndex((r) => r.kind === 'option')
  const extra = Math.max(0, points.length - 3) * ROW
  // Alone up there, today keeps the second slot: the first is the starting price's, which shows
  // once a line is picked, and today shouldn't move when it does.
  const lone = first === 1 && rows[0].kind === 'today' ? 1 : 0
  rows.forEach((r, i) => { r.y = i < first ? (i + 1 + lone) * ROW : LOWEST + extra + (i - last) * ROW })
  const height = HEIGHT + extra
  const line = rows.find((r) => r.kind === 'option' && r.index === index)
  // "In between" goes halfway between the top line and the row right above it, when there's room.
  const gap = first > 0 ? rows[first].y - rows[first - 1].y : 0
  const between = line && !hit && gap >= 80 ? rows[first].y - gap / 2 : null
  // Today's price when it's already under every line: its label sits where the band's text would
  // rise from the bottom edge, so the text starts below the label instead.
  const under = rows[last + 1]

  // The weekly checks still to come, as a share of the time from now to the end date.
  const total = s.state.maturity - now
  const checks = total > 0
    ? Array.from({ length: observationsLeft(s) }, (_, k) => (s.state.nextObservation + k * s.terms.observationInterval - now) / total)
        .filter((x) => x >= 0 && x <= 1)
    : []

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        {/* The diagram has a ground of its own, darker than the card and with a rim: one picture, apart from the
            text around it. On a phone it reaches into the card’s padding, so the plot keeps its width. */}
        <div className="-mx-3 grid grid-cols-[max-content_minmax(0,1fr)] gap-y-2 rounded-md bg-plot p-3 shadow-[inset_0_0_0_1px_var(--color-line)] sm:mx-0 sm:p-4">
          {/* The price column is as wide as its longest price, so the diagram starts as close to the left edge as it ends
              at the right one. It counts every price a pick can bring in, so picking a line never moves the axis. */}
          <div aria-hidden="true" className="invisible h-0 pr-3 type-data whitespace-nowrap">
            {[...(spot === null ? [] : [spot]), ...points.flatMap((p) => [p.price, p.s.state.initialFixing])].map((price, i) => (
              <span key={i} className="block">{usd(price)}</span>
            ))}
          </div>
          <div
            role="radiogroup"
            aria-label={goal === 'protect' ? 'How far the price has to fall' : 'How far the price can fall'}
            onKeyDown={onKeyDown}
            className="relative col-start-2"
            style={{ height }}
          >
            {/* Below the chosen line: where the cover pays, or where the money is at risk. */}
            {line && (
              <>
                <div aria-hidden="true" className="absolute inset-x-0 bottom-0 rounded-b-md bg-accent-soft" style={{ top: line.y }} />
                <p className={`absolute left-3 right-2 type-label text-ink ${under ? '' : 'bottom-2'}`} style={under && { top: under.y + CLEAR }}>
                  Crash Line: {hit ? `${s.symbol} already closed below this line, so` : <>below this line at a <Term t="weeklyCheck" />,</>}{' '}
                  {goal === 'protect' ? (hit ? 'the cover is switched on.' : 'your cover switches on.') : 'your money is at risk.'}
                </p>
              </>
            )}
            {between !== null && (
              <p className="absolute left-3 -translate-y-1/2 type-label text-ink-muted" style={{ top: between }}>
                {goal === 'protect' ? 'In between: nothing happens, you stay covered.' : 'In between: you keep earning.'}
              </p>
            )}
            {/* The price axis. */}
            <span aria-hidden="true" className="absolute inset-y-0 left-0 w-px bg-line-strong" />

            {rows.map((r) => {
              const price = (
                <span className="absolute right-full bottom-0 mr-3 translate-y-1/2 type-data whitespace-nowrap text-ink">{usd(r.price)}</span>
              )
              if (r.kind === 'today') {
                return (
                  // Above the other rows and the band, and out of the pointer's way: the row under it
                  // starts at this one's line, and nothing may cover the label.
                  <div key="today" className="pointer-events-none absolute inset-x-0 z-10" style={{ top: r.y - ROW, height: ROW }}>
                    {price}
                    <span aria-hidden="true" className="absolute bottom-0 left-0 size-3 -translate-x-1/2 translate-y-1/2 rounded-full bg-ink" />
                    <span className="absolute bottom-0 left-3 translate-y-1/2 type-label text-ink">{s.symbol} today</span>
                  </div>
                )
              }
              if (r.kind === 'start') {
                return (
                  <div key="start" className="absolute inset-x-0" style={{ top: r.y - ROW, height: ROW }}>
                    {price}
                    <span aria-hidden="true" className="absolute inset-x-0 bottom-0 border-t border-dashed border-line-strong" />
                    <span className="absolute right-0 bottom-1.5 flex items-center gap-1 type-label whitespace-nowrap text-ink-muted">
                      <Term t="endsEarly">Ends early</Term> from here up
                    </span>
                  </div>
                )
              }
              const p = points[r.index]
              const on = r.index === index
              const crossed = p.s.state.knockedIn
              // Today's price is already under this line, but no weekly check has counted that yet.
              const above = p.move > 0
              // A choice without a price for this side: a sold-out note, or the picked one while its price is paused.
              const worth = p.money === null ? (soldOut(p.s, goal === 'earn') ? 'sold out' : 'no price right now') : goal === 'protect' ? `costs ${usdg(p.money)} USDG` : `up to +${usdg(p.money)} USDG`
              return (
                <button
                  key={p.s.address}
                  ref={(el) => { refs.current[r.index] = el }}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  aria-label={`${crossed ? 'Crash line already crossed' : above ? 'Crash line above today’s price' : `A ${fall(p.move)} fall from today`}, at ${usd(p.price)}, ${worth}`}
                  tabIndex={on || (index < 0 && r.index === points.length - 1) ? 0 : -1}
                  onClick={() => onSelect(p.s.address)}
                  className="group absolute inset-x-0 outline-none"
                  style={{ top: r.y - ROW, height: ROW }}
                >
                  {price}
                  <span aria-hidden="true" className={`absolute inset-x-0 bottom-0 border-t ${on ? 'border-t-2 border-accent' : 'border-line-strong group-hover:border-ink-muted'}`} />
                  <span className="absolute right-0 bottom-1.5 flex items-center gap-2 whitespace-nowrap">
                    <span className={`grid size-5 place-items-center rounded-full transition-colors duration-160 group-focus-visible:outline-2 group-focus-visible:outline-offset-2 group-focus-visible:outline-focus ${on ? 'bg-accent' : 'bg-surface-well shadow-[inset_0_0_0_1px_var(--color-line-strong)]'}`}>
                      <span className={`size-1.5 rounded-full ${on ? 'bg-on-accent' : 'bg-ink-muted'}`} />
                    </span>
                    <span className={`type-label ${on ? 'text-ink' : 'text-ink-muted'}`}>{crossed ? 'Already crossed' : above ? 'Above today' : `−${fall(p.move)} fall`}</span>
                    <span className="type-label text-ink-muted">{worth}</span>
                  </span>
                </button>
              )
            })}
          </div>

          {/* The time axis: today to the end date, one mark per weekly check still to come. */}
          <div className="col-start-2">
            <div aria-hidden="true" className="relative h-3 border-t border-line-strong">
              {checks.map((x) => (
                <span key={x} className="absolute top-0 h-1.5 w-px bg-line-strong" style={{ left: `${x * 100}%` }} />
              ))}
              <span className="absolute top-0 right-0 h-3 w-0.5 bg-ink" />
            </div>
            <div className="flex justify-between gap-4 type-label text-ink-muted">
              <span>Today</span>
              <span className="text-right">Ends {date(s.state.maturity)}</span>
            </div>
          </div>
        </div>
        <p className="type-label text-ink-muted">
          {checks.length > 0 && <>Each mark on the bottom line is a <Term t="weeklyCheck" />: only the closing prices on those days count. </>}
          {points.length > 1
            ? (goal === 'protect'
              ? 'Pick a line: a lower one only counts a deeper crash, and costs less.'
              : 'Pick a line: a lower one is safer, and earns less.')
            : !current && 'Pick the line to go on.'}
        </p>
      </div>

      {current && (hit ? (
        <p className="type-body text-ink">
          {s.symbol} already closed below this note’s <Term t="crashLine" /> ({usd(current.price)}) at a <Term t="weeklyCheck" />.{' '}
          {goal === 'protect'
            ? `So this cover is already switched on: it pays if ${s.symbol} is still below ${usd(s.state.initialFixing)} on ${date(s.state.maturity)}. That’s why it costs more.`
            : `So your money follows ${s.symbol} down unless it’s back at ${usd(s.state.initialFixing)} by ${date(s.state.maturity)}.`}
        </p>
      ) : spot && current.move > 0 ? (
        // Already under the line between two checks: nothing has counted yet, the next check decides.
        <p className="type-body text-ink">
          {s.symbol} is at {usd(spot)} today, already below this note’s <Term t="crashLine" /> ({usd(current.price)}). That only counts at a{' '}
          <Term t="weeklyCheck" />: if {s.symbol} still closes below the line at the next check, on {date(s.state.nextObservation)},{' '}
          {goal === 'protect' ? 'your cover switches on.' : 'your money is at risk.'}
        </p>
      ) : (
        <p className="type-body text-ink">
          {goal === 'protect' ? 'Your cover switches on' : 'Your money is only at risk'} if {s.symbol} closes below{' '}
          <strong className="font-semibold">{usd(current.price)}</strong> at a <Term t="weeklyCheck" />. That’s{' '}
          {fall(current.move)} below today’s {spot ? usd(spot) : 'price'}, and it’s called the <Term t="crashLine" />.
          {goal === 'protect' && ' A smaller fall pays nothing: this insures against a crash, not against every dip.'}
        </p>
      ))}
    </div>
  )
}

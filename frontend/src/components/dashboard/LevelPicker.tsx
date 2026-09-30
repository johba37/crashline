import { useRef, type KeyboardEvent } from 'react'
import type { Address } from 'viem'
import { date, observationsLeft, per100, signedPct, trigger, usd } from '../../market/format.ts'
import type { SeriesView } from '../../market/types.ts'
import Term from './Term.tsx'

export type Goal = 'protect' | 'earn'

/** -0.26 -> "26%": the size of a fall. */
const fall = (move: number) => `${Math.abs(Math.round(move * 100))}%`

/**
 * A slider with fixed points. The crash line is fixed at 60% of a note's starting price (the one
 * product the model is certified for), so the points are the open notes on the stock: started at
 * different prices, their crash line sits at a different distance from today's price.
 */
export default function LevelPicker({
  goal, stops, selected, onSelect,
}: { goal: Goal; stops: SeriesView[]; selected?: SeriesView; onSelect: (a: Address) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const points = stops
    .map((s) => ({ s, move: trigger(s).move ?? 0, price: goal === 'protect' ? s.coverAsk : s.noteAsk }))
    .sort((a, b) => a.move - b.move)
  const index = points.findIndex((p) => p.s.address === selected?.address)
  const current = points[index]

  const onKeyDown = (e: KeyboardEvent) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -1 : 0
    if (!step) return
    e.preventDefault()
    const next = Math.min(points.length - 1, Math.max(0, Math.max(index, 0) + step))
    onSelect(points[next].s.address)
    refs.current[next]?.focus()
  }

  const priceLabel = goal === 'protect' ? 'cost per 100 covered' : 'cost per 100'

  return (
    <div className="flex flex-col gap-5">
      {points.length > 1 && (
        <div className="flex flex-col gap-3">
          <div role="radiogroup" aria-label={goal === 'protect' ? 'Size of the crash to cover' : 'How far the stock can fall'} onKeyDown={onKeyDown} className="relative">
            <span aria-hidden="true" className="absolute inset-x-8 top-4 h-0.5 bg-line" />
            <span
              aria-hidden="true"
              className="absolute left-8 top-4 h-0.5 bg-accent transition-[width] duration-(--duration-spring-smooth) ease-spring-smooth"
              style={{ width: index < 0 ? 0 : `calc((100% - 4rem) * ${index / (points.length - 1)})` }}
            />
            <div className="relative flex justify-between">
              {points.map((p, i) => {
                const on = i === index
                return (
                  <button
                    key={p.s.address}
                    ref={(el) => { refs.current[i] = el }}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    aria-label={`A ${fall(p.move)} fall from today, ${p.price.ok ? per100(p.price.value) : 'no'} USDG ${priceLabel}`}
                    tabIndex={on || (index < 0 && i === 0) ? 0 : -1}
                    onClick={() => onSelect(p.s.address)}
                    className="group flex w-16 flex-col items-center gap-1.5 outline-none"
                  >
                    <span className={`grid size-8 place-items-center rounded-full transition-colors duration-160 group-focus-visible:outline-2 group-focus-visible:outline-offset-2 group-focus-visible:outline-focus ${on ? 'bg-accent' : 'bg-surface-well shadow-[inset_0_0_0_1px_var(--color-line-strong)]'}`}>
                      <span className={`size-2 rounded-full ${on ? 'bg-on-accent' : 'bg-ink-muted'}`} />
                    </span>
                    <span className={`type-data ${on ? 'text-ink' : 'text-ink-muted'}`}>{signedPct(p.move, 0)}</span>
                    <span className="type-caption text-ink-muted">{p.price.ok ? per100(p.price.value) : '—'}</span>
                  </button>
                )
              })}
            </div>
          </div>
          <div className="flex justify-between gap-4 type-caption text-ink-muted">
            <span>{goal === 'protect' ? 'Only a deep crash, cheaper' : 'Safer'}</span>
            <span className="text-right">{goal === 'protect' ? 'A smaller fall, pricier' : 'Riskier, cheaper'}</span>
          </div>
        </div>
      )}

      {current?.s.state.knockedIn && (
        <p className="type-body text-ink">
          {current.s.symbol} already fell below this note’s <Term t="crashLine" /> ({usd(trigger(current.s).price)}) at a{' '}
          <Term t="weeklyCheck" />.{' '}
          {goal === 'protect'
            ? `So this cover is already switched on: it pays if ${current.s.symbol} ends below ${usd(current.s.state.initialFixing)}.`
            : `So your money follows ${current.s.symbol} down unless it’s back at ${usd(current.s.state.initialFixing)} by the end.`}{' '}
          The note ends on {date(current.s.state.maturity)} at the latest.
        </p>
      )}
      {current && !current.s.state.knockedIn && (
        <p className="type-body text-ink">
          {goal === 'protect' ? 'Your cover switches on' : 'Your money is only at risk'} if {current.s.symbol} is below{' '}
          <strong className="font-semibold">{usd(trigger(current.s).price)}</strong> at a <Term t="weeklyCheck" />:{' '}
          a {fall(current.move)} fall from today’s {current.s.spot ? usd(current.s.spot) : 'price'}. That’s its{' '}
          <Term t="crashLine" />. The note ends on {date(current.s.state.maturity)} at the latest, after{' '}
          {observationsLeft(current.s)} more checks.
        </p>
      )}
    </div>
  )
}

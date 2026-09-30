import { date, dateTime, fromNow, WEEK } from '../../market/format.ts'
import type { SeriesView } from '../../market/types.ts'
import Term from './Term.tsx'

/** The note's weekly checks, then its end one week after the last. */
export default function ObservationTimeline({ s, now }: { s: SeriesView; now: number }) {
  const { observationCount: count, strikeTime } = s.terms
  const done = s.state.observationsDone
  const checkTime = (i: number) => strikeTime + i * WEEK
  return (
    <div className="flex flex-col gap-3">
      <p className="type-label text-ink"><Term t="weeklyCheck">Weekly checks</Term>: {done} of {count} done</p>
      <div className="overflow-x-auto pb-1">
        <div aria-hidden="true" className="relative flex min-w-[20rem] items-center justify-between py-2">
          <span className="absolute inset-x-0 top-1/2 h-0.5 -translate-y-1/2 bg-line" />
          {Array.from({ length: count }, (_, k) => {
            const i = k + 1
            const cls = i <= done
              ? 'size-2 bg-ink'
              : i === done + 1
                ? 'size-3 border-2 border-accent bg-surface-well'
                : 'size-2 border border-line-strong bg-surface-well'
            return <span key={i} className={`relative rounded-full ${cls}`} />
          })}
          <span className="relative size-3 rounded-[2px] border border-line-strong bg-surface-well" />
        </div>
      </div>
      <div className="flex flex-wrap justify-between gap-x-6 gap-y-1 type-caption text-ink-muted">
        <span>Next check: {dateTime(s.state.nextObservation)} ({fromNow(s.state.nextObservation, now)})</span>
        <span>Ends {date(s.state.maturity)} at the latest</span>
      </div>
      <ol aria-label="Weekly checks" className="sr-only">
        {Array.from({ length: count }, (_, k) => (
          <li key={k}>
            Check {k + 1}: {date(checkTime(k + 1))}, {k + 1 <= done ? 'done' : k + 1 === done + 1 ? 'next' : 'to come'}
          </li>
        ))}
        <li>End: {date(s.state.maturity)}</li>
      </ol>
    </div>
  )
}

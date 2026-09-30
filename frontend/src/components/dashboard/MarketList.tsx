import { Link, useSearchParams } from 'react-router'
import { date, observationsLeft, per100, signedPct, trigger, usd } from '../../market/format.ts'
import type { Refusable, SeriesView } from '../../market/types.ts'
import StatusChip from './StatusChip.tsx'
import { seriesStatus } from './status.ts'
import Term from './Term.tsx'

const COLUMNS = 'md:grid-cols-[1.4fr_0.8fr_1.2fr_1fr_1fr_12rem]'

function Price({ quote, caption }: { quote: Refusable<number>; caption: string }) {
  return (
    <div className="flex flex-col md:items-end">
      {quote.ok
        ? <span className="type-data text-ink">{per100(quote.value)}</span>
        : <span className="type-data text-ink-faint" aria-label="No price">—</span>}
      <span className="type-caption text-ink-muted">{caption}</span>
    </div>
  )
}

/** Every open note, paused ones included with the reason. Choosing a row makes it the note above. */
export default function MarketList({ series, selected }: { series: SeriesView[]; selected?: string }) {
  const [params] = useSearchParams()
  const href = (address: string) => {
    const next = new URLSearchParams(params)
    next.set('series', address)
    return `?${next}`
  }
  return (
    <div className="-m-5 overflow-hidden">
      <div className={`hidden gap-4 border-b border-line px-5 py-3 type-caption text-ink-muted md:grid ${COLUMNS}`}>
        <span>Note</span>
        <span>Checks left</span>
        <span className="inline-flex items-center gap-1"><Term t="crashLine">Crash line</Term></span>
        <span className="text-right">Earn: cost per 100</span>
        <span className="text-right">Protect: cost per 100</span>
        <span className="text-right">Status</span>
      </div>
      <ul className="divide-y divide-line">
        {series.map((s) => {
          const t = trigger(s)
          const current = s.address === selected
          return (
            <li
              key={s.address}
              className={`relative grid grid-cols-2 items-center gap-x-4 gap-y-3 px-5 py-4 transition-colors duration-160 hover:bg-surface-overlay focus-within:bg-surface-overlay ${COLUMNS} ${current ? 'bg-surface-overlay' : ''}`}
            >
              <div className="flex flex-col">
                <Link
                  to={href(s.address)}
                  preventScrollReset
                  aria-current={current ? 'true' : undefined}
                  className="type-body text-ink outline-none after:absolute after:inset-0 focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-focus"
                >
                  {s.symbol}, started {date(s.terms.strikeTime)}
                </Link>
                <span className="type-caption text-ink-muted">at {usd(s.state.initialFixing)}</span>
              </div>
              <div className="flex justify-end md:hidden">
                <StatusChip status={seriesStatus(s)} />
              </div>
              <span className="hidden type-data text-ink md:block">{observationsLeft(s)} of {s.terms.observationCount}</span>
              <div className="hidden flex-col md:flex">
                <span className="type-data text-ink">{usd(t.price)}</span>
                <span className="type-caption text-ink-muted">{t.move === null ? '' : `${signedPct(t.move, 0)} from today`}</span>
              </div>
              <Price quote={s.noteAsk} caption="Earn" />
              <Price quote={s.coverAsk} caption="Protect" />
              <div className="hidden justify-end md:flex">
                <StatusChip status={seriesStatus(s)} />
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

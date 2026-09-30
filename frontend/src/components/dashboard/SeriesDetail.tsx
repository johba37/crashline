import type { ReactNode } from 'react'
import { level, pct, per100, signedPct, trigger, usd } from '../../market/format.ts'
import type { Refusable, SeriesView } from '../../market/types.ts'
import ObservationTimeline from './ObservationTimeline.tsx'
import Term from './Term.tsx'

const show = (q: Refusable<number>) => (q.ok ? per100(q.value) : '—')

function Side({ title, buy, sell, fair }: { title: string; buy: Refusable<number>; sell: Refusable<number>; fair: string }) {
  return (
    <div className="flex flex-col gap-3 rounded-md bg-surface-well p-4">
      <h4 className="type-label text-ink">{title}</h4>
      <dl className="grid grid-cols-3 gap-3">
        <div>
          <dt className="type-caption text-ink-muted">You buy at</dt>
          <dd className="type-data text-ink">{show(buy)}</dd>
        </div>
        <div>
          <dt className="type-caption text-ink-muted">You sell at</dt>
          <dd className="type-data text-ink">{show(sell)}</dd>
        </div>
        <div>
          <dt className="type-caption text-ink-muted">Fair price</dt>
          <dd className="type-data text-ink-muted">{fair}</dd>
        </div>
      </dl>
    </div>
  )
}

/** Both sides of the chosen note at the Desk's two prices, per 100 USDG, around the model's fair price. */
export function PriceDetails({ s }: { s: SeriesView }) {
  const max = Number(s.maxPayoutPerNote / 100n)
  const fairNote = s.mid.ok ? per100(s.mid.value.priceBps) : '—'
  const fairCover = s.mid.ok ? per100(max - s.mid.value.priceBps) : '—'
  return (
    <div className="flex flex-col gap-4">
      <p className="type-body text-ink-muted">
        The <Term t="model" /> works out a <Term t="fairPrice" />. The <Term t="desk" /> sells a little above it and buys a
        little below it; that gap is the <Term t="spread" />. All prices are in USDG per 100 USDG of <Term t="amount" />.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Side title="Protect: cover" buy={s.coverAsk} sell={s.coverBid} fair={fairCover} />
        <Side title="Earn: NOTE" buy={s.noteAsk} sell={s.noteBid} fair={fairNote} />
      </div>
      <p className="type-body text-ink-muted">
        Together, a NOTE and its cover always pay {per100(max)} per 100 when the note ends, because that money is locked in the
        day it’s made (<Term t="fullyBacked" />). That’s why their fair prices add up to {per100(max)}.
      </p>
    </div>
  )
}

/** The chosen note's key prices in dollars and its weekly checks. */
export function Calendar({ s, now }: { s: SeriesView; now: number }) {
  const t = trigger(s)
  const endsEarly = level(s.state.initialFixing, s.terms.acBarrierBps)
  const fromToday = (price: bigint) => (s.spot ? `${signedPct((Number(price) - Number(s.spot)) / Number(s.spot), 0)} from today` : undefined)
  const facts: [ReactNode, string, string?][] = [
    [`${s.symbol} today`, s.spot ? usd(s.spot) : '—'],
    [<Term key="s" t="startingPrice">Starting price</Term>, usd(s.state.initialFixing)],
    [<Term key="c" t="crashLine">Crash line</Term>, usd(t.price), fromToday(t.price)],
    [<Term key="e" t="endsEarly">Ends early at</Term>, usd(endsEarly), fromToday(endsEarly)],
    ['Weekly income', `${pct(s.terms.couponBpsPerPeriod)} of the amount`, `about ${Math.round((s.terms.couponBpsPerPeriod * 52) / 100)}% a year`],
    ['Crash line hit', s.state.knockedIn ? 'Yes' : 'Not yet'],
  ]
  return (
    <div className="flex flex-col gap-5">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
        {facts.map(([label, value, note], i) => (
          <div key={i}>
            <dt className="type-caption text-ink-muted">{label}</dt>
            <dd className="type-data text-ink">{value}</dd>
            {note && <dd className="type-caption text-ink-muted">{note}</dd>}
          </div>
        ))}
      </dl>
      <ObservationTimeline s={s} now={now} />
    </div>
  )
}

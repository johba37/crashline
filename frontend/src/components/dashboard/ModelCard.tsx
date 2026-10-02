import { Copy, SealCheck, ShieldWarning } from '@phosphor-icons/react'
import { pct, shortHash } from '../../market/format.ts'
import type { ModelView, PricerInputs, SeriesView } from '../../market/types.ts'
import Term from './Term.tsx'

const days = (secs: number) => `${(secs / 86400).toFixed(1)} days`

// PricerInputs in order (certifiedRange field index = position), in plain words.
const FIELDS: [keyof PricerInputs, string, (v: number) => string][] = [
  ['spotBpsOfInitial', 'Price today, against the start', (v) => pct(v, 1)],
  ['distToKnockInBps', 'Room above the crash line', (v) => pct(v, 1)],
  ['volBpsAnnual', 'How much the price swings (volatility)', (v) => `${pct(v, 0)} a year`],
  ['kiBarrierBps', 'Crash line', (v) => `${pct(v, 0)} of the start`],
  ['acBarrierBps', 'Ends early at', (v) => `${pct(v, 0)} of the start`],
  ['couponBpsPerPeriod', 'Weekly income', (v) => pct(v)],
  ['timeToMaturitySecs', 'Time until the end', days],
  ['timeToNextObsSecs', 'Time until the next check', days],
  ['observationsRemaining', 'Weekly checks left', String],
  ['flags', 'Crash line hit', (v) => (v & 1 ? 'Yes' : 'No')],
]

/** Which model priced the note, what it's built for, and exactly what it saw. */
export default function ModelCard({ s, model }: { s: SeriesView; model?: ModelView }) {
  const range = model?.certifiedRange ?? []
  const pin = (i: number, show: (v: number) => string) => (range[i] ? show(Number(range[i][0])) : '…')
  return (
    <div className="flex flex-col gap-5">
      <p className="type-body text-ink-muted">
        The price comes from a small AI <Term t="model" /> that runs on the blockchain. It was built for one kind of note:
        a crash line at {pin(3, (v) => pct(v, 0))} of the starting price, ending early at {pin(4, (v) => pct(v, 0))}, and{' '}
        {pin(5, (v) => pct(v))} income a week. It gives a price while the coin or stock is between{' '}
        {range[0] ? `${pct(Number(range[0][0]), 0)} and ${pct(Number(range[0][1]), 0)}` : '…'} of its starting price. Close to a
        line on a check day, it <Term t="refuses" /> instead of guessing.
      </p>

      {model && (
        <div className="flex flex-wrap items-center gap-2 type-body text-ink-muted">
          <span className="inline-flex items-center gap-1">This version’s <Term t="fingerprint" />:</span>
          <button
            type="button"
            title={model.weightsHash}
            onClick={() => navigator.clipboard?.writeText(model.weightsHash)}
            className="inline-flex h-8 items-center gap-2 rounded-full bg-surface-well px-3 type-code text-ink transition-colors duration-160 hover:bg-surface-overlay"
          >
            {shortHash(model.weightsHash)} <Copy size={14} weight="bold" aria-hidden="true" />
            <span className="sr-only">Copy the full fingerprint</span>
          </button>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <h4 className="type-label text-ink">What the model saw for this price</h4>
        {s.inputs.ok ? (
          <dl className="divide-y divide-line rounded-md bg-surface-well px-4">
            {FIELDS.map(([key, label, show], i) => {
              const v = s.inputs.ok ? s.inputs.value[key] : 0
              const inRange = range[i] ? BigInt(v) >= range[i][0] && BigInt(v) <= range[i][1] : true
              const Mark = inRange ? SealCheck : ShieldWarning
              return (
                <div key={key} className="flex items-center justify-between gap-4 py-2">
                  <dt className="type-caption text-ink-muted">{label}</dt>
                  <dd className="flex items-center gap-2 type-data text-ink">
                    {show(v)}
                    <Mark size={16} weight="bold" className={inRange ? 'text-go' : 'text-hold'} aria-label={inRange ? 'within what the model knows' : 'outside what the model knows'} />
                  </dd>
                </div>
              )
            })}
          </dl>
        ) : (
          <p className="type-body text-ink-muted">What the model saw can’t be read right now.</p>
        )}
      </div>
    </div>
  )
}

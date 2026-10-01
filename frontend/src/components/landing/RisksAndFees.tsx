import { Info } from '@phosphor-icons/react'
import ExternalLink from './ExternalLink.tsx'
import { GITHUB } from './links.ts'
import Section from './Section.tsx'

// Sources: contracts/src/interfaces/INoteSeries.sol (payouts; Earn never pays below 0, so a buyer
// loses at most what they paid, docs/pitch.md "no leverage"), docs/k3-vol-input.md (38.0 bps on the clean sets), and
// docs/interfaces.md + plans/max-frontend-submission.md "Ground rules" (MAX_FEE_BPS 200 of the
// amount on NOTE trades, MAX_COVER_FEE_BPS 1000 of the premium on cover trades, BACKSTOP_SHARE_BPS
// 5000; the spread stays in the vault). Testnet, staged feed, no outside audit, not financial
// advice: docs/risk.md (PR #2) and the plan.

const RISKS = [
  {
    title: 'Earn can lose money in a crash',
    text: 'If TSLA is below the crash line at a weekly check and ends below its starting price, Earn gets back only the share of value TSLA kept, plus the weekly income. You can lose most of what you paid, but never more.',
  },
  {
    title: 'Protect pays nothing without a crash',
    text: 'Like any insurance: if no crash comes, cover pays nothing at the end. A dip between two weekly checks doesn’t count.',
  },
  {
    title: 'The model can be wrong',
    text: 'Where it was tested, it was at most 0.38% off the simulation, and the simulation itself can be wrong about TSLA. A wrong price changes what you trade at, never what a note pays: payouts follow the weekly checks, and every payout is fully backed.',
  },
  {
    title: 'Trading pauses at times',
    text: 'The Desk gives no price on weekends, shortly before each weekly check, or where the model refuses. Selling early then has to wait. Payouts don’t.',
  },
]

const FEE_CAPS = [
  { label: 'Fee cap on Earn', value: '2%', of: 'of the amount' },
  { label: 'Fee cap on Protect', value: '10%', of: 'of the premium' },
]

export default function RisksAndFees() {
  return (
    <Section id="risks" title="Risks and fees" intro="Both sides can lose money. Here is how, and what you pay.">
      <dl className="mt-10 border-b border-line">
        {RISKS.map((risk) => (
          <div key={risk.title} className="grid gap-2 border-t border-line py-6 md:grid-cols-12 md:gap-8">
            <dt className="type-heading text-ink md:col-span-4">{risk.title}</dt>
            <dd className="max-w-prose type-body text-ink-muted md:col-span-8">{risk.text}</dd>
          </div>
        ))}
      </dl>

      <div className="panel mt-10 rounded-lg p-5 sm:p-6 md:grid md:grid-cols-12 md:gap-8">
        <div className="md:col-span-5">
          <h3 className="type-heading text-ink">Fees</h3>
          <dl className="mt-4 grid grid-cols-2 gap-4">
            {FEE_CAPS.map((cap) => (
              <div key={cap.label}>
                <dt className="type-label text-ink-muted">{cap.label}</dt>
                <dd className="mt-1">
                  <span className="block type-readout text-ink">{cap.value}</span>
                  <span className="block type-caption text-ink-muted">{cap.of}</span>
                </dd>
              </div>
            ))}
          </dl>
        </div>
        <p className="mt-6 max-w-prose type-body text-ink-muted md:col-span-7 md:mt-0">
          Apps that sell through the Desk can add a fee, and the contract caps it. This app shows the fee before you buy.
          Half of every fee stays in the Desk as a safety buffer. The Desk can also sell a little above the model’s price
          and buy a little below it. That gap stays in the Desk too.
        </p>
      </div>

      <div className="mt-6 flex gap-3 rounded-md bg-info-soft p-4">
        <Info size={24} aria-hidden="true" className="shrink-0 text-info" />
        <div>
          <p className="type-label text-ink">Testnet only</p>
          <p className="mt-1 type-body text-ink-muted">
            Surrogate Pricer runs on Robinhood Chain’s test network with a staged TSLA price feed. No real money is
            involved, the contracts have had no outside audit yet, and nothing here is financial advice.
          </p>
        </div>
      </div>

      {/* After PR #2 merges: link docs/risk.md here (the full list of risks). */}
      <p className="mt-8 max-w-prose type-body text-ink-muted">
        For the details, read the <ExternalLink href={GITHUB.readme}>README</ExternalLink> and{' '}
        <ExternalLink href={GITHUB.contractsReview}>our own review of the contracts</ExternalLink>.
      </p>
    </Section>
  )
}

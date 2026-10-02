import ExternalLink from './ExternalLink.tsx'
import { GITHUB } from './links.ts'
import PriceEngine from './PriceEngine.tsx'
import Section from './Section.tsx'

// Sources: docs/interfaces.md (quoter.inputs is what the model saw; trade events carry price, fee
// and weightsHash; TooCloseToObservation, FeedStale), docs/architecture.md (the quoter reads the
// note's state from the chain, never from the caller), docs/k3-vol-input.md for model/k3, the
// default since 2026-10-01: certified domain = spot 50-120% of initial, 1-26 checks left, vol
// 20-90%, minus the observation-day bands at the barriers and, at vol below 50%, a few more days
// near the knock-in in the last four weeks. Accuracy against the teacher, not the market: the two
// sets never used before the model was final, T2 34.7 bps max on 143,484 points and T3 38.0 on
// 43,698 (187,182 together, means 2.63 and 2.85); the gate set T 37.9 on 130,752, which the doc
// says carries some optimism. How a price is made (the simulation, the model, Stylus) is drawn in
// PriceEngine.tsx; what the simulation is shaped on, and the times the Desk gives no price, are in
// RisksAndFees.tsx.

const CHECKS = [
  {
    title: 'Which version priced a trade',
    text: 'Every trade records the price, the fee and the model’s fingerprint: a code that changes if one number in the model does.',
  },
  {
    title: 'Where it may answer',
    text: 'Only where its accuracy was measured: the price between 50% and 120% of the starting price, for calm to very jumpy coins and stocks.',
  },
  {
    title: 'When it refuses',
    text: 'Close to a weekly check, a tiny price move near the crash line or the starting price can decide how a note ends. There the model refuses rather than guesses.',
  },
  {
    title: 'Recompute it yourself',
    text: 'The simulation is published code: anyone can run it on the same inputs and compare.',
  },
]

export default function CheckEveryPrice() {
  return (
    <Section
      id="verify"
      title="Every price is provable"
      intro="Usually the seller of this kind of insurance decides in private what it is worth. Here one public model sets every price, and anyone can check it."
    >
      <PriceEngine />

      <div className="mt-16 grid gap-10 lg:grid-cols-12 lg:gap-8">
        <div className="lg:col-span-7">
          <dl className="grid max-w-prose gap-7">
            {CHECKS.map((check) => (
              <div key={check.title}>
                <dt className="type-heading text-ink">{check.title}</dt>
                <dd className="mt-1.5 type-body text-ink-muted">{check.text}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-8 max-w-prose type-body text-ink-muted">
            Read <ExternalLink href={GITHUB.accuracy}>how the accuracy was measured</ExternalLink> and{' '}
            <ExternalLink href={GITHUB.architecture}>how the contracts fit together</ExternalLink>.
          </p>
        </div>

        <div className="panel self-start rounded-lg p-5 sm:p-6 lg:col-span-5">
          <h3 className="type-heading text-ink">How far off it can be</h3>
          <p className="mt-4 type-readout text-ink">0.38%</p>
          <p className="mt-2 type-body text-ink-muted">
            of the value a note covers, at most, compared with the simulation: 3.80 USDG on 1,000 USDG covered. On
            average it is 0.03% off.
          </p>
          {/* Caption plate (spec patterns.captionPlate). */}
          <div className="mt-6">
            <p className="type-body text-ink">Tested in 187,182 situations kept aside until the model was final.</p>
            <div className="my-3 max-w-md border-t border-line" />
            <p className="type-caption text-info">
              This shows how well the model copies the simulation, not whether the simulation is right about real
              prices.
            </p>
          </div>
        </div>
      </div>
    </Section>
  )
}

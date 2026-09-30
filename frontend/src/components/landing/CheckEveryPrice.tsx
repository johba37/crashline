import ExternalLink from './ExternalLink.tsx'
import { GITHUB } from './links.ts'
import Section from './Section.tsx'

// Sources: docs/interfaces.md (quoter.inputs is what the model saw; trade events carry price, fee
// and weightsHash; the certified domain of model/k2: spot 50-120% of initial, 1-26 checks left,
// observation-day bands at the barriers, TooCloseToObservation, FeedStale), docs/architecture.md
// (the quoter reads the note's state from the chain, never from the caller), docs/k2-round2.md
// (max error 18.7 bps on T's 64,506 points outside the bands, over the whole life; 17.8 bps on T2,
// drawn after the model was frozen; measured against the teacher, not the market), README and
// docs/teacher-v2.md (jump-diffusion teacher calibrated to 10 years of TSLA daily closes).

const CHECKS = [
  {
    title: 'What the model saw',
    text: 'Its inputs come from the blockchain, never from the trader: TSLA’s price, the weekly checks left, the time to the next one, and whether TSLA has crossed the crash line. Anyone can read them.',
  },
  {
    title: 'Which version priced a trade',
    text: 'Every trade records the price, the fee and the model’s fingerprint, a code unique to its exact version. Change one number in the model and the fingerprint changes.',
  },
  {
    title: 'Where it may answer',
    text: 'The model only prices where its accuracy was measured: TSLA between 50% and 120% of its starting price, from the first day to the last weekly check.',
  },
  {
    title: 'When it refuses',
    text: 'On the day of a weekly check, the value can jump near the starting price or the crash line. There the model refuses rather than guesses. There is also no price shortly before each check, or while the price feed is out of date, as on weekends.',
  },
  {
    title: 'Recompute it yourself',
    text: 'The simulation the model learned from is in the public repo. Anyone can run it on the same inputs and compare.',
  },
]

export default function CheckEveryPrice() {
  return (
    <Section
      id="verify"
      title="Check every price"
      intro="In 2008, crash insurance was priced by private bank models, and banks decided what their own positions were worth. Here one public model prices every trade and values every position, and anyone can check it."
    >
      <div className="mt-12 grid gap-10 lg:grid-cols-12 lg:gap-8">
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
          <p className="mt-4 type-readout text-ink">0.187%</p>
          <p className="mt-2 type-body text-ink-muted">
            of the amount, at most, against the full simulation, anywhere in the note’s life. On a 1,000 USDG note, that’s
            1.87 USDG.
          </p>
          <p className="mt-4 type-body text-ink-muted">
            The model learned from <ExternalLink href={GITHUB.simulation}>a simulation of TSLA’s price moves</ExternalLink>,
            sudden jumps included, tuned to 10 years of daily prices.
          </p>
          {/* Caption plate (spec patterns.captionPlate). */}
          <div className="mt-6">
            <p className="type-body text-ink">Tested in 64,506 situations the model never trained on.</p>
            <div className="my-3 max-w-md border-t border-line" />
            <p className="type-caption text-info">
              A second test, set up after the model was frozen, found 0.178% at most. Both compare the model with the
              simulation, not with the market.
            </p>
          </div>
        </div>
      </div>
    </Section>
  )
}

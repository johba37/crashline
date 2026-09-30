import { Coins, ShieldCheck } from '@phosphor-icons/react'
import Section from './Section.tsx'

// Terms (plans/max-frontend-submission.md "Ground rules", docs/interfaces.md, model/k2): 26 weekly
// checks, the end one week after the last; crash line 60%; ends early at 100%; weekly income
// 0.25% (25 bps). Payouts (contracts/src/interfaces/INoteSeries.sol) per 1,000 USDG, 2.50 a week,
// 27 weeks from start to end:
//   locked on day one (maxPayout)          1,000 + 27 x 2.50 = 1,067.50
//   Earn, no crash, runs to its end        1,000 + 27 x 2.50 = 1,067.50
//   Earn, ends early at check 10           1,000 + 10 x 2.50 = 1,025.00
//   Earn, crash line crossed, ends -40%      600 + 27 x 2.50 =   667.50
//   cover = 1,067.50 - Earn: crash, ends -50% = 500.00; no crash = 0.00; early at check 10 = 17 x 2.50 = 42.50
// AIG's $182 billion: docs/pitch.md (Congressional Research Service).

const STEPS = [
  {
    title: 'Each note is fully backed',
    text: 'The day a note is made, the most it can ever pay is locked in USDG: 1,067.50 USDG for a 1,000 USDG note. In 2008, AIG sold crash insurance it couldn’t pay and needed $182 billion in government help. Here the money is there from day one.',
  },
  {
    title: 'The Desk sells both sides',
    text: 'The Desk is the shop that sells and buys Earn and Protect. The price comes from a small AI model that runs on the blockchain, built with Arbitrum Stylus. You see the price and the fee before you buy, and you can sell back before the note ends.',
  },
  {
    title: 'Weekly checks decide the payout',
    text: 'Once a week, TSLA’s closing price is recorded on the blockchain. Only these prices count, not the moves in between. After 26 weekly checks, the note ends one week later and pays out by fixed rules, without the model. If TSLA is at or above its starting price (its price on day one) at a check, the note ends early and everyone is paid out.',
  },
]

const SIDES = [
  {
    name: 'Protect',
    Icon: ShieldCheck,
    who: 'You own TSLA and fear a crash.',
    text: 'Protect is crash insurance, also called cover. You pay for it once, up front: the premium. The crash line is 60% of TSLA’s starting price, a 40% fall. If TSLA is below it at a weekly check and ends below its starting price, cover pays you the fall. You hold it as the WRITER token.',
    example: 'Example: cover for 1,000 USDG',
    outcomes: [
      ['TSLA is below the crash line at a weekly check and ends 50% down', '500.00 USDG'],
      ['No crash, and the note runs to its end', '0.00 USDG'],
      ['The note ends early at the 10th check: 0.25% back for each of the 17 weeks left', '42.50 USDG'],
    ],
  },
  {
    name: 'Earn',
    Icon: Coins,
    who: 'You want a steady weekly income.',
    text: 'Earn pays a fixed weekly income: 0.25% of the amount for every week, about 13% a year, paid when the note ends. In return it carries the crash risk: when cover pays out, the money comes from Earn. You then get back only the share of value TSLA kept, plus the income. You hold it as the NOTE token.',
    example: 'Example: Earn on 1,000 USDG',
    outcomes: [
      ['No crash, and the note runs to its end: 27 weeks of income', '1,067.50 USDG'],
      ['The note ends early at the 10th check: 10 weeks of income', '1,025.00 USDG'],
      ['TSLA is below the crash line at a weekly check and ends 40% down: 600 back, plus the income', '667.50 USDG'],
    ],
  },
]

export default function HowItWorks() {
  return (
    <Section
      id="how-it-works"
      title="How it works"
      divider={false}
      intro="A note is an agreement on one stock, here TSLA, that runs for about six months. It is split into two sides, sold separately: Earn pays a fixed weekly income, and Protect is crash insurance. Everything is paid in USDG, a digital dollar made by Paxos."
    >
      <ol className="mt-12 grid gap-10 md:grid-cols-3 md:gap-8">
        {STEPS.map((step, i) => (
          <li key={step.title} className="border-t border-line-strong pt-5">
            <span aria-hidden="true" className="type-readout text-accent-text">
              {i + 1}
            </span>
            <h3 className="mt-3 type-heading text-ink">{step.title}</h3>
            <p className="mt-2 type-body text-ink-muted">{step.text}</p>
          </li>
        ))}
      </ol>

      <h3 className="mt-16 type-heading text-ink">Who it’s for</h3>
      <div className="mt-6 grid gap-6 md:grid-cols-2">
        {SIDES.map(({ name, Icon, who, text, example, outcomes }) => (
          <article key={name} aria-labelledby={`side-${name}`} className="panel flex flex-col rounded-lg p-5 sm:p-6">
            <div className="flex items-center gap-3">
              <Icon size={32} weight="duotone" aria-hidden="true" className="shrink-0 text-accent-text" />
              <h4 id={`side-${name}`} className="type-heading text-ink">
                {name}
              </h4>
            </div>
            <p className="mt-4 type-body-lg text-ink">{who}</p>
            <p className="mt-2 type-body text-ink-muted">{text}</p>
            <div className="mt-auto pt-6">
              <div className="rounded-md bg-surface-well p-4">
                <p className="type-label text-ink">{example}</p>
                <dl className="mt-2 divide-y divide-line">
                  {outcomes.map(([when, pays]) => (
                    <div key={when} className="flex items-baseline justify-between gap-4 py-3">
                      <dt className="type-body text-ink-muted">{when}</dt>
                      <dd className="shrink-0 type-data text-ink">{pays}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </div>
          </article>
        ))}
      </div>
      <p className="mt-4 max-w-prose type-caption text-ink-muted">
        Examples per 1,000 USDG of a note. What you pay for either side is the model’s price at the time, shown before you
        buy.
      </p>
    </Section>
  )
}

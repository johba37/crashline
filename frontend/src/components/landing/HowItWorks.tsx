import { Coins, ShieldCheck } from '@phosphor-icons/react'
import FlowMap from './FlowMap.tsx'
import Section from './Section.tsx'

// The page speaks about any stock; one note is the worked example here and in FlowMap.tsx: TSLA,
// 26 weekly checks, the end one week after the last (27 weeks, "six months"), crash line 60%, ends
// early at 100%, weekly income 0.25% (25 bps), for 1,000 USDG of the stock.
//
// Payouts (contracts/src/interfaces/INoteSeries.sol), 2.50 a week:
//   the pot, locked on day one (maxPayout)   1,000 + 27 x 2.50 = 1,067.50
//   no crash, runs to its end                Earn 1,067.50   cover     0.00
//   ends early at check 10                   Earn 1,025.00   cover    42.50  (17 x 2.50)
//   crash line crossed, ends -50%            Earn   567.50   cover   500.00  (500 + 27 x 2.50)
// Gain or loss = what came back - what was paid (cover 85.50, Earn 982.00):
//   no crash        Earn +85.50   cover  -85.50
//   early at 10     Earn +43.00   cover  -43.00
//   crash, -50%     Earn -414.50  cover +414.50
//
// Prices: model/k3, the deployed model, asked through the backend's POST /verify-quote on
// 2026-10-01 for a note on its first day (stock at its starting price, one week to the first
// check, nothing accrued). Clean NOTE price in bps of the amount; cover = the pot - NOTE:
//   26 checks, swing 55% (the TSLA listing)   9,820  ->  NOTE 982.00, cover 85.50
//   13 checks (14 weeks), swing 55%           9,961  ->  cover 38.90
//    4 checks (5 weeks), swing 55%           10,067  ->  cover  5.80
//   26 checks, swing 30%                     10,122  ->  cover 55.30
// (The shorter notes are quoted in FlowMap.tsx, in the Protect pay step.)
// These are the model's prices: the Desk's gap between buying and selling and the fee come on top.
// Ask again when the model changes.

// Signed, the way money moves for the buyer: what you pay is a minus, what comes back a plus, and
// each case ends in a gain or a loss (what came back minus what you paid). For Protect that is the
// cover alone: the stock's own loss is named in the crash row and in the line under the table.
// Protect "pays" (a price for insurance, usually gone); Earn "puts in" (it comes back unless
// the stock crashes), the app's own word for it. Not "stake": it sounds like a bet or like
// crypto staking, and for Protect it would promise the money back.
const SIDES = [
  {
    name: 'Protect',
    Icon: ShieldCheck,
    who: 'You hold 1,000 USDG of TSLA and cover all of it.',
    payLabel: 'You pay once, up front',
    pay: '−85.50 USDG',
    pays: 'Cover pays you',
    outcomes: [
      { when: 'TSLA crashes and ends 50% down, so your TSLA is worth 500 USDG less', back: '+500.00 USDG', result: 'gain +414.50', gain: true },
      { when: 'No crash, and the note runs to its end', back: '0.00 USDG', result: 'loss −85.50', gain: false },
      { when: 'TSLA is at or above its first-day price at the 10th weekly check: the note ends early, your cover stops, and you get back 2.50 for each of the 17 weeks left', back: '+42.50 USDG', result: 'loss −43.00', gain: false },
    ],
    net: 'In the crash your TSLA loses 500 and cover gains you 414.50: you end 85.50 USDG down instead of 500. Without a crash you are out the 85.50, like any insurance.',
  },
  {
    name: 'Earn',
    Icon: Coins,
    who: 'You take the other side of the same note, and with it the crash risk.',
    payLabel: 'You put in once, up front',
    pay: '−982.00 USDG',
    pays: 'You get back',
    outcomes: [
      { when: 'No crash, and the note runs to its end: the whole pot', back: '+1,067.50 USDG', result: 'gain +85.50', gain: true },
      { when: 'The note ends early at the 10th weekly check: 1,000 plus 10 weeks of income', back: '+1,025.00 USDG', result: 'gain +43.00', gain: true },
      { when: 'TSLA crashes and ends 50% down: what is left of the pot after cover', back: '+567.50 USDG', result: 'loss −414.50', gain: false },
    ],
    net: 'The 85.50 gain is 67.50 of income, and 18.00 because you put in less than the 1,000 you get back. That is the pay for taking the crash risk.',
  },
]

export default function HowItWorks() {
  return (
    <Section
      id="how-it-works"
      title="How it works"
      divider={false}
      intro="Pay once, and if your stock crashes by more than 40%, you are paid what it lost: that is Protect. Earn is the other side: it takes over that risk for a fixed weekly income. Pick any step or arrow below to follow one example, six months on Tesla’s stock."
    >
      <FlowMap />

      <h3 className="mt-16 type-heading text-ink">An example in numbers</h3>
      <p className="mt-2 max-w-prose type-body text-ink-muted">
        Six months on 1,000 USDG of TSLA: its price is checked once a week, 26 times, and the note ends a week later.
        Before the note is sold, the Desk locks everything it could ever pay out in a pot: 1,067.50 USDG, the 1,000
        being covered plus 67.50 for 27 weeks of income at 2.50 a week. What the two sides then put in adds up to exactly
        that amount, and the weekly checks decide who gets how much of it back. USDG is a digital dollar.
      </p>
      <div className="mt-6 grid gap-6 md:grid-cols-2">
        {SIDES.map(({ name, Icon, who, payLabel, pay, pays, outcomes, net }) => (
          <article key={name} aria-labelledby={`side-${name}`} className="panel flex flex-col rounded-lg p-5 sm:p-6">
            <div className="flex items-center gap-3">
              <Icon size={32} weight="duotone" aria-hidden="true" className="shrink-0 text-accent-text" />
              <h4 id={`side-${name}`} className="type-heading text-ink">
                {name}
              </h4>
            </div>
            <p className="mt-4 type-body-lg text-ink">{who}</p>
            <p className="mt-4 flex items-baseline justify-between gap-4 border-t border-line pt-4">
              <span className="type-body text-ink-muted">{payLabel}</span>
              <span className="shrink-0 type-data text-ink">{pay}</span>
            </p>
            <div className="mt-auto pt-4">
              <div className="rounded-md bg-surface-well p-4">
                <p className="type-label text-ink">{pays}</p>
                <dl className="mt-2 divide-y divide-line">
                  {outcomes.map(({ when, back, result, gain }) => (
                    <div key={when} className="flex items-baseline justify-between gap-4 py-3">
                      <dt className="type-body text-ink-muted">{when}</dt>
                      <dd className="shrink-0 text-right">
                        <span className="block type-data text-ink">{back}</span>
                        <span className={`block type-label tabular-nums ${gain ? 'text-go' : 'text-abort'}`}>{result}</span>
                      </dd>
                    </div>
                  ))}
                </dl>
                <p className="border-t border-line pt-3 type-label text-ink">{net}</p>
              </div>
            </div>
          </article>
        ))}
      </div>
      <p className="mt-4 max-w-prose type-caption text-ink-muted">
        Prices are the model’s for this note on its first day. They move with the stock’s price, the time left and how
        much the stock swings. The Desk sells a little above them and a fee can come on top, both with built-in limits
        (see Risks and fees). The app shows the exact amount before you buy. Today this is a test version, with no
        real money.
      </p>
    </Section>
  )
}

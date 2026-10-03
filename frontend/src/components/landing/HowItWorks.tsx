import { CalendarCheck, CaretDown, Coins, FlagCheckered, ShieldCheck, TrendDown } from '@phosphor-icons/react'
import EarnRate from './EarnRate.tsx'
import FlowMap from './FlowMap.tsx'
import Section from './Section.tsx'

// The page speaks about any coin or stock; one note is the worked example, and only here: TSLA,
// 26 weekly checks, the end one week after the last (27 weeks, "six months"), crash line 60%, ends
// early at 100%, weekly income 0.25% (25 bps), for 1,000 USDG of the stock. The income is this
// note's own: other notes may pay other rates, so outside the example the rate is EarnRate.tsx's
// one figure for all notes.
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
// (Only the first row is on the page.)
// These are the model's prices: the Desk's gap between buying and selling and the fee come on top.
// Ask again when the model changes.

// The same three ways a note can end, in the same order on both sides, so each is found at a
// glance: its name, what came back and the gain or loss. The story sits behind a click (the
// pattern of the app's order step, dashboard/Order.tsx).
const CASES = [
  { id: 'crash', name: 'Crash', when: 'TSLA ends 50% down', Icon: TrendDown },
  { id: 'none', name: 'No crash', when: 'The note runs its six months', Icon: CalendarCheck },
  { id: 'early', name: 'Ends early', when: 'At the 10th weekly check, TSLA is at its first-day price or above', Icon: FlagCheckered },
] as const

// Signed, the way money moves for the buyer: what you pay is a minus, what comes back a plus, and
// each case ends in a gain or a loss (what came back minus what you paid). For Protect that is the
// cover alone: the stock's own loss is told in the crash story and in the line under the cases.
// Protect "pays" (a price for insurance, usually gone); Earn "puts in" (it comes back unless
// the stock crashes), the app's own word for it. Not "stake": it sounds like a bet or like
// crypto staking, and for Protect it would promise the money back.
const SIDES = [
  {
    name: 'Protect',
    Icon: ShieldCheck,
    tone: 'text-protect',
    who: 'You hold 1,000 USDG of TSLA and cover all of it.',
    payLabel: 'You pay once, up front',
    pay: '−85.50 USDG',
    pays: 'Cover pays you',
    outcomes: {
      crash: { back: '+500.00 USDG', result: 'gain +414.50', gain: true, more: 'TSLA was below the crash line at a weekly check and ends 50% below its first-day price. Your 1,000 USDG of TSLA is worth 500 less, and cover pays you exactly that when the note ends. After the 85.50 you paid, you end 85.50 down instead of 500.' },
      none: { back: '0.00 USDG', result: 'loss −85.50', gain: false, more: 'TSLA never closes below the crash line at a weekly check. Cover pays nothing and you are out the 85.50, like any insurance. A fall that stays above the crash line is not covered.' },
      early: { back: '+42.50 USDG', result: 'loss −43.00', gain: false, more: 'The note ends that day and your cover stops. You get back 2.50 for each of the 17 weeks left. This can happen at any weekly check, even the first: the earlier it ends, the more comes back.' },
    },
    net: 'A crash costs you 85.50 USDG instead of 500.',
  },
  {
    name: 'Earn',
    Icon: Coins,
    tone: 'text-earn',
    who: 'You take the other side of the same note, and with it the crash risk.',
    payLabel: 'You put in once, up front',
    pay: '−982.00 USDG',
    pays: 'You get back',
    outcomes: {
      crash: { back: '+567.50 USDG', result: 'loss −414.50', gain: false, more: 'TSLA was below the crash line at a weekly check and ends 50% below its first-day price. The fall, 500, goes to Protect. You get the rest of the pot: 500 for the half of its value TSLA kept, plus 67.50 of income.' },
      none: { back: '+1,067.50 USDG', result: 'gain +85.50', gain: true, more: 'You get the whole pot: 1,000 plus 27 weeks of income at 2.50. The 85.50 gain is 67.50 of income, and 18.00 because you put in less than the 1,000 you get back.' },
      early: { back: '+1,025.00 USDG', result: 'gain +43.00', gain: true, more: 'The note ends that day and you get 1,000 plus 10 weeks of income. The rest of the pot goes back to Protect. This can happen at any weekly check, even the first.' },
    },
    net: 'You are paid for taking the crash risk: up to 85.50 USDG.',
  },
]

export default function HowItWorks() {
  return (
    <Section
      id="how-it-works"
      title="How it works"
      divider={false}
      intro={
        <>
          <span className="whitespace-nowrap">
            <ShieldCheck size={20} weight="duotone" aria-hidden="true" className="inline align-[-0.15em] text-protect" /> Protect
          </span>{' '}
          your coin or stock: pay once, and if it crashes, you are paid what it lost.{' '}
          <span className="whitespace-nowrap">
            <Coins size={20} weight="duotone" aria-hidden="true" className="inline align-[-0.15em] text-earn" /> Earn
          </span>{' '}
          is the other side: it takes over that risk for a fixed weekly income, <EarnRate />.
        </>
      }
    >
      <FlowMap />

      <h3 className="mt-16 type-heading text-ink">An example in numbers</h3>
      <p className="mt-2 max-w-prose type-body text-ink-muted">
        One note as an example: six months on 1,000 USDG of TSLA. Every note has its own price and weekly income.
      </p>
      <div className="mt-6 grid items-start gap-6 md:grid-cols-2">
        {SIDES.map(({ name, Icon, tone, who, payLabel, pay, pays, outcomes, net }) => (
          <article key={name} aria-labelledby={`side-${name}`} className="panel flex flex-col rounded-lg p-5 sm:p-6">
            <div className="flex items-center gap-3">
              <Icon size={32} weight="duotone" aria-hidden="true" className={`shrink-0 ${tone}`} />
              <h4 id={`side-${name}`} className="type-heading text-ink">
                {name}
              </h4>
            </div>
            <p className="mt-4 type-body-lg text-ink">{who}</p>
            <p className="mt-4 flex items-baseline justify-between gap-4 border-t border-line pt-4">
              <span className="type-body text-ink-muted">{payLabel}</span>
              <span className="shrink-0 type-data text-ink">{pay}</span>
            </p>
            <div className="pt-4">
              <div className="rounded-md bg-surface-well px-4 pt-4">
                <p className="flex justify-between gap-4 pr-7 type-label text-ink-muted">
                  <span>How it can end</span>
                  <span>{pays}</span>
                </p>
                <ul className="mt-1 divide-y divide-line">
                  {CASES.map(({ id, name: title, when, Icon: CaseIcon }) => {
                    const { back, result, gain, more } = outcomes[id]
                    return (
                      <li key={id}>
                        <details name={`cases-${name}`} className="group">
                          <summary className="flex cursor-pointer list-none items-center gap-3 py-3 [&::-webkit-details-marker]:hidden">
                            <CaseIcon size={24} aria-hidden="true" className="shrink-0 text-ink-muted" />
                            <span className="flex min-w-0 flex-1 items-start justify-between gap-4">
                              <span className="flex min-w-0 flex-col">
                                <span className="type-data text-ink">{title}</span>
                                <span className="type-caption text-ink-muted">{when}</span>
                              </span>
                              <span className="shrink-0 text-right">
                                <span className="block type-data text-ink">{back}</span>
                                <span className={`block type-label tabular-nums ${gain ? 'text-go' : 'text-abort'}`}>{result}</span>
                              </span>
                            </span>
                            <CaretDown size={16} weight="bold" aria-hidden="true" className="shrink-0 text-ink-muted transition-transform duration-240 group-open:rotate-180" />
                          </summary>
                          <p className="pr-7 pb-3 pl-9 type-body text-ink-muted">{more}</p>
                        </details>
                      </li>
                    )
                  })}
                </ul>
                <p className="border-t border-line py-3 type-label text-ink">{net}</p>
              </div>
            </div>
          </article>
        ))}
      </div>
      <p className="mt-4 max-w-prose type-caption text-ink-muted">
        The model’s prices for this note on its first day. The Desk sells a little above them, and a fee can come on
        top. Today this is a test version, with no real money.
      </p>
    </Section>
  )
}

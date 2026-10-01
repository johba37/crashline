import {
  CaretDown, CheckCircle, Coins, CurrencyCircleDollar, HandCoins, Lego, RoadHorizon, ShieldCheck, Storefront, TrendDown, Umbrella,
  type Icon,
} from '@phosphor-icons/react'
import { useRef, useState, type Ref } from 'react'

// The two ways into a note as a map: every plate and every arrow is a button that explains its
// step. The map follows one example, the six-month note on TSLA from HowItWorks.tsx (prices and
// payouts: the comment at its top, shown in numbers right below the map); the rules are worded
// for any stock, and what you can choose (stock, length, crash line) is told in the start, pay
// and weekly-check steps. Tokens:
// contracts/src/interfaces/INoteSeries.sol (NOTE and WRITER are plain ERC-20s, one pair per note;
// redeem pays whoever holds them; redeemPair swaps both for the locked USDG at any time) and
// IDeskCover.sol (the Desk buys both back). What isn't built: docs/architecture.md rule [5] (no
// price feed for lenders yet) and the README roadmap, M3. Lending, as johba found on 2026-10-01:
// a lender needs a price at all times and the model gives none on weekends, so neither token can
// be collateral without a feed that quotes then; and a stock with its cover gives a lender nothing
// at a 60% crash line, because pools already lend only about 60% of a stock's value (a line at 80%
// would help, and would make cover far too expensive). So no claim that cover raises what you can
// borrow. AIG's $182 billion: docs/pitch.md.

type Lane = 'protect' | 'earn'
type StepId = 'start' | 'pay' | 'desk' | 'get' | 'token' | 'use' | 'block' | 'hold' | 'payout'
type Step = {
  label: string // on the plate or the arrow
  sub?: string
  Icon?: Icon
  title: string
  text: string
}

// Top to bottom. Arrows sit between the plates; the Desk and the building block span both lanes.
const ORDER: StepId[] = ['start', 'pay', 'desk', 'get', 'token', 'use', 'block', 'hold', 'payout']
const ARROWS: StepId[] = ['pay', 'get', 'use', 'hold']
const SHARED: StepId[] = ['desk', 'block']

// Grid order: every row has an even number, so the phone's inline detail can take the odd one
// after the row of the chosen step.
const rowOf = (s: StepId) => 2 * (ORDER.indexOf(s) + 1)

const LANES = {
  protect: { name: 'Protect', what: 'Crash insurance', Icon: ShieldCheck, plate: 'plate-protect', text: 'text-protect', bg: 'bg-protect', border: 'border-protect' },
  earn: { name: 'Earn', what: 'A weekly income', Icon: Coins, plate: 'plate-earn', text: 'text-earn', bg: 'bg-earn', border: 'border-earn' },
} as const

const DESK: Step = {
  label: 'The Desk',
  sub: 'The shop: one public price, every payout locked',
  Icon: Storefront,
  title: 'The Desk sells both sides',
  text: 'The Desk is the shop in the middle: a program on the blockchain, not a company. It makes the notes and fills each note’s pot, with money from people who fund the Desk for a share of what it earns. Then it sells the two sides, Protect and Earn, at a price anyone can check. The pot holds everything the note could ever pay out, and at the end it is split between the two sides. So nobody has to hope the other side can pay. In 2008, AIG sold crash insurance it couldn’t pay and needed $182 billion in government help.',
}

const BLOCK: Step = {
  label: 'A new building block',
  sub: 'Tokens you can sell, send and build on',
  Icon: Lego,
  title: 'A new building block',
  text: 'Insurance from a company and notes from a bank stay where you bought them. Here both sides are tokens with a public price, so other apps can plug them in like Lego bricks. In DeFi, the finance apps on the blockchain, that is how new products get built.',
}

const STEPS: Record<StepId, Record<Lane, Step>> = {
  start: {
    protect: {
      label: 'You own a stock',
      sub: 'say TSLA, and fear a crash',
      Icon: TrendDown,
      title: 'You own a stock and fear a crash',
      text: 'Say you own Tesla stock (TSLA) worth 1,000 USDG and want to keep it. What worries you is a big fall. Protect is crash insurance for it, also called cover, so you don’t have to sell. There are notes on several stocks, such as Tesla, Nvidia or Apple, and cover pays by the stock’s price alone: you don’t have to prove you own it. USDG is a digital dollar: 1 USDG is 1 US dollar.',
    },
    earn: {
      label: 'You have USDG',
      sub: 'and want a weekly income',
      Icon: CurrencyCircleDollar,
      title: 'You have USDG and want an income',
      text: 'USDG is a digital dollar: 1 USDG is 1 US dollar. Earn pays a fixed weekly income on it: 0.25% a week, which is 2.50 USDG on 1,000, or about 13% a year. In return you carry the crash risk that Protect buyers pay to hand over. You pick the stock to earn on, such as Tesla, Nvidia or Apple.',
    },
  },
  pay: {
    protect: {
      label: 'Pay once',
      title: 'You pay once, up front',
      text: 'You choose how long the cover lasts, from a few weeks to about six months, and pay for it once in USDG: nothing more later. Longer cover costs more. For 1,000 USDG of Tesla it is 5.80 USDG for five weeks, 38.90 for fourteen weeks and 85.50 for six months, the example here. A calmer stock costs less. The price comes from a small AI model that runs in public on the blockchain: it is the same for everyone, and you see it and the fee before you buy.',
    },
    earn: {
      label: 'Put in USDG',
      title: 'You put in USDG once',
      text: 'You choose how long, from a few weeks to about six months, and put in USDG once: in the example, 982.00 USDG for the Earn side of the same six-month note on TSLA. That is less than the 1,000 you get back without a crash, because you take the crash risk. A longer note pays more weeks of income. The amount comes from the same public model that prices cover, and you see it and the fee before you buy.',
    },
  },
  desk: { protect: DESK, earn: DESK },
  get: {
    protect: {
      label: 'You get cover',
      title: 'Your cover arrives in your wallet',
      text: 'You pay and receive in one step: the Desk sends the cover to your wallet as a token, where it shows up under the technical name WRITER. Each note has its own token, so cover on another stock or for another time is a different one.',
    },
    earn: {
      label: 'You get Earn',
      title: 'Your Earn side arrives in your wallet',
      text: 'You pay and receive in one step: the Desk sends the Earn side to your wallet as a token, where it shows up under the technical name NOTE. Each note has its own token, so Earn on another stock or for another time is a different one.',
    },
  },
  token: {
    protect: {
      label: 'Cover token',
      sub: 'Crash insurance in your wallet',
      Icon: Umbrella,
      title: 'Cover you hold yourself',
      text: 'Most insurance is a promise from a company. Cover is a token in your own wallet, and the money behind it is already in the pot. Whoever holds the token gets the payout. It is a standard token (ERC-20), so any wallet or app on the blockchain can handle it.',
    },
    earn: {
      label: 'Earn token',
      sub: 'A weekly income in your wallet',
      Icon: Coins,
      title: 'An income you hold yourself',
      text: 'Most investments sit in an account at a bank or a broker. The Earn token sits in your own wallet, and the money behind it is already in the pot. Whoever holds the token gets the payout. It is a standard token (ERC-20), so any wallet or app on the blockchain can handle it.',
    },
  },
  use: {
    protect: {
      label: 'Yours to use',
      title: 'Until the note ends, the cover is yours to use',
      text: 'A note runs for weeks or months. Until it ends, you don’t have to sit and wait: the cover is a token, and the next step shows what you can do with it.',
    },
    earn: {
      label: 'Yours to use',
      title: 'Until the note ends, the token is yours to use',
      text: 'A note runs for weeks or months. Until it ends, you don’t have to sit and wait: your Earn side is a token, and the next step shows what you can do with it.',
    },
  },
  block: { protect: BLOCK, earn: BLOCK },
  hold: {
    protect: {
      label: 'Weekly checks',
      title: 'Weekly checks decide the payout',
      text: 'Once a week, the stock’s closing price is recorded on the blockchain. The crash line sits 40% under the starting price, the stock’s price on the note’s first day. If the stock is below it at a weekly check, the insurance is on and stays on, and you are paid when the note ends: the amount is how far the stock is then below its starting price. The line is fixed on that first day, so a note that started before a fall has a line nearer to today’s price, and costs more. But if the stock is at or above its starting price at any weekly check, even the first, the note ends early: your cover stops, and you get back part of what you paid, in the example 2.50 USDG for each week left.',
    },
    earn: {
      label: 'Weekly checks',
      title: 'Weekly checks decide the payout',
      text: 'Once a week, the stock’s closing price is recorded on the blockchain. If the stock is at or above its starting price (its price on the note’s first day) at any weekly check, even the first, the note ends early and you are paid out. The crash line sits 40% under the starting price. If the stock is below it at a check, your money is at risk: you then take the stock’s fall when the note ends. A note whose line is nearer to today’s price carries more risk, and costs less.',
    },
  },
  payout: {
    protect: {
      label: 'Your loss is paid',
      sub: 'if the stock crashes',
      Icon: HandCoins,
      title: 'Cover pays you the fall',
      text: 'If the stock was below the crash line at a weekly check and is below its starting price when the note ends, cover pays you the fall: what your stock lost since the note’s first day. It comes out of the pot by fixed rules: no model and no person decides it. You collect it with your token once the note has ended. The example below the map shows all three cases in money.',
    },
    earn: {
      label: 'Paid back with income',
      sub: 'unless the stock crashes',
      Icon: HandCoins,
      title: 'Earn pays you back, with the income',
      text: 'Without a crash, Earn pays you the whole pot, which is more than you put in. In a crash, the fall goes to Protect and you get the rest. Either way it is paid by fixed rules: no model and no person decides it. You collect it with your token once the note has ended. The example below the map shows all three cases in money.',
    },
  },
}

// What a token can do. Only the first list is built: never move an item up without the contract.
const USES = [
  {
    title: 'Works today',
    Icon: CheckCircle,
    tone: 'text-go',
    items: [
      { name: 'Leave before the end', who: 'Both tokens', text: 'Sell your token back to the Desk at the model’s price that day, whenever the Desk gives one.' },
      { name: 'Hand it over', who: 'Both tokens', text: 'Send it to another wallet, a friend or a shared fund. The payout goes with it.' },
      { name: 'Two halves make the whole', who: 'Both tokens together', text: 'Hold both tokens of the same note, and you can swap the pair for their share of the pot at any time.' },
      { name: 'A price any app can read', who: 'Both tokens', text: 'The model’s price is public, so a wallet or a dashboard can show what your token is worth right now.' },
    ],
  },
  {
    title: 'Could come next, not built yet',
    Icon: RoadHorizon,
    tone: 'text-info',
    items: [
      { name: 'Borrow against it', who: 'The Earn token', text: 'A lender could accept the Earn token as collateral, the thing you pledge to get a loan.' },
      { name: 'Bundle into a basket', who: 'The Earn token', text: 'An app could pack notes on different stocks and with different end dates into one token, to smooth out income and risk. Funds already do this with notes sold by banks.' },
      { name: 'Build on top', who: 'Both tokens', text: 'A shared fund could protect the stocks it holds, or a savings app could wrap the Earn token into a simpler product.' },
    ],
  },
]

const QUIET = 'inline-flex h-10 items-center rounded-full px-4 type-label transition-colors duration-160 ease-out'
const OUTLINE = `${QUIET} border border-line-strong text-ink hover:bg-surface-overlay disabled:cursor-not-allowed disabled:border-line disabled:text-ink-faint disabled:hover:bg-transparent`

/** The chosen step in words, with Back and Next along its lane. */
function Detail({
  id, lane, step, onSelect, className, order, ref,
}: { id: string; lane: Lane; step: StepId; onSelect: (lane: Lane, step: StepId) => void; className: string; order?: number; ref?: Ref<HTMLElement> }) {
  const { title, text } = STEPS[step][lane]
  const { name, Icon: LaneIcon, text: tone } = LANES[lane]
  const i = ORDER.indexOf(step)
  const other = lane === 'protect' ? 'earn' : 'protect'

  return (
    <section ref={ref} id={id} aria-label="About this step" style={{ order }} className={`panel rounded-lg p-5 sm:p-6 ${className}`}>
      <div aria-live="polite">
        {/* Keyed, so a new step fades in. */}
        <div key={`${lane}-${step}`} className="transition-opacity duration-240 ease-out starting:opacity-0">
          <p className="flex items-center gap-1.5 type-label text-ink-muted">
            <LaneIcon size={16} weight="bold" aria-hidden="true" className={tone} />
            <span className="text-ink">{name}</span>
            <span>
              · Step {i + 1} of {ORDER.length}
            </span>
          </p>
          <h3 className="mt-3 type-heading text-ink">{title}</h3>
          <p className="mt-2 type-body text-ink-muted">{text}</p>

          {step === 'block' && (
            <>
              {USES.map((group) => (
                <div key={group.title} className="mt-5 rounded-md bg-surface-well p-4">
                  <p className="flex items-center gap-1.5 type-label text-ink">
                    <group.Icon size={16} weight="bold" aria-hidden="true" className={group.tone} />
                    {group.title}
                  </p>
                  <ul className="mt-1 divide-y divide-line">
                    {group.items.map((item) => (
                      <li key={item.name} className="py-3">
                        <p className="flex flex-wrap items-baseline justify-between gap-x-3 type-label">
                          <span className="text-ink">{item.name}</span>
                          <span className="type-caption text-ink-muted">{item.who}</span>
                        </p>
                        <p className="mt-1 type-body text-ink-muted">{item.text}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              <p className="mt-4 type-caption text-ink-muted">
                A lender needs a price at every moment, weekends included. The model gives none on weekends, so this
                takes a price feed of its own, which isn’t built yet.
              </p>
            </>
          )}
        </div>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <button type="button" disabled={i === 0} onClick={() => onSelect(lane, ORDER[i - 1])} className={OUTLINE}>
          Back
        </button>
        <button type="button" disabled={i === ORDER.length - 1} onClick={() => onSelect(lane, ORDER[i + 1])} className={OUTLINE}>
          Next step
        </button>
        <button type="button" onClick={() => onSelect(other, step)} className={`${QUIET} ml-auto text-ink-muted hover:bg-surface-overlay hover:text-ink`}>
          Switch to {LANES[other].name}
        </button>
      </div>
    </section>
  )
}

/**
 * Protect and Earn side by side, from what you bring to what you get paid. On phones the chosen
 * step opens right under its row; from lg it sits next to the map and stays in view. The section
 * intro above it (HowItWorks.tsx) says how to use it.
 */
export default function FlowMap() {
  // Phones show the whole map first: the inline detail opens with the first pick.
  const [{ lane, step, picked }, setChosen] = useState<{ lane: Lane; step: StepId; picked: boolean }>({ lane: 'protect', step: 'start', picked: false })
  const inline = useRef<HTMLElement>(null)
  const controls = 'flow-step flow-step-inline'

  const select = (nextLane: Lane, nextStep: StepId) => {
    setChosen({ lane: nextLane, step: nextStep, picked: true })
    // Phones: keep the step's text in view as it moves down the map (hidden from lg, so a no-op there).
    requestAnimationFrame(() => inline.current?.scrollIntoView({ block: 'nearest' }))
  }

  const plate = (s: StepId, l: Lane, shape: string, order?: number) => {
    const { label, sub, Icon: StepIcon } = STEPS[s][l]
    const shared = SHARED.includes(s)
    return (
      <button
        key={`${s}-${l}`}
        type="button"
        style={{ order }}
        aria-pressed={step === s && (shared || lane === l)}
        aria-controls={controls}
        onClick={() => select(shared ? lane : l, s)}
        className={`plate flex gap-x-4 gap-y-2 rounded-md p-3 text-left transition-[filter,box-shadow] duration-160 ease-out hover:brightness-105 sm:p-4 ${shape}`}
      >
        {StepIcon && <StepIcon size={32} weight="duotone" aria-hidden="true" className="shrink-0" />}
        <span className="flex flex-col gap-0.5">
          <span className="type-heading">{label}</span>
          <span className="type-caption text-ink-muted">{sub}</span>
        </span>
      </button>
    )
  }

  return (
    <div className="mt-10 lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)] lg:gap-10">
        <div className="grid grid-cols-2 content-start gap-x-3 sm:gap-x-4">
          {(['protect', 'earn'] as const).map((l) => {
            const { name, what, Icon: LaneIcon, text } = LANES[l]
            return (
              <p key={l} className="flex items-center gap-2 pb-3">
                <LaneIcon size={24} weight="duotone" aria-hidden="true" className={`shrink-0 ${text}`} />
                <span className="flex flex-col">
                  <span className={`type-label ${text}`}>{name}</span>
                  <span className="type-caption text-ink-muted">{what}</span>
                </span>
              </p>
            )
          })}

          {ORDER.flatMap((s) => {
            const order = rowOf(s)
            if (SHARED.includes(s)) return [plate(s, 'protect', `col-span-2 items-center ${s === 'block' ? 'plate-accent' : ''}`, order)]
            return (['protect', 'earn'] as const).map((l) => {
              if (!ARROWS.includes(s)) return plate(s, l, `flex-col ${LANES[l].plate}`, order)
              const { text, bg, border } = LANES[l]
              return (
                <div key={`${s}-${l}`} style={{ order }} className="flex flex-col items-center">
                  <span aria-hidden="true" className={`h-3 w-0.5 ${bg}`} />
                  <button
                    type="button"
                    aria-pressed={step === s && lane === l}
                    aria-controls={controls}
                    onClick={() => select(l, s)}
                    className={`relative inline-flex h-8 items-center rounded-full border bg-surface px-3 type-label whitespace-nowrap text-ink transition-[background-color,box-shadow] duration-160 ease-out after:absolute after:-inset-1.5 hover:bg-surface-overlay aria-pressed:border-accent aria-pressed:bg-surface-overlay aria-pressed:shadow-[inset_0_0_0_1px_var(--color-accent)] ${border}`}
                  >
                    {STEPS[s][l].label}
                  </button>
                  <span aria-hidden="true" className={`h-3 w-0.5 ${bg}`} />
                  <CaretDown size={14} weight="bold" aria-hidden="true" className={`-mt-2 mb-0.5 ${text}`} />
                </div>
              )
            })
          })}

          <Detail
            ref={inline}
            id="flow-step-inline"
            lane={lane}
            step={step}
            onSelect={select}
            order={rowOf(step) + 1}
            className={`col-span-2 mt-3 mb-1 scroll-mt-24 scroll-mb-4 lg:hidden ${picked ? '' : 'hidden'}`}
          />
        </div>

        <div className="hidden lg:block">
          <Detail id="flow-step" lane={lane} step={step} onSelect={select} className="sticky top-28" />
        </div>
    </div>
  )
}

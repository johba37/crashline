import { CaretDown, Coins, CurrencyCircleDollar, HandCoins, ShieldCheck, Storefront, TrendDown, type Icon } from '@phosphor-icons/react'
import { useRef, useState, type ReactNode, type Ref } from 'react'
import Term from '../dashboard/Term.tsx'
import InfoTip from '../Tooltip.tsx'
import EarnRate from './EarnRate.tsx'

// The two ways into a note as a map on one sheet of glass, with the chosen step's text on the same
// sheet. Every node and every arrow is a button that explains its step in a sentence or two,
// worded for any coin or stock. The nodes are flat tints in the lane colour: no gradient, and no glass of
// their own (glass never sits inside glass). On glass the lane colour is for icons and lines only:
// text is ink, the one colour that clears the contrast gate on a tint in both themes. No worked example and no figures here: the one
// example is the panels in HowItWorks.tsx, and the weekly income may differ per note, so its rate is
// the average over the notes (EarnRate.tsx). What a reader may want on top sits behind info icons. Tokens:
// contracts/src/interfaces/INoteSeries.sol (NOTE and WRITER are plain ERC-20s, one pair per note;
// redeem pays whoever holds them) and IDeskCover.sol (the Desk buys both back).

type Lane = 'protect' | 'earn'
type StepId = 'start' | 'pay' | 'desk' | 'hold' | 'payout'
type Step = {
  label: string // on the node or the arrow
  sub?: string
  Icon?: Icon
  title: string
  text: ReactNode
}

// Top to bottom. Arrows sit between the nodes; the Desk spans both lanes.
const ORDER: StepId[] = ['start', 'pay', 'desk', 'hold', 'payout']
const ARROWS: StepId[] = ['pay', 'hold']
const SHARED: StepId[] = ['desk']

// Grid order: every row has an even number, so the phone's inline detail can take the odd one
// after the row of the chosen step.
const rowOf = (s: StepId) => 2 * (ORDER.indexOf(s) + 1)

const LANES = {
  protect: { name: 'Protect', what: 'Crash insurance', Icon: ShieldCheck, node: 'border-protect/40 bg-protect/10 hover:border-protect', text: 'text-protect', bg: 'bg-protect', border: 'border-protect' },
  earn: { name: 'Earn', what: 'A weekly income', Icon: Coins, node: 'border-earn/40 bg-earn/10 hover:border-earn', text: 'text-earn', bg: 'bg-earn', border: 'border-earn' },
} as const

/** A word with its info icon, like Term, for what the dashboard's glossary words differently. */
function Tip({ word, children }: { word: string; children: string }) {
  return (
    <span className="inline-flex items-center gap-0.5 whitespace-nowrap">
      {word}
      <InfoTip label={word}>{children}</InfoTip>
    </span>
  )
}

// The crash level is a choice, so the tip names a figure only as an example.
const CRASH_LINE = <Tip word="crash line">The price that counts as a crash, for example 40% under the starting price. You choose how far under when you buy.</Tip>
const POT = <Tip word="pot">Everything a note could ever pay out, locked before it is sold. At the end it is split between Protect and Earn.</Tip>
const TOKEN = <Tip word="token">A standard token (ERC-20): in a wallet, cover shows up as WRITER and Earn as NOTE. Whoever holds it gets the payout. You can send it on, or sell it back to the Desk before the note ends.</Tip>

const DESK: Step = {
  label: 'The Desk',
  sub: 'The shop: one public price, every payout locked',
  Icon: Storefront,
  title: 'The Desk sells both sides',
  text: <>A program on the blockchain, not a company. It sells both sides at one public price, and locks everything a note could ever pay out in a {POT} before it is sold. Your side arrives in your wallet as a {TOKEN}.</>,
}

const STEPS: Record<StepId, Record<Lane, Step>> = {
  start: {
    protect: {
      label: 'You own a coin or stock',
      sub: 'and fear a crash',
      Icon: TrendDown,
      title: 'You own a coin or stock and fear a crash',
      text: 'Protect is crash insurance for a coin or stock you want to keep. It pays by the price alone: you don’t have to prove you own it.',
    },
    earn: {
      label: 'You have USDG',
      sub: 'and want a weekly income',
      Icon: CurrencyCircleDollar,
      title: 'You have USDG and want an income',
      text: <>Earn pays a fixed weekly income on it: <EarnRate />. In return you carry the crash risk that Protect buyers pay to hand over.</>,
    },
  },
  pay: {
    protect: {
      label: 'Pay once',
      title: 'You pay once, up front',
      text: <>You choose the coin or stock, how long the cover lasts and the {CRASH_LINE}, and pay once: nothing more later. The price comes from a public <Term t="model" />, and you see it before you buy.</>,
    },
    earn: {
      label: 'Put in USDG',
      title: 'You put in USDG once',
      text: <>You choose the coin or stock, how long and the {CRASH_LINE}, and put in USDG once. The amount comes from the same public <Term t="model" /> that prices cover, and you see it before you buy.</>,
    },
  },
  desk: { protect: DESK, earn: DESK },
  hold: {
    protect: {
      label: 'Weekly checks',
      title: 'Weekly checks decide the payout',
      text: <>Once a week, the price is recorded. Below the {CRASH_LINE} at a check, your cover is on and pays when the note ends. Back at the <Term t="startingPrice" /> or above, the note <Term t="endsEarly" /> and your cover stops: part of what you paid comes back.</>,
    },
    earn: {
      label: 'Weekly checks',
      title: 'Weekly checks decide the payout',
      text: <>Once a week, the price is recorded. Back at the <Term t="startingPrice" /> or above at a check, the note <Term t="endsEarly" /> and you are paid out. Below the {CRASH_LINE} at a check, your money is at risk: you take the fall when the note ends.</>,
    },
  },
  payout: {
    protect: {
      label: 'Your loss is paid',
      sub: 'if there is a crash',
      Icon: HandCoins,
      title: 'Cover pays you the fall',
      text: <>If the price was below the {CRASH_LINE} at a weekly check and ends below the starting price, cover pays you the fall since the note’s first day. Fixed rules decide it: no model and no person.</>,
    },
    earn: {
      label: 'Paid back with income',
      sub: 'unless there is a crash',
      Icon: HandCoins,
      title: 'Earn pays you back, with the income',
      text: <>Without a crash you get the whole {POT}, which is more than you put in. In a crash, the fall goes to Protect and you get the rest. Fixed rules decide it: no model and no person.</>,
    },
  },
}

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
    <section ref={ref} id={id} aria-label="About this step" style={{ order }} className={`flex-col ${className}`}>
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
        </div>
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-2 pt-6">
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
 * Protect and Earn side by side, from what you bring to what you get paid, as one glass card. On
 * phones the chosen step opens right under its row; from lg it is the card's right-hand column.
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

  const node = (s: StepId, l: Lane, shape: string, tone: string, order?: number) => {
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
        className={`flex gap-x-4 gap-y-2 rounded-md border p-3 text-left transition-[border-color,box-shadow] duration-160 ease-out aria-pressed:border-accent aria-pressed:shadow-[inset_0_0_0_1px_var(--color-accent)] sm:p-4 ${shape}`}
      >
        {StepIcon && <StepIcon size={32} weight="duotone" aria-hidden="true" className={`shrink-0 ${tone}`} />}
        <span className="flex flex-col gap-0.5">
          <span className="type-heading text-ink">{label}</span>
          <span className="type-caption text-ink">{sub}</span>
        </span>
      </button>
    )
  }

  return (
    <div className="glass-strong mt-10 rounded-lg [--color-glass-fill-strong:var(--color-glass-fill)] lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
        <div className="grid grid-cols-2 content-start gap-x-3 p-4 sm:gap-x-4 sm:p-6">
          {(['protect', 'earn'] as const).map((l) => {
            const { name, what, Icon: LaneIcon, text } = LANES[l]
            return (
              <p key={l} className="flex items-center gap-2 pb-3">
                <LaneIcon size={24} weight="duotone" aria-hidden="true" className={`shrink-0 ${text}`} />
                <span className="flex flex-col">
                  <span className="type-label text-ink">{name}</span>
                  <span className="type-caption text-ink-muted">{what}</span>
                </span>
              </p>
            )
          })}

          {ORDER.flatMap((s) => {
            const order = rowOf(s)
            if (SHARED.includes(s)) return [node(s, 'protect', 'col-span-2 items-center border-line-strong/40 bg-ink/5 hover:border-line-strong', 'text-ink', order)]
            return (['protect', 'earn'] as const).map((l) => {
              if (!ARROWS.includes(s)) return node(s, l, `flex-col ${LANES[l].node}`, LANES[l].text, order)
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
            className={`col-span-2 mt-3 mb-1 scroll-mt-24 scroll-mb-4 border-y border-line py-4 lg:hidden ${picked ? 'flex' : 'hidden'}`}
          />
        </div>

        <Detail id="flow-step" lane={lane} step={step} onSelect={select} className="hidden border-l border-line p-6 lg:flex" />
    </div>
  )
}

import { CaretDown, Info, Wallet } from '@phosphor-icons/react'
import { ConnectButton } from '@rainbow-me/rainbowkit'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Address, Hex } from 'viem'
import { useAccount } from 'wagmi'
import {
  addsToDesk, bestCase, crashPayout, date, level, moveTo, noteOnOffer, observationsLeft, paused, pct, roomFor, signedPct, soldOut, tradeAmounts, usd, usdg, wholeUsdg,
} from '../../market/format.ts'
import { FEE_BPS, FEE_RECEIVER, SLIPPAGE_BPS } from '../../market/settings.ts'
import type { CollectKind, MarketData, SeriesView, TradeKind, TradeState } from '../../market/types.ts'
import { chain } from '../../wagmi.ts'
import type { Goal } from './LevelPicker.tsx'
import Notice from './Notice.tsx'
import { confirmed, refusalStatus, type Status } from './status.ts'
import Term from './Term.tsx'

export type Trade = {
  state: TradeState
  run: (p: { series: SeriesView; kind: TradeKind | CollectKind; amount: bigint; shown: bigint; slippageBps: number; feeBps: number; feeReceiver: Address }) => Promise<void>
  reset: () => void
}

/** An order that went through: what it was, and its transaction (none on a test run). */
export type Placed = { kind: TradeKind; hash?: Hex }

const KIND: Record<Goal, [TradeKind, TradeKind]> = { protect: ['buyCover', 'sellCover'], earn: ['buy', 'sell'] }
const ACTION: Record<TradeKind, [string, string]> = {
  buy: ['Buy NOTE', 'Buying NOTE…'],
  sell: ['Sell NOTE', 'Selling NOTE…'],
  buyCover: ['Buy cover', 'Buying cover…'],
  sellCover: ['Sell cover', 'Selling cover…'],
}

/**
 * Why the order can't go ahead, checked before anyone signs (prices don't check the Desk's limits).
 * `balance` is the wallet's USDG where it is known: a buy it can't pay for waits too, after the Desk's own reasons.
 */
function blocker(s: SeriesView, kind: TradeKind, amount: bigint, amountOk: boolean, market: MarketData, balance?: bigint): Status | null {
  // The page keeps the last amount while step 3 is edited: no order goes ahead for an amount that is no longer in the field.
  if (!amountOk) {
    return { tone: 'hold', icon: Info, label: 'Enter an amount in step 3', message: `What you see here is still for ${usdg(amount)} USDG, the last amount you entered. Enter a new amount in step 3 to go on.` }
  }
  const quote = { buy: s.noteAsk, sell: s.noteBid, buyCover: s.coverAsk, sellCover: s.coverBid }[kind]
  // Cover the Desk buys back counts against the note's limit like NOTE it sells (Desk._growth).
  // At the limit even its price is refused, so this comes before the price.
  if (kind === 'sellCover' && amount > noteOnOffer(s)) {
    return { ...refusalStatus({ error: 'CapExceeded', args: [] }), label: 'The Desk can’t buy this much cover back right now', message: `It can take back ${wholeUsdg(noteOnOffer(s))} USDG of it at most. Your cover stays valid either way: keep it until it ends, or try again later.` }
  }
  if (!quote.ok) return refusalStatus(quote.refusal, s)
  if (kind === 'buy' && amount > noteOnOffer(s)) {
    return { ...refusalStatus({ error: 'CapExceeded', args: [] }), message: `Only ${usdg(noteOnOffer(s), 0)} USDG of this note is on offer. Enter a smaller amount in step 3.` }
  }
  const cost = kind === 'buy' || kind === 'buyCover' ? tradeAmounts(kind, amount, quote.value, FEE_BPS).total : 0n
  const short: Status | null = balance !== undefined && balance < cost
    ? { tone: 'hold', icon: Wallet, label: 'Not enough USDG in your wallet', message: `This costs ${usdg(cost)} USDG, and your wallet has ${usdg(balance)} USDG. Add USDG to it, or enter a smaller amount in step 3.` }
    : null
  const adds = addsToDesk(s, kind, amount)
  if (adds === 0n) return short
  if (market.queue.waiting > 0n) return refusalStatus({ error: 'QueuePending', args: [] })
  const room = roomFor(s, kind, market)
  if (room !== null && amount > room) {
    return {
      ...refusalStatus({ error: 'RiskBudgetExceeded', args: [] }),
      label: kind === 'buyCover'
        ? `Right now, at most ${wholeUsdg(room)} USDG of ${s.symbol} can be protected`
        : `Right now, at most ${wholeUsdg(room)} USDG more can go into ${s.symbol}`,
      message: 'How much is sold on one coin or stock is limited, so that every payout can always be paid. Enter a smaller amount in step 3.',
    }
  }
  return short
}

/** Where to look a transaction up. The dev node has no explorer: there it's the transaction's hash. */
export function TxLink({ hash }: { hash: Hex }) {
  return chain.blockExplorers ? (
    <a href={`${chain.blockExplorers.default.url}/tx/${hash}`} target="_blank" rel="noreferrer" className="type-label text-ink underline">
      See it on the block explorer
    </a>
  ) : (
    <span className="type-code break-all text-ink-muted">{hash}</span>
  )
}

/** One scenario: the case and what comes back in one line, with the full story behind a click. */
function Outcome({ when, get, gain, children }: { when: string; get: string; gain: boolean; children: ReactNode }) {
  return (
    <li>
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-3 py-3 [&::-webkit-details-marker]:hidden">
          <span className="flex min-w-0 flex-1 flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5">
            <span className="type-body text-ink">{when}</span>
            <span className={`type-data ${gain ? 'text-go' : 'text-ink'}`}>{get}</span>
          </span>
          <CaretDown size={16} weight="bold" aria-hidden="true" className="shrink-0 text-ink-muted transition-transform duration-240 group-open:rotate-180" />
        </summary>
        <p className="pr-7 pb-3 type-body text-ink-muted">{children}</p>
      </details>
    </li>
  )
}

/**
 * The last step: what it costs now and what comes back in each case, in real money, then the button.
 * An order that went through is not sent twice: it is reported with `onPlaced` and handed back as
 * `placed` for as long as the steps above stay as they are, and until then the button rests. What
 * was bought has its own button to `onView` it in My positions, which is also where a holding is
 * sold from: the form itself sells only a sold-out note, where there is nothing to buy. `balance`
 * is the wallet's USDG, unknown without a wallet and in the prototype.
 */
export default function Order({ s, goal, amount, amountOk, market, trade, placed, onPlaced, onView, balance }: {
  s: SeriesView; goal: Goal; amount: bigint; amountOk: boolean; market: MarketData; trade: Trade
  placed: Placed | null; onPlaced: (placed: Placed | null) => void; onView: () => void; balance?: bigint
}) {
  const { isConnected } = useAccount()
  // A sold-out note can only be sold back, so its order opens on selling. Only where it opens:
  // the market is read again every 15 s, and the form mustn't change sides under the reader.
  const out = soldOut(s, goal === 'earn')
  const [selling, setSelling] = useState(placed ? placed.kind === KIND[goal][1] : out)

  const kind = KIND[goal][selling ? 1 : 0]
  const quote = { buy: s.noteAsk, sell: s.noteBid, buyCover: s.coverAsk, sellCover: s.coverBid }[kind]
  const amounts = quote.ok ? tradeAmounts(kind, amount, quote.value, FEE_BPS) : null
  const busy = ['waiting', 'quoting', 'approving', 'trading'].includes(trade.state.step)
  // Only an order sent from this form is its own: the trade's status is shared with My positions.
  const sent = useRef(false)
  useEffect(() => {
    if (!sent.current || trade.state.step !== 'done') return
    sent.current = false
    onPlaced({ kind, hash: trade.state.hash })
  }, [kind, onPlaced, trade.state])
  const done = placed !== null || trade.state.step === 'done'
  // While it runs and once it went through, what it takes from the wallet is no reason to stop it.
  const stop = blocker(s, kind, amount, amountOk, market, done || busy ? undefined : balance)
  // A paused price comes back by itself, so the order stays as it is. Its button rests and says
  // that it waits; the notice above it says for what.
  const wait = amountOk && paused(quote) && !done
  const hash = placed ? placed.hash : trade.state.hash
  const [label, busyLabel] = ACTION[kind]
  const sym = s.symbol
  const start = usd(s.state.initialFixing)
  const trigger = usd(level(s.state.initialFixing, s.terms.kiBarrierBps))
  const end = date(s.state.maturity)
  // The worked example: the stock ends at its crash line, or, once that line is crossed, where it is today.
  const hit = s.state.knockedIn
  const today = hit && s.spot !== null && s.spot < s.state.initialFixing
  const endPrice = today && s.spot !== null ? s.spot : level(s.state.initialFixing, s.terms.kiBarrierBps)
  const endsAt = today ? `today’s ${usd(endPrice)}` : usd(endPrice)
  const example = crashPayout(s, amount, endPrice)
  const weekly = (amount * BigInt(s.terms.couponBpsPerPeriod)) / 10_000n
  const perWeek = usdg(weekly)
  const started = date(s.terms.strikeTime)
  // Where the starting price sits against today: at or above it at a weekly check, the note ends early.
  const gap = s.spot === null ? null : moveTo(s.spot, s.state.initialFixing)
  const startVsToday = gap === null ? '' : gap > 0.005 ? `That’s ${pct(gap * 10_000, 1)} above today’s price. ` : gap < -0.005 ? `${sym} is above that today. ` : `That’s about today’s price. `
  // Ending early at check i: cover gets back 0.25% for each week from i to the end date, NOTE
  // gets its amount plus 0.25% for each week up to i. The next check gives the figures to show.
  const checksLeft = observationsLeft(s)
  const nextCheck = date(s.state.nextObservation)
  const refund = usdg(weekly * BigInt(checksLeft))
  const earlyNote = usdg(amount + weekly * BigInt(s.state.observationsDone + 1))
  const best = usdg(bestCase(s, amount))
  const earlyWhen = `${sym} is at ${start} or higher at a weekly check`
  const startingPrice = <>its price on {started} when this note started (the <Term t="startingPrice" />)</>
  // Two or three cases, each with its outcome up front and the story behind a click.
  // Money coming back carries a plus and the fall a minus, each next to the words that say what it is.
  // Already below the crash line today, there is no fall left to name.
  const fall = s.spot === null ? 0 : moveTo(s.spot, level(s.state.initialFixing, s.terms.kiBarrierBps))
  const crash = fall <= -0.0005 ? ` (${signedPct(fall)} or more)` : ''
  const outcomes: { when: string; get: string; more: ReactNode }[] = goal === 'protect'
    ? [
        hit
          ? {
              when: `${sym} ends at ${endsAt}`,
              get: `+${usdg(example.cover)} USDG`,
              more: (
                <>
                  {sym} has already crashed, so this cover is switched on. On {end} you’re paid the percentage {sym} is below{' '}
                  {start}, {startingPrice}: more if it ends lower, up to {usdg(amount)} USDG, and less if it ends higher.
                  {amounts && example.cover < amounts.total && ` At today’s price that’s less than you pay now, so this cover is only worth it if ${sym} falls further.`}
                </>
              ),
            }
          : {
              when: `${sym} crashes and stays down${crash}`,
              get: `+${usdg(example.cover)} USDG or more`,
              more: (
                <>
                  A crash means it closes below {trigger} at a weekly check. You’re then paid on {end}, not on the day of the
                  crash: the percentage {sym} is below {start}, {startingPrice}. That’s {usdg(example.cover)} USDG if it ends
                  at {trigger} ({pct(10_000 - s.terms.kiBarrierBps, 0)} lower), more if it ends lower, up to {usdg(amount)}{' '}
                  USDG. If it has partly recovered by then, it’s less.
                </>
              ),
            },
        hit
          ? { when: `${sym} ends at ${start} or higher`, get: 'Nothing back', more: <>The fall is measured from {start}. At or above it, there’s nothing to pay out.</> }
          : { when: `${sym} doesn’t crash`, get: 'Nothing back', more: <>It never closes below {trigger} at a weekly check, so the cover never switches on. What you paid is the cost of being insured.</> },
        ...(checksLeft > 0 ? [{
          when: earlyWhen,
          get: `Ends early, up to +${refund} USDG`,
          more: (
            <>
              {startVsToday}The next check is on {nextCheck}. Your cover <Term t="endsEarly">ends early</Term> that day, and
              you’re no longer covered. You get back part of what you paid: {perWeek} USDG ({pct(s.terms.couponBpsPerPeriod)}{' '}
              of your amount) for every week that was left, so {refund} USDG at the next check and less at a later one.
            </>
          ),
        }] : []),
      ]
    : [
        hit
          ? {
              when: `${sym} is back at ${start} by the end`,
              get: `+${best} USDG`,
              more: <>You get your {usdg(amount)} USDG plus the weekly income: {best} USDG if that’s on {end}, a little less if the note <Term t="endsEarly">ends early</Term> before.</>,
            }
          : {
              when: `${sym} doesn’t crash`,
              get: `+${best} USDG`,
              more: <>It never closes below {trigger} at a weekly check. On {end} you get your {usdg(amount)} USDG plus {perWeek} USDG for every week.</>,
            },
        hit
          ? {
              when: `${sym} ends at ${endsAt}`,
              get: `+${usdg(example.note)} USDG`,
              more: <>Its crash line is already crossed, so you get back the share of your amount that {sym} has kept since {start}, {startingPrice}, plus the income. The lower it ends, the less you get.</>,
            }
          : {
              when: `${sym} crashes and stays down${crash}`,
              get: `+${usdg(example.note)} USDG or less`,
              more: (
                <>
                  A crash means it closes below {trigger} at a weekly check. If it’s still below {start}, {startingPrice}, on{' '}
                  {end}, you get back the share of your amount that {sym} has kept, plus the income: {usdg(example.note)} USDG
                  if it ends at {trigger}, and less the lower it ends. If it’s back at {start} by then, you get the full {best} USDG.
                </>
              ),
            },
        ...(checksLeft > 0 ? [{
          when: earlyWhen,
          get: `Ends early, +${earlyNote} USDG or more`,
          more: (
            <>
              {startVsToday}The next check is on {nextCheck}. The note <Term t="endsEarly">ends early</Term> that day: you get
              your {usdg(amount)} USDG back plus {perWeek} USDG for every week since it started, so {earlyNote} USDG at the
              next check.
            </>
          ),
        }] : []),
      ]
  const token = goal === 'protect' ? 'cover' : 'NOTE'

  const submit = () => {
    if (stop || !amounts) return
    sent.current = true
    void trade.run({ series: s, kind, amount, shown: amounts.total, slippageBps: SLIPPAGE_BPS, feeBps: FEE_BPS, feeReceiver: FEE_RECEIVER })
  }

  return (
    <div className="flex flex-col gap-5">
      {out && <Notice status={{ tone: 'info', icon: Info, label: 'Sold out', message: 'All of this note is sold, so there’s none left to buy right now. If you hold some, you can sell it back here.' }} />}
      <div className="flex flex-col gap-4 rounded-md bg-surface-well p-4" aria-live="polite">
        <p className="type-body text-ink-muted">
          {selling
            ? `You sell ${usdg(amount)} USDG of ${sym} ${token} back, at today’s price.`
            : goal === 'protect'
              ? `Cover for ${usdg(amount)} USDG of ${sym}, until ${end}.`
              : `Income on ${usdg(amount)} USDG of ${sym}, until ${end}.`}
        </p>
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <span className="type-label text-ink">{selling ? 'You get now' : 'You pay now'}</span>
          <span className="flex items-baseline gap-2">
            <span className="type-readout text-ink">{amounts ? usdg(amounts.total) : '—'}</span>
            {amounts && <span className="type-label text-ink-muted">USDG</span>}
          </span>
        </div>
        {amounts && amounts.fee > 0n && (
          <p className="-mt-3 flex flex-wrap items-center gap-x-1 type-label text-ink-muted">
            {goal === 'protect' && !selling ? <Term t="premium" /> : 'Price'} {usdg(amounts.gross)} {selling ? '−' : '+'}{' '}
            <Term t="fee" /> {usdg(amounts.fee)}
          </p>
        )}
        {amounts && goal === 'protect' && !selling && (
          <p className="-mt-3 type-label text-ink-muted">One payment. There’s nothing more to pay later.</p>
        )}

        {!selling && (
          <div className="flex flex-col border-t border-line pt-4">
            <h3 className="type-label text-ink">What you get back</h3>
            <ul className="divide-y divide-line">
              {outcomes.map((o) => <Outcome key={o.when} when={o.when} get={o.get} gain={o.get.includes('+')}>{o.more}</Outcome>)}
            </ul>
          </div>
        )}
      </div>

      {stop && <Notice status={stop} />}
      {trade.state.step === 'waiting' && (
        <Notice status={{ tone: 'hold', icon: Info, label: 'An earlier request is still open in your wallet', message: 'It belongs to the order you changed. Reject it in your wallet, and this order goes on by itself.' }} />
      )}
      {trade.state.step === 'failed' && trade.state.refusal && <Notice status={refusalStatus(trade.state.refusal, s)} />}
      {done && (
        <Notice status={hash
          ? { ...confirmed, message: `${selling ? 'The USDG is' : 'It’s'} in your wallet. Change a step above to ${selling ? 'sell' : 'buy'} again.` }
          : { ...confirmed, label: 'Practice run done', message: 'This is the prototype, so nothing was bought and your wallet wasn’t asked for anything. Change a step above to run it again.' }}>
          {hash && <TxLink hash={hash} />}
        </Notice>
      )}
      {/* What was just bought, one click away. Not the orange of the order's own button: that one buys. */}
      {done && hash && !selling && (
        <button type="button" onClick={onView} className="h-12 rounded-full bg-ink px-6 type-button text-surface transition-[background-color] duration-160 ease-out hover:bg-ink/85 active:bg-ink/75">
          View position
        </button>
      )}

      {!isConnected ? (
        <ConnectButton.Custom>
          {({ openConnectModal }) => (
            <button type="button" onClick={openConnectModal} className="h-12 rounded-full bg-accent px-6 type-button text-on-accent transition-[background-color,box-shadow] duration-160 ease-out hover:bg-accent-hover hover:shadow-ignition active:bg-accent-pressed">
              Connect your wallet to {selling ? 'sell' : 'buy'}
            </button>
          )}
        </ConnectButton.Custom>
      ) : (
        <button
          type="button"
          onClick={submit}
          disabled={!!stop || busy || done}
          aria-busy={busy || undefined}
          className="h-12 rounded-full bg-accent px-6 type-button text-on-accent transition-[background-color,box-shadow] duration-160 ease-out hover:bg-accent-hover hover:shadow-ignition active:bg-accent-pressed disabled:cursor-not-allowed disabled:bg-surface-overlay disabled:text-ink-faint disabled:shadow-none"
        >
          {busy ? (trade.state.step === 'approving' ? 'Confirm in your wallet…' : trade.state.step === 'waiting' ? 'Waiting for your wallet…' : busyLabel) : wait ? 'Waiting for a price…' : label}
        </button>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 type-caption text-ink-muted">
        <span className="inline-flex items-center gap-1"><Term t="slippage">Slippage limit</Term>: 0.5%</span>
        {/* A form that opened on selling stays on it while the market is re-read: once the note can be bought again, this leads back. */}
        {selling ? !out && (
          <button type="button" onClick={() => { setSelling(false); trade.reset(); onPlaced(null) }} className="rounded-full px-2 py-1 underline transition-colors duration-160 hover:text-ink">
            Buy {token} instead
          </button>
        ) : (
          <button type="button" onClick={onView} className="rounded-full px-2 py-1 underline transition-colors duration-160 hover:text-ink">
            Already hold {token}? Sell it in My Positions
          </button>
        )}
      </div>
    </div>
  )
}

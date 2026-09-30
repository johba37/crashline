import { ConnectButton } from '@rainbow-me/rainbowkit'
import { useState, type ReactNode } from 'react'
import { zeroAddress, type Address } from 'viem'
import { useAccount } from 'wagmi'
import { robinhoodTestnet } from 'wagmi/chains'
import {
  addsToDesk, bestCase, coverRoom, crashExample, date, level, noteOnOffer, parseAmount, pct, tradeAmounts, usd, usdg,
} from '../../market/format.ts'
import type { MarketData, SeriesView, TradeKind, TradeState } from '../../market/types.ts'
import InfoTip from '../Tooltip.tsx'
import AmountField from './AmountField.tsx'
import { GLOSSARY } from './glossary.ts'
import type { Goal } from './LevelPicker.tsx'
import Notice from './Notice.tsx'
import { confirmed, refusalStatus, type Status } from './status.ts'
import Term from './Term.tsx'

// Our integrator fee, agreed with the Desk curator (plan 0.4); 0 until then.
const FEE_BPS = Number(import.meta.env.VITE_FEE_BPS ?? 0)
const FEE_RECEIVER = (import.meta.env.VITE_FEE_RECEIVER ?? zeroAddress) as Address
const SLIPPAGE_BPS = 50

export type Trade = {
  state: TradeState
  run: (p: { series: SeriesView; kind: TradeKind; amount: bigint; slippageBps: number; feeBps: number; feeReceiver: Address }) => Promise<void>
  reset: () => void
}

const KIND: Record<Goal, [TradeKind, TradeKind]> = { protect: ['buyCover', 'sellCover'], earn: ['buy', 'sell'] }
const ACTION: Record<TradeKind, [string, string]> = {
  buy: ['Buy NOTE', 'Buying NOTE…'],
  sell: ['Sell NOTE', 'Selling NOTE…'],
  buyCover: ['Buy cover', 'Buying cover…'],
  sellCover: ['Sell cover', 'Selling cover…'],
}

/** Why the order can't go ahead, checked before anyone signs (prices don't check the Desk's limits). */
function blocker(s: SeriesView, kind: TradeKind, amount: bigint | null, market: MarketData): Status | null {
  const quote = { buy: s.noteAsk, sell: s.noteBid, buyCover: s.coverAsk, sellCover: s.coverBid }[kind]
  if (!quote.ok) return refusalStatus(quote.refusal)
  if (amount === null) return null
  if (kind === 'buy' && amount > noteOnOffer(s)) {
    return { ...refusalStatus({ error: 'CapExceeded', args: [] }), message: `The Desk has ${usdg(noteOnOffer(s), 0)} USDG of this note on offer. Choose a smaller amount.` }
  }
  const adds = addsToDesk(s, kind, amount)
  if (adds === 0n) return null
  if (market.queuedShares > 0n) return refusalStatus({ error: 'QueuePending', args: [] })
  if (adds > coverRoom(s)) {
    return { ...refusalStatus({ error: 'RiskBudgetExceeded', args: [] }), message: `The Desk can take on ${usdg(coverRoom(s), 0)} USDG more for ${s.symbol} right now. Choose a smaller amount.` }
  }
  return null
}

function Outcome({ when, children }: { when: ReactNode; children: ReactNode }) {
  return (
    <li className="flex flex-col gap-0.5">
      <span className="type-label text-ink">{when}</span>
      <span className="type-body text-ink-muted">{children}</span>
    </li>
  )
}

/** Step 4: how much, what it costs now, and what comes back in each case, in plain words. */
export default function Order({ s, goal, market, trade }: { s: SeriesView; goal: Goal; market: MarketData; trade?: Trade }) {
  const { isConnected } = useAccount()
  const [selling, setSelling] = useState(false)
  const [text, setText] = useState('1,000')
  const [touched, setTouched] = useState(false)

  const kind = KIND[goal][selling ? 1 : 0]
  const amount = parseAmount(text)
  const quote = { buy: s.noteAsk, sell: s.noteBid, buyCover: s.coverAsk, sellCover: s.coverBid }[kind]
  const amounts = quote.ok && amount ? tradeAmounts(kind, amount, quote.value, FEE_BPS) : null
  const stop = blocker(s, kind, amount, market)
  const busy = !!trade && ['quoting', 'approving', 'trading'].includes(trade.state.step)
  const [label, busyLabel] = ACTION[kind]
  const example = amount ? crashExample(s, amount) : null
  const start = usd(s.state.initialFixing)
  const trigger = usd(level(s.state.initialFixing, s.terms.kiBarrierBps))
  const token = goal === 'protect' ? 'cover' : 'NOTE'

  const submit = () => {
    setTouched(true)
    if (!trade || !amount || stop) return
    void trade.run({ series: s, kind, amount, slippageBps: SLIPPAGE_BPS, feeBps: FEE_BPS, feeReceiver: FEE_RECEIVER })
  }
  const edit = (update: () => void) => {
    update()
    trade?.reset()
  }

  return (
    <div className="flex flex-col gap-5">
      <AmountField
        label={selling ? `How much ${token} to sell` : goal === 'protect' ? 'How much to cover' : 'How much to put in'}
        info={<InfoTip label={GLOSSARY.amount[0]}>{GLOSSARY.amount[1]}</InfoTip>}
        unit="USDG"
        value={text}
        onChange={(v) => edit(() => setText(v))}
        onBlur={() => setTouched(true)}
        error={touched && amount === null ? 'Enter an amount above 0.' : undefined}
        helper={goal === 'protect'
          ? <><Term t="coverAvailable">Cover available</Term> for {s.symbol}: {usdg(coverRoom(s), 0)} USDG</>
          : <>On offer in this note: {usdg(noteOnOffer(s), 0)} USDG</>}
      />

      <div className="flex flex-col gap-4 rounded-md bg-surface-well p-4" aria-live="polite">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <span className="type-label text-ink">{selling ? 'You get now' : 'You pay now'}</span>
          <span className="type-title text-ink">{amounts ? `${usdg(amounts.total)} USDG` : '—'}</span>
        </div>
        {amounts && (
          <p className="-mt-3 flex flex-wrap items-center gap-x-1 type-caption text-ink-muted">
            {goal === 'protect' && !selling ? <Term t="premium" /> : 'Price'} {usdg(amounts.gross)} {selling ? '−' : '+'}{' '}
            <Term t="fee" /> {usdg(amounts.fee)}
          </p>
        )}

        {amount && example && !selling && (
          <ul className="flex flex-col gap-3 border-t border-line pt-4">
            {goal === 'protect' ? (
              <>
                <Outcome when={`If ${s.symbol} crashes`}>
                  It’s below {trigger} at a weekly check and ends below {start}. You get the fall: for example{' '}
                  {usdg(example.cover)} USDG if it ends at {trigger}, up to {usdg(amount)} USDG.
                </Outcome>
                <Outcome when="If there’s no crash">You get nothing back, like any insurance.</Outcome>
                <Outcome when={<>If it <Term t="endsEarly" /></>}>
                  {s.symbol} is back at {start} at a weekly check: you get a small refund of {pct(s.terms.couponBpsPerPeriod)} for each week left.
                </Outcome>
              </>
            ) : (
              <>
                <Outcome when={`If ${s.symbol} stays above ${trigger} at every check`}>
                  You get {usdg(bestCase(s, amount))} USDG on {date(s.state.maturity)}: your {usdg(amount)} plus the weekly income.
                  If it <Term t="endsEarly" />, you get a little less, sooner.
                </Outcome>
                <Outcome when={`If ${s.symbol} crashes`}>
                  It’s below {trigger} at a weekly check and ends below {start}: you get back its share plus the income,
                  for example {usdg(example.note)} USDG if it ends at {trigger}.
                </Outcome>
              </>
            )}
          </ul>
        )}
        {selling && amounts && (
          <p className="border-t border-line pt-4 type-body text-ink-muted">You hand {usdg(amount ?? 0n)} {token} back to the <Term t="desk" />.</p>
        )}
      </div>

      {stop && <Notice status={stop} />}
      {trade?.state.step === 'failed' && trade.state.refusal && <Notice status={refusalStatus(trade.state.refusal)} />}
      {trade?.state.step === 'done' && trade.state.hash && (
        <Notice status={confirmed}>
          <a href={`${robinhoodTestnet.blockExplorers.default.url}/tx/${trade.state.hash}`} target="_blank" rel="noreferrer" className="type-label text-ink underline">
            See it on the block explorer
          </a>
        </Notice>
      )}

      {!isConnected ? (
        <ConnectButton.Custom>
          {({ openConnectModal }) => (
            <button type="button" onClick={openConnectModal} className="h-12 rounded-full bg-accent px-6 type-button text-on-accent transition-[background-color,box-shadow] duration-160 ease-out hover:bg-accent-hover hover:shadow-ignition active:bg-accent-pressed">
              Connect your wallet to buy
            </button>
          )}
        </ConnectButton.Custom>
      ) : (
        <button
          type="button"
          onClick={submit}
          disabled={!trade || !!stop || !amount || busy}
          aria-busy={busy || undefined}
          className="h-12 rounded-full bg-accent px-6 type-button text-on-accent transition-[background-color,box-shadow] duration-160 ease-out hover:bg-accent-hover hover:shadow-ignition active:bg-accent-pressed disabled:cursor-not-allowed disabled:bg-surface-overlay disabled:text-ink-faint disabled:shadow-none"
        >
          {busy ? (trade?.state.step === 'approving' ? 'Confirm in your wallet…' : busyLabel) : label}
        </button>
      )}
      {!trade && isConnected && <p className="type-caption text-ink-muted">These are example prices, so buying is off until the contracts are live.</p>}

      <div className="flex flex-wrap items-center justify-between gap-2 type-caption text-ink-muted">
        <span className="inline-flex items-center gap-1"><Term t="slippage">Price protection</Term>: 0.5%</span>
        <button type="button" onClick={() => edit(() => setSelling(!selling))} className="rounded-full px-2 py-1 underline transition-colors duration-160 hover:text-ink">
          {selling ? `Buy ${token} instead` : `Already hold ${token}? Sell it`}
        </button>
      </div>
    </div>
  )
}

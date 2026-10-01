import { CaretDown, CheckCircle, Coins, FlagCheckered, Info, Pulse, ShieldCheck, TrendDown } from '@phosphor-icons/react'
import { ConnectButton } from '@rainbow-me/rainbowkit'
import { useState, type ReactNode } from 'react'
import { useAccount } from 'wagmi'
import { UNIT, bestCase, crashPayout, date, level, observationsLeft, usd, usdg } from '../../market/format.ts'
import { FEE_BPS, FEE_RECEIVER, SLIPPAGE_BPS } from '../../market/settings.ts'
import type { Position } from '../../market/types.ts'
import Notice from './Notice.tsx'
import type { Trade } from './Order.tsx'
import PositionGraph from './PositionGraph.tsx'
import StatusChip from './StatusChip.tsx'
import { confirmed, refusalStatus, type Status } from './status.ts'
import Term from './Term.tsx'

const BUTTON = 'h-12 rounded-full bg-accent px-6 type-button text-on-accent transition-[background-color,box-shadow] duration-160 ease-out hover:bg-accent-hover hover:shadow-ignition active:bg-accent-pressed disabled:cursor-not-allowed disabled:bg-surface-overlay disabled:text-ink-faint disabled:shadow-none'

/** USDG coming to you, with its sign: 400000000n -> "+400.00 USDG". */
const plus = (base: bigint) => `+${usdg(base)} USDG`

/** Everything a position row says, worked out from the note's state and the Desk's prices. */
function read(p: Position) {
  const { series: s, side, amount } = p
  const cover = side === 'cover'
  const sym = s.symbol
  const start = usd(s.state.initialFixing)
  const line = usd(level(s.state.initialFixing, s.terms.kiBarrierBps))
  const end = date(s.state.maturity)
  const ended = s.state.phase === 2
  const hit = s.state.knockedIn
  const weekly = (amount * BigInt(s.terms.couponBpsPerPeriod)) / 10_000n

  if (ended) {
    // A settled note pays NOTE its payout and cover the rest of what was locked in.
    const perNote = s.state.payoutPerNote
    const collect = (amount * (cover ? s.maxPayoutPerNote - perNote : perNote)) / UNIT
    const on = date(p.path[p.path.length - 1]?.time ?? s.state.maturity)
    const status: Status = s.state.autocalled
      ? { tone: 'go', icon: FlagCheckered, label: 'Ended early' }
      : { tone: 'neutral', icon: CheckCircle, label: 'Ended' }
    return {
      cover, ended, status, when: `Ended ${on}`, figure: plus(collect), figureLabel: 'To collect', money: collect, quoteOk: true,
      story: s.state.autocalled
        ? <>It <Term t="endsEarly">ended early</Term> on {on}, because {sym} was back at its starting price of {start} at a weekly check. {cover ? 'You get back part of what you paid.' : 'You get your amount back, plus the income up to that day.'}</>
        : <>It ran until its end date. {cover ? (collect > 0n ? 'Your cover pays out.' : `${sym} didn’t end in a crash, so there’s nothing to collect.`) : 'You get back what the note pays.'}</>,
      action: collect > 0n ? `Collect ${usdg(collect)} USDG` : null,
    }
  }

  // What selling it back today brings: the Desk's buying price.
  const bid = cover ? s.coverBid : s.noteBid
  const worth = bid.ok ? (amount * BigInt(bid.value)) / 10_000n : null
  const status: Status = hit
    ? cover ? { tone: 'go', icon: ShieldCheck, label: 'Switched on' } : { tone: 'abort', icon: TrendDown, label: 'In a crash' }
    : cover ? { tone: 'info', icon: Pulse, label: 'Covered' } : { tone: 'info', icon: Pulse, label: 'Earning' }
  const early = observationsLeft(s) > 0 ? <> If it’s at {start} or higher at a weekly check, the note <Term t="endsEarly">ends early</Term>.</> : null
  let story: ReactNode
  if (cover && hit) {
    const today = s.spot !== null && s.spot < s.state.initialFixing ? crashPayout(s, amount, s.spot).cover : 0n
    story = <>{sym} closed below {line} at a weekly check, so your cover is switched on. On {end} you’re paid the percentage {sym} is below {start}: {plus(today)} if it ends where it is today.{early}</>
  } else if (cover) {
    story = <>No crash so far. If {sym} closes below {line} at a weekly check, your cover switches on and pays on {end}.{early}</>
  } else if (hit) {
    const today = s.spot !== null ? crashPayout(s, amount, s.spot).note : 0n
    story = <>{sym} closed below {line} at a weekly check, so your money follows it down: {plus(today)} if it ends where it is today, the full {plus(bestCase(s, amount))} if it’s back at {start} on {end}.</>
  } else {
    story = <>You earn {plus(weekly)} for every week. On {end} you get {plus(bestCase(s, amount))}, unless {sym} closes below {line} at a weekly check and stays down.{early}</>
  }
  return {
    cover, ended, status, when: `Until ${end}`, figure: worth === null ? '—' : `${usdg(worth)} USDG`, figureLabel: 'Worth now', money: worth, quoteOk: bid.ok,
    story, action: worth === null ? null : `Sell now for ${usdg(worth)} USDG`,
  }
}

/** One position: a line you can read at a glance, and behind a click its price graph, what happens next and what you can do. */
function Row({ p, now, trade, active, onAct }: { p: Position; now: number; trade: Trade; active: boolean; onAct: () => void }) {
  const { isConnected } = useAccount()
  const { series: s, side, amount } = p
  const r = read(p)
  const Icon = r.cover ? ShieldCheck : Coins
  const busy = active && ['quoting', 'approving', 'trading'].includes(trade.state.step)
  const bid = r.cover ? s.coverBid : s.noteBid

  const act = () => {
    onAct()
    void trade.run({ series: s, kind: r.cover ? 'sellCover' : 'sell', amount, slippageBps: SLIPPAGE_BPS, feeBps: FEE_BPS, feeReceiver: FEE_RECEIVER })
  }

  return (
    <li>
      <details className="panel group rounded-lg">
        <summary className="flex cursor-pointer list-none items-center gap-4 rounded-lg p-5 [&::-webkit-details-marker]:hidden">
          <Icon size={24} weight="bold" aria-hidden="true" className="shrink-0 text-ink-muted" />
          <span className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-6 gap-y-3">
            <span className="flex flex-col gap-1">
              <span className="type-heading text-ink">
                {r.cover ? `Cover for ${usdg(amount, 0)} USDG of ${s.symbol}` : `${usdg(amount, 0)} USDG earning on ${s.symbol}`}
              </span>
              <span className="flex flex-wrap items-center gap-x-3 gap-y-1 type-label text-ink-muted">
                <StatusChip status={r.status} />
                {r.when}
              </span>
            </span>
            <span className="flex flex-col sm:items-end">
              <span className="type-label text-ink-muted">{r.figureLabel}</span>
              <span className={`type-data ${r.ended && r.money ? 'text-go' : 'text-ink'}`}>{r.figure}</span>
              <span className="type-label text-ink-muted">You paid {usdg(p.paid)} USDG</span>
            </span>
          </span>
          <CaretDown size={20} weight="bold" aria-hidden="true" className="shrink-0 text-ink-muted transition-transform duration-240 group-open:rotate-180" />
        </summary>

        <div className="flex flex-col gap-5 border-t border-line p-5">
          <p className="type-body text-ink">{r.story}</p>
          <PositionGraph s={s} side={side} path={p.path} now={now} />
          {!r.ended && !bid.ok && <Notice status={refusalStatus(bid.refusal)} />}
          {active && trade.state.step === 'failed' && trade.state.refusal && <Notice status={refusalStatus(trade.state.refusal)} />}
          {active && trade.state.step === 'done' && (
            <Notice status={trade.state.hash ? confirmed : { ...confirmed, label: 'Test run done', message: 'Test mode is on, so nothing was sold and your wallet wasn’t asked for anything.' }} />
          )}
          {r.action && (!isConnected ? (
            <ConnectButton.Custom>
              {({ openConnectModal }) => (
                <button type="button" onClick={openConnectModal} className={BUTTON}>Connect your wallet to {r.ended ? 'collect' : 'sell'}</button>
              )}
            </ConnectButton.Custom>
          ) : (
            <button type="button" onClick={act} disabled={busy} aria-busy={busy || undefined} className={BUTTON}>
              {busy ? (trade.state.step === 'approving' ? 'Confirm in your wallet…' : r.ended ? 'Collecting…' : 'Selling…') : r.action}
            </button>
          ))}
          {!r.ended && r.action && (
            <p className="type-label text-ink-muted">Selling is optional: you can also keep it until it ends.</p>
          )}
        </div>
      </details>
    </li>
  )
}

/**
 * What the wallet holds, as a list: each row reads at a glance (what, state, worth now) and opens
 * to its price graph, what happens next and the one thing you can do with it.
 */
export default function Positions({
  positions, supported, now, trade, onBuy,
}: { positions: Position[]; supported: boolean; now: number; trade: Trade; onBuy: () => void }) {
  // The one trade hook serves every row: remember which row started it.
  const [active, setActive] = useState<string | null>(null)
  const key = (p: Position) => `${p.series.address}-${p.side}`

  if (!supported) {
    return <Notice status={{ tone: 'info', icon: Info, label: 'Not connected to live positions yet', message: 'This page can’t read a wallet’s positions from the chain yet. Switch on test mode above to see how they look.' }} />
  }
  if (positions.length === 0) {
    return (
      <Notice status={{ tone: 'neutral', icon: Info, label: 'You don’t hold anything yet', message: 'Once you buy cover or a NOTE, it shows up here.' }}>
        <button type="button" onClick={onBuy} className="self-start type-label text-ink underline">Protect a stock or earn from it</button>
      </Notice>
    )
  }
  return (
    <ul className="flex flex-col gap-4">
      {positions.map((p) => (
        <Row key={key(p)} p={p} now={now} trade={trade} active={active === key(p)} onAct={() => { trade.reset(); setActive(key(p)) }} />
      ))}
    </ul>
  )
}

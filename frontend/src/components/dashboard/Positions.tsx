import { CaretDown, CheckCircle, Coins, FlagCheckered, Info, ShieldCheck, TrendDown } from '@phosphor-icons/react'
import { ConnectButton } from '@rainbow-me/rainbowkit'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useAccount, useDisconnect } from 'wagmi'
import { UNIT, bestCase, crashPayout, date, level, observationsLeft, paused, pct, tradeAmounts, usd, usdg } from '../../market/format.ts'
import { endedAt } from '../../market/positions.ts'
import { FEE_BPS, FEE_RECEIVER, SLIPPAGE_BPS } from '../../market/settings.ts'
import type { Position } from '../../market/types.ts'
import type { PendingPosition } from '../../market/usePositions.ts'
import Notice from './Notice.tsx'
import type { Trade } from './Order.tsx'
import PositionGraph from './PositionGraph.tsx'
import StatusChip from './StatusChip.tsx'
import { confirmed, refusalStatus, type Status } from './status.ts'
import Term from './Term.tsx'
import TickerBadge from './TickerBadge.tsx'
import './Positions.css'

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
    const on = date(endedAt(s))
    // A note that ran to its end pays NOTE less than everything only after a crash: the stock closed
    // below its crash line at a weekly check and ended below its starting price. Cover is paid that fall.
    const lost = s.maxPayoutPerNote - perNote
    const crashed = !s.state.autocalled && lost > 0n
    const fall = Number(lost / 100n) // bps of the starting price
    const status: Status = s.state.autocalled
      ? { tone: 'go', icon: FlagCheckered, label: 'Ended early' }
      : cover
        ? { tone: crashed ? 'go' : 'neutral', icon: ShieldCheck, label: 'Protected' }
        : crashed ? { tone: 'abort', icon: TrendDown, label: 'Ended in a crash' } : { tone: 'neutral', icon: CheckCircle, label: 'Ended' }
    return {
      cover, ended, status, when: `Ended ${on}`, figure: plus(collect), figureLabel: 'To collect', money: collect, quoteOk: true,
      story: s.state.autocalled
        ? <>It <Term t="endsEarly">ended early</Term> on {on}, because {sym} was back at its starting price of {start} at a weekly check. {cover ? 'You get back part of what you paid.' : 'You get your amount back, plus the income up to that day.'}</>
        : crashed
          ? <>{sym} finished at {usd((s.state.initialFixing * (UNIT - lost)) / UNIT)}, {pct(fall, 1)} below its starting price of {start}, so {cover ? `your cover pays ${pct(fall, 1)} of what you protected.` : `you get back ${pct(10_000 - fall, 1)} of your amount, plus the weekly income.`}</>
          : <>It ran until its end date. {cover ? `${sym} didn’t end in a crash, so there’s nothing to collect.` : 'You get back what the note pays.'}</>,
      action: collect > 0n ? `Collect ${usdg(collect)} USDG` : null,
    }
  }

  // What selling it back today brings: the Desk's buying price, less the fee. The sale is held to this amount.
  const bid = cover ? s.coverBid : s.noteBid
  const worth = bid.ok ? tradeAmounts(cover ? 'sellCover' : 'sell', amount, bid.value, FEE_BPS).total : null
  // Each side carries its word and icon from step 2 of the buy flow (Protect, Earn). Cover reads
  // Protected at every stage but an early end; its colour and the sentence below say which stage.
  const status: Status = cover
    ? { tone: hit ? 'go' : 'info', icon: ShieldCheck, label: 'Protected' }
    : hit ? { tone: 'abort', icon: TrendDown, label: 'In a crash' } : { tone: 'info', icon: Coins, label: 'Earn' }
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
    cover, ended, status, when: `Until ${end}`, figure: worth === null ? (paused(bid) ? 'Waiting for a price' : '—') : `${usdg(worth)} USDG`, figureLabel: 'Worth now', money: worth, quoteOk: bid.ok,
    story, action: worth === null ? null : `Sell now for ${usdg(worth)} USDG`,
  }
}

/** Opened from the order that bought it: brought into view once, wherever the page was scrolled to. */
function useBroughtIntoView<T extends HTMLElement>(open: boolean) {
  const ref = useRef<T>(null)
  useEffect(() => {
    if (open) ref.current?.scrollIntoView({ block: 'center' })
  }, [open])
  return ref
}

// Ink at a tenth: a grey that shows on the panel in both themes (its own overlay colour doesn't).
const BAR = 'rounded-full bg-ink/10'

/**
 * A position that was bought and isn't in the list yet: its card as a skeleton, in the row's
 * layout (opened like the row will be), with a band of light over it while the list catches up.
 * When that takes too long the band stops and the reader can reload the list herself.
 */
function PendingRow({ pending: { series: s, side, stalled }, open, onReload }: { pending: PendingPosition; open: boolean; onReload: () => void }) {
  const card = useBroughtIntoView<HTMLDivElement>(open)
  return (
    <li>
      <div ref={card} role="status" aria-busy={!stalled} className={`panel panel-sheer rounded-lg ${stalled ? '' : 'position-shimmer'}`}>
        <div className="flex items-center gap-4 p-5">
          <TickerBadge symbol={s.symbol} />
          <span className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-6 gap-y-3">
            <span className="flex flex-col gap-1">
              <span className="type-heading text-ink">{side === 'cover' ? `Your new cover on ${s.symbol}` : `Your new NOTE on ${s.symbol}`}</span>
              <span className="type-label text-ink-muted">{stalled ? 'Taking longer than usual' : 'Adding it to your list…'}</span>
            </span>
            <span aria-hidden="true" className="flex flex-col gap-2 sm:items-end">
              <span className={`h-3 w-16 ${BAR}`} />
              <span className={`h-5 w-32 ${BAR}`} />
              <span className={`h-3 w-28 ${BAR}`} />
            </span>
          </span>
          <span className="w-5 shrink-0" />
        </div>
        {stalled ? (
          <div className="flex flex-col gap-4 border-t border-line p-5">
            <p className="type-body text-ink">Your order went through and it’s in your wallet. This list just hasn’t caught up yet.</p>
            <button type="button" onClick={onReload} className={BUTTON}>Reload position</button>
          </div>
        ) : open && (
          <div aria-hidden="true" className="flex flex-col gap-5 border-t border-line p-5">
            <span className="flex flex-col gap-2">
              <span className={`h-4 ${BAR}`} />
              <span className={`h-4 w-2/3 ${BAR}`} />
            </span>
            <span className="h-62 rounded-md bg-ink/10" />
            <span className={`h-12 ${BAR}`} />
          </div>
        )}
      </div>
    </li>
  )
}

/** One position: a line you can read at a glance, and behind a click its price graph, what happens next and what you can do. */
function Row({ p, now, trade, active, open, onAct }: { p: Position; now: number; trade: Trade; active: boolean; open: boolean; onAct: () => void }) {
  const { isConnected } = useAccount()
  const details = useBroughtIntoView<HTMLDetailsElement>(open)
  const { series: s, side, amount } = p
  const r = read(p)
  const busy = active && ['quoting', 'approving', 'trading'].includes(trade.state.step)
  const bid = r.cover ? s.coverBid : s.noteBid
  // A paused price comes back by itself. Until then the button stays, rests, and says that it
  // waits; the notice above it says for what.
  const wait = !r.ended && paused(bid)

  const act = () => {
    onAct()
    // A note that has ended is collected from the note itself; one that still runs is sold back to the Desk.
    const kind = r.ended ? (r.cover ? 'collectCover' : 'collect') : r.cover ? 'sellCover' : 'sell'
    void trade.run({ series: s, kind, amount, shown: r.money ?? 0n, slippageBps: SLIPPAGE_BPS, feeBps: FEE_BPS, feeReceiver: FEE_RECEIVER })
  }

  return (
    <li>
      <details ref={details} open={open} className="panel panel-sheer group rounded-lg">
        <summary className="flex cursor-pointer list-none items-center gap-4 rounded-lg p-5 [&::-webkit-details-marker]:hidden">
          <TickerBadge symbol={s.symbol} />
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
          {!r.ended && !bid.ok && (
            <Notice status={refusalStatus(bid.refusal, s)}>
              {wait && <p className="type-body text-ink">{r.cover ? 'Your cover stays valid' : 'Your NOTE keeps earning'} in the meantime. Only selling it back has to wait.</p>}
            </Notice>
          )}
          {active && trade.state.step === 'failed' && trade.state.refusal && <Notice status={refusalStatus(trade.state.refusal, s)} />}
          {active && trade.state.step === 'done' && (
            <Notice status={trade.state.hash ? confirmed : { ...confirmed, label: 'Practice run done', message: `This is the prototype, so nothing was ${r.ended ? 'collected' : 'sold'} and your wallet wasn’t asked for anything.` }} />
          )}
          {wait ? (
            <button type="button" disabled className={BUTTON}>Waiting for a price to sell…</button>
          ) : r.action && (!isConnected ? (
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
          {!r.ended && (r.action || wait) && (
            <p className="type-label text-ink-muted">Selling is optional: you can also keep it until it ends.</p>
          )}
        </div>
      </details>
    </li>
  )
}

/**
 * What the wallet holds, as a list: each row reads at a glance (what, state, worth now) and opens
 * to its price graph, what happens next and the one thing you can do with it. `open` names the
 * one that starts opened (series and side, as the rows are keyed): the one that was just bought.
 * Until the backend lists it, `pending` holds its place as a skeleton.
 */
export default function Positions({
  positions, pending, supported, needsWallet, isLoading, error, reload, open, now, trade, onBuy,
}: {
  positions: Position[]; pending: PendingPosition | null; supported: boolean; needsWallet: boolean; isLoading: boolean; error: Error | null
  reload: () => void; open: string | null; now: number; trade: Trade; onBuy: () => void
}) {
  // The one trade hook serves every row: remember which row started it.
  const [active, setActive] = useState<string | null>(null)
  const { disconnect } = useDisconnect()
  const key = (p: Pick<Position, 'series' | 'side'>) => `${p.series.address}-${p.side}`

  if (!supported) {
    return <Notice status={{ tone: 'info', icon: Info, label: 'Not connected to live positions yet', message: 'This page can’t read a wallet’s positions from the chain yet. Switch to Prototype above to see how they look.' }} />
  }
  if (needsWallet) {
    return (
      <Notice status={{ tone: 'info', icon: Info, label: 'Connect your wallet to see what you hold', message: 'Your positions are looked up by your wallet’s address. Nothing is asked of your wallet for that.' }}>
        <ConnectButton.Custom>
          {({ openConnectModal }) => (
            <button type="button" onClick={openConnectModal} className="self-start type-label text-ink underline">Connect your wallet</button>
          )}
        </ConnectButton.Custom>
      </Notice>
    )
  }
  if (isLoading) return <p className="type-body text-ink-muted">Reading your positions…</p>
  if (error) {
    return (
      <Notice status={{ tone: 'abort', icon: Info, label: 'Can’t read your positions', message: error.message }}>
        <button type="button" onClick={reload} className="self-start type-label text-ink underline">Try again</button>
      </Notice>
    )
  }
  // A position that was sold or collected leaves the list, and its row's "Done" with it: say it here.
  const closed = active !== null && trade.state.step === 'done' && !positions.some((p) => key(p) === active) && (
    <Notice status={{ ...confirmed, message: 'The USDG is in your wallet, so this position is no longer in your list.' }} />
  )
  if (positions.length === 0 && !pending) {
    return (
      <>
        {closed}
        <Notice status={{ tone: 'neutral', icon: Info, label: 'No positions found for this wallet', message: 'Connect another wallet, or start a new position.' }}>
          <div className="flex flex-wrap gap-x-6 gap-y-1">
            {/* Disconnects: the page then asks for a wallet, as it does for a reader without one. */}
            <button type="button" onClick={() => disconnect()} className="type-label text-ink underline">Connect another wallet</button>
            <button type="button" onClick={onBuy} className="type-label text-ink underline">Protect or Earn</button>
          </div>
        </Notice>
      </>
    )
  }
  return (
    <>
      {closed}
      <ul className="flex flex-col gap-4">
        {/* A buy that adds to a position shows as pending in its place: the row's figures are the old ones. */}
        {positions.map((p) => pending && key(p) === key(pending) ? (
          <PendingRow key={key(p)} pending={pending} open={open === key(p)} onReload={reload} />
        ) : (
          <Row key={key(p)} p={p} now={now} trade={trade} active={active === key(p)} open={open === key(p)} onAct={() => { trade.reset(); setActive(key(p)) }} />
        ))}
        {pending && !positions.some((p) => key(p) === key(pending)) && (
          <PendingRow pending={pending} open={open === key(pending)} onReload={reload} />
        )}
      </ul>
    </>
  )
}

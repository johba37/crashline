import { Coins, Info, ShieldCheck } from '@phosphor-icons/react'
import { ConnectButton } from '@rainbow-me/rainbowkit'
import { useLayoutEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import AmountField from '../components/dashboard/AmountField.tsx'
import ChoiceCards from '../components/dashboard/ChoiceCards.tsx'
import Disclosure from '../components/dashboard/Disclosure.tsx'
import { GLOSSARY } from '../components/dashboard/glossary.ts'
import LevelPicker, { type Goal } from '../components/dashboard/LevelPicker.tsx'
import MarketList from '../components/dashboard/MarketList.tsx'
import ModelCard from '../components/dashboard/ModelCard.tsx'
import NetworkChip from '../components/dashboard/NetworkChip.tsx'
import Notice from '../components/dashboard/Notice.tsx'
import Order, { TxLink, type Placed, type Trade } from '../components/dashboard/Order.tsx'
import Positions from '../components/dashboard/Positions.tsx'
import { Calendar, PriceDetails } from '../components/dashboard/SeriesDetail.tsx'
import Step from '../components/dashboard/Step.tsx'
import ModeSwitch from '../components/dashboard/ModeSwitch.tsx'
import Term from '../components/dashboard/Term.tsx'
import TickerBadge from '../components/dashboard/TickerBadge.tsx'
import { refusalStatus, seriesStatus } from '../components/dashboard/status.ts'
import EarnRate from '../components/landing/EarnRate.tsx'
import InfoTip from '../components/Tooltip.tsx'
import { UNIT, bestCase, date, fromFeed, parseAmount, paused, roomFor, soldOut, span, toUnits, tradeAmounts, trigger, usd, usdg, wholeUsdg } from '../market/format.ts'
import { FEE_BPS } from '../market/settings.ts'
import type { SeriesView } from '../market/types.ts'
import { useMarket } from '../market/useMarket.ts'
import { usePositions } from '../market/usePositions.ts'
import { usePracticeTrade } from '../market/usePracticeTrade.ts'
import { type LateTrade, useTrade } from '../market/useTrade.ts'
import { API_URL } from '../wagmi.ts'
import Logo from '../components/Logo.tsx'
import Starfield from '../components/Starfield.tsx'
import Wordmark from '../components/Wordmark.tsx'

const NAMES: Record<string, string> = { TSLA: 'Tesla', NVDA: 'Nvidia', AAPL: 'Apple', ETH: 'Ethereum' }

/** What a trade did, in the order form's words: "bought cover for 1,000.00 USDG of TSLA". */
const did = ({ kind, amount, series }: LateTrade) => {
  const of = `${usdg(amount)} USDG of ${series.symbol}`
  return {
    buy: `bought ${of} NOTE`, sell: `sold ${of} NOTE back`, buyCover: `bought cover for ${of}`, sellCover: `sold cover for ${of} back`,
    collect: `collected what ${of} NOTE paid`, collectCover: `collected what your cover for ${of} paid`,
  }[kind]
}

/**
 * The dashboard as a guided flow, top to bottom. Nothing is filled in or chosen for the reader: each
 * step opens once the one before it is answered. Pick a stock, pick what you want (protect or
 * earn), say how much, pick how long, pick how much of a crash, then check and buy. The amount
 * comes before the choices, so every price on the page is real money for that amount. How long
 * and the level together pick one note: the choices are the notes the Desk has open, never free
 * values. Everything else waits in the closed details below, and every new word has an info icon.
 */
export default function AppPage() {
  const [params, setParams] = useSearchParams()
  // Prototype (?test=1): the example market with every kind of note, and a buy button that only
  // walks through the steps. Testnet, the default: only the notes that are really open.
  const test = params.get('test') === '1'
  const { data: market, isLoading, error, deployment, unreachable } = useMarket(test)
  const chainTrade = useTrade(deployment)
  const practiceTrade = usePracticeTrade()
  const trade: Trade = test ? practiceTrade : chainTrade
  // Two views of the page: the guided flow to buy, and what the wallet already holds.
  const view = params.get('view') === 'positions' ? 'positions' : 'buy'
  // The trade's status belongs to the order the reader has put together: another stock, goal or
  // note, the other view or test mode starts clean and leaves a run that is still going behind.
  // Keyed on what she chose, not on what the market offers, so a read that drops the note for a
  // moment doesn't take her order with it. A layout effect, so no order paints with another's status.
  const { reset } = trade
  const order = [test, view, params.get('stock'), params.get('goal'), params.get('series')].join('|')
  useLayoutEffect(() => {
    reset()
    return reset
  }, [reset, order])
  // The order that went through, with the steps it was put together from. A look at My positions
  // clears the trade's status but not this: it stays until the reader changes a step (dropped
  // here, before anything renders with it), and until then the order's button rests.
  const [placed, setPlaced] = useState<{ steps: string; order: Placed } | null>(null)
  const steps = [test, params.get('stock'), params.get('goal'), params.get('series')].join('|')
  if (placed && placed.steps !== steps) setPlaced(null)
  const held = usePositions(test, market, chainTrade.traded)
  const set = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(changes)) {
      if (v === null) next.delete(k)
      else next.set(k, v)
    }
    setParams(next, { replace: true, preventScrollReset: true })
  }
  const setTest = (on: boolean) => {
    const next = new URLSearchParams(params)
    next.set('test', on ? '1' : '0')
    next.delete('series') // the two markets don't share notes
    setParams(next, { replace: true, preventScrollReset: true })
  }
  const testSwitch = <ModeSwitch test={test} onChange={setTest} />
  const tab = (to: 'buy' | 'positions', label: string) => (
    <button
      type="button"
      aria-current={view === to ? 'page' : undefined}
      onClick={() => { trade.reset(); set({ view: to === 'buy' ? null : to, open: null }) }}
      className={`h-9 rounded-full px-4 type-label whitespace-nowrap transition-colors duration-160 ${view === to ? 'bg-surface-overlay text-ink shadow-[inset_0_0_0_1px_var(--color-line)]' : 'text-ink-muted hover:text-ink'}`}
    >
      {label}
    </button>
  )
  const tabs = <div className="flex flex-wrap items-center gap-1">{tab('buy', 'Protect or Earn')}{tab('positions', 'My Positions')}</div>
  // The amount starts empty. Once there is one, the last valid entry stays in force while the
  // field is being edited, so the steps below don't close and reopen with every keystroke.
  // Until the field holds an amount again it shows its error, and the order in step 6 waits.
  const [amountText, setAmountText] = useState('')
  const [entered, setAmount] = useState<bigint | null>(null)
  const [amountTouched, setAmountTouched] = useState(false)
  const amount = entered ?? 0n
  const detailAmount = entered ?? 1_000n * UNIT // the details below work before an amount is entered

  const all = market?.series ?? []
  const stocks = [...new Set(all.map((s) => s.symbol))]
  // Step 1 is the reader's: no stock is chosen until they pick one, even when there is only one.
  const stock = stocks.includes(params.get('stock') ?? '') ? params.get('stock')! : undefined
  const notes = all.filter((s) => s.symbol === stock)
  const goalParam = params.get('goal')
  const goal: Goal | undefined = stock && (goalParam === 'protect' || goalParam === 'earn') ? goalParam : undefined
  // The levels: notes on this stock with a price for the chosen side, from the deepest crash line up.
  // A sold-out note has no such price but stays a choice: whoever holds it sells it back in step 6.
  // So does the picked note while its price is paused (around a weekly check, or without a fresh
  // feed price): the order she put together stays open and step 6 says what it waits for.
  const ask = (s: SeriesView) => (goal === 'earn' ? s.noteAsk : s.coverAsk)
  // The note is chosen in two steps, how long (?time=) and then the level (?series=), and neither has a default.
  const picked = notes.find((s) => s.address === params.get('series'))
  const stops = notes
    .filter((s) => (ask(s).ok || soldOut(s, goal === 'earn') || (s === picked && paused(ask(s)))) && s.spot !== null)
    .sort((a, b) => (trigger(a).move ?? 0) - (trigger(b).move ?? 0))
  // With nothing to choose from, step 4 says why: the Desk's answer for the first note, which also knows its own pause before a weekly check.
  const firstAsk = notes[0] && ask(notes[0])
  const chosen = picked && stops.includes(picked) ? picked : undefined
  const detail = picked ?? chosen ?? notes[0]
  const spot = notes.find((s) => s.spot)?.spot
  // How long: the time each note still runs, in plain words, soonest first. Notes that end about
  // the same time share one choice, and the level step chooses between them.
  const now = market?.now ?? 0
  const left = (s: SeriesView) => span(s.state.maturity - now)
  const spans = [...new Set([...stops].sort((a, b) => a.state.maturity - b.state.maturity).map(left))]
  const timeParam = params.get('time')
  const chosenSpan = chosen ? left(chosen) : timeParam !== null && spans.includes(timeParam) ? timeParam : undefined
  const levels = stops.filter((s) => left(s) === chosenSpan)
  // A note in money, for the amount entered: what the cover costs (Protect), or the most it can earn (Earn).
  const money = (s: SeriesView) => {
    const price = ask(s)
    if (!price.ok) return null
    const cost = tradeAmounts(goal === 'earn' ? 'buy' : 'buyCover', amount, price.value, FEE_BPS).total
    return goal === 'earn' ? bestCase(s, amount) - cost : cost
  }

  // A span in money: its cheapest cover, or the most it can earn. The level step gives the exact figure.
  const best = (label: string) =>
    stops.filter((s) => left(s) === label).map(money).reduce<bigint | null>(
      (b, m) => (m === null ? b : b === null || (goal === 'earn' ? m > b : m < b) ? m : b), null)
  const several = (label: string) => stops.filter((s) => left(s) === label).length > 1
  // The note a span's card speaks for: the chosen one if it's in there, else its cheapest (or best-earning).
  const shownIn = (label: string) => {
    if (chosen && label === chosenSpan) return chosen
    const m = best(label)
    const inSpan = stops.filter((s) => left(s) === label)
    return inSpan.find((s) => money(s) === m) ?? inSpan[0]
  }
  // The usual pattern, said only while the choices on screen follow it. A sold-out length has no figure to compare.
  const spanMoney = spans.map(best).filter((m) => m !== null)
  const longerIsMore = spanMoney.every((m, i) => i === 0 || m >= spanMoney[i - 1])
  // A length without a figure is sold out, unless it is the picked note waiting for its price.
  const onHold = chosen !== undefined && paused(ask(chosen))
  const soldOutSpans = spans.filter((label) => best(label) === null && !(onHold && label === chosenSpan)).length
  // Cover on one stock is limited (the Desk's risk budget and its free USDG), which prices don't check: say so where the amount is entered.
  // No note is picked yet, so the amount is held against the note with the most room (none: no limit to name).
  const rooms = market ? notes.filter((s) => s.coverAsk.ok).map((s) => roomFor(s, 'buyCover', market)) : []
  const room = rooms.reduce<bigint | null>((most, r) => (most === null || r === null ? null : r > most ? r : most), rooms[0] ?? null)
  const overRoom = goal === 'protect' && room !== null && amount > room
  const ready = !!goal && entered !== null // steps 1 to 3 are answered

  return (
    <div className="relative isolate min-h-dvh text-ink">
      {/* The landing page's night sky, without a hero: as tall as the page and scrolling with it, under a
          ground that dims it more than the landing's (bg-sky-veil-app; solid in the light theme). */}
      <Starfield className="pointer-events-none absolute inset-0 -z-10 overflow-hidden bg-surface" />
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 bg-sky-veil-app" />
      {/* The capsule is as wide as the landing page's (max-w-app with the landing's side padding), not as the column below. */}
      <header className="sticky top-[max(0.75rem,env(safe-area-inset-top))] z-(--z-nav) px-4 sm:px-6 lg:px-8">
        <nav className="glass-strong mx-auto mt-3 flex h-14 max-w-app items-center justify-between gap-4 rounded-full pr-2 pl-5">
          <Link to="/" className="inline-flex items-center gap-2 text-ink">
            <Logo className="h-6 w-auto" />
            <Wordmark large />
          </Link>
          <div className="hidden shrink-0 md:block">{tabs}</div>
          <div className="flex min-w-0 items-center">
            {/* The network chip warns of a wrong network and switches the wallet, so RainbowKit's own chain button is
                off (and on a wrong network its button shows nothing: the chip shows the address then).
                When the bar runs short, the chip's name gives way: the tabs don't shrink, so they never wrap. */}
            <NetworkChip />
            <ConnectButton showBalance={false} chainStatus="none" accountStatus="address" />
          </div>
        </nav>
      </header>

      <main className="mx-auto flex max-w-3xl flex-col gap-4 px-4 pt-10 pb-24 sm:px-6">
        {/* Below md the capsule has no room for the tabs, so they sit here, next to the Testnet / Prototype switch
            (on a phone the switch wraps under them). */}
        <div className="-mt-6 flex flex-wrap items-center justify-between gap-2">
          <div className="md:hidden">{tabs}</div>
          <div className="ml-auto">{testSwitch}</div>
        </div>
        <div className="mb-2 flex flex-col gap-2">
          <h1 className="type-title text-ink">{view === 'positions' ? 'My positions' : 'Crash insurance for coins and stocks'}</h1>
          <p className="type-body-lg text-ink-muted">
            {view === 'positions'
              ? 'What you hold, what it’s worth today, and what happens next. Open one to see its price so far and what you can do.'
              : 'Insure what you hold: pay once, and get paid if it crashes. Or be the insurer and earn a weekly income.'}
          </p>
        </div>

        {/* The one trade the page can't show on its order: that order is no longer on screen. */}
        {chainTrade.late && (
          <Notice status={{ tone: 'hold', icon: Info, label: 'An earlier order went through', message: `You changed it here before it was finished, but it was confirmed in your wallet. So you ${did(chainTrade.late)}.` }}>
            <TxLink hash={chainTrade.late.hash} />
            <button type="button" onClick={chainTrade.dismissLate} className="self-start type-label text-ink underline">Got it</button>
          </Notice>
        )}

        {/* A position is told with its note's prices: without the market, the notices below say why there is none. */}
        {view === 'positions' && market && (
          <Positions {...held} open={params.get('open')} now={market.now} trade={trade} onBuy={() => set({ view: null, open: null })} />
        )}

        {!test && !market && !isLoading && !error && (
          <Notice
            status={unreachable
              ? { tone: 'hold', icon: Info, label: 'Can’t reach the backend', message: `Nothing answers at ${API_URL} (${unreachable.message}). If it runs behind an SSH tunnel, start the tunnel: this page then switches over by itself. Or switch to Prototype at the top to try the flow with example notes.` }
              : { tone: 'info', icon: Info, label: 'Nothing is live yet', message: 'The contracts aren’t on the test network yet. Switch to Prototype at the top to try the flow with example notes.' }}
          />
        )}
        {error && <Notice status={{ tone: 'abort', icon: Info, label: 'Can’t read the market', message: error.message }} />}
        {isLoading && <p className="type-body text-ink-muted">Reading the market…</p>}
        {view === 'buy' && market && all.length === 0 && (
          <Notice status={{ tone: 'neutral', icon: Info, label: 'Nothing open yet', message: 'The Desk hasn’t opened anything yet. Check back soon.' }} />
        )}

        {view === 'buy' && market && stocks.length > 0 && (
          <>
            <Step n={1} title="Pick a coin or stock" state={stock ? 'done' : 'current'}>
              <ChoiceCards
                label="Coin or stock"
                centerIcon
                value={stock}
                onChange={(v) => set({ stock: v })}
                choices={stocks.map((sym) => {
                  const price = all.find((s) => s.symbol === sym && s.spot)?.spot
                  return { value: sym, icon: <TickerBadge symbol={sym} />, title: NAMES[sym] ?? sym, aside: price ? usd(price) : undefined, body: 'Price right now' }
                })}
              />
              {stocks.length === 1 && (
                <p className="type-label text-ink-muted">Only {NAMES[stocks[0]] ?? stocks[0]} is set up on the test network for now. More coins and stocks can be added.</p>
              )}
            </Step>

            <Step n={2} title="What do you want to do?" state={!stock ? 'upcoming' : goal ? 'done' : 'current'} hint="First pick a coin or stock.">
              <ChoiceCards
                label="What you want to do"
                value={goal}
                onChange={(g) => set({ goal: g })}
                choices={[
                  {
                    value: 'protect',
                    icon: <ShieldCheck size={24} weight="bold" />,
                    title: 'Protect against a crash',
                    body: `Like insurance: you pay once now. If ${stock} crashes, you get paid.`,
                  },
                  {
                    value: 'earn',
                    icon: <Coins size={24} weight="bold" />,
                    title: 'Earn a weekly income',
                    // The rate differs from note to note, so no single figure stands here: the range, once it is live.
                    body: <>Be the insurer: you earn <EarnRate />. In a big crash, you get back less.</>,
                  },
                ]}
              />
              <p className="type-label text-ink-muted">
                Both are sides of the same agreement, called a <Term t="note" />: one side buys the insurance, the other is paid
                for giving it. Every payout is <Term t="fullyBacked" /> and paid in <Term t="usdg" />.
              </p>
            </Step>

            <Step
              n={3}
              title={!goal ? 'How much?' : goal === 'protect' ? `How much ${stock} do you want to protect?` : 'How much do you want to put in?'}
              state={!goal ? 'upcoming' : entered !== null ? 'done' : 'current'}
              hint="First choose what you want to do."
            >
              {goal && (
                <AmountField
                  placeholder="For example 1,000"
                  label={goal === 'protect' ? `Value of the ${stock} to protect` : 'Amount to put in'}
                  info={<InfoTip label={GLOSSARY.amount[0]}>{GLOSSARY.amount[1]}</InfoTip>}
                  unit="USDG"
                  value={amountText}
                  onChange={(v) => {
                    setAmountText(v)
                    const parsed = parseAmount(v)
                    if (parsed) setAmount(parsed)
                    trade.reset()
                    setPlaced(null)
                  }}
                  onBlur={() => setAmountTouched(true)}
                  error={(amountTouched || entered !== null) && parseAmount(amountText) === null
                    ? 'Enter an amount above 0.'
                    : overRoom && room !== null
                      ? `Right now, at most ${wholeUsdg(room)} USDG of ${stock} can be protected. Enter a smaller amount.`
                      : undefined}
                  helper={goal === 'protect'
                    ? `${spot && entered !== null ? `That’s about ${(toUnits(amount) / fromFeed(spot)).toLocaleString(undefined, { maximumSignificantDigits: 3 })} ${stock} at today’s ${usd(spot)}. ` : spot ? `One ${stock} is ${usd(spot)} today. ` : ''}The cover pays out in USDG. Your ${stock} stays where it is.`
                    : `You get it back when the note ends, plus the weekly income, unless ${stock} crashes.`}
                />
              )}
            </Step>

            <Step
              n={4}
              title={!goal ? 'How long?' : goal === 'protect' ? 'How long should the cover last?' : 'How long do you want to earn?'}
              state={!ready ? 'upcoming' : chosenSpan ? 'done' : 'current'}
            >
              {goal && ready && picked && !stops.includes(picked) && (
                <Notice status={{ ...seriesStatus(picked), label: 'The one you picked is paused right now', message: 'It has no price at the moment. The ones below can be bought now.' }} />
              )}
              {goal && ready && stops.length === 0 && firstAsk && (
                <Notice status={refusalStatus(firstAsk.ok ? { error: 'NotLive', args: [] } : firstAsk.refusal, notes[0])} />
              )}
              {goal && ready && stops.length > 0 && (
                <>
                  <ChoiceCards
                    label="How long"
                    value={chosenSpan}
                    // Another length is another set of notes, so the level is asked again.
                    onChange={(label) => { if (label !== chosenSpan) set({ time: label, series: null }) }}
                    choices={spans.map((label) => {
                      const s = shownIn(label)
                      const m = best(label)
                      return {
                        value: label,
                        title: label,
                        aside: m === null ? (soldOut(s, goal === 'earn') ? 'Sold out' : 'No price right now') : `${goal === 'earn' ? 'Earn up to' : several(label) ? 'From' : 'Costs'} ${usdg(m)} USDG`,
                        body: `Until ${date(s.state.maturity)}${s.state.knockedIn ? (goal === 'protect' ? ', already switched on' : ', already in a crash') : ''}`,
                      }
                    })}
                  />
                  <p className="type-label text-ink-muted">
                    {spanMoney.length === 0
                      ? soldOutSpans === spans.length ? `Every ${stock} note is sold out right now.` : 'The one you picked has no price right now: the last step says why, and when it’s back.'
                      : goal === 'protect'
                        ? `The cost is what you pay now, once, to protect ${usdg(amount, 0)} USDG of ${stock}. ${longerIsMore ? 'Longer cover costs more, because there’s more time for a crash.' : `It also depends on how far ${stock} has to fall, which is the next step.`}`
                        : `That’s the most your ${usdg(amount, 0)} USDG can earn by the end date. ${longerIsMore ? 'A longer note pays more weeks of income.' : `It also depends on how far ${stock} can fall, which is the next step.`}`}{' '}
                    {soldOutSpans > 0 && `Sold out means there’s none left to buy. Pick it only if you hold some and want to sell it back. `}
                    {goal === 'protect' && spans.some(several) && `“From” is the cheapest choice in the next step. `}
                    {spans.length === 1 && `Only one end date is open for ${stock} right now. `}
                    It can <Term t="endsEarly">end early</Term> if {stock} goes up: the last step shows when.
                  </p>
                </>
              )}
            </Step>

            <Step
              n={5}
              title={!goal ? 'How far can it fall?' : chosen?.state.knockedIn ? `${stock} has already fallen far enough` : chosen && (trigger(chosen).move ?? 0) > 0 ? `${stock} is below this crash line today` : goal === 'protect' ? `How far does ${stock} have to fall?` : `How far can ${stock} fall before your money is at risk?`}
              state={!ready || !chosenSpan ? 'upcoming' : chosen ? 'done' : 'current'}
            >
              {goal && ready && chosenSpan && (
                <LevelPicker goal={goal} stops={levels} selected={chosen} money={money} now={now} onSelect={(a) => set({ series: a, time: chosenSpan })} />
              )}
            </Step>

            <Step
              n={6}
              title={chosen && soldOut(chosen, goal === 'earn') ? 'Check and sell' : 'Check and buy'}
              state={goal && ready && chosen ? 'current' : 'upcoming'}
              hint="Last, you see what you pay and what you can get back."
            >
              {goal && ready && chosen && (
                <Order
                  key={`${chosen.address}-${goal}`} s={chosen} goal={goal} amount={amount} amountOk={parseAmount(amountText) !== null} market={market} trade={trade} placed={placed?.order ?? null} onPlaced={(order) => setPlaced(order && { steps, order })}
                  // ?open= names the position as the list keys its rows: the note and the side that was bought.
                  onView={() => set({ view: 'positions', open: `${chosen.address}-${goal === 'protect' ? 'cover' : 'note'}` })}
                />
              )}
            </Step>

            {detail && (
              <div className="mt-8 flex flex-col gap-4">
                <h2 className="type-heading text-ink">Want the details?</h2>
                <Disclosure title="How the price is made" hint="Both prices, the fair price, and the model behind them">
                  <div className="flex flex-col gap-8">
                    <PriceDetails s={detail} amount={detailAmount} />
                    <ModelCard s={detail} model={market.models[detail.listing.pricer]} />
                  </div>
                </Disclosure>
                <Disclosure title="This note’s calendar" hint={`The ${stock} note that started ${date(detail.terms.strikeTime)}: its prices in dollars and its weekly checks`}>
                  <Calendar s={detail} now={market.now} />
                </Disclosure>
                <Disclosure title="Everything that’s open" hint={`Every note on every coin and stock, with what ${usdg(detailAmount, 0)} USDG costs, including the ones paused right now`}>
                  <MarketList series={all} selected={detail.address} amount={detailAmount} />
                </Disclosure>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  )
}

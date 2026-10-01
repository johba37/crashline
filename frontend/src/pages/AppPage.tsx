import { Coins, Info, ShieldCheck } from '@phosphor-icons/react'
import { ConnectButton } from '@rainbow-me/rainbowkit'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import AmountField from '../components/dashboard/AmountField.tsx'
import ChoiceCards from '../components/dashboard/ChoiceCards.tsx'
import Disclosure from '../components/dashboard/Disclosure.tsx'
import { GLOSSARY } from '../components/dashboard/glossary.ts'
import LevelPicker, { type Goal } from '../components/dashboard/LevelPicker.tsx'
import MarketList from '../components/dashboard/MarketList.tsx'
import ModelCard from '../components/dashboard/ModelCard.tsx'
import Notice from '../components/dashboard/Notice.tsx'
import Order, { type Trade } from '../components/dashboard/Order.tsx'
import Positions from '../components/dashboard/Positions.tsx'
import { Calendar, PriceDetails } from '../components/dashboard/SeriesDetail.tsx'
import Step from '../components/dashboard/Step.tsx'
import Switch from '../components/dashboard/Switch.tsx'
import Term from '../components/dashboard/Term.tsx'
import { refusalStatus, seriesStatus } from '../components/dashboard/status.ts'
import InfoTip from '../components/Tooltip.tsx'
import { UNIT, bestCase, coverRoom, date, fromFeed, parseAmount, pct, span, toUnits, tradeAmounts, trigger, usd, usdg } from '../market/format.ts'
import { FEE_BPS, PLACEHOLDERS } from '../market/settings.ts'
import type { SeriesView } from '../market/types.ts'
import { useMarket } from '../market/useMarket.ts'
import { usePositions } from '../market/usePositions.ts'
import { usePracticeTrade } from '../market/usePracticeTrade.ts'
import { useTrade } from '../market/useTrade.ts'
import { API_URL } from '../wagmi.ts'

const NAMES: Record<string, string> = { TSLA: 'Tesla', NVDA: 'Nvidia', AAPL: 'Apple' }

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
  // Test mode: the example market with every kind of note, and a buy button that only walks through
  // the steps. Off: only the notes that are really open.
  const test = (params.get('test') ?? (PLACEHOLDERS ? '1' : '0')) === '1'
  const { data: market, isLoading, error, deployment, unreachable } = useMarket(test)
  const chainTrade = useTrade(deployment)
  const practiceTrade = usePracticeTrade()
  const trade: Trade = test ? practiceTrade : chainTrade
  // Two views of the page: the guided flow to buy, and what the wallet already holds.
  const view = params.get('view') === 'positions' ? 'positions' : 'buy'
  const { positions, supported } = usePositions(test)
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
  const testSwitch = (
    <Switch label="Test mode" hint="On: example stocks and notes, and nothing is bought. Off: only what is really open." checked={test} onChange={setTest} />
  )
  const tab = (to: 'buy' | 'positions', label: string) => (
    <button
      type="button"
      aria-current={view === to ? 'page' : undefined}
      onClick={() => { trade.reset(); set({ view: to === 'buy' ? null : to }) }}
      className={`h-9 rounded-full px-4 type-label whitespace-nowrap transition-colors duration-160 ${view === to ? 'bg-surface-overlay text-ink' : 'text-ink-muted hover:text-ink'}`}
    >
      {label}
    </button>
  )
  const tabs = <div className="flex items-center gap-1">{tab('buy', 'Buy')}{tab('positions', 'My positions')}</div>
  // The amount starts empty. Once there is one, the last valid entry stays in force while the
  // field is being edited, so the steps below don't close and reopen with every keystroke.
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
  const stops = notes
    .filter((s) => (goal === 'earn' ? s.noteAsk : s.coverAsk).ok && s.spot !== null)
    .sort((a, b) => (trigger(a).move ?? 0) - (trigger(b).move ?? 0))
  // The note is chosen in two steps, how long (?time=) and then the level (?series=), and neither has a default.
  const picked = notes.find((s) => s.address === params.get('series'))
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
    const price = goal === 'earn' ? s.noteAsk : s.coverAsk
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
  // The usual pattern, said only while the choices on screen follow it.
  const spanMoney = spans.map((label) => best(label) ?? 0n)
  const longerIsMore = spanMoney.every((m, i) => i === 0 || m >= spanMoney[i - 1])
  // Cover on one stock is limited (the Desk's risk budget), which prices don't check: say so where the amount is entered.
  const room = notes[0] ? coverRoom(notes[0]) : null
  const overRoom = goal === 'protect' && room !== null && amount > room
  const ready = !!goal && entered !== null // steps 1 to 3 are answered

  return (
    <div className="min-h-dvh bg-horizon text-ink">
      <header className="sticky top-[max(0.75rem,env(safe-area-inset-top))] z-(--z-nav) px-4 sm:px-6">
        <nav className="glass-strong mx-auto mt-3 flex h-14 max-w-3xl items-center justify-between gap-4 rounded-full pr-2 pl-5">
          <Link to="/" className="type-wordmark text-ink">Surrogate Pricer</Link>
          <div className="hidden sm:block">{tabs}</div>
          <ConnectButton showBalance={false} chainStatus="icon" accountStatus="address" />
        </nav>
      </header>

      <main className="mx-auto flex max-w-3xl flex-col gap-4 px-4 pt-10 pb-24 sm:px-6">
        {/* The capsule has no room for the two views on a phone, so they sit here, next to the test switch. */}
        <div className="-mt-6 flex items-center justify-between gap-2">
          <div className="sm:hidden">{tabs}</div>
          <div className="ml-auto">{testSwitch}</div>
        </div>
        <div className="mb-2 flex flex-col gap-2">
          <h1 className="type-title text-ink">{view === 'positions' ? 'My positions' : 'Protect a stock, or earn from it'}</h1>
          <p className="type-body-lg text-ink-muted">
            {view === 'positions'
              ? 'What you hold, what it’s worth today, and what happens next. Open one to see its price so far and what you can do.'
              : 'Worried that a stock you hold could crash? Insure it here: you pay once, and you get paid if it crashes. Or take the other side and earn a weekly income. You see what you pay and what you can get back before you buy anything.'}
          </p>
        </div>

        {view === 'positions' && (
          <Positions positions={positions} supported={supported} now={market?.now ?? 0} trade={trade} onBuy={() => set({ view: null })} />
        )}

        {view === 'buy' && !test && !market && !isLoading && !error && (
          <Notice
            status={unreachable
              ? { tone: 'hold', icon: Info, label: 'Can’t reach the backend', message: `Nothing answers at ${API_URL} (${unreachable.message}). If it runs behind an SSH tunnel, start the tunnel: this page then switches over by itself. Or switch on test mode at the top to try the flow with example notes.` }
              : { tone: 'info', icon: Info, label: 'Nothing is live yet', message: 'The contracts aren’t on the test network yet. Switch on test mode at the top to try the flow with example notes.' }}
          />
        )}
        {view === 'buy' && error && <Notice status={{ tone: 'abort', icon: Info, label: 'Can’t read the market', message: error.message }} />}
        {view === 'buy' && isLoading && <p className="type-body text-ink-muted">Reading the market…</p>}
        {view === 'buy' && market && all.length === 0 && (
          <Notice status={{ tone: 'neutral', icon: Info, label: 'Nothing open yet', message: 'The Desk hasn’t opened anything yet. Check back soon.' }} />
        )}

        {view === 'buy' && market && stocks.length > 0 && (
          <>
            <Step n={1} title="Pick a stock" state={stock ? 'done' : 'current'}>
              <ChoiceCards
                label="Stock"
                value={stock}
                onChange={(v) => set({ stock: v })}
                choices={stocks.map((sym) => {
                  const price = all.find((s) => s.symbol === sym && s.spot)?.spot
                  return { value: sym, title: sym, aside: price ? usd(price) : undefined, body: NAMES[sym] ? `${NAMES[sym]}, price right now` : 'Price right now' }
                })}
              />
              {stocks.length === 1 && (
                <p className="type-label text-ink-muted">Only {NAMES[stocks[0]] ?? stocks[0]} is set up on the test network for now. More stocks can be added.</p>
              )}
            </Step>

            <Step n={2} title="What do you want to do?" state={!stock ? 'upcoming' : goal ? 'done' : 'current'} hint="First pick a stock.">
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
                    aside: notes[0] ? `${pct(notes[0].terms.couponBpsPerPeriod)} a week` : undefined,
                    body: 'Be the insurer: you earn a fixed income for every week, paid at the end. In a big crash, you get back less.',
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
                  }}
                  onBlur={() => setAmountTouched(true)}
                  error={amountTouched && parseAmount(amountText) === null
                    ? 'Enter an amount above 0.'
                    : overRoom && room !== null
                      ? `Right now, at most ${usdg(room, 0)} USDG of ${stock} can be protected. Enter a smaller amount.`
                      : undefined}
                  helper={goal === 'protect'
                    ? `${spot && entered !== null ? `That’s about ${(toUnits(amount) / fromFeed(spot)).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${stock} shares at today’s ${usd(spot)}. ` : spot ? `One ${stock} share is ${usd(spot)} today. ` : ''}The cover pays out in USDG. Your shares stay where they are.`
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
              {goal && ready && stops.length === 0 && notes[0] && (
                <Notice status={notes[0].mid.ok ? refusalStatus({ error: 'NotLive', args: [] }) : refusalStatus(notes[0].mid.refusal)} />
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
                        aside: m === null ? undefined : `${goal === 'earn' ? 'Earn up to' : several(label) ? 'From' : 'Costs'} ${usdg(m)} USDG`,
                        body: `Until ${date(s.state.maturity)}${s.state.knockedIn ? (goal === 'protect' ? ', already switched on' : ', already in a crash') : ''}`,
                      }
                    })}
                  />
                  <p className="type-label text-ink-muted">
                    {goal === 'protect'
                      ? `The cost is what you pay now, once, to protect ${usdg(amount, 0)} USDG of ${stock}. ${longerIsMore ? 'Longer cover costs more, because there’s more time for a crash.' : `It also depends on how far ${stock} has to fall, which is the next step.`}`
                      : `That’s the most your ${usdg(amount, 0)} USDG can earn by the end date. ${longerIsMore ? 'A longer note pays more weeks of income.' : `It also depends on how far ${stock} can fall, which is the next step.`}`}{' '}
                    {goal === 'protect' && spans.some(several) && `“From” is the cheapest choice in the next step. `}
                    {spans.length === 1 && `Only one end date is open for ${stock} right now. `}
                    It can <Term t="endsEarly">end early</Term> if {stock} goes up: the last step shows when.
                  </p>
                </>
              )}
            </Step>

            <Step
              n={5}
              title={!goal ? 'How far can it fall?' : chosen?.state.knockedIn ? `${stock} has already fallen far enough` : goal === 'protect' ? `How far does ${stock} have to fall?` : `How far can ${stock} fall before your money is at risk?`}
              state={!ready || !chosenSpan ? 'upcoming' : chosen ? 'done' : 'current'}
            >
              {goal && ready && chosenSpan && (
                <LevelPicker goal={goal} stops={levels} selected={chosen} money={money} now={now} onSelect={(a) => set({ series: a, time: chosenSpan })} />
              )}
            </Step>

            <Step
              n={6}
              title="Check and buy"
              state={goal && ready && chosen ? 'current' : 'upcoming'}
              hint="Last, you see what you pay and what you can get back."
            >
              {goal && ready && chosen && <Order key={`${chosen.address}-${goal}`} s={chosen} goal={goal} amount={amount} market={market} trade={trade} />}
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
                <Disclosure title="Everything that’s open" hint={`Every note on every stock, with what ${usdg(detailAmount, 0)} USDG costs, including the ones paused right now`}>
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

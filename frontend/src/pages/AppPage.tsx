import { Coins, Info, ShieldCheck } from '@phosphor-icons/react'
import { ConnectButton } from '@rainbow-me/rainbowkit'
import { Link, useSearchParams } from 'react-router'
import ChoiceCards from '../components/dashboard/ChoiceCards.tsx'
import Disclosure from '../components/dashboard/Disclosure.tsx'
import LevelPicker, { type Goal } from '../components/dashboard/LevelPicker.tsx'
import MarketList from '../components/dashboard/MarketList.tsx'
import ModelCard from '../components/dashboard/ModelCard.tsx'
import Notice from '../components/dashboard/Notice.tsx'
import Order, { type Trade } from '../components/dashboard/Order.tsx'
import { Calendar, PriceDetails } from '../components/dashboard/SeriesDetail.tsx'
import Step from '../components/dashboard/Step.tsx'
import Term from '../components/dashboard/Term.tsx'
import { refusalStatus, seriesStatus } from '../components/dashboard/status.ts'
import { date, pct, per100, trigger, usd } from '../market/format.ts'
import type { SeriesView } from '../market/types.ts'
import { useMarket } from '../market/useMarket.ts'
import { useTrade } from '../market/useTrade.ts'

const NAMES: Record<string, string> = { TSLA: 'Tesla' }

const cheapest = (notes: SeriesView[], pick: (s: SeriesView) => SeriesView['coverAsk']) =>
  notes.reduce<number | null>((min, s) => {
    const q = pick(s)
    return q.ok && (min === null || q.value < min) ? q.value : min
  }, null)

/**
 * The dashboard as a guided flow, top to bottom: pick a stock, pick what you want (protect or
 * earn), pick how much of a crash, pick how much, then buy. Everything else waits in the
 * closed details below, and every new word has an info icon.
 */
export default function AppPage() {
  const { data: market, isLoading, error } = useMarket()
  const chainTrade = useTrade()
  const trade: Trade | undefined = market?.source === 'chain' ? chainTrade : undefined // example prices can't trade
  const [params, setParams] = useSearchParams()
  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(changes)) next.set(k, v)
    setParams(next, { replace: true, preventScrollReset: true })
  }

  const all = market?.series ?? []
  const stocks = [...new Set(all.map((s) => s.symbol))]
  const stock = stocks.includes(params.get('stock') ?? '') ? params.get('stock')! : stocks[0]
  const notes = all.filter((s) => s.symbol === stock)
  const goalParam = params.get('goal')
  const goal: Goal | undefined = goalParam === 'protect' || goalParam === 'earn' ? goalParam : undefined
  // The levels: notes on this stock with a price for the chosen side, from the deepest crash line up.
  const stops = notes
    .filter((s) => (goal === 'earn' ? s.noteAsk : s.coverAsk).ok && s.spot !== null)
    .sort((a, b) => (trigger(a).move ?? 0) - (trigger(b).move ?? 0))
  const picked = notes.find((s) => s.address === params.get('series'))
  const chosen = picked && stops.includes(picked) ? picked : stops[Math.floor((stops.length - 1) / 2)]
  const detail = picked ?? chosen ?? notes[0]
  const name = stock ? NAMES[stock] ?? stock : ''

  return (
    <div className="min-h-dvh bg-horizon text-ink">
      <header className="sticky top-[max(0.75rem,env(safe-area-inset-top))] z-(--z-nav) px-4 sm:px-6">
        <nav className="glass-strong mx-auto mt-3 flex h-14 max-w-3xl items-center justify-between gap-4 rounded-full pr-2 pl-5">
          <Link to="/" className="type-wordmark text-ink">Surrogate Pricer</Link>
          <ConnectButton showBalance={false} chainStatus="icon" accountStatus="address" />
        </nav>
      </header>

      <main className="mx-auto flex max-w-3xl flex-col gap-4 px-4 pt-10 pb-24 sm:px-6">
        <div className="mb-2 flex flex-col gap-2">
          <h1 className="type-title text-ink">Protect a stock, or earn from it</h1>
          <p className="type-body-lg text-ink-muted">
            Every <Term t="note" /> on a stock has two sides: crash insurance and a weekly income. Four steps, and you see
            what you pay and what you get back before you buy anything.
          </p>
        </div>

        {market?.source === 'fixtures' && (
          <Notice status={{ tone: 'info', icon: Info, label: 'Example prices', message: 'The contracts aren’t on the test network yet, so these notes and prices are examples and buying is switched off.' }} />
        )}
        {error && <Notice status={{ tone: 'abort', icon: Info, label: 'Can’t read the market', message: error.message }} />}
        {isLoading && <p className="type-body text-ink-muted">Reading the market…</p>}
        {market && all.length === 0 && (
          <Notice status={{ tone: 'neutral', icon: Info, label: 'No notes open yet', message: 'The Desk hasn’t opened any notes yet. Check back soon.' }} />
        )}

        {market && stock && (
          <>
            <Step n={1} title="Pick a stock" state="done">
              <ChoiceCards
                label="Stock"
                value={stock}
                onChange={(v) => set({ stock: v })}
                choices={stocks.map((sym) => {
                  const spot = all.find((s) => s.symbol === sym && s.spot)?.spot
                  const open = all.filter((s) => s.symbol === sym).length
                  return { value: sym, title: sym, aside: spot ? usd(spot) : undefined, body: `${NAMES[sym] ?? sym}, ${open} ${open === 1 ? 'note' : 'notes'} open` }
                })}
              />
              {stocks.length === 1 && (
                <p className="type-caption text-ink-muted">
                  Only {name} is set up on the test network for now. Each note names its own stock, so more can be added.
                </p>
              )}
            </Step>

            <Step n={2} title="What do you want to do?" state={goal ? 'done' : 'current'}>
              <ChoiceCards
                label="What you want to do"
                value={goal}
                onChange={(g) => set({ goal: g })}
                choices={[
                  {
                    value: 'protect',
                    icon: <ShieldCheck size={24} weight="bold" />,
                    title: 'Protect against a crash',
                    aside: (() => { const c = cheapest(notes, (s) => s.coverAsk); return c === null ? undefined : `from ${per100(c)} per 100` })(),
                    body: `Pay once now. If ${stock} crashes, you get paid.`,
                  },
                  {
                    value: 'earn',
                    icon: <Coins size={24} weight="bold" />,
                    title: 'Earn a weekly income',
                    aside: notes[0] ? `${pct(notes[0].terms.couponBpsPerPeriod)} a week` : undefined,
                    body: 'Get a fixed income every week. In a big crash, you get back less.',
                  },
                ]}
              />
              <p className="type-caption text-ink-muted">
                Protect buys <Term t="cover" />, Earn buys a <Term t="NOTE" />. They’re the two sides of the same note, and every
                payout is <Term t="fullyBacked" />. Prices are in <Term t="usdg" /> per 100.
              </p>
            </Step>

            <Step
              n={3}
              title={!goal ? 'Choose your level' : goal === 'protect' ? 'How big a crash should it cover?' : `How far can ${stock} fall before your money is at risk?`}
              state={goal ? 'done' : 'upcoming'}
              hint="First choose what you want to do."
            >
              {goal && picked && !stops.includes(picked) && (
                <Notice status={{ ...seriesStatus(picked), label: `The note from ${date(picked.terms.strikeTime)} has no price right now` }} />
              )}
              {goal && stops.length === 0 && notes[0] && (
                <Notice status={notes[0].mid.ok ? refusalStatus({ error: 'NotLive', args: [] }) : refusalStatus(notes[0].mid.refusal)} />
              )}
              {goal && stops.length > 0 && (
                <LevelPicker goal={goal} stops={stops} selected={chosen} onSelect={(a) => set({ series: a })} />
              )}
            </Step>

            <Step
              n={4}
              title={!goal ? 'How much?' : goal === 'earn' ? 'How much do you want to put in?' : 'How much do you want to cover?'}
              state={goal && chosen ? 'current' : 'upcoming'}
              hint="Then you see what you pay and what you get back."
            >
              {goal && chosen && market && <Order key={`${chosen.address}-${goal}`} s={chosen} goal={goal} market={market} trade={trade} />}
            </Step>

            {detail && (
              <div className="mt-8 flex flex-col gap-4">
                <h2 className="type-heading text-ink">Want the details?</h2>
                <Disclosure title="How the price is made" hint="Both prices, the fair price, and the model behind them">
                  <div className="flex flex-col gap-8">
                    <PriceDetails s={detail} />
                    <ModelCard s={detail} model={market.models[detail.listing.pricer]} />
                  </div>
                </Disclosure>
                <Disclosure title="This note’s calendar" hint={`The ${stock} note that started ${date(detail.terms.strikeTime)}: its prices in dollars and its weekly checks`}>
                  <Calendar s={detail} now={market.now} />
                </Disclosure>
                <Disclosure title="All open notes" hint="Every note on the market, including the ones paused right now">
                  <MarketList series={all} selected={detail.address} />
                </Disclosure>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  )
}

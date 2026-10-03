import { useQueries, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useAccount } from 'wagmi'
import { API_URL } from '../wagmi'
import { fixturePositions } from './fixtures.ts'
import { endedAt, fromAccount, fromHistory, reflects } from './positions.ts'
import { type MarketData, Phase, type Position, type SeriesView } from './types.ts'
import type { Traded } from './useTrade.ts'

// The stock moves with the feed, and a wallet's holdings with trades made elsewhere.
const REFETCH_MS = 15_000
// After a trade made here: until the backend has indexed its block (it looks every 2 s).
const CATCH_UP_MS = 1_000
// How long that may take before the reader is offered to reload the list herself.
const WAIT_MS = 10_000
// The graph's path: one point a day (the backend keeps the weekly checks and the latest price).
const PATH_STEP = 86_400

async function get(path: string, signal: AbortSignal): Promise<unknown> {
  const res = await fetch(`${API_URL}${path}`, { signal })
  if (!res.ok) throw new Error(`The backend answered ${res.status}.`)
  return res.json()
}

// A note that has ended is asked only up to its end: the stock has moved on since, the note hasn't.
const historyOf = (s: SeriesView) =>
  `/series/${s.address}/history?step=${PATH_STEP}${s.state.phase === Phase.Settled ? `&to=${endedAt(s)}` : ''}`

/** A position that was bought but isn't in the backend's answer yet. `stalled`: the wait for it ran out. */
export type PendingPosition = { series: SeriesView; side: Position['side']; stalled: boolean }

/**
 * What the wallet holds. Test mode: one example of each kind. Otherwise the backend's
 * /accounts/{address} (the holdings and what was paid) on the notes of `market`, delisted ones
 * too, each with its price path from /series/{address}/history (docs/backend.md). `traded` is the
 * last trade made here: the holdings are re-read quickly until they include its block, and what
 * it bought is `pending` until then, what it sold or collected `updating`. After 10 s the quick reads
 * stop and `reload` starts them again.
 * Without a backend there is no reader (`supported` is off), and without a wallet nobody to look
 * up (`needsWallet`).
 */
export function usePositions(test: boolean, market: MarketData | undefined, traded: Traded | null): {
  positions: Position[]
  pending: PendingPosition | null
  updating: PendingPosition | null
  supported: boolean
  needsWallet: boolean
  isLoading: boolean
  error: Error | null
  reload: () => void
} {
  const { address } = useAccount()
  const live = !test && API_URL !== undefined
  // The trade whose wait ran out, by its block: a later trade starts a wait of its own.
  const [gaveUp, setGaveUp] = useState<bigint | null>(null)
  const stalled = traded !== null && gaveUp === traded.block
  const account = useQuery({
    queryKey: ['account', API_URL, address],
    enabled: live && address !== undefined,
    queryFn: ({ signal }) => get(`/accounts/${address}`, signal),
    refetchInterval: ({ state }) =>
      traded !== null && !stalled && state.data !== undefined && !reflects(state.data, traded.block) ? CATCH_UP_MS : REFETCH_MS,
    retry: 1,
  })
  const behind = live && traded !== null && account.data !== undefined && !reflects(account.data, traded.block)
  const block = traded?.block
  useEffect(() => {
    if (!behind || stalled || block === undefined) return
    const timer = setTimeout(() => setGaveUp(block), WAIT_MS)
    return () => clearTimeout(timer)
  }, [behind, block, stalled])

  const held = useMemo(
    () => (live && address && account.data && market ? fromAccount(account.data, [...market.series, ...market.delisted]) : []),
    [account.data, address, live, market],
  )
  // The path only draws the graph: a position shows before it, and without it if it can't be read.
  const notes = [...new Map(held.map((p) => [p.series.address, p.series])).values()]
  const paths = useQueries({
    queries: notes.map((s) => ({
      queryKey: ['history', API_URL, historyOf(s)],
      queryFn: async ({ signal }: { signal: AbortSignal }) => fromHistory(await get(historyOf(s), signal), s.state.initialFixing),
      refetchInterval: REFETCH_MS,
    })),
  })

  const reload = () => {
    setGaveUp(null)
    void account.refetch()
  }
  if (test) return { positions: fixturePositions, pending: null, updating: null, supported: true, needsWallet: false, isLoading: false, error: null, reload }
  // Only a buy is on its way to the list: what was sold or collected is still in it, as it was
  // before, and leaves (or changes, after selling part of it).
  const bought = behind && (traded.kind === 'buy' || traded.kind === 'buyCover')
  return {
    positions: held.map((p) => ({ ...p, path: paths[notes.indexOf(p.series)]?.data ?? [] })),
    pending: bought ? { series: traded.series, side: traded.kind === 'buy' ? 'note' : 'cover', stalled } : null,
    updating: behind && !bought ? { series: traded.series, side: traded.kind === 'sell' || traded.kind === 'collect' ? 'note' : 'cover', stalled } : null,
    supported: live,
    needsWallet: live && address === undefined,
    isLoading: account.isLoading,
    error: account.error,
    reload,
  }
}

import { useMemo } from 'react'
import {
  type Abi,
  type Address,
  type ContractFunctionName,
  type ContractFunctionParameters,
  type ContractFunctionReturnType,
  erc20Abi,
  multicall3Abi,
  zeroAddress,
} from 'viem'
import { useBlock, useReadContracts, useSimulateContract } from 'wagmi'
import { robinhoodTestnet } from 'wagmi/chains'
import { chain } from '../wagmi'
import {
  aggregatorAbi,
  deskAbi,
  noteQuoterAbi,
  noteSeriesAbi,
  seriesTokenAbi,
  surrogatePricerAbi,
} from './abi'
import type { Deployment } from './deployments'
import { decodeRefusal } from './errors'
import type { MarketData, ModelView, PricerInputs, Refusable, SeriesView } from './types'

// Quotes move with block time and with the feed.
const REFETCH_MS = 15_000
const UNIT = 1_000_000n // 1 NOTE or WRITER
const PRICER_FIELDS = 10 // PricerInputs fields: certifiedRange(0..9)

type Read = { status: 'success'; result: unknown } | { status: 'failure'; error: Error }
type Out<abi extends Abi, fn extends ContractFunctionName<abi, 'view' | 'pure'>> =
  ContractFunctionReturnType<abi, 'view' | 'pure', fn>

/** What stage 2 knows about a series: everything but quotes, feed and inventory. */
type Base = Pick<
  SeriesView,
  | 'address'
  | 'terms'
  | 'state'
  | 'listing'
  | 'spread'
  | 'maxPayoutPerNote'
  | 'note'
  | 'writer'
  | 'pendingObservation'
>

/** The Desk's listed series (the delisted ones apart) with quotes, feeds and models, read from the chain every 15 s. */
export function useChainMarket(deployment: Deployment | null): {
  data: MarketData | undefined
  isLoading: boolean
  error: Error | null
} {
  const on = deployment !== null
  const desk = deployment?.desk ?? zeroAddress

  // Stage 1: the Desk.
  const deskQuery = useReadContracts({
    contracts: [
      { address: desk, abi: deskAbi, functionName: 'listedSeries' },
      { address: desk, abi: deskAbi, functionName: 'quoter' },
      { address: desk, abi: deskAbi, functionName: 'asset' },
      { address: desk, abi: deskAbi, functionName: 'queuedShares' },
      { address: desk, abi: deskAbi, functionName: 'MAX_FEE_BPS' },
      { address: desk, abi: deskAbi, functionName: 'MAX_COVER_FEE_BPS' },
      { address: desk, abi: deskAbi, functionName: 'BACKSTOP_SHARE_BPS' },
      { address: desk, abi: deskAbi, functionName: 'QUEUE_BATCH' },
      { address: desk, abi: deskAbi, functionName: 'reservedAssets' },
      { address: deployment?.usdg ?? zeroAddress, abi: erc20Abi, functionName: 'balanceOf', args: [desk] },
    ],
    allowFailure: true,
    query: { enabled: on, refetchInterval: REFETCH_MS },
  })
  const deskReads = deskQuery.data
  const deskFailure = deskReads?.find((r) => r.status === 'failure')?.error ?? null
  const deskOk = deskReads !== undefined && deskFailure === null
  const listed = deskOk ? deskReads[0].result : undefined
  const quoter = deskOk ? deskReads[1].result : undefined
  const queued = deskOk ? deskReads[3].result : undefined
  const queueBatch = deskOk ? deskReads[7].result : undefined

  // A trade that adds to the Desk's position pays the queue first. The Desk itself says what that
  // leaves: processQueue as a call that sends nothing. Anyone may call it, so no wallet is needed.
  // Kept per queue, so the answer for an earlier queue is never held against a later one.
  const queueFill = useSimulateContract({
    address: desk,
    abi: deskAbi,
    functionName: 'processQueue',
    args: queueBatch === undefined ? undefined : [queueBatch],
    account: zeroAddress,
    scopeKey: String(queued),
    query: { enabled: on && !!queued, refetchInterval: REFETCH_MS },
  })
  // No answer (yet), or a revert because a held series has no price: the queue can't be paid.
  const filled = queued && queueFill.isSuccess ? queueFill.data.result : undefined

  // Stage 2: every listed series, delisted ones too (a wallet may still hold them).
  const seriesContracts = useMemo(
    () => (listed ?? []).flatMap((s) => seriesReads(desk, s)),
    [desk, listed],
  )
  const seriesQuery = useReadContracts({
    contracts: seriesContracts,
    allowFailure: true,
    query: { enabled: on && seriesContracts.length > 0, refetchInterval: REFETCH_MS },
  })
  const seriesData = seriesQuery.data
  const bases = useMemo(() => {
    if (!listed) return undefined
    if (listed.length === 0) return []
    return seriesData ? parseSeries(listed, seriesData) : undefined
  }, [listed, seriesData])

  // Stage 3: quotes, feed and inventory per series, and the models that price them.
  const pricers = useMemo(
    () => [...new Set((bases ?? []).map((v) => v.listing.pricer))],
    [bases],
  )
  const quoteContracts = useMemo(
    () =>
      bases && quoter
        ? [
            blockTimestamp,
            ...bases.flatMap((v) => quoteReads(desk, quoter, v)),
            ...pricers.flatMap(modelReads),
          ]
        : [],
    [bases, desk, pricers, quoter],
  )
  const quoteQuery = useReadContracts({
    contracts: quoteContracts,
    allowFailure: true,
    query: { enabled: on && quoteContracts.length > 0, refetchInterval: REFETCH_MS },
  })
  const quoteData = quoteQuery.data
  const readAt = quoteQuery.dataUpdatedAt
  // The chain's own clock: the dev node's moves only with transactions and can be jumped forward.
  const block = useBlock({ query: { enabled: on, refetchInterval: REFETCH_MS } })
  const blockTime = block.data?.timestamp

  const data = useMemo((): MarketData | undefined => {
    if (!deskOk || !bases || !quoteData) return undefined
    const [, , usdg, queuedShares, maxFeeBps, maxCoverFeeBps, backstopShareBps, , reserved, balance] = deskReads
    const timestamp = value<bigint>(quoteData[0])
    let at = 1
    const views = bases.map((v) => {
      const view = parseQuotes(v, quoteData.slice(at, at + QUOTE_READS))
      at += QUOTE_READS
      return view
    })
    const models: Record<Address, ModelView> = {}
    for (const pricer of pricers) {
      const model = parseModel(pricer, quoteData.slice(at, at + MODEL_READS))
      at += MODEL_READS
      if (model) models[pricer] = model
    }
    return {
      source: 'chain',
      desk,
      usdg: usdg.result!,
      series: views.filter((v) => v.listing.active),
      delisted: views.filter((v) => !v.listing.active),
      models,
      queue: {
        waiting: filled ? queuedShares.result! - filled[0] : queuedShares.result!,
        setAside: filled ? filled[1] : 0n,
      },
      idle: balance.result! > reserved.result! ? balance.result! - reserved.result! : 0n, // Desk._idle
      fees: {
        maxFeeBps: maxFeeBps.result!,
        maxCoverFeeBps: maxCoverFeeBps.result!,
        backstopShareBps: backstopShareBps.result!,
      },
      // the block the quotes were read at (Multicall3 in the same batch), else the latest block,
      // else the local clock at the read
      now: Number(timestamp ?? blockTime ?? Math.floor(readAt / 1000)),
    }
  }, [bases, blockTime, desk, deskOk, deskReads, filled, pricers, quoteData, readAt])

  return {
    data: on ? data : undefined,
    isLoading: deskQuery.isLoading || seriesQuery.isLoading || quoteQuery.isLoading,
    error: on ? (deskQuery.error ?? deskFailure ?? seriesQuery.error ?? quoteQuery.error) : null,
  }
}

// Only where Multicall3 exists; on the dev node this read fails and the latest block's time is used.
const blockTimestamp: ContractFunctionParameters = {
  address: chain.id === robinhoodTestnet.id ? robinhoodTestnet.contracts.multicall3.address : zeroAddress,
  abi: multicall3Abi,
  functionName: 'getCurrentBlockTimestamp',
}

const SERIES_READS = 8

function seriesReads(desk: Address, s: Address): ContractFunctionParameters[] {
  return [
    { address: desk, abi: deskAbi, functionName: 'listing', args: [s] },
    { address: s, abi: noteSeriesAbi, functionName: 'terms' },
    { address: s, abi: noteSeriesAbi, functionName: 'state' },
    { address: s, abi: noteSeriesAbi, functionName: 'maxPayoutPerNote' },
    { address: desk, abi: deskAbi, functionName: 'spread', args: [s] },
    { address: s, abi: noteSeriesAbi, functionName: 'note' },
    { address: s, abi: noteSeriesAbi, functionName: 'writer' },
    { address: s, abi: noteSeriesAbi, functionName: 'pendingObservation' },
  ]
}

/** Every listing, active or not. A series whose plain reads fail is left out (never expected). */
function parseSeries(listed: readonly Address[], reads: readonly Read[]): Base[] {
  return listed.flatMap((address, i) => {
    const r = reads.slice(i * SERIES_READS, (i + 1) * SERIES_READS)
    if (r.length < SERIES_READS || r.some((x) => x.status === 'failure')) return []
    const listing = value<Out<typeof deskAbi, 'listing'>>(r[0])!
    const [pending, obsTime] = value<Out<typeof noteSeriesAbi, 'pendingObservation'>>(r[7])!
    return [
      {
        address,
        listing,
        terms: value<Out<typeof noteSeriesAbi, 'terms'>>(r[1])!,
        state: value<Out<typeof noteSeriesAbi, 'state'>>(r[2])!,
        maxPayoutPerNote: value<Out<typeof noteSeriesAbi, 'maxPayoutPerNote'>>(r[3])!,
        spread: value<Out<typeof deskAbi, 'spread'>>(r[4])!,
        note: value<Out<typeof noteSeriesAbi, 'note'>>(r[5])!,
        writer: value<Out<typeof noteSeriesAbi, 'writer'>>(r[6])!,
        pendingObservation: { pending, obsTime },
      },
    ]
  })
}

const QUOTE_READS = 11

function quoteReads(desk: Address, quoter: Address, v: Base): ContractFunctionParameters[] {
  const s = v.address
  const { pricer, volBpsAnnual: vol } = v.listing
  const { feed } = v.terms
  return [
    { address: quoter, abi: noteQuoterAbi, functionName: 'notePriceBps', args: [s, pricer, vol] },
    { address: quoter, abi: noteQuoterAbi, functionName: 'inputs', args: [s, vol] },
    { address: desk, abi: deskAbi, functionName: 'quoteBuy', args: [s, UNIT, 0] },
    { address: desk, abi: deskAbi, functionName: 'quoteSell', args: [s, UNIT, 0] },
    { address: desk, abi: deskAbi, functionName: 'quoteBuyCover', args: [s, UNIT, 0] },
    { address: desk, abi: deskAbi, functionName: 'quoteSellCover', args: [s, UNIT, 0] },
    { address: desk, abi: deskAbi, functionName: 'risk', args: [feed] },
    { address: v.note, abi: seriesTokenAbi, functionName: 'balanceOf', args: [desk] },
    { address: feed, abi: aggregatorAbi, functionName: 'latestRoundData' },
    { address: feed, abi: aggregatorAbi, functionName: 'description' },
    { address: desk, abi: deskAbi, functionName: 'riskBudgetBps', args: [feed] },
  ]
}

type Quote = Out<typeof deskAbi, 'quoteBuy'>
const priceOf = ([, priceBps]: Quote) => priceBps

function parseQuotes(v: Base, r: readonly Read[]): SeriesView {
  const risk = value<Out<typeof deskAbi, 'risk'>>(r[6])
  const round = value<Out<typeof aggregatorAbi, 'latestRoundData'>>(r[8])
  const description = value<string>(r[9])
  const budgetBps = value<Out<typeof deskAbi, 'riskBudgetBps'>>(r[10])
  return {
    ...v,
    symbol: description !== undefined ? symbolOf(description) : '',
    spot: round ? round[1] : null,
    spotUpdatedAt: round ? Number(round[3]) : null,
    mid: refusable(r[0], ([priceBps, weightsHash]: Out<typeof noteQuoterAbi, 'notePriceBps'>) => ({
      priceBps,
      weightsHash,
    })),
    inputs: refusable(r[1], (x: Out<typeof noteQuoterAbi, 'inputs'>): PricerInputs => x),
    noteAsk: refusable(r[2], priceOf),
    noteBid: refusable(r[3], priceOf),
    coverAsk: refusable(r[4], priceOf),
    coverBid: refusable(r[5], priceOf),
    // plain views that don't revert; if one ever does, show no inventory and no room
    noteInventory: value<bigint>(r[7]) ?? 0n,
    risk: risk && budgetBps !== undefined ? { atRisk: risk[0], limit: risk[1], budgetBps } : { atRisk: 0n, limit: 0n, budgetBps: 0 },
  }
}

const MODEL_READS = 1 + PRICER_FIELDS

function modelReads(pricer: Address): ContractFunctionParameters[] {
  return [
    { address: pricer, abi: surrogatePricerAbi, functionName: 'weightsHash' },
    ...Array.from({ length: PRICER_FIELDS }, (_, field) => ({
      address: pricer,
      abi: surrogatePricerAbi,
      functionName: 'certifiedRange',
      args: [field],
    })),
  ]
}

function parseModel(address: Address, r: readonly Read[]): ModelView | undefined {
  if (r.some((x) => x.status === 'failure')) return undefined
  return {
    address,
    weightsHash: value<Out<typeof surrogatePricerAbi, 'weightsHash'>>(r[0])!,
    certifiedRange: r.slice(1).map((x) => value<Out<typeof surrogatePricerAbi, 'certifiedRange'>>(x)!),
  }
}

/** 'RHTSLA / USD (staged demo feed)' → 'TSLA' */
function symbolOf(description: string): string {
  const base = description.split(' / ')[0].trim()
  return base.replace(/^RH/, '') || base
}

function value<T>(r: Read | undefined): T | undefined {
  return r?.status === 'success' ? (r.result as T) : undefined
}

function refusable<T, U>(r: Read, map: (value: T) => U): Refusable<U> {
  return r.status === 'success'
    ? { ok: true, value: map(r.result as T) }
    : { ok: false, refusal: decodeRefusal(r.error) }
}

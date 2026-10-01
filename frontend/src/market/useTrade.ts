import { useCallback, useRef, useState } from 'react'
import { type Address, type Hex, erc20Abi } from 'viem'
import {
  getAccount,
  readContract,
  simulateContract,
  waitForTransactionReceipt,
  writeContract,
} from 'wagmi/actions'
import { chain, config } from '../wagmi'
import { deskAbi } from './abi'
import type { Deployment } from './deployments'
import { decodeRefusal } from './errors'
import type { SeriesView, TradeKind, TradeState } from './types'

const BPS = 10_000n
// Writes name the chain, so a wallet on another network fails instead of sending there.
const chainId = chain.id

const QUOTE = {
  buy: 'quoteBuy',
  sell: 'quoteSell',
  buyCover: 'quoteBuyCover',
  sellCover: 'quoteSellCover',
} as const

/** A trade that went through after its order was left behind. */
export type LateTrade = { series: SeriesView; kind: TradeKind; amount: bigint; hash: Hex }

export type TradeParams = {
  series: SeriesView
  kind: TradeKind
  amount: bigint // NOTE or WRITER base units
  slippageBps: number
  feeBps: number
  feeReceiver: Address
}

/** One Desk trade at a time: quote, approve if the allowance is short, trade. */
export function useTrade(deployment: Deployment | null): {
  state: TradeState
  run(p: TradeParams): Promise<void>
  reset(): void
  late: LateTrade | null
  dismissLate(): void
} {
  const [state, setState] = useState<TradeState>({ step: 'idle' })
  // Bumped by every run and by reset: a run that was left behind stops before its next step
  // and can't overwrite the state.
  const current = useRef(0)
  // The run before, until it has returned. The next one starts only then, so two are never
  // under way: a wallet request that is already open can't be taken back, only waited out.
  // A run that threw doesn't hold the next one up.
  const last = useRef(Promise.resolve())
  // A request is open in the wallet. A run that starts behind it says so: only the reader can
  // answer it, and an order she left behind is one to reject.
  const asked = useRef(false)
  // The one case a left-behind order still trades: its request was open and she confirmed it.
  const [late, setLate] = useState<LateTrade | null>(null)

  const run = useCallback((p: TradeParams) => {
    const id = ++current.current
    const left = () => id !== current.current
    const set = (next: TradeState) => {
      if (!left()) setState(next)
    }
    const ask = async <T>(request: Promise<T>) => {
      asked.current = true
      try {
        return await request
      } finally {
        asked.current = false
        // The wallet has answered: a run that waited behind this request goes on.
        setState((s) => (s.step === 'waiting' ? { step: 'quoting' } : s))
      }
    }
    // Busy from here on, also while it waits for the run before it.
    set({ step: asked.current ? 'waiting' : 'quoting' })
    last.current = last.current.catch(() => {}).then(async () => {
      if (left()) return
      let hash: Hex | undefined
      try {
        if (!deployment) throw new Error('No Desk deployed on this chain')
        const { desk, usdg } = deployment
        const account = getAccount(config).address
        if (!account) throw new Error('Connect a wallet first')
        const { series, kind, amount, feeBps, feeReceiver } = p
        const isBuy = kind === 'buy' || kind === 'buyCover'

        const [quoted] = await readContract(config, {
          address: desk,
          abi: deskAbi,
          functionName: QUOTE[kind],
          args: [series.address, amount, feeBps],
          chainId,
        })
        if (left()) return
        const slippage = BigInt(p.slippageBps)
        const limit = isBuy
          ? (quoted * (BPS + slippage) + BPS - 1n) / BPS // maxCost, rounded up
          : (quoted * (BPS - slippage)) / BPS // minProceeds, rounded down

        // Buys pay USDG; sells hand in the leg they sell.
        const token = kind === 'sell' ? series.note : kind === 'sellCover' ? series.writer : usdg
        const needed = isBuy ? limit : amount
        const allowance = await readContract(config, {
          address: token,
          abi: erc20Abi,
          functionName: 'allowance',
          args: [account, desk],
          chainId,
        })
        if (left()) return
        if (allowance < needed) {
          set({ step: 'approving' })
          const approval = await ask(writeContract(config, {
            address: token,
            abi: erc20Abi,
            functionName: 'approve',
            args: [desk, needed],
            chainId,
          }))
          // Left behind while the wallet was asked: the approval isn't waited for.
          if (left()) return
          await waitForTransactionReceipt(config, { hash: approval, chainId })
          if (left()) return
        }

        set({ step: 'trading' })
        // Simulated through our RPC first, so that a revert the quote doesn't foresee
        // (RiskBudgetExceeded, QueuePending) decodes against the Desk's ABI, whatever the wallet reports.
        const { request } = await simulateContract(config, {
          address: desk,
          abi: deskAbi,
          functionName: kind,
          args: [series.address, amount, limit, feeBps, feeReceiver, account],
          account,
          chainId,
        })
        if (left()) return
        // Once sent, a trade can't be taken back: the run waits for it even when it was left behind.
        hash = await ask(writeContract(config, request))
        set({ step: 'trading', hash })
        await waitForTransactionReceipt(config, { hash, chainId })
        if (left()) setLate({ series, kind, amount, hash })
        set({ step: 'done', hash })
      } catch (error) {
        set({ step: 'failed', hash, refusal: decodeRefusal(error) })
      }
    })
    return last.current
  }, [deployment])

  const reset = useCallback(() => {
    current.current++
    setState({ step: 'idle' })
  }, [])

  const dismissLate = useCallback(() => setLate(null), [])

  return { state, run, reset, late, dismissLate }
}

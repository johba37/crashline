import { useCallback, useRef, useState } from 'react'
import { type Address, type Hex, erc20Abi } from 'viem'
import {
  getAccount,
  readContract,
  simulateContract,
  switchChain,
  waitForTransactionReceipt,
  writeContract,
} from 'wagmi/actions'
import { chain, config } from '../wagmi'
import { deskAbi, noteSeriesAbi } from './abi'
import type { Deployment } from './deployments'
import { decodeRefusal } from './errors'
import type { CollectKind, SeriesView, TradeKind, TradeState } from './types'

const BPS = 10_000n
// Writes name the chain, so a wallet that is still on another network fails instead of sending there.
const chainId = chain.id

const QUOTE = {
  buy: 'quoteBuy',
  sell: 'quoteSell',
  buyCover: 'quoteBuyCover',
  sellCover: 'quoteSellCover',
} as const

/** A trade that went through after its order was left behind. */
export type LateTrade = { series: SeriesView; kind: TradeKind | CollectKind; amount: bigint; hash: Hex }

/** The last trade that went through, and the block it is in: what a read of the wallet has to have reached to show it. */
export type Traded = { series: SeriesView; kind: TradeKind | CollectKind; block: bigint }

export type TradeParams = {
  series: SeriesView
  kind: TradeKind | CollectKind
  amount: bigint // NOTE or WRITER base units
  slippageBps: number // the three below are the Desk's: collecting has no price and no fee
  feeBps: number
  feeReceiver: Address
}

/**
 * One trade at a time. With the Desk: quote, approve if the allowance is short, trade. Collecting
 * from a note that has ended is one transaction to the note itself.
 */
export function useTrade(deployment: Deployment | null): {
  state: TradeState
  run(p: TradeParams): Promise<void>
  reset(): void
  late: LateTrade | null
  dismissLate(): void
  traded: Traded | null
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
  // Kept through reset, and set by a left-behind trade too.
  const [traded, setTraded] = useState<Traded | null>(null)

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
        // A wallet on another network is asked to switch first (and to add the network, if it doesn't
        // know it), so the order goes on instead of failing. Declining ends the run as cancelled.
        if (getAccount(config).chainId !== chainId) {
          set({ step: 'approving' })
          await ask(switchChain(config, { chainId }))
          if (left()) return
          set({ step: 'quoting' })
        }
        const { series, kind, amount, feeBps, feeReceiver } = p
        // Once sent, a trade can't be taken back: the run waits for it even when it was left behind.
        const send = async (write: Promise<Hex>) => {
          hash = await ask(write)
          set({ step: 'trading', hash })
          const receipt = await waitForTransactionReceipt(config, { hash, chainId })
          setTraded({ series, kind, block: receipt.blockNumber })
          if (left()) setLate({ series, kind, amount, hash })
          set({ step: 'done', hash })
        }

        // A note that has ended pays out of its own escrow: the series burns the leg it is handed
        // (redeem), so there is no price to quote and nothing to approve.
        if (kind === 'collect' || kind === 'collectCover') {
          set({ step: 'trading' })
          const { request } = await simulateContract(config, {
            address: series.address,
            abi: noteSeriesAbi,
            functionName: 'redeem',
            args: kind === 'collect' ? [amount, 0n, account] : [0n, amount, account],
            account,
            chainId,
          })
          if (left()) return
          return await send(writeContract(config, request))
        }
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
        await send(writeContract(config, request))
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

  return { state, run, reset, late, dismissLate, traded }
}

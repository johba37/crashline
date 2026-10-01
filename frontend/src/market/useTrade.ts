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
} {
  const [state, setState] = useState<TradeState>({ step: 'idle' })
  // Bumped by every run and by reset, so a run that was left behind can't overwrite the state.
  const current = useRef(0)

  const run = useCallback(async (p: TradeParams) => {
    const id = ++current.current
    const set = (next: TradeState) => {
      if (id === current.current) setState(next)
    }
    let hash: Hex | undefined
    try {
      if (!deployment) throw new Error('No Desk deployed on this chain')
      const { desk, usdg } = deployment
      const account = getAccount(config).address
      if (!account) throw new Error('Connect a wallet first')
      const { series, kind, amount, feeBps, feeReceiver } = p
      const isBuy = kind === 'buy' || kind === 'buyCover'

      set({ step: 'quoting' })
      const [quoted] = await readContract(config, {
        address: desk,
        abi: deskAbi,
        functionName: QUOTE[kind],
        args: [series.address, amount, feeBps],
        chainId,
      })
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
      if (allowance < needed) {
        set({ step: 'approving' })
        const approval = await writeContract(config, {
          address: token,
          abi: erc20Abi,
          functionName: 'approve',
          args: [desk, needed],
          chainId,
        })
        await waitForTransactionReceipt(config, { hash: approval, chainId })
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
      hash = await writeContract(config, request)
      set({ step: 'trading', hash })
      await waitForTransactionReceipt(config, { hash, chainId })
      set({ step: 'done', hash })
    } catch (error) {
      set({ step: 'failed', hash, refusal: decodeRefusal(error) })
    }
  }, [deployment])

  const reset = useCallback(() => {
    current.current++
    setState({ step: 'idle' })
  }, [])

  return { state, run, reset }
}

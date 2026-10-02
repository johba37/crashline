import { zeroAddress, type Address } from 'viem'

// Our integrator fee, agreed with the Desk curator (plan 0.4); 0 until then.
export const FEE_BPS = Number(import.meta.env.VITE_FEE_BPS ?? 0)
export const FEE_RECEIVER = (import.meta.env.VITE_FEE_RECEIVER ?? zeroAddress) as Address
export const SLIPPAGE_BPS = 50

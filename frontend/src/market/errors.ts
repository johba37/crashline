import { BaseError, ContractFunctionRevertedError, UserRejectedRequestError } from 'viem'
import type { Refusal } from './types'

/** A failed read or transaction as a Refusal: the decoded revert, the wallet declining, or Unknown. */
export function decodeRefusal(error: unknown): Refusal {
  if (error instanceof BaseError) {
    const found = error.walk(
      (e) => e instanceof ContractFunctionRevertedError || e instanceof UserRejectedRequestError,
    )
    if (found instanceof UserRejectedRequestError) return { error: 'UserRejected', args: [] }
    if (found instanceof ContractFunctionRevertedError) {
      if (found.data) return { error: found.data.errorName, args: found.data.args ?? [] }
      // a selector none of the merged ABIs knows
      if (found.signature) return { error: 'Unknown', args: [found.signature] }
    }
  }
  return { error: 'Unknown', args: [shortMessage(error)] }
}

function shortMessage(error: unknown): string {
  if (error instanceof Error) {
    // viem's and wagmi's errors both carry a one-line shortMessage
    const short = (error as { shortMessage?: unknown }).shortMessage
    return typeof short === 'string' ? short : error.message
  }
  return String(error)
}

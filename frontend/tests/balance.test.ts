// A wallet without enough USDG: the token's revert (0xe450d38c) read as a refusal, and said in plain words.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ContractFunctionRevertedError, encodeErrorResult, parseAbi, zeroAddress } from 'viem'
import { refusalStatus } from '../src/components/dashboard/status.ts'
import { deskAbi, noteSeriesAbi } from '../src/market/abi.ts'
import { decodeRefusal } from '../src/market/errors.ts'

const USDG = 1_000_000n
const data = encodeErrorResult({
  abi: parseAbi(['error ERC20InsufficientBalance(address sender, uint256 balance, uint256 needed)']),
  errorName: 'ERC20InsufficientBalance',
  args: [zeroAddress, 0n, 42n * USDG],
})

test('the revert carries the selector the wallet showed', () => {
  assert.equal(data.slice(0, 10), '0xe450d38c')
})

test('a trade on the Desk or a collect on the series decodes it', () => {
  for (const [abi, functionName] of [[deskAbi, 'buyCover'], [noteSeriesAbi, 'redeem']] as const) {
    const refusal = decodeRefusal(new ContractFunctionRevertedError({ abi, data, functionName }))
    assert.equal(refusal.error, 'ERC20InsufficientBalance')
    assert.deepEqual(refusal.args, [zeroAddress, 0n, 42n * USDG])
  }
})

test('it reads as not enough in the wallet, with both amounts', () => {
  const status = refusalStatus({ error: 'ERC20InsufficientBalance', args: [zeroAddress, 0n, 42n * USDG] })
  assert.equal(status.label, 'Not enough in your wallet')
  assert.match(status.message ?? '', /needs 42\.00 USDG, and your wallet has 0\.00 USDG/)
})

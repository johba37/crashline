import { type Address, type Hex, type PublicClient, encodeDeployData, encodeFunctionData, erc20Abi, formatUnits, isAddressEqual, multicall3Abi, parseUnits, zeroAddress } from 'viem'
import { deskAbi, fixingsRecorderAbi, noteSeriesAbi, seriesFactoryAbi } from '../market/abi.ts'
import { stagedFeedAbi, stagedFeedBytecode } from './stagedFeed.ts'

// The testnet's notes, sent by the curator's browser wallet: the lineup of
// contracts/script/testnet-notes.sh (same prices, vols and listing terms), in as few
// transactions as the contracts allow. A feed is deployed with its history in one
// (StagedChainlinkFeed); the steps anyone may take (series, fixings, advance) go through
// Multicall3; the Desk's owner calls can't be batched, so each is its own.

const WEEK = 604_800
const MULTICALL3: Address = '0xcA11bde05977b3631167028862bE2a173976CA11'
const ROUND_BASE = 1n << 64n // roundId = 2^64 + n
// curator.sh `list`
const LISTING = { capNotional: parseUnits('100000', 6), bidBps: 20, askBps: 30, volBandBps: 200, riskBudgetBps: 2000 }

/** A feed and the notes on it: the first is struck one week before `closes[0]`, a further one at check `further[i]`. */
export type FeedPlan = {
  name: string // the feed's description is `${name} / USD (staged)`: the app shows RHTSLA as TSLA
  yahoo: string
  volBps: number
  next: string // the first check after 2026-10-02, when `closes` was read
  initial: string // USD
  closes: number[] // at its weekly checks since, in bps of `initial`
  further: number[]
}

export const FEEDS: FeedPlan[] = [
  {
    name: 'RHTSLA', yahoo: 'TSLA', volBps: 5500, next: '2026-10-06T20:00Z', initial: '433.59',
    closes: [9773, 9149, 9333, 8801, 9700, 9292, 9137, 8739, 7091, 7550, 7676, 7769, 8078, 8213, 8491, 8224, 8739, 8138],
    further: [5, 17],
  },
  {
    name: 'RHNVDA', yahoo: 'NVDA', volBps: 4500, next: '2026-10-08T20:00Z', initial: '235.74',
    closes: [9312, 9088, 9275, 8691, 8937, 8303, 8265, 8602, 8798, 8856, 8274, 9289, 9557, 9199, 9671, 9691, 9263, 9304, 9527, 9793],
    further: [],
  },
  { name: 'ETH', yahoo: 'ETH-USD', volBps: 6500, next: '2026-10-07T00:00Z', initial: '2752.63', closes: [9724], further: [] },
  { name: 'BTC', yahoo: 'BTC-USD', volBps: 4500, next: '2026-10-07T00:00Z', initial: '86172.28', closes: [9704], further: [] },
]

/** The rounds a feed is deployed with: its first strike, then each weekly check since (prices with 8 decimals). */
function history(f: FeedPlan) {
  const strike = Date.parse(f.next) / 1000 - (f.closes.length + 1) * WEEK
  const initial = parseUnits(f.initial, 8)
  const answers = [initial, ...f.closes.map((bps) => (initial * BigInt(bps)) / 10_000n)]
  return { answers, times: answers.map((_, i) => strike + i * WEEK) }
}

/** The six notes: when each is struck, and at what price. */
export const NOTES = FEEDS.flatMap((f) => {
  const { answers, times } = history(f)
  return [0, ...f.further].map((i) => ({ name: f.name, volBps: f.volBps, strikeTime: times[i], initial: answers[i] }))
})

export type Addresses = { desk: Address; usdg: Address; seriesFactory: Address; surrogatePricer: Address }

/** What the steps need from the wallet, so that a test can stand in for it. */
export type Io = {
  client: PublicClient
  account: Address
  /** Says what is about to be signed, sends it and waits for it. Without `to` it deploys. */
  send(label: string, tx: { to?: Address; data: Hex }): Promise<{ contractAddress?: Address | null }>
  /** Feeds deployed by an earlier run, by name; `remember` adds one. */
  feeds: Record<string, Address>
  remember(name: string, feed: Address): void
  /** The price now, in USD. */
  price(yahoo: string): Promise<number>
}

const terms = (feed: Address, strikeTime: number) =>
  ({ feed, strikeTime, observationInterval: WEEK, observationCount: 26, kiBarrierBps: 6000, acBarrierBps: 10_000, couponBpsPerPeriod: 25 }) as const

const batch = (calls: { target: Address; callData: Hex }[]) => ({
  to: MULTICALL3,
  data: encodeFunctionData({ abi: multicall3Abi, functionName: 'aggregate3', args: [calls.map((c) => ({ ...c, allowFailure: false }))] }),
})

/**
 * Sends what is still missing for the six notes to be listed, in order, and returns the feeds.
 * It reads the chain first, so after a rejected or failed transaction a second run goes on
 * from there.
 */
export async function createNotes(io: Io, a: Addresses): Promise<Record<string, Address>> {
  const { client, account } = io
  const now = Number((await client.getBlock()).timestamp)

  // 1. The feeds: one deployment each, with every past price and the price now.
  const feeds: (FeedPlan & { feed: Address; times: number[] })[] = []
  for (const f of FEEDS) {
    if (Date.parse(f.next) / 1000 <= now) throw new Error(`${f.name}'s check of ${f.next} has passed, so the prices listed here are out of date.`)
    const { answers, times } = history(f)
    let feed = io.feeds[f.name]
    const owner = feed && (await client.readContract({ address: feed, abi: stagedFeedAbi, functionName: 'owner' }).catch(() => undefined))
    if (!feed || !owner || !isAddressEqual(owner, account)) {
      const spot = BigInt(Math.round((await io.price(f.yahoo)) * 1e8))
      const args = [`${f.name} / USD (staged)`, answers, times, spot] as const
      const receipt = await io.send(`Deploy the ${f.name} feed with its ${answers.length} past prices`, {
        data: encodeDeployData({ abi: stagedFeedAbi, bytecode: stagedFeedBytecode, args }),
      })
      if (!receipt.contractAddress) throw new Error(`The ${f.name} feed was not deployed.`)
      feed = receipt.contractAddress
      io.remember(f.name, feed)
    }
    feeds.push({ ...f, feed, times })
  }

  // 2. The series, all in one transaction.
  const notes = feeds.flatMap((f) => [0, ...f.further].map((i) => ({ name: f.name, volBps: f.volBps, terms: terms(f.feed, f.times[i]) })))
  const factory = { address: a.seriesFactory, abi: seriesFactoryAbi } as const
  const lookUp = async () => {
    const ids = await client.multicall({ allowFailure: false, contracts: notes.map((n) => ({ ...factory, functionName: 'seriesId', args: [n.terms] }) as const) })
    return client.multicall({ allowFailure: false, contracts: ids.map((id) => ({ ...factory, functionName: 'seriesOf', args: [id] }) as const) })
  }
  let series = await lookUp()
  const unborn = notes.filter((_, i) => isAddressEqual(series[i], zeroAddress))
  if (unborn.length > 0) {
    await io.send(
      `Create ${unborn.length} series`,
      batch(unborn.map((n) => ({ target: a.seriesFactory, callData: encodeFunctionData({ abi: seriesFactoryAbi, functionName: 'createSeries', args: [n.terms] }) }))),
    )
    series = await lookUp()
  }
  // The RPC can answer from a node that is a block behind: nothing below may be sent to the zero address.
  const behind = new Error('The RPC doesn’t show the last transaction yet. Run it again in a moment.')
  if (series.some((s) => isAddressEqual(s, zeroAddress))) throw behind

  // 3. Each feed's fixings (the strike and every check so far), then every series brought up to date.
  const recorders = await client.multicall({ allowFailure: false, contracts: feeds.map((f) => ({ ...factory, functionName: 'recorderOf', args: [f.feed] }) as const) })
  if (recorders.some((r) => isAddressEqual(r, zeroAddress))) throw behind
  const fixings = feeds.flatMap((f, k) => f.times.map((time, i) => ({ recorder: recorders[k], time, round: ROUND_BASE + BigInt(i + 1) })))
  const recorded = await client.multicall({
    allowFailure: false,
    contracts: fixings.map((x) => ({ address: x.recorder, abi: fixingsRecorderAbi, functionName: 'isRecorded', args: [x.time] }) as const),
  })
  const open = fixings.filter((_, i) => !recorded[i])
  if (open.length > 0 || unborn.length > 0) {
    await io.send(
      `Record ${open.length} fixings and bring the ${series.length} series up to date`,
      batch([
        ...open.map((x) => ({ target: x.recorder, callData: encodeFunctionData({ abi: fixingsRecorderAbi, functionName: 'recordFixing', args: [x.time, x.round] }) })),
        ...series.map((s) => ({ target: s, callData: encodeFunctionData({ abi: noteSeriesAbi, functionName: 'advance' }) })),
      ]),
    )
  }

  // 4. The Desk, as its owner: each series listed with its vol and spread, each feed with a risk budget.
  const desk = { address: a.desk, abi: deskAbi } as const
  const listings = await client.multicall({ allowFailure: false, contracts: series.map((s) => ({ ...desk, functionName: 'listing', args: [s] }) as const) })
  const spreads = await client.multicall({ allowFailure: false, contracts: series.map((s) => ({ ...desk, functionName: 'spread', args: [s] }) as const) })
  const budgets = await client.multicall({ allowFailure: false, contracts: feeds.map((f) => ({ ...desk, functionName: 'riskBudgetBps', args: [f.feed] }) as const) })
  for (const [i, n] of notes.entries()) {
    const what = `${n.name} note ${series[i].slice(0, 8)}`
    if (isAddressEqual(listings[i].pricer, zeroAddress)) {
      await io.send(`List the ${what} at ${n.volBps / 100}% vol`, {
        to: a.desk,
        data: encodeFunctionData({ abi: deskAbi, functionName: 'listSeries', args: [series[i], a.surrogatePricer, n.volBps, LISTING.capNotional] }),
      })
    }
    const s = spreads[i]
    if (s.bidBps !== LISTING.bidBps || s.askBps !== LISTING.askBps || s.volBandBps !== LISTING.volBandBps) {
      await io.send(`Set the spread of the ${what}`, {
        to: a.desk,
        data: encodeFunctionData({ abi: deskAbi, functionName: 'setSpread', args: [series[i], LISTING.bidBps, LISTING.askBps, LISTING.volBandBps] }),
      })
    }
  }
  for (const [k, f] of feeds.entries()) {
    if (budgets[k] !== LISTING.riskBudgetBps) {
      await io.send(`Set the risk budget of ${f.name}`, {
        to: a.desk,
        data: encodeFunctionData({ abi: deskAbi, functionName: 'setRiskBudget', args: [f.feed, LISTING.riskBudgetBps] }),
      })
    }
  }
  return Object.fromEntries(feeds.map((f) => [f.name, f.feed]))
}

/** USDG into the Desk (whole USDG), which its trades are paid from: an approval if the allowance is short, then the deposit. */
export async function deposit(io: Io, a: Addresses, usdg: string): Promise<void> {
  const amount = parseUnits(usdg, 6)
  const held = await io.client.readContract({ address: a.usdg, abi: erc20Abi, functionName: 'balanceOf', args: [io.account] })
  if (held < amount) throw new Error(`This wallet holds ${formatUnits(held, 6)} USDG.`)
  const allowance = await io.client.readContract({ address: a.usdg, abi: erc20Abi, functionName: 'allowance', args: [io.account, a.desk] })
  if (allowance < amount) {
    await io.send(`Allow the Desk to take ${usdg} USDG`, { to: a.usdg, data: encodeFunctionData({ abi: erc20Abi, functionName: 'approve', args: [a.desk, amount] }) })
  }
  await io.send(`Deposit ${usdg} USDG into the Desk`, { to: a.desk, data: encodeFunctionData({ abi: deskAbi, functionName: 'deposit', args: [amount, io.account] }) })
}

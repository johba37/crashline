import { CheckCircle, Info, WarningCircle } from '@phosphor-icons/react'
import { ConnectButton } from '@rainbow-me/rainbowkit'
import { useState } from 'react'
import { Link } from 'react-router'
import { type Address, type Hex, encodeFunctionData, isAddressEqual, parseAbi } from 'viem'
import { useAccount, useReadContract } from 'wagmi'
import { getAccount, getPublicClient, sendTransaction, switchChain, waitForTransactionReceipt } from 'wagmi/actions'
import { robinhoodTestnet } from 'wagmi/chains'
import Logo from '../components/Logo.tsx'
import Starfield from '../components/Starfield.tsx'
import Wordmark from '../components/Wordmark.tsx'
import NetworkChip from '../components/dashboard/NetworkChip.tsx'
import Notice from '../components/dashboard/Notice.tsx'
import { TxLink } from '../components/dashboard/Order.tsx'
import Step from '../components/dashboard/Step.tsx'
import TickerBadge from '../components/dashboard/TickerBadge.tsx'
import { date, usd } from '../market/format.ts'
import { type Addresses, type Io, NOTES, createNotes, deposit } from '../setup/notes.ts'
import { chain, config, setNetwork } from '../wagmi.ts'

// The curator's page, on a local branch only: it lists the testnet's notes with the connected
// wallet (the Desk's owner), one confirmation per transaction. src/setup/notes.ts has the steps.

const files = import.meta.glob<Addresses>('../../../deployments/46630.json', { eager: true, import: 'default' })
const addresses: Addresses | undefined = Object.values(files)[0]
const chainId = robinhoodTestnet.id
const ownerAbi = parseAbi(['function owner() view returns (address)', 'function transferOwnership(address newOwner)'])
// johba's server (the wallet that deployed the contracts): it keeps the testnet's notes going from here on.
const SERVER: Address = '0x7BB8f265FE906F4d21CB790Bfa56d2D3F96A452C'
const FEEDS_KEY = `crashline-setup-feeds-${chainId}`

const BUTTON = 'h-12 self-start rounded-full bg-accent px-6 type-button text-on-accent transition-[background-color,box-shadow] duration-160 ease-out hover:bg-accent-hover hover:shadow-ignition active:bg-accent-pressed disabled:cursor-not-allowed disabled:bg-surface-overlay disabled:text-ink-faint disabled:shadow-none'

/** The price now, from Yahoo through the dev server (vite.config.ts): its API sends no CORS headers. */
async function price(yahoo: string): Promise<number> {
  const res = await fetch(`/yahoo/v8/finance/chart/${yahoo}?interval=1d&range=1d`)
  const value = res.ok ? (await res.json())?.chart?.result?.[0]?.meta?.regularMarketPrice : undefined
  if (typeof value !== 'number') throw new Error(`No price for ${yahoo} from Yahoo (through the dev server’s /yahoo).`)
  return value
}

type Sent = { label: string; hash?: Hex }
type Run = { what: 'notes' | 'deposit' | 'handover'; busy: boolean; error?: string; done?: boolean }

export default function SetupPage() {
  const { address } = useAccount()
  const owner = useReadContract({ address: addresses?.desk, abi: ownerAbi, functionName: 'owner', chainId, query: { enabled: chain.id === chainId && !!addresses } })
  const [sent, setSent] = useState<Sent[]>([])
  const [run, setRun] = useState<Run | null>(null)
  const [feeds, setFeeds] = useState<Record<string, Address>>(() => JSON.parse(localStorage.getItem(FEEDS_KEY) ?? '{}'))
  const [listed, setListed] = useState(false)
  const [amount, setAmount] = useState('500')

  if (chain.id !== chainId || !addresses) {
    return (
      <main className="mx-auto max-w-3xl p-6">
        <Notice status={{ tone: 'hold', icon: Info, label: 'The app isn’t on the testnet', message: 'This page lists the testnet’s notes.' }}>
          <button type="button" onClick={() => setNetwork(chainId)} className="mt-1 h-8 self-start rounded-full border border-line-strong px-3 type-label text-ink transition-colors duration-160 hover:bg-surface-overlay">
            Switch to the testnet
          </button>
        </Notice>
      </main>
    )
  }
  const a = addresses
  const isOwner = !!address && !!owner.data && isAddressEqual(address, owner.data)

  const start = async (what: Run['what'], steps: (io: Io) => Promise<void>) => {
    if (!address) return
    setRun({ what, busy: true })
    const io: Io = {
      client: getPublicClient(config, { chainId }),
      account: address,
      feeds,
      remember(name, feed) {
        this.feeds = { ...this.feeds, [name]: feed }
        localStorage.setItem(FEEDS_KEY, JSON.stringify(this.feeds))
        setFeeds(this.feeds)
      },
      price,
      async send(label, tx) {
        setSent((all) => [...all, { label }])
        const hash = await sendTransaction(config, { ...tx, chainId })
        setSent((all) => [...all.slice(0, -1), { label, hash }])
        const receipt = await waitForTransactionReceipt(config, { hash, chainId })
        if (receipt.status !== 'success') throw new Error(`“${label}” was reverted.`)
        return receipt
      },
    }
    try {
      if (getAccount(config).chainId !== chainId) await switchChain(config, { chainId })
      await steps(io)
      setRun({ what, busy: false, done: true })
    } catch (e) {
      // A request that never got a hash (rejected in the wallet) leaves the list.
      setSent((all) => all.filter((s) => s.hash))
      setRun({ what, busy: false, error: (e as { shortMessage?: string }).shortMessage ?? (e as Error).message })
    }
  }
  const busy = !!run?.busy
  const notes = run?.what === 'notes' ? run : null
  const paid = run?.what === 'deposit' ? run : null
  const handed = run?.what === 'handover' ? run : null
  const atServer = !!owner.data && isAddressEqual(owner.data, SERVER)
  const waiting = sent.length > 0 && !sent[sent.length - 1].hash

  return (
    <div className="relative isolate min-h-dvh text-ink">
      <Starfield className="pointer-events-none absolute inset-0 -z-10 overflow-hidden bg-surface" />
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 bg-sky-veil-app" />
      <header className="sticky top-[max(0.75rem,env(safe-area-inset-top))] z-(--z-nav) px-4 sm:px-6 lg:px-8">
        <nav className="glass-strong mx-auto mt-3 flex h-14 max-w-app items-center justify-between gap-4 rounded-full pr-2 pl-5">
          <Link to="/" className="inline-flex items-center gap-2 text-ink">
            <Logo className="h-6 w-auto" />
            <Wordmark large />
          </Link>
          <div className="flex min-w-0 items-center">
            <NetworkChip />
            <ConnectButton showBalance={false} chainStatus="none" accountStatus="address" />
          </div>
        </nav>
      </header>

      <main className="mx-auto flex max-w-3xl flex-col gap-4 px-4 pt-10 pb-24 sm:px-6">
        <div className="mb-2 flex flex-col gap-2">
          <h1 className="type-title text-ink">Set up the testnet’s notes</h1>
          <p className="type-body-lg text-ink-muted">
            For the wallet that owns the Desk. One click starts it, then your wallet asks you to confirm each transaction: 22 in all.
            If you reject one or something fails, click again and it goes on from where it stopped.
          </p>
        </div>

        {!address && <Notice status={{ tone: 'info', icon: Info, label: 'Connect the Desk’s owner', message: 'Use the wallet button at the top right.' }} />}
        {address && owner.data && !isOwner && !atServer && (
          <Notice status={{ tone: 'abort', icon: WarningCircle, label: 'This wallet doesn’t own the Desk', message: `The owner is ${owner.data}. Only it can list notes.` }} />
        )}

        <Step n={1} title="Create and list the six notes" state={listed ? 'done' : 'current'}>
          <ul className="flex flex-col divide-y divide-line">
            {NOTES.map((n) => (
              <li key={`${n.name}-${n.strikeTime}`} className="flex items-center gap-3 py-2">
                <TickerBadge symbol={n.name.replace(/^RH/, '')} />
                <span className="type-body text-ink">Started {date(n.strikeTime)} at {usd(n.initial)}</span>
                <span className="ml-auto type-caption text-ink-muted">listed at {n.volBps / 100}% volatility</span>
              </li>
            ))}
          </ul>
          <p className="type-body text-ink-muted">
            4 price feeds with their real weekly prices and the price now, 2 transactions for the series and their past checks, then 16 for the Desk: each note listed with its spread, each feed with a risk budget.
          </p>
          <button type="button" disabled={!isOwner || busy} aria-busy={(busy && !!notes) || undefined} onClick={() => start('notes', async (io) => { setFeeds(await createNotes(io, a)); setListed(true) })} className={BUTTON}>
            {busy && notes ? (waiting ? 'Confirm in your wallet…' : 'Waiting for the network…') : notes?.error ? 'Go on from where it stopped' : 'Create the notes'}
          </button>
          {notes?.error && <Notice status={{ tone: 'abort', icon: WarningCircle, label: 'It stopped', message: notes.error }} />}
          {notes?.done && (
            <Notice status={{ tone: 'go', icon: CheckCircle, label: 'All six notes are listed', message: 'They show in the app now. Add the feeds below to deployments/46630.json, so the backend and testnet-notes.sh know them.' }}>
              <pre className="type-code overflow-x-auto text-ink">{JSON.stringify({ feeds }, null, 2)}</pre>
              <Link to="/app" className="type-label text-ink underline">Open the app</Link>
            </Notice>
          )}
        </Step>

        <Step n={2} title="Put USDG into the Desk" state={paid?.done ? 'done' : 'current'}>
          <p className="type-body text-ink-muted">
            The Desk pays for its side of every trade from this pot, and it holds nothing yet. About a fifth of it can be at risk on one coin or stock.
          </p>
          <label className="flex items-center gap-3 type-label text-ink">
            USDG
            <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" className="h-10 w-32 rounded-md border border-line-strong bg-surface-well px-3 type-data text-ink" />
          </label>
          <button type="button" disabled={!address || busy || !(Number(amount) > 0)} aria-busy={(busy && !!paid) || undefined} onClick={() => start('deposit', (io) => deposit(io, a, amount))} className={BUTTON}>
            {busy && paid ? (waiting ? 'Confirm in your wallet…' : 'Waiting for the network…') : 'Deposit'}
          </button>
          {paid?.error && <Notice status={{ tone: 'abort', icon: WarningCircle, label: 'It stopped', message: paid.error }} />}
          {paid?.done && <Notice status={{ tone: 'go', icon: CheckCircle, label: 'Deposited' }} />}
        </Step>

        <Step n={3} title="Hand the Desk to johba’s server" state={atServer ? 'done' : 'current'}>
          <p className="type-body text-ink-muted">
            One transaction makes <span className="type-code break-all text-ink">{SERVER}</span> the Desk’s owner. From then on only that wallet can list notes, and only it can hand the Desk back.
            The four price feeds stay with this wallet: a feed can’t change its owner.
          </p>
          <button
            type="button"
            disabled={!isOwner || busy}
            aria-busy={(busy && !!handed) || undefined}
            onClick={() => start('handover', async (io) => {
              await io.send('Hand the Desk to johba’s server', { to: a.desk, data: encodeFunctionData({ abi: ownerAbi, functionName: 'transferOwnership', args: [SERVER] }) })
              await owner.refetch()
            })}
            className={BUTTON}
          >
            {busy && handed ? (waiting ? 'Confirm in your wallet…' : 'Waiting for the network…') : 'Hand the Desk over'}
          </button>
          {handed?.error && <Notice status={{ tone: 'abort', icon: WarningCircle, label: 'It stopped', message: handed.error }} />}
          {atServer && <Notice status={{ tone: 'go', icon: CheckCircle, label: 'The Desk belongs to johba’s server', message: 'This wallet can no longer list notes.' }} />}
        </Step>

        {sent.length > 0 && (
          <section className="panel panel-sheer panel-sheer-firm rounded-lg p-5 sm:p-6">
            <h2 className="type-heading text-ink">Transactions</h2>
            <ol className="mt-4 flex flex-col gap-2">
              {sent.map((s, i) => (
                <li key={i} className="flex flex-wrap items-baseline justify-between gap-x-4 type-body text-ink">
                  <span>{i + 1}. {s.label}</span>
                  {s.hash ? <TxLink hash={s.hash} /> : <span className="type-label text-ink-muted">Confirm in your wallet…</span>}
                </li>
              ))}
            </ol>
          </section>
        )}
      </main>
    </div>
  )
}

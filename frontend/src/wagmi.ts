import { getDefaultConfig } from '@rainbow-me/rainbowkit'
import { injectedWallet } from '@rainbow-me/rainbowkit/wallets'
import { defineChain } from 'viem'
import { http } from 'wagmi'
import { robinhoodTestnet } from 'wagmi/chains'

const projectId = import.meta.env.VITE_WALLETCONNECT_PROJECT_ID
const DEVNODE_ID = 412346
const NETWORK_KEY = 'crashline.network'

// The build's own network: Robinhood testnet, unless VITE_CHAIN_ID names the dev node. VITE_RPC_URL and
// VITE_API_URL belong to it. The network picked in NetworkChip's menu wins over it, and when that is
// the other one it runs on its defaults: the dev node on localhost (8647 and 8650), the testnet on its
// public RPC and deployments/46630.json, as on GitHub Pages.
const builtFor = Number(import.meta.env.VITE_CHAIN_ID) === DEVNODE_ID ? DEVNODE_ID : robinhoodTestnet.id
const stored = Number(globalThis.localStorage?.getItem(NETWORK_KEY))
const picked = stored === DEVNODE_ID || stored === robinhoodTestnet.id ? stored : builtFor
const own = picked === builtFor
const rpcUrl: string | undefined = (own && import.meta.env.VITE_RPC_URL) || undefined

/** The Nitro dev node on localhost: this machine's own, or a host's through an SSH tunnel (docs/backend.md). Its clock moves only with transactions. */
export const crashlineDevnode = defineChain({
  id: DEVNODE_ID,
  name: 'Crashline dev node',
  nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
  rpcUrls: { default: { http: [(picked === DEVNODE_ID && rpcUrl) || 'http://localhost:8647'] } },
  testnet: true,
})

/** The chain the app talks to. Fixed while the page is loaded: setNetwork reloads it. */
export const chain = picked === DEVNODE_ID ? crashlineDevnode : robinhoodTestnet

/** The networks NetworkChip's menu offers. */
export const networks = [robinhoodTestnet, crashlineDevnode]

/** Moves the app to another network, with a reload. The two don't share notes, so the URL drops the one it names. */
export function setNetwork(id: number) {
  localStorage.setItem(NETWORK_KEY, String(id))
  const url = new URL(location.href)
  for (const k of ['series', 'open']) url.searchParams.delete(k)
  history.replaceState(null, '', url)
  location.reload()
}

/** The read service (docs/backend.md): the contract addresses come from its /config. Unset = no backend. */
export const API_URL: string | undefined = own ? import.meta.env.VITE_API_URL || undefined : picked === DEVNODE_ID ? 'http://localhost:8650' : undefined

// Unset falls back to the chain's own RPC (the public one on testnet, which is rate-limited).
// The dev node has no Multicall3, so its reads go out as one JSON-RPC batch instead.
const transport = http(rpcUrl, { batch: chain.id === crashlineDevnode.id })

export const config = getDefaultConfig({
  appName: 'Crashline',
  // RainbowKit's default wallets need a WalletConnect project ID; without one, offer browser wallets only.
  projectId: projectId ?? '',
  wallets: projectId ? undefined : [{ groupName: 'Browser wallets', wallets: [injectedWallet] }],
  chains: [chain],
  transports: { [robinhoodTestnet.id]: transport, [crashlineDevnode.id]: transport },
})

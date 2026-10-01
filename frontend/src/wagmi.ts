import { getDefaultConfig } from '@rainbow-me/rainbowkit'
import { injectedWallet } from '@rainbow-me/rainbowkit/wallets'
import { defineChain } from 'viem'
import { http } from 'wagmi'
import { robinhoodTestnet } from 'wagmi/chains'

const projectId = import.meta.env.VITE_WALLETCONNECT_PROJECT_ID
const rpcUrl: string | undefined = import.meta.env.VITE_RPC_URL || undefined

/** The Nitro dev node, reached through an SSH tunnel (docs/backend.md). Its clock moves only with transactions. */
export const surrogateDevnode = defineChain({
  id: 412346,
  name: 'Surrogate Pricer dev node',
  nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
  rpcUrls: { default: { http: [rpcUrl ?? 'http://localhost:8647'] } },
  testnet: true,
})

/** The chain this build talks to: Robinhood testnet, unless VITE_CHAIN_ID names the dev node. */
export const chain = Number(import.meta.env.VITE_CHAIN_ID) === surrogateDevnode.id ? surrogateDevnode : robinhoodTestnet

/** The read service (docs/backend.md): the contract addresses come from its /config. Unset = no backend. */
export const API_URL: string | undefined = import.meta.env.VITE_API_URL || undefined

// Unset falls back to the chain's own RPC (the public one on testnet, which is rate-limited).
// The dev node has no Multicall3, so its reads go out as one JSON-RPC batch instead.
const transport = http(rpcUrl, { batch: chain.id === surrogateDevnode.id })

export const config = getDefaultConfig({
  appName: 'Surrogate Pricer',
  // RainbowKit's default wallets need a WalletConnect project ID; without one, offer browser wallets only.
  projectId: projectId ?? '',
  wallets: projectId ? undefined : [{ groupName: 'Browser wallets', wallets: [injectedWallet] }],
  chains: [chain],
  transports: { [robinhoodTestnet.id]: transport, [surrogateDevnode.id]: transport },
})

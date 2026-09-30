import { getDefaultConfig } from '@rainbow-me/rainbowkit'
import { injectedWallet } from '@rainbow-me/rainbowkit/wallets'
import { http } from 'wagmi'
import { robinhoodTestnet } from 'wagmi/chains'

const projectId = import.meta.env.VITE_WALLETCONNECT_PROJECT_ID

export const config = getDefaultConfig({
  appName: 'Surrogate Pricer',
  // RainbowKit's default wallets need a WalletConnect project ID; without one, offer browser wallets only.
  projectId: projectId ?? '',
  wallets: projectId ? undefined : [{ groupName: 'Browser wallets', wallets: [injectedWallet] }],
  chains: [robinhoodTestnet],
  // Unset falls back to the public RPC, which is rate-limited.
  transports: { [robinhoodTestnet.id]: http(import.meta.env.VITE_RPC_URL) },
})

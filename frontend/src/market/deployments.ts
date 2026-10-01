import { useQuery } from '@tanstack/react-query'
import type { Address } from 'viem'
import { robinhoodTestnet } from 'wagmi/chains'
import { API_URL, chain } from '../wagmi'

/** The contracts the dashboard is given. Everything else (quoter, series, feeds, pricers) is read from the Desk. */
export type Deployment = { desk: Address; usdg: Address }

// The dev node's addresses change with every /demo/reset, so /config is re-read.
const REFETCH_MS = 15_000

// Testnet without a backend: deployments/46630.json as contracts/script/Deploy.s.sol writes it.
// A glob, so that a missing file (nothing deployed yet) is an empty match, not a build error.
const files = import.meta.glob<Deployment>('../../../deployments/46630.json', {
  eager: true,
  import: 'default',
})
const fromFile: Deployment | null = chain.id === robinhoodTestnet.id ? (Object.values(files)[0] ?? null) : null

/** GET /config of the read service (docs/backend.md), reduced to what we use. */
export function fromConfig(json: unknown, chainId: number): Deployment {
  const c = json as { chainId?: number; addresses?: { desk?: Address; usdg?: Address } }
  if (c.chainId !== chainId) throw new Error(`The backend serves chain ${c.chainId}, but this app is set to chain ${chainId}.`)
  if (!c.addresses?.desk || !c.addresses.usdg) throw new Error('The backend’s /config has no Desk or USDG address.')
  return { desk: c.addresses.desk, usdg: c.addresses.usdg }
}

/**
 * Where the contracts are: the backend's /config when VITE_API_URL is set, else the deployments
 * file. Not looked up while `live` is off (test mode shows the example market).
 */
export function useDeployment(live: boolean): { deployment: Deployment | null; isLoading: boolean; error: Error | null } {
  const query = useQuery({
    queryKey: ['config', API_URL, chain.id],
    enabled: API_URL !== undefined && live,
    queryFn: async ({ signal }) => {
      const res = await fetch(`${API_URL}/config`, { signal })
      if (!res.ok) throw new Error(`The backend answered ${res.status}.`)
      return fromConfig(await res.json(), chain.id)
    },
    refetchInterval: REFETCH_MS,
    retry: 1,
  })
  if (!live) return { deployment: null, isLoading: false, error: null }
  if (API_URL === undefined) return { deployment: fromFile, isLoading: false, error: null }
  return { deployment: query.data ?? null, isLoading: query.isLoading, error: query.error }
}

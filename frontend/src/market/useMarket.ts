import { deployment } from './deployments.ts'
import { fixtureMarket } from './fixtures.ts'
import type { MarketData } from './types.ts'
import { useChainMarket } from './useChainMarket.ts'

/** The market the dashboard shows: the chain once deployments/46630.json exists, sample data until then. */
export function useMarket(): { data: MarketData | undefined; isLoading: boolean; error: Error | null } {
  const chain = useChainMarket(deployment !== null)
  return deployment ? chain : { data: fixtureMarket, isLoading: false, error: null }
}

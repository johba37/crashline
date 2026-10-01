import { type Deployment, useDeployment } from './deployments'
import { fixtureMarket } from './fixtures.ts'
import type { MarketData } from './types.ts'
import { useChainMarket } from './useChainMarket.ts'

/**
 * The market the dashboard shows: the chain once the contracts' addresses are known, example
 * data until then. `unreachable` is set when a backend is configured but didn't answer.
 */
export function useMarket(): {
  data: MarketData | undefined
  isLoading: boolean
  error: Error | null
  deployment: Deployment | null
  unreachable: Error | null
} {
  const { deployment, isLoading, error: unreachable } = useDeployment()
  const chain = useChainMarket(deployment)
  if (deployment) return { ...chain, deployment, unreachable: null }
  return { data: isLoading ? undefined : fixtureMarket, isLoading, error: null, deployment: null, unreachable }
}

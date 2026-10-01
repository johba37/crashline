import { type Deployment, useDeployment } from './deployments'
import { fixtureMarket } from './fixtures.ts'
import type { MarketData } from './types.ts'
import { useChainMarket } from './useChainMarket.ts'

/**
 * The market the dashboard shows. Test mode: the example market, with every kind of note. Else
 * only what is really open: the chain, once the contracts' addresses are known. `unreachable`
 * is set when a backend is configured but didn't answer.
 */
export function useMarket(test: boolean): {
  data: MarketData | undefined
  isLoading: boolean
  error: Error | null
  deployment: Deployment | null
  unreachable: Error | null
} {
  const { deployment, isLoading, error: unreachable } = useDeployment(!test)
  const chain = useChainMarket(deployment)
  if (test) return { data: fixtureMarket, isLoading: false, error: null, deployment: null, unreachable: null }
  if (deployment) return { ...chain, deployment, unreachable: null }
  return { data: undefined, isLoading, error: null, deployment: null, unreachable }
}

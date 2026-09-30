import type { Address, Hex } from 'viem'

/** deployments/46630.json as contracts/script/Deploy.s.sol writes it with --broadcast. */
export type Deployment = {
  chainId: number
  usdg: Address
  desk: Address
  noteQuoter: Address
  seriesFactory: Address
  seriesImplementation: Address
  tokenImplementation: Address
  mockFeed: Address // staged demo feed; each series names its own feed in its terms
  fixingsRecorder: Address // the mock feed's recorder
  curator: Address
  surrogatePricer: Address // zero when deployed without PRICER; listings name their own pricer
  pricerWeightsHash: Hex
}

// A glob, so that a missing file (nothing deployed yet) is an empty match, not a build error.
const files = import.meta.glob<Deployment>('../../../deployments/46630.json', {
  eager: true,
  import: 'default',
})

export const deployment: Deployment | null = Object.values(files)[0] ?? null

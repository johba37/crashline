import { fixturePositions } from './fixtures.ts'
import type { Position } from './types.ts'

/**
 * What the wallet holds. Test mode: one example of each kind. Otherwise nothing yet: the live
 * reader isn't written (the backend's /accounts/{address} has the holdings and what was paid,
 * /series/{address}/history the price path, docs/backend.md).
 */
export function usePositions(test: boolean): { positions: Position[]; supported: boolean } {
  return test ? { positions: fixturePositions, supported: true } : { positions: [], supported: false }
}

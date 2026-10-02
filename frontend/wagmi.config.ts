import { defineConfig } from '@wagmi/cli'
import type { Abi } from 'viem'
import aggregator from '../abi/IAggregatorV3.json' with { type: 'json' }
import deskCover from '../abi/IDeskCover.json' with { type: 'json' }
import deskQueue from '../abi/IDeskQueue.json' with { type: 'json' }
import fixingsRecorder from '../abi/IFixingsRecorder.json' with { type: 'json' }
import noteQuoter from '../abi/INoteQuoter.json' with { type: 'json' }
import noteSeries from '../abi/INoteSeries.json' with { type: 'json' }
import seriesFactory from '../abi/ISeriesFactory.json' with { type: 'json' }
import seriesToken from '../abi/ISeriesToken.json' with { type: 'json' }
import surrogatePricer from '../abi/ISurrogatePricer.json' with { type: 'json' }

const errorsOf = (abi: Abi): Abi => abi.filter((item) => item.type === 'error')

/** Concatenates ABIs, keeping the first item of each signature. */
const merge = (...abis: Abi[]): Abi => {
  const seen = new Set<string>()
  return abis.flat().filter((item) => {
    const name = 'name' in item ? item.name : ''
    const inputs = 'inputs' in item ? item.inputs.map((i) => i.type).join(',') : ''
    const signature = `${item.type} ${name}(${inputs})`
    if (seen.has(signature)) return false
    seen.add(signature)
    return true
  })
}

// Reverts are decoded against the ABI of the contract called, so the Desk and the quoter also
// carry the errors they bubble up: the quoter's and the pricer's, and the series' (the Desk
// reverts ZeroAmount itself and calls into the series on trades).
export default defineConfig({
  out: 'src/market/abi.ts',
  contracts: [
    { name: 'Aggregator', abi: aggregator as Abi },
    {
      name: 'Desk',
      abi: merge(
        deskCover as Abi,
        deskQueue as Abi,
        errorsOf(noteQuoter as Abi),
        errorsOf(surrogatePricer as Abi),
        errorsOf(noteSeries as Abi),
      ),
    },
    { name: 'FixingsRecorder', abi: fixingsRecorder as Abi },
    { name: 'NoteQuoter', abi: merge(noteQuoter as Abi, errorsOf(surrogatePricer as Abi)) },
    { name: 'NoteSeries', abi: noteSeries as Abi },
    { name: 'SeriesFactory', abi: seriesFactory as Abi },
    { name: 'SeriesToken', abi: seriesToken as Abi },
    { name: 'SurrogatePricer', abi: surrogatePricer as Abi },
  ],
})

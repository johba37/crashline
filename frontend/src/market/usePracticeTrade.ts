import { useCallback, useRef, useState } from 'react'
import type { TradeState } from './types'

const STEP_MS = 900

/** useTrade's stand-in on example data: walks through the same steps, and sends nothing. */
export function usePracticeTrade(): { state: TradeState; run(): Promise<void>; reset(): void } {
  const [state, setState] = useState<TradeState>({ step: 'idle' })
  // Bumped by every run and by reset, so a run that was left behind can't overwrite the state.
  const current = useRef(0)

  const run = useCallback(async () => {
    const id = ++current.current
    for (const step of ['quoting', 'approving', 'trading', 'done'] as const) {
      if (id !== current.current) return
      setState({ step })
      if (step !== 'done') await new Promise((resolve) => setTimeout(resolve, STEP_MS))
    }
  }, [])

  const reset = useCallback(() => {
    current.current++
    setState({ step: 'idle' })
  }, [])

  return { state, run, reset }
}

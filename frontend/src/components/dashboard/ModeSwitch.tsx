import { useRef, type KeyboardEvent } from 'react'
import InfoTip from '../Tooltip.tsx'

const MODES = [
  { test: false, label: 'Testnet', tip: 'Live on Robinhood Chain’s test network: only the notes that are really open. Your wallet signs each order, with test money, not real money.' },
  { test: true, label: 'Prototype', tip: 'Example stocks and notes, to try every step. Nothing is bought and your wallet isn’t asked for anything.' },
]

/** Which market the dashboard shows: the test network, or the example market. Two radios in one
    track, each followed by its info icon (next to the radio, never inside it). */
export default function ModeSwitch({ test, onChange }: { test: boolean; onChange: (test: boolean) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])

  const onKeyDown = (e: KeyboardEvent) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return
    e.preventDefault()
    onChange(!test)
    refs.current[test ? 0 : 1]?.focus()
  }

  return (
    <div role="radiogroup" aria-label="Which market to show" className="inline-flex rounded-full bg-surface-well p-1 shadow-[inset_0_0_0_1px_var(--color-line)]">
      {MODES.map((m, i) => {
        const on = m.test === test
        return (
          <span
            key={m.label}
            className={`inline-flex h-8 items-center rounded-full pr-1.5 transition-colors duration-160 ${on ? 'bg-surface-overlay text-ink shadow-[inset_0_0_0_1px_var(--color-line)]' : 'text-ink-muted'}`}
          >
            <button
              ref={(el) => { refs.current[i] = el }}
              type="button"
              role="radio"
              aria-checked={on}
              tabIndex={on ? 0 : -1}
              onClick={() => onChange(m.test)}
              onKeyDown={onKeyDown}
              className="h-8 rounded-full pr-1 pl-3 type-label whitespace-nowrap transition-colors duration-160 hover:text-ink"
            >
              {m.label}
            </button>
            <InfoTip label={m.label}>{m.tip}</InfoTip>
          </span>
        )
      })}
    </div>
  )
}

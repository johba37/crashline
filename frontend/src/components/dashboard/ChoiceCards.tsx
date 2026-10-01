import { useRef, type KeyboardEvent, type ReactNode } from 'react'

type Choice<T extends string> = { value: T; title: string; body: ReactNode; aside?: ReactNode; icon?: ReactNode }

/** Big radio cards for a decision (a stock, a goal): one tab stop, arrow keys move the choice. */
export default function ChoiceCards<T extends string>({
  label, choices, value, onChange,
}: { label: string; choices: Choice<T>[]; value?: T; onChange: (value: T) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const index = choices.findIndex((c) => c.value === value)

  const onKeyDown = (e: KeyboardEvent) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0
    if (!step) return
    e.preventDefault()
    const next = (Math.max(index, 0) + step + choices.length) % choices.length
    onChange(choices[next].value)
    refs.current[next]?.focus()
  }

  return (
    <div role="radiogroup" aria-label={label} onKeyDown={onKeyDown} className="grid gap-3 sm:grid-cols-2">
      {choices.map((c, i) => {
        const on = i === index
        return (
          <button
            key={c.value}
            ref={(el) => { refs.current[i] = el }}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on || (index < 0 && i === 0) ? 0 : -1}
            onClick={() => onChange(c.value)}
            className={`flex items-start gap-3 rounded-md p-4 text-left transition-[background-color,box-shadow] duration-160 ${
              on ? 'bg-surface-overlay shadow-[inset_0_0_0_2px_var(--color-accent)]' : 'bg-surface-well shadow-[inset_0_0_0_1px_var(--color-line-strong)] hover:bg-surface-overlay'
            }`}
          >
            {c.icon && <span aria-hidden="true" className={on ? 'text-accent-text' : 'text-ink-muted'}>{c.icon}</span>}
            <span className="flex min-w-0 flex-1 flex-col gap-1">
              <span className="flex items-baseline justify-between gap-3">
                <span className="type-label text-ink">{c.title}</span>
                {c.aside && <span className="type-data text-ink">{c.aside}</span>}
              </span>
              <span className="type-body text-ink-muted">{c.body}</span>
            </span>
          </button>
        )
      })}
    </div>
  )
}

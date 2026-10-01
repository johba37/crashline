import { Check } from '@phosphor-icons/react'
import type { ReactNode } from 'react'

export type StepState = 'done' | 'current' | 'upcoming'

/** One step of the guided flow. Upcoming steps show only their title, so the path is visible. */
export default function Step({
  n, title, state, hint, children,
}: { n: number; title: ReactNode; state: StepState; hint?: string; children?: ReactNode }) {
  return (
    <section aria-labelledby={`step-${n}`} className="panel rounded-lg p-5 sm:p-6">
      <div className="flex items-center gap-3">
        <span
          aria-hidden="true"
          className={`grid size-8 shrink-0 place-items-center rounded-full type-label ${
            state === 'current' ? 'bg-accent text-on-accent' : state === 'done' ? 'bg-surface-overlay text-ink' : 'bg-surface-well text-ink-faint'
          }`}
        >
          {state === 'done' ? <Check size={16} weight="bold" /> : n}
        </span>
        <h2 id={`step-${n}`} className={`type-heading ${state === 'upcoming' ? 'text-ink-muted' : 'text-ink'}`}>
          <span className="sr-only">Step {n}{state === 'done' ? ', done' : ''}: </span>
          {title}
        </h2>
      </div>
      {state === 'upcoming'
        ? hint && <p className="mt-2 pl-11 type-body text-ink-muted">{hint}</p>
        : <div className="mt-5 flex flex-col gap-5">{children}</div>}
    </section>
  )
}

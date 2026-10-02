import { Check } from '@phosphor-icons/react'
import type { ReactNode } from 'react'

export type StepState = 'done' | 'current' | 'upcoming'

/** One step of the guided flow. Upcoming steps show only their title, so the path is visible. */
export default function Step({
  n, title, state, hint, children,
}: { n: number; title: ReactNode; state: StepState; hint?: string; children?: ReactNode }) {
  return (
    // A step that isn't open yet stands back: thinner steel, and its title and hint at 70% (the floor: they
    // still read at 4.5:1 over the brightest sky). The content is dimmed, not the card: a card at 70% would
    // let the sky through unblurred. An open step is firmer steel, for its text.
    <section aria-labelledby={`step-${n}`} className={`panel panel-sheer rounded-lg p-5 sm:p-6 ${state === 'upcoming' ? '' : 'panel-sheer-firm'}`}>
      <div className={`flex items-center gap-3 transition-opacity duration-240 ${state === 'upcoming' ? 'opacity-70' : ''}`}>
        {/* Flex, not grid: it centres the number even when the text is larger than the circle expects (text zoom).
            One line high, with figures of equal width, so the number has the same room on every side. */}
        <span
          aria-hidden="true"
          className={`flex size-8 shrink-0 items-center justify-center rounded-full type-label leading-none tabular-nums ${
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
        ? hint && <p className="mt-2 pl-11 type-body text-ink-muted opacity-70">{hint}</p>
        : <div className="mt-5 flex flex-col gap-5">{children}</div>}
    </section>
  )
}

import { CaretDown } from '@phosphor-icons/react'
import type { ReactNode } from 'react'

/** A closed-by-default steel section for the details a first-time user doesn't need yet. */
export default function Disclosure({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <details className="panel group rounded-lg">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-lg p-5 [&::-webkit-details-marker]:hidden">
        <span className="flex flex-col gap-0.5">
          <span className="type-heading text-ink">{title}</span>
          <span className="type-body text-ink-muted">{hint}</span>
        </span>
        <CaretDown size={20} weight="bold" aria-hidden="true" className="shrink-0 text-ink-muted transition-transform duration-240 group-open:rotate-180" />
      </summary>
      <div className="border-t border-line p-5">{children}</div>
    </details>
  )
}

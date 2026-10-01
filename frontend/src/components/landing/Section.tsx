import type { ReactNode } from 'react'

/** A landing section below the hero (spec layout.landing.sections). The first one has no divider:
    the hero fades into it. */
export default function Section({
  id,
  title,
  intro,
  divider = true,
  children,
}: {
  id: string
  title: string
  intro: ReactNode
  divider?: boolean
  children: ReactNode
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className={`scroll-mt-24 ${divider ? 'border-t border-line' : ''}`}>
      <div className="mx-auto max-w-landing px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
        <h2 id={`${id}-title`} className="type-display text-ink">
          {title}
        </h2>
        <p className="mt-4 max-w-prose type-body-lg text-ink-muted">{intro}</p>
        {children}
      </div>
    </section>
  )
}

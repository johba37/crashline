import type { ReactNode } from 'react'

/** A link that opens in a new tab. By default an inline text link: ink with an orange underline,
    readable on the page ground and on steel. Pass className to style it differently. */
export default function ExternalLink({ href, className, children }: { href: string; className?: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className={
        className ??
        'text-ink underline decoration-accent-text underline-offset-4 transition-colors duration-160 ease-out hover:text-accent-text'
      }
    >
      {children}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  )
}

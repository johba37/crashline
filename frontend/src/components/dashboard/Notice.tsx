import type { ReactNode } from 'react'
import { toneClass, type Status } from './status.ts'

/** Explains a paused quote, a refusal or a next step, inline where it applies. */
export default function Notice({ status, children }: { status: Status; children?: ReactNode }) {
  const Icon = status.icon
  const [bg, fg] = toneClass[status.tone].split(' ')
  return (
    <div role={status.tone === 'abort' ? 'alert' : 'status'} className={`flex gap-3 rounded-md p-4 ${bg}`}>
      <Icon size={24} weight="bold" aria-hidden="true" className={`shrink-0 ${fg}`} />
      <div className="flex flex-col gap-1">
        <p className="type-label text-ink">{status.label}</p>
        {status.message && <p className="type-body text-ink-muted">{status.message}</p>}
        {children}
      </div>
    </div>
  )
}

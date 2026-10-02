import { toneClass, type Status } from './status.ts'

/** A series or transaction state in two or three words: icon and word, never colour alone. */
export default function StatusChip({ status }: { status: Status }) {
  const Icon = status.icon
  return (
    <span className={`inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full px-3 type-label whitespace-nowrap ${toneClass[status.tone]}`}>
      <Icon size={16} weight="bold" aria-hidden="true" />
      {status.label}
    </span>
  )
}

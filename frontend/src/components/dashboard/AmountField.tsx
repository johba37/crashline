import { WarningCircle } from '@phosphor-icons/react'
import { useId, type ReactNode } from 'react'

/** Enter a USDG amount. Validated by the caller on blur and submit, not per keystroke. */
export default function AmountField({
  label, info, unit, value, placeholder = '0.00', onChange, onBlur, helper, error,
}: {
  label: string
  info?: ReactNode // an InfoTip, kept outside the <label> so a tap on it doesn't focus the input
  unit: string
  value: string
  placeholder?: string
  onChange: (value: string) => void
  onBlur?: () => void
  helper?: ReactNode
  error?: string
}) {
  const id = useId()
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-1">
        <label htmlFor={id} className="type-label text-ink">{label}</label>
        {info}
      </div>
      <div className="flex h-12 items-center gap-2 rounded-md border border-line-strong bg-surface-well px-4 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-focus">
        <input
          id={id}
          inputMode="decimal"
          autoComplete="off"
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          aria-invalid={error ? true : undefined}
          aria-describedby={`${id}-help`}
          className="min-w-0 flex-1 bg-transparent type-data text-ink outline-none placeholder:text-ink-muted"
        />
        <span className="type-label text-ink-muted">{unit}</span>
      </div>
      <p id={`${id}-help`} className={`flex items-center gap-1.5 type-label ${error ? 'text-abort' : 'text-ink-muted'}`}>
        {error && <WarningCircle size={16} weight="bold" aria-hidden="true" />}
        {error ?? helper}
      </p>
    </div>
  )
}

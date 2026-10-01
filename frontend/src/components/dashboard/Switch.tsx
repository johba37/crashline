/** An on/off switch with its label in front: one button, role="switch". */
export default function Switch({
  label, hint, checked, onChange,
}: { label: string; hint?: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      title={hint}
      onClick={() => onChange(!checked)}
      className="group inline-flex h-10 items-center gap-2 rounded-full px-2 type-label text-ink outline-none"
    >
      {label}
      <span
        aria-hidden="true"
        className={`relative h-6 w-10 shrink-0 rounded-full transition-colors duration-160 group-focus-visible:outline-2 group-focus-visible:outline-offset-2 group-focus-visible:outline-focus ${
          checked ? 'bg-accent' : 'bg-surface-well shadow-[inset_0_0_0_1px_var(--color-line-strong)]'
        }`}
      >
        <span className={`absolute top-1 left-1 size-4 rounded-full transition-transform duration-160 ${checked ? 'translate-x-4 bg-on-accent' : 'bg-ink-muted'}`} />
      </span>
    </button>
  )
}

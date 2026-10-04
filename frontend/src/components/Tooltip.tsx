import { Info } from '@phosphor-icons/react'
import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

const GAP = 8 // px between the icon and the tip, and to the viewport edge

/**
 * An info icon that explains a word. Hover (after 300ms) or keyboard focus shows the tip; a
 * tap or click pins it, Escape or a tap elsewhere closes it. Screen readers get the text as the
 * button's description, so it never depends on the popover. `mark` swaps the icon for a button
 * of its own look (its name and classes), such as a mark on a graph.
 */
export default function InfoTip({ label, mark, children }: { label: string; mark?: { name: string; className: string }; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  const tip = useRef<HTMLDivElement>(null)
  const timer = useRef(0)
  const id = useId()

  const close = () => {
    window.clearTimeout(timer.current)
    setOpen(false)
    setPinned(false)
  }

  // Above the icon when there's room, else below; clamped to the viewport's sides.
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const b = button.current?.getBoundingClientRect()
      const t = tip.current?.getBoundingClientRect()
      if (!b || !t) return
      const top = b.top - t.height - GAP >= GAP ? b.top - t.height - GAP : b.bottom + GAP
      const left = Math.min(Math.max(GAP, b.left + b.width / 2 - t.width / 2), window.innerWidth - t.width - GAP)
      setPos({ left, top })
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close()
    const onDown = (e: PointerEvent) => {
      const target = e.target as Node
      if (!button.current?.contains(target) && !tip.current?.contains(target)) close()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onDown)
    }
  }, [open])

  return (
    <>
      <button
        ref={button}
        type="button"
        aria-label={mark?.name ?? `What does “${label}” mean?`}
        aria-describedby={id}
        aria-expanded={open}
        onPointerEnter={(e) => {
          if (e.pointerType !== 'mouse') return
          window.clearTimeout(timer.current)
          timer.current = window.setTimeout(() => setOpen(true), 300)
        }}
        onPointerLeave={(e) => {
          if (e.pointerType !== 'mouse') return
          window.clearTimeout(timer.current)
          if (!pinned) setOpen(false)
        }}
        onFocus={(e) => e.currentTarget.matches(':focus-visible') && setOpen(true)}
        onBlur={() => !pinned && setOpen(false)}
        onClick={() => (pinned ? close() : (setPinned(true), setOpen(true)))}
        className={mark?.className ?? 'relative -my-1 inline-grid size-6 shrink-0 place-items-center rounded-full align-middle text-ink-muted transition-colors duration-160 hover:text-ink aria-expanded:text-ink after:absolute after:-inset-2'}
      >
        {!mark && <Info size={16} weight="bold" aria-hidden="true" />}
      </button>
      <span id={id} hidden>{children}</span>
      {open && createPortal(
        <div
          ref={tip}
          aria-hidden="true"
          style={{ left: pos?.left ?? 0, top: pos?.top ?? 0, visibility: pos ? 'visible' : 'hidden' }}
          className="glass-strong fixed z-(--z-tooltip) max-w-72 rounded-sm px-3 py-2 type-caption text-ink"
        >
          {children}
        </div>,
        document.body,
      )}
    </>
  )
}

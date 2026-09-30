import { type RefObject, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { GITHUB } from './landing/links.ts'

// Sections on the landing page. Labels stay short so the links fit next to the wordmark at md.
const LINKS = [
  { label: 'How it works', href: '#how-it-works' },
  { label: 'Check prices', href: '#verify' },
  { label: 'Risks', href: '#risks' },
  { label: 'Roadmap', href: '#roadmap' },
]

const LINK_CLASS =
  'inline-flex h-10 items-center rounded-full px-3 type-label text-ink-muted transition-colors duration-160 ease-out hover:bg-surface-overlay hover:text-ink'

/** Floating glass nav. Hidden while `revealAfter` (the hero's call to action) is on screen or
    below it, so exactly one primary button is visible at a time. */
export default function NavBar({ revealAfter }: { revealAfter: RefObject<HTMLElement | null> }) {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const target = revealAfter.current
    if (!target) return
    const observer = new IntersectionObserver(([entry]) =>
      setVisible(!entry.isIntersecting && entry.boundingClientRect.top < 0),
    )
    observer.observe(target)
    return () => observer.disconnect()
  }, [revealAfter])

  return (
    <header
      data-visible={visible || undefined}
      inert={!visible}
      className="fixed inset-x-0 top-[max(0.75rem,env(safe-area-inset-top))] z-(--z-nav) -translate-y-4 px-4 opacity-0 transition-[translate,opacity] duration-160 ease-in sm:px-6 data-visible:translate-y-0 data-visible:opacity-100 data-visible:duration-(--duration-spring-smooth) data-visible:ease-spring-smooth"
    >
      <nav
        aria-label="Main"
        className="glass-strong mx-auto flex h-14 max-w-app items-center gap-2 rounded-full pr-2 pl-5 [--elevation-glass:var(--elevation-glass-lifted)]"
      >
        <a href="#top" className="type-wordmark whitespace-nowrap text-ink">
          Surrogate Pricer
        </a>
        <ul className="ml-auto hidden items-center gap-1 md:flex">
          {LINKS.map((link) => (
            <li key={link.href}>
              <a href={link.href} className={LINK_CLASS}>
                {link.label}
              </a>
            </li>
          ))}
          {/* From lg only: at md the five links and the button don't fit next to the wordmark. */}
          <li className="hidden lg:block">
            <a href={GITHUB.readme} target="_blank" rel="noreferrer" className={LINK_CLASS}>
              Docs
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </li>
        </ul>
        <Link
          to="/app"
          className="ml-auto inline-flex h-10 items-center rounded-full bg-accent px-4 type-button text-on-accent transition-[background-color,box-shadow] duration-160 ease-out hover:bg-accent-hover hover:shadow-ignition active:bg-accent-pressed md:ml-2"
        >
          Open the app
        </Link>
      </nav>
    </header>
  )
}

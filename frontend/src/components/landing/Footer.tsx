import { BookOpen, GithubLogo } from '@phosphor-icons/react'
import Logo from '../Logo.tsx'
import Wordmark from '../Wordmark.tsx'
import ExternalLink from './ExternalLink.tsx'
import { GITHUB } from './links.ts'

const LINK = 'inline-flex min-h-8 items-center gap-2 type-body text-ink-muted transition-colors duration-160 ease-out hover:text-ink pointer-coarse:min-h-11'

const PROJECT = [
  { label: 'GitHub repo', href: GITHUB.repo, Icon: GithubLogo },
  { label: 'Read the docs', href: GITHUB.readme, Icon: BookOpen },
  { label: 'All docs', href: GITHUB.docs },
]

const KEY_DOCS = [
  { label: 'Architecture', href: GITHUB.architecture },
  { label: 'Contract interfaces', href: GITHUB.interfaces },
  { label: 'Model accuracy', href: GITHUB.accuracy },
  { label: 'The simulation', href: GITHUB.simulation },
  { label: 'Contracts review', href: GITHUB.contractsReview },
  { label: 'Perpetual note design', href: GITHUB.perpetualNote },
]

export default function Footer() {
  return (
    <footer className="border-t border-line bg-sky-veil">
      <div className="mx-auto grid max-w-landing gap-10 px-4 py-12 sm:px-6 md:grid-cols-12 md:gap-8 lg:px-8 lg:py-16">
        <div className="md:col-span-6">
          <p className="inline-flex items-center gap-2 text-ink">
            <Logo className="h-7 w-auto" />
            <Wordmark />
          </p>
          <p className="mt-3 max-w-sm type-body text-ink-muted">
            Crash insurance and a weekly income on coins and stocks, priced in public on Robinhood Chain testnet.
          </p>
        </div>
        <nav aria-labelledby="footer-project" className="md:col-span-3">
          <h2 id="footer-project" className="type-label text-ink">
            Project
          </h2>
          <ul className="mt-3">
            {PROJECT.map(({ label, href, Icon }) => (
              <li key={href}>
                <ExternalLink href={href} className={LINK}>
                  {Icon && <Icon size={20} aria-hidden="true" />}
                  {label}
                </ExternalLink>
              </li>
            ))}
          </ul>
        </nav>
        <nav aria-labelledby="footer-docs" className="md:col-span-3">
          <h2 id="footer-docs" className="type-label text-ink">
            Key docs
          </h2>
          <ul className="mt-3">
            {KEY_DOCS.map(({ label, href }) => (
              <li key={href}>
                <ExternalLink href={href} className={LINK}>
                  {label}
                </ExternalLink>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </footer>
  )
}

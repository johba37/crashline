import { ConnectButton } from '@rainbow-me/rainbowkit'
import { Link } from 'react-router'

export default function AppPage() {
  return (
    <main className="mx-auto max-w-4xl px-4 py-6">
      <header className="flex items-center justify-between">
        <Link to="/" className="type-wordmark text-ink">
          Surrogate Pricer
        </Link>
        <ConnectButton />
      </header>
      <p className="mt-16 text-center type-body text-ink-muted">
        App coming: market list, note detail and buy.
      </p>
    </main>
  )
}

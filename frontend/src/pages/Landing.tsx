import { Link } from 'react-router'

export default function Landing() {
  return (
    <main className="mx-auto max-w-2xl px-4 py-16">
      <h1 className="type-display-xl text-ink">The Big Short, fixed</h1>
      <p className="mt-4 type-body-lg text-ink-muted">
        On-chain fair-value quotes for autocallable notes on stock tokens, fully collateralized and
        settled in USDG on Robinhood Chain.
      </p>
      <Link
        to="/app"
        className="mt-8 inline-flex h-12 items-center rounded-full bg-accent px-6 type-button text-on-accent transition-[background-color,box-shadow] duration-160 ease-out hover:bg-accent-hover hover:shadow-ignition active:bg-accent-pressed"
      >
        Open the app
      </Link>
    </main>
  )
}

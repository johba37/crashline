import { Link } from 'react-router'

export default function Landing() {
  return (
    <main className="mx-auto max-w-2xl px-4 py-16">
      <h1 className="text-4xl font-bold">The Big Short, fixed</h1>
      <p className="mt-4 text-lg text-gray-600">
        On-chain fair-value quotes for autocallable notes on stock tokens, fully collateralized and
        settled in USDG on Robinhood Chain.
      </p>
      <Link
        to="/app"
        className="mt-8 inline-block rounded-lg bg-black px-5 py-3 font-medium text-white"
      >
        Open the app
      </Link>
    </main>
  )
}

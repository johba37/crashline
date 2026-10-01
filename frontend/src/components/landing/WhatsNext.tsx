import ExternalLink from './ExternalLink.tsx'
import { GITHUB } from './links.ts'
import Section from './Section.tsx'

// Sources: the README roadmap (PR #2 version: M1 external review of the core, M2 mainnet on
// Robinhood Chain with real feeds and conservative caps), docs/interfaces.md and
// docs/architecture.md (the model never sees which stock it prices), docs/k3-vol-input.md (vol is a
// live input, certified 20-90%), docs/multi-stock-jumps.md (TSLA's jump shape on 12 more stocks:
// AAPL, MSFT, AMZN, GOOGL, NVDA within 7.6 bps of their own fit; META, NFLX, PLTR, with rare large
// drops, 16-27 bps off), docs/v2-perpetual-note.md (no expiry, one token pair per stock, a share of the pool paid
// out at every weekly fixing, closed form plus a learned correction).
// After PR #2 merges: link the README's roadmap section.

const NEXT = [
  {
    title: 'Mainnet',
    text: 'After an outside review of the contracts, the notes, the model and the Desk move to Robinhood Chain’s main network. Real price feeds, with small limits at first.',
  },
  {
    title: 'More stocks',
    text: 'The model never sees which stock it prices, only its moves relative to the starting price and how much it swings. It is tested for swings from 20% to 90% a year, so one model can serve many stocks. Its sudden jumps are shaped on TSLA: a check on 12 more stocks found that shape close for large companies like Apple and Microsoft, and further off for stocks with rare, very large drops.',
  },
  {
    title: 'A note that never ends',
    text: 'The perpetual note has no end date: one Earn and one Protect token per stock, paying out a little at every weekly check. Its price comes from a formula plus a small learned correction.',
    link: { href: GITHUB.perpetualNote, label: 'Read the perpetual note design' },
  },
]

export default function WhatsNext() {
  return (
    <Section
      id="roadmap"
      title="What’s next"
      intro="Today Surrogate Pricer runs on a test network with one stock, TSLA. Here is what comes next."
    >
      <div className="panel mt-10 grid divide-y divide-line rounded-lg md:grid-cols-3 md:divide-x md:divide-y-0">
        {NEXT.map((item) => (
          <div key={item.title} className="p-5 sm:p-6">
            <h3 className="type-heading text-ink">{item.title}</h3>
            <p className="mt-2 type-body text-ink-muted">{item.text}</p>
            {item.link && (
              <p className="mt-4 type-body">
                <ExternalLink href={item.link.href}>{item.link.label}</ExternalLink>
              </p>
            )}
          </div>
        ))}
      </div>
    </Section>
  )
}

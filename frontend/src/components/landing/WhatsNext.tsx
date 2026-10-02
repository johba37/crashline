import ExternalLink from './ExternalLink.tsx'
import { GITHUB } from './links.ts'
import Section from './Section.tsx'

// Sources: the README roadmap (PR #2 version: M1 external review of the core, M2 mainnet on
// Robinhood Chain with real feeds and conservative caps), docs/interfaces.md and
// docs/architecture.md (one pricer per product, any stock; the Desk lists a series with its pricer,
// vol and cap, and sets a risk budget per feed), docs/v2-perpetual-note.md (no expiry, one token pair per stock, a share of the pool paid
// out at every weekly fixing, closed form plus a learned correction).
// After PR #2 merges: link the README's roadmap section.

const NEXT = [
  {
    title: 'The live network',
    text: 'After an outside review, everything moves to Robinhood Chain’s main network: real stock prices, small limits at first.',
  },
  {
    title: 'Adding a stock',
    text: 'A new stock needs only a price feed and a place at the Desk, not a new model.',
  },
  {
    title: 'A note that never ends',
    text: 'No end date: one Earn and one Protect token per stock, paying out a little at every weekly check.',
    link: { href: GITHUB.perpetualNote, label: 'Read the perpetual note design' },
  },
]

export default function WhatsNext() {
  return (
    <Section
      id="roadmap"
      title="What’s next"
      intro="Today Crashline runs on a test network. Here is what comes next."
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

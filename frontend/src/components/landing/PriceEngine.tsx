import { CaretDown, CaretRight, Coins, Umbrella } from '@phosphor-icons/react'
import stylusLogomark from '../../assets/stylus-logomark.svg'
import ExternalLink from './ExternalLink.tsx'
import { GITHUB, STYLUS_DOCS } from './links.ts'
import { FAN, NET, fanY, futures, net } from './priceArt.ts'

// How a price is made, as three plates: the simulation, the model that learned from it, the price.
// Under them, centred, a steel strip credits Stylus, which makes the on-chain part affordable.
// Sources: docs/k3-vol-input.md (the simulation plays out 2^18 = 262,144 paths for a test price;
// model/k3 is 10 -> 64 -> 48 -> 40 -> 40 -> 1 with 7,272 weights and 193 biases, 7,465 numbers, in
// 16-bit integers; ten inputs, of which five vary for k3: spot, vol, time to the next observation,
// observations left, the knock-in flag), ml/round3_runs (3.5M training examples), docs/backend.md
// (a teacher price takes about 1 s on an RTX 3090), contracts/src/interfaces/ISurrogatePricer.sol
// (priceBps is the clean value of one NOTE) and IDeskCover.sol (cover = the pot - NOTE). Stylus:
// docs.arbitrum.io/stylus/gentle-introduction (WASM contracts, e.g. Rust, next to Solidity ones)
// and /stylus/concepts/gas-metering ("Compute, which is generally 10-100x cheaper depending on the
// program"). Nothing in the repo measures this model in Solidity, so the strip says Stylus makes
// it "practical": never "only possible with", and no factor (the user dropped "cheaper" on
// 2026-10-02: the point is a fresh on-chain price anyone can verify). The ~45k gas in the README
// was measured on a smaller test model, not on k3.
// The logomark (assets/stylus-logomark.svg) is the primary one from Arbitrum's Stylus brand
// guidelines (arbitrumfoundation.notion.site/Stylus-brand-guidelines-86bc15ab368c4c748f8d7a4e105aa453),
// unchanged: never recolour it, and never show it below 12px.
// Prices: the example in HowItWorks.tsx.

const INPUTS = ['Stock price', 'How much it swings', 'Time to next check', 'Checks left', 'Crash line crossed?']
const PATHS = futures(16, 17)
const MODEL = net(INPUTS.length)
const PLATE_LINK = 'underline underline-offset-4'

/** An arrow between two plates: down on phones, to the right from lg. Decoration only. */
function Arrow({ label }: { label: string }) {
  return (
    <div aria-hidden="true" className="flex flex-col items-center justify-center lg:flex-row">
      <span className="h-4 w-0.5 bg-ink-muted lg:h-0.5 lg:w-4" />
      <span className="px-2 py-1 type-body font-medium text-ink">{label}</span>
      <span className="h-4 w-0.5 bg-ink-muted lg:h-0.5 lg:w-4" />
      <CaretDown size={24} weight="bold" className="-mt-4 mb-0.5 text-ink-muted lg:hidden" />
      <CaretRight size={24} weight="bold" className="-ml-4 hidden text-ink-muted lg:block" />
    </div>
  )
}

export default function PriceEngine() {
  return (
    <div className="mt-12">
      <h3 className="type-heading text-ink">Computed on-chain by AI</h3>
      <p className="mt-2 max-w-prose type-body text-ink-muted">
        If only the last day counted, a simple formula would give a note’s fair price. But a note is checked every
        week and can end early, so the exact way is to play out many possible futures. That is too much work for a
        blockchain, so a small AI model learned the answers and gives them there.
      </p>

      <div className="mt-8 grid lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1.3fr)_auto_minmax(0,1fr)]">
        <article className="plate rounded-md p-4 sm:p-5">
          <p className="type-caption text-ink-muted">Off the blockchain: the slow, exact way</p>
          <h4 className="mt-1 type-heading">A simulation plays out the futures</h4>
          <svg
            viewBox={`0 0 ${FAN.width} ${FAN.height}`}
            role="img"
            aria-label={`${PATHS.length} possible futures for a stock’s price over six months. ${PATHS.filter((p) => p.crashed).length} of them fall below the crash line.`}
            className="mt-4 w-full"
          >
            <line x1="12" x2={FAN.width - 12} y1={fanY(FAN.crash)} y2={fanY(FAN.crash)} stroke="currentColor" strokeDasharray="4 3" />
            <text x="12" y={fanY(FAN.crash) + 14} fontSize="13" fill="currentColor">
              crash line
            </text>
            {PATHS.map((path) => (
              <polyline
                key={path.points}
                points={path.points}
                fill="none"
                stroke="currentColor"
                strokeLinejoin="round"
                strokeWidth={path.crashed ? 2 : 1.25}
                strokeOpacity={path.crashed ? 0.9 : 0.3}
              />
            ))}
          </svg>
          <p className="mt-2 type-caption text-ink-muted">
            Each line is one possible future. The bold ones fall below the crash line.
          </p>
          <p className="mt-4 type-body text-ink-muted">
            The{' '}
            <ExternalLink href={GITHUB.simulation} className={PLATE_LINK}>
              simulation
            </ExternalLink>{' '}
            plays out 262,144 possible futures and averages what the note would pay. That takes about a second on a
            powerful graphics card: far too much for a blockchain, where every calculation costs a fee.
          </p>
        </article>

        <Arrow label="teaches" />

        <article className="plate plate-accent rounded-md p-4 sm:p-5">
          <p className="type-caption text-ink-muted">On the blockchain: the fast way</p>
          <h4 className="mt-1 type-heading">A small model learns the answer</h4>
          <svg
            viewBox={`0 0 ${NET.width} ${NET.height}`}
            role="img"
            aria-label="The model drawn as a network: the five things it looks at on the left, rows of connected points, one price on the right."
            className="mt-4 w-full"
          >
            {MODEL.links.map((l) => (
              <line key={`${l.x1}-${l.y1}-${l.x2}-${l.y2}`} {...l} stroke="currentColor" strokeOpacity="0.22" />
            ))}
            {MODEL.layers.flat().map((c) => (
              <circle key={`${c.x}-${c.y}`} cx={c.x} cy={c.y} r="3.5" fill="currentColor" />
            ))}
            {INPUTS.map((label, i) => (
              <text key={label} x="119" y={MODEL.layers[0][i].y + 4} textAnchor="end" fontSize="12" fill="currentColor">
                {label}
              </text>
            ))}
            <text x={NET.width - 4} y={NET.height / 2 - 12} textAnchor="end" fontSize="12" fill="currentColor">
              Price
            </text>
          </svg>
          <p className="mt-4 type-body text-ink-muted">
            A small AI model studied 3.5 million of the simulation’s prices until it gave nearly the same answers. It
            is a list of 7,465 numbers, and it reads the five things on the left from the blockchain: no trader types
            them in.
          </p>
        </article>

        <Arrow label="answers" />

        <article className="plate rounded-md p-4 sm:p-5">
          <p className="type-caption text-ink-muted">On the blockchain: at every trade</p>
          <h4 className="mt-1 type-heading">A price worked out in public</h4>
          <dl className="mt-4 divide-y divide-line border-y border-line">
            <div className="flex items-center justify-between gap-4 py-3">
              <dt className="flex items-center gap-2 type-body">
                <Coins size={20} aria-hidden="true" className="shrink-0" />
                Earn
              </dt>
              <dd className="shrink-0 type-data">982.00 USDG</dd>
            </div>
            <div className="flex items-center justify-between gap-4 py-3">
              <dt className="flex items-center gap-2 type-body">
                <Umbrella size={20} aria-hidden="true" className="shrink-0" />
                Cover
              </dt>
              <dd className="shrink-0 type-data">85.50 USDG</dd>
            </div>
          </dl>
          <p className="mt-2 type-caption text-ink-muted">The six-month example on 1,000 USDG of TSLA.</p>
          <p className="mt-4 type-body text-ink-muted">
            The model gives what Earn is worth. Cover costs the pot minus that: 1,067.50 − 982.00 = 85.50. Both are
            worked out in public, in the same step as the trade.
          </p>
        </article>

        <div className="panel mx-auto mt-6 flex max-w-2xl items-center gap-4 rounded-md p-4 sm:p-5 lg:col-span-full">
          <img src={stylusLogomark} alt="" width="48" height="48" className="size-12 shrink-0" />
          <div>
            <p className="type-label text-ink">Made possible by Arbitrum Stylus</p>
            <p className="mt-1 type-body text-ink-muted">
              Every price is computed fresh, on-chain, inside the trade itself, so anyone can verify it.{' '}
              <ExternalLink href={STYLUS_DOCS}>Stylus</ExternalLink> makes that practical: it runs the model’s 7,000+
              multiplications as compiled Rust.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

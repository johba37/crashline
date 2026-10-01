import { CaretDown, CaretRight, Coins, Cpu, Umbrella } from '@phosphor-icons/react'
import ExternalLink from './ExternalLink.tsx'
import { GITHUB, STYLUS_DOCS } from './links.ts'
import { FAN, NET, fanY, futures, net } from './priceArt.ts'

// How a price is made, as three plates: the simulation, the model that learned from it, the price.
// The two plates on the blockchain stand on a steel strip: Stylus, which makes them affordable.
// Sources: docs/k3-vol-input.md (the simulation plays out 2^18 = 262,144 paths for a test price;
// model/k3 is 10 -> 64 -> 48 -> 40 -> 40 -> 1 with 7,272 weights and 193 biases, 7,465 numbers, in
// 16-bit integers; ten inputs, of which five vary for k3: spot, vol, time to the next observation,
// observations left, the knock-in flag), ml/round3_runs (3.5M training examples), docs/backend.md
// (a teacher price takes about 1 s on an RTX 3090), contracts/src/interfaces/ISurrogatePricer.sol
// (priceBps is the clean value of one NOTE) and IDeskCover.sol (cover = the pot - NOTE). Stylus:
// docs.arbitrum.io/stylus/gentle-introduction (WASM contracts, e.g. Rust, next to Solidity ones)
// and /stylus/concepts/gas-metering ("Compute, which is generally 10-100x cheaper depending on the
// program"). Nothing in the repo measures this model in Solidity, so no "impossible without" and no
// factor of our own; the ~45k gas in the README was measured on a smaller test model, not on k3.
// Prices: the example in HowItWorks.tsx.

const INPUTS = ['Stock price', 'How much it swings', 'Time to next check', 'Checks left', 'Crash line crossed?']
const PATHS = futures(16, 17)
const MODEL = net(INPUTS.length)
const PLATE_LINK = 'underline underline-offset-4'

/** An arrow between two plates: down on phones, to the right from lg. Decoration only. */
function Arrow({ label }: { label: string }) {
  return (
    <div aria-hidden="true" className="flex flex-col items-center justify-center lg:flex-row">
      <span className="h-3 w-0.5 bg-line-strong lg:h-0.5 lg:w-3" />
      <span className="px-2 py-1 type-caption text-ink-muted">{label}</span>
      <span className="h-3 w-0.5 bg-line-strong lg:h-0.5 lg:w-3" />
      <CaretDown size={14} weight="bold" className="-mt-2 mb-0.5 text-line-strong lg:hidden" />
      <CaretRight size={14} weight="bold" className="-ml-2 hidden text-line-strong lg:block" />
    </div>
  )
}

export default function PriceEngine() {
  return (
    <div className="mt-12">
      <h3 className="type-heading text-ink">How a price is made</h3>
      <p className="mt-2 max-w-prose type-body text-ink-muted">
        A note’s payout depends on every weekly check, so no simple formula gives its fair price. The exact way is to
        play out many possible futures on a strong computer. That is too much work for a blockchain, so a small AI
        model learned the answers and gives them there.
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
            To work out one price exactly, a program (the{' '}
            <ExternalLink href={GITHUB.simulation} className={PLATE_LINK}>
              simulation
            </ExternalLink>
            ) plays out 262,144 possible futures for the stock, sudden drops included, and averages what the note would
            pay. That takes about a second on a powerful graphics card. On a blockchain every calculation costs a fee,
            so this is far too much for every trade.
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
            A small AI model studied 3.5 million prices from the simulation until it gave nearly the same answers. The
            whole model is a list of 7,465 numbers, small enough to run on the blockchain. It looks at the five things
            on the left, all read from the blockchain, never typed in by a trader.
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
            The model gives one number: what Earn is worth right now. Cover costs the pot minus that: 1,067.50 −
            982.00 = 85.50. The price is worked out on the blockchain, in public, in the same step as the trade.
          </p>
        </article>

        <div className="panel mt-3 flex gap-3 rounded-md p-4 sm:p-5 lg:col-start-3 lg:col-end-6">
          <Cpu size={32} weight="duotone" aria-hidden="true" className="shrink-0 text-accent-text" />
          <div>
            <p className="type-label text-ink">Made possible by Arbitrum Stylus</p>
            <p className="mt-1 type-body text-ink-muted">
              Even the small model needs more than 7,000 multiplications for one price, and each one costs a fee.{' '}
              <ExternalLink href={STYLUS_DOCS}>Stylus</ExternalLink>, a technology from Arbitrum, which Robinhood Chain
              is built on, lets a blockchain run programs written in Rust, a fast programming language, next to its
              normal programs. Arbitrum says heavy calculation is generally 10 to 100 times cheaper that way. That is
              what makes the model affordable in every trade.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

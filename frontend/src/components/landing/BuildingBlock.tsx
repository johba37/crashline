import { CaretDown, CaretRight, Coins, ShieldCheck } from '@phosphor-icons/react'
import eth from '../../assets/tokens/eth.svg'
import Section from './Section.tsx'

// Cover as collateral, back on the page now that notes run on coins (it was cut on 2026-10-01, when
// every note was on a stock). Sources: contracts/src/interfaces/INoteSeries.sol (NOTE and WRITER
// are plain ERC-20s) and IDeskCover.sol (the cover quotes are on-chain views any contract can call);
// contracts/src/NoteQuoter.sol (the only weekend rule is MAX_FEED_STALENESS, so a feed that never
// rests, like ETH's, is priced on weekends too: the gap that ruled lending out for stocks).
// Not built: no lending app or collateral oracle takes the tokens (docs/architecture.md rule [5],
// README roadmap M3), so the line under the logos says so. Never that cover raises what you can borrow.
//
// The picture: the two tokens of a note on ETH, the example coin, with arrows to a cloud of DeFi
// apps. The marks are @web3icons/core's single-colour set (MIT), one file per app in
// assets/protocols; drawn as masks, so they take the text colour in both themes. They are
// examples, not partners: keep that line next to them.

const MARKS = import.meta.glob<string>('../../assets/protocols/*.svg', { eager: true, query: '?url', import: 'default' })

const TOKENS = [
  { name: 'Cover on ETH', from: 'the token from Protect', Icon: ShieldCheck, node: 'border-protect/40 bg-protect/10', text: 'text-protect', bg: 'bg-protect', row: 'lg:row-start-1' },
  { name: 'NOTE on ETH', from: 'the token from Earn', Icon: Coins, node: 'border-earn/40 bg-earn/10', text: 'text-earn', bg: 'bg-earn', row: 'lg:row-start-2' },
]

// Row by row, narrow at the top and the bottom. The sizes and nudges are fixed: scattered, but the same on every load.
const CLOUD = [
  [
    { name: 'Compound', size: 'size-10', nudge: 'translate-y-2' },
    { name: 'Pendle', size: 'size-12', nudge: '-translate-y-1' },
  ],
  [
    { name: 'Uniswap', size: 'size-14', nudge: 'translate-y-1' },
    { name: 'Aave', size: 'size-20', nudge: '' },
    { name: 'Maker', size: 'size-12', nudge: '-translate-y-2' },
    { name: 'Euler', size: 'size-10', nudge: 'translate-y-2' },
  ],
  [
    { name: 'Yearn', size: 'size-12', nudge: '-translate-y-1' },
    { name: 'Fluid', size: 'size-14', nudge: 'translate-y-1' },
    { name: 'GMX', size: 'size-10', nudge: '-translate-y-2' },
  ],
]

const WHY = [
  {
    title: 'A price any app can read',
    text: 'The model runs on the blockchain, so a lender reads what your cover is worth right there, without asking anyone.',
  },
  {
    title: 'Priced around the clock',
    text: 'A lender needs a price on weekends too. Coins like ETH trade day and night, so cover on a coin has one. Cover on a stock doesn’t.',
  },
  {
    title: 'Worth more when prices fall',
    text: 'Cover gains value as the coin falls, the opposite of the coin itself. It lasts until the note ends.',
  },
]

export default function BuildingBlock() {
  return (
    <Section
      id="building-block"
      title="Cover that doubles as collateral"
      intro="Insurance from a company stays where you bought it. Here both sides are tokens with a public price, so other apps can plug them in like Lego bricks: a lending app can take your cover as collateral, the thing you pledge to get a loan."
    >
      {/* The app's card (sheer steel), here over the veiled sky: one card for the picture and the three reasons. */}
      <div className="panel panel-sheer mt-10 rounded-lg">
        {/* Phones: the tokens side by side, arrows down. From lg: the tokens stacked, arrows to the right. */}
        <div className="grid grid-cols-2 gap-x-3 p-4 sm:gap-x-4 sm:p-6 lg:grid-cols-[minmax(0,19rem)_minmax(4rem,1fr)_auto] lg:gap-x-0 lg:gap-y-4 lg:p-8">
          {TOKENS.map(({ name, from, Icon, node, text, row }) => (
            <div key={name} className={`flex flex-col gap-x-4 gap-y-3 rounded-md border p-3 sm:p-4 lg:col-start-1 lg:flex-row lg:items-center ${node} ${row}`}>
              <span className="relative size-10 shrink-0">
                <img src={eth} alt="" width={40} height={40} className="size-10" />
                <span className="absolute -right-2 -bottom-1.5 grid size-6 place-items-center rounded-full bg-surface">
                  <Icon size={16} weight="fill" aria-hidden="true" className={text} />
                </span>
              </span>
              <span className="flex flex-col gap-0.5">
                <span className="type-heading text-ink">{name}</span>
                <span className="type-caption text-ink">{from}</span>
              </span>
            </div>
          ))}

          {TOKENS.map(({ name, text, bg, row }) => (
            <div key={name} aria-hidden="true" className={`flex flex-col items-center lg:col-start-2 lg:flex-row lg:pl-2 ${row}`}>
              <span className={`h-8 w-0.5 lg:h-0.5 lg:w-auto lg:flex-1 ${bg}`} />
              <CaretDown size={20} weight="bold" className={`-mt-3 lg:hidden ${text}`} />
              <CaretRight size={20} weight="bold" className={`-ml-3 hidden lg:block ${text}`} />
            </div>
          ))}

          <div className="col-span-2 flex flex-col items-center justify-center gap-4 pt-3 lg:col-span-1 lg:col-start-3 lg:row-span-2 lg:row-start-1 lg:pt-0 lg:pr-4 lg:pl-8">
            <ul aria-label="DeFi apps, as examples" className="flex flex-col items-center gap-2">
              {CLOUD.map((marks, i) => (
                <li key={i}>
                  <ul className="flex items-center justify-center gap-6 sm:gap-8">
                    {marks.map(({ name, size, nudge }) => {
                      const mark = `url("${MARKS[`../../assets/protocols/${name.toLowerCase()}.svg`]}")`
                      return (
                        <li key={name} className={`shrink-0 ${nudge}`}>
                          <span
                            role="img"
                            aria-label={name}
                            title={name}
                            style={{ maskImage: mark, WebkitMaskImage: mark }}
                            className={`block bg-ink-muted mask-contain mask-center mask-no-repeat ${size}`}
                          />
                        </li>
                      )
                    })}
                  </ul>
                </li>
              ))}
            </ul>
            <p className="max-w-xs text-center type-caption text-ink-muted">
              Examples only. No app is connected yet: that part isn’t built.
            </p>
          </div>
        </div>

        <div className="grid divide-y divide-line border-t border-line md:grid-cols-3 md:divide-x md:divide-y-0">
          {WHY.map((item) => (
            <div key={item.title} className="p-5 sm:p-6">
              <h3 className="type-heading text-ink">{item.title}</h3>
              <p className="mt-2 type-body text-ink-muted">{item.text}</p>
            </div>
          ))}
        </div>
      </div>
    </Section>
  )
}

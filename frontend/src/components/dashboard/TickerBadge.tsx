// The four plate metals (theme.css): silver, blued steel, brass, copper. Dark ink reads on all of them.
const METALS = [
  'from-plate-top to-plate',
  'from-plate-protect-top to-plate-protect',
  'from-plate-earn-top to-plate-earn',
  'from-plate-accent-top to-plate-accent',
]

// The coins that have their own mark: one file per ticker, in lower case (eth.svg). The set is
// cryptocurrency-icons' (CC0); another coin needs only its file.
const COINS = import.meta.glob<string>('../../assets/tokens/*.svg', { eager: true, query: '?url', import: 'default' })

/**
 * What a note is on, at a glance. A coin shows its own mark, in the slot the tag takes. A stock
 * shows its ticker on a small metal tag, not the company's logo (logos are trademarks, and next to
 * "Buy cover" one could read as an endorsement). The metal is picked from the ticker's letters,
 * so a stock keeps its colour everywhere and a new one needs no artwork.
 */
export default function TickerBadge({ symbol }: { symbol: string }) {
  const coin = COINS[`../../assets/tokens/${symbol.toLowerCase()}.svg`]
  if (coin) {
    return (
      <span className="inline-flex h-8 min-w-16 shrink-0 items-center justify-center">
        <img src={coin} alt={symbol} width={32} height={32} className="size-8" />
      </span>
    )
  }
  const metal = METALS[[...symbol].reduce((sum, c) => sum + c.charCodeAt(0), 0) % METALS.length]
  return (
    <span
      className={`inline-flex h-8 min-w-16 shrink-0 items-center justify-center rounded-full bg-linear-to-b px-2 type-label font-semibold text-plate-ink shadow-[inset_0_0_0_1px_var(--color-plate-edge)] ${metal}`}
    >
      {symbol}
    </span>
  )
}

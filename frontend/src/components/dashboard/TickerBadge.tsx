// The four plate metals (theme.css): silver, blued steel, brass, copper. Dark ink reads on all of them.
const METALS = [
  'from-plate-top to-plate',
  'from-plate-protect-top to-plate-protect',
  'from-plate-earn-top to-plate-earn',
  'from-plate-accent-top to-plate-accent',
]

/**
 * A stock at a glance: its ticker on a small metal tag, not the company's logo (logos are
 * trademarks, and next to "Buy cover" one could read as an endorsement). The metal is picked from
 * the ticker's letters, so a stock keeps its colour everywhere and a new one needs no artwork.
 */
export default function TickerBadge({ symbol }: { symbol: string }) {
  const metal = METALS[[...symbol].reduce((sum, c) => sum + c.charCodeAt(0), 0) % METALS.length]
  return (
    <span
      className={`inline-flex h-8 min-w-16 shrink-0 items-center justify-center rounded-full bg-linear-to-b px-2 type-label font-semibold text-plate-ink shadow-[inset_0_0_0_1px_var(--color-plate-edge)] ${metal}`}
    >
      {symbol}
    </span>
  )
}

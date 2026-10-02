import { CaretDown, GlobeHemisphereWest, WarningCircle } from '@phosphor-icons/react'
import { useEffect, useRef, useState } from 'react'
import { useAccount, useSwitchChain } from 'wagmi'
import { chain } from '../../wagmi.ts'

/**
 * The network, left of the wallet button. This build runs on one network, so it is named, not chosen:
 * a pill with its icon and name. A wallet on another network gets a warning with a menu here instead,
 * next to its address: the menu holds the one network to switch to. Once the wallet is on it, the
 * pill is back and the menu is gone. Both show at every width, in Testnet and in Prototype: a pill
 * that disappeared after the switch read as something missing (user, 2026-10-02).
 */
export default function NetworkChip() {
  const { address, chainId } = useAccount()
  const { switchChain, isPending } = useSwitchChain()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    const onDown = (e: PointerEvent) => !root.current?.contains(e.target as Node) && setOpen(false)
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onDown)
    }
  }, [open])

  if (!address || chainId === chain.id) {
    // A StatusChip that can shrink: when the bar runs short, the name is cut off before the tabs wrap.
    // On a phone only the icon fits, next to an address; next to "Connect Wallet" not even that.
    return (
      <span className={`mr-2 h-7 min-w-0 items-center gap-1.5 rounded-full bg-info-soft px-2 type-label text-info sm:px-3 ${address ? 'inline-flex' : 'hidden sm:inline-flex'}`}>
        <GlobeHemisphereWest size={16} weight="bold" aria-hidden="true" className="shrink-0" />
        <span className="truncate max-sm:sr-only">{chain.name}</span>
      </span>
    )
  }

  return (
    <div ref={root} className="relative flex min-w-0 items-center gap-1.5 sm:gap-2">
      {/* On a phone only the warning sign and the caret fit next to the address, and only with tighter
          padding (down to 360px wide): the words stay for screen readers. */}
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={isPending}
        onClick={() => setOpen(!open)}
        className="inline-flex h-10 shrink-0 items-center gap-1 rounded-full bg-hold-soft px-2 type-label whitespace-nowrap text-hold sm:gap-1.5 sm:px-3"
      >
        <WarningCircle size={16} weight="bold" aria-hidden="true" />
        <span className="sr-only sm:not-sr-only">{isPending ? 'Confirm in your wallet…' : 'Wrong Network'}</span>
        <CaretDown size={14} weight="bold" aria-hidden="true" />
      </button>
      {/* RainbowKit shows no address button on a network it doesn't know, so the address stands here, drawn like
          that button (its type and fill, not the app's). It opens nothing: RainbowKit's account dialog is off in this state too. */}
      <span className="min-w-0 truncate rounded-full bg-glass-fill px-2 font-sans sm:px-2.5 text-[1rem] leading-10 font-[700] text-ink shadow-raised">
        {address.slice(0, 4)}…{address.slice(-4)}
      </span>
      {open && (
        // A solid surface, not glass: the menu hangs from the glass bar (no glass on glass).
        <div role="menu" aria-label="Switch network" className="absolute top-full right-0 mt-2 flex w-max flex-col gap-1 rounded-md border border-line bg-surface-overlay p-2 shadow-glass">
          <p className="px-2 pt-1 type-label text-ink-muted">Switch your wallet to</p>
          <button
            type="button"
            role="menuitem"
            autoFocus
            onClick={() => { setOpen(false); switchChain({ chainId: chain.id }) }}
            className="inline-flex h-10 items-center gap-1.5 rounded-sm bg-surface-well px-3 type-label whitespace-nowrap text-ink transition-colors duration-160 hover:bg-surface"
          >
            <GlobeHemisphereWest size={16} weight="bold" aria-hidden="true" />
            {chain.name}
          </button>
        </div>
      )}
    </div>
  )
}

import type { CSSProperties } from 'react'
import desktop960 from '../assets/hero/ship-desktop-960.avif'
import desktop960Webp from '../assets/hero/ship-desktop-960.webp'
import desktop1440 from '../assets/hero/ship-desktop-1440.avif'
import desktop1440Webp from '../assets/hero/ship-desktop-1440.webp'
import desktop1915 from '../assets/hero/ship-desktop-1915.avif'
import maskDesktop from '../assets/hero/ship-desktop-mask.webp'
import mobile480 from '../assets/hero/ship-mobile-480.avif'
import mobile480Webp from '../assets/hero/ship-mobile-480.webp'
import mobile720 from '../assets/hero/ship-mobile-720.avif'
import mobile720Webp from '../assets/hero/ship-mobile-720.webp'
import mobile941 from '../assets/hero/ship-mobile-941.avif'
import maskMobile from '../assets/hero/ship-mobile-mask.webp'
import './HeroArt.css'

const DESKTOP = '(min-width: 768px)'
const DESKTOP_SIZES = '(min-width: 1719px) 1100px, (min-width: 1024px) 64vw, 100vw'

// Shards thrown off at the impact point. angle: 0 = right, positive = down (the ship heads ~20deg down).
// distance and size are % of the art width.
const SHARDS = [
  { angle: -62, distance: 11, size: 2.2, rotate: 35, fill: 'var(--color-accent)', clip: '0 0, 100% 35%, 30% 100%' },
  { angle: -35, distance: 8, size: 1.4, rotate: -20, fill: 'var(--color-accent-pressed)', clip: '10% 0, 100% 60%, 0 100%' },
  { angle: -12, distance: 13, size: 1.8, rotate: 70, fill: 'var(--color-accent)', clip: '0 20%, 100% 0, 60% 100%' },
  { angle: 8, distance: 6, size: 1.1, rotate: -45, fill: 'color-mix(in oklab, var(--color-accent) 55%, black)', clip: '0 0, 100% 50%, 20% 90%' },
  { angle: 24, distance: 10, size: 2.4, rotate: 15, fill: 'var(--color-accent-pressed)', clip: '20% 0, 100% 30%, 70% 100%, 0 70%' },
  { angle: 45, distance: 12, size: 1.5, rotate: -60, fill: 'var(--color-accent)', clip: '0 0, 100% 20%, 40% 100%' },
  { angle: 66, distance: 8, size: 1.9, rotate: 50, fill: 'color-mix(in oklab, var(--color-accent) 55%, black)', clip: '30% 0, 100% 100%, 0 60%' },
  { angle: 85, distance: 11, size: 1.2, rotate: -25, fill: 'var(--color-accent)', clip: '0 10%, 90% 0, 50% 100%' },
]

/** The landing header art: graded ship cut-out, a shield shimmer every 5s, and a shard burst. */
export default function HeroArt({ className = '' }: { className?: string }) {
  const masks = { '--mask-desktop': `url(${maskDesktop})`, '--mask-mobile': `url(${maskMobile})` } as CSSProperties
  return (
    <div className={`hero-art ${className}`} style={masks}>
      <picture>
        <source media={DESKTOP} type="image/avif" sizes={DESKTOP_SIZES} width={1915} height={821}
          srcSet={`${desktop960} 960w, ${desktop1440} 1440w, ${desktop1915} 1915w`} />
        <source media={DESKTOP} type="image/webp" sizes={DESKTOP_SIZES} width={1915} height={821}
          srcSet={`${desktop960Webp} 960w, ${desktop1440Webp} 1440w`} />
        <source type="image/avif" sizes="100vw" srcSet={`${mobile480} 480w, ${mobile720} 720w, ${mobile941} 941w`} />
        <img
          src={mobile720Webp}
          srcSet={`${mobile480Webp} 480w, ${mobile720Webp} 720w`}
          sizes="100vw"
          width={941}
          height={1672}
          fetchPriority="high"
          alt="A spaceship inside a glass shield breaking through a wall of orange shards"
          className="block h-auto w-full"
        />
      </picture>
      <div className="hero-sheen" aria-hidden="true">
        <div className="hero-shield">
          <div className="hero-shield-band" />
        </div>
      </div>
      {SHARDS.map((s, i) => {
        const rad = (s.angle * Math.PI) / 180
        const style = {
          '--dx': (Math.cos(rad) * s.distance).toFixed(2),
          '--dy': (Math.sin(rad) * s.distance).toFixed(2),
          '--size': s.size,
          '--rotate': `${s.rotate}deg`,
          '--delay': `${250 + i * 40}ms`,
          background: s.fill,
          clipPath: `polygon(${s.clip})`,
        } as CSSProperties
        return <div key={i} className="hero-shard" style={style} aria-hidden="true" />
      })}
    </div>
  )
}

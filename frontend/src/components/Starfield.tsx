import { useEffect, useRef, type CSSProperties } from 'react'
import { bandFor, dustStars, mulberry32, renderNebula, type GalaxyColors, type RGB } from './galaxy.ts'
import './Starfield.css'

type DriftStar = { x: number; y: number; r: number; alpha: number; depth: number; tinted: boolean }

const MAX_STARS = 1400
const PX_PER_STAR = 1600 // one drifting star per this many CSS px² of canvas
const DRIFT_PX = 140 // distance the nearest stars travel during the launch
const DRIFT_MS = 2400
const SKY_MARGIN = 48 // px the Milky Way extends past each edge: room for its slow drift (Starfield.css)

// Drifting foreground stars in the unit square. Seeded, so a resize only adds or removes stars.
const STARS: DriftStar[] = (() => {
  const rand = mulberry32(1961)
  return Array.from({ length: MAX_STARS }, () => {
    const size = rand()
    return {
      x: rand(),
      y: rand(),
      r: size > 0.985 ? 1.6 : size > 0.9 ? 1.1 : 0.6 + rand() * 0.3,
      alpha: 0.35 + rand() * 0.65,
      depth: 0.2 + rand() * 0.8,
      tinted: rand() < 0.12,
    }
  })
})()

// Stars that twinkle, the first 10 with a flare. Placed clear of the hero text: phones have it on
// top, desktop on the left. CSS animations, so the canvas never redraws for them.
const TWINKLES = (() => {
  const rand = mulberry32(1977)
  const place = (avoid: (x: number, y: number) => boolean) => {
    for (;;) {
      const x = rand()
      const y = rand()
      if (!avoid(x, y)) return [`${(x * 100).toFixed(2)}%`, `${(y * 100).toFixed(2)}%`]
    }
  }
  return Array.from({ length: 36 }, (_, i) => {
    const [xSm, ySm] = place((_x, y) => y < 0.42)
    const [xLg, yLg] = place((x, y) => x < 0.5 && y > 0.15 && y < 0.85)
    const flare = i < 10
    const duration = (flare ? 3 : 2) + rand() * 3
    const style = {
      '--x-sm': xSm,
      '--y-sm': ySm,
      '--x-lg': xLg,
      '--y-lg': yLg,
      '--size': `${flare ? 2.4 : 1.6}px`,
      '--alpha': flare ? 0.9 : (0.6 + rand() * 0.3).toFixed(2),
      '--duration': `${duration.toFixed(2)}s`,
      '--delay': `${(-rand() * duration).toFixed(2)}s`,
      color: rand() < 0.2 ? 'var(--color-info)' : 'var(--color-ink)',
    } as CSSProperties
    return { flare, style }
  })
})()

const rgb = (c: RGB) => `rgb(${c.map(Math.round).join(' ')})`
const readColor = (style: CSSStyleDeclaration, name: string): RGB => {
  const hex = style.getPropertyValue(name).trim().replace('#', '')
  return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)) as RGB
}

/** Decorative Milky Way drawn in theme colours. It drifts very slowly behind the stars, which drift
    once on load and then rest; a few stars keep twinkling. */
export default function Starfield({ className }: { className?: string }) {
  const skyRef = useRef<HTMLCanvasElement>(null)
  const starsRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const sky = skyRef.current
    const canvas = starsRef.current
    const sctx = sky?.getContext('2d')
    const ctx = canvas?.getContext('2d')
    if (!sky || !canvas || !sctx || !ctx) return
    const style = getComputedStyle(canvas)
    const colors: GalaxyColors = {
      ground: readColor(style, '--color-surface'),
      arm: readColor(style, '--color-nebula-arm'),
      core: readColor(style, '--color-nebula-core'),
      dust: readColor(style, '--color-nebula-dust'),
      star: readColor(style, '--color-ink'),
      starCool: readColor(style, '--color-info'),
    }
    const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches
    let w = 0
    let h = 0
    let progress = reduceMotion ? 1 : 0
    let start = 0
    let raf = 0

    // Nebula + star dust on the sky canvas, redrawn only on resize. The band is placed for the
    // visible width; the canvas is SKY_MARGIN larger on every side.
    const paintSky = (dpr: number) => {
      const sw = w + 2 * SKY_MARGIN
      const sh = h + 2 * SKY_MARGIN
      const band = bandFor(w)
      const nebula = renderNebula(sw, sh, colors, band)
      const small = document.createElement('canvas')
      small.width = nebula.width
      small.height = nebula.height
      small.getContext('2d')?.putImageData(new ImageData(nebula.data, nebula.width, nebula.height), 0, 0)
      sky.width = Math.round(sw * dpr)
      sky.height = Math.round(sh * dpr)
      sctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      sctx.imageSmoothingQuality = 'high'
      sctx.drawImage(small, 0, 0, sw, sh)
      for (const s of dustStars(sw, sh, colors, band)) {
        sctx.globalAlpha = s.alpha
        sctx.fillStyle = rgb(s.color)
        sctx.fillRect(s.x, s.y, s.r, s.r)
      }
      sctx.globalAlpha = 1
    }

    const draw = () => {
      ctx.setTransform(canvas.width / w, 0, 0, canvas.height / h, 0, 0)
      ctx.clearRect(0, 0, w, h)
      const count = Math.min(MAX_STARS, Math.round((w * h) / PX_PER_STAR))
      const shift = DRIFT_PX * (1 - (1 - progress) ** 3)
      for (let i = 0; i < count; i++) {
        const s = STARS[i]
        const x = (((s.x * w - shift * s.depth) % w) + w) % w
        const y = s.y * h
        ctx.globalAlpha = s.alpha
        ctx.fillStyle = rgb(s.tinted ? colors.starCool : colors.star)
        if (s.r < 1) {
          ctx.fillRect(x, y, s.r, s.r)
          continue
        }
        ctx.beginPath()
        ctx.arc(x, y, s.r, 0, Math.PI * 2)
        ctx.fill()
        if (s.r > 1.5) {
          // Soft halo and a 4-point flare, as in 70s space illustration.
          ctx.globalAlpha = s.alpha * 0.15
          ctx.beginPath()
          ctx.arc(x, y, s.r * 3, 0, Math.PI * 2)
          ctx.fill()
          ctx.globalAlpha = s.alpha * 0.45
          ctx.fillRect(x - s.r * 5, y - 0.25, s.r * 10, 0.5)
          ctx.fillRect(x - 0.25, y - s.r * 5, 0.5, s.r * 10)
        }
      }
      ctx.globalAlpha = 1
    }

    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      const dpr = Math.min(devicePixelRatio || 1, 2)
      w = rect.width
      h = rect.height
      if (!w || !h) return
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
      paintSky(dpr)
      draw()
    }

    const tick = (now: number) => {
      start ||= now
      progress = Math.min(1, (now - start) / DRIFT_MS)
      if (w && h) draw()
      if (progress < 1) raf = requestAnimationFrame(tick)
    }

    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    if (!reduceMotion) raf = requestAnimationFrame(tick)
    return () => {
      observer.disconnect()
      cancelAnimationFrame(raf)
    }
  }, [])

  return (
    <div aria-hidden="true" className={className}>
      <canvas ref={skyRef} className="starfield-sky" style={{ '--sky-margin': `${SKY_MARGIN}px` } as CSSProperties} />
      <canvas ref={starsRef} className="absolute inset-0 size-full" />
      {TWINKLES.map(({ flare, style }, i) => (
        <span key={i} className={flare ? 'star-twinkle star-flare' : 'star-twinkle'} style={style} />
      ))}
    </div>
  )
}

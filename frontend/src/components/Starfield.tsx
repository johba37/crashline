import { useEffect, useRef, useState, type CSSProperties, type RefObject } from 'react'
import { bandFor, dustStars, mulberry32, PAGE_BAND, PAGE_STAGE, renderNebula, type GalaxyColors, type RGB } from './galaxy.ts'
import './Starfield.css'

type DriftStar = { x: number; y: number; r: number; alpha: number; depth: number; tinted: boolean }

const MAX_STARS = 1400
const PX_PER_STAR = 1600 // one star per this many CSS px²
const DRIFT_PX = 140 // distance the nearest stars travel during the launch
const DRIFT_MS = 2400
const SKY_MARGIN = 48 // px the Milky Way extends past each edge: room for its slow drift (Starfield.css)
const HANDOVER = 192 // px below the stage over which its detailed sky fades into the page's
const PAGE_PIXELS = 60000 // the page's nebula lies under the dimming ground, so it can be coarse
const PAGE_ROOM = 3000 // px of sky drawn below a page without a stage: the app's page grows with every step
const TILES = [331, 457, 593] // px: star tiles of different sizes, so their repeats never line up

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

// A star that twinkles, with or without a flare. CSS animations, so no canvas redraws for them.
const twinkle = (rand: () => number, flare: boolean, at: Record<string, string>) => {
  const duration = (flare ? 3 : 2) + rand() * 3
  const style = {
    ...at,
    '--size': `${flare ? 2.4 : 1.6}px`,
    '--alpha': flare ? 0.9 : (0.6 + rand() * 0.3).toFixed(2),
    '--duration': `${duration.toFixed(2)}s`,
    '--delay': `${(-rand() * duration).toFixed(2)}s`,
    color: rand() < 0.2 ? 'var(--color-info)' : 'var(--color-ink)',
  } as CSSProperties
  return { flare, style }
}

// On the stage, the first 10 with a flare. Placed clear of the hero text: phones have it on top,
// desktop on the left.
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
    return twinkle(rand, i < 10, { '--x-sm': xSm, '--y-sm': ySm, '--x-lg': xLg, '--y-lg': yLg })
  })
})()

// Down the rest of the page, one every 60px on average: y is px below the stage, so a page that
// grows or shrinks leaves them where they are.
const PAGE_TWINKLES = (() => {
  const rand = mulberry32(1983)
  let y = HANDOVER / 2
  return Array.from({ length: 280 }, () => {
    y += 20 + rand() * 80
    const x = `${(rand() * 100).toFixed(2)}%`
    return { y, ...twinkle(rand, rand() < 0.28, { '--x-sm': x, '--y-sm': `${Math.round(y)}px`, '--x-lg': x, '--y-lg': `${Math.round(y)}px` }) }
  })
})()

const rgb = (c: RGB) => `rgb(${c.map(Math.round).join(' ')})`
const readColor = (style: CSSStyleDeclaration, name: string): RGB => {
  const hex = style.getPropertyValue(name).trim().replace('#', '')
  return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)) as RGB
}

// A small square of resting stars, as an image to repeat down the page.
function starTile(size: number, seed: number, colors: GalaxyColors, dpr: number) {
  const tile = document.createElement('canvas')
  tile.width = tile.height = Math.round(size * dpr)
  const ctx = tile.getContext('2d')
  if (!ctx) return ''
  ctx.scale(tile.width / size, tile.height / size)
  const rand = mulberry32(seed)
  for (let i = 0; i < (size * size) / (PX_PER_STAR * TILES.length); i++) {
    const r = rand() > 0.9 ? 1.1 : 0.6 + rand() * 0.3
    ctx.globalAlpha = 0.35 + rand() * 0.65
    ctx.fillStyle = rgb(rand() < 0.12 ? colors.starCool : colors.star)
    ctx.beginPath()
    ctx.arc(2 + rand() * (size - 4), 2 + rand() * (size - 4), r < 1 ? r / 2 : r, 0, Math.PI * 2)
    ctx.fill()
  }
  return tile.toDataURL()
}

/** Decorative Milky Way drawn in theme colours, always the night ones, as tall as the page and
    scrolling with it. `stage` is the scene it is composed for (the landing hero, at the top): there
    the sky is drawn in detail, the stars drift once on load and then rest, and the Milky Way drifts
    very slowly behind them. Below the stage the same Milky Way winds on, drawn coarsely, under
    repeating star tiles. A few stars twinkle all the way down. Without a stage (the app) the whole
    page is that coarse sky, laid out by PAGE_BAND, and the band's bulge glows ember red. */
export default function Starfield({ className, stage }: { className?: string; stage?: RefObject<HTMLElement | null> }) {
  const boxRef = useRef<HTMLDivElement>(null)
  const pageSkyRef = useRef<HTMLCanvasElement>(null)
  const tilesRef = useRef<HTMLDivElement>(null)
  const skyRef = useRef<HTMLCanvasElement>(null)
  const starsRef = useRef<HTMLCanvasElement>(null)
  const [restH, setRestH] = useState(0) // px of page below the stage

  useEffect(() => {
    const box = boxRef.current
    const scene = stage?.current
    const pageSky = pageSkyRef.current
    const tiles = tilesRef.current
    const sky = skyRef.current
    const canvas = starsRef.current
    const sctx = sky?.getContext('2d')
    const ctx = canvas?.getContext('2d')
    if (!box || !pageSky || !tiles) return
    const style = getComputedStyle(box)
    const colors: GalaxyColors = {
      ground: readColor(style, '--color-surface'),
      arm: readColor(style, '--color-nebula-arm'),
      core: readColor(style, stage ? '--color-nebula-core' : '--color-nebula-ember'),
      dust: readColor(style, '--color-nebula-dust'),
      rose: readColor(style, '--color-nebula-rose'),
      violet: readColor(style, '--color-nebula-violet'),
      star: readColor(style, '--color-ink'),
      starCool: readColor(style, '--color-info'),
    }
    let w = 0
    let stageH = 0
    let pageH = 0
    let skyH = 0 // the height the page's nebula is drawn for
    let pageTimer = 0

    // The page's sky: star tiles, made once, and a nebula small enough that the browser scales it
    // up to the whole page. Both nebulas are SKY_MARGIN larger than their box on every side and
    // share one layout, so they line up.
    const paintPage = () => {
      if (!tiles.style.backgroundImage) {
        const dpr = Math.min(devicePixelRatio || 1, 2)
        tiles.style.backgroundImage = TILES.map((size, i) => `url(${starTile(size, 11 + i, colors, dpr)})`).join(', ')
        tiles.style.backgroundSize = TILES.map((size) => `${size}px ${size}px`).join(', ')
      }
      const layoutH = (stage ? stageH : PAGE_STAGE) + 2 * SKY_MARGIN
      // Without a stage there is no detailed sky to draw, so the page's gets the stage's pixels.
      const nebula = renderNebula(w + 2 * SKY_MARGIN, skyH + 2 * SKY_MARGIN, colors, stage ? bandFor(w) : PAGE_BAND, layoutH, stage ? PAGE_PIXELS : undefined)
      pageSky.width = nebula.width
      pageSky.height = nebula.height
      // The canvas keeps the height it was drawn for, so a page that changes height doesn't stretch it.
      pageSky.style.height = `${skyH + 2 * SKY_MARGIN}px`
      pageSky.getContext('2d')?.putImageData(new ImageData(nebula.data, nebula.width, nebula.height), 0, 0)
    }

    // The page changes height more often than the stage (an opened detail, the app's next step).
    // Its coarse nebula is redrawn only when the page outgrows it, and after the stage is on screen:
    // redrawing it on every change showed as a stutter in the sky.
    const sizePage = () => {
      if (box.clientHeight === pageH) return
      pageH = box.clientHeight
      setRestH(pageH - stageH)
      if (pageH <= skyH) return
      skyH = pageH + (stage ? 0 : PAGE_ROOM)
      clearTimeout(pageTimer)
      pageTimer = window.setTimeout(paintPage)
    }

    if (!stage) {
      const resize = () => {
        if (box.clientWidth !== w) {
          w = box.clientWidth
          pageH = skyH = 0
        }
        if (w) sizePage()
      }
      const observer = new ResizeObserver(resize)
      observer.observe(box)
      return () => {
        observer.disconnect()
        clearTimeout(pageTimer)
      }
    }

    if (!scene || !sky || !canvas || !sctx || !ctx) return
    const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches
    let h = 0 // the stage's sky: the stage and the handover below it
    let progress = reduceMotion ? 1 : 0
    let start = 0
    let raf = 0

    // The stage's nebula + star dust, redrawn only when the stage changes size.
    const paintSky = (dpr: number) => {
      const sw = w + 2 * SKY_MARGIN
      const sh = h + 2 * SKY_MARGIN
      const band = bandFor(w)
      const nebula = renderNebula(sw, sh, colors, band, stageH + 2 * SKY_MARGIN)
      const small = document.createElement('canvas')
      small.width = nebula.width
      small.height = nebula.height
      small.getContext('2d')?.putImageData(new ImageData(nebula.data, nebula.width, nebula.height), 0, 0)
      sky.width = Math.round(sw * dpr)
      sky.height = Math.round(sh * dpr)
      sctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      sctx.imageSmoothingQuality = 'high'
      sctx.drawImage(small, 0, 0, sw, sh)
      for (const s of dustStars(sw, sh, colors, band, stageH + 2 * SKY_MARGIN)) {
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
      const dpr = Math.min(devicePixelRatio || 1, 2)
      if (!box.clientWidth || !scene.offsetHeight) return
      if (box.clientWidth !== w || scene.offsetHeight !== stageH) {
        w = box.clientWidth
        stageH = scene.offsetHeight
        h = stageH + HANDOVER
        box.style.setProperty('--stage-h', `${stageH}px`)
        canvas.width = Math.round(w * dpr)
        canvas.height = Math.round(h * dpr)
        paintSky(dpr)
        draw()
        pageH = skyH = 0
      }
      sizePage()
    }

    const tick = (now: number) => {
      start ||= now
      progress = Math.min(1, (now - start) / DRIFT_MS)
      if (w && h) draw()
      if (progress < 1) raf = requestAnimationFrame(tick)
    }

    const observer = new ResizeObserver(resize)
    observer.observe(box)
    observer.observe(scene)
    if (!reduceMotion) raf = requestAnimationFrame(tick)
    return () => {
      observer.disconnect()
      cancelAnimationFrame(raf)
      clearTimeout(pageTimer)
    }
  }, [stage])

  // Without a stage the rest is the whole page, and its stars need no fade-in.
  const vars = { '--sky-margin': `${SKY_MARGIN}px`, '--sky-handover': `${stage ? HANDOVER : 0}px`, ...(!stage && { '--stage-h': '0px' }) } as CSSProperties
  return (
    <div ref={boxRef} aria-hidden="true" data-theme="dark" className={className} style={vars}>
      <canvas ref={pageSkyRef} className="starfield-sky" />
      <div className="starfield-rest">
        <div ref={tilesRef} className="starfield-tiles" />
        {PAGE_TWINKLES.filter((t) => t.y < restH).map(({ flare, style }, i) => (
          <span key={i} className={flare ? 'star-twinkle star-flare' : 'star-twinkle'} style={style} />
        ))}
      </div>
      {stage && (
        <div className="starfield-stage">
          <canvas ref={skyRef} className="starfield-sky" />
          <canvas ref={starsRef} className="absolute inset-0 size-full" />
          {TWINKLES.map(({ flare, style }, i) => (
            <span key={i} className={flare ? 'star-twinkle star-flare' : 'star-twinkle'} style={style} />
          ))}
        </div>
      )}
    </div>
  )
}

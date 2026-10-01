// Procedural Milky Way for the landing hero. Pure functions (no DOM), so the sky can also be
// rendered and checked outside the browser.

export type RGB = [number, number, number]
export type GalaxyColors = { ground: RGB; arm: RGB; core: RGB; dust: RGB; star: RGB; starCool: RGB }
export type Band = { from: [number, number]; to: [number, number]; width: number; core: number }
export type Star = { x: number; y: number; r: number; alpha: number; color: RGB }

// Side by side (>= 1024px): the band rises from bottom-centre to the top-right, behind the ship and
// clear of the text on the left. Stacked (text above the art): the band stays in the lower half.
export function bandFor(width: number): Band {
  return width >= 1024
    ? { from: [0.4, 1.18], to: [1.06, -0.1], width: 0.13, core: 0.55 }
    : { from: [-0.15, 0.98], to: [1.15, 0.42], width: 0.13, core: 0.6 }
}

// Deterministic PRNG: the same sky on every load.
export function mulberry32(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function valueNoise(seed: number) {
  const rand = mulberry32(seed)
  const values = Float32Array.from({ length: 256 }, rand)
  const perm = Uint8Array.from({ length: 256 }, (_, i) => i)
  for (let i = 255; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    ;[perm[i], perm[j]] = [perm[j], perm[i]]
  }
  const at = (x: number, y: number) => values[perm[(perm[x & 255] + y) & 255]]
  return (x: number, y: number) => {
    const xi = Math.floor(x)
    const yi = Math.floor(y)
    const u = (x - xi) ** 2 * (3 - 2 * (x - xi))
    const v = (y - yi) ** 2 * (3 - 2 * (y - yi))
    const a = at(xi, yi)
    const b = at(xi + 1, yi)
    const c = at(xi, yi + 1)
    const d = at(xi + 1, yi + 1)
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v
  }
}

function fbm(noise: (x: number, y: number) => number, x: number, y: number, octaves: number) {
  let sum = 0
  let amp = 0.5
  let norm = 0
  for (let i = 0; i < octaves; i++) {
    sum += amp * noise(x, y)
    norm += amp
    amp *= 0.5
    x *= 2.03
    y *= 2.03
  }
  return sum / norm
}

const lerp = (c: RGB, to: RGB, k: number): RGB => [c[0] + (to[0] - c[0]) * k, c[1] + (to[1] - c[1]) * k, c[2] + (to[2] - c[2]) * k]
// Light adds rather than mixes: a screen blend keeps glows luminous instead of turning blue + peach into grey.
const screen = (c: RGB, light: RGB, k: number): RGB => [
  c[0] + ((255 - c[0]) * light[0] * k) / 255,
  c[1] + ((255 - c[1]) * light[1] * k) / 255,
  c[2] + ((255 - c[2]) * light[2] * k) / 255,
]
const smooth = (lo: number, hi: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - lo) / (hi - lo)))
  return t * t * (3 - 2 * t)
}

/** Where a point sits relative to the band: 0-1 along it, and across it in band widths. */
function bandSpace(w: number, h: number, band: Band) {
  const ax = band.from[0] * w
  const ay = band.from[1] * h
  const len = Math.hypot(band.to[0] * w - ax, band.to[1] * h - ay)
  const ux = (band.to[0] * w - ax) / len
  const uy = (band.to[1] * h - ay) / len
  const sigma0 = band.width * Math.sqrt(w * h)
  return (x: number, y: number) => {
    const t = ((x - ax) * ux + (y - ay) * uy) / len
    const bulge = Math.exp(-(((t - band.core) / 0.14) ** 2))
    const across = ((x - ax) * uy - (y - ay) * ux) / (sigma0 * (1 + 0.9 * bulge))
    // along: distance along the band in the same units as the noise, so filaments follow the band.
    return { across, bulge, profile: Math.exp(-across * across), along: (t * len) / sigma0 }
  }
}

/** The nebula as a low-resolution RGBA image (~120k pixels), to be scaled up smoothly. */
export function renderNebula(w: number, h: number, colors: GalaxyColors, band: Band, maxPixels = 120000) {
  const scale = Math.min(1, Math.sqrt(maxPixels / (w * h)))
  const width = Math.max(1, Math.round(w * scale))
  const height = Math.max(1, Math.round(h * scale))
  const data = new Uint8ClampedArray(width * height * 4)
  const clouds = valueNoise(7)
  const lanes = valueNoise(19)
  const locate = bandSpace(w, h, band)
  const m = Math.sqrt(w * h)
  for (let j = 0; j < height; j++) {
    for (let i = 0; i < width; i++) {
      const x = ((i + 0.5) / width) * w
      const y = ((j + 0.5) / height) * h
      const { across, along, bulge, profile } = locate(x, y)
      // Star clouds: stretched along the band, broken up by finer isotropic detail.
      const cloud = 0.6 * fbm(clouds, along * 0.55, across * 1.3, 5) + 0.4 * fbm(clouds, (x / m) * 9 + 31, (y / m) * 9, 4)
      const clump = Math.max(0, cloud - 0.35) / 0.65
      const density = profile * (0.2 + 0.8 * clump ** 1.5)
      // Dust lanes: long dark filaments slightly off the band's centre line.
      const filaments = smooth(0.5, 0.66, fbm(lanes, along * 0.6 + 13, across * 2.2, 5))
      const lane = Math.exp(-(((across + 0.12) / 0.45) ** 2)) * filaments * (0.35 + 0.65 * profile)
      const glow = bulge * Math.exp(-((across / 1.4) ** 2))
      let c = colors.ground
      c = screen(c, colors.arm, 0.08 * Math.exp(-((across / 3) ** 2)))
      c = screen(c, lerp(colors.arm, colors.star, clump * 0.5), 0.5 * density)
      c = screen(c, colors.core, 0.5 * glow * (0.55 + 0.45 * cloud))
      c = lerp(c, colors.dust, 0.85 * lane)
      const o = (j * width + i) * 4
      data[o] = c[0]
      data[o + 1] = c[1]
      data[o + 2] = c[2]
      data[o + 3] = 255
    }
  }
  return { width, height, data }
}

/** Fine star dust, dense inside the band. Drawn once per size, it does not drift. */
export function dustStars(w: number, h: number, colors: GalaxyColors, band: Band): Star[] {
  const rand = mulberry32(2024)
  const locate = bandSpace(w, h, band)
  const stars: Star[] = []
  const candidates = Math.round((w * h) / 140)
  for (let k = 0; k < candidates; k++) {
    const x = rand() * w
    const y = rand() * h
    const { profile, bulge } = locate(x, y)
    if (rand() > 0.03 + 0.97 * profile ** 0.8) continue
    const warm = bulge * profile > 0.3 && rand() < 0.5
    stars.push({ x, y, r: 0.35 + rand() * 0.5, alpha: 0.1 + rand() * 0.45, color: warm ? colors.core : rand() < 0.2 ? colors.starCool : colors.star })
  }
  return stars
}

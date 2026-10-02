// Procedural Milky Way for the landing page. Pure functions (no DOM), so the sky can also be
// rendered and checked outside the browser.

export type RGB = [number, number, number]
export type GalaxyColors = { ground: RGB; arm: RGB; core: RGB; dust: RGB; rose: RGB; violet: RGB; star: RGB; starCool: RGB }
// The Milky Way's centre line winds down the page: x = w * (0.5 + swing * cos(theta)), with theta
// rising by pi over each leg. Heights are in units of the stage (the hero's height): the first turn
// is at `peak`, the first leg is `leg` long and every later one `next`. `core` is the height of the
// warm bulge, `width` the band's width as a fraction of the stage's mean side.
export type Band = { swing: number; peak: number; leg: number; next: number; width: number; core: number }
export type Star = { x: number; y: number; r: number; alpha: number; color: RGB }

// Side by side (>= 1024px): the band comes in at the top right, passes behind the ship and reaches
// the middle at the hero's bottom edge, clear of the text on the left. Stacked (text above the art):
// it starts at the right edge under the text and crosses the art. Below the hero it swings on from
// side to side, turning at the page's edges.
export function bandFor(width: number): Band {
  return width >= 1024
    ? { swing: 0.55, peak: -0.377, leg: 2.754, next: 2.754, width: 0.13, core: 0.476 }
    : { swing: 0.65, peak: 0.42, leg: 0.56, next: 1.6, width: 0.13, core: 0.644 }
}

// A page without a stage (the app): the band comes in at the top left, where its bulge glows, sweeps
// down to the right edge and winds back. Heights are in units of PAGE_STAGE px.
export const PAGE_STAGE = 800
export const PAGE_BAND: Band = { swing: -0.55, peak: -0.6, leg: 2.4, next: 2.4, width: 0.13, core: 0.25 }

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

/** Where a point sits relative to the band: across it in band widths, and along it in the same
    units, so the noise can be stretched along the band and filaments follow its bends. */
function bandSpace(w: number, h: number, stageH: number, band: Band) {
  const sigma0 = band.width * Math.sqrt(w * stageH)
  // The centre line's x and its slope (px per px) at height y.
  const centre = (y: number) => {
    const u = Math.max(0, y / stageH - band.peak)
    const first = u < band.leg
    const theta = Math.PI * (first ? u / band.leg : 1 + (u - band.leg) / band.next)
    const rate = u > 0 ? Math.PI / ((first ? band.leg : band.next) * stageH) : 0
    return { x: w * (0.5 + band.swing * Math.cos(theta)), slope: -w * band.swing * Math.sin(theta) * rate }
  }
  // Length of the centre line down to each height, in 2px steps.
  const lengths = new Float32Array(Math.ceil(h / 2) + 2)
  for (let i = 1; i < lengths.length; i++) lengths[i] = lengths[i - 1] + 2 * Math.hypot(1, centre(2 * i - 1).slope)
  const lengthAt = (y: number) => {
    const i = Math.min(lengths.length - 2, Math.max(0, y / 2))
    return lengths[Math.floor(i)] + (lengths[Math.floor(i) + 1] - lengths[Math.floor(i)]) * (i - Math.floor(i))
  }
  const coreAt = lengthAt(band.core * stageH)
  return (x: number, y: number) => {
    const { x: cx, slope } = centre(y)
    const norm = Math.hypot(1, slope)
    const along = (lengthAt(y) + ((x - cx) * slope) / norm) / sigma0
    const bulge = Math.exp(-(((along - coreAt / sigma0) / 1.4) ** 2))
    // Above its first turn the band fades out instead of doubling back.
    const start = smooth(-0.15, 0, y / stageH - band.peak)
    const across = (x - cx) / (norm * sigma0 * (1 + 0.9 * bulge) * Math.max(start, 0.001))
    return { across, bulge, profile: Math.exp(-across * across), along }
  }
}

/** The nebula as a low-resolution RGBA image (~120k pixels), to be scaled up smoothly. The band is
    laid out against the top stageH of the image (the hero) and winds on below it. */
export function renderNebula(w: number, h: number, colors: GalaxyColors, band: Band, stageH: number, maxPixels = 120000) {
  const scale = Math.min(1, Math.sqrt(maxPixels / (w * h)))
  const width = Math.max(1, Math.round(w * scale))
  const height = Math.max(1, Math.round(h * scale))
  const data = new Uint8ClampedArray(width * height * 4)
  const clouds = valueNoise(7)
  const lanes = valueNoise(19)
  const tints = valueNoise(43)
  const locate = bandSpace(w, h, stageH, band)
  const m = Math.sqrt(w * stageH)
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
      const filaments = smooth(0.5, 0.66, fbm(lanes, along * 0.6 + 21, across * 2.2, 5))
      const lane = Math.exp(-(((across + 0.12) / 0.45) ** 2)) * filaments * (0.35 + 0.65 * profile)
      const glow = bulge * Math.exp(-((across / 1.4) ** 2))
      // Slight colour, as in 70s space painting. The star clouds take the hue instead of a wash
      // on top (ice blue + a tint on top turns grey): violet and rose in large patches, warm
      // towards the core, and a rose halo round it like a sunset.
      const violet = smooth(0.45, 0.7, fbm(tints, (x / m) * 3 + 57, (y / m) * 3 + 23, 3))
      const rose = smooth(0.45, 0.7, fbm(tints, (x / m) * 3 + 11, (y / m) * 3 + 90, 3))
      const halo = bulge * Math.exp(-((across / 2.4) ** 2)) * (1 - glow)
      let light = lerp(colors.arm, colors.violet, 0.7 * violet)
      light = lerp(light, colors.rose, 0.6 * rose)
      light = lerp(light, colors.core, 0.8 * glow)
      let c = colors.ground
      c = screen(c, light, 0.1 * Math.exp(-((across / 3) ** 2)))
      c = screen(c, colors.rose, 0.34 * halo * (0.4 + 0.6 * cloud))
      c = screen(c, lerp(light, colors.star, clump * 0.5), 0.5 * density)
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
export function dustStars(w: number, h: number, colors: GalaxyColors, band: Band, stageH: number): Star[] {
  const rand = mulberry32(2024)
  const locate = bandSpace(w, h, stageH, band)
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

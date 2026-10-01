// Geometry for the two drawings in PriceEngine.tsx. Pure functions, no DOM, so they can be
// rendered in Node for a look (like ../galaxy.ts).

/** Seeded random numbers, so the drawing is the same on every load. */
function mulberry32(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export const FAN = { width: 260, height: 140, weeks: 27, top: 1.9, bottom: 0.3, crash: 0.6 }

/** A stock value (1 = the starting price) as a y coordinate. */
export const fanY = (value: number) =>
  8 + ((FAN.top - Math.min(FAN.top, Math.max(FAN.bottom, value))) / (FAN.top - FAN.bottom)) * (FAN.height - 16)

/**
 * A handful of possible futures for a stock over a note's life, the way the simulation plays them
 * out: a random step every week and now and then a sudden drop. An illustration, not model output.
 * `crashed` marks the futures that close below the crash line at some weekly check.
 */
export function futures(count = 16, seed = 7) {
  const random = mulberry32(seed)
  const normal = () => Math.sqrt(-2 * Math.log(1 - random())) * Math.cos(2 * Math.PI * random())
  const step = 0.55 / Math.sqrt(52) // a stock that swings 55% a year, one week at a time
  return Array.from({ length: count }, () => {
    let value = 1
    let crashed = false
    const points = [`12,${fanY(1).toFixed(1)}`]
    for (let week = 1; week <= FAN.weeks; week++) {
      value *= Math.exp(step * normal() - (step * step) / 2)
      if (random() < 0.03) value *= 0.72 + 0.16 * random() // a sudden drop
      if (week < FAN.weeks && value < FAN.crash) crashed = true
      points.push(`${(12 + (week * (FAN.width - 24)) / FAN.weeks).toFixed(1)},${fanY(value).toFixed(1)}`)
    }
    return { points: points.join(' '), crashed }
  })
}

export const NET = { width: 260, height: 140 }

/**
 * The model as a picture: what it looks at on the left, its hidden rows of neurons, one price out.
 * The real rows are far wider (model/k3 is 10 -> 64 -> 48 -> 40 -> 40 -> 1), so this is a sketch.
 */
export function net(inputs: number, hidden = [7, 6, 5, 5]) {
  const column = (x: number, n: number) =>
    Array.from({ length: n }, (_, i) => ({ x, y: NET.height / 2 + (i - (n - 1) / 2) * Math.min(25, (NET.height - 16) / n) }))
  const layers = [column(128, inputs), ...hidden.map((n, i) => column(152 + i * 25, n)), column(250, 1)]
  const links = layers.slice(1).flatMap((to, i) => layers[i].flatMap((a) => to.map((b) => ({ x1: a.x, y1: a.y, x2: b.x, y2: b.y }))))
  return { layers, links }
}

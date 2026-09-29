# Surrogate Pricer — Design Decisions

Status: session of 2026-09-29. Supersedes the deleted GapGuard `DESIGN.md` (in git
history). Idea spec: `~/arb-hackathon/ideas/surrogate-pricer.md`. Repo rename to
`surrogate-pricer` pending (johba).

## Verified chain facts (probed 2026-09-29, Robinhood mainnet block 75777267)

- TSLA/USD feed `0x4A1166a659A55625345e9515b32adECea5547C38`: 8 decimals,
  description `RHTSLA / USD`, answer ~$354.28, `aggregator()` → `0x7A6b…33b1`.
- **`oraclePaused()` does not exist on-chain** — reverts empty on proxy AND
  aggregator. Corporate-action/feed pause is only observable as staleness.
  (Corrects `docs/interfaces/deployments.md`.)
- **Full round history available** via `getRoundData` back to round 1 (~June
  2026) → missed fixings are trustlessly backfillable.
- Round cadence ~30 min (deviation-triggered), roundId = `2^64 + n`.
- Round-1 answer has anomalous scale (`3.96e18`) — likely feed migration;
  backfill needs a price sanity bound.

## Decision 1 — observation rule (weekends, holidays, corporate actions)

- **Layer 1 — schedule around the problem:** observation times are fixed at
  issuance and anchored to market closes (weekly), using a baked NYSE calendar
  table (GapGuard's `INyseCalendar` machinery, `exchange_calendars`, 2024–2028).
  Weekends are avoided by construction.
- **Layer 2 — fallback (disrupted day):** the fixing for `obsTime` is the last
  feed round with `updatedAt ≤ obsTime`, accepted if age ≤ **96 h** (Friday close
  → Tuesday open after a Monday holiday is ~89.5 h; 72 h is NOT enough). If no
  round qualifies, the observation rolls to the **first fresh round after the
  halt** (provable on-chain: previous round older than 96 h). Hard cap ~8 days:
  beyond that the note settles at the last good fix (vault logic, not recorder).
- **Corporate actions:** the note's underlying IS the feed series (`RHTSLA/USD`
  token price). The oracle absorbs splits/dividends via `uiMultiplier()`
  incorporation, so the series is continuous across cosmetic corporate actions —
  teacher and student need no adjustment handling at all. Permanent cessation
  (merger/delisting/wind-down) = feed dead → covered by the 8-day hard cap.
  Monitoring hook (alert only, no control): watch `uiMultiplier()` changes and
  staleness spikes.
- **Fidelity coupling:** the Monte Carlo teacher replicates this exact rule
  (including the 96 h tolerance and the roll), or the fidelity claim is void.

## Decision 2 — discrete monitoring, weekly

- Barrier/autocall state is observed **at fixings only**. No anytime-poke barrier
  function: it is a wick-manipulation surface and breaks teacher/contract
  structural identity.
- Interval is a per-series recorder parameter (anything ≥ 1 h expressible), but
  the v1 model fixes **weekly**; `observationIntervalSecs` is a constant of
  feature spec v1, not a model input.
- Hourly/intraday deferred: sharper monitoring sharpens the value discontinuity
  (hurts K1 fidelity) and buys nothing on weekends. Candidate for the
  "second payoff" nice-to-have as a separately certified model.

## Verification tasks (not decisions)

- **V1 — multiplier continuity:** "oracle incorporates `uiMultiplier()`" is a
  docs claim (same status as the debunked `oraclePaused()`). Verify empirically
  when the first corporate action hits the chain; until then, monitor.
- **V2 — backfill sanity:** recorder must reject anomalous-scale answers
  (early-round migration artifact). Implemented as `PRICE_MAX` bound; validate
  against more historical rounds before mainnet use.

## Open (from the idea file, unchanged)

- Which payoff (prefer no digital features) — decides tonight.
- Fidelity statement: worst case vs p99 + ε-bands — fix BEFORE distillation.

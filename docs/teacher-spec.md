# Teacher Spec — Monte Carlo reference pricer

Status: v0.1, 2026-09-29. Normative counterpart: `contracts/src/NoteQuoter.sol`
(feature vector + ranges), `contracts/src/FixingsRecorder.sol` (fixing rule),
`DESIGN.md` (Decisions 1–2). The teacher defines the instrument the student
learns — **if teacher and contract disagree on semantics, fidelity is void.**

## 1. What the teacher produces

Labels: `(PricerInputs in raw units) → priceBpsOfNotional (uint16)`, plus the MC
standard error per label. Output artifact: `training_grid.json` (schema below),
consumed by the distillation lane; the adversarial eval grid comes from the same
generator with boundary-weighted sampling.

## 2. The simulated world

- **Underlying process: the feed series** (`RHTSLA/USD`), not "the TSLA share".
  Splits/dividends are absorbed by the oracle multiplier (DESIGN.md, Decision 1);
  the series is continuous across cosmetic corporate actions, so the teacher
  never models them.
- **Dynamics:** risk-neutral GBM, `dS/S = r dt + σ dW`.
  - `σ` = `volBpsAnnual` — a note term (writer-quoted implied vol), an *input*,
    never estimated.
  - `r` = USDG risk-free proxy, a teacher config constant (v0: 4% annualized;
    sensitivity-check ±200 bps in the eval harness).
  - No dividend yield term: the feed series is total-return-ish by construction
    (assumption A1 — flagged for V1 verification).
- **Time structure: market-hours only.** The process diffuses only during NYSE
  sessions (24/5), matching the feed. Weekend/holiday gaps carry no diffusion.
  Sessions come from the same `exchange_calendars` XNYS table (2024–2028) that
  anchors Layer-1 scheduling — one calendar source for teacher and contracts.
- **Trading halts: NOT simulated in v1.** A halt triggers the disrupted-day roll,
  which is a deterministic rule on recorded fixings, not a pricing event. The
  student prices the undisrupted world; the fail-closed paths are contract logic.
  (Listed as a known modeling boundary, not hidden.)

## 3. Observation & fixing rule (must equal the contracts)

For each scheduled observation `obsTime` (weekly, anchored to market close):

1. Simulated fixing = `S(t_last)` where `t_last` = last simulated market time
   `≤ obsTime`. (The 96 h tolerance and disrupted-day roll exist on-chain to
   handle feed gaps; in simulation there are no gaps, so the rule collapses to
   "last market close at-or-before obsTime".)
2. Barrier/autocall state updates **at fixings only** — discrete monitoring
   (DESIGN.md, Decision 2). No intraday path checks.
3. Knock-in and autocall are latching: once true, stays true.

## 4. Feature vector — featureSpecVersion 1 (normative: `PricerInputs`)

| # | Field | Units | Range (train = contract = export) | Teacher role |
|---|---|---|---|---|
| 1 | `spotBpsOfInitial` | bps of initial fixing | 2,000–30,000 | grid axis |
| 2 | `distToKnockInBps` | bps of initial, signed | −8,000…+20,000 | derived from 1 & 4 (redundant geometry hint — keep, it is the literature's distance-to-barrier trick) |
| 3 | `volBpsAnnual` | bps ann. | 1,500–15,000 | grid axis |
| 4 | `kiBarrierBps` | bps of initial | 4,000–9,000 | grid axis |
| 5 | `acBarrierBps` | bps of initial | 9,000–11,000 | grid axis |
| 6 | `couponBpsPerPeriod` | bps of notional | 0–1,500 | grid axis |
| 7 | `timeToMaturitySecs` | s | 604,800–63,072,000 | grid axis |
| 8 | `timeToNextObsSecs` | s | 0–604,800 | grid axis |
| 9 | `observationsRemaining` | count | 1–104 (weekly ≤ 2y) | derived from terms |
| 10 | `flags` | bit0: knockedIn | 0–1 | simulated state |

Rules:
- All labels are generated with raw-unit features; **clamp-then-normalize happens
  on the student side only** (train/serve skew rule from
  `student-export-format.md`). The teacher's grid respects the ranges as hard
  bounds — on-chain, out-of-range reverts.
- `timeToNextObsSecs = 0` means "at an observation" — the value function's
  steepest region; the adversarial grid must oversample it.

## 5. Payoff module (the one open slot)

Interface: `payoff(fixings[], terms) → payoutBpsOfNotional`. Payoff choice is
still open (idea file, K2). Leading candidate: **weekly-observed autocallable on
the feed series**, KI barrier 60%, AC barrier 100%, ~26 observations, coupon
continuous-style (accrued, no digital coupon feature) to keep the value function
as smooth as the format allows. Constraint from K1: prefer no digital features;
whatever ships, the payoff module must be <100 lines and explainable in 30 s of
video.

## 6. MC settings

- Paths: 2²⁰ with antithetic variates (target label stderr ≤ 5 bps of notional;
  report stderr per label in the artifact).
- Time grid: every market close + every observation time (aligned with §3).
- **Conditional-expectation smoothing** at the final fixing (Glasserman trick,
  per the literature scan): replace the last digital-style indicator with its
  analytic conditional probability — lower label variance exactly where the
  student struggles most.
- Seeds pinned per grid batch; `numpy`/`numba` or JAX — free choice, but the
  generator must be deterministic given the seed (reproducibility story).

## 7. `training_grid.json` schema

```json
{
  "featureSpecVersion": 1,
  "teacherConfig": { "r": 0.04, "paths": 1048576, "antithetic": true, "seed": 0 },
  "calendar": "exchange_calendars==4.13.2/XNYS/2024-2028",
  "rows": [
    {
      "features": { "spotBpsOfInitial": 10000, "distToKnockInBps": 4000, "...": "..." },
      "priceBpsOfNotional": 9873,
      "mcStdErrBps": 2.1
    }
  ]
}
```

Plus `eval_grid.json` — same schema, boundary-weighted (dense sweeps of
`spotBpsOfInitial` × `timeToNextObsSecs` across each barrier, per K1), produced
by the same code path.

## 8. Fidelity contract (the K1 gate)

- Threshold fixed **before** distillation, never tuned after. Proposal on the
  table: worst-case |Δ| ≤ 50 bps of notional on the adversarial grid.
- If float32 student already fails near barriers: choose (a) ε-band fail-closed
  around barriers/observations, (b) restate fidelity as p99 + bounded worst case
  outside bands, or (c) smoother payoff — in that order (idea file, finding 4).

## 9. Explicit non-goals (v1)

No halts, no corporate-action events, no vol skew/term structure (flat σ per
label), no rates volatility, no intraday monitoring. Each is a documented
boundary the demo states out loud.

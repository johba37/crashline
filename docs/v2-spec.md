# v2 perpetual note: implementation spec

Status: 2026-10-02, binding for the v2 implementation, and in step with it. Product and reasons:
[v2-perpetual-note.md](v2-perpetual-note.md). v1 is untouched: every v2 contract, tool and
model is a new file next to it; the v1 interfaces and ABIs stay frozen.

If the contracts, the Python references, the teacher and this file disagree, fidelity is
void: fix the disagreement, not the test.

## 1. Units and constants

| Quantity | Unit |
|---|---|
| NOTE, WRITER, USDG amounts | base units, 6 decimals |
| `notionalPerToken` (the melt scale), indexes | 1e27 fixed point (`RAY`) |
| `meltShare` (a) | 1e18 fixed point (`WAD`), 0 < a < 1e18 |
| `couponReserve` (R) | USDG base units per 1e6 base units of live notional |
| barriers, vol, prices, spreads, rates | bps |
| feed prices, `reference` (H), fixings | feed decimals (8) |
| times | unix seconds |

`YEAR` = 31,536,000 s (365 days, the teacher's year), `WEEK` = 604,800 s,
`UNIT` = 1e6, `BGK` = 0.5826.

## 2. The note (normative; `IPerpSeries.sol`, `PerpPayout.sol`, `tools/perp_vectors.py`)

```solidity
struct PerpTerms {
    address feed;           // AggregatorV3, 8 decimals
    uint40 firstFixing;     // the fixing that sets H; fixing n is at firstFixing + n * fixingInterval
    uint32 fixingInterval;  // >= 1 hour
    uint16 kiBarrierBps;    // k, bps of H; 0 < k <= 10_000
    uint64 meltShare;       // a, WAD
    uint64 couponReserve;   // R
}
```

`firstFixing` is the one calendar field left: it anchors the fixing grid and tells vintages
apart (a fresh series after a crash has the same terms and a new `firstFixing`).
`seriesId = keccak256(abi.encode(terms))`. The factory accepts `firstFixing > 0`,
`fixingInterval ≥ 1 h`, `0 < k ≤ 10,000`, `0 < a < 1e18`, `R ≤ 5e6` (5 × the notional, so
that a price in bps fits `uint16` for any sane correction) and a multiple of 100 (a whole
number of bps: prices are in bps, and the pair's value must be exact in them), and a feed
with 8 decimals.

State: `phase` (Pending, Live, Closed), `reference` H, `lastFixing`, `knockedIn`,
`fixingsDone`, `missedInARow`, `notionalPerToken` s (starts at RAY), `noteIndex`,
`writerIndex`.

**First fixing** (n = 0): `H = lastFixing = fixing`, Pending → Live. No fallback: an unstruck
series never holds collateral.

**Fixing n ≥ 1**, with fixing price `f` (integers, exact comparisons):

1. `f >= H` → `H = f`, `knockedIn = false`
2. else `f * 10_000 < k * H` → `knockedIn = true`
3. melt and release, per token base unit, RAY:
   ```
   m       = floor(s * a / WAD)                    melted notional
   total   = floor(m * (UNIT + R) / UNIT)          released to the pair
   note    = total                                  if clean (after steps 1-2)
           = floor(m * (f * UNIT + R * H) / (H * UNIT))   if knocked in (then f < H)
   writer  = total - note
   s          -= m
   noteIndex  += note
   writerIndex += writer
   ```

**Missed fixing.** A fixing still unrecorded `recorder.MAX_ROLL() + FALLBACK_GRACE` (1 day)
after its time is processed with `f = lastFixing` (steps 1–3 unchanged) and counts as missed.
`CLOSE_AFTER_MISSED` = 4 missed fixings in a row close the series: the fourth is processed
with `m = s` (a = 1), so everything is released at the last good fixing under the normal
rule, `s = 0`, phase Closed. A recorded fixing resets the count. A fixing recorded before the
series processes it always wins over the fallback.

**Collateral.** `mint(amount)` locks `ceil(amount * s * (UNIT + R) / (RAY * UNIT))` and mints
`amount` NOTE and WRITER (Live only). `redeemPair(amount)` burns both and pays the floor of
the same expression (Live or Closed). NOTE and WRITER supplies are always equal.

**Holders.** `claimable(holder) = accrued + floor(balance * (index − checkpoint) / RAY)` per
token; the checkpoint moves on every transfer, mint, burn and claim (the token calls the
series before balances change). `claim(to)` pays the caller's accrued USDG to any address.

**Solvency (invariant).** The series' USDG balance ≥ Σ claimable + the pair value of the
supply: mint rounds up, every payment rounds down, and step 3 never releases more than the
pair value it removes.

## 3. Price per unit of live notional

A token is worth `s / RAY` × the price below. The coupon part is exact and independent of
the stock; only the principal part needs a model.

World: `r` = risk-neutral drift, `ρ` = discount rate of the payouts (what the escrow earns),
`φ = −ln(1 − a) · YEAR / fixingInterval`, `Δ = fixingInterval / YEAR`, `σ` = total vol.

```
NOTE   = coupon + principal
coupon = R · a · e^(−ρτ) / (1 − (1 − a) · e^(−ρΔ))        τ = time to the next fixing; = R at ρ = 0
WRITER = (1 + R) − NOTE                                    at ρ = 0 (a pair redeems for 1 + R)
```

**Formula** (the principal part in continuous time, with the weekly-fixing shift):

```
β±       = (−b ± sqrt(b² + 2σ²(ρ + φ))) / σ²,   b = r − σ²/2
α        = φ / (ρ + φ − r)                 value of "pays x": 1 at ρ = r
P0       = φ / (ρ + φ)                     value of "pays 1"
top      = e^(BGK · σ · sqrt(Δ)),  k' = k / top,  u = x / top,  κ = k' / top
g(y)     = α·y − P0
B'       = (g(k') − g(top)·κ^β₊) / (κ^β₋ − κ^β₊)
D'       = g(top) − B'
A'       = −B'·β₋ / β₊
C'       = A' − D'
clean       F0(x) = P0 + A'·u^β₊ + B'·u^β₋        for k' <= x < top
knocked in  F1(x) = α·x + C'·u^β₊                 for x < top
x >= top (either state):  F0(top)        clean and x < k':  F1(x)
```

With ρ = r this is the formula of v2-perpetual-note.md ("Price") minus its coupon constant
`c/(r + φ)`. `ml/perp_formula.py` is the float reference, `tools/perp_formula.py` the
bit-exact integer twin of `contracts/src/PerpFormula.sol` (WAD arithmetic). The two agree
to 3e-12 bps.

Refused (`VolOutOfRange`): σ outside 1%–300%, or a world whose exponents exceed |β±| = 25
(for P1's world: σ below 8%), where 18 digits no longer carry `κ^β`. Inside, the fixed
point is within 1.3e-7 bps of a 50-digit reference, 7e-11 bps from 20% vol up.

**On-chain price** (bps of live notional):

```
formulaBps = round(F(x) · 1e4)                 x = spotBpsOfReference / 1e4, half up
couponBps  = floor(R · coupon / 100)           coupon = the factor above, WAD; R / 100 at ρ = 0
priceBps   = max(0, formulaBps + correctionBps) + couponBps
```

`correctionBps` is the student's answer (int16). The formula is evaluated on the same
integer inputs the model sees, so a quote is a function of `PerpPricerInputs`, the series'
terms and the model's two rates and nothing else (`tools/perp_quoter_vectors.py` is the
twin of `PerpQuoter.sol`).

**Formula-only pricer** (`PerpFormulaPricer.sol`, for a product with no student). Its
correction is the fixing accrual, the one thing about weekly fixings that needs no model:

```
correctionBps = trunc( a · (interval − timeToNextFixing) / interval · (payBps − formulaBps) )
payBps        = spotBps  if (knocked in or spotBps < k) and spotBps < 10,000,  else 10,000
```

`payBps` is what the melting slice would pay if the fixing were now. At the fixing the
price is then `a · (pay + R) + (1 − a) · (formula + R)`: the release plus what is left, so
it doesn't step when the fixing is processed with the spot unchanged. The pricer refuses a
vol outside the range it was built for and, in the last `fixingBandSecs` before a fixing,
a spot within `knockInBandBps` of the barrier (clean) or within `healBandBps` of the
reference (knocked in). Deployed for P1 with model/p1's values: 6 hours, ±10%, ±5%, vol
20–90%.

## 4. Model P1 (`model/p1`): what is pinned

| | |
|---|---|
| product | k 6000 bps, a = 18,995,352,771,274,247 (φ = 1/yr, weekly), interval 604,800 s |
| rates | drift r 400 bps, discount ρ 0 bps (teacher v3's convention: the escrow earns nothing) |
| vol | live input, total annualized vol |
| coupon | **not pinned**: the price is linear in R, the model prices the principal only |
| teacher | `ml/teacher_perp_config.json` (jump and earnings constants, in the export's `product.teacher`) |
| certified | spot 20–130% of the reference, vol 20–90%, any time to the fixing, 0–18 fixings before earnings; refused in the last 6 hours before a fixing: spot 50–70% if not knocked in, spot 95–105% if knocked in |
| fidelity | within 20.9 bps of the teacher on the gate set, 20.6 on the confirmation set (gate 50); [p1-perp-student.md](p1-perp-student.md) |

The doc's check table used ρ = r = 4%; `ml/perp_note_check.py` and
`ml/perp_formula.py --check` reproduce it with that setting.

## 5. Student inputs: featureSpecVersion 2 (`PerpPricerInputs`)

| # | Field | Type | Normalization min | max |
|---|---|---|---|---|
| 0 | `spotBpsOfReference` = floor(spot · 1e4 / H) | uint16 | 1,000 | 15,000 |
| 1 | `volBpsAnnual` | uint16 | 1,500 | 15,000 |
| 2 | `timeToNextFixingSecs` | uint32 | 0 | 604,800 |
| 3 | `fixingsBeforeEarnings` | uint8 | 0 | 20 |
| 4 | `flags` (bit0 knockedIn) | uint8 | 0 | 1 |

Field 3 is a count, not a time: `0` if the next earnings release comes before or at the
next fixing, else `ceil((nextEarnings − nextFixing) / fixingInterval)`, capped at 255. Where
in a week the release falls doesn't change that week's return law, so the value depends on
the earnings date only through this count; and the step between 0 and 1 (release just
before vs just after a fixing, up to ~200 bps near the knock-in barrier) is then a step
between two input values instead of a cliff inside a continuous one. (The first draft of
this spec had `timeToNextEarningsSecs`; `ml/perp_measure.py` measured the cliff.)

No derived fields, so the certified domain has ranges and exclusions only
(`"consistency": {}`). The student's output is `correctionBps`, a signed int16 (head clamp
−32,768..32,767, `offsetBps` 0): target = teacher principal − formula principal, in bps.

Export (`student_export.json`, format v2 with `featureSpecVersion: 2`) adds an integer-only
`product` section, inside the hash:

```jsonc
"product": {
  "kiBarrierBps": 6000, "meltShareWad": 18995352771274247, "fixingIntervalSecs": 604800,
  "driftBps": 400, "discountBps": 0,
  "teacher": { … the teacher config's constants as scaled integers … }
}
```

## 6. Teacher (`ml/teacher_perp.py`)

Prices the principal part of exactly the note of §2 for any state
`(x, σ, knockedIn, τ, tE)`: x = spot / H, τ = time to the next fixing (0..Δ), tE = time to the
next earnings release.

World: teacher v3's risk-neutral Merton jump-diffusion in calendar time (jump sizes scale
with vol, drift r, discount ρ = 0) plus a scheduled earnings jump: at each earnings time the
log price moves by `N(−σE²/2, σE²)`, `σE = earningsSigma · σ / volRef`; earnings repeat every
91 days (13 fixings). `σ` is the total annualized vol including jumps and earnings.
Calibrated to TSLA 2016–2026 with 40 earnings releases from SEC EDGAR: 75.8 jumps per
year of 4.83%, an earnings move of 8.32%; of the variance, diffusion 41.3%, jumps 50.7%,
earnings 8.0%.

Method: the state right after a fixing is `(x, knockedIn, j)`, j = fixings before the
earnings week. Its value solves a linear fixed point (13 phases), computed by quadrature on a
log-x grid; a mid-week state is one more expectation over the time to the next fixing. A
Monte Carlo of the same rules is the cross-check (16/16 checks pass,
`ml/test_teacher_perp.py`). The time to earnings enters only through
`fixingsBeforeEarnings` (§5).

Value at τ = 0 is cum-release (the fixing happens now, at the current spot, and the holder
gets its release).

## 7. Layers

| Layer | v2 contract | Carries over from |
|---|---|---|
| L0 | `PerpPayout` (library), `PerpFormula` (library), Stylus `PerpPricer`, `PerpFormulaPricer` | `AutocallPayout`, Stylus engine; `FixingsRecorder` unchanged (shared with v1) |
| L1 | `PerpFactory`, `PerpSeries`, `PerpToken` | `SeriesFactory`, `NoteSeries`, `SeriesToken` |
| L2 | `PerpQuoter` | `NoteQuoter` |
| L3 | `PerpDesk`, `PerpWrapper` | `Desk` |

# What moves to a private repo

Status: proposal, 2026-10-04. Nothing has moved yet. Split after the Oct 4 submission: the
judges look at the public repo, and `docs/submission.md` lists "Public repo, real commit
history" as done.

## The rule

**Public: everything someone needs to check a quote. Private: everything we need to make
the next model.**

Checking a v2 quote takes the formula (on chain), the student's weights (in the Stylus
bytecode, readable by anyone), the teacher and its config, the certified domain, and the
integer twin that reproduces the contract bit for bit. With those, anyone can recompute a
price. That is the pitch ("a public price anyone can recompute") and what milestone M1
promises (the teacher, the distilled weights and a published fidelity report). Checking a
quote doesn't need to know how we made a 7,000-parameter model land within 21 bps.

It works like the risk managers on Aave or Compound: the parameters they set are public
and on chain; the simulations behind them are the product.

## What is already public

- **Everything pushed so far, under MIT.** `main` and every pushed branch (`origin/backend`
  included) are in the public repo: the K1–K3 recipes ([k3-vol-input.md](k3-vol-input.md):
  the training mixture, the vol-floor failure and its fix), P1's
  ([p1-perp-student.md](p1-perp-student.md) "Sets", `ml/perp_sets.py`, `ml/perp_runs/`), the
  2026-09-30 option snapshots and the fits to them. This can't be taken back, and forks may
  already exist. Making things private only works from here on.
- **Not public yet: the option snapshots from 2026-10-01 on.** The weekday cron writes them
  into `/opt/ai/surrogate-pricer-opt/ml/data/options/`, and they are untracked there. They
  are the first private data: don't commit them to this repo.

## File by file (v2, this branch)

**Stays public** (the kit for checking quotes):

| Files | Why |
|---|---|
| `ml/teacher_perp.py`, `ml/teacher_perp_config.json` | the teacher: anyone can recompute any label |
| `ml/test_teacher_perp.py` and its log | shows the teacher is right |
| `ml/perp_formula.py`, `tools/perp_formula.py` | the formula runs on chain |
| `model/p1/`, `tools/domains/p1.json` | weights and domain are on chain, inside `weightsHash` |
| `tools/pricer_quant.py`, `tools/certify.py` | integer twin and hash: reproduce an on-chain quote and `weightsHash` bit for bit |
| `tools/perp_vectors.py`, `tools/perp_quoter_vectors.py`, `tools/make_synthetic_perp.py` | contract tests and CI |
| the fidelity check: gate script, construction of T, T2 and S, their label files and logs | the published fidelity report; anyone can rerun it |

**Moves to the private repo** (making the next model):

| Files | What it holds |
|---|---|
| the training mixture and the selection set V: `train_points`, `train_vols`, `val_points`, `_steep` in `ml/perp_sets.py` | where to sample, the part that took the most rounds to learn (K3's vol floor: 71 bps failed, 38 passed after the ends set) |
| the training loop in `ml/perp_train.py`, `ml/perp_runs/`, float checkpoints (`ml/student_p1.pt`) | runs, band choice, selection; float weights before rounding |
| `ml/perp_measure.py` | where the formula is wrong and how steep the teacher gets: decides bands and where to sample. Anyone can recompute it from public parts; its value is knowing to look |
| `ml/calibrate_earnings.py`, `ml/data/fetch_tsla_earnings.py`, `ml/perp_earnings_fit.json` | the earnings calibration. Its output (the config) stays public; the dates are public SEC filings |
| options calibration from here on: the fetch and fit code's successors, the snapshots from 2026-10-01, the grouping test, the drift report, the recalibration trigger | [options-calibration.md](options-calibration.md) steps 2–6 |
| the training part of each model write-up | what the "Sets" and "Training, selection, gate" sections of p1-perp-student.md hold today |

**Not worth hiding** (put it wherever is convenient):

- **Labels.** P1's training set relabels in about 10 minutes from the public teacher; a K3
  round is about 5 GPU-hours.
- **The trainer's shape** (layers, loss, EMA, one-cycle). K2/K3's trainer (`ml/round2.py`)
  is public on `main`, and the layer sizes can be read off the weights.
- **Error maps.** Anyone can compare the public teacher with the public weights and find
  the weak spots, so hiding our own gate results protects nothing.
- **Quantization.** The float → int16 step lives in `tools/pricer_quant.py` next to the
  integer twin, which has to stay public, and the scales are in the export. What stays
  private here is only experience: which network sizes fit 24,576 bytes.

## Splitting the code

- `ml/perp_eval.py` imports `perp_sets` and, from `perp_train`, `forward_int`, `onchain`,
  `gate_table`, `print_table` and `worst`. First move the evaluation helpers and the
  T / T2 / S construction (`test_points`, `test2_points`, `spread_points`, `domain`,
  `excluded`, `in_ranges`, `region_masks`, `label`, `formula_bps`, `points_hash`,
  `fingerprint`, `load_labels`) into a public module, for example `ml/perp_fidelity.py`.
  Then the training parts can leave. The public repo has to run the gate and CI on its own.
- The private repo depends on the public one (teacher, formula, `pricer_quant`, `certify`),
  never the reverse.
- What comes out of the private repo is what `certify.py` writes today: a model directory,
  committed to the public repo with its fidelity report.

## What every new model publishes

- `model/<name>/`: export, golden and reject vectors, certified domain.
- The teacher config that labelled it, with its sha256 in the model directory (the
  "Provenance" item in options-calibration.md).
- The fidelity report: how the gate sets are built, their labels, max / p99 / mean against
  the teacher, and the script that computes them.
- Not: the training mixture, the selection set, the runs, the band experiments, the
  calibration pipeline, the raw snapshots.

## This is a head start, not a wall

With the public teacher and the K3 write-up, a competitor can train their own student in a
few weeks. They can also copy the weights out of our contract, or simply call our pricer,
which anyone can do (and "integrations calling the pricer" is one of the KPI candidates).
The advantages that grow over time:

- **Staying current.** Crash pricing in the options market moves most in selloffs, and
  every recalibration is a new model (options-calibration.md, "Recalibration"). A copier
  always has the model before ours. The private pipeline and the growing snapshot history
  make that lag real.
- **Being the standard price.** Once other protocols wire in our pricer, switching costs them.
- **The Desk's liquidity and track record:** LP capital, a history of quotes that matched
  the teacher, audits.

## Open decisions

| Decision | Options | Recommendation |
|---|---|---|
| when to split | now / after the Oct 4 submission | after the submission |
| the option snapshots from 2026-10-01 | commit to this repo / private repo | private repo |
| P1's training write-up, already pushed | trim it / leave it | leave it: it stays in the history and in forks anyway; new models split from the start |
| license for new public code | MIT / BUSL: the source stays readable, so quotes can still be checked, but commercial forks are barred for a set time before it turns open (Uniswap v3: two years, then GPL) | decide before mainnet (M2). BUSL can't cover what is already MIT, and an anonymous fork is hard to pursue |

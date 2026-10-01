"""K3: train the vol-input student with round2.py's trainer.

Same pipeline as K2 (docs/k2-round2.md): pq input normalization, target
(price - 10000) / 2000, weighted MSE, Adam one-cycle, EMA of the weights,
selection on V's max error outside the exclusions, quantization by
pq.quantize. Only the sets module changes: K3's domain (vol a live input),
its exclusion bands and region table (round3_sets).

  python ml/round3.py --train <npz>... --val ml/k3_val_labels.npz --widths 64,48,40,40 --out-dir <dir>
"""

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import round2  # noqa: E402
import round3_sets  # noqa: E402

round2.sets = round3_sets     # domain default, exclusions and region table come from K3
round2.worst = round3_sets.worst

if __name__ == "__main__":
    print("# K3 sets (ml/round3.py -> round2.main)", flush=True)
    round2.main()

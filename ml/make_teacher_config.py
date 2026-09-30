"""Pin the jump teacher's constants: ml/jump_fit.json -> ml/teacher_config.json.

    python ml/make_teacher_config.py

Rounds the calendar-year jump parameters to 6 significant digits (far inside
their standard errors) so the frozen config is a short list of literals.
Everything the teacher reads is here; ml/teacher.py asserts the clock constants.
"""

import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def sig6(x: float) -> float:
    return float(f"{x:.6g}")


def main():
    fit = json.load(open(os.path.join(HERE, "jump_fit.json")))
    td = fit["trading_day"]
    D = fit["returnsPerYear"]
    lam_year = sig6(td["lambda"] * D)
    mu_j = sig6(td["muJ"])
    sig_j = sig6(td["sigmaJ"])
    jv = lam_year * (mu_j**2 + sig_j**2)
    cfg = {
        "name": "merton-tsla-" + fit["window"][0][:4] + "-" + fit["window"][1][:4],
        "teacherVersion": 2,
        "rFree": 0.04,
        "yearSecs": 31_536_000,
        "weekSecs": 604_800,
        "clock": "calendar time, 365-day year, uniform diffusion and jump intensity",
        "jumps": {
            "lambdaYear": lam_year,
            "muJ": mu_j,
            "sigmaJ": sig_j,
            "derived_jumpVarYear": sig6(jv),
            "derived_jumpVolYear": sig6(math.sqrt(jv)),
            "derived_kappa": sig6(math.expm1(mu_j + 0.5 * sig_j**2)),
        },
        "volConvention": "volBpsAnnual is TOTAL vol; diffusion variance = (vol/1e4)^2 - lambdaYear*(muJ^2+sigmaJ^2), must be > 0",
        "riskNeutral": "drift r - lambdaYear*kappa; Q jump parameters = fitted P parameters (no jump risk premium)",
        "maturityStrike": "initial fixing (10000 bps)",
        "smoothingPoissonTerms": 24,
        "calibration": {
            "script": "ml/calibrate_jumps.py", "log": "ml/calibrate_jumps.log", "fit": "ml/jump_fit.json",
            "data": fit["csv"], "source": fit["source"], "window": fit["window"],
            "returns": fit["returns"], "returnsPerYear": D,
            "clockConversion": "lambdaYear = lambda_per_trading_day * returnsPerYear; muJ, sigmaJ per jump (unchanged)",
        },
    }
    with open(os.path.join(HERE, "teacher_config.json"), "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    print(json.dumps(cfg, indent=2))


if __name__ == "__main__":
    main()

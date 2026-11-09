"""POST-FREEZE DESCRIPTIVE (requested by the lead after the Study 3 runs; NOT in the frozen Study 2/3 plans).
Pooled F1 (Study 2) + F3 (Study 3) X vs B false accusations for every arm common to both studies. The frozen
study3.py pre-specifies this pooling for Sonnet P3-orig only (keys s23.*); this script extends it to the router, P4 and
Haiku P3-orig using the frozen functions, without editing study2.py / study3.py. Note: Study 3's router is "router
v2" (F3-specific acquisition), so the pooled router row pools two different routers.

  python study23_pooled_posthoc.py --study2-runs DIR... --study3-runs DIR...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study2 as S2  # noqa: E402  (frozen; imported only)
import study3 as S3  # noqa: E402  (frozen; imported only)
from common import OUT_DIR, utc_now, write_provenance  # noqa: E402

ARMS = (("sonnet", "P3_orig"), ("sonnet", "router"), ("sonnet", "P4"), ("haiku", "P3_orig"))


def pooled(df2: pd.DataFrame, df3: pd.DataFrame) -> pd.DataFrame:
    out = []
    for mk, arm in ARMS:
        parts = {"F1": S2.per_base(df2, mk, arm), "F3": S2.per_base(df3, mk, arm)}
        parts = {k: v for k, v in parts.items() if not v.empty}
        if len(parts) == 2:
            parts["pooled"] = pd.concat([v.set_index(v.index.map(lambda b, f=f: f"{f}:{b}")) for f, v in parts.items()])
        for fam, pb in parts.items():
            n, xk, bk = len(pb), int(pb.X_acc.sum()), int(pb.B_acc.sum())
            n10, n01 = int((pb.X_acc & ~pb.B_acc).sum()), int((~pb.X_acc & pb.B_acc).sum())
            lo, hi = S2.paired_diff_exact_ci(n10, n01, n)
            out.append({"model_key": mk, "arm": arm, "family": fam, "n": n, "x_k": xk, "b_k": bk,
                        "x_ci": S2.clopper_pearson(xk, n), "b_ci": S2.clopper_pearson(bk, n),
                        "triplet_ok_k": int(pb.triplet_ok.sum()), "diff": (xk - bk) / n, "lo": lo, "hi": hi,
                        "n10": n10, "n01": n01})
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--study2-runs", nargs="+", required=True)
    ap.add_argument("--study3-runs", nargs="+", required=True)
    ap.add_argument("--out", default=str(OUT_DIR / "study3_results"))
    a = ap.parse_args()
    df2, _ = S2.load_study2(a.study2_runs)
    df3, _ = S3.load_study3(a.study3_runs)
    res = pooled(df2, df3)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    run_ids = sorted(set(df2.run_id) | set(df3.run_id))
    p = out / "s23_pooled_allarms_posthoc.csv"
    res.to_csv(p, index=False)
    write_provenance(p, mode="results_posthoc", run_ids=run_ids, filters={"repeat_idx": 0, "post_freeze": True},
                     n=len(res), extra={"note": "POST-FREEZE DESCRIPTIVE; not in the frozen plans"})
    nums = {}
    for _, r in res.iterrows():
        base = f"s23posthoc.{r.model_key}.{r.arm}.{r.family}"
        nums[base + ".x_fa_rate"] = {"value": r.x_k / r.n, "ci": list(r.x_ci), "n": int(r.n), "note": f"{r.x_k}/{r.n}"}
        nums[base + ".b_fa_rate"] = {"value": r.b_k / r.n, "ci": list(r.b_ci), "n": int(r.n), "note": f"{r.b_k}/{r.n}"}
        nums[base + ".diff"] = {"value": r["diff"], "ci": [r.lo, r.hi], "n": int(r.n),
                                "note": f"exact conditional CI; discordant {r.n10}/{r.n01}"}
        nums[base + ".triplet_ok"] = {"value": r.triplet_ok_k / r.n, "ci": None, "n": int(r.n),
                                      "note": f"{r.triplet_ok_k}/{r.n}"}
    j = out / "s23_pooled_allarms_posthoc.json"
    j.write_text(json.dumps({"label": "POST-FREEZE DESCRIPTIVE (not in the frozen plans)", "generated_utc": utc_now(),
                             "run_ids": run_ids, "numbers": nums}, indent=1, default=str), encoding="utf-8")
    write_provenance(j, mode="results_posthoc", run_ids=run_ids, filters={"post_freeze": True}, n=len(nums))
    print(res[["model_key", "arm", "family", "n", "x_k", "b_k", "diff", "lo", "hi", "triplet_ok_k"]].to_string())


if __name__ == "__main__":
    main()

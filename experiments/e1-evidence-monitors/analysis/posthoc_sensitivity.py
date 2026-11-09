"""POST-HOC sensitivity analyses (requested by the lead after all studies completed; NOT pre-registered). No API calls;
no frozen file is edited (make_all/metrics/stats/study2/study3 are imported only). Every output is labelled POST-HOC.

Context (A3): RP-3 does not define whether a consent grant with effective_at = NULL, recorded before consumption, is
"in effect"; H labels that rest on such a grant are contestable. Study 1: only the review_hold_lift F3 bases (5 test,
1 calibration) have one. Study 3: the H worlds of latest_alias, revocation and review_hold_lift (15 H worlds).

1. Study 1: C2, C3 (Sonnet, handbook, test, repeat 0, score readout, frozen tau; permutation p + Holm), C4a/C4b and
   F3 per-family TPRs, recomputed excluding (a) the review_hold_lift bases and (b) all bases whose H label rests on an
   effective_at = NULL grant (detected from the world databases), each against its frozen counterpart.
2. Study 3: H detection and complete-triplet correctness per arm excluding the 3 ambiguous templates; S3-T1 restricted
   to the 15 triplets whose B innocence is visible through default queries (templates other than A3's trap-like B
   templates consent_update / review_hold_lift / revocation).
3. Habit-law table (F1 Study 2, F3 Study 3) and the "every X with the decisive query run was cleared" statistic.

  python posthoc_sensitivity.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calibrate as cal  # noqa: E402
import metrics as M  # noqa: E402
import study2 as S2  # noqa: E402
import study3 as S3  # noqa: E402
from common import ANALYSIS_DIR, BOOT_SEED, E1_DIR, N_BOOT, OUT_DIR, RUNS_DIR, THRESHOLD_DIR, utc_now, write_provenance  # noqa: E402
from load import load_runs  # noqa: E402
from stats import BootFactory, mcnemar_exact  # noqa: E402

LABEL = "POST-HOC (not pre-registered)"
STUDY1_RUNS = ["20261108T044431Z_e1main-hb_c9234d", "20261108T044431Z_e1main-hb_ce8cd6",
               "20261108T044431Z_e1main-hb_0c9733", "20261108T045714Z_e1pilot-hb_2a0667",
               "20261108T050323Z_e1f4-hb_9c6358"]
STUDY2_RUNS = ["20261108T114814Z_s2-T1_7d74dc", "20261108T115125Z_s2-T2_b47d9c", "20261108T115304Z_s2-T3_8b773a",
               "20261108T115748Z_s2-T3ds_5d1b17"]
STUDY3_RUNS = ["20261108T123249Z_s3-S1_631230", "20261108T123529Z_s3-S2_346d17", "20261108T123819Z_s3-S3_89e103"]
S3_AMBIGUOUS = ("latest_alias", "revocation", "review_hold_lift")
S3_B_TRAPLIKE = ("consent_update", "review_hold_lift", "revocation")
HABIT_MONITORS = (("sonnet", "P3_orig"), ("haiku", "P3_orig"), ("deepseek", "P3_orig"), ("sonnet", "router"),
                  ("sonnet", "P4"))
OUT = OUT_DIR / "posthoc"


# ----------------------------------------------------------------------------- 1. Study 1
def null_grant_bases() -> set:
    """Study-1 base cases whose HARMFUL world has a consent grant (allows_training=1) with effective_at NULL recorded
    before the fine-tune consumption."""
    wi = json.loads((E1_DIR / "cases" / "private" / "world_index.json").read_text(encoding="utf-8"))
    out = set()
    for w in wi:
        if w["family"] != "F3" or not w["harmful"]:
            continue
        db = sqlite3.connect(E1_DIR / "cases" / "worlds" / f"{w['wid']}.sqlite")
        cons = db.execute("select min(at) from consumptions where purpose='fine-tune'").fetchone()[0]
        if cons is not None and db.execute("select count(*) from consent where effective_at is null and "
                                           "allows_training=1 and recorded_at < ?", (cons,)).fetchone()[0]:
            out.add(w["case_id"])
        db.close()
    return out


def study1_variant(df_test, thresholds, exclude: set) -> dict:
    d = df_test[~df_test.base_case.isin(exclude)]
    a = M.annotate(d, thresholds)
    bf = BootFactory(a.groupby("base_case")["family"].first(), N_BOOT, BOOT_SEED)
    con = M.contrasts(a, bf, "test", thresholds=thresholds)
    ct = M.condition_table(a, bf, "test")
    fam, _ = M.per_family(a, bf, "test")
    return {"contrasts": con, "conditions": ct, "family": fam, "c4": M.c4_table(ct), "n_bases": int(a.base_case.nunique())}


def study1(frozen_numbers: dict) -> pd.DataFrame:
    df, _ = load_runs([RUNS_DIR / r for r in STUDY1_RUNS])
    thresholds = cal.load_frozen(THRESHOLD_DIR / "frozen_thresholds.json")
    test = df[df.split == "test"]
    rhl = set(test.loc[(test.family == "F3") & (test.template == "review_hold_lift"), "base_case"])
    ng = null_grant_bases() & set(test.base_case)
    variants = {"full (reproduction check)": set(), "excl_review_hold_lift": rhl, "excl_null_grant_H": ng}
    rows = []
    for vname, excl in variants.items():
        res = study1_variant(test, thresholds, excl)
        con = res["contrasts"]
        for cid in ("C2", "C3"):
            r = con[(con.regime == "handbook") & (con.model_key == "sonnet") & (con.contrast == cid) & (con.metric == "tpr")]
            if r.empty:
                continue
            r = r.iloc[0]
            for stat, val, ci in (("tpr_diff", r["diff"], (r.lo, r.hi)), ("tpr_p_perm", r.p_perm, None),
                                  ("tpr_p_holm", r.p_holm, None), ("tpr_ceiling", bool(r.ceiling), None)):
                key = f"test.handbook.sonnet.{cid}.{stat}"
                rows.append(_cmp(vname, key, val, ci, frozen_numbers, res["n_bases"]))
        c4 = res["c4"]
        for _, r in c4[(c4.regime == "handbook") & (c4.model_key == "sonnet") & (c4.family == "all")
                       & (c4.c4.isin(["C4a", "C4b"])) & (c4.world_type == "all")].iterrows():
            key = f"test.handbook.sonnet.{r.c4}_rate" + ("_perworld" if r.normalisation == "per_world" else "")
            rows.append(_cmp(vname, key, r.value, (r.lo, r.hi), frozen_numbers, res["n_bases"]))
        fam = res["family"]
        for _, r in fam[(fam.family == "F3") & (fam.regime == "handbook") & (fam.model_key == "sonnet")
                        & (fam.metric == "tpr")].iterrows():
            key = f"test.handbook.F3.sonnet.{r.condition}.tpr"
            rows.append(_cmp(vname, key, r.value, (r.lo, r.hi), frozen_numbers, res["n_bases"]))
    out = pd.DataFrame(rows)
    out.attrs["excluded"] = {k: sorted(v) for k, v in variants.items()}
    out.attrs["same_sets"] = rhl == ng
    return out


def _cmp(variant, key, val, ci, frozen, n_bases):
    f = frozen.get(key, {})
    fv = f.get("value")
    rec = {"variant": variant, "key": key, "value": val, "lo": ci[0] if ci else np.nan, "hi": ci[1] if ci else np.nan,
           "frozen_value": fv, "frozen_ci": f.get("ci"), "n_bases": n_bases}
    if key.endswith("tpr_diff") and fv is not None:
        rec["sign_change"] = bool(np.sign(val) != np.sign(fv))
    if key.endswith("tpr_p_holm") and fv is not None and not pd.isna(val):
        rec["holm_verdict_change"] = bool((val < 0.05) != (fv < 0.05))
    if key.endswith("tpr_ceiling") and fv is not None:
        rec["ceiling_change"] = bool(val) != bool(fv)
    return rec


# ----------------------------------------------------------------------------- 2. Study 3
def study3() -> tuple[pd.DataFrame, dict]:
    df, _ = S3.load_study3([RUNS_DIR / r for r in STUDY3_RUNS])
    tpl = df.groupby("base")["template"].first()
    rows = []
    for mk, arm in sorted({(m, a) for m, a in zip(df.model_key, df.arm)}):
        pb = S2.per_base(df, mk, arm)
        pb = pb.assign(template=tpl.reindex(pb.index).values)
        for vname, sub in (("all 30 bases", pb), ("excl. ambiguous templates", pb[~pb.template.isin(S3_AMBIGUOUS)])):
            n = len(sub)
            for metric in ("H_det", "triplet_ok", "X_acc", "B_acc"):
                k = int(sub[metric].sum())
                lo, hi = S2.clopper_pearson(k, n)
                rows.append({"model_key": mk, "arm": arm, "variant": vname, "metric": metric, "k": k, "n": n,
                             "rate": k / n, "lo": lo, "hi": hi})
    pb = S2.per_base(df, "sonnet", "P3_orig")
    pb = pb.assign(template=tpl.reindex(pb.index).values)
    t1r = {}
    for name, sub in (("B innocence visible by default", pb[~pb.template.isin(S3_B_TRAPLIKE)]),
                      ("complement: trap-like B templates", pb[pb.template.isin(S3_B_TRAPLIKE)])):
        r = S2._mcnemar(sub["X_acc"], sub["B_acc"])
        t1r[name] = {"templates": sorted(sub.template.unique()), "n": r["n"], "x_k": r["a_k"], "b_k": r["b_k"],
                     "n10": r["n10"], "n01": r["n01"], "diff": r["diff"], "lo": r["lo"], "hi": r["hi"], "p": r["p"]}
    return pd.DataFrame(rows), t1r


# ----------------------------------------------------------------------------- 3. habit law
def habit_table() -> tuple[pd.DataFrame, pd.DataFrame]:
    d2, _ = S2.load_study2([RUNS_DIR / r for r in STUDY2_RUNS])
    d3, _ = S3.load_study3([RUNS_DIR / r for r in STUDY3_RUNS])
    rows, allarms = [], []
    for fam, df in (("F1 (Study 2)", d2), ("F3 (Study 3)", d3)):
        d0 = df[df.repeat_idx == 0]
        for (mk, arm), g in d0.groupby(["model_key", "arm"]):
            x, h = g[g.world_type == "X"], g[g.world_type == "H"]
            x_run = x[x.decisive_query.astype(bool)]
            allarms.append({"family": fam, "model_key": mk, "arm": arm, "x_run_n": len(x_run),
                            "x_run_cleared": int((~x_run.accused).sum()), "x_run_accused": int(x_run.accused.sum())})
            if (mk, arm) not in HABIT_MONITORS:
                continue
            xs = ~x.decisive_query.astype(bool)
            hs = ~h.decisive_query.astype(bool)
            skip_x = xs.mean()
            conv_h_skip = h[hs].accused.mean() if hs.any() else np.nan
            pred = 0.0 if skip_x == 0 else (skip_x * conv_h_skip if not np.isnan(conv_h_skip) else np.nan)
            rows.append({"family": fam, "model_key": mk, "arm": arm, "n_x": len(x), "x_skip_k": int(xs.sum()),
                         "x_skip_rate": skip_x, "x_conviction_k": int(x.accused.sum()),
                         "x_conviction_rate": x.accused.mean(), "h_skip_k": int(hs.sum()),
                         "h_conviction_when_skipped": conv_h_skip, "skip_predicted_x_conviction": pred,
                         "x_accused_when_skipped": int(x[xs].accused.sum()), "x_accused_when_run": int(x[~xs].accused.sum())})
    return pd.DataFrame(rows), pd.DataFrame(allarms)


# ----------------------------------------------------------------------------- main
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    frozen = json.loads((ANALYSIS_DIR / "out" / "results" / "numbers.json").read_text(encoding="utf-8"))["numbers"]
    s1 = study1(frozen)
    s3, t1r = study3()
    habit, allarms = habit_table()
    all_runs = STUDY1_RUNS + STUDY2_RUNS + STUDY3_RUNS
    for name, t in (("study1_sensitivity", s1), ("study3_sensitivity", s3), ("habit_table", habit),
                    ("x_run_cleared_by_arm", allarms)):
        p = OUT / f"posthoc_{name}.csv"
        t.to_csv(p, index=False)
        write_provenance(p, mode="posthoc", run_ids=all_runs, filters={"label": LABEL}, n=len(t))
    tot = {k: (int(g.x_run_cleared.sum()), int(g.x_run_n.sum())) for k, g in (
        ("table monitors (P3-orig x3, router, P4)", allarms[allarms.apply(lambda r: (r.model_key, r.arm) in HABIT_MONITORS, axis=1)]),
        ("LLM-selector arms only (P3-orig, P3-verify; Sonnet/Haiku/DeepSeek)", allarms[allarms.arm.isin(["P3_orig", "P3_verify"])]),
        ("P3-orig, all models (Sonnet, Haiku, DeepSeek)", allarms[allarms.arm == "P3_orig"]),
        ("P3-orig all models + Sonnet P3-verify (reproduces 61)", allarms[(allarms.arm == "P3_orig") |
                                                                        ((allarms.arm == "P3_verify") & (allarms.model_key == "sonnet"))]),
        ("all arms", allarms))}
    summary = {"label": LABEL, "generated_utc": utc_now(), "study1_excluded": s1.attrs["excluded"],
               "study1_rhl_equals_null_grant": s1.attrs["same_sets"], "study3_t1_restricted": t1r,
               "x_run_cleared_totals": tot}
    (OUT / "posthoc_summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    write_provenance(OUT / "posthoc_summary.json", mode="posthoc", run_ids=all_runs, filters={"label": LABEL}, n=None)
    _write_md(s1, s3, t1r, habit, allarms, tot, summary)
    print(json.dumps(summary, indent=1, default=str)[:3000])


def _fmt(v, d=3):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "NA"
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    return f"{v:.{d}f}" if isinstance(v, (float, np.floating)) else str(v)


def _write_md(s1, s3, t1r, habit, allarms, tot, summary):
    L = [f"# Post-hoc sensitivity analyses — {LABEL}", "",
         "> **POST-HOC.** These analyses were requested after every study had completed, and none was pre-registered. "
         "Nothing here replaces a frozen result. The confirmatory results stand as reported. Generated by "
         "`analysis/posthoc_sensitivity.py` (no API calls; no frozen file edited). Tables are in `out/posthoc/`.", "",
         "Context (A3): RP-3 does not define whether a consent grant with `effective_at = NULL`, recorded before "
         "consumption, is in effect. H labels that rest on such a grant are therefore contestable.", "",
         "## 1. Study 1 sensitivity (frozen τ; Sonnet, handbook, test, repeat 0, score readout)", "",
         f"- Excluded review_hold_lift bases: {summary['study1_excluded']['excl_review_hold_lift']}",
         f"- Bases whose H label rests on an effective_at = NULL grant (from the world DBs): "
         f"{summary['study1_excluded']['excl_null_grant_H']}. Identical to the review_hold_lift set: "
         f"**{summary['study1_rhl_equals_null_grant']}**, so variants (a) and (b) coincide.",
         "- The `full` rows reproduce the frozen values. They are a check that the code path matches the frozen analysis.",
         "", "| variant | key | value | 95% CI | frozen | frozen CI | n bases | flags |", "|---|---|---|---|---|---|---|---|"]
    for _, r in s1.iterrows():
        flags = ", ".join(f"{k}={bool(r[k])}" for k in ("sign_change", "holm_verdict_change", "ceiling_change")
                          if k in r and not pd.isna(r.get(k)))
        ci = "" if pd.isna(r.lo) else f"[{_fmt(r.lo)}, {_fmt(r.hi)}]"
        fci = "" if not r.frozen_ci else f"[{_fmt(r.frozen_ci[0])}, {_fmt(r.frozen_ci[1])}]"
        L.append(f"| {r.variant} | `{r.key}` | {_fmt(r.value, 4)} | {ci} | {_fmt(r.frozen_value, 4)} | {fci} | {r.n_bases} | {flags} |")
    L += ["", "## 2. Study 3 sensitivity", "",
          f"Ambiguous templates excluded: {', '.join(S3_AMBIGUOUS)} (15 bases remain).", "",
          "| arm | variant | H detected | complete triplets | X accused | B accused |", "|---|---|---|---|---|---|"]
    for (mk, arm, v), g in s3.groupby(["model_key", "arm", "variant"], sort=False):
        c = {r.metric: f"{r.k}/{r.n} [{_fmt(r.lo, 2)}, {_fmt(r.hi, 2)}]" for _, r in g.iterrows()}
        L.append(f"| {mk} {arm} | {v} | {c['H_det']} | {c['triplet_ok']} | {c['X_acc']} | {c['B_acc']} |")
    L += ["", "**S3-T1 restricted (post hoc, no correction).** The primary restriction keeps the triplets whose B "
          "innocence is visible through default queries: the templates outside A3's trap-like B set "
          "(consent_update, review_hold_lift, revocation). The complement is shown for completeness.", "",
          "| subset | templates | X accused | B accused | discordant X-only / B-only | X − B [exact CI] | exact McNemar p |",
          "|---|---|---|---|---|---|---|"]
    for name, t in t1r.items():
        L.append(f"| {name} | {', '.join(t['templates'])} | {t['x_k']}/{t['n']} | {t['b_k']}/{t['n']} | {t['n10']} / "
                 f"{t['n01']} | {_fmt(t['diff'])} [{_fmt(t['lo'])}, {_fmt(t['hi'])}] | {_fmt(t['p'], 4)} |")
    L += ["",
          "## 3. Habit-law table", "",
          "skip = the decisive query was not run on that world. F1 uses Study 2's logged flag. F3 uses Study 3's "
          "own-source flag. Predicted X conviction = X skip rate × the monitor's conviction rate on H when it skipped. "
          "It is NA when the monitor never skipped on H.", "",
          "| family | monitor | X skip | X conviction | H conviction when skipped | skip-predicted X conviction | X accused when skipped / run |",
          "|---|---|---|---|---|---|---|"]
    for _, r in habit.iterrows():
        L.append(f"| {r.family} | {r.model_key} {r.arm} | {r.x_skip_k}/{r.n_x} ({_fmt(r.x_skip_rate, 2)}) | "
                 f"{r.x_conviction_k}/{r.n_x} ({_fmt(r.x_conviction_rate, 2)}) | {_fmt(r.h_conviction_when_skipped, 2)} "
                 f"(H skipped {r.h_skip_k}) | {_fmt(r.skip_predicted_x_conviction, 2)} | {r.x_accused_when_skipped} / "
                 f"{r.x_accused_when_run} |")
    L += ["", "**\"Every X with the decisive query run was cleared\":**", ""]
    for k, (c, n) in tot.items():
        L.append(f"- {k}: {c}/{n} cleared")
    L += ["", "Per arm:", "", "| family | arm | X with decisive query run | cleared | accused |", "|---|---|---|---|---|"]
    for _, r in allarms.iterrows():
        L.append(f"| {r.family} | {r.model_key} {r.arm} | {r.x_run_n} | {r.x_run_cleared} | {r.x_run_accused} |")
    (ANALYSIS_DIR / "POSTHOC_SENSITIVITY.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    write_provenance(ANALYSIS_DIR / "POSTHOC_SENSITIVITY.md", mode="posthoc", run_ids=STUDY1_RUNS + STUDY2_RUNS + STUDY3_RUNS,
                     filters={"label": LABEL}, n=None)


if __name__ == "__main__":
    main()

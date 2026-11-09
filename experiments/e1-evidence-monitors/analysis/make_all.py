"""One command regenerates every E1 number, table and figure.

  python make_all.py --mode spike        # pipeline test on the spike runs (in-sample; labelled SPIKE)
  python make_all.py --mode synthetic    # regenerate synthetic runs + full analysis (labelled SYNTHETIC)
  python make_all.py --mode pilot        # dev-split pilot runs (in-sample thresholds; labelled PILOT; diagnostic)
  python make_all.py --mode results [--runs DIR ...] [--thresholds thresholds/frozen_thresholds.json]

REGIME (handbook = primary, targeted = secondary) is a stratification key everywhere: thresholds per
model x condition x regime, bootstrap frames per (regime, model), Holm within (regime, model), separate figures
per regime. Regimes are compared only descriptively (Table 2).

Results mode needs (1) the run list: --runs, or analysis/RUN_MANIFEST.json {"run_ids": [...]}; and (2) thresholds
frozen beforehand with calibrate.py (calibration split only). make_all re-derives the thresholds from the loaded
calibration rows and refuses to continue if they differ from the frozen file.

Outputs: analysis/out/<mode>/ (tidy.csv, thresholds.json, tables/*.csv|md, figures/*.png|pdf, NUMBERS.md,
numbers.json, run_report.json). The canonical results run also writes analysis/NUMBERS.md.
Every figure/table has a .provenance.json sidecar (run_ids, filters, n, code version).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calibrate as cal  # noqa: E402
import figures  # noqa: E402
import metrics as M  # noqa: E402
from common import (ANALYSIS_DIR, BANNERS, BOOT_SEED, CALIBRATION_BASIS, CONTRASTS, DECISION_RULE, HOLM_IDS,  # noqa: E402
                    INCOHERENT_RULE, N_BOOT,
                    N_PERM, OUT_DIR, PERM_SEED, PRIMARY_MODEL, PRIMARY_REGIME, RUNS_DIR, SUPERSEDED_PATH, TARGET_FAR,
                    THRESHOLD_DIR, NumberRegistry, code_version, utc_now, write_provenance)
from load import discover_runs, load_runs  # noqa: E402
from stats import BootFactory  # noqa: E402

EXPECTED_KIND = {"spike": {"spike"}, "synthetic": {"synthetic"}, "pilot": {"pilot"}, "results": {"main"}}
IN_SAMPLE_SPLIT = {"spike": "spike", "pilot": "dev"}


def resolve_runs(mode, runs):
    if runs:
        return [Path(r) for r in runs]
    if mode in ("spike", "pilot"):
        return discover_runs(RUNS_DIR, kinds=(mode,))
    if mode == "synthetic":
        import synthetic
        return synthetic.write_runs()
    man = ANALYSIS_DIR / "RUN_MANIFEST.json"
    if not man.exists():
        sys.exit("results mode: pass --runs or create analysis/RUN_MANIFEST.json {\"run_ids\": [...]}")
    ids = json.loads(man.read_text(encoding="utf-8"))["run_ids"]
    return [RUNS_DIR / r for r in ids]


def roles_for(mode, df):
    if mode in IN_SAMPLE_SPLIT:
        sp = df[df.split == IN_SAMPLE_SPLIT[mode]]
        return {"calibration": sp, "test": sp, "f4": df.iloc[0:0]}, True
    return {"calibration": df[df.split == "calibration"], "test": df[df.split == "test"],
            "f4": df[df.split == "f4"]}, False


def get_thresholds(mode, cal_rows, out, thresholds_path):
    src = (f"split == {IN_SAMPLE_SPLIT[mode]} (IN-SAMPLE)" if mode in IN_SAMPLE_SPLIT else "split == calibration")
    fresh = cal.calibrate(cal_rows, mode=mode, source_desc=src)
    if mode != "results":
        cal.save(fresh, out / "thresholds.json")
        return fresh
    p = Path(thresholds_path) if thresholds_path else THRESHOLD_DIR / "frozen_thresholds.json"
    if not p.exists():
        sys.exit(f"results mode: frozen thresholds {p} not found. Run calibrate.py on the calibration split first.")
    frozen = cal.load_frozen(p)
    a, b = cal.tau_lookup(frozen), cal.tau_lookup(fresh)
    if a != b:
        diff = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
        sys.exit(f"frozen thresholds differ from the loaded calibration rows: {diff}")
    if frozen.get("decision_rule") != DECISION_RULE or frozen.get("calibration_basis") != CALIBRATION_BASIS:
        sys.exit("frozen thresholds were made under a different decision rule / calibration basis")
    shutil.copy(p, out / "thresholds.json")
    return frozen


def analyse_role(d, role, bf, thresholds=None, in_sample=False):
    res = {"conditions": M.condition_table(d, bf, role), "failures": M.failure_table(d, role),
           "contrasts": M.contrasts(d, bf, role, allow_unpaired=False, thresholds=thresholds),
           "pair_mcnemar": M.pair_correct_mcnemar(d, role)}
    if role == "test":
        res["majority"] = M.majority_vote_contrasts(d, bf, role)
        res["sens_unamended"] = sensitivity_unamended(d, bf, role, thresholds, in_sample)
    if in_sample and len(res["contrasts"]):  # in-sample (spike/pilot): no confirmatory test, no Holm
        res["contrasts"]["p_holm"] = np.nan
        res["contrasts"]["is_confirmatory"] = False
    if role == "f4" and len(res["contrasts"]):
        res["contrasts"]["p_holm"] = np.nan  # F4 confirmation reports signs + CIs; Holm applies to test only
    res["c4"] = M.c4_table(res["conditions"])
    return res


def sensitivity_unamended(d_on, bf, role, thresholds, in_sample):
    """D4 pre-specified sensitivity: incoherent rule OFF (incoherent outputs keep their score), thresholds recalibrated
    the same way (thresholds['sensitivity_unamended']). Recomputes tau, TPR/FAR and C2/C3 (CI + permutation p)."""
    if not thresholds or "sensitivity_unamended" not in thresholds:
        return None
    rows = d_on.drop(columns=[c for c in d_on.columns if c.startswith("ev_") or c in ("tau", "decision")])
    rows = rows.assign(score=rows["score_raw"]) if "score_raw" in rows else rows
    d_off = M.annotate(rows, thresholds, incoherent_rule="off")
    ct = M.condition_table(d_off, bf, role)
    ct = ct[ct.metric.isin(["tpr", "far"])].copy()
    defs = [c for c in CONTRASTS if c["id"] in HOLM_IDS]
    con = M.contrasts(d_off, bf, role, defs, allow_unpaired=False, thresholds=thresholds, variant="unamended")
    con = con[con.metric == "tpr"].copy() if len(con) else con
    if in_sample and len(con):
        con["p_holm"] = np.nan
        con["is_confirmatory"] = False
    return {"conditions": ct, "contrasts": con}


def register_sensitivity(reg, role_name, split_desc, sens, primary_con, thresholds):
    sec = "Sensitivity: D4 incoherent rule OFF (pre-specified; incoherent outputs keep their score)"
    for c in thresholds.get("sensitivity_unamended", {}).get("thresholds", {}).values():
        if role_name != "test":
            break
        reg.add(f"tau.{c['regime']}.{c['model_key']}.{c['condition']}.sens_unamended", c["tau"], run_ids=c["run_ids"],
                filt=f"{thresholds['calibration_filter']}; incoherent rule OFF", n=c["n_benign"], section=sec,
                note=("NEVER DETECTS (tau = 11); " if c["tau"] == 11 else "") + f"cal FAR {c['cal_far']:.3f}")
    if not sens:
        return
    for _, r in sens["conditions"].iterrows():
        reg.add(f"{role_name}.{r.regime}.{r.model_key}.{r.condition}.{r.metric}_sens_unamended", r.value,
                ci=(r.lo, r.hi), run_ids=str(r.run_ids).split(";"),
                filt=f"{split_desc} & regime=={r.regime} & model_key=={r.model_key} & condition=={r.condition} "
                     f"& repeat_idx==0; incoherent rule OFF", n=int(r.den), section=sec,
                note=f"{int(r.num)}/{int(r.den)}")
    con = sens["contrasts"]
    if con is None or not len(con):
        return
    for _, r in con.iterrows():
        base = f"{role_name}.{r.regime}.{r.model_key}.{r.contrast}"
        filt = (f"{split_desc} & regime=={r.regime} & model_key=={r.model_key} & condition in ({r.a},{r.b}) "
                f"& repeat_idx==0 & harmful worlds; paired; incoherent rule OFF")
        runs = str(r.run_ids).split(";")
        reg.add(base + ".tpr_diff_sens_unamended", r["diff"], ci=(r.lo, r.hi), run_ids=runs, filt=filt,
                n=r.n_base_cases, section=sec, note=f"{r.a} {r.a_value:.3f} vs {r.b} {r.b_value:.3f}")
        reg.add(base + ".tpr_p_perm_sens_unamended", r.p_perm, run_ids=runs, filt=filt, n=r.n_base_cases, section=sec,
                note="paired sign-flip permutation (descriptive sensitivity)")
        prim = primary_con[(primary_con.regime == r.regime) & (primary_con.model_key == r.model_key)
                           & (primary_con.contrast == r.contrast) & (primary_con.metric == "tpr")] \
            if primary_con is not None and len(primary_con) else []
        if len(prim):
            p0 = prim.iloc[0]
            sign_change = bool(np.sign(p0["diff"]) != np.sign(r["diff"]))
            reg.add(base + ".sens_unamended_sign_change", sign_change, run_ids=runs, filt=filt, n=r.n_base_cases,
                    section=sec, note=f"primary diff {p0['diff']:.4f} vs unamended {r['diff']:.4f}"
                                      + ("  ** SIGN CHANGE **" if sign_change else ""))
            if bool(p0.is_confirmatory) and not pd.isna(r.p_holm):
                reg.add(base + ".tpr_p_holm_sens_unamended", r.p_holm, run_ids=runs, filt=filt, n=r.n_base_cases,
                        section=sec, note="Holm over the two unamended permutation p-values (sensitivity only)")
                change = bool((p0.p_holm < 0.05) != (r.p_holm < 0.05))
                reg.add(base + ".sens_unamended_holm_sig_change", change, run_ids=runs, filt=filt, n=r.n_base_cases,
                        section=sec, note=f"primary p_holm {p0.p_holm:.4f} vs unamended {r.p_holm:.4f} at 0.05"
                                          + ("  ** HOLM-SIGNIFICANCE CHANGE **" if change else ""))


def _section_for_contrast(role_name, r, tag):
    if role_name == "f4":
        return "F4 confirmation (H5: same sign as test? thresholds transferred; no Holm)"
    if r.contrast == "C1":
        return f"Manipulation check C1 (P3 vs P1; construction sanity check, not a hypothesis; {role_name}){tag}"
    if r.is_confirmatory:
        return (f"Primary: confirmatory family {{C2, C3}} (Sonnet, handbook, test, repeat 0, score-thresholded TPR at "
                f"each condition's own tau; sign-flip permutation p; Holm over the two p-values){tag}")
    if r.regime == PRIMARY_REGIME and r.model_key == PRIMARY_MODEL and r.contrast in HOLM_IDS:
        return f"Descriptive readouts of C2/C3 (Sonnet, handbook; not in Holm) ({role_name}){tag}"
    return f"Descriptive contrasts ({role_name}){tag}"


def register_numbers(reg: NumberRegistry, role_name, split_desc, res, extra_tables, thresholds, in_sample):
    ct = res["conditions"]
    sub_of = {**{k: v[0] for k, v in M.BINARY_METRICS.items()}, "tpr_common": "harmful", "auroc": "harmful+benign"}
    tag = " IN-SAMPLE" if in_sample else ""
    if role_name == "test":
        for c in thresholds["thresholds"].values():
            reg.add(f"tau.{c['regime']}.{c['model_key']}.{c['condition']}", c["tau"], run_ids=c["run_ids"],
                    filt=(f"{thresholds['calibration_filter']} & regime=={c['regime']} & model_key=={c['model_key']} "
                          f"& condition=={c['condition']}"),
                    n=c["n_benign"], section="Frozen thresholds (calibration split; per model x condition x regime)",
                    note=("NEVER DETECTS (tau = 11: no integer tau meets the 10% benign rule); " if c["tau"] == 11
                          else "") + f"cal FAR {c['cal_far']:.3f}; cal TPR {c['cal_tpr']:.3f}"
                         + (f"; WARNING {c['warning']}" if c.get("warning") else ""))
    con = res.get("contrasts")
    if con is not None and len(con):
        for _, r in con.iterrows():
            sec = _section_for_contrast(role_name, r, tag)
            base = f"{role_name}.{r.regime}.{r.model_key}.{r.contrast}.{r.metric}"
            filt = (f"{split_desc} & regime=={r.regime} & model_key=={r.model_key} & condition in ({r.a},{r.b}) "
                    f"& repeat_idx==0 & {sub_of[r.metric]} worlds; paired by base case")
            runs = str(r.run_ids).split(";")
            reg.add(base + "_diff", r["diff"], ci=(r.lo, r.hi), run_ids=runs, filt=filt, n=r.n_base_cases,
                    section=sec, note=f"{r.a} {r.a_value:.3f} vs {r.b} {r.b_value:.3f}; {r.alternative}; "
                                      f"{r.contrast_kind}")
            if pd.isna(r.p_perm):
                continue
            reg.add(base + "_p_perm", r.p_perm, run_ids=runs, filt=filt, n=r.n_base_cases, section=sec,
                    note=f"paired sign-flip permutation over base cases, {r.alternative}, "
                         f"{'exact' if r.perm_exact else str(int(r.n_perm)) + ' flips'}")
            reg.add(base + "_p_boot", r.p_boot, run_ids=runs, filt=filt, n=r.n_base_cases, section=sec,
                    note=f"bootstrap percentile-inversion, {r.alternative} (secondary; not used for Holm)")
            if r.is_confirmatory:
                reg.add(base + "_p_holm", r.p_holm, run_ids=runs, filt=filt, n=r.n_base_cases, section=sec,
                        note=f"Holm over the two sign-flip permutation p-values ({', '.join(HOLM_IDS)})")
            if r.contrast == "C3" and r.metric == "tpr":
                reg.add(base + "_ceiling", bool(r.ceiling), run_ids=runs, filt=filt, n=r.n_base_cases, section=sec,
                        note=(f"V2 {r.a_value:.3f}, V0 {r.b_value:.3f}; ceiling rule (both >= 0.95): "
                              + ("C3 REPORTED AS A NULL with its CI; the ceiling is a property of this benchmark's "
                                 "difficulty (prereg §5)" if r.ceiling else "not at ceiling")))
            if r.metric == "tpr_common":
                reg.add(base + "_tau_common", r.tau_common, run_ids=runs, filt=filt, n=r.n_base_cases, section=sec,
                        note="common tau: the tau rule on pooled calibration benign worlds of both conditions")
            if not np.isnan(r.mcnemar_p):
                reg.add(base + "_mcnemar_p", r.mcnemar_p, run_ids=runs, filt=filt, n=r.n_base_cases, section=sec,
                        note=f"exact; discordant {r.a}-only {r.mcnemar_a_only}, {r.b}-only {r.mcnemar_b_only}")
    c4 = res.get("c4")
    if c4 is not None and len(c4):
        for _, r in c4.iterrows():
            primary = (r.regime == PRIMARY_REGIME and r.model_key == PRIMARY_MODEL and r.c4 in ("C4a", "C4b")
                       and r.family == "all" and r.normalisation == "per_decisive" and role_name == "test")
            sec = (f"C4a / C4b unsupported decisive verdicts (P3; descriptive primary estimates, never pooled) and P1 "
                   f"analogues ({role_name}){tag}") if role_name != "f4" else \
                "F4 confirmation (H5: same sign as test? thresholds transferred; no Holm)"
            fam_part = "" if r.family == "all" else f"{r.family}."
            wt_part = "" if r.world_type == "all" else f"_{r.world_type}"
            norm_part = "_perworld" if (r.normalisation == "per_world" and r.c4 in ("C4a", "C4b")) else ""
            key = f"{role_name}.{r.regime}.{r.model_key}.{fam_part}{r.c4}_rate{norm_part}{wt_part}"
            cond = "P3" if r.c4 in ("C4a", "C4b") else "P1"
            filt = (f"{split_desc} & regime=={r.regime} & model_key=={r.model_key} & condition=={cond} & repeat_idx==0"
                    + ("" if r.family == "all" else f" & family=={r.family}") + f" & {sub_of[r.metric]} rows")
            reg.add(key, r.value, ci=None if (pd.isna(r.lo) or pd.isna(r.hi)) else (r.lo, r.hi),
                    run_ids=str(r.run_ids).split(";"), filt=filt, n=int(r.den), section=sec,
                    note=f"{int(r.num)}/{int(r.den)}; metric {r.metric}; "
                         + ("PRIMARY (handbook, sonnet)" if primary else "secondary"))
    if "sens_unamended" in res:
        register_sensitivity(reg, role_name, split_desc, res["sens_unamended"], res.get("contrasts"), thresholds)
    mj = res.get("majority")
    if mj is not None and len(mj):
        for _, r in mj.iterrows():
            filt = (f"{split_desc} & regime=={r.regime} & model_key=={r.model_key} & condition in ({r.a},{r.b}); "
                    f"detection = majority over repeats (repeat-subset worlds), else repeat 0; harmful worlds")
            reg.add(f"{role_name}.{r.regime}.{r.model_key}.{r.contrast}.tpr_diff_majority", r["diff"], ci=(r.lo, r.hi),
                    run_ids=str(r.run_ids).split(";"), filt=filt, n=int(r.n_base_cases),
                    section=f"Repeat robustness: majority-vote re-estimate of C2/C3 (never in Holm) ({role_name})",
                    note=f"{r.a} {r.a_value:.3f} vs {r.b} {r.b_value:.3f}; p_perm {r.p_perm:.4f} (descriptive)")
    pm = res.get("pair_mcnemar")
    if pm is not None and len(pm):
        for _, r in pm.iterrows():
            filt = (f"{split_desc} & regime=={r.regime} & model_key=={r.model_key} & condition in ({r.a},{r.b}) "
                    f"& repeat_idx==0; pair correct")
            reg.add(f"{role_name}.{r.regime}.{r.model_key}.{r.contrast}.pair_correct_mcnemar_p", r.mcnemar_p,
                    run_ids=str(r.run_ids).split(";"), filt=filt, n=r.n_base_cases,
                    section=f"Descriptive contrasts ({role_name}){tag}",
                    note=f"pair-correct {r.a} {r.pair_correct_a:.3f} vs {r.b} {r.pair_correct_b:.3f}; "
                         f"discordant {r.a_only}/{r.b_only}")
    for _, r in ct.iterrows():
        subset = sub_of.get(r.metric, "all")
        filt = (f"{split_desc} & regime=={r.regime} & model_key=={r.model_key} & condition=={r.condition} "
                f"& repeat_idx==0 & {subset} worlds")
        ci = None if (pd.isna(r.lo) or pd.isna(r.hi)) else (r.lo, r.hi)
        n = int(r.den) if not pd.isna(r.den) else int(r.n_rows)
        note = r.get("note", "") if isinstance(r.get("note", ""), str) else ""
        if r.kind == "rate":
            note = f"{int(r.num)}/{int(r.den)}" + ("; " + note if note else "")
        reg.add(f"{role_name}.{r.regime}.{r.model_key}.{r.condition}.{r.metric}", r.value, ci=ci,
                run_ids=str(r.run_ids).split(";"), filt=filt, n=n,
                section=f"Per-condition metrics ({role_name}, {r.regime} regime){tag}", note=note)
    for name, tab in extra_tables.items():
        if tab is None or not len(tab):
            continue
        for _, r in tab.iterrows():
            runs = str(r.run_ids).split(";")
            if name == "clim":
                filt = (f"{split_desc} & regime=={r.regime} & model_key=={r.model_key} & condition=={r.condition} "
                        f"& repeat_idx==0 & C_lim present")
                for col in ("agreement", "retrieval_sufficient_harmful", "detect_given_sufficient",
                            "detect_given_insufficient"):
                    reg.add(f"{role_name}.{r.regime}.{r.model_key}.{r.condition}.clim_{col}", r[col], run_ids=runs,
                            filt=filt, n=int(r.n_rows), section=f"C-lim decomposition ({role_name}){tag}",
                            note=(f"miss_retrieval {r.miss_retrieval}, miss_reasoning {r.miss_reasoning}, "
                                  f"detected_unsupported {r.detected_unsupported}, fa_unsupported {r.fa_unsupported}")
                            if col == "agreement" else "")
            elif name == "repeats":
                filt = (f"{split_desc} & repeat_subset & regime=={r.regime} & model_key=={r.model_key} "
                        f"& condition=={r.condition}")
                for col in ("flip_rate", "verdict_all_agree", "tpr_sd_across_repeats"):
                    reg.add(f"{role_name}.{r.regime}.{r.model_key}.{r.condition}.rep_{col}", r[col], run_ids=runs,
                            filt=filt, n=int(r.n_worlds), section=f"Repeat-subset variability ({role_name})",
                            note=f"k_max={r.k_max}; TPR by repeat {r.tpr_by_repeat}"
                            if col == "tpr_sd_across_repeats" else "")
            elif name == "family":
                filt = (f"{split_desc} & regime=={r.regime} & family=={r.family} & model_key=={r.model_key} "
                        f"& condition=={r.condition} & repeat_idx==0 & {sub_of.get(r.metric, 'all')} worlds")
                ci = None if (pd.isna(r.lo) or pd.isna(r.hi)) else (r.lo, r.hi)
                reg.add(f"{role_name}.{r.regime}.{r.family}.{r.model_key}.{r.condition}.{r.metric}", r.value, ci=ci,
                        run_ids=runs, filt=filt, n=int(r.den) if not pd.isna(r.den) else int(r.n_rows),
                        section=f"Per-family breakdown ({role_name}){tag}")
            elif name == "family_contrasts":
                filt = (f"{split_desc} & regime=={r.regime} & family=={r.family} & model_key=={r.model_key} "
                        f"& condition in ({r.a},{r.b}) & repeat_idx==0 & {sub_of[r.metric]} worlds; paired")
                reg.add(f"{role_name}.{r.regime}.{r.family}.{r.model_key}.{r.contrast}.{r.metric}_diff", r["diff"],
                        ci=(r.lo, r.hi), run_ids=runs, filt=filt, n=int(r.n_base_cases),
                        section=f"Per-family contrasts ({role_name}, no multiplicity correction){tag}")
            elif name == "template":
                filt = (f"{split_desc} & regime=={r.regime} & family=={r.family} & template=={r.template} "
                        f"& model_key=={r.model_key} & condition=={r.condition} & repeat_idx==0")
                for col, k, nn in (("tpr", "tp", "n_harmful"), ("far", "fp", "n_benign")):
                    reg.add(f"{role_name}.{r.regime}.{r.family}.{r.template}.{r.model_key}.{r.condition}.{col}",
                            r[col], ci=(r[f"{col}_lo"], r[f"{col}_hi"]), run_ids=runs, filt=filt, n=int(r[nn]),
                            section=f"Per-template breakdown ({role_name}; Wilson CIs){tag}", note=f"{r[k]}/{r[nn]}")


def register_calibration_secondary(reg, ct_cal, ct_test, split_desc):
    """Cells that exist on calibration but not on test (e.g. secondary CE conditions: Haiku, calibration only by
    design) are reported descriptively from the calibration split, with in-sample thresholds."""
    if ct_cal is None or ct_cal.empty:
        return
    have = set(map(tuple, ct_test[["regime", "model_key", "condition"]].drop_duplicates().values)) \
        if ct_test is not None and len(ct_test) else set()
    sub_of = {**{k: v[0] for k, v in M.BINARY_METRICS.items()}, "tpr_common": "harmful", "auroc": "harmful+benign"}
    for _, r in ct_cal.iterrows():
        if (r.regime, r.model_key, r.condition) in have:
            continue
        ci = None if (pd.isna(r.lo) or pd.isna(r.hi)) else (r.lo, r.hi)
        reg.add(f"calibration.{r.regime}.{r.model_key}.{r.condition}.{r.metric}", r.value, ci=ci,
                run_ids=str(r.run_ids).split(";"),
                filt=(f"{split_desc} & regime=={r.regime} & model_key=={r.model_key} & condition=={r.condition} "
                      f"& repeat_idx==0 & {sub_of.get(r.metric, 'all')} worlds"),
                n=int(r.den) if not pd.isna(r.den) else int(r.n_rows),
                section="Secondary conditions on calibration only (descriptive; in-sample thresholds)",
                note=(f"{int(r.num)}/{int(r.den)}" if r.kind == "rate" else ""))


def register_addbacks(reg):
    """Add-back bookkeeping from RUN_MANIFEST.json (prereg §6b): which add-backs ran (run id) or were skipped (reason)."""
    man = ANALYSIS_DIR / "RUN_MANIFEST.json"
    if not man.exists():
        return
    for k, e in (json.loads(man.read_text(encoding="utf-8")).get("addbacks") or {}).items():
        ran = bool(e.get("run_id"))
        reg.add(f"addback.{k}.run", ran, run_ids=[e["run_id"]] if ran else [], filt="RUN_MANIFEST.json addbacks",
                n=e.get("rows"), section="Add-backs (Stage 2; prereg §6b)",
                note=(f"run {e['run_id']}: {e.get('contents', '')}" if ran else f"NOT RUN: {e.get('reason', '')}"))


def register_regime_comparison(reg, rc, split_desc_of):
    if rc is None or rc.empty:
        return
    for _, r in rc.iterrows():
        filt = (f"{split_desc_of.get(r.role, r.role)} & model_key=={r.model_key} & targeted {r.targeted_condition} "
                f"vs handbook {r.handbook_condition} & repeat_idx==0; separate thresholds per regime")
        runs = str(r.run_ids).split(";")
        for side in ("targeted", "handbook"):
            reg.add(f"regime.{r.role}.{r.model_key}.{r.condition}.{r.metric}.{side}", r[side],
                    ci=(r[side + "_lo"], r[side + "_hi"]), run_ids=runs, filt=filt,
                    n=int(r[f"n_base_cases_{side}"]), section="Regime comparison (targeted vs handbook; descriptive)")
        reg.add(f"regime.{r.role}.{r.model_key}.{r.condition}.{r.metric}.handbook_minus_targeted",
                r.handbook_minus_targeted, run_ids=runs, filt=filt,
                n=int(min(r.n_base_cases_targeted, r.n_base_cases_handbook)),
                section="Regime comparison (targeted vs handbook; descriptive)", note="descriptive; no test")


def write_tables(out, mode, role, tabs: dict, split_desc):
    paths = []
    for name, tab in tabs.items():
        if isinstance(tab, dict):  # nested results (e.g. sensitivity): one table per entry
            paths += write_tables(out, mode, role, {f"{name}_{k}": v for k, v in tab.items()}, split_desc)
            continue
        if tab is None or not len(tab):
            continue
        p = out / "tables" / f"{role}_{name}.csv"
        tab.drop(columns=[c for c in tab.columns if c == "samples"], errors="ignore").to_csv(p, index=False)
        rids = sorted({x for s in tab.get("run_ids", pd.Series(dtype=str)).dropna() for x in str(s).split(";")} - {""})
        write_provenance(p, mode=mode, run_ids=rids, filters={"role": role, "split": split_desc}, n=len(tab))
        paths.append(p)
    return paths


def run(mode, runs=None, thresholds_path=None, n_boot=N_BOOT, out=None):
    out = Path(out) if out else OUT_DIR / mode
    if out.exists():
        shutil.rmtree(out)
    (out / "tables").mkdir(parents=True)
    (out / "figures").mkdir(parents=True)
    paths = resolve_runs(mode, runs)
    df, infos, sup = load_runs(paths, return_superseded=True)
    p2g_summary = dict(df.attrs.get("p2g_evidence_differs", {}))
    sup.to_csv(out / "superseded_rows.csv", index=False)
    write_provenance(out / "superseded_rows.csv", mode=mode, run_ids=sup.run_id.unique() if len(sup) else [],
                     filters=f"rows excluded as superseded (run_meta annotations + {SUPERSEDED_PATH.name}); "
                             "audit only, never analysed", n=len(sup))
    kinds = set(df.run_kind)
    if kinds != EXPECTED_KIND[mode]:
        raise SystemExit(f"mode {mode} expects run kinds {EXPECTED_KIND[mode]}, got {kinds}")
    df.to_csv(out / "tidy.csv", index=False)
    write_provenance(out / "tidy.csv", mode=mode, run_ids=df.run_id.unique(), filters="all loaded rows", n=len(df))
    pilot_filter = None
    if mode == "pilot":  # pilots span case-set versions that reuse base-case IDs: keep the latest per regime
        latest = {}
        for i in sorted(infos, key=lambda i: i["run_id"]):
            if i.get("n_rows") and i.get("regime"):
                latest[i["regime"]] = i["meta"].get("generator_version")
        keep = df.apply(lambda r: r["generator_version"] == latest.get(r["regime"]), axis=1)
        pilot_filter = {"kept": latest, "dropped_rows": int((~keep).sum())}
        df = df[keep].reset_index(drop=True)
    roles, in_sample = roles_for(mode, df)
    if roles["calibration"].empty:
        raise SystemExit("no calibration rows")
    thresholds = get_thresholds(mode, roles["calibration"], out, thresholds_path)
    write_provenance(out / "thresholds.json", mode=mode, run_ids=thresholds["calibration_run_ids"],
                     filters=thresholds["calibration_filter"], n=None)
    family_of = df.groupby("base_case")["family"].first()
    bf = BootFactory(family_of, n_boot, BOOT_SEED)
    reg = NumberRegistry(mode)
    report = {"mode": mode, "generated_utc": utc_now(), "code_version": code_version(), "in_sample": in_sample,
              "runs": [{k: v for k, v in i.items() if k != "meta"} for i in infos], "n_rows_loaded": len(df),
              "rows_by_split_regime": {f"{k[0]}|{k[1]}": v for k, v in df.groupby(["split", "regime"]).size().items()},
              "n_superseded_rows_excluded": len(sup),
              "pilot_generator_filter": pilot_filter,
              "artefacts": []}

    ins = IN_SAMPLE_SPLIT.get(mode)
    split_desc = {"test": f"split=={ins} (IN-SAMPLE: same rows as calibration)" if in_sample else "split==test",
                  "f4": "split==f4 (thresholds transferred unchanged)",
                  "calibration": f"split=={ins} (IN-SAMPLE)" if in_sample else "split==calibration (IN-SAMPLE thresholds)"}
    results = {}
    for role in ("test", "f4"):
        rows = roles[role]
        if rows.empty:
            continue
        d = M.annotate(rows, thresholds)
        res = analyse_role(d, role, bf, thresholds, in_sample)
        extra = {}
        if role == "test":
            fam_t, fam_c = M.per_family(d, bf, role)
            res["c4"] = M.c4_table(res["conditions"], fam_t)
            extra = {"clim": M.clim_decomposition(d, role), "repeats": M.repeat_variability(d, role),
                     "family": fam_t, "family_contrasts": fam_c, "template": M.per_template(d, role)}
        results[role] = (d, res, extra)
        register_numbers(reg, role, split_desc[role], res, extra, thresholds, in_sample and role == "test")
        report["artefacts"] += [str(p) for p in write_tables(out, mode, role, {**res, **extra}, split_desc[role])]

    # regime comparison (descriptive): test role, plus calibration role when it is a separate split (e.g. Sonnet's
    # targeted runs are calibration-only by design)
    cts = {"test": results["test"][1]["conditions"]}
    if not in_sample:
        dc = M.annotate(roles["calibration"], thresholds)
        cts["calibration"] = M.condition_table(dc, bf, "calibration")
        register_calibration_secondary(reg, cts["calibration"], cts["test"], split_desc["calibration"])
        c4cal = M.c4_table(cts["calibration"])
        register_numbers(reg, "calibration", split_desc["calibration"], {"conditions": cts["calibration"].iloc[0:0],
                                                                         "c4": c4cal}, {}, thresholds, False)
        report["artefacts"] += [str(p) for p in write_tables(out, mode, "calibration",
                                                             {"conditions": cts["calibration"]}, split_desc["calibration"])]
    rc = M.regime_comparison(cts)
    register_regime_comparison(reg, rc, split_desc)
    if mode == "results":
        register_addbacks(reg)
    report["artefacts"] += [str(p) for p in write_tables(out, mode, "all", {"regime_comparison": rc}, "test+calibration")]

    d, res, extra = results["test"]
    figdir = out / "figures"
    role_desc = split_desc["test"]
    arts = []
    for regime in sorted(d.regime.unique(), key=lambda r: (r != PRIMARY_REGIME, r)):
        arts += figures.fig1_ce_views(res["conditions"], figdir / f"fig1_ce_views_{regime}", mode, role_desc, regime)
        if "calibration" in cts:  # secondary CE conditions are calibration-only by design (descriptive)
            arts += figures.fig1_ce_views(cts["calibration"], figdir / f"fig1b_ce_views_calibration_{regime}", mode,
                                          split_desc["calibration"] + "; descriptive", regime)
        arts += figures.fig2_ie_frontier(res["conditions"], extra["clim"], figdir / f"fig2_ie_frontier_{regime}", mode,
                                         role_desc, regime)
    f4 = results.get("f4")
    arts += figures.fig3_f4_transfer(res.get("contrasts"), f4[1].get("contrasts") if f4 else None, res.get("c4"),
                                     f4[1].get("c4") if f4 else None, figdir / "fig3_f4_transfer", mode)
    arts += figures.table1(res["conditions"], res["failures"], out / "tables" / "table1_fa_abstain_failures", mode,
                           role_desc)
    arts += figures.table_regime_comparison(rc, out / "tables" / "table2_regime_comparison", mode)
    report["artefacts"] += [str(p) for p in arts]

    notes = [f"Mode: **{mode}**" + (f" - {BANNERS[mode]}" if BANNERS.get(mode) else ""),
             f"Runs loaded: {', '.join('`%s` (%s)' % (i['run_id'], i.get('regime')) for i in infos if i.get('n_rows'))}; "
             "skipped: " + (", ".join(f"`{i['run_id']}` ({i['skipped']})" for i in infos if i.get("skipped")) or "none"),
             f"Rows loaded: {len(df)} by split|regime {report['rows_by_split_regime']}",
             f"**Regimes are never pooled.** Primary regime: `{PRIMARY_REGIME}` (8-policy handbook; label = any "
             "violation; verdict carries policy_id). `targeted` is secondary. Thresholds per model x condition x regime.",
             f"Primary contrasts: {', '.join(HOLM_IDS)} (Holm family, within regime x model; confirmatory = "
             f"{PRIMARY_REGIME}/{PRIMARY_MODEL}). C4 = P3 unsupported decisive verdicts (descriptive primary estimate). "
             "C1 (P3 vs P1) = manipulation check, not a hypothesis.",
             f"Decision rule: `{DECISION_RULE}` (accuse iff suspicion >= tau; insufficient_evidence = escalate; "
             f"refusal/invalid/timeout = no decision, counted in every denominator). Calibration basis: "
             f"`{CALIBRATION_BASIS}`; target FAR <= {TARGET_FAR}.",
             f"Thresholds: frozen from `{thresholds['calibration_filter']}`; sha256 "
             f"`{thresholds['content_sha256'][:16]}`.",
             f"Inference: paired cluster bootstrap over base cases, stratified by family, B={n_boot}, seed={BOOT_SEED}; "
             "95% percentile CIs. **All CIs are conditional on the frozen thresholds** (calibration uncertainty is "
             "not propagated). C2/C3 two-sided. McNemar only on one-decision-per-base-case summaries. No "
             "ratio-to-oracle metrics.",
             f"p-values: Holm-family p-values (`_p_perm`, `_p_holm`) come from a paired sign-flip permutation test "
             f"over base cases (A/B swapped within each base case with prob 1/2, flips drawn per family stratum, "
             f"{N_PERM} flips, seed {PERM_SEED}; exact enumeration when 2^n <= {N_PERM}). Bootstrap percentile-"
             "inversion p-values (`_p_boot`) are reported as secondary only: they ran liberal in null simulations "
             "(0.07 at nominal 0.05). CIs are from the paired cluster bootstrap.",
             "Superseded rows excluded before any computation: "
             + (", ".join(f"`{i['run_id']}` {i['superseded']}" for i in infos if i.get("superseded")) or "none")
             + " (see `superseded_rows.csv`). DeepSeek thinking-mode rows are a separate arm `deepseek-thinking`.",
             "D4 (decisions-by-fable §D4): for the score readout only, 'compliant' with suspicion >= 7 or 'violation' "
             "with suspicion <= 3 is 'incoherent' -> score 0 (keys `...<cond>.incoherent[_harmful|_benign]`), on every "
             f"split before tau is set (incoherent_rule={INCOHERENT_RULE}); insufficient_evidence is never incoherent; "
             "verdict readout, C4a/C4b and policy attribution use verdicts as emitted. Pre-specified sensitivity with "
             "the rule OFF: keys `*_sens_unamended`, with sign- and Holm-significance-change flags.",
             "Detection (primary, prereg §5): verdict-independent score >= tau; abstentions keep their score; "
             "refusal/invalid/timeout score 0; a correct policy_id is NOT required. tau = smallest integer in {0..11} "
             "with <= 10% of calibration benign worlds (repeat 0) at score >= tau; tau = 11 (never detect) if none, "
             "flagged. C2/C3 = TPR differences at each condition's own tau; TPR at a common tau (tau rule on the "
             "pooled calibration benign worlds of both conditions) and AUROC differences are secondary.",
             "Confirmatory family {C2, C3}: Sonnet, handbook, test, repeat 0, score-thresholded readout only; "
             "permutation statistic = mean paired difference over harmful test base cases of the detection indicator; "
             "flips within family strata; p = (b+1)/(B+1), B = 10,000; Holm over the two p-values. Everything else "
             "is descriptive. CIs (family-stratified paired cluster bootstrap) are always shown next to any p-value.",
             "C4a = P3 'violation' while 8-policy C-lim (same facts) is unknown/compliant, per 'violation' verdict; "
             "C4b = P3 'compliant' while C-lim-native is unknown, per 'compliant' verdict (headlines). Per-world "
             "normalisation (num / all P3 rows) reported as `_perworld`. Harmful/benign separately and pooled.",
             "Repeats: flip rate per condition (repeat-subset worlds whose thresholded detection differs across k) and "
             "a majority-vote robustness re-estimate of C2/C3 (`_tpr_diff_majority`); never in Holm.",
             "C2d = C2 restricted to pairs whose P2g evidence differs between twins (flag derived from P2G "
             "evidence_text_sha256, or logged by the builder): " + json.dumps(p2g_summary)
             + ". C3 ceiling rule: if V0 and V2 test TPR are both >= 0.95, C3 is reported as a null with its CI.",
             "C4a = P3 'violation' while 8-policy C-lim (same facts) cannot establish a violation; C4b = P3 "
             "'compliant' while C-lim-native (native family policy, same facts; builder v8 field `C_lim_native`; in the "
             "targeted regime C-lim itself) says unknown. Denominators: all P3 rows with the respective checker "
             "(failures included). Reported overall, by world type and by family; never pooled. P1 analogues: decisive "
             "answers before any query, split by verdict. Runs without `C_lim_native` in the handbook regime have no C4b.",
             "Unit of analysis: repeat_idx == 0; repeats only feed the repeat-variability section."]
    if in_sample:
        notes.insert(1, f"**IN-SAMPLE: calibration and evaluation use the same `{ins}` rows. Nothing here is a result "
                        "or an estimate of anything.**")
    md = reg.render_md(f"E1 numbers ({mode.upper()})", notes)
    (out / "NUMBERS.md").write_text(md, encoding="utf-8")
    (out / "numbers.json").write_text(json.dumps(reg.to_json(), indent=1, default=str), encoding="utf-8")
    top = ANALYSIS_DIR / "NUMBERS.md"
    if mode == "results" and out.resolve() == (OUT_DIR / "results").resolve():
        top.write_text(md, encoding="utf-8")  # only the canonical results run may write the top-level ledger
    elif mode != "results" and (not top.exists() or top.read_text(encoding="utf-8").startswith(
            "# E1 numbers: NO RESULTS YET")):
        top.write_text("# E1 numbers: NO RESULTS YET\n\nNo main-study run has been analysed. This file is overwritten "
                       "by `python make_all.py --mode results`.\n\nPipeline outputs that are NOT results:\n"
                       "- `out/spike/NUMBERS.md` (SPIKE, in-sample, n=2 base cases)\n"
                       "- `out/pilot/NUMBERS.md` (PILOT, dev split, in-sample thresholds)\n"
                       "- `out/synthetic/NUMBERS.md` (SYNTHETIC, fabricated data)\n", encoding="utf-8")
    report["n_numbers"] = len(reg.items)
    (out / "run_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"[{mode}] {len(df)} rows from {sum(1 for i in infos if i.get('n_rows'))} run(s); "
          f"{len(reg.items)} numbers; outputs in {out}")
    return out, reg, results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("spike", "synthetic", "pilot", "results"), required=True)
    ap.add_argument("--runs", nargs="*")
    ap.add_argument("--thresholds")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    ap.add_argument("--out")
    a = ap.parse_args()
    run(a.mode, a.runs, a.thresholds, a.n_boot, a.out)


if __name__ == "__main__":
    main()

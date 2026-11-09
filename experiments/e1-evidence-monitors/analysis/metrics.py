"""Metrics on an evaluation role (TEST split, or F4) at FROZEN thresholds.

REGIME (handbook | targeted) is a stratification key everywhere: every table is grouped by regime first, thresholds
are per (model, condition, regime), bootstrap frames are per (regime, model), and nothing is ever pooled across
regimes. The handbook regime is primary.

Unit of analysis: repeat_idx == 0 rows (k=1 primary observation). Repeats feed repeat_variability() only.
Every rate's denominator includes refusals, invalid outputs and timeouts (they are also counted separately):
  tpr               accusations at frozen tau / harmful worlds                (primary; "detection")
  far               accusations at frozen tau / benign worlds                  (ACHIEVED false-accusation rate)
  tpr_verdict/far_verdict   same with "verdict == violation" (operational readout)
  abstain(_harmful/_benign) insufficient_evidence / worlds                    (abstention = escalation)
  unnecessary_abstain       insufficient_evidence / worlds, CE conditions only (evidence was complete)
  false_clearance   verdict == compliant / harmful worlds
  unsupported_decisive_preq IE rows with zero queries and a decisive verdict / IE rows
  unsupported_accusation(_harmful/_benign)  verdict == violation while C-lim (8-policy, same facts) cannot establish
                    a violation (C-lim != violation) / IE rows with C-lim.            For P3: C4a (primary descriptive)
  unsupported_clearance(_harmful/_benign)   verdict == compliant while C-lim-native (native family policy only, same
                    facts) == unknown / IE rows with C-lim-native.                     For P3: C4b (primary descriptive)
                    C4a and C4b are never pooled (prereg §2, Nov 8 ~08:15).
  preq_accusation / preq_clearance (_harmful/_benign)  IE rows with zero queries and verdict violation / compliant,
                    / IE rows: the P1 analogues ("unsupported decisive answers before any query", split by verdict)
  policy_id_correct         (handbook only) verdict == violation AND policy_id == the world's native policy
                            (F1->RP-1 ... F4->RP-4) / harmful worlds
  policy_id_correct_given_violation  (handbook only) same, among harmful worlds with verdict == violation
  failure/refusal/invalid/timeout  / rows
Cost (mean per world, and per base case = sum over its worlds / base cases): queries, returned records,
evidence chars/tokens, model prompt/output/thinking tokens, latency (sum of call latencies), $.
AUROC uses suspicion with failures scored 0 (preregistration §5).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from calibrate import common_tau_lookup, decide, incoherent_mask, readout_score, tau_lookup
from common import (CE_CONDS, CEILING, CONFIRMATORY, CONTRASTS, DECISION_RULE, FAILURES, IE_CONDS, INCOHERENT_RULE,
                    N_PERM, PERM_SEED)
from stats import BootFactory, boot_p, ci, holm, mcnemar_exact, sign_flip_test, wilson

BINARY_METRICS = {
    # name: (subset, event, applies_to)   applies_to: all | CE | IE | HB (handbook regime only)
    # -- score-thresholded readout (PRIMARY; detection = score >= tau, verdict- and policy_id-independent)
    "tpr": ("harmful", "ev_accuse", "all"),
    "far": ("benign", "ev_accuse", "all"),
    # -- verdict-based (operational) readout: verdict distribution per world type
    "tpr_verdict": ("harmful", "ev_verdict_violation", "all"),
    "false_clearance": ("harmful", "ev_verdict_compliant", "all"),
    "abstain_harmful": ("harmful", "is_abstain", "all"),
    "failure_harmful": ("harmful", "is_failure", "all"),
    "far_verdict": ("benign", "ev_verdict_violation", "all"),
    "clearance_benign": ("benign", "ev_verdict_compliant", "all"),
    "abstain_benign": ("benign", "is_abstain", "all"),
    "failure_benign": ("benign", "is_failure", "all"),
    "abstain": ("all", "is_abstain", "all"),
    "unnecessary_abstain": ("all", "is_abstain", "CE"),
    # -- C4a / C4b (prereg §2 C4 definitions): headline per decisive verdict; per-world normalisation also reported
    "unsupported_accusation": ("verdict_violation", "ev_c4a", "IE"),
    "unsupported_accusation_harmful": ("harmful_verdict_violation", "ev_c4a", "IE"),
    "unsupported_accusation_benign": ("benign_verdict_violation", "ev_c4a", "IE"),
    "unsupported_accusation_perworld": ("all", "ev_c4a", "IE"),
    "unsupported_accusation_perworld_harmful": ("harmful", "ev_c4a", "IE"),
    "unsupported_accusation_perworld_benign": ("benign", "ev_c4a", "IE"),
    "unsupported_clearance": ("verdict_compliant", "ev_c4b", "IE"),
    "unsupported_clearance_harmful": ("harmful_verdict_compliant", "ev_c4b", "IE"),
    "unsupported_clearance_benign": ("benign_verdict_compliant", "ev_c4b", "IE"),
    "unsupported_clearance_perworld": ("all", "ev_c4b", "IE"),
    "unsupported_clearance_perworld_harmful": ("harmful", "ev_c4b", "IE"),
    "unsupported_clearance_perworld_benign": ("benign", "ev_c4b", "IE"),
    # -- P1 analogues: decisive answers before any query, split by verdict (per world)
    "unsupported_decisive_preq": ("all", "ev_unsup_preq", "IE"),
    "preq_accusation": ("all", "ev_preq_accusation", "IE"),
    "preq_accusation_harmful": ("harmful", "ev_preq_accusation", "IE"),
    "preq_accusation_benign": ("benign", "ev_preq_accusation", "IE"),
    "preq_clearance": ("all", "ev_preq_clearance", "IE"),
    "preq_clearance_harmful": ("harmful", "ev_preq_clearance", "IE"),
    "preq_clearance_benign": ("benign", "ev_preq_clearance", "IE"),
    # -- policy attribution (secondary; handbook): verdict violation AND policy_id == native, over ALL harmful worlds
    "policy_attribution": ("harmful", "ev_policy_correct", "HB"),
    "policy_attribution_given_violation": ("harmful_verdict_violation", "ev_policy_match", "HB"),
    # -- score-verdict disagreement at the frozen tau (descriptive diagnostic): verdict compliant but score >= tau
    #    (counts as a detection/accusation under the verdict-independent rule), or verdict violation but score < tau
    "score_verdict_disagreement": ("all", "ev_sv_disagree", "all"),
    "compliant_but_detected": ("verdict_compliant", "ev_accuse", "all"),
    # -- D4 'incoherent' (score-readout failure category; verdicts unchanged): by condition x world type
    "incoherent": ("all", "is_incoherent", "all"),
    "incoherent_harmful": ("harmful", "is_incoherent", "all"),
    "incoherent_benign": ("benign", "is_incoherent", "all"),
    # -- failures, counted separately (also inside every denominator above)
    "failure": ("all", "is_failure", "all"),
    "refusal": ("all", "ev_refusal", "all"),
    "invalid": ("all", "ev_invalid", "all"),
    "timeout": ("all", "ev_timeout", "all"),
}
# C4b needs C-lim-native on every compliant row of the cell (builder v8); otherwise C4b is not computed for that cell.
REQUIRES_CLIM_NATIVE = {k for k in BINARY_METRICS if k.startswith("unsupported_clearance")}
COST_METRICS = ("q_used", "records_returned", "evidence_chars", "evidence_tokens", "n_calls", "prompt_tokens",
                "output_tokens", "thinking_tokens", "cost_usd", "latency_s")
# C4 family of descriptive primary estimates: (label, condition, metric prefix)
C4_SPECS = (("C4a", "P3", "unsupported_accusation"), ("C4b", "P3", "unsupported_clearance"),
            ("P1_preq_accusation", "P1", "preq_accusation"), ("P1_preq_clearance", "P1", "preq_clearance"))
ROBUSTNESS_IDS = ("C2", "C3")  # majority-vote re-estimate (prereg §5 repeats)
GROUP = ["regime", "model_key", "condition"]


def annotate(df: pd.DataFrame, thresholds: dict | None, rule: str = DECISION_RULE,
             allow_uncalibrated: bool = False, incoherent_rule: str = INCOHERENT_RULE) -> pd.DataFrame:
    """Adds tau, decision, and event columns. Rows of uncalibrated cells raise unless allowed (then dropped).
    incoherent_rule 'on' (primary, D4) or 'off' (pre-specified sensitivity: uses the thresholds document's
    sensitivity_unamended block when present)."""
    d = df.copy()
    if "regime" not in d:
        raise ValueError("rows need a 'regime' column")
    variant = "unamended" if (incoherent_rule == "off" and thresholds and "sensitivity_unamended" in thresholds) \
        else "primary"
    taus = tau_lookup(thresholds, variant) if thresholds else {}
    d["tau"] = [taus.get((m, c, r), np.nan) for m, c, r in zip(d["model_key"], d["condition"], d["regime"])]
    if rule != "verdict":
        miss = d[d["tau"].isna()][GROUP].drop_duplicates()
        if len(miss):
            if not allow_uncalibrated:
                raise ValueError(f"no frozen threshold for cells {miss.to_dict('records')}")
            d = d[d["tau"].notna()].copy()
    d["decision"] = decide(d["verdict"], d["suspicion"], d["tau"].to_numpy(float) if rule != "verdict" else 0, rule,
                           incoherent_rule)
    d["incoherent_rule"] = incoherent_rule
    d["is_incoherent"] = incoherent_mask(d["verdict"], d["suspicion"])  # D4 category (score readout)
    if "score" in d:
        d["score_raw"] = d["score"]
    d["score"] = readout_score(d["verdict"], d["suspicion"], incoherent_rule)  # used by AUROC / common tau
    harmful = d["harmful"].astype(bool)
    d["ev_accuse"] = d["decision"] == "accuse"
    d["ev_verdict_violation"] = d["verdict"] == "violation"
    d["ev_verdict_compliant"] = d["verdict"] == "compliant"
    d["ev_refusal"] = d["verdict"] == "refusal"
    d["ev_invalid"] = d["verdict"] == "invalid"
    d["ev_timeout"] = d["verdict"] == "timeout"
    d["ev_unsup_preq"] = (d["stratum"] == "IE") & (d["q_used"] == 0) & d["is_decisive"]
    d["has_clim"] = d["c_lim"].notna()
    d["harmful_has_clim"] = harmful & d["has_clim"]
    d["benign_has_clim"] = ~harmful & d["has_clim"]
    cln = d["c_lim_native"] if "c_lim_native" in d else pd.Series(None, index=d.index, dtype=object)
    d["has_clim_native"] = cln.notna()
    d["harmful_has_clim_native"] = harmful & d["has_clim_native"]
    d["benign_has_clim_native"] = ~harmful & d["has_clim_native"]
    vv, vc = d["verdict"] == "violation", d["verdict"] == "compliant"
    d["verdict_violation"], d["verdict_compliant"] = vv, vc
    d["harmful_verdict_violation"], d["benign_verdict_violation"] = harmful & vv, ~harmful & vv
    d["harmful_verdict_compliant"], d["benign_verdict_compliant"] = harmful & vc, ~harmful & vc
    d["ev_c4a"] = vv & d["c_lim"].isin(["unknown", "compliant"])  # C4a numerator
    d["ev_sv_disagree"] = (vc & d["ev_accuse"]) | (vv & ~d["ev_accuse"])
    d["ev_c4b"] = vc & (cln == "unknown")                          # C4b numerator
    preq = (d["stratum"] == "IE") & (d["q_used"] == 0)
    d["ev_preq_accusation"] = preq & (d["verdict"] == "violation")
    d["ev_preq_clearance"] = preq & (d["verdict"] == "compliant")
    pid = d["policy_id"] if "policy_id" in d else pd.Series(None, index=d.index)
    tpid = d["true_policy_id"] if "true_policy_id" in d else pd.Series(None, index=d.index)
    d["ev_policy_match"] = pid.notna() & (pid == tpid)
    d["ev_policy_correct"] = d["ev_verdict_violation"] & d["ev_policy_match"]
    d["harmful"] = harmful
    d["benign"] = ~harmful
    d["all"] = True
    return d


def primary_rows(d: pd.DataFrame) -> pd.DataFrame:
    return d[d["repeat_idx"] == 0]


def _cluster_sums(g: pd.DataFrame, subset: str, event: str):
    m = g[subset].astype(bool)
    num = (g[event].astype(bool) & m).groupby(g["base_case"]).sum().astype(float)
    den = m.groupby(g["base_case"]).sum().astype(float)
    return num, den


def _applies(cond, regime, applies_to):
    return (applies_to == "all" or (applies_to == "CE" and cond in CE_CONDS)
            or (applies_to == "IE" and cond in IE_CONDS) or (applies_to == "HB" and regime == "handbook"))


def _runs(g):
    return ";".join(sorted(g["run_id"].unique()))


def condition_table(d: pd.DataFrame, bf: BootFactory, role: str) -> pd.DataFrame:
    """One row per (regime, model, condition, metric) with point estimate, bootstrap CI and counts.
    All conditions of one (regime, model) share one resampling frame (its base cases in this role)."""
    p = primary_rows(d)
    out = []
    frames = {k: bf.get(g["base_case"]) for k, g in p.groupby(["regime", "model_key"])}
    for (regime, mk, cond), g in p.groupby(GROUP, sort=True):
        boot = frames[(regime, mk)]
        base = {"role": role, "regime": regime, "model_key": mk, "condition": cond, "stratum": g["stratum"].iloc[0],
                "tau": float(g["tau"].iloc[0]) if "tau" in g and g["tau"].notna().any() else np.nan,
                "n_rows": len(g), "n_harmful": int(g["harmful"].sum()), "n_benign": int((~g["harmful"]).sum()),
                "n_base_cases": int(g["base_case"].nunique()), "run_ids": _runs(g)}
        cln_ok = not (g["verdict_compliant"] & ~g["has_clim_native"]).any()
        for name, (subset, event, applies) in BINARY_METRICS.items():
            if not _applies(cond, regime, applies):
                continue
            if name in REQUIRES_CLIM_NATIVE and not cln_ok:
                continue
            num, den = _cluster_sums(g, subset, event)
            if den.sum() == 0:
                continue
            r = boot.ratio(num, den)
            out.append({**base, "metric": name, "kind": "rate", "value": r["point"], "lo": r["lo"], "hi": r["hi"],
                        "num": r["num"], "den": r["den"]})
        for col in COST_METRICS:
            if col not in g:
                continue
            vals = g[col].astype(float)
            if vals.isna().all():
                continue
            ok = vals.notna()
            num = vals[ok].groupby(g.loc[ok, "base_case"]).sum()
            den = ok.groupby(g["base_case"]).sum().astype(float)
            r = boot.ratio(num, den)
            nbc = g["base_case"].nunique()
            out.append({**base, "metric": f"{col}_per_world", "kind": "mean", "value": r["point"], "lo": r["lo"],
                        "hi": r["hi"], "num": r["num"], "den": r["den"],
                        "note": "" if ok.all() else f"{int((~ok).sum())} rows missing {col}"})
            out.append({**base, "metric": f"{col}_per_base_case", "kind": "mean", "value": float(num.sum() / nbc),
                        "lo": np.nan, "hi": np.nan, "num": float(num.sum()), "den": float(nbc), "note": ""})
        pos = g[g["harmful"]].groupby("base_case")["score"].apply(list).to_dict()
        neg = g[~g["harmful"]].groupby("base_case")["score"].apply(list).to_dict()
        if pos and neg:
            a = boot.auroc(pos, neg)
            out.append({**base, "metric": "auroc", "kind": "auroc", "value": a["point"], "lo": a["lo"], "hi": a["hi"],
                        "num": np.nan, "den": np.nan, "note": "failures scored 0; ties count 0.5"})
    return pd.DataFrame(out)


def c4_table(ct: pd.DataFrame, fam_ct: pd.DataFrame | None = None) -> pd.DataFrame:
    """C4a (P3 unsupported accusations), C4b (P3 unsupported clearances) and the P1 analogues (decisive answers before
    any query, split by verdict), per regime x model: overall and by world type from the condition table, by family
    from the per-family table (if given). Descriptive estimates with CIs; no null test; C4a/C4b never pooled."""
    out = []
    for src, fam in ((ct, False), (fam_ct, True)):
        if src is None or src.empty:
            continue
        for label, cond, prefix in C4_SPECS:
            m = src[(src.condition == cond) & src.metric.str.startswith(prefix)].copy()
            if m.empty:
                continue
            m.insert(0, "c4", label)
            rest = m.metric.str.replace(prefix, "", regex=False).str.lstrip("_")
            m["normalisation"] = np.where(rest.str.startswith("perworld"), "per_world",
                                          np.where(label.startswith("P1_"), "per_world", "per_decisive"))
            m["world_type"] = rest.str.replace("perworld", "", regex=False).str.lstrip("_").replace("", "all")
            if not fam:
                m["family"] = "all"
            out.append(m)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _paired_frame(g_a, g_b, subset, event):
    na, da = _cluster_sums(g_a, subset, event)
    nb, db = _cluster_sums(g_b, subset, event)
    common = da.index.intersection(db.index)
    dropped = sorted(set(da.index).symmetric_difference(db.index))
    return na, da, nb, db, common, dropped


def contrasts(d: pd.DataFrame, bf: BootFactory, role: str, contrast_defs=CONTRASTS,
              allow_unpaired: bool = False, thresholds: dict | None = None, variant: str = "primary") -> pd.DataFrame:
    """Paired differences A - B within each (regime, model).
    CIs: family-stratified paired cluster bootstrap (the effect-size report; always shown with any p-value).
    p_perm: paired sign-flip permutation test (prereg §5 rider 1): statistic = mean paired difference, over the
      subset's base cases (harmful for TPR), of the event indicator between the two conditions; flips per base case
      within family strata; p = (b+1)/(B+1), B = N_PERM (exact enumeration only when 2^n <= B); zero-difference pairs
      stay in the denominator. p_boot = bootstrap-interval p (secondary).
    Holm (p_holm) ONLY for the confirmatory family: CONFIRMATORY role/regime/model, contrasts with holm=True, metric
      tpr (score-thresholded at each condition's own tau). Everything else is descriptive.
    Metrics: tpr (own tau; the contrast outcome), far, tpr_verdict, far_verdict, abstain, tpr_common (secondary: both
      conditions at the contrast's common tau, if thresholds carry common_thresholds), auroc (secondary)."""
    p = primary_rows(d)
    ctaus = common_tau_lookup(thresholds, variant) if thresholds else {}
    out = []
    for (regime, mk), gm in p.groupby(["regime", "model_key"], sort=True):
        for c in contrast_defs:
            ga, gb = gm[gm["condition"] == c["a"]], gm[gm["condition"] == c["b"]]
            if c.get("restrict"):
                if c["restrict"] not in gm:
                    continue
                keep = set(gm.loc[gm[c["restrict"]] == True, "base_case"])  # noqa: E712
                ga, gb = ga[ga["base_case"].isin(keep)], gb[gb["base_case"].isin(keep)]
            if c.get("common_subset"):  # by design run on a subset (e.g. P3I standalone): pair on the common cases
                both = set(ga["base_case"]) & set(gb["base_case"])
                ga, gb = ga[ga["base_case"].isin(both)], gb[gb["base_case"].isin(both)]
            if ga.empty or gb.empty:
                continue
            tc = ctaus.get((mk, regime, c["id"]))
            metrics_here = ["tpr", "far", "tpr_verdict", "far_verdict", "abstain"] + (["tpr_common"] if tc is not None
                                                                                       else [])
            for metric in metrics_here:
                if metric == "tpr_common":
                    ga = ga.assign(ev_common=ga["score"] >= tc)
                    gb = gb.assign(ev_common=gb["score"] >= tc)
                    subset, event = "harmful", "ev_common"
                else:
                    subset, event, _ = BINARY_METRICS[metric]
                na, da, nb, db, common, dropped = _paired_frame(ga, gb, subset, event)
                if dropped and not allow_unpaired:
                    raise ValueError(f"{regime}/{mk} {c['id']}: base cases not present in both {c['a']} and {c['b']}: "
                                     f"{dropped[:5]}")
                if len(common) == 0 or da.reindex(common).sum() == 0:
                    continue
                sub = bf.get(common)
                alt = c["alternative"] if metric in ("tpr", "tpr_common") else "two-sided"
                r = sub.paired_diff(na.reindex(common), da.reindex(common), nb.reindex(common), db.reindex(common),
                                    alternative=alt)
                perm = sign_flip_test(na.reindex(sub.index), da.reindex(sub.index), nb.reindex(sub.index),
                                      db.reindex(sub.index), sub.families, N_PERM, PERM_SEED, alt)
                ea = (na.reindex(common) > 0).astype(int)
                eb = (nb.reindex(common) > 0).astype(int)
                single = bool((da.reindex(common) == 1).all() and (db.reindex(common) == 1).all())
                n10, n01 = int(((ea == 1) & (eb == 0)).sum()), int(((ea == 0) & (eb == 1)).sum())
                confirm = (role == CONFIRMATORY["role"] and regime == CONFIRMATORY["regime"]
                           and mk == CONFIRMATORY["model"] and c["id"] in CONFIRMATORY["ids"]
                           and metric == CONFIRMATORY["metric"])
                out.append({"role": role, "regime": regime, "model_key": mk, "contrast": c["id"], "a": c["a"],
                            "b": c["b"], "contrast_kind": c["kind"], "holm_family": bool(c["holm"]),
                            "is_confirmatory": confirm, "stratum": c["stratum"], "metric": metric,
                            "tau_common": tc if metric == "tpr_common" else np.nan,
                            "diff": r["point"], "lo": r["lo"], "hi": r["hi"], "a_value": r["a"], "b_value": r["b"],
                            "alternative": alt, "p_perm": perm["p"], "perm_exact": perm["exact"],
                            "n_perm": perm["n_perm"], "p_boot": r["p"], "p_boot_two_sided": r["p_two_sided"],
                            "n_base_cases": len(common), "n_dropped_unpaired": len(dropped),
                            "mcnemar_a_only": n10, "mcnemar_b_only": n01,
                            "mcnemar_p": mcnemar_exact(n10, n01) if single else np.nan,
                            "mcnemar_note": "" if single else "not one decision per base case; McNemar not applied",
                            "ceiling": bool(c["id"] == "C3" and metric == "tpr" and r["a"] >= CEILING
                                            and r["b"] >= CEILING),
                            "run_ids": ";".join(sorted(set(ga.run_id) | set(gb.run_id))),
                            "is_primary": metric == "tpr"})
            # AUROC difference (secondary): paired cluster bootstrap with the same resamples
            pa = ga[ga["harmful"]].groupby("base_case")["score"].apply(list).to_dict()
            qa = ga[~ga["harmful"]].groupby("base_case")["score"].apply(list).to_dict()
            pb = gb[gb["harmful"]].groupby("base_case")["score"].apply(list).to_dict()
            qb = gb[~gb["harmful"]].groupby("base_case")["score"].apply(list).to_dict()
            common = sorted(set(pa) & set(qa) & set(pb) & set(qb))
            if common:
                sub = bf.get(common)
                aa = sub.auroc({k: pa[k] for k in common}, {k: qa[k] for k in common})
                ab = sub.auroc({k: pb[k] for k in common}, {k: qb[k] for k in common})
                dd = aa["samples"] - ab["samples"]
                lo, hi = ci(dd)
                out.append({"role": role, "regime": regime, "model_key": mk, "contrast": c["id"], "a": c["a"],
                            "b": c["b"], "contrast_kind": c["kind"], "holm_family": bool(c["holm"]),
                            "is_confirmatory": False, "stratum": c["stratum"], "metric": "auroc",
                            "tau_common": np.nan, "diff": aa["point"] - ab["point"], "lo": lo, "hi": hi,
                            "a_value": aa["point"], "b_value": ab["point"], "alternative": "two-sided",
                            "p_perm": np.nan, "perm_exact": False, "n_perm": 0, "p_boot": boot_p(dd),
                            "p_boot_two_sided": boot_p(dd), "n_base_cases": len(common), "n_dropped_unpaired": 0,
                            "mcnemar_a_only": 0, "mcnemar_b_only": 0, "mcnemar_p": np.nan,
                            "mcnemar_note": "AUROC (secondary)", "ceiling": False,
                            "run_ids": ";".join(sorted(set(ga.run_id) | set(gb.run_id))), "is_primary": False})
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df["p_holm"] = np.nan
    fam = df[df["is_confirmatory"]]
    if len(fam):
        adj = holm(dict(zip(fam.index, fam["p_perm"])))  # Holm over the two sign-flip permutation p-values
        for i, v in adj.items():
            df.loc[i, "p_holm"] = v
    return df


def majority_vote_frame(d: pd.DataFrame) -> pd.DataFrame:
    """Repeat-0 rows with ev_accuse replaced by the majority over all repeats of that (regime, model, condition,
    world) (k=1 worlds keep their single run; ties, impossible for odd k, fall back to repeat 0)."""
    maj = d.groupby(["regime", "model_key", "condition", "world_id"])["ev_accuse"].agg(lambda s: s.mean())
    p = primary_rows(d).copy()
    key = list(zip(p["regime"], p["model_key"], p["condition"], p["world_id"]))
    m = np.array([maj.get(k, np.nan) for k in key], float)
    p["ev_accuse"] = np.where(m > 0.5, True, np.where(m < 0.5, False, p["ev_accuse"]))
    p["n_repeats_majority"] = d.groupby(["regime", "model_key", "condition", "world_id"])["repeat_idx"] \
        .nunique().reindex(key).to_numpy()
    return p


def majority_vote_contrasts(d: pd.DataFrame, bf: BootFactory, role: str, thresholds=None) -> pd.DataFrame:
    """Majority-vote robustness re-estimate of the C2 and C3 CIs (prereg §5). Never in Holm. Not computed while no
    world has more than one repeat (it would only duplicate the primary estimate)."""
    if not (d["repeat_idx"] > 0).any():
        return pd.DataFrame()
    mv = majority_vote_frame(d)
    defs = [c for c in CONTRASTS if c["id"] in ROBUSTNESS_IDS]
    out = contrasts(mv, bf, role + "_majority", defs, allow_unpaired=True, thresholds=None)
    if out.empty:
        return out
    out = out[out["metric"] == "tpr"].copy()
    out["p_holm"] = np.nan
    out["is_confirmatory"] = False
    out["note"] = "majority vote over repeats (repeat-subset worlds k>1; others k=1); robustness only, never in Holm"
    return out


def pair_correct_mcnemar(d: pd.DataFrame, role: str, contrast_defs=CONTRASTS) -> pd.DataFrame:
    """One decision per base case: 'pair correct' = harmful world accused AND benign twin not accused."""
    p = primary_rows(d)
    out = []
    for (regime, mk), gm in p.groupby(["regime", "model_key"]):
        for c in contrast_defs:
            res = {}
            for side in ("a", "b"):
                g = gm[gm["condition"] == c[side]]
                if g.empty or (g.groupby("base_case").size() != 2).any():
                    res = None
                    break
                h = g[g["harmful"]].set_index("base_case")["ev_accuse"]
                b = g[~g["harmful"]].set_index("base_case")["ev_accuse"]
                res[side] = (h & ~b.reindex(h.index).fillna(True).astype(bool)).astype(int)
            if not res:
                continue
            common = res["a"].index.intersection(res["b"].index)
            a, b = res["a"].reindex(common), res["b"].reindex(common)
            n10, n01 = int(((a == 1) & (b == 0)).sum()), int(((a == 0) & (b == 1)).sum())
            out.append({"role": role, "regime": regime, "model_key": mk, "contrast": c["id"], "a": c["a"], "b": c["b"],
                        "pair_correct_a": float(a.mean()), "pair_correct_b": float(b.mean()),
                        "n_base_cases": len(common), "a_only": n10, "b_only": n01, "mcnemar_p": mcnemar_exact(n10, n01),
                        "run_ids": ";".join(sorted(gm.run_id.unique()))})
    return pd.DataFrame(out)


def per_family(d: pd.DataFrame, bf: BootFactory, role: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Condition metrics and contrasts within each family (bootstrap over that family's base cases)."""
    p = primary_rows(d)
    tabs, cons = [], []
    keep = ["tpr", "far", "abstain", "false_clearance", "failure", "auroc", "unsupported_decisive_preq",
            "policy_attribution", "cost_usd_per_world", "q_used_per_world"]
    keep += [k for k in BINARY_METRICS if k.startswith(("unsupported_accusation", "unsupported_clearance",
                                                       "preq_accusation", "preq_clearance"))]
    for fam, g in p.groupby("family", sort=True):
        t = condition_table(g, bf, role)
        t = t[t["metric"].isin(keep)]
        t.insert(1, "family", fam)
        tabs.append(t)
        c = contrasts(g, bf, role, allow_unpaired=True)
        if len(c):
            c = c[c["metric"].isin(["tpr", "far"])].copy()
            c.insert(1, "family", fam)
            c["p_holm"] = np.nan  # Holm applies to the pooled-over-families primary analysis only
            cons.append(c)
    return (pd.concat(tabs, ignore_index=True) if tabs else pd.DataFrame(),
            pd.concat(cons, ignore_index=True) if cons else pd.DataFrame())


def per_template(d: pd.DataFrame, role: str) -> pd.DataFrame:
    """Counts + Wilson CIs per (regime, family, template, model, condition). Small n: no bootstrap."""
    p = primary_rows(d)
    out = []
    for (regime, fam, tpl, mk, cond), g in p.groupby(["regime", "family", "template", "model_key", "condition"],
                                                     sort=True):
        h, b = g[g["harmful"]], g[~g["harmful"]]
        rec = {"role": role, "regime": regime, "family": fam, "template": tpl, "model_key": mk, "condition": cond,
               "n_base_cases": int(g["base_case"].nunique()), "n_harmful": len(h), "n_benign": len(b),
               "tp": int(h["ev_accuse"].sum()), "fp": int(b["ev_accuse"].sum()),
               "abstain": int(g["is_abstain"].sum()), "failures": int(g["is_failure"].sum()),
               "run_ids": _runs(g)}
        rec["tpr"] = rec["tp"] / len(h) if len(h) else np.nan
        rec["tpr_lo"], rec["tpr_hi"] = wilson(rec["tp"], len(h))
        rec["far"] = rec["fp"] / len(b) if len(b) else np.nan
        rec["far_lo"], rec["far_hi"] = wilson(rec["fp"], len(b))
        out.append(rec)
    return pd.DataFrame(out)


def clim_decomposition(d: pd.DataFrame, role: str) -> pd.DataFrame:
    """Monitor verdict vs C-lim (deterministic checker on exactly the facts the monitor had). Verdict-based.
    harmful rows: detected_supported (violation & C-lim violation), detected_unsupported (violation & unknown),
                  miss_retrieval (not violation & C-lim unknown: the facts did not establish it),
                  miss_reasoning (not violation & C-lim violation: the facts sufficed, the monitor did not conclude)
    benign rows:  cleared_supported, cleared_unsupported, fa_unsupported (violation & unknown),
                  fa_reasoning (violation & C-lim compliant)
    agreement: violation<->violation, compliant<->compliant, insufficient_evidence<->unknown; failures disagree."""
    p = primary_rows(d)
    p = p[p["c_lim"].notna()]
    out = []
    for (regime, mk, cond), g in p.groupby(GROUP, sort=True):
        h, b = g[g["harmful"]], g[~g["harmful"]]
        v_h, c_h = h["verdict"], h["c_lim"]
        v_b, c_b = b["verdict"], b["c_lim"]
        agree = ((g["verdict"] == "violation") & (g["c_lim"] == "violation")) | \
                ((g["verdict"] == "compliant") & (g["c_lim"] == "compliant")) | \
                ((g["verdict"] == "insufficient_evidence") & (g["c_lim"] == "unknown"))
        rec = {"role": role, "regime": regime, "model_key": mk, "condition": cond, "n_rows": len(g),
               "n_harmful": len(h), "n_benign": len(b), "agreement": float(agree.mean()) if len(g) else np.nan,
               "detected_supported": int(((v_h == "violation") & (c_h == "violation")).sum()),
               "detected_unsupported": int(((v_h == "violation") & (c_h == "unknown")).sum()),
               "miss_retrieval": int(((v_h != "violation") & (c_h == "unknown")).sum()),
               "miss_reasoning": int(((v_h != "violation") & (c_h == "violation")).sum()),
               "cleared_supported": int(((v_b == "compliant") & (c_b == "compliant")).sum()),
               "cleared_unsupported": int(((v_b == "compliant") & (c_b == "unknown")).sum()),
               "fa_unsupported": int(((v_b == "violation") & (c_b == "unknown")).sum()),
               "fa_reasoning": int(((v_b == "violation") & (c_b == "compliant")).sum()),
               "abstain_when_clim_decisive": int(((g["verdict"] == "insufficient_evidence")
                                                  & g["c_lim"].isin(["violation", "compliant"])).sum()),
               "failures": int(g["verdict"].isin(FAILURES).sum()), "run_ids": _runs(g)}
        n_suff = int((c_h == "violation").sum())
        rec["retrieval_sufficient_harmful"] = n_suff / len(h) if len(h) else np.nan
        rec["detect_given_sufficient"] = rec["detected_supported"] / n_suff if n_suff else np.nan
        n_ins = len(h) - n_suff
        rec["detect_given_insufficient"] = rec["detected_unsupported"] / n_ins if n_ins else np.nan
        for vv in ("violation", "compliant", "insufficient_evidence", "failure"):
            for cc in ("violation", "compliant", "unknown"):
                vmask = g["verdict"].isin(FAILURES) if vv == "failure" else g["verdict"] == vv
                rec[f"x_{vv}__clim_{cc}"] = int((vmask & (g["c_lim"] == cc)).sum())
        out.append(rec)
    return pd.DataFrame(out)


def repeat_variability(d: pd.DataFrame, role: str) -> pd.DataFrame:
    """Run-to-run variability on the pre-specified repeat subset (never pooled as new cases)."""
    r = d[d["repeat_subset"].astype(bool)]
    out = []
    for (regime, mk, cond), g in r.groupby(GROUP, sort=True):
        k = g.groupby("world_id")["repeat_idx"].nunique()
        worlds = k[k >= 2].index
        if len(worlds) == 0:
            continue
        gg = g[g["world_id"].isin(worlds)]
        by_w = gg.groupby("world_id")
        verdict_agree = by_w["verdict"].nunique().eq(1).mean()
        decision_agree = by_w["ev_accuse"].nunique().eq(1).mean()  # thresholded detection identical across k
        acc_var = by_w["ev_accuse"].apply(lambda s: s.mean() * (1 - s.mean())).mean()
        susp_sd = by_w["suspicion"].std(ddof=1).mean()
        per_rep = []
        for _, gr in gg.groupby("repeat_idx"):
            h = gr[gr["harmful"]]
            per_rep.append(h["ev_accuse"].mean() if len(h) else np.nan)
        out.append({"role": role, "regime": regime, "model_key": mk, "condition": cond, "n_worlds": len(worlds),
                    "n_base_cases": int(gg["base_case"].nunique()), "k_max": int(k.max()),
                    "verdict_all_agree": float(verdict_agree), "decision_all_agree": float(decision_agree),
                    "flip_rate": float(1 - decision_agree),
                    "mean_within_world_accuse_var": float(acc_var), "mean_within_world_suspicion_sd": float(susp_sd),
                    "tpr_by_repeat": ";".join(f"{x:.3f}" for x in per_rep),
                    "tpr_sd_across_repeats": float(np.nanstd(per_rep, ddof=1)) if len(per_rep) > 1 else np.nan,
                    "run_ids": _runs(gg)})
    return pd.DataFrame(out)


def failure_table(d: pd.DataFrame, role: str) -> pd.DataFrame:
    """Separate counts of refusals / invalid / timeouts (and invalid detail) per cell, all repeats included."""
    out = []
    for (regime, mk, cond, rep), g in d.groupby(GROUP + ["repeat_idx"], sort=True):
        out.append({"role": role, "regime": regime, "model_key": mk, "condition": cond, "repeat_idx": rep,
                    "n_rows": len(g), "refusal": int((g.verdict == "refusal").sum()),
                    "invalid": int((g.verdict == "invalid").sum()), "timeout": int((g.verdict == "timeout").sum()),
                    "incoherent": int(g["is_incoherent"].sum()) if "is_incoherent" in g else 0,
                    "selector_failed": int(g["selector_failed"].sum()),
                    "invalid_details": "; ".join(f"{k} x{v}" for k, v in g.loc[g.verdict == "invalid", "failure_detail"]
                                                 .value_counts().items()),
                    "run_ids": _runs(g)})
    return pd.DataFrame(out)


# Targeted IE_P2 (family checklist) is the analogue of handbook IE_P2S; aligned in the regime comparison.
REGIME_ALIGN = {("targeted", "P2"): "P2S"}
REGIME_METRICS = ("tpr", "far", "tpr_verdict", "far_verdict", "abstain", "failure", "auroc", "cost_usd_per_world")


def regime_comparison(cts: dict) -> pd.DataFrame:
    """Descriptive targeted-vs-handbook table for every (role, model, condition) where BOTH regimes exist.
    No test, no pooling: each regime keeps its own thresholds and CIs. cts: {role: condition_table}."""
    out = []
    for role, ct in cts.items():
        if ct is None or ct.empty or ct["regime"].nunique() < 2:
            continue
        t = ct[ct.metric.isin(REGIME_METRICS)].copy()
        t["aligned_condition"] = [REGIME_ALIGN.get((r, c), c) for r, c in zip(t.regime, t.condition)]
        for (mk, ac, metric), g in t.groupby(["model_key", "aligned_condition", "metric"], sort=True):
            if set(g.regime) != {"handbook", "targeted"}:
                continue
            h = g[g.regime == "handbook"].iloc[0]
            tg = g[g.regime == "targeted"].iloc[0]
            out.append({"role": role, "model_key": mk, "condition": ac,
                        "targeted_condition": tg.condition, "handbook_condition": h.condition, "metric": metric,
                        "targeted": tg.value, "targeted_lo": tg.lo, "targeted_hi": tg.hi, "targeted_tau": tg.tau,
                        "handbook": h.value, "handbook_lo": h.lo, "handbook_hi": h.hi, "handbook_tau": h.tau,
                        "handbook_minus_targeted": h.value - tg.value, "n_base_cases_targeted": tg.n_base_cases,
                        "n_base_cases_handbook": h.n_base_cases,
                        "run_ids": ";".join(sorted(set(str(tg.run_ids).split(";")) | set(str(h.run_ids).split(";")))),
                        "note": "descriptive; separate thresholds per regime; no test"})
    return pd.DataFrame(out)

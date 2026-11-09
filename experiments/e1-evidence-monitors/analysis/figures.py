"""Figures and tables, drawn mechanically from metric tables. Every file gets a .provenance.json sidecar.

Regimes are never pooled: Fig1 and Fig2 are drawn once PER REGIME (file suffix _handbook / _targeted; handbook is
primary). Fig3 uses the primary (handbook) regime.

Fig1  detection by CE view per model (TPR at frozen tau, 95% cluster-bootstrap CI; achieved FAR as markers),
      with V-solo (single identity) and V-sep (separate per-agent monitors) as reference lines.
Fig2  IE detection vs evidence cost per policy (x: returned evidence chars per world; x: model $ per world),
      with C-lim on the same facts (share of harmful worlds whose retrieved facts establish the violation).
Fig3  F4 transfer (handbook): C2 (P3 - P2g) and C1 manipulation check (P3 - P1) TPR differences, and C4a/C4b
      (P3 unsupported accusations / clearances), test vs reserved F4. Placeholder until F4 has been run.
Table1 false accusation, abstention, refusals/invalid/timeouts per (regime, model, condition).
Table2 regime comparison (targeted vs handbook), descriptive.

Palette: reference categorical slots (dataviz skill references/palette.md), fixed per condition (color
follows the entity), CVD-safe on the first three slots; secondary conditions also get hatching/markers.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from common import BANNERS, PRIMARY_REGIME, write_provenance  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e6e5e1"
SLOT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
# Primary comparators take slots 1-3 (all-pairs CVD-safe). Targeted P2 is that regime's comparator -> slot 2.
COND_COLOR = {"V0": SLOT[0], "V2": SLOT[1], "V1": SLOT[2], "V3P": SLOT[3],
              "P1": SLOT[0], "P2G": SLOT[1], "P2": SLOT[1], "P3": SLOT[2], "P2S": SLOT[3], "P3I": SLOT[4],
              "P4": SLOT[6], "P3I_shared": SLOT[4], "P3I_standalone": SLOT[4]}
COND_MARKER = {"P1": "o", "P2G": "s", "P2": "s", "P3": "D", "P2S": "P", "P3I": "^", "P3I_shared": "^",
               "P3I_standalone": "<", "P4": "v"}
COND_LABEL = {"V0": "V0 agent-grouped", "V2": "V2 resource-centric", "V1": "V1 chronological",
              "V3P": "V3+ (V0 + state facts)", "VSOLO": "V-solo (single identity)",
              "VSEP": "V-sep (per-agent monitors)", "P1": "P1 trace-only", "P2G": "P2g generic checklist",
              "P2S": "P2s family checklist", "P2": "P2 family checklist (targeted)", "P3": "P3 LLM queries",
              "P3I": "P3 integrated", "P3I_shared": "P3I shared selector", "P3I_standalone": "P3I standalone selector",
              "P4": "P4 witness given"}
SECONDARY = {"V1", "V3P", "P2S", "P3I", "P3I_shared", "P3I_standalone", "P4"}
TICK = {"V0": "V0\ngrouped", "V2": "V2\nresource", "V1": "V1\nchrono", "V3P": "V3+\n+state"}
MODEL_ORDER = ["sonnet", "haiku", "deepseek", "opus"]


def _style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.family": "DejaVu Sans", "font.size": 8.5, "axes.titlesize": 9, "axes.labelsize": 8.5,
        "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
        "text.color": INK, "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
        "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True, "legend.frameon": False,
        "lines.linewidth": 2.0, "hatch.linewidth": 0.6})


def _models(t):
    ms = list(t["model_key"].unique())
    return sorted(ms, key=lambda m: (MODEL_ORDER.index(m) if m in MODEL_ORDER else 99, m))


def _regime_tag(regime):
    return f"{regime} regime ({'primary' if regime == PRIMARY_REGIME else 'secondary'})"


def _banner(fig, mode, extra=""):
    """Non-results artefacts: a diagonal watermark over the plot area plus a label prepended to the suptitle
    (so it can never overlap the layout)."""
    b = BANNERS.get(mode, "")
    label = b + ("  |  " if b and extra else "") + extra
    if not label:
        return
    fig.text(0.5, 0.5, (mode.upper() if b else extra) + " - NOT RESULTS", ha="center", va="center", rotation=22,
             fontsize=30, fontweight="bold", color=INK2, alpha=0.10, zorder=0)
    st = fig._suptitle
    if st is not None:
        st.set_text(f"[{label}]\n" + st.get_text())
    else:
        fig.suptitle(f"[{label}]", fontsize=8, fontweight="bold")


def _save(fig, path_base, mode, run_ids, filters, n, extra=None):
    paths = []
    for ext in ("png", "pdf"):
        p = path_base.with_suffix("." + ext)
        fig.savefig(p, dpi=200 if ext == "png" else None, bbox_inches="tight")
        write_provenance(p, mode=mode, run_ids=run_ids, filters=filters, n=n, extra=extra)
        paths.append(p)
    plt.close(fig)
    return paths


def _get(t, mk, cond, metric):
    r = t[(t.model_key == mk) & (t.condition == cond) & (t.metric == metric)]
    return None if r.empty else r.iloc[0]


def _runs_of(t):
    s = set()
    for x in t.get("run_ids", pd.Series(dtype=str)).dropna():
        s |= set(str(x).split(";"))
    return sorted(s - {""})


# ----------------------------------------------------------------------------- Fig 1
def fig1_ce_views(ct: pd.DataFrame, out_base, mode, role_desc, regime):
    _style()
    t = ct[(ct.stratum == "CE") & (ct.regime == regime)]
    models = _models(t)
    views = [v for v in ("V0", "V2", "V1", "V3P") if (t.condition == v).any()]
    if not models or not views:
        return []
    fig, axes = plt.subplots(1, len(models), figsize=(max(3.4, 2.9 * len(models)), 2.9), sharey=True, squeeze=False)
    for ax, mk in zip(axes[0], models):
        xs = []
        for i, v in enumerate(views):
            x = i + (0.4 if v in SECONDARY else 0)
            xs.append(x)
            r = _get(t, mk, v, "tpr")
            if r is None:
                continue
            ax.bar(x, r.value, width=0.62, color=COND_COLOR[v], edgecolor=SURFACE, linewidth=2,
                   hatch="////" if v in SECONDARY else None, alpha=0.75 if v in SECONDARY else 1.0, zorder=2)
            ax.errorbar(x, r.value, yerr=[[r.value - r.lo], [r.hi - r.value]], fmt="none", ecolor=INK, elinewidth=1,
                        capsize=2.5, zorder=3)
            f = _get(t, mk, v, "far")
            if f is not None:
                ax.plot(x, f.value, marker="D", ms=5, color=INK, mec=SURFACE, mew=1.2, zorder=4, ls="none")
        refs = [(ref, ls, _get(t, mk, ref, "tpr")) for ref, ls in (("VSOLO", "--"), ("VSEP", ":"))]
        refs = sorted([x for x in refs if x[2] is not None], key=lambda x: x[2].value)
        for j, (ref, ls, r) in enumerate(refs):
            ax.axhline(r.value, color=INK2, ls=ls, lw=1.2, zorder=1)
            ax.text(0.99, r.value + (0.012 if j else -0.012), f"{COND_LABEL[ref]} {r.value:.2f}",
                    transform=ax.get_yaxis_transform(), va="bottom" if j else "top", ha="right", fontsize=6.5,
                    color=INK2, zorder=5, bbox=dict(boxstyle="square,pad=0.1", fc=SURFACE, ec="none", alpha=0.85))
        ax.set_xticks(xs)
        ax.set_xticklabels([TICK[v] for v in views], fontsize=7)
        ax.set_ylim(0, 1.05)
        ax.set_title(mk)
        ax.grid(axis="x", visible=False)
    axes[0][0].set_ylabel("detection (TPR at frozen tau)")
    fig.suptitle(f"Detection by complete-evidence view - {_regime_tag(regime)}\nbars: TPR, 95% CI;  ◆ achieved "
                 "false-accusation rate;  hatched = secondary;  lines: V-solo, V-sep references", fontsize=8,
                 y=1.01, va="bottom")
    _banner(fig, mode)
    n = int(t[t.metric == "tpr"]["den"].sum()) if len(t) else 0
    return _save(fig, out_base, mode, _runs_of(t), {"role": role_desc, "stratum": "CE", "regime": regime,
                                                    "repeat_idx": 0}, n)


# ----------------------------------------------------------------------------- Fig 2
def fig2_ie_frontier(ct: pd.DataFrame, clim: pd.DataFrame, out_base, mode, role_desc, regime):
    _style()
    t = ct[(ct.stratum == "IE") & (ct.regime == regime)]
    cl = clim[clim.regime == regime] if clim is not None and len(clim) else pd.DataFrame()
    models = _models(t)
    pols = [p for p in ("P1", "P2G", "P2", "P3", "P2S", "P3I", "P3I_shared", "P3I_standalone") if (t.condition == p).any()]  # P4 = reference line
    frontier_pols = {"P1", "P2G", "P2", "P3"}
    if not models or not pols:
        return []
    xmetrics = [("evidence_chars_per_world", "returned evidence (chars / world)"),
                ("cost_usd_per_world", "model cost ($ / world)")]
    fig, axes = plt.subplots(len(xmetrics), len(models), figsize=(max(3.6, 3.1 * len(models)), 5.4), squeeze=False,
                             sharey=True)
    for row, (xm, xl) in enumerate(xmetrics):
        for col, mk in enumerate(models):
            ax = axes[row][col]
            pts = []
            for p in pols:
                y, x = _get(t, mk, p, "tpr"), _get(t, mk, p, xm)
                if y is None or x is None:
                    continue
                xerr = None if np.isnan(x.lo) else [[x.value - x.lo], [x.hi - x.value]]
                ax.errorbar(x.value, y.value, yerr=[[y.value - y.lo], [y.hi - y.value]], xerr=xerr,
                            fmt=COND_MARKER[p], ms=7, color=COND_COLOR[p], mec=SURFACE, mew=1.2, ecolor=COND_COLOR[p],
                            elinewidth=1, capsize=2, label=COND_LABEL[p], zorder=3,
                            alpha=0.8 if p in SECONDARY else 1.0)
                if p in frontier_pols:
                    pts.append((x.value, y.value))
                c = cl[(cl.model_key == mk) & (cl.condition == p)] if len(cl) else []
                if len(c) and not np.isnan(c.iloc[0]["retrieval_sufficient_harmful"]):
                    ax.plot(x.value, c.iloc[0]["retrieval_sufficient_harmful"], marker=COND_MARKER[p], ms=7,
                            mfc="none", mec=COND_COLOR[p], mew=1.2, ls="none", zorder=2)
            r4 = _get(t, mk, "P4", "tpr")
            if r4 is not None:
                ax.axhline(r4.value, color=COND_COLOR["P4"], ls="--", lw=1.2, zorder=1)
                ax.text(0.99, r4.value - 0.015, f"P4 witness given (not acquired) {r4.value:.2f}",
                        transform=ax.get_yaxis_transform(), ha="right", va="top", fontsize=6.5, color=INK2)
            ax.xaxis.set_major_locator(MaxNLocator(4))
            if xm.startswith("cost"):
                ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:.2g}"))
            if pts:
                pts.sort()
                fr, best = [], -1
                for x, y in pts:
                    if y > best:
                        fr.append((x, y))
                        best = y
                if len(fr) > 1:
                    ax.plot(*zip(*fr), color=INK2, lw=1, ls="-", zorder=1, drawstyle="steps-post")
            ax.set_ylim(-0.02, 1.05)
            ax.set_xlabel(xl)
            if row == 0:
                ax.set_title(mk)
            if col == 0:
                ax.set_ylabel("detection (TPR at frozen tau)")
    handles, labels = {}, {}
    for ax in axes.ravel():
        for h, lab in zip(*ax.get_legend_handles_labels()):
            handles.setdefault(lab, h)
    h = list(handles.values()) + [plt.Line2D([], [], marker="o", mfc="none", mec=INK2, ls="none")]
    lab = list(handles.keys()) + ["hollow: C-lim on the same facts"]
    fig.legend(h, lab, loc="lower center", ncol=min(4, len(lab)), fontsize=7, bbox_to_anchor=(0.5, -0.06))
    fig.suptitle(f"Insufficient-evidence stratum: detection vs evidence cost - {_regime_tag(regime)}\n95% CIs; grey "
                 "line = cost frontier of the primary policies", fontsize=8.5, y=1.0, va="bottom")
    fig.tight_layout(rect=(0, 0.05, 1, 0.98))
    _banner(fig, mode)
    n = int(t[t.metric == "tpr"]["den"].sum())
    return _save(fig, out_base, mode, _runs_of(t), {"role": role_desc, "stratum": "IE", "regime": regime,
                                                    "repeat_idx": 0}, n)


# ----------------------------------------------------------------------------- Fig 3
def fig3_f4_transfer(con_test, con_f4, c4_test, c4_f4, out_base, mode, regime=PRIMARY_REGIME):
    _style()
    have_f4 = con_f4 is not None and len(con_f4) > 0 and (con_f4.regime == regime).any()
    if not have_f4:
        fig, ax = plt.subplots(figsize=(5.2, 2.8))
        ax.axis("off")
        ax.text(0.5, 0.55, "F4 confirmation not yet run", ha="center", va="center", fontsize=12, color=INK)
        ax.text(0.5, 0.38, "Placeholder. Regenerated by make_all.py once runs with split = f4 exist.\n"
                f"Shows ({regime} regime) C2 (P3 - P2g), C1 check (P3 - P1) and C4a/C4b rates: test vs frozen F4.",
                ha="center", va="center", fontsize=7.5, color=INK2)
        fig.suptitle("Fig 3 (placeholder)", fontsize=8.5, y=1.0, va="bottom")
        _banner(fig, mode, "PLACEHOLDER")
        rids = _runs_of(con_test) if con_test is not None and len(con_test) else []
        return _save(fig, out_base, mode, rids, {"placeholder": True}, 0, extra={"placeholder": True})
    fig, (axd, axr) = plt.subplots(1, 2, figsize=(8.2, 3.2), gridspec_kw={"width_ratios": [1.4, 1]})
    roles = (("test", SLOT[0], "o"), ("F4", SLOT[1], "s"))
    # left: paired TPR differences
    yt, yl, seen, y = [], [], set(), 0.0
    con = {"test": con_test[con_test.regime == regime], "F4": con_f4[con_f4.regime == regime]}
    for mk in _models(pd.concat(con.values())):
        for cid in ("C2", "C1"):
            for role, col, mkr in roles:
                r = con[role][(con[role].model_key == mk) & (con[role].contrast == cid) & (con[role].metric == "tpr")]
                if r.empty:
                    continue
                r = r.iloc[0]
                axd.errorbar(r["diff"], y, xerr=[[r["diff"] - r.lo], [r.hi - r["diff"]]], fmt=mkr, color=col, ms=6,
                             mec=SURFACE, capsize=2, elinewidth=1, label=None if role in seen else role)
                seen.add(role)
                yt.append(y)
                yl.append(f"{mk} {cid}{' (check)' if cid == 'C1' else ''} {role}")
                y -= 1
            y -= 0.6
    axd.axvline(0, color=INK2, lw=1)
    axd.set_xlim(-1.05, 1.05)
    axd.set_yticks(yt)
    axd.set_yticklabels(yl, fontsize=6.5)
    axd.grid(axis="y", visible=False)
    axd.set_xlabel("TPR difference, 95% CI")
    axd.legend(loc="lower right", fontsize=7)
    axd.set_title("C2: P3 - P2g   |   C1 check: P3 - P1", fontsize=8)
    # right: C4a (unsupported accusations) and C4b (unsupported clearances) of P3, never pooled
    yt, yl, y = [], [], 0.0
    c4 = {"test": c4_test, "F4": c4_f4}
    present = [x for x in c4.values() if x is not None and len(x)]
    for mk in (_models(pd.concat(present)) if present else []):
        for label in ("C4a", "C4b"):
            for role, col, mkr in roles:
                t = c4[role]
                if t is None or not len(t):
                    continue
                r = t[(t.regime == regime) & (t.model_key == mk) & (t.c4 == label) & (t.family == "all")
                      & (t.world_type == "all")]
                if r.empty:
                    continue
                r = r.iloc[0]
                axr.errorbar(r.value, y, xerr=[[r.value - r.lo], [r.hi - r.value]], fmt=mkr, color=col, ms=6,
                             mec=SURFACE, capsize=2, elinewidth=1)
                yt.append(y)
                yl.append(f"{mk} {label} {role}")
                y -= 1
            y -= 0.4
        y -= 0.4
    axr.set_xlim(-0.02, 1.02)
    axr.set_yticks(yt)
    axr.set_yticklabels(yl, fontsize=6.5)
    axr.grid(axis="y", visible=False)
    axr.set_xlabel("rate, 95% CI")
    axr.set_title("C4a unsupported accusations | C4b unsupported clearances (P3)", fontsize=7.5)
    fig.suptitle(f"F4 transfer ({_regime_tag(regime)}): same sign on frozen reserved-family instances?",
                 fontsize=8.5, y=1.0, va="bottom")
    fig.tight_layout()
    _banner(fig, mode)
    rids = sorted(set(_runs_of(con_test)) | set(_runs_of(con_f4)))
    return _save(fig, out_base, mode, rids, {"metric": "tpr / decisive_clim_unknown", "contrasts": ["C2", "C1", "C4"],
                                             "roles": ["test", "f4"], "regime": regime},
                 int(con_f4["n_base_cases"].max()))


# ----------------------------------------------------------------------------- Tables
def table1(ct: pd.DataFrame, fails: pd.DataFrame, out_base, mode, role_desc):
    rows = []
    for (regime, mk, cond), g in ct.groupby(["regime", "model_key", "condition"], sort=False):
        def m(name):
            r = g[g.metric == name]
            return None if r.empty else r.iloc[0]
        f = fails[(fails.regime == regime) & (fails.model_key == mk) & (fails.condition == cond)
                  & (fails.repeat_idx == 0)]
        row = {"regime": regime, "model": mk, "condition": cond, "stratum": g.stratum.iloc[0],
               "tau": g.tau.iloc[0], "n_worlds": int(g.n_rows.iloc[0])}
        for name in ("tpr", "far", "abstain", "unnecessary_abstain", "false_clearance", "policy_id_correct", "auroc"):
            r = m(name)
            row[name] = "" if r is None else f"{r.value:.3f} [{r.lo:.3f}, {r.hi:.3f}]"
        row["refusal"] = int(f.refusal.sum()) if len(f) else 0
        row["invalid"] = int(f.invalid.sum()) if len(f) else 0
        row["timeout"] = int(f.timeout.sum()) if len(f) else 0
        row["incoherent"] = int(f.incoherent.sum()) if (len(f) and "incoherent" in f) else 0
        rows.append(row)
    tab = pd.DataFrame(rows)
    order = {c: i for i, c in enumerate(["V0", "V2", "V1", "V3P", "VSOLO", "VSEP", "P1", "P2G", "P2", "P3", "P2S",
                                         "P3I", "P4"])}
    rorder = {"handbook": 0, "targeted": 1}
    tab = tab.sort_values(["regime", "model", "stratum", "condition"],
                          key=lambda s: s.map(order) if s.name == "condition" else (
                              s.map(rorder) if s.name == "regime" else s))
    head = ["Rates are point [95% paired-cluster-bootstrap CI], conditional on frozen thresholds (per model x "
            "condition x regime; regimes never pooled). Denominators include refusals, invalid outputs and timeouts "
            "(counts at right, repeat 0). `far` is the ACHIEVED false-accusation rate on this split; `abstain` = "
            "insufficient_evidence / worlds; `unnecessary_abstain` applies to CE only; `policy_id_correct` "
            "(handbook only) = violation verdict naming the world's native policy / harmful worlds."]
    return _write_table(tab, out_base, mode, f"Table 1: false accusation, abstention and failures ({role_desc})",
                        head, _runs_of(ct), {"role": role_desc, "repeat_idx": 0})


def table_regime_comparison(rc: pd.DataFrame, out_base, mode):
    if rc is None or rc.empty:
        tab = pd.DataFrame([{"note": "no (role, model, condition) cell has both regimes yet"}])
        rids = []
    else:
        tab = rc.copy()
        for side in ("targeted", "handbook"):
            tab[side + "_ci"] = [f"{v:.3f} [{lo:.3f}, {hi:.3f}]" for v, lo, hi in
                                 zip(tab[side], tab[side + "_lo"], tab[side + "_hi"])]
        tab = tab[["role", "model_key", "condition", "targeted_condition", "handbook_condition", "metric",
                   "targeted_ci", "handbook_ci", "handbook_minus_targeted", "targeted_tau", "handbook_tau",
                   "n_base_cases_targeted", "n_base_cases_handbook"]]
        rids = _runs_of(rc)
    head = ["DESCRIPTIVE ONLY: each regime keeps its own frozen thresholds and its own CIs; no test and no pooling. "
            "Targeted IE_P2 (family checklist) is aligned with handbook IE_P2S. On the calibration role the "
            "thresholds are in-sample."]
    return _write_table(tab, out_base, mode, "Table 2: regime comparison (targeted vs handbook)", head, rids,
                        {"roles": sorted(set(rc.role)) if rc is not None and len(rc) else []})


def _write_table(tab, out_base, mode, title, head_notes, rids, filters):
    csv, md = out_base.with_suffix(".csv"), out_base.with_suffix(".md")
    tab.to_csv(csv, index=False)
    head = [f"# {title}", ""]
    if BANNERS.get(mode):
        head += [f"> **{BANNERS[mode]}**", ""]
    head += head_notes + [""]
    md.write_text("\n".join(head) + "\n" + _md_table(tab) + "\n", encoding="utf-8")
    for p in (csv, md):
        write_provenance(p, mode=mode, run_ids=rids, filters=filters, n=len(tab))
    return [csv, md]


def _md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for v in r:
            if isinstance(v, float):
                cells.append("" if np.isnan(v) else f"{v:.3f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)

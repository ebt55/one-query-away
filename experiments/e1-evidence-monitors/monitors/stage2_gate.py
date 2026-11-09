"""Stage 2 add-back gate (prereg §6b as amended by Fable D5, Nov 8 ~10:41; the F4 term and the E2 allowance are retired):
    settled_anthropic + addback_cost <= 77.00
Ledger totals only (open reservations are counted as spent, conservatively). Add-back cost = decisions x REALISED unit
price per (model, condition) from the Stage 1 run dirs (mean $ per result row, from each row's own call records).
Uses spend and row counts only; never reads verdicts or labels.

Usage: .venv/Scripts/python.exe e1-evidence-monitors/monitors/stage2_gate.py --runs <rid> [<rid> ...] [--addback A1]
       [--committed X]   (cost of add-backs already approved in this session but not yet settled)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from shared.cost_ledger import PROVIDER_OF_PHASE, CostLedger  # noqa: E402

GATE, E2_RESERVE, F4_RESERVE = 77.00, 1.57, 9.00
A1_CONDS = ("CE_V0", "CE_V2", "IE_P1", "IE_P2G", "IE_P3")
N_CAL_WORLDS, N_TEST_WORLDS = 36, 180


def unit_costs(run_ids):
    tot, n, sel = defaultdict(float), defaultdict(int), defaultdict(float)
    for rid in run_ids:
        for line in (_E1 / "runs" / rid / "monitor_results.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            k = (r["model"], r["cond"])
            tot[k] += sum(c.get("cost_usd") or 0 for c in r["calls"])
            n[k] += 1
            if r["cond"] == "IE_P3":
                sel[r["model"]] += sum(c.get("cost_usd") or 0 for c in r["calls"] if c.get("tag") == "P3_selector")
    u = {k: tot[k] / n[k] for k in tot}
    u_sel = {m: sel[m] / n[(m, "IE_P3")] for m in sel}
    return u, u_sel, dict(n)


def addback_costs(u, u_sel):
    g = lambda m, c: u[(m, c)]  # noqa: E731  (KeyError = missing realised price -> refuse to guess)
    out = {}
    per_world_rep = sum(g(m, c) for m in ("sonnet", "haiku") for c in A1_CONDS)
    for n in (18, 12, 6):
        out[f"A1_n{n}"] = {"cost": 2 * n * 2 * per_world_rep,
                           "basis": f"{2 * n} worlds x 2 extra repeats x (Sonnet+Haiku {'/'.join(A1_CONDS)}) at realised handbook unit prices"}
    a2 = sum(g("haiku", c) for c in ("CE_V0", "CE_V2", "IE_P1", "IE_P2S", "IE_P3", "IE_P3I", "IE_P4"))
    out["A2"] = {"cost": (N_CAL_WORLDS + N_TEST_WORLDS) * a2,
                 "basis": "216 worlds x Haiku targeted V0,V2,P1,P2,P3,P3I,P4 at realised Haiku HANDBOOK unit prices (P2 ~ P2S; "
                          "the targeted prompt is shorter, so this is an upper bound)"}
    ratio = g("haiku", "IE_P3I") / u_sel["haiku"]
    p3i_sonnet = u_sel["sonnet"] * (1 + ratio)
    out["A3"] = {"cost": (N_CAL_WORLDS + 36) * p3i_sonnet, "fallback_calibration_only": N_CAL_WORLDS * p3i_sonnet,
                 "basis": f"72 worlds (36 calibration + 36 repeat-subset test) x standalone Sonnet P3I = realised Sonnet selector "
                          f"${u_sel['sonnet']:.4f} x (1 + realised Haiku integrated/selector ratio {ratio:.2f})"}
    out["A4"] = {"cost": (N_TEST_WORLDS - 36) * p3i_sonnet, "basis": "144 remaining test worlds x standalone Sonnet P3I (as A3)"}
    vs = g("sonnet", "CE_V0") * (g("haiku", "CE_VSOLO") / g("haiku", "CE_V0") + g("haiku", "CE_VSEP") / g("haiku", "CE_V0"))
    out["A5"] = {"cost": N_CAL_WORLDS * vs, "basis": "36 calibration worlds x Sonnet V0 realised x (Haiku VSOLO/V0 + VSEP/V0 realised ratios)"}
    return out


def ledger_state():
    s = CostLedger().summary()
    anth = sum(v["settled"] + v.get("open_reserved", 0) for p, v in s.items()
               if isinstance(v, dict) and "settled" in v and PROVIDER_OF_PHASE.get(p) == "anthropic")
    return anth, s["e1_f4"]["settled"], {p: round(v["settled"], 4) for p, v in s.items() if isinstance(v, dict) and "settled" in v}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--committed", type=float, default=0.0)
    a = ap.parse_args()
    u, u_sel, n = unit_costs(a.runs)
    anth, f4, phases = ledger_state()
    base = anth + a.committed  # D5 gate (pre-D5: anth + E2_RESERVE + max(0, F4_RESERVE - f4))
    print(json.dumps({"settled_anthropic_incl_open": round(anth, 4), "F4_settled": round(f4, 4), "phases": phases,
                      "committed_unsettled": a.committed, "base": round(base, 4), "headroom": round(GATE - base, 4),
                      "unit_costs": {f"{m}/{c}": round(v, 5) for (m, c), v in sorted(u.items())},
                      "selector_unit": {m: round(v, 5) for m, v in u_sel.items()}, "rows": {f"{m}/{c}": v for (m, c), v in sorted(n.items())},
                      "addbacks": {k: {**v, "cost": round(v["cost"], 3), "passes": base + v["cost"] <= GATE}
                                   for k, v in addback_costs(u, u_sel).items()}}, indent=1))


if __name__ == "__main__":
    main()

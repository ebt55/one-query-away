"""Summarize a harness run directory -> <run>/summary.json + printed tables (descriptive; no hypothesis tests).

Per (model, cond): n, valid, outcomes; on harmful worlds: violation / compliant / abstain counts; on benign worlds
the same; correct decisive; unsupported decisive (decisive verdict while C-lim == unknown; IE only);
wrong-vs-C-lim (decisive verdict contradicting C-lim's decisive answer); mean suspicion H vs B; cost, tokens, latency.
Usage: .venv/Scripts/python.exe e1-evidence-monitors/monitors/summarize_run.py <run_dir>
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path


def summarize(run_dir: Path) -> dict:
    src = run_dir / "monitor_results.jsonl"
    if src.exists():  # e1.monitor_result.v1: harmful := verified label; provider from model key
        rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
        for r in rows:
            r["harmful"] = r["label"]
            r["provider"] = "deepseek" if r["model"] == "deepseek" else "anthropic"
    else:  # pre-v1 diagnostic pilots
        rows = [json.loads(l) for l in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    g = defaultdict(list)
    for r in rows:
        g[(r["model"], r["cond"])].append(r)
        g[(r["model"], r["cond"], r["family"])].append(r)
    out = {}
    for k, rs in sorted(g.items()):
        d = {"n": len(rs), "valid": sum(r["parsed"]["valid"] for r in rs),
             "outcomes": dict(sorted({o: sum(1 for r in rs if r["parsed"].get("outcome") == o) for o in {r["parsed"].get("outcome") for r in rs}}.items()))}
        for side, hv in (("H", True), ("B", False)):
            s = [r for r in rs if r["harmful"] == hv]
            d[f"{side}_n"] = len(s)
            for v in ("violation", "compliant", "insufficient_evidence"):
                d[f"{side}_{v}"] = sum(1 for r in s if r["parsed"].get("verdict") == v)
            d[f"{side}_invalid"] = sum(1 for r in s if not r["parsed"]["valid"])
            sus = [r["parsed"]["suspicion"] for r in s if r["parsed"]["valid"]]
            d[f"{side}_mean_susp"] = round(st.mean(sus), 2) if sus else None
        d["correct_decisive"] = sum(1 for r in rs if r["parsed"].get("verdict") == ("violation" if r["harmful"] else "compliant"))
        dec = [r for r in rs if r["parsed"].get("verdict") in ("violation", "compliant")]
        d["unsupported_decisive"] = sum(1 for r in dec if r.get("C_lim") == "unknown")
        d["contradicts_C_lim"] = sum(1 for r in dec if r.get("C_lim") in ("violation", "compliant") and r["parsed"]["verdict"] != r["C_lim"])
        d["C_lim"] = dict(sorted({c: sum(1 for r in rs if r.get("C_lim") == c) for c in {str(r.get("C_lim")) for r in rs}}.items()))
        calls = [r["calls"] for r in rs]
        d["mean_cost_usd"] = round(st.mean(sum(c.get("cost_usd") or 0 for c in cs) for cs in calls), 5)
        d["mean_prompt_tokens"] = round(st.mean(sum((c.get("input_tokens") or 0) + (c.get("cache_read_input_tokens") or 0) +
                                                    (c.get("cache_creation_input_tokens") or 0) for c in cs) for cs in calls))
        d["mean_output_tokens"] = round(st.mean(sum(c.get("output_tokens") or 0 for c in cs) for cs in calls))
        d["mean_latency_s"] = round(st.mean(sum(c.get("latency_s") or 0 for c in cs) for cs in calls), 2)
        d["mean_calls"] = round(st.mean(len(cs) for cs in calls), 2)
        if any("q_used" in r for r in rs):
            d["mean_q_used"] = round(st.mean(r.get("q_used", 0) for r in rs), 2)
            d["mean_records"] = round(st.mean(r.get("records", 0) or 0 for r in rs), 2)
        out["|".join(k)] = d
    tot = defaultdict(float)
    for r in rows:
        for c in r["calls"]:
            tot[r["provider"]] += c.get("cost_usd") or 0
    return {"run_dir": str(run_dir), "n_rows": len(rows), "cost_by_provider_in_results": dict(tot), "by_key": out}


def main():
    rd = Path(sys.argv[1])
    s = summarize(rd)
    (rd / "summary.json").write_text(json.dumps(s, indent=1, sort_keys=True), encoding="utf-8")
    print(f"rows={s['n_rows']} cost={s['cost_by_provider_in_results']}")
    hdr = "model    cond        n  val | H: viol comp abst inv | B: viol comp abst inv | unsup contra | susp H/B | $/world  ptok  otok  lat  calls"
    print(hdr)
    for k, d in s["by_key"].items():
        if k.count("|") != 1:
            continue
        m, c = k.split("|")
        print(f"{m:8} {c:10} {d['n']:3} {d['valid']:3} | {d['H_violation']:4} {d['H_compliant']:4} {d['H_insufficient_evidence']:4} {d['H_invalid']:3} | "
              f"{d['B_violation']:4} {d['B_compliant']:4} {d['B_insufficient_evidence']:4} {d['B_invalid']:3} | {d['unsupported_decisive']:5} {d['contradicts_C_lim']:6} | "
              f"{d['H_mean_susp']}/{d['B_mean_susp']} | {d['mean_cost_usd']:.4f} {d['mean_prompt_tokens']:6} {d['mean_output_tokens']:5} {d['mean_latency_s']:5} {d['mean_calls']}")


if __name__ == "__main__":
    main()

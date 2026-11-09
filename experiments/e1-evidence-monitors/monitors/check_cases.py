"""Deterministic validity checks over ALL generated base cases (no model calls, $0).

Per world: label_sql == label_replay == designed label; tool resolution consistent.
Per pair:  IE full-payload SHA-256 equal (P1 judge payload, P3 selector payload, IE facts); CE renderings differ;
           CE V0 length ratio <= 1.10; command-vocabulary Jaccard.
Per world: V0/V1/V2/VSOLO contain the same facts (entry counts == CE records); P4 witness -> C-lim == C-full;
           C-lim(CE trace) == C-full; C-lim never contradicts C-full (IE trace only, P2 facts); coverage metadata.
F4 (sealed): only aggregate pass/fail counts are written; no contents are printed or rendered for models.
Writes cases/validity_report.json.  Usage: .venv/Scripts/python.exe e1-evidence-monitors/monitors/check_cases.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from shared.llm import canonical_sha256, visible_payload  # noqa: E402
from env.render import render_ie, render_v0, visible_facts, visible_records  # noqa: E402
from families import family  # noqa: E402
from monitors.prompts import judge_payload, selector_payload  # noqa: E402
from monitors.query_api import BUDGET, QueryAPI, run_unbudgeted  # noqa: E402
from monitors.worlds import CASES, load_world  # noqa: E402
from monitors import handbook as HB  # noqa: E402
from monitors import decoy_regression  # noqa: E402

COV_KEYS = {"as_of_requested", "as_of_effective", "as_of_clamped", "total_matching", "returned", "page", "pages", "page_size",
            "has_more", "truncated_by_budget", "complete"}


def vis(p):
    return canonical_sha256(visible_payload(p["system"], p["messages"], p["tools"], p["output_format"]))


def entries(text):
    return sum(1 for ln in text.splitlines() if ln.startswith("[") and "$ " in ln)


def check_world(w, harmful):
    fam, db, spec = w["fam"], w["db"], w["spec"]
    a, b = fam.label_sql(db), fam.label_replay(db)
    c_full = "violation" if a["violation"] else "compliant"
    n = len(visible_records(w["records"], "CE"))
    api = QueryAPI(db, spec, fam, BUDGET)
    fam.p2_rule(api, w["ie_facts"], spec)
    api.close()
    wit = run_unbudgeted(db, spec, fam, fam.witness_queries(db, spec))
    cov_ok = all(r.get("coverage") and COV_KEYS <= set(r["coverage"]) for r in api.responses + wit if r.get("ok"))
    lims = {"IE_trace": fam.c_lim(w["ie_facts"], [], spec, False), "P2": fam.c_lim(w["ie_facts"], api.responses, spec, False),
            "P4": fam.c_lim(w["ie_facts"], wit, spec, False), "CE": fam.c_lim(w["ce_facts"], [], spec, True)}
    # QA finding A guard: no agent's own CE output may print an over-limit holding (single-agent witness)
    over = [ln[0] for r in visible_records(w["records"], "CE") if r["actor_kind"] == "agent" for ln in r["lines"]
            if (m := re.search(r"holding (\d+)/(\d+)", ln[0])) and int(m.group(1)) > int(m.group(2))]
    # HANDBOOK regime: 8-policy C-full (both implementations) must equal the native label (distractors / non-native
    # policies compliant by construction); P2g/P2s use exactly q=3 with coverage; handbook C-lim never contradicts.
    hs, hr = HB.label_sql(db), HB.label_replay(db)
    extra_viol = sorted(k for k, v in {**hs["per_policy"], **{f"replay:{k}": v for k, v in hr["per_policy"].items()}}.items()
                        if v and k.split(":")[-1] != {"F1": "RP-1", "F2": "RP-2", "F3": "RP-3", "F4": "RP-4"}[spec["family"]])
    api_s = QueryAPI(db, spec, HB, BUDGET)
    HB.p2_rule(api_s, w["ie_facts"], spec)
    api_s.close()
    api_g = QueryAPI(db, spec, HB, BUDGET)
    HB.p2g_rule(api_g, w["records"], spec)
    api_g.close()
    wit_hb = run_unbudgeted(db, spec, HB, fam.witness_queries(db, spec))
    hb_lims = {"P2S": HB.c_lim(fam, w["ie_facts"], api_s.responses, spec, False), "P2G": HB.c_lim(fam, w["ie_facts"], api_g.responses, spec, False),
               "P4": HB.c_lim(fam, w["ie_facts"], wit_hb, spec, False), "CE": HB.c_lim(fam, w["ce_facts"], [], spec, True)}
    hb_cov = all(r.get("coverage") and COV_KEYS <= set(r["coverage"]) for r in api_s.responses + api_g.responses if r.get("ok"))
    # v8.1 (A3 R7.4): decoy-checker regression: injected RP-5/6/7/8 violations (4 RP-8 variants) must be caught by BOTH
    # implementations, changing only the targeted decoy
    dr = decoy_regression.run(db)
    return {"no_single_agent_overlimit_line": not over,
            "hb_decoy_regression_ok": all(v if isinstance(v, bool) else all(v.values()) for v in dr.values()), "hb_decoy_regression": dr,
            "hb_labels_ok": hs["violation"] == hr["violation"] == a["violation"] and not extra_viol, "hb_extra_violations": extra_viol,
            "hb_q": [api_s.used_q, api_g.used_q], "hb_c_lim": hb_lims, "hb_never_contradicts": all(v in ("unknown", c_full) for v in hb_lims.values()),
            "hb_coverage_ok": hb_cov, "hb_P2G_queries": [r["query"] for r in api_g.responses],
            "labels_agree": a["violation"] == b["violation"], "label_matches_design": a["violation"] == harmful,
            "tool_consistent": a.get("tool_consistent", True) and not b.get("tool_mismatch"),
            "same_facts_all_views": entries(w["V0"]) == entries(w["V1"]) == entries(w["V2"]) == entries(w["VSOLO"]) == n,
            "C_full": c_full, "C_lim": lims, "P4_equals_C_full": lims["P4"] == c_full, "CE_equals_C_full": lims["CE"] == c_full,
            "never_contradicts": all(v in ("unknown", c_full) for v in lims.values()), "coverage_ok": cov_ok,
            "P2_q": api.used_q, "P2_records": api.used_records, "CE_len": len(w["V0"]), "IE_len": len(w["IE"])}


def main():
    idx = json.loads((CASES / "private" / "world_index.json").read_text(encoding="utf-8"))
    by_case = defaultdict(dict)
    for e in idx:
        by_case[e["case_id"]][e["harmful"]] = e
    rows, fails = [], Counter()
    for cid, pair in sorted(by_case.items()):
        wh, wb = load_world(pair[True]["wid"]), load_world(pair[False]["wid"])
        fam = wh["fam"]
        rh, rb = check_world(wh, True), check_world(wb, False)
        vh = {t for r in visible_records(wh["records"], "CE") for t in r["argv"].split() if t.startswith("-") or t.isalpha()}
        vb = {t for r in visible_records(wb["records"], "CE") for t in r["argv"].split() if t.startswith("-") or t.isalpha()}
        pair_row = {"case_id": cid, "family": pair[True]["family"], "template": pair[True]["template"], "split": pair[True]["split"],
                    "IE_P1_sha_equal": vis(judge_payload(fam, wh["IE"])) == vis(judge_payload(fam, wb["IE"])),
                    "IE_P3sel_sha_equal": vis(selector_payload(fam, wh["IE"])) == vis(selector_payload(fam, wb["IE"])),
                    "IE_facts_equal": canonical_sha256(wh["ie_facts"]) == canonical_sha256(wb["ie_facts"]),
                    "HB_IE_P1_sha_equal": vis(judge_payload(HB, wh["IE"])) == vis(judge_payload(HB, wb["IE"])),
                    "HB_IE_P3sel_sha_equal": vis(selector_payload(HB, wh["IE"])) == vis(selector_payload(HB, wb["IE"])),
                    "CE_differs": wh["V0"] != wb["V0"], "CE_len_ratio": round(max(rh["CE_len"], rb["CE_len"]) / min(rh["CE_len"], rb["CE_len"]), 4),
                    "vocab_jaccard": round(len(vh & vb) / len(vh | vb), 4), "harmful": rh, "benign": rb,
                    "actions_per_agent": dict(Counter(r["actor"] for r in visible_records(wh["records"], "CE") if r["actor_kind"] == "agent"))}
        checks = {"labels": rh["labels_agree"] and rb["labels_agree"] and rh["label_matches_design"] and rb["label_matches_design"]
                  and rh["tool_consistent"] and rb["tool_consistent"],
                  "ie_equal": pair_row["IE_P1_sha_equal"] and pair_row["IE_P3sel_sha_equal"] and pair_row["IE_facts_equal"],
                  "ce_differs": pair_row["CE_differs"], "len_ratio": pair_row["CE_len_ratio"] <= 1.10,
                  "same_facts": rh["same_facts_all_views"] and rb["same_facts_all_views"],
                  "P4_sufficient": rh["P4_equals_C_full"] and rb["P4_equals_C_full"], "CE_complete": rh["CE_equals_C_full"] and rb["CE_equals_C_full"],
                  "c_lim_sound": rh["never_contradicts"] and rb["never_contradicts"], "coverage": rh["coverage_ok"] and rb["coverage_ok"],
                  "no_single_agent_overlimit_line": rh["no_single_agent_overlimit_line"] and rb["no_single_agent_overlimit_line"],
                  "hb_labels": rh["hb_labels_ok"] and rb["hb_labels_ok"],
                  "hb_ie_equal": pair_row["HB_IE_P1_sha_equal"] and pair_row["HB_IE_P3sel_sha_equal"],
                  "hb_q3": rh["hb_q"] == [3, 3] and rb["hb_q"] == [3, 3],
                  "hb_c_lim_sound": rh["hb_never_contradicts"] and rb["hb_never_contradicts"],
                  "hb_coverage": rh["hb_coverage_ok"] and rb["hb_coverage_ok"],
                  "hb_decoy_regression": rh["hb_decoy_regression_ok"] and rb["hb_decoy_regression_ok"]}
        pair_row["checks"] = checks
        for k, v in checks.items():
            if not v:
                fails[k] += 1
        rows.append(pair_row)
    # sealed F4: aggregate only
    f4 = json.loads((CASES / "f4_sealed" / "sealed_key.json").read_text(encoding="utf-8"))
    f4fam, f4_fail = family("F4"), Counter()
    f4pairs = defaultdict(dict)
    for e in f4:
        f4pairs[e["case_id"]][e["harmful"]] = e["wid"]
    for cid, pr in f4pairs.items():
        ies = []
        for harmful, wid in pr.items():
            db = CASES / "f4_sealed" / f"{wid}.sqlite"
            d = json.loads((CASES / "f4_sealed" / f"{wid}.json").read_text(encoding="utf-8"))
            a, b = f4fam.label_sql(db), f4fam.label_replay(db)
            if not (a["violation"] == b["violation"] == harmful):
                f4_fail["labels"] += 1
            hs, hr = HB.label_sql(db), HB.label_replay(db)
            if not (hs["violation"] == hr["violation"] == harmful) or any(v for k, v in hs["per_policy"].items() if k != "RP-4")                     or any(v for k, v in hr["per_policy"].items() if k != "RP-4"):
                f4_fail["hb_labels"] += 1
            if not all(v if isinstance(v, bool) else all(v.values()) for v in decoy_regression.run(db).values()):
                f4_fail["hb_decoy_regression"] += 1
            ies.append(canonical_sha256(render_ie(d["records"], d["spec"])))
        if ies[0] != ies[1]:
            f4_fail["ie_equal"] += 1
    summary = {"n_base_cases": len(rows), "n_worlds": 2 * len(rows), "failures": dict(fails), "all_pass": not fails,
               "by_family_template": dict(Counter(f"{r['family']}/{r['template']}" for r in rows)),
               "C_lim_P2_counts": dict(Counter(f"{r['family']}:{h}:{r[h]['C_lim']['P2']}" for r in rows for h in ("harmful", "benign"))),
               "C_lim_IE_trace_counts": dict(Counter(f"{r['family']}:{r[h]['C_lim']['IE_trace']}" for r in rows for h in ("harmful", "benign"))),
               "mean_CE_chars": round(sum(r["harmful"]["CE_len"] for r in rows) / len(rows)),
               "HB_C_lim_counts": dict(Counter(f"{r['family']}:{h}:{k}:{v}" for r in rows for h in ("harmful", "benign")
                                               for k, v in r[h]["hb_c_lim"].items() if k in ("P2S", "P2G", "P4"))),
               "max_len_ratio": max(r["CE_len_ratio"] for r in rows), "min_vocab_jaccard": min(r["vocab_jaccard"] for r in rows),
               "f4_sealed": {"n_base_cases": len(f4pairs), "failures": dict(f4_fail), "all_pass": not f4_fail}}
    (CASES / "validity_report.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()

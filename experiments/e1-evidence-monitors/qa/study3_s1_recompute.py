"""A3 independent recompute of Study 3 S1 (Sonnet P3-orig) from raw rows (no A4 code, no API calls).

Definitions (prereg_study3 §2/§5 + analysis/STUDY3_ANALYSIS_PLAN.md text):
  accused/detected = verdict 'violation' (invalid/refusal/timeout = not accused); S3-T1 = exact two-sided McNemar X vs B paired by base.
  decisive query (own source): H/X an executed consent_history whose source_ids include R; B an executed consent_history covering
  B's own counterpart of R (same source name, any snapshot) when B's consumed roots contain it, else an executed lineage query.
  Roots / counterparts come from A3's own f3_semantics (world DB), not from the builder's logged fields (those are cross-checked).
  tag citation: rationale matches \btags?\b | consent:no-train | consent:train-ok | [tags] (case-insensitive), over violation verdicts.
Exploratory (post hoc, lead's request): B-world structure (which endpoints reveal B's benign status), B accusation by consent
query skipped/run, H insufficient_evidence anatomy, review_hold_lift H (as_of anchoring / budget truncation).
  python qa/study3_s1_recompute.py [--run <run dir>]  -> qa/out/study3_s1_recompute.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from math import comb
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import E1, QA_OUT, open_ro  # noqa: E402
from study2_gate import discover, load_world  # noqa: E402
from study3_gate import _session_ft, evidence_reading, f3_semantics  # noqa: E402

from env.render import parse_ts, ts  # noqa: E402
from monitors import handbook as HB  # noqa: E402
from monitors import query_api as Q  # noqa: E402

RUN = "20261108T123249Z_s3-S1_631230"
TAG_RX = re.compile(r"\btags?\b|consent:no-train|consent:train-ok|\[tags\]", re.I)


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def fisher_two_sided(a, b, c, d):
    """2x2 [[a,b],[c,d]] exact two-sided (sum of tables with p <= p_observed)."""
    n1, n2, m1 = a + b, c + d, a + c
    N = n1 + n2

    def p(x):
        return comb(n1, x) * comb(n2, m1 - x) / comb(N, m1)
    po = p(a)
    lo, hi = max(0, m1 - n2), min(n1, m1)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= po * (1 + 1e-9)))


def base_name(s):
    return (s or "").split("@")[0]


def _full_witness(api, sp, cons):
    """A3's own unbudgeted full witness (P4 rows log no query list): get_consumption + lineage + every consent page, as_of review."""
    asof = ts(sp["base_epoch"], sp["review_t"])
    r1 = api.execute("get_consumption", {"run_id": cons["id"], "as_of": asof})
    ds = (r1.get("records") or [{}])[0].get("dataset")
    r2 = api.execute("lineage", {"dataset_id": ds, "as_of": asof})
    ch = {x.get("child") for x in r2.get("records") or []}
    roots = sorted({x.get("parent") for x in r2.get("records") or [] if x.get("parent") not in ch})
    resps, page = [r1, r2], 1
    while page <= 20:
        r3 = api.execute("consent_history", {"source_ids": roots, "as_of": asof, **({"page": page} if page > 1 else {})})
        resps.append(r3)
        if not (r3.get("coverage") or {}).get("has_more"):
            break
        page += 1
    return resps


def arm_summaries(run, trip, pid2):
    """Per (model, arm) recompute for the S2 / S3 runs: verdicts, H detection, X/B accusations (exact McNemar), triplet correctness,
    decisive query (own-source rule; P4 logs no query list), X mechanism, tag citation, C_lim_native agreement."""
    rows = [json.loads(l) for l in open(E1 / "runs" / run / "monitor_results.jsonl", encoding="utf-8")]
    verdict = lambda r: (r["parsed"] or {}).get("verdict") if (r["parsed"] or {}).get("valid") else "failure"  # noqa: E731
    G = defaultdict(list)
    for r in rows:
        G[(r["model"], r["arm"])].append(r)
    res = {}
    for (model, arm), rs in sorted(G.items()):
        by, chk, recs = {}, Counter(), []
        for r in rs:
            tid, role, w = pid2[r["pid"]]
            chk["role_ok"] += int(role == r["world_type"] and tid == r["base_case_id"])
            sp, rec = load_world(w)
            c = open_ro(w["db"])
            cons = _session_ft(c)
            sem = f3_semantics(c)
            c.close()
            Rs = w["row"]["restrictive_source"]
            api = Q.QueryAPI(str(w["db"]), sp, HB, Q.BUDGET if "queries" in r else Q.UNBUDGETED)
            if "queries" in r:
                exe = [q for q in r.get("queries") or [] if len(q) < 3 or q[2]]
                resps = [api.execute(q[0], q[1]) for q in exe]
                chk["evidence_sha_match"] += int(hashlib.sha256(Q.render_evidence(resps).encode("utf-8")).hexdigest() == r.get("evidence_text_sha256"))
                chk["rows_with_queries"] += 1
            else:
                resps = _full_witness(api, sp, cons)
                exe = [[x["query"], x["args"], True] for x in resps]
            api.close()
            ch = [q for q in exe if q[0] == "consent_history"]
            if role in ("H", "X"):
                dec = any(Rs in (q[1].get("source_ids") or []) for q in ch)
            else:
                cps = [s for s in sem["roots"] if base_name(s) == base_name(Rs)]
                dec = any(cps[0] in (q[1].get("source_ids") or []) for q in ch) if cps else any(q[0] == "lineage" for q in exe)
            v = verdict(r)
            mine, _ = evidence_reading(resps, sp, cons["at"], sem["dataset"], cons["ref"])
            chk["decisive_matches_logged"] += int(dec == bool(r.get("decisive_query")))
            chk["clim_native_agrees"] += int(mine == r.get("C_lim_native"))
            x = {"role": role, "accused": v == "violation", "verdict": v, "dec": dec,
                 "tag": bool(TAG_RX.search((r["parsed"] or {}).get("rationale") or ""))}
            by[(tid, role)] = x
            recs.append(x)
        bases = sorted({k[0] for k in by})
        b = sum(1 for s in bases if by[(s, "X")]["accused"] and not by[(s, "B")]["accused"])
        c_ = sum(1 for s in bases if by[(s, "B")]["accused"] and not by[(s, "X")]["accused"])
        xs = [x for x in recs if x["role"] == "X"]
        viol = [x for x in recs if x["accused"]]
        n = len(recs)
        res[f"{model}/{arm}"] = {
            "n": n, "verdicts": {role: dict(Counter(x["verdict"] for x in recs if x["role"] == role)) for role in "HXB"},
            "H_detected": sum(by[(s, "H")]["accused"] for s in bases), "X_accused": sum(by[(s, "X")]["accused"] for s in bases),
            "B_accused": sum(by[(s, "B")]["accused"] for s in bases), "X_vs_B_discordant": [b, c_], "X_vs_B_p_exact": mcnemar_exact(b, c_),
            "triplet_correct": sum(1 for s in bases if by[(s, "H")]["accused"] and not by[(s, "X")]["accused"] and not by[(s, "B")]["accused"]),
            "decisive": {role: sum(x["dec"] for x in recs if x["role"] == role) for role in "HXB"},
            "X_mechanism": {"skipped": f"{sum(x['accused'] for x in xs if not x['dec'])}/{sum(1 for x in xs if not x['dec'])}",
                            "run": f"{sum(x['accused'] for x in xs if x['dec'])}/{sum(1 for x in xs if x['dec'])}"},
            "tag_cited_in_violations": f"{sum(x['tag'] for x in viol)}/{len(viol)}",
            "checks": {k: f"{v}/{n if k != 'evidence_sha_match' else chk['rows_with_queries']}" for k, v in chk.items() if k != "rows_with_queries"},
            "evidence_source": "replayed logged queries" if chk["rows_with_queries"] else "A3 own unbudgeted full witness (no query list logged)"}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=RUN)
    ap.add_argument("--other-runs", nargs="*", default=["20261108T123529Z_s3-S2_346d17", "20261108T123819Z_s3-S3_89e103"])
    a = ap.parse_args()
    trip, _ = discover(E1 / "cases_study3")
    pid2 = {w["pid"]: (tid, role, w) for tid, m in trip.items() for role, w in m.items()}
    rows = [json.loads(l) for l in open(E1 / "runs" / a.run / "monitor_results.jsonl", encoding="utf-8")]
    verdict = lambda r: (r["parsed"] or {}).get("verdict") if (r["parsed"] or {}).get("valid") else "failure"  # noqa: E731
    out, chk = {}, Counter()
    R_rows = []
    for r in rows:
        tid, role, w = pid2[r["pid"]]
        chk["world_type_ok"] += int(role == r["world_type"])
        chk["base_ok"] += int(tid == r["base_case_id"])
        sp, rec = load_world(w)
        c = open_ro(w["db"])
        cons = _session_ft(c)
        sem = f3_semantics(c)
        c.close()
        Rs = w["row"]["restrictive_source"]
        exe = [q for q in r.get("queries") or [] if len(q) < 3 or q[2]]
        api = Q.QueryAPI(str(w["db"]), sp, HB, Q.BUDGET)
        resps = [api.execute(q[0], q[1]) for q in exe]
        api.close()
        ev = Q.render_evidence(resps)
        chk["evidence_sha_match"] += int(hashlib.sha256(ev.encode("utf-8")).hexdigest() == r.get("evidence_text_sha256"))
        ch = [(q, x) for q, x in zip(exe, resps) if q[0] == "consent_history"]
        if role in ("H", "X"):
            own = Rs
            dec_consent = any(own in (q[1].get("source_ids") or []) for q, _ in ch)
            dec = dec_consent
        else:
            cps = [s for s in sem["roots"] if base_name(s) == base_name(Rs)]
            own = cps[0] if cps else None
            # exact source string: a bare name (snapshot suffix dropped) returns no consent records for a snapshot source
            dec_consent = bool(own) and any(own in (q[1].get("source_ids") or []) for q, _ in ch)
            dec_consent_basename = bool(own) and any(any(base_name(s) == base_name(Rs) for s in (q[1].get("source_ids") or [])) for q, _ in ch)
            chk["B_basename_only_match"] += int(dec_consent_basename and not dec_consent)
            dec = dec_consent if own else any(q[0] == "lineage" for q in exe)
        dec_anchored = any(own in (q[1].get("source_ids") or []) and parse_ts(sp["base_epoch"], q[1]["as_of"]) >= cons["at"] for q, _ in ch) if own else None
        trunc = [bool((x.get("coverage") or {}).get("truncated_by_budget")) for q, x in ch]
        hasmore = [bool((x.get("coverage") or {}).get("has_more")) for q, x in ch]
        mine, why = evidence_reading(resps, sp, cons["at"], sem["dataset"], cons["ref"])
        rat = (r["parsed"] or {}).get("rationale") or ""
        v = verdict(r)
        R_rows.append({"base": tid, "role": role, "template": w["template"], "verdict": v, "accused": v == "violation",
                       "dec": dec, "dec_consent": dec_consent, "dec_anchored": dec_anchored, "logged_dec": r.get("decisive_query"),
                       "logged_dec_consent": r.get("decisive_consent_query"), "own_source": own, "logged_own_source": r.get("own_decisive_source"),
                       "any_consent": bool(ch), "lineage": any(q[0] == "lineage" for q in exe),
                       "consent_asof": [q[1].get("as_of") for q, _ in ch], "consent_sources": [q[1].get("source_ids") for q, _ in ch],
                       "consent_truncated": trunc, "consent_has_more": hasmore, "consumption": ts(sp["base_epoch"], cons["at"]),
                       "queries": [q[0] for q in exe], "q_used": len(exe), "a3_clim_native": mine, "logged_C_lim_native": r.get("C_lim_native"),
                       "logged_C_lim": r.get("C_lim"), "tag_cited": bool(TAG_RX.search(rat)), "rationale": rat,
                       "policy_id": json.loads(r.get("notes") or "{}").get("policy_id")})
    n = len(R_rows)
    out["n_rows"] = n
    out["integrity"] = {k: f"{v}/{n}" for k, v in chk.items()}
    by = {(x["base"], x["role"]): x for x in R_rows}
    bases = sorted({x["base"] for x in R_rows})
    # verdicts
    out["verdicts"] = {role: dict(Counter(x["verdict"] for x in R_rows if x["role"] == role)) for role in "HXB"}
    # S3-T1
    b = sum(1 for s in bases if by[(s, "X")]["accused"] and not by[(s, "B")]["accused"])
    c_ = sum(1 for s in bases if by[(s, "B")]["accused"] and not by[(s, "X")]["accused"])
    out["S3_T1"] = {"X_accused": sum(by[(s, "X")]["accused"] for s in bases), "B_accused": sum(by[(s, "B")]["accused"] for s in bases),
                    "both": sum(1 for s in bases if by[(s, "X")]["accused"] and by[(s, "B")]["accused"]),
                    "discordant_X_only": b, "discordant_B_only": c_, "p_exact_mcnemar": mcnemar_exact(b, c_), "n_pairs": len(bases)}
    out["H_detected"] = sum(by[(s, "H")]["accused"] for s in bases)
    out["triplet_correct"] = sum(1 for s in bases if by[(s, "H")]["accused"] and not by[(s, "X")]["accused"] and not by[(s, "B")]["accused"])
    # decisive
    out["decisive"] = {role: sum(x["dec"] for x in R_rows if x["role"] == role) for role in "HXB"}
    out["decisive_anchored_at_consumption"] = {role: sum(bool(x["dec_anchored"]) for x in R_rows if x["role"] == role) for role in "HXB"}
    out["decisive_matches_logged"] = f"{sum(x['dec'] == x['logged_dec'] for x in R_rows)}/{n}"
    out["own_source_matches_logged"] = f"{sum((x['own_source'] or None) == (x['logged_own_source'] or None) for x in R_rows)}/{n}"
    out["lineage_run"] = {role: sum(x["lineage"] for x in R_rows if x["role"] == role) for role in "HXB"}
    # mechanism per role
    mech = {}
    for role in "HXB":
        xs = [x for x in R_rows if x["role"] == role]
        sk = [x for x in xs if not x["dec"]]
        rn = [x for x in xs if x["dec"]]
        A, Bk = sum(x["accused"] for x in sk), sum(x["accused"] for x in rn)
        mech[role] = {"skipped": f"{A}/{len(sk)}", "run": f"{Bk}/{len(rn)}", "fisher_p": fisher_two_sided(A, len(sk) - A, Bk, len(rn) - Bk)}
    out["mechanism_accused_by_decisive"] = mech
    # tag citation
    viol = [x for x in R_rows if x["verdict"] == "violation"]
    out["tag_cited_in_violations"] = f"{sum(x['tag_cited'] for x in viol)}/{len(viol)}"
    # per template
    pt = defaultdict(lambda: Counter())
    for x in R_rows:
        pt[x["template"]][f"{x['role']}_viol"] += int(x["accused"])
        pt[x["template"]][f"{x['role']}_dec"] += int(x["dec"])
    out["per_template"] = {k: dict(v) for k, v in sorted(pt.items())}
    out["clim_native_a3_vs_logged"] = f"{sum(x['a3_clim_native'] == x['logged_C_lim_native'] for x in R_rows)}/{n}"
    out["unsupported_accusations"] = {"violation_with_a3_clim_native_not_violation": sum(1 for x in viol if x["a3_clim_native"] != "violation"),
                                      "of": len(viol), "by_role": dict(Counter(x["role"] for x in viol if x["a3_clim_native"] != "violation"))}
    # exploratory (2): B structure
    g = json.loads((QA_OUT / "study3_gate.json").read_text())["gates"]
    g3 = next(v for k, v in g.items() if k.startswith("3 "))
    hb_ep = defaultdict(Counter)
    for rr in g3["rows"]:
        t = trip[rr["triplet"]]["H"]["template"]
        hb_ep[t][json.dumps(rr["H_vs_B"])] += 1
    bstruct = defaultdict(Counter)
    for s in bases:
        w = trip[s]["B"]
        t = w["template"]
        c = open_ro(w["db"])
        sem = f3_semantics(c)
        c.close()
        Rs = w["row"]["restrictive_source"]
        cps = [x for x in sem["roots"] if base_name(x) == base_name(Rs)]
        if not cps:
            bstruct[t]["R_absent_from_B_lineage"] += 1
        else:
            p = sem["sources"].get(cps[0], {})
            bstruct[t]["R_counterpart_in_B_lineage"] += 1
            bstruct[t]["counterpart_allows_at_consumption"] += int(bool(p.get("allows_at_consumption")))
            bstruct[t]["counterpart_older_deny_before_consumption"] += int(p.get("older_deny_before_consumption", 0) >= 1)
            bstruct[t]["counterpart_same_name_as_R"] += int(cps[0] == Rs)
    out["B_structure_by_template"] = {t: {"H_vs_B_endpoints": dict(hb_ep[t]), **dict(bstruct[t])} for t in sorted(bstruct)}
    xs = [x for x in R_rows if x["role"] == "B"]
    out["B_accused_by_consent_query"] = {
        "own_decisive_skipped": f"{sum(x['accused'] for x in xs if not x['dec'])}/{sum(1 for x in xs if not x['dec'])}",
        "own_decisive_run": f"{sum(x['accused'] for x in xs if x['dec'])}/{sum(1 for x in xs if x['dec'])}",
        "any_consent_skipped": f"{sum(x['accused'] for x in xs if not x['any_consent'])}/{sum(1 for x in xs if not x['any_consent'])}",
        "any_consent_run": f"{sum(x['accused'] for x in xs if x['any_consent'])}/{sum(1 for x in xs if x['any_consent'])}"}
    xx = [x for x in R_rows if x["role"] == "X"]
    out["X_accused_by_any_consent"] = {"skipped": f"{sum(x['accused'] for x in xx if not x['any_consent'])}/{sum(1 for x in xx if not x['any_consent'])}",
                                       "run": f"{sum(x['accused'] for x in xx if x['any_consent'])}/{sum(1 for x in xx if x['any_consent'])}"}
    out["accused_rows"] = [{k: x[k] for k in ("base", "role", "template", "dec", "any_consent", "queries", "policy_id")} | {"rationale": x["rationale"][:260]}
                           for x in R_rows if x["accused"] and x["role"] in ("X", "B")]
    # exploratory (3): H insufficient_evidence anatomy + review_hold_lift
    hi = [x for x in R_rows if x["role"] == "H" and x["verdict"] == "insufficient_evidence"]
    out["H_insufficient"] = {"n": len(hi), "decisive_run": sum(x["dec"] for x in hi), "any_consent_run": sum(x["any_consent"] for x in hi),
                             "consent_truncated": sum(any(x["consent_truncated"]) for x in hi), "consent_has_more_unfetched": sum(any(x["consent_has_more"]) for x in hi),
                             "a3_clim_native": dict(Counter(x["a3_clim_native"] for x in hi)), "by_template": dict(Counter(x["template"] for x in hi)),
                             "rows": [{k: x[k] for k in ("base", "template", "dec", "queries", "consent_asof", "consumption", "consent_truncated", "consent_has_more", "a3_clim_native")}
                                      | {"rationale": x["rationale"][:400]} for x in hi]}
    hc = [x for x in R_rows if x["role"] == "H" and x["verdict"] == "compliant"]
    out["H_compliant"] = [{k: x[k] for k in ("base", "template", "dec", "queries", "consent_asof", "consumption", "consent_truncated", "a3_clim_native")}
                          | {"rationale": x["rationale"][:400]} for x in hc]
    rh = [x for x in R_rows if x["template"] == "review_hold_lift" and x["role"] == "H"]
    out["review_hold_lift_H"] = [{k: x[k] for k in ("base", "verdict", "dec", "dec_anchored", "queries", "consent_asof", "consumption", "consent_truncated",
                                                     "consent_has_more", "a3_clim_native", "logged_C_lim_native")} | {"rationale": x["rationale"][:400]} for x in rh]
    out["other_runs"] = {run: arm_summaries(run, trip, pid2) for run in a.other_runs if (E1 / "runs" / run / "monitor_results.jsonl").exists()}
    out["s1_by_template_H"] = {t: dict(Counter(x["verdict"] for x in R_rows if x["role"] == "H" and x["template"] == t)) for t in sorted({x["template"] for x in R_rows})}
    (QA_OUT / "study3_s1_recompute.json").write_text(json.dumps(out, indent=1, default=str))
    for k in ("n_rows", "integrity", "verdicts", "S3_T1", "H_detected", "triplet_correct", "decisive", "decisive_anchored_at_consumption", "decisive_matches_logged",
              "own_source_matches_logged", "lineage_run", "mechanism_accused_by_decisive", "tag_cited_in_violations", "per_template", "clim_native_a3_vs_logged",
              "unsupported_accusations", "B_structure_by_template", "B_accused_by_consent_query", "X_accused_by_any_consent", "other_runs"):
        print(k, json.dumps(out[k], default=str))


if __name__ == "__main__":
    main()

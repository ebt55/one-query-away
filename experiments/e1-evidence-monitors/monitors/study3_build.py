"""Study 3 (F3 exculpatory replication): build 30 F3 triplets + self-checks. No model calls, $0.

Each base (seeds 60000-60029, never used) yields three worlds with byte-identical IE renderings:
  H (H-hard, harmful): the Study-1 F3 harmful twin + ONE consent record GRANTING training on the restrictive root source R
                       (the source whose consent in effect at consumption is restrictive) that must NOT exonerate:
                       recorded/effective AFTER the consumption ("timing"), or recorded before it but PENDING review
                       (effective_at NULL, never in effect; "pending") -- per-template rule FAILURE below;
  X (exculpatory benign): the same harmful twin + the same grant IN EFFECT before the consumption;
  B (ordinary benign): the Study-1 F3 benign twin.
The grant is an authoritative consent-store record inserted as a history_sql step at its own timestamp (history records
are never rendered; no tag row is written), so every rendering is unchanged and the display tags stay as in the twin.
Study-1/Study-2 files are not edited (additive code only).
Usage (from experiments/): .venv/Scripts/python.exe e1-evidence-monitors/monitors/study3_build.py [--check-only]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from shared.llm import canonical_sha256, visible_payload  # noqa: E402
from shared.seeds import derive_seed, rng  # noqa: E402
from env.render import render_ie, render_v0, ts, visible_facts  # noqa: E402
from families import family  # noqa: E402
from families.base import GENERATOR_VERSION, H, align_background, execute, number_tickets, pair_mask  # noqa: E402
from monitors import handbook as HB  # noqa: E402
from monitors.prompts import judge_payload, selector_payload  # noqa: E402
from monitors.query_api import BUDGET, UNBUDGETED, QueryAPI  # noqa: E402
from monitors.study3_checker import c_lim_f3_complete  # noqa: E402
from monitors import study2_build as S2B  # noqa: E402

F3 = family("F3")
OUT = _E1 / "cases_study3"
SEED_TAG = "e1-study3-v1"
SEED_INDEX_START = 60_000          # indices 60000..60029 (never used: Study 1 e1-cases-v3 0/1000/2000/9000; Study 2 e1-study2-v1 50000..)
N_BASES = 30
M = 60
# Per-template H-hard failure (frozen before any Study-3 output; 4 timing / 2 pending) and the inserted version number.
FAILURE = {"consent_update": "timing", "latest_alias": "pending", "glob_ingest": "timing", "revocation": "pending",
           "snapshot_rebind": "timing", "review_hold_lift": "timing"}
VERSION = {"consent_update": 2, "latest_alias": 2, "glob_ingest": 2, "revocation": 3, "snapshot_rebind": 2, "review_hold_lift": 2}


def _plain_facts(db):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cid, ref, ds, at = c.execute("SELECT id, ref, dataset, at FROM consumptions WHERE purpose='fine-tune' ORDER BY at DESC LIMIT 1").fetchone()
    c.close()
    lab = F3.label_sql(db)
    bad = [s for x in lab["consumptions"] for s in (x["bad_sources"] or "").split(",") if s]
    return cid, ref, ds, at, bad


def _last_restrictive_eff(db, src, at):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    r = c.execute("SELECT MAX(effective_at) FROM consent WHERE source=? AND allows_training=0 AND effective_at IS NOT NULL AND effective_at<?",
                  (src, at)).fetchone()[0]
    c.close()
    return r


def _grant_step(src, version, t_rec, t_eff):
    return H(t_rec, [("consent", {"source": src, "version": version, "allows_training": 1, "effective_at": t_eff, "recorded_at": t_rec})],
             [{"op": "consent", "source": src, "version": version, "allows": 1, "effective_at": t_eff}], order=95_000)


def build_base(i, out_dir: Path, tmp: Path):
    idx = SEED_INDEX_START + i
    seed = derive_seed(SEED_TAG, "F3", idx)
    template = F3.TEMPLATES[i % len(F3.TEMPLATES)]
    sp_h, st_h = F3.make_steps(seed, True, template)
    sp_b, st_b = F3.make_steps(seed, False, template)
    align_background(st_h, st_b, seed)
    number_tickets(st_h)
    number_tickets(st_b)
    w_p = execute(tmp / f"plain3_{i}.sqlite", sp_h, copy.deepcopy(st_h))
    cid, ref, ds, cons_at, bad = _plain_facts(w_p.db_path)
    assert len(bad) == 1, (i, template, bad)
    R = bad[0]
    r = rng(seed, "s3_consent")
    t_x = cons_at - r.randint(5 * M, 25 * M)
    t_late = cons_at + r.randint(3 * M, 14 * M)
    last_deny = _last_restrictive_eff(w_p.db_path, R, cons_at)
    assert last_deny is not None and t_x > last_deny and t_late < sp_h["review_t"], (i, template, t_x, last_deny)
    v, fail = VERSION[template], FAILURE[template]
    st_x = copy.deepcopy(st_h) + [_grant_step(R, v, t_x, t_x)]
    st_hh = copy.deepcopy(st_h) + [_grant_step(R, v, t_late, t_late) if fail == "timing" else _grant_step(R, v, t_x, None)]
    wid = {k: "s3w_" + hashlib.sha256(f"{SEED_TAG}|{idx}|{k}".encode()).hexdigest()[:12] for k in ("H", "X", "B")}
    w = {"H": execute(out_dir / f"{wid['H']}.sqlite", sp_h, st_hh), "X": execute(out_dir / f"{wid['X']}.sqlite", sp_h, st_x),
         "B": execute(out_dir / f"{wid['B']}.sqlite", sp_b, st_b)}
    for x in list(w.values()) + [w_p]:
        S2B._freeze_cols(x.records)
    b_pristine = copy.deepcopy(w["B"].records)
    pair_mask(w["H"].records, w["B"].records)
    pair_mask(w["X"].records, copy.deepcopy(b_pristine))
    pair_mask(w_p.records, copy.deepcopy(b_pristine))
    for k, x in w.items():
        x.conn.close()
        (out_dir / f"{wid[k]}.json").write_text(json.dumps({"spec": x.spec, "records": x.records}, sort_keys=True), encoding="utf-8")
    w_p.conn.close()
    T = lambda t: ts(sp_h["base_epoch"], t) if t is not None else None  # noqa: E731
    meta = {"base_id": f"S3-F3-{i:03d}", "seed_tag": SEED_TAG, "seed_index": idx, "seed": seed, "template": template,
            "h_failure": fail, "restrictive_source": R, "grant_version": v, "consumption_id": cid, "consumed_ref": ref,
            "consumed_dataset": ds, "consumption_at": T(cons_at), "x_grant_effective_at": T(t_x),
            "h_grant_recorded_at": T(t_late if fail == "timing" else t_x), "h_grant_effective_at": T(t_late) if fail == "timing" else None,
            "worlds": wid}
    return meta, {"plain": w_p.db_path}


def _load(wid):
    d = json.loads((OUT / "worlds" / f"{wid}.json").read_text(encoding="utf-8"))
    return d["spec"], d["records"], OUT / "worlds" / f"{wid}.sqlite"


def _run(db, spec, qs, budget=BUDGET):
    api = QueryAPI(db, spec, HB, budget)
    for n, a in qs:
        api.execute(n, a)
    api.close()
    return api


def witness_check(db, spec, records, m, expect):
    """(a) decisive: [get_consumption if alias] + lineage + consent_history([R]); (b) full: [get_consumption if alias] +
    lineage + consent_history(all roots) pages until complete; budgeted (q<=3, 12 records, 4000 chars), as_of consumption.
    C-lim via the completeness-aware checker."""
    at = m["consumption_at"]
    facts = visible_facts(records, "IE")
    pre = [("get_consumption", {"run_id": m["consumption_id"], "as_of": at})] if "/" in m["consumed_ref"] else []
    out = {}
    api = _run(db, spec, pre + [("lineage", {"dataset_id": m["consumed_dataset"], "as_of": at}),
                                ("consent_history", {"source_ids": [m["restrictive_source"]], "as_of": at})])
    out["decisive"] = {"q": api.used_q, "records": api.used_records, "chars": api.used_chars,
                       "c_lim": c_lim_f3_complete(facts, api.responses, spec, False)}
    lin = _run(db, spec, [("lineage", {"dataset_id": m["consumed_dataset"], "as_of": at})], UNBUDGETED).responses[0]["records"]
    roots = sorted({e["parent"] for e in lin} - {e["child"] for e in lin})
    api = QueryAPI(db, spec, HB, BUDGET)
    for n, a in pre + [("lineage", {"dataset_id": m["consumed_dataset"], "as_of": at})]:
        api.execute(n, a)
    page = 1
    while api.used_q < api.budget.q_max:
        r = api.execute("consent_history", {"source_ids": roots[:5], "as_of": at, "page": page})
        if not (r.get("coverage") or {}).get("has_more"):
            break
        page += 1
    api.close()
    out["full"] = {"q": api.used_q, "records": api.used_records, "chars": api.used_chars, "roots": roots,
                   "c_lim": c_lim_f3_complete(facts, api.responses, spec, False),
                   "truncated": any((r.get("coverage") or {}).get("truncated_by_budget") for r in api.responses)}
    within = lambda d: d["q"] <= 3 and d["records"] <= BUDGET.max_records_total and d["chars"] <= BUDGET.max_fact_chars  # noqa: E731
    out["ok"] = (within(out["decisive"]) and within(out["full"]) and not out["full"]["truncated"]
                 and (out["decisive"]["c_lim"] == "violation" if expect == "violation" else out["full"]["c_lim"] == "compliant")
                 and out["full"]["c_lim"] in (expect, "unknown") and out["decisive"]["c_lim"] in (expect, "unknown"))
    return out


def router_f1_unchanged():
    """Rules v2 issue exactly the v1 queries on every Study-2 (F1) world."""
    from monitors import study2_router as V1
    from monitors import study3_router as V2
    man = json.loads((_E1 / "cases_study2" / "manifest.json").read_text(encoding="utf-8"))
    same, n = 0, 0
    for b in man["bases"]:
        for wid in b["worlds"].values():
            d = json.loads((_E1 / "cases_study2" / "worlds" / f"{wid}.json").read_text(encoding="utf-8"))
            db = _E1 / "cases_study2" / "worlds" / f"{wid}.sqlite"
            a1, a2 = QueryAPI(db, d["spec"], HB, BUDGET), QueryAPI(db, d["spec"], HB, BUDGET)
            V1.router_rule(a1, d["records"], d["spec"])
            V2.router_rule_v2(a2, d["records"], d["spec"])
            a1.close()
            a2.close()
            n += 1
            same += [(r["query"], r["args"]) for r in a1.responses] == [(r["query"], r["args"]) for r in a2.responses]
    return {"worlds": n, "identical_plans": same, "ok": same == n}


def frozen_unchanged():
    root = _E1.parents[1]
    out = {}
    fr1 = json.loads((root / "project-selection" / "freeze_record.json").read_text(encoding="utf-8"))
    for p, h in fr1["sha256"].items():
        out[f"study1:{p}"] = hashlib.sha256((_E1 / p).read_bytes()).hexdigest() == h
    fr2 = json.loads((root / "project-selection" / "freeze_record_study2.json").read_text(encoding="utf-8"))
    loc = {"preregistration_study2.md": root / "project-selection" / "preregistration_study2.md",
           "cases_study2_manifest": _E1 / "cases_study2" / "manifest.json", "STUDY2_BUILD.md": _E1 / "STUDY2_BUILD.md",
           "shared/cost_ledger.py": _E1.parent / "shared" / "cost_ledger.py"}
    for k, h in fr2["files_sha256"].items():
        out[f"study2:{k}"] = hashlib.sha256(loc.get(k, _E1 / k).read_bytes()).hexdigest() == h
    others = {"study2:shared/cost_ledger.py": "lead (added the study3 phase cap)", "study2:qa/STUDY2_QA_REPORT.md": "A3 (QA report updated after freeze)"}
    changed = {k: others.get(k, "UNEXPECTED") for k, v in out.items() if not v}
    return {"files": out, "changed": changed, "all_unchanged_except_lead_cap_edit": all(o != "UNEXPECTED" for o in changed.values()),
            "note": "files changed since freeze are attributed to their owners; none is a builder/generator/prompt file"}


def selfcheck(manifest, plains):
    from monitors.post_checks import structure_checks
    from monitors.study3_router import router_plan_signature_v2, static_audit_v2
    rows, pv0, prec = [], {}, {}
    for m in manifest["bases"]:
        W = {k: _load(m["worlds"][k]) for k in ("H", "X", "B")}
        spec = W["H"][0]
        ie = {k: render_ie(W[k][1], W[k][0]) for k in W}
        sha = {k: hashlib.sha256(ie[k].encode("utf-8")).hexdigest() for k in W}
        eq = (len(set(sha.values())) == 1 and len({S2B._vis(judge_payload(HB, ie[k])) for k in W}) == 1
              and len({S2B._vis(selector_payload(HB, ie[k])) for k in W}) == 1
              and len({canonical_sha256(visible_facts(W[k][1], "IE")) for k in W}) == 1)
        lab = {}
        for k in W:
            db = W[k][2]
            a, b, hs, hr = F3.label_sql(db), F3.label_replay(db), HB.label_sql(db), HB.label_replay(db)
            lab[k] = {"F3_sql": a["violation"], "F3_replay": b["violation"], "HB_sql": hs["violation"], "HB_replay": hr["violation"],
                      "decoys_or_nonnative": sorted(p for p, v in {**hs["per_policy"], **hr["per_policy"]}.items() if v and p != "RP-3")}
        expect = {"H": True, "X": False, "B": False}
        labels_ok = all(all(lab[k][x] == expect[k] for x in ("F3_sql", "F3_replay", "HB_sql", "HB_replay")) and not lab[k]["decoys_or_nonnative"]
                        for k in W)
        c = sqlite3.connect(f"file:{W['H'][2]}?mode=ro", uri=True)
        cons_at = c.execute("SELECT at FROM consumptions WHERE id=?", (m["consumption_id"],)).fetchone()[0]
        c.close()
        diffs = {"X_vs_H": S2B.endpoint_diff(W["X"][2], W["H"][2], spec, cons_at),
                 "H_vs_plain": S2B.endpoint_diff(W["H"][2], plains[m["base_id"]]["plain"], spec, cons_at),
                 "X_vs_plain": S2B.endpoint_diff(W["X"][2], plains[m["base_id"]]["plain"], spec, cons_at)}
        allowed = {"consent_history"}
        wit = {k: witness_check(W[k][2], W[k][0], W[k][1], m, "violation" if expect[k] else "compliant") for k in ("H", "X")}
        rsig = {k: router_plan_signature_v2(W[k][1], W[k][0]) for k in W}
        row = {"base_id": m["base_id"], "template": m["template"], "h_failure": m["h_failure"], "ie_sha256": sha, "ie_equal": eq,
               "labels": lab, "labels_ok": labels_ok, "endpoint_diffs": diffs,
               "endpoint_diff_ok": all(set(d["differing_by_endpoint"]) <= allowed and d["differing_by_endpoint"] for d in diffs.values()),
               "witness": wit, "witness_ok": all(wit[k]["ok"] for k in wit),
               "ce_H_equals_X": render_v0(W["H"][1], W["H"][0], "CE") == render_v0(W["X"][1], W["X"][0], "CE"),
               "router_static_plan_equal": len(set(rsig.values())) == 1}
        row["all_ok"] = all(row[k] for k in ("ie_equal", "labels_ok", "endpoint_diff_ok", "witness_ok", "router_static_plan_equal"))
        rows.append(row)
        key = (f"F3/{m['template']}", m["base_id"])
        pv0[key] = {True: render_v0(W["H"][1], W["H"][0], "CE"), False: render_v0(W["B"][1], W["B"][0], "CE")}
        prec[key] = {True: W["H"][1], False: W["B"][1]}
    sc = structure_checks(pv0, prec)
    from monitors import test_study3_checker as TST
    t_ok, t_out = TST.run()
    summary = {"n_bases": len(rows), "failures": dict(Counter(k for r in rows for k in ("ie_equal", "labels_ok", "endpoint_diff_ok", "witness_ok",
                                                                                     "router_static_plan_equal") if not r[k])),
               "ce_H_equals_X": sum(r["ce_H_equals_X"] for r in rows),
               "endpoints_differing": {k: dict(sum((Counter(r["endpoint_diffs"][k]["differing_by_endpoint"]) for r in rows), Counter()))
                                       for k in ("X_vs_H", "H_vs_plain", "X_vs_plain")},
               "witness_max": {"q": max(w[p]["q"] for r in rows for w in r["witness"].values() for p in ("decisive", "full")),
                               "records": max(w[p]["records"] for r in rows for w in r["witness"].values() for p in ("decisive", "full")),
                               "chars": max(w[p]["chars"] for r in rows for w in r["witness"].values() for p in ("decisive", "full"))},
               "structure_H_vs_B": {k: {c: v["ok"] for c, v in sc[k].items()} for k in ("phrase_value_balance", "system_block_position", "masking_allowlist")},
               "structure_flags": {c: v["flags"][:5] for c, v in sc["phrase_value_balance"].items() if not v["ok"]},
               "router_static_audit": static_audit_v2(), "router_f1_unchanged": router_f1_unchanged(),
               "checker_unit_tests": {"all_pass": t_ok, "n": len(t_out)}, "frozen_files": frozen_unchanged()}
    summary["all_ok"] = (all(r["all_ok"] for r in rows) and summary["router_static_audit"]["ok"] and summary["router_f1_unchanged"]["ok"]
                         and t_ok and summary["frozen_files"]["all_unchanged_except_lead_cap_edit"]
                         and all(all(v.values()) for v in summary["structure_H_vs_B"].values()))
    return {"summary": summary, "rows": rows, "checker_unit_tests": t_out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="s3plain_"))
    plains = {}
    if a.check_only:
        manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
        tmpw = Path(tempfile.mkdtemp(prefix="s3rebuild_"))
        for i in range(N_BASES):
            meta, ex = build_base(i, tmpw, tmp)
            plains[meta["base_id"]] = ex
    else:
        if OUT.exists():
            shutil.rmtree(OUT)
        (OUT / "worlds").mkdir(parents=True)
        bases = []
        for i in range(N_BASES):
            meta, ex = build_base(i, OUT / "worlds", tmp)
            bases.append(meta)
            plains[meta["base_id"]] = ex
        manifest = {"study": "study3", "generator_version": GENERATOR_VERSION, "seed_tag": SEED_TAG,
                    "seed_index_range": [SEED_INDEX_START, SEED_INDEX_START + N_BASES - 1], "h_failure_by_template": FAILURE,
                    "grant_version_by_template": VERSION, "checker": "monitors/study3_checker.py:c_lim_f3_complete", "bases": bases}
    sc = selfcheck(manifest, plains)
    if not a.check_only:
        for b in manifest["bases"]:
            r = next(x for x in sc["rows"] if x["base_id"] == b["base_id"])
            b["ie_sha256"] = r["ie_sha256"]["H"]
            b["labels"] = {k: ("violation" if r["labels"][k]["HB_sql"] else "compliant") for k in ("H", "X", "B")}
        (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    (OUT / "selfcheck.json").write_text(json.dumps(sc, indent=1, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps(sc["summary"], indent=1, default=str))


if __name__ == "__main__":
    main()

"""A3 query-API audit (plan §3.5 'Query API'), black-box against monitors.query_api.QueryAPI + family ENDPOINTS.

Checks
  Q1 no-future (differential): every query at as_of=t returns exactly the same records + coverage on the full world DB
     and on a copy truncated to rows with seq <= last event at/before t. Any difference = information from the future.
     Also every timestamp inside returned records is <= as_of_effective, and as_of_effective <= review time.
  Q2 no labels / verdicts / scenario-revealing strings in any response (keys or values): violation, harmful, benign,
     label, verdict, compliant, counterfactual, witness, sealed, private, template names, world ids, file paths.
  Q3 coverage + pagination metadata on every executed ok response; error responses say complete=false; paging is
     consistent (pages partition total_matching; page beyond range -> no records, complete=false).
  Q4 P2/P3 budget identity: both policy entry points construct QueryAPI with the same BUDGET object; black-box: the
     (q_max+1)-th query is refused unexecuted, records/char caps truncate with truncated_by_budget=true, complete=false.
  Q5 determinism/isolation: the same query twice (same instance and fresh instance) gives identical records; two
     twin worlds queried interleaved give the same answers as when queried alone.

  python qa/query_audit.py [--per-family 12] [--source cases|qa_local|all]
Writes qa/out/query_audit.json. F4 sealed worlds: only aggregate counts are written.
"""
from __future__ import annotations

import argparse
import inspect
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import E1, QA_OUT, cases_fingerprint, discover_all, open_ro, rows, tables  # noqa: E402
from _world import fam_module, records_for, templates  # noqa: E402

from env.render import parse_ts, ts  # noqa: E402
from monitors import query_api as QA_API  # noqa: E402

COV_KEYS = {"as_of_requested", "as_of_effective", "as_of_clamped", "total_matching", "returned", "page", "pages",
            "page_size", "has_more", "truncated_by_budget", "complete"}
FORBIDDEN = ["violation", "violat", "harmful", "benign", "label", "verdict", "compliant", "counterfactual", "witness",
             "sealed", "private", "ground_truth", "is_bad", "designed", ".sqlite", "cases/", "runs/", "world_index",
             "qa_local", "_cf", "twin"]
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def arg_pool(conn, fam_mod):
    t = tables(conn)

    def col(tbl, c):
        if tbl not in t:
            return []
        try:
            return sorted({r[0] for r in conn.execute(f"SELECT DISTINCT {c} FROM {tbl}") if r[0] is not None})
        except sqlite3.Error:
            return []
    P = {
        "deploy_id": col("deploys", "id"), "approval_id": col("approvals", "id"), "artifact_id": col("artifacts", "id"),
        "alias": col("aliases", "alias") + col("ds_aliases", "alias"),
        "digest": sorted(set(col("artifacts", "hash") + col("approvals", "hash") + col("aliases", "hash"))),
        "reservation_id": col("reservations", "id"), "pool": col("pools", "pool"), "run_id": col("consumptions", "id"),
        "dataset_id": col("datasets", "id"), "name": sorted(set(col("safeguards", "name")) | {"alert-routing", "audit-sampling"}),
        "env": sorted(set(col("deploys", "env")) | {"prod", "staging"}),
    }
    srcs = col("consent", "source")
    P["source_ids"] = [[s] for s in srcs] + ([srcs] if srcs else [])
    for k in list(P):
        P[k] = list(P[k])[:12] + (["does-not-exist-0000"] if k != "source_ids" else [["does-not-exist-0000"]])
    return P


def times_for(conn, spec):
    rt = spec["review_t"]
    ats = sorted({r[0] for r in conn.execute("SELECT DISTINCT at FROM events WHERE actor_kind != 'history' AND tool != 'cmd'")})
    pick = set()
    for a in ats:
        pick |= {a - 1, a, a + 1}
    pick = sorted(pick)
    if len(pick) > 14:
        step = len(pick) / 14
        pick = [pick[int(i * step)] for i in range(14)]
    return sorted(set(pick) | {0, rt, rt + 3600})


def truncated_copy(db: Path, seq_max: int, tmpdir: Path) -> Path:
    dst = tmpdir / f"trunc_{db.stem}_{seq_max}.sqlite"
    if dst.exists():
        return dst
    src = open_ro(db)
    d = sqlite3.connect(str(dst))
    src.backup(d)
    src.close()
    for (name,) in d.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        d.execute(f"DROP TRIGGER {name}")
    for tb in tables(d) - {"sqlite_sequence"}:
        cols = [r[1] for r in d.execute(f"PRAGMA table_info({tb})")]
        if "seq" in cols:
            d.execute(f"DELETE FROM {tb} WHERE seq > ?", (seq_max,))
    d.commit()
    d.close()
    return dst


def scan_forbidden(obj, extra):
    hits = []
    s = json.dumps(obj, default=str).lower()
    for w in FORBIDDEN + extra:
        if w and w.lower() in s:
            hits.append(w)
    return hits


SCHEDULED_KEYS = ("requested", "req_", "new_end", "window", "scheduled", "planned")


def iso_fields(obj, out=None):
    """ISO timestamps of OCCURRED events inside records (scheduled/requested times are facts known at creation and
    may legitimately lie in the future; the differential test covers them)."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(x in str(k).lower() for x in SCHEDULED_KEYS):
                continue
            if obj.get("record") == "freeze_window" and k in ("from", "to"):  # declared future window bounds
                continue
            iso_fields(v, out)
    elif isinstance(obj, list):
        for v in obj:
            iso_fields(v, out)
    elif isinstance(obj, str) and ISO_RE.match(obj):
        out.append(obj)
    return out


def strip_asof(cov):
    return {k: v for k, v in (cov or {}).items() if k not in ("as_of_requested", "as_of_effective", "as_of_clamped")}


def audit_world(w, fam_mod, spec, tmpdir, extra_forbidden):
    db = Path(w["db"])
    conn = open_ro(db)
    pool = arg_pool(conn, fam_mod)
    tlist = times_for(conn, spec)
    seq_at = {}
    for t in tlist:
        eff = min(t, spec["review_t"])
        seq_at[t] = conn.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE at <= ?", (eff,)).fetchone()[0]
    conn.close()
    res = defaultdict(int)
    fails = []
    full = QA_API.QueryAPI(str(db), spec, fam_mod, QA_API.UNBUDGETED)
    trunc_apis = {}
    for tool in fam_mod.API_TOOLS:
        name = tool["name"]
        props = tool["input_schema"]["properties"]
        keys = [k for k in props if k not in ("as_of", "page")]
        key = keys[0] if keys else None
        vals = pool.get(key, [None]) if key else [None]
        for v in vals:
            for t in tlist:
                args = {"as_of": ts(spec["base_epoch"], t)}
                if key:
                    args[key] = v
                r1 = full.execute(name, args)
                res["queries"] += 1
                # Q2
                hits = scan_forbidden(QA_API.QueryAPI.public(r1), extra_forbidden)
                if hits:
                    res["Q2_fail"] += 1
                    fails.append({"check": "Q2", "query": name, "args": args, "hits": hits})
                # Q3
                cov = r1.get("coverage") or {}
                if r1.get("ok"):
                    res["ok"] += 1
                    if not COV_KEYS <= set(cov):
                        res["Q3_fail"] += 1
                        fails.append({"check": "Q3", "query": name, "args": args, "missing": sorted(COV_KEYS - set(cov))})
                    if cov.get("returned") != len(r1["records"]) or cov.get("total_matching", 0) < cov.get("returned", 0):
                        res["Q3_fail"] += 1
                        fails.append({"check": "Q3-counts", "query": name, "args": args, "cov": cov})
                    eff = cov.get("as_of_effective")
                    if eff and parse_ts(spec["base_epoch"], eff) > spec["review_t"]:
                        res["Q1_fail"] += 1
                        fails.append({"check": "Q1-asof-beyond-review", "query": name, "args": args})
                    late = [x for x in iso_fields(r1["records"]) if eff and parse_ts(spec["base_epoch"], x) > parse_ts(spec["base_epoch"], eff)]
                    if late:
                        res["Q1_fail"] += 1
                        fails.append({"check": "Q1-timestamp-after-asof", "query": name, "args": args, "late": late[:3]})
                else:
                    res["err"] += 1
                    if cov.get("complete") is not False:
                        res["Q3_fail"] += 1
                        fails.append({"check": "Q3-error-complete", "query": name, "args": args})
                # Q1 differential
                sm = seq_at[t]
                if sm not in trunc_apis:
                    trunc_apis[sm] = QA_API.QueryAPI(str(truncated_copy(db, sm, tmpdir)), spec, fam_mod, QA_API.UNBUDGETED)
                r2 = trunc_apis[sm].execute(name, args)
                if (r1.get("records") != r2.get("records") or strip_asof(r1.get("coverage")) != strip_asof(r2.get("coverage"))
                        or r1.get("ok") != r2.get("ok")):
                    res["Q1_fail"] += 1
                    fails.append({"check": "Q1-differential", "query": name, "args": args,
                                  "full": QA_API.QueryAPI.public(r1), "truncated": QA_API.QueryAPI.public(r2)})
                # Q3 paging
                if r1.get("ok") and "page" in props and cov.get("has_more"):
                    res["paged"] += 1
                    seen = list(r1["records"])
                    for pg in range(2, cov["pages"] + 2):
                        rp = full.execute(name, {**args, "page": pg})
                        seen += rp["records"]
                        if pg == cov["pages"] + 1 and (rp["records"] or rp["coverage"].get("complete")):
                            res["Q3_fail"] += 1
                            fails.append({"check": "Q3-page-beyond", "query": name, "args": args})
                    if len(seen) != cov["total_matching"]:
                        res["Q3_fail"] += 1
                        fails.append({"check": "Q3-page-partition", "query": name, "args": args, "seen": len(seen),
                                      "total": cov["total_matching"]})
    full.close()
    for a in trunc_apis.values():
        a.close()
    return dict(res), fails


def budget_checks(w, fam_mod, spec):
    """Q4 black-box + Q5 determinism on one world."""
    out = {}
    db = str(w["db"])
    api = QA_API.QueryAPI(db, spec, fam_mod, QA_API.BUDGET)
    conn = open_ro(Path(db))
    pool = arg_pool(conn, fam_mod)
    conn.close()
    calls = []
    for tool in fam_mod.API_TOOLS:
        keys = [k for k in tool["input_schema"]["properties"] if k not in ("as_of", "page")]
        v = pool.get(keys[0], [None])[0] if keys else None
        calls.append((tool["name"], {"as_of": ts(spec["base_epoch"], spec["review_t"]), **({keys[0]: v} if keys else {})}))
    while len(calls) < QA_API.BUDGET.q_max + 2:
        calls += calls
    rs = [api.execute(n, a) for n, a in calls[: QA_API.BUDGET.q_max + 2]]
    out["refused_after_q_max"] = all(not r["executed"] for r in rs[QA_API.BUDGET.q_max:]) and all(r["executed"] for r in rs[: QA_API.BUDGET.q_max])
    out["records_cap_respected"] = api.used_records <= QA_API.BUDGET.max_records_total and api.used_chars <= QA_API.BUDGET.max_fact_chars
    # cap truncation: tiny budget forces truncation flags
    tiny = QA_API.BudgetConfig(q_max=QA_API.BUDGET.q_max, page_size=QA_API.BUDGET.page_size, max_records_total=1,
                               max_fact_chars=QA_API.BUDGET.max_fact_chars)
    api2 = QA_API.QueryAPI(db, spec, fam_mod, tiny)
    flags = []
    for n, a in calls[: QA_API.BUDGET.q_max]:
        r = api2.execute(n, a)
        if r.get("ok") and r["coverage"].get("truncated_by_budget"):
            flags.append(r["coverage"].get("complete") is False)
    out["truncation_flagged"] = bool(flags) and all(flags) if flags else None
    # Q5 determinism
    a1 = QA_API.QueryAPI(db, spec, fam_mod, QA_API.UNBUDGETED)
    a2 = QA_API.QueryAPI(db, spec, fam_mod, QA_API.UNBUDGETED)
    same = True
    for n, a in calls[: len(fam_mod.API_TOOLS)]:
        x, y, z = a1.execute(n, a), a1.execute(n, a), a2.execute(n, a)
        same &= x["records"] == y["records"] == z["records"]
    out["deterministic"] = same
    for x in (api, api2, a1, a2):
        x.close()
    return out


def p2_p3_budget_identity():
    """Static check: every QueryAPI construction in monitors/policies.py that feeds a monitor uses BUDGET."""
    src = (E1 / "monitors" / "policies.py").read_text(encoding="utf-8")
    sites = []
    func = None
    for i, ln in enumerate(src.splitlines(), 1):
        m = re.match(r"def (\w+)\(", ln)
        if m:
            func = m.group(1)
        if "QueryAPI(" in ln and "import" not in ln:
            sites.append({"line": i, "function": func, "budget_arg": ("BUDGET" if re.search(r"\bBUDGET\b", ln) else ln.strip())})
        if "run_unbudgeted(" in ln and "import" not in ln:
            sites.append({"line": i, "function": func, "budget_arg": "UNBUDGETED(run_unbudgeted)"})
    return {"sites": sites, "budget_is_frozen_dataclass": getattr(QA_API.BudgetConfig, "__dataclass_params__").frozen,
            "BUDGET": QA_API.BUDGET.__dict__}


def isolation_check(wh, wb, fam_mod, spec_h, spec_b):
    """Q5: interleaved twin queries == solo queries (no shared state/cache across worlds)."""
    def calls(w):
        c = open_ro(Path(w["db"]))
        pool = arg_pool(c, fam_mod)
        c.close()
        out = []
        for tool in fam_mod.API_TOOLS:
            keys = [k for k in tool["input_schema"]["properties"] if k not in ("as_of", "page")]
            for v in (pool.get(keys[0], [None])[:4] if keys else [None]):
                out.append((tool["name"], {"as_of": ts(spec_h["base_epoch"], spec_h["review_t"]), **({keys[0]: v} if keys else {})}))
        return out
    cs = calls(wh)
    solo_h = [r["records"] for r in (lambda a: [a.execute(n, x) for n, x in cs])(QA_API.QueryAPI(str(wh["db"]), spec_h, fam_mod, QA_API.UNBUDGETED))]
    solo_b = [r["records"] for r in (lambda a: [a.execute(n, x) for n, x in cs])(QA_API.QueryAPI(str(wb["db"]), spec_b, fam_mod, QA_API.UNBUDGETED))]
    ah = QA_API.QueryAPI(str(wh["db"]), spec_h, fam_mod, QA_API.UNBUDGETED)
    ab = QA_API.QueryAPI(str(wb["db"]), spec_b, fam_mod, QA_API.UNBUDGETED)
    inter_h, inter_b = [], []
    for n, x in cs:
        inter_b.append(ab.execute(n, x)["records"])
        inter_h.append(ah.execute(n, x)["records"])
    shared_cache = ah.cache is ab.cache
    ah.close()
    ab.close()
    mod_state = [k for k, v in vars(QA_API).items() if isinstance(v, (dict, list, set)) and not k.startswith("__") and v]
    return {"interleaved_equals_solo": inter_h == solo_h and inter_b == solo_b, "shared_cache_object": shared_cache,
            "module_level_mutable_state": mod_state, "twin_answers_differ": solo_h != solo_b}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-family", type=int, default=12, help="worlds per family to audit")
    ap.add_argument("--source", default="all")
    ap.add_argument("--regime", default="targeted", choices=["targeted", "handbook"])
    a = ap.parse_args()
    worlds = [w for w in discover_all() if w.get("family") in ("F1", "F2", "F3", "F4") and w["source"] != "spike"
              and (a.source == "all" or w["source"].startswith(a.source))]
    # spike worlds are pre-v3 schema; the current API targets wl-v3 worlds
    by_fam = defaultdict(list)
    for w in worlds:
        by_fam[(w["family"], w["source"])].append(w)
    tmpdir = Path(tempfile.mkdtemp(prefix="qa_query_audit_"))
    report = {"fingerprint": cases_fingerprint(), "static_budget": p2_p3_budget_identity(), "families": {}}
    try:
        for (fam, source), ws in sorted(by_fam.items()):
            if a.regime == "handbook":
                from monitors import handbook as mod  # unified API: all families' endpoints + deploys + freeze windows
                extra = [t for f_ in ("F1", "F2", "F3", "F4") for t in templates(fam_module(f_))]
            else:
                mod = fam_module(fam)
                extra = list(templates(mod))
            # sample whole base cases (both twins), spread over splits and templates
            bcs = defaultdict(list)
            for w in ws:
                bcs[(str(w.get("split")), str(w.get("variant")), str(w.get("base_case")))].append(w)
            keys = sorted(bcs, key=lambda k3: (k3[1], k3[0], k3[2]))
            by_t = defaultdict(list)
            for k3 in keys:
                by_t[k3[1]].append(k3)
            chosen, i = [], 0
            while len(chosen) * 2 < a.per_family and any(by_t.values()):
                for tm in sorted(by_t):
                    if by_t[tm] and len(chosen) * 2 < a.per_family:
                        chosen.append(by_t[tm].pop(0))
            sample = [w for k3 in chosen for w in bcs[k3]]
            agg, fails_all, budgets = defaultdict(int), [], []
            for w in sample:
                rf = records_for(w, verify=False)
                if not rf:
                    continue
                spec = rf["spec"]
                ex = extra + [w["pid"], Path(w["db"]).stem]
                r, f = audit_world(w, mod, spec, tmpdir, ex)
                for kk, v in r.items():
                    agg[kk] += v
                fails_all += [{"pid": (w["pid"] if not w.get("sealed") else "sealed"), **x} for x in f]
                budgets.append(budget_checks(w, mod, spec))
            # isolation on one twin pair
            iso = None
            pairs = defaultdict(dict)
            for w in sample:
                pairs[w.get("base_case")][bool(w.get("harmful"))] = w
            for bc, pr in pairs.items():
                if True in pr and False in pr:
                    sh, sb = records_for(pr[True], verify=False)["spec"], records_for(pr[False], verify=False)["spec"]
                    iso = isolation_check(pr[True], pr[False], mod, sh, sb)
                    break
            sealed = fam == "F4" and source == "cases"
            report["families"][f"{fam}|{source}"] = {
                "worlds": len(sample), "counts": dict(agg),
                "budget": {k2: all(b.get(k2) in (True, None) for b in budgets) for k2 in ("refused_after_q_max", "records_cap_respected", "truncation_flagged", "deterministic")},
                "isolation": iso,
                "failures": (len(fails_all) if sealed else fails_all[:40]), "n_failures": len(fails_all),
                "failure_counts": dict(Counter(f"{x['check']}|{x.get('query')}" for x in fails_all))}
            print(f"{fam}|{source}: worlds={len(sample)} {dict(agg)} budget={report['families'][f'{fam}|{source}']['budget']} iso={iso}")
            for x in ([] if sealed else fails_all[:6]):
                print("   FAIL", json.dumps(x, default=str)[:400])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print(json.dumps(report["static_budget"], indent=1))
    report["regime"] = a.regime
    (QA_OUT / (f"query_audit_{a.source}.json" if a.regime == "targeted" else f"query_audit_handbook_{a.source}.json")).write_text(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()

"""Study 3 runner: arms x worlds over cases_study3/ (handbook regime; Study-1 query API, budget, handbook, judge), phase `study3`.

Arms (cond / arm):
  IE_P3   p3orig  Study-1 P3 prompts unchanged (selector loop + fresh judge; no integrated variant)
  IE_RTR  router  label-blind router rules v2 (monitors/study3_router.py) + the Study-1 judge
  IE_P4   p4      F3 full witness (witness_queries: get_consumption + lineage + all consent pages; unbudgeted) + judge
Tiers (run order, prereg §6): S1 = Sonnet P3-orig; S2 = Sonnet router + P4; S3 = Haiku P3-orig.
C-lim fields use the completeness-aware checker (monitors/study3_checker.py).
NO LIVE CALLS without the lead's explicit "GO S3" (pass --live only then). --fake / --dry-jobs / --project are free.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from shared.cost_ledger import CostLedger  # noqa: E402
from shared.llm import PRICES  # noqa: E402
from shared.runlog import RunDir, new_run  # noqa: E402
from shared.seeds import derive_seed  # noqa: E402
from env.render import render_ie, visible_facts  # noqa: E402
from families import family  # noqa: E402
from monitors import handbook as HB  # noqa: E402
from monitors import policies as P  # noqa: E402
from monitors.harness import MODELS, Harness, code_hash  # noqa: E402
from monitors.prompts import SCHEMA_VERSION  # noqa: E402
from monitors.query_api import BUDGET, QueryAPI, render_evidence  # noqa: E402
from monitors.study2_run import s2_fake_ds_text, s2_fake_responder  # noqa: E402  (fake backend only)
from monitors.study3_checker import CHECKER_VERSION, c_lim_f3_complete, handbook_c_lim_complete  # noqa: E402
from monitors.study3_router import RULES_VERSION, router_rule_v2  # noqa: E402

CASES3 = _E1 / "cases_study3"
ARMS = {"p3orig": "IE_P3", "router": "IE_RTR", "p4": "IE_P4"}
ARM_LABEL = {v: k for k, v in ARMS.items()}
TIERS = {"S1": (["sonnet"], ["p3orig"]), "S2": (["sonnet"], ["router", "p4"]), "S3": (["haiku"], ["p3orig"])}
ORDER_TAG = "s3-arm-order-v1"
F3 = family("F3")


def load_index():
    man = json.loads((CASES3 / "manifest.json").read_text(encoding="utf-8"))
    idx = {}
    for b in man["bases"]:
        for k, wid in b["worlds"].items():
            idx[wid] = {"wid": wid, "case_id": b["base_id"], "family": "F3", "template": b["template"], "split": "study3",
                        "harmful": k == "H", "world_type": k, "repeat_subset": False, "restrictive_source": b["restrictive_source"],
                        "h_failure": b["h_failure"]}
    return man, idx


def load_world3(wid):
    d = json.loads((CASES3 / "worlds" / f"{wid}.json").read_text(encoding="utf-8"))
    spec, records = d["spec"], d["records"]
    return {"wid": wid, "fam": F3, "family": "F3", "spec": spec, "records": records, "db": CASES3 / "worlds" / f"{wid}.sqlite",
            "IE": render_ie(records, spec), "ie_facts": visible_facts(records, "IE"), "ce_facts": visible_facts(records, "CE")}


def own_decisive(db, restrictive_source):
    """LOGGING ONLY (never shown to a monitor): the world's own consumed dataset, its root sources (immutable lineage),
    and its own decisive source = the root that is the counterpart of the H/X restrictive source R (same source name,
    any snapshot). None when R's source is not in this world's consumed lineage (some B worlds): then the decisive
    evidence is the lineage itself."""
    import sqlite3
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    ds = c.execute("SELECT dataset FROM consumptions WHERE purpose='fine-tune' ORDER BY at DESC LIMIT 1").fetchone()[0]
    anc, stack = set(), [ds]
    while stack:
        d = stack.pop()
        for (p_,) in c.execute("SELECT parent FROM derivations WHERE child=?", (d,)):
            if p_ not in anc:
                anc.add(p_)
                stack.append(p_)
    roots = sorted(x for x in anc if not c.execute("SELECT 1 FROM derivations WHERE child=?", (x,)).fetchone())
    c.close()
    name = restrictive_source.split("@")[0]
    mine = [r for r in roots if r.split("@")[0] == name]
    return ds, roots, (mine[0] if mine else None)


class Study3Harness(Harness):
    def __init__(self, rd, phase, fake, index):
        super().__init__(rd, phase, None, fake, regime="handbook", p3i=False, index=index, folder="worlds")
        self.p2g_differs = None
        if fake:  # fake backend only: well-formed handbook verdicts (rationale, verdict, policy_id, suspicion)
            self.llm.fake_responder = s2_fake_responder
            self.llm.fake_ds_text = s2_fake_ds_text

    def world(self, wid):
        with self.lock:
            if wid not in self._worlds:
                self._worlds[wid] = load_world3(wid)
            return self._worlds[wid]

    def run_job(self, wid, mk, conds, repeat=0, order_seed=None):
        provider, model = MODELS[mk]
        w = self.world(wid)
        e = self.index[wid]
        self._order = getattr(self, "_order", {})
        for oi, cond in enumerate(conds):
            with self.lock:
                self._order[(wid, mk, cond, 0)] = {"order_idx": oi, "order_seed": order_seed, "order_n": len(conds)}
            if self.key(wid, mk, cond, 0) in self.done:
                continue
            m = {"wid": wid, "model_key": mk, "repeat_idx": 0, "cond": cond, "order_idx": oi, "study": "study3"}
            if cond == "IE_P3":
                res = P.p3(self.llm, provider, model, HB, w, m, integrated=False)
                res.pop("integrated", None)
                res["calls"] = res.pop("selector_calls") + res["calls"]
            elif cond == "IE_RTR":
                api = QueryAPI(w["db"], w["spec"], HB, BUDGET)
                router_rule_v2(api, w["records"], w["spec"])
                api.close()
                j = P.judge(self.llm, provider, model, HB, w["IE"], render_evidence(api.responses), "RTR_judge", m)
                res = {**j, "queries": [[r["query"], r["args"], r.get("executed")] for r in api.responses], "q_used": api.used_q,
                       "records": api.used_records, "chars": api.used_chars, "responses": api.responses}
            elif cond == "IE_P4":
                res = P.p4(self.llm, provider, model, HB, w, m)
                recs = [r for x in res["responses"] for r in x.get("records", [])]
                res.update(records=len(recs), chars=sum(len(json.dumps(r, sort_keys=True)) for r in recs))
            else:
                raise ValueError(cond)
            resp = res.pop("responses")
            R = e["restrictive_source"]
            ds, roots, mine = own_decisive(w["db"], R)
            queried = sorted({x for r in resp if r.get("executed") and r["query"] == "consent_history" for x in (r["args"].get("source_ids") or [])})
            lineage_q = any(r.get("executed") and r["query"] == "lineage" for r in resp)
            consent_q = mine is not None and mine in queried
            decisive = consent_q if mine is not None else lineage_q
            self.emit(wid, mk, cond, res, handbook_c_lim_complete(w["ie_facts"], resp, w["spec"], False), repeat=0,
                      evidence=render_evidence(resp),
                      extra={"study": "study3", "world_type": e["world_type"], "arm": ARM_LABEL[cond], "h_failure": e["h_failure"],
                             "decisive_query": decisive, "decisive_consent_query": consent_q, "lineage_query": lineage_q,
                             "restrictive_source_HX": R, "own_decisive_source": mine, "consumed_roots": roots,
                             "consent_sources_queried": queried,
                             "C_lim_native": c_lim_f3_complete(w["ie_facts"], resp, w["spec"], False), "c_lim_checker": CHECKER_VERSION})


def build_jobs(index, models, arms, limit=0):
    wids = sorted(index, key=lambda w: (index[w]["case_id"], "HXB".index(index[w]["world_type"])))
    if limit:
        wids = wids[:limit]
    jobs = []
    for wid in wids:
        for mk in models:
            cs = [ARMS[a] for a in arms]
            oseed = derive_seed(ORDER_TAG, wid, mk)
            random.Random(oseed).shuffle(cs)
            jobs.append((wid, mk, cs, 0, oseed))
    return wids, jobs


def _calls(run_id):
    return [json.loads(x) for x in (_E1 / "runs" / run_id / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def projection(n_worlds=90):
    """Per-arm $ from Stage-1 realised per-call usage on F3 worlds (Sonnet c9234d, Haiku ce8cd6), cold-cache adjusted as in
    Study 2 (cold standalone selector from A3 70e644; the first judge call per (world, model) job pays the trace cache write),
    plus a Study-2-calibrated estimate (Study-2 realised/projected per tier: T1 6.67/11.82, T2 1.45/4.15, T3 0.48/0.86)."""
    wi = {e["wid"]: e["family"] for e in json.loads((_E1 / "cases" / "private" / "world_index.json").read_text(encoding="utf-8"))}
    runs = {"sonnet": "20261108T044431Z_e1main-hb_c9234d", "haiku": "20261108T044431Z_e1main-hb_ce8cd6",
            "sonnet_standalone": "20261108T053135Z_e1main-hb_70e644"}
    agg = defaultdict(lambda: defaultdict(list))
    for key, rid in runs.items():
        for c in _calls(rid):
            mt = c.get("meta", {})
            if wi.get(mt.get("wid")) == "F3":
                agg[key][(mt.get("cond"), c.get("tag"))].append(c)

    def per_world(xs):
        return sum(x.get("cost_usd") or 0 for x in xs) / max(1, len({x["meta"]["wid"] for x in xs}))

    def mean(xs, f):
        return sum((x.get(f) or 0) for x in xs) / max(1, len(xs))

    s_cold = per_world(agg["sonnet_standalone"][("IE_P3I", "P3_selector")]) / per_world(agg["sonnet"][("IE_P3", "P3_selector")])
    out = {}
    for mk in ("sonnet", "haiku"):
        pr = PRICES[MODELS[mk][1]]
        sel_cold = per_world(agg[mk][("IE_P3", "P3_selector")]) * s_cold
        jw = agg[mk][("IE_P3", "P3_judge")]
        judge_warm = per_world(jw)
        trace_tok = max(0.0, mean(jw, "cache_read_input_tokens") - mean(agg[mk][("CE_V0", "CE_V0")], "cache_read_input_tokens"))
        cold_extra = trace_tok * (pr["cw"] - pr["cr"]) / 1e6
        rtr = per_world(agg[mk][("IE_P2S", "P2_judge")])
        p4 = per_world(agg[mk][("IE_P4", "P4_judge")])
        out[mk] = {"units": {"selector_cold": round(sel_cold, 5), "judge_warm": round(judge_warm, 5), "judge_cold_extra": round(cold_extra, 5),
                             "router_judge_warm": round(rtr, 5), "p4_judge_warm": round(p4, 5)},
                   "p3orig_alone": round(n_worlds * (sel_cold + judge_warm + cold_extra), 3),   # only judge in its job -> cold
                   "router": round(n_worlds * (rtr + cold_extra / 2), 3), "p4": round(n_worlds * (p4 + cold_extra / 2), 3)}
    tiers = {"S1 (Sonnet P3-orig)": out["sonnet"]["p3orig_alone"], "S2 (Sonnet router + P4)": out["sonnet"]["router"] + out["sonnet"]["p4"],
             "S3 (Haiku P3-orig)": out["haiku"]["p3orig_alone"]}
    cal = {"S1 (Sonnet P3-orig)": 6.67 / 11.82, "S2 (Sonnet router + P4)": 1.45 / 4.15, "S3 (Haiku P3-orig)": 0.48 / 0.86}
    return {"per_model": out, "tiers_conservative": {k: round(v, 3) for k, v in tiers.items()},
            "total_conservative": round(sum(tiers.values()), 3),
            "tiers_study2_calibrated": {k: round(v * cal[k], 3) for k, v in tiers.items()},
            "total_study2_calibrated": round(sum(v * cal[k] for k, v in tiers.items()), 3), "study3_cap": 8.0, "pause_at": 7.5,
            "basis": "Stage-1 realised F3 per-call costs; 90 worlds per arm; cold-cache adjusted; Study-2 realised/projected ratios"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=sorted(TIERS), default=None)
    ap.add_argument("--models", default=None)
    ap.add_argument("--arms", default=None)
    ap.add_argument("--phase", default="study3")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--limit-worlds", type=int, default=0)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--dry-jobs", action="store_true")
    ap.add_argument("--project", action="store_true")
    ap.add_argument("--live", action="store_true", help="required for paid calls (only after the lead's explicit GO S3)")
    a = ap.parse_args()
    if a.project:
        print(json.dumps(projection(), indent=1))
        return
    models, arms = TIERS[a.tier] if a.tier else ([], [])
    models = a.models.split(",") if a.models else models
    arms = a.arms.split(",") if a.arms else arms
    if not models or not arms:
        raise SystemExit("give --tier or --models + --arms")
    if not a.fake and not a.dry_jobs and not a.live:
        raise SystemExit("live calls need --live (only after the lead's explicit GO S3)")
    man, index = load_index()
    wids, jobs = build_jobs(index, models, arms, a.limit_worlds)
    if a.dry_jobs:
        c = Counter((j[1], ARM_LABEL[x]) for j in jobs for x in j[2])
        print(f"DRY: {len(wids)} worlds, {len(jobs)} jobs, {sum(c.values())} decisions:", dict(c))
        return
    name = ("s3fake-" if a.fake else "s3-") + (a.tier or "custom")
    meta = {"study": "study3", "args": vars(a), "budget": BUDGET.__dict__, "regime": "handbook", "schema": "e1.monitor_result.v1",
            "verdict_schema_version": SCHEMA_VERSION, "code_hash": code_hash(), "router_rules_version": RULES_VERSION,
            "c_lim_checker": CHECKER_VERSION,
            "study3_code_sha256": {f: hashlib.sha256((_E1 / "monitors" / f).read_bytes()).hexdigest()
                                   for f in ("study3_run.py", "study3_router.py", "study3_build.py", "study3_checker.py", "study2_router.py")},
            "manifest_sha256": hashlib.sha256((CASES3 / "manifest.json").read_bytes()).hexdigest(), "arms": {x: ARMS[x] for x in arms},
            "models": models, "arm_order": f"per (world, model) random, seed = derive_seed('{ORDER_TAG}', wid, model)", "p3i": "off"}
    rd = RunDir(Path(a.run_dir)) if a.run_dir else new_run(_E1 / "runs", name, meta)
    print("run_dir", rd.path, flush=True)
    h = Study3Harness(rd, a.phase, a.fake, index)
    print(f"{len(wids)} worlds, {len(jobs)} jobs, {len(h.done)} results already done", flush=True)
    t0, n_done, n_err = time.time(), 0, 0
    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        futs = {ex.submit(h.run_job, *j): j for j in jobs}
        for f in as_completed(futs):
            n_done += 1
            try:
                f.result()
            except Exception as e:
                n_err += 1
                rd.log("job_errors", {"job": list(futs[f]), "error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-2000:]})
            if n_done % 30 == 0 or n_done == len(jobs):
                led = CostLedger().summary()
                print(f"[{time.time() - t0:6.0f}s] jobs {n_done}/{len(jobs)} errors {n_err} | {a.phase} settled "
                      f"${led[a.phase]['settled']:.3f} open ${led[a.phase]['open_reserved']:.3f}", flush=True)
    print("done", rd.path, flush=True)


if __name__ == "__main__":
    main()

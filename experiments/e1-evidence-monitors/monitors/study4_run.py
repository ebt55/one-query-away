"""Study 4 runner: prospective forecast of unseen monitors' exculpatory risk (OpenAI monitors; handbook v-S4).

Arm: P3-orig with an OpenAI model as BOTH selector and judge, Study-1 prompts through the Study-1 OpenAI-compatible
path of monitors/policies.py (the same code path DeepSeek used: flattened messages + function tools for the selector;
JSON-object judge with the schema text appended), via a thin adapter that routes `deepseek_chat` calls to
LLM.openai_chat. Policies/prompts/handbook files are not modified; the handbook object is v-S4 (monitors/study4_handbook.py).
Monitors (frozen by the lead, Nov 8 ~20:15 IST): gpt-5.4-mini (reasoning_effort="medium"), gpt-4.1-mini (no reasoning param).
Sets: cal  = fresh calibration H + B (cases_study4/f1cal, f3cal; X sealed, never run): 120 worlds per monitor
      eval = the EXISTING Study-2 F1 X (30) + Study-3 F3 X (30) worlds: 60 per monitor (needs --allow-eval)
Token ledger + usage guard: shared/token_ledger.py (STUDY4_MAX_TOKENS 5.5M; paid-overflow estimate <= $5).
NO LIVE CALLS without the lead's explicit GO (pass --live only then). --fake / --dry-jobs / --project are free.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
from shared.runlog import RunDir, new_run  # noqa: E402
from shared.token_ledger import TokenLedger, ceilings  # noqa: E402
from env.render import render_ie, visible_facts  # noqa: E402
from families import family  # noqa: E402
from monitors import handbook as HB  # noqa: E402
from monitors import policies as P  # noqa: E402
from monitors.harness import Harness, code_hash  # noqa: E402
from monitors.prompts import SCHEMA_VERSION  # noqa: E402
from monitors.query_api import BUDGET, render_evidence  # noqa: E402
from monitors.study2_run import s2_fake_ds_text  # noqa: E402  (fake backend only)
from monitors.study3_checker import c_lim_f3_complete, handbook_c_lim_complete  # noqa: E402
from monitors.study4_handbook import HBS4, VERSION as HANDBOOK_VERSION, self_check as handbook_self_check  # noqa: E402

MONITORS = {"gpt-5.4-mini": {"reasoning_effort": "medium"}, "gpt-4.1-mini": {"reasoning_effort": None}}
F1, F3 = family("F1"), family("F3")
FAMS = {"F1": F1, "F3": F3}


class OAAdapter:
    """Presents LLM.openai_chat under the `deepseek_chat` name used by policies.p3/judge for OpenAI-compatible providers."""

    def __init__(self, llm, model):
        self.llm, self.model, self.effort = llm, model, MONITORS[model]["reasoning_effort"]

    def deepseek_chat(self, *, messages, tools=None, json_mode=False, model=None, max_tokens=16000, tag="", meta=None):
        return self.llm.openai_chat(model=self.model, messages=messages, tools=tools, json_mode=json_mode, max_tokens=max_tokens,
                                    reasoning_effort=self.effort, tag=tag, meta=meta)


CAL_PER_TEMPLATE = 3  # lead decision, Nov 8 ~20:30 IST: first 3 seeds per template per family (balanced)


def calibration_subset():
    """Frozen calibration subset: per family and template, the 3 bases with the lowest seed index (18 per family)."""
    out = {}
    for fam, d in (("F1", "f1cal"), ("F3", "f3cal")):
        man = json.loads((_E1 / "cases_study4" / d / "manifest.json").read_text(encoding="utf-8"))
        by_t = {}
        for b in man["bases"]:
            by_t.setdefault(b["template"], []).append(b)
        out[fam] = sorted(b["base_id"] for t, bs in by_t.items() for b in sorted(bs, key=lambda x: x["seed_index"])[:CAL_PER_TEMPLATE])
    return out


def load_index(sets):
    idx = {}
    if "cal" in sets:
        subset = calibration_subset()
        for fam, d in (("F1", "f1cal"), ("F3", "f3cal")):
            man = json.loads((_E1 / "cases_study4" / d / "manifest.json").read_text(encoding="utf-8"))
            for b in man["bases"]:
                if b["base_id"] not in subset[fam]:
                    continue
                for k in ("H", "B"):  # X is sealed (cases_study4/<fam>cal/sealed_X): never run
                    idx[b["worlds"][k]] = {"wid": b["worlds"][k], "case_id": b["base_id"], "family": fam, "template": b["template"],
                                           "split": "study4_cal", "harmful": k == "H", "world_type": k, "repeat_subset": False,
                                           "folder": str(_E1 / "cases_study4" / d / "worlds"), "set": "cal"}
    if "eval" in sets:
        for fam, d in (("F1", "cases_study2"), ("F3", "cases_study3")):
            man = json.loads((_E1 / d / "manifest.json").read_text(encoding="utf-8"))
            for b in man["bases"]:
                idx[b["worlds"]["X"]] = {"wid": b["worlds"]["X"], "case_id": b["base_id"], "family": fam, "template": b["template"],
                                         "split": "study4_eval", "harmful": False, "world_type": "X", "repeat_subset": False,
                                         "folder": str(_E1 / d / "worlds"), "set": "eval"}
    return idx


class Study4Harness(Harness):
    def __init__(self, rd, fake, index):
        super().__init__(rd, "study4_openai", None, fake, regime="handbook", p3i=False, index=index, folder="worlds")
        self.p2g_differs, self.stop = None, False
        self.HB = HBS4  # emit(): labels via HB.label_sql (same functions; v-S4 differs only in policy TEXT)
        if fake:
            self.llm.fake_oa_text = s2_fake_ds_text

    def world(self, wid):
        with self.lock:
            if wid not in self._worlds:
                e = self.index[wid]
                folder = Path(e["folder"])
                d = json.loads((folder / f"{wid}.json").read_text(encoding="utf-8"))
                self._worlds[wid] = {"wid": wid, "fam": FAMS[e["family"]], "family": e["family"], "spec": d["spec"], "records": d["records"],
                                     "db": folder / f"{wid}.sqlite", "IE": render_ie(d["records"], d["spec"]),
                                     "ie_facts": visible_facts(d["records"], "IE")}
            return self._worlds[wid]

    def run_job(self, wid, model, conds, repeat=0, order_seed=None):
        if self.stop or self.key(wid, model, "IE_P3", 0) in self.done:
            return
        w, e = self.world(wid), self.index[wid]
        m = {"wid": wid, "model_key": model, "repeat_idx": 0, "cond": "IE_P3", "study": "study4", "set": e["set"]}
        res = P.p3(OAAdapter(self.llm, model), "deepseek", model, HBS4, w, m, integrated=False)
        res.pop("integrated", None)
        res["calls"] = res.pop("selector_calls") + res["calls"]
        if any(c.get("outcome") == "budget_refused" for c in res["calls"]):
            self.stop = True  # usage guard / token ceiling tripped: stop submitting new work (the run pauses)
        resp = res.pop("responses")
        if e["family"] == "F3":
            cl, nat = handbook_c_lim_complete(w["ie_facts"], resp, w["spec"], False), c_lim_f3_complete(w["ie_facts"], resp, w["spec"], False)
        else:
            cl, nat = HB.c_lim(F1, w["ie_facts"], resp, w["spec"], False), F1.c_lim(w["ie_facts"], resp, w["spec"], False)
        self.emit(wid, model, "IE_P3", res, cl, repeat=0, evidence=render_evidence(resp),
                  extra={"study": "study4", "set": e["set"], "world_type": e["world_type"], "arm": "p3orig", "monitor": model,
                         "reasoning_effort": MONITORS[model]["reasoning_effort"], "handbook_version": HANDBOOK_VERSION, "C_lim_native": nat})


def build_jobs(index, models, limit=0):
    wids = sorted(index, key=lambda w: (index[w]["set"], index[w]["family"], index[w]["case_id"], index[w]["world_type"]))
    if limit:
        wids = wids[:limit]
    return wids, [(wid, mk, ["IE_P3"], 0, None) for mk in models for wid in wids]


# ------------------------------------------------------------------ token estimates (no calls)
def projection():
    """Per monitor: 120 calibration (F1 H/B, F3 H/B) + 60 evaluation (F1 X, F3 X) P3 world evaluations. Per-world tokens =
    Sonnet P3-orig selector + judge calls in Studies 2/3 (prompt = input + cache read + cache write; output incl. thinking).
    OpenAI tokenization differs (+/-15%); gpt-5.4-mini output x2 allowance for reasoning tokens."""
    runs = {"F1": "20261108T114814Z_s2-T1_7d74dc", "F3": "20261108T123249Z_s3-S1_631230"}
    per = {}
    for fam, rid in runs.items():
        wt = {}
        for line in (_E1 / "runs" / rid / "monitor_results.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r["arm"] == "p3orig":
                wt[r["pid"]] = r["world_type"]
        agg = defaultdict(lambda: [0, 0, set()])
        for line in (_E1 / "runs" / rid / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines():
            c = json.loads(line)
            mt = c.get("meta", {})
            if mt.get("cond") != "IE_P3" or mt.get("wid") not in wt:
                continue
            a = agg[wt[mt["wid"]]]
            a[0] += (c.get("input_tokens") or 0) + (c.get("cache_read_input_tokens") or 0) + (c.get("cache_creation_input_tokens") or 0)
            a[1] += c.get("output_tokens") or 0
            a[2].add(mt["wid"])
        per[fam] = {k: {"prompt_per_world": round(v[0] / len(v[2])), "output_per_world": round(v[1] / len(v[2]))} for k, v in agg.items()}
    plan = {"cal": [("F1", "H"), ("F1", "B"), ("F3", "H"), ("F3", "B")], "eval": [("F1", "X"), ("F3", "X")]}
    n_per_cell = {"cal": 6 * CAL_PER_TEMPLATE, "eval": 30}
    out = {}
    for mon, mult in (("gpt-5.4-mini", 2.0), ("gpt-4.1-mini", 1.0)):
        o = {}
        for s, cells in plan.items():
            pin = sum(n_per_cell[s] * per[f][t]["prompt_per_world"] for f, t in cells)
            pout = sum(n_per_cell[s] * per[f][t]["output_per_world"] * mult for f, t in cells)
            o[s] = {"prompt_tokens": int(pin), "output_tokens": int(pout), "total": int(pin + pout)}
        o["all"] = {k: o["cal"][k] + o["eval"][k] for k in ("prompt_tokens", "output_tokens", "total")}
        out[mon] = o
    grand = sum(out[m]["all"]["total"] for m in out)
    cal_total = sum(out[m]["cal"]["total"] for m in out)
    ev_total = sum(out[m]["eval"]["total"] for m in out)

    def over_usd(tokens, key):  # conservative $1/M in, $4/M out on the share beyond 2.4M free in one UTC day
        pin = sum(out[m][key]["prompt_tokens"] for m in out)
        pout = sum(out[m][key]["output_tokens"] for m in out)
        return max(0, tokens - 2_400_000) * (pin * 1.0 + pout * 4.0) / (pin + pout) / 1e6
    return {"per_world_sonnet_tokens": per, "per_monitor": out, "grand_total_tokens": grand, "calibration_total_tokens": cal_total,
            "evaluation_total_tokens": ev_total, "calibration_worlds_per_monitor": 4 * n_per_cell["cal"],
            "limits": {"STUDY4_MAX_TOKENS": ceilings()[0], "free_assumed_per_utc_day": 2_400_000, "max_paid_overflow_usd": 5.50},
            "paid_overflow_usd_estimate_day_split": {"calibration_day (UTC Nov 8)": round(over_usd(cal_total, "cal"), 3),
                                                     "evaluation_day (UTC Nov 9)": round(over_usd(ev_total, "eval"), 3),
                                                     "total": round(over_usd(cal_total, "cal") + over_usd(ev_total, "eval"), 3)},
            "basis": "Sonnet P3-orig token counts (Study 2 T1, Study 3 S1); OpenAI tokenization differs by roughly +/-15%"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=sorted(MONITORS), default=None)
    ap.add_argument("--set", choices=["cal", "eval"], default="cal")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--limit-worlds", type=int, default=0)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--dry-jobs", action="store_true")
    ap.add_argument("--project", action="store_true")
    ap.add_argument("--allow-eval", action="store_true", help="evaluation X runs: only after the forecasts are frozen and the lead's GO")
    ap.add_argument("--live", action="store_true", help="paid/token-consuming calls: only after the lead's explicit GO")
    a = ap.parse_args()
    if a.project:
        print(json.dumps(projection(), indent=1))
        return
    if not a.model:
        raise SystemExit("--model is required")
    if a.set == "eval" and not a.allow_eval:
        raise SystemExit("evaluation set needs --allow-eval (after forecasts are frozen + GO)")
    import datetime as _dt
    if a.set == "eval" and a.live and _dt.datetime.now(_dt.timezone.utc).date().isoformat() < "2026-11-09":
        raise SystemExit("evaluation X runs only after 05:30 IST Nov 9 (new UTC day of free allowance; lead decision)")
    if not a.fake and not a.dry_jobs and not a.live:
        raise SystemExit("live calls need --live (only after the lead's explicit GO)")
    hs = handbook_self_check()
    assert hs["exactly_one_added"] and hs["study1_handbook_unchanged"], hs
    index = load_index([a.set])
    wids, jobs = build_jobs(index, [a.model], a.limit_worlds)
    if a.dry_jobs:
        print(f"DRY: {a.model} set={a.set}: {len(wids)} worlds, {len(jobs)} decisions:",
              dict(Counter((index[w]["family"], index[w]["world_type"]) for w in wids)))
        return
    name = ("s4fake-" if a.fake else "s4-") + f"{a.set}-{a.model}"
    meta = {"study": "study4", "args": vars(a), "budget": BUDGET.__dict__, "regime": "handbook", "handbook_version": HANDBOOK_VERSION,
            "handbook_self_check": hs, "schema": "e1.monitor_result.v1", "verdict_schema_version": SCHEMA_VERSION, "code_hash": code_hash(),
            "monitor": a.model, "reasoning_effort": MONITORS[a.model]["reasoning_effort"], "provider": "openai", "set": a.set,
            "openai_path": "policies.p3 OpenAI-compatible branch via OAAdapter -> LLM.openai_chat (chat.completions; json_object judge)",
            "study4_code_sha256": {f: hashlib.sha256((_E1 / "monitors" / f).read_bytes()).hexdigest()
                                   for f in ("study4_run.py", "study4_handbook.py", "study4_build.py", "study4_replay.py")},
            "token_guard": TokenLedger().guard()}
    rd = RunDir(Path(a.run_dir)) if a.run_dir else new_run(_E1 / "runs", name, meta)
    print("run_dir", rd.path, flush=True)
    h = Study4Harness(rd, a.fake, index)
    print(f"{len(wids)} worlds, {len(jobs)} jobs, {len(h.done)} done", flush=True)
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
            if n_done % 20 == 0 or n_done == len(jobs):
                g = TokenLedger().guard() if not a.fake else {}
                print(f"[{time.time() - t0:6.0f}s] jobs {n_done}/{len(jobs)} errors {n_err} | tokens today {g.get('tokens_today')} "
                      f"open {g.get('open_reserved')} paid-est ${g.get('paid_overflow_usd_estimate')}" + (" | PAUSED (guard)" if h.stop else ""), flush=True)
    print(("PAUSED " if h.stop else "done ") + str(rd.path), flush=True)


if __name__ == "__main__":
    main()

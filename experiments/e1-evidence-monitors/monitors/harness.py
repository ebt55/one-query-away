"""E1 monitor harness: bounded-concurrency synchronous calls, resumable, every call ledger-debited.

Result key = (wid, model, condition, repeat). A finished key is never re-run; budget-refused conditions are logged to
`skipped.jsonl` and retried on resume. Jobs are ordered primary-first so a phase cap truncates secondary conditions.
Refuses F4 and non-dev splits unless --allow-split is given (main/F4 wait for the G2 protocol freeze).

Usage (from experiments/):
  .venv/Scripts/python.exe e1-evidence-monitors/monitors/harness.py --split dev --models sonnet,haiku,deepseek \
      --phase e1_pilot --concurrency 12 [--run-dir e1-evidence-monitors/runs/<run_id>] [--fake]

Subset filters (Stage 2 add-backs, lead D1):
  --conds C1,C2,...        keep only these conditions (intersected with the tier's list). IE_P3I alone = STANDALONE P3I:
                           selector re-run + integrated verdict, no fresh judge; emits only IE_P3I rows (notes.p3i_mode).
  --repeat-n N             N in {18,12,6}: the repeat subset is the first N/3 test base cases per family in the
                           pre-registered seed order sha256("repeat-subset-v1|case_id") (the order that selected the 18).
  --repeat-indices 1,2     run only these repeat indices (e.g. k=3 add-back on top of an existing k=0 run).
  --test-subset all|repeat|nonrepeat   restrict TEST-split worlds to the (N-limited) repeat subset or its complement;
                           other splits are unaffected (A3: calibration + repeat-subset test; A4: remaining test).
  --case-ids ID,... | @file   keep only these base cases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from shared.cost_ledger import CostLedger  # noqa: E402
from shared.llm import LLM  # noqa: E402
from shared.runlog import RunDir, new_run  # noqa: E402
from families.base import GENERATOR_VERSION  # noqa: E402
from monitors import policies as P  # noqa: E402
from monitors.query_api import BUDGET  # noqa: E402
from monitors.prompts import SCHEMA_VERSION  # noqa: E402
from monitors.query_api import render_evidence  # noqa: E402
from shared.llm import CHARS_PER_TOKEN_HEURISTIC, DEEPSEEK_THINKING  # noqa: E402
from shared.seeds import derive_seed  # noqa: E402
from monitors.worlds import CASES, load_world  # noqa: E402

MODELS = {"sonnet": ("anthropic", "claude-sonnet-5-5"), "haiku": ("anthropic", "claude-haiku-5-5"),
          "opus": ("anthropic", "claude-opus-5-5"), "deepseek": ("deepseek", "deepseek-flash")}
PRIMARY = {"CE": ["CE_V0", "CE_V2"], "IE": ["IE_P1", "IE_P2", "IE_P3", "IE_P4"]}  # P3I is produced with P3; P4 runs with primary (lead plan A/B)
SECONDARY = {"CE": ["CE_V1", "CE_VSOLO", "CE_VSEP", "CE_V3P"], "IE": []}
DEEPSEEK_CONDS = {"CE_V0", "CE_V2", "IE_P1", "IE_P2", "IE_P3", "IE_P2G", "IE_P2S"}
# HANDBOOK regime (behind --regime handbook): 8-policy handbook prompt, unified API, P2g (generic) + P2s (policy-derived)
HB_PRIMARY = {"CE": ["CE_V0", "CE_V2"], "IE": ["IE_P1", "IE_P2G", "IE_P2S", "IE_P3", "IE_P4"]}
HB_SECONDARY = {"CE": ["CE_V1", "CE_VSOLO", "CE_VSEP", "CE_V3P"], "IE": []}
RESULT_SCHEMA = "e1.monitor_result.v1"
ORDER_SEED_TAG = "cond-order-v1"  # A4 R12: per-world random condition order, seed logged per row (notes.order_seed)
P2G_RULE = "p2g_v1_cross_actor"  # current P2G (lead-specified after A3 R6); p2g_v0 rows are superseded
REPEAT_ORDER_TAG = "repeat-subset-v1"  # make_cases.py: repeat subset = first round(20%) test cases per family in this order
VSOLO_AGENT = "single-identity"  # V-solo = whole trace attributed to ONE agent identity (see INTERFACE.md)
CODE_DIRS = ("env", "families", "monitors")


def code_hash() -> str:
    h = hashlib.sha256()
    for d in CODE_DIRS:
        for f in sorted((_E1 / d).glob("*.py")):
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def fake_responder(kwargs):
    """Offline stand-in used only with --fake to exercise code paths. Never a result."""
    usage = {"input_tokens": len(json.dumps(kwargs["messages"], default=str)) // 3, "output_tokens": 40,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    n_res = sum(1 for m in kwargs["messages"] if m["role"] == "user" and isinstance(m["content"], list)
                and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"]))
    if kwargs.get("tools") and "format" not in kwargs.get("output_config", {}) and n_res < 2:
        t = kwargs["tools"][n_res % len(kwargs["tools"])]
        args = {k: ("2026-12-31T00:00:00Z" if k == "as_of" else (["x"] if v.get("type") == "array" else (1 if v.get("type") == "integer" else "x")))
                for k, v in t["input_schema"]["properties"].items() if k in t["input_schema"]["required"]}
        return {"content": [{"type": "tool_use", "id": f"toolu_f{n_res}", "name": t["name"], "input": args}], "usage": usage, "stop_reason": "tool_use"}
    if "format" in kwargs.get("output_config", {}):
        return {"content": [{"type": "text", "text": json.dumps({"verdict": "insufficient_evidence", "suspicion": 5, "rationale": "fake"})}],
                "usage": usage, "stop_reason": "end_turn"}
    return {"content": [{"type": "text", "text": "DONE"}], "usage": usage, "stop_reason": "end_turn"}


class Harness:
    def __init__(self, rd: RunDir, phase: str, sub: str | None, fake: bool, regime: str = "targeted", p3i: bool = True,
                 index: dict | None = None, folder: str = "worlds"):
        self.rd, self.lock = rd, threading.Lock()
        self.regime, self.p3i = regime, p3i
        self.HB = None
        if regime == "handbook":
            from monitors import handbook as HB
            self.HB = HB
        self.llm = LLM(rd, phase=phase, sub=sub, backend="fake" if fake else "anthropic", fake_responder=fake_responder,
                       use_count_tokens=False)
        self.done = set()
        p = rd.path / "monitor_results.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.done.add(self.key(r["pid"], r["model"], r["cond"], r["repeat_idx"]))
        self._worlds = {}
        self.folder = folder
        self.index = index if index is not None else {e["wid"]: e for e in json.loads((CASES / "private" / "world_index.json").read_text(encoding="utf-8"))}
        # per-pair flag for A4's pre-specified breakdown: does the P2G rule's evidence differ between the twins of this base
        # case? Deterministic, written by monitors/post_checks.py at generation time (no model outputs involved).
        fp = (CASES / "f4_sealed" if folder == "f4_sealed" else CASES / "private") / "p2g_evidence_differs.json"
        self.p2g_differs = json.loads(fp.read_text(encoding="utf-8"))["by_case"] if fp.exists() else None
        if self.p2g_differs is None and not fake:
            raise SystemExit(f"missing {fp} (run monitors/post_checks.py)")

    @staticmethod
    def key(wid, mk, cond, repeat=0):
        return f"{wid}|{mk}|{cond}|{repeat}"

    def world(self, wid):
        with self.lock:
            if wid not in self._worlds:
                self._worlds[wid] = load_world(wid, self.folder, allow_sealed=(self.folder == "f4_sealed"))
            return self._worlds[wid]

    def emit(self, wid, mk, cond, res, c_lim, repeat=0, evidence=None, extra=None):
        """Write one e1.monitor_result.v1 record (analysis/REQUESTS.md R1)."""
        e = self.index[wid]
        w = self.world(wid)
        labeller = self.HB if self.HB is not None else w["fam"]
        c_full = "violation" if labeller.label_sql(w["db"])["violation"] else "compliant"
        row = {"schema": RESULT_SCHEMA, "run_id": self.rd.run_id, "base_case_id": e["case_id"], "family": e["family"],
               "template": e["template"], "split": e["split"], "designed_harmful": bool(e["harmful"]), "label": c_full == "violation",
               "cond": cond, "model": mk, "pid": wid, "parsed": res["parsed"], "calls": res.get("calls", []), "C_lim": c_lim,
               "repeat_idx": repeat, "repeat_subset": bool(e.get("repeat_subset", False)), "generator_version": GENERATOR_VERSION,
               "C_full": c_full}
        for k in ("queries", "q_used", "records", "chars", "selector_stop", "refused"):
            if k in res:
                row[k] = res[k]
        if evidence is not None:
            row["evidence_tokens"] = int(len(evidence) / CHARS_PER_TOKEN_HEURISTIC) + 1
            row["evidence_text_sha256"] = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
        row.update(extra or {})
        if cond.startswith("IE_"):
            row["p2g_evidence_differs"] = None if self.p2g_differs is None else bool(self.p2g_differs[e["case_id"]])
        pid = row["parsed"].pop("_policy_id", None) if isinstance(row["parsed"], dict) else None
        note = json.loads(row["notes"]) if "notes" in row else {}
        if self.HB is not None:
            note.update({"regime": "handbook", "policy_id": pid, "p2g_rule": P2G_RULE})
        om = getattr(self, "_order", {})
        order = om.get((wid, mk, cond, repeat)) or (om.get((wid, mk, "IE_P3", repeat)) if cond == "IE_P3I" else None)
        if order:
            note.update(order)  # A4 R12: condition order within the world's job (cache-dependent cost)
        if note:
            row["notes"] = json.dumps(note, sort_keys=True)
        if any(c.get("outcome") == "budget_refused" for c in row["calls"]) or res["parsed"].get("outcome") == "budget_refused":
            self.rd.log("skipped", {**row, "reason": "budget_refused"})
            return
        self.rd.log("monitor_results", row)
        with self.lock:
            self.done.add(self.key(wid, mk, cond, repeat))

    def run_job(self, wid, mk, conds, repeat=0, order_seed=None):
        provider, model = MODELS[mk]
        w = self.world(wid)
        native = w["fam"]
        fam = self.HB if self.HB is not None else native  # prompts / API / P2 rule source

        def clim(facts, resp, complete):
            if self.HB is not None:
                return self.HB.c_lim(native, facts, resp, w["spec"], complete)
            return native.c_lim(facts, resp, w["spec"], complete)

        def nat(resp):  # C_lim_native: the native family policy only, on exactly the IE facts the monitor had
            return {"C_lim_native": native.c_lim(w["ie_facts"], resp, w["spec"], False)}
        meta = {"wid": wid, "model_key": mk, "repeat_idx": repeat}
        R = repeat
        self._order = getattr(self, "_order", {})
        for oi, cond in enumerate(conds):
            with self.lock:
                self._order[(wid, mk, cond, R)] = {"order_idx": oi, "order_seed": order_seed, "order_n": len(conds)}
            if self.key(wid, mk, cond, R) in self.done:
                continue
            m = {**meta, "cond": cond, "order_idx": oi}
            if cond in ("CE_V0", "CE_V1", "CE_V2", "CE_V3P"):
                res = P.judge(self.llm, provider, model, fam, w[cond[3:]], None, cond, m)
                self.emit(wid, mk, cond, res, clim(w["ce_facts"], [], True), repeat=R)
            elif cond == "CE_VSOLO":
                res = P.judge(self.llm, provider, model, fam, w["VSOLO"], None, cond, m)
                self.emit(wid, mk, cond, res, clim(w["ce_facts"], [], True), repeat=R, extra={"view_agent": VSOLO_AGENT})
            elif cond == "CE_VSEP":
                res = P.vsep(self.llm, provider, model, fam, w["VSEP"], m)
                self.emit(wid, mk, cond, res, None, repeat=R, extra={"notes": json.dumps({"per_agent": res.get("per_agent")}, sort_keys=True)})
            elif cond == "IE_P1":
                res = P.judge(self.llm, provider, model, fam, w["IE"], None, "IE_P1", m)
                self.emit(wid, mk, cond, res, clim(w["ie_facts"], [], False), repeat=R, extra=nat([]))
            elif cond in ("IE_P2", "IE_P2S"):
                res = P.p2(self.llm, provider, model, fam, w, m)
                resp = res.pop("responses")
                self.emit(wid, mk, cond, res, clim(w["ie_facts"], resp, False), repeat=R, evidence=render_evidence(resp), extra=nat(resp))
            elif cond == "IE_P2G":
                res = P.p2g(self.llm, provider, model, fam, w, m)
                resp = res.pop("responses")
                self.emit(wid, mk, cond, res, clim(w["ie_facts"], resp, False), repeat=R, evidence=render_evidence(resp), extra=nat(resp))
            elif cond == "IE_P3":
                res = P.p3(self.llm, provider, model, fam, w, m, integrated=(provider == "anthropic" and self.p3i and R == 0))  # P3I on k=0 only
                resp = res.pop("responses")
                cl = clim(w["ie_facts"], resp, False)
                integ = res.pop("integrated")
                res["calls"] = res.pop("selector_calls") + res["calls"]
                ev = render_evidence(resp)
                self.emit(wid, mk, "IE_P3", res, cl, repeat=R, evidence=ev, extra=nat(resp))
                if integ is not None:
                    self.emit(wid, mk, "IE_P3I", {**integ, "queries": res["queries"], "q_used": res["q_used"], "records": res["records"],
                                                  "chars": res["chars"], "refused": res["refused"]}, cl, repeat=R, evidence=ev,
                              extra={**nat(resp), "notes": json.dumps({"p3i_mode": "shared_selector"})})
            elif cond == "IE_P3I":  # STANDALONE P3I (Stage 2 add-back): own selector run + integrated verdict, no fresh judge
                if provider != "anthropic":
                    raise ValueError("IE_P3I is Anthropic-only")
                res = P.p3(self.llm, provider, model, fam, w, m, integrated=True, fresh_judge=False)
                resp = res.pop("responses")
                integ = res.pop("integrated")
                if integ is None:  # selector failed (refusal/error): log the selector outcome as the P3I result
                    integ = {"calls": [], "parsed": {"verdict": None, "outcome": res["selector_stop"]}}
                self.emit(wid, mk, "IE_P3I", {**integ, "calls": res["selector_calls"] + integ["calls"], "queries": res["queries"],
                                              "q_used": res["q_used"], "records": res["records"], "chars": res["chars"],
                                              "refused": res["refused"], "selector_stop": res["selector_stop"]},
                          clim(w["ie_facts"], resp, False), repeat=R, evidence=render_evidence(resp),
                          extra={**nat(resp), "notes": json.dumps({"p3i_mode": "standalone_selector"})})
            elif cond == "IE_P4":
                res = P.p4(self.llm, provider, model, fam, w, m)
                resp = res.pop("responses")
                recs = [r for x in resp for r in x.get("records", [])]
                res.update(records=len(recs), chars=sum(len(json.dumps(r, sort_keys=True)) for r in recs))
                self.emit(wid, mk, cond, res, clim(w["ie_facts"], resp, False), repeat=R, evidence=render_evidence(resp), extra=nat(resp))
            else:
                raise ValueError(cond)


def secondary_subset(index, scope):
    """Worlds that receive SECONDARY conditions. scope: all | calibration | calibration+test30 (30 test base cases,
    10 per family, chosen by seed hash before any outputs)."""
    if scope == "all":
        return None, []
    cases = sorted({e["case_id"]: e for e in index.values()}.values(), key=lambda e: e["case_id"])
    chosen = {e["case_id"] for e in cases if e["split"] == "calibration"}
    test30 = []
    if scope == "calibration+test30":
        for fam in ("F1", "F2", "F3"):
            ids = [e["case_id"] for e in cases if e["split"] == "test" and e["family"] == fam]
            test30 += sorted(ids, key=lambda x: hashlib.sha256(f"secondary-subset-v1|{x}".encode()).hexdigest())[:10]
        chosen |= set(test30)
    elif scope != "calibration":
        raise ValueError(scope)
    return {w for w, e in index.items() if e["case_id"] in chosen}, sorted(test30)


def repeat_cases(index, n=None):
    """Repeat-subset base cases. n=None: the pre-registered subset (index flag). n in {18,12,6}: the first n/3 per family in
    the pre-registered seed order sha256("repeat-subset-v1|case_id") restricted to the subset (so 18 == the full subset)."""
    cases = {e["case_id"]: e for e in index.values() if e.get("repeat_subset")}
    if n is None:
        return set(cases)
    if n % 3:
        raise ValueError("--repeat-n must be a multiple of 3 (n/3 per family)")
    out = set()
    for fam in sorted({e["family"] for e in cases.values()}):
        ids = sorted((c for c, e in cases.items() if e["family"] == fam),
                     key=lambda x: hashlib.sha256(f"{REPEAT_ORDER_TAG}|{x}".encode()).hexdigest())
        if len(ids) < n // 3:
            raise ValueError(f"repeat subset of {fam} has {len(ids)} < {n // 3} cases")
        out |= set(ids[:n // 3])
    return out


def build_jobs(wids, models, tiers, index, repeats=1, secondary_wids=None, deepseek_repeats=False, regime="targeted",
               conds_filter=None, repeat_set=None, repeat_indices=None):
    """Primary tier first (all worlds; k=`repeats` on repeat base cases), then secondary (repeat 0 only, restricted to
    `secondary_wids` if given). DeepSeek runs primary conditions only; its repeats are optional.
    conds_filter: keep only these conditions ('IE_P3I' = standalone P3I). repeat_set: base cases eligible for repeat>0
    (default: index repeat_subset). repeat_indices: run only these repeat indices."""
    jobs = []
    for tier in tiers:
        if regime == "handbook":
            conds = HB_PRIMARY if tier == "primary" else HB_SECONDARY
        else:
            conds = PRIMARY if tier == "primary" else SECONDARY
        for rep in range(repeats if tier == "primary" else 1):
            for wid in wids:
                if rep > 0 and not (index[wid]["case_id"] in repeat_set if repeat_set is not None else index[wid].get("repeat_subset")):
                    continue
                if repeat_indices is not None and rep not in repeat_indices:
                    continue
                if tier == "secondary" and secondary_wids is not None and wid not in secondary_wids:
                    continue
                for mk in models:
                    if MODELS[mk][0] == "deepseek" and rep > 0 and not deepseek_repeats:
                        continue
                    cs = [c for st in ("CE", "IE") for c in conds[st] if MODELS[mk][0] == "anthropic" or c in DEEPSEEK_CONDS]
                    if conds_filter is not None:
                        cs = [c for c in cs if c in conds_filter]
                        if "IE_P3I" in conds_filter and tier == "primary" and MODELS[mk][0] == "anthropic" and "IE_P3" not in conds_filter:
                            cs.append("IE_P3I")  # standalone P3I (no IE_P3 row in this run)
                    if cs:
                        oseed = derive_seed(ORDER_SEED_TAG, wid, mk, rep, tier)
                        random.Random(oseed).shuffle(cs)
                        jobs.append((wid, mk, cs, rep, oseed))
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", help="comma list, e.g. calibration,test")
    ap.add_argument("--families", default="F1,F2,F3")
    ap.add_argument("--models", default="sonnet,haiku,deepseek")
    ap.add_argument("--tiers", default="primary,secondary")
    ap.add_argument("--phase", default="e1_pilot")
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--limit-worlds", type=int, default=0)
    ap.add_argument("--repeats", type=int, default=1, help="k for repeat_subset base cases (primary tier)")
    ap.add_argument("--secondary-scope", default="all", choices=["all", "calibration", "calibration+test30"])
    ap.add_argument("--deepseek-repeats", action="store_true")
    ap.add_argument("--regime", default="targeted", choices=["targeted", "handbook"])
    ap.add_argument("--no-p3i", action="store_true", help="skip the integrated P3 variant")
    ap.add_argument("--unseal-f4", action="store_true", help="F4 CONFIRMATION ONLY (after G2 + lead OK): run the sealed F4 worlds")
    ap.add_argument("--dry-jobs", action="store_true", help="print the job plan and exit (loads no worlds, makes no calls)")
    ap.add_argument("--allow-split", action="store_true")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--conds", default=None, help="comma list of conditions to keep (IE_P3I alone = standalone P3I)")
    ap.add_argument("--repeat-n", type=int, default=None, choices=[18, 12, 6], help="repeat subset size: first n/3 per family (seed order)")
    ap.add_argument("--repeat-indices", default=None, help="comma list of repeat indices to run, e.g. 1,2")
    ap.add_argument("--test-subset", default="all", choices=["all", "repeat", "nonrepeat"], help="restrict TEST worlds to the repeat subset or its complement")
    ap.add_argument("--case-ids", default=None, help="comma list of base case ids, or @file with one id per line")
    a = ap.parse_args()
    splits = a.split.split(",")
    if ("F4" in a.families or "f4" in splits) and not a.unseal_f4:
        raise SystemExit("F4 is sealed until the confirmation run (use --unseal-f4 --split f4 --families F4 --phase e1_f4)")
    if a.unseal_f4 and (splits != ["f4"] or a.families != "F4" or a.phase != "e1_f4" or not a.allow_split):
        raise SystemExit("--unseal-f4 requires --split f4 --families F4 --phase e1_f4 --allow-split")
    if splits != ["dev"] and not a.allow_split:
        raise SystemExit("only the dev split may run before the G2 protocol freeze (use --allow-split after G2)")
    if a.unseal_f4:  # F4 index from the sealed key (labels stay evaluator-side; worlds load from cases/f4_sealed)
        man = {c["case_id"]: c for c in json.loads((CASES / "f4_manifest.json").read_text(encoding="utf-8"))["cases"]}
        index = {e["wid"]: {"wid": e["wid"], "case_id": e["case_id"], "split": "f4", "family": "F4", "template": e["template"],
                            "harmful": e["harmful"], "seed": man[e["case_id"]]["seed"], "repeat_subset": False}
                 for e in json.loads((CASES / "f4_sealed" / "sealed_key.json").read_text(encoding="utf-8"))}
    else:
        index = {e["wid"]: e for e in json.loads((CASES / "private" / "world_index.json").read_text(encoding="utf-8"))}
    sec_wids, test30 = secondary_subset(index, a.secondary_scope)
    fams = set(a.families.split(","))
    wids = [w for w, e in sorted(index.items(), key=lambda kv: (kv[1]["case_id"], not kv[1]["harmful"]))
            if e["split"] in splits and e["family"] in fams]
    rset = repeat_cases(index, a.repeat_n) if (a.repeat_n or a.test_subset != "all") else None
    if a.test_subset != "all":
        keep = (lambda e: e["case_id"] in rset) if a.test_subset == "repeat" else (lambda e: e["case_id"] not in rset)
        wids = [w for w in wids if index[w]["split"] != "test" or keep(index[w])]
    if a.case_ids:
        ids = (Path(a.case_ids[1:]).read_text(encoding="utf-8").split() if a.case_ids.startswith("@") else a.case_ids.split(","))
        unknown = set(ids) - {e["case_id"] for e in index.values()}
        if unknown:
            raise SystemExit(f"unknown case ids: {sorted(unknown)[:5]}")
        wids = [w for w in wids if index[w]["case_id"] in set(ids)]
    if a.limit_worlds:
        wids = wids[:a.limit_worlds]
    conds_filter = set(a.conds.split(",")) if a.conds else None
    if conds_filter:
        allc = {c for d in (PRIMARY, SECONDARY, HB_PRIMARY, HB_SECONDARY) for st in d.values() for c in st} | {"IE_P3I"}
        if conds_filter - allc:
            raise SystemExit(f"unknown conditions: {sorted(conds_filter - allc)}")
        if "IE_P3I" in conds_filter and a.no_p3i:
            raise SystemExit("--conds IE_P3I conflicts with --no-p3i")
    rep_idx = {int(x) for x in a.repeat_indices.split(",")} if a.repeat_indices else None
    jobs = build_jobs(wids, a.models.split(","), a.tiers.split(","), index, a.repeats, sec_wids, a.deepseek_repeats, a.regime,
                      conds_filter=conds_filter, repeat_set=rset, repeat_indices=rep_idx)
    if a.dry_jobs:
        from collections import Counter
        calls = Counter((j[1], c, "k0" if j[3] == 0 else "rep") for j in jobs for c in j[2])
        p3i_on = not a.no_p3i and (conds_filter is None or "IE_P3I" in conds_filter)
        for j in jobs:  # shared-selector P3I rows are produced inside IE_P3 (Anthropic, repeat 0, P3I on)
            if p3i_on and "IE_P3" in j[2] and j[3] == 0 and MODELS[j[1]][0] == "anthropic":
                calls[(j[1], "IE_P3I(shared, with IE_P3)", "k0")] += 1
        print(f"DRY: {len(wids)} worlds ({len({index[w]['case_id'] for w in wids})} base cases), {len(jobs)} jobs; "
              f"repeat set: {'index flag' if rset is None else len(rset)}; decisions by (model, cond, k):")
        for k, v in sorted(calls.items()):
            print("   ", k, v)
        print("    total decisions:", sum(calls.values()), "| by model:", dict(Counter(j[1] for j in jobs for _ in j[2])))
        return
    name = "e1fake" if a.fake else {"e1_main": "e1main", "e1_f4": "e1f4"}.get(a.phase, "e1pilot") + ("-hb" if a.regime == "handbook" else "")
    rd = RunDir(Path(a.run_dir)) if a.run_dir else new_run(_E1 / "runs", name, {
        "args": vars(a), "budget": BUDGET.__dict__, "generator_version": GENERATOR_VERSION, "schema": RESULT_SCHEMA,
        "verdict_schema_version": SCHEMA_VERSION, "code_hash": code_hash(), "code_hash_dirs": list(CODE_DIRS),
        "evidence_tokens_method": f"heuristic: int(len(evidence_text)/{CHARS_PER_TOKEN_HEURISTIC})+1",
        "vsolo_definition": "whole CE trace, chronological, every agent action attributed to one identity",
        "secondary_scope": a.secondary_scope, "secondary_test_subset": test30, "regime": a.regime,
        "p2g_rule": P2G_RULE if a.regime == "handbook" else None, "deepseek_thinking": DEEPSEEK_THINKING,
        "p3i_scope": ("standalone (selector re-run, no fresh judge)" if conds_filter and "IE_P3I" in conds_filter and "IE_P3" not in conds_filter
                      else ("off" if a.no_p3i or (conds_filter and "IE_P3I" not in conds_filter) else "repeat_idx 0 only, shared selector with IE_P3")),
        "filters": {"conds": sorted(conds_filter) if conds_filter else None, "repeat_n": a.repeat_n, "repeat_indices": sorted(rep_idx) if rep_idx else None,
                    "test_subset": a.test_subset, "case_ids": a.case_ids, "repeat_cases": sorted(rset) if rset is not None else None},
        "p2g_evidence_differs": "per IE row; deterministic twin comparison of P2G evidence (cases/private/p2g_evidence_differs.json)", "cond_order": f"per-world random, seed = derive_seed('{ORDER_SEED_TAG}', wid, model, repeat, tier)",
        "regime_note": ("handbook: 8-policy handbook prompt (RP-1..RP-8), unified query API, IE_P2G generic acquisition, IE_P2S "
                        "policy-derived checklist; label = any of 8 policies (C-full); notes carry policy_id") if a.regime == "handbook" else "targeted"})
    print("run_dir", rd.path, flush=True)
    h = Harness(rd, a.phase, None, a.fake, regime=a.regime, p3i=not a.no_p3i and (conds_filter is None or "IE_P3I" in conds_filter), index=index,
                folder="f4_sealed" if a.unseal_f4 else "worlds")
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
                j = futs[f]
                rd.log("job_errors", {"job": [j[0], j[1], j[2], j[3], j[4]], "error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-2000:]})
            if n_done % 10 == 0 or n_done == len(jobs):
                led = CostLedger().summary()
                print(f"[{time.time() - t0:6.0f}s] jobs {n_done}/{len(jobs)} errors {n_err} | {a.phase} settled ${led[a.phase]['settled']:.3f} "
                      f"open ${led[a.phase]['open_reserved']:.3f} | deepseek ${led['deepseek']['settled']:.3f}", flush=True)
    print("done", rd.path, flush=True)


if __name__ == "__main__":
    main()

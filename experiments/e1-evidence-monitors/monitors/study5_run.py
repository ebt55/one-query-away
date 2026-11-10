"""Study 5 runner (project-selection/preregistration_study5.md): arm P3-sequential (cond IE_P3SEQ, arm "p3seq") on the 90
frozen Study-2 release worlds (cases_study2), for the five monitors of the report.

Each monitor uses the model ID, API settings, handbook and code path of its original run:
  sonnet, haiku   Anthropic via LLM.call (Study-2 handbook monitors/handbook.py)        cost-ledger phase `study5`
  deepseek        DeepSeek via LLM.deepseek_chat (Study-2 handbook)                      cost-ledger phase `deepseek`
  gpt-5.4-mini    OpenAI via study4_run.OAAdapter -> LLM.openai_chat, reasoning medium   token ledger (Study-5 limit,
  gpt-4.1-mini    OpenAI via OAAdapter, no reasoning param (Study-4 handbook HBS4)       run_id must contain "_s5-")
Tranche order (prereg Section 3): X, then B, then H, for every monitor. --world-types picks tranches (default X).

NO LIVE CALLS without the author's explicit GO: --live is required for paid calls. --fake / --dry-jobs / --project are free
and never touch the ledgers.
Usage (from experiments/):
  .venv/Scripts/python.exe -B e1-evidence-monitors/monitors/study5_run.py --project
  .venv/Scripts/python.exe -B e1-evidence-monitors/monitors/study5_run.py --models sonnet,haiku --world-types X --fake --run-dir <dir>
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
from shared.llm import PRICES  # noqa: E402
from shared.runlog import RunDir, new_run  # noqa: E402
from families import family  # noqa: E402
from monitors import handbook as HB  # noqa: E402
from monitors.harness import MODELS as H_MODELS, code_hash, fake_responder  # noqa: E402
from monitors.prompts import SCHEMA_VERSION, judge_payload, schema_text, selector_payload  # noqa: E402
from monitors.query_api import BUDGET, render_evidence  # noqa: E402
from monitors.study2_run import CASES2, Study2Harness, _fake_verdict_json, load_index  # noqa: E402
from monitors.study4_handbook import HBS4, VERSION as HBS4_VERSION, self_check as hbs4_self_check  # noqa: E402
from monitors.study4_run import MONITORS as OA_MONITORS, OAAdapter  # noqa: E402
from monitors.study5_seq import (ANCHOR, PREREG5, REJECT_CONTENT, SEQ_JOIN, SEQ_MAX_TURNS, SEQ_SENTENCE, p3_seq,  # noqa: E402
                                 prereg_texts, seq_family)

F1 = family("F1")
COND, ARM, STUDY = "IE_P3SEQ", "p3seq", "study5"
TRANCHE_ORDER = ("X", "B", "H")
ALL_MONITORS = ("sonnet", "haiku", "deepseek", "gpt-5.4-mini", "gpt-4.1-mini")
HB_SEQ, HBS4_SEQ = seq_family(HB), seq_family(HBS4)
# monitor -> (provider used by p3_seq, model id, base handbook, seq handbook, ledger)
MONITORS = {
    "sonnet": ("anthropic", H_MODELS["sonnet"][1], HB, HB_SEQ, "cost:study5"),
    "haiku": ("anthropic", H_MODELS["haiku"][1], HB, HB_SEQ, "cost:study5"),
    "deepseek": ("deepseek", H_MODELS["deepseek"][1], HB, HB_SEQ, "cost:deepseek"),
    "gpt-5.4-mini": ("openai", "gpt-5.4-mini", HBS4, HBS4_SEQ, "token:study5"),
    "gpt-4.1-mini": ("openai", "gpt-4.1-mini", HBS4, HBS4_SEQ, "token:study5"),
}
ORIGINAL_RUNS = {  # prereg Section 3 table
    "sonnet": ("20261108T114814Z_s2-T1_7d74dc", "p3orig"),
    "haiku": ("20261108T115304Z_s2-T3_8b773a", "p3orig"),
    "deepseek": ("20261108T115748Z_s2-T3ds_5d1b17", "p3orig"),
    "gpt-5.4-mini": ("20261109T000101Z_s4-eval-gpt-5.4-mini_44a59a", "p3orig"),
    "gpt-4.1-mini": ("20261109T000352Z_s4-eval-gpt-4.1-mini_32e9cf", "p3orig"),
}
STUDY5_RUN_MARKER = "_s5-"  # must match shared/token_ledger.STUDY5_RUN_MARKER (live OpenAI runs)
CAPS = {"study5_anthropic_usd": 5.25, "deepseek_provider_usd": 6.55, "openai_study5_paid_overflow_usd": 2.20}


def tree_sha(d: Path) -> str:
    """Same function as tools/freeze_study2.py (cases_study2_tree_sha256)."""
    h = hashlib.sha256()
    for f in sorted(x for x in d.rglob("*") if x.is_file()):
        h.update(str(f.relative_to(d)).replace("\\", "/").encode())
        h.update(hashlib.sha256(f.read_bytes()).hexdigest().encode())
    return h.hexdigest()


def frozen_tree_sha() -> str:
    return json.loads((_E1.parents[1] / "project-selection" / "freeze_record_study2.json").read_text(encoding="utf-8"))["cases_study2_tree_sha256"]


def prompt_record(mk):
    """Exact system prompts this monitor sees (sha256 + checks), recorded in run_meta (validity checks, prereg Section 8)."""
    prov, _, base, seq, _ = MONITORS[mk]
    s_orig, s_seq = base.SELECTOR_SYSTEM(), seq.SELECTOR_SYSTEM()
    jp_orig, jp_seq = judge_payload(base, "T", None), judge_payload(seq, "T", None)
    if prov == "anthropic":
        j_orig, j_seq = jp_orig["system"], jp_seq["system"]
    else:  # OpenAI-compatible judge: system + schema text (policies.judge)
        j_orig, j_seq = jp_orig["system"] + "\n\n" + schema_text(base), jp_seq["system"] + "\n\n" + schema_text(seq)
    sha = lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest()  # noqa: E731
    return {"selector_system_sha256": sha(s_seq), "selector_system_orig_sha256": sha(s_orig),
            "selector_diff_is_prereg_sentence": s_seq == s_orig.replace(ANCHOR, ANCHOR + SEQ_JOIN + SEQ_SENTENCE) and s_orig.count(ANCHOR) == 1,
            "judge_system_sha256": sha(j_seq), "judge_system_identical_to_orig": j_seq == j_orig,
            "verdict_schema_sha256": sha(json.dumps(jp_seq["output_format"], sort_keys=True)),
            "verdict_schema_identical_to_orig": jp_seq["output_format"] == jp_orig["output_format"],
            "selector_user_message_identical": selector_payload(seq, "T")["messages"] == selector_payload(base, "T")["messages"],
            "handbook": "handbook-v-S4" if base is HBS4 else "study2-handbook"}


# ------------------------------------------------------------------ fake backend (never a result)
def s5_fake_responder(kwargs):
    """FAKE ONLY (Anthropic path): first selector turn sends 3 tool calls at once (exercises the one-per-turn rejection),
    the next two turns one call each, then DONE; judge calls get a well-formed handbook verdict."""
    if "format" in kwargs.get("output_config", {}):
        msg = fake_responder(kwargs)
        return {**msg, "content": [{"type": "text", "text": _fake_verdict_json(json.dumps(kwargs["messages"], default=str))}]}
    n_res = sum(1 for m in kwargs["messages"] if m["role"] == "user" and isinstance(m["content"], list)
                and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"]))
    base = fake_responder(kwargs)
    if not kwargs.get("tools") or n_res >= 3:
        return {**base, "content": [{"type": "text", "text": "DONE"}], "stop_reason": "end_turn"}
    k = 3 if n_res == 0 else 1
    blocks = []
    for i in range(k):
        t = kwargs["tools"][(n_res + i) % len(kwargs["tools"])]
        args = {a: ("2026-12-31T00:00:00Z" if a == "as_of" else (["x"] if v.get("type") == "array" else (1 if v.get("type") == "integer" else "x")))
                for a, v in t["input_schema"]["properties"].items() if a in t["input_schema"]["required"]}
        blocks.append({"type": "tool_use", "id": f"toolu_s5f{n_res}_{i}", "name": t["name"], "input": args})
    return {**base, "content": blocks, "stop_reason": "tool_use"}


class Study5Harness(Study2Harness):
    def __init__(self, rd, fake, index):
        super().__init__(rd, "study5", fake, index)  # Anthropic calls reserve/settle against cost-ledger phase `study5`
        self.stopped = set()  # monitors whose ledger refused a call: no new work is submitted for them (the run pauses)
        if fake:
            self.llm.fake_responder = s5_fake_responder

    def run_job(self, wid, mk, conds=None, repeat=0, order_seed=None):
        if mk in self.stopped or self.key(wid, mk, COND, 0) in self.done:
            return
        prov, model, base, seq, _ = MONITORS[mk]
        w, e = self.world(wid), self.index[wid]
        m = {"wid": wid, "model_key": mk, "repeat_idx": 0, "cond": COND, "study": STUDY}
        if prov == "openai":
            res = p3_seq(OAAdapter(self.llm, model), "deepseek", model, seq, w, m)
        else:
            res = p3_seq(self.llm, prov, model, seq, w, m)
        res.pop("integrated", None)
        res["calls"] = res.pop("selector_calls") + res["calls"]
        if any(c.get("outcome") == "budget_refused" for c in res["calls"]):
            with self.lock:
                self.stopped.add(mk)
        resp = res.pop("responses")
        decisive = any(r.get("executed") and r["query"] == "approvals_for_digest" and r["args"].get("digest") == e["deployed_digest"]
                       for r in resp)
        extra = {"study": STUDY, "world_type": e["world_type"], "arm": ARM, "h_failure": e["h_failure"], "decisive_query": decisive,
                 "C_lim_native": F1.c_lim(w["ie_facts"], resp, w["spec"], False), "seq_rejected": res.pop("seq_rejected"),
                 "turn_queries": res.pop("turn_queries"), "monitor": mk,
                 "handbook_version": HBS4_VERSION if base is HBS4 else "study2-handbook"}
        if prov == "openai":
            extra["reasoning_effort"] = OA_MONITORS[model]["reasoning_effort"]
        self.emit(wid, mk, COND, res, HB.c_lim(F1, w["ie_facts"], resp, w["spec"], False), repeat=0,
                  evidence=render_evidence(resp), extra=extra)


def build_jobs(index, models, world_types, limit=0):
    """Tranche order X, B, H (prereg Section 3); within a tranche, bases in manifest order; --limit-worlds = first N bases
    per tranche."""
    jobs, per = [], {}
    for wt in [t for t in TRANCHE_ORDER if t in world_types]:
        wids = sorted([w for w in index if index[w]["world_type"] == wt], key=lambda w: index[w]["case_id"])
        if limit:
            wids = wids[:limit]
        per[wt] = wids
        for mk in models:
            jobs += [(wid, mk) for wid in wids]
    return per, jobs


# ------------------------------------------------------------------ projection (no calls)
def _orig_rows(mk):
    rid, arm = ORIGINAL_RUNS[mk]
    p = _E1 / "runs" / rid / "monitor_results.jsonl"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r for r in rows if r.get("arm") == arm and r.get("family") == "F1"]


def projection(turns_central=4, turns_upper=SEQ_MAX_TURNS, growth_extra=600):
    """Per monitor and tranche (30 worlds each), from the ORIGINAL run's realised usage on the same worlds (Study-4 GPT runs
    cover X only; their X figures stand in for B and H). No calls are made.
    Selector, per world: turn 1 = the original run's realised FIRST selector call (mean). Each later turn t re-sends turn 1's
    prompt (Anthropic/DeepSeek: as a cache read, since the system + log prefix is cached; OpenAI: counted in full, as the token
    ledger counts it) plus (t-1) x G uncached tokens, G = turn-1 output + `growth_extra` (one result and one assistant turn),
    and produces turn 1's output again. Selector calls per world: central 4 (three one-query turns + one closing turn), upper 6
    (the turn limit). Judge, per world: the original realised mean; Anthropic judges priced cache-cold for the log (Study 5 has
    one judge per world; in Study 2 half the judge calls read the log from the other arm's cache). DeepSeek x1.15 cache-miss
    allowance (as in the Study-2 projection)."""
    out = {}
    for mk in ALL_MONITORS:
        rows = _orig_rows(mk)
        prov, model = MONITORS[mk][0], MONITORS[mk][1]
        by_wt = defaultdict(list)
        for r in rows:
            by_wt[r["world_type"]].append(r)
        mon = {}
        for wt in TRANCHE_ORDER:
            src = by_wt.get(wt) or by_wt.get("X")
            n = len(src)
            first = [[c for c in r["calls"] if c.get("tag") == "P3_selector"][0] for r in src]
            sel = [c for r in src for c in r["calls"] if c.get("tag") == "P3_selector"]
            jud = [c for r in src for c in r["calls"] if c.get("tag") == "P3_judge"]

            def mean(xs, k):
                return sum((x.get(k) or 0) for x in xs) / max(1, len(xs))
            i1, cw1, cr1, o1 = (mean(first, k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
            G = o1 + growth_extra
            cell = {"basis_world_type": wt if wt in by_wt else "X (no original B/H run)", "worlds": 30,
                    "orig_selector_calls_per_world": round(len(sel) / n, 2)}
            if prov == "openai":
                p1 = i1  # OpenAI input_tokens = full prompt (cached included)
                j_in, j_out = mean(jud, "input_tokens"), mean(jud, "output_tokens")
                for lab, k in (("central", turns_central), ("upper", turns_upper)):
                    pin = sum(p1 + (t - 1) * G for t in range(1, k + 1)) + j_in
                    pout = k * o1 + j_out
                    cell[lab] = {"prompt_tokens": int(30 * pin), "output_tokens": int(30 * pout), "total_tokens": int(30 * (pin + pout))}
                cell["orig_tokens"] = int(30 * sum((c.get("input_tokens") or 0) + (c.get("output_tokens") or 0) for c in sel + jud) / n)
            else:
                pr = PRICES[model]
                c1 = mean(first, "cost_usd")
                later = lambda t: ((i1 + cw1 + cr1) * pr["cr"] + (t - 1) * G * pr["in"] + o1 * pr["out"]) / 1e6  # noqa: E731
                j = mean(jud, "cost_usd")
                if prov == "anthropic":  # cold log write for the judge: judge cache reads beyond the system prompt, re-priced as writes
                    j += max(0.0, mean(jud, "cache_read_input_tokens") - cr1) * (pr["cw"] - pr["cr"]) / 1e6
                mult = 1.15 if prov == "deepseek" else 1.0
                cell["orig_usd"] = round(30 * sum(c.get("cost_usd") or 0 for c in sel + jud) / n, 3)
                for lab, k in (("central", turns_central), ("upper", turns_upper)):
                    cell[f"{lab}_usd"] = round(30 * mult * (c1 + sum(later(t) for t in range(2, k + 1)) + j), 3)
            mon[wt] = cell
        out[mk] = mon
    oa = ("gpt-5.4-mini", "gpt-4.1-mini")
    usd = lambda mks, lab: round(sum(out[m][wt][f"{lab}_usd"] for m in mks for wt in TRANCHE_ORDER), 3)  # noqa: E731

    def overflow(day_tokens, lab):  # token-ledger guard rule: $1/M input, $4/M output beyond 2.4M tokens per UTC day
        pin = sum(out[m][wt][lab]["prompt_tokens"] for m in oa for wt in TRANCHE_ORDER)
        pout = sum(out[m][wt][lab]["output_tokens"] for m in oa for wt in TRANCHE_ORDER)
        return round(sum(max(0, t - 2_400_000) for t in day_tokens) * (pin + 4.0 * pout) / (pin + pout) / 1e6, 3)
    oa_tok = {lab: sum(out[m][wt][lab]["total_tokens"] for m in oa for wt in TRANCHE_ORDER) for lab in ("central", "upper")}
    tranche_tok = {lab: [out[m][wt][lab]["total_tokens"] for m in oa for wt in TRANCHE_ORDER] for lab in ("central", "upper")}
    led = {}
    try:
        from shared.cost_ledger import CostLedger
        sm = CostLedger().summary()  # read-only
        led = {"deepseek_settled_usd": sm["deepseek"]["settled"], "study5_settled_usd": sm.get("study5", {}).get("settled")}
    except Exception as e:  # noqa: BLE001
        led = {"error": str(e)}
    ds_left = round(CAPS["deepseek_provider_usd"] - led["deepseek_settled_usd"], 3) if "deepseek_settled_usd" in led else None
    return {"per_monitor": out,
            "totals": {"anthropic_usd_sonnet+haiku_phase_study5": {lab: usd(("sonnet", "haiku"), lab) for lab in ("central", "upper")},
                       "anthropic_usd_by_tranche": {wt: {lab: round(sum(out[m][wt][f"{lab}_usd"] for m in ("sonnet", "haiku")), 3) for lab in ("central", "upper")} for wt in TRANCHE_ORDER},
                       "deepseek_usd": {lab: usd(("deepseek",), lab) for lab in ("central", "upper")},
                       "openai_total_tokens": oa_tok,
                       "openai_paid_overflow_usd_all_in_one_utc_day": {lab: overflow([oa_tok[lab]], lab) for lab in ("central", "upper")},
                       "openai_paid_overflow_usd_one_tranche_per_utc_day": {lab: overflow(tranche_tok[lab], lab) for lab in ("central", "upper")}},
            "caps": {**CAPS, "deepseek_remaining_usd": ds_left, "ledger_read": led},
            "basis": "original runs " + ", ".join(v[0] for v in ORIGINAL_RUNS.values()) + f"; selector calls per world central {turns_central}, "
                     f"upper {turns_upper}; growth per later turn = turn-1 output + {growth_extra} tokens"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="sonnet", help="comma list of " + ",".join(ALL_MONITORS))
    ap.add_argument("--world-types", default="X", help="comma list of X,B,H (always run in the order X, B, H)")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--limit-worlds", type=int, default=0, help="first N bases per tranche (live check: 3)")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--dry-jobs", action="store_true")
    ap.add_argument("--project", action="store_true")
    ap.add_argument("--live", action="store_true", help="required for paid calls (only after the author's explicit GO)")
    a = ap.parse_args()
    if a.project:
        print(json.dumps(projection(), indent=1))
        return
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    bad = [m for m in models if m not in MONITORS]
    if bad:
        raise SystemExit(f"unknown monitor(s) {bad}; choose from {list(ALL_MONITORS)}")
    wts = [t.strip().upper() for t in a.world_types.split(",") if t.strip()]
    if not wts or any(t not in TRANCHE_ORDER for t in wts):
        raise SystemExit("--world-types must be a comma list of X,B,H")
    if a.fake and a.live:
        raise SystemExit("--fake and --live are exclusive")
    if not a.fake and not a.dry_jobs and not a.live:
        raise SystemExit("live calls need --live (only after the author's explicit GO)")
    pt = prereg_texts()
    if not all(pt.values()):
        raise SystemExit(f"Study-5 constants do not match {PREREG5.name} byte-for-byte: {pt}")
    hs = hbs4_self_check()
    assert hs["exactly_one_added"] and hs["study1_handbook_unchanged"], hs
    prompts = {mk: prompt_record(mk) for mk in models}
    for mk, pr in prompts.items():
        if not (pr["selector_diff_is_prereg_sentence"] and pr["judge_system_identical_to_orig"] and pr["verdict_schema_identical_to_orig"]
                and pr["selector_user_message_identical"]):
            raise SystemExit(f"prompt check failed for {mk}: {pr}")
    tsha = tree_sha(CASES2)
    if tsha != frozen_tree_sha():
        raise SystemExit("cases_study2 tree hash does not match freeze_record_study2.json")
    man, index = load_index()
    per, jobs = build_jobs(index, models, wts, a.limit_worlds)
    if a.dry_jobs:
        print(f"DRY: tranches {[(t, len(v)) for t, v in per.items()]}, {len(jobs)} decisions:", dict(Counter((j[1], index[j[0]]['world_type']) for j in jobs)))
        return
    oa_live = a.live and any(MONITORS[m][0] == "openai" for m in models)
    if oa_live and a.run_dir and STUDY5_RUN_MARKER not in Path(a.run_dir).name:
        raise SystemExit(f"live OpenAI runs need a run dir whose name contains {STUDY5_RUN_MARKER!r} (token-ledger Study-5 limit)")
    name = ("s5fake-" if a.fake else "s5-") + ("all" if set(models) == set(ALL_MONITORS) else "+".join(models))
    meta = {"study": STUDY, "args": vars(a), "budget": BUDGET.__dict__, "regime": "handbook", "schema": "e1.monitor_result.v1",
            "verdict_schema_version": SCHEMA_VERSION, "code_hash": code_hash(), "arm": ARM, "cond": COND, "models": models,
            "model_ids": {m: MONITORS[m][1] for m in models}, "ledgers": {m: MONITORS[m][4] for m in models},
            "reasoning_effort": {m: OA_MONITORS[m]["reasoning_effort"] for m in models if m in OA_MONITORS},
            "seq_sentence": SEQ_SENTENCE, "seq_join": SEQ_JOIN, "reject_content": REJECT_CONTENT, "seq_max_turns": SEQ_MAX_TURNS,
            "prompts": prompts, "prereg_checks": pt, "handbook_v_s4_self_check": hs,
            "cases_study2_tree_sha256": tsha, "cases_study2_tree_matches_freeze_record": True,
            "manifest_sha256": hashlib.sha256((CASES2 / "manifest.json").read_bytes()).hexdigest(),
            "prereg_sha256": hashlib.sha256(PREREG5.read_bytes()).hexdigest(),
            "study5_code_sha256": {f: hashlib.sha256((_E1 / "monitors" / f).read_bytes()).hexdigest() for f in ("study5_seq.py", "study5_run.py")},
            "original_runs": ORIGINAL_RUNS, "tranche_order": list(TRANCHE_ORDER), "world_types": wts}
    if a.run_dir:
        rd = RunDir(Path(a.run_dir))
        if not (rd.path / "run_meta.json").exists():
            rd.write_json("run_meta.json", {"run_id": rd.run_id, "name": name, **meta})
        else:
            rd.log("resume_meta", {"at": time.time(), **meta})
    else:
        rd = new_run(_E1 / "runs", name, meta)
    print("run_dir", rd.path, flush=True)
    h = Study5Harness(rd, a.fake, index)
    print(f"tranches {[(t, len(v)) for t, v in per.items()]}, {len(jobs)} jobs, {len(h.done)} results already done", flush=True)
    t0, n_done, n_err = time.time(), 0, 0
    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        futs = {ex.submit(h.run_job, *j): j for j in jobs}  # submitted in tranche order X, B, H
        for f in as_completed(futs):
            n_done += 1
            try:
                f.result()
            except Exception as e:
                n_err += 1
                rd.log("job_errors", {"job": list(futs[f]), "error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-2000:]})
            if n_done % 15 == 0 or n_done == len(jobs):
                msg = ""
                if not a.fake:  # read-only ledger summaries (never in fake mode)
                    from shared.cost_ledger import CostLedger
                    led = CostLedger().summary()
                    msg = f" | study5 ${led['study5']['settled']:.3f} (open {led['study5']['open_reserved']:.3f}) | deepseek ${led['deepseek']['settled']:.3f}"
                    if any(MONITORS[m][0] == "openai" for m in models):
                        from shared.token_ledger import TokenLedger
                        tl = TokenLedger()
                        ents = tl._entries()
                        ids = {x["id"] for x in ents if x["kind"] == "reserve" and STUDY5_RUN_MARKER in str(x.get("run_id", ""))}
                        g = tl.guard([x for x in ents if x["id"] in ids])
                        msg += f" | openai s5 tokens today {g['tokens_today']} paid-est ${g['paid_overflow_usd_estimate']}"
                print(f"[{time.time() - t0:6.0f}s] jobs {n_done}/{len(jobs)} errors {n_err}{msg}"
                      + (f" | PAUSED (ledger refused): {sorted(h.stopped)}" if h.stopped else ""), flush=True)
    print(("PAUSED " if h.stopped else "done ") + str(rd.path), flush=True)


if __name__ == "__main__":
    main()

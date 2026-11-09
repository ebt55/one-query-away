"""SUPERSEDED (spike-era, generator f1-v1/v2): the current label implementations live in families/f*.py
(label_sql / label_replay) and the current runner is monitors/harness.py. Kept for provenance of runs/2026110723*;
this file no longer matches the current world schema (deploys.alias -> deploys.target) and is not maintained.
"""
"""E1 feasibility spike (plan section 5.1). F1 only, 2 base cases.

Usage (from experiments/):
  .venv/Scripts/python.exe e1-evidence-monitors/monitors/run_spike.py            # deterministic checks + offline harness
  .venv/Scripts/python.exe e1-evidence-monitors/monitors/run_spike.py --live     # + Haiku/Sonnet k=1, cache test, DeepSeek smoke
"""
from __future__ import annotations

import argparse
import json
import secrets
import statistics
import sys
from collections import defaultdict
from pathlib import Path

E1 = Path(__file__).resolve().parents[1]
EXP = E1.parent
for p in (str(EXP), str(E1)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.cost_ledger import CostLedger  # noqa: E402
from shared.llm import LLM, canonical_sha256, have_key, heuristic_tokens, visible_payload  # noqa: E402
from shared.runlog import new_run  # noqa: E402
from shared.seeds import derive_seed  # noqa: E402
from env.labels import label_sql  # noqa: E402
from env.labels_replay import label_replay  # noqa: E402
from env.render import agent_view, render_v0, render_v2, visible_facts, visible_records  # noqa: E402
from families.f1_alias import AGENTS, GENERATOR_VERSION, VARIANTS, make_world  # noqa: E402
from monitors.policies import c_lim, make_fake_responder, p2_checklist, run_judge, run_p2, run_p3  # noqa: E402
from monitors.prompts import judge_payload, judge_user_message, selector_payload, JUDGE_SYSTEM  # noqa: E402
from monitors.query_api import BUDGET, QueryAPI  # noqa: E402

MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
SEED_TAG = "e1-spike-v1"


def vis_sha(p: dict) -> str:
    return canonical_sha256(visible_payload(p["system"], p["messages"], p["tools"], p["output_format"]))


def build(rd):
    worlds = []
    for ci, variant in enumerate(VARIANTS):
        seed = derive_seed(SEED_TAG, "F1", ci)
        for harmful in (True, False):
            pid = "w_" + secrets.token_hex(6)  # private world id: only in db filename + private index
            w = make_world(rd.sub("worlds") / f"{pid}.sqlite", seed, harmful, variant)
            ie = render_v0(w.records, w.spec, "IE")
            worlds.append({"pid": pid, "case": ci, "variant": variant, "harmful": harmful, "seed": seed,
                           "world": w, "db": w.db_path, "spec": w.spec,
                           "V0": render_v0(w.records, w.spec, "CE"), "V2": render_v2(w.records, w.spec), "IE": ie,
                           "ie_facts": visible_facts(w.records, "IE"), "ce_facts": visible_facts(w.records, "CE")})
            w.conn.close()
    rd.write_json("private/world_index.json", [{k: x[k] for k in ("pid", "case", "variant", "harmful", "seed")} for x in worlds])
    for x in worlds:
        for v in ("V0", "V2", "IE"):
            rd.write_text(f"renders/{x['pid']}_{v}.txt", x[v])
    return worlds


def deterministic_checks(rd, worlds):
    res = {}
    # 1. labels: two implementations + expected + tool consistency
    lab = []
    for x in worlds:
        a, b = label_sql(x["db"]), label_replay(x["db"])
        lab.append({"pid": x["pid"], "case": x["case"], "harmful": x["harmful"], "sql": a["violation"],
                    "replay": b["violation"], "agree": a["violation"] == b["violation"],
                    "matches_design": a["violation"] == x["harmful"],
                    "tool_resolution_consistent": (not b["tool_resolution_mismatch"]) and all(
                        d["resolved_hash"] == d["recorded_hash"] for d in a["deploys"]),
                    "n_prod_deploys": len(a["deploys"])})
        x["label"] = a["violation"]
    res["labels"] = {"rows": lab, "pass": all(r["agree"] and r["matches_design"] and r["tool_resolution_consistent"] for r in lab)}

    # 2. IE payload equality per pair; CE sanity (should differ)
    pairs = []
    for ci in sorted({x["case"] for x in worlds}):
        h = next(x for x in worlds if x["case"] == ci and x["harmful"])
        b = next(x for x in worlds if x["case"] == ci and not x["harmful"])
        row = {"case": ci, "variant": h["variant"],
               "IE_trace_sha_equal": canonical_sha256(h["IE"]) == canonical_sha256(b["IE"]),
               "P1_payload_sha": [vis_sha(judge_payload(h["IE"])), vis_sha(judge_payload(b["IE"]))],
               "P3_selector_payload_sha": [vis_sha(selector_payload(h["IE"])), vis_sha(selector_payload(b["IE"]))],
               "IE_visible_facts_equal": canonical_sha256(h["ie_facts"]) == canonical_sha256(b["ie_facts"]),
               "CE_V0_differs": h["V0"] != b["V0"], "CE_V2_differs": h["V2"] != b["V2"]}
        row["P1_equal"] = row["P1_payload_sha"][0] == row["P1_payload_sha"][1]
        row["P3_equal"] = row["P3_selector_payload_sha"][0] == row["P3_selector_payload_sha"][1]
        # twin matching: length within 10%, command vocabulary
        def vocab(x):
            return {tok for r in visible_records(x["world"].records, "CE") for tok in r["argv"].split()
                    if tok.startswith("-") or tok.isalpha()}
        lh, lb = len(h["V0"]), len(b["V0"])
        vh, vb = vocab(h), vocab(b)
        row.update(CE_len=[lh, lb], CE_len_ratio=round(max(lh, lb) / min(lh, lb), 4),
                   cmd_vocab_jaccard=round(len(vh & vb) / len(vh | vb), 4),
                   n_actions=[len(visible_records(h["world"].records, "CE")), len(visible_records(b["world"].records, "CE"))],
                   noise_actions_per_agent={a: sum(1 for r in visible_records(h["world"].records, "CE")
                                                   if r["actor"] == a) for a in AGENTS})
        pairs.append(row)
    res["ie_payload_equality"] = {"rows": pairs, "pass": all(r["P1_equal"] and r["P3_equal"] and r["IE_trace_sha_equal"]
                                                             for r in pairs)}
    res["twin_matching"] = {"pass": all(r["CE_len_ratio"] <= 1.10 for r in pairs),
                            "ce_differs": all(r["CE_V0_differs"] and r["CE_V2_differs"] for r in pairs)}

    # 3. each fact once in V0 and V2
    once = []
    for x in worlds:
        n0 = sum(1 for ln in x["V0"].splitlines() if ln.startswith("[") and "] $ " in ln)
        n2 = sum(1 for ln in x["V2"].splitlines() if ln.startswith("[") and "$ " in ln)
        once.append({"pid": x["pid"], "V0_entries": n0, "V2_entries": n2,
                     "records": len(visible_records(x["world"].records, "CE"))})
    res["v0_v2_same_facts"] = {"rows": once, "pass": all(o["V0_entries"] == o["V2_entries"] == o["records"] for o in once)}

    # 4. distributed dependence (counterfactual search)
    dd = []
    for ci in sorted({x["case"] for x in worlds}):
        h = next(x for x in worlds if x["case"] == ci and x["harmful"])
        tmp = rd.sub("worlds_cf")
        H = make_world(tmp / f"cf_{secrets.token_hex(4)}.sqlite", h["seed"], True, h["variant"])
        dig = label_sql(H.db_path)["deploys"][-1]["resolved_hash"]
        cands = {"benign_twin": (False, None), "cf_add_approval": (True, {"kind": "add_approval", "digest": dig}),
                 "cf_delay_ci": (True, {"kind": "delay_ci"})}
        built = {}
        for name, (harm, cf) in cands.items():
            W = make_world(tmp / f"cf_{secrets.token_hex(4)}.sqlite", h["seed"], harm, h["variant"], cf)
            built[name] = (W, label_sql(W.db_path)["violation"])
        per_agent = {}
        for a in AGENTS:
            hv = agent_view(H.records, H.spec, a)
            per_agent[a] = [n for n, (W, viol) in built.items() if not viol and agent_view(W.records, W.spec, a) == hv]
        dd.append({"case": ci, "benign_witness_per_agent": per_agent, "pass": all(per_agent.values())})
        H.conn.close()
        for W, _ in built.values():
            W.conn.close()
    res["distributed_dependence"] = {"rows": dd, "pass": all(r["pass"] for r in dd)}

    # 5. offline P2 + C-full/C-lim + coverage metadata + budget parity (fake backend exercises P3 loop)
    off = []
    fake = LLM(rd, phase="spikes", sub="e1_spike", backend="fake", fake_responder=make_fake_responder())
    cov_keys = {"as_of_requested", "as_of_effective", "as_of_clamped", "total_matching", "returned", "page",
                "pages", "page_size", "has_more", "truncated_by_budget", "complete"}
    cov_ok = True
    for x in worlds:
        api2 = QueryAPI(x["db"], x["spec"], BUDGET)
        p2_checklist(api2, x["ie_facts"], x["spec"])
        api3 = QueryAPI(x["db"], x["spec"], BUDGET)
        p3 = run_p3(fake, "claude-haiku-5-5", x["IE"], api3, meta={"pid": x["pid"], "dry_run": True})
        for r in api2.responses + api3.responses:
            if r.get("executed") and not (r.get("coverage") and cov_keys <= set(r["coverage"])):
                cov_ok = False
        off.append({"pid": x["pid"], "case": x["case"], "harmful": x["harmful"], "C_full": "violation" if x["label"] else "compliant",
                    "C_lim_P1": c_lim(x["ie_facts"], [], x["spec"], trace_complete=False),
                    "C_lim_P2": c_lim(x["ie_facts"], api2.responses, x["spec"], trace_complete=False),
                    "C_lim_CE": c_lim(x["ce_facts"], [], x["spec"], trace_complete=True),
                    "P2_queries": [(r["query"], r["args"]) for r in api2.responses], "P2_q_used": api2.used_q,
                    "P2_records": api2.used_records, "P2_chars": api2.used_chars,
                    "fakeP3_q_used": api3.used_q, "fakeP3_refused": api3.refused,
                    "fakeP3_selector_stop": p3["selector_stop"], "fakeP3_judge_valid": p3["parsed"]["valid"]})
    res["offline_policies"] = {"rows": off,
                               "budget_parity": {"P2_config": BUDGET.__dict__, "P3_config": BUDGET.__dict__,
                                                 "same_object": True,
                                                 "P2_exactly_q": all(o["P2_q_used"] == BUDGET.q_max for o in off),
                                                 "P3_never_exceeds_q": all(o["fakeP3_q_used"] <= BUDGET.q_max for o in off),
                                                 "P3_overbudget_refused": all(o["fakeP3_refused"] >= 1 for o in off)},
                               "coverage_metadata_present": cov_ok,
                               "C_lim_CE_equals_C_full": all(o["C_lim_CE"] == o["C_full"] for o in off),
                               "C_lim_P1_all_unknown": all(o["C_lim_P1"] == "unknown" for o in off),
                               "C_lim_P2_equals_C_full": all(o["C_lim_P2"] == o["C_full"] for o in off)}
    return res


def token_estimates(rd, worlds, live: bool):
    """Exact input tokens via count_tokens (free) when a key exists; else heuristic."""
    est = defaultdict(list)
    llm = LLM(rd, phase="spikes", sub="e1_spike")
    for mk, model in MODELS.items():
        for x in worlds:
            for name, p in (("CE_V0", judge_payload(x["V0"])), ("CE_V2", judge_payload(x["V2"])),
                            ("IE_P1", judge_payload(x["IE"])), ("IE_P3_selector_t0", selector_payload(x["IE"]))):
                sb = [{"type": "text", "text": p["system"]}]
                if live:
                    n, how = llm.estimate_input_tokens(model, sb, p["messages"], p["tools"])
                else:
                    n, how = heuristic_tokens([sb, p["messages"], p["tools"]]), "heuristic"
                est[f"{mk}:{name}"].append((n, how))
    sys_tokens = {}
    for mk, model in MODELS.items():
        if live:
            n, how = llm.estimate_input_tokens(model, [{"type": "text", "text": JUDGE_SYSTEM}],
                                               [{"role": "user", "content": "x"}], None)
            sys_tokens[mk] = (n, how)
    return {k: {"values": [v[0] for v in vs], "method": vs[0][1]} for k, vs in est.items()} | {"judge_system_tokens": sys_tokens}


def live_matrix(rd, worlds):
    llm = LLM(rd, phase="spikes", sub="e1_spike")
    out = []
    for mk in ("haiku", "sonnet"):
        model = MODELS[mk]
        for x in worlds:
            meta = {"pid": x["pid"], "model_key": mk}
            for view in ("V0", "V2"):
                j = run_judge(llm, model, x[view], None, f"CE_{view}", meta)
                out.append({"model": mk, "pid": x["pid"], "cond": f"CE_{view}", "parsed": j["parsed"],
                            "calls": [j["call"]], "C_lim": c_lim(x["ce_facts"], [], x["spec"], True)})
            j = run_judge(llm, model, x["IE"], None, "IE_P1", meta)
            out.append({"model": mk, "pid": x["pid"], "cond": "IE_P1", "parsed": j["parsed"], "calls": [j["call"]],
                        "C_lim": c_lim(x["ie_facts"], [], x["spec"], False)})
            api = QueryAPI(x["db"], x["spec"], BUDGET)
            r2 = run_p2(llm, model, x["IE"], api, x["ie_facts"], x["spec"], meta)
            out.append({"model": mk, "pid": x["pid"], "cond": "IE_P2", "parsed": r2["parsed"], "calls": [r2["call"]],
                        "queries": [(q["query"], q["args"]) for q in r2["queries"]], "q_used": api.used_q,
                        "records": api.used_records, "chars": api.used_chars,
                        "C_lim": c_lim(x["ie_facts"], r2["queries"], x["spec"], False)})
            api = QueryAPI(x["db"], x["spec"], BUDGET)
            r3 = run_p3(llm, model, x["IE"], api, meta)
            out.append({"model": mk, "pid": x["pid"], "cond": "IE_P3", "parsed": r3["parsed"],
                        "calls": r3["selector_calls"] + [r3["call"]], "selector_stop": r3["selector_stop"],
                        "queries": [(q["query"], q["args"], q.get("executed")) for q in r3["queries"]],
                        "q_used": api.used_q, "refused": api.refused, "records": api.used_records, "chars": api.used_chars,
                        "C_lim": c_lim(x["ie_facts"], r3["queries"], x["spec"], False)})
            for o in out[-5:]:
                if o["pid"] == x["pid"] and o["model"] == mk:
                    rd.log("monitor_results", {**o, "label": x["label"], "case": x["case"]})
            print(f"[live] {mk} {x['pid']} done; ledger e1_spike:", CostLedger().summary()["subcaps"]["spikes/e1_spike"], flush=True)
        if mk == "haiku":
            # Haiku 5.5 caching verification: identical >=512-token prefix twice, top-level cache_control
            x = worlds[0]
            p = judge_payload(x["IE"])
            cache = []
            for i in range(2):
                c = llm.call(model=model, system=p["system"], messages=p["messages"], output_format=p["output_format"],
                             max_tokens=8000, top_level_cache=True, tag=f"haiku_cache_test_{i}", meta={"pid": x["pid"]})
                cache.append(c["record"])
            rd.write_json("haiku_cache_test.json", cache)
            # DeepSeek smoke (cost negligible; separate 'deepseek' phase)
            try:
                ds = llm.deepseek_call(system=JUDGE_SYSTEM, user=judge_user_message(x["IE"], None), tag="deepseek_smoke",
                                       meta={"pid": x["pid"]})
                rd.write_json("deepseek_smoke.json", {"outcome": ds["outcome"], "record": ds["record"],
                                                      "text_head": ds["text"][:300]})
            except Exception as e:
                rd.write_json("deepseek_smoke.json", {"outcome": "exception", "error": f"{type(e).__name__}: {e}"})
    return out


def summarize_calls(rd):
    path = rd.path / "llm_calls.jsonl"
    if not path.exists():
        return {}
    recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    recs = [r for r in recs if r.get("backend") != "fake" and r.get("provider") == "anthropic"
            and not r.get("tag", "").startswith("haiku_cache_test")]
    agg = defaultdict(list)
    for r in recs:
        agg[(r["model"], r["tag"])].append(r)
    out = {}
    for (m, tag), rs in sorted(agg.items()):
        ok = [r for r in rs if r.get("input_tokens") is not None]
        f = lambda k: round(statistics.mean([(r.get(k) or 0) for r in ok]), 1) if ok else None  # noqa: E731
        out[f"{m}|{tag}"] = {"n": len(rs), "outcomes": dict(sorted({o: sum(1 for r in rs if r.get("outcome") == o)
                                                                     for o in {r.get("outcome") for r in rs}}.items())),
                             "mean_input_tokens": f("input_tokens"), "mean_cache_read": f("cache_read_input_tokens"),
                             "mean_cache_write": f("cache_creation_input_tokens"), "mean_output_tokens": f("output_tokens"),
                             "max_output_tokens": max([r.get("output_tokens") or 0 for r in ok], default=None),
                             "mean_latency_s": f("latency_s"), "max_latency_s": max([r.get("latency_s") or 0 for r in ok], default=None),
                             "mean_cost_usd": round(statistics.mean([r.get("cost_usd") or 0 for r in ok]), 6) if ok else None,
                             "total_cost_usd": round(sum(r.get("cost_usd") or 0 for r in rs), 6)}
    rl = {}
    for r in recs:
        if r.get("ratelimit_headers"):
            rl[r["model"]] = r["ratelimit_headers"]
    return {"by_model_tag": out, "last_ratelimit_headers": rl,
            "total_cost_usd": round(sum(r.get("cost_usd") or 0 for r in recs), 6),
            "model_mismatch_any": any(r.get("model_mismatch") for r in recs)}


def projection(call_summary, live_rows):
    """Main-matrix projection from measured per-call means. Matrix (plan 3.6): calibration 12 + test 45 base
    cases (F1-F3), x1.4 for k=3 repeats on a 20% subset; pilot 9; F4 15 (run once). Per base case: 2 worlds x
    (CE_V0, CE_V2, IE_P1, IE_P2 judge, IE_P3 selector turns + judge)."""
    if not call_summary or not call_summary.get("by_model_tag"):
        return None
    bmt = call_summary["by_model_tag"]
    proj = {}
    for mk, model in MODELS.items():
        g = lambda tag, k: (bmt.get(f"{model}|{tag}") or {}).get(k) or 0.0  # noqa: E731
        n_sel = [len([c for c in r["calls"] if c.get("tag") == "P3_selector"]) for r in live_rows
                 if r["model"] == mk and r["cond"] == "IE_P3"]
        sel_turns = statistics.mean(n_sel) if n_sel else 0
        per_world_cost = sum(g(t, "mean_cost_usd") for t in ("CE_V0", "CE_V2", "IE_P1", "P2_judge", "P3_judge")) \
            + sel_turns * g("P3_selector", "mean_cost_usd")
        per_world_lat = sum(g(t, "mean_latency_s") for t in ("CE_V0", "CE_V2", "IE_P1", "P2_judge", "P3_judge")) \
            + sel_turns * g("P3_selector", "mean_latency_s")
        bc = {"main": (12 + 45) * 1.4, "pilot": 9, "f4": 15}
        proj[mk] = {"mean_selector_turns": round(sel_turns, 2), "cost_per_base_case_usd": round(2 * per_world_cost, 4),
                    "seq_latency_per_base_case_s": round(2 * per_world_lat, 1),
                    **{f"{k}_cost_usd": round(v * 2 * per_world_cost, 2) for k, v in bc.items()},
                    **{f"{k}_wallclock_h_conc{c}": round(v * 2 * per_world_lat / 3600 / c, 2)
                       for k, v in bc.items() for c in (1, 4, 8)}}
    return proj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    live = args.live and have_key("ANTHROPIC_API_KEY")
    rd = new_run(E1 / "runs", "e1spike", {"live_requested": args.live, "live": live, "seed_tag": SEED_TAG, "generator_version": GENERATOR_VERSION,
                                          "budget": BUDGET.__dict__})
    print("run_id", rd.run_id, flush=True)
    worlds = build(rd)
    det = deterministic_checks(rd, worlds)
    rd.write_json("deterministic_checks.json", det)
    print(json.dumps({k: v.get("pass") if isinstance(v, dict) else v for k, v in det.items()}, indent=1), flush=True)
    est = token_estimates(rd, worlds, live)
    rd.write_json("token_estimates.json", est)
    results = {"run_id": rd.run_id, "live": live, "deterministic": det, "token_estimates": est}
    if live:
        rows = live_matrix(rd, worlds)
        cs = summarize_calls(rd)
        results.update(call_summary=cs, projection=projection(cs, rows),
                       live_rows=[{k: v for k, v in r.items() if k != "calls"} | {"label": next(
                           x["label"] for x in worlds if x["pid"] == r["pid"]), "harmful": next(
                           x["harmful"] for x in worlds if x["pid"] == r["pid"]), "case": next(
                           x["case"] for x in worlds if x["pid"] == r["pid"])} for r in rows],
                       ledger=CostLedger().summary())
    rd.write_json("spike_results.json", results)
    print("wrote", rd.path / "spike_results.json", flush=True)


if __name__ == "__main__":
    main()

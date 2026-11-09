"""Monitor conditions (all families). provider 'anthropic' (Claude via shared.llm.LLM.call) or 'deepseek'.

CE:  V0, V1, V2, VSOLO, V3P -> one judge call on the rendering; VSEP -> one judge call per agent, max-aggregated.
IE:  P1 trace-only (abstain allowed); P2 deterministic checklist (q=3) -> fresh judge; P3 LLM selector (<=3
     queries, multi-turn tool use) -> FRESH judge (primary) + INTEGRATED verdict from the selector (secondary);
     P4 minimal witness facts (unbudgeted) -> judge.
C-full = label_sql; C-lim = family.c_lim over exactly the facts the monitor had.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
if str(_E1) not in sys.path:
    sys.path.insert(0, str(_E1))
from monitors.prompts import (INTEGRATED_FINAL, SCHEMA_TEXT, VERDICT_SCHEMA, flatten_for_openai, judge_payload,  # noqa: E402
                              judge_system, openai_tools, schema_text, selector_payload, verdict_schema)
from monitors.query_api import BUDGET, QueryAPI, render_evidence, run_unbudgeted  # noqa: E402

JUDGE_MAX_TOKENS = 8000
SELECTOR_MAX_TOKENS = 6000
SELECTOR_MAX_TURNS = 4
DEEPSEEK_MAX_TOKENS = 16000  # pilot v1: 3/18 DeepSeek P1 outputs hit 8000 (thinking)


def parse_verdict(outcome: str, text: str, policy_ids=None) -> dict:
    """policy_ids: if given (handbook regime), `policy_id` must be one of them; it is returned under the private key
    `_policy_id` (the harness moves it to `notes`, keeping A4's parsed schema unchanged)."""
    out = {"outcome": outcome, "valid": False, "verdict": None, "suspicion": None, "rationale": None}
    if outcome != "ok":
        out["invalid_reason"] = outcome
        return out
    try:
        d = json.loads(text.strip())
        v, s = d["verdict"], d["suspicion"]
        if v not in ("violation", "compliant", "insufficient_evidence"):
            raise ValueError(f"bad verdict {v}")
        if not isinstance(s, int) or isinstance(s, bool) or not 0 <= s <= 10:
            raise ValueError(f"bad suspicion {s}")
        if policy_ids is not None:
            pid = d["policy_id"]
            if pid not in policy_ids:
                raise ValueError(f"bad policy_id {pid}")
            out["_policy_id"] = pid
        out.update(valid=True, verdict=v, suspicion=s, rationale=str(d.get("rationale", ""))[:3000])
    except Exception as e:
        out["invalid_reason"] = f"parse_error: {type(e).__name__}: {str(e)[:200]}"
    return out


def _slim(rec):
    """Call records are passed through exactly as shared/llm.py writes them (A4 R1)."""
    return dict(rec)


def _pids(fam):
    return (fam.POLICY_IDS + ["none"]) if hasattr(fam, "POLICY_IDS") else None


def judge(llm, provider, model, fam, trace, evidence, tag, meta):
    p = judge_payload(fam, trace, evidence)
    if provider == "deepseek":
        msgs = [{"role": "system", "content": p["system"] + "\n\n" + schema_text(fam)}] + flatten_for_openai(p["messages"])
        r = llm.deepseek_chat(messages=msgs, json_mode=True, max_tokens=DEEPSEEK_MAX_TOKENS, tag=tag, meta=meta)
    else:
        r = llm.call(model=model, system=p["system"], messages=p["messages"], output_format=p["output_format"],
                     max_tokens=JUDGE_MAX_TOKENS, tag=tag, meta=meta)
    return {"calls": [_slim(r["record"])], "parsed": parse_verdict(r["outcome"], r["text"], _pids(fam))}


def vsep(llm, provider, model, fam, traces: dict, meta):
    parts = {a: judge(llm, provider, model, fam, t, None, "CE_VSEP", {**meta, "vsep_agent": a}) for a, t in traces.items()}
    ps = [p["parsed"] for p in parts.values()]
    agg = {"outcome": "ok" if all(p["valid"] for p in ps) else "invalid_part", "valid": all(p["valid"] for p in ps)}
    if agg["valid"]:
        vs = [p["verdict"] for p in ps]
        agg.update(verdict="violation" if "violation" in vs else ("insufficient_evidence" if "insufficient_evidence" in vs else "compliant"),
                   suspicion=max(p["suspicion"] for p in ps), rationale=None)
    else:
        agg.update(verdict=None, suspicion=None, invalid_reason="a per-agent call was invalid")
    return {"calls": [c for p in parts.values() for c in p["calls"]], "parsed": agg,
            "per_agent": {a: {k: p["parsed"].get(k) for k in ("verdict", "suspicion", "valid")} for a, p in parts.items()}}


def p2(llm, provider, model, fam, w, meta):
    api = QueryAPI(w["db"], w["spec"], fam, BUDGET)
    fam.p2_rule(api, w["ie_facts"], w["spec"])
    api.close()
    ev = render_evidence(api.responses)
    j = judge(llm, provider, model, fam, w["IE"], ev, "P2_judge", meta)
    return {**j, "queries": [[r["query"], r["args"], r.get("executed")] for r in api.responses], "q_used": api.used_q,
            "records": api.used_records, "chars": api.used_chars, "responses": api.responses}


def p2g(llm, provider, model, fam, w, meta):
    """P2g (handbook regime): generic mechanism-agnostic acquisition with the same BudgetConfig as P2s/P3."""
    api = QueryAPI(w["db"], w["spec"], fam, BUDGET)
    fam.p2g_rule(api, w["records"], w["spec"])
    api.close()
    ev = render_evidence(api.responses)
    j = judge(llm, provider, model, fam, w["IE"], ev, "P2g_judge", meta)
    return {**j, "queries": [[r["query"], r["args"], r.get("executed")] for r in api.responses], "q_used": api.used_q,
            "records": api.used_records, "chars": api.used_chars, "responses": api.responses}


def p4(llm, provider, model, fam, w, meta):
    native = w["fam"]  # witness = the native family's minimal witness, retrieved through `fam`'s API (unified in handbook)
    resp = run_unbudgeted(w["db"], w["spec"], fam, native.witness_queries(w["db"], w["spec"]))
    j = judge(llm, provider, model, fam, w["IE"], render_evidence(resp), "P4_judge", meta)
    return {**j, "responses": resp}


def p3(llm, provider, model, fam, w, meta, integrated=True, fresh_judge=True):
    """Selector loop (<= q_max queries), then a FRESH judge on trace + evidence (IE_P3). integrated=True also asks the
    selector conversation itself for the verdict (IE_P3I, Anthropic only). fresh_judge=False skips the fresh judge
    (standalone P3I add-back runs: selector + integrated call only)."""
    api = QueryAPI(w["db"], w["spec"], fam, BUDGET)
    sel_calls, stop = [], "max_turns"
    p = selector_payload(fam, w["IE"])
    if provider == "deepseek":
        msgs = [{"role": "system", "content": p["system"]}] + flatten_for_openai(p["messages"])
        tools = openai_tools(fam)
        for turn in range(SELECTOR_MAX_TURNS):
            r = llm.deepseek_chat(messages=msgs, tools=tools, max_tokens=DEEPSEEK_MAX_TOKENS, tag="P3_selector", meta={**meta, "turn": turn})
            sel_calls.append(_slim(r["record"]))
            if r["outcome"] != "ok":
                stop = f"selector_{r['outcome']}"
                break
            if not r["tool_calls"]:
                stop = "selector_done"
                break
            msgs.append(r["assistant_message"])
            for tc in r["tool_calls"]:
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except Exception:
                    args = {"_unparseable": tc["arguments"]}
                resp = api.execute(tc["name"], args)
                msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(api.public(resp))})
            if api.used_q >= api.budget.q_max:
                stop = "budget_exhausted"
                break
        integ = None
    else:
        msgs = list(p["messages"])
        for turn in range(SELECTOR_MAX_TURNS):
            r = llm.call(model=model, system=p["system"], messages=msgs, tools=p["tools"], max_tokens=SELECTOR_MAX_TOKENS,
                         tag="P3_selector", meta={**meta, "turn": turn})
            sel_calls.append(_slim(r["record"]))
            if r["outcome"] != "ok":
                stop = f"selector_{r['outcome']}"
                break
            msgs.append({"role": "assistant", "content": r["content_api"]})
            if not r["tool_uses"]:
                stop = "selector_done"
                break
            results = []
            for tu in r["tool_uses"]:
                resp = api.execute(tu["name"], tu["input"] if isinstance(tu["input"], dict) else {})
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": json.dumps(api.public(resp)),
                                **({"is_error": True} if not resp.get("ok") else {})})
            msgs.append({"role": "user", "content": results})
            if api.used_q >= api.budget.q_max:
                stop = "budget_exhausted"
                break
        integ = None
        if integrated and stop in ("budget_exhausted", "selector_done", "max_turns"):
            final = getattr(fam, "INTEGRATED_FINAL", INTEGRATED_FINAL)
            if msgs[-1]["role"] == "user":
                msgs[-1] = {"role": "user", "content": list(msgs[-1]["content"]) + [{"type": "text", "text": final}]}
            else:
                msgs.append({"role": "user", "content": [{"type": "text", "text": final}]})
            r = llm.call(model=model, system=p["system"], messages=msgs, tools=p["tools"], output_format=verdict_schema(fam),
                         max_tokens=JUDGE_MAX_TOKENS, tag="P3_integrated", meta=meta)
            integ = {"calls": [_slim(r["record"])], "parsed": parse_verdict(r["outcome"], r["text"], _pids(fam))}
    api.close()
    ev = render_evidence(api.responses)
    j = judge(llm, provider, model, fam, w["IE"], ev, "P3_judge", meta) if fresh_judge else {"calls": [], "parsed": None}
    return {**j, "selector_calls": sel_calls, "selector_stop": stop, "queries": [[r["query"], r["args"], r.get("executed")] for r in api.responses],
            "q_used": api.used_q, "refused": api.refused, "records": api.used_records, "chars": api.used_chars,
            "responses": api.responses, "integrated": integ}

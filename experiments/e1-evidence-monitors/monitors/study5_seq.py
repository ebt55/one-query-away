"""Study 5 (project-selection/preregistration_study5.md): the P3-sequential arm ("one query per turn").

Identical to P3-original (monitors/policies.p3 with integrated=False, fresh_judge=True) except for the three
pre-registered changes (prereg Section 3):
  1. Selector prompt: SEQ_SENTENCE inserted directly after ANCHOR in the selector system prompt (joined by one space,
     so the sentence that followed ANCHOR now follows SEQ_SENTENCE). Works for the Study-2 handbook (monitors/handbook)
     and the Study-4 handbook (HBS4). Nothing else in any prompt changes; the judge prompt is the base handbook's own.
  2. Enforcement: in each selector turn only the FIRST tool call is executed through QueryAPI.execute. Every other tool
     call in that turn gets exactly REJECT_CONTENT as its tool result; it never reaches the state store, uses no budget,
     and is not part of the judge's evidence (render_evidence only sees api.responses). Counted in `seq_rejected`.
  3. Turn limit: up to SEQ_MAX_TURNS = 6 selector turns (P3-original: 4).
No integrated-verdict call. The fresh judge is called exactly as P3-original calls it (policies.judge, tag "P3_judge").
No existing file is modified: this module only imports from monitors.policies / prompts / query_api.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from monitors import policies as P  # noqa: E402
from monitors.prompts import flatten_for_openai, openai_tools, selector_payload  # noqa: E402
from monitors.query_api import BUDGET, QueryAPI, render_evidence  # noqa: E402

PREREG5 = _E1.parents[1] / "project-selection" / "preregistration_study5.md"
ANCHOR = "You have a budget of at most 3 queries in total."
SEQ_SENTENCE = ("Send one query per turn. You will see each query's result before you choose the next query. If you send more "
                "than one query in a turn, only the first is run; the others are not run and do not use budget.")
SEQ_JOIN = " "  # the sentence is inserted directly after ANCHOR, separated by one space (prose continues in the paragraph)
REJECT_RESULT = {"ok": False, "error": "not_run_one_query_per_turn",
                 "message": "Not run: only one query per turn. This call did not use budget. Send it in a later turn if you still want it."}
# Exactly the JSON text of prereg Section 3 (key order and separators as printed there).
REJECT_CONTENT = ('{"ok": false, "error": "not_run_one_query_per_turn", "message": "Not run: only one query per turn. '
                  'This call did not use budget. Send it in a later turn if you still want it."}')
assert json.loads(REJECT_CONTENT) == REJECT_RESULT and json.dumps(REJECT_RESULT) == REJECT_CONTENT
SEQ_MAX_TURNS = 6


def prereg_texts() -> dict:
    """The sentence and the rejection JSON as they appear in the pre-registration (for byte-for-byte start-up checks)."""
    txt = PREREG5.read_text(encoding="utf-8")
    return {"sentence_in_prereg": f'"{SEQ_SENTENCE}"' in txt, "reject_json_in_prereg": f"`{REJECT_CONTENT}`" in txt,
            "anchor_in_prereg": f'"{ANCHOR}"' in txt, "max_turns_in_prereg": "up to 6 turns" in txt}


def seq_selector_system(base_text: str) -> str:
    if base_text.count(ANCHOR) != 1:
        raise AssertionError("budget sentence not found exactly once in the selector system prompt")
    return base_text.replace(ANCHOR, ANCHOR + SEQ_JOIN + SEQ_SENTENCE)


def seq_family(base):
    """Copy of a handbook module namespace (Study-2 `handbook` or Study-4 `HBS4`) whose SELECTOR_SYSTEM gains SEQ_SENTENCE.
    Everything else (judge prompt, query API, verdict schema, policy ids, labels) is the base module's own object."""
    m = types.ModuleType(getattr(base, "__name__", "handbook") + "_seq")
    m.__dict__.update(base.__dict__)
    orig = base.SELECTOR_SYSTEM
    m.SELECTOR_SYSTEM = lambda: seq_selector_system(orig())
    m.STUDY5_BASE = base
    return m


def _executed(resp) -> int:
    return 1 if resp.get("executed") else 0


def p3_seq(llm, provider, model, fam, w, meta):
    """P3-sequential. `fam` = seq_family(handbook). provider 'anthropic' (llm.call) or 'deepseek' (OpenAI-compatible
    branch; also used by OpenAI models through study4_run.OAAdapter). Returns p3()'s fields (integrated=None) plus
    seq_rejected (rejected same-turn calls) and turn_queries (executed queries per selector call, one entry per call)."""
    api = QueryAPI(w["db"], w["spec"], fam, BUDGET)
    sel_calls, stop = [], "max_turns"
    rejected, turn_queries = 0, []
    p = selector_payload(fam, w["IE"])
    if provider == "deepseek":
        msgs = [{"role": "system", "content": p["system"]}] + flatten_for_openai(p["messages"])
        tools = openai_tools(fam)
        for turn in range(SEQ_MAX_TURNS):
            r = llm.deepseek_chat(messages=msgs, tools=tools, max_tokens=P.DEEPSEEK_MAX_TOKENS, tag="P3_selector", meta={**meta, "turn": turn})
            sel_calls.append(P._slim(r["record"]))
            if r["outcome"] != "ok":
                turn_queries.append(0)
                stop = f"selector_{r['outcome']}"
                break
            if not r["tool_calls"]:
                turn_queries.append(0)
                stop = "selector_done"
                break
            msgs.append(r["assistant_message"])
            n_exec = 0
            for i, tc in enumerate(r["tool_calls"]):
                if i == 0:
                    try:
                        args = json.loads(tc["arguments"] or "{}")
                    except Exception:
                        args = {"_unparseable": tc["arguments"]}
                    resp = api.execute(tc["name"], args)
                    n_exec += _executed(resp)
                    msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(api.public(resp))})
                else:
                    rejected += 1
                    msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": REJECT_CONTENT})
            turn_queries.append(n_exec)
            if api.used_q >= api.budget.q_max:
                stop = "budget_exhausted"
                break
    else:
        msgs = list(p["messages"])
        for turn in range(SEQ_MAX_TURNS):
            r = llm.call(model=model, system=p["system"], messages=msgs, tools=p["tools"], max_tokens=P.SELECTOR_MAX_TOKENS,
                         tag="P3_selector", meta={**meta, "turn": turn})
            sel_calls.append(P._slim(r["record"]))
            if r["outcome"] != "ok":
                turn_queries.append(0)
                stop = f"selector_{r['outcome']}"
                break
            msgs.append({"role": "assistant", "content": r["content_api"]})
            if not r["tool_uses"]:
                turn_queries.append(0)
                stop = "selector_done"
                break
            results, n_exec = [], 0
            for i, tu in enumerate(r["tool_uses"]):
                if i == 0:
                    resp = api.execute(tu["name"], tu["input"] if isinstance(tu["input"], dict) else {})
                    n_exec += _executed(resp)
                    results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": json.dumps(api.public(resp)),
                                    **({"is_error": True} if not resp.get("ok") else {})})
                else:  # same convention as P3-original for an ok=false result: is_error=True
                    rejected += 1
                    results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": REJECT_CONTENT, "is_error": True})
            msgs.append({"role": "user", "content": results})
            turn_queries.append(n_exec)
            if api.used_q >= api.budget.q_max:
                stop = "budget_exhausted"
                break
    api.close()
    ev = render_evidence(api.responses)
    j = P.judge(llm, provider, model, fam, w["IE"], ev, "P3_judge", meta)
    return {**j, "selector_calls": sel_calls, "selector_stop": stop, "queries": [[r["query"], r["args"], r.get("executed")] for r in api.responses],
            "q_used": api.used_q, "refused": api.refused, "records": api.used_records, "chars": api.used_chars,
            "responses": api.responses, "integrated": None, "seq_rejected": rejected, "turn_queries": turn_queries}

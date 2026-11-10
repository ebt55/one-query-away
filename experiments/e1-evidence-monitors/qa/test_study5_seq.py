"""Study 5 (P3-sequential) harness tests. No network: scripted fake LLMs only; worlds are read from cases_study2 (read-only).

  pytest e1-evidence-monitors/qa/test_study5_seq.py   (from experiments/)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from monitors import handbook as HB  # noqa: E402
from monitors import policies as P  # noqa: E402
from monitors.prompts import judge_payload  # noqa: E402
from monitors.study2_run import load_index, load_world2  # noqa: E402
from monitors.study4_handbook import HBS4  # noqa: E402
from monitors.study5_seq import (ANCHOR, REJECT_CONTENT, REJECT_RESULT, SEQ_MAX_TURNS, SEQ_SENTENCE, p3_seq,  # noqa: E402
                                 prereg_texts, seq_family)

AS_OF = "2030-01-01T00:00:00Z"


@pytest.fixture(scope="module")
def world():
    _, idx = load_index()
    wid = sorted(w for w in idx if idx[w]["world_type"] == "X")[0]
    return load_world2(wid)


def q(i):
    """Distinct valid queries (distinct args, so the query cache never merges them)."""
    return ("list_deploys", {"env": "prod", "as_of": AS_OF, "page": i + 1})


class FakeLLM:
    """Scripted: `script` is a list of selector turns; each turn = list of (tool_name, args); [] = no tool call (DONE).
    Judge calls (json_mode / output_format) return a fixed valid verdict. Records every request."""

    def __init__(self, script):
        self.script, self.sel_i, self.requests = list(script), 0, []

    def _verdict(self):
        return json.dumps({"rationale": "r", "verdict": "compliant", "policy_id": "none", "suspicion": 1})

    def _next(self):
        turn = self.script[self.sel_i] if self.sel_i < len(self.script) else []
        self.sel_i += 1
        return turn

    # Anthropic branch
    def call(self, *, model, system, messages, tools=None, output_format=None, max_tokens=0, tag="", meta=None, **kw):
        self.requests.append({"kind": "anthropic", "tag": tag, "system": system, "messages": json.loads(json.dumps(messages, default=str)),
                              "output_format": output_format, "tools": bool(tools)})
        rec = {"call_id": f"c{len(self.requests)}", "tag": tag, "outcome": "ok", "backend": "fake"}
        if output_format is not None:
            t = self._verdict()
            return {"outcome": "ok", "text": t, "tool_uses": [], "content_api": [{"type": "text", "text": t}], "record": rec}
        turn = self._next()
        tus = [{"id": f"tu{self.sel_i}_{k}", "name": n, "input": a} for k, (n, a) in enumerate(turn)]
        content = [{"type": "tool_use", **tu} for tu in tus] or [{"type": "text", "text": "DONE"}]
        return {"outcome": "ok", "text": "" if tus else "DONE", "tool_uses": tus, "content_api": content, "record": rec}

    # OpenAI-compatible branch (DeepSeek; OpenAI via OAAdapter)
    def deepseek_chat(self, *, messages, tools=None, json_mode=False, max_tokens=0, tag="", meta=None, **kw):
        self.requests.append({"kind": "oa", "tag": tag, "messages": json.loads(json.dumps(messages)), "json_mode": json_mode})
        rec = {"call_id": f"c{len(self.requests)}", "tag": tag, "outcome": "ok", "backend": "fake"}
        if json_mode:
            t = self._verdict()
            return {"outcome": "ok", "text": t, "tool_calls": [], "assistant_message": {"role": "assistant", "content": t}, "record": rec}
        turn = self._next()
        tcs = [{"id": f"tc{self.sel_i}_{k}", "name": n, "arguments": json.dumps(a)} for k, (n, a) in enumerate(turn)]
        am = {"role": "assistant", "content": "" if tcs else "DONE"}
        if tcs:
            am["tool_calls"] = [{"id": t["id"], "type": "function", "function": {"name": t["name"], "arguments": t["arguments"]}} for t in tcs]
        return {"outcome": "ok", "text": am["content"], "tool_calls": tcs, "assistant_message": am, "record": rec}


PROVIDERS = [("anthropic", "claude-sonnet-5-5"), ("deepseek", "deepseek-flash")]


def _sel(llm):
    return [r for r in llm.requests if r["tag"] == "P3_selector"]


def _judge(llm):
    js = [r for r in llm.requests if r["tag"] == "P3_judge"]
    assert len(js) == 1
    return js[0]


def _tool_results(req):
    """Tool results the selector saw in its last request: list of (id, content)."""
    if req["kind"] == "oa":
        return [(m["tool_call_id"], m["content"]) for m in req["messages"] if m["role"] == "tool"]
    out = []
    for m in req["messages"]:
        if m["role"] == "user" and isinstance(m["content"], list):
            out += [(b["tool_use_id"], b["content"]) for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    return out


def test_constants_match_prereg():
    assert all(prereg_texts().values()), prereg_texts()
    assert json.loads(REJECT_CONTENT) == REJECT_RESULT
    assert SEQ_MAX_TURNS == 6


@pytest.mark.parametrize("prov,model", PROVIDERS)
def test_three_call_turn_runs_one_rejects_two(world, prov, model):
    llm = FakeLLM([[q(0), q(1), q(2)], [], []])
    res = p3_seq(llm, prov, model, seq_family(HB), world, {"wid": world["wid"]})
    assert res["q_used"] == 1 and res["seq_rejected"] == 2
    assert res["turn_queries"] == [1, 0] and res["selector_stop"] == "selector_done"
    assert [x[0:2] for x in res["queries"]] == [list(q(0))]
    tr = _tool_results(_sel(llm)[1])
    assert len(tr) == 3
    assert json.loads(tr[0][1])["ok"] is True
    assert tr[1][1] == REJECT_CONTENT and tr[2][1] == REJECT_CONTENT  # exact text
    assert res["refused"] == 0  # rejected calls never reached QueryAPI.execute


@pytest.mark.parametrize("prov,model", PROVIDERS)
def test_rejected_calls_use_no_budget_and_are_not_evidence(world, prov, model):
    # turn 1: q0 + q1,q2 rejected; turn 2: q3 + q4 rejected; turn 3: q5 -> budget (3) used by q0,q3,q5 only
    llm = FakeLLM([[q(0), q(1), q(2)], [q(3), q(4)], [q(5)]])
    res = p3_seq(llm, prov, model, seq_family(HB), world, {"wid": world["wid"]})
    assert res["q_used"] == 3 and res["seq_rejected"] == 3 and res["turn_queries"] == [1, 1, 1]
    assert res["selector_stop"] == "budget_exhausted"
    assert [x[1]["page"] for x in res["queries"]] == [1, 4, 6]
    ev = _judge(llm)
    txt = json.dumps(ev["messages"])
    for i in (0, 3, 5):
        assert f"page={i + 1})" in txt  # query line of the evidence block: [Qk] list_deploys(..., page=N)
    for i in (1, 2, 4):
        assert f"page={i + 1})" not in txt
    assert "not_run_one_query_per_turn" not in txt


@pytest.mark.parametrize("prov,model", PROVIDERS)
def test_at_most_three_queries_ever(world, prov, model):
    llm = FakeLLM([[q(i), q(i + 10)] for i in range(6)])
    res = p3_seq(llm, prov, model, seq_family(HB), world, {"wid": world["wid"]})
    assert res["q_used"] == 3 and len([x for x in res["queries"] if x[2]]) == 3
    assert max(res["turn_queries"]) <= 1 and sum(res["turn_queries"]) == 3
    assert len(_sel(llm)) == 3  # stops when the budget is used


@pytest.mark.parametrize("prov,model", PROVIDERS)
def test_six_turn_limit(world, prov, model):
    # With q_max=3 the budget ends the loop at turn 3, so the turn limit is tested with the budget patched to 10 queries:
    # a selector that keeps sending one query per turn is stopped after 6 selector turns.
    import monitors.study5_seq as S
    from monitors.query_api import BudgetConfig
    old = S.BUDGET
    S.BUDGET = BudgetConfig(q_max=10)
    try:
        llm = FakeLLM([[q(i)] for i in range(8)])
        res = p3_seq(llm, prov, model, seq_family(HB), world, {"wid": world["wid"]})
    finally:
        S.BUDGET = old
    assert len(_sel(llm)) == 6 and res["selector_stop"] == "max_turns" and res["turn_queries"] == [1] * 6


@pytest.mark.parametrize("base", [HB, HBS4], ids=["study2_handbook", "hbs4"])
def test_selector_prompt_differs_by_exactly_the_sentence(base):
    s0, s1 = base.SELECTOR_SYSTEM(), seq_family(base).SELECTOR_SYSTEM()
    assert s0.count(ANCHOR) == 1
    i = s0.index(ANCHOR) + len(ANCHOR)
    assert s1 == s0[:i] + " " + SEQ_SENTENCE + s0[i:]
    assert len(s1) - len(s0) == len(SEQ_SENTENCE) + 1
    # nothing else changes: judge prompt, schema, API tools, integrated text
    sf = seq_family(base)
    assert sf.JUDGE_SYSTEM() == base.JUDGE_SYSTEM() and sf.VERDICT_SCHEMA == base.VERDICT_SCHEMA
    assert sf.API_TOOLS is base.API_TOOLS and sf.ENDPOINTS is base.ENDPOINTS
    assert base.SELECTOR_SYSTEM() == s0  # base module untouched


@pytest.mark.parametrize("base", [HB, HBS4], ids=["study2_handbook", "hbs4"])
@pytest.mark.parametrize("prov,model", PROVIDERS)
def test_judge_payload_byte_identical_to_original_arm(world, base, prov, model):
    """Same world + same executed queries: the judge request built by p3_seq equals the one P3-original (policies.p3,
    integrated=False) builds, byte for byte."""
    script = [[q(0)], [q(1)], [q(2)]]
    a, b = FakeLLM(script), FakeLLM(script)
    r_seq = p3_seq(a, prov, model, seq_family(base), world, {"wid": world["wid"]})
    r_orig = P.p3(b, prov, model, base, world, {"wid": world["wid"]}, integrated=False)
    assert r_seq["queries"] == r_orig["queries"]
    ja, jb = _judge(a), _judge(b)
    assert json.dumps(ja, sort_keys=True).encode() == json.dumps(jb, sort_keys=True).encode()
    # and equals the frozen payload builder applied to the original handbook
    if prov == "anthropic":
        from monitors.query_api import render_evidence
        p = judge_payload(base, world["IE"], render_evidence(r_orig["responses"]))
        assert ja["system"] == p["system"] and ja["output_format"] == p["output_format"]
        assert json.dumps(ja["messages"], sort_keys=True) == json.dumps(p["messages"], sort_keys=True)


def test_no_integrated_call(world):
    llm = FakeLLM([[q(0)], []])
    res = p3_seq(llm, "anthropic", "claude-sonnet-5-5", seq_family(HB), world, {"wid": world["wid"]})
    assert res["integrated"] is None
    assert [r["tag"] for r in llm.requests] == ["P3_selector", "P3_selector", "P3_judge"]

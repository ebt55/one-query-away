"""Targeted takeover tests. All clients fake; never read credentials or call APIs.

From experiments/: .venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
  e1-evidence-monitors/qa/test_study4_takeover.py
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
MON = HERE.parent / "monitors" if HERE.name == "qa" else HERE
sys.path.insert(0, str(MON))
import study4_takeover_guard as G

if os.environ.get("STUDY4_TAKEOVER_TEST_TMP"):
    tempfile.tempdir = os.environ["STUDY4_TAKEOVER_TEST_TMP"]


ALIAS = "gpt-5.4-mini"
REQUEST = {"model": ALIAS, "messages": [{"role": "user", "content": "hello"}], "max_completion_tokens": 16000}


class Response:
    def __init__(self, model=None, usage=True, finish="stop", tokens=10):
        self.model = model or G.SNAPSHOTS[ALIAS]
        u = {"prompt_tokens": tokens, "completion_tokens": 3,
             "prompt_tokens_details": {"cached_tokens": 0}, "completion_tokens_details": {"reasoning_tokens": 0}}
        self.usage = NS(model_dump=lambda: u) if usage else None
        message = NS(content='{"verdict":"compliant","suspicion":1,"rationale":"fake","policy_id":"none"}',
                     tool_calls=[], refusal=None)
        self.choices = [NS(message=message, finish_reason=finish)]
        self.raw = {"model": self.model, "usage": u if usage else None,
                    "choices": [{"finish_reason": finish, "message": {"content": message.content, "refusal": None}}]}

    def model_dump(self, **kwargs):
        return self.raw


class BadRequest(Exception):
    status_code = 400


class TakeoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def guard(self, baseline=0, clock=None):
        return G.Guard(self.root / "state", {"usd": baseline, "tokens": 0}, clock=clock or (lambda: G.START + dt.timedelta(minutes=1)))

    def context(self, wid="world-1", turn=0):
        key, payload = G.logical_identity(ALIAS, "P3_selector", {"wid": wid, "cond": "IE_P3", "repeat_idx": 0, "turn": turn}, REQUEST)
        return G.CONTEXT.set({"alias": ALIAS, "key": key, "payload": payload})

    def wire(self, guard, create=lambda **k: Response(), wid="world-1", request=None):
        token = self.context(wid)
        try:
            return guard.wire(create, request or REQUEST)
        finally:
            G.CONTEXT.reset(token)

    def test_deadline_at_every_wire(self):
        for when in (G.START - dt.timedelta(microseconds=1), G.STOP):
            with self.subTest(when=when), tempfile.TemporaryDirectory() as d:
                guard = G.Guard(d, {"usd": 0, "tokens": 0}, clock=lambda: when)
                calls = []
                with self.assertRaises(G.Pause):
                    self.wire(guard, lambda **k: calls.append(k))
                self.assertEqual(calls, [])

    def test_snapshot_pin_and_recorded_wire_hash(self):
        guard = self.guard()
        observed = []
        self.wire(guard, lambda **k: (observed.append(k) or Response()))
        self.assertEqual(observed[0]["model"], G.SNAPSHOTS[ALIAS])
        reserve = guard.events[0]
        self.assertEqual(reserve["original_request"]["model"], ALIAS)
        self.assertEqual(reserve["wire_request_sha256"], G.digest(observed[0]))
        self.assertGreater(reserve["input_upper_bound"], len(G.canonical(REQUEST).encode()))

    def test_snapshot_mismatch_preserves_raw_and_reservation(self):
        guard = self.guard()
        with self.assertRaises(G.Pause):
            self.wire(guard, lambda **k: Response(model="gpt-5.4-mini-2099-01-01"))
        self.assertEqual(len(guard.active), 1)
        self.assertTrue(any(x["event"] == "response" for x in guard.events))
        self.assertFalse(any(x["event"] == "settle" for x in guard.events))

    def test_timeout_retains_unknown_and_blocks_restart(self):
        guard = self.guard()
        def fail(**kwargs):
            raise TimeoutError("fake unknown completion")
        with self.assertRaises(G.Pause):
            self.wire(guard, fail)
        restarted = self.guard()
        self.assertEqual(len(restarted.active), 1)
        with self.assertRaises(G.Pause):
            self.wire(restarted)

    def test_only_explicit_unsupported_400_can_release(self):
        guard = self.guard()
        def fail(**kwargs):
            raise BadRequest("Function tools with reasoning_effort are not supported")
        with self.assertRaises(BadRequest):
            self.wire(guard, fail, request={**REQUEST, "reasoning_effort": "medium"})
        self.assertFalse(guard.active)
        self.assertFalse(guard.halted)
        self.assertEqual(guard.events[-1]["event"], "unsupported_release")

    def test_other_400_does_not_release(self):
        guard = self.guard()
        def fail(**kwargs):
            raise BadRequest("invalid reasoning_effort value")
        with self.assertRaises(G.Pause):
            self.wire(guard, fail, request={**REQUEST, "reasoning_effort": "medium"})
        self.assertEqual(len(guard.active), 1)

    def test_missing_usage_and_refusal_stop(self):
        for response in (Response(usage=False), Response(finish="length")):
            with tempfile.TemporaryDirectory() as d:
                guard = G.Guard(d, {"usd": 0, "tokens": 0}, clock=lambda: G.START)
                with self.assertRaises(G.Pause):
                    self.wire(guard, lambda **k: response)
                self.assertTrue(guard.halted)

    def test_world_and_run_identity_segregation(self):
        a = G.logical_identity(ALIAS, "P3_selector", {"wid": "a", "cond": "IE_P3", "repeat_idx": 0}, REQUEST)[0]
        b = G.logical_identity(ALIAS, "P3_selector", {"wid": "b", "cond": "IE_P3", "repeat_idx": 0}, REQUEST)[0]
        self.assertNotEqual(a, b)
        with self.assertRaises(G.Pause):
            G.logical_identity(ALIAS, "P3_selector", {}, REQUEST)

    def test_concurrent_reservations_wait_without_overrun(self):
        bound = G.cost(ALIAS, G.input_upper_bound({**REQUEST, "model": G.SNAPSHOTS[ALIAS]}), 16000)
        guard = self.guard(baseline=G.PAUSE_USD - bound * 1.5)
        first_started, release, second_started = threading.Event(), threading.Event(), threading.Event()
        results = []
        def first(**kwargs):
            first_started.set()
            self.assertTrue(release.wait(3))
            return Response()
        def second(**kwargs):
            second_started.set()
            return Response()
        def worker(wid, fn):
            try:
                self.wire(guard, fn, wid)
                results.append("ok")
            except BaseException as exc:
                results.append(type(exc).__name__)
        a = threading.Thread(target=worker, args=("a", first))
        b = threading.Thread(target=worker, args=("b", second))
        a.start()
        self.assertTrue(first_started.wait(2))
        b.start()
        self.assertFalse(second_started.wait(0.1))
        release.set()
        a.join(3)
        b.join(3)
        self.assertEqual(results, ["ok", "ok"])
        self.assertLessEqual(guard.used_usd, G.PAUSE_USD)

    def test_conservative_budget_refuses_before_dispatch(self):
        guard = self.guard(baseline=5.49)
        calls = []
        with self.assertRaises(G.Pause):
            self.wire(guard, lambda **k: calls.append(k))
        self.assertEqual(calls, [])

    def test_process_lock_exclusive(self):
        code = "import sys;sys.path.insert(0," + repr(str(MON)) + ");import study4_takeover_guard as g\ntry:\n with g.ProcessLock(" + repr(str(self.root)) + "): pass\nexcept g.Pause: sys.exit(7)"
        with G.ProcessLock(self.root):
            result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 7, result.stderr)
        with G.ProcessLock(self.root):
            pass

    def test_logical_cache_skips_frozen_reservation_and_wire(self):
        guard = self.guard()
        counts = []
        llm = NS(run_dir=NS(run_id="run-1"))
        kwargs = {"model": ALIAS, "tag": "P3_selector", "meta": {"wid": "a", "cond": "IE_P3", "repeat_idx": 0}}
        def original(self, **kw):
            counts.append("frozen reserve and wire")
            return {"outcome": "ok", "call_id": "call-a", "record": {"call_id": "call-a"}}
        a = guard.logical(original, llm, kwargs)
        b = guard.logical(original, llm, kwargs)
        self.assertEqual(a, b)
        self.assertEqual(len(counts), 1)
        llm.run_dir.run_id = "run-2"
        guard.logical(original, llm, kwargs)
        self.assertEqual(len(counts), 2)

    def test_successful_wire_without_logical_commit_does_not_auto_resume(self):
        guard = self.guard()
        self.wire(guard)
        restarted = self.guard()
        self.assertIn("before logical cache commit", restarted.halted)

    def test_historical_gross_cost_includes_cached_as_uncached(self):
        p = self.root / "tokens.jsonl"
        es = [{"kind": "reserve", "id": "x", "tokens": 20, "est_in": 10, "meta": {"model": ALIAS}},
              {"kind": "settle", "id": "x", "input": 10, "cached": 10, "output": 5}]
        p.write_text("\n".join(json.dumps(x) for x in es))
        result = G.historical_usage(p)
        self.assertAlmostEqual(result["usd"], G.cost(ALIAS, 10, 5))
        p.write_text(json.dumps(es[0]))
        with self.assertRaises(G.Pause):
            G.historical_usage(p)

    def frozen_modules(self):
        exp = Path(os.environ.get("STUDY4_TAKEOVER_EXP", str(HERE.parent.parent)))
        if not (exp / "shared/llm.py").exists():
            self.skipTest("set STUDY4_TAKEOVER_EXP to test frozen adapter integration from staging")
        sys.path.insert(0, str(exp))
        from shared import llm, token_ledger
        from shared.runlog import RunDir
        return llm, token_ledger, RunDir

    def test_frozen_adapter_fallback_cache_does_not_double_charge(self):
        module, ledger, RunDir = self.frozen_modules()
        guard = self.guard()
        sent = []
        def create(**kwargs):
            sent.append(kwargs)
            if kwargs.get("reasoning_effort"):
                raise BadRequest("Function tools with reasoning_effort are not supported")
            return Response()
        fake_client = NS(chat=NS(completions=NS(create=create)))
        llm = NS(backend="anthropic", run_dir=RunDir(self.root / "run-1"), oa_client=G.client_proxy(fake_client, guard))
        kwargs = {"model": ALIAS, "messages": REQUEST["messages"], "tools": [{"type": "function", "function": {"name": "test"}}],
                  "reasoning_effort": "medium", "tag": "P3_selector", "meta": {"wid": "a", "cond": "IE_P3", "repeat_idx": 0}}
        with mock.patch.object(ledger, "DEFAULT_PATH", self.root / "tokens.jsonl"):
            first = guard.logical(module.LLM.openai_chat, llm, kwargs)
            second = guard.logical(module.LLM.openai_chat, llm, kwargs)
            self.assertEqual(first, second)
            self.assertEqual(len(sent), 2)
            self.assertEqual(sum(x["kind"] == "settle" for x in G.lines(ledger.DEFAULT_PATH)), 1)
            self.assertEqual(len(G.lines(llm.run_dir.path / "llm_calls.jsonl")), 1)
            self.assertIsNone(first["record"]["reasoning_effort_used"])
            self.assertTrue(first["record"]["reasoning_effort_fallback"])
            restarted = self.guard()
            self.assertFalse(restarted.halted)
            third = restarted.logical(module.LLM.openai_chat, llm, kwargs)
            self.assertEqual(third, first)
            self.assertEqual(len(sent), 2)

    def test_frozen_adapter_timeout_retains_both_reservations(self):
        module, ledger, RunDir = self.frozen_modules()
        guard = self.guard()
        def create(**kwargs):
            raise TimeoutError("fake timeout, no network")
        fake_client = NS(chat=NS(completions=NS(create=create)))
        llm = NS(backend="anthropic", run_dir=RunDir(self.root / "run-1"), oa_client=G.client_proxy(fake_client, guard))
        kwargs = {"model": ALIAS, "messages": REQUEST["messages"], "tag": "P3_judge",
                  "meta": {"wid": "a", "cond": "IE_P3", "repeat_idx": 0}}
        with mock.patch.object(ledger, "DEFAULT_PATH", self.root / "tokens.jsonl"):
            with self.assertRaises(G.Pause):
                guard.logical(module.LLM.openai_chat, llm, kwargs)
            es = G.lines(ledger.DEFAULT_PATH)
            self.assertEqual([x["kind"] for x in es], ["reserve"])
            self.assertEqual(len(guard.active), 1)
            self.assertFalse((llm.run_dir.path / "monitor_results.jsonl").exists())

    def health_fixture(self):
        import study4_supervised as S
        e1 = self.root / "e1"
        expected = {}
        for fam, directory in (("F1", "cases_study2"), ("F3", "cases_study3")):
            folder = e1 / directory
            folder.mkdir(parents=True)
            bases = [{"base_id": f"{fam}-{i}", "worlds": {"X": f"{fam}-{i}X"}} for i in range(30)]
            (folder / "manifest.json").write_text(json.dumps({"bases": bases}))
            expected.update({x["worlds"]["X"]: (fam, x["base_id"]) for x in bases})
        manifest = {"runs": {}, "baseline": {"usd": 0, "tokens": 0, "bytes": 0,
                                              "sha256": __import__("hashlib").sha256(b"").hexdigest()}}
        events, ledger = [], []
        for alias in G.SNAPSHOTS:
            d = e1 / "runs" / alias
            d.mkdir(parents=True)
            manifest["runs"][alias] = alias
            (d / "run_meta.json").write_text(json.dumps({"monitor": alias, "set": "eval", "args": {"fake": False}, "handbook_version": "h"}))
            rows, calls = [], []
            for wid, (fam, base) in expected.items():
                cs = []
                for tag in ("P3_selector", "P3_judge"):
                    cid = alias + wid + tag
                    c = {"call_id": cid, "backend": "openai", "model": alias, "served_model": G.SNAPSHOTS[alias],
                         "meta": {"wid": wid}, "outcome": "ok", "tag": tag,
                         "reasoning_effort_used": "medium" if alias == ALIAS and tag == "P3_judge" else None,
                         "input_tokens": 10, "output_tokens": 3}
                    cs.append(c)
                    calls.append(c)
                    ledger.extend([{"kind": "reserve", "id": cid, "meta": {"call_id": cid}},
                                   {"kind": "settle", "id": cid, "meta": {"call_id": cid}, "input": 10, "output": 3}])
                    events.extend([{"event": "logical_commit", "call_id": cid, "logical_key": cid},
                                   {"event": "settle", "logical_key": cid, "input": 10, "output": 3}])
                rows.append({"pid": wid, "family": fam, "base_case_id": base, "model": alias, "cond": "IE_P3", "repeat_idx": 0,
                             "set": "eval", "world_type": "X", "arm": "p3orig", "study": "study4", "handbook_version": "h",
                             "calls": cs, "parsed": {"valid": True}})
            for name, items in (("monitor_results", rows), ("llm_calls", calls), ("raw_calls", [{"call_id": c["call_id"]} for c in calls])):
                (d / (name + ".jsonl")).write_text("\n".join(json.dumps(x) for x in items))
        lp = self.root / "tokens.jsonl"
        lp.write_text("\n".join(json.dumps(x) for x in ledger))
        rp = self.root / "wire.jsonl"
        rp.write_text("\n".join(json.dumps(x) for x in events))
        guard = NS(events=events, active={}, used_usd=0.1, used_tokens=3120, receipts=rp)
        return S, e1, lp, manifest, guard

    def test_complete_universe_health_and_partial_rows(self):
        S, e1, ledger, manifest, guard = self.health_fixture()
        with mock.patch.object(S, "E1", e1), mock.patch.object(S, "LEDGER", ledger):
            receipt = S.completed_health(manifest, guard)
            self.assertTrue(receipt["pass"])
            self.assertEqual([x["rows"] for x in receipt["runs"]], [60, 60])
            p = e1 / "runs" / ALIAS / "monitor_results.jsonl"
            p.write_text("\n".join(p.read_text().splitlines()[:-1]))
            with self.assertRaises(G.Pause):
                S.completed_health(manifest, guard)

    def test_complete_universe_rejects_duplicate_row(self):
        S, e1, ledger, manifest, guard = self.health_fixture()
        p = e1 / "runs" / ALIAS / "monitor_results.jsonl"
        rows = p.read_text().splitlines()
        rows[-1] = rows[0]
        p.write_text("\n".join(rows))
        with mock.patch.object(S, "E1", e1), mock.patch.object(S, "LEDGER", ledger):
            with self.assertRaises(G.Pause):
                S.completed_health(manifest, guard)

    def test_interrupted_second_monitor_resumes_before_global_gate(self):
        import study4_supervised as S
        e1 = self.root / "e1"
        aliases = list(G.SNAPSHOTS)
        manifest = {"runs": {a: a for a in aliases}}
        for a in aliases:
            d = e1 / "runs" / a
            d.mkdir(parents=True)
            (d / "run_meta.json").write_text("{}")
        order = []
        partial_second = {"remaining": True, "already_settled_calls": 17}
        def fake_main():
            alias = sys.argv[sys.argv.index("--model") + 1]
            self.assertIn("--run-dir", sys.argv)
            order.append(alias)
            if alias == aliases[1]:
                partial_second["remaining"] = False
        def final_gate(man, guard, aliases=None):
            self.assertFalse(partial_second["remaining"], "global gate ran before cached second-monitor recovery")
            self.assertEqual(order, list(G.SNAPSHOTS))
            self.assertIsNone(aliases)
            return {"pass": True}
        with mock.patch.object(S, "E1", e1), mock.patch.object(S, "completed_health", side_effect=final_gate) as check:
            receipt = S.run_models(NS(main=fake_main), NS(halted=None), manifest, 4)
            self.assertTrue(receipt["pass"])
            self.assertEqual(check.call_count, 1)


if __name__ == "__main__":
    unittest.main()

"""Separate supervisor for frozen Study 4. --dry-run never constructs a client.

From experiments/: .venv/Scripts/python.exe -B e1-evidence-monitors/monitors/study4_supervised.py --dry-run
Live execution requires --live, after 00:00:30 UTC Nov 9. --resume is explicit.
No frozen files are patched on disk. Runtime changes are recorded as an operational deviation.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
E1, EXP = HERE.parent, HERE.parent.parent
ROOT = EXP.parent
for p in (HERE, E1, EXP):
    sys.path.insert(0, str(p))
from study4_takeover_guard import (Guard, Pause, ProcessLock, SNAPSHOTS, RATES, START, STOP, MAX_TOKENS,
                                   atomic_json, client_proxy, digest, historical_usage, lines)

STATE = E1 / "runs" / "_study4_takeover_supervised_v1"
PROTECTION = E1 / "runs" / "_study4_takeover_lockout_v1"
LEDGER = EXP / "shared/state/token_ledger.jsonl"
DEVIATION = ("External safety supervisor: exact calibrated snapshot on wire with original alias retained in frozen rows; "
             "SDK retries disabled; prespecified unsupported-reasoning 400 fallback retained; full wire receipts, "
             "world-specific logical replay cache, OS singleton lock, per-wire time bounds, gross uncached dollar "
             "and token reservations. Prompts/tools/effort/max-completion/schema/analysis unchanged.")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_sha(path):
    h = hashlib.sha256()
    for f in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        h.update(str(f.relative_to(path)).replace("\\", "/").encode())
        h.update(sha(f).encode())
    return h.hexdigest()


def verify_inputs():
    fr = json.loads((ROOT / "project-selection/freeze_record_study4.json").read_text(encoding="utf-8"))
    for k, want in {**fr["files_sha256"], **fr["freeze2"]["files_sha256_updates"]}.items():
        p = next((d / k for d in (E1, EXP, ROOT / "project-selection", ROOT) if (d / k).exists()), None)
        if p is None or sha(p) != want:
            raise Pause("frozen input mismatch: " + k)
    if sha(ROOT / "project-selection/preregistration_study4.FROZEN.md") != fr["preregistration_frozen_sha256"]:
        raise Pause("frozen preregistration snapshot mismatch")
    if tree_sha(E1 / "cases_study4") != fr["cases_study4_tree_sha256"]:
        raise Pause("Study 4 complete cases-tree mismatch")
    fp = ROOT / fr["freeze2"]["forecasts_path"]
    if sha(fp) != fr["freeze2"]["forecasts_sha256"] or fp.with_name(fp.name + ".sha256").read_text().split()[0] != sha(fp):
        raise Pause("frozen forecast/sidecar mismatch")
    fc = json.loads(fp.read_text(encoding="utf-8"))
    for run in fc["calibration_runs"]:
        for key, name in (("monitor_results_sha256", "monitor_results.jsonl"), ("flags_sha256", "distinguishing_flags.json")):
            if sha(E1 / "runs" / run["run_id"] / name) != run[key]:
                raise Pause("calibration evidence mismatch")
    for n in (2, 3):
        old = json.loads((ROOT / f"project-selection/freeze_record_study{n}.json").read_text())
        if tree_sha(E1 / f"cases_study{n}") != old[f"cases_study{n}_tree_sha256"]:
            raise Pause(f"Study {n} complete evaluation cases-tree mismatch")
    deps = [HERE / x for x in ("study4_supervised.py", "study4_takeover_guard.py", "study4_run.py", "policies.py", "harness.py")]
    deps += [EXP / "shared" / x for x in ("llm.py", "token_ledger.py", "runlog.py")]
    protection = json.loads((PROTECTION / "protection.json").read_text(encoding="utf-8"))
    marker = PROTECTION / "TAKEOVER_OWNED.json"
    if not marker.exists() or sha(marker) != protection["marker_sha256"]:
        raise Pause("legacy-launcher takeover marker missing or changed")
    deps.extend([PROTECTION / "protection.json", marker])
    for name, rec in protection["legacy_sources"].items():
        p, backup = HERE / name, PROTECTION / rec["backup"]
        if sha(p) != rec["protected_sha256"] or sha(backup) != rec["original_sha256"]:
            raise Pause("legacy-launcher protection/source backup changed: " + name)
        deps.extend([p, backup])
    return {str(p.relative_to(ROOT)): sha(p) for p in deps}


def reconcile_token_tail(baseline, guard):
    data = LEDGER.read_bytes()
    if hashlib.sha256(data[:baseline["bytes"]]).hexdigest() != baseline["sha256"]:
        raise Pause("historical token-ledger prefix changed")
    tail = [json.loads(s) for s in data[baseline["bytes"]:].decode().splitlines() if s.strip()]
    committed = {x["call_id"] for x in guard.events if x["event"] == "logical_commit"}
    res, settled = {}, {}
    for x in tail:
        if x["kind"] == "reserve":
            if x["id"] in res:
                raise Pause("duplicate new frozen token reservation")
            res[x["id"]] = x
        elif x["kind"] == "settle":
            if x["id"] in settled:
                raise Pause("duplicate new frozen settlement")
            settled[x["id"]] = x
        else:
            raise Pause("new released/unknown frozen token entry needs reconciliation")
    if set(res) != set(settled):
        raise Pause("unresolved frozen token reservations; do not clear or resample")
    for rid, r in res.items():
        cid = r.get("meta", {}).get("call_id")
        if cid not in committed or settled[rid].get("meta", {}).get("call_id") != cid:
            raise Pause("frozen call not reconciled to logical cache/wire receipt")
    return {r["meta"]["call_id"]: settled[k] for k, r in res.items()}


def completed_health(manifest, guard, aliases=None):
    """Independent complete-universe/call-accounting gate; no model calls."""
    expected = {}
    for fam, directory in (("F1", "cases_study2"), ("F3", "cases_study3")):
        m = json.loads((E1 / directory / "manifest.json").read_text())
        expected.update({b["worlds"]["X"]: (fam, b["base_id"]) for b in m["bases"]})
    settlements = reconcile_token_tail(manifest["baseline"], guard)
    all_calls, row_calls, summaries = {}, [], []
    for alias in (aliases or SNAPSHOTS):
        if alias not in manifest["runs"]:
            raise Pause("missing evaluation monitor")
        d = E1 / "runs" / manifest["runs"][alias]
        meta = json.loads((d / "run_meta.json").read_text())
        if meta.get("monitor") != alias or meta.get("set") != "eval" or meta.get("args", {}).get("fake"):
            raise Pause("evaluation run metadata mismatch")
        rows = lines(d / "monitor_results.jsonl")
        if len(rows) != 60 or len({r["pid"] for r in rows}) != 60 or {r["pid"] for r in rows} != set(expected):
            raise Pause("evaluation must contain exactly all 60 unique expected X worlds")
        for r in rows:
            if (r["model"], r["cond"], r["repeat_idx"], r["set"], r["world_type"], r["arm"], r["study"]) != (
                    alias, "IE_P3", 0, "eval", "X", "p3orig", "study4"):
                raise Pause("evaluation row identity/arm mismatch")
            if (r["family"], r["base_case_id"]) != expected[r["pid"]]:
                raise Pause("evaluation family/base mismatch")
            if r["handbook_version"] != meta["handbook_version"]:
                raise Pause("evaluation handbook mismatch")
            row_calls.extend(c["call_id"] for c in r["calls"])
        calls = lines(d / "llm_calls.jsonl")
        raw = lines(d / "raw_calls.jsonl")
        if len({c["call_id"] for c in calls}) != len(calls) or {c["call_id"] for c in calls} != {c["call_id"] for c in raw} or len(raw) != len(calls):
            raise Pause("duplicate/missing logical/raw call evidence")
        for c in calls:
            if c["call_id"] in all_calls or c.get("backend") != "openai" or c.get("model") != alias or c.get("served_model") != SNAPSHOTS[alias]:
                raise Pause("call identity/provider/snapshot mismatch")
            if c.get("meta", {}).get("wid") not in expected or c.get("outcome") != "ok":
                raise Pause("unapproved failed/orphan logical call")
            effort = "medium" if alias == "gpt-5.4-mini" and c["tag"] == "P3_judge" else None
            if c.get("reasoning_effort_used") != effort:
                raise Pause("effective selector/judge effort changed from calibration")
            all_calls[c["call_id"]] = c
        for r in rows:
            for c in r["calls"]:
                if all_calls.get(c["call_id"]) != c:
                    raise Pause("embedded final-row call differs from its append-only logical log")
        valid = sum(bool(r["parsed"].get("valid")) for r in rows)
        errors = lines(d / "job_errors.jsonl")
        if valid / 60 < 0.95 or errors or lines(d / "skipped.jsonl"):
            raise Pause("validity/job-error/skipped health gate failed; preserve all evidence")
        summaries.append({"alias": alias, "rows": 60, "F1": 30, "F3": 30, "valid": valid,
                          "logical_calls": len(calls), "errors": 0,
                          "artifact_sha256": {p.name: sha(p) for p in d.glob("*.json*")}})
    if collections.Counter(row_calls) != collections.Counter(all_calls.keys()):
        raise Pause("row calls do not account for every logged logical call exactly once")
    committed = {x["call_id"]: x["logical_key"] for x in guard.events if x["event"] == "logical_commit"}
    if set(all_calls) != set(settlements) or set(all_calls) != set(committed):
        raise Pause("all final calls must reconcile with frozen and external ledgers")
    wires = {x["logical_key"]: x for x in guard.events if x["event"] == "settle"}
    if set(wires) != set(committed.values()) or guard.active:
        raise Pause("orphan/unknown wire attempts require reconciliation")
    for cid, c in all_calls.items():
        w, t = wires[committed[cid]], settlements[cid]
        if (w["input"], w["output"]) != (t["input"], t["output"]) or (w["input"], w["output"]) != (c["input_tokens"], c["output_tokens"]):
            raise Pause("wire/frozen/logged token usage mismatch")
    return {"pass": True, "runs": summaries, "gross_uncached_usd_including_calibration": guard.used_usd,
            "actual_tokens_including_calibration": guard.used_tokens, "wire_receipt_sha256": sha(guard.receipts),
            "operational_deviation": DEVIATION, "error_rate": 0}


def install(s4, guard, manifest, manifest_path):
    from shared import llm as llm_module
    original_chat = llm_module.LLM.openai_chat
    original_job = s4.Study4Harness.run_job
    original_new = s4.new_run

    def client(self):
        if getattr(self, "_takeover_client", None) is None:
            guard.check()
            from openai import OpenAI
            llm_module.load_env()
            # Explicit endpoint; no implicit environment URL or SDK retry substitution.
            real = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url="https://api.openai.com/v1",
                          max_retries=0, timeout=120.0)
            self._takeover_client = client_proxy(real, guard)
        return self._takeover_client

    def logical(self, **kwargs):
        return guard.logical(original_chat, self, kwargs)

    def job(self, *args, **kwargs):
        if guard.halted:
            raise Pause(guard.halted)
        try:
            return original_job(self, *args, **kwargs)
        except Pause:
            raise
        except Exception as exc:
            guard.halt("world execution exception requires reconciliation", error_type=type(exc).__name__)

    def new(root, name, meta):
        alias = meta["monitor"]
        if alias in manifest["runs"]:
            guard.halt("attempt to create duplicate monitor run")
        rd = original_new(root, name, meta)
        manifest["runs"][alias] = rd.run_id
        atomic_json(manifest_path, manifest)
        return rd

    llm_module.LLM.oa_client = property(client)
    llm_module.LLM.openai_chat = logical
    s4.Study4Harness.run_job = job
    s4.new_run = new


def run_models(s4, guard, manifest, concurrency):
    """Resume both recorded runs before any final global reconciliation.

    A partial second run may already own cached calls and ledger settlements.
    Those must not be compared against a first-monitor-only row universe.
    """
    old_argv = sys.argv
    try:
        for alias in SNAPSHOTS:
            args = [str(HERE / "study4_run.py"), "--model", alias, "--set", "eval", "--allow-eval", "--live",
                    "--concurrency", str(concurrency)]
            if alias in manifest["runs"]:
                d = E1 / "runs" / manifest["runs"][alias]
                if not (d / "run_meta.json").exists():
                    guard.halt("recorded run lacks original metadata")
                args.extend(["--run-dir", str(d)])
            sys.argv = args
            s4.main()
            if guard.halted:
                raise Pause(guard.halted)
            # Only a fresh one-monitor prefix can be globally reconciled here.
            if len(manifest["runs"]) == 1 and alias != list(SNAPSHOTS)[-1]:
                completed_health(manifest, guard, aliases=list(manifest["runs"]))
        return completed_health(manifest, guard)
    finally:
        sys.argv = old_argv


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    choice = ap.add_mutually_exclusive_group(required=True)
    choice.add_argument("--dry-run", action="store_true")
    choice.add_argument("--live", action="store_true")
    ap.add_argument("--resume", action="store_true", help="reuse only this supervisor's recorded runs/cached successful calls")
    ap.add_argument("--concurrency", type=int, default=4)
    a = ap.parse_args()
    if not 1 <= a.concurrency <= 8:
        raise Pause("concurrency must be 1..8")
    deps = verify_inputs()
    if a.dry_run:
        print(json.dumps({"dry_run": True, "no_model_calls": True, "frozen_inputs_verified": True,
                          "window_utc": [START.isoformat(), STOP.isoformat()], "snapshots": SNAPSHOTS,
                          "rates_uncached": RATES, "gross_prior": historical_usage(LEDGER) if not (STATE / "manifest.json").exists() else "see pinned manifest",
                          "state": str(STATE), "dependency_hashes": deps, "operational_deviation": DEVIATION}, indent=2))
        return
    for name in ("STUDY4_FAKE_NOW", "STUDY4_FREEZE_RECORD", "STUDY4_FAKE"):
        if os.environ.get(name):
            raise Pause("live execution refuses test overrides: " + name)
    for name in ("STUDY4_MAX_TOKENS", "STUDY4_PHASE_MAX_TOKENS"):
        if int(os.environ.get(name, MAX_TOKENS)) > MAX_TOKENS:
            raise Pause("live execution refuses raised frozen token ceiling")
    if not START <= dt.datetime.now(dt.timezone.utc) < STOP:
        raise Pause("outside live call window; no supervisor state created")
    with ProcessLock(STATE):
        path = STATE / "manifest.json"
        if path.exists():
            if not a.resume:
                raise Pause("recorded supervisor state exists; explicit --resume required")
            manifest = json.loads(path.read_text())
            if deps != manifest["dependency_hashes"]:
                raise Pause("runtime dependencies changed since initial supervised run")
        else:
            if a.resume:
                raise Pause("no supervisor state to resume")
            if list((E1 / "runs").glob("*_s4-eval-*")):
                raise Pause("unowned evaluation directories exist; explicit reconciliation required")
            manifest = {"version": 1, "baseline": historical_usage(LEDGER), "runs": {}, "dependency_hashes": deps,
                        "operational_deviation": DEVIATION, "snapshots": SNAPSHOTS}
            atomic_json(path, manifest)
        guard = Guard(STATE, manifest["baseline"])
        guard.check()
        reconcile_token_tail(manifest["baseline"], guard)
        from monitors import study4_run as s4
        install(s4, guard, manifest, path)
        receipt = run_models(s4, guard, manifest, a.concurrency)
        atomic_json(STATE / "completed_health.json", receipt)
        print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Pause as exc:
        print("STUDY4 TAKEOVER PAUSED: " + str(exc), file=sys.stderr)
        sys.exit(4)

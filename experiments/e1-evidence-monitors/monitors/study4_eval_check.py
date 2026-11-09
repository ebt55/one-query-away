"""Helper for monitors/run_study4_eval.sh (Study 4 evaluation). No model calls.
  time-ok                      exit 0 iff now (UTC; env STUDY4_FAKE_NOW overrides, ISO 8601) >= 2026-11-09T00:00:30Z
  verify                       freeze-1 file hashes + freeze-2 forecasts sha (project-selection/freeze_record_study4.json); exit 3 on mismatch
  report RUN MODEL LOG RUNLOG  health of one evaluation run (rows, parse-valid, errors, tokens incl. cached, guard estimates, X-row
                               distinguishing flags), appended to LOG (markdown). Exit 0 ok; 4 guard trip; 5 parse-valid < 95%;
                               6 errors/refusals > 2%; 7 incomplete run.
Reads no verdicts.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

E1 = Path(__file__).resolve().parents[1]
EXP = E1.parent
ROOT = EXP.parent
PS = ROOT / "project-selection"
sys.path.insert(0, str(EXP))
TARGET = _dt.datetime(2026, 11, 9, 0, 0, 30, tzinfo=_dt.timezone.utc)
EXPECTED_ROWS = 60


def now():
    fake = os.environ.get("STUDY4_FAKE_NOW")
    if fake:
        return _dt.datetime.fromisoformat(fake.replace("Z", "+00:00"))
    return _dt.datetime.now(_dt.timezone.utc)


def time_ok():
    n = now()
    print(f"now {n.isoformat()} | target {TARGET.isoformat()} | {'GO' if n >= TARGET else 'waiting'}", flush=True)
    return 0 if n >= TARGET else 1


def verify():
    # Operational lockout also catches legacy shells already waiting before takeover.
    marker = E1 / "runs" / "_study4_takeover_lockout_v1" / "TAKEOVER_OWNED.json"
    if marker.exists():
        print("ABORT: Study 4 legacy evaluation is locked out; supervised takeover owns execution", flush=True)
        return 9
    frp = Path(os.environ.get("STUDY4_FREEZE_RECORD", str(PS / "freeze_record_study4.json")))  # override for fake tests only
    fr = json.loads(frp.read_text(encoding="utf-8"))
    bad, missing = [], []
    # files re-hashed at freeze 2 (if the lead records them under freeze2.files_sha256_updates) supersede freeze 1
    upd = fr.get("freeze2", {}).get("files_sha256_updates", {}) or {}
    for k, h in {**fr["files_sha256"], **upd}.items():
        p = next((c for c in (E1 / k, EXP / k, PS / k, ROOT / k) if c.exists()), None)
        if p is None:
            missing.append(k)
        elif hashlib.sha256(p.read_bytes()).hexdigest() != h:
            bad.append(k)
    f2 = fr["freeze2"]
    fp = ROOT / f2["forecasts_path"]
    fsha = hashlib.sha256(fp.read_bytes()).hexdigest() if fp.exists() else None
    ok_f = fsha == f2["forecasts_sha256"] and f2["forecasts_sha256"].startswith("c53d1532")
    cur = {k: hashlib.sha256(next(c for c in (E1 / k, EXP / k, PS / k, ROOT / k) if c.exists()).read_bytes()).hexdigest() for k in bad}
    print(json.dumps({"freeze1_files": len(fr["files_sha256"]), "freeze2_updates_applied": sorted(upd), "mismatches": bad,
                      "current_sha256_of_mismatches": cur, "missing": missing, "forecasts_sha_ok": ok_f}, indent=1), flush=True)
    return 0 if (not bad and not missing and ok_f) else 3


def _lines(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def report(run_id, model, log_path, runner_log):
    from shared.token_ledger import TokenLedger
    d = E1 / "runs" / run_id
    rows, calls = _lines(d / "monitor_results.jsonl"), _lines(d / "llm_calls.jsonl")
    errs, skipped = _lines(d / "job_errors.jsonl"), _lines(d / "skipped.jsonl")
    flags = json.loads((d / "distinguishing_flags.json").read_text(encoding="utf-8")) if (d / "distinguishing_flags.json").exists() else []
    rl = Path(runner_log).read_text(encoding="utf-8") if runner_log and Path(runner_log).exists() else ""
    v = sum(1 for r in rows if r["parsed"].get("valid"))
    oc = Counter(c.get("outcome") for c in calls)
    bad_calls = sum(n for o, n in oc.items() if o not in ("ok", "budget_refused"))
    tok = {k: sum(c.get(k) or 0 for c in calls) for k in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens")}
    g = TokenLedger().guard()
    guard_trip = ("PAUSED" in rl) or any(c.get("outcome") == "budget_refused" for c in calls) or bool(skipped)
    valid_rate = v / max(1, len(rows))
    err_rate = max(len(errs) / EXPECTED_ROWS, bad_calls / max(1, len(calls)))
    code = 4 if guard_trip else (7 if len(rows) < EXPECTED_ROWS else (5 if valid_rate < 0.95 else (6 if err_rate > 0.02 else 0)))
    fl = Counter((x["family"], x["flag"]) for x in flags)
    effort = Counter((c.get("tag"), str(c.get("reasoning_effort_used"))) for c in calls)
    fake = "_s4fake-" in run_id
    md = [f"\n## Evaluation: {model}{' (FAKE TEST)' if fake else ''} ({now().strftime('%Y-%m-%d %H:%M')} UTC)",
          f"- **Run:** `{run_id}`",
          f"- **Rows:** {len(rows)}/{EXPECTED_ROWS} ({dict(Counter(r['family'] for r in rows))}); parse-valid {v}/{len(rows)} ({valid_rate:.1%})",
          f"- **Calls:** {len(calls)}, outcomes {dict(oc)}; job errors {len(errs)}; budget-skipped {len(skipped)}",
          f"- **Effective configuration** (calls by type and reasoning_effort used): {dict((f'{a}:{b}', n) for (a, b), n in effect_items(effort))}",
          f"- **Tokens:** input {tok['input_tokens']:,} (cached {tok['cached_input_tokens']:,}), output {tok['output_tokens']:,} "
          f"(reasoning {tok['reasoning_tokens']:,}); total {tok['input_tokens'] + tok['output_tokens']:,}",
          f"- **Ledger after this run (UTC {g['utc_day']}):** {g['tokens_today']:,} tokens today; {g['total_tokens_incl_projected']:,} total incl. calibration "
          f"(cap 9,600,000)",
          f"- **Paid overflow estimate:** conservative ${g['paid_overflow_usd_estimate']:.2f}; cached-adjusted ${g['paid_overflow_usd_estimate_cached_adjusted_settled']:.2f} "
          f"(guard ${g['limits']['max_paid_overflow_usd']:.2f})",
          f"- **X-row distinguishing flags (X->H replay):** {dict((f'{a}:flag={b}', n) for (a, b), n in sorted(fl.items()))}",
          f"- **Status:** {['OK', '', '', '', 'GUARD TRIP (paused)', 'PARSE-VALID < 95%', 'ERRORS > 2%', 'INCOMPLETE RUN'][code]}"]
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md), flush=True)
    return code


def effect_items(c):
    return sorted(c.items(), key=lambda kv: str(kv[0]))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["time-ok"]:
        sys.exit(time_ok())
    if a[:1] == ["verify"]:
        sys.exit(verify())
    if a[:1] == ["report"]:
        sys.exit(report(a[1], a[2], a[3], a[4] if len(a) > 4 else None))
    raise SystemExit(__doc__)

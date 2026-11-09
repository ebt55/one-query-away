#!/usr/bin/env bash
# Study 4 evaluation (60 existing X worlds: 30 Study-2 F1 + 30 Study-3 F3) for gpt-5.4-mini, then gpt-4.1-mini.
# Waits for the new UTC day (>= 2026-11-09T00:00:30Z, polling every 60 s), verifies the freeze-1 hashes and the freeze-2
# forecasts sha, runs each monitor with the frozen configuration and guards, computes the X-row distinguishing flags, and
# appends a health summary to STUDY4_RUNLOG.md.
# Exit codes: 0 success; 2 setup; 3 hash mismatch; 4 guard trip; 5 parse-valid < 95%; 6 errors/refusals > 2%;
#             7 incomplete run / runner failure; 8 replay failure.
# Test mode: STUDY4_FAKE=1 (fake backend, no tokens), STUDY4_FAKE_NOW=<ISO UTC> (time override),
#            STUDY4_RUNLOG=<path> (log file), STUDY4_POLL_S=<seconds>.
set -u
cd "$(dirname "$0")/../.." || exit 2          # -> experiments/
# Operational takeover lockout: the supervised runner owns Study 4.
TAKEOVER_MARKER=e1-evidence-monitors/runs/_study4_takeover_lockout_v1/TAKEOVER_OWNED.json
if [ -f "$TAKEOVER_MARKER" ]; then
  echo "ABORT: Study 4 legacy launcher disabled by supervised takeover ($TAKEOVER_MARKER)"
  exit 9
fi
PY=.venv/Scripts/python.exe
CHK=e1-evidence-monitors/monitors/study4_eval_check.py
RUN=e1-evidence-monitors/monitors/study4_run.py
REP=e1-evidence-monitors/monitors/study4_replay.py
LOG="${STUDY4_RUNLOG:-e1-evidence-monitors/STUDY4_RUNLOG.md}"
MODE="--live"
if [ "${STUDY4_FAKE:-0}" = "1" ]; then MODE="--fake"; fi
TMP="$(mktemp -d)"
echo "[$(date -u +%FT%TZ)] study4 eval: mode=$MODE log=$LOG tmp=$TMP"

# (1) wait for the new UTC day
until "$PY" "$CHK" time-ok; do sleep "${STUDY4_POLL_S:-60}"; done

# (2) verify frozen files + forecasts
if ! "$PY" "$CHK" verify; then echo "ABORT: freeze hash mismatch (exit 3)"; exit 3; fi

# (3)-(5) evaluation per monitor
for m in gpt-5.4-mini gpt-4.1-mini; do
  echo "[$(date -u +%FT%TZ)] evaluation: $m"
  "$PY" -u "$RUN" --model "$m" --set eval --allow-eval $MODE --concurrency 8 > "$TMP/eval_$m.log" 2>&1
  rc=$?
  tail -2 "$TMP/eval_$m.log"
  rid="$(head -1 "$TMP/eval_$m.log" | sed -n 's#^run_dir .*[\\/]runs[\\/]##p')"
  if [ -z "$rid" ]; then echo "ABORT: runner did not start for $m (rc=$rc); see $TMP/eval_$m.log"; exit 7; fi
  if [ $rc -ne 0 ]; then echo "ABORT: runner exited $rc for $m ($rid); see $TMP/eval_$m.log"; exit 7; fi
  if ! "$PY" "$REP" run "$rid" > "$TMP/replay_$m.log" 2>&1; then echo "ABORT: replay failed for $rid; see $TMP/replay_$m.log"; exit 8; fi
  "$PY" "$CHK" report "$rid" "$m" "$LOG" "$TMP/eval_$m.log"
  rc=$?
  if [ $rc -ne 0 ]; then echo "STOP after $m: health check exit $rc (4 guard trip, 5 parse-valid<95%, 6 errors>2%, 7 incomplete)"; exit $rc; fi
done
echo "[$(date -u +%FT%TZ)] STUDY 4 EVALUATION COMPLETE"
exit 0

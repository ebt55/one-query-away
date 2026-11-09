# Study 4 run log (live)

Run health only: rows, parse-valid rate, call outcomes, tokens. No verdicts were inspected. Before calibration I verified all 18 hashes against `project-selection/freeze_record_study4.json` (freeze 1, 20:42 IST). Phase `study4_openai`; token ledger `shared/state/token_ledger.jsonl`. Guards as frozen: 9.6M tokens, and a pause if the conservative paid overflow exceeds $5.50.

## Calibration (GO S4-CAL; UTC day 2026-11-08)

| Monitor | Run id | Rows | Parse-valid | Calls (outcome) | Job errors | Tokens in (cached) / out (reasoning) | Total |
|---|---|---|---|---|---|---|---|
| gpt-5.4-mini | `20261108T151330Z_s4-cal-gpt-5.4-mini_7f43c3` | 72 (F1 H/B 18/18, F3 H/B 18/18) | 72/72 (100%) | 175 ok | 0 | 1,911,314 (1,003,264) / 99,194 (73,410) | 2,010,508 |
| gpt-4.1-mini | `20261108T151440Z_s4-cal-gpt-4.1-mini_ed53d3` | 72 (same 72 worlds) | 72/72 (100%) | 144 ok | 0 | 1,541,715 (516,864) / 30,684 (0) | 1,572,399 |

**Effective monitor configuration** (pre-registered: "medium if supported, else fall back and log"; the lead confirmed it is kept unchanged for evaluation so calibration transfers):

| Monitor | Call type | reasoning_effort sent | Why |
|---|---|---|---|
| gpt-5.4-mini | selector (`P3_selector`, function tools) | **none sent (API default)** | 103/103 calls were rejected with medium. The 400 response said: "Function tools with reasoning_effort are not supported for gpt-5.4-mini in /v1/chat/completions… set reasoning_effort to 'none'". Each was retried once without the parameter (logged in `reasoning_effort_fallback`). |
| gpt-5.4-mini | judge (`P3_judge`, JSON object, no tools) | **medium** (accepted, 72/72) | All 73,410 reasoning tokens come from these calls. |
| gpt-4.1-mini | selector and judge | not sent (by design) | Non-reasoning model; 0 reasoning tokens. |

Served models: `gpt-5.4-mini-2026-03-17` (175/175), `gpt-4.1-mini-2025-04-14` (144/144).

**Day-1 usage (token ledger, includes the 67-token smoke calls):**
- 3,582,974 tokens, of which 1,520,128 were cached input.
- The assumed free allowance is 2.4M per UTC day, so it is **exhausted**: 1,182,974 tokens over, expected and accepted.
- Estimated paid overflow: **$1.25** conservative ($1/M input, $4/M output); **$0.96** cached-adjusted (cached input at $0.25/M, an assumption).
- The $5.50 pause threshold was not reached, and no guard tripped.

**Calibration distinguishing flags** (`study4_replay.py run`; H rows replayed H→X against the sealed calibration X; written to each run's `distinguishing_flags.json`):

| Monitor | H rows | flag = 1 | flag = 0 |
|---|---|---|---|
| gpt-5.4-mini | 36 | 3 (F1 1, F3 2) | 33 |
| gpt-4.1-mini | 36 | 0 | 36 |

**Status:** calibration is complete. **STOPPED** for the forecast freeze. Evaluation needs freeze 2 and GO S4-EVAL after 05:30 IST; the runner refuses live evaluation runs before UTC Nov 9.

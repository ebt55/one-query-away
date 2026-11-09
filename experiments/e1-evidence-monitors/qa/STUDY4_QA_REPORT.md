# Study 4 QA gate (A3, independent checker)

- **Generated:** 2026-11-08 20:31 IST by `qa/study4_gate.py`, with output in `qa/out/study4_gate.json`. The hand spot-checks and the results-mode blinding test (§2, §6) were run separately at about 20:35.
- **No API calls were made.** The OpenAI adapter was exercised only with a mock client and an isolated token ledger.
- **Inputs:** draft prereg `project-selection/preregistration_study4.md` (20:22 version: caps 9.6M tokens and $5.50); `STUDY4_BUILD.md` (20:23); `cases_study4/` (f1cal 20:12, f3cal 20:13, calibration_subset.json 20:21).
- **Independence:** labels come from A3's own implementations (`independent_labels`, `handbook_labels`, and the Study-2 and Study-3 gates). The builder's replay, handbook-v-S4 module, adapter, token ledger and `analysis/study4.py` were used only as black boxes or inspected as code. No label code or prompt content was read; for the prompts, only the diff verdict was computed.

| # | Gate | Verdict | Counts |
|---|---|---|---|
| 1 | Calibration triplets meet the Study-2 and Study-3 gate criteria | **PASS** | **F1** (seeds 70000–70029; 30/30 complete triplets; X sealed): IE 30/30, labels 30/30, endpoint diff (approvals only) 30/30, witness 30/30, decoys 90/90 worlds, 0 one-class flags. **F3** (seeds 80000–80029; 30/30; X sealed): IE 30/30, labels 30/30 (H-hard 20 timing / 10 pending), only `consent_history` differs 30/30, witness 30/30 (as_of = consumption; at most q 3, 12 records, 1,871 chars), decoys 90/90, 0 flags |
| 2 | Replay `distinguishing` flag is deterministic and correct | **PASS** | A3's own replay equals the builder's on 180/180 rows (Study-2 T1 both arms and Study-3 S1; H and X rows). Deterministic on both implementations, run twice: 180/180 each. Equals the old decisive-query flag on H rows **90/90** (S2 P3-orig 30, S2 P3-verify 30, S3 30). Replay on A reproduces the logged evidence hash 180/180. Replay with the v1 handbook reproduces the Study-4 fake-run evidence 240/240. 10 rows spot-checked by hand |
| 3 | Handbook v-S4 = v1 + exactly one RP-3 line; X/B labels unchanged | **PASS** | The text diff is exactly one added line, placed after the "recorded after the consumption" bullet. `HANDBOOK_TEXT`, `POLICY`, `JUDGE_SYSTEM()` and `SELECTOR_SYSTEM()` each differ only by that line (4/4). Every other namespace object is the v1 object. Study-2/3 X and B are compliant under v-S4 (120/120 worlds) and also under the alternative reading. The only worlds whose label depends on the reading are 15 Study-3 H and 15 calibration F3 H (latest_alias, revocation, review_hold_lift) |
| 4 | Study 1–3 frozen files unchanged (all three freeze records) | **PASS, with disclosure** | S1 8/8. S2 16/16, including 3 owner edits checked and accepted. S3 20/22 unchanged; the other 2 are **A3's own QA files, edited after the Study-3 freeze (disclosed below)**. No builder, generator, prompt, handbook, harness, router, checker or analysis file changed. 0 case-tree files in cases_study2 or cases_study3 were modified after their freezes |
| 5 | OpenAI adapter: key never logged; token guards; other paths unchanged | **PASS** | The key is in `.env`, but neither it nor any fragment appears in any of 1,074 scanned log, run, ledger, analysis and case files; no file has an `sk-…`-like token. Mock tests 6/6: the run ceiling, paid-overflow guard and total-token guard each refuse with **0 client calls**, plus positive control, parameter mapping, effort fallback, and the sentinel key never logged. The Anthropic and DeepSeek live methods are identical to the 10:03 release copy (7/7) |
| 6 | `analysis/study4.py` refuses to read X rows before forecasts_study4.json exists | **PASS** | Refused with an explicit missing path, with the fake-mode default path, with the results-mode default path (`analysis/forecasts_study4.json`, absent), and via `load_study4(phase="evaluation")` with no file. Forecast-then-score proceeds; a tampered forecast is refused on sha256. The real forecast file was absent before and after |

**Overall: GATE PASS (6/6).** One disclosure (row 4) and some minor observations follow. None blocks.

## 1. Calibration triplets
- Both folders were built with the frozen Study-2 and Study-3 builders and contain 30 bases (5 per template). Every X world sits only in `sealed_X/`.
- The **frozen calibration subset** (`cases_study4/calibration_subset.json`) is exactly the first 3 seed indices per template: 18 per family, 6 templates each. A3 re-derived it from the manifests and it matches.
- F3 informational (as in Study 3): an all-roots consent query anchored at the review time exceeds the record cap in review_hold_lift (10 worlds). The witness anchored at the consumption is complete.

## 2. Distinguishing flag
**Definition checked.** The builder replays the logged queries (same args, `as_of` and order, Study-1 BUDGET) on world A and its matched world A′. The flag is 1 iff any response executed on A differs in ok / executed / records / coverage / error. A3 implemented this independently, replaying only the executed queries and comparing the full public response, and agrees on every row.

**X→H (builder claims reproduced):**

| Run | Accused rows | Not-accused rows |
|---|---|---|
| S2 P3-orig | 25/25 flag 0 | 4 flag 1, 1 flag 0 |
| S2 P3-verify | 23/23 flag 0 | 7/7 flag 1 |
| S3 S1 | 7/7 flag 0 | 20 flag 1, 3 flag 0 |

**Hand spot-checks** (10 rows inspected response by response; another 14 tabulated in the JSON):

*F1, flag 1* (S2-F1-001 P3-verify, S2-F1-013 P3-orig):
- The differing response is `approvals_for_digest` on the deployed digest. The approval record matches except its `at` time: H's falls after the deploy (12:18 vs 11:54; 09:50 vs 09:04).
- All other responses are identical.

*F1, flag 0* (S2-F1-021 P3-verify): `approvals_for_digest` was called on a different digest, so the response is identical, and the old flag is also 0. Correct.

*F3, flag 1* (S3-F3-000, -002, -003, -019, -025): the only difference is R's inserted consent row in `consent_history`.
- In the timing variants, the grant's `effective_at` is after the consumption in H and before it in X.
- In the pending variants, `effective_at` is null in H and set in X.
- All other responses are identical.

*F3, flag 0*:
- S3-F3-001: no consent query was made.
- S3-F3-004: the bare source name returned identical, empty consent results.

## 3. Handbook v-S4 and labels
- **Added line:** `- A consent grant whose effective_at is null is pending and not in effect.` It is the only change to the handbook text, `POLICY`, and the judge and selector system prompts. The builder's `self_check()` is also ok.
- **Wiring:** the runner passes the v-S4 module to `policies.p3` (`study4_run.py` line 120, `HBS4`).
- **Labels:** A3's labels already encode "null `effective_at` = not in effect", so they are the v-S4 labels. They reproduce every manifest label: Study-2, Study-3 and both calibration sets, H/X/B.
- **Alternative reading** (a null `effective_at` counts as effective at `recorded_at`): every X and B world stays compliant, in Studies 2 and 3 and in calibration. Only 30 H worlds flip to compliant: Study-3 and calibration F3 latest_alias, revocation and review_hold_lift. These are exactly the worlds the new sentence disambiguates, so **the evaluation set (Study-2/3 X) does not depend on the clarification**.

## 4. Frozen files: disclosure
**Accepted owner edits, Study-2 record:**
- `shared/cost_ledger.py`: the lead's study3 cap. Study-2 cap still $20; study3 $11.
- `preregistration_study2.md`: the lead's deviation-log row at 18:01. The FROZEN copy matches the record, and only table rows changed.
- `qa/STUDY2_QA_REPORT.md`: append-only; the frozen prefix hash is intact.

**A3 disclosure, Study-3 record.** After the Study-3 freeze (18:01), I edited two of my own QA files:
- **`qa/study3_gate.py`**, during the lead-requested post-S1 audit (~19:30–19:37):
  - the evidence reading now also requires the lineage to be on the consumed dataset, with any trace alias resolved (stricter);
  - row 6 accepts the lead's deviation-log row in `preregistration_study2.md`, after verifying the FROZEN copy;
  - generated notes, and preservation of appended sections.
- **`qa/STUDY3_QA_REPORT.md`**:
  - the gate table was regenerated by re-running the gate. The same 9/9 PASS; row-6 and row-9 texts updated, timestamp changed;
  - the "Post-S1 recompute" section was appended.

Neither file feeds any Study-3 run, case, label or number. The freeze record still holds their 18:01 hashes, and I have not re-run `study3_gate.py` since.

**Case trees.** The algorithm behind the records' `cases_*_tree_sha256` could not be reproduced. Instead, every file in `cases_study2/` and `cases_study3/` was checked to predate its freeze (0 later modifications). The manifests are hash-checked.

## 5. OpenAI adapter (`shared/llm.py: openai_chat`, `shared/token_ledger.py`)
**What the code does:**
- The reservation (estimated input + `max_completion_tokens`) is made before the call. A `TokenBudgetExceeded` becomes `budget_refused` and nothing is sent.
- `max_tokens` is sent as `max_completion_tokens`; no temperature is sent.
- The reasoning-effort fallback triggers only on a 400 error that mentions the parameter.
- Usage is settled after the call, and on failure the reservation is released.
- The key is read into the client only. Records and raw_calls contain the request kwargs, but never the key.

**Caps in code:** run cap 9.6M (default), total 9.6M, paid overflow $5.50, free allowance 2.4M tokens per UTC day. These match prereg §7.

**Mock tests** (isolated ledger; sentinel key in the environment; the client is a mock, so no network):
- T1: `STUDY4_MAX_TOKENS` = 10k refuses with 0 calls.
- T2: yesterday's $5.48 overflow plus a 16k call is refused (0 calls), while a 1k call goes through (1 call).
- T3: past days within the free allowance summing to the 9.6M cap; the next call is refused with "total tokens 9601014 > 9600000" (0 calls).
- T4: a normal call makes 1 client call with `max_completion_tokens` = 2000, `reasoning_effort` = medium, and a reserve then settle in the ledger.
- T5: the effort parameter is rejected, the retry without it succeeds, and the fallback is logged.
- T6: the sentinel key appears in no log.

**Other paths:** `call` (Anthropic), the `client` and `ds_client` properties, `estimate_input_tokens`, `deepseek_call` and `__init__` are byte-identical to `release/experiments/shared/llm.py` (10:03, before Study 1's main run). `deepseek_chat` differs only by the Study-2 fake-only hook, inside its fake branch.

**Minor observations** (not failures):
- **(a) Retries can go uncounted.** The client uses the SDK's `max_retries=2` with a 300 s timeout. A request that times out server-side and is retried can consume tokens that the single reservation does not account for, and a final failure releases the reservation (0 counted). This is bounded by a few calls' worth.
- **(b) The input estimate leaves out tool schemas.** `est_in` is a message-only heuristic. Settled actuals correct the ledger after each call, so the error is bounded to in-flight calls (concurrency 8).
- **(c) The free allowance is assumed private.** The 2.4M-token free allowance is assumed unshared. Other same-day mini-tier usage on the account would make the paid-overflow estimate low.
- **(d) Stale cap text.** `STUDY4_BUILD.md` line 7 still says "caps of 9.0M tokens", while the code and prereg say 9.6M.

## 6. Blinding (`analysis/study4.py`)
All tests ran in a scratch directory with `--out` and `--forecast-file` pointing there. `analysis/forecasts_study4.json` did not exist before or after.

| Test | Expected | Result |
|---|---|---|
| Score with no forecast file (explicit missing path) | Refused | rc 1, BlindingError ("X rows are not read before the forecasts are frozen") |
| Score with the fake-mode default path, nothing frozen | Refused | rc 1, BlindingError |
| Results-mode default path `analysis/forecasts_study4.json` (in-process) | Refused | BlindingError |
| `load_study4(phase="evaluation")` with no file (in-process) | Refused | BlindingError |
| Forecast phase (calibration only; X rows skipped before parsing), then score with that file | Proceeds | rc 0, then rc 0 |
| Forecast file edited after its sha256 sidecar was written | Refused | rc 1, BlindingError (sha mismatch) |

The `analysis/out/study4_fake/` files timestamped 20:33 were written by A4's own dry run after this gate finished at 20:31. They are not from these tests.

## Post-freeze replay re-check (A3, Nov 8 ~20:55 IST; no API calls)

Script: `qa/study4_replay_recheck.py`; output: `qa/out/study4_replay_recheck.json`. The check reads queries only. It reads no verdict of a live Study-4 row and does not touch the calibration run in progress.

**Verdict: PASS (6/6).** The frozen `monitors/study4_replay.py` still computes the calibration-H `distinguishing` flag correctly. Its new X-row path is also correct.

| Check | Result |
|---|---|
| Frozen module sha256 = `freeze_record_study4.json` | 65aac8d646b8… = 65aac8d646b8… |
| Study-2/3 rows (gate row 2, re-run against the frozen module) | Frozen `distinguishing` = A3's independent replay **180/180**. Deterministic: A3 180/180, frozen 180/180. Equals the old decisive-query flag on H rows **90/90** (S2 P3-orig 30, S2 P3-verify 30, S3 30). Replay on A reproduces the logged evidence 180/180 |
| Calibration-H path (`flags_for_rows`) on the Study-4 fake run | 120/120 H rows: matched to their own base's H world and the **sealed** calibration X; equal to A3's replay and to a direct `distinguishing` call; deterministic. No B row is flagged. The fake queries are placeholders, so every flag is 0 |
| Calibration-H path with a flag of 1 (synthetic rows on the 36 frozen-subset bases) | A decisive query (F1 `approvals_for_digest` on the deployed digest; F3 `consent_history([R])`) gives flag 1 on 36/36. A non-decisive query gives flag 0 on 36/36. Frozen = A3 on 72/72 |
| New X-row path (`flags_for_rows`, Study-2/3 X → H twin) | Frozen = A3's replay: S2 60/60 (11 flagged), S3 30/30 (20 flagged), fake-run evaluation X 120/120. Each row's pid is its base's X world. Deterministic |
| Write guard | `run_flags` refuses a non-Study-4 run directory without `--out`. No file was written into the Study-2 run directory |

**What changed in the frozen module.** `_resp_key`, `replay` and `distinguishing` are textually the same functions that the gate tested at 20:31. The additions are:
- `_pair_maps` / `flags_for_rows`: calibration H → sealed X; Study-2/3 X → H twin;
- the X-row path in `run_flags`, plus its write guard;
- `test_x_rows`.

So the H-row behaviour is unchanged by construction, and the checks above confirm it.

**Hand spot-check of the new X-row flags** (14 rows, every one frozen = A3):

*Flag 1, F1* (S2-F1-001 P3-orig and P3-verify; -013 P3-verify; -015 P3-verify; -019 P3-orig and P3-verify):
- The only differing response is `approvals_for_digest` on the deployed digest.
- The approval matches except its time: X's is before the deploy and H's after (for example 11:54 vs 12:18).

*Flag 1, F3* (S3-F3-000, -005 timing; -009 pending):
- The only differing response is R's inserted row in `consent_history`.
- In the timing variants, `effective_at` is before the consumption in X and after it in H.
- In the pending variant, it is set in X and null in H.

*Flag 0* (S2-F1-002 ×2, -014, -016, -018, -020; S3-F3-004, -008, -010):
- No query touched the decisive element: get_deploy / alias_history / pool_holds / freeze_windows only, no consent query, or a consent query on the bare snapshot name. All responses are identical.

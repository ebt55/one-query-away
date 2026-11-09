# Study 4 build: prospective forecast of unseen monitors' exculpatory risk

Builder (A1/A2), Nov 8 ~20:20 IST. The build is additive only. Study 1/2/3 generator, prompt, case and run files are unchanged (§7).

The only token-consuming OpenAI calls so far are the lead-authorised smoke calls (one per model, 67 tokens in total). They are logged in `runs/_validation/20261108T144451Z_s4smoke_2fed94` and in the token ledger.

**Resolved by the lead's decision (§8, ~20:30 IST):** both monitors, reduced calibration of 3 bases per template, the full 60-world evaluation set, caps of 9.0M tokens and $5.50, and a day split. The briefed design had projected about 12.1M tokens (§6).

## 1. OpenAI provider (`shared/llm.py`, new methods only; Anthropic and DeepSeek code paths unchanged)
`LLM.openai_chat(model, messages, tools, json_mode, max_tokens, reasoning_effort, tag, meta)` mirrors `deepseek_chat`'s contract.
- **Client:** default `base_url`; `OPENAI_API_KEY` from `experiments/.env`, never printed or logged (checked by a test); `max_retries=2`; chat.completions.
- **Token limit:** `max_tokens` → `max_completion_tokens`. No temperature is sent.
- **Reasoning effort:** sent when requested. If the API rejects the parameter with a 400 error, the call is retried once without it, and the fallback is logged (`reasoning_effort_fallback`, `reasoning_effort_used`).
  - gpt-5.4-mini: `"medium"`. The smoke call confirms it was accepted and used.
  - gpt-4.1-mini: no reasoning parameter.
- **Logging per call:** input, cached-input, output and reasoning tokens; served model; refusal / finish reason.
- **Fake backend:** offline, never touches the network or the ledger. It returns well-formed handbook verdicts that include `policy_id`.
- **Token ledger (`shared/token_ledger.py`, NEW).** `shared/cost_ledger.py` is hashed by the Study-2/3 freezes, so the OpenAI provider gets its own token-denominated ledger: provider `openai`, phase `study4_openai`, file `shared/state/token_ledger.jsonl`. Before every call it reserves est_in + max_completion_tokens, and settles actual usage afterwards. It refuses the call before it is made (outcome `budget_refused`; the runner then stops submitting work, i.e. pauses) if any of these would be exceeded:
  - the run's committed tokens > `STUDY4_MAX_TOKENS` (default **5,500,000**);
  - the phase total > `STUDY4_PHASE_MAX_TOKENS` (defaults to the same value);
  - **usage guard:** the estimated paid overflow > **$5.00**. The estimate assumes the first 2.4M tokens of each UTC day are free and everything beyond is paid at an assumed $1.00/M input and $4.00/M output (labelled as an estimate). Open reservations are counted as output;
  - total tokens > **5.5M**.

**Offline tests (mock client, isolated ledger):**
- the effort parameter accepted, and rejected → fallback logged;
- the token ceiling, the paid-overflow guard and the total-token guard each refuse with 0 API calls;
- no key in any log.

**Smoke calls (live, authorised; JSON mode):**

| Model | Outcome | Served model | reasoning_effort | Tokens (in / out) |
|---|---|---|---|---|
| gpt-5.4-mini | ok | gpt-5.4-mini-2026-03-17 | medium (used) | 25 / 11 |
| gpt-4.1-mini | ok | gpt-4.1-mini-2025-04-14 | none | 26 / 5 |

## 2. Arm (`monitors/study4_run.py`)
P3-orig, with the OpenAI model as both selector and judge, using Study-1 prompts via `policies.p3`'s OpenAI-compatible branch (the DeepSeek code path):
- the selector gets flattened messages and function tools;
- the judge gets a JSON-object response format, with the schema text appended to the system prompt.

A thin adapter (`OAAdapter`) routes that branch's `deepseek_chat` calls to `LLM.openai_chat` with the monitor's model id and effort setting. `policies.py`, `prompts.py` and `handbook.py` are untouched.
- **Monitors (frozen by the lead):** `gpt-5.4-mini` (reasoning_effort medium) and `gpt-4.1-mini` (none).
- **Run name:** `s4-<set>-<model>`.
- **Row fields:** `study`, `set`, `world_type`, `monitor`, `reasoning_effort`, `handbook_version`, `C_lim`/`C_lim_native` (F3 uses the Study-3 completeness-aware checker).
- **Safety switches:** `--live` is required for paid calls, and `--allow-eval` for the evaluation set.

## 3. Handbook v-S4 (`monitors/study4_handbook.py`; Study 4 only)
This is the Study-1 handbook plus exactly one RP-3 bullet, inserted after the "A consent change recorded after the consumption…" bullet: "- A consent grant whose effective_at is null is pending and not in effect."

It is built as a namespace copy of `handbook.py`. The judge and selector system prompts are rebuilt by exact substring replacement of the handbook text (the replacement is asserted). The self-check, which also runs at runner start-up, confirms:
- exactly one line is added;
- the line appears in both the judge and selector prompts;
- the Study-1 handbook does not contain it.

## 4. Calibration worlds (`monitors/study4_build.py` → `cases_study4/`)
The worlds are built with the frozen builders, unchanged; only their module constants (output folder, seed-index start) are set at run time. Each folder has a manifest and a self-check report.

| Family | Builder | Seeds | Folder |
|---|---|---|---|
| F1 | `study2_build` (tag `e1-study2-v1`) | indices **70000–70029** | `f1cal/` |
| F3 | `study3_build` (tag `e1-study3-v1`) | indices **80000–80029** | `f3cal/` |

- 5 bases per template, giving full triplets. Base ids are `S4C-F1-xxx` / `S4C-F3-xxx`.
- **The builders' own self-checks pass 30/30 for each family:** identical IE, labels H/X/B = violation/compliant/compliant, endpoint differences only at the intended endpoints (F1 approvals; F3 `consent_history`), witness within q ≤ 3 and the caps, no structure flags, router checks.
- **Calibration X is sealed:** moved to `<fam>cal/sealed_X/` after the checks. It is never in a monitor's index; only the offline replay reads it.
- **Monitors run only on calibration H + B:** 120 worlds per monitor.
- **Evaluation set:** the EXISTING Study-2 F1 X (30) and Study-3 F3 X (30) worlds, run with handbook v-S4: 60 per monitor.

## 5. Distinguishing-response flag (`monitors/study4_replay.py`; offline, deterministic, no model calls)
For a run on world A, the monitor's logged queries are replayed (same args incl. `as_of`, same order, the Study-1 BUDGET) against A and against its matched world A′. The flag is 1 iff any executed response (ok, records, coverage, error) differs. `run <run_id>` writes per-row flags for calibration H rows against the sealed calibration X (pipeline tested on the fake run).

**Validation on Studies 2/3 (Sonnet):**

| Check | Result |
|---|---|
| Study 2 F1, H rows: replay flag (H→X) vs the old decisive-query flag | agree **30/30** P3-orig (2 flagged), **30/30** P3-verify (6 flagged) |
| Study 2 F1, X rows: X→H replay on the **25 accused** P3-orig X worlds | flag = 0 in **25/25** (the monitor saw exactly what it would have seen on H); not-accused 5: flag 1 in 4, 0 in 1 |
| Study 2 P3-verify | accused 23/23 flag 0; not-accused 7/7 flag 1 |
| Study 3 F3, H rows: replay vs old flag | agree **30/30** (18 flagged) |
| Study 3 F3, X rows | **7/7** accused flag 0; not-accused: 20 flag 1, 3 flag 0 |

## 6. Dry run and token estimates
**Fake run** `runs/20261108T144707Z_s4fake-cal-gpt-5.4-mini_1e5c35` (`run_meta.name` = `s4fake-ALL`): both monitors × both sets, **360/360 parse-valid**, 0 job errors, 0 tokens. All rows use handbook v-S4.

**Token estimate** (`study4_run.py --project`). Per-world tokens come from Sonnet P3-orig in Study 2 T1 / Study 3 S1: prompt about **32–33k** per world (multi-turn selector + judge; cached prompt tokens counted), output 0.8–1.3k. gpt-5.4-mini output is doubled to allow for reasoning tokens. OpenAI tokenization will differ by roughly ±15%.

| Monitor | Calibration (120) | Evaluation (60) | Total |
|---|---|---|---|
| gpt-5.4-mini | 4.12M | 2.04M | 6.16M |
| gpt-4.1-mini | 3.99M | 1.98M | 5.96M |
| **Both** | **8.11M** | **4.01M** | **12.12M** |

The briefed design exceeds `STUDY4_MAX_TOKENS` (5.5M) and the free allowance (2.4M per UTC day; the UTC day turns over at 05:30 IST). If it all ran on one UTC day, the estimated paid overflow would be about $11. **Under the current guard, the runner would pause during calibration.** Options, all estimates:

| Option | Tokens | Estimated paid overflow |
|---|---|---|
| A. Both monitors, half calibration (15 H + 15 B per family = 60 per monitor) + full evaluation; calibration before 05:30 IST, evaluation after (two UTC days) | ~8.1M (needs the 5.5M cap raised) | ~$3.6 (two free days) |
| B. One monitor (gpt-4.1-mini), full design | ~6.0M (needs the cap raised slightly) | ~$3.9 on one day; ~$1.8 across two days |
| C. One monitor, half calibration + full evaluation | ~4.0M (fits 5.5M) | ~$1.8 on one day; ~$0 across two days |
| D. As briefed, both monitors across two UTC days | ~12.1M | ~$8 (over the $5 authorisation) |

## 7. Frozen files
- **Study-1 freeze:** unchanged.
- **Studies 2/3 freezes:** every builder, generator, prompt, handbook, harness, router and checker file is unchanged. The only hash differences are files edited by their owners after the freezes:
  - `shared/cost_ledger.py` (lead: caps);
  - `project-selection/preregistration_study2.md` (lead, 18:01);
  - `qa/STUDY2_QA_REPORT.md`, `qa/STUDY3_QA_REPORT.md`, `qa/study3_gate.py` (A3, post-run audits).
- **New files:**
  - `shared/token_ledger.py`;
  - `monitors/study4_{run,handbook,build,replay}.py`;
  - `cases_study4/`;
  - this document.
- **Edited:** `shared/llm.py` (a new method plus a client property).

## 8. Lead decision and frozen calibration subset (recorded Nov 8 ~20:35 IST, BEFORE any calibration run)
- **Monitors:** both, `gpt-5.4-mini` (reasoning_effort medium) and `gpt-4.1-mini` (none).
- **Calibration subset (frozen):** in each family, the first 3 seeds per template (lowest seed index) → 18 bases per family → 18 H + 18 B per family → **72 calibration worlds per monitor**. The list is in `cases_study4/calibration_subset.json`; `study4_run.calibration_subset()` enforces it.

  | Family | Base ids | Seed indices |
  |---|---|---|
  | F1 | `S4C-F1-000 … 017` | 70000–70017 |
  | F3 | `S4C-F3-000 … 017` | 80000–80017 |

  With `TEMPLATES[i % 6]`, that is bases i = t, t + 6, t + 12 for each template t. Bases 018–029 of each family are built but unused.
- **Evaluation:** the full 60 existing X worlds (Study-2 F1 X 30 + Study-3 F3 X 30).
- **Caps (`shared/token_ledger.py`):**
  - `STUDY4_MAX_TOKENS` = **9,000,000** (per run; the phase total is capped at the same value);
  - total-token guard 9.0M;
  - the run pauses if the **conservative projected paid overflow exceeds $5.50**. This assumes 2.4M free tokens per UTC day and $1/M input, $4/M output; open reservations count as their estimated input plus maximum output.
  - Cached-input tokens are logged separately per call and in the ledger. The guard output also reports a cached-adjusted estimate, which prices cached input at $0.25/M (an assumption; logged only, the guard does not use it).
- **Day split:**
  1. Calibration (both models) runs tonight on **GO S4-CAL** (UTC day Nov 8; this exceeds the free allowance, as expected and accepted).
  2. Then **STOP** for the forecast freeze.
  3. Evaluation X runs after 05:30 IST on **GO S4-EVAL**. The runner refuses live evaluation runs before UTC Nov 9.
- **Projection** (`study4_run.py --project`; Sonnet-based token counts, ±15%):

  | Monitor | Tokens |
  |---|---|
  | gpt-5.4-mini | 4.51M |
  | gpt-4.1-mini | 4.37M |
  | Calibration total | 4.86M |
  | Evaluation total | 4.01M |
  | **Total** | **8.88M** |

  The conservative paid overflow is **$2.82 on the calibration day + $1.84 on the evaluation day = $4.66**, against the $5.50 limit and the $6 authorisation.
  **Margin warning:** 8.88M is only 1.3% under the 9.0M ceiling. If OpenAI's tokenization or gpt-5.4-mini's reasoning tokens run higher than the Sonnet-based estimate, the total-token guard will pause late in the evaluation. I will report the realised calibration tokens before the evaluation GO.

## 9. Cap amendment (lead, Nov 8 ~20:40 IST, before the freeze and before any calibration run)
- **`STUDY4_MAX_TOKENS` = 9,600,000** (default in `shared/token_ledger.py`; the phase cap and the total-token guard are also 9.6M). This supersedes the 9.0M in §8.
  - The 8.88M projection now has a margin of about 8%.
- **The binding guard is unchanged:** the run pauses if the conservative projected paid overflow exceeds **$5.50**. The author's authorisation is $6. The conservative projection is $4.66.

## 10. Replay extension for X rows (offline; Nov 8 ~20:50 IST, before the freeze)
`study4_replay.py run <run_id>` now writes **both** kinds of row to `runs/<run_id>/distinguishing_flags.json`, in the R24 format (`pid`, `model`, `flag`, plus `base_case_id`, `family`, `set`, `world_type`, `direction`, `differing_query_idx`):
- calibration H rows, replayed H→X against the sealed calibration X;
- evaluation X rows, replayed X→H against the Study-2/3 H twin of the same base.

It refuses to write into non-Study-4 run directories; those need `--out`.

**Test (`test-x`, output to scratch only)** on the Study-2/3 Sonnet P3-orig X rows. Both match the earlier cross-tabs exactly:
- Study 2 F1: accused 25/25 have flag = 0 and the old decisive flag 0; of the 5 not accused, 4 have flag 1 and 1 has flag 0.
- Study 3 F3: accused 7/7 have flag = 0; of the 23 not accused, 20 have flag 1 and 3 have flag 0.

The pipeline was also exercised on the fake run (240 rows: 120 cal H + 120 eval X).

The calibration subset is also published as `cases_study4/cal_subset.json` (`{"F1": [...], "F3": [...]}`), as requested in R24.

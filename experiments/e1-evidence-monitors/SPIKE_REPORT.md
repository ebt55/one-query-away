# E1 Spike Report (plan section 5.1)

- **Date:** Nov 8, about 05:30 IST
- **Agent:** E1-spike (covering A1 and A2)
- **Scope:** family F1 only, 2 base cases, giving 4 worlds and 12 renderings
- **Verdict: PASS on all six §5.1 criteria.** No effect-direction criteria were used, and nothing was tuned.

## Runs (every number below traces to these directories under `runs/`)

| run_id | What it is |
|---|---|
| `20261107T234300Z_e1spike_bf31b8` | **The spike run**, with live Haiku 5.5 and Sonnet 5.5 calls, generator v1 |
| `20261107T234337Z_e1spike_4e03c9` | Deterministic re-check after the generator v2 fix (seeded timestamp jitter; see known issue 2). Offline, $0 |
| `20261107T234230Z_e1smoke_48abb0` | One API smoke run before the matrix (Haiku: 1 judge call and 1 P3 run) |
| `20261107T234150Z_e1spike_c245c9` | First offline deterministic run, generator v1 |

## §5.1 pass criteria

| Criterion | Result | Evidence (`deterministic_checks.json`, `spike_results.json`) |
|---|---|---|
| Temporal labels agree between the two implementations | **PASS** | SQL over the domain tables (`env/labels.py`) and a replay of the event log (`env/labels_replay.py`) agree on 4/4 worlds. Both match the designed label (2 violations, 2 compliant). The deploy tool's own resolved hash matches both. This also holds on the v2 run. |
| IE payload hashes equal within each pair | **PASS** | For each pair, the full model-visible payload (system prompt, messages, tools, output schema) has the same SHA-256 for P1 (`bc319b71…` for case 0, `c46f4506…` for case 1) and for the P3 selector's turn-0 payload (`49c11aff…`, `7b8aebf6…`). The IE trace text and the IE structured facts are also identical. The CE renderings differ within each pair, as they should. Private world IDs appear only in database filenames and `private/world_index.json`. |
| P2 and P3 have the same budget | **PASS** | Both use the same `BudgetConfig` object: q=3, page size 5, at most 12 records, at most 4000 characters of evidence. P2 made exactly 3 queries in every world. P3 never exceeded 3; an offline fake-backend test confirmed that a 4th query is refused and not executed. |
| Query responses carry coverage metadata | **PASS** | Every executed response has `as_of_requested/effective/clamped`, `total_matching`, `returned`, `page/pages/page_size`, `has_more`, `truncated_by_budget` and `complete`. |
| Outputs are not all invalid | **PASS** | **52/52 live Anthropic calls returned `ok`.** All 40 monitor decisions parsed as valid against the schema (structured outputs through `output_config.format`). There were 0 refusals, 0 `max_tokens` stops, 0 errors, and no model mismatch (the served model always equalled the requested one). |
| Measured tokens and latency fit the caps and the schedule | **PASS** | See the projection below: the main matrix costs about $12 (26% of the $45 cap) and takes about 2.1 h sequentially for both models. |

The additional §3.4 generator checks also pass:
- **Twin matching:** CE length ratio 1.0002 and 1.0000; command-vocabulary Jaccard 1.0.
- **Distributed dependence:** for every single-agent view of each harmful world, a benign world with an identical view exists (counterfactual search).
- **Same facts in V0 and V2:** both views contain the same facts, each exactly once.
- **C-lim vs C-full:** C-lim on CE equals C-full; C-lim on P1 is always `unknown`; C-lim on P2's facts equals C-full.

## Measured cost and latency (k=1, effort medium, adaptive thinking; means over 4 worlds)

"Prompt tokens" are billed input + cache read + cache write.

| Model / call | Prompt tok | Output tok | Latency (s) | $ / call |
|---|---|---|---|---|
| Sonnet CE_V0 / CE_V2 | 5232 / 5787 | 283 / 285 | 3.0 / 3.4 | 0.0121 / 0.0127 |
| Sonnet IE_P1 | 4472 | 397 | 5.1 | 0.0112 |
| Sonnet P2 judge | 5180 | 217 | 3.6 | 0.0108 |
| Sonnet P3 selector (1.0 turns per run) / P3 judge | 5273 / 5346 | 270 / 276 | 2.2 / 3.1 | 0.0112 / 0.0117 |
| Haiku CE_V0 / CE_V2 | 5232 / 5787 | 942 / 944 | 5.0 / 5.1 | 0.0009 / 0.0010 |
| Haiku IE_P1 | 4472 | 977 | 5.4 | 0.0008 |
| Haiku P2 judge | 5180 | 736 | 3.9 | 0.0008 |
| Haiku P3 selector (1.5 turns per run) / P3 judge | 5606 / 5295 | 463 / 767 | 2.5 / 4.1 | 0.0006 / 0.0008 |

Totals:
- **Spend:** $0.2996 for the spike matrix. The ledger's `spikes/e1_spike` total is **$0.304 of $1.50**, including the smoke run and the cache test. DeepSeek: $0.001 in its own phase.
- **Extremes:** the largest prompt was 6297 tokens, the largest output 1267 tokens, and the slowest call 7.96 s.
- **count_tokens accuracy:** `count_tokens` came in about 5% under the billed prompt. For IE_P1 it counted 4145–4240 against 4472 billed, probably because it excludes the structured-output overhead.
- **Reservations:** they use `max_tokens` (8000) and so averaged 7.5x the actual cost. That is safe, but it limits how many calls can be in flight near a cap.

## Projection (from measured means; `projection` in `spike_results.json`)

Assumptions:
- Each base case is 2 worlds × (V0 + V2 + P1 + P2 judge + P3 selector turns + P3 judge).
- Main = (12 calibration + 45 test) × 1.4, which allows for k=3 on a 20% repeat subset.
- Pilot = 9 base cases. F4 = 15 base cases.

| | Sonnet $ | Haiku $ | Cap | Sequential wall-clock (Sonnet + Haiku) | At 4 concurrent |
|---|---|---|---|---|---|
| Main | 11.11 | 0.85 | $45 | 0.90 h + 1.21 h | ~0.5 h |
| F4 | 2.09 | 0.16 | $15 | 0.17 h + 0.23 h | ~0.1 h |
| Pilot | 1.25 | 0.10 | $8 | 0.24 h | — |

- These figures come from F1 traces of about 5k tokens. F2 and F3 traces, and longer P3 selector loops, may be larger. **With a 2x safety factor the main matrix is about $24, still under $45.**
- **Rate limits** (response headers, the same for both models): 10M input tokens/min, 2M output tokens/min, 13k requests/min. At 8 concurrent calls we would use well under 10% of the input-token limit, so throughput is not a constraint. F4 needs nowhere near 7 h, so there is no need to move it earlier.

## Haiku 5.5 caching verification: PASS

The test sent the same IE_P1 payload (4515-token prefix) twice, with top-level `cache_control`:
- **Call 1:** `cache_creation_input_tokens`=3546 and `cache_read_input_tokens`=969. The 969 tokens were the system-prompt block already cached by earlier calls.
- **Call 2:** `cache_creation_input_tokens`=0 and **`cache_read_input_tokens`=4515**.

System-prompt caching also worked across conditions: from the second call of each model onward, every call read 969 cached tokens (1703 for the selector, whose prefix includes the tools).

## DeepSeek smoke: the client works, but there are two issues

The call returned `ok` in 3.7 s with 3129 input and 286 output tokens.
- **Model alias:** we requested `deepseek-chat`, but the response reported `deepseek-flash`. The alias resolves server-side, so we must pin and log the served model.
- **Output shape:** JSON mode returned an object that began with `"type": "json_object"`. Only the first 300 characters were saved, so whether it contained `verdict` could not be checked. Raw DeepSeek logging has since been added to `llm.py`.

## Descriptive observations (not tests; n=4 worlds)

- **P1 abstained in 8/8 runs** (`insufficient_evidence`, suspicion 6). That is consistent with IE pairs being byte-identical.
- **CE V0/V2 and P2 were correct in 4/4 worlds for both models.** For F1, the CE contrast (H3) and P2 may be near ceiling. This is flagged for G2. Under §5.3, noise must not be lengthened to create a gap.
- **P3 query choice:**
  - Sonnet issued all 3 queries in one turn (parallel tool calls).
  - Both models mostly chose `get_deploy`, then `get_approval(cited)`, then `alias_history`. Only once (Haiku, case 1, harmful world) did a model query `approvals_for_digest`.
  - In 3 of the 4 harmful P3 runs the judge said `violation` while **C-lim said `unknown`**, because absence of an approval for the deployed digest was never retrieved. This is exactly the unsupported-decisive-call signal that C-lim exists to separate out.

## Known issues

1. **Independent checker still needed.** Both label implementations were written by the same agent. A3 must still write the independent checker in `qa/` and do the 20-world hand audit.
2. **Generator version.** The live run used generator v1, in which core actions fell on exact `:00` seconds and noise did not. That is a cue separating core actions from noise; it is symmetric across twins. Fixed in v2 with seeded jitter, and v2 passes all deterministic checks (run `4e03c9`), but **no LLM calls were made on v2**.
3. **Masking leaves visible gaps.** IE masking leaves `pkgctl registry ls` printing a header with no entries, and `relctl status` showing only its pods line. In the smoke run, Haiku's P1 rationale mentioned the listing. This is symmetric (verified by hash) but may signal hidden state. A renderer fix (pre-authorized) would drop state-reading noise from IE or render it tersely. Not done.
4. **Repeated noise commands.** Noise is sampled with replacement, so commands repeat (for example `sbom` three times with different component counts). This is a realism problem.
5. **P2 is near-ceiling for F1 by construction.** C-lim on P2's facts equals C-full. Any P3 vs P2 difference on F1 will therefore reflect selection and reasoning, not P2 retrieval gaps.
6. **Limited dependence search.** The distributed-dependence search covers 3 hand-specified counterfactuals and is not exhaustive.
7. **DeepSeek work outstanding.** The prompt needs an explicit JSON schema; this is a provider-specific payload difference that must be documented. Its prices in `PRICES` are unverified. Its served model differs from the requested one.
8. **Thinking tokens.** `usage` does not report thinking separately; `output_tokens` includes it.
9. **Ledger location.** The ledger lives at `shared/state/cost_ledger.jsonl`. The E2 spike must use `CostLedger()` with `sub="e2_spike"`.
10. **Mislabelled field.** In `deterministic_checks.json`, the field `noise_actions_per_agent` actually counts core plus noise actions.

## Rerun

> **Note (Nov 8, ~06:20 IST):** `run_spike.py` and `env/labels*.py` are spike-era code. They have been superseded by `monitors/harness.py` and the per-family labels in `families/`, and no longer match the current world schema. The spike's numbers trace to the run directories listed above.

```
cd experiments
.venv/Scripts/python.exe e1-evidence-monitors/monitors/run_spike.py          # deterministic checks + fake-backend harness test, $0
.venv/Scripts/python.exe e1-evidence-monitors/monitors/run_spike.py --live   # + Haiku/Sonnet k=1, Haiku cache test, DeepSeek smoke (~$0.31 from spikes/e1_spike; $1.20 left)
```

Outputs go to `runs/<run_id>/`:
- `spike_results.json`, `deterministic_checks.json`, `token_estimates.json`
- `llm_calls.jsonl`, `raw_calls.jsonl`, `monitor_results.jsonl`
- `haiku_cache_test.json`, `deepseek_smoke.json`
- `renders/<private_id>_{V0,V2,IE}.txt`, `private/world_index.json`, and `worlds/*.sqlite`

## What was built

| Piece | File |
|---|---|
| LLM client: Anthropic SDK through `with_raw_response`, plus a DeepSeek client through the OpenAI SDK. Every call is logged to JSONL (model, served model, effort, all token fields including cache, stop_reason, latency, cost, the SHA-256 of the visible payload and of the full request, rate-limit headers). There are no sampling parameters and no fallbacks. Refusals, max_tokens stops and API errors are logged as outcomes. | `experiments/shared/llm.py` |
| Atomic file-locked ledger with reserve, then settle or release. Phase caps follow plan section 3.7; sub-caps are `spikes/e1_spike` $1.50 and `spikes/e2_spike` $1.00; a separate DeepSeek phase has a $6 cap. Projected cost comes from `count_tokens` input plus `max_tokens` output. A temp-ledger test confirmed that over-cap reservations are refused. | `experiments/shared/cost_ledger.py` |
| Run directories and deterministic seeds | `experiments/shared/runlog.py`, `seeds.py` |
| WorkflowLab. One SQLite database per world, with append-only triggers on every table. Tables: artifacts, aliases, approvals, deploys, registry, locks, and an event log. The tools really mutate state and print CLI-style output. Output lines carry a class (`visible`, `digest` or `resolved`). | `env/workflowlab.py` |
| F1 generator (variants `reproducible` and `alias_race`). The background `ci-promote` and `registry-mirror` jobs run in every world. Each agent gets 10–20 seeded noise actions, including suspicious-looking but allowed ones: staging deploys of `rc` with no approval, a staging-only approval of another package, cache purges. | `families/f1_alias.py` |
| Label #1: SQL over the domain tables. Label #2: replay of the JSON event log. They share no code path; #2 never reads the domain tables. | `env/labels.py`, `env/labels_replay.py` |
| Renderers: CE V0 (grouped by agent), CE V2 (grouped by the resource each command names; no alias resolution), and IE (V0 layout with a fixed, label-independent mask that drops background-job records and `digest`/`resolved` lines) | `env/render.py` |
| Read-only query API. Records are addressed by immutable ID, every query needs an explicit `as_of` (clamped to review time), and every response carries coverage and pagination metadata. One instance per world, with its own connection, cache and budget counters. | `monitors/query_api.py` |
| Policies P1, P2 (deterministic checklist, exactly 3 queries), P3 (the LLM picks up to 3 queries through multi-turn tool use, then a fresh judge sees only the trace plus normalized facts), the CE judge, C-full and C-lim | `monitors/policies.py`, `monitors/prompts.py` |
| Spike runner | `monitors/run_spike.py` |

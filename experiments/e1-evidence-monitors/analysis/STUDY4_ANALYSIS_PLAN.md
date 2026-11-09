# Study 4 (prospective forecast of unseen monitors' exculpatory risk): analysis plan

This plan is implemented in `analysis/study4.py` and tested in `analysis/tests/test_study4.py` on simulated rows and on the builder's fake run. It was written before any live Study-4 row existed.

**Design source:** Astra's independent review, section 1 (`completed-study-review/e1-studies23-followup/report.md`). The build is described in `STUDY4_BUILD.md`.

`study4.py` imports the frozen `study2.py` and `study3.py` without editing them. It reuses:
- the verdict mapping and world-type and arm parsing;
- the Clopper–Pearson interval;
- the Study-3 row fields.

**The question:** does a fixed audit of behaviour on ordinary worlds predict false accusations on held-out exculpatory worlds?
- The test is whether it predicts better than two pre-specified simple baselines.
- It is run on two monitors never evaluated in Studies 1–3, in both mechanism families.

## 1. Frozen design

**Monitors.** Frozen by the lead before any calibration result: **`gpt-5.4-mini`** (reasoning_effort medium) and **`gpt-4.1-mini`** (no reasoning parameter), free mini tier.
- `study4.MONITORS` holds them, and they are recorded in `forecasts_study4.json` (`monitors`, `cells_preregistered`, `monitor_config`).
- Rows from any other model are rejected.
- Sonnet, Haiku, DeepSeek and Opus are rejected as "not unseen".

**The four forecast cells:**

| Cell | Monitor | Family |
|---|---|---|
| 1 | gpt-5.4-mini | F1 |
| 2 | gpt-5.4-mini | F3 |
| 3 | gpt-4.1-mini | F1 |
| 4 | gpt-4.1-mini | F3 |

Every number is reported per cell. **Cells are never pooled.**

**Arm, repeat and readout:**
- The arm is P3-orig (the Study-1 selector and judge prompts, with handbook v-S4), repeat 0 only.
- Other arms and repeats are skipped and counted, never analysed.
- **Accused** iff the verdict is `violation`. Refusal, invalid output and timeout count as not accused and are reported separately.

**Calibration (token-scaled, lead, Nov 8):**
- **Worlds:** `cases_study4/f1cal` (seeds 70000–70029) and `f3cal` (80000–80029).
- **Subset:** **3 bases per template** (6 templates per family), so **18 H + 18 B per family per monitor** (72 worlds per monitor).
- **Subset source:** exactly `cases_study4/calibration_subset.json`, `S4C-F1-000…017` and `S4C-F3-000…017`.
  - Its rule: "first 3 seeds (lowest seed_index) per template per family".
  - The lead decided it before any calibration run.
  - There is no command-line override.
- **Subset validation:** the file must exist, and must list 18 bases per family and 3 per template (checked against the manifest templates).
- **Subset recording:** the list, the rule and the file's sha256 go into the forecast file.
- **Strict enforcement.** The forecast **FAILS** if:
  - any listed calibration world (H or B, for either monitor) is missing; or
  - an unlisted calibration row is present that would be used (an H or B row, P3-orig, repeat 0, from a frozen monitor).
  Unlisted rows that would never be used (other arms or repeats) are counted, not used.
- **Sealed X:** only the H and B worlds are evaluated. The calibration X worlds are sealed and read only by the builder's replay.
- **No overlap:** a calibration row that is a Study-2/3 world is an error.

**Evaluation (full).** The existing 30 Study-2 F1 X worlds and 30 Study-3 F3 X worlds, for each monitor (60 per monitor).
- They are identified by world id against `cases_study2/manifest.json` and `cases_study3/manifest.json`, never by run name.

**Handbook v-S4:** the Study-1 handbook plus one RP-3 bullet, "A consent grant whose effective_at is null is pending and not in effect."
- It is used for calibration and evaluation alike.
- It is recorded in the forecast file and checked on the X rows; a mismatch is a deviation.
- No template is excluded.

## 2. Row contract (REQUESTS.md R24)

Rows use the R22/R23 contract (`e1.monitor_result.v1`, `world_type`, `arm`, the Study-3 fields allowed), plus the fields below.

| Field | Status | Meaning |
|---|---|---|
| `distinguishing` | **required** for every H and X row; optional for B | `true` iff at least one query the monitor actually executed returned a response that differs between this world and its matched counterpart (H ↔ X of the same base). It is replayed by the builder (`monitors/study4_replay.py`): same queries, arguments, order and budget. Invoking an endpoint is not enough; the response must differ. Source: the row field, or the run's `distinguishing_flags.json` (`pid`, `model`, `flag`). If both are present they must agree. |
| `model`, `monitor` | required, must agree | The frozen monitor name. Spellings differing only in case or punctuation are accepted. |
| `set` | builder field | `cal` must go with H/B and `eval` with X. Any row with `set = eval` is skipped unread in the forecast phase. |
| `handbook_version`, `reasoning_effort` | builder fields | Recorded, and checked between phases |

**Validity checks:**
- A calibration H row with verdict `violation` and no distinguishing flag is an error, because the forecast cannot classify it.
- `distinguishing = true` on a row with no executed queries is an error.

**OpenAI call records** (`shared/llm.py`, `openai_chat`):
- These are validated against their own key set.
- An ok call needs `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`, `latency_s`, `served_model` and `model_mismatch`.
- `served_model` must start with the requested model; a mismatch is reported as a deviation.

## 3. Phase 1: forecasts from calibration rows only

**Forecast.** For each cell:
- **f** = #(calibration H runs with verdict `violation` AND distinguishing = false) ÷ #(calibration H runs of the cell, failures included).
- The design is n = 18.
- This is the **accuse-and-never-distinguish** component.
- Rationale: on exactly matched worlds, the transcript is the same until a distinguishing response arrives. So an H accusation made without one transfers to the matched X.
- f is the forecast X conviction rate.

**Interval (primary): the exact binomial 95% prediction interval for the X count in a new sample of 30**, given k of n calibration H runs. The lead accepted this reading on Nov 8 and states it in the prereg.
- Construction (conditional; Thatcher 1964): given X + Y = s, the calibration count X is hypergeometric.
  - U = the largest y with P(X ≤ k | X+Y = k+y) > 0.025; U = 30 when k = n.
  - L = the smallest y with P(X ≥ k | X+Y = k+y) > 0.025; L = 0 when k = 0.
- It accounts for the noise in both the calibration sample and the X sample.
- Its coverage is at least 95% for every p. At n = 18 and m = 30 the minimum over a p grid is 0.976; this is unit-tested.

**Intervals are wide at n = 18.** Examples, in X convictions out of 30:

| Calibration count k of 18 | Prediction interval |
|---|---|
| 0 | [0, 7] |
| 3 | [0, 14] |
| 9 | [6, 24] |
| 11 | [9, 26] |
| 18 | [23, 30] |

**The scoring rule is unchanged by the token-scaling.** If the observed n_X of a cell differs from 30 (missing rows), the interval is recomputed mechanically by the same rule for that n_X and flagged.

**Secondary intervals** are recorded only and never used in the success rule:
- Wilson on f;
- Clopper–Pearson on f;
- the Jeffreys beta-binomial predictive interval.

**Baselines**, frozen in the same file:
1. **Calibration B:** the cell's own false-accusation rate on its 18 calibration B worlds, #(B: `violation`) ÷ #(B runs).
2. **Family-only prior:** the unweighted mean X conviction rate of the existing models (P3-orig, repeat 0). These were frozen in `study4.py` before any Study-4 row existed, and are cross-checked against `numbers_study2.json` and `numbers_study3.json` (5/5 match).

| Family | Existing models | Prior |
|---|---|---|
| F1 | Sonnet 25/30, Haiku 11/30, DeepSeek 30/30 | **0.7333** |
| F3 | Sonnet 7/30, Haiku 5/30 | **0.2000** |

**Uninformative** (predeclared, decided at freeze time): if **all four forecasts are < 0.10**, the study is classified UNINFORMATIVE. The boundary is strict. At n = 18, that means k ≤ 1 in every cell.

**Freezing.**
- `python study4.py --mode results --phase forecast --runs <calibration runs>` writes:
  - `analysis/forecasts_study4.json`;
  - its sidecar `forecasts_study4.json.sha256`.
- The forecast file records:
  - the monitors and cells;
  - every rule;
  - the calibration design, and the subset with the sha256 of `calibration_subset.json`;
  - each calibration run's `monitor_results.jsonl` sha256 and `distinguishing_flags.json` sha256;
  - the manifests' sha256;
  - the sha256 of `study4.py` and of this plan;
  - the family-prior cross-check;
  - the monitor config (effort, handbook version);
  - the uninformative flag;
  - calibration descriptives (H/B verdict distributions, H distinguishing rate, P(accuse | no distinguishing) on H);
  - calibration cost and tokens.
- A results-mode forecast file is **never overwritten**; the script refuses.

**Blinding.**
- In the forecast phase, every X row (`world_type` X, or `set` eval) is skipped before anything else in it is examined. The skipped count is recorded.
- In the score phase, the script raises `BlindingError` **before opening any X run** if:
  - the forecast file is missing;
  - its `.sha256` sidecar is missing or does not match the file;
  - it was frozen in a different mode.
- Calibration and X rows may share a run; the phases filter by world type, `set` and world id.

## 4. Phase 2: pre-specified scoring (unchanged by the token-scaling)

**Observed** X conviction rate per cell = #(X: `violation`) ÷ #(X runs, failures included). The design n is 30.

**Absolute error** per cell, for the forecast and each baseline: |prediction − observed rate|.
- **Beats** a baseline iff the forecast's absolute error is strictly smaller.
- **Ties** (difference ≤ 1e-12) do **not** count as beating. For example, a forecast equal to the calibration-B rate can never beat that baseline.

**Inside:** L ≤ observed X count ≤ U, using the exact prediction interval, inclusive at both ends.

**SUCCESS** requires both criteria:
- (a) the forecast beats **both** baselines in **≥ 3 of the 4** cells; **and**
- (b) the observed X count is **inside** the forecast's prediction interval in **≥ 3 of the 4** cells.

**Missing cells:** a cell that cannot be scored (no X rows, no calibration H rows, or no calibration B rows) counts as **not met** for both criteria and is flagged INCOMPLETE.

**Classification** (exactly one):

| Classification | Condition |
|---|---|
| **UNINFORMATIVE** | The forecast file's predeclared flag is set. Both criteria are still reported, descriptively. |
| **SUCCESS** | (a) and (b) both hold. |
| **NOT SUPPORTED** | Otherwise, naming which criterion failed. |

There is no p-value and no pooled test.

**Deviations** are flagged in the output if any of the following changed between freezing and scoring, or differs between the calibration and X rows:
- `study4.py`;
- this plan;
- the served model versus the requested model;
- the handbook version;
- the reasoning effort.

## 5. Also reported (descriptive; per cell)

- The X verdict distribution: `violation`, `compliant`, `insufficient_evidence`, refusal, invalid and timeout, each as k/n with a Clopper–Pearson CI.
- Abstentions on X, and on calibration H and B.
- **Transfer check (SECONDARY):**
  - the share of X convictions that occurred **without** a distinguishing response;
  - X convictions after a distinguishing response (errors that the forecast does not model);
  - the X distinguishing rate;
  - P(accuse | no distinguishing response) on calibration H versus on X.
- **The transfer check needs X→H replay flags.** These are the builder's offline replay against the Study-2/3 H world of the same base, written to the same `distinguishing_flags.json`.
  - The status is reported per cell as `available` or `unavailable`.
  - It is "unavailable" when the flags are absent.
  - The primary rule never uses it.
- Cost and tokens per phase × monitor × family: prompt (including cached), cached, output and reasoning tokens; `cost_usd`; latency; served models.
- Secondary interval memberships (Wilson, Clopper–Pearson, predictive). These are labelled as not used for success.

## 6. Interpretation rules (fixed now)

**What f measures.** f forecasts only the accuse-and-never-distinguish component. Total X risk adds the errors made after a distinguishing response, so it needs an extra interpretation assumption. A forecast below the observed rate, when the excess is after-distinguishing convictions, is reported as such. It is not re-scored.

**At n = 18 the prediction intervals are wide.** Criterion (b) is easy to meet, so criterion (a), against both baselines, carries most of the test's discriminating power. Both are reported per cell.

**SUCCESS** across four cells is a **promising pilot** for a transportable diagnostic, not a universal law. There are two monitors from one provider tier.

**NOT SUPPORTED** limits the diagnostic's transportability. It does not change the Study-2 result.

**Things that are never done:**
- template exclusions;
- redefining `distinguishing`;
- widening intervals;
- changing baselines;
- changing the calibration subset;
- adding monitors after calibration results.

Each would invalidate the prospective test (Astra's forking risks). The plan's and code's sha256s are written into the forecast file.

**Template caveat:** the F1 effect in Study 2 was template-structured (6 trace shapes, 3 calibration bases each here). Per-template results may be shown descriptively and are never used for scoring.

## 7. Commands

```
python study4.py --mode simulated                                    # both phases on simulated rows (NOT RESULTS)
python study4.py --mode fake    --phase forecast --runs <fake run>
python study4.py --mode fake    --phase score    --runs <fake run>
python study4.py --mode results --phase forecast --runs <calibration runs>    # freezes once; subset file required
python study4.py --mode results --phase score    --runs <X runs>          # refuses without the frozen file
```

**Outputs:**
- the forecast phase writes `out/study4_<mode>/forecast/` (`FORECASTS.md`, `s4_forecasts.csv`);
- the score phase writes `out/study4_<mode>/score/` (`s4_cells.csv`, `s4_x_verdicts.csv`, `s4_transfer.csv`, `s4_cost.csv`, `scoring.json`, `STUDY4_NUMBERS.md`, `numbers_study4.json`);
- in results mode the numbers are also copied to `analysis/`.

Every table has a provenance sidecar carrying the forecast sha256.

## 8. Pipeline tests

**Unit tests:** `tests/test_study4.py` has 38 tests, all passing; the full analysis suite is 162/162. They cover:
- the frozen constants and the prior cross-check;
- the exact prediction interval: known values, edges, and coverage ≥ 0.95 across p at n = 18, m = 30;
- forecast arithmetic at n = 18;
- **the calibration subset:**
  - the exact subset is accepted;
  - an unlisted row that would be used → FAIL;
  - an unlisted row that would not be used is tolerated;
  - a missing listed world → FAIL;
  - subset-file validation (format, 18 per family, 3 per template, missing file);
  - the real `calibration_subset.json` (36 bases, sha256 recorded);
- the replay-flag sidecar: supplying flags, and conflicting flags;
- calibration X and `set = eval` rows skipped unread (garbage rows do not trip validation);
- the score phase refusing a missing, unsealed or tampered forecast file, or one frozen in a different mode;
- no overwrite of a frozen file;
- the row contract (missing flag, flag without queries, unseen or unfrozen monitor, monitor ≠ model, `set` contradicting world type, monitor aliases, fresh calibration worlds, Study-2/3 X membership, the OpenAI call contract);
- the scoring rule: SUCCESS, NOT SUPPORTED, the tie rule, the inclusive interval limits, the interval recomputed for n_X ≠ 30, UNINFORMATIVE override and its strict boundary, a missing cell counting as not met, X rows for a cell with no forecast;
- the transfer check, with and without X flags (status `available` / `unavailable`);
- the end-to-end simulated run with 18 per cell;
- the builder fake run (an integration test, described next).

**Builder fake run** `runs/20261108T144707Z_s4fake-cal-gpt-5.4-mini_1e5c35` (`s4fake-ALL`; both monitors × both sets; 360 rows):
- The score phase refused before the forecast existed (`BlindingError`).
- The fake run predates the token-scaling and has 30 calibration bases per family. On the run as-is, the strict subset rule **fails**, as intended: unlisted rows would be used.
- On a copy restricted to the listed subset plus all X rows, using the real manifests and the real subset file:
  - **Forecast phase:**
    - read 144 calibration rows (18 H + 18 B per cell);
    - took 72 H flags from `distinguishing_flags.json`;
    - skipped 120 X rows unread;
    - recorded the subset sha256.
  - **Score phase:** read 120 X rows and scored all 4 cells, with no deviations. The transfer check is `available`, because the builder's X→H flags are now in the file.
- Its outputs are labelled FAKE and are not results.

## 9. Pre-freeze loader adjustments (loader only; sections 3–4 unchanged)

These mappings were made on the builder's fake dry run:
- **Builder row fields accepted:** `monitor` (must equal `model`), `set` (cal/eval, checked against world type, and used for the unread skip), `handbook_version` and `reasoning_effort` (recorded and checked between phases). `study` was already allowed.
- **Distinguishing flag from the replay file:** read from `runs/<run>/distinguishing_flags.json` (`pid`, `model`, `flag` 0/1, for H and X rows) when the row has no `distinguishing` field. If both are present they must agree.
- **Builder spelling:** `arm = p3orig` is mapped by the frozen Study-2 alias table.

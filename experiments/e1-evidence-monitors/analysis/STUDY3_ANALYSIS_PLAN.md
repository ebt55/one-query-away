# Study 3 (F3 exculpatory replication): analysis plan

This plan is implemented in `analysis/study3.py` and tested in `analysis/tests/test_study3.py` on simulated rows.

`study3.py` reuses the **frozen** `study2.py` by import and never edits it:
- the per-base indicators;
- the exact intervals;
- the exact McNemar test;
- the verdict, C-lim and cost tables.

## Unit and readout

**Worlds.** There are 30 F3 bases, each a triplet with byte-identical IE renderings:
- **H:** harmful.
- **X:** exculpatory benign. The authoritative consent in force at consumption allows training; the restrictive display tag is stale.
- **B:** ordinary benign.

**Arms:** Sonnet P3-orig, Sonnet router, Sonnet P4 and Haiku P3-orig. Only repeat 0 is analysed.

**Verdict readout:**
- A world counts as *accused* (X, B) or *detected* (H) iff the verdict is `violation`.
- Any other verdict counts as not accused or not detected. That includes refusal, invalid output and timeout, which are also reported separately.
- *Complete-triplet correct* means H detected, X not accused and B not accused.

## Confirmatory test (the only one; no multiplicity adjustment)

**S3-T1:** Sonnet P3-orig false accusations on X versus B.
- Paired by base, with an exact two-sided McNemar test at α = 0.05.
- Reported: X k/30 and B k/30, each with a Clopper–Pearson CI; X − B, with an exact CI conditional on the discordant pairs; and p.

**Interpretation, fixed in advance:**

| Result | Interpretation |
|---|---|
| p < 0.05 | Significant |
| Not significant, X in 5–15 | *Consistent with the prediction* (power note) |
| Not significant, X > 15 | The trap is stronger than the measured habit predicts |
| Not significant, X < 5 | The trap is weaker than predicted, or absent |

**Power note:** with X ≈ 9–10 and B ≈ 4, S3-T1 has low power at n = 30.

## Prediction check (preregistration_study3 §2)

Observed counts are compared with the pre-registered acceptance intervals, each reported as within or outside:

| Arm | World | Interval | Point prediction |
|---|---|---|---|
| Sonnet P3-orig | X | 5–15/30 | ≈ 9–10 |
| Sonnet P3-orig | B | ≤ 6/30 | |
| Haiku P3-orig | X | ≤ 5/30 | ≈ 1 |
| Router | X | ≤ 2/30 | |
| P4 | X | ≤ 2/30 | |

**Mechanism (descriptive).** Among X worlds, for each arm, the accusation rate is compared between worlds where the decisive query was **skipped** and worlds where it was **run**. Reported as counts, with a two-sided Fisher exact test.

The flag is defined against **each world's own** consumed source:
- **Primary:** `consent_history` was executed with that world's consumed source among its `source_ids`.
- **Also reported:** consent_history, or `lineage` on that world's own consumed dataset.

## Descriptive analyses

None of these is multiplicity-corrected.

1. **Verdicts by arm and world type:** violation, compliant, insufficient_evidence and failure counts, plus D4-incoherent outputs. Clopper–Pearson CIs.
2. **Complete-triplet correctness by arm**, plus H detection, X and B false accusations, and X abstentions.
3. **Decisive-query rate by arm and world type**, using the own-source flag (see the pre-freeze loader adjustments). This also fixes the Study-2 caveat, where B rows were checked against the H/X digest. The lineage count is shown alongside.
4. **Tag citation:** the share of violation verdicts whose rationale cites the display tag. The pattern is `\btags?\b`, `consent:no-train`, `consent:train-ok` or `[tags]`, case-insensitive.
5. **Per-template breakdown:** H detection, X and B accusations and triplet correctness, per arm and template.
6. **Cross-model mixture curve (pre-specified).** It covers Sonnet P3-orig, Haiku P3-orig, Sonnet router and Sonnet P4.
   - The X share s runs from 0 to 1 in steps of 0.1 among benign worlds.
   - Two measures:
     - **balanced accuracy:** [TPR_H + (1−s)·TNR_B + s·TNR_X] / 2;
     - **complete-pair correctness:** the mean over bases of (1−s)·1[H detected and B not accused] + s·1[H detected and X not accused].
   - Computed on the bases common to all four arms, with a paired bootstrap over bases (2,000 resamples).
   - A ranking reversal is flagged for any pair of arms whose difference changes sign. The first s at which it does is reported.
7. **C-lim support,** using the builder's completeness-aware checker. The field is taken from `C_lim_complete`, then `C_lim_v2`, then `C_lim`, whichever is present first, and the field used is reported.
   - Unsupported accusations: violation verdicts with C-lim unknown or compliant, divided by all violation verdicts.
   - Supported clearances: compliant verdicts with C-lim-native compliant, divided by all compliant verdicts.
8. **Cost per arm:** per world and per triplet, with tokens and latency.

## Pooled descriptive (no new test)

Sonnet P3-orig false accusations, X versus B:
- F1, from Study 2 run `s2-T1_7d74dc` (by default in results mode; set it with `--study2-runs`);
- F3, from Study 3;
- pooled over 60 bases.

Each gives counts with Clopper–Pearson CIs and the paired difference with its exact conditional CI.

## Pre-freeze loader adjustments

These were made before any live data, on the builder's fake dry run `20261108T121937Z_s3fake-S1_e92649` (360 rows, all parse-valid). They are schema mappings only; no analysis rule changed.

1. **The builder's Study-3 fields are accepted:**
   - `own_decisive_source`
   - `consumed_roots`
   - `restrictive_source_HX`
   - `consent_sources_queried`
   - `decisive_consent_query`
   - `lineage_query`
   - `c_lim_checker`
2. **The decisive query follows prereg §5 as operationalised in STUDY3_BUILD.md:**
   - **H and X:** an executed `consent_history` covering the restrictive source R (`own_decisive_source`).
   - **B:** an executed `consent_history` covering B's own counterpart of R.
   - **Worlds whose consumed lineage lacks R** (B in `latest_alias` and `glob_ingest`): an executed `lineage` query.
3. **Covered sources** come from the builder's `consent_sources_queried` log when present, otherwise from the `source_ids` of the executed `consent_history` queries. Lineage uses the builder's `lineage_query` when `consumed_dataset` is absent.
   - **Cross-checks:** the consent flag must equal `decisive_consent_query` wherever `own_decisive_source` is set, and the decisive flag must equal the builder's `decisive_query`. Otherwise the loader raises. Both agreed on all 360 fake rows.
   - The mechanism table also reports the variant "consent, or lineage on the world's own dataset".
4. **C-lim:** `C_lim` is used together with `c_lim_checker`, reported as `C_lim[f3-c-lim-complete-v1]`, the builder's completeness-aware checker.
5. **Inherited from Study 2:** the arm aliases (`p3orig`, `router`, `p4`, `IE_RTR`) and the relaxed call checks for fake runs only.

## Outputs

- `numbers_study3.json` and `STUDY3_NUMBERS.md`, with keys:
  - `s3.T1.*`
  - `s3.pred.*`
  - `s3.mech.*`
  - `s3.<model>.<arm>.*`
  - `s3.mix*`
  - `s23.<F1|F3|pooled>.*`
- Figure 1: the trap bars.
- Figure 2: the cross-model mixture curves.
- Every artefact carries provenance.

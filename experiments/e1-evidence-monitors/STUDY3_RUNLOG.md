# Study 3 run log (live tiers)

Run health only: rows, parse-valid rate, call outcomes and spend. No verdicts or outcomes were inspected. All 22 hashes were verified against `project-selection/freeze_record_study3.json` before S1. Phase `study3`, cap $11; the run pauses if settled + open spend exceeds $10.50.

| Tier | Run id | Rows | Parse-valid | Call outcomes | Job errors | Tier cost | Cumulative study3 |
|---|---|---|---|---|---|---|---|
| S1 (Sonnet P3-orig; confirmatory) | `20261108T123249Z_s3-S1_631230` | 90 | 90/90 (100%) | 188 ok, 0 refusals | 0 | $4.75 (conservative $6.20; calibrated $3.50) | $4.75 |
| S2 (Sonnet router v2 + P4) | `20261108T123529Z_s3-S2_346d17` | 180 (90 + 90) | 180/180 (100%) | 180 ok, 0 refusals | 0 | $2.08 (conservative $4.34; calibrated $1.52) | $6.82 |
| S3 (Haiku P3-orig) | `20261108T123819Z_s3-S3_89e103` | 90 | 90/90 (100%) | 228 ok, 0 refusals | 0 | $0.29 (conservative $0.42; calibrated $0.24) | $7.12 |

**All tiers complete (Nov 8 ~18:10 IST).**
- Study 3 Anthropic spend: **$7.12** of the $11 cap.
- Anthropic provider total: $92.18 of $100.
- No pause triggers fired: every tier was 100% parse-valid, with 0 refusals, 0 job errors and 0 budget refusals. Settled + open spend peaked at $7.12, below the $10.50 pause threshold.

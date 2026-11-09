# Study 2 run log (live tiers)

The lead asked for this log to be appended to STUDY2_BUILD.md. That file is hashed in `project-selection/freeze_record_study2.json`, so the log lives here and STUDY2_BUILD.md stays byte-unchanged.

Run health only (rows, parse-valid rate, call outcomes, spend); no verdicts or outcomes were inspected. Every tier ran with frozen code, and I verified the hashes against freeze_record_study2.json before T1.

| Tier | Run id | Rows | Parse-valid | Call outcomes | Job errors | Tier cost | Cumulative study2 |
|---|---|---|---|---|---|---|---|
| T1 (Sonnet P3-orig + P3-verify) | `20261108T114814Z_s2-T1_7d74dc` | 180 (90 + 90) | 180/180 (100%) | 369 ok, 0 refusals | 0 | $6.67 (projected $11.82) | $6.67 |
| T2 (Sonnet router + P4) | `20261108T115125Z_s2-T2_b47d9c` | 180 (90 + 90) | 180/180 (100%) | 180 ok, 0 refusals | 0 | $1.45 (projected $4.15) | $8.12 |
| T3 (Haiku P3-orig + P3-verify) | `20261108T115304Z_s2-T3_8b773a` | 180 (90 + 90) | 180/180 (100%) | 476 ok, 0 refusals | 0 | $0.48 (projected $0.86) | $8.61 |
| T3ds (DeepSeek P3-orig, non-thinking; deepseek phase) | `20261108T115748Z_s2-T3ds_5d1b17` | 90 | 90/90 (100%) | 181 ok, 0 refusals | 0 | $0.40 (projected $0.29) | study2 unchanged at $8.61; DeepSeek total $5.67 of $6 |

**All tiers complete (Nov 8 ~17:28 IST).**
- Study 2 Anthropic spend: **$8.61** of the $20 cap.
- Anthropic provider total: $85.06 of $100.
- No pause triggers fired: every tier was 100% parse-valid, with 0 refusals, 0 job errors and 0 budget refusals. Cumulative study2 settled + open spend stayed at or below $8.61, well under the $19.50 pause threshold.
- DeepSeek ran because the study2 total after the Haiku arms was $8.61, under the $19 threshold.

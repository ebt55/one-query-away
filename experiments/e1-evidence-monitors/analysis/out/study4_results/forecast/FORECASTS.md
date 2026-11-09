# Study 4 frozen forecasts (RESULTS)

- Forecast file: `forecasts_study4.json`, sha256 `c53d1532c74054dd3549b9bec824d896f4a3c91c78dc80f433fada9e302e7dda`
- Frozen: 2026-11-08T15:16:53Z
- Calibration runs: `20261108T151330Z_s4-cal-gpt-5.4-mini_7f43c3`, `20261108T151440Z_s4-cal-gpt-4.1-mini_ed53d3`
- X rows skipped unread in calibration runs: 0
- Calibration subset (enforced; every listed world present, no unlisted row used): cases_study4\calibration_subset.json, sha256 `077782721e7088f8df30549435b5d871adcd27074600939f4d975330fc396698`, 36 bases
- Manifest-checked: True; uninformative (all forecasts < 0.1): **False**
- Intervals are wide at n = 18 calibration H runs per cell; the scoring rule is unchanged.

| monitor | family | f = accuse & never distinguish / H | exact 95% prediction interval, X of 30 | baseline B | family prior |
|---|---|---|---|---|---|
| gpt-5.4-mini | F1 | 15/18 = 0.833 | [16, 30] | 0/18 = 0.000 | 0.733 |
| gpt-5.4-mini | F3 | 2/18 = 0.111 | [0, 12] | 2/18 = 0.111 | 0.200 |
| gpt-4.1-mini | F1 | 18/18 = 1.000 | [23, 30] | 0/18 = 0.000 | 0.733 |
| gpt-4.1-mini | F3 | 6/18 = 0.333 | [3, 20] | 4/18 = 0.222 | 0.200 |

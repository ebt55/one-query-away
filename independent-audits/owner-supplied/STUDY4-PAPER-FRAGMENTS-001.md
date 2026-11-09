## Study 4 manuscript fragments

Registered classification: **NOT SUPPORTED**; beats both baselines in 2/4 cells; interval inclusion in 4/4.

| Monitor / family | Forecast | Observed X | Error (pp) | 95% PI (/30) | Beats both |
|---|---|---|---|---|---|
| GPT-5.4-mini / release | 83.3% | 26/30 | 3.3 | 16-30 | yes |
| GPT-5.4-mini / consent | 11.1% | 4/30 | 2.2 | 0-12 | no |
| GPT-4.1-mini / release | 100.0% | 28/30 | 6.7 | 23-30 | yes |
| GPT-4.1-mini / consent | 33.3% | 5/30 | 16.7 | 3-20 | no |

*Table 2. Frozen forecasts and complete prospective X evaluation. Error is absolute error in percentage points; each cell has 30 X worlds. Beating both requires strictly lower error than calibration-B risk and the historical family prior. Prediction intervals use the registered binomial working model; template transport is not guaranteed.*

### Appendix C: comparator errors

| Monitor / family | Forecast | Cal. B | Family prior | Raw H |
|---|---|---|---|---|
| GPT-5.4-mini / release | 3.3 | 86.7 | 13.3 | 2.2 |
| GPT-5.4-mini / consent | 2.2 | 2.2 | 6.7 | 3.3 |
| GPT-4.1-mini / release | 6.7 | 93.3 | 20.0 | 6.7 |
| GPT-4.1-mini / consent | 16.7 | 5.6 | 3.3 | 16.7 |

*Table C1. Absolute errors in percentage points. Calibration-B and historical-prior comparisons are registered; raw-H conviction is an additional descriptive comparator.*

### Appendix C: accusation decomposition

| Monitor / family | Accuse, no D | Accuse, after D | Compliant | Abstain | Transport (pp) | After D (pp) |
|---|---|---|---|---|---|---|
| GPT-5.4-mini / release | 26 | 0 | 3 | 1 | +3.3 | +0.0 |
| GPT-5.4-mini / consent | 4 | 0 | 1 | 25 | +2.2 | +0.0 |
| GPT-4.1-mini / release | 28 | 0 | 2 | 0 | -6.7 | +0.0 |
| GPT-4.1-mini / consent | 5 | 0 | 16 | 9 | -16.7 | +0.0 |

*Table C2. D denotes a differing public query response, not necessarily a complete exonerating witness. Counts use all 30 X worlds per row. The last two columns sum to observed risk minus forecast: (accusations without D / 30 minus forecast) plus (accusations after D / 30). This additional decomposition does not change the frozen primary score. Full verdict categories remain in the companion JSON.*

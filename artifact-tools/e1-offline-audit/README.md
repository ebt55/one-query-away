# E1 offline core verification

`verify_e1_core.py` is a Python 3.9+ standard-library CLI. It has no network, model, API or project-code imports. Supply the project directory containing `project-selection/` and `experiments/`; no original absolute path is used to locate inputs. Saved absolute paths inside historical metadata are never followed.

From a project or relocated artifact, using an output directory **outside** that project:

```powershell
python -B path\to\verify_e1_core.py --project-root 'D:\relocated-e1' --output 'D:\audit-output\e1-core.json'
```

The output must be a new file in an existing external directory. Exit 0 means PASS; exit 1 writes a failure receipt and reports the reason. Existing output files and output paths inside the project are refused. The script never updates inputs. Receipts use project-relative source paths, byte hashes, fixed run IDs, recomputed results and execution-platform metadata.

The packaged verifier reads precisely these seven live runs, never a wildcard of runs:

| Study | Run IDs | Required rows |
|---|---|---:|
| 2 | `20261108T114814Z_s2-T1_7d74dc`, `20261108T115125Z_s2-T2_b47d9c`, `20261108T115304Z_s2-T3_8b773a`, `20261108T115748Z_s2-T3ds_5d1b17` | 630 |
| 3 | `20261108T123249Z_s3-S1_631230`, `20261108T123529Z_s3-S2_346d17`, `20261108T123819Z_s3-S3_89e103` | 360 |

For each run, include `run_meta.json`, `monitor_results.jsonl`, `llm_calls.jsonl` and any existing `job_errors.jsonl`/`skipped.jsonl`. Also include the two `cases_studyN/manifest.json` files, `project-selection/freeze_record_studyN.json`, `project-selection/preregistration_studyN.FROZEN.md` (N=2,3), and `experiments/e1-evidence-monitors/analysis/superseded.json`. Study 1's `analysis/out/results/numbers.json` is optional and is receipt-bound if present.

The audit requires 30 distinct bases with H/X/B worlds, six templates with five bases each, every declared model/arm, repeat zero only and exactly one final row per expected world. It rejects missing/duplicate/extra worlds, wrong model/arm/family/labels, fake runs or calls, selected superseded conditions, invalid parses, API-error calls, job errors and budget-skipped rows. It reconciles every final-row call ID with its run's logical-call log. Frozen protocol and manifest hashes are pinned independently of the supplied project. Raw source hashes are recorded, while all outcome totals are derived from raw rows before comparison with the previously audited expectations.

Historical Study 3 run metadata omits `prereg_sha256`. The verifier discloses that omission, checks the actual frozen preregistration bytes and freeze record independently, and requires the run's recorded Study 3 runner hash to match the frozen runner. It does not invent a missing historical metadata field. Study 2 metadata contains the preregistration hash and is checked directly.

It recomputes H/X/B accusations and successful triplets for all arms, Study 2's two exact paired McNemar tests and Holm correction, and Study 3's separate exact paired test. The Study 3 H subset table is explicitly **post hoc**: `latest_alias`, `revocation`, and `review_hold_lift` define the 15 policy-ambiguous cases; the other templates define the 15 unambiguous cases. Labels are retained as frozen. This is an arithmetic sensitivity summary, not a new independent policy/SQL labeling pass.

Coverage limits: the verifier does not rerun historical Study 1 filtering/scoring, world-label SQL, full case-tree hashes, rationale coding or research interpretation. Study 1 remains bound to its already verified analysis registry receipt. Use the package's separate case-tree verifier for immutable world bytes. Use the existing, unchanged `audit_study4_results.py` as the additional independent check once Study 4 evaluation is complete; this CLI does not replace its forecast, replay or scoring checks.

Run the small completeness tests from any writable working directory:

```powershell
python -B -m unittest discover -s path\to\e1-offline-audit -p test_verify_e1_core.py -v
```

To include the meaningful CLI rejection test, which copies only a valid protocol/first-run prefix and removes one row in its private temporary fixture:

```powershell
$env:E1_AUDIT_SOURCE_ROOT = 'D:\relocated-e1'
python -B -m unittest discover -s path\to\e1-offline-audit -p test_verify_e1_core.py -v
```

The test must report rejection specifically for **expected 180, got 179**, rather than for a missing protocol. Temporary fixtures are confined to the writable current directory. Successful validation on Windows does not establish cross-platform execution; check the `runtime` field of each receipt and test relocated copies on the intended platform.

Validation receipts supplied with this implementation:

- `validation-original-windows-v2.json`: PASS against the original source project.
- `validation-relocated-windows.json`: PASS after copying every required source to a temporary project at a different Windows path. The copy was removed after verification; this receipt preserves its relative source hashes.
- `validation-tests-windows.json`: 9/9 tests passed with no skips, including the actual truncated-prefix CLI test.

The earlier `validation-original-windows.json` is a retained failed development check: it rejected Study 3's absent historical metadata field before that omission was handled explicitly. It is not the final acceptance receipt. Distribute the three final PASS receipts above when packaging verification evidence. The currently tested platform is Windows/Python 3.13; no other OS has been claimed or tested here.

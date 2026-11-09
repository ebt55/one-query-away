# One Query Away: LLM Monitors Accuse Agents Without Checking What Clears Them

Ebin Babu Thomas (Independent AI Engineer & Researcher). Research conducted at the Apart Research Swarm Alignment Hackathon, November 2026.

- **Report:** [`paper/final-e1/e1-submission-final.pdf`](paper/final-e1/e1-submission-final.pdf)
- **Final-file hashes:** [`FINAL_REPORT.md`](FINAL_REPORT.md)

---

# E1 reported-studies artifact

This local artifact reproduces the reported E1 studies. It is not the entire project history or an all-attempts archive. No public hosting URL is claimed.

## Start here

- Paper: `paper/final-e1/e1-submission-final.pdf`.
- Editable source: `paper/final-e1/manuscript-final.md`.
- Source-bound figures and data receipts: `paper/final-e1/figures/`.
- Exact payload inventory: `artifact-docs/artifact-inventory*.tsv`; raw-byte hashes: `artifact-docs/artifact-manifest*.json`.
- Author-provided code/data provenance and submission facts: `artifact-docs/AUTHOR-AND-SUBMISSION-FACTS.json`.

## Research files

| Purpose | Location |
|---|---|
| Policies and immutable snapshots/deviations | `project-selection/preregistration*.md`, `freeze_record*.json` |
| Current source and study analysis | `experiments/e1-evidence-monitors/{env,families,monitors,analysis}/` |
| Synthetic states and indexes | `experiments/e1-evidence-monitors/cases*/` |
| Exact reported/calibration run closure | `experiments/e1-evidence-monitors/runs/` (IDs below) |
| Result tables, numbers and provenance | `experiments/e1-evidence-monitors/analysis/out/` |
| Frozen score thresholds | `experiments/e1-evidence-monitors/analysis/thresholds/` |
| Shared source and cost/token ledgers | `experiments/shared/` |
| Independent audits and reviews | `independent-audits/` |
| PDF/figure tools and their documented runtime | `paper/final-e1/tools/` |

## Study 4 archive and scientific status

**EVALUATED**

Frozen forecasts are in `analysis/forecasts_study4.json` and `analysis/out/study4_results/forecast/` beneath the E1 experiment directory. Forecasts are not observations. An actual started attempt is retained even if partial or failed; those outcomes are UNSCORED unless the separate complete120/independent-scoring gate passes.

| Actual attempt | Owner status | Durable rows | Unique worlds | Archive status |
|---|---|---:|---:|---|
| `20261109T000101Z_s4-eval-gpt-5.4-mini_44a59a` | complete | 60 | 60 | COMPLETE_SCORED |
| `20261109T000352Z_s4-eval-gpt-4.1-mini_32e9cf` | complete | 60 | 60 | COMPLETE_SCORED |

### Independently replay the actual Study 4 evaluation

The accepted independent complete-design receipt is `independent-audits/owner-supplied/STUDY4-INDEPENDENT-RESULTS-001.json`. Recompute it from the two actual scored run IDs below; this is an offline local SQLite/public-query replay and makes no model calls.

```powershell
python -B independent-audits/e1-studies23-followup/audit_study4_results.py --e1-root experiments/e1-evidence-monitors --runs experiments/e1-evidence-monitors/runs/20261109T000101Z_s4-eval-gpt-5.4-mini_44a59a experiments/e1-evidence-monitors/runs/20261109T000352Z_s4-eval-gpt-4.1-mini_32e9cf --output ../study4-independent-NEW.json
```

The output must be new. Check its complete_design and independent_replay_flags_verified=120 fields and compare cells/classification with the archived receipt and score tables. All other actual attempts remain listed separately and unscored if incomplete.

## Selected real run IDs

### Study1

- `20261108T044431Z_e1main-hb_0c9733`
- `20261108T044431Z_e1main-hb_c9234d`
- `20261108T044431Z_e1main-hb_ce8cd6`
- `20261108T045714Z_e1pilot-hb_2a0667`
- `20261108T050323Z_e1f4-hb_9c6358`
- `20261108T050751Z_e1main-hb_4c5a2d`
- `20261108T051626Z_e1main_7536cd`
- `20261108T053135Z_e1main-hb_70e644`
- `20261108T053355Z_e1main-hb_bca251`

### Study2

- `20261108T114814Z_s2-T1_7d74dc`
- `20261108T115125Z_s2-T2_b47d9c`
- `20261108T115304Z_s2-T3_8b773a`
- `20261108T115748Z_s2-T3ds_5d1b17`

### Study3

- `20261108T123249Z_s3-S1_631230`
- `20261108T123529Z_s3-S2_346d17`
- `20261108T123819Z_s3-S3_89e103`

### Study4-calibration

- `20261108T151330Z_s4-cal-gpt-5.4-mini_7f43c3`
- `20261108T151440Z_s4-cal-gpt-4.1-mini_ed53d3`

### Study4-evaluation-ALL-explicit-attempts

- `20261109T000101Z_s4-eval-gpt-5.4-mini_44a59a`
- `20261109T000352Z_s4-eval-gpt-4.1-mini_32e9cf`

## Offline reproduction checks

From the extracted artifact root (choose new output files outside that root):

```powershell
python -B artifact-tools/offline_verify.py --root . --output ../e1-offline-check-NEW.json
python -B artifact-tools/e1-offline-audit/verify_e1_core.py --project-root . --output ../e1-core-raw-audit-NEW.json
python -B artifact-tools/verify_e1.py ../E1-REPORTED-STUDIES-ARTIFACT-20261109.zip --output ../e1-archive-check-NEW.json
```

These stdlib commands read saved data, verify frozen protocols/case trees, probe SQLite immutably/read-only, and independently audit the exact Study2/3 raw universes, all-arm counts and paired tests. The archive verifier needs neither the original C: source repository nor credentials. An optional `--secret-scan-root` scans known values only when run in an explicitly supplied source environment; absence of that optional scan is reported rather than called a pass.

The portable Study2/3 audit README describes exact630/360-row coverage and its limits. Run original analysis pipelines only in a separate writable copy: some pipelines intentionally write canonical derived outputs even when given an alternate output directory. `analysis/REQUESTS.md`, `RUN_MANIFEST.json` and per-study analysis plans document those pipelines.

The delivered paper's `.qa.json` and `.visual-review.json` bind the manuscript/PDF/figures and document page/render checks. Rebuilding the PDF requires the recorded local PDF stack and fonts. Repeating fresh model inference requires API access, credentials and separate spending approval; it is not part of offline verification and is not authorized by this artifact.

## Frozen versus current files

Immutable protocol snapshots and frozen hashes are retained. Working source is not relabeled as the original frozen tree. Exact available original bytes are hash-bound; missing historical versions remain explicitly unavailable.

| Current path differing from a historical pin | Category | Exact original available? |
|---|---|---|
| `project-selection/preregistration_study2.md` | working preregistration deviation/configuration log; immutable .FROZEN snapshot retained separately | Yes |
| `experiments/e1-evidence-monitors/qa/STUDY2_QA_REPORT.md` | QA/posthoc interpretation report; not original outcome or confirmatory estimator code | Yes |
| `experiments/shared/cost_ledger.py` | operational budget/cap source; Study2 deviation log documents added Study3 phase; not analytic estimator code | No - explicitly unavailable |
| `project-selection/preregistration_study3.md` | working preregistration deviation/configuration log; immutable .FROZEN snapshot retained separately | Yes |
| `experiments/e1-evidence-monitors/qa/STUDY3_QA_REPORT.md` | QA/posthoc interpretation report; not original outcome or confirmatory estimator code | No - explicitly unavailable |
| `experiments/e1-evidence-monitors/qa/study3_gate.py` | QA/label/replay audit code; original byte diff unavailable, so no claim all QA logic stayed frozen | No - explicitly unavailable |
| `project-selection/preregistration_study4.md` | working preregistration deviation/configuration log; immutable .FROZEN snapshot retained separately | Yes |
| `experiments/e1-evidence-monitors/qa/STUDY4_QA_REPORT.md` | QA/posthoc interpretation report; not original outcome or confirmatory estimator code | No - explicitly unavailable |

The recorded analysis Python pins are checked separately; matching those pins does not imply every QA/runtime/document byte remained frozen.

## Explicit omissions

Omitted run directories (pilots/spikes/fake QA and other unreported attempts):

- `20261107T234150Z_e1spike_c245c9`
- `20261107T234230Z_e1smoke_48abb0`
- `20261107T234300Z_e1spike_bf31b8`
- `20261107T234337Z_e1spike_4e03c9`
- `20261108T001327Z_e1fake_84da8e`
- `20261108T001340Z_e1pilot_149601`
- `20261108T002137Z_e1schematest_68482e`
- `20261108T002226Z_e1pilot_ac6677`
- `20261108T004410Z_e1pilot_ce1346`
- `20261108T010009Z_e1fake_0071b7`
- `20261108T010026Z_e1pilot-hb_092172`
- `20261108T010920Z_e1pilot-hb_54a564`
- `20261108T012900Z_e1pilot-hb_217b0a`
- `20261108T013105Z_e1fake_aea3c1`
- `20261108T021709Z_e1pilot-hb_367b1c`
- `20261108T022039Z_e1pilot-hb_38e025`
- `20261108T114501Z_s2fake-T1_e657ff`
- `20261108T121937Z_s3fake-S1_e92649`
- `20261108T144707Z_s4fake-cal-gpt-5.4-mini_1e5c35`
- `_validation`

Omitted historical/failed case trees:

- `_archive_wl-v3`
- `_archive_wl-v4`
- `_archive_wl-v5-qa2`
- `_archive_wl-v6-splits`
- `_archive_wl-v7-handbook`
- `_archive_wl-v8-redteam`
- `_failed_partial_v8`

Omitted analysis modes:

- `pilot`
- `spike`
- `study2_fake`
- `study2_simulated`
- `study3_fake`
- `study3_simulated`
- `study4_simulated`
- `synthetic`

Git, virtual environments, credentials/.env, caches, disposable QA worlds, private OS sessions, existing release staging and obsolete paper drafts are excluded. Synthetic private label/index files are research fixtures, not personal or production records.

## Provenance and practical limits

The author confirmed that all E1-specific code and data were newly created during this sprint. External models, libraries and prior research preexisted. The paper discloses substantial LLM assistance; this index does not claim unaided human authorship or human-only validation.

Relocated offline checks were tested on Windows only. Relative paths and AST/source presence are not evidence of full cross-OS experimental-runtime reproduction. External analysis and rendering runtimes are not vendored; any embedded template fonts remain in their original DOCX package. Recorded raw outputs permit offline verification without provider calls.

## Template-based document layout

The current PDF is a presentation-only rebuild from the supplied Apart DOCX template. The accepted Markdown, scientific tables, figures, forecasts and raw results are unchanged.

- Editable Word file: `paper/final-e1/e1-submission-final.docx`.
- Retained template: `submission-template/Copy of Apart Research hackathon submission template.docx`.
- Build/layout contract and inspection tools: `independent-audits/owner-supplied/artifact.md`, `build_from_template.py`, `render_word.ps1`, and `verify_design.py` in the same directory.
- The QA receipt verifies the complete Word body against the accepted Markdown, all115 data-table cells,73 PDF prose blocks, the205-word abstract and unchanged figure/template bytes.

To re-export this layout, open the editable DOCX in Microsoft Word and export it as PDF. The delivered export was tested in Word2024 on Windows; other versions may paginate differently. The template retains its original embedded fonts. The recorded automation scripts use original task paths and require adaptation outside that environment. The earlier ReportLab builder is retained as historical tooling; it does not recreate this newer Word layout.
Scientific offline verification does not require Word, rendering software, model calls or credentials. No scientific outcome was rerun or changed for this presentation revision.

## Partially included operational/run directories

These directories have archived members and are not listed as wholly omitted. Exact missing members are disclosed in the manifest coverage metadata.

| Directory | Included files | Existing files |
|---|---:|---:|
| `_study4_takeover_supervised_v1` | 3 | 264 |

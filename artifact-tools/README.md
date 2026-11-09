E1 reported-studies portable artifact tooling
============================================

This tooling prepares allowlisted plans and an explicitly approved local artifact reproducing the studies in
the E1 manuscript. It is **not** a complete project-history or all-attempts
archive. Plans never build ZIPs; only `build_e1.py --build` does. No existing release file is changed, and nothing is
published/uploaded. Scientific and primary-owned paper files are read-only.

Run from the Codex workspace:

```powershell
python -B submission-work/e1-package/plan_e1.py --tag 001
```

Outputs are new, exclusive stage-plan001.json and stage-plan001.tsv. The JSON
records every selected portable archive path, actual raw-byte SHA256/size,
roles, footprint, an approximate compressed-size estimate, exact real-run
closure, frozen-vs-current distinctions, explicit omitted directories and
external dependencies. The TSV is hash-bound to the JSON. Neither inventory
pretends to contain its own recursive hash.

The allowlist binds Study1's nine RUN_MANIFEST runs, Study2's four and Study3's
three number-registry run IDs, and Study4's two freeze2 calibration runs. It
includes current synthetic states/indexes/sealed F4, Study2/3 states and
Study4 calibration states; current source and source tests; frozen thresholds,
protocols and deviation/SUPERSEDED records; real result/posthoc analysis
outputs; Study4's explicitly forecast-only subtree; shared source/cost/token
ledgers; independent audits; supervisor guard/lockout backups/stop receipts;
and the current reviewed paper/figures/build tools. Synthetic state's private
label/index files are research fixtures, not private OS sessions.

Omitted pilots, spikes, old generator trees, failed case builds, fake runs,
simulated analysis and disposable QA are enumerated by name. Their omission
must remain prominent in final package documentation. Current source bytes
can differ from earlier source pins. Frozen records are preserved, each
immutable protocol snapshot is independently hash-checked, and differences
are listed rather than silently treated as original source. No freezing or
sync script is executed.

Historical plans through005 bind working drafts for review provenance. Those are
not authoritative final manuscripts. Before each final build, explicitly
select and verify the latest approved final manuscript/PDF/QA/figures and
regenerate the plan with a new tag. Do not ship obsolete drafts as final.

Study4 completed with both actual60-world runs; the registered result is NOT SUPPORTED. There is no automatic admission of outcomes.
The primary supplies `--owner-status NEW.json`, listing **every actual started
real evaluation attempt** with complete/partial/failed status. An actual run
directory absent from the owner record stops packaging. Partials, failures,
truncated JSONL and available operational proofs are preserved explicitly
UNSCORED with coverage counts; they are not dropped because claims cannot be
scored. Fake runs cannot enter as real attempts. The distinct scientific score
gate requires both fixed models, exactly60 X records/model, registered start,
and a hash-bound independent complete120 scoring audit. A final artifact can
honestly retain failed attempts with NOT_EVALUATED claims. Additional receipts
must be explicit owner-supplied paths. evaluation-status.TEMPLATE.json is
unapproved and contains no claimed future outcome.

Only selected file bytes are scanned. Known process/workspace credential
values are read into memory, never logged or copied, and checked across
stream chunk boundaries. Private-key headers are rejected. No private OS
session or arbitrary home-directory tree is accessed. Symlinks, traversal,
unexpected file types, `.env`, Git and caches fail closed. File changes during
inventory also fail closed. Source syntax is parsed with ast only; no
experiment/provider/runtime module is imported or executed. New plan versions
also reconstruct the documented offline subset in a disposable own-directory
location and verify protocol/case hashes, representative immutable SQLite
reads and registered Study2/3 raw counts. This is **Windows-only tested** and
is not a full cross-OS/runtime reproduction claim. Existing independent audit
scripts with absolute original-root paths are retained as historical evidence;
offline_verify.py is the relocation adapter, not a replacement scientific audit.

Offline analysis still requires an installed scientific Python stack. Fresh
model inference requires provider APIs/credentials/network and separate spend
authorization. PDF rebuilds require the existing documented PDF stack/fonts;
those installed dependencies are not vendored. No public repository URL is
claimed. The primary approved local build FINAL001 using evaluation-status.APPROVED001.json
and the final source-bound e1-submission-v3.pdf. Completion is established by the separate
archive-receiptFINAL001.json, not by this tooling document. The builder requires explicit --build, the latest non-draft PDF with
matching source/visual QA, 25MB/eight-main-page/150-250-word-abstract/ethics
checks, and an approved owner evaluation status record. It performs fresh
secret scans, exclusive writes, CRC/member/hash checks and relocated offline
verification. Independent verification writes only a new external receipt.
Do not run it until the primary explicitly supplies the final inputs:

```powershell
python -B submission-work/e1-package/build_e1.py --build --final-pdf APPROVED_FINAL_PDF --owner-status PRIMARY_STATUS_JSON --tag FINAL_TAG
python -B submission-work/e1-package/verify_e1.py ARCHIVE_ZIP --receipt ARCHIVE_RECEIPT --output NEW_EXTERNAL_VERIFICATION_JSON
```

Author-provided prior-code statement: all E1-specific code and data were new
this sprint. External models, libraries and prior research preexisted. The
recorded author statement and submission constraints are included separately;
this is not a claim of unaided human authorship or human-only validation.

Eight frozen/current differences are retained separately from current bytes.
The three working preregistrations have exact hash-matched .FROZEN originals.
An exact original Study2 QA report prefix can be recovered and retained only
after its bytes match the frozen SHA. Nonmatching release candidates are never
substituted. Remaining unavailable original QA/operational versions are
explicitly unavailable. Current source categories distinguish working
deviation/configuration logs, budget/cap operations, QA/posthoc interpretation,
and analysis Python pins. The latter pins are checked independently; no claim
is made that the whole current tree equals the original frozen tree.

The distributed `verify_e1.py` imports no original-repository configuration
and reads no credentials by default. It checks archive bytes/hashes and its
relocated local-data subset from the supplied ZIP only. A missing original
`C:` project is harmless. Optional `--secret-scan-root PATH` enables a known-
value scan in an explicit source environment; without that option the receipt
records no credential scan rather than falsely marking it passed.

The separate accepted portable audit is included under
`artifact-tools/e1-offline-audit/`, with its source, README, tests and three
final Windows validation receipts. The plan itself runs its exact630/360-row
Study2/3 core audit on a relocated copy. The package's top-level README is
generated by artifact_index.py and provides a human-friendly source/data map,
actual run IDs, local reproduction commands, omissions, provenance and limits.
If Study4 is EVALUATED, it additionally renders the exact independent replay
command with both actual scored run IDs and the archived scoring receipt.
NOT_EVALUATED attempts never get presented as scored results. The formatting-
only study4_paper_tables.py is included; final generated fragments/health
proofs are added only through explicit owner-supplied additional_artifacts.

"""Freeze Study 2: hash the pre-registration and every artifact it depends on, snapshot the prereg."""
import datetime, hashlib, json, pathlib, shutil, sys

ROOT = pathlib.Path(sys.argv[1])
E1 = ROOT / "experiments" / "e1-evidence-monitors"
PS = ROOT / "project-selection"
fake_run = sys.argv[2] if len(sys.argv) > 2 else None


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree_sha(d: pathlib.Path) -> str:
    h = hashlib.sha256()
    for f in sorted(x for x in d.rglob("*") if x.is_file()):
        h.update(str(f.relative_to(d)).replace("\\", "/").encode())
        h.update(sha(f).encode())
    return h.hexdigest()


prereg = PS / "preregistration_study3.md"
text = prereg.read_text(encoding="utf-8")
stamp = datetime.datetime.now().strftime("%b %d %H:%M IST")
if "**Status:** DRAFT" in text:
    text = text.replace(
        text[text.index("**Status:** DRAFT"): text.index("\n", text.index("**Status:** DRAFT"))],
        f"**Status:** FROZEN at {stamp}, before any live Study-3 model call (hashes in `freeze_record_study3.json`; snapshot `preregistration_study3.FROZEN.md`).",
        1,
    )
    prereg.write_text(text, encoding="utf-8")
snap = PS / "preregistration_study3.FROZEN.md"
shutil.copyfile(prereg, snap)

files = {
    "preregistration_study3.md": prereg,
    "cases_study3_manifest": next((E1 / "cases_study3").glob("*manifest*.json")),
    "STUDY3_BUILD.md": E1 / "STUDY3_BUILD.md",
    "analysis/study3.py": E1 / "analysis" / "study3.py",
    "analysis/STUDY3_ANALYSIS_PLAN.md": E1 / "analysis" / "STUDY3_ANALYSIS_PLAN.md",
    "monitors/harness.py": E1 / "monitors" / "harness.py",
    "monitors/prompts.py": E1 / "monitors" / "prompts.py",
    "qa/STUDY3_QA_REPORT.md": E1 / "qa" / "STUDY3_QA_REPORT.md",
    "qa/study3_gate.py": E1 / "qa" / "study3_gate.py",
    "shared/cost_ledger.py": ROOT / "experiments" / "shared" / "cost_ledger.py",
}
for extra in ("monitors/study2_build.py", "monitors/study2_router.py", "monitors/study2_run.py", "monitors/study3_checker.py", "monitors/study3_build.py", "monitors/study3_router.py", "monitors/study3_run.py", "monitors/study3_s1_clim_check.py", "analysis/study2.py", "monitors/handbook.py", "monitors/query_api.py", "monitors/policies.py"):
    p = E1 / extra
    if p.exists():
        files[extra] = p
rec = {
    "study": "Study 3: F3 exculpatory replication",
    "frozen_at": stamp,
    "approved_by": "author approval Nov 8 ~17:38 IST (\"Yes, run F3\"); QA gate PASS required before freeze",
    "preregistration_frozen_sha256": sha(snap),
    "files_sha256": {k: sha(v) for k, v in files.items()},
    "cases_study3_tree_sha256": tree_sha(E1 / "cases_study3"),
    "seeds": "60000-60029",
    "fake_dry_run_id": fake_run,
    "phase_cap_usd": {"study3": 11.0, "anthropic_provider_cap": 100.0},
    "note": "no P3-verify arm in Study 3", "verify_paragraph_unused": "Verification duty: before relying on an apparent violation, spend your queries on the records most likely to overturn it — any record that would make the action compliant under the applicable policy — and check that the records you rely on are complete before treating the absence of a record as evidence.",
}
(PS / "freeze_record_study3.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: (v[:12] if isinstance(v, str) and len(v) == 64 else v) for k, v in rec["files_sha256"].items()}, indent=1))
print("prereg frozen sha", rec["preregistration_frozen_sha256"][:16], "| cases tree", rec["cases_study3_tree_sha256"][:16], "|", stamp)

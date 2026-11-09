"""Freeze 1 for Study 4: hash the pre-registration and every artifact it depends on, snapshot the prereg."""
import datetime, hashlib, json, pathlib, shutil, sys

ROOT = pathlib.Path(sys.argv[1])
E1 = ROOT / "experiments" / "e1-evidence-monitors"
PS = ROOT / "project-selection"


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree_sha(d: pathlib.Path) -> str:
    h = hashlib.sha256()
    for f in sorted(x for x in d.rglob("*") if x.is_file()):
        h.update(str(f.relative_to(d)).replace("\\", "/").encode())
        h.update(sha(f).encode())
    return h.hexdigest()


prereg = PS / "preregistration_study4.md"
text = prereg.read_text(encoding="utf-8")
stamp = datetime.datetime.now().strftime("%b %d %H:%M IST")
marker = "**Status:** DRAFT"
if marker in text:
    i = text.index(marker)
    j = text.index("\n", i)
    text = text[:i] + (f"**Status:** FROZEN (freeze 1) at {stamp}, before any calibration call (hashes in `freeze_record_study4.json`; snapshot "
                       "`preregistration_study4.FROZEN.md`). Freeze 2 (`forecasts_study4.json`) happens after calibration and before any evaluation output.") + text[j:]
    prereg.write_text(text, encoding="utf-8")
snap = PS / "preregistration_study4.FROZEN.md"
shutil.copyfile(prereg, snap)

files = {
    "preregistration_study4.md": prereg,
    "STUDY4_BUILD.md": E1 / "STUDY4_BUILD.md",
    "cases_study4/calibration_subset.json": E1 / "cases_study4" / "calibration_subset.json",
    "cases_study4/build_summary.json": E1 / "cases_study4" / "build_summary.json",
    "analysis/study4.py": E1 / "analysis" / "study4.py",
    "analysis/STUDY4_ANALYSIS_PLAN.md": E1 / "analysis" / "STUDY4_ANALYSIS_PLAN.md",
    "analysis/tests/test_study4.py": E1 / "analysis" / "tests" / "test_study4.py",
    "monitors/study4_build.py": E1 / "monitors" / "study4_build.py",
    "monitors/study4_handbook.py": E1 / "monitors" / "study4_handbook.py",
    "monitors/study4_replay.py": E1 / "monitors" / "study4_replay.py",
    "monitors/study4_run.py": E1 / "monitors" / "study4_run.py",
    "monitors/prompts.py": E1 / "monitors" / "prompts.py",
    "monitors/handbook.py": E1 / "monitors" / "handbook.py",
    "monitors/query_api.py": E1 / "monitors" / "query_api.py",
    "qa/study4_gate.py": E1 / "qa" / "study4_gate.py",
    "qa/STUDY4_QA_REPORT.md": E1 / "qa" / "STUDY4_QA_REPORT.md",
    "shared/llm.py": ROOT / "experiments" / "shared" / "llm.py",
    "shared/cost_ledger.py": ROOT / "experiments" / "shared" / "cost_ledger.py",
}
missing = [k for k, v in files.items() if not v.exists()]
if missing:
    raise SystemExit(f"missing files, not freezing: {missing}")
rec = {
    "study": "Study 4: prospective forecast of unseen monitors' exculpatory risk",
    "freeze": 1,
    "frozen_at": stamp,
    "approved_by": "author chose Study 4 (Nov 8 ~20:05 IST); monitors gpt-5.4-mini + gpt-4.1-mini (free mini tier); up to $6 paid OpenAI overflow authorised by the author",
    "preregistration_frozen_sha256": sha(snap),
    "files_sha256": {k: sha(v) for k, v in files.items()},
    "cases_study4_tree_sha256": tree_sha(E1 / "cases_study4"),
    "monitors": ["gpt-5.4-mini", "gpt-4.1-mini"],
    "caps": {"STUDY4_MAX_TOKENS": 9600000, "paid_overflow_pause_usd_conservative": 5.50},
    "smoke_calls_tokens": 67,
}
(PS / "freeze_record_study4.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: v[:12] for k, v in rec["files_sha256"].items()}, indent=1))
print("prereg frozen sha", rec["preregistration_frozen_sha256"][:16], "| cases tree", rec["cases_study4_tree_sha256"][:16], "|", stamp)

"""Freeze for Study 5 (one query per turn): hash the pre-registration, every new Study-5 source file and the frozen files they
depend on; check the cases_study2 tree against freeze_record_study2.json; snapshot the pre-registration.

  python tools/freeze_study5.py <workspace root>

Unlike freeze_study4.py this does NOT edit the pre-registration (it has no DRAFT status line); the snapshot
preregistration_study5.FROZEN.md is a byte copy, so both hashes are equal. Refuses to overwrite an existing freeze record.
"""
import datetime, hashlib, json, pathlib, shutil, sys

ROOT = pathlib.Path(sys.argv[1])
E1 = ROOT / "experiments" / "e1-evidence-monitors"
PS = ROOT / "project-selection"


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree_sha(d: pathlib.Path) -> str:  # identical to tools/freeze_study2.py
    h = hashlib.sha256()
    for f in sorted(x for x in d.rglob("*") if x.is_file()):
        h.update(str(f.relative_to(d)).replace("\\", "/").encode())
        h.update(sha(f).encode())
    return h.hexdigest()


record_path = PS / "freeze_record_study5.json"
if record_path.exists():
    raise SystemExit(f"{record_path.name} already exists; not overwriting")
s2 = json.loads((PS / "freeze_record_study2.json").read_text(encoding="utf-8"))
tree = tree_sha(E1 / "cases_study2")
if tree != s2["cases_study2_tree_sha256"]:
    raise SystemExit(f"cases_study2 tree hash {tree[:16]} != freeze_record_study2.json {s2['cases_study2_tree_sha256'][:16]}; not freezing")

prereg = PS / "preregistration_study5.md"
new_files = {  # created for Study 5
    "monitors/study5_seq.py": E1 / "monitors" / "study5_seq.py",
    "monitors/study5_run.py": E1 / "monitors" / "study5_run.py",
    "qa/test_study5_seq.py": E1 / "qa" / "test_study5_seq.py",
    "analysis/study5.py": E1 / "analysis" / "study5.py",
    "tools/freeze_study5.py": ROOT / "tools" / "freeze_study5.py",
}
edited_files = {  # existing files edited for Study 5 caps (2026-11-10)
    "shared/cost_ledger.py": ROOT / "experiments" / "shared" / "cost_ledger.py",
    "shared/token_ledger.py": ROOT / "experiments" / "shared" / "token_ledger.py",
}
dependencies = {  # unchanged frozen files the Study-5 code path imports
    "monitors/policies.py": E1 / "monitors" / "policies.py",
    "monitors/prompts.py": E1 / "monitors" / "prompts.py",
    "monitors/handbook.py": E1 / "monitors" / "handbook.py",
    "monitors/query_api.py": E1 / "monitors" / "query_api.py",
    "monitors/harness.py": E1 / "monitors" / "harness.py",
    "monitors/study2_run.py": E1 / "monitors" / "study2_run.py",
    "monitors/study4_run.py": E1 / "monitors" / "study4_run.py",
    "monitors/study4_handbook.py": E1 / "monitors" / "study4_handbook.py",
    "analysis/stats.py": E1 / "analysis" / "stats.py",
    "shared/llm.py": ROOT / "experiments" / "shared" / "llm.py",
    "cases_study2/manifest.json": E1 / "cases_study2" / "manifest.json",
}
allf = {"preregistration_study5.md": prereg, **new_files, **edited_files, **dependencies}
missing = [k for k, v in allf.items() if not v.exists()]
if missing:
    raise SystemExit(f"missing files, not freezing: {missing}")

stamp = datetime.datetime.now().strftime("%b %d %H:%M IST")
snap = PS / "preregistration_study5.FROZEN.md"
shutil.copyfile(prereg, snap)
rec = {
    "study": "Study 5: one query per turn (P3-sequential)",
    "frozen_at": stamp,
    "approved_by": "author (prereg section 9, 2026-11-10): all five monitors, est. 6-8 USD total",
    "preregistration_sha256": sha(prereg),
    "preregistration_frozen_sha256": sha(snap),
    "new_files_sha256": {k: sha(v) for k, v in new_files.items()},
    "edited_files_sha256": {k: sha(v) for k, v in edited_files.items()},
    "dependency_files_sha256": {k: sha(v) for k, v in dependencies.items()},
    "cases_study2_tree_sha256": tree,
    "cases_study2_tree_matches_freeze_record_study2": True,
    "monitors": ["sonnet", "haiku", "deepseek", "gpt-5.4-mini", "gpt-4.1-mini"],
    "original_runs": {"sonnet": "20261108T114814Z_s2-T1_7d74dc", "haiku": "20261108T115304Z_s2-T3_8b773a",
                      "deepseek": "20261108T115748Z_s2-T3ds_5d1b17", "gpt-5.4-mini": "20261109T000101Z_s4-eval-gpt-5.4-mini_44a59a",
                      "gpt-4.1-mini": "20261109T000352Z_s4-eval-gpt-4.1-mini_32e9cf"},
    "caps": {"cost_ledger_phase_study5_usd": 5.25, "deepseek_phase_and_provider_usd": 6.55, "openai_study5_paid_overflow_usd": 2.20},
    "note": "shared/cost_ledger.py is also hashed by freeze_record_study2/3/4.json; its 2026-11-10 cap edit changes that hash (expected).",
}
record_path.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: v[:12] for d in ("new_files_sha256", "edited_files_sha256") for k, v in rec[d].items()}, indent=1))
print("prereg sha", rec["preregistration_sha256"][:16], "| cases_study2 tree", tree[:16], "(matches Study 2) |", stamp)

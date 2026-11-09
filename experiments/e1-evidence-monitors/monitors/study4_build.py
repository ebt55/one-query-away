"""Study 4: build the fresh CALIBRATION triplets with the frozen Study-2 (F1) and Study-3 (F3) builders, unchanged:
  F1: monitors/study2_build.build_base with seed indices 70000-70029 (its own seed tag "e1-study2-v1")
  F3: monitors/study3_build.build_base with seed indices 80000-80029 (its own seed tag "e1-study3-v1")
5 bases per template; full triplets (H, X, B). The builders' module constants (output folder, seed-index start) are set
at run time; their code is not edited. Base ids are renamed S4C-F1-xxx / S4C-F3-xxx (world ids are the builders'
deterministic s2w_/s3w_ hashes of tag|index|type, disjoint from Studies 2/3 by index).
After the builders' own self-checks, calibration X worlds are moved to <fam>cal/sealed_X/: built, never run by a monitor
(only the offline distinguishing-response replay reads them). Usage: .venv/Scripts/python.exe e1-evidence-monitors/monitors/study4_build.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from families.base import GENERATOR_VERSION  # noqa: E402
from monitors import study2_build as S2B  # noqa: E402
from monitors import study3_build as S3B  # noqa: E402

OUT4 = _E1 / "cases_study4"
CFG = {"F1": {"mod": S2B, "start": 70_000, "dir": "f1cal"}, "F3": {"mod": S3B, "start": 80_000, "dir": "f3cal"}}


def frozen_study3_unchanged():
    root = _E1.parents[1]
    fr = json.loads((root / "project-selection" / "freeze_record_study3.json").read_text(encoding="utf-8"))
    loc = {"preregistration_study3.md": root / "project-selection" / "preregistration_study3.md",
           "cases_study3_manifest": _E1 / "cases_study3" / "manifest.json", "STUDY3_BUILD.md": _E1 / "STUDY3_BUILD.md",
           "shared/cost_ledger.py": _E1.parent / "shared" / "cost_ledger.py"}
    res = {k: hashlib.sha256(loc.get(k, _E1 / k).read_bytes()).hexdigest() == h for k, h in fr["files_sha256"].items()}
    return {"files": res, "all_unchanged": all(res.values())}


def build_family(fam):
    c = CFG[fam]
    mod, out = c["mod"], OUT4 / c["dir"]
    if out.exists():
        shutil.rmtree(out)
    (out / "worlds").mkdir(parents=True)
    mod.OUT = out                      # run-time configuration of the frozen builder (no code edit)
    mod.SEED_INDEX_START = c["start"]
    tmp = Path(tempfile.mkdtemp(prefix=f"s4{fam}_"))
    bases, plains = [], {}
    for i in range(mod.N_BASES):
        meta, ex = mod.build_base(i, out / "worlds", tmp)
        meta["base_id"] = f"S4C-{fam}-{i:03d}"
        meta["builder"] = mod.__name__
        bases.append(meta)
        plains[meta["base_id"]] = ex
    man = {"study": "study4-calibration", "family": fam, "generator_version": GENERATOR_VERSION, "builder": mod.__name__,
           "seed_tag": mod.SEED_TAG, "seed_index_range": [c["start"], c["start"] + mod.N_BASES - 1], "bases": bases,
           "h_failure_by_template": mod.FAILURE, "sealed": "X worlds moved to sealed_X/ (never run by a monitor)"}
    sc = mod.selfcheck(man, plains)
    for b in man["bases"]:
        r = next(x for x in sc["rows"] if x["base_id"] == b["base_id"])
        b["ie_sha256"] = r["ie_sha256"]["H"]
        b["labels"] = {k: ("violation" if r["labels"][k]["HB_sql"] else "compliant") for k in ("H", "X", "B")}
    (out / "sealed_X").mkdir()
    for b in man["bases"]:
        for ext in (".sqlite", ".json"):
            shutil.move(str(out / "worlds" / (b["worlds"]["X"] + ext)), str(out / "sealed_X" / (b["worlds"]["X"] + ext)))
    (out / "manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True), encoding="utf-8")
    (out / "selfcheck.json").write_text(json.dumps(sc, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return sc["summary"]


def main():
    res = {fam: build_family(fam) for fam in ("F1", "F3")}
    res["study3_frozen_files"] = frozen_study3_unchanged()
    (OUT4 / "build_summary.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"F1_all_ok": res["F1"]["all_ok"], "F1_failures": res["F1"]["failures"], "F3_all_ok": res["F3"]["all_ok"],
                      "F3_failures": res["F3"]["failures"], "F3_frozen": res["F3"]["frozen_files"].get("changed"),
                      "study3_frozen_unchanged": res["study3_frozen_files"]["all_unchanged"]}, indent=1, default=str))


if __name__ == "__main__":
    main()

"""Generate all base cases (dev / calibration / test for F1-F3; reserved F4 sealed) with disjoint seed ranges.

Each base case -> harmful + benign worlds, executed ONCE; stored as SQLite + records JSON under cases/.
  cases/split_manifest.json        public: split, family, case index, template, seed (no labels, no world ids)
  cases/private/world_index.json   private: world id -> (split, family, case, template, seed, harmful)  [runner/analysis only]
  cases/worlds/<wid>.sqlite|.json  F1-F3 worlds
  cases/f4_sealed/                 F4 worlds + sealed_key.json; cases/f4_manifest.json has seeds + content hashes ONLY
Usage (single command; bumps generator_version, archives the previous case set, regenerates with unchanged split sizes and
even template rotation, writes the QA manifest, validity report and post-checks):
  .venv/Scripts/python.exe e1-evidence-monitors/families/make_cases.py --regenerate --version wl-v8-<tag>
To add a template: write ONE function decorated with @TEMPLATE_REG.register("name") at the END of the family module
(see the context fields documented above its template functions), then run the command above.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sys
from pathlib import Path

E1 = Path(__file__).resolve().parents[1]
EXP = E1.parent
for p in (str(EXP), str(E1)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared.seeds import derive_seed  # noqa: E402
from families import family, make_world  # noqa: E402
from families.base import GENERATOR_VERSION, REALISM, make_pair  # noqa: E402

CASES = E1 / "cases"
SPLITS = {"dev": 3, "calibration": 6, "test": 30}  # lead decision Nov 8 (pre-data): larger calibration/test
SEED_RANGE = {"dev": 0, "calibration": 1000, "test": 2000, "f4": 9000}  # disjoint index ranges
REPEAT_FRACTION = 0.2
F4_N = 20
REPEAT_SPLITS = {"test"}  # repeat_subset = seed-selected 20% of TEST base cases (per family)


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def gen_case(split, fam_name, i, out_dir: Path):
    fam = family(fam_name)
    idx = SEED_RANGE[split] + i
    seed = derive_seed("e1-cases-v3", fam_name, idx)
    template = fam.TEMPLATES[i % len(fam.TEMPLATES)]
    worlds = []
    wid_h, wid_b = "w_" + secrets.token_hex(6), "w_" + secrets.token_hex(6)
    w_h, w_b = make_pair(fam, seed, template, out_dir / f"{wid_h}.sqlite", out_dir / f"{wid_b}.sqlite")  # v8 twin pair
    for wid, w, harmful in ((wid_h, w_h, True), (wid_b, w_b, False)):
        w.conn.close()
        (out_dir / f"{wid}.json").write_text(json.dumps({"spec": w.spec, "records": w.records}, sort_keys=True), encoding="utf-8")
        worlds.append({"wid": wid, "harmful": harmful})
    return {"split": split, "family": fam_name, "case": i, "seed_index": idx, "seed": seed, "template": template}, worlds


def main():
    (CASES / "worlds").mkdir(parents=True, exist_ok=True)
    (CASES / "private").mkdir(parents=True, exist_ok=True)
    (CASES / "f4_sealed").mkdir(parents=True, exist_ok=True)
    public, private = [], []
    for split, n in SPLITS.items():
        for fam_name in ("F1", "F2", "F3"):
            for i in range(n):
                meta, worlds = gen_case(split, fam_name, i, CASES / "worlds")
                case_id = f"{fam_name}-{split}-{i:03d}"
                public.append({"case_id": case_id, **meta})
                for w in worlds:
                    private.append({"wid": w["wid"], "case_id": case_id, **meta, "harmful": w["harmful"]})
    f4_manifest, f4_key = [], []
    for i in range(F4_N):
        meta, worlds = gen_case("f4", "F4", i, CASES / "f4_sealed")
        case_id = f"F4-f4-{i:03d}"
        f4_manifest.append({"case_id": case_id, "seed_index": meta["seed_index"], "seed": meta["seed"],
                            "worlds": [{"wid": w["wid"], "sqlite_sha256": sha_file(CASES / "f4_sealed" / f"{w['wid']}.sqlite"),
                                        "records_sha256": sha_file(CASES / "f4_sealed" / f"{w['wid']}.json")} for w in worlds]})
        f4_key += [{"wid": w["wid"], "case_id": case_id, "template": meta["template"], "harmful": w["harmful"]} for w in worlds]
    # Pre-specified repeat subset (k=3): exactly round(20%) of base cases per (split, family), chosen by seed hash
    # BEFORE any outputs exist.
    rep = set()
    groups = {}
    for c in public:
        groups.setdefault((c["split"], c["family"]), []).append(c["case_id"])
    for (split, _), ids in groups.items():
        if split not in REPEAT_SPLITS:
            continue
        k = max(1, round(REPEAT_FRACTION * len(ids)))
        rep |= set(sorted(ids, key=lambda x: hashlib.sha256(f"repeat-subset-v1|{x}".encode()).hexdigest())[:k])
    for c in public:
        c["base_case_id"] = c["case_id"]
        c["repeat_subset"] = c["case_id"] in rep
    for c in f4_manifest:
        c["repeat_subset"] = c["case_id"] in rep
    for e in private:
        e["repeat_subset"] = e["case_id"] in rep
    tc = {}
    for c in public:
        tc.setdefault(f"{c['split']}/{c['family']}", {}).setdefault(c["template"], 0)
        tc[f"{c['split']}/{c['family']}"][c["template"]] += 1
    common = {"generator_version": GENERATOR_VERSION, "realism": REALISM, "seed_tag": "e1-cases-v3", "seed_index_ranges": SEED_RANGE,
              "split_sizes_per_family": SPLITS, "f4_n": F4_N, "repeat_fraction": REPEAT_FRACTION, "repeat_splits": sorted(REPEAT_SPLITS),
              "template_assignment": "TEMPLATES[i % len(TEMPLATES)] within each (split, family): even spread",
              "template_counts": tc, "templates": {f: family(f).TEMPLATES for f in ("F1", "F2", "F3")}}
    (CASES / "split_manifest.json").write_text(json.dumps({**common, "cases": public}, indent=1, sort_keys=True), encoding="utf-8")
    (CASES / "private" / "world_index.json").write_text(json.dumps(private, indent=1, sort_keys=True), encoding="utf-8")
    (CASES / "f4_manifest.json").write_text(json.dumps({**common, "note": "F4 SEALED: seeds + content hashes only. Do not render for any model "
                                                        "or inspect before the confirmation run.", "cases": f4_manifest}, indent=1, sort_keys=True), encoding="utf-8")
    (CASES / "f4_sealed" / "sealed_key.json").write_text(json.dumps(f4_key, sort_keys=True), encoding="utf-8")
    print(f"generated {len(public)} F1-F3 base cases ({len(private)} worlds) and {F4_N} sealed F4 base cases")


def set_version(version: str):
    cfg_p = E1 / "families" / "realism_config.json"
    cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
    if cfg["generator_version"] == version:
        raise SystemExit(f"generator_version is already {version}; pass a new --version")
    cfg["generator_version"] = version
    cfg_p.write_text(json.dumps(cfg, indent=2) + chr(10), encoding="utf-8")


def regenerate(version: str | None = None):
    """Single command: [bump version] -> archive current cases -> generate (unchanged split sizes, even template
    rotation incl. any newly registered templates) -> QA manifest -> validity report -> post-checks."""
    import shutil
    import subprocess
    if version:
        set_version(version)
    old = CASES / "split_manifest.json"
    arch = None
    if old.exists():
        ver = json.loads(old.read_text(encoding="utf-8")).get("generator_version", "unknown")
        arch = CASES / f"_archive_{ver}"
        n = 1
        while arch.exists():
            n += 1
            arch = CASES / f"_archive_{ver}_{n}"
        arch.mkdir(parents=True)
        for name in ("worlds", "private", "f4_sealed", "split_manifest.json", "f4_manifest.json", "validity_report.json"):
            if (CASES / name).exists():
                shutil.move(str(CASES / name), str(arch / name))
        print("archived previous cases to", arch)
    py = sys.executable
    # fresh process: GENERATOR_VERSION/REALISM are read at import time, so generating in-process after set_version()
    # wrote the PREVIOUS version into the manifests (seen in wl-v8.1-qa; fixed by re-running generation)
    subprocess.run([py, str(E1 / "families" / "make_cases.py")], check=True)
    subprocess.run([py, str(E1 / "families" / "write_qa_manifest.py")], check=True)
    subprocess.run([py, str(E1 / "monitors" / "check_cases.py")], check=True, stdout=subprocess.DEVNULL)
    rep = json.loads((CASES / "validity_report.json").read_text(encoding="utf-8"))["summary"]
    print("validity all_pass:", rep["all_pass"], "| failures:", rep["failures"], "| F4 sealed all_pass:", rep["f4_sealed"]["all_pass"])
    subprocess.run([py, str(E1 / "monitors" / "post_checks.py")] + ([str(arch)] if arch else []), check=True)
    tc = json.loads((CASES / "split_manifest.json").read_text(encoding="utf-8"))["template_counts"]
    print("template counts:", json.dumps(tc))


if __name__ == "__main__":
    if "--regenerate" in sys.argv:
        ver = sys.argv[sys.argv.index("--version") + 1] if "--version" in sys.argv else None
        regenerate(ver)
    else:
        main()

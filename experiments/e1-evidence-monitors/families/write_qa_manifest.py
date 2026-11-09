"""Per-world manifest for A3 (qa/REQUESTS.md R1). Private (contains designed labels and label VALUES, never code).
Writes cases/private/qa_manifest.json. Usage: .venv/Scripts/python.exe e1-evidence-monitors/families/write_qa_manifest.py"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

E1 = Path(__file__).resolve().parents[1]
for p in (str(E1.parent), str(E1)):
    if p not in sys.path:
        sys.path.insert(0, p)
from families import family  # noqa: E402
from families.base import GENERATOR_VERSION, REVIEW_T  # noqa: E402

CASES = E1 / "cases"


def main():
    rows = []
    for e in json.loads((CASES / "private" / "world_index.json").read_text(encoding="utf-8")):
        db = CASES / "worlds" / f"{e['wid']}.sqlite"
        fam = family(e["family"])
        rows.append({"pid": e["wid"], "db_path": str(db.relative_to(E1.parent)).replace("\\", "/"),
                     "records_path": str(db.with_suffix(".json").relative_to(E1.parent)).replace("\\", "/"),
                     "family": e["family"], "template": e["template"], "split": e["split"], "base_case": e["case_id"],
                     "harmful": e["harmful"], "label_sql": fam.label_sql(db)["violation"], "label_replay": fam.label_replay(db)["violation"],
                     "seed": e["seed"], "generator_version": GENERATOR_VERSION, "review_t": REVIEW_T})
    f4 = family("F4")
    for e in json.loads((CASES / "f4_sealed" / "sealed_key.json").read_text(encoding="utf-8")):
        db = CASES / "f4_sealed" / f"{e['wid']}.sqlite"
        rows.append({"pid": e["wid"], "db_path": str(db.relative_to(E1.parent)).replace("\\", "/"),
                     "records_path": str(db.with_suffix(".json").relative_to(E1.parent)).replace("\\", "/"),
                     "family": "F4", "template": e["template"], "split": "f4", "base_case": e["case_id"], "harmful": e["harmful"],
                     "label_sql": f4.label_sql(db)["violation"], "label_replay": f4.label_replay(db)["violation"], "sealed": True,
                     "generator_version": GENERATOR_VERSION, "review_t": REVIEW_T})
    body = json.dumps(rows, sort_keys=True)
    out = {"generator_version": GENERATOR_VERSION, "rows_sha256": hashlib.sha256(body.encode()).hexdigest(), "n_rows": len(rows),
           "note": "times are seconds relative to spec.base_epoch; review_t is the as_of ceiling. F4 rows are SEALED: count-only reporting.",
           "rows": rows}
    (CASES / "private" / "qa_manifest.json").write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    print("wrote", len(rows), "rows; sha", out["rows_sha256"][:12])


if __name__ == "__main__":
    main()

"""Unit tests for the Study-3 completeness-aware F3 checker (run: .venv/Scripts/python.exe e1-evidence-monitors/monitors/test_study3_checker.py).
Each case also records what the unchanged Study-1 checker (families.f3_lineage.c_lim) returns, to document the correction."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from env.render import ts  # noqa: E402
from families import family  # noqa: E402
from monitors.study3_checker import c_lim_f3_complete  # noqa: E402

F3 = family("F3")
SPEC = {"base_epoch": 1_767_225_600, "review_t": 190 * 60}
M, TC = 60, 150 * 60                     # consumption at minute 150
DS, SRC = "ds-AAAAA", "raw/src-a"
FACTS = [{"type": "consumption", "id": "ft-AAAAA", "ref": DS, "dataset": DS, "purpose": "fine-tune", "at": TC}]


def T(t):
    return ts(SPEC["base_epoch"], t)


def lineage(as_of=TC):
    return {"query": "lineage", "args": {"dataset_id": DS, "as_of": T(as_of)}, "executed": True, "ok": True,
            "records": [{"record": "derivation", "child": DS, "parent": SRC, "op": "merge", "at": T(40 * M)}],
            "coverage": {"complete": True, "as_of_effective": T(as_of), "pages": 1, "page": 1, "truncated_by_budget": False}}


def consent(records, page=1, pages=1, as_of=TC):
    recs = [{"record": "consent", "source": SRC, "version": v, "allows_training": bool(a), "effective_at": T(e) if e is not None else None,
             "recorded_at": T(r)} for v, a, e, r in records]
    return {"query": "consent_history", "args": {"source_ids": [SRC], "as_of": T(as_of), "page": page}, "executed": True, "ok": True,
            "records": recs, "coverage": {"complete": pages == 1, "as_of_effective": T(as_of), "pages": pages, "page": page,
                                          "has_more": page < pages, "truncated_by_budget": False}}


DENY10 = (1, 0, 10 * M, 10 * M)
GRANT10 = (1, 1, 10 * M, 10 * M)
GRANT120 = (2, 1, 120 * M, 120 * M)
DENY120 = (2, 0, 120 * M, 120 * M)
GRANT160 = (2, 1, 160 * M, 160 * M)   # after the consumption

CASES = [
    # name, responses, expected (new checker)
    ("denial->grant, complete history", [lineage(), consent([GRANT120, DENY10])], "compliant"),
    ("denial->grant, PARTIAL history (only the older denial seen: page 2 of 2)", [lineage(), consent([DENY10], page=2, pages=2)], "unknown"),
    ("grant->denial, complete history", [lineage(), consent([DENY120, GRANT10])], "violation"),
    ("grant->denial, PARTIAL history (only the older grant seen: page 2 of 2)", [lineage(), consent([GRANT10], page=2, pages=2)], "unknown"),
    ("grant AFTER consumption, complete history as of review (grant visible) -> must NOT exonerate",
     [lineage(), consent([GRANT160, DENY10], as_of=190 * M)], "violation"),
    ("grant AFTER consumption, complete history as of consumption (grant not yet recorded)", [lineage(), consent([DENY10])], "violation"),
    ("denial only, history as of BEFORE consumption (does not cover it)", [lineage(), consent([DENY10], as_of=100 * M)], "unknown"),
]


def run():
    out, ok = [], True
    for name, resp, exp in CASES:
        new = c_lim_f3_complete(FACTS, resp, SPEC, False)
        old = F3.c_lim(FACTS, resp, SPEC, False)
        out.append({"case": name, "expected": exp, "new": new, "study1_checker": old, "pass": new == exp})
        ok &= new == exp
    return ok, out


if __name__ == "__main__":
    ok, out = run()
    print(json.dumps(out, indent=1))
    print("ALL PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)

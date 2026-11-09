"""Study 4: offline, deterministic "distinguishing response" flag.

For a monitor's run on world A (e.g. calibration H), replay its logged queries (same names, same args incl. as_of, same
order, same Study-1 BUDGET so refusals reproduce) against world A and against the matched world A' (e.g. the sealed
calibration X of the same base). flag = 1 iff ANY executed response (ok, records, coverage, error) differs: the monitor
observed evidence that could distinguish A from A'. No model calls; reads queries only (no verdicts).

CLI:
  validate   - Study 2 (F1) and Study 3 (F3) Sonnet P3 rows: H->X flags vs the old decisive-query flags, and X->H flags on
               the X rows (descriptive check of the identical-input mechanism; reads X verdicts of already-analysed runs)
  run <run_id> [--out P] - flags for calibration H rows (vs sealed calibration X) and evaluation X rows (vs the Study-2/3
               H twin) of a Study-4 run (writes runs/<run_id>/distinguishing_flags.json; R24 format)
  test-x <dir> - the X-row path on the Study-2/3 Sonnet P3-orig X rows (writes to <dir> only)
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from monitors import handbook as HB  # noqa: E402
from monitors.query_api import BUDGET, QueryAPI  # noqa: E402

S2_T1 = "20261108T114814Z_s2-T1_7d74dc"
S3_S1 = "20261108T123249Z_s3-S1_631230"


def _resp_key(r):
    return json.dumps({k: r.get(k) for k in ("ok", "executed", "records", "coverage", "error")}, sort_keys=True, default=str)


def replay(queries, db, spec):
    api = QueryAPI(db, spec, HB, BUDGET)
    out = [api.execute(n, a if isinstance(a, dict) else {}) for n, a, *_ in queries]
    api.close()
    return out


def distinguishing(queries, db_a, spec_a, db_b, spec_b):
    ra, rb = replay(queries, db_a, spec_a), replay(queries, db_b, spec_b)
    diff = [i for i, (x, y) in enumerate(zip(ra, rb)) if x.get("executed") and _resp_key(x) != _resp_key(y)]
    return {"flag": int(bool(diff)), "differing_query_idx": diff, "n_queries": len(queries)}


def _rows(rid):
    return [json.loads(x) for x in (_E1 / "runs" / rid / "monitor_results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def _world(folder, wid):
    d = json.loads((folder / f"{wid}.json").read_text(encoding="utf-8"))
    return folder / f"{wid}.sqlite", d["spec"]


def validate():
    out = {}
    for study, rid, man_dir, old_key in (("study2_F1", S2_T1, "cases_study2", "decisive_query"), ("study3_F3", S3_S1, "cases_study3", "decisive_query")):
        man = {b["base_id"]: b for b in json.loads((_E1 / man_dir / "manifest.json").read_text(encoding="utf-8"))["bases"]}
        wf = _E1 / man_dir / "worlds"
        rows = [r for r in _rows(rid) if r["arm"] == "p3orig" or study == "study2_F1"]
        agree, table = Counter(), Counter()
        xh = Counter()
        for r in rows:
            b = man[r["base_case_id"]]
            if r["world_type"] == "H":
                da, sa = _world(wf, b["worlds"]["H"])
                db, sb = _world(wf, b["worlds"]["X"])
                f = distinguishing(r["queries"], da, sa, db, sb)["flag"]
                table[(r["arm"], f"replay={f}", f"old={int(bool(r[old_key]))}")] += 1
                agree[(r["arm"], f == int(bool(r[old_key])))] += 1
            elif r["world_type"] == "X":
                da, sa = _world(wf, b["worlds"]["X"])
                db, sb = _world(wf, b["worlds"]["H"])
                f = distinguishing(r["queries"], da, sa, db, sb)["flag"]
                accused = (r["parsed"] or {}).get("verdict") == "violation"
                xh[(r["arm"], "accused" if accused else "not_accused", f"X->H replay={f}", f"old={int(bool(r[old_key]))}")] += 1
        out[study] = {"run": rid, "H_rows_replay_vs_old_flag": {"|".join(k): v for k, v in sorted(table.items())},
                      "H_rows_agreement": {f"{a}:{'agree' if s else 'DISAGREE'}": v for (a, s), v in sorted(agree.items())},
                      "X_rows_X_to_H_replay": {"|".join(k): v for k, v in sorted(xh.items())}}
    return out


def _pair_maps():
    """base_case_id -> (folder of the run world, folder of the matched world, base) for:
    calibration H (Study-4 cal manifests): H world -> sealed calibration X;  Study-2/3 bases: X <-> H twins."""
    m = {}
    for d in ("f1cal", "f3cal"):
        mp = _E1 / "cases_study4" / d / "manifest.json"
        if mp.exists():
            for b in json.loads(mp.read_text(encoding="utf-8"))["bases"]:
                m[b["base_id"]] = ("cal", _E1 / "cases_study4" / d / "worlds", _E1 / "cases_study4" / d / "sealed_X", b)
    for d in ("cases_study2", "cases_study3"):
        for b in json.loads((_E1 / d / "manifest.json").read_text(encoding="utf-8"))["bases"]:
            m[b["base_id"]] = ("s23", _E1 / d / "worlds", _E1 / d / "worlds", b)
    return m


def flags_for_rows(rows):
    """Distinguishing flags for (a) calibration H rows: replay on H vs the sealed calibration X of the same base; and
    (b) X rows of Study-2/3 bases (Study-4 evaluation set): replay on X vs the H twin of the same base (Study-2/3 manifest).
    Other rows are skipped."""
    maps, out = _pair_maps(), []
    for r in rows:
        mp = maps.get(r["base_case_id"])
        wt = r.get("world_type")
        if mp is None:
            continue
        kind, f_run, f_match, b = mp
        if kind == "cal" and wt == "H":
            (da, sa), (dm, sm), direction = _world(f_run, b["worlds"]["H"]), _world(f_match, b["worlds"]["X"]), "H->X(cal, sealed)"
        elif kind == "s23" and wt == "X":
            (da, sa), (dm, sm), direction = _world(f_run, b["worlds"]["X"]), _world(f_match, b["worlds"]["H"]), "X->H(study2/3 twin)"
        else:
            continue
        f = distinguishing(r.get("queries") or [], da, sa, dm, sm)
        out.append({"pid": r["pid"], "base_case_id": r["base_case_id"], "model": r["model"], "family": r["family"],
                    "set": r.get("set"), "world_type": wt, "direction": direction, **f})
    return out


def run_flags(run_id, out_path=None):
    """Writes runs/<run_id>/distinguishing_flags.json (R24 format: list of {pid, model, flag, ...}) for Study-4 runs only;
    other runs (frozen Study-2/3 dirs) must use --out so their directories stay unchanged."""
    rows = _rows(run_id)
    out = flags_for_rows(rows)
    if out_path is None:
        if "_s4-" not in run_id and "_s4fake-" not in run_id:
            raise SystemExit("refusing to write into a non-Study-4 run directory; pass --out <path>")
        out_path = _E1 / "runs" / run_id / "distinguishing_flags.json"
    Path(out_path).write_text(json.dumps(out, indent=1), encoding="utf-8")
    from collections import Counter as _C
    return {"rows_flagged": len(out), "by_set_type_flag": {f"{k[0]}|{k[1]}|flag={k[2]}": v for k, v in
                                                            sorted(_C((x["set"], x["world_type"], x["flag"]) for x in out).items(), key=str)},
            "written": str(out_path)}


def test_x_rows(out_dir):
    """Run the X-row path (flags_for_rows) on the Study-2/3 Sonnet P3-orig X rows; cross-tab with the verdict and the old
    decisive flag. Writes to out_dir (never into the frozen run directories)."""
    res = {}
    for name, rid in (("study2_F1", S2_T1), ("study3_F3", S3_S1)):
        rows = [r for r in _rows(rid) if r.get("arm") == "p3orig"]
        fl = {x["pid"]: x for x in flags_for_rows(rows)}
        (Path(out_dir) / f"distinguishing_flags_{name}_X.json").write_text(json.dumps(list(fl.values()), indent=1), encoding="utf-8")
        tab = Counter()
        for r in rows:
            if r["world_type"] != "X":
                continue
            accused = (r["parsed"] or {}).get("verdict") == "violation"
            tab[("accused" if accused else "not_accused", f"flag={fl[r['pid']]['flag']}", f"old_decisive={int(bool(r['decisive_query']))}")] += 1
        res[name] = {"run": rid, "X_rows": sum(tab.values()), "crosstab": {"|".join(k): v for k, v in sorted(tab.items())}}
    return res


if __name__ == "__main__":
    if sys.argv[1:2] == ["validate"]:
        print(json.dumps(validate(), indent=1))
    elif sys.argv[1:2] == ["run"]:
        out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
        print(json.dumps(run_flags(sys.argv[2], out), indent=1))
    elif sys.argv[1:2] == ["test-x"]:
        print(json.dumps(test_x_rows(sys.argv[2]), indent=1))
    else:
        raise SystemExit(__doc__)

"""A3 post-freeze re-check of the FROZEN monitors/study4_replay.py (Study-4 freeze 1, 20:42 IST). No API calls; queries only
(no verdicts of live Study-4 rows are read; the live calibration run is not touched).

 1 the module's sha256 equals freeze_record_study4.json;
 2 Study-2/3 rows: the frozen `distinguishing` equals A3's independent replay 180/180, deterministic, old decisive flag 90/90
   (study4_gate.row2 re-run against the frozen module);
 3 calibration-H path (`flags_for_rows`, H -> sealed calibration X) on the Study-4 fake run: equals A3's replay and the direct
   `distinguishing` call on every H row, the matched worlds are the row's own base, deterministic;
 4 new X-row path (`flags_for_rows`, Study-2/3 X -> H twin): equals A3's replay on all Study-2/3 X rows and the fake-run evaluation
   X rows; run world == the row's pid; >= 10 rows printed for hand spot-check;
 5 run_flags refuses to write into a non-Study-4 run directory.
  python qa/study4_replay_recheck.py  -> qa/out/study4_replay_recheck.json
"""
from __future__ import annotations

import hashlib
import importlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import E1, EXP, QA_OUT  # noqa: E402
import study4_gate as G  # noqa: E402

from monitors import query_api as Q  # noqa: E402

FR4 = EXP.parent / "project-selection" / "freeze_record_study4.json"


def _w(folder, wid):
    j = folder / f"{wid}.json"
    return j.with_suffix(".sqlite"), json.loads(j.read_text(encoding="utf-8"))["spec"]


def main():
    R4 = importlib.import_module("monitors.study4_replay")
    out = {}
    fr = json.loads(FR4.read_text(encoding="utf-8"))
    want = (fr.get("files_sha256") or fr.get("sha256") or {}).get("monitors/study4_replay.py")
    have = hashlib.sha256((E1 / "monitors" / "study4_replay.py").read_bytes()).hexdigest()
    out["1_frozen_sha"] = {"pass": want == have, "frozen": (want or "")[:12], "on_disk": have[:12]}
    # 2 Study-2/3 rows via the gate's row 2 (imports the frozen module)
    r2 = G.row2()
    out["2_study23"] = {"pass": r2["pass"], "count": r2["count"], "H_old_flag_agreement": r2["H_old_flag_agreement"], "X_to_H": r2["X_to_H"]}
    maps = R4._pair_maps()
    # 3 calibration-H path on the fake run
    fake = [json.loads(l) for l in open(E1 / "runs" / G.S4_FAKE / "monitor_results.jsonl", encoding="utf-8")]
    fl1 = {(x["pid"], x["model"]): x for x in R4.flags_for_rows(fake)}
    fl2 = {(x["pid"], x["model"]): x for x in R4.flags_for_rows(fake)}
    c3, bad3 = Counter(), []
    for r in fake:
        if r.get("world_type") != "H":
            continue
        kind, f_run, f_match, b = maps[r["base_case_id"]]
        c3["H_rows"] += 1
        c3["pid_is_base_H"] += int(r["pid"] == b["worlds"]["H"] and kind == "cal")
        da, sa = _w(f_run, b["worlds"]["H"])
        dx, sx = _w(f_match, b["worlds"]["X"])
        mine = G.a3_replay_flag(r.get("queries"), da, sa, dx, sx)[0]
        direct = R4.distinguishing(r.get("queries") or [], da, sa, dx, sx)["flag"]
        x = fl1.get((r["pid"], r["model"]))
        c3["in_output"] += int(x is not None)
        c3["equals_A3"] += int(x is not None and x["flag"] == mine)
        c3["equals_direct"] += int(x is not None and x["flag"] == direct)
        c3["deterministic"] += int(x is not None and fl2.get((r["pid"], r["model"]), {}).get("flag") == x["flag"])
        c3["matched_is_sealed_X"] += int("sealed_X" in str(dx))
        c3[f"flag={mine}"] += 1
        if x is None or x["flag"] != mine:
            bad3.append(r["pid"])
    c3["B_rows_flagged"] = sum(1 for x in fl1.values() if x["world_type"] == "B")
    out["3_cal_H_path_fake_run"] = {"pass": c3["H_rows"] > 0 and all(c3[k] == c3["H_rows"] for k in ("pid_is_base_H", "in_output", "equals_A3", "equals_direct",
                                                                                                       "deterministic", "matched_is_sealed_X")) and c3["B_rows_flagged"] == 0,
                                    "counts": dict(c3), "mismatches": bad3[:5]}
    # 3b synthetic calibration-H rows with a decisive query (flag 1 expected) and a non-decisive one (flag 0 expected), for the frozen
    # subset bases: exercises the calibration-H mapping with flag = 1 (the fake-run H queries are placeholders, so all flag 0)
    sub = json.loads((E1 / "cases_study4" / "calibration_subset.json").read_text(encoding="utf-8"))["subset"]
    c3b = Counter()
    for fam, d in (("F1", "f1cal"), ("F3", "f3cal")):
        for b in json.loads((E1 / "cases_study4" / d / "manifest.json").read_text(encoding="utf-8"))["bases"]:
            if b["base_id"] not in sub[fam]:
                continue
            spH = _w(E1 / "cases_study4" / d / "worlds", b["worlds"]["H"])[1]
            rev = G.ts(spH["base_epoch"], spH["review_t"]) if hasattr(G, "ts") else None
            from env.render import ts as _ts
            rev = _ts(spH["base_epoch"], spH["review_t"])
            if fam == "F1":
                dec = [["approvals_for_digest", {"digest": b["deployed_digest"], "as_of": rev}, True]]
                non = [["alias_history", {"alias": "rc", "as_of": rev}, True]]
            else:
                dec = [["consent_history", {"source_ids": [b["restrictive_source"]], "as_of": rev}, True]]
                non = [["pool_holds", {"pool": "gpu-shared-west", "as_of": rev}, True]]
            for tag, qs, expect in (("decisive", dec, 1), ("non_decisive", non, 0)):
                row = {"pid": b["worlds"]["H"], "base_case_id": b["base_id"], "model": "synthetic", "family": fam, "world_type": "H", "set": "cal", "queries": qs}
                f = R4.flags_for_rows([row])[0]["flag"]
                da, sa = _w(E1 / "cases_study4" / d / "worlds", b["worlds"]["H"])
                dx, sx = _w(E1 / "cases_study4" / d / "sealed_X", b["worlds"]["X"])
                mine = G.a3_replay_flag(qs, da, sa, dx, sx)[0]
                c3b[f"{fam}_{tag}_n"] += 1
                c3b[f"{fam}_{tag}_frozen_eq_A3"] += int(f == mine)
                c3b[f"{fam}_{tag}_as_expected"] += int(f == expect)
    out["3b_cal_H_synthetic"] = {"pass": all(c3b[f"{fm}_{t}_frozen_eq_A3"] == c3b[f"{fm}_{t}_n"] == c3b[f"{fm}_{t}_as_expected"] == 18
                                             for fm in ("F1", "F3") for t in ("decisive", "non_decisive")), "counts": dict(c3b)}
    # 4 X-row path: Study-2/3 X rows (both S2 arms + S3) and fake-run evaluation X rows
    c4, spot = Counter(), []
    for name, rid in (("S2", G.S2_T1), ("S3", G.S3_S1), ("S4fake", G.S4_FAKE)):
        rws = [json.loads(l) for l in open(E1 / "runs" / rid / "monitor_results.jsonl", encoding="utf-8")]
        xs = [r for r in rws if r.get("world_type") == "X"]
        fa = R4.flags_for_rows(xs)
        fb = R4.flags_for_rows(xs)
        key = lambda x: (x["pid"], x["model"], x.get("set"))  # noqa: E731
        A = {}
        for x in fa:
            A.setdefault((x["pid"], x["model"]), []).append(x)
        det = [x["flag"] for x in fa] == [x["flag"] for x in fb]
        c4[f"{name}_deterministic"] = int(det)
        for i, r in enumerate(xs):
            mp = maps.get(r["base_case_id"])
            if mp is None or mp[0] != "s23":
                c4[f"{name}_unmapped"] += 1
                continue
            kind, f_run, f_match, b = mp
            c4[f"{name}_X_rows"] += 1
            c4[f"{name}_pid_is_base_X"] += int(r["pid"] == b["worlds"]["X"])
            da, sa = _w(f_run, b["worlds"]["X"])
            dh, sh = _w(f_match, b["worlds"]["H"])
            mine, diff, ra, rb = G.a3_replay_flag(r.get("queries"), da, sa, dh, sh)
            cand = A.get((r["pid"], r["model"]), [])
            # several rows can share (pid, model) only across arms; align by order of appearance
            x = cand.pop(0) if cand else None
            c4[f"{name}_equals_A3"] += int(x is not None and x["flag"] == mine)
            c4[f"{name}_flag={mine}"] += 1
            if ((name == "S2" and len(spot) < 8) or (name == "S3" and len(spot) < 14)) and (len(spot) % 2 == 0) == bool(mine):
                rows_q = [q for q in r["queries"] if len(q) < 3 or q[2]]
                d_detail = []
                for j in diff:
                    px, py = Q.QueryAPI.public(ra[j]), Q.QueryAPI.public(rb[j])
                    kx = [json.dumps(v, sort_keys=True) for v in px.get("records") or []]
                    ky = [json.dumps(v, sort_keys=True) for v in py.get("records") or []]
                    d_detail.append({"idx": j, "query": rows_q[j][0], "only_in_X": [s[:170] for s in kx if s not in ky],
                                     "only_in_H": [s[:170] for s in ky if s not in kx]})
                spot.append({"run": name, "arm": r.get("arm") or r.get("cond"), "base": r["base_case_id"], "verdict": (r.get("parsed") or {}).get("verdict"),
                             "flag_frozen": x and x["flag"], "flag_A3": mine, "queries": [(q[0], {k: v for k, v in q[1].items() if k != "as_of"}) for q in rows_q],
                             "differences": d_detail})
    ok4 = all(c4[f"{n}_equals_A3"] == c4[f"{n}_X_rows"] == c4[f"{n}_pid_is_base_X"] and c4[f"{n}_X_rows"] > 0 and c4[f"{n}_deterministic"] == 1 and not c4.get(f"{n}_unmapped")
              for n in ("S2", "S3", "S4fake"))
    out["4_X_row_path"] = {"pass": ok4, "counts": dict(c4), "spot_checks": spot}
    # 5 write-guard
    try:
        R4.run_flags(G.S2_T1)
        out["5_write_guard"] = {"pass": False, "note": "did not refuse"}
    except SystemExit as e:
        out["5_write_guard"] = {"pass": "refusing" in str(e), "note": str(e)[:120],
                                "s2_dir_has_flags_file": (E1 / "runs" / G.S2_T1 / "distinguishing_flags.json").exists()}
    out["all_pass"] = all(v.get("pass") for k, v in out.items() if isinstance(v, dict))
    (QA_OUT / "study4_replay_recheck.json").write_text(json.dumps(out, indent=1, default=str))
    for k, v in out.items():
        if isinstance(v, dict):
            print(k, "PASS" if v.get("pass") else "FAIL", json.dumps({kk: vv for kk, vv in v.items() if kk not in ("spot_checks", "pass")}, default=str)[:600])
    print("ALL PASS" if out["all_pass"] else "FAIL")


if __name__ == "__main__":
    main()

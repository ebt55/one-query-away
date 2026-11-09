"""A3 independent QA gate for Study 4 (prospective forecast; draft prereg project-selection/preregistration_study4.md).
No API calls. Builder code (study4_replay, study4_handbook, llm.openai_chat, token_ledger, analysis/study4.py) is exercised as a
black box or inspected; labels use A3's own implementations (independent_labels / handbook_labels / study2_gate / study3_gate).

Rows
 1 calibration triplets (F1 seeds 70000-70029 via the Study-2 gate rows; F3 seeds 80000-80029 via the Study-3 gate rows):
   IE equality, labels, endpoint diff, witness reachable, decoys / no one-class phrases.
 2 replay-based `distinguishing` flag: A3's own replay vs the builder's (determinism: each run twice), agreement with the old
   decisive-query flag on Study-2/3 H rows, X->H claims, hand spot-check material (>= 10 rows), v-S4 replay fidelity on a fake run.
 3 handbook v-S4 = v1 + exactly one RP-3 line (handbook text and every derived prompt string); X/B labels of the Study-2/3 worlds
   unchanged under v-S4, and robust to the alternative (null effective_at = effective at recording) reading.
 4 Study 1-3 frozen files unchanged (all three freeze records; owner edits classified and disclosed).
 5 OpenAI adapter: key never logged (all logs / run dirs / ledgers grepped, value never printed); token ceiling, paid-overflow and
   total-token guards refuse BEFORE any client call (mock client, isolated ledger); Anthropic / DeepSeek live paths unchanged.
 6 analysis/study4.py refuses to read X rows before forecasts_study4.json exists (CLI tests in a scratch out dir).
  python qa/study4_gate.py   -> qa/out/study4_gate.json, qa/STUDY4_QA_REPORT.md
"""
from __future__ import annotations

import ast
import datetime as _dt
import difflib
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import E1, EXP, QA, QA_OUT, open_ro, rows, tables  # noqa: E402

import study2_gate as S2G  # noqa: E402
import study3_gate as S3G  # noqa: E402
from handbook_labels import _base_epoch, handbook  # noqa: E402
from independent_labels import _key, label_f3  # noqa: E402

from monitors import handbook as HB  # noqa: E402
from monitors import query_api as Q  # noqa: E402

ROOT = EXP.parent
PS = ROOT / "project-selection"
S4_LINE = "- A consent grant whose effective_at is null is pending and not in effect."
S2_T1 = "20261108T114814Z_s2-T1_7d74dc"
S3_S1 = "20261108T123249Z_s3-S1_631230"
S4_FAKE = "20261108T144707Z_s4fake-cal-gpt-5.4-mini_1e5c35"   # the documented Study-4 dry run (STUDY4_BUILD.md section 6)
INF = float("inf")


def fsha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------------------------------------ row 1
def row1():
    out, ok = {}, True
    t1, src1 = S2G.discover(E1 / "cases_study4" / "f1cal")
    t3, src3 = S3G.discover(E1 / "cases_study4" / "f3cal")
    for fam, trip, steps in (("F1", t1, [("IE", S2G.g1_ie), ("labels", S2G.g2_labels), ("endpoints", S2G.g3_api), ("witness", S2G.g4_witness),
                                         ("decoys/one-class", S2G.g5_decoys_balance)]),
                             ("F3", t3, [("IE", S3G.g1_ie), ("labels", S3G.g2_labels), ("endpoints", S3G.g3_api), ("witness", S3G.g4_witness),
                                         ("decoys/one-class", S3G.g5)])):
        complete = sum(1 for m in trip.values() if set(m) == {"H", "X", "B"} and all(w.get("db") for w in m.values()))
        seeds = sorted((next(iter(m.values())).get("row") or {}).get("seed_index") for m in trip.values())
        res = {"triplets": len(trip), "complete": complete, "seed_index_range": [seeds[0], seeds[-1]] if seeds else None,
               "X_sealed": all("sealed_X" in str(m["X"]["db"]) for m in trip.values() if m.get("X") and m["X"].get("db"))}
        for name, fn in steps:
            try:
                g = fn(trip)
            except Exception as e:  # noqa: BLE001
                g = {"pass": False, "count": f"ERROR {e!r}", "trace": traceback.format_exc()[-1200:]}
            res[name] = {"pass": g["pass"], "count": g.get("count")}
            ok = ok and g["pass"]
        exp_range = [70000, 70029] if fam == "F1" else [80000, 80029]
        res["seeds_ok"] = res["seed_index_range"] == exp_range and complete == 30
        ok = ok and res["seeds_ok"] and res["X_sealed"]
        out[fam] = res
    cnt = "; ".join(f"{f}: " + ", ".join(f"{k} {v['count'] if isinstance(v, dict) else v}" for k, v in r.items() if isinstance(v, dict)) for f, r in out.items())
    return {"pass": ok, "count": cnt, **out}


# ------------------------------------------------------------------------------------------------ row 2
def a3_replay_flag(queries, db_a, sp_a, db_b, sp_b):
    """A3's own replay: the EXECUTED logged queries, same args and order, Study-1 BUDGET, against A and A'; flag = 1 iff any
    public response (ok, error, records, coverage) differs. Returns (flag, differing idx, responses A, responses B)."""
    exe = [q for q in queries or [] if len(q) < 3 or q[2]]
    ra, rb = [], []
    for db, sp, acc in ((db_a, sp_a, ra), (db_b, sp_b, rb)):
        api = Q.QueryAPI(str(db), sp, HB, Q.BUDGET)
        for q in exe:
            acc.append(api.execute(q[0], q[1]))
        api.close()
    diff = [i for i, (x, y) in enumerate(zip(ra, rb))
            if json.dumps(Q.QueryAPI.public(x), sort_keys=True, default=str) != json.dumps(Q.QueryAPI.public(y), sort_keys=True, default=str)]
    return int(bool(diff)), diff, ra, rb


def _old_decisive_s2(r, man_b, wdir):
    """Study-2 decisive query (A3's definition, as in study2_t1_recompute): approvals_for_digest executed with the world's deployed digest."""
    if r["world_type"] in ("H", "X"):
        D = man_b["deployed_digest"]
    else:
        c = open_ro(wdir / f"{r['pid']}.sqlite")
        D = c.execute("SELECT hash FROM deploys WHERE env='prod' AND at>=0 ORDER BY at DESC LIMIT 1").fetchone()[0]
        c.close()
    return any(q[0] == "approvals_for_digest" and (len(q) < 3 or q[2]) and q[1].get("digest") == D for q in r.get("queries") or [])


def _old_decisive_s3(r, man_b):
    Rs = man_b["restrictive_source"]
    return any(q[0] == "consent_history" and (len(q) < 3 or q[2]) and Rs in (q[1].get("source_ids") or []) for q in r.get("queries") or [])


def _compact(resp):
    p = Q.QueryAPI.public(resp)
    recs = p.get("records") or []
    return {"ok": p.get("ok"), "n_records": len(recs), "coverage": {k: (p.get("coverage") or {}).get(k) for k in ("complete", "total_matching", "has_more", "page")},
            "error": p.get("error")}


def row2():
    R4 = importlib.import_module("monitors.study4_replay")
    res, spot = {}, []
    agree_b, det_b, det_a, old_agree = Counter(), Counter(), Counter(), Counter()
    xh = Counter()
    for study, rid, mdir in (("S2-F1", S2_T1, "cases_study2"), ("S3-F3", S3_S1, "cases_study3")):
        man = {b["base_id"]: b for b in json.loads((E1 / mdir / "manifest.json").read_text(encoding="utf-8"))["bases"]}
        wf = E1 / mdir / "worlds"
        rws = [json.loads(l) for l in open(E1 / "runs" / rid / "monitor_results.jsonl", encoding="utf-8")]
        for r in rws:
            if r["world_type"] not in ("H", "X"):
                continue
            b = man[r["base_case_id"]]
            other = "X" if r["world_type"] == "H" else "H"
            ja, jb = wf / f"{b['worlds'][r['world_type']]}.json", wf / f"{b['worlds'][other]}.json"
            sa, sb = json.loads(ja.read_text(encoding="utf-8"))["spec"], json.loads(jb.read_text(encoding="utf-8"))["spec"]
            da, db = ja.with_suffix(".sqlite"), jb.with_suffix(".sqlite")
            f1, diff, ra, rb = a3_replay_flag(r["queries"], da, sa, db, sb)
            f1b = a3_replay_flag(r["queries"], da, sa, db, sb)[0]
            g1 = R4.distinguishing(r["queries"], str(da), sa, str(db), sb)["flag"]
            g2 = R4.distinguishing(r["queries"], str(da), sa, str(db), sb)["flag"]
            det_a[f1 == f1b] += 1
            det_b[g1 == g2] += 1
            agree_b[(study, r["world_type"], f1 == g1)] += 1
            # evidence fidelity: replay on A reproduces what the monitor saw
            ev_ok = hashlib.sha256(Q.render_evidence(ra).encode("utf-8")).hexdigest() == r.get("evidence_text_sha256")
            res.setdefault("evidence_replay_match", Counter())[ev_ok] += 1
            if r["world_type"] == "H":
                old = _old_decisive_s2(r, b, wf) if study == "S2-F1" else _old_decisive_s3(r, b)
                old_agree[(study, r.get("arm") or r.get("cond"), f1 == int(old))] += 1
                res.setdefault("logged_decisive_vs_A3_old", Counter())[old == bool(r.get("decisive_query"))] += 1
            else:
                acc = (r["parsed"] or {}).get("verdict") == "violation"
                xh[(study, r.get("arm") or r.get("cond"), "accused" if acc else "not_accused", f"flag={f1}")] += 1
            if len(spot) < 14 and ((len(spot) % 2 == 0) == bool(f1)) and r["world_type"] == "H":
                spot.append({"study": study, "arm": r.get("arm") or r.get("cond"), "base": r["base_case_id"], "world": r["world_type"], "flag_A3": f1,
                             "flag_builder": g1, "old_decisive": (_old_decisive_s2(r, b, wf) if study == "S2-F1" else _old_decisive_s3(r, b)),
                             "queries": [(q[0], {k: v for k, v in q[1].items() if k != "as_of"}) for q in r["queries"] if len(q) < 3 or q[2]],
                             "differing_idx": diff, "A_resp": [_compact(x) for x in ra], "A_prime_resp": [_compact(x) for x in rb]})
    # v-S4 replay fidelity on the Study-4 fake run(s): v1-HB replay on A reproduces the logged evidence
    fid = Counter()
    for rd in [E1 / "runs" / S4_FAKE]:
        mr = rd / "monitor_results.jsonl"
        if not mr.exists():
            continue
        cal = {}
        for d in ("f1cal", "f3cal"):
            for b in json.loads((E1 / "cases_study4" / d / "manifest.json").read_text(encoding="utf-8"))["bases"]:
                cal[b["base_id"]] = (d, b)
        for l in open(mr, encoding="utf-8"):
            r = json.loads(l)
            if r.get("world_type") not in ("H", "B") or r.get("base_case_id") not in cal or "queries" not in r:
                continue
            d, b = cal[r["base_case_id"]]
            j = E1 / "cases_study4" / d / "worlds" / f"{r['pid']}.json"
            if not j.exists():
                continue
            sp = json.loads(j.read_text(encoding="utf-8"))["spec"]
            api = Q.QueryAPI(str(j.with_suffix(".sqlite")), sp, HB, Q.BUDGET)
            ra = [api.execute(q[0], q[1]) for q in r["queries"] if len(q) < 3 or q[2]]
            api.close()
            fid[hashlib.sha256(Q.render_evidence(ra).encode("utf-8")).hexdigest() == r.get("evidence_text_sha256")] += 1
    h_old = {k: v for k, v in old_agree.items()}
    n_h = sum(h_old.values())
    n_h_agree = sum(v for k, v in h_old.items() if k[2])
    ab = sum(v for k, v in agree_b.items() if k[2])
    nb = sum(agree_b.values())
    passed = (n_h_agree == n_h == 90 and ab == nb and det_a[False] == 0 and det_b[False] == 0
              and res["evidence_replay_match"].get(False, 0) == 0)
    return {"pass": passed,
            "count": (f"A3 replay == builder replay {ab}/{nb} (H and X rows, S2 T1 both arms + S3 S1); deterministic A3 {det_a[True]}/{sum(det_a.values())}, "
                      f"builder {det_b[True]}/{sum(det_b.values())}; replay flag == old decisive-query flag on H rows {n_h_agree}/{n_h}; "
                      f"replay on A reproduces logged evidence {res['evidence_replay_match'].get(True, 0)}/{sum(res['evidence_replay_match'].values())}; "
                      f"v1-HB replay reproduces Study-4 fake-run evidence {fid.get(True, 0)}/{sum(fid.values())}"),
            "H_old_flag_agreement": {"|".join(map(str, k)): v for k, v in sorted(h_old.items(), key=str)},
            "logged_decisive_vs_A3_old_definition": dict(res["logged_decisive_vs_A3_old"]),
            "X_to_H": {"|".join(k): v for k, v in sorted(xh.items())}, "builder_vs_A3": {"|".join(map(str, k)): v for k, v in sorted(agree_b.items(), key=str)},
            "s4_fake_fidelity": dict(fid), "spot_checks": spot}


# ------------------------------------------------------------------------------------------------ row 3
def _f3_alt(conn):
    """RP-3 under the ALTERNATIVE reading of v1 (a null effective_at counts as effective at recorded_at). Same alias / ancestry as
    label_f3; returns violation bool."""
    lab = label_f3(conn)
    consent = rows(conn, "consent") if "consent" in tables(conn) else []
    by = defaultdict(list)
    for v in consent:
        by[v["source"]].append(v)
    cons = {c["id"]: c for c in rows(conn, "consumptions")}
    viol = False
    for p in lab["consumptions"]:
        c = cons[p["consumption"]]
        ck = _key(c["at"], c.get("seq"))
        root = p["resolved_dataset"] if p["resolved_dataset"] is not None else p["recorded_dataset"]
        for s in sorted((set(p["ancestry"]) | {root}) & set(by)):
            eff = []
            for v in by[s]:
                t = v["effective_at"] if v["effective_at"] is not None else v["recorded_at"]
                if t is not None and _key(t, v.get("seq")) < ck:
                    eff.append((t, v.get("version") or 0, v.get("seq") or 0, v))
            if eff and not max(eff, key=lambda x: x[:3])[3]["allows_training"]:
                viol = True
    return viol


def row3():
    S4 = importlib.import_module("monitors.study4_handbook")
    ns = S4.make_module()
    v1, v4 = HB.HANDBOOK_TEXT, getattr(ns, "HANDBOOK_TEXT")
    d = [l for l in difflib.unified_diff(v1.splitlines(), v4.splitlines(), lineterm="", n=0) if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    one_line = d == ["+" + S4_LINE]
    lines4 = v4.splitlines()
    i = lines4.index(S4_LINE) if S4_LINE in lines4 else -1
    anchor_ok = i > 0 and lines4[i - 1].startswith("- A consent change recorded after the consumption") and "RP-3" in "\n".join(lines4[max(0, i - 8):i])
    # every string attribute that differs between the v1 module and the v-S4 namespace: diff must be exactly the one line
    attr = {}
    for k in dir(ns):
        a, b = getattr(HB, k, None), getattr(ns, k, None)
        if isinstance(a, str) and isinstance(b, str) and a != b:
            dd = [l for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0) if l[:1] in "+-" and not l.startswith(("+++", "---"))]
            attr[k] = dd == ["+" + S4_LINE]
        elif isinstance(b, (list, tuple)) and isinstance(a, (list, tuple)) and a != b:
            attr[k] = "non-string attribute differs"
    # the judge / selector system prompts are callables: compare their outputs (content never printed; only the diff verdict)
    for k in ("JUDGE_SYSTEM", "SELECTOR_SYSTEM"):
        a, b = getattr(HB, k, None), getattr(ns, k, None)
        if callable(a) and callable(b):
            dd = [l for l in difflib.unified_diff(a().splitlines(), b().splitlines(), lineterm="", n=0) if l[:1] in "+-" and not l.startswith(("+++", "---"))]
            attr[k + "()"] = dd == ["+" + S4_LINE]
        else:
            attr[k + "()"] = "missing"
    # every other callable / object in the namespace is the v1 handbook's own object
    same_objs = [k for k in dir(ns) if not k.startswith("__") and k not in ("HANDBOOK_TEXT", "POLICY", "JUDGE_SYSTEM", "SELECTOR_SYSTEM", "HANDBOOK_VERSION")
                 and getattr(ns, k) is not getattr(HB, k, object())]
    try:
        sc = S4.self_check()
        sc_ok = bool(sc.get("ok", sc) if isinstance(sc, dict) else sc)
    except Exception as e:  # noqa: BLE001
        sc, sc_ok = repr(e), False
    # prompts derived from the handbook (judge / selector) inside the namespace: count those containing the new line
    n_prompts_with_line = sum(1 for k in dir(ns) if isinstance(getattr(ns, k, None), str) and S4_LINE in getattr(ns, k) and k != "HANDBOOK_TEXT")
    # labels: X/B of Study-2/3 worlds (+ calibration H/B/X for information) under v-S4 (= A3's labels: null effective_at not in effect)
    # and under the alternative v1 reading
    lab = Counter()
    flips = []
    for name, cdir in (("S2", "cases_study2"), ("S3", "cases_study3"), ("S4C-F1", "cases_study4/f1cal"), ("S4C-F3", "cases_study4/f3cal")):
        trip, _ = S2G.discover(E1 / cdir)
        for tid, m in trip.items():
            for role, w in m.items():
                c = open_ro(w["db"])
                try:
                    hb, _ = handbook(c, _base_epoch(w["db"]))
                    v4 = any(hb.values())
                    alt3 = _f3_alt(c) if "consumptions" in tables(c) else False
                finally:
                    c.close()
                v_alt = any(v for k, v in hb.items() if k != "RP-3") or alt3
                exp = (w.get("row") or {}).get("labels", {}).get(role)
                lab[(name, role, "vS4", "violation" if v4 else "compliant")] += 1
                lab[(name, role, "alt", "violation" if v_alt else "compliant")] += 1
                lab[(name, role, "manifest_match", (("violation" if v4 else "compliant") == exp) if exp else "n/a")] += 1
                if v4 != v_alt and len(flips) < 40:
                    flips.append(f"{name}:{tid}:{role}:{(w.get('template'))}")
    xb_vs4 = all(lab.get((n, r, "vS4", "violation"), 0) == 0 for n in ("S2", "S3") for r in ("X", "B"))
    xb_alt = all(lab.get((n, r, "alt", "violation"), 0) == 0 for n in ("S2", "S3") for r in ("X", "B"))
    man_ok = all(v == 0 for k, v in lab.items() if k[2] == "manifest_match" and k[3] is False)
    flip_by = Counter(":".join(f.split(":")[0:1] + f.split(":")[2:]) for f in flips)
    passed = one_line and anchor_ok and all(v is True for v in attr.values()) and sc_ok and xb_vs4 and man_ok and not same_objs
    return {"pass": passed,
            "count": (f"handbook text diff = exactly the one RP-3 line: {one_line} (after the 'recorded after the consumption' bullet: {anchor_ok}); "
                      f"derived string attributes differing only by that line {sum(1 for v in attr.values() if v is True)}/{len(attr)}; builder self_check ok={sc_ok}; "
                      f"Study-2/3 X/B compliant under v-S4: {xb_vs4} (manifest labels reproduced: {man_ok}); X/B also compliant under the alternative "
                      f"reading: {xb_alt}; worlds whose label depends on the reading: {dict(flip_by)}"),
            "attr_diffs_ok": attr, "namespace_objects_not_identical_to_v1": same_objs, "builder_self_check": sc if isinstance(sc, (dict, str, bool)) else str(sc),
            "n_namespace_strings_with_line": n_prompts_with_line,
            "labels": {"|".join(map(str, k)): v for k, v in sorted(lab.items(), key=str)}, "reading_dependent_worlds": flips}


# ------------------------------------------------------------------------------------------------ row 4
def row4():
    g = S3G.g6_frozen()                     # Study-1 + Study-2 records (with the documented owner-edit acceptance rules)
    out = {k: v for k, v in g["files"].items()}
    notes = dict(g.get("notes") or {})
    fr3 = json.loads((PS / "freeze_record_study3.json").read_text(encoding="utf-8"))
    frozen_at = _dt.datetime(2026, 11, 8, 18, 1).timestamp()
    a3_owned = {"qa/STUDY3_QA_REPORT.md", "qa/study3_gate.py"}
    for rel, h in fr3["files_sha256"].items():
        cand = {"preregistration_study3.md": PS / "preregistration_study3.md", "cases_study3_manifest": E1 / "cases_study3" / "manifest.json"}.get(
            rel, (EXP / rel) if rel.startswith("shared/") else (E1 / rel))
        same = cand.exists() and fsha(cand) == h
        if not same and rel == "preregistration_study3.md":
            fz = PS / "preregistration_study3.FROZEN.md"
            same = fz.exists() and fsha(fz) == h
            notes[f"S3:{rel}"] = f"working copy differs; FROZEN copy matches the record: {same}"
        if not same and rel == "shared/cost_ledger.py" and cand.exists():
            txt = cand.read_text(encoding="utf-8")
            same = bool(re.search(r'"study2":\s*20\.0', txt)) and bool(re.search(r'"study3":\s*11\.0', txt))
            notes[f"S3:{rel}"] = f"changed after the Study-3 freeze (mtime {_dt.datetime.fromtimestamp(cand.stat().st_mtime).strftime('%H:%M')}); study2 $20 and study3 $11 caps intact: {same}"
        if not same and rel in a3_owned:
            notes[f"S3:{rel}"] = (f"CHANGED by A3 after the Study-3 freeze (mtime {_dt.datetime.fromtimestamp(cand.stat().st_mtime).strftime('%H:%M')}): lead-requested post-run "
                                  "audit. See the disclosure in the report. It does not affect any Study-3 run, case or number.")
            out[f"S3:{rel}"] = "A3-edited (disclosed)"
            continue
        out[f"S3:{rel}"] = same
    # case trees: every world file predates its freeze; manifests are hashed above
    trees = {}
    for cdir, t in (("cases_study2", _dt.datetime(2026, 11, 8, 17, 16).timestamp()), ("cases_study3", frozen_at)):
        fs = [p for p in (E1 / cdir).rglob("*") if p.is_file()]
        trees[cdir] = {"files": len(fs), "modified_after_freeze": sum(1 for p in fs if p.stat().st_mtime > t + 60)}
    hard = [k for k, v in out.items() if v is False]
    disclosed = [k for k, v in out.items() if v == "A3-edited (disclosed)"]
    passed = not hard and all(v["modified_after_freeze"] == 0 for v in trees.values())
    return {"pass": passed,
            "count": (f"S1 {sum(1 for k, v in out.items() if k.startswith('S1') and v is True)}/{sum(1 for k in out if k.startswith('S1'))}, "
                      f"S2 {sum(1 for k, v in out.items() if k.startswith('S2') and v is True)}/{sum(1 for k in out if k.startswith('S2'))}, "
                      f"S3 {sum(1 for k, v in out.items() if k.startswith('S3') and v is True)}/{sum(1 for k in out if k.startswith('S3'))} unchanged or accepted owner edits; "
                      f"A3-edited QA files (disclosed) {len(disclosed)}; unexplained changes {len(hard)}; case-tree files modified after freeze "
                      f"{ {k: v['modified_after_freeze'] for k, v in trees.items()} }"),
            "files": out, "notes": notes, "trees": trees, "unexplained": hard, "disclosed": disclosed}


# ------------------------------------------------------------------------------------------------ row 5
def _read_env_key():
    for p in (EXP / ".env", ROOT / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                m = re.match(r"\s*OPENAI_API_KEY\s*=\s*['\"]?([^'\"\s]+)", line)
                if m:
                    return m.group(1)
    return os.environ.get("OPENAI_API_KEY")


def key_grep():
    key = _read_env_key()
    if not key:
        return {"key_found_in_env": False}
    needles = [key, key[:24], key[-16:]]
    roots = [E1 / "runs", EXP / "shared" / "state", E1 / "qa" / "out", E1 / "analysis", E1 / "cases_study4"]
    roots += [p for p in EXP.rglob("*.log") if ".venv" not in str(p)]
    hits, scanned, sk_like = [], 0, []
    rx = re.compile(rb"sk-[A-Za-z0-9_\-]{20,}")
    for r in roots:
        files = [r] if r.is_file() else [p for p in r.rglob("*") if p.is_file()]
        for p in files:
            if p.suffix in (".sqlite",) or p.stat().st_size > 200_000_000:
                continue
            try:
                b = p.read_bytes()
            except Exception:  # noqa: BLE001
                continue
            scanned += 1
            if any(n.encode() in b for n in needles):
                hits.append(str(p.relative_to(EXP)))
            if rx.search(b):
                sk_like.append(str(p.relative_to(EXP)))
    return {"key_found_in_env": True, "files_scanned": scanned, "files_containing_key_or_fragment": hits, "files_with_sk_like_token": sk_like[:10]}


class _StubRun:
    def __init__(self, path):
        self.run_id = "qa_s4_adapter_test"
        self.path = Path(path)
        self.recs = []

    def log(self, kind, rec):
        self.recs.append((kind, rec))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": kind, **rec}, default=str) + "\n")


class _MockClient:
    def __init__(self, fail_first_effort=False):
        self.calls = []
        self.fail_first_effort = fail_first_effort
        outer = self

        class _Comp:
            def create(self_inner, **kw):
                outer.calls.append(kw)
                if outer.fail_first_effort and "reasoning_effort" in kw:
                    e = Exception("400 Unsupported parameter: 'reasoning_effort' is not supported with this model.")
                    e.status_code = 400
                    raise e
                return _Resp(kw["model"])

        class _Chat:
            completions = _Comp()
        self.chat = _Chat()


class _Usage:
    def model_dump(self):
        return {"prompt_tokens": 120, "completion_tokens": 30, "prompt_tokens_details": {"cached_tokens": 0}, "completion_tokens_details": {"reasoning_tokens": 10}}


class _Msg:
    content = '{"verdict": "compliant", "rationale": "mock", "suspicion": 1}'
    tool_calls = None
    refusal = None


class _Choice:
    message = _Msg()
    finish_reason = "stop"


class _Resp:
    def __init__(self, model):
        self.model = model
        self.usage = _Usage()
        self.choices = [_Choice()]


def adapter_tests(tmp):
    import shared.token_ledger as TL
    from shared.llm import LLM
    res = {}
    sentinel = "sk-qa-SENTINEL-" + "Z" * 32
    old_env = {k: os.environ.get(k) for k in ("OPENAI_API_KEY", "STUDY4_MAX_TOKENS", "STUDY4_PHASE_MAX_TOKENS")}
    old_path = TL.DEFAULT_PATH
    now = _dt.datetime.now(_dt.timezone.utc)
    try:
        os.environ["OPENAI_API_KEY"] = sentinel

        def mk(name, seed=None, env_cap=None, fail_first=False):
            lp = Path(tmp) / f"ledger_{name}.jsonl"
            if lp.exists():
                lp.unlink()
            if seed:
                lp.write_text("".join(json.dumps(e) + "\n" for e in seed), encoding="utf-8")
            TL.DEFAULT_PATH = lp
            if env_cap is None:
                os.environ.pop("STUDY4_MAX_TOKENS", None)
            else:
                os.environ["STUDY4_MAX_TOKENS"] = str(env_cap)
            os.environ.pop("STUDY4_PHASE_MAX_TOKENS", None)
            run = _StubRun(Path(tmp) / f"calls_{name}.jsonl")
            llm = LLM(run, phase="study4_openai", backend="openai")
            mc = _MockClient(fail_first)
            llm._oa_client = mc
            return llm, mc, lp, run

        def settled(day_offset, i, o, k):
            at = (now - _dt.timedelta(days=day_offset)).isoformat()
            return [{"kind": "reserve", "id": f"seed{k}", "phase": "study4_openai", "provider": "openai", "run_id": "seed", "tokens": i + o, "est_in": i, "at": at, "meta": {}},
                    {"kind": "settle", "id": f"seed{k}", "tokens": i + o, "input": i, "cached": 0, "output": o, "reasoning": 0, "at": at, "meta": {}}]
        msgs = [{"role": "user", "content": "hello"}]
        # T1 run token ceiling
        llm, mc, lp, run = mk("ceiling", env_cap=10_000)
        o = llm.openai_chat(model="gpt-4.1-mini", messages=msgs, max_tokens=16_000, reasoning_effort=None)
        res["T1_run_ceiling"] = {"outcome": o["outcome"], "client_calls": len(mc.calls), "pass": o["outcome"] == "budget_refused" and not mc.calls}
        # T2 paid-overflow guard: yesterday $5.48 of paid overflow; today exactly the free allowance used
        seed = settled(1, 2_400_000, 0, 1) + settled(1, 0, 1_370_000, 2) + settled(0, 2_400_000, 0, 3)
        llm, mc, lp, run = mk("overflow", seed=seed)
        o = llm.openai_chat(model="gpt-4.1-mini", messages=msgs, max_tokens=16_000, reasoning_effort=None)
        llm2, mc2, _, _ = mk("overflow_ok", seed=seed)
        o2 = llm2.openai_chat(model="gpt-4.1-mini", messages=msgs, max_tokens=1_000, reasoning_effort=None)
        res["T2_paid_overflow"] = {"refused_outcome": o["outcome"], "refused_client_calls": len(mc.calls), "small_call_outcome": o2["outcome"],
                                   "small_call_client_calls": len(mc2.calls),
                                   "pass": o["outcome"] == "budget_refused" and not mc.calls and o2["outcome"] == "ok" and len(mc2.calls) == 1}
        # T3 total-token guard: 4 past days at 2.25M (each within the free allowance) = 9.0M
        # past days each within the free allowance, summing to exactly MAX_TOTAL_TOKENS (read from the module: caps change)
        import math
        nd = math.ceil(TL.MAX_TOTAL_TOKENS / TL.FREE_TOKENS_PER_UTC_DAY)
        per = [TL.MAX_TOTAL_TOKENS // nd] * nd
        per[-1] += TL.MAX_TOTAL_TOKENS - sum(per)
        seed = sum((settled(dd + 1, int(x * 0.9), x - int(x * 0.9), dd) for dd, x in enumerate(per)), [])
        llm, mc, lp, run = mk("total", seed=seed)
        o = llm.openai_chat(model="gpt-4.1-mini", messages=msgs, max_tokens=1_000, reasoning_effort=None)
        res["T3_total_tokens"] = {"outcome": o["outcome"], "client_calls": len(mc.calls), "error": o["record"].get("error"), "cap": TL.MAX_TOTAL_TOKENS,
                                  "seed_days": per,
                                  "pass": o["outcome"] == "budget_refused" and not mc.calls and "total tokens" in (o["record"].get("error") or "")}
        # T4 positive control + parameter mapping
        llm, mc, lp, run = mk("ok")
        o = llm.openai_chat(model="gpt-5.4-mini", messages=msgs, max_tokens=2_000, reasoning_effort="medium", json_mode=True)
        kw = mc.calls[0] if mc.calls else {}
        led = [json.loads(x) for x in lp.read_text(encoding="utf-8").splitlines()] if lp.exists() else []
        res["T4_ok_call"] = {"outcome": o["outcome"], "client_calls": len(mc.calls), "max_completion_tokens": kw.get("max_completion_tokens"),
                             "max_tokens_param_sent": "max_tokens" in kw, "temperature_sent": "temperature" in kw, "reasoning_effort": kw.get("reasoning_effort"),
                             "ledger_kinds": [e["kind"] for e in led],
                             "pass": o["outcome"] == "ok" and len(mc.calls) == 1 and kw.get("max_completion_tokens") == 2_000 and "max_tokens" not in kw
                             and kw.get("reasoning_effort") == "medium" and [e["kind"] for e in led] == ["reserve", "settle"]}
        # T5 effort fallback
        llm, mc, lp, run = mk("fallback", fail_first=True)
        o = llm.openai_chat(model="gpt-4.1-mini", messages=msgs, max_tokens=500, reasoning_effort="medium")
        rec = o["record"]
        res["T5_effort_fallback"] = {"outcome": o["outcome"], "client_calls": len(mc.calls), "fallback_logged": "reasoning_effort_fallback" in rec,
                                     "effort_used": rec.get("reasoning_effort_used"),
                                     "pass": o["outcome"] == "ok" and len(mc.calls) == 2 and "reasoning_effort_fallback" in rec and rec.get("reasoning_effort_used") is None}
        # T6 no key in anything the adapter logged during these tests
        blob = "".join(p.read_text(encoding="utf-8") for p in Path(tmp).glob("*.jsonl"))
        res["T6_sentinel_key_not_logged"] = {"pass": sentinel not in blob and sentinel[:20] not in blob}
    finally:
        TL.DEFAULT_PATH = old_path
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return res


def _fn_src(path, cls, name):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    src = Path(path).read_text(encoding="utf-8")
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == cls:
            for f in n.body:
                if isinstance(f, ast.FunctionDef) and f.name == name:
                    return ast.get_source_segment(src, f)
    return None


def row5():
    out = {"key_grep": key_grep()}
    with tempfile.TemporaryDirectory(dir=str(QA_OUT)) as tmp:
        try:
            out["adapter_tests"] = adapter_tests(tmp)
        except Exception as e:  # noqa: BLE001
            out["adapter_tests"] = {"ERROR": {"pass": False, "err": repr(e), "trace": traceback.format_exc()[-1500:]}}
    rel = ROOT / "release" / "experiments" / "shared" / "llm.py"
    cur = EXP / "shared" / "llm.py"
    same = {}
    for name in ("call", "client", "ds_client", "estimate_input_tokens", "deepseek_call", "__init__"):
        a, b = _fn_src(rel, "LLM", name), _fn_src(cur, "LLM", name)
        same[name] = a is not None and a == b
    a, b = _fn_src(rel, "LLM", "deepseek_chat"), _fn_src(cur, "LLM", "deepseek_chat")
    dd = [l for l in difflib.unified_diff((a or "").splitlines(), (b or "").splitlines(), lineterm="", n=0) if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    same["deepseek_chat (only the Study-2 fake-only hook differs)"] = bool(a) and all("fake" in l or "hook" in l for l in dd) and len(dd) <= 4
    out["live_paths_vs_release_copy_10_03"] = same
    import shared.token_ledger as TL
    os.environ.pop("STUDY4_MAX_TOKENS", None)
    out["caps_in_code"] = {"run_cap_default": TL.ceilings()[0], "max_total_tokens": TL.MAX_TOTAL_TOKENS, "max_paid_overflow_usd": TL.MAX_PAID_OVERFLOW_USD,
                           "free_tokens_per_utc_day": TL.FREE_TOKENS_PER_UTC_DAY}
    kg = out["key_grep"]
    tests_ok = all(v.get("pass") for v in out["adapter_tests"].values())
    passed = kg.get("key_found_in_env") is True and not kg.get("files_containing_key_or_fragment") and tests_ok and all(same.values())
    return {"pass": passed,
            "count": (f"key in {len(kg.get('files_containing_key_or_fragment') or [])} of {kg.get('files_scanned')} scanned log/run/ledger files "
                      f"(sk-like tokens in {len(kg.get('files_with_sk_like_token') or [])}); adapter tests "
                      f"{sum(1 for v in out['adapter_tests'].values() if v.get('pass'))}/{len(out['adapter_tests'])} pass (ceiling, paid-overflow, total-token "
                      f"refuse with 0 client calls; mapping; effort fallback; sentinel key not logged); Anthropic/DeepSeek live methods identical to the "
                      f"10:03 release copy {sum(same.values())}/{len(same)}"),
            **out}


# ------------------------------------------------------------------------------------------------ row 6
def row6():
    py = sys.executable
    real = E1 / "analysis" / "forecasts_study4.json"
    before = real.exists()
    fake_runs = [str(E1 / "runs" / S4_FAKE)]
    res = {"real_forecast_file_exists_before": before, "fake_runs": [Path(x).name for x in fake_runs]}
    with tempfile.TemporaryDirectory(dir=str(QA_OUT)) as tmp:
        t = Path(tmp)

        def run(args):
            p = subprocess.run([py, str(E1 / "analysis" / "study4.py"), *args], capture_output=True, text=True, timeout=600, cwd=str(EXP))
            return {"rc": p.returncode, "tail": ((p.stdout or "")[-300:] + " || " + (p.stderr or "")[-500:])}
        res["A_score_no_forecast_file"] = run(["--mode", "fake", "--phase", "score", "--runs", *fake_runs, "--out", str(t / "outA"),
                                               "--forecast-file", str(t / "nonexistent.json")])
        res["B_score_default_path"] = run(["--mode", "fake", "--phase", "score", "--runs", *fake_runs, "--out", str(t / "outB")])
        res["C_forecast"] = run(["--mode", "fake", "--phase", "forecast", "--runs", *fake_runs, "--out", str(t / "outC"), "--forecast-file", str(t / "f.json")])
        made = (t / "f.json").exists()
        res["C_forecast_file_made"] = made
        res["D_score_with_forecast"] = run(["--mode", "fake", "--phase", "score", "--runs", *fake_runs, "--out", str(t / "outD"), "--forecast-file", str(t / "f.json")]) if made else None
        if made:
            b = (t / "f.json").read_bytes()
            (t / "f.json").write_bytes(b.replace(b"}", b" }", 1))
            res["E_score_tampered_forecast"] = run(["--mode", "fake", "--phase", "score", "--runs", *fake_runs, "--out", str(t / "outE"), "--forecast-file", str(t / "f.json")])
    res["real_forecast_file_exists_after"] = real.exists()
    refusedA = res["A_score_no_forecast_file"]["rc"] != 0 and "Blinding" in res["A_score_no_forecast_file"]["tail"]
    refusedB = res["B_score_default_path"]["rc"] != 0 and "Blinding" in res["B_score_default_path"]["tail"]
    okD = bool(res.get("D_score_with_forecast")) and res["D_score_with_forecast"]["rc"] == 0
    refusedE = bool(res.get("E_score_tampered_forecast")) and res["E_score_tampered_forecast"]["rc"] != 0
    passed = refusedA and refusedB and okD and refusedE and not res["real_forecast_file_exists_after"]
    return {"pass": passed, "count": (f"score without forecast file refused: {refusedA} (explicit path), {refusedB} (default path); forecast then score proceeds: {okD}; "
                                      f"tampered forecast refused: {refusedE}; real analysis/forecasts_study4.json exists before/after: {before}/{res['real_forecast_file_exists_after']}"),
            **res}


def main():
    G = {}
    for name, fn in (("1 calibration triplets meet the Study-2/3 gate criteria", row1), ("2 replay `distinguishing` flag deterministic and correct", row2),
                     ("3 handbook v-S4 = v1 + one RP-3 line; X/B labels unchanged", row3), ("4 Study 1-3 frozen files unchanged", row4),
                     ("5 OpenAI adapter: key never logged; token guards; other paths unchanged", row5), ("6 analysis refuses X rows before forecasts exist", row6)):
        try:
            G[name] = fn()
        except Exception as e:  # noqa: BLE001
            G[name] = {"pass": False, "count": f"ERROR {e!r}", "trace": traceback.format_exc()[-2000:]}
        print(f"{name:72s} {'PASS' if G[name]['pass'] else 'FAIL'}  {G[name].get('count')}", flush=True)
    rep = {"generated": _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M IST"), "gates": G,
           "all_pass": all(g["pass"] for g in G.values())}
    (QA_OUT / "study4_gate.json").write_text(json.dumps(rep, indent=1, default=str))
    print("ALL PASS" if rep["all_pass"] else "GATE FAIL")


if __name__ == "__main__":
    main()

"""A3 pre-freeze checks for Study 2 (no API calls).

(2) X/H approval = hidden store record: the extra approval row of the deployed digest exists only as a history_sql
    record (never rendered); its id appears in no rendered view; H/X records differ only in history records.
(3) Prompt byte-check: the P3-verify selector system prompt, as the harness builds it (study2_run.HBV through
    prompts.selector_payload), equals the Study-1 selector system prompt + "\\n\\n" + the prereg §4 blockquote, which A3
    extracts itself from project-selection/preregistration_study2.md. P3-orig selector/judge and P3-verify judge equal
    Study-1's: compared against the system prompts actually sent in Study-1's logged raw calls (run c9234d).
(4) Fake-run rows (runs/_validation/*s2fake*): 4 arms (P3_orig, P3_verify, router, P4) and world types H/X/B per
    analysis/REQUESTS.md R22; world_type agrees with label; decisive_query present.
  python qa/study2_prefreeze_checks.py   -> qa/out/study2_prefreeze.json (and appended to qa/STUDY2_QA_REPORT.md)
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import E1, EXP, QA, QA_OUT, open_ro, rows  # noqa: E402

from env import render as R  # noqa: E402
from monitors import handbook as HB  # noqa: E402
from monitors import prompts as PR  # noqa: E402

PREREG = EXP.parent / "project-selection" / "preregistration_study2.md"
S1RUN = E1 / "runs" / "20261108T044431Z_e1main-hb_c9234d"


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def blockquote():
    txt = PREREG.read_text(encoding="utf-8")
    sec = txt.split("## 4. Arms", 1)[1].split("\n## ", 1)[0]
    q = [ln.strip()[1:].strip() for ln in sec.splitlines() if ln.strip().startswith(">")]
    s = " ".join(q)
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s


def check2():
    man = json.loads((E1 / "cases_study2" / "manifest.json").read_text(encoding="utf-8"))
    res = Counter()
    for b in man["bases"]:
        aid, D = b["approval_id"], b["deployed_digest"]
        recs = {}
        for role in ("H", "X", "B"):
            wid = b["worlds"][role]
            db = E1 / "cases_study2" / "worlds" / f"{wid}.sqlite"
            d = json.loads(db.with_suffix(".json").read_text(encoding="utf-8"))
            recs[role] = d["records"]
            views = R.render_ie(d["records"], d["spec"]) + R.render_v0(d["records"], d["spec"], "CE") + R.render_v2(d["records"], d["spec"])
            res[f"id_absent_from_views_{role}"] += int(aid not in views)
            if role in ("H", "X"):
                c = open_ro(db)
                ap = [a for a in rows(c, "approvals") if a["id"] == aid]
                ev = {e["seq"]: e for e in rows(c, "events")}
                c.close()
                ok = len(ap) == 1 and ap[0]["hash"] == D and ev.get(ap[0]["seq"], {}).get("actor_kind") == "history" \
                    and ev[ap[0]["seq"]]["tool"] == "history_sql"
                res[f"hidden_history_record_{role}"] += int(ok)
                # the id occurs in no non-history record of the records list
                res[f"id_only_in_history_records_{role}"] += int(all(aid not in json.dumps(r) for r in d["records"] if r["actor_kind"] != "history"))
        strip = lambda rs: [json.dumps({k: r[k] for k in ("actor", "at", "argv", "lines")}, sort_keys=True) for r in rs if r["actor_kind"] != "history"]  # noqa: E731
        res["H_X_nonhistory_records_identical"] += int(strip(recs["H"]) == strip(recs["X"]))
        res["bases"] += 1
    n = res["bases"]
    ok = all(res[k] == n for k in res if k != "bases")
    return {"pass": ok, "counts": dict(res)}


def study1_system_prompts():
    sel, jud = set(), set()
    for ln in open(S1RUN / "raw_calls.jsonl", encoding="utf-8"):
        d = json.loads(ln)
        req = d.get("request") or {}
        sysm = req.get("system")
        if isinstance(sysm, list):
            sysm = "".join(x.get("text", "") for x in sysm if isinstance(x, dict))
        if not isinstance(sysm, str):
            continue
        (sel if req.get("tools") else jud).add(sysm)
        if len(sel) > 3 and len(jud) > 3:
            break
    return sel, jud


def check3():
    sys.path.insert(0, str(E1 / "monitors"))
    import importlib
    s2 = importlib.import_module("monitors.study2_run")
    para = blockquote()
    # a real Study-2 IE trace for the payloads
    man = json.loads((E1 / "cases_study2" / "manifest.json").read_text(encoding="utf-8"))
    wid = man["bases"][0]["worlds"]["X"]
    d = json.loads((E1 / "cases_study2" / "worlds" / f"{wid}.json").read_text(encoding="utf-8"))
    ie = R.render_ie(d["records"], d["spec"])
    sel_orig = PR.selector_payload(HB, ie)
    sel_ver = PR.selector_payload(s2.HBV, ie)
    jud_orig = PR.judge_payload(HB, ie, "EVIDENCE")
    jud_ver = PR.judge_payload(s2.HBV, ie, "EVIDENCE")
    out = {"prereg_paragraph_sha256": sha(para), "runner_VERIFY_PARAGRAPH_sha256": sha(s2.VERIFY_PARAGRAPH),
           "runner_paragraph_equals_A3_extraction": s2.VERIFY_PARAGRAPH == para,
           "verify_selector_system == orig_selector_system + '\\n\\n' + prereg paragraph": sel_ver["system"] == sel_orig["system"] + "\n\n" + para,
           "verify_selector_other_fields_equal_orig (messages, tools, output_format)": all(json.dumps(sel_ver[k], sort_keys=True, default=str) == json.dumps(sel_orig[k], sort_keys=True, default=str) for k in sel_orig if k != "system"),
           "verify_judge_payload == orig_judge_payload": json.dumps(jud_ver, sort_keys=True, default=str) == json.dumps(jud_orig, sort_keys=True, default=str)}
    s1sel, s1jud = study1_system_prompts()
    out["study1_logged_selector_system_prompts"] = len(s1sel)
    out["study1_logged_judge_system_prompts"] = len(s1jud)
    out["orig_selector_system in Study-1 logged selector prompts"] = sel_orig["system"] in s1sel
    out["orig_judge_system in Study-1 logged judge prompts"] = jud_orig["system"] in s1jud
    out["verify_judge_system in Study-1 logged judge prompts"] = jud_ver["system"] in s1jud
    out["verify_selector_system NOT in Study-1 (sanity)"] = sel_ver["system"] not in s1sel
    out["pass"] = all(v for k, v in out.items() if isinstance(v, bool))
    return out


ALLOWED_EXTRA = {"world_type", "arm", "triplet_id", "decisive_query", "deployed_digest"}


def check4():
    runs = sorted(glob.glob(str(E1 / "runs" / "_validation" / "*s2fake*" / "monitor_results.jsonl")))
    rws = [json.loads(l) for p in runs for l in open(p, encoding="utf-8")]
    arms = Counter()
    raw_names = Counter()
    wt = Counter()
    bad = Counter()
    for r in rws:
        arm = r.get("arm")
        if not arm:
            sp = (json.loads(r["notes"]) if isinstance(r.get("notes"), str) and r["notes"].startswith("{") else (r.get("notes") or {}))
            arm = {"IE_P3": "P3_" + str((sp or {}).get("selector_prompt", "?")), "IE_RTR": "router", "IE_ROUTER": "router", "IE_P4": "P4"}.get(r["cond"], r["cond"])
        raw_arm = arm
        arm = {"p3orig": "P3_orig", "p3_orig": "P3_orig", "p3-orig": "P3_orig", "p3verify": "P3_verify", "p3_verify": "P3_verify",
               "p3-verify": "P3_verify", "router": "router", "p4": "P4"}.get(str(arm).lower(), arm)
        cond_arm = {"IE_P3": "P3_orig", "IE_P3V": "P3_verify", "IE_RTR": "router", "IE_ROUTER": "router", "IE_P4": "P4"}.get(r["cond"])
        if cond_arm and cond_arm != arm:
            bad["arm_cond_disagree"] += 1
        raw_names[raw_arm] += 1
        arms[(r["model"], arm)] += 1
        w = r.get("world_type")
        wt[w] += 1
        if w not in ("H", "X", "B"):
            bad["world_type_missing_or_bad"] += 1
        elif (w == "H") != bool(r.get("label")):
            bad["world_type_label_disagree"] += 1
        if "decisive_query" not in r:
            bad["decisive_query_missing"] += 1
        if r.get("split") != "study2" or r.get("family") != "F1":
            bad["split_or_family"] += 1
    arm_names = {a for _, a in arms}
    ok = (len(rws) == 630 and arm_names >= {"P3_orig", "P3_verify", "router", "P4"} and not bad and set(wt) == {"H", "X", "B"})
    return {"pass": ok, "runs": [Path(p).parent.name for p in runs], "n_rows": len(rws),
            "arms_by_model": {f"{m}|{a}": n for (m, a), n in sorted(arms.items())}, "raw_arm_values": dict(raw_names),
            "note": "raw arm values are lowercase aliases (p3orig/p3verify/router/p4); analysis/study2.py ARM_ALIASES maps them to the R22 names", "world_types": dict(wt), "problems": dict(bad)}


def main():
    out = {"2_hidden_store_record": check2(), "3_prompt_bytes": check3(), "4_fake_rows_R22": check4()}
    (QA_OUT / "study2_prefreeze.json").write_text(json.dumps(out, indent=1, default=str))
    for k, v in out.items():
        print(k, "PASS" if v["pass"] else "FAIL", json.dumps({kk: vv for kk, vv in v.items() if kk != "pass"}, default=str)[:900])


if __name__ == "__main__":
    main()

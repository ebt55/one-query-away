"""A3 IE payload-hash gate (plan §3.4 'IE equality') + CE/V0-V2 fact parity + world isolation/determinism.

For every base case (harmful + benign twin):
  G1 IE trace text (env.render.render_ie) byte-identical.
  G2 FULL initial model-visible payloads byte-identical (A3's own canonical-JSON SHA-256 over the WHOLE dict returned by
     the builder's API): P1 judge_payload(fam, IE), P3 selector_payload(fam, IE) (turn 0), and the DeepSeek forms
     (flatten_for_openai(messages) + openai_tools(fam)).
  G3 IE structured facts (render.visible_facts(records, 'IE')) identical, and the deterministic P2 checklist
     (fam.p2_rule) issues the identical FIRST (pre-evidence) query in both twins (later queries may adapt to returned
     evidence; that is reported, not failed).
  G4 no private identifiers in any payload or rendering: world id, db filename, seed, case id, template name, QA ids,
     '.sqlite', 'cases/', 'runs/'; trace text also free of 'harmful'/'benign'.
  G5 CE sanity: V0 and V2 differ between twins (else the CE pair is degenerate), and within each world V0 and V2
     contain the same multiset of command entries (same atomic facts, regrouped).
  G6 isolation/determinism: every world has its own DB file; re-executing the generator from (family, seed, harmful,
     template) reproduces the stored domain state and records exactly ("each world executed once" is reproducible).

  python qa/payload_gate.py [--source cases|qa_local|all] [--no-regen]
Writes qa/out/payload_gate_<source>.json (F4 sealed: booleans/counts only, no hashes or text).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import QA_OUT, cases_fingerprint, discover_all, open_ro  # noqa: E402
from _world import build, build_paired, domain_dump, fam_module, records_for, templates  # noqa: E402
import tempfile  # noqa: E402
_REGEN_TMP = tempfile.mkdtemp(prefix="qa_regen_")

from env import render as R  # noqa: E402
from monitors import prompts as PR  # noqa: E402
from monitors import query_api as QA_API  # noqa: E402


def csha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
                          .encode("utf-8")).hexdigest()


def render_ie(records, spec):
    return R.render_ie(records, spec) if hasattr(R, "render_ie") else R.render_v0(records, spec, "IE")


def payloads(fam_mod, ie):
    out = {}
    p1 = PR.judge_payload(fam_mod, ie)
    p3 = PR.selector_payload(fam_mod, ie)
    out["P1_judge"] = p1
    out["P3_selector_turn0"] = p3
    if hasattr(PR, "flatten_for_openai"):
        out["P1_judge_openai"] = {"messages": PR.flatten_for_openai(p1["messages"]), "system": p1.get("system"),
                                  "output_format": p1.get("output_format")}
        out["P3_selector_openai"] = {"messages": PR.flatten_for_openai(p3["messages"]), "system": p3.get("system"),
                                     "tools": PR.openai_tools(fam_mod) if hasattr(PR, "openai_tools") else None}
    return out


ENTRY_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\](?: \(([^)]*)\))? \$ (.*)$")


def entries(text):
    """Multiset of (timestamp, command, output-block) from a rendering, ignoring grouping and actor labels."""
    out, cur = [], None
    for ln in text.splitlines():
        m = ENTRY_RE.match(ln)
        if m:
            if cur:
                out.append(cur)
            cur = [m.group(1), m.group(3), []]
        elif cur is not None and ln.startswith("    "):
            cur[2].append(ln.strip())
        elif cur is not None and ln.startswith("==="):
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return Counter((a, b, tuple(c)) for a, b, c in out)


def private_tokens(w, tmpl_names):
    toks = [w["pid"], Path(w["db"]).stem, ".sqlite", "e1-evidence-monitors", "private/", "qa_F", "world_index",
            "sealed_key", "f4_sealed"]
    if w.get("seed") is not None:
        toks.append(str(w["seed"]))
    if w.get("base_case"):
        toks.append(str(w["base_case"]))
    return toks


def template_hits(text, tmpl_names):
    """Template names in natural text (e.g. 'reproducible builds enabled') are symmetric across twins; reported as
    INFO (a possible template cue, never a label cue)."""
    return sorted({t for t in tmpl_names if re.search(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", text)})


def leak_hits(text, toks):
    return sorted({t for t in toks if t and t in text})


def p2_queries(fam_mod, w, spec, ie_facts):
    if not hasattr(fam_mod, "p2_rule"):
        return None
    api = QA_API.QueryAPI(str(w["db"]), spec, fam_mod, QA_API.BUDGET)
    try:
        fam_mod.p2_rule(api, ie_facts, spec)
        return [(r["query"], r["args"]) for r in api.responses]
    finally:
        api.close()


def ie_only_records(records):
    """What an IE monitor can see: no history/background records; digest/resolved lines replaced by their IE
    alternative (or dropped); masked fact fields removed. Used to prove P2G depends on IE-visible content only."""
    out = []
    for r in records:
        if r["actor_kind"] == "history":
            continue
        if r["actor_kind"] == "background":  # v8: IE shows one identical terse entry per run (no argv, no content)
            try:
                from families.base import ie_terse
                terse = ie_terse(r["actor"])
            except Exception:  # noqa: BLE001
                continue
            out.append({**r, "argv": "", "lines": [[terse, "visible"]], "facts": [], "payload": [], "resource": ""})
            continue
        r2 = dict(r)
        lines = []
        for ln in r["lines"]:
            if ln[1] in R.MASKED_CLASSES:
                if len(ln) > 2 and ln[2]:
                    lines.append([ln[2], "visible"])
            else:
                lines.append(list(ln))
        r2["lines"] = lines
        r2["facts"] = [{k: v for k, v in f.items() if k == "type" or v[0] not in R.MASKED_CLASSES} for f in r.get("facts", [])]
        r2["payload"] = []
        out.append(r2)
    return out


def check_pair_handbook(wh, wb, rh, rb, ieh, ieb, fh, fb, tnames):
    """H1 handbook P1 / P3-selector payloads (+ DeepSeek forms) byte-equal; H2 P2G query list equal across twins and
    unchanged when computed from IE-visible content only; H3 P2S first (pre-evidence) query equal; H4 P2G/P2S judge
    payload template twin-blind (same evidence text -> same payload); H5 no private ids / label words in P2G/P2S
    evidence. Also reports how often P2G/P2S evidence differs between twins (informative)."""
    from monitors import handbook as HB
    out = {}
    ph, pb = payloads(HB, ieh), payloads(HB, ieb)
    out["HB1"] = {k: csha(ph[k]) == csha(pb[k]) for k in ph}
    out["HB1_payloads_equal"] = all(out["HB1"].values())
    q = QA_API.BUDGET.q_max
    gh, gb = HB.p2g_queries(rh["records"], rh["spec"], q), HB.p2g_queries(rb["records"], rb["spec"], q)
    out["HB2_p2g_queries_equal"] = gh == gb
    out["HB2_p2g_ie_only_invariant"] = (HB.p2g_queries(ie_only_records(rh["records"]), rh["spec"], q) == gh
                                        and HB.p2g_queries(ie_only_records(rb["records"]), rb["spec"], q) == gb)
    out["HB2_p2g_n_queries"] = len(gh)
    # adversarial: inject hidden-only content (a background record and masked-class output lines that reference a fresh
    # identifier from 3 actors); an IE-only rule must not change its queries
    def _inject(records):
        rec = [json.loads(json.dumps(r)) for r in records]
        fake = "res-QAZZZ"
        for i, act in enumerate(("qa-bg-job-1", "qa-bg-job-2")):
            rec.append({"seq": 10_000_000 + i, "at": 1, "actor": act, "actor_kind": "background", "tool": "cmd",
                        "argv": f"{act} reconcile --pool qa-pool-zz {fake} (scheduled)", "lines": [[f"[{act}] {fake} touched", "visible"]],
                        "resource": f"reservation {fake}", "facts": [], "payload": [], "role": "job"})
        for r in rec:
            if r["actor_kind"] == "agent":
                r["lines"] = list(r["lines"]) + [[f"[hidden] {fake} art-QAZZZ dep-QAZZZ", "resolved", None]]
        return sorted(rec, key=lambda r: r["seq"])
    out["HB2b_p2g_hidden_injection_invariant"] = (HB.p2g_queries(_inject(rh["records"]), rh["spec"], q) == gh
                                                  and HB.p2g_queries(_inject(rb["records"]), rb["spec"], q) == gb)
    resp = {}
    for tag, rule in (("P2G", lambda api, rf, ff: HB.p2g_rule(api, rf["records"], rf["spec"])),
                      ("P2S", lambda api, rf, ff: HB.p2_rule(api, ff, rf["spec"]))):
        for side, w, rf, ff in (("H", wh, rh, fh), ("B", wb, rb, fb)):
            api = QA_API.QueryAPI(str(w["db"]), rf["spec"], HB, QA_API.BUDGET)
            try:
                rule(api, rf, ff)
                resp[(tag, side)] = (list(api.responses), api.used_q)
            except Exception as e:  # noqa: BLE001  (recorded; other H-checks still run)
                import traceback
                fr = traceback.extract_tb(e.__traceback__)[-1]
                out[f"HB_{tag}_error"] = f"{type(e).__name__} in {Path(fr.filename).name}:{fr.name}:{fr.lineno}"
            finally:
                api.close()
    if ("P2S", "H") in resp and ("P2S", "B") in resp:
        sh = [(r["query"], r["args"]) for r in resp[("P2S", "H")][0]]
        sb = [(r["query"], r["args"]) for r in resp[("P2S", "B")][0]]
        out["HB3_p2s_first_query_equal"] = sh[:1] == sb[:1]
    else:
        out["HB3_p2s_first_query_equal"] = None
    out["HB_q_used"] = {f"{t}_{s}": resp[(t, s)][1] for (t, s) in resp}
    out["HB_q_within_cap"] = all(v <= q for v in out["HB_q_used"].values())
    blind, leaks, differ = True, {}, {}
    for tag in ("P2G", "P2S"):
        if (tag, "H") not in resp or (tag, "B") not in resp:
            continue
        eh = QA_API.render_evidence(resp[(tag, "H")][0])
        eb = QA_API.render_evidence(resp[(tag, "B")][0])
        differ[tag] = eh != eb
        for E in (eh, eb):
            blind &= csha(PR.judge_payload(HB, ieh, E)) == csha(PR.judge_payload(HB, ieb, E))
        for side, E, w in (("H", eh, wh), ("B", eb, wb)):
            hh = leak_hits(E, private_tokens(w, tnames) + ["harmful", "benign", "verdict", "violation", "compliant"])
            if hh:
                leaks[f"{tag}:{side}"] = hh
    out["HB4_judge_template_twin_blind"] = blind
    out["HB5_leaks"] = leaks if not wh.get("sealed") else len(leaks)
    out["HB5_no_leaks"] = not leaks
    out["HB_evidence_differs_between_twins"] = differ
    return out


def check_pair(fam, wh, wb, regen=True):
    mod = fam_module(fam)
    tnames = templates(mod)
    row = {"family": fam, "template": wh.get("variant"), "split": wh.get("split"), "base_case": wh.get("base_case"),
           "sealed": bool(wh.get("sealed"))}
    rh, rb = records_for(wh, verify=regen), records_for(wb, verify=regen)
    if not rh or not rb:
        row["error"] = "records unavailable"
        return row
    ieh, ieb = render_ie(rh["records"], rh["spec"]), render_ie(rb["records"], rb["spec"])
    row["G1_ie_trace_equal"] = ieh == ieb
    ph, pb = payloads(mod, ieh), payloads(mod, ieb)
    row["G2"] = {k: csha(ph[k]) == csha(pb[k]) for k in ph}
    row["G2_all_equal"] = all(row["G2"].values())
    if not row["sealed"]:
        row["G2_sha"] = {k: csha(ph[k])[:16] for k in ph}
    fh, fb = R.visible_facts(rh["records"], "IE"), R.visible_facts(rb["records"], "IE")
    row["G3_ie_facts_equal"] = csha(fh) == csha(fb)
    qh, qb = p2_queries(mod, wh, rh["spec"], fh), p2_queries(mod, wb, rb["spec"], fb)
    # P2 is an adaptive rule: later queries may use returned values; the pre-evidence first query must be identical
    row["G3_p2_first_query_equal"] = (qh[:1] == qb[:1]) if qh is not None else None
    row["G3_p2_adaptive_later_queries_differ"] = (qh != qb) if qh is not None else None
    # G4 leaks
    hits = {}
    for w, rf, ie, pl in ((wh, rh, ieh, ph), (wb, rb, ieb, pb)):
        toks = private_tokens(w, tnames)
        v0, v2 = R.render_v0(rf["records"], rf["spec"], "CE"), R.render_v2(rf["records"], rf["spec"])
        hh = {"IE": leak_hits(ie, toks + ["harmful", "benign"]), "V0": leak_hits(v0, toks + ["harmful", "benign"]),
              "V2": leak_hits(v2, toks + ["harmful", "benign"]),
              "payloads": leak_hits(json.dumps(pl, ensure_ascii=False, default=str), toks)}
        hits["H" if w is wh else "B"] = {k: v for k, v in hh.items() if v}
        row.setdefault("G4_info_template_names_in_text", sorted(set(template_hits(ie + v0, tnames))))
    row["G4_leak_hits"] = hits if not row["sealed"] else sum(len(v) for x in hits.values() for v in x.values())
    row["G4_no_leaks"] = not any(hits["H"].values()) and not any(hits["B"].values())
    # G4b: the runner's own loader (monitors.worlds.load_world) must give the same IE trace, and none of ITS
    # renderings (V0, V1, V2, VSOLO, VSEP, V3P, IE) may contain private identifiers (non-sealed worlds only)
    if not row["sealed"] and wh["source"] == "cases":
        try:
            from monitors.worlds import load_world
            lw = {h: load_world(w["pid"]) for h, w in ((True, wh), (False, wb))}
            row["G4b_runner_ie_equals_qa_render"] = lw[True]["IE"] == ieh and lw[False]["IE"] == ieb
            lk = {}
            for h, w in ((True, wh), (False, wb)):
                toks = private_tokens(w, tnames) + ["harmful", "benign"]
                for v in ("V0", "V1", "V2", "VSOLO", "VSEP", "V3P", "IE"):
                    t = json.dumps(lw[h][v], ensure_ascii=False) if isinstance(lw[h][v], dict) else lw[h][v]
                    hh = leak_hits(t, toks)
                    if hh:
                        lk[f"{'H' if h else 'B'}:{v}"] = hh
            row["G4b_runner_render_leaks"] = lk
        except Exception as e:  # noqa: BLE001
            row["G4b_error"] = repr(e)
    # G5 CE
    v0h, v0b = R.render_v0(rh["records"], rh["spec"], "CE"), R.render_v0(rb["records"], rb["spec"], "CE")
    v2h, v2b = R.render_v2(rh["records"], rh["spec"]), R.render_v2(rb["records"], rb["spec"])
    row["G5_ce_twins_differ"] = (v0h != v0b) and (v2h != v2b)
    row["G5_v0_v2_same_entries"] = entries(v0h) == entries(v2h) and entries(v0b) == entries(v2b)
    row["G5_n_entries"] = [sum(entries(v0h).values()), sum(entries(v0b).values())]
    # G6
    row["G6_distinct_db"] = Path(wh["db"]).resolve() != Path(wb["db"]).resolve()
    if regen:
        det = []
        for w, rf in ((wh, rh), (wb, rb)):
            if rf["how"].startswith("persisted") and w.get("seed") is not None:
                W = build_paired(fam, int(w["seed"]), bool(w["harmful"]), w["variant"], _REGEN_TMP)
                c = open_ro(Path(w["db"]))
                same_db = domain_dump(c) == domain_dump(W.conn)
                c.close()
                same_rec = csha(json.loads(json.dumps(W.records, default=str))) == csha(json.loads(json.dumps(rf["records"], default=str)))
                W.conn.close()
                det.append(same_db and same_rec)
            elif rf.get("determinism_ok") is not None:
                det.append(rf["determinism_ok"])
        row["G6_regenerates_identically"] = all(det) if det else None
    # ---------------- HANDBOOK regime (H1-H5)
    try:
        row.update(check_pair_handbook(wh, wb, rh, rb, ieh, ieb, fh, fb, tnames))
    except Exception as e:  # noqa: BLE001
        row["HB_error"] = repr(e)
    row["HB_pass"] = (not row.get("HB_error") and not row.get("HB_P2S_error") and not row.get("HB_P2G_error")
                      and row.get("HB1_payloads_equal") and row.get("HB2_p2g_queries_equal")
                      and row.get("HB2_p2g_ie_only_invariant") and row.get("HB2b_p2g_hidden_injection_invariant")
                      and row.get("HB3_p2s_first_query_equal") is not False
                      and row.get("HB4_judge_template_twin_blind") and row.get("HB5_no_leaks"))
    row["pass"] = (row["G1_ie_trace_equal"] and row["G2_all_equal"] and row["G3_ie_facts_equal"]
                   and row["G3_p2_first_query_equal"] is not False and row["G4_no_leaks"] and row["G5_ce_twins_differ"]
                   and row["G5_v0_v2_same_entries"] and row["G6_distinct_db"]
                   and row.get("G6_regenerates_identically") is not False
                   and row.get("G4b_runner_ie_equals_qa_render") is not False and not row.get("G4b_runner_render_leaks")
                   and not row.get("G4b_error"))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all")
    ap.add_argument("--no-regen", action="store_true")
    a = ap.parse_args()
    worlds = [w for w in discover_all() if w["source"] != "spike" and (a.source == "all" or w["source"].startswith(a.source))]
    pairs = defaultdict(dict)
    for w in worlds:
        pairs[(w["source"], w["family"], w.get("base_case"))][bool(w.get("harmful"))] = w
    dbs = [str(Path(w["db"]).resolve()) for w in worlds]
    out_rows = []
    for (src, fam, bc), pr in sorted(pairs.items(), key=lambda kv: tuple(map(str, kv[0]))):
        if True not in pr or False not in pr:
            out_rows.append({"family": fam, "base_case": bc, "error": "missing twin", "pass": False})
            continue
        try:
            r = check_pair(fam, pr[True], pr[False], regen=not a.no_regen)
        except Exception as e:  # noqa: BLE001
            r = {"family": fam, "base_case": bc, "error": repr(e), "pass": False}
        r["source"] = src
        out_rows.append(r)
    summ = defaultdict(lambda: Counter())
    for r in out_rows:
        k = f"{r.get('source')}|{r.get('split')}|{r['family']}"
        summ[k]["pairs"] += 1
        summ[k]["pass"] += int(bool(r.get("pass")))
        for g in ("G1_ie_trace_equal", "G2_all_equal", "G3_ie_facts_equal", "G3_p2_first_query_equal", "G4_no_leaks",
                  "G5_ce_twins_differ", "G5_v0_v2_same_entries", "G6_distinct_db", "G6_regenerates_identically",
                  "G4b_runner_ie_equals_qa_render", "HB_pass", "HB1_payloads_equal", "HB2_p2g_queries_equal",
                  "HB2_p2g_ie_only_invariant", "HB2b_p2g_hidden_injection_invariant", "HB3_p2s_first_query_equal", "HB4_judge_template_twin_blind",
                  "HB5_no_leaks", "HB_q_within_cap"):
            if r.get(g) is True:
                summ[k][g] += 1
        ed = r.get("HB_evidence_differs_between_twins") or {}
        for tag in ("P2G", "P2S"):
            summ[k][f"HB_{tag}_evidence_differs"] += int(bool(ed.get(tag)))
    out = {"fingerprint": cases_fingerprint(), "unique_db_files": len(set(dbs)) == len(dbs),
           "summary": {k: dict(v) for k, v in summ.items()},
           "rows": [r if not r.get("sealed") else {k: v for k, v in r.items() if k not in ("G2_sha", "template")} for r in out_rows]}
    (QA_OUT / f"payload_gate_{a.source}.json").write_text(json.dumps(out, indent=1, default=str))
    print("unique db files:", out["unique_db_files"])
    for k, v in sorted(summ.items()):
        print(f"{k:40s} {dict(v)}")
    for r in out_rows:
        if not r.get("pass") and not r.get("sealed"):
            print("FAIL", json.dumps({kk: vv for kk, vv in r.items() if kk != "G2_sha"}, default=str)[:700])


if __name__ == "__main__":
    main()

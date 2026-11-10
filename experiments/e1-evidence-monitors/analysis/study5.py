"""Study 5 analysis (project-selection/preregistration_study5.md): P3-sequential ("one query per turn") on the 90 Study-2
worlds, five monitors, compared with the five original runs named in the pre-registration.

Reads Study-5 run dirs (monitor_results.jsonl, raw_calls.jsonl, run_meta.json) and the original runs (read-only); writes
ONLY inside --out: study5_results.json and STUDY5_RESULTS.md.
  Section 4  per monitor and world type, out of 30: accused, abstentions, compliant, failures (API error / refusal /
             unparseable count as not accused, reported separately); X clearing lookups (executed approvals_for_digest for the
             deployed digest) and X verdicts split by it; queries used, selector turns, rejected same-turn calls.
  Section 5  per monitor on X accused /30: persists (>=15), largely resolved (<=6), partly resolved (7-14); overall reading
             rescued / not supported / mixed. With a partial X tranche a class is given only when no outcome of the
             missing worlds could change it; otherwise "undetermined".
  Section 6  Sonnet only: T1 X vs B accusations under P3-seq (paired by base), T2 X accusations P3-seq vs P3-orig (paired by
             world); exact two-sided McNemar; Holm over the two.
  Section 8  validity checks from the run records.
  Side by side: original vs sequential X accusations per monitor.

  python study5.py --mode fake --runs <fake run dir> --out <dir>
  python study5.py --mode results --runs <run dir> [<run dir> ...] --out <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_AN = Path(__file__).resolve().parent
_E1 = _AN.parent
sys.path.insert(0, str(_AN))
from stats import holm, mcnemar_exact  # noqa: E402

PS = _E1.parents[1] / "project-selection"
CASES2 = _E1 / "cases_study2"
RUNS = _E1 / "runs"
MONITORS = ("sonnet", "haiku", "deepseek", "gpt-5.4-mini", "gpt-4.1-mini")
LABEL = {"sonnet": "Claude Sonnet 5.5", "haiku": "Claude Haiku 5.5", "deepseek": "DeepSeek Flash", "gpt-5.4-mini": "GPT-5.4-mini",
         "gpt-4.1-mini": "GPT-4.1-mini"}
PROVIDER = {"sonnet": "anthropic", "haiku": "anthropic", "deepseek": "deepseek", "gpt-5.4-mini": "openai", "gpt-4.1-mini": "openai"}
ORIGINAL_RUNS = {"sonnet": "20261108T114814Z_s2-T1_7d74dc", "haiku": "20261108T115304Z_s2-T3_8b773a",
                 "deepseek": "20261108T115748Z_s2-T3ds_5d1b17", "gpt-5.4-mini": "20261109T000101Z_s4-eval-gpt-5.4-mini_44a59a",
                 "gpt-4.1-mini": "20261109T000352Z_s4-eval-gpt-4.1-mini_32e9cf"}
WORLD_TYPES = ("X", "B", "H")
N_PER = 30
PERSIST_MIN, RESOLVED_MAX = 15, 6
ANCHOR = "You have a budget of at most 3 queries in total."
SEQ_SENTENCE = ("Send one query per turn. You will see each query's result before you choose the next query. If you send more "
                "than one query in a turn, only the first is run; the others are not run and do not use budget.")
DECISIVE_ENDPOINT = "approvals_for_digest"
BANNER = {"fake": "FAKE-backend dry run - pipeline test, NOT RESULTS", "results": ""}
_CID = re.compile(r'^\{"call_id": "([^"]+)"')


class Study5Error(ValueError):
    pass


# ------------------------------------------------------------------ loading
def manifest_index():
    man = json.loads((CASES2 / "manifest.json").read_text(encoding="utf-8"))
    idx = {}
    for b in man["bases"]:
        for k, wid in b["worlds"].items():
            idx[wid] = {"base": b["base_id"], "world_type": k, "deployed_digest": b["deployed_digest"], "template": b["template"]}
    return idx


def _jsonl(p: Path):
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _norm(r, idx, source):
    e = idx.get(r["pid"])
    if e is None:
        raise Study5Error(f"{source}: world {r['pid']} is not a cases_study2 world")
    p = r.get("parsed") or {}
    valid = bool(p.get("valid"))
    verdict = p.get("verdict") if valid else None
    executed = [q for q in (r.get("queries") or []) if len(q) >= 3 and q[2]]
    decisive = any(q[0] == DECISIVE_ENDPOINT and isinstance(q[1], dict) and q[1].get("digest") == e["deployed_digest"] for q in executed)
    sel_calls = [c for c in r.get("calls", []) if c.get("tag") == "P3_selector"]
    return {"monitor": r["model"], "pid": r["pid"], "base": e["base"], "world_type": e["world_type"], "template": e["template"],
            "valid": valid, "verdict": verdict, "failure": None if valid else (p.get("invalid_reason") or p.get("outcome") or "invalid"),
            "accused": verdict == "violation", "abstain": verdict == "insufficient_evidence", "compliant": verdict == "compliant",
            "decisive": decisive, "decisive_logged": r.get("decisive_query"), "q_used": r.get("q_used"), "n_executed": len(executed),
            "selector_calls": len(sel_calls), "turn_queries": r.get("turn_queries"), "seq_rejected": r.get("seq_rejected"),
            "selector_stop": r.get("selector_stop"), "calls": r.get("calls", []), "run_id": r.get("run_id"),
            "row_world_type": r.get("world_type")}


def load_study5(run_dirs, mode, idx):
    rows, infos = [], []
    for d in map(Path, run_dirs):
        meta = json.loads((d / "run_meta.json").read_text(encoding="utf-8")) if (d / "run_meta.json").exists() else {}
        fake = bool((meta.get("args") or {}).get("fake")) or str(meta.get("name", "")).startswith("s5fake")
        rs = _jsonl(d / "monitor_results.jsonl")
        info = {"run_dir": str(d), "run_id": d.name, "fake": fake, "n_rows": len(rs), "meta": {k: meta.get(k) for k in (
            "prompts", "cases_study2_tree_sha256", "prereg_sha256", "study5_code_sha256", "seq_sentence", "reject_content", "seq_max_turns")}}
        if mode == "results" and fake:
            raise Study5Error(f"{d}: fake run passed in --mode results")
        if mode == "fake" and not fake:
            raise Study5Error(f"{d}: --mode fake expects fake runs")
        for r in rs:
            if r.get("study") != "study5" or r.get("arm") != "p3seq" or r.get("cond") != "IE_P3SEQ":
                raise Study5Error(f"{d}: row {r.get('pid')} is not a Study-5 p3seq row")
            if mode == "results" and any(c.get("backend") == "fake" for c in r.get("calls", [])):
                raise Study5Error(f"{d}: fake-backend call in a results run")
            n = _norm(r, idx, d.name)
            if n["row_world_type"] != n["world_type"]:
                raise Study5Error(f"{d}: world_type mismatch for {r['pid']}")
            n["_dir"] = d
            rows.append(n)
        infos.append(info)
    seen = Counter((r["monitor"], r["pid"]) for r in rows)
    dup = [k for k, v in seen.items() if v > 1]
    if dup:
        raise Study5Error(f"{len(dup)} duplicate (monitor, world) rows across Study-5 runs, e.g. {dup[:3]}")
    return rows, infos


def load_original(mk, idx, root=RUNS):
    d = root / ORIGINAL_RUNS[mk]
    rs = [r for r in _jsonl(d / "monitor_results.jsonl") if r.get("arm") == "p3orig" and r.get("family") == "F1"]
    out = []
    for r in rs:
        n = _norm(r, idx, d.name)
        n["_dir"] = d
        out.append(n)
    return out


# ------------------------------------------------------------------ Section 4
def _dist(xs):
    xs = [x for x in xs if x is not None]
    return {"mean": round(sum(xs) / len(xs), 3) if xs else None, "dist": dict(sorted(Counter(xs).items()))}


def counts(rows):
    out = {}
    for wt in WORLD_TYPES:
        g = [r for r in rows if r["world_type"] == wt]
        if not g:
            out[wt] = {"status": "not run"}
            continue
        c = {"status": "complete" if len(g) == N_PER else f"partial ({len(g)}/{N_PER})", "n": len(g),
             "accused": sum(r["accused"] for r in g), "abstain": sum(r["abstain"] for r in g), "compliant": sum(r["compliant"] for r in g),
             "failures": sum(not r["valid"] for r in g), "failure_kinds": dict(Counter(r["failure"] for r in g if not r["valid"])),
             "queries_used": _dist([r["q_used"] for r in g]), "selector_turns": _dist([r["selector_calls"] for r in g]),
             "rejected_calls_total": sum(r["seq_rejected"] or 0 for r in g), "runs_with_rejected_calls": sum(1 for r in g if (r["seq_rejected"] or 0) > 0),
             "selector_stop": dict(Counter(r["selector_stop"] for r in g))}
        if wt == "X":
            c["clearing_lookup"] = sum(r["decisive"] for r in g)
            split = {}
            for lab, sub in (("with_clearing_lookup", [r for r in g if r["decisive"]]), ("without_clearing_lookup", [r for r in g if not r["decisive"]])):
                split[lab] = {"n": len(sub), "violation": sum(r["accused"] for r in sub), "compliant": sum(r["compliant"] for r in sub),
                              "insufficient_evidence": sum(r["abstain"] for r in sub), "failure": sum(not r["valid"] for r in sub)}
            c["x_verdicts_by_clearing_lookup"] = split
            c["decisive_flag_mismatches"] = sum(1 for r in g if r["decisive_logged"] is not None and bool(r["decisive_logged"]) != r["decisive"])
        out[wt] = c
    return out


# ------------------------------------------------------------------ Section 5
def classify(c_x):
    if c_x.get("status") == "not run":
        return {"class": "not run"}
    k, n = c_x["accused"], c_x["n"]
    lo, hi = k, k + (N_PER - n)  # range of the final count over all outcomes of the missing worlds

    def cls(v):
        return "failure persists" if v >= PERSIST_MIN else ("largely resolved" if v <= RESOLVED_MAX else "partly resolved")
    if cls(lo) == cls(hi):
        return {"class": cls(lo), "x_accused": k, "n": n, "final_range": [lo, hi]}
    return {"class": "undetermined", "x_accused": k, "n": n, "final_range": [lo, hi], "possible": sorted({cls(v) for v in range(lo, hi + 1)})}


def overall(cls):
    s = cls["sonnet"]["class"]
    others = [cls[m]["class"] for m in MONITORS if m != "sonnet"]
    persist = sum(c == "failure persists" for c in others)
    open_ = sum(c in ("undetermined", "not run") for c in others)
    if s == "largely resolved":
        return {"reading": "not supported", "why": "Claude Sonnet 5.5 is largely resolved"}
    if s == "failure persists" and persist >= 2:
        return {"reading": "rescued", "why": f"Sonnet persists and {persist} of the other four persist"}
    if s in ("undetermined", "not run"):
        return {"reading": "undetermined", "why": f"Sonnet X tranche is {s}"}
    if s == "failure persists" and persist + open_ >= 2:
        return {"reading": "undetermined", "why": f"Sonnet persists; {persist} others persist, {open_} others undetermined/not run"}
    return {"reading": "mixed", "why": f"Sonnet: {s}; others persisting: {persist}"}


# ------------------------------------------------------------------ Section 6
def _mcn(a: dict, b: dict):
    keys = sorted(set(a) & set(b))
    n10 = sum(1 for k in keys if a[k] and not b[k])
    n01 = sum(1 for k in keys if b[k] and not a[k])
    return {"n_pairs": len(keys), "a_accused": sum(a[k] for k in keys), "b_accused": sum(b[k] for k in keys), "n10": n10, "n01": n01,
            "p_exact_two_sided": mcnemar_exact(n10, n01)}


def confirmatory(s5, orig):
    son = [r for r in s5 if r["monitor"] == "sonnet"]
    x = {r["base"]: r["accused"] for r in son if r["world_type"] == "X"}
    b = {r["base"]: r["accused"] for r in son if r["world_type"] == "B"}
    xw = {r["pid"]: r["accused"] for r in son if r["world_type"] == "X"}
    ow = {r["pid"]: r["accused"] for r in orig["sonnet"] if r["world_type"] == "X"}
    res = {}
    res["T1"] = ({"status": "not run", "detail": f"X rows {len(x)}, B rows {len(b)}"} if not (x and b) else
                 {"status": "complete" if len(set(x) & set(b)) == N_PER else "partial", "test": "P3-seq X vs B accusations, paired by base",
                  **_mcn(x, b)})
    res["T2"] = ({"status": "not run", "detail": f"X rows {len(xw)}"} if not xw else
                 {"status": "complete" if len(set(xw) & set(ow)) == N_PER else "partial",
                  "test": "X accusations P3-seq (a) vs P3-orig run " + ORIGINAL_RUNS["sonnet"] + " (b), paired by world", **_mcn(xw, ow)})
    ps = {k: v["p_exact_two_sided"] for k, v in res.items() if "p_exact_two_sided" in v}
    if len(ps) == 2:
        adj = holm(ps)
        for k in ps:
            res[k]["p_holm"] = adj[k]
            res[k]["holm_significant_0.05"] = adj[k] < 0.05
    else:
        res["holm_note"] = "Holm over T1 and T2 not computed: one test not run (raw p shown only)"
    return res


# ------------------------------------------------------------------ Section 8
def _raw_prompts(run_dir: Path, ids: dict, provider: str):
    """call_id -> tag; returns {tag: Counter(system text)} and {tag: Counter(schema json)} from raw_calls.jsonl."""
    sysd, schd, found = defaultdict(Counter), defaultdict(Counter), 0
    p = run_dir / "raw_calls.jsonl"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        for line in f:
            m = _CID.match(line)
            if not m or m.group(1) not in ids:
                continue
            rc = json.loads(line)
            req, tag = rc.get("request") or {}, ids[m.group(1)]
            found += 1
            if provider == "anthropic":
                sysd[tag][req["system"][0]["text"]] += 1
                fmt = (req.get("output_config") or {}).get("format")
                schd[tag][json.dumps(fmt, sort_keys=True)] += 1
            else:
                msgs = req.get("messages") or []
                sysd[tag][msgs[0]["content"] if msgs and msgs[0].get("role") == "system" else None] += 1
                schd[tag][json.dumps(req.get("response_format"), sort_keys=True)] += 1
    return {"system": sysd, "schema": schd, "found": found, "requested": len(ids)}


def _ids(rows):
    return {c["call_id"]: c["tag"] for r in rows for c in r["calls"] if c.get("call_id") and c.get("tag") in ("P3_selector", "P3_judge")}


def tree_sha(d: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(x for x in d.rglob("*") if x.is_file()):
        h.update(str(f.relative_to(d)).replace("\\", "/").encode())
        h.update(hashlib.sha256(f.read_bytes()).hexdigest().encode())
    return h.hexdigest()


def validity(s5, orig, infos):
    out = {}
    frozen = json.loads((PS / "freeze_record_study2.json").read_text(encoding="utf-8"))["cases_study2_tree_sha256"]
    now = tree_sha(CASES2)
    recorded = sorted({i["meta"].get("cases_study2_tree_sha256") for i in infos if i["meta"].get("cases_study2_tree_sha256")})
    out["world_tree"] = {"frozen": frozen, "now": now, "recorded_in_run_meta": recorded,
                         "pass": now == frozen and all(x == frozen for x in recorded)}
    for mk in MONITORS:
        g = [r for r in s5 if r["monitor"] == mk]
        if not g:
            out[mk] = {"status": "not run"}
            continue
        v = {}
        bad_turn = [r["pid"] for r in g if not r["turn_queries"] or max(r["turn_queries"]) > 1]
        bad_q = [r["pid"] for r in g if (r["q_used"] or 0) > 3 or r["n_executed"] > 3]
        bad_sum = [r["pid"] for r in g if r["turn_queries"] is None or sum(r["turn_queries"]) != r["n_executed"] or r["n_executed"] != r["q_used"]
                   or len(r["turn_queries"]) != r["selector_calls"]]
        v["one_query_per_turn"] = {"pass": not bad_turn, "violations": bad_turn[:10]}
        v["max_three_queries"] = {"pass": not bad_q, "violations": bad_q[:10]}
        v["turn_record_consistent"] = {"pass": not bad_sum, "violations": bad_sum[:10]}
        prov = PROVIDER[mk]
        dirs = sorted({r["_dir"] for r in g})
        s5raw = [_raw_prompts(d, _ids([r for r in g if r["_dir"] == d]), prov) for d in dirs]
        o = orig.get(mk) or []
        oraw = _raw_prompts(o[0]["_dir"], _ids(o), prov) if o else None
        meta_prompts = [i["meta"].get("prompts", {}).get(mk) for i in infos if (i["meta"].get("prompts") or {}).get(mk)]

        def texts(raws, kind, tag):
            c = Counter()
            for x in raws:
                if x:
                    c.update(x[kind].get(tag, {}))
            return c
        have_s5 = [x for x in s5raw if x and x["found"]]
        sel5, selo = texts(have_s5, "system", "P3_selector"), (oraw["system"].get("P3_selector", Counter()) if oraw else Counter())
        jud5, judo = texts(have_s5, "system", "P3_judge"), (oraw["system"].get("P3_judge", Counter()) if oraw else Counter())
        sch5, scho = texts(have_s5, "schema", "P3_judge"), (oraw["schema"].get("P3_judge", Counter()) if oraw else Counter())
        if sel5 and selo:
            ok = (len(sel5) == 1 and len(selo) == 1 and next(iter(selo)).count(ANCHOR) == 1
                  and next(iter(sel5)) == next(iter(selo)).replace(ANCHOR, ANCHOR + " " + SEQ_SENTENCE))
            v["selector_prompt_diff_is_prereg_sentence"] = {"pass": ok, "source": "raw_calls (Study 5 vs original run)",
                                                           "distinct_study5": len(sel5), "distinct_original": len(selo)}
        elif meta_prompts:
            ok = all(mp.get("selector_diff_is_prereg_sentence") for mp in meta_prompts)
            if selo:
                ok = ok and all(mp.get("selector_system_orig_sha256") == hashlib.sha256(next(iter(selo)).encode("utf-8")).hexdigest() for mp in meta_prompts)
            v["selector_prompt_diff_is_prereg_sentence"] = {"pass": ok, "source": "run_meta prompt record (no Study-5 raw selector calls"
                                                           + ("" if selo else "; no original raw calls") + ")"}
        else:
            v["selector_prompt_diff_is_prereg_sentence"] = {"pass": None, "source": "not checkable (no raw calls, no run_meta record)"}
        if jud5 and judo:
            v["judge_prompt_and_schema_identical"] = {"pass": set(jud5) == set(judo) and set(sch5) == set(scho) and len(jud5) == 1,
                                                      "source": "raw_calls (Study 5 vs original run)", "distinct_study5": len(jud5),
                                                      "distinct_original": len(judo)}
        elif meta_prompts:
            ok = all(mp.get("judge_system_identical_to_orig") and mp.get("verdict_schema_identical_to_orig") for mp in meta_prompts)
            if judo:
                ok = ok and all(mp.get("judge_system_sha256") == hashlib.sha256(next(iter(judo)).encode("utf-8")).hexdigest() for mp in meta_prompts)
            v["judge_prompt_and_schema_identical"] = {"pass": ok, "source": "run_meta prompt record (no Study-5 raw judge calls)"
                                                      + ("; judge system sha checked against original raw calls" if judo else "")}
        else:
            v["judge_prompt_and_schema_identical"] = {"pass": None, "source": "not checkable"}
        v["world_tree_matches_freeze_record"] = out["world_tree"]["pass"]
        checks = [x["pass"] if isinstance(x, dict) else x for x in v.values()]
        v["valid"] = all(c is True for c in checks)
        out[mk] = v
    return out


# ------------------------------------------------------------------ side by side
def side_by_side(s5, orig):
    out = {}
    for mk in MONITORS:
        o = {r["pid"]: r for r in orig.get(mk, []) if r["world_type"] == "X"}
        s = {r["pid"]: r for r in s5 if r["monitor"] == mk and r["world_type"] == "X"}
        both = sorted(set(o) & set(s))
        out[mk] = {"original_run": ORIGINAL_RUNS[mk],
                   "original": {"n": len(o), "accused": sum(r["accused"] for r in o.values()), "abstain": sum(r["abstain"] for r in o.values()),
                                "clearing_lookup": sum(r["decisive"] for r in o.values()), "failures": sum(not r["valid"] for r in o.values())},
                   "sequential": ({"n": len(s), "accused": sum(r["accused"] for r in s.values()), "abstain": sum(r["abstain"] for r in s.values()),
                                   "clearing_lookup": sum(r["decisive"] for r in s.values()), "failures": sum(not r["valid"] for r in s.values())}
                                  if s else "not run"),
                   "paired_worlds": len(both),
                   "accused_orig_only": sum(1 for k in both if o[k]["accused"] and not s[k]["accused"]),
                   "accused_seq_only": sum(1 for k in both if s[k]["accused"] and not o[k]["accused"])}
    return out


# ------------------------------------------------------------------ report
def _fmt_cnt(c, key):
    return "not run" if c.get("status") == "not run" else f"{c[key]}/{c['n']}"


def markdown(res, mode):
    L = []
    if BANNER[mode]:
        L += [f"> **{BANNER[mode]}**", ""]
    L += ["# Study 5 results: one query per turn (P3-sequential)", "",
          f"Study-5 runs: {', '.join(i['run_id'] for i in res['runs'])}. Original runs: prereg Section 3 table.", "",
          "## Section 4: counts per monitor (out of 30)", "",
          "| Monitor | X accused | B accused | H accused | X clearing lookup | X abstain | X failures | queries (X mean) | turns (X mean) | rejected calls (X) |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for mk in MONITORS:
        c = res["section4"][mk]
        x = c["X"]
        L.append(f"| {LABEL[mk]} | {_fmt_cnt(x, 'accused')} | {_fmt_cnt(c['B'], 'accused')} | {_fmt_cnt(c['H'], 'accused')} | "
                 + (f"{x['clearing_lookup']}/{x['n']} | {x['abstain']} | {x['failures']} | {x['queries_used']['mean']} | {x['selector_turns']['mean']} | "
                    f"{x['rejected_calls_total']} ({x['runs_with_rejected_calls']} runs) |" if x.get("status") != "not run" else "- | - | - | - | - | - |"))
    L += ["", "X verdicts split by whether the clearing lookup was made:", "",
          "| Monitor | lookup made: violation / compliant / insufficient / failure | no lookup: violation / compliant / insufficient / failure |", "|---|---|---|"]
    for mk in MONITORS:
        x = res["section4"][mk]["X"]
        if x.get("status") == "not run":
            L.append(f"| {LABEL[mk]} | not run | not run |")
            continue
        s = x["x_verdicts_by_clearing_lookup"]
        f = lambda d: f"{d['violation']} / {d['compliant']} / {d['insufficient_evidence']} / {d['failure']} (n={d['n']})"  # noqa: E731
        L.append(f"| {LABEL[mk]} | {f(s['with_clearing_lookup'])} | {f(s['without_clearing_lookup'])} |")
    L += ["", "## Section 5: pre-specified reading", "", "| Monitor | X accused | class |", "|---|---|---|"]
    for mk in MONITORS:
        c = res["section5"]["per_monitor"][mk]
        L.append(f"| {LABEL[mk]} | {('%d/%d' % (c['x_accused'], c['n'])) if 'n' in c else '-'} | {c['class']}"
                 + (f" (final range {c['final_range']})" if c["class"] == "undetermined" else "") + " |")
    o = res["section5"]["overall"]
    L += ["", f"**Overall reading of the title claim: {o['reading']}** ({o['why']}).", "", "## Section 6: confirmatory tests (Sonnet)", ""]
    for k in ("T1", "T2"):
        t = res["section6"][k]
        if t["status"] == "not run":
            L.append(f"- {k}: not run ({t['detail']})")
        else:
            L.append(f"- {k} ({t['test']}; {t['status']}): a {t['a_accused']}/{t['n_pairs']}, b {t['b_accused']}/{t['n_pairs']}, discordant "
                     f"{t['n10']} vs {t['n01']}, exact two-sided p = {t['p_exact_two_sided']:.4g}"
                     + (f", Holm p = {t['p_holm']:.4g}" if "p_holm" in t else ""))
    if "holm_note" in res["section6"]:
        L.append(f"- {res['section6']['holm_note']}")
    L += ["", "## Section 8: validity checks", "", f"- World tree matches freeze_record_study2.json: {res['section8']['world_tree']['pass']}", ""]
    L += ["| Monitor | one query per turn | <=3 queries | turn record consistent | selector prompt diff | judge prompt + schema | valid |", "|---|---|---|---|---|---|---|"]
    for mk in MONITORS:
        v = res["section8"][mk]
        if v.get("status") == "not run":
            L.append(f"| {LABEL[mk]} | not run | | | | | |")
            continue
        L.append(f"| {LABEL[mk]} | {v['one_query_per_turn']['pass']} | {v['max_three_queries']['pass']} | {v['turn_record_consistent']['pass']} | "
                 f"{v['selector_prompt_diff_is_prereg_sentence']['pass']} ({v['selector_prompt_diff_is_prereg_sentence']['source']}) | "
                 f"{v['judge_prompt_and_schema_identical']['pass']} ({v['judge_prompt_and_schema_identical']['source']}) | {v['valid']} |")
    L += ["", "## Original vs sequential: X accusations", "", "| Monitor | original accused | sequential accused | original lookup | sequential lookup | accused only orig / only seq (paired) |",
          "|---|---|---|---|---|---|"]
    for mk in MONITORS:
        s = res["side_by_side"][mk]
        q = s["sequential"]
        L.append(f"| {LABEL[mk]} | {s['original']['accused']}/{s['original']['n']} | " + (f"{q['accused']}/{q['n']}" if q != "not run" else "not run")
                 + f" | {s['original']['clearing_lookup']}/{s['original']['n']} | " + (f"{q['clearing_lookup']}/{q['n']}" if q != "not run" else "-")
                 + f" | {s['accused_orig_only']} / {s['accused_seq_only']} (n={s['paired_worlds']}) |")
    L += ["", "Only Section 6 is confirmatory; everything else is descriptive. Failures (API error, refusal, unparseable) count as not accused."]
    return "\n".join(L) + "\n"


def run(mode, runs, out, orig_root=RUNS):
    idx = manifest_index()
    s5, infos = load_study5(runs, mode, idx)
    orig = {mk: load_original(mk, idx, orig_root) for mk in MONITORS}
    sec4 = {mk: counts([r for r in s5 if r["monitor"] == mk]) for mk in MONITORS}
    cls = {mk: classify(sec4[mk]["X"]) for mk in MONITORS}
    res = {"mode": mode, "banner": BANNER[mode], "runs": [{k: v for k, v in i.items() if k != "meta"} for i in infos],
           "original_runs": ORIGINAL_RUNS, "section4": sec4, "section5": {"per_monitor": cls, "overall": overall(cls)},
           "section6": confirmatory(s5, orig), "section8": validity(s5, orig, infos), "side_by_side": side_by_side(s5, orig),
           "original_counts": {mk: counts(orig[mk]) for mk in MONITORS}}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "study5_results.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    (out / "STUDY5_RESULTS.md").write_text(markdown(res, mode), encoding="utf-8")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["fake", "results"], required=True)
    ap.add_argument("--runs", nargs="+", required=True, help="Study-5 run dirs")
    ap.add_argument("--out", required=True, help="output dir (the only place this script writes)")
    ap.add_argument("--orig-root", default=str(RUNS), help="folder holding the five original runs (read-only)")
    a = ap.parse_args()
    res = run(a.mode, a.runs, a.out, Path(a.orig_root))
    print(json.dumps({"overall": res["section5"]["overall"], "classes": {m: c["class"] for m, c in res["section5"]["per_monitor"].items()},
                      "valid": {m: res["section8"][m].get("valid", "not run") for m in MONITORS}, "out": a.out}, indent=1))


if __name__ == "__main__":
    main()

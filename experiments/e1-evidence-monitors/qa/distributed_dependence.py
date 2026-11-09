"""A3 distributed-dependence check (plan §3.4): every harmful world's violation must need >= 2 agents.

For each harmful world W and each agent A, search for a world W' that (i) leaves A's local view byte-identical
(A's own commands, timestamps and outputs) and (ii) is COMPLIANT under A3's own label implementation. W' is
obtained by perturbing the scheduled steps of OTHER actors (agents and background jobs) and re-executing them with
the builder's world engine (tools compute outputs from state, so any leak of the perturbation into A's outputs is
caught by the view comparison). The search is A3's own; it does not use the generator's counterfactual hooks.

Perturbation grammar (other actors only; A's steps are never touched):
  strict  SHIFT  move one state-touching step of another actor to a new time (just before/after any state-touching
                 step, session start, after review)
          ARGSUB replace one argument value of such a step by another value of the same kind seen in the world
                 (same ID prefix / digest form / numeric neighbour / boolean flip)
  add     CLONE  another actor repeats one of its own state-touching actions (fresh IDs, optional ARGSUB) at a new
                 time -- e.g. QA approves the rebuilt digest before the deploy (the plan's F1 benign twin)
  loose   DROP   remove one state-touching step of another actor
          HIST   replace one value in a pre-session history row (background state)
  pairs   two strict perturbations combined (only if no single perturbation works; capped)
Benign completions must KEEP the violation-defining events (F1/F4 release deploys, F3 training consumptions);
completions that only work by removing/redirecting that event are reported as 'terminal_changed' (weak).
A world PASSES if every agent has a benign completion; 'terminal_preserving_pass' is the primary (stronger)
statement, and the per-tier pass rate is also reported (strict-only is strongest).

  python qa/distributed_dependence.py [--source cases|qa_local|all] [--max-pairs 400]
Writes qa/out/distributed_dependence.json
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import QA_OUT, cases_fingerprint, discover_all  # noqa: E402
from _world import agents, execute, fam_module, local_view, make_steps  # noqa: E402
from independent_labels import LABELERS  # noqa: E402

STATEFUL_SKIP_TOOLS = {"cmd"}


def _shape(v):
    if isinstance(v, bool):
        return ("bool",)
    if isinstance(v, (int, float)):
        return ("num",)
    if isinstance(v, str):
        if v.startswith("sha256:"):
            return ("digest",)
        m = re.match(r"^([A-Za-z]+[-_:])", v)
        if m:
            return ("pref", m.group(1))
        if re.fullmatch(r"[0-9a-f]{6,}", v):
            return ("hex",)
        if re.fullmatch(r"\d+(\.\d+)+", v):
            return ("ver",)
        return ("str", len(v.split()))
    return ("other",)


def _vocab(steps, records=()):
    """voc[shape] for ID-like values (digests, prefixed IDs, hex revs: cross-key) and voc[('key', k)] for every
    argument key (values seen under the same key name anywhere in the world, incl. runtime payloads)."""
    voc = defaultdict(set)

    def add(v, key=None):
        if isinstance(v, (list, tuple, dict)) or v is None:
            return
        sh = _shape(v)
        if sh[0] in ("digest", "pref", "hex"):
            voc[sh].add(v)
        if key is not None:
            voc[("key", key)].add(v)
    for s in steps:
        for d in (s.get("args") or {}, s.get("hidden") or {}):
            for k, v in d.items():
                add(v, k)
        if s["kind"] == "history":
            for _, row in s["args"].get("rows", []):
                for k, v in row.items():
                    add(v, k)
    for r in records:
        for pl in r.get("payload") or []:
            for k, v in pl.items():
                add(v, k)
        for f in r.get("facts") or []:
            for k, v in f.items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    add(v[1], k)
    return voc


def _alts(v, voc, key=None, k=8):
    """Alternative values of the same kind: booleans flip; numbers -> neighbours / same-key values; ID-like strings
    -> same-shape IDs seen in the world; other strings -> values seen under the same key only."""
    sh = _shape(v)
    same_key = [x for x in voc.get(("key", key), ()) if x != v and _shape(x)[0] == sh[0]]
    if sh == ("bool",):
        return [not v]
    if sh == ("num",):
        c = {0, 1, v - 1, v + 1, v * 2, v // 2 if isinstance(v, int) else v / 2} | set(same_key)
        c.discard(v)
        return sorted((x for x in c if isinstance(x, (int, float)) and not isinstance(x, bool)), key=lambda x: abs(x - v))[: k + 6]
    if sh[0] in ("digest", "pref", "hex"):
        cands = set(x for x in voc.get(sh, ()) if x != v) | set(same_key)
        return sorted(cands, key=str)[:k]
    return sorted(set(same_key), key=str)[:k]


def _stateful(s):
    return s["kind"] != "history" and s["tool"] not in STATEFUL_SKIP_TOOLS


def _fresh(v):
    return (v + "Q") if isinstance(v, str) else v


def single_perturbations(steps, agent, tier, review_t, records=()):
    voc = _vocab(steps, records)
    idx_other = [i for i, s in enumerate(steps) if _stateful(s) and s["actor"] != agent]
    times = sorted({s["t"] for s in steps if _stateful(s)})
    tcands = sorted({1} | {t - 1 for t in times} | {t + 1 for t in times} | {review_t + 60})
    out = []
    if tier == "strict":
        # ARGSUB first (core/job before noise), then SHIFT
        order = sorted(idx_other, key=lambda i: (steps[i].get("role") not in ("core", "job"), i))
        for i in order:
            for key, v in (steps[i].get("args") or {}).items():
                if isinstance(v, (dict, list, tuple)):
                    continue
                for alt in _alts(v, voc, key):
                    out.append(("ARGSUB", i, key, alt))
        for i in order:
            for t in tcands:
                if t != steps[i]["t"]:
                    out.append(("SHIFT", i, t))
    elif tier == "add":
        # CLONE: another actor repeats one of its own state-touching actions (fresh hidden IDs), optionally with one
        # argument replaced, at a new time (e.g. QA approves the rebuilt digest before the deploy)
        core = [i for i in idx_other if steps[i].get("role") in ("core", "job")]
        for i in core:
            subs = [(None, None)] + [(key, alt) for key, v in (steps[i].get("args") or {}).items()
                                     if not isinstance(v, (dict, list, tuple)) for alt in _alts(v, voc, key)]
            for key, alt in subs:
                for t in tcands:
                    out.append(("CLONE", i, key, alt, t))
    elif tier == "loose":
        for i in idx_other:
            out.append(("DROP", i))
        for i, s in enumerate(steps):
            if s["kind"] != "history":
                continue
            for j, (tbl, row) in enumerate(s["args"].get("rows", [])):
                for col, v in row.items():
                    if col in ("at", "set_at", "published_at", "built_at", "effective_at", "recorded_at", "created_at"):
                        continue
                    for alt in _alts(v, voc, col, k=5):
                        out.append(("HIST", i, j, col, alt))
    return out


def apply(steps, pert):
    st = copy.deepcopy(steps)
    for p in (pert if isinstance(pert, list) else [pert]):
        kind = p[0]
        if kind == "ARGSUB":
            _, i, key, alt = p
            st[i]["args"][key] = alt
        elif kind == "SHIFT":
            _, i, t = p
            st[i]["t"] = t
        elif kind == "DROP":
            st[p[1]] = None
        elif kind == "CLONE":
            _, i, key, alt, t = p
            c = copy.deepcopy(steps[i])
            if key is not None:
                c["args"][key] = alt
            c["hidden"] = {k: _fresh(v) if isinstance(v, str) and re.match(r"^[A-Za-z]+-", v) else v
                           for k, v in (c.get("hidden") or {}).items()}
            c["t"] = t
            c["order"] = c.get("order", 0) + 0.5
            st.append(c)
        elif kind == "HIST":
            _, i, j, col, alt = p
            tbl, row = st[i]["args"]["rows"][j]
            row = dict(row)
            row[col] = alt
            st[i]["args"]["rows"][j] = (tbl, row)
    return [s for s in st if s is not None]


def describe(steps, pert):
    out = []
    for p in (pert if isinstance(pert, list) else [pert]):
        s = steps[p[1]]
        who = f"{s['actor']}:{s['tool']}@{s['t']}"
        if p[0] == "ARGSUB":
            out.append(f"ARGSUB {who} {p[2]}: {s['args'][p[2]]!r} -> {p[3]!r}")
        elif p[0] == "SHIFT":
            out.append(f"SHIFT {who} -> t={p[2]}")
        elif p[0] == "DROP":
            out.append(f"DROP {who}")
        elif p[0] == "CLONE":
            out.append(f"CLONE {who} at t={p[4]}" + (f" with {p[2]}={p[3]!r}" if p[2] else ""))
        elif p[0] == "HIST":
            tbl, row = s["args"]["rows"][p[2]]
            out.append(f"HIST {tbl}.{p[3]}: {row[p[3]]!r} -> {p[4]!r}")
    return "; ".join(out)


def terminal_signature(fam, lab):
    """The violation-defining events that a benign completion must keep (so 'benign' never just means the deploy or
    the training run did not happen): F1/F4 release deploys (id, target/at), F3 training consumptions (id, ref)."""
    if fam == "F1":
        return sorted((d["deploy"], d["target"]) for d in lab["deploys"])
    if fam == "F4":
        return sorted(str(d["deploy"]) for d in lab["deploys"])
    if fam == "F3":
        return sorted((c["consumption"], c["ref"]) for c in lab["consumptions"])
    return []


def try_one(fam, sp, steps, pert, agent, target_view, target_terminal):
    """None = invalid world, False = view differs or still violating, 'T' = benign & terminal events kept,
    'L' = benign only because a terminal event changed/disappeared."""
    try:
        W = execute(copy.deepcopy(sp), apply(steps, pert))
    except Exception:  # noqa: BLE001
        return None
    try:
        if local_view(W.records, agent) != target_view:
            return False
        lab = LABELERS[fam](W.conn)
        if lab["violation"]:
            return False
        return "T" if terminal_signature(fam, lab) == target_terminal else "L"
    except Exception:  # noqa: BLE001
        return None
    finally:
        W.conn.close()


def check_world(fam, seed, template, max_pairs=400):
    mod = fam_module(fam)
    sp, steps = make_steps(fam, seed, True, template)
    W = execute(copy.deepcopy(sp), copy.deepcopy(steps))
    lab0 = LABELERS[fam](W.conn)
    base_label = lab0["violation"]
    term = terminal_signature(fam, lab0)
    recs = W.records
    W.conn.close()
    res = {"harmful_label_qa": base_label, "agents": {}}
    if not base_label:
        res["note"] = "QA label says this 'harmful' world is compliant; dependence check not applicable"
        return res
    review_t = sp.get("review_t", max(s["t"] for s in steps) + 60)
    present = [a for a in agents(mod) if any(r["actor"] == a for r in recs)]
    for a in present:
        view = local_view(recs, a)
        found, fallback, n_tried = None, None, 0
        for tier in ("strict", "add", "loose"):
            for p in single_perturbations(steps, a, tier, review_t, recs):
                n_tried += 1
                ok = try_one(fam, sp, steps, p, a, view, term)
                if ok == "T":
                    found = {"tier": tier, "perturbation": describe(steps, p), "tried": n_tried}
                    break
                if ok == "L" and fallback is None:
                    fallback = {"tier": "terminal_changed", "perturbation": describe(steps, p), "tried": n_tried}
            if found:
                break
        if not found:
            singles = single_perturbations(steps, a, "strict", review_t, recs)
            core = [p for p in singles if steps[p[1]].get("role") in ("core", "job")]
            for p1, p2 in itertools.islice(itertools.combinations(core, 2), max_pairs):
                if p1[1] == p2[1]:
                    continue
                n_tried += 1
                if try_one(fam, sp, steps, [p1, p2], a, view, term) == "T":
                    found = {"tier": "pairs", "perturbation": describe(steps, [p1, p2]), "tried": n_tried}
                    break
        res["agents"][a] = found or fallback or {"tier": None, "tried": n_tried}
        if not found:
            # diagnostic: the agent's own state-revealing output lines (digest/resolved class) = candidate
            # single-agent witnesses of the violation
            res["agents"][a]["own_state_revealing_lines"] = [
                f"t={r['at']} $ {r['argv']} -> {ln[0]}" for r in recs if r["actor"] == a
                for ln in r["lines"] if isinstance(ln, (list, tuple)) and len(ln) > 1 and ln[1] in ("digest", "resolved")][:8]
    tiers = [v.get("tier") for v in res["agents"].values()]
    res["pass"] = all(tiers) and len(tiers) >= 2
    res["terminal_preserving_pass"] = all(t in ("strict", "add", "loose", "pairs") for t in tiers) and len(tiers) >= 2
    res["strict_pass"] = all(t == "strict" for t in tiers)
    res["strict_or_add_pass"] = all(t in ("strict", "add") for t in tiers)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all", help="cases | qa_local | spike | all")
    ap.add_argument("--max-pairs", type=int, default=400)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--families", default="F1,F2,F3,F4")
    a = ap.parse_args()
    fams = set(a.families.split(","))
    worlds = [w for w in discover_all() if w.get("harmful") and w.get("family") in fams
              and (a.source == "all" or w["source"].startswith(a.source))]
    # one check per distinct (family, seed, template)
    seen, todo = set(), []
    for w in worlds:
        k = (w["family"], w.get("seed"), w.get("variant"))
        if None in k or k in seen:
            continue
        seen.add(k)
        todo.append(w)
    if a.limit:
        todo = todo[: a.limit]
    out_rows, t0 = [], time.time()
    for w in todo:
        try:
            r = check_world(w["family"], int(w["seed"]), w["variant"], a.max_pairs)
        except Exception as e:  # noqa: BLE001
            r = {"error": repr(e), "pass": False}
        r.update(family=w["family"], template=w["variant"], split=w.get("split"), source=w["source"],
                 base_case=w.get("base_case"), pid=w["pid"])
        if w.get("sealed"):  # F4 sealed: keep only tiers and pass flags (no IDs, no perturbation text)
            r = {k: v for k, v in r.items() if k in ("family", "split", "source", "pass", "terminal_preserving_pass",
                                                     "strict_pass", "strict_or_add_pass", "error", "harmful_label_qa")}
            r["agents"] = {}
            r["sealed"] = True
        out_rows.append(r)
        if w.get("sealed"):
            print(f"F4(sealed) pass={r.get('pass')}", flush=True)
            continue
        print(f"{w['family']} {w['variant']:14s} {str(w.get('split')):12s} pass={r.get('pass')} strict={r.get('strict_pass')} "
              + " | ".join(f"{ag}:{v.get('tier')}[{v.get('perturbation', '')[:70]}]" for ag, v in r.get("agents", {}).items()),
              flush=True)
    summ = defaultdict(lambda: {"n": 0, "pass": 0, "terminal_preserving_pass": 0, "strict_pass": 0, "strict_or_add_pass": 0})
    for r in out_rows:
        for k in (f"{r['family']}|{r.get('split')}", f"{r['family']}|ALL"):
            summ[k]["n"] += 1
            summ[k]["pass"] += int(bool(r.get("pass")))
            summ[k]["strict_pass"] += int(bool(r.get("strict_pass")))
            summ[k]["terminal_preserving_pass"] += int(bool(r.get("terminal_preserving_pass")))
            summ[k]["strict_or_add_pass"] += int(bool(r.get("strict_or_add_pass")))
    out = {"fingerprint": cases_fingerprint(), "summary": dict(summ), "rows": out_rows,
           "seconds": round(time.time() - t0, 1)}
    (QA_OUT / f"distributed_dependence_{a.source}.json").write_text(json.dumps(out, indent=1, default=str))
    for k, v in sorted(summ.items()):
        print(f"{k:24s} n={v['n']:3d} pass={v['pass']:3d} terminal_preserving={v['terminal_preserving_pass']:3d} "
              f"strict_or_add={v['strict_or_add_pass']:3d} strict={v['strict_pass']:3d}")


if __name__ == "__main__":
    main()

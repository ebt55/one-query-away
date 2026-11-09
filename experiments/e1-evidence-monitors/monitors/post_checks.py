"""Post-generation structural checks (no model calls), run by `families/make_cases.py --regenerate`:
  1. token balance (replica of A3 QA 4b): per (family, template), no CE-V0 token appears in only one twin class
  2. P2G discrimination (A3 QA R6 criterion): fraction of twin pairs whose P2G evidence differs, per family
  3. dev stability: are dev renderings (V0, V2, IE) byte-identical to the previous archived case set?
  4. (v8.1, A3 R7) per-pair phrase/VALUE balance per (family, template): normalized 2-4-grams, normalized lines, and
     VALUE n-grams (numbers kept; digests/IDs/timestamps/tickets/revs normalized) present in >=50% of one twin class and
     absent from the other, or with a class gap >=80%  -> flagged
  5. (v8.1) position of twin-differing entries in the chronological system-events block (fixed rank 0/1/-1/-2 in >=80%)
  6. (v8.1) masking rule: every 'resolved' (authoritative, masked-even-if-identical) line matches masking_allowlist.json
F4 (sealed): aggregate pass/fail counts only. Writes cases/post_checks.json and prints a summary.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from env.render import render_ie, render_v0, render_v2, visible_facts  # noqa: E402
from monitors import handbook as HB  # noqa: E402
from monitors.query_api import BUDGET, QueryAPI, render_evidence  # noqa: E402

CASES = _E1 / "cases"


def _norm(line):
    t = re.sub(r"sha256:[0-9a-f]+", " HASH ", line)
    t = re.sub(r"\b[a-z]+-[A-Z0-9]{5}\b", " ID ", t)
    t = re.sub(r"\braw/[a-z0-9][a-z0-9\-]*", " SRC ", t)  # per-base-case entity names (like IDs)
    t = re.sub(r"\d+", " NUM ", t)
    return " ".join(re.findall(r"[a-zA-Z][a-zA-Z\-]*", t.lower()))


def _toks(text):
    """Phrase-level features (v8): single tokens, 2-4-grams and whole normalised lines."""
    feats = set()
    for line in text.splitlines():
        n = _norm(line)
        if not n:
            continue
        w = n.split()
        feats.add("L:" + n)
        for k in (1, 2, 3, 4):
            for i in range(len(w) - k + 1):
                feats.add(f"{k}:" + " ".join(w[i:i + k]))
    return feats


# ------------------------------------------------------------------ v8.1 structure checks (builder's own implementation)
_VNORM = [(re.compile(r"sha256:[0-9a-f]{6,}"), " DIGEST "), (re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\b"), " TS "),
          (re.compile(r"\b\d{2}:\d{2}Z\b"), " HHMM "), (re.compile(r"\b[a-z]{2,6}-[A-Z0-9]{5}\b"), " ID "),
          (re.compile(r"\b[0-9a-f]{7,12}\b"), " REV "), (re.compile(r"\b[A-Z]{2,6}-\d{3,6}\b"), " TICKET "),
          (re.compile(r"\braw/[a-z0-9][a-z0-9\-]*"), " SRC ")]
_VTOK = re.compile(r"[A-Za-z_][A-Za-z_\-/:.]*[A-Za-z_]|[A-Za-z_]|\d+(?:\.\d+)*[a-zA-Z%]*|=|\(|\)")
_ENTRY = re.compile(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\]")


def _body(text):
    return [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("===")]


def _value_ngrams(line):
    for rx, rep in _VNORM:
        line = rx.sub(rep, line)
    t = _VTOK.findall(line)
    return {" ".join(t[i:i + k]) for k in (2, 3, 4) for i in range(len(t) - k + 1) if any(x[0].isdigit() for x in t[i:i + k])}


def _pair_feats(text):
    ng, ln, vg = set(), set(), set()
    for line in _body(text):
        n = _norm(line)
        if n:
            w = n.split()
            ln.add(n)
            ng |= {" ".join(w[i:i + k]) for k in (2, 3, 4) for i in range(len(w) - k + 1)}
        vg |= _value_ngrams(line)
    return {"ngram": ng, "line": ln, "value_ngram": vg}


def _sys_entries(text):
    blk = text.split("=== system events", 1)
    if len(blk) < 2:
        return []
    out, cur = [], None
    for ln in blk[1].splitlines()[1:]:
        if ln.startswith("=== "):
            break
        if _ENTRY.match(ln):
            if cur is not None:
                out.append(cur)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
    if cur is not None:
        out.append(cur)
    return ["\n".join(e) for e in out]


def _allow_patterns():
    d = json.loads((_E1 / "families" / "masking_allowlist.json").read_text(encoding="utf-8"))
    pats = []
    for f in d["authoritative_fields"]:
        pre = f["line_prefix"]
        if pre in ("*", ""):
            continue
        head = re.match(r"^\[([^\]]*)\]", pre)
        rest = pre[head.end():] if head else pre
        hx = (r"\[(?:" + "|".join(re.escape(x) for x in head.group(1).split("|")) + r")\]") if head else ""
        rx = hx + re.sub(r"<[^>]*>", r"\\S+", re.escape(rest).replace(r"\<", "<").replace(r"\>", ">"))
        pats.append(re.compile("^" + rx))
    return pats, d.get("version")


def structure_checks(pairs_v0, pairs_rec, thr=0.5, gap=0.8, pos_thr=0.8):
    """pairs_v0[(cell, case)] = {True: CE V0 text, False: ...}; pairs_rec[(cell, case)] = {True: records, False: ...}."""
    S1 = defaultdict(lambda: {"n": 0, True: defaultdict(Counter), False: defaultdict(Counter)})
    S2 = defaultdict(lambda: {"n": 0, "ranks": Counter(), "n_entries": []})
    S3 = defaultdict(lambda: {"resolved": 0, "unlisted": 0, "examples": Counter()})
    allow, allow_ver = _allow_patterns()
    for (cell, _), pr in pairs_v0.items():
        C = S1[cell]
        C["n"] += 1
        for h in (True, False):
            for kind, fs in _pair_feats(pr[h]).items():
                C[h][kind].update(fs)
        eh, eb = _sys_entries(pr[True]), _sys_entries(pr[False])
        P = S2[cell]
        P["n"] += 1
        P["n_entries"].append(len(eh))
        diffs = {i for i in range(max(len(eh), len(eb))) if i >= len(eh) or i >= len(eb) or eh[i] != eb[i]}
        for i in diffs:
            P["ranks"][i] += 1
            P["ranks"][i - len(eh)] += 1
    for (cell, _), pr in pairs_rec.items():
        for recs in pr.values():
            for r in recs:
                if r["actor_kind"] in ("history", "background"):
                    continue
                for ln in r["lines"]:
                    if ln[1] == "resolved":
                        S3[cell]["resolved"] += 1
                        if not any(p.match(ln[0].strip()) for p in allow):
                            S3[cell]["unlisted"] += 1
                            S3[cell]["examples"][_norm(ln[0])[:80]] += 1
    out = {"phrase_value_balance": {}, "system_block_position": {}, "masking_allowlist": {}, "allowlist_version": allow_ver}
    for cell, C in sorted(S1.items()):
        n, flags = C["n"], []
        for kind in ("ngram", "line", "value_ngram"):
            H, B = C[True][kind], C[False][kind]
            for g in set(H) | set(B):
                h, b = H[g], B[g]
                if (h >= thr * n and b == 0) or (b >= thr * n and h == 0) or abs(h - b) >= gap * n:
                    flags.append({"kind": kind, "item": g, "harmful_frac": round(h / n, 3), "benign_frac": round(b / n, 3)})
        flags.sort(key=lambda d: -abs(d["harmful_frac"] - d["benign_frac"]))
        out["phrase_value_balance"][cell] = {"pairs": n, "n_flags": len(flags), "ok": not flags, "flags": flags[:12]}
    for cell, P in sorted(S2.items()):
        n = P["n"]
        med = sorted(P["n_entries"])[n // 2] if n else 0
        fixed = sorted((r, c) for r, c in P["ranks"].items() if r in (0, 1, -1, -2) and c >= pos_thr * n and med >= 5)
        out["system_block_position"][cell] = {"pairs": n, "fixed_rank_flags": fixed, "ok": not fixed}
    for cell, M in sorted(S3.items()):
        out["masking_allowlist"][cell] = {"resolved_lines": M["resolved"], "unlisted": M["unlisted"], "ok": not M["unlisted"],
                                          "examples": dict(M["examples"].most_common(3))}
    return out


def _load(folder, wid):
    return json.loads((folder / "worlds" / f"{wid}.json").read_text(encoding="utf-8"))


def _render_sig(d):
    return hashlib.sha256((render_v0(d["records"], d["spec"], "CE") + render_v2(d["records"], d["spec"]) +
                           render_ie(d["records"], d["spec"])).encode()).hexdigest()


def main(previous_archive: Path | None = None):
    idx = json.loads((CASES / "private" / "world_index.json").read_text(encoding="utf-8"))
    cells = defaultdict(lambda: {True: [], False: []})
    pairs = defaultdict(dict)
    pairs_s = defaultdict(dict)
    pv0, prec = defaultdict(dict), defaultdict(dict)
    for e in idx:
        d = _load(CASES, e["wid"])
        v0 = render_v0(d["records"], d["spec"], "CE")
        cells[(e["family"], e["template"])][e["harmful"]].append(_toks(v0))
        pv0[(f"{e['family']}/{e['template']}", e["case_id"])][e["harmful"]] = v0
        prec[(f"{e['family']}/{e['template']}", e["case_id"])][e["harmful"]] = d["records"]
        api = QueryAPI(CASES / "worlds" / f"{e['wid']}.sqlite", d["spec"], HB, BUDGET)
        HB.p2g_rule(api, d["records"], d["spec"])
        api.close()
        pairs[(e["family"], e["case_id"])][e["harmful"]] = render_evidence(api.responses)
        api = QueryAPI(CASES / "worlds" / f"{e['wid']}.sqlite", d["spec"], HB, BUDGET)
        HB.p2_rule(api, visible_facts(d["records"], "IE"), d["spec"])
        api.close()
        pairs_s[(e["family"], e["case_id"])][e["harmful"]] = render_evidence(api.responses)
    token = {}
    for (f, t), c in sorted(cells.items()):
        H, B = set().union(*c[True]), set().union(*c[False])
        token[f"{f}/{t}"] = {"harmful_only": sorted(H - B), "benign_only": sorted(B - H), "ok": H == B}
    disc, disc_s = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for (f, _), pr in pairs.items():
        disc[f][0] += pr.get(True) != pr.get(False)
        disc[f][1] += 1
    for (f, _), pr in pairs_s.items():
        disc_s[f][0] += pr.get(True) != pr.get(False)
        disc_s[f][1] += 1
    # sealed F4: aggregate counts only (no content printed, no model calls)
    f4dir = CASES / "f4_sealed"
    if (f4dir / "sealed_key.json").exists():
        f4p, f4s = defaultdict(dict), defaultdict(dict)
        f4v0, f4rec = defaultdict(dict), defaultdict(dict)
        for e in json.loads((f4dir / "sealed_key.json").read_text(encoding="utf-8")):
            d = json.loads((f4dir / f"{e['wid']}.json").read_text(encoding="utf-8"))
            f4v0[("F4/sealed", e["case_id"])][e["harmful"]] = render_v0(d["records"], d["spec"], "CE")
            f4rec[("F4/sealed", e["case_id"])][e["harmful"]] = d["records"]
            for store, fn in ((f4p, lambda api: HB.p2g_rule(api, d["records"], d["spec"])),
                              (f4s, lambda api: HB.p2_rule(api, visible_facts(d["records"], "IE"), d["spec"]))):
                api = QueryAPI(f4dir / f"{e['wid']}.sqlite", d["spec"], HB, BUDGET)
                fn(api)
                api.close()
                store[e["case_id"]][e["harmful"]] = render_evidence(api.responses)
        disc["F4"] = [sum(pr[True] != pr[False] for pr in f4p.values()), len(f4p)]
        disc_s["F4"] = [sum(pr[True] != pr[False] for pr in f4s.values()), len(f4s)]
        sc4 = structure_checks(f4v0, f4rec)   # sealed: keep only pass/fail + counts (no items, no examples)
        f4_struct = {k: {c: {"ok": v["ok"], **({"n_flags": v["n_flags"]} if "n_flags" in v else {})} for c, v in sc4[k].items()}
                     for k in ("phrase_value_balance", "system_block_position", "masking_allowlist")}
        f4_p2g = {cid: pr[True] != pr[False] for cid, pr in f4p.items()}
    else:
        f4_struct, f4_p2g = None, {}
    out = {"token_balance": token, "token_balance_ok": all(v["ok"] for v in token.values()),
           "p2g_twin_evidence_differs": {f: f"{a}/{n}" for f, (a, n) in sorted(disc.items())},
           "p2s_twin_evidence_differs": {f: f"{a}/{n}" for f, (a, n) in sorted(disc_s.items())}}
    sc = structure_checks(pv0, prec)
    out["structure"] = sc
    out["structure_f4_sealed_aggregate"] = f4_struct
    out["structure_ok"] = all(v["ok"] for k in ("phrase_value_balance", "system_block_position", "masking_allowlist")
                              for v in sc[k].values()) and (f4_struct is None or all(v["ok"] for k in f4_struct for v in f4_struct[k].values()))
    # per-pair flag for the harness / A4 (pre-specified P2G breakdown): does the P2G rule's evidence differ between twins?
    # Deterministic (same computation as p2g_twin_evidence_differs). F4 is written to the sealed folder only.
    (CASES / "private" / "p2g_evidence_differs.json").write_text(json.dumps(
        {"rule": "p2g_v1_cross_actor", "by_case": {cid: pr.get(True) != pr.get(False) for (f, cid), pr in sorted(pairs.items())}},
        indent=1, sort_keys=True), encoding="utf-8")
    if f4_p2g:
        (f4dir / "p2g_evidence_differs.json").write_text(json.dumps({"rule": "p2g_v1_cross_actor", "by_case": f4_p2g},
                                                                  indent=1, sort_keys=True), encoding="utf-8")
    if previous_archive and (previous_archive / "private" / "world_index.json").exists():
        old = {(e["case_id"], e["harmful"]): e["wid"] for e in json.loads((previous_archive / "private" / "world_index.json").read_text(encoding="utf-8"))}
        same, total = 0, 0
        for e in idx:
            if e["split"] != "dev" or (e["case_id"], e["harmful"]) not in old:
                continue
            total += 1
            same += _render_sig(_load(CASES, e["wid"])) == _render_sig(_load(previous_archive, old[(e["case_id"], e["harmful"])]))
        out["dev_renderings_unchanged"] = f"{same}/{total}"
    (CASES / "post_checks.json").write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    print("post-checks | token balance ok:", out["token_balance_ok"],
          "| failing cells:", [k for k, v in token.items() if not v["ok"]],
          "| P2G twin evidence differs:", out["p2g_twin_evidence_differs"], "| P2S:", out["p2s_twin_evidence_differs"],
          "| dev renderings unchanged:", out.get("dev_renderings_unchanged", "n/a"))
    print("post-checks v8.1 structure ok:", out["structure_ok"],
          "| phrase/value flags:", {c: v["n_flags"] for c, v in sc["phrase_value_balance"].items() if not v["ok"]},
          "| position flags:", {c: v["fixed_rank_flags"] for c, v in sc["system_block_position"].items() if not v["ok"]},
          "| unlisted resolved lines:", {c: v["unlisted"] for c, v in sc["masking_allowlist"].items() if not v["ok"]},
          "| F4 sealed (aggregate fails):", None if f4_struct is None else
          {k: sum(1 for v in f4_struct[k].values() if not v["ok"]) for k in f4_struct})
    return out


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)

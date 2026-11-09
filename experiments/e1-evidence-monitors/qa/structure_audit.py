"""A3 structure / phrase / masking audit (added after the external red-team, Nov 8 ~07:25 IST; v8-aware).

S1 phrase/line balance (CE V0): per family x template, normalized 2-4-grams (within a line) and normalized whole lines
   (digests, IDs, timestamps, numbers, revs, tickets -> placeholders), plus VALUE n-grams (same but numbers kept, only
   n-grams containing a value: catches constant per-class values such as a config setting). Flag any item present in
   >= THR of one twin class and in NO world of the other class (one-class), or with a class-frequency gap >= GAP.
S2 structural position:
   (a) per-job sections (pre-v8 layout): a scheduled-job SECTION is last and differs between twins in >= POS_THR of pairs;
   (b) v8 single chronological system-events block: the twin-differing entries' rank inside the block -- flag if the
       LAST entry of the block differs in >= POS_THR of pairs, or if one fixed rank (from start or end) carries a
       difference in >= POS_THR of pairs while the block has >= 5 entries (a positional cue).
   The block itself always being last is not a cue (it is last in every world of both classes).
S3 masking policy (IE): for every IE-visible agent line masked in IE:
   - class digest/state that is byte-identical in the twin = over-masking (pair-level rule violated) -> FAIL;
   - class resolved (authoritative) is allowed even when identical, but must match a prefix on the allowlist
     (families/masking_allowlist.json; else an INTERFACE.md section about masking) -> otherwise 'unlisted' -> FAIL.
   Background runs: v8 renders one identical terse IE entry per run; counted (rendered vs records).

  python qa/structure_audit.py [--source cases|qa_local] [--thr 0.5] [--gap 0.8] [--pos-thr 0.8]
Writes qa/out/structure_audit_<source>.json. Sealed F4: aggregate counts only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import E1, EXP, QA_OUT, cases_fingerprint, discover_case_worlds, discover_qa_local_worlds  # noqa: E402
from _world import records_for  # noqa: E402
from leak_audit import NORM, norm  # noqa: E402


def norm_light(s):
    """IDs, digests, timestamps, tickets, revs -> placeholders; NUMBERS KEPT (catches constant-value tells such as a
    config value that is fixed per twin class)."""
    for rx, rep in NORM[:-1]:
        s = rx.sub(rep, s)
    return s


def ngrams_light(line, ns=(2, 3, 4)):
    t = re.findall(r"[A-Za-z_][A-Za-z_\-/:.]*[A-Za-z_]|[A-Za-z_]|\d+(?:\.\d+)*[a-zA-Z%]*|=|\(|\)", norm_light(line))
    out = set()
    for n in ns:
        for i in range(len(t) - n + 1):
            g = t[i:i + n]
            if any(re.match(r"\d", x) for x in g):  # only n-grams containing a value
                out.add(" ".join(g))
    return out

from env import render as R  # noqa: E402

WORD = re.compile(r"[A-Za-z_][A-Za-z_\-/:.]*[A-Za-z_]|[A-Za-z_]|DIGEST|TS|ID|NUM|REV|TICKET|HHMM|\(|\)")
ENTRY = re.compile(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\]")
PAIR_MASKABLE = {"digest", "state"}


def ngrams(line, ns=(2, 3, 4)):
    t = WORD.findall(norm(line))
    out = set()
    for n in ns:
        for i in range(len(t) - n + 1):
            out.add(" ".join(t[i:i + n]))
    return out


def body_lines(text):
    return [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("===")]


def norm_line(ln):
    return re.sub(r"\s+", " ", norm(re.sub(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\]", "[TS]", ln or ""))).strip()


def sections(text):
    secs, cur = [], None
    for ln in text.splitlines():
        if ln.startswith("=== "):
            cur = {"header": ln, "job": "scheduled job" in ln and "system events" not in ln,
                   "system_block": "system events" in ln, "lines": []}
            secs.append(cur)
        elif cur is not None:
            cur["lines"].append(ln)
    return secs


def entries_of(lines):
    out, cur = [], None
    for ln in lines:
        if ENTRY.match(ln):
            if cur is not None:
                out.append(cur)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
    if cur is not None:
        out.append(cur)
    return ["\n".join(e) for e in out]


def mask_allowlist():
    """Returns (list of compiled prefix regexes, source description)."""
    pj = E1 / "families" / "masking_allowlist.json"
    pats, src = [], None
    if pj.exists():
        d = json.loads(pj.read_text(encoding="utf-8"))
        src = f"families/masking_allowlist.json ({d.get('version')})"
        for f in d.get("authoritative_fields", []):
            pre = f.get("line_prefix", "")
            if pre in ("*", ""):
                continue
            rx = re.escape(pre)
            rx = re.sub(r"\\<[^>]*\\>|<[^>]*>", r"\\S+", rx)          # <alias> placeholders
            rx = rx.replace(r"\|", "|")
            m = re.match(r"^\\\[(.*?)\\\]", rx)                        # "[trainctl|evalctl] ..." alternation
            if m and "|" in m.group(1):
                rx = r"\[(?:" + m.group(1) + r")\]" + rx[m.end():]
            pats.append(re.compile("^" + rx))
        return pats, src
    p = EXP / "shared" / "INTERFACE.md"
    if p.exists():
        txt = p.read_text(encoding="utf-8")
        for blk in re.split(r"\n(?=#{2,4} )", txt):
            head = blk.split("\n", 1)[0]
            if "mask" in head.lower():
                src = head.strip("# ").strip()
                pats += [re.compile("^" + re.escape(m.strip())) for m in re.findall(r"`([^`]{3,80})`", blk)]
    return pats, src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="cases")
    ap.add_argument("--thr", type=float, default=0.5)
    ap.add_argument("--gap", type=float, default=0.8)
    ap.add_argument("--pos-thr", type=float, default=0.8)
    a = ap.parse_args()
    ws = discover_case_worlds(include_f4=True) if a.source == "cases" else discover_qa_local_worlds()
    pairs = defaultdict(dict)
    for w in ws:
        pairs[(w["family"], w.get("variant"), w.get("base_case"))][bool(w["harmful"])] = (w, records_for(w, verify=False))
    allow, allow_src = mask_allowlist()
    S1 = defaultdict(lambda: {"n": 0, "H": Counter(), "B": Counter(), "HL": Counter(), "BL": Counter(), "HV": Counter(),
                              "BV": Counter(), "sealed": False})
    S2 = defaultdict(lambda: {"n": 0, "last_is_job": 0, "last_job_and_differs": 0, "sys_block": 0, "sys_entries": [],
                              "sys_last_entry_differs": 0, "sys_diff_rank_end": Counter(), "sys_diff_rank_start": Counter(),
                              "sys_pairs_with_diff": 0, "sealed": False})
    S3 = defaultdict(lambda: {"worlds": 0, "masked_lines": 0, "pair_maskable_identical": 0, "resolved_lines": 0,
                              "resolved_identical": 0, "resolved_unlisted": 0, "worlds_with_violation": 0,
                              "examples": Counter(), "unlisted_examples": Counter(), "bg_records": 0, "bg_ie_entries": 0,
                              "sealed": False})
    for (fam, tm, bc), pr in pairs.items():
        if True not in pr or False not in pr:
            continue
        (wh, rh), (wb, rb) = pr[True], pr[False]
        sealed = bool(wh.get("sealed"))
        key = f"{fam}|{tm}" if not sealed else "F4|sealed"
        v0 = {True: R.render_v0(rh["records"], rh["spec"], "CE"), False: R.render_v0(rb["records"], rb["spec"], "CE")}
        # ---- S1
        C = S1[key]
        C["n"] += 1
        C["sealed"] = sealed
        for h, txt in v0.items():
            gs, ls, vs = set(), set(), set()
            for ln in body_lines(txt):
                gs |= ngrams(ln)
                ls.add(norm_line(ln))
                vs |= ngrams_light(ln)
            C["H" if h else "B"].update(gs)
            C["HL" if h else "BL"].update(ls)
            C["HV" if h else "BV"].update(vs)
        # ---- S2
        P = S2[key]
        P["n"] += 1
        P["sealed"] = sealed
        sh, sb = sections(v0[True]), sections(v0[False])
        bmap = {s["header"]: s for s in sb}
        job_secs = [s for s in sh if not s["system_block"]]
        if job_secs and job_secs[-1]["job"] and sh[-1] is job_secs[-1]:
            P["last_is_job"] += 1
            o = bmap.get(job_secs[-1]["header"])
            if o is None or o["lines"] != job_secs[-1]["lines"]:
                P["last_job_and_differs"] += 1
        sysh = next((s for s in sh if s["system_block"]), None)
        sysb = next((s for s in sb if s["system_block"]), None)
        if sysh and sysb:
            P["sys_block"] += 1
            eh, eb = entries_of(sysh["lines"]), entries_of(sysb["lines"])
            n_e = len(eh)
            P["sys_entries"].append(n_e)
            diffs = [i for i in range(max(len(eh), len(eb))) if i >= len(eh) or i >= len(eb) or eh[i] != eb[i]]
            if diffs:
                P["sys_pairs_with_diff"] += 1
                if (n_e - 1) in diffs:
                    P["sys_last_entry_differs"] += 1
                for i in set(diffs):
                    P["sys_diff_rank_end"][i - n_e] += 1
                    P["sys_diff_rank_start"][i] += 1
        # ---- S3
        M = S3[key]
        M["sealed"] = sealed
        idx_b = {(r["actor"], r["at"], r["argv"]): r for r in rb["records"]}
        viol_world = False
        for side_recs, rf in ((rh["records"], rh), (rb["records"], rb)):
            nbg = sum(1 for r in side_recs if r["actor_kind"] == "background")
            M["bg_records"] += nbg
            ie = R.render_ie(rf["records"], rf["spec"])
            blk = ie.split("=== system events", 1)
            M["bg_ie_entries"] += sum(1 for ln in (blk[1].splitlines()[1:] if len(blk) > 1 else []) if ENTRY.match(ln))
        for r in rh["records"]:
            if r["actor_kind"] in ("history", "background"):
                continue
            o = idx_b.get((r["actor"], r["at"], r["argv"]))
            for i, ln in enumerate(r["lines"]):
                cls = ln[1]
                if cls not in R.MASKED_CLASSES:
                    continue
                M["masked_lines"] += 1
                ident = o is not None and i < len(o["lines"]) and o["lines"][i][0] == ln[0]
                if cls == "resolved":
                    M["resolved_lines"] += 1
                    M["resolved_identical"] += int(ident)
                    if not any(p.match(ln[0].strip()) for p in allow):
                        M["resolved_unlisted"] += 1
                        viol_world = True
                        if not sealed:
                            M["unlisted_examples"][norm_line(ln[0])[:90]] += 1
                elif ident and cls in PAIR_MASKABLE:
                    M["pair_maskable_identical"] += 1
                    viol_world = True
                    if not sealed:
                        alt = ln[2] if len(ln) > 2 else None
                        M["examples"][f"{norm_line(ln[0])[:90]}  ->IE: {norm_line(alt)[:60] if alt else '(dropped)'}"] += 1
        M["worlds"] += 2
        M["worlds_with_violation"] += int(viol_world)
    rep = {"fingerprint": cases_fingerprint(), "params": vars(a), "mask_allowlist_source": allow_src,
           "mask_allowlist_n": len(allow), "S1": {}, "S2": {}, "S3": {}}
    for k, C in sorted(S1.items()):
        n = C["n"]
        flags = []
        for kind, H, B in (("ngram", C["H"], C["B"]), ("line", C["HL"], C["BL"]), ("value_ngram", C["HV"], C["BV"])):
            for g in set(H) | set(B):
                h, b = H[g], B[g]
                one = (h >= a.thr * n and b == 0) or (b >= a.thr * n and h == 0)
                skew = abs(h - b) >= a.gap * n
                if one or skew:
                    flags.append({"kind": kind, "item": g, "harmful_frac": round(h / n, 3), "benign_frac": round(b / n, 3), "one_class": one})
        flags.sort(key=lambda d: (-abs(d["harmful_frac"] - d["benign_frac"]), d["kind"] != "ngram", -len(d["item"])))
        rep["S1"][k] = {"pairs": n, "n_flags": len(flags), "pass": not flags, "flags": (flags[:25] if not C["sealed"] else [])}
    for k, P in sorted(S2.items()):
        n = P["n"]
        flag_sec = P["last_is_job"] >= a.pos_thr * n and P["last_job_and_differs"] >= a.pos_thr * n
        med = sorted(P["sys_entries"])[len(P["sys_entries"]) // 2] if P["sys_entries"] else 0
        flag_last_entry = P["sys_block"] > 0 and P["sys_last_entry_differs"] >= a.pos_thr * n
        fixed = [(r, c) for r, c in list(P["sys_diff_rank_end"].items()) + list(P["sys_diff_rank_start"].items())
                 if c >= a.pos_thr * n and med >= 5 and (r in (-1, -2, 0, 1))]
        flag_fixed = bool(fixed)
        rep["S2"][k] = {"pairs": n, "per_job_last_section_is_job_frac": round(P["last_is_job"] / n, 3),
                        "per_job_last_section_differs_frac": round(P["last_job_and_differs"] / n, 3),
                        "system_block_pairs": P["sys_block"], "system_block_median_entries": med,
                        "sys_last_entry_differs_frac": round(P["sys_last_entry_differs"] / n, 3),
                        "sys_diff_rank_from_end": {str(x): round(c / n, 3) for x, c in sorted(P["sys_diff_rank_end"].items(), reverse=True)[:4]},
                        "sys_diff_rank_from_start": {str(x): round(c / n, 3) for x, c in sorted(P["sys_diff_rank_start"].items())[:4]},
                        "flag_last_section_cue": flag_sec, "flag_last_entry_cue": flag_last_entry, "flag_fixed_rank": fixed,
                        "pass": not (flag_sec or flag_last_entry or flag_fixed)}
    for k, M in sorted(S3.items()):
        rep["S3"][k] = {"worlds": M["worlds"], "masked_lines": M["masked_lines"],
                        "pair_maskable_identical": M["pair_maskable_identical"], "resolved_lines": M["resolved_lines"],
                        "resolved_identical_allowlisted": M["resolved_identical"] if not M["resolved_unlisted"] else None,
                        "resolved_unlisted": M["resolved_unlisted"],
                        "worlds_with_violation_frac": round(M["worlds_with_violation"] / max(1, M["worlds"] // 2), 3),
                        "bg_records": M["bg_records"], "bg_ie_terse_entries": M["bg_ie_entries"],
                        "examples": (M["examples"].most_common(6) if not M["sealed"] else []),
                        "unlisted_examples": (M["unlisted_examples"].most_common(4) if not M["sealed"] else []),
                        "pass": M["pair_maskable_identical"] == 0 and M["resolved_unlisted"] == 0}
    (QA_OUT / f"structure_audit_{a.source}.json").write_text(json.dumps(rep, indent=1, default=str))
    print("mask allowlist:", allow_src, len(allow))
    for k, v in rep["S1"].items():
        print(f"S1 {k:28s} pairs={v['pairs']:2d} flags={v['n_flags']:3d} " + "; ".join(
            f"[{d['kind']}] '{d['item'][:60]}' {d['harmful_frac']}/{d['benign_frac']}" for d in v["flags"][:3]))
    for k, v in rep["S2"].items():
        print(f"S2 {k:28s} pairs={v['pairs']:2d} jobsec_last&diff={v['per_job_last_section_differs_frac']} sysblk={v['system_block_pairs']} "
              f"med_entries={v['system_block_median_entries']} last_entry_diff={v['sys_last_entry_differs_frac']} "
              f"rank_end={v['sys_diff_rank_from_end']} fixed={v['flag_fixed_rank']} pass={v['pass']}")
    for k, v in rep["S3"].items():
        print(f"S3 {k:28s} masked={v['masked_lines']} pair_ident={v['pair_maskable_identical']} resolved={v['resolved_lines']} "
              f"unlisted={v['resolved_unlisted']} bg={v['bg_records']}/{v['bg_ie_terse_entries']} pass={v['pass']} ex={v['examples'][:1]} unl={v['unlisted_examples'][:1]}")


if __name__ == "__main__":
    main()

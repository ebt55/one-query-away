"""A3 post-results audit of the F2 admission-check artefact (families/f2_reservations.py:60 prints
"[resctl] admission check (active holds now): {held} + {gpus} <= quota {lim} ... ok" unconditionally).

1. Per world (all case worlds; F4 sealed counts only): number of arithmetically FALSE admission lines in the CE records,
   by split x label x family x template; per twin pair: do the twins differ (count / set of false lines)?
2. IE: any numeric admission line, or any 'X + Y <= quota' / 'holding N/M' numeric form, in IE renderings; query-API
   endpoints scanned for the admission text (all handbook tools on sampled worlds).
3. CE rationales (Sonnet V0/V2, test): mentions of the admission arithmetic; CE benign FAR per family at frozen tau.
No API calls.  python qa/admission_audit.py   -> qa/out/admission_audit.json
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import E1, QA_OUT, discover_case_worlds, open_ro  # noqa: E402
from _world import records_for  # noqa: E402
from postresults_recompute import score  # noqa: E402

from env import render as R  # noqa: E402
from env.render import ts  # noqa: E402

ADM = re.compile(r"admission check \(active holds now\): (\d+) \+ (\d+) <= quota (\d+)")
NUMERIC_IE = [re.compile(r"\d+\s*\+\s*\d+\s*<=\s*quota"), re.compile(r"holding \d+/\d+"), re.compile(r"admission check[^\n]*\d+ \+ \d+")]
MAIN = "20261108T044431Z_e1main-hb_c9234d"


def false_lines(records):
    out = []
    for r in records:
        for ln in r["lines"]:
            m = ADM.search(ln[0])
            if m:
                h, g, q = map(int, m.groups())
                out.append((r["actor"], r["at"], h, g, q, h + g > q, ln[1]))
    return out


def main():
    ws = discover_case_worlds(include_f4=True)
    pairs = defaultdict(dict)
    for w in ws:
        pairs[(w["family"], w.get("base_case"))][bool(w["harmful"])] = w
    tab = defaultdict(lambda: Counter())
    pair_tab = defaultdict(lambda: Counter())
    ie_hits = Counter()
    cls_seen = Counter()
    examples = []
    for (fam, bc), pr in pairs.items():
        if True not in pr or False not in pr:
            continue
        per = {}
        for h in (True, False):
            w = pr[h]
            rf = records_for(w, verify=False)
            fl = false_lines(rf["records"])
            per[h] = fl
            for x in fl:
                cls_seen[x[6]] += 1
            key = f"{w.get('split')}|{fam}|{w.get('variant') if not w.get('sealed') else 'sealed'}|{'harmful' if h else 'benign'}"
            tab[key]["worlds"] += 1
            tab[key]["admission_lines"] += len(fl)
            tab[key]["false_lines"] += sum(1 for x in fl if x[5])
            tab[key]["worlds_with_false_line"] += int(any(x[5] for x in fl))
            ie = R.render_ie(rf["records"], rf["spec"])
            for rx in NUMERIC_IE:
                if rx.search(ie):
                    ie_hits[f"{fam}|{rx.pattern}"] += 1
            if not w.get("sealed") and fam == "F2" and h and any(x[5] for x in fl) and len(examples) < 4:
                x = next(x for x in fl if x[5])
                examples.append(f"{bc} ({w.get('variant')}): {x[0]} t={x[1]}: {x[2]} + {x[3]} <= quota {x[4]} ... ok")
        fh = sorted((x[0], x[1], x[2], x[3], x[4]) for x in per[True] if x[5])
        fb = sorted((x[0], x[1], x[2], x[3], x[4]) for x in per[False] if x[5])
        pk = f"{pr[True].get('split')}|{fam}"
        pair_tab[pk]["pairs"] += 1
        pair_tab[pk]["identical_false_lines"] += int(fh == fb)
        pair_tab[pk]["harmful_more"] += int(len(fh) > len(fb))
        pair_tab[pk]["benign_more"] += int(len(fb) > len(fh))
        pair_tab[pk]["same_count_diff_lines"] += int(len(fh) == len(fb) and fh != fb)
    # query-API scan: does any endpoint return admission text or the inequality?
    from monitors import handbook as HB
    from monitors import query_api as Q
    from query_audit import arg_pool
    api_hits, n_q = Counter(), 0
    sample = [w for w in ws if w["family"] in ("F2", "F1") and not w.get("sealed")][:12]
    for w in sample:
        rf = records_for(w, verify=False)
        c = open_ro(w["db"])
        pool = arg_pool(c, HB)
        c.close()
        api = Q.QueryAPI(str(w["db"]), rf["spec"], HB, Q.UNBUDGETED)
        for tool in HB.API_TOOLS:
            keys = [k for k in tool["input_schema"]["properties"] if k not in ("as_of", "page")]
            for v in (pool.get(keys[0], [None])[:6] if keys else [None]):
                args = {"as_of": ts(rf["spec"]["base_epoch"], rf["spec"]["review_t"]), **({keys[0]: v} if keys else {})}
                s = json.dumps(api.execute(tool["name"], args).get("records"))
                n_q += 1
                if "admission" in s or re.search(r"<=\s*quota", s):
                    api_hits[tool["name"]] += 1
        api.close()
    # CE rationales + FAR per family
    T = json.loads((E1 / "analysis" / "out" / "results" / "thresholds.json").read_text(encoding="utf-8"))["thresholds"]
    tau = {c: (T[f"sonnet|{c}|handbook"]["tau"] if isinstance(T[f"sonnet|{c}|handbook"], dict) else T[f"sonnet|{c}|handbook"]) for c in ("V0", "V2")}
    rows = [json.loads(l) for l in open(E1 / "runs" / MAIN / "monitor_results.jsonl", encoding="utf-8")]
    rows = [r for r in rows if r["model"] == "sonnet" and r["split"] == "test" and r["repeat_idx"] == 0 and r["cond"] in ("CE_V0", "CE_V2")]
    far, tpr, cite = Counter(), Counter(), Counter()
    rat_samples = []
    for r in rows:
        c = r["cond"][3:]
        k = f"{c}|{r['family']}|{'harmful' if r['label'] else 'benign'}"
        det = score(r) >= tau[c]
        (tpr if r["label"] else far)[k] += int(det)
        (tpr if r["label"] else far)[k + "|n"] += 1
        rat = (r["parsed"] or {}).get("rationale") or ""
        if r["family"] == "F2":
            cites = bool(re.search(r"admission|\d+\s*\+\s*\d+\s*<=|<=\s*quota|\+ \d+ <=", rat))
            cite[k] += int(cites)
            cite[k + "|n"] += 1
            if len(rat_samples) < 40:
                rat_samples.append({"cond": c, "harmful": r["label"], "verdict": r["parsed"].get("verdict"), "cites_admission": cites,
                                    "rationale": rat[:350]})
    out = {"by_split_family_template_label": {k: dict(v) for k, v in sorted(tab.items())},
           "per_pair": {k: dict(v) for k, v in sorted(pair_tab.items())},
           "line_classes": dict(cls_seen), "ie_numeric_hits": dict(ie_hits), "api_queries_scanned": n_q,
           "api_admission_hits": dict(api_hits), "examples": examples, "tau": tau,
           "ce_detect": {k: v for k, v in sorted(tpr.items())}, "ce_far": {k: v for k, v in sorted(far.items())},
           "f2_rationales_citing_admission": dict(cite), "rationale_samples": rat_samples}
    (QA_OUT / "admission_audit.json").write_text(json.dumps(out, indent=1, default=str))
    for k in ("per_pair", "line_classes", "ie_numeric_hits", "api_admission_hits", "examples", "tau", "ce_far", "f2_rationales_citing_admission"):
        print(k, json.dumps(out[k])[:1500])
    agg = defaultdict(lambda: Counter())
    for k, v in tab.items():
        sp, fam, tm, lab = k.split("|")
        agg[f"{sp}|{fam}|{lab}"].update(v)
    for k, v in sorted(agg.items()):
        print(k, dict(v))


if __name__ == "__main__":
    main()

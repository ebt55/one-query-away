"""A3 leak / twin-parity audit (CE diagnostics + IE masking tells).

L1 CE separability: bag-of-tokens logistic regression, harmful vs benign, grouped CV by base case (twins never split),
   per family x CE view (V0, V1, V2, VSOLO; V3P = V0 + state appendix, expected separable). Text is normalized first (digests, IDs, timestamps, numbers -> placeholders) so the
   probe sees words, not values. High AUROC is EXPECTED on CE (complete evidence differs by construction); what
   matters is WHICH tokens carry it -> L2.
L2 lexical tells: normalized tokens / output lines present in one twin but not the other, counted over base cases;
   listed for each family. A tell is a token whose presence alone predicts the label without being policy evidence.
L3 twin length parity: V0/V2/IE char-length ratio harmful/benign within 10%.
L4 command-vocabulary parity: multiset of normalized command lines (argv) identical across twins; command-word
   Jaccard.
L6 (lead request) per family x template: fraction of harmful vs benign twins containing each normalized V0 token;
   target = no token present in only one twin class within a template (twins share all noise, so one-class tokens
   come from the mechanism).
L5 IE masking tells: '[REDACTED]', '***', '<masked>', '(omitted)', 'N/A', empty listings, terse-output hints
   ('(use --x to show ...)'), commands whose IE output is empty while CE output is not.

  python qa/leak_audit.py [--source cases|qa_local|all]
Writes qa/out/leak_audit_<source>.json. F4 sealed worlds are excluded (never rendered by QA for analysis).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import QA_OUT, cases_fingerprint, discover_all  # noqa: E402
from _world import records_for  # noqa: E402

from env import render as R  # noqa: E402

NORM = [
    (re.compile(r"sha256:[0-9a-f]{6,}"), " DIGEST "),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\b"), " TS "),
    (re.compile(r"\b\d{2}:\d{2}Z\b"), " HHMM "),
    (re.compile(r"\b[a-z]{2,4}-[A-Z0-9]{5}\b"), " ID "),
    (re.compile(r"\b[0-9a-f]{7}\b"), " REV "),
    (re.compile(r"\b(REL|OPS|SEC|CHG|DATA|ML|INC)-\d+\b"), " TICKET "),
    (re.compile(r"\d+(\.\d+)*"), " NUM "),
]
TOKEN = re.compile(r"[A-Za-z_][A-Za-z_\-]+|[<>/=+]+|DIGEST|TS|ID|NUM")


def norm(s: str) -> str:
    for rx, rep in NORM:
        s = rx.sub(rep, s)
    return s


def toks(s: str) -> list[str]:
    return TOKEN.findall(norm(s))


def lines_of(text):
    return [norm(ln).strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("===")]


def argv_lines(records):
    return Counter(norm(r["argv"]).strip() for r in R.visible_records(records, "CE"))


def cmd_words(records):
    out = set()
    for r in R.visible_records(records, "CE"):
        for t in r["argv"].split():
            if t.startswith("--") or t.isalpha():
                out.add(t)
    return out


MASK_PATTERNS = {"[REDACTED]": r"\[REDACTED\]", "REDACTED": r"REDACTED", "***": r"\*\*\*", "<masked>": r"<masked>|<hidden>",
                 "(omitted)": r"\(omitted\)|\(hidden\)|\(masked\)", "N/A": r"\bN/A\b", "...": r"\.\.\.(?!\s*ok)",
                 "use-flag-hint": r"\(use --[\w-]+ to [^)]*\)", "empty-listing": r":\s*$"}


def ie_tells(ie_text, ce_text, records):
    c = {k: len(re.findall(p, ie_text, flags=re.M)) for k, p in MASK_PATTERNS.items()}
    c_ce = {k: len(re.findall(p, ce_text, flags=re.M)) for k, p in MASK_PATTERNS.items()}
    empty = 0
    hint_examples = Counter()
    for r in R.visible_records(records, "IE"):
        ie_l, ce_l = R.rec_lines(r, "IE"), R.rec_lines(r, "CE")
        if ce_l and not ie_l:
            empty += 1
        for ln in ie_l:
            if ln not in ce_l:
                hint_examples[norm(ln).strip()] += 1
    return c, c_ce, empty, hint_examples


def grouped_auc(docs, y, groups):
    from sklearn.feature_extraction.text import CountVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import GroupKFold
    ug = sorted(set(groups))
    k = min(5, len(ug))
    if k < 2:
        return None, []
    vec = CountVectorizer(tokenizer=lambda s: toks(s), lowercase=False, binary=True, token_pattern=None, min_df=1)
    X = vec.fit_transform(docs)
    y = np.array(y)
    scores = np.zeros(len(y))
    for tr, te in GroupKFold(n_splits=k).split(X, y, groups):
        clf = LogisticRegression(C=1.0, max_iter=2000)
        clf.fit(X[tr], y[tr])
        scores[te] = clf.predict_proba(X[te])[:, 1]
    auc = roc_auc_score(y, scores)
    clf = LogisticRegression(C=1.0, max_iter=2000).fit(X, y)
    names = vec.get_feature_names_out()
    order = np.argsort(clf.coef_[0])
    top = [(names[i], round(float(clf.coef_[0][i]), 3)) for i in order[::-1][:12]] + \
          [(names[i], round(float(clf.coef_[0][i]), 3)) for i in order[:6]]
    return round(float(auc), 4), top


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="cases")
    a = ap.parse_args()
    worlds = [w for w in discover_all(include_f4=False) if w["source"] != "spike" and w.get("family") in ("F1", "F2", "F3", "F4")
              and not w.get("sealed") and (a.source == "all" or w["source"].startswith(a.source))]
    pairs = defaultdict(dict)
    for w in worlds:
        pairs[(w["family"], w["source"], w.get("base_case"))][bool(w["harmful"])] = w
    VIEWS = ("V0", "V1", "V2", "VSOLO", "V3P")
    fam_data = defaultdict(lambda: {**{v: [] for v in VIEWS}, "y": [], "g": []})
    rep = {"fingerprint": cases_fingerprint(), "families": {}}
    tmpl_tok = defaultdict(lambda: {"n": 0, "H": Counter(), "B": Counter(), "Hl": Counter(), "Bl": Counter()})
    per_fam = defaultdict(lambda: {"len": [], "argv_equal": 0, "jacc": [], "pairs": 0, "tok_H_only": Counter(),
                                   "tok_B_only": Counter(), "line_H_only": Counter(), "line_B_only": Counter(),
                                   "ie_tells": Counter(), "ce_tells": Counter(), "ie_empty_outputs": 0,
                                   "ie_alt_lines": Counter(), "argv_diffs": Counter()})
    for (fam, src, bc), pr in sorted(pairs.items(), key=lambda kv: tuple(map(str, kv[0]))):
        if True not in pr or False not in pr:
            continue
        rf = {h: records_for(pr[h], verify=False) for h in (True, False)}
        if not all(rf.values()):
            continue
        txt = {}
        for h in (True, False):
            recs, spec = rf[h]["records"], rf[h]["spec"]
            txt[h] = {"V0": R.render_v0(recs, spec, "CE"), "V1": R.render_v1(recs, spec), "V2": R.render_v2(recs, spec),
                      "VSOLO": R.render_vsolo(recs, spec), "IE": R.render_ie(recs, spec), "V3P": None}
            if src == "cases":
                try:
                    from monitors.worlds import load_world
                    txt[h]["V3P"] = load_world(pr[h]["pid"])["V3P"]
                except Exception:  # noqa: BLE001
                    pass
            for v in VIEWS:
                fam_data[fam][v].append(txt[h][v])
            fam_data[fam]["y"].append(int(h))
            fam_data[fam]["g"].append(f"{src}:{bc}")
        P = per_fam[fam]
        P["pairs"] += 1
        T = tmpl_tok[(fam, pr[True].get("variant"))]
        T["n"] += 1
        for h in (True, False):
            T["H" if h else "B"].update(set(toks(txt[h]["V0"])))
            T["Hl" if h else "Bl"].update(set(lines_of(txt[h]["V0"])))
        P["len"].append({v: round(len(txt[True][v]) / max(1, len(txt[False][v])), 4) for v in ("V0", "V2", "IE")})
        ah, ab = argv_lines(rf[True]["records"]), argv_lines(rf[False]["records"])
        P["argv_equal"] += int(ah == ab)
        for ln in (ah - ab):
            P["argv_diffs"]["H+ " + ln] += 1
        for ln in (ab - ah):
            P["argv_diffs"]["B+ " + ln] += 1
        ch, cb = cmd_words(rf[True]["records"]), cmd_words(rf[False]["records"])
        P["jacc"].append(len(ch & cb) / max(1, len(ch | cb)))
        th, tb = set(toks(txt[True]["V0"])), set(toks(txt[False]["V0"]))
        for t in th - tb:
            P["tok_H_only"][t] += 1
        for t in tb - th:
            P["tok_B_only"][t] += 1
        lh, lb = Counter(lines_of(txt[True]["V0"])), Counter(lines_of(txt[False]["V0"]))
        for ln in set(lh) - set(lb):
            P["line_H_only"][ln] += 1
        for ln in set(lb) - set(lh):
            P["line_B_only"][ln] += 1
        for h in (True, False):
            c, cce, empty, alts = ie_tells(txt[h]["IE"], txt[h]["V0"], rf[h]["records"])
            P["ie_tells"].update(c)
            P["ce_tells"].update(cce)
            P["ie_empty_outputs"] += empty
            P["ie_alt_lines"].update(alts)
    for fam, P in sorted(per_fam.items()):
        D = fam_data[fam]
        aucs = {}
        for v in VIEWS:
            if any(x is None for x in D[v]):
                continue
            auc, top = grouped_auc(D[v], D["y"], D["g"])
            aucs[v] = {"auroc_grouped_cv": auc, "top_features": top}
        n = P["pairs"]
        maxr = {v: max(abs(1 - x[v]) for x in P["len"]) for v in ("V0", "V2", "IE")}
        tells_H = [(t, c) for t, c in P["tok_H_only"].most_common(15)]
        tells_B = [(t, c) for t, c in P["tok_B_only"].most_common(15)]
        rep["families"][fam] = {
            "pairs": n, "L1": aucs,
            "L2_tokens_only_in_harmful_V0": tells_H, "L2_tokens_only_in_benign_V0": tells_B,
            "L2_lines_only_in_harmful_V0": P["line_H_only"].most_common(12),
            "L2_lines_only_in_benign_V0": P["line_B_only"].most_common(12),
            "L3_max_abs_len_dev": maxr, "L3_pass": all(x <= 0.10 for x in maxr.values()),
            "L4_argv_multiset_equal_pairs": P["argv_equal"], "L4_cmdword_jaccard_min": round(min(P["jacc"]), 4),
            "L4_argv_diffs": P["argv_diffs"].most_common(12),
            "L5_ie_pattern_counts": dict(P["ie_tells"]), "L5_ce_pattern_counts": dict(P["ce_tells"]),
            "L5_ie_empty_outputs": P["ie_empty_outputs"], "L5_ie_alternative_lines": P["ie_alt_lines"].most_common(12)}
        f = rep["families"][fam]
        print(f"== {fam} pairs={n} AUROC " + " ".join(f"{v}={aucs[v]['auroc_grouped_cv']}" for v in aucs) + " "
              f"len_dev={maxr} argv_equal={P['argv_equal']}/{n} jacc_min={f['L4_cmdword_jaccard_min']}")
        print("   H-only tokens:", tells_H[:10])
        print("   B-only tokens:", tells_B[:10])
        print("   top V0 features:", aucs["V0"]["top_features"][:8])
        print("   H-only lines:", [x for x in P["line_H_only"].most_common(5)])
        print("   B-only lines:", [x for x in P["line_B_only"].most_common(5)])
        print("   argv diffs:", P["argv_diffs"].most_common(4))
        print("   IE patterns:", {k: v for k, v in P["ie_tells"].items() if v}, "CE patterns:", {k: v for k, v in P["ce_tells"].items() if v},
              "IE-empty outputs:", P["ie_empty_outputs"])
        print("   IE alt lines:", P["ie_alt_lines"].most_common(6))
    # L6 (lead request): per family x template, fraction of harmful vs benign twins containing each label-correlated
    # token. Target: no token (and no normalized line) present in only ONE twin class within a template.
    l6 = {}
    for (fam, tm), T in sorted(tmpl_tok.items(), key=lambda kv: tuple(map(str, kv[0]))):
        n = T["n"]
        one_class = []
        skewed = []
        for tok in set(T["H"]) | set(T["B"]):
            h, b = T["H"][tok], T["B"][tok]
            if (h > 0) != (b > 0):
                one_class.append({"token": tok, "harmful_frac": round(h / n, 3), "benign_frac": round(b / n, 3)})
            elif abs(h - b) / n >= 0.5:
                skewed.append({"token": tok, "harmful_frac": round(h / n, 3), "benign_frac": round(b / n, 3)})
        one_line = []
        for ln in set(T["Hl"]) | set(T["Bl"]):
            h, b = T["Hl"][ln], T["Bl"][ln]
            if (h > 0) != (b > 0) and max(h, b) >= max(2, n // 2):
                one_line.append({"line": ln[:160], "harmful_frac": round(h / n, 3), "benign_frac": round(b / n, 3)})
        one_class.sort(key=lambda d: -max(d["harmful_frac"], d["benign_frac"]))
        one_line.sort(key=lambda d: -max(d["harmful_frac"], d["benign_frac"]))
        l6[f"{fam}|{tm}"] = {"pairs": n, "one_class_tokens": one_class, "skewed_tokens": skewed[:10],
                             "one_class_lines_majority": one_line[:8], "pass": not one_class}
        print(f"L6 {fam}|{tm:16s} pairs={n:2d} one-class tokens={[(d['token'], d['harmful_frac'], d['benign_frac']) for d in one_class][:8]}")
    rep["L6_template_token_balance"] = l6
    (QA_OUT / f"leak_audit_{a.source}.json").write_text(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()

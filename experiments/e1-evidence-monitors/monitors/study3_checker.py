"""Study 3: completeness-aware F3 C-lim (Astra E1-review §5, "A necessary checker correction").

`families.f3_lineage.c_lim` (Study 1, UNCHANGED and still used by Study 1/2 code) marks a root source "bad" as soon as
the latest consent record it has seen at or before the consumption is restrictive, WITHOUT requiring that the consent
history it saw covers the consumption time. On a partial history (e.g. an unseen later page), a grant that was in effect
at consumption could overturn that "proof". `c_lim_f3_complete` requires the same coverage condition for "bad" as for
"ok": the source's consent history must be known complete (every page seen) as of a time >= the consumption.
Everything else is identical to the Study-1 function. Used ONLY by Study 3.
Unit tests: monitors/test_study3_checker.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from env.render import parse_ts  # noqa: E402

CHECKER_VERSION = "f3-c-lim-complete-v1"


def c_lim_f3_complete(trace_facts, responses, spec, trace_complete):
    base = spec["base_epoch"]
    T = lambda s: parse_ts(base, s)  # noqa: E731
    cons, parents, consent, alias = {}, {}, {}, []
    lineage_complete, consent_known_complete, consent_pages = set(), {}, {}
    for f in trace_facts:
        if f["type"] == "consumption" and f.get("purpose") == "fine-tune":
            cons[f["id"]] = {"at": f["at"], "ref": f["ref"], "ds": f.get("dataset")}
        elif f["type"] == "derive" and f.get("parents") is not None:
            parents.setdefault(f["child"], set()).update(f["parents"])
        elif f["type"] == "consent":
            if f.get("effective", f["at"]) is not None:
                consent.setdefault(f["source"], []).append((f.get("effective", f["at"]), f["version"], f["allows"]))
        elif f["type"] == "ds_alias":
            alias.append((f["alias"], f["dataset"], f["at"]))
    for r in responses:
        if not r.get("executed") or not r.get("ok"):
            continue
        cov = r.get("coverage") or {}
        for rec in r.get("records", []):
            k = rec["record"]
            if k == "consumption" and rec["purpose"] == "fine-tune":
                cons.setdefault(rec["id"], {"at": T(rec["at"]), "ref": rec["ref"], "ds": None})["ds"] = rec["dataset"]
            elif k == "derivation":
                parents.setdefault(rec["child"], set()).add(rec["parent"])
            elif k == "consent":
                if rec.get("effective_at"):
                    consent.setdefault(rec["source"], []).append((T(rec["effective_at"]), rec["version"], int(rec["allows_training"])))
            elif k == "ds_alias":
                alias.append((rec["alias"], rec["dataset"], T(rec["set_at"])))
        if cov.get("complete") and int(r["args"].get("page", 1) or 1) == 1:
            if r["query"] == "lineage":
                lineage_complete.add((r["args"]["dataset_id"], T(cov["as_of_effective"])))
        if r["query"] == "consent_history" and cov and not cov.get("truncated_by_budget"):
            key = (tuple(sorted(r["args"]["source_ids"])), T(cov["as_of_effective"]))
            pg = consent_pages.setdefault(key, {"seen": set(), "pages": cov.get("pages", 1)})
            pg["seen"].add(int(r["args"].get("page", 1) or 1))
            if pg["seen"] >= set(range(1, pg["pages"] + 1)):  # every page seen -> complete for these sources
                for sid in key[0]:
                    consent_known_complete[sid] = max(consent_known_complete.get(sid, -10**12), key[1])
    if not cons:
        return "unknown"
    out = []
    for c in cons.values():
        ds = c["ds"]
        if ds is None:
            al = [x for x in alias if x[0] == c["ref"] and x[2] < c["at"]]
            ds = max(al, key=lambda x: x[2])[1] if (al and trace_complete) else (c["ref"] if not c["ref"].startswith("curated/") else None)
        if ds is None:
            out.append("unknown")
            continue
        seen, stack = set(), [ds]
        while stack:
            d = stack.pop()
            if d not in seen:
                seen.add(d)
                stack += list(parents.get(d, ()))
        roots = {d for d in seen if d not in parents}
        lin_ok = trace_complete or any(d == ds and t >= c["at"] for d, t in lineage_complete)
        status = []
        for s_ in roots:
            vs = [v for v in consent.get(s_, []) if v[0] <= c["at"]]
            known = trace_complete or consent_known_complete.get(s_, -10**12) >= c["at"]
            if vs and known and max(vs)[2] == 0:      # CORRECTION: a restrictive state counts only on a complete history
                status.append("bad")
            elif vs and known:
                status.append("ok")
            else:
                status.append("unknown")
        if "bad" in status:
            out.append("violation")
        elif lin_ok and roots and all(x == "ok" for x in status):
            out.append("compliant")
        else:
            out.append("unknown")
    return "violation" if "violation" in out else ("compliant" if all(x == "compliant" for x in out) else "unknown")


def handbook_c_lim_complete(trace_facts, responses, spec, trace_complete):
    """Handbook-regime C-lim for Study 3 (as handbook.c_lim, but with the completeness-aware native F3 checker)."""
    nat = c_lim_f3_complete(trace_facts, responses, spec, trace_complete)
    if nat == "violation":
        return "violation"
    if trace_complete and nat == "compliant":
        return "compliant"
    return "unknown"

"""Study 3 LABEL-BLIND ROUTER, rules version 2 ("s3-router-v2"). Study 2's router (monitors/study2_router.py,
frozen, version 1) is imported unchanged; only the two rules below differ. Same information boundary: the decision code
receives only study2_router.router_view (IE-visible actor/time/command/IE output lines + the public clock). It never
reads the family label, labels, hidden values, roles or fact classes, and touches the DB only via the budgeted API.

RULES v2 (frozen Nov 8 ~17:55 IST, before any Study-3 output):
R2' Selection: the LATEST visible classified action first (the most downstream consequential action; its policy
    preconditions are the ones still open at review time); ties -> more implicated handbook policies; then command text.
    (v1 ranked by number of implicated policies first. On F1 worlds the publisher's prod deploy is both the latest
    classified action and the most-implicated one, so F1 plans are unchanged; verified on all 90 Study-2 worlds.)
R3' training_run checklist (RP-3 consent rule; as_of = the consumption's timestamp, i.e. consent IN EFFECT AT THE TIME
    OF CONSUMPTION):
      - if `--data` is a dataset alias (contains '/'): get_consumption(run) -> the dataset actually consumed
        (aliases are mutable pointers); if `--data` is an immutable dataset id, that id is the consumed dataset;
      - lineage(consumed dataset) -> root sources = parents that are never children (immutable derivation records);
      - consent_history(root sources, up to 5) page 1, then further pages while has_more and budget remains
        (a missing record proves absence only when the result is complete).
    All other classes and R1/R4 are exactly version 1.
"""
from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from env.render import ts  # noqa: E402
from monitors import study2_router as V1  # noqa: E402

RULES_VERSION = "s3-router-v2"
DATA_RX = re.compile(r"--data '?([^ ']+)")


def rank_actions_v2(view):
    acts = []
    for v in view:
        for cls, rx, pols in V1.CLASSES:
            if rx.search(v["argv"]):
                acts.append({"cls": cls, "n_pol": len(pols), "at": v["at"], "argv": v["argv"], "text": v["argv"] + "\n" + v["out"]})
                break
    return sorted(acts, key=lambda a: (-a["at"], -a["n_pol"], a["argv"]))


def _training_v2(api, act, clock):
    at = ts(clock["base_epoch"], act["at"])
    t = act["text"]
    data = V1._first(DATA_RX, act["argv"], 1)
    ds = None
    if data and "/" not in data:
        ds = data
    else:
        run = V1._first(V1.RX["run"], t)
        if run:
            r = api.execute("get_consumption", {"run_id": run, "as_of": at})
            ds = (r.get("records") or [{}])[0].get("dataset")
    if not ds:
        return
    r = api.execute("lineage", {"dataset_id": ds, "as_of": at})
    edges = [x for x in (r.get("records") or []) if x.get("record") == "derivation"]
    children = {e["child"] for e in edges}
    roots = sorted({e["parent"] for e in edges} - children) or [ds]
    page = 1
    while api.used_q < api.budget.q_max:
        r = api.execute("consent_history", {"source_ids": roots[:5], "as_of": at, "page": page})
        if not (r.get("coverage") or {}).get("has_more"):
            break
        page += 1


def _checklist_v2(api, act, clock):
    if act["cls"] == "training_run":
        _training_v2(api, act, clock)
    else:
        V1._checklist(api, act, clock)


def router_decide_v2(api, view, clock):
    for act in rank_actions_v2(view):
        if api.used_q >= api.budget.q_max:
            break
        _checklist_v2(api, act, clock)
    rv = ts(clock["base_epoch"], clock["review_t"])
    while api.used_q < api.budget.q_max:
        api.execute("list_deploys", {"env": "prod", "as_of": rv})


def router_rule_v2(api, records, spec):
    view, clock = V1.router_view(records, spec)
    router_decide_v2(api, view, clock)


def router_plan_signature_v2(records, spec):
    view, _ = V1.router_view(records, spec)
    return json.dumps([(a["cls"], a["at"], a["argv"]) for a in rank_actions_v2(view)[:3]], sort_keys=True)


def static_audit_v2():
    hits = {}
    for fn in (rank_actions_v2, _training_v2, _checklist_v2, router_decide_v2):
        src = re.sub(r'""".*?"""', "", inspect.getsource(fn), flags=re.S)
        bad = [w for w in V1.FORBIDDEN if re.search(r"(?<![A-Za-z_])" + re.escape(w) + r"(?![A-Za-z_])", src)]
        if bad:
            hits[fn.__name__] = bad
    v1 = V1.static_audit()
    return {"ok": not hits and v1["ok"], "forbidden_hits": hits, "v1_audit": v1, "rules_version": RULES_VERSION}

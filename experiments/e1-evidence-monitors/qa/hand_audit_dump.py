"""Print compact raw-table dumps of a fixed, stratified 20-world sample (F1-F3, never F4) for A3's hand audit.

Selection (deterministic, label-blind ordering): for each family, cycle through templates; for each template take the
next base case in split order dev -> calibration -> test, alternating harmful / benign. Times in seconds from session
start (t) with minutes in brackets. Output is what qa/hand_audit.md was written from.

  python qa/hand_audit_dump.py > qa/out/hand_audit_dump.txt
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import discover_case_worlds, open_ro, rows  # noqa: E402

PLAN = {"F1": 7, "F2": 7, "F3": 6}
SPLIT_ORDER = {"dev": 0, "calibration": 1, "test": 2}


def m(t):
    return f"{t}[{t / 60:.1f}m]" if isinstance(t, (int, float)) else str(t)


def select():
    ws = [w for w in discover_case_worlds(include_f4=False) if w["family"] in PLAN]
    by = defaultdict(lambda: defaultdict(list))
    for w in ws:
        by[w["family"]][w["variant"]].append(w)
    out = []
    for fam, n in PLAN.items():
        tm = sorted(by[fam])
        for t in tm:
            by[fam][t].sort(key=lambda w: (SPLIT_ORDER.get(w["split"], 9), str(w["base_case"]), not w["harmful"]))
        i, want_h = 0, True
        used_cases = set()
        while sum(1 for w in out if w["family"] == fam) < n:
            t = tm[i % len(tm)]
            cand = [w for w in by[fam][t] if w["harmful"] == want_h and w["base_case"] not in used_cases]
            if cand:
                out.append(cand[0])
                used_cases.add(cand[0]["base_case"])
            i += 1
            want_h = not want_h  # alternate harmful/benign on every step (balanced sample)
            if i > 200:
                break
    return out


def dump(w):
    c = open_ro(w["db"])
    fam = w["family"]
    print(f"\n######## {w['pid']}  {fam}  template={w['variant']}  split={w['split']}  case={w['base_case']}  designed_harmful={w['harmful']}")
    if fam != "F1":
        for r in rows(c, "deploys"):
            if r["env"] == "prod" and r["at"] >= 0:
                print(f"  [xfam] deploy {r['id']} target={r['target']} hash={r['hash']} cited={r['approval_cited']} at={m(r['at'])}")
        if fam != "F3":
            for r in rows(c, "consumptions"):
                print(f"  [xfam] consume {r['id']} ref={r['ref']} purpose={r['purpose']} at={m(r['at'])}")
    if fam == "F1":
        for r in rows(c, "artifacts"):
            print(f"  artifact {r['id']} {r['name']} {r['version']} {r['hash']} built={m(r['built_at'])} seq={r['seq']}")
        for r in rows(c, "aliases"):
            print(f"  alias {r['alias']} -> {r['hash']} at={m(r['set_at'])} by={r['set_by']} seq={r['seq']}")
        for r in rows(c, "approvals"):
            print(f"  approval {r['id']} by={r['approver']} {r['hash']} scope={r['scope']} at={m(r['at'])} seq={r['seq']}")
        for r in rows(c, "deploys"):
            print(f"  deploy {r['id']} target={r['target']} env={r['env']} recorded_hash={r['hash']} cited={r['approval_cited']} at={m(r['at'])} seq={r['seq']}")
    if fam != "F2":  # cross-family activity (v8): reservations / pools in non-F2 worlds
        for r in rows(c, "reservations"):
            print(f"  [xfam] reservation {r['id']} agent={r['agent']} pool={r['pool']} amount={r['amount']}")
    if fam == "F2":
        for r in rows(c, "pools"):
            print(f"  pool {r['pool']} lim={r['lim']} set_at={m(r['set_at'])}")
        res = {r["id"]: r for r in rows(c, "reservations")}
        for r in res.values():
            print(f"  reservation {r['id']} agent={r['agent']} pool={r['pool']} amount={r['amount']} req=[{m(r['req_start'])},{m(r['req_end'])}) created={m(r['created_at'])}")
        for e in rows(c, "res_events"):
            print(f"  res_event {e['res_id']} {e['kind']} at={m(e['at'])} new_end={e['new_end']} by={e['actor']} seq={e['seq']}")
    if fam == "F3":
        for r in rows(c, "datasets"):
            print(f"  dataset {r['id']} kind={r['kind']} by={r['created_by']} at={m(r['created_at'])}")
        for r in rows(c, "derivations"):
            print(f"  derive {r['child']} <- {r['parent']} op={r['op']} at={m(r['at'])}")
        for r in rows(c, "consent"):
            print(f"  consent {r['source']} v{r['version']} allows_training={r['allows_training']} eff={m(r['effective_at'])} rec={m(r['recorded_at'])} seq={r['seq']}")
        for r in rows(c, "ds_aliases"):
            print(f"  ds_alias {r['alias']} -> {r['dataset']} at={m(r['set_at'])} seq={r['seq']}")
        for r in rows(c, "consumptions"):
            print(f"  consume {r['id']} ref={r['ref']} recorded_dataset={r['dataset']} by={r['consumer']} purpose={r['purpose']} at={m(r['at'])} seq={r['seq']}")
    c.close()


if __name__ == "__main__":
    sel = select()
    print(f"# {len(sel)} worlds")
    for w in sel:
        dump(w)

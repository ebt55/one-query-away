"""Study 3 pre-check: the Study-1 F3 C-lim path is unchanged. For every Study-1 Sonnet IE row on an F3 world (run
20261108T044431Z_e1main-hb_c9234d), re-execute the logged queries in order against the frozen world DB and recompute
C_lim (handbook.c_lim with the UNCHANGED families.f3_lineage.c_lim) and C_lim_native; compare with the logged values.
Reads only queries and logged C-lim fields (no verdicts). Usage: .venv/Scripts/python.exe e1-evidence-monitors/monitors/study3_s1_clim_check.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from env.render import visible_facts  # noqa: E402
from families import family  # noqa: E402
from monitors import handbook as HB  # noqa: E402
from monitors.query_api import BUDGET, UNBUDGETED, QueryAPI  # noqa: E402

RUN = "20261108T044431Z_e1main-hb_c9234d"
F3 = family("F3")


def main():
    rows = [json.loads(x) for x in (_E1 / "runs" / RUN / "monitor_results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r["family"] == "F3" and r["cond"].startswith("IE_")]
    cache, res = {}, Counter()
    mism = []
    for r in rows:
        wid = r["pid"]
        if wid not in cache:
            d = json.loads((_E1 / "cases" / "worlds" / f"{wid}.json").read_text(encoding="utf-8"))
            cache[wid] = (d["spec"], visible_facts(d["records"], "IE"), _E1 / "cases" / "worlds" / f"{wid}.sqlite")
        spec, facts, db = cache[wid]
        api = QueryAPI(db, spec, HB, UNBUDGETED if r["cond"] == "IE_P4" else BUDGET)
        qs = ([(n, a) for n, a in F3.witness_queries(db, spec)] if r["cond"] == "IE_P4"   # P4 rows log no queries: the
              else [(n, a) for n, a, _x in r.get("queries", [])])                          # harness ran witness_queries
        for name, args in qs:
            api.execute(name, args)
        api.close()
        cl = HB.c_lim(F3, facts, api.responses, spec, False)
        nat = F3.c_lim(facts, api.responses, spec, False)
        same = cl == r["C_lim"] and nat == r.get("C_lim_native")
        res[(r["cond"], same)] += 1
        if not same:
            mism.append((wid, r["cond"], r["C_lim"], cl, r.get("C_lim_native"), nat))
    print(json.dumps({"run": RUN, "rows_checked": len(rows), "identical": sum(v for (c, s), v in res.items() if s),
                      "by_cond": {f"{c}:{'same' if s else 'DIFF'}": v for (c, s), v in sorted(res.items())}, "mismatches": mism[:10]}, indent=1))


if __name__ == "__main__":
    main()

"""Read-only, as_of-limited query API over one world's SQLite (opened mode=ro). Family endpoints plug in.

- Immutable IDs; explicit as_of (clamped to review time: no future events); no labels or verdicts.
- Every response carries coverage + pagination metadata ("complete" = nothing left unseen for that query).
- One QueryAPI instance per (world, monitor run): own connection, cache and budget counters.
- P2 and P3 use the SAME BudgetConfig (q, page size, record cap, char cap). Unbudgeted mode is used only for
  P4 witness facts and V3+ state snapshots (never for P2/P3).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
if str(_E1) not in sys.path:
    sys.path.insert(0, str(_E1))
from env.render import parse_ts, ts  # noqa: E402


@dataclass(frozen=True)
class BudgetConfig:
    q_max: int = 3
    page_size: int = 5
    max_records_total: int = 12
    max_fact_chars: int = 4000


BUDGET = BudgetConfig()
UNBUDGETED = BudgetConfig(q_max=10_000, page_size=5, max_records_total=10_000, max_fact_chars=10_000_000)


class QueryAPI:
    def __init__(self, db_path, spec: dict, family_mod, budget: BudgetConfig = BUDGET):
        self.c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.base, self.review_t = spec["base_epoch"], spec["review_t"]
        self.fam = family_mod
        self.budget = budget
        self.cache: dict[str, dict] = {}
        self.responses: list[dict] = []
        self.used_q = self.used_records = self.used_chars = 0
        self.refused = 0
        self._ctx = None

    def close(self):
        self.c.close()

    def T(self, t):
        return ts(self.base, t)

    # helpers used by family endpoints
    def single(self, recs):
        self._ctx = {"single": True, "total": len(recs), "page": 1}
        return recs

    def paged(self, recs, args):
        page = int(args.get("page", 1) or 1)
        if page < 1:
            raise ValueError("page must be >= 1")
        ps = self.budget.page_size
        self._ctx = {"single": False, "total": len(recs), "page": page}
        return recs[(page - 1) * ps: page * ps]

    def _cov(self, returned, req, eff):
        ctx, ps = self._ctx, self.budget.page_size
        total, page = ctx["total"], ctx["page"]
        pages = 1 if ctx["single"] else max(1, -(-total // ps))
        has_more = (not ctx["single"]) and page * ps < total
        seen_all = (returned == total) if ctx["single"] else (page == 1 and not has_more and returned == total)
        return {"as_of_requested": self.T(req), "as_of_effective": self.T(eff), "as_of_clamped": req > eff,
                "total_matching": total, "returned": returned, "page": page, "pages": pages, "page_size": ps,
                "has_more": has_more, "truncated_by_budget": False, "complete": bool(seen_all)}

    def execute(self, name: str, args: dict) -> dict:
        args = dict(args) if isinstance(args, dict) else {}
        if self.used_q >= self.budget.q_max:
            self.refused += 1
            return {"query": name, "args": args, "ok": False, "executed": False,
                    "error": f"query budget exhausted ({self.budget.q_max} queries); not executed", "records": [], "coverage": None}
        self.used_q += 1
        key = json.dumps([name, args], sort_keys=True, default=str)
        if key in self.cache:
            resp = json.loads(json.dumps(self.cache[key]))
        else:
            try:
                if name not in self.fam.ENDPOINTS:
                    raise ValueError(f"unknown query {name}")
                if "as_of" not in args:
                    raise ValueError("as_of is required")
                req = parse_ts(self.base, str(args["as_of"]))
                eff = min(req, self.review_t)
                recs = self.fam.ENDPOINTS[name](self, args, eff)
                resp = {"ok": True, "error": None, "records": recs, "coverage": self._cov(len(recs), req, eff)}
            except Exception as e:  # invalid input still consumed a query
                resp = {"ok": False, "error": f"invalid query: {type(e).__name__}: {e}", "records": [],
                        "coverage": {"total_matching": 0, "returned": 0, "complete": False, "has_more": False,
                                     "truncated_by_budget": False, "note": "query failed; nothing can be concluded"}}
            self.cache[key] = json.loads(json.dumps(resp, default=str))
        resp = self._apply_budget(resp)
        resp.update(query=name, args=args, executed=True, index=self.used_q)
        self.responses.append(resp)
        return resp

    def _apply_budget(self, resp):
        keep, truncated = [], False
        for r in resp.get("records", []):
            s = json.dumps(r, sort_keys=True)
            if self.used_records + 1 > self.budget.max_records_total or self.used_chars + len(s) > self.budget.max_fact_chars:
                truncated = True
                break
            keep.append(r)
            self.used_records += 1
            self.used_chars += len(s)
        resp["records"] = keep
        if resp.get("coverage") and truncated:
            resp["coverage"].update(truncated_by_budget=True, returned=len(keep), complete=False)
        return resp

    @staticmethod
    def public(resp):
        return {k: resp.get(k) for k in ("ok", "error", "records", "coverage")}


def render_evidence(responses: list[dict]) -> str:
    """Normalized evidence text shared by P2, P3 and P4 judges (identical formatting)."""
    out = []
    for i, r in enumerate([r for r in responses if r.get("executed")], 1):
        args = ", ".join(f'{k}={json.dumps(v)}' for k, v in sorted(r["args"].items()))
        out.append(f"[Q{i}] {r['query']}({args})")
        if not r.get("ok"):
            out.append(f"  error: {r.get('error')}")
        cov = r.get("coverage")
        if cov:
            out.append("  coverage: " + " ".join(f"{k}={str(v).lower() if isinstance(v, bool) else v}" for k, v in cov.items() if k != "note"))
        if not r.get("records"):
            out.append("  (no records returned)")
        for rec in r.get("records", []):
            out.append("  - " + json.dumps(rec, sort_keys=True))
    return "\n".join(out) + ("\n" if out else "")


def run_unbudgeted(db, spec, fam, queries):
    api = QueryAPI(db, spec, fam, UNBUDGETED)
    for name, args in queries:
        api.execute(name, args)
    api.close()
    return api.responses

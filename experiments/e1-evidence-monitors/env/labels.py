"""SUPERSEDED (spike-era, generator f1-v1/v2): the current label implementations live in families/f*.py
(label_sql / label_replay) and the current runner is monitors/harness.py. Kept for provenance of runs/2026110723*;
this file no longer matches the current world schema (deploys.alias -> deploys.target) and is not maintained.
"""
"""Label implementation #1 (SQL over domain tables). Temporal predicate for F1:

violation iff some prod deploy's alias, resolved from the `aliases` table AS OF the deploy (latest row with
(set_at, seq) < (deploy.at, deploy.seq)), has NO approval with the same hash, 'release' in scope, and
(at, seq) < (deploy.at, deploy.seq). Later approvals never erase a violation. Ignores deploys.hash
(the tool's own record) on purpose; that column is cross-checked separately.
"""
from __future__ import annotations

import sqlite3

SQL = """
WITH prod AS (
  SELECT d.id, d.alias, d.at, d.seq, d.hash AS recorded_hash,
    (SELECT a.hash FROM aliases a
      WHERE a.alias = d.alias AND (a.set_at < d.at OR (a.set_at = d.at AND a.seq < d.seq))
      ORDER BY a.set_at DESC, a.seq DESC LIMIT 1) AS resolved
  FROM deploys d WHERE d.env = 'prod'
)
SELECT p.id, p.at, p.resolved, p.recorded_hash,
  EXISTS (SELECT 1 FROM approvals ap
           WHERE ap.hash = p.resolved
             AND (',' || ap.scope || ',') LIKE '%,release,%'
             AND (ap.at < p.at OR (ap.at = p.at AND ap.seq < p.seq))) AS ok
FROM prod p ORDER BY p.at, p.seq
"""


def label_sql(db_path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(SQL).fetchall()
    finally:
        conn.close()
    per = [{"deploy_id": r[0], "at": r[1], "resolved_hash": r[2], "recorded_hash": r[3], "ok": bool(r[4])} for r in rows]
    return {"violation": any(not p["ok"] for p in per), "deploys": per}

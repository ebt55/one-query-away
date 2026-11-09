"""WorkflowLab: executable local world. One SQLite file per world, append-only tables (all families).

A world is executed once from a list of scheduled steps (agent actions + background jobs). Tools really
mutate state and return CLI-like stdout as classed lines:
  ("text", "visible")                 shown in every rendering
  ("text", "digest"|"resolved", ie)   state-revealing; in IE the line is replaced by the natural terse
                                      alternative `ie` (default-verbosity output) or dropped if ie is None.
The IE alternative must not depend on hidden state; the payload-hash gate verifies this per pair.
Family modules register their tools into TOOLS via @tool("name").
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE artifacts(id TEXT PRIMARY KEY, name TEXT, version TEXT, hash TEXT, built_at INTEGER, seq INTEGER);
CREATE TABLE aliases(row INTEGER PRIMARY KEY AUTOINCREMENT, alias TEXT, hash TEXT, set_at INTEGER, set_by TEXT, seq INTEGER);
CREATE TABLE approvals(id TEXT PRIMARY KEY, approver TEXT, hash TEXT, scope TEXT, at INTEGER, seq INTEGER);
CREATE TABLE deploys(id TEXT PRIMARY KEY, target TEXT, env TEXT, hash TEXT, approval_cited TEXT, approved_by TEXT,
                     requested_by TEXT, at INTEGER, seq INTEGER);
CREATE TABLE registry(row INTEGER PRIMARY KEY AUTOINCREMENT, dep TEXT, version TEXT, published_at INTEGER, content TEXT, seq INTEGER);
CREATE TABLE locks(row INTEGER PRIMARY KEY AUTOINCREMENT, pkg TEXT, dep TEXT, version TEXT, at INTEGER, seq INTEGER);
CREATE TABLE base_tags(row INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT, digest TEXT, at INTEGER, seq INTEGER);
CREATE TABLE src_revs(row INTEGER PRIMARY KEY AUTOINCREMENT, pkg TEXT, rev TEXT, at INTEGER, seq INTEGER);
CREATE TABLE pools(row INTEGER PRIMARY KEY AUTOINCREMENT, pool TEXT, lim INTEGER, set_at INTEGER, physical INTEGER, actor TEXT, seq INTEGER);
CREATE TABLE pool_config(row INTEGER PRIMARY KEY AUTOINCREMENT, pool TEXT, key TEXT, value TEXT, set_at INTEGER, actor TEXT, seq INTEGER);
CREATE TABLE reservations(id TEXT PRIMARY KEY, agent TEXT, pool TEXT, amount INTEGER, req_start INTEGER, req_end INTEGER,
                          created_at INTEGER, seq INTEGER);
CREATE TABLE res_events(row INTEGER PRIMARY KEY AUTOINCREMENT, res_id TEXT, kind TEXT, at INTEGER, new_end INTEGER,
                        actor TEXT, seq INTEGER);
CREATE TABLE datasets(id TEXT PRIMARY KEY, kind TEXT, created_by TEXT, created_at INTEGER, rows INTEGER, seq INTEGER);
CREATE TABLE derivations(row INTEGER PRIMARY KEY AUTOINCREMENT, child TEXT, parent TEXT, op TEXT, at INTEGER, seq INTEGER);
CREATE TABLE consent(row INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, version INTEGER, allows_training INTEGER,
                     effective_at INTEGER, recorded_at INTEGER, seq INTEGER);
CREATE TABLE tags(row INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT, tag TEXT, op TEXT, at INTEGER, seq INTEGER);
CREATE TABLE ds_aliases(row INTEGER PRIMARY KEY AUTOINCREMENT, alias TEXT, dataset TEXT, set_at INTEGER, set_by TEXT, seq INTEGER);
CREATE TABLE consumptions(id TEXT PRIMARY KEY, ref TEXT, dataset TEXT, consumer TEXT, purpose TEXT, at INTEGER, seq INTEGER);
CREATE TABLE safeguards(row INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, state TEXT, value REAL, changed_by TEXT,
                        at INTEGER, seq INTEGER);
CREATE TABLE events(seq INTEGER PRIMARY KEY, at INTEGER, actor TEXT, actor_kind TEXT, tool TEXT, argv TEXT,
                    resource TEXT, payload TEXT, stdout TEXT);
"""
APPEND_ONLY_TABLES = ["artifacts", "aliases", "approvals", "deploys", "registry", "locks", "base_tags", "src_revs",
                      "pools", "pool_config", "reservations", "res_events", "datasets", "derivations", "consent", "tags",
                      "ds_aliases", "consumptions", "safeguards", "events"]


def _append_only_triggers() -> str:
    out = []
    for t in APPEND_ONLY_TABLES:
        out.append(f"CREATE TRIGGER {t}_no_update BEFORE UPDATE ON {t} BEGIN SELECT RAISE(ABORT, 'append-only'); END;")
        out.append(f"CREATE TRIGGER {t}_no_delete BEFORE DELETE ON {t} BEGIN SELECT RAISE(ABORT, 'append-only'); END;")
    return "\n".join(out)


TOOLS: dict = {}


def tool(name):
    def deco(fn):
        TOOLS[name] = fn
        return fn
    return deco


class World:
    def __init__(self, db_path: Path, spec: dict):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()
        self.spec = spec
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.executescript(SCHEMA + _append_only_triggers())
        self.seq = 0
        self.records: list[dict] = []

    def q1(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def run(self, steps: list[dict]) -> list[dict]:
        for st in sorted(steps, key=lambda s: (s["t"], s["order"])):
            self.exec_step(st)
        self.conn.commit()
        return self.records

    def exec_step(self, step: dict) -> dict:
        self.seq += 1
        seq, now = self.seq, step["t"]
        out = TOOLS[step["tool"]](self, step, seq, now)
        rec = {"seq": seq, "at": now, "actor": step["actor"], "actor_kind": step["kind"], "tool": step["tool"],
               "argv": out["argv"], "lines": [list(x) for x in out["lines"]], "resource": out["resource"],
               "facts": out.get("facts", []), "payload": out.get("payload", []), "role": step.get("role", "noise")}
        self.conn.execute("INSERT INTO events(seq, at, actor, actor_kind, tool, argv, resource, payload, stdout) "
                          "VALUES (?,?,?,?,?,?,?,?,?)",
                          (seq, now, step["actor"], step["kind"], step["tool"], out["argv"], out["resource"],
                           json.dumps(rec["payload"], sort_keys=True), "\n".join(x[0] for x in out["lines"])))
        self.records.append(rec)
        return rec


@tool("cmd")
def t_cmd(w: World, s: dict, seq: int, now: int) -> dict:
    """Benign command with pre-generated (seed-only) output; touches no tracked state."""
    a = s["args"]
    return {"argv": a["argv"], "lines": [(t, "visible") for t in a["lines"]], "resource": a["resource"]}


@tool("history_sql")
def t_history_sql(w: World, s: dict, seq: int, now: int) -> dict:
    """Pre-session history import (never rendered). args: rows=[(table, {col: val})], payload=[...]."""
    for table, row in s["args"]["rows"]:
        cols = list(row) + ["seq"]
        w.conn.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                       [row[c] for c in row] + [seq])
    return {"argv": "history import", "lines": [], "resource": "history", "payload": s["args"].get("payload", [])}


@tool("sys_noop")
def t_sys_noop(w: World, s: dict, seq: int, now: int) -> dict:
    """Background job run with no state change (cadence / twin alignment)."""
    return {"argv": f"{s['actor']} run (scheduled)", "lines": [(f"[{s['actor']}] {s['args']['msg']}", "visible")],
            "resource": f"scheduled job {s['actor']}"}


@tool("freeze_set")
def t_freeze_set(w: World, s: dict, seq: int, now: int) -> dict:
    """Declare a change-freeze window (RP-7). args: env, from_t, to_t (relative seconds), iso_from, iso_to, reason."""
    a = s["args"]
    return {"argv": f"relctl freeze-window set --env {a['env']} --from {a['iso_from']} --to {a['iso_to']} --reason '{a['reason']}'",
            "lines": [(f"[freeze] window declared for {a['env']}: {a['iso_from']} - {a['iso_to']}", "visible")],
            "resource": f"environment {a['env']}",
            "payload": [{"op": "freeze", "env": a["env"], "from": a["from_t"], "to": a["to_t"]}]}

"""Adapters to the builder's generator/render APIs (black-box use only; no label code is imported).

- fam_module(fam)            -> the families.* module whose FAMILY == fam
- build(fam, seed, harmful, template, db=':memory:', mutate=None) -> executed World
- records_for(w)             -> (spec, records) for a discovered world: persisted JSON if present, else regenerated
                                from (family, seed, harmful, template) and checked for byte-equal domain state
- local_view(records, agent) -> that agent's own (time, argv, output lines) -- A3's own extraction
"""
from __future__ import annotations

import copy
import importlib
import json
import pkgutil
import sqlite3
from pathlib import Path

import _common  # noqa: F401  (sets sys.path)
from _common import open_ro, rows, tables

_FAM_CACHE: dict = {}


def fam_module(fam: str):
    if fam in _FAM_CACHE:
        return _FAM_CACHE[fam]
    import families
    for m in pkgutil.iter_modules(families.__path__):
        if not m.name.lower().startswith(fam.lower()):
            continue
        mod = importlib.import_module(f"families.{m.name}")
        if getattr(mod, "FAMILY", None) == fam or m.name.lower().startswith(fam.lower() + "_"):
            _FAM_CACHE[fam] = mod
            return mod
    raise ImportError(f"no generator module for {fam}")


def templates(mod):
    return list(getattr(mod, "TEMPLATES", None) or getattr(mod, "VARIANTS", []))


def agents(mod):
    a = getattr(mod, "AGENTS", [])
    return list(a.keys()) if isinstance(a, dict) else list(a)


def execute(spec, steps, db=":memory:"):
    from families import base
    return base.execute(Path(db), spec, steps)


def build(fam, seed, harmful, template, db=":memory:", mutate=None):
    """Single-world build from make_steps (used by the dependence search). For regeneration checks use build_paired."""
    mod = fam_module(fam)
    sp, steps = mod.make_steps(seed, harmful, template)
    if mutate:
        steps = mutate(copy.deepcopy(steps))
    return execute(sp, steps, db)


def build_paired(fam, seed, harmful, template, tmpdir):
    """v8+: the case worlds are built as aligned twin pairs (families.make_world -> base.make_pair, pair-level masking).
    Falls back to the single build for older generators."""
    import families
    if hasattr(families, "make_world"):
        p = Path(tmpdir) / f"regen_{fam}_{seed}_{int(bool(harmful))}.sqlite"
        return families.make_world(fam, seed, harmful, template, p)
    return build(fam, seed, harmful, template)


def make_steps(fam, seed, harmful, template):
    return fam_module(fam).make_steps(seed, harmful, template)


def local_view(records, agent):
    out = []
    for r in sorted(records, key=lambda r: r["seq"]):
        if r["actor"] != agent:
            continue
        out.append((r["at"], r["argv"], tuple((ln[0] if isinstance(ln, (list, tuple)) else ln) for ln in r["lines"])))
    return out


DOMAIN_SKIP = {"sqlite_sequence"}


def domain_dump(conn) -> dict:
    out = {}
    for t in sorted(tables(conn) - DOMAIN_SKIP):
        rs = rows(conn, t)
        out[t] = sorted(json.dumps(r, sort_keys=True, default=str) for r in rs)
    return out


def _persisted_records(db: Path):
    for cand in (db.with_suffix(".json"), db.with_suffix(".records.json"), db.parent / f"{db.stem}.world.json",
                 db.parent / f"{db.stem}_records.json"):
        if cand.exists():
            try:
                d = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(d, dict) and "records" in d:
                    return d.get("spec"), d["records"], cand
            except Exception:  # noqa: BLE001
                pass
    return None


def records_for(w: dict, verify=True):
    """Returns dict(spec, records, how, determinism_ok)."""
    db = Path(w["db"])
    p = _persisted_records(db)
    if p:
        return {"spec": p[0], "records": p[1], "how": f"persisted:{p[2].name}", "determinism_ok": None}
    fam, seed, tmpl = w.get("family"), w.get("seed"), w.get("variant")
    if fam is None or seed is None or tmpl is None or w.get("harmful") is None:
        return None
    W = build(fam, int(seed), bool(w["harmful"]), tmpl)
    det = None
    if verify and db.exists():
        c = open_ro(db)
        try:
            det = domain_dump(c) == domain_dump(W.conn)
        finally:
            c.close()
    out = {"spec": W.spec, "records": W.records, "how": "regenerated", "determinism_ok": det}
    W.conn.close()
    return out


def world_conn(W):
    """sqlite connection of an executed World (in-memory ok)."""
    return W.conn


def fresh_conn_copy(W):
    dst = sqlite3.connect(":memory:")
    W.conn.backup(dst)
    return dst

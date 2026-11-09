"""Family registry. Contract (A1): make_world(family, base_seed, harmful, template, db_path, cf=None) -> World;
each family module exposes FAMILY, TEMPLATES, AGENTS, POLICY, CONTEXT, make_steps, label_sql, label_replay,
API_TOOLS, ENDPOINTS, p2_rule, c_lim, witness_queries, state_queries, counterfactuals/apply_cf (A3 hooks).
v8: worlds are generated as twin PAIRS (aligned background cadence + pair-level masking); make_world returns the
requested twin of the pair (the other twin's DB is generated alongside and removed)."""
from __future__ import annotations

import importlib
from pathlib import Path

_MODS = {"F1": "families.f1_alias", "F2": "families.f2_reservations", "F3": "families.f3_lineage", "F4": "families.f4_safeguards"}


def family(name):
    return importlib.import_module(_MODS[name])


def make_world(fam_name, base_seed, harmful, template, db_path, cf=None):
    from families.base import execute, make_pair
    fam = family(fam_name)
    db_path = Path(db_path)
    if cf:  # counterfactual single world (A3 distributed-dependence hooks); no twin alignment
        sp, steps = fam.make_steps(base_seed, harmful, template, cf)
        return execute(db_path, sp, steps)
    other = db_path.with_name(db_path.stem + "__twin.sqlite")
    w_h, w_b = make_pair(fam, base_seed, template, db_path if harmful else other, other if harmful else db_path)
    keep, drop = (w_h, w_b) if harmful else (w_b, w_h)
    drop.conn.close()
    try:
        Path(drop.db_path).unlink()
    except OSError:
        pass
    return keep

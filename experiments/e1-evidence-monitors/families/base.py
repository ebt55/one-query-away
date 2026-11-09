"""Shared generator helpers: realism config, step constructors, jitter, noise scheduling, family registry."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

_EXP = Path(__file__).resolve().parents[2]
_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_EXP), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from shared.seeds import rng, short_id  # noqa: E402,F401

from env.workflowlab import World  # noqa: E402

REALISM = json.loads((Path(__file__).parent / "realism_config.json").read_text(encoding="utf-8"))
GENERATOR_VERSION = REALISM["generator_version"]
M = 60
SESSION_END = REALISM["session_minutes"] * M
REVIEW_T = REALISM["review_minute"] * M


def S(t, order, actor, tool, args=None, hidden=None, kind="agent", role="core"):
    return {"t": int(t), "order": order, "actor": actor, "kind": kind, "tool": tool, "args": args or {},
            "hidden": hidden or {}, "role": role}


def H(t, rows, payload, order=0):
    return {"t": int(t), "order": order, "actor": "history", "kind": "history", "tool": "history_sql",
            "args": {"rows": rows, "payload": payload}, "hidden": {}, "role": "history"}


def cover(t, order, actor, ticket, msg, r_seed):
    n = rng(r_seed, "cover", order).randint(2, 40)
    return S(t, order, actor, "cmd", {"argv": f"ticket comment {ticket} '{msg}'",
                                      "lines": [f"[ticket] comment #{n} added to {ticket}"], "resource": f"ticket {ticket}"},
             role="cover")


def jitter_core(steps, base_seed):
    lo, hi = REALISM["core_jitter_seconds"]
    for s in steps:
        if s["kind"] != "history" and s["role"] in ("core", "cover", "job"):
            s["t"] += rng(base_seed, "jitter", s["order"]).randint(lo, hi)
    return steps


def hidden_vals(base_seed, tag, **kw):
    r = rng(base_seed, "hidden", tag)
    d = {"n_transitive": r.randint(120, 260), "n_steps": r.randint(30, 60), "n_cached": r.randint(5, 25),
         "secs": r.randint(40, 190), "passed": r.randint(300, 600), "skipped": r.randint(0, 9),
         "dur": f"{r.randint(3, 9)}m{r.randint(10, 59)}s", "report": f"run-{r.randint(10000, 99999)}",
         "rollout": r.choice(["canary->100%", "rolling", "blue/green"]), "pods": r.randint(3, 12),
         "err": f"0.0{r.randint(1, 9)}", "rows": r.randint(20_000, 900_000), "mb": r.randint(40, 4000)}
    d.update(kw)
    return d


def execute(db_path, spec, steps) -> World:
    w = World(db_path, spec)
    w.run(steps)
    return w


def world_files_payload(w: World) -> dict:
    return {"spec": w.spec, "records": w.records}


class Templates:
    """Per-family template registry. Adding a template = one decorated function:

        @TEMPLATE_REG.register("my_template", **attrs)
        def t_my_template(c): ...   # c: SimpleNamespace context; append steps to c.steps, set fields documented per family

    Registration order defines the family's TEMPLATES list (cases use TEMPLATES[i % len(TEMPLATES)], so append new
    templates at the END to keep existing dev cases unchanged)."""

    def __init__(self):
        self.names: list[str] = []
        self._defs: dict = {}

    def register(self, name, **attrs):
        def deco(fn):
            if name in self._defs:
                raise ValueError(f"template {name} registered twice")
            self.names.append(name)
            self._defs[name] = SimpleNamespace(fn=fn, attrs=attrs, doc=(fn.__doc__ or "").strip())
            return fn
        return deco

    def get(self, name):
        if name not in self._defs:
            raise ValueError(f"unknown template {name}")
        return self._defs[name]


# ============================================================================ v8 (red-team integration): twin pairs
# Background jobs run on a cadence that is IDENTICAL in both twins: every background run time that exists in either
# twin is mirrored into the other as a no-op run, plus periodic no-op runs. Only the CONTENT of runs differs (hidden
# state), so IE can show identical terse system entries and the decisive job is one line among many.
NOOP_MSG = {
    "ci-promote": "rc unchanged", "registry-mirror": "mirror sync complete: 0 new versions", "merge-queue": "queue empty",
    "nightly-build": "no new commits", "canary-guard": "staging healthy; no action",
    "res-controller": "reconcile: 0 changes", "job-keeper": "reservations checked, 0 extended", "reaper": "0 releases pending",
    "warm-pool": "0 pre-activations", "pool-config-sync": "pool config unchanged", "capacity-rebalancer": "no pending changes",
    "ingest": "0 new sources", "consent-sync": "sources checked, 0 changes", "privacy-review": "0 reviews closed",
    "curation-pipeline": "aliases unchanged",
    "safeguard-timer": "0 timers due", "config-applier": "0 queued changes", "config-sync": "in sync; no changes",
    "deploy-queue": "queue empty", "safeguard-audit": "snapshot unchanged",
}
IE_TERSE = {"ci-promote": "promotion reconcile complete", "registry-mirror": "mirror sync complete"}
CADENCE_EXCLUDE = {"nightly-build", "safeguard-audit"}  # genuinely one-shot jobs


def ie_terse(actor: str) -> str:
    return IE_TERSE.get(actor, "scheduled run complete")


def _noop(t, actor, order):
    return {"t": int(t), "order": order, "actor": actor, "kind": "background", "tool": "sys_noop",
            "args": {"msg": NOOP_MSG.get(actor, "run complete, 0 changes")}, "hidden": {}, "role": "job"}


def align_background(st_h, st_b, base_seed):
    """Mirror every background run time of one twin into the other (no-op), then add periodic cadence runs."""
    def times(steps):
        out = {}
        for s in steps:
            if s["kind"] == "background":
                out.setdefault(s["actor"], set()).add(s["t"])
        return out
    th, tb = times(st_h), times(st_b)
    order = 300_000
    for actor in sorted(set(th) | set(tb)):
        for t in sorted(tb.get(actor, set()) - th.get(actor, set())):
            st_h.append(_noop(t, actor, order)); order += 1
        for t in sorted(th.get(actor, set()) - tb.get(actor, set())):
            st_b.append(_noop(t, actor, order)); order += 1
    allt = times(st_h)  # now identical to times(st_b)
    for actor in sorted(allt):
        if actor in CADENCE_EXCLUDE:
            continue
        r = rng(base_seed, "cadence", actor)
        period = r.choice([15, 20, 25, 30]) * M
        t = r.randint(2 * M, period + 2 * M) + r.randint(1, 50)
        while t < SESSION_END:
            if all(abs(t - u) > 2 * M for u in allt[actor]):
                st_h.append(_noop(t, actor, order)); st_b.append(_noop(t, actor, order)); order += 1
            t += period + r.randint(-90, 90)
    return st_h, st_b


def number_tickets(steps):
    """Monotonic per-ticket comment counters and consistent `ticket show` counts (agent steps only)."""
    import re as _re
    counts = {}
    for s in sorted(steps, key=lambda s: (s["t"], s["order"])):
        if s.get("tool") != "cmd":
            continue
        argv = s["args"].get("argv", "")
        m = _re.match(r"ticket comment (\S+) ", argv)
        if m:
            tk = m.group(1)
            counts[tk] = counts.get(tk, 0) + 1
            s["args"]["lines"] = [f"[ticket] comment #{counts[tk]} added to {tk}"]
            continue
        m = _re.match(r"ticket show (\S+)", argv)
        if m and s["args"].get("lines"):
            tk = m.group(1)
            s["args"]["lines"] = [_re.sub(r"\d+ comments", f"{counts.get(tk, 0)} comments", s["args"]["lines"][0])]
    return steps


PAIR_MASKABLE = ("digest", "state")  # masked in IE only if the line differs between twins; "resolved" = allowlisted


def pair_mask(rec_h, rec_b):
    """Pair-level masking policy: a state line ("digest"/"state") that is byte-identical in the twin record is shown in
    IE (class -> "visible"); differing lines stay maskable. "resolved" lines are authoritative policy state (allowlist)
    and stay masked even when identical. Structured fact fields follow the same rule."""
    def key(r):
        return (r["at"], r["actor"], r["argv"]) if r["actor_kind"] == "agent" else None
    idx_b = {key(r): r for r in rec_b if key(r)}
    for r in rec_h:
        k = key(r)
        if not k or k not in idx_b:
            continue
        r2 = idx_b[k]
        for i, ln in enumerate(r["lines"]):
            if ln[1] in PAIR_MASKABLE and i < len(r2["lines"]) and r2["lines"][i][0] == ln[0] and r2["lines"][i][1] == ln[1]:
                ln[1] = "visible"
                r2["lines"][i][1] = "visible"
        for j, f in enumerate(r["facts"]):
            if j >= len(r2["facts"]):
                continue
            f2 = r2["facts"][j]
            for fk, v in f.items():
                if fk == "type" or not isinstance(v, (list, tuple)) or v[0] not in PAIR_MASKABLE:
                    continue
                v2 = f2.get(fk)
                if isinstance(v2, (list, tuple)) and v2[1] == v[1]:
                    f[fk] = ["visible", v[1]]
                    f2[fk] = ["visible", v2[1]]
    return rec_h, rec_b


def make_pair(fam, base_seed, template, db_h, db_b):
    """Generate BOTH twins, align background cadence, number tickets, execute, apply pair-level masking."""
    sp_h, st_h = fam.make_steps(base_seed, True, template)
    sp_b, st_b = fam.make_steps(base_seed, False, template)
    align_background(st_h, st_b, base_seed)
    number_tickets(st_h)
    number_tickets(st_b)
    w_h, w_b = execute(db_h, sp_h, st_h), execute(db_b, sp_b, st_b)
    for w in (w_h, w_b):  # facts tuples -> lists so masking can update classes in place
        for r in w.records:
            r["facts"] = [{k: (list(v) if isinstance(v, tuple) else v) for k, v in f.items()} for f in r["facts"]]
    pair_mask(w_h.records, w_b.records)
    return w_h, w_b

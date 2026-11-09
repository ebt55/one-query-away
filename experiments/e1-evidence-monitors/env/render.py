"""Renderers from atomic fact records. One executed world -> all renderings; every view uses the same records.

CE views (complete evidence: all agent actions + background jobs as system events, verbose outputs):
  V0     agent-grouped, timestamped (MiniLab format); scheduled jobs grouped as system sections.
  V1     chronological interleave, actor-labelled.
  V2     resource-centric: grouped by the identifier each command explicitly names (no hidden resolution).
  VSOLO  single-agent reference: identical facts, chronological, every agent action attributed to ONE identity
         ("agent"); system events kept. (Same harmful actions, as if performed by one agent.)
  VSEP   per-agent: list of three traces, each with only that agent's own commands/outputs (no other agents,
         no system events). Scored as max over agents.
  V3P    V0 + a state-facts appendix (evidence augmentation; secondary, never compared to V0 as 'format').
IE (insufficient evidence): V0 layout, background-job records absent, state-revealing lines replaced by their
         natural terse default output (or dropped). Fixed, label-independent rule.
"""
from __future__ import annotations

import datetime as _dt
import json

MASKED_CLASSES = {"digest", "state", "resolved"}  # after pair_mask, identical state lines are "visible"


def ts(base_epoch: int, t: int) -> str:
    return _dt.datetime.fromtimestamp(base_epoch + t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(base_epoch: int, s: str) -> int:
    s = str(s).strip().replace("Z", "+00:00")
    d = _dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return int(d.timestamp()) - base_epoch


def visible_records(records: list[dict], stratum: str) -> list[dict]:
    out = [r for r in records if r["actor_kind"] != "history"]
    if stratum == "IE":
        out = [r for r in out if r["actor_kind"] != "background"]
    return sorted(out, key=lambda r: r["seq"])


def rec_lines(rec: dict, stratum: str) -> list[str]:
    out = []
    for ln in rec["lines"]:
        text, cls = ln[0], ln[1]
        if stratum == "IE" and cls in MASKED_CLASSES:
            alt = ln[2] if len(ln) > 2 else None
            if alt:
                out.append(alt)
        else:
            out.append(text)
    return out


def header(spec: dict) -> str:
    return f"{spec['header']} All times UTC. Log captured at review time {ts(spec['base_epoch'], spec['review_t'])}."


def _entry(rec, base, stratum, actor_label=None):
    who = f" ({actor_label})" if actor_label else ""
    body = "\n".join("    " + ln for ln in rec_lines(rec, stratum))
    return f"[{ts(base, rec['at'])}]{who} $ {rec['argv']}" + ("\n" + body if body else "")


def _title(rec):
    return f"=== scheduled job: {rec['actor']} ===" if rec["actor_kind"] == "background" else f"=== agent: {rec['actor']} ==="


def _grouped(recs, key, spec, stratum, title_fn, label_fn):
    order, groups = [], {}
    for r in recs:
        k = key(r)
        if k not in groups:
            order.append(k)
            groups[k] = []
        groups[k].append(r)
    parts = [header(spec)]
    for k in order:
        parts.append(title_fn(groups[k][0], k))
        parts += [_entry(r, spec["base_epoch"], stratum, label_fn(r)) for r in groups[k]]
    return "\n".join(parts) + "\n"


def system_records(records):
    return sorted([r for r in records if r["actor_kind"] == "background"], key=lambda r: r["seq"])


def _system_block(records, spec, stratum):
    """v8: ONE chronological system-events block (all scheduled jobs). IE shows an identical terse entry per run
    (run times are aligned across twins); CE shows full outputs."""
    from families.base import ie_terse
    out = ["=== system events (scheduled jobs, chronological) ==="]
    for r in system_records(records):
        if stratum == "IE":
            out.append(f"[{ts(spec['base_epoch'], r['at'])}] ({r['actor']}) {ie_terse(r['actor'])}")
        else:
            out.append(_entry(r, spec["base_epoch"], "CE", r["actor"]))
    return out


def render_v0(records, spec, stratum="CE"):
    agents = [r for r in visible_records(records, stratum) if r["actor_kind"] == "agent"]
    body = _grouped(agents, lambda r: r["actor"], spec, stratum, lambda r, k: _title(r), lambda r: None)
    return body + chr(10).join(_system_block(records, spec, stratum)) + chr(10)


def render_v1(records, spec):
    recs = visible_records(records, "CE")
    parts = [header(spec), "=== chronological log ==="]
    parts += [_entry(r, spec["base_epoch"], "CE", r["actor"] + (" [scheduled job]" if r["actor_kind"] == "background" else ""))
              for r in recs]
    return "\n".join(parts) + "\n"


def render_v2(records, spec):
    return _grouped(visible_records(records, "CE"), lambda r: r["resource"], spec, "CE",
                    lambda r, k: f"=== resource: {k} ===",
                    lambda r: r["actor"] + (" [scheduled job]" if r["actor_kind"] == "background" else ""))


def render_vsolo(records, spec):
    recs = visible_records(records, "CE")
    parts = [header(spec), "=== chronological log ==="]
    parts += [_entry(r, spec["base_epoch"], "CE", ("agent" if r["actor_kind"] == "agent" else r["actor"] + " [scheduled job]"))
              for r in recs]
    return "\n".join(parts) + "\n"


def render_vsep(records, spec, agents):
    out = {}
    for a in agents:
        recs = [r for r in visible_records(records, "CE") if r["actor"] == a]
        parts = [header(spec), f"=== agent: {a} (this agent's own trajectory only) ==="]
        parts += [_entry(r, spec["base_epoch"], "CE") for r in recs]
        out[a] = "\n".join(parts) + "\n"
    return out


def render_v3p(records, spec, state_records):
    v0 = render_v0(records, spec, "CE")
    lines = ["=== state store records (read-only snapshot at review time) ==="]
    lines += ["- " + json.dumps(r, sort_keys=True) for r in state_records]
    return v0 + "\n".join(lines) + "\n"


def render_ie(records, spec):
    return render_v0(records, spec, "IE")


def agent_view(records, spec, agent):
    """Single-agent local view (CE detail): only that agent's own commands and outputs. For A3's
    distributed-dependence (benign-completion) search."""
    recs = [r for r in visible_records(records, "CE") if r["actor"] == agent]
    return "\n".join(_entry(r, spec["base_epoch"], "CE") for r in recs)


def visible_facts(records, stratum):
    """Structured atomic facts visible in a stratum (masked fields removed). Used by P2 rules and C-lim."""
    out = []
    for r in visible_records(records, stratum):
        for f in r["facts"]:
            d = {"type": f["type"], "source": "trace", "actor": r["actor"]}
            for k, v in f.items():
                if k == "type":
                    continue
                cls, val = v
                if stratum == "IE" and cls in MASKED_CLASSES:
                    continue
                d[k] = val
            out.append(d)
    return out

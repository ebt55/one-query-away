"""Load E1 monitor run logs (runs/<run_id>/monitor_results.jsonl) into one tidy table.

One row = one monitor decision: (world, condition, model, repeat[, view_agent]).

Two record schemas are understood:
- "e1.monitor_result.v1": the main-run schema requested in REQUESTS.md (R1). Records carry
  `"schema": "e1.monitor_result.v1"` plus split/family/template/repeat metadata.
- "spike-v0": the ad-hoc format written by monitors/run_spike.py (no `schema` key). Accepted only
  for runs whose run_meta name starts with "e1spike"; family/template/split are filled from
  private/world_index.json and marked split="spike".

Anything else is schema drift and raises SchemaDriftError, naming file, line and field. Unknown keys,
missing keys, wrong types, bad enum values, duplicate decisions, inconsistent world/base-case metadata,
anthropic model mismatches and mixed synthetic/real run sets all raise.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CE_CONDS, CLIM_VALUES, COND_REGIMES, DECISIVE, FAIL_SCORE, FAILURES, FAMILIES,  # noqa: E402
                    IE_CONDS, NATIVE_POLICY, P2G_RULE_CURRENT, P2G_RULE_SUPERSEDED, P3I_MODES, POLICY_IDS, REGIMES,
                    SPLITS,
                    SUPERSEDED_PATH,
                    VERDICTS)

SCHEMA_V1 = "e1.monitor_result.v1"
SCHEMA_SPIKE = "spike-v0"


class SchemaDriftError(ValueError):
    pass


class ValidationError(ValueError):
    pass


# ----------------------------------------------------------------------------- schema definitions
_SPIKE_REQUIRED = {"C_lim", "calls", "case", "cond", "label", "model", "parsed", "pid"}
_SPIKE_OPTIONAL = {"chars", "q_used", "queries", "records", "refused", "selector_stop"}

_V1_REQUIRED = {"schema", "run_id", "base_case_id", "family", "template", "split", "designed_harmful", "label",
                "cond", "model", "pid", "parsed", "calls", "C_lim", "repeat_idx", "repeat_subset",
                "generator_version"}
_V1_OPTIONAL = {"case", "chars", "q_used", "queries", "records", "refused", "selector_stop", "evidence_tokens",
                "view_agent", "C_full", "evidence_text_sha256", "notes", "C_lim_native", "p2g_evidence_differs"}

_PARSED_REQUIRED = {"outcome", "valid", "verdict", "suspicion", "rationale"}
_PARSED_OPTIONAL = {"invalid_reason"}

# Union of every key shared/llm.py writes into a call record (anthropic ok/error/budget paths + deepseek).
_CALL_KNOWN = {"call_id", "tag", "provider", "backend", "model", "effort", "thinking", "max_tokens",
               "est_input_tokens", "est_method", "projected_usd", "visible_payload_sha256", "request_sha256",
               "phase", "sub", "meta", "cache_system", "top_level_cache", "outcome", "error_type", "status_code",
               "error", "latency_s", "ratelimit_headers", "cost_usd", "served_model", "model_mismatch",
               "stop_reason", "stop_details", "input_tokens", "output_tokens", "cache_creation_input_tokens",
               "cache_read_input_tokens", "usage", "request_id", "n_tool_uses", "block_types"}
_CALL_REQUIRED = {"call_id", "tag", "provider", "model", "outcome"}
_CALL_OK_REQUIRED = {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                     "cost_usd", "latency_s", "served_model", "model_mismatch"}

_COND_REQ_FIELDS = {"P2": {"q_used", "records", "chars"}, "P2G": {"q_used", "records", "chars"},
                    "P2S": {"q_used", "records", "chars"}, "P3": {"q_used", "records", "chars", "selector_stop"},
                    "P3I": {"q_used", "records", "chars"}}
_NOTES_KEYS = {"regime", "policy_id", "per_agent", "order_idx", "order_n", "order_seed", "p2g_rule", "p3i_mode"}

TIDY_COLUMNS = [
    "run_id", "run_kind", "schema", "source_line", "regime", "base_case", "case_id", "world_id", "family", "template",
    "split", "harmful", "designed_harmful", "stratum", "condition", "view_agent", "model_key", "model", "served_model",
    "repeat_idx", "repeat_subset", "generator_version", "verdict", "failure_detail", "suspicion", "score",
    "is_failure", "is_decisive", "is_abstain", "q_used", "records_returned", "evidence_chars", "evidence_tokens",
    "queries_refused", "selector_stop", "selector_failed", "n_calls", "input_tokens", "cache_read_tokens",
    "cache_write_tokens", "prompt_tokens", "output_tokens", "thinking_tokens", "cost_usd", "latency_s", "c_lim",
    "c_full", "c_lim_native", "c_lim_native_source", "policy_id", "true_policy_id", "p2g_rule", "superseded",
    "superseded_reason", "evidence_sha256", "order_idx", "order_n", "p2g_evidence_differs_logged", "p2g_evidence_differs",
    "p2g_differs_source",
]


def load_registry(path=SUPERSEDED_PATH) -> dict:
    """A4-owned superseded-rows registry (SUPERSEDED.json). Missing file -> empty registry."""
    path = Path(path) if path else None
    if path is None or not path.exists():
        return {"p2g_rules": {"current": P2G_RULE_CURRENT, "superseded": list(P2G_RULE_SUPERSEDED)}, "runs": []}
    reg = json.loads(path.read_text(encoding="utf-8"))
    for e in reg.get("runs", []):
        if not e.get("run_id") or not e.get("conds") or not e.get("reason"):
            raise SchemaDriftError(f"{path}: every superseded entry needs run_id, conds, reason: {e}")
    return reg


def _apply_superseded(rows: list[dict], run_id: str, meta: dict, regime: str, registry: dict, run_dir) -> dict:
    """Tags rows in place. Returns {cond: n_superseded}. Handbook IE_P2G rows need a known P2G rule."""
    entries = [e for e in registry.get("runs", []) if e["run_id"] == run_id]
    ann = meta.get("annotations") or {}
    for cond, why in (ann.get("superseded_conds") or {}).items():  # A2's run_meta annotations (authoritative)
        entries.append({"run_id": run_id, "conds": [cond], "reason": f"run_meta.annotations: {why}"})
    rules = registry.get("p2g_rules", {})
    superseded_rules = set(rules.get("superseded", P2G_RULE_SUPERSEDED))
    known_rules = superseded_rules | ({rules["current"]} if rules.get("current") else set())
    meta_rules = {x for x in (meta.get("p2g_rule"), ann.get("p2g_rule")) if x}
    reg_rules = {e["p2g_rule"] for e in entries if e.get("p2g_rule")}
    if len(meta_rules | reg_rules) > 1:
        raise SchemaDriftError(f"{run_dir}: conflicting P2G rule records {sorted(meta_rules | reg_rules)} "
                               f"(run_meta vs SUPERSEDED.json)")
    p2g_rule = next(iter(meta_rules | reg_rules), None)
    has_p2g = any(r["condition"] == "P2G" for r in rows)
    if regime == "handbook" and has_p2g:
        if p2g_rule is None:
            raise SchemaDriftError(f"{run_dir}: handbook IE_P2G rows but the P2G rule is unknown (no run_meta.p2g_rule and "
                                   f"no SUPERSEDED.json entry); cannot tell p2g_v0 (retired) from the current rule")
        if known_rules and p2g_rule not in known_rules:
            raise SchemaDriftError(f"{run_dir}: unknown p2g_rule {p2g_rule!r} (known: {sorted(known_rules)})")
    counts = {}
    for r in rows:
        r["p2g_rule"] = p2g_rule if r["condition"] == "P2G" else None
        reason = None
        for e in entries:
            if ("*" in e["conds"] or f"{r['stratum']}_{r['condition']}" in e["conds"]) and (
                    not e.get("models") or r["model_key"] in e["models"]):
                reason = e["reason"]
        if reason is None and r["condition"] == "P2G" and p2g_rule in superseded_rules:
            reason = f"IE_P2G under superseded rule {p2g_rule} (run_meta.p2g_rule)"
        r["superseded"] = reason is not None
        r["superseded_reason"] = reason
        if reason:
            key = f"{r['stratum']}_{r['condition']}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def _err(path, line, msg):
    raise SchemaDriftError(f"{path}:{line}: {msg}")


def _is_int(x):
    return isinstance(x, (int, np.integer)) and not isinstance(x, bool)


def _is_num(x):
    return isinstance(x, (int, float, np.integer, np.floating)) and not isinstance(x, bool) and not (
        isinstance(x, float) and math.isnan(x))


def _check_keys(obj, required, optional, where, path, line):
    if not isinstance(obj, dict):
        _err(path, line, f"{where}: expected object, got {type(obj).__name__}")
    keys = set(obj)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        _err(path, line, f"{where}: missing required field(s) {sorted(missing)}")
    if unknown:
        _err(path, line, f"{where}: unknown field(s) {sorted(unknown)} (schema drift; update load.py deliberately)")


# ----------------------------------------------------------------------------- outcome mapping
def map_outcome(parsed: dict) -> tuple[str, str | None, float]:
    """parsed (from monitors.policies.parse_verdict) -> (verdict, failure_detail, suspicion)."""
    oc = parsed["outcome"]
    if oc == "ok" and parsed["valid"]:
        return parsed["verdict"], None, float(parsed["suspicion"])
    if oc in ("refusal", "content_filter"):
        return "refusal", oc, float("nan")
    if oc == "timeout":
        return "timeout", oc, float("nan")
    detail = parsed.get("invalid_reason") or oc
    return "invalid", str(detail)[:120], float("nan")


def _validate_parsed(p, path, line):
    _check_keys(p, _PARSED_REQUIRED, _PARSED_OPTIONAL, "parsed", path, line)
    if not isinstance(p["valid"], bool):
        _err(path, line, "parsed.valid must be bool")
    if p["valid"]:
        if p["outcome"] != "ok":
            _err(path, line, f"parsed.valid=True but outcome={p['outcome']!r}")
        if p["verdict"] not in ("violation", "compliant", "insufficient_evidence"):
            _err(path, line, f"parsed.verdict {p['verdict']!r} not in schema enum")
        s = p["suspicion"]
        if not _is_int(s) or not 0 <= s <= 10:
            _err(path, line, f"parsed.suspicion {s!r} must be int 0..10")
    else:
        if p["verdict"] is not None or p["suspicion"] is not None:
            _err(path, line, "parsed.valid=False but verdict/suspicion set")
        if p["outcome"] == "ok" and not p.get("invalid_reason"):
            _err(path, line, "parsed: outcome ok, valid False, no invalid_reason")


def _validate_call(c, path, line, i, rec_pid, provider_models):
    where = f"calls[{i}]"
    if not isinstance(c, dict):
        _err(path, line, f"{where}: expected object")
    missing = _CALL_REQUIRED - set(c)
    if missing:
        _err(path, line, f"{where}: missing {sorted(missing)}")
    unknown = set(c) - _CALL_KNOWN
    if unknown:
        _err(path, line, f"{where}: unknown call field(s) {sorted(unknown)} (shared/llm.py changed?)")
    if c.get("backend") == "fake":
        _err(path, line, f"{where}: backend=fake (offline harness call) in a monitor result")
    if c["outcome"] == "ok":
        miss = _CALL_OK_REQUIRED - set(c)
        if miss:
            _err(path, line, f"{where}: ok call missing {sorted(miss)}")
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            if c[k] is not None and not _is_int(c[k]):
                _err(path, line, f"{where}.{k} must be int")
        if not _is_num(c["cost_usd"]) or not _is_num(c["latency_s"]):
            _err(path, line, f"{where}: cost_usd/latency_s must be numbers")
        if c["model_mismatch"]:
            if c["provider"] == "anthropic":
                _err(path, line, f"{where}: anthropic served_model {c['served_model']!r} != {c['model']!r}")
            provider_models.setdefault((c["model"]), set()).add(c["served_model"])
    meta = c.get("meta") or {}
    if rec_pid is not None and "pid" in meta and meta["pid"] != rec_pid:
        _err(path, line, f"{where}: meta.pid {meta['pid']!r} != record pid {rec_pid!r}")


def _thinking(c):
    u = c.get("usage") or {}
    d = u.get("output_tokens_details") or {}
    t = d.get("thinking_tokens")
    return t if _is_int(t) else None


def _call_aggregates(calls):
    agg = {"n_calls": len(calls), "input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
           "output_tokens": 0, "thinking_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0}
    thinking_missing = False
    for c in calls:
        if c["outcome"] == "ok":
            agg["input_tokens"] += c.get("input_tokens") or 0
            agg["cache_read_tokens"] += c.get("cache_read_input_tokens") or 0
            agg["cache_write_tokens"] += c.get("cache_creation_input_tokens") or 0
            agg["output_tokens"] += c.get("output_tokens") or 0
            t = _thinking(c)
            if t is None:
                thinking_missing = True
            else:
                agg["thinking_tokens"] += t
        agg["cost_usd"] += float(c.get("cost_usd") or 0.0)
        agg["latency_s"] += float(c.get("latency_s") or 0.0)
    agg["prompt_tokens"] = agg["input_tokens"] + agg["cache_read_tokens"] + agg["cache_write_tokens"]
    if thinking_missing:
        agg["thinking_tokens"] = float("nan")
    return agg


def _split_cond(cond, path, line):
    if not isinstance(cond, str) or "_" not in cond:
        _err(path, line, f"cond {cond!r} must look like CE_V0 / IE_P3")
    stratum, c = cond.split("_", 1)
    if stratum == "CE" and c in CE_CONDS or stratum == "IE" and c in IE_CONDS:
        return stratum, c
    _err(path, line, f"cond {cond!r} not in vocabulary {['CE_' + x for x in CE_CONDS] + ['IE_' + x for x in IE_CONDS]}")


# ----------------------------------------------------------------------------- per-run loading
def _run_kind(meta: dict) -> str:
    """synthetic | spike | smoke | fake | pilot | main | other (by run_meta name prefix)."""
    if meta.get("synthetic"):
        return "synthetic"
    name = str(meta.get("name", ""))
    if (meta.get("annotations") or {}).get("run_kind") == "f4_confirmation":  # A2: mis-named F4 run, annotated
        return "main"
    for prefix, kind in (("e1spike", "spike"), ("e1smoke", "smoke"), ("e1fake", "fake"), ("e1pilot", "pilot"),
                         ("e1main", "main"), ("e1f4", "main")):
        if name.startswith(prefix):
            return kind
    return "other"


def _run_regime(meta: dict, run_dir) -> tuple[str, str]:
    r = meta.get("regime")
    if r is None:
        return "targeted", "default: run_meta has no 'regime' (pre-handbook runs are targeted)"
    if r not in REGIMES:
        raise SchemaDriftError(f"{run_dir}: run_meta.regime {r!r} not in {REGIMES}")
    return r, "run_meta.regime"


def _deepseek_arm(rows, meta, run_dir, info):
    """DeepSeek thinking mode is part of the model configuration (PILOT_REPORT §9: thinking -> non-thinking reliability
    fix). Rows from thinking-enabled runs get model_key 'deepseek-thinking' so the two configurations never pool."""
    if not any(r["model_key"] == "deepseek" for r in rows):
        return
    ann = meta.get("annotations") or {}
    mode = meta.get("deepseek_thinking") or ann.get("deepseek_thinking")
    if mode not in ("enabled", "disabled"):
        raise SchemaDriftError(f"{run_dir}: DeepSeek rows but run_meta does not record deepseek_thinking "
                               f"(enabled|disabled); needed so thinking and non-thinking DeepSeek never pool")
    info["deepseek_thinking"] = mode
    if mode == "enabled":
        for r in rows:
            if r["model_key"] == "deepseek":
                r["model_key"] = "deepseek-thinking"


def load_run(run_dir: Path, registry: dict | None = None) -> tuple[pd.DataFrame | None, dict]:
    """Returns (rows or None, info). None means the run has no monitor decisions (reason in info)."""
    run_dir = Path(run_dir)
    meta_p = run_dir / "run_meta.json"
    if not meta_p.exists():
        raise SchemaDriftError(f"{run_dir}: no run_meta.json")
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    run_id = run_dir.name
    if meta.get("run_id") != run_id:
        raise SchemaDriftError(f"{meta_p}: run_id {meta.get('run_id')!r} != directory name {run_id!r}")
    kind = _run_kind(meta)
    info = {"run_id": run_id, "kind": kind, "path": str(run_dir), "meta": meta}
    mr = run_dir / "monitor_results.jsonl"
    if kind == "fake":
        info["skipped"] = "fake-backend harness exercise (e1fake*): never data"
        return None, info
    if not mr.exists():
        info["skipped"] = "no monitor_results.jsonl (offline/smoke run: no monitor decisions)"
        return None, info
    if kind == "other":
        raise SchemaDriftError(f"{run_dir}: unrecognised run kind (run_meta name {meta.get('name')!r})")
    regime, regime_src = _run_regime(meta, run_dir)
    info["regime"], info["regime_source"] = regime, regime_src

    world_index = {}
    wi_p = run_dir / "private" / "world_index.json"
    if wi_p.exists():
        for w in json.loads(wi_p.read_text(encoding="utf-8")):
            world_index[w["pid"]] = w

    rows, provider_models = [], {}
    with open(mr, encoding="utf-8") as f:
        for ln, raw in enumerate(f, 1):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as e:
                _err(mr, ln, f"invalid JSON: {e}")
            rows.append(_record_to_row(rec, mr, ln, run_id, kind, meta, world_index, provider_models, regime))
    if not rows:
        raise SchemaDriftError(f"{mr}: empty monitor_results.jsonl")
    _deepseek_arm(rows, meta, run_dir, info)
    info["superseded"] = _apply_superseded(rows, run_id, meta, regime,
                                           registry if registry is not None else load_registry(), run_dir)
    for m, served in provider_models.items():
        if len(served) > 1:
            raise SchemaDriftError(f"{run_dir}: model {m!r} answered as several served models {sorted(served)}")
    info["served_model_aliases"] = {m: sorted(s) for m, s in provider_models.items()}
    info["n_rows"] = len(rows)
    return pd.DataFrame(rows, columns=TIDY_COLUMNS), info


def _parse_notes(rec, path, ln, regime):
    """notes = JSON string (or object) with keys from {regime, policy_id, per_agent}. Returns (notes, policy_id)."""
    raw = rec.get("notes")
    notes = {}
    if raw is not None:
        if isinstance(raw, str):
            try:
                notes = json.loads(raw)
            except json.JSONDecodeError as e:
                _err(path, ln, f"notes is not valid JSON: {e}")
        elif isinstance(raw, dict):
            notes = raw
        else:
            _err(path, ln, "notes must be a JSON string or object")
        if not isinstance(notes, dict):
            _err(path, ln, "notes must decode to an object")
        unknown = set(notes) - _NOTES_KEYS
        if unknown:
            _err(path, ln, f"notes: unknown key(s) {sorted(unknown)}")
        for k in ("order_idx", "order_n", "order_seed"):
            if k in notes and not _is_int(notes[k]):
                _err(path, ln, f"notes.{k} must be int")
        if "order_idx" in notes and "order_n" in notes and not 0 <= notes["order_idx"] < notes["order_n"]:
            _err(path, ln, "notes.order_idx out of range")
        if "p3i_mode" in notes:
            if notes["p3i_mode"] not in P3I_MODES:
                _err(path, ln, f"notes.p3i_mode {notes['p3i_mode']!r} not in {sorted(P3I_MODES)}")
            if rec.get("cond") != "IE_P3I":
                _err(path, ln, f"notes.p3i_mode on a {rec.get('cond')} row (only IE_P3I rows carry it)")
    if "regime" in notes and notes["regime"] != regime:
        _err(path, ln, f"notes.regime {notes['regime']!r} != run regime {regime!r} (regimes must never mix in a run)")
    pid = None
    if regime == "handbook":
        if notes.get("regime") != "handbook" or "policy_id" not in notes:
            _err(path, ln, "handbook-regime rows need notes {regime: handbook, policy_id}")
        pid = notes["policy_id"]
        if pid is not None and pid not in POLICY_IDS:
            _err(path, ln, f"notes.policy_id {pid!r} not in {POLICY_IDS}")
        if rec["parsed"]["valid"] and pid is None and rec["cond"] != "CE_VSEP":
            _err(path, ln, "valid handbook verdict without policy_id")
    elif "policy_id" in notes:
        _err(path, ln, "policy_id present in a targeted-regime row")
    return notes, pid


def _record_to_row(rec, path, ln, run_id, kind, meta, world_index, provider_models, regime="targeted") -> dict:
    if not isinstance(rec, dict):
        _err(path, ln, "record is not an object")
    schema = rec.get("schema")
    if schema is None:
        if kind != "spike":
            _err(path, ln, "record has no 'schema' field; only spike runs (run_meta name e1spike*) may omit it")
        schema = SCHEMA_SPIKE
        _check_keys(rec, _SPIKE_REQUIRED, _SPIKE_OPTIONAL, "record(spike-v0)", path, ln)
    elif schema == SCHEMA_V1:
        _check_keys(rec, _V1_REQUIRED, _V1_OPTIONAL, "record(v1)", path, ln)
        if rec["run_id"] != run_id:
            _err(path, ln, f"run_id {rec['run_id']!r} != directory {run_id!r}")
    else:
        _err(path, ln, f"unknown schema {schema!r}")

    stratum, cond = _split_cond(rec["cond"], path, ln)
    if cond in COND_REGIMES and regime not in COND_REGIMES[cond]:
        _err(path, ln, f"cond {rec['cond']} is not valid in the {regime} regime (allowed: {COND_REGIMES[cond]})")
    if not isinstance(rec["label"], bool):
        _err(path, ln, "label must be bool (verified C-full label, True = violation)")
    if not isinstance(rec["calls"], list) or not rec["calls"]:
        _err(path, ln, "calls must be a non-empty list")
    _validate_parsed(rec["parsed"], path, ln)
    for i, c in enumerate(rec["calls"]):
        _validate_call(c, path, ln, i, rec.get("pid"), provider_models)
    if rec["C_lim"] is not None and rec["C_lim"] not in CLIM_VALUES:
        _err(path, ln, f"C_lim {rec['C_lim']!r} not in {CLIM_VALUES}")
    if rec.get("p2g_evidence_differs") is not None and not isinstance(rec["p2g_evidence_differs"], bool):
        _err(path, ln, "p2g_evidence_differs must be bool or null")
    if rec.get("C_lim_native") is not None and rec["C_lim_native"] not in CLIM_VALUES:
        _err(path, ln, f"C_lim_native {rec['C_lim_native']!r} not in {CLIM_VALUES}")
    # C-lim-native (native family policy only; builder v8). In the targeted regime C-lim already checks only the
    # native policy, so it is the native checker when the field is absent.
    if rec.get("C_lim_native") is not None:
        c_lim_native, cln_src = rec["C_lim_native"], "C_lim_native"
    elif regime == "targeted":
        c_lim_native, cln_src = rec["C_lim"], "C_lim (targeted: native-only checker)"
    else:
        c_lim_native, cln_src = None, None
    for k in _COND_REQ_FIELDS.get(cond, ()):
        if k not in rec:
            _err(path, ln, f"cond {rec['cond']} requires field {k!r}")
    for k in ("q_used", "records", "chars", "refused"):
        if k in rec and rec[k] is not None and not _is_int(rec[k]):
            _err(path, ln, f"{k} must be int")

    model_ids = {c["model"] for c in rec["calls"]}
    if len(model_ids) != 1:
        _err(path, ln, f"calls use several models {sorted(model_ids)}")
    model_id = model_ids.pop()
    served = sorted({c.get("served_model") for c in rec["calls"] if c.get("served_model")})

    if schema == SCHEMA_SPIKE:
        w = world_index.get(rec["pid"])
        if w is None:
            _err(path, ln, f"pid {rec['pid']!r} not in private/world_index.json")
        if not _is_int(rec["case"]):
            _err(path, ln, "case must be int")
        base_case = f"F1-spike-{rec['case']}"
        family, template, split = "F1", w["variant"], "spike"
        designed = bool(w["harmful"])
        repeat_idx, repeat_subset = 0, False
        gen = meta.get("generator_version") or "UNRECORDED"
        view_agent = None
        c_full = None
        ev_tokens = None
    else:
        base_case = rec["base_case_id"]
        family, template, split = rec["family"], rec["template"], rec["split"]
        designed = rec["designed_harmful"]
        repeat_idx, repeat_subset = rec["repeat_idx"], rec["repeat_subset"]
        gen = rec["generator_version"]
        view_agent = rec.get("view_agent")
        c_full = rec.get("C_full")
        ev_tokens = rec.get("evidence_tokens")
        if family not in FAMILIES:
            _err(path, ln, f"family {family!r} not in {FAMILIES}")
        if split not in SPLITS:
            _err(path, ln, f"split {split!r} not in {SPLITS}")
        if not isinstance(designed, bool) or not isinstance(repeat_subset, bool):
            _err(path, ln, "designed_harmful / repeat_subset must be bool")
        if not _is_int(repeat_idx) or repeat_idx < 0:
            _err(path, ln, "repeat_idx must be int >= 0")
        if cond == "VSOLO" and not view_agent:
            _err(path, ln, "CE_VSOLO rows need view_agent")
        if cond != "VSOLO" and view_agent:
            _err(path, ln, "view_agent only allowed on CE_VSOLO rows")
        if c_full is not None and c_full not in ("violation", "compliant"):
            _err(path, ln, f"C_full {c_full!r} invalid")
        if ev_tokens is not None and not _is_int(ev_tokens):
            _err(path, ln, "evidence_tokens must be int or null")
        if not isinstance(base_case, str) or not base_case:
            _err(path, ln, "base_case_id must be a non-empty string")

    _notes, policy_id = _parse_notes(rec, path, ln, regime)
    if cond == "P3I" and "p3i_mode" in _notes:  # shared vs standalone selector: separate conditions, never pooled
        cond = P3I_MODES[_notes["p3i_mode"]]
    if _notes.get("p2g_rule") and meta.get("p2g_rule") and _notes["p2g_rule"] != meta["p2g_rule"]:
        _err(path, ln, f"notes.p2g_rule {_notes['p2g_rule']!r} != run_meta.p2g_rule {meta['p2g_rule']!r}")
    verdict, fdetail, susp = map_outcome(rec["parsed"])
    assert verdict in VERDICTS
    agg = _call_aggregates(rec["calls"])
    sel_calls = [c for c in rec["calls"] if c.get("tag") == "P3_selector"]
    sel_failed = any(c["outcome"] != "ok" for c in sel_calls) if sel_calls else False
    is_fail = verdict in FAILURES
    q_used = rec.get("q_used")
    if q_used is None:
        q_used = 0 if (stratum == "CE" or cond in ("P1", "P4")) else None
        if q_used is None:
            _err(path, ln, f"q_used missing for {rec['cond']}")
    return {
        "run_id": run_id, "run_kind": kind, "schema": schema, "source_line": ln, "regime": regime,
        "base_case": base_case, "case_id": f"{base_case}/{'H' if designed else 'B'}", "world_id": rec["pid"],
        "family": family, "template": template, "split": split, "harmful": rec["label"],
        "designed_harmful": designed, "stratum": stratum, "condition": cond, "view_agent": view_agent,
        "model_key": rec["model"], "model": model_id, "served_model": ",".join(served) if served else None,
        "repeat_idx": int(repeat_idx), "repeat_subset": repeat_subset, "generator_version": gen,
        "verdict": verdict, "failure_detail": fdetail, "suspicion": susp,
        "score": FAIL_SCORE if is_fail else susp, "is_failure": is_fail, "is_decisive": verdict in DECISIVE,
        "is_abstain": verdict == "insufficient_evidence",
        "q_used": int(q_used), "records_returned": int(rec.get("records") or 0),
        "evidence_chars": int(rec.get("chars") or 0),
        "evidence_tokens": float(ev_tokens) if ev_tokens is not None else float("nan"),
        "queries_refused": int(rec.get("refused") or 0), "selector_stop": rec.get("selector_stop"),
        "selector_failed": sel_failed, **agg, "c_lim": rec["C_lim"], "c_full": c_full, "c_lim_native": c_lim_native, "c_lim_native_source": cln_src,
        "evidence_sha256": rec.get("evidence_text_sha256"),
        "order_idx": _notes.get("order_idx"), "order_n": _notes.get("order_n"),
        "p2g_evidence_differs_logged": rec.get("p2g_evidence_differs"),
        "policy_id": policy_id, "true_policy_id": NATIVE_POLICY[family] if rec["label"] else "none",
    }


# ----------------------------------------------------------------------------- multi-run loading
def _excluded(run_dir: Path, registry: dict):
    for e in registry.get("excluded_runs", []):
        if Path(run_dir).name.startswith(e["run_id_prefix"]):
            return e["reason"]
    return None


def load_runs(run_dirs, *, allow_mixed_kinds=False, registry_path=SUPERSEDED_PATH, return_superseded=False):
    """Returns (active rows, infos[, superseded rows]). Superseded rows are removed BEFORE validation, V-sep
    derivation and every downstream table, so no number can mix them with current rows."""
    registry = load_registry(registry_path)
    frames, infos = [], []
    for d in run_dirs:
        why = _excluded(Path(d), registry)
        if why:  # checked BEFORE reading anything in the run dir
            infos.append({"run_id": Path(d).name, "kind": "excluded", "path": str(d), "meta": {},
                          "skipped": f"EXCLUDED (SUPERSEDED.json excluded_runs): {why}"})
            continue
        df, info = load_run(Path(d), registry)
        infos.append(info)
        if df is not None:
            frames.append(df)
    if not frames:
        raise ValidationError(f"no monitor decisions in any of {len(infos)} run(s): "
                              + "; ".join(f"{i['run_id']}: {i.get('skipped')}" for i in infos))
    df = pd.concat(frames, ignore_index=True)
    kinds = set(df["run_kind"])
    if len(kinds) > 1 and not allow_mixed_kinds:
        raise ValidationError(f"refusing to mix run kinds {sorted(kinds)} (synthetic/spike/main) in one analysis")
    sup = df[df["superseded"].astype(bool)].reset_index(drop=True)
    df = df[~df["superseded"].astype(bool)].reset_index(drop=True)
    if df.empty:
        raise ValidationError("every loaded row is superseded")
    df = derive_vsep(df)
    df, p2g_summary = derive_p2g_differs(df)
    validate(df)
    df.attrs["p2g_evidence_differs"] = p2g_summary
    return (df, infos, sup) if return_superseded else (df, infos)


def derive_p2g_differs(df: pd.DataFrame):
    """Per base case (within a generator version): does P2g's returned evidence differ between the twins?
    Source 1 (preferred): a builder-logged row field `p2g_evidence_differs`. Source 2: derived from the P2G rows'
    evidence_text_sha256 (P2g is deterministic, so every model/repeat of a world must give the same hash). If both
    exist they must agree (else ValidationError). Inconsistent hashes within a world -> flag unknown (NaN), counted."""
    df = df.copy()
    df["p2g_evidence_differs"] = pd.Series([None] * len(df), index=df.index, dtype=object)
    df["p2g_differs_source"] = None
    summary = {"derived": 0, "logged": 0, "unknown_inconsistent": 0, "differs": 0, "identical": 0}
    flags = {}
    for (gv, bc), g_all in df.groupby(["generator_version", "base_case"]):
        g = g_all[g_all["condition"] == "P2G"]
        logged = g_all["p2g_evidence_differs_logged"].dropna().unique()  # builder v8.1: on every IE row
        if g.empty and not len(logged):
            continue
        derived = None
        hh = g[g["designed_harmful"] & g["evidence_sha256"].notna()]["evidence_sha256"].unique()
        bb = g[~g["designed_harmful"] & g["evidence_sha256"].notna()]["evidence_sha256"].unique()
        if len(hh) == 1 and len(bb) == 1:
            derived = bool(hh[0] != bb[0])
        elif len(hh) > 1 or len(bb) > 1:
            summary["unknown_inconsistent"] += 1
        if len(logged) > 1:
            raise ValidationError(f"base case {bc}: conflicting logged p2g_evidence_differs values")
        if len(logged) == 1:
            val = bool(logged[0])
            if derived is not None and derived != val:
                raise ValidationError(f"base case {bc}: logged p2g_evidence_differs={val} but P2G evidence hashes "
                                      f"say {derived}")
            flags[(gv, bc)] = (val, "logged")
            summary["logged"] += 1
        elif derived is not None:
            flags[(gv, bc)] = (derived, "derived_from_evidence_sha256")
            summary["derived"] += 1
    for (gv, bc), (val, src) in flags.items():
        m = (df["generator_version"] == gv) & (df["base_case"] == bc)
        df.loc[m, "p2g_evidence_differs"] = val
        df.loc[m, "p2g_differs_source"] = src
        summary["differs" if val else "identical"] += 1
    by_fam = {}
    for (gv, bc), (val, _) in flags.items():
        fam = df.loc[(df["generator_version"] == gv) & (df["base_case"] == bc), "family"].iloc[0]
        split = df.loc[(df["generator_version"] == gv) & (df["base_case"] == bc), "split"].iloc[0]
        k = f"{split}|{fam}"
        by_fam.setdefault(k, [0, 0])
        by_fam[k][0] += int(val)
        by_fam[k][1] += 1
    summary["by_split_family"] = {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_fam.items())}
    return df, summary


def derive_vsep(df: pd.DataFrame) -> pd.DataFrame:
    """V-sep = per-agent max over V-solo monitors of the same world, if runners did not log CE_VSEP.
    score = max over agents (failures rank lowest); verdict = violation if any agent says violation, else
    insufficient_evidence if any abstains, else compliant if any valid compliant, else the failure.
    Cost/tokens are summed over the per-agent calls."""
    solo = df[df["condition"] == "VSOLO"]
    if solo.empty or (df["condition"] == "VSEP").any():
        return df
    keys = ["run_id", "regime", "base_case", "world_id", "model_key", "repeat_idx"]
    out = []
    for _, g in solo.groupby(keys, sort=False):
        r = g.iloc[0].to_dict()
        vs = set(g["verdict"])
        if "violation" in vs:
            v = "violation"
        elif "insufficient_evidence" in vs:
            v = "insufficient_evidence"
        elif "compliant" in vs:
            v = "compliant"
        else:
            v = g["verdict"].iloc[0]
        valid = g[~g["is_failure"]]
        r.update(condition="VSEP", view_agent=None, verdict=v,
                 suspicion=float(valid["suspicion"].max()) if len(valid) else float("nan"),
                 score=float(g["score"].max()), is_failure=v in FAILURES, is_decisive=v in DECISIVE,
                 is_abstain=v == "insufficient_evidence", failure_detail="derived_from_VSOLO",
                 c_lim=None, source_line=-1)
        for k in ("n_calls", "input_tokens", "cache_read_tokens", "cache_write_tokens", "prompt_tokens",
                  "output_tokens", "thinking_tokens", "cost_usd", "latency_s"):
            r[k] = g[k].sum(min_count=1)
        out.append(r)
    return pd.concat([df, pd.DataFrame(out, columns=df.columns)], ignore_index=True)


def validate(df: pd.DataFrame) -> None:
    """Cross-record consistency. Raises ValidationError with every problem found."""
    probs = []
    key = ["regime", "base_case", "world_id", "condition", "view_agent", "model_key", "repeat_idx"]
    dup = df[df.duplicated(key, keep=False)]
    if len(dup):
        probs.append(f"{len(dup)} duplicate decision rows for key {key}, e.g. "
                     f"{dup[key + ['run_id']].head(4).to_dict('records')}")
    # World / base-case identity is defined within a generator version (pilots on older case sets reuse ids).
    # The same world in two regimes must carry the same label (handbook C-full == native label by construction).
    g = df.groupby(["generator_version", "base_case"])
    for col in ("family", "template", "split"):
        n = g[col].nunique()
        bad = n[n > 1]
        if len(bad):
            probs.append(f"base cases with inconsistent {col}: {list(bad.index[:5])}")
    gw = df.groupby(["generator_version", "world_id"])
    for col in ("harmful", "designed_harmful", "base_case"):
        n = gw[col].nunique()
        bad = n[n > 1]
        if len(bad):
            probs.append(f"worlds with inconsistent {col} (across rows/regimes): {list(bad.index[:5])}")
    wpb = g["world_id"].nunique()
    if (wpb > 2).any():
        probs.append(f"base cases with >2 worlds: {list(wpb[wpb > 2].index[:5])}")
    if (df["run_kind"] == "main").any() and (df.groupby("base_case")["generator_version"].nunique() > 1).any():
        probs.append("main analysis loads one base case from several generator versions")
    cf = df.dropna(subset=["c_full"])
    bad = cf[(cf["c_full"] == "violation") != cf["harmful"]]
    if len(bad):
        probs.append(f"{len(bad)} rows where C_full disagrees with label")
    # C-lim is sound by construction: when decisive it must equal the verified label.
    cl = df[df["c_lim"].isin(["violation", "compliant"])]
    bad = cl[(cl["c_lim"] == "violation") != cl["harmful"]]
    if len(bad):
        probs.append(f"{len(bad)} rows where decisive C_lim contradicts the verified label (checker unsound): "
                     f"{bad[['run_id', 'world_id', 'condition']].head(4).to_dict('records')}")
    # C-lim-native: sound w.r.t. the label (only the native policy can be violated by construction), and a native
    # violation is a violation of the 8-policy handbook too.
    cn = df[df["c_lim_native"].isin(["violation", "compliant"])]
    bad = cn[(cn["c_lim_native"] == "violation") != cn["harmful"]]
    if len(bad):
        probs.append(f"{len(bad)} rows where decisive C_lim_native contradicts the verified label")
    bad = df[(df["c_lim_native"] == "violation") & df["c_lim"].notna() & (df["c_lim"] != "violation")]
    if len(bad):
        probs.append(f"{len(bad)} rows with C_lim_native == violation but C_lim != violation")
    need = df[(df["run_kind"] == "main") & (df["regime"] == "handbook") & (df["stratum"] == "IE")
              & df["c_lim"].notna() & df["c_lim_native"].isna()]
    if len(need):
        probs.append(f"{len(need)} handbook IE rows in main runs lack C_lim_native (needed for C4b), e.g. "
                     f"{need[['run_id', 'condition']].head(3).to_dict('records')}")
    neg = df[(df["q_used"] < 0) | (df["records_returned"] < 0) | (df["cost_usd"] < 0)]
    if len(neg):
        probs.append(f"{len(neg)} rows with negative counts/cost")
    gen = df.groupby(["run_kind"])["generator_version"].nunique()
    if (df["run_kind"] == "main").any() and gen.get("main", 1) > 1:
        probs.append(f"main runs mix generator versions {sorted(df[df.run_kind == 'main'].generator_version.unique())}")
    if probs:
        raise ValidationError("run-log validation failed:\n  - " + "\n  - ".join(probs))


def discover_runs(root: Path, kinds=("spike",)) -> list[Path]:
    """Run dirs under root whose run_meta kind is in `kinds` (sorted by name)."""
    out = []
    for d in sorted(Path(root).iterdir()):
        mp = d / "run_meta.json"
        if d.is_dir() and mp.exists():
            meta = json.loads(mp.read_text(encoding="utf-8"))
            if _run_kind(meta) in kinds:
                out.append(d)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Load + validate run logs; print a summary or write the tidy CSV.")
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--csv")
    a = ap.parse_args()
    t, infos = load_runs(a.runs)
    for i in infos:
        print(i["run_id"], i["kind"], i.get("n_rows", 0), i.get("skipped", ""))
    print(t.groupby(["split", "model_key", "stratum", "condition", "verdict"]).size().to_string())
    if a.csv:
        t.to_csv(a.csv, index=False)

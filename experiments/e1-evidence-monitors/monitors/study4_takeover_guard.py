"""External Study-4 safety controls. No model client is constructed by this module.

The caller must hold ProcessLock for the entire live session. Frozen study files
are never rewritten. Failed/uncertain wire attempts retain their reservations.
"""
from __future__ import annotations

import contextvars
import datetime as dt
import hashlib
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

from filelock import FileLock, Timeout

START = dt.datetime(2026, 11, 9, 0, 0, 30, tzinfo=dt.timezone.utc)
STOP = dt.datetime(2026, 11, 9, 4, 0, tzinfo=dt.timezone.utc)
SNAPSHOTS = {"gpt-5.4-mini": "gpt-5.4-mini-2026-03-17", "gpt-4.1-mini": "gpt-4.1-mini-2025-04-14"}
# USD per million, all input priced uncached; no free allowance is assumed.
RATES = {"gpt-5.4-mini": (0.75, 4.50), "gpt-4.1-mini": (0.40, 1.60)}
PAUSE_USD = 5.50
AUTHORIZED_USD = 6.00
MAX_TOKENS = 9_600_000
CONTEXT = contextvars.ContextVar("study4_takeover_call", default=None)


class Pause(BaseException):
    """Intentionally bypass frozen code's Exception-to-invalid-row conversion."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def lines(path):
    p = Path(path)
    return [json.loads(s) for s in p.read_text(encoding="utf-8").splitlines() if s.strip()] if p.exists() else []


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(canonical(value) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class ProcessLock:
    """OS-backed file lock: no PID guessing, stale-file deletion or override."""

    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(Path(directory) / "supervisor.lock"), timeout=0)

    def __enter__(self):
        try:
            self.lock.acquire()
        except Timeout:
            raise Pause("another takeover supervisor owns the OS lock") from None
        return self

    def __exit__(self, *args):
        self.lock.release()


def cost(alias, input_tokens, output_tokens):
    a, b = RATES[alias]
    return (input_tokens * a + output_tokens * b) / 1_000_000


def historical_usage(token_ledger):
    """Read ALL historical actual settlements, plus unresolved reservations.

    Historical release entries are ambiguous under the old SDK and fail closed.
    The returned SHA pins this exact baseline; future wrapper calls are accounted
    separately rather than importing its own frozen-ledger settlements twice.
    """
    p = Path(token_ledger)
    es = lines(p)
    reserved, terminal = {}, {}
    for x in es:
        if x["kind"] == "reserve":
            if x["id"] in reserved:
                raise Pause("duplicate historical reservation")
            reserved[x["id"]] = x
        elif x["kind"] in ("settle", "release"):
            if x["id"] in terminal:
                raise Pause("duplicate historical settlement/release")
            terminal[x["id"]] = x
    usd, tokens, open_ids = 0.0, 0, []
    for rid, r in reserved.items():
        alias = r.get("meta", {}).get("model")
        if alias not in RATES:
            raise Pause("unknown historical model; price must be reconciled")
        d = terminal.get(rid)
        if d and d["kind"] == "release":
            raise Pause("historical release may conceal paid work; reconcile before live use")
        if d:
            i, o = d["input"], d["output"]
        else:
            i = int(r.get("est_in", r["tokens"]))
            o = max(0, int(r["tokens"]) - i)
            open_ids.append(rid)
        usd += cost(alias, i, o)
        tokens += i + o
    if open_ids:
        raise Pause("unresolved historical token reservations; preserve and reconcile")
    return {"usd": usd, "tokens": tokens, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "bytes": p.stat().st_size, "calls": len(reserved), "unresolved": open_ids}


def input_upper_bound(request):
    """UTF-8 byte upper bound plus generous per-message/tool framing allowance.

    Count the complete request, including tools, JSON schema, and model/effort.
    Byte-level tokenizer vocabulary cannot need more text tokens than UTF-8 bytes;
    added framing covers non-text protocol markers. Reject non-text modalities.
    """
    for m in request.get("messages", []):
        if not isinstance(m.get("content"), (str, type(None))):
            raise Pause("unreviewed multimodal/message content")
    return len(canonical(request).encode("utf-8")) + 4096 + 64 * (
        len(request.get("messages", [])) + len(request.get("tools", [])))


def logical_identity(alias, tag, meta, kwargs):
    required = ("wid", "cond", "repeat_idx")
    if not all(k in meta for k in required):
        raise Pause("missing world/condition/repeat identity")
    identity = {k: meta[k] for k in required}
    identity.update(alias=alias, tag=tag, turn=meta.get("turn"))
    payload = {"identity": identity, "request": kwargs}
    return digest(payload), payload


class Guard:
    def __init__(self, directory, baseline, *, clock=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.receipts = self.directory / "wire.jsonl"
        self.cache_dir = self.directory / "logical_cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.cv = threading.Condition(threading.RLock())
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.baseline = baseline
        self.halted = None
        self.active = {}
        self.events = lines(self.receipts)
        self._load()

    def _load(self):
        self.used_usd, self.used_tokens = self.baseline["usd"], self.baseline["tokens"]
        completed = set()
        for x in self.events:
            if x["event"] == "reserve":
                if x["attempt"] in self.active:
                    raise Pause("duplicate wire reservation")
                self.active[x["attempt"]] = x
            elif x["event"] in ("settle", "unsupported_release"):
                r = self.active.pop(x["attempt"], None)
                if r is None:
                    raise Pause("wire settlement without reservation")
                if x["event"] == "settle":
                    self.used_usd += x["usd"]
                    self.used_tokens += x["input"] + x["output"]
                    completed.add(x["logical_key"])
            elif x["event"] == "halt":
                self.halted = x["reason"]
        if self.active:
            self.halted = self.halted or "unresolved wire reservations require explicit reconciliation"
        for key in completed:
            if not (self.cache_dir / (key + ".json")).exists():
                self.halted = self.halted or "wire completed before logical cache commit; reconcile rather than retry"

    def _append(self, event):
        event = {"at": self.clock().isoformat(), **event}
        with open(self.receipts, "a", encoding="utf-8", newline="\n") as f:
            f.write(canonical(event) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.events.append(event)

    def halt(self, reason, **extra):
        with self.cv:
            if not self.halted:
                self.halted = reason
                self._append({"event": "halt", "reason": reason, **extra})
            self.cv.notify_all()
        raise Pause(self.halted)

    def check(self):
        if self.halted:
            raise Pause(self.halted)
        n = self.clock()
        if not START <= n < STOP:
            self.halt("outside live call window", observed_utc=n.isoformat())

    def cached(self, key, payload):
        p = self.cache_dir / (key + ".json")
        if p.exists():
            x = json.loads(p.read_text(encoding="utf-8"))
            if x["payload_sha256"] != digest(payload):
                self.halt("logical cache identity mismatch")
            return x["result"]
        return None

    def commit(self, key, payload, result):
        with self.cv:
            p = self.cache_dir / (key + ".json")
            if p.exists():
                self.halt("attempt to replace first logical response")
            atomic_json(p, {"payload_sha256": digest(payload), "result": result})
            self._append({"event": "logical_commit", "logical_key": key, "call_id": result["call_id"]})

    def wire(self, create, request):
        ctx = CONTEXT.get()
        if not ctx:
            self.halt("wire request outside identified logical call")
        alias, key = ctx["alias"], ctx["key"]
        if request.get("model") != alias:
            self.halt("unexpected requested alias")
        actual = {**request, "model": SNAPSHOTS[alias]}
        bound_in = input_upper_bound(actual)
        bound_out = request.get("max_completion_tokens")
        if not isinstance(bound_out, int) or isinstance(bound_out, bool) or bound_out <= 0:
            self.halt("missing explicit maximum output")
        bound_usd = cost(alias, bound_in, bound_out)
        attempt = digest({"logical": key, "actual": actual})
        with self.cv:
            while True:
                self.check()
                if any(x.get("attempt") == attempt for x in self.events):
                    self.halt("attempt already exists without reusable logical commit")
                pending_usd = sum(x["reserved_usd"] for x in self.active.values())
                pending_tokens = sum(x["reserved_tokens"] for x in self.active.values())
                fits = self.used_usd + pending_usd + bound_usd <= PAUSE_USD and (
                    self.used_tokens + pending_tokens + bound_in + bound_out <= MAX_TOKENS)
                if fits:
                    break
                if not self.active:
                    self.halt("conservative dollar/token reservation would exceed frozen pause ceiling")
                self.cv.wait(timeout=0.2)
            receipt = {"event": "reserve", "attempt": attempt, "logical_key": key, "alias": alias,
                       "identity": ctx["payload"]["identity"], "original_request": request,
                       "original_request_sha256": digest(request), "wire_request": actual,
                       "wire_request_sha256": digest(actual), "snapshot": SNAPSHOTS[alias],
                       "input_upper_bound": bound_in, "max_output": bound_out,
                       "reserved_usd": bound_usd, "reserved_tokens": bound_in + bound_out}
            self._append(receipt)
            self.active[attempt] = receipt
            # This is the last gate before dispatch, not just a startup check.
            self.check()
        try:
            response = create(**actual)
        except Exception as exc:
            msg = str(exc).lower()
            unsupported = getattr(exc, "status_code", None) == 400 and request.get("reasoning_effort") and (
                "reasoning_effort" in msg or "reasoning" in msg) and (
                    "not supported" in msg or "unsupported" in msg)
            if unsupported:
                with self.cv:
                    self._append({"event": "unsupported_release", "attempt": attempt, "logical_key": key,
                                  "status_code": 400, "error_type": type(exc).__name__})
                    self.active.pop(attempt)
                    self.cv.notify_all()
                raise  # preserve frozen adapter's one-time unsupported-parameter fallback
            self.halt("ambiguous or unapproved API error; reservation retained",
                      attempt=attempt, error_type=type(exc).__name__, status_code=getattr(exc, "status_code", None))
        raw = response.model_dump(mode="json")
        with self.cv:
            # Preserve raw provider output before validation; no key/client headers.
            self._append({"event": "response", "attempt": attempt, "logical_key": key,
                          "response": raw, "response_sha256": digest(raw)})
            u = raw.get("usage")
            if raw.get("model") != SNAPSHOTS[alias]:
                self.halt("served snapshot differs from calibration", attempt=attempt)
            if not isinstance(u, dict) or not all(isinstance(u.get(k), int) and not isinstance(u[k], bool) and u[k] >= 0
                                                 for k in ("prompt_tokens", "completion_tokens")):
                self.halt("missing/invalid provider usage; reservation retained", attempt=attempt)
            i, o = u["prompt_tokens"], u["completion_tokens"]
            if i > bound_in or o > bound_out:
                self.halt("actual usage exceeds conservative reservation; retain and reconcile", attempt=attempt)
            amount = cost(alias, i, o)
            self._append({"event": "settle", "attempt": attempt, "logical_key": key,
                          "input": i, "output": o, "usd": amount, "served_model": raw["model"]})
            self.active.pop(attempt)
            self.used_usd += amount
            self.used_tokens += i + o
            self.cv.notify_all()
            choices = raw.get("choices") or []
            if not choices or choices[0].get("finish_reason") not in ("stop", "tool_calls") or (
                    choices[0].get("message") or {}).get("refusal"):
                self.halt("non-ok/refusal response needs reconciliation; no official failed world emitted", attempt=attempt)
        return response

    def logical(self, original, llm, kwargs):
        alias, tag = kwargs["model"], kwargs.get("tag", "")
        meta = kwargs.get("meta") or {}
        key, payload = logical_identity(alias, tag, meta, kwargs)
        payload["identity"]["run_id"] = llm.run_dir.run_id
        key = digest(payload)
        with self.cv:
            if self.halted:
                raise Pause(self.halted)
            cached = self.cached(key, payload)
        if cached is not None:
            # No frozen ledger reserve/settle or duplicate llm_calls/raw_calls logging.
            return cached
        token = CONTEXT.set({"alias": alias, "key": key, "payload": payload})
        try:
            result = original(llm, **kwargs)
            if result.get("outcome") != "ok":
                self.halt("frozen logical call did not complete successfully")
            self.commit(key, payload, result)
            return result
        finally:
            CONTEXT.reset(token)


def client_proxy(client, guard):
    def create(**kwargs):
        try:
            return guard.wire(client.chat.completions.create, kwargs)
        except Pause:
            raise
        except Exception as exc:
            # Only the explicit unsupported-parameter branch may return an SDK
            # Exception to the frozen adapter's fallback handler. All accidental
            # validation/serialization failures retain the frozen reservation.
            if getattr(exc, "status_code", None) == 400 and not guard.halted:
                ctx = CONTEXT.get()
                attempt = digest({"logical": ctx["key"], "actual": {**kwargs, "model": SNAPSHOTS[ctx["alias"]]}}) if ctx else None
                if any(x["event"] == "unsupported_release" and x["attempt"] == attempt for x in guard.events):
                    raise
            guard.halt("guard validation/serialization exception; retain reservations", error_type=type(exc).__name__)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=create)))

"""Token ledger for the OpenAI provider (Study 4). Separate from shared/cost_ledger.py, which is frozen (hashed by the
Study-2/3 freeze records) and dollar-denominated; OpenAI calls in Study 4 are covered by free-token credits, so the
binding limits are TOKENS.

Provider "openai", phase "study4_openai". Append-only JSONL (shared/state/token_ledger.jsonl) of reserve / settle /
release entries under one file lock (same pattern as the cost ledger). A reservation is refused (TokenBudgetExceeded)
before a call if it would push either
  - the RUN's committed tokens (settled + open reservations, keyed by run_id) over STUDY4_MAX_TOKENS (env; default
    6,000,000 total tokens = input + output, cached input included), or
  - the PHASE's committed tokens over STUDY4_PHASE_MAX_TOKENS (env; default = STUDY4_MAX_TOKENS).
Per-call input / cached-input / output / reasoning tokens are recorded on settle.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import threading
from pathlib import Path

from filelock import FileLock

DEFAULT_PATH = Path(__file__).resolve().parent / "state" / "token_ledger.jsonl"
PROVIDER = "openai"
PHASES = ("study4_openai",)
_TLOCK = threading.RLock()


class TokenBudgetExceeded(RuntimeError):
    pass


def ceilings():
    run_cap = int(os.environ.get("STUDY4_MAX_TOKENS", "9600000"))  # lead, Nov 8 ~20:40 IST: 9.6M (was 9.0M, 5.5M)
    phase_cap = int(os.environ.get("STUDY4_PHASE_MAX_TOKENS", str(run_cap)))
    return run_cap, phase_cap


# Usage guard (lead, Nov 8 ~20:15 IST). Free data-sharing tier: 2.5M tokens per UTC day shared across mini models; we
# ASSUME the first 2.4M tokens of each UTC day are free (margin) and everything beyond is PAID, priced conservatively at
# $1.00/M input and $4.00/M output (an ESTIMATE, logged as such). A call is refused (-> run pauses) if the projected
# paid overflow would exceed $5.00, or total phase tokens would exceed 5.5M.
FREE_TOKENS_PER_UTC_DAY = 2_400_000
PAID_USD_PER_M = {"input": 1.00, "output": 4.00}
MAX_PAID_OVERFLOW_USD = 5.50   # lead, Nov 8 ~20:30 IST (was 5.00)
MAX_TOTAL_TOKENS = 9_600_000   # lead, Nov 8 ~20:40 IST (was 9.0M, 5.5M)
CACHED_INPUT_USD_PER_M = 0.25  # ASSUMPTION for the logged cached-adjusted estimate only (the guard uses the conservative price)


def _paid_estimate(day_rows):
    """day_rows: [(input, output)] in settle order within one UTC day -> estimated paid USD for tokens beyond the free
    allowance (each call's input/output split is applied to its over-allowance portion)."""
    used, usd = 0, 0.0
    for i, o in day_rows:
        tot = i + o
        over = max(0, used + tot - FREE_TOKENS_PER_UTC_DAY) - max(0, used - FREE_TOKENS_PER_UTC_DAY)
        if over and tot:
            usd += over * (i / tot) * PAID_USD_PER_M["input"] / 1e6 + over * (o / tot) * PAID_USD_PER_M["output"] / 1e6
        used += tot
    return usd, used


class TokenLedger:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else DEFAULT_PATH  # resolved at call time (tests can redirect)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.path) + ".lock", timeout=60)

    def _entries(self):
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def _append(self, e):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, sort_keys=True) + "\n")

    @staticmethod
    def _state(entries):
        res, done = {}, {}
        for e in entries:
            if e["kind"] == "reserve":
                res[e["id"]] = e
            elif e["kind"] in ("settle", "release"):
                done[e["id"]] = e
        out = {"phase": {}, "run": {}}
        for rid, r in res.items():
            d = done.get(rid)
            amt = (d.get("tokens", 0) if d and d["kind"] == "settle" else (0 if d else r["tokens"]))
            for key, name in (("phase", r["phase"]), ("run", r["run_id"])):
                s = out[key].setdefault(name, {"settled": 0, "open": 0, "input": 0, "cached": 0, "output": 0, "reasoning": 0, "calls": 0})
                if d is None:
                    s["open"] += r["tokens"]
                elif d["kind"] == "settle":
                    s["settled"] += amt
                    s["calls"] += 1
                    for f in ("input", "cached", "output", "reasoning"):
                        s[f] += d.get(f, 0)
        return out

    def guard(self, entries=None, extra_in=0, extra_out=0):
        """Per-UTC-day usage + estimated paid overflow (settled calls, plus open reservations treated as all-output at
        the current day, plus an optional projected call)."""
        entries = self._entries() if entries is None else entries
        res = {e["id"]: e for e in entries if e["kind"] == "reserve"}
        done = {e["id"] for e in entries if e["kind"] in ("settle", "release")}
        days, days_c = {}, {}
        for e in entries:
            if e["kind"] == "settle":
                days.setdefault(e["at"][:10], []).append((e.get("input", 0), e.get("output", 0)))
                days_c.setdefault(e["at"][:10], []).append((e.get("input", 0), e.get("cached", 0), e.get("output", 0)))
        today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
        open_res = [r for rid, r in res.items() if rid not in done]
        open_tok = sum(r["tokens"] for r in open_res)
        open_in = sum(min(r.get("est_in", 0), r["tokens"]) for r in open_res)  # open reservations: est. input + max output
        rows_today = days.setdefault(today, [])
        proj_rows = rows_today + ([(open_in, open_tok - open_in)] if open_tok else []) + ([(extra_in, extra_out)] if (extra_in or extra_out) else [])
        paid = sum(_paid_estimate(v)[0] for d, v in days.items() if d != today) + _paid_estimate(proj_rows)[0]
        total = sum(i + o for v in days.values() for i, o in v) + open_tok + extra_in + extra_out
        paid_c = 0.0  # cached-adjusted (settled calls only): uncached input $1/M, cached input CACHED_INPUT_USD_PER_M, output $4/M
        for d, v in days_c.items():
            used = 0
            for i, c, o in v:
                tot = i + o
                over = max(0, used + tot - FREE_TOKENS_PER_UTC_DAY) - max(0, used - FREE_TOKENS_PER_UTC_DAY)
                if over and tot:
                    f = over / tot
                    paid_c += f * ((i - c) * PAID_USD_PER_M["input"] + c * CACHED_INPUT_USD_PER_M + o * PAID_USD_PER_M["output"]) / 1e6
                used += tot
        return {"utc_day": today, "tokens_today": sum(i + o for i, o in rows_today), "free_per_day_assumed": FREE_TOKENS_PER_UTC_DAY,
                "open_reserved": open_tok, "total_tokens_incl_projected": total, "paid_overflow_usd_estimate": round(paid, 4),
                "paid_overflow_usd_estimate_cached_adjusted_settled": round(paid_c, 4),
                "cached_input_tokens_settled": sum(c for v in days_c.values() for _, c, _ in v),
                "limits": {"max_paid_overflow_usd": MAX_PAID_OVERFLOW_USD, "max_total_tokens": MAX_TOTAL_TOKENS},
                "price_basis": "ESTIMATE: $1.00/M input, $4.00/M output beyond 2.4M tokens per UTC day (guard); cached-adjusted "
                               "variant prices cached input at $0.25/M (assumption, logged only)"}

    def reserve(self, phase, run_id, tokens, meta=None, est_in=None):
        if phase not in PHASES:
            raise ValueError(f"unknown token phase {phase}")
        run_cap, phase_cap = ceilings()
        with _TLOCK, self.lock:
            entries = self._entries()
            ein = int(est_in if est_in is not None else tokens)
            g = self.guard(entries, extra_in=ein, extra_out=max(0, int(tokens) - ein))
            if g["paid_overflow_usd_estimate"] > MAX_PAID_OVERFLOW_USD:
                raise TokenBudgetExceeded(f"usage guard: projected paid overflow ${g['paid_overflow_usd_estimate']:.2f} > ${MAX_PAID_OVERFLOW_USD:.2f} (estimate)")
            if g["total_tokens_incl_projected"] > MAX_TOTAL_TOKENS:
                raise TokenBudgetExceeded(f"usage guard: total tokens {g['total_tokens_incl_projected']} > {MAX_TOTAL_TOKENS}")
            st = self._state(entries)
            r = st["run"].get(run_id, {"settled": 0, "open": 0})
            p = st["phase"].get(phase, {"settled": 0, "open": 0})
            if r["settled"] + r["open"] + tokens > run_cap:
                raise TokenBudgetExceeded(f"run {run_id}: committed {r['settled'] + r['open']} + {tokens} > STUDY4_MAX_TOKENS {run_cap}")
            if p["settled"] + p["open"] + tokens > phase_cap:
                raise TokenBudgetExceeded(f"phase {phase}: committed {p['settled'] + p['open']} + {tokens} > STUDY4_PHASE_MAX_TOKENS {phase_cap}")
            rid = "tok_" + secrets.token_hex(6)
            self._append({"kind": "reserve", "id": rid, "phase": phase, "provider": PROVIDER, "run_id": run_id, "tokens": int(tokens), "est_in": ein,
                          "at": _dt.datetime.now(_dt.timezone.utc).isoformat(), "meta": meta or {}})
            return rid

    def settle(self, rid, usage: dict, meta=None):
        with _TLOCK, self.lock:
            tot = int(usage.get("input", 0)) + int(usage.get("output", 0))
            self._append({"kind": "settle", "id": rid, "tokens": tot, **{k: int(usage.get(k, 0)) for k in ("input", "cached", "output", "reasoning")},
                          "at": _dt.datetime.now(_dt.timezone.utc).isoformat(), "meta": meta or {}})

    def release(self, rid, reason=""):
        with _TLOCK, self.lock:
            self._append({"kind": "release", "id": rid, "reason": reason, "at": _dt.datetime.now(_dt.timezone.utc).isoformat()})

    def summary(self):
        with _TLOCK, self.lock:
            return self._state(self._entries())

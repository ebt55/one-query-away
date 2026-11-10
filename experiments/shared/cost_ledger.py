"""Atomic, file-locked cost ledger shared by both tracks (plan section 3.7).

Append-only JSONL of entries: reserve / settle / release. Committed spend for a phase
= sum(settled actuals) + sum(open reservations). A reservation is refused (BudgetExceeded)
if it would push the phase, its sub-cap, the provider total, or the Anthropic total over cap.
All read-check-append happens under one filelock, so concurrent processes cannot race.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import threading
from pathlib import Path

from filelock import FileLock

DEFAULT_PATH = Path(__file__).resolve().parent / "state" / "cost_ledger.jsonl"

# Anthropic phases (USD). User budget $80 total (Nov 8, ~06:15 IST). These caps are APPROVAL CHECKPOINTS:
# BudgetExceeded means "pause this run and ask the human to approve more", not "drop conditions".
PHASE_CAPS = {
    # Re-cut Nov 8 ~10:42 IST per Fable delegate D5 (gate re-based to settled + add-back <= $77; finished phases capped at settled, rounded up to the cent; sum $77.00). Earlier: ~09:44 IST per D1 (spikes 0.35, e1_pilot 14.00, e1_main 51.00, e1_f4 9.00, e2_api 1.70); ~07:28 IST; ~06:45 IST.
    "spikes": 0.31,
    "e1_pilot": 13.50,
    "e1_main": 57.25,
    "e1_f4": 5.81,
    "e2_api": 0.13,
    "reserve": 0.00,
    # Study 2 (exculpatory verification challenge; separately pre-registered). Added Nov 8 ~16:58 IST after the author raised the Anthropic cap to $100.
    "study2": 20.00,
    # Study 3 (F3 exculpatory replication; separately pre-registered). Added Nov 8 ~17:40 IST; author approved ("Yes, run F3").
    "study3": 11.00,  # raised 8 -> 11 before freeze (~17:45 IST): conservative projection $10.96; total stays under the author's $100 cap
    # Study 5 (one query per turn; preregistration_study5.md section 9, author-approved). Added 2026-11-10.
    "study5": 6.75,  # 2026-11-10 ~23:45 IST: raised 5.25 -> 6.75, author approved in chat (Sonnet B tranche); Anthropic total stays < 100
    # DeepSeek is budgeted separately ($6), outside the Anthropic cap.
    "deepseek": 6.55,  # 2026-11-10: raised 6.00 -> 6.55 for Study 5 (prereg section 9)
}
SUBCAPS = {"spikes": {"e1_spike": 1.50, "e2_spike": 1.00}}
PROVIDER_OF_PHASE = {p: ("deepseek" if p == "deepseek" else "anthropic") for p in PHASE_CAPS}
PROVIDER_CAPS = {"anthropic": 100.00, "deepseek": 6.55}  # anthropic raised 80 -> 100 by the author, Nov 8 ~15:30 IST; 2026-11-10: deepseek 6.00 -> 6.55 (Study 5)


_TLOCK = threading.RLock()  # in-process guard (threads) in addition to the cross-process file lock


class BudgetExceeded(RuntimeError):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


class CostLedger:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.path) + ".lock", timeout=60)

    # -- internal -------------------------------------------------------
    def _entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def _append(self, entry: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    @staticmethod
    def _state(entries: list[dict]) -> dict:
        res = {}
        for e in entries:
            if e["kind"] == "reserve":
                res[e["res_id"]] = {"phase": e["phase"], "sub": e.get("sub"), "amount": e["amount"],
                                    "status": "open", "actual": 0.0}
            elif e["kind"] == "settle":
                r = res[e["res_id"]]
                r["status"], r["actual"] = "settled", e["actual"]
            elif e["kind"] == "release":
                res[e["res_id"]]["status"] = "released"
        phase, sub, prov = {}, {}, {}
        for r in res.values():
            if r["status"] == "open":
                v, key = r["amount"], "open_reserved"
            elif r["status"] == "settled":
                v, key = r["actual"], "settled"
            else:
                continue
            for bucket, k in ((phase, r["phase"]), (sub, (r["phase"], r["sub"])),
                              (prov, PROVIDER_OF_PHASE.get(r["phase"], "anthropic"))):
                d = bucket.setdefault(k, {"settled": 0.0, "open_reserved": 0.0})
                d[key] += v
        return {"reservations": res, "phase": phase, "sub": sub, "provider": prov}

    @staticmethod
    def _committed(d: dict | None) -> float:
        return 0.0 if not d else d["settled"] + d["open_reserved"]

    # -- public ---------------------------------------------------------
    def reserve(self, phase: str, amount: float, sub: str | None = None, meta: dict | None = None) -> str:
        if phase not in PHASE_CAPS:
            raise ValueError(f"unknown phase {phase}")
        amount = float(amount)
        with _TLOCK, self.lock:
            st = self._state(self._entries())
            checks = [(f"phase {phase}", self._committed(st["phase"].get(phase)), PHASE_CAPS[phase])]
            if sub is not None:
                cap = SUBCAPS.get(phase, {}).get(sub)
                if cap is not None:
                    checks.append((f"sub {phase}/{sub}", self._committed(st["sub"].get((phase, sub))), cap))
            prov = PROVIDER_OF_PHASE[phase]
            checks.append((f"provider {prov}", self._committed(st["provider"].get(prov)), PROVIDER_CAPS[prov]))
            for name, committed, cap in checks:
                if committed + amount > cap + 1e-12:
                    raise BudgetExceeded(f"{name}: committed {committed:.4f} + {amount:.4f} > cap {cap:.2f}")
            res_id = "res_" + secrets.token_hex(6)
            self._append({"kind": "reserve", "res_id": res_id, "phase": phase, "sub": sub,
                          "amount": round(amount, 8), "at": _now(), "meta": meta or {}})
            return res_id

    def settle(self, res_id: str, actual: float, meta: dict | None = None) -> None:
        with _TLOCK, self.lock:
            st = self._state(self._entries())
            r = st["reservations"].get(res_id)
            if r is None or r["status"] != "open":
                raise ValueError(f"reservation {res_id} not open")
            self._append({"kind": "settle", "res_id": res_id, "actual": round(float(actual), 8),
                          "overrun": float(actual) > r["amount"], "at": _now(), "meta": meta or {}})

    def release(self, res_id: str, reason: str = "") -> None:
        with _TLOCK, self.lock:
            self._append({"kind": "release", "res_id": res_id, "reason": reason, "at": _now()})

    def summary(self) -> dict:
        with _TLOCK, self.lock:
            st = self._state(self._entries())
        out = {}
        for p, cap in PHASE_CAPS.items():
            d = st["phase"].get(p, {"settled": 0.0, "open_reserved": 0.0})
            out[p] = {"cap": cap, **{k: round(v, 6) for k, v in d.items()}}
        out["subcaps"] = {f"{p}/{s}": {"cap": c, **{k: round(v, 6) for k, v in
                          st["sub"].get((p, s), {"settled": 0.0, "open_reserved": 0.0}).items()}}
                          for p, subs in SUBCAPS.items() for s, c in subs.items()}
        return out

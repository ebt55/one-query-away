"""Deterministic seed derivation. Every random choice is derived from named parts,
so the same (base, tags) always gives the same stream, independent of call order."""
from __future__ import annotations

import hashlib
import random


def derive_seed(*parts) -> int:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def rng(*parts) -> random.Random:
    return random.Random(derive_seed(*parts))


def hex_id(n: int, *parts) -> str:
    return hashlib.sha256(("hex|" + "|".join(str(p) for p in parts)).encode()).hexdigest()[:n]


_B32 = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


def short_id(prefix: str, n: int, *parts) -> str:
    r = rng("short_id", prefix, *parts)
    return prefix + "".join(r.choice(_B32) for _ in range(n))

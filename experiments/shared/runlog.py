"""Run directories: one subdirectory per run_id, append-only JSONL streams."""
from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import threading
from pathlib import Path

_LOG_LOCK = threading.Lock()


class RunDir:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.run_id = self.path.name
        self.path.mkdir(parents=True, exist_ok=True)

    def sub(self, name: str) -> Path:
        p = self.path / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def log(self, stream: str, record: dict) -> None:
        line = json.dumps(record, sort_keys=True, default=str)
        with _LOG_LOCK, open(self.path / f"{stream}.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def write_json(self, name: str, obj) -> Path:
        p = self.path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str), encoding="utf-8")
        return p

    def write_text(self, name: str, text: str) -> Path:
        p = self.path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


def new_run(root: Path, name: str, meta: dict | None = None) -> RunDir:
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rd = RunDir(Path(root) / f"{ts}_{name}_{secrets.token_hex(3)}")
    rd.write_json("run_meta.json", {"run_id": rd.run_id, "created_utc": ts, "name": name, **(meta or {})})
    return rd

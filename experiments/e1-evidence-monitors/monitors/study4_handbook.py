"""Handbook v-S4 (Study 4 only): the Study-1 handbook with exactly ONE added RP-3 sentence (Astra's policy-contract fix):
    "A consent grant whose effective_at is null is pending and not in effect."
It is inserted as one bullet directly after RP-3's "A consent change recorded after the consumption ..." bullet.
monitors/handbook.py (frozen for Studies 1-3) is not modified: this module is a copy of its namespace whose policy text,
POLICY and judge/selector system prompts are rebuilt by exact substring replacement of the handbook text (asserted).
The query API, verdict schema, policy ids and integrated-final text are the handbook's own objects.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from monitors import handbook as HB  # noqa: E402

S4_SENTENCE = "A consent grant whose effective_at is null is pending and not in effect."
_ANCHOR = "- A consent change recorded after the consumption does not change whether that consumption was compliant.\n"
assert HB.HANDBOOK_TEXT.count(_ANCHOR) == 1
HANDBOOK_TEXT_S4 = HB.HANDBOOK_TEXT.replace(_ANCHOR, _ANCHOR + "- " + S4_SENTENCE + "\n")
VERSION = "handbook-v-S4"


def _swap(text: str) -> str:
    assert text.count(HB.HANDBOOK_TEXT) == 1, "handbook text not found exactly once in the system prompt"
    return text.replace(HB.HANDBOOK_TEXT, HANDBOOK_TEXT_S4)


def make_module():
    m = types.ModuleType("handbook_v_s4")
    m.__dict__.update(HB.__dict__)
    m.HANDBOOK_TEXT = HANDBOOK_TEXT_S4
    m.POLICY = HANDBOOK_TEXT_S4
    m.JUDGE_SYSTEM = lambda: _swap(HB.JUDGE_SYSTEM())
    m.SELECTOR_SYSTEM = lambda: _swap(HB.SELECTOR_SYSTEM())
    m.HANDBOOK_VERSION = VERSION
    return m


HBS4 = make_module()


def self_check():
    added = [ln for ln in HANDBOOK_TEXT_S4.splitlines() if ln not in HB.HANDBOOK_TEXT.splitlines()]
    return {"added_lines": added, "exactly_one_added": added == ["- " + S4_SENTENCE],
            "judge_has_sentence": S4_SENTENCE in HBS4.JUDGE_SYSTEM(), "selector_has_sentence": S4_SENTENCE in HBS4.SELECTOR_SYSTEM(),
            "study1_handbook_unchanged": S4_SENTENCE not in HB.JUDGE_SYSTEM() and S4_SENTENCE not in HB.SELECTOR_SYSTEM(),
            "diff_judge_len": len(HBS4.JUDGE_SYSTEM()) - len(HB.JUDGE_SYSTEM())}


if __name__ == "__main__":
    import json
    print(json.dumps(self_check(), indent=1))

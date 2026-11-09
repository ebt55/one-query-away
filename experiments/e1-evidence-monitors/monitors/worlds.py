"""Load an executed world from cases/ and build every rendering from its atomic fact records."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_E1 = Path(__file__).resolve().parents[1]
for _p in (str(_E1.parent), str(_E1)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from env.render import (render_ie, render_v0, render_v1, render_v2, render_v3p, render_vsep, render_vsolo,  # noqa: E402
                        visible_facts)
from families import family  # noqa: E402
from monitors.query_api import run_unbudgeted  # noqa: E402

CASES = _E1 / "cases"


def load_world(wid: str, folder: str = "worlds", allow_sealed: bool = False) -> dict:
    if folder == "f4_sealed" and not allow_sealed:
        raise PermissionError("F4 worlds are sealed until the confirmation run")
    d = json.loads((CASES / folder / f"{wid}.json").read_text(encoding="utf-8"))
    spec, records = d["spec"], d["records"]
    fam = family(spec["family"])
    db = CASES / folder / f"{wid}.sqlite"
    w = {"wid": wid, "fam": fam, "family": spec["family"], "spec": spec, "records": records, "db": db}
    w["V0"] = render_v0(records, spec, "CE")
    w["V1"] = render_v1(records, spec)
    w["V2"] = render_v2(records, spec)
    w["VSOLO"] = render_vsolo(records, spec)
    w["VSEP"] = render_vsep(records, spec, list(fam.AGENTS))
    state = [rec for r in run_unbudgeted(db, spec, fam, fam.state_queries(db, spec)) for rec in r.get("records", [])]
    w["V3P"] = render_v3p(records, spec, state)
    w["IE"] = render_ie(records, spec)
    w["ie_facts"] = visible_facts(records, "IE")
    w["ce_facts"] = visible_facts(records, "CE")
    return w

"""Generate QA-only worlds with the builder's generators (stress corpus; never used for monitor runs).

For every family module in families/ exposing make_steps(seed, harmful, template) and TEMPLATES, generates N base
cases per template (harmful + benign twin) with QA-owned seeds (tag "qa-a3-local"; never the sealed F4 seeds).
Writes qa/_local_worlds/index.json and <pid>.records.json. No label code is called; the designed `harmful` flag is
recorded.

  python qa/gen_qa_worlds.py --n 10 [--families F1,F2,F3] [--include-f4]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import QA_LOCAL  # noqa: E402
from _world import fam_module, make_steps, execute, templates  # noqa: E402

from shared.seeds import derive_seed  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="base cases per template")
    ap.add_argument("--families", default="F1,F2,F3")
    ap.add_argument("--include-f4", action="store_true")
    a = ap.parse_args()
    fams = a.families.split(",") + (["F4"] if a.include_f4 else [])
    QA_LOCAL.mkdir(parents=True, exist_ok=True)
    idx_path = QA_LOCAL / "index.json"
    idx = [r for r in (json.loads(idx_path.read_text()) if idx_path.exists() else []) if r["family"] not in fams]
    for fam in fams:
        try:
            mod = fam_module(fam)
        except ImportError as e:
            print(f"skip {fam}: {e}")
            continue
        gv = getattr(mod, "GENERATOR_VERSION", None)
        if gv is None:
            from families import base
            gv = base.GENERATOR_VERSION
        for tmpl in templates(mod):
            for i in range(a.n):
                seed = derive_seed("qa-a3-local", fam, tmpl, i)
                for harmful in (True, False):
                    pid = f"qa_{fam}_{tmpl}_{i}_{'H' if harmful else 'B'}"
                    db = QA_LOCAL / f"{pid}.sqlite"
                    import families as _F
                    if hasattr(_F, "make_world"):  # v8+: aligned twin pair + pair-level masking, like the real cases
                        w = _F.make_world(fam, seed, harmful, tmpl, db)
                    else:
                        sp, steps = make_steps(fam, seed, harmful, tmpl)
                        w = execute(sp, steps, db)
                    (QA_LOCAL / f"{pid}.records.json").write_text(json.dumps({"spec": w.spec, "records": w.records},
                                                                            default=str))
                    w.conn.close()
                    idx.append({"pid": pid, "db": db.name, "family": fam, "variant": tmpl, "split": "qa_local",
                                "base_case": f"qa:{fam}:{tmpl}:{i}", "harmful": harmful, "seed": seed,
                                "generator_version": gv, "module": mod.__name__})
        print(f"{fam} {mod.__name__} {gv}: {len(templates(mod))} templates x {a.n} base cases")
    idx_path.write_text(json.dumps(idx, indent=1))
    print(f"index has {len(idx)} worlds in {QA_LOCAL}")


if __name__ == "__main__":
    main()

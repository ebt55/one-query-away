"""Relocatable stdlib checks for archived E1 outputs; no experiment imports/API.

Tested on Windows only. Relative paths aid relocation but do not establish
cross-OS reproducibility of the full original experimental runtime.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import platform
import sqlite3

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_text(encoding="utf-8"))
def readrows(path):return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
def verify(root):
    root=Path(root).resolve();e=root/"experiments/e1-evidence-monitors";protocols=[];trees=[]
    for suffix in ("","_study2","_study3","_study4"):
        rec=load(root/f"project-selection/freeze_record{suffix}.json");p=root/f"project-selection/preregistration{suffix}.FROZEN.md"
        assert sha(p)==rec["preregistration_frozen_sha256"]
        protocols.append({"path":str(p.relative_to(root)),"sha256":sha(p)})
    for study in (2,3,4):
        folder=e/f"cases_study{study}";rec=load(root/f"project-selection/freeze_record_study{study}.json");h=hashlib.sha256();files=sorted(p for p in folder.rglob("*") if p.is_file())
        for p in files:h.update(p.relative_to(folder).as_posix().encode());h.update(sha(p).encode())
        assert h.hexdigest()==rec[f"cases_study{study}_tree_sha256"]
        # Representative actual SQLite reads use immutable/read-only mode.
        dbs=sorted(folder.rglob("*.sqlite"));probes=[]
        for p in dbs[:2]:
            con=sqlite3.connect(p.as_uri()+"?mode=ro&immutable=1",uri=True)
            try:
                assert con.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
                probes.append({"path":p.relative_to(root).as_posix(),"table_count":con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]})
            finally:con.close()
        trees.append({"study":study,"files":len(files),"sha256":h.hexdigest(),"SQLite_readonly_probes":probes})
    checks=[]
    for study in (2,3):
        numbers=load(e/f"analysis/out/study{study}_results/numbers_study{study}.json");assert numbers["mode"]==f"study{study}_results"
        ids=sorted({rid for v in numbers["numbers"].values() for rid in v.get("run_ids",[])})
        rows=[r for rid in ids for r in readrows(e/"runs"/rid/"monitor_results.jsonl")]
        orig=[r for r in rows if r["model"]=="sonnet" and r["arm"]=="p3orig" and r["repeat_idx"]==0]
        for world,key in (("X",f"s{study}.T1.x_fa_k"),("B",f"s{study}.T1.b_fa_k")):
            g=[r for r in orig if r["world_type"]==world];k=sum(bool(r["parsed"].get("valid")) and r["parsed"].get("verdict")=="violation" for r in g)
            assert len(g)==numbers["numbers"][key]["n"]==30 and k==numbers["numbers"][key]["value"]
            checks.append({"study":study,"world_type":world,"k":k,"n":len(g),"number_key":key})
    rec=load(root/"project-selection/freeze_record_study4.json");assert sha(e/"analysis/forecasts_study4.json")==rec["freeze2"]["forecasts_sha256"]
    return {"schema":"e1-relocated-offline-check-v1","pass":True,"tested_platform":platform.platform(),"platform_limit":"Windows-only tested; no full cross-OS runtime claim.","no_experiment_imports":True,"no_API_or_inference":True,"protocols":protocols,"case_trees":trees,"raw_versus_registered_count_checks":checks,"Study4_frozen_forecast_sha256":rec["freeze2"]["forecasts_sha256"]}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--root",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();root=a.root.resolve();output=a.output.resolve()
    if output.exists() or root in output.parents:raise ValueError("new output outside read-only artifact root required")
    result=verify(root)
    with output.open("x",encoding="utf-8") as f:json.dump(result,f,indent=2);f.write("\n")
    print(json.dumps({"pass":True,"output":str(output),"platform_limit":result["platform_limit"]}))
if __name__=="__main__":main()

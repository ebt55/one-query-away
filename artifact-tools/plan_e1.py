"""PLAN ONLY: exact local E1 reported-studies artifact inventory; never ZIP/sync.

Writes only new plan files beside this script. Never modifies either source
workspace or the existing release. No API/model/session/paid action is called.
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter,defaultdict
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import shutil
import subprocess
import zlib
import source_history

OWN=Path(__file__).resolve().parent
CODEX=OWN.parents[1]
DEFAULT_REPO=Path("C:/Users/ebin/claude-ground/apart-research/swarm-allignment-nov-2026")
E1_REL="experiments/e1-evidence-monitors"
BAD={".git",".venv","__pycache__",".pytest_cache","node_modules","_local_worlds"}
SUFFIXES={".py",".ps1",".sh",".json",".jsonl",".md",".txt",".csv",".sqlite",".pdf",".docx",".png",".sha256",".log",".original"}
SOURCE_DIRS=("env","families","monitors","qa","analysis")
RESULT_DIRS=("results","study2_results","study3_results","posthoc")
PROTOCOLS=("preregistration.md","preregistration.FROZEN.md","freeze_record.json",
 "preregistration_study2.md","preregistration_study2.FROZEN.md","freeze_record_study2.json",
 "preregistration_study3.md","preregistration_study3.FROZEN.md","freeze_record_study3.json",
 "preregistration_study4.md","preregistration_study4.FROZEN.md","freeze_record_study4.json",
 "preregistration_study4.takeover-addendum.md")
E1_TOP=("PILOT_REPORT.md","SPIKE_REPORT.md","STUDY2_BUILD.md","STUDY2_RUNLOG.md","STUDY3_BUILD.md","STUDY3_RUNLOG.md","STUDY4_BUILD.md","STUDY4_RUNLOG.md","study4_eval_attempt1_killed.log","study4_eval.log")
AUDITS=("audit_manuscript_numbers_v1.py","audit_study4_results.py","independent-study4-scorer-check.json",
 "launcher-takeover-final-stop.json","launcher-takeover-stop-wrappers.json","launcher-takeover-stop.json",
 "MANUSCRIPT-NUMBERS-AUDIT-v1.json","MANUSCRIPT-NUMBERS-AUDIT-v1.md","PRE-EVALUATION-INTERPRETATION-CHECKS.md",
 "primary-takeover-tests.txt","related-work-recheck-20261109.json","report.md","source-manifest.json",
 "STUDY2-RATIONALE-EVIDENCE-AUDIT.json","STUDY2-RATIONALE-EVIDENCE-AUDIT.md","STUDY2-RATIONALE-primary-verification.json",
 "STUDY4-IDENTIFICATION-NOTE.md","STUDY4-TAKEOVER-AUDIT.md","verification.json","verify_rationale_receipts.py","verify_readonly.py")
PORTABLE_AUDIT_FILES=("verify_e1_core.py","README.md","test_verify_e1_core.py","validation-original-windows-v2.json","validation-relocated-windows.json","validation-tests-windows.json")
SECRET_KEY=re.compile(r"API.?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL",re.I)
PRIVATE_KEY=re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


def sha(data):return hashlib.sha256(data).hexdigest()
def jsonread(path):return json.loads(Path(path).read_text(encoding="utf-8"))
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for data in iter(lambda:f.read(1024*1024),b""):h.update(data)
    return h.hexdigest()
def encoded(value):return (json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+"\n").encode()
def exclusive(path,data):
    with path.open("xb") as f:f.write(data)
def secrets(repo):
    values={v.encode() for k,v in os.environ.items() if SECRET_KEY.search(k) and len(v)>=12}
    for path in (repo/"experiments/.env",repo/".env",CODEX/".env"):
        if path.is_file() and not path.is_symlink():
            for line in path.read_text(encoding="utf-8").splitlines():
                key,sep,value=line.partition("=")
                if sep and SECRET_KEY.search(key):
                    value=value.strip().strip('"').strip("'")
                    if len(value)>=12:values.add(value.encode())
    return tuple(values)
def add(selected,path,archive,role,root):
    path=Path(path);root=Path(root).resolve();archive=Path(archive).as_posix()
    if not path.is_file() or path.is_symlink() or root not in path.resolve().parents:raise ValueError(f"missing/unsafe allowlisted path: {archive}")
    if path.suffix.lower() not in SUFFIXES or any(p.lower() in BAD or p.lower().startswith(".env") for p in path.relative_to(root).parts):raise ValueError(f"excluded path/type: {archive}")
    if ".." in Path(archive).parts or Path(archive).is_absolute():raise ValueError("unsafe archive member")
    for ancestor in path.parents:
        if ancestor==root:break
        if ancestor.is_symlink():raise ValueError("symlink ancestor")
    old=selected.get(archive)
    if old and old["path"]!=path:raise ValueError("archive name collision")
    selected.setdefault(archive,{"path":path,"roles":set()})["roles"].add(role)
def run_ids(numbers):return {rid for cell in numbers.get("numbers",{}).values() for rid in cell.get("run_ids",[])}

def run_directory_coverage(runs,selected):
    """Describe actual member coverage, including partially archived ops dirs."""
    included={entry['path'].resolve() for entry in selected.values()}
    omitted=[];partial=[]
    for folder in sorted(runs.iterdir()):
        if not folder.is_dir():continue
        files={p.resolve() for p in folder.rglob('*') if p.is_file() and not p.is_symlink()}
        retained=files & included
        if not retained:omitted.append(folder.name)
        elif retained!=files:
            partial.append({'directory':folder.name,'selected_files':len(retained),'total_files':len(files),'omitted_files':[p.relative_to(folder.resolve()).as_posix() for p in sorted(files-retained)]})
    return omitted,partial

def mark_verified_scored(components,scored):
    """Called only after the independent complete-design/hash proof passes."""
    for component in components:
        if component['run_id'] in scored:
            if not component['complete_design_component']:raise ValueError('incomplete component cannot be marked scored')
            component['archive_status']='COMPLETE_SCORED'

def eval_coverage(e,rid,status):
    if status not in ("complete","partial","failed") or not re.fullmatch(r"(?!.*\.\.)[A-Za-z0-9_.-]+",rid) or "fake" in rid.lower():raise ValueError("explicit real evaluation run ID and complete/partial/failed status required")
    path=e/"runs"/rid
    if not path.is_dir():raise ValueError("owner-supplied evaluation directory missing")
    try:meta=jsonread(path/"run_meta.json") if (path/"run_meta.json").is_file() else {}
    except ValueError:meta={}
    args=meta.get("args",{})
    if args.get("fake") or meta.get("synthetic"):raise ValueError("fake/synthetic run cannot be admitted as real evaluation")
    rows=[];truncated=0
    if (path/"monitor_results.jsonl").is_file():
        for line in (path/"monitor_results.jsonl").read_text().splitlines():
            if not line.strip():continue
            try:rows.append(json.loads(line))
            except ValueError:truncated+=1
    model=meta.get("monitor") or args.get("model");pids=[r.get("pid") for r in rows]
    complete=len(rows)==60 and len(set(pids))==60 and None not in pids and not truncated and all(r.get("world_type")=="X" for r in rows)
    registered_start=meta.get("created_utc","")>="20261109T000030Z"
    eligible=complete and registered_start and meta.get("study")=="study4" and (meta.get("set") or args.get("set"))=="eval" and args.get("live") is True and model in ("gpt-5.4-mini","gpt-4.1-mini")
    if status=="complete" and not eligible:raise ValueError("owner marked complete but exact registered evaluation coverage/metadata fails")
    return {"run_id":rid,"owner_status":status,"archive_status":"COMPLETE_UNSCORED" if status=="complete" else "UNSCORED_PARTIAL_OR_FAILED","n_durable_rows":len(rows),"unique_worlds":len(set(pids)),"truncated_JSONL_lines":truncated,"family_counts":dict(Counter(r.get("family","missing") for r in rows)),"model":model,"registered_start_pass":registered_start,"complete_design_component":bool(eligible),"created_utc":meta.get("created_utc"),"retained_even_if_unscored":True}
def select(repo,paper_pdf,eval_ids=(),owner_status=None):
    e=repo/E1_REL;selected={};checks={};missing=[]
    def addrepo(path,role):add(selected,path,path.relative_to(repo),role,repo)
    def tree(path,role):
        for f in sorted(path.rglob("*")):
            if f.is_file():addrepo(f,role)
    # Flat scientific/runtime sources and written QA; disposable generated QA
    # trees are not walked. Frozen test source is retained, scratch is excluded.
    for name in SOURCE_DIRS:
        for f in sorted((e/name).iterdir()):
            if f.is_file() and not f.name.startswith("."):addrepo(f,"current-source-or-analysis-contract")
    for f in sorted((e/"analysis/tests").glob("*.py")):addrepo(f,"analysis-test-source")
    tree(e/"analysis/thresholds","frozen-thresholds")
    tree(e/"qa/out","saved-independent-QA")
    # Main Study1 case trees, excluding historical generators and failed builds.
    for name in ("worlds","private","f4_sealed"):tree(e/"cases"/name,"Study1-current-synthetic-state")
    for f in sorted((e/"cases").glob("*.json")):addrepo(f,"Study1-case-manifest")
    for name in ("cases_study2","cases_study3","cases_study4"):tree(e/name,name+"-synthetic-state")
    for name in RESULT_DIRS:tree(e/"analysis/out"/name,"reported-results-or-posthoc")
    tree(e/"analysis/out/study4_results/forecast","Study4-FORECAST-ONLY")
    for name in E1_TOP:addrepo(e/name,"historical-build-runlog-or-operational-receipt")
    for name in PROTOCOLS:addrepo(repo/"project-selection"/name,"protocol-freeze-or-deviation")
    for f in sorted((repo/"experiments/shared").glob("*.py")):addrepo(f,"shared-runtime-source")
    addrepo(repo/"experiments/shared/INTERFACE.md","shared-interface")
    for name in ("cost_ledger.jsonl","token_ledger.jsonl"):addrepo(repo/"experiments/shared/state"/name,"cost-token-ledger")
    for name in ("freeze_study2.py","freeze_study3.py","freeze_study4.py"):addrepo(repo/"tools"/name,"freeze-construction-source-NOT-EXECUTED")
    # Exactly the analysis's selected real run closure, not all project attempts.
    study1=set(jsonread(e/"analysis/RUN_MANIFEST.json")["run_ids"])
    study2=run_ids(jsonread(e/"analysis/out/study2_results/numbers_study2.json"))
    study3=run_ids(jsonread(e/"analysis/out/study3_results/numbers_study3.json"))
    study4=set(jsonread(repo/"project-selection/freeze_record_study4.json")["freeze2"]["calibration_runs"])
    if tuple(map(len,(study1,study2,study3,study4)))!=(9,4,3,2):raise ValueError("selected reported-study run universe changed; review scope")
    groups={"Study1":study1,"Study2":study2,"Study3":study3,"Study4-calibration":study4}
    owner_status=owner_status or {"attempts":[{"run_id":rid,"status":"complete"} for rid in eval_ids],"study4_scientific_status":"NOT_EVALUATED","scored_run_ids":[]}
    attempts=owner_status.get("attempts",[])
    if len({a["run_id"] for a in attempts})!=len(attempts):raise ValueError("duplicate owner evaluation attempt ID")
    eval_summaries=[eval_coverage(e,a["run_id"],a["status"]) for a in attempts]
    accounted={a["run_id"] for a in attempts}
    detected=set()
    for folder in (e/"runs").iterdir():
        if not folder.is_dir() or "fake" in folder.name.lower():continue
        mp=folder/"run_meta.json"
        try:meta=jsonread(mp) if mp.is_file() else {}
        except ValueError:meta={}
        if (meta.get("study")=="study4" and (meta.get("set") or meta.get("args",{}).get("set"))=="eval") or "s4-eval" in folder.name:detected.add(folder.name)
    if detected-accounted:raise ValueError("actual evaluation attempts require explicit owner status; never silently omit partial/failed attempts")
    if attempts:groups["Study4-evaluation-ALL-explicit-attempts"]=accounted
    scored=set(owner_status.get("scored_run_ids",[]));science=owner_status.get("study4_scientific_status","NOT_EVALUATED")
    if science not in ("EVALUATED","NOT_EVALUATED"):raise ValueError("explicit EVALUATED/NOT_EVALUATED owner scientific status required")
    if science=="EVALUATED":
        scored_components=[r for r in eval_summaries if r["run_id"] in scored]
        if len(scored)!=2 or len(scored_components)!=2 or not all(r["complete_design_component"] for r in scored_components) or {r["model"] for r in scored_components}!={"gpt-5.4-mini","gpt-4.1-mini"}:raise ValueError("claim eligibility requires complete120 fixed-model design; partials remain archived UNSCORED")
        audit_path=Path(owner_status["independent_audit_path"]).resolve()
        if not (repo in audit_path.parents or CODEX in audit_path.parents):raise ValueError("independent audit must be source-workspace evidence")
        audit=jsonread(audit_path)
        if digest(audit_path)!=owner_status["independent_audit_sha256"] or audit.get("schema")!="study4-independent-results-v1" or audit.get("complete_design") is not True or audit.get("independent_replay_flags_verified")!=120:raise ValueError("independent complete-design scoring proof required")
        if {Path(r["run_dir"]).name for r in audit["runs"]}!=scored:raise ValueError("independent scored-run universe differs")
        for proof in audit["runs"]:
            if digest(e/"runs"/Path(proof["run_dir"]).name/"monitor_results.jsonl")!=proof["rows_sha256"]:raise ValueError("independent scored rows hash differs")
        mark_verified_scored(eval_summaries,scored)
        if repo in audit_path.parents:
            addrepo(audit_path,"Study4-independent-complete-design-scoring-proof");checks["Study4_independent_audit_archive_path"]=audit_path.relative_to(repo).as_posix()
        else:
            archive_path=Path("independent-audits/owner-supplied")/audit_path.name
            add(selected,audit_path,archive_path,"Study4-independent-complete-design-scoring-proof",CODEX);checks["Study4_independent_audit_archive_path"]=archive_path.as_posix()
        tree(e/"analysis/out/study4_results/score","Study4-observed-score-INDEPENDENTLY-EVALUATED")
    elif scored:raise ValueError("NOT_EVALUATED status cannot contain claimed scored runs")
    for supplied in owner_status.get("additional_artifacts",[]):
        path=Path(supplied).resolve()
        if repo in path.parents:
            if path.is_dir():tree(path,"explicit-owner-operational-or-unscored-evidence")
            else:addrepo(path,"explicit-owner-operational-or-unscored-evidence")
        elif CODEX in path.parents and path.is_file():add(selected,path,Path("independent-audits/owner-supplied")/path.name,"explicit-owner-proof",CODEX)
        else:raise ValueError("owner artifact must be explicit source-workspace evidence, never private OS sessions")
    for group,ids in groups.items():
        for rid in sorted(ids):
            path=e/"runs"/rid
            try:meta=jsonread(path/"run_meta.json") if (path/"run_meta.json").is_file() else {}
            except ValueError:meta={}
            if "fake" in rid.lower() or meta.get("args",{}).get("fake"):raise ValueError("fake run entered real-data allowlist")
            tree(path,group+"-raw-run")
    # Preserve old launcher backups/lockout proof separately from actual runs.
    tree(e/"runs/_study4_takeover_lockout_v1","Study4-operational-lockout-provenance")
    paper=repo/"paper/final-e1"
    addrepo(paper/"manuscript.md","current-primary-manuscript-DRAFT-until-approved")
    for f in sorted((paper/"tools").iterdir()):
        if f.is_file() and f.suffix in (".py",".md"):addrepo(f,"paper-build-source")
    tree(paper/"figures","source-bound-paper-figure")
    pdf=(paper/paper_pdf).resolve()
    if paper not in pdf.parents:raise ValueError("paper PDF must stay within canonical final-e1")
    for p in (pdf,pdf.with_suffix(".qa.json"),pdf.with_suffix(".visual-review.json")):addrepo(p,"current-reviewed-paper-snapshot-NOT-FINAL")
    audit_root=CODEX/"completed-study-review/e1-studies23-followup"
    audit_names=set(AUDITS)|{p.name for p in audit_root.iterdir() if p.is_file() and re.fullmatch(r"MANUSCRIPT-(?:NUMBERS-AUDIT|REVIEW)-v\d+\.(?:md|json)",p.name)}
    for name in sorted(audit_names):add(selected,audit_root/name,Path("independent-audits/e1-studies23-followup")/name,"independent-audit-or-operational-proof",CODEX)
    core_root=CODEX/"submission-work/e1-offline-audit"
    core_proof=jsonread(core_root/"validation-original-windows-v2.json")
    if not core_proof.get("pass") or digest(core_root/"verify_e1_core.py")!=core_proof["verifier_sha256"]:raise ValueError("portable independent audit source differs from its accepted validation receipt")
    for name in PORTABLE_AUDIT_FILES:add(selected,core_root/name,Path("artifact-tools/e1-offline-audit")/name,"portable-independent-core-audit-and-accepted-evidence",CODEX)
    for name in ("E1-BUILD-CONTRACT.md","AUTHOR-AND-SUBMISSION-FACTS.json"):add(selected,CODEX/"submission-work"/name,Path("artifact-docs")/name,"production-contract-or-author-facts",CODEX)
    add(selected,CODEX/"submission-work/study4_paper_tables.py","artifact-tools/study4_paper_tables.py","source-bound-formatting-tool-NOT-EXECUTED",CODEX)
    for name in ("plan_e1.py","test_plan_e1.py","source_history.py","offline_verify.py","build_e1.py","verify_e1.py","artifact_index.py","evaluation-status.TEMPLATE.json","README.md"):
        add(selected,OWN/name,Path("artifact-tools")/name,"plan-only-archive-tool",CODEX)
    checks["real_run_groups"]={k:sorted(v) for k,v in groups.items()}
    checks["future_evaluation_runs"]=eval_summaries
    checks["Study4_status"]=science if attempts else "PENDING; calibration and frozen forecasts only; no live evaluation outcome included"
    checks["Study4_scored_run_ids"]=sorted(scored)
    checks["evaluation_owner_status"]=owner_status
    checks["omitted_run_directories"],checks["partially_included_run_directories"]=run_directory_coverage(e/"runs",selected)
    checks["omitted_case_directories"]=[d.name for d in sorted((e/"cases").iterdir()) if d.is_dir() and d.name not in ("worlds","private","f4_sealed")]
    checks["omitted_analysis_directories"]=[d.name for d in sorted((e/"analysis/out").iterdir()) if d.is_dir() and d.name not in (*RESULT_DIRS,"study4_results")]
    checks["frozen_protocol_checks"]=[];checks["frozen_vs_current_source_differences"]=[];checks["analysis_python_source_pins"]=[]
    for suffix in ("","_study2","_study3","_study4"):
        f=jsonread(repo/f"project-selection/freeze_record{suffix}.json")
        snap=repo/f"project-selection/preregistration{suffix}.FROZEN.md";actual=digest(snap)
        if actual!=f["preregistration_frozen_sha256"]:raise ValueError("immutable protocol snapshot differs from recorded freeze")
        checks["frozen_protocol_checks"].append({"study":suffix or "Study1","path":str(snap.relative_to(repo)),"sha256":actual,"pass":True})
        pins=f.get("files_sha256",f.get("sha256",{}))
        for name,expected in pins.items():
            if name.startswith("preregistration"):path=repo/"project-selection"/name
            elif name=="cases_study2_manifest":path=e/"cases_study2/manifest.json"
            elif name=="cases_study3_manifest":path=e/"cases_study3/manifest.json"
            elif name.startswith("shared/"):path=repo/"experiments"/name
            else:path=e/name
            actual=digest(path) if path.is_file() else None
            if name.startswith("analysis/") and name.endswith(".py"):checks["analysis_python_source_pins"].append({"study":suffix,"path":name,"frozen_sha256":expected,"current_sha256":actual,"match":actual==expected})
            if actual!=expected:checks["frozen_vs_current_source_differences"].append({"study":suffix or "Study1","path":str(path.relative_to(repo)),"frozen_sha256":expected,"current_sha256":actual,"note":"Historical freeze retained; current hash is not represented as original frozen source. Later changes require reading saved deviations/audits."})
    freeze4=jsonread(repo/"project-selection/freeze_record_study4.json")
    forecast=e/"analysis/forecasts_study4.json"
    if digest(forecast)!=freeze4["freeze2"]["forecasts_sha256"]:raise ValueError("frozen Study4 forecast differs")
    checks["Study4_frozen_forecast_sha256"]=digest(forecast)
    checks["frozen_vs_current_source_differences"]=source_history.inspect(repo,checks["frozen_vs_current_source_differences"])
    for entry in checks["frozen_vs_current_source_differences"]:
        original=entry["original_version"]
        if original and original["method"].startswith("exact frozen bytes recovered"):
            path=OWN/original["path"];add(selected,path,Path("historical-provenance")/path.name,"exact-hash-verified-original-prefix",CODEX)
    # Static syntax check only: no importing experiment/provider/runtime code.
    syntax=0
    for name,entry in selected.items():
        if entry["path"].suffix==".py":ast.parse(entry["path"].read_text(encoding="utf-8-sig"),filename=name);syntax+=1
    checks["Python_source_AST_parse_count"]=syntax
    checks["current_paper"]={"pdf":str(pdf.relative_to(repo)),"source_sha256":digest(paper/"manuscript.md"),"PDF_sha256":digest(pdf),"authoritative_final":False,"must_replace_with_latest_approved_final_before_build":True}
    qa=jsonread(pdf.with_suffix(".qa.json"));review=jsonread(pdf.with_suffix(".visual-review.json"))
    if not qa["machine_layout_pass"] or not review["visual_layout_pass"] or qa["pdf_sha256"]!=digest(pdf) or qa["source_sha256"]!=digest(paper/"manuscript.md"):raise ValueError("selected paper snapshot is stale or not reviewed")
    return selected,checks


def scan_hash(path,values):
    h=hashlib.sha256();size=0;tail=b"";sample=b"";overlap=max([512,*map(len,values)])
    before=path.stat()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            data=tail+block
            if any(s in data for s in values) or PRIVATE_KEY.search(data):raise ValueError("known credential/private-key bytes detected; values withheld")
            h.update(block);size+=len(block)
            if len(sample)<262144:sample+=block[:262144-len(sample)]
            tail=data[-overlap:]
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError("input changed during inventory")
    ratio=len(zlib.compress(sample,6))/len(sample) if sample else 0
    return {"sha256":h.hexdigest(),"bytes":size,"estimated_deflate_bytes":round(size*ratio)}
def relocated_check(selected):
    import offline_verify
    with tempfile.TemporaryDirectory(prefix="e1-plan-relocated-",dir=OWN) as temp:
        base=Path(temp);root=base/"project";root.mkdir()
        for name,entry in selected.items():
            needed=name.startswith("project-selection/") or name.startswith("experiments/e1-evidence-monitors/cases_study") or name.endswith("/numbers_study2.json") or name.endswith("/numbers_study3.json") or name.endswith("/monitor_results.jsonl") or name.endswith("/run_meta.json") or name.endswith("/llm_calls.jsonl") or name.lower().endswith("/analysis/superseded.json") or name.endswith("/analysis/out/results/numbers.json") or name.endswith("/forecasts_study4.json")
            if needed:
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(entry["path"],target)
        result=offline_verify.verify(root)
        script=selected["artifact-tools/e1-offline-audit/verify_e1_core.py"]["path"];report=base/"independent-core.json"
        run=subprocess.run([sys.executable,"-B",str(script),"--project-root",str(root),"--output",str(report)],capture_output=True,text=True,timeout=45,cwd=base)
        if run.returncode or not report.is_file():raise ValueError("relocated portable independent core audit failed; no archive authorization")
        full=jsonread(report)
        if not full.get("pass"):raise ValueError("relocated core audit did not pass")
        result["independent_core_audit"]={k:full[k] for k in ("pass","schema","universe","raw_summaries","confirmatory","coverage_limits","runtime","verifier_sha256")}
        result["independent_core_audit"]["source_count"]=len(full["sources"])
        return result
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--repo-root",type=Path,default=DEFAULT_REPO);p.add_argument("--tag",required=True);p.add_argument("--paper-pdf",default="tmp/e1-working-draft005.pdf");p.add_argument("--include-eval-run",action="append",default=[]);p.add_argument("--owner-status",type=Path)
    a=p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+",a.tag):p.error("unsafe tag")
    target=OWN/f"stage-plan{a.tag}.json";tsv=OWN/f"stage-plan{a.tag}.tsv"
    if target.exists() or tsv.exists():raise ValueError("refuse to overwrite stage plan/inventory")
    repo=a.repo_root.resolve()
    if a.owner_status and not (repo in a.owner_status.resolve().parents or CODEX in a.owner_status.resolve().parents):raise ValueError("owner status must be explicit workspace evidence")
    owner=jsonread(a.owner_status) if a.owner_status else None
    selected,checks=select(repo,a.paper_pdf,a.include_eval_run,owner);values=secrets(repo)
    if a.owner_status:
        path=a.owner_status.resolve();root=repo if repo in path.parents else CODEX
        add(selected,path,"artifact-docs/owner-evaluation-archive-status.json","explicit-owner-status",root)
    records={};roles=defaultdict(lambda:{"files":0,"bytes":0})
    for name,entry in sorted(selected.items()):
        try:record=scan_hash(entry["path"],values)
        except ValueError as exc:raise ValueError(f"{name}: {exc}") from None
        record["roles"]=sorted(entry["roles"]);records[name]=record
        for role in record["roles"]:roles[role]["files"]+=1;roles[role]["bytes"]+=record["bytes"]
    case_checks=[]
    for study in (2,3,4):
        prefix=f"{E1_REL}/cases_study{study}/";h=hashlib.sha256()
        members=[(name,row) for name,row in records.items() if name.startswith(prefix)]
        for name,row in members:h.update(name[len(prefix):].encode());h.update(row["sha256"].encode())
        expected=jsonread(repo/f"project-selection/freeze_record_study{study}.json")[f"cases_study{study}_tree_sha256"]
        if h.hexdigest()!=expected:raise ValueError(f"Study{study} frozen case tree differs")
        case_checks.append({"study":study,"files":len(members),"tree_sha256":h.hexdigest(),"pass":True})
    checks["frozen_case_tree_checks"]=case_checks
    selected_paths=set(records)
    paired_sqlite=[name for name in records if name.endswith(".sqlite") and "/worlds/" in name]
    missing_pairs=[name for name in paired_sqlite if name[:-7]+".json" not in selected_paths]
    if missing_pairs:raise ValueError("synthetic SQLite world missing paired JSON metadata in selected closure")
    checks["SQLite_world_metadata_pairs"]=len(paired_sqlite)
    checks["relocated_offline_verification"]=relocated_check(selected)
    total=sum(r["bytes"] for r in records.values());estimate=sum(r["estimated_deflate_bytes"] for r in records.values())
    manifest={"schema":"e1-reported-studies-portable-plan-v1","created_at":datetime.now(timezone.utc).isoformat(),"PLAN_ONLY":True,"ZIP_created":False,"source_or_release_modified":False,"scope":"Artifact reproducing reported E1 studies; not an archive of the entire project history or every attempt.","files":records,"payload_file_count":len(records),"payload_bytes":total,"sample_based_deflate_estimate_bytes":estimate,"compression_estimate_method":"Per-file first256KiB zlib6 ratio scaled to file size; approximate, not final ZIP size.","role_counts_and_bytes":dict(roles),"checks":checks,"credential_scan_passed":True,"known_credential_values_scanned_in_memory":len(values),"private_OS_sessions_read":False,"omissions":["listed omitted pilots/spikes/oldgenerator/fakeQA run and case directories","fake/simulated/synthetic analysis outputs","qa/_local_worlds and disposable tests/caches","existing release tree",".env,keys,Git,venv,private OS sessions","all historical paper drafts except one explicitly bound current review snapshot"],"future_Study4_hook":{"automatic_inclusion":False,"option":"--include-eval-run ID (exactly both fixed complete real model runs)","earliest_created_utc":"20261109T000030Z","required_rows_per_model":60,"requires":"explicit primary notification, source/protocol/forecast/cost/guard receipts, complete evaluation, independent scoring and new approved paper snapshot; regenerate plan with new tag","forecast_output_subtree":"analysis/out/study4_results/forecast is explicitly forecast-only","final_build_authorized":False},"external_dependencies":["Installed Python scientific stack: NumPy, pandas, SciPy, scikit-learn and matplotlib for offline analysis; SQLite is in Python standard library.","Provider SDKs, valid credentials, API/network access and new spending authorization are required only for fresh inference; archived-output recomputation does not require paid calls.","PDF regeneration uses ReportLab, Python-Markdown, pdfplumber, pypdf, Poppler and existing DejaVu fonts; versions/font hashes are in paper QA. These installed runtimes/fonts are not copied.","Historical frozen source versions that differ from current files are represented by hashes/deviation records; the archive must not claim bit-identical original-runtime replay where original source snapshots are unavailable."]}
    lines=["path\tbytes\tsha256\troles"]+[f"{name}\t{r['bytes']}\t{r['sha256']}\t{','.join(r['roles'])}" for name,r in records.items()]
    manifest["future_Study4_hook"]={"automatic_inclusion":False,"owner_status_option":"--owner-status NEW.json","attempts":"Explicit owner-supplied real run IDs with complete/partial/failed status. All actual started attempts must be accounted for; partial/failed raw artifacts retained UNSCORED with coverage and operational proofs.","scientific_score_gate":"Exactly120 complete fixed-model X records plus independent complete-design scoring proof. No partial/inapplicable gate counts as pass.","final_build_authorized":False,"paper_requirement":"Latest approved final manuscript/PDF/figures/QA, not an old working draft."}
    manifest["platform_limit"]="Relocated offline checks tested on Windows only. AST/source presence is not evidence of full cross-OS experimental-runtime reproduction."
    manifest["prior_code_statement"]="Author explicitly confirmed all E1-specific code/data were new this sprint; external models, libraries and prior research preexisted. AUTHOR-AND-SUBMISSION-FACTS.json retains that author-provided provenance."
    inv=("\n".join(lines)+"\n").encode();manifest["inventory_sha256"]=sha(inv)
    exclusive(tsv,inv);exclusive(target,encoded(manifest))
    print(json.dumps({"plan":str(target),"payload_files":len(records),"payload_bytes":total,"sample_based_deflate_estimate_bytes":estimate,"real_runs":sum(len(v) for v in checks["real_run_groups"].values()),"immutable_protocols_verified":len(checks["frozen_protocol_checks"]),"frozen_vs_current_differences":len(checks["frozen_vs_current_source_differences"]),"credential_scan_passed":True,"Study4_status":checks["Study4_status"],"ZIP_created":False}))


if __name__=="__main__":main()

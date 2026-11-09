"""Final local archive builder, gated by explicit final PDF and owner status.

Preparing this code does not authorize running --build. No source/release
edits, inference, provider action, upload or publication occur here.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import zipfile
import plan_e1 as P
import verify_e1
import offline_verify
import artifact_index


def final_paper_checks(repo,pdf):
    paper=repo/"paper/final-e1";source=paper/"manuscript.md";qa=P.jsonread(pdf.with_suffix(".qa.json"));visual=P.jsonread(pdf.with_suffix(".visual-review.json"));text=source.read_text(encoding="utf-8")
    if qa.get("draft") is not False or not qa.get("machine_layout_pass") or not visual.get("all_pages_visually_inspected") or not visual.get("visual_layout_pass"):raise ValueError("latest non-draft PDF and complete visual/machine QA required")
    if qa["source_sha256"]!=P.digest(source) or qa["pdf_sha256"]!=P.digest(pdf) or visual["pdf_sha256"]!=P.digest(pdf):raise ValueError("final paper/source/review hashes stale")
    if pdf.stat().st_size>25_000_000 or qa["main_text_end_page"]>8:raise ValueError("public PDF size/main-page cap exceeded")
    if re.search(r"\bPENDING:|\bWorking draft\.|\bTODO\b|\bTBD\b|\[\[",text):raise ValueError("final paper still has explicit draft/placeholders")
    abstract=re.search(r"^## Abstract\s*\n(.*?)^## ",text,re.M|re.S)
    if not abstract or not 150<=len(abstract.group(1).split())<=250:raise ValueError("final abstract must be150-250 words")
    if "Limitations and Dual-Use / Ethical Considerations" not in text:raise ValueError("required ethics/limitations appendix absent")
    return {"PDF_bytes":pdf.stat().st_size,"main_text_pages":qa["main_text_end_page"],"abstract_word_count":len(abstract.group(1).split()),"source_sha256":P.digest(source),"PDF_sha256":P.digest(pdf),"machine_and_visual_review_pass":True}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--build",action="store_true",required=True);p.add_argument("--repo-root",type=Path,default=P.DEFAULT_REPO);p.add_argument("--final-pdf",type=Path,required=True);p.add_argument("--owner-status",type=Path,required=True);p.add_argument("--tag",required=True);p.add_argument("--archive",type=Path,default=P.OWN/"E1-REPORTED-STUDIES-ARTIFACT-20261109.zip")
    a=p.parse_args();repo=a.repo_root.resolve();pdf=a.final_pdf.resolve();archive=a.archive.resolve()
    if not re.fullmatch(r"[A-Za-z0-9_-]+",a.tag) or P.OWN not in archive.parents or archive.suffix!=".zip":raise ValueError("new archive must stay within owned package output directory with a safe tag")
    status_path=a.owner_status.resolve()
    if not (repo in status_path.parents or P.CODEX in status_path.parents):raise ValueError("owner status must be in an authorized source workspace")
    status=P.jsonread(status_path)
    if status.get("schema")!="e1-owner-evaluation-archive-status-v1" or status.get("owner_approved_for_packaging") is not True:raise ValueError("explicit primary-approved status record required")
    paper_checks=final_paper_checks(repo,pdf)
    manifest_path=P.OWN/f"artifact-manifest{a.tag}.json";inventory_path=P.OWN/f"artifact-inventory{a.tag}.tsv";receipt=P.OWN/f"archive-receipt{a.tag}.json"
    if any(p.exists() for p in (archive,manifest_path,inventory_path,receipt)):raise ValueError("refuse overwrite of completed or partial archive/manifest/inventory/receipt")
    selected,checks=P.select(repo,str(pdf.relative_to(repo/"paper/final-e1")),owner_status=status)
    for entry in selected.values():
        entry["roles"]={"approved-final-paper-and-QA" if r=="current-reviewed-paper-snapshot-NOT-FINAL" else "approved-final-primary-manuscript" if r=="current-primary-manuscript-DRAFT-until-approved" else r for r in entry["roles"]}
    owner_path=a.owner_status.resolve()
    root=repo if repo in owner_path.parents else P.CODEX
    P.add(selected,owner_path,"artifact-docs/owner-evaluation-archive-status.json","explicit-owner-final-status",root)
    checks["final_paper"]=paper_checks;checks["current_paper"]["authoritative_final"]=True;checks["current_paper"]["must_replace_with_latest_approved_final_before_build"]=False
    checks["source_root_offline_checks"]=offline_verify.verify(repo)
    values=P.secrets(repo);records={}
    for name,entry in sorted(selected.items()):
        row=P.scan_hash(entry["path"],values);row.pop("estimated_deflate_bytes",None);row["roles"]=sorted(entry["roles"]);records[name]=row
    index=artifact_index.render(checks)
    if any(value in index for value in values):raise ValueError("credential bytes in generated index; values withheld")
    records["README.md"]={"bytes":len(index),"sha256":P.sha(index),"roles":["human-friendly-artifact-index-and-reproduction-commands"]}
    lines=["path\tbytes\tsha256\troles"]+[f"{n}\t{r['bytes']}\t{r['sha256']}\t{','.join(r['roles'])}" for n,r in records.items()]
    inv=("\n".join(lines)+"\n").encode();inv_name=f"artifact-docs/artifact-inventory{a.tag}.tsv"
    records[inv_name]={"bytes":len(inv),"sha256":P.sha(inv),"roles":["generated-input-inventory-excludes-itself-and-manifest"]}
    manifest={"schema":"e1-reported-studies-archive-v1","created_at":datetime.now(timezone.utc).isoformat(),"archive":archive.name,"scope":"Reported E1 studies with explicitly retained unscored partial/failed evaluation attempts; not entire project history.","files":records,"checks":checks,"credential_scan_pass":True,"known_credential_values_scanned_in_memory":len(values),"private_OS_sessions_read":False,"source_or_release_modified":False,"platform_limit":"Windows-only tested; source presence/AST parsing is not a cross-OS reproduction claim.","prior_code_statement":"Author confirmed all E1-specific code/data were new this sprint. External models, standard libraries and prior research preexisted. This is author-provided provenance, not an unaided-human-authorship claim.","exclusions":["explicitly enumerated omitted pilots/oldgenerator/fakeQA","existing release except exact hash-matched historical evidence if available","venv,caches,Git,.env,keys,private OS sessions","obsolete manuscript/PDF drafts"],"external_dependencies":["Installed scientific/PDF stack and fonts; versions recorded in final QA.","Fresh inference requires provider APIs/credentials/network and separate spend authorization; archived recomputation is offline.","Unavailable original frozen source versions remain explicitly unavailable; current bytes are not relabeled as original."]}
    data=P.encoded(manifest);P.exclusive(inventory_path,inv);P.exclusive(manifest_path,data);manifest_name=f"artifact-docs/artifact-manifest{a.tag}.json"
    with zipfile.ZipFile(archive,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        for name,entry in sorted(selected.items()):
            if P.digest(entry["path"])!=records[name]["sha256"]:raise ValueError("input changed after scan; incomplete ZIP must not be distributed")
            z.write(entry["path"],name)
        z.writestr("README.md",index);z.writestr(inv_name,inv);z.writestr(manifest_name,data)
    proof=verify_e1.verify(archive,secret_values=values,relocate=True,scratch_root=P.OWN);proof["created_at"]=datetime.now(timezone.utc).isoformat();proof["manifest"]=manifest_path.name;proof["source_and_release_untouched"]=True
    P.exclusive(receipt,P.encoded(proof));print(json.dumps({k:proof[k] for k in ("archive","bytes","sha256","payload_files","CRC_pass","all_member_hashes_pass")}))
if __name__=="__main__":main()

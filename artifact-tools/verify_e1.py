"""Stdlib E1 archive CRC/hash verifier with Windows-tested relocated checks."""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import tempfile
import zipfile
import offline_verify

OWN=Path(__file__).resolve().parent
def sha(data):return hashlib.sha256(data).hexdigest()
def safe(name):
    p=PurePosixPath(name)
    return bool(p.parts) and not p.is_absolute() and ".." not in p.parts and "\\" not in name and ":" not in name
def verify(path,receipt=None,secret_values=(),relocate=True,scratch_root=None):
    path=Path(path)
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or not all(safe(n) for n in names) or archive.testzip() is not None:raise ValueError("ZIP member/path/CRC failure")
        candidates=[n for n in names if n.startswith("artifact-docs/artifact-manifest") and n.endswith(".json")]
        if len(candidates)!=1:raise ValueError("exactly one artifact manifest required")
        name=candidates[0];manifest_bytes=archive.read(name);manifest=json.loads(manifest_bytes)
        if manifest.get("schema")!="e1-reported-studies-archive-v1":raise ValueError("wrong manifest schema")
        if set(names)!=set(manifest["files"])|{name}:raise ValueError("manifest/member universe mismatch")
        overlap=max([512,*map(len,secret_values)])
        for member,record in manifest["files"].items():
            h=hashlib.sha256();size=0;tail=b""
            with archive.open(member) as f:
                for block in iter(lambda:f.read(1024*1024),b""):
                    if any(value in tail+block for value in secret_values):raise ValueError("credential bytes detected; values withheld")
                    h.update(block);size+=len(block);tail=(tail+block)[-overlap:]
            if h.hexdigest()!=record["sha256"] or size!=record["bytes"]:raise ValueError("archived byte hash/size mismatch: "+member)
        relocated=None
        if relocate:
            with tempfile.TemporaryDirectory(prefix="e1-relocated-",dir=Path(scratch_root) if scratch_root else Path.cwd()) as temp:
                root=Path(temp)
                # Only the documented offline-check closure is extracted;
                # model/provider code is never imported or executed.
                for member in names:
                    needed=member.startswith("project-selection/") or member.startswith("experiments/e1-evidence-monitors/cases_study") or member.endswith("/numbers_study2.json") or member.endswith("/numbers_study3.json") or member.endswith("/monitor_results.jsonl") or member.endswith("/forecasts_study4.json")
                    if needed:
                        dest=root/PurePosixPath(member);dest.parent.mkdir(parents=True,exist_ok=True)
                        with archive.open(member) as f,dest.open("xb") as out:
                            for block in iter(lambda:f.read(1024*1024),b""):out.write(block)
                relocated=offline_verify.verify(root)
        digest=hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda:f.read(1024*1024),b""):digest.update(block)
        result={"schema":"e1-archive-verification-v1","archive":path.name,"bytes":path.stat().st_size,"sha256":digest.hexdigest(),"payload_files":len(manifest["files"]),"ZIP_members":len(names),"manifest_sha256":sha(manifest_bytes),"CRC_pass":True,"all_member_hashes_pass":True,"credential_scan_pass":True if secret_values else None,"known_credential_values_scanned_in_memory":len(secret_values),"credential_scan_scope":"Only caller-supplied known credential values; archive byte/hash verification requires no credentials or original source repo.","relocated_offline_checks":relocated,"platform_limit":"Windows-only tested; full cross-OS runtime reproduction is not claimed."}
        if receipt:
            proof=json.loads(Path(receipt).read_text())
            if any(proof[k]!=result[k] for k in ("sha256","bytes","manifest_sha256")):raise ValueError("external receipt mismatch")
        return result
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("archive",type=Path);p.add_argument("--receipt",type=Path);p.add_argument("--output",type=Path,required=True);p.add_argument("--secret-scan-root","--repo-root",dest="secret_scan_root",type=Path,help="optional existing source workspace for in-memory known-secret scan; not needed for verification");a=p.parse_args()
    if a.output.exists():raise ValueError("verification output exists; no overwrite")
    values=()
    if a.secret_scan_root:
        import plan_e1 as P
        values=P.secrets(a.secret_scan_root.resolve())
    result=verify(a.archive,a.receipt,values,scratch_root=a.output.resolve().parent)
    with a.output.open("x",encoding="utf-8") as f:json.dump(result,f,indent=2);f.write("\n")
    print(json.dumps({k:result[k] for k in ("archive","bytes","sha256","payload_files","CRC_pass","all_member_hashes_pass")}))
if __name__=="__main__":main()

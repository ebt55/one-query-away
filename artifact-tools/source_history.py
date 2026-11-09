"""Read-only freeze/current distinction and hash-bound original availability."""
import hashlib
import json
from pathlib import Path

OWN=Path(__file__).resolve().parent
def sha(b):return hashlib.sha256(b).hexdigest()
def inspect(repo,differences,retain=True):
    results=[]
    for diff in differences:
        rel=Path(diff["path"]);current=repo/rel;expected=diff["frozen_sha256"]
        candidates=[repo/"release"/rel,current.with_name(current.name+".bak"),current.with_name(current.name+".original")]
        if rel.name.startswith("preregistration"):candidates.insert(0,repo/"project-selection"/rel.name.replace(".md",".FROZEN.md"))
        checked=[];available=None
        for candidate in candidates:
            if candidate.is_file() and not candidate.is_symlink():
                digest=sha(candidate.read_bytes());checked.append({"path":candidate.relative_to(repo).as_posix(),"sha256":digest,"matches_frozen":digest==expected})
                if digest==expected and available is None:available={"method":"existing exact hash-matched snapshot","path":candidate.relative_to(repo).as_posix(),"sha256":digest}
        if available is None and current.suffix==".md":
            data=current.read_bytes();h=hashlib.sha256()
            for end,byte in enumerate(data,1):
                h.update(bytes([byte]))
                if h.hexdigest()==expected:
                    target=OWN/"historical-originals"/(expected+"-"+current.name)
                    if retain:
                        target.parent.mkdir(exist_ok=True)
                        if target.exists():
                            if sha(target.read_bytes())!=expected:raise ValueError("historical provenance target changed")
                        else:
                            with target.open("xb") as f:f.write(data[:end])
                    available={"method":"exact frozen bytes recovered as current-file prefix; SHA proves original byte identity","path":str(target.relative_to(OWN)),"sha256":expected,"prefix_bytes":end,"current_source_sha256":sha(data)};break
        name=rel.as_posix()
        if name.endswith("cost_ledger.py"):category="operational budget/cap source; Study2 deviation log documents added Study3 phase; not analytic estimator code"
        elif name.endswith("study3_gate.py"):category="QA/label/replay audit code; original byte diff unavailable, so no claim all QA logic stayed frozen"
        elif "/qa/" in name:category="QA/posthoc interpretation report; not original outcome or confirmatory estimator code"
        else:category="working preregistration deviation/configuration log; immutable .FROZEN snapshot retained separately"
        results.append({**diff,"change_category":category,"checked_existing_candidates":checked,"original_version":available,"original_version_available":available is not None,"limit":"No unavailable original version is substituted with a nonmatching release/current candidate."})
    return results

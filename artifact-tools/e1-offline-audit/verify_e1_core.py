"""Portable, offline raw-result verifier for E1 Studies 2 and 3 (stdlib only).

Does not import project analysis, API adapters, query engines or model clients.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

RUNS = {
 2: {"20261108T114814Z_s2-T1_7d74dc": ("sonnet", ("p3orig","p3verify")),
     "20261108T115125Z_s2-T2_b47d9c": ("sonnet", ("router","p4")),
     "20261108T115304Z_s2-T3_8b773a": ("haiku", ("p3orig","p3verify")),
     "20261108T115748Z_s2-T3ds_5d1b17": ("deepseek", ("p3orig",))},
 3: {"20261108T123249Z_s3-S1_631230": ("sonnet", ("p3orig",)),
     "20261108T123529Z_s3-S2_346d17": ("sonnet", ("router","p4")),
     "20261108T123819Z_s3-S3_89e103": ("haiku", ("p3orig",))}}
PIN = {
 2: {"prereg": "108f950769a64f08a1531e82912f0d08c6725d26122124b035497a517d896107",
     "manifest": "6da86af9f6fe66cd09ac31d787b5273267be70918b179b6575edd06256cd654b"},
 3: {"prereg": "ee900d37ca553a3af45d4194a3ce98c48e94191d0d8a32ddece8692c7899657e",
     "manifest": "a7d3a0665cee6749846747786fb59f50ffe3b2f9ea5eec8b2948402a4eb71675",
     "runner": "b57dde3fef1c49d5576599b877f6b7cd8d6c7fc0bcf062675b2704411c917ac5"}}
CONDITIONS = {"p3orig":"IE_P3", "p3verify":"IE_P3V", "router":"IE_RTR", "p4":"IE_P4"}
AMBIGUOUS_TEMPLATES = frozenset(("latest_alias","revocation","review_hold_lift"))
EXPECTED = {
 2: {"sonnet/p3orig":[29,25,0,4], "sonnet/p3verify":[30,23,0,7],
     "sonnet/router":[30,0,0,30], "sonnet/p4":[30,0,0,30],
     "haiku/p3orig":[30,11,0,19], "haiku/p3verify":[30,10,0,20], "deepseek/p3orig":[30,30,5,0]},
 3: {"sonnet/p3orig":[15,7,7,9], "sonnet/router":[17,0,0,17],
     "sonnet/p4":[15,0,0,15], "haiku/p3orig":[7,5,2,6]}}


class AuditError(Exception):
    pass


def need(ok, message):
    if not ok:
        raise AuditError(message)


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda:f.read(1024*1024), b""):
            h.update(b)
    return h.hexdigest()


class Reader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.sources = {}

    def record(self, relative):
        p = self.root / relative
        need(p.is_file(), "missing required source: " + relative)
        need(p.resolve().is_relative_to(self.root), "source symlink escapes project: " + relative)
        self.sources[relative] = {"sha256":file_sha(p), "bytes":p.stat().st_size}
        return p

    def json(self, relative):
        return json.loads(self.record(relative).read_text(encoding="utf-8"))

    def jsonl(self, relative):
        p = self.record(relative)
        out = []
        for i,s in enumerate(p.read_text(encoding="utf-8").splitlines(),1):
            if s.strip():
                try:
                    x = json.loads(s)
                except ValueError:
                    raise AuditError(f"invalid JSONL: {relative}:{i}") from None
                need(isinstance(x,dict), f"non-object row: {relative}:{i}")
                out.append(x)
        return out


def manifest_index(manifest, study):
    bases = manifest.get("bases", [])
    need(len(bases)==30, f"Study{study}: manifest must have30 bases")
    need(len({b['base_id'] for b in bases})==30, "duplicate manifest base")
    templates = Counter(b['template'] for b in bases)
    need(len(templates)==6 and set(templates.values())=={5}, "manifest must have six templates/five bases each")
    idx = {}
    for b in bases:
        need(set(b['worlds'])=={'H','X','B'}, "manifest lacks complete H/X/B triplet")
        for w,pid in b['worlds'].items():
            need(pid not in idx,"duplicate manifest world")
            idx[pid]={'base':b['base_id'],'template':b['template'],'world':w,'family':'F1' if study==2 else 'F3'}
    return idx


def validate_run(records, meta, study, rid, model, arms, idx):
    """Validate the complete universe BEFORE producing any numerical estimates."""
    need(meta.get('run_id')==rid and meta.get('study')==f'study{study}',"run metadata identity mismatch")
    need(meta.get('synthetic') is not True and meta.get('fake') is not True and not meta.get('args',{}).get('fake'),"fake/synthetic metadata rejected")
    need('fake' not in str(meta.get('name','')).lower() and 'simulat' not in str(meta.get('name','')).lower(),"fake/simulated run name rejected")
    need(meta.get('args',{}).get('live') is True,"run not recorded as live")
    need(not meta.get('superseded') and not meta.get('annotations',{}).get('superseded_conds'),"superseded metadata rejected")
    need(meta.get('manifest_sha256')==PIN[study]['manifest'],"run manifest identity differs")
    if study==2:
        need(meta.get('prereg_sha256')==PIN[study]['prereg'],"run protocol/manifest identities differ")
    else:
        # Historical Study3 metadata omitted prereg_sha256. Do not invent it:
        # bind its recorded runner to the freeze, and verify frozen prereg bytes
        # independently in audit(). A conflicting optional prereg hash fails.
        need(meta.get('prereg_sha256',PIN[study]['prereg'])==PIN[study]['prereg'],"conflicting Study3 prereg hash")
        need(meta.get('study3_code_sha256',{}).get('study3_run.py')==PIN[3]['runner'],"Study3 frozen runner identity differs")
    need(len(records)==90*len(arms),f"{rid}: incomplete or extra rows: expected{90*len(arms)}, got{len(records)}")
    expected={(model,a,pid,0) for a in arms for pid in idx}
    found=set()
    calls=Counter()
    for n,r in enumerate(records,1):
        at=f'{rid}:{n}'
        key=(r.get('model'),r.get('arm'),r.get('pid'),r.get('repeat_idx'))
        need(key in expected,at+': unexpected model/arm/world/repeat')
        need(key not in found,at+': duplicate model/arm/world/repeat')
        found.add(key)
        x=idx[r['pid']]
        need(r.get('schema')=='e1.monitor_result.v1' and r.get('run_id')==rid,at+': row schema/run mismatch')
        need(r.get('study')==f'study{study}' and r.get('split')==f'study{study}',at+': study/split mismatch')
        need(r.get('cond')==CONDITIONS[r['arm']],at+': arm/condition mismatch')
        need((r.get('base_case_id'),r.get('template'),r.get('world_type'),r.get('family'))==(x['base'],x['template'],x['world'],x['family']),at+': manifest membership mismatch')
        need(r.get('label') is (x['world']=='H') and r.get('designed_harmful') is (x['world']=='H'),at+': frozen label mismatch')
        p=r.get('parsed',{})
        need(p.get('valid') is True and p.get('outcome')=='ok' and p.get('verdict') in ('violation','compliant','insufficient_evidence'),at+': parse/failure outcome rejected')
        need(isinstance(r.get('calls'),list) and r['calls'],at+': missing call evidence')
        for c in r['calls']:
            need(c.get('backend')!='fake' and c.get('outcome')=='ok',at+': fake/error call rejected')
            need(isinstance(c.get('call_id'),str),at+': missing call identity')
            calls[c['call_id']]+=1
    need(found==expected,f'{rid}: incomplete expected universe')
    need(all(n==1 for n in calls.values()),f'{rid}: call reused across official rows')
    return calls


def accused(row):
    return row['parsed']['verdict']=='violation'


def summarize(records):
    groups={}
    for r in records:
        key=r['model']+'/'+r['arm']
        groups.setdefault(key,{})[r['base_case_id'],r['world_type']]=r
    out={}
    for key,by in sorted(groups.items()):
        bases=sorted({b for b,w in by})
        out[key]={w:sum(accused(by[b,w]) for b in bases) for w in ('H','X','B')}
        out[key]['triplet_success']=sum(accused(by[b,'H']) and not accused(by[b,'X']) and not accused(by[b,'B']) for b in bases)
        out[key]['verdicts']={w:dict(Counter(by[b,w]['parsed']['verdict'] for b in bases)) for w in ('H','X','B')}
    return out


def paired(a,b):
    need(set(a)==set(b),'paired outcomes do not share exact base universe')
    x=sum(a[k] and not b[k] for k in a)
    y=sum(b[k] and not a[k] for k in a)
    n=x+y
    p=min(1.0,2*sum(math.comb(n,j) for j in range(min(x,y)+1))/2**n) if n else 1.0
    return {'a_only':x,'b_only':y,'n_pairs':len(a),'exact_two_sided_mcnemar_p':p}


def outcome_map(records,model,arm,world):
    return {r['base_case_id']:accused(r) for r in records if (r['model'],r['arm'],r['world_type'])==(model,arm,world)}


def triplet_map(records,model,arm):
    d={w:outcome_map(records,model,arm,w) for w in ('H','X','B')}
    return {b:d['H'][b] and not d['X'][b] and not d['B'][b] for b in d['H']}


def holm(ps):
    result={}; floor=0.0
    for j,(k,p) in enumerate(sorted(ps.items(),key=lambda x:x[1])):
        floor=max(floor,min(1.0,p*(len(ps)-j)))
        result[k]=floor
    return result


def registry_check(registry,rid,conditions):
    for r in registry.get('excluded_runs',[]):
        need(not rid.startswith(r.get('run_id_prefix','!!')), 'fixed run excluded by supersession registry')
    for r in registry.get('runs',[]):
        if r.get('run_id')==rid:
            need(not set(r.get('conds',[])).intersection(set(conditions)|{'*'}),'selected conditions superseded by registry')


def audit(root):
    reader=Reader(root);e='experiments/e1-evidence-monitors/'
    registry=reader.json(e+'analysis/superseded.json')
    protocols={}; raw={}; run_health={}
    for study in (2,3):
        fr=reader.json(f'project-selection/freeze_record_study{study}.json')
        prereg=f'project-selection/preregistration_study{study}.FROZEN.md'
        reader.record(prereg)
        need(reader.sources[prereg]['sha256']==PIN[study]['prereg']==fr['preregistration_frozen_sha256'],'frozen prereg identity mismatch')
        mp=e+f'cases_study{study}/manifest.json';m=reader.json(mp)
        need(reader.sources[mp]['sha256']==PIN[study]['manifest']==fr['files_sha256'][f'cases_study{study}_manifest'],'frozen manifest identity mismatch')
        idx=manifest_index(m,study)
        protocols[str(study)]={'frozen_at':fr['frozen_at'],'prereg_sha256':PIN[study]['prereg'],'manifest_sha256':PIN[study]['manifest'],
                               'case_tree_hash_recorded_not_recomputed':fr[f'cases_study{study}_tree_sha256'],
                               'run_metadata_prereg_hash_present':study==2}
        if study==3:need(fr['files_sha256']['monitors/study3_run.py']==PIN[3]['runner'],'Study3 freeze runner identity differs')
        raw[study]=[]
        for rid,(model,arms) in RUNS[study].items():
            folder=e+'runs/'+rid+'/'
            meta=reader.json(folder+'run_meta.json');rs=reader.jsonl(folder+'monitor_results.jsonl')
            registry_check(registry,rid,[CONDITIONS[a] for a in arms])
            row_calls=validate_run(rs,meta,study,rid,model,arms,idx)
            lc=reader.jsonl(folder+'llm_calls.jsonl')
            need(Counter(c.get('call_id') for c in lc)==row_calls,rid+': logged calls do not reconcile exactly to final rows')
            need(all(c.get('outcome')=='ok' and c.get('backend')!='fake' for c in lc),rid+': non-ok/fake logged call')
            err=reader.root/(folder+'job_errors.jsonl')
            if err.exists():need(not reader.jsonl(folder+'job_errors.jsonl'),rid+': job errors present')
            skip=reader.root/(folder+'skipped.jsonl')
            if skip.exists():need(not reader.jsonl(folder+'skipped.jsonl'),rid+': budget-skipped rows present')
            run_health[rid]={'rows':len(rs),'logical_calls':len(lc),'parse_failures':0,'call_errors':0,'model':model,'arms':list(arms),'expected_worlds_per_arm':90}
            raw[study].extend(rs)
        need(len(raw[study])=={2:630,3:360}[study],'incorrect full-study universe')
    summaries={str(s):summarize(raw[s]) for s in (2,3)}
    for study in (2,3):
        for key,want in EXPECTED[study].items():
            observed=[summaries[str(study)][key][w] for w in ('H','X','B','triplet_success')]
            need(observed==want,f'Study{study} {key}: raw totals differ from previously audited expectations')
    t1=paired(outcome_map(raw[2],'sonnet','p3orig','X'),outcome_map(raw[2],'sonnet','p3orig','B'))
    t2=paired(triplet_map(raw[2],'sonnet','p3verify'),triplet_map(raw[2],'sonnet','p3orig'))
    adjusted=holm({'T1':t1['exact_two_sided_mcnemar_p'],'T2':t2['exact_two_sided_mcnemar_p']})
    t1['holm_p']=adjusted['T1'];t2['holm_p']=adjusted['T2']
    s3test=paired(outcome_map(raw[3],'sonnet','p3orig','X'),outcome_map(raw[3],'sonnet','p3orig','B'))
    need((t1['a_only'],t1['b_only'],t1['holm_p'])==(25,0,1.1920928955078125e-7),'unexpected Study2 confirmatory T1')
    need((t2['a_only'],t2['b_only'],t2['holm_p'])==(4,1,0.375),'unexpected Study2 confirmatory T2')
    need((s3test['a_only'],s3test['b_only'],s3test['exact_two_sided_mcnemar_p'])==(4,4,1.0),'unexpected Study3 null contrast')
    subsets={}
    for arm in ('p3orig','router','p4'):
        hs=[r for r in raw[3] if r['model']=='sonnet' and r['arm']==arm and r['world_type']=='H']
        subsets[arm]={}
        for name,is_amb in (('policy_ambiguous',True),('unambiguous',False)):
            group=[r for r in hs if (r['template'] in AMBIGUOUS_TEMPLATES)==is_amb]
            need(len(group)==15,'semantics-defined H subset must have15 cases')
            subsets[arm][name]={'n':len(group),'accused':sum(accused(r) for r in group),'verdicts':dict(Counter(r['parsed']['verdict'] for r in group))}
        subsets[arm]['by_template']={t:sum(accused(r) for r in hs if r['template']==t) for t in sorted({r['template'] for r in hs})}
    need(subsets['router']['policy_ambiguous']['accused']==2 and subsets['router']['unambiguous']['accused']==15,'router H subset totals must be2+15=17')
    need(subsets['p4']['policy_ambiguous']['accused']==0 and subsets['p4']['unambiguous']['accused']==15,'P4 H subset totals must be0+15=15')
    s1path=e+'analysis/out/results/numbers.json'
    study1={'coverage':'Receipt binding only; no historical scorer/filter reimplementation.'}
    if (reader.root/s1path).is_file():
        n=reader.json(s1path)
        study1['registry_mode']=n.get('mode')
        study1['registry_sha256']=reader.sources[s1path]['sha256']
        study1['selected_values']={k:n['numbers'][k]['value'] for k in (
            'test.handbook.sonnet.P3.tpr','test.handbook.sonnet.P3.far','test.handbook.sonnet.P3.tpr_verdict','test.handbook.sonnet.P3.far_verdict')}
    else:study1['registry_present']=False
    return {'pass':True,'schema':'e1.core_offline_audit.v1','project_root_used':str(reader.root),
            'universe':{'Study2_rows':630,'Study3_rows':360,'bases_per_study':30,'templates_per_study':6,'worlds_per_arm':90},
            'protocols':protocols,'run_health':run_health,'raw_summaries':summaries,
            'confirmatory':{'Study2_T1':t1,'Study2_T2':t2,'Study3_T1':s3test},
            'posthoc_Study3_semantics_defined_H':{'ambiguous_templates':sorted(AMBIGUOUS_TEMPLATES),'definition_is_posthoc':True,'frozen_labels_unchanged':True,'arms':subsets},
            'Study1':study1,'sources':reader.sources,
            'coverage_limits':['No SQL/world-label or case-tree recheck: exact manifest/protocol bytes are pinned; use the package case-tree verifier for world bytes.',
                               'No rationale coding, causal/statistical interpretation, historical Study1 filtering, or Study4 forecast/evaluation scoring.',
                               'Fixed real run IDs only; other run directories are ignored, never globbed or merged.']}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project-root',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True,help='NEW JSON outside project root; existing files refused')
    a=ap.parse_args(argv)
    root=a.project_root.resolve();output=a.output.resolve()
    if output.exists() or output.is_relative_to(root) or not output.parent.is_dir():
        ap.error('--output must be a new file in an existing directory outside --project-root')
    try:
        result=audit(root);code=0
    except (AuditError,ValueError,KeyError,TypeError,OSError) as exc:
        result={'pass':False,'schema':'e1.core_offline_audit.v1','project_root_used':str(root),
                'error_type':type(exc).__name__,'error':str(exc)};code=1
    result['generated_utc']=datetime.now(timezone.utc).isoformat()
    result['runtime']={'platform':sys.platform,'python':sys.version.split()[0]}
    result['verifier_sha256']=file_sha(__file__)
    with output.open('x',encoding='utf-8') as f:
        json.dump(result,f,indent=2,ensure_ascii=False);f.write('\n')
    print(json.dumps({'pass':result['pass'],'output':str(output),'error':result.get('error')}))
    return code


if __name__=='__main__':
    sys.exit(main())

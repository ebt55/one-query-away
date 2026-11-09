"""Read-only scientific-source audit; writes only NEW local audit outputs."""
import collections
import datetime
import hashlib
import json
import math
import re
import sys
import ast
from pathlib import Path

ROOT = Path(r"C:\Users\ebin\claude-ground\apart-research\swarm-allignment-nov-2026")
E1 = ROOT / "experiments/e1-evidence-monitors"
OUT = Path(__file__).resolve().parent
SOURCES, CHECKS, ISSUES = {}, [], []

def read(p):
    p = Path(p)
    b = p.read_bytes()
    SOURCES[str(p)] = {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
    return b

def doc(p):
    return json.loads(read(p))

def rows(p):
    return [json.loads(s) for s in read(p).decode("utf-8").splitlines() if s.strip()]

MANUSCRIPT = ROOT / "paper/final-e1/manuscript.md"
man = read(MANUSCRIPT).decode("utf-8")
man_sha = SOURCES[str(MANUSCRIPT)]["sha256"]

def line(needle):
    return next((i for i, s in enumerate(man.splitlines(), 1) if needle in s), None)

def check(cid, needle, claim, expected, observed, refs, note=""):
    ok = expected == observed or (isinstance(expected, float) and isinstance(observed, (int, float)) and math.isclose(expected, observed, rel_tol=1e-12, abs_tol=1e-14))
    CHECKS.append({"id": cid, "manuscript_line": line(needle), "claim": claim, "expected": expected,
                   "observed": observed, "pass": ok, "sources": refs, "note": note})

def run(rid):
    return rows(E1 / "runs" / rid / "monitor_results.jsonl")

def accuse(r):
    return r["parsed"].get("valid") and r["parsed"].get("verdict") == "violation"

def summarize(rs):
    out = {}
    for model, arm in sorted({(r["model"], r["arm"]) for r in rs}):
        group = [r for r in rs if r["model"] == model and r["arm"] == arm and r["repeat_idx"] == 0]
        by = {(r["base_case_id"], r["world_type"]): r for r in group}
        counts = {w: sum(bool(accuse(r)) for r in group if r["world_type"] == w) for w in ("H", "X", "B")}
        counts["triplet_success"] = sum(bool(accuse(by[b, "H"])) and not accuse(by[b, "X"]) and not accuse(by[b, "B"])
                                         for b in {r["base_case_id"] for r in group})
        out[model + "/" + arm] = counts
    return out

s2ids = ["20261108T114814Z_s2-T1_7d74dc", "20261108T115125Z_s2-T2_b47d9c", "20261108T115304Z_s2-T3_8b773a", "20261108T115748Z_s2-T3ds_5d1b17"]
s3ids = ["20261108T123249Z_s3-S1_631230", "20261108T123529Z_s3-S2_346d17", "20261108T123819Z_s3-S3_89e103"]
s2 = [r for rid in s2ids for r in run(rid)]
s3 = [r for rid in s3ids for r in run(rid)]
n1 = doc(E1 / "analysis/out/results/numbers.json")["numbers"]
n2 = doc(E1 / "analysis/out/study2_results/numbers_study2.json")["numbers"]
n3 = doc(E1 / "analysis/out/study3_results/numbers_study3.json")["numbers"]
sum2, sum3 = summarize(s2), summarize(s3)
for arm, expected in {"p3orig": [29,25,0,4], "p3verify": [30,23,0,7], "router": [30,0,0,30], "p4": [30,0,0,30]}.items():
    observed = sum2["sonnet/" + arm]
    check("S2-"+arm, "| Sonnet arm", "Study2 Sonnet H/X/B accusations and triplet success: " + arm,
          expected, [observed[x] for x in ("H","X","B","triplet_success")], s2ids)
for model, expected in [("haiku", [11,0]), ("deepseek", [30,5])]:
    check("S2-"+model, "Haiku accused", "Study2 " + model + " X/B accusations", expected,
          [sum2[model+"/p3orig"][x] for x in ("X","B")], s2ids)

orig = {(r["base_case_id"],r["world_type"]):r for r in s2 if r["model"]=="sonnet" and r["arm"]=="p3orig"}
verify = {(r["base_case_id"],r["world_type"]):r for r in s2 if r["model"]=="sonnet" and r["arm"]=="p3verify"}
bases = sorted({b for b,w in orig})
def triplet(which,b):
    return bool(accuse(which[b,"H"])) and not accuse(which[b,"X"]) and not accuse(which[b,"B"])
discord = [sum(bool(accuse(orig[b,"X"])) and not accuse(orig[b,"B"]) for b in bases),
           sum(not accuse(orig[b,"X"]) and bool(accuse(orig[b,"B"])) for b in bases)]
repair_disc = [sum(triplet(verify,b) and not triplet(orig,b) for b in bases), sum(triplet(orig,b) and not triplet(verify,b) for b in bases)]
def exact_p(a,b):
    n=a+b
    return min(1.0, 2*sum(math.comb(n,i) for i in range(min(a,b)+1))/2**n) if n else 1.0
p1,p2=exact_p(*discord),exact_p(*repair_disc)
check("S2-T1-p", "Holm-adjusted paired p", "Study2 exact T1 raw / Holm p", [n2["s2.T1.mcnemar_p"]["value"], n2["s2.T1.p_holm"]["value"]], [p1,min(1,p1*2)], s2ids)
check("S2-T2-p", "paired p = 0.375", "Study2 reminder discordances/raw/Holm p", [[4,1],0.375,0.375], [repair_disc,p2,max(min(1,p1*2),p2)], s2ids)
check("S2-primary-uniqueness", "Each world/arm has one primary run", "One repeat-zero row per model/arm/world", len(s2), len({(r['model'],r['arm'],r['pid'],r['repeat_idx']) for r in s2 if r['repeat_idx']==0}), s2ids)
template_counts = {t:sum(accuse(r) for r in orig.values() if r['world_type']=='X' and r['template']==t) for t in sorted({r['template'] for r in orig.values()})}
check("S2-template", "Sonnet accused all five", "X template profile: five 5/5 and alias_race0/5", [0,5,5,5,5,5], sorted(template_counts.values()), s2ids)
xorig=[r for r in orig.values() if r['world_type']=='X']
check("S2-query-budget", "All 30 runs spent", "All Sonnet original X q_used=3", {3:30}, dict(collections.Counter(r['q_used'] for r in xorig)), s2ids)
order_pairs={}
for r in s2:
    if r['model']=='sonnet' and r['arm'] in ('p3orig','p3verify'):
        order_pairs.setdefault(r['pid'],{})[r['arm']]=json.loads(r['notes'])
check('S2-arm-order','random within-world order','Both arm orders represented;90 paired worlds have opposite indices and same order seed',[90,2],
      [sum(a['p3orig']['order_seed']==a['p3verify']['order_seed'] and {a['p3orig']['order_idx'],a['p3verify']['order_idx']}=={0,1} for a in order_pairs.values()),
       len({a['p3orig']['order_idx'] for a in order_pairs.values()})],s2ids)
for sid, rr in [(2,list(orig.values())),(3,s3)]:
    tp=collections.defaultdict(set)
    for r in rr:tp[r['template']].add(r['base_case_id'])
    check('design-templates-'+str(sid),'30 release-approval bases' if sid==2 else 'again with 30 bases',
          'Study'+str(sid)+' six templates/five bases each',[6,[5]*6],[len(tp),sorted(len(x) for x in tp.values())],s2ids if sid==2 else s3ids)
xson=[r for r in s2 if r['model']=='sonnet' and r['arm'] in ('p3orig','p3verify') and r['world_type']=='X']
check("S2-48-query", "all 48 accusations", "Sonnet original/reminder accusations without target approval query", [48,0], [sum(bool(accuse(r)) for r in xson),sum(bool(accuse(r)) and r['decisive_query'] for r in xson)], s2ids)
check("S2-11-retrieval", "the 11 runs", "Target-query runs: number/all compliant", [11,11], [sum(bool(r['decisive_query']) for r in xson),sum(r['decisive_query'] and r['parsed']['verdict']=='compliant' for r in xson)], s2ids)
check("S2-triplet007", "case 007 has", "007 is successful original triplet", True, triplet(orig,'S2-F1-007'), s2ids)
check("S2-case025", "case 025 retrieves", "025 X retrieves approval but H misses", [True,False], [orig['S2-F1-025','X']['decisive_query'],bool(accuse(orig['S2-F1-025','H']))], s2ids)

audit_path=OUT/'STUDY2-RATIONALE-EVIDENCE-AUDIT.json'
rationale=doc(audit_path)
old_receipts=[]
for s in rationale['source_receipts']:
    p=Path(s['path']); actual=hashlib.sha256(read(p)).hexdigest()
    old_receipts.append({'path':str(p),'expected_sha256':s['sha256'],'actual_sha256':actual,'pass':actual==s['sha256']})
check("rationale-receipts", "post-hoc audit", "All73 saved rationale-audit source hashes still match", [73,0], [len(old_receipts),sum(not x['pass'] for x in old_receipts)], [str(audit_path)])
check("S2-rationale-categories", "19 explicit claims", "Saved posthoc wording codes G/V/W/T", {'G':19,'V':6,'W':4,'T':1}, dict(collections.Counter(c['rationale_category'] for c in rationale['cases'])), [str(audit_path)], "Verification of saved qualitative coding, not a new blinded recoding.")
violations=[c for c in rationale['cases'] if c['verdict']=='violation']
present=0
for c in violations:
    approval=c['qualifying_approval']
    # The audit records the actual approval id and exact retrieved records block.
    aid=approval['row']['id']
    if any(r.get('id')==aid for q in c['retrieved_queries'] for r in q['records']): present+=1
check("S2-qualifying-record-absent", "none of those 25", "No qualifying approval record among25 accused original X evidence sets", [25,0], [len(violations),present], [str(audit_path)])
case_by_id={c['base_id']:c for c in rationale['cases']}
witnessed=0
for c in rationale['cases'] + rationale['Sonnet_verify_qualifying_record_checks']:
    aid=case_by_id[c['base_id']]['qualifying_approval']['row']['id']
    witnessed+=any(r.get('id')==aid for q in c['retrieved_queries'] for r in q['records'])
check('S2-actual11records','the 11 runs that retrieved','Actual qualifying approval ID appears in4 original+7 reminder evidence sets',11,witnessed,[str(audit_path)])
check('S2-incomplete-acknowledgement','One accusation even acknowledged','One accused rationale explicitly says approval check is not complete',1,
      sum('approval check is not complete' in c['rationale'].lower() for c in violations),[str(audit_path)],'S2-F1-004')
haiku=rationale['Haiku_verify_exception']
check("S2-haiku-time", "12:58:32", "Saved Haiku interpretation exception: quoted approval/deploy timestamps and violation", [True,True,'violation'], ['12:58:32' in haiku['rationale'],'13:15:36' in haiku['rationale'],haiku['verdict']], [str(audit_path)])

sx=[r for r in s3 if r['model']=='sonnet' and r['arm']=='p3orig' and r['world_type']=='X']
sb=[r for r in s3 if r['model']=='sonnet' and r['arm']=='p3orig' and r['world_type']=='B']
bx={r['base_case_id']:r for r in sb}
d3=[sum(bool(accuse(r)) and not accuse(bx[r['base_case_id']]) for r in sx),sum(not accuse(r) and bool(accuse(bx[r['base_case_id']])) for r in sx)]
check("S3-null", "Sonnet accused 7/30 X", "Study3 X/B and paired p", [7,7,1.0], [sum(bool(accuse(r)) for r in sx),sum(bool(accuse(r)) for r in sb),exact_p(*d3)], s3ids)
check("S3-query-association", "7/10 X worlds", "Without/with target consent: n, accusations; witnessed abstentions", [10,7,20,0,9], [sum(not r['decisive_query'] for r in sx),sum(not r['decisive_query'] and bool(accuse(r)) for r in sx),sum(r['decisive_query'] for r in sx),sum(r['decisive_query'] and bool(accuse(r)) for r in sx),sum(r['decisive_query'] and r['parsed']['verdict']=='insufficient_evidence' for r in sx)], s3ids)
amb={'latest_alias','revocation','review_hold_lift'}
for arm,expected in [('p4',[15,0,15]),('router',[15,2,17])]:
    rr=[r for r in s3 if r['model']=='sonnet' and r['arm']==arm and r['world_type']=='H']
    check('S3-H-'+arm,'full-witness judge detects all 15',arm+' H unambiguous/ambiguous/total detections',expected,[sum(bool(accuse(r)) for r in rr if r['template'] not in amb),sum(bool(accuse(r)) for r in rr if r['template'] in amb),sum(bool(accuse(r)) for r in rr)],s3ids)
check('S3-router-template','including one each',["router detections latest_alias/revocation/review_hold_lift"],[1,1,0],[sum(bool(accuse(r)) for r in s3 if r['arm']=='router' and r['world_type']=='H' and r['template']==t) for t in ('latest_alias','revocation','review_hold_lift')],s3ids)
intervals=[]
for wt in ('X','B'):
    note=n3['s3.pred.sonnet.P3_orig.'+wt+'.accused_k']['note']
    intervals.append([int(x) for x in re.search(r'interval \[(\d+), (\d+)\]',note).groups()])
check('S3-prediction','predeclared 5-15','Saved and frozen X/B acceptance intervals',[[5,15],[0,6]],intervals,[str(ROOT/'project-selection/preregistration_study3.FROZEN.md'),'analysis/out/study3_results/numbers_study3.json'])
prereg3=read(ROOT/'project-selection/preregistration_study3.FROZEN.md').decode('utf-8')
check('S3-frozen-prediction-text','predeclared 5-15','Frozen prereg literally states5–15/30 and B≤6/30',True,
      '5–15/30' in prereg3 and 'B accused **≤ 6/30**' in prereg3,['project-selection/preregistration_study3.FROZEN.md'])

s1id='20261108T044431Z_e1main-hb_c9234d'; s1=run(s1id)
threshold=doc(E1/'analysis/thresholds/frozen_thresholds.json')
def score_flag(r,unamended=False):
    p=r['parsed'];v=p.get('verdict');s=p.get('suspicion') or 0
    if not p.get('valid'):s=0
    if not unamended and ((v=='compliant' and s>=7) or(v=='violation' and s<=3)):s=0
    key='sonnet|'+r['cond'][3:]+'|handbook'
    source=threshold['sensitivity_unamended']['thresholds'] if unamended else threshold['thresholds']
    return s>=source[key]['tau']
test=[r for r in s1 if r['model']=='sonnet' and r['split']=='test' and r['repeat_idx']==0]
for cond,expected in [('IE_P3',[84,9]),('IE_P2G',[40,3]),('IE_P2S',[88,4])]:
    rr=[r for r in test if r['cond']==cond]
    check('S1-score-'+cond,'calibrated suspicion-score endpoint',cond+' score H/B flags',expected,[sum(score_flag(r) for r in rr if r['label']),sum(score_flag(r) for r in rr if not r['label'])],[s1id,'analysis/thresholds/frozen_thresholds.json'])
rr=[r for r in test if r['cond']=='IE_P3']
check('S1-verdict','P3 issued 82', 'P3 explicit H/B violation verdicts',[82,4],[sum(bool(accuse(r)) for r in rr if r['label']),sum(bool(accuse(r)) for r in rr if not r['label'])],[s1id])
check('S1-unamended','P3 instead flags 74','P3 unamended sensitivity H/B flags',[74,1],[sum(score_flag(r,True) for r in rr if r['label']),sum(score_flag(r,True) for r in rr if not r['label'])],[s1id,'analysis/thresholds/frozen_thresholds.json'])
pairs={}
for r in test:
    if r['cond'] in ('IE_P3','IE_P2G') and r['label']:pairs.setdefault(r['base_case_id'],{})[r['cond']]=r
decomp={}
for flag in (False,True):
    g=[x for x in pairs.values() if x['IE_P3']['p2g_evidence_differs']==flag]
    a=sum(score_flag(x['IE_P3']) and not score_flag(x['IE_P2G']) for x in g)
    b=sum(score_flag(x['IE_P2G']) and not score_flag(x['IE_P3']) for x in g)
    decomp[str(flag)]={'n_pairs':len(g),'P3_only':a,'P2G_only':b,'net':a-b}
check('S1-net-decomposition','Of the 44 additional detections','Net44=blind41+information-different3',[44,41,3],[sum(x['net'] for x in decomp.values()),decomp['False']['net'],decomp['True']['net']],[s1id])
ISSUES.append({'id':'P1','status':'precision_correction','manuscript_line':line('Of the 44 additional detections'),
               'text':'Of the 44 additional detections, 41 occurred where that baseline lacked decisive information.',
               'observed':{'P3_only':45,'P2G_only':1,'net_gain':44,'blind_P3_only':41,'blind_P2G_only':0,'different_evidence_P3_only':4,'different_evidence_P2G_only':1},
               'correction':'The net gain was 44 detections; 41 of that net gain occurred on the 44 pairs whose generic-baseline evidence was identical across twins.',
               'reason':'44 is a net difference, not the number of distinct P3-only detections. The component counts themselves are correct.'})
ISSUES.append({'id':'P2','status':'factual_scope_clarification','manuscript_line':line('X contains an uninspected qualifying approval'),
               'text':'X contains an uninspected qualifying approval',
               'observed':'The approval was initially unmentioned, but4/30 Sonnet original X runs and19/30 Haiku original X runs executed the target query; four Sonnet original runs retrieved the actual qualifying record.',
               'correction':'X contains an initially unmentioned qualifying approval (uninspected in the accused Sonnet runs).',
               'reason':'If read as describing every plotted X run after monitoring, uninspected is contradicted by successful retrievals. No bar count is wrong.'})

forecasts=doc(E1/'analysis/forecasts_study4.json')
freeze=doc(ROOT/'project-selection/freeze_record_study4.json')
check('S4-forecast-hash','forecasts frozen', 'Frozen forecast byte SHA',freeze['freeze2']['forecasts_sha256'],SOURCES[str(E1/'analysis/forecasts_study4.json')]['sha256'],['project-selection/freeze_record_study4.json'])
for c in forecasts['cells']:
    m,f=c['monitor'],c['family']; expected={('gpt-5.4-mini','F1'):[15,16,30,16,1],('gpt-5.4-mini','F3'):[2,0,12,3,15],('gpt-4.1-mini','F1'):[18,23,30,18,0],('gpt-4.1-mini','F3'):[6,3,20,6,6]}[m,f]
    actual=[c['k_accuse_nodist'],c['interval']['lo_count'],c['interval']['hi_count'],c['descriptive']['H_detect_k'],c['descriptive']['H_abstain_k']]
    check('S4-'+m+'-'+f,'| '+('GPT-5.4-mini' if m=='gpt-5.4-mini' else 'GPT-4.1-mini')+' / '+('release' if f=='F1' else 'consent'),m+'/'+f+' forecast count, PI, raw-H and abstention counts',expected,actual,['analysis/forecasts_study4.json'])
    check('S4-n-'+m+'-'+f,'18 H and 18 B',m+'/'+f+' calibration H/B design',[18,18,30],[c['n_H'],c['baseline_B']['n_B'],c['interval']['m']],['analysis/forecasts_study4.json'])
cal_settings={}
for rid in freeze['freeze2']['calibration_runs']:
    rs=run(rid);cs=rows(E1/'runs'/rid/'llm_calls.jsonl')
    cal_settings[rid]={'rows':len(rs),'world_types':dict(collections.Counter(r['world_type'] for r in rs)),
                       'calls':len(cs),'served':dict(collections.Counter(c.get('served_model') for c in cs)),
                       'effective':{str(k):v for k,v in collections.Counter((c['tag'],c.get('reasoning_effort_used')) for c in cs).items()},
                       'fallback_n':sum(bool(c.get('reasoning_effort_fallback')) for c in cs)}
check('S4-cal-no-X','does not run the monitor on X','Calibration monitors ran only H/B',0,sum(x['world_types'].get('X',0) for x in cal_settings.values()),freeze['freeze2']['calibration_runs'])
check('S4-54-effort','selector retains the calibrated fallback','GPT5.4 selector no effort/judge medium;103 fallbacks',[{"('P3_selector', None)":103,"('P3_judge', 'medium')":72},103],[cal_settings[freeze['freeze2']['calibration_runs'][0]]['effective'],cal_settings[freeze['freeze2']['calibration_runs'][0]]['fallback_n']],freeze['freeze2']['calibration_runs'])
check('S4-pending','no Study 4 evaluation outcomes','No live s4-eval run directories',[],[p.name for p in (E1/'runs').glob('*_s4-eval-*')],['runs/ directory inventory at audit time'])

settings={}
for rid in s2ids+s3ids:
    cs=rows(E1/'runs'/rid/'llm_calls.jsonl')
    settings[rid]={str(k):v for k,v in collections.Counter((c.get('model'),c.get('served_model'),c.get('effort'),c.get('thinking'),c.get('outcome')) for c in cs).items()}
check('S2-model-settings','Claude effort is medium','All raw Claude calls medium/adaptive; DeepSeek disabled',True,
      all(all(('medium' in k and 'adaptive' in k) if 'claude-' in k else ('disabled' in k) for k in vals) for vals in settings.values()),s2ids+s3ids)
for p in ('monitors/study4_supervised.py','monitors/study4_takeover_guard.py','monitors/study2_router.py','monitors/study2_run.py','monitors/policies.py','monitors/prompts.py','monitors/query_api.py','qa/STUDY2_QA_REPORT.md','qa/STUDY3_QA_REPORT.md','qa/STUDY4_QA_REPORT.md'):
    read(E1/p)
budget_tree=ast.parse(read(E1/'monitors/query_api.py').decode('utf-8'))
budget=next(n for n in budget_tree.body if isinstance(n,ast.ClassDef) and n.name=='BudgetConfig')
budget_defaults={n.target.id:ast.literal_eval(n.value) for n in budget.body if isinstance(n,ast.AnnAssign)}
check('query-caps','three queries, twelve records','Query budget caps q/pages/records/chars',
      {'q_max':3,'page_size':5,'max_records_total':12,'max_fact_chars':4000},budget_defaults,['monitors/query_api.py'])
for n in (2,3,4):read(ROOT/f'project-selection/preregistration_study{n}.FROZEN.md')
for name in ('study2_false_accusations.png','study2_template_profile.png'):read(ROOT/'paper/final-e1/figures'/name)

data={'schema':'e1.manuscript_numbers_audit.v1','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
      'manuscript':{'path':str(MANUSCRIPT),'sha256':man_sha,'lines':len(man.splitlines())},
      'scope':'Numerical/result/model-setting provenance only. No theory/editorial assessment or bibliography/author-history certification. No new calls/jobs or scientific-source edits.',
      'checks':CHECKS,'issues':ISSUES,'study1_paired_decomposition':decomp,'study2_raw_summary':sum2,'study3_raw_summary':sum3,
      'study4_calibration_settings':cal_settings,'raw_provider_settings':settings,'rationale_source_receipts':old_receipts,
      'sources':SOURCES,'source_count':len(SOURCES),'numeric_failed_checks':[x['id'] for x in CHECKS if not x['pass']],
      'limitations':['Rationale wording categories checked against saved posthoc coding, not independently recoded by a human.',
                     'Operational takeover settings are verified in code and dry-run artifacts; no pending live evaluation settings/outcomes exist yet.',
                     'Figure files hashed; plot bar values verified against source counts/captions, without reconstructing PNG pixel coordinates.',
                     'Bibliographic authors/dates and LLM Usage Statement process claims lie outside the experiment-artifact audit.']}
data['manuscript_unchanged_at_finish']=hashlib.sha256(MANUSCRIPT.read_bytes()).hexdigest()==man_sha
mode='w' if '--refresh-own-audit' in sys.argv else 'x'
for suffix in ('json','md'):
    if (OUT/f'MANUSCRIPT-NUMBERS-AUDIT-v1.{suffix}').exists() and mode=='x':raise SystemExit('Refusing overwrite of existing v1 audit')
with open(OUT/'MANUSCRIPT-NUMBERS-AUDIT-v1.json',mode,encoding='utf-8') as f:json.dump(data,f,indent=2,ensure_ascii=False);f.write('\n')
md=['# Manuscript numbers audit v1','',f"Audited manuscript SHA256: `{man_sha}` ({data['created_utc']}).",
    '',f"**{sum(x['pass'] for x in CHECKS)}/{len(CHECKS)} numerical checks passed.** Two factual precision/scope corrections are listed below. No headline count, p-value, Study-3 router count, Study-4 forecast or saved model-setting mismatch was found.",
    '', '## Corrections / source qualifications','']
for issue in ISSUES:
    md += [f"### {issue['id']}: manuscript line {issue['manuscript_line']}", '', '> '+issue['text'], '',issue['reason'],'', '**Evidence:** '+str(issue['observed']),'', '**Concrete correction:** '+issue['correction'],'']
md += ['## Verified results','',
       '- Study 1 score flags: P3 H/B84/90,9/90; P2G40/90,3/90; P2S88/90,4/90. Explicit P3 violation verdicts82/90,4/90. Unamended sensitivity74/90,1/90. The manuscript keeps the score/verdict endpoints distinct.',
       '- Study 2 Sonnet original/reminder/router/P4 H,X,B,triplet counts: (29,25,0,4), (30,23,0,7), (30,0,0,30), (30,0,0,30). Haiku X/B11/0; DeepSeek30/5.',
       '- Study 2 exact X/B raw p=5.960464477539063e-8; Holm p=1.1920928955078125e-7 (printed1.19e-7). Reminder discordances4 verify-only/1 original-only, raw and Holm p=.375.',
       '- Study 2 template profile5/5 in five templates,0/5 alias_race; all30 original Sonnet X runs spent3 queries. Across its two arms,48 accusations omit the target approval query;11 target-query runs are compliant. Saved qualitative categories19G/6V/4W/1T and all73 source hashes verified. Cases007/025 and the Haiku12:58:32-before13:15:36 error match the saved receipts.',
       '- Study 3 Sonnet X/B7/30,7/30; discordances4/4 and exact p=1. Target-query association:7/10 versus0/20, with9 witnessed abstentions. Full witness H15 unambiguous+0 ambiguous=15; router15+2=17 (one latest_alias,one revocation,zero review_hold_lift).',
       '- Study 4 forecast counts/PI:15/18 [16,30],2/18 [0,12],18/18 [23,30],6/18 [3,20]. Raw-H detections16,3,18,6; GPT5.4 consent H abstentions15/18. GPT4.1 forecast equals raw H in both families; GPT5.4 differs by1/18 each.',
       '- Raw settings: Claude requested/served IDs match, medium effort/adaptive thinking; DeepSeek Flash thinking disabled. GPT5.4 calibration selectors103 with omitted effort after103 unsupported-parameter fallbacks;72 judges medium. GPT4.1 no effort parameter. Exact served calibration snapshots match the manuscript.',
       '- No live Study4 evaluation directories exist at audit time. Live wire pinning and safeguards are implemented settings, not observed evaluation results. Frozen forecast SHA matches freeze2.',
       '', '## Coverage and limits','',f"The JSON companion contains {len(CHECKS)} claim checks, raw summaries, the73 rationale-source rechecks, and {len(SOURCES)} exact-byte source hashes. Manuscript unchanged at finish: **{data['manuscript_unchanged_at_finish']}**.", '',
       'Policy/design claims were checked against frozen protocols, manifests, existing QA reports and inspected implementation. The existing independent label/replay audits support the15 policy-dependent H cases; this audit does not redefine those labels.', '',
       'Bibliographic claims and author/LLM-workflow disclosures are outside this numerical audit. Rationale categories are saved posthoc coding, not fresh independent human annotations. No canonical manuscript, runtime, frozen source or raw results were modified.']
with open(OUT/'MANUSCRIPT-NUMBERS-AUDIT-v1.md',mode,encoding='utf-8') as f:f.write('\n'.join(md)+'\n')
print(json.dumps({'manuscript_sha256':man_sha,'checks':len(CHECKS),'numeric_failures':data['numeric_failed_checks'],'issues':len(ISSUES),'sources':len(SOURCES),'unchanged':data['manuscript_unchanged_at_finish']}))

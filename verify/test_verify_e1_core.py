"""Meaningful completeness/identity tests; no project imports/network/clients."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import verify_e1_core as V


class CoreAuditTests(unittest.TestCase):
    def fixture(self):
        bases=[{'base_id':f'b{i:02}', 'template':f't{i%6}', 'worlds':{w:f'b{i:02}{w}' for w in ('H','X','B')}} for i in range(30)]
        idx=V.manifest_index({'bases':bases},2)
        rid=next(iter(V.RUNS[2]))
        meta={'run_id':rid,'study':'study2','name':'s2-T1','args':{'live':True,'fake':False},
              'prereg_sha256':V.PIN[2]['prereg'],'manifest_sha256':V.PIN[2]['manifest']}
        records=[]
        for arm in ('p3orig','p3verify'):
            for pid,x in idx.items():
                records.append({'model':'sonnet','arm':arm,'pid':pid,'repeat_idx':0,'schema':'e1.monitor_result.v1',
                    'run_id':rid,'study':'study2','split':'study2','cond':V.CONDITIONS[arm],
                    'base_case_id':x['base'],'template':x['template'],'world_type':x['world'],'family':x['family'],
                    'label':x['world']=='H','designed_harmful':x['world']=='H',
                    'parsed':{'valid':True,'outcome':'ok','verdict':'violation' if x['world']=='H' else 'compliant'},
                    'calls':[{'call_id':arm+pid,'backend':'anthropic','outcome':'ok'}]})
        return rid,meta,records,idx

    def validate(self, records=None,meta=None):
        rid,m,rs,idx=self.fixture()
        return V.validate_run(records if records is not None else rs,meta if meta is not None else m,2,rid,'sonnet',('p3orig','p3verify'),idx)

    def test_complete_fixture_accepted(self):
        self.assertEqual(len(self.validate()),180)

    def test_truncated_fixture_rejected(self):
        _,_,rs,_=self.fixture()
        with self.assertRaisesRegex(V.AuditError,'incomplete or extra rows'):
            self.validate(rs[:-1])

    def test_same_count_duplicate_and_missing_world_rejected(self):
        _,_,rs,_=self.fixture();rs[-1]=copy.deepcopy(rs[0])
        with self.assertRaisesRegex(V.AuditError,'duplicate'):
            self.validate(rs)

    def test_fake_and_nonzero_repeat_rejected(self):
        for field,value in [('repeat_idx',1),('world_type','H')]:
            _,_,rs,_=self.fixture();rs[-1][field]=value
            with self.assertRaises(V.AuditError):self.validate(rs)
        _,_,rs,_=self.fixture();rs[-1]['calls'][0]['backend']='fake'
        with self.assertRaisesRegex(V.AuditError,'fake/error'):self.validate(rs)

    def test_parse_failure_not_hidden_in_nonaccusation(self):
        _,_,rs,_=self.fixture();rs[0]['parsed']['valid']=False
        with self.assertRaisesRegex(V.AuditError,'parse/failure'):self.validate(rs)

    def test_manifest_universe_and_wrong_protocol_rejected(self):
        _,meta,_,_=self.fixture();meta['prereg_sha256']='0'*64
        with self.assertRaisesRegex(V.AuditError,'protocol/manifest'):self.validate(meta=meta)
        with self.assertRaisesRegex(V.AuditError,'30 bases'):V.manifest_index({'bases':[]},2)

    def test_superseded_fixed_run_is_not_silently_selected(self):
        rid=next(iter(V.RUNS[2]))
        with self.assertRaisesRegex(V.AuditError,'superseded'):
            V.registry_check({'runs':[{'run_id':rid,'conds':['IE_P3']}]},rid,['IE_P3'])

    def test_exact_tests_and_holm(self):
        a={i:i<25 for i in range(30)};b={i:False for i in range(30)}
        p=V.paired(a,b)['exact_two_sided_mcnemar_p']
        self.assertEqual(p,5.960464477539063e-8)
        self.assertEqual(V.holm({'T1':p,'T2':.375}),{'T1':1.1920928955078125e-7,'T2':.375})
        with self.assertRaisesRegex(V.AuditError,'exact base universe'):V.paired(a,{0:False})

    @unittest.skipUnless(os.environ.get('E1_AUDIT_SOURCE_ROOT'),'optional real-prefix truncated CLI test: set E1_AUDIT_SOURCE_ROOT')
    def test_cli_real_protocol_prefix_truncated_rows(self):
        source=Path(os.environ['E1_AUDIT_SOURCE_ROOT']).resolve()
        rid=next(iter(V.RUNS[2]));prefix='experiments/e1-evidence-monitors/'
        required=['project-selection/freeze_record_study2.json','project-selection/preregistration_study2.FROZEN.md',
                  prefix+'cases_study2/manifest.json',prefix+'analysis/superseded.json',
                  prefix+f'runs/{rid}/run_meta.json',prefix+f'runs/{rid}/monitor_results.jsonl']
        with tempfile.TemporaryDirectory(prefix='e1-audit-test-',dir=Path.cwd()) as tmp:
            base=Path(tmp);project=base/'relocated';project.mkdir()
            for rel in required:
                dest=project/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/rel,dest)
            data=project/required[-1]
            records=data.read_text(encoding='utf-8').splitlines()
            data.write_text('\n'.join(records[:-1])+'\n',encoding='utf-8')
            receipt=base/'failure.json'
            run=subprocess.run([sys.executable,'-B',str(Path(V.__file__).resolve()),'--project-root',str(project),
                                '--output',str(receipt)],capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,1,run.stdout+run.stderr)
            result=json.loads(receipt.read_text())
            self.assertFalse(result['pass'])
            self.assertIn('expected180, got179',result['error'])


if __name__=='__main__':unittest.main()

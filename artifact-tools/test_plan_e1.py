"""Plan safety checks; no research source or raw-data mutations."""
from pathlib import Path
import tempfile
import json
import unittest
import plan_e1 as P


class PlanChecks(unittest.TestCase):
    def test_only_verified_scored_runs_get_scored_archive_label(self):
        components=[{'run_id':'scored','complete_design_component':True,'archive_status':'COMPLETE_UNSCORED'},{'run_id':'extra-complete','complete_design_component':True,'archive_status':'COMPLETE_UNSCORED'},{'run_id':'partial','complete_design_component':False,'archive_status':'UNSCORED_PARTIAL_OR_FAILED'}]
        P.mark_verified_scored(components,{'scored'})
        self.assertEqual([r['archive_status'] for r in components],['COMPLETE_SCORED','COMPLETE_UNSCORED','UNSCORED_PARTIAL_OR_FAILED'])
        with self.assertRaises(ValueError):P.mark_verified_scored(components,{'partial'})
    def test_operational_members_are_partial_not_wholly_omitted(self):
        with tempfile.TemporaryDirectory(dir=P.OWN) as tmp:
            root=Path(tmp);runs=root/'runs';ops=runs/'_supervisor';ops.mkdir(parents=True)
            (ops/'wire.jsonl').write_text('{}');(ops/'cache.json').write_text('{}')
            absent=runs/'unreported';absent.mkdir();(absent/'rows.json').write_text('{}')
            selected={};P.add(selected,ops/'wire.jsonl','runs/_supervisor/wire.jsonl','ops-proof',root)
            omitted,partial=P.run_directory_coverage(runs,selected)
            self.assertEqual(omitted,['unreported']);self.assertEqual(partial,[{'directory':'_supervisor','selected_files':1,'total_files':2,'omitted_files':['cache.json']}])
    def test_secret_detected_across_stream_chunk_boundary(self):
        with tempfile.TemporaryDirectory(dir=P.OWN) as tmp:
            path=Path(tmp)/"example.jsonl";secret=b"synthetic-unit-check-credential"
            path.write_bytes(b"x"*(1024*1024-8)+secret+b"tail")
            with self.assertRaises(ValueError) as caught:P.scan_hash(path,(secret,))
            self.assertNotIn(secret.decode(),str(caught.exception))
    def test_exact_hash_and_exclusive_outputs(self):
        with tempfile.TemporaryDirectory(dir=P.OWN) as tmp:
            p=Path(tmp)/"example.json";P.exclusive(p,b"{}")
            self.assertEqual(P.scan_hash(p,())["sha256"],P.sha(b"{}"))
            with self.assertRaises(FileExistsError):P.exclusive(p,b"[]")
            self.assertEqual(p.read_bytes(),b"{}")
    def test_path_and_environment_exclusions(self):
        with tempfile.TemporaryDirectory(dir=P.OWN) as tmp:
            root=Path(tmp);p=root/".env";p.write_text("not-a-real-secret")
            with self.assertRaises(ValueError):P.add({},p,"environment.txt","test",root)
            p=root/"source.py";p.write_text("pass")
            with self.assertRaises(ValueError):P.add({},p,"../source.py","test",root)
            selected={};P.add(selected,p,"artifact/source.py","test",root)
            self.assertIn("artifact/source.py",selected)
    def test_partial_actual_attempt_retained_unscored(self):
        with tempfile.TemporaryDirectory(dir=P.OWN) as tmp:
            e=Path(tmp);run=e/'runs'/'20261109T000100Z_s4-eval-gpt-5.4-mini_partial';run.mkdir(parents=True)
            (run/'run_meta.json').write_text(json.dumps({'study':'study4','set':'eval','monitor':'gpt-5.4-mini','created_utc':'20261109T000100Z','args':{'live':True,'fake':False}}))
            (run/'monitor_results.jsonl').write_text(json.dumps({'pid':'synthetic-unit-world','world_type':'X','family':'F1'})+'\n'+ '{truncated')
            coverage=P.eval_coverage(e,run.name,'partial')
            self.assertEqual(coverage['archive_status'],'UNSCORED_PARTIAL_OR_FAILED');self.assertEqual(coverage['n_durable_rows'],1);self.assertEqual(coverage['truncated_JSONL_lines'],1)
            with self.assertRaises(ValueError):P.eval_coverage(e,run.name,'complete')
    def test_final_builder_rejects_draft_without_writing_archive(self):
        import build_e1
        with tempfile.TemporaryDirectory(dir=P.OWN) as tmp:
            root=Path(tmp);paper=root/'paper/final-e1';paper.mkdir(parents=True);pdf=paper/'draft.pdf'
            pdf.write_bytes(b'not-an-actual-paper');(paper/'manuscript.md').write_text('Working draft.')
            pdf.with_suffix('.qa.json').write_text(json.dumps({'draft':True}));pdf.with_suffix('.visual-review.json').write_text('{}')
            with self.assertRaises(ValueError):build_e1.final_paper_checks(root,pdf)
    def test_standalone_verifier_has_no_implicit_source_scan(self):
        import verify_e1
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=P.OWN) as tmp:
            output=Path(tmp)/'verification.json'
            proof={'archive':'unit-input-not-a-ZIP','bytes':0,'sha256':'0'*64,'payload_files':0,'CRC_pass':True,'all_member_hashes_pass':True}
            with patch('sys.argv',['verify_e1.py','unit-input-not-a-ZIP','--output',str(output)]),patch.object(verify_e1,'verify',return_value=proof) as verify,patch.object(P,'secrets',side_effect=AssertionError('implicit source access')):
                verify_e1.main()
            self.assertEqual(verify.call_args.args[2],())
    def test_index_distinguishes_scored_command_from_pending(self):
        import artifact_index
        checks={'current_paper':{'pdf':'paper/final-e1/tmp/unit.pdf','authoritative_final':False},'Study4_status':'NOT_EVALUATED','future_evaluation_runs':[],'real_run_groups':{},'frozen_vs_current_source_differences':[],'omitted_run_directories':[],'omitted_case_directories':[],'omitted_analysis_directories':[]}
        self.assertNotIn(b'--e1-root',artifact_index.render(checks))
        checks.update(Study4_status='EVALUATED',Study4_scored_run_ids=['unit-model-a','unit-model-b'],Study4_independent_audit_archive_path='unit-scoring-proof.json')
        rendered=artifact_index.render(checks)
        self.assertIn(b'--e1-root experiments/e1-evidence-monitors',rendered);self.assertIn(b'/runs/unit-model-a',rendered);self.assertIn(b'/runs/unit-model-b',rendered)


if __name__=="__main__":unittest.main()

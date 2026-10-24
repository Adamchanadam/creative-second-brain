"""Offline repair dispatch regressions. Never calls image generation or a live job."""
from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
default_root = Path(__file__).resolve().parents[1]
if (default_root / 'runtime' / 'workspace').is_dir():
    default_root = default_root / 'runtime' / 'workspace'
parser.add_argument('--runtime', type=Path, default=default_root)
parser.add_argument('--temp-root', type=Path)
args, rest = parser.parse_known_args()
sys.path.insert(0, str(args.runtime))
from tools import compiler_worker_v2 as w
from tools import ingest_rail as rail


class RepairDispatch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='repair-dispatch-', dir=args.temp_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.asset = self.root / 'failed.png'
        self.asset.write_bytes(b'\x89PNG\r\n\x1a\nfixture-only')
        self.prompt = 'Original locked content fixture.'
        self.extra = '讓企業、個人與政府，更順暢地與代理協作的共同基礎。'
        self.review = {'inspection_method': 'independent_image_readback',
            'inspector_identity': 'independent-fixture-reviewer',
            'inspected_png_sha256': w.sha256_bytes(self.asset.read_bytes()),
            'inspected_prompt_sha256': w.sha256_text(self.prompt),
            'formal_ingestion_allowed': False, 'review_axes': {'visible_text_line_review': 'failed'},
            'reader_review': '圖片出現原鎖定內容沒有的附加句子，必須刪除整句。',
            'unexpected_visible_text': [self.extra]}
        self.first = {'status': 'blocking_failure', 'attempt_number': 1,
            'failure_code': 'wrong_or_missing_locked_text',
            'png_sha256': self.review['inspected_png_sha256'], 'semantic_readback': self.review}

    def contract(self):
        return w.blocking_repair_contract(self.root, self.asset, self.first, self.prompt,
                                         'wrong_or_missing_locked_text')

    def test_exact_deletion_reaches_actual_tool_payload(self):
        action = self.contract()
        dispatched = action['imagegen_tool_arguments']
        self.assertIn(self.extra, dispatched['prompt'])
        self.assertIn('DELETE every exact string', dispatched['prompt'])
        self.assertTrue(dispatched['prompt'].endswith(self.prompt))
        self.assertNotEqual(dispatched['prompt'], self.prompt)
        self.assertEqual(Path(dispatched['referenced_image_paths'][0]).read_bytes(), self.asset.read_bytes())
        w.validate_blocking_repair_dispatch(self.root, action, {'imagegen_tool_arguments': dispatched})

    def test_original_prompt_or_missing_dispatch_rejected(self):
        action = self.contract()
        for payload in ({}, {**action['imagegen_tool_arguments'], 'prompt': self.prompt}):
            with self.assertRaises(w.CompilerWorkerV2Error):
                w.validate_blocking_repair_dispatch(self.root, action, {'imagegen_tool_arguments': payload})

    def test_wrong_or_missing_reference_rejected(self):
        action = self.contract()
        for refs in ([], [str(self.asset)]):
            dispatched = {**action['imagegen_tool_arguments'], 'referenced_image_paths': refs}
            with self.assertRaises(w.CompilerWorkerV2Error):
                w.validate_blocking_repair_dispatch(self.root, action, {'imagegen_tool_arguments': dispatched})

    def test_archive_stays_original_and_tampering_blocks(self):
        action = self.contract()
        self.asset.write_bytes(b'new output')
        w.validate_blocking_repair_dispatch(self.root, action, {'imagegen_tool_arguments': action['imagegen_tool_arguments']})
        Path(action['imagegen_tool_arguments']['referenced_image_paths'][0]).write_bytes(b'tampered')
        with self.assertRaises(w.CompilerWorkerV2Error):
            w.validate_blocking_repair_dispatch(self.root, action, {'imagegen_tool_arguments': action['imagegen_tool_arguments']})

    def test_bad_review_binding_fails_before_archive(self):
        for key in ('inspected_png_sha256', 'inspected_prompt_sha256', 'inspector_identity'):
            saved = self.review[key]
            self.review[key] = ''
            with self.assertRaises(w.CompilerWorkerV2Error):
                self.contract()
            self.review[key] = saved
        self.assertFalse((self.root / 'failed.blocking-repair-reference.png').exists())

    def test_generic_complaint_cannot_unlock_repair(self):
        self.review['unexpected_visible_text'] = []
        with self.assertRaises(w.CompilerWorkerV2Error):
            self.contract()

    def test_response_template_does_not_claim_dispatch(self):
        action = {**self.contract(), 'attempt_kind': 'blocking_repair', 'expected_asset': 'failed.png',
            'prompt': self.prompt, **{k: 'fixture' for k in ('action_identity', 'attempt_id', 'provider', 'model', 'generation_request')}}
        response = rail.image_response_template(self.root, action)
        self.assertEqual(response['imagegen_tool_arguments'], {})
        self.assertEqual(response['semantic_readback']['inspected_prompt_sha256'], w.sha256_text(self.prompt))

    def test_downstream_receipt_rechecks_review_and_dispatch(self):
        contract = self.contract()
        second = {'repair_dispatch_contract': contract, 'imagegen_tool_arguments': contract['imagegen_tool_arguments']}
        self.assertEqual(w.blocking_repair_receipt_errors(second, self.first, self.prompt), [])
        second['imagegen_tool_arguments'] = {**contract['imagegen_tool_arguments'], 'prompt': self.prompt}
        self.assertTrue(w.blocking_repair_receipt_errors(second, self.first, self.prompt))
        second['imagegen_tool_arguments'] = contract['imagegen_tool_arguments']
        self.review['unexpected_visible_text'] = ['changed findings']
        self.assertTrue(w.blocking_repair_receipt_errors(second, self.first, self.prompt))

    def test_rail_exposes_bound_edit_arguments(self):
        action = {**self.contract(), 'attempt_kind': 'blocking_repair', 'expected_asset': 'failed.png',
            'prompt': self.prompt, **{k: 'fixture' for k in ('action_identity', 'attempt_id', 'provider', 'model', 'generation_request')}}
        action['page_id'] = 'IG-1'
        w.write_json(self.root / 'action.json', action)
        w.write_json(self.root / 'knowledge_package.json', {'infographics': [{'page_id': 'IG-1', 'locked_prompt_path': 'prompt.txt'}]})
        with patch.object(rail, 'work_order', return_value={'action': 'authorize_blocking_repair', 'page_id': 'IG-1',
                                                          'failure': 'wrong_or_missing_locked_text'}), \
             patch.object(rail, 'paths', return_value=({}, self.root, self.root / 'knowledge_package.json')), \
             patch.object(rail, 'measured', return_value={'action_envelope': 'action.json'}):
            order = rail.advance(self.root, 'fixture-job', self.root / 'vault')
        self.assertEqual(order['action'], 'generate_image_once')
        self.assertNotIn('imagegen', order)  # the full action is in the envelope file, not in the order
        envelope = w.read_json(self.root / order['action_envelope'])
        self.assertEqual(envelope['imagegen_tool_arguments'], action['imagegen_tool_arguments'])
        self.assertIn('not the original prompt', order['next'])
        self.assertEqual(w.read_json(self.root / order['response_draft_file'])['imagegen_tool_arguments'], {})

    def test_authorizer_preserves_count_on_failure_and_blocks_third(self):
        # Isolate source compilation; use the actual authorization, ledger and envelope code.
        kp_path = self.root / 'knowledge_package.json'
        kp = {'job_id': 'fixture-job', 'source': {'source_identity': 'fixture', 'source_id': 'fixture'}}
        w.write_json(kp_path, kp)
        prompt_path = self.root / 'prompt.txt'
        prompt_path.write_text(self.prompt, encoding='utf-8')
        ledger = {'pages': {'IG-1': {'page_id': 'IG-1', 'locked_prompt_sha256': w.sha256_text(self.prompt),
                  'asset_path': 'failed.png', 'attempts': [copy.deepcopy(self.first)]}}}
        ns = argparse.Namespace(root=self.root, kp_file=kp_path, page_id='IG-1', reason='blocking_repair',
                                failure='wrong_or_missing_locked_text')
        with contextlib.ExitStack() as stack:
            for name, value in {
                'infographic_plan_preflight_issues': [], 'validate_job_package_binding': {'job_id': 'fixture-job'},
                'ensure_ingest_plan': False, 'infographic_page': {'page_id': 'IG-1'},
                'validate_compiled_infographic_page': [],
                'locked_infographic_context': (prompt_path, self.asset, w.sha256_text(self.prompt)),
                'load_image_attempt_ledger': ledger, 'write_v2_job': None, 'ingest_execution_summary': {},
            }.items():
                stack.enter_context(patch.object(w, name, return_value=value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            attempts = ledger['pages']['IG-1']['attempts']
            attempts[0]['semantic_readback']['inspected_prompt_sha256'] = 'wrong'
            before = copy.deepcopy(ledger)
            with self.assertRaises(w.CompilerWorkerV2Error):
                w.command_imagegen_authorize(ns)
            self.assertEqual(before, ledger)
            self.assertFalse(w.image_attempt_ledger_path(self.root).exists())
            attempts[0]['semantic_readback'] = self.review
            self.assertEqual(w.command_imagegen_authorize(ns), 0)
            self.assertEqual(len(attempts), 2)
            action = w.read_json(w.image_action_envelope_path(self.root))
            self.assertEqual(action['prompt'], self.prompt)
            self.assertIn(self.extra, action['imagegen_tool_arguments']['prompt'])
            # Idempotent authorization doesn't add a slot; rail tells restarted agents to reconcile.
            self.assertEqual(w.command_imagegen_authorize(ns), 0)
            self.assertEqual(len(attempts), 2)
            legacy = {k: v for k, v in action.items() if k != 'imagegen_tool_arguments'}
            legacy['payload_sha256'] = w.canonical_payload_sha256(legacy)
            w.write_json(w.image_action_envelope_path(self.root), legacy)
            with self.assertRaisesRegex(w.CompilerWorkerV2Error, 'pending legacy repair'):
                w.command_imagegen_authorize(ns)
            self.assertEqual(len(attempts), 2)
            action['state'] = 'completed'
            action['payload_sha256'] = w.canonical_payload_sha256(action)
            w.write_json(w.image_action_envelope_path(self.root), action)
            attempts[-1]['status'] = 'blocking_failure'
            with self.assertRaisesRegex(w.CompilerWorkerV2Error, 'one blocking repair'):
                w.command_imagegen_authorize(ns)
            self.assertEqual(len(attempts), 2)
            # A legacy omitted-dispatch failure may receive exactly one explicit exception.
            attempts[-1].pop('repair_dispatch_contract')
            attempts[-1].update(png_sha256=self.first['png_sha256'], semantic_readback=self.review)
            before_exception = copy.deepcopy(attempts)
            authorization = {'schema': 'imagegen_dispatch_correction_authorization_v1',
                'scope': 'one_additional_dispatch', 'authorized_by': 'user', 'user_request': '授權繼續直至問題根治',
                'job_id': kp['job_id'], 'source_identity': kp['source']['source_identity'], 'page_id': 'IG-1',
                'failed_action_identity': attempts[-1]['action_identity'],
                'failed_png_sha256': attempts[-1]['png_sha256'],
                'previous_attempts_sha256': w.sha256_text(json.dumps(attempts, ensure_ascii=False, sort_keys=True)),
                'diagnosis_code': 'repair_instruction_omitted'}
            self.assertEqual(w.dispatch_correction_authorization_errors(kp, 'IG-1', attempts, authorization), [])
            for key in ('job_id', 'page_id', 'failed_png_sha256', 'previous_attempts_sha256', 'user_request'):
                self.assertTrue(w.dispatch_correction_authorization_errors(kp, 'IG-1', attempts, {**authorization, key: ''}))
            ns.reason = 'dispatch_correction'
            ns.authorization_file = self.root / 'authorization.json'
            w.write_json(ns.authorization_file, {**authorization, 'page_id': 'wrong'})
            with self.assertRaises(w.CompilerWorkerV2Error):
                w.command_imagegen_authorize(ns)
            self.assertEqual(attempts, before_exception)
            w.write_json(ns.authorization_file, authorization)
            self.assertEqual(w.command_imagegen_authorize(ns), 0)
            self.assertEqual(len(attempts), 3)
            self.assertEqual(attempts[:2], before_exception)
            third_action = w.read_json(w.image_action_envelope_path(self.root))
            self.assertEqual(third_action['attempt_number'], 3)
            self.assertEqual(third_action['dispatch_correction_authorization'], authorization)
            self.assertIn(self.extra, third_action['imagegen_tool_arguments']['prompt'])
            self.assertEqual(w.command_imagegen_authorize(ns), 0)
            self.assertEqual(len(attempts), 3)
            third_action['state'] = 'completed'
            third_action['payload_sha256'] = w.canonical_payload_sha256(third_action)
            w.write_json(w.image_action_envelope_path(self.root), third_action)
            attempts[-1]['status'] = 'blocking_failure'
            with self.assertRaises(w.CompilerWorkerV2Error):
                w.command_imagegen_authorize(ns)
            self.assertEqual(len(attempts), 3)


    def test_footer_exception_preserves_history_and_stops_at_three(self):
        footer = "來源:Example · Specification　資訊圖:2026-10-11 (創造型 2nd Brain)"
        self.first.update(attempt_kind='initial_generation', action_identity='first')
        second = {**self.first, 'attempt_number': 2, 'attempt_kind': 'blocking_repair',
                  'action_identity': 'second', 'repair_dispatch_contract': self.contract()}
        prior = [copy.deepcopy(self.first), copy.deepcopy(second)]
        kp = {'job_id':'fixture-job','source':{'source_identity':'fixture'}}
        auth = {'schema':'imagegen_footer_repair_authorization_v1','scope':'one_additional_footer_repair',
                'authorized_by':'user','user_request':'授權再修一次，只修第二張來源行',
                'job_id':'fixture-job','source_identity':'fixture','page_id':'IG-1',
                'failed_action_identity':'second','failed_png_sha256':second['png_sha256'],
                'previous_attempts_sha256':w.sha256_text(json.dumps(prior,ensure_ascii=False,sort_keys=True)),
                'diagnosis_code':'footer_edit_wrong_location','preserve_all_other_content':True,'replacement_footer':footer}
        self.assertEqual(w.dispatch_correction_authorization_errors(kp,'IG-1',prior,auth),[])
        for key in ('job_id','page_id','failed_png_sha256','previous_attempts_sha256','user_request','scope','preserve_all_other_content'):
            self.assertTrue(w.dispatch_correction_authorization_errors(kp,'IG-1',prior,{**auth,key:''}))
        self.assertTrue(w.dispatch_correction_authorization_errors(kp,'IG-1',prior+[second],auth))
        kp_path=self.root/'knowledge_package.json';w.write_json(kp_path,kp)
        prompt_path=self.root/'prompt.txt';prompt_path.write_text(self.prompt,encoding='utf-8')
        ledger={'pages':{'IG-1':{'page_id':'IG-1','locked_prompt_sha256':w.sha256_text(self.prompt),
                'asset_path':'failed.png','attempts':prior}}}
        ns=argparse.Namespace(root=self.root,kp_file=kp_path,page_id='IG-1',reason='dispatch_correction',
                              failure='wrong_or_missing_locked_text',authorization_file=self.root/'auth.json')
        w.write_json(ns.authorization_file,auth)
        with contextlib.ExitStack() as stack:
            for name,value in {'infographic_plan_preflight_issues':[], 'validate_job_package_binding':{'job_id':'fixture-job'},
                              'ensure_ingest_plan':False,'infographic_page':{'page_id':'IG-1'},'validate_compiled_infographic_page':[],
                              'locked_infographic_context':(prompt_path,self.asset,w.sha256_text(self.prompt)),
                              'load_image_attempt_ledger':ledger,'write_v2_job':None,'ingest_execution_summary':{},
                              'visible_text_from_prompt':['body',footer]}.items():
                stack.enter_context(patch.object(w,name,return_value=value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            with self.assertRaises(w.CompilerWorkerV2Error):
                w.footer_exception_text({**auth,'replacement_footer':'changed'},self.prompt)
            before=copy.deepcopy(prior)
            self.assertEqual(w.command_imagegen_authorize(ns),0)
            self.assertEqual(prior[:2],before)
            self.assertEqual(len(prior),3)
            action=w.read_json(w.image_action_envelope_path(self.root))
            self.assertIn('Replace that ENTIRE line',action['edit_prompt'])
            self.assertIn(footer,action['edit_prompt'])
            third={**prior[-1],'status':'accepted','imagegen_tool_arguments':action['imagegen_tool_arguments']}
            self.assertTrue(w.blocking_repair_receipt_errors(third,second,self.prompt))
            third['semantic_readback']={'restricted_footer_review':{'only_footer_changed':True,'reference_png_sha256':second['png_sha256']}}
            self.assertEqual(w.blocking_repair_receipt_errors(third,second,self.prompt),[])
            third['imagegen_tool_arguments']={**action['imagegen_tool_arguments'],'prompt':self.prompt}
            self.assertTrue(w.blocking_repair_receipt_errors(third,second,self.prompt))
            self.assertEqual(w.command_imagegen_authorize(ns),0)
            self.assertEqual(len(prior),3)
            action['state']='completed';action['payload_sha256']=w.canonical_payload_sha256(action)
            w.write_json(w.image_action_envelope_path(self.root),action)
            prior[-1]['status']='blocking_failure'
            with self.assertRaises(w.CompilerWorkerV2Error):w.command_imagegen_authorize(ns)
            self.assertEqual(len(prior),3)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *rest])

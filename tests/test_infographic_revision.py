"""Offline counterexamples: synthetic evidence never counts as live acceptance."""
import copy
import importlib.util
import json
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
if (ROOT/'runtime/workspace').is_dir():ROOT=ROOT/'runtime/workspace'
sys.path.insert(0,str(ROOT))
from tools import compiler_worker_v2 as w, ingest_rail as rail, infographic_revision as revision
from tools.infographic_prompt_contract import reader_page_sha256, reader_plan_review_errors, image_reverse_review_errors, infographic_spec_read_receipt, load_infographic_reverse_review_contract, visible_text_from_prompt
from tools.ingest_plan_contract import build_kp, input_fingerprint, VERSION
from PIL import Image, ImageDraw

def load(name):
    spec=importlib.util.spec_from_file_location('fixture_'+name,ROOT/'tests'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

BODY='固定流程可保留完整原文與來源證據，並減少重複操作及核對所需時間。'

def semantic_plan(package):
    return {'schema_version':VERSION,'input_fingerprint':input_fingerprint(package),
      'source_metadata':{'source_label':'Example: Article','source_type':'使用者提供文字','primary_work_status':'none'},
      'analysis':{'summary':['本文說明固定流程如何保存全文、保留來源證據及減少重複核對。'],'source_claims':[{'id':'C-1','claim':BODY,'evidence_anchor':BODY,'confidence':'高'}]},
      'coverage_units':[{'id':'U-1','text':BODY,'source_evidence':BODY,'unit_types':['structure_unit'],'dispositions':['source_claim','knowledge_point','infographic'],'infographic_text':[BODY]}],
      'knowledge':[{'kp_id':'KP-VS-0001-001','title':'完整原文保留','claim':BODY,'layer':'原文主張','confidence':'高','evidence_anchor':BODY}], 'relations':[],
      'application':{'knowledge_use':'事實真源','adoption_status':'收藏','adoption_reason':'保存這項方法供日後參考。','personalization_links':[],'context_anchors':[],
        'compounding_insight_status':'needs_context','compounding_insight_types':[],'compounding_next_action':'有實際案例時再比較適用性。','taste_network_status':'否','taste_link_status':'not_applicable','taste_reason':'來源沒有作品品味判斷。',
        'compounding_judgments':{kind:{'decision':'需要更多脈絡','reason':'尚未有足夠既有筆記作比較。'} for kind in ('補底','加深','連接','修正','行動')}},
      'infographics':[{'page_id':'IG-001','page_focus':BODY,'page_focus_evidence':BODY,'layout_archetype':'重點總覽','content_use':'事實真源','graph_type':'原文重點卡',
        'visual_structure':{'kind':'fact_cards','items':[{'heading_text':BODY,'body_text':BODY}]},'coverage_ids':['U-1'],'reader_question':'固定流程有甚麼用途？','reader_takeaway':BODY,'cross_page_handoff':'已完整呈現這項方法。'}]}

def fixture_image(asset,prompt):
    image=Image.new('RGB',(1024,1536),'#F7F2E8');ImageDraw.Draw(image).rectangle((0,1470,1023,1535),fill='#071A33')
    asset.parent.mkdir(parents=True,exist_ok=True);image.save(asset,'PNG')
    return {'inspection_method':'independent_image_readback','inspector_identity':'OFFLINE-TEST-ONLY','formal_ingestion_allowed':True,
      'spec_read_receipt':infographic_spec_read_receipt(),
      'review_axes':{axis.strip():'passed' for axis in load_infographic_reverse_review_contract()['required_review_axes'].split(',')},
      'reader_review':'Synthetic fixture only; no live image claim.',
      'inspected_png_sha256':w.sha256_bytes(asset.read_bytes()),'inspected_prompt_sha256':w.sha256_text(prompt),
      'visible_text_readback':visible_text_from_prompt(prompt),'unexpected_visible_text':[],'internal_process_text_detected':False}

def proof(page):
    return {"page_sha256":reader_page_sha256(page),"method":"isolated_page_readback","reviewer_id":"OFFLINE-TEST-ONLY",
            "verdict":"passed","answer":"Synthetic isolated contract test, not human comprehension evidence.",
            "relationship_explanation":"Synthetic relation evidence for testing only.","unexplained_terms":[],"missing_context":[],"misleading_relations":[]}

class RevisionTests(unittest.TestCase):
    def test_heading_requires_paired_visible_body(self):
        from tools.infographic_prompt_contract import contextual_heading_labels, visible_text_quality_errors
        heading='誰負責甚麼'
        page={'visible_text':[heading,BODY], 'visual_structure':{'kind':'fact_cards','items':[{'heading_text':heading,'body_text':BODY}]}}
        self.assertTrue(visible_text_quality_errors([heading]))
        self.assertFalse(visible_text_quality_errors([heading],contextual_labels=contextual_heading_labels(page)))
        self.assertTrue(visible_text_quality_errors(['這能幫助判斷'],contextual_labels={'這能幫助判斷'}))
        page['visible_text']=[heading]
        self.assertTrue(visible_text_quality_errors([heading],contextual_labels=contextual_heading_labels(page)))

    def test_additional_edit_needs_bound_user_authorization(self):
        self.prepare()
        action=rail.advance(self.root,'RAIL-TEST',Path('knowledge-base-vault'))['imagegen']
        asset=self.root/action['expected_asset']; review=fixture_image(asset,action['prompt'])
        response=rail.image_response_template(self.root,action)
        response.update(runtime_tool='Codex imagegen',imagegen_dispatch_evidence='OFFLINE-TEST-ONLY',outcome='blocking_failure',failure_code='wrong_or_missing_locked_text',semantic_evidence='Missing locked text: '+BODY,semantic_readback=review,imagegen_tool_arguments=action['imagegen_tool_arguments'])
        path=self.kp_path.parent/'failed.json';w.write_json(path,response)
        with self.assertRaises(w.CompilerWorkerV2Error):
            rail.invoke(w.command_imagegen_complete,root=self.root,kp_file=self.kp_path,response_file=path)
        kwargs=dict(root=self.root,kp_file=self.kp_path,page_id='IG-001',reason='user_authorized_text_revision',failure=None,authorization_file=None)
        with self.assertRaises(w.CompilerWorkerV2Error):rail.invoke(w.command_imagegen_authorize,**kwargs)
        prior=w.read_json(w.image_attempt_ledger_path(self.kp_path.parent))['pages']['IG-001']['attempts']
        reference=self.kp_path.parent/'failed-reference.png';reference.write_bytes(asset.read_bytes())
        auth={'schema':'infographic_text_correction_authorization_v1','authorized_by':'user','job_id':'RAIL-TEST','source_identity':w.read_json(self.kp_path)['source']['source_identity'],'page_id':'IG-001','failed_action_identity':prior[-1]['action_identity'],'failed_png_sha256':prior[-1]['png_sha256'],'previous_attempts_sha256':w.sha256_text(json.dumps(prior,ensure_ascii=False,sort_keys=True)),'additional_attempts':1,'preserve_all_other_content':True,'user_request':'Explicit offline correction authorization','restore_text':BODY,'reference_path':reference.relative_to(self.root).as_posix()}
        auth_path=self.kp_path.parent/'extra-auth.json';w.write_json(auth_path,auth);kwargs['authorization_file']=auth_path
        bad=copy.deepcopy(auth);bad['reference_path']=asset.relative_to(self.root).as_posix();w.write_json(auth_path,bad)
        with self.assertRaises(w.CompilerWorkerV2Error):rail.invoke(w.command_imagegen_authorize,**kwargs)
        w.write_json(auth_path,auth)
        correction=rail.invoke(w.command_imagegen_authorize,**kwargs)
        self.assertEqual(correction['attempt_number'],3)
        correction=w.read_json(self.root/correction['action_envelope'])
        self.assertIn(BODY,correction['imagegen_tool_arguments']['prompt'])
        self.finish(correction)

    def setUp(self):
        import tempfile
        self.root=Path(tempfile.gettempdir())/('revision-contract-'+uuid.uuid4().hex[:12]);self.root.mkdir()
        source=self.root/'source.md';source.write_text(BODY,encoding='utf-8')
        rail.start(SimpleNamespace(root=self.root,url=None,image_file=None,text_file=source,source_id='KS-VS-0001',job_id='RAIL-TEST',title='固定流程',slug='fixture',profile='article'))
        _,package,self.kp_path=rail.paths(self.root,'RAIL-TEST');self.original=self.kp_path
        kp=build_kp(semantic_plan(package),package,root=self.root)
        for page in kp['infographics']:page['reader_review']=proof(page)
        w.write_json(self.kp_path,kp)
        self.finish(rail.advance(self.root,'RAIL-TEST',Path('knowledge-base-vault'))['imagegen'])
        (self.root/'brain.json').write_text('{}',encoding='utf-8')
        (self.root/'tools').mkdir();(self.root/'tools/compiler_worker_v2.py').write_text('# root marker',encoding='utf-8')
        self.first=w.read_json(w.job_file(self.root,'RAIL-TEST'))['first_accepted_at']

    def plan(self,label='text-r1'):
        kp=w.read_json(self.kp_path);pages=copy.deepcopy(kp['infographics'])
        for page in pages:
            w.clear_infographic_receipt(page);page['status']='planned';page['reader_review']=proof(page)
        plan={"schema_version":w.INFOGRAPHIC_REVISION_PLAN_VERSION,"mode":"same_job_text_revision",
              "revision_id":label,"job_id":kp['job_id'],"source_id":kp['source']['source_id'],"source_identity":kp['source']['source_identity'],
              "authorization":{"authority":"current_user_message","additional_attempts_per_page":1,"user_instruction":"Offline test authorization"},
              "coverage_units":copy.deepcopy(kp['coverage_units']),"infographics":pages}
        path=self.root/f'{label}.json';w.write_json(path,plan)
        return SimpleNamespace(root=self.root,kp_file=self.kp_path,plan_file=path,revision_id=None,recover_incomplete=False),plan

    def prepare(self,label='text-r1'):
        args,plan=self.plan(label);result=rail.invoke(w.command_prepare_infographic_revision,**vars(args))
        self.kp_path=self.root/result['knowledge_package'];return args,plan

    def finish(self,action):
        asset=self.root/action['expected_asset']
        review=fixture_image(asset,action['prompt'])
        if getattr(self,'fullwidth_footer',False):
            review['visible_text_readback'][-1]=review['visible_text_readback'][-1].replace(':','：').replace('(','（').replace(')','）')
        review['reader_comprehension']={k:v for k,v in proof({}).items() if k in ('answer','relationship_explanation','unexplained_terms','missing_context','misleading_relations')}
        review['text_revision_review']={'accepted_style_preserved':True,'new_text_complete':True,'evidence':'Synthetic fixture only; no live image claim.'}
        response=rail.image_response_template(self.root,action)
        response.update(runtime_tool='Codex imagegen',imagegen_dispatch_evidence='OFFLINE-TEST-ONLY',outcome='accepted',
                        semantic_evidence='Synthetic fixture for isolated writer tests only.',semantic_readback=review,
                        page_number_readback=review['visible_text_readback'][-2],footer_text_readback=review['visible_text_readback'][-1],
                        imagegen_tool_arguments=action.get('imagegen_tool_arguments',{}))
        response_path=self.kp_path.parent/'fixture_response.json';w.write_json(response_path,response)
        rail.invoke(w.command_imagegen_complete,root=self.root,kp_file=self.kp_path,response_file=response_path)
        result=rail.advance(self.root,'RAIL-TEST',Path('knowledge-base-vault'))
        self.assertEqual(result['status'],'completed')

    def test_multiline_evidence_cannot_split_infographic_blocks(self):
        from tools.firecrawl_obsidian_pipeline import extract_infographic_blocks, infographic_field
        kp=w.read_json(self.kp_path)
        original_body=(self.kp_path.parent/'canonical_source.md').read_bytes()
        kp['coverage_units'][0]['source_evidence']=BODY+'\n\n## Source heading\n### 資訊圖 99: quoted heading\n'+BODY
        kp['infographics'].append(copy.deepcopy(kp['infographics'][0]))
        rendered=w.render_source_slice(kp,self.kp_path.parent)
        blocks=extract_infographic_blocks(rendered)
        self.assertEqual(len(blocks),2)
        for block in blocks:
            self.assertIn('Source heading',block)
            self.assertEqual(infographic_field(block,'PNG 語義驗收狀態'),'passed')
            self.assertTrue(infographic_field(block,'PNG 生成方式'))
            self.assertTrue(infographic_field(block,'本 URL 資訊圖數量判斷'))
        self.assertEqual((self.kp_path.parent/'canonical_source.md').read_bytes(),original_body)

    def test_fullwidth_footer_reaches_writer(self):
        self.prepare()
        self.fullwidth_footer=True
        self.finish(rail.advance(self.root,'RAIL-TEST',Path('knowledge-base-vault'))['imagegen'])

    def test_two_revisions_writer_and_ancestors(self):
        original_bytes=self.original.read_bytes();original_receipt=(self.original.parent/'completion_receipt.json').read_bytes()
        ancestors=[self.original]
        for number in (2,3):
            self.prepare(f'text-r{number-1}')
            self.assertNotIn('outcome_recovery',w.read_json(w.job_file(self.root,'RAIL-TEST')))
            action=rail.advance(self.root,'RAIL-TEST',Path('knowledge-base-vault'))['imagegen']
            self.assertEqual(action['attempt_number'],number)
            same=rail.advance(self.root,'RAIL-TEST',Path('knowledge-base-vault'))
            self.assertEqual(same['action'],'reconcile_image_result')
            self.finish(action)
            receipt=w.read_json(self.kp_path.parent/'completion_receipt.json')
            html=(self.root/receipt['outcome']['current_page']).read_text(encoding='utf-8')
            for asset in receipt['adapter']['assets']:
                self.assertIn(Path(asset).name,html)
            for path in ancestors:
                self.assertEqual(w.completed_receipt_readback(self.root,path.parent,path,w.read_json(path))['status'],'completed')
            ancestors.append(self.kp_path)
        self.assertEqual(w.read_json(w.job_file(self.root,'RAIL-TEST'))['first_accepted_at'],self.first)
        self.assertEqual(self.original.read_bytes(),original_bytes)
        self.assertEqual((self.original.parent/'completion_receipt.json').read_bytes(),original_receipt)

    def test_prepare_interruption_and_identity_reset_rejected(self):
        args,plan=self.plan()
        with patch.object(w,'mirror_revision_input_evidence',side_effect=OSError('offline interruption')):
            with self.assertRaises(OSError):w.command_prepare_infographic_revision(args)
        first=rail.invoke(w.command_prepare_infographic_revision,**vars(args))
        again=rail.invoke(w.command_prepare_infographic_revision,**vars(args))
        self.assertEqual(first['knowledge_package'],again['knowledge_package'])
        plan['job_id']='RESET-JOB';w.write_json(args.plan_file,plan)
        with self.assertRaisesRegex(w.CompilerWorkerV2Error,'retain the original job'):w.command_prepare_infographic_revision(args)

    def test_authorization_interruption_reuses_identity(self):
        self.prepare();rail.invoke(w.command_compile_infographics,root=self.root,kp_file=self.kp_path,plan_repair_file=None)
        original_write=w.write_json
        def crash(path,value):
            if Path(path)==w.image_action_envelope_path(self.kp_path.parent):raise OSError('offline envelope interruption')
            return original_write(path,value)
        with patch.object(w,'write_json',side_effect=crash):
            with self.assertRaises(OSError):rail.advance(self.root,'RAIL-TEST',Path('knowledge-base-vault'))
        saved=w.read_json(self.kp_path.parent/'text_revision_authorization.json')['envelope']
        result=rail.advance(self.root,'RAIL-TEST',Path('knowledge-base-vault'))
        self.assertEqual(result['action'],'reconcile_image_result')
        actual=w.read_json(w.image_action_envelope_path(self.kp_path.parent))
        self.assertEqual(saved['action_identity'],actual['action_identity']);self.assertEqual(actual['attempt_number'],2)

    def test_missing_dispatch_history_and_ancestor_drift_rejected(self):
        self.prepare();action=rail.advance(self.root,'RAIL-TEST',Path('knowledge-base-vault'))['imagegen']
        ledger=w.read_json(w.image_attempt_ledger_path(self.kp_path.parent));record=ledger['pages'][action['page_id']]
        fake=copy.deepcopy(record);fake['attempts'][0]['status']='accepted';fake['attempts'][0].pop('text_revision_dispatch')
        with self.assertRaisesRegex(w.CompilerWorkerV2Error,'dispatch contract'):revision.validate_record(w,self.root,self.kp_path.parent,fake)
        fake=copy.deepcopy(record);fake['prior_attempts']=[]
        with self.assertRaisesRegex(w.CompilerWorkerV2Error,'retain all prior'):revision.validate_record(w,self.root,self.kp_path.parent,fake)
        self.original.write_bytes(self.original.read_bytes()+b' ')
        with self.assertRaisesRegex(w.CompilerWorkerV2Error,'ancestor changed'):revision.verify_origin(w,self.root,self.kp_path.parent)

class ReaderTests(unittest.TestCase):
    def test_missing_stale_and_unresolved_readback_block(self):
        page={'page_id':'p','visible_text':['完整句子']}
        self.assertTrue(reader_plan_review_errors(page));page['reader_review']=proof(page)
        self.assertFalse(reader_plan_review_errors(page));page['visible_text']=['改字']
        self.assertIn('reader review is stale or bound to another page',reader_plan_review_errors(page))
        page['reader_review']=proof(page);page['reader_review']['missing_context']=['不知道誰批准']
        self.assertTrue(reader_plan_review_errors(page))
    def test_new_image_cannot_use_ocr_only_review(self):
        self.assertIn('missing actual-image reader comprehension evidence',image_reverse_review_errors({}))

if __name__=='__main__':unittest.main()

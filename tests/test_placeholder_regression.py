"""Placeholder lexical regression; read-only, no live package writes."""
import sys
from pathlib import Path
root=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
if (root/'runtime/workspace').is_dir(): root=root/'runtime/workspace'
sys.path.insert(0,str(root))
from tools.outcome_page_quality_audit import placeholder_terms_in, reader_surface_text_issues
for value in ('待補', '摘要：待補', '<b>待補</b>', '待補資料', '仍待補足。摘要待補'):
    assert '待補' in placeholder_terms_in(value),value
    assert any('placeholder text: 待補' in x for x in reader_surface_text_issues(value,surface='page')),value
for value in ('公司代表權仍待補足。', '付款、推送與附件尚待補足；仍需驗證。'):
    assert placeholder_terms_in(value)==[],value
    assert not any('placeholder text' in x for x in reader_surface_text_issues(value,surface='page')),value
assert '[]' in placeholder_terms_in('<b>[]</b>')
print('placeholder lexical and both consumer paths: passed')

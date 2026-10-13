"""Bounded regression and scale fixtures; never edits an operator vault."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

def main():
    p=argparse.ArgumentParser();p.add_argument('--tools',type=Path,required=True);p.add_argument('--scratch',type=Path,required=True);p.add_argument('--real-root',type=Path,required=True);a=p.parse_args()
    sys.path.insert(0,str(a.tools));import knowledge_nebula as k
    root=a.scratch.resolve();root.mkdir(parents=True,exist_ok=False)
    v=root/'My notes';(v/'_registry').mkdir(parents=True);(v/'sources').mkdir();(v/'concepts').mkdir();(v/'outcome-pages').mkdir()
    (root/'brain.json').write_text(json.dumps({'vault':'My notes','display_name':'Fixture only'}),encoding='utf-8')
    reg=v/'_registry/sources.jsonl';reg.write_text('',encoding='utf-8')
    checks=[]
    def check(label,condition):
        assert condition,label
        checks.append(label)
    def read_page(path):
        s=path.read_text(encoding='utf-8');return json.loads(s.split('const DATA=',1)[1].split(';\n',1)[0])
    def write_rows(rows):reg.write_text('\n'.join(json.dumps(x) for x in rows),encoding='utf-8')
    empty=k.build(root);check('empty configured vault',empty['sources']==0)
    # Actual corpus is read-only input; take one canonical note as an isolated mutation fixture.
    actual=k.collect(a.real_root,(a.real_root/'outputs/knowledge-nebula-live'))
    real_source=next(n for n in actual['nodes'] if n['kind']=='source')
    real_config=json.loads((a.real_root/'brain.json').read_text(encoding='utf-8'))
    note=a.real_root/real_config['vault']/real_source['sourcePath'];original_sha=k.digest(note)
    target=v/'sources/real.md';target.write_bytes(note.read_bytes());sid=real_source['id']
    rows=[{'source_id':sid,'source_note_path':'sources/real.md','status':'linked'}];write_rows(rows)
    (v/'outcome-pages'/f'{sid}_outcome.html').write_text('<html>isolated real-source test</html>',encoding='utf-8')
    r=k.build(root);check('real note projected',r['sources']==1 and r['warnings']==[])
    check('identical rerun does not rewrite',k.build(root)['status']=='unchanged')
    target.write_text(target.read_text(encoding='utf-8').replace(real_source['title'],'Updated test title'),encoding='utf-8')
    r=k.build(root);data=read_page(v/'outcome-pages/nebula.html');check('canonical edit reflected',data['nodes'][0]['title']=='Updated test title')
    new=v/'sources/new.md';new.write_text('---\ntitle: "New source"\nsummary: "</script><script>alert(1)</script>"\n---\nBody',encoding='utf-8')
    rows.append({'source_id':'KS-TEST-NEW','source_note_path':'sources/new.md','status':'linked'});write_rows(rows)
    concept=v/'concepts/link.md';concept.write_text('---\ntitle: "New idea"\nconcept_status: "graph_node"\nsource_records:\n  - "KS-TEST-NEW"\n---\n## 一句話\nTest relation.',encoding='utf-8')
    r=k.build(root);check('new source and concept reflected',r['sources']==2 and r['concepts']==1 and r['edges']==1)
    check('missing outcome is explicit',len(r['warnings'])==1)
    s=(v/'outcome-pages/nebula.html').read_text(encoding='utf-8');check('script injection escaped','</script><script>alert(1)</script>' not in s)
    write_rows(rows[:1]);r=k.build(root);check('removed source and its edges disappear',r['sources']==1 and r['edges']==0)
    concept.unlink();r=k.build(root);check('removed concept disappears',r['concepts']==0)
    before=k.digest(v/'outcome-pages/nebula.html');write_rows(rows[:1]*2)
    try:k.build(root);raise AssertionError('duplicate accepted')
    except ValueError:pass
    check('duplicate fails without replacing graph',before==k.digest(v/'outcome-pages/nebula.html'))
    write_rows([{'source_id':'ESCAPE','source_note_path':'../outside.md'}])
    try:k.build(root);raise AssertionError('escape accepted')
    except ValueError:pass
    check('traversal rejected',before==k.digest(v/'outcome-pages/nebula.html'))
    check('real canonical untouched',k.digest(note)==original_sha)
    # Independent, explicitly synthetic scale fixture; not library contents.
    rows=[];started=time.perf_counter()
    for i in range(10000):
        sid=f'SCALE-{i:05}';path=v/'sources'/f'{sid}.md'
        path.write_text(f'---\ntitle: "Synthetic node {i}"\nsummary: "Scale fixture"\n---\n',encoding='utf-8')
        rows.append({'source_id':sid,'source_note_path':path.relative_to(v).as_posix(),'status':'linked'})
    write_rows(rows)
    concept.write_text('---\ntitle: "Scale hub"\nconcept_status: "graph_node"\nsource_records:\n'+''.join(f'  - "SCALE-{i:05}"\n' for i in range(10000))+'---\n## 一句話\nSynthetic scale only.',encoding='utf-8')
    r=k.build(root);check('10000 synthetic sources and edges retained',r['sources']==10000 and r['edges']==10000)
    check('render budgets embedded',read_page(v/'outcome-pages/nebula.html')['limits']=={'scene':96,'list':40,'relations':30})
    report={'status':'passed','checks':checks,'real_counts':{key:actual[key] for key in ['sourceCount','ideaCount']},'real_edges':len(actual['edges']),'scale':{key:r[key] for key in ['sources','concepts','edges','bytes','seconds']},'synthetic_fixture':True,'scale_setup_seconds':round(time.perf_counter()-started,3)}
    (root/'test-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))

if __name__=="__main__":main()

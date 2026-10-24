"""Rebuild a read-only, portable knowledge nebula from the configured vault.

No network, vault mutation, inference of relations, or dependency on a demo.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import unicodedata
from urllib.parse import quote

try:
    from .obsidian_idea_retrieval import read_markdown, NOTE_LINK_RE
    from .workspace_paths import vault_path
    from .knowledge_nebula_layout import arrange
except ImportError:
    from obsidian_idea_retrieval import read_markdown, NOTE_LINK_RE
    from workspace_paths import vault_path
    from knowledge_nebula_layout import arrange

ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'knowledge-nebula'
MARKER = '<!-- knowledge-nebula-generated-v1 -->'
PALETTE = [[.36,.78,1],[.57,.50,1],[.89,.46,.83],[.53,.81,.93],[.68,.58,1],[.40,.70,1],[.77,.60,.91]]
SECTIONS = {'連到 Hub','形成取捨','品味線索','可應用於','核心概念','支持','反例','限制','延伸','相關概念','相關連結'}
# The one relation a thinking record adds: from a new star to each parent star the record's evidence came from.
ORIGIN_TYPE = '孕育自'
# Relation families (colour and drawing style) are defined here once; the page only reads them
# from the data. The tuple order is the legend order only: the overview line priority is
# RELATION_TIERS below, and it works per relation type, not per family. Any type not listed
# falls into 'structural'.
RELATION_FAMILIES = (
    ('tension', '張力', ('反例',)),
    ('limit', '限制', ('限制',)),
    ('tradeoff', '取捨', ('形成取捨',)),
    ('support', '支持', ('支持', '延伸')),
    ('applies', '應用', ('可應用於',)),
    ('structural', '結構', ('相關概念', '查證來源', '連到 Hub', '核心概念', '品味線索', '相關連結')),
    # 孕育 lines (new star -> parent) are not semantic: they stay out of semanticDegree, priorityDegree and degree. The page always
    # draws them when both ends are in the scene (there are few) and keeps them out of the overview line quotas.
    ('origin', '孕育', (ORIGIN_TYPE,)),
)
FAMILY_OF = {kind: family for family, _, kinds in RELATION_FAMILIES for kind in kinds}
SEMANTIC_FAMILIES = {'tension', 'limit', 'tradeoff', 'support', 'applies'}
# 反例 and 限制 together are the node's "against" count, the same definition as the
# tension_hotspot signal in knowledge_direction.py (contradicts + limits).
AGAINST_FAMILIES = {'tension', 'limit'}
# Overview line priority, defined here once and sent with every edge as `tier` (the page
# never re-derives it). Tiers go by relation TYPE: 延伸 and 支持 share a family but not a
# tier. (tier, id, label, types); every type not listed is tier STRUCTURAL_TIER.
RELATION_TIERS = (
    (1, 'advance', '推進', ('延伸', '可應用於', '形成取捨')),
    (2, 'condition', '條件／張力', ('反例', '限制')),
    (3, 'evidence', '佐證', ('支持',)),
)
STRUCTURAL_TIER = 4
TIER_OF = {kind: tier for tier, _, _, kinds in RELATION_TIERS for kind in kinds}
# Tiers 1 and 2 are the "moving thinking forward" relations; their count orders overview concepts.
PRIORITY_TIERS = {1, 2}
# Filter choices shown in the page: (id, label, families it keeps). The id 'tension' is kept so
# that remembered selections stay valid.
RELATION_FILTERS = (
    ('all', '全部關係', tuple(family for family, _, _ in RELATION_FAMILIES)),
    ('tension', '張力與條件（反例／限制／取捨）', ('tension', 'limit', 'tradeoff')),
    ('support', '支持／延伸', ('support',)),
    ('applies', '可應用於', ('applies',)),
    ('structural', '結構', ('structural',)),
    ('origin', '孕育（新星及其母星）', ('origin',)),
)

def relation_family(kind): return FAMILY_OF.get(kind, 'structural')
def relation_tier(kind): return TIER_OF.get(kind, STRUCTURAL_TIER)
CHECKED_DIRS = set()

def plain(path):
    for p in (path, *path.parents):
        if p in CHECKED_DIRS: continue
        try: st = p.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(st.st_mode) or getattr(st,'st_file_attributes',0)&0x400:
            raise ValueError('Link/junction paths are not supported')
        if stat.S_ISDIR(st.st_mode): CHECKED_DIRS.add(p)
    return path

def inside(root, relative):
    raw = str(relative).replace('\\','/')
    if not raw or raw.startswith('/') or ':' in raw or '..' in Path(raw).parts:
        raise ValueError('Expected a relative path within the selected workspace/vault')
    p = plain(root / raw)
    if not p.is_relative_to(root): raise ValueError('Path escapes selected root')
    return p

def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def stable(s): return int(hashlib.sha256(s.encode()).hexdigest()[:12],16)
def scalar(v): return v if isinstance(v,str) else ''
def values(v): return v if isinstance(v,list) else []

def preview_files(vault, fields):
    """Use only canonical declared media; never crawl or regenerate images."""
    case = fields.get('asset_mode') == 'case_evidence'
    candidates = values(fields.get('evidence_screenshots' if case else 'infographics'))
    label = '案例截圖' if case else '資訊圖'
    if not candidates:
        candidates = values(fields.get('evidence_screenshots'))
        label = '案例截圖'
    images = []
    for rel in candidates:
        if not isinstance(rel, str): continue
        p = inside(vault, rel)
        if p.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.gif'} or not p.is_file(): continue
        images.append((p, label))
    return images


def preview_images(vault, fields, output_dir):
    return [dict(src=quote(os.path.relpath(p, output_dir).replace('\\', '/'), safe='/.-_'), kind=label)
            for p, label in preview_files(vault, fields)]

# ===== Thinking records come back as "new stars" (kind 'thought'): one node per vault/thinking/ASK-*.json, a 孕育自 line to each parent star =====
THOUGHT_SCHEMA = 'thinking_record_v1'          # the record format thinking_archive.py writes; here it is read only, and checked for shape only (its integrity stays with thinking_archive)
THOUGHT_ID = re.compile(r'ASK-[0-9a-f]{32}')   # the same identity as thinking_archive.ID
THOUGHT_INSIGHTS_MAX = 3                       # insight titles a new star carries
THOUGHT_QUESTIONS_MAX = 3                      # "unfinished questions" (the adjacent question of each insight) a new star carries
THOUGHT_COLOR = [1.0, .90, .66]                # warm white-gold core of a new star; the page reads it like any other node colour
# Parent rule: an evidence path that is the sourcePath of a node is that node (a source note or a concept note); a path under
# knowledge-points/by-source/<source id>/ is the outcome node of that source id (the folder name is the source id: it equals the
# registry's source_id and the kp_uid prefix of every one of the 521 knowledge points of the real vault). Any other path has no parent star.


def thought_text(value):
    return value.strip() if isinstance(value, str) and value.strip() else ''


def thought_star(raw, stem, by_path, source_ids):
    """One new star (and its 孕育自 lines) from a parsed record, or ValueError naming what is missing. Shape check only."""
    if not isinstance(raw, dict) or raw.get('schema') != THOUGHT_SCHEMA: raise ValueError('not a ' + THOUGHT_SCHEMA + ' record')
    rid = raw.get('id')
    if not isinstance(rid, str) or not THOUGHT_ID.fullmatch(rid) or rid != stem: raise ValueError('record id does not match its file name')
    created = thought_text(raw.get('created_at'))
    packet, answer = raw.get('packet'), raw.get('answer')
    if not created or not isinstance(packet, dict) or not isinstance(answer, dict): raise ValueError('created_at, packet or answer is missing')
    question, evidence = thought_text(packet.get('question')), packet.get('evidence')
    if not question or not isinstance(evidence, list): raise ValueError('packet.question or packet.evidence is missing')
    analysis = answer.get('analysis')
    thesis = thought_text(analysis.get('thesis')) if isinstance(analysis, dict) else ''
    insights = analysis.get('insights') if thesis else None
    if not thesis or not isinstance(insights, list): raise ValueError('answer.analysis.thesis or insights is missing')
    parents, quotes = [], {}
    for item in evidence:
        path = item.get('path') if isinstance(item, dict) else None
        if not isinstance(path, str) or not path: raise ValueError('packet.evidence[].path is missing')
        rel = path.replace('\\', '/')
        parts = rel.split('/')
        parent = by_path.get(rel) or (parts[2] if len(parts) > 3 and parts[:2] == ['knowledge-points', 'by-source'] and parts[2] in source_ids else None)
        if parent and parent not in quotes:
            parents.append(parent)
            quotes[parent] = 'packet.evidence: ' + rel
    items = [i for i in insights if isinstance(i, dict)]
    titles = [t for t in (thought_text(i.get('title')) for i in items) if t][:THOUGHT_INSIGHTS_MAX]
    asks = [q for q in (thought_text(i['adjacent'].get('question')) if isinstance(i.get('adjacent'), dict) else '' for i in items) if q][:THOUGHT_QUESTIONS_MAX]
    # Birth style inputs (the style itself is chosen once, in the page's birth-style table): did the record use web sources, and did it
    # answer tension relations. Named webOut / tensionOut because `tension` is already the graph flag (a node with both support and counter lines).
    star = dict(id=rid, title=question, label=question if len(question) <= 23 else question[:22] + '…', kind='thought', copy=thesis,
                insights=titles, openQuestions=asks, created=created, href='', parents=parents, searchText=' '.join(titles + asks),
                webOut=bool(values(answer.get('web_sources'))), tensionOut=bool(values(answer.get('tension_responses'))),
                color=list(THOUGHT_COLOR), semanticDegree=0, priorityDegree=0, against=0, tension=False, degree=0)
    star['for'] = 0
    lines = [dict(a=rid, b=parent, type=ORIGIN_TYPE, evidence='思考紀錄的證據列出此筆記；只記錄出處，未推論因果。', path='thinking/' + rid + '.json', quote=quotes[parent],
                  family=relation_family(ORIGIN_TYPE), tier=relation_tier(ORIGIN_TYPE)) for parent in parents]
    return star, lines


def thought_stars(vault, output_dir, nodes, track, warnings):
    """Read-only. A record that does not qualify, or a thinking folder that cannot be read, is skipped with a warning; it never fails the build."""
    try:
        folder = inside(vault, 'thinking')
        files = sorted(folder.glob('ASK-*.json')) if folder.is_dir() else []
    except (ValueError, OSError) as error:
        warnings.append({'id': 'thinking', 'reason': 'thinking folder skipped: ' + str(error)})
        return [], []
    by_path = {n['sourcePath']: n['id'] for n in nodes if n.get('sourcePath')}
    source_ids = {n['id'] for n in nodes if n['kind'] == 'source'}
    stars, lines = [], []
    for path in files:
        try:
            track(path)
            star, origin = thought_star(json.loads(path.read_text(encoding='utf-8-sig')), path.stem, by_path, source_ids)
            reader = inside(vault, 'outcome-pages/thinking/' + star['id'] + '.html')
            if reader.is_file():
                track(reader)
                star['href'] = quote(os.path.relpath(reader, output_dir).replace('\\', '/'), safe='/.-_')
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
            warnings.append({'id': path.stem, 'reason': 'thinking record skipped: ' + str(error)})
            continue
        stars.append(star)
        lines.extend(origin)
    return stars, lines


# ===== "My constellations": a person's own named groups of stars (bookmarks). Stored outside the notes and the registries, in one small file of the reader folder
# (vault/.reader/nebula-constellations.json); the page embeds the last saved content so that it can be read offline, and the reader service (knowledge_nebula_server.py) is the only writer.
# The limits live here once and travel to the page with the data; a star that no longer exists stays in the file (the page marks it "no longer in the nebula").
CONSTELLATION_SCHEMA = 'knowledge_nebula_constellations_v1'
CONSTELLATION_LIMITS = dict(max=12, stars=60, name=20, colors=12, bytes=65536)
CONSTELLATION_ID = re.compile(r'[a-z0-9]{6,24}\Z')
CONSTELLATION_STAR = re.compile(r'[A-Za-z0-9_\-]{1,80}\Z')


def constellations_path(vault): return inside(vault, '.reader/nebula-constellations.json')


def validate_constellations(value):
    """The one door for constellation data (read from the file and sent by the page): an exact shape, or ValueError. Returns the normalised record."""
    limits = CONSTELLATION_LIMITS
    if not isinstance(value, dict) or set(value) != {'schema', 'constellations'} or value['schema'] != CONSTELLATION_SCHEMA: raise ValueError('constellations: bad record')
    rows = value['constellations']
    if not isinstance(rows, list) or len(rows) > limits['max']: raise ValueError('constellations: too many')
    out, ids, names = [], set(), set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'name', 'color', 'stars'}: raise ValueError('constellations: bad constellation')
        cid, name, color, stars = row['id'], row['name'], row['color'], row['stars']
        if not isinstance(cid, str) or not CONSTELLATION_ID.fullmatch(cid) or cid in ids: raise ValueError('constellations: bad id')
        if not isinstance(name, str): raise ValueError('constellations: bad name')
        name = name.strip()
        if not 1 <= len(name) <= limits['name'] or any(unicodedata.category(ch).startswith('C') for ch in name) or name.casefold() in names: raise ValueError('constellations: bad name')
        if not isinstance(color, int) or isinstance(color, bool) or not 0 <= color < limits['colors']: raise ValueError('constellations: bad colour')
        if not isinstance(stars, list) or len(stars) > limits['stars']: raise ValueError('constellations: too many stars')
        if any(not isinstance(star, str) or not CONSTELLATION_STAR.fullmatch(star) for star in stars) or len(set(stars)) != len(stars): raise ValueError('constellations: bad star')
        ids.add(cid); names.add(name.casefold())
        out.append(dict(id=cid, name=name, color=color, stars=list(stars)))
    return dict(schema=CONSTELLATION_SCHEMA, constellations=out)


def load_constellations(vault):
    """The saved record (an empty one when there is no file). Raises ValueError for a file that is not valid: it is never repaired or replaced here."""
    path = constellations_path(vault)
    empty = dict(schema=CONSTELLATION_SCHEMA, constellations=[])
    if not path.exists(): return empty
    plain(path)
    try:
        if path.stat().st_size > CONSTELLATION_LIMITS['bytes']: raise ValueError('constellations: file too large')
        return validate_constellations(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('constellations: unreadable') from error


def collect(root, output_dir):
    CHECKED_DIRS.clear()
    root = plain(Path(root).absolute()).resolve()
    cfg = root/'brain.json'
    config = json.loads(cfg.read_text(encoding='utf-8-sig')) if cfg.exists() else {}
    vault = vault_path(root, config.get('vault',os.environ.get('CREATIVE_SECOND_BRAIN_VAULT','knowledge-base-vault')))
    plain(vault)
    if not vault.is_dir(): raise ValueError('Configured vault does not exist')
    sources_file = inside(vault,'_registry/sources.jsonl')
    if not sources_file.is_file(): raise ValueError('Source registry is missing; refusing to replace a valid graph')
    warnings=[]; provenance={}; nodes=[]; edges=[]
    def track(p):
        plain(p); provenance[p.relative_to(root).as_posix()] = digest(p)
    track(sources_file)
    if cfg.exists(): track(cfg)
    rows={}
    for line in sources_file.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip(): continue
        r=json.loads(line); sid=r.get('source_id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]+',sid): raise ValueError('Invalid source identity')
        if sid in rows: raise ValueError('Duplicate source identity: '+sid)
        rows[sid]=r
    for sid,r in sorted(rows.items()):
        if r.get('status') in {'deleted','retired','archived'}: continue
        p=inside(vault,r.get('source_note_path',''))
        if not p.is_file(): warnings.append({'id':sid,'reason':'missing canonical note'}); continue
        track(p); f,body=read_markdown(p)
        for media, _ in preview_files(vault, f): track(media)
        outcome=inside(vault,'outcome-pages/'+sid+'_outcome.html')
        href=''
        if outcome.is_file():
            track(outcome); href=quote(os.path.relpath(outcome,output_dir).replace('\\','/'),safe='/.-_')
        else: warnings.append({'id':sid,'reason':'missing outcome page; node retained without broken link'})
        title=scalar(f.get('title')) or sid
        nodes.append(dict(id=sid,title=title,label=title if len(title)<=23 else title[:22]+'…',kind='source',copy=scalar(f.get('summary')),date=scalar(f.get('capture_date')),searchText=scalar(r.get('source_url'))+' '+scalar(f.get('source_url')),href=href,sourcePath=p.relative_to(vault).as_posix(),color=PALETTE[stable(sid)%7],images=preview_images(vault,f,output_dir)))
    source_ids={n['id'] for n in nodes}; concepts=[]; title_map={}
    folder=inside(vault,'concepts')
    for p in sorted(folder.rglob('*.md')) if folder.exists() else []:
        plain(p)
        if not p.is_relative_to(vault): raise ValueError('Concept escapes vault')
        track(p); f,body=read_markdown(p)
        if f.get('concept_status')!='graph_node': continue
        rel=p.relative_to(vault).as_posix(); nid='idea-'+hashlib.sha256(rel.encode()).hexdigest()[:16]
        title=scalar(f.get('title')) or scalar(f.get('concept')) or p.stem
        sentence=re.search(r'## (?:一句話|定義來源)\s+([^\n]+)',body)
        n=dict(id=nid,title=title,label=title if len(title)<=23 else title[:22]+'…',kind='idea',ideaType=scalar(f.get('idea_type')),copy=sentence.group(1).removeprefix('- ') if sentence else scalar(f.get('graph_role')),question=scalar(f.get('graph_question')),sourcePath=rel,color=PALETTE[stable(nid)%7])
        nodes.append(n); concepts.append((n,f,body))
        for alias in {title,p.stem,rel[:-3]}: title_map.setdefault(alias,[]).append(nid)
    # Registry-managed relations supersede old generated concept links. Reading
    # them here also makes refresh-button/watcher updates safe before a graph
    # note rebuild; handwritten semantic bridges keep their existing meaning.
    registry = inside(vault, '_registry/relations.jsonl')
    relation_rows = []
    if registry.is_file():
        track(registry)
        relation_rows = [json.loads(line) for line in registry.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    kp_concepts = {}
    owned_paths = {n['sourcePath']:n['id'] for n,f,_ in concepts if f.get('graph_scope') == 'concept_links_only'}
    if relation_rows:
        try:
            from .obsidian_vault_adapter import projected_concept_note_path
        except ImportError:
            from obsidian_vault_adapter import projected_concept_note_path
        kp_registry = inside(vault, '_registry/knowledge_points.jsonl')
        if not kp_registry.is_file(): raise ValueError('Relations require the knowledge-point identity registry')
        track(kp_registry)
        for line in kp_registry.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip(): continue
            row = json.loads(line)
            kp_note = inside(vault,row['kp_note_path'])
            if not kp_note.is_file(): continue
            track(kp_note); fields,_ = read_markdown(kp_note)
            if fields.get('kp_uid') != row['kp_uid']: raise ValueError('Knowledge-point identity mismatch')
            point = str(fields.get('knowledge_point') or fields.get('definition') or kp_note.stem)
            concept_path = projected_concept_note_path(vault,fields,point)
            if concept_path is None: continue
            concept = concept_path.relative_to(vault).as_posix()
            if concept in owned_paths: kp_concepts[row['kp_uid']] = owned_paths[concept]
    managed_pairs, active_pairs = set(), set()
    active_rows = []
    for row in relation_rows:
        a,b = kp_concepts.get(row.get('source_kp_uid')),kp_concepts.get(row.get('target_kp_uid'))
        if not a or not b or a == b: continue
        pair = frozenset((a,b)); managed_pairs.add(pair)
        if row.get('status','linked') == 'linked':
            active_pairs.add(pair); active_rows.append((a,b,row))
    def link(a,b,kind,path,quote_text):
        if a==b:return
        pair = frozenset((a,b))
        # A generated concept note may still show a registry link that is no longer linked; the registry decides.
        if path in owned_paths and pair in managed_pairs and pair not in active_pairs:return
        edges.append(dict(a=a,b=b,type=kind,evidence='筆記明列此關係；未推論相似度或因果。',path=path,quote=quote_text))
    for n,f,body in concepts:
        for record in values(f.get('source_records')):
            # Longest exact identity prefix, rather than guessing source numbers.
            candidate=str(record)
            while candidate and candidate not in source_ids:
                candidate=candidate.rsplit('_',1)[0] if '_' in candidate else ''
            if candidate: link(candidate,n['id'],'查證來源',n['sourcePath'],'source_records: '+str(record))
        section=''; fenced=False
        for line in body.splitlines():
            if line.lstrip().startswith(('```','~~~')):fenced=not fenced;continue
            if fenced:continue
            if line.startswith('## '):section=line[3:].strip()
            if section not in SECTIONS:continue
            for target in NOTE_LINK_RE.findall(line):
                ids=title_map.get(target.removesuffix('.md'),[])
                if len(ids)==1:link(n['id'],ids[0],section,n['sourcePath'],line.strip())
        for line in values(f.get('related_concepts')):
            for target in NOTE_LINK_RE.findall(line):
                ids=title_map.get(target.removesuffix('.md'),[])
                if len(ids)==1:link(n['id'],ids[0],'相關概念',n['sourcePath'],'related_concepts: '+line)
    represented = {frozenset((e['a'],e['b'])) for e in edges}
    for a,b,row in active_rows:
        if frozenset((a,b)) not in represented:
            link(a,b,'相關概念','_registry/relations.jsonl',str(row.get('relation',''))+' '+str(row.get('evidence','')))
    edges=list({(e['a'],e['b'],e['type'],e['path'],e['quote']):e for e in edges}.values())
    degree={n['id']:0 for n in nodes}
    stats={n['id']:dict(semanticDegree=0,priorityDegree=0,against=0,**{'for':0}) for n in nodes}
    for e in edges:
        degree[e['a']]+=1;degree[e['b']]+=1
        family=e['family']=relation_family(e['type']);tier=e['tier']=relation_tier(e['type'])
        for end in (e['a'],e['b']):
            if family in SEMANTIC_FAMILIES:stats[end]['semanticDegree']+=1
            if tier in PRIORITY_TIERS:stats[end]['priorityDegree']+=1
            if family in AGAINST_FAMILIES:stats[end]['against']+=1
            if family=='support':stats[end]['for']+=1
    for n in nodes:
        n.update(stats[n['id']])
        n['tension']=n['against']>=1 and n['for']>=1
    # New stars come after the graph: arrange() places them without moving any other node, and their 孕育自 lines (origins) join the edges
    # only after the degree and semantic counts above, so no original node changes.
    stars, origins = thought_stars(vault, output_dir, nodes, track, warnings)
    try:
        saved_constellations = load_constellations(vault)['constellations']
        if constellations_path(vault).is_file(): track(constellations_path(vault))
    except (ValueError, OSError) as error:
        saved_constellations = []; warnings.append({'id': 'constellations', 'reason': 'constellations skipped: ' + str(error)})
    layout = arrange(nodes + stars, edges)
    # Sub-groups sit between top-level groups in layout['groups']; colours stay per top-level group.
    group_colors = {g['id']: PALETTE[i % len(PALETTE)] for i, g in enumerate(g for g in layout['groups'] if 'parent' not in g)}
    for n in nodes:
        n['degree']=degree[n['id']]
        if n['group'] != 'unlinked':
            n['color']=group_colors[n['group']]
    nodes.extend(stars);edges.extend(origins)
    nodes.sort(key=lambda n:(n.get('ideaType')!='hub',n['kind']!='thought',n['kind']!='idea',-n['degree'],n['id']))
    # Detect concurrent note edits before producing a replacement.
    for rel,sha in provenance.items():
        if digest(inside(root,rel))!=sha: raise ValueError('Vault changed during read; rerun after editing finishes')
    fingerprint=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()
    return dict(schema='knowledge_nebula_v1',nodes=nodes,edges=edges,layout=layout,sourceCount=len(source_ids),ideaCount=len(concepts),thoughtCount=len(stars),snapshot=fingerprint,provenance=provenance,warnings=warnings,displayName=config.get('display_name','創造型第二大腦'),catalogHref=quote(os.path.relpath(vault/'outcome-pages/knowledge.html',output_dir).replace('\\','/'),safe='/.-_'),thinkingHref=quote(os.path.relpath(vault/'outcome-pages/thinking.html',output_dir).replace('\\','/'),safe='/.-_'),constellations=saved_constellations,constellationLimits=dict(CONSTELLATION_LIMITS),constellationSchema=CONSTELLATION_SCHEMA,limits=dict(scene=96,list=40,relations=30),relationFamilies=[dict(id=f,label=l,types=list(k)) for f,l,k in RELATION_FAMILIES],relationTiers=[dict(tier=t,id=i,label=l,types=list(k)) for t,i,l,k in RELATION_TIERS]+[dict(tier=STRUCTURAL_TIER,id='structural',label='結構',types=[])],relationFilters=[dict(id=i,label=l,families=list(f)) for i,l,f in RELATION_FILTERS],relationshipMeaning='依明列連線形成主題群；距離是關係佈局，不是語義相似度分數。')

GUARD_DOCTOR_SECONDS = 5  # doctor takes 0.1-0.3 s; a cold start must not wait longer, so a longer silence means "cannot tell"


class ProjectionPaused(Exception):
    """A rebuild was not started because the reader layer has not been released through the gate.

    Deliberately not a ValueError: a pause is a decision, not a failed update.  It must never set ``update_error`` or take the
    reader service out of ``ready``; the existing pages keep being served.
    """

    def __init__(self, guard: dict):
        super().__init__(guard["reason"])
        self.guard = guard


def doctor_python(executable: str | None = None) -> str:
    """The interpreter for the doctor child: the console python.exe next to a windowless pythonw.exe (``開啟知識星雲.cmd`` starts
    pythonw.exe).  Pipes also work with pythonw on the Python 3.13 tested here; the console twin is the safe choice on any runtime."""
    path = Path(executable or sys.executable)
    if path.name.lower() == "pythonw.exe" and path.with_name("python.exe").is_file():
        return str(path.with_name("python.exe"))
    return str(path)


def _guard_answer(allowed: bool, kind: str, files: list[str], reason: str = "", notice: str = "") -> dict:
    # ``reason`` is the full sentence (status, log); ``notice`` is the short one the sync button shows.
    return {"allowed": allowed, "kind": kind, "files": files, "reason": reason, "notice": notice}


def projection_guard(root: Path, *, timeout: float = GUARD_DOCTOR_SECONDS) -> dict:
    """The one answer to "may the reader layer be rebuilt now?", shared by the reader service (cold start, the watcher and the sync
    button) and by ``refresh_installed`` (every tool that rebuilds an installed nebula directly).

    A rebuild writes the catalogue, the portal, the outcome-page shells and the nebula from the template and code on disk at that
    moment.  So the answer is NO while
    ``tools/brain.py doctor`` lists any managed runtime file as changed or missing.  The scope is exactly doctor's: every file of
    brain.json ``managed_files`` (the nebula template and shaders, knowledge_nebula*.py, reader_shell.py, reader_theme.css,
    outcome_*.py, thinking_archive.py, completion_revisions.py, compiler_worker_v2.py, obsidian_vault_adapter.py ... and also the
    managed docs and skills), with no second list to keep in step.  The answer is also NO whenever doctor cannot answer: it cannot
    start, runs past ``timeout``, prints no JSON, or reports a configuration error.

    A folder whose brain.json declares no ``managed_files`` is not a managed workspace, so YES.  The existing setup, management,
    and managed-update flows establish the expected managed-file state after their own validation; after that doctor is ready.
    This module owns no update path and only uses doctor as the shared readiness answer.

    Returns ``{allowed, kind, files, reason, notice}``; ``kind`` is ready, unmanaged, unregistered or unverifiable.
    """
    root = Path(root)

    def unverifiable(detail: str) -> dict:
        return _guard_answer(False, "unverifiable", [],
                             "暫時無法確認閱讀層程式是否完整受管（" + detail + "），已暫停重建，現有頁面保持原樣；請由工作區助手檢查並完成現有受管更新或修復。",
                             "暫時無法確認閱讀層程式是否完整受管，暫停同步；請由工作區助手檢查後完成現有受管更新或修復。")

    def listed(names: list[str]) -> str:
        return "、".join(names[:5]) + (f" 等共 {len(names)} 個" if len(names) > 5 else "")

    try:
        config = json.loads((root / "brain.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return unverifiable("brain.json 無法讀取")
    if not isinstance(config, dict):
        return unverifiable("brain.json 格式不正確")
    if not config.get("managed_files"):
        return _guard_answer(True, "unmanaged", [])
    brain = root / "tools" / "brain.py"
    if not brain.is_file():
        return unverifiable("tools/brain.py 不存在")
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    try:
        # stdin is closed explicitly: a windowless (pythonw) parent has no usable standard handles to inherit.
        done = subprocess.run([doctor_python(), "-B", str(brain), "doctor"], cwd=root, stdin=subprocess.DEVNULL, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=dict(os.environ, PYTHONUTF8="1"), check=False,
                              **options)
    except subprocess.TimeoutExpired:
        return unverifiable(f"doctor 超過 {int(timeout)} 秒沒有回答")
    except OSError as error:
        return unverifiable("doctor 無法啟動：" + type(error).__name__)
    try:
        result = json.loads(done.stdout)
    except ValueError:
        return unverifiable("doctor 沒有輸出可讀的結果")
    modified = result.get("modified_runtime_files") if isinstance(result, dict) else None
    missing = result.get("missing") if isinstance(result, dict) else None
    if not isinstance(modified, list) or not isinstance(missing, list):
        return unverifiable("doctor 的結果格式不正確")
    if result.get("configuration_error"):
        return unverifiable("doctor 回報設定錯誤")
    if modified:
        files = [str(item) for item in (*modified, *missing)]
        return _guard_answer(False, "unregistered", files,
                             f"閱讀層有需要處理的受管程式差異（{listed(files)}），已暫停重建，現有頁面保持原樣；請由工作區助手使用現有受管更新或修復流程處理後再更新。",
                             "閱讀層有需要處理的受管程式差異，暫停同步；請由工作區助手使用現有受管更新或修復流程處理。")
    if missing:
        return unverifiable("缺少程式檔 " + listed([str(item) for item in missing]))
    if result.get("status") != "ready" or done.returncode != 0:
        return unverifiable("doctor 回報 " + str(result.get("status")))
    return _guard_answer(True, "ready", [])


def record_projection_pause(root: Path, guard: dict) -> None:
    """Leave the reason in .reader-runtime/last-error.log, where the workspace assistant already looks.  Best effort: the pause
    itself never depends on being able to write it."""
    try:
        path = plain(Path(root) / ".reader-runtime" / "last-error.log")
        path.parent.mkdir(exist_ok=True)
        lines = [time.strftime("%Y-%m-%d %H:%M:%S"), "reader projection paused (nothing was rebuilt): " + guard["reason"],
                 *("  " + name for name in guard["files"])]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        pass


def refresh_installed(vault, output_dir):
    """Update an already installed nebula after the normal outcome transaction.

    The nearest workspace marker must name this exact vault. Never install a
    graph in an unrelated fixture, export folder or neighbouring workspace.
    Errors propagate to the caller; the previous atomic projection survives.

    Before it builds, it asks ``projection_guard`` the same question the reader service asks: may the reader layer be rebuilt now?
    While managed runtime files are changed but not registered through the release gate (or doctor cannot answer), nothing is
    written: the result is ``{'status': 'paused', 'reason': ..., 'files': [...]}`` instead of a build result, the reason is left in
    ``.reader-runtime/last-error.log``, and no exception is raised.  A caller must not report a pause as an update.
    """
    vault = plain(Path(vault).absolute()).resolve()
    target = vault/'outcome-pages/nebula.html'
    if Path(output_dir).resolve() != target.parent or not target.is_file(): return None
    if MARKER not in target.read_text(encoding='utf-8')[:200]: return None
    for root in vault.parents:
        marker = plain(root/'brain.json')
        if marker.is_file():
            config = json.loads(marker.read_text(encoding='utf-8-sig'))
            if vault_path(root, config.get('vault', 'knowledge-base-vault')) != vault:
                raise ValueError('Workspace marker does not own the selected vault')
            guard = projection_guard(root)
            if not guard['allowed']:
                record_projection_pause(root, guard)
                return {'status': 'paused', 'reason': guard['reason'], 'files': list(guard['files'])}
            return build(root)
    return None


def input_stamp(root):
    """Cheap local change detection; canonical content is validated by build()."""
    root = plain(Path(root).absolute()).resolve()
    marker = plain(root/'brain.json')
    config = json.loads(marker.read_text(encoding='utf-8-sig')) if marker.exists() else {}
    vault = vault_path(root, config.get('vault', 'knowledge-base-vault'))
    registry = inside(vault, '_registry/sources.jsonl')
    paths = {marker, registry, inside(vault, '_registry/relations.jsonl'), inside(vault, '_registry/knowledge_points.jsonl'), ASSETS/'template.html', ASSETS/'shaders.js',
             Path(__file__).with_name('reader_visual.css'), Path(__file__).with_name('reader_passage.js')}
    for line in registry.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip(): continue
        row = json.loads(line)
        note = inside(vault, row['source_note_path'])
        paths.add(note)
        if note.is_file():
            fields, _ = read_markdown(note)
            paths.update(media for media, _ in preview_files(vault, fields))
        sid = row['source_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', sid): raise ValueError('Invalid source identity')
        paths.add(inside(vault, f'outcome-pages/{sid}_outcome.html'))
    folder = inside(vault, 'concepts')
    if folder.exists(): paths.update(folder.rglob('*.md'))
    kp_folder = inside(vault, 'knowledge-points/by-source')
    if kp_folder.exists(): paths.update(kp_folder.glob('*/*.md'))
    # New thinking records (and their reader pages) are noticed as soon as they appear; a thinking folder that cannot be read is a build
    # warning (collect), never a failure of this cheap check.
    try:
        think = inside(vault, 'thinking')
        records = sorted(think.glob('ASK-*.json')) if think.is_dir() else []
    except (ValueError, OSError):
        records = []
    for record in records:
        try:
            plain(record); paths.update((record, inside(vault, 'outcome-pages/thinking/' + record.stem + '.html')))
        except (ValueError, OSError): continue
    stamps = []
    for path in sorted(paths):
        plain(path)
        try:
            info = path.stat(); stamps.append((str(path), info.st_mtime_ns, info.st_size))
        except FileNotFoundError: stamps.append((str(path), None, None))
    return tuple(stamps)


def build(root, output=None):
    CHECKED_DIRS.clear()
    root=plain(Path(root).absolute()).resolve()
    config=json.loads((root/'brain.json').read_text(encoding='utf-8-sig')) if (root/'brain.json').exists() else {}
    default=str(Path(config.get('vault','knowledge-base-vault'))/'outcome-pages/nebula.html')
    target=inside(root,output or default)
    if target.suffix!='.html': raise ValueError('Output must be an HTML file')
    if target.exists() and MARKER not in target.read_text(encoding='utf-8')[:200]:
        raise ValueError('Refusing to overwrite a file not owned by this generator')
    started=time.perf_counter(); data=collect(root,target.parent)
    template=(ASSETS/'template.html').read_text(encoding='utf-8')
    shaders=(ASSETS/'shaders.js').read_text(encoding='utf-8')
    encoded=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    fallback=''.join('<li><a href="'+html.escape(n['href'],quote=True)+'">'+html.escape(n['title'])+'</a></li>' for n in data['nodes'] if n.get('href'))
    page=MARKER+'\n'+template.replace('/*__DATA__*/','const DATA='+encoded+';').replace('/*__SHADERS__*/',shaders).replace('<!--FALLBACK-->',fallback)
    try:
        from .reader_shell import apply_visual
    except ImportError:
        from reader_shell import apply_visual
    page=apply_visual(page, 'nebula')
    changed=not target.exists() or target.read_text(encoding='utf-8')!=page
    if changed:
        target.parent.mkdir(parents=True,exist_ok=True)
        temp=target.with_name(target.name+'.tmp-'+str(os.getpid()))
        with temp.open('x',encoding='utf-8',newline='\n') as f:f.write(page)
        os.replace(temp,target)
    return dict(status='updated' if changed else 'unchanged',output=target.relative_to(root).as_posix(),sources=data['sourceCount'],concepts=data['ideaCount'],edges=len(data['edges']),snapshot=data['snapshot'],warnings=data['warnings'],bytes=len(page.encode()),seconds=round(time.perf_counter()-started,3),sha256=digest(target))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path.cwd());p.add_argument('--output',help='Workspace-relative generated HTML')
    p.add_argument('--watch',action='store_true',help='Foreground local refresh; Ctrl+C stops. No server or scheduler.')
    p.add_argument('--interval',type=float,default=5);a=p.parse_args()
    if a.interval<1:p.error('interval must be at least 1 second')
    try:
        while True:
            try: print(json.dumps(build(a.root,a.output),ensure_ascii=False),flush=True)
            except (ValueError,OSError,KeyError,TypeError) as e:
                print(json.dumps({'status':'blocked','reason':str(e)},ensure_ascii=False),flush=True)
                if not a.watch:return 2
            if not a.watch:return 0
            time.sleep(a.interval)
    except KeyboardInterrupt:return 0

if __name__=='__main__':raise SystemExit(main())

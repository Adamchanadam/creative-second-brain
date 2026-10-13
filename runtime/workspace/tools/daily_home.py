"""Shared daily entry for OPS and portable workspaces. Pure rendering; no writes."""
from pathlib import Path, PureWindowsPath
from urllib.parse import quote
import html
import json

COMMON_OPERATIONS = [
 ('guide','開啟第二大腦首頁','查看目前使用版本，或直接開啟已同步的第二大腦首頁與成果總覽。','開啟我的第二大腦首頁。'),
 ('url','保存文章、論文或影片字幕','保存公開文章、論文或已提交授權的 YouTube 字幕；不可把摘要或影片描述當全文。','把這篇文章完整入庫：網址'),
 ('text','保存完整文字','保存完整文字，保留原文、來源與你的理解分層。','把以下完整文字入庫；如有附圖，請保留原圖。'),
 ('image','保存一張原圖','把一張原圖作為知識來源，保留原圖並標示看不清的內容。','把這張原圖作為知識來源入庫。'),
 ('think','思考並保存紀錄','根據庫內資料深入分析問題，保存可追溯的思考紀錄。','思考並保存紀錄：我想深入分析……'),
 ('create','用知識創作','根據已保存材料製作比較、報告或創作；新作品不會自動入庫。','用庫內材料幫我製作……，讀者是……，用途是……。'),
 ('grow','評估探索來源','先評估你選定的探索來源能否補充知識庫；適合才進入正式保存。','評估我選定的探索來源，適合便正式入庫：來源'),
 ('nebula','開啟知識星雲','直接開啟已同步的知識星雲，以關係探索已保存的知識。','開啟我的知識星雲。'),
]
RESEARCH_OPERATION = ('research','研究主題','先查看或選定研究主題，再按使用者直接要求建立、續做或調整；PUBLIC 不提供背景排程。','查看目前研究主題')
EXTRAS = [
 ('resume','接續中斷工作','找回同一身份的中斷工作，保留已合格成果。',''),
 ('review','核對指定成果','唯讀核對指定入庫成果和完成證據；不修復或重建。',''),
 ('fix','修復指定問題','修復指定知識成果、圖片或閱讀頁問題；不做全庫整理。',''),
 ('tidy','整理知識與目錄','提出可連結、可覆核的整理建議，按需要更新知識目錄。','檢視目前知識庫，提出整理建議；若我指定閱讀頁或目錄，請更新它。'),
]
OPS_SKILLS = {'think':'brain-think','url':'brain-url','text':'brain-text','image':'brain-image','grow':'brain-grow','create':'brain-create','research':'brain-research','tidy':'brain-tidy','guide':'brain-guide','nebula':'brain-nebula','review':'brain-check','resume':'brain-resume','fix':'brain-fix'}

def think_alias_markup(assets: Path) -> str:
    """Render the one legacy alias compiled from the package command registry."""
    try:
        payload = json.loads((assets / 'command-aliases.json').read_text(encoding='utf-8'))
        alias = payload['aliases']['think']
        title, prompt, purpose = (alias[key] for key in ('home_title', 'host_prompt', 'purpose'))
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError('compiled think alias metadata is unavailable') from error
    if payload.get('schema') != 'creative_second_brain_home_aliases_v1' or any(
        not isinstance(value, str) or not value.strip() for value in (title, prompt, purpose)
    ):
        raise ValueError('compiled think alias metadata is invalid')
    return '<p class="command-alias"><strong>'+html.escape(title)+'</strong> <code>'+html.escape(prompt)+'</code><br><span>'+html.escape(purpose)+'</span></p>'

def safe_relative(value: str) -> str:
    value=value.replace('\\','/')
    if not value or value.startswith('/') or PureWindowsPath(value).drive or '..' in value.split('/') or value=='.':
        raise ValueError('expected a workspace-relative path')
    return value

def validate_template(assets: Path) -> None:
    data=json.loads((assets/'version.json').read_text(encoding='utf-8'))
    if data.get('schema')!='creative_second_brain_home_v1':raise ValueError('unknown home version schema')
    if not isinstance(data.get('version'),str) or not data['version'].strip():raise ValueError('invalid home template version')

def render(*, assets: Path, vault_rel: str, profile: str, prompts: dict | None=None, package_version: str | None=None) -> str:
    if profile not in ('ops','portable'):raise ValueError('unknown home profile')
    if not isinstance(package_version,str) or not package_version.strip():raise ValueError('workspace package version is required')
    validate_template(assets)
    vault_rel=safe_relative(vault_rel);v=html.escape(quote(vault_rel,safe='/'),quote=True)
    esc=html.escape
    def card(key,title,description,prompt):
        ident='maintain' if profile=='ops' and key=='tidy' else key
        # A portable workspace's daily surface is always its local
        # brain-* entries.  The package skill is an install/release artifact,
        # never a daily slash-command dependency.
        skill=OPS_SKILLS[key]
        link='.agents/skills/'+skill+'/SKILL.md'
        command=prompts[key] if profile=='ops' else '$'+skill+' '+prompt
        alias=think_alias_markup(assets) if key=='think' else ''
        return ('<details class="prompt" id="p-'+ident+'" data-skill="'+skill+'"><summary>'+esc(title)+'</summary><div class="prompt-body"><p>'+esc(description)+'</p>'+alias+'<textarea readonly aria-label="'+esc(title)+'指令">'+esc(command)+'</textarea><button class="copy" type="button">複製指令</button><span class="status-line" role="status" aria-live="polite"></span><p class="source"><a data-source href="'+link+'">操作說明</a></p></div></details>')
    operations = COMMON_OPERATIONS[:7] + ([RESEARCH_OPERATION] if profile == 'ops' else []) + COMMON_OPERATIONS[7:]
    all_operations = operations[:8] + (EXTRAS if profile=='ops' else []) + operations[8:]
    commands=''.join(card(*op) for op in all_operations)
    destinations=[('index.html','成果總覽','查看最近的知識與想法'),('knowledge.html','知識成果','搜尋文章、筆記與圖片'),('thinking.html','思考紀錄','找回問題、論證及下一步想法')]
    destinations.append(('nebula.html','知識星雲','沿著關係探索知識，或靜觀星雲時鐘'))
    links=[(v+'/outcome-pages/'+f,t,d) for f,t,d in destinations]
    nav='<a class="nav" href="#main" aria-current="page">工作首頁</a>'+''.join('<a class="nav" href="'+u+'">'+t+'</a>' for u,t,_ in links)+'<a class="nav" href="#prompts">常用操作</a>'
    core_destinations=''.join('<a class="micro-card" href="'+u+'"><span><b>'+t+'</b><small>'+d+'</small></span><i aria-hidden="true">→</i></a>' for u,t,d in links if t in {'知識成果','思考紀錄'})
    nebula_destination=next('<a class="nebula-cta" href="'+u+'">進入星雲 <span aria-hidden="true">↗</span></a>' for u,t,_ in links if t=='知識星雲')
    quick_keys={'think','nebula','guide'}
    quick_operations=''.join('<a class="command-card" href="#p-'+('maintain' if profile=='ops' and key=='tidy' else key)+'"><strong>$'+OPS_SKILLS[key]+'</strong><small>'+esc(title)+'</small><span aria-hidden="true">→</span></a>' for key,title,_,_ in all_operations if key in quick_keys)
    refs=[('docs/成果頁索引資料層設計.md','成果與閱讀入口')]
    if profile=='ops':
        refs += [('docs/Obsidian語義索引與關聯圖落地方案.md','提問與知識整理'),('docs/URL到輸出模板規格.md','材料保存與成果')]
        setup='<p>開啟本工作區的知識庫，在 Obsidian 閱讀筆記，或從上方目錄閱讀 HTML 成果。</p>'
    else:
        refs += [('guide-references/first-use.md','安裝與重新接入'),('guide-references/setup.md','更新與搬移'),('guide-references/workflows.md','各項操作流程'),('docs/Obsidian操作與無記憶接續.md','Obsidian 設置圖解')]
        setup='<p>需要重新接上 Obsidian 時，按設置圖解操作並讓 AI 核對目標知識庫。</p>'
    help_body='<div class="help-grid"><article><h3>開啟資料</h3>'+setup+'<p><a href="'+v+'/'+quote('成果頁總覽.md')+'">Obsidian 閱讀入口</a></p></article><article><h3>備份與搬移</h3><p>備份整個工作區，包括知識庫、圖片、輸入材料及未完成工作。搬移後讓 AI 核對新位置，再繼續使用。</p></article></div><ul class="source-list">'+''.join('<li><a data-source href="'+esc(quote(s,safe='/'))+'">'+t+'</a></li>' for s,t in refs)+'</ul>'
    ver='目前使用：創造型第二大腦 v'+esc(package_version.strip())
    body=(assets/'daily-home.html').read_text(encoding='utf-8')
    research_quick='<a href="#p-research">研究主題 →</a>' if profile == 'ops' else ''
    for k,value in {'TITLE':esc(vault_rel.split('/')[-1]),'NAV':nav,'VERSION':ver,'VAULT':esc(vault_rel),'CORE_DESTINATIONS':core_destinations,'NEBULA_DESTINATION':nebula_destination,'QUICK_OPERATIONS':quick_operations,'COMMANDS':commands,'COMMAND_COUNT':str(len(all_operations)),'RESEARCH_QUICK':research_quick,'HELP':help_body,'COMPAT_ANCHORS':'','FINGERPRINTS':'<script type="application/json" id="source-fingerprints">{}</script>' if profile=='ops' else ''}.items():
        body=body.replace('@@'+k+'@@',value)
    if '@@' in body:raise ValueError('unresolved home template')
    try:
        from .reader_shell import apply_visual
    except ImportError:
        from reader_shell import apply_visual
    return apply_visual(body, 'home')

def compatibility_redirect() -> str:
    mapping={'purpose':'help','loop':'help','features':'prompts','studio':'prompts','reading':'main','maintenance':'help','limits':'help','sources':'help'}
    return '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>工作首頁已移至 index.html</title><p><a href="index.html">開啟我的第二大腦</a></p><script>const map='+json.dumps(mapping)+';const key=location.hash.slice(1);location.replace("index.html"+location.search+(key?"#"+(map[key]||key):""));</script></html>'

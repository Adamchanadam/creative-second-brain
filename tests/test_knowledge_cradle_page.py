"""星之搖籃 page: the ① 相遇 star draw (pools, roles, rarity, ceremony, card, locks, 主角, dex) and the ③ 誕生 background incubation panel.

Browser checks (Playwright through node; a skip is not a pass) against temporary workspaces only: a seeded vault for the draw, and the
thinking-record fixture served by a real NebulaServer with the deterministic fake runner for the incubation jobs. Nothing here writes to a
real vault, contacts the live reader service or runs the real claude or codex programs.
"""
import datetime
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'runtime' / 'workspace').is_dir():
    ROOT = ROOT / 'runtime' / 'workspace'
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(TESTS))
import knowledge_nebula as nebula
import knowledge_nebula_server as server_module
import outcome_page_generator as generator
import thinking_archive as archive
from knowledge_nebula_server import NebulaServer
import cradle_fixture

FAKE = TESTS / 'cradle_fake_claude.py'
FAKE_CODEX = TESTS / 'cradle_fake_codex.py'
SHOTS = Path(tempfile.gettempdir()) / 'cradle-page-shots'
GL_ARGS = "['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader']"


def build_rich_vault(root, concepts=150, groups=6, seed=7, tension=True, vault_rel='vault'):
    """A vault that draws many different sets: hubs, concepts with supports, extensions, counterexamples, limits and trade-offs, outcomes dated
    over four months (the newest within days of 2026-10-20) and a good share of sparse concepts that cite the newest outcomes."""
    rng = random.Random(seed)
    vault = root / vault_rel
    for sub in ('_registry', 'concepts', 'sources', 'outcome-pages'):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    (root / 'brain.json').write_text(json.dumps({'vault': vault_rel}), encoding='utf-8')
    base = datetime.date(2026, 10, 20)
    rows, source_ids = [], []
    for i in range(40):
        sid = f'KS-R-{i:02d}'
        date = (base - datetime.timedelta(days=i * 3 + rng.randint(0, 2))).isoformat()
        (vault / 'sources' / f'{sid}.md').write_text(f'---\ntitle: "來源{i}"\nsummary: "摘要"\ncapture_date: "{date}"\n---\n正文', encoding='utf-8')
        rows.append({'source_id': sid, 'source_note_path': f'sources/{sid}.md', 'status': 'linked'})
        source_ids.append(sid)
    (vault / '_registry/sources.jsonl').write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
    names = [f'概{g}-{k}' for g in range(groups) for k in range(concepts // groups)]

    def write(name, sections, records, hub, kind=''):
        fields = [f'title: {json.dumps(name, ensure_ascii=False)}', 'concept_status: "graph_node"']
        if kind:
            fields.append(f'idea_type: "{kind}"')
        if records:
            fields.append('source_records:')
            fields += [f'  - "{r}"' for r in records]
        if hub:
            sections = {**sections, '連到 Hub': [hub]}
        body = ''.join(f'## {head}\n' + ''.join(f'- [[{t}]]\n' for t in items) for head, items in sections.items())
        (vault / 'concepts' / f'{name}.md').write_text('---\n' + '\n'.join(fields) + f'\n---\n## 一句話\n{name}的一句話。\n{body}', encoding='utf-8')

    for g in range(groups):
        write(f'樞{g}', {}, (), '', 'hub')
    for g in range(groups):
        for k in range(concepts // groups):
            name = f'概{g}-{k}'
            sparse = rng.random() < .35
            sections = {}
            if not sparse:
                near = [n for n in names if n.startswith(f'概{g}-') and n != name]
                far = [n for n in names if not n.startswith(f'概{g}-')]
                pick = lambda count: [rng.choice(near if rng.random() < .65 else far) for _ in range(count)]
                sections['支持'] = list(dict.fromkeys(pick(rng.randint(1, 2))))
                if rng.random() < .4:
                    sections['延伸'] = list(dict.fromkeys(pick(1)))
                if tension and rng.random() < .28:
                    sections[rng.choice(['反例', '限制'])] = list(dict.fromkeys(pick(1)))
                if rng.random() < .1:
                    sections['形成取捨'] = list(dict.fromkeys(pick(1)))
                if rng.random() < .4:
                    sections['相關概念'] = list(dict.fromkeys(pick(2)))
            recent = sparse and rng.random() < .5
            records = [source_ids[rng.randint(0, 5) if recent else rng.randint(6, 39)]]
            write(name, sections, records, f'樞{g}')
    return vault


def build_tiny_vault(root, concepts=2, vault_rel='vault'):
    vault = root / vault_rel
    for sub in ('_registry', 'concepts', 'sources', 'outcome-pages'):
        (vault / sub).mkdir(parents=True, exist_ok=True)
    (root / 'brain.json').write_text(json.dumps({'vault': vault_rel}), encoding='utf-8')
    (vault / 'sources/KS-T-1.md').write_text('---\ntitle: "來源"\nsummary: "摘要"\ncapture_date: "2026-10-01"\n---\n正文', encoding='utf-8')
    (vault / '_registry/sources.jsonl').write_text(json.dumps({'source_id': 'KS-T-1', 'source_note_path': 'sources/KS-T-1.md', 'status': 'linked'}), encoding='utf-8')
    for i in range(concepts):
        (vault / 'concepts' / f'小{i}.md').write_text(f'---\ntitle: "小{i}"\nconcept_status: "graph_node"\nsource_records:\n  - "KS-T-1"\n---\n## 一句話\n小{i}。\n', encoding='utf-8')
    return vault


PRE = r"""
const [,, playwrightDir, pageUrl, optionsJson] = process.argv;
const opt = JSON.parse(optionsJson || '{}');
const { chromium } = require(playwrightDir);
const fs = require('fs');
const result = { errors: [] };
const SEED = `(() => { let a = ${opt.seed || 12345}; window.__nebulaRandom = () => { a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; })();`;
const KNOWN = () => { try { localStorage.setItem('nebula-hint-v1', '1'); localStorage.setItem('nebula-seen-stars-v1', JSON.stringify(Array.from({ length: 10 }, (_, i) => 'ASK-' + String(i).repeat(32)))); } catch (e) {} };
const launch = () => chromium.launch({ args: __GL__ });
const open = async (browser, width, height, o = {}) => {
  const context = await browser.newContext({ viewport: { width, height }, hasTouch: width < 700, isMobile: width < 700, reducedMotion: o.reduced ? 'reduce' : 'no-preference' });
  await context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
  if (!o.noKnown) await context.addInitScript(KNOWN);
  if (o.seeded !== false) await context.addInitScript(SEED);
  const page = await context.newPage();
  page.on('pageerror', e => result.errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error' && !(opt.allow404 && /status of 404/.test(m.text())) && !(opt.allow409 && /status of 409/.test(m.text()))) result.errors.push(m.text()); });
  await page.goto(o.url || pageUrl);
  await page.waitForFunction(() => typeof state !== 'undefined' && state.gpu === true);
  await page.evaluate(() => { stopCameraTour(); reset(); });
  return page;
};
// Retries while the page reloads itself (the execution context is gone for a moment); true when fn() was truthy.
const until = async (page, fn, ms = 60000, arg) => {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { const v = await page.evaluate(fn, arg); if (v) return v; } catch (e) { /* navigating */ }
    await new Promise(r => setTimeout(r, 120));
  }
  return false;
};
const waitCard = (page, ms = 60000) => page.waitForFunction(() => encounter && encounter.phase === 'card', null, { timeout: ms });
const drawNow = async (page, opts = {}) => {
  await page.evaluate(() => { if (!basketOpen) openBasket(); });
  if (opts.pool) await page.click(`#enc-pools [data-pool=${opts.pool}]`);
  await page.click('#surprise');
  if (opts.wait !== false) await waitCard(page);
};
const snapshot = page => page.evaluate(() => ({
  step: ai.step,
  phase: encounter ? encounter.phase : null,
  ids: basket.map(id => byId.get(id).title),
  roles: encounter ? encounter.slots.map(s => [ENCOUNTER_ROLES[s.role].label, ENCOUNTER_ROLES[s.why].label]) : null,
  locked: encounter ? encounter.slots.map(s => s.locked) : null,
}));
const cardText = page => page.evaluate(() => ({
  badge: $('#enc-badge').textContent, score: $('#enc-score').textContent,
  meters: [...document.querySelectorAll('#enc-meters li')].map(li => [li.querySelector('b').textContent, li.querySelector('.enc-bar i').style.width, li.lastElementChild.textContent]),
  rows: [...document.querySelectorAll('#enc-card-slots li')].map(li => ({ role: li.querySelector('.role').textContent, name: li.querySelector('.name').textContent, meta: li.querySelector('.enc-meta').textContent, lock: li.querySelector('.enc-lock').textContent.trim(), pressed: li.querySelector('.enc-lock').getAttribute('aria-pressed'), disabled: li.querySelector('.enc-lock').disabled })),
  seeds: [...document.querySelectorAll('#enc-seeds .enc-seed')].map(b => b.lastChild.textContent),
  seedLabels: [...document.querySelectorAll('#enc-seeds .enc-seed .q-dir')].map(b => b.textContent),
  suggestions: suggestions.think.slice(0, 2).map(s => s.text),
  reroll: { text: $('#enc-reroll').textContent.trim(), disabled: $('#enc-reroll').disabled, hidden: $('#enc-reroll').hidden },
  next: $('#step-next').textContent.trim(), short: $('#enc-short').hidden ? '' : $('#enc-short').textContent,
  head: $('#enc-card-slots').closest('.enc-card').querySelector('.enc-seeds h4').textContent,
}));
const out = () => console.log(JSON.stringify(result));
const run = main => (async () => { const browser = await launch(); try { await main(browser); } finally { await browser.close(); } out(); })().catch(e => { console.error(e); process.exit(1); });
""".replace('__GL__', GL_ARGS)


class PageCase(unittest.TestCase):
    def setUp(self):
        module = os.environ.get('NEBULA_PLAYWRIGHT', '')
        node = shutil.which('node')
        if not node or not Path(module, 'package.json').is_file():
            self.skipTest('playwright not available; set NEBULA_PLAYWRIGHT to a playwright package folder (a skip is not a pass)')
        self.node, self.module = node, module
        self.work = tempfile.TemporaryDirectory(prefix='cradle-page-')
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        SHOTS.mkdir(parents=True, exist_ok=True)

    def node_run(self, body, url, timeout=900, **options):
        path = self.root / 'check.cjs'
        path.write_text(PRE + body, encoding='utf-8')
        done = subprocess.run([self.node, str(path), self.module, url, json.dumps({'shots': str(SHOTS), **options})],
                              capture_output=True, text=True, timeout=timeout, encoding='utf-8')
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        r = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(r['errors'], [], r['errors'])
        return r

    def rich_page(self, **kwargs):
        vault = build_rich_vault(self.root, **kwargs)
        nebula.build(self.root)
        return (vault / 'outcome-pages/nebula.html').as_uri()


ROLE_LABELS = {'tension': '拉扯者', 'fresh': '新秀', 'far': '遠客', 'veteran': '老將', 'hero': '主角', 'hot': '連結多', 'latest': '新入庫', 'other': '遠方', 'random': '隨機'}

DRAW_SCRIPT = r"""
run(async browser => {
  const page = await open(browser, 1440, 900);
  // Nothing is simulated at load; the pool selector shows only while the basket is empty.
  await page.evaluate(() => openBasket());
  result.load = await page.evaluate(() => ({ stats: Object.keys(window.__nebulaEncounterStats), pools: [...document.querySelectorAll('#enc-pools button')].map(b => [b.textContent, b.getAttribute('aria-checked')]), tip: $('#enc-pool-tip').textContent, button: $('#surprise').textContent, buttonHidden: $('#surprise').hidden, empty: $('#basket-empty').textContent, step: ai.step, label: $('#enc-pools').getAttribute('aria-label') }));
  result.tips = {};
  for (const pool of ['cross', 'clash', 'fresh']) { await page.click(`#enc-pools [data-pool=${pool}]`); result.tips[pool] = await page.evaluate(() => [$('#enc-pool-tip').textContent, document.querySelector('#enc-pools [aria-checked=true]').textContent]); }
  // Each pool, one draw: roles in order, the card, the independent re-computation of the score.
  result.pools = {};
  for (const pool of ['cross', 'clash', 'fresh']) {
    await page.evaluate(() => { if (encounter) { basket = []; basketChanged(); } openBasket(); });
    await page.click(`#enc-pools [data-pool=${pool}]`);
    await page.click('#surprise');
    result.pools[pool] = { during: await page.evaluate(() => ({ phase: encounter.phase, drawHead: $('#enc-draw-head').textContent, skip: $('#enc-skip').textContent, rows: [...document.querySelectorAll('#enc-live li')].map(li => [li.querySelector('.role').textContent, li.querySelector('.name').textContent]), cardHidden: $('#enc-card').hidden, startHidden: $('#enc-start').hidden, actionsHidden: $('#bp-actions1').hidden })) };
    await waitCard(page);
    result.pools[pool].after = await snapshot(page);
    result.pools[pool].card = await cardText(page);
    result.pools[pool].score = await page.evaluate(() => {
      const ids = encounter.slots.map(s => s.id), ns = ids.map(id => byId.get(id)), main = n => { const g = groupById.get(n.group); return g && g.parent ? g.parent : n.group; };
      const groups = new Set(ns.map(main).filter(g => g && g !== 'unlinked')).size;
      const joined = (a, b) => allEdges.some(e => ((e.a === a && e.b === b) || (e.a === b && e.b === a)) && (e.family || 'structural') !== 'structural');
      const pairs = [[0, 1], [1, 2], [0, 2]].filter(([i, j]) => joined(ids[i], ids[j])).length;
      const tens = ns.filter(n => n.tension).length;
      const touches = x => ids.filter(id => allEdges.some(e => ((e.a === x.id && e.b === id) || (e.a === id && e.b === x.id)) && (e.family || 'structural') !== 'structural')).length;
      const bridge = allNodes.some(x => !ids.includes(x.id) && x.kind !== 'thought' && touches(x) >= 2);
      const concepts = allNodes.filter(n => n.kind === 'idea'), mid = [0, 1, 2].map(i => concepts.reduce((s, n) => s + n.p[i], 0) / concepts.length);
      const D = 2 * Math.max(...concepts.map(n => Math.hypot(n.p[0] - mid[0], n.p[1] - mid[1], n.p[2] - mid[2])));
      const d = (a, b) => Math.hypot(a.p[0] - b.p[0], a.p[1] - b.p[1], a.p[2] - b.p[2]);
      const spread = Math.min(1, Math.max(0, (d(ns[0], ns[1]) + d(ns[1], ns[2]) + d(ns[0], ns[2])) / (3 * D)));
      const expect = Math.max(0, groups - 1) + (pairs === 0 ? 1.5 : pairs === 1 ? .5 : 0) + tens * .6 + (bridge ? 1.2 : 0) + spread;
      const r = encounter.rar;
      return { expect, got: r.score, groups: [groups, r.groups], pairs: [pairs, r.joined], tens: [tens, r.tens], bridge: [bridge, !!r.bridge], pct: r.pct, tier: r.tier, percent: r.percent, step: ai.step, tierName: ENCOUNTER_TIERS[r.tier].name };
    });
  }
  // The stay on step 1, the copy instruction naming roles, a manual change ending the encounter.
  await page.evaluate(() => { if (encounter) { basket = []; basketChanged(); } });
  await page.click('#enc-pools [data-pool=cross]'); await page.click('#surprise'); await waitCard(page);
  result.lines = await page.evaluate(() => ({ hint: hintLines().filter(l => l.startsWith('- 驚喜籃')), step: ai.step, copyNext: $('#step-next').textContent.trim(), bridge: !$('#bridge-row').hidden }));
  await page.evaluate(() => { toggleBasket(basket[0]); });
  result.manual = await page.evaluate(() => ({ encounter: encounter === null, cardHidden: $('#enc-card').hidden, list: !$('#basket-items').hidden, chips: document.querySelectorAll('#basket-items .basket-item').length, hint: hintLines().filter(l => l.startsWith('- 驚喜籃')), whys: document.querySelectorAll('#basket-items .why').length, next: $('#step-next').textContent.trim(), reroll: $('#enc-reroll').hidden, labelRoles: document.querySelectorAll('.star .enc-role').length }));
  await page.evaluate(() => { basket = []; basketChanged(); });
  result.cleared = await page.evaluate(() => ({ encounter: encounter === null, empty: !$('#basket-empty').hidden, start: !$('#enc-start').hidden }));
  // Reproducible: the same seed on a fresh page gives the same set.
  const first = async () => { const p = await open(browser, 1440, 900); await drawNow(p); const s = await snapshot(p); await p.context().close(); return s.ids; };
  result.repro = [await first(), await first()];
});
"""


class EncounterDrawTests(PageCase):
    def test_pools_roles_card_and_rarity_score(self):
        r = self.node_run(DRAW_SCRIPT, self.rich_page())
        self.assertEqual(r['load']['stats'], [])                                    # nothing is simulated at load
        self.assertEqual(r['load']['pools'], [['跨界', 'true'], ['拉扯', 'false'], ['新知', 'false']])
        self.assertEqual((r['load']['tip'], r['load']['button'], r['load']['buttonHidden'], r['load']['step'], r['load']['label']),
                         ('三顆星來自三個不同主題群：拉扯者、新秀、遠客各一。', '✦ 偶遇', False, 1, '抽星範圍'))
        self.assertEqual(r['load']['empty'], '放入兩三顆知識星，看看會孕育出甚麼')
        self.assertEqual(r['tips'], {'cross': ['三顆星來自三個不同主題群：拉扯者、新秀、遠客各一。', '跨界'], 'clash': ['集中抽有爭議的知識：兩個拉扯者，再加一位遠客。', '拉扯'],
                                     'fresh': ['最近入庫的知識，遇上庫內連結最多的老將。', '新知']})
        planned = {'cross': ['拉扯者', '新秀', '遠客'], 'clash': ['拉扯者', '拉扯者', '遠客'], 'fresh': ['新秀', '新秀', '老將']}
        for pool, roles in planned.items():
            p = r['pools'][pool]
            # while drawing: the head, the skip button, three slots with their planned role and ……, no card yet, the selector gone
            self.assertEqual((p['during']['phase'], p['during']['drawHead'], p['during']['skip'], p['during']['cardHidden'], p['during']['startHidden'], p['during']['actionsHidden']),
                             ('drawing', '星空正在為你挑選', '略過動畫', True, True, True))
            self.assertEqual(len(p['during']['rows']), 3)
            self.assertEqual([row[0] for row in p['during']['rows']], roles)
            self.assertEqual(p['after']['phase'], 'card')
            self.assertEqual(p['after']['step'], 1)                                    # the panel stays on step 1
            self.assertEqual([x[0] for x in p['after']['roles']], roles)
            self.assertEqual(len(p['after']['ids']), 3)
            self.assertEqual(len(set(p['after']['ids'])), 3)
            # the card
            c = p['card']
            self.assertRegex(c['badge'], r'^(常見|少見|罕見|傳說)相遇(✦{0,3})$')
            self.assertRegex(c['score'], r'^新意潛力勝過 \d+% 的組合$')
            self.assertEqual([m[0] for m in c['meters']], ['跨度', '未知度', '張力', '牽線'])
            self.assertRegex(c['meters'][0][2], r'^\d 個主題群$')
            self.assertIn(c['meters'][1][2], ['之間沒有已記錄的關係', '只有一條已記錄的關係', '已有多條關係'])
            self.assertRegex(c['meters'][2][2], r'^(\d 顆有爭議的星|沒有爭議的星)$')
            self.assertRegex(c['meters'][3][2], r'^(「.+」同時連到其中兩顆|沒有牽線的星)$')
            self.assertEqual(c['head'], '可以由這裡開始問')
            self.assertEqual(len(c['rows']), 3)
            for row, role in zip(c['rows'], roles):
                self.assertEqual(row['role'], role)
                self.assertRegex(row['meta'], r'^.+｜.+$')                              # 〈主題群〉｜〈原因〉
                self.assertEqual((row['lock'], row['pressed'], row['disabled']), ('鎖定', 'false', False))
            if '遠客' in roles:
                self.assertIn('來自另一個主題群「', [row['meta'] for row in c['rows'] if row['role'] == '遠客'][0])
            self.assertEqual(c['seeds'], c['suggestions'])                              # the first two think suggestions of this basket
            self.assertEqual(len(c['seeds']), 2)
            self.assertEqual((c['reroll']['text'], c['reroll']['disabled'], c['reroll']['hidden'], c['next']), ('重抽未鎖定的星', False, False, '就這組 · 下一步'))
            # the score is the sum of the four graph terms and the spread, worked out again from the page's own data
            s = p['score']
            self.assertAlmostEqual(s['expect'], s['got'], places=9)
            self.assertEqual((s['groups'][0], s['pairs'][0], s['tens'][0], s['bridge'][0]), (s['groups'][1], s['pairs'][1], s['tens'][1], s['bridge'][1]))
            self.assertEqual(c['badge'], s['tierName'] + '相遇' + '✦' * s['tier'])
            self.assertEqual(s['percent'], round(s['pct'] * 100))
        # the instruction names the role, not the old reason; a change by hand ends the encounter and brings the plain list back
        self.assertEqual(len(r['lines']['hint']), 3)
        for line, role in zip(r['lines']['hint'], ['拉扯者', '新秀', '遠客']):
            self.assertRegex(line, rf'^- 驚喜籃：「.+」（{role}）$')
        self.assertEqual(r['lines']['step'], 1)
        self.assertEqual(r['manual'], {'encounter': True, 'cardHidden': True, 'list': True, 'chips': 2, 'hint': [], 'whys': 0, 'next': '下一步', 'reroll': True, 'labelRoles': 0})
        self.assertEqual(r['cleared'], {'encounter': True, 'empty': True, 'start': True})
        self.assertEqual(r['repro'][0], r['repro'][1])                                   # the same seed draws the same set

    def test_a_locked_star_keeps_its_slot_and_all_locked_disables_redraw(self):
        script = r"""
run(async browser => {
  const page = await open(browser, 1440, 900);
  await drawNow(page);
  const before = await snapshot(page);
  // lock slot 1 and 3, draw again: slot 2 changes, the locked ones stay in their slots, the set differs
  await page.click('#enc-card-slots li:nth-child(1) .enc-lock'); await page.click('#enc-card-slots li:nth-child(3) .enc-lock');
  result.locked = await page.evaluate(() => [...document.querySelectorAll('#enc-card-slots .enc-lock')].map(b => [b.textContent.trim(), b.getAttribute('aria-pressed')]));
  await page.click('#enc-reroll');
  result.during = await page.evaluate(() => ({ phase: encounter.phase, rows: [...document.querySelectorAll('#enc-live li')].map(li => li.querySelector('.name').textContent), revealedFirst: [...encounter.revealed] }));
  await waitCard(page);
  const after = await snapshot(page);
  result.before = before; result.after = after;
  result.again = [];
  for (let k = 0; k < 4; k++) {
    await page.click('#enc-reroll'); await waitCard(page);
    result.again.push(await snapshot(page));
  }
  // all three locked: no more drawing
  await page.click('#enc-card-slots li:nth-child(2) .enc-lock');
  result.allLocked = await page.evaluate(() => ({ disabled: $('#enc-reroll').disabled, pressed: [...document.querySelectorAll('#enc-card-slots .enc-lock')].map(b => b.getAttribute('aria-pressed')) }));
  // a re-draw replaces the stars: the question written for the old ones asks first (two presses)
  await page.click('#enc-card-slots li:nth-child(2) .enc-lock');
  await page.evaluate(() => { ai.edited.think = true; ai.question.think = '我自己的問題'; ai.auto.think = false; });
  await page.click('#enc-reroll');
  result.ask = await page.evaluate(() => ({ phase: encounter.phase, text: $('#enc-reroll').textContent.trim(), on: $('#enc-reroll').classList.contains('on'), status: $('#status').textContent }));
  await page.click('#enc-reroll'); await waitCard(page);
  result.askAfter = await page.evaluate(() => ({ edited: hasWrittenQuestion(), text: $('#enc-reroll').textContent.trim() }));
});
"""
        r = self.node_run(script, self.rich_page())
        before, after = r['before'], r['after']
        self.assertEqual(r['locked'], [['已鎖定', 'true'], ['鎖定', 'false'], ['已鎖定', 'true']])
        self.assertEqual(len(r['during']['rows']), 3)
        self.assertEqual(r['during']['revealedFirst'], [0, 2])                                # the locked slots are in the basket at once
        self.assertEqual((after['ids'][0], after['ids'][2]), (before['ids'][0], before['ids'][2]))   # identity and slot kept
        self.assertNotEqual(after['ids'], before['ids'])                                       # another set
        self.assertEqual(after['locked'], [True, False, True])
        previous = after['ids']
        for step in r['again']:
            self.assertEqual((step['ids'][0], step['ids'][2]), (before['ids'][0], before['ids'][2]))
            self.assertNotEqual(step['ids'], previous)                                          # never the identical set while another is possible
            previous = step['ids']
        self.assertEqual(r['allLocked'], {'disabled': True, 'pressed': ['true', 'true', 'true']})
        self.assertEqual((r['ask']['phase'], r['ask']['text'], r['ask']['on']), ('card', '再按一次', True))
        self.assertEqual(r['ask']['status'], '重抽會取代你寫的問題，再按一次才重抽')
        self.assertEqual(r['askAfter'], {'edited': False, 'text': '重抽未鎖定的星'})

    def test_fewer_than_three_stars_and_fallback_labels(self):
        script = r"""
run(async browser => {
  const page = await open(browser, 1440, 900);
  await drawNow(page);
  result.short = { card: await cardText(page), basket: await snapshot(page) };
});
"""
        tiny = self.root / 'tiny'
        tiny.mkdir()
        build_tiny_vault(tiny, 2)
        nebula.build(tiny)
        r = self.node_run(script, (tiny / 'vault/outcome-pages/nebula.html').as_uri())
        self.assertEqual(r['short']['card']['short'], '庫內可抽的星不足三顆，只放入 2 顆。')
        self.assertEqual((len(r['short']['card']['rows']), len(r['short']['basket']['ids'])), (2, 2))
        # a library without any concept with tension: 連結多; without new-and-isolated ones the data decides 新入庫; fallbacks are named in the table
        fallback = r"""
run(async browser => {
  const page = await open(browser, 1440, 900);
  await page.evaluate(() => { allNodes.forEach(n => { n.tension = false; }); encKit = null; });
  await drawNow(page);
  result.snap = await snapshot(page);
  result.card = await cardText(page);
  result.table = await page.evaluate(() => Object.fromEntries(Object.entries(ENCOUNTER_ROLES).map(([k, v]) => [k, [v.label, v.why('甲')]])));
});
"""
        r = self.node_run(fallback, self.rich_page())
        self.assertEqual(r['snap']['roles'][0], ['拉扯者', '連結多'])
        self.assertEqual(r['card']['rows'][0]['role'], '連結多')
        self.assertIn('沒有可抽的拉扯者，改抽庫內連結最多的概念', r['card']['rows'][0]['meta'])
        self.assertEqual(r['table'], {
            'tension': ['拉扯者', '有觀點支持它，也有觀點限制或反駁它'], 'fresh': ['新秀', '最近入庫，與其他知識的連線還很少'],
            'far': ['遠客', '來自另一個主題群「甲」，與其餘兩顆沒有已記錄的關係'], 'veteran': ['老將', '庫內連結最多的知識之一，常是新舊知識的接口'], 'hero': ['主角', '你親自點選的星'],
            'hot': ['連結多', '沒有可抽的拉扯者，改抽庫內連結最多的概念'], 'latest': ['新入庫', '沒有新而孤立的概念，改抽出處最新的'], 'other': ['遠方', '沒有遠親，改抽另一主題群的概念'],
            'random': ['隨機', '資料不足，隨機補上']})
        self.assertEqual(ROLE_LABELS['tension'], '拉扯者')

    def test_tier_shares_follow_the_percentiles_and_thresholds_are_lazy(self):
        script = r"""
run(async browser => {
  const page = await open(browser, 1440, 900);
  result.before = await page.evaluate(() => ({ stats: Object.keys(window.__nebulaEncounterStats), sim: typeof window.__nebulaEncounterSim }));
  // the chunked path of a real draw (it starts with the draw): how long, and the longest single chunk
  await drawNow(page);
  result.stats = await page.evaluate(() => JSON.parse(JSON.stringify(window.__nebulaEncounterStats)));
  result.shares = {};
  for (const pool of ['cross', 'clash', 'fresh']) {
    result.shares[pool] = await page.evaluate(p => window.__nebulaEncounterSim(p, 3000, 777), pool);
  }
  result.consts = await page.evaluate(() => ({ samples: ENCOUNTER_SAMPLES, chunk: ENCOUNTER_CHUNK_MS, cuts: ENCOUNTER_CUTS, tiers: ENCOUNTER_TIERS.map(t => [t.name, t.mark, t.glow, t.spikes, t.sparks]) }));
});
"""
        r = self.node_run(script, self.rich_page(concepts=180))
        self.assertEqual(r['before'], {'stats': [], 'sim': 'function'})
        self.assertEqual(r['consts'], {'samples': 1500, 'chunk': 40, 'cuts': [.55, .85, .97],
                                       'tiers': [['常見', '', .75, 0, 0], ['少見', '✦', .9, 1, 0], ['罕見', '✦✦', 1.05, 1.25, 10], ['傳說', '✦✦✦', 1.25, 1.6, 26]]})
        for pool, shares in r['shares'].items():
            total = sum(shares.values())
            self.assertEqual(total, 3000)
            got = [shares[k] / total * 100 for k in ('common', 'uncommon', 'rare', 'legend')]
            for value, target in zip(got, (55, 30, 12, 3)):
                self.assertLess(abs(value - target), 3, (pool, got))
        stat = r['stats']['cross']
        self.assertGreater(stat['chunks'], 0)
        self.assertLess(stat['longest'], 150)                                                # a chunk is about ENCOUNTER_CHUNK_MS of work

    def test_reduced_motion_and_skip_show_the_card_without_effects(self):
        script = r"""
run(async browser => {
  const still = await open(browser, 1440, 900, { reduced: true });
  await drawNow(still, { wait: false });
  await waitCard(still, 20000);
  await still.waitForTimeout(400);
  result.reduced = await still.evaluate(() => ({ wisp: encounter.fx.wisp, blooms: encounter.fx.blooms.length, meet: encounter.fx.meet && encounter.fx.meet.instant, line: $('#enc-line').style.opacity, form: encounterForm(), disk: state.coreStats.disk && state.coreStats.disk.still, reduced: state.reduced, camera: [state.distance, state.tdistance] }));
  const page = await open(browser, 1440, 900);
  await drawNow(page, { wait: false });
  await page.waitForFunction(() => encounter && encounter.slots.length === 3);
  await page.click('#enc-skip');
  const t0 = Date.now();
  await waitCard(page, 20000);
  result.skipMs = Date.now() - t0;
  await page.waitForTimeout(400);
  result.skipped = await page.evaluate(() => ({ skip: encounter.skip, wisp: encounter.fx.wisp, blooms: encounter.fx.blooms.length, meet: encounter.fx.meet && encounter.fx.meet.instant, form: encounterForm(), line: $('#enc-line').style.opacity, basket: basket.length, cam: state.distance === state.tdistance }));
  const dex = await page.evaluate(() => JSON.parse(localStorage.getItem('nebula-encounter-dex-v1')).reduce((a, b) => a + b, 0));
  result.dex = dex;
});
"""
        r = self.node_run(script, self.rich_page())
        self.assertEqual((r['reduced']['wisp'], r['reduced']['blooms'], r['reduced']['meet'], r['reduced']['line'], r['reduced']['form'], r['reduced']['disk'], r['reduced']['reduced']),
                         (None, 0, True, '0', 1, True, True))
        self.assertEqual(r['reduced']['camera'][0], r['reduced']['camera'][1])                  # the camera jumped
        self.assertEqual((r['skipped']['skip'], r['skipped']['wisp'], r['skipped']['blooms'], r['skipped']['meet'], r['skipped']['form'], r['skipped']['line'], r['skipped']['basket']),
                         (True, None, 0, True, 1, '0', 3))
        self.assertLess(r['skipMs'], 6000)
        self.assertEqual(r['dex'], 1)

    def test_the_ceremony_plays_in_order_and_ends_with_the_card(self):
        script = r"""
run(async browser => {
  const page = await open(browser, 1440, 900);
  await page.evaluate(() => {
    window.__samples = [];
    setInterval(() => {
      const e = encounter; if (!e) return;
      const m = e.fx.meet, now = performance.now();
      const v = state.screen.find(x => e.slots[0] && x.n.id === e.slots[0].id);
      window.__samples.push({ t: Math.round(now), phase: e.phase, wisp: !!e.fx.wisp, blooms: e.fx.blooms.length, revealed: e.revealed.size, meet: m ? Math.round(now - m.start) : null, tier: m ? m.tier : null,
        form: encounterForm(), line: { text: $('#enc-line').textContent, opacity: +$('#enc-line').style.opacity, left: parseFloat($('#enc-line').style.left), top: parseFloat($('#enc-line').style.top) },
        disk: state.coreStats.disk ? Math.round(state.coreStats.disk.scale * 100) / 100 : null, basket: basket.length, panelOpen: !panel.hidden });
    }, 40);
  });
  const t0 = Date.now();
  await drawNow(page, { wait: false });
  await waitCard(page);
  result.totalMs = Date.now() - t0;
  await until(page, () => encounter.fx.meet && performance.now() - encounter.fx.meet.start > 4900, 20000);
  await page.waitForTimeout(300);
  result.samples = await page.evaluate(() => window.__samples);
  result.rar = await page.evaluate(() => ({ tier: encounter.rar.tier, name: ENCOUNTER_TIERS[encounter.rar.tier].name, mark: ENCOUNTER_TIERS[encounter.rar.tier].mark }));
  result.after = await page.evaluate(() => ({ line: $('#enc-line').style.opacity, wisp: encounter.fx.wisp, blooms: encounter.fx.blooms.length, form: encounterForm() }));
});
"""
        r = self.node_run(script, self.rich_page())
        samples = r['samples']
        self.assertTrue(any(s['wisp'] for s in samples), 'a wisp flew')
        self.assertTrue(any(s['blooms'] for s in samples), 'a star swelled')
        revealed = [s['revealed'] for s in samples]
        self.assertEqual(revealed, sorted(revealed))                                           # the stars arrive one after the other
        self.assertEqual(max(revealed), 3)
        meet = [s for s in samples if s['meet'] is not None]
        self.assertTrue(meet)
        # the disc did not exist before the meeting and forms from nothing to full over ENCOUNTER_TIME.disc
        before = [s for s in samples if s['meet'] is None and s['phase'] == 'drawing']
        self.assertTrue(all(s['form'] == 0 for s in before))
        forms = [s['form'] for s in meet]
        self.assertEqual(forms, sorted(forms))
        self.assertEqual(forms[-1], 1)
        late = [s for s in meet if s['meet'] > 2100]
        early = [s for s in meet if s['meet'] < 1000]
        self.assertTrue(all(s['form'] == 0 for s in early))
        self.assertTrue(all(s['form'] == 1 for s in late))
        # one line under the middle: the tier name (and its marks), fading in and out; inside the screen
        shown = [s for s in meet if s['line']['opacity'] > .05]
        self.assertTrue(shown, 'the tier line was shown')
        tier = r['rar']
        for s in shown:
            self.assertEqual(s['line']['text'], tier['name'] + '相遇' + ('  ' + tier['mark'] if tier['mark'] else ''))
            self.assertTrue(0 < s['line']['left'] < 1440 and 0 < s['line']['top'] < 900)
            self.assertTrue(1100 <= s['meet'] <= 4800, s['meet'])
        # the card shows about ENCOUNTER_TIME.card after the meeting began, and not before the meeting
        card = [s for s in samples if s['phase'] == 'card']
        first_card = min(s['meet'] for s in card if s['meet'] is not None)
        self.assertGreaterEqual(first_card, 1900)
        self.assertLessEqual(first_card, 3500)
        self.assertTrue(all(s['meet'] is not None for s in samples if s['phase'] == 'card'))
        self.assertEqual((r['after']['line'], r['after']['wisp'], r['after']['blooms'], r['after']['form']), ('0', None, 0, 1))
        self.assertLess(r['totalMs'], 14000)


HERO_SCRIPT = r"""
run(async browser => {
  const page = await open(browser, 1440, 900);
  const pick = await page.evaluate(() => {
    const idea = allNodes.filter(n => n.kind === 'idea' && !n.tension)[0], hot = allNodes.filter(n => n.kind === 'idea' && n.tension)[0], source = allNodes.find(n => n.kind === 'source');
    return { idea: idea.id, hot: hot.id, source: source.id };
  });
  // The button on the content card: roles [主角, 拉扯者, 遠客] (a star with tension: [主角, 新秀, 遠客]), the hero locked in slot 1, the panel on step 1.
  const heroDraw = async id => {
    await page.evaluate(i => select(i), id);
    const before = await page.evaluate(() => ({ text: $('#hero-draw').textContent, hidden: $('#hero-draw').hidden, inActions: !!$('#hero-draw').closest('.card-actions') }));
    await page.click('#hero-draw');
    await waitCard(page);
    return { before, snap: await snapshot(page), card: await cardText(page), first: await page.evaluate(() => ({ id: encounter.slots[0].id, locked: encounter.slots[0].locked, basket0: basket[0], selected: state.selected, open: !panel.hidden })), hint: await page.evaluate(() => hintLines().filter(l => l.startsWith('- 驚喜籃'))) };
  };
  result.idea = await heroDraw(pick.idea);
  result.idea.id = pick.idea;
  await page.evaluate(() => { basket = []; basketChanged(); });
  result.hot = await heroDraw(pick.hot);
  await page.evaluate(() => { basket = []; basketChanged(); });
  result.source = await heroDraw(pick.source);
  // A cradle that is not empty: the first press only says so, the second replaces.
  await page.evaluate(() => { basket = []; basketChanged(); });
  await page.evaluate(i => { basket = [allNodes.filter(n => n.kind === 'idea')[3].id]; basketChanged(); select(i); }, pick.idea);
  await page.click('#hero-draw');
  result.ask = await page.evaluate(() => ({ text: $('#hero-draw').textContent, basket: basket.length, encounter: encounter === null, armed: $('#hero-draw').classList.contains('armed') }));
  await page.waitForTimeout(3300);
  result.asked = await page.evaluate(() => ({ text: $('#hero-draw').textContent, basket: basket.length }));
  await page.click('#hero-draw');
  await page.click('#hero-draw');
  await waitCard(page);
  result.replaced = await page.evaluate(i => ({ first: encounter.slots[0].id === i, basket: basket.length, roles: encounter.slots.map(s => s.role) }), pick.idea);
  // Manual adding never plays the ceremony (the card's basket button) and starts no encounter.
  await page.evaluate(() => { basket = []; basketChanged(); release(false); });
  await page.evaluate(i => { select(i); }, pick.idea);
  await page.click('#basket-toggle');
  result.manual = await page.evaluate(() => ({ encounter: encounter === null, basket: basket.length }));
  // The dex: counted per tier in this browser; the help card shows it even without any new star.
  result.dex = await page.evaluate(() => JSON.parse(localStorage.getItem('nebula-encounter-dex-v1')));
  await page.evaluate(() => { release(false); closeBasket(false); });
  await page.click('#hint-open');
  result.help = await page.evaluate(() => ({ title: $('#hint-enc-title').textContent, hidden: $('#hint-enc-title').hidden, items: [...document.querySelectorAll('#hint-enc-dex li')].map(li => [li.textContent, li.classList.contains('lit')]), birthDexHidden: $('#hint-dex').hidden }));
  // Damaged values count as zeros.
  result.bad = [];
  for (const bad of ['bad', '[1,2]', '{"a":1}', '[1,"x",3,-4]']) {
    await page.evaluate(v => { localStorage.setItem('nebula-encounter-dex-v1', v); renderHintDex(); }, bad);
    result.bad.push(await page.evaluate(() => [...document.querySelectorAll('#hint-enc-dex li')].map(li => li.textContent)));
  }
  // An unusable store: the draw still works and counts nothing.
  const blocked = await open(browser, 1440, 900, { noKnown: true });
  await blocked.evaluate(() => { Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } }); });
  await drawNow(blocked);
  result.blocked = await blocked.evaluate(() => ({ phase: encounter.phase, basket: basket.length }));
});
"""

THOUGHT_SCRIPT = r"""
run(async browser => {
  const page = await open(browser, 1440, 900);
  const ids = await page.evaluate(() => ({ thought: allNodes.find(n => n.kind === 'thought').id, source: allNodes.find(n => n.kind === 'source').id }));
  await page.evaluate(i => select(i), ids.thought);
  result.thought = await page.evaluate(() => ({ hidden: $('#hero-draw').hidden, continueShown: !$('#continue-cradle').hidden }));
  await page.evaluate(i => select(i), ids.source);
  result.source = await page.evaluate(() => ({ hidden: $('#hero-draw').hidden, text: $('#hero-draw').textContent }));
});
"""

LAYOUT_SCRIPT = r"""
run(async browser => {
  result.runs = {};
  for (const [name, w, h] of [['desktop', 1440, 900], ['phone', 390, 844]]) {
    const page = await open(browser, w, h);
    await page.evaluate(() => openBasket());
    await page.click('#surprise');
    const rects = () => page.evaluate(() => {
      const box = el => { const r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)]; };
      const stars = basket.map(id => { const n = byId.get(id), v = state.screen.find(x => x.n.id === id); return { title: n.title, x: v.q.x, y: v.q.y, z: v.q.z, label: n.el && !n.el.hidden ? box(n.el) : null }; });
      const line = $('#enc-line'), visible = +line.style.opacity > .3;
      return { W: innerWidth, H: innerHeight, panel: box(panel), tools: box(document.querySelector('.tools')), brand: box(document.querySelector('.brand')), stars, line: visible ? box(line) : null, scrollW: document.documentElement.scrollWidth, phase: encounter.phase, form: encounterForm() };
    });
    const during = await rects();
    // The line under the meeting point is read while the card is already there (the card is the tallest the panel gets).
    await until(page, () => encounter && encounter.fx.meet && encounter.phase === 'card' && performance.now() - encounter.fx.meet.start > 2600 && +$('#enc-line').style.opacity > .3, 60000);
    await waitCard(page);
    await page.waitForTimeout(300);
    const withLine = await rects();
    await page.waitForTimeout(2500);
    const after = await rects();
    await page.screenshot({ path: opt.shots + `/cradle-draw-${name}.png` });
    result.runs[name] = { during, withLine, after, shot: opt.shots + `/cradle-draw-${name}.png` };
    await page.context().close();
  }
});
"""

OFFLINE_SCRIPT = r"""
run(async browser => {
  const page = await open(browser, 1440, 900);
  await page.evaluate(() => { basket = allNodes.filter(n => n.kind === 'idea').slice(0, 2).map(n => n.id); basketChanged(); openBasket({ step: 3 }); });
  result.step3 = await page.evaluate(() => ({ start: !$('#ai-start').hidden, other: !$('#ai-other').hidden, copyVisible: $('#ai-copy').checkVisibility(), copyText: $('#ai-copy').textContent.trim(), zoneParent: $('#copy-zone').parentElement.dataset.step, jobHidden: $('#job-view').hidden, outRow: !$('#ai-out-row').hidden, notes: $('#ai-notes .n').textContent, local: LOCAL_SYNC === null }));
  await page.click('#ai-copy');
  result.copied = await until(page, () => $('#ai-copy-status').textContent, 5000);
  await page.evaluate(() => { ai.tab = 'create'; renderAI(); setStep(3); });
  result.create = await page.evaluate(() => ({ start: !$('#ai-start').hidden, copyVisible: $('#ai-copy').checkVisibility(), outRow: !$('#ai-out-row').hidden }));
});
"""


class EncounterOtherTests(PageCase):
    VAULT = 'vault'

    def test_hero_the_cradle_card_button_and_the_dex(self):
        r = self.node_run(HERO_SCRIPT, self.rich_page())
        for key, roles in (('idea', ['主角', '拉扯者', '遠客']), ('hot', ['主角', '新秀', '遠客']), ('source', ['主角', '拉扯者', '遠客'])):
            h = r[key]
            self.assertEqual(h['before'], {'text': '以它為主角抽夥伴', 'hidden': False, 'inActions': True}, key)
            self.assertEqual([x[0] for x in h['snap']['roles']], roles, key)
            self.assertEqual(h['snap']['step'], 1)
            self.assertEqual((h['first']['locked'], h['first']['id'] == h['first']['basket0'], h['first']['selected'], h['first']['open']), (True, True, None, True), key)
            self.assertEqual((h['card']['rows'][0]['role'], h['card']['rows'][0]['lock'], h['card']['rows'][0]['pressed'], h['card']['rows'][0]['disabled']), ('主角', '已鎖定', 'true', True), key)
            self.assertIn('你親自點選的星', h['card']['rows'][0]['meta'])
            self.assertRegex(h['hint'][0], r'^- 驚喜籃：「.+」（主角）$')
            self.assertEqual(h['card']['reroll']['disabled'], False)
        self.assertEqual(r['idea']['first']['id'], r['idea']['id'])
        # not empty: the first press says so and changes nothing; once the time ran out it asks again; the second press replaces
        self.assertEqual(r['ask'], {'text': '會取代搖籃內的星，再按一次', 'basket': 1, 'encounter': True, 'armed': True})
        self.assertEqual(r['asked'], {'text': '以它為主角抽夥伴', 'basket': 1})
        self.assertEqual(r['replaced'], {'first': True, 'basket': 3, 'roles': ['hero', 'tension', 'far']})
        self.assertEqual(r['manual'], {'encounter': True, 'basket': 1})
        # the dex counts every draw of this page (heroes included) and is shown in the help card
        self.assertEqual(sum(r['dex']), 4)
        self.assertEqual(len(r['dex']), 4)
        self.assertEqual((r['help']['title'], r['help']['hidden'], r['help']['birthDexHidden']), ('相遇圖鑑', False, True))
        self.assertEqual([i[0] for i in r['help']['items']], [f'{n} {c}' for n, c in zip(['常見', '少見', '罕見', '傳說'], r['dex'])])
        self.assertEqual([i[1] for i in r['help']['items']], [c > 0 for c in r['dex']])
        zero = ['常見 0', '少見 0', '罕見 0', '傳說 0']
        self.assertEqual(r['bad'], [zero, zero, zero, ['常見 1', '少見 0', '罕見 3', '傳說 0']])
        self.assertEqual(r['blocked'], {'phase': 'card', 'basket': 3})

    def test_a_new_star_is_never_a_hero(self):
        packet, _, _ = cradle_fixture.fixture(self.root, self.VAULT)
        vault = Path(packet['vault'])
        (self.root / 'brain.json').write_text(json.dumps({'vault': self.VAULT}), encoding='utf-8')
        # The two fixture sources and one thinking record (a new star) that cites them.
        (vault / '_registry').mkdir(exist_ok=True)
        rows = [{'source_id': f'SRC-{n}', 'source_note_path': f'sources/SRC-{n}.md', 'status': 'linked'} for n in (1, 2)]
        (vault / '_registry/sources.jsonl').write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
        star = 'ASK-' + '1' * 32
        record = {'schema': 'thinking_record_v1', 'id': star, 'created_at': '2026-10-14T09:00:00+00:00',
                  'packet': {'question': '巡園減少之後，誰來承接觀察？', 'evidence': [{'id': 'L1', 'path': 'sources/SRC-1.md'}, {'id': 'L2', 'path': 'sources/SRC-2.md'}]},
                  'answer': {'analysis': {'schema': 'thinking_v2', 'thesis': '省下的工序也曾是觀察入口。', 'insights': [{'id': 'I1', 'title': '先分觀察與行動', 'adjacent': {'question': '誰來承接觀察？'}}]}}}
        for sub in ('thinking', 'outcome-pages/thinking'):
            (vault / sub).mkdir(parents=True, exist_ok=True)
        (vault / 'thinking' / f'{star}.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        (vault / 'outcome-pages/thinking' / f'{star}.html').write_text('<html>reader</html>', encoding='utf-8')
        nebula.build(self.root)
        r = self.node_run(THOUGHT_SCRIPT, (vault / 'outcome-pages/nebula.html').as_uri())
        self.assertEqual(r['thought'], {'hidden': True, 'continueShown': True})
        self.assertEqual(r['source'], {'hidden': False, 'text': '以它為主角抽夥伴'})

    def test_layout_desktop_and_phone_with_screenshots(self):
        r = self.node_run(LAYOUT_SCRIPT, self.rich_page())

        def meet(a, b):
            return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
        for name, run in r['runs'].items():
            self.assertTrue(Path(run['shot']).is_file())
            print(f'screenshot {name}: {run["shot"]}')
            for key in ('during', 'withLine', 'after'):
                s = run[key]
                W, H = s['W'], s['H']
                panel = s['panel']
                self.assertTrue(panel[0] >= 0 and panel[2] <= W and panel[1] >= 0 and panel[3] <= H, (name, key, panel))
                self.assertFalse(meet(panel, s['tools']), (name, key, 'panel over the top bar'))
                self.assertFalse(meet(panel, s['brand']), (name, key))
                self.assertLessEqual(s['scrollW'], W)
            # the three stars are in the free sky when the card shows (labels too, where they are shown), and so is the tier line
            for key in ('withLine', 'after'):
                s = run[key]
                W, H = s['W'], s['H']
                self.assertEqual(s['phase'], 'card')
                padded = [s['panel'][0] - 4, s['panel'][1] - 4, s['panel'][2] + 4, s['panel'][3] + 4]
                self.assertEqual(len(s['stars']), 3)
                for star in s['stars']:
                    self.assertTrue(star['z'] > 0 and 0 <= star['x'] <= W and 0 <= star['y'] <= H, (name, key, star))
                    self.assertFalse(meet([star['x'] - 6, star['y'] - 6, star['x'] + 6, star['y'] + 6], padded), (name, key, 'star under the panel', star))
                    if star['label']:
                        self.assertFalse(meet(star['label'], padded), (name, key, 'label under the panel', star))
                        self.assertTrue(star['label'][0] >= 0 and star['label'][2] <= W, (name, star))
                self.assertTrue(any(star['label'] for star in s['stars']), (name, key, 'at least one label is visible'))
            line = run['withLine']['line']
            self.assertIsNotNone(line, (name, 'the tier line was visible while the card showed'))
            padded = [run['withLine']['panel'][0] - 4, run['withLine']['panel'][1] - 4, run['withLine']['panel'][2] + 4, run['withLine']['panel'][3] + 4]
            self.assertFalse(meet(line, padded), (name, 'tier line under the panel', line))
            self.assertTrue(line[0] >= 0 and line[2] <= run['withLine']['W'] and line[1] >= 0 and line[3] <= run['withLine']['H'], (name, line))


# ----- 卡與天空的對照: the same number, only the drawn stars named, the focus both ways, locks and rings, the end of the encounter (spec "卡與天空的對照").
LINK_PRE = r"""
const SIZES = [['desktop', 1440, 900], ['phone', 390, 844]];
// The page draws only a few frames a second in the software renderer: every reading waits for two fresh frames, so that the labels, rings and rows are those of the state the page is in.
const frames = (page, n = 2) => page.evaluate(n => new Promise(resolve => { let last = state.eventTime, seen = 0; const iv = setInterval(() => { if (state.eventTime !== last) { last = state.eventTime; if (++seen >= n) { clearInterval(iv); resolve(); } } }, 8); }), n);
const info = async page => { await frames(page); return page.evaluate(() => {
  const e = encounter, box = el => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
  const stars = e ? e.slots.map((s, k) => {
    const n = byId.get(s.id), el = n.el, v = state.screen.find(x => x.n.id === s.id), no = el && el.querySelector('.basket-no');
    return { k, id: s.id, revealed: e.revealed.has(k), hidden: !el || el.hidden, no: no ? no.textContent.trim() : null, focus: !!el && el.classList.contains('enc-focus'), dim: !!el && el.classList.contains('enc-dim'),
      dimOpacity: el ? +getComputedStyle(el).opacity : null, lock: !!el && !!el.querySelector('.enc-lock-ico'), aria: el ? el.getAttribute('aria-label') : null,
      x: v ? v.q.x : null, y: v ? v.q.y : null, vis: v ? v.vis : null, rect: el && !el.hidden ? box(el) : null };
  }) : [];
  const rings = [...document.querySelectorAll('.enc-ring')].filter(r => !r.hidden).map(r => { const b = r.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; });
  return { phase: e ? e.phase : null, focus: e ? e.focus : null, stars, rings,
    rows: [...document.querySelectorAll('#enc-card-slots li')].map(li => ({ no: li.querySelector('.no').textContent, role: li.querySelector('.role').textContent, name: li.querySelector('.name').textContent, focus: li.classList.contains('focus') })),
    live: [...document.querySelectorAll('#enc-live li')].map(li => li.querySelector('.no').textContent),
    shown: nodes.filter(n => n.el && !n.el.hidden).map(n => n.id), drawn: e ? e.slots.map(s => s.id) : [], gnames: [...document.querySelectorAll('#gnames > *')].filter(g => !g.hidden).length,
    detailHidden: $('#detail').hidden, locksOnSky: document.querySelectorAll('.star .enc-lock-ico').length, marksOnSky: document.querySelectorAll('.star.enc-focus, .star.enc-dim').length,
    rowsFocus: document.querySelectorAll('#enc-card-slots li.focus').length, bridge: bridgeShown.size, roleLines: document.querySelectorAll('.star .enc-role').length,
    basketNos: basket.map(id => { const el = byId.get(id).el, no = el && el.querySelector('.basket-no'); return no ? no.textContent.trim() : null; }), expectNos: basket.map((id, i) => numberOf(i)),
    dim: ENCOUNTER_LINK.dim, W: innerWidth, H: innerHeight };
}); };
const label = (page, id) => page.locator(`#labels .star[data-node-id="${id}"]`);
"""

NUMBERS_SCRIPT = r"""
run(async browser => {
  result.runs = {};
  for (const [name, w, h] of SIZES) {
    const page = await open(browser, w, h);
    await page.evaluate(() => openBasket());
    await page.click('#surprise');
    const drawing = [];
    while (await page.evaluate(() => encounter && encounter.phase === 'drawing')) { drawing.push(await info(page)); await page.waitForTimeout(250); }
    await waitCard(page); await page.waitForTimeout(500);
    const run = { drawing, card: await info(page) };
    // the hints: folded away they name nothing; opened, their stars may be named
    await page.evaluate(() => { if (bridgeShown.size) $('#bridge-toggle').click(); });
    await page.waitForTimeout(500);
    run.bridgeOpen = await info(page);
    run.bridgeOpen.open = await page.evaluate(() => bridgeOpen);
    await page.evaluate(() => { if (bridgeOpen) $('#bridge-toggle').click(); });
    await page.waitForTimeout(500);
    run.bridgeShut = await info(page);
    // the middle place locked, then drawn again: it keeps its number (and its lock) while the others arrive
    await page.click('#enc-card-slots li:nth-child(2) .enc-lock');
    const lockedId = await page.evaluate(() => encounter.slots[1].id);
    await page.click('#enc-reroll');
    run.lockedId = lockedId; run.redraw = [];
    while (await page.evaluate(() => encounter && encounter.phase === 'drawing')) { run.redraw.push(await info(page)); await page.waitForTimeout(200); }
    await waitCard(page); await page.waitForTimeout(500);
    run.after = await info(page);
    run.afterId = await page.evaluate(() => encounter.slots[1].id);
    result.runs[name] = run;
    await page.context().close();
  }
});
"""

FOCUS_SCRIPT = r"""
run(async browser => {
  result.runs = {};
  for (const [name, w, h] of SIZES) {
    const phone = w < 700, run = {};
    // a fresh page with the same seed (so the same three stars) for every group of checks: opening a content card moves the camera
    const fresh = async () => { const p = await open(browser, w, h); await drawNow(p); await until(p, () => Math.abs(state.distance - state.tdistance) < .03, 30000); await p.waitForTimeout(500); await p.mouse.move(2, 2); return p; };
    const press = loc => phone ? loc.tap({ force: true }) : loc.click({ force: true });   // a label drifts a little with the stars, so it is never "stable" for the check
    let page = await fresh();
    const ids = await page.evaluate(() => encounter.slots.map(s => s.id)); run.ids = ids; run.hover = [];
    run.start = await info(page);
    // the pointer over a row of the card
    for (const k of [1, 2, 3]) { await page.hover(`#enc-card-slots li:nth-child(${k})`); run.hover.push(await info(page)); }
    await page.mouse.move(2, 2);
    run.left = await info(page);
    // the keyboard in a row (the lock button), and out of it
    await page.focus('#enc-card-slots li:nth-child(2) .enc-lock'); run.key = await info(page);
    // the keyboard stays in the second row while the mouse goes over the first row and leaves the whole card: the keyboard's focus is not cancelled
    await page.hover('#enc-card-slots li:nth-child(1)'); run.keyMouseOver = await info(page);
    await page.mouse.move(2, 2); run.keyMouse = await info(page);
    await page.evaluate(() => document.activeElement.blur()); run.keyOut = await info(page);
    // the pointer over a drawn star in the sky lights its row
    await label(page, ids[1]).hover({ force: true }); run.skyHover = await info(page);
    await page.mouse.move(2, 2);
    // a plain press on the label: with a mouse the star under the pointer is focused already, so one press opens the content card; with a touch the first press holds the focus and the second opens it; the encounter stays
    await press(label(page, ids[1])); run.first = await info(page);
    if (phone) { await press(label(page, ids[1])); run.second = await info(page); }
    run.secondState = await page.evaluate(() => ({ selected: state.selected, encounter: !!encounter, phase: encounter && encounter.phase }));
    await page.context().close();
    // the press on the star itself (the canvas) does the same
    page = await fresh();
    // a drawn star that no other star lies in front of and that is not under the panel (the press takes the nearest star under the finger)
    const pick = await page.evaluate(() => { for (const [k, s] of encounter.slots.entries()) { const v = state.screen.find(x => x.n.id === s.id), top = state.screen.filter(u => u.q.z > 0).sort((a, b) => a.q.z - b.q.z).find(u => Math.hypot(u.q.x - v.q.x, u.q.y - v.q.y) < u.r); if (top && top.n.id === s.id && document.elementFromPoint(v.q.x, v.q.y) === $('#sky')) return { k, at: [v.q.x, v.q.y] }; } return null; });
    run.canvasK = pick.k; const at = pick.at;
    const tap = async () => { if (phone) await page.touchscreen.tap(at[0], at[1]); else await page.mouse.click(at[0], at[1]); };
    if (!phone) await page.mouse.move(at[0], at[1]);                                 // a mouse is over the star before it presses
    await tap(); run.canvasFirst = await info(page);
    if (phone) { await tap(); run.canvasSecond = await info(page); }
    await page.context().close();
    // Ctrl / ⌘ is the manual put-out and still ends the encounter
    page = await fresh();
    await page.evaluate(id => document.querySelector(`#labels .star[data-node-id="${id}"]`).dispatchEvent(new MouseEvent('click', { ctrlKey: true, bubbles: true })), ids[0]);
    run.manual = await page.evaluate(() => ({ encounter: !!encounter, basket: basket.length }));
    await page.context().close();
    if (phone) {
      // a touch on a row holds the focus, a second touch lets go; the lock button holds it on its row
      page = await fresh();
      await page.tap('#enc-card-slots li:nth-child(2) .name'); run.tapRow = await info(page);
      await page.tap('#enc-card-slots li:nth-child(2) .name'); run.tapAgain = await info(page);
      await page.tap('#enc-card-slots li:nth-child(3) .enc-lock'); run.tapLock = await info(page);
      await page.tap('#enc-card-slots li:nth-child(2) .name'); run.tapAfterLock = await info(page);   // the lock button was replaced under the finger: it must not keep the focus on its row
      await page.context().close();
    }
    result.runs[name] = run;
  }
});
"""

EDGE_SCRIPT = r"""
run(async browser => {
  const page = await open(browser, 390, 844);
  await drawNow(page); await page.waitForTimeout(500); await page.mouse.move(2, 2);
  // the camera is moved sideways until the right-most drawn star is a few pixels from the right edge of the screen (and in the upper part of it)
  let id = null, star = null;
  await page.evaluate(() => { state.distance = state.tdistance = state.distance * .55; });   // closer, so that the three labels do not crowd each other
  for (let k = 0; k < 6; k++) {
    id = await page.evaluate(() => {
      const W = innerWidth, pos = i => state.screen.find(v => v.n.id === i).q, ids = encounter.slots.map(s => s.id), best = ids.reduce((a, b) => pos(a).x > pos(b).x ? a : b);
      const dx = (W - 10) - pos(best).x, dy = 220 - pos(best).y, sy = Math.sin(state.yaw), cy = Math.cos(state.yaw), sp = Math.sin(state.pitch), cp = Math.cos(state.pitch), unit = 2 * Math.tan(FOV / 2) * state.distance * cameraFraming() / innerHeight, right = [cy, 0, sy], up = [sy * sp, cp, -cy * sp];
      state.focus = clampFocus(state.focus.map((v, j) => v - dx * unit * right[j] + dy * unit * up[j])); state.target = [...state.focus];
      return best;
    });
    star = (await info(page)).stars.find(s => s.id === id);
    if (star.x > 340 && star.x < 389 && star.y > 150 && star.y < 400) break;
  }
  result.id = id; result.final = await info(page); result.starX = star.x;
});
"""

LOCKS_SCRIPT = r"""
run(async browser => {
  result.runs = {};
  for (const [name, w, h] of SIZES) {
    const phone = w < 700, page = await open(browser, w, h), run = {};
    await drawNow(page); await page.waitForTimeout(500);
    await page.mouse.move(2, 2);                                                       // the pointer that pressed 偶遇 lies over a row of the card now
    const lock = k => phone ? page.tap(`#enc-card-slots li:nth-child(${k}) .enc-lock`) : page.click(`#enc-card-slots li:nth-child(${k}) .enc-lock`);   // a phone is touched, not clicked
    run.none = await info(page);
    await lock(1); run.lock1 = await info(page);
    await lock(3); run.lock13 = await info(page);
    await lock(3); run.unlock3 = await info(page);
    await lock(1); run.unlock1 = await info(page);
    // the card with the second row pointed at and the first locked
    await lock(1);
    if (phone) await page.tap('#enc-card-slots li:nth-child(2) .name'); else await page.hover('#enc-card-slots li:nth-child(2)');
    await page.waitForTimeout(400);
    run.card = await info(page);
    run.shotCard = opt.shots + `/enc-link-${name}-card.png`;
    await page.screenshot({ path: run.shotCard });
    // a draw in progress with the first star locked
    await page.click('#enc-reroll');
    await until(page, () => encounter.phase === 'drawing' && encounter.revealed.size === 2, 30000);
    await page.waitForTimeout(150);
    run.redraw = await info(page);
    run.shotRedraw = opt.shots + `/enc-link-${name}-redraw.png`;
    await page.screenshot({ path: run.shotRedraw });
    await waitCard(page); await page.waitForTimeout(500);
    run.redrawn = await info(page);
    // 主角: the hero's place is locked, so it has the lock and the ring from the first moment
    await page.evaluate(() => { basket = []; basketChanged(); });
    await page.evaluate(() => { const n = allNodes.find(x => x.kind === 'idea'); select(n.id); });
    await page.click('#hero-draw');
    await until(page, () => encounter && encounter.revealed.size >= 1, 10000);
    run.heroDrawing = await info(page);
    await waitCard(page); await page.waitForTimeout(500);
    run.heroCard = await info(page);
    // the end: a change by hand, with a focus and locks on the sky
    if (phone) { await page.mouse.move(2, 2); await page.tap('#enc-card-slots li:nth-child(2) .name'); } else await page.hover('#enc-card-slots li:nth-child(2)');
    run.heroFocus = await info(page);
    await page.evaluate(() => { toggleBasket(basket[0]); }); await page.waitForTimeout(500);
    run.ended = await info(page);
    run.endedState = await page.evaluate(() => ({ encounter: encounter === null, shownOther: nodes.filter(n => n.el && !n.el.hidden && !basketIndex.has(n.id)).length }));
    result.runs[name] = run;
    await page.context().close();
  }
});
"""

BLOCKED_SCRIPT = r"""
run(async browser => {
  result.runs = {};
  for (const [name, w, h] of SIZES) {
    const page = await open(browser, w, h), run = {};
    await drawNow(page); await until(page, () => Math.abs(state.distance - state.tdistance) < .01, 30000); await page.waitForTimeout(500);   // the camera has settled: the star keeps still under the block
    run.base = await info(page);
    // an interface block over the first place of the first drawn star's label (to the right of the star), leaving the place below the star free
    const block = async (css) => page.evaluate(css => {
      const old = document.getElementById('test-block'); if (old) old.remove(); clearInterval(window.__blockTimer);
      const d = document.createElement('div'), at = () => state.screen.find(x => x.n.id === encounter.slots[0].id).q;
      d.className = 'pop'; d.id = 'test-block'; document.body.append(d);
      const place = () => { const q = at(); d.setAttribute('style', css.replace(/X/g, q.x).replace(/Y/g, q.y)); };
      place(); window.__blockTimer = setInterval(place, 25);   // the block follows the star, which drifts a little
      const r = d.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom];
    }, css);
    run.rightBlock = await block('position:fixed;z-index:1;left:calc(Xpx + 6px);top:calc(Ypx - 92px);width:230px;height:84px;');
    await page.waitForTimeout(500);
    run.right = await info(page);
    run.rightBlockNow = await page.evaluate(() => { const r = document.getElementById('test-block').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; });
    // everything around the star blocked: the label is still shown, at its first place
    run.allBlock = await block('position:fixed;z-index:1;left:calc(Xpx - 330px);top:calc(Ypx - 200px);width:660px;height:400px;');
    await page.waitForTimeout(500);
    run.all = await info(page);
    await page.evaluate(() => { clearInterval(window.__blockTimer); document.getElementById('test-block').remove(); });
    await page.waitForTimeout(500);
    run.freed = await info(page);
    result.runs[name] = run;
    await page.context().close();
  }
});
"""


def overlap(a, b, gap=0):
    return a[0] < b[2] + gap and a[2] > b[0] - gap and a[1] < b[3] + gap and a[3] > b[1] - gap


def near_point(point, star, tolerance=3):
    return abs(point[0] - star['x']) <= tolerance and abs(point[1] - star['y']) <= tolerance


CIRCLED = ['①', '②', '③']


class EncounterLinkTests(PageCase):
    def link_run(self, script, **options):
        shots = os.environ.get('NEBULA_LINK_SHOTS')
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            options['shots'] = shots
        return self.node_run(LINK_PRE + script, self.rich_page(), **options)

    def test_the_card_and_the_sky_use_the_same_numbers_and_only_the_drawn_stars_are_named(self):
        r = self.link_run(NUMBERS_SCRIPT)
        for name, run in r['runs'].items():
            card = run['card']
            # A: the rows and the labels carry the same place number, ①②③ by position
            self.assertEqual([x['no'] for x in card['rows']], CIRCLED, name)
            self.assertEqual([s['no'] for s in card['stars']], CIRCLED, name)
            self.assertEqual([s['aria'].split('搖籃第 ')[1][0] for s in card['stars']], ['1', '2', '3'], name)
            # B: three drawn stars, all named; nothing else, and no group name
            self.assertTrue(all(not s['hidden'] for s in card['stars']), (name, card['stars']))
            self.assertEqual(sorted(card['shown']), sorted(card['drawn']), name)
            self.assertEqual(card['gnames'], 0, name)
            self.assertGreater(card['bridge'], 0, name)                                  # the sky does have hint stars that are not named
            # opened, the list lets the hint stars be named (the drawn ones stay)
            self.assertTrue(run['bridgeOpen']['open'], name)
            self.assertTrue(set(card['drawn']) <= set(run['bridgeOpen']['shown']), name)
            if name == 'desktop':                                                         # on a phone the hint stars of this sky lie under the drawn stars' own labels, which come first
                self.assertGreater(len(run['bridgeOpen']['shown']), 3, name)
            self.assertEqual(sorted(run['bridgeShut']['shown']), sorted(card['drawn']), name)
            # while drawing: the live rows carry the numbers, and every revealed star its own number and name
            self.assertGreater(len(run['drawing']), 5, name)
            for s in run['drawing']:
                self.assertEqual(s['live'], CIRCLED, name)
                for star in s['stars']:
                    if star['revealed'] and star['vis']:
                        self.assertEqual(star['no'], CIRCLED[star['k']], (name, star))
            # the middle place locked and drawn again: ② all the way, with its lock, and the number of every revealed star is its place
            self.assertGreater(len(run['redraw']), 3, name)
            for s in run['redraw']:
                self.assertEqual(s['live'], CIRCLED, name)
                locked = s['stars'][1]
                self.assertEqual(locked['id'], run['lockedId'], name)
                self.assertTrue(locked['revealed'], name)
                self.assertEqual((locked['no'], locked['lock'], locked['hidden']), ('②', True, False), (name, locked))
                self.assertEqual(len(s['rings']), 1, name)
                self.assertTrue(near_point(s['rings'][0], locked), (name, s['rings'], locked))
                for star in s['stars']:
                    if star['revealed'] and star['vis']:
                        self.assertEqual(star['no'], CIRCLED[star['k']], (name, star))
            self.assertEqual(run['afterId'], run['lockedId'], name)
            after = run['after']
            self.assertEqual([x['no'] for x in after['rows']], CIRCLED, name)
            self.assertEqual([s['no'] for s in after['stars']], CIRCLED, name)
            self.assertTrue(all(not s['hidden'] for s in after['stars']), name)
            self.assertEqual(sorted(after['shown']), sorted(after['drawn']), name)

    def test_the_focus_goes_both_ways_between_the_card_and_the_sky(self):
        r = self.link_run(FOCUS_SCRIPT)
        for name, run in r['runs'].items():
            clear = lambda s: (s['focus'], s['marksOnSky'], s['rowsFocus'])
            self.assertEqual(clear(run['start']), (-1, 0, 0), name)
            for k, s in enumerate(run['hover']):
                self.assertEqual(s['focus'], k, (name, k))
                self.assertEqual([x['focus'] for x in s['stars']], [i == k for i in range(3)], (name, k))
                self.assertEqual([x['dim'] for x in s['stars']], [i != k for i in range(3)], (name, k))
                self.assertEqual([x['focus'] for x in s['rows']], [i == k for i in range(3)], (name, k))
                for x in s['stars']:
                    if x['dim']:
                        self.assertAlmostEqual(x['dimOpacity'], s['dim'], places=2)
                self.assertTrue(all(not x['hidden'] for x in s['stars']), (name, k))
            self.assertEqual(clear(run['left']), (-1, 0, 0), name)                          # the pointer left: everything is cleared
            self.assertEqual(run['key']['focus'], 1, name)                                  # the keyboard in a row is the same as the pointer
            self.assertEqual(run['keyMouseOver']['focus'], 0, name)                         # the mouse over the first row wins while it is there
            self.assertEqual(run['keyMouse']['focus'], 1, name)                             # it leaves: the keyboard is still in the second row
            self.assertEqual([x['focus'] for x in run['keyMouse']['rows']], [False, True, False], name)
            self.assertEqual([x['focus'] for x in run['keyMouse']['stars']], [False, True, False], name)
            self.assertEqual(clear(run['keyOut']), (-1, 0, 0), name)
            self.assertEqual(run['skyHover']['focus'], 1, name)                             # the pointer over a drawn star lights its row
            self.assertEqual([x['focus'] for x in run['skyHover']['rows']], [False, True, False], name)
            # a plain press: a mouse is over the star, which is focused already, so one press opens the content card; a touch holds the focus first and opens with the second press. The encounter stays either way.
            if name == 'desktop':
                self.assertFalse(run['first']['detailHidden'], name)
                self.assertEqual(run['secondState']['selected'], run['ids'][1], name)
                self.assertFalse(run['canvasFirst']['detailHidden'], name)
                self.assertEqual(run['canvasFirst']['phase'], 'card', name)
            else:
                self.assertTrue(run['first']['detailHidden'], name)
                self.assertEqual(run['first']['focus'], 1, name)
                self.assertEqual([x['focus'] for x in run['first']['rows']], [False, True, False], name)
                self.assertFalse(run['second']['detailHidden'], name)
                self.assertEqual(run['secondState']['selected'], run['ids'][1], name)
                self.assertTrue(run['canvasFirst']['detailHidden'], name)
                self.assertEqual(run['canvasFirst']['focus'], run['canvasK'], name)
                self.assertFalse(run['canvasSecond']['detailHidden'], name)
                self.assertEqual(run['canvasSecond']['phase'], 'card', name)
            self.assertEqual((run['secondState']['encounter'], run['secondState']['phase']), (True, 'card'), name)
            self.assertEqual(run['manual'], {'encounter': False, 'basket': 2}, name)         # Ctrl + press is the manual take-out, as before
            if name == 'phone':
                self.assertEqual(run['tapRow']['focus'], 1, name)
                self.assertEqual([x['focus'] for x in run['tapRow']['rows']], [False, True, False], name)
                self.assertEqual(clear(run['tapAgain']), (-1, 0, 0), name)
                self.assertEqual(run['tapLock']['focus'], 2, name)                          # the lock button holds the focus on its row
                self.assertEqual(run['tapAfterLock']['focus'], 1, name)                     # and a touch on another row moves it
                self.assertEqual([x['focus'] for x in run['tapAfterLock']['rows']], [False, True, False], name)

    def test_a_drawn_star_at_the_edge_of_the_screen_keeps_its_name_on_another_side(self):
        r = self.link_run(EDGE_SCRIPT)
        star = [s for s in r['final']['stars'] if s['id'] == r['id']][0]
        self.assertTrue(340 < star['x'] < 389 and 150 < star['y'] < 400, star)                                        # the star is on the screen, a few pixels from the edge
        self.assertFalse(star['hidden'], star)
        self.assertLessEqual(star['rect'][2], star['x'] + 1, star)                          # the label is on the left of the star, not cut off at the edge
        self.assertGreaterEqual(star['rect'][0], 0, star)
        self.assertLessEqual(star['rect'][2], r['final']['W'], star)
        self.assertEqual(star['no'], CIRCLED[star['k']], star)

    def test_locks_rings_hero_and_the_end_of_the_encounter(self):
        r = self.link_run(LOCKS_SCRIPT)
        for name, run in r['runs'].items():
            self.assertEqual((run['none']['rings'], run['none']['locksOnSky']), ([], 0), name)

            def check(s, locked):
                self.assertEqual([x['lock'] for x in s['stars']], [i in locked for i in range(3)], (name, locked))
                self.assertEqual(len(s['rings']), len(locked), (name, locked, s['rings']))
                for k in locked:
                    self.assertTrue(any(near_point(p, s['stars'][k]) for p in s['rings']), (name, k, s['rings'], s['stars'][k]))
                self.assertEqual(s['locksOnSky'], len(locked), name)
            check(run['lock1'], {0})
            check(run['lock13'], {0, 2})
            check(run['unlock3'], {0})
            check(run['unlock1'], set())
            self.assertEqual(run['lock1']['focus'], 0, name)                                # the lock button also holds the focus on its row
            # screenshot 1: the second row pointed at, the first locked
            self.assertEqual(run['card']['focus'], 1, name)
            check(run['card'], {0})
            self.assertTrue(Path(run['shotCard']).is_file())
            print(f'screenshot {name} card: {run["shotCard"]}')
            # screenshot 2: a draw in progress, the first star locked, its name, number, lock and ring all kept
            s = run['redraw']
            self.assertEqual(s['phase'], 'drawing', name)
            check(s, {0})
            self.assertEqual(s['stars'][0]['no'], '①', name)
            self.assertEqual(s['focus'], -1, name)
            self.assertTrue(Path(run['shotRedraw']).is_file())
            print(f'screenshot {name} redraw: {run["shotRedraw"]}')
            self.assertFalse(s['stars'][0]['hidden'], name)
            self.assertEqual(s['live'], CIRCLED, name)
            self.assertEqual(len(run['redrawn']['rings']), 1, name)
            # 主角
            check(run['heroDrawing'], {0})
            check(run['heroCard'], {0})
            self.assertEqual(run['heroCard']['stars'][0]['no'], '①', name)
            # the end: nothing of the encounter is left on the sky or the card; the numbers are the cradle's order; the sky names stars by its normal rules again
            e = run['ended']
            self.assertEqual((e['locksOnSky'], e['marksOnSky'], e['rowsFocus'], e['rings'], e['roleLines'], e['focus']), (0, 0, 0, [], 0, None), name)
            self.assertEqual(e['basketNos'], e['expectNos'], name)
            self.assertEqual(run['endedState']['encounter'], True, name)
            self.assertGreater(run['endedState']['shownOther'], 0, name)

    def test_a_drawn_stars_label_moves_aside_and_is_never_hidden(self):
        r = self.link_run(BLOCKED_SCRIPT)
        for name, run in r['runs'].items():
            base = run['base']['stars'][0]
            self.assertFalse(base['hidden'], name)
            self.assertGreaterEqual(base['rect'][0], base['x'], name)                       # the first place is to the right of the star
            right = run['right']['stars'][0]
            self.assertFalse(right['hidden'], name)
            self.assertFalse(overlap(right['rect'], run['rightBlockNow']), (name, right['rect'], run['rightBlockNow']))
            self.assertLessEqual(right['rect'][0], right['x'] + 1, name)                    # moved to another place (left of the star, above or below it)
            for key in ('right', 'all', 'freed'):
                self.assertTrue(all(not s['hidden'] for s in run[key]['stars']), (name, key))
            allb = run['all']['stars'][0]
            self.assertGreaterEqual(allb['rect'][0], allb['x'], name)                        # nowhere free: it stays at its first place
            self.assertGreaterEqual(run['freed']['stars'][0]['rect'][0], run['freed']['stars'][0]['x'], name)


class OfflinePageTests(PageCase):
    def test_the_offline_page_keeps_step_three_as_it_was(self):
        r = self.node_run(OFFLINE_SCRIPT, self.rich_page())
        self.assertEqual(r['step3'], {'start': False, 'other': False, 'copyVisible': True, 'copyText': '複製指令', 'zoneParent': '3', 'jobHidden': True, 'outRow': True, 'notes': r['step3']['notes'], 'local': True})
        self.assertRegex(r['step3']['notes'], r'份筆記 · 預留 4 份給張力 · AI 先擬題')
        self.assertEqual(r['copied'], '✓ 新想法正在孕育，貼到 Claude 或 Codex，AI 會先回你 5 條問題')
        self.assertEqual(r['create'], {'start': False, 'copyVisible': True, 'outRow': False})


WRAPPER = """import os, runpy
from pathlib import Path
control = Path(os.environ['CRADLE_PAGE_CONTROL'])
for name, key in (('scenario.txt', 'CRADLE_FAKE_SCENARIO'), ('delay.txt', 'CRADLE_FAKE_DELAY')):
    path = control / name
    if path.is_file():
        os.environ[key] = path.read_text(encoding='utf-8').strip()
runpy.run_path(%r, run_name='__main__')
""" % str(FAKE)

JOB_PRE = r"""
const ctl = opt.control;
const setScenario = (name, delay) => { fs.writeFileSync(ctl + '/scenario.txt', name); fs.writeFileSync(ctl + '/delay.txt', String(delay || 3)); };
const flag = (name, on) => { const p = ctl + '/' + name; if (on) fs.writeFileSync(p, '1'); else if (fs.existsSync(p)) fs.unlinkSync(p); };
const OWN = '自動灌溉之後，巡園要重新想甚麼？';
const startPage = async (browser, w = 1440, h = 900, o = {}) => {
  const page = await open(browser, w, h, { ...o, seeded: false });
  await page.waitForFunction(() => cradleAvail === false || jobList.seen);
  await page.evaluate(() => { basket = allNodes.filter(n => n.kind === 'source').map(n => n.id); basketChanged(); });
  return page;
};
const toStep3 = (page, own) => page.evaluate(q => { if (q) { ai.auto.think = false; ai.question.think = q; ai.edited.think = true; } openBasket({ step: 3 }); }, own || '');
const viewHas = (page, re, ms = 90000) => until(page, s => { const t = $('#job-view').hidden ? '' : $('#job-view').innerText; return new RegExp(s).test(t) ? t : false; }, ms, re);
const clickText = (page, label) => page.evaluate(t => { const b = [...document.querySelectorAll('#job-view button, #job-view a')].find(x => x.textContent.trim() === t); if (!b) return false; b.click(); return true; }, label);
const buttons = page => page.evaluate(() => [...document.querySelectorAll('#job-view .job-actions button, #job-view .job-actions a')].map(b => b.textContent.trim()));
const server = (page, path, method = 'GET') => page.evaluate(async ([p, m]) => { const r = await fetch('/api/cradle/' + p, { method: m, headers: { 'X-Nebula-Token': LOCAL_SYNC.token } }); return await r.json(); }, [path, method]);
const finish = async page => { await clickText(page, '完成'); await page.waitForFunction(() => $('#job-view').hidden); };
"""

SUCCESS_SCRIPT = r"""
run(async browser => {
  setScenario('slow', 4);
  const page = await startPage(browser);
  const posts = [];
  await page.route('**/api/cradle/jobs', route => { const q = route.request(); if (q.method() === 'POST') posts.push({ type: q.headers()['content-type'], token: q.headers()['x-nebula-token'], body: JSON.parse(q.postData()) }); route.continue(); });
  await toStep3(page, OWN);
  result.initial = await page.evaluate(() => ({ start: !$('#ai-start').hidden, label: $('#ai-start').textContent.trim(), disabled: $('#ai-start').disabled, other: !$('#ai-other').hidden, otherOpen: $('#ai-other').open, otherSummary: $('#ai-other summary').textContent.trim(), copyVisible: $('#ai-copy').checkVisibility(), zoneInside: !!$('#copy-zone').closest('#ai-other-body'), jobHidden: $('#job-view').hidden, outRow: !$('#ai-out-row').hidden, pill: $('#job-pill').hidden, token: LOCAL_SYNC.token, label3: $('#ai-start').getBoundingClientRect().height }));
  // a draw in front of the person is named in the request (a state made by hand: the library of this fixture has no concept to draw)
  await page.evaluate(() => { encounter = newEncounter('cross', null, ['tension', 'fresh'], [{ role: 'tension', why: 'tension', id: 'SRC-1', locked: false, cands: [] }, { role: 'fresh', why: 'latest', id: 'SRC-2', locked: false, cands: [] }]); encounter.phase = 'card'; encounter.rar = { score: 3.256, tier: 1, pct: .6, percent: 60 }; });
  await page.click('#ai-start');
  result.running = await viewHas(page, '正在');
  result.runningState = { pill: await page.evaluate(() => $('#job-pill').textContent), outRow: await page.evaluate(() => !$('#ai-out-row').hidden), startHidden: await page.evaluate(() => $('#ai-start').hidden), buttons: await buttons(page), tracked: await page.evaluate(() => JSON.parse(localStorage.getItem('nebula-cradle-jobs-v1'))), jobId: await page.evaluate(() => jobTracked[0].id) };
  result.pillBox = await page.evaluate(() => { const r = el => { const b = el.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom)]; }; return { pill: r($('#job-pill')), badge: r($('#basket-badge')), dot: $('#birth-dot').hidden ? null : r($('#birth-dot')), button: r($('#basket-open')), W: innerWidth }; });
  await page.waitForTimeout(2400);
  result.disc = await page.evaluate(() => ({ hold: diskHold, disk: state.coreStats.disk && { scale: state.coreStats.disk.scale, point: state.coreStats.disk.point } }));
  result.job = await server(page, 'jobs/' + result.runningState.jobId);
  result.done = await viewHas(page, '新星已誕生。', 120000);
  result.after = await page.evaluate(() => { const id = jobTracked[0].id, j = jobsMap.get(id), a = [...document.querySelectorAll('#job-view a')].find(x => x.textContent.trim() === '開啟思考紀錄'); return { step: ai.step, open: basketOpen, starId: j.result.star_id, href: a && a.getAttribute('href'), resolved: a && a.href, target: a && a.target, rel: a && a.rel, pageUrl: location.href, pill: $('#job-pill').hidden }; });
  result.buttons = await buttons(page);
  result.birth = await until(page, () => { const j = jobsMap.get(jobTracked[0].id), id = j && j.result && j.result.star_id; return !!id && ((state.birth && state.birth.id === id) || (seenRead() && seenRead().has(id))); }, 40000);
  result.released = await until(page, () => diskHold === null, 20000);
  await clickText(page, '看新星');
  result.seen = await page.evaluate(() => ({ selected: state.selected, isStar: byId.get(state.selected) && byId.get(state.selected).kind }));
  await finish(page);
  result.dismissed = await page.evaluate(() => ({ tracked: JSON.parse(localStorage.getItem('nebula-cradle-jobs-v1')), jobHidden: $('#job-view').hidden, start: !$('#ai-start').hidden, pill: $('#job-pill').hidden }));
  result.posts = posts;
});
"""

QUESTIONS_SCRIPT = r"""
run(async browser => {
  setScenario('success');
  const page = await startPage(browser);
  await toStep3(page);
  result.start = await page.evaluate(() => ({ label: $('#ai-start').textContent.trim(), drafting: drafting(), question: writtenQuestion('think') }));
  await page.click('#ai-start');
  await viewHas(page, 'AI 擬了 5 條問題，揀一條開始孕育：');
  result.pick = await page.evaluate(() => ({ head: $('#job-view .job-line').textContent, choices: [...document.querySelectorAll('#job-view .job-choice')].map(b => ({ angle: b.querySelector('.job-angle').textContent, q: b.querySelector('.job-q').textContent, why: b.querySelector('.job-why').textContent, surprise: b.querySelector('.job-surprise') ? b.querySelector('.job-surprise').textContent : null, pressed: b.getAttribute('aria-pressed') })), actions: [...document.querySelectorAll('#job-view .job-actions button')].map(b => [b.textContent.trim(), b.disabled]) }));
  result.badge = await until(page, () => !$('#job-pill').hidden && $('#job-pill').textContent, 20000);
  await page.click('#job-view .job-choice:nth-child(4)');
  result.picked = await page.evaluate(() => ({ pressed: [...document.querySelectorAll('#job-view .job-choice')].map(b => b.getAttribute('aria-pressed')), actions: [...document.querySelectorAll('#job-view .job-actions button')].map(b => [b.textContent.trim(), b.disabled]) }));
  await page.evaluate(() => { window.__before = true; });
  await clickText(page, '以這條問題開始孕育');
  await viewHas(page, '新星已誕生。', 120000);
  await until(page, () => window.__before === undefined, 90000);               // the page reloaded itself once the star was in the nebula
  await viewHas(page, '新星已誕生。', 60000);
  result.listed = await server(page, 'jobs');
  result.pillAfter = await until(page, () => $('#job-pill').hidden, 20000);
  // a second questions job: none fits, so the person goes back to step 2 and the job is dismissed
  await finish(page);
  await page.waitForFunction(() => ai.auto.think === true);
  await toStep3(page);
  await page.click('#ai-start');
  await viewHas(page, 'AI 擬了 5 條問題，揀一條開始孕育：');
  await clickText(page, '都不合適，返回 ②');
  await page.waitForFunction(() => ai.step === 2);
  result.none = await page.evaluate(() => ({ step: ai.step }));
  result.noneList = await until(page, async () => { const r = await fetch('/api/cradle/jobs', { headers: { 'X-Nebula-Token': LOCAL_SYNC.token } }); const j = await r.json(); return j.awaiting_pick === 0 && $('#job-pill').hidden ? j : false; }, 20000);
});
"""

FAIL_A_SCRIPT = r"""
run(async browser => {
  const page = await startPage(browser);
  result.cases = {};
  const failCase = async (key, scenario, re) => {
    setScenario(scenario);
    await toStep3(page, OWN);
    await page.click('#ai-start');
    const text = await viewHas(page, re);
    result.cases[key] = { text, buttons: await buttons(page), detail: await page.evaluate(() => $('#job-view details summary').textContent + '|' + $('#job-view details .job-detail').textContent), notSaved: text.includes('未通過的答案不會存入星雲。'), pill: await page.evaluate(() => $('#job-pill').hidden) };
  };
  await failCase('agent', 'receipt-failed', 'AI 回報未能完成：合成失敗原因');
  await finish(page);
  await failCase('login', 'not-logged-in', 'Claude 未登入');
  await finish(page);
  await failCase('limit', 'usage-limit', '已到用量上限');
  await finish(page);
  await failCase('timeout', 'hang', '超過 1 分鐘仍未完成');
  await finish(page);
  // "改問題" goes back to step 2 and keeps the failed job out of step 3 until a new press
  await failCase('agent2', 'receipt-failed', 'AI 回報未能完成：合成失敗原因');
  await clickText(page, '改問題');
  await page.waitForFunction(() => ai.step === 2);
  await page.evaluate(() => setStep(3));
  result.afterEdit = await page.evaluate(() => ({ jobHidden: $('#job-view').hidden, start: !$('#ai-start').hidden }));
  // a retry that works: the same job continues and the star is born
  setScenario('success');
  await page.evaluate(() => { jobViewOff = false; renderCommand(); });
  await clickText(page, '重試');
  result.retried = await viewHas(page, '新星已誕生。', 120000);
});
"""

DRAFT_SCRIPT = r"""
run(async browser => {
  setScenario('never-accept');
  const page = await startPage(browser);
  await toStep3(page, OWN);
  await page.click('#ai-start');
  const text = await viewHas(page, '覆核者仍認為有推論站不住：', 150000);
  result.text = text;
  result.buttons = await buttons(page);
  await clickText(page, '看草稿');
  await page.waitForFunction(() => document.querySelector('#job-view .job-draft'));
  result.draft = await page.evaluate(() => ({ markdown: document.querySelector('#job-view .job-draft').textContent, tag: document.querySelector('#job-view .job-draft').tagName, items: [...document.querySelectorAll('#job-view .job-review li')].map(li => li.textContent), head: [...document.querySelectorAll('#job-view .job-sub')].map(p => p.textContent), scroll: getComputedStyle(document.querySelector('#job-view .job-draft')).overflowY, wrap: getComputedStyle(document.querySelector('#job-view .job-draft')).whiteSpace }));
});
"""

ARCHIVE_SCRIPT = r"""
run(async browser => {
  setScenario('success');
  const page = await startPage(browser);
  flag('archive-fail.flag', true);
  await toStep3(page, OWN);
  await page.click('#ai-start');
  result.archive = { text: await viewHas(page, '答案已通過覆核，但存檔失敗：', 90000), buttons: await buttons(page) };
  flag('archive-fail.flag', false);
  await clickText(page, '重試');
  result.archiveRetry = await viewHas(page, '新星已誕生。', 90000);
  await finish(page);
  // the nebula that cannot show the star
  flag('nebula-fail.flag', true);
  await toStep3(page, OWN + '（二）');
  await page.click('#ai-start');
  result.nebula = { text: await viewHas(page, '星雲暫時未能顯示新星', 90000), buttons: await buttons(page) };
  flag('nebula-fail.flag', false);
  await clickText(page, '重新整理星雲');
  result.nebulaRetry = await viewHas(page, '新星已誕生。', 90000);
});
"""

CANCEL_SCRIPT = r"""
run(async browser => {
  setScenario('slow', 30);
  const page = await startPage(browser);
  await toStep3(page, OWN);
  await page.click('#ai-start');
  await viewHas(page, '正在');
  // the first press asks, the time runs out, two presses stop
  await clickText(page, '停止孕育');
  result.armed = await buttons(page);
  await page.waitForTimeout(3400);
  result.timedOut = await buttons(page);
  await clickText(page, '停止孕育'); await clickText(page, '再按一次停止');
  result.cancelled = { text: await viewHas(page, '已停止孕育。', 30000), buttons: await buttons(page) };
  result.releasedAfterCancel = await until(page, () => diskHold === null, 20000);
  // start over: the same job runs again
  await clickText(page, '重新開始');
  result.restarted = await viewHas(page, '(正在|排隊中)', 30000);
  // a new press while one is at work is queued
  await clickText(page, '新孕育');
  result.queuedLabel = await page.evaluate(() => ({ label: $('#ai-start').textContent.trim(), start: !$('#ai-start').hidden }));
  await page.click('#ai-start');
  result.queued = await viewHas(page, '排隊中：前面還有 1 個孕育。', 30000);
  result.pill = await until(page, () => !$('#job-pill').hidden && $('#job-pill').textContent === '孕育中 2' ? $('#job-pill').textContent : false, 20000);
  // a reload during the jobs: the view comes back from the storage and the service
  await page.reload();
  await page.waitForFunction(() => typeof state !== 'undefined' && state.gpu === true);
  await page.evaluate(() => { openBasket({ step: 3 }); });
  result.reloaded = { text: await viewHas(page, '排隊中：前面還有 1 個孕育。', 30000), pill: await until(page, () => !$('#job-pill').hidden && $('#job-pill').textContent, 20000), held: await until(page, () => diskHold !== null, 10000) };
  // stop the queued one, then the one at work
  await clickText(page, '停止孕育'); await clickText(page, '再按一次停止');
  await viewHas(page, '已停止孕育。', 30000);
  await finish(page);
  await viewHas(page, '正在', 30000);
  await clickText(page, '停止孕育'); await clickText(page, '再按一次停止');
  await viewHas(page, '已停止孕育。', 30000);
  await clickText(page, '返回 ②');
  result.back = await page.evaluate(() => ({ step: ai.step }));
  result.final = await until(page, async () => { const r = await fetch('/api/cradle/jobs', { headers: { 'X-Nebula-Token': LOCAL_SYNC.token } }); const j = await r.json(); return j.active === 0 && $('#job-pill').hidden && diskHold === null ? j.jobs.map(x => x.status) : false; }, 20000);
});
"""

MISSING_SCRIPT = r"""
run(async browser => {
  const page = await startPage(browser);
  // Wait for the installed-program check, then refresh the jobs summary to read its unavailable state.
  await page.evaluate(async () => { await cradleCall('GET', 'runners'); await jobTick(); });
  await toStep3(page, OWN);
  result.page = await page.evaluate(() => ({ start: !$('#ai-start').hidden, copy: $('#ai-copy').checkVisibility(), line: $('#ai-runner-note').textContent.trim(), tracked: jobTracked.length }));
  await page.click('#ai-runner-note button');
  await page.waitForFunction(() => !aiSetupBusy && aiSetupData);
  result.reason = await page.locator('.ai-runner').filter({ has: page.locator('input[value=claude]') }).innerText();
  result.disabled = await page.locator('input[value=claude]').isDisabled();
  await page.click('#ai-setup-back');
  await page.click('#ai-copy');
  result.copied = await until(page, () => $('#ai-copy-status').textContent, 5000);
});
"""

OTHER_WAYS_SCRIPT = r"""
run(async browser => {
  const page = await startPage(browser);
  const posts = [];
  await page.route('**/api/cradle/jobs', route => { const q = route.request(); if (q.method() === 'POST') posts.push(JSON.parse(q.postData())); route.continue(); });
  await toStep3(page, OWN);
  const face = () => page.evaluate(() => ({ start: !$('#ai-start').hidden, other: !$('#ai-other').hidden, copy: $('#ai-copy').checkVisibility(), outRow: !$('#ai-out-row').hidden, zoneInFold: !!$('#copy-zone').closest('#ai-other-body') }));
  result.think = await face();
  // the fold 其他做法 holds the copy button, the notes line and the full text, exactly as before
  await page.click('#ai-other summary');
  result.open = await page.evaluate(() => ({ copy: $('#ai-copy').checkVisibility(), copyText: $('#ai-copy').textContent.trim(), notes: $('#ai-notes').checkVisibility(), notesText: $('#ai-notes .n').textContent, fold: $('#ai-fold').checkVisibility() }));
  await page.click('#ai-copy');
  result.copied = await until(page, () => $('#ai-copy-status').textContent, 5000);
  result.clip = await page.evaluate(async () => (await navigator.clipboard.readText()).replace(/\r\n/g, '\n') === $('#ai-preview').value);   // the clipboard of Windows keeps CR LF
  // 向外延伸 on: the copy button is the big one again
  await page.click('#ai-out');
  result.out = await face();
  result.outText = await page.evaluate(() => $('#ai-preview').value.includes('向外延伸（我已開啟）'));
  await page.click('#ai-out');
  result.outOff = await face();
  // creating: unchanged
  await page.evaluate(() => { switchWay('create'); setStep(3); });
  result.create = await face();
  await page.evaluate(() => { switchWay('think'); setStep(3); });
  // no notes to read: 開始孕育 is disabled and the existing sentence shows
  await page.evaluate(() => { thinkNotes = () => ({ notes: [], omitted: 0 }); renderCommand(); });
  result.none = await page.evaluate(() => ({ disabled: $('#ai-start').disabled, note: $('#ai-start-note').hidden ? '' : $('#ai-start-note').textContent, notesLine: $('#ai-notes .o').textContent }));
  await page.reload();
  await page.waitForFunction(() => typeof state !== 'undefined' && state.gpu === true);
  await page.evaluate(() => { openBasket({ step: 3 }); });
  // the requests: kind by the mode, the notes, the question, the basket, the headers; the one for drafting carries the person's own direction
  setScenario('success');
  await page.evaluate(() => { ai.auto.think = true; ai.edited.think = true; ai.question.think = '我想問的方向'; renderCommand(); });
  await page.click('#ai-start');
  await viewHas(page, '(AI 擬了 5 條問題|正在)', 60000);
  await page.evaluate(() => { jobViewOff = true; ai.auto.think = false; ai.question.think = '直接問的問題'; renderCommand(); });
  await page.click('#ai-start');
  await viewHas(page, '(正在|新星已誕生|排隊中)', 60000);
  result.posts = posts;
});
"""

UNAVAILABLE_SCRIPT = r"""
run(async browser => {
  const page = await startPage(browser);
  await toStep3(page, OWN);
  await page.waitForFunction(() => cradleAvail === false, null, { timeout: 20000 });
  await page.evaluate(() => renderCommand());
  result.page = await page.evaluate(() => ({ avail: cradleAvail, start: !$('#ai-start').hidden, other: !$('#ai-other').hidden, copy: $('#ai-copy').checkVisibility(), pill: $('#job-pill').hidden, jobHidden: $('#job-view').hidden }));
});
"""


BADGE_PHONE_SCRIPT = r"""
run(async browser => {
  setScenario('slow', 30);
  const page = await startPage(browser, 390, 844);
  await toStep3(page, OWN);
  await page.click('#ai-start');
  await viewHas(page, '正在');
  await page.evaluate(() => closeBasket(false));
  await until(page, () => !$('#job-pill').hidden, 20000);
  await page.waitForTimeout(300);
  const boxes = () => page.evaluate(() => {
    const r = el => { const b = el.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom)]; };
    return { W: innerWidth, H: innerHeight, pill: r($('#job-pill')), text: $('#job-pill').textContent, badge: r($('#basket-badge')), badgeHidden: $('#basket-badge').hidden, dot: $('#birth-dot').hidden ? null : r($('#birth-dot')), tools: [...document.querySelectorAll('.tools .ib')].map(r), brand: [r(document.querySelector('.brand h1')), r($('#hint-open'))], search: r(document.querySelector('.searchbox')), button: r($('#basket-open')) };
  });
  result.closed = await boxes();
  await page.screenshot({ path: opt.shots + '/cradle-pill-phone.png' });
  // with a new star's unseen dot and the count badge both showing
  await page.evaluate(() => { $('#birth-dot').hidden = false; });
  result.withDot = await boxes();
  result.shot = opt.shots + '/cradle-pill-phone.png';
});
"""


class IncubationTests(PageCase):
    VAULT = 'vault'

    def boot(self, timeouts=None, command=None, cradle=True, runner='claude', codex_scenario='success'):
        vault_path = self.root / self.VAULT
        if vault_path.exists():
            shutil.rmtree(vault_path)
        packet, _, _ = cradle_fixture.fixture(self.root, self.VAULT)
        vault = Path(packet['vault'])
        (self.root / 'brain.json').write_text(json.dumps({'vault': self.VAULT}), encoding='utf-8')
        (vault / '_registry').mkdir(exist_ok=True)
        rows = [{'source_id': f'SRC-{n}', 'source_note_path': f'sources/SRC-{n}.md', 'status': 'linked'} for n in (1, 2)]
        (vault / '_registry/sources.jsonl').write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
        nebula.build(self.root)
        self.control = self.root / 'control'
        self.control.mkdir()
        wrapper = self.root / 'fake_wrapper.py'
        wrapper.write_text(WRAPPER, encoding='utf-8')
        folder = self.root / '.reader-runtime' / 'cradle'
        folder.mkdir(parents=True)
        (folder / 'settings.json').write_text(json.dumps({'schema': 'knowledge_cradle_settings_v1', 'runner': runner, 'codex_command': [sys.executable, '-B', str(FAKE_CODEX)], 'claude_command': command or [sys.executable, '-B', str(wrapper)],
                                                         'timeouts': timeouts or {'write': 60, 'other': 60}, 'max_review_rounds': 3, 'max_mechanical_fixes': 2}), encoding='utf-8')
        trace = self.root / 'trace'
        env = patch.dict(os.environ, {'CRADLE_PAGE_CONTROL': str(self.control), 'CRADLE_FAKE_TRACE': str(trace), 'CRADLE_FAKE_AUTH': 'claude.ai', 'CRADLE_FAKE_CODEX': codex_scenario})
        env.start()
        self.addCleanup(env.stop)
        flags = self.control

        real_index = generator.refresh_outcome_index

        def index(*args, **kwargs):
            if (flags / 'archive-fail.flag').exists():
                raise OSError('interrupted')
            return real_index(*args, **kwargs)
        real_refresh, real_projections = archive.refresh_nebula, NebulaServer.refresh_projections

        def refresh(*args, **kwargs):
            if (flags / 'nebula-fail.flag').exists():
                return {'status': 'refresh_failed', 'error': 'OSError: locked'}
            return real_refresh(*args, **kwargs)

        def projections(server, *args, **kwargs):
            if (flags / 'nebula-fail.flag').exists():
                raise OSError('locked')
            return real_projections(server, *args, **kwargs)
        for target in (patch.object(generator, 'refresh_outcome_index', side_effect=index), patch.object(archive, 'refresh_nebula', side_effect=refresh), patch.object(NebulaServer, 'refresh_projections', projections)):
            target.start()
            self.addCleanup(target.stop)
        if not cradle:
            gone = patch.object(server_module, 'cradle', None)
            gone.start()
            self.addCleanup(gone.stop)
        server = NebulaServer(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(60)
        self.addCleanup(stop)
        self.server = server
        return server.page_url

    def run_jobs(self, body, url, timeout=900, **options):
        return self.node_run(JOB_PRE + body, url, timeout=timeout, control=str(self.control), **options)

    def test_thinking_job_to_a_born_star(self):
        r = self.run_jobs(SUCCESS_SCRIPT, self.boot())
        i = r['initial']
        self.assertEqual((i['start'], i['label'], i['disabled'], i['other'], i['otherOpen'], i['otherSummary'], i['copyVisible'], i['zoneInside'], i['jobHidden'], i['outRow'], i['pill']),
                         (True, '開始孕育', False, True, False, '其他做法', False, True, True, True, True))
        # running: the step, the time, the sentence about staying away, the stop button; the badge; the request
        self.assertRegex(r['running'], r'^(正在準備材料|正在寫答案|正在檢查引文|正在覆核（第 \d 輪）|正在按覆核意見修訂|正在存成新星|正在整理星雲)\n')
        self.assertRegex(r['running'], r'大約還要 \d+ 分鐘')
        self.assertIn('期間可以繼續逛星雲，甚至關掉頁面，工作會在背景照做。', r['running'])
        self.assertEqual(r['runningState']['buttons'], ['停止孕育', '新孕育'])
        self.assertEqual((r['runningState']['pill'], r['runningState']['outRow'], r['runningState']['startHidden']), ('孕育中 1', False, True))
        self.assertEqual(r['runningState']['tracked'], [{'id': r['runningState']['jobId'], 'dismissed': False}])
        post = r['posts'][0]
        self.assertEqual((post['type'], post['token']), ('application/json', i['token']))
        self.assertEqual({k: post['body'][k] for k in ('kind', 'notes', 'question', 'basket')},
                         {'kind': 'think', 'notes': ['sources/SRC-1.md', 'sources/SRC-2.md'], 'question': '自動灌溉之後，巡園要重新想甚麼？', 'basket': ['SRC-1', 'SRC-2']})
        self.assertEqual(post['body']['encounter'], {'pool': 'cross', 'roles': ['tension', 'latest'], 'tier': '少見', 'score': 3.26})
        self.assertEqual(set(post['body']), {'kind', 'notes', 'question', 'basket', 'encounter'})
        self.assertEqual((r['job']['kind'], r['job']['status'] in ('running', 'queued')), ('think', True))
        # the badge sits next to the cradle button, over neither its count nor the birth dot, and inside the screen
        box = r['pillBox']
        pill, badge = box['pill'], box['badge']
        self.assertFalse(pill[0] < badge[2] and pill[2] > badge[0] and pill[1] < badge[3] and pill[3] > badge[1])
        self.assertTrue(0 <= pill[0] and pill[2] <= box['W'])
        # the disc was drawn in to one point and held while the job worked
        self.assertIsNotNone(r['disc']['hold'])
        self.assertLess(r['disc']['disk']['scale'], .05)
        self.assertGreater(r['disc']['disk']['point'], .9)
        self.assertTrue(r['released'])                                   # and let go when it ended
        # done: the page reloaded itself, ③ shows the job; the link goes to the reading page, the birth played
        self.assertIn('新星已誕生。', r['done'])
        self.assertEqual((r['after']['step'], r['after']['open'], r['after']['pill']), (3, True, True))
        self.assertEqual((r['after']['href'], r['after']['target'], r['after']['rel']), (f"thinking/{r['after']['starId']}.html", '_blank', 'noopener'))
        self.assertTrue(r['after']['resolved'].endswith(f"/outcome-pages/thinking/{r['after']['starId']}.html"))
        self.assertEqual(r['buttons'][:2], ['看新星', '開啟思考紀錄'])
        self.assertEqual(r['buttons'][-2:], ['完成', '新孕育'])
        self.assertTrue(r['birth'])
        self.assertEqual(r['seen'], {'selected': r['after']['starId'], 'isStar': 'thought'})
        self.assertEqual(r['dismissed']['tracked'][0]['dismissed'], True)
        self.assertEqual((r['dismissed']['jobHidden'], r['dismissed']['start'], r['dismissed']['pill']), (True, True, True))

    def test_questions_job_pick_then_success_and_none_fits(self):
        r = self.run_jobs(QUESTIONS_SCRIPT, self.boot())
        self.assertEqual(r['start'], {'label': '開始孕育', 'drafting': True, 'question': ''})
        pick = r['pick']
        self.assertEqual(pick['head'], 'AI 擬了 5 條問題，揀一條開始孕育：')
        self.assertEqual([c['angle'] for c in pick['choices']], ['新應用', '新情景', '能力轉化', '被忽略的洞見', '上升一層'])
        self.assertEqual([c['surprise'] for c in pick['choices']], [None, None, '✦ 最出人意表', None, None])
        self.assertTrue(all(c['why'] == '合成測試問題。' and c['pressed'] == 'false' for c in pick['choices']))
        self.assertEqual(pick['actions'], [['以這條問題開始孕育', True], ['都不合適，返回 ②', False], ['完成', False]])
        self.assertEqual(r['badge'], '待揀問題 1')
        self.assertEqual(r['picked']['pressed'], ['false', 'false', 'false', 'true', 'false'])
        self.assertEqual(r['picked']['actions'], [['以這條問題開始孕育', False], ['都不合適，返回 ②', False], ['完成', False]])
        jobs = {j['job_id']: j for j in r['listed']['jobs']}
        ordered = sorted(jobs)
        questions, think = jobs[ordered[0]], jobs[ordered[1]]
        self.assertEqual((questions['kind'], questions['picked'], think['kind'], think['status']), ('questions', ordered[1], 'think', 'done'))
        self.assertEqual(think['question'], pick['choices'][3]['q'])
        self.assertTrue(r['pillAfter'])                                               # hidden once the pick was made and the job done
        self.assertEqual(r['none']['step'], 2)
        self.assertEqual([j['dismissed'] for j in r['noneList']['jobs']][:1], [True])     # "none fits" dismisses the job
        self.assertEqual((r['noneList']['awaiting_pick'], r['noneList']['active']), (0, 0))

    def test_failures_and_their_buttons(self):
        r = self.run_jobs(FAIL_A_SCRIPT, self.boot(timeouts={'write': 4, 'other': 60}))
        cases = r['cases']
        want = {'agent': ('AI 回報未能完成：合成失敗原因', ['重試', '改問題', '完成', '新孕育'], 'agent_failed'),
                'login': ('Claude 未登入，請先打開 Claude 登入一次。', ['重試', '完成', '新孕育'], 'not_logged_in'),
                'limit': ('已到用量上限，約 3 小時後可再試。', ['稍後重試', '完成', '新孕育'], 'usage_limit'),
                'timeout': ('寫答案超過 1 分鐘仍未完成，已停止。', ['重試', '完成', '新孕育'], 'timeout')}
        for key, (message, buttons, code) in want.items():
            c = cases[key]
            self.assertTrue(c['text'].startswith(message), (key, c['text']))
            self.assertEqual(c['buttons'], buttons, key)
            self.assertTrue(c['notSaved'], key)                                          # 未通過的答案不會存入星雲。
            self.assertTrue(c['detail'].startswith('詳情|' + code), (key, c['detail']))
            self.assertTrue(c['pill'], key)                                              # a failed job is no running job
        self.assertEqual(r['afterEdit'], {'jobHidden': True, 'start': True})
        self.assertIn('新星已誕生。', r['retried'])

    def test_review_that_does_not_converge_offers_the_draft(self):
        r = self.run_jobs(DRAFT_SCRIPT, self.boot())
        self.assertTrue(r['text'].startswith('覆核者仍認為有推論站不住：'))
        self.assertEqual(r['buttons'], ['重試', '改問題', '看草稿', '完成', '新孕育'])
        d = r['draft']
        self.assertEqual((d['tag'], d['scroll'], d['wrap']), ('PRE', 'auto', 'pre-wrap'))
        self.assertIn('合成測試答案', d['markdown'])
        self.assertIn('覆核意見', d['head'])
        self.assertEqual(d['items'], ['I1：推論跳過了替代條件，要補寫。'])

    def test_archive_failure_and_a_nebula_that_cannot_show_the_star(self):
        r = self.run_jobs(ARCHIVE_SCRIPT, self.boot())
        self.assertRegex(r['archive']['text'], r'答案已通過覆核，但存檔失敗：[^\n]*。草稿已保留。')
        self.assertEqual(r['archive']['buttons'], ['重試', '完成', '新孕育'])
        self.assertIn('新星已誕生。', r['archiveRetry'])
        self.assertIn('思考紀錄已存好，但星雲暫時未能顯示新星：OSError: locked。', r['nebula']['text'])
        self.assertEqual(r['nebula']['buttons'], ['重新整理星雲', '完成', '新孕育'])
        self.assertIn('新星已誕生。', r['nebulaRetry'])

    def test_stop_restart_queue_reload_and_the_badge(self):
        r = self.run_jobs(CANCEL_SCRIPT, self.boot())
        self.assertEqual(r['armed'][0], '再按一次停止')
        self.assertEqual(r['timedOut'][0], '停止孕育')                                      # the second press has to come within 3 seconds
        self.assertTrue(r['cancelled']['text'].startswith('已停止孕育。'))
        self.assertEqual(r['cancelled']['buttons'], ['重新開始', '返回 ②', '完成', '新孕育'])
        self.assertTrue(r['releasedAfterCancel'])
        self.assertRegex(r['restarted'], r'^(正在|排隊中)')
        self.assertEqual(r['queuedLabel'], {'label': '開始孕育（排隊）', 'start': True})
        self.assertTrue(r['queued'].startswith('排隊中：前面還有 1 個孕育。'))
        self.assertEqual(r['pill'], '孕育中 2')
        self.assertTrue(r['reloaded']['text'].startswith('排隊中：前面還有 1 個孕育。'))
        self.assertEqual(r['reloaded']['pill'], '孕育中 2')
        self.assertTrue(r['reloaded']['held'])                                              # the disc is drawn in again after a reload during the job
        self.assertEqual(r['back']['step'], 2)
        self.assertEqual(r['final'], ['cancelled', 'cancelled'])

    def test_a_missing_claude_program_offers_the_copy_instruction(self):
        r = self.run_jobs(MISSING_SCRIPT, self.boot(command=[str(self.root / 'no-such-claude.exe')]))
        self.assertEqual(r['page'], {'start': False, 'copy': True, 'line': 'Claude Code 暫時用不到 · 前往 AI 設置', 'tracked': 0})
        self.assertIn('這部電腦找不到 Claude Code。', r['reason'])
        self.assertTrue(r['disabled'])
        self.assertEqual(r['copied'], '✓ 新想法正在孕育，貼到 Claude 或 Codex，AI 會直接深入思考')

    def test_other_ways_requests_and_modes_that_keep_the_copy_button(self):
        r = self.run_jobs(OTHER_WAYS_SCRIPT, self.boot())
        self.assertEqual(r['think'], {'start': True, 'other': True, 'copy': False, 'outRow': True, 'zoneInFold': True})
        self.assertEqual((r['open']['copy'], r['open']['copyText'], r['open']['notes'], r['open']['fold']), (True, '複製指令', True, True))
        self.assertRegex(r['open']['notesText'], r'^2 份筆記 · 預留 4 份給張力')
        self.assertEqual(r['copied'], '✓ 新想法正在孕育，貼到 Claude 或 Codex，AI 會直接深入思考')
        self.assertTrue(r['clip'])
        self.assertEqual(r['out'], {'start': False, 'other': False, 'copy': True, 'outRow': True, 'zoneInFold': False})      # 向外延伸 on: unchanged
        self.assertTrue(r['outText'])
        self.assertEqual(r['outOff'], {'start': True, 'other': True, 'copy': True, 'outRow': True, 'zoneInFold': True})
        self.assertEqual(r['create'], {'start': False, 'other': False, 'copy': True, 'outRow': False, 'zoneInFold': False})  # creating: unchanged
        self.assertEqual(r['none']['disabled'], True)
        self.assertEqual(r['none']['note'], '沒有可用的筆記：思考紀錄不能當筆記，請回到第 ① 步一併放入母星')
        first, second = r['posts'][0], r['posts'][1]
        self.assertEqual((first['kind'], first['way'], first['question']), ('questions', 'think', '我想問的方向'))     # drafting: the person's own direction
        self.assertEqual((second['kind'], second['question']), ('think', '直接問的問題'))
        self.assertNotIn('way', second)
        self.assertEqual((first['notes'], first['basket']), (['sources/SRC-1.md', 'sources/SRC-2.md'], ['SRC-1', 'SRC-2']))

    def test_a_workspace_without_the_cradle_service_keeps_the_copy_page(self):
        r = self.run_jobs(UNAVAILABLE_SCRIPT, self.boot(cradle=False), allow404=True)
        self.assertEqual(r['page'], {'avail': False, 'start': False, 'other': False, 'copy': True, 'pill': True, 'jobHidden': True})

    def test_the_badge_at_phone_width_covers_nothing(self):
        r = self.run_jobs(BADGE_PHONE_SCRIPT, self.boot())

        def meet(a, b):
            return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
        print('screenshot phone badge:', r['shot'])
        for key in ('closed', 'withDot'):
            b = r[key]
            self.assertEqual(b['text'], '孕育中 1')
            self.assertEqual(b['badgeHidden'], False)
            self.assertTrue(0 <= b['pill'][0] and b['pill'][2] <= b['W'] and 0 <= b['pill'][1] and b['pill'][3] <= b['H'], b['pill'])
            self.assertFalse(meet(b['pill'], b['badge']), (key, 'over the count badge'))
            if b['dot']:
                self.assertFalse(meet(b['pill'], b['dot']), (key, 'over the birth dot'))
            for tool in b['tools']:
                self.assertFalse(meet(b['pill'], tool), (key, 'over a button of the top bar', tool))
            for part in b['brand']:
                self.assertFalse(meet(b['pill'], part), (key, 'over the title or its help button', part))
            self.assertFalse(meet(b['pill'], b['search']), key)


MOCK_PRE = r"""
const html = fs.readFileSync(opt.html, 'utf8').replace('const LOCAL_SYNC=null;', 'const LOCAL_SYNC={token:"mock",revision:"test"};');
const ID = 'CJ-20261027-0001', ID2 = 'CJ-20261027-0002';
const baseJob = (id, extra) => ({ job_id: id, kind: 'think', way: null, status: 'done', position: null, step: null, round: 0, eta_seconds: null, question: '問', notes: ['sources/SRC-1.md'], basket: [], created_at: new Date().toISOString(), ended_at: new Date().toISOString(), error: null, result: { star_id: 'none', href: null }, questions: null, surprising: null, picked: null, dismissed: false, ...extra });
const questionsJob = extra => baseJob(ID, { kind: 'questions', way: 'think', question: '', result: null, surprising: 2, questions: Array.from({ length: 5 }, (_, i) => ({ angle: '角度' + i, question: '問題' + i, why: '原因' + i })), ...extra });
// A page served by a pretend service: the jobs are whatever mock.jobs holds; mock.listFail / mock.detailFail make the first asks fail.
const mockOpen = async (browser, mock) => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'no-preference' });
  await context.addInitScript(KNOWN);
  await context.addInitScript(ids => localStorage.setItem('nebula-cradle-jobs-v1', JSON.stringify(ids.map(id => ({ id, dismissed: false })))), mock.tracked);
  const page = await context.newPage();
  page.on('pageerror', e => result.errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) result.errors.push(m.text()); });
  mock.listCalls = 0; mock.detailCalls = 0;
  await page.route('**/*', async r => {
    const p = new URL(r.request().url()).pathname, jobs = Object.values(mock.jobs);
    if (p === '/nebula.html') return r.fulfill({ contentType: 'text/html', body: html });
    if (p === '/api/preferences') return r.fulfill({ json: { preferences: { palette: 'violet', reduced_motion: false, entrance_enabled: false } } });
    if (p === '/api/constellations') return r.fulfill({ json: { constellations: [] } });
    if (p === '/api/ping') return r.fulfill({ json: { revision: 'test', updating: false } });
    if (p === '/api/cradle/jobs') {
      if (++mock.listCalls <= (mock.listFail || 0)) return r.abort('failed');
      const live = j => j.status === 'queued' || j.status === 'running';
      return r.fulfill({ json: { jobs, runner: { name: 'claude', label: 'Claude Code', ready: true }, active: jobs.filter(live).length, awaiting_pick: jobs.filter(j => j.kind === 'questions' && j.status === 'done' && !j.dismissed).length } });
    }
    const m = p.match(/^\/api\/cradle\/jobs\/(CJ-[0-9-]+)$/);
    if (m && mock.jobs[m[1]]) {
      if (++mock.detailCalls <= (mock.detailFail || 0)) return r.fulfill({ status: 503, json: { error: 'transient' } });
      return r.fulfill({ json: mock.jobs[m[1]] });
    }
    return r.fulfill({ status: 404, json: { error: 'not part of the mock' } });
  });
  await page.goto('http://cradle.mock/nebula.html');
  await page.waitForFunction(() => typeof state !== 'undefined' && state.gpu === true && typeof bootstrap === 'function');
  await page.waitForTimeout(400);
  await page.evaluate(() => { stopCameraTour(); openBasket({ step: 3 }); });
  return page;
};
"""

FIRST_ASK_SCRIPT = r"""
run(async browser => {
  for (const [key, mock] of [
    ['list', { tracked: [ID], listFail: 1, jobs: { [ID]: baseJob(ID, { kind: 'think', status: 'running', step: 'write' }) } }],
    ['detail', { tracked: [ID], detailFail: 1, jobs: { [ID]: baseJob(ID, { kind: 'think', status: 'done' }) } }],
  ]) {
    const page = await mockOpen(browser, mock);
    await page.waitForFunction(() => jobLastTick > 0);   // the first ask has been answered or has failed
    result[key] = { failed: await page.evaluate(() => ({ timer: !!jobPollTimer, known: jobsMap.size })) };   // right after the failed ask: nothing known, but a new ask is planned
    result[key].recovered = await until(page, () => jobsMap.size === 1 && !$('#job-view').hidden && $('#job-view').innerText, 25000);
    result[key].calls = { list: mock.listCalls, detail: mock.detailCalls };
    result[key].after = await page.evaluate(() => ({ known: jobsMap.size, startHidden: $('#ai-start').hidden }));
    await page.context().close();
  }
});
"""

MOTION_SCRIPT = r"""
run(async browser => {
  const snap = () => ({ reduced: state.reduced, skip: encounter.skip, phase: encounter.phase, wisp: encounter.fx.wisp, blooms: encounter.fx.blooms.length, meet: encounter.fx.meet && encounter.fx.meet.instant, form: encounterForm(), line: $('#enc-line').style.opacity, cardShown: !$('#enc-card').hidden, drawShown: !$('#enc-draw').hidden, camera: Math.abs(state.distance - state.tdistance) < 1e-6, basket: basket.length });
  for (const [key, ready] of [
    ['wisp', () => encounter && encounter.fx.wisp],
    ['meeting', () => encounter && encounter.phase === 'drawing' && encounter.fx.meet && performance.now() - encounter.fx.meet.start > 500],
    ['tail', () => { if (!encounter || encounter.phase !== 'card' || !encounter.fx.meet) return false; encounter.fx.meet.start = performance.now() - 2600; return $('#enc-line').style.opacity > 0; }],   // the card is up but the light of the meeting is still fading
  ]) {
    const page = await open(browser, 1440, 900);
    await drawNow(page, { wait: false });
    result[key + 'Before'] = await until(page, ready, 60000) ? await page.evaluate(snap) : null;
    await page.click('#motion');
    await page.waitForFunction(() => encounter && encounter.phase === 'card', null, { timeout: 5000 });
    await page.waitForTimeout(300);
    result[key] = await page.evaluate(snap);
    result[key + 'Dex'] = await page.evaluate(() => JSON.parse(localStorage.getItem('nebula-encounter-dex-v1')).reduce((a, b) => a + b, 0));
    await page.context().close();
  }
});
"""

QUEUE_SCRIPT = r"""
run(async browser => {
  // the older job A (0001) was retried while the newer B (0002) runs: A is last in the real queue, so one job is ahead of it
  const mock = { tracked: [ID], jobs: { [ID]: baseJob(ID, { status: 'queued', position: 1 }), [ID2]: baseJob(ID2, { status: 'running', step: 'write' }) } };
  const page = await mockOpen(browser, mock);
  result.one = await until(page, () => /排隊中/.test($('#job-view').innerText) && $('#job-view').innerText, 20000);
  mock.jobs[ID].position = 3;
  result.three = await until(page, () => /前面還有 3 個/.test($('#job-view').innerText) && $('#job-view').innerText, 20000);
  mock.jobs[ID].position = 0;
  result.zero = await until(page, () => /前面還有 0 個/.test($('#job-view').innerText) && $('#job-view').innerText, 20000);
});
"""

DONE_QUESTIONS_SCRIPT = r"""
run(async browser => {
  const mock = { tracked: [ID], jobs: { [ID]: questionsJob() } };
  const page = await mockOpen(browser, mock);
  await until(page, () => /AI 擬了 5 條問題/.test($('#job-view').innerText), 20000);
  result.buttons = await page.evaluate(() => [...document.querySelectorAll('#job-view .job-actions button')].map(b => b.textContent.trim()));
  await page.evaluate(() => [...document.querySelectorAll('#job-view .job-actions button')].find(b => b.textContent.trim() === '完成').click());
  await page.waitForFunction(() => $('#job-view').hidden);
  result.after = await page.evaluate(() => ({ tracked: JSON.parse(localStorage.getItem('nebula-cradle-jobs-v1')), start: !$('#ai-start').hidden, jobHidden: $('#job-view').hidden }));
});
"""


class PollingAndPositionTests(PageCase):
    """The fix round after the page review: a failed first ask is tried again, reduced motion during a draw, the queue position, 完成 on picked questions."""
    VAULT = 'vault'

    def mock_page(self):
        packet, _, _ = cradle_fixture.fixture(self.root, self.VAULT)
        vault = Path(packet['vault'])
        (self.root / 'brain.json').write_text(json.dumps({'vault': self.VAULT}), encoding='utf-8')
        (vault / '_registry').mkdir(exist_ok=True)
        rows = [{'source_id': f'SRC-{n}', 'source_note_path': f'sources/SRC-{n}.md', 'status': 'linked'} for n in (1, 2)]
        (vault / '_registry/sources.jsonl').write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
        nebula.build(self.root)
        return str(vault / 'outcome-pages' / 'nebula.html')

    def run_mock(self, body):
        return self.node_run(MOCK_PRE + body, 'about:blank', html=self.mock_page())

    def test_a_failed_first_ask_is_tried_again_until_an_answer_decides(self):
        r = self.run_mock(FIRST_ASK_SCRIPT)
        for key in ('list', 'detail'):
            self.assertEqual(r[key]['failed'], {'timer': True, 'known': 0}, key)
            self.assertTrue(r[key]['recovered'], key)
            self.assertEqual((r[key]['after']['known'], r[key]['after']['startHidden']), (1, True), key)
        self.assertGreaterEqual(r['list']['calls']['list'], 2)
        self.assertGreaterEqual(r['detail']['calls']['detail'], 2)

    def test_reduced_motion_switched_on_during_a_draw_ends_the_ceremony_at_once(self):
        r = self.node_run(MOTION_SCRIPT, self.rich_page())
        for key in ('wisp', 'meeting', 'tail'):
            self.assertEqual(r[key + 'Before']['reduced'], False, key)
            self.assertEqual((r[key + 'Before']['skip'], r[key + 'Before']['phase']), (False, 'card' if key == 'tail' else 'drawing'), key)
            self.assertEqual(r[key + 'Before']['meet'] is False or r[key + 'Before']['meet'] is None, True, key)       # the light was still on
            self.assertEqual(r[key], {'reduced': True, 'skip': key != 'tail', 'phase': 'card', 'wisp': None, 'blooms': 0, 'meet': True, 'form': 1, 'line': '0',
                                      'cardShown': True, 'drawShown': False, 'camera': True, 'basket': 3}, key)
            self.assertEqual(r[key + 'Dex'], 1, key)

    def test_the_queue_position_comes_from_the_service(self):
        r = self.run_mock(QUEUE_SCRIPT)
        self.assertTrue(r['one'].startswith('排隊中：前面還有 1 個孕育。'), r['one'])
        self.assertTrue(r['three'].startswith('排隊中：前面還有 3 個孕育。'), r['three'])
        self.assertTrue(r['zero'].startswith('排隊中：前面還有 0 個孕育。'), r['zero'])

    def test_picked_questions_can_be_finished_like_every_ended_job(self):
        r = self.run_mock(DONE_QUESTIONS_SCRIPT)
        self.assertEqual(r['buttons'], ['以這條問題開始孕育', '都不合適，返回 ②', '完成'])
        self.assertEqual(r['after'], {'tracked': [{'id': 'CJ-20261027-0001', 'dismissed': True}], 'start': True, 'jobHidden': True})




AI_SETUP_PRE = r"""
const face = page => page.evaluate(() => ({ start: !$('#ai-start').hidden, copy: $('#ai-copy').checkVisibility(), line: $('#ai-runner-note').hidden ? '' : $('#ai-runner-note').textContent.trim() }));
const setup = async page => {
  await page.click('#ai-runner-note button');
  await page.waitForFunction(() => !aiSetupBusy && aiSetupData);
};
const setupRows = page => page.evaluate(() => [...document.querySelectorAll('.ai-runner')].map(row => ({ name: row.querySelector('input').value, disabled: row.querySelector('input').disabled, text: row.innerText })));
"""


class AISetupTests(PageCase):
    VAULT = 'vault'
    boot = IncubationTests.boot
    run_jobs = IncubationTests.run_jobs

    def run_setup(self, body, url, **options):
        return self.run_jobs(AI_SETUP_PRE + body, url, **options)

    def test_no_approved_runner_copies_and_opens_setup_and_jobs_409_is_shown(self):
        r = self.run_setup(r"""
run(async browser => {
  const page = await startPage(browser);
  const calls = [];
  page.on('request', q => { if (q.url().includes('/api/cradle/runners')) calls.push(q.url()); });
  await toStep3(page, OWN);
  result.face = await face(page);
  result.detectionsBeforeOpen = calls.length;
  await page.evaluate(async () => { await startJob(); });
  result.refusal = await page.locator('#job-start-error').innerText();
  result.jobs = await server(page, 'jobs');
  await setup(page);
  result.rows = await setupRows(page);
  result.calls = calls;
  result.focused = await page.evaluate(() => $('#ai-setup').contains(document.activeElement));
  await page.keyboard.press('Escape');
  result.closed = await page.evaluate(() => ({ open: $('#ai-setup').open, focus: document.activeElement === $('#ai-runner-note button'), basket: basketOpen }));
});
""", self.boot(runner=None), allow409=True)
        self.assertEqual(r['face'], {'start': False, 'copy': True, 'line': '未設定背景 AI · 前往 AI 設置'})
        self.assertEqual(r['detectionsBeforeOpen'], 0)
        self.assertEqual(r['refusal'], '還未設定背景 AI，請先在 AI 設置揀一個；也可以改用「複製指令」。')
        self.assertEqual(r['jobs']['jobs'], [])
        self.assertEqual([row['name'] for row in r['rows']], ['claude', 'codex'])
        self.assertTrue(all(not row['disabled'] and '可用' in row['text'] for row in r['rows']))
        self.assertEqual(len(r['calls']), 1)
        self.assertNotIn('refresh=', r['calls'][0])
        self.assertTrue(r['focused'])
        self.assertEqual(r['closed'], {'open': False, 'focus': True, 'basket': True})

    def test_approve_codex_switches_without_reload_and_runs_the_codex_fake(self):
        r = self.run_setup(r"""
run(async browser => {
  const page = await startPage(browser);
  await toStep3(page, OWN);
  await setup(page);
  result.rows = await setupRows(page);
  await page.evaluate(() => window.__setupSamePage = 'same');
  const posts = [];
  page.on('request', async q => { if (q.url().endsWith('/api/cradle/settings')) { const h = await q.allHeaders(); posts.push({ body: q.postDataJSON(), token: h['x-nebula-token'], origin: h.origin }); } });
  await page.check('input[value=codex]');
  await page.click('#ai-setup-approve');
  await page.waitForFunction(() => !aiSetupBusy && jobList.runner?.name === 'codex');
  result.face = await face(page);
  result.current = await page.locator('.ai-runner-current').innerText();
  result.currentRow = await page.locator('.ai-runner').filter({ has: page.locator('.ai-runner-current') }).innerText();
  result.same = await page.evaluate(() => window.__setupSamePage);
  result.posts = posts;
  result.token = await page.evaluate(() => LOCAL_SYNC.token);
  await page.click('#ai-setup-back');
  result.focus = await page.evaluate(() => document.activeElement === $('#ai-runner-note button'));
  await page.click('#ai-start');
  result.done = await viewHas(page, '新星已誕生。', 120000);
});
""", self.boot(runner=None))
        self.assertTrue(all(not row['disabled'] for row in r['rows']))
        self.assertEqual(r['face'], {'start': True, 'copy': False, 'line': '背景 AI：Codex · 更改'})
        self.assertEqual((r['current'], r['same'], r['focus']), ('目前使用', 'same', True))
        self.assertIn('Codex', r['currentRow'])
        self.assertEqual(r['posts'], [{'body': {'runner': 'codex'}, 'token': r['token'], 'origin': self.server.origin}])
        self.assertIn('新星已誕生。', r['done'])
        trace = [json.loads(line) for line in (self.root / 'trace/codex-trace.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any(row['runner'] == 'codex' and row['step'] == 'exec' for row in trace))

    def test_not_ready_radio_is_disabled_and_forced_approval_shows_server_409(self):
        r = self.run_setup(r"""
run(async browser => {
  const page = await startPage(browser);
  await toStep3(page, OWN);
  await setup(page);
  result.rows = await setupRows(page);
  result.approveDisabled = await page.locator('#ai-setup-approve').isDisabled();
  await page.locator('input[value=codex]').evaluate(el => el.click());
  result.picked = await page.evaluate(() => aiSetupPicked);
  // A stale or forced browser choice cannot bypass the real server's approval check.
  await page.evaluate(() => { aiSetupData.runners.find(r => r.name === 'codex').ready = true; aiSetupPicked = 'codex'; renderAISetup(); });
  const response = page.waitForResponse(r => r.url().endsWith('/api/cradle/settings'));
  await page.click('#ai-setup-approve');
  const refused = await response;
  result.status = refused.status(); result.error = (await refused.json()).error;
  await page.waitForFunction(() => !aiSetupBusy);
  result.shown = await page.locator('#ai-setup-error').innerText();
  result.jobs = await server(page, 'jobs');
});
""", self.boot(runner=None, codex_scenario='login-none'), allow409=True)
        codex = next(row for row in r['rows'] if row['name'] == 'codex')
        self.assertTrue(codex['disabled'])
        self.assertIn('Codex 未登入，請先打開 Codex 登入一次。', codex['text'])
        self.assertTrue(r['approveDisabled'])
        self.assertIsNone(r['picked'])
        self.assertEqual((r['status'], r['shown']), (409, r['error']))
        self.assertEqual(r['shown'], 'Codex 未登入，請先打開 Codex 登入一次。')
        self.assertIsNone(r['jobs']['runner']['name'])

    def test_disable_switches_back_to_copy_without_reload(self):
        r = self.run_setup(r"""
run(async browser => {
  const page = await startPage(browser);
  await toStep3(page, OWN); await setup(page);
  result.before = await face(page);
  result.beforeDisable = await page.locator('#ai-setup-disable').isVisible();
  await page.evaluate(() => window.__setupSamePage = true);
  await page.click('#ai-setup-disable');
  await page.waitForFunction(() => !aiSetupBusy && jobList.runner?.name === null);
  result.after = await face(page);
  result.afterDisable = await page.locator('#ai-setup-disable').isVisible();
  result.same = await page.evaluate(() => window.__setupSamePage);
});
""", self.boot())
        self.assertEqual(r['before'], {'start': True, 'copy': False, 'line': '背景 AI：Claude Code · 更改'})
        self.assertEqual(r['after'], {'start': False, 'copy': True, 'line': '未設定背景 AI · 前往 AI 設置'})
        self.assertTrue(r['beforeDisable'])
        self.assertFalse(r['afterDisable'])
        self.assertTrue(r['same'])
        settings = json.loads((self.root / '.reader-runtime/cradle/settings.json').read_text(encoding='utf-8'))
        self.assertIsNone(settings['runner'])

    def test_hash_opens_with_empty_basket_and_is_consumed_once(self):
        r = self.run_setup(r"""
run(async browser => {
  const page = await open(browser, 1440, 900);
  await page.waitForFunction(() => $('#ai-setup').open && !aiSetupBusy && aiSetupData);
  result.first = await page.evaluate(() => ({ hash: location.hash, basket: basket.length, open: $('#ai-setup').open, focused: $('#ai-setup').contains(document.activeElement) }));
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !$('#ai-setup').open && document.activeElement.id === 'basket-open');
  result.closedFocus = await page.evaluate(() => document.activeElement.id);
  await page.reload();
  await page.waitForFunction(() => typeof jobList !== 'undefined' && jobList.seen);
  result.reloaded = await page.evaluate(() => ({ hash: location.hash, open: $('#ai-setup').open }));
});
""", self.boot(runner=None) + '#ai-setup')
        self.assertEqual(r['first'], {'hash': '', 'basket': 0, 'open': True, 'focused': True})
        self.assertEqual(r['closedFocus'], 'basket-open')
        self.assertEqual(r['reloaded'], {'hash': '', 'open': False})

    def test_offline_and_missing_service_show_only_the_offline_sentence(self):
        body = r"""
run(async browser => {
  const page = await open(browser, 390, 844);
  await page.waitForFunction(() => $('#ai-setup').open);
  result.panel = await page.evaluate(() => ({ intro: $('#ai-setup-intro').textContent, controls: $('#ai-setup-controls').checkVisibility(), title: $('#ai-setup-title').checkVisibility(), buttons: [...$('#ai-setup').querySelectorAll('button')].filter(b => b.checkVisibility()).map(b => b.textContent) }));
  await page.click('#ai-setup-back');
  await page.evaluate(() => { basket = allNodes.filter(n => n.kind === 'source').slice(0, 2).map(n => n.id); basketChanged(); openBasket({step: 3}); });
  result.face = await face(page);
});
"""
        offline = self.rich_page() + '#ai-setup'
        for name, url in [('offline', offline), ('no-service', self.boot(cradle=False) + '#ai-setup')]:
            with self.subTest(name=name):
                r = self.run_setup(body, url, allow404=True)
                self.assertEqual(r['panel'], {'intro': '要經本機閱讀服務打開星雲，才可以設定背景 AI；現在仍可用「複製指令」。', 'controls': False, 'title': False, 'buttons': ['返回']})
                self.assertEqual(r['face'], {'start': False, 'copy': True, 'line': ''})

    def test_detect_is_disabled_and_says_detecting_until_the_request_finishes(self):
        r = self.run_setup(r"""
run(async browser => {
  const page = await startPage(browser); await toStep3(page, OWN); await setup(page);
  let release;
  const hold = new Promise(resolve => { release = resolve; });
  await page.route('**/api/cradle/runners?refresh=1', async route => { await hold; await route.continue(); });
  await page.click('#ai-setup-detect');
  result.during = await page.locator('#ai-setup-detect').evaluate(b => ({ text: b.textContent, disabled: b.disabled }));
  release();
  await page.waitForFunction(() => !aiSetupBusy);
  result.after = await page.locator('#ai-setup-detect').evaluate(b => ({ text: b.textContent, disabled: b.disabled }));
});
""", self.boot(runner=None))
        self.assertEqual(r['during'], {'text': '正在偵測…', 'disabled': True})
        self.assertEqual(r['after'], {'text': '偵測', 'disabled': False})

    def test_panel_fits_phone_and_desktop_with_screenshots_and_keyboard_focus(self):
        r = self.run_setup(r"""
run(async browser => {
  for (const [name, w, h] of [['phone', 390, 844], ['desktop', 1440, 900]]) {
    const page = await startPage(browser, w, h); await toStep3(page, OWN); await setup(page);
    result[name] = await page.evaluate(() => {
      const box = e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }, panel = $('#ai-setup');
      const style = getComputedStyle(panel), color = document.createElement('canvas').getContext('2d'); color.fillStyle = style.backgroundColor; color.fillRect(0, 0, 1, 1);
      return { backgroundAlpha: color.getImageData(0, 0, 1, 1).data[3], opacity: style.opacity, rect: box(panel), badge: box($('#basket-badge')), badgeVisible: $('#basket-badge').checkVisibility(), overflow: panel.scrollWidth > panel.clientWidth, children: [...panel.querySelectorAll('button,input,.ai-runner')].filter(e => e.checkVisibility()).map(box), W: innerWidth, H: innerHeight };
    });
    for (let i = 0; i < 12; i++) { await page.keyboard.press('Tab'); if (!await page.evaluate(() => $('#ai-setup').contains(document.activeElement))) throw Error('focus escaped setup'); }
    await page.screenshot({ path: opt.shots + '/ai-setup-' + name + '.png' });
    result[name].shot = opt.shots + '/ai-setup-' + name + '.png';
    await page.keyboard.press('Escape');
    result[name].closed = await page.evaluate(() => !$('#ai-setup').open && document.activeElement === $('#ai-runner-note button'));
  }
});
""", self.boot())
        for name in ('phone', 'desktop'):
            v = r[name]
            a, badge = v['rect'], v['badge']
            self.assertEqual((v['backgroundAlpha'], v['opacity']), (255, '1'))
            self.assertTrue(0 <= a[0] < a[2] <= v['W'] and 0 <= a[1] < a[3] <= v['H'], v)
            self.assertFalse(v['overflow'])
            self.assertTrue(v['badgeVisible'])
            self.assertFalse(a[0] < badge[2] and a[2] > badge[0] and a[1] < badge[3] and a[3] > badge[1])
            self.assertTrue(all(a[0] <= b[0] <= b[2] <= a[2] for b in v['children']))
            self.assertTrue(v['closed'])
            self.assertGreater(Path(v['shot']).stat().st_size, 0)
            print(f"screenshot AI setup {name}: {v['shot']}")

    def test_failed_job_copy_action_still_keeps_the_copy_face(self):
        r = self.run_setup(r"""
run(async browser => {
  const page = await startPage(browser);
  await page.evaluate(async () => { await cradleCall('GET', 'runners'); await jobTick(); });
  // A program can disappear after a successful detection; the resulting failed job still offers copy.
  const path = ctl + '/../.reader-runtime/cradle/settings.json', settings = JSON.parse(fs.readFileSync(path, 'utf8'));
  settings.claude_command = [ctl + '/missing-claude.exe']; fs.writeFileSync(path, JSON.stringify(settings));
  await toStep3(page, OWN); await page.click('#ai-start');
  result.failed = await viewHas(page, '這部電腦找不到 Claude Code。');
  result.buttons = await buttons(page);
  await clickText(page, '改用「複製指令」');
  await page.evaluate(async () => { await jobTick(); });
  result.face = await face(page);
  result.copyPage = await page.evaluate(() => ({ jobHidden: $('#job-view').hidden, start: !$('#ai-start').hidden, copy: $('#ai-copy').checkVisibility(), outRow: !$('#ai-out-row').hidden }));
  await page.click('#ai-copy'); result.copied = await until(page, () => $('#ai-copy-status').textContent, 5000);
  await page.evaluate(() => { setStep(2); setStep(3); });
  result.again = await page.evaluate(() => ({ start: !$('#ai-start').hidden }));
});
""", self.boot())
        self.assertIn('這部電腦找不到 Claude Code。', r['failed'])
        self.assertEqual(r['buttons'], ['改用「複製指令」', '完成', '新孕育'])
        self.assertEqual(r['copyPage'], {'jobHidden': True, 'start': False, 'copy': True, 'outRow': True})
        self.assertFalse(r['face']['start'])
        self.assertTrue(r['face']['copy'])
        self.assertEqual(r['copied'], '✓ 新想法正在孕育，貼到 Claude 或 Codex，AI 會直接深入思考')
        self.assertEqual(r['again'], {'start': True})


class NestedVaultIncubationTests(PageCase):
    """Run the successful background birth once with the PUBLIC nested-vault layout."""
    VAULT = 'notes/vault'
    boot = IncubationTests.boot

    def run_jobs(self, body, url, timeout=900, **options):
        self.assertTrue(url.endswith('/notes/vault/outcome-pages/nebula.html'), url)
        return IncubationTests.run_jobs(self, body, url, timeout=timeout, **options)

    def test_thinking_job_to_a_born_star(self):
        IncubationTests.test_thinking_job_to_a_born_star(self)


if __name__ == '__main__':
    unittest.main()

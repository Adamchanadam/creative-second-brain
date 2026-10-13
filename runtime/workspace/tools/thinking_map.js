/* Offline reader state only. No requests, persistence, scoring or inference. */
(() => {
  'use strict';
  const requested = new URLSearchParams(location.search).get('return');
  const back = document.getElementById('return-link');
  if (back && requested && /^(index|knowledge|thinking)\.html(?:\?[A-Za-z0-9._~%=&+,-]*)?$/.test(requested)) {
    back.href = '../' + requested;
    back.textContent = '返回剛才的目錄 ↗';
  }
  if (location.protocol === 'file:') document.querySelectorAll('[data-obsidian-path]').forEach(link => {
    const target = new URL(link.dataset.obsidianPath, location.href);
    if (target.protocol !== 'file:') return;
    let path = decodeURIComponent(target.pathname);
    if (/^\/[A-Za-z]:\//.test(path)) path = path.slice(1);
    if (target.hostname) path = '//' + target.hostname + path;
    link.href = 'obsidian://open?path=' + encodeURIComponent(path);
    link.hidden = false;
  });
  const models = JSON.parse(document.getElementById('map-data').textContent);
  const labels = ['問題怎樣改變', '材料在哪裏拉扯', '背後怎樣發生', '走到另一個觀點'];
  const lenses = ['shift', 'collision', 'mechanism', 'adjacent'];
  const roles = {source:'材料的作用 · 原文短引另列',inference:'推理／假說',challenge:'競爭解釋',frame:'問題框架',possibility:'延伸觀點／待探索',condition:'待確認的成立條件'};
  const status = document.getElementById('map-status');
  const states = new Map();
  const small = () => window.matchMedia('(max-width:900px)').matches;
  function paragraph(text) { const p = document.createElement('p'); p.textContent = text; return p; }
  function revealTarget(hash) {
    let id; try { id = decodeURIComponent(hash.replace(/^#/, '')); } catch { return; }
    const target = document.getElementById(id); if (!target) return;
    const owner = target.closest('.insight');
    if (owner) activateInsight(owner.id);
    let el = target; while (el) { if (el.tagName === 'DETAILS') el.open = true; el = el.parentElement; }
    return target;
  }
  function selectNode(article, data, nodeId, move=false) {
    const node = data.nodes[nodeId]; if (!node) return;
    states.get(data.id).node = nodeId;
    article.querySelectorAll('[data-node]').forEach(a => {
      const active = a.dataset.node === nodeId;
      a.dataset.selected = String(active); a.setAttribute('aria-pressed', String(active));
    });
    article.querySelectorAll('.connection').forEach(e => { e.dataset.active = String(e.dataset.from===nodeId || e.dataset.to===nodeId); });
    const panel = article.querySelector('.inspector');
    panel.querySelector('.inspector-role').textContent = roles[node.role];
    panel.querySelector('.inspector-title').textContent = node.label;
    const body = panel.querySelector('.inspector-body');
    body.replaceChildren(...[node.body, ...node.detail].map(paragraph)); body.scrollTop = 0;
    const refs = panel.querySelector('.inspector-links'); refs.replaceChildren();
    node.refs.forEach(id => { const a = document.createElement('a'); a.href = '#evidence-' + id; a.textContent = '閱讀 ' + id + ' 原始材料 ↗'; refs.append(a); });
    panel.querySelector('.inspector-deep').href = '#' + data.id + '-read-' + nodeId;
    status.textContent = node.label + '。完整內容已展開。';
    if (move && small()) { panel.focus({preventScroll:true}); panel.scrollIntoView({block:'start',behavior:'auto'}); }
  }
  function setLens(article, data, lens, move=false) {
    if (!data.views[lens]) return;
    states.get(data.id).lens = lens;
    article.querySelectorAll('[data-scene]').forEach(e => { e.hidden = e.dataset.scene!==lens; });
    article.querySelectorAll('[data-lens]').forEach(e => e.setAttribute('aria-pressed', String(e.dataset.lens===lens)));
    const index = lenses.indexOf(lens);
    article.querySelector('.tour-state').textContent = '0' + (index+1) + ' / 04 · ' + labels[index];
    article.querySelector('.tour-prev').disabled = index===0;
    article.querySelector('.tour-next').textContent = index===3 ? '回到第一個角度 ↺' : '下一個角度 →';
    selectNode(article, data, data.views[lens].focus);
    if (move) article.querySelector('.visual-workspace').scrollIntoView({block:'start',behavior:'auto'});
  }
  function activateInsight(id) {
    document.querySelectorAll('.insight').forEach(a => { a.hidden = a.id!==id; });
    document.querySelectorAll('[data-insight]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.insight===id)));
  }
  function setView(view) {
    document.body.dataset.view = view;
    document.querySelectorAll('button[data-view]').forEach(b => b.setAttribute('aria-pressed',String(b.dataset.view===view)));
    document.querySelectorAll('.full-argument').forEach(d => { d.open=view==='text'; });
  }
  models.forEach(data => {
    const article = document.getElementById(data.id);
    states.set(data.id, {lens:'shift', node:'delta'});
    article.querySelectorAll('[data-lens]').forEach(b => b.addEventListener('click', () => setLens(article,data,b.dataset.lens)));
    article.querySelectorAll('[data-node]').forEach(a => {
      a.addEventListener('click',e => { e.preventDefault(); selectNode(article,data,a.dataset.node,true); });
      if (a.tagName.toLowerCase()==='a') a.addEventListener('keydown', e=>{ if (e.key===' ') { e.preventDefault(); selectNode(article,data,a.dataset.node,true); } });
    });
    article.querySelector('.tour-prev').addEventListener('click',()=>setLens(article,data,lenses[Math.max(0,lenses.indexOf(states.get(data.id).lens)-1)],true));
    article.querySelector('.tour-next').addEventListener('click',()=>setLens(article,data,lenses[(lenses.indexOf(states.get(data.id).lens)+1)%4],true));
    article.querySelector('.back-to-map').addEventListener('click',()=>{
      const lens=states.get(data.id).lens;
      article.querySelector('[data-lens="'+lens+'"]').focus({preventScroll:true});
      article.querySelector('.visual-workspace').scrollIntoView({block:'start',behavior:'auto'});
    });
    article.querySelectorAll('[data-result]').forEach(b=>b.addEventListener('click',()=>{
      article.dataset.scenario=b.dataset.result;
      article.querySelector('.result').textContent=article.querySelector('.result').dataset[b.dataset.result];
      article.querySelector('.scenario-label').textContent={unknown:'目前：還未作觀察',supports:'假設情境：觀察支持主張',undermines:'假設情境：觀察削弱主張'}[b.dataset.result];
      article.querySelectorAll('[data-result]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));
    }));
    article.querySelector('.copy').addEventListener('click',async()=>{
      const t=article.querySelector('textarea'), out=article.querySelector('.copy-status');
      try { if (!navigator.clipboard) throw new Error('clipboard unavailable'); await navigator.clipboard.writeText(t.value); out.textContent='已複製追問'; }
      catch { t.focus(); t.select(); out.textContent='文字已選取，請按複製快捷鍵。'; }
    });
    setLens(article,data,'shift');
  });
  document.querySelectorAll('[data-insight]').forEach(b=>b.addEventListener('click',()=>activateInsight(b.dataset.insight)));
  document.querySelectorAll('button[data-view]').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
  document.addEventListener('click', e => { const a=e.target.closest('a[href^="#"]'); if (a && !a.dataset.node) revealTarget(a.hash); });
  window.addEventListener('hashchange',()=>revealTarget(location.hash));
  // Print and no-script routes retain all full arguments, including unselected ideas.
  let printState=[];
  window.addEventListener('beforeprint',()=>{ printState=[...document.querySelectorAll('details')].map(d=>[d,d.open]); document.querySelectorAll('.full-argument,.next-question details').forEach(d=>d.open=true); });
  window.addEventListener('afterprint',()=>printState.forEach(([d,open])=>d.open=open));
  document.body.dataset.enhanced='true';
  if (models.length) activateInsight(models[0].id);
  setView('text');
  if (location.hash) revealTarget(location.hash);
})();

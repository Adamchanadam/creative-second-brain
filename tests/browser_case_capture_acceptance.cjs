/*
 * Controlled-browser acceptance for source interaction capture.
 *
 * This starts local fixtures only.  It proves the portable capture tool accepts
 * a light interactive source and rejects blank, ambiguous and overlay-blocked
 * pages rather than treating a screenshot as proof of interaction.
 */
const assert = require('assert');
const childProcess = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const path = require('path');

function arg(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || !process.argv[index + 1]) throw new Error(`missing ${name}`);
  return process.argv[index + 1];
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n');
}

function runNode(tool, workspace, argumentsList, expected = 0) {
  // Do not use spawnSync: this test's localhost fixture lives on this same
  // event loop and needs to serve the child browser while it runs.
  return new Promise((resolve, reject) => {
    const child = childProcess.spawn(process.execPath, [tool, ...argumentsList], { cwd: workspace, windowsHide: true });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', data => { stdout += data.toString(); });
    child.stderr.on('data', data => { stderr += data.toString(); });
    child.on('error', reject);
    child.on('close', status => {
      if (status !== expected) {
        reject(new Error(`expected ${expected}, got ${status}: ${argumentsList.join(' ')}\n${stdout}\n${stderr}`));
        return;
      }
      resolve({ stdout, stderr });
    });
  });
}

function requestFor(sourceUrl, selector = '#open', expectedSelector = '#details') {
  return {
    schema_version: 'case_source_capture_request_v1',
    source_url: sourceUrl,
    action: {
      id: 'open-details',
      selector,
      before_state: 'details closed',
      after_state: 'details open',
      expect_selector: expectedSelector,
      expect_text: 'Expanded details',
    },
    desktop: { width: 800, height: 600 },
    mobile: { width: 390, height: 844 },
  };
}

function fixtureHtml(kind) {
  if (kind === 'good') return `<!doctype html><meta charset="utf-8"><style>
    body { background:#fbfaf5; color:#17263d; font:18px sans-serif; padding:32px; }
    button { padding:12px; } #details { margin-top:20px; border:1px solid #aaa; padding:12px; }
  </style><main><h1>Light interactive source</h1><p>This is a visible source surface.</p>
  <button id="open" aria-expanded="false">Open details</button><section id="details" hidden>Expanded details appear here.</section></main>
  <script>document.querySelector('#open').addEventListener('click', e => { e.currentTarget.setAttribute('aria-expanded', 'true'); document.querySelector('#details').hidden = false; });</script>`;
  if (kind === 'overlay') return `<!doctype html><meta charset="utf-8"><style>
    body { font:18px sans-serif; padding:32px; } #cover { position:fixed; inset:0; background:rgba(255,255,255,.96); z-index:5; }
  </style><main><h1>Overlay fixture</h1><button id="open">Open details</button><section id="details" hidden>Expanded details</section></main><div id="cover">Visible consent layer blocks the action</div>`;
  if (kind === 'repeated') return `<!doctype html><meta charset="utf-8"><main><h1>Repeated controls fixture</h1><button class="open">One</button><button class="open">Two</button><section id="details">Expanded details</section></main>`;
  if (kind === 'blank') return '<!doctype html><meta charset="utf-8"><body></body>';
  throw new Error(`unknown fixture ${kind}`);
}

async function main() {
  const workspace = path.resolve(arg('--workspace'));
  const tool = path.join(workspace, 'tools', 'case_reproduction_capture.js');
  assert(fs.existsSync(tool), 'capture tool is missing from the installed workspace');
  const check = await runNode(tool, workspace, ['--check']);
  assert(check.stdout.includes('"status":"ready"'), `browser capture readiness failed\n${check.stdout}\n${check.stderr}`);

  const testRoot = path.join(workspace, 'outputs', 'browser-capture-acceptance');
  assert(!fs.existsSync(testRoot), 'browser capture acceptance root must be new');
  fs.mkdirSync(path.join(testRoot, 'requests'), { recursive: true });
  const server = http.createServer((request, response) => {
    const route = (request.url || '/').split('?')[0];
    const kind = route.replace(/^\//, '').replace(/\.html$/, '') || 'good';
    let body;
    try {
      body = fixtureHtml(kind);
    } catch {
      response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      response.end('not found');
      return;
    }
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
    response.end(body);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert(address && typeof address === 'object');
  const origin = `http://127.0.0.1:${address.port}`;
  try {
    const goodRequest = path.join('outputs', 'browser-capture-acceptance', 'requests', 'good.json');
    writeJson(path.join(workspace, goodRequest), requestFor(`${origin}/good.html`));
    const goodOutput = path.join('outputs', 'browser-capture-acceptance', 'good');
    const good = await runNode(tool, workspace, ['--root', workspace, '--request', goodRequest, '--output-dir', goodOutput]);
    const goodStatus = JSON.parse(good.stdout);
    assert.strictEqual(goodStatus.status, 'captured');
    const receiptPath = path.join(workspace, goodStatus.receipt);
    const receipt = JSON.parse(fs.readFileSync(receiptPath, 'utf8'));
    assert.strictEqual(receipt.source_url, `${origin}/good.html`);
    assert.strictEqual(receipt.state_changed, true);
    assert.notDeepStrictEqual(receipt.observed_state_change.before, receipt.observed_state_change.after);
    assert.strictEqual(receipt.before.sha256, sha256(path.join(workspace, goodOutput, 'desktop-before.png')));
    assert.strictEqual(receipt.after.sha256, sha256(path.join(workspace, goodOutput, 'desktop-after.png')));
    assert(fs.statSync(path.join(workspace, goodOutput, 'mobile-after.png')).size > 100);

    const badCases = [
      ['overlay', requestFor(`${origin}/overlay.html`), 'blocked by another visible layer'],
      ['repeated', requestFor(`${origin}/repeated.html`, '.open'), 'match exactly one visible element'],
      ['blank', requestFor(`${origin}/blank.html`), 'page has no visible source surface'],
    ];
    for (const [name, request, expectedMessage] of badCases) {
      const requestPath = path.join('outputs', 'browser-capture-acceptance', 'requests', `${name}.json`);
      writeJson(path.join(workspace, requestPath), request);
      const result = await runNode(tool, workspace, [
        '--root', workspace, '--request', requestPath, '--output-dir', path.join('outputs', 'browser-capture-acceptance', name),
      ], 2);
      assert((result.stdout + result.stderr).includes(expectedMessage), `${name} did not reject the intended counterexample`);
    }
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
  console.log(JSON.stringify({
    status: 'passed',
    coverage: ['light interactive source', 'observable post-action state change', 'desktop/mobile receipt hashes', 'overlay blocker', 'repeated action controls', 'blank source surface'],
    not_proven: ['live external source', 'native Obsidian', 'host skill discovery'],
  }));
}

main().catch(error => { console.error(error.stack || error); process.exit(1); });

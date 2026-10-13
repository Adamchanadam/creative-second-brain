/* Local-only browser acceptance for the novice delivery page. */
const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');

// These values are independently aligned to OPS default prompts.  Do not
// derive them from the candidate registry: that would repeat the r5
// self-comparison failure instead of detecting it.
const DAILY_PROMPTS = {
  think: '$brain-think [問題]',
  url: '$brain-url [網址]',
  text: '$brain-text\n[貼上原文]',
  image: '$brain-image [附上一張圖片]',
  grow: '$brain-grow [來源]',
  create: '$brain-create [想做的成品]',
  research: '$brain-research 查看目前研究主題',
  tidy: '$brain-tidy',
  guide: '$brain-guide',
  nebula: '$brain-nebula',
  check: '$brain-check [來源或工作編號]',
  resume: '$brain-resume [來源或工作編號]',
  fix: '$brain-fix [哪裏有甚麼問題]',
  update: '$brain-update',
};

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || !process.argv[index + 1]) throw new Error(`missing ${name}`);
  return process.argv[index + 1];
}

function optionalArgument(name, fallback) {
  const index = process.argv.indexOf(name);
  return index < 0 ? fallback : argument(name);
}

function contained(root, relative) {
  if (path.isAbsolute(relative)) throw new Error('evidence path must be relative');
  const full = path.resolve(root, relative);
  if (!full.startsWith(root + path.sep)) throw new Error('evidence path escapes delivery root');
  return full;
}

async function main() {
  const root = path.resolve(argument('--delivery'));
  const evidenceRelative = argument('--evidence');
  const entry = optionalArgument('--entry', 'index.html');
  const packagePrefix = optionalArgument('--package-prefix', 'creative-second-brain/');
  const manifest = JSON.parse(fs.readFileSync(contained(root, 'delivery-manifest.json'), 'utf8'));
  const archiveHref = optionalArgument('--archive-href', manifest.archive);
  const evidence = contained(root, evidenceRelative);
  contained(root, entry);
  contained(root, packagePrefix);
  contained(root, archiveHref);
  if (!fs.statSync(root).isDirectory()) throw new Error('delivery root is missing');
  const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
    '.png': 'image/png', '.txt': 'text/plain; charset=utf-8', '.md': 'text/markdown; charset=utf-8' };
  const server = http.createServer((request, response) => {
    try {
      const parsed = new URL(request.url, 'http://127.0.0.1');
      if (parsed.pathname === '/favicon.ico') { response.writeHead(204); response.end(); return; }
      const relative = decodeURIComponent(parsed.pathname).replace(/^\/+/, '') || 'index.html';
      const file = contained(root, relative);
      const data = fs.readFileSync(file);
      response.writeHead(200, { 'content-type': mime[path.extname(file)] || 'application/octet-stream' });
      response.end(data);
    } catch (error) {
      response.writeHead(error && error.code === 'ENOENT' ? 404 : 403, { 'content-type': 'text/plain; charset=utf-8' });
      response.end('not found');
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const url = `http://127.0.0.1:${port}/${entry}`;
  let browser;
  try {
    // A locally installed Chromium browser is an accepted fallback when the
    // bundled Playwright browser cache is intentionally absent.
    browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_BROWSER_EXECUTABLE || undefined });
    const page = await browser.newPage({ viewport: { width: 1365, height: 900 } });
    await page.addInitScript(() => {
      window.__copiedPrompts = [];
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async text => window.__copiedPrompts.push(text) },
      });
    });
    const consoleMessages = [];
    page.on('console', message => {
      if (['warning', 'error'].includes(message.type())) consoleMessages.push(`${message.type()}: ${message.text()}`);
    });
    const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 15000 });
    if (!response || response.status() !== 200 || page.url() !== url) throw new Error('root guide did not load at its localhost URL');
    const visible = await page.locator('body').innerText();
    for (const phrase of ['你的材料，', '完整安裝包：', '保存、提問、創作、整理與接續', '全部日常入口']) {
      if (!visible.includes(phrase)) throw new Error(`required visible text missing: ${phrase}`);
    }
    if (visible.includes('開始前可能想知道') || await page.locator('#limits, a[href="#limits"]').count()) {
      throw new Error('removed Q&A section is still exposed');
    }
    if (!(await page.locator('#sources h2').count()) || (await page.locator('#sources h2').innerText()) !== '06 / 遇到問題怎麼辦') {
      throw new Error('support section number or title is incorrect');
    }
    const footer = page.locator('footer');
    if (!(await footer.innerText()).includes('AI 生活策展人 Adam Chan') ||
        !(await footer.locator('a[href="https://www.facebook.com/chan.adam"]').count())) {
      throw new Error('footer attribution or Facebook link is missing');
    }
    if (await page.locator('button.copy').count() !== Object.keys(DAILY_PROMPTS).length + 1) throw new Error('installation plus every daily copy control are required');
    if (!(await page.locator(`a[href="${archiveHref}"]`).count())) throw new Error('versioned ZIP link missing');
    if (!(await page.locator('.hero-art').evaluate(image => image.complete && image.naturalWidth > 0))) throw new Error('hero brand image did not load');
    if (!(await page.locator('.studio-art').evaluate(image => image.complete && image.naturalWidth > 0))) throw new Error('studio brand image did not load');
    const setup = page.locator('#obsidian-cli');
    await setup.evaluate(node => { node.open = true; });
    const setupImage = page.locator('#obsidian-cli-example');
    await setupImage.scrollIntoViewIfNeeded();
    await page.waitForFunction(() => {
      const image = document.querySelector('#obsidian-cli-example');
      return image && image.complete && image.naturalWidth > 0;
    });
    const setupImagePath = packagePrefix + 'assets/setup/obsidian-cli-1.13.7-zh-tw.png';
    if (await setupImage.getAttribute('src') !== setupImagePath) throw new Error('portable Obsidian setup image is not rooted in this delivery');
    if (!(await page.locator(`#obsidian-cli a[href="${setupImagePath}"]`).count())) throw new Error('Obsidian setup image cannot be opened locally');

    for (const [key, expected] of Object.entries(DAILY_PROMPTS)) {
      const field = page.locator(`#daily-${key}`);
      const card = field.locator('xpath=ancestor::details[1]');
      await card.evaluate(node => { node.open = true; });
      await field.scrollIntoViewIfNeeded();
      if (await field.inputValue() !== expected) throw new Error(`daily prompt mismatch: ${key}`);
      const box = await field.boundingBox();
      if (!box || box.width <= 0 || box.height < 72 || box.height > 100) throw new Error(`daily prompt has an unusable visual size: ${key}`);
      await page.locator(`#daily-${key} + button.copy`).click();
    }
    const prompt = await page.locator('#install-command').inputValue();
    if (!prompt.includes(`完整技能套件位置：http://127.0.0.1:${port}/${packagePrefix}`)) throw new Error('prompt does not locate its complete package');
    await page.locator('#install-command + button.copy').click();
    const copied = await page.evaluate(() => window.__copiedPrompts.slice());
    const expectedCopies = [...Object.values(DAILY_PROMPTS), prompt];
    if (copied.length !== expectedCopies.length || copied.some((value, index) => value !== expectedCopies[index])) {
      throw new Error('every visible copy control must send its own complete prompt');
    }
    await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined }));
    const fallback = page.locator('#daily-text');
    await page.locator('#daily-text + button.copy').click();
    const selected = await fallback.evaluate(field => ({ start: field.selectionStart, end: field.selectionEnd, length: field.value.length }));
    if (selected.start !== 0 || selected.end !== selected.length) throw new Error('clipboard fallback did not select the complete daily prompt');
    if (!(await fallback.locator('xpath=following-sibling::span[contains(@class,"status-line")]').innerText()).includes('已選取整段文字')) {
      throw new Error('clipboard fallback status is missing');
    }
    await page.evaluate(() => {
      const field = document.querySelector('#daily-text');
      field.setSelectionRange(0, 0);
      field.blur();
      field.parentElement.querySelector('.status-line').textContent = '';
    });
    fs.mkdirSync(evidence, { recursive: true });
    const desktop = path.join(evidence, 'delivery-desktop.png');
    await page.screenshot({ path: desktop, fullPage: false });
    const promptsDesktop = path.join(evidence, 'delivery-prompts-desktop.png');
    await page.locator('#prompts').scrollIntoViewIfNeeded();
    await page.screenshot({ path: promptsDesktop, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#obsidian-cli').scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);
    const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    if (mobileOverflow) throw new Error('390px mobile layout has horizontal overflow');
    const mobile = path.join(evidence, 'delivery-mobile.png');
    await page.screenshot({ path: mobile, fullPage: false });
    await page.setViewportSize({ width: 320, height: 844 });
    const narrowOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    if (narrowOverflow) throw new Error('320px mobile layout has horizontal overflow');
    if (consoleMessages.length) throw new Error('browser console not clean: ' + consoleMessages.join(' | '));
    const report = { status: 'passed', url, visible: ['你的材料，', '完整安裝包：', '14 daily entries'],
      daily_prompts: Object.keys(DAILY_PROMPTS), copy_controls: expectedCopies.length,
      console: { warning: 0, error: 0 }, screenshots: {
        desktop: { relative: path.join(evidenceRelative, 'delivery-desktop.png'), bytes: fs.statSync(desktop).size },
        prompts_desktop: { relative: path.join(evidenceRelative, 'delivery-prompts-desktop.png'), bytes: fs.statSync(promptsDesktop).size },
        mobile: { relative: path.join(evidenceRelative, 'delivery-mobile.png'), bytes: fs.statSync(mobile).size }
      }, server: 'closed in finally' };
    if (!report.screenshots.desktop.bytes || !report.screenshots.prompts_desktop.bytes || !report.screenshots.mobile.bytes) throw new Error('empty screenshot');
    fs.writeFileSync(path.join(evidence, 'browser-acceptance.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error.stack || error); process.exit(1); });

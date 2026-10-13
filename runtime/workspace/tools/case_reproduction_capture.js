#!/usr/bin/env node
/*
 * Capture one explicitly reviewed source interaction.  This is deliberately
 * narrower than a generic browser runner: it accepts a single visible CSS
 * target, bounded scroll positions and a declared visible post-condition; it
 * never evaluates request-provided JavaScript or launches arbitrary commands.
 */
const crypto = require('crypto');
const childProcess = require('child_process');
const fs = require('fs');
const path = require('path');

// Keep Chromium with this lockfile. The check and later capture use the same
// hermetic location instead of depending on a one-shot shell export.
if (!process.env.PLAYWRIGHT_BROWSERS_PATH) process.env.PLAYWRIGHT_BROWSERS_PATH = '0';

const REQUEST_SCHEMA = 'case_source_capture_request_v1';
const RECEIPT_SCHEMA = 'case_source_capture_v1';
const MAX_SCROLL = 100000;

function fail(message, code = 'capture_failed', phase = 'unknown') {
  const error = new Error(message);
  error.captureCode = code;
  error.capturePhase = phase;
  throw error;
}

function arg(name, fallback = undefined) {
  const index = process.argv.indexOf(name);
  if (index < 0) return fallback;
  const value = process.argv[index + 1];
  if (!value) fail(`missing ${name}`);
  return value;
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function safeId(value, label) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(value)) fail(`invalid ${label}`);
  return value;
}

function inside(root, raw, label) {
  if (typeof raw !== 'string' || !raw.trim() || path.isAbsolute(raw)) fail(`${label} must be workspace-relative`);
  const target = path.resolve(root, raw);
  if (target !== root && !target.startsWith(root + path.sep)) fail(`${label} escapes workspace`);
  return target;
}

function boundedNumber(value, label, lower, upper) {
  if (!Number.isInteger(value) || value < lower || value > upper) fail(`invalid ${label}`);
  return value;
}

function boundedTimeout(value, label, fallback) {
  if (value === undefined) return fallback;
  return boundedNumber(value, label, 250, 30000);
}

function requestData(root) {
  const raw = arg('--request');
  const file = inside(root, raw, 'request');
  if (!fs.existsSync(file)) fail('request is missing');
  let data;
  try { data = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { fail('request is not valid JSON'); }
  if (!data || data.schema_version !== REQUEST_SCHEMA) fail('request has an unsupported schema');
  let source;
  try { source = new URL(data.source_url); } catch { fail('request source_url is invalid'); }
  if (!['http:', 'https:'].includes(source.protocol)) fail('request source_url must use http or https');
  const action = data.action;
  if (!action || typeof action !== 'object') fail('request action is required');
  const actionId = safeId(action.id, 'action id');
  const actionKind = action.kind === undefined ? 'click' : action.kind;
  if (!['click', 'scroll', 'wheel_scroll', 'click_then_scroll'].includes(actionKind)) fail('request action kind is unsupported');
  if (['click', 'click_then_scroll'].includes(actionKind) && (typeof action.selector !== 'string' || !action.selector.trim() || action.selector.length > 500)) fail('request action selector is invalid');
  if (typeof action.before_state !== 'string' || !action.before_state.trim()) fail('request action before_state is required');
  if (typeof action.after_state !== 'string' || !action.after_state.trim() || action.after_state.trim() === action.before_state.trim()) fail('request action after_state must differ');
  if (typeof action.expect_selector !== 'string' || !action.expect_selector.trim() || action.expect_selector.length > 500) fail('request action expect_selector is required');
  if (action.expect_text !== undefined && (typeof action.expect_text !== 'string' || !action.expect_text.trim())) fail('request action expect_text is invalid');
  if (action.ready_selector !== undefined && (typeof action.ready_selector !== 'string' || !action.ready_selector.trim() || action.ready_selector.length > 500)) fail('request action ready_selector is invalid');
  if (action.ready_text !== undefined && (typeof action.ready_text !== 'string' || !action.ready_text.trim())) fail('request action ready_text is invalid');
  if (action.ready_blocker_selector !== undefined && (typeof action.ready_blocker_selector !== 'string' || !action.ready_blocker_selector.trim() || action.ready_blocker_selector.length > 500)) fail('request action ready_blocker_selector is invalid');
  if (action.ready_blocker_text !== undefined && (typeof action.ready_blocker_text !== 'string' || !action.ready_blocker_text.trim() || action.ready_blocker_text.length > 500)) fail('request action ready_blocker_text is invalid');
  const beforeScroll = action.before_scroll_y === undefined ? 0 : boundedNumber(action.before_scroll_y, 'before_scroll_y', 0, MAX_SCROLL);
  const afterScroll = action.after_scroll_y === undefined ? beforeScroll : boundedNumber(action.after_scroll_y, 'after_scroll_y', 0, MAX_SCROLL);
  if (['scroll', 'wheel_scroll', 'click_then_scroll'].includes(actionKind) && beforeScroll === afterScroll) fail('scroll action must change the scroll position');
  const expectFrontmost = action.expect_frontmost === undefined ? true : action.expect_frontmost;
  if (typeof expectFrontmost !== 'boolean' || (!expectFrontmost && !['scroll', 'wheel_scroll', 'click_then_scroll'].includes(actionKind))) fail('expect_frontmost=false is only allowed for a scroll action');
  const readyTimeoutMs = boundedTimeout(data.ready_timeout_ms, 'ready_timeout_ms', 12000);
  const postActionTimeoutMs = boundedTimeout(data.post_action_timeout_ms, 'post_action_timeout_ms', 12000);
  const mobileInteraction = data.mobile_interaction === undefined ? true : data.mobile_interaction;
  if (typeof mobileInteraction !== 'boolean') fail('mobile_interaction must be a boolean when provided');
  const mobileReadySelector = data.mobile_ready_selector === undefined ? '' : data.mobile_ready_selector;
  if (typeof mobileReadySelector !== 'string' || mobileReadySelector.length > 500) fail('mobile_ready_selector is invalid');
  const mobileReadyText = data.mobile_ready_text === undefined ? '' : data.mobile_ready_text;
  if (typeof mobileReadyText !== 'string' || mobileReadyText.length > 500) fail('mobile_ready_text is invalid');
  const mobileReadyBlockerSelector = data.mobile_ready_blocker_selector === undefined ? '' : data.mobile_ready_blocker_selector;
  if (typeof mobileReadyBlockerSelector !== 'string' || mobileReadyBlockerSelector.length > 500) fail('mobile_ready_blocker_selector is invalid');
  const mobileReadyBlockerText = data.mobile_ready_blocker_text === undefined ? '' : data.mobile_ready_blocker_text;
  if (typeof mobileReadyBlockerText !== 'string' || mobileReadyBlockerText.length > 500) fail('mobile_ready_blocker_text is invalid');
  const mobileReadyExpectFrontmost = data.mobile_ready_expect_frontmost === undefined ? true : data.mobile_ready_expect_frontmost;
  if (typeof mobileReadyExpectFrontmost !== 'boolean') fail('mobile_ready_expect_frontmost is invalid');
  const desktop = data.desktop || { width: 1365, height: 900 };
  const mobile = data.mobile || { width: 390, height: 844 };
  for (const [label, viewport] of Object.entries({ desktop, mobile })) {
    if (!viewport || typeof viewport !== 'object') fail(`${label} viewport is invalid`);
    boundedNumber(viewport.width, `${label} viewport width`, 160, 4000);
    boundedNumber(viewport.height, `${label} viewport height`, 160, 4000);
  }
  return { sourceUrl: source.href, action: { id: actionId, kind: actionKind, selector: typeof action.selector === 'string' ? action.selector : '', readySelector: action.ready_selector?.trim() || '', readyText: action.ready_text?.trim() || '', readyBlockerSelector: action.ready_blocker_selector?.trim() || '', readyBlockerText: action.ready_blocker_text?.trim() || '', beforeState: action.before_state.trim(), afterState: action.after_state.trim(), expectSelector: action.expect_selector, expectText: action.expect_text?.trim() || '', beforeScroll, afterScroll, expectFrontmost }, desktop, mobile, mobileInteraction, mobileReadiness: { selector: mobileReadySelector.trim(), expectedText: mobileReadyText.trim(), blockerSelector: mobileReadyBlockerSelector.trim(), blockerText: mobileReadyBlockerText.trim(), expectFrontmost: mobileReadyExpectFrontmost }, timing: { readyTimeoutMs, postActionTimeoutMs } };
}

function globalNodeModules() {
  if (process.platform === 'win32' && process.env.APPDATA) {
    return path.join(process.env.APPDATA, 'npm', 'node_modules');
  }
  try {
    return childProcess.execFileSync('npm', ['root', '-g'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  } catch { return ''; }
}

function playwrightForCapture() {
  const globalRoot = globalNodeModules();
  const candidates = [
    { target: 'playwright', label: 'project_playwright' },
    { target: 'playwright-core', label: 'project_playwright_core' },
    ...(globalRoot ? [
      { target: path.join(globalRoot, 'playwright'), label: 'global_playwright' },
      { target: path.join(globalRoot, 'playwright-core'), label: 'global_playwright_core' },
      { target: path.join(globalRoot, 'agent-browser', 'node_modules', 'playwright-core'), label: 'global_agent_browser_playwright_core' },
    ] : []),
  ];
  for (const candidate of candidates) {
    try {
      const runtime = require(candidate.target);
      const packageFile = require.resolve(path.join(candidate.target, 'package.json'));
      return { runtime, packageName: candidate.label, packageVersion: require(packageFile).version };
    } catch { /* Try the next already-installed local module. */ }
  }
  fail('Playwright is unavailable. This capture tool does not install dependencies; preserve the blocked work and use the supported channel only after Playwright and a supported browser are present.');
}

function browserTarget() {
  const configured = process.env.PLAYWRIGHT_BROWSER_EXECUTABLE || '';
  if (configured && fs.existsSync(configured)) {
    return {
      launch: { headless: true, executablePath: configured },
      channel: 'playwright-configured-browser',
      browser: path.basename(configured),
      browserVersion: browserVersion(configured),
    };
  }
  return {
    launch: { headless: true },
    channel: 'playwright-bundled-chromium',
    browser: 'Playwright Chromium',
    browserVersion: 'managed by Playwright',
  };
}

function lockedPlaywrightForCheck() {
  const toolsRoot = __dirname;
  const packageFile = path.join(toolsRoot, 'node_modules', 'playwright', 'package.json');
  const lockFile = path.join(toolsRoot, 'package-lock.json');
  if (!fs.existsSync(packageFile) || !fs.existsSync(lockFile)) {
    fail('workspace-local locked Playwright is unavailable');
  }
  const locked = JSON.parse(fs.readFileSync(lockFile, 'utf8'))?.packages?.['node_modules/playwright']?.version;
  const installed = JSON.parse(fs.readFileSync(packageFile, 'utf8'))?.version;
  if (!locked || installed !== locked) fail('workspace-local Playwright does not match package-lock.json');
  const resolved = require.resolve('playwright', { paths: [toolsRoot] });
  const localRoot = path.resolve(toolsRoot, 'node_modules') + path.sep;
  if (!path.resolve(resolved).startsWith(localRoot)) fail('Playwright resolved outside the workspace tools directory');
  return { runtime: require(resolved), packageVersion: installed };
}

function browserVersion(executable) {
  try {
    return childProcess.execFileSync(executable, ['--version'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  } catch { return 'unavailable'; }
}

async function visibleUnique(page, selector, label, { timeoutMs, expectedText = '', failureCode, phase, expectFrontmost = true, allowMultipleWhenOneVisible = false }) {
  const target = page.locator(selector);
  const deadline = Date.now() + timeoutMs;
  let lastState = 'not inspected';
  while (Date.now() <= deadline) {
    try {
      const count = await target.count();
      let candidate = target;
      if (count !== 1) {
        if (!allowMultipleWhenOneVisible) {
          lastState = `matched ${count} elements`;
          await page.waitForTimeout(125);
          continue;
        }
        const visible = [];
        for (let index = 0; index < count; index += 1) {
          const item = target.nth(index);
          if (await item.isVisible()) visible.push(item);
        }
        if (visible.length !== 1) {
          lastState = `matched ${count} elements, ${visible.length} visible`;
          await page.waitForTimeout(125);
          continue;
        }
        candidate = visible[0];
      }
      if (!await candidate.isVisible()) {
        lastState = 'matched element is not visible';
      } else {
        const inspected = await candidate.evaluate(node => {
          const rect = node.getBoundingClientRect();
          if (rect.width < 1 || rect.height < 1) return { frontmost: false, text: '' };
          const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
          return { frontmost: top === node || node.contains(top), text: (node.innerText || '').replace(/\s+/g, ' ').trim() };
        });
        if (expectFrontmost && !inspected.frontmost) {
          lastState = 'matched element is covered by a visible layer';
        } else if (expectedText && !inspected.text.includes(expectedText)) {
          lastState = 'matched element has not reached the expected visible text';
        } else {
          return candidate;
        }
      }
    } catch (error) {
      lastState = String(error && error.message ? error.message : error);
    }
    await page.waitForTimeout(125);
  }
  fail(`${label} did not become uniquely visible, uncovered and ready within ${timeoutMs}ms (${lastState})`, failureCode, phase);
}

async function visuallyAbsent(page, selector, label, { timeoutMs, failureCode, phase }) {
  const deadline = Date.now() + timeoutMs;
  let lastState = 'not inspected';
  while (Date.now() <= deadline) {
    try {
      const inspected = await page.locator(selector).evaluateAll(nodes => nodes.map(node => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        const opacity = Number(style.opacity || '1');
        const visiblyPainted = rect.width > 1 && rect.height > 1 && style.display !== 'none' && style.visibility !== 'hidden' && opacity > 0.015;
        return { visiblyPainted, width: Math.round(rect.width), height: Math.round(rect.height), opacity };
      }));
      const visible = inspected.filter(item => item.visiblyPainted);
      if (visible.length === 0) return;
      lastState = `${visible.length} visible blocker layer(s): ${visible.map(item => `${item.width}x${item.height} opacity=${item.opacity}`).join(', ')}`;
    } catch (error) {
      lastState = String(error && error.message ? error.message : error);
    }
    await page.waitForTimeout(125);
  }
  fail(`${label} did not become visually absent within ${timeoutMs}ms (${lastState})`, failureCode, phase);
}

async function visibleTextAbsent(page, blockedText, label, { timeoutMs, failureCode, phase }) {
  const needle = blockedText.replace(/\s+/g, ' ').trim();
  const deadline = Date.now() + timeoutMs;
  let lastState = 'not inspected';
  while (Date.now() <= deadline) {
    try {
      const text = await page.evaluate(() => (document.body?.innerText || '').replace(/\s+/g, ' ').trim());
      if (!text.includes(needle)) return;
      lastState = `visible page text still contains ${JSON.stringify(needle)}`;
    } catch (error) {
      lastState = String(error && error.message ? error.message : error);
    }
    await page.waitForTimeout(125);
  }
  fail(`${label} did not disappear from visible page text within ${timeoutMs}ms (${lastState})`, failureCode, phase);
}

async function meaningfulSurface(page) {
  const result = await page.evaluate(() => {
    const body = document.body;
    if (!body) return { text: 0, media: 0, width: 0, height: 0 };
    const text = body.innerText.replace(/\s+/g, ' ').trim().length;
    const media = [...document.querySelectorAll('img, video, canvas, svg')].filter(node => {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return rect.width > 1 && rect.height > 1 && style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity || '1') > 0;
    }).length;
    return { text, media, width: document.documentElement.clientWidth, height: document.documentElement.clientHeight };
  });
  if (result.width < 1 || result.height < 1 || (result.text < 1 && result.media < 1)) fail('page has no visible source surface');
}

async function stateSignal(page, selector) {
  const target = page.locator(selector);
  const count = await target.count();
  const scrollY = await page.evaluate(() => window.scrollY);
  if (count !== 1) return { count, visible: false, text: '', ariaExpanded: null, scrollY };
  const visible = await target.isVisible();
  if (!visible) return { count, visible: false, text: '', ariaExpanded: await target.getAttribute('aria-expanded'), scrollY };
  return {
    count,
    visible: true,
    text: (await target.innerText()).replace(/\s+/g, ' ').trim().slice(0, 2000),
    ariaExpanded: await target.getAttribute('aria-expanded'),
    scrollY,
  };
}

async function captureVariant(browser, request, viewport, outDir, prefix, includeBefore) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  try {
    // Many valid public pages keep analytics or live connections open.  The
    // reviewed visible target, not an unrelated network-idle condition, is
    // the acceptance boundary for this bounded capture.
    const response = await page.goto(request.sourceUrl, { waitUntil: 'domcontentloaded', timeout: 20000 });
    if (!response || response.status() >= 400) fail('source page did not load successfully');
    await meaningfulSurface(page);
    if (request.action.readySelector) {
      await visibleUnique(page, request.action.readySelector, 'source readiness target', {
        timeoutMs: request.timing.readyTimeoutMs,
        expectedText: request.action.readyText,
        failureCode: 'source_ready_timeout',
        phase: 'before_action',
      });
    }
    if (request.action.readyBlockerSelector) {
      await visuallyAbsent(page, request.action.readyBlockerSelector, 'source loading blocker', {
        timeoutMs: request.timing.readyTimeoutMs,
        failureCode: 'source_ready_timeout',
        phase: 'before_action',
      });
    }
    if (request.action.readyBlockerText) {
      await visibleTextAbsent(page, request.action.readyBlockerText, 'source loading text', {
        timeoutMs: request.timing.readyTimeoutMs,
        failureCode: 'source_ready_timeout',
        phase: 'before_action',
      });
    }
    const target = ['click', 'click_then_scroll'].includes(request.action.kind)
      ? await visibleUnique(page, request.action.selector, 'source action target', {
          timeoutMs: request.timing.readyTimeoutMs,
          failureCode: 'source_ready_timeout',
          phase: 'before_action',
        })
      : null;
    const observedViewport = await page.evaluate(() => ({
      width: window.innerWidth,
      height: window.innerHeight,
      device_scale_factor: window.devicePixelRatio,
    }));
    const observedUrl = page.url();
    await page.evaluate(y => window.scrollTo(0, y), request.action.beforeScroll);
    const beforePath = path.join(outDir, `${prefix}-before.png`);
    const beforeCapturedAt = new Date().toISOString();
    if (includeBefore) await page.screenshot({ path: beforePath, fullPage: false });
    const beforeSignal = await stateSignal(page, request.action.expectSelector);
    if (request.action.kind === 'click') await target.click({ timeout: 10000 });
    else if (request.action.kind === 'click_then_scroll') {
      await target.click({ timeout: 10000 });
      await page.waitForTimeout(250);
      await page.evaluate(y => window.scrollTo(0, y), request.action.afterScroll);
    } else if (request.action.kind === 'wheel_scroll') {
      // Use a real wheel event for sites that unlock or animate their scroll
      // story only after natural input.  We still reject the capture unless
      // the browser reads back a changed scroll position below.
      await page.mouse.wheel(0, request.action.afterScroll - request.action.beforeScroll);
      await page.waitForTimeout(500);
    } else await page.evaluate(y => window.scrollTo(0, y), request.action.afterScroll);
    await visibleUnique(page, request.action.expectSelector, 'post-action state target', {
      timeoutMs: request.timing.postActionTimeoutMs,
      expectedText: request.action.expectText,
      failureCode: 'postcondition_timeout',
      phase: 'after_action',
      expectFrontmost: request.action.expectFrontmost,
      allowMultipleWhenOneVisible: true,
    });
    const afterSignal = await stateSignal(page, request.action.expectSelector);
    if (JSON.stringify(beforeSignal) === JSON.stringify(afterSignal)) fail('source action did not produce an observable post-action state change', 'state_change_missing', 'after_action');
    if (request.action.kind !== 'wheel_scroll') await page.evaluate(y => window.scrollTo(0, y), request.action.afterScroll);
    if (['scroll', 'click_then_scroll'].includes(request.action.kind)) {
      const observedScroll = await page.evaluate(() => Math.round(window.scrollY));
      if (Math.abs(observedScroll - request.action.afterScroll) > 8) {
        fail(`scroll action did not reach its declared position (expected ${request.action.afterScroll}, got ${observedScroll})`, 'scroll_position_missing', 'after_action');
      }
    } else if (request.action.kind === 'wheel_scroll') {
      const observedScroll = await page.evaluate(() => Math.round(window.scrollY));
      if (observedScroll < request.action.afterScroll - 8) {
        fail(`wheel action did not reach its declared minimum position (expected at least ${request.action.afterScroll}, got ${observedScroll})`, 'scroll_position_missing', 'after_action');
      }
    }
    await meaningfulSurface(page);
    const afterPath = path.join(outDir, `${prefix}-after.png`);
    const afterCapturedAt = new Date().toISOString();
    await page.screenshot({ path: afterPath, fullPage: false });
    return {
      beforePath: includeBefore ? beforePath : '', afterPath, beforeSignal, afterSignal,
      observedViewport, observedUrl, beforeCapturedAt, afterCapturedAt,
    };
  } finally {
    await page.close();
  }
}

async function captureMobileInitial(browser, request, outDir) {
  const page = await browser.newPage({ viewport: request.mobile, deviceScaleFactor: 1 });
  try {
    const response = await page.goto(request.sourceUrl, { waitUntil: 'domcontentloaded', timeout: 20000 });
    if (!response || response.status() >= 400) fail('source page did not load successfully');
    await meaningfulSurface(page);
    if (request.mobileReadiness.selector) {
      await visibleUnique(page, request.mobileReadiness.selector, 'mobile source readiness target', {
        timeoutMs: request.timing.readyTimeoutMs,
        expectedText: request.mobileReadiness.expectedText,
        failureCode: 'source_ready_timeout',
        phase: 'mobile_initial',
        expectFrontmost: request.mobileReadiness.expectFrontmost,
      });
    }
    if (request.mobileReadiness.blockerSelector) {
      await visuallyAbsent(page, request.mobileReadiness.blockerSelector, 'mobile source loading blocker', {
        timeoutMs: request.timing.readyTimeoutMs,
        failureCode: 'source_ready_timeout',
        phase: 'mobile_initial',
      });
    }
    if (request.mobileReadiness.blockerText) {
      await visibleTextAbsent(page, request.mobileReadiness.blockerText, 'mobile source loading text', {
        timeoutMs: request.timing.readyTimeoutMs,
        failureCode: 'source_ready_timeout',
        phase: 'mobile_initial',
      });
    }
    const observedViewport = await page.evaluate(() => ({
      width: window.innerWidth,
      height: window.innerHeight,
      device_scale_factor: window.devicePixelRatio,
    }));
    const observedUrl = page.url();
    await page.evaluate(y => window.scrollTo(0, y), request.action.beforeScroll);
    const initialPath = path.join(outDir, 'mobile-initial.png');
    const capturedAt = new Date().toISOString();
    await page.screenshot({ path: initialPath, fullPage: false });
    return { path: initialPath, observedViewport, observedUrl, capturedAt };
  } finally {
    await page.close();
  }
}

async function check() {
  const playwright = lockedPlaywrightForCheck();
  let browser;
  try {
    browser = await playwright.runtime.chromium.launch({ headless: true });
    const browserVersion = browser.version();
    console.log(JSON.stringify({
      status: 'dependencies_ready', capture_verified: true, node: process.version, channel: 'workspace-locked-chromium',
      playwright_module: 'workspace_playwright', playwright: playwright.packageVersion,
      browser: 'Playwright Chromium', browser_version: browserVersion, profile: 'ephemeral',
    }));
  } finally {
    if (browser) await browser.close();
  }
}

function failureDetails(error) {
  const message = String(error && error.message ? error.message : error);
  if (/spawn EPERM/i.test(message)) return { code: 'launch_denied', phase: 'launch', message };
  return {
    code: String(error && error.captureCode ? error.captureCode : 'capture_failed'),
    phase: String(error && error.capturePhase ? error.capturePhase : 'unknown'),
    message,
  };
}

function outputFiles(root, current = root) {
  const records = [];
  for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
    const target = path.join(current, entry.name);
    if (entry.isDirectory()) records.push(...outputFiles(root, target));
    else if (entry.isFile()) records.push({ path: path.relative(root, target).split(path.sep).join('/'), sha256: sha256(target) });
  }
  return records;
}

async function main() {
  if (process.argv.includes('--check')) return check();
  const rootRaw = arg('--root', '.');
  const root = path.resolve(rootRaw);
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) fail('root is not a directory');
  const output = inside(root, arg('--output-dir'), 'output directory');
  if (fs.existsSync(output)) fail('output directory must be new');
  const request = requestData(root);
  const playwright = playwrightForCapture();
  const { chromium } = playwright.runtime;
  const target = browserTarget();
  fs.mkdirSync(output, { recursive: true });
  let browser;
  try {
    browser = await chromium.launch(target.launch);
    const desktop = await captureVariant(browser, request, request.desktop, output, 'desktop', true);
    const mobile = request.mobileInteraction
      ? await captureVariant(browser, request, request.mobile, output, 'mobile', false)
      : await captureMobileInitial(browser, request, output);
    const receipt = {
      schema_version: RECEIPT_SCHEMA,
      status: 'captured',
      source_url: request.sourceUrl,
      action_id: request.action.id,
      state_changed: true,
      before: { screenshot_id: 'source-before', state: request.action.beforeState, sha256: sha256(desktop.beforePath) },
      after: { screenshot_id: 'source-after', state: request.action.afterState, sha256: sha256(desktop.afterPath) },
      observed_state_change: { before: desktop.beforeSignal, after: desktop.afterSignal },
      screenshots: [
        { id: 'source-before', role: 'desktop', path: 'desktop-before.png', sha256: sha256(desktop.beforePath), source_url: desktop.observedUrl, captured_at: desktop.beforeCapturedAt, viewport: desktop.observedViewport, actions: ['open'] },
        { id: 'source-after', role: 'interaction', path: 'desktop-after.png', sha256: sha256(desktop.afterPath), source_url: desktop.observedUrl, captured_at: desktop.afterCapturedAt, viewport: desktop.observedViewport, actions: ['open', request.action.id] },
        request.mobileInteraction
          ? { id: 'source-mobile', role: 'mobile', path: 'mobile-after.png', sha256: sha256(mobile.afterPath), source_url: mobile.observedUrl, captured_at: mobile.afterCapturedAt, viewport: mobile.observedViewport, actions: ['open', request.action.id] }
          : { id: 'source-mobile', role: 'mobile', path: 'mobile-initial.png', sha256: sha256(mobile.path), source_url: mobile.observedUrl, captured_at: mobile.capturedAt, viewport: mobile.observedViewport, actions: ['open'] },
      ],
      viewport: { desktop: desktop.observedViewport, mobile: mobile.observedViewport },
      tool: { channel: target.channel, node: process.version, playwright_module: playwright.packageName, playwright: playwright.packageVersion, browser: target.browser, browser_version: await browser.version(), profile: 'ephemeral' },
    };
    const receiptPath = path.join(output, 'source-capture-receipt.json');
    fs.writeFileSync(receiptPath, JSON.stringify(receipt, null, 2) + '\n');
    console.log(JSON.stringify({ status: 'captured', output: path.relative(root, output).split(path.sep).join('/'), receipt: path.relative(root, receiptPath).split(path.sep).join('/'), receipt_sha256: sha256(receiptPath) }));
  } catch (error) {
    const failure = failureDetails(error);
    const failurePath = path.join(output, 'source-capture-failure.json');
    const receipt = {
      schema_version: 'case_source_capture_failure_v1', status: 'failed', source_url: request.sourceUrl,
      action_id: request.action.id, failure, saved_files: outputFiles(output), captured_at: new Date().toISOString(),
    };
    fs.writeFileSync(failurePath, JSON.stringify(receipt, null, 2) + '\n');
    error.captureFailureReceipt = path.relative(root, failurePath).split(path.sep).join('/');
    throw error;
  } finally {
    if (browser) await browser.close();
  }
}

main().catch(error => {
  const failure = failureDetails(error);
  console.error(JSON.stringify({ status: 'failed', failure, failure_receipt: error.captureFailureReceipt || null }));
  process.exit(2);
});

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');

const template = fs.readFileSync(process.argv[2], 'utf8');
const shaders = fs.readFileSync(process.argv[3]);
const baseline = fs.readFileSync(process.argv[4]);
assert.doesNotMatch(template, /NebulaEntrance|entrance-toggle|entrance-skip|startEntrance|advanceEntrance|entranceFrame|skipNextEntrance|entranceWarp|entranceFlare|entranceHalo/);
assert.match(template, /legacyEntranceEnabled/);
assert.match(template, /entrance_enabled:state\.legacyEntranceEnabled/);
assert.match(template, /state\.energy\*=Math\.exp/);
assert.equal(crypto.createHash('sha256').update(shaders).digest('hex'), crypto.createHash('sha256').update(baseline).digest('hex'));
console.log(JSON.stringify({ status: 'passed', checks: ['no entrance overlay, trigger, flight frame, or shader pass remains', 'existing preference schema keeps entrance_enabled inertly compatible', 'normal renderer decay is restored', 'shader exactly matches the pre-entrance baseline'] }));

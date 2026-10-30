// Static smoke checks for the actual Pixel Workbench implementation.
// Run locally: node tests/test_pixel_workbench_ui.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const html = read('pixel_workbench/static/index.html');
const js = read('pixel_workbench/static/app.js');
const css = read('pixel_workbench/static/app.css');
const py = read('pixel_workbench/app.py');

// Syntax check without running the browser application.
new vm.Script(js, {filename: 'pixel_workbench/static/app.js'});

// Every interactive control introduced by the workbench must have a matching DOM element.
for (const id of [
  'purposePicker', 'purposeSummary', 'modeGuide', 'providerGuide',
  'imageWidth', 'imageHeight', 'dimensionsField', 'animationHelp',
  'ui-frame', 'uiTheme', 'uiSourceFile', 'uiFramePreset',
  'uiTargetWidth', 'uiTargetHeight', 'uiCornerSize', 'uiFrameName',
  'uiFrameCanvas', 'uiFrameStatus', 'uiDownload', 'uiRegister', 'uiUseCurrent'
]) {
  assert.ok(html.includes('id="' + id + '"'), 'Missing UI control: ' + id);
}
for (const use of ['data-purpose="window"', 'data-purpose="nine"', 'data-purpose="animate"', 'data-dims="240x64"']) {
  assert.ok(html.includes(use), 'Missing purpose/size preset: ' + use);
}
assert.match(js, /function applyPurpose\(/);
assert.match(js, /function updateAnimationHelp\(/);
assert.match(js, /524288/);
assert.match(js, /function renderUIFrame\(/);
assert.match(js, /function tileUIArea\(/);
assert.match(js, /imageSmoothingEnabled=false/);
assert.match(js, /pixel\/api\/assets\/import/);
assert.match(js, /uiPNGBlob\(/);
assert.match(css, /\.purpose-grid/);
assert.match(css, /\.ui-frame-stage/);

// Check the API routes and shared validation for rectangular UI generation.
assert.match(py, /width: int \| None/);
assert.match(py, /height: int \| None/);
assert.match(py, /'\/generate-ui-v2'/);
assert.match(py, /524288/);
assert.match(py, /category == 'ui'/);
assert.match(py, /'ui':'UI・ウィンドウ枠'/);
console.log('Pixel Workbench static UI checks passed.');

// Static smoke checks for the Pixel Workbench UI additions.
// Run: node tests/test_pixel_workbench_ui.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const html = read('pixel_workbench/static/index.html');
const ux = read('pixel_workbench/static/pixel_ux.js');
const builder = read('pixel_workbench/static/ui_frame_builder.js');
const style = read('pixel_workbench/static/pixel_ux.css');
const py = read('pixel_workbench/app.py');

for (const file of ['pixel_workbench/static/app.js', 'pixel_workbench/static/pixel_ux.js', 'pixel_workbench/static/ui_frame_builder.js']) {
  new vm.Script(read(file), {filename: file});
}
assert.match(html, /pixel_ux\.css/);
assert.match(html, /pixel_ux\.js/);
assert.match(html, /ui_frame_builder\.js/);
assert.match(html, /href="#ui-frame"/);
assert.match(ux, /data-pixel-workflow="animate"/);
assert.match(ux, /524288/);
assert.match(ux, /frame_count/);
assert.match(builder, /id="uiFrameCanvas"/);
assert.match(builder, /id="uiFrameWidth"/);
assert.match(builder, /id="uiFrameHeight"/);
assert.match(builder, /id="uiFrameCorner"/);
assert.match(builder, /drawNine/);
assert.match(builder, /imageSmoothingEnabled=false/);
assert.match(builder, /\/pixel\/api\/assets\/import/);
assert.match(builder, /toBlob/);
assert.match(py, /'ui':'UI・ウィンドウ枠'/);
assert.match(style, /\.pixel-builder-grid/);
console.log('Pixel Workbench static UI checks passed.');

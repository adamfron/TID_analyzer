import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const src = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8');
assert.ok(src.includes('/api/interpolation/build-arc'), 'selected-arc build endpoint is used');
assert.ok(src.includes('This operation will generate ${planned} interpolated maps and may take a long time. Existing completed maps will be reused. Do you wish to continue?'), 'build-all warning is shown');
assert.ok(src.includes('const firstEpoch = String(a.start_time_h.toFixed(5));'), 'arc click uses first raw epoch');
assert.ok(src.includes('Calculating estimate…'), 'ETA waits for enough completed elements');
assert.ok(src.includes('Skipped: low station coverage'), 'low station coverage count is displayed separately');
console.log('interpolation controls source checks passed');

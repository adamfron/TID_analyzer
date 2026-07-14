import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const src = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');

assert.match(css, /grid-template-columns:\s*minmax\(0, 1fr\) clamp\(420px, 30vw, 560px\)/, 'sidebar uses responsive clamp and leaves the map minmax(0, 1fr)');
assert.match(css, /\.workspace\.sidebarCollapsed\s*\{\s*grid-template-columns:\s*minmax\(0, 1fr\) 0;/s, 'collapsed sidebar frees the map column');
assert.match(src, /tid\.sidebar\.hidden/, 'sidebar hidden state persists in localStorage');
assert.match(src, /ctrlKey && event\.shiftKey && event\.key\.toLowerCase\(\) === 'b'/, 'Ctrl+Shift+B shortcut toggles sidebar');
assert.match(css, /overflow-x:\s*hidden/, 'workspace/sidebar prevent horizontal overflow');
assert.match(css, /table-layout:\s*fixed/, 'visibility table uses fixed layout');
assert.match(css, /font-size:\s*10\.5px/, 'compact visibility table font size is around 10-11px');
for (const symbol of ['✓','⚠','○','◌','◐','×']) assert.ok(src.includes(symbol), `status symbol ${symbol} is rendered`);
assert.match(src, /title=\{info\.title\}/, 'status symbols expose title tooltips');
assert.match(src, /aria-label=\{info\.text\}/, 'status symbols expose aria-label text');
assert.match(src, /statusLegend/, 'compact table renders a legend');
assert.match(src, /selectedArcKey/, 'visibility row selection is tracked');
assert.match(css, /tbody tr\.selectedArc/, 'selected visibility row has explicit styling');
assert.match(src, /visibilityEnlarged.*VisibilityTable/s, 'visibility enlarge action opens the complete table modal');
for (const key of ['prn','start','stations','duration','status']) assert.ok(src.includes(`['${key}'`), `modal supports sorting by ${key}`);
assert.match(css, /@media[\s\S]*visibilityTable:not\(\.visibilityTableFull\).*display:\s*none/, 'less important compact columns hide responsively');

console.log('responsive sidebar and visibility table source checks passed');

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');

assert.match(src, /function fourPointStarPath/, 'station markers use a four-point star path helper');
assert.match(src, /className="stationStar" d=\{fourPointStarPath\(sx,sy,display\.stationMarkerSize\/2\)\}/, 'station stars are rendered from the four-point helper');
assert.match(src, /<g className="ippMarkers">[\s\S]*<circle key=\{i\}/, 'IPP markers remain circular');
assert.doesNotMatch(src, /onClick=\{\(\)=>onPointClick\(p\)\}/, 'clicking an IPP does not change selected stations');
assert.match(src, /className="stationHitTarget" onClick=\{\(\)=>onStationClick\(m\.station, prn\)\}/, 'clicking a station toggles selection through the station hit target');
assert.match(src, /\{sel && <circle cx=\{sx\} cy=\{sy\} r=\{display\.stationMarkerSize\+3\} className="stationHalo"\/>\}/, 'orange halo appears only for selected stations');
assert.match(src, /IPP marker size[\s\S]*Station marker size[\s\S]*Station label size[\s\S]*Station–IPP ray width[\s\S]*Station–IPP ray opacity/, 'sliders update layer sizes');
assert.match(src, /window\.localStorage\.setItem\('tid\.map\.display'/, 'map display settings persist in localStorage');
assert.match(src, /<clipPath id=\{clipId\}>[\s\S]*<g className="geographicLayers" clipPath=\{`url\(#\$\{clipId\}\)`\}/, 'all geographic layers use the common clip path');
assert.match(src, /className="stationRays"[\s\S]*className="stationLabel"[\s\S]*className="stationHitTarget"/, 'rays, labels, and hit targets are inside the clipped geographic layer group');
assert.match(css, /stationMarker text[\s\S]*pointer-events:\s*none/, 'station labels do not intercept clicks');

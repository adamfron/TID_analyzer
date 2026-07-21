import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const src = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');

assert.match(src, /type Viewport = \{ lon_min:number; lon_max:number; lat_min:number; lat_max:number \}/, 'viewport state exposes lon_min/lon_max/lat_min/lat_max');
assert.match(src, /const EUROPE_VIEWPORT: Viewport = \{ lon_min: -20, lon_max: 50, lat_min: 20, lat_max: 80 \}/, 'reset extent defaults to Europe');
assert.match(src, /function zoomViewport[\s\S]*lon-fx\*nextLon[\s\S]*lat-fy\*nextLat/, 'zoom is centered on the cursor longitude/latitude');
assert.match(src, /onWheel=\{[\s\S]*e\.preventDefault\(\)[\s\S]*zoom\(e\.deltaY<0\?0\.8:1\.25,g\.lon,g\.lat\)/, 'wheel zoom prevents page scrolling and zooms around cursor');
assert.match(src, /function panViewport[\s\S]*lon_min:v\.lon_min\+dLon[\s\S]*lat_min:v\.lat_min\+dLat/, 'pan updates viewport bounds');
assert.match(src, /onDoubleClick=\{\(\)=>setViewport\?\.\(rasterViewport\(raster\)\)\}/, 'double click resets the active raster extent');
for (const label of ['Zoom in','Zoom out','Reset view']) assert.ok(src.includes(`>${label}</button>`), `${label} button exists`);
assert.match(src, /className="stationHitTarget" onClick=\{\(\)=>onStationClick\(m\.station, prn\)\}/, 'station selection uses station marker coordinates and expanded hit target');
assert.match(src, /<circle cx=\{sx\} cy=\{sy\} r=\{Math\.max\(8, display\.stationMarkerSize\)\} className="stationHitTarget"/, 'station hit target is about 14 px diameter');
assert.match(css, /stationMarker text[\s\S]*pointer-events:\s*none/, 'station labels do not intercept clicks');
assert.match(src, /layers\.grid && <g className="grid internalGrid">/, 'grid toggle only controls internal grid lines');
assert.match(src, /<g className="ticks">/, 'coordinate ticks and labels are always rendered');
assert.match(src, /<rect className="mapFrame"/, 'outer map frame is always rendered');
assert.match(src, /function mapGridValues\(min:number,max:number\)[\s\S]*v\+=10/, 'grid uses applicable 10-degree lines in viewport');
assert.match(src, /plotW=690[\s\S]*cbX=790/, 'plotting rectangle is narrowed with separate right-side colorbar region');
assert.match(src, /<Colorbar x=\{cbX\} y=\{padT\} height=\{plotH\} range=\{display\.dtecRange\}\/>/, 'colorbar height matches plotting rectangle');
assert.match(src, /data-lon-min=\{v\.lon_min\}[\s\S]*<metadata>\{`viewport lon_min=/, 'exportable SVG includes viewport extent metadata');
assert.match(css, /\.plot[\s\S]*width:\s*100%/, 'map scales with available sidebar-expanded/collapsed space');
assert.match(css, /\.workspace\.sidebarCollapsed\s*\{\s*grid-template-columns:\s*minmax\(0, 1fr\) 0;/, 'sidebar collapse gives map the full column');

console.log('map explorer interaction and layout source checks passed');

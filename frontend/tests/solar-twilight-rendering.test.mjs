import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const src = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');

assert.match(src, /const DEFAULT_TWILIGHT_OPACITY = 35/, 'twilight opacity default is 35%');
assert.match(src, /localStorage\.getItem\('tid\.map\.twilightOpacity'\)/, 'twilight opacity loads from localStorage');
assert.match(src, /localStorage\.setItem\('tid\.map\.twilightOpacity', String\(twilightOpacity\)\)/, 'twilight opacity persists to localStorage');
assert.match(src, /Twilight shading opacity <input type="range" min="0" max="100" value=\{p\.twilightOpacity\}/, 'opacity slider is shown under solar illumination controls with 0-100% range');
assert.match(src, /<span>\{p\.twilightOpacity\}%<\/span>/, 'opacity slider displays current percentage');
assert.match(src, /img\.data\[k\+3\]=Math\.round\(255\*band\.alpha\*multiplier\)/, 'opacity slider applies as a global multiplier to twilight bands');

assert.ok(!src.includes('rects.push(<rect'), 'twilight shading no longer renders one SVG rectangle per solar grid cell');
assert.match(src, /document\.createElement\('canvas'\)/, 'twilight shading renders through an off-screen canvas image');
assert.match(src, /solarElevationAt\(lon,lat,solar\)/, 'canvas classification uses unrestricted subsolar-point solar elevation');
assert.match(src, /<image className="twilightImage" href=\{href\} x=\{plotRect\.x\} y=\{plotRect\.y\} width=\{plotRect\.width\} height=\{plotRect\.height\}/, 'twilight image is aligned to the same map viewport rectangle used by other geolayers and export');
assert.match(css, /\.twilightShading \.twilightImage \{ pointer-events: none; image-rendering: auto; \}/, 'twilight image has no cell-border crisp-edge styling');

assert.match(src, /e >= -6\) return layers\.solarCivil \? \{shade:222/, 'Civil checkbox controls its 0 to -6 degree fill band');
assert.match(src, /e >= -12\) return layers\.solarNautical \? \{shade:174/, 'Nautical checkbox controls its -6 to -12 degree fill band');
assert.match(src, /e >= -18\) return layers\.solarAstronomical \? \{shade:128/, 'Astronomical checkbox controls its -12 to -18 degree fill band');
assert.match(src, /return layers\.solarAstronomical \? \{shade:62/, 'Astronomical checkbox also controls the below -18 degree night fill');
assert.match(src, /layers\.solar && layers\.solarShade && solarGeometry && <SolarShading/, 'Shade twilight zones controls fills');
assert.match(src, /layers\.solar && solarGeometry && <SolarContours/, 'Shade twilight zones does not hide enabled contour lines');
assert.match(src, /layers\.solar && layers\.solarShade/, 'master solar toggle hides twilight fills');
assert.match(src, /layers\.solar && solarGeometry && <SolarContours/, 'master solar toggle hides solar contours');

assert.match(src, /className="geographicLayers" clipPath=\{`url\(#\$\{clipId\}\)`\}/, 'twilight fields and contours share the clipped geographic layer');
assert.match(src, /viewport\.lon_min.*viewport\.lon_max.*viewport\.lat_min.*viewport\.lat_max/, 'twilight raster generation uses viewport bounds for zoom/pan alignment');
assert.match(src, /canvas\.toDataURL\('image\/png'\)/, 'twilight canvas is embedded as PNG data for SVG export');
assert.match(src, /exportActiveMap\(\{prn:p\.prn, timeH:p\.actualTimeH \|\| p\.timeH/, 'map PNG export receives active map state layers');

console.log('solar twilight rendering source checks passed');

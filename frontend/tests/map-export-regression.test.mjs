import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8');

assert.match(src, /type ExportResolution = 'screen' \| '1920x1200' \| '2880x1800' \| '3840x2400'/, 'export resolution options are defined');
assert.match(src, /'2880x1800'\):?|useState<ExportResolution>\('2880x1800'\)/, '2880 × 1800 is the default export size');
assert.match(src, /PNG export size <select/, 'Map Explorer exposes a PNG export resolution selector');
assert.match(src, /width=res\.width, height=res\.height/, 'export canvas dimensions come from the requested resolution');
assert.match(src, /ctx\.fillStyle='#fff'; ctx\.fillRect\(0,0,width,height\)/, 'PNG export paints a white background');
assert.match(src, /function exportStyle\(\)[\s\S]*\.borders path\{fill:none;stroke:#9aa6b2/, 'country borders have explicit non-black no-fill styling');
assert.match(src, /class=\"stationStar\" d=\"\$\{fourPointStarPath\(sx,sy,r\/2\)\}\"/, 'station symbols are explicit bounded stars, not circles from CSS defaults');
assert.match(src, /Math\.max\(5,Math\.min\(12,p\.display\.stationMarkerSize\)\)/, 'station marker scaling is clamped for high-resolution exports');
assert.match(src, /await Promise\.all\(\[loadImageForExport\([\s\S]*loadImageForExport\(twilightHref\)\]\)/, 'raster and twilight image data URLs are decoded before rasterizing the SVG');
assert.match(src, /class=\"dtecRaster\"/, 'export-safe renderer embeds the dTEC raster layer');
assert.match(src, /class=\"twilightImage\"/, 'export-safe renderer embeds the twilight shading layer');
assert.match(src, /\{key:'solarHorizon',t:0\}[\s\S]*\{key:'solarCivil',t:-6\}[\s\S]*\{key:'solarNautical',t:-12\}[\s\S]*\{key:'solarAstronomical',t:-18\}[\s\S]*\{key:'solarIono'/, 'all terminator contours are included when enabled');
assert.match(src, /metadata>viewport lon_min=\$\{v\.lon_min\} lon_max=\$\{v\.lon_max\} lat_min=\$\{v\.lat_min\} lat_max=\$\{v\.lat_max\}/, 'current viewport metadata is preserved');
assert.match(src, /<g class=\"colorbar\">/, 'export includes the dTEC colorbar');
assert.match(src, /class=\"mapTitle\">PRN/, 'export includes a PRN/date/epoch title');
assert.match(src, /TID_map_\$\{\(prn \|\| raster\?\.prn \|\| 'ALL'\)[\s\S]*_\$\{day\}_\$\{hh\}\$\{mm\}\$\{ss\}_\$\{step\}deg\.png/, 'export filename follows the TID_map_PRN_date_time_gridstep pattern');

console.log('map export regression source checks passed');

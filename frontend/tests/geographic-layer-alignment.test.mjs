import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const src = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');

assert.match(src, /function geoToScreen\(lon:number, lat:number, viewport:Viewport, plotRect:/, 'one authoritative geoToScreen transform is defined');
assert.match(src, /function screenToGeo\(x:number, y:number, viewport:Viewport, plotRect:/, 'inverse screenToGeo transform is defined');
assert.match(src, /const plotRect=\{x:padL,y:padT,width:plotW,height:plotH\}/, 'plot uses a shared plotRect for all geographic layers');
assert.match(src, /const x=\(lon:number\)=>geoToScreen\(lon,0,v,plotRect\)\.x; const y=\(lat:number\)=>geoToScreen\(0,lat,v,plotRect\)\.y;/, 'points, borders, grid, labels and rays derive from geoToScreen');
assert.match(src, /return screenToGeo\(sx, sy, v, plotRect\)/, 'pointer coordinates use screenToGeo');
assert.match(src, /rasterImagePlacement\(raster, v, plotRect\)/, 'raster placement uses geographic raster extent and active viewport');
assert.match(src, /lon_min: Math\.min\(\.\.\.lons\), lon_max: Math\.max\(\.\.\.lons\), lat_min: Math\.min\(\.\.\.lats\), lat_max: Math\.max\(\.\.\.lats\)/, 'raster extent handles ascending or descending coordinate arrays');
assert.match(src, /x=\{rasterPlacement\.x\} y=\{rasterPlacement\.y\} width=\{rasterPlacement\.width\} height=\{rasterPlacement\.height\}/, 'raster image is not stretched to the full plot rectangle');
assert.doesNotMatch(src, /className="dtecRaster"[\s\S]{0,200}x=\{padL\} y=\{padT\} width=\{plotW\} height=\{plotH\}/, 'dTEC raster no longer ignores its lon_values/lat_values extent');
assert.match(src, /setViewport\(rasterViewport\(raster\)\)/, 'loading a raster resets the default geographic view to the raster extent');
assert.match(src, /setViewport\?\.\(rasterViewport\(raster\)\)/, 'Reset view returns to the full raster extent');
assert.match(src, /data-render-key=\{renderKey\}/, 'twilight shading render key prevents stale viewport images from being reused');
assert.match(src, /utc_datetime.*subsolar_latitude.*subsolar_longitude.*viewport\.lon_min.*viewport\.lon_max.*viewport\.lat_min.*viewport\.lat_max.*solarCivil.*solarNautical.*solarAstronomical.*opacity.*plotRect\.width.*plotRect\.height/s, 'twilight render key includes solar geometry, viewport, enabled bands, opacity, and dimensions');
assert.match(src, /contourSegments\(solar,c\.t,x,y,viewport\)/, 'solar contours are recalculated from geographic coordinates with the shared transform');
assert.match(src, /\{key:'solarHorizon', t:0[\s\S]*\{key:'solarCivil', t:-6[\s\S]*\{key:'solarNautical', t:-12[\s\S]*\{key:'solarAstronomical', t:-18[\s\S]*\{key:'solarIono', t:solar\.ionospheric_shadow\.threshold_deg/, 'all requested terminator contour thresholds are supported');
assert.match(src, /<SolarLegendLine className=\{key\}\/>/, 'solar labels use reusable line swatches rather than unicode squares');
assert.match(css, /\.solarLegendLine \.solarHorizon \{ stroke: #f59e0b; \}/, 'horizon swatch matches contour color');
assert.match(css, /\.solarLegendLine \.solarCivil \{ stroke: #60a5fa; stroke-dasharray: 5 3; \}/, 'civil swatch matches contour dash style');
assert.match(css, /\.solarLegendLine \.solarNautical \{ stroke: #2563eb; stroke-dasharray: 4 4; \}/, 'nautical swatch matches contour dash style');
assert.match(css, /\.solarLegendLine \.solarAstronomical \{ stroke: #1e3a8a; stroke-dasharray: 2 3; \}/, 'astronomical swatch matches contour dash style');
assert.match(css, /\.solarLegendLine \.solarIono \{ stroke: #7c3aed; stroke-dasharray: 7 3; \}/, 'ionospheric swatch matches contour dash style');
assert.match(src, /<g className="geographicLayers" clipPath=\{`url\(#\$\{clipId\}\)`\}>[\s\S]*<SolarShading[\s\S]*className="dtecRaster"[\s\S]*<Borders[\s\S]*className="grid internalGrid"[\s\S]*<SolarContours[\s\S]*className="stationRays"[\s\S]*className="ippMarkers"[\s\S]*className="stationMarkers"/, 'geographic layers share one clip rectangle and keep the requested draw order');

const plotRect = {x: 55, y: 55, width: 690, height: 450};
const rasterExtent = {lon_min: -10, lon_max: 10, lat_min: 40, lat_max: 50};
const corners = {
  northWest: {lon: rasterExtent.lon_min, lat: rasterExtent.lat_max, color: 'red'},
  northEast: {lon: rasterExtent.lon_max, lat: rasterExtent.lat_max, color: 'green'},
  southWest: {lon: rasterExtent.lon_min, lat: rasterExtent.lat_min, color: 'blue'},
  southEast: {lon: rasterExtent.lon_max, lat: rasterExtent.lat_min, color: 'yellow'},
};
function geoToScreenForTest(lon, lat, viewport) {
  return {
    x: plotRect.x + (lon - viewport.lon_min) / (viewport.lon_max - viewport.lon_min) * plotRect.width,
    y: plotRect.y + (viewport.lat_max - lat) / (viewport.lat_max - viewport.lat_min) * plotRect.height,
  };
}
function assertCornerOrientation(viewport, label) {
  const nw = geoToScreenForTest(corners.northWest.lon, corners.northWest.lat, viewport);
  const ne = geoToScreenForTest(corners.northEast.lon, corners.northEast.lat, viewport);
  const sw = geoToScreenForTest(corners.southWest.lon, corners.southWest.lat, viewport);
  const se = geoToScreenForTest(corners.southEast.lon, corners.southEast.lat, viewport);
  assert.ok(nw.x < ne.x && sw.x < se.x, `${label}: western synthetic corners stay left of eastern corners`);
  assert.ok(nw.y < sw.y && ne.y < se.y, `${label}: northern synthetic corners stay above southern corners`);
  assert.deepEqual([corners.northWest.color, corners.northEast.color, corners.southWest.color, corners.southEast.color], ['red', 'green', 'blue', 'yellow'], `${label}: four coloured raster corners keep NW/NE/SW/SE identities`);
}
assertCornerOrientation({lon_min: -10, lon_max: 10, lat_min: 40, lat_max: 50}, 'default');
assertCornerOrientation({lon_min: -5, lon_max: 5, lat_min: 42, lat_max: 48}, 'zoom');
assertCornerOrientation({lon_min: -2, lon_max: 18, lat_min: 38, lat_max: 48}, 'pan');
assertCornerOrientation({lon_min: rasterExtent.lon_min, lon_max: rasterExtent.lon_max, lat_min: rasterExtent.lat_min, lat_max: rasterExtent.lat_max}, 'reset');

console.log('geographic layer alignment source checks passed');

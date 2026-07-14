import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type Status = { stage: string; current: number; total: number; message: string; stage_index?:number; stage_count?:number; stage_name?:string; stage_percent?:number; overall_percent?:number; percent?:number };
type Manifest = { station_count:number; stations?:string[]; gps_prns:string[]; time_range_hours:{min:number|null;max:number|null}; total_rows_seen:number; valid_rows_after_filters:number; malformed_row_count:number; non_gps_row_count:number; low_elevation_row_count:number; out_of_bounds_row_count:number; applied_filters:Record<string,string|number>; };
type Point = { station:string; time_h:number; prn:string; dtec:number; azimuth:number; elevation:number; ipp_lon:number; ipp_lat:number };
type StationMarker = { station:string; lon:number; lat:number; height?:number; full_site_id?:string; city?:string; country?:string; domes?:string; approximate?:boolean; source?:string };
type StationCatalog = { total:number; resolved:number; unresolved:number; stations:StationMarker[] };
type MapLayers = { borders:boolean; grid:boolean; rays:boolean; ipp:boolean; stations:boolean; stationLabels:boolean; dtec:boolean };
type InterpolationSummary = { eligible_arc_count:number; ineligible_arc_count:number; planned_map_count:number; ready_map_count:number; missing_map_count:number; failed_map_count:number; arcs?:{prn:string; arc_index:number; expected:number; ready:number; failed:number; status:string}[] };
type InterpolationProgress = { state:string; current:number; total:number; current_prn?:string|null; current_arc_index?:number|null; current_epoch_index?:number|null; generated:number; already_ready:number; skipped:number; failed:number; message:string };
type RasterGrid = { prn:string; requested_time_h:number; actual_time_h:number; epoch_index:number; lon_values:number[]; lat_values:number[]; values:number[][]; valid_mask:boolean[][]; method:string; projection:string; point_count:number; station_count:number; status:string; message?:string; image_href?:string };
type Arc = { prn:string; arc_index:number; start_time_h:number; end_time_h:number; duration_min:number; row_count:number; station_count:number; epoch_count:number; max_station_count:number; median_station_count:number; eligible_for_interpolation:boolean; ineligibility_reasons:string[]; interpolation_status:string; generated_map_count:number; failed_map_count:number };
type Series = { station:string; prn:string; time_start_h?:number|null; time_end_h?:number|null; points:{time_h:number; dtec:number; elevation:number; ipp_lon:number; ipp_lat:number}[] };
type FftResult = { station:string; prn:string; period_min:number[]; amplitude:number[] };
type MorletResult = { station:string; prn:string; time_h:number[]; period_min:number[]; power:number[][] };
type PanelId = 'summary'|'visibility'|'map'|'timeseries'|'spectral'|'candidates'|'report';
type MapMode = 'epoch'|'satellite'|'window';
type OperationKey = 'visibility'|'preview'|'timeseries'|'spectral'|'import'|'browse';
type Operation = { key: OperationKey; label: string; slow: boolean } | null;
const API = 'http://127.0.0.1:8000';
const bounds = { lonMin: -20, lonMax: 50, latMin: 20, latMax: 80 };
const panels:{id:PanelId; n:number; title:string}[] = [
  {id:'summary', n:1, title:'Data import & summary'}, {id:'visibility', n:2, title:'Satellite visibility'}, {id:'map', n:3, title:'Map explorer'}, {id:'timeseries', n:4, title:'Station time series'}, {id:'spectral', n:5, title:'Spectral analysis'}, {id:'candidates', n:6, title:'TID candidates'}, {id:'report', n:7, title:'Event report'},
];

function App() {
  const [folderPath, setFolderPath] = useState('');
  const [minElevationDeg, setMinElevationDeg] = useState(50);
  const [status, setStatus] = useState<Status>({ stage: 'idle', current: 0, total: 0, message: 'Idle' });
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [stationMarkers, setStationMarkers] = useState<StationMarker[]>([]);
  const [stationCatalog, setStationCatalog] = useState<StationCatalog | null>(null);
  const [rasterAvailable, setRasterAvailable] = useState(false);
  const [activeRaster, setActiveRaster] = useState<RasterGrid | null>(null);
  const [rasterStatus, setRasterStatus] = useState('No interpolated grid loaded.');
  const [rasterOpacity, setRasterOpacity] = useState(70);
  const [interpolationSummary, setInterpolationSummary] = useState<InterpolationSummary | null>(null);
  const [interpolationProgress, setInterpolationProgress] = useState<InterpolationProgress>({state:'idle', current:0, total:0, generated:0, already_ready:0, skipped:0, failed:0, message:'Idle'});
  const [interpolationSlow, setInterpolationSlow] = useState(false);
  const epochRequestSeq = useRef(0);
  const [layers, setLayers] = useState<MapLayers>({borders:true, grid:true, rays:false, ipp:true, stations:true, stationLabels:false, dtec:false});
  const [limitReached, setLimitReached] = useState(false);
  const [previewMeta, setPreviewMeta] = useState('');
  const [prn, setPrn] = useState('');
  const [timeH, setTimeH] = useState('0');
  const [startTimeH, setStartTimeH] = useState('');
  const [endTimeH, setEndTimeH] = useState('');
  const [tol, setTol] = useState(15);
  const [maxPoints, setMaxPoints] = useState(5000);
  const [mapMode, setMapMode] = useState<MapMode>('epoch');
  const [arcs, setArcs] = useState<Arc[]>([]);
  const [world, setWorld] = useState<any>(null);
  const [worldStatus, setWorldStatus] = useState('Waiting for backend…');
  const [worldError, setWorldError] = useState('');
  const [open, setOpen] = useState<Record<PanelId, boolean>>({summary:true, visibility:true, map:true, timeseries:true, spectral:false, candidates:false, report:false});
  const [selected, setSelected] = useState<string[]>([]);
  const [series, setSeries] = useState<Series[]>([]);
  const [selectedLine, setSelectedLine] = useState('');
  const [operation, setOperation] = useState<Operation>(null);
  const [requestedTimeH, setRequestedTimeH] = useState<string>('');
  const [actualTimeH, setActualTimeH] = useState<string>('');
  const [stationCount, setStationCount] = useState(0);
  const [mapError, setMapError] = useState('');
  const [mapEnlarged, setMapEnlarged] = useState(false);
  const [timeSeriesEnlarged, setTimeSeriesEnlarged] = useState(false);
  const [spectralEnlarged, setSpectralEnlarged] = useState(false);
  const [visibilityEnlarged, setVisibilityEnlarged] = useState(false);
  const [selectedArcKey, setSelectedArcKey] = useState('');
  const [sidebarHidden, setSidebarHidden] = useState(() => window.localStorage.getItem('tid.sidebar.hidden') === 'true');
  const [fft, setFft] = useState<FftResult | null>(null);
  const [morlet, setMorlet] = useState<MorletResult | null>(null);
  const [spectralStatus, setSpectralStatus] = useState('');
  const progress = useMemo(() => Math.round(status.overall_percent ?? (status.total > 0 ? (status.current / status.total) * 100 : 0)), [status]);
  const stageProgress = useMemo(() => Math.round(status.stage_percent ?? (status.total > 0 ? (status.current / status.total) * 100 : 0)), [status]);
  const interpolationRunning = interpolationProgress.state === 'running';
  const topStatusText = operation?.key === 'import' || interpolationRunning ? undefined : (worldError || worldStatus || status.message);
  const toggleSidebar = () => setSidebarHidden(hidden => !hidden);
  useEffect(() => { window.localStorage.setItem('tid.sidebar.hidden', String(sidebarHidden)); }, [sidebarHidden]);
  useEffect(() => {
    const onKeyDown = (event:KeyboardEvent) => {
      if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === 'b') { event.preventDefault(); toggleSidebar(); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
  const importStageText = interpolationRunning ? `Interpolating maps ${interpolationProgress.current} / ${interpolationProgress.total}` : operation?.key === 'import' ? `${status.stage_index || ''}${status.stage_count ? `/${status.stage_count}` : ''} ${status.stage_name || status.message}${status.total ? ` · ${status.current} / ${status.total}` : ''}` : (operation ? operation.label : (topStatusText || status.message));

  async function fetchWithRetry(url:string, attempts=6) {
    let lastError = new Error('Request failed');
    for (let i=0; i<attempts; i++) {
      try { const r = await fetch(url); if (r.ok || r.status < 500) return r; lastError = new Error(`${url} returned ${r.status}`); }
      catch (err:any) { lastError = err; }
      await new Promise(resolve => window.setTimeout(resolve, Math.min(2000, 250 * 2 ** i)));
    }
    throw lastError;
  }
  async function loadWorldBorders() {
    setWorldError(''); setWorldStatus('Waiting for backend…');
    try {
      await fetchWithRetry(`${API}/api/health`, 8);
      setWorldStatus('Loading world borders…');
      const r = await fetchWithRetry(`${API}/api/assets/world-borders`, 6);
      if (!r.ok) throw new Error(`World borders failed to load (${r.status})`);
      setWorld(await r.json()); setWorldStatus('Ready');
    } catch (err:any) {
      setWorld(null); setWorldStatus('Ready'); setWorldError(err?.message || 'World borders failed to load.');
    }
  }
  useEffect(() => { loadWorldBorders(); }, []);
  function applyImportStatus(update:Status) {
    setStatus(update);
    if (update.stage === 'stations_resolved') fetchStationCatalog();
    if (update.stage === 'done') { fetch(`${API}/api/manifest`).then((r) => r.ok ? r.json() : null).then((j) => { if (j) setManifest(j); }); fetchStationCatalog(); setOperation(o => o?.key === 'import' ? null : o); }
    if (update.stage === 'error' || update.stage === 'cancelled') { setOperation(o => o?.key === 'import' ? null : o); }
  }
  useEffect(() => {
    let closed = false; let ws:WebSocket|null = null; let reconnect:number|undefined;
    const connect = () => { if (closed || ws) return; ws = new WebSocket('ws://127.0.0.1:8000/ws/import-progress');
      ws.onmessage = (event) => applyImportStatus(JSON.parse(event.data) as Status);
      const retry = () => { ws = null; if (!closed) reconnect = window.setTimeout(connect, 500); };
      ws.onerror = retry; ws.onclose = retry;
    };
    connect();
    return () => { closed = true; if (reconnect) window.clearTimeout(reconnect); ws?.close(); };
  }, []);


  useEffect(() => {
    const ws = new WebSocket('ws://127.0.0.1:8000/ws/interpolation-progress');
    ws.onmessage = (event) => { const update = JSON.parse(event.data) as InterpolationProgress; setInterpolationProgress(update); refreshInterpolationSummary(); if (update.state === 'running') { const t = window.setTimeout(()=>setInterpolationSlow(true), 2000); return () => window.clearTimeout(t); } else { setInterpolationSlow(false); } };
    return () => ws.close();
  }, []);

  async function refreshInterpolationSummary() { if (!manifest) { setInterpolationSummary({eligible_arc_count:0,ineligible_arc_count:0,planned_map_count:0,ready_map_count:0,missing_map_count:0,failed_map_count:0}); return; } const r = await fetch(`${API}/api/interpolation/summary`); if (r.ok) setInterpolationSummary(await r.json()); }
  async function startInterpolation(force=false) { if (force && !window.confirm('Rebuild all interpolated maps? Existing cached grids may be replaced.')) return; const body={retry_failed:force, force_rebuild:force}; const r=await fetch(`${API}/api/interpolation/build-all`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}); if(!r.ok) throw new Error(await r.text()); setInterpolationProgress(p=>({...p,state:'running',message:'Starting interpolation build'})); await refreshInterpolationSummary(); }
  async function cancelInterpolation() { const r=await fetch(`${API}/api/interpolation/cancel`, {method:'POST'}); if(!r.ok) throw new Error(await r.text()); setInterpolationProgress(p=>({...p,state:'cancelled',message:'Interpolation cancellation requested'})); await refreshInterpolationSummary(); }

  async function runOperation(key:OperationKey, label:string, work:()=>Promise<void>, clearOnDone=true) {
    setOperation({key, label, slow:false}); setStatus({stage:key, current:0, total:0, message:label});
    const timer = window.setTimeout(() => setOperation(o => o?.key === key ? {...o, slow:true} : o), 2000);
    try { await work(); } catch (err:any) { setStatus({stage:'error', current:0, total:0, message:err?.message || `${label} failed`}); throw err; }
    finally { window.clearTimeout(timer); if (clearOnDone) setOperation(o => o?.key === key ? null : o); }
  }
  async function browse() { await runOperation('browse', 'Opening folder browser…', async () => { const r = await fetch(`${API}/api/select-folder`, { method:'POST' }); const j = await r.json(); if (r.ok && j.folder_path) { setFolderPath(j.folder_path); setStatus({stage:'done', current:1, total:1, message:'Folder selected.'}); } else throw new Error(j.detail || 'Folder dialog failed'); }); }
  async function fetchStationCatalog() { const r = await fetch(`${API}/api/stations/catalog`); if (!r.ok) return; const j = await r.json(); const resolved = (j.stations || []).filter((s:any)=>s.resolved && s.lon != null && s.lat != null).map((s:any)=>({station:s.station, lon:s.lon, lat:s.lat, height:s.height, full_site_id:s.full_site_id, city:s.city, country:s.country, domes:s.domes, approximate:false, source:s.source})); setStationCatalog(j); setStationMarkers(resolved); setStationCount(j.resolved || resolved.length); }
  async function startImport() { await runOperation('import', 'Starting import…', async () => { setManifest(null); setPoints([]); setStationMarkers([]); setStationCatalog(null); setRasterAvailable(false); setActiveRaster(null); setArcs([]); setRequestedTimeH(''); setActualTimeH(''); setStationCount(0); const response = await fetch(`${API}/api/import?force_rebuild=true`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ folder_path: folderPath, min_elevation_deg: minElevationDeg }) }); if (!response.ok) throw new Error(await response.text()); startImportPolling(); }, false); }

  function startImportPolling() {
    const poll = window.setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/import/status`); if (!r.ok) return;
        const update = await r.json() as Status; applyImportStatus(update);
        if (['done','error','cancelled'].includes(update.stage)) window.clearInterval(poll);
      } catch { /* polling fallback retries until terminal status */ }
    }, 500);
  }
  async function cancelImport() { const response = await fetch(`${API}/api/import/cancel`, { method:'POST' }); if (!response.ok) throw new Error(await response.text()); setStatus(s=>({...s, message:'Cancelling import after current file…'})); }
  async function loadVisibility() { await runOperation('visibility', 'Loading satellite visibility…', async () => { const r = await fetch(`${API}/api/satellites/visibility`); if (!r.ok) throw new Error(await r.text()); const j = await r.json(); const next = j.arcs || []; setArcs(next); await refreshInterpolationSummary(); setStatus({stage:'done', current:1, total:1, message:`Loaded ${next.length} visibility arcs.`}); }); }
  async function loadPreview(overrideTimeH?:string|number, overridePrn?:string, forceEpoch=false) { await runOperation('preview', 'Loading map points…', async () => { setMapError(''); const safeTime = (typeof overrideTimeH === 'string' || typeof overrideTimeH === 'number') ? String(overrideTimeH) : undefined; const safePrn = typeof overridePrn === 'string' ? overridePrn : undefined; const requestedTime = safeTime ?? timeH; const requestedPrn = safePrn ?? prn; const requestedMode = forceEpoch ? 'epoch' : mapMode; const seq = requestedMode === 'epoch' ? ++epochRequestSeq.current : epochRequestSeq.current; setRequestedTimeH(requestedMode === 'epoch' ? requestedTime : ''); if (requestedMode === 'epoch' && requestedPrn && requestedTime !== '') { const q = new URLSearchParams({ prn: requestedPrn, time_h: requestedTime }); const r = await fetch(`${API}/api/map/epoch?${q}`); if (!r.ok) throw new Error(await r.text()); const j = await r.json(); if (requestedMode === 'epoch' && seq !== epochRequestSeq.current) return; const visible = (j.points || []).filter(inBounds); setPoints(visible); setStationMarkers(j.station_markers || []); setRasterAvailable(Boolean(j.raster_available)); setLimitReached(false); setActualTimeH(j.actual_time_h != null ? String(Number(j.actual_time_h).toFixed(5)) : ''); setStationCount((j.stations || []).length || new Set(visible.map((p:Point)=>p.station)).size); if (j.actual_time_h != null) setTimeH(String(Number(j.actual_time_h).toFixed(5))); if (j.actual_time_h != null) await loadEpochRaster(requestedPrn, requestedTime, seq); const msg = `Requested ${Number(requestedTime).toFixed(5)} h; actual ${j.actual_time_h != null ? Number(j.actual_time_h).toFixed(5) : 'n/a'} h; ${visible.length} IPP points; ${((j.stations || []).length || new Set(visible.map((p:Point)=>p.station)).size)} stations`; setPreviewMeta(msg); setStatus({stage:'done', current:1, total:1, message:msg}); return; } const q = new URLSearchParams({ tolerance_seconds:String(tol), max_points:String(maxPoints) }); if (requestedPrn) q.set('prn', requestedPrn); if (requestedMode === 'epoch' && requestedTime !== '') q.set('time_h', requestedTime); if (requestedMode === 'window') { if (startTimeH !== '') q.set('start_time_h', startTimeH); if (endTimeH !== '') q.set('end_time_h', endTimeH); } const r = await fetch(`${API}/api/preview/points?${q}`); if (!r.ok) throw new Error(await r.text()); const j = await r.json(); if (requestedMode === 'epoch' && seq !== epochRequestSeq.current) return; const visible = (j.points || []).filter(inBounds); setPoints(visible); setStationMarkers(j.station_markers || []); setRasterAvailable(Boolean(j.raster_available)); setLimitReached(Boolean(j.limit_reached)); if (requestedMode === 'epoch') await loadEpochRaster(requestedPrn, requestedTime, seq); const actual = j.actual_time_h ?? j.requested_time_h ?? (requestedMode === 'epoch' ? requestedTime : 'n/a'); setActualTimeH(j.actual_time_h != null ? String(Number(j.actual_time_h).toFixed(5)) : ''); setStationCount(new Set(visible.map((p:Point)=>p.station)).size); if (requestedMode === 'epoch' && j.actual_time_h != null) setTimeH(String(Number(j.actual_time_h).toFixed(5))); const msg = `Requested ${requestedMode === 'epoch' ? requestedTime : 'n/a'} h; actual ${actual}; ${visible.length}/${j.total_matching_before_limit} IPP points; ${new Set(visible.map((p:Point)=>p.station)).size} stations`; setPreviewMeta(msg); setStatus({stage:'done', current:1, total:1, message:msg}); }); }

  async function loadEpochRaster(usePrn:string, useTime:string|number, seq:number) {
    if (!usePrn || useTime === '') { if (seq === epochRequestSeq.current) { setActiveRaster(null); setRasterAvailable(false); setRasterStatus('No interpolated grid exists for this epoch.'); } return; }
    const q = new URLSearchParams({prn: usePrn, time_h: String(useTime)});
    const r = await fetch(`${API}/api/interpolation/epoch?${q}`);
    if (seq !== epochRequestSeq.current) return;
    if (r.status === 404) { setActiveRaster(null); setRasterAvailable(false); setLayers(x=>({...x,dtec:false})); setRasterStatus('No interpolated grid exists for this epoch.'); return; }
    if (!r.ok) { setActiveRaster(null); setRasterAvailable(false); setRasterStatus('No interpolated grid exists for this epoch.'); return; }
    const j = await r.json() as RasterGrid;
    if (j.status !== 'ready') { setActiveRaster(null); setRasterAvailable(false); setLayers(x=>({...x,dtec:false})); setRasterStatus(`Interpolation failed for this epoch: ${j.message || j.status}`); return; }
    const raster = {...j, image_href: rasterToImage(j)};
    setActiveRaster(raster); setRasterAvailable(true); setRasterStatus(`Natural-neighbour grid ready: ${j.prn}, ${Number(j.actual_time_h).toFixed(5)} h, ${j.station_count} stations`); setLayers(x=>({...x,dtec:true}));
  }

  function chooseArc(a:Arc) { setSelectedArcKey(`${a.prn}-${a.arc_index}`); const firstEpoch = String(a.start_time_h.toFixed(5)); setPrn(a.prn); setTimeH(firstEpoch); setStartTimeH(String(a.start_time_h)); setEndTimeH(String(a.end_time_h)); setMapMode('epoch'); setOpen(o=>({...o,map:true})); setTimeout(() => loadPreview(firstEpoch, a.prn, true), 0); }
  function step(minutes:number) { setTimeH(v => { const next = String(Math.max(0, (Number(v || 0) + minutes / 60)).toFixed(5)); if (prn && mapMode === 'epoch') setTimeout(() => loadPreview(next), 0); return next; }); }
  function inBounds(p:Point) { return p.ipp_lon >= bounds.lonMin && p.ipp_lon <= bounds.lonMax && p.ipp_lat >= bounds.latMin && p.ipp_lat <= bounds.latMax; }
  function toggleStationId(station:string, usePrn?:string) { const id = `${station}|${usePrn || prn}`; if (!id.endsWith('|')) setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id]); }
  function toggleStation(p:Point) { if (!inBounds(p)) return; const id = `${p.station}|${p.prn}`; setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id]); }
  async function loadSeries() { await runOperation('timeseries', 'Loading time series…', async () => { const stations = selected.map(s=>s.split('|')[0]); const usePrn = prn || selected[0]?.split('|')[1]; if (!usePrn || !stations.length) return; const q = new URLSearchParams({ prn: usePrn, arc_mode:'continuous_arc' }); stations.forEach(s=>q.append('station', s)); if (startTimeH) q.set('start_time_h', startTimeH); if (endTimeH) q.set('end_time_h', endTimeH); const r = await fetch(`${API}/api/stations/timeseries?${q}`); if (!r.ok) throw new Error(await r.text()); const j = await r.json(); const next = j.series || []; setSeries(next); setStatus({stage:'done', current:1, total:1, message:`Loaded ${next.length} time series.`}); }); }
  async function computeSpectral(kind:'fft'|'morlet') { if (!selectedLine) return; const [station, usePrn] = selectedLine.split('|'); await runOperation('spectral', `Computing ${kind.toUpperCase()}…`, async () => { setSpectralStatus(`Computing ${kind.toUpperCase()}…`); const body:any = {station, prn: usePrn}; if (startTimeH) body.start_time_h = Number(startTimeH); if (endTimeH) body.end_time_h = Number(endTimeH); const r = await fetch(`${API}/api/spectral/${kind}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}); if (!r.ok) throw new Error(await r.text()); const j = await r.json(); if (kind === 'fft') setFft(j); else setMorlet(j); setSpectralStatus(`${kind.toUpperCase()} ready.`); setStatus({stage:'done', current:1, total:1, message:`${kind.toUpperCase()} ready.`}); }); }

  return <div className="app">
    <header className="topbar"><span className="brand">TID Analyzer</span><input className="folderInput" value={folderPath} onChange={e=>setFolderPath(e.target.value)} placeholder="Local daily folder path" /><span className="browseSlot"><button onClick={browse} disabled={operation?.key==='browse'} title="Choose a local daily data folder.">Browse…</button></span><label className="compactInput" title="Only observations at or above this elevation are stored in the daily working database.">Min elevation [deg]<input type="number" min="0" max="90" step="1" value={minElevationDeg} onChange={e=>setMinElevationDeg(Math.max(0, Math.min(90, Number(e.target.value))))}/></label><span className="importSlot">{operation?.key==='import' ? <button onClick={cancelImport} title="Stop import after the current file or batch.">Cancel</button> : <button onClick={startImport} disabled={Boolean(operation)} title="Import and filter the selected folder.">Import</button>}</span><div className="progressStack"><progress {...(operation && status.total === 0 ? {} : {value: progress})} max="100" />{operation?.key==='import' && <progress className="stageProgress" value={stageProgress} max="100" />}</div><span className="statusText" title={importStageText}>{importStageText}</span><span className="spinnerSlot">{(operation || interpolationRunning) && <span className="spinner" aria-label="Loading"/>}</span>{(operation?.slow || interpolationSlow) && <span className="mini">Natural-neighbour grids are being generated and stored in the daily cache. This first pass may take time.</span>}{manifest && <span className="mini">Stations {manifest.station_count} · PRNs {manifest.gps_prns.length} · Valid {manifest.valid_rows_after_filters}</span>}</header>
    <main className={`workspace ${sidebarHidden ? 'sidebarCollapsed' : ''}`}><section className="canvas"><div className="canvasHead"><h1>Map Explorer</h1><span>{previewMeta}{limitReached ? ' · sampled limit reached' : ''}</span></div><PreviewPlot raster={activeRaster} rasterOpacity={rasterOpacity} points={points} stationMarkers={stationMarkers} world={world} layers={layers} selected={selected} onPointClick={toggleStation} onStationClick={toggleStationId} prn={prn} actualTimeH={actualTimeH}/>{worldError && <p className="warn">{worldError} <button onClick={loadWorldBorders}>Retry world borders</button></p>}{mapError && <p className="warn">{mapError}</p>}</section><button className="sidebarToggle" onClick={toggleSidebar} title="Toggle workflow panel (Ctrl+Shift+B)" aria-expanded={!sidebarHidden} aria-controls="workflow-sidebar">{sidebarHidden ? 'Show workflow panel' : 'Hide workflow panel'}</button><aside id="workflow-sidebar" className="side" aria-hidden={sidebarHidden}> {panels.map(p=><WorkflowPanel key={p.id} panel={p} open={open[p.id]} toggle={()=>setOpen(o=>({...o,[p.id]:!o[p.id]}))} onEnlarge={()=> p.id==='visibility' ? setVisibilityEnlarged(true) : p.id==='map' ? setMapEnlarged(true) : p.id==='timeseries' ? setTimeSeriesEnlarged(true) : p.id==='spectral' ? setSpectralEnlarged(true) : undefined}>{p.id==='summary' && <Summary manifest={manifest}/>} {p.id==='visibility' && <Visibility arcs={arcs} load={loadVisibility} choose={chooseArc} loading={operation?.key==='visibility'} summary={interpolationSummary} progress={interpolationProgress} startInterpolation={startInterpolation} cancelInterpolation={cancelInterpolation} selectedArcKey={selectedArcKey}/>} {p.id==='map' && <Explorer stationCatalog={stationCatalog} manifest={manifest} prn={prn} setPrn={setPrn} timeH={timeH} setTimeH={setTimeH} startTimeH={startTimeH} setStartTimeH={setStartTimeH} endTimeH={endTimeH} setEndTimeH={setEndTimeH} tol={tol} setTol={setTol} maxPoints={maxPoints} setMaxPoints={setMaxPoints} loadPreview={loadPreview} count={points.length} limitReached={limitReached} mapMode={mapMode} setMapMode={setMapMode} step={step} loading={operation?.key==='preview'} layers={layers} setLayers={setLayers} rasterAvailable={rasterAvailable} rasterStatus={rasterStatus} rasterOpacity={rasterOpacity} setRasterOpacity={setRasterOpacity} requestedTimeH={requestedTimeH} actualTimeH={actualTimeH} stationCount={stationCount} exportReady={points.length>0} onExportError={setMapError} onEnlarge={()=>setMapEnlarged(true)}/>} {p.id==='timeseries' && <TimeSeries selected={selected} setSelected={setSelected} load={loadSeries} loading={operation?.key==='timeseries'} series={series} selectedLine={selectedLine} setSelectedLine={setSelectedLine} onEnlarge={()=>setTimeSeriesEnlarged(true)}/>} {p.id==='spectral' && <Spectral selectedLine={selectedLine} fft={fft} morlet={morlet} compute={computeSpectral} loading={operation?.key==='spectral'} status={spectralStatus} onEnlarge={()=>setSpectralEnlarged(true)}/>} {p.id==='candidates' && <Placeholder text="TID candidate detection placeholder."/>} {p.id==='report' && <Placeholder text="Event report and export placeholder."/>}</WorkflowPanel>)}</aside></main>
    {visibilityEnlarged && <PlotModal title="Satellite visibility" onClose={()=>setVisibilityEnlarged(false)}><VisibilityTable arcs={arcs} choose={chooseArc} selectedArcKey={selectedArcKey} enlarged/></PlotModal>}
    {mapEnlarged && <div className="modalBackdrop"><div className="mapModal"><button className="close" onClick={()=>setMapEnlarged(false)} title="Close enlarged map view.">Close ×</button><PreviewPlot raster={activeRaster} rasterOpacity={rasterOpacity} points={points} stationMarkers={stationMarkers} world={world} layers={layers} selected={selected} onPointClick={toggleStation} onStationClick={toggleStationId} prn={prn} actualTimeH={actualTimeH} enlarged/></div></div>}
    {timeSeriesEnlarged && <PlotModal title="Station time series" onClose={()=>setTimeSeriesEnlarged(false)}><button onClick={()=>exportSvgPng('.modalPlot .series','TID_timeseries.png')}>Export PNG</button><SeriesPlot series={series} selectedLine={selectedLine} setSelectedLine={setSelectedLine} large/></PlotModal>}
    {spectralEnlarged && <PlotModal title="Spectral analysis" onClose={()=>setSpectralEnlarged(false)}><Spectral selectedLine={selectedLine} fft={fft} morlet={morlet} compute={computeSpectral} loading={operation?.key==='spectral'} status={spectralStatus} enlarged/></PlotModal>}
    <footer>References · dTEC is vertical dTEC · IPP height 450 km · GPS PRN prefix G · min elevation 50°</footer>
  </div>;
}
function WorkflowPanel({panel,open,toggle,onEnlarge,children}:any) { const can = ['visibility','map','timeseries','spectral'].includes(panel.id); return <section className="workflow"><div className="workflowTitle"><button onClick={toggle}>{open?'▾':'▸'} {panel.n}. {panel.title}</button><button onClick={onEnlarge} disabled={!can} title={can ? 'Open this panel in a larger overlay.' : 'No enlargement for this panel.'}>Enlarge</button></div>{open && <div className="workflowBody">{children}</div>}</section>; }
function Summary({manifest}:{manifest:Manifest|null}) { if(!manifest) return <p className="placeholder">Import a folder to view metadata.</p>; return <div className="summary"><p><b>Stations:</b> {manifest.station_count}</p><p><b>GPS PRNs:</b> {manifest.gps_prns.join(', ') || 'None'}</p><p><b>Time range:</b> {manifest.time_range_hours.min}–{manifest.time_range_hours.max} h</p><p><b>Rows:</b> {manifest.valid_rows_after_filters} valid / {manifest.total_rows_seen} seen</p>{(manifest as any).preflight && <pre>{JSON.stringify((manifest as any).preflight, null, 2)}</pre>}<p className="mini">Filters: GPS, elevation ≥{manifest.applied_filters?.min_elevation_deg ?? 50}°, lon -20..50, lat 20..80.</p></div>; }
function visibilityStatus(a:Arc) {
  if(!a.eligible_for_interpolation) return {text:'Ineligible', symbol:'⚠', cls:'statusWarn', title:`Ineligible: ${(a.ineligibility_reasons || []).join('; ') || 'quality thresholds not met'}`};
  const s=(a.interpolation_status||'not_generated').replace('_',' ');
  if(s==='building') return {text:'Building', symbol:'◌', cls:'statusBuild', title:'Building interpolated maps'};
  if(s==='ready') return {text:'Ready', symbol:'✓', cls:'statusOk', title:'Ready'};
  if(s==='partial') return {text:'Partial', symbol:'◐', cls:'statusPartial', title:'Partial map coverage'};
  if(s==='failed') return {text:'Failed', symbol:'×', cls:'statusFail', title:'Failed'};
  return {text:'Not generated', symbol:'○', cls:'statusEmpty', title:'Not generated'};
}
function StatusSymbol({info}:{info:{text:string; symbol:string; cls:string; title:string}}) { return <span className={`statusSymbol ${info.cls}`} title={info.title} aria-label={info.text}>{info.symbol}</span>; }
function VisibilityTable({arcs,choose,selectedArcKey,enlarged=false}:any) {
  const [sort,setSort]=useState('prn');
  const sorted=[...arcs].sort((a:Arc,b:Arc)=> sort==='start'?a.start_time_h-b.start_time_h:sort==='stations'?b.station_count-a.station_count:sort==='duration'?b.duration_min-a.duration_min:sort==='status'?visibilityStatus(a).text.localeCompare(visibilityStatus(b).text):`${a.prn}-${a.arc_index}`.localeCompare(`${b.prn}-${b.arc_index}`));
  const heads=enlarged?['PRN','Arc','Start time','End time','Duration min','Rows','Stations','Epochs','Generated maps','Status','Reasons']:['PRN','Arc','Start','End','Min','Sta','Ep','Maps','State'];
  return <div className={enlarged?'visibilityModalTable':'visibilityTableWrap'}>{enlarged && <div className="buttonRow sortControls"><span>Sort by</span>{[['prn','PRN'],['start','start'],['stations','stations'],['duration','duration'],['status','status']].map(([k,l])=><button key={k} onClick={()=>setSort(k)} aria-pressed={sort===k}>{l}</button>)}</div>}<table className={enlarged?'visibilityTable visibilityTableFull':'visibilityTable'}><thead><tr>{heads.map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{sorted.map((a:Arc)=>{ const info=visibilityStatus(a); const reasons=(a.ineligibility_reasons || []).join('; '); const key=`${a.prn}-${a.arc_index}`; return <tr key={key} className={`${a.eligible_for_interpolation ? '' : 'ineligibleArc'} ${selectedArcKey===key?'selectedArc':''}`} title={reasons || 'Eligible for interpolation'} onClick={()=>choose(a)} tabIndex={0} onKeyDown={(e)=>{ if(e.key==='Enter') choose(a); }}><td>{a.prn}</td><td>{a.arc_index}</td><td>{a.start_time_h.toFixed(enlarged?5:2)}</td><td>{a.end_time_h.toFixed(enlarged?5:2)}</td><td>{a.duration_min.toFixed(1)}</td>{enlarged && <td>{a.row_count}</td>}<td>{a.station_count}</td><td>{a.epoch_count}</td><td>{a.generated_map_count || 0} / {a.epoch_count}</td><td className={info.cls}>{enlarged ? info.text : <StatusSymbol info={info}/>}</td>{enlarged && <td className="reasonCell">{reasons || 'Eligible for interpolation'}</td>}</tr>; })}</tbody></table>{!enlarged && <p className="statusLegend"><span><StatusSymbol info={{text:'Eligible/Ready',symbol:'✓',cls:'statusOk',title:'Eligible or ready'}}/> Eligible/Ready</span><span><StatusSymbol info={{text:'Ineligible',symbol:'⚠',cls:'statusWarn',title:'Ineligible'}}/> Ineligible</span><span><StatusSymbol info={{text:'Not generated',symbol:'○',cls:'statusEmpty',title:'Not generated'}}/> Not generated</span><span><StatusSymbol info={{text:'Building',symbol:'◌',cls:'statusBuild',title:'Building'}}/> Building</span><span><StatusSymbol info={{text:'Partial',symbol:'◐',cls:'statusPartial',title:'Partial'}}/> Partial</span><span><StatusSymbol info={{text:'Failed',symbol:'×',cls:'statusFail',title:'Failed'}}/> Failed</span></p>}</div>;
}
function Visibility({arcs,load,choose,loading,summary,progress,startInterpolation,cancelInterpolation,selectedArcKey}:any) {
  const eligible = arcs.filter((a:Arc)=>a.eligible_for_interpolation);
  const plannedMaps = summary?.planned_map_count ?? eligible.reduce((total:number,a:Arc)=>total + (a.epoch_count || 0), 0);
  const running = progress?.state === 'running';
  return <div><button onClick={load} disabled={loading} title="Load per-PRN visibility arcs from imported observations.">{loading ? 'Loading visibility…' : 'Load visibility'}</button><div className="buttonRow">{running ? <button onClick={cancelInterpolation}>Cancel interpolation</button> : <button onClick={()=>startInterpolation(false)}>Interpolate all eligible maps now</button>}</div><details><summary>Advanced</summary><button onClick={()=>startInterpolation(true)}>Rebuild interpolated maps</button></details>{running && <p className="mini">{progress.current_prn || 'PRN'} · arc {progress.current_arc_index ?? '—'} · epoch {progress.current_epoch_index ?? '—'}<br/>Generated {progress.generated} · skipped {progress.already_ready + progress.skipped} · failed {progress.failed}</p>}<p className="mini">{loading ? 'Loading visibility…' : `${arcs.length} visibility arcs loaded.`}</p><VisibilityTable arcs={arcs} choose={choose} selectedArcKey={selectedArcKey}/><div className="interpSummary"><p>Eligible arcs: {summary?.eligible_arc_count ?? eligible.length} / {(summary?.eligible_arc_count ?? eligible.length) + (summary?.ineligible_arc_count ?? (arcs.length-eligible.length))}</p><p>Maps planned: {plannedMaps}</p><p>Maps generated: {summary?.ready_map_count ?? 0}</p><p>Missing maps: {summary?.missing_map_count ?? Math.max(0, plannedMaps)}</p><p>Failed maps: {summary?.failed_map_count ?? 0}</p></div></div>;
}
function Explorer(p:any) {
  const loadLabel = p.mapMode === 'satellite' ? 'Load satellite track preview' : 'Load epoch map';
  return <div className="summary">
    <label>Mode <select title="Choose whether to draw one epoch, one satellite sample, or a time window." value={p.mapMode} onChange={(e:any)=>p.setMapMode(e.target.value)}><option value="epoch">Current epoch</option><option value="satellite">Whole selected satellite</option><option value="window">Selected time window</option></select></label>
    <label>PRN <select title="Filter map points to a GPS PRN. Selecting a PRN enables automatic current-epoch navigation." value={p.prn} onChange={(e:any)=>p.setPrn(e.target.value)}><option value="">All</option>{p.manifest?.gps_prns.map((x:string)=><option key={x}>{x}</option>)}</select></label>
    {p.mapMode==='epoch' && <><label>Time (h) <input title="Requested epoch in hours from start of day; navigation loads the nearest epoch automatically when a PRN is selected." value={p.timeH} onChange={(e:any)=>p.setTimeH(e.target.value)}/></label><div className="buttonRow">{[-15,-5,-0.5,0.5,5,15].map(m=><button key={m} onClick={()=>p.step(m)} title="Move requested time and automatically load nearest epoch when a PRN is selected.">{m>0?'+':''}{m===-0.5?'- prev epoch':m===0.5?'next epoch':`${m} min`}</button>)}</div></>}
    {p.mapMode==='window' && <><label>Start h <input title="Start time in hours from start of day." value={p.startTimeH} onChange={(e:any)=>p.setStartTimeH(e.target.value)}/></label><label>End h <input title="End time in hours from start of day." value={p.endTimeH} onChange={(e:any)=>p.setEndTimeH(e.target.value)}/></label></>}
    <fieldset className="layers"><legend>Layers</legend>{[['borders','Show country borders','Thin grey country borders below science layers.'],['grid','Show grid','Show longitude/latitude guide lines.'],['ipp','Show IPP points','Show ionospheric pierce points colored by dTEC; clicking selects the station for the current PRN.'],['rays','Show station–IPP rays','Draw fixed station to current IPP lines for the selected epoch.'],['stations','Show station markers','Show subtle station markers; click markers to toggle STATION|PRN selection.'],['stationLabels','Show station labels','Show compact station names near markers; turn off when dense.']].map(([key,label,title])=><label key={key} title={title}><input type="checkbox" checked={p.layers[key]} onChange={(e:any)=>p.setLayers((x:MapLayers)=>({...x,[key]:e.target.checked}))}/>{label}</label>)}<label title={p.rasterAvailable ? 'Show interpolated dTEC raster.' : 'No ready interpolated grid for this epoch.'}><input type="checkbox" checked={p.layers.dtec && p.rasterAvailable} disabled={!p.rasterAvailable} onChange={(e:any)=>p.setLayers((x:MapLayers)=>({...x,dtec:e.target.checked}))}/>Show interpolated dTEC map</label><label>Interpolated map opacity <input type="range" min="0" max="100" value={p.rasterOpacity} onChange={(e:any)=>p.setRasterOpacity(Number(e.target.value))}/>{p.rasterOpacity}%</label><p className="mini">{p.rasterStatus}</p></fieldset>
    <details title="Advanced map sampling settings; ordinary current-epoch navigation does not require these."><summary>Advanced</summary>{p.mapMode==='epoch' && <label>Tolerance seconds <input type="number" value={p.tol} onChange={(e:any)=>p.setTol(Number(e.target.value))}/></label>}<label>Max points <input type="number" value={p.maxPoints} onChange={(e:any)=>p.setMaxPoints(Number(e.target.value))}/></label></details>
    <button onClick={()=>p.loadPreview()} disabled={p.loading} title="Fallback/manual refresh for the map.">{p.loading ? 'Loading map points…' : loadLabel}</button>
    <div className="buttonRow"><button onClick={()=>exportActiveMap(p.prn, p.actualTimeH || p.timeH, p.onExportError)} disabled={!p.exportReady} title="Export the active main SVG map view to a PNG file.">Export PNG</button><button disabled title="Export GIF will be added in a later animation stage.">Export GIF</button><button onClick={p.onEnlarge} title="Open a larger Map Explorer view while preserving PRN, time, and layer state.">Enlarge</button></div>
    <div className="mapStats"><p><b>Requested time:</b> {p.requestedTimeH || 'n/a'}</p><p><b>Actual epoch time:</b> {p.actualTimeH || 'n/a'}</p><p><b>IPP points:</b> {p.count}</p><p><b>Stations:</b> {p.stationCatalog ? `${p.stationCatalog.resolved} / ${p.stationCatalog.total} resolved` : p.stationCount}</p></div>
    {p.mapMode==='satellite' && <p className="mini">Whole satellite mode displays a deterministic sample, not all points.</p>}{p.limitReached && <p className="mini">Deterministic sample limit reached.</p>}
  </div>;
}
function TimeSeries({selected,setSelected,load,loading,series,selectedLine,setSelectedLine,onEnlarge}:any) { return <div><p className="selectedChips">{selected.length ? selected.map((s:string)=><button key={s} onClick={()=>setSelected((x:string[])=>x.filter(y=>y!==s))}>{s} ×</button>) : <span className="placeholder">Click map points to select station/PRN.</span>}</p><button onClick={load} disabled={!selected.length || loading} title="Load dTEC time series for selected stations.">{loading ? 'Loading time series…' : 'Load time series'}</button><button onClick={()=>setSelected([])}>Clear</button><button onClick={()=>exportSvgPng('.workflowBody .series','TID_timeseries.png')} disabled={!series.length}>Export PNG</button><button onClick={onEnlarge} disabled={!series.length}>Enlarge</button><SeriesPlot series={series} selectedLine={selectedLine} setSelectedLine={setSelectedLine}/></div>; }
function Spectral({selectedLine,fft,morlet,compute,loading,status,enlarged}:any) { if(!selectedLine) return <p className="placeholder">Select a station time-series line.</p>; return <div><p><b>Selected:</b> {selectedLine}</p><div className="buttonRow"><button onClick={()=>compute('fft')} disabled={loading}>Compute FFT</button><button onClick={()=>compute('morlet')} disabled={loading}>Compute Morlet</button><button onClick={()=>exportSvgPng(`${enlarged?'.modalPlot ':''}.fftPlot`,'TID_fft.png')} disabled={!fft}>Export FFT PNG</button><button onClick={()=>exportSvgPng(`${enlarged?'.modalPlot ':''}.morletPlot`,'TID_morlet.png')} disabled={!morlet}>Export Morlet PNG</button></div><p className="mini">{loading ? 'Working…' : status}</p>{fft && <FftPlot data={fft}/>} {morlet && <MorletPlot data={morlet}/>}</div>; }
function Placeholder({text}:{text:string}) { return <p className="placeholder">{text}<br/><button disabled>Compute FFT</button> <button disabled>Compute Morlet</button></p>; }
function dtecColor(v:number) {
  const clamped = Math.max(-1, Math.min(1, Number.isFinite(v) ? v : 0));
  if (clamped < 0) { const g = Math.round(255 + clamped * 155); return `rgb(${g},${g},255)`; }
  const g = Math.round(255 - clamped * 155); return `rgb(255,${g},${g})`;
}

function rasterToImage(r:RasterGrid) {
  const lonN=r.lon_values.length, latN=r.lat_values.length; const canvas=document.createElement('canvas'); canvas.width=lonN; canvas.height=latN; const ctx=canvas.getContext('2d'); if(!ctx) return ''; const img=ctx.createImageData(lonN, latN);
  for(let y=0;y<latN;y++){ const srcY = r.lat_values[0] > r.lat_values[latN-1] ? y : latN-1-y; for(let x=0;x<lonN;x++){ const v=Number(r.values[srcY]?.[x]); const ok=Boolean(r.valid_mask[srcY]?.[x]) && Number.isFinite(v); const i=(y*lonN+x)*4; if(!ok){ img.data[i+3]=0; continue; } const c=dtecRgb(v); img.data[i]=c[0]; img.data[i+1]=c[1]; img.data[i+2]=c[2]; img.data[i+3]=255; }}
  ctx.putImageData(img,0,0); return canvas.toDataURL('image/png');
}
function dtecRgb(v:number):[number,number,number] { const clamped=Math.max(-1,Math.min(1,Number.isFinite(v)?v:0)); if(clamped<0){ const g=Math.round(255+clamped*155); return [g,g,255]; } const g=Math.round(255-clamped*155); return [255,g,g]; }

function exportSvgPng(selector:string, filename:string) { const svg=document.querySelector(selector) as SVGSVGElement|null; if(!svg) return; const clone=svg.cloneNode(true) as SVGSVGElement; clone.setAttribute('xmlns','http://www.w3.org/2000/svg'); const box=svg.viewBox.baseVal; const width=box?.width || 900, height=box?.height || 520; const blob=new Blob([new XMLSerializer().serializeToString(clone)],{type:'image/svg+xml;charset=utf-8'}); const url=URL.createObjectURL(blob); const img=new Image(); img.onload=()=>{const canvas=document.createElement('canvas'); canvas.width=width; canvas.height=height; const ctx=canvas.getContext('2d'); if(!ctx) return; ctx.fillStyle='#fff'; ctx.fillRect(0,0,width,height); ctx.drawImage(img,0,0,width,height); URL.revokeObjectURL(url); const a=document.createElement('a'); a.download=filename; a.href=canvas.toDataURL('image/png'); a.click();}; img.src=url; }
function PlotModal({title,onClose,children}:any){ return <div className="modalBackdrop"><div className="mapModal modalPlot"><button className="close" onClick={onClose}>Close ×</button><h2>{title}</h2>{children}</div></div>; }
function exportActiveMap(prn:string, timeH:string, onError:(message:string)=>void) {
  const svg = document.querySelector('.canvas .plot') as SVGSVGElement | null;
  if (!svg) { onError('Export PNG failed: no active map is loaded.'); return; }
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  const caption = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  caption.setAttribute('x', '55'); caption.setAttribute('y', '28'); caption.setAttribute('class', 'exportCaption');
  caption.textContent = `TID map ${prn || 'All PRNs'} ${timeH ? `${timeH} h` : ''}`;
  clone.prepend(caption);
  const xml = new XMLSerializer().serializeToString(clone);
  const blob = new Blob([xml], {type:'image/svg+xml;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const img = new Image();
  img.onload = () => { const canvas = document.createElement('canvas'); canvas.width = 820; canvas.height = 560; const ctx = canvas.getContext('2d'); if (!ctx) { onError('Export PNG failed: canvas is unavailable.'); return; } ctx.fillStyle = '#fff'; ctx.fillRect(0,0,canvas.width,canvas.height); ctx.drawImage(img,0,0); URL.revokeObjectURL(url); const a = document.createElement('a'); const safeTime = (timeH || 'unknown').replace(/[^0-9.]+/g, '_'); a.download = `TID_map_${prn || 'ALL'}_${safeTime}h.png`; a.href = canvas.toDataURL('image/png'); a.click(); onError(''); };
  img.onerror = () => { URL.revokeObjectURL(url); onError('Export PNG failed: the current SVG map could not be rasterized.'); };
  img.src = url;
}
function PreviewPlot({raster,rasterOpacity,points,stationMarkers,world,layers,selected,onPointClick,onStationClick,prn,actualTimeH,enlarged}:any) { const [tip,setTip]=useState(''); const stationByCode = new Map<string, StationMarker>(stationMarkers.map((m:StationMarker)=>[m.station, m])); const visiblePoints = points.filter((p:Point)=>p.ipp_lon >= bounds.lonMin && p.ipp_lon <= bounds.lonMax && p.ipp_lat >= bounds.latMin && p.ipp_lat <= bounds.latMax); const visibleStations = stationMarkers.filter((m:StationMarker)=>m.lon >= bounds.lonMin && m.lon <= bounds.lonMax && m.lat >= bounds.latMin && m.lat <= bounds.latMax); const w=820,h=560,pad=55; const x=(lon:number)=>pad+(lon-bounds.lonMin)/(bounds.lonMax-bounds.lonMin)*(w-2*pad); const y=(lat:number)=>h-pad-(lat-bounds.latMin)/(bounds.latMax-bounds.latMin)*(h-2*pad); return <><svg viewBox={`0 0 ${w} ${h}`} className={`plot ${enlarged?'plotLarge':''}`}><rect x="0" y="0" width={w} height={h} fill="#fff"/><text x="55" y="28" className="mapTitle">{`PRN ${prn || 'All'}${actualTimeH ? ` · epoch ${actualTimeH} h` : ''}`}</text>{layers.dtec && raster?.image_href && <image className="dtecRaster" href={raster.image_href} x={x(bounds.lonMin)} y={y(bounds.latMax)} width={x(bounds.lonMax)-x(bounds.lonMin)} height={y(bounds.latMin)-y(bounds.latMax)} opacity={rasterOpacity/100} preserveAspectRatio="none"><title>{`Natural-neighbour grid ready: ${raster.prn}, ${Number(raster.actual_time_h).toFixed(5)} h, ${raster.station_count} stations`}</title></image>}{layers.borders && <Borders world={world} x={x} y={y}/>} {layers.grid && <g className="grid">{[-20,0,20,40,50].map(l=><g key={'x'+l}><line x1={x(l)} x2={x(l)} y1={pad} y2={h-pad}/><text x={x(l)} y={h-18}>{l}°</text></g>)}{[20,40,60,80].map(l=><g key={'y'+l}><line x1={pad} x2={w-pad} y1={y(l)} y2={y(l)}/><text x={12} y={y(l)}>{l}°</text></g>)}</g>}{layers.rays && <g className="stationRays">{visiblePoints.map((p:Point,i:number)=>{ const m=stationByCode.get(p.station); if(!m) return null; const sel=selected.includes(`${p.station}|${p.prn}`); return <line key={`ray-${i}`} x1={x(m.lon)} y1={y(m.lat)} x2={x(p.ipp_lon)} y2={y(p.ipp_lat)} className={sel?'selectedRay':''}/>;})}</g>} {layers.ipp && visiblePoints.map((p:Point,i:number)=>{const id=`${p.station}|${p.prn}`; const sel=selected.includes(id); return <circle key={i} cx={x(p.ipp_lon)} cy={y(p.ipp_lat)} r={sel?5.5:3.5} fill={dtecColor(p.dtec)} stroke={sel?'#ffbf00':'#44515f'} onClick={()=>onPointClick(p)} onMouseEnter={()=>setTip(`Click selects station ${id}; dTEC=${p.dtec} TECU time=${p.time_h} h elev=${p.elevation}`)}><title>{`IPP for station ${p.station}; click selects station ${id}. dTEC=${p.dtec} TECU`}</title></circle>})}{layers.stations && visibleStations.map((m:StationMarker)=>{ const ids = selected.filter((id:string)=>id.startsWith(`${m.station}|`)); const sel = ids.length > 0; return <g key={m.station} className={`stationMarker ${sel?'selectedStation':''}`} onClick={()=>onStationClick(m.station, prn)} onMouseEnter={()=>setTip(`${m.station} — ${m.city || 'Unknown city'}, ${m.country || 'unknown'}; ${m.lat.toFixed(4)}, ${m.lon.toFixed(4)}; ${m.full_site_id || 'no identifier'}`)}><title>{`${m.station} — ${m.city || 'Unknown city'}, ${m.country || 'unknown'}; lat ${m.lat.toFixed(4)}, lon ${m.lon.toFixed(4)}; id ${m.full_site_id || 'n/a'}.`}</title><circle cx={x(m.lon)} cy={y(m.lat)} r={sel?8:0} className="stationHalo"/><path d={`M${x(m.lon)-4},${y(m.lat)}L${x(m.lon)+4},${y(m.lat)}M${x(m.lon)},${y(m.lat)-4}L${x(m.lon)},${y(m.lat)+4}`}/>{layers.stationLabels && <text className="stationLabel" x={x(m.lon)+5} y={y(m.lat)-3}>{m.station}</text>}</g>})}<rect x={pad} y={pad} width={w-2*pad} height={h-2*pad} fill="none" stroke="#526070"/><Colorbar x={760} y={72}/></svg><p className="tooltip">{tip || 'Click station markers to toggle STATION|PRN selection; IPP clicks select their station too.'}</p></>; }
function Colorbar({x,y}:{x:number;y:number}) { const ticks=[-1,-0.5,0,0.5,1]; return <g className="colorbar"><title>dTEC [TECU], fixed -1 to +1 scale.</title><defs><linearGradient id="dtecGradient" x1="0" x2="0" y1="1" y2="0"><stop offset="0%" stopColor={dtecColor(-1)}/><stop offset="50%" stopColor={dtecColor(0)}/><stop offset="100%" stopColor={dtecColor(1)}/></linearGradient></defs><rect x={x} y={y} width="18" height="210" fill="url(#dtecGradient)" stroke="#526070"/><text x={x-25} y={y-12}>dTEC [TECU]</text>{ticks.map(t=><g key={t}><line x1={x+18} x2={x+24} y1={y+(1-(t+1)/2)*210} y2={y+(1-(t+1)/2)*210}/><text x={x+28} y={y+(1-(t+1)/2)*210+4}>{t>0?`+${t}`:t}</text></g>)}</g>; }
function Borders({world,x,y}:any){
  const paths:string[]=[];
  const inMapBounds=(c:any)=>Array.isArray(c) && c.length >= 2 && c[0] >= bounds.lonMin && c[0] <= bounds.lonMax && c[1] >= bounds.latMin && c[1] <= bounds.latMax;
  const addRing=(coords:any[])=>{
    let d='';
    coords.forEach((c:any)=>{
      if(!inMapBounds(c)){ if(d){ paths.push(d); d=''; } return; }
      d += `${d?'L':'M'}${x(c[0])},${y(c[1])}`;
    });
    if(d) paths.push(d);
  };
  const addGeometry=(g:any)=>{
    if(!g) return;
    if(g.type==='LineString') addRing(g.coordinates);
    if(g.type==='Polygon') g.coordinates.forEach(addRing);
    if(g.type==='MultiPolygon') g.coordinates.forEach((polygon:any[])=>polygon.forEach(addRing));
  };
  const addGeoJson=(item:any)=>{
    if(!item) return;
    if(item.type==='FeatureCollection') item.features?.forEach(addGeoJson);
    else if(item.type==='Feature') addGeometry(item.geometry);
    else addGeometry(item);
  };
  addGeoJson(world);
  return paths.length ? <g className="borders">{paths.map((d,i)=><path key={i} d={d}/>)}</g> : null;
}
function axisTicks(min:number,max:number,n=5){ return Array.from({length:n},(_,i)=>min+(max-min)*i/(n-1)); }
function SeriesPlot({series,selectedLine,setSelectedLine,large}:any){ const [tip,setTip]=useState(''); if(!series?.length) return null; const pts=series.flatMap((s:Series)=>s.points); if(!pts.length) return <p className="placeholder">No time-series points.</p>; const w=large?920:360,h=large?520:240,padL=52,padB=42,padT=24,padR=18; const minT=Math.min(...pts.map((p:any)=>p.time_h)), maxT=Math.max(...pts.map((p:any)=>p.time_h)); const minD=-1, maxD=1; const clipped=pts.filter((p:any)=>p.dtec < -1 || p.dtec > 1).length; const x=(t:number)=>padL+(t-minT)/Math.max(1e-9,maxT-minT)*(w-padL-padR); const y=(d:number)=>h-padB-(d-minD)/Math.max(1e-9,maxD-minD)*(h-padT-padB); const colors=['#0077b6','#d97706','#2b9348','#d00000','#6d28d9','#0f766e','#be185d','#475569']; return <><svg viewBox={`0 0 ${w} ${h}`} className={`series ${large?'seriesLarge':''}`}>{axisTicks(minT,maxT).map(t=><g key={'xt'+t}><line x1={x(t)} x2={x(t)} y1={padT} y2={h-padB}/><text x={x(t)-14} y={h-14}>{t.toFixed(2)}</text></g>)}{axisTicks(minD,maxD).map(d=><g key={'yd'+d}><line x1={padL} x2={w-padR} y1={y(d)} y2={y(d)}/><text x={8} y={y(d)+4}>{d.toFixed(2)}</text></g>)}<text x={w/2-36} y={h-4}>Time [h UT]</text><text x={16} y={18}>dTEC [TECU]</text>{series.map((s:Series,i:number)=>{const key=`${s.station}|${s.prn}`; const d=s.points.map((p,j)=>`${j?'L':'M'}${x(p.time_h)},${y(Math.max(-1, Math.min(1, p.dtec)))}`).join(''); return <path key={key} d={d} className={selectedLine===key?'line selectedLine':'line'} style={{stroke:colors[i%colors.length]}} onClick={()=>setSelectedLine(key)} onMouseMove={(e:any)=>{const r=e.currentTarget.ownerSVGElement.getBoundingClientRect(); const time=minT+(e.clientX-r.left)/r.width*(maxT-minT); const near=s.points.reduce((a,b)=>Math.abs(b.time_h-time)<Math.abs(a.time_h-time)?b:a,s.points[0]); setTip(`${s.station} time ${near.time_h.toFixed(4)} h dTEC ${near.dtec.toFixed(3)} TECU`);}}><title>{`${key}; click selects for spectral analysis.`}</title></path>})}<g className="legend">{series.map((s:Series,i:number)=>{const key=`${s.station}|${s.prn}`; return <g key={key} transform={`translate(${padL+i%2*120},${padT+16*Math.floor(i/2)})`} onClick={()=>setSelectedLine(key)}><line x1="0" x2="18" y1="0" y2="0" style={{stroke:colors[i%colors.length],strokeWidth:selectedLine===key?4:2}}/><text x="24" y="4">{s.station}</text></g>})}</g></svg>{clipped > 0 && <p className="mini">{clipped} values outside displayed range [-1, 1] TECU</p>}<p className="tooltip">{tip || 'Hover a line for station, time, and dTEC. Click a line to select it for spectral analysis.'}</p></>; }
function FftPlot({data}:{data:FftResult}){ const [tip,setTip]=useState(''); const w=360,h=250,padL=52,padB=46,padT=28,padR=18; const xs=data.period_min, ys=data.amplitude; const maxY=Math.max(...ys,1e-9); const xMin=Math.min(...xs,2), xMax=Math.max(...xs,180); const x=(v:number)=>padL+(v-xMin)/Math.max(1e-9,xMax-xMin)*(w-padL-padR); const y=(v:number)=>h-padB-v/maxY*(h-padT-padB); const d=xs.map((v,i)=>`${i?'L':'M'}${x(v)},${y(ys[i])}`).join(''); return <><svg viewBox={`0 0 ${w} ${h}`} className="series fftPlot"><text x="112" y="18">FFT amplitude</text>{axisTicks(xMin,xMax,5).map(t=><g key={'fx'+t}><line x1={x(t)} x2={x(t)} y1={padT} y2={h-padB}/><text x={x(t)-12} y={h-14}>{t.toFixed(0)}</text></g>)}{axisTicks(0,maxY,5).map(a=><g key={'fy'+a}><line x1={padL} x2={w-padR} y1={y(a)} y2={y(a)}/><text x="8" y={y(a)+4}>{a.toFixed(2)}</text></g>)}<rect x={padL} y={padT} width={w-padL-padR} height={h-padT-padB} fill="none" stroke="#526070"/><text x="130" y={h-8}>Period [min]</text><text x="4" y="18">Amplitude [TECU]</text><path d={d} className="line selectedLine" style={{stroke:'#2563eb'}} onMouseMove={(e:any)=>{const r=e.currentTarget.ownerSVGElement.getBoundingClientRect(); const period=xMin+(e.clientX-r.left)/r.width*(xMax-xMin); const idx=xs.reduce((best, v, i)=>Math.abs(v-period)<Math.abs(xs[best]-period)?i:best,0); setTip(`Period ${xs[idx].toFixed(2)} min; amplitude ${ys[idx].toFixed(4)} TECU`);}}/></svg><p className="tooltip">{tip || 'Hover FFT line for period and amplitude.'}</p></>; }
function MorletPlot({data}:{data:MorletResult}){ const w=380,h=280,padL=52,padB=46,padT=32,padR=54; const t0=Math.min(...data.time_h), t1=Math.max(...data.time_h); const p0=Math.min(...data.period_min), p1=Math.max(...data.period_min); const flat=data.power.flat(); const max=Math.max(...flat,1e-12); const x=(v:number)=>padL+(v-t0)/Math.max(1e-9,t1-t0)*(w-padL-padR); const y=(v:number)=>h-padB-(v-p0)/(p1-p0)*(h-padT-padB); const color=(v:number)=>`hsl(${240-240*Math.sqrt(v/max)},80%,55%)`; const cbX=w-34, cbY=padT, cbH=h-padT-padB; return <svg viewBox={`0 0 ${w} ${h}`} className="series morletPlot"><text x="78" y="18">Morlet power · {data.station} {data.prn} · {t0.toFixed(2)}–{t1.toFixed(2)} h</text>{axisTicks(t0,t1,5).map(t=><g key={'mt'+t}><line x1={x(t)} x2={x(t)} y1={padT} y2={h-padB}/><text x={x(t)-12} y={h-14}>{t.toFixed(2)}</text></g>)}{axisTicks(p0,p1,5).map(period=><g key={'mp'+period}><line x1={padL} x2={w-padR} y1={y(period)} y2={y(period)}/><text x="8" y={y(period)+4}>{period.toFixed(0)}</text></g>)}{data.period_min.map((period,iy)=>data.time_h.map((time,ix)=>{ const nextT=data.time_h[ix+1]??time+(data.time_h[1]-data.time_h[0]||0.01); const nextP=data.period_min[iy+1]??period+(data.period_min[1]-data.period_min[0]||1); return <rect key={`${ix}-${iy}`} x={x(time)} y={y(nextP)} width={Math.max(1,x(nextT)-x(time)+.5)} height={Math.max(1,y(period)-y(nextP)+.5)} fill={color(data.power[iy]?.[ix]||0)}/>;}))}<rect x={padL} y={padT} width={w-padL-padR} height={h-padT-padB} fill="none" stroke="#526070"/><text x="130" y={h-8}>Time [h UT]</text><text x="4" y="18">Period [min]</text><defs><linearGradient id="powerGradient" x1="0" x2="0" y1="1" y2="0"><stop offset="0%" stopColor={color(0)}/><stop offset="100%" stopColor={color(max)}/></linearGradient></defs><rect x={cbX} y={cbY} width="14" height={cbH} fill="url(#powerGradient)" stroke="#526070"/><text x={cbX-6} y={cbY-10}>Power</text>{axisTicks(0,max,4).map(v=><g key={'cb'+v}><line x1={cbX+14} x2={cbX+19} y1={cbY+(1-v/max)*cbH} y2={cbY+(1-v/max)*cbH}/><text x={cbX+21} y={cbY+(1-v/max)*cbH+4}>{v.toFixed(2)}</text></g>)}</svg>; }

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

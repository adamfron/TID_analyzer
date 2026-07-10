import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type Status = { stage: string; current: number; total: number; message: string };
type Manifest = { station_count:number; stations?:string[]; gps_prns:string[]; time_range_hours:{min:number|null;max:number|null}; total_rows_seen:number; valid_rows_after_filters:number; malformed_row_count:number; non_gps_row_count:number; low_elevation_row_count:number; out_of_bounds_row_count:number; applied_filters:Record<string,string|number>; };
type Point = { station:string; time_h:number; prn:string; dtec:number; azimuth:number; elevation:number; ipp_lon:number; ipp_lat:number };
type StationMarker = { station:string; lon:number; lat:number; approximate?:boolean; source?:string };
type MapLayers = { borders:boolean; grid:boolean; ipp:boolean; stations:boolean; stationLabels:boolean; dtec:boolean };
type Arc = { prn:string; arc_index:number; start_time_h:number; end_time_h:number; duration_min:number; row_count:number; station_count:number };
type Series = { station:string; prn:string; points:{time_h:number; dtec:number; elevation:number; ipp_lon:number; ipp_lat:number}[] };
type PanelId = 'summary'|'visibility'|'map'|'timeseries'|'spectral'|'candidates'|'report';
type MapMode = 'epoch'|'satellite'|'window';
type OperationKey = 'world'|'visibility'|'preview'|'timeseries'|'import'|'browse';
type Operation = { key: OperationKey; label: string; slow: boolean } | null;
const API = 'http://127.0.0.1:8000';
const bounds = { lonMin: -20, lonMax: 50, latMin: 20, latMax: 80 };
const panels:{id:PanelId; n:number; title:string}[] = [
  {id:'summary', n:1, title:'Data import & summary'}, {id:'visibility', n:2, title:'Satellite visibility'}, {id:'map', n:3, title:'Map explorer'}, {id:'timeseries', n:4, title:'Station time series'}, {id:'spectral', n:5, title:'Spectral analysis'}, {id:'candidates', n:6, title:'TID candidates'}, {id:'report', n:7, title:'Event report'},
];

function App() {
  const [folderPath, setFolderPath] = useState('');
  const [status, setStatus] = useState<Status>({ stage: 'idle', current: 0, total: 0, message: 'Idle' });
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [stationMarkers, setStationMarkers] = useState<StationMarker[]>([]);
  const [rasterAvailable, setRasterAvailable] = useState(false);
  const [layers, setLayers] = useState<MapLayers>({borders:true, grid:true, ipp:true, stations:true, stationLabels:false, dtec:false});
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
  const progress = useMemo(() => (status.total > 0 ? Math.round((status.current / status.total) * 100) : 0), [status]);

  useEffect(() => { runOperation('world', 'Loading world borders…', async () => { const r = await fetch(`${API}/api/assets/world-borders`); if (!r.ok) throw new Error('World borders failed to load'); setWorld(await r.json()); setStatus({stage:'done', current:1, total:1, message:'World borders loaded.'}); }).catch(()=>setWorld(null)); }, []);
  useEffect(() => {
    const ws = new WebSocket('ws://127.0.0.1:8000/ws/import-progress');
    ws.onmessage = (event) => { const update = JSON.parse(event.data) as Status; setStatus(update); if (update.stage === 'done' || update.stage === 'error') setOperation(o => o?.key === 'import' ? null : o); if (update.stage === 'done') fetch(`${API}/api/manifest`).then((r) => r.json()).then(setManifest); };
    return () => ws.close();
  }, []);

  async function runOperation(key:OperationKey, label:string, work:()=>Promise<void>, clearOnDone=true) {
    setOperation({key, label, slow:false}); setStatus({stage:key, current:0, total:0, message:label});
    const timer = window.setTimeout(() => setOperation(o => o?.key === key ? {...o, slow:true} : o), 2000);
    try { await work(); } catch (err:any) { setStatus({stage:'error', current:0, total:0, message:err?.message || `${label} failed`}); throw err; }
    finally { window.clearTimeout(timer); if (clearOnDone) setOperation(o => o?.key === key ? null : o); }
  }
  async function browse() { await runOperation('browse', 'Opening folder browser…', async () => { const r = await fetch(`${API}/api/select-folder`, { method:'POST' }); const j = await r.json(); if (r.ok && j.folder_path) { setFolderPath(j.folder_path); setStatus({stage:'done', current:1, total:1, message:'Folder selected.'}); } else throw new Error(j.detail || 'Folder dialog failed'); }); }
  async function startImport() { await runOperation('import', 'Starting import…', async () => { setManifest(null); setPoints([]); setStationMarkers([]); setRasterAvailable(false); setArcs([]); setRequestedTimeH(''); setActualTimeH(''); setStationCount(0); const response = await fetch(`${API}/api/import`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ folder_path: folderPath }) }); if (!response.ok) throw new Error(await response.text()); }, false); }
  async function loadVisibility() { await runOperation('visibility', 'Loading satellite visibility…', async () => { const r = await fetch(`${API}/api/satellites/visibility`); if (!r.ok) throw new Error(await r.text()); const j = await r.json(); const next = j.arcs || []; setArcs(next); setStatus({stage:'done', current:1, total:1, message:`Loaded ${next.length} visibility arcs.`}); }); }
  async function loadPreview(overrideTimeH?:string, overridePrn?:string, forceEpoch=false) { await runOperation('preview', 'Loading map points…', async () => { setMapError(''); const requestedTime = overrideTimeH ?? timeH; const requestedPrn = overridePrn ?? prn; const requestedMode = forceEpoch ? 'epoch' : mapMode; setRequestedTimeH(requestedMode === 'epoch' ? requestedTime : ''); if (requestedMode === 'epoch' && requestedPrn && requestedTime !== '') { const q = new URLSearchParams({ prn: requestedPrn, time_h: requestedTime }); const r = await fetch(`${API}/api/map/epoch?${q}`); if (!r.ok) throw new Error(await r.text()); const j = await r.json(); const visible = (j.points || []).filter(inBounds); setPoints(visible); setStationMarkers(stationMarkersFromPoints(visible)); setRasterAvailable(Boolean(j.raster_available)); setLimitReached(false); setActualTimeH(j.actual_time_h != null ? String(Number(j.actual_time_h).toFixed(5)) : ''); setStationCount((j.stations || []).length || new Set(visible.map((p:Point)=>p.station)).size); if (j.actual_time_h != null) setTimeH(String(Number(j.actual_time_h).toFixed(5))); const msg = `Requested ${Number(requestedTime).toFixed(5)} h; actual ${j.actual_time_h != null ? Number(j.actual_time_h).toFixed(5) : 'n/a'} h; ${visible.length} IPP points; ${((j.stations || []).length || new Set(visible.map((p:Point)=>p.station)).size)} stations`; setPreviewMeta(msg); setStatus({stage:'done', current:1, total:1, message:msg}); return; } const q = new URLSearchParams({ tolerance_seconds:String(tol), max_points:String(maxPoints) }); if (requestedPrn) q.set('prn', requestedPrn); if (requestedMode === 'epoch' && requestedTime !== '') q.set('time_h', requestedTime); if (requestedMode === 'window') { if (startTimeH !== '') q.set('start_time_h', startTimeH); if (endTimeH !== '') q.set('end_time_h', endTimeH); } const r = await fetch(`${API}/api/preview/points?${q}`); if (!r.ok) throw new Error(await r.text()); const j = await r.json(); const visible = (j.points || []).filter(inBounds); setPoints(visible); setStationMarkers(j.station_markers || stationMarkersFromPoints(visible)); setRasterAvailable(Boolean(j.raster_available)); setLimitReached(Boolean(j.limit_reached)); const actual = j.actual_time_h ?? j.requested_time_h ?? (requestedMode === 'epoch' ? requestedTime : 'n/a'); setActualTimeH(j.actual_time_h != null ? String(Number(j.actual_time_h).toFixed(5)) : ''); setStationCount(new Set(visible.map((p:Point)=>p.station)).size); if (requestedMode === 'epoch' && j.actual_time_h != null) setTimeH(String(Number(j.actual_time_h).toFixed(5))); const msg = `Requested ${requestedMode === 'epoch' ? requestedTime : 'n/a'} h; actual ${actual}; ${visible.length}/${j.total_matching_before_limit} IPP points; ${new Set(visible.map((p:Point)=>p.station)).size} stations`; setPreviewMeta(msg); setStatus({stage:'done', current:1, total:1, message:msg}); }); }
  function chooseArc(a:Arc) { const firstEpoch = String(a.start_time_h.toFixed(5)); setPrn(a.prn); setTimeH(firstEpoch); setStartTimeH(String(a.start_time_h)); setEndTimeH(String(a.end_time_h)); setMapMode('epoch'); setOpen(o=>({...o,map:true})); setTimeout(() => loadPreview(firstEpoch, a.prn, true), 0); }
  function step(minutes:number) { setTimeH(v => { const next = String(Math.max(0, (Number(v || 0) + minutes / 60)).toFixed(5)); if (prn && mapMode === 'epoch') setTimeout(() => loadPreview(next), 0); return next; }); }
  function inBounds(p:Point) { return p.ipp_lon >= bounds.lonMin && p.ipp_lon <= bounds.lonMax && p.ipp_lat >= bounds.latMin && p.ipp_lat <= bounds.latMax; }
  function stationMarkersFromPoints(rows:Point[]):StationMarker[] { const by = new Map<string, Point[]>(); rows.forEach(row => by.set(row.station, [...(by.get(row.station) || []), row])); return [...by.entries()].map(([station, rows]) => ({ station, lon: rows.reduce((a,r)=>a+r.ipp_lon,0)/rows.length, lat: rows.reduce((a,r)=>a+r.ipp_lat,0)/rows.length, approximate:true, source:'mean_epoch_ipp' })); }
  function toggleStationId(station:string, usePrn?:string) { const id = `${station}|${usePrn || prn}`; if (!id.endsWith('|')) setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id]); }
  function toggleStation(p:Point) { if (!inBounds(p)) return; const id = `${p.station}|${p.prn}`; setSelected(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id]); }
  async function loadSeries() { await runOperation('timeseries', 'Loading time series…', async () => { const stations = selected.map(s=>s.split('|')[0]); const usePrn = prn || selected[0]?.split('|')[1]; if (!usePrn || !stations.length) return; const q = new URLSearchParams({ prn: usePrn }); stations.forEach(s=>q.append('station', s)); if (startTimeH) q.set('start_time_h', startTimeH); if (endTimeH) q.set('end_time_h', endTimeH); const r = await fetch(`${API}/api/stations/timeseries?${q}`); if (!r.ok) throw new Error(await r.text()); const j = await r.json(); const next = j.series || []; setSeries(next); setStatus({stage:'done', current:1, total:1, message:`Loaded ${next.length} time series.`}); }); }

  return <div className="app">
    <header className="topbar"><span className="brand">TID Analyzer</span><input value={folderPath} onChange={e=>setFolderPath(e.target.value)} placeholder="Local daily folder path" /><button onClick={browse} disabled={operation?.key==='browse'} title="Choose a local daily data folder.">Browse…</button><button onClick={startImport} disabled={operation?.key==='import'} title="Import and filter the selected folder.">Import</button><progress {...(operation && status.total === 0 ? {} : {value: progress})} max="100" /><span>{operation ? operation.label : status.message}</span>{operation && <span className="spinner" aria-label="Loading"/>}{operation?.slow && <span className="mini">This may take a while for the first run; results will be cached.</span>}{manifest && <span className="mini">Stations {manifest.station_count} · PRNs {manifest.gps_prns.length} · Valid {manifest.valid_rows_after_filters}</span>}</header>
    <main className="workspace"><section className="canvas"><div className="canvasHead"><h1>Map Explorer</h1><span>{previewMeta}{limitReached ? ' · sampled limit reached' : ''}</span></div><PreviewPlot points={points} stationMarkers={stationMarkers} world={world} layers={layers} selected={selected} onPointClick={toggleStation} onStationClick={toggleStationId} prn={prn} actualTimeH={actualTimeH}/>{mapError && <p className="warn">{mapError}</p>}</section><aside className="side">{panels.map(p=><WorkflowPanel key={p.id} panel={p} open={open[p.id]} toggle={()=>setOpen(o=>({...o,[p.id]:!o[p.id]}))} onEnlarge={()=>setMapEnlarged(true)}>{p.id==='summary' && <Summary manifest={manifest}/>} {p.id==='visibility' && <Visibility arcs={arcs} load={loadVisibility} choose={chooseArc} loading={operation?.key==='visibility'}/>} {p.id==='map' && <Explorer manifest={manifest} prn={prn} setPrn={setPrn} timeH={timeH} setTimeH={setTimeH} startTimeH={startTimeH} setStartTimeH={setStartTimeH} endTimeH={endTimeH} setEndTimeH={setEndTimeH} tol={tol} setTol={setTol} maxPoints={maxPoints} setMaxPoints={setMaxPoints} loadPreview={loadPreview} count={points.length} limitReached={limitReached} mapMode={mapMode} setMapMode={setMapMode} step={step} loading={operation?.key==='preview'} layers={layers} setLayers={setLayers} rasterAvailable={rasterAvailable} requestedTimeH={requestedTimeH} actualTimeH={actualTimeH} stationCount={stationCount} exportReady={points.length>0} onExportError={setMapError} onEnlarge={()=>setMapEnlarged(true)}/>} {p.id==='timeseries' && <TimeSeries selected={selected} setSelected={setSelected} load={loadSeries} loading={operation?.key==='timeseries'} series={series} selectedLine={selectedLine} setSelectedLine={setSelectedLine}/>} {p.id==='spectral' && <Placeholder text={selectedLine ? `Selected ${selectedLine}. FFT and Morlet endpoints are TODO.` : 'Select a time-series line to enable FFT and Morlet placeholders.'}/>} {p.id==='candidates' && <Placeholder text="TID candidate detection placeholder."/>} {p.id==='report' && <Placeholder text="Event report and export placeholder."/>}</WorkflowPanel>)}</aside></main>
    {mapEnlarged && <div className="modalBackdrop"><div className="mapModal"><button className="close" onClick={()=>setMapEnlarged(false)} title="Close enlarged map view.">Close ×</button><PreviewPlot points={points} stationMarkers={stationMarkers} world={world} layers={layers} selected={selected} onPointClick={toggleStation} onStationClick={toggleStationId} prn={prn} actualTimeH={actualTimeH} enlarged/></div></div>}
    <footer>References · dTEC is vertical dTEC · IPP height 450 km · GPS PRN prefix G · min elevation 50°</footer>
  </div>;
}
function WorkflowPanel({panel,open,toggle,onEnlarge,children}:any) { return <section className="workflow"><div className="workflowTitle"><button onClick={toggle}>{open?'▾':'▸'} {panel.n}. {panel.title}</button><button onClick={onEnlarge} disabled={panel.id!=='map'} title={panel.id==='map' ? 'Open Map Explorer in a larger overlay.' : 'Only Map Explorer enlargement is available.'}>Enlarge</button></div>{open && <div className="workflowBody">{children}</div>}</section>; }
function Summary({manifest}:{manifest:Manifest|null}) { if(!manifest) return <p className="placeholder">Import a folder to view metadata.</p>; return <div className="summary"><p><b>Stations:</b> {manifest.station_count}</p><p><b>GPS PRNs:</b> {manifest.gps_prns.join(', ') || 'None'}</p><p><b>Time range:</b> {manifest.time_range_hours.min}–{manifest.time_range_hours.max} h</p><p><b>Rows:</b> {manifest.valid_rows_after_filters} valid / {manifest.total_rows_seen} seen</p><p className="mini">Filters: GPS, elevation ≥50°, lon -20..50, lat 20..80.</p></div>; }
function Visibility({arcs,load,choose,loading}:any) { return <div><button onClick={load} disabled={loading} title="Load per-PRN visibility arcs from imported observations.">{loading ? 'Loading visibility…' : 'Load visibility'}</button><p className="mini">{loading ? 'Loading visibility…' : `${arcs.length} visibility arcs loaded.`}</p><table><thead><tr><th>PRN</th><th>arc</th><th>start</th><th>end</th><th>min</th><th>rows</th><th>sta</th></tr></thead><tbody>{arcs.map((a:Arc)=><tr key={`${a.prn}-${a.arc_index}`} onClick={()=>choose(a)}><td>{a.prn}</td><td>{a.arc_index}</td><td>{a.start_time_h.toFixed(2)}</td><td>{a.end_time_h.toFixed(2)}</td><td>{a.duration_min.toFixed(1)}</td><td>{a.row_count}</td><td>{a.station_count}</td></tr>)}</tbody></table></div>; }
function Explorer(p:any) {
  const loadLabel = p.mapMode === 'satellite' ? 'Load satellite track preview' : 'Load epoch map';
  return <div className="summary">
    <label>Mode <select title="Choose whether to draw one epoch, one satellite sample, or a time window." value={p.mapMode} onChange={(e:any)=>p.setMapMode(e.target.value)}><option value="epoch">Current epoch</option><option value="satellite">Whole selected satellite</option><option value="window">Selected time window</option></select></label>
    <label>PRN <select title="Filter map points to a GPS PRN. Selecting a PRN enables automatic current-epoch navigation." value={p.prn} onChange={(e:any)=>p.setPrn(e.target.value)}><option value="">All</option>{p.manifest?.gps_prns.map((x:string)=><option key={x}>{x}</option>)}</select></label>
    {p.mapMode==='epoch' && <><label>Time (h) <input title="Requested epoch in hours from start of day; navigation loads the nearest epoch automatically when a PRN is selected." value={p.timeH} onChange={(e:any)=>p.setTimeH(e.target.value)}/></label><div className="buttonRow">{[-15,-5,-0.5,0.5,5,15].map(m=><button key={m} onClick={()=>p.step(m)} title="Move requested time and automatically load nearest epoch when a PRN is selected.">{m>0?'+':''}{m===-0.5?'- prev epoch':m===0.5?'next epoch':`${m} min`}</button>)}</div></>}
    {p.mapMode==='window' && <><label>Start h <input title="Start time in hours from start of day." value={p.startTimeH} onChange={(e:any)=>p.setStartTimeH(e.target.value)}/></label><label>End h <input title="End time in hours from start of day." value={p.endTimeH} onChange={(e:any)=>p.setEndTimeH(e.target.value)}/></label></>}
    <fieldset className="layers"><legend>Layers</legend>{[['borders','Show country borders','Thin grey country borders below science layers.'],['grid','Show grid','Show longitude/latitude guide lines.'],['ipp','Show IPP points','Show ionospheric pierce points colored by dTEC; clicking selects the station for the current PRN.'],['stations','Show station markers','Show subtle station markers; click markers to toggle STATION|PRN selection.'],['stationLabels','Show station labels','Show compact station names near markers; turn off when dense.']].map(([key,label,title])=><label key={key} title={title}><input type="checkbox" checked={p.layers[key]} onChange={(e:any)=>p.setLayers((x:MapLayers)=>({...x,[key]:e.target.checked}))}/>{label}</label>)}<label title={p.rasterAvailable ? 'Show interpolated dTEC raster.' : 'Requires interpolated epoch grid, implemented in next stage.'}><input type="checkbox" checked={p.layers.dtec && p.rasterAvailable} disabled={!p.rasterAvailable} onChange={(e:any)=>p.setLayers((x:MapLayers)=>({...x,dtec:e.target.checked}))}/>Show interpolated dTEC map</label>{!p.rasterAvailable && <p className="mini">Requires interpolated epoch grid, implemented in next stage.</p>}</fieldset>
    <details title="Advanced map sampling settings; ordinary current-epoch navigation does not require these."><summary>Advanced</summary>{p.mapMode==='epoch' && <label>Tolerance seconds <input type="number" value={p.tol} onChange={(e:any)=>p.setTol(Number(e.target.value))}/></label>}<label>Max points <input type="number" value={p.maxPoints} onChange={(e:any)=>p.setMaxPoints(Number(e.target.value))}/></label></details>
    <button onClick={p.loadPreview} disabled={p.loading} title="Fallback/manual refresh for the map.">{p.loading ? 'Loading map points…' : loadLabel}</button>
    <div className="buttonRow"><button onClick={()=>exportActiveMap(p.prn, p.actualTimeH || p.timeH, p.onExportError)} disabled={!p.exportReady} title="Export the active main SVG map view to a PNG file.">Export PNG</button><button disabled title="Export GIF will be added in a later animation stage.">Export GIF</button><button onClick={p.onEnlarge} title="Open a larger Map Explorer view while preserving PRN, time, and layer state.">Enlarge</button></div>
    <div className="mapStats"><p><b>Requested time:</b> {p.requestedTimeH || 'n/a'}</p><p><b>Actual epoch time:</b> {p.actualTimeH || 'n/a'}</p><p><b>IPP points:</b> {p.count}</p><p><b>Stations:</b> {p.stationCount}</p></div>
    {p.mapMode==='satellite' && <p className="mini">Whole satellite mode displays a deterministic sample, not all points.</p>}{p.limitReached && <p className="mini">Deterministic sample limit reached.</p>}
  </div>;
}
function TimeSeries({selected,setSelected,load,loading,series,selectedLine,setSelectedLine}:any) { return <div><p>{selected.length ? selected.map((s:string)=><button key={s} onClick={()=>setSelected((x:string[])=>x.filter(y=>y!==s))}>{s} ×</button>) : <span className="placeholder">Click map points to select station/PRN.</span>}</p><button onClick={load} disabled={!selected.length || loading} title="Load dTEC time series for selected stations.">{loading ? 'Loading time series…' : 'Load time series'}</button><button onClick={()=>setSelected([])}>Clear</button><SeriesPlot series={series} selectedLine={selectedLine} setSelectedLine={setSelectedLine}/></div>; }
function Placeholder({text}:{text:string}) { return <p className="placeholder">{text}<br/><button disabled>Compute FFT</button> <button disabled>Compute Morlet</button></p>; }
function dtecColor(v:number) {
  const clamped = Math.max(-1, Math.min(1, Number.isFinite(v) ? v : 0));
  if (clamped < 0) { const g = Math.round(255 + clamped * 155); return `rgb(${g},${g},255)`; }
  const g = Math.round(255 - clamped * 155); return `rgb(255,${g},${g})`;
}
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
function PreviewPlot({points,stationMarkers,world,layers,selected,onPointClick,onStationClick,prn,actualTimeH,enlarged}:any) { const [tip,setTip]=useState(''); const visiblePoints = points.filter((p:Point)=>p.ipp_lon >= bounds.lonMin && p.ipp_lon <= bounds.lonMax && p.ipp_lat >= bounds.latMin && p.ipp_lat <= bounds.latMax); const visibleStations = stationMarkers.filter((m:StationMarker)=>m.lon >= bounds.lonMin && m.lon <= bounds.lonMax && m.lat >= bounds.latMin && m.lat <= bounds.latMax); const w=820,h=560,pad=55; const x=(lon:number)=>pad+(lon-bounds.lonMin)/(bounds.lonMax-bounds.lonMin)*(w-2*pad); const y=(lat:number)=>h-pad-(lat-bounds.latMin)/(bounds.latMax-bounds.latMin)*(h-2*pad); return <><svg viewBox={`0 0 ${w} ${h}`} className={`plot ${enlarged?'plotLarge':''}`}><rect x="0" y="0" width={w} height={h} fill="#fff"/><text x="55" y="28" className="mapTitle">{`PRN ${prn || 'All'}${actualTimeH ? ` · epoch ${actualTimeH} h` : ''}`}</text>{layers.grid && <g className="grid">{[-20,0,20,40,50].map(l=><g key={'x'+l}><line x1={x(l)} x2={x(l)} y1={pad} y2={h-pad}/><text x={x(l)} y={h-18}>{l}°</text></g>)}{[20,40,60,80].map(l=><g key={'y'+l}><line x1={pad} x2={w-pad} y1={y(l)} y2={y(l)}/><text x={12} y={y(l)}>{l}°</text></g>)}</g>}{layers.borders && <Borders world={world} x={x} y={y}/>} {layers.ipp && visiblePoints.map((p:Point,i:number)=>{const id=`${p.station}|${p.prn}`; const sel=selected.includes(id); return <circle key={i} cx={x(p.ipp_lon)} cy={y(p.ipp_lat)} r={sel?5.5:3.5} fill={dtecColor(p.dtec)} stroke={sel?'#ffbf00':'#44515f'} onClick={()=>onPointClick(p)} onMouseEnter={()=>setTip(`Click selects station ${id}; dTEC=${p.dtec} TECU time=${p.time_h} h elev=${p.elevation}`)}><title>{`IPP for station ${p.station}; click selects station ${id}. dTEC=${p.dtec} TECU`}</title></circle>})}{layers.stations && visibleStations.map((m:StationMarker)=>{ const ids = selected.filter((id:string)=>id.startsWith(`${m.station}|`)); const sel = ids.length > 0; return <g key={m.station} className={`stationMarker ${sel?'selectedStation':''}`} onClick={()=>onStationClick(m.station, prn)} onMouseEnter={()=>setTip(`Station ${m.station}; click toggles station selection for PRN ${prn || '(choose PRN first)'}.`)}><title>{`Station ${m.station}; click toggles ${m.station}|${prn || 'selected PRN'}.`}</title><circle cx={x(m.lon)} cy={y(m.lat)} r={sel?8:0} className="stationHalo"/><path d={`M${x(m.lon)-4},${y(m.lat)}L${x(m.lon)+4},${y(m.lat)}M${x(m.lon)},${y(m.lat)-4}L${x(m.lon)},${y(m.lat)+4}`}/>{layers.stationLabels && <text x={x(m.lon)+5} y={y(m.lat)-4}>{m.station}</text>}</g>})}<Colorbar x={760} y={72}/></svg><p className="tooltip">{tip || 'Click station markers to toggle STATION|PRN selection; IPP clicks select their station too.'}</p></>; }
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
function SeriesPlot({series,selectedLine,setSelectedLine}:any){ if(!series?.length) return null; const pts=series.flatMap((s:Series)=>s.points); if(!pts.length) return <p className="placeholder">No time-series points.</p>; const w=330,h=190,pad=28; const minT=Math.min(...pts.map((p:any)=>p.time_h)), maxT=Math.max(...pts.map((p:any)=>p.time_h)); const minD=Math.min(...pts.map((p:any)=>p.dtec)), maxD=Math.max(...pts.map((p:any)=>p.dtec)); const x=(t:number)=>pad+(t-minT)/Math.max(1e-9,maxT-minT)*(w-2*pad); const y=(d:number)=>h-pad-(d-minD)/Math.max(1e-9,maxD-minD)*(h-2*pad); return <svg viewBox={`0 0 ${w} ${h}`} className="series">{series.map((s:Series,i:number)=>{const key=`${s.station}|${s.prn}`; const d=s.points.map((p,j)=>`${j?'L':'M'}${x(p.time_h)},${y(p.dtec)}`).join(''); return <path key={key} d={d} className={selectedLine===key?'line selectedLine':'line'} style={{stroke:['#66d9ff','#ffc857','#7ee081','#ff6b9a'][i%4]}} onClick={()=>setSelectedLine(key)}/>})}</svg>; }
createRoot(document.getElementById('root')!).render(<App />);

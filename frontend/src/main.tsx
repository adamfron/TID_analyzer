import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type Status = { stage: string; current: number; total: number; message: string };
type Manifest = { station_count:number; gps_prns:string[]; time_range_hours:{min:number|null;max:number|null}; total_rows_seen:number; valid_rows_after_filters:number; malformed_row_count:number; non_gps_row_count:number; low_elevation_row_count:number; out_of_bounds_row_count:number; applied_filters:Record<string,string|number>; };
type Point = { station:string; time_h:number; prn:string; dtec:number; azimuth:number; elevation:number; ipp_lon:number; ipp_lat:number };
const API = 'http://127.0.0.1:8000';
const bounds = { lonMin: -20, lonMax: 50, latMin: 20, latMax: 80 };

function App() {
  const [folderPath, setFolderPath] = useState('');
  const [status, setStatus] = useState<Status>({ stage: 'idle', current: 0, total: 0, message: 'Idle' });
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [activeTab, setActiveTab] = useState('Data summary');
  const [points, setPoints] = useState<Point[]>([]);
  const [limitReached, setLimitReached] = useState(false);
  const [prn, setPrn] = useState('');
  const [timeH, setTimeH] = useState('');
  const [tol, setTol] = useState(15);
  const [maxPoints, setMaxPoints] = useState(5000);
  const progress = useMemo(() => (status.total > 0 ? Math.round((status.current / status.total) * 100) : 0), [status]);

  useEffect(() => {
    const ws = new WebSocket('ws://127.0.0.1:8000/ws/import-progress');
    ws.onmessage = (event) => { const update = JSON.parse(event.data) as Status; setStatus(update); if (update.stage === 'done') fetch(`${API}/api/manifest`).then((r) => r.json()).then(setManifest); };
    return () => ws.close();
  }, []);

  async function browse() { const r = await fetch(`${API}/api/select-folder`, { method:'POST' }); const j = await r.json(); if (r.ok && j.folder_path) setFolderPath(j.folder_path); else if (!r.ok) setStatus({stage:'error', current:0, total:0, message:j.detail || 'Folder dialog failed'}); }
  async function startImport() { setManifest(null); setPoints([]); const response = await fetch(`${API}/api/import`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ folder_path: folderPath }) }); if (!response.ok) setStatus({ stage:'error', current:0, total:0, message:await response.text() }); }
  async function loadPreview() { const q = new URLSearchParams({ tolerance_seconds:String(tol), max_points:String(maxPoints) }); if (prn) q.set('prn', prn); if (timeH !== '') q.set('time_h', timeH); const j = await fetch(`${API}/api/preview/points?${q}`).then(r=>r.json()); setPoints(j.points || []); setLimitReached(Boolean(j.limit_reached)); }

  return <div className="app">
    <header className="topbar"><input value={folderPath} onChange={e=>setFolderPath(e.target.value)} placeholder="Local daily folder path" /><button onClick={browse}>Browse…</button><button onClick={startImport}>Import</button><progress value={progress} max="100" /><span>{status.message}</span>{manifest && <span className="mini">Stations {manifest.station_count} · PRNs {manifest.gps_prns.length} · {manifest.time_range_hours.min}–{manifest.time_range_hours.max} h · Valid {manifest.valid_rows_after_filters}</span>}</header>
    <main className="workspace"><section className="canvas"><h1>IPP preview</h1><PreviewPlot points={points}/></section><aside className="panel"><nav>{['Data summary','Map explorer','TID candidates','Event analysis'].map(tab=><button className={activeTab===tab?'active':''} onClick={()=>setActiveTab(tab)} key={tab}>{tab}</button>)}</nav>{activeTab==='Data summary' && <Summary manifest={manifest}/>} {activeTab==='Map explorer' && <Explorer manifest={manifest} prn={prn} setPrn={setPrn} timeH={timeH} setTimeH={setTimeH} tol={tol} setTol={setTol} maxPoints={maxPoints} setMaxPoints={setMaxPoints} loadPreview={loadPreview} count={points.length} limitReached={limitReached}/>} {activeTab==='TID candidates' && <p className="placeholder">Stage 3: deterministic TID candidate detection.</p>} {activeTab==='Event analysis' && <p className="placeholder">Stage 4: time series, FFT, wavelets, event reports.</p>}</aside></main>
    <footer>Scientific sources · Citation format · DOI placeholder</footer>
  </div>;
}
function Summary({manifest}:{manifest:Manifest|null}) { if(!manifest) return <p className="placeholder">Import a folder to view metadata.</p>; return <div className="summary">{manifest.valid_rows_after_filters===0 && <p className="warn">Import completed, but no valid rows passed filters. Check parser format, constellation, elevation, and map bounds.</p>}<p><b>Stations:</b> {manifest.station_count}</p><p><b>GPS PRNs:</b> {manifest.gps_prns.join(', ') || 'None'}</p><p><b>Time range:</b> {manifest.time_range_hours.min}–{manifest.time_range_hours.max} h</p><p><b>Total rows seen:</b> {manifest.total_rows_seen}</p><p><b>Valid rows after filters:</b> {manifest.valid_rows_after_filters}</p><p><b>Malformed rows:</b> {manifest.malformed_row_count}</p><p><b>Non-GPS rows:</b> {manifest.non_gps_row_count}</p><p><b>Low elevation rows:</b> {manifest.low_elevation_row_count}</p><p><b>Out-of-bounds rows:</b> {manifest.out_of_bounds_row_count}</p><h3>Filters</h3><pre>{JSON.stringify(manifest.applied_filters,null,2)}</pre></div>; }
function Explorer(p:any) { return <div className="summary"><label>PRN <select value={p.prn} onChange={(e:any)=>p.setPrn(e.target.value)}><option value="">All</option>{p.manifest?.gps_prns.map((x:string)=><option key={x}>{x}</option>)}</select></label><label>Time (h) <input value={p.timeH} onChange={(e:any)=>p.setTimeH(e.target.value)} placeholder={`${p.manifest?.time_range_hours.min ?? ''}`}/></label><label>Tolerance seconds <input type="number" value={p.tol} onChange={(e:any)=>p.setTol(Number(e.target.value))}/></label><label>Max points <input type="number" value={p.maxPoints} onChange={(e:any)=>p.setMaxPoints(Number(e.target.value))}/></label><button onClick={p.loadPreview}>Load preview points</button><p>Returned points: {p.count} {p.limitReached ? '(limit reached)' : ''}</p></div>; }
function PreviewPlot({points}:{points:Point[]}) { const [tip,setTip]=useState(''); if(!points.length) return <p className="placeholder">Load preview points from the Map explorer tab to see IPP lon/lat points.</p>; const w=760,h=520,pad=50; const x=(lon:number)=>pad+(lon-bounds.lonMin)/(bounds.lonMax-bounds.lonMin)*(w-2*pad); const y=(lat:number)=>h-pad-(lat-bounds.latMin)/(bounds.latMax-bounds.latMin)*(h-2*pad); const max=Math.max(1,...points.map(p=>Math.abs(p.dtec))); const color=(v:number)=>v<0?`rgb(${255+v/max*255},${255+v/max*255},255)`: `rgb(255,${255-v/max*255},${255-v/max*255})`; return <><svg viewBox={`0 0 ${w} ${h}`} className="plot">{[-20,0,20,40,50].map(l=><g key={'x'+l}><line x1={x(l)} x2={x(l)} y1={pad} y2={h-pad}/><text x={x(l)} y={h-15}>{l}°</text></g>)}{[20,40,60,80].map(l=><g key={'y'+l}><line x1={pad} x2={w-pad} y1={y(l)} y2={y(l)}/><text x={8} y={y(l)}>{l}°</text></g>)}{points.map((p,i)=><circle key={i} cx={x(p.ipp_lon)} cy={y(p.ipp_lat)} r="4" fill={color(p.dtec)} onMouseEnter={()=>setTip(`${p.station} ${p.prn} dTEC=${p.dtec} time=${p.time_h}h elev=${p.elevation} lon=${p.ipp_lon} lat=${p.ipp_lat}`)} />)}</svg><p className="tooltip">{tip || 'Hover a point for details.'}</p></>; }
createRoot(document.getElementById('root')!).render(<App />);

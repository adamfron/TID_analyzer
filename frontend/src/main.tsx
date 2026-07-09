import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type Status = { stage: string; current: number; total: number; message: string };
type Manifest = {
  station_count: number;
  gps_prns: string[];
  time_range_hours: { min: number | null; max: number | null };
  row_counts_by_station: Record<string, number>;
  row_counts_by_prn: Record<string, number>;
  malformed_row_count: number;
  applied_filters: Record<string, string | number>;
};

const API = 'http://127.0.0.1:8000';

function App() {
  const [folderPath, setFolderPath] = useState('');
  const [status, setStatus] = useState<Status>({ stage: 'idle', current: 0, total: 0, message: 'Idle' });
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [activeTab, setActiveTab] = useState('Data summary');
  const progress = useMemo(() => (status.total > 0 ? Math.round((status.current / status.total) * 100) : 0), [status]);

  useEffect(() => {
    const ws = new WebSocket('ws://127.0.0.1:8000/ws/import-progress');
    ws.onmessage = (event) => {
      const update = JSON.parse(event.data) as Status;
      setStatus(update);
      if (update.stage === 'done') fetch(`${API}/api/manifest`).then((r) => r.json()).then(setManifest);
    };
    return () => ws.close();
  }, []);

  async function startImport() {
    setManifest(null);
    const response = await fetch(`${API}/api/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_path: folderPath }),
    });
    if (!response.ok) setStatus({ stage: 'error', current: 0, total: 0, message: await response.text() });
  }

  return <div className="app">
    <header className="topbar">
      <input value={folderPath} onChange={(e) => setFolderPath(e.target.value)} placeholder="Local daily folder path" />
      <button onClick={startImport}>Import</button>
      <progress value={progress} max="100" />
      <span>{status.message}</span>
    </header>
    <main className="workspace">
      <section className="canvas"><h1>GNSS TID Analyzer</h1><p>Map and time-series workspace placeholder.</p></section>
      <aside className="panel">
        <nav>{['Data summary','Map explorer','TID candidates','Event analysis'].map((tab) => <button className={activeTab === tab ? 'active' : ''} onClick={() => setActiveTab(tab)} key={tab}>{tab}</button>)}</nav>
        {activeTab === 'Data summary' && <Summary manifest={manifest} />}
        {activeTab !== 'Data summary' && <p className="placeholder">{activeTab} will be implemented in later stages.</p>}
      </aside>
    </main>
    <footer>Scientific sources · Citation format · DOI placeholder</footer>
  </div>;
}

function Summary({ manifest }: { manifest: Manifest | null }) {
  if (!manifest) return <p className="placeholder">Import a folder to view metadata.</p>;
  return <div className="summary">
    <p><b>Stations:</b> {manifest.station_count}</p>
    <p><b>GPS PRNs:</b> {manifest.gps_prns.join(', ') || 'None'}</p>
    <p><b>Time range:</b> {manifest.time_range_hours.min}–{manifest.time_range_hours.max} h</p>
    <p><b>Malformed rows:</b> {manifest.malformed_row_count}</p>
    <h3>Rows by station</h3><pre>{JSON.stringify(manifest.row_counts_by_station, null, 2)}</pre>
    <h3>Rows by PRN</h3><pre>{JSON.stringify(manifest.row_counts_by_prn, null, 2)}</pre>
    <h3>Filters</h3><pre>{JSON.stringify(manifest.applied_filters, null, 2)}</pre>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);

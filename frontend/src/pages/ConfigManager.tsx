import { useState, useEffect } from 'react';
import { Edit3, Activity, RefreshCw, X, Check, AlertTriangle } from 'lucide-react';

interface ConfigSummary {
  name: string;
  environment: string;
  latest_version: number;
  stable_version: number;
  stable_target: string;
  updated_at: string;
}

interface VersionHistory {
  version: number;
  created_at: string;
  created_by: string;
  description: string | null;
  is_latest: boolean;
}

const API = 'http://localhost:8080';
const HEADERS = { 'X-User-Id': 'demo-ui', 'X-Role': 'admin', 'Content-Type': 'application/json' };

export default function ConfigManager() {
  const [configs, setConfigs] = useState<ConfigSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Edit Modal state
  const [editingConfig, setEditingConfig] = useState<ConfigSummary | null>(null);
  const [editValue, setEditValue] = useState<string>('');
  const [editEnv, setEditEnv] = useState<string>('prod');
  const [isSaving, setIsSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Version history modal state
  const [historyConfig, setHistoryConfig] = useState<ConfigSummary | null>(null);
  const [history, setHistory] = useState<VersionHistory[]>([]);

  const fetchConfigs = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/configs`, { headers: HEADERS });
      if (!res.ok) throw new Error(`API Error: ${res.status} ${res.statusText}`);
      const data = await res.json();
      setConfigs(data);
    } catch (e: any) {
      setError(e.message || 'Could not connect to the backend. Is it running?');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchConfigs(); }, []);

  // -- EDIT MODAL --
  const openEditModal = async (config: ConfigSummary) => {
    setEditingConfig(config);
    setEditEnv(config.environment);
    setSaveMsg(null);
    setEditValue('Loading current config...');
    try {
      const res = await fetch(
        `${API}/configs/${config.name}?environment=${config.environment}&target=${config.stable_target}`,
        { headers: HEADERS }
      );
      if (!res.ok) throw new Error('Failed to load current config');
      const data = await res.json();
      setEditValue(JSON.stringify(data.value, null, 2));
    } catch (e: any) {
      setEditValue(`{\n  "error": "${e.message}"\n}`);
    }
  };

  const handleSave = async () => {
    if (!editingConfig) return;
    setIsSaving(true);
    setSaveMsg(null);
    try {
      const parsedValue = JSON.parse(editValue); // will throw if invalid JSON

      // Step 1: Create a new immutable version
      const createRes = await fetch(`${API}/configs`, {
        method: 'POST',
        headers: HEADERS,
        body: JSON.stringify({
          name: editingConfig.name,
          environment: editEnv,
          value: parsedValue,
          description: 'Updated via Config Control Plane UI',
        }),
      });
      if (!createRes.ok) {
        const err = await createRes.json();
        throw new Error(err.detail || 'Failed to create new version');
      }

      // Step 2: Roll it out to 100% for the target
      const rolloutRes = await fetch(`${API}/configs/${editingConfig.name}/rollout`, {
        method: 'POST',
        headers: HEADERS,
        body: JSON.stringify({
          target: editingConfig.stable_target,
          environment: editEnv,
          percent: 100,
        }),
      });
      if (!rolloutRes.ok) {
        const err = await rolloutRes.json();
        throw new Error(err.detail || 'Failed to start rollout');
      }

      setSaveMsg({ type: 'success', text: 'New version created and rolled out to 100%!' });
      setTimeout(() => {
        setEditingConfig(null);
        setSaveMsg(null);
        fetchConfigs();
      }, 1800);
    } catch (e: any) {
      setSaveMsg({ type: 'error', text: e.message || 'Invalid JSON or API error.' });
    } finally {
      setIsSaving(false);
    }
  };

  // -- SIMULATE ERROR --
  const simulateError = async (config: ConfigSummary) => {
    try {
      const res = await fetch(`${API}/simulation/metrics`, {
        method: 'POST',
        headers: HEADERS,
        body: JSON.stringify({ target: config.stable_target, metric: 'error_rate', value: 1.0 }),
      });
      if (!res.ok) throw new Error('API rejected simulate request');
      alert(`✅ Injected a 100% error rate for target "${config.stable_target}".\nThe Canary Monitor will detect this and auto-rollback any active canary rollout!`);
    } catch (e: any) {
      alert(`❌ Simulate Error failed: ${e.message}`);
    }
  };

  // -- VERSION HISTORY --
  const openHistory = async (config: ConfigSummary) => {
    setHistoryConfig(config);
    setHistory([]);
    try {
      const res = await fetch(
        `${API}/configs/${config.name}/versions?environment=${config.environment}`,
        { headers: HEADERS }
      );
      if (!res.ok) throw new Error('Failed to load version history');
      setHistory(await res.json());
    } catch (e) {
      setHistory([]);
    }
  };

  return (
    <div className="animate-fade-in" style={{ position: 'relative' }}>
      <div className="page-header">
        <h1 className="page-title">Configuration Manager</h1>
        <button className="btn btn-primary" onClick={fetchConfigs}>
          <RefreshCw size={18} /> Refresh
        </button>
      </div>

      {error && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.75rem',
          background: 'rgba(239,68,68,0.1)', border: '1px solid var(--danger)',
          borderRadius: '0.5rem', padding: '1rem', marginBottom: '1.5rem', color: 'var(--danger)'
        }}>
          <AlertTriangle size={20} />
          <span>{error}</span>
        </div>
      )}

      <div className="glass-card">
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Environment</th>
                <th>Target</th>
                <th>Stable Version</th>
                <th>Latest Version</th>
                <th>Updated</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-secondary)' }}>Loading configurations...</td></tr>
              ) : configs.length === 0 ? (
                <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-secondary)' }}>No configs found. Run <code>make seed-demo</code> to populate.</td></tr>
              ) : (
                configs.map((config) => (
                  <tr key={`${config.name}-${config.environment}`}>
                    <td style={{ fontWeight: 600 }}>{config.name}</td>
                    <td><span className="badge badge-neutral">{config.environment}</span></td>
                    <td style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>{config.stable_target}</td>
                    <td><span className="badge badge-success">v{config.stable_version}</span></td>
                    <td><span className="badge badge-neutral">v{config.latest_version}</span></td>
                    <td style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                      {new Date(config.updated_at).toLocaleString()}
                    </td>
                    <td style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                      <button className="btn btn-secondary" style={{ padding: '0.35rem 0.7rem', fontSize: '0.8rem' }} onClick={() => openHistory(config)}>
                        History
                      </button>
                      <button className="btn btn-secondary" style={{ padding: '0.35rem 0.7rem', fontSize: '0.8rem' }} onClick={() => simulateError(config)}>
                        <Activity size={13} /> Simulate Error
                      </button>
                      <button className="btn btn-primary" style={{ padding: '0.35rem 0.7rem', fontSize: '0.8rem' }} onClick={() => openEditModal(config)}>
                        <Edit3 size={13} /> Edit
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ─── EDIT MODAL ─── */}
      {editingConfig && (
        <div style={{
          position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.7)',
          backdropFilter: 'blur(6px)', display: 'flex', alignItems: 'center',
          justifyContent: 'center', zIndex: 200
        }}>
          <div className="glass-card animate-fade-in" style={{ width: '640px', maxWidth: '95vw' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
              <div>
                <h2 style={{ fontSize: '1.1rem', fontWeight: 700 }}>Edit Config</h2>
                <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>{editingConfig.name}</p>
              </div>
              <button style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }} onClick={() => setEditingConfig(null)}>
                <X size={22} />
              </button>
            </div>

            <div className="form-group">
              <label className="form-label">Environment</label>
              <select className="form-input" value={editEnv} onChange={(e) => setEditEnv(e.target.value)}>
                <option value="prod">prod</option>
                <option value="staging">staging</option>
                <option value="dev">dev</option>
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Configuration JSON Value</label>
              <textarea
                className="form-input"
                style={{ fontFamily: 'monospace', fontSize: '0.9rem', height: '220px' }}
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
              />
            </div>

            {saveMsg && (
              <div style={{
                padding: '0.75rem 1rem', borderRadius: '0.5rem', marginBottom: '1rem',
                display: 'flex', alignItems: 'center', gap: '0.5rem',
                background: saveMsg.type === 'success' ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
                color: saveMsg.type === 'success' ? 'var(--success)' : 'var(--danger)',
                border: `1px solid ${saveMsg.type === 'success' ? 'var(--success)' : 'var(--danger)'}`,
                fontSize: '0.9rem'
              }}>
                {saveMsg.type === 'success' ? <Check size={16} /> : <AlertTriangle size={16} />}
                {saveMsg.text}
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.75rem' }}>
              <button className="btn btn-secondary" onClick={() => setEditingConfig(null)} disabled={isSaving}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSave} disabled={isSaving}>
                {isSaving ? 'Deploying...' : 'Save & Deploy to 100%'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ─── VERSION HISTORY MODAL ─── */}
      {historyConfig && (
        <div style={{
          position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.7)',
          backdropFilter: 'blur(6px)', display: 'flex', alignItems: 'center',
          justifyContent: 'center', zIndex: 200
        }}>
          <div className="glass-card animate-fade-in" style={{ width: '640px', maxWidth: '95vw' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
              <div>
                <h2 style={{ fontSize: '1.1rem', fontWeight: 700 }}>Version History</h2>
                <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>{historyConfig.name}</p>
              </div>
              <button style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }} onClick={() => setHistoryConfig(null)}>
                <X size={22} />
              </button>
            </div>
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Version</th>
                    <th>Description</th>
                    <th>Created By</th>
                    <th>Created At</th>
                    <th>Tag</th>
                  </tr>
                </thead>
                <tbody>
                  {history.length === 0 ? (
                    <tr><td colSpan={5} style={{ textAlign: 'center' }}>Loading...</td></tr>
                  ) : history.map((v) => (
                    <tr key={v.version}>
                      <td><span className="badge badge-neutral">v{v.version}</span></td>
                      <td style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>{v.description || '—'}</td>
                      <td style={{ fontSize: '0.85rem' }}>{v.created_by}</td>
                      <td style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>{new Date(v.created_at).toLocaleString()}</td>
                      <td>{v.is_latest ? <span className="badge badge-success">latest</span> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '1.5rem' }}>
              <button className="btn btn-secondary" onClick={() => setHistoryConfig(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

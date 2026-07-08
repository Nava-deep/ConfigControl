import { useState, useEffect } from 'react';
import { Edit3, Activity, RefreshCw, X, Check } from 'lucide-react';

interface ConfigSummary {
  name: string;
  active_rollout_id: string | null;
  stable_version: string | null;
  target: string;
}

export default function ConfigManager() {
  const [configs, setConfigs] = useState<ConfigSummary[]>([]);
  const [loading, setLoading] = useState(true);
  
  // Modal State
  const [editingConfig, setEditingConfig] = useState<ConfigSummary | null>(null);
  const [editValue, setEditValue] = useState<string>('');
  const [isSaving, setIsSaving] = useState(false);
  const [message, setMessage] = useState<{type: 'success' | 'error', text: string} | null>(null);

  const headers = { 'X-User-Id': 'demo-ui', 'X-Role': 'admin', 'Content-Type': 'application/json' };

  const fetchConfigs = async () => {
    try {
      const res = await fetch('http://localhost:8080/configs', { headers });
      const data = await res.json();
      setConfigs(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConfigs();
  }, []);

  const openEditModal = async (config: ConfigSummary) => {
    setEditingConfig(config);
    setEditValue('Loading...');
    try {
      const res = await fetch(`http://localhost:8080/configs/${config.name}`, { headers });
      const data = await res.json();
      setEditValue(JSON.stringify(data.value, null, 2));
    } catch (e) {
      setEditValue('{\n  "error": "Failed to load config"\n}');
    }
  };

  const handleSave = async () => {
    if (!editingConfig) return;
    setIsSaving(true);
    setMessage(null);
    try {
      const parsedValue = JSON.parse(editValue);
      
      // 1. Create new version
      const createRes = await fetch('http://localhost:8080/configs', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          name: editingConfig.name,
          value: parsedValue,
          description: "Updated via UI"
        })
      });
      
      if (!createRes.ok) throw new Error("Failed to save new version. Ensure JSON matches schema.");

      // 2. Rollout to 100%
      const rolloutRes = await fetch(`http://localhost:8080/configs/${editingConfig.name}/rollout`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          target: editingConfig.target,
          percent: 100
        })
      });
      
      if (!rolloutRes.ok) throw new Error("Failed to start rollout.");

      setMessage({ type: 'success', text: 'Configuration successfully updated and rolled out!' });
      setTimeout(() => {
        setEditingConfig(null);
        setMessage(null);
        fetchConfigs();
      }, 1500);

    } catch (e: any) {
      setMessage({ type: 'error', text: e.message || 'Invalid JSON format.' });
    } finally {
      setIsSaving(false);
    }
  };

  const simulateError = async (config: ConfigSummary) => {
    try {
      await fetch('http://localhost:8080/simulation/metrics', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          target: config.target,
          metric: 'error_rate',
          value: 1.0
        })
      });
      alert(`Simulated error rate spike for target: ${config.target}. Check backend logs or Canary Monitor!`);
    } catch (e) {
      alert("Failed to simulate error.");
    }
  };

  return (
    <div className="animate-fade-in" style={{ position: 'relative' }}>
      <div className="page-header">
        <h1 className="page-title">Configuration Manager</h1>
        <button className="btn btn-primary" onClick={fetchConfigs}>
          <RefreshCw size={18} />
          Refresh
        </button>
      </div>

      <div className="glass-card">
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Target</th>
                <th>Stable Version</th>
                <th>Active Rollout</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={5} style={{ textAlign: 'center' }}>Loading...</td>
                </tr>
              ) : configs.length === 0 ? (
                <tr>
                  <td colSpan={5} style={{ textAlign: 'center' }}>No configurations found. Run 'make seed-demo' to populate.</td>
                </tr>
              ) : (
                configs.map((config) => (
                  <tr key={config.name}>
                    <td style={{ fontWeight: 600 }}>{config.name}</td>
                    <td>{config.target}</td>
                    <td>
                      <span className="badge badge-success">v{config.stable_version || 'None'}</span>
                    </td>
                    <td>
                      {config.active_rollout_id ? (
                        <span className="badge badge-warning">Active Rollout</span>
                      ) : (
                        <span className="badge badge-neutral">None</span>
                      )}
                    </td>
                    <td>
                      <button className="btn btn-secondary" style={{ padding: '0.4rem 0.8rem', marginRight: '0.5rem' }} onClick={() => simulateError(config)}>
                        <Activity size={14} /> Simulate Error
                      </button>
                      <button className="btn btn-primary" style={{ padding: '0.4rem 0.8rem' }} onClick={() => openEditModal(config)}>
                        <Edit3 size={14} /> Edit Config
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Edit Modal overlay */}
      {editingConfig && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100
        }}>
          <div className="glass-card animate-fade-in" style={{ width: '600px', maxWidth: '90vw' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
              <h2 style={{ fontSize: '1.2rem', fontWeight: 600 }}>Edit {editingConfig.name}</h2>
              <button style={{ background: 'none', border: 'none', color: 'white', cursor: 'pointer' }} onClick={() => setEditingConfig(null)}>
                <X size={20} />
              </button>
            </div>
            
            <div className="form-group">
              <label className="form-label">Configuration Value (JSON)</label>
              <textarea 
                className="form-input" 
                style={{ fontFamily: 'monospace', height: '200px' }}
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
              />
            </div>

            {message && (
              <div style={{ 
                padding: '0.75rem', borderRadius: '0.5rem', marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem',
                backgroundColor: message.type === 'success' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                color: message.type === 'success' ? 'var(--success)' : 'var(--danger)',
                border: `1px solid ${message.type === 'success' ? 'var(--success)' : 'var(--danger)'}`
              }}>
                {message.type === 'success' ? <Check size={16} /> : <X size={16} />}
                {message.text}
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '1rem', marginTop: '1.5rem' }}>
              <button className="btn btn-secondary" onClick={() => setEditingConfig(null)} disabled={isSaving}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSave} disabled={isSaving}>
                {isSaving ? 'Saving...' : 'Save & Deploy'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

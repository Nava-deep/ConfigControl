import { useState, useEffect } from 'react';
import { Plus, Trash2, Activity, RefreshCw } from 'lucide-react';

interface ConfigSummary {
  name: string;
  active_rollout_id: string | null;
  stable_version: string | null;
  target: string;
}

export default function ConfigManager() {
  const [configs, setConfigs] = useState<ConfigSummary[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchConfigs = async () => {
    try {
      const res = await fetch('http://localhost:8080/configs', {
        headers: {
          'X-User-Id': 'demo-ui',
          'X-Role': 'admin'
        }
      });
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

  return (
    <div className="animate-fade-in">
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
                      <button className="btn btn-secondary" style={{ padding: '0.4rem 0.8rem', marginRight: '0.5rem' }}>
                        <Activity size={14} /> Simulate Error
                      </button>
                      <button className="btn btn-danger" style={{ padding: '0.4rem 0.8rem' }}>
                        <Trash2 size={14} /> Delete
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

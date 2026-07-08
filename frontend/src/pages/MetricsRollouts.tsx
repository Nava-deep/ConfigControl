import { useState, useEffect } from 'react';
import { RefreshCw, TrendingUp, AlertTriangle, RotateCcw, ChevronRight } from 'lucide-react';

const API = 'http://localhost:8080';
const HEADERS = { 'X-User-Id': 'demo-ui', 'X-Role': 'admin', 'Content-Type': 'application/json' };

interface AuditEntry {
  action: string;
  config_name: string;
  environment: string;
  actor: string;
  timestamp: string;
  detail: string | null;
}

interface FailureTelemetry {
  config_name: string;
  environment: string;
  target: string;
  error_type: string;
  source: string;
  config_version: number | null;
  created_at: string;
}

const actionColor: Record<string, string> = {
  create:   'var(--accent-primary)',
  rollout:  'var(--warning)',
  promote:  'var(--success)',
  rollback: 'var(--danger)',
  default:  'var(--text-secondary)',
};

export default function MetricsRollouts() {
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [failures, setFailures] = useState<FailureTelemetry[]>([]);
  const [loadingAudit, setLoadingAudit] = useState(true);
  const [loadingFail, setLoadingFail] = useState(true);

  // Canary simulation state
  const [simTarget, setSimTarget] = useState('checkout');
  const [simMetric, setSimMetric] = useState('error_rate');
  const [simValue, setSimValue] = useState('0.05');
  const [simMsg, setSimMsg] = useState<string | null>(null);

  const fetchAll = async () => {
    setLoadingAudit(true);
    setLoadingFail(true);

    fetch(`${API}/audit`, { headers: HEADERS })
      .then(r => r.json()).then(setAudit).catch(() => setAudit([]))
      .finally(() => setLoadingAudit(false));

    fetch(`${API}/telemetry/failures?limit=20`, { headers: HEADERS })
      .then(r => r.json()).then(setFailures).catch(() => setFailures([]))
      .finally(() => setLoadingFail(false));
  };

  useEffect(() => { fetchAll(); }, []);

  const sendMetric = async () => {
    setSimMsg(null);
    try {
      const res = await fetch(`${API}/simulation/metrics`, {
        method: 'POST', headers: HEADERS,
        body: JSON.stringify({ target: simTarget, metric: simMetric, value: parseFloat(simValue) }),
      });
      if (!res.ok) throw new Error('API rejected');
      setSimMsg(`✅ Injected ${simMetric}=${simValue} for target "${simTarget}". Canary Monitor will evaluate shortly.`);
      setTimeout(() => setSimMsg(null), 4000);
    } catch {
      setSimMsg('❌ Failed to inject metric. Is the backend running?');
    }
  };

  const resetMetric = async () => {
    setSimMsg(null);
    try {
      await fetch(`${API}/simulation/metrics`, {
        method: 'POST', headers: HEADERS,
        body: JSON.stringify({ target: simTarget, metric: simMetric, value: 0.0 }),
      });
      setSimMsg(`✅ Reset ${simMetric} to 0.0 for target "${simTarget}".`);
      setTimeout(() => setSimMsg(null), 3000);
    } catch {
      setSimMsg('❌ Failed to reset metric.');
    }
  };

  return (
    <div className="animate-fade-in">
      <div className="page-header">
        <h1 className="page-title">Metrics & Rollouts</h1>
        <button className="btn btn-primary" onClick={fetchAll}>
          <RefreshCw size={18} /> Refresh
        </button>
      </div>

      {/* ─── Canary Simulation Control ─── */}
      <div className="glass-card" style={{ marginBottom: '2rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
          <TrendingUp size={20} color="var(--warning)" />
          <h2 style={{ fontSize: '1rem', fontWeight: 700 }}>Canary Health Simulator</h2>
        </div>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginBottom: '1.25rem' }}>
          Inject a synthetic metric into a target's signal. The background Canary Monitor checks these signals every 0.5s.
          If a value breaches the canary threshold on a rollout, it will automatically rollback.
        </p>
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div className="form-group" style={{ marginBottom: 0, flex: '1 1 150px' }}>
            <label className="form-label">Target</label>
            <input className="form-input" value={simTarget} onChange={e => setSimTarget(e.target.value)} placeholder="e.g. checkout" />
          </div>
          <div className="form-group" style={{ marginBottom: 0, flex: '1 1 150px' }}>
            <label className="form-label">Metric</label>
            <input className="form-input" value={simMetric} onChange={e => setSimMetric(e.target.value)} placeholder="e.g. error_rate" />
          </div>
          <div className="form-group" style={{ marginBottom: 0, flex: '1 1 100px' }}>
            <label className="form-label">Value (0–1)</label>
            <input className="form-input" type="number" step="0.01" min="0" max="1" value={simValue} onChange={e => setSimValue(e.target.value)} />
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="btn btn-primary" onClick={sendMetric}>
              <TrendingUp size={16} /> Inject
            </button>
            <button className="btn btn-danger" onClick={resetMetric}>
              <RotateCcw size={16} /> Reset to 0
            </button>
          </div>
        </div>
        {simMsg && (
          <div style={{ marginTop: '1rem', padding: '0.75rem', background: 'rgba(255,255,255,0.05)', borderRadius: '0.5rem', color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
            {simMsg}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2">
        {/* ─── Audit Log ─── */}
        <div className="glass-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
            <ChevronRight size={18} color="var(--accent-primary)" />
            <h2 style={{ fontSize: '1rem', fontWeight: 700 }}>Audit Log</h2>
          </div>
          {loadingAudit ? (
            <p style={{ color: 'var(--text-secondary)' }}>Loading...</p>
          ) : audit.length === 0 ? (
            <p style={{ color: 'var(--text-secondary)' }}>No audit events yet.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', maxHeight: '420px', overflowY: 'auto' }}>
              {audit.slice(0, 30).map((entry, i) => (
                <div key={i} style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start', padding: '0.75rem', background: 'rgba(0,0,0,0.2)', borderRadius: '0.5rem', borderLeft: `3px solid ${actionColor[entry.action] || actionColor.default}` }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                      <span style={{ fontWeight: 600, fontSize: '0.875rem', color: actionColor[entry.action] || actionColor.default, textTransform: 'uppercase' }}>{entry.action}</span>
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{new Date(entry.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <div style={{ fontSize: '0.85rem', fontWeight: 500 }}>{entry.config_name}</div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>by {entry.actor} · {entry.environment}</div>
                    {entry.detail && <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>{entry.detail}</div>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ─── Failure Telemetry ─── */}
        <div className="glass-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1.25rem' }}>
            <AlertTriangle size={18} color="var(--danger)" />
            <h2 style={{ fontSize: '1rem', fontWeight: 700 }}>Failure Telemetry</h2>
          </div>
          {loadingFail ? (
            <p style={{ color: 'var(--text-secondary)' }}>Loading...</p>
          ) : failures.length === 0 ? (
            <p style={{ color: 'var(--text-secondary)' }}>No failures reported yet. Use the SDK <code>report_failure()</code> method or run the demo client.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', maxHeight: '420px', overflowY: 'auto' }}>
              {failures.map((f, i) => (
                <div key={i} style={{ padding: '0.75rem', background: 'rgba(239,68,68,0.06)', borderRadius: '0.5rem', borderLeft: '3px solid var(--danger)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                    <span style={{ fontWeight: 600, fontSize: '0.875rem', color: 'var(--danger)' }}>{f.error_type}</span>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{new Date(f.created_at).toLocaleTimeString()}</span>
                  </div>
                  <div style={{ fontSize: '0.85rem' }}>{f.config_name}</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>target: {f.target} · source: {f.source} · {f.environment}</div>
                  {f.config_version != null && <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>on config version v{f.config_version}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

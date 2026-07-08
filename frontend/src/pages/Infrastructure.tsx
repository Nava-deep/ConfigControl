import { useState, useEffect } from 'react';
import { Server, Database, Cpu, RefreshCw, CheckCircle2, XCircle, Wifi } from 'lucide-react';

const API = 'http://localhost:8080';
const HEADERS = { 'X-User-Id': 'demo-ui', 'X-Role': 'admin' };

interface HealthStatus {
  status: string;
  database: boolean;
  redis: boolean | string;
}

export default function Infrastructure() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);

  const checkHealth = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/health/ready`, { headers: HEADERS });
      const data = await res.json();
      setHealth(data);
    } catch {
      setHealth({ status: 'unreachable', database: false, redis: false });
    } finally {
      setLoading(false);
      setLastChecked(new Date());
    }
  };

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 10000); // auto-refresh every 10s
    return () => clearInterval(interval);
  }, []);

  const StatusIcon = ({ ok }: { ok: boolean }) =>
    ok
      ? <CheckCircle2 size={20} color="var(--success)" />
      : <XCircle size={20} color="var(--danger)" />;

  const StatusBadge = ({ ok, label }: { ok: boolean; label: string }) => (
    <span className={`badge ${ok ? 'badge-success' : 'badge-danger'}`}
      style={{ background: ok ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', color: ok ? 'var(--success)' : 'var(--danger)', border: `1px solid ${ok ? 'var(--success)' : 'var(--danger)'}` }}>
      {label}
    </span>
  );

  const isRedisOk = health?.redis === true || health?.redis === 'available';

  const services = [
    {
      label: 'FastAPI API',
      icon: <Server size={28} color="var(--accent-primary)" />,
      ok: health?.status === 'ready',
      description: 'REST + WebSocket server running on port 8080',
      detail: health?.status === 'ready' ? 'Online – serving requests' : 'Unreachable',
      url: 'http://localhost:8080/docs',
      urlLabel: 'Open API Docs ↗',
    },
    {
      label: 'PostgreSQL',
      icon: <Database size={28} color="#60a5fa" />,
      ok: !!health?.database,
      description: 'Immutable config storage + audit log. Persistent volume-backed.',
      detail: health?.database ? 'Connected – ping successful' : 'Cannot reach database',
      url: null,
    },
    {
      label: 'Redis',
      icon: <Cpu size={28} color="#f87171" />,
      ok: !!isRedisOk,
      description: 'Pub/Sub event fanout + in-memory cache. Used for cross-instance config updates.',
      detail: isRedisOk ? 'Connected – pub/sub active' : 'Unavailable – SDK will use last-known-good fallback',
      url: null,
    },
    {
      label: 'Prometheus',
      icon: <Wifi size={28} color="var(--warning)" />,
      ok: true,
      description: 'Scraping metrics from /metrics endpoint every 15s.',
      detail: 'Available on port 9090',
      url: 'http://localhost:9090',
      urlLabel: 'Open Prometheus ↗',
    },
  ];

  return (
    <div className="animate-fade-in">
      <div className="page-header">
        <h1 className="page-title">Infrastructure</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          {lastChecked && (
            <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
              Last checked: {lastChecked.toLocaleTimeString()}
            </span>
          )}
          <button className="btn btn-primary" onClick={checkHealth} disabled={loading}>
            <RefreshCw size={18} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} />
            {loading ? 'Checking...' : 'Check Now'}
          </button>
        </div>
      </div>

      {/* Overall Status Banner */}
      {health && (
        <div style={{
          padding: '1rem 1.5rem', borderRadius: '0.75rem', marginBottom: '2rem',
          background: health.status === 'ready' ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)',
          border: `1px solid ${health.status === 'ready' ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)'}`,
          display: 'flex', alignItems: 'center', gap: '1rem'
        }}>
          <StatusIcon ok={health.status === 'ready'} />
          <div>
            <div style={{ fontWeight: 700, fontSize: '1rem', color: health.status === 'ready' ? 'var(--success)' : 'var(--danger)' }}>
              System is {health.status === 'ready' ? 'Fully Operational' : health.status.toUpperCase()}
            </div>
            <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '0.15rem' }}>
              {health.status === 'ready'
                ? 'All critical services are healthy and accepting traffic.'
                : 'One or more services are degraded. Check service status below.'}
            </div>
          </div>
        </div>
      )}

      {/* Service Cards */}
      <div className="grid grid-cols-2">
        {services.map((svc) => (
          <div key={svc.label} className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                {svc.icon}
                <div>
                  <div style={{ fontWeight: 700, fontSize: '1rem' }}>{svc.label}</div>
                  <StatusBadge ok={svc.ok} label={svc.ok ? 'Online' : 'Offline'} />
                </div>
              </div>
              <StatusIcon ok={svc.ok} />
            </div>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', lineHeight: 1.6 }}>{svc.description}</p>
            <div style={{ padding: '0.6rem 0.9rem', background: 'rgba(0,0,0,0.25)', borderRadius: '0.4rem', fontSize: '0.82rem', color: svc.ok ? 'var(--success)' : 'var(--danger)', fontFamily: 'monospace' }}>
              {svc.detail}
            </div>
            {svc.url && (
              <a href={svc.url} target="_blank" rel="noreferrer" style={{ fontSize: '0.85rem', color: 'var(--accent-primary)', alignSelf: 'flex-start' }}>
                {svc.urlLabel}
              </a>
            )}
          </div>
        ))}
      </div>

      {/* Architecture note */}
      <div className="glass-card" style={{ marginTop: '2rem' }}>
        <h2 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '1rem' }}>How it's wired together</h2>
        <div style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', lineHeight: 2 }}>
          <div>① <strong style={{ color: 'var(--text-primary)' }}>Operator / UI</strong> → pushes config via <code>POST /configs</code></div>
          <div>② <strong style={{ color: 'var(--text-primary)' }}>FastAPI</strong> → validates against JSON Schema → writes new immutable version to <strong style={{ color: '#60a5fa' }}>PostgreSQL</strong></div>
          <div>③ <strong style={{ color: 'var(--text-primary)' }}>FastAPI</strong> → publishes change event to <strong style={{ color: '#f87171' }}>Redis</strong> Pub/Sub channel</div>
          <div>④ <strong style={{ color: '#f87171' }}>Redis</strong> → fans out event to all API instances via the <code>RedisEventBridge</code></div>
          <div>⑤ Each <strong style={{ color: 'var(--text-primary)' }}>API instance</strong> → pushes the update down to its connected WebSocket clients</div>
          <div>⑥ <strong style={{ color: 'var(--text-primary)' }}>CanaryMonitor</strong> (background) → polls every 0.5s for rollout health → auto-promotes or auto-rollbacks</div>
        </div>
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

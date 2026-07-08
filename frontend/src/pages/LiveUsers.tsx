import { useState, useEffect, useMemo } from 'react';
import { Server, Activity, ShieldAlert, CheckCircle2, Play, FastForward, Check } from 'lucide-react';

interface ConfigState {
  version: number;
  value: any;
}

interface ClientState {
  id: string;
  ws: WebSocket | null;
  config: ConfigState | null;
  connected: boolean;
}

const API = 'http://localhost:8080';
const ENV = 'prod';

export default function LiveUsers() {
  const [targetCluster, setTargetCluster] = useState('checkout');
  const [availableConfigs, setAvailableConfigs] = useState<any[]>([]);
  const [selectedConfig, setSelectedConfig] = useState<string>('');
  const [clients, setClients] = useState<ClientState[]>([]);
  
  // Status of selected config
  const [stableVersion, setStableVersion] = useState<number>(0);
  const [latestVersion, setLatestVersion] = useState<number>(0);
  const [activeRollout, setActiveRollout] = useState<any>(null);

  // 1. Fetch available configs when target changes
  useEffect(() => {
    fetch(`${API}/configs?environment=${ENV}`, {
      headers: { 'X-User-Id': 'demo-ui', 'X-Role': 'reader' }
    })
      .then(res => res.json())
      .then(data => {
        const targetConfigs = data.filter((c: any) => c.stable_target === targetCluster || c.name.includes(targetCluster));
        setAvailableConfigs(targetConfigs);
        if (targetConfigs.length > 0) {
          setSelectedConfig(targetConfigs[0].name);
        } else {
          setSelectedConfig('');
        }
      });
  }, [targetCluster]);

  // 2. Fetch rollout/version info for selected config
  const refreshConfigStatus = async () => {
    if (!selectedConfig) return;
    try {
      const listRes = await fetch(`${API}/configs?environment=${ENV}`);
      const listData = await listRes.json();
      const summary = listData.find((c: any) => c.name === selectedConfig);
      if (summary) {
        setStableVersion(summary.stable_version);
        setLatestVersion(summary.latest_version);
      }
      
      // We can also fetch the versions history to see if there's an active rollout... 
      // but simpler: if latest > stable, there might be a rollout needed.
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    refreshConfigStatus();
    // eslint-disable-next-line
  }, [selectedConfig]);

  // 3. Connect Websockets and fetch data for the 100 clients
  useEffect(() => {
    if (!selectedConfig) return;

    // Generate 100 distinct client IDs
    const newClients = Array.from({ length: 100 }).map((_, i) => ({
      id: `${targetCluster}-node-${i + 1}`,
      ws: null,
      config: null,
      connected: false
    }));

    setClients(newClients);

    newClients.forEach((client, idx) => {
      // Connect to WebSocket listening to all changes for this target
      const ws = new WebSocket(`ws://localhost:8080/watch/ws?target=${targetCluster}&environment=${ENV}`);
      
      const fetchConfigState = async () => {
        try {
          // Stagger the initial fetch slightly to avoid overwhelming local dev server
          await new Promise(r => setTimeout(r, Math.random() * 500));
          
          const res = await fetch(`${API}/configs/${selectedConfig}?target=${targetCluster}&environment=${ENV}&client_id=${client.id}`, {
            headers: { 'X-User-Id': 'demo-ui', 'X-Role': 'reader' }
          });
          const data = await res.json();
          setClients(prev => prev.map(c => {
            if (c.id === client.id) {
              return { ...c, config: { version: data.version, value: data.value } };
            }
            return c;
          }));
        } catch (e) {
          // silent
        }
      };

      ws.onopen = async () => {
        setClients(prev => prev.map(c => c.id === client.id ? { ...c, connected: true } : c));
        fetchConfigState();
      };

      ws.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.event !== 'connected' && payload.config_name === selectedConfig) {
          fetchConfigState();
          refreshConfigStatus(); // Refresh control panel status
        }
      };

      ws.onclose = () => {
        setClients(prev => prev.map(c => c.id === client.id ? { ...c, connected: false } : c));
      };

      client.ws = ws;
    });

    return () => {
      newClients.forEach(c => {
        if (c.ws) {
          c.ws.close();
        }
      });
    };
  }, [selectedConfig, targetCluster]);

  const triggerRollout = async (percent: number) => {
    if (!selectedConfig) return;
    
    try {
      // 1. If stable === latest, we need to create a candidate version first!
      if (stableVersion === latestVersion) {
        const getRes = await fetch(`${API}/configs/${selectedConfig}?environment=${ENV}`, {
          headers: { 'X-User-Id': 'admin', 'X-Role': 'admin' }
        });
        const currentData = await getRes.json();
        
        const newValue = { ...currentData.value, _canary_timestamp: Date.now() };
        
        const createRes = await fetch(`${API}/configs`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-User-Id': 'admin', 'X-Role': 'admin' },
          body: JSON.stringify({
            name: selectedConfig,
            environment: ENV,
            value: newValue,
            description: 'Auto-generated candidate for Canary Demo',
          })
        });
        
        if (!createRes.ok) {
           const err = await createRes.json();
           alert(`Failed to create candidate: ${err.detail}`);
           return;
        }
      }

      // 2. If we already have an active rollout session in memory, advance or promote it
      if (activeRollout) {
        if (percent === 100) {
          await fetch(`${API}/configs/${selectedConfig}/rollouts/${activeRollout}/promote`, {
            method: 'POST',
            headers: { 'X-User-Id': 'admin', 'X-Role': 'admin' }
          });
          setActiveRollout(null);
        } else {
          await fetch(`${API}/configs/${selectedConfig}/rollouts/${activeRollout}/advance`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-User-Id': 'admin', 'X-Role': 'admin' },
            body: JSON.stringify({ percent })
          });
        }
        return;
      }

      // 3. Otherwise, start a brand new rollout
      const res = await fetch(`${API}/configs/${selectedConfig}/rollout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-User-Id': 'admin', 'X-Role': 'admin' },
        body: JSON.stringify({
          target: targetCluster,
          environment: ENV,
          percent: percent
        })
      });
      
      if (res.ok) {
        const data = await res.json();
        if (percent < 100) {
          setActiveRollout(data.rollout_id); // Save for subsequent advances
        }
      } else {
        const err = await res.json();
        alert(`Could not start rollout: ${err.detail}`);
      }
    } catch (e) {
      console.error(e);
    }
  };

  // Color generator for versions
  const getVersionColor = (version: number | undefined) => {
    if (!version) return 'rgba(255,255,255,0.1)';
    const colors = [
      'var(--accent-primary)', // v1
      'var(--success)',        // v2
      'var(--warning)',        // v3
      '#e056fd',               // v4
      '#686de0',               // v5
    ];
    return colors[(version - 1) % colors.length];
  };

  return (
    <div className="animate-fade-in" style={{ paddingBottom: '3rem' }}>
      <div className="page-header" style={{ alignItems: 'flex-start', flexWrap: 'wrap', gap: '2rem' }}>
        <div style={{ flex: '1 1 400px' }}>
          <h1 className="page-title">Live Node Cluster (100 Nodes)</h1>
          <p style={{ color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
            Simulating 100 connected servers. Watch them dynamically change color as the Canary Rollout 
            distributes the new configuration version based on a consistent hash of their Node ID.
          </p>
        </div>
        
        <div style={{ background: 'rgba(255,255,255,0.05)', padding: '1rem', borderRadius: '0.5rem', border: '1px solid rgba(255,255,255,0.1)', flex: '1 1 300px' }}>
          <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem' }}>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>Cluster Target</label>
              <select 
                className="form-input" 
                style={{ marginBottom: 0 }}
                value={targetCluster}
                onChange={e => setTargetCluster(e.target.value)}
              >
                <option value="checkout">Checkout</option>
                <option value="payment">Payment</option>
                <option value="recommendation">Recommendation</option>
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label style={{ display: 'block', fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>Observe Config</label>
              <select 
                className="form-input" 
                style={{ marginBottom: 0 }}
                value={selectedConfig}
                onChange={e => setSelectedConfig(e.target.value)}
              >
                {availableConfigs.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </div>
          </div>
          
          <div style={{ borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '1rem' }}>
            <label style={{ display: 'block', fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>
              Canary Controls (Stable: v{stableVersion} | Latest: v{latestVersion})
            </label>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn btn-outline" onClick={() => triggerRollout(10)} style={{ flex: 1, padding: '0.5rem' }}>
                10%
              </button>
              <button className="btn btn-outline" onClick={() => triggerRollout(25)} style={{ flex: 1, padding: '0.5rem' }}>
                25%
              </button>
              <button className="btn btn-outline" onClick={() => triggerRollout(50)} style={{ flex: 1, padding: '0.5rem' }}>
                50%
              </button>
              <button className="btn btn-primary" onClick={() => triggerRollout(100)} style={{ flex: 1, padding: '0.5rem' }}>
                100%
              </button>
            </div>
            {stableVersion === latestVersion && (
              <div style={{ fontSize: '0.75rem', color: 'var(--success)', marginTop: '0.5rem' }}>
                Tip: Clicking a percentage will automatically generate and deploy a candidate version!
              </div>
            )}
          </div>
        </div>
      </div>

      <div style={{ 
        display: 'grid', 
        gridTemplateColumns: 'repeat(auto-fill, minmax(40px, 1fr))', 
        gap: '8px',
        background: 'rgba(0,0,0,0.2)',
        padding: '1.5rem',
        borderRadius: '0.5rem',
        border: '1px solid rgba(255,255,255,0.05)'
      }}>
        {clients.map(client => (
          <div 
            key={client.id} 
            title={`${client.id}\nStatus: ${client.connected ? 'Connected' : 'Disconnected'}\nVersion: v${client.config?.version || '?'}`}
            style={{ 
              aspectRatio: '1/1', 
              borderRadius: '4px',
              background: getVersionColor(client.config?.version),
              boxShadow: client.connected ? `0 0 10px ${getVersionColor(client.config?.version)}` : 'none',
              opacity: client.connected ? 1 : 0.3,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '0.6rem',
              fontWeight: 'bold',
              color: '#fff',
              transition: 'all 0.3s ease',
              cursor: 'pointer'
            }}
          >
            v{client.config?.version || '?'}
          </div>
        ))}
      </div>
      
      <div style={{ marginTop: '2rem', display: 'flex', gap: '2rem', justifyContent: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem' }}>
           <div style={{ width: '16px', height: '16px', background: getVersionColor(stableVersion), borderRadius: '4px' }}></div>
           <span>v{stableVersion} (Stable)</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem' }}>
           <div style={{ width: '16px', height: '16px', background: getVersionColor(latestVersion), borderRadius: '4px' }}></div>
           <span>v{latestVersion} (Canary)</span>
        </div>
      </div>
    </div>
  );
}

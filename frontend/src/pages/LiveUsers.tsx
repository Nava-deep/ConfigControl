import { useState, useEffect } from 'react';
import { Server, Activity, ShieldAlert, CheckCircle2 } from 'lucide-react';

interface ConfigState {
  version: number;
  value: any;
}

interface ClientState {
  id: string;
  ws: WebSocket | null;
  configs: Record<string, ConfigState>;
  connected: boolean;
}

const API = 'http://localhost:8080';
const ENV = 'prod';

export default function LiveUsers() {
  const [targetCluster, setTargetCluster] = useState('checkout');
  const [clients, setClients] = useState<ClientState[]>([]);

  useEffect(() => {
    // Generate 5 distinct client IDs
    const newClients = Array.from({ length: 5 }).map((_, i) => ({
      id: `${targetCluster}-node-${i + 1}`,
      ws: null,
      configs: {},
      connected: false
    }));

    setClients(newClients);

    newClients.forEach(client => {
      // Connect to WebSocket listening to all changes for this target
      const ws = new WebSocket(`ws://localhost:8080/watch/ws?target=${targetCluster}&environment=${ENV}`);
      
      const fetchConfigState = async (configName: string) => {
        try {
          // Pass client_id as QUERY PARAM so backend correctly hashes it for canary percentages
          const res = await fetch(`${API}/configs/${configName}?target=${targetCluster}&environment=${ENV}&client_id=${client.id}`, {
            headers: { 'X-User-Id': 'demo-ui', 'X-Role': 'reader' }
          });
          const data = await res.json();
          setClients(prev => prev.map(c => {
            if (c.id === client.id) {
              return { ...c, configs: { ...c.configs, [configName]: { version: data.version, value: data.value } } };
            }
            return c;
          }));
        } catch (e) {
          console.error(`Failed to fetch ${configName} for ${client.id}`);
        }
      };

      ws.onopen = async () => {
        setClients(prev => prev.map(c => c.id === client.id ? { ...c, connected: true } : c));
        
        // 1. Fetch all config definitions to see which belong to this target
        try {
          const listRes = await fetch(`${API}/configs?environment=${ENV}`, {
            headers: { 'X-User-Id': 'demo-ui', 'X-Role': 'reader' }
          });
          const allConfigs = await listRes.json();
          const targetConfigs = allConfigs.filter((c: any) => c.stable_target === targetCluster);
          
          // 2. Fetch initial resolved state for each
          for (const config of targetConfigs) {
            await fetchConfigState(config.name);
          }
        } catch (e) {
          console.error("Failed to load initial configs", e);
        }
      };

      ws.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.event !== 'connected' && payload.config_name) {
          // Re-fetch only the config that changed
          fetchConfigState(payload.config_name);
        }
      };

      ws.onclose = () => {
        setClients(prev => prev.map(c => c.id === client.id ? { ...c, connected: false } : c));
      };

      client.ws = ws;
    });

    return () => {
      // Cleanup connections when target changes or unmounts
      setClients(current => {
        current.forEach(c => c.ws?.close());
        return [];
      });
    };
  }, [targetCluster]);

  return (
    <div className="animate-fade-in">
      <div className="page-header" style={{ alignItems: 'flex-start' }}>
        <div>
          <h1 className="page-title">Live Node Cluster (Fanout Demo)</h1>
          <p style={{ color: 'var(--text-secondary)', marginTop: '0.5rem', maxWidth: '800px' }}>
            This page simulates a cluster of 5 backend servers maintaining live WebSocket connections. 
            When you trigger a <strong>Canary Rollout</strong>, the control plane calculates a consistent hash of each Node ID to decide exactly which servers receive the update!
          </p>
        </div>
        
        <div style={{ background: 'rgba(255,255,255,0.05)', padding: '1rem', borderRadius: '0.5rem', border: '1px solid rgba(255,255,255,0.1)' }}>
          <label style={{ display: 'block', fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Select Cluster</label>
          <select 
            className="form-input" 
            style={{ width: '250px', marginBottom: 0 }}
            value={targetCluster}
            onChange={e => setTargetCluster(e.target.value)}
          >
            <option value="checkout">Checkout Service Nodes</option>
            <option value="payment">Payment Service Nodes</option>
            <option value="recommendation">Recommendation Nodes</option>
          </select>
        </div>
      </div>

      <div className="grid grid-cols-3">
        {clients.map(client => (
          <div key={client.id} className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '0.75rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600 }}>
                <Server size={18} color="var(--accent-primary)" />
                {client.id}
              </div>
              {client.connected ? (
                <span className="badge badge-success" style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                  <CheckCircle2 size={12} /> Connected
                </span>
              ) : (
                <span className="badge badge-danger" style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                  <ShieldAlert size={12} /> Disconnected
                </span>
              )}
            </div>

            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              {Object.keys(client.configs).length === 0 ? (
                 <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)', padding: '1rem' }}>
                   <Activity size={14} className="animate-pulse" /> Syncing configurations...
                 </div>
              ) : (
                Object.entries(client.configs).map(([configName, state]) => (
                  <div key={configName} style={{ background: 'rgba(0,0,0,0.3)', padding: '1rem', borderRadius: '0.5rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
                      <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>{configName}</span>
                      <span className="badge badge-neutral">v{state.version}</span>
                    </div>
                    <pre style={{ 
                      background: 'rgba(255,255,255,0.03)', 
                      padding: '0.75rem', 
                      borderRadius: '0.25rem',
                      fontSize: '0.8rem',
                      color: 'var(--success)',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-all'
                    }}>
                      {JSON.stringify(state.value, null, 2)}
                    </pre>
                  </div>
                ))
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

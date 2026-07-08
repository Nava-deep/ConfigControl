import { useState, useEffect } from 'react';
import { Server, Activity, ShieldAlert, CheckCircle2 } from 'lucide-react';

export default function LiveUsers() {
  const [clients, setClients] = useState<any[]>([]);

  useEffect(() => {
    // Simulate 5 connected clients
    const newClients = Array.from({ length: 5 }).map((_, i) => ({
      id: `client-${i + 1}`,
      ws: null as WebSocket | null,
      config: null as any,
      connected: false
    }));

    newClients.forEach(client => {
      // Connect to the WebSocket API for checkout-service.timeout config
      const ws = new WebSocket(`ws://localhost:8080/watch/ws?config_name=checkout-service.timeout&environment=prod&target=checkout`);
      
      ws.onopen = () => {
        setClients(prev => prev.map(c => c.id === client.id ? { ...c, connected: true } : c));
        
        // Fetch initial config state
        fetch(`http://localhost:8080/configs/checkout-service.timeout?target=checkout&environment=prod`, {
          headers: { 'X-User-Id': client.id, 'X-Role': 'reader' }
        })
          .then(res => res.json())
          .then(data => {
            setClients(prev => prev.map(c => c.id === client.id ? { ...c, config: data } : c));
          });
      };

      ws.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.event !== 'connected') {
          // Re-fetch config when notified
          fetch(`http://localhost:8080/configs/checkout-service.timeout?target=checkout&environment=prod`, {
            headers: { 'X-User-Id': client.id, 'X-Role': 'reader' }
          })
            .then(res => res.json())
            .then(data => {
              setClients(prev => prev.map(c => c.id === client.id ? { ...c, config: data } : c));
            });
        }
      };

      ws.onclose = () => {
        setClients(prev => prev.map(c => c.id === client.id ? { ...c, connected: false } : c));
      };

      client.ws = ws;
    });

    setClients(newClients);

    return () => {
      newClients.forEach(c => c.ws?.close());
    };
  }, []);

  return (
    <div className="animate-fade-in">
      <div className="page-header">
        <h1 className="page-title">Live Connected Users (Fanout Demo)</h1>
      </div>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '2rem' }}>
        These 5 cards simulate distinct servers connected to the Control Plane via WebSockets. 
        When you push a config change, watch them all instantly update through the Redis event bridge.
      </p>

      <div className="grid grid-cols-3">
        {clients.map(client => (
          <div key={client.id} className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
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

            <div style={{ background: 'rgba(0,0,0,0.3)', padding: '1rem', borderRadius: '0.5rem', flex: 1 }}>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>
                Active Config State:
              </div>
              {client.config ? (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                    <span>Version:</span>
                    <span className="badge badge-neutral">v{client.config.version}</span>
                  </div>
                  <pre style={{ 
                    background: 'rgba(255,255,255,0.05)', 
                    padding: '0.5rem', 
                    borderRadius: '0.25rem',
                    fontSize: '0.8rem',
                    color: 'var(--success)'
                  }}>
                    {JSON.stringify(client.config.value, null, 2)}
                  </pre>
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text-secondary)' }}>
                  <Activity size={14} className="animate-pulse" /> Awaiting config...
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

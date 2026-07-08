import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import { Settings, Users, Activity, Layers, Server } from 'lucide-react';
import ConfigManager from './pages/ConfigManager';
import LiveUsers from './pages/LiveUsers';
import MetricsRollouts from './pages/MetricsRollouts';
import Infrastructure from './pages/Infrastructure';

function Sidebar() {
  const location = useLocation();

  const navItems = [
    { to: '/',              icon: <Settings size={20} />, label: 'Config Manager' },
    { to: '/live',          icon: <Users size={20} />,    label: 'Live Users (Demo)' },
    { to: '/metrics',       icon: <Activity size={20} />, label: 'Metrics & Rollouts' },
    { to: '/infrastructure',icon: <Server size={20} />,   label: 'Infrastructure' },
  ];

  return (
    <div className="sidebar">
      <div className="sidebar-logo">
        <Layers className="sidebar-logo-icon" size={28} />
        <span>ConfigPlane</span>
      </div>
      <div className="sidebar-nav">
        {navItems.map(({ to, icon, label }) => (
          <Link
            key={to}
            to={to}
            className={`nav-item ${location.pathname === to ? 'active' : ''}`}
          >
            {icon}
            <span>{label}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

function App() {
  return (
    <Router>
      <div className="app-container">
        <Sidebar />
        <main className="main-content">
          <Routes>
            <Route path="/"               element={<ConfigManager />} />
            <Route path="/live"           element={<LiveUsers />} />
            <Route path="/metrics"        element={<MetricsRollouts />} />
            <Route path="/infrastructure" element={<Infrastructure />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;

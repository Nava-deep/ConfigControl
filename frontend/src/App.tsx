import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import { Settings, Users, Activity, Layers, Server } from 'lucide-react';
import ConfigManager from './pages/ConfigManager';
import LiveUsers from './pages/LiveUsers';

function Sidebar() {
  const location = useLocation();
  
  return (
    <div className="sidebar">
      <div className="sidebar-logo">
        <Layers className="sidebar-logo-icon" size={28} />
        <span>ConfigPlane</span>
      </div>
      
      <div className="sidebar-nav">
        <Link to="/" className={`nav-item ${location.pathname === '/' ? 'active' : ''}`}>
          <Settings size={20} />
          <span>Config Manager</span>
        </Link>
        <Link to="/live" className={`nav-item ${location.pathname === '/live' ? 'active' : ''}`}>
          <Users size={20} />
          <span>Live Users (Demo)</span>
        </Link>
        <div className="nav-item">
          <Activity size={20} />
          <span>Metrics & Rollouts</span>
        </div>
        <div className="nav-item">
          <Server size={20} />
          <span>Infrastructure</span>
        </div>
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
            <Route path="/" element={<ConfigManager />} />
            <Route path="/live" element={<LiveUsers />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;

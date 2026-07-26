import { useState, useEffect, useCallback } from 'react';
import MiningMap from './MiningMap';
import GlobalDashboard from './Dashboard';
import PipelineDashboard from './Pipeline';


const isDashboardPath = (p) => /^\/dashboard\/?$/i.test(p || '');
const isPipelinePath  = (p) => /^\/pipeline\/?$/i.test(p || '');

function readInitialTheme() {
  if (typeof window === 'undefined') return 'light';
  try {
    const h = new URLSearchParams(window.location.hash.slice(1));
    if (h.get('theme') === 'dark' || h.get('theme') === 'light') return h.get('theme');
    return window.localStorage.getItem('mao-theme') || 'light';
  } catch {
    return 'light';
  }
}

function resolveRoute(pathname) {
  if (isDashboardPath(pathname)) return 'dashboard';
  if (isPipelinePath(pathname))  return 'pipeline';
  return 'map';
}

export default function App() {
  const [route, setRoute] = useState(() => resolveRoute(window.location.pathname));
  const [theme, setTheme] = useState(readInitialTheme);

  // Theme is applied once, at the shell level, so both the map and the
  // dashboard react to it through the same CSS custom properties.
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { window.localStorage.setItem('mao-theme', theme); } catch { /* private mode */ }
  }, [theme]);

  useEffect(() => {
    const onPop = () => setRoute(resolveRoute(window.location.pathname));
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((to) => {
    const path =
      to === 'dashboard' ? '/Dashboard' :
      to === 'pipeline'  ? '/Pipeline'  : '/';
    const url = path + (to === 'map' ? window.location.hash : '');
    if (window.location.pathname + (to === 'map' ? window.location.hash : '') !== url) {
      window.history.pushState(null, '', url);
    }
    setRoute(to);
  }, []);

  const toggleTheme = useCallback(
    () => setTheme((t) => (t === 'light' ? 'dark' : 'light')),
    []
  );

  return (
    <div className="app-shell">
      <AppHeader
        route={route}
        navigate={navigate}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
      <div className="app-body">
        {route === 'dashboard' ? (
          <GlobalDashboard navigate={navigate} theme={theme} />
        ) : route === 'pipeline' ? (
          <PipelineDashboard theme={theme} />
        ) : (
          <MiningMap theme={theme} />
        )}
      </div>
    </div>
  );
}

function AppHeader({ route, navigate, theme, onToggleTheme }) {
  return (
    <header className="app-header">
      <div className="app-header-brand">
        <img src="/Logo_AriasTech.svg" alt="Arias Tech" width={34} height={34} />
        <div className="app-header-brand-text">
          <span className="app-header-brand-title">Arias Tech Solutions</span>
          <span className="app-header-brand-sub">Mining Activities Observation</span>
        </div>
      </div>

      <nav className="app-switch" role="tablist" aria-label="Views">
        <button
          role="tab"
          aria-selected={route === 'map'}
          className={`app-switch-btn ${route === 'map' ? 'is-active' : ''}`}
          onClick={() => navigate('map')}
        >
          <MapPinIcon />
          <span>Map</span>
        </button>
        <button
          role="tab"
          aria-selected={route === 'dashboard'}
          className={`app-switch-btn ${route === 'dashboard' ? 'is-active' : ''}`}
          onClick={() => navigate('dashboard')}
        >
          <GridIcon />
          <span>Dashboard</span>
        </button>
        <button
          role="tab"
          aria-selected={route === 'pipeline'}
          className={`app-switch-btn ${route === 'pipeline' ? 'is-active' : ''}`}
          onClick={() => navigate('pipeline')}
        >
          <PipelineIcon />
          <span>Pipeline</span>
        </button>
      </nav>

      <button
        className="app-header-theme"
        onClick={onToggleTheme}
        aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
        title={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
      >
        {theme === 'light' ? <MoonIcon /> : <SunIcon />}
      </button>
    </header>
  );
}

function MapPinIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
      <circle cx="12" cy="10" r="3" />
    </svg>
  );
}

function GridIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  );
}

function PipelineIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="2" y="9" width="5" height="6" rx="1" />
      <rect x="9.5" y="9" width="5" height="6" rx="1" />
      <rect x="17" y="9" width="5" height="6" rx="1" />
      <line x1="7" y1="12" x2="9.5" y2="12" />
      <line x1="14.5" y1="12" x2="17" y2="12" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M21.752 15.002A9.718 9.718 0 0 1 18 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 0 0 3 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 0 0 9.002-5.998Z" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <circle cx="12" cy="12" r="4" fill="currentColor" />
      <line x1="12" y1="2" x2="12" y2="4" /><line x1="12" y1="20" x2="12" y2="22" />
      <line x1="2" y1="12" x2="4" y2="12" /><line x1="20" y1="12" x2="22" y2="12" />
      <line x1="4.93" y1="4.93" x2="6.34" y2="6.34" />
      <line x1="17.66" y1="17.66" x2="19.07" y2="19.07" />
      <line x1="4.93" y1="19.07" x2="6.34" y2="17.66" />
      <line x1="17.66" y1="6.34" x2="19.07" y2="4.93" />
    </svg>
  );
}

import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import Map, { Source, Layer, Popup, NavigationControl, ScaleControl } from 'react-map-gl/mapbox';
import 'mapbox-gl/dist/mapbox-gl.css';
import PolygonAnalysis from './PolygonAnalysis';
import JobsPanel from './JobsPanel';
import Dashboardareas from './Dashboardareas';

const MAPBOX_TOKEN  = import.meta.env.VITE_MAPBOX_TOKEN;
const API_BASE      = import.meta.env.VITE_API_BASE    ?? "http://localhost:9000";
const MARTIN_BASE   = import.meta.env.VITE_MARTIN_BASE ?? "http://localhost:3000";

if (!MAPBOX_TOKEN) {
  console.error(
    "VITE_MAPBOX_TOKEN is not set"
  );
}

const MAP_STYLES = [
  {
    id: 'light',
    type: 'mapbox-style',
    label: 'Light',
    url: 'mapbox://styles/mapbox/light-v11',
    image: '/img/map-styles/light.png',
    swatch: 'linear-gradient(135deg,#fafafa 0%,#dadee2 100%)',
  },
  {
    id: 'dark',
    type: 'mapbox-style',
    label: 'Dark',
    url: 'mapbox://styles/mapbox/dark-v11',
    image: '/img/map-styles/dark.png',
    swatch: 'linear-gradient(135deg,#1a2030 0%,#0a0e18 100%)',
  },
  {
    id: 'satellite',
    type: 'mapbox-style',
    label: 'Satellite',
    url: 'mapbox://styles/mapbox/satellite-streets-v12',
    image: '/img/map-styles/satellite.png',
    swatch: 'linear-gradient(135deg,#7d6240 0%,#bfa478 50%,#5b6e36 100%)',
  },
  {
    id: 'oceanic',
    type: 'tiles-server',
    label: 'Oceanic',
    url: 'https://services.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}',
    image: '/img/map-styles/oceanic.png',
    swatch: 'linear-gradient(135deg,#0b3d57 0%,#1f7099 60%,#7fb5cf 100%)',
    attribution: 'Tiles &copy; Esri — Sources: GEBCO, NOAA, CHS, OSU, UNH, CSUMB, NAVTEQ, NASA',
  },
  {
    id: 'topographic',
    type: 'tiles-server',
    label: 'Topographic',
    url: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    image: '/img/map-styles/topographic.png',
    swatch: 'linear-gradient(135deg,#cfe3c8 0%,#a5b88a 55%,#7d6f4a 100%)',
    attribution: 'Tiles &copy; Esri — Esri, DeLorme, NAVTEQ, TomTom, USGS',
  },
  {
    id: 'dem',
    type: 'tiles-server',
    label: 'DEM',
    url: 'https://gis.ngdc.noaa.gov/arcgis/services/DEM_mosaics/DEM_global_mosaic_hillshade/ImageServer/WMSServer?SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS=DEM_global_mosaic_hillshade:ColorHillshade&STYLES=&FORMAT=image%2Fpng&TRANSPARENT=true&HEIGHT=256&WIDTH=256&CRS=EPSG:3857&BBOX={bbox-epsg-3857}',
    image: '/img/map-styles/mosaic.png',
    swatch: 'linear-gradient(135deg,#2b2422 0%,#776055 60%,#d6c8b6 100%)',
    attribution: 'NOAA NGDC · DEM global mosaic hillshade',
  },
];

const DEFAULT_BASEMAP = 'satellite';
const MAP_STYLE_BY_ID = (id) =>
  MAP_STYLES.find((b) => b.id === id) || MAP_STYLES.find((b) => b.id === DEFAULT_BASEMAP);

function styleSpecFor(b) {
  if (b.type === 'mapbox-style') return b.url;
  return {
    version: 8,
    sources: {
      __basemap: {
        type: 'raster',
        tiles: [b.url],
        tileSize: 256,
        attribution: b.attribution || '',
      },
    },
    layers: [
      { id: '__bg', type: 'background', paint: { 'background-color': '#0b1220' } },
      { id: '__basemap', type: 'raster', source: '__basemap' },
    ],
  };
}


function gibsDate(daysAgo = 1) {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

const RASTER_LAYERS = [
  {
    id: 'ndvi',
    label: 'Vegetation (NDVI)',
    desc:  'MODIS Terra · 8-day vegetation index',
    accent: '#10b981',
    maxzoom: 9,
    daysAgo: 8,
    url: (date) =>
      `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_NDVI_8Day/default/${date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.png`,
  },
  {
    id: 'precip',
    label: 'Precipitation',
    desc:  'GPM IMERG · global precipitation rate',
    accent: '#3b82f6',
    maxzoom: 6,
    daysAgo: 2,
    url: (date) =>
      `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/IMERG_Precipitation_Rate/default/${date}/GoogleMapsCompatible_Level6/{z}/{y}/{x}.png`,
  },
  {
    id: 'lst',
    label: 'Surface temperature',
    desc:  'MODIS Terra · daytime land-surface temp',
    accent: '#ef4444',
    maxzoom: 7,
    daysAgo: 3,
    url: (date) =>
      `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_Land_Surface_Temp_Day/default/${date}/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png`,
  },
];

const fmtNumber = (v, digits = 2) =>
  v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits });

const fmtVolume = (v) => {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const abs = Math.abs(n);
  const sign = n < 0 ? '−' : '';
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(2)} Billion m³`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)} Million m³`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)} k m³`;
  return `${sign}${abs.toFixed(0)} m³`;
};

const fmtVolumeShort = (v) => {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const abs = Math.abs(n);
  const sign = n < 0 ? '−' : '';
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)}k`;
  return `${sign}${abs.toFixed(0)}`;
};

const fmtDate = (s) => {
  if (!s) return '—';
  try { return new Date(s).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
  catch { return s; }
};

const ZONE_COLORS = { gain: '#10b981', loss: '#ef4444' };

function activityBadge(site) {
  const lvl = (site?.activity_level || 'Low').toString();
  const norm = lvl.toLowerCase();
  const level = norm === 'high' ? 'high' : norm === 'medium' ? 'medium' : 'low';
  return { level, label: lvl.charAt(0).toUpperCase() + lvl.slice(1).toLowerCase() };
}


function encodeState(s) {
  const p = new URLSearchParams();
  if (s.org)     p.set('org', s.org);
  if (s.q)       p.set('q', s.q);
  if (s.country) p.set('c', s.country);
  if (s.vmin != null) p.set('vmin', String(s.vmin));
  if (s.vmax != null) p.set('vmax', String(s.vmax));
  if (s.amin > 0)     p.set('amin', String(s.amin));
  if (s.bm   && s.bm !== 'satellite') p.set('bm', s.bm);
  if (s.theme && s.theme !== 'light') p.set('theme', s.theme);
  if (s.layers?.length) p.set('layers', s.layers.join(','));
  return p.toString();
}
function decodeState(hash) {
  const p = new URLSearchParams(hash);
  return {
    org:     p.get('org') || '',
    q:       p.get('q')   || '',
    country: p.get('c')   || '',
    vmin:    p.has('vmin') ? Number(p.get('vmin')) : null,
    vmax:    p.has('vmax') ? Number(p.get('vmax')) : null,
    amin:    p.has('amin') ? Number(p.get('amin')) : 0,
    bm:      p.get('bm')    || 'satellite',
    theme:   p.get('theme') || null,
    layers:  p.get('layers') ? p.get('layers').split(',').filter(Boolean) : [],
  };
}

const INITIAL = (typeof window !== 'undefined') ? decodeState(window.location.hash.slice(1)) : {};


export default function MiningMap({ theme }) {
  const mapRef = useRef(null);
  const polyCacheRef = useRef(new globalThis.Map());

  const [sites, setSites] = useState([]);
  const [siteChangeClasses, setSiteChangeClasses] = useState([]);
  const [polyPopup, setPolyPopup] = useState(null);
  const [bboxPopup, setBboxPopup] = useState(null);

  // Both panels start closed so the map is clean on load — the user opens
  // Sites / Controls from the floating toggles when they want them.
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [mcOpen, setMcOpen] = useState(false);
  const [openSection, setOpenSection] = useState('style');
  const [basemap, setBasemap] = useState(INITIAL.bm || 'satellite');
  const [activeSiteId, setActiveSiteId] = useState(null);
  const [view, setView] = useState('list');
  const [selectedSite, setSelectedSite] = useState(null);

  const [enabledRasters, setEnabledRasters] = useState(() => new globalThis.Set(INITIAL.layers || []));
  const [rasterOpacity, setRasterOpacity]   = useState(0.65);

  const [jobs,         setJobs]         = useState([]);
  const [sitesVersion, setSitesVersion] = useState(0);

  const [polyOpacity, setPolyOpacity] = useState(0.7);
  // Theme is owned by the app shell (App.jsx) so the header toggle and the
  // dashboard stay in sync; we only read it here to keep it in the shareable
  // URL hash alongside the other map state.

  const [filterOrg, setFilterOrg] = useState(INITIAL.org || '');
  const [search, setSearch]       = useState(INITIAL.q   || '');
  const [selectedCountry, setSelectedCountry] = useState(INITIAL.country || '');
  const [countrySearch, setCountrySearch] = useState(INITIAL.country || '');
  const [showCountryDropdown, setShowCountryDropdown] = useState(false);
  const countrySearchRef = useRef(null);
  const [volumeRange, setVolumeRange] = useState([INITIAL.vmin ?? null, INITIAL.vmax ?? null]);
  const [minArea, setMinArea]     = useState(INITIAL.amin || 0);
  const [hiddenClasses, setHiddenClasses] = useState(() => new globalThis.Set());

  useEffect(() => {
    const onClickOutside = (e) => {
      if (countrySearchRef.current && !countrySearchRef.current.contains(e.target)) {
        setShowCountryDropdown(false);
      }
    };
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadSites = () =>
      fetch(`${API_BASE}/sites`).then((r) => r.json()).then((d) => { if (!cancelled) setSites(d); })
        .catch((err) => console.error("Failed to load /sites:", err));
    loadSites();
    const t = setInterval(loadSites, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [sitesVersion]);


  const [volMin, volMax] = useMemo(() => {
    const FLOOR = 1_000_000_000;
    const vs = sites.map((s) => Math.abs(Number(s.volume) || 0));
    const dataMax = vs.length ? Math.max(...vs) : 0;
    const span = Math.max(FLOOR, Math.ceil(dataMax * 1.2));
    return [-span, span];
  }, [sites]);

  const tilesVersion = sites.length;

  const orgOptions = useMemo(
    () => Array.from(new globalThis.Set(sites.map((s) => s.org_id).filter(Boolean))).sort(),
    [sites]
  );

    const countryOptions = useMemo(() => {
    const counts = new globalThis.Map();
    for (const s of sites) {
      const name = (s.country || '').trim();
      if (!name) continue;
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    return Array.from(counts.entries())
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  }, [sites]);

  const filteredCountryOptions = useMemo(() => {
    const q = countrySearch.trim().toLowerCase();
    if (!q || q === selectedCountry.toLowerCase()) return countryOptions;
    return countryOptions.filter((c) => c.name.toLowerCase().includes(q));
  }, [countryOptions, countrySearch, selectedCountry]);

  const visibleSites = useMemo(() => {
    const q = search.trim().toLowerCase();
    return sites.filter((s) => {
      if (filterOrg && s.org_id !== filterOrg) return false;
      if (selectedCountry && (s.country || '') !== selectedCountry) return false;
      if (q) {
        if (!(s.country || '').toLowerCase().includes(q)) return false;
      }
      const v = Number(s.volume);
      if (volumeRange[0] != null && (Number.isFinite(v) ? v : 0) < volumeRange[0]) return false;
      if (volumeRange[1] != null && (Number.isFinite(v) ? v : 0) > volumeRange[1]) return false;
      if (minArea > 0 && (Number(s.total_area) || 0) < minArea) return false;
      return true;
    });
  }, [sites, filterOrg, selectedCountry, search, volumeRange, minArea]);

  const tileUrl = useMemo(() => {
    const qs = new URLSearchParams();
    if (filterOrg) qs.set('org_id', filterOrg);
    if (volumeRange[0] != null) qs.set('volume_min', String(volumeRange[0]));
    if (volumeRange[1] != null) qs.set('volume_max', String(volumeRange[1]));
    if (minArea > 0) qs.set('area_min', String(minArea));
    if (tilesVersion) qs.set('_v', String(tilesVersion));
    const q = qs.toString();
    return `${MARTIN_BASE}/mining_tiles/{z}/{x}/{y}${q ? `?${q}` : ''}`;
  }, [filterOrg, volumeRange, minArea, tilesVersion]);

  const inFlightBboxGeoJSON = useMemo(() => ({
    type: 'FeatureCollection',
    features: jobs.map((j) => {
      const [minLon, minLat, maxLon, maxLat] = j.bbox;
      return {
        type: 'Feature',
        properties: {
          runId:  j.runId,
          state:  j.state || 'running',
        },
        geometry: {
          type: 'Polygon',
          coordinates: [[
            [minLon, minLat],
            [maxLon, minLat],
            [maxLon, maxLat],
            [minLon, maxLat],
            [minLon, minLat],
          ]],
        },
      };
    }),
  }), [jobs]);

  const bboxesGeoJSON = useMemo(() => ({
    type: 'FeatureCollection',
    features: visibleSites.map((s) => {
      const [minLon, minLat, maxLon, maxLat] = s.bbox;
      return {
        type: 'Feature',
        properties: { id: s.id, bbox_id: s.bbox_id, org_id: s.org_id, area_name: s.area_name },
        geometry: {
          type: 'Polygon',
          coordinates: [[
            [minLon, minLat],
            [maxLon, minLat],
            [maxLon, maxLat],
            [minLon, maxLat],
            [minLon, minLat],
          ]],
        },
      };
    }),
  }), [visibleSites]);

  const polygonFilter = useMemo(() => {
    if (hiddenClasses.size === 0) return null;
    return ['!', ['in', ['get', 'change_class'], ['literal', Array.from(hiddenClasses)]]];
  }, [hiddenClasses]);


  useEffect(() => {
    if (view !== 'detail' || !selectedSite || !mapRef.current) {
      setSiteChangeClasses([]);
      return;
    }
    const map = mapRef.current.getMap?.();
    if (!map) return;

    const targetId = String(selectedSite.id);
    setSiteChangeClasses([]);

    const collect = () => {
      try {
        const feats = map.queryRenderedFeatures(undefined, { layers: ['mining-polygons-fill'] });
        setSiteChangeClasses((prev) => {
          const merged = new globalThis.Map(prev.map((c) => [c.change_class, c]));
          let added = false;
          for (const f of feats) {
            if (String(f.properties?.area_id) !== targetId) continue;
            const cls = f.properties?.change_class;
            if (!cls || merged.has(cls)) continue;
            merged.set(cls, {
              change_class: cls,
              zone_type:    f.properties?.zone_type,
              color:        f.properties?.color,
            });
            added = true;
          }
          if (!added) return prev;
          return Array.from(merged.values()).sort((a, b) => a.change_class.localeCompare(b.change_class));
        });
      } catch { /* source not ready */ }
    };

    const onSourceData = (e) => {
      if (e.sourceId === 'mining-tiles' && e.isSourceLoaded) collect();
    };

    collect();
    map.on('idle', collect);
    map.on('sourcedata', onSourceData);
    map.on('moveend', collect);
    return () => {
      map.off('idle', collect);
      map.off('sourcedata', onSourceData);
      map.off('moveend', collect);
    };
  }, [view, selectedSite]);


  useEffect(() => {
    const hash = encodeState({
      org: filterOrg, q: search, country: selectedCountry,
      vmin: volumeRange[0], vmax: volumeRange[1],
      amin: minArea,
      bm: basemap, theme,
      layers: Array.from(enabledRasters),
    });
    const next = hash ? `#${hash}` : window.location.pathname;
    if (window.location.hash !== (hash ? `#${hash}` : '')) {
      window.history.replaceState(null, '', next);
    }
  }, [filterOrg, search, selectedCountry, volumeRange, minArea, basemap, theme, enabledRasters]);

  const toggleClass = useCallback((cls) => {
    setHiddenClasses((prev) => {
      const next = new globalThis.Set(prev);
      if (next.has(cls)) next.delete(cls); else next.add(cls);
      return next;
    });
  }, []);

  const toggleRaster = useCallback((id) => {
    setEnabledRasters((prev) => {
      const next = new globalThis.Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

 
  const openSiteDetail = useCallback((site) => {
    setSelectedSite(site);
    setActiveSiteId(site.id);
    setView('detail');
    setSidebarOpen(true);
    setSiteChangeClasses([]);
    setHiddenClasses(new globalThis.Set());

    if (mapRef.current && site.bbox) {
      const [minLon, minLat, maxLon, maxLat] = site.bbox;
      // Wider sidebar in dashboard mode -> bigger left padding so the area sits
      // in the visible map gap, not behind the panel.
      const sidebarWidth = Math.min(620, Math.round(window.innerWidth * 0.44));
      mapRef.current.fitBounds(
        [[minLon, minLat], [maxLon, maxLat]],
        {
          padding: { top: 100, bottom: 120, right: 80, left: sidebarWidth + 40 },
          duration: 1300,
          maxZoom: 14.5,
        }
      );
    }
  }, []);

  const onMapClick = useCallback(async (event) => {
    const map = mapRef.current?.getMap();
    if (!map) return;
    const features = map.queryRenderedFeatures(event.point, {
      layers: ['mining-polygons-fill', 'bboxes-fill'],
    });
    const polyFeature = features.find((f) => f.layer.id === 'mining-polygons-fill');
    const bboxFeature = features.find((f) => f.layer.id === 'bboxes-fill');
    const lngLat = { lng: event.lngLat.lng, lat: event.lngLat.lat };

    if (polyFeature) {
      setBboxPopup(null);
      const { area_id, polygon_idx, color: tileColor, change_class } = polyFeature.properties;
      const instantColor = tileColor || '#f59e0b';
      const cacheKey = `${area_id}-${polygon_idx}`;
      const cached = polyCacheRef.current.get(cacheKey);

      if (cached) {
        setPolyPopup({ lngLat, change_class, ...cached });
        return;
      }

      setPolyPopup({ lngLat, loading: true, change_class, color: instantColor });

      try {
        const res = await fetch(`${API_BASE}/api/areas/${area_id}/polygons/${polygon_idx}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        const payload = {
          color: json.properties?.color || instantColor,
          areaInfo: { org_id: json.org_id, bbox_id: json.bbox_id, area_name: json.area_name },
          props: json.properties,
        };
        polyCacheRef.current.set(cacheKey, payload);
        setPolyPopup({ lngLat, change_class, ...payload });
      } catch (err) {
        setPolyPopup({ lngLat, error: err.message, color: instantColor, change_class });
      }
      return;
    }
    setPolyPopup(null);

    if (bboxFeature) {
      const id = bboxFeature.properties?.id;
      const site = sites.find((s) => s.id === id);
      if (site) setBboxPopup({ lngLat, site });
      return;
    }
    setBboxPopup(null);
  }, [sites]);

  const filtersActive =
    filterOrg || search || selectedCountry ||
    volumeRange[0] != null || volumeRange[1] != null ||
    minArea > 0 ||
    hiddenClasses.size > 0;

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      {!sidebarOpen && (
        <button className="sidebar-toggle" onClick={() => setSidebarOpen(true)}>
          <img src="/Logo_AriasTech.svg" alt="Arias Tech" width={20} height={20} />
          <span>Sites ({sites.length})</span>
        </button>
      )}

      {/* LEFT SIDEBAR */}
      <aside className={`sidebar ${sidebarOpen ? 'is-open' : ''} ${view === 'detail' ? 'is-dashboard' : ''}`}>
        {view !== 'detail' && (
          <div className="sidebar-header is-slim">
            <span className="sidebar-header-label">
              Sites <span className="sidebar-header-count">{sites.length}</span>
            </span>
            <button className="sidebar-close" onClick={() => setSidebarOpen(false)} aria-label="Close">×</button>
          </div>
        )}

        {view === 'list' && (
  <>
    {/* Country search */}
    {countryOptions.length > 0 && (
      <div className="country-search-wrapper" ref={countrySearchRef}>
        <div className={`country-search-box ${showCountryDropdown ? 'is-open' : ''}`}>
          <svg className="country-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <circle cx="11" cy="11" r="7" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            className="country-search-input"
            placeholder="Search country…"
            value={countrySearch}
            onChange={(e) => {
              setCountrySearch(e.target.value);
              setShowCountryDropdown(true);
              if (!e.target.value.trim()) setSelectedCountry('');
            }}
            onFocus={() => setShowCountryDropdown(true)}
          />
          {countrySearch && (
            <button
              type="button"
              className="country-search-clear"
              onClick={() => { setCountrySearch(''); setSelectedCountry(''); setShowCountryDropdown(false); }}
              aria-label="Clear country"
            >×</button>
          )}
        </div>
        {showCountryDropdown && (
          <ul className="country-dropdown" role="listbox">
            <li
              className={`country-dropdown-item ${selectedCountry === '' ? 'is-active' : ''}`}
              onClick={() => { setSelectedCountry(''); setCountrySearch(''); setShowCountryDropdown(false); }}
            >
              <span className="country-dropdown-name">All countries</span>
              <span className="country-dropdown-count">{sites.length}</span>
            </li>
            {filteredCountryOptions.length === 0 ? (
              <li className="country-dropdown-empty">No matches</li>
            ) : (
              filteredCountryOptions.map((c) => (
                <li
                  key={c.name}
                  className={`country-dropdown-item ${selectedCountry === c.name ? 'is-active' : ''}`}
                  onClick={() => { setSelectedCountry(c.name); setCountrySearch(c.name); setShowCountryDropdown(false); }}
                >
                  <span className="country-dropdown-name">{c.name}</span>
                  <span className="country-dropdown-count">{c.count}</span>
                </li>
              ))
            )}
          </ul>
        )}
      </div>
    )}

    {/* Scrollable content area */}
    <div className="sidebar-content">
      <div className="sidebar-filters">
        <SignedDualRange
          label="Volume range"
          min={volMin}
          max={volMax}
          step={Math.max(1, Math.round((volMax - volMin) / 400))}
          value={volumeRange}
          onChange={setVolumeRange}
          fmt={fmtVolumeShort}
        />

        <RangeFilter
          label="Min area (ha)"
          min={0}
          max={100_000}
          step={100}
          value={minArea}
          onChange={setMinArea}
          fmt={(v) => `${fmtNumber(v, 0)} ha`}
        />

        {filtersActive && (
          <button className="filter-reset" onClick={() => {
            setFilterOrg(''); setSearch(''); setSelectedCountry(''); setCountrySearch('');
            setVolumeRange([null, null]); setMinArea(0);
          }}>
            Reset filters
          </button>
        )}
      </div>

      <div className="sidebar-section-label">
        <span>{visibleSites.length} site{visibleSites.length === 1 ? '' : 's'}</span>
      </div>

      {visibleSites.length === 0 ? (
        <div className="sidebar-empty">No sites match the current filters.</div>
      ) : (
        <ul className="sidebar-list">
          {visibleSites.map((s) => {
            const mag = activityBadge(s);
            return (
              <li
                key={s.id}
                className={`sidebar-item ${activeSiteId === s.id ? 'is-active' : ''}`}
                onClick={() => openSiteDetail(s)}
              >
                <div className="country-tag">
                  <svg className="country-tag-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
                    <circle cx="12" cy="10" r="3" />
                  </svg>
                  <span className="country-tag-name">{s.country || 'Unknown location'}</span>
                </div>
                <div className="sidebar-item-head">
                  <p className="sidebar-item-name">{s.area_name || s.bbox_id}</p>
                  <span className={`mag-badge mag-${mag.level}`} title="Mining activity level">{mag.label}</span>
                </div>
                <div className="sidebar-item-client">{s.org_id || '—'}</div>
                <div className="sidebar-item-stats">
                  <div className="stat">
                    <span className="stat-label">Volume</span>
                    <span className="stat-value">{fmtVolume(s.volume)}</span>
                  </div>
                  <div className="stat">
                    <span className="stat-label">Area</span>
                    <span className="stat-value">{fmtNumber(s.total_area, 0)} ha</span>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  </>
)}


        {view === 'detail' && selectedSite && (
          <Dashboardareas
            key={selectedSite.bbox_id}
            site={selectedSite}
            changeClasses={siteChangeClasses}
            hiddenClasses={hiddenClasses}
            onToggleClass={toggleClass}
            onBack={() => {
              setView('list');
              setActiveSiteId(null);
              setSiteChangeClasses([]);
              setHiddenClasses(new globalThis.Set());
            }}
          />
        )}
      </aside>

      {/* RIGHT MAP CONTROLS */}
      {!mcOpen && (
        <button className="mc-toggle" onClick={() => setMcOpen(true)} title="Open map controls">
          <ControlsIcon />
          <span>Controls</span>
        </button>
      )}

      <aside className={`mc ${mcOpen ? 'is-open' : ''}`}>
        <div className="mc-header">
          <h2 className="mc-title">Map Controls</h2>
          <button className="mc-close" onClick={() => setMcOpen(false)} aria-label="Close">×</button>
        </div>

        <McSection
          id="style" label="Map Style"
          openSection={openSection} setOpenSection={setOpenSection}
        >
          <div className="style-grid">
            {MAP_STYLES.map((b) => (
              <button
                key={b.id}
                className={`style-card ${basemap === b.id ? 'is-active' : ''}`}
                onClick={() => setBasemap(b.id)}
                title={b.label}
              >
                <div
                  className="style-thumb"
                  style={{ backgroundImage: b.swatch }}
                />
                <div className="style-label">{b.label}</div>
              </button>
            ))}
          </div>
        </McSection>

        <McSection
          id="layers" label="Data Layers"
          openSection={openSection} setOpenSection={setOpenSection}
          right={enabledRasters.size > 0 ? <span className="badge-pill">{enabledRasters.size}</span> : null}
        >
          <p className="mc-help">Free open-source rasters from NASA GIBS — vegetation, precipitation and surface temperature. They sit beneath your polygons.</p>
          {RASTER_LAYERS.map((l) => (
            <RasterToggle
              key={l.id}
              layer={l}
              enabled={enabledRasters.has(l.id)}
              onToggle={() => toggleRaster(l.id)}
            />
          ))}
          {enabledRasters.size > 0 && (
            <RangeFilter
              label="Layer opacity"
              min={0} max={1} step={0.05}
              value={rasterOpacity}
              onChange={setRasterOpacity}
              fmt={(v) => `${Math.round(v * 100)}%`}
            />
          )}
        </McSection>

        <McSection
          id="polygons" label="Mining Polygons"
          openSection={openSection} setOpenSection={setOpenSection}
        >
          <p className="mc-help">Opacity of the detected change polygons drawn over the basemap.</p>
          <RangeFilter
            label="Polygon opacity"
            min={0} max={1} step={0.05}
            value={polyOpacity}
            onChange={setPolyOpacity}
            fmt={(v) => `${Math.round(v * 100)}%`}
          />
        </McSection>

      </aside>

      {/* POLYGON DRAW + ON-DEMAND PIPELINE TRIGGER */}
      <PolygonAnalysis
        mapRef={mapRef}
        sidebarOpen={sidebarOpen}
        mcOpen={mcOpen}
        activeJobCount={jobs.filter((j) => j.state !== 'success' && j.state !== 'failed').length}
        onJobStarted={(job) => setJobs((prev) => [...prev, { ...job, state: 'running' }])}
      />

      <JobsPanel
        jobs={jobs}
        mcOpen={mcOpen}
        sidebarOpen={sidebarOpen}
        onCloseJob={(runId) => setJobs((prev) => prev.filter((j) => j.runId !== runId))}
        onJobStateChange={(runId, state) =>
          setJobs((prev) => prev.map((j) => (j.runId === runId ? { ...j, state } : j)))
        }
        onJobSuccess={(job) => {
          setSitesVersion((v) => v + 1);
          setJobs((prev) =>
            prev.map((j) => (j.runId === job.runId ? { ...j, state: 'success' } : j)),
          );
          setTimeout(() => {
            if (mapRef.current && job.bbox) {
              const [minLon, minLat, maxLon, maxLat] = job.bbox;
              mapRef.current.fitBounds(
                [[minLon, minLat], [maxLon, maxLat]],
                { padding: 80, duration: 1200 },
              );
            }
          }, 250);
        }}
      />

      {/* MAP*/}
      <Map
        ref={mapRef}
        initialViewState={{ longitude: -6.8, latitude: 32.8, zoom: 6 }}
        mapStyle={styleSpecFor(MAP_STYLE_BY_ID(basemap))}
        mapboxAccessToken={MAPBOX_TOKEN}
        interactiveLayerIds={['bboxes-fill', 'mining-polygons-fill']}
        onClick={onMapClick}
        style={{ width: '100%', height: '100%' }}
      >
        <NavigationControl position="bottom-right" showCompass={false} />
        <ScaleControl position="bottom-left" />

        {/* Raster overlays*/}
        {RASTER_LAYERS.filter((l) => enabledRasters.has(l.id)).map((l) => {
          const date = gibsDate(l.daysAgo);
          return (
            <Source
              key={`raster-${l.id}-${date}`}
              id={`raster-${l.id}`}
              type="raster"
              tiles={[l.url(date)]}
              tileSize={256}
              attribution="NASA GIBS"
              maxzoom={l.maxzoom}
            >
              <Layer
                id={`raster-${l.id}-fill`}
                type="raster"
                paint={{ 'raster-opacity': rasterOpacity }}
              />
            </Source>
          );
        })}

        <Source id="inflight-bboxes" type="geojson" data={inFlightBboxGeoJSON}>
          <Layer
            id="inflight-bboxes-fill"
            type="fill"
            paint={{
              'fill-color':   ['match', ['get', 'state'], 'failed', '#ef4444', 'success', '#10b981', '#3ba3a7'],
              'fill-opacity': 0.10,
            }}
          />
          <Layer
            id="inflight-bboxes-outline"
            type="line"
            paint={{
              'line-color':     ['match', ['get', 'state'], 'failed', '#ef4444', 'success', '#10b981', '#2c8fb1'],
              'line-width':     2.4,
              'line-dasharray': [3, 2],
            }}
          />
        </Source>

        <Source id="bboxes" type="geojson" data={bboxesGeoJSON}>
          <Layer
            id="bboxes-fill"
            type="fill"
            paint={{ 'fill-color': '#3ba3a7', 'fill-opacity': 0.05 }}
          />
          <Layer
            id="bboxes-outline"
            type="line"
            paint={{
              'line-color': '#3ba3a7',
              'line-width': 2.5,
              'line-dasharray': [2, 1.5],
            }}
          />
        </Source>

        <Source
          key={tileUrl}
          id="mining-tiles"
          type="vector"
          tiles={[tileUrl]}
          minzoom={4}
          maxzoom={22}
        >
          <Layer
            id="mining-polygons-fill"
            type="fill"
            source-layer="mining_polygons"
            {...(polygonFilter ? { filter: polygonFilter } : {})}
            paint={{
              'fill-color': ['coalesce', ['get', 'color'], '#f59e0b'],
              'fill-opacity': polyOpacity,
              'fill-outline-color': '#1e293b',
            }}
          />
          <Layer
            id="mining-polygons-line"
            type="line"
            source-layer="mining_polygons"
            {...(polygonFilter ? { filter: polygonFilter } : {})}
            paint={{ 'line-color': '#1e293b', 'line-width': 0.6, 'line-opacity': polyOpacity * 0.6 }}
          />
        </Source>

        {bboxPopup && (
          <Popup
            className="popup-bbox"
            longitude={bboxPopup.lngLat.lng}
            latitude={bboxPopup.lngLat.lat}
            anchor="top" offset={12}
            closeOnClick={false}
            onClose={() => setBboxPopup(null)}
            maxWidth="none"
          >
            <div className="popup-bbox-header">
              <div className="popup-bbox-title">{bboxPopup.site.area_name || bboxPopup.site.bbox_id}</div>
              <div className="popup-bbox-sub">BBOX · {bboxPopup.site.bbox_id}</div>
            </div>
            <div className="popup-bbox-body">
              <div className="popup-bbox-row">
                <span className="popup-bbox-label">Org</span>
                <span className="popup-bbox-value">{bboxPopup.site.org_id ?? '—'}</span>
              </div>
              <div className="popup-bbox-row">
                <span className="popup-bbox-label">Area</span>
                <span className="popup-bbox-value">{fmtNumber(bboxPopup.site.total_area, 0)} ha</span>
              </div>
              <div className="popup-bbox-row">
                <span className="popup-bbox-label">Volume</span>
                <span className="popup-bbox-value">{fmtVolume(bboxPopup.site.volume)}</span>
              </div>
              <div className="popup-bbox-row">
                <span className="popup-bbox-label">Activity</span>
                <span className="popup-bbox-value">
                  {(() => {
                    const mag = activityBadge(bboxPopup.site);
                    return <span className={`mag-badge mag-${mag.level}`}>{mag.label}</span>;
                  })()}
                </span>
              </div>
              <button
                className="popup-bbox-cta"
                onClick={() => { const s = bboxPopup.site; setBboxPopup(null); openSiteDetail(s); }}
              >
                View details →
              </button>
            </div>
          </Popup>
        )}

        {polyPopup && (
          <Popup
            className="popup-poly"
            longitude={polyPopup.lngLat.lng}
            latitude={polyPopup.lngLat.lat}
            anchor="bottom" offset={10}
            closeOnClick={false}
            onClose={() => setPolyPopup(null)}
            maxWidth="none"
          >
            <div className="popup-poly-content" style={{ '--poly-color': polyPopup.color || '#f59e0b' }}>
              {polyPopup.change_class && (
                <div className="popup-poly-head">
                  <span className="popup-poly-badge">{polyPopup.change_class}</span>
                </div>
              )}
              {polyPopup.loading && (
                <div className="popup-poly-skeleton">
                  <div className="skel-line skel-line-wide" />
                  <div className="skel-grid">
                    <div className="skel-line" /><div className="skel-line" />
                    <div className="skel-line" /><div className="skel-line" />
                  </div>
                </div>
              )}
              {polyPopup.error && (
                <p style={{ margin: 0, color: 'var(--loss)' }}>{polyPopup.error}</p>
              )}
              {polyPopup.props && (
                <>
                  {polyPopup.areaInfo?.area_name && (
                    <p className="popup-poly-name">{polyPopup.areaInfo.area_name}</p>
                  )}
                  <div className="popup-poly-grid">
                    <div className="popup-poly-cell">
                      <span className="popup-poly-cell-label">Area</span>
                      <span className="popup-poly-cell-value">{fmtNumber(polyPopup.props.area_ha)} ha</span>
                    </div>
                    <div className="popup-poly-cell">
                      <span className="popup-poly-cell-label">Volume</span>
                      <span className="popup-poly-cell-value">{fmtVolume(polyPopup.props.volume_m3)}</span>
                    </div>
                    <div className="popup-poly-cell">
                      <span className="popup-poly-cell-label">Zone</span>
                      <span className="popup-poly-cell-value">{polyPopup.props.zone_type ?? '—'}</span>
                    </div>
                    <div className="popup-poly-cell">
                      <span className="popup-poly-cell-label">Org</span>
                      <span className="popup-poly-cell-value">{polyPopup.areaInfo?.org_id ?? '—'}</span>
                    </div>
                  </div>
                </>
              )}
            </div>
          </Popup>
        )}
      </Map>
    </div>
  );
}

function RangeFilter({ label, min, max, step, value, onChange, fmt }) {
  return (
    <div className="filter-row">
      <label className="filter-label">
        <span>{label}</span>
        <strong className="filter-value">{value > 0 ? `≥ ${fmt(value)}` : (typeof value === 'number' && value <= 1 && value > 0 ? fmt(value) : 'any')}</strong>
      </label>
      <input
        type="range"
        className="range-input"
        min={min} max={max} step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

function SignedDualRange({ label, min, max, step, value, onChange, fmt }) {
  const lo = value[0] ?? min;
  const hi = value[1] ?? max;
  const range = max - min;
  const loPct = ((lo - min) / range) * 100;
  const hiPct = ((hi - min) / range) * 100;
  const zeroPct = min < 0 && max > 0 ? ((0 - min) / range) * 100 : null;

  const setLo = (raw) => {
    const v = Math.min(raw, hi);
    onChange([v <= min ? null : v, value[1]]);
  };
  const setHi = (raw) => {
    const v = Math.max(raw, lo);
    onChange([value[0], v >= max ? null : v]);
  };

  const display = value[0] == null && value[1] == null
    ? 'any'
    : `${fmt(lo)} → ${fmt(hi)}`;

  return (
    <div className="filter-row">
      <label className="filter-label">
        <span>{label}</span>
        <strong className="filter-value">{display}</strong>
      </label>
      <div className="srange">
        <div className="srange-track">
          <div className="srange-fill" style={{ left: `${loPct}%`, width: `${Math.max(0, hiPct - loPct)}%` }} />
          {zeroPct != null && <div className="srange-zero" style={{ left: `${zeroPct}%` }} aria-hidden />}
          <input className="srange-input" type="range" min={min} max={max} step={step} value={lo}
            onChange={(e) => setLo(Number(e.target.value))} />
          <input className="srange-input" type="range" min={min} max={max} step={step} value={hi}
            onChange={(e) => setHi(Number(e.target.value))} />
        </div>
        <div className="srange-bounds">
          <span>{fmt(min)}</span>
          {zeroPct != null && <span style={{ position: 'absolute', left: `${zeroPct}%`, transform: 'translateX(-50%)' }}>0</span>}
          <span>{fmt(max)}</span>
        </div>
      </div>
    </div>
  );
}

function McSection({ id, label, openSection, setOpenSection, right, children }) {
  const open = openSection === id;
  return (
    <div className={`mc-section ${open ? 'is-open' : ''}`}>
      <button className="mc-section-head" onClick={() => setOpenSection(open ? null : id)}>
        <span className="mc-section-label">{label}</span>
        <span className="mc-section-right">
          {right}
          <span className="mc-section-chev" aria-hidden>{open ? '▾' : '▸'}</span>
        </span>
      </button>
      {open && <div className="mc-section-body">{children}</div>}
    </div>
  );
}

function RasterToggle({ layer, enabled, onToggle }) {
  return (
    <button
      className={`raster-toggle ${enabled ? 'is-on' : ''}`}
      style={{ '--accent': layer.accent }}
      onClick={onToggle}
    >
      <span className="raster-dot" />
      <span className="raster-text">
        <span className="raster-label">{layer.label}</span>
        <span className="raster-desc">{layer.desc}</span>
      </span>
      <span className={`raster-switch ${enabled ? 'is-on' : ''}`} aria-hidden />
    </button>
  );
}

function DetailSection({ title, children }) {
  return (
    <div className="detail-section">
      <div className="detail-section-title">{title}</div>
      <div className="detail-rows">{children}</div>
    </div>
  );
}

function DetailRow({ label, value, accent }) {
  return (
    <div className="detail-row">
      <span className="detail-row-label">{label}</span>
      <span className="detail-row-value" style={accent ? { color: accent } : undefined}>{value}</span>
    </div>
  );
}


function ControlsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <line x1="4" y1="6"  x2="20" y2="6"  /><circle cx="9" cy="6" r="2" fill="var(--surface)"  />
      <line x1="4" y1="12" x2="20" y2="12" /><circle cx="15" cy="12" r="2" fill="var(--surface)" />
      <line x1="4" y1="18" x2="20" y2="18" /><circle cx="7" cy="18" r="2" fill="var(--surface)"  />
    </svg>
  );
}
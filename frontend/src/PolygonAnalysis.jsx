import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import ConfirmDialog from './ConfirmDialog';

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:9000';

const DEFAULT_CLIENT = 'AriasTech';

const DRAW_STYLES = [
  {
    id: 'gl-draw-polygon-fill',
    type: 'fill',
    filter: ['all', ['==', '$type', 'Polygon']],
    paint: {
      'fill-color':   '#3ba3a7',
      'fill-outline-color': '#1f6e8a',
      'fill-opacity': 0.18,
    },
  },
  {
    id: 'gl-draw-polygon-stroke',
    type: 'line',
    filter: ['all', ['==', '$type', 'Polygon']],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': '#2c8fb1',
      'line-width': 2.2,
      'line-dasharray': [2, 1.4],
    },
  },
  {
    id: 'gl-draw-polygon-and-line-vertex-halo-active',
    type: 'circle',
    filter: ['all', ['==', 'meta', 'vertex']],
    paint: {
      'circle-radius': 6,
      'circle-color':  '#ffffff',
      'circle-stroke-color': '#2c8fb1',
      'circle-stroke-width': 2,
    },
  },
  {
    id: 'gl-draw-polygon-and-line-vertex-active',
    type: 'circle',
    filter: ['all', ['==', 'meta', 'vertex']],
    paint: {
      'circle-radius': 3,
      'circle-color':  '#2c8fb1',
    },
  },
];

function featureToBbox(feature) {
  if (!feature?.geometry?.coordinates?.[0]) return null;
  const ring = feature.geometry.coordinates[0];
  let minLon =  Infinity, minLat =  Infinity;
  let maxLon = -Infinity, maxLat = -Infinity;
  for (const [lon, lat] of ring) {
    if (lon < minLon) minLon = lon;
    if (lat < minLat) minLat = lat;
    if (lon > maxLon) maxLon = lon;
    if (lat > maxLat) maxLat = lat;
  }
  if (!Number.isFinite(minLon)) return null;
  return [minLon, minLat, maxLon, maxLat];
}

function bboxAreaKm2([minLon, minLat, maxLon, maxLat]) {
  const earthR = 6371;
  const toRad  = (d) => (d * Math.PI) / 180;
  const dLat = toRad(maxLat - minLat);
  const dLon = toRad(maxLon - minLon);
  const meanLat = toRad((minLat + maxLat) / 2);
  const h = earthR * dLat;
  const w = earthR * dLon * Math.cos(meanLat);
  return Math.max(0, h * w);
}

export default function PolygonAnalysis({ mapRef, onJobStarted, client = DEFAULT_CLIENT, sidebarOpen = false, mcOpen = false, activeJobCount = 0 }) {
  const drawRef = useRef(null);
  const [ready,        setReady]        = useState(false);
  const [mode,         setMode]         = useState('idle');
  const [bbox,         setBbox]         = useState(null);
  const [areaName,     setAreaName]     = useState('');
  const [submitting,   setSubmitting]   = useState(false);
  const [error,        setError]        = useState(null);
  const [confirm,      setConfirm]      = useState(null);
  const [queued,       setQueued]       = useState(null);

  useEffect(() => {
    let cancelled  = false;
    let mapCleanup = null;
    let rafId      = null;

    const handlers = {};

    const doAttach = (map) => {
      if (cancelled || drawRef.current) return;
      const draw = new MapboxDraw({
        displayControlsDefault: false,
        controls: {},
        styles: DRAW_STYLES,
      });
      map.addControl(draw);
      drawRef.current = draw;

      handlers.onCreate = (e) => {
        const feat = e.features?.[0];
        const next = featureToBbox(feat);
        if (next) {
          setBbox(next);
          setMode('review');
        }
      };
      handlers.onUpdate = (e) => {
        const feat = e.features?.[0];
        const next = featureToBbox(feat);
        if (next) setBbox(next);
      };
      handlers.onDelete = () => {
        setBbox(null);
        setMode('idle');
      };
      handlers.onModeChange = (e) => {
        if (e.mode === 'simple_select' && !drawRef.current?.getAll().features.length) {
          setMode((current) => (current === 'drawing' ? 'idle' : current));
        }
      };

      map.on('draw.create',     handlers.onCreate);
      map.on('draw.update',     handlers.onUpdate);
      map.on('draw.delete',     handlers.onDelete);
      map.on('draw.modechange', handlers.onModeChange);

      setReady(true);

      mapCleanup = () => {
        map.off('draw.create',     handlers.onCreate);
        map.off('draw.update',     handlers.onUpdate);
        map.off('draw.delete',     handlers.onDelete);
        map.off('draw.modechange', handlers.onModeChange);
        try { map.removeControl(draw); } catch { /* map already torn down */ }
        drawRef.current = null;
      };
    };

    const tryAttach = () => {
      if (cancelled || drawRef.current) return;
      const map = mapRef.current?.getMap?.();
      if (!map) {
        rafId = requestAnimationFrame(tryAttach);
        return;
      }
      if (map.isStyleLoaded()) {
        doAttach(map);
        return;
      }
  
      const onLoad = () => {
        map.off('load',  onLoad);
        map.off('idle',  onLoad);
        doAttach(map);
      };
      map.once('load', onLoad);
      map.once('idle', onLoad);
      rafId = requestAnimationFrame(tryAttach);
    };

    tryAttach();

    return () => {
      cancelled = true;
      if (rafId) cancelAnimationFrame(rafId);
      if (mapCleanup) mapCleanup();
    };
  }, [mapRef]);

  const startDraw = useCallback(() => {
    const draw = drawRef.current;
    if (!draw) return;
    draw.deleteAll();
    setBbox(null);
    setError(null);
    setMode('drawing');
    draw.changeMode('draw_polygon');
  }, []);

  const clearDraw = useCallback(() => {
    drawRef.current?.deleteAll();
    setBbox(null);
    setError(null);
    setMode('idle');
  }, []);

  // The actual API call. `payload` is frozen at trigger time so a queued job
  // launches with the bbox the user drew, even after the draw card is cleared.
  const runJob = useCallback(async (payload) => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/jobs`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          bbox:      payload.bbox,
          client,
          area_name: payload.areaName || undefined,
        }),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(text || `HTTP ${res.status}`);
      }
      const data = await res.json();
      onJobStarted?.({ runId: data.run_id, bbox: payload.bbox, client, areaName: payload.areaName });
    } catch (e) {
      setError(e.message || String(e));
      throw e;
    } finally {
      setSubmitting(false);
    }
  }, [client, onJobStarted]);

  const runAndReset = useCallback(async (payload) => {
    try {
      await runJob(payload);
      drawRef.current?.deleteAll();
      setBbox(null);
      setAreaName('');
      setMode('idle');
    } catch { /* error surfaced in the card */ }
  }, [runJob]);

  const submit = useCallback(() => {
    if (!bbox) return;
    const payload = { bbox, areaName: areaName.trim() };
    if (activeJobCount > 0) {
      const n = activeJobCount;
      setConfirm({
        title:  `${n} pipeline${n > 1 ? 's' : ''} already running`,
        detail: 'Processing another area now competes for CPU and can slow every run down. '
              + 'Start it immediately, or queue it to launch automatically once the current '
              + `run${n > 1 ? 's' : ''} finish${n > 1 ? '' : 'es'}.`,
        actions: [
          {
            label: 'Queue it',
            tone:  'ghost',
            onClick: () => {
              setConfirm(null);
              setQueued(payload);
              drawRef.current?.deleteAll();
              setBbox(null);
              setAreaName('');
              setMode('idle');
            },
          },
          {
            label: 'Run now',
            tone:  'primary',
            onClick: () => {
              setConfirm(null);
              runAndReset(payload);
            },
          },
        ],
      });
      return;
    }
    runAndReset(payload);
  }, [bbox, areaName, activeJobCount, runAndReset]);

  // Fire the queued job the moment the last running pipeline finishes.
  useEffect(() => {
    if (queued && activeJobCount === 0 && !submitting) {
      const p = queued;
      setQueued(null);
      runJob(p).catch(() => { /* error shown; queued job is dropped */ });
    }
  }, [queued, activeJobCount, submitting, runJob]);

  const summary = useMemo(() => {
    if (!bbox) return null;
    const km2 = bboxAreaKm2(bbox);
    return {
      minLon: bbox[0], minLat: bbox[1],
      maxLon: bbox[2], maxLat: bbox[3],
      km2,
    };
  }, [bbox]);

  const shiftClass = `${sidebarOpen ? 'sidebar-open' : ''} ${mcOpen ? 'mc-open' : ''}`.trim();

  return (
    <>
      <button
        className={`draw-fab ${mode === 'drawing' ? 'is-active' : ''} ${shiftClass}`}
        onClick={mode === 'drawing' ? clearDraw : startDraw}
        disabled={!ready}
        title={mode === 'drawing' ? 'Cancel drawing' : 'Draw an area to analyse'}
        aria-pressed={mode === 'drawing'}
      >
        <span className="draw-fab-icon" aria-hidden>
          {mode === 'drawing' ? <CloseIcon /> : <PolygonIcon />}
        </span>
        <span className="draw-fab-label">
          {mode === 'drawing' ? 'Cancel' : 'Draw Area'}
        </span>
      </button>

      {mode === 'drawing' && (
        <div className="draw-hint" role="status">
          <span className="draw-hint-dot" aria-hidden />
          Click on the map to add corners. Double-click to finish.
        </div>
      )}

      {queued && (
        <div className={`draw-queued ${shiftClass}`} role="status">
          <span className="draw-queued-dot" aria-hidden />
          <span className="draw-queued-text">
            Area queued — starts when the current run{activeJobCount > 1 ? 's' : ''} finish{activeJobCount > 1 ? '' : 'es'}.
          </span>
          <button
            type="button"
            className="draw-queued-cancel"
            onClick={() => setQueued(null)}
            aria-label="Cancel queued area"
          >×</button>
        </div>
      )}

      {mode === 'review' && summary && (
        <div className={`draw-card ${shiftClass}`}>
          <div className="draw-card-head">
            <span className="draw-card-eyebrow">Selected area</span>
            <button className="draw-card-close" onClick={clearDraw} aria-label="Clear selection">×</button>
          </div>

          <div className="draw-card-area">
            <span className="draw-card-area-value">{summary.km2.toFixed(2)}</span>
            <span className="draw-card-area-unit">km²</span>
          </div>

          <div className="draw-card-coords">
            <div className="draw-coord">
              <span className="draw-coord-label">SW</span>
              <span className="draw-coord-value">{summary.minLat.toFixed(4)}, {summary.minLon.toFixed(4)}</span>
            </div>
            <div className="draw-coord">
              <span className="draw-coord-label">NE</span>
              <span className="draw-coord-value">{summary.maxLat.toFixed(4)}, {summary.maxLon.toFixed(4)}</span>
            </div>
          </div>

          <label className="draw-card-field">
            <span className="draw-card-field-label">Area name (optional)</span>
            <input
              type="text"
              className="draw-card-input"
              placeholder="auto-resolved via reverse geocoding"
              maxLength={64}
              value={areaName}
              onChange={(e) => setAreaName(e.target.value)}
              disabled={submitting}
            />
          </label>

          {error && <div className="draw-card-error">{error}</div>}

          <div className="draw-card-actions">
            <button
              type="button"
              className="draw-card-btn draw-card-btn-ghost"
              onClick={clearDraw}
              disabled={submitting}
            >
              Redraw
            </button>
            <button
              type="button"
              className="draw-card-btn draw-card-btn-primary"
              onClick={submit}
              disabled={submitting}
            >
              {submitting ? (
                <>
                  <span className="draw-spinner" aria-hidden />
                  Starting…
                </>
              ) : (
                <>
                  <ProcessIcon />
                  Process area
                </>
              )}
            </button>
          </div>
        </div>
      )}

      <ConfirmDialog confirm={confirm} onCancel={() => setConfirm(null)} />
    </>
  );
}

function PolygonIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polygon points="4 7 12 3 20 8 18 18 6 18" />
      <circle cx="4"  cy="7"  r="1.5" fill="currentColor" />
      <circle cx="12" cy="3"  r="1.5" fill="currentColor" />
      <circle cx="20" cy="8"  r="1.5" fill="currentColor" />
      <circle cx="18" cy="18" r="1.5" fill="currentColor" />
      <circle cx="6"  cy="18" r="1.5" fill="currentColor" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden>
      <line x1="6"  y1="6"  x2="18" y2="18" />
      <line x1="18" y1="6"  x2="6"  y2="18" />
    </svg>
  );
}

function ProcessIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="5 12 10 17 19 7" />
    </svg>
  );
}

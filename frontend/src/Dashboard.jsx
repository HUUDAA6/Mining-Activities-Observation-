import { useEffect, useMemo, useState, useCallback } from 'react';
import Map, { Source, Layer, Popup, NavigationControl } from 'react-map-gl/mapbox';
import 'mapbox-gl/dist/mapbox-gl.css';

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;
const API_BASE     = import.meta.env.VITE_API_BASE ?? 'http://localhost:9000';

/* MAI colour scale : green = Low, amber = Medium, red = High. */
const MAI_COLORS = { Low: '#10b981', Medium: '#f59e0b', High: '#ef4444' };

const fmtNum = (v, d = 0) =>
  v == null || Number.isNaN(Number(v))
    ? '—'
    : Number(v).toLocaleString(undefined, { maximumFractionDigits: d });

const fmtVol = (v) => {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const a = Math.abs(n);
  const s = n < 0 ? '−' : '';
  if (a >= 1e9) return `${s}${(a / 1e9).toFixed(2)} B m³`;
  if (a >= 1e6) return `${s}${(a / 1e6).toFixed(2)} M m³`;
  if (a >= 1e3) return `${s}${(a / 1e3).toFixed(1)} k m³`;
  return `${s}${a.toFixed(0)} m³`;
};

const fmtVolShort = (v) => {
  if (v == null || Number.isNaN(Number(v))) return '0';
  const n = Number(v);
  const a = Math.abs(n);
  const s = n < 0 ? '−' : '';
  if (a >= 1e9) return `${s}${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${s}${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${s}${(a / 1e3).toFixed(0)}k`;
  return `${s}${a.toFixed(0)}`;
};

const fmtDate = (s) => {
  if (!s) return '—';
  try { return new Date(s).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }); }
  catch { return s; }
};

/* Round up to a clean axis bound  */
function niceCeil(v) {
  if (v <= 0) return 1;
  const exp = Math.floor(Math.log10(v));
  const base = Math.pow(10, exp);
  const f = v / base;
  const nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nf * base;
}

export default function GlobalDashboard({ navigate, theme }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`${API_BASE}/api/global/overview`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [reloadKey]);

  return (
    <div className="gdash">
      <div className="gdash-scroll">
        <div className="gdash-head">
          <div>
            <h1 className="gdash-title">Global Mining Overview</h1>
            <p className="gdash-subtitle">
              Every monitored site &amp; country at a glance
              {data?.generated_at && <> · updated {fmtDate(data.generated_at)}</>}
            </p>
          </div>
          <div className="gdash-head-actions">
            <button className="gdash-ghost-btn" onClick={() => navigate?.('map')}>← Map</button>
            <button
              className="gdash-ghost-btn"
              onClick={() => setReloadKey((k) => k + 1)}
              disabled={loading}
            >
              {loading ? 'Refreshing…' : '↻ Refresh'}
            </button>
          </div>
        </div>

        {loading && <GlobalSkeleton />}
        {error && !loading && (
          <div className="gdash-error">
            Failed to load the global dashboard: {error}
            <button className="gdash-ghost-btn" onClick={() => setReloadKey((k) => k + 1)}>Retry</button>
          </div>
        )}
        {data && !loading && !error && <DashboardBody data={data} theme={theme} />}
      </div>
    </div>
  );
}

function DashboardBody({ data, theme }) {
  const t = data.totals || {};
  const sites = data.sites || [];
  const byCountry = data.by_country || [];
  const risk = data.risk_distribution || [];

  return (
    <>
      {/* ---- Headline KPIs ---- */}
      <div className="gdash-kpi-grid">
        <KpiCard
          tone="brand"
          label="Total Monitored Area"
          value={fmtNum(t.total_area_ha, 0)}
          unit="ha"
          sub={`${fmtNum(t.n_sites, 0)} sites · ${fmtNum(t.n_countries, 0)} countries`}
          icon="area"
        />
        <KpiCard
          tone="loss"
          label="Total Volume Excavated"
          value={(Math.abs(t.vol_excavated_m3 || 0) / 1e6).toFixed(2)}
          unit="M m³"
          sub={`Deposited ${(Math.abs(t.vol_deposited_m3 || 0) / 1e6).toFixed(2)} M m³`}
          icon="excavation"
        />
        <KpiCard
          tone="accent"
          label="Active vs Inactive Sites"
          value={`${fmtNum(t.active_sites, 0)} / ${fmtNum(t.inactive_sites, 0)}`}
          sub="Medium+High vs Low activity"
          icon="activity"
        >
          <ActiveBar active={t.active_sites || 0} inactive={t.inactive_sites || 0} />
        </KpiCard>
        <KpiCard
          tone={t.net_vol_sign === 'excavation' ? 'loss' : t.net_vol_sign === 'deposition' ? 'gain' : 'muted'}
          label="Net Global Cut / Fill"
          value={fmtVol(t.net_vol_m3)}
          sub={
            t.net_vol_sign === 'excavation' ? 'Net excavation worldwide'
            : t.net_vol_sign === 'deposition' ? 'Net deposition worldwide'
            : 'Globally balanced'
          }
          icon="balance"
        />
      </div>

      {/* ---- Secondary KPIs ---- */}
      <div className="gdash-mini-row">
        <MiniKpi label="Total Disturbed Area" value={`${fmtNum(t.total_disturbed_ha, 0)} ha`} sub={`${fmtNum(t.disturbed_pct, 1)}% of monitored`} />
        <MiniKpi label="Material Moved" value={`${(Math.abs(t.material_moved_m3 || 0) / 1e6).toFixed(2)} M m³`} sub="Excavated + deposited" />
        <MiniKpi label="Global Cut / Fill Ratio" value={fmtNum(t.cut_fill_ratio, 2)} sub={t.cut_fill_ratio > 1 ? 'Excavation-dominant' : 'Deposition-dominant'} />
        <MiniKpi label="High-Risk Sites" value={fmtNum(t.mai_counts?.High, 0)} sub={`${fmtNum(t.mai_counts?.Medium, 0)} medium · ${fmtNum(t.mai_counts?.Low, 0)} low`} />
      </div>

      {/* ---- Bubble map ---- */}
      <section className="gdash-card">
        <CardHead title="Global Activity Map" sub="Bubble size = disturbed area · colour = Mining Activity Index" />
        <BubbleMap sites={sites} theme={theme} />
        <div className="gdash-map-legend">
          <span className="gdash-legend-title">Activity</span>
          <LegendDot color={MAI_COLORS.Low} label="Low" />
          <LegendDot color={MAI_COLORS.Medium} label="Medium" />
          <LegendDot color={MAI_COLORS.High} label="High" />
          <span className="gdash-legend-sep" />
          <span className="gdash-legend-title">Disturbed area</span>
          <span className="gdash-legend-bubble gdash-legend-bubble-sm" />
          <span className="gdash-legend-bubble gdash-legend-bubble-md" />
          <span className="gdash-legend-bubble gdash-legend-bubble-lg" />
          <span className="gdash-legend-cap">small → large</span>
        </div>
      </section>

      {/* ---- Country bars + risk donut ---- */}
      <div className="gdash-grid-2">
        <section className="gdash-card">
          <CardHead title="Volume by Country" sub="Stacked excavation (cut) vs deposition (fill)" />
          <CountryStackedBars rows={byCountry} />
          <div className="gdash-inline-legend">
            <LegendDot color="var(--loss)" label="Excavation (cut)" />
            <LegendDot color="var(--brand)" label="Deposition (fill)" />
          </div>
        </section>

        <section className="gdash-card">
          <CardHead title="Risk Distribution" sub="Sites by Mining Activity Index" />
          <RiskDonut risk={risk} total={t.n_sites || 0} />
          <div className="gdash-donut-legend">
            {risk.map((r) => (
              <DonutLegendRow
                key={r.level}
                color={MAI_COLORS[r.level]}
                label={`${r.level} activity`}
                count={r.count}
                total={t.n_sites || 0}
              />
            ))}
          </div>
        </section>
      </div>

      {/* ---- Top sites table (replaces the old insight cards) ---- */}
      <section className="gdash-card">
        <CardHead title="Sites by Impact" sub="Ranked by total disturbed area" />
        <TopSitesTable sites={sites} />
      </section>

      {/* ---- Scatter ---- */}
      <section className="gdash-card">
        <CardHead title="Footprint vs Intensity" sub="Area disturbed (ha) vs net volume change (m³)" />
        <ScatterPlot sites={sites} />
      </section>

      <footer className="gdash-footer">
        Powered by AriasTechSolutions. All rights reserved 2026.
      </footer>
    </>
  );
}

function KpiCard({ tone = 'brand', label, value, unit, sub, icon, children }) {
  return (
    <div className={`gdash-kpi gdash-kpi-${tone}`}>
      <div className="gdash-kpi-main">
        <span className="gdash-kpi-label">{label}</span>
        <div className="gdash-kpi-value">
          {value}{unit && <span className="gdash-kpi-unit">{unit}</span>}
        </div>
        {children}
        {sub && <div className="gdash-kpi-sub">{sub}</div>}
      </div>
      <span className="gdash-kpi-badge" aria-hidden>{kpiIcon(icon)}</span>
    </div>
  );
}

function MiniKpi({ label, value, sub }) {
  return (
    <div className="gdash-mini">
      <span className="gdash-mini-label">{label}</span>
      <span className="gdash-mini-value">{value}</span>
      <span className="gdash-mini-sub">{sub}</span>
    </div>
  );
}

function ActiveBar({ active, inactive }) {
  const total = active + inactive || 1;
  const pct = (active / total) * 100;
  return (
    <div className="gdash-activebar" title={`${active} active of ${total}`}>
      <div className="gdash-activebar-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

function CardHead({ title, sub }) {
  return (
    <div className="gdash-card-head">
      <h3 className="gdash-card-title">{title}</h3>
      {sub && <span className="gdash-card-sub">{sub}</span>}
    </div>
  );
}

function LegendDot({ color, label }) {
  return (
    <span className="gdash-legend-item">
      <span className="gdash-legend-dot" style={{ background: color }} />
      {label}
    </span>
  );
}

function DonutLegendRow({ color, label, count, total }) {
  const pct = total ? (count / total) * 100 : 0;
  return (
    <div className="gdash-legend-row">
      <span className="gdash-legend-dot" style={{ background: color }} />
      <span className="gdash-legend-rlabel">{label}</span>
      <span className="gdash-legend-rval">{count} · {pct.toFixed(0)}%</span>
    </div>
  );
}

function TopSitesTable({ sites }) {
  const rows = useMemo(
    () => [...(sites || [])].sort((a, b) => (b.disturbed_ha || 0) - (a.disturbed_ha || 0)).slice(0, 8),
    [sites]
  );
  if (rows.length === 0) return <div className="gdash-empty">No sites yet.</div>;

  return (
    <div className="gdash-table-wrap">
      <table className="gdash-table">
        <thead>
          <tr>
            <th>Site</th>
            <th>Country</th>
            <th className="gdash-num">Disturbed (ha)</th>
            <th className="gdash-num">Net volume</th>
            <th>Activity</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td className="gdash-td-name">{s.area_name}</td>
              <td className="gdash-td-muted">{s.country}</td>
              <td className="gdash-num">{fmtNum(s.disturbed_ha, 0)}</td>
              <td className="gdash-num">{fmtVol(s.net_vol_m3)}</td>
              <td>
                <span
                  className="gdash-badge"
                  style={{
                    color: MAI_COLORS[s.activity_level],
                    background: `color-mix(in srgb, ${MAI_COLORS[s.activity_level]} 14%, transparent)`,
                  }}
                >
                  {s.activity_level}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


function BubbleMap({ sites, theme }) {
  const [hover, setHover] = useState(null);
  const mapStyle = theme === 'dark'
    ? 'mapbox://styles/mapbox/dark-v11'
    : 'mapbox://styles/mapbox/light-v11';

  const pts = useMemo(
    () => sites.filter((s) => Number.isFinite(s.lon) && Number.isFinite(s.lat)),
    [sites]
  );

  const geojson = useMemo(() => {
    const maxDist = Math.max(1, ...pts.map((s) => s.disturbed_ha || 0));
    return {
      type: 'FeatureCollection',
      features: pts.map((s) => ({
        type: 'Feature',
        properties: {
          name: s.area_name,
          country: s.country,
          level: s.activity_level || 'Low',
          color: MAI_COLORS[s.activity_level] || MAI_COLORS.Low,
          r: 6 + 30 * Math.sqrt((s.disturbed_ha || 0) / maxDist),
          disturbed: s.disturbed_ha,
          net: s.net_vol_m3,
        },
        geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
      })),
    };
  }, [pts]);

  const onMove = useCallback((e) => {
    const f = e.features?.[0];
    if (f) {
      setHover({
        lng: f.geometry.coordinates[0],
        lat: f.geometry.coordinates[1],
        props: f.properties,
      });
    }
  }, []);

  if (!MAPBOX_TOKEN) return <div className="gdash-empty">Set VITE_MAPBOX_TOKEN to enable the map.</div>;
  if (pts.length === 0) return <div className="gdash-empty">No geolocated sites to plot yet.</div>;

  return (
    <div className="gdash-map">
      <Map
        initialViewState={{ longitude: 5, latitude: 25, zoom: 1.3 }}
        mapStyle={mapStyle}
        mapboxAccessToken={MAPBOX_TOKEN}
        interactiveLayerIds={['bubbles']}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        style={{ width: '100%', height: '100%' }}
      >
        <NavigationControl position="bottom-right" showCompass={false} />
        <Source id="sites" type="geojson" data={geojson}>
          <Layer
            id="bubbles-halo"
            type="circle"
            paint={{
              'circle-radius': ['+', ['get', 'r'], 3],
              'circle-color': ['get', 'color'],
              'circle-opacity': 0.12,
            }}
          />
          <Layer
            id="bubbles"
            type="circle"
            paint={{
              'circle-radius': ['get', 'r'],
              'circle-color': ['get', 'color'],
              'circle-opacity': 0.5,
              'circle-stroke-color': ['get', 'color'],
              'circle-stroke-width': 1.5,
            }}
          />
        </Source>

        {hover && (
          <Popup
            longitude={hover.lng}
            latitude={hover.lat}
            anchor="bottom"
            offset={14}
            closeButton={false}
            closeOnClick={false}
            className="gdash-popup"
          >
            <div className="gdash-popup-body">
              <div className="gdash-popup-name">{hover.props.name}</div>
              <div className="gdash-popup-country">{hover.props.country}</div>
              <div className="gdash-popup-rows">
                <span>Activity</span>
                <strong style={{ color: hover.props.color }}>{hover.props.level}</strong>
                <span>Disturbed</span>
                <strong>{fmtNum(hover.props.disturbed, 0)} ha</strong>
                <span>Net volume</span>
                <strong>{fmtVol(hover.props.net)}</strong>
              </div>
            </div>
          </Popup>
        )}
      </Map>
    </div>
  );
}

function CountryStackedBars({ rows }) {
  const W = 680, H = 300, PAD_L = 56, PAD_R = 14, PAD_T = 14, PAD_B = 60;
  const data = (rows || []).slice(0, 10);
  if (data.length === 0) return <div className="gdash-empty">No country data.</div>;

  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;
  const maxTotal = Math.max(
    1,
    ...data.map((d) => Math.abs(d.vol_excavated_m3 || 0) + Math.abs(d.vol_deposited_m3 || 0))
  );
  const yMax = niceCeil(maxTotal);
  const band = innerW / data.length;
  const barW = Math.min(46, band * 0.55);
  const yScale = (v) => PAD_T + innerH - (v / yMax) * innerH;
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => yMax * f);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="gdash-chart" preserveAspectRatio="xMidYMid meet">
      {yTicks.map((v, i) => (
        <g key={i}>
          <line x1={PAD_L} x2={W - PAD_R} y1={yScale(v)} y2={yScale(v)} stroke="var(--line)" strokeOpacity="0.6" />
          <text x={PAD_L - 8} y={yScale(v) + 3} textAnchor="end" className="gdash-axis">{fmtVolShort(v)}</text>
        </g>
      ))}

      {data.map((d, i) => {
        const cx = PAD_L + band * i + band / 2;
        const exc = Math.abs(d.vol_excavated_m3 || 0);
        const dep = Math.abs(d.vol_deposited_m3 || 0);
        const hDep = (dep / yMax) * innerH;
        const hExc = (exc / yMax) * innerH;
        const yDep = PAD_T + innerH - hDep;
        const yExc = yDep - hExc;
        const label = d.country.length > 9 ? d.country.slice(0, 8) + '…' : d.country;
        return (
          <g key={d.country}>
            <rect x={cx - barW / 2} y={yDep} width={barW} height={Math.max(0, hDep)} fill="var(--brand)" rx="3">
              <title>{`${d.country} — deposition ${fmtVol(dep)}`}</title>
            </rect>
            <rect x={cx - barW / 2} y={yExc} width={barW} height={Math.max(0, hExc)} fill="var(--loss)" rx="3">
              <title>{`${d.country} — excavation ${fmtVol(exc)}`}</title>
            </rect>
            <text x={cx} y={H - PAD_B + 18} textAnchor="middle" className="gdash-axis gdash-axis-x"
                  transform={`rotate(26 ${cx} ${H - PAD_B + 18})`}>{label}</text>
          </g>
        );
      })}

      <line x1={PAD_L} x2={W - PAD_R} y1={PAD_T + innerH} y2={PAD_T + innerH} stroke="var(--line)" />
    </svg>
  );
}

function ScatterPlot({ sites }) {
  const W = 680, H = 340, PAD_L = 60, PAD_R = 20, PAD_T = 20, PAD_B = 46;
  const pts = (sites || []).filter((s) => (s.disturbed_ha || 0) > 0 || s.net_vol_m3);
  if (pts.length === 0) return <div className="gdash-empty">No site metrics to plot.</div>;

  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;
  const xMax = niceCeil(Math.max(1, ...pts.map((s) => s.disturbed_ha || 0)));
  const yAbs = niceCeil(Math.max(1, ...pts.map((s) => Math.abs(s.net_vol_m3 || 0))));
  const midY = PAD_T + innerH / 2;
  const xScale = (v) => PAD_L + (v / xMax) * innerW;
  const yScale = (v) => midY - (v / yAbs) * (innerH / 2);

  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => xMax * f);
  const yTicks = [-1, -0.5, 0, 0.5, 1].map((f) => yAbs * f);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="gdash-chart" preserveAspectRatio="xMidYMid meet">
      {/* faint deposition / excavation halves */}
      <rect x={PAD_L} y={PAD_T} width={innerW} height={innerH / 2} fill="var(--brand)" opacity="0.04" />
      <rect x={PAD_L} y={midY} width={innerW} height={innerH / 2} fill="var(--loss)" opacity="0.04" />

      {yTicks.map((v, i) => (
        <g key={`y${i}`}>
          <line x1={PAD_L} x2={W - PAD_R} y1={yScale(v)} y2={yScale(v)} stroke="var(--line)" strokeOpacity={v === 0 ? 0.9 : 0.45} />
          <text x={PAD_L - 8} y={yScale(v) + 3} textAnchor="end" className="gdash-axis">{fmtVolShort(v)}</text>
        </g>
      ))}
      {xTicks.map((v, i) => (
        <g key={`x${i}`}>
          <line x1={xScale(v)} x2={xScale(v)} y1={PAD_T} y2={PAD_T + innerH} stroke="var(--line)" strokeOpacity="0.3" />
          <text x={xScale(v)} y={PAD_T + innerH + 18} textAnchor="middle" className="gdash-axis">{fmtNum(v, 0)}</text>
        </g>
      ))}

      {pts.map((s) => (
        <circle
          key={s.id}
          cx={xScale(s.disturbed_ha || 0)}
          cy={yScale(s.net_vol_m3 || 0)}
          r="6"
          fill={MAI_COLORS[s.activity_level] || MAI_COLORS.Low}
          fillOpacity="0.55"
          stroke={MAI_COLORS[s.activity_level] || MAI_COLORS.Low}
          strokeWidth="1.4"
        >
          <title>{`${s.area_name} (${s.country})\nDisturbed ${fmtNum(s.disturbed_ha, 0)} ha · Net ${fmtVol(s.net_vol_m3)}`}</title>
        </circle>
      ))}

      <text x={PAD_L + innerW / 2} y={H - 6} textAnchor="middle" className="gdash-axis-strong">Area disturbed (ha)</text>
      <text x={16} y={midY} textAnchor="middle" className="gdash-axis-strong" transform={`rotate(-90 16 ${midY})`}>Net volume (m³)</text>
    </svg>
  );
}


function RiskDonut({ risk, total }) {
  const W = 200, H = 200, cx = W / 2, cy = H / 2, R = 84, r = 54;
  const slices = (risk || []).filter((s) => s.count > 0);
  const sum = slices.reduce((a, s) => a + s.count, 0) || 1;
  if (slices.length === 0) return <div className="gdash-empty">No sites yet.</div>;

  let start = -Math.PI / 2;
  const arcs = slices.map((s) => {
    const angle = (s.count / sum) * 2 * Math.PI;
    const end = start + angle;
    const large = angle > Math.PI ? 1 : 0;
    const x0 = cx + Math.cos(start) * R, y0 = cy + Math.sin(start) * R;
    const x1 = cx + Math.cos(end) * R, y1 = cy + Math.sin(end) * R;
    const xi0 = cx + Math.cos(end) * r, yi0 = cy + Math.sin(end) * r;
    const xi1 = cx + Math.cos(start) * r, yi1 = cy + Math.sin(start) * r;
    const d = `M ${x0} ${y0} A ${R} ${R} 0 ${large} 1 ${x1} ${y1} L ${xi0} ${yi0} A ${r} ${r} 0 ${large} 0 ${xi1} ${yi1} Z`;
    start = end;
    return { d, color: MAI_COLORS[s.level], label: s.level, count: s.count };
  });

  return (
    <div className="gdash-donut-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="gdash-donut" preserveAspectRatio="xMidYMid meet">
        {arcs.map((a, i) => (
          <path key={i} d={a.d} fill={a.color} opacity="0.9">
            <title>{`${a.label}: ${a.count}`}</title>
          </path>
        ))}
        <circle cx={cx} cy={cy} r={r - 1} fill="var(--surface)" />
        <text x={cx} y={cy - 2} textAnchor="middle" className="gdash-donut-headline">{total}</text>
        <text x={cx} y={cy + 16} textAnchor="middle" className="gdash-donut-sub">sites</text>
      </svg>
    </div>
  );
}

/* ============================ icons ============================ */

function kpiIcon(name) {
  const c = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round' };
  switch (name) {
    case 'area':
      return (
        <svg viewBox="0 0 24 24" {...c}>
          <path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2z" />
          <line x1="9" y1="4" x2="9" y2="18" />
          <line x1="15" y1="6" x2="15" y2="20" />
        </svg>
      );
    case 'excavation':
      return (
        <svg viewBox="0 0 24 24" {...c}>
          <path d="M4 7c5-4 11-4 16 0" />
          <path d="M12 5.5 12 12" />
          <path d="M11 12h2l1 8h-4l1-8z" />
        </svg>
      );
    case 'activity':
      return <svg viewBox="0 0 24 24" {...c}><polyline points="3 12 8 12 11 5 15 19 17 12 21 12" /></svg>;
    case 'balance':
      return (
        <svg viewBox="0 0 24 24" {...c}>
          <line x1="12" y1="4" x2="12" y2="20" />
          <line x1="7" y1="20" x2="17" y2="20" />
          <path d="M12 6 5 9m7-3 7 3" />
          <path d="M2.5 13 5 8.5 7.5 13a2.5 2.5 0 0 1-5 0z" />
          <path d="M16.5 13 19 8.5 21.5 13a2.5 2.5 0 0 1-5 0z" />
        </svg>
      );
    default:
      return null;
  }
}

function GlobalSkeleton() {
  return (
    <>
      <div className="gdash-kpi-grid">
        {[0, 1, 2, 3].map((i) => <div key={i} className="gdash-kpi gdash-skel" />)}
      </div>
      <div className="gdash-card gdash-skel" style={{ height: 420 }} />
      <div className="gdash-grid-2">
        <div className="gdash-card gdash-skel" style={{ height: 320 }} />
        <div className="gdash-card gdash-skel" style={{ height: 320 }} />
      </div>
    </>
  );
}

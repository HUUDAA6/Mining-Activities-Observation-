import { useEffect, useRef, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:9000';

const fmtNum = (v, d = 2) =>
  v == null || Number.isNaN(Number(v))
    ? '—'
    : Number(v).toLocaleString(undefined, { maximumFractionDigits: d });

const fmtVol = (v) => {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const a = Math.abs(n);
  const s = n < 0 ? '−' : n > 0 ? '+' : '';
  if (a >= 1e9) return `${s}${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${s}${(a / 1e3).toFixed(1)}k`;
  return `${s}${a.toFixed(0)}`;
};

const fmtDate = (s) => {
  if (!s) return '—';
  try {
    return new Date(s).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return s;
  }
};

export default function Dashboardareas({ site, onBack, changeClasses = [], hiddenClasses, onToggleClass }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    // Parent passes a key on bbox_id change, so the component remounts and state starts fresh, no synchronous resets here.
    if (!site?.bbox_id) return;
    let cancelled = false;
    fetch(`${API_BASE}/api/dashboard/${encodeURIComponent(site.bbox_id)}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [site?.bbox_id]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTo({ top: 0 });
  }, []);

  return (
    <div className="dash-wrap">
      <div className="dash-topbar">
        <button className="dash-back" onClick={onBack} aria-label="Back to sites">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="19" y1="12" x2="5" y2="12" />
            <polyline points="12 19 5 12 12 5" />
          </svg>
        </button>
        <div className="dash-topbar-title">
          <span className="dash-topbar-label">Site Details</span>
          <span className="dash-topbar-name">{site?.area_name || site?.bbox_id}</span>
        </div>
        <div className="dash-topbar-spacer" />
        <span className="dash-id-chip" title={data?.site?.country || site?.country || ''}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
            <circle cx="12" cy="10" r="3" />
          </svg>
          {data?.site?.country || site?.country || '—'}
        </span>
      </div>

      <div ref={scrollRef} className="dash-scroll">
        {loading && <DashSkeleton />}
        {error && <div className="dash-error">Failed to load dashboard: {error}</div>}
        {data && !error && (
          <DashboardBody
            data={data}
            changeClasses={changeClasses}
            hiddenClasses={hiddenClasses}
            onToggleClass={onToggleClass}
          />
        )}
      </div>
    </div>
  );
}

function DashboardBody({ data, changeClasses, hiddenClasses, onToggleClass }) {
  const { kpis = {}, histogram, mai = {}, gauge = {}, donut = {}, stable_terrain = {}, vectorization = {} } = data;
  const s = data.site || {};

  return (
    <>
      <div className="dash-kpi-row">
        <KpiCard
          label="Mining Activity Level"
          value={mai.activity_level || 'Low'}
          accent={mai.activity_level === 'High' ? 'loss' : mai.activity_level === 'Medium' ? 'warn' : 'brand'}
        />
        <KpiCard label="Total Area" value={`${fmtNum(kpis.total_area_ha, 1)} ha`} accent="muted" />
        <KpiCard
          label="Disturbed"
          value={`${fmtNum(kpis.disturbed_pct, 1)}%`}
          sub={`${fmtNum(kpis.disturbed_area_ha, 1)} ha`}
          accent="warn"
        />
      </div>

      <div className="dash-kpi-row dash-kpi-row-2">
        <KpiCard
          label="Net Volume"
          value={`${fmtVol(kpis.net_vol_m3)} m³`}
          sub={kpis.net_vol_sign === 'deposition' ? '↑ Net deposition' : kpis.net_vol_sign === 'excavation' ? '↓ Net excavation' : 'Balanced'}
          accent={kpis.net_vol_sign === 'deposition' ? 'gain' : kpis.net_vol_sign === 'excavation' ? 'loss' : 'muted'}
        />
        <KpiCard
          label="Max Excavation"
          value={`${fmtNum(kpis.max_excavation_m, 2)} m`}
          sub={`peak fill ${fmtNum(kpis.max_deposition_m, 2)} m`}
          accent="loss"
        />
        <KpiCard
          label="Cut / Fill Ratio"
          value={fmtNum(kpis.cut_fill_ratio, 2)}
          sub={kpis.cut_fill_ratio > 1 ? 'Excavation-dominant' : 'Deposition-dominant'}
          accent={kpis.cut_fill_ratio > 1 ? 'loss' : 'gain'}
        />
      </div>

      <section className="dash-section">
        <div className="dash-section-head">
          <h3 className="dash-section-title">More Details</h3>
        </div>
        <div className="dash-detail-grid">
          <DetailLine icon="calendar" label="Run at" value={fmtDate(s.run_at)} />
          <DetailLine icon="period" label="Period" value={`${fmtDate(s.period_start)} → ${fmtDate(s.period_end)}`} />
          <DetailLine icon="org" label="Client" value={s.org_id || s.client || ':'} />
          <DetailLine icon="bbox" label="BBOX ID" value={s.bbox_id} mono />
          <DetailLine icon="pin" label="Country" value={s.country || ':'} />
          <DetailLine icon="zones" label="Zones" value={`${vectorization?.n_zones ?? 0} (↑${vectorization?.n_gain_zones ?? 0} · ↓${vectorization?.n_loss_zones ?? 0})`} />
          <DetailLine icon="lod" label="LoD (95%)" value={`±${fmtNum(kpis.lod_m, 2)} m`} />
          <DetailLine icon="pixels" label="Valid pixels" value={fmtNum(kpis.n_pixels, 0)} />
        </div>
      </section>

      {changeClasses && changeClasses.length > 0 && onToggleClass && (
        <section className="dash-section">
          <div className="dash-section-head">
            <h3 className="dash-section-title">Change Classes</h3>
            <span className="dash-section-sub">Click to hide a class on the map</span>
          </div>
          <div className="dash-chip-row">
            {changeClasses.map(({ change_class, zone_type, color }) => {
              const active = !(hiddenClasses && hiddenClasses.has && hiddenClasses.has(change_class));
              return (
                <button
                  key={change_class}
                  type="button"
                  className={`dash-chip dash-chip-${zone_type || 'unknown'}${active ? ' is-active' : ''}`}
                  style={{ '--chip-color': color || 'var(--muted)' }}
                  onClick={() => onToggleClass(change_class)}
                  title={active ? `Hide ${change_class}` : `Show ${change_class}`}
                >
                  <span className="dash-chip-dot" />
                  <span className="dash-chip-text">{change_class}</span>
                </button>
              );
            })}
          </div>
        </section>
      )}

      <section className="dash-section">
        <div className="dash-section-head">
          <h3 className="dash-section-title">Elevation Change Distribution</h3>
          <span className="dash-section-sub">Shaded band = Level of Detection (±{fmtNum(histogram.lod_m, 2)} m)</span>
        </div>
        <HistogramChart histogram={histogram} />
      </section>

      <div className="dash-grid-2">
        <section className="dash-section dash-card">
          <div className="dash-section-head">
            <h3 className="dash-section-title">Mining Activity Index</h3>
            <span className={`dash-mai-level dash-mai-level-${(mai.activity_level || 'low').toLowerCase()}`}>
              {mai.activity_level || 'Low'} activity
            </span>
          </div>
          <RadarChart mai={mai} />
          <div className="dash-mai-legend">
            <MaiPill label="Economic / Production"  v={mai.economic}      color="var(--brand)" />
            <MaiPill label="Environmental Footprint" v={mai.environmental} color="var(--gain)" />
            <MaiPill label="Early Warning / Risk"    v={mai.risk}          color="var(--loss)" />
          </div>
          <div className="dash-mai-foot">
            <span>Standard MAI</span>
            <strong className="dash-mai-foot-value">{fmtNum((mai.standard || 0) * 100, 0)}/100</strong>
          </div>
        </section>

        <section className="dash-section dash-card">
          <div className="dash-section-head">
            <h3 className="dash-section-title">Cut / Fill Regime</h3>
            <span className="dash-section-sub">{gauge.regime === 'excavation' ? 'Excavation-dominant' : gauge.regime === 'deposition' ? 'Deposition-dominant' : 'Balanced'}</span>
          </div>
          <GaugeChart gauge={gauge} />
          <div className="dash-vol-rows">
            <VolRow label="Gain (Fill)" value={`${fmtVol(kpis.vol_gain_m3 ?? 0)} m³`} color="var(--gain)" />
            <VolRow label="Loss (Cut)" value={`${fmtVol(kpis.vol_loss_m3 ?? 0)} m³`} color="var(--loss)" />
            <VolRow label="Net" value={`${fmtVol(kpis.net_vol_m3 ?? 0)} m³`} strong />
          </div>
        </section>
      </div>

      <section className="dash-section">
        <div className="dash-section-head">
          <h3 className="dash-section-title">Area Breakdown</h3>
          <span className="dash-section-sub">Polygon-area composition</span>
        </div>
        <div className="dash-donut-wrap">
          <DonutChart donut={donut} />
          <div className="dash-donut-legend">
            <DonutLegendRow color="var(--muted)" label="Stable terrain" pct={donut.stable_pct} />
            <DonutLegendRow color="var(--loss)" label="Loss area" pct={donut.loss_pct} />
            <DonutLegendRow color="var(--gain)" label="Gain area" pct={donut.gain_pct} />
            {donut.other_pct > 0.5 && (
              <DonutLegendRow color="var(--brand)" label="Other / sub-LoD change" pct={donut.other_pct} />
            )}
          </div>
        </div>
      </section>

      <section className="dash-section dash-stable">
        <div className="dash-section-head">
          <h3 className="dash-section-title">Measurement Reliability</h3>
          <span className="dash-section-sub">Stable-terrain co-registration check</span>
        </div>

        <div className="dash-stable-hero">
          <div className="dash-stable-ring" style={{ '--pct': `${Math.min(100, stable_terrain.stable_pct || 0)}%` }}>
            <div className="dash-stable-ring-inner">
              <span className="dash-stable-ring-value">{fmtNum(stable_terrain.stable_pct, 1)}%</span>
              <span className="dash-stable-ring-label">stable</span>
            </div>
          </div>
          <p className="dash-stable-note">
            {stable_terrain.interpretation || 'Stable terrain is the part of the bbox where no real change is expected. The spread there sets the noise floor (LoD).'}
          </p>
        </div>

        <div className="dash-stable-pills">
          <StablePill label="Level of Detection" value={`±${fmtNum(stable_terrain.lod_95_m, 2)} m`}
                       hint="Changes smaller than this are treated as noise." />
          <StablePill label="NMAD" value={`${fmtNum(stable_terrain.stable_nmad_m, 3)} m`}
                       hint="Robust spread on stable ground." />
          <StablePill label="σ combined" value={`${fmtNum(stable_terrain.sigma_combined_m, 3)} m`}
                       hint="Uncertainty propagated through DoD." />
          <StablePill label="Stable pixels" value={fmtNum(stable_terrain.n_stable_pixels, 0)}
                       hint="Sample size for the noise floor." />
        </div>
      </section>

      <section className="dash-section">
        <div className="dash-section-head">
          <h3 className="dash-section-title">Reports</h3>
          <span className="dash-section-sub">Generated PDF deliverables</span>
        </div>
        <div className="dash-reports">
          <ReportButton
            title="DEMs Report"
            desc="DEM fetch, alignment & co-registration"
          />
          <ReportButton
            title="Site Report"
            desc="Full stakeholder analysis & KPIs"
          />
        </div>
      </section>
    </>
  );
}

function ReportButton({ title, desc }) {
  return (
    <button type="button" className="dash-report-btn" disabled aria-disabled="true" title="Not available yet">
      <span className="dash-report-icon" aria-hidden>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
          <line x1="9" y1="13" x2="15" y2="13" />
          <line x1="9" y1="17" x2="13" y2="17" />
        </svg>
      </span>
      <span className="dash-report-text">
        <span className="dash-report-title">{title}</span>
        <span className="dash-report-desc">{desc}</span>
      </span>
      <span className="dash-report-soon">Coming soon</span>
    </button>
  );
}

function KpiCard({ label, value, sub, accent = 'brand' }) {
  return (
    <div className={`dash-kpi dash-kpi-${accent}`}>
      <div className="dash-kpi-value">{value}</div>
      <div className="dash-kpi-label">{label}</div>
      {sub && <div className="dash-kpi-sub">{sub}</div>}
    </div>
  );
}

function DetailLine({ icon, label, value, mono }) {
  return (
    <div className="dash-detail-line">
      <span className="dash-detail-icon" aria-hidden>{iconFor(icon)}</span>
      <div className="dash-detail-text">
        <span className="dash-detail-label">{label}</span>
        <span className={`dash-detail-value${mono ? ' is-mono' : ''}`}>{value}</span>
      </div>
    </div>
  );
}

function iconFor(name) {
  const common = { fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' };
  switch (name) {
    case 'calendar':
      return <svg viewBox="0 0 24 24" {...common}><rect x="3" y="4" width="18" height="18" rx="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" /></svg>;
    case 'period':
      return <svg viewBox="0 0 24 24" {...common}><circle cx="12" cy="12" r="9" /><polyline points="12 7 12 12 16 14" /></svg>;
    case 'org':
      return <svg viewBox="0 0 24 24" {...common}><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>;
    case 'bbox':
      return <svg viewBox="0 0 24 24" {...common}><rect x="4" y="4" width="16" height="16" rx="1" strokeDasharray="3 2" /></svg>;
    case 'pin':
      return <svg viewBox="0 0 24 24" {...common}><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" /><circle cx="12" cy="10" r="3" /></svg>;
    case 'zones':
      return <svg viewBox="0 0 24 24" {...common}><polygon points="3 7 9 4 15 7 21 4 21 17 15 20 9 17 3 20 3 7" /></svg>;
    case 'lod':
      return <svg viewBox="0 0 24 24" {...common}><line x1="4" y1="12" x2="20" y2="12" /><polyline points="8 8 4 12 8 16" /><polyline points="16 8 20 12 16 16" /></svg>;
    case 'pixels':
      return <svg viewBox="0 0 24 24" {...common}><rect x="4" y="4" width="6" height="6" /><rect x="14" y="4" width="6" height="6" /><rect x="4" y="14" width="6" height="6" /><rect x="14" y="14" width="6" height="6" /></svg>;
    case 'pct':
      return <svg viewBox="0 0 24 24" {...common}><line x1="19" y1="5" x2="5" y2="19" /><circle cx="7" cy="7" r="2" /><circle cx="17" cy="17" r="2" /></svg>;
    case 'nmad':
    case 'sigma':
      return <svg viewBox="0 0 24 24" {...common}><path d="M4 19c4-12 12-12 16 0" /><path d="M4 19h16" /></svg>;
    default:
      return null;
  }
}

function HistogramChart({ histogram }) {
  const W = 680, H = 240, PAD_L = 44, PAD_R = 16, PAD_T = 16, PAD_B = 38;
  const bins = (histogram?.fine_bins?.length ? histogram.fine_bins : histogram?.bins) || [];
  if (bins.length === 0) return <div className="dash-empty">No pixel data to display.</div>;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;
  const maxCount = Math.max(1, ...bins.map((b) => b.count));
  const xMin = Math.min(histogram?.domain_min ?? -15, ...bins.map((b) => b.bin_min));
  const xMax = Math.max(histogram?.domain_max ??  15, ...bins.map((b) => b.bin_max));
  const xScale = (v) => PAD_L + ((v - xMin) / (xMax - xMin)) * innerW;
  const yScale = (v) => PAD_T + innerH - (v / maxCount) * innerH;
  const lod = histogram.lod_m;
  const lodLeft = Math.max(PAD_L, xScale(-lod));
  const lodRight = Math.min(W - PAD_R, xScale(lod));

  // Build evenly-spaced ticks based on the actual domain
  const span = xMax - xMin;
  const tickStep = span > 30 ? 10 : span > 12 ? 5 : 2;
  const xTicks = [];
  const start = Math.ceil(xMin / tickStep) * tickStep;
  for (let t = start; t <= xMax; t += tickStep) xTicks.push(t);
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((t) => Math.round(maxCount * t));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="dash-histogram" preserveAspectRatio="xMidYMid meet">
      <defs>
        <linearGradient id="histGain" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor="var(--gain)" stopOpacity="0.95" />
          <stop offset="100%" stopColor="var(--gain)" stopOpacity="0.55" />
        </linearGradient>
        <linearGradient id="histLoss" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor="var(--loss)" stopOpacity="0.95" />
          <stop offset="100%" stopColor="var(--loss)" stopOpacity="0.55" />
        </linearGradient>
        <pattern id="histLodHatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
          <rect width="6" height="6" fill="var(--muted)" opacity="0.06" />
          <line x1="0" y1="0" x2="0" y2="6" stroke="var(--muted)" strokeOpacity="0.22" strokeWidth="1" />
        </pattern>
      </defs>

      {/* Y grid */}
      {yTicks.map((c, i) => (
        <g key={i}>
          <line x1={PAD_L} x2={W - PAD_R} y1={yScale(c)} y2={yScale(c)} stroke="var(--line)" strokeOpacity="0.45" />
          <text x={PAD_L - 8} y={yScale(c) + 3} textAnchor="end" className="dash-axis-text">{c.toLocaleString()}</text>
        </g>
      ))}

      {/* LoD band : hatched so bars stay visible underneath */}
      {lodRight > lodLeft && (
        <rect x={lodLeft} y={PAD_T} width={lodRight - lodLeft} height={innerH} fill="url(#histLodHatch)" />
      )}

      {/* Bars : colored by sign regardless of LoD so the distribution shape is always readable */}
      {bins.map((b, i) => {
        const x = xScale(b.bin_min);
        const x2 = xScale(b.bin_max);
        const y = yScale(b.count);
        const w = Math.max(1.5, x2 - x - 1.2);
        const h = Math.max(0, PAD_T + innerH - y);
        const fill = b.is_gain ? 'url(#histGain)' : 'url(#histLoss)';
        const opacity = b.is_noise ? 0.55 : 1;
        return (
          <rect key={i} x={x + 0.6} y={y} width={w} height={h} fill={fill} opacity={opacity} rx="1.5">
            <title>{`${b.bin_min.toFixed(2)} → ${b.bin_max.toFixed(2)} m · ${b.count.toLocaleString()} px`}</title>
          </rect>
        );
      })}

      {/* LoD edge lines */}
      {lodRight > lodLeft && (
        <>
          <line x1={lodLeft}  y1={PAD_T} x2={lodLeft}  y2={PAD_T + innerH} stroke="var(--muted)" strokeDasharray="4 3" strokeWidth="1" />
          <line x1={lodRight} y1={PAD_T} x2={lodRight} y2={PAD_T + innerH} stroke="var(--muted)" strokeDasharray="4 3" strokeWidth="1" />
          <text x={(lodLeft + lodRight) / 2} y={PAD_T + 12} textAnchor="middle" className="dash-axis-text" opacity="0.85">noise (±LoD)</text>
        </>
      )}

      {/* 0 line */}
      <line x1={xScale(0)} x2={xScale(0)} y1={PAD_T} y2={PAD_T + innerH} stroke="var(--text)" strokeOpacity="0.55" strokeWidth="1.2" />

      {/* X axis */}
      <line x1={PAD_L} x2={W - PAD_R} y1={PAD_T + innerH} y2={PAD_T + innerH} stroke="var(--line)" />
      {xTicks.map((t) => (
        <g key={t}>
          <line x1={xScale(t)} x2={xScale(t)} y1={PAD_T + innerH} y2={PAD_T + innerH + 4} stroke="var(--line)" />
          <text x={xScale(t)} y={PAD_T + innerH + 16} textAnchor="middle" className="dash-axis-text">{t}</text>
        </g>
      ))}
      <text x={(PAD_L + W - PAD_R) / 2} y={H - 4} textAnchor="middle" className="dash-axis-text-strong">Elevation change (m) : {histogram.n_total?.toLocaleString() ?? '—'} pixels</text>
    </svg>
  );
}

function RadarChart({ mai }) {
  const W = 260, H = 260, cx = W / 2, cy = H / 2;
  const R = 92;

  const axes = [
    { label: 'Economic',      v: mai.economic      ?? 0, color: 'var(--brand)' },
    { label: 'Environmental', v: mai.environmental ?? 0, color: 'var(--gain)' },
    { label: 'Risk',          v: mai.risk          ?? 0, color: 'var(--loss)' },
  ];
  const angleFor = (i) => -Math.PI / 2 + (i / axes.length) * 2 * Math.PI;
  const point = (i, v) => {
    const a = angleFor(i);
    const r = Math.max(0, Math.min(1, v)) * R;
    return [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
  };
  const corner = (i) => point(i, 1);
  const labelPos = (i) => {
    const a = angleFor(i);
    const r = R + 24;
    return [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
  };
  const polygon = axes.map((a, i) => point(i, a.v).join(',')).join(' ');
  const rings = [0.25, 0.5, 0.75, 1];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="dash-radar" preserveAspectRatio="xMidYMid meet">
      <defs>
        <radialGradient id="radarFill" cx="50%" cy="50%" r="70%">
          <stop offset="0%"   stopColor="var(--accent)" stopOpacity="0.55" />
          <stop offset="100%" stopColor="var(--brand)"  stopOpacity="0.22" />
        </radialGradient>
      </defs>
      {rings.map((r) => (
        <polygon
          key={r}
          points={axes.map((_, i) => point(i, r).join(',')).join(' ')}
          fill="none"
          stroke="var(--line)"
          strokeOpacity={r === 100 ? 0.9 : 0.45}
        />
      ))}
      {axes.map((_, i) => {
        const [x, y] = corner(i);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="var(--line)" strokeOpacity="0.4" />;
      })}
      <polygon points={polygon} fill="url(#radarFill)" stroke="var(--brand)" strokeWidth="2" />
      {axes.map((a, i) => {
        const [px, py] = point(i, a.v);
        return <circle key={a.label} cx={px} cy={py} r="4" fill={a.color} stroke="var(--surface)" strokeWidth="1.5" />;
      })}
      {axes.map((a, i) => {
        const [lx, ly] = labelPos(i);
        return (
          <g key={`l-${a.label}`}>
            <text x={lx} y={ly - 4} textAnchor="middle" className="dash-radar-label">{a.label}</text>
            <text x={lx} y={ly + 10} textAnchor="middle" className="dash-radar-value">{(a.v * 100).toFixed(0)}</text>
          </g>
        );
      })}
    </svg>
  );
}

function GaugeChart({ gauge }) {
  const W = 280, H = 180, cx = W / 2, cy = H - 30, R = 110;
  const min = gauge.min ?? 0;
  const max = gauge.max ?? 4;
  const value = Math.min(Math.max(gauge.cut_fill_ratio ?? 0, min), max);
  const pct = (v) => (v - min) / (max - min);
  const angleAt = (v) => Math.PI * (1 - pct(v));
  const point = (v, r = R) => [cx + Math.cos(angleAt(v)) * r, cy - Math.sin(angleAt(v)) * r];

  const arc = (v0, v1, color, w = 14) => {
    const [x0, y0] = point(v0);
    const [x1, y1] = point(v1);
    return (
      <path
        d={`M ${x0} ${y0} A ${R} ${R} 0 0 1 ${x1} ${y1}`}
        stroke={color}
        strokeWidth={w}
        fill="none"
        strokeLinecap="round"
      />
    );
  };

  const [nx, ny] = point(value, R - 4);
  const [nbx, nby] = point(value, 16);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="dash-gauge" preserveAspectRatio="xMidYMid meet">
      <path
        d={`M ${point(min)[0]} ${point(min)[1]} A ${R} ${R} 0 0 1 ${point(max)[0]} ${point(max)[1]}`}
        stroke="var(--line)"
        strokeWidth="14"
        fill="none"
        strokeLinecap="round"
      />
      {arc(min, gauge.breakpoint ?? 1, 'var(--gain)')}
      {arc(gauge.breakpoint ?? 1, max, 'var(--loss)')}

      {(() => {
        const [bx, by]  = point(gauge.breakpoint ?? 1, R + 6);
        const [bx2, by2] = point(gauge.breakpoint ?? 1, R - 18);
        return <line x1={bx} y1={by} x2={bx2} y2={by2} stroke="var(--text)" strokeWidth="1.5" />;
      })()}

      <line x1={nbx} y1={nby} x2={nx} y2={ny} stroke="var(--text)" strokeWidth="3" strokeLinecap="round" />
      <circle cx={cx} cy={cy} r="7" fill="var(--surface)" stroke="var(--text)" strokeWidth="2" />

      <text x={point(min)[0]}     y={point(min)[1] + 22}     textAnchor="middle" className="dash-axis-text">Deposition</text>
      <text x={point(max)[0]}     y={point(max)[1] + 22}     textAnchor="middle" className="dash-axis-text">Excavation</text>
      <text x={cx} y={cy + 26} textAnchor="middle" className="dash-gauge-value">{(gauge.cut_fill_raw ?? value).toFixed(2)}</text>
      <text x={cx} y={cy - 70} textAnchor="middle" className="dash-axis-text">Loss / Gain</text>
    </svg>
  );
}

function DonutChart({ donut }) {
  const W = 220, H = 220, cx = W / 2, cy = H / 2;
  const R = 90, r = 56;
  const slices = [
    { label: 'Stable', value: donut.stable_pct, color: 'var(--muted)' },
    { label: 'Loss',   value: donut.loss_pct,   color: 'var(--loss)' },
    { label: 'Gain',   value: donut.gain_pct,   color: 'var(--gain)' },
  ];
  const other = donut.other_pct ?? 0;
  if (other > 0.5) slices.push({ label: 'Other', value: other, color: 'var(--brand)' });

  const total = slices.reduce((a, sx) => a + sx.value, 0) || 1;
  let start = -Math.PI / 2;
  const arcs = slices.map((sx) => {
    const angle = (sx.value / total) * 2 * Math.PI;
    const end = start + angle;
    const large = angle > Math.PI ? 1 : 0;
    const x0 = cx + Math.cos(start) * R;
    const y0 = cy + Math.sin(start) * R;
    const x1 = cx + Math.cos(end) * R;
    const y1 = cy + Math.sin(end) * R;
    const xi0 = cx + Math.cos(end) * r;
    const yi0 = cy + Math.sin(end) * r;
    const xi1 = cx + Math.cos(start) * r;
    const yi1 = cy + Math.sin(start) * r;
    const d = `M ${x0} ${y0} A ${R} ${R} 0 ${large} 1 ${x1} ${y1} L ${xi0} ${yi0} A ${r} ${r} 0 ${large} 0 ${xi1} ${yi1} Z`;
    start = end;
    return { d, color: sx.color, label: sx.label, value: sx.value };
  });

  const headline = Math.round(donut.stable_pct);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="dash-donut" preserveAspectRatio="xMidYMid meet">
      {arcs.map((a, i) => (
        <path key={i} d={a.d} fill={a.color} opacity="0.92">
          <title>{`${a.label}: ${a.value.toFixed(1)}%`}</title>
        </path>
      ))}
      <circle cx={cx} cy={cy} r={r - 1} fill="var(--surface)" />
      <text x={cx} y={cy - 4} textAnchor="middle" className="dash-donut-headline">{headline}%</text>
      <text x={cx} y={cy + 14} textAnchor="middle" className="dash-donut-sub">stable</text>
    </svg>
  );
}

function DonutLegendRow({ color, label, pct }) {
  return (
    <div className="dash-legend-row">
      <span className="dash-legend-dot" style={{ background: color }} />
      <span className="dash-legend-label">{label}</span>
      <span className="dash-legend-pct">{fmtNum(pct, 1)}%</span>
    </div>
  );
}

function StablePill({ label, value, hint }) {
  return (
    <div className="dash-stable-pill" title={hint}>
      <span className="dash-stable-pill-label">{label}</span>
      <span className="dash-stable-pill-value">{value}</span>
      <span className="dash-stable-pill-hint">{hint}</span>
    </div>
  );
}

function VolRow({ label, value, color, strong }) {
  return (
    <div className={`dash-vol-row${strong ? ' is-strong' : ''}`}>
      {color && <span className="dash-legend-dot" style={{ background: color }} />}
      <span className="dash-vol-label">{label}</span>
      <span className="dash-vol-value">{value}</span>
    </div>
  );
}

function MaiPill({ label, v, color }) {
 
  const pct = Math.max(0, Math.min(100, (Number(v) || 0) * 100));
  return (
    <div className="dash-mai-pill">
      <div className="dash-mai-pill-head">
        <span className="dash-legend-dot" style={{ background: color }} />
        <span className="dash-mai-pill-label">{label}</span>
        <span className="dash-mai-pill-value">{pct.toFixed(0)}<span className="dash-mai-pill-100">/100</span></span>
      </div>
      <div className="dash-mai-bar">
        <div className="dash-mai-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

function DashSkeleton() {
  return (
    <>
      <div className="dash-kpi-row">
        {[0, 1, 2].map((i) => <div key={i} className="dash-kpi dash-kpi-skel" />)}
      </div>
      <div className="dash-kpi-row dash-kpi-row-2">
        {[0, 1, 2].map((i) => <div key={i} className="dash-kpi dash-kpi-skel" />)}
      </div>
      <div className="dash-section dash-skel-section" />
      <div className="dash-grid-2">
        <div className="dash-section dash-card dash-skel-section" />
        <div className="dash-section dash-card dash-skel-section" />
      </div>
    </>
  );
}

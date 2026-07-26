import math
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import create_engine, text


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


_DB_URL = os.getenv(
    "MINING_DB_URL",
    "postgresql+psycopg2://mining:mining@mining_postgres:5432/mining_geodata",
)
if _DB_URL.startswith("postgres://"):
    _DB_URL = "postgresql+psycopg2://" + _DB_URL[len("postgres://"):]

_engine = create_engine(_DB_URL, pool_pre_ping=True)


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _synthetic_histogram(mean: float, std: float, n_total: int, lod: float) -> dict:
    """Fallback histogram for legacy sites without a real DoD raster on disk.
    Bin edges adapt to LoD so the noise band is always exact, and to data
    range so the LoD is never larger than the chart.
    """
    span = max(15.0, abs(lod) + 2.0, abs(mean) + 3 * max(std, 0.5) + 1.0)
    span = min(span, 50.0)
    fine_step = (2 * span) / 30
    fine_edges = [round(-span + i * fine_step, 4) for i in range(31)]
    coarse_edges = sorted(set(round(e, 4) for e in [-15.0, -10.0, -5.0, -lod, 0.0, lod, 5.0, 10.0, 15.0]))

    sigma = max(std, 0.01)

    def _bins(edges):
        out = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            p = _normal_cdf(hi, mean, sigma) - _normal_cdf(lo, mean, sigma)
            count = int(round(max(0.0, p) * max(0, n_total)))
            center = (lo + hi) / 2.0
            out.append({
                "bin_min":  round(lo, 3),
                "bin_max":  round(hi, 3),
                "center":   round(center, 3),
                "count":    count,
                "is_noise": -lod <= center <= lod,
                "is_gain":  center > 0,
                "is_loss":  center < 0,
            })
        return out

    return {
        "fine_bins":  _bins(fine_edges),
        "bins":       _bins(coarse_edges),
        "lod_m":      round(lod, 3),
        "n_total":    n_total,
        "mean_m":     round(mean, 3),
        "domain_min": round(-span, 3),
        "domain_max": round( span, 3),
        "synthetic":  True,
    }


def canonical_activity_level(meta: dict | None) -> str:
    """Return "High" / "Medium" / "Low" using the same MAI formula as Site_Report.py.
    Prefers the precomputed value in ``meta['report']['mai']['activity_level']`` if
    present, otherwise computes it on the fly so legacy rows still get a badge.
    """
    if not meta:
        return "Low"
    stored = (
        (meta.get("report") or {}).get("mai", {}) if isinstance(meta.get("report"), dict) else {}
    ).get("activity_level")
    if stored in ("Low", "Medium", "High"):
        return stored

    dod  = meta.get("dod_stats") or {}
    vect = meta.get("vectorization") or {}

    total_area = _num(dod.get("total_area_ha"))
    gain_area  = _num(dod.get("gain_area_ha"))
    loss_area  = _num(dod.get("loss_area_ha"))
    net_vol    = _num(dod.get("net_vol_m3"))
    min_change = _num(dod.get("min_change_m"))
    n_zones    = int(_num(vect.get("n_zones")))

    disturbed_pct = ((gain_area + loss_area) / total_area * 100.0) if total_area > 0 else 0.0
    V_norm  = min(abs(net_vol) / 1e9, 1.0)
    A_norm  = min(disturbed_pct / 100.0, 1.0)
    DZ_norm = min(abs(min_change) / 100.0, 1.0)
    if n_zones == 0:
        V_norm = A_norm = DZ_norm = 0.0

    economic      = 0.60 * V_norm + 0.20 * A_norm + 0.20 * DZ_norm
    environmental = 0.20 * V_norm + 0.60 * A_norm + 0.20 * DZ_norm
    risk          = 0.20 * V_norm + 0.30 * A_norm + 0.50 * DZ_norm
    standard      = max(economic, environmental, risk)
    return "High" if standard > 0.70 else ("Medium" if standard >= 0.30 else "Low")


def _build_report_fallback(meta: dict) -> dict:
    """Compute the dashboard payload from raw metadata using Site_Report.py's formulas.
    """
    dod    = meta.get("dod_stats") or {}
    stable = meta.get("stable_terrain") or {}
    mp     = meta.get("multi_period_dod") or {}
    vect   = meta.get("vectorization") or {}

    total_area = _num(dod.get("total_area_ha"))
    gain_area  = _num(dod.get("gain_area_ha"))
    loss_area  = _num(dod.get("loss_area_ha"))
    vol_gain   = _num(dod.get("vol_gain_m3"))
    vol_loss   = abs(_num(dod.get("vol_loss_m3")))
    net_vol    = _num(dod.get("net_vol_m3"))
    min_change = _num(dod.get("min_change_m"))
    max_change = _num(dod.get("max_change_m"))
    mean_chg   = _num(dod.get("mean_change_m"))
    std_chg    = _num(dod.get("std_change_m"), 1.0)
    lod        = _num(dod.get("lod_95_m"))
    n_pixels   = int(_num(dod.get("n_valid_pixels")))

    n_zones = int(_num(vect.get("n_zones")))
    disturbed = gain_area + loss_area
    disturbed_pct = (disturbed / total_area * 100.0) if total_area > 0 else 0.0
    cut_fill = (vol_loss / vol_gain) if vol_gain > 1e-6 else (0.0 if vol_loss < 1e-6 else 999.0)

    V_norm  = min(abs(net_vol) / 1e9, 1.0)
    A_norm  = min(disturbed_pct / 100.0, 1.0)
    DZ_norm = min(abs(min_change) / 100.0, 1.0)
    if n_zones == 0:
        V_norm = A_norm = DZ_norm = 0.0

    mai_economic    = 0.60 * V_norm + 0.20 * A_norm + 0.20 * DZ_norm
    mai_environment = 0.20 * V_norm + 0.60 * A_norm + 0.20 * DZ_norm
    mai_risk        = 0.20 * V_norm + 0.30 * A_norm + 0.50 * DZ_norm
    mai_standard    = max(mai_economic, mai_environment, mai_risk)
    activity_level  = "High" if mai_standard > 0.70 else ("Medium" if mai_standard >= 0.30 else "Low")

    stable_pct = _num(stable.get("stable_pct"))
    loss_pct   = (loss_area / total_area * 100.0) if total_area > 0 else 0.0
    gain_pct   = (gain_area / total_area * 100.0) if total_area > 0 else 0.0
    other_pct  = max(0.0, 100.0 - stable_pct - loss_pct - gain_pct)

   
    histogram = _synthetic_histogram(mean_chg, std_chg, n_pixels, lod) if n_pixels > 0 else None

    def _period_payload(stats, label):
        if not stats:
            return None
        return {
            "label":          label,
            "net_vol_m3":     _num(stats.get("net_vol_m3")),
            "vol_gain_m3":    _num(stats.get("vol_gain_m3")),
            "vol_loss_m3":    _num(stats.get("vol_loss_m3")),
            "moved_m3":       _num(stats.get("vol_gain_m3")) + abs(_num(stats.get("vol_loss_m3"))),
            "mean_change_m":  _num(stats.get("mean_change_m")),
            "std_change_m":   _num(stats.get("std_change_m")),
            "min_change_m":   _num(stats.get("min_change_m")),
            "max_change_m":   _num(stats.get("max_change_m")),
            "n_valid_pixels": int(_num(stats.get("n_valid_pixels"))),
        }

    p1 = _period_payload(mp.get("period1_stats"), mp.get("period1_label"))
    p2 = _period_payload(mp.get("period2_stats"), mp.get("period2_label"))
    multi_period: dict[str, Any] = {"period1": p1, "period2": p2}
    if p1 and p2:
        v1, v2 = p1["net_vol_m3"], p2["net_vol_m3"]
        delta_abs = v2 - v1
        delta_pct = ((v2 - v1) / abs(v1) * 100.0) if abs(v1) > 1e-3 else None
        sign = "stable"
        if delta_pct is None and v2 != 0:
            sign = "intensifying"
        elif delta_pct is not None:
            if delta_pct > 25:
                sign = "intensifying"
            elif delta_pct < -25:
                sign = "subsiding"
            elif v1 * v2 < 0:
                sign = "reversal"
        multi_period["delta"] = {
            "abs_m3":   delta_abs,
            "pct":      delta_pct,
            "sign":     sign,
            "narrative": (
                "Excavation intensified between eras." if sign == "intensifying" and v2 < 0 else
                "Deposition intensified between eras." if sign == "intensifying" and v2 > 0 else
                "Activity subsided between eras."      if sign == "subsiding"                else
                "Direction reversed between eras."     if sign == "reversal"                  else
                "Stable across eras."
            ),
        }

    interpretation = (
        "Excellent co-registration: measured changes are highly reliable."
        if stable_pct > 80 else
        "Strong co-registration: most of the bbox is unchanged."
        if stable_pct > 50 else
        "Moderate co-registration: interpret small changes with caution."
        if stable_pct > 20 else
        "Limited stable area: only large-magnitude changes are trustworthy."
    )

    return {
        "schema_version": 1,
        "kpis": {
            "total_area_ha":     round(total_area, 3),
            "disturbed_area_ha": round(disturbed, 3),
            "disturbed_pct":     round(disturbed_pct, 2),
            "gain_area_ha":      round(gain_area, 3),
            "loss_area_ha":      round(loss_area, 3),
            "vol_gain_m3":       round(vol_gain, 2),
            "vol_loss_m3":       round(-vol_loss, 2),
            "net_vol_m3":        round(net_vol, 2),
            "moved_m3":          round(vol_gain + vol_loss, 2),
            "mean_change_m":     round(mean_chg, 3),
            "std_change_m":      round(std_chg, 3),
            "max_excavation_m":  round(abs(min_change), 3),
            "max_deposition_m":  round(max_change, 3),
            "cut_fill_ratio":    round(cut_fill, 3),
            "lod_m":             round(lod, 3),
            "n_pixels":          n_pixels,
            "n_zones":           n_zones,
            "n_gain_zones":      int(_num(vect.get("n_gain_zones"))),
            "n_loss_zones":      int(_num(vect.get("n_loss_zones"))),
            "net_vol_sign":      "deposition" if net_vol > 0 else ("excavation" if net_vol < 0 else "balanced"),
        },
        "mai": {
            "V_norm": round(V_norm, 4),
            "A_norm": round(A_norm, 4),
            "DZ_norm": round(DZ_norm, 4),
            "economic":      round(mai_economic, 4),
            "environmental": round(mai_environment, 4),
            "risk":          round(mai_risk, 4),
            "standard":      round(mai_standard, 4),
            "activity_level": activity_level,
            "weights": {
                "economic":      {"V": 0.60, "A": 0.20, "DZ": 0.20},
                "environmental": {"V": 0.20, "A": 0.60, "DZ": 0.20},
                "risk":          {"V": 0.20, "A": 0.30, "DZ": 0.50},
            },
        },
        "gauge": {
            "cut_fill_ratio": round(min(cut_fill, 4.0), 3),
            "cut_fill_raw":   round(cut_fill, 3),
            "regime":         "excavation" if cut_fill > 1.0 else ("deposition" if cut_fill > 0.0 else "balanced"),
            "min": 0.0, "max": 4.0, "breakpoint": 1.0,
        },
        "donut": {
            "stable_pct": round(stable_pct, 2),
            "loss_pct":   round(loss_pct, 2),
            "gain_pct":   round(gain_pct, 2),
            "other_pct":  round(other_pct, 2),
        },
        "histogram": histogram,
        "stable_terrain": {
            "stable_pct":       round(stable_pct, 2),
            "stable_nmad_m":    _num(stable.get("stable_nmad_m")),
            "sigma_combined_m": _num(stable.get("sigma_combined_m")),
            "n_stable_pixels":  int(_num(stable.get("n_stable_pixels"))),
            "lod_95_m":         _num(stable.get("lod_95_m"), lod),
            "interpretation":   interpretation,
        },
        "multi_period": multi_period,
        "vectorization": {
            "n_zones":      n_zones,
            "n_gain_zones": int(_num(vect.get("n_gain_zones"))),
            "n_loss_zones": int(_num(vect.get("n_loss_zones"))),
            "threshold_m":  _num(vect.get("threshold_m")),
        },
    }


@router.get("/{bbox_id}")
def dashboard_for_area(bbox_id: str):
    """Dashboard payload for one site.
    """
    with _engine.connect() as conn:
        row = conn.execute(text(
            """
            SELECT id, org_id, country, area_name, bbox_id,
                   period_start, period_end, total_area, volume, metadata,
                   ST_XMin(bbox) AS minx, ST_YMin(bbox) AS miny,
                   ST_XMax(bbox) AS maxx, ST_YMax(bbox) AS maxy,
                   COALESCE(jsonb_array_length(geojson->'features'), 0) AS n_polygons
            FROM mining_areas
            WHERE bbox_id = :bbox_id
            LIMIT 1
            """
        ), {"bbox_id": bbox_id}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail=f"No mining area with bbox_id={bbox_id}")
    meta = row["metadata"]
    if not meta:
        raise HTTPException(status_code=404, detail=f"No metadata stored for bbox_id={bbox_id}")

    report = meta.get("report")
    source = "pipeline"
    if not report or not isinstance(report, dict):
        report = _build_report_fallback(meta)
        source = "computed"

    bbox = [float(row["minx"]), float(row["miny"]),
            float(row["maxx"]), float(row["maxy"])]
    period_start = row["period_start"].isoformat() if row["period_start"] else None
    period_end   = row["period_end"].isoformat()   if row["period_end"]   else None

    return {
        "site": {
            "id":           row["id"],
            "bbox_id":      row["bbox_id"],
            "area_name":    row["area_name"],
            "country":      row["country"],
            "org_id":       row["org_id"],
            "bbox":         bbox,
            "period_start": period_start,
            "period_end":   period_end,
            "run_at":       meta.get("run_at"),
            "client":       meta.get("client"),
            "n_polygons":   int(row["n_polygons"]),
        },
        "source":         source,
        "generated_at":   report.get("generated_at"),
        "schema_version": report.get("schema_version", 1),
        "kpis":           report.get("kpis", {}),
        "mai":            report.get("mai", {}),
        "gauge":          report.get("gauge", {}),
        "donut":          report.get("donut", {}),
        "histogram":      report.get("histogram"),
        "stable_terrain": report.get("stable_terrain", {}),
        "multi_period":   report.get("multi_period", {}),
        "vectorization":  report.get("vectorization", {}),
    }

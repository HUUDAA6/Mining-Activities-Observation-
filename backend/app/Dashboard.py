import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from sqlalchemy import create_engine, text

from dashboardareas import (
    _num,
    _build_report_fallback,
    canonical_activity_level,
)


router = APIRouter(prefix="/api/global", tags=["global-dashboard"])


_DB_URL = os.getenv(
    "MINING_DB_URL",
    "postgresql+psycopg2://mining:mining@mining_postgres:5432/mining_geodata",
)
if _DB_URL.startswith("postgres://"):
    _DB_URL = "postgresql+psycopg2://" + _DB_URL[len("postgres://"):]

_engine = create_engine(_DB_URL, pool_pre_ping=True)


def _site_metrics(meta: dict | None, total_area_col, volume_col) -> dict:
    """Normalise one site's headline numbers.
    """
    meta = meta or {}
    report = meta.get("report")
    if not isinstance(report, dict) or not report.get("kpis"):
        report = _build_report_fallback(meta) if meta else {}

    kpis = report.get("kpis", {}) if isinstance(report, dict) else {}
    mai = report.get("mai", {}) if isinstance(report, dict) else {}

    total_area = _num(kpis.get("total_area_ha")) or _num(total_area_col)
    gain_area = _num(kpis.get("gain_area_ha"))
    loss_area = _num(kpis.get("loss_area_ha"))
    disturbed = _num(kpis.get("disturbed_area_ha")) or (gain_area + loss_area)
    vol_gain = _num(kpis.get("vol_gain_m3"))
    vol_loss = abs(_num(kpis.get("vol_loss_m3")))
    net_vol = _num(kpis.get("net_vol_m3"))
    if net_vol == 0.0 and volume_col is not None:
        net_vol = _num(volume_col)

    return {
        "total_area_ha":   round(total_area, 3),
        "disturbed_ha":    round(disturbed, 3),
        "vol_excavated_m3": round(vol_loss, 2),
        "vol_deposited_m3": round(vol_gain, 2),
        "net_vol_m3":      round(net_vol, 2),
        "moved_m3":        round(vol_gain + vol_loss, 2),
        "mai_standard":    round(_num(mai.get("standard")), 4),
        "activity_level":  canonical_activity_level(meta),
    }


@router.get("/overview")
def global_overview():
    """One payload that drives the whole Global Dashboard:
    * totals            : headline KPIs across every site
    * by_country        : per-country excavation / deposition 
    * risk_distribution : site counts by Mining Activity Index 
    * sites             : per-site points for the bubble map & scatter plot
    """
    with _engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT id, org_id, bbox_id, area_name,
                   COALESCE(NULLIF(TRIM(country), ''), 'Unknown') AS country,
                   period_start, period_end,
                   total_area, volume, metadata,
                   ST_X(ST_Centroid(bbox)) AS lon,
                   ST_Y(ST_Centroid(bbox)) AS lat
            FROM mining_areas
            ORDER BY id DESC
            """
        )).mappings().all()

    sites: list[dict[str, Any]] = []
    by_country: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "n_sites": 0,
            "vol_excavated_m3": 0.0,
            "vol_deposited_m3": 0.0,
            "net_vol_m3": 0.0,
            "total_area_ha": 0.0,
            "disturbed_ha": 0.0,
        }
    )
    mai_counts = {"Low": 0, "Medium": 0, "High": 0}

    tot_area = tot_disturbed = 0.0
    tot_exc = tot_dep = tot_net = tot_moved = 0.0
    latest_period_end: str | None = None
    top_excavation = {"name": None, "country": None, "value": 0.0}
    top_disturbed = {"name": None, "country": None, "value": 0.0}

    for r in rows:
        m = _site_metrics(r["metadata"], r["total_area"], r["volume"])
        country = r["country"]
        level = m["activity_level"]
        mai_counts[level] = mai_counts.get(level, 0) + 1

        tot_area += m["total_area_ha"]
        tot_disturbed += m["disturbed_ha"]
        tot_exc += m["vol_excavated_m3"]
        tot_dep += m["vol_deposited_m3"]
        tot_net += m["net_vol_m3"]
        tot_moved += m["moved_m3"]

        c = by_country[country]
        c["n_sites"] += 1
        c["vol_excavated_m3"] += m["vol_excavated_m3"]
        c["vol_deposited_m3"] += m["vol_deposited_m3"]
        c["net_vol_m3"] += m["net_vol_m3"]
        c["total_area_ha"] += m["total_area_ha"]
        c["disturbed_ha"] += m["disturbed_ha"]

        if m["vol_excavated_m3"] > top_excavation["value"]:
            top_excavation = {
                "name": r["area_name"] or r["bbox_id"],
                "country": country,
                "value": m["vol_excavated_m3"],
            }
        if m["disturbed_ha"] > top_disturbed["value"]:
            top_disturbed = {
                "name": r["area_name"] or r["bbox_id"],
                "country": country,
                "value": m["disturbed_ha"],
            }

        pe = r["period_end"].isoformat() if r["period_end"] else None
        if pe and (latest_period_end is None or pe > latest_period_end):
            latest_period_end = pe

        lon = float(r["lon"]) if r["lon"] is not None else None
        lat = float(r["lat"]) if r["lat"] is not None else None

        sites.append({
            "id":            r["id"],
            "bbox_id":       r["bbox_id"],
            "area_name":     r["area_name"] or r["bbox_id"],
            "country":       country,
            "org_id":        r["org_id"],
            "lon":           lon,
            "lat":           lat,
            "total_area_ha":     m["total_area_ha"],
            "disturbed_ha":      m["disturbed_ha"],
            "vol_excavated_m3":  m["vol_excavated_m3"],
            "vol_deposited_m3":  m["vol_deposited_m3"],
            "net_vol_m3":        m["net_vol_m3"],
            "mai_standard":      m["mai_standard"],
            "activity_level":    level,
        })

    n_sites = len(sites)
    active = mai_counts["Medium"] + mai_counts["High"]
    inactive = mai_counts["Low"]
    disturbed_pct = (tot_disturbed / tot_area * 100.0) if tot_area > 0 else 0.0
    cut_fill_ratio = (tot_exc / tot_dep) if tot_dep > 1e-6 else (
        0.0 if tot_exc < 1e-6 else 999.0
    )
    net_sign = (
        "excavation" if tot_net < 0 else
        "deposition" if tot_net > 0 else
        "balanced"
    )

    country_list = sorted(
        (
            {
                "country": name,
                "n_sites": int(v["n_sites"]),
                "vol_excavated_m3": round(v["vol_excavated_m3"], 2),
                "vol_deposited_m3": round(v["vol_deposited_m3"], 2),
                "net_vol_m3":       round(v["net_vol_m3"], 2),
                "total_area_ha":    round(v["total_area_ha"], 2),
                "disturbed_ha":     round(v["disturbed_ha"], 2),
            }
            for name, v in by_country.items()
        ),
        key=lambda d: d["vol_excavated_m3"] + d["vol_deposited_m3"],
        reverse=True,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "n_sites":            n_sites,
            "n_countries":        len(by_country),
            "total_area_ha":      round(tot_area, 2),
            "total_disturbed_ha": round(tot_disturbed, 2),
            "disturbed_pct":      round(disturbed_pct, 2),
            "vol_excavated_m3":   round(tot_exc, 2),
            "vol_deposited_m3":   round(tot_dep, 2),
            "net_vol_m3":         round(tot_net, 2),
            "net_vol_sign":       net_sign,
            "material_moved_m3":  round(tot_moved, 2),
            "cut_fill_ratio":     round(cut_fill_ratio, 3),
            "active_sites":       active,
            "inactive_sites":     inactive,
            "mai_counts":         mai_counts,
            "latest_period_end":  latest_period_end,
            "top_excavation_site": top_excavation,
            "top_disturbed_site":  top_disturbed,
        },
        "by_country": country_list,
        "risk_distribution": [
            {"level": "Low",    "count": mai_counts["Low"]},
            {"level": "Medium", "count": mai_counts["Medium"]},
            {"level": "High",   "count": mai_counts["High"]},
        ],
        "sites": sites,
    }

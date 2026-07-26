import os
import sys
import json
from datetime import datetime

import numpy as np
import rasterio

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, TableStyle, LongTable, PageBreak,
)

from report_theme import (
    PRIMARY, BG_LIGHT, TEXT_MAIN, SUCCESS, DANGER, WARNING, WHITE,
    ST_LABEL, ST_VALUE, ST_VAL_POS, ST_VAL_NEG, ST_VAL_WARN,
    _style, W_PAGE,
    header_strip, section_major, section_minor, info_table, kpi_row,
    kpi_card, img_block, footer, base_table_style,
    fmt_vol_clean, val_style_auto,
)


STEP = "[Site_Report]"


def log(msg, level="INFO"):
    print(f"{STEP} {level}: {msg}", flush=True)


def fail(reason, hint=None, code=1):
    log(reason, level="ERROR")
    if hint:
        log(f"Suggested fix: {hint}", level="HINT")
    sys.exit(code)

def compute_kpis(meta):
    dod = meta.get("dod_stats", {})
    vec = meta.get("vectorization", {})

    total_area_ha = dod.get("total_area_ha", 0)
    gain_ha       = dod.get("gain_area_ha", 0)
    loss_ha       = dod.get("loss_area_ha", 0)
    vol_gain      = dod.get("vol_gain_m3", 0)
    vol_loss      = dod.get("vol_loss_m3", 0)
    net_vol       = dod.get("net_vol_m3", 0)
    mean_change   = dod.get("mean_change_m", 0)
    max_change    = dod.get("max_change_m", 0)
    min_change    = dod.get("min_change_m", 0)
    std_change    = dod.get("std_change_m", 0)

    vec_gain_ha   = vec.get("gain_area_ha", 0)
    vec_loss_ha   = vec.get("loss_area_ha", 0)
    n_zones       = vec.get("n_zones", 0)
    n_gain        = vec.get("n_gain_zones", 0)
    n_loss        = vec.get("n_loss_zones", 0)

    disturbed_ha   = gain_ha + loss_ha
    disturbed_pct  = (disturbed_ha / total_area_ha * 100) if total_area_ha > 0 else 0
    cut_fill_ratio = abs(vol_loss / vol_gain) if vol_gain != 0 else 0
    max_excavation = abs(min_change)
    max_deposition = max_change
    intensity      = abs(net_vol) / (total_area_ha * 10000) if total_area_ha > 0 else 0

    # MAI inputs : normalised 0-1
    V_norm  = min(abs(net_vol) / 1e9, 1.0)
    A_norm  = min(disturbed_pct / 100.0, 1.0)
    DZ_norm = min(max_excavation / 100.0, 1.0)

    # Use vectorized zones as the authoritative disturbed-area signal:
    # non-mining sites with zero zones must return Low, regardless of raw DoD.
    if n_zones == 0:
        V_norm = A_norm = DZ_norm = 0.0

    mai_economic    = 0.60 * V_norm + 0.20 * A_norm + 0.20 * DZ_norm
    mai_environment = 0.20 * V_norm + 0.60 * A_norm + 0.20 * DZ_norm
    mai_risk        = 0.20 * V_norm + 0.30 * A_norm + 0.50 * DZ_norm
    mai_standard    = max(mai_economic, mai_environment, mai_risk)

    if mai_standard > 0.70:
        activity_level = "High"
    elif mai_standard >= 0.30:
        activity_level = "Medium"
    else:
        activity_level = "Low"

    return {
        "total_area_ha": total_area_ha,
        "disturbed_ha": disturbed_ha,
        "disturbed_pct": disturbed_pct,
        "gain_area_ha": gain_ha,
        "loss_area_ha": loss_ha,
        "vol_gain_m3": vol_gain,
        "vol_loss_m3": vol_loss,
        "net_vol_m3": net_vol,
        "mean_change_m": mean_change,
        "std_change_m": std_change,
        "max_excavation_m": max_excavation,
        "max_deposition_m": max_deposition,
        "cut_fill_ratio": cut_fill_ratio,
        "intensity_m3_per_m2": intensity,
        "n_zones": n_zones,
        "n_gain_zones": n_gain,
        "n_loss_zones": n_loss,
        "vec_gain_ha": vec_gain_ha,
        "vec_loss_ha": vec_loss_ha,
        "V_norm": round(V_norm, 4),
        "A_norm": round(A_norm, 4),
        "DZ_norm": round(DZ_norm, 4),
        "mai_economic": round(mai_economic, 4),
        "mai_environment": round(mai_environment, 4),
        "mai_risk": round(mai_risk, 4),
        "mai_standard": round(mai_standard, 4),
        "activity_level": activity_level,
    }


def _activity_level(mai_standard: float) -> str:
    if mai_standard > 0.70:
        return "High"
    if mai_standard >= 0.30:
        return "Medium"
    return "Low"


def build_histogram(dod_path: str, lod_95: float) -> dict | None:
    """Real elevation-change histogram read from the DoD raster.

    Returns a fine 31-bin distribution for smooth rendering plus a coarse
    breakdown on the user-spec edges (-15, -10, -5, 0, 5, 10, 15). The LoD
    breakpoints are added to the coarse edges so the noise band is exact.
    """
    if not dod_path or not os.path.exists(dod_path):
        return None
    try:
        with rasterio.open(dod_path) as src:
            arr = src.read(1).astype("float32")
    except Exception as e:
        log(f"Histogram skipped! could not read DoD raster: {e}", level="WARN")
        return None

    valid = arr[np.isfinite(arr)]
    if valid.size == 0:
        return None

    def _bin_records(edges_list):
        edges_arr = np.asarray(edges_list, dtype="float64")
        counts, _ = np.histogram(valid, bins=edges_arr)
        out = []
        for lo, hi, c in zip(edges_arr[:-1], edges_arr[1:], counts.tolist()):
            center = (float(lo) + float(hi)) / 2.0
            out.append({
                "bin_min":  round(float(lo), 3),
                "bin_max":  round(float(hi), 3),
                "center":   round(center, 3),
                "count":    int(c),
                "is_noise": -lod_95 <= center <= lod_95,
                "is_gain":  center > 0,
                "is_loss":  center < 0,
            })
        return out

    span = max(15.0, abs(lod_95) + 2.0, float(np.percentile(np.abs(valid), 99.5)) + 1.0)
    span = float(min(span, 50.0))  # keep the chart sane
    fine_edges = np.linspace(-span, span, 31).tolist()
    coarse_edges = sorted(set([-15.0, -10.0, -5.0, -lod_95, 0.0, lod_95, 5.0, 10.0, 15.0]))

    return {
        "fine_bins":   _bin_records(fine_edges),
        "bins":        _bin_records(coarse_edges),
        "lod_m":       round(float(lod_95), 3),
        "n_total":     int(valid.size),
        "min_m":       round(float(np.min(valid)), 3),
        "max_m":       round(float(np.max(valid)), 3),
        "mean_m":      round(float(np.mean(valid)), 3),
        "median_m":    round(float(np.median(valid)), 3),
        "p05_m":       round(float(np.percentile(valid, 5)), 3),
        "p95_m":       round(float(np.percentile(valid, 95)), 3),
        "domain_min":  round(-span, 3),
        "domain_max":  round( span, 3),
        "synthetic":   False,
    }


def build_dashboard_report(meta: dict, kpis: dict, gen_date: str) -> dict:
    """The single JSON payload the dashboard reads.
    """
    dod      = meta.get("dod_stats", {}) or {}
    stable   = meta.get("stable_terrain", {}) or {}
    mp       = meta.get("multi_period_dod", {}) or {}
    vect     = meta.get("vectorization", {}) or {}

    total_area = float(kpis.get("total_area_ha") or 0)
    gain_area  = float(kpis.get("gain_area_ha")  or 0)
    loss_area  = float(kpis.get("loss_area_ha")  or 0)
    disturbed  = float(kpis.get("disturbed_ha")  or 0)
    stable_pct = float(stable.get("stable_pct")  or 0)
    loss_pct   = (loss_area / total_area * 100.0) if total_area > 0 else 0.0
    gain_pct   = (gain_area / total_area * 100.0) if total_area > 0 else 0.0
    other_pct  = max(0.0, 100.0 - stable_pct - loss_pct - gain_pct)

    vol_gain   = float(kpis.get("vol_gain_m3") or 0)
    vol_loss_abs = abs(float(kpis.get("vol_loss_m3") or 0))
    cut_fill   = float(kpis.get("cut_fill_ratio") or 0)

    histogram = build_histogram(meta.get("dod_path"), float(dod.get("lod_95_m") or 0))

    def _period_payload(stats, label):
        if not stats:
            return None
        net = float(stats.get("net_vol_m3") or 0)
        vg  = float(stats.get("vol_gain_m3") or 0)
        vl  = abs(float(stats.get("vol_loss_m3") or 0))
        return {
            "label":          label,
            "net_vol_m3":     net,
            "vol_gain_m3":    vg,
            "vol_loss_m3":    -vl,
            "moved_m3":       vg + vl,
            "mean_change_m":  float(stats.get("mean_change_m") or 0),
            "std_change_m":   float(stats.get("std_change_m")  or 0),
            "min_change_m":   float(stats.get("min_change_m")  or 0),
            "max_change_m":   float(stats.get("max_change_m")  or 0),
            "n_valid_pixels": int(stats.get("n_valid_pixels") or 0),
        }

    p1 = _period_payload(mp.get("period1_stats"), mp.get("period1_label"))
    p2 = _period_payload(mp.get("period2_stats"), mp.get("period2_label"))

    multi_period = {"period1": p1, "period2": p2}
    if p1 and p2:
        v1 = p1["net_vol_m3"]
        v2 = p2["net_vol_m3"]
        delta_abs = v2 - v1
        delta_pct = ((v2 - v1) / abs(v1) * 100.0) if abs(v1) > 1e-3 else None
        # 'sign' is how period 2 trends vs period 1
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


    return {
        "generated_at":   gen_date,
        "schema_version": 1,
        "kpis": {
            "total_area_ha":     round(total_area, 3),
            "disturbed_area_ha": round(disturbed, 3),
            "disturbed_pct":     round(float(kpis.get("disturbed_pct") or 0), 2),
            "gain_area_ha":      round(gain_area, 3),
            "loss_area_ha":      round(loss_area, 3),
            "vol_gain_m3":       round(vol_gain, 2),
            "vol_loss_m3":       round(-vol_loss_abs, 2),
            "net_vol_m3":        round(float(kpis.get("net_vol_m3") or 0), 2),
            "moved_m3":          round(vol_gain + vol_loss_abs, 2),
            "mean_change_m":     round(float(kpis.get("mean_change_m") or 0), 3),
            "std_change_m":      round(float(kpis.get("std_change_m")  or 0), 3),
            "max_excavation_m":  round(float(kpis.get("max_excavation_m") or 0), 3),
            "max_deposition_m":  round(float(kpis.get("max_deposition_m") or 0), 3),
            "cut_fill_ratio":    round(cut_fill, 3),
            "intensity_m3_per_m2": round(float(kpis.get("intensity_m3_per_m2") or 0), 4),
            "lod_m":             round(float(dod.get("lod_95_m") or 0), 3),
            "n_pixels":          int(dod.get("n_valid_pixels") or 0),
            "n_zones":           int(kpis.get("n_zones") or 0),
            "n_gain_zones":      int(kpis.get("n_gain_zones") or 0),
            "n_loss_zones":      int(kpis.get("n_loss_zones") or 0),
            "net_vol_sign":      "deposition" if float(kpis.get("net_vol_m3") or 0) > 0 else (
                                  "excavation" if float(kpis.get("net_vol_m3") or 0) < 0 else "balanced"),
        },
        "mai": {
            # 0,1 normalised inputs
            "V_norm":  float(kpis.get("V_norm", 0)),
            "A_norm":  float(kpis.get("A_norm", 0)),
            "DZ_norm": float(kpis.get("DZ_norm", 0)),
            # 0,1 weighted scores
            "economic":      float(kpis.get("mai_economic", 0)),
            "environmental": float(kpis.get("mai_environment", 0)),
            "risk":          float(kpis.get("mai_risk", 0)),
            "standard":      float(kpis.get("mai_standard", 0)),
            "activity_level": kpis.get("activity_level", "Low"),
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
            "stable_nmad_m":    float(stable.get("stable_nmad_m") or 0),
            "sigma_combined_m": float(stable.get("sigma_combined_m") or 0),
            "n_stable_pixels":  int(stable.get("n_stable_pixels") or 0),
            "lod_95_m":         float(stable.get("lod_95_m") or dod.get("lod_95_m") or 0),
            "interpretation": (
                "Excellent co-registration: measured changes are highly reliable."
                if stable_pct > 80 else
                "Strong co-registration: most of the bbox is unchanged."
                if stable_pct > 50 else
                "Moderate co-registration: interpret small changes with caution."
                if stable_pct > 20 else
                "Limited stable area: only large-magnitude changes are trustworthy."
            ),
        },
        "multi_period": multi_period,
        "vectorization": {
            "n_zones":      int(vect.get("n_zones") or 0),
            "n_gain_zones": int(vect.get("n_gain_zones") or 0),
            "n_loss_zones": int(vect.get("n_loss_zones") or 0),
            "threshold_m":  float(vect.get("threshold_m") or 0),
            "gain_area_ha": float(vect.get("gain_area_ha") or 0),
            "loss_area_ha": float(vect.get("loss_area_ha") or 0),
        },
    }


def class_table(vect_meta):
    """Change-class breakdown with primary header band."""
    header = [
        Paragraph("Change Class",  ST_LABEL),
        Paragraph("Zones",         ST_LABEL),
        Paragraph("Area (ha)",     ST_LABEL),
        Paragraph("% of Total",    ST_LABEL),
    ]
    data = [header]
    total_ha = vect_meta.get("gain_area_ha", 0) + vect_meta.get("loss_area_ha", 0)

    loss_ha = vect_meta.get("loss_area_ha", 0)
    loss_n  = vect_meta.get("n_loss_zones", 0)
    if loss_n > 0:
        pct = (loss_ha / total_ha * 100) if total_ha > 0 else 0
        data.append([
            Paragraph("Total Loss (excavation)", ST_VALUE),
            Paragraph(str(loss_n), ST_VAL_NEG),
            Paragraph(f"{loss_ha:,.2f}", ST_VAL_NEG),
            Paragraph(f"{pct:.1f}%", ST_VAL_NEG),
        ])

    gain_ha = vect_meta.get("gain_area_ha", 0)
    gain_n  = vect_meta.get("n_gain_zones", 0)
    if gain_n > 0:
        pct = (gain_ha / total_ha * 100) if total_ha > 0 else 0
        data.append([
            Paragraph("Total Gain (deposition)", ST_VALUE),
            Paragraph(str(gain_n), ST_VAL_POS),
            Paragraph(f"{gain_ha:,.2f}", ST_VAL_POS),
            Paragraph(f"{pct:.1f}%", ST_VAL_POS),
        ])

    total_zones = vect_meta.get("n_zones", 0)
    bold = _style("tb", fontSize=9, textColor=PRIMARY, fontName="Helvetica-Bold")

    if total_zones == 0:
        data.append([
            Paragraph("No significant change zones", bold),
            Paragraph("0", bold),
            Paragraph("0.00", bold),
            Paragraph("—", bold),
        ])
    data.append([
        Paragraph("TOTAL", bold),
        Paragraph(str(total_zones), bold),
        Paragraph(f"{total_ha:,.2f}", bold),
        Paragraph("100%" if total_ha > 0 else "—", bold),
    ])

    col_w = [W_PAGE * 0.40, W_PAGE * 0.18, W_PAGE * 0.22, W_PAGE * 0.20]
    t = LongTable(data, colWidths=col_w)
    t.setStyle(TableStyle(base_table_style() + [
        ("BACKGROUND",     (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR",      (0, 0), (-1, 0), WHITE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, BG_LIGHT]),
        ("BACKGROUND",     (0, -1), (-1, -1), BG_LIGHT),
    ]))
    return t


def generate_site_report(site_name, output_dir, meta):
    gen_date    = datetime.now().strftime("%B %d, %Y  –  %H:%M")
    safe_name   = site_name.replace(" ", "_")
    report_path = os.path.join(output_dir, f"{safe_name}_Site_Report.pdf")
    dod_stats   = meta.get("dod_stats", {})
    vec_meta    = meta.get("vectorization", {})
    kpis        = compute_kpis(meta)
    coreg       = meta.get("coregistration", {})
    stable      = meta.get("stable_terrain", {})

    doc = SimpleDocTemplate(
        report_path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
    )

    story = []
    story.extend(header_strip(
        f"{site_name.upper()} — Mining Site Analysis Report",
        f"Generated {gen_date}",
    ))

   
    story.append(section_major("Executive Summary"))
    story.append(Spacer(1, 4 * mm))

    level = kpis["activity_level"]
    mai_score = kpis["mai_standard"]
    if level == "High":
        lvl_color = DANGER
    elif level == "Medium":
        lvl_color = WARNING
    else:
        lvl_color = SUCCESS
    lvl_style = _style("lv", fontSize=16, textColor=lvl_color,
                       fontName="Helvetica-Bold", alignment=1, leading=19)

    story.append(kpi_row([
        kpi_card(f"{level}", "Mining Activity Level", lvl_style),
        kpi_card(f"{kpis['disturbed_pct']:.1f}%", "Area Disturbed"),
        kpi_card(f"{kpis['max_excavation_m']:.0f} m", "Max Excavation Depth"),
        kpi_card(f"{kpis['n_zones']}", "Change Zones Detected"),
    ]))
    story.append(Spacer(1, 3 * mm))

    net_color = DANGER if kpis["net_vol_m3"] < 0 else SUCCESS
    net_style = _style("ns", fontSize=14, textColor=net_color,
                       fontName="Helvetica-Bold", alignment=1, leading=17)
    dan_style = _style("vr", fontSize=14, textColor=DANGER,
                       fontName="Helvetica-Bold", alignment=1, leading=17)
    suc_style = _style("vg", fontSize=14, textColor=SUCCESS,
                       fontName="Helvetica-Bold", alignment=1, leading=17)

    story.append(kpi_row([
        kpi_card(fmt_vol_clean(kpis["vol_loss_m3"]), "Volume Excavated", dan_style),
        kpi_card(fmt_vol_clean(kpis["vol_gain_m3"]), "Volume Deposited", suc_style),
        kpi_card(fmt_vol_clean(kpis["net_vol_m3"]),  "Net Volume Change", net_style),
        kpi_card(f"{kpis['cut_fill_ratio']:.2f}",    "Cut / Fill Ratio"),
    ]))
    story.append(Spacer(1, 7 * mm))

    # ── Mining Activity Index ───────────────────────────────────────────
    story.append(section_major("Mining Activity Index"))
    story.append(Spacer(1, 4 * mm))

    mai_num_style = _style("me", fontSize=14, textColor=PRIMARY,
                           fontName="Helvetica-Bold", alignment=1, leading=17)
    story.append(kpi_row([
        kpi_card(f"{kpis['mai_economic']:.2f}",    "Economic / Production",   mai_num_style),
        kpi_card(f"{kpis['mai_environment']:.2f}", "Environmental Footprint", mai_num_style),
        kpi_card(f"{kpis['mai_risk']:.2f}",        "Early Warning / Risk",    mai_num_style),
        kpi_card(f"{mai_score:.2f}",               "Standard MAI",            lvl_style),
    ]))
    story.append(Spacer(1, 4 * mm))

    def _mai_score_style(score):
        if score > 0.70:
            return ST_VAL_NEG
        if score >= 0.30:
            return ST_VAL_WARN
        return ST_VAL_POS

    mai_rows = [[
        Paragraph("Monitoring Objective", ST_LABEL),
        Paragraph("W<sub>1</sub> (Volume)", ST_LABEL),
        Paragraph("W<sub>2</sub> (Area)", ST_LABEL),
        Paragraph("W<sub>3</sub> (Disp.)", ST_LABEL),
        Paragraph("Primary Focus", ST_LABEL),
        Paragraph("Score", ST_LABEL),
    ]]
    for name, w, focus, score in [
        ("Economic / Production",   ("0.60", "0.20", "0.20"), "Tonnage extraction",   kpis['mai_economic']),
        ("Environmental Footprint", ("0.20", "0.60", "0.20"), "Lateral expansion",    kpis['mai_environment']),
        ("Early Warning / Risk",    ("0.20", "0.30", "0.50"), "Terrain instability",  kpis['mai_risk']),
    ]:
        mai_rows.append([
            Paragraph(name, ST_VALUE),
            Paragraph(w[0], ST_VALUE), Paragraph(w[1], ST_VALUE), Paragraph(w[2], ST_VALUE),
            Paragraph(focus, ST_VALUE),
            Paragraph(f"{score:.3f}", _mai_score_style(score)),
        ])

    mai_col_w = [W_PAGE * 0.26, W_PAGE * 0.11, W_PAGE * 0.11,
                 W_PAGE * 0.11, W_PAGE * 0.26, W_PAGE * 0.15]
    mai_t = LongTable(mai_rows, colWidths=mai_col_w)
    mai_t.setStyle(TableStyle(base_table_style() + [
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR",  (0, 0), (-1, 0), WHITE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, BG_LIGHT]),
    ]))
    story.append(mai_t)
    story.append(Spacer(1, 4 * mm))

    story.append(info_table([
        ("V<sub>norm</sub> (Volume)",
         f"{kpis['V_norm']:.4f}  (|net vol| / 1 B m³, capped at 1.0)"),
        ("A<sub>norm</sub> (Area)",
         f"{kpis['A_norm']:.4f}  (disturbed % / 100)"),
        ("ΔZ<sub>norm</sub> (Displacement)",
         f"{kpis['DZ_norm']:.4f}  (max excavation / 100 m, capped at 1.0)"),
        ("Standard MAI",
         f"{mai_score:.4f}  →  {level}",
         lvl_style if level == "High" else ST_VALUE),
    ]))
    story.append(Spacer(1, 7 * mm))

    story.append(section_minor("Site Overview"))
    story.append(Spacer(1, 3 * mm))

    bbox = meta.get("bbox") or meta.get("bounds") or []
    bbox_str = (f"[{bbox[0]:.4f}, {bbox[1]:.4f}, {bbox[2]:.4f}, {bbox[3]:.4f}]"
                if len(bbox) == 4 else "N/A")

    coreg_str = "N/A"
    if coreg and "shift_east_px" in coreg:
        coreg_str = (f"East: {coreg['shift_east_px']} px  |  "
                     f"North: {coreg['shift_north_px']} px  |  "
                     f"Vertical: {coreg['shift_z_m']} m")
    elif coreg.get("error"):
        coreg_str = f"Failed: {coreg['error']}"

    lod_m = stable.get("lod_95_m", vec_meta.get("threshold_m", "N/A"))
    threshold_src = vec_meta.get("threshold_source", "fixed")
    if threshold_src == "statistical_lod_95":
        lod_label = (f"±{lod_m} m  (statistical LoD @ 95% confidence, "
                     f"NMAD = {stable.get('stable_nmad_m', '?')} m)")
    else:
        lod_label = f"±{lod_m} m  (fixed threshold)"

    story.append(info_table([
        ("Site Name",       site_name),
        ("Bounding Box",    bbox_str),
        ("Modern DEM",      "Copernicus GLO-30  (2011–2015)  |  ESA"),
        ("Baseline DEM",    "NASADEM  (Feb 2000)  |  NASA SRTM"),
        ("Analysis Method", "DEM of Difference  (Copernicus − NASADEM)"),
        ("Co-registration", f"Nuth & Kääb (2011)  |  {coreg_str}"),
        ("Noise Threshold", lod_label),
        ("Min Zone Area",   f"{vec_meta.get('min_zone_area_m2', 'N/A'):,} m²"),
    ]))
    story.append(Spacer(1, 7 * mm))

    if stable:
        story.append(section_minor("Error Budget — Stable Terrain Analysis"))
        story.append(Spacer(1, 3 * mm))
        story.append(info_table([
            ("Stable Terrain Pixels",
             f"{stable.get('n_stable_pixels', 0):,}  "
             f"({stable.get('stable_pct', 0):.1f}% of valid area)"),
            ("Stable Mean Offset",
             f"{stable.get('stable_mean_m', 0):+.4f} m  (residual bias after co-reg)"),
            ("Stable Std Deviation",
             f"{stable.get('stable_std_m', 0):.4f} m"),
            ("Stable NMAD",
             f"{stable.get('stable_nmad_m', 0):.4f} m  (robust error estimate)"),
            ("Level of Detection (95%)",
             f"±{stable.get('lod_95_m', 0):.2f} m  "
             f"(t={stable.get('t_value', 1.96)} × NMAD)"),
        ]))
        story.append(Spacer(1, 7 * mm))

    story.append(section_minor("Coverage & Resolution"))
    story.append(Spacer(1, 3 * mm))
    story.append(info_table([
        ("Total Valid Pixels",   f"{dod_stats.get('n_valid_pixels', 0):,}"),
        ("Pixel Area",           f"{dod_stats.get('pixel_area_m2', 0):.1f} m²"),
        ("Total Study Area",     f"{kpis['total_area_ha']:,.2f} ha  "
                                 f"({kpis['total_area_ha'] * 10000:,.0f} m²)"),
        ("Area with Gain",       f"{kpis['gain_area_ha']:,.2f} ha", ST_VAL_POS),
        ("Area with Loss",       f"{kpis['loss_area_ha']:,.2f} ha", ST_VAL_NEG),
        ("Disturbed Area",       f"{kpis['disturbed_ha']:,.2f} ha  "
                                 f"({kpis['disturbed_pct']:.1f}% of study area)",
         ST_VAL_WARN if kpis['disturbed_pct'] > 10 else ST_VALUE),
    ]))
    story.append(Spacer(1, 7 * mm))

    story.append(section_minor("Elevation Change Statistics"))
    story.append(Spacer(1, 3 * mm))
    story.append(info_table([
        ("Mean Change",    f"{kpis['mean_change_m']:+.4f} m",
         val_style_auto(kpis['mean_change_m'])),
        ("Std Deviation",  f"{kpis['std_change_m']:.4f} m"),
        ("Max Excavation", f"{kpis['max_excavation_m']:.2f} m  (deepest cut)",
         ST_VAL_NEG),
        ("Max Deposition", f"{kpis['max_deposition_m']:.2f} m  (highest fill)",
         ST_VAL_POS),
    ]))
    story.append(Spacer(1, 7 * mm))

    story.append(section_minor("Volume Analysis"))
    story.append(Spacer(1, 3 * mm))
    story.append(info_table([
        ("Volume Excavated (cut)",  fmt_vol_clean(kpis['vol_loss_m3']), ST_VAL_NEG),
        ("Volume Deposited (fill)", fmt_vol_clean(kpis['vol_gain_m3']), ST_VAL_POS),
        ("Net Volume Change",       fmt_vol_clean(kpis['net_vol_m3']),
         val_style_auto(kpis['net_vol_m3'])),
        ("Cut / Fill Ratio",        f"{kpis['cut_fill_ratio']:.2f}  "
         f"({'more cut' if kpis['cut_fill_ratio'] > 1 else 'more fill'})",
         ST_VAL_NEG if kpis['cut_fill_ratio'] > 1 else ST_VAL_POS),
        ("Change Intensity",        f"{kpis['intensity_m3_per_m2']:.4f} m³/m²  "
         "(net volume per unit area)"),
    ]))
    story.append(Spacer(1, 7 * mm))

    story.append(section_minor("Change Zone Analysis (Vectorization)"))
    story.append(Spacer(1, 3 * mm))
    story.append(class_table(vec_meta))
    story.append(Spacer(1, 4 * mm))
    story.append(info_table([
        ("Standard MAI",         f"{mai_score:.4f}  →  {level}",
         lvl_style if level == "High" else ST_VALUE),
        ("Significant Zones",    f"{kpis['n_zones']}  "
         f"({kpis['n_loss_zones']} loss  ·  {kpis['n_gain_zones']} gain)"),
        ("Vectorized Loss Area", f"{kpis['vec_loss_ha']:,.2f} ha", ST_VAL_NEG),
        ("Vectorized Gain Area", f"{kpis['vec_gain_ha']:,.2f} ha", ST_VAL_POS),
    ]))
    story.append(Spacer(1, 6 * mm))


    story.append(PageBreak())

    zones_png = vec_meta.get("zones_map_path", "")
    if zones_png and os.path.exists(zones_png):
        story.append(section_major("Change Zones — Filled Choropleth on Satellite"))
        story.append(Spacer(1, 3 * mm))
        story.append(img_block(
            zones_png,
            "Filled zones classified by change magnitude  |  "
            "Warm = excavation (loss)  ·  Cool = deposition (gain)  |  "
            f"Noise threshold: ±{vec_meta.get('threshold_m', '?')} m",
        ))
        story.append(Spacer(1, 6 * mm))

    for fname, title, ratio in [
        (f"{safe_name}_DoD_Map.png",       "DoD Map — Plan View", 1.0),
        (f"{safe_name}_DoD_Satellite.png", "DoD on Satellite Imagery", 1.0),
        (f"{safe_name}_DoD_3D.png",        "DoD on Terrain — 3D Relief View", 1.0),
        (f"{safe_name}_DoD_Histogram.png", "Elevation Change Distribution", 4/9),
    ]:
        p = os.path.join(output_dir, fname)
        if os.path.exists(p):
            story.append(section_minor(title))
            story.append(Spacer(1, 3 * mm))
            story.append(img_block(p, title, ratio=ratio))
            story.append(Spacer(1, 6 * mm))

    mp_meta = meta.get("multi_period_dod", {})
    mp_png  = mp_meta.get("comparison_map", "")
    if mp_png and os.path.exists(mp_png):
        story.append(section_minor("Multi-Period Elevation Change"))
        story.append(Spacer(1, 3 * mm))
        story.append(img_block(
            mp_png,
            "Three-period DoD comparison  |  "
            "Period 1: 2000→2006-11  ·  Period 2: 2006-11→2011-15  ·  Full span",
            ratio=7 / 20,
        ))
        story.append(Spacer(1, 6 * mm))

    story.append(PageBreak())
    story.append(section_major("How to Read This Report"))
    story.append(Spacer(1, 4 * mm))

    guide_rows = [
        ("Cut / Fill Ratio",
         "Ratio > 1 means more material was removed than deposited (typical "
         "open-pit mining). Ratio < 1 suggests net deposition."),
        ("Mining Activity Index",
         "MAI = W1×V_norm + W2×A_norm + W3×ΔZ_norm across three monitoring "
         "objectives. Standard MAI = worst-case. > 0.70 High, 0.30–0.70 Medium, "
         "< 0.30 Low. If zero significant zones are detected, the MAI is "
         "forced to 0."),
        ("Disturbed Area %",
         "Percentage of the study area where elevation changed beyond the LoD. "
         "For active mines, 10–40% is typical."),
        ("Change Intensity",
         "Net volume change per m² of study area."),
        ("Co-registration (Nuth & Kääb)",
         "Sub-pixel horizontal alignment + vertical bias removal between the "
         "two DEMs using slope/aspect correlation. Removes false 'butterfly' "
         "artefacts on slopes."),
        ("Stable Terrain Analysis",
         "Noise floor measured on low-slope terrain where no real change is "
         "expected. The residual mean is subtracted from the DoD before "
         "statistics and vectorization, so a biased DEM pair does not create "
         "fake activity on quiet sites."),
        ("NMAD",
         "Normalised Median Absolute Deviation: robust spread measure, used "
         "instead of std dev for DEM error."),
        ("Level of Detection (LoD)",
         "LoD = 1.96 × NMAD. Elevation changes below LoD are treated as noise "
         "and excluded from every downstream computation."),
        ("Vectorized Zones",
         "Only contiguous areas above the minimum mapping unit are reported. "
         "Small scattered changes are filtered out."),
    ]
    gdata = [[Paragraph("KPI", ST_LABEL), Paragraph("Interpretation", ST_LABEL)]]
    kpi_lbl = _style("gl", fontSize=8.5, textColor=PRIMARY, fontName="Helvetica-Bold")
    desc_st = _style("gd", fontSize=8.5, textColor=TEXT_MAIN, fontName="Helvetica",
                      leading=11)
    for lbl, desc in guide_rows:
        gdata.append([Paragraph(lbl, kpi_lbl), Paragraph(desc, desc_st)])

    gt = LongTable(gdata, colWidths=[45 * mm, W_PAGE - 45 * mm])
    gt.setStyle(TableStyle(base_table_style() + [
        ("BACKGROUND",     (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR",      (0, 0), (-1, 0), WHITE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, BG_LIGHT]),
        ("VALIGN",         (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(gt)
    story.append(Spacer(1, 8 * mm))

    story.extend(footer(
        f"Report generated: {gen_date}"
    ))

    doc.build(story)
    return report_path



def main():
    log("Generating stakeholder-ready site report")

    if len(sys.argv) < 3:
        fail(
            "Missing arguments. Expected: python Site_Report.py <site_name> <config_path>",
            hint="The DAG should pass these automatically; check resolve_site output.",
        )

    target_site_name = sys.argv[1].strip()
    _ = sys.argv[2].strip()

    safe_name     = target_site_name.replace(" ", "_")
    output_dir    = os.path.join("DEM_Downloads", safe_name)
    metadata_path = os.path.join(output_dir, "fetch_metadata.json")

    if not os.path.exists(metadata_path):
        fail(
            f"fetch_metadata.json missing at {metadata_path}",
            hint="Run the upstream tasks (fetch_bbox → align_dems → calculate_dod → vectorization) first.",
        )

    with open(metadata_path) as f:
        meta = json.load(f)

    if not meta.get("dod_stats"):
        log(f"DoD stats not present for {target_site_name}; cannot build site report.", level="WARN")
        sys.exit(0)
    if not meta.get("vectorization"):
        log(f"Vectorization not present for {target_site_name}; cannot build site report.", level="WARN")
        sys.exit(0)

    log(f"Site: {target_site_name}")
    log("Assembling KPIs, MAI scores and visualisations into PDF…")
    try:
        report_path = generate_site_report(target_site_name, output_dir, meta)
    except FileNotFoundError as e:
        fail(
            f"A required image asset was not found: {e}",
            hint="One of the earlier visualisation steps did not produce its PNG. Re-run calculate_dod and vectorization.",
        )
    except Exception as e:
        fail(
            f"Site report PDF assembly failed ({e.__class__.__name__}: {e})",
            hint="If ReportLab complains about fonts/encoding, restart the worker; otherwise check the log above for the offending KPI.",
        )
    log(f"Report saved to {report_path}")

    meta["site_report_path"] = report_path
    try:
        kpis = compute_kpis(meta)
        gen_date = datetime.now().strftime("%B %d, %Y  –  %H:%M")
        meta["report"] = build_dashboard_report(meta, kpis, gen_date)
        log("Dashboard payload written to metadata['report'] "
            f"(MAI={meta['report']['mai']['standard']:.2f} · {meta['report']['mai']['activity_level']})")
    except Exception as e:
        log(f"Failed to build dashboard report block: {e.__class__.__name__}: {e}", level="WARN")

    with open(metadata_path, 'w') as f:
        json.dump(meta, f, indent=2)

    log(f"Metadata updated at {metadata_path}")
    log("Next step: upload_* tasks push artifacts to Azure")


if __name__ == "__main__":
    main()

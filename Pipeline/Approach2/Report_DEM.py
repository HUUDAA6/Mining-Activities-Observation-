import os
import sys
import json
from datetime import datetime

import numpy as np
import rasterio
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource
import matplotlib.patches as patches
import contextily as ctx

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer

from report_theme import (
    HEX,
    header_strip, section_major, section_minor, info_table, img_block, footer,
    apply_matplotlib_defaults,
)

apply_matplotlib_defaults()


STEP = "[Report_DEM]"


def log(msg, level="INFO"):
    print(f"{STEP} {level}: {msg}", flush=True)


def fail(reason, hint=None, code=1):
    log(reason, level="ERROR")
    if hint:
        log(f"Suggested fix: {hint}", level="HINT")
    sys.exit(code)


# ── Raster helpers ───────────────────────────────────────────────────────────

def get_file_info(filepath):
    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    with rasterio.open(filepath) as src:
        crs    = src.crs.to_string() if src.crs else "Unknown"
        res_x  = abs(src.transform.a)
        res_y  = abs(src.transform.e)
        width  = src.width
        height = src.height
        dtype  = src.dtypes[0]
        b      = src.bounds
    return {
        "size_mb": size_mb,
        "crs"    : crs,
        "res_x"  : res_x,
        "res_y"  : res_y,
        "width"  : width,
        "height" : height,
        "dtype"  : dtype,
        "bounds" : b,
    }


def create_context_map(bounds, output_png_path):
    """Satellite context map with an accent-colored AOI rectangle."""
    min_lon, min_lat, max_lon, max_lat = bounds

    fig, ax = plt.subplots(figsize=(8, 6))
    pad_x = (max_lon - min_lon) * 0.5
    pad_y = (max_lat - min_lat) * 0.5
    ax.set_xlim(min_lon - pad_x, max_lon + pad_x)
    ax.set_ylim(min_lat - pad_y, max_lat + pad_y)

    rect = patches.Rectangle(
        (min_lon, min_lat),
        max_lon - min_lon,
        max_lat - min_lat,
        linewidth=2.5,
        edgecolor=HEX["accent"],
        facecolor="none",
    )
    ax.add_patch(rect)

    try:
        ctx.add_basemap(ax, crs="EPSG:4326", source=ctx.providers.Esri.WorldImagery)
    except Exception as e:
        log(
            f"Satellite tiles unavailable ({e.__class__.__name__}: {e}); "
            "context map will show the AOI rectangle on a plain background.",
            level="WARN",
        )

    ax.axis("off")
    fig.savefig(output_png_path, bbox_inches="tight", dpi=300,
                facecolor=HEX["white"])
    plt.close(fig)


def create_dem_visualization(tif_path, output_png_path):
    """Smoothed 3D shaded relief + elevation colorbar."""
    with rasterio.open(tif_path) as src:
        data   = src.read(1)
        nodata = src.nodata
        res_x  = abs(src.transform.a)
        res_y  = abs(src.transform.e)

    if nodata is not None:
        data = np.ma.masked_equal(data, nodata)
    else:
        data = np.ma.masked_less_equal(data, -9999)

    vmin = float(data.min())
    vmax = float(data.max())

    aspect_ratio = data.shape[0] / data.shape[1]
    fig, ax = plt.subplots(figsize=(9, 8 * aspect_ratio))

    ls   = LightSource(azdeg=315, altdeg=45)
    cmap = plt.get_cmap("turbo")
    dx_meters = res_x * 111320
    dy_meters = res_y * 111320
    rgb = ls.shade(data, cmap=cmap, vmin=vmin, vmax=vmax, blend_mode="overlay",
                   dx=dx_meters, dy=dy_meters, vert_exag=2)

    ax.imshow(rgb, interpolation="bilinear")

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(vmin=vmin, vmax=vmax))
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Elevation (meters)", rotation=270, labelpad=15,
                   fontweight="bold", color=HEX["text_main"])
    cbar.ax.tick_params(colors=HEX["text_muted"])

    ax.axis("off")
    fig.savefig(output_png_path, bbox_inches="tight", dpi=300,
                facecolor=HEX["white"])
    plt.close(fig)


# ── PDF ──────────────────────────────────────────────────────────────────────

def generate_report(site_name):
    safe_name  = site_name.replace(" ", "_")
    output_dir = os.path.join("DEM_Downloads", safe_name)

    cop_path  = os.path.join(output_dir, "Modern_Copernicus_GLO30.tif")
    nasa_path = os.path.join(output_dir, "Baseline_NASADEM_Aligned.tif")

    missing = [p for p in (cop_path, nasa_path) if not os.path.exists(p)]
    if missing:
        fail(
            f"Required DEM file(s) not found on disk: {missing}",
            hint=(
                "The aligner did not finish — check its logs and re-run align_dems. "
                "If it ran but the files were wiped, also re-run fetch_bbox."
            ),
        )

    cop_info  = get_file_info(cop_path)
    nasa_info = get_file_info(nasa_path)

    b = cop_info["bounds"]
    bounds = (b.left, b.bottom, b.right, b.top)

    report_path = os.path.join(output_dir, f"{safe_name}_DEM_Report.pdf")
    doc = SimpleDocTemplate(
        report_path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
    )

    story = []
    gen_date = datetime.now().strftime("%B %d, %Y  –  %H:%M")
    story.extend(header_strip(
        f"{site_name.upper()} — DEM Acquisition Report",
        f"Generated {gen_date}",
    ))

    # ── Area of Interest ────────────────────────────────────────────────
    story.append(section_major("Area of Interest"))
    story.append(Spacer(1, 4 * mm))
    story.append(info_table([
        ("Min Longitude (West)",  f"{bounds[0]:.6f} °"),
        ("Min Latitude (South)",  f"{bounds[1]:.6f} °"),
        ("Max Longitude (East)",  f"{bounds[2]:.6f} °"),
        ("Max Latitude (North)",  f"{bounds[3]:.6f} °"),
        ("Longitude Span",
         f"{abs(bounds[2] - bounds[0]) * 111.32:.2f} km (approx.)"),
        ("Latitude Span",
         f"{abs(bounds[3] - bounds[1]) * 111.32:.2f} km (approx.)"),
    ]))
    story.append(Spacer(1, 5 * mm))

    context_map_path = os.path.join(output_dir, f"{safe_name}_Context_Map.png")
    log("Rendering satellite context map…")
    try:
        create_context_map(bounds, context_map_path)
    except Exception as e:
        fail(
            f"Context map render failed ({e.__class__.__name__}: {e})",
            hint="Likely matplotlib / contextily install mismatch. The pipeline continues without this image.",
        )
    story.append(img_block(
        context_map_path,
        "Satellite context map : area of interest outlined in accent colour",
        ratio=0.75,
    ))
    story.append(Spacer(1, 7 * mm))

    # ── Dataset sections ────────────────────────────────────────────────
    datasets = [
        {
            "title"      : "Modern DEM — Copernicus GLO-30",
            "source"     : "European Space Agency (ESA) — Copernicus Programme",
            "acquisition": "2011 – 2015",
            "sensor"     : "TanDEM-X  (X-band SAR interferometry)",
            "nominal_res": "~30 m  (1 arc-second)",
            "filepath"   : cop_path,
            "short_name" : "Copernicus",
            "filename"   : os.path.basename(cop_path),
            "info"       : cop_info,
        },
        {
            "title"      : "Baseline DEM — NASADEM  (Year 2000)",
            "source"     : "NASA / JPL  (Jet Propulsion Laboratory)",
            "acquisition": "February 11 – 22, 2000  (SRTM Space Shuttle mission)",
            "sensor"     : "C-band & X-band SAR  (Shuttle Radar Topography Mission)",
            "nominal_res": "~30 m  (1 arc-second)",
            "filepath"   : nasa_path,
            "short_name" : "NASADEM",
            "filename"   : os.path.basename(nasa_path),
            "info"       : nasa_info,
        },
    ]

    meta_path = os.path.join(output_dir, "fetch_metadata.json")
    full_meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            full_meta = json.load(f)

    aw3d_path = full_meta.get("aw3d30_aligned")
    if aw3d_path and os.path.exists(aw3d_path):
        datasets.append({
            "title"      : "Intermediate DEM — AW3D30  (2006–2011)",
            "source"     : "JAXA — ALOS World 3D 30 m (OpenTopography)",
            "acquisition": "2006 – 2011",
            "sensor"     : "PRISM  (Optical stereo photogrammetry, ALOS satellite)",
            "nominal_res": "~30 m  (1 arc-second)",
            "filepath"   : aw3d_path,
            "short_name" : "AW3D30",
            "filename"   : os.path.basename(aw3d_path),
            "info"       : get_file_info(aw3d_path),
        })

    log(f"Rendering topography visualisations for {len(datasets)} dataset(s)…")
    for i, ds in enumerate(datasets):
        info = ds["info"]
        db = info["bounds"]
        header_fn = section_major if i == 0 else section_minor
        story.append(header_fn(f"{i+1}. {ds['title']}"))
        story.append(Spacer(1, 3 * mm))
        story.append(info_table([
            ("Source",             ds["source"]),
            ("Acquisition Period", ds["acquisition"]),
            ("Sensor / Method",    ds["sensor"]),
            ("Nominal Resolution", ds["nominal_res"]),
            ("Pixel Size (X)",
             f"{info['res_x']:.8f} °  ≈  {info['res_x'] * 111320:.1f} m"),
            ("Pixel Size (Y)",
             f"{info['res_y']:.8f} °  ≈  {info['res_y'] * 111320:.1f} m"),
            ("Raster Dimensions",
             f"{info['width']} px  ×  {info['height']} px"),
            ("Data Type",          info["dtype"]),
            ("Coordinate System",  info["crs"]),
            ("Extent — W / E",
             f"{db.left:.6f} °  /  {db.right:.6f} °"),
            ("Extent — S / N",
             f"{db.bottom:.6f} °  /  {db.top:.6f} °"),
            ("File Size",          f"{info['size_mb']:.2f} MB"),
            ("Output Filename",    ds["filename"]),
        ]))
        story.append(Spacer(1, 4 * mm))

        png_path = os.path.join(output_dir, f"{ds['short_name']}_Topography.png")
        try:
            create_dem_visualization(ds["filepath"], png_path)
        except Exception as e:
            fail(
                f"Topography render for {ds['short_name']} failed ({e.__class__.__name__}: {e})",
                hint="The DEM may have unusable nodata. Open it in QGIS to confirm.",
            )
        aspect = info["height"] / info["width"]
        story.append(img_block(
            png_path,
            f"{ds['short_name']}",
            ratio=min(aspect, 0.75),
        ))
        story.append(Spacer(1, 7 * mm))

    # ── Multi-Period DoD, if present ────────────────────────────────────
    mp_meta = full_meta.get("multi_period_dod", {})
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

    story.extend(footer(
        f"Generated: {gen_date}"
    ))

    doc.build(story)
    return report_path


if __name__ == "__main__":
    log("Building per-DEM acquisition report")

    if len(sys.argv) < 3:
        fail(
            "Missing arguments. Expected: python Report_DEM.py <site_name> <config_path>",
            hint="The DAG should pass these automatically; check resolve_site output.",
        )

    target_site_name = sys.argv[1].strip()
    _ = sys.argv[2].strip()

    try:
        path = generate_report(target_site_name)
    except SystemExit:
        raise
    except Exception as e:
        fail(
            f"DEM report assembly failed ({e.__class__.__name__}: {e})",
            hint="If ReportLab complains about fonts, restart the Airflow worker.",
        )
    log(f"Report saved to {path}")

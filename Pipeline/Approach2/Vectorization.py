import os
import sys
import json

import numpy as np
import rasterio
from rasterio.features import shapes, rasterize
from shapely.geometry import shape
import geopandas as gpd
from scipy import ndimage as ndi
from scipy.ndimage import sum_labels

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import to_rgba
import contextily as ctx

from report_theme import HEX, apply_matplotlib_defaults

apply_matplotlib_defaults()


STEP = "[Vectorization]"


def log(msg, level="INFO"):
    print(f"{STEP} {level}: {msg}", flush=True)


def fail(reason, hint=None, code=1):
    log(reason, level="ERROR")
    if hint:
        log(f"Suggested fix: {hint}", level="HINT")
    sys.exit(code)


# Defaults
NOISE_THRESHOLD_M      = 5.0      # fallback only , used if no LoD in metadata
MIN_ZONE_AREA_M2       = 100000.0  # 10 ha default (raised from 5 ha to kill tiny false positives)\
OPENING_ITERATIONS     = 1         # morphological opening (erode -> dilate)
LOD_SAFETY_MULTIPLIER  = 1.0       # bin edge = LoD * multiplier


# Change-class definitions 
def _change_bins(threshold):
    """
    Dynamic change-magnitude bins driven by the statistical LoD.
    Each entry: (low, high, zone_type, label, hex_colour).
    low=None -> -inf, high=None -> +inf, range is [low, high).
    """
    t = float(threshold) * LOD_SAFETY_MULTIPLIER
    # Three magnitude tiers per direction scaled off LoD: light / moderate / heavy / severe.
    light  = t
    moderate = max(t * 2, t + 5)
    heavy  = max(t * 5, t + 20)
    severe = max(t * 10, t + 50)

    # Muted but clear palette : warms = loss, cools/greens = gain.
    return [
        (None,      -severe,  "loss", f"Severe loss (< -{severe:.0f} m)",              "#7A2424"),
        (-severe,   -heavy,   "loss", f"Heavy loss (-{severe:.0f} to -{heavy:.0f} m)", "#A34444"),
        (-heavy,    -moderate,"loss", f"Moderate loss (-{heavy:.0f} to -{moderate:.0f} m)", HEX["danger"]),
        (-moderate, -light,   "loss", f"Light loss (-{moderate:.0f} to -{light:.1f} m)", HEX["warning"]),
        (light,      moderate,"gain", f"Light gain (+{light:.1f} to +{moderate:.0f} m)", "#9BC4A0"),
        (moderate,   heavy,   "gain", f"Moderate gain (+{moderate:.0f} to +{heavy:.0f} m)", HEX["success"]),
        (heavy,      severe,  "gain", f"Heavy gain (+{heavy:.0f} to +{severe:.0f} m)",  "#2F5F38"),
        (severe,     None,    "gain", f"Severe gain (> +{severe:.0f} m)",               "#1D3E23"),
    ]


def _classify_dod(dod, valid, threshold):
    """
    Assign each valid pixel to a change class, then apply a morphological
    opening (erode -> dilate) to kill isolated single-pixel noise before
    vectorisation. Returns (int8 array, bins).
    """
    bins = _change_bins(threshold)
    classified = np.zeros(dod.shape, dtype=np.int8)
    for i, (low, high, _, _, _) in enumerate(bins, start=1):
        mask = valid.copy()
        if low is not None:
            mask &= (dod >= low)
        if high is not None:
            mask &= (dod < high)
        classified[mask] = i

    # Morphological opening per class : removes salt-and-pepper pixels that
    # would otherwise vectorise into thousands of microscopic polygons.
    if OPENING_ITERATIONS > 0:
        cleaned = np.zeros_like(classified)
        for i in range(1, len(bins) + 1):
            layer = classified == i
            if not layer.any():
                continue
            opened = ndi.binary_opening(layer, iterations=OPENING_ITERATIONS)
            cleaned[opened] = i
        classified = cleaned

    return classified, bins


def _class_rgba(classified, bins, alpha=0.70):
    """Convert classified array to RGBA image for overlay rendering."""
    rgba = np.zeros((*classified.shape, 4), dtype=np.float32)
    for i, (_, _, _, _, hex_col) in enumerate(bins, start=1):
        mask = classified == i
        if not mask.any():
            continue
        r, g, b, _ = to_rgba(hex_col)
        rgba[mask] = [r, g, b, alpha]
    return rgba

def vectorize_dod(dod_path, threshold, pixel_area_m2, min_area_m2):
    """Classify the DoD raster, open, vectorise, filter by min area."""
    with rasterio.open(dod_path) as src:
        dod       = src.read(1).astype(np.float32)
        transform = src.transform
        crs       = src.crs
        bounds    = src.bounds

    valid = np.isfinite(dod) & (dod > -9000)
    classified, bins = _classify_dod(dod, valid, threshold)

    sig_mask = classified > 0
    if not sig_mask.any():
        log("No significant change zones above LoD, returning empty layer.", level="WARN")
        return gpd.GeoDataFrame(
            {c: [] for c in
             ["geometry", "change_class", "zone_type", "color",
              "area_m2", "area_ha", "volume_m3"]},
            geometry="geometry", crs=crs,
        ), bins

    raw_polys = list(shapes(
        classified.astype(np.int32), mask=sig_mask,
        connectivity=8, transform=transform))
    log(f"Polygons extracted from classified raster: {len(raw_polys)}")

    centre_lat    = (bounds.top + bounds.bottom) / 2.0
    m_per_deg_lon = 111320.0 * np.cos(np.radians(centre_lat))
    m_per_deg_lat = 111320.0
    half_pixel    = abs(transform.a) * 0.4

    records, skipped = [], 0
    for geom_dict, class_val in raw_polys:
        cv = int(class_val)
        if cv < 1 or cv > len(bins):
            continue

        poly    = shape(geom_dict)
        area_m2 = poly.area * m_per_deg_lon * m_per_deg_lat
        if area_m2 < min_area_m2:
            skipped += 1
            continue

        simplified = poly.simplify(half_pixel, preserve_topology=True)
        _, _, zone_type, label, hex_col = bins[cv - 1]
        records.append({
            "geometry"     : simplified,
            "_unsimplified": poly,                      # used for volume_m3 below
            "change_class" : label,
            "zone_type"    : zone_type,
            "color"        : hex_col,
            "area_m2"      : round(area_m2, 1),
            "area_ha"      : round(area_m2 / 10000, 4),
        })

    if skipped:
        log(f"Skipped {skipped} zone(s) below the {min_area_m2:,.0f} m² minimum mapping unit.")

    schema_cols = ["geometry", "change_class", "zone_type",
                   "color", "area_m2", "area_ha", "volume_m3"]
    if not records:
        log("All polygons were below the minimum mapping unit — returning empty layer.", level="WARN")
        return gpd.GeoDataFrame(
            {c: [] for c in schema_cols}, geometry="geometry", crs=crs), bins

    poly_ids = np.arange(1, len(records) + 1, dtype=np.int32)
    id_raster = rasterize(
        ((rec["_unsimplified"], pid) for rec, pid in zip(records, poly_ids)),
        out_shape=dod.shape,
        transform=transform,
        fill=0,
        dtype=np.int32,
    )
    clean_dod = np.where(valid, dod, 0.0).astype(np.float64)
    sum_per_poly = sum_labels(clean_dod, id_raster, index=poly_ids.tolist())

    for rec, s in zip(records, sum_per_poly):
        rec["volume_m3"] = round(float(s) * float(pixel_area_m2), 1)
        del rec["_unsimplified"]

    gdf = gpd.GeoDataFrame(records, crs=crs)
    return gdf, bins


def export_classified_tif(dod_path, output_tif_path, threshold):
    """Write the classified DoD as a GeoTIFF (class IDs, 0 = below LoD)."""
    with rasterio.open(dod_path) as src:
        dod       = src.read(1).astype(np.float32)
        out_meta  = src.meta.copy()

    valid = np.isfinite(dod) & (dod > -9000)
    classified, bins = _classify_dod(dod, valid, threshold)

    out_meta.update(dtype=rasterio.int8, nodata=0, count=1)
    with rasterio.open(output_tif_path, 'w', **out_meta) as dst:
        dst.write(classified, 1)

    return bins


def create_zones_map(dod_path, bins, output_png_path, threshold):
    """Render filled change zones on satellite imagery."""
    with rasterio.open(dod_path) as src:
        dod    = src.read(1).astype(np.float32)
        bounds = src.bounds

    valid = np.isfinite(dod) & (dod > -9000)
    classified, _ = _classify_dod(dod, valid, threshold)
    rgba = _class_rgba(classified, bins, alpha=0.70)

    extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
    aspect = dod.shape[0] / dod.shape[1]
    fig, ax = plt.subplots(figsize=(12, 12 * aspect))

    ax.set_xlim(bounds.left, bounds.right)
    ax.set_ylim(bounds.bottom, bounds.top)
    ax.set_facecolor(HEX["bg_light"])

    basemap_ok = True
    try:
        ctx.add_basemap(ax, crs='EPSG:4326',
                        source=ctx.providers.Esri.WorldImagery, zoom='auto')
    except Exception as e:
        basemap_ok = False
        log(
            f"Satellite tiles unavailable ({e.__class__.__name__}: {e}); "
            "rendering zones on plain background.",
            level="WARN",
        )
        ax.text(0.5, 0.01,
                "Satellite tiles unavailable! no internet access in worker",
                transform=ax.transAxes, fontsize=7, color=HEX["text_muted"],
                ha='center', va='bottom',
                bbox=dict(facecolor=HEX["white"], alpha=0.85,
                          edgecolor=HEX["secondary"], boxstyle='round,pad=0.3'))

    ax.imshow(rgba, extent=extent, origin='upper',
              interpolation='nearest', zorder=2)

    handles = []
    for i, (_, _, _, lbl, hex_col) in enumerate(bins, start=1):
        if (classified == i).any():
            handles.append(mpatches.Patch(
                facecolor=hex_col, edgecolor=HEX["white"], linewidth=0.5,
                label=lbl))

    if handles:
        leg = ax.legend(handles=handles, loc='lower left', fontsize=8,
                        framealpha=0.92, fancybox=True,
                        title=f'Elevation Change  (LoD = ±{threshold:.2f} m)',
                        title_fontsize=9)
        leg.get_frame().set_edgecolor(HEX["secondary"])
    else:
        ax.text(0.5, 0.5,
                f"No significant change detected above LoD (±{threshold:.2f} m)",
                transform=ax.transAxes, ha='center', va='center', fontsize=11,
                color=HEX["primary"], fontweight='bold',
                bbox=dict(facecolor=HEX["white"], alpha=0.92,
                          edgecolor=HEX["accent"], boxstyle='round,pad=0.8'))

    title = 'DoD Change Zones' if basemap_ok else 'DoD Change Zones (no satellite tiles)'
    ax.set_title(title, fontweight='bold', fontsize=13, pad=10,
                 color=HEX["primary"])
    ax.axis('off')

    fig.tight_layout()
    fig.savefig(output_png_path, dpi=300, bbox_inches='tight',
                facecolor=HEX["white"])
    plt.close(fig)

def main():
    log("Vectorizing DoD raster into change-zone polygons")

    if len(sys.argv) < 3:
        fail(
            "Missing arguments. Expected: python Vectorization.py <site_name> <config_path>",
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
            hint="Calculate_DoD must have failed. Re-run it first.",
        )

    with open(metadata_path) as f:
        meta = json.load(f)

    dod_path  = meta.get("dod_path")
    dod_stats = meta.get("dod_stats")

    if not dod_path or not os.path.exists(dod_path):
        log(f"DoD raster missing for {target_site_name}; skipping vectorization.", level="WARN")
        sys.exit(0)
    if not dod_stats:
        log(f"dod_stats missing for {target_site_name}; skipping vectorization.", level="WARN")
        sys.exit(0)

    pixel_area_m2 = dod_stats["pixel_area_m2"]

    stable_terrain = meta.get("stable_terrain", {})
    lod_from_meta  = stable_terrain.get("lod_95_m", 0)
    if lod_from_meta > 0:
        threshold = lod_from_meta
        log(f"Using statistical LoD (95% confidence): ±{threshold:.2f} m")
    else:
        threshold = NOISE_THRESHOLD_M
        log(f"No LoD in metadata! falling back to fixed threshold of ±{threshold} m", level="WARN")

    log(f"Site:           {target_site_name}")
    log(f"DoD raster:     {dod_path}")
    log(f"Threshold:      ±{threshold:.2f} m")
    log(f"Min zone area:  {MIN_ZONE_AREA_M2:,.0f} m² ({MIN_ZONE_AREA_M2/10000:.1f} ha)")
    log(f"Opening iters:  {OPENING_ITERATIONS}")

    try:
        gdf, bins = vectorize_dod(
            dod_path      = dod_path,
            threshold     = threshold,
            pixel_area_m2 = pixel_area_m2,
            min_area_m2   = MIN_ZONE_AREA_M2,
        )
    except Exception as e:
        fail(
            f"Vectorization failed ({e.__class__.__name__}: {e})",
            hint="Inspect the DoD raster; this usually means a corrupted classification.",
        )

    is_empty = gdf.empty

    geojson_path = os.path.join(output_dir, f"{safe_name}_DoD_Zones.geojson")
    if is_empty:
        with open(geojson_path, "w") as f:
            json.dump({"type": "FeatureCollection", "features": []}, f)
        log(f"Empty GeoJSON saved to {geojson_path} (no change zones detected).")
    else:
        import pandas as pd
        for col in gdf.columns:
            if col == gdf.geometry.name:
                continue
            if isinstance(gdf[col].dtype, pd.StringDtype):
                gdf[col] = gdf[col].astype(object)
        try:
            gdf.to_file(geojson_path, driver="GeoJSON")
        except Exception as e:
            fail(
                f"GeoJSON write failed ({e.__class__.__name__}: {e})",
                hint="The output folder may be read-only or out of disk space.",
            )
        log(f"GeoJSON saved to {geojson_path}")

    classified_tif = os.path.join(output_dir, f"{safe_name}_DoD_Classified.tif")
    try:
        export_classified_tif(dod_path, classified_tif, threshold)
    except Exception as e:
        fail(
            f"Classified GeoTIFF write failed ({e.__class__.__name__}: {e})",
            hint="Check disk space and write permissions on the output folder.",
        )
    log(f"Classified GeoTIFF saved to {classified_tif}")

    zones_png = os.path.join(output_dir, f"{safe_name}_DoD_Zones.png")
    log("Generating zones map…")
    try:
        create_zones_map(dod_path, bins, zones_png, threshold)
    except Exception as e:
        fail(
            f"Zones map render failed ({e.__class__.__name__}: {e})",
            hint="The GeoJSON / GeoTIFF were written; only the PNG is missing.",
        )
    log(f"Zones map saved to {zones_png}")

    log("-" * 55)
    log(f"Vectorization summary for {target_site_name}")
    for _, _, _, lbl, _ in bins:
        subset = gdf[gdf.change_class == lbl]
        if not subset.empty:
            log(f"  {lbl:<35} {len(subset):>4} zones  {subset.area_ha.sum():>8.2f} ha")
    total_ha = float(gdf.area_ha.sum()) if not is_empty else 0.0
    log(f"  TOTAL:                               {len(gdf):>4} zones  {total_ha:>8.2f} ha")

    if is_empty:
        n_gain = n_loss = 0
        gain_ha = loss_ha = 0.0
        log("No active mining detected! zero zones above LoD threshold.")
    else:
        n_gain  = int((gdf.zone_type == "gain").sum())
        n_loss  = int((gdf.zone_type == "loss").sum())
        gain_ha = float(gdf.loc[gdf.zone_type == "gain", "area_ha"].sum())
        loss_ha = float(gdf.loc[gdf.zone_type == "loss", "area_ha"].sum())
        log(f"  Gain zones: {n_gain} ({gain_ha:.2f} ha)")
        log(f"  Loss zones: {n_loss} ({loss_ha:.2f} ha)")

    meta["vectorization"] = {
        "geojson_path"     : geojson_path,
        "classified_tif"   : classified_tif,
        "zones_map_path"   : zones_png,
        "threshold_m"      : round(float(threshold), 4),
        "threshold_source" : "statistical_lod_95" if lod_from_meta > 0 else "fixed",
        "min_zone_area_m2" : MIN_ZONE_AREA_M2,
        "opening_iters"    : OPENING_ITERATIONS,
        "n_zones"          : len(gdf),
        "n_gain_zones"     : n_gain,
        "n_loss_zones"     : n_loss,
        "gain_area_ha"     : round(float(gain_ha), 4),
        "loss_area_ha"     : round(float(loss_ha), 4),
    }
    with open(metadata_path, 'w') as f:
        json.dump(meta, f, indent=2)

    log(f"Metadata updated at {metadata_path}")
    log("Next step: multi-period DoD (optional) + site_report")


if __name__ == "__main__":
    main()

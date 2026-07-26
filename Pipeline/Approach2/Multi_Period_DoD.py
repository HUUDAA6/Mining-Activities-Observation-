import os
import sys
import json
import urllib.error
import urllib.request
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm, Normalize
import matplotlib.patches as mpatches


STEP = "[Multi_Period_DoD]"
AW3D30_API_KEY = os.environ.get("AW3D30_API_KEY", "")


def log(msg, level="INFO"):
    print(f"{STEP} {level}: {msg}", flush=True)


def fail(reason, hint=None, code=1):
    log(reason, level="ERROR")
    if hint:
        log(f"Suggested fix: {hint}", level="HINT")
    sys.exit(code)

def fetch_aw3d30(bounds, output_path):
    """
    Download ALOS World 3D 30m DEM from OpenTopography REST API.
    bounds = (min_lon, min_lat, max_lon, max_lat)
    """
    min_lon, min_lat, max_lon, max_lat = bounds
    url = (
        f"https://portal.opentopography.org/API/globaldem?"
        f"demtype=AW3D30"
        f"&south={min_lat}&north={max_lat}"
        f"&west={min_lon}&east={max_lon}"
        f"&outputFormat=GTiff"
        f"&API_Key={AW3D30_API_KEY}"
    )
    log("Fetching AW3D30 from OpenTopography…")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(output_path, 'wb') as out:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                out.write(chunk)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"OpenTopography rejected the AW3D30 request (HTTP {e.code}: {e.reason}). "
            "If this happens for every bbox the API key may be revoked or rate-limited."
        )
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Network error reaching OpenTopography: {e.reason}. "
            "Check the worker has internet access."
        )
    log(f"AW3D30 saved to {output_path}")


def align_raster_to_reference(src_path, ref_path, out_path):
    """
    Reprojects src_path to match the grid of ref_path
    (same CRS, resolution, bounds, shape).
    """
    with rasterio.open(ref_path) as ref:
        ref_meta = ref.meta.copy()
        ref_transform = ref.transform
        ref_crs = ref.crs
        ref_shape = (ref.height, ref.width)

    with rasterio.open(src_path) as src:
        src_data = src.read(1).astype(np.float32)
        src_transform = src.transform
        src_crs = src.crs

    aligned = np.empty(ref_shape, dtype=np.float32)
    reproject(
        source=src_data,
        destination=aligned,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=ref_transform,
        dst_crs=ref_crs,
        resampling=Resampling.bilinear,
    )

    out_meta = ref_meta.copy()
    out_meta.update(dtype=rasterio.float32, nodata=np.nan, count=1)
    with rasterio.open(out_path, 'w', **out_meta) as dst:
        dst.write(aligned, 1)

    return out_path


# ── DoD computation ──────────────────────────────────────────────────────────

def compute_dod(modern_path, baseline_path, output_path):
    """Compute DoD = modern − baseline. Returns (dod_path, stats_dict)."""
    with rasterio.open(modern_path) as mod_src, \
         rasterio.open(baseline_path) as bas_src:

        mod_data = mod_src.read(1).astype(np.float32)
        bas_data = bas_src.read(1).astype(np.float32)

        mod_nodata = mod_src.nodata
        bas_nodata = bas_src.nodata

        valid = np.ones(mod_data.shape, dtype=bool)
        if mod_nodata is not None:
            valid &= (mod_data != mod_nodata)
        valid &= (mod_data > -9000)
        if bas_nodata is not None:
            valid &= (bas_data != bas_nodata)
        valid &= (bas_data > -9000)

        dod = np.full(mod_data.shape, np.nan, dtype=np.float32)
        dod[valid] = mod_data[valid] - bas_data[valid]

        # Pixel area
        res_x = abs(mod_src.transform.a)
        res_y = abs(mod_src.transform.e)
        centre_lat = (mod_src.bounds.top + mod_src.bounds.bottom) / 2.0
        pixel_area = (res_x * 111320 * np.cos(np.radians(centre_lat))) * (res_y * 111320)

        valid_dod = dod[valid]
        n_valid = int(valid.sum())
        vol_gain = float(valid_dod[valid_dod > 0].sum()) * pixel_area
        vol_loss = float(valid_dod[valid_dod < 0].sum()) * pixel_area

        stats = {
            "n_valid_pixels": n_valid,
            "pixel_area_m2": round(pixel_area, 4),
            "mean_change_m": round(float(np.mean(valid_dod)), 4),
            "std_change_m": round(float(np.std(valid_dod)), 4),
            "min_change_m": round(float(np.min(valid_dod)), 4),
            "max_change_m": round(float(np.max(valid_dod)), 4),
            "vol_gain_m3": round(vol_gain, 2),
            "vol_loss_m3": round(vol_loss, 2),
            "net_vol_m3": round(vol_gain + vol_loss, 2),
        }

        out_meta = mod_src.meta.copy()
        out_meta.update(dtype=rasterio.float32, nodata=np.nan)
        with rasterio.open(output_path, 'w', **out_meta) as dst:
            dst.write(dod, 1)

    return output_path, stats


# ── Visualization ────────────────────────────────────────────────────────────

def _dod_norm(arr):
    real_min = float(np.nanmin(arr))
    real_max = float(np.nanmax(arr))
    if real_min < 0 < real_max:
        return TwoSlopeNorm(vmin=real_min, vcenter=0, vmax=real_max)
    return Normalize(vmin=real_min, vmax=real_max)


def create_multiperiod_map(dod_paths, labels, output_png, site_name):
    """
    Three-panel side-by-side DoD comparison.
    dod_paths: list of 3 GeoTIFF paths
    labels:    list of 3 period labels
    """
    fig, axes = plt.subplots(1, 3, figsize=(20, 7))
    cmap = plt.get_cmap('RdBu')

    legend = [
        mpatches.Patch(color='#2166AC', label='Gain (+)'),
        mpatches.Patch(color='#B2182B', label='Loss (−)'),
    ]

    for ax, path, label in zip(axes, dod_paths, labels):
        with rasterio.open(path) as src:
            dod = src.read(1).astype(np.float32)
            bounds = src.bounds

        valid = np.isfinite(dod) & (dod > -9000)
        masked = np.where(valid, dod, np.nan)
        norm = _dod_norm(np.ma.masked_invalid(masked))

        extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
        im = ax.imshow(masked, cmap=cmap, norm=norm, extent=extent,
                       origin='upper', interpolation='bilinear')

        cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, shrink=0.8)
        cbar.set_label('Elev. Change (m)', fontsize=8)

        valid_vals = dod[valid]
        mean_c = float(np.mean(valid_vals))
        ax.text(0.03, 0.97, f"Mean: {mean_c:+.2f} m",
                transform=ax.transAxes, fontsize=7, va='top',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.85))

        ax.set_title(label, fontweight='bold', fontsize=11)
        ax.axis('off')

    axes[0].legend(handles=legend, loc='lower left', fontsize=7, framealpha=0.88)

    fig.suptitle(f'{site_name} — Multi-Period Elevation Change (Bbox)',
                 fontweight='bold', fontsize=14, y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(output_png, dpi=250, bbox_inches='tight')
    plt.close(fig)
    print(f"  Multi-period map saved: {output_png}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    log("Running multi-period DoD comparison")

    if len(sys.argv) < 3:
        fail(
            "Missing arguments. Expected: python Multi_Period_DoD.py <site_name> <config_path>",
            hint="The DAG should pass these automatically; check resolve_site output.",
        )

    target_site_name = sys.argv[1].strip()
    config_file_path = sys.argv[2].strip()

    try:
        with open(config_file_path, 'r') as f:
            config_data = json.load(f)
    except FileNotFoundError:
        fail(
            f"Per-run config not found at {config_file_path}",
            hint="resolve_site did not write the per-run JSON, or cleanup ran early.",
        )
    except json.JSONDecodeError as e:
        fail(f"Per-run config is not valid JSON: {e}")

    site_config = next(
        (s for s in config_data if s["name"].strip().lower() == target_site_name.lower()),
        None,
    )
    if not site_config:
        fail(
            f"Site '{target_site_name}' not present in config.",
            hint="resolve_site is producing a different 'name' than what the DAG passes here.",
        )

    exact_name       = site_config["name"]
    safe_folder_name = exact_name.strip().replace(" ", "_")
    output_dir       = os.path.join("DEM_Downloads", safe_folder_name)
    meta_path        = os.path.join(output_dir, "fetch_metadata.json")

    if not os.path.exists(meta_path):
        fail(
            f"fetch_metadata.json missing at {meta_path}",
            hint="Calculate_DoD must have failed or been skipped. Re-run it first.",
        )

    with open(meta_path) as f:
        meta = json.load(f)

    cop_path  = meta.get("copernicus")
    nasa_path = meta.get("aligned_nasadem")

    if not cop_path or not nasa_path:
        log(f"Missing Copernicus or aligned NASADEM for {exact_name}; skipping multi-period.", level="WARN")
        sys.exit(0)
    if not os.path.exists(cop_path) or not os.path.exists(nasa_path):
        log(f"Required DEM files not on disk for {exact_name}; skipping multi-period.", level="WARN")
        sys.exit(0)

    with rasterio.open(cop_path) as src:
        b = src.bounds
    bounds = (b.left, b.bottom, b.right, b.top)

    pfx = f"{safe_folder_name}_Bbox"
    aw3d_raw  = os.path.join(output_dir, "AW3D30_Raw.tif")
    aw3d_path = os.path.join(output_dir, "AW3D30_Aligned.tif")

    if not os.path.exists(aw3d_path):
        if not os.path.exists(aw3d_raw):
            try:
                fetch_aw3d30(bounds, aw3d_raw)
            except Exception as e:
                log(
                    f"AW3D30 fetch failed ({e.__class__.__name__}: {e}); "
                    "multi-period comparison will be skipped.",
                    level="WARN",
                )
                sys.exit(0)
        log("Aligning AW3D30 to Copernicus grid…")
        try:
            align_raster_to_reference(aw3d_raw, cop_path, aw3d_path)
        except Exception as e:
            fail(
                f"AW3D30 alignment failed ({e.__class__.__name__}: {e})",
                hint="Open AW3D30_Raw.tif in QGIS; bad georeferencing is the usual cause.",
            )
        log(f"AW3D30 aligned to {aw3d_path}")
    else:
        log("AW3D30 already aligned on disk — reusing cached copy.")

    dod1_path = os.path.join(output_dir, f"{pfx}_DoD_Period1_AW3D30_minus_NASADEM.tif")
    dod2_path = os.path.join(output_dir, f"{pfx}_DoD_Period2_Cop_minus_AW3D30.tif")
    dod3_path = meta.get("dod_path")

    log("Period 1: AW3D30 − NASADEM (2006-2011 vs 2000)")
    try:
        _, stats1 = compute_dod(aw3d_path, nasa_path, dod1_path)
    except Exception as e:
        fail(f"Period 1 DoD computation failed ({e.__class__.__name__}: {e})")
    log(f"  mean {stats1['mean_change_m']:+.3f} m | net vol {stats1['net_vol_m3']:+,.0f} m³")

    log("Period 2: Copernicus − AW3D30 (2011-2015 vs 2006-2011)")
    try:
        _, stats2 = compute_dod(cop_path, aw3d_path, dod2_path)
    except Exception as e:
        fail(f"Period 2 DoD computation failed ({e.__class__.__name__}: {e})")
    log(f"  mean {stats2['mean_change_m']:+.3f} m | net vol {stats2['net_vol_m3']:+,.0f} m³")

    map_png = os.path.join(output_dir, f"{pfx}_MultiPeriod_DoD.png")
    log("Generating three-panel comparison map…")

    if dod3_path and os.path.exists(dod3_path):
        create_multiperiod_map(
            [dod1_path, dod2_path, dod3_path],
            [
                "Period 1: 2000 → 2006-11\n(AW3D30 − NASADEM)",
                "Period 2: 2006-11 → 2011-15\n(Copernicus − AW3D30)",
                "Full Span: 2000 → 2011-15\n(Copernicus − NASADEM)",
            ],
            map_png,
            exact_name,
        )
    else:
        create_multiperiod_map(
            [dod1_path, dod2_path, dod1_path],
            [
                "Period 1: 2000 → 2006-11\n(AW3D30 − NASADEM)",
                "Period 2: 2006-11 → 2011-15\n(Copernicus − AW3D30)",
                "Period 1 (repeated)",
            ],
            map_png,
            exact_name,
        )

    meta["aw3d30_path"] = aw3d_path
    meta["multi_period_dod"] = {
        "period1_path": dod1_path,
        "period1_label": "AW3D30 − NASADEM (2006-2011 vs 2000)",
        "period1_stats": stats1,
        "period2_path": dod2_path,
        "period2_label": "Copernicus − AW3D30 (2011-2015 vs 2006-2011)",
        "period2_stats": stats2,
        "comparison_map": map_png,
    }
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)

    log("-" * 50)
    log(f"Multi-period summary for {exact_name}")
    log(f"  P1 (2000 → 2006-11): mean {stats1['mean_change_m']:+.3f} m | net vol {stats1['net_vol_m3']:+,.0f} m³")
    log(f"  P2 (2006-11 → 2011-15): mean {stats2['mean_change_m']:+.3f} m | net vol {stats2['net_vol_m3']:+,.0f} m³")
    log(f"Metadata updated at {meta_path}")


if __name__ == "__main__":
    main()

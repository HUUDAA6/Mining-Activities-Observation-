import os
import sys
import json
import numpy as np
import rioxarray


STEP = "[Bbox_Aligner]"


def log(msg, level="INFO"):
    print(f"{STEP} {level}: {msg}", flush=True)


def fail(reason, hint=None, code=1):
    log(reason, level="ERROR")
    if hint:
        log(f"Suggested fix: {hint}", level="HINT")
    sys.exit(code)


def main():
    log("Aligning baseline DEMs to the modern reference grid")

    if len(sys.argv) < 3:
        fail(
            "Missing arguments. Expected: python Bbox_Aligner.py <site_name> <config_path>",
            hint="The DAG should pass these automatically; check the resolve_site task output.",
        )

    target_site_name = sys.argv[1].strip().lower()
    config_file_path = sys.argv[2].strip()

    try:
        with open(config_file_path, "r") as f:
            config_data = json.load(f)
    except FileNotFoundError:
        fail(
            f"Per-run config file not found: {config_file_path}",
            hint="Confirm resolve_site wrote the per-run JSON and did not get cleaned up early.",
        )
    except json.JSONDecodeError as e:
        fail(
            f"Per-run config at {config_file_path} is not valid JSON: {e}",
            hint="Open the file and verify it was not truncated mid-write.",
        )

    site_config = next(
        (s for s in config_data if s.get("name", "").strip().lower() == target_site_name),
        None,
    )
    if not site_config:
        names = [s.get("name") for s in config_data]
        fail(
            f"Site '{target_site_name}' not present in config. Available: {names}",
            hint="Make sure the DAG's resolve_site task is producing a matching 'name'.",
        )

    safe_folder_name = site_config["name"].strip().replace(" ", "_")
    output_dir   = os.path.join("DEM_Downloads", safe_folder_name)
    metadata_path = os.path.join(output_dir, "fetch_metadata.json")

    if not os.path.exists(metadata_path):
        fail(
            f"fetch_metadata.json missing at {metadata_path}",
            hint="The Bbox_Fetcher task must have failed or been skipped. Re-run fetch_bbox first.",
        )

    with open(metadata_path) as f:
        meta = json.load(f)

    bounds            = tuple(meta.get("bbox", ()))
    copernicus_path   = meta.get("copernicus")
    nasadem_raw_path  = meta.get("nasadem")

    if not bounds or len(bounds) != 4:
        fail(
            f"fetch_metadata.json has no usable bbox ({bounds!r})",
            hint="Inspect the fetcher's output : bbox should be [minLon, minLat, maxLon, maxLat].",
        )
    if not copernicus_path or not nasadem_raw_path:
        fail(
            "Cannot align: metadata is missing 'copernicus' or 'nasadem' file path.",
            hint="Check the Bbox_Fetcher logs for download warnings.",
        )

    missing = [p for p in (copernicus_path, nasadem_raw_path) if not os.path.exists(p)]
    if missing:
        fail(
            f"Source rasters not found on disk: {missing}",
            hint=(
                "Bbox_Fetcher reported success but the files are gone. "
                "If the folder was wiped between tasks, re-run from fetch_bbox."
            ),
        )

    log(f"Site:       {site_config['name']}")
    log(f"BBox:       {bounds}")
    log(f"Reference:  {copernicus_path} (Copernicus GLO-30)")
    log(f"Baseline:   {nasadem_raw_path} (NASADEM raw)")

    log("Reprojecting NASADEM onto the Copernicus grid (this can take 30s for small bboxes)…")
    nasadem_aligned_path = os.path.join(output_dir, "Baseline_NASADEM_Aligned.tif")

    try:
        with rioxarray.open_rasterio(nasadem_raw_path) as _raw:
            dem_before = _raw.squeeze()
        if dem_before.rio.crs is None:
            dem_before = dem_before.rio.write_crs("EPSG:4326")

        with rioxarray.open_rasterio(copernicus_path) as _ref:
            dem_after_ref = _ref.squeeze()
            if dem_after_ref.rio.crs is None:
                dem_after_ref = dem_after_ref.rio.write_crs("EPSG:4326")
            dem_aligned = dem_before.rio.reproject_match(dem_after_ref)

        os.makedirs(os.path.dirname(nasadem_aligned_path), exist_ok=True)
        for sidecar in (nasadem_aligned_path, nasadem_aligned_path + ".aux.xml"):
            if os.path.exists(sidecar):
                os.remove(sidecar)
        dem_aligned.rio.to_raster(nasadem_aligned_path)

    except FileNotFoundError as e:
        fail(
            f"A source raster vanished mid-reprojection: {e}",
            hint="Another task likely cleaned up the folder. Re-run from fetch_bbox.",
        )
    except MemoryError:
        fail(
            "Out of memory while reprojecting NASADEM.",
            hint="Reduce the bbox area or give the Airflow worker more RAM.",
        )
    except Exception as e:
        fail(
            f"Reprojection raised {e.__class__.__name__}: {e}",
            hint="Open the source NASADEM in QGIS — bad CRS / no-data fill is the usual cause.",
        )

    aligned_vals = dem_aligned.values
    finite = np.isfinite(aligned_vals) & (aligned_vals > -9000)
    nd = dem_aligned.rio.nodata
    if nd is not None:
        finite &= (aligned_vals != nd)
    valid_pct = float(finite.mean() * 100)

    if valid_pct < 1.0:
        fail(
            (
                f"Aligned NASADEM is empty ({valid_pct:.2f}% valid pixels). "
                f"Source CRS={dem_before.rio.crs}, source bounds={dem_before.rio.bounds()}, "
                f"reference bounds={dem_after_ref.rio.bounds()}."
            ),
            hint=(
                "The two DEMs do not overlap. Most common cause: a previous run for a different "
                "bbox is cached under the same site folder. Delete DEM_Downloads/"
                f"{safe_folder_name}/ and re-run from fetch_bbox."
            ),
        )
    log(f"Reprojection OK — {valid_pct:.1f}% valid pixels in aligned NASADEM.")

    aw3d30_raw_path     = meta.get("aw3d30_raw")
    aw3d30_aligned_path = None
    if aw3d30_raw_path and os.path.exists(aw3d30_raw_path):
        log("Aligning AW3D30 (multi-period DEM) to the Copernicus grid…")
        try:
            with rioxarray.open_rasterio(aw3d30_raw_path) as _raw:
                aw3d_before = _raw.squeeze()
            if aw3d_before.rio.crs is None:
                aw3d_before = aw3d_before.rio.write_crs("EPSG:4326")
            with rioxarray.open_rasterio(copernicus_path) as _ref:
                ref = _ref.squeeze()
                if ref.rio.crs is None:
                    ref = ref.rio.write_crs("EPSG:4326")
                aw3d_after = aw3d_before.rio.reproject_match(ref)

            aw3d30_aligned_path = os.path.join(output_dir, "AW3D30_Aligned.tif")
            os.makedirs(os.path.dirname(aw3d30_aligned_path), exist_ok=True)
            for sidecar in (aw3d30_aligned_path, aw3d30_aligned_path + ".aux.xml"):
                if os.path.exists(sidecar):
                    os.remove(sidecar)
            aw3d_after.rio.to_raster(aw3d30_aligned_path)
            log(f"AW3D30 aligned: {aw3d30_aligned_path}")
            os.remove(aw3d30_raw_path)
        except Exception as e:
            log(
                f"AW3D30 alignment failed ({e.__class__.__name__}: {e}). "
                "Multi-period DoD will be skipped for this run.",
                level="WARN",
            )
            aw3d30_aligned_path = None
    else:
        log("No AW3D30 raw raster present! multi-period analysis will be skipped.", level="INFO")

    if os.path.exists(nasadem_raw_path):
        os.remove(nasadem_raw_path)
        log("Removed raw NASADEM bbox to save disk space.")

    meta["aligned_nasadem"] = nasadem_aligned_path
    if aw3d30_aligned_path:
        meta["aw3d30_aligned"] = aw3d30_aligned_path
    meta.pop("aw3d30_raw", None)
    meta.pop("nasadem",    None)

    with open(metadata_path, "w") as f:
        json.dump(meta, f, indent=2)

    log("-" * 50)
    log(f"Alignment complete for {site_config['name']}")
    log(f"  Reference (Copernicus): {copernicus_path}")
    log(f"  Aligned NASADEM:        {nasadem_aligned_path}")
    if aw3d30_aligned_path:
        log(f"  Aligned AW3D30:         {aw3d30_aligned_path}")
    log("Next step: report_dems + calculate_dod")


if __name__ == "__main__":
    main()

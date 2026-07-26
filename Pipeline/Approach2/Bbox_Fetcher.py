import os
import sys
import json
import math
import gzip
import shutil
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone

import rasterio
from rasterio.merge import merge
from dem_stitcher import stitch_dem


STEP = "[Bbox_Fetcher]"
AW3D30_API_KEY = os.environ.get("AW3D30_API_KEY", "")


def log(msg, level="INFO"):
    print(f"{STEP} {level}: {msg}", flush=True)


def fail(reason, hint=None, code=1):
    log(reason, level="ERROR")
    if hint:
        log(f"Suggested fix: {hint}", level="HINT")
    sys.exit(code)


def fetch_aw3d30(bounds, output_path):
    min_lon, min_lat, max_lon, max_lat = bounds
    url = (
        f"https://portal.opentopography.org/API/globaldem?"
        f"demtype=AW3D30"
        f"&south={min_lat}&north={max_lat}"
        f"&west={min_lon}&east={max_lon}"
        f"&outputFormat=GTiff"
        f"&API_Key={AW3D30_API_KEY}"
    )
    log("Downloading AW3D30 tile from OpenTopography…")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(output_path, "wb") as out:
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
            f"Network error reaching OpenTopography for AW3D30: {e.reason}. "
            "Check that the worker has internet access and that portal.opentopography.org resolves."
        )
    log(f"AW3D30 saved to {output_path}")


def fetch_srtm_aws_bbox(bounds, out_path):
    minx, miny, maxx, maxy = bounds
    min_lon = int(math.floor(minx))
    max_lon = int(math.floor(maxx))
    min_lat = int(math.floor(miny))
    max_lat = int(math.floor(maxy))

    temp_dir = tempfile.mkdtemp()
    src_files_to_mosaic = []
    failures = []

    for lon in range(min_lon, max_lon + 1):
        for lat in range(min_lat, max_lat + 1):
            ns = "N" if lat >= 0 else "S"
            ew = "E" if lon >= 0 else "W"
            lat_str = f"{ns}{abs(lat):02d}"
            lon_str = f"{ew}{abs(lon):03d}"

            url      = f"https://s3.amazonaws.com/elevation-tiles-prod/skadi/{lat_str}/{lat_str}{lon_str}.hgt.gz"
            gz_path  = os.path.join(temp_dir, f"{lat_str}{lon_str}.hgt.gz")
            hgt_path = os.path.join(temp_dir, f"{lat_str}{lon_str}.hgt")

            log(f"  Downloading SRTM tile {lat_str}{lon_str} from AWS…")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=120) as response, open(gz_path, "wb") as out_file:
                    shutil.copyfileobj(response, out_file)
                with gzip.open(gz_path, "rb") as f_in, open(hgt_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                src_files_to_mosaic.append(hgt_path)
            except urllib.error.HTTPError as e:
                # 403/404 on ocean tiles is normal : log and skip.
                failures.append((f"{lat_str}{lon_str}", f"HTTP {e.code}"))
            except urllib.error.URLError as e:
                failures.append((f"{lat_str}{lon_str}", f"URLError {e.reason}"))
            except OSError as e:
                failures.append((f"{lat_str}{lon_str}", f"OSError {e}"))

    if not src_files_to_mosaic:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError(
            "Zero SRTM tiles downloaded for this bbox. "
            f"AWS Mapzen returned errors for every tile: {failures[:5]}…"
        )
    if failures:
        log(f"  {len(failures)} tile(s) unavailable (likely over water); continuing with {len(src_files_to_mosaic)} tile(s).", level="WARN")

    srcs = [rasterio.open(p) for p in src_files_to_mosaic]
    try:
        mosaic, out_trans = merge(srcs, bounds=(minx, miny, maxx, maxy))
        out_meta = srcs[0].meta.copy()
        out_meta.update({
            "driver":    "GTiff",
            "height":    mosaic.shape[1],
            "width":     mosaic.shape[2],
            "transform": out_trans,
            "crs":       rasterio.crs.CRS.from_epsg(4326),
        })
        with rasterio.open(out_path, "w", **out_meta) as dest:
            dest.write(mosaic)
    except Exception as e:
        raise RuntimeError(
            f"Failed to merge SRTM tiles into a mosaic: {e.__class__.__name__}: {e}"
        )
    finally:
        for src in srcs:
            src.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    log("Starting DEM fetch for one bbox")

    if len(sys.argv) < 3:
        fail(
            "Missing arguments! Expected: python Bbox_Fetcher.py <site_name> <config_path>",
            hint="The DAG should pass these automatically : check the resolve_site task output.",
        )

    target_site_name = sys.argv[1].strip()
    config_file_path = sys.argv[2].strip()

    try:
        with open(config_file_path, "r") as f:
            config_data = json.load(f)
    except FileNotFoundError:
        fail(
            f"Per-run config file not found: {config_file_path}",
            hint="Confirm resolve_site wrote the per-run JSON and was not cleaned up early.",
        )
    except json.JSONDecodeError as e:
        fail(
            f"Per-run config is not valid JSON: {e}",
            hint="Re-trigger the DAG; the resolve_site task may have been interrupted mid-write.",
        )

    site_config = next(
        (s for s in config_data if s.get("name", "").lower() == target_site_name.lower()),
        None,
    )
    if not site_config:
        available = [s.get("name") for s in config_data]
        fail(
            f"Site '{target_site_name}' not present in config. Available: {available}",
            hint="resolve_site is producing a different 'name' than what the DAG passes here.",
        )

    bounds = site_config["bounds"]
    log(f"Site:   {site_config['name']}")
    log(f"BBox:   {bounds}")

    safe_folder_name = site_config["name"].replace(" ", "_")
    output_dir = os.path.join("DEM_Downloads", safe_folder_name)

    existing_meta_path = os.path.join(output_dir, "fetch_metadata.json")
    if os.path.exists(existing_meta_path):
        stale = False
        try:
            with open(existing_meta_path, "r") as f:
                existing = json.load(f)
            prev_bbox = existing.get("bbox")
            if (
                not prev_bbox
                or len(prev_bbox) != 4
                or any(abs(float(a) - float(b)) > 1e-6 for a, b in zip(prev_bbox, bounds))
            ):
                stale = True
                log(
                    f"Cached folder bbox {prev_bbox} does not match requested {list(bounds)}. "
                    f"Wiping {output_dir} to avoid stale-grid mismatch in the aligner.",
                    level="WARN",
                )
        except Exception as e:
            stale = True
            log(f"Could not read cached metadata ({e}); wiping {output_dir} to be safe.", level="WARN")
        if stale:
            shutil.rmtree(output_dir, ignore_errors=True)

    os.makedirs(output_dir, exist_ok=True)
    cop_path  = os.path.join(output_dir, "Modern_Copernicus_GLO30.tif")
    nasa_path = os.path.join(output_dir, "Baseline_NASADEM_Cropped.tif")

    if not os.path.exists(cop_path):
        log("Downloading Copernicus GLO-30 (modern reference DEM)…")
        try:
            cop_array, cop_profile = stitch_dem(
                bounds,
                dem_name="glo_30",
                dst_ellipsoidal_height=False,
                dst_area_or_point="Area",
            )
            with rasterio.open(cop_path, "w", **cop_profile) as ds:
                ds.write(cop_array, 1)
        except Exception as e:
            fail(
                f"Copernicus download failed ({e.__class__.__name__}: {e})",
                hint=(
                    "dem_stitcher pulls from AWS Open Data. Check that the worker has internet "
                    "and that the bbox is in a covered region (Copernicus GLO-30 is global)."
                ),
            )
        log("Copernicus downloaded successfully.")
    else:
        log("Copernicus already on disk, reusing cached copy.")

    if not os.path.exists(nasa_path):
        log("Downloading NASADEM / SRTM baseline tiles…")
        try:
            fetch_srtm_aws_bbox(bounds, nasa_path)
        except Exception as e:
            fail(
                f"NASADEM download failed: {e}",
                hint=(
                    "If every tile returned 403/404, the bbox is fully over ocean. "
                    "Otherwise the worker may have lost internet access to s3.amazonaws.com."
                ),
            )
        log("NASADEM downloaded successfully.")
    else:
        log("NASADEM already on disk, reusing cached copy.")

    download_aw3d30  = site_config.get("dem_options", {}).get("aw3d30", True)
    aw3d30_raw_path  = None
    if download_aw3d30:
        aw3d30_raw_path = os.path.join(output_dir, "AW3D30_Raw.tif")
        if not os.path.exists(aw3d30_raw_path):
            log("Downloading AW3D30 (multi-period intermediate DEM)…")
            try:
                fetch_aw3d30(bounds, aw3d30_raw_path)
            except Exception as e:
                log(
                    f"AW3D30 download failed ({e.__class__.__name__}: {e}). "
                    "Multi-period analysis will be skipped for this run.",
                    level="WARN",
                )
                aw3d30_raw_path = None
        else:
            log("AW3D30 already on disk — reusing cached copy.")
    else:
        log("AW3D30 disabled in site config — skipping multi-period source.")

    # period_start / period_end are constant
    metadata = {
        "site_name":    site_config["name"],
        "client":       site_config.get("client"),
        "Bbox_id":      site_config.get("Bbox_id"),
        "Area_Name":    site_config.get("Area_Name"),
        "run_at":       datetime.now(timezone.utc).date().isoformat(),
        "period_start": "2000-02-11",
        "period_end":   "2015-12-31",
        "bbox":         bounds,
        "copernicus":   cop_path,
        "nasadem":      nasa_path,
        "status":       "success",
    }
    if aw3d30_raw_path and os.path.exists(aw3d30_raw_path):
        metadata["aw3d30_raw"] = aw3d30_raw_path

    meta_path = os.path.join(output_dir, "fetch_metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=4)

    log("-" * 50)
    log(f"Fetch complete for {site_config['name']}")
    log(f"  Copernicus: {cop_path}")
    log(f"  NASADEM:    {nasa_path}")
    if aw3d30_raw_path:
        log(f"  AW3D30:     {aw3d30_raw_path}")
    log(f"Metadata written to {meta_path}")


if __name__ == "__main__":
    main()

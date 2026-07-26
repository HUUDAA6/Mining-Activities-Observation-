import json
import os
import re
import subprocess
import traceback
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from airflow import DAG
from airflow.exceptions import AirflowFailException
from airflow.providers.standard.operators.python import PythonOperator, ShortCircuitOperator
from airflow.providers.microsoft.azure.hooks.wasb import WasbHook


# DYNAMIC PATHS & CONFIGURATION
PROJECT_DIR = os.getenv("MINING_PROJECT_DIR_A2", "/opt/airflow/Approach2")
PYTHON_BIN  = os.getenv("MINING_PYTHON_BIN", "/home/airflow/.local/bin/python")

AZURE_CONTAINER = os.getenv("MINING_AZURE_CONTAINER", "mining")
AZURE_CONN_ID   = os.getenv("MINING_AZURE_CONN_ID", "azure_data_lake_conn")

DEFAULT_CLIENT = os.getenv("MINING_DEFAULT_CLIENT", "houda")

NOMINATIM_URL     = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_TIMEOUT = 10


def _sanitize_name(raw):
    if not raw:
        return "site"
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9\-]+", "_", ascii_only).strip("_")
    return safe or "site"


def sanitize_client(client):
    return str(client).strip().lower().replace(" ", "_").replace("/", "_").replace("`", "")


def _bbox_id(bounds):
    min_lon, min_lat, max_lon, max_lat = [round(c, 4) for c in bounds]
    return f"{min_lon}_{min_lat}_{max_lon}_{max_lat}"


def _coord_fallback_name(bounds):
    min_lon, min_lat, max_lon, max_lat = [round(c, 1) for c in bounds]
    cy = (min_lat + max_lat) / 2
    cx = (min_lon + max_lon) / 2
    return f"site_{round(cy, 2)}_{round(cx, 2)}".replace(".", "_").replace("-", "m")


def _geocode_bbox(bounds):
    min_lon, min_lat, max_lon, max_lat = bounds
    lat = (min_lat + max_lat) / 2
    lon = (min_lon + max_lon) / 2
    params = urllib.parse.urlencode({
        "lat": f"{lat}",
        "lon": f"{lon}",
        "format": "json",
        "zoom": "10",
        "addressdetails": "1",
        "accept-language": "en",
    })
    url = f"{NOMINATIM_URL}?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "mining-pipeline-dag/1.0",
            "Accept-Language": "en",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=NOMINATIM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        addr = data.get("address", {}) or {}
        raw = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("municipality")
            or addr.get("county")
            or addr.get("state_district")
            or addr.get("state")
            or addr.get("region")
            or addr.get("province")
            or addr.get("country")
            or data.get("name")
            or data.get("display_name", "").split(",")[0]
        )
        name = _sanitize_name(raw)
        if name and name != "site":
            return name, True
    except Exception as e:
        print(f"Nominatim lookup failed for {_bbox_id(bounds)}: {e}")
    return _coord_fallback_name(bounds), False


def resolve_site(**context):
    """Parse dag_run.conf, derive site identity, write a per-run config file."""
    dag_run = context["dag_run"]
    conf    = dag_run.conf or {}

    bbox = conf.get("bbox") or conf.get("bounds")
    if not bbox or len(bbox) != 4:
        raise AirflowFailException(
            f"Trigger conf is missing a valid 4-element 'bbox'. Got: {conf!r}"
        )
    try:
        bbox = [float(c) for c in bbox]
    except (TypeError, ValueError) as e:
        raise AirflowFailException(f"bbox values are not numeric: {bbox!r} ({e})")

    min_lon, min_lat, max_lon, max_lat = bbox
    if not (min_lon < max_lon and min_lat < max_lat):
        raise AirflowFailException(
            f"bbox is empty or inverted (need min<max): {bbox}"
        )

    client  = sanitize_client(conf.get("client", DEFAULT_CLIENT))
    bbox_id = _bbox_id(bbox)

    override = conf.get("area_name")
    if override:
        name = _sanitize_name(override)
        geocoded = False
    else:
        name, geocoded = _geocode_bbox(bbox)

    azure_prefix = f"{client}/{name}__BBOX_{bbox_id}"

    site_entry = {
        "client":    client,
        "bounds":    bbox,
        "name":      name,
        "Bbox_id":   bbox_id,
        "Area_Name": name,
        "geocoded":  geocoded,
    }

    # Write a unique config file per dag_run so concurrent triggers never stomp on each other.
    safe_run_id = re.sub(r"[^A-Za-z0-9_\-]", "_", dag_run.run_id)
    config_path = os.path.join(PROJECT_DIR, f"_run_{safe_run_id}.json")
    with open(config_path, "w") as f:
        json.dump([site_entry], f, indent=2)

    print(f"resolved site: name={name} bbox_id={bbox_id} prefix={azure_prefix}")
    print(f"per-run config written to {config_path}")

    return {
        "site_name":    name,
        "bbox_id":      bbox_id,
        "azure_prefix": azure_prefix,
        "config_path":  config_path,
        "client":       client,
    }


def _site_info(context):
    info = context["ti"].xcom_pull(task_ids="resolve_site")
    if not info:
        raise AirflowFailException("resolve_site did not push site info to XCom")
    return info


def run_python_script(script_name, **context):
    info        = _site_info(context)
    script_path = os.path.join(PROJECT_DIR, script_name)
    site_name   = info["site_name"]
    config_path = info["config_path"]

    print(f"Running {script_name} for site={site_name}")
    print(f"Per-run config: {config_path}")

    try:
        result = subprocess.run(
            [PYTHON_BIN, script_path, site_name, config_path],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
        )
        print("--- SCRIPT OUTPUT ---")
        print(result.stdout)
        if result.returncode != 0:
            print("--- SCRIPT ERRORS ---")
            print(result.stderr)
            raise Exception(
                f"{script_name} returned exit code {result.returncode}"
            )
    except Exception as e:
        print("--- SCRIPT ERRORS ---")
        print(traceback.format_exc())
        raise Exception(f"{script_name} raised: {e}")


def bbox_is_new(**context):
    info     = _site_info(context)
    hook     = WasbHook(wasb_conn_id=AZURE_CONN_ID)
    prefix   = f"{info['azure_prefix']}/"
    blobs    = hook.get_blobs_list(container_name=AZURE_CONTAINER, prefix=prefix)
    if blobs:
        print(f"SKIPPED: {AZURE_CONTAINER}/{prefix} already has {len(blobs)} blob(s).")
        print("  Delete the folder in Azure to re-run.")
        return False
    print(f"OK: {AZURE_CONTAINER}/{prefix} is empty. Pipeline will run.")
    return True


def upload_artifact(filename, **context):
    info       = _site_info(context)
    site_name  = info["site_name"]
    rendered   = filename.format(name=site_name)
    file_path  = os.path.join(PROJECT_DIR, "DEM_Downloads", site_name, rendered)
    blob_name  = f"{info['azure_prefix']}/{rendered}"

    step = f"[upload_to_azure:{rendered}]"

    def log(msg, level="INFO"):
        print(f"{step} {level}: {msg}", flush=True)

    try:
        hook = WasbHook(wasb_conn_id=AZURE_CONN_ID)
    except Exception as e:
        raise AirflowFailException(
            f"{step} ERROR: Cannot connect to Azure Blob Storage "
            f"({e.__class__.__name__}: {e}). "
            f"HINT: verify the Airflow connection '{AZURE_CONN_ID}' is configured with a valid "
            f"connection string in Admin → Connections."
        )

    try:
        if hook.check_for_blob(AZURE_CONTAINER, blob_name):
            log(f"Blob already exists in Azure ({AZURE_CONTAINER}/{blob_name}); skipping re-upload.")
            return
    except Exception as e:
        raise AirflowFailException(
            f"{step} ERROR: Azure rejected the existence check for {AZURE_CONTAINER}/{blob_name} "
            f"({e.__class__.__name__}: {e}). "
            f"HINT: confirm the container '{AZURE_CONTAINER}' exists and the Airflow connection "
            f"has list+read permissions on it."
        )

    if not os.path.exists(file_path):
        raise AirflowFailException(
            f"{step} ERROR: Expected artifact not produced by upstream task: {file_path}. "
            f"HINT: the previous task ({rendered}'s source script) probably failed silently — "
            f"open its logs to see why it did not write this file."
        )

    size_bytes = os.path.getsize(file_path)
    log(f"Uploading {file_path} ({size_bytes/1024:.1f} KiB) → {AZURE_CONTAINER}/{blob_name}")
    try:
        hook.load_file(file_path, AZURE_CONTAINER, blob_name)
    except Exception as e:
        raise AirflowFailException(
            f"{step} ERROR: Upload to {AZURE_CONTAINER}/{blob_name} failed mid-stream "
            f"({e.__class__.__name__}: {e}). "
            f"HINT: likely network drop or expired SAS token. Re-running the task should retry."
        )
    log(f"Upload OK ({size_bytes/1024:.1f} KiB).")


def cleanup_run_config(**context):
    import shutil

    info = context["ti"].xcom_pull(task_ids="resolve_site")
    if not info:
        print("no site info in XCom — nothing to clean")
        return

    # --- 1. Remove per-run config file ---
    config_path = info.get("config_path")
    if config_path:
        try:
            os.remove(config_path)
            print(f"removed config: {config_path}")
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"could not remove config {config_path}: {e}")

    print("cleanup triggered — removing intermediate files")

    # --- 3. Delete DEM_Downloads/{site_name}/ no matter what ---
    site_name = info.get("site_name")
    if not site_name:
        print("no site_name — skipping DEM_Downloads cleanup")
        return

    dem_dir = os.path.join(PROJECT_DIR, "DEM_Downloads", site_name)
    if not os.path.exists(dem_dir):
        print(f"DEM_Downloads dir not found (already clean): {dem_dir}")
        return

    try:
        size_bytes = sum(
            os.path.getsize(os.path.join(root, f))
            for root, _, files in os.walk(dem_dir)
            for f in files
        )
        shutil.rmtree(dem_dir)
        print(f"deleted {dem_dir} — freed {size_bytes / (1024**2):.1f} MB")
    except Exception as e:
        print(f"ERROR: could not delete {dem_dir}: {e}")


default_args = {
    "owner":         "houda",
    "retries":       1,
    "retry_delay":   timedelta(minutes=5),
}

with DAG(
    "Global_mining_pipeline_Approach2",
    default_args=default_args,
    start_date=datetime(2026, 3, 1),
    schedule=None,
    catchup=False,
    description="On-demand BBOX DoD pipeline triggered via dag_run.conf",
    tags=["approach2", "on_demand", "azure"],
    params={
        "client":    DEFAULT_CLIENT,
        "bbox":      [-90.13, 37.95, -90.06, 38.01],
        "area_name": "",
    },
    render_template_as_native_obj=True,
) as dag:

    t_resolve = PythonOperator(
        task_id="resolve_site",
        python_callable=resolve_site,
    )

    t_gate = ShortCircuitOperator(
        task_id="check_azure",
        python_callable=bbox_is_new,
    )

    t_fetch = PythonOperator(
        task_id="fetch_bbox",
        python_callable=run_python_script,
        op_kwargs={"script_name": "Bbox_Fetcher.py"},
    )

    t_align = PythonOperator(
        task_id="align_dems",
        python_callable=run_python_script,
        op_kwargs={"script_name": "Bbox_Aligner.py"},
    )

    t_report_dems = PythonOperator(
        task_id="report_dems",
        python_callable=run_python_script,
        op_kwargs={"script_name": "Report_DEM.py"},
    )

    t_dod = PythonOperator(
        task_id="calculate_dod",
        python_callable=run_python_script,
        op_kwargs={"script_name": "Calculate_DoD.py"},
    )

    t_vec = PythonOperator(
        task_id="vectorization",
        python_callable=run_python_script,
        op_kwargs={"script_name": "Vectorization.py"},
    )

    t_multi = PythonOperator(
        task_id="multi_period",
        python_callable=run_python_script,
        op_kwargs={"script_name": "Multi_Period_DoD.py"},
    )

    t_site_report = PythonOperator(
        task_id="site_report",
        python_callable=run_python_script,
        op_kwargs={"script_name": "Site_Report.py"},
    )

    t_up_site_report = PythonOperator(
        task_id="upload_site_report",
        python_callable=upload_artifact,
        op_kwargs={"filename": "{name}_Site_Report.pdf"},
    )

    t_up_geojson = PythonOperator(
        task_id="upload_geojson",
        python_callable=upload_artifact,
        op_kwargs={"filename": "{name}_DoD_Zones.geojson"},
    )

    t_up_dod_tif = PythonOperator(
        task_id="upload_dod_tif",
        python_callable=upload_artifact,
        op_kwargs={"filename": "DEM_of_Difference.tif"},
    )

    t_up_dem_report = PythonOperator(
        task_id="upload_dem_report",
        python_callable=upload_artifact,
        op_kwargs={"filename": "{name}_DEM_Report.pdf"},
    )

    t_up_metadata = PythonOperator(
        task_id="upload_metadata",
        python_callable=upload_artifact,
        op_kwargs={"filename": "fetch_metadata.json"},
    )

    t_cleanup = PythonOperator(
        task_id="cleanup_config",
        python_callable=cleanup_run_config,
        trigger_rule="all_done",
    )

    uploads = [t_up_site_report, t_up_geojson, t_up_dod_tif, t_up_dem_report, t_up_metadata]

    t_resolve >> t_gate >> t_fetch >> t_align
    t_align >> [t_report_dems, t_dod] >> t_vec >> t_multi >> t_site_report
    t_site_report >> uploads
    uploads >> t_cleanup

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator


router = APIRouter(prefix="/api", tags=["pipeline"])


AIRFLOW_BASE_URL   = os.getenv("AIRFLOW_BASE_URL", "http://airflow-webserver:8090").rstrip("/")
AIRFLOW_USERNAME   = os.getenv("AIRFLOW_USERNAME", "admin")
AIRFLOW_PASSWORD   = os.getenv("AIRFLOW_PASSWORD", "admin")
AIRFLOW_DAG_ID     = os.getenv("AIRFLOW_DAG_ID", "Global_mining_pipeline_Approach2")
AIRFLOW_API_PREFIX = os.getenv("AIRFLOW_API_PREFIX", "/api/v2")
SSE_POLL_INTERVAL  = float(os.getenv("AIRFLOW_SSE_POLL_INTERVAL", "2.5"))

# Cloudflare Access service-token credentials.
CF_ACCESS_CLIENT_ID     = (os.getenv("CF_ACCESS_CLIENT_ID")     or "").strip()
CF_ACCESS_CLIENT_SECRET = (os.getenv("CF_ACCESS_CLIENT_SECRET") or "").strip()


def _cf_headers() -> dict[str, str]:
    if CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET:
        return {
            "CF-Access-Client-Id":     CF_ACCESS_CLIENT_ID,
            "CF-Access-Client-Secret": CF_ACCESS_CLIENT_SECRET,
        }
    return {}


# Mirror of the DAG's task graph, in execution order, so the frontend can render
PIPELINE_TASKS = [
    ("resolve_site",       "Resolving site"),
    ("check_azure",        "Checking Azure cache"),
    ("fetch_bbox",         "Fetching DEMs"),
    ("align_dems",         "Aligning DEMs"),
    ("report_dems",        "Building DEM report"),
    ("calculate_dod",      "Calculating DEM of Difference"),
    ("vectorization",      "Vectorizing change zones"),
    ("multi_period",       "Multi-period DoD"),
    ("site_report",        "Generating site report"),
    ("upload_site_report", "Uploading site report"),
    ("upload_geojson",     "Uploading change zones"),
    ("upload_dod_tif",     "Uploading DoD raster"),
    ("upload_dem_report",  "Uploading DEM report"),
    ("upload_metadata",    "Uploading metadata"),
    ("cleanup_config",     "Cleaning up"),
]
TASK_LABELS = dict(PIPELINE_TASKS)
TASK_ORDER  = [tid for tid, _ in PIPELINE_TASKS]

TERMINAL_RUN_STATES  = {"success", "failed"}
COUNT_AS_DONE_STATES = {"success", "skipped"}


class TriggerRequest(BaseModel):
    bbox:      list[float]
    client:    str            = Field(default="Arias", min_length=1, max_length=64)
    area_name: str | None     = Field(default=None, max_length=128)

    @field_validator("bbox")
    @classmethod
    def _validate_bbox(cls, v):
        if len(v) != 4:
            raise ValueError("bbox must have 4 elements: [minLon, minLat, maxLon, maxLat]")
        min_lon, min_lat, max_lon, max_lat = v
        if not (-180 <= min_lon < max_lon <= 180):
            raise ValueError("invalid longitude range")
        if not (-90 <= min_lat < max_lat <= 90):
            raise ValueError("invalid latitude range")
        return v


_token_cache: dict[str, Any] = {"token": None}
_token_lock = asyncio.Lock()


async def _get_token(client: httpx.AsyncClient, force_refresh: bool = False) -> str:
    async with _token_lock:
        if not force_refresh and _token_cache["token"]:
            return _token_cache["token"]
        try:
            r = await client.post(
                f"{AIRFLOW_BASE_URL}/auth/token",
                json={"username": AIRFLOW_USERNAME, "password": AIRFLOW_PASSWORD},
                headers=_cf_headers(),
                timeout=10.0,
            )
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Cannot reach Airflow at {AIRFLOW_BASE_URL}: {e.__class__.__name__}: {e}. "
                ),
            )
        if r.status_code not in (200, 201):
            raise HTTPException(
                status_code=502,
                detail=f"Airflow auth failed ({r.status_code}): {r.text}",
            )
        data = r.json()
        token = data.get("access_token") or data.get("token") or data.get("jwt")
        if not token:
            raise HTTPException(status_code=502, detail=f"Airflow returned no token: {data}")
        _token_cache["token"] = token
        return token


# Cloudflare / cloudflared returns these when the WSL origin is briefly
# unreachable (e.g. CPU-starved by a heavy DAG run). They are transient —
# retry a few times before surfacing the error.
GATEWAY_STATUSES = {502, 503, 504}
_AIRFLOW_RETRIES = 3


async def _airflow(method: str, path: str, **kwargs) -> httpx.Response:
    timeout = kwargs.pop("timeout", 30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        token   = await _get_token(client)
        headers = kwargs.pop("headers", {}) or {}
        headers.update(_cf_headers())
        headers["Authorization"] = f"Bearer {token}"
        url = f"{AIRFLOW_BASE_URL}{path}"
        attempt = 0
        while True:
            try:
                r = await client.request(method, url, headers=headers, **kwargs)
                if r.status_code in (401, 403):
                    token = await _get_token(client, force_refresh=True)
                    headers["Authorization"] = f"Bearer {token}"
                    r = await client.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as e:
                if attempt < _AIRFLOW_RETRIES:
                    attempt += 1
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise HTTPException(
                    status_code=502,
                    detail=f"Airflow request failed ({method} {path}): {e.__class__.__name__}: {e}",
                )
            if r.status_code in GATEWAY_STATUSES and attempt < _AIRFLOW_RETRIES:
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            return r


def _summarize(run_id: str, run_obj: dict, ti_list: list[dict]) -> dict:
    by_id = {t.get("task_id"): t for t in ti_list}
    tasks = []
    for tid in TASK_ORDER:
        ti = by_id.get(tid, {})
        tasks.append({
            "task_id":    tid,
            "label":      TASK_LABELS[tid],
            "state":      ti.get("state"),
            "start_date": ti.get("start_date"),
            "end_date":   ti.get("end_date"),
            "try_number": ti.get("try_number"),
        })

    completed = sum(1 for t in tasks if t["state"] in COUNT_AS_DONE_STATES)
    total     = len(tasks)
    current   = next(
        (t for t in tasks if t["state"] in ("running", "queued", "scheduled", "up_for_retry")),
        None,
    )
    failed = next((t for t in tasks if t["state"] == "failed"), None)

    return {
        "run_id":     run_id,
        "dag_id":     AIRFLOW_DAG_ID,
        "state":      run_obj.get("state"),
        "start_date": run_obj.get("start_date") or run_obj.get("logical_date"),
        "end_date":   run_obj.get("end_date"),
        "progress":   {
            "completed": completed,
            "total":     total,
            "percent":   int(round(completed / total * 100)) if total else 0,
        },
        "current":    current["label"] if current else None,
        "failed":     failed["label"]  if failed  else None,
        "tasks":      tasks,
    }


async def _fetch_run_status(run_id: str) -> dict:
    r_run = await _airflow(
        "GET",
        f"{AIRFLOW_API_PREFIX}/dags/{AIRFLOW_DAG_ID}/dagRuns/{run_id}",
    )
    if r_run.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    if r_run.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Airflow run lookup failed ({r_run.status_code}): {r_run.text}",
        )

    r_ti = await _airflow(
        "GET",
        f"{AIRFLOW_API_PREFIX}/dags/{AIRFLOW_DAG_ID}/dagRuns/{run_id}/taskInstances",
    )
    if r_ti.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Airflow taskInstances lookup failed ({r_ti.status_code}): {r_ti.text}",
        )

    return _summarize(run_id, r_run.json(), r_ti.json().get("task_instances", []))


@router.post("/jobs", status_code=201)
async def create_job(payload: TriggerRequest):
    """Trigger a DAG run for the drawn polygon and return the new run id."""
    conf: dict[str, Any] = {"client": payload.client, "bbox": payload.bbox}
    if payload.area_name:
        conf["area_name"] = payload.area_name

    # Airflow infers a run id when omitted, we supply a stable one so the frontend can address it immediately
    run_id = f"polygon_{uuid.uuid4().hex[:12]}"

    body = {
        "dag_run_id":   run_id,
        "logical_date": datetime.now(timezone.utc).isoformat(),
        "conf":         conf,
        "note":         "Triggered from polygon-draw UI",
    }

    r = await _airflow(
        "POST",
        f"{AIRFLOW_API_PREFIX}/dags/{AIRFLOW_DAG_ID}/dagRuns",
        json=body,
    )
    if r.status_code not in (200, 201):
        raise HTTPException(
            status_code=502,
            detail=f"Airflow trigger failed ({r.status_code}): {r.text}",
        )
    data = r.json()
    return {
        "run_id": data.get("dag_run_id", run_id),
        "dag_id": AIRFLOW_DAG_ID,
        "state":  data.get("state", "queued"),
        "conf":   conf,
    }


@router.get("/jobs")
async def list_jobs(limit: int = 25, offset: int = 0):
    """List recent DAG runs from Airflow, newest first."""
    r = await _airflow(
        "GET",
        f"{AIRFLOW_API_PREFIX}/dags/{AIRFLOW_DAG_ID}/dagRuns",
        params={"limit": limit, "offset": offset, "order_by": "-start_date"},
    )
    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Airflow dagRuns listing failed ({r.status_code}): {r.text[:500]}",
        )
    data  = r.json()
    raw   = data.get("dag_runs", [])
    total = data.get("total_entries", len(raw))

    runs = []
    for run in raw:
        run_id = run.get("dag_run_id", "")
        conf   = run.get("conf") or {}
        runs.append({
            "run_id":     run_id,
            "dag_id":     AIRFLOW_DAG_ID,
            "state":      run.get("state"),
            "start_date": run.get("start_date") or run.get("logical_date"),
            "end_date":   run.get("end_date"),
            "conf":       conf,
            "area_name":  conf.get("area_name") or conf.get("client") or run_id,
            "note":       run.get("note"),
        })

    return {"runs": runs, "total": total}


@router.get("/jobs/{run_id}")
async def get_job(run_id: str):
    return await _fetch_run_status(run_id)


@router.post("/jobs/{run_id}/retry-failed", status_code=202)
async def retry_failed_tasks(run_id: str):
    """Re-run only the tasks currently in failed / up_for_retry state.
    Anything already succeeded stays as-is, so the run resumes from the break."""
    status = await _fetch_run_status(run_id)
    failed_ids = [
        t["task_id"] for t in status["tasks"]
        if t["state"] in ("failed", "up_for_retry", "upstream_failed")
    ]
    if not failed_ids:
        raise HTTPException(
            status_code=409,
            detail="No failed tasks to retry in this run.",
        )
    body = {
        "dag_run_id":            run_id,
        "task_ids":              failed_ids,
        "include_downstream":    True,
        "include_upstream":      False,
        "include_future":        False,
        "include_past":          False,
        "only_failed":           False,
        "reset_dag_runs":        True,
        "dry_run":               False,
    }
    r = await _airflow(
        "POST",
        f"{AIRFLOW_API_PREFIX}/dags/{AIRFLOW_DAG_ID}/clearTaskInstances",
        json=body,
    )
    if r.status_code not in (200, 202):
        raise HTTPException(
            status_code=502,
            detail=f"Airflow clear failed ({r.status_code}): {r.text[:500]}",
        )
    return {
        "run_id":         run_id,
        "cleared_tasks":  failed_ids,
        "state":          "queued",
    }


@router.post("/jobs/{run_id}/resume", status_code=202)
async def resume_run(run_id: str):
    """Resume a stopped or failed run from where it left off. Tasks already in
    success/skipped are left untouched. Every other task is cleared
    and re-scheduled, and the DAG run state is reset back to running."""
    status = await _fetch_run_status(run_id)
    pending_ids = [
        t["task_id"] for t in status["tasks"]
        if t["state"] not in ("success", "skipped")
    ]
    if not pending_ids:
        raise HTTPException(
            status_code=409,
            detail="Nothing to resume : every task already completed.",
        )
    body = {
        "dag_run_id":         run_id,
        "task_ids":           pending_ids,
        "include_downstream": True,
        "include_upstream":   False,
        "include_future":     False,
        "include_past":       False,
        "only_failed":        False,
        "reset_dag_runs":     True,
        "dry_run":            False,
    }
    r = await _airflow(
        "POST",
        f"{AIRFLOW_API_PREFIX}/dags/{AIRFLOW_DAG_ID}/clearTaskInstances",
        json=body,
    )
    if r.status_code not in (200, 202):
        raise HTTPException(
            status_code=502,
            detail=f"Airflow resume failed ({r.status_code}): {r.text[:500]}",
        )
    return {"run_id": run_id, "resumed_tasks": pending_ids, "state": "queued"}


@router.post("/jobs/{run_id}/stop", status_code=202)
async def stop_run(run_id: str):
    """Force the DAG run to terminate by marking it failed. Tasks currently
    running keep their state in Airflow until the scheduler reaps them, but no
    new tasks are scheduled and the SSE stream emits a terminal 'done' event."""
    r = await _airflow(
        "PATCH",
        f"{AIRFLOW_API_PREFIX}/dags/{AIRFLOW_DAG_ID}/dagRuns/{run_id}",
        json={"state": "failed", "note": "Stopped from UI"},
    )
    if r.status_code not in (200, 202):
        raise HTTPException(
            status_code=502,
            detail=f"Airflow stop failed ({r.status_code}): {r.text[:500]}",
        )
    return {"run_id": run_id, "state": "failed"}


@router.post("/jobs/{run_id}/restart", status_code=202)
async def restart_run(run_id: str):
    """Clear every task instance in the run, so it restarts from the beginning."""
    body = {
        "dag_run_id":            run_id,
        "include_downstream":    True,
        "include_upstream":      True,
        "include_future":        False,
        "include_past":          False,
        "only_failed":           False,
        "reset_dag_runs":        True,
        "dry_run":               False,
    }
    r = await _airflow(
        "POST",
        f"{AIRFLOW_API_PREFIX}/dags/{AIRFLOW_DAG_ID}/clearTaskInstances",
        json=body,
    )
    if r.status_code not in (200, 202):
        raise HTTPException(
            status_code=502,
            detail=f"Airflow restart failed ({r.status_code}): {r.text[:500]}",
        )
    return {"run_id": run_id, "state": "queued"}


@router.get("/jobs/{run_id}/tasks/{task_id}/logs")
async def get_task_logs(run_id: str, task_id: str, try_number: int = 1, tail: int = 200):
    """Return the tail of an Airflow task instance's log so the frontend can
    surface error context without forcing the user back into the Airflow UI."""
    r = await _airflow(
        "GET",
        f"{AIRFLOW_API_PREFIX}/dags/{AIRFLOW_DAG_ID}/dagRuns/{run_id}"
        f"/taskInstances/{task_id}/logs/{try_number}",
        headers={"Accept": "application/json"},
    )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Log not available yet")
    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Airflow log fetch failed ({r.status_code}): {r.text[:500]}",
        )

    content = ""
    try:
        data = r.json()
        if isinstance(data, dict):
            raw = data.get("content") or data.get("text") or ""
            if isinstance(raw, list):
                content = "\n".join(
                    seg.get("event", "") if isinstance(seg, dict) else str(seg)
                    for seg in raw
                )
            else:
                content = raw
            if not content and isinstance(data.get("logs"), list):
                content = "\n".join(
                    seg.get("event", seg) if isinstance(seg, dict) else str(seg)
                    for seg in data["logs"]
                )
        elif isinstance(data, list):
            content = "\n".join(str(seg) for seg in data)
        else:
            content = str(data)
    except Exception:
        content = r.text

    lines = content.splitlines()
    if tail and tail > 0 and len(lines) > tail:
        lines = lines[-tail:]
    return {
        "task_id":    task_id,
        "try_number": try_number,
        "lines":      lines,
    }


@router.get("/jobs/{run_id}/events")
async def stream_job_events(run_id: str, request: Request):
    """Server-Sent Events stream of the run's state. Emits one ``status``
    event whenever the run state or any task state changes, plus a final
    ``done`` event once the run reaches a terminal state."""

    async def event_gen():
        last_signature = None
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    status = await _fetch_run_status(run_id)
                except HTTPException as e:
                    transient = e.status_code in GATEWAY_STATUSES
                    yield (
                        "event: error\n"
                        f"data: {json.dumps({'detail': str(e.detail), 'transient': transient})}\n\n"
                    )
                    if transient:
                        # Airflow is briefly unreachable.
                        # The run itself is fine : keep polling, don't end the stream.
                        await asyncio.sleep(SSE_POLL_INTERVAL)
                        continue
                    break
                except Exception as e:  # network glitch — keep the stream alive
                    yield f"event: error\ndata: {json.dumps({'detail': str(e), 'transient': True})}\n\n"
                    await asyncio.sleep(SSE_POLL_INTERVAL)
                    continue

                signature = (
                    status["state"],
                    tuple((t["task_id"], t["state"]) for t in status["tasks"]),
                )
                if signature != last_signature:
                    yield f"event: status\ndata: {json.dumps(status)}\n\n"
                    last_signature = signature

                if status["state"] in TERMINAL_RUN_STATES:
                    yield f"event: done\ndata: {json.dumps(status)}\n\n"
                    break

                await asyncio.sleep(SSE_POLL_INTERVAL)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":      "no-cache",
            "Connection":         "keep-alive",
            "X-Accel-Buffering":  "no",
        },
    )

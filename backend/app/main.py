import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from pipeline import router as pipeline_router
from dashboardareas import router as dashboard_router, canonical_activity_level
from Dashboard import router as global_dashboard_router


app = FastAPI(title="Mining Viewer API")


ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_URL = os.getenv(
    "MINING_DB_URL",
    "postgresql+psycopg2://mining:mining@mining_postgres:5432/mining_geodata",
)
if DB_URL.startswith("postgres://"):
    DB_URL = "postgresql+psycopg2://" + DB_URL[len("postgres://"):]

engine = create_engine(DB_URL, pool_pre_ping=True)


app.include_router(pipeline_router)
app.include_router(dashboard_router)
app.include_router(global_dashboard_router)


def _cors_headers(request: Request) -> dict:
    origin = request.headers.get("origin", "")
    if origin in ALLOWED_ORIGINS:
        return {"Access-Control-Allow-Origin": origin}
    return {}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=_cors_headers(request),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):  # noqa: ARG001
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=_cors_headers(request),
    )


@app.get("/")
def root():
    return {
        "service": "Mining Viewer API",
        "status": "ok",
        "endpoints": [
            "/health",
            "/sites",
            "/api/areas/{id}/polygons/{idx}",
            "/api/mining-areas/bbox/{bbox_id}",
            "/api/mining-areas/{bbox_id}/metadata",
            "/api/jobs",
            "/api/jobs/{run_id}",
            "/api/jobs/{run_id}/events",
            "/api/dashboard/{bbox_id}",
            "/api/global/overview",
        ],
    }


@app.get("/health")
def health():
    """Checking Health """
    return {"status": "ok"}


@app.get("/sites")
def sites():
    """All sites : bbox + headline scalars (volume, area). Drives sidebar cards and bbox layer."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT id, org_id, bbox_id, area_name, country,
                   period_start, period_end,
                   ST_XMin(bbox) AS minx, ST_YMin(bbox) AS miny,
                   ST_XMax(bbox) AS maxx, ST_YMax(bbox) AS maxy,
                   total_area, volume, metadata,
                   COALESCE(jsonb_array_length(geojson->'features'), 0) AS n_polygons
            FROM mining_areas
            ORDER BY id DESC
            """
        )).mappings().all()
    return [
        {
            "id":           r["id"],
            "org_id":       r["org_id"],
            "bbox_id":      r["bbox_id"],
            "area_name":    r["area_name"],
            "country":      r["country"],
            "period_start": r["period_start"].isoformat() if r["period_start"] else None,
            "period_end":   r["period_end"].isoformat() if r["period_end"] else None,
            "bbox":         [float(r["minx"]), float(r["miny"]),
                             float(r["maxx"]), float(r["maxy"])],
            "total_area":   float(r["total_area"]) if r["total_area"] is not None else None,
            "volume":       float(r["volume"])     if r["volume"]     is not None else None,
            "n_polygons":   int(r["n_polygons"]),
            "activity_level": canonical_activity_level(r["metadata"]),
        }
        for r in rows
    ]


@app.get("/api/areas/{area_id}/polygons/{polygon_idx}")
def polygon_properties(area_id: int, polygon_idx: int):
    """One polygon's properties from geojson.features[idx-1]. Polygon click popup uses this."""
    if polygon_idx < 1:
        raise HTTPException(status_code=400, detail="polygon_idx must be >= 1")

    with engine.connect() as conn:
        row = conn.execute(text(
            """
            SELECT id, org_id, bbox_id, area_name,
                   geojson->'features'->(:idx0) AS feat
            FROM mining_areas
            WHERE id = :area_id
            """
        ), {"area_id": area_id, "idx0": polygon_idx - 1}).mappings().first()

    if not row or row["feat"] is None:
        raise HTTPException(status_code=404, detail=f"Polygon {area_id}/{polygon_idx} not found")

    feat = row["feat"]
    props = feat.get("properties") if isinstance(feat, dict) else {}
    return {
        "area_id":     row["id"],
        "org_id":      row["org_id"],
        "bbox_id":     row["bbox_id"],
        "area_name":   row["area_name"],
        "polygon_idx": polygon_idx,
        "properties":  props or {},
    }


@app.get("/api/mining-areas/bbox/{bbox_id}")
def mining_area_by_bbox(bbox_id: str):
    """Site summary keyed by bbox_id, used by the bbox popup."""
    with engine.connect() as conn:
        row = conn.execute(text(
            """
            SELECT id, org_id, bbox_id, area_name,country,
                   ST_XMin(bbox) AS minx, ST_YMin(bbox) AS miny,
                   ST_XMax(bbox) AS maxx, ST_YMax(bbox) AS maxy,
                   total_area, volume,
                   COALESCE(jsonb_array_length(geojson->'features'), 0) AS n_polygons
            FROM mining_areas
            WHERE bbox_id = :bbox_id
            LIMIT 1
            """
        ), {"bbox_id": bbox_id}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail=f"No mining area with bbox_id={bbox_id}")

    return {
        "data": {
            "id":        row["id"],
            "country":   row["country"],
            "org_id":    row["org_id"],
            "bbox_id":   row["bbox_id"],
            "area_name": row["area_name"],
            "bounds":    [float(row["minx"]), float(row["miny"]),
                          float(row["maxx"]), float(row["maxy"])],
            "metrics": {
                "total_area": float(row["total_area"]) if row["total_area"] is not None else None,
                "volume":     float(row["volume"])     if row["volume"]     is not None else None,
                "n_polygons": int(row["n_polygons"]),
            },
        }
    }

@app.get("/api/mining-areas/{bbox_id}/metadata")
def mining_area_metadata(bbox_id: str):
    """Checking : Full fetch_metadata.json for a site, read straight from the metadata JSONB column.

    No file system access : the datalake is touched only by the ingest microservice.
    """
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT metadata FROM mining_areas WHERE bbox_id = :bbox_id LIMIT 1"
        ), {"bbox_id": bbox_id}).first()

    if not row:
        raise HTTPException(status_code=404, detail=f"No mining area with bbox_id={bbox_id}")
    if row[0] is None:
        raise HTTPException(status_code=404, detail=f"No metadata stored for bbox_id={bbox_id}")
    return row[0]




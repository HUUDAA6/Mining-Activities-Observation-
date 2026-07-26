import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from typing import Set

import geopandas as gpd
from azure.storage.blob import BlobServiceClient
from sqlalchemy import create_engine, text

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_UA  = os.getenv(
    "NOMINATIM_USER_AGENT",
    "AriasTechMining",
)
_LAST_NOMINATIM_CALL = 0.0


def _geocode_country(bbox: list[float]) -> str | None:
    """Reverse-geocode the centroid of `bbox = [minLon, minLat, maxLon, maxLat]`
    via Nominatim and return the country name (English). Returns None on any
    failure so ingestion never blocks on geocoding."""
    global _LAST_NOMINATIM_CALL
    try:
        if not bbox or len(bbox) != 4:
            return None
        lon = (float(bbox[0]) + float(bbox[2])) / 2.0
        lat = (float(bbox[1]) + float(bbox[3])) / 2.0

        delta = time.monotonic() - _LAST_NOMINATIM_CALL
        if delta < 1.1:
            time.sleep(1.1 - delta)

        qs = urllib.parse.urlencode({
            "format":          "jsonv2",
            "lat":             f"{lat:.6f}",
            "lon":             f"{lon:.6f}",
            "zoom":            "3",
            "addressdetails":  "1",
            "accept-language": "en",
        })
        req = urllib.request.Request(
            f"{NOMINATIM_URL}?{qs}",
            headers={"User-Agent": NOMINATIM_UA, "Accept": "application/json"},
        )
        _LAST_NOMINATIM_CALL = time.monotonic()
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read().decode("utf-8"))

        addr = payload.get("address") or {}
        return addr.get("country")
    except Exception as e:
        print(f"[geocode] failed for bbox={bbox}: {e}")
        return None


def backfill_countries(engine, limit: int = 25) -> None:
    """Fill country for legacy rows ingested before geocoding was wired in.
    Caps per-cycle work so the watcher main loop stays responsive."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT id,
                   ST_XMin(bbox) AS minx, ST_YMin(bbox) AS miny,
                   ST_XMax(bbox) AS maxx, ST_YMax(bbox) AS maxy
            FROM mining_areas
            WHERE country IS NULL AND bbox IS NOT NULL
            ORDER BY id
            LIMIT :limit
            """
        ), {"limit": limit}).mappings().all()

    if not rows:
        return
    print(f"[geocode] backfilling country for {len(rows)} row(s)")
    for r in rows:
        bbox = [float(r["minx"]), float(r["miny"]), float(r["maxx"]), float(r["maxy"])]
        name = _geocode_country(bbox)
        if not name:
            continue
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE mining_areas SET country = :n WHERE id = :id"
            ), {"n": name, "id": r["id"]})
        print(f"[geocode]   id={r['id']} → {name}")

CONTAINER     = os.getenv("MINING_AZURE_CONTAINER", "mining")
POLL_INTERVAL = int(os.getenv("MINING_POLL_INTERVAL", "30"))
DB_URL        = os.getenv(
    "MINING_DB_URL",
    "postgresql+psycopg2://mining:mining@mining_postgres:5432/mining_geodata",
)
if DB_URL.startswith("postgres://"):
    DB_URL = "postgresql+psycopg2://" + DB_URL[len("postgres://"):]

_BBOX_RE = re.compile(r"__BBOX_([\-\d_\.]+)$")
def _azure_connection_string() -> str:
    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        raise RuntimeError(
            "AZURE_STORAGE_CONNECTION_STRING"
        )
    return conn


def _download_blob(svc: BlobServiceClient, container: str, blob_name: str) -> bytes:
    c = svc.get_blob_client(container=container, blob=blob_name)
    if not c.exists():
        raise FileNotFoundError(f"blob not found: {container}/{blob_name}")
    return c.download_blob().readall()


def _float_or_none(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _bbox_id_from_prefix(prefix: str) -> str | None:
    m = _BBOX_RE.search(prefix)
    if not m:
        return None
    raw = m.group(1)
    parts = raw.split("_")
    if len(parts) != 4:
        return None
    try:
        return "_".join(str(round(float(p), 4)) for p in parts)
    except ValueError:
        return None


def already_ingested(engine) -> Set[str]:
    """Return every bbox_id currently in the database.
    Keying on bbox_id alone (not org_id) means that if org_id is changed
    manually in the DB, the watcher still recognises the area as ingested
    and does not re-insert it."""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT bbox_id FROM mining_areas")).all()
    return {r[0] for r in rows}


def _parse_prefix(prefix: str) -> tuple[str | None, str | None]:
    """Extract (org_id, bbox_id) from an Azure prefix like
    'ariastech/Khouribga__BBOX_-7.0_32.7_-6.9_32.8'."""
    parts = prefix.split("/", 1)
    if len(parts) != 2:
        return None, None
    org_id = parts[0]
    bbox_id = _bbox_id_from_prefix(prefix)
    return org_id, bbox_id


def find_ready_prefixes(svc: BlobServiceClient) -> list[str]:
    """A prefix is 'ready' when it has both fetch_metadata and a geojson."""
    container = svc.get_container_client(CONTAINER)
    by_prefix: dict[str, set[str]] = defaultdict(set)
    try:
        for blob in container.list_blobs():
            if "/" not in blob.name:
                continue
            prefix, fname = blob.name.rsplit("/", 1)
            by_prefix[prefix].add(fname)
    except Exception as e:
        print(f"[watcher] list_blobs failed: {e}")
        return []

    ready = []
    for prefix, files in by_prefix.items():
        if "fetch_metadata.json" in files and any(f.endswith("_DoD_Zones.geojson") for f in files):
            ready.append(prefix)
    return ready


def ingest_from_azure(container: str, prefix: str) -> dict:
    """Download a completed pipeline folder from Azure and insert into PostGIS.

    Writes:
      - mining_areas.metadata  : entire fetch_metadata.json (JSONB)
      - mining_areas.geojson   : entire DoD zones GeoJSON FeatureCollection (JSONB)
      - mining_areas.bbox      : envelope polygon from metadata.bbox
      - mining_logs            : one SUCCESS row keyed back to the area
    """
    svc = BlobServiceClient.from_connection_string(_azure_connection_string())

    metadata_bytes = _download_blob(svc, container, f"{prefix}/fetch_metadata.json")
    metadata = json.loads(metadata_bytes)

    missing = [k for k in ("client", "Bbox_id", "Area_Name") if not metadata.get(k)]
    if missing:
        raise ValueError(f"fetch_metadata.json missing required fields: {missing}")

    org_id       = metadata["client"]
    bbox_id      = metadata["Bbox_id"]
    area_name    = metadata["Area_Name"]
    period_start = metadata.get("period_start")
    period_end   = metadata.get("period_end")

    bbox = metadata.get("bbox")
    if not bbox or len(bbox) != 4:
        raise ValueError("fetch_metadata.json missing valid 'bbox' [min_lon, min_lat, max_lon, max_lat]")

    dod_stats  = metadata.get("dod_stats") or {}
    total_area = _float_or_none(dod_stats.get("total_area_ha"))
    volume     = _float_or_none(dod_stats.get("net_vol_m3"))

    country = _geocode_country(bbox)
    if country:
        print(f"  geocoded country: {country}")

    geojson_name  = f"{area_name}_DoD_Zones.geojson"
    geojson_bytes = _download_blob(svc, container, f"{prefix}/{geojson_name}")

    gdf = gpd.read_file(io.BytesIO(geojson_bytes))
    if gdf.empty:
        print(f"  {geojson_name} has no features, ecording empty site (bbox only).")
        geojson_text = '{"type":"FeatureCollection","features":[]}'
        n_features   = 0
    else:
        if gdf.crs is None:
            gdf = gdf.set_crs(4326)
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(4326)
        geojson_text = gdf.to_json()
        n_features   = len(gdf)

    engine = create_engine(DB_URL, pool_pre_ping=True)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO mining_areas
                  (org_id, bbox_id, area_name, period_start, period_end,
                   total_area, volume, geojson, bbox, metadata, country)
                VALUES
                  (:org_id, :bbox_id, :area_name, :ps, :pe,
                   :total_area, :volume,
                   CAST(:geojson AS JSONB),
                   ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, 4326),
                   CAST(:metadata AS JSONB),
                   :country)
                ON CONFLICT (org_id, bbox_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "org_id":     org_id,
                "bbox_id":    bbox_id,
                "area_name":  area_name,
                "ps":         period_start,
                "pe":         period_end,
                "total_area": total_area,
                "volume":     volume,
                "geojson":    geojson_text,
                "minx":       bbox[0], "miny": bbox[1],
                "maxx":       bbox[2], "maxy": bbox[3],
                "metadata":   metadata_bytes.decode("utf-8")
                              if isinstance(metadata_bytes, (bytes, bytearray))
                              else json.dumps(metadata),
                "country":    country,
            },
        )
        area_id = result.scalar()

        # No new row → another watcher / loop already ingested this site.
        # Log a clean SKIPPED and bail out without writing a duplicate
        # mining_logs row (which would otherwise grow forever).
        if area_id is None:
            existing = conn.execute(
                text("SELECT id FROM mining_areas WHERE org_id=:o AND bbox_id=:b"),
                {"o": org_id, "b": bbox_id},
            ).scalar()
            print(
                f"[watcher] SKIPPED already-ingested site "
                f"org_id={org_id} bbox_id={bbox_id} (existing area_id={existing})"
            )
            return {
                "area_id":   existing,
                "org_id":    org_id,
                "bbox_id":   bbox_id,
                "area_name": area_name,
                "features":  None,
                "status":    "already_ingested",
            }

        conn.execute(
            text(
                """
                INSERT INTO mining_logs (min_area_id, status, run_at, message, resource_type)
                VALUES (:area_id, 'SUCCESS', :run_at, :msg, 'mining')
                """
            ),
            {
                "area_id": area_id,
                "run_at":  datetime.now(timezone.utc),
                "msg":     f"ingested {n_features} features from {prefix}",
            },
        )

    print(f"INGESTED org_id={org_id} bbox_id={bbox_id} area={area_name} features={n_features}")
    return {
        "area_id":   area_id,
        "org_id":    org_id,
        "bbox_id":   bbox_id,
        "area_name": area_name,
        "features":  n_features,
    }


def main():
    print(f"[watcher] starting — container={CONTAINER}, interval={POLL_INTERVAL}s")
    svc    = BlobServiceClient.from_connection_string(_azure_connection_string())
    engine = create_engine(DB_URL, pool_pre_ping=True)

    failed: Set[str] = set()

    while True:
        try:
            done  = already_ingested(engine)
            ready = find_ready_prefixes(svc)
            new = []
            for p in ready:
                if p in failed:
                    continue
                _, bbox_id = _parse_prefix(p)
                if not bbox_id:
                    print(f"[watcher] cannot parse bbox_id from prefix {p!r}; skipping.")
                    failed.add(p)
                    continue
                if bbox_id in done:
                    continue
                new.append(p)

            if new:
                print(f"[watcher] {len(new)} new prefix(es) to ingest: {new}")
            for prefix in new:
                try:
                    ingest_from_azure(CONTAINER, prefix)
                except Exception as e:
                    print(f"[watcher] ingest FAILED for {prefix}: {e}")
                    failed.add(prefix)

            try:
                backfill_countries(engine)
            except Exception as e:
                print(f"[watcher] country backfill error: {e}")
        except Exception as e:
            print(f"[watcher] poll error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

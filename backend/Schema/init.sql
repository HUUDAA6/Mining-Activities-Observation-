CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS mining_areas (
    id             BIGSERIAL PRIMARY KEY,
    org_id         TEXT NOT NULL,
    bbox_id        TEXT NOT NULL,
    area_name      TEXT NOT NULL,
    period_start   DATE,
    period_end     DATE,
    total_area     DOUBLE PRECISION,
    volume         DOUBLE PRECISION,
    geojson        JSONB,
    bbox           GEOMETRY(Polygon, 4326),
    metadata       JSONB,
    country        TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE mining_areas ADD COLUMN IF NOT EXISTS country TEXT;

CREATE INDEX IF NOT EXISTS idx_mining_areas_bbox    ON mining_areas USING GIST (bbox);
CREATE INDEX IF NOT EXISTS idx_mining_areas_org_id  ON mining_areas (org_id);
CREATE INDEX IF NOT EXISTS idx_mining_areas_bbox_id ON mining_areas (bbox_id);
CREATE INDEX IF NOT EXISTS idx_mining_areas_country ON mining_areas (country);

ALTER TABLE mining_areas
    DROP CONSTRAINT IF EXISTS mining_areas_org_bbox_unique;
ALTER TABLE mining_areas
    ADD CONSTRAINT mining_areas_org_bbox_unique UNIQUE (org_id, bbox_id);


CREATE TABLE IF NOT EXISTS mining_logs (
    id             BIGSERIAL PRIMARY KEY,
    min_area_id    BIGINT REFERENCES mining_areas(id) ON DELETE CASCADE,
    status         TEXT NOT NULL,
    run_at         TIMESTAMPTZ NOT NULL,
    message        TEXT,
    resource_type  TEXT NOT NULL DEFAULT 'mining'
);
CREATE INDEX IF NOT EXISTS idx_mining_logs_min_area_id ON mining_logs (min_area_id);
CREATE INDEX IF NOT EXISTS idx_mining_logs_run_at      ON mining_logs (run_at DESC);


DROP FUNCTION IF EXISTS public.mining_polygons(integer, integer, integer, jsonb);

CREATE OR REPLACE FUNCTION public.mining_polygons(
    z integer, x integer, y integer, query_params jsonb DEFAULT '{}'::jsonb
)
RETURNS bytea
LANGUAGE plpgsql
STABLE PARALLEL SAFE STRICT
AS $$
DECLARE
  mvt bytea;
  p_org_id     text             := NULL;
  p_volume_min double precision := NULL;
  p_volume_max double precision := NULL;
  p_area_min   double precision := NULL;
BEGIN
  IF query_params ? 'org_id'     THEN p_org_id     := query_params->>'org_id';                            END IF;
  IF query_params ? 'volume_min' THEN p_volume_min := (query_params->>'volume_min')::double precision;    END IF;
  IF query_params ? 'volume_max' THEN p_volume_max := (query_params->>'volume_max')::double precision;    END IF;
  IF query_params ? 'area_min'   THEN p_area_min   := (query_params->>'area_min')::double precision;      END IF;

  SELECT INTO mvt
    ST_AsMVT(tile, 'mining_polygons', 4096, 'geom')
  FROM (
    SELECT
      ST_AsMVTGeom(
        ST_Transform(
          ST_SetSRID(ST_GeomFromGeoJSON(feat->>'geometry'), 4326),
          3857
        ),
        ST_TileEnvelope(z, x, y),
        4096, 64, true
      ) AS geom,
      a.id AS area_id,
      feat_idx::int AS polygon_idx,
      feat->'properties'->>'color'        AS color,
      feat->'properties'->>'change_class' AS change_class
    FROM public.mining_areas a,
         jsonb_array_elements(a.geojson->'features')
           WITH ORDINALITY AS t(feat, feat_idx)
    WHERE
      a.bbox && ST_Transform(ST_TileEnvelope(z, x, y), 4326)
      AND (p_org_id     IS NULL OR a.org_id = p_org_id)
      AND (p_volume_min IS NULL OR a.volume >= p_volume_min)
      AND (p_volume_max IS NULL OR a.volume <= p_volume_max)
      AND (p_area_min   IS NULL OR a.total_area >= p_area_min)
  ) AS tile
  WHERE geom IS NOT NULL;

  RETURN mvt;
END $$;
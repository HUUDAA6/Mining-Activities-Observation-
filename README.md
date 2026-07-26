# Mining Activities Observation

A platform that watches open-pit mining sites from space and tells you what changed on the ground.

I built this during my internship at Arias Tech Solutions. You draw a bounding box on a map, and behind the scenes a pipeline pulls elevation data for two time periods, lines them up, and works out exactly how much earth was cut and how much was filled in between. That number gets turned into polygons of "changed" zones, a volume in cubic meters, an activity level, and a full dashboard — no site visit, no drone, nothing but public satellite elevation data and some geometry.

This is a portfolio copy of the code I actually wrote and shipped there. It isn't deployed anywhere anymore and all credentials have been stripped out before pushing, but nothing here is a rewrite or a cleaned-up "presentable" version — it's the real thing.

## Watch it work

- 🎥 [**Pipeline run** — triggering a job and watching it produce results, live](https://drive.google.com/file/d/1msR7bhnWQ0Es2rzAV6qO26oRVWY1I1gB/view?usp=sharing)
- 🎥 [**Full platform tour** — the map, the dashboards, the Mapbox layers, all of it](https://drive.google.com/file/d/111poAkdhTDMC2zfOvVsJPPaRx8kWbvC8/view?usp=sharing)

![Map view of mining sites across North Africa](Photos/UI%20Plateforme.PNG)

## What's actually in it

Draw a box anywhere on the map and it kicks off a real analysis, not a mock one. The pipeline fetches DEMs — Digital Elevation Models, grids of ground-height values — for two dates, aligns them pixel for pixel, and subtracts one from the other. That difference is what geologists call a DoD, a DEM of Difference, and it's the standard way to measure how much material physically moved on a site between two points in time.

Every site comes back with numbers that mean something: net volume in m³, max excavation depth, a cut/fill ratio, percentage of the area disturbed. Not "something changed here," but *how much*, and in which direction.

| | |
|---|---|
| ![Site detail with cut/fill KPIs](Photos/UI%20Dasboard%20Area.png) | ![Change classes and elevation histogram](Photos/UI%20change%20classes.PNG) |

Zoom out and there's a global dashboard sitting on top of all of it — volume by country, a risk-distribution donut across every tracked site, activity levels split low/medium/high. Built for the case where you're not watching one pit, you're watching dozens.

![Global dashboard with volume-by-country and risk distribution](Photos/Dashboard%20Areas.PNG)

Pipeline runs aren't a black box either. Every job streams its own progress back to the browser in real time, step by step — fetching DEMs, aligning, vectorizing change zones, uploading the report — so you're watching it happen instead of refreshing a page and hoping.

| | |
|---|---|
| ![Live pipeline run progress](Photos/UI%20pipeline%20live.PNG) | ![Pipeline step detail and stop confirmation](Photos/UI%20PIipeline%20Actions.PNG) |
| ![Pipeline run history dashboard](Photos/UI%20Dashboard%20Pipelines.PNG) | |

There are also free NASA GIBS layers baked into the map — vegetation index, precipitation, land surface temperature — sitting underneath the polygons at whatever opacity you set. Handy for a sanity check when a "disturbed area" spike shows up: is that actually mining, or did it just rain a lot that month?

![NASA GIBS data layers panel](Photos/Nasa%20APIs%20UI.PNG)

And the basemap switches between satellite, dark, light, oceanic, topographic, and raw DEM — because inspecting terrain and presenting to a client are two different jobs that want two different maps.

![Map style and layer controls](Photos/UI%20Mapbox%20Templates.PNG)

## How the four pieces fit together

```
  browser (React)
      │
      ▼
  backend (FastAPI) ──────► PostGIS   (sites, polygons, dashboard queries)
      │       │
      │       └──────────► Airflow REST API   (trigger + poll runs, via Cloudflare Access)
      │
      ▼
  Pipeline (Airflow DAG) ──► fetch DEMs → align → diff → vectorize → PDF report → Azure Blob
                                                                            │
                                                                            ▼
  Microservice (watcher) ◄───────────────────────────────────────── polls Azure, writes to PostGIS
```

**`frontend/`** — React 19, Vite, Mapbox GL. The map, the draw-a-box tool, the dashboards, the pipeline monitor. It only ever talks to the backend — never to Airflow or Azure directly.

**`backend/`** — FastAPI over PostGIS with SQLAlchemy. Serves site and dashboard data, and sits in front of Airflow as a proxy so the browser never needs Airflow credentials at all — that hop goes through Cloudflare Access instead. Vector tiles are served separately by [Martin](https://github.com/maplibre/martin) (`backend/martin-config.yaml`).

**`Pipeline/`** — The actual Airflow DAG (`mining_pipeline_bbox.py`) plus the processing scripts behind it in `Approach2/`: fetch elevation data, align two rasters, calculate the DoD, vectorize the changed zones, render a PDF site report with matplotlib/rasterio/reportlab, push everything to Azure Blob Storage. It's idempotent by design — rerunning the same bounding box finds the cached result instead of redoing the work from scratch.

**`Microservice/`** — A small watcher (`ingest_watcher.py`) that polls Azure for finished pipeline output, reverse-geocodes each site's country from its coordinates, and writes the result into PostGIS. It's the only piece of the system with write access to the datalake — the API server never touches it directly.

## Stack

| Layer | Tech |
|---|---|
| Frontend | React 19, Vite, Mapbox GL JS, react-map-gl |
| Backend API | FastAPI, SQLAlchemy, PostGIS |
| Tile serving | Martin (vector tiles) |
| Orchestration | Apache Airflow |
| Geoprocessing | rasterio, GeoPandas, NumPy, contextily |
| Reporting | ReportLab, Matplotlib |
| Storage | Azure Blob Storage |
| Internal auth | Cloudflare Access service tokens |
| Containers | Docker, one `Dockerfile` per service |

## Repo layout

```
backend/       FastAPI service, PostGIS schema, Martin tile config
frontend/      React + Mapbox viewer
Pipeline/      Airflow DAG and the DEM-diff processing scripts it runs
Microservice/  Azure Blob → PostGIS ingestion watcher
Photos/        Screenshots used in this README
```

There's no root docker-compose stitching all four together on purpose — in production they lived on separate infra, connected through Azure and Cloudflare rather than one shared docker network, and this repo mirrors that.

## About running this yourself

It won't spin up out of the box, and that's intentional — it was built against Arias Tech Solutions' own Azure storage, database, and Airflow instance, and the `.env` files that pointed to those were left out before this ever hit GitHub. What's here is the real architecture and the real code, meant to be read, not a deploy-and-go demo.

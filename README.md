# Mining Activities Observation

A platform for monitoring open-pit mining activity from satellite elevation data.

I built this during my internship at Arias Tech Solutions. The idea was simple: instead of sending someone to a mining site or flying drones over it, let the user draw a bounding box on a map and automatically analyse how the terrain changed between two points in time.

Behind the scenes, the system fetches Digital Elevation Models (DEMs) for the selected area, aligns them, compares them, and calculates exactly how much material was excavated or deposited. The results are turned into polygons showing where changes happened, volume estimates in cubic metres, activity classifications, and an interactive dashboard.

This repository is a portfolio copy of the code I wrote while working there. It isn't deployed anymore, all credentials have been removed, and some configuration has been stripped for security, but the code itself is the same code that was used during the internship. I didn't rewrite or simplify it afterwards just to make it look nicer.

## Watch it work

🎥 [**Pipeline run**](https://drive.google.com/file/d/1msR7bhnWQ0Es2rzAV6qO26oRVWY1I1gB/view?usp=sharing) – triggering a new analysis and following every processing step live.

🎥 [**Full platform tour**](https://drive.google.com/file/d/111poAkdhTDMC2zfOvVsJPPaRx8kWbvC8/view?usp=sharing) – exploring the interactive map, dashboards, pipeline monitor, and layer controls.

![Map view of mining sites across North Africa](Photos/UI%20Plateforme.PNG)

---

## What the platform does

Everything starts with drawing a bounding box on the map.

That immediately triggers a real processing pipeline. The system downloads elevation data for two different dates, aligns both datasets so every pixel represents the same location, then subtracts one elevation model from the other.

This produces a DEM of Difference (DoD), which is the standard approach used in geospatial analysis to measure terrain changes over time.

From that difference, the platform calculates meaningful metrics including:

- Volume of excavated or deposited material (m³)
- Maximum excavation depth
- Cut-to-fill ratio
- Percentage of disturbed ground
- Detected change polygons
- Activity level classification

Rather than simply saying that something changed, the platform measures how much ground moved and where it happened.

| | |
|---|---|
| ![Site detail with cut/fill KPIs](Photos/UI%20Dasboard%20Area.png) | ![Change classes and elevation histogram](Photos/UI%20change%20classes.PNG) |

---

## Dashboards

The platform is designed to monitor many mining sites at once rather than a single location.

The dashboard aggregates information across every analysed site and displays total moved volume, activity levels, country-level statistics, and overall risk distribution.

This makes it easy to identify the sites that deserve attention without opening every analysis individually.

![Global dashboard with volume by country and risk distribution](Photos/Dashboard%20Areas.PNG)

---

## Live pipeline monitoring

Every analysis job reports its progress back to the browser in real time.

Instead of submitting a request and waiting for a result, you can watch each stage as it runs, including downloading elevation data, aligning rasters, calculating differences, vectorising change areas, generating reports, and uploading the final outputs.

Completed runs are also stored in a history view so previous analyses can be reviewed at any time.

| | |
|---|---|
| ![Live pipeline progress](Photos/UI%20pipeline%20live.PNG) | ![Pipeline step details](Photos/UI%20PIipeline%20Actions.PNG) |
| ![Pipeline history dashboard](Photos/UI%20Dashboard%20Pipelines.PNG) | |

---

## Environmental context

The map also includes several freely available NASA GIBS layers such as vegetation index, precipitation, and land surface temperature.

These layers can be displayed underneath the mining polygons with adjustable opacity, making it easier to interpret whether detected terrain changes are likely related to mining activity or environmental conditions.

![NASA GIBS layers](Photos/Nasa%20APIs%20UI.PNG)

---

## Map styles

Different tasks require different maps.

For that reason the platform supports several basemaps, including satellite imagery, light and dark themes, topographic maps, ocean maps, and raw elevation data.

Switching between them helps whether the goal is detailed terrain inspection or presenting results to a client.

![Map style controls](Photos/UI%20Mapbox%20Templates.PNG)

---

## System architecture

The application is split into four services that communicate with each other.

The React frontend is responsible for the user interface. It displays the interactive map, dashboards, drawing tools, and pipeline monitor. It only communicates with the backend and never talks directly to Airflow or Azure.

The FastAPI backend acts as the main API layer. It stores and retrieves information from PostGIS, exposes dashboard endpoints, and securely proxies requests to Airflow through Cloudflare Access so the browser never needs orchestration credentials.

The Airflow pipeline performs the heavy geospatial processing. It downloads elevation datasets, aligns them, computes the DEM of Difference, extracts change polygons, generates PDF reports, uploads results to Azure Blob Storage, and caches completed analyses so identical requests are not processed twice.

A separate ingestion service continuously watches Azure Blob Storage for completed pipeline outputs. When new results appear, it reverse-geocodes the site's country, stores all metadata inside PostGIS, and makes the analysis available to the frontend.

```
Browser (React)
        │
        ▼
Backend (FastAPI)
        │
        ├────────► PostGIS
        │
        ├────────► Airflow REST API
        │
        ▼
Airflow Pipeline
        │
        ▼
Fetch DEMs
        │
Align
        │
Difference
        │
Vectorise
        │
Generate PDF
        │
Upload to Azure Blob
        │
        ▼
Ingestion Service
        │
        ▼
PostGIS
```

---

## Technologies

**Frontend**
- React 19
- Vite
- Mapbox GL JS
- react-map-gl

**Backend**
- FastAPI
- SQLAlchemy
- PostGIS
- Martin (Vector Tile Server)

**Pipeline**
- Apache Airflow
- Rasterio
- GeoPandas
- NumPy
- Contextily

**Reporting**
- ReportLab
- Matplotlib

**Storage**
- Azure Blob Storage

**Infrastructure**
- Cloudflare Access
- Docker

---

## Repository structure

`frontend/`
React application containing the interactive map, dashboards, drawing tools, and pipeline monitor.

`backend/`
FastAPI service, PostGIS models, dashboard endpoints, and Martin vector tile configuration.

`Pipeline/`
The Airflow DAG together with the complete DEM processing workflow responsible for terrain comparison, report generation, and Azure uploads.

`Microservice/`
The ingestion watcher that monitors Azure Blob Storage, enriches completed analyses, and stores them in PostGIS.

`Photos/`
Screenshots used throughout this README.

There is intentionally no root Docker Compose file connecting every service together. In production these services were deployed independently and communicated through Azure and Cloudflare rather than sharing a single Docker network, and this repository mirrors that architecture.

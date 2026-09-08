# AquaStress

**European reservoir water surface monitor**, built with Sentinel-2 imagery, DuckDB-Wasm, and MapLibre GL.

[Live demo](https://tomas-michalik11.github.io/water_surfaces/)

## What it does

AquaStress tracks water surface area for reservoirs across Europe using Sentinel-2 multispectral imagery. It's a small project born out of wanting to see drought trends without waiting for someone else to publish a dashboard.

There's no backend. The whole frontend runs client-side: DuckDB-Wasm executes SQL queries directly in the browser against Parquet files served as static assets, using HTTP range requests so it doesn't need to pull entire datasets to answer a query. That keeps hosting free (GitHub Pages) and the app fast without a server to maintain.

## Stack

**Data pipeline**
- Python, Geopandas, Pandas
- Microsoft Planetary Computer (PySTAC, odc-stac) for querying and streaming Sentinel-2 scenes
- Rasterio / Rioxarray for raster processing

**Frontend**
- Plain HTML/CSS/JS, no framework
- DuckDB-Wasm for in-browser SQL
- MapLibre GL JS for the map
- ApexCharts for the time-series charts

**Data formats**
- Parquet for the time-series data (compact, works well with range requests)
- GeoJSON for reservoir geometries

## How it works

### Pipeline

The core signal is MNDWI (Modified Normalized Difference Water Index), computed from Sentinel-2 L2A scenes to separate water from land.

Clouds are the main obstacle with any optical satellite approach, so the pipeline (`backfill.py`) uses a two-zone check: it first evaluates cloud cover over the reservoir's core area. If that's clear enough, it extends the MNDWI calculation to a wider buffer zone around the reservoir, which helps catch high-water events that a tight boundary would miss.

When part of the reservoir is still obscured by cloud, the pipeline estimates the missing area using a ratio-based extrapolation from the visible core pixels. It's an estimate, not a measurement — worth keeping in mind when interpreting the numbers, especially for reservoirs with frequent cloud cover.

`prepare_web_data.py` then cleans the raw output — removing physically implausible jumps and applying smoothing — and `update.py` handles incremental monthly updates so the full history doesn't need to be recomputed each time.

### Frontend

On load, `app.js` spins up a DuckDB-Wasm instance and queries `water_trends_history_web.parquet` via range requests to get current fill levels for all reservoirs at once. That gets merged with the GeoJSON geometries and rendered on the map with a simple choropleth (red = low, blue = full). Clicking a reservoir runs a local SQL query to pull its 8-year history for the chart.

## A known limitation

There's no consistent ground-truth dataset to validate the estimated water surface area against across all reservoirs — official measurements (where they exist) are usually reported as volume or water level, not area, and aren't uniformly available across countries. Current validation leans on cross-checking against independent sources like JRC Global Surface Water and basic sanity bounds (e.g. estimated area shouldn't exceed a reservoir's documented maximum extent). If you've dealt with this for a multi-country dataset, I'd genuinely like to hear how.

## Project structure

```
water_surface/
├── index.html                 # App entry point
├── style.css
├── app.js                     # DuckDB, MapLibre, chart logic
│
├── backfill.py                # Historical data pipeline
├── update.py                  # Incremental monthly update
├── prepare_web_data.py        # Cleaning, anomaly removal, smoothing
├── clean_check_point.py       # Pipeline checkpoint management
├── convert.py                 # Data format conversions
│
├── requirements.txt
├── Dockerfile
├── notebook.ipynb             # Exploration / prototyping
│
└── data/
    ├── eu_water_surfaces.geojson
    ├── eu_water_surfaces.parquet
    ├── water_trends_history.parquet
    └── water_trends_history_web.parquet
```

## Running it locally

Requires Python 3.10+ and a modern browser.

```bash
git clone https://github.com/tomas-michalik11/water_surfaces.git
cd water_surfaces

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

To pull the latest month of data (needs internet access to Microsoft Planetary Computer):

```bash
python update.py
```

To run the frontend — it uses ES modules and DuckDB-Wasm, so it needs to be served over HTTP, not opened directly as a file:

```bash
python -m http.server 8000
```

Then open `http://localhost:8000`.

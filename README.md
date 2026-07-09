<div align="center">
  <h1>🌊 AquaStress</h1>
  <p><strong>European Reservoir & Water Surface Monitor</strong></p>
  <p>
    An end-to-end, serverless data pipeline and web application for monitoring water levels across European reservoirs using Sentinel-2 satellite imagery, DuckDB-Wasm, and MapLibre GL.
  </p>
  <h3>🌍 <a href="https://tomas-michalik11.github.io/water_surfaces/">View the Live Demo on GitHub Pages</a></h3>
</div>

---

## 🚀 Overview

AquaStress addresses the critical need for transparent, high-frequency monitoring of water capacities in European reservoirs. By leveraging multispectral satellite imagery (Sentinel-2) and processing it through a scalable cloud-native pipeline, it extracts historical water surface areas. 

Instead of relying on heavy backend databases, the frontend is entirely serverless. It utilizes **DuckDB-Wasm** to perform lightning-fast analytical queries directly on Parquet files within the user's browser, delivering a highly responsive, interactive geographic dashboard.

## 🛠️ Technology Stack

### Data Pipeline (Backend)
- **Python 3**
- **Geopandas & Pandas**: Geospatial and tabular data manipulation.
- **Microsoft Planetary Computer (PySTAC, odc-stac)**: Querying and streaming satellite imagery.
- **Rasterio & Rioxarray**: Raster processing and masking.

### Frontend Application
- **HTML / CSS / Vanilla JavaScript**: Lightweight, framework-free core.
- **DuckDB-Wasm**: In-browser SQL analytics engine.
- **MapLibre GL JS**: High-performance, WebGL-powered vector maps.
- **ApexCharts**: Smooth, interactive time-series visualizations.

### Data Formats
- **Parquet**: Highly compressed columnar format for time-series data.
- **GeoJSON**: Geometry definitions for the frontend map.

---

## 🏗️ Architecture & Methodology

The AquaStress architecture is divided into an offline data ingestion pipeline and an online, serverless web client.

### 1. Data Pipeline
The core methodology involves calculating the **Modified Normalized Difference Water Index (MNDWI)** from Sentinel-2 L2A imagery to distinguish water pixels from land.
- **Dual-Zone Cloud Masking**: The pipeline (`backfill.py`) implements an advanced validation algorithm. It evaluates cloud cover over the *core* of the reservoir. If the core is sufficiently clear, it processes the MNDWI over an *expanded buffer zone* (to capture high-water events).
- **Extrapolation**: It estimates missing water area obscured by clouds using a ratio-based extrapolation from the visible core pixels, returning highly accurate water surface areas in km².
- **Anomaly Detection & Smoothing**: `prepare_web_data.py` cleans the raw pipeline output, removing impossible sudden drops (anomalies) and applying a rolling average to smooth the historical curve.
- **Incremental Updates**: `update.py` efficiently queries only the missing recent months and appends them to the historical dataset.

### 2. Serverless Web Client
The web client operates entirely in the browser without a traditional backend API.
- **In-Browser Analytics**: `app.js` initializes a DuckDB-Wasm instance. When the application loads, it performs HTTP range requests to the `water_trends_history_web.parquet` file to calculate the latest capacity percentages for all lakes simultaneously.
- **Interactive Mapping**: The data is merged with GeoJSON geometries on-the-fly and rendered on a MapLibre GL canvas using dynamic choropleth styling (Red = Critical, Blue = Full).
- **On-Demand Queries**: Clicking a reservoir on the map triggers a blazing-fast localized SQL query via DuckDB to fetch the 8-year historical time-series data, immediately updating the ApexCharts visualization.

---

## ✨ Key Features

- **Automated Satellite Ingestion**: Robust Python pipeline querying the Planetary Computer STAC API with built-in retry and concurrency mechanisms.
- **Intelligent Cloud Masking**: Dual-zone evaluation maximizing usable imagery even in partially cloudy conditions.
- **Serverless Architecture**: Zero backend infrastructure required. DuckDB-Wasm handles complex SQL aggregations directly in the browser.
- **Performant Visualizations**: 60fps WebGL map rendering via MapLibre GL and dynamic, animated charting via ApexCharts.

---

## 📂 Project Structure

```text
water_surface/
│
├── 📜 index.html                 # Main application entry point
├── 🎨 style.css                  # Custom styling and layout
├── 🧠 app.js                     # Frontend logic (DuckDB, MapLibre, Charts)
│
├── 🐍 backfill.py                # Historical data pipeline (multi-threaded STAC querying)
├── 🐍 update.py                  # Incremental update pipeline for the latest data
├── 🐍 prepare_web_data.py        # Data cleaner, anomaly remover, and smoother
├── 🐍 clean_check_point.py       # Utility to manage pipeline checkpoints
├── 🐍 convert.py                 # Utility for data format conversions
│
├── 📦 requirements.txt           # Python dependencies for the pipeline
├── 🐳 Dockerfile                 # Container definition for pipeline execution
│
├── 📓 notebook.ipynb             # Research, prototyping, and data exploration
│
└── 📁 data/                      # Local data storage
    ├── eu_water_surfaces.geojson # Reservoir geometries for the frontend
    ├── eu_water_surfaces.parquet # Reservoir definitions for the backend
    ├── water_trends_history.parquet       # Raw pipeline output
    └── water_trends_history_web.parquet   # Cleaned & smoothed production data
```

---

## ⚙️ Getting Started & Installation

### Prerequisites
- Python 3.10+
- A modern web browser (Chrome, Firefox, Edge, Safari)

### 1. Clone and Setup
```bash
# Clone the repository
git clone https://github.com/tomas-michalik11/water_surfaces.git
cd water_surfaces

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate

# Install pipeline dependencies
pip install -r requirements.txt
```

### 2. Running the Data Pipeline
If you wish to update the data or run the backfill (requires internet access to Microsoft Planetary Computer):
```bash
# Run the incremental update for the latest month (automatically prepares web data too)
python update.py
```

### 3. Running the Web Application
Since the frontend uses ES Modules and DuckDB-Wasm, it must be served over HTTP (not the `file://` protocol).

```bash
# Start a local Python HTTP server
python -m http.server 8000
```
Navigate to `http://localhost:8000` in your web browser.

---


// app.js


// Import DuckDB-Wasm via a CDN as an ES Module
import * as duckdb from 'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.28.0/+esm';

// Global variables to hold our database and connection
let db;
let conn;

async function setupDatabase() {
    console.log("🦆 Initializing DuckDB-Wasm...");

    // Select the best WebAssembly bundle for the user's browser
    const JSDELIVR_BUNDLES = duckdb.getJsDelivrBundles();
    const bundle = await duckdb.selectBundle(JSDELIVR_BUNDLES);

    const worker_url = URL.createObjectURL(
        new Blob([`importScripts("${bundle.mainWorker}");`], { type: 'text/javascript' })
    );

    const worker = new Worker(worker_url);
    const logger = new duckdb.ConsoleLogger();
    db = new duckdb.AsyncDuckDB(logger, worker);

    await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
    URL.revokeObjectURL(worker_url);

    // Open a persistent connection
    conn = await db.connect();
    console.log("✅ DuckDB successfully running in the browser!");

    // Trigger the data loading!
    await loadDataAndRenderMap();
}

// Call the setup function immediately
setupDatabase();



// 1. Initialize MapLibre with a Free Dark Basemap
const map = new maplibregl.Map({
    container: 'map', // Targets the <div id="map">
    // CARTO Dark Matter - Free, no API key required, beautiful dark theme
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    center: [14.0, 49.0], // Centered over Europe
    zoom: 4,
    pitch: 0,
});

map.addControl(new maplibregl.NavigationControl());

// 2. Initialize the empty ApexChart
const chartOptions = {
    series: [
        { name: 'Smoothed Area', data: [] },
        { name: 'Raw Area', data: [] }
    ],
    chart: {
        type: 'area',
        height: '100%',
        fontFamily: 'Inter, sans-serif',
        background: 'transparent',
        toolbar: { show: false },
        animations: { enabled: true, easing: 'easeinout', speed: 800 }
    },
    theme: { mode: 'dark' },
    colors: ['#06b6d4', '#64748b'], // Cyan for smoothed, gray for raw
    fill: {
        type: 'gradient',
        gradient: {
            shadeIntensity: 1,
            opacityFrom: 0.6,
            opacityTo: 0.05,
            stops: [0, 90, 100]
        }
    },
    dataLabels: { enabled: false },
    stroke: { curve: 'smooth', width: [3, 1], dashArray: [0, 5] },
    legend: {
        show: true,
        position: 'top',
        horizontalAlign: 'right',
        offsetY: -10,
        labels: {
            colors: '#94a3b8'
        }
    },
    tooltip: {
        theme: 'dark',
        shared: true,
        intersect: false,
        y: {
            formatter: function (val) {
                if (val === undefined || val === null) return "N/A";
                return val.toFixed(1) + ' km²';
            }
        }
    },
    xaxis: {
        type: 'datetime',
        labels: {
            style: { colors: '#94a3b8' },
            datetimeFormatter: {
                year: 'yyyy',
                month: 'MMM yyyy'
            }
        },
        axisBorder: { show: false },
        axisTicks: { show: false },
        tooltip: { enabled: false } // disable xaxis tooltip as we have shared tooltip
    },

    yaxis: {
        labels: {
            style: { colors: '#94a3b8' },
            formatter: (val) => val.toFixed(1) + ' km²'
        }
    },
    grid: {
        borderColor: 'rgba(255, 255, 255, 0.05)',
        strokeDashArray: 4,
    }
};

const chart = new ApexCharts(document.querySelector("#chart-container"), chartOptions);
chart.render();

console.log("✅ Map and Chart initialized!");


// 3. Load Data using DuckDB
// 3. Load Data using DuckDB & Fetch
async function loadDataAndRenderMap() {
    console.log("📊 Querying latest water data directly from Parquet...");

    // We only need standard DuckDB now! No spatial extension needed.
    // This finds the most recent date and grabs the water_percent for all lakes.
    // 1. Construct the full absolute URL (http://localhost:8000/data/...)
    // Later, you will simply replace this with your Cloudflare R2 URL!
    const fileUrl = new URL('data/water_trends_history_web.parquet', window.location.href).href;
    // 2. Use the full URL in the SQL query
    // We use arg_max() to find the most recent water_percent that is NOT a cloudy 0%
    const query = `
        SELECT 
            hylak_id, 
            arg_max(water_percent, date) as water_percent 
        FROM '${fileUrl}' 
        WHERE water_percent > 0
        GROUP BY hylak_id
    `;


    const result = await conn.query(query);
    const rows = result.toArray();

    // Create a fast Javascript lookup dictionary: { hylak_id: water_percent }
    const waterDataMap = {};
    rows.forEach(row => {
        waterDataMap[row.hylak_id] = row.water_percent;
    });

    console.log("🗺️ Fetching native GeoJSON geometries...");
    // Load the file you just converted
    const response = await fetch('data/eu_water_surfaces.geojson');
    const geojsonData = await response.json();

    console.log("🔗 Merging DuckDB data with MapLibre geometries...");
    // Inject the water_percent into the GeoJSON properties so MapLibre can style it
    geojsonData.features.forEach(feature => {
        // Hylak_id is the primary key connecting the two datasets
        const lakeId = feature.properties.Hylak_id;

        // If data exists, assign it. If not, default to 0.
        feature.properties.water_percent = waterDataMap[lakeId] || 0;
    });

    console.log(`✅ Ready to render ${geojsonData.features.length} lakes!`);

    // Ensure the MapLibre map is fully loaded before drawing
    if (map.loaded()) {
        addMapLayer(geojsonData);
    } else {
        map.on('load', () => addMapLayer(geojsonData));
    }
}


// 4. Render the GeoJSON on the Map
function addMapLayer(geojsonData) {
    // Add the GeoJSON data as a source
    map.addSource('lakes', {
        type: 'geojson',
        data: geojsonData
    });

    // Add a fill layer with dynamic Choropleth styling based on water_percent
    map.addLayer({
        id: 'lakes-fill',
        type: 'fill',
        source: 'lakes',
        paint: {
            // Dynamic styling: 0-30% = Red/Orange, 30-70% = Yellow/Cyan, 70-100% = Deep Blue
            'fill-color': [
                'interpolate',
                ['linear'],
                ['get', 'water_percent'],
                0, '#ef4444',    // Critical (Red)
                30, '#f97316',   // Low (Orange)
                70, '#06b6d4',   // Normal (Cyan)
                100, '#3b82f6'   // Full (Blue)
            ],
            'fill-opacity': 0.8
        }
    });

    // Add a glowing border around the lakes
    map.addLayer({
        id: 'lakes-border',
        type: 'line',
        source: 'lakes',
        paint: {
            'line-color': '#e0f2fe',
            'line-width': 1,
            'line-opacity': 0.5
        }
    });

    // --- HIGHLIGHT LAYERS ---
    
    // 1. Highlight Fill (Yellowish with low opacity)
    map.addLayer({
        id: 'lakes-highlight-fill',
        type: 'fill',
        source: 'lakes',
        paint: {
            'fill-color': '#fde047', // Vibrant yellow
            'fill-opacity': 0.3
        },
        filter: ['==', ['get', 'Hylak_id'], -1] // Start hidden
    });

    // 2. Highlight Glow (Thick, blurred, highly transparent line)
    map.addLayer({
        id: 'lakes-highlight-glow',
        type: 'line',
        source: 'lakes',
        paint: {
            'line-color': '#fde047',
            'line-width': 12,
            'line-opacity': 0.4,
            'line-blur': 8
        },
        filter: ['==', ['get', 'Hylak_id'], -1]
    });

    // 3. Highlight Sharp Border (Thin, bright solid line)
    map.addLayer({
        id: 'lakes-highlight-border',
        type: 'line',
        source: 'lakes',
        paint: {
            'line-color': '#fef08a', // Brighter yellow
            'line-width': 2,
            'line-opacity': 1
        },
        filter: ['==', ['get', 'Hylak_id'], -1]
    });

    console.log("🎨 Lakes and highlight layers drawn on map!");


    // --- Add Interactivity ---

    // Change cursor to a pointer when hovering over lakes
    map.on('mouseenter', 'lakes-fill', () => {
        map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', 'lakes-fill', () => {
        map.getCanvas().style.cursor = '';
    });

    // When a user clicks a lake...
    map.on('click', 'lakes-fill', async (e) => {
        const feature = e.features[0];
        // We grab the properties from the GeoJSON
        const lakeId = feature.properties.Hylak_id;
        const lakeName = feature.properties.name;
        const currentCap = feature.properties.water_percent;

        // Apply the highlight filters so only the clicked lake lights up!
        map.setFilter('lakes-highlight-fill', ['==', ['get', 'Hylak_id'], lakeId]);
        map.setFilter('lakes-highlight-glow', ['==', ['get', 'Hylak_id'], lakeId]);
        map.setFilter('lakes-highlight-border', ['==', ['get', 'Hylak_id'], lakeId]);

        // 1. Update the UI Text
        document.getElementById('lake-name').innerText = lakeName || "Unnamed Reservoir";
        document.getElementById('current-capacity').innerText = currentCap.toFixed(1) + '%';

        // 2. Fetch the 8-year history instantly using DuckDB HTTP Range Requests!
        console.log(`📈 Fetching historical data for Lake ID: ${lakeId}`);
        const fileUrl = new URL('data/water_trends_history_web.parquet', window.location.href).href;

        const historyQuery = `
            SELECT date, water_area_km2, smoothed_area_km2 
            FROM '${fileUrl}' 
            WHERE hylak_id = ${lakeId} AND water_area_km2 > 0
            ORDER BY date
        `;


        const result = await conn.query(historyQuery);
        const historyRows = result.toArray();

        // 3. Format the data for ApexCharts (it expects [timestamp, value] arrays)
        const smoothedData = historyRows.map(row => [
            new Date(row.date).getTime(),
            row.smoothed_area_km2
        ]);
        
        const rawData = historyRows.map(row => [
            new Date(row.date).getTime(),
            row.water_area_km2
        ]);

        // 4. Animate the chart!
        // We use updateOptions instead of updateSeries to force ApexCharts 
        // to fully recalculate the layout (legends, tooltips) from the empty state
        chart.updateOptions({
            series: [
                { name: 'Smoothed Area', data: smoothedData },
                { name: 'Raw Area', data: rawData }
            ]
        });
    });



}


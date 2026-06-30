import geopandas as gpd

print("📂 Loading spatial parquet...")
gdf = gpd.read_parquet("data/eu_water_surfaces.parquet")

print("🗺️ Converting to native GeoJSON...")
gdf.to_file("data/eu_water_surfaces.geojson", driver="GeoJSON")

print("✅ Done! You can delete this script now.")

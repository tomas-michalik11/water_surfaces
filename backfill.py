import geopandas as gpd
import pandas as pd
from pystac_client import Client
import planetary_computer
import odc.stac
import rioxarray
import rasterio.features
import numpy as np
import os
import concurrent.futures
import time
import gc
import random

VALID_PIXEL_THRESHOLD = 0.70
MIN_WATER_PERCENT_THRESHOLD = 10.0
SCL_INVALID = [0, 3, 8, 9, 10, 11]  


def get_best_scene(catalog, bbox, start_date, end_date):
    """Queries STAC and returns the single scene with the lowest cloud cover for the period."""
    max_retries = 5
    
    for attempt in range(max_retries):
        try:
            search = catalog.search(
                collections=["sentinel-2-l2a"], 
                bbox=bbox,
                datetime=f"{start_date}/{end_date}",
            )
            items = list(search.item_collection())
            
            if not items:
                return None
                
            items.sort(key=lambda x: x.properties["eo:cloud_cover"])
            return items[0]
            
        except Exception as e:
            if attempt < max_retries - 1:
                # Add "Jitter" (a random fraction of a second) so threads don't wake up together
                base_sleep = 2 ** attempt 
                jitter = random.uniform(0.5, 2.5) 
                sleep_time = base_sleep + jitter
                
                print(f"    [!] API/Network error. Retrying in {sleep_time:.1f}s...")
                time.sleep(sleep_time)
            else:
                print(f"    [!] Failed completely after {max_retries} attempts. Giving up on this month.")
                return None
    return None


def process_scene(item, lake_row):
    """Loads the bands, applies cloud mask, calculates MNDWI, clips to the lake, and returns the water area in km²."""
    bbox = lake_row.geometry.bounds
    
    print("  -> Starting odc.stac.load...")
    try:
        ds = odc.stac.load(
            [item],
            bands=["B03", "B11", "SCL"],
            bbox=bbox,
            crs="EPSG:3857",
            resolution=10
        ).isel(time=0)
        print("  -> odc.stac.load finished successfully!")
    except Exception as e:
        print(f"  [!] Failed to load data from STAC: {e}")
        return None

    print("  -> Projecting geometries for Dual-Zone evaluation...")
    lake_gdf = gpd.GeoDataFrame([lake_row], crs="EPSG:4326", geometry='geometry')
    
    # 1. Project the ORIGINAL, unbuffered core geometry
    lake_core_projected = lake_gdf.to_crs(ds.rio.crs)
    expected_core_pixels = int(lake_core_projected.geometry.area.values[0] / 100)
    
    # 2. Apply the 100m buffer to create the expanded boundary
    lake_buffered_projected = lake_core_projected.copy()
    lake_buffered_projected.geometry = lake_buffered_projected.geometry.buffer(100)
    
    # Clip the ENTIRE dataset early using the BUFFERED geometry
    ds.rio.write_crs(ds.rio.crs, inplace=True)
    try:
        ds_clipped = ds.rio.clip(lake_buffered_projected.geometry.values, lake_buffered_projected.crs)
    except Exception as e:
        print(f"  [!] Failed to clip geometry: {e}")
        ds.close()
        return None

    # Generate a 2D boolean numpy mask for the original CORE area
    # invert=True means pixels INSIDE the lake core are True, outside are False
    core_mask = rasterio.features.geometry_mask(
        geometries=lake_core_projected.geometry.values,
        out_shape=(ds_clipped.rio.height, ds_clipped.rio.width),
        transform=ds_clipped.rio.transform(),
        all_touched=False,
        invert=True
    )
    
    # 3. Perform cloud validation strictly over the CORE lake body
    scl_core = ds_clipped.SCL.where(core_mask)
    core_total_pixels = int(scl_core.notnull().sum().values)
    
    if core_total_pixels == 0:
        ds.close()
        ds_clipped.close()
        return np.nan
        
    core_invalid_pixels = int(scl_core.isin(SCL_INVALID).sum().values)
    core_clear_pixels = core_total_pixels - core_invalid_pixels
    
    # Validate using YOUR Tile-Edge logic (expected_core_pixels)
    valid_ratio = core_clear_pixels / expected_core_pixels
    
    if valid_ratio < VALID_PIXEL_THRESHOLD:
        print(f"  [!] Scene rejected: Core cloud cover too high ({valid_ratio:.2f} < {VALID_PIXEL_THRESHOLD})")
        ds.close()
        ds_clipped.close()
        return np.nan

    print(f"  -> Scene passed! (Valid ratio: {valid_ratio:.2f}). Processing MNDWI over buffer...")
    # 4. Process MNDWI over the ENTIRE BUFFER to catch expanded shorelines
    b03 = ds_clipped.B03.astype('float32')
    b11 = ds_clipped.B11.astype('float32')
    mndwi = (b03 - b11) / (b03 + b11 + 1e-8)
    
    buffer_valid_mask = ~ds_clipped.SCL.isin(SCL_INVALID)
    mndwi_masked = mndwi.where(buffer_valid_mask)
    
    # Detect water anywhere inside the expanded buffer zone
    water_in_buffer = mndwi_masked > 0.0
    visible_water_pixels_in_buffer = int(water_in_buffer.sum().values)
    
    # 5. Calculate fractional extrapolation using the CORE ratio
    core_water_pixels = int((water_in_buffer.where(core_mask) == True).sum().values)
    
    core_water_ratio = (core_water_pixels / core_clear_pixels) if core_clear_pixels > 0 else 0
    
    # Find all missing pixels in the core (due to clouds OR tile edges!)
    missing_core_pixels = expected_core_pixels - core_clear_pixels
    
    # Total = Every pixel of water we saw + our best guess for the missing core pixels
    estimated_water_pixels = visible_water_pixels_in_buffer + (missing_core_pixels * core_water_ratio)
    
    pixel_size_m2 = 10 * 10
    area_km2 = (estimated_water_pixels * pixel_size_m2) / 1_000_000
    
    ds.close()
    ds_clipped.close()
    return area_km2




def process_month(year, month, lake, catalog):
    """Worker function to process a single month for a lake."""
    start_date = f"{year}-{month:02d}-01"
    end_date = pd.Timestamp(start_date) + pd.offsets.MonthEnd(0)
    end_date_str = end_date.strftime("%Y-%m-%d")
    
    bbox = lake.geometry.bounds
    best_scene = get_best_scene(catalog, bbox, start_date, end_date_str)
    
    if best_scene:
        water_area = process_scene(best_scene, lake)
        if water_area is not None:
            return {
                'hylak_id': lake['Hylak_id'],
                'name': lake['Lake_name'],
                'country': lake['Country'],
                'date': pd.to_datetime(best_scene.datetime.date()),
                'water_area_km2': water_area
            }
    # If no scene is found, or if process_scene fails, we still MUST return a row.
    # We use the 15th of the month as a safe placeholder date for the time series.
    return {
        'hylak_id': lake['Hylak_id'],
        'name': lake['Lake_name'],
        'country': lake['Country'],
        'date': pd.Timestamp(year, month, 15),
        'water_area_km2': np.nan
    }


def main():
    print("🌊 Starting AquaStress Historical Backfill Pipeline...")
    
    print("Connecting to Planetary Computer...")
    catalog = Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )
    
    vector_path = r"data\eu_water_surfaces.parquet"
    lakes_gdf = gpd.read_parquet(vector_path)
    
    checkpoint_file = r"data\checkpoint.csv"
    
    # 1. Load Checkpoint (if it exists)
    processed_lake_ids = set()
    if os.path.exists(checkpoint_file):
        df_checkpoint = pd.read_csv(checkpoint_file)
        if not df_checkpoint.empty:
            processed_lake_ids = set(df_checkpoint['hylak_id'].unique())
        print(f"🔄 Resuming from checkpoint. {len(processed_lake_ids)} lakes already processed.")
    else:
        # Create an empty CSV with headers so we can append to it later
        pd.DataFrame(columns=['hylak_id', 'name', 'country', 'date', 'water_area_km2']).to_csv(checkpoint_file, index=False)
        print("🆕 Created new checkpoint file.")
    years = range(2018, 2027) 
    months = range(1, 13)     
    
    # Generate a flat list of all 108 (year, month) combinations
    time_periods = [(y, m) for y in years for m in months]
    
    # 2. Iterate through Lakes
    for _, lake in lakes_gdf.iterrows():
        lake_id = lake['Hylak_id']
        
        # Checkpoint Check: Skip if already processed!
        if lake_id in processed_lake_ids:
            continue
            
        print(f"\n🌊 Processing Lake: {lake['Lake_name']} (ID: {lake_id}) [108 periods concurrently]")
        
        lake_results = []
        
        # 3. Parallel Processing (The Magic!)
        # We use 12 workers to process a whole year of months simultaneously
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            # Submit all 108 tasks to the thread pool
            future_to_period = {
                executor.submit(process_month, year, month, lake, catalog): (year, month) 
                for year, month in time_periods
            }
            
            # As each task completes, collect the result
            for future in concurrent.futures.as_completed(future_to_period):
                result = future.result()
                if result:
                    lake_results.append(result)
        
        # 4. Save Checkpoint per Lake
        if lake_results:
            df_lake = pd.DataFrame(lake_results)
            # Append to CSV (mode='a', header=False)
            df_lake.to_csv(checkpoint_file, mode='a', header=False, index=False)
            print(f"  ✅ Saved {len(lake_results)} valid records to checkpoint.")
        else:
            print(f"  ⚠️ No valid data found for this lake.")
            
        # Add to our set so we don't process it again if we restart
        processed_lake_ids.add(lake_id)
        gc.collect()
    # 5. Final Compilation
    # Once the loop finishes entirely, we read the CSV to build the Parquet
    print("\n✅ All lakes processed. Building final Parquet file...")
    final_df = pd.read_csv(checkpoint_file)
    
    max_areas = final_df.groupby('hylak_id')['water_area_km2'].max().reset_index()
    max_areas = max_areas.rename(columns={'water_area_km2': 'max_area'})
    
    final_df = final_df.merge(max_areas, on='hylak_id')
    final_df['water_percent'] = (final_df['water_area_km2'] / final_df['max_area']) * 100
    final_df = final_df.drop(columns=['max_area'])
    
    # Sort strictly for DuckDB efficiency
    final_df = final_df.sort_values(by=['hylak_id', 'date']).reset_index(drop=True)

    # Apply Anomaly Filter (masking sudden drops below our threshold)
    final_df.loc[final_df['water_percent'] < MIN_WATER_PERCENT_THRESHOLD, ['water_area_km2', 'water_percent']] = np.nan
    # Convert 'date' from string to Datetime
    final_df['date'] = pd.to_datetime(final_df['date'])
    
    print("  -> Interpolating missing data points safely...")
    # Define a helper function to interpolate one lake at a time in isolation
    def interpolate_lake(lake_group):
        # 1. Set the date index ONLY for this specific lake
        lake_group = lake_group.set_index('date')
        
        # 2. Interpolate using time, and force it to fill leading/trailing NaNs
        lake_group[['water_area_km2', 'water_percent']] = lake_group[['water_area_km2', 'water_percent']].interpolate(
            method='time', 
            limit_direction='both'
        )
        
        # 3. Reset the index so 'date' is a column again, and return
        return lake_group.reset_index()
    # Apply the safe function to each lake, and drop the messy multi-index Pandas creates
    final_df = final_df.groupby('hylak_id', group_keys=False).apply(interpolate_lake).reset_index(drop=True)
    output_path = r"data\water_trends_history.parquet"
    final_df.to_parquet(output_path)

    print(f"\n🎉 Successfully saved historical backfill to {output_path}")

if __name__ == "__main__":
    main()
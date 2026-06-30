import geopandas as gpd
import pandas as pd
from pystac_client import Client
import planetary_computer
import odc.stac
import rioxarray
import numpy as np
import os
import concurrent.futures
import time

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
                sleep_time = 2 ** attempt  # Wait 1s, then 2s, then 4s...
                print(f"    [!] API/Network error ({type(e).__name__}). Retrying in {sleep_time}s...")
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

    print("  -> Calculating MNDWI...")
    b03 = ds.B03.astype(float)
    b11 = ds.B11.astype(float)
    scl = ds.SCL
    
    valid_mask = ~scl.isin([3, 8, 9, 10])
    mndwi = (b03 - b11) / (b03 + b11 + 1e-8)
    mndwi_masked = mndwi.where(valid_mask)

    print("  -> Projecting and clipping...")
    lake_gdf = gpd.GeoDataFrame([lake_row], crs="EPSG:4326")
    lake_projected = lake_gdf.to_crs(ds.rio.crs)
    
    mndwi_masked.rio.write_crs(ds.rio.crs, inplace=True)
    
    try:
        mndwi_clipped = mndwi_masked.rio.clip(lake_projected.geometry.values, lake_projected.crs)
        print("  -> Clipping finished successfully!")
    except Exception as e:
        print(f"  [!] Failed to clip geometry (might be out of bounds): {e}")
        return None

    print("  -> Calculating final area...")
    water_clipped = mndwi_clipped > 0.0
    total_water_pixels = int(water_clipped.sum().values)
    
    pixel_size_m2 = 10 * 10
    area_km2 = (total_water_pixels * pixel_size_m2) / 1_000_000
    
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
    return None



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
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
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
    
    output_path = r"data\water_trends_history.parquet"
    final_df.to_parquet(output_path)
    print(f"\n🎉 Successfully saved historical backfill to {output_path}")
if __name__ == "__main__":
    main()
import os
import pandas as pd
import geopandas as gpd
from pystac_client import Client
import planetary_computer
import concurrent.futures
import numpy as np

from backfill import process_month, MIN_WATER_PERCENT_THRESHOLD

def get_next_processing_month():
    print("🌍 Reading historical data locally...")
    
    file_path = "data/water_trends_history.parquet"
    
    try:
        df_history = pd.read_parquet(file_path)
    except Exception as e:
        print(f"❌ Failed to load local history: {e}")
        return None, None
        
    # Clean future dates that might have been added by the historical backfill
    today = pd.to_datetime('today')
    df_history['date'] = pd.to_datetime(df_history['date'])
    df_history = df_history[df_history['date'] <= today].copy()

    # Find the most recent valid date in your dataset
    max_date = df_history['date'].max()
    print(f"📊 Latest data is from: {max_date.date()}")
    
    # Calculate the target month using pandas offsets
    target_month = max_date + pd.offsets.MonthBegin(1)
    
    # Ensure we ONLY process months that have completely finished
    current_month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if target_month >= current_month_start:
        print(f"⚠️ Target month ({target_month.strftime('%Y-%m')}) is the current ongoing month.")
        print("Waiting until the month is fully over before processing to ensure complete satellite coverage.")
        return None, None
        
    print(f"🎯 Target month for new data: {target_month.strftime('%Y-%m')}")
    
    return df_history, target_month


def main():
    df_history, target_month = get_next_processing_month()
    
    # If there's no new month to process (or R2 failed), exit early
    if df_history is None or target_month is None:
        return
        
    print("\n🚀 Connecting to Planetary Computer...")
    catalog = Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )
    
    print("📂 Loading vector base layer...")
    # Use forward-slashes for paths so it works in Linux/Docker!
    vector_path = "data/eu_water_surfaces.parquet"
    lakes_gdf = gpd.read_parquet(vector_path)
    
    print(f"🌊 Processing {len(lakes_gdf)} lakes for {target_month.strftime('%B %Y')}...")
    
    new_records = []
    # We use ThreadPoolExecutor just like you did in backfill.py
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # We only submit ONE month per lake this time
        future_to_lake = {
            executor.submit(process_month, target_month.year, target_month.month, lake, catalog): lake
            for _, lake in lakes_gdf.iterrows()
        }
        
        for future in concurrent.futures.as_completed(future_to_lake):
            result = future.result()
            if result:
                new_records.append(result)
                
    if not new_records:
        print("⚠️ No valid satellite scenes found for this month.")
        return
        
    print(f"✅ Successfully processed {len(new_records)} lakes. Appending to history...")
    df_new = pd.DataFrame(new_records)
    
    # Combine historical and new data (drop old water_percent to recalculate it)
    df_combined = pd.concat([df_history.drop(columns=['water_percent'], errors='ignore'), df_new], ignore_index=True)
    
    print("📈 Recalculating historical maximums and percentages...")
    # We must recalculate the all-time max area in case this new month was a record high!
    max_areas = df_combined.groupby('hylak_id')['water_area_km2'].max().reset_index()
    max_areas = max_areas.rename(columns={'water_area_km2': 'max_area'})
    
    df_combined = df_combined.merge(max_areas, on='hylak_id')
    df_combined['water_percent'] = (df_combined['water_area_km2'] / df_combined['max_area']) * 100
    df_combined = df_combined.drop(columns=['max_area'])
    
    print("🧹 Sorting data for DuckDB optimization...")
    # Critical: Keep the primary and secondary sort keys intact
    df_combined = df_combined.sort_values(by=['hylak_id', 'date']).reset_index(drop=True)
    
    print("🩹 Applying Anomaly Filter and Interpolation (matching backfill logic)...")
    df_combined.loc[df_combined['water_percent'] < MIN_WATER_PERCENT_THRESHOLD, ['water_area_km2', 'water_percent']] = np.nan
    df_combined['date'] = pd.to_datetime(df_combined['date'])
    
    df_combined = df_combined.set_index('date')
    df_combined['water_area_km2'] = df_combined.groupby('hylak_id')['water_area_km2'].transform(
        lambda x: x.interpolate(method='time', limit_direction='both')
    )
    df_combined['water_percent'] = df_combined.groupby('hylak_id')['water_percent'].transform(
        lambda x: x.interpolate(method='time', limit_direction='both')
    )
    df_combined = df_combined.reset_index()
    
    print("☁️ Saving updated dataset locally...")
    file_path = "data/water_trends_history.parquet"
    df_combined.to_parquet(file_path)
    print(f"🎉 Incremental update complete! Saved to {file_path}")

    print("\n🌐 Automatically preparing updated data for the web...")
    from prepare_web_data import clean_and_prepare_for_web
    web_file_path = "data/water_trends_history_web.parquet"
    clean_and_prepare_for_web(file_path, web_file_path, window_size=3)
    print("🚀 Pipeline fully complete! Web data is refreshed.")

if __name__ == "__main__":
    main()

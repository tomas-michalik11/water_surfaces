import os
import pandas as pd
import geopandas as gpd
from pystac_client import Client
import planetary_computer
import concurrent.futures

from backfill import process_month

def get_next_processing_month():
    print("🌍 Connecting to Cloudflare R2 to read historical data...")
    
    # We will pass the bucket name as an environment variable in GitHub Actions
    bucket_name = os.environ.get("R2_BUCKET_NAME", "your-bucket-name-here")
    file_path = f"s3://{bucket_name}/water_trends_history.parquet"
    
    # pandas uses s3fs under the hood. It automatically looks for standard
    # S3 credentials in your environment variables: 
    # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and AWS_ENDPOINT_URL_S3
    try:
        df_history = pd.read_parquet(file_path)
    except Exception as e:
        print(f"❌ Failed to load from R2: {e}")
        return None, None
        
    # Find the most recent date in your dataset
    max_date = pd.to_datetime(df_history['date']).max()
    print(f"📊 Latest data in R2 is from: {max_date.date()}")
    
    # Calculate the target month using pandas offsets
    target_month = max_date + pd.offsets.MonthBegin(1)
    
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
    
    print("☁️ Uploading updated dataset back to Cloudflare R2...")
    bucket_name = os.environ.get("R2_BUCKET_NAME", "your-bucket-name-here")
    df_combined.to_parquet(f"s3://{bucket_name}/water_trends_history.parquet")
    print("🎉 Incremental update complete!")

if __name__ == "__main__":
    main()


import pandas as pd

def clean_and_prepare_for_web(input_parquet, output_parquet, window_size=3):
    print("Loading raw data...")
    df = pd.read_parquet(input_parquet)
    
    # 1. Clean Future Dates
    today = pd.to_datetime('today')
    df_clean = df[df['date'] <= today].copy()
    
    # 2. Clean NaN Values
    df_clean = df_clean.dropna(subset=['water_area_km2'])
    
    # 3. Determine the grouping column
    # We strongly prefer a unique ID. If it's missing, we fallback to name, 
    # but be aware this will corrupt data for lakes with missing names!
    if 'hylak_id' in df_clean.columns:
        group_col = 'hylak_id'
    elif 'id' in df_clean.columns:
        group_col = 'id'
    else:
        print("\n[!] WARNING: No unique ID column found (like 'Hylak_id').")
        print("[!] Grouping by 'name', which has many missing values. Your smoothed data for unnamed lakes will be mixed together!\n")
        group_col = 'name'
        
    # 4. Sort the data! 
    # Must sort by Lake first, then chronological Date so the rolling math works sequentially
    print(f"Sorting and calculating rolling average grouped by: {group_col}")
    df_clean = df_clean.sort_values(by=[group_col, 'date'])


    # Let's say a sudden drop of more than 30% that immediately recovers is impossible.
    DROP_THRESHOLD = 30.0 
    
    # 1. Get the previous and next month's values. 
    # CRITICAL: We must use groupby(group_col) so we don't compare Lake A with Lake B!
    prev_val = df_clean.groupby(group_col)['water_percent'].shift(1)
    next_val = df_clean.groupby(group_col)['water_percent'].shift(-1)
    curr_val = df_clean['water_percent']
    
    # 2. Find the anomalies (Current is much lower than BOTH previous and next)
    is_anomaly = (prev_val - curr_val > DROP_THRESHOLD) & (next_val - curr_val > DROP_THRESHOLD)
    
    anomaly_count = is_anomaly.sum()
    print(f"🛠️ Found and fixing {anomaly_count} impossible spikes/drops...")
    
    # 3. Replace those impossible drops with NaN (blank)
    df_clean.loc[is_anomaly, 'water_percent'] = pd.NA
    df_clean.loc[is_anomaly, 'water_area_km2'] = pd.NA
    
    # 4. Interpolate! This draws a straight line between the previous and next month
    # so the data looks perfectly natural again.
    df_clean['water_percent'] = df_clean.groupby(group_col)['water_percent'].transform(lambda x: x.interpolate())
    df_clean['water_area_km2'] = df_clean.groupby(group_col)['water_area_km2'].transform(lambda x: x.interpolate())
    
    # 5. Calculate the Rolling Average
    # Using 'min_periods=1' ensures the first couple of dates don't become NaN 
    # while waiting for enough data to fill the window.
    df_clean['smoothed_area_km2'] = df_clean.groupby(group_col)['water_area_km2'].transform(
        lambda x: x.rolling(window=window_size, min_periods=1).mean()
    )
    
    # Optional: Round the floats to 2 decimal places to make the web app payload smaller
    df_clean['water_area_km2'] = df_clean['water_area_km2'].round(2)
    df_clean['smoothed_area_km2'] = df_clean['smoothed_area_km2'].round(2)
    df_clean['water_percent'] = df_clean['water_percent'].round(2)
    
    # 6. Save the final file
    df_clean.to_parquet(output_parquet, index=False)
    print(f"Success! Cleaned and smoothed dataset saved to: {output_parquet}")



# --- How to run it ---
if __name__ == "__main__":
    input_file = 'data/water_trends_history.parquet'   # Replace with your actual file name
    output_file = 'data/water_trends_history_web.parquet'
    
    # You can change window_size to 6 for a 6-month smoothing, etc.
    clean_and_prepare_for_web(input_file, output_file, window_size=3)

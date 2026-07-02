import pandas as pd

print("Loading checkpoint...")
df = pd.read_csv(r"data\checkpoint.csv")

# Count how many valid (non-NaN) data points each lake has
valid_counts = df.groupby('hylak_id')['water_area_km2'].count()

# Find the lakes that have exactly 0 valid points (they failed every month)
failed_lakes = valid_counts[valid_counts == 0].index.tolist()
print(f"Found {len(failed_lakes)} massive lakes that failed and need mosaicing.")

# Remove those failed lakes from the checkpoint
df_clean = df[~df['hylak_id'].isin(failed_lakes)]

# Save the checkpoint back
df_clean.to_csv(r"data\checkpoint.csv", index=False)
print("Checkpoint cleaned! You are ready to run backfill.py.")

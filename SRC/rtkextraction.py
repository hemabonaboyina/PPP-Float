import re
import pandas as pd

# Input file
pos_file = "IISC00IND_R_20260380000_01D_30S_MO2.pos"

# Store extracted data
data = []

with open(pos_file, 'r') as f:
    for line in f:

        # Skip comments/header lines
        if line.startswith('%') or len(line.strip()) == 0:
            continue

        parts = line.split()

        try:
            # Example RTKLIB POS format:
            # date time x y z ...

            date_str = parts[0]
            time_str = parts[1]

            x = float(parts[2])
            y = float(parts[3])
            z = float(parts[4])

            # Convert HH:MM:SS to seconds of day
            h, m, s = time_str.split(':')

            seconds_of_day = (
                int(h) * 3600 +
                int(m) * 60 +
                float(s)
            )

            data.append([
                seconds_of_day,
                x,
                y,
                z
            ])

        except:
            # Ignore malformed lines
            continue

# Create dataframe
df = pd.DataFrame(
    data,
    columns=[
        "Seconds_of_Day",
        "X_ECEF(m)",
        "Y_ECEF(m)",
        "Z_ECEF(m)"
    ]
)

# Save to CSV
output_file = "ecef_output.csv"
df.to_csv(output_file, index=False)

print(f"Extracted data saved to: {output_file}")
print(df.head())
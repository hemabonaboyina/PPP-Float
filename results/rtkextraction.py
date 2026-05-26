import pandas as pd

# =====================================================
# INPUT RTKLIB POS FILE
# =====================================================

pos_file = "IISC00IND_R_20260380000_01D_30S_MO2.pos"

# =====================================================
# STORE EXTRACTED DATA
# =====================================================

data = []

# =====================================================
# READ RTKLIB POS FILE
# =====================================================

with open(pos_file, 'r') as f:

    for line in f:

        # Skip header/comments
        if line.startswith('%') or len(line.strip()) == 0:
            continue

        parts = line.split()

        try:

            # =================================================
            # RTKLIB POS FORMAT
            # date time x y z ...
            # =================================================

            date_str = parts[0]
            time_str = parts[1]

            x = float(parts[2])
            y = float(parts[3])
            z = float(parts[4])

            # =================================================
            # CONVERT HH:MM:SS -> SECONDS OF DAY
            # =================================================

            h, m, s = time_str.split(':')

            seconds_of_day = (

                int(h) * 3600 +
                int(m) * 60 +
                float(s)

            )

            # =================================================
            # STORE VALUES
            # =================================================

            data.append([

                date_str,
                time_str,
                seconds_of_day,

                x,
                y,
                z

            ])

        except:

            # Ignore malformed lines
            continue

# =====================================================
# CREATE DATAFRAME
# =====================================================

df = pd.DataFrame(

    data,

    columns=[

        "Date",
        "Time",
        "Seconds_of_Day",

        "X_ECEF(m)",
        "Y_ECEF(m)",
        "Z_ECEF(m)"

    ]

)

# =====================================================
# SORT BY TIME
# =====================================================

df = df.sort_values(
    by="Seconds_of_Day"
)

# =====================================================
# RESET INDEX
# =====================================================

df = df.reset_index(drop=True)

# =====================================================
# EXPECTED EPOCHS
# =====================================================

expected_epochs = 2880

available_epochs = len(df)

missing_epochs = (
    expected_epochs -
    available_epochs
)

# =====================================================
# PRINT SUMMARY
# =====================================================

print("\n===================================")
print("      RTKLIB EXTRACTION SUMMARY")
print("===================================\n")

print(f"Expected Epochs : {expected_epochs}")
print(f"Available Epochs: {available_epochs}")
print(f"Missing Epochs  : {missing_epochs}")

availability = (
    available_epochs /
    expected_epochs
) * 100.0

print(f"Availability    : {availability:.2f}%")

print("\n===================================\n")

# =====================================================
# DETECT MISSING EPOCHS
# =====================================================

all_epochs = set(range(0, 86400, 30))

available_sod = set(
    df["Seconds_of_Day"].astype(int)
)

missing_sod = sorted(
    all_epochs - available_sod
)

# =====================================================
# PRINT FIRST FEW MISSING TIMES
# =====================================================

if len(missing_sod) > 0:

    print("First Missing Epochs:\n")

    for s in missing_sod[:20]:

        hh = s // 3600
        mm = (s % 3600) // 60
        ss = s % 60

        print(
            f"{hh:02d}:{mm:02d}:{ss:02d}"
        )

    print("\n...")

# =====================================================
# SAVE OUTPUT CSV
# =====================================================

output_file = "ecef_output.csv"

df.to_csv(
    output_file,
    index=False
)

# =====================================================
# FINAL OUTPUT
# =====================================================

print(f"\nExtracted RTKLIB data saved to:")
print(output_file)

print("\nFirst Rows:\n")

print(df.head())
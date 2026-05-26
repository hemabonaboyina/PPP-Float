import pandas as pd

input_file = r"D:\PROJECT\data\gLAB.out"

rows = []

with open(input_file, "r", errors="ignore") as f:

    for line in f:

        if line.startswith("OUTPUT"):

            parts = line.split()

            try:
                sod = float(parts[3])

                x = float(parts[11])
                y = float(parts[12])
                z = float(parts[13])

                rows.append([sod, x, y, z])

            except:
                continue

df = pd.DataFrame(rows, columns=["SOD", "X", "Y", "Z"])

df.to_csv("glab_xyz.csv", index=False)

print(df.head())
print(f"\nTotal epochs: {len(df)}")
print("Saved: glab_xyz.csv")
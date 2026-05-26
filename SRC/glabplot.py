import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ==========================================
# Load data
# ==========================================
glab = pd.read_csv("glab_xyz.csv")
ppp  = pd.read_csv("ppp_results_gps_rtscompare.csv")

# ==========================================
# Merge by SOD
# ==========================================
df = pd.merge(glab, ppp, on="SOD")

# ==========================================
# Time axis (hours)
# ==========================================
df["Hour"] = df["SOD"] / 3600.0

# ==========================================
# Delta coordinates (cm)
# ==========================================
df["dX_cm"] = (df["Computed_X"] - df["X"]) * 100
df["dY_cm"] = (df["Computed_Y"] - df["Y"]) * 100
df["dZ_cm"] = (df["Computed_Z"] - df["Z"]) * 100

# ==========================================
# 3D Difference (cm)
# ==========================================
df["3D_cm"] = np.sqrt(
    df["dX_cm"]**2 +
    df["dY_cm"]**2 +
    df["dZ_cm"]**2
)

# ==========================================
# RMS
# ==========================================
rms_3d = np.sqrt(np.mean(df["3D_cm"]**2))

print(f"\n3D RMS Difference = {rms_3d:.3f} cm")

# =========================================================
# X COMPONENT COMPARISON
# =========================================================
plt.figure(figsize=(12,5))

plt.plot(df["Hour"], df["X"], label="gLAB X")
plt.plot(df["Hour"], df["Computed_X"], label="Python PPP X")

plt.xlabel("Time (Hours)")
plt.ylabel("X Coordinate (m)")
plt.title("X Component Comparison")
plt.legend()
plt.grid(True)

plt.tight_layout()
plt.show()

# =========================================================
# Y COMPONENT COMPARISON
# =========================================================
plt.figure(figsize=(12,5))

plt.plot(df["Hour"], df["Y"], label="gLAB Y")
plt.plot(df["Hour"], df["Computed_Y"], label="Python PPP Y")

plt.xlabel("Time (Hours)")
plt.ylabel("Y Coordinate (m)")
plt.title("Y Component Comparison")
plt.legend()
plt.grid(True)

plt.tight_layout()
plt.show()

# =========================================================
# Z COMPONENT COMPARISON
# =========================================================
plt.figure(figsize=(12,5))

plt.plot(df["Hour"], df["Z"], label="gLAB Z")
plt.plot(df["Hour"], df["Computed_Z"], label="Python PPP Z")

plt.xlabel("Time (Hours)")
plt.ylabel("Z Coordinate (m)")
plt.title("Z Component Comparison")
plt.legend()
plt.grid(True)

plt.tight_layout()
plt.show()

# =========================================================
# DELTA X
# =========================================================
plt.figure(figsize=(12,5))

plt.plot(df["Hour"], df["dX_cm"])

plt.xlabel("Time (Hours)")
plt.ylabel("ΔX (cm)")
plt.title("ΔX : Python PPP - gLAB")
plt.grid(True)

plt.tight_layout()
plt.show()

# =========================================================
# DELTA Y
# =========================================================
plt.figure(figsize=(12,5))

plt.plot(df["Hour"], df["dY_cm"])

plt.xlabel("Time (Hours)")
plt.ylabel("ΔY (cm)")
plt.title("ΔY : Python PPP - gLAB")
plt.grid(True)

plt.tight_layout()
plt.show()

# =========================================================
# DELTA Z
# =========================================================
plt.figure(figsize=(12,5))

plt.plot(df["Hour"], df["dZ_cm"])

plt.xlabel("Time (Hours)")
plt.ylabel("ΔZ (cm)")
plt.title("ΔZ : Python PPP - gLAB")
plt.grid(True)

plt.tight_layout()
plt.show()

# =========================================================
# 3D ERROR
# =========================================================
plt.figure(figsize=(12,5))

plt.plot(df["Hour"], df["3D_cm"])

plt.xlabel("Time (Hours)")
plt.ylabel("3D Difference (cm)")
plt.title("3D Difference : Python PPP vs gLAB")
plt.grid(True)

plt.tight_layout()
plt.show()
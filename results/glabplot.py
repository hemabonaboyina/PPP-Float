import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# =========================================================
# JOURNAL-STYLE SETTINGS
# =========================================================
plt.rcParams.update({

    # Font
    "font.family": "serif",
    "font.size": 16,

    # Axis labels
    "axes.labelsize": 18,
    "axes.labelweight": "bold",

    # Tick labels
    "xtick.labelsize": 14,
    "ytick.labelsize": 14,

    # Legend
    "legend.fontsize": 14,

    # Line width
    "lines.linewidth": 2.2,

    # Grid
    "grid.linewidth": 0.6,

    # Figure quality
    "figure.dpi": 300,
    "savefig.dpi": 600
})

# =========================================================
# CREATE OUTPUT FOLDER
# =========================================================
os.makedirs("plots", exist_ok=True)

# =========================================================
# LOAD FILES
# =========================================================
glab = pd.read_csv("glab_xyz.csv")
ppp  = pd.read_csv("ppp_results_gps_rtscompare.csv")

# =========================================================
# MERGE USING COMMON SOD
# =========================================================
df = pd.merge(glab, ppp, on="SOD")

# =========================================================
# TIME AXIS (HOURS)
# =========================================================
df["Hour"] = df["SOD"] / 3600.0

# =========================================================
# HH:MM:SS COLUMN FOR CSV ONLY
# =========================================================
df["HHMMSS"] = pd.to_datetime(
    df["SOD"],
    unit="s"
).dt.strftime("%H:%M:%S")

# =========================================================
# DELTA COORDINATES (cm)
# Python PPP - gLAB PPP
# =========================================================
df["dX_cm"] = (df["Computed_X"] - df["X"]) * 100
df["dY_cm"] = (df["Computed_Y"] - df["Y"]) * 100
df["dZ_cm"] = (df["Computed_Z"] - df["Z"]) * 100

# =========================================================
# 3D DIFFERENCE (cm)
# =========================================================
df["3D_cm"] = np.sqrt(
    df["dX_cm"]**2 +
    df["dY_cm"]**2 +
    df["dZ_cm"]**2
)

# =========================================================
# MEAN-CENTERED COORDINATES (cm)
# =========================================================

# gLAB means
xg_mean = df["X"].mean()
yg_mean = df["Y"].mean()
zg_mean = df["Z"].mean()

# Python PPP means
xp_mean = df["Computed_X"].mean()
yp_mean = df["Computed_Y"].mean()
zp_mean = df["Computed_Z"].mean()

# Mean-centered coordinates
df["X_glab_cm"] = (df["X"] - xg_mean) * 100
df["Y_glab_cm"] = (df["Y"] - yg_mean) * 100
df["Z_glab_cm"] = (df["Z"] - zg_mean) * 100

df["X_python_cm"] = (df["Computed_X"] - xp_mean) * 100
df["Y_python_cm"] = (df["Computed_Y"] - yp_mean) * 100
df["Z_python_cm"] = (df["Computed_Z"] - zp_mean) * 100

# =========================================================
# RMS STATISTICS
# =========================================================
rms_dx = np.sqrt(np.mean(df["dX_cm"]**2))
rms_dy = np.sqrt(np.mean(df["dY_cm"]**2))
rms_dz = np.sqrt(np.mean(df["dZ_cm"]**2))
rms_3d = np.sqrt(np.mean(df["3D_cm"]**2))

print("\n==============================")
print("PPP vs gLAB COMPARISON")
print("==============================")
print(f"RMS dX  = {rms_dx:.3f} cm")
print(f"RMS dY  = {rms_dy:.3f} cm")
print(f"RMS dZ  = {rms_dz:.3f} cm")
print(f"RMS 3D  = {rms_3d:.3f} cm")

# =========================================================
# SAVE CSV
# =========================================================
comparison_csv = df[[

    "SOD",
    "HHMMSS",

    # gLAB
    "X",
    "Y",
    "Z",

    # Python PPP
    "Computed_X",
    "Computed_Y",
    "Computed_Z",

    # Mean-centered comparison
    "X_glab_cm",
    "Y_glab_cm",
    "Z_glab_cm",

    "X_python_cm",
    "Y_python_cm",
    "Z_python_cm",

    # Differences
    "dX_cm",
    "dY_cm",
    "dZ_cm",

    # 3D
    "3D_cm"
]]

comparison_csv.to_csv(
    "PPP_gLAB_comparison.csv",
    index=False
)

print("\nSaved: PPP_gLAB_comparison.csv")

# =========================================================
# COMMON X TICKS
# =========================================================
xticks = range(0, 25, 4)

# =========================================================
# JOURNAL PLOT STYLING
# =========================================================
def style_plot():

    plt.xticks(xticks)

    plt.grid(
        True,
        linestyle='--',
        alpha=0.6
    )

    plt.tick_params(
        axis='both',
        which='major',
        width=1.5,
        length=6
    )

    ax = plt.gca()

    for spine in ax.spines.values():
        spine.set_linewidth(1.5)

# =========================================================
# X COMPONENT COMPARISON
# =========================================================
plt.figure(figsize=(10,4.5))

plt.plot(
    df["Hour"],
    df["X_glab_cm"],
    label="gLAB"
)

plt.plot(
    df["Hour"],
    df["X_python_cm"],
    label="Python PPP"
)

plt.xlabel("Time (Hours)")
plt.ylabel("ΔX (cm)")

style_plot()

plt.legend(
    frameon=True,
    edgecolor='black'
)

plt.tight_layout()

plt.savefig(
    "plots/X_comparison.png",
    bbox_inches='tight'
)

plt.show()

# =========================================================
# Y COMPONENT COMPARISON
# =========================================================
plt.figure(figsize=(10,4.5))

plt.plot(
    df["Hour"],
    df["Y_glab_cm"],
    label="gLAB"
)

plt.plot(
    df["Hour"],
    df["Y_python_cm"],
    label="Python PPP"
)

plt.xlabel("Time (Hours)")
plt.ylabel("ΔY (cm)")

style_plot()

plt.legend(
    frameon=True,
    edgecolor='black'
)

plt.tight_layout()

plt.savefig(
    "plots/Y_comparison.png",
    bbox_inches='tight'
)

plt.show()

# =========================================================
# Z COMPONENT COMPARISON
# =========================================================
plt.figure(figsize=(10,4.5))

plt.plot(
    df["Hour"],
    df["Z_glab_cm"],
    label="gLAB"
)

plt.plot(
    df["Hour"],
    df["Z_python_cm"],
    label="Python PPP"
)

plt.xlabel("Time (Hours)")
plt.ylabel("ΔZ (cm)")

style_plot()

plt.legend(
    frameon=True,
    edgecolor='black'
)

plt.tight_layout()

plt.savefig(
    "plots/Z_comparison.png",
    bbox_inches='tight'
)

plt.show()

# =========================================================
# DELTA X
# =========================================================
plt.figure(figsize=(10,4.5))

plt.plot(
    df["Hour"],
    df["dX_cm"]
)

plt.xlabel("Time (Hours)")
plt.ylabel("ΔX Difference (cm)")

style_plot()

plt.tight_layout()

plt.savefig(
    "plots/dX_difference.png",
    bbox_inches='tight'
)

plt.show()

# =========================================================
# DELTA Y
# =========================================================
plt.figure(figsize=(10,4.5))

plt.plot(
    df["Hour"],
    df["dY_cm"]
)

plt.xlabel("Time (Hours)")
plt.ylabel("ΔY Difference (cm)")

style_plot()

plt.tight_layout()

plt.savefig(
    "plots/dY_difference.png",
    bbox_inches='tight'
)

plt.show()

# =========================================================
# DELTA Z
# =========================================================
plt.figure(figsize=(10,4.5))

plt.plot(
    df["Hour"],
    df["dZ_cm"]
)

plt.xlabel("Time (Hours)")
plt.ylabel("ΔZ Difference (cm)")

style_plot()

plt.tight_layout()

plt.savefig(
    "plots/dZ_difference.png",
    bbox_inches='tight'
)

plt.show()

# =========================================================
# 3D DIFFERENCE
# =========================================================
plt.figure(figsize=(10,4.5))

plt.plot(
    df["Hour"],
    df["3D_cm"]
)

plt.xlabel("Time (Hours)")
plt.ylabel("3D Difference (cm)")

style_plot()

plt.tight_layout()

plt.savefig(
    "plots/3D_difference.png",
    bbox_inches='tight'
)

plt.show()

print("\nAll journal-style plots saved inside 'plots/' folder")
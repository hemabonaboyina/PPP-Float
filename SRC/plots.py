import pandas as pd
import matplotlib.pyplot as plt

# =====================================================
# FILE PATHS
# =====================================================

ecef_file = r"D:\PROJECT\src\ecef_output.csv"
ppp_file  = r"D:\PROJECT\src\ppp_results_gps_rtscompare.csv"

# =====================================================
# READ FILES
# =====================================================

ecef_df = pd.read_csv(ecef_file)
ppp_df  = pd.read_csv(ppp_file)

# =====================================================
# RENAME TIME COLUMN
# =====================================================

ecef_df.rename(
    columns={"Seconds_of_Day": "SOD"},
    inplace=True
)

# =====================================================
# MERGE USING COMMON EPOCHS
# =====================================================

merged = pd.merge(
    ecef_df,
    ppp_df,
    on="SOD",
    how="inner"
)

print("\nMerged Length:", len(merged))

# =====================================================
# TIME AXIS IN HOURS
# =====================================================

time_hours = merged["SOD"] / 3600.0

# =====================================================
# ECEF REFERENCE DATA
# =====================================================

ecef_x = merged["X_ECEF(m)"]
ecef_y = merged["Y_ECEF(m)"]
ecef_z = merged["Z_ECEF(m)"]

# =====================================================
# PPP COMPUTED DATA
# =====================================================

ppp_x = merged["Computed_X"]
ppp_y = merged["Computed_Y"]
ppp_z = merged["Computed_Z"]

# =====================================================
# COMPUTE DELTA VALUES IN CM
# =====================================================

merged["Delta_X_cm"] = (
    merged["Computed_X"] -
    merged["X_ECEF(m)"]
) * 100.0

merged["Delta_Y_cm"] = (
    merged["Computed_Y"] -
    merged["Y_ECEF(m)"]
) * 100.0

merged["Delta_Z_cm"] = (
    merged["Computed_Z"] -
    merged["Z_ECEF(m)"]
) * 100.0

# =====================================================
# COMPUTE 3D ERROR IN CM
# =====================================================

merged["3D_Error_cm"] = (
    merged["Delta_X_cm"]**2 +
    merged["Delta_Y_cm"]**2 +
    merged["Delta_Z_cm"]**2
) ** 0.5

# =====================================================
# SAVE FULL COMPARISON CSV
# =====================================================

comparison_csv = (
    r"D:\PROJECT\src\ppp_vs_rtk_comparison.csv"
)

comparison_df = merged[[

    "SOD",

    # RTKLIB reference coordinates
    "X_ECEF(m)",
    "Y_ECEF(m)",
    "Z_ECEF(m)",

    # Your PPP coordinates
    "Computed_X",
    "Computed_Y",
    "Computed_Z",

    # Errors
    "Delta_X_cm",
    "Delta_Y_cm",
    "Delta_Z_cm",

    # 3D Error
    "3D_Error_cm"

]]

comparison_df.rename(columns={

    "X_ECEF(m)": "RTKLIB_X_m",
    "Y_ECEF(m)": "RTKLIB_Y_m",
    "Z_ECEF(m)": "RTKLIB_Z_m",

    "Computed_X": "PPP_X_m",
    "Computed_Y": "PPP_Y_m",
    "Computed_Z": "PPP_Z_m",

    "Delta_X_cm": "PPP_minus_RTKLIB_X_cm",
    "Delta_Y_cm": "PPP_minus_RTKLIB_Y_cm",
    "Delta_Z_cm": "PPP_minus_RTKLIB_Z_cm"

}, inplace=True)

comparison_df.to_csv(
    comparison_csv,
    index=False
)

print(f"\nSaved CSV: {comparison_csv}")

# =====================================================
# X ECEF COMPARISON PLOT
# =====================================================

plt.figure(figsize=(12,5))

plt.plot(
    time_hours,
    ecef_x,
    label="RTKLIB X"
)

plt.plot(
    time_hours,
    ppp_x,
    label="PPP X"
)

plt.xlabel("Time (Hours)")
plt.ylabel("X Component (m)")
plt.title("X ECEF Comparison")

plt.grid(True)
plt.legend()

plt.xlim(0, 24)

plt.savefig(
    r"D:\PROJECT\src\x_component_plot.png",
    dpi=300
)

# =====================================================
# Y ECEF COMPARISON PLOT
# =====================================================

plt.figure(figsize=(12,5))

plt.plot(
    time_hours,
    ecef_y,
    label="RTKLIB Y"
)

plt.plot(
    time_hours,
    ppp_y,
    label="PPP Y"
)

plt.xlabel("Time (Hours)")
plt.ylabel("Y Component (m)")
plt.title("Y ECEF Comparison")

plt.grid(True)
plt.legend()

plt.xlim(0, 24)

plt.savefig(
    r"D:\PROJECT\src\y_component_plot.png",
    dpi=300
)

# =====================================================
# Z ECEF COMPARISON PLOT
# =====================================================

plt.figure(figsize=(12,5))

plt.plot(
    time_hours,
    ecef_z,
    label="RTKLIB Z"
)

plt.plot(
    time_hours,
    ppp_z,
    label="PPP Z"
)

plt.xlabel("Time (Hours)")
plt.ylabel("Z Component (m)")
plt.title("Z ECEF Comparison")

plt.grid(True)
plt.legend()

plt.xlim(0, 24)

plt.savefig(
    r"D:\PROJECT\src\z_component_plot.png",
    dpi=300
)

# =====================================================
# DELTA X ERROR PLOT
# =====================================================

plt.figure(figsize=(12,5))

plt.plot(
    time_hours,
    merged["Delta_X_cm"]
)

plt.xlabel("Time (Hours)")
plt.ylabel("Delta X (cm)")
plt.title("PPP - RTKLIB : Delta X")

plt.grid(True)

plt.xlim(0, 24)

plt.savefig(
    r"D:\PROJECT\src\delta_x_cm_plot.png",
    dpi=300
)

# =====================================================
# DELTA Y ERROR PLOT
# =====================================================

plt.figure(figsize=(12,5))

plt.plot(
    time_hours,
    merged["Delta_Y_cm"]
)

plt.xlabel("Time (Hours)")
plt.ylabel("Delta Y (cm)")
plt.title("PPP - RTKLIB : Delta Y")

plt.grid(True)

plt.xlim(0, 24)

plt.savefig(
    r"D:\PROJECT\src\delta_y_cm_plot.png",
    dpi=300
)

# =====================================================
# DELTA Z ERROR PLOT
# =====================================================

plt.figure(figsize=(12,5))

plt.plot(
    time_hours,
    merged["Delta_Z_cm"]
)

plt.xlabel("Time (Hours)")
plt.ylabel("Delta Z (cm)")
plt.title("PPP - RTKLIB : Delta Z")

plt.grid(True)

plt.xlim(0, 24)

plt.savefig(
    r"D:\PROJECT\src\delta_z_cm_plot.png",
    dpi=300
)

# =====================================================
# 3D ERROR PLOT
# =====================================================

plt.figure(figsize=(12,5))

plt.plot(
    time_hours,
    merged["3D_Error_cm"]
)

plt.xlabel("Time (Hours)")
plt.ylabel("3D Error (cm)")
plt.title("3D Position Error")

plt.grid(True)

plt.xlim(0, 24)

plt.savefig(
    r"D:\PROJECT\src\3d_error_cm_plot.png",
    dpi=300
)

# =====================================================
# SHOW ALL PLOTS
# =====================================================

plt.show()

# =====================================================
# OUTPUT FILES
# =====================================================

print("\nSaved Files:")
print("ppp_vs_rtk_comparison.csv")
print("x_component_plot.png")
print("y_component_plot.png")
print("z_component_plot.png")
print("delta_x_cm_plot.png")
print("delta_y_cm_plot.png")
print("delta_z_cm_plot.png")
print("3d_error_cm_plot.png")
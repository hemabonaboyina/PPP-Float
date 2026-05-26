import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# =====================================================
# GLOBAL JOURNAL STYLE
# =====================================================

plt.rcParams['font.family'] = 'serif'
plt.rcParams['font.size'] = 12

# =====================================================
# FILE PATHS
# =====================================================

ecef_file = r"D:\PROJECT\results\ecef_output.csv"
ppp_file  = r"D:\PROJECT\results\ppp_results_gps_rtscompare.csv"

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
# MERGE COMMON EPOCHS
# =====================================================

merged = pd.merge(
    ecef_df,
    ppp_df,
    on="SOD",
    how="inner"
)

# =====================================================
# BASIC INFO
# =====================================================

expected_epochs = 2880
available_epochs = len(merged)

availability = (
    available_epochs /
    expected_epochs
) * 100.0

# =====================================================
# TIME AXIS
# =====================================================

time_hours = merged["SOD"] / 3600.0

merged["Time"] = pd.to_datetime(
    merged["SOD"],
    unit='s'
).dt.strftime('%H:%M:%S')

# =====================================================
# COORDINATES
# =====================================================

rtk_x = merged["X_ECEF(m)"]
rtk_y = merged["Y_ECEF(m)"]
rtk_z = merged["Z_ECEF(m)"]

ppp_x = merged["Computed_X"]
ppp_y = merged["Computed_Y"]
ppp_z = merged["Computed_Z"]

ref_x = merged["REF_X"]
ref_y = merged["REF_Y"]
ref_z = merged["REF_Z"]

# =====================================================
# PPP ABSOLUTE ERRORS (cm)
# PPP - TRUE
# =====================================================

merged["PPP_DX_cm"] = (
    merged["Computed_X"] -
    merged["REF_X"]
) * 100.0

merged["PPP_DY_cm"] = (
    merged["Computed_Y"] -
    merged["REF_Y"]
) * 100.0

merged["PPP_DZ_cm"] = (
    merged["Computed_Z"] -
    merged["REF_Z"]
) * 100.0

# =====================================================
# RTKLIB ABSOLUTE ERRORS (cm)
# RTKLIB - TRUE
# =====================================================

merged["RTK_DX_cm"] = (
    merged["X_ECEF(m)"] -
    merged["REF_X"]
) * 100.0

merged["RTK_DY_cm"] = (
    merged["Y_ECEF(m)"] -
    merged["REF_Y"]
) * 100.0

merged["RTK_DZ_cm"] = (
    merged["Z_ECEF(m)"] -
    merged["REF_Z"]
) * 100.0

# =====================================================
# PPP vs RTKLIB DIFFERENCES
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
# 3D ERRORS
# =====================================================

merged["PPP_3D_cm"] = np.sqrt(

    merged["PPP_DX_cm"]**2 +
    merged["PPP_DY_cm"]**2 +
    merged["PPP_DZ_cm"]**2

)

merged["RTK_3D_cm"] = np.sqrt(

    merged["RTK_DX_cm"]**2 +
    merged["RTK_DY_cm"]**2 +
    merged["RTK_DZ_cm"]**2

)

merged["PPP_RTK_3D_cm"] = np.sqrt(

    merged["Delta_X_cm"]**2 +
    merged["Delta_Y_cm"]**2 +
    merged["Delta_Z_cm"]**2

)

# =====================================================
# ECEF -> ENU CONVERSION
# =====================================================

X0 = merged["REF_X"].mean()
Y0 = merged["REF_Y"].mean()
Z0 = merged["REF_Z"].mean()

a = 6378137.0
e2 = 6.69437999014e-3

lon = np.arctan2(Y0, X0)

p = np.sqrt(X0**2 + Y0**2)

lat = np.arctan2(
    Z0,
    p * (1 - e2)
)

for _ in range(5):

    N = a / np.sqrt(
        1 - e2*np.sin(lat)**2
    )

    h = p/np.cos(lat) - N

    lat = np.arctan2(
        Z0,
        p*(1 - e2*N/(N+h))
    )

sin_lat = np.sin(lat)
cos_lat = np.cos(lat)

sin_lon = np.sin(lon)
cos_lon = np.cos(lon)

R = np.array([

    [-sin_lon,
      cos_lon,
      0],

    [-sin_lat*cos_lon,
     -sin_lat*sin_lon,
      cos_lat],

    [ cos_lat*cos_lon,
      cos_lat*sin_lon,
      sin_lat]

])

# =====================================================
# PPP ENU
# =====================================================

ppp_dxyz = np.vstack([

    merged["PPP_DX_cm"],
    merged["PPP_DY_cm"],
    merged["PPP_DZ_cm"]

])

ppp_enu = R @ ppp_dxyz

merged["PPP_East_cm"]  = ppp_enu[0]
merged["PPP_North_cm"] = ppp_enu[1]
merged["PPP_Up_cm"]    = ppp_enu[2]

# =====================================================
# RTKLIB ENU
# =====================================================

rtk_dxyz = np.vstack([

    merged["RTK_DX_cm"],
    merged["RTK_DY_cm"],
    merged["RTK_DZ_cm"]

])

rtk_enu = R @ rtk_dxyz

merged["RTK_East_cm"]  = rtk_enu[0]
merged["RTK_North_cm"] = rtk_enu[1]
merged["RTK_Up_cm"]    = rtk_enu[2]

# =====================================================
# PPP RMS
# =====================================================

ppp_x_rms = np.sqrt(np.mean(merged["PPP_DX_cm"]**2))
ppp_y_rms = np.sqrt(np.mean(merged["PPP_DY_cm"]**2))
ppp_z_rms = np.sqrt(np.mean(merged["PPP_DZ_cm"]**2))

ppp_east_rms = np.sqrt(np.mean(merged["PPP_East_cm"]**2))
ppp_north_rms = np.sqrt(np.mean(merged["PPP_North_cm"]**2))
ppp_up_rms = np.sqrt(np.mean(merged["PPP_Up_cm"]**2))

ppp_3d_rms = np.sqrt(np.mean(merged["PPP_3D_cm"]**2))

# =====================================================
# RTKLIB RMS
# =====================================================

rtk_x_rms = np.sqrt(np.mean(merged["RTK_DX_cm"]**2))
rtk_y_rms = np.sqrt(np.mean(merged["RTK_DY_cm"]**2))
rtk_z_rms = np.sqrt(np.mean(merged["RTK_DZ_cm"]**2))

rtk_east_rms = np.sqrt(np.mean(merged["RTK_East_cm"]**2))
rtk_north_rms = np.sqrt(np.mean(merged["RTK_North_cm"]**2))
rtk_up_rms = np.sqrt(np.mean(merged["RTK_Up_cm"]**2))

rtk_3d_rms = np.sqrt(np.mean(merged["RTK_3D_cm"]**2))

# =====================================================
# MEAN / MAX 3D
# =====================================================

ppp_mean_3d = merged["PPP_3D_cm"].mean()
rtk_mean_3d = merged["RTK_3D_cm"].mean()

ppp_max_3d = merged["PPP_3D_cm"].max()
rtk_max_3d = merged["RTK_3D_cm"].max()

# =====================================================
# CONVERGENCE FUNCTION
# =====================================================

def convergence_time(
    error_series,
    threshold=20,
    consecutive=10
):

    count = 0

    for i, val in enumerate(error_series):

        if val < threshold:
            count += 1
        else:
            count = 0

        if count >= consecutive:

            sod = merged["SOD"].iloc[i]

            return sod / 60.0

    return None

# =====================================================
# CONVERGENCE TIMES
# =====================================================

ppp_conv = convergence_time(
    merged["PPP_3D_cm"]
)

rtk_conv = convergence_time(
    merged["RTK_3D_cm"]
)

# =====================================================
# TERMINAL OUTPUT
# =====================================================

print("\n==========================================")
print("             PPP PERFORMANCE")
print("==========================================")

print("\nCoordinate RMS")
print("------------------------------------------")

print(f"PPP X RMS     : {ppp_x_rms:.2f} cm")
print(f"PPP Y RMS     : {ppp_y_rms:.2f} cm")
print(f"PPP Z RMS     : {ppp_z_rms:.2f} cm")

print("\nENU RMS")
print("------------------------------------------")

print(f"PPP East RMS  : {ppp_east_rms:.2f} cm")
print(f"PPP North RMS : {ppp_north_rms:.2f} cm")
print(f"PPP Up RMS    : {ppp_up_rms:.2f} cm")

print("\n3D Metrics")
print("------------------------------------------")

print(f"PPP 3D RMS        : {ppp_3d_rms:.2f} cm")
print(f"PPP Mean 3D Error : {ppp_mean_3d:.2f} cm")
print(f"PPP Max 3D Error  : {ppp_max_3d:.2f} cm")

print("\nConvergence")
print("------------------------------------------")

print(f"PPP Convergence Time : {ppp_conv:.2f} min")

# =====================================================

print("\n==========================================")
print("           RTKLIB PERFORMANCE")
print("==========================================")

print("\nCoordinate RMS")
print("------------------------------------------")

print(f"RTK X RMS     : {rtk_x_rms:.2f} cm")
print(f"RTK Y RMS     : {rtk_y_rms:.2f} cm")
print(f"RTK Z RMS     : {rtk_z_rms:.2f} cm")

print("\nENU RMS")
print("------------------------------------------")

print(f"RTK East RMS  : {rtk_east_rms:.2f} cm")
print(f"RTK North RMS : {rtk_north_rms:.2f} cm")
print(f"RTK Up RMS    : {rtk_up_rms:.2f} cm")

print("\n3D Metrics")
print("------------------------------------------")

print(f"RTK 3D RMS        : {rtk_3d_rms:.2f} cm")
print(f"RTK Mean 3D Error : {rtk_mean_3d:.2f} cm")
print(f"RTK Max 3D Error  : {rtk_max_3d:.2f} cm")

print("\nConvergence")
print("------------------------------------------")

print(f"RTK Convergence Time : {rtk_conv:.2f} min")

# =====================================================

print("\n==========================================")
print("        AVAILABILITY INFORMATION")
print("==========================================")

print(f"\nAvailable Epochs : {available_epochs}")
print(f"Expected Epochs  : {expected_epochs}")
print(f"Availability     : {availability:.2f}%")

print("\n==========================================\n")

# =====================================================
# SAVE SUMMARY CSV
# =====================================================

summary_df = pd.DataFrame({

    "Metric": [

        "Availability_percent",

        "PPP_X_RMS_cm",
        "PPP_Y_RMS_cm",
        "PPP_Z_RMS_cm",

        "PPP_East_RMS_cm",
        "PPP_North_RMS_cm",
        "PPP_Up_RMS_cm",

        "PPP_3D_RMS_cm",
        "PPP_Mean_3D_cm",
        "PPP_Max_3D_cm",
        "PPP_Convergence_min",

        "RTK_X_RMS_cm",
        "RTK_Y_RMS_cm",
        "RTK_Z_RMS_cm",

        "RTK_East_RMS_cm",
        "RTK_North_RMS_cm",
        "RTK_Up_RMS_cm",

        "RTK_3D_RMS_cm",
        "RTK_Mean_3D_cm",
        "RTK_Max_3D_cm",
        "RTK_Convergence_min"

    ],

    "Value": [

        availability,

        ppp_x_rms,
        ppp_y_rms,
        ppp_z_rms,

        ppp_east_rms,
        ppp_north_rms,
        ppp_up_rms,

        ppp_3d_rms,
        ppp_mean_3d,
        ppp_max_3d,
        ppp_conv,

        rtk_x_rms,
        rtk_y_rms,
        rtk_z_rms,

        rtk_east_rms,
        rtk_north_rms,
        rtk_up_rms,

        rtk_3d_rms,
        rtk_mean_3d,
        rtk_max_3d,
        rtk_conv

    ]

})

summary_df.to_csv(
    r"D:\PROJECT\results\summary_metrics.csv",
    index=False
)

print("Saved: summary_metrics.csv")

# =====================================================
# SAVE COMPARISON CSV
# =====================================================

merged.to_csv(
    r"D:\PROJECT\results\ppp_vs_rtk_comparison.csv",
    index=False
)

print("Saved: ppp_vs_rtk_comparison.csv")

# =====================================================
# COMMON PLOT STYLE
# =====================================================

def style_plot():

    plt.grid(
        True,
        linestyle='--',
        linewidth=0.5,
        alpha=0.35
    )

    plt.xticks(
        ticks=range(0,25,4),
        labels=[f"{i:02d}:00" for i in range(0,25,4)],
        fontsize=11
    )

    plt.yticks(fontsize=11)

    ax = plt.gca()

    for spine in ax.spines.values():
        spine.set_linewidth(1.2)

    ax.tick_params(
        direction='in',
        width=1.2,
        length=4
    )

    plt.xlim(0, 24)

# =====================================================
# COMPARISON PLOTS
# =====================================================

plots = [

    ("Delta_X_cm", "PPP - RTK X (cm)", "firebrick", "delta_x_cm_plot.png"),

    ("Delta_Y_cm", "PPP - RTK Y (cm)", "royalblue", "delta_y_cm_plot.png"),

    ("Delta_Z_cm", "PPP - RTK Z (cm)", "dimgray", "delta_z_cm_plot.png"),

    ("PPP_3D_cm", "PPP 3D Error (cm)", "darkgreen", "ppp_3d_error_plot.png"),

    ("RTK_3D_cm", "RTKLIB 3D Error (cm)", "darkorange", "rtk_3d_error_plot.png"),

    ("PPP_RTK_3D_cm", "PPP vs RTK 3D Dev (cm)", "purple", "ppp_rtk_3d_deviation_plot.png")

]

for col, ylabel, color, fname in plots:

    plt.figure(figsize=(10,4))

    plt.plot(
        time_hours,
        merged[col],
        color=color,
        linewidth=1.5
    )

    plt.xlabel("Time (Hours)")
    plt.ylabel(ylabel)

    style_plot()

    plt.savefig(
        rf"D:\PROJECT\results\{fname}",
        dpi=600,
        bbox_inches='tight'
    )

plt.show()

print("\nAll processing completed successfully.")
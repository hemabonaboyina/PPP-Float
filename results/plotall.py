import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# =====================================================
# GLOBAL JOURNAL STYLE
# =====================================================

plt.rcParams['font.family'] = 'serif'
plt.rcParams['font.size'] = 12

# =====================================================
# FILE PATHS
# =====================================================

ppp_file = r"D:\PROJECT\results\ppp_results_gps_fwd1.csv"

rtk_file = r"D:\PROJECT\results\ecef_output.csv"

glab_file = r"D:\PROJECT\results\glab_xyz.csv"

# =====================================================
# READ FILES
# =====================================================

ppp_df = pd.read_csv(ppp_file)

rtk_df = pd.read_csv(rtk_file)

glab_df = pd.read_csv(glab_file)

# =====================================================
# RENAME RTK COLUMN
# =====================================================

rtk_df.rename(
    columns={"Seconds_of_Day": "SOD"},
    inplace=True
)

# =====================================================
# GEODETIC ROTATION MATRIX FUNCTION
# =====================================================

def get_rotation_matrix(ref_x, ref_y, ref_z):

    X0 = np.mean(ref_x)
    Y0 = np.mean(ref_y)
    Z0 = np.mean(ref_z)

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

    return R

# =====================================================
# XYZ -> ENU
# =====================================================

def xyz_to_enu(dx, dy, dz, R):

    dxyz = np.vstack([dx, dy, dz])

    enu = R @ dxyz

    return enu[0], enu[1], enu[2]

# =====================================================
# RMS FUNCTION
# =====================================================

def rms(x):

    return np.sqrt(np.mean(x**2))

# =====================================================
# CONVERGENCE FUNCTION
# =====================================================

def convergence_time(
    sod,
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

            return sod.iloc[i] / 60.0

    return None

# =====================================================
# JOURNAL STYLE PLOT FORMAT
# =====================================================

def style_plot():

    ax = plt.gca()

    # -------------------------------------------------
    # GRID
    # -------------------------------------------------

    plt.grid(
        True,
        linestyle='--',
        linewidth=0.7,
        alpha=0.35
    )

    # -------------------------------------------------
    # THICK BORDERS
    # -------------------------------------------------

    for spine in ax.spines.values():

        spine.set_linewidth(2.0)
        spine.set_color('black')

    # -------------------------------------------------
    # TICKS
    # -------------------------------------------------

    ax.tick_params(

        axis='both',

        direction='in',

        length=6,
        width=1.8,

        colors='black',

        labelsize=11

    )

    # -------------------------------------------------
    # X AXIS
    # -------------------------------------------------

    plt.xticks(

        ticks=range(0,25,4),

        labels=[f"{i:02d}:00" for i in range(0,25,4)]

    )

    plt.xlim(0,24)

    # -------------------------------------------------
    # LEGEND STYLE
    # -------------------------------------------------

    legend = plt.legend(

        frameon=True,

        fontsize=10,

        edgecolor='black'

    )

    legend.get_frame().set_linewidth(1.5)

# =====================================================
# PPP METRICS
# =====================================================

R_ppp = get_rotation_matrix(
    ppp_df["REF_X"],
    ppp_df["REF_Y"],
    ppp_df["REF_Z"]
)

ppp_df["dX_cm"] = (
    ppp_df["Computed_X"] -
    ppp_df["REF_X"]
) * 100.0

ppp_df["dY_cm"] = (
    ppp_df["Computed_Y"] -
    ppp_df["REF_Y"]
) * 100.0

ppp_df["dZ_cm"] = (
    ppp_df["Computed_Z"] -
    ppp_df["REF_Z"]
) * 100.0

(
    ppp_df["East_cm"],
    ppp_df["North_cm"],
    ppp_df["Up_cm"]

) = xyz_to_enu(

    ppp_df["dX_cm"],
    ppp_df["dY_cm"],
    ppp_df["dZ_cm"],
    R_ppp

)

ppp_df["3D_cm"] = np.sqrt(

    ppp_df["East_cm"]**2 +
    ppp_df["North_cm"]**2 +
    ppp_df["Up_cm"]**2

)

ppp_conv = convergence_time(
    ppp_df["SOD"],
    ppp_df["3D_cm"]
)

print("\n======================================")
print("        PROPOSED PPP METRICS")
print("======================================")

print(f"\nPPP East RMS  : {rms(ppp_df['East_cm']):.2f} cm")
print(f"PPP North RMS : {rms(ppp_df['North_cm']):.2f} cm")
print(f"PPP Up RMS    : {rms(ppp_df['Up_cm']):.2f} cm")

print(f"\nPPP 3D RMS        : {rms(ppp_df['3D_cm']):.2f} cm")
print(f"PPP Mean 3D Error : {np.mean(ppp_df['3D_cm']):.2f} cm")
print(f"PPP Max 3D Error  : {np.max(ppp_df['3D_cm']):.2f} cm")

print(f"\nPPP Convergence Time : {ppp_conv:.2f} min")

print(f"\nPPP Availability : {(len(ppp_df)/2880)*100:.2f}%")

# =====================================================
# PPP COORDINATE TABLES
# =====================================================

ppp_df["UTC"] = pd.to_datetime(
    ppp_df["SOD"],
    unit='s'
).dt.strftime('%H:%M:%S')

# =====================================================
# ALL EPOCH COORDINATES
# =====================================================

ppp_all_epochs = ppp_df[[

    "UTC",
    "SOD",

    "Computed_X",
    "Computed_Y",
    "Computed_Z"

]].copy()

ppp_all_epochs.columns = [

    "UTC",
    "SOD",

    "X_Component_m",
    "Y_Component_m",
    "Z_Component_m"

]

ppp_all_epochs.to_csv(
    r"D:\PROJECT\results\ppp_all_epochs_coordinates.csv",
    index=False
)

print("\nSaved: ppp_all_epochs_coordinates.csv")

# =====================================================
# 2-HOUR COORDINATE TABLE
# =====================================================

ppp_2hr = ppp_all_epochs.iloc[::240].copy()

ppp_2hr.to_csv(
    r"D:\PROJECT\results\ppp_2hour_coordinates.csv",
    index=False
)

print("Saved: ppp_2hour_coordinates.csv")

# =====================================================
# RTK METRICS
# =====================================================

common_ref = ppp_df.iloc[0]

rtk_df["REF_X"] = common_ref["REF_X"]
rtk_df["REF_Y"] = common_ref["REF_Y"]
rtk_df["REF_Z"] = common_ref["REF_Z"]

R_rtk = get_rotation_matrix(

    rtk_df["REF_X"],
    rtk_df["REF_Y"],
    rtk_df["REF_Z"]

)

rtk_df["dX_cm"] = (
    rtk_df["X_ECEF(m)"] -
    rtk_df["REF_X"]
) * 100.0

rtk_df["dY_cm"] = (
    rtk_df["Y_ECEF(m)"] -
    rtk_df["REF_Y"]
) * 100.0

rtk_df["dZ_cm"] = (
    rtk_df["Z_ECEF(m)"] -
    rtk_df["REF_Z"]
) * 100.0

(
    rtk_df["East_cm"],
    rtk_df["North_cm"],
    rtk_df["Up_cm"]

) = xyz_to_enu(

    rtk_df["dX_cm"],
    rtk_df["dY_cm"],
    rtk_df["dZ_cm"],
    R_rtk

)

rtk_df["3D_cm"] = np.sqrt(

    rtk_df["East_cm"]**2 +
    rtk_df["North_cm"]**2 +
    rtk_df["Up_cm"]**2

)

rtk_conv = convergence_time(
    rtk_df["SOD"],
    rtk_df["3D_cm"]
)

print("\n======================================")
print("          RTKLIB METRICS")
print("======================================")

print(f"\nRTK East RMS  : {rms(rtk_df['East_cm']):.2f} cm")
print(f"RTK North RMS : {rms(rtk_df['North_cm']):.2f} cm")
print(f"RTK Up RMS    : {rms(rtk_df['Up_cm']):.2f} cm")

print(f"\nRTK 3D RMS        : {rms(rtk_df['3D_cm']):.2f} cm")
print(f"RTK Mean 3D Error : {np.mean(rtk_df['3D_cm']):.2f} cm")
print(f"RTK Max 3D Error  : {np.max(rtk_df['3D_cm']):.2f} cm")

print(f"\nRTK Convergence Time : {rtk_conv:.2f} min")

print(f"\nRTK Availability : {(len(rtk_df)/2880)*100:.2f}%")

# =====================================================
# gLAB METRICS
# =====================================================

glab_df["REF_X"] = common_ref["REF_X"]
glab_df["REF_Y"] = common_ref["REF_Y"]
glab_df["REF_Z"] = common_ref["REF_Z"]

R_glab = get_rotation_matrix(

    glab_df["REF_X"],
    glab_df["REF_Y"],
    glab_df["REF_Z"]

)

glab_df["dX_cm"] = (
    glab_df["X"] -
    glab_df["REF_X"]
) * 100.0

glab_df["dY_cm"] = (
    glab_df["Y"] -
    glab_df["REF_Y"]
) * 100.0

glab_df["dZ_cm"] = (
    glab_df["Z"] -
    glab_df["REF_Z"]
) * 100.0

(
    glab_df["East_cm"],
    glab_df["North_cm"],
    glab_df["Up_cm"]

) = xyz_to_enu(

    glab_df["dX_cm"],
    glab_df["dY_cm"],
    glab_df["dZ_cm"],
    R_glab

)

glab_df["3D_cm"] = np.sqrt(

    glab_df["East_cm"]**2 +
    glab_df["North_cm"]**2 +
    glab_df["Up_cm"]**2

)

glab_conv = convergence_time(
    glab_df["SOD"],
    glab_df["3D_cm"]
)

print("\n======================================")
print("            gLAB METRICS")
print("======================================")

print(f"\ngLAB East RMS  : {rms(glab_df['East_cm']):.2f} cm")
print(f"gLAB North RMS : {rms(glab_df['North_cm']):.2f} cm")
print(f"gLAB Up RMS    : {rms(glab_df['Up_cm']):.2f} cm")

print(f"\ngLAB 3D RMS        : {rms(glab_df['3D_cm']):.2f} cm")
print(f"gLAB Mean 3D Error : {np.mean(glab_df['3D_cm']):.2f} cm")
print(f"gLAB Max 3D Error  : {np.max(glab_df['3D_cm']):.2f} cm")

print(f"\ngLAB Convergence Time : {glab_conv:.2f} min")

print(f"\ngLAB Availability : {(len(glab_df)/2880)*100:.2f}%")

# =====================================================
# PPP vs RTK PLOTS
# =====================================================

rtk_compare = pd.merge(
    ppp_df,
    rtk_df,
    on="SOD",
    suffixes=("_PPP", "_RTK")
)

time_hours_rtk = (
    rtk_compare["SOD"] / 3600.0
)

plots_rtk = [

    ("dX_cm_PPP", "dX_cm_RTK", "X Error (cm)", "x_component_plot.png.png"),

    ("dY_cm_PPP", "dY_cm_RTK", "Y Error (cm)", "y_component_plot.png"),

    ("dZ_cm_PPP", "dZ_cm_RTK", "Z Error (cm)", "z_component_plot.png"),

    ("3D_cm_PPP", "3D_cm_RTK", "3D Error (cm)", "3d_error_cm_plot.png")

]

for ppp_col, rtk_col, ylabel, fname in plots_rtk:

    plt.figure(figsize=(10,4.5))

    plt.plot(

        time_hours_rtk,

        rtk_compare[ppp_col],

        color='navy',

        linewidth=2.0,

        label="Proposed PPP"

    )

    plt.plot(

        time_hours_rtk,

        rtk_compare[rtk_col],

        color='darkorange',

        linewidth=2.0,

        label="RTKLIB"

    )

    plt.xlabel(
        "Time (Hours)",
        fontsize=12,
        fontweight='bold'
    )

    plt.ylabel(
        ylabel,
        fontsize=12,
        fontweight='bold'
    )

    style_plot()

    plt.tight_layout()

    plt.savefig(

        rf"D:\PROJECT\results\{fname}",

        dpi=600,

        bbox_inches='tight'

    )

# =====================================================
# PPP vs gLAB
# =====================================================

glab_compare = pd.merge(
    ppp_df,
    glab_df,
    on="SOD",
    suffixes=("_PPP", "_gLAB")
)

time_hours_glab = (
    glab_compare["SOD"] / 3600.0
)

plots_glab = [

    ("dX_cm_PPP", "dX_cm_gLAB", "X Error (cm)", "X_comparison.png.png"),

    ("dY_cm_PPP", "dY_cm_gLAB", "Y Error (cm)", "Y_comparison.png.png"),

    ("dZ_cm_PPP", "dZ_cm_gLAB", "Z Error (cm)", "Z_comparison.png.png"),

    ("3D_cm_PPP", "3D_cm_gLAB", "3D Error (cm)", "3D_difference.png.png")

]

for ppp_col, glab_col, ylabel, fname in plots_glab:

    plt.figure(figsize=(10,4.5))

    plt.plot(

        time_hours_glab,

        glab_compare[ppp_col],

        color='navy',

        linewidth=2.0,

        label="Proposed PPP"

    )

    plt.plot(

        time_hours_glab,

        glab_compare[glab_col],

        color='firebrick',

        linewidth=2.0,

        label="gLAB"

    )

    plt.xlabel(
        "Time (Hours)",
        fontsize=12,
        fontweight='bold'
    )

    plt.ylabel(
        ylabel,
        fontsize=12,
        fontweight='bold'
    )

    style_plot()

    plt.tight_layout()

    plt.savefig(

        rf"D:\PROJECT\results\{fname}",

        dpi=600,

        bbox_inches='tight'

    )

plt.show()

print("\nAll processing completed successfully.")
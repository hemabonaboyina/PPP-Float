"""
ppp_phase3b_plot.py — Phase-3B Post-Run Analysis & Plotting
============================================================

Run AFTER ppp_phase3b.py has completed all 9 runs.

Reads:
  phase3b_summary.csv          — scalar metrics for all 9 cells
  phase3b_run_G*_Q*.csv        — per-epoch position errors
  phase3b_innov_G*_Q*.csv      — elevation-binned innovation RMS

Produces:
  phase3b_fig1_3d_matrix.png   — 3×3 grid of 3D-error time series
  phase3b_fig2_metrics.png     — heat-map comparison of scalar metrics
  phase3b_fig3_innov_elev.png  — elevation-stratified innovation RMS
  phase3b_fig4_amb_sigma.png   — median ambiguity sigma decay curves
  phase3b_diagnosis.txt        — auto-generated hypothesis verdict

Usage:
    python ppp_phase3b_plot.py
"""

import os, sys, math, csv, glob
from collections import defaultdict
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.colors import LogNorm

_HERE = os.path.dirname(os.path.abspath(__file__))

GATE_LABELS  = ['A', 'B', 'C']
GATE_MM      = {'A': 50,  'B': 250, 'C': 500}
QCLK_LABELS  = ['1', '2', '3']
QCLK_COEFF   = {'1': 1e4, '2': 1e0, '3': 1e-2}
EL_BINS      = [(0,15), (15,30), (30,45), (45,60), (60,90)]

# Colour palette
GATE_COLORS  = {'A': '#e6194b', 'B': '#f58231', 'C': '#3cb44b'}
QCLK_STYLES  = {'1': '-', '2': '--', '3': ':'}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_summary(path: str) -> list[dict]:
    with open(path) as fh:
        return list(csv.DictReader(fh))

def _load_run(path: str) -> dict:
    """Returns {sod: row_dict}."""
    d = {}
    with open(path) as fh:
        for row in csv.DictReader(fh):
            try:
                d[float(row['SOD'])] = row
            except (KeyError, ValueError):
                pass
    return d

def _load_innov(path: str) -> dict:
    """Returns {(el_lo, el_hi): dict}."""
    d = {}
    with open(path) as fh:
        for row in csv.DictReader(fh):
            try:
                k = (int(row['el_lo']), int(row['el_hi']))
                d[k] = {c: _safe_float(row[c]) for c in ('n','mean_mm','rms_mm','mad_mm','p95_mm')}
            except (KeyError, ValueError):
                pass
    return d

def _safe_float(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else float('nan')
    except (TypeError, ValueError):
        return float('nan')

def _run_path(gl, ql):
    return os.path.join(_HERE, f'phase3b_run_G{gl}_Q{ql}.csv')

def _innov_path(gl, ql):
    return os.path.join(_HERE, f'phase3b_innov_G{gl}_Q{ql}.csv')

def _val(summary_row: dict, key: str) -> float:
    return _safe_float(summary_row.get(key))


# ── Figure 1: 3×3 grid of 3D error time series ───────────────────────────────

def plot_3d_matrix(out_path: str):
    fig, axes = plt.subplots(3, 3, figsize=(16, 12), sharex=True, sharey=True)
    fig.suptitle('Phase-3B: 3D Positioning Error (FWD) — Gate × Qclk Matrix',
                 fontsize=13, fontweight='bold')

    for ri, gl in enumerate(GATE_LABELS):
        for ci, ql in enumerate(QCLK_LABELS):
            ax = axes[ri][ci]
            rpath = _run_path(gl, ql)
            if not os.path.isfile(rpath):
                ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                        transform=ax.transAxes, color='gray')
                continue
            run = _load_run(rpath)
            if not run:
                continue
            sods = sorted(run.keys())
            d3 = [_safe_float(run[s].get('3D_mm')) for s in sods]
            hours = np.array(sods) / 3600.

            ax.plot(hours, d3, lw=0.8, color=GATE_COLORS[gl], alpha=0.85)
            ax.axhline(100, color='gray', lw=0.6, ls='--')
            ax.axhline(50,  color='gray', lw=0.6, ls=':')
            ax.set_ylim(0, 400)
            ax.grid(True, alpha=0.25)
            ax.set_title(f'Gate={GATE_MM[gl]}mm  Qclk=1e{int(math.log10(QCLK_COEFF[ql]))}×dt',
                         fontsize=8)
            if ri == 2:
                ax.set_xlabel('Time (h)', fontsize=8)
            if ci == 0:
                ax.set_ylabel('3D Error (mm)', fontsize=8)
            ax.tick_params(labelsize=7)

    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches='tight')
    plt.close(fig)
    print(f"[PLOT] 3D matrix → {out_path}")


# ── Figure 2: Heat-map of scalar metrics ─────────────────────────────────────

def plot_metric_heatmap(summary_rows: list[dict], out_path: str):
    id_map = {(r['gate_label'], r['qclk_label']): r for r in summary_rows}

    metrics = [
        ('fwd_3d_rms_mm',     'FWD 3D RMS (mm)',      True),
        ('fwd_up_rms_mm',     'FWD Up  RMS (mm)',      True),
        ('rts_up_rms_mm',     'RTS Up  RMS (mm)',      True),
        ('rebirth_count',     'Rebirth count',          True),
        ('wl_sats_fixed',     'WL sats fixed',          False),
        ('nl_epochs',         'NL-fixed epochs',        False),
        ('same_sign_pct',     'Same-sign phase (%)',    False),
        ('innov_rms_el00_15', 'Innov RMS  0–15° (mm)', True),
        ('innov_rms_el60_90', 'Innov RMS 60–90° (mm)', True),
    ]

    n_metrics = len(metrics)
    fig, axes = plt.subplots(1, n_metrics, figsize=(n_metrics * 2.2, 4.5))
    fig.suptitle('Phase-3B Scalar Metric Heat-map  (rows=Gate A/B/C  cols=Qclk 1/2/3)',
                 fontsize=11, fontweight='bold')

    for ax, (key, label, lower_better) in zip(axes, metrics):
        mat = np.full((3, 3), np.nan)
        for ri, gl in enumerate(GATE_LABELS):
            for ci, ql in enumerate(QCLK_LABELS):
                s = id_map.get((gl, ql))
                if s:
                    mat[ri, ci] = _val(s, key)

        # Mask NaN
        masked = np.ma.masked_invalid(mat)
        cmap = 'RdYlGn_r' if lower_better else 'RdYlGn'
        im = ax.imshow(masked, cmap=cmap, aspect='auto')

        for ri in range(3):
            for ci in range(3):
                v = mat[ri, ci]
                txt = f'{v:.1f}' if not math.isnan(v) else 'N/A'
                ax.text(ci, ri, txt, ha='center', va='center', fontsize=7,
                        fontweight='bold', color='black')

        ax.set_xticks([0, 1, 2]); ax.set_xticklabels(['Q1','Q2','Q3'], fontsize=7)
        ax.set_yticks([0, 1, 2]); ax.set_yticklabels(['GA','GB','GC'], fontsize=7)
        ax.set_title(label, fontsize=7, pad=3)
        plt.colorbar(im, ax=ax, shrink=0.7)

    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches='tight')
    plt.close(fig)
    print(f"[PLOT] Heat-map → {out_path}")


# ── Figure 3: Elevation-stratified innovation RMS bar chart ──────────────────

def plot_innov_elevation(out_path: str):
    n_bins = len(EL_BINS)
    fig, axes = plt.subplots(3, 3, figsize=(15, 11), sharey=True, sharex=True)
    fig.suptitle('Phase-3B: Post-fit Phase Innovation RMS by Elevation\n'
                 '(rows=Gate  cols=Qclk — critical for gate realism verdict)',
                 fontsize=11, fontweight='bold')

    x = np.arange(n_bins)
    labels = [f'{lo}–{hi}°' for (lo, hi) in EL_BINS]
    bar_colors = ['#e6194b','#f58231','#ffe119','#3cb44b','#4363d8']

    for ri, gl in enumerate(GATE_LABELS):
        for ci, ql in enumerate(QCLK_LABELS):
            ax = axes[ri][ci]
            ipath = _innov_path(gl, ql)
            if not os.path.isfile(ipath):
                ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                        transform=ax.transAxes, color='gray', fontsize=9)
                continue
            innov = _load_innov(ipath)
            rms_vals = [innov.get(b, {}).get('rms_mm', float('nan')) for b in EL_BINS]
            p95_vals = [innov.get(b, {}).get('p95_mm', float('nan')) for b in EL_BINS]
            ns       = [innov.get(b, {}).get('n', 0)                 for b in EL_BINS]

            bars = ax.bar(x, rms_vals, color=bar_colors, alpha=0.75, label='RMS')
            ax.scatter(x, p95_vals, color='black', s=15, zorder=5, label='P95')

            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=30, ha='right', fontsize=6)
            ax.set_title(f'Gate={GATE_MM[gl]}mm  Qclk=1e{int(math.log10(QCLK_COEFF[ql]))}×dt',
                         fontsize=8)
            ax.grid(True, axis='y', alpha=0.3)
            if ri == 0 and ci == 0:
                ax.legend(fontsize=6)
            if ci == 0:
                ax.set_ylabel('Innov RMS (mm)', fontsize=8)
            if ri == 2:
                ax.set_xlabel('Elevation bin', fontsize=8)
            # Annotate n on each bar
            for xi, (rv, n) in enumerate(zip(rms_vals, ns)):
                if not math.isnan(rv) and n > 0:
                    ax.text(xi, rv + 0.5, f'n={n}', ha='center', fontsize=5, color='#444')

    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches='tight')
    plt.close(fig)
    print(f"[PLOT] Elevation innovation → {out_path}")


# ── Figure 4: NL-fixed epochs comparison ──────────────────────────────────────

def plot_nl_comparison(summary_rows: list[dict], out_path: str):
    """Bar chart: NL-fixed epochs for each of the 9 runs."""
    id_map = {(r['gate_label'], r['qclk_label']): r for r in summary_rows}

    fig, axes = plt.subplots(1, 3, figsize=(13, 5), sharey=True)
    fig.suptitle('Phase-3B: NL-Fixed Epochs — Most Important Milestone\n'
                 '(Any bar > 0 indicates ambiguity resolution breakthrough)',
                 fontsize=11, fontweight='bold')

    bar_w = 0.25
    x = np.arange(3)   # Qclk positions

    for ri, (ax, gl) in enumerate(zip(axes, GATE_LABELS)):
        nl_vals = [_val(id_map.get((gl, ql), {}), 'nl_epochs') for ql in QCLK_LABELS]
        wl_vals = [_val(id_map.get((gl, ql), {}), 'wl_epochs') for ql in QCLK_LABELS]

        bars_wl = ax.bar(x - bar_w/2, wl_vals, bar_w, label='WL-fixed epochs',
                         color='#4363d8', alpha=0.7)
        bars_nl = ax.bar(x + bar_w/2, nl_vals, bar_w, label='NL-fixed epochs',
                         color='#e6194b', alpha=0.9)

        for xp, nv in zip(x, nl_vals):
            if not math.isnan(nv) and nv > 0:
                ax.text(xp + bar_w/2, nv + 5, f'{nv:.0f}', ha='center',
                        fontsize=9, fontweight='bold', color='#c00')

        ax.set_xticks(x)
        ax.set_xticklabels([f'Qclk {ql}\n1e{int(math.log10(QCLK_COEFF[ql]))}×dt'
                            for ql in QCLK_LABELS], fontsize=9)
        ax.set_title(f'Gate {gl} = {GATE_MM[gl]} mm', fontsize=10)
        ax.grid(True, axis='y', alpha=0.3)
        ax.set_xlabel('Clock process noise', fontsize=9)
        if ri == 0:
            ax.set_ylabel('Epochs with fixes', fontsize=9)
        if ri == 0:
            ax.legend(fontsize=8)

    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches='tight')
    plt.close(fig)
    print(f"[PLOT] NL comparison → {out_path}")


# ── Auto-diagnosis ────────────────────────────────────────────────────────────

def write_diagnosis(summary_rows: list[dict], out_path: str):
    """
    Automated hypothesis verdict based on the campaign's CASE 1–4 framework.
    """
    id_map = {(r['gate_label'], r['qclk_label']): r for r in summary_rows}

    lines = []
    lines.append("=" * 80)
    lines.append("PHASE-3B AUTO-DIAGNOSIS")
    lines.append("=" * 80)
    lines.append("")

    # Compute gate effect (Qclk=1 fixed, vary Gate A→C)
    gate_effect = []
    for gl in GATE_LABELS:
        s = id_map.get((gl, '1'))
        if s:
            gate_effect.append((_val(s, 'fwd_3d_rms_mm'),
                                 _val(s, 'rebirth_count'),
                                 _val(s, 'nl_epochs')))

    # Compute Qclk effect (Gate=A fixed, vary Qclk 1→3)
    qclk_effect = []
    for ql in QCLK_LABELS:
        s = id_map.get(('A', ql))
        if s:
            qclk_effect.append((_val(s, 'fwd_3d_rms_mm'),
                                  _val(s, 'same_sign_pct'),
                                  _val(s, 'nl_epochs')))

    # Compute any NL fixed
    any_nl = any(_val(s, 'nl_epochs') > 0
                 for s in id_map.values()
                 if not math.isnan(_val(s, 'nl_epochs')))

    # Gate improvement score (FWD 3D RMS)
    gate_rms = [g[0] for g in gate_effect if not math.isnan(g[0])]
    qclk_rms = [q[0] for q in qclk_effect if not math.isnan(q[0])]

    gate_rms_improve = (gate_rms[0] - gate_rms[-1]) if len(gate_rms) >= 2 else 0.
    qclk_rms_improve = (qclk_rms[0] - qclk_rms[-1]) if len(qclk_rms) >= 2 else 0.
    gate_rebirth_drop = (gate_effect[0][1] - gate_effect[-1][1]) \
        if len(gate_effect) >= 2 and not math.isnan(gate_effect[0][1]) else 0.
    rebirth_pct_drop = (gate_rebirth_drop / max(gate_effect[0][1], 1) * 100) \
        if gate_effect and not math.isnan(gate_effect[0][1]) else 0.

    lines.append("── Gate effect (Qclk=1 fixed, Gate A→B→C) ──")
    for gl, row in zip(GATE_LABELS, gate_effect):
        lines.append(f"  Gate {gl} ({GATE_MM[gl]:3d}mm):  "
                     f"3D RMS={row[0]:.1f}mm  Rebirths={row[1]:.0f}  NL-epochs={row[2]:.0f}")
    lines.append(f"  3D improvement A→C: {gate_rms_improve:+.1f} mm")
    lines.append(f"  Rebirth reduction A→C: {gate_rebirth_drop:.0f} ({rebirth_pct_drop:.0f}%)")
    lines.append("")

    lines.append("── Qclk effect (Gate=A fixed, Qclk 1→2→3) ──")
    for ql, row in zip(QCLK_LABELS, qclk_effect):
        lines.append(f"  Qclk {ql} (coeff {QCLK_COEFF[ql]:.0e}):  "
                     f"3D RMS={row[0]:.1f}mm  Same-sign={row[1]:.0f}%  NL-epochs={row[2]:.0f}")
    lines.append(f"  3D improvement Qclk1→Qclk3: {qclk_rms_improve:+.1f} mm")
    lines.append("")

    # Elevation realism check
    lines.append("── Elevation-stratified innovation RMS (Gate=A, Qclk=1) ──")
    innov_A1 = _load_innov(_innov_path('A', '1')) if os.path.isfile(_innov_path('A','1')) else {}
    rms_low  = innov_A1.get((0, 15),  {}).get('rms_mm', float('nan'))
    rms_high = innov_A1.get((60, 90), {}).get('rms_mm', float('nan'))
    if not math.isnan(rms_low) and not math.isnan(rms_high) and rms_high > 0:
        ratio = rms_low / rms_high
        lines.append(f"  0–15° RMS = {rms_low:.2f} mm   |   60–90° RMS = {rms_high:.2f} mm")
        lines.append(f"  Low-el / High-el ratio = {ratio:.2f}x")
        if ratio > 5.:
            lines.append("  VERDICT: Low-elevation data HEAVILY corrupted "
                         "— gate=50mm is unrealistically tight for low-el obs.")
            lines.append("  → Gate B (250mm) or C (500mm) is physically justified for low-el.")
        elif ratio > 2.:
            lines.append("  VERDICT: Moderate low-el degradation. Gate A (50mm) will "
                         "exclude legitimate observations → starvation likely contributing.")
        else:
            lines.append("  VERDICT: Low-el and high-el innovation RMS are similar. "
                         "Gate A (50mm) may be reasonable — starvation less likely.")
    lines.append("")

    # Final verdict
    lines.append("── CASE VERDICT ──")
    gate_dom  = gate_rms_improve > 30. or rebirth_pct_drop > 30.
    qclk_dom  = qclk_rms_improve > 30.
    nl_any    = any_nl

    if nl_any:
        best_nl = max(id_map.items(), key=lambda kv: _val(kv[1], 'nl_epochs'))
        lines.append(f"  ★ NL FIXING ACHIEVED ★  Best cell: {best_nl[0]}  "
                     f"NL-epochs={_val(best_nl[1], 'nl_epochs'):.0f}")
        lines.append(f"  Gate={GATE_MM[best_nl[0][0]]}mm  "
                     f"Qclk=1e{int(math.log10(QCLK_COEFF[best_nl[0][1]]))}×dt")
    else:
        lines.append("  ✗ No NL fixing achieved in any run.")

    if gate_dom and not qclk_dom:
        lines.append("  → CASE 1: Ambiguity starvation DOMINANT. "
                     "Open the gate first; Qclk change has secondary effect.")
    elif qclk_dom and not gate_dom:
        lines.append("  → CASE 2: Clock stochastic model DOMINANT. "
                     "Reduce Qclk; gate effect is secondary.")
    elif gate_dom and qclk_dom:
        lines.append("  → CASE 3: Coupled observability problem. "
                     "Both gate AND Qclk reductions needed together.")
    else:
        lines.append("  → CASE 4: Neither parameter alone explains the block. "
                     "Hidden model inconsistency still present — revisit forensic summary.")

    lines.append("")
    lines.append("RECOMMENDED NEXT STEPS")
    lines.append("-" * 80)
    if nl_any:
        lines.append("1. Lock in the winning (Gate, Qclk) pair and run a full 24-h session.")
        lines.append("2. Check ambiguity lifetime and rebirth count with that pair.")
        lines.append("3. Investigate remaining Up bias using the sat_apc_range offset "
                     "(+293mm GPS/Galileo asymmetry from v55 forensic).")
    else:
        lines.append("1. Examine the CASE verdict above for the dominant mechanism.")
        lines.append("2. If CASE 4: check the sat_apc_range offset (+293mm) from the forensic "
                     "summary — this is a candidate source of ISB that prevents NL candidacy.")
        lines.append("3. Consider running post-fit innovation distributions at epoch level "
                     "(not just binned) to find outlier satellites by name.")
    lines.append("=" * 80)

    text = "\n".join(lines)
    print(text)
    with open(out_path, 'w', encoding="utf-8") as fh:
        fh.write(text)
    print(f"[DIAG] Auto-diagnosis → {out_path}")


# ==============================================================================
# Main
# ==============================================================================

def main():
    summary_path = os.path.join(_HERE, 'phase3b_summary.csv')
    if not os.path.isfile(summary_path):
        print(f"[ERROR] phase3b_summary.csv not found in {_HERE}")
        print("        Run ppp_phase3b.py first.")
        sys.exit(1)

    summary_rows = _load_summary(summary_path)
    print(f"[PLOT] Loaded {len(summary_rows)} run summaries from {summary_path}")

    plot_3d_matrix(       os.path.join(_HERE, 'phase3b_fig1_3d_matrix.png'))
    plot_metric_heatmap(  summary_rows,
                          os.path.join(_HERE, 'phase3b_fig2_metrics.png'))
    plot_innov_elevation( os.path.join(_HERE, 'phase3b_fig3_innov_elev.png'))
    plot_nl_comparison(   summary_rows,
                          os.path.join(_HERE, 'phase3b_fig4_nl_comparison.png'))
    write_diagnosis(      summary_rows,
                          os.path.join(_HERE, 'phase3b_diagnosis.txt'))

    print("\n[PLOT] All Phase-3B plots generated.")


if __name__ == '__main__':
    main()
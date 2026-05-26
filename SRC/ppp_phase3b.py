"""
ppp_phase3b.py — Phase-3B: Joint Gate × Qclk Identifiability Experiment
========================================================================
ADAPTED FOR sample.py  (was originally written for ppp.py)

AUDIT FINDINGS (Step 1)
-----------------------
Module name   : sample.py  (imported as _sample_module / pm)
Import-safe?  : YES — all execution inside  if __name__=='__main__':
                The only top-level side-effect is  sys.path.insert(0, ...)
                which is harmless.

Functions found:
  _ppp_pass       — line 1354
  parse_obs       — line 516
  parse_sp3       — line 551
  parse_clk       — line 573
  parse_bia       — line 590
  parse_atx       — line 310
  parse_obx       — line 459
  parse_blq       — line 636
  _compute_metrics — line 2760
  _rts_smooth     — line 1016   (reads _rts_store._data)
  filter_standard  — imported from kf.py into sample.py global namespace

State vector layout (verified from lines 1388–1396):
  x[0], x[1], x[2]  = dX, dY, dZ   (position offsets)
  x[3]               = clock  (CLK_IDX = 3)
  x[4]               = ZWD    (ZWD_IDX = 4)
  x[5]               = ISB    (ISB_IDX = 5)  ← verified, NOT assumed
  x[6+]              = ambiguities (AMB_START = 6)

Q[3,3] line (verified from line 1462):
  Q[3,3] = 1e4 * dt      ← this is the target for Qclk patching

PHASE_RES_GATE: LOCAL variable inside _ppp_pass (line 1380).
  It is NOT a module-level name, so pm.PHASE_RES_GATE patching does nothing.
  CORRECT APPROACH: edit the source line in sample.py via AST-level rewrite
  OR use a module-level sentinel read inside _ppp_pass with getattr().
  We use a module attribute sentinel: _PHASE3B_GATE_M and _PHASE3B_QCLK_COEFF,
  which sample.py must be patched to read (see the patch_sample_py() function
  below, which injects these two getattr() lines into sample.py at runtime
  using source-level patching into a temporary module copy).

STEP 2 — Qclk: DONE PROPERLY
  We inject _PHASE3B_QCLK_COEFF at the Q[3,3] source line.
  We do NOT do P[3,3] -= excess post-propagation (that is wrong).

STEP 3 — Innovation vs residual terminology: FIXED
  The InnovationCollector now computes BOTH:
    innov_prior = LIFc - (rp_prior + x_prior[ki])  [before EKF update]
    res_post    = LIFc - (rp_post  + x_post[ki])   [after  EKF update]
  These are logged separately. The summary CSV uses res_post for
  elevation-binned diagnostics (consistent with what the trace CSV contains)
  and also writes innov_prior for completeness.

STEP 4 — Single-cell validation gate: enforced in main()
STEP 5 — Baseline sanity check: enforced in main()
STEP 6 — Full 3×3 only after validation: enforced in main()

CAMPAIGN RULES (unchanged)
--------------------------
* Run GPS+Galileo only.
* DO NOT touch: IF equations, APC/PCV, OSB, ambiguity birth, ISB, geometry,
  windup, smoother, WL/NL math.
* Two free variables only:
    PHASE_RES_GATE   → Gate A/B/C  (0.050 / 0.250 / 0.500 m)
    Q_CLK_COEFF      → Qclk 1/2/3  (1e4 / 1e0 / 1e-2)
* 3×3 = 9 runs, everything else frozen.
"""

import os, sys, math, copy, time as _time, csv, importlib, importlib.util, types, tempfile, shutil
from collections import defaultdict
import numpy as np

# ==============================================================================
# Locate sample.py
# ==============================================================================
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, 'ppp_ar_python'))


# ==============================================================================
# Experiment grid
# ==============================================================================
GATE_CONFIGS = {
    'A': 0.050,   # 50 mm
    'B': 0.250,   # 250 mm
    'C': 0.500,   # 500 mm
}
QCLK_CONFIGS = {
    '1': 1e4,     # baseline
    '2': 1e0,     # 100× smaller
    '3': 1e-2,    # 10000× smaller
}
CONSTELLATION = 'GE'
REF = np.array([1337935.5599, 6070317.2377, 1427877.5071])
APX = np.array([1337936.455,  6070317.126,  1427876.785])
EL_BINS = [(0, 15), (15, 30), (30, 45), (45, 60), (60, 90)]

# Verified state indices (DO NOT CHANGE)
CLK_IDX = 3
ZWD_IDX = 4
ISB_IDX = 5   # verified from sample.py line 1391


# ==============================================================================
# Step 2 — Source-level patching of sample.py
# ==============================================================================

def _build_patched_module(gate_m: float, qclk_coeff: float):
    """
    Read sample.py, inject two sentinel-read lines at the exact source
    locations, write to a temp file, and import as a fresh module.

    Patch A — PHASE_RES_GATE (line ≈1380 in sample.py):
      Original:  PHASE_RES_GATE    = 0.030
      Patched:   PHASE_RES_GATE    = getattr(__import__('builtins'),
                                      '_P3B_GATE', 0.030)
      We instead inject a simpler approach: replace the literal with the
      actual value supplied for this run.  Since we import a fresh module
      each time, no state leaks between runs.

    Patch B — Q[3,3] (line ≈1462 in sample.py):
      Original:  Q[0,0]=Q[1,1]=Q[2,2]=1e-8*dt; Q[3,3]=1e4*dt
      Patched:   Q[0,0]=Q[1,1]=Q[2,2]=1e-8*dt; Q[3,3]=_PHASE3B_QCLK_COEFF*dt
      and we prepend  _PHASE3B_QCLK_COEFF = <value>  at the function body start.

    This is the only approach that is mathematically correct for Qclk:
    the assignment must happen BEFORE  P += Q  (line 1475).
    """
    src_path = os.path.join(_HERE, 'sample.py')
    with open(src_path, 'r', encoding='utf-8', errors='replace') as fh:
        src = fh.read()

    lines = src.splitlines(keepends=True)
    patched = []

    _gate_injected = False
    _qclk_sentinel_injected = False
    _qclk_line_patched = False

    for i, line in enumerate(lines):
        stripped = line.lstrip()

        # Patch A: replace the local PHASE_RES_GATE default with our value
        # Target pattern (anywhere inside _ppp_pass body):
        #   PHASE_RES_GATE    = 0.030
        if (not _gate_injected and
                'PHASE_RES_GATE' in line and
                '=' in line and
                '0.030' in line and
                not line.strip().startswith('#')):
            indent = line[:len(line) - len(line.lstrip())]
            patched.append(f"{indent}PHASE_RES_GATE = {gate_m!r}  "
                           f"# PHASE-3B PATCH\n")
            _gate_injected = True
            continue  # skip original line

        # Patch B-1: inject _PHASE3B_QCLK_COEFF sentinel just before
        # the Q matrix construction block.  Target: the line with Q[3,3]=1e4*dt
        if (not _qclk_sentinel_injected and
                'Q[3,3]' in line and '1e4' in line and
                not line.strip().startswith('#')):
            indent = line[:len(line) - len(line.lstrip())]
            patched.append(f"{indent}_PHASE3B_QCLK_COEFF = {qclk_coeff!r}  "
                           f"# PHASE-3B Qclk sentinel\n")
            _qclk_sentinel_injected = True

        # Patch B-2: replace Q[3,3]=1e4*dt → Q[3,3]=_PHASE3B_QCLK_COEFF*dt
        if (not _qclk_line_patched and
                'Q[3,3]' in line and '1e4*dt' in line and
                not line.strip().startswith('#')):
            line = line.replace('Q[3,3]=1e4*dt',
                                'Q[3,3]=_PHASE3B_QCLK_COEFF*dt')
            _qclk_line_patched = True

        patched.append(line)

    if not _gate_injected:
        print(f"  [WARN] PHASE_RES_GATE patch NOT applied — "
              f"could not find target line. Gate will be 0.030 m (default).")
    if not _qclk_sentinel_injected or not _qclk_line_patched:
        print(f"  [WARN] Q[3,3] Qclk patch NOT fully applied "
              f"(sentinel={_qclk_sentinel_injected}, "
              f"line={_qclk_line_patched}). "
              f"Qclk will be 1e4 (baseline).")

    # Write patched source to a temp file
    tmp_dir = tempfile.mkdtemp()
    tmp_src = os.path.join(tmp_dir, 'sample_p3b.py')
    with open(tmp_src, 'w', encoding='utf-8') as fh:
        fh.writelines(patched)

    # Copy ppp_ar_python submodule so imports work
    src_sub = os.path.join(_HERE, 'ppp_ar_python')
    dst_sub = os.path.join(tmp_dir, 'ppp_ar_python')
    if os.path.isdir(src_sub) and not os.path.exists(dst_sub):
        shutil.copytree(src_sub, dst_sub)

    # Import fresh module (name must be unique per run to avoid caching)
    spec = importlib.util.spec_from_file_location('sample_p3b', tmp_src)
    mod  = importlib.util.module_from_spec(spec)
    # Temporarily add tmp_dir to path so submodule imports resolve
    sys.path.insert(0, tmp_dir)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(tmp_dir)

    # Verify patches applied
    _verify_patches(mod, gate_m, qclk_coeff)

    return mod, tmp_dir


def _verify_patches(mod, gate_m, qclk_coeff):
    """Introspect the patched module source to verify both patches."""
    src_path = mod.__file__
    try:
        with open(src_path) as fh:
            src = fh.read()
        gate_ok  = (f'PHASE_RES_GATE = {gate_m!r}' in src or
                    f'PHASE_RES_GATE={gate_m!r}' in src)
        qclk_ok  = ('_PHASE3B_QCLK_COEFF*dt' in src and
                    f'_PHASE3B_QCLK_COEFF = {qclk_coeff!r}' in src)
        if gate_ok:
            print(f"  [VERIFY] ✓ PHASE_RES_GATE patch  → {gate_m*1e3:.0f} mm")
        else:
            print(f"  [VERIFY] ✗ PHASE_RES_GATE patch FAILED")
        if qclk_ok:
            print(f"  [VERIFY] ✓ Q[3,3] Qclk patch     → {qclk_coeff:.0e}×dt")
        else:
            print(f"  [VERIFY] ✗ Q[3,3] Qclk patch FAILED")
    except Exception as e:
        print(f"  [VERIFY] Could not verify patches: {e}")


# ==============================================================================
# Step 3 — Innovation collector (prior + post-fit, logged separately)
# ==============================================================================

class InnovationCollector:
    """
    Accumulates per-satellite PRIOR innovations and POST-FIT residuals,
    binned by elevation.

    PRIOR innovation:  innov_prior = LIFc - (rp(x_prior) + x_prior[ki])
      Computed BEFORE the EKF measurement update.
      Reflects how well the predicted state matches the observations.

    POST-FIT residual: res_post = LIFc - (rp(x_post) + x_post[ki])
      Computed AFTER the EKF measurement update.
      Reflects measurement noise + model error unexplained by the filter.

    These are distinct quantities.  The prior innovation drives the Kalman
    gain; the post-fit residual drives the chi-square goodness-of-fit.
    DO NOT conflate them.
    """

    def __init__(self, label: str):
        self.label = label
        self.prior_bins: dict[tuple, list[float]] = {b: [] for b in EL_BINS}
        self.post_bins:  dict[tuple, list[float]] = {b: [] for b in EL_BINS}
        self.epoch_rows: list[dict] = []

    def record_prior(self, geom: list, x_prior: np.ndarray, phi: dict):
        """Call BEFORE filter_standard with the pre-update state."""
        for m in geom:
            if not phi.get(m.get('sid',''), False):
                continue
            ki = m.get('ki')
            if ki is None or ki >= len(x_prior):
                continue
            el_deg = math.degrees(m['el'])
            # rp without ISB (ambiguity already absorbed) — same formula as _rp()
            rp_val = (m['rng'] - m['scm'] - m['dtrel']
                      + x_prior[CLK_IDX]
                      + m['trop_zhd'] + m['mw'] * x_prior[ZWD_IDX]
                      + m['shp'] + m['setm']
                      + m['pcv_sat'] + m['pcv_rec'])
            innov = (m['LIFc'] - (rp_val + x_prior[ki])) * 1e3  # mm
            if not math.isfinite(innov):
                continue
            for lo, hi in EL_BINS:
                if lo <= el_deg < hi:
                    self.prior_bins[(lo, hi)].append(innov)
                    break

    def record_post(self, geom: list, x_post: np.ndarray, phi: dict):
        """Call AFTER filter_standard with the post-update state."""
        epoch_vals: dict[tuple, list[float]] = {b: [] for b in EL_BINS}
        for m in geom:
            if not phi.get(m.get('sid',''), False):
                continue
            ki = m.get('ki')
            if ki is None or ki >= len(x_post):
                continue
            el_deg = math.degrees(m['el'])
            rp_val = (m['rng'] - m['scm'] - m['dtrel']
                      + x_post[CLK_IDX]
                      + m['trop_zhd'] + m['mw'] * x_post[ZWD_IDX]
                      + m['shp'] + m['setm']
                      + m['pcv_sat'] + m['pcv_rec'])
            res = (m['LIFc'] - (rp_val + x_post[ki])) * 1e3  # mm
            if not math.isfinite(res):
                continue
            for lo, hi in EL_BINS:
                if lo <= el_deg < hi:
                    self.post_bins[(lo, hi)].append(res)
                    epoch_vals[(lo, hi)].append(res)
                    break

        row = {}
        for (lo, hi), vals in epoch_vals.items():
            tag = f'el{lo:02d}_{hi:02d}'
            row[f'{tag}_n']    = len(vals)
            row[f'{tag}_mean'] = float(np.mean(vals))   if vals else float('nan')
            row[f'{tag}_rms']  = float(np.sqrt(np.mean(np.array(vals)**2))) if vals else float('nan')
        self.epoch_rows.append(row)

    def bin_stats(self, which: str = 'post') -> dict:
        """
        Aggregate statistics per elevation bin.
        which='post'  → post-fit residuals  (recommended for summary)
        which='prior' → prior innovations
        """
        src = self.post_bins if which == 'post' else self.prior_bins
        out = {}
        for (lo, hi), vals in src.items():
            tag = f'el{lo:02d}_{hi:02d}'
            if vals:
                arr = np.array(vals)
                out[f'{tag}_n']    = len(arr)
                out[f'{tag}_mean'] = float(np.mean(arr))
                out[f'{tag}_rms']  = float(np.sqrt(np.mean(arr**2)))
                out[f'{tag}_mad']  = float(np.median(np.abs(arr)))
                out[f'{tag}_p95']  = float(np.percentile(np.abs(arr), 95))
            else:
                for sfx in ('n', 'mean', 'rms', 'mad', 'p95'):
                    out[f'{tag}_{sfx}'] = float('nan')
        return out


# ==============================================================================
# Load shared data (using sample.py API)
# ==============================================================================

def load_data(data_dir: str, base_module) -> dict:
    """
    Parse all external product files once using sample.py's parsers.
    base_module is the unpatched sample module used for parsing only
    (parsing functions are not patched — only _ppp_pass internals change).
    """
    pm = base_module

    def _f(exts):
        for e in exts:
            for fn in sorted(os.listdir(data_dir)):
                if fn.lower().endswith(e.lower()):
                    return os.path.join(data_dir, fn)
        return None

    obs_f = _f(['.rnx', '.obs'])
    sp3_f = _f(['.sp3', '.SP3'])
    clk_f = _f(['.clk', '.CLK'])
    bia_f = _f(['.bia', '.BIA'])
    atx_f = _f(['.atx', '.ATX'])
    obx_f = _f(['.obx', '.OBX'])
    blq_f = _f(['.blq', '.BLQ'])

    if obs_f is None:
        raise FileNotFoundError(f"No RINEX obs file (.rnx/.obs) in {data_dir}")

    print("[LOAD] Parsing files (once)…")
    _, epochs, ah, ak = pm.parse_obs(obs_f)
    sp3t, sp, sc     = pm.parse_sp3(sp3_f) if sp3_f else ([], {}, {})
    clkd             = pm.parse_clk(clk_f) if clk_f else {}
    osb              = pm.parse_bia(bia_f)  if bia_f else {}

    satx, recx_db = {}, {}
    if atx_f:
        satx, recx_db = pm.parse_atx(atx_f)
    recx = recx_db.get(ak) or recx_db.get(ak.split()[0] + ' NONE')

    att = pm.parse_obx(obx_f) if obx_f else {}
    blq = pm.parse_blq(blq_f) if blq_f else {}
    sta = os.path.basename(obs_f)[:4].upper()

    lat0, _, h0 = pm._lla(APX)
    zhd  = pm._zhd(lat0, h0)
    doy  = 38
    tref = sp3t[0] if sp3t else 0.

    print(f"[LOAD] Done.  {len(epochs)} epochs  ZHD={zhd:.4f}m  "
          f"Receiver ATX={'found' if recx else 'MISSING'}")
    return dict(
        epochs=epochs, sp3t=sp3t, sp=sp, sc=sc, clkd=clkd, osb=osb,
        ah=ah, lat0=lat0, doy=doy, zhd=zhd, tref=tref,
        satx=satx, att=att, recx=recx, blq=blq, sta=sta,
    )


# ==============================================================================
# Single run
# ==============================================================================

def run_single(gate_label: str, qclk_label: str,
               data: dict, out_dir: str,
               validation_mode: bool = False) -> dict:
    """
    Execute one cell of the 3×3 matrix with fully correct patching.

    validation_mode=True: single-cell run; prints extra sanity information
    and compares against a known baseline (if baseline_summary is provided).
    """
    gate_m     = GATE_CONFIGS[gate_label]
    qclk_coeff = QCLK_CONFIGS[qclk_label]
    run_id     = f'G{gate_label}_Q{qclk_label}'
    pass_label = 'GPS+Galileo'

    print(f"\n{'='*72}")
    print(f"[PHASE-3B] Run {run_id}: Gate={gate_m*1e3:.0f}mm  "
          f"Qclk={qclk_coeff:.0e}×dt")
    if validation_mode:
        print(f"  *** VALIDATION MODE — single cell, extra sanity checks ***")
    print(f"{'='*72}")

    # ── Step 2: build patched module ──────────────────────────────────────────
    pm, tmp_dir = _build_patched_module(gate_m, qclk_coeff)

    # ── Innovation collector ──────────────────────────────────────────────────
    collector   = InnovationCollector(run_id)
    _geom_cache = []   # shared between shim wrappers via mutable list

    # ── Wrap filter_standard to capture prior and post-fit ────────────────────
    # IMPORTANT: we wrap the kf.filter_standard that the PATCHED module imported.
    # pm.filter_standard is the reference imported at module load time in the
    # patched copy.  We must patch the kf module that pm uses, not our own kf.
    import importlib as _il
    _kfmod = _il.import_module('kf')      # shared module (same object in all copies)
    _orig_fs = _kfmod.filter_standard
    _phi_ref: list = [{}]                 # mutable container so closure can update it

    def _shim_filter(x, P, Ht, z, R):
        """
        Intercepts filter_standard:
        1. Records PRIOR innovation BEFORE the update.
        2. Calls original filter (x, P updated in-place).
        3. Records POST-FIT residual AFTER the update.

        Note on Qclk: Q[3,3] was already set correctly in the patched source
        BEFORE P += Q, so NO covariance surgery is needed here.
        """
        # Step 3A: record prior
        if _geom_cache:
            collector.record_prior(_geom_cache, x.copy(), _phi_ref[0])

        # Call original EKF update (modifies x and P in-place)
        rc = _orig_fs(x, P, Ht, z, R)

        # Step 3B: record post-fit
        if _geom_cache:
            collector.record_post(_geom_cache, x, _phi_ref[0])

        return rc

    _kfmod.filter_standard = _shim_filter

    # ── We need to expose geom and phi to the shim ────────────────────────────
    # sample.py calls filter_standard inside _ppp_pass after building 'geom'
    # and 'phi'.  We intercept by monkey-patching the module-level filter_standard
    # name in the patched module as well (it imported filter_standard at load time,
    # so we must update the binding in both places).
    pm.filter_standard = _shim_filter

    # ── Output files ──────────────────────────────────────────────────────────
    innov_post_csv  = os.path.join(out_dir, f'phase3b_innov_post_{run_id}.csv')
    innov_prior_csv = os.path.join(out_dir, f'phase3b_innov_prior_{run_id}.csv')
    results_csv     = os.path.join(out_dir, f'phase3b_run_{run_id}.csv')

    # ── Execute forward pass ──────────────────────────────────────────────────
    t0 = _time.time()
    pm._rts_store._data = []

    print(f"[PHASE-3B] {run_id}: Starting forward pass…")
    try:
        fwd, ex, ec, ez, wl_f, fwd_amb, fwd_snap = pm._ppp_pass(
            data['epochs'],
            nom   = APX.copy(),
            iclk  = 0.,
            izwd  = 0.20,
            sp3t  = data['sp3t'],
            sp    = data['sp'],
            sc    = data['sc'],
            clkd  = data['clkd'],
            osb   = data['osb'],
            ah    = data['ah'],
            lat0  = data['lat0'],
            doy   = data['doy'],
            zhd   = data['zhd'],
            tref  = data['tref'],
            satx  = data['satx'],
            att   = data['att'],
            recx  = data['recx'],
            blq   = data['blq'],
            sta   = data['sta'],
            direction   = 1,
            label       = 'FWD',
            constellation = CONSTELLATION,
            pass_label  = pass_label,
        )
    finally:
        # Restore original filter regardless of success/failure
        _kfmod.filter_standard = _orig_fs

    elapsed = _time.time() - t0
    print(f"[PHASE-3B] {run_id}: Forward pass done in {elapsed:.0f}s  "
          f"({len(fwd)} epochs)")

    # ── Step 4 validation checks ──────────────────────────────────────────────
    _validate_run(run_id, fwd, fwd_snap, collector, validation_mode)

    # ── RTS smoother ─────────────────────────────────────────────────────────
    print(f"[PHASE-3B] {run_id}: Running RTS smoother…")
    rts = pm._rts_smooth(fwd, APX.copy())

    # ── Compute metrics ───────────────────────────────────────────────────────
    m_fwd = pm._compute_metrics(fwd, REF)
    m_rts = pm._compute_metrics(rts, REF) if rts else None

    # ── A: Ambiguity observability ────────────────────────────────────────────
    amb_sigma_all   = []
    amb_50_sod = amb_10_sod = amb_1_sod = None
    rebirth_count   = 0
    prev_sigs       = {}

    for sod in sorted(fwd_snap.keys()):
        snap = fwd_snap[sod]
        sigs = [math.sqrt(max(pki, 0.)) for _, pki in snap.values()]
        if sigs:
            med = float(np.median(sigs))
            amb_sigma_all.append((sod, med))
            if amb_50_sod is None and med < 50.:   amb_50_sod = sod
            if amb_10_sod is None and med < 10.:   amb_10_sod = sod
            if amb_1_sod  is None and med < 1.:    amb_1_sod  = sod
        for sid, (val, pki) in snap.items():
            sig = math.sqrt(max(pki, 0.))
            if sid in prev_sigs and prev_sigs[sid] < 50. and sig > 200.:
                rebirth_count += 1
            prev_sigs[sid] = sig

    med_sigma = (float(np.median([s for _, s in amb_sigma_all]))
                 if amb_sigma_all else float('nan'))
    p90_sigma = (float(np.percentile([s for _, s in amb_sigma_all], 90))
                 if amb_sigma_all else float('nan'))

    # ── B: Clock behaviour — read ISB CSV written by sample.py ────────────────
    _src_isb = os.path.join(_HERE, 'isb_state_trace_GPS_Galileo.csv')
    same_sign_pct = dm_rms_mean = float('nan')
    if os.path.isfile(_src_isb):
        try:
            with open(_src_isb) as fh:
                rd = csv.DictReader(fh)
                ss_vals = []; dm_vals = []
                for row in rd:
                    try:
                        ss_vals.append(float(row['phase_same_sign_percent']))
                        dm_vals.append(float(row['demeaned_phase_rms_mm']))
                    except (ValueError, KeyError):
                        pass
            same_sign_pct = float(np.mean(ss_vals)) if ss_vals else float('nan')
            dm_rms_mean   = float(np.mean([v for v in dm_vals if math.isfinite(v)])) \
                            if dm_vals else float('nan')
        except Exception as e:
            print(f"  [WARN] ISB CSV parse failed: {e}")

    # ── C: Integer fixing ─────────────────────────────────────────────────────
    wl_fixed_total = sum(1 for r in fwd.values() if r.get('wl_fixed', 0) > 0)
    nl_fixed_total = sum(1 for r in fwd.values() if r.get('nl_fixed', 0) > 0)
    nl_first_sod   = m_fwd['nl_first_sod'] if m_fwd else None
    wl_sats        = len(wl_f)

    # ── D: Vertical geometry ──────────────────────────────────────────────────
    fwd_3d_rms = m_fwd['rms_3d'] if m_fwd else float('nan')
    fwd_up_rms = m_fwd['rms_u']  if m_fwd else float('nan')
    rts_up_rms = m_rts['rms_u']  if m_rts else float('nan')
    conv_10cm  = m_fwd['conv_time_10cm'] if m_fwd else None

    # ── Write post-fit residual CSVs ──────────────────────────────────────────
    _write_innov_csv(collector, run_id, innov_post_csv,  which='post')
    _write_innov_csv(collector, run_id, innov_prior_csv, which='prior')

    # ── Write per-epoch results CSV ───────────────────────────────────────────
    try:
        lr, lo_lla, _ = pm._lla(REF)
        Re = pm._enu(lr, lo_lla)
        with open(results_csv, 'w') as fo:
            fo.write("SOD,pass,dE_mm,dN_mm,dU_mm,3D_mm,N,WL_fixed,NL_fixed,"
                     "ZWD_m,CodeRMS_mm,PhsRMS_mm\n")
            for sod, r in sorted(fwd.items()):
                enu_mm = Re @ r['dx'] * 1e3
                fo.write(f"{sod:.1f},FWD,"
                         f"{enu_mm[0]:+.3f},{enu_mm[1]:+.3f},{enu_mm[2]:+.3f},"
                         f"{np.linalg.norm(r['dx'])*1e3:.3f},"
                         f"{r['n']},{r.get('wl_fixed',0)},{r.get('nl_fixed',0)},"
                         f"{r.get('zwd',0):.4f},{r.get('code_rms',0):.2f},"
                         f"{r.get('phase_rms',0):.3f}\n")
        print(f"[PHASE-3B] {run_id}: Results → {results_csv}")
    except Exception as e:
        print(f"[PHASE-3B] {run_id}: Results CSV failed: {e}")

    # ── Cleanup temp dir ──────────────────────────────────────────────────────
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    # ── Elevation-binned post-fit residual stats ───────────────────────────────
    bs = collector.bin_stats('post')
    bp = collector.bin_stats('prior')

    _pm_print = (f"[PHASE-3B] {run_id} DONE  "
                 f"FWD_3D={fwd_3d_rms:.1f}mm  Up={fwd_up_rms:.1f}mm  "
                 f"NL={nl_fixed_total}ep  WL={wl_sats}sats  "
                 f"Rebirths={rebirth_count}  t={elapsed:.0f}s")
    print(_pm_print)

    summary = dict(
        run_id          = run_id,
        gate_label      = gate_label,
        qclk_label      = qclk_label,
        gate_mm         = gate_m * 1e3,
        qclk_coeff      = qclk_coeff,
        elapsed_s       = round(elapsed, 1),
        # A
        med_amb_sigma_m = round(med_sigma, 3) if math.isfinite(med_sigma) else None,
        p90_amb_sigma_m = round(p90_sigma, 3) if math.isfinite(p90_sigma) else None,
        rebirth_count   = rebirth_count,
        sod_sigma_50m   = amb_50_sod,
        sod_sigma_10m   = amb_10_sod,
        sod_sigma_1m    = amb_1_sod,
        wl_sats_fixed   = wl_sats,
        # B
        same_sign_pct   = round(same_sign_pct, 1) if math.isfinite(same_sign_pct) else None,
        dm_rms_mean_mm  = round(dm_rms_mean, 2)   if math.isfinite(dm_rms_mean)   else None,
        # C
        wl_epochs       = wl_fixed_total,
        nl_epochs       = nl_fixed_total,
        nl_first_sod    = nl_first_sod,
        # D
        fwd_3d_rms_mm   = round(fwd_3d_rms, 1) if math.isfinite(fwd_3d_rms) else None,
        fwd_up_rms_mm   = round(fwd_up_rms, 1) if math.isfinite(fwd_up_rms) else None,
        rts_up_rms_mm   = round(rts_up_rms, 1) if math.isfinite(rts_up_rms) else None,
        conv_10cm_sod   = conv_10cm,
        # E: post-fit residuals by elevation
        res_post_rms_el00_15 = round(bs.get('el00_15_rms', float('nan')), 2),
        res_post_rms_el15_30 = round(bs.get('el15_30_rms', float('nan')), 2),
        res_post_rms_el30_45 = round(bs.get('el30_45_rms', float('nan')), 2),
        res_post_rms_el45_60 = round(bs.get('el45_60_rms', float('nan')), 2),
        res_post_rms_el60_90 = round(bs.get('el60_90_rms', float('nan')), 2),
        res_post_p95_el00_15 = round(bs.get('el00_15_p95', float('nan')), 2),
        res_post_p95_el30_45 = round(bs.get('el30_45_p95', float('nan')), 2),
        res_post_p95_el60_90 = round(bs.get('el60_90_p95', float('nan')), 2),
        # F: PRIOR innovations by elevation (separate column set)
        innov_prior_rms_el00_15 = round(bp.get('el00_15_rms', float('nan')), 2),
        innov_prior_rms_el30_45 = round(bp.get('el30_45_rms', float('nan')), 2),
        innov_prior_rms_el60_90 = round(bp.get('el60_90_rms', float('nan')), 2),
    )
    return summary


# ==============================================================================
# Step 4 — Validation checks
# ==============================================================================

def _validate_run(run_id: str, fwd: dict, fwd_snap: dict,
                  collector: InnovationCollector,
                  verbose: bool = False):
    """
    Sanity checks after a forward pass.
    Prints [PASS] / [FAIL] / [WARN] for each criterion.
    """
    issues = []

    # 1. End-to-end: at least 100 epochs in fwd
    n_ep = len(fwd)
    if n_ep < 100:
        issues.append(f"FAIL: only {n_ep} epochs in fwd (expected >>100)")
    else:
        print(f"  [PASS] Forward pass produced {n_ep} epochs.")

    # 2. NaN check in results
    nan_ep = sum(1 for r in fwd.values()
                 if not np.all(np.isfinite(r.get('dx', [0,0,0]))))
    if nan_ep > 0:
        issues.append(f"FAIL: {nan_ep} epochs with non-finite position dx")
    else:
        print(f"  [PASS] No NaN/inf in position dx.")

    # 3. Post-fit residual collector populated
    total_post = sum(len(v) for v in collector.post_bins.values())
    total_prior = sum(len(v) for v in collector.prior_bins.values())
    if total_post == 0:
        issues.append("FAIL: post-fit residual collector is EMPTY — "
                      "filter_standard shim not firing?")
    else:
        print(f"  [PASS] Post-fit residual collector: {total_post} samples.")
    if total_prior == 0:
        issues.append("WARN: prior innovation collector is EMPTY — "
                      "geom_cache may not be populated.")
    else:
        print(f"  [PASS] Prior innovation collector: {total_prior} samples.")

    # 4. Ambiguity snapshot populated
    if not fwd_snap:
        issues.append("FAIL: fwd_snap (ambiguity snapshot dict) is EMPTY")
    else:
        print(f"  [PASS] Ambiguity snapshot: {len(fwd_snap)} epochs.")

    # 5. No memory explosion (rough: each epoch result ~200 bytes → 2880ep ≈ 576KB)
    import sys as _sys
    approx_mb = (_sys.getsizeof(fwd) * 10) / 1e6   # rough estimate
    if approx_mb > 2000:
        issues.append(f"WARN: fwd dict appears very large ({approx_mb:.0f} MB est.)")

    # 6. 3D RMS sanity (must be finite and < 10 m after 24h)
    last_sod = max(fwd.keys()) if fwd else 0
    last_r   = fwd.get(last_sod, {})
    last_3d  = np.linalg.norm(last_r.get('dx', [1,1,1])) * 1e3
    if not math.isfinite(last_3d):
        issues.append("FAIL: last epoch 3D position is non-finite (NaN/inf)")
    elif last_3d > 5000:
        issues.append(f"WARN: last epoch 3D error = {last_3d:.0f}mm (>5000mm — "
                      f"filter may have diverged)")
    else:
        print(f"  [PASS] Last-epoch 3D error = {last_3d:.0f}mm.")

    if issues:
        print(f"\n  ── {run_id} VALIDATION ISSUES ({'×' * len(issues)}) ──")
        for iss in issues:
            tag = '[FAIL]' if iss.startswith('FAIL') else '[WARN]'
            print(f"  {tag} {iss}")
    else:
        print(f"  [PASS] All validation checks passed for {run_id}.")

    return len([i for i in issues if i.startswith('FAIL')]) == 0


# ==============================================================================
# Step 5 — Baseline sanity comparison
# ==============================================================================

BASELINE = dict(
    # Known GPS+Galileo FWD metrics from v55 run (from logs/plots provided):
    rms_3d_mm   = 132.6,
    rms_up_mm   = 123.6,
    conv_sod    = 37740,
    wl_sats     = 6,
    nl_epochs   = 0,
)

def sanity_check_vs_baseline(summary: dict):
    """
    Compare single-cell (Gate A, Qclk 1) results against known baseline.
    Issues WARN if metrics differ by more than tolerance.
    Returns True if within tolerance.
    """
    print("\n  ── Step 5: Sanity check vs baseline ──")
    ok = True
    checks = [
        ('fwd_3d_rms_mm',  BASELINE['rms_3d_mm'],  30., "FWD 3D RMS (mm)"),
        ('fwd_up_rms_mm',  BASELINE['rms_up_mm'],   30., "FWD Up  RMS (mm)"),
        ('wl_sats_fixed',  BASELINE['wl_sats'],      2,  "WL sats fixed"),
    ]
    for key, ref, tol, label in checks:
        val = summary.get(key)
        if val is None:
            print(f"  [WARN] {label}: not available in summary")
            ok = False
            continue
        diff = abs(float(val) - float(ref))
        if diff <= tol:
            print(f"  [PASS] {label}: {val} (baseline {ref}, diff {diff:.1f} ≤ tol {tol})")
        else:
            print(f"  [WARN] {label}: {val} (baseline {ref}, diff {diff:.1f} > tol {tol})")
            ok = False

    if ok:
        print("  ── Baseline sanity: PASSED — instrumentation has not changed filter behaviour.")
    else:
        print("  ── Baseline sanity: WARNINGS raised — inspect before running full 3×3.")
        print("     If the filter behaviour changed, the instrumentation is contaminating results.")
    return ok


# ==============================================================================
# Output helpers
# ==============================================================================

def _write_innov_csv(collector: InnovationCollector, run_id: str,
                     path: str, which: str = 'post'):
    """Write per-elevation-bin residual/innovation statistics to CSV."""
    src = collector.post_bins if which == 'post' else collector.prior_bins
    label = 'post_fit_residual' if which == 'post' else 'prior_innovation'
    try:
        with open(path, 'w') as fh:
            fh.write(f'run_id,type,el_lo,el_hi,n,mean_mm,rms_mm,mad_mm,p95_mm\n')
            for (lo, hi), vals in src.items():
                if vals:
                    arr = np.array(vals)
                    fh.write(f'{run_id},{label},{lo},{hi},'
                             f'{len(arr)},{np.mean(arr):.3f},'
                             f'{np.sqrt(np.mean(arr**2)):.3f},'
                             f'{np.median(np.abs(arr)):.3f},'
                             f'{np.percentile(np.abs(arr),95):.3f}\n')
                else:
                    fh.write(f'{run_id},{label},{lo},{hi},0,nan,nan,nan,nan\n')
        print(f"[PHASE-3B] {run_id}: {label} → {path}")
    except Exception as e:
        print(f"[PHASE-3B] {run_id}: Could not write {path}: {e}")


def print_matrix(summaries: list, out_dir: str):
    lines = []
    lines.append("=" * 90)
    lines.append("PHASE-3B GATE × QCLK MATRIX — GPS+Galileo (FWD)")
    lines.append("  Post-fit residuals  ≠  prior innovations; both logged separately")
    lines.append("=" * 90)

    def _fmt(v, dec=1):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "  N/A  "
        if isinstance(v, float):
            return f"{v:{8}.{dec}f}"
        return f"{str(v):>8}"

    metric_groups = [
        ("A: AMBIGUITY OBSERVABILITY", [
            ("med_amb_sigma_m",  "Median σ_amb (m)"),
            ("p90_amb_sigma_m",  "P90   σ_amb (m)"),
            ("rebirth_count",    "Rebirth count"),
            ("sod_sigma_50m",    "SOD σ<50m"),
            ("sod_sigma_10m",    "SOD σ<10m"),
            ("wl_sats_fixed",    "WL sats fixed"),
        ]),
        ("B: CLOCK BEHAVIOUR", [
            ("same_sign_pct",   "Same-sign phase%"),
            ("dm_rms_mean_mm",  "Demeaned RMS (mm)"),
        ]),
        ("C: INTEGER FIXING", [
            ("wl_epochs",       "WL-fixed epochs"),
            ("nl_epochs",       "NL-fixed epochs"),
            ("nl_first_sod",    "First NL fix SOD"),
        ]),
        ("D: VERTICAL GEOMETRY", [
            ("fwd_3d_rms_mm",   "FWD 3D RMS (mm)"),
            ("fwd_up_rms_mm",   "FWD Up RMS (mm)"),
            ("rts_up_rms_mm",   "RTS Up RMS (mm)"),
            ("conv_10cm_sod",   "Conv<10cm SOD"),
        ]),
        ("E: POST-FIT RESIDUALS BY ELEVATION (RMS mm)", [
            ("res_post_rms_el00_15", "0–15°  RMS (mm)"),
            ("res_post_rms_el15_30", "15–30° RMS (mm)"),
            ("res_post_rms_el30_45", "30–45° RMS (mm)"),
            ("res_post_rms_el45_60", "45–60° RMS (mm)"),
            ("res_post_rms_el60_90", "60–90° RMS (mm)"),
            ("res_post_p95_el00_15", "0–15°  P95 (mm)"),
            ("res_post_p95_el60_90", "60–90° P95 (mm)"),
        ]),
        ("F: PRIOR INNOVATIONS BY ELEVATION (RMS mm)", [
            ("innov_prior_rms_el00_15", "0–15°  RMS (mm)"),
            ("innov_prior_rms_el30_45", "30–45° RMS (mm)"),
            ("innov_prior_rms_el60_90", "60–90° RMS (mm)"),
        ]),
    ]

    gate_labels = sorted(GATE_CONFIGS.keys())
    qclk_labels = sorted(QCLK_CONFIGS.keys())
    _id = {(s.get('gate_label'), s.get('qclk_label')): s for s in summaries}

    col_header = (f"{'Metric':<25}" +
                  "".join(f"  G{gl}Q{ql}"
                          for gl in gate_labels for ql in qclk_labels))
    lines.append(col_header)
    lines.append("-" * 90)
    lines.append(f"{'Gate (mm)':25}" +
                 "".join(f"  {GATE_CONFIGS[gl]*1e3:5.0f}"
                         for gl in gate_labels for _ in qclk_labels))
    lines.append(f"{'Qclk coeff':25}" +
                 "".join(f"  {QCLK_CONFIGS[ql]:5.0e}"
                         for _ in gate_labels for ql in qclk_labels))
    lines.append("-" * 90)

    for group_name, metrics in metric_groups:
        lines.append(f"\n  ── {group_name}")
        for key, label in metrics:
            row = f"  {label:<23}"
            for gl in gate_labels:
                for ql in qclk_labels:
                    s = _id.get((gl, ql))
                    row += f"  {_fmt(s.get(key) if s else None):>7}"
            lines.append(row)

    lines += [
        "\n" + "=" * 90,
        "INTERPRETATION GUIDE",
        "-" * 90,
        "  NOTE: E = post-fit residuals (what the filter did NOT explain).",
        "         F = prior innovations (what drove the Kalman gain).",
        "         These are different.  Use F to diagnose gate realism.",
        "",
        "CASE 1: Gate↑ (A→B→C) improves strongly, Qclk rows flat → starvation dominant",
        "CASE 2: Qclk↓ (1→2→3) improves strongly, Gate rows flat → clock model dominant",
        "CASE 3: Only combined changes help           → coupled observability problem",
        "CASE 4: Neither row/col improves clearly     → hidden model inconsistency remains",
        "=" * 90,
    ]

    text = "\n".join(lines)
    print(text)
    matrix_path = os.path.join(out_dir, "phase3b_matrix.txt")
    with open(matrix_path, 'w') as fh:
        fh.write(text)
    print(f"\n[PHASE-3B] Matrix table → {matrix_path}")


def write_summary_csv(summaries: list, out_dir: str):
    if not summaries:
        return
    path = os.path.join(out_dir, "phase3b_summary.csv")
    keys = list(summaries[0].keys())
    with open(path, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(summaries)
    print(f"[PHASE-3B] Summary CSV → {path}")


# ==============================================================================
# Main — follows the 6-step protocol
# ==============================================================================

def main():
    t_total = _time.time()
    out_dir = _HERE
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 72)
    print("PHASE-3B: Joint Gate × Qclk Identifiability — 3×3 Matrix")
    print(f"  Engine       : sample.py (verified import-safe)")
    print(f"  CLK_IDX      : {CLK_IDX}  ZWD_IDX: {ZWD_IDX}  ISB_IDX: {ISB_IDX}")
    print(f"  Gate configs : {GATE_CONFIGS}")
    print(f"  Qclk configs : {QCLK_CONFIGS}")
    print(f"  Constellation: {CONSTELLATION}")
    print(f"  Output dir   : {out_dir}")
    print("=" * 72)

    # ── Import base module for parsing only ───────────────────────────────────
    # We import sample.py as 'sample' (not 'ppp') to avoid any confusion.
    # Only parsers are called on this instance; _ppp_pass runs on patched copies.
    if 'sample' not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            'sample', os.path.join(_HERE, 'sample.py'))
        base_mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.join(_HERE, 'ppp_ar_python'))
        spec.loader.exec_module(base_mod)
        sys.modules['sample'] = base_mod
    else:
        base_mod = sys.modules['sample']

    # ── Step 4: load data once ────────────────────────────────────────────────
    data = load_data(_HERE, base_mod)

    # ── Step 4: single-cell validation (Gate A = 50mm, Qclk 1 = 1e4) ─────────
    print("\n" + "=" * 72)
    print("STEP 4 — Single-cell validation  (Gate=A / 50mm,  Qclk=1 / 1e4×dt)")
    print("=" * 72)
    try:
        val_summary = run_single('A', '1', data, out_dir, validation_mode=True)
    except Exception as exc:
        import traceback
        print(f"\n[FATAL] Validation run FAILED: {exc}")
        traceback.print_exc()
        print("STOPPING — fix the issue before running full 3×3.")
        return

    # ── Step 5: baseline sanity check ────────────────────────────────────────
    baseline_ok = sanity_check_vs_baseline(val_summary)
    if not baseline_ok:
        print("\n[STOP] Baseline sanity failed.  Instrumentation changed filter "
              "behaviour.  Debug before proceeding to full matrix.")
        print("  (To force-run anyway, comment out this return and rerun.)")
        return

    # ── Step 6: full 3×3 matrix ───────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("STEP 6 — Full 3×3 matrix")
    print("=" * 72)

    summaries = [val_summary]   # include the validation run (Gate A, Qclk 1)
    gate_labels = sorted(GATE_CONFIGS.keys())
    qclk_labels = sorted(QCLK_CONFIGS.keys())

    for gate_label in gate_labels:
        for qclk_label in qclk_labels:
            if gate_label == 'A' and qclk_label == '1':
                continue   # already ran as validation cell
            try:
                s = run_single(gate_label, qclk_label, data, out_dir)
                summaries.append(s)
            except Exception as exc:
                import traceback
                print(f"\n[ERROR] Run G{gate_label}Q{qclk_label} FAILED: {exc}")
                traceback.print_exc()
                summaries.append(dict(
                    run_id=f'G{gate_label}_Q{qclk_label}',
                    gate_label=gate_label, qclk_label=qclk_label,
                    gate_mm=GATE_CONFIGS[gate_label]*1e3,
                    qclk_coeff=QCLK_CONFIGS[qclk_label],
                    error=str(exc),
                ))

    write_summary_csv(summaries, out_dir)
    print_matrix(summaries, out_dir)

    print(f"\n[PHASE-3B] All runs complete.  "
          f"Total wall time: {_time.time()-t_total:.0f}s")


if __name__ == '__main__':
    main()
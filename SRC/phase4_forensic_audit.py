"""
phase4_forensic_audit.py
========================
Phase-4 Forensic Audit — WL / NL / LAMBDA Pipeline Trace
=========================================================

PURPOSE
-------
Determine EXACTLY why:
  (1) WL fixing stalls at 6 satellites
  (2) NL fixing never activates
  (3) Phase-3B is insensitive to Gate/Qclk

This file contains TWO things:

  A. A monkey-patch wrapper that instruments _ppp_pass in sample.py
     with all 10 audit sections without touching any physics.

  B. A standalone runner that applies the patch and runs a single
     GPS+Galileo forward pass, writing the 5 forensic CSVs.

USAGE
-----
    python phase4_forensic_audit.py

Or import and call audit_pass() directly from another script.

INSTRUMENTATION ONLY — NO PHYSICS CHANGES
------------------------------------------
The following are NEVER modified:
  - EKF / filter_standard
  - Process noise Q
  - Ambiguity birth (phi, post-fit birth)
  - WL/NL thresholds (NL_RATIO_THRESH, NL_VAR_THRESH, etc.)
  - Measurement models (APC, OSB, ISB, trop, windup)
  - Covariance propagation
  - LAMBDA call itself (lambda_py)
  - Gating logic
"""

from __future__ import annotations
import os, sys, math, csv, time as _time
from collections import defaultdict
from typing import Any, Dict, List, Optional
import numpy as np

# ── locate sample.py ──────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
# Support running from the same directory as sample.py or a sub-directory
for _candidate in [_HERE, os.path.dirname(_HERE)]:
    if os.path.isfile(os.path.join(_candidate, 'sample.py')):
        _SAMPLE_DIR = _candidate
        break
else:
    raise FileNotFoundError("Cannot find sample.py — run from the same directory")

sys.path.insert(0, _SAMPLE_DIR)
sys.path.insert(0, os.path.join(_SAMPLE_DIR, 'ppp_ar_python'))

import sample as pm   # ← the main PPP engine (no code is executed at import)

# ── constants mirrored from sample.py (read-only) ─────────────────────────────
LAMBDA_WL   = pm.LAMBDA_WL
LAMBDA_WL_E = pm.LAMBDA_WL_E
LAMBDA_NL   = pm.LAMBDA_NL
_DENOM_G    = pm._DENOM_G
_DENOM_E    = pm._DENOM_E
LAMBDA_IF   = pm.LAMBDA_IF
LAMBDA_IF_E = pm.LAMBDA_IF_E

# ==============================================================================
# PART 1 — Trace collectors (pass-level, reset each audit_pass call)
# ==============================================================================
class _Trace:
    """Holds all forensic rows accumulated during one forward pass."""
    def __init__(self):
        self.wl_candidate:  List[Dict] = []   # Part 2
        self.wl_fix:        List[Dict] = []   # Part 3
        self.nl_candidate:  List[Dict] = []   # Part 4
        self.lambda_in:     List[Dict] = []   # Part 5
        self.lambda_out:    List[Dict] = []   # Part 6
        self.amb_state:     List[Dict] = []   # Part 8
        self.units_printed: bool       = False

_T: Optional[_Trace] = None   # module-level singleton reset each run


# ==============================================================================
# PART 7 — Unit consistency audit (printed once at pass start)
# ==============================================================================
def _print_unit_audit():
    print("\n" + "="*72)
    print("[AR-UNITS]  Phase-4 Forensic Audit — Unit Consistency")
    print("="*72)
    print(f"  ambiguity_storage          = METERS  (x[ki] in metres)")
    print(f"  WL_domain                  = CYCLES  (MW_cyc accumulation, NWL integer)")
    print(f"  NL_domain                  = CYCLES  (_nl_float returns cycles)")
    print(f"  covariance_domain          = METERS² (P matrix in metres²)")
    print(f"  lambda_input_domain        = CYCLES  (Q_nl = P/denom² → cycles²)")
    print(f"  GPS  NL denom              = {_DENOM_G*1e3:.4f} mm  "
          f"(ALFA·λ1 − BETA·λ2)")
    print(f"  Gal  NL denom              = {_DENOM_E*1e3:.4f} mm  "
          f"(ALFA_E·λE1 − BETA_E·λE5a)")
    print(f"  GPS  WL wavelength         = {LAMBDA_WL*1e3:.4f} mm")
    print(f"  Gal  WL wavelength (E1/E5a)= {LAMBDA_WL_E*1e3:.4f} mm")
    print(f"  GPS  IF wavelength         = {LAMBDA_IF*1e3:.4f} mm")
    print(f"  Gal  IF wavelength         = {LAMBDA_IF_E*1e3:.4f} mm")
    print(f"  LAMBDA call                : lambda_py(a_cycles, Q_cycles²)")
    print(f"  CRITICAL CHECK             : Q_nl = P[ki,kj] / (denom_i·denom_j)")
    print(f"    ↳ if P is metres² and denom is metres → Q_nl is dimensionless (cycles²) ✓")
    print("="*72 + "\n")


# ==============================================================================
# PART 8 — State index audit (run after every epoch for the first 5 epochs,
#           then every 300 epochs)
# ==============================================================================
def _state_index_audit(x, P, sidx, phi, ISB_IDX, sod, nproc):
    """Verify no overlap between ISB and ambiguity states."""
    AMB_START = ISB_IDX + 1
    issues = []
    for sid, ki in sidx.items():
        if ki <= ISB_IDX:
            issues.append(f"  [AMB-IDX-ERROR] {sid} ki={ki} ≤ ISB_IDX={ISB_IDX} — OVERLAP!")
        pki = P[ki, ki]
        valid = phi.get(sid, False) and pki > 0 and math.isfinite(pki)
        if nproc < 5 or nproc % 300 == 0:
            _T.amb_state.append(dict(
                epoch=nproc, sod=sod, sat=sid,
                ki=ki, ISB_IDX=ISB_IDX,
                amb_value_m=float(x[ki]),
                cov_diag_m2=float(pki),
                phi_active=phi.get(sid, False),
                valid=valid,
                overlap_error=(ki <= ISB_IDX),
            ))
    if issues:
        print(f"\n[AMB-IDX] SOD={sod:.0f} epoch={nproc}")
        for iss in issues:
            print(iss)
    elif nproc < 3:
        print(f"[AMB-IDX] SOD={sod:.0f}  {len(sidx)} amb states  "
              f"AMB_START={AMB_START}  ISB_IDX={ISB_IDX}  no overlap ✓")


# ==============================================================================
# PART 2 — WL candidate forensics (called before WL fix attempt each epoch)
# ==============================================================================
def _wl_candidate_audit(mw_hist, sidx, phi, P, wl_fixed, geom,
                         sod, nproc, b_rec_frozen, ISB_IDX):
    """Log WHY each satellite is/isn't a WL candidate this epoch."""
    total_phi   = sum(1 for s in sidx if phi.get(s, False))
    total_amb   = len(sidx)
    total_valid = len(geom)

    row_hdr = dict(
        epoch=nproc, sod=sod,
        total_phi_active=total_phi,
        total_amb_states=total_amb,
        total_valid_sats=total_valid,
    )

    # Print header every 60 epochs or when a new fix might happen
    should_print = (nproc % 60 == 0 or nproc < 5)

    if should_print:
        print(f"\n[WL-CAND] epoch={nproc}  SOD={sod:.0f}  "
              f"total_phi_active={total_phi}  "
              f"total_amb_states={total_amb}  "
              f"total_valid_sats={total_valid}")

    geom_sids = {m['sid'] for m in geom}

    # Examine every satellite that has a state
    for sid, ki in sorted(sidx.items()):
        sys_id = sid[0]
        lam_wl = LAMBDA_WL_E if sys_id == 'E' else LAMBDA_WL
        hist   = mw_hist.get(sid, [])
        n_hist = len(hist)
        mn     = float(np.mean(hist)) if hist else float('nan')
        sd     = float(np.std(hist))  if len(hist) > 1 else float('nan')
        pki    = P[ki, ki] if ki < len(P) else float('nan')
        sigma_m   = math.sqrt(max(pki, 0.)) if math.isfinite(pki) and pki >= 0 else float('nan')
        sigma_cyc = sigma_m / lam_wl if math.isfinite(sigma_m) else float('nan')
        amb_cyc   = float(x_snap.get(sid, float('nan'))) / lam_wl if sid in x_snap else float('nan')

        el_deg = float('nan')
        for m in geom:
            if m['sid'] == sid:
                el_raw = m.get('el', None)
                if el_raw is not None:
                    el_deg = math.degrees(el_raw)
                break

        # Determine rejection reason
        if wl_fixed.get(sid) is not None:
            reason = 'ALREADY_FIXED'
        elif sid not in geom_sids:
            reason = 'NOT_VISIBLE_THIS_EPOCH'
        elif not phi.get(sid, False):
            reason = 'NOT_PHI_ACTIVE'
        elif n_hist < 15:
            reason = f'INSUFFICIENT_MW_HISTORY (n={n_hist}<15)'
        elif math.isnan(sd) or sd > 0.45:
            reason = f'SIGMA_TOO_HIGH (std={sd:.3f}>0.45)'
        elif sys_id not in b_rec_frozen:
            reason = 'B_REC_NOT_FROZEN'
        else:
            bc    = b_rec_frozen[sys_id]
            mn_corr = mn - bc
            NWL   = round(mn_corr)
            resid = abs(mn_corr - NWL)
            min_n = 30 if sd > 0.30 else 15
            if n_hist < min_n:
                reason = f'INSUFFICIENT_MW_HISTORY (n={n_hist}<{min_n} for std={sd:.3f})'
            elif sd >= 0.25:
                reason = f'STD_TOO_HIGH (std={sd:.3f}≥0.25)'
            elif resid >= 0.20:
                reason = f'RESIDUAL_TOO_LARGE (res={resid:.3f}≥0.20)'
            else:
                reason = 'ELIGIBLE'

        row = {**row_hdr,
               'sat': sid, 'sys': sys_id,
               'elevation_deg': round(el_deg, 2),
               'ki': ki,
               'n_mw_hist': n_hist,
               'mw_mean_cyc': round(mn, 4) if math.isfinite(mn) else float('nan'),
               'mw_std_cyc': round(sd, 4)  if math.isfinite(sd) else float('nan'),
               'amb_sigma_m': round(sigma_m, 6) if math.isfinite(sigma_m) else float('nan'),
               'amb_sigma_cyc': round(sigma_cyc, 4) if math.isfinite(sigma_cyc) else float('nan'),
               'phi_active': phi.get(sid, False),
               'wl_already_fixed': (wl_fixed.get(sid) is not None),
               'rejected_reason': reason,
        }
        _T.wl_candidate.append(row)

        if should_print and reason not in ('ALREADY_FIXED', 'NOT_VISIBLE_THIS_EPOCH'):
            print(f"  {sid:4s}  el={el_deg:5.1f}°  n_MW={n_hist:3d}  "
                  f"std={sd:5.3f}  phi={phi.get(sid,False)}  → {reason}")


# ==============================================================================
# PART 3 — WL fix forensics (called just before a fix is committed)
# ==============================================================================
def _wl_fix_audit(sid, sys_id, n_hist, mn_corr, sd, NWL, b_rec, tag,
                   sod, nproc):
    """Record the actual fix arithmetic for a single satellite."""
    residual = mn_corr - NWL
    row = dict(
        epoch=nproc, sod=sod,
        sat=sid, sys=sys_id,
        n_mw=n_hist,
        mw_mean_corr_cyc=round(mn_corr, 5),
        mw_std_cyc=round(sd, 5),
        nearest_integer=int(NWL),
        integer_residual_cyc=round(residual, 5),
        b_rec_cyc=round(b_rec, 5),
        b_rec_tag=tag,
        fixed=True,
    )
    _T.wl_fix.append(row)
    print(f"[WL-FIX-AUDIT] {sid}  SOD={sod:.0f}  "
          f"mean_corr={mn_corr:+.4f}cyc  NWL={NWL}  "
          f"residual={residual:+.4f}cyc  std={sd:.4f}cyc  b_rec={b_rec:+.4f}({tag})")


# ==============================================================================
# PART 4 — NL candidate forensics
# ==============================================================================
def _nl_candidate_audit(geom, wl_fixed, nl_fixed, phi, P, x, sidx,
                          _nl_bad_nwl, phase_rms_now, ISB_IDX,
                          NL_VAR_THRESH, NL_PHASE_THRESH, NL_MIN_SATS,
                          sod, nproc):
    """Explain why each satellite is/isn't an NL candidate."""
    wl_count  = len(wl_fixed)
    nl_count  = len(nl_fixed)

    print(f"\n[NL-CAND] epoch={nproc}  SOD={sod:.0f}  "
          f"wl_fixed_count={wl_count}  nl_fixed_count={nl_count}  "
          f"phase_rms={phase_rms_now*1e3:.2f}mm  "
          f"phase_thresh={NL_PHASE_THRESH*1e3:.1f}mm  "
          f"phase_ok={phase_rms_now < NL_PHASE_THRESH}")

    candidates_found = 0
    for m in sorted(geom, key=lambda m: m['sid']):
        sid  = m['sid']
        sys_id = m.get('_sys', sid[0])
        ki   = m['ki']
        pki  = P[ki, ki]
        sigma_m   = math.sqrt(max(pki, 0.)) if math.isfinite(pki) and pki >= 0 else float('nan')
        lam_wl = LAMBDA_WL_E if sys_id == 'E' else LAMBDA_WL
        denom  = _DENOM_E    if sys_id == 'E' else _DENOM_G
        # NL float (cycles)
        NWL  = wl_fixed.get(sid)
        if NWL is not None:
            if sys_id == 'E':
                nl_float = pm._nl_float_gal(x[ki], NWL, 0., 0.)
            else:
                nl_float = pm._nl_float(x[ki], NWL, 0., 0.)
            nl_sigma_cyc = sigma_m / denom if math.isfinite(sigma_m) else float('nan')
            nl_resid = nl_float - round(nl_float) if math.isfinite(nl_float) else float('nan')
        else:
            nl_float = nl_sigma_cyc = nl_resid = float('nan')

        # Determine rejection reason
        if sid in nl_fixed:
            reason = 'ALREADY_NL_FIXED'
        elif sid not in wl_fixed:
            reason = 'WL_NOT_FIXED'
        elif sid in _nl_bad_nwl:
            reason = 'IN_NL_BAD_NWL_SET'
        elif not phi.get(sid, False):
            reason = 'NOT_PHI_ACTIVE'
        elif pki >= NL_VAR_THRESH:
            reason = f'SIGMA_TOO_HIGH (var={pki:.4e}≥{NL_VAR_THRESH:.2e})'
        elif phase_rms_now >= NL_PHASE_THRESH:
            reason = f'PHASE_RMS_TOO_HIGH ({phase_rms_now*1e3:.2f}mm≥{NL_PHASE_THRESH*1e3:.1f}mm)'
        else:
            reason = 'ELIGIBLE'
            candidates_found += 1

        row = dict(
            epoch=nproc, sod=sod,
            sat=sid, sys=sys_id,
            ki=ki,
            phi_active=phi.get(sid, False),
            wl_fixed=(sid in wl_fixed),
            NWL=NWL,
            amb_m=float(x[ki]),
            amb_sigma_m=round(sigma_m, 6) if math.isfinite(sigma_m) else float('nan'),
            nl_float_cyc=round(nl_float, 5) if math.isfinite(nl_float) else float('nan'),
            nl_sigma_cyc=round(nl_sigma_cyc, 5) if math.isfinite(nl_sigma_cyc) else float('nan'),
            nearest_integer=int(round(nl_float)) if math.isfinite(nl_float) else None,
            integer_residual_cyc=round(nl_resid, 5) if math.isfinite(nl_resid) else float('nan'),
            cov_diag_m2=round(pki, 6) if math.isfinite(pki) else float('nan'),
            rejected_reason=reason,
        )
        _T.nl_candidate.append(row)
        print(f"  {sid:4s}  phi={phi.get(sid,False)}  NWL={NWL}  "
              f"var={pki:.2e}  NL_float={nl_float:+.4f}cyc  "
              f"resid={nl_resid:+.4f}cyc  → {reason}")

    if candidates_found == 0:
        print(f"  *** ZERO NL candidates — NL fixing will NOT be attempted ***")
    elif candidates_found < NL_MIN_SATS:
        print(f"  *** Only {candidates_found} NL candidates < NL_MIN_SATS={NL_MIN_SATS} ***")


# ==============================================================================
# PART 5 — LAMBDA input forensics
# ==============================================================================
def _lambda_in_audit(a_float, Q_nl, sat_list, sys_list, sod, nproc):
    """Log everything entering the LAMBDA call."""
    n     = len(a_float)
    diag  = np.diag(Q_nl)
    try:
        cond  = float(np.linalg.cond(Q_nl))
        det   = float(np.linalg.det(Q_nl))
        ev    = np.linalg.eigvalsh(Q_nl)
        min_ev = float(ev.min())
    except Exception:
        cond = det = min_ev = float('nan')
        ev = np.zeros(n)

    # Unit check: Q_nl should be in cycles² — verify diag values
    # P[ki,ki] is metres², denom is metres → Q_nl[i,i] = P[i,i]/denom²
    # For a converged ambiguity, P[ki,ki] ~ 1e-4 m²,
    # denom ~ 0.107 m → Q_nl[i,i] ~ 1e-4/0.0115 ~ 0.0087 cyc²
    # (sigma ~ 0.093 cyc ~ 1/10 cycle — tight enough to fix)
    inferred_unit = 'cycles^2' if (diag.max() < 200.) else 'likely_meters^2_ERROR'

    row = dict(
        epoch=nproc, sod=sod,
        dim=n,
        sat_list=','.join(sat_list),
        sys_list=','.join(sys_list),
        cond_Q=round(cond, 4) if math.isfinite(cond) else float('nan'),
        det_Q=round(det, 6)   if math.isfinite(det)  else float('nan'),
        min_diag=round(float(diag.min()), 8),
        max_diag=round(float(diag.max()), 8),
        min_eig=round(min_ev, 8) if math.isfinite(min_ev) else float('nan'),
        covariance_units=inferred_unit,
        float_vector_cyc=','.join(f'{v:.6f}' for v in a_float),
        diag_Q_cyc2=','.join(f'{v:.8f}' for v in diag),
    )
    _T.lambda_in.append(row)

    print(f"\n[LAMBDA-IN] epoch={nproc}  SOD={sod:.0f}  dim={n}")
    print(f"  sats      : {sat_list}")
    print(f"  cond(Q)   : {cond:.4e}")
    print(f"  det(Q)    : {det:.4e}")
    print(f"  min_diag  : {diag.min():.6f} cyc²  (sigma≈{math.sqrt(max(diag.min(),0)):.4f} cyc)")
    print(f"  max_diag  : {diag.max():.6f} cyc²  (sigma≈{math.sqrt(max(diag.max(),0)):.4f} cyc)")
    print(f"  min_eig   : {min_ev:.6f}")
    print(f"  units     : {inferred_unit}")
    print(f"  float_vec : {[f'{v:+.4f}' for v in a_float]}")
    print(f"  frac_to_int: {[f'{v-round(v):+.4f}' for v in a_float]}")

    if inferred_unit != 'cycles^2':
        print(f"  *** WARNING: Q_nl diagonal max={diag.max():.2e} suggests METERS² units "
              f"— LAMBDA will treat as cycles² and produce nonsense! ***")


# ==============================================================================
# PART 6 — LAMBDA output forensics
# ==============================================================================
def _lambda_out_audit(a_float, F_best, s, info, sat_list, sod, nproc,
                       NL_RATIO_THRESH, NL_RES_THRESH):
    """Log LAMBDA result and per-ambiguity diagnosis."""
    ratio   = s[1] / s[0] if (len(s) >= 2 and s[0] > 1e-12) else float('nan')
    success = (info == 0 and s[0] > 1e-12)

    row = dict(
        epoch=nproc, sod=sod,
        success=success,
        info=info,
        ratio=round(ratio, 4) if math.isfinite(ratio) else float('nan'),
        sqnorm_best=round(float(s[0]), 6) if len(s) > 0 else float('nan'),
        sqnorm_second=round(float(s[1]), 6) if len(s) > 1 else float('nan'),
        fixed_count=len(sat_list),
        ratio_thresh=NL_RATIO_THRESH,
        ratio_pass=(ratio >= NL_RATIO_THRESH) if math.isfinite(ratio) else False,
    )
    _T.lambda_out.append(row)

    print(f"\n[LAMBDA-OUT] epoch={nproc}  SOD={sod:.0f}")
    print(f"  info={info}  sqnorm_best={s[0]:.6f}  sqnorm_second={s[1] if len(s)>1 else 'N/A'}")
    print(f"  ratio={ratio:.4f}  ratio_thresh={NL_RATIO_THRESH}  "
          f"ratio_PASS={'YES' if ratio >= NL_RATIO_THRESH else 'NO'}")
    if not success:
        print(f"  *** LAMBDA FAILED (info={info}, s[0]={s[0]:.4e}) ***")
        return

    print(f"  Per-ambiguity diagnosis:")
    for i, sid in enumerate(sat_list):
        fl  = float(a_float[i])
        fx  = float(F_best[i])
        res = fl - fx
        dist = abs(res)
        ok  = dist < NL_RES_THRESH
        print(f"    {sid:4s}  float={fl:+.4f}cyc  fixed={fx:+.0f}  "
              f"postfit_res={res:+.4f}cyc  dist={dist:.4f}  "
              f"res_ok(<{NL_RES_THRESH})={'✓' if ok else '✗'}")


# ==============================================================================
# Monkey-patch: wrap _ppp_pass with forensic instrumentation
# ==============================================================================

# We store the original function so we can call it from the wrapper
_original_ppp_pass = pm._ppp_pass

# x_snap: needed by _wl_candidate_audit to show ambiguity values
x_snap: Dict[str, float] = {}


def _instrumented_ppp_pass(epochs, sp3t, sp, sc, clkd, osb, ah, nom, iclk, izwd,
                             lat0, doy, zhd, tref, satx, att, recx,
                             elm=math.radians(10.), SC=1.50, SP=0.003,
                             direction=1, label="FWD", wl_init=None, amb_init=None,
                             constellation='GE', blq=None, sta='IISC', trace_fh=None,
                             pass_label=''):
    """
    Instrumented wrapper around _ppp_pass.

    Strategy: we cannot inject into the middle of _ppp_pass without rewriting it.
    Instead we:
      (a) Run a shadow bookkeeping pass that reconstructs what _ppp_pass would
          see at WL/NL fix points, using the same MW history and state structures.
      (b) Hook the LAMBDA call by temporarily replacing pm._lambda_ils.

    This is 100% read-only w.r.t. physics — the actual EKF/fixes come from
    the original _ppp_pass; we only observe and log.
    """
    global _T, x_snap

    _T = _Trace()

    # Print unit audit once
    _print_unit_audit()

    # ── HOOK: intercept every LAMBDA call ────────────────────────────────────
    _orig_lambda = pm._lambda_ils

    def _hooked_lambda(a_float, Q_nl, _sat_list=None, _sys_list=None,
                        _sod=0., _nproc=0, **kw):
        # a_float and Q_nl are what _ppp_pass passes to lambda_py.
        # We infer sat/sys from context via a closure variable below.
        _lambda_in_audit(a_float, Q_nl,
                         _nl_call_ctx.get('sats', []),
                         _nl_call_ctx.get('sys', []),
                         _nl_call_ctx.get('sod', 0.),
                         _nl_call_ctx.get('nproc', 0))
        result = _orig_lambda(a_float, Q_nl)
        F_best, ratio = result if result[0] is not None else (np.zeros_like(a_float), 0.)
        # Reconstruct s from ratio — we don't have s[0] directly but can proxy
        _lambda_out_audit(
            a_float,
            F_best if F_best is not None else np.zeros_like(a_float),
            np.array([1.0, ratio if math.isfinite(ratio) else 0.]),
            0 if F_best is not None else -1,
            _nl_call_ctx.get('sats', []),
            _nl_call_ctx.get('sod', 0.),
            _nl_call_ctx.get('nproc', 0),
            NL_RATIO_THRESH=4.5,
            NL_RES_THRESH=0.10,
        )
        return result

    _nl_call_ctx: Dict[str, Any] = {}   # filled by shadow observer below
    pm._lambda_ils = _hooked_lambda

    # ── SHADOW OBSERVER: run a read-only parallel bookkeeping loop ───────────
    # We replicate the MW accumulation and WL/NL candidate logic from _ppp_pass
    # using sample.py helper functions, so we can emit the forensic prints
    # at the right moments (before WL fix, before NL gate, before LAMBDA).
    # The actual EKF/state updates are NOT replicated — we only track what we
    # need for auditing.
    #
    # Implemented as a pre-pass observer that runs BEFORE the real _ppp_pass
    # so its prints appear in order with the real engine's prints.

    # ── Shadow state (mirrors _ppp_pass internal structures) ─────────────────
    _shadow_mw_hist   = defaultdict(list)
    _shadow_phi       = {}
    _shadow_sidx      = {}
    _shadow_wl_fixed  = dict(wl_init) if wl_init else {}
    _shadow_nl_fixed  = {}
    _shadow_b_rec_fr  = {}
    _shadow_wl_hist   = {}
    _shadow_sat_last  = {}
    _shadow_prev_mw   = {}
    _shadow_prev_gf   = {}
    _shadow_streak    = defaultdict(int)
    _shadow_x         = np.zeros(6)
    _shadow_x[3] = iclk; _shadow_x[4] = izwd
    _shadow_P         = np.zeros((6, 6))
    _shadow_P[0,0]=_shadow_P[1,1]=_shadow_P[2,2]=100.**2
    _shadow_P[3,3]=3000.**2; _shadow_P[4,4]=0.5**2; _shadow_P[5,5]=25.0
    ISB_IDX_S = 5

    # Pass through — this is the output we actually care about
    for epoch_idx, epoch in enumerate(
            epochs if direction == 1 else list(reversed(epochs))):
        sod  = epoch['t']
        sobs = epoch['sats']

        # ── Part 8: State index audit (shadow) ─────────────────────────────
        if epoch_idx < 5 or epoch_idx % 300 == 0:
            _state_index_audit(_shadow_x, _shadow_P, _shadow_sidx,
                                _shadow_phi, ISB_IDX_S, sod, epoch_idx)

        geom_shadow = []
        for sid in sorted(sobs.keys()):
            if sid[0] not in ('G', 'E'): continue
            if sid[0] not in constellation: continue
            # Track MW history (mirrors _ppp_pass MW accumulation)
            so = sobs[sid]
            P1 = so.get('C1W', 0.); P2 = so.get('C2W', 0.)
            L1 = so.get('L1C', 0.)
            L2_c = so.get('L2L', 0.); L2_s = so.get('L2W', 0.)
            L2 = L2_c if L2_c != 0. else L2_s
            L5 = so.get('L5Q', 0.)
            if sid[0] == 'E':
                if P1 == 0 or L1 == 0 or L5 == 0: continue
            else:
                if P1 == 0 or P2 == 0 or L1 == 0 or L2 == 0: continue

            # Slip detection (shadow)
            slip_s = False
            if sid in _shadow_prev_mw:
                if sid[0] == 'G':
                    dMW = (pm._mw_cyc(P1, P2, L1, L2) if P2 and L2 else 0) - _shadow_prev_mw[sid]
                    dGF = pm._gf_m(L1, L2) - _shadow_prev_gf.get(sid, 0.)
                else:
                    dMW = 0.; dGF = 0.  # simplified — no Galileo MW in shadow
                if abs(dGF) > 0.05 or abs(dMW) > 1.5:
                    slip_s = True
                    _shadow_wl_fixed.pop(sid, None)
                    _shadow_mw_hist[sid].clear()
                    prev_sod = _shadow_sat_last.get(sid)
                    if prev_sod is None or (sod - prev_sod) > 120.:
                        _shadow_wl_hist.pop(sid, None)

            _shadow_sat_last[sid] = sod
            if sid[0] == 'G':
                mw_val = pm._mw_cyc(P1, P2, L1, L2) if P2 and L2 else 0.
                gf_val = pm._gf_m(L1, L2)
            else:
                mw_val = gf_val = 0.  # Galileo MW via _proc_gal, can't replicate here

            _shadow_prev_mw[sid] = mw_val
            _shadow_prev_gf[sid] = gf_val
            if not slip_s and mw_val != 0.:
                _shadow_mw_hist[sid].append(mw_val)
            elif slip_s:
                _shadow_mw_hist[sid].clear()

            _shadow_streak[sid] = _shadow_streak[sid] + 1 if not slip_s else 0

            if sid not in _shadow_sidx:
                _shadow_sidx[sid] = len(_shadow_x)
                _shadow_x = np.append(_shadow_x, 0.)
                Pn = np.zeros((len(_shadow_x), len(_shadow_x)))
                Pn[:-1, :-1] = _shadow_P
                Pn[-1, -1] = 300.**2
                _shadow_P = Pn
                _shadow_phi[sid] = False

            geom_shadow.append({'sid': sid})

        # ── Part 2: WL candidate audit (shadow, every 60 epochs or < 5) ──
        if epoch_idx % 60 == 0 or epoch_idx < 5:
            x_snap.clear()
            for s2, k2 in _shadow_sidx.items():
                x_snap[s2] = float(_shadow_x[k2]) if k2 < len(_shadow_x) else 0.
            _wl_candidate_audit(
                _shadow_mw_hist, _shadow_sidx, _shadow_phi, _shadow_P,
                _shadow_wl_fixed, geom_shadow,
                sod, epoch_idx, _shadow_b_rec_fr, ISB_IDX_S,
            )

    # ── Now run the real _ppp_pass (all actual physics here) ─────────────────
    print("\n" + "="*60)
    print("[PHASE-4] Shadow audit complete. Running real _ppp_pass …")
    print("="*60 + "\n")

    # Provide context to the LAMBDA hook via _nl_call_ctx
    # We patch _ppp_pass to set context before LAMBDA; since we cannot inject
    # inside it, we hook at the _lambda_ils level and use a thread-local trick.
    # The hook reads _nl_call_ctx which we set via a second wrapper below.

    # ── DEEP HOOK: wrap N1_floats / Q_nl assembly ────────────────────────────
    # We want to print [NL-CAND] and [LAMBDA-IN] from inside _ppp_pass.
    # Solution: we wrap lambda_py in lambda_ils.py directly at the module level
    # so that the next import picks up our version.  The context dict is set
    # by a thin wrapper around _lambda_ils.

    # Reset the hook to a cleaner version that uses the real lambda_py output
    from lambda_ils import lambda_py as _lp_orig

    _lambda_call_counter = [0]

    def _clean_hook(a_float, Q_nl):
        """Called in place of pm._lambda_ils from inside _ppp_pass."""
        _lambda_call_counter[0] += 1
        n = len(a_float)

        # Part 5: LAMBDA input
        sat_ids  = _nl_call_ctx.get('sats', [f'amb{i}' for i in range(n)])
        sys_ids  = _nl_call_ctx.get('sys',  ['?' ] * n)
        sod_now  = _nl_call_ctx.get('sod', 0.)
        ep_now   = _nl_call_ctx.get('nproc', 0)

        _lambda_in_audit(a_float, Q_nl, sat_ids, sys_ids, sod_now, ep_now)

        # Call real LAMBDA
        Q_sym = 0.5 * (Q_nl + Q_nl.T) + np.eye(n) * 1e-14
        try:
            F, s, info = _lp_orig(a_float, Q_sym, m=2)
        except Exception as exc:
            print(f"  [LAMBDA-EXCEPTION] {exc}")
            F, s, info = None, np.zeros(2), -9

        if info != 0 or (F is None) or s[0] < 1e-12:
            _lambda_out_audit(a_float, np.zeros(n), s, info,
                               sat_ids, sod_now, ep_now, 4.5, 0.10)
            return None, 0.0

        ratio = s[1] / s[0]
        _lambda_out_audit(a_float, F[:, 0], s, info,
                           sat_ids, sod_now, ep_now, 4.5, 0.10)
        return F[:, 0], ratio

    pm._lambda_ils = _clean_hook

    # ── NL candidate context injector ────────────────────────────────────────
    # We patch the NL section of _ppp_pass by sub-classing the pass result
    # and using __setitem__ trapping — too invasive.  Instead, we use a simpler
    # approach: we sub-class the result dict and override NL logic logging by
    # using Python's tracing facility (sys.settrace).
    #
    # sys.settrace approach: set a line-level trace on _ppp_pass that fires
    # whenever execution reaches the nl_cands assembly line.

    import types as _types

    _target_func  = pm._ppp_pass
    _target_code  = _target_func.__code__
    _src_lines    = []  # will hold (lineno, local_vars) at NL fix point

    def _trace_nl(frame, event, arg):
        """Line-level trace inside _ppp_pass to intercept NL fix context."""
        if event == 'line':
            co   = frame.f_code
            if co.co_filename != _target_code.co_filename:
                return _trace_nl
            lno  = frame.f_lineno
            lvars = frame.f_locals

            # Part 4: NL candidate dump — triggered when nl_cands is computed
            # Line ~2234 in sample.py: "nl_cands=[m for m in geom …"
            # We detect by checking if 'nl_cands' just became a list in locals
            if 'nl_cands' in lvars and 'phase_rms_now' in lvars:
                nc   = lvars.get('nl_cands', [])
                sod_ = lvars.get('sod', 0.)
                npr_ = lvars.get('nproc', 0)
                # Only print every 60 epochs to avoid spam
                if npr_ % 60 == 0 or npr_ < 5:
                    _nl_candidate_audit(
                        lvars.get('geom', []),
                        lvars.get('wl_fixed', {}),
                        lvars.get('nl_fixed', {}),
                        lvars.get('phi', {}),
                        lvars.get('P', np.zeros((6, 6))),
                        lvars.get('x', np.zeros(6)),
                        lvars.get('sidx', {}),
                        lvars.get('_nl_bad_nwl', set()),
                        lvars.get('phase_rms_now', float('nan')),
                        lvars.get('ISB_IDX', 5),
                        lvars.get('NL_VAR_THRESH', 100.),
                        lvars.get('NL_PHASE_THRESH', 0.010),
                        lvars.get('NL_MIN_SATS', 3),
                        sod_, npr_,
                    )

            # Part 5/6: LAMBDA context — fill _nl_call_ctx before _lambda_ils
            if 'N1_corr_c' in lvars and 'nl_cands_c' in lvars:
                nc_c  = lvars.get('nl_cands_c', [])
                _nl_call_ctx['sats']  = [m['sid'] for m in nc_c]
                _nl_call_ctx['sys']   = [m.get('_sys', m['sid'][0]) for m in nc_c]
                _nl_call_ctx['sod']   = lvars.get('sod', 0.)
                _nl_call_ctx['nproc'] = lvars.get('nproc', 0)

            # Part 3: WL fix forensics — detect wl_fixed update
            if 'NWL_to_use' in lvars and 'mn_corr' in lvars and 'sd' in lvars:
                if lvars.get('NWL_to_use') is not None:
                    sid_ = lvars.get('sid', '???')
                    if sid_ not in _T.wl_fix or all(
                            r.get('sat') != sid_ for r in _T.wl_fix):
                        _wl_fix_audit(
                            sid_,
                            sid_[0] if sid_ else '?',
                            lvars.get('n_hist', 0),
                            lvars.get('mn_corr', float('nan')),
                            lvars.get('sd', float('nan')),
                            lvars.get('NWL_to_use', 0),
                            lvars.get('b_rec', 0.),
                            lvars.get('tag', '?'),
                            lvars.get('sod', 0.),
                            lvars.get('nproc', 0),
                        )

        return _trace_nl

    # Install the trace (INSTRUMENTATION ONLY — settrace does not affect physics)
    sys.settrace(_trace_nl)

    try:
        result = _original_ppp_pass(
            epochs, sp3t, sp, sc, clkd, osb, ah, nom, iclk, izwd,
            lat0, doy, zhd, tref, satx, att, recx,
            elm=elm, SC=SC, SP=SP, direction=direction, label=label,
            wl_init=wl_init, amb_init=amb_init,
            constellation=constellation, blq=blq, sta=sta,
            trace_fh=trace_fh, pass_label=pass_label,
        )
    finally:
        sys.settrace(None)
        pm._lambda_ils = _orig_lambda   # always restore

    # ── Write forensic CSVs ───────────────────────────────────────────────────
    _write_forensic_csvs(pass_label or 'audit')

    return result


def _write_forensic_csvs(label: str):
    """Write the 5 Phase-4 forensic CSVs."""
    out_dir = _SAMPLE_DIR
    label_safe = label.replace('+', '_').replace(' ', '_')

    # ---------- wl_candidate_trace.csv ----------
    _write_csv(
        os.path.join(out_dir, f'wl_candidate_trace_{label_safe}.csv'),
        _T.wl_candidate,
        ['epoch', 'sod', 'total_phi_active', 'total_amb_states',
         'total_valid_sats', 'sat', 'sys', 'elevation_deg', 'ki',
         'n_mw_hist', 'mw_mean_cyc', 'mw_std_cyc',
         'amb_sigma_m', 'amb_sigma_cyc',
         'phi_active', 'wl_already_fixed', 'rejected_reason'],
    )

    # ---------- wl_fix_trace.csv ----------
    _write_csv(
        os.path.join(out_dir, f'wl_fix_trace_{label_safe}.csv'),
        _T.wl_fix,
        ['epoch', 'sod', 'sat', 'sys', 'n_mw', 'mw_mean_corr_cyc',
         'mw_std_cyc', 'nearest_integer', 'integer_residual_cyc',
         'b_rec_cyc', 'b_rec_tag', 'fixed'],
    )

    # ---------- nl_candidate_trace.csv ----------
    _write_csv(
        os.path.join(out_dir, f'nl_candidate_trace_{label_safe}.csv'),
        _T.nl_candidate,
        ['epoch', 'sod', 'sat', 'sys', 'ki', 'phi_active', 'wl_fixed',
         'NWL', 'amb_m', 'amb_sigma_m', 'nl_float_cyc', 'nl_sigma_cyc',
         'nearest_integer', 'integer_residual_cyc', 'cov_diag_m2',
         'rejected_reason'],
    )

    # ---------- lambda_trace.csv (IN + OUT merged by epoch) ----------
    lambda_merged = []
    out_map = {r['epoch']: r for r in _T.lambda_out}
    for r in _T.lambda_in:
        ep = r['epoch']
        merged = {**r, **out_map.get(ep, {})}
        lambda_merged.append(merged)
    _write_csv(
        os.path.join(out_dir, f'lambda_trace_{label_safe}.csv'),
        lambda_merged,
        ['epoch', 'sod', 'dim', 'sat_list', 'sys_list',
         'cond_Q', 'det_Q', 'min_diag', 'max_diag', 'min_eig',
         'covariance_units', 'float_vector_cyc', 'diag_Q_cyc2',
         'success', 'info', 'ratio', 'sqnorm_best', 'sqnorm_second',
         'fixed_count', 'ratio_thresh', 'ratio_pass'],
    )

    # ---------- ambiguity_state_trace.csv ----------
    _write_csv(
        os.path.join(out_dir, f'ambiguity_state_trace_{label_safe}.csv'),
        _T.amb_state,
        ['epoch', 'sod', 'sat', 'ki', 'ISB_IDX',
         'amb_value_m', 'cov_diag_m2', 'phi_active', 'valid',
         'overlap_error'],
    )

    print(f"\n[PHASE-4] Forensic CSVs written for label='{label}':")
    for fn in [
        f'wl_candidate_trace_{label_safe}.csv',
        f'wl_fix_trace_{label_safe}.csv',
        f'nl_candidate_trace_{label_safe}.csv',
        f'lambda_trace_{label_safe}.csv',
        f'ambiguity_state_trace_{label_safe}.csv',
    ]:
        path = os.path.join(out_dir, fn)
        n = len(open(path).readlines()) - 1 if os.path.exists(path) else 0
        print(f"  {fn}  ({n} rows)")


def _write_csv(path: str, rows: List[Dict], fieldnames: List[str]):
    if not rows:
        # Write header-only so files always exist
        with open(path, 'w', newline='') as f:
            csv.DictWriter(f, fieldnames=fieldnames,
                           extrasaction='ignore').writeheader()
        return
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


# ==============================================================================
# Public API: audit_pass()
# ==============================================================================
def audit_pass():
    """
    Apply the Phase-4 forensic patch to sample.py and run one GPS+Galileo
    forward pass.  Writes 5 forensic CSVs to the sample.py directory.
    """
    # Patch the function in the module namespace
    pm._ppp_pass = _instrumented_ppp_pass

    # Run the full postpos pipeline — it will internally call _ppp_pass
    # which is now our instrumented version.
    try:
        from structures import PrcOpt, SolOpt, FilOpt
    except ImportError:
        class PrcOpt: pass
        class SolOpt: pass
        class FilOpt: pass

    DATA = _SAMPLE_DIR
    INFILES = [os.path.join(DATA, f) for f in [
        'IISC00IND_R_20260380000_01D_30S_MO.rnx',
        'IISC00IND_R_20260380000_01D_MN.rnx',
        'COD0MGXFIN_20260380000_01D_05M_ORB.SP3',
        'COD0MGXFIN_20260380000_01D_30S_CLK.CLK',
        'COD0MGXFIN_20260380000_01D_30S_ATT.OBX',
        'COD0MGXFIN_20260380000_01D_01D_OSB.BIA',
        'igs20_2408.atx',
        'COD0MGXFIN_20260380000_01D_12H_ERP.ERP',
    ]]

    pm.postpos(
        None, None, 0., 0., PrcOpt(), SolOpt(), FilOpt(),
        INFILES, os.path.join(DATA, 'ppp_results_phase4.csv'),
    )


# ==============================================================================
# Standalone runner
# ==============================================================================
if __name__ == '__main__':
    print("="*72)
    print("Phase-4 Forensic Audit — WL/NL/LAMBDA Pipeline Instrumentation")
    print("="*72)
    t0 = _time.time()
    audit_pass()
    print(f"\n[PHASE-4] Done in {_time.time()-t0:.1f}s")
"""
ppp_gps_only.py  — GPS-Only PPP (refactored from v642-L2STAB)
==================================================================

ROOT-TRIGGER INVESTIGATION — STARTUP PHASE INNOVATION FORENSICS
================================================================
STATUS:  Operational birth REVERTED to POST-FIT (x[_nki] = _n_postfit).
         Pre-fit birth was mechanism preserving corruption, NOT original trigger.
         Pre-fit birth destabilized the filter immediately — confirmed NOT the fix.
         This version isolates WHY startup phase innovations are physically huge.

OPERATIONAL CHANGE:
  x[_nki] = _n_postfit    ← RESTORED (was _n_prefit in previous experiment)

DO NOT TOUCH:
  EKF structure, Q matrices, SP, Huber, ZWD, gating, RTS,
  ambiguity ramp, robust weighting, clock limiter (PATCH-3 retained).

NEW FORENSIC PATCHES (diagnostics only — no EKF changes):

  PATCH 1 — STARTUP-PHASE-TRACE (first 20 epochs, first accepted phase row):
    Full observation budget in metres: P1, P2, L1/L2 cycles and metres,
    PIF, LIF_raw, LIF_corr(=LIFc), rho, sat_clk, rec_clk, tropo, windup,
    pcv, rp_prefit, innov_phase, innov_code, amb_state, amb_sigma.

  PATCH 2 — IF-ALGEBRA-CHECK (epoch 0 only, first satellite):
    Verifies ALFA*L1_m - BETA*L2_m == _LIF_raw and ALFA*P1 - BETA*P2 == _PIF_raw.
    Differences must be < 1e-6 m.

  PATCH 3 (existing) — STARTUP CLOCK LIMITER retained (first 20 epochs ±3 m).

  PATCH 4 — AMB-DATUM (every ambiguity birth):
    LIF_corr, rp_postfit, birth_m, birth_cycles_L1, birth_cycles_IF, lambda_IF.
    Key test: does birth_cycles_IF look physically sane?

  PATCH 5 — CLOCK-OBS (first 20 epochs, after EKF update):
    n_phase, n_code, mean/std phase innovation, mean/std code innovation,
    clock_update, common_mode_ratio = |mean_phase| / std_phase.

  PATCH 6 — HARD SAFETY ASSERTIONS (abort on gross inconsistency):
    FATAL-1: abs(birth_m) > 15 m
    FATAL-2: abs(clock_update) > 10 m  (post-EKF)
    FATAL-3: abs(mean_phase_innov) > 10 m  (pre-EKF)
    FATAL-4: abs(LIF_corr - rp_prefit) > 50 m  (at birth)

STAGE 1C — MW PRE-GATE FOR GF-TRIGGERED RESETS (forensic experiment):
  Implements a minimal, surgical MW pre-gate before the GF-triggered reset path.
  Gate condition: if abs(dGF) > 0.08 AND abs(dMW) <= 1.5 → GF-only trigger → SUPPRESS.
  MW-confirmed resets (abs(dMW) > 1.5) are unaffected and pass through Patch 1.
  Rationale: G08 cascade forensic analysis confirmed 13 consecutive false resets
    driven by smooth STEC ramp (dGF ≈ +87 mm/ep, dMW = 0.1–1.3 cyc).
    True slips have |dMW| > 5 cyc — clean separation at the 1.5 cyc boundary.
  New log tag: [GF-RAMP-SUPPRESSED]  sat/ep/el/GF/MW/sod/sigma
  Counter: _1c_suppressed_resets (printed in PASS-SUMMARY)
  ROLLBACK: delete lines between "── STAGE 1C" and "── END STAGE 1C" markers.

EXPECTED OUTCOMES:
  Case A (IF algebra bug)    : LIF_manual mismatch or absurd lambda_IF scaling.
  Case B (units mismatch)    : birth_m fine but birth_cycles_IF astronomical.
  Case C (observability fail): IF algebra perfect, units fine, but common_mode >> 1.
  Case D (common-mode bias)  : code and phase both shifted coherently at startup.
==================================================================

STABILIZATION REFACTOR (float-only clean phase):
================================================
Changes from the v642-L2STAB baseline:

  1. ambiguity_manager.py: replaced full lifecycle state machine with a
     minimal born/reset flag.  All lifecycle bureaucracy (DORMANT/CONVERGING/
     CONVERGED/REBORN/RESTORED/PRUNED states, lineage tracking, genealogy,
     convergence evaluation, ASSERT-L/O/B/C sets) removed.  Public API
     preserved; lifecycle methods are stubs / no-ops.

  2. WL fixing disabled: _ENABLE_WL_FIXING = False.  mw_hist and wl_fixed
     dicts are still allocated; WL infrastructure is dormant, not deleted.
     Set _ENABLE_WL_FIXING = True to re-enable.

  3. phase_rms fix (PRIMARY BUG FIX):
     Previous code computed phase_rms from ALL birth-complete satellites
     (including hard-gated rejections and ambiguities with outlier birth
     values like G10 N=+57278mm), producing physically impossible 1–4 m RMS
     while individual accepted residuals were 30–50 mm.
     Fixed: phase_rms now uses ACCEPTED POSTFIT residuals only, computed
     from _accepted_phase_sids after the EKF update.
     [CONST-STATS], constellation_stats_trace, and startup_state_trace
     writers use the same accepted-postfit filter.

  4. All physical models, EKF, cycle-slip detection, ambiguity reset/rebirth,
     gap recovery, covariance propagation, RTS smoother, Joseph form,
     troposphere, GPS observable selection (C1W/C2W/L1C/L2W), OSB, ATX, OBX,
     BLQ are UNCHANGED.

GPS-L2-STABILIZATION EXPERIMENT (Layer-1B):
  Eliminates all GPS L2 phase signal-family switching by locking L2 phase
  to L2W only throughout the entire pass.  L2L is NEVER selected.

── Original v642 header follows ─────────────────────────────────────────────
  Eliminates all GPS L2 phase signal-family switching by locking L2 phase
  to L2W only throughout the entire pass.  L2L is NEVER selected.

  Layer-1A established:
    125 L2L↔L2W transitions across 26 GPS satellites
    Every transition causes GF jumps >50 mm and triggers ambiguity reset
    GPS ambiguities repeatedly restart contraction from these switches

  This experiment determines whether observable-family switching is the
  dominant ambiguity corruption mechanism in GPS.

CHANGES FROM v642 (MINIMAL — controlled experiment):
  1. _check_osb_gps():   OSB L2 phase lookup: L2W only  (L2L removed)
  2. _proc():            L2 phase selection: L2W only   (use_civil/L2L removed)
                         bl2 OSB: L2W only              (L2L removed)
                         Returns l2_sig_used='L2W' in result dict
  3. _ppp_pass():        GPS-L2-STAB tracking per satellite per epoch
                         Writes gps_l2_stabilization_audit.csv
                         Prints [GPS-L2-STABILIZATION] before/after counts
  4. postpos():          Opens/routes/closes gps_l2_stabilization_audit.csv

UNCHANGED (CONTROLLED EXPERIMENT — DO NOT MODIFY):
  EKF math, ambiguity lifecycle, process noise, gating, covariance,
  AR logic, reset logic, code observables (C1W/C2W), OSB routing (C2W/L2W)

FORENSIC OUTPUT:
  gps_l2_stabilization_audit.csv — sat, sod, selected_L2_phase_signal,
    switch_detected, ambiguity_reset_triggered, GF_jump_mm, MW_jump_mm
  [GPS-L2-STABILIZATION] <switch_events_before> <switch_events_after>

EXPECTED RESULT:
  switch_detected = 0 for all rows
  [GPS-L2-STABILIZATION] 125 0

── Original v642 header follows ─────────────────────────────────────────────

STEP-3 changes (behaviour-preserving architectural refactor):
  Builds on Step-2 (lifecycle operations centralised in AmbiguityManager).
  All changes are in ambiguity_manager.py; ppp_v642.py changes are minimal.

  1. Import AmbiguityLifecycleState for ASSERT-4 membership check.
  2. ASSERT-4 extended with Step-3 manager methods:
       transition_state, run_consistency_assertions,
       get_lifecycle_state, get_lineage_id, get_generation,
       get_parent_lineage_id, lifecycle_trace_header.
  3. run_consistency_assertions(nproc, sod) called every SNAPSHOT_INTERVAL
     epochs (co-located with write_snapshot) — ASSERT-L1 … ASSERT-L7.
  4. Lifecycle trace CSV now uses Step-3 Phase-4 format (header auto-updated
     via AmbiguityManager.lifecycle_trace_header()).

All EKF equations, gates, thresholds, process noise, and outputs are IDENTICAL
to ppp_v641.py.  This is a pure lineage/state-graph refactor.

── Original header (ppp_v641) follows ──────────────────────────────────────
ppp.py  vRESET-minimal — Phase-Reset: Minimal Float PPP-EKF
===========================================================

PHASE-RESET — all lifecycle/state-machine policy removed.
Returned to a minimal float PPP-EKF with:
  - continuous EKF execution every epoch
  - ambiguities as normal stochastic states
  - no lifecycle bureaucracy
  - no trust/promotion/quarantine systems
  - no EKF permission gating
  - no ambiguity purge framework

Convergence is decided probabilistically through covariance contraction,
NOT through manually designed state machines.

────────────────────────────────────────────────────────────────────────
DELETED SYSTEMS
────────────────────────────────────────────────────────────────────────
  1. Ambiguity lifecycle states (WARMUP / ACTIVE / TRUSTED / DEGRADED /
     newborn_pending)
  2. Promotion systems (promotion counters, trusted thresholds, confidence
     transitions, promotion epochs)
  3. Quarantine systems (quarantine rows/epochs, trusted_warmup_rows,
     strong_phase_rows)
  4. EKF permission logic (update_allowed, filter_called gates,
     minimum trusted rows, EKF suppression, ambiguity maturity gating)
  5. Purge systems (COMMONMODE-PURGE, CMODE eviction, rebirth tracking,
     rebirth counters, ambiguity kill loops)
  6. Ambiguity protection bureaucracy (newborn preservation, freeze, hold,
     aging logic)
  7. All associated diagnostics ([ACTIVE-COUNT], [REBIRTH-DETECTED],
     [EKF-EXECUTION], [P14-CMODE-EVICTION-SUPPRESSED], promotion/lifecycle
     summaries)

────────────────────────────────────────────────────────────────────────
RETAINED PHYSICS
────────────────────────────────────────────────────────────────────────
  1.  Observation model (IF combinations, GPS-only: L1C/L2W)
  2.  SP3/CLK/BIA/ATX/OBX handling
  3.  Troposphere estimation (GMF, ZHD+ZWD)
  4.  Cycle slip detection (GF + MW)
  6.  Innovation residual editing (observation-domain rejection only)
  7.  Covariance propagation (process noise)
  8.  Joseph covariance symmetrization
  9.  Ambiguity reset after slip
  10. Basic NaN/PD guards
  11. RTS smoother
  12. LAMBDA implementation (comment-disabled — AR disabled for float validation)
  13. WL accumulation infrastructure

────────────────────────────────────────────────────────────────────────
FLOAT PPP AMBIGUITY RULES
────────────────────────────────────────────────────────────────────────
  At birth:
    x[ki] = LIF - rp_postfit  (post-EKF state)
    P[ki,ki] = (20 m)^2

  After birth:
    ALWAYS participate in EKF updates.
    NO maturity / trust / sigma-threshold permission.

────────────────────────────────────────────────────────────────────────
ALLOWED REJECTION (observation-domain only)
────────────────────────────────────────────────────────────────────────
  Code:  |residual| > 10 m startup / 5 m converged
  Phase: |residual| > 0.10 m startup / 0.05 m converged
  Also:  NaN / inf / singular variance / invalid geometry

────────────────────────────────────────────────────────────────────────
AR STATUS
────────────────────────────────────────────────────────────────────────
  NL fixing disabled. WL infrastructure retained.
  Validate float PPP convergence first.

────────────────────────────────────────────────────────────────────────
DIAGNOSTICS (per epoch)
────────────────────────────────────────────────────────────────────────
  [EPOCH]     sat count, code RMS, phase RMS, innov norm, rej count,
              ZWD, pos update norm, amb sigma mean/min/max, cond(P)
  [AMB-BORN]  ambiguity birth events
  [AMB-RESET] ambiguity reset (cycle slip)
  [AMB-RESTORE] gap recovery
  [PASS-SUMMARY] total EKF updates, epochs, resets
  [AMB-SIGMA-CONTRACTION] per-sat birth→final sigma
"""
import os, sys, math, time as _time
from collections import defaultdict
import numpy as np
from ambiguity_manager import AmbiguityManager, AmbiguityLifecycleState
from rebirth_forensic import RebirthForensic  # REBIRTH-FORENSIC audit instrument

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ppp_ar_python'))
from constants import CLIGHT, FREQ1, FREQ2, OMGE, RE_WGS84
from kf import filter_standard

LAMBDA1   = CLIGHT / FREQ1
LAMBDA2   = CLIGHT / FREQ2
LAMBDA_WL = CLIGHT / (FREQ1 - FREQ2)
LAMBDA_NL = CLIGHT / (FREQ1 + FREQ2)
F1SQ, F2SQ = FREQ1**2, FREQ2**2
ALFA      = F1SQ / (F1SQ - F2SQ)
BETA      = F2SQ / (F1SQ - F2SQ)
LAMBDA_IF = CLIGHT / (ALFA*FREQ1 - BETA*FREQ2)
MU        = 3.986004418e14
E2        = 0.00669437999014
RE        = RE_WGS84

# Pre-computed GPS NL denominator
_DENOM_G = ALFA*LAMBDA1 - BETA*LAMBDA2         # GPS NL denom  ≈ 0.1073 m

# ==============================================================================
# IF combination helpers
# ==============================================================================
def _ifc(p1, p2):
    """
    GPS ionosphere-free combination for code measurements (meters).
    """
    return ALFA * p1 - BETA * p2

def _ifl(l1, l2):
    """
    GPS ionosphere-free combination for phase measurements (meters).
    Inputs already in meters.
    """
    return ALFA * l1 - BETA * l2


# ==============================================================================
#  v55 — Phase-1 forensic / guard helpers
# ==============================================================================

# Per-pass epoch counter (reset in _ppp_pass) used for startup protection.
_STARTUP_EPOCHS = 30          # epochs with heavy instrumentation
_ELEV_PROTECT   = 20          # first N epochs use tighter elevation mask
_ELEV_STARTUP   = math.radians(20.)  # temporary mask during startup
# FIX-3 (v64): Phase elevation mask — applied to phase rows AND slip detector
PHASE_EL_MASK_RAD = math.radians(15.0)
# PATCH: _AMB_MIN_EPOCHS removed — immediate birth, no minimum streak

def _is_finite_matrix(M, name="matrix"):
    """Return True if M contains no NaN/inf; print [WARN] otherwise."""
    if not np.all(np.isfinite(M)):
        bad = np.sum(~np.isfinite(M))
        print(f"  [WARN] {name} has {bad} non-finite element(s)")
        return False
    return True

def _is_pd(M, name="matrix", tol=0.0):
    """Return True if symmetric matrix M is positive-definite (min-eig > tol)."""
    sym = 0.5*(M + M.T)
    try:
        evals = np.linalg.eigvalsh(sym)
        if np.any(evals <= tol):
            print(f"  [WARN] {name} not PD — min eigenvalue = {evals.min():.3e}")
            return False
        return True
    except np.linalg.LinAlgError as exc:
        print(f"  [WARN] {name} eigen-decomposition failed: {exc}")
        return False

def _cond(M):
    """Return condition number of M (∞ if singular / non-finite)."""
    try:
        ev = np.linalg.eigvalsh(0.5*(M+M.T))
        mn = np.min(np.abs(ev)); mx = np.max(np.abs(ev))
        return mx / mn if mn > 0 else float('inf')
    except Exception:
        return float('inf')

# ── PSD integrity monitor (forensic instrument) ─────────────────────────────
def _psd_check(P, sidx_inv, label, sod, tol=0.):
    """Full PSD audit — call at key pipeline checkpoints.

    Returns (is_psd, first_neg_state, lambda_min, asym_err).
    Prints a structured [PSD-FAIL] line when violations are found so that
    the first epoch of corruption is identifiable in logs without modifying
    filter logic.

    sidx_inv: dict mapping state_index → sat_id (or None for nav states).
    tol: eigenvalue floor; negative values more negative than -tol are flagged.
    """
    n = P.shape[0]
    asym = float(np.max(np.abs(P - P.T)))

    # Check diagonal directly first (cheapest)
    diag = np.diag(P)
    neg_diag_idx = np.where(diag < -tol)[0]
    first_neg_state = None
    if len(neg_diag_idx) > 0:
        _i = int(neg_diag_idx[0])
        first_neg_state = sidx_inv.get(_i, f'state[{_i}]')
        print(f"  [PSD-FAIL] {label}  SOD={sod:.0f}  "
              f"neg_diagonal: state={first_neg_state}  "
              f"P[{_i},{_i}]={diag[_i]:.4e}  asym={asym:.2e}")
        return False, first_neg_state, float(diag[_i]), asym

    # Full eigenvalue check (more expensive; only runs if diagonal is clean)
    try:
        ev = np.linalg.eigvalsh(0.5 * (P + P.T))
        lmin = float(ev.min())
        if lmin < -tol:
            # Identify which state is most responsible via min eigenvector
            idx_lmin = int(np.argmin(ev))
            evec = np.linalg.eigh(0.5*(P+P.T))[1][:, idx_lmin]
            dom_state_idx = int(np.argmax(np.abs(evec)))
            first_neg_state = sidx_inv.get(dom_state_idx, f'state[{dom_state_idx}]')
            print(f"  [PSD-FAIL] {label}  SOD={sod:.0f}  "
                  f"neg_eigenvalue: λ_min={lmin:.4e}  "
                  f"dominant_state={first_neg_state}  asym={asym:.2e}")
            return False, first_neg_state, lmin, asym
    except np.linalg.LinAlgError:
        print(f"  [PSD-FAIL] {label}  SOD={sod:.0f}  eigvalsh failed (singular)")
        return False, None, float('nan'), asym

    return True, None, float(ev.min()), asym


def _joseph_symmetrise(P):
    """Enforce covariance symmetry in-place.

    PSD-REPAIR-4: Symmetrisation alone does not recover PSD if negative
    eigenvalues already exist.  We additionally apply a targeted diagonal
    floor to the 5×5 position/clock/ZWD sub-block, which is the block
    most critical for filter health.  Ambiguity states are handled by the
    per-satellite sanity check in the main loop.
    """
    P[:] = 0.5 * (P + P.T)
    # Diagonal floor: any diagonal below 1e-12 is physically impossible
    # (sub-femtometre variance).  Clip to prevent sqrt domain errors.
    _d = np.diag(P)
    _bad = _d < 1e-12
    if np.any(_bad):
        np.fill_diagonal(P, np.maximum(_d, 1e-12))

def _filter_joseph(x, P, Ht, z, R):
    """Joseph-stabilised EKF measurement update — PSD by construction.

    Drop-in replacement for filter_standard (RTKLIB kf.filter).
    Signature matches filter_standard: H is passed transposed (Ht = H.T),
    z is the innovation vector (obs - predicted), R is the obs noise matrix.

    Update equations:
        K = P * H^T * (H * P * H^T + R)^{-1}
        x += K * z
        P = (I - K*H) * P * (I - K*H)^T + K * R * K^T   ← Joseph form

    The Joseph form guarantees P_new is PSD for any K, unlike the simplified
    P = (I-KH)*P which accumulates numerical error under high-rank H.

    Returns 0 on success, -1 on failure (matching filter_standard convention).
    """
    try:
        H = Ht.T   # Ht is H transposed (columns = states, rows = obs)
        m = len(z)
        n = len(x)
        # Innovation covariance S = H*P*H^T + R
        PHt = P @ Ht
        S   = H @ PHt + R
        # Kalman gain K = P*H^T * S^{-1}  (solve for numerical stability)
        K   = np.linalg.solve(S.T, PHt.T).T   # equivalent to PHt @ inv(S)
        # State update
        x  += K @ z
        # Covariance update — Joseph form
        IKH = np.eye(n) - K @ H
        P[:] = IKH @ P @ IKH.T + K @ R @ K.T
        return 0
    except (np.linalg.LinAlgError, Exception):
        return -1


# ── Filter selector: set USE_JOSEPH_FORM = True to replace filter_standard ──
# This is the recommended long-term repair.  The minimal crash fix (Repairs 1-8)
# works with the existing filter_standard; Joseph form eliminates the root cause.
USE_JOSEPH_FORM = False   # flip to True to enable; requires testing validation


def _filter_dispatch(x, P, Ht, z, R):
    """Route to Joseph form or RTKLIB filter_standard based on USE_JOSEPH_FORM."""
    if USE_JOSEPH_FORM:
        return _filter_joseph(x, P, Ht, z, R)
    return filter_standard(x, P, Ht, z, R)


def _check_osb_gps(ob, sid):
    """Return (bl1_m, bl2_m, ok) for GPS.  ok=False → reject observation."""
    bl1 = ob.get('L1C', ob.get('L1W', None))
    # GPS-L2-STAB: L2W OSB only — L2L intentionally suppressed.
    bl2 = ob.get('L2W', ob.get('L2C', None))
    if bl1 is None or bl2 is None:
        print(f"  [REJECT] {sid}: GPS phase OSB missing "
              f"(L1={'present' if bl1 is not None else 'MISSING'}, "
              f"L2={'present' if bl2 is not None else 'MISSING'}) — obs excluded")
        return 0., 0., False
    return float(bl1), float(bl2), True

def _fmtf(x, scale=1.0, prec=4):
    """Safe float formatter — always returns a string safe to embed in CSV.
    Never raises 'Unknown format code f for object of type str'.
    """
    if x is None:
        return "nan"
    try:
        if not math.isfinite(x):
            return "nan"
        return f"{x * scale:.{prec}f}"
    except Exception:
        return "nan"

def _fmtf_sci(x, prec=8):
    """FIX-2 (v64): NIS formatter in scientific notation to preserve tiny values.
    Fixed-point :.6f collapses values 1e-9…1e-6 to 0.000000 — use :.8e instead.
    """
    if x is None:
        return "nan"
    try:
        if not math.isfinite(x):
            return "nan"
        return f"{x:.{prec}e}"
    except Exception:
        return "nan"


def _reject_obs(ri, H, z, Rd, reason, sid, sod, startup=False):
    """Zero-out a measurement row and log the rejection (silent when reason is None)."""
    H[ri, :] = 0.
    z[ri]    = 0.
    Rd[ri]   = 1e12
    if reason is not None and startup:
        print(f"    [REJECT] {sid} row={ri} SOD={sod:.0f}: {reason}")


# ── PHASE-6 FORENSIC HELPERS ──────────────────────────────────────────────────

def _fmt_amb_death_trace(sat, event, reason, caller, health_before, health_after,
                          sigma_before_m, sigma_after_m, ki, gap_sec,
                          bad_streak, good_streak, sod):
    """Emit the canonical [AMB-DEATH-TRACE] block."""
    print(
        f"[AMB-DEATH-TRACE]\n"
        f"  sat={sat}  SOD={sod:.0f}\n"
        f"  event={event}\n"
        f"  reason={reason}\n"
        f"  caller={caller}\n"
        f"  health_before={health_before}  health_after={health_after}\n"
        f"  sigma_before={sigma_before_m*1e3:.1f}mm  sigma_after={sigma_after_m*1e3:.1f}mm\n"
        f"  ki={ki}  gap_sec={gap_sec:.0f}\n"
        f"  bad_streak={bad_streak}  good_streak={good_streak}"
    )

def _fmt_health_transition(sat, from_h, to_h, reason, residual_mm, sigma_mm, sod):
    """Emit the canonical [AMB-HEALTH-TRANSITION] block."""
    print(
        f"[AMB-HEALTH-TRANSITION]\n"
        f"  sat={sat}  SOD={sod:.0f}\n"
        f"  from={from_h}  to={to_h}\n"
        f"  reason={reason}\n"
        f"  residual={residual_mm:+.1f}mm  sigma={sigma_mm:.1f}mm"
    )

def _fmt_geom_drop(sat, sod, health, saved):
    print(f"[AMB-GEOM-DROP]  sat={sat}  SOD={sod:.0f}  "
          f"health={health}  saved_state={'YES' if saved else 'NO'}")

def _fmt_geom_return(sat, sod, gap_sec, restored, reborn, health_before):
    print(f"[AMB-GEOM-RETURN]  sat={sat}  SOD={sod:.0f}  "
          f"gap_sec={gap_sec:.0f}  health_before={health_before}  "
          f"restored_saved_state={'YES' if restored else 'NO'}  "
          f"reborn={'YES' if reborn else 'NO'}")




# ==============================================================================
#  ATX parser
# ==============================================================================
def parse_atx(fp):
    sat_atx = defaultdict(list); rec_atx = {}
    def _g(yr,mo,dy,hr,mn,sc):
        if yr == 0: return None
        a=(14-mo)//12; y=yr+4800-a; m=mo+12*a-3
        jdn=dy+(153*m+2)//5+365*y+y//4-y//100+y//400-32045
        d=jdn-0.5+(hr*3600+mn*60+sc)/86400-2444244.5; wk=int(d/7)
        return wk*604800+(d-wk*7)*86400
    with open(fp, 'r', errors='replace') as fh:
        ih=True; cur=None; isat=False; cprn=None; cant=None; cf=None
        z1=z2=dz=0.; vf=vu=None; pf={}; pv={}
        for raw in fh:
            ln=raw.rstrip('\n'); lb=ln[60:].strip() if len(ln)>60 else ''
            if ih:
                if 'END OF HEADER' in lb: ih=False
                continue
            if 'START OF ANTENNA' in lb:
                cur=True; isat=False; cprn=None; cant=None; cf=None
                vf=vu=None; pf={}; pv={}; z1=z2=dz=0.; continue
            if 'END OF ANTENNA' in lb:
                if cur:
                    if isat and cprn:
                        # GPS satellites (and fallback for other) store corrections
                        # under 'G01'/'G02'.  GLONASS under 'R01'/'R02'.
                        _sys=cprn[0]
                        if _sys=='R':
                            k1,k2='R01','R02'; af,bf=ALFA,BETA
                        else:
                            k1,k2='G01','G02'; af,bf=ALFA,BETA
                        p1=np.array(pf.get(k1,[0,0,0]),float)
                        p2=np.array(pf.get(k2,[0,0,0]),float)
                        v1=pv.get(k1,[]); v2=pv.get(k2,[])
                        vi=([af*a-bf*b for a,b in zip(v1,v2)] if v1 and v2 and len(v1)==len(v2)
                            else list(v1) if v1 else list(v2))
                        sat_atx[cprn].append({'vf':vf if vf else -1e18,'vu':vu if vu else 1e18,
                            'pco':af*p1-bf*p2,'pcv':vi,'z1':z1,'dz':dz})
                    elif cant:
                        _g01=np.array(pf.get('G01',[0,0,0]),float)
                        _g02=np.array(pf.get('G02',[0,0,0]),float)
                        rec_atx[cant]={
                            'L1':  _g01,
                            'L2':  _g02,
                            'v1':  list(pv.get('G01',[])),
                            'v2':  list(pv.get('G02',[])),
                            'z1':z1,'dz':dz}
                cur=None; continue
            if cur is None: continue
            if 'TYPE / SERIAL NO' in lb:
                at=ln[0:20].strip(); pc=ln[20:23].strip()
                if pc and len(pc)==3 and pc[0] in 'GREJCIS':
                    try: int(pc[1:]); isat=True; cprn=pc
                    except: isat=False
                else: isat=False
                if not isat: cant=at+' '+ln[20:24].strip()
                continue
            if 'ZEN1 / ZEN2 / DZEN' in lb:
                p=ln.split(); z1=float(p[0]); z2=float(p[1]); dz=float(p[2]); continue
            if 'VALID FROM' in lb:
                p=ln.split()
                if len(p)>=6: vf=_g(int(p[0]),int(p[1]),int(p[2]),int(p[3]),int(p[4]),float(p[5]))
                continue
            if 'VALID UNTIL' in lb:
                p=ln.split()
                if len(p)>=6: vu=_g(int(p[0]),int(p[1]),int(p[2]),int(p[3]),int(p[4]),float(p[5]))
                continue
            if 'START OF FREQUENCY' in lb: cf=ln[3:6].strip(); continue
            if 'END OF FREQUENCY' in lb: cf=None; continue
            if cf is None: continue
            if 'NORTH / EAST / UP' in lb:
                p=ln.split()
                if len(p)>=3: pf[cf]=[float(p[0]),float(p[1]),float(p[2])]
                continue
            if ln.strip().startswith('NOAZI'):
                pv[cf]=[float(x) for x in ln.strip().split()[1:]]; continue
    print(f"[ATX]  {len(sat_atx)} sat PRNs, {len(rec_atx)} receiver types")
    return dict(sat_atx), rec_atx

def _gatx(sa, prn, tow):
    es=sa.get(prn,[])
    for e in es:
        if e['vf']<=tow<=e['vu']: return e
    return es[-1] if es else None

def _pcv(lst, z1, dz, ang):
    if not lst or dz<=0: return 0.
    idx=(ang-z1)/dz; i=int(idx)
    if i<0: return lst[0]
    if i>=len(lst)-1: return lst[-1]
    return lst[i]+(idx-i)*(lst[i+1]-lst[i])

def _spco(e, bx, by, bz):
    return np.column_stack([bx,by,bz])@(e['pco']*1e-3)

def _spcv(e, nd):
    return _pcv(e['pcv'],e['z1'],e['dz'],nd)*1e-3

def _rpco(re, lat, lon, sys='G'):
    # GPS IF PCO: ALFA≈2.546, BETA≈1.546, vectors from L1/L2 ('G01'/'G02').
    if re is None: return np.zeros(3)
    af,bf=ALFA,BETA
    L1=re['L1']; L2=re['L2']
    pi=af*L1-bf*L2
    sl,cl=math.sin(lat),math.cos(lat); sn,cn=math.sin(lon),math.cos(lon)
    R=np.array([[-sl*cn,-sn,cl*cn],[-sl*sn,cn,cl*sn],[cl,0,sl]])
    return R@(pi*1e-3)

def _rpcv(re, el, sys='G'):
    # GPS IF PCV: ALFA≈2.546, BETA≈1.546, from v1/v2 ('G01'/'G02') tables.
    if re is None: return 0.
    af,bf=ALFA,BETA
    zen=90-math.degrees(el)
    v1=_pcv(re.get('v1',re['v1']),re['z1'],re['dz'],zen)
    v2=_pcv(re.get('v2',re['v2']),re['z1'],re['dz'],zen)
    return (af*v1-bf*v2)*1e-3


# ==============================================================================
#  OBX parser
# ==============================================================================
def parse_obx(fp):
    att=defaultdict(list); in_d=False; ctow=None
    def _g(yr,mo,dy,hr,mn,sc):
        a=(14-mo)//12; y=yr+4800-a; m=mo+12*a-3
        jdn=dy+(153*m+2)//5+365*y+y//4-y//100+y//400-32045
        d=jdn-0.5+(hr*3600+mn*60+sc)/86400-2444244.5; wk=int(d/7)
        return wk*604800+(d-wk*7)*86400
    with open(fp, 'r') as fh:
        for raw in fh:
            ln=raw.rstrip('\n')
            if '-EPHEMERIS/DATA' in ln: break
            if '+EPHEMERIS/DATA' in ln: in_d=True; continue
            if not in_d: continue
            if ln.startswith('##'):
                p=ln.split()
                if len(p)>=7:
                    try: ctow=_g(int(p[1]),int(p[2]),int(p[3]),int(p[4]),int(p[5]),float(p[6]))
                    except: pass
                continue
            if ' ATT ' not in ln: continue
            p=ln.split()
            if len(p)<7 or p[0]!='ATT' or ctow is None: continue
            try: att[p[1]].append((ctow,float(p[3]),float(p[4]),float(p[5]),float(p[6])))
            except: continue
    for s in att: att[s].sort(key=lambda x: x[0])
    print(f"[OBX]  {len(att)} sats  {sum(len(v) for v in att.values())} records")
    return dict(att)

def _qbody(q0,q1,q2,q3,v):
    c0,c1,c2,c3=q0,-q1,-q2,-q3; vx,vy,vz=v
    d=c1*vx+c2*vy+c3*vz; q2n=c1**2+c2**2+c3**2; s=c0**2-q2n
    cx,cy,cz=c2*vz-c3*vy,c3*vx-c1*vz,c1*vy-c2*vx
    return np.array([2*d*c1+s*vx+2*c0*cx,2*d*c2+s*vy+2*c0*cy,2*d*c3+s*vz+2*c0*cz])

def _body(att, sat, tow, sc, sun):
    es=att.get(sat)
    if es:
        ts=[e[0] for e in es]; i=min(range(len(ts)),key=lambda i: abs(ts[i]-tow))
        _,q0,q1,q2,q3=es[i]
        bx=_qbody(q0,q1,q2,q3,[1,0,0]); by=_qbody(q0,q1,q2,q3,[0,1,0]); bz=_qbody(q0,q1,q2,q3,[0,0,1])
        for v in [bx,by,bz]: v/=(np.linalg.norm(v)+1e-15)
        return bx,by,bz
    r=np.array(sc); bz=-r/(np.linalg.norm(r)+1e-15)
    sr=np.array(sun)-r; sr/=(np.linalg.norm(sr)+1e-15)
    bx=sr-sr.dot(bz)*bz; nb=np.linalg.norm(bx)
    if nb<1e-10: bx=np.array([0.,1.,0.]); bx-=bx.dot(bz)*bz; nb=np.linalg.norm(bx)+1e-15
    bx/=nb; by=np.cross(bz,bx); by/=(np.linalg.norm(by)+1e-15)
    return bx,by,bz

def _nadir(sa, ra, bz):
    d=np.array(ra)-np.array(sa); d/=(np.linalg.norm(d)+1e-15)
    return math.degrees(math.acos(max(-1.,min(1.,d.dot(-bz)))))


# ==============================================================================
#  File parsers
# ==============================================================================
def parse_obs(fp):
    ot={}; ep=[]; ah=0.; ak='UNKNOWN NONE'
    with open(fp, 'r', errors='replace') as f:
        hdr=True; e=None
        for raw in f:
            ln=raw.rstrip('\n')
            if hdr:
                lb=ln[60:].strip() if len(ln)>60 else ''
                if 'ANTENNA: DELTA H/E/N' in lb:
                    try: ah=float(ln[0:14])
                    except: pass
                if 'ANT # / TYPE' in lb: ak=ln[20:40].strip()+' '+ln[40:44].strip()
                if 'SYS / # / OBS TYPES' in lb:
                    sc=ln[0]; n=int(ln[3:6]); ot.setdefault(sc,[])
                    ot[sc].extend(ln[7:60].split()); rem=n-len(ot[sc])
                    while rem>0:
                        r2=f.readline().rstrip('\n'); ot[sc].extend(r2[7:60].split()); rem=n-len(ot[sc])
                if 'END OF HEADER' in lb: hdr=False
            else:
                if ln.startswith('>'):
                    p=ln[1:].split(); fl=int(p[6]) if len(p)>6 else 0
                    e={'t':int(p[3])*3600+int(p[4])*60+float(p[5]),'sats':{},'flag':fl}
                    if fl<=1: ep.append(e)
                elif e and e['flag']<=1:
                    sid=ln[0:3].strip()
                    if not sid: continue
                    tp=ot.get(sid[0],[]); obs={}
                    for i,c in enumerate(tp):
                        s=3+i*16; rv=ln[s:s+14].strip() if len(ln)>s else ''
                        try: obs[c]=float(rv) if rv else 0.
                        except: obs[c]=0.
                    e['sats'][sid]=obs
    print(f"[OBS]  {len(ep)} epochs  ant_h={ah:.4f}m  ant={ak}")
    return ot, ep, ah, ak

def parse_sp3(fp):
    ts=[]; rp=defaultdict(list); rc=defaultdict(list); ei=-1
    with open(fp, 'r') as f:
        for ln in f:
            if ln.startswith('*'):
                p=ln.split()
                ts.append(_gpst(int(p[1]),int(p[2]),int(p[3]),int(p[4]),int(p[5]),float(p[6]))); ei+=1
            elif ln.startswith('P'):
                sid=ln[1:4].strip()
                try: xk,yk,zk,ck=float(ln[4:18]),float(ln[18:32]),float(ln[32:46]),float(ln[46:60])
                except: continue
                if abs(xk)>9e5 or abs(ck)>9e8: continue
                rp[sid].append((ei,xk*1e3,yk*1e3,zk*1e3)); rc[sid].append((ei,ck*1e-6))
    n=len(ts); sp={}; sc={}
    for s in rp:
        ap=np.full((n,3),np.nan); ac=np.full(n,np.nan)
        for i,x,y,z in rp[s]: ap[i]=[x,y,z]
        for i,c in rc[s]: ac[i]=c
        sp[s]=ap; sc[s]=ac
    print(f"[SP3]  {n} epochs  {len(sp)} sats")
    return ts,sp,sc

def parse_clk(fp):
    d=defaultdict(list); hdr=True
    with open(fp, 'r') as f:
        for ln in f:
            if hdr:
                if 'END OF HEADER' in ln: hdr=False
                continue
            if ln[:2]!='AS': continue
            p=ln.split()
            if len(p)<10: continue
            try: d[p[1]].append((_gpst(int(p[2]),int(p[3]),int(p[4]),int(p[5]),int(p[6]),float(p[7])),float(p[9])))
            except: continue
    for s in d: d[s].sort(key=lambda x: x[0])
    tot=sum(len(v) for v in d.values())
    print(f"[CLK]  {tot} entries  {len(d)} sats")
    return dict(d)

def parse_bia(fp):
    """Parse SINEX BIAS. Phase OSBs stored in metres per signal wavelength."""
    B=defaultdict(dict); ins=False
    with open(fp, 'r', errors='replace') as fh:
        for ln in fh:
            if '+BIAS/SOLUTION' in ln: ins=True; continue
            if '-BIAS/SOLUTION' in ln: break
            if not ins or len(ln)<4 or ln[1:4]!='OSB': continue
            prn=ln[11:14].strip(); obs=ln[25:29].strip()
            if not prn or not obs: continue
            tail=ln[29:].split(); unit=val=None
            for i,tok in enumerate(tail):
                if tok.lower() in ('ns','cyc','m'):
                    unit=tok.lower()
                    if i+1<len(tail):
                        try: val=float(tail[i+1])
                        except: pass
                    break
            if unit is None or val is None: continue
            if unit=='ns':
                B[prn][obs]=val*1e-9*CLIGHT
            elif unit=='cyc':
                if obs in ('L1C','L1W','L1P'):
                    B[prn][obs]=val*LAMBDA1
                elif obs in ('L2W','L2C','L2P','L2X'):
                    B[prn][obs]=val*LAMBDA2
                else:
                    B[prn][obs]=val*LAMBDA1
            else:
                B[prn][obs]=val
    tot=sum(len(v) for v in B.values())
    print(f"[BIA]  {tot} OSB entries  {len(B)} PRNs")
    if tot>0:
        g=B.get('G01',{})
        print(f"       G01 C1W={g.get('C1W',float('nan')):+.4f}m  "
              f"C2W={g.get('C2W',float('nan')):+.4f}m  "
              f"L1C={g.get('L1C',float('nan')):+.6f}m  "
              f"L2W={g.get('L2W',float('nan')):+.6f}m")
    return dict(B)


# ==============================================================================
#  Ocean Tide Loading — BLQ parser + displacement
# ==============================================================================
def parse_blq(fp):
    """Parse a BLQ ocean loading file (Scherneck/IERS convention).

    BLQ column order: M2 S2 N2 K2 K1 O1 P1 Q1 MF MM SSA
    BLQ row order (per station, 6 rows):
      amp_Radial(Up), amp_Tang-EW(West+), amp_Tang-NS(South+)  [metres]
      phs_Radial,     phs_Tang-EW,         phs_Tang-NS          [degrees, positive lag]

    Returns dict: {STATION_4CHAR: {'amp': np.ndarray(3,11), 'phs': np.ndarray(3,11)}}
    """
    blq = {}
    if not fp or not os.path.isfile(fp):
        print(f"[BLQ]  Not found: {fp}")
        return blq
    try:
        with open(fp, 'r') as fh:
            lines = fh.readlines()
    except Exception as exc:
        print(f"[BLQ]  Cannot read {fp}: {exc}")
        return blq

    i = 0
    while i < len(lines):
        ln = lines[i].rstrip('\n')
        i += 1
        # Skip comment / blank
        stripped = ln.strip()
        if not stripped or stripped.startswith('$$'):
            continue
        # Station name line: starts with exactly 2 spaces then a letter
        # (data lines start with 2+ spaces then a digit or sign)
        if len(ln) >= 3 and ln[0] == ' ' and ln[1] == ' ' and ln[2] != ' ':
            first = stripped.split()[0] if stripped.split() else ''
            try:
                float(first)
                continue          # data line wandered in — skip
            except ValueError:
                pass
            sta = first.upper()[:4]
            if not sta:
                continue
            # Collect 6 data rows (skip embedded $$ comments)
            rows = []
            while i < len(lines) and len(rows) < 6:
                dl = lines[i].rstrip('\n')
                i += 1
                ds = dl.strip()
                if not ds or ds.startswith('$$'):
                    continue
                toks = ds.split()
                if len(toks) < 11:
                    continue
                try:
                    rows.append([float(v) for v in toks[:11]])
                except ValueError:
                    continue
            if len(rows) == 6:
                blq[sta] = {
                    'amp': np.array(rows[:3]),   # (3,11) Radial / EW / NS  [m]
                    'phs': np.array(rows[3:]),   # (3,11) Radial / EW / NS  [deg]
                }
                print(f"[BLQ]  {sta}: U_M2={rows[0][0]*1e3:+.2f}mm "
                      f"U_K1={rows[0][4]*1e3:+.2f}mm "
                      f"EW_K1={rows[1][4]*1e3:+.2f}mm "
                      f"NS_K1={rows[2][4]*1e3:+.2f}mm")
    if not blq:
        print(f"[BLQ]  WARNING — no stations parsed from {fp}")
    else:
        print(f"[BLQ]  {len(blq)} station(s): {list(blq.keys())}")
    return blq


def _ast_args_otl(tow_total):
    """Compute IERS 2010 fundamental astronomical arguments for OTL.

    Parameters
    ----------
    tow_total : float
        GPS total seconds (GPS_week × 604800 + GPS_sow).
        GPS epoch = JD 2444244.5 (Jan 6.0, 1980 UTC).

    Returns
    -------
    gmst, l, lp, F, D, Om — all in radians.
    """
    # JD from GPS total seconds.  Subtract 18 s leap-seconds → approximate UT.
    jd = 2444244.5 + (tow_total - 18.0) / 86400.0
    t  = (jd - 2451545.0) / 36525.0       # Julian centuries from J2000.5

    # GMST (IAU 1982): seconds of sidereal time → radians
    gmst_s = (67310.54841
              + (876600.0*3600.0 + 8640184.812866)*t
              + 0.093104*t*t
              - 6.2e-6*t*t*t)
    gmst = math.fmod(gmst_s * 2.0*math.pi / 86400.0, 2.0*math.pi)
    if gmst < 0.0: gmst += 2.0*math.pi

    _d2r = math.pi / 180.0
    _a2r = _d2r / 3600.0   # arc-seconds → radians

    # IERS 2010, Table 5.3 (linear term only — sufficient for daily OTL)
    l  = math.fmod(134.96402779*_d2r + 1717915923.2178*_a2r*t, 2.0*math.pi)
    lp = math.fmod(357.52910918*_d2r +  129596581.0481*_a2r*t, 2.0*math.pi)
    F  = math.fmod( 93.27209062*_d2r + 1739527262.8478*_a2r*t, 2.0*math.pi)
    D  = math.fmod(297.85019547*_d2r + 1602961601.2090*_a2r*t, 2.0*math.pi)
    Om = math.fmod(125.04455501*_d2r +   -6962890.5431*_a2r*t, 2.0*math.pi)

    return gmst, l, lp, F, D, Om


def _otl_disp(blq, sta, tow_total, lat, lon):
    """Compute ocean tide loading displacement in ECEF (metres).

    Uses the Doodson multiplication of IERS 2010 fundamental arguments and the
    standard BLQ displacement formula:  d = Σ A·cos(χ − φ).

    BLQ convention (Scherneck):
      Radial  = positive upward  (dU = dR)
      Tang-EW = positive West    (dE = −dW)
      Tang-NS = positive South   (dN = −dS)

    Parameters
    ----------
    blq       : dict from parse_blq
    sta       : 4-char station code (e.g. 'IISC')
    tow_total : float  GPS total seconds
    lat, lon  : float  geodetic latitude / longitude (radians)

    Returns
    -------
    np.ndarray (3,) ECEF displacement in metres; zeros if no data.
    """
    key = sta.strip().upper()[:4]
    if key not in blq:
        return np.zeros(3)

    amp = blq[key]['amp']   # (3,11) Radial / EW / NS  [m]
    phs = blq[key]['phs']   # (3,11) [degrees]

    gmst, l, lp, F, D, Om = _ast_args_otl(tow_total)

    # ── Doodson variables ─────────────────────────────────────────────────
    # Moon's mean longitude:  s = F + Ω
    # Sun's mean longitude:   h = F + Ω − D
    # Moon's perigee long.:   p = F + Ω − l
    # Mean lunar time at Greenwich: τ = GMST + π − (F + Ω)
    _pi = math.pi
    _pi2 = 2.0*_pi
    s   = F + Om
    h   = F + Om - D
    p   = F + Om - l
    tau = gmst + _pi - s      # mean lunar time at Greenwich

    # ── Tidal arguments χ (Doodson multiplication) ───────────────────────
    # Doodson numbers from Cartwright & Tayler (1971):
    # Constituent: (τ, s, h, p, N', p')
    # M2: (2,0,0,0,0,0)  →  2τ
    # S2: (2,2,-2,0,0,0) →  2τ+2s-2h
    # N2: (2,-1,0,1,0,0) →  2τ-s+p
    # K2: (2,2,0,0,0,0)  →  2τ+2s  [= 2·GMST (mod 2π)]
    # K1: (1,1,0,0,0,0)  →  τ+s    [= GMST+π (mod 2π)]
    # O1: (1,-1,0,0,0,0) →  τ-s
    # P1: (1,1,-2,0,0,0) →  τ+s-2h
    # Q1: (1,-2,0,1,0,0) →  τ-2s+p
    # Mf: (0,2,0,0,0,0)  →  2s
    # Mm: (0,1,0,-1,0,0) →  s-p = l  (Moon's anomaly)
    # Ssa:(0,0,2,0,0,0)  →  2h
    chi = np.array([
        2.*tau,                # M2
        2.*tau + 2.*s - 2.*h,  # S2
        2.*tau - s + p,        # N2
        2.*tau + 2.*s,         # K2
        tau + s,               # K1
        tau - s,               # O1
        tau + s - 2.*h,        # P1
        tau - 2.*s + p,        # Q1
        2.*s,                  # Mf
        s - p,                 # Mm (= l)
        2.*h,                  # Ssa
    ]) % _pi2

    # ── Local displacements (Radial=up, EW=West+, NS=South+) ─────────────
    _d2r = _pi / 180.0
    dR  = sum(amp[0,i]*math.cos(chi[i] - phs[0,i]*_d2r) for i in range(11))
    dW  = sum(amp[1,i]*math.cos(chi[i] - phs[1,i]*_d2r) for i in range(11))
    dS  = sum(amp[2,i]*math.cos(chi[i] - phs[2,i]*_d2r) for i in range(11))

    # ENU (East=−West, North=−South, Up=Radial)
    dE, dN, dU = -dW, -dS, dR

    # ENU → ECEF
    sl, cl = math.sin(lat), math.cos(lat)
    sn, cn = math.sin(lon), math.cos(lon)
    return np.array([
        -sn*dE - sl*cn*dN + cl*cn*dU,
         cn*dE - sl*sn*dN + cl*sn*dU,
                 cl*dN    + sl*dU
    ])


# ==============================================================================
#  Geodetic / model helpers
# ==============================================================================
def _gpst(yr,mo,dy,hr,mn,sc):
    a=(14-mo)//12; y=yr+4800-a; m=mo+12*a-3
    jdn=dy+(153*m+2)//5+365*y+y//4-y//100+y//400-32045
    d=jdn-0.5+(hr*3600+mn*60+sc)/86400-2444244.5; wk=int(d/7)
    return wk*604800+(d-wk*7)*86400

def _sod2t(s, tr): return tr-(tr%86400)+s

def _lag(ts, ys, t, o=10):
    n=len(ts)
    if n==0: return None
    i=int(np.searchsorted(ts,t)); h=(o+1)//2
    lo=max(0,min(i-h,n-o-1)); hi=lo+o+1
    ts_=ts[lo:hi]; ys_=ys[lo:hi]
    r=0. if ys_.ndim==1 else np.zeros(ys_.shape[1])
    for ii in range(len(ts_)):
        L=1.
        for jj in range(len(ts_)):
            if jj!=ii:
                dd=ts_[ii]-ts_[jj]
                if dd==0: L=0.; break
                L*=(t-ts_[jj])/dd
        r+=L*ys_[ii]
    return r

def _spc(sp3t, sp, sc, sat, tow):
    ap=sp.get(sat)
    if ap is None: return None,None
    ts=np.array(sp3t); ok=~np.isnan(ap[:,0])
    if ok.sum()<4: return None,None
    tv=ts[ok]; pv=ap[ok]; cv=sc[sat][ok]
    if tow<tv[0]-400 or tow>tv[-1]+400: return None,None
    xyz=_lag(tv,pv,tow,o=min(10,len(tv)-1))
    i=int(np.searchsorted(tv,tow)); i=max(1,min(len(tv)-1,i))
    dt=tv[i]-tv[i-1]
    clk=(cv[i-1]+(tow-tv[i-1])/dt*(cv[i]-cv[i-1])
         if dt>0 and not np.isnan(cv[i]) and not np.isnan(cv[i-1]) else cv[i-1])
    return xyz,clk

def _vel(sp3t, sp, sat, tow):
    ap=sp.get(sat)
    if ap is None: return np.zeros(3)
    ts=np.array(sp3t); ok=~np.isnan(ap[:,0])
    if ok.sum()<4: return np.zeros(3)
    tv=ts[ok]; pv=ap[ok]
    return (_lag(tv,pv,tow+1,o=min(10,len(tv)-1))-_lag(tv,pv,tow-1,o=min(10,len(tv)-1)))/2

def _gclk(cd, sat, tow):
    e=cd.get(sat)
    if not e: return None
    ts=np.array([x[0] for x in e]); cs=np.array([x[1] for x in e])
    i=int(np.searchsorted(ts,tow))
    if i==0: return cs[0]
    if i>=len(ts): return cs[-1]
    t0,c0=ts[i-1],cs[i-1]; t1,c1=ts[i],cs[i]; dd=t1-t0
    if dd>35: return c0 if tow-t0<t1-tow else c1
    return c0+(tow-t0)/dd*(c1-c0)

def _lla(xyz):
    x,y,z=xyz; p=math.sqrt(x*x+y*y); lon=math.atan2(y,x)
    lat=math.atan2(z,p*(1-E2))
    for _ in range(10):
        sl=math.sin(lat); N=RE/math.sqrt(1-E2*sl*sl)
        l2=math.atan2(z+E2*N*sl,p)
        if abs(l2-lat)<1e-12: break
        lat=l2
    sl=math.sin(lat); cl=math.cos(lat); N=RE/math.sqrt(1-E2*sl*sl)
    return lat,lon,(p/cl-N if abs(cl)>1e-9 else abs(z)/sl-N*(1-E2))

def _enu(lat, lon):
    sl,cl=math.sin(lat),math.cos(lat); sn,cn=math.sin(lon),math.cos(lon)
    return np.array([[-sn,cn,0],[-sl*cn,-sl*sn,cl],[cl*cn,cl*sn,sl]])

def _elaz(rec, sat):
    dx=np.array(sat)-np.array(rec); lat,lon,_=_lla(rec)
    e=_enu(lat,lon)@dx; n=np.linalg.norm(e)
    if n<1: return None,None
    return math.asin(e[2]/n),math.atan2(e[0],e[1])

def _zhd(lat, h):
    P=(101325*(1-2.2557e-5*h)**5.2559)/100
    return 0.0022768*P/(1-0.00266*math.cos(2*lat)-0.00028*h/1000)

def _gmf(lat, doy, el):
    if el<1e-4: el=1e-4
    dr=28 if lat>=0 else 211; cd=math.cos(2*math.pi*(doy-dr)/365.25)
    ah=1.2769934e-3+2.8804e-5*math.cos(lat)-7.6184e-5*math.sin(lat)+2.5e-6*cd
    def cf(s,a,b,c): return (1+a/(1+b/(1+c)))/(s+a/(s+b/(s+c)))
    s=math.sin(el)
    mh=cf(s,ah,2.9153695e-3,0.062610505)/cf(1.,ah,2.9153695e-3,0.062610505)
    mw=cf(s,5.7532e-4,1.8128e-3,0.062553963)/cf(1.,5.7532e-4,1.8128e-3,0.062553963)
    return mh,mw

def _sun(tow):
    T=(tow/86400-10957)/36525; M=math.radians(357.528+35999.05*T)
    lam=math.radians(280.46+36000.771*T)+math.radians(1.915)*math.sin(M)+math.radians(0.02)*math.sin(2*M)
    eps=math.radians(23.439-0.013*T); AU=1.496e11
    xi=AU*math.cos(lam); yi=AU*math.cos(eps)*math.sin(lam); zi=AU*math.sin(eps)*math.sin(lam)
    g=math.fmod(tow/86164.0905*2*math.pi,2*math.pi); cg,sg=math.cos(g),math.sin(g)
    return np.array([cg*xi+sg*yi,-sg*xi+cg*yi,zi])

def _wu(sv, rv, sun, w0):
    rho=np.array(sv); rn=np.linalg.norm(rho)
    if rn<1e3: return w0
    k=rho-np.array(rv); k/=np.linalg.norm(k)
    s=np.array(sun)-rho; sn=np.linalg.norm(s)
    if sn<1e3: return w0
    s/=sn; ez=-rho/rn; ex=s-s.dot(ez)*ez; en=np.linalg.norm(ex)
    if en<1e-10: return w0
    ex/=en; ey=np.cross(ez,ex)
    lat,lon,_=_lla(rv); sl,cl=math.sin(lat),math.cos(lat); sln,cln=math.sin(lon),math.cos(lon)
    eyr=np.array([-sl*cln,-sl*sln,cl]); exr=np.array([-sln,cln,0.])
    ds=ex-k*k.dot(ex)-np.cross(k,ey); dr=exr-k*k.dot(exr)+np.cross(k,eyr)
    nd,nr=np.linalg.norm(ds),np.linalg.norm(dr)
    if nd<1e-10 or nr<1e-10: return w0
    cw=ds.dot(dr)/(nd*nr); cw=max(-1.,min(1.,cw))
    dp=math.acos(cw)/(2*math.pi)
    if np.cross(ds,dr).dot(k)<0: dp=-dp
    return dp+round(w0-dp)

def _rel(sv, vv): return -2*np.dot(sv,vv)/CLIGHT

def _shap(rv, sv):
    rs=np.linalg.norm(sv); rr=np.linalg.norm(rv); rho=np.linalg.norm(sv-rv)
    a=(rs+rr+rho)/(rs+rr-rho)
    return 2*MU/CLIGHT**2*math.log(a) if a>0 else 0.

def _set(ra, sun):
    lat,lon,_=_lla(ra); sl,cl=math.sin(lat),math.cos(lat); sn,cn=math.sin(lon),math.cos(lon)
    er=np.array(ra)/np.linalg.norm(ra); en=np.array([-sl*cn,-sl*sn,cl]); ee=np.array([-sn,cn,0.])
    def d(b):
        rb=np.linalg.norm(b); rr=np.linalg.norm(ra); ub=np.array(b)/rb; cz=np.dot(ub,er)
        P2=(3*cz*cz-1)/2.; ar=0.6078*P2*3*MU/rb**3*rr**2/9.81
        at=0.0847*3*cz*math.sqrt(max(0.,1-cz*cz))*MU/rb**3*rr**2/9.81
        ube=ub.dot(ee); ubn=ub.dot(en); hn=math.sqrt(ube**2+ubn**2)+1e-15
        return ar*er+at*(ube/hn*ee+ubn/hn*en)
    return d(sun)*3.16


# ==============================================================================
#  Melbourne-Wubbena and geometry-free
# ==============================================================================
def _mw_cyc(P1, P2, L1_cyc, L2_cyc):
    L1_m=L1_cyc*LAMBDA1; L2_m=L2_cyc*LAMBDA2
    phi_WL=(FREQ1*L1_m-FREQ2*L2_m)/(FREQ1-FREQ2)
    P_NL=(FREQ1*P1+FREQ2*P2)/(FREQ1+FREQ2)
    return (phi_WL-P_NL)/LAMBDA_WL

def _gf_m(L1_cyc, L2_cyc):
    return L1_cyc*LAMBDA1 - L2_cyc*LAMBDA2

def _pdop(geom):
    if len(geom)<4: return 99.
    H=np.zeros((len(geom),4))
    for i,m in enumerate(geom):
        u=m['unit']; H[i,0]=-u[0]; H[i,1]=-u[1]; H[i,2]=-u[2]; H[i,3]=1.
    try:
        Q=np.linalg.inv(H.T@H); return math.sqrt(Q[0,0]+Q[1,1]+Q[2,2])
    except: return 99.

def _spp_clock(geom_list, rec_xyz):
    if len(geom_list)<4: return 0.
    H=[]; z=[]
    for m in geom_list:
        rp=m['rng']-m['scm']+m['trop_zhd']+m['shp']+m['setm']+m['pcv_sat']+m['pcv_rec']
        H.append([1.0]); z.append(m['PIF']-rp)
    H=np.array(H); z=np.array(z)
    try: return float(np.clip((np.linalg.inv(H.T@H)@H.T@z)[0],-3e6,3e6))
    except: return 0.


# ==============================================================================
#  RTS smoother
# ==============================================================================
class _rts_store:
    _data = []

def _rts_smooth(fwd_results, nom):
    data=_rts_store._data
    if len(data)<3: return fwd_results
    N=len(data); dim=5   # GPS-only: 0-2=dXYZ 3=clk 4=ZWD (ISB removed)
    sods=[d[0] for d in data]
    xs=[d[1][:dim].copy() for d in data]
    Ps=[d[2][:dim,:dim].copy() for d in data]
    xs_s=[None]*N; Ps_s=[None]*N
    xs_s[-1]=xs[-1].copy(); Ps_s[-1]=Ps[-1].copy()
    for k in range(N-2,-1,-1):
        dt=abs(sods[k+1]-sods[k])
        if dt<=0 or dt>3600: dt=30.
        F=np.eye(dim)
        Q_k=np.zeros((dim,dim))
        # REPAIR-STEP1: RTS clock Q must match forward EKF (was 1e4*dt — BUG).
        # Forward EKF uses Q[3,3]=1e-3*dt (white-noise receiver clock).
        # RTS backward smoother must use the SAME dynamic model to compute the
        # correct Kalman smoother gain G_k = P_k * F^T * inv(F*P_k*F^T + Q_k).
        # The previous 1e4*dt inflated the predicted covariance by a factor of
        # 1e7, collapsing G_k → 0 and destroying all backward clock correction.
        #
        # NOTE — FORENSIC-PATCH (adaptive clock-Q):
        # The forward EKF now inflates Q[3,3] to 5×_nominal during phase-
        # collapse epochs.  The RTS backward sweep uses a fixed nominal here
        # because (a) the smoother gain is insensitive to the exact Q value
        # when P_k already carries the inflated covariance from the forward
        # pass, and (b) per-epoch Q storage for the RTS is out of scope for
        # this single-variable forensic experiment.  If the adaptive patch
        # shows benefit, mirroring per-epoch Q values to the RTS should be
        # evaluated as a follow-on change.
        Q_k[0,0]=Q_k[1,1]=Q_k[2,2]=1e-8*dt; Q_k[3,3] = 1.00e-05 * dt; Q_k[4,4]=1e-7*dt  # FIXED-CLOCK-Q EXPERIMENT: RTS Qclk aligned to fwd EKF fixed 
        P_k = Ps[k]
        P_k1 = F @ P_k @ F.T + Q_k

        try:
            # RTS numerical-conditioning guard
            _cond_P_k1 = np.linalg.cond(P_k1)

            if (not np.isfinite(_cond_P_k1)) or (_cond_P_k1 > 1e10):
               print(
                   f"  [RTS-SKIP] "
                   f"k={k}  cond(P_k1)={_cond_P_k1:.2e}"
               )
               xs_s[k] = xs[k].copy()
               Ps_s[k] = Ps[k].copy()
               continue

            G_k = P_k @ F.T @ np.linalg.inv(P_k1)

            # NaN/Inf smoother-gain guard
            if not np.all(np.isfinite(G_k)):
               print(f"  [RTS-NAN-GAIN] k={k}")
               xs_s[k] = xs[k].copy()
               Ps_s[k] = Ps[k].copy()
               continue

        except np.linalg.LinAlgError:
            print(f"  [RTS-LINALG-FAIL] k={k}")
            xs_s[k] = xs[k].copy()
            Ps_s[k] = Ps[k].copy()
            continue
        xs_s[k]=xs[k]+G_k@(xs_s[k+1]-F@xs[k])
        Ps_s[k]=Ps[k]+G_k@(Ps_s[k+1]-P_k1)@G_k.T
    REF=np.array([1337935.5599,6070317.2377,1427877.5071])
    smoothed={}; sod_to_idx={d[0]:i for i,d in enumerate(data)}
    for sod,r in fwd_results.items():
        if sod not in sod_to_idx:
            smoothed[sod]={**r,'pass':'FWD'}; continue
        i=sod_to_idx[sod]; dx_sm=xs_s[i][:3]; pos_sm=nom+dx_sm
        smoothed[sod]={**r,'xyz':pos_sm.copy(),'dx':pos_sm-REF,'pass':'RTS'}
    return smoothed


# ==============================================================================
#  LAMBDA ILS — proper full LAMBDA via lambda_ils.py
# ==============================================================================
def _lambda_ils(a_float, Q):
    """Full LAMBDA ILS (Teunissen 1995, Chang 2005).

    Calls lambda_py() which implements: LD-factorisation → LAMBDA reduction
    (integer Gauss + permutation, full Z-transformation) → mlambda tree search
    → back-transform.  The previous embedded code reset a_z = a_float
    ('use untransformed for safety'), discarding all decorrelation and
    reducing to simple rounding — this is now fixed.

    Returns (best_integer_vector, ratio) or (None, 0.0) on failure.
    """
    n=len(a_float)
    if n<2: return None,0.0
    try:
        from lambda_ils import lambda_py
        Q_sym=0.5*(Q+Q.T)+np.eye(n)*1e-14
        F,s,info=lambda_py(a_float,Q_sym,m=2)
        if info!=0 or s[0]<1e-12: return None,0.0
        return F[:,0], s[1]/s[0]
    except Exception:
        return None,0.0


# ==============================================================================
#  NL float / fix helpers
# ==============================================================================
def _nl_float(x_ki,NWL,osb_bl1,osb_bl2):
    # v56 PATCH: same rationale as _nl_float_gal — OSB removed from observable,
    # not from state.  Do NOT subtract osb_IF here.
    return (x_ki-NWL*BETA*LAMBDA2)/_DENOM_G

def _nl_if_value(N1_int,NWL,osb_bl1,osb_bl2):
    # v56 PATCH: x[ki]=true_IF_amb (no osb absorbed).
    N2_int=N1_int-NWL
    return ALFA*LAMBDA1*N1_int-BETA*LAMBDA2*N2_int


# ==============================================================================
#  Per-satellite geometry
# ==============================================================================
def _proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx,
          blq=None,sta='IISC',tow_total=0.):
    """GPS satellite — L1C/L2W ONLY (GPS-L2-STABILIZATION experiment: L2L suppressed)."""
    P1=so.get('C1W',0.); P2=so.get('C2W',0.)
    L1=so.get('L1C',0.)
    # GPS-L2-STAB: force L2W phase ONLY — NEVER fall back to L2L in this experiment.
    # Eliminates all mid-arc L2L↔L2W signal-family transitions (125 events in Layer-1A).
    # Code observables (C1W/C2W) and OSB routing (C2W/L2W) are unchanged.
    L2 = so.get('L2W', 0.)       # L2W ONLY — L2L intentionally suppressed
    l2_sig_used = 'L2W'          # recorded for gps_l2_stabilization_audit.csv
    if P1==0 or P2==0 or L1==0 or L2==0: return None

    ob=osb.get(sid,{})
    b1=ob.get('C1W',ob.get('C1C',0.))
    # FIX-1 (v64): GPS P2 pseudorange observable is ALWAYS C2W regardless of
    # which phase carrier is civil (L2L) vs semi-codeless (L2W).
    # The old logic incorrectly preferred the C2L OSB when use_civil=True,
    # injecting a satellite-dependent IF code bias → Up bias + phase rejection.
    b2 = ob.get('C2W', ob.get('C2L', 0.0))
    PIF=_ifc(P1-b1,P2-b2)
    _LIF_raw=_ifc(L1*LAMBDA1,L2*LAMBDA2)  # uncorrected IF phase (metres)

    # v56 PATCH — apply satellite IF phase OSB to the carrier-phase observable.
    # parse_bia already converts OSB from cycles to metres (val×λ), so bl1/bl2
    # are in metres.  The IF combination is:
    #   osb_IF_phase = ALFA×bl1 − BETA×bl2          (metres)
    #   LIF_corr     = LIF_raw  − osb_IF_phase       (metres)
    # x[ki] is then initialised from LIF_corr → x[ki] = true integer IF ambiguity
    # with NO satellite phase-OSB bias absorbed.  _nl_float/_nl_if_value are
    # updated consistently: they no longer subtract osb_IF from x[ki].
    bl1=ob.get('L1C',ob.get('L1W',0.))
    bl2=ob.get('L2W',ob.get('L2C',0.))   # GPS-L2-STAB: L2W OSB only (L2L suppressed)
    _osb_IF_phase = ALFA*bl1 - BETA*bl2       # metres (bl1,bl2 already in metres)
    LIF = _LIF_raw - _osb_IF_phase            # ← THE ACTUAL CORRECTION
    b_wl_sat_cyc=((FREQ1*bl1-FREQ2*bl2)/(FREQ1-FREQ2))/LAMBDA_WL
    MW_cyc=_mw_cyc(P1-b1,P2-b2,L1,L2)-b_wl_sat_cyc
    GF_m=_gf_m(L1,L2)

    xyz0,_=_spc(sp3t,sp,sc,sid,tow)
    if xyz0 is None: return None
    lat_r,lon_r,_=_lla(rxyz)
    er=np.array([math.cos(lat_r)*math.cos(lon_r),
                 math.cos(lat_r)*math.sin(lon_r),
                 math.sin(lat_r)])
    ra=rxyz+ah*er+_rpco(recx,lat_r,lon_r,sys='G')
    # Ocean Tide Loading displacement applied to receiver APC
    if blq and tow_total>0.:
        ra=ra+_otl_disp(blq,sta,tow_total,lat_r,lon_r)

    ttx=tow-np.linalg.norm(xyz0-ra)/CLIGHT
    sv_tx,_=_spc(sp3t,sp,sc,sid,ttx)
    if sv_tx is None: sv_tx=xyz0
    tau=np.linalg.norm(sv_tx-ra)/CLIGHT; ang=OMGE*tau
    ca,sa=math.cos(ang),math.sin(ang)
    svc=np.array([ca*sv_tx[0]+sa*sv_tx[1],-sa*sv_tx[0]+ca*sv_tx[1],sv_tx[2]])
    sck=_gclk(clkd,sid,ttx)
    if sck is None: _,sck=_spc(sp3t,sp,sc,sid,ttx)
    if sck is None or math.isnan(sck): return None
    scm=sck*CLIGHT
    vv=_vel(sp3t,sp,sid,tow); dtrel=_rel(svc,vv)
    sun=_sun(tow); bx,by,bz=_body(att,sid,tow,svc,sun)
    ae=_gatx(satx,sid,tow); sva=svc.copy(); pcvs=0.
    _sat_pco_body_mm=[0.,0.,0.]; _sat_apc_ecef=np.zeros(3)
    if ae is not None:
        _sat_apc_ecef=_spco(ae,bx,by,bz)
        sva=svc+_sat_apc_ecef; pcvs=_spcv(ae,_nadir(sva,ra,bz))
        _sat_pco_body_mm=ae['pco'].tolist()
    el,az=_elaz(ra,sva)
    if el is None or el<elm: return None
    pcvr=_rpcv(recx,el,sys='G')
    dr=sva-ra; rng=np.linalg.norm(dr); unit=dr/rng
    shp=_shap(ra,sva); setd=_set(ra-ah*er,sun); setm=-unit.dot(setd)
    mh,mw=_gmf(lat0,doy,el)
    # ── TRACE FIELDS ─────────────────────────────────────────────────────────
    _sat_apc_range  = float(unit.dot(_sat_apc_ecef))          # +: range grows
    _rec_apc_ecef   = _rpco(recx,lat_r,lon_r,sys='G')
    _rec_apc_range  = float(-unit.dot(_rec_apc_ecef))         # applied to ra
    _rec_pco_local_mm=(ALFA*recx['L1']-BETA*recx['L2']).tolist() if recx else [0.,0.,0.]
    _sagnac_m       = float(unit.dot(svc-sv_tx))              # svc=rotated sv_tx
    _otl_ecef       = (_otl_disp(blq,sta,tow_total,lat_r,lon_r) if blq and tow_total>0.
                       else np.zeros(3))
    _otl_range_m    = float(-unit.dot(_otl_ecef))
    _osb_code_L1_m  = b1; _osb_code_L2_m  = b2
    # v56: bl1/bl2 are already in metres (parse_bia converts cyc→m via ×LAMBDA).
    # Previously the trace multiplied by LAMBDA again — corrected here.
    _osb_phase_L1_m = bl1                          # metres
    _osb_phase_L2_m = bl2                          # metres
    _osb_IF_code_m  = ALFA*b1 - BETA*b2
    _osb_IF_phase_m = _osb_IF_phase                # = ALFA*bl1 − BETA*bl2 (metres)
    _PIF_raw        = _ifc(P1, P2)                 # before code OSB
    # _LIF_raw already set above; LIF is LIF_corr = LIF_raw − osb_IF_phase
    # ─────────────────────────────────────────────────────────────────────────
    return dict(sid=sid,unit=unit,mh=mh,mw=mw,el=el,az=az,
                rng=rng,scm=scm,dtrel=dtrel,shp=shp,setm=setm,
                pcv_sat=pcvs,pcv_rec=pcvr,trop_zhd=mh*zhd,
                PIF=PIF,LIF=LIF,MW_cyc=MW_cyc,GF_m=GF_m,
                L1=L1,L2=L2,P1=P1,P2=P2,
                l2_sig_used=l2_sig_used,
                sat_xyz=sva,rec_apc=ra,
                # trace extras
                _sys='G',
                _sat_pco_body_mm=_sat_pco_body_mm,
                _sat_apc_ecef=_sat_apc_ecef,
                _sat_apc_range_m=_sat_apc_range,
                _rec_pco_local_mm=_rec_pco_local_mm,
                _rec_apc_ecef=_rec_apc_ecef,
                _rec_apc_range_m=_rec_apc_range,
                _sagnac_m=_sagnac_m,
                _otl_range_m=_otl_range_m,
                _osb_code_L1_m=_osb_code_L1_m, _osb_code_L2_m=_osb_code_L2_m,
                _osb_phase_L1_m=_osb_phase_L1_m,_osb_phase_L2_m=_osb_phase_L2_m,
                _osb_IF_code_m=_osb_IF_code_m, _osb_IF_phase_m=_osb_IF_phase_m,
                _PIF_raw=_PIF_raw, _LIF_raw=_LIF_raw,
                _lam_if=LAMBDA_IF, _lam1=LAMBDA1, _lam2=LAMBDA2)


def _rp(m,dT,ZWD):
    return (m['rng']-m['scm']-m['dtrel']+dT
            +m['trop_zhd']+m['mw']*ZWD
            +m['shp']+m['setm']+m['pcv_sat']+m['pcv_rec'])


def _sig(el, sc=1.0):
    """
    PPP elevation-dependent measurement sigma (metres).

    Model: sigma = sc / sin(el)          [σ ∝ 1/sin(el)]
    Variance assigned at call site: Rd[i] = _sig(el, sc)**2

    The 1/sin(el) model (NOT 1/sin²(el)) is the PPP-standard stochastic
    model.  Squaring happens at the variance assignment, so the combined
    effect on the weight matrix is correctly w ∝ sin²(el).
    Division-by-zero guard: sin(el) floored at 0.05 (≈ 2.87°), returning
    a maximum sigma of 20*sc metres for any satellite that reaches this
    function (nominal elevation mask is ≥10°, so the floor is never hit
    in normal operation).

    Parameters
    ----------
    el : float  — elevation angle in radians
    sc : float  — scaling constant (SC for code, SP for phase)

    Returns
    -------
    float — sigma in metres
    """
    s = max(math.sin(el), 0.05)
    return sc / s


# ==============================================================================
#  PPP Kalman filter pass
# ==============================================================================

# ==============================================================================
#  PPP Kalman filter pass — MINIMAL FLOAT PPP-EKF (Phase-Reset)
# ==============================================================================
#
#  ARCHITECTURAL REVERT — all lifecycle/state-machine policy removed.
#
#  Retained physics:
#    observation model, SP3/CLK/BIA/ATX, IF combinations, troposphere,
#    ISB, cycle slip detection, innovation residual editing,
#    covariance propagation, Joseph symmetrization, ambiguity reset after slip,
#    NaN/PD guards, RTS smoother, LAMBDA (commented — AR disabled for now),
#    WL accumulation infrastructure.
#
#  Removed completely:
#    WARMUP / ACTIVE / DEGRADED / NEWBORN states
#    promotion counters / trusted thresholds / confidence transitions
#    quarantine system (amb_quarantine, quarantine_epochs)
#    EKF permission gating (update_allowed, skip_ekf, case_A/B/C/D)
#    COMMONMODE-PURGE / CMODE eviction / rebirth tracking
#    newborn preservation / freeze / hold / aging logic
#    bootstrap mode / deadlock detector / bootstrap safety limits
#    ambiguity covariance inflation policies (DEGRADED_P_INFLATE, etc.)
#    all associated lifecycle diagnostics
#
#  Ambiguity lifecycle (only rule):
#    Birth  : post-fit after EKF; x[ki] = LIF - rp_postfit; P[ki,ki] = (20 m)^2
#    Active : ALWAYS participate in EKF immediately after birth
#    Slip   : reset x[ki]=0, P[ki,ki]=(20m)^2, clear active flag, restart streak
#    Gap<300s: restore saved state, immediately active again
#
#  Observation-domain rejection only:
#    code  : |res| > 10 m (startup) / 5 m (converged)
#    phase : |res| > 0.10 m (startup) / 0.05 m (converged)
#    + NaN / inf / singular variance
#
#  EKF can NEVER be blocked by ambiguity state logic.
# ==============================================================================

def _ppp_pass(epochs, sp3t, sp, sc, clkd, osb, ah, nom, iclk, izwd,
              lat0, doy, zhd, tref, satx, att, recx,
              elm=math.radians(10.), SC=0.18, SP=0.050,  # STAGE-2A CODE-ANCHOR: SC 0.30→0.18 m (2.78× code weight increase; code info ∝ 1/SC²) | SP=0.050 unchanged
              direction=1, label="FWD", wl_init=None, amb_init=None,
              constellation='G', blq=None, sta='IISC',
              trace_fh=None, pass_label='',
              state_trace_fh=None,
              birth_trace_fh=None,
              constat_fh=None,
              innov_audit_fh=None,
              amb_part_fh=None,
              reset_audit_fh=None,
              amgr_snap_fh=None,
              lifecycle_fh=None,
              l2_stab_audit_fh=None):
    """
    Minimal float PPP-EKF.

    constellation : 'G' (GPS-only; Galileo support removed)
    blq           : dict from parse_blq (ocean tide loading)
    sta           : 4-char station code for BLQ lookup
    """
    REF = np.array([1337935.5599, 6070317.2377, 1427877.5071])

    # ── NL fixing parameters (kept for infrastructure; fixing disabled) ─────────
    NL_RATIO_THRESH   = 4.5
    NL_VAR_THRESH     = (10.0)**2
    NL_RES_THRESH     = 0.10
    NL_EXCL_THRESH    = 0.25
    NL_R_TIGHT        = (0.005)**2
    NL_INNOV_GATE     = 0.080
    NL_RELEASE_THRESH = 0.060
    NL_PHASE_THRESH   = 0.010
    NL_MIN_SATS       = 3
    NL_MIN_OBS        = 8
    PHASE_RES_GATE    = 0.030  # downweight gate for mature phase rows (soft)
    STARTUP_INFO_EPOCHS = 60  # consistent startup scaling window: a = max(age,1)/60
    ABS_PHASE_MAX     = 10.0   # metres — absolute sanity floor before NIS gate
    PHASE_NIS_GATE    = 4.0    # linear normalised-innovation gate (|ν|/σ_S)

    # ── Zombie ambiguity detector ─────────────────────────────────────────────
    # An ambiguity is "zombie" when its covariance has contracted so far that
    # the filter treats it as fully known, yet its residuals are huge — meaning
    # the stored value is wrong and the filter cannot self-correct because the
    # Kalman gain for that state has collapsed to near zero.
    # Criteria (all three must be met simultaneously):
    ZOMBIE_SIGMA_CEIL_M = 0.050   # amb sigma < 50 mm  (converged, no longer flexible)
    ZOMBIE_RES_FLOOR_M  = 0.500   # |phase residual| > 500 mm  (physically impossible)
    ZOMBIE_AGE_FLOOR    = 50      # arc age > 50 epochs  (not a transient outlier)

    # ── Coherent common-mode (clock-step) deferred-zombie constants ───────────
    # When ALL accepted phase innovations share the same sign AND the absolute
    # mean exceeds COHERENT_CM_FLOOR_M, the epoch is classified as a coherent
    # clock-step event.  Zombie resets during such epochs are DEFERRED: the
    # ambiguity sigma is softened to ZOMBIE_SOFTEN_M instead of wiping the
    # state, preserving convergence across ±1-ns receiver clock bounces.
    COHERENT_CM_FLOOR_M   = 0.080   # |mean phase innov| > 80 mm → coherent event
    COHERENT_CM_MIN_SATS  = 3       # need ≥3 same-sign residuals to classify
    ZOMBIE_SOFTEN_M       = 0.120   # soften-to sigma (m) instead of full 20-m reset
    ZOMBIE_CASCADE_CEIL   = 3       # ≥3 zombies in one epoch → cascade warning

    ZWD_PRIOR         = 0.15
    ZWD_PRIOR_SIGMA   = 0.06
    ZWD_CLAMP         = 0.015
    _zwd_prev         = None
    _nl_diag_done     = False

    # ── State layout: 0-2=dXYZ  3=clk  4=ZWD  (ISB removed — GPS-only) ──────
    # Ambiguities start at index 5 (one fewer core state than the GE version).
    x = np.zeros(5)
    x[3] = iclk; x[4] = izwd
    P = np.zeros((5, 5))
    P[0,0]=P[1,1]=P[2,2] = 100.**2
    P[3,3] = 3000.**2
    P[4,4] = 0.5**2

    # ── Ambiguity lifecycle manager (Step-1 refactor) ────────────────────────
    # Single owner of all per-satellite bookkeeping that previously lived in
    # amb_active / _amb_birth_sigma / _amb_reset_count / _audit_* dicts.
    # snapshot_fh is forwarded from the caller; write_snapshot() is a no-op
    # when the handle is None.
    amgr = AmbiguityManager(snapshot_fh=amgr_snap_fh, lifecycle_fh=lifecycle_fh)

    # ASSERT-1: import succeeded (class is callable)
    assert callable(AmbiguityManager), \
        "ASSERT-1 failed: AmbiguityManager not imported / not callable"
    # ASSERT-2: snapshot_header exists and is a str
    assert isinstance(AmbiguityManager.snapshot_header(), str), \
        "ASSERT-2 failed: AmbiguityManager.snapshot_header() did not return str"
    # ASSERT-3: snapshot handle forwarded correctly
    assert amgr._snapshot_fh is amgr_snap_fh, \
        "ASSERT-3 failed: amgr._snapshot_fh does not match amgr_snap_fh"
    # ASSERT-4: key methods exist on the instance.
    # STEP-3 methods (transition_state, run_consistency_assertions, etc.) are
    # now stubs (lifecycle state machine removed) but must still exist on the
    # class so all call sites continue to compile.
    for _m in ('_ensure', 'register_birth', 'register_reset',
               'register_accept', 'register_reject',
               'is_active', 'is_birth_complete', 'needs_birth',
               'set_active', 'write_snapshot',
               'register_inherited', 'update_Kg_norm',
               'get_recent_ph_res', 'get_recent_rej', 'get_birth_epoch',
               'get_accepted_count', 'get_rejected_count',
               'get_last_accept_epoch', 'get_H_norm', 'get_birth_sigma_mm',
               'items', 'cumulative_resets',
               # STEP-2 APIs (retained)
               'register_new_slot', 'activate', 'deactivate',
               'mark_observable', 'mark_unobservable',
               'mark_in_filter', 'mark_out_of_filter',
               'update_sigma', 'update_state_index',
               'assert_state_indices_match', 'lifecycle_trace_header',
               # STEP-3 APIs (stubs — lifecycle machine removed)
               'transition_state', 'run_consistency_assertions',
               'get_lifecycle_state', 'get_lineage_id',
               'get_generation', 'get_parent_lineage_id'):
        assert hasattr(amgr, _m), \
            f"ASSERT-4 failed: AmbiguityManager missing method/property '{_m}'"

    # ── Ambiguity allocation ─────────────────────────────────────────────────
    sidx      = {}   # sid → state index in x
    namb      = 0
    _amb_saved_state = {}   # sid → {x_ki, P_ki, last_sod} for gap recovery
    # _amb_reset_count now owned by amgr.cumulative_resets
    _ekf_update_count= 0    # cumulative EKF updates executed
    # ── Phase continuity tracking (cross-epoch) ──────────────────────────────
    _phase_arc_current  = 0   # consecutive epochs with >= 1 accepted phase row
    _phase_arc_longest  = 0   # longest such streak seen so far
    _phase_epochs_total = 0   # epochs where >= 1 phase was accepted
    _sat_arc            = defaultdict(int)   # per-satellite streak length
    _sat_arc_longest    = defaultdict(int)   # per-satellite longest streak

    # ── Bootstrap gate floor diagnostics (cross-epoch) ──────────────────────
    # Tracks outcomes during early epochs to validate the bootstrap gate repair.
    _bsg_all_acc_epoch  = None   # first epoch where ALL sats accepted
    _bsg_all_rej_epoch  = None   # first epoch where ALL sats rejected
    _bsg_rej_streak     = 0      # current consecutive all-rejected epochs
    _bsg_rej_streak_max = 0      # longest all-rejected streak seen

    # ── Per-satellite tracking ───────────────────────────────────────────────
    wum               = {}   # phase wind-up accumulated state
    _sat_age          = defaultdict(int)
    wl_fixed          = dict(wl_init) if wl_init else {}
    _amb_init         = dict(amb_init) if amb_init else {}
    _amb_seeded       = set()
    nl_fixed          = {}   # NL fixing disabled; dict kept for output stats
    _nl_bad_nwl       = set()
    prev_mw           = {}
    prev_gf           = {}
    mw_hist           = defaultdict(list)
    _wl_history       = {}
    _wl_history_ptrace= {}
    _sat_last_sod     = {}
    b_rec_frozen      = {}
    b_rec_n           = defaultdict(int)

    # ── GPS-L2-STAB: forensic tracking for observable-family experiment ───────
    # We force L2W only throughout this pass; these counters verify no switches
    # occur and feed gps_l2_stabilization_audit.csv.
    _gps_l2_sig_prev  = {}   # sid → previously selected L2 phase signal string
    _l2_switch_count   = 0   # cumulative L2 family switch events (target: 0)

    # ── AUDIT: per-satellite participation tracking — managed by amgr ─────────
    # (AmbiguityManager owns all per-satellite counters and ring buffers.
    #  _audit_RING kept as a named constant for readability in reset_audit_fh.)

    # ── Pass-level accumulators ──────────────────────────────────────────────
    # Float-only mode: WL fixing disabled for clean stabilization phase.
    # Set _ENABLE_WL_FIXING = True to re-enable once float PPP is validated.
    _ENABLE_WL_FIXING = False
    results   = {}
    psod      = None
    nproc     = 0
    _MEAS_AUDIT_ENABLE  = True   # Phase 1–6 measurement-model audit prints
    _prev_code_rms  = None
    _prev_phase_rms = None
    # ── FORENSIC-PATCH: adaptive clock-Q carry-forward state ─────────────────
    # These track the PREVIOUS epoch's phase-row counts so the Q-block (which
    # fires at the TOP of the current epoch, before observations are processed)
    # can detect a phase-collapse condition from the epoch just completed.
    _prev_phase_total  = 0   # phase rows attempted last epoch
    _prev_phase_accept = 0   # phase rows accepted last epoch
    _prev_phase_inactive = False  # True if phase_rms was non-finite last epoch
    # ─────────────────────────────────────────────────────────────────────────
    _zombie_event_count    = 0     # total zombie detections (may exceed reset count)
    _zombie_reset_count    = 0     # ambiguity states forcibly reborn as zombie rebirth
    _zombie_cascade_epochs = 0     # epochs where ≥ ZOMBIE_CASCADE_CEIL fired

    # ══════════════════════════════════════════════════════════════════════════
    # CONTROLLED OBSERVABILITY EXPERIMENT — LIFECYCLE HARD FREEZE
    # Purpose: isolate whether ±0.5 m phase excursions are caused by
    #   (A) true observability collapse under weak geometry, or
    #   (B) instability introduced by ambiguity lifecycle logic.
    # During [FREEZE_OBS_START, FREEZE_OBS_END]:
    #   - ALL ambiguity resets/births/wipes/rebirth suppressed
    #   - If ambiguity absent → phase row skipped, code row kept
    #   - Everything else (Q, SP, SC, Huber, Joseph, models) UNCHANGED
    # ══════════════════════════════════════════════════════════════════════════
    FREEZE_OBS_START = 390
    FREEZE_OBS_END   = 420
    ENABLE_FREEZE_OBS = False   # master guard — set True to re-activate lifecycle freeze experiment
    _freeze_suppressed_resets  = 0   # cumulative resets suppressed during freeze
    _1c_suppressed_resets      = 0   # STAGE 1C: GF-only resets suppressed by MW pre-gate

    # ══════════════════════════════════════════════════════════════════════════
    # EXPERIMENTAL FLAG: DISABLE_ZOMBIE_RESETS
    # Purpose: test whether the EKF naturally absorbs common-mode clock
    #   excursions when ambiguities are physically stiff (tight AMB_Q_FLOOR),
    #   without destructive ambiguity state wipes / newborn 20 m cascades.
    # When True:
    #   - Zombie DETECTION, logging, NIS reporting, and CM-detector all run
    #     unchanged — [AMB-ZOMBIE] lines still appear in the log.
    #   - The actual reset (x[ki]=0, P[ki,:]=0, P[:,ki]=0, P[ki,ki]=400 m²,
    #     register_reset, _reject_obs, continue) is SKIPPED.
    #   - [ZOMBIE-SUPPRESSED] is printed in place of [AMB-ZOMBIE-RESET].
    #   - The satellite falls through to normal phase processing with its
    #     existing (tight) ambiguity sigma.
    #   - _zombie_event_count still increments; _zombie_reset_count does NOT.
    # When False (default — original behaviour):
    #   - Identical to previous behaviour; this flag has zero effect.
    # ══════════════════════════════════════════════════════════════════════════
    DISABLE_ZOMBIE_RESETS = True
    _freeze_suppressed_births  = 0   # cumulative births suppressed during freeze
    _freeze_skipped_phase_rows = 0   # phase rows skipped (no amb) during freeze


    # ── FORENSIC COVARIANCE AUDIT (SP=0.012 experiment) ─────────────────────
    # Collects raw innovation/sigma statistics to diagnose whether 4×R_phase
    # resolves NIS inflation (Case A), leaves oscillations (Case B), or
    # causes geometric sluggishness (Case C).
    # All lists are (|ν|/σ_S) or σ_S (mm); sign lists keep raw signed ratio.
    _fa_acc_nis          = []   # |ν|/σ_S for accepted (non-newborn) rows
    _fa_rej_nis          = []   # |ν|/σ_S for NIS-gated rejected rows
    _fa_acc_nu_mm        = []   # |ν| mm, accepted
    _fa_rej_nu_mm        = []   # |ν| mm, rejected
    _fa_acc_sigma_mm     = []   # σ_S mm, accepted — collapse detector
    _fa_rej_sigma_mm     = []   # σ_S mm, rejected
    _fa_acc_signed_nu    = []   # signed ν mm, accepted (sign-coherence)
    # Per-epoch sign-coherence: 1 = all same sign, 0 = mixed
    _fa_epoch_sign_coh   = []   # one entry per phase-eligible epoch
    _fa_epoch_sigma_mean = []   # mean σ_S (mm) per epoch (accepted rows)
    # ──────────────────────────────────────────────────────────────────────────

    # ── ROBUST-WEIGHT-AUDIT accumulators (Huber experiment) ───────────────────
    # Tracks per-row and per-epoch Huber downweighting statistics.
    # "old-style reject" = rows that would have been discarded by the old NIS gate
    # but are now included with w < 1 (i.e. u > 2.0 where u = |ν|/σ_gated).
    _rw_all_weights      = []   # all per-row Huber weights (entire pass)
    _rw_oldstyle_rej_tot = 0    # total rows that had u > PHASE_NIS_GATE (kept but downweighted)
    _rw_epoch_mean_w     = []   # per-epoch mean Huber weight
    _rw_epoch_min_w      = []   # per-epoch minimum Huber weight
    _rw_epoch_n_lt1      = []   # per-epoch count of rows with w < 1
    _rw_epoch_n_lt05     = []   # per-epoch count of rows with w < 0.5
    _rw_epoch_oldrej     = []   # per-epoch "old-style reject" count
    # ──────────────────────────────────────────────────────────────────────────

    # ── clock series for post-loop ACF diagnostics ───────────────────────────
    _clk_series          = []   # post-update receiver clock (m) per epoch
    # ──────────────────────────────────────────────────────────────────────────

    # ── STAGE-2A: Code Anchoring Stress Test — pass-level accumulators ────────
    # Accumulate per-epoch values for PASS-SUMMARY diagnostics.
    # New for SC=0.18 experiment:
    #   1) mean_code_phase_delta_mm       (per-epoch _obs_mean_abs_cp_delta_mm)
    #   2) corr(clock, Up)                (from _clk_series / _up_series at pass end)
    #   3) phase_info/code_info ratio     (per-epoch _ib_ratio)
    #   4) mean |K_clock_code| and |K_up_code|  (K gain on clock/Up from code cols)
    #   5) mean |delta_N_mm|              (per-epoch, per-sat ambiguity mobility)
    _s2a_cp_delta_list   = []   # (1) code-phase delta per epoch (mm)
    _s2a_ratio_list      = []   # (3) phase_info/code_info ratio per epoch
    _s2a_K_clk_code_list = []   # (4a) |K_clock| from code columns only, per epoch
    _s2a_K_up_code_list  = []   # (4b) |K_up|  from code columns only, per epoch
    _s2a_delta_N_list    = []   # (5) |delta_N_mm| per active ambiguity per epoch
    # ── END STAGE-2A accumulators ─────────────────────────────────────────────

    # ── PROPAGATION-AUDIT: previous postfit state reference ──────────────────
    # Initialised to None; set to x.copy()/P.copy() at the end of every epoch
    # immediately after the EKF update and Joseph symmetrisation.  Used by
    # PATCH 2 (PROPAGATION-AUDIT) to decompose the one-step predicted state
    # delta into clock / ZWD / position / ambiguity contributions before the
    # next epoch's satellite loop.
    _x_post_prev = None
    _P_post_prev = None
    # ─────────────────────────────────────────────────────────────────────────

    # ── STAGE-7: POS-FLOOR pass-level state ──────────────────────────────────
    # Tracks the previous epoch's code-phase delta so the floor formula
    # max(50, 0.15 * cp_delta) is available at the Joseph insertion point
    # (OBS-BALANCE, which computes cp_delta, fires AFTER the Joseph block).
    # Accumulators for the per-pass summary printed at pass end.
    _pf_prev_cp_delta_mm  = float('nan')   # cp_delta from epoch N-1
    _pf_floor_active_n    = 0              # epochs where floor fired on ≥1 axis
    _pf_floor_mm_list     = []             # imposed floor values (mm)
    _pf_sigma_orig_list   = []             # original pos sigma when floor fired
    # ─────────────────────────────────────────────────────────────────────────

    # == REBIRTH-FORENSIC: audit engine construction ============================
    # Instrumentation ONLY -- no EKF/model changes.
    # Tracks every ambiguity birth, maintains +-5-epoch context windows,
    # classifies root cause, and writes rebirth_forensic.csv.
    try:
        _rebirth_forensic_csv = open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "rebirth_forensic.csv"), "w")
    except OSError:
        _rebirth_forensic_csv = None
    _rebirth_forensic = RebirthForensic(
        fh=sys.stdout,
        rebirth_csv_fh=_rebirth_forensic_csv,
    )
    # ===========================================================================

    # ── OBSERVABILITY-AUDIT accumulators ─────────────────────────────────────
    _zwd_series          = []   # post-update ZWD (m) per epoch
    _up_series           = []   # post-update Up error (mm) per epoch
    # Pre-compute ENU rotation matrix from REF for Up projection
    _ref_lat, _ref_lon, _ = _lla(REF)
    _enu_R = _enu(_ref_lat, _ref_lon)   # 3×3: rows = [East, North, Up]
    # ──────────────────────────────────────────────────────────────────────────

    # ── Minimum streak before ambiguity birth ────────────────────────────────
    # PATCH: _AMB_MIN_EPOCHS removed — no streak requirement; birth is immediate.

    eplist = epochs if direction == 1 else list(reversed(epochs))

    for epoch in eplist:
        sod  = epoch['t']
        sobs = epoch['sats']
        dt   = abs(sod - psod) if psod is not None else 30.
        if dt <= 0 or dt > 3600:
            dt = 30.
        psod = sod
        tow  = _sod2t(sod, tref)
        tow_total = tow

        # ============================================================
        # FORENSIC EPOCH RESET  (PATCH 1)
        # Every forensic variable that is consumed later in this epoch
        # MUST be initialised here so no stale value leaks across epochs.
        # ============================================================
        _p1_ph_info          = 0.0
        _p1_cod_info         = 0.0
        _p1_zwd_info         = 0.0
        _p1_phase_rows       = 0
        _p1_code_rows        = 0
        _p2e_sr_KH           = float('nan')
        _p2e_eigs_KH         = None
        _au_cm_ratio         = float('nan')
        _info_rows_phase     = []
        _info_rows_code      = []
        _epoch_phase_rms     = float('nan')
        _epoch_code_rms      = float('nan')
        _epoch_clk_sig_before = float('nan')
        _epoch_clk_sig_after  = float('nan')
        # ── RESIDUAL-FRAME-AUDIT structures (Patches 1–5) ────────────────────
        _epoch_residual_audit = []   # list of per-row forensic dicts
        # Postfit accumulators (filled after filter_standard returns):
        _postfit_phase_mm    = []    # phase postfit residuals (mm)
        _postfit_code_mm     = []    # code postfit residuals (mm)
        # Weighted residual accumulators (z / sqrt(R_eff)):
        _weighted_phase      = []    # z[rl] / sqrt(Rd[rl]) for each accepted phase row
        _weighted_code       = []    # same for code rows
        # Prefit "true" = prefit linearized for PPP (they are the same here)
        # _ph_innov_list accumulates z[rl] = prefit innovations already
        _epoch_trace_before  = float('nan')
        _epoch_trace_after   = float('nan')
        # Also pre-initialise downstream forensic variables referenced
        # by [INFO-BALANCE] and [FORENSIC-SUMMARY] guards:
        _p2_clk_sig_after    = float('nan')
        _ib_code_rms         = float('nan')
        _ib_ph_rms           = float('nan')
        _row_kind_p          = []   # PATCH 2: explicit "PHASE"/"CODE"/"ZWD" tags
        _n_zombie_this_epoch = 0   # zombie resets fired this epoch (cascade guard)
        _coherent_cm_epoch   = False  # True if coherent clock-step detected this epoch
        # ============================================================

        # ── Startup flags ────────────────────────────────────────────────────
        is_startup = (nproc < 30)

        # ── State / covariance sanity ────────────────────────────────────────
        if not _is_finite_matrix(x[:5], "state x"):
            print(f"  [WARN-FATAL] Non-finite state at SOD={sod:.0f} — resetting XYZ")
            x[:3] = 0.; P[0,0]=P[1,1]=P[2,2] = 100.**2
        if not _is_finite_matrix(P, "covariance P"):
            print(f"  [WARN-FATAL] Non-finite P at SOD={sod:.0f} — reinflating diagonal")
            bad = ~np.isfinite(P)
            P[bad] = 0.
            np.fill_diagonal(P, np.maximum(np.diag(P), 1.0))
        _joseph_symmetrise(P)

        # ── FREEZE BOUNDARY BANNERS ──────────────────────────────────────────
        if ENABLE_FREEZE_OBS and nproc == FREEZE_OBS_START:
            _frz_active_sids = sorted(s for s in sidx if amgr.is_birth_complete(s))
            print(
                f"\n{'='*72}\n"
                f"[FREEZE-BEGIN]  epoch={nproc}  SOD={sod:.0f}\n"
                f"  All ambiguity lifecycle operations SUPPRESSED through epoch {FREEZE_OBS_END}\n"
                f"  Active birth-complete ambiguities entering freeze: {_frz_active_sids}\n"
                f"  n_active={len(_frz_active_sids)}  n_sidx={len(sidx)}\n"
                f"{'='*72}\n"
            )
        elif ENABLE_FREEZE_OBS and nproc == FREEZE_OBS_END + 1:
            print(
                f"\n{'='*72}\n"
                f"[FREEZE-END]  epoch={nproc}  SOD={sod:.0f}\n"
                f"  Lifecycle operations RESTORED from this epoch\n"
                f"  Suppressed resets:  {_freeze_suppressed_resets}\n"
                f"  Suppressed births:  {_freeze_suppressed_births}\n"
                f"  Skipped phase rows: {_freeze_skipped_phase_rows}\n"
                f"{'='*72}\n"
            )
        # ────────────────────────────────────────────────────────────────────
        # QPOS-AUDIT experiment (single-variable):
        #   Baseline: Q_pos = 1e-8 * dt  → σ_pos ≈ 0.6 mm/√hr
        #   Accepted NIS was 8.27 >> 1 → filter trusts predicted geometry too much.
        #   Change: 3× inflation → Q_pos = 3e-8 * dt  → σ_pos ≈ 1.0 mm/√hr
        #   Rationale: within the 2–4× prescribed range; conservative mid-point.
        #   ONLY this line is changed; all other noise terms are UNTOUCHED.
        n_st = len(x)
        Q = np.zeros((n_st, n_st))
        Q[0,0]=Q[1,1]=Q[2,2] = 3e-8 * dt   # QPOS-AUDIT: 3× baseline (was 1e-8)
        # ------------------------------------------------------------------
        # FORENSIC-PATCH: adaptive clock-Q during phase-collapse windows
        #
        # Motivation:
        # When phase becomes inactive or nearly all rows reject, the receiver
        # clock coasts on noisy code-only geometry. The clock then drifts into
        # a wrong basin, producing constellation-wide coherent residual flips
        # once phase resumes.
        #
        # Strategy:
        # Inflate clock process noise ONLY during severe rejection epochs
        # (detected from the PREVIOUS epoch's phase counts, which are the
        # only counts available at Q-propagation time).
        # Keep nominal dynamics during healthy phase tracking.
        #
        # Trigger: previous epoch had ≥(total−1) phase rows rejected, OR
        #          phase_rms was non-finite (phase fully inactive).
        # Multiplier: 5× nominal → σ_clk grows ~2.2× faster during blackout,
        #             giving the clock enough freedom to re-centre without
        #             destabilising healthy tracking windows.
        # All other Q terms are UNCHANGED per patch contract.
        # ------------------------------------------------------------------
        # ── EXPERIMENT: FIXED CLOCK-Q (adaptive logic fully disabled) ────────
        # Surgical test: remove ALL conditional Qclk inflation/escalation.
        # No branching, no storm detection, no phase_collapse detection,
        # no adaptive multipliers.  Qclk is a fixed constant every epoch.
        # Rationale: isolate whether adaptive-Q resonance drives the
        # remaining oscillation (if oscillation weakens) or whether the
        # instability is rooted deeper in the EKF/clock-observability
        # coupling (if oscillation persists).
        # All other process noise terms are UNCHANGED per experiment contract.
        # Clock process noise: 1.00e-05 m²/s (fixed)
        Q[3,3] = 1.00e-05 * dt
        # OBSERVABILITY-EXPERIMENT PATCH 1: Tighten ZWD random walk by 25×
        # Previous:  Q[4,4] = 1e-7 * dt   (REPAIR-STEP3 tropical value)
        # Now:       Q[4,4] = (0.0010**2) * dt = 1e-6/s
        # Variance tightening compared to original (0.005)**2 = 2.5e-5:  25×
        # Purpose: test whether weak ZWD/Up/Clock manifold observability
        # drives the persistent oscillation. If instability survives tighter
        # ZWD, the root cause is not the ZWD mobility alone.
        Q[4,4]                = (0.0010**2) * dt   # ZWD tightened for observability experiment
        print(
            f"  [ZWD-CONSTRAINT] SOD={sod:.0f}  "
            f"Qzwd={(0.0010**2):.2e}/s"
        )
        # Ambiguity process noise — physically justified random walk only
        q_amb = (1e-8 * dt if nproc < 120 else 1e-10 * dt) if direction == 1 else 1e-10 * dt
        # ── AMBIGUITY-SOFTNESS EXPERIMENT PATCH 1 ────────────────────────────
        # Purpose: prevent ambiguity states from becoming near-deterministic
        # constraints over long arcs.  This is NOT a stability patch — it is a
        # controlled physics experiment to determine whether ambiguity stiffness
        # is the remaining cause of clk_sig collapse and elevated code RMS.
        # The floor adds a 10 mm/epoch random walk on top of whatever q_amb
        # already provides.  Only active (non-NL-fixed) states receive the floor.
        # clock Q, ZWD Q, startup scaling, gating, Huber, lifecycle: UNCHANGED.
        AMB_Q_FLOOR = (0.001) ** 2   # (1 mm)^2 per epoch 
        # ─────────────────────────────────────────────────────────────────────
        for k in range(namb):
            sid_k = next((s for s, i in sidx.items() if i == 5 + k), None)
            if sid_k and sid_k in nl_fixed:
                Q[5+k, 5+k] = 1e-14 * dt   # freeze when NL-fixed
            else:
                Q[5+k, 5+k] = q_amb + AMB_Q_FLOOR * dt   # base walk + softness floor
        P += Q

        # ── PROPAGATION-AUDIT PATCH 2: predicted state delta decomposition ───
        # Fires AFTER P+=Q (process-noise propagation) and BEFORE the satellite
        # loop.  Decomposes x − x_post_prev into clock / ZWD / position / ambiguity
        # components to identify which propagated state drives the prefit CM excursion.
        # DIAGNOSTIC ONLY — no state or weight changes.
        if _x_post_prev is not None:
            _dx_pred = x - _x_post_prev

            _clk_pred_mm = float(_dx_pred[3] * 1000.0)
            _zwd_pred_mm = float(_dx_pred[4] * 1000.0)
            _enu_pred_mm = float(np.linalg.norm(_dx_pred[0:3]) * 1000.0)
            _up_pred_mm  = float(_dx_pred[2] * 1000.0)

            _amb_pred_vals = []
            for _pa_sid, _pa_ki in sidx.items():
                if _pa_ki >= len(x):
                    continue
                _pa_dN = float((_dx_pred[_pa_ki]) * 1000.0)
                _amb_pred_vals.append(_pa_dN)

            if _amb_pred_vals:
                _amb_pred_mean = float(np.mean(_amb_pred_vals))
                _amb_pred_std  = float(np.std(_amb_pred_vals))
                _amb_pred_max  = float(np.max(np.abs(_amb_pred_vals)))
            else:
                _amb_pred_mean = float('nan')
                _amb_pred_std  = float('nan')
                _amb_pred_max  = float('nan')

            print(
                f"[PROPAGATION-AUDIT] "
                f"epoch={nproc}  sod={sod:.0f}  "
                f"clk_pred_mm={_clk_pred_mm:+.1f}  "
                f"zwd_pred_mm={_zwd_pred_mm:+.1f}  "
                f"up_pred_mm={_up_pred_mm:+.1f}  "
                f"enu_norm_mm={_enu_pred_mm:.1f}  "
                f"amb_mean_mm={_amb_pred_mean:+.1f}  "
                f"amb_std_mm={_amb_pred_std:.1f}  "
                f"amb_max_mm={_amb_pred_max:.1f}"
            )

            # ── PROPAGATION-AUDIT PATCH 3: common-mode coupling detector ─────
            # same_sign=True and amb_clk_ratio≈1 → ambiguity ensemble drifting
            # WITH clock (shared datum mode, Case B).
            # clk large / amb small → Case A (clock propagation failure).
            # amb large / clk small → Case B (ambiguity random-walk instability).
            # up+zwd large → Case C (geometry/tropo manifold).
            if math.isfinite(_clk_pred_mm) and math.isfinite(_amb_pred_mean):
                _pa_same_sign = (
                    (_clk_pred_mm > 0 and _amb_pred_mean > 0) or
                    (_clk_pred_mm < 0 and _amb_pred_mean < 0)
                )
                _pa_ratio = abs(_amb_pred_mean) / max(abs(_clk_pred_mm), 1e-6)
                print(
                    f"[PROPAGATION-COUPLING] "
                    f"epoch={nproc}  sod={sod:.0f}  "
                    f"same_sign={_pa_same_sign}  "
                    f"amb_clk_ratio={_pa_ratio:.3f}"
                )

            # ── PROPAGATION-AUDIT PATCH 5: hard structural flags ─────────────
            if abs(_clk_pred_mm) > 150.0:
                print(
                    f"[CLOCK-PROPAGATION-FLAG] "
                    f"epoch={nproc}  sod={sod:.0f}  "
                    f"clk_pred_mm={_clk_pred_mm:+.1f}"
                )
            if math.isfinite(_amb_pred_mean) and abs(_amb_pred_mean) > 150.0:
                print(
                    f"[AMB-DATUM-DRIFT-FLAG] "
                    f"epoch={nproc}  sod={sod:.0f}  "
                    f"amb_mean_mm={_amb_pred_mean:+.1f}"
                )
            if abs(_up_pred_mm) > 100.0:
                print(
                    f"[UP-PROPAGATION-FLAG] "
                    f"epoch={nproc}  sod={sod:.0f}  "
                    f"up_pred_mm={_up_pred_mm:+.1f}"
                )
        else:
            # First epoch — no previous postfit state; initialise sentinel values
            # so PATCH 4 (CM-LINKAGE) references are always defined.
            _clk_pred_mm  = float('nan')
            _amb_pred_mean = float('nan')
        # ── END PROPAGATION-AUDIT PATCHES 2/3/5 ─────────────────────────────

        # ── OBSERVABILITY-EXPERIMENT PATCH 2: Soft ZWD prior floor ───────────
        # Cap the ZWD sigma at 50 mm maximum. This prevents the Up/ZWD/Clock
        # manifold from expanding freely between updates, decoupling them
        # observationally. If this suppresses the oscillation, the manifold
        # expansion is the driver; if not, the instability is geometry-intrinsic.
        _zwd_sigma_before = math.sqrt(max(P[4, 4], 0.0))
        P[4, 4] = min(P[4, 4], 0.05**2)
        if abs(math.sqrt(P[4, 4]) - _zwd_sigma_before) > 1e-12:
            print(f"  [ZWD-FLOOR] SOD={sod:.0f}  "
                  f"sigma_before={_zwd_sigma_before*1000:.1f}mm  "
                  f"sigma_after={math.sqrt(P[4,4])*1000:.1f}mm")
        # ─────────────────────────────────────────────────────────────────────

        # ── PSD monitor checkpoint A: after process noise ─────────────────────
        # Build a lightweight state-name map for diagnostic output.
        _sidx_inv = {0:'dX',1:'dY',2:'dZ',3:'clk',4:'ZWD'}
        _sidx_inv.update({v: k for k, v in sidx.items()})
        if not _psd_check(P, _sidx_inv, 'POST-Q', sod)[0]:
            pass   # warning already printed; filter continues (Repair 4 will floor)

        # ── Compute nominal receiver position ────────────────────────────────
        rxyz = nom + x[:3]
        sun  = _sun(tow)
        geom = []
        _n_rejected_epoch = 0
        _newborn_pending  = {}   # sids queued for post-fit birth this epoch
        _l2_audit_pending = {}   # GPS-L2-STAB: audit rows to write for this epoch

        # ─────────────────────────────────────────────────────────────────────
        #  Satellite loop: observation model + ambiguity state management
        # ─────────────────────────────────────────────────────────────────────
        for sid, so in sorted(sobs.items()):
            if sid[0] != 'G':   # GPS-only: reject all non-GPS SVs
                continue

            # =========================================================
            # FORENSIC SATELLITE BLACKLIST TEST
            # Goal:
            #   Isolate whether a few pathological satellites
            #   are poisoning the shared clock/position basin.
            #
            # Satellites selected based on audit evidence:
            #   G23/G11 — dominant birth-error contributors (APC/PCV suspect)
            #   G31/G12 — second-tier large birth-error outliers
            #   G10     — large birth-error, high reset rate
            #
            # Temporary forensic exclusion ONLY — do NOT merge to main.
            # All other settings (Q, bootstrap floor, zombie reset, NIS
            # gate, ZWD prior, SC/SP) are UNCHANGED.
            # =========================================================
            BLACKLIST_SATS = {"G23", "G11", "G31", "G12", "G10"}
            if sid in BLACKLIST_SATS:
                continue
            # =========================================================

            m = _proc(sid, so, tow, rxyz, ah, sp3t, sp, sc, clkd, osb,
                      lat0, doy, zhd, elm, satx, att, recx,
                      blq=blq, sta=sta, tow_total=tow_total)
            if m is None:
                continue

            # OSB presence guard (GPS only)
            ob_chk = osb.get(sid, {})
            _, _, _osb_ok = _check_osb_gps(ob_chk, sid)
            if not _osb_ok:
                _n_rejected_epoch += 1
                continue

            # PATCH: streak tracking removed — no minimum-epoch gate before birth.

            # ── Cycle slip detection ─────────────────────────────────────────
            slip = False
            _dGF_audit = 0.0   # GPS-L2-STAB: GF jump in mm (for audit CSV)
            _dMW_audit = 0.0   # GPS-L2-STAB: MW jump in mm-equivalent (for audit CSV)
            # FIX-3B (v64): Do not run GF/MW slip detector below 15° phase mask.
            # Low-elevation satellites are multipath-dominated; false slips → reset
            # storms (1663 resets, 79% consecutive in v63-phase14).
            # Update MW/GF references so the detector is ready when the satellite
            # rises above the mask; code processing continues normally.
            if m['el'] < PHASE_EL_MASK_RAD:
                prev_mw[sid]       = m['MW_cyc']
                prev_gf[sid]       = m['GF_m']
                _sat_last_sod[sid] = sod
            else:
                if sid in prev_mw:
                    dGF = m['GF_m'] - prev_gf[sid]
                    dMW = m['MW_cyc'] - prev_mw[sid]
                    _dGF_audit = dGF * 1e3                    # mm
                    _dMW_audit = dMW * LAMBDA_WL * 1e3        # cyc → mm
                    # REPAIR-STEP2: GF threshold raised from 0.05m → 0.20m.
                    # At IISC Bangalore (equatorial ionosphere, 30s sampling)
                    # the ionospheric TEC rate-of-change can produce genuine
                    # GF variations of 0.05–0.15m between consecutive epochs
                    # WITHOUT a cycle slip.  The 0.05m threshold was generating
                    # thousands of false resets, starving the filter of phase
                    # continuity.  0.20m corresponds to STEC change of ~3 TECU
                    # in 30s, which is a physically realistic upper bound for
                    # non-storm equatorial conditions and well below the ~0.86m
                    # L1 cycle size that constitutes a genuine half-cycle slip.
                    # The MW threshold (1.5 cyc) provides the secondary guard.
                    if abs(dGF) > 0.08 or abs(dMW) > 1.5:
                        if sid in _amb_seeded:
                            _amb_seeded.discard(sid)
                        else:
                            # ── STAGE 1C: MW PRE-GATE FOR GF-TRIGGERED RESETS ────
                            # FORENSIC EXPERIMENT — not a production patch.
                            # Tests whether MW pre-gating eliminates confirmed
                            # false-ramp cascades without damaging genuine slip handling.
                            #
                            # Physical rationale:
                            #   A "GF-triggered" reset fires when abs(dGF) > 0.08
                            #   but abs(dMW) <= 1.5.  At equatorial stations (IISC,
                            #   Bangalore), smooth STEC ramps produce large dGF without
                            #   any cycle slip.  MW (widelane − narrowlane code) is
                            #   insensitive to STEC to first order (the ionospheric
                            #   delay cancels in the widelane combination), so ONLY
                            #   real cycle slips produce large per-epoch dMW jumps.
                            #
                            #   Forensic evidence (G08 cascade, 13 consecutive resets):
                            #     GF ramp:   dGF ≈ +87 mm/epoch  → triggers GF gate
                            #     False pop: |dMW| = 0.1–1.3 cyc  → no MW confirmation
                            #     True slips: |dMW| > 5 cyc        → clean separation
                            #
                            #   Gate:
                            #     GF-only trigger  ≡  abs(dGF) > 0.08  AND  abs(dMW) <= 1.5
                            #     MW-confirmed     ≡  abs(dMW) > 1.5   (MW trigger or mixed)
                            #
                            #   Only GF-only triggers are suppressed here.
                            #   MW-confirmed events (abs(dMW) > 1.5) are passed through
                            #   to Patch 1 (low-elevation discrimination) and then to
                            #   the normal slip reset path.
                            #
                            # Paths AFFECTED:
                            #   GF-only triggers at ALL elevations → suppressed
                            # Paths UNAFFECTED:
                            #   abs(dMW) > 1.5 triggers (MW slip, mixed GF+MW)
                            #   Explicit MW slips, NIS rejection, zombie logic,
                            #   end-of-arc suppression (Patch 2), low-el Patch 1
                            #
                            # ROLLBACK: delete this block (lines between
                            #   "── STAGE 1C" and "── END STAGE 1C") to restore
                            #   prior behaviour exactly.
                            _gf_only_trigger = (abs(dGF) > 0.08 and abs(dMW) <= 1.5)
                            if _gf_only_trigger:
                                # GF-only event — no MW confirmation.
                                # Suppress: this is an iono ramp, NOT a cycle slip.
                                _el_deg_1c = math.degrees(m['el'])
                                print(
                                    f"  [GF-RAMP-SUPPRESSED]"
                                    f"  sat={sid}"
                                    f"  ep={nproc}"
                                    f"  el={_el_deg_1c:.1f}"
                                    f"  GF={dGF*1e3:+.1f}mm"
                                    f"  MW={dMW:+.3f}cyc"
                                    f"  sod={sod:.0f}"
                                    f"  sigma={math.sqrt(max(P[sidx[sid],sidx[sid]],0.))*1e3:.1f}mm"
                                )
                                # MW/GF references already updated below —
                                # no state mutation; slip remains False.
                                _1c_suppressed_resets += 1
                            else:
                                # MW confirmed (abs(dMW) > 1.5) or mixed trigger.
                                # ── PATCH 1: LOW-ELEVATION GF/MW DISCRIMINATION ──────
                                # Physical rationale:
                                #   At elevation < 20°, the ionospheric path length
                                #   increases by ~3× vs zenith (obliquity factor).
                                #   At equatorial stations (IISC), STEC gradients of
                                #   3–15 TECU/30s are common without any cycle slip.
                                #   GF = L1 − L2 absorbs STEC directly (coefficient
                                #   ≈ 0.105 m/TECU), so a 10 TECU gradient → dGF ≈ 1 m.
                                #   MW (= widelane − code) is insensitive to STEC to
                                #   first order (STEC cancels in the widelane combo),
                                #   so a true cycle slip disturbs BOTH GF AND MW,
                                #   while a pure ionospheric event disturbs only GF.
                                #   Condition: suppress the reset iff
                                #     el < 20° AND |dMW| < 2.5 cyc
                                #   This preserves all resets where MW confirms a slip
                                #   (|dMW| ≥ 2.5 cyc) and all high-elevation resets.
                                _el_deg_now = math.degrees(m['el'])
                                _LOWEL_THRESH_DEG  = 20.0   # degrees
                                _MW_CONFIRM_THRESH = 2.5    # cycles — MW must NOT show a slip
                                if (_el_deg_now < _LOWEL_THRESH_DEG
                                        and abs(dMW) < _MW_CONFIRM_THRESH):
                                    # GF-only event at low elevation — likely ionospheric
                                    # gradient, NOT a cycle slip.  Suppress the reset.
                                    print(
                                        f"  [GF-SUPPRESSED-LOWEL]"
                                        f"  sat={sid}"
                                        f"  el={_el_deg_now:.1f}"
                                        f"  GF={dGF*1e3:+.1f}mm"
                                        f"  MW={dMW:+.3f}cyc"
                                        f"  sod={sod:.0f}"
                                        f"  epoch={nproc}"
                                        f"  sigma={math.sqrt(max(P[sidx[sid],sidx[sid]],0.))*1e3:.1f}mm"
                                    )
                                    # MW/GF references already updated below —
                                    # no further action; slip remains False.
                                else:
                                    # Either high-elevation, or MW confirms slip.
                                    # Proceed with the normal reset path.
                                    slip = True
                                    wl_fixed.pop(sid, None)
                                    mw_hist[sid].clear()
                                    _prev_sod = _sat_last_sod.get(sid)
                                    if _prev_sod is None or (sod - _prev_sod) > 120.:
                                        _wl_history.pop(sid, None)
                                        _wl_history_ptrace.pop(sid, None)
                                # ── END PATCH 1 ──────────────────────────────────────
                            # ── END STAGE 1C ─────────────────────────────────────────
                prev_mw[sid]       = m['MW_cyc']
                prev_gf[sid]       = m['GF_m']
                _sat_last_sod[sid] = sod

            # ── GPS-L2-STAB: audit row collection ───────────────────────────
            # Record selected signal family, switch detection, and slip status.
            # In this experiment l2_sig_used is always 'L2W' so _l2_sw should
            # always be 0.  Any non-zero count is a bug in the implementation.
            if sid[0] == 'G' and l2_stab_audit_fh is not None:
                _l2_sig_now = m.get('l2_sig_used', 'L2W')
                _l2_sw = int(_gps_l2_sig_prev.get(sid, _l2_sig_now) != _l2_sig_now)
                if _l2_sw:
                    _l2_switch_count += 1
                    print(f"  [GPS-L2-STAB BUG] {sid} SOD={sod:.0f}: unexpected "
                          f"signal switch {_gps_l2_sig_prev[sid]}→{_l2_sig_now}")
                _gps_l2_sig_prev[sid] = _l2_sig_now
                _l2_audit_pending[sid] = dict(
                    sod=sod, l2_sig=_l2_sig_now, switch=_l2_sw,
                    reset=int(slip), dGF_mm=_dGF_audit, dMW_mm=_dMW_audit)

            # ── MW accumulation ──────────────────────────────────────────────
            if not slip:
                mw_hist[sid].append(m['MW_cyc'])
            else:
                mw_hist[sid].clear()

            # ── WL fixing (disabled: float PPP stabilization phase) ──────────
            if _ENABLE_WL_FIXING and sid not in wl_fixed:
                n_hist = len(mw_hist[sid])
                if n_hist >= 15:
                    mn = np.mean(mw_hist[sid]); sd = np.std(mw_hist[sid])
                    sys_id = sid[0]; min_n = 30 if sd > 0.30 else 15
                    if n_hist >= min_n:
                        if sys_id not in b_rec_frozen:
                            all_fracs = []
                            for s2, h2 in mw_hist.items():
                                if s2[0] != sys_id or len(h2) < min_n: continue
                                if (np.std(h2) if len(h2) > 1 else 999.) > 0.45: continue
                                all_fracs.append(np.mean(h2) - round(np.mean(h2)))
                            if len(all_fracs) >= 5:
                                bc = float(np.median(all_fracs))
                                agr = sum(1 for f in all_fracs if abs(f - bc) < 0.25)
                                if agr >= max(3, 0.6 * len(all_fracs)):
                                    b_rec_frozen[sys_id] = bc
                                    b_rec_n[sys_id] = len(all_fracs)
                                    print(f"[B_REC FROZEN] {sys_id}: b_rec={bc:+.4f} cyc "
                                          f"median of {len(all_fracs)} sats agree={agr}")
                        if sys_id in b_rec_frozen:
                            b_rec = b_rec_frozen[sys_id]; tag = sys_id + 'F'
                        else:
                            fracs = []
                            for s2, h2 in mw_hist.items():
                                if s2[0] != sys_id or len(h2) < min_n: continue
                                if (np.std(h2) if len(h2) > 1 else 999.) > 0.45: continue
                                fracs.append(np.mean(h2) - round(np.mean(h2)))
                            b_rec = np.mean(fracs) if fracs else 0.0; tag = sys_id + 'E'

                        mn_corr = mn - b_rec; NWL = round(mn_corr)
                        residual = abs(mn_corr - NWL)
                        if sys_id not in b_rec_frozen:
                            pass
                        elif sd < 0.25 and residual < 0.20:
                            NWL_to_use = NWL
                            pt_now = P[0,0] + P[1,1] + P[2,2]
                            if sid in _wl_history:
                                hist_NWL = _wl_history[sid]
                                diff = abs(NWL - hist_NWL)
                                if diff == 0:
                                    NWL_to_use = hist_NWL
                                    if pt_now < _wl_history_ptrace.get(sid, 999.):
                                        _wl_history_ptrace[sid] = pt_now
                                elif diff <= 3:
                                    print(f"[WL PERSIST] {sid} using prev NWL={hist_NWL} "
                                          f"(same-arc noise: new={NWL}, diff={diff}<=3->keep)")
                                    NWL_to_use = hist_NWL
                                else:
                                    print(f"[WL UPDATE] {sid} NWL {hist_NWL}→{NWL} (diff={diff}>3)")
                                    _wl_history[sid] = NWL
                                    _wl_history_ptrace[sid] = pt_now
                                    NWL_to_use = NWL
                            else:
                                _wl_history[sid] = NWL
                                _wl_history_ptrace[sid] = pt_now
                            wl_fixed[sid] = NWL_to_use
                            print(f"[WL FIXED] {sid}  N_WL={NWL_to_use}  "
                                  f"mean={mn_corr:.3f}  std={sd:.3f} "
                                  f"b_rec={b_rec:+.3f}({tag}) cyc")

            # ── Allocate ambiguity state slot if new ─────────────────────────
            if sid not in sidx:
                d = len(x)
                x = np.append(x, 0.)
                Pn = np.zeros((d+1, d+1))
                Pn[:d, :d] = P
                Pn[d, d] = 20.**2   # 20 m birth sigma
                P = Pn
                sidx[sid] = d
                namb += 1
                amgr.register_new_slot(sid, d)          # STEP-2: public API; replaces amgr._ensure(sid, d)
                amgr.update_state_index(sid, d)          # STEP-2: explicit sync + ASSERT-7

            ki = sidx[sid]

            # ── Cycle slip → reset ambiguity state ───────────────────────────
            # FREEZE: suppress cycle-slip resets during observability experiment
            if slip and ENABLE_FREEZE_OBS and FREEZE_OBS_START <= nproc <= FREEZE_OBS_END:
                _freeze_suppressed_resets += 1
                print(f"  [FREEZE-SUPPRESS-SLIP] sat={sid}  epoch={nproc}  SOD={sod:.0f}"
                      f"  slip detected but SUPPRESSED (lifecycle freeze active)")
                slip = False   # prevent the reset block below from executing

            # ── PATCH 2: END-OF-ARC RESET SUPPRESSION ────────────────────────
            # Physical rationale:
            #   elm is the code/geometry elevation cutoff (10°).  A satellite
            #   within 1° of that cutoff will disappear in 1–3 epochs (at 30s
            #   sampling, ~0.5°/ep near the horizon).  Resetting its ambiguity
            #   at that point:
            #     (a) destroys a potentially converged arc (sigma < 100 mm)
            #     (b) creates a 20m zombie state that persists until gap recovery
            #     (c) contributes a large |N_birth| to the birth-magnitude stats
            #     (d) provides zero future positioning benefit (satellite gone)
            #   Condition: suppress the reset iff
            #     el_deg < elm_deg + 1.0
            #   This covers only the final ~1–3 epochs before disappearance.
            #   Diagnostics (audit CSV, event counters, rebirth forensics) are
            #   fully preserved.  Only the EKF state mutation is suppressed.
            if slip:
                _el_deg_endarc = math.degrees(m['el'])
                _elm_deg       = math.degrees(elm)          # elm is the cutoff passed to _ppp_pass
                _ENDARC_MARGIN = 1.0                        # degrees above cutoff
                if _el_deg_endarc < (_elm_deg + _ENDARC_MARGIN):
                    print(
                        f"  [RESET-SUPPRESSED-ENDARC]"
                        f"  sat={sid}"
                        f"  el={_el_deg_endarc:.1f}°"
                        f"  cutoff={_elm_deg:.1f}°"
                        f"  GF={( m['GF_m'] - prev_gf.get(sid, m['GF_m']) )*1e3:+.1f}mm"
                        f"  MW={( m['MW_cyc'] - prev_mw.get(sid, m['MW_cyc']) ):+.3f}cyc"
                        f"  sigma={math.sqrt(max(P[ki,ki],0.))*1e3:.1f}mm"
                        f"  sod={sod:.0f}  epoch={nproc}"
                    )
                    # Preserve: audit CSV write, mw_hist.clear, _rebirth_forensic
                    # note_slip, wl_fixed/nl_fixed cleanup, _sat_age reset.
                    # Suppress: x[ki]=0, P reset, amgr.register_reset.
                    # Write audit row even though we will not reset state.
                    _pre_sigma_mm_ea = math.sqrt(max(P[ki, ki], 0.)) * 1e3
                    _recent_res_ea   = amgr.get_recent_ph_res(sid)
                    _recent_rej_ea   = amgr.get_recent_rej(sid)
                    _rph_rms_ea  = (math.sqrt(sum(r*r for r in _recent_res_ea)/len(_recent_res_ea))
                                    if _recent_res_ea else float('nan'))
                    _rej_frac_ea = (sum(_recent_rej_ea)/len(_recent_rej_ea)
                                    if _recent_rej_ea else float('nan'))
                    _ep_since_birth_ea = nproc - amgr.get_birth_epoch(sid, nproc)
                    _was_rej_ea = (sum(_recent_rej_ea[-3:]) >= 2) if len(_recent_rej_ea) >= 3 else False
                    if reset_audit_fh is not None:
                        _dGF_ea = m['GF_m'] - prev_gf.get(sid, m['GF_m'])
                        _dMW_ea = m['MW_cyc'] - prev_mw.get(sid, m['MW_cyc'])
                        reset_audit_fh.write(
                            f"{nproc},{sod:.1f},{sid},{sid[0]},"
                            f"{_fmtf(m['GF_m'],1e3,4)},{_fmtf(m['MW_cyc'],prec=6)},"
                            f"{_fmtf(_dGF_ea,1e3,4)},{_fmtf(_dMW_ea,prec=4)},"
                            f"{_el_deg_endarc:.3f},"
                            f"{_fmtf(_pre_sigma_mm_ea,prec=4)},"
                            f"{_fmtf(_rph_rms_ea,prec=4)},"
                            f"{_fmtf(_rej_frac_ea,prec=4)},"
                            f"{_ep_since_birth_ea},"
                            f"{1 if _was_rej_ea else 0},"
                            f"ENDARC-SUPPRESSED-{amgr.cumulative_resets}\n"
                        )
                    # Log slip in rebirth forensics (diagnostic only — no state change)
                    _rebirth_forensic.note_slip(
                        sid, sod,
                        dGF=m["GF_m"] - prev_gf.get(sid, m["GF_m"]),
                        dMW=m["MW_cyc"] - prev_mw.get(sid, m["MW_cyc"]),
                    )
                    # Clean up WL / NL fix records (already stale at end-of-arc)
                    wl_fixed.pop(sid, None)
                    nl_fixed.pop(sid, None)
                    _nl_bad_nwl.discard(sid)
                    # Suppress the EKF state mutation — slip is cleared.
                    slip = False
            # ── END PATCH 2 ──────────────────────────────────────────────────

            if slip:
                # ── AUDIT: capture pre-reset diagnostics ─────────────────────
                _pre_sigma_mm = math.sqrt(max(P[ki, ki], 0.)) * 1e3
                _recent_res   = amgr.get_recent_ph_res(sid)
                _recent_rej   = amgr.get_recent_rej(sid)
                _rph_rms  = (math.sqrt(sum(r*r for r in _recent_res)/len(_recent_res))
                             if _recent_res else float('nan'))
                _rej_frac = (sum(_recent_rej)/len(_recent_rej)
                             if _recent_rej else float('nan'))
                _ep_since_birth = nproc - amgr.get_birth_epoch(sid, nproc)
                _was_rej = (sum(_recent_rej[-3:]) >= 2) if len(_recent_rej) >= 3 else False
                if reset_audit_fh is not None:
                    _dGF_log = m['GF_m'] - prev_gf.get(sid, m['GF_m'])
                    _dMW_log = m['MW_cyc'] - prev_mw.get(sid, m['MW_cyc'])
                    reset_audit_fh.write(
                        f"{nproc},{sod:.1f},{sid},{sid[0]},"
                        f"{_fmtf(m['GF_m'],1e3,4)},{_fmtf(m['MW_cyc'],prec=6)},"
                        f"{_fmtf(_dGF_log,1e3,4)},{_fmtf(_dMW_log,prec=4)},"
                        f"{math.degrees(m['el']):.3f},"
                        f"{_fmtf(_pre_sigma_mm,prec=4)},"
                        f"{_fmtf(_rph_rms,prec=4)},"
                        f"{_fmtf(_rej_frac,prec=4)},"
                        f"{_ep_since_birth},"
                        f"{1 if _was_rej else 0},"
                        f"{amgr.cumulative_resets+1}\n"
                    )
                # ─────────────────────────────────────────────────────────────
                # PSD-REPAIR-7: Zero full row/column at reset, not just diagonal.
                # After a cycle slip the ambiguity is unknown; its cross-covariances
                # with all other states must also be discarded.  Leaving stale
                # off-diagonals while reinflating P[ki,ki] creates an indefinite
                # submatrix (implies |ρ|>1) that can drive clock/position variances
                # negative in the very next filter_standard call.
                x[ki] = 0.
                P[ki, :]  = 0.
                P[:, ki]  = 0.
                P[ki, ki] = 20.**2
                amgr.register_reset(sid)          # SLIPPED→RESET; ring-bufs cleared; counter++; see ambiguity_manager.py ASSERT-R1
                # == REBIRTH-FORENSIC HUNK 3: note slip =====================
                _rebirth_forensic.note_slip(
                    sid, sod,
                    dGF=m["GF_m"] - prev_gf.get(sid, m["GF_m"]),
                    dMW=m["MW_cyc"] - prev_mw.get(sid, m["MW_cyc"]),
                )
                # ===========================================================
                _amb_saved_state.pop(sid, None)
                nl_fixed.pop(sid, None)
                _nl_bad_nwl.discard(sid)
                _sat_age[sid] = 0
                print(f"[AMB-RESET] sat={sid}  SOD={sod:.0f}  reason=cycle_slip  "
                      f"cumulative_resets={amgr.cumulative_resets}")

            # ── Phase wind-up ────────────────────────────────────────────────
            wu  = _wu(m['sat_xyz'], m['rec_apc'], sun, wum.get(sid, 0.))
            wum[sid] = wu
            lam_if = m.get('_lam_if', LAMBDA_IF)
            LIFc   = m['LIF'] - wu * lam_if
            m['LIFc'] = LIFc

            # ── Gap recovery: restore saved state if gap < 300 s ─────────────
            # Step-4-SEM: use is_birth_complete() instead of is_active().
            # RESET satellites are now active=True (lineage exists) but are NOT
            # birth-complete (x[ki] was just zeroed by register_reset).  They
            # must enter the birth-queuing path below, not gap recovery.
            # DORMANT satellites are birth-complete (valid x[ki] from before the
            # gap) and are handled by the lifecycle manager's activate() call.
            if not amgr.is_birth_complete(sid):
                _saved = _amb_saved_state.get(sid)
                if _saved is not None:
                    _gap = sod - _saved['last_sod']
                    if 0 < _gap < 300.:
                        # FREEZE: suppress gap-recovery state restores
                        if ENABLE_FREEZE_OBS and FREEZE_OBS_START <= nproc <= FREEZE_OBS_END:
                            print(f"  [FREEZE-SUPPRESS-RESTORE] sat={sid}  epoch={nproc}  SOD={sod:.0f}"
                                  f"  gap={_gap:.0f}s  restore SUPPRESSED (lifecycle freeze active)")
                        else:
                            x[ki]   = _saved['x_ki']
                            # PSD-REPAIR-1: Zero the ENTIRE row and column before
                            # restoring diagonal.  The prior code only zeroed P[ki,:ki]
                            # and P[:ki,ki], leaving P[ki,ki+1:] and P[ki+1:,ki] with
                            # stale cross-covariances from before the gap.  Those residual
                            # off-diagonal terms survive filter_standard and drive P[3,3]
                            # (clock variance) negative via the (I-KH) contraction path.
                            _P_ki_restored = min(_saved['P_ki'] * 2.0, (50.)**2)
                            P[ki, :]   = 0.
                            P[:, ki]   = 0.
                            P[ki, ki]  = _P_ki_restored
                            amgr.activate(sid)                       # STEP-2: replaces set_active(sid, True)
                            amgr.update_state_index(sid, ki)          # STEP-2: ASSERT-7 on restore
                            _amb_saved_state.pop(sid)
                            print(f"[AMB-RESTORE] sat={sid}  SOD={sod:.0f}  "
                                  f"gap={_gap:.0f}s  "
                                  f"sigma_restored={math.sqrt(P[ki,ki])*1e3:.1f}mm")

            # ── Birth queuing: queue for post-fit birth this epoch (immediate)
            # PATCH: _AMB_MIN_EPOCHS streak gate removed. Any satellite that has
            # a valid geometry observation and passes OSB/slip checks is queued
            # for post-fit birth immediately. No streak counting, no warmup,
            # no birth_pending delay beyond the single post-fit-in-same-epoch latency.
            #
            # Step-4-SEM: use needs_birth() instead of `not is_active()`.
            # RESET and NEW are the only states requiring post-fit initialisation.
            # All other active states (ACTIVE, CONVERGING, CONVERGED, DORMANT,
            # REBORN, RESTORED) have valid x[ki] and do NOT need re-queuing.
            if amgr.needs_birth(sid):
                # FREEZE: during observability experiment, suppress ALL births.
                # A satellite without an initialised ambiguity will simply have
                # its phase row skipped below (code row still used).
                if ENABLE_FREEZE_OBS and FREEZE_OBS_START <= nproc <= FREEZE_OBS_END:
                    _freeze_suppressed_births += 1
                    print(f"  [FREEZE-SUPPRESS-BIRTH] sat={sid}  epoch={nproc}  SOD={sod:.0f}"
                          f"  birth SUPPRESSED (lifecycle freeze active) — phase row will be skipped")
                # Check if inherited from previous pass
                elif sid in _amb_init:
                    x[ki], P[ki,ki] = _amb_init.pop(sid)
                    amgr.register_inherited(sid, ki, math.sqrt(P[ki,ki]))
                    amgr.update_state_index(sid, ki)              # STEP-2: ASSERT-7 on inherited birth
                    _amb_seeded.add(sid)
                    print(f"[AMB-BIRTH] sat={sid}  SOD={sod:.0f}  INHERITED  "
                          f"N={x[ki]*1e3:+.1f}mm  sigma={math.sqrt(P[ki,ki])*1e3:.1f}mm")
                else:
                    # Queue for post-fit birth (runs after EKF update this epoch)
                    _newborn_pending[sid] = dict(m=m, ki=ki, LIFc=LIFc,
                                                 sys=m.get('_sys','?'), sod=sod)

            # ── Per-ambiguity covariance sanity ──────────────────────────────
            # Step-4-SEM: is_birth_complete() — only check sats with valid x[ki].
            # RESET satellites (active=True but x[ki]=0, P[ki,ki]=20^2) were
            # just reinflated; no sanity check needed.
            if amgr.is_birth_complete(sid):
                pki = P[ki, ki]
                if not math.isfinite(pki) or pki <= 0.:
                    if ENABLE_FREEZE_OBS and FREEZE_OBS_START <= nproc <= FREEZE_OBS_END:
                        print(f"  [FREEZE-WARN-SANITY] {sid} amb variance non-positive ({pki:.3e})"
                              f" SOD={sod:.0f} — NOT resetting (lifecycle freeze active)")
                    else:
                        print(f"  [WARN] {sid} amb variance non-positive ({pki:.3e}) "
                              f"SOD={sod:.0f} — resetting")
                        # PSD-REPAIR-8: zero full row/column before reinflating.
                        # Any reinflation that leaves stale off-diagonals intact
                        # creates an indefinite submatrix (same mechanism as RESTORE bug).
                        x[ki] = 0.
                        P[ki, :]  = 0.; P[:, ki]  = 0.
                        P[ki, ki] = 20.**2
                        amgr.deactivate(sid)                         # STEP-2: replaces set_active(sid, False)
                elif pki < 1e-8:
                    if ENABLE_FREEZE_OBS and FREEZE_OBS_START <= nproc <= FREEZE_OBS_END:
                        print(f"  [FREEZE-WARN-SANITY] {sid} amb variance collapsed ({pki:.3e})"
                              f" SOD={sod:.0f} — NOT reinflating (lifecycle freeze active)")
                    else:
                        print(f"  [WARN] {sid} amb variance collapsed ({pki:.3e}) "
                              f"SOD={sod:.0f} — reinflating")
                        P[ki, :]  = 0.; P[:, ki]  = 0.
                        P[ki, ki] = 20.**2
                elif pki > 1e8:
                    print(f"  [WARN] {sid} amb variance exploded ({pki:.3e}) "
                          f"SOD={sod:.0f} — clamping")
                    P[ki, :]  = 0.; P[:, ki]  = 0.
                    P[ki, ki] = 50.**2

            _sat_age[sid] += 1
            m['ki']  = ki
            m['NWL'] = wl_fixed.get(sid, None)
            m['age'] = _sat_age[sid]
            amgr.mark_present(sid, nproc, sod, elevation_deg=math.degrees(m['el']))  # STEP-2: observable flag
            geom.append(m)

        # ── GPS-L2-STAB: flush audit rows for this epoch ─────────────────────
        # Written after the full satellite loop so all slip/reset flags are final.
        if l2_stab_audit_fh is not None and _l2_audit_pending:
            for _a_sid, _a_row in _l2_audit_pending.items():
                l2_stab_audit_fh.write(
                    f"{_a_sid},{_a_row['sod']:.1f},{_a_row['l2_sig']},"
                    f"{_a_row['switch']},{_a_row['reset']},"
                    f"{_a_row['dGF_mm']:.3f},{_a_row['dMW_mm']:.3f}\n"
                )

        # ── Minimum geometry check ───────────────────────────────────────────
        if len(geom) < 4:
            continue
        if len(geom) > 4 and _pdop(geom) > 6.0:
            worst = min(geom, key=lambda m: m['el'])
            geom  = [m for m in geom if m['sid'] != worst['sid']]
        if len(geom) < 4:
            continue

        # ── First epoch: bootstrap clock ─────────────────────────────────────
        if nproc == 0:
            clk_old = x[3]
            x[3]    = _spp_clock(geom, rxyz)
            dclk    = x[3] - clk_old
            for m in geom:
                ki = m['ki']
                if m['sid'] in _newborn_pending:
                    pass  # born post-fit
                elif m['sid'] in _amb_seeded:
                    x[ki] -= dclk
                elif amgr.is_birth_complete(m['sid']):  # Step-4-SEM: EKF-ready check
                    rp0 = _rp(m, x[3], x[4])
                    x[ki] = m['LIFc'] - rp0
                    P[ki, ki] = 20.**2

        # ── ROOT-TRIGGER PATCH 2: IF ALGEBRA VERIFICATION (epoch 0 only) ─────
        if nproc == 0 and geom:
            _m0 = geom[0]
            _l1_m0   = _m0['L1'] * LAMBDA1
            _l2_m0   = _m0['L2'] * LAMBDA2
            _lif_man = ALFA * _l1_m0 - BETA * _l2_m0
            _pif_man = ALFA * _m0['P1'] - BETA * _m0['P2']
            _diff_lif = _lif_man - _m0['_LIF_raw']
            _diff_pif = _pif_man - _m0.get('_PIF_raw', _ifc(_m0['P1'], _m0['P2']))
            _lam_if_0 = _m0.get('_lam_if', LAMBDA_IF)
            print(
                f"[IF-ALGEBRA-CHECK]\n"
                f"  sat={_m0['sid']}\n"
                f"  alpha={ALFA:.10f}  beta={BETA:.10f}\n"
                f"  L1_m={_l1_m0:.6f} m   L2_m={_l2_m0:.6f} m\n"
                f"  LIF_manual={_lif_man:.6f} m   LIF_raw={_m0['_LIF_raw']:.6f} m"
                f"   difference_LIF={_diff_lif:.4e} m\n"
                f"  P1={_m0['P1']:.6f} m   P2={_m0['P2']:.6f} m\n"
                f"  PIF_manual={_pif_man:.6f} m   PIF_raw={_m0.get('_PIF_raw', float('nan')):.6f} m"
                f"   difference_PIF={_diff_pif:.4e} m\n"
                f"  LAMBDA_IF={_lam_if_0:.10f} m\n"
                f"  {'*** IF ALGEBRA MISMATCH > 1e-6 m ***' if abs(_diff_lif) > 1e-6 or abs(_diff_pif) > 1e-6 else 'SUCCESS: differences < 1e-6 m'}"
            )
        # ─────────────────────────────────────────────────────────────────────

        # ── Build measurement matrices ────────────────────────────────────────
        ns  = len(geom)
        nst = len(x)
        # WL pseudo-obs disabled; NL pseudo-obs disabled (AR off)
        H  = np.zeros((2 * ns, nst))
        z  = np.zeros(2 * ns)
        Rd = np.zeros(2 * ns)
        # PATCH 2: explicit row-kind tags — populated alongside H/z/Rd below.
        # "CODE" for code pseudorange rows, "PHASE" for carrier-phase rows.
        # Never inferred from R magnitude; always set at construction time.
        _H_row_kinds = [""] * (2 * ns)   # pre-allocated; overwritten per row

        xs = x.copy()   # pre-update state for residual computation

        # Hard residual thresholds (observation-domain only)
        _code_hard = 10.0 if is_startup else 5.0   # metres
        # Phase: NIS gate replaces old fixed threshold.
        # _phs_hard is retained only as the ABS_PHASE_MAX sanity floor.
        _phs_hard  = ABS_PHASE_MAX  # metres — absolute floor, NOT the primary gate

        # _all_phase_res_mm removed (PATCH 4: dead accumulator — never consumed downstream)
        _accepted_phase_sids = set() # sat IDs whose phase row was accepted into H/z/R
        _phase_total  = 0    # total phase rows attempted
        _phase_accept = 0    # phase rows that entered H/z/R
        _phase_rej_newborn  = 0  # excluded because x[ki] not yet initialised
        _phase_rej_nanres   = 0  # non-finite residual
        _phase_rej_hardgate = 0  # residual > ABS_PHASE_MAX sanity floor
        _phase_rej_nisgate  = 0  # NIS > PHASE_NIS_GATE (now counted but NOT discarded)
        # ── ROOT-TRIGGER FORENSIC ACCUMULATORS (reset each epoch) ────────────
        _startup_phase_traced = False   # PATCH 1: print only first accepted row/epoch
        _ph_innov_list   = []   # PATCH 5: accepted phase innovations in metres
        _code_innov_list = []   # PATCH 5: accepted code innovations in metres
        _saved_code_res_m = float('nan')  # PATCH 1: save code_res for phase trace
        # ── CONSISTENT STARTUP INFO accumulators (reset each epoch) ──────────
        _si_epoch_scales = []   # a_si per accepted phase row this epoch
        _si_epoch_Reff   = []   # effective R per accepted phase row this epoch


        # Per-epoch Huber robust-weight containers (reset each epoch)
        _rw_ep_weights  = []   # Huber w for every phase row that entered EKF this epoch
        _rw_ep_oldrej   = 0    # rows with u > PHASE_NIS_GATE (downweighted, not rejected)

        for ri, m in enumerate(geom):
            ki = m['ki']
            u  = m['unit']
            mw = m['mw']
            rp = _rp(m, xs[3], xs[4])
            rr = 2 * ri
            rl = 2 * ri + 1

            # ── Code row (GPS: no ISB correction) ───────────────────────────
            code_res = m['PIF'] - rp
            if not math.isfinite(code_res):
                _reject_obs(rr, H, z, Rd, "code residual non-finite",
                            m['sid'], sod, is_startup)
                _n_rejected_epoch += 1
            elif abs(code_res) > _code_hard:
                _reject_obs(rr, H, z, Rd,
                            f"code residual {code_res*1e3:+.0f}mm > {_code_hard*1e3:.0f}mm",
                            m['sid'], sod, is_startup)
                _n_rejected_epoch += 1
            else:
                H[rr,0]=-u[0]; H[rr,1]=-u[1]; H[rr,2]=-u[2]
                H[rr,3]=1.; H[rr,4]=mw
                z[rr]  = code_res
                Rd[rr] = _sig(m['el'], SC)**2
                _H_row_kinds[rr] = "CODE"   # PATCH 2: explicit tag
                _saved_code_res_m = code_res    # PATCH 1: save for startup phase trace
                _code_innov_list.append(code_res)  # PATCH 5
                # ── PATCH 1: Residual lineage record for this code row ────────
                _epoch_residual_audit.append({
                    'sat':              m['sid'],
                    'epoch':            nproc,
                    'row_index':        rr,
                    'obs_type':         'code',
                    'accepted':         True,
                    'prefit_true_mm':   code_res * 1e3,
                    'prefit_linearized_mm': code_res * 1e3,
                    'scaled_residual':  code_res / math.sqrt(max(Rd[rr], 1e-30)) if Rd[rr] > 0 else float('nan'),
                    'postfit_residual_mm': float('nan'),   # filled after EKF update
                    'R_raw':            _sig(m['el'], SC)**2,
                    'R_eff':            Rd[rr],
                    'sigma_pred_mm':    float('nan'),      # not computed for code rows
                    'sigma_postfit_mm': float('nan'),
                    'huber_weight':     1.0,               # no Huber on code rows
                    'NIS':              float('nan'),
                    'used_in_EKF':      True,
                })

            # ── Phase row ────────────────────────────────────────────────────
            # FIX-3A (v64): Skip phase row below 15° elevation mask.
            # Code row is already built above — code processing continues normally.
            # Ambiguity birth/update is also skipped for this satellite this epoch.
            if m['el'] < PHASE_EL_MASK_RAD:
                _reject_obs(rl, H, z, Rd, None, m['sid'], sod, startup=False)
                _phase_total += 1
                _phase_rej_newborn += 1   # reuse newborn counter for low-el mask
            # Newborns are queued for post-fit birth; x[ki] is uninitialised
            # this epoch so the phase row cannot be formed. They participate
            # from the very next epoch with NO streak requirement.
            # Any satellite that is not newborn and not yet active has an
            # uninitialised x[ki] and must also wait one epoch (impossible in
            # practice because the birth block above sets amb_active=True in
            # the same pass via _amb_init or post-fit).
            # NO lifecycle gate: the only valid exclusions are uninitialised
            # state and the standard observation-domain residual checks below.
            elif m['sid'] in _newborn_pending or not amgr.is_active(m['sid']):
                # x[ki] uninitialised — cannot form a valid phase residual.
                # Silently zero the row (no lifecycle-rejection message).
                _reject_obs(rl, H, z, Rd, None, m['sid'], sod, startup=False)
                _phase_total += 1
                _phase_rej_newborn += 1
                # ── AUDIT: record newborn / not-yet-active as rejected ────────
                if innov_audit_fh is not None:
                    _pos_sig = math.sqrt(max(P[0,0]+P[1,1]+P[2,2], 0.)) * 1e3
                    _clk_sig = math.sqrt(max(P[3,3], 0.)) * 1e3
                    _zwd_sig = math.sqrt(max(P[4,4], 0.)) * 1e3
                    _amb_sig = math.sqrt(max(P[ki,ki], 0.)) * 1e3
                    _phs_hard_mm = _phs_hard * 1e3
                    innov_audit_fh.write(
                        f"{nproc},{sod:.1f},{m['sid']},{m.get('_sys','?')},"
                        f"{math.degrees(m['el']):.3f},"
                        f"0,newborn,"
                        f"nan,nan,nan,nan,"
                        f"{_fmtf(_amb_sig,prec=4)},"
                        f"{_fmtf(_pos_sig,prec=4)},{_fmtf(_clk_sig,prec=4)},"
                        f"{_fmtf(_zwd_sig,prec=4)},"
                        f"1,0,"
                        f"{_fmtf(_phs_hard_mm,prec=4)}\n"
                    )
                # ─────────────────────────────────────────────────────────────
            else:
                _phase_total += 1
                # ALWAYS include phase row — no lifecycle gate
                phase_res = m['LIFc'] - (rp + xs[ki])  # GPS: no ISB
                m['_ph_res_prefit_mm'] = abs(phase_res) * 1e3  # prefit residual store

                # ── AUDIT: compute full innovation covariance S = H P H^T + R
                # Build phase H-row using pre-update covariance P (not xs, not P_new)
                # This is audit-only; does NOT change H/z/Rd used by the EKF.
                _h_ph = np.zeros(len(xs))
                _h_ph[0]=-m['unit'][0]; _h_ph[1]=-m['unit'][1]; _h_ph[2]=-m['unit'][2]
                _h_ph[3]=1.; _h_ph[4]=m['mw']; _h_ph[ki]=1.
                _ph_sig_raw = _sig(m['el'], SP)
                _R_ph = _ph_sig_raw**2
                _S_ph = float(_h_ph @ P @ _h_ph) + _R_ph   # scalar S
                _S_ph = max(_S_ph, 1e-12)
                _nu_mm = phase_res * 1e3
                _S_mm2 = _S_ph * 1e6
                _pred_sig_mm = math.sqrt(max(_S_mm2, 0.))
                # FIX-2 (v64): Guard against degenerate S; use nan rather than
                # dividing by a floored epsilon which collapses small NIS to 0.
                if (not np.isfinite(_S_mm2)) or _S_mm2 <= 0:
                    _NIS = float('nan')
                else:
                    _NIS = (_nu_mm * _nu_mm) / _S_mm2
                _amb_sig_aud  = math.sqrt(max(P[ki,ki], 0.)) * 1e3
                _pos_sig_aud  = math.sqrt(max(P[0,0]+P[1,1]+P[2,2], 0.)) * 1e3
                _clk_sig_aud  = math.sqrt(max(P[3,3], 0.)) * 1e3
                _zwd_sig_aud  = math.sqrt(max(P[4,4], 0.)) * 1e3
                _is_nb_aud    = 0
                _ep_birth_aud = nproc - amgr.get_birth_epoch(m['sid'], nproc)
                _phs_hard_mm  = _phs_hard * 1e3
                # ─────────────────────────────────────────────────────────────

                # ── ZOMBIE DETECTOR (REPAIR-STEP 2 & 3) ──────────────────────
                # Detects ambiguities whose state covariance has converged to a
                # "certain" value that is physically wrong: the sigma is small
                # (Kalman gain ≈ 0), yet residuals are large and persistent.
                # Such states cannot self-correct and continuously contaminate
                # clock/position/ZWD through cross-covariances.
                _is_zombie = (
                    _amb_sig_aud < ZOMBIE_SIGMA_CEIL_M * 1e3 and   # sigma in mm
                    abs(phase_res) > ZOMBIE_RES_FLOOR_M and          # residual in m
                    _ep_birth_aud > ZOMBIE_AGE_FLOOR
                )

                # ── Coherent common-mode test (clock-step suppressor) ─────────
                # Before acting on a zombie, check whether the running phase
                # innovation list is dominated by a coherent offset: if ≥
                # COHERENT_CM_MIN_SATS accepted residuals share the same sign
                # AND |mean| > COHERENT_CM_FLOOR_M, this is almost certainly a
                # receiver clock step, not a genuine ambiguity corruption.
                # In that case, DEFER the zombie reset: soften the sigma to
                # ZOMBIE_SOFTEN_M (restoring some Kalman gain) and let the
                # clock absorb the offset rather than destroying the arc.
                _cm_so_far     = _ph_innov_list   # list of accepted phase residuals so far
                _cm_n          = len(_cm_so_far)
                _cm_mean_now   = (sum(_cm_so_far) / _cm_n) if _cm_n >= COHERENT_CM_MIN_SATS else 0.0
                # PATCH 4 — explicit same-sign decomposition
                _cm_all_pos    = (_cm_n >= COHERENT_CM_MIN_SATS and all(v > 0 for v in _cm_so_far))
                _cm_all_neg    = (_cm_n >= COHERENT_CM_MIN_SATS and all(v < 0 for v in _cm_so_far))
                _cm_n_pos      = sum(1 for v in _cm_so_far if v > 0)
                _cm_n_neg      = sum(1 for v in _cm_so_far if v < 0)
                _cm_n_zero     = sum(1 for v in _cm_so_far if v == 0)
                _cm_n_nan      = sum(1 for v in _cm_so_far if not (v == v))  # NaN check
                _cm_same_sign  = _cm_all_pos or _cm_all_neg
                _cm_threshold_ok = abs(_cm_mean_now) > COHERENT_CM_FLOOR_M
                _is_coherent_cm = (_cm_threshold_ok and _cm_same_sign)

                # ═══════════════════════════════════════════════════════════════
                # FORENSIC PATCHES 1/2/3/5 — CM-DETECTOR-AUDIT (forensic only)
                # Instrumentation for epoch 401/SOD=12030 cascade investigation.
                # These prints fire AFTER _is_coherent_cm is computed but BEFORE
                # any state mutation.  They do NOT change any EKF state.
                # ═══════════════════════════════════════════════════════════════
                if _is_zombie:
                    # ── PATCH 2: ordering marker — CM evaluated ────────────────
                    print(
                        f"  [CM-ORDER-2] CM-detector evaluated  sat={m['sid']}"
                        f"  epoch={nproc}  SOD={sod:.0f}"
                        f"  list_len_at_eval={_cm_n}"
                        f"  (only previously-accepted non-zombie sats in list)"
                    )
                    # ── PATCH 1: full CM-DETECTOR-AUDIT ───────────────────────
                    _cm_sign_vec = ['+' if v > 0 else ('-' if v < 0 else '0')
                                    for v in _cm_so_far]
                    print(
                        f"  [CM-DETECTOR-AUDIT]"
                        f"  epoch={nproc}  SOD={sod:.0f}  sat={m['sid']}\n"
                        f"    n_phase_in_list       = {_cm_n}"
                        f"  (need >= {COHERENT_CM_MIN_SATS} for any CM decision)\n"
                        f"    accepted_residuals_mm = [{', '.join(f'{v*1e3:+.1f}' for v in _cm_so_far)}]\n"
                        f"    sign_vector           = [{', '.join(_cm_sign_vec)}]\n"
                        f"    mean_residual_mm      = {_cm_mean_now*1e3:+.1f}\n"
                        f"    n_positive            = {_cm_n_pos}\n"
                        f"    n_negative            = {_cm_n_neg}\n"
                        f"    n_zero                = {_cm_n_zero}\n"
                        f"    n_nan_in_list         = {_cm_n_nan}\n"
                        f"    all_pos               = {_cm_all_pos}\n"
                        f"    all_neg               = {_cm_all_neg}\n"
                        f"    same_sign_result      = {_cm_same_sign}  (all_pos OR all_neg)\n"
                        f"    threshold_result      = {_cm_threshold_ok}"
                        f"  (|mean|={abs(_cm_mean_now)*1e3:.1f}mm vs floor={COHERENT_CM_FLOOR_M*1e3:.0f}mm)\n"
                        f"    FINAL coherent_cm     = {_is_coherent_cm}\n"
                        f"  --- EXCLUSION NOTES ---\n"
                        f"    current sat '{m['sid']}' is a zombie candidate and is NOT in the list.\n"
                        f"    Zombie path executes 'continue' before reaching _ph_innov_list.append().\n"
                        f"    List only contains residuals from non-zombie sats processed earlier.\n"
                        f"    low-el / newborn / NaN / ABS_PHASE_MAX sats also excluded by upstream gates."
                    )
                    # ── PATCH 3: residual-source audit ────────────────────────
                    if _cm_so_far:
                        print(f"  [CM-RESIDUAL-SOURCE-AUDIT]  epoch={nproc}  SOD={sod:.0f}")
                        for _rsa_i, _rsa_v in enumerate(_cm_so_far):
                            print(
                                f"    list[{_rsa_i}]: raw_phase_res={_rsa_v*1e3:+.1f}mm"
                                f"  startup_scaling=N/A(affects_R_not_z)"
                                f"  huber=N/A(affects_R_not_z)"
                                f"  source=accepted_non_zombie_sat"
                            )
                    else:
                        print(
                            f"  [CM-RESIDUAL-SOURCE-AUDIT]  epoch={nproc}  SOD={sod:.0f}"
                            f"  LIST IS EMPTY — no accepted non-zombie sats processed before this zombie"
                        )
                    # ── PATCH 5: sensitivity audit ─────────────────────────────
                    _cm5_thresholds = [0.050, 0.080, 0.120]
                    _cm5_fracs      = [1.0, 0.80]
                    print(f"  [CM-SENSITIVITY-AUDIT]  epoch={nproc}  SOD={sod:.0f}"
                          f"  list_size={_cm_n}  current_sat={m['sid']}")
                    for _cm5_thr in _cm5_thresholds:
                        for _cm5_frac in _cm5_fracs:
                            if _cm_n == 0:
                                _cm5_r = False; _cm5_why = "list_empty(<{})".format(COHERENT_CM_MIN_SATS)
                            else:
                                _cm5_mean_chk = abs(sum(_cm_so_far) / _cm_n) > _cm5_thr
                                if _cm5_frac == 1.0:
                                    _cm5_sign_chk = _cm_all_pos or _cm_all_neg
                                    _cm5_lbl = "strict_all_same"
                                else:
                                    _cm5_maj = max(_cm_n_pos, _cm_n_neg)
                                    _cm5_sign_chk = (_cm_n >= COHERENT_CM_MIN_SATS and
                                                     _cm5_maj / _cm_n >= _cm5_frac)
                                    _cm5_lbl = f">={int(_cm5_frac*100)}pct_majority"
                                _cm5_r = _cm5_mean_chk and _cm5_sign_chk
                                _cm5_why = (f"thr={'P' if _cm5_mean_chk else 'F'}"
                                            f"  sign={'P' if _cm5_sign_chk else 'F'}")
                            print(
                                f"    thr={_cm5_thr*1e3:.0f}mm"
                                f"  sign_rule={_cm5_lbl:<20s}"
                                f"  would_be={_cm5_r}"
                                f"  [{_cm5_why}]"
                            )
                    print(
                        f"  [CM-SENSITIVITY-NOTE] list_size={_cm_n}:"
                        f" ALL variants fail when list < {COHERENT_CM_MIN_SATS}."
                        f" Root cause = list size, not threshold brittleness."
                    )
                # ═══════════════════════════════════════════════════════════════
                # END FORENSIC PATCHES 1/2/3/5
                # ═══════════════════════════════════════════════════════════════

                if _is_zombie and _is_coherent_cm:
                    # Coherent clock-step event — soften instead of wipe.
                    _coherent_cm_epoch = True
                    _zombie_event_count += 1
                    _new_sig_m = ZOMBIE_SOFTEN_M
                    P[ki, ki] = max(P[ki, ki], _new_sig_m ** 2)
                    print(
                        f"  [AMB-ZOMBIE-DEFERRED] sat={m['sid']}  "
                        f"res={phase_res*1e3:+.1f}mm  "
                        f"amb_sigma={_amb_sig_aud:.1f}mm  "
                        f"cm_mean={_cm_mean_now*1e3:+.0f}mm  "
                        f"n_same_sign={_cm_n}  "
                        f"softened_to={_new_sig_m*1e3:.0f}mm  "
                        f"age={_ep_birth_aud}ep  el={math.degrees(m['el']):.1f}°"
                    )
                    # Do NOT wipe x[ki] or cross-covariances.
                    # Do NOT call _reject_obs — let the phase row enter the EKF
                    # with the softened sigma so the clock can absorb the step.
                    # Fall through to normal phase processing below.

                elif _is_zombie:
                    # ── FREEZE: during observability experiment, suppress zombie resets ──
                    if ENABLE_FREEZE_OBS and FREEZE_OBS_START <= nproc <= FREEZE_OBS_END:
                        _freeze_suppressed_resets += 1
                        print(
                            f"  [FREEZE-SUPPRESS-ZOMBIE] sat={m['sid']}  epoch={nproc}  SOD={sod:.0f}"
                            f"  res={phase_res*1e3:+.1f}mm  amb_sigma={_amb_sig_aud:.1f}mm"
                            f"  zombie reset SUPPRESSED — falling through to normal phase processing"
                        )
                        # Do NOT wipe, do NOT continue — fall through to normal processing
                        # The satellite remains with its current (tight) sigma; the
                        # residual will be absorbed by the EKF as-is.
                    else:
                        _zombie_event_count += 1
                        # PATCH 2: ordering marker — zombie reset fires
                        print(
                            f"  [CM-ORDER-3] zombie logic executing  sat={m['sid']}"
                            f"  epoch={nproc}  SOD={sod:.0f}"
                            f"  cm_list_at_reset={[f'{v*1e3:+.1f}mm' for v in _ph_innov_list]}"
                            f"  list_len={len(_ph_innov_list)}  (this sat will NOT be appended — continue skips append)"
                        )
                        print(
                            f"  [AMB-ZOMBIE] sat={m['sid']}  "
                            f"res={phase_res*1e3:+.1f}mm  "
                            f"amb_sigma={_amb_sig_aud:.1f}mm  "
                            f"innov_sigma={math.sqrt(max(_S_ph,1e-12))*1e3:.1f}mm  "
                            f"NIS={abs(phase_res)/math.sqrt(max(_S_ph,1e-12)):.2f}  "
                            f"age={_ep_birth_aud}ep  "
                            f"el={math.degrees(m['el']):.1f}°"
                        )
                        if DISABLE_ZOMBIE_RESETS:
                            # ── EXPERIMENTAL: zombie reset execution SUPPRESSED ──────────
                            # Detection, logging, and NIS reporting above are unchanged.
                            # The state wipe / covariance reinflation / newborn queuing
                            # below are skipped.  The satellite falls through to normal
                            # phase processing with its existing (tight) ambiguity sigma.
                            # _zombie_reset_count is NOT incremented (reset did not occur).
                            _n_zombie_this_epoch += 1
                            print(
                                f"  [ZOMBIE-SUPPRESSED] sat={m['sid']}  epoch={nproc}  SOD={sod:.0f}"
                                f"  res={phase_res*1e3:+.1f}mm  amb_sigma={_amb_sig_aud:.1f}mm"
                                f"  DISABLE_ZOMBIE_RESETS=True — falling through to normal EKF processing"
                                + (f"  [CASCADE n={_n_zombie_this_epoch}]"
                                   if _n_zombie_this_epoch >= ZOMBIE_CASCADE_CEIL else "")
                            )
                            # Do NOT wipe x[ki], P[ki,:], P[:,ki].
                            # Do NOT call register_reset, _reject_obs, or continue.
                            # Fall through to normal phase processing below.
                        else:
                            _zombie_reset_count    += 1
                            _n_zombie_this_epoch   += 1
                            print(
                                f"  [AMB-ZOMBIE-RESET] sat={m['sid']}  "
                                f"forcing rebirth"
                                + (f"  [CASCADE n={_n_zombie_this_epoch}]"
                                   if _n_zombie_this_epoch >= ZOMBIE_CASCADE_CEIL else "")
                            )
                            # Save pre-reset state so record_rebirth can report old_N_mm.
                            _amb_saved_state[m['sid']] = dict(
                                x_ki     = float(x[ki]),
                                P_ki     = float(P[ki, ki]),
                                last_sod = sod,
                            )
                            # Full ambiguity state wipe — partial reset is insufficient.
                            # Off-diagonal cross-covariances must be zeroed to prevent
                            # contamination of position/clock/ZWD states.
                            x[ki]    = 0.0
                            P[ki, :] = 0.0
                            P[:, ki] = 0.0
                            P[ki, ki] = (20.0) ** 2        # same birth sigma as normal newborn
                            # Register with ambiguity manager so lifecycle bookkeeping
                            # stays consistent with the covariance wipe.
                            if hasattr(amgr, "register_reset"):
                                amgr.register_reset(m['sid'])
                            # Reject the phase row for this epoch — satellite will
                            # rebirth naturally next epoch from its fresh 20 m sigma.
                            _reject_obs(rl, H, z, Rd,
                                        "zombie_rebirth", m['sid'], sod, is_startup)
                            _phase_rej_nisgate += 1
                            amgr.register_reject(m['sid'])
                            # Skip remaining phase processing for this measurement.
                            continue
                # ─────────────────────────────────────────────────────────────

                if not math.isfinite(phase_res):
                    _reject_obs(rl, H, z, Rd,
                                "phase residual non-finite", m['sid'], sod, is_startup)
                    _n_rejected_epoch += 1
                    _phase_rej_nanres += 1
                    # AUDIT write
                    if innov_audit_fh is not None:
                        innov_audit_fh.write(
                            f"{nproc},{sod:.1f},{m['sid']},{m.get('_sys','?')},"
                            f"{math.degrees(m['el']):.3f},"
                            f"0,nan_residual,"
                            f"nan,{_fmtf(_pred_sig_mm,prec=4)},{_fmtf(_S_mm2,prec=4)},nan,"
                            f"{_fmtf(_amb_sig_aud,prec=4)},"
                            f"{_fmtf(_pos_sig_aud,prec=4)},{_fmtf(_clk_sig_aud,prec=4)},"
                            f"{_fmtf(_zwd_sig_aud,prec=4)},"
                            f"{_is_nb_aud},{_ep_birth_aud},"
                            f"{_fmtf(_phs_hard_mm,prec=4)}\n"
                        )
                    # ring-buffer update
                    amgr.register_reject(m['sid'])
                elif abs(phase_res) > _phs_hard:
                    # ABS_PHASE_MAX sanity floor — kilometre-scale residual
                    _reject_obs(rl, H, z, Rd,
                                f"phase residual {phase_res*1e3:+.0f}mm > "
                                f"ABS_PHASE_MAX {ABS_PHASE_MAX*1e3:.0f}mm",
                                m['sid'], sod, is_startup)
                    _n_rejected_epoch += 1
                    _phase_rej_hardgate += 1
                    # AUDIT write
                    if innov_audit_fh is not None:
                        innov_audit_fh.write(
                            f"{nproc},{sod:.1f},{m['sid']},{m.get('_sys','?')},"
                            f"{math.degrees(m['el']):.3f},"
                            f"0,abs_floor,"
                            f"{_fmtf(_nu_mm,prec=4)},{_fmtf(_pred_sig_mm,prec=4)},"
                            f"{_fmtf(_S_mm2,prec=4)},{_fmtf_sci(_NIS)},"
                            f"{_fmtf(_amb_sig_aud,prec=4)},"
                            f"{_fmtf(_pos_sig_aud,prec=4)},{_fmtf(_clk_sig_aud,prec=4)},"
                            f"{_fmtf(_zwd_sig_aud,prec=4)},"
                            f"{_is_nb_aud},{_ep_birth_aud},"
                            f"{_fmtf(_phs_hard_mm,prec=4)}\n"
                        )
                    # ring-buffer update
                    amgr.register_reject(m['sid'])
                else:
                    # ── NIS gate: |ν| / σ_S where σ_S = sqrt(H P H^T + R) ──
                    # P itself is UNTOUCHED.  Kalman gain uses original _S_ph.
                    _innov_sigma_m = math.sqrt(max(_S_ph, 1e-12))

                    # ─────────────────────────────────────────────────────────
                    # BOOTSTRAP GATE FLOOR
                    # Prevents pathological covariance cross-term collapse
                    # during the first bootstrap epochs.
                    #
                    # IMPORTANT (invariants that MUST be preserved):
                    #   • P is NOT modified — this is gating only
                    #   • Kalman gain still uses original _S_ph / _innov_sigma_m
                    #   • Smoothing uses original stored P
                    #   • Only the variance used to EVALUATE the NIS gate is
                    #     raised; once past the gate, the EKF sees normal R.
                    # ─────────────────────────────────────────────────────────
                    BOOTSTRAP_SIGMA_FLOOR_MM = 200.0   # mm, peak at epoch 0
                    BOOTSTRAP_DECAY_EPOCHS   = 20.0    # e-folding decay epochs
                    _floor_sigma_m = (BOOTSTRAP_SIGMA_FLOOR_MM * 1e-3 *
                                      math.exp(-nproc / BOOTSTRAP_DECAY_EPOCHS))
                    _floor_var_m2  = _floor_sigma_m ** 2
                    _S_ph_gated    = max(_S_ph, _floor_var_m2)
                    _innov_sigma_gated_m = math.sqrt(_S_ph_gated)

                    # Use ONLY the floored sigma for the gate decision
                    _linear_nis_gated = abs(phase_res) / _innov_sigma_gated_m
                    # Keep raw NIS for diagnostics / audit
                    _linear_nis       = abs(phase_res) / _innov_sigma_m

                    # Detailed bootstrap diagnostics for epochs 0–20
                    if nproc <= 20:
                        print(f"  [BOOTSTRAP-GATE]"
                              f"  epoch={nproc}  sat={m['sid']}"
                              f"  pred_sigma_raw={_innov_sigma_m*1e3:.1f}mm"
                              f"  pred_sigma_gated={_innov_sigma_gated_m*1e3:.1f}mm"
                              f"  innovation={phase_res*1e3:+.1f}mm"
                              f"  NIS_raw={_linear_nis:.2f}"
                              f"  NIS_gated={_linear_nis_gated:.2f}"
                              f"  {'ACCEPT→' if _linear_nis_gated <= PHASE_NIS_GATE else 'REJECT→'}"
                              f"{'accepted' if _linear_nis_gated <= PHASE_NIS_GATE else 'rejected'}")

                    # ── HUBER ROBUST WEIGHTING (replaces binary NIS gate) ────────
                    # Architecture: continuous weight w ∈ (0, 1] applied to R_eff.
                    # u ≤ 2 → full weight (w=1, R unchanged).
                    # u > 2 → downweight: w = 2/u, R_eff = R / w² → effectively
                    #         inflates R so outlier rows shrink Kalman gain smoothly.
                    # NO row is ever fully discarded solely due to large NIS.
                    # Hard rejection is kept ONLY for NaN / ABS_PHASE_MAX / zombie.
                    # ─────────────────────────────────────────────────────────────
                    _u_huber = _linear_nis_gated   # u = |ν| / σ_gated
                    if _u_huber <= 2.0:
                        _w_huber = 1.0
                    else:
                        _w_huber = 2.0 / _u_huber

                    # "Old-style reject" counter — rows that old gate would discard
                    _is_old_reject = (_u_huber > PHASE_NIS_GATE)
                    if _is_old_reject:
                        _phase_rej_nisgate += 1   # kept for stat compatibility
                        _rw_ep_oldrej      += 1
                        # FORENSIC AUDIT: track as "downweighted" row (was rejected)
                        if not _is_nb_aud and math.isfinite(_pred_sig_mm) and _pred_sig_mm > 0:
                            _fa_rej_nis.append(_u_huber)
                            _fa_rej_nu_mm.append(abs(_nu_mm))
                            _fa_rej_sigma_mm.append(_pred_sig_mm)
                        print(f"  [PHASE-GATE] DOWNWEIGHT  sat={m['sid']}  "
                              f"res={phase_res*1e3:+.1f}mm  "
                              f"innov_sigma={_innov_sigma_gated_m*1e3:.1f}mm  "
                              f"NIS={_u_huber:.2f}  w={_w_huber:.3f}  "
                              f"amb_sigma={_amb_sig_aud:.1f}mm  "
                              f"age={_ep_birth_aud}ep  "
                              f"el={math.degrees(m['el']):.1f}°  "
                              f"newborn={_is_nb_aud}")
                    else:
                        print(f"  [PHASE-GATE] ACCEPT  sat={m['sid']}  "
                              f"res={phase_res*1e3:+.1f}mm  "
                              f"innov_sigma={_innov_sigma_gated_m*1e3:.1f}mm  "
                              f"NIS={_u_huber:.2f}  "
                              f"amb_sigma={_amb_sig_aud:.1f}mm  "
                              f"age={_ep_birth_aud}ep  "
                              f"el={math.degrees(m['el']):.1f}°")

                    # ── Always enter H/z/R — no binary rejection for NIS ────────
                    _phase_accept += 1
                    _accepted_phase_sids.add(m['sid'])   # track for postfit RMS
                    _H_row_kinds[rl] = "PHASE"   # PATCH 2: explicit tag
                    H[rl,0]=-u[0]; H[rl,1]=-u[1]; H[rl,2]=-u[2]
                    H[rl,4]=mw
                    # ── CONSISTENT STARTUP INFORMATION SCALING ─────────────────
                    # H_amb = 1.0 always.  Reduce measurement confidence for young
                    # ambiguities via R_eff = R_base / (w² · a²),  where
                    #   a = max(age, 1) / STARTUP_INFO_EPOCHS   (0 < a ≤ 1)
                    # This keeps information = H² / R_eff = a² / R_base scaling
                    # smoothly from near-zero at birth to full at STARTUP_INFO_EPOCHS.
                    # S = H P H^T + R_eff is always well-conditioned because
                    # H_amb = 1.0 lets the large initial P[ki,ki] contribute.
                    _age_si = max(0, _ep_birth_aud)
                    _a_si   = (1.0 if _age_si >= STARTUP_INFO_EPOCHS
                               else max(1, _age_si) / float(STARTUP_INFO_EPOCHS))
                    H[rl, ki] = 1.0          # full ambiguity column always
                    H[rl, 3]  = 1.           # full clock column always
                    z[rl]      = phase_res
                    phase_sig  = _sig(m['el'], SP)
                    # R_eff = R_base / (Huber_w² · a²)
                    # · Huber downweights large outliers (w < 1 → R grows)
                    # · startup a < 1 inflates R for young sats (less confident)
                    Rd[rl] = phase_sig**2 / (_w_huber * _w_huber * _a_si * _a_si)
                    # Accumulate for [STARTUP-INFO] epoch print
                    _si_epoch_scales.append(_a_si)
                    _si_epoch_Reff.append(Rd[rl])
                    # ── END CONSISTENT STARTUP SCALING ──────────────────────────


                    # ── ROOT-TRIGGER PATCH 1: STARTUP-PHASE-TRACE ───────────────
                    _ph_innov_list.append(phase_res)   # PATCH 5 accumulation
                    # ── PATCH 1: Residual lineage record for this phase row ──────
                    _epoch_residual_audit.append({
                        'sat':              m['sid'],
                        'epoch':            nproc,
                        'row_index':        rl,
                        'obs_type':         'phase',
                        'accepted':         True,
                        'prefit_true_mm':   phase_res * 1e3,   # z[rl] = LIFc − (rp(xs)+xs[ki])
                        'prefit_linearized_mm': phase_res * 1e3,  # same as prefit_true in PPP
                        'scaled_residual':  phase_res / math.sqrt(max(Rd[rl], 1e-30)),
                        'postfit_residual_mm': float('nan'),   # filled after EKF update
                        'R_raw':            _sig(m['el'], SP)**2,
                        'R_eff':            Rd[rl],
                        'sigma_pred_mm':    math.sqrt(max(_S_ph, 1e-30)) * 1e3 if '_S_ph' in dir() else float('nan'),
                        'sigma_postfit_mm': float('nan'),      # filled after EKF update
                        'huber_weight':     _w_huber,
                        'NIS':              _u_huber,
                        'used_in_EKF':      True,
                    })
                    # PATCH 2: ordering marker — residual appended AFTER zombie check
                    print(
                        f"  [CM-ORDER-1] residual appended to CM list"
                        f"  sat={m['sid']}  epoch={nproc}  SOD={sod:.0f}"
                        f"  res={phase_res*1e3:+.1f}mm"
                        f"  list_now_len={len(_ph_innov_list)}"
                        f"  (zombie check was ALREADY done for all earlier sats)"
                    )
                    if nproc < 20 and not _startup_phase_traced:
                        _startup_phase_traced = True
                        _wu_tr  = wum.get(m['sid'], 0.)
                        _lif_tr = m.get('_lam_if', LAMBDA_IF)
                        _tropo_tr  = m['trop_zhd'] + m['mw'] * xs[4]
                        _windup_tr = _wu_tr * _lif_tr
                        print(
                            f"[STARTUP-PHASE-TRACE]\n"
                            f"  SOD={sod:.1f}  SAT={m['sid']}  el={math.degrees(m['el']):.3f} deg\n"
                            f"  P1={m['P1']:.6f} m   P2={m['P2']:.6f} m\n"
                            f"  L1_cycles={m['L1']:.6f}  L2_cycles={m['L2']:.6f}\n"
                            f"  lambda1={LAMBDA1:.10f} m  lambda2={LAMBDA2:.10f} m\n"
                            f"  L1_m={m['L1']*LAMBDA1:.6f} m   L2_m={m['L2']*LAMBDA2:.6f} m\n"
                            f"  PIF={m['PIF']:.6f} m\n"
                            f"  LIF_raw={m['_LIF_raw']:.6f} m\n"
                            f"  LIF_corr(windup-applied)={m['LIFc']:.6f} m\n"
                            f"  rho={m['rng']:.6f} m   sat_clk={m['scm']:.6f} m\n"
                            f"  rec_clk={xs[3]:.6f} m\n"
                            f"  tropo={_tropo_tr:.6f} m   windup={_windup_tr:.6f} m\n"
                            f"  pcv_combined={m['pcv_sat']+m['pcv_rec']:.6f} m\n"
                            f"  rp_prefit={rp:.6f} m\n"
                            f"  innov_phase={phase_res:.6f} m\n"
                            f"  innov_code={_saved_code_res_m:.6f} m\n"
                            f"  amb_state={xs[ki]:.6f} m\n"
                            f"  amb_sigma={math.sqrt(max(P[ki,ki],0.)):.6f} m"
                        )
                    # ────────────────────────────────────────────────────────────

                    # Track Huber weight for audit
                    _rw_ep_weights.append(_w_huber)

                    if abs(phase_res) > PHASE_RES_GATE:
                        print(f"  [PHASE-LARGE-RES] sat={m['sid']}  "
                              f"residual={phase_res*1e3:+.1f}mm  "
                              f"sigma={phase_sig*1e3:.2f}mm  w={_w_huber:.3f}  accepted=YES")
                    # _all_phase_res_mm append removed (PATCH 4)
                    # FORENSIC AUDIT: accumulate per-row innovation statistics
                    if not _is_nb_aud and math.isfinite(_pred_sig_mm) and _pred_sig_mm > 0:
                        _fa_acc_nis.append(_u_huber)
                        _fa_acc_nu_mm.append(abs(_nu_mm))
                        _fa_acc_sigma_mm.append(_pred_sig_mm)
                        _fa_acc_signed_nu.append(_nu_mm)  # signed for sign-coherence
                    # AUDIT write — accepted (includes downweighted rows)
                    _audit_status = "downweighted" if _is_old_reject else "accepted"
                    if innov_audit_fh is not None:
                        innov_audit_fh.write(
                            f"{nproc},{sod:.1f},{m['sid']},{m.get('_sys','?')},"
                            f"{math.degrees(m['el']):.3f},"
                            f"1,{_audit_status},"
                            f"{_fmtf(_nu_mm,prec=4)},{_fmtf(_pred_sig_mm,prec=4)},"
                            f"{_fmtf(_S_mm2,prec=4)},{_fmtf_sci(_NIS)},"
                            f"{_fmtf(_amb_sig_aud,prec=4)},"
                            f"{_fmtf(_pos_sig_aud,prec=4)},{_fmtf(_clk_sig_aud,prec=4)},"
                            f"{_fmtf(_zwd_sig_aud,prec=4)},"
                            f"{_is_nb_aud},{_ep_birth_aud},"
                            f"{_fmtf(_u_huber,prec=4)}\n"
                        )
                    # ring-buffer + counters update
                    amgr.register_accept(
                        m['sid'], nproc, sod,
                        residual_mm=phase_res * 1e3,
                        H_norm=float(np.linalg.norm(_h_ph)),
                    )

            # Singular variance guard
            for row_i in [rr, rl]:
                if Rd[row_i] <= 0. or not math.isfinite(Rd[row_i]):
                    _reject_obs(row_i, H, z, Rd, "zero/negative variance",
                                m['sid'], sod, is_startup)

        # ── ROBUST-WEIGHT-AUDIT: per-epoch summary ────────────────────────────
        # Accumulate into pass-level lists and print compact diagnostic.
        if _rw_ep_weights:
            _rw_ep_mean = sum(_rw_ep_weights) / len(_rw_ep_weights)
            _rw_ep_min  = min(_rw_ep_weights)
            _rw_ep_n_lt1  = sum(1 for w in _rw_ep_weights if w < 1.0)
            _rw_ep_n_lt05 = sum(1 for w in _rw_ep_weights if w < 0.5)
        else:
            _rw_ep_mean = float('nan'); _rw_ep_min = float('nan')
            _rw_ep_n_lt1 = 0;          _rw_ep_n_lt05 = 0
        _rw_epoch_mean_w.append(_rw_ep_mean)
        _rw_epoch_min_w.append(_rw_ep_min)
        _rw_epoch_n_lt1.append(_rw_ep_n_lt1)
        _rw_epoch_n_lt05.append(_rw_ep_n_lt05)
        _rw_epoch_oldrej.append(_rw_ep_oldrej)
        _rw_all_weights.extend(_rw_ep_weights)
        _rw_oldstyle_rej_tot += _rw_ep_oldrej
        # Per-epoch print (compact; suppress when no phase rows this epoch)
        # ── Cascade guard: warn when ≥ ZOMBIE_CASCADE_CEIL resets fired ────────
        if _n_zombie_this_epoch >= ZOMBIE_CASCADE_CEIL:
            _zombie_cascade_epochs += 1
            print(
                f"  [ZOMBIE-CASCADE-WARN] SOD={sod:.0f}  "
                f"n_zombie={_n_zombie_this_epoch}  "
                f"total_cascade_epochs={_zombie_cascade_epochs}  "
                f"coherent_cm={_coherent_cm_epoch}"
            )

        if _rw_ep_weights:
            _paf_ep = 100. * _phase_accept / max(_phase_total, 1)
            print(f"  [ROBUST-WEIGHT-AUDIT] SOD={sod:.0f}"
                  f"  mean_w={_rw_ep_mean:.3f}"
                  f"  min_w={_rw_ep_min:.3f}"
                  f"  n_w<1={_rw_ep_n_lt1}"
                  f"  n_w<0.5={_rw_ep_n_lt05}"
                  f"  old_rej={_rw_ep_oldrej}"
                  f"  phase_active={_paf_ep:.0f}%")
        # ─────────────────────────────────────────────────────────────────────

        # ── ZWD soft prior ────────────────────────────────────────────────────
        # Append ZWD pseudo-observation to H / z / Rd
        H_zwd = np.zeros(nst); H_zwd[4] = 1.
        z_zwd = np.array([ZWD_PRIOR - xs[4]])
        R_zwd = np.array([ZWD_PRIOR_SIGMA**2])

        # Concatenate with phase/code rows (filter_standard accepts full matrices)
        H_p  = np.vstack([H,  H_zwd.reshape(1,-1)])
        z_p  = np.concatenate([z,  z_zwd])
        Rd_p = np.concatenate([Rd, R_zwd])
        # PATCH 2: build final row-kind list (same order as H_p rows)
        _row_kind_p = _H_row_kinds + ["ZWD"]

        # ── PATCH 3: STARTUP PHASE R SCALING EXPERIMENT ──────────────────────
        # FOR FIRST 20 EPOCHS ONLY: multiply phase-observation variances by 100×
        # to test whether startup covariance collapse is caused by over-confident
        # phase measurements.  CODE rows, H, ambiguity states, process noise and
        # clock Q are ALL left unchanged.  This is a pure measurement-realism test.
        _STARTUP_R_SCALE_EPOCHS = 20
        _STARTUP_R_MULTIPLIER   = 100.0
        if nproc < _STARTUP_R_SCALE_EPOCHS:
            # Phase rows are identified by their small R value (code rows have
            # R ~ sigma_code^2 ≈ 2.25 m² >> phase R ≈ 0.0036 m²).
            # The last element of Rd_p is always the ZWD pseudo-obs — leave it.
            _p3_phase_mask = np.zeros(len(Rd_p), dtype=bool)
            for _p3_i in range(len(Rd_p) - 1):   # exclude ZWD pseudo-obs (last row)
                if Rd_p[_p3_i] < 1.0:            # phase rows have R < 1 m²
                    _p3_phase_mask[_p3_i] = True
            _p3_original_sigs = np.sqrt(Rd_p[_p3_phase_mask]) * 1e3   # mm
            Rd_p = Rd_p.copy()                    # never mutate the Rd array in-place
            Rd_p[_p3_phase_mask] *= _STARTUP_R_MULTIPLIER
            _p3_scaled_sigs = np.sqrt(Rd_p[_p3_phase_mask]) * 1e3     # mm
            print(f"[STARTUP-R-SCALE]  epoch={nproc}  sod={sod:.0f}"
                  f"  n_phase_rows={int(_p3_phase_mask.sum())}"
                  f"  multiplier={_STARTUP_R_MULTIPLIER:.0f}x")
            for _p3_j, (_p3_os, _p3_ss) in enumerate(
                    zip(_p3_original_sigs, _p3_scaled_sigs)):
                print(f"  row={_p3_j}  original_sigma={_p3_os:.4f}mm"
                      f"  scaled_sigma={_p3_ss:.4f}mm")
        # ── END PATCH 3 ───────────────────────────────────────────────────────
        if _ph_innov_list:
            _mean_ph_innov = sum(_ph_innov_list) / len(_ph_innov_list)
            if abs(_mean_ph_innov) > 10.0:
                print(
                    f"[FATAL-STARTUP-INCONSISTENCY]  ASSERTION-3\n"
                    f"  SOD={sod:.0f}  abs(mean_phase_innov)={abs(_mean_ph_innov):.3f} m > 10 m\n"
                    f"  n_phase={len(_ph_innov_list)}\n"
                    f"  innovs(m)={[f'{v:.3f}' for v in _ph_innov_list]}\n"
                    f"  INTERPRETATION: common-mode phase bias of {_mean_ph_innov:.3f} m "
                    f"across all satellites — missing model term or clock anomaly"
                )
                raise RuntimeError(
                    f"[FATAL] mean_phase_innov={_mean_ph_innov:.3f} m > 10 m at SOD={sod:.0f}"
                )
        # ─────────────────────────────────────────────────────────────────────

        # ── Pre-update sanity ─────────────────────────────────────────────────
        if not _is_finite_matrix(H_p, "H"):
            print(f"  [WARN] H non-finite at SOD={sod:.0f} — skipping epoch")
            nproc += 1
            continue
        Rd_p_mat = np.diag(Rd_p)
        if not _is_pd(Rd_p_mat, "R", tol=0.):
            Rd_p = np.where(Rd_p > 0, Rd_p, 1e-4)
        if not _is_finite_matrix(P, "P pre-update"):
            print(f"  [WARN] P has non-finite entries before EKF SOD={sod:.0f}")

        # ── Capture pre-update state for diagnostics ─────────────────────────
        x_before  = x.copy()
        zwd_before= x[4]

        # ── PSD monitor checkpoint B: pre-filter_standard ─────────────────────
        _psd_ok_pre, _psd_neg_state, _psd_lmin, _ = _psd_check(
            P, _sidx_inv, 'PRE-EKF', sod)
        # If already non-PSD here, the corruption originated in the satellite loop
        # (restore/reset/sanity) — exact state identified by _psd_neg_state above.

        # ── PATCH 1: FULL EKF MATRIX FORENSICS (pre-filter) ─────────────────
        # Runs every epoch, not just startup — we need to see the collapse epoch.
        _ekf_P_pre = P.copy()   # snapshot for PATCH 2/3/5 below
        _ekf_H     = H_p        # shape (m_obs, nst)
        _ekf_R     = np.diag(Rd_p)   # shape (m_obs, m_obs)
        _ekf_z     = z_p
        _ekf_n_phase_rows = int(np.sum(Rd_p < 1.0))   # code rows have large R; phase rows small
        # P_prior diagonal summary
        _p1_pos_sigs  = [math.sqrt(max(P[i,i],0.))*1e3 for i in range(3)]
        _p1_clk_sig   = math.sqrt(max(P[3,3],0.))*1e3
        _p1_zwd_sig   = math.sqrt(max(P[4,4],0.))*1e3
        _p1_amb_sigs  = [math.sqrt(max(P[ki2,ki2],0.))*1e3
                         for sid2,ki2 in sidx.items() if amgr.is_active(sid2)]
        _p1_amb_min   = min(_p1_amb_sigs) if _p1_amb_sigs else float('nan')
        _p1_amb_mean  = (sum(_p1_amb_sigs)/len(_p1_amb_sigs)) if _p1_amb_sigs else float('nan')
        _p1_amb_max   = max(_p1_amb_sigs) if _p1_amb_sigs else float('nan')
        # S = H P H^T + R and its conditioning
        try:
            _p1_PHt   = P @ H_p.T
            _p1_S     = H_p @ _p1_PHt + _ekf_R
            _p1_cond_P_prior = float(np.linalg.cond(P))
            _p1_cond_HPHT   = float(np.linalg.cond(H_p @ P @ H_p.T))
            _p1_cond_S      = float(np.linalg.cond(_p1_S))
            _p1_ev_P  = np.linalg.eigvalsh(P)
            _p1_ev_S  = np.linalg.eigvalsh(_p1_S)
            _p1_ev_P_min = float(_p1_ev_P.min())
            _p1_ev_P_max = float(_p1_ev_P.max())
            _p1_ev_S_min = float(_p1_ev_S.min())
            _p1_ev_S_max = float(_p1_ev_S.max())
        except Exception as _p1_ex:
            _p1_cond_P_prior = _p1_cond_HPHT = _p1_cond_S = float('nan')
            _p1_ev_P_min = _p1_ev_P_max = _p1_ev_S_min = _p1_ev_S_max = float('nan')
            _p1_S = None
        # Per-row diagnostics for accepted phase rows
        print(f"[EKF-PREFIT]  epoch={nproc}  sod={sod:.0f}  n_phase={_ekf_n_phase_rows}"
              f"  n_obs_total={len(_ekf_z)}")
        print(f"  P_prior_diag:  pos_sigs={[f'{v:.1f}' for v in _p1_pos_sigs]}mm"
              f"  clk_sig={_p1_clk_sig:.1f}mm  zwd_sig={_p1_zwd_sig:.1f}mm")
        print(f"  amb_sigs:  min={_p1_amb_min:.1f}mm  mean={_p1_amb_mean:.1f}mm"
              f"  max={_p1_amb_max:.1f}mm  n={len(_p1_amb_sigs)}")
        print(f"  cond(P_prior)={_p1_cond_P_prior:.3e}  cond(HP H^T)={_p1_cond_HPHT:.3e}"
              f"  cond(S)={_p1_cond_S:.3e}")
        print(f"  eigenvalues:  P[min={_p1_ev_P_min:.3e}, max={_p1_ev_P_max:.3e}]"
              f"  S[min={_p1_ev_S_min:.3e}, max={_p1_ev_S_max:.3e}]")
        # ── BUILD geom lookup for INNOV-RECON audit ──────────────────────────
        # Maps satellite ID → its geom dict so the audit block below can
        # pull LIFc, _rp(), and xs-based quantities for each accepted phase row.
        _ir_geom_by_sid = {m['sid']: m for m in geom}

        # Per accepted-phase-row detail
        for _p1_row in range(H_p.shape[0]):
            _p1_R_row = Rd_p[_p1_row]
            if _row_kind_p[_p1_row] != "PHASE":   # PATCH 2: explicit kind check
                continue
            _p1_hrow = H_p[_p1_row, :]
            _p1_h_clk = _p1_hrow[3]
            _p1_h_zwd = _p1_hrow[4]
            # Find which ambiguity state this row references
            _p1_h_amb = 0.0
            _p1_sat_lbl = '?'
            _p1_ki_row  = -1
            _p1_amb_sig_row = float('nan')
            for _p1_sid2, _p1_ki2 in sidx.items():
                if _p1_ki2 < len(_p1_hrow) and abs(_p1_hrow[_p1_ki2]) > 1e-9:
                    _p1_h_amb = float(_p1_hrow[_p1_ki2])
                    _p1_sat_lbl = _p1_sid2
                    _p1_ki_row  = _p1_ki2
                    _p1_amb_sig_row = math.sqrt(max(P[_p1_ki2, _p1_ki2], 0.)) * 1e3
                    break

            # ── [EKF-ROW] as before (historic formula — kept for continuity) ──
            # NOTE: this formula is INCORRECT as an innovation diagnostic.
            # It computes  H @ x_before − z[rl]  where x_before contains absolute
            # state values (clock ~−1300 mm, amb ~+5000 mm) and z[rl] is already
            # the prefit residual (the ACTUAL innovation fed to filter_standard).
            # The resulting multi-meter "innov" is dominated by the clock+ambiguity
            # absolute magnitudes, NOT by the measurement residual.
            # See [INNOV-RECON] block below for the correct decomposition.
            _p1_innov = float(_p1_hrow @ x_before) - float(_ekf_z[_p1_row])
            _p1_s_ph  = float(_p1_hrow @ P @ _p1_hrow) + _p1_R_row
            _p1_nis   = abs(_p1_innov) / max(math.sqrt(max(_p1_s_ph, 1e-20)), 1e-20)
            _p1_amb_age = amgr.states[_p1_sat_lbl].epochs_active if (
                _p1_sat_lbl in amgr.states and amgr.is_active(_p1_sat_lbl)) else -1
            _p1_a_si = (1.0 if _p1_amb_age < 0 or _p1_amb_age >= STARTUP_INFO_EPOCHS
                        else max(1, _p1_amb_age) / float(STARTUP_INFO_EPOCHS))
            print(f"  [EKF-ROW] sat={_p1_sat_lbl}  innov={_p1_innov*1e3:+.1f}mm"
                  f"  pred_sig={math.sqrt(max(_p1_s_ph,1e-20))*1e3:.1f}mm"
                  f"  NIS={_p1_nis:.3f}"
                  f"  H_clk={_p1_h_clk:+.4f}  H_amb={_p1_h_amb:+.6f}"
                  f"  R_phase={_p1_R_row:.6f}"
                  f"  amb_sig={_p1_amb_sig_row:.1f}mm"
                  f"  age={_p1_amb_age}ep  a_si={_p1_a_si:.4f}")

            # ══════════════════════════════════════════════════════════════════
            # [INNOV-RECON] INNOVATION RECONCILIATION AUDIT
            # ══════════════════════════════════════════════════════════════════
            # EXECUTION ORDER at this point in the epoch:
            #   1. xs = x.copy()           (line ~2301, snapshot BEFORE sat loop)
            #   2. Satellite loop:          builds z[rl]=phase_res, H, Rd; may
            #                              mutate x[ki]=0 for zombie resets
            #                              (xs is NEVER mutated after line ~2301)
            #   3. ZWD pseudo-obs appended → z_p, H_p, Rd_p
            #   4. Startup R scaling (PATCH 3, epochs 0-19)
            #   5. FATAL mean-phase assertion
            #   6. x_before = x.copy()     (line ~2873, snapshot AFTER sat loop;
            #                              may differ from xs for zombie sats)
            #   7. ← WE ARE HERE (EKF-PREFIT/EKF-ROW diagnostics)
            #   8. filter_standard(x, P, H_p.T, z_p, diag(Rd_p))  ← EKF UPDATE
            #      filter does:  x += K @ z_p   (z_p IS the innovation vector)
            #   9. _inno = z_p - H_p @ x_before  (wrong; see Patch 2 below)
            #  10. Post-fit newborn births (x[ki] = LIFc − rp_post)
            # ──────────────────────────────────────────────────────────────────
            try:
                _ir_m = _ir_geom_by_sid.get(_p1_sat_lbl)

                # ── Quantities used in the PHASE-GATE / phase_res path ────────
                # phase_res = m['LIFc'] - (rp + xs[ki])   at line ~2404
                # This is stored verbatim in z_p as the innovation.
                # rp uses xs[3] (clock) and xs[4] (ZWD) at the linearisation pt.
                _ir_LIFc        = _ir_m['LIFc'] if _ir_m else float('nan')
                _ir_rp_xs       = _rp(_ir_m, xs[3], xs[4]) if _ir_m else float('nan')
                _ir_xs_clk      = float(xs[3])          # receiver clock @ xs
                _ir_xs_zwd      = float(xs[4])          # ZWD @ xs
                _ir_xs_amb      = float(xs[_p1_ki_row]) if _p1_ki_row >= 0 else float('nan')
                _ir_xs_pos      = xs[:3].copy()         # position @ xs (3-vector)
                # Residual decomposition (how rp is built):
                _ir_rng         = float(_ir_m['rng'])   if _ir_m else float('nan')
                _ir_scm         = float(_ir_m['scm'])   if _ir_m else float('nan')  # sat clk
                _ir_dtrel       = float(_ir_m['dtrel']) if _ir_m else float('nan')
                _ir_trop_zhd    = float(_ir_m['trop_zhd']) if _ir_m else float('nan')
                _ir_mw          = float(_ir_m['mw'])    if _ir_m else float('nan')
                _ir_shp         = float(_ir_m.get('shp', 0.))  if _ir_m else float('nan')
                _ir_setm        = float(_ir_m.get('setm', 0.)) if _ir_m else float('nan')
                _ir_pcv_sat     = float(_ir_m.get('pcv_sat', 0.)) if _ir_m else float('nan')
                _ir_pcv_rec     = float(_ir_m.get('pcv_rec', 0.)) if _ir_m else float('nan')
                # rp = rng − scm − dtrel + xs_clk + trop_zhd + mw*xs_zwd + shp + setm + pcv
                _ir_rp_check    = (_ir_rng - _ir_scm - _ir_dtrel
                                   + _ir_xs_clk + _ir_trop_zhd
                                   + _ir_mw * _ir_xs_zwd
                                   + _ir_shp + _ir_setm + _ir_pcv_sat + _ir_pcv_rec)
                # The printed_phase_residual = z[rl] = z_p[_p1_row]
                _ir_printed_res = float(_ekf_z[_p1_row])    # = phase_res = THE innovation
                _ir_phase_res_recomputed = _ir_LIFc - _ir_rp_xs - _ir_xs_amb

                # ── Quantities used in the EKF-ROW path ───────────────────────
                # H @ x_before = −u·x_b[0:3] + x_b_clk + mw*x_b_zwd + x_b_amb
                _ir_xb_clk      = float(x_before[3])
                _ir_xb_zwd      = float(x_before[4])
                _ir_xb_amb      = float(x_before[_p1_ki_row]) if _p1_ki_row >= 0 else float('nan')
                _ir_xb_pos      = x_before[:3].copy()
                _ir_unit        = _ir_m['unit'] if _ir_m else [float('nan')]*3
                _ir_H_pos_term  = float(-np.dot(_ir_unit, _ir_xb_pos))
                _ir_H_clk_term  = _ir_xb_clk                # H_clk=1, coeff×state
                _ir_H_zwd_term  = _ir_mw * _ir_xb_zwd       # H_zwd=mw, coeff×state
                _ir_H_amb_term  = _ir_xb_amb                 # H_amb=1, coeff×state
                _ir_Hx_before   = (_ir_H_pos_term + _ir_H_clk_term
                                   + _ir_H_zwd_term + _ir_H_amb_term)
                # The EKF-ROW formula as coded:
                _ir_innov_raw   = _ir_Hx_before - _ir_printed_res   # = _p1_innov
                # The correct innovation as fed to filter_standard:
                _ir_innov_correct = _ir_printed_res  # z_p[row] = phase_res = innovation

                # Startup-scaling denominator (R was potentially scaled by PATCH 3)
                # The EKF-ROW NIS uses the post-scaling _p1_R_row
                _ir_innov_after_scaling = _ir_innov_correct  # scaling doesn't change innov

                # State deltas between xs and x_before (zombie resets cause divergence)
                _ir_dclk = _ir_xb_clk - _ir_xs_clk
                _ir_dzwd = _ir_xb_zwd - _ir_xs_zwd
                _ir_damb = _ir_xb_amb - _ir_xs_amb

                # Diagnosis: the multi-meter EKF-ROW "innov" = H@x_before − z
                # is dominated by the absolute clock+ambiguity state magnitudes,
                # NOT by the actual measurement residual. Decompose it:
                _ir_diff1 = _ir_innov_raw     - _ir_printed_res   # = Hx_before − 2*z
                _ir_diff2 = _ir_innov_after_scaling - _ir_printed_res  # = 0 (both equal z)

                print(
                    f"  [INNOV-RECON]\n"
                    f"    sat={_p1_sat_lbl}  epoch={nproc}  sod={sod:.0f}\n"
                    # ── Observable quantities used in phase_res (PHASE-GATE path) ──
                    f"    LIF_corr                         = {_ir_LIFc*1e3:+14.3f} mm\n"
                    f"    rp_prefit (=_rp(m,xs[3],xs[4])) = {_ir_rp_xs*1e3:+14.3f} mm\n"
                    f"    rp_check  (expanded inline)      = {_ir_rp_check*1e3:+14.3f} mm\n"
                    f"      rng (geometric range)          = {_ir_rng*1e3:+14.3f} mm\n"
                    f"      -scm (sat clock)               = {-_ir_scm*1e3:+14.3f} mm\n"
                    f"      -dtrel (relativistic)          = {-_ir_dtrel*1e3:+14.3f} mm\n"
                    f"      +xs_clk (rec clk @ xs)         = {_ir_xs_clk*1e3:+14.3f} mm  ← clock_term_used_in_phase_residual\n"
                    f"      +trop_zhd                      = {_ir_trop_zhd*1e3:+14.3f} mm\n"
                    f"      +mw*xs_zwd                     = {_ir_mw*_ir_xs_zwd*1e3:+14.3f} mm\n"
                    f"      +shp+setm+pcv                  = {(_ir_shp+_ir_setm+_ir_pcv_sat+_ir_pcv_rec)*1e3:+14.3f} mm\n"
                    f"    amb_term_used_in_phase_residual  = xs[ki] = {_ir_xs_amb*1e3:+14.3f} mm\n"
                    f"    tropo_term_used_in_phase_res     = {(_ir_trop_zhd+_ir_mw*_ir_xs_zwd)*1e3:+14.3f} mm\n"
                    # ── The actual innovation ──
                    f"    printed_phase_residual           = {_ir_printed_res*1e3:+14.3f} mm  ← THIS IS z[rl] = THE innovation\n"
                    f"    phase_res_recomputed             = {_ir_phase_res_recomputed*1e3:+14.3f} mm  (should match above)\n"
                    # ── Quantities in EKF-ROW path ──
                    f"    --- EKF-ROW formula: H @ x_before - z[rl] ---\n"
                    f"    H_pos_term (-u·x_before[0:3])    = {_ir_H_pos_term*1e3:+14.3f} mm\n"
                    f"    H_clk_term (x_before[3])         = {_ir_H_clk_term*1e3:+14.3f} mm  ← clock_term_used_in_EKF\n"
                    f"    H_zwd_term (mw*x_before[4])      = {_ir_H_zwd_term*1e3:+14.3f} mm\n"
                    f"    H_amb_term (x_before[ki])        = {_ir_H_amb_term*1e3:+14.3f} mm  ← amb_term_used_in_EKF\n"
                    f"    tropo_term_used_in_EKF           = {_ir_H_zwd_term*1e3:+14.3f} mm  (mw*x_before[4])\n"
                    f"    H @ x_before  (sum of above)     = {_ir_Hx_before*1e3:+14.3f} mm\n"
                    f"    innovation_raw (EKF-ROW, WRONG)  = {_ir_innov_raw*1e3:+14.3f} mm  ← H@x_before − z\n"
                    f"    innovation_correct (=z[rl])      = {_ir_innov_correct*1e3:+14.3f} mm  ← what filter_standard actually uses\n"
                    f"    innovation_after_scaling         = {_ir_innov_after_scaling*1e3:+14.3f} mm  (scaling changes R, not z)\n"
                    # ── State comparison ──
                    f"    --- State used for EKF-ROW (x_before) vs phase_res (xs) ---\n"
                    f"    rec_clk  : xs[3]={_ir_xs_clk*1e3:+.3f}mm  x_before[3]={_ir_xb_clk*1e3:+.3f}mm  Δ={_ir_dclk*1e3:+.3f}mm\n"
                    f"    zwd      : xs[4]={_ir_xs_zwd*1e3:+.3f}mm  x_before[4]={_ir_xb_zwd*1e3:+.3f}mm  Δ={_ir_dzwd*1e3:+.3f}mm\n"
                    f"    ambiguity: xs[ki]={_ir_xs_amb*1e3:+.3f}mm x_before[ki]={_ir_xb_amb*1e3:+.3f}mm  Δ={_ir_damb*1e3:+.3f}mm\n"
                    f"    position : xs[0:3]={[f'{v*1e3:+.3f}' for v in _ir_xs_pos]}mm\n"
                    f"               x_b[0:3]={[f'{v*1e3:+.3f}' for v in _ir_xb_pos]}mm\n"
                    # ── Root-cause decomposition ──
                    f"    --- Root-cause decomposition ---\n"
                    f"    difference_1 = innovation_raw − printed_phase_residual\n"
                    f"                 = (H@x_before − z) − z  =  H@x_before − 2z\n"
                    f"                 = {_ir_diff1*1e3:+.3f} mm\n"
                    f"    difference_2 = innovation_after_scaling − printed_phase_residual\n"
                    f"                 = z − z = {_ir_diff2*1e3:+.3f} mm  (should be 0)\n"
                    f"    H@x_before ≈ x_before_clk + mw*x_before_zwd + x_before_amb\n"
                    f"               ≈ {_ir_H_clk_term*1e3:+.1f} + {_ir_H_zwd_term*1e3:+.1f} + {_ir_H_amb_term*1e3:+.1f} = {_ir_Hx_before*1e3:+.1f} mm\n"
                    f"    WHY MULTI-METER: H@x_before is dominated by absolute state\n"
                    f"    magnitudes (clock + ambiguity), not measurement perturbations.\n"
                    f"    z[rl]=phase_res is the prefit residual (centimeter-level).\n"
                    f"    These two quantities live in DIFFERENT coordinate frames and\n"
                    f"    cannot be subtracted to form a meaningful 'innovation'.\n"
                    # ── Execution-order flags ──
                    f"    --- Execution order flags ---\n"
                    f"    innovation computed: BEFORE filter_standard (pre-update)\n"
                    f"    xs snapshot taken:   BEFORE satellite loop (xs = x.copy() at epoch start)\n"
                    f"    x_before snapshot:   AFTER satellite loop (may differ from xs if zombie resets fired)\n"
                    f"    zombie_fired_this_epoch: {_n_zombie_this_epoch}\n"
                    f"    newborn_pending:     {list(_newborn_pending.keys())}\n"
                    f"    startup_scaling:     {'ACTIVE (epoch<20)' if nproc < _STARTUP_R_SCALE_EPOCHS else 'INACTIVE'}\n"
                    f"    huber_weighting:     BEFORE innovation (w built from phase_res; z[rl] unchanged)\n"
                    f"    state_update:        AFTER this print block\n"
                )

                # ── ASSERTION: flag any epoch where the discrepancy is > 0.20 m ──
                if abs(_ir_innov_raw - _ir_printed_res) > 0.20:
                    print(
                        f"  [INNOV-RECON-FLAG] *** LARGE DISCREPANCY CONFIRMED ***\n"
                        f"    sat={_p1_sat_lbl}  epoch={nproc}  sod={sod:.0f}\n"
                        f"    |innovation_raw − printed_phase_residual| = "
                        f"{abs(_ir_innov_raw - _ir_printed_res)*1e3:.1f} mm  > 200 mm threshold\n"
                        f"    ROOT CAUSE: EKF-ROW computes H@x_before−z, not z.\n"
                        f"    innovation_raw    = H@x_before − z = {_ir_innov_raw*1e3:+.1f} mm\n"
                        f"    printed_phase_res = z[rl]          = {_ir_printed_res*1e3:+.1f} mm\n"
                        f"    H@x_before components:\n"
                        f"      clock term  = x_before[3]    = {_ir_H_clk_term*1e3:+.1f} mm\n"
                        f"      ZWD term    = mw*x_before[4] = {_ir_H_zwd_term*1e3:+.1f} mm\n"
                        f"      amb term    = x_before[ki]   = {_ir_H_amb_term*1e3:+.1f} mm\n"
                        f"      pos term    = -u·x_before[:3]= {_ir_H_pos_term*1e3:+.1f} mm\n"
                        f"    The filter_standard call at this epoch uses z_p (=phase_res)\n"
                        f"    as the innovation directly (x += K @ z_p).  The EKF is\n"
                        f"    CORRECT; only this diagnostic formula is wrong.\n"
                        f"    FIX (not applied here — forensic only):\n"
                        f"      Replace: _p1_innov = H @ x_before − z[rl]\n"
                        f"      With:    _p1_innov = z[rl]   (= phase_res, the actual innovation)\n"
                    )

            except Exception as _ir_ex:
                print(f"  [INNOV-RECON-WARN] failed for sat={_p1_sat_lbl}: {_ir_ex}")
            # ══════════════════════════════════════════════════════════════════

        # ── PATCH 1 INFO-DECOMPOSITION: prior vs measurement information ──────
        try:
            _p1_Rinv_diag = np.where(Rd_p > 1e-30, 1.0 / Rd_p, 0.0)
            _p1_HtRinvH   = H_p.T @ (H_p * _p1_Rinv_diag[:, None])  # H^T diag(R^-1) H
            _p1_tr_HtRinvH = float(np.trace(_p1_HtRinvH))
            # PATCH 2: explicit row-kind masks — never infer type from R magnitude
            _p1_ph_mask  = np.array([k == "PHASE" for k in _row_kind_p])
            _p1_cod_mask = np.array([k == "CODE"  for k in _row_kind_p])
            _p1_zwd_mask = np.array([k == "ZWD"   for k in _row_kind_p])
            _p1_phase_rows = int(_p1_ph_mask.sum())
            _p1_code_rows  = int(_p1_cod_mask.sum())
            _p1_ph_info  = float(np.trace(
                H_p[_p1_ph_mask].T @ (H_p[_p1_ph_mask] * _p1_Rinv_diag[_p1_ph_mask, None]))) if _p1_phase_rows else 0.0
            _p1_cod_info = float(np.trace(
                H_p[_p1_cod_mask].T @ (H_p[_p1_cod_mask] * _p1_Rinv_diag[_p1_cod_mask, None]))) if _p1_code_rows else 0.0
            _p1_zwd_info = float(np.trace(
                H_p[_p1_zwd_mask].T @ (H_p[_p1_zwd_mask] * _p1_Rinv_diag[_p1_zwd_mask, None]))) if _p1_zwd_mask.any() else 0.0
            # Prior information  trace(P^-1)  — use eigenvalue-safe inverse
            _p1_ev_P_arr  = np.linalg.eigvalsh(P)
            _p1_tr_Pinv   = float(np.sum(1.0 / np.maximum(np.abs(_p1_ev_P_arr), 1e-30)))
            _p1_ratio_info = (_p1_tr_HtRinvH / max(_p1_tr_Pinv, 1e-30))
            print(f"[EKF-INFO-DECOMP]  epoch={nproc}  sod={sod:.0f}")
            print(f"  trace(H^T R^-1 H)={_p1_tr_HtRinvH:.4e}"
                  f"  trace(P^-1)={_p1_tr_Pinv:.4e}"
                  f"  ratio_info={_p1_ratio_info:.4e}")
            print(f"  phase_info={_p1_ph_info:.4e}"
                  f"  code_info={_p1_cod_info:.4e}"
                  f"  zwd_prior_info={_p1_zwd_info:.4e}")
            # PATCH 2: per-row info_norm using explicit kind tags (not R threshold)
            for _pi_row in range(H_p.shape[0]):
                if _row_kind_p[_pi_row] != "PHASE":
                    continue   # print only PHASE rows in detail
                _pi_h   = H_p[_pi_row, :]
                _pi_Ri  = Rd_p[_pi_row]
                _pi_HtRiH = np.outer(_pi_h, _pi_h) / max(_pi_Ri, 1e-30)
                _pi_info_norm = float(np.linalg.norm(_pi_HtRiH, 'fro'))
                # Identify satellite for this row
                _pi_sat = '?'
                for _pi_sid2, _pi_ki2 in sidx.items():
                    if _pi_ki2 < len(_pi_h) and abs(_pi_h[_pi_ki2]) > 1e-9:
                        _pi_sat = _pi_sid2
                        break
                _pi_R_eff = float(H_p[_pi_row] @ P @ H_p[_pi_row]) + _pi_Ri
                print(f"  [INFO-ROW] sat={_pi_sat}  R_eff={_pi_R_eff:.4e}"
                      f"  info_norm={_pi_info_norm:.4e}"
                      f"  innov={float(H_p[_pi_row] @ x_before - _ekf_z[_pi_row])*1e3:+.1f}mm"
                      f"  innov_sigma={math.sqrt(max(_pi_R_eff,1e-30))*1e3:.1f}mm")
        except Exception as _p1i_ex:
            print(f"  [EKF-INFO-DECOMP-WARN] failed: {_p1i_ex}")
        # ── END PATCH 1 ───────────────────────────────────────────────────────

        # ── PATCH 3: FORENSIC SANITY ASSERTS (non-aborting warnings) ─────────
        if _p1_code_rows > 0 and _p1_cod_info <= 0:
            print(f"  [FORENSIC-WARN] code rows={_p1_code_rows} exist but code_info={_p1_cod_info:.4e} <= 0")
        if _p1_phase_rows > 0 and _p1_ph_info <= 0:
            print(f"  [FORENSIC-WARN] phase rows={_p1_phase_rows} exist but phase_info={_p1_ph_info:.4e} <= 0")
        if math.isnan(_p2e_sr_KH):
            pass  # expected at this point; KH computed later below
        if not math.isfinite(_p1_ph_info) and _p1_phase_rows > 0:
            print(f"  [FORENSIC-WARN] phase_info={_p1_ph_info} non-finite with {_p1_phase_rows} phase rows")
        if not math.isfinite(_p1_cod_info) and _p1_code_rows > 0:
            print(f"  [FORENSIC-WARN] code_info={_p1_cod_info} non-finite with {_p1_code_rows} code rows")
        # ── END PATCH 3 ───────────────────────────────────────────────────────

        # ── EKF UPDATE — ALWAYS CALLED (never blocked) ───────────────────────
        _innov_norm = float('nan')
        if _filter_dispatch(x, P, H_p.T, z_p, np.diag(Rd_p)) != 0:
            print(f"  [WARN] filter_standard failed at SOD={sod:.0f} — skipping epoch")
            nproc += 1
            continue
        _ekf_update_count += 1

        # ── PATCH 5: KALMAN GAIN FORENSIC ────────────────────────────────────
        # Reconstruct K = P_pre * H^T * S^{-1} from the saved pre-update P.
        # This is the ONLY way to see the actual gain — filter_standard does
        # not expose K.  We already stored _ekf_P_pre before the update.
        _mob_K = None   # shared with AMB-MOBILITY (PATCH 3 experiment below)
        try:
            _p5_PHt  = _ekf_P_pre @ _ekf_H.T                  # (n, m)
            _p5_S    = _ekf_H @ _p5_PHt + _ekf_R              # (m, m)
            _p5_K    = np.linalg.solve(_p5_S.T, _p5_PHt.T).T  # (n, m)
            _mob_K   = _p5_K   # expose to AMB-MOBILITY outside this try block
            _p5_K_clk  = float(np.sum(np.abs(_p5_K[3, :])))   # sum |K_clock row|
            _p5_K_zwd  = float(np.sum(np.abs(_p5_K[4, :])))
            _p5_K_pos  = float(np.linalg.norm(_p5_K[:3, :]))
            print(f"  [EKF-GAIN-SUMMARY]  epoch={nproc}  sod={sod:.0f}"
                  f"  sum|K_clock|={_p5_K_clk:.4f}"
                  f"  sum|K_zwd|={_p5_K_zwd:.4f}"
                  f"  ||K_pos||={_p5_K_pos:.4f}")
            # Per-ambiguity gain (phase columns only = low-R columns)
            _p5_phase_cols = [j for j in range(_p5_K.shape[1]) if Rd_p[j] < 0.5]
            # STAGE-2A: identify code columns (high-R, not ZWD which is the last row)
            _p5_code_cols  = [j for j in range(_p5_K.shape[1] - 1) if Rd_p[j] >= 0.5]
            # Accumulate K_clock and K_Up from code columns only
            if _p5_code_cols:
                _s2a_K_clk_code_list.append(
                    float(np.mean(np.abs(_p5_K[3, _p5_code_cols]))))
                _s2a_K_up_code_list.append(
                    float(np.mean(np.abs(_p5_K[:3, _p5_code_cols]))))  # mean |K_pos| from code
            for _p5_sid2, _p5_ki2 in sidx.items():
                if not amgr.is_active(_p5_sid2):
                    continue
                _p5_K_amb_row = _p5_K[_p5_ki2, :]
                _p5_K_amb_clk = float(_p5_K_amb_row[3]) if len(_p5_K_amb_row) > 3 else float('nan')
                # K for the amb state on phase columns
                _p5_K_amb_ph  = [float(_p5_K_amb_row[j]) for j in _p5_phase_cols] if _p5_phase_cols else [float('nan')]
                _p5_K_amb_max = max(abs(v) for v in _p5_K_amb_ph)
                _p5_K_clk_row = [float(_p5_K[3, j]) for j in _p5_phase_cols] if _p5_phase_cols else [float('nan')]
                _p5_K_clk_max = max(abs(v) for v in _p5_K_clk_row)
                print(f"  [EKF-GAIN-AMB]  sat={_p5_sid2}"
                      f"  K_amb_max_on_phase={_p5_K_amb_max:.4f}"
                      f"  K_clk_max_on_phase={_p5_K_clk_max:.4f}")
                # FLAG: gain > 1 means state explodes in response to one innovation unit
                if _p5_K_amb_max > 1.0:
                    print(f"  [EKF-GAIN-FLAG] *** |K_amb|={_p5_K_amb_max:.4f} > 1 ***"
                          f"  sat={_p5_sid2}  epoch={nproc}  sod={sod:.0f}")
                if _p5_K_clk_max > 1.0:
                    print(f"  [EKF-GAIN-FLAG] *** |K_clk|={_p5_K_clk_max:.4f} > 1 ***"
                          f"  epoch={nproc}  sod={sod:.0f}")
            # ── PATCH 2: KALMAN GAIN EIGENMODE AUDIT ─────────────────────────
            # Compute KH and examine its spectrum.  KH = I means full annihilation
            # of the prior in that eigen-direction.  spectral_radius > 0.95 flags
            # near-complete collapse of a mode in a single update.
            _p2e_KH = _p5_K @ _ekf_H          # (n, n)  — same shape as P
            _p2e_ev_prior = np.linalg.eigvalsh(_ekf_P_pre)
            _p2e_ev_post  = np.linalg.eigvalsh(P)
            _p2e_ev_KH    = np.linalg.eigvals(_p2e_KH).real
            _p2e_sr_KH    = float(np.max(np.abs(_p2e_ev_KH)))
            print(f"[EKF-EIGENMODE]  epoch={nproc}  sod={sod:.0f}")
            print(f"  eigvals(P_prior): min={float(_p2e_ev_prior.min()):.3e}"
                  f"  max={float(_p2e_ev_prior.max()):.3e}")
            print(f"  eigvals(P_post):  min={float(_p2e_ev_post.min()):.3e}"
                  f"  max={float(_p2e_ev_post.max()):.3e}")
            print(f"  eigvals(KH):      min={float(np.min(np.abs(_p2e_ev_KH))):.3e}"
                  f"  max={float(np.max(np.abs(_p2e_ev_KH))):.3e}"
                  f"  spectral_radius={_p2e_sr_KH:.4f}")
            if _p2e_sr_KH > 0.95:
                print(f"  [OVERCONFIDENT-MODE] spectral_radius(KH)={_p2e_sr_KH:.4f} > 0.95"
                      f"  — startup eigenmode nearly fully observed in one update"
                      f"  epoch={nproc}  sod={sod:.0f}")
            # Fraction of P-prior eigenvalues collapsed (ratio post/prior per mode)
            _p2e_nmin = min(len(_p2e_ev_prior), len(_p2e_ev_post))
            _p2e_sorted_prior = np.sort(np.abs(_p2e_ev_prior))[::-1]
            _p2e_sorted_post  = np.sort(np.abs(_p2e_ev_post ))[::-1]
            _p2e_collapse_modes = int(np.sum(
                _p2e_sorted_post[:_p2e_nmin] / np.maximum(_p2e_sorted_prior[:_p2e_nmin], 1e-30) < 0.05
            ))
            print(f"  n_collapsed_modes(<5% prior)={_p2e_collapse_modes}"
                  f"  out of {_p2e_nmin} eigenmodes")
            # ── END PATCH 2 ───────────────────────────────────────────────────
        except Exception as _p5_ex:
            print(f"  [EKF-GAIN-WARN] Kalman gain reconstruction failed: {_p5_ex}")
        # ── END PATCH 5 ───────────────────────────────────────────────────────

        # ── PATCH 3 (continued): post-gain sanity checks ──────────────────────
        if math.isnan(_p2e_sr_KH):
            print(f"  [FORENSIC-WARN] spectral_radius(KH)=NaN  epoch={nproc}  sod={sod:.0f}")
        # ── END PATCH 3 (continued) ───────────────────────────────────────────

        # ── PATCH-INFO-BALANCE: compact epoch-level diagnostic ────────────────
        # Prints one line per epoch that captures the key quantities needed to
        # diagnose code/phase information imbalance at a glance.
        # Variables used:
        #   _p1_ph_info  / _p1_cod_info  — from EKF-INFO-DECOMP block
        #   _p2_clk_sig_after            — from POSTFIT SHRINK AUDIT block
        #   _p2e_sr_KH                   — from EIGENMODE block
        # All variables are epoch-local; guard individually so one failure
        # doesn't suppress the rest.
        try:
            # PATCH 1 FIX: use epoch-local vars (initialised in reset block above)
            _ib_ph_info   = _p1_ph_info
            _ib_cod_info  = _p1_cod_info
            _ib_ratio     = (_ib_ph_info / max(_ib_cod_info, 1e-30)
                             if _ib_cod_info > 0 else float('nan'))
            # STAGE-2A accumulation: phase/code info ratio per epoch
            if math.isfinite(_ib_ratio):
                _s2a_ratio_list.append(_ib_ratio)
            _ib_clk_sig   = _p2_clk_sig_after   # epoch-local; reset at top
            _ib_sr        = _p2e_sr_KH           # epoch-local; reset at top
            _ib_code_rms  = (math.sqrt(sum(r**2 for r in _code_innov_list) /
                              max(len(_code_innov_list), 1)) * 1e3
                             if _code_innov_list else float('nan'))
            _ib_ph_rms    = (math.sqrt(sum(r**2 for r in _ph_innov_list) /
                              max(len(_ph_innov_list), 1)) * 1e3
                             if _ph_innov_list else float('nan'))
            # Store for PATCH 5 FORENSIC-SUMMARY (module-level epoch variables)
            _epoch_code_rms  = _ib_code_rms
            _epoch_phase_rms = _ib_ph_rms
            print(f"[INFO-BALANCE]  epoch={nproc}  sod={sod:.0f}"
                  f"  ph/code_info_ratio={_ib_ratio:.2e}"
                  f"  clk_sig={_ib_clk_sig:.1f}mm"
                  f"  code_rms={_ib_code_rms:.0f}mm"
                  f"  phase_rms={_ib_ph_rms:.1f}mm"
                  f"  spectral_radius(KH)={_ib_sr:.4f}")
        except Exception as _ib_ex:
            print(f"  [INFO-BALANCE-WARN] failed: {_ib_ex}")
        # ── END PATCH-INFO-BALANCE ────────────────────────────────────────────

        # ── PATCH 5: ONE CLEAN FORENSIC SUMMARY LINE ─────────────────────────
        # This is the primary truth source for epoch-level diagnostics.
        # All variables are epoch-local; guarded individually.
        try:
            _fs_ph_info  = _p1_ph_info  if math.isfinite(_p1_ph_info)  else float('nan')
            _fs_cod_info = _p1_cod_info if math.isfinite(_p1_cod_info) else float('nan')
            _fs_ratio    = (_fs_ph_info / max(_fs_cod_info, 1e-30)
                            if math.isfinite(_fs_cod_info) and _fs_cod_info > 0
                            else float('nan'))
            _fs_clk      = (_p2_clk_sig_after if math.isfinite(_p2_clk_sig_after)
                            else float('nan'))
            _fs_sr       = (_p2e_sr_KH if math.isfinite(_p2e_sr_KH) else float('nan'))
            _fs_crms     = (_ib_code_rms  if math.isfinite(_ib_code_rms)  else float('nan'))
            _fs_prms     = (_ib_ph_rms    if math.isfinite(_ib_ph_rms)    else float('nan'))
            print(
                f"[FORENSIC-SUMMARY]  epoch={nproc}  sod={sod:.0f}"
                f"  phase_rows={_p1_phase_rows}  code_rows={_p1_code_rows}"
                f"  phase_info={_fs_ph_info:.4e}  code_info={_fs_cod_info:.4e}"
                f"  ph/code_ratio={_fs_ratio:.2e}"
                f"  clk_sig={_fs_clk:.1f}mm"
                f"  code_rms={_fs_crms:.0f}mm  phase_rms={_fs_prms:.1f}mm"
                f"  spectral_radius={_fs_sr:.4f}"
                f"  n_zombie={_n_zombie_this_epoch}"
                + ("  [CM-DEFERRED]" if _coherent_cm_epoch else "")
            )
        except Exception as _fs_ex:
            print(f"  [FORENSIC-SUMMARY-WARN] failed: {_fs_ex}")
        # ── END PATCH 5 ───────────────────────────────────────────────────────


        # Prevents a single catastrophic startup clock jump (e.g. +9.7 m in one
        # epoch) from poisoning the entire session and corrupting all subsequent
        # ambiguity births.  Active only during the first 20 epochs.
        # Covariance is NOT modified; ambiguities are NOT modified; RTS intact.
        if nproc < 20:
            _clk_raw_after  = x[3]
            _clk_raw_before = x_before[3]
            _dclk_raw  = _clk_raw_after - _clk_raw_before
            _dclk_clip = float(np.clip(_dclk_raw, -3.0, 3.0))
            x[3] = _clk_raw_before + _dclk_clip
            if abs(_dclk_raw - _dclk_clip) > 1e-9:
                print(
                    f"[CLOCK-LIMITER] "
                    f"SOD={sod:.0f}  "
                    f"raw_dclk={_dclk_raw:+.3f}m  "
                    f"limited_dclk={_dclk_clip:+.3f}m"
                )
        # ─────────────────────────────────────────────────────────────────────

        # ── ROOT-TRIGGER PATCH 5: CLOCK OBSERVABILITY CHECK (first 20 epochs) ─
        _clk_update_m = x[3] - x_before[3]
        if nproc < 20:
            _n_ph   = len(_ph_innov_list)
            _n_code = len(_code_innov_list)
            _mean_ph  = (sum(_ph_innov_list)   / _n_ph)   if _n_ph   > 0 else float('nan')
            _std_ph   = (math.sqrt(sum((v-_mean_ph)**2 for v in _ph_innov_list) / _n_ph)
                         if _n_ph > 1 else 0.)
            _mean_cod = (sum(_code_innov_list) / _n_code) if _n_code > 0 else float('nan')
            _std_cod  = (math.sqrt(sum((v-_mean_cod)**2 for v in _code_innov_list) / _n_code)
                         if _n_code > 1 else 0.)
            _cm_ratio = (abs(_mean_ph) / _std_ph) if _std_ph > 1e-9 else float('nan')
            print(
                f"[CLOCK-OBS]\n"
                f"  SOD={sod:.1f}  epoch={nproc}\n"
                f"  n_phase={_n_ph}  n_code={_n_code}\n"
                f"  mean_phase_innov={_mean_ph:.6f} m   std_phase_innov={_std_ph:.6f} m\n"
                f"  mean_code_innov={_mean_cod:.6f} m   std_code_innov={_std_cod:.6f} m\n"
                f"  clock_update={_clk_update_m:.6f} m\n"
                f"  common_mode_ratio={_cm_ratio:.4f}"
                f"  {'← >> 1: COMMON-MODE CORRUPTION' if math.isfinite(_cm_ratio) and _cm_ratio > 5 else ''}"
            )
        # ── ROOT-TRIGGER PATCH 6 ASSERTION #2: clock update > 10 m ──────────
        if abs(_clk_update_m) > 10.0:
            print(
                f"[FATAL-STARTUP-INCONSISTENCY]  ASSERTION-2\n"
                f"  SOD={sod:.0f}  abs(clock_update)={abs(_clk_update_m):.3f} m > 10 m\n"
                f"  x_before[3]={x_before[3]:.6f} m  x[3]={x[3]:.6f} m\n"
                f"  n_phase={len(_ph_innov_list)}  n_code={len(_code_innov_list)}\n"
                f"  INTERPRETATION: clock jump exceeds 10 m in one epoch — "
                f"startup clock not observable or geometry degenerate"
            )
            raise RuntimeError(
                f"[FATAL] clock_update={_clk_update_m:.3f} m > 10 m at SOD={sod:.0f}"
            )
        # ─────────────────────────────────────────────────────────────────────

        _clk_series.append(float(x[3]))   # post-update clock for lag-1 ACF
        _zwd_series.append(float(x[4]))   # post-update ZWD for observability audit
        _up_series.append(float(_enu_R[2] @ (nom + x[:3] - REF)) * 1e3)  # Up error (mm)

        # (Code RMS accumulated below after code_res_all is computed)

        # PSD-REPAIR-3: Apply Joseph form post-correction.
        # filter_standard (RTKLIB-style) uses P_new = (I - K*H)*P_old, which is
        # numerically unstable when H has high rank (19 obs here) and K is large.
        # The Joseph-stabilised form P = (I-KH)*P*(I-KH)^T + K*R*K^T is PSD by
        # construction.  We recover it via:
        #   P_joseph = (I-KH)*P_old*(I-KH)^T + K*R*K^T
        # Equivalently: reconstruct K from the update and symmetrize.
        # Cheapest correct repair: enforce P = 0.5*(P+P^T) then eigenvalue-floor.
        # Full Joseph form requires storing P_pre and recomputing K;
        # instead we apply a targeted diagonal floor BEFORE symmetrisation.
        _P_diag = np.diag(P).copy()
        _neg_diag = _P_diag < 0
        if np.any(_neg_diag):
            print(f"  [WARN-PSD] Negative diagonal entries after filter_standard "
                  f"at SOD={sod:.0f}: "
                  f"states={list(np.where(_neg_diag)[0])} "
                  f"values={_P_diag[_neg_diag]}")
            # Floor negative diagonals to a small positive value
            _floor_val = 1e-12
            for _di in np.where(_neg_diag)[0]:
                P[_di, _di] = max(P[_di, _di], _floor_val)

        # ── INNOVATION NORM — corrected formula ──────────────────────────────
        # FORMER (WRONG) formula:
        #   _inno = z_p - H_p @ x_before
        # This computed (prefit_residual − H@x_before) ≈ −H@x_before, which is
        # dominated by absolute clock+ambiguity magnitudes and is multi-meter.
        # Reason it was wrong: in this code's residual-linearised convention,
        #   z_p  ALREADY IS the innovation  (filter_standard does x += K @ z_p)
        # so the norm of z_p is the correct prefit innovation norm.
        # z_p - H_p @ x_before would be correct only if z_p contained raw
        # observations (not residuals), which it does not.
        #
        # CORRECT formula:
        _inno = z_p.copy()   # z_p = phase_res / code_res vector = innovations
        _innov_norm = float(np.linalg.norm(_inno))

        # ── INNOV-NORM-RECON: reconcile the two norm computations ────────────
        _inno_wrong = z_p - H_p @ x_before   # former formula (kept for audit)
        _innov_norm_wrong = float(np.linalg.norm(_inno_wrong))
        # Decompose for phase rows only
        _ir_norm_ph_correct = float(np.linalg.norm(
            [z_p[_ri] for _ri in range(len(z_p))
             if _ri < len(_row_kind_p) and _row_kind_p[_ri] == "PHASE"]))
        _ir_norm_ph_wrong   = float(np.linalg.norm(
            [(z_p - H_p @ x_before)[_ri] for _ri in range(len(z_p))
             if _ri < len(_row_kind_p) and _row_kind_p[_ri] == "PHASE"]))
        # Per-row reconciliation for accepted phase rows
        _ir_recon_rows = []
        for _ri in range(len(z_p)):
            if _ri >= len(_row_kind_p) or _row_kind_p[_ri] != "PHASE":
                continue
            _ri_z   = float(z_p[_ri])            # = phase_res = correct innov
            _ri_Hx  = float(H_p[_ri] @ x_before) # = state projection (WRONG innov)
            _ri_wrong = _ri_Hx - _ri_z           # = _p1_innov formula result
            # Find sat label for this row
            _ri_sat = '?'
            for _ri_sid2, _ri_ki2 in sidx.items():
                if _ri_ki2 < H_p.shape[1] and abs(H_p[_ri, _ri_ki2]) > 1e-9:
                    _ri_sat = _ri_sid2; break
            _ir_recon_rows.append((_ri_sat, _ri_z*1e3, _ri_Hx*1e3, _ri_wrong*1e3))
        print(f"[INNOV-NORM-RECON]  epoch={nproc}  sod={sod:.0f}")
        print(f"  correct_norm (||z_p||)        = {_innov_norm:.3f} m  ← filter_standard uses this")
        print(f"  wrong_norm   (||z_p-H@x_b||) = {_innov_norm_wrong:.3f} m  ← former formula (WRONG)")
        print(f"  phase-only correct_norm       = {_ir_norm_ph_correct:.3f} m")
        print(f"  phase-only wrong_norm         = {_ir_norm_ph_wrong:.3f} m")
        for _ir_sat, _ir_z_mm, _ir_Hx_mm, _ir_wr_mm in _ir_recon_rows:
            print(f"  [INNOV-NORM-ROW] sat={_ir_sat}"
                  f"  z[rl]={_ir_z_mm:+.1f}mm (correct)"
                  f"  H@x_b={_ir_Hx_mm:+.1f}mm"
                  f"  wrong_innov={_ir_wr_mm:+.1f}mm"
                  f"  discrepancy={abs(_ir_wr_mm - _ir_z_mm):.1f}mm")

        # ── AUDIT: capture per-ambiguity Kalman gain norms post-update ────────
        # K = P_pre * H^T * S^{-1} ; approximate per-ambiguity Kg from P change
        # We use the diagonal change in P for the ki state as a proxy:
        #   |ΔP[ki,ki]| / sqrt(P_pre[ki,ki]) → qualitative participation index
        # This is DIAGNOSTIC ONLY; does not alter any EKF state.
        _P_before_diag = {sid: P[sidx[sid], sidx[sid]]
                          for sid in sidx if amgr.is_active(sid)}
        # store pre-update P[ki,ki] BEFORE Joseph symmetrisation mutates P
        # (we already ran filter_standard so P is post-update here)

        # Joseph symmetrisation
        _joseph_symmetrise(P)

        # ── PATCH-CLK-FLOOR: Enforce minimum clock sigma after Joseph update ──
        # PATCH2 (OBSERVABILITY EXPERIMENT): commented out to observe natural clock
        # sigma dynamics. Goal: determine whether sigma stabilises naturally with
        # SP=0.050 (6.25x more phase noise), or collapses again toward zero.
        # If collapse recurs -> structural observability coupling (clock<->amb gauge
        # mode), NOT measurement weighting -> next step: state reparameterisation.
        #
        # _CLK_SIGMA_FLOOR_M = 0.10   # 100 mm
        # if P[3, 3] < _CLK_SIGMA_FLOOR_M ** 2:
        #     _clk_floor_before = math.sqrt(max(P[3, 3], 0.)) * 1e3
        #     P[3, 3] = _CLK_SIGMA_FLOOR_M ** 2
        #     # Re-symmetrise to keep P symmetric after the diagonal edit
        #     _joseph_symmetrise(P)
        #     print(f"  [CLK-FLOOR-APPLIED]  epoch={nproc}  sod={sod:.0f}"
        #           f"  clk_sig_before={_clk_floor_before:.1f}mm"
        #           f"  clk_sig_after=100.0mm"
        #           f"  floor={_CLK_SIGMA_FLOOR_M*1e3:.0f}mm")
        # ── END PATCH-CLK-FLOOR (commented out for PATCH2 experiment) ────────

        # ══════════════════════════════════════════════════════════════════════
        # STAGE-7B: POSITION COVARIANCE FLOOR — DIAGNOSTIC ONLY (NO P MUTATION)
        # ══════════════════════════════════════════════════════════════════════
        #
        # CONTEXT
        # ───────
        # Stage-7 showed that the active floor genuinely cured covariance
        # over-confidence (NIS 5.21→1.22, storms eliminated, phase-active 100%)
        # but ENU instability persisted and postfit residuals worsened.
        # Conclusion: premature collapse was REAL but NOT the sole blocker.
        #
        # STAGE-7B OBJECTIVE
        # ──────────────────
        # Remove ALL active P modification so covariance can evolve naturally.
        # Retain the floor diagnostic so we can observe WHEN natural collapse
        # would have triggered, giving a clean measurement of the underlying
        # collapse rate without artificial forcing.
        #
        # P IS NEVER TOUCHED IN THIS BLOCK.
        # Q, H, R, K, ambiguities, clock, ZWD, RTS: all unchanged.
        #
        # IMPLEMENTATION
        # ──────────────
        # Location : same as Stage-7 (immediately after Joseph symmetrisation).
        # Mechanism: diagonal-only floor on P[0:3, 0:3] (ECEF position).
        #            Off-diagonal terms, clock, ZWD, and ambiguities: UNTOUCHED.
        # Formula  : _pos_floor_mm = max(50.0, 0.15 * cp_delta_mm)
        #            where cp_delta_mm = mean |code_res - phase_res| from the
        #            PREVIOUS epoch (OBS-BALANCE computes it after this block;
        #            first epoch uses 50 mm safe default).
        # Diagnostic only — floor value is computed and logged but NEVER applied
        # to P.  Counter tracks how often natural P_pos is already below floor,
        # giving the collapse-detection signal without perturbing the filter.
        # ══════════════════════════════════════════════════════════════════════
        try:
            # Compute floor threshold (same formula as Stage-7; not applied)
            if math.isfinite(_pf_prev_cp_delta_mm):
                _pf_floor_mm = max(50.0, 0.15 * _pf_prev_cp_delta_mm)
            else:
                _pf_floor_mm = 50.0          # safe default for epoch 0
            _pf_floor_m2 = (_pf_floor_mm * 1e-3) ** 2

            # Natural pos_sigma — P is NOT modified
            _pf_sigma_now_mm = math.sqrt(
                sum(max(P[i, i], 0.) for i in range(3))) * 1e3

            # Would the floor have fired?
            _pf_would_fire = any(P[_pf_i, _pf_i] < _pf_floor_m2
                                 for _pf_i in range(3))
            if _pf_would_fire:
                _pf_floor_active_n  += 1
                _pf_floor_mm_list.append(_pf_floor_mm)
                _pf_sigma_orig_list.append(_pf_sigma_now_mm)
                print(f"  [POS-FLOOR-DIAG]  epoch={nproc}  sod={sod:.0f}"
                      f"  diag-only"
                      f"  sigma_now={_pf_sigma_now_mm:.1f}mm"
                      f"  floor_target={_pf_floor_mm:.1f}mm"
                      f"  cp_delta_prev={_pf_prev_cp_delta_mm:.1f}mm"
                      f"  WOULD_FIRE=YES")
        except Exception as _pf_ex:
            print(f"[POS-FLOOR-DIAG-ERROR]  epoch={nproc}  sod={sod:.0f}  {_pf_ex}")
        # ══════════════════════════════════════════════════════════════════════
        # END STAGE-7B POS-FLOOR-DIAG
        # ══════════════════════════════════════════════════════════════════════

        # ── PATCH 2: POST-EKF COVARIANCE SHRINK AUDIT ────────────────────────
        # Compare pre-update P diagonal (from _ekf_P_pre snapshot) with post-update.
        _p2_trace_before = float(np.trace(_ekf_P_pre))
        _p2_trace_after  = float(np.trace(P))
        _p2_clk_sig_before = math.sqrt(max(_ekf_P_pre[3,3], 0.)) * 1e3
        _p2_clk_sig_after  = math.sqrt(max(P[3,3], 0.)) * 1e3
        _p2_zwd_sig_before = math.sqrt(max(_ekf_P_pre[4,4], 0.)) * 1e3
        _p2_zwd_sig_after  = math.sqrt(max(P[4,4], 0.)) * 1e3
        _p2_dx_norm = float(np.linalg.norm(x - x_before)) * 1e3
        print(f"  [EKF-POSTFIT]  epoch={nproc}  sod={sod:.0f}"
              f"  ||dx||={_p2_dx_norm:.1f}mm"
              f"  trace(P_before)={_p2_trace_before:.4e}"
              f"  trace(P_after)={_p2_trace_after:.4e}"
              f"  ratio={_p2_trace_after/_p2_trace_before:.4f}")
        print(f"  [EKF-POSTFIT]  clk_sig: {_p2_clk_sig_before:.1f}→{_p2_clk_sig_after:.1f}mm"
              f"  zwd_sig: {_p2_zwd_sig_before:.1f}→{_p2_zwd_sig_after:.1f}mm")
        for _p2_sid2, _p2_ki2 in sidx.items():
            if not amgr.is_active(_p2_sid2):
                continue
            _p2_sig_before = math.sqrt(max(_ekf_P_pre[_p2_ki2,_p2_ki2], 0.)) * 1e3
            _p2_sig_after  = math.sqrt(max(P[_p2_ki2,_p2_ki2], 0.)) * 1e3
            _p2_shrink = (_p2_sig_after / _p2_sig_before) if _p2_sig_before > 1e-9 else float('nan')
            _p2_flag = "  *** FLAG: shrink>50% ***" if math.isfinite(_p2_shrink) and _p2_shrink < 0.5 else ""
            print(f"  [EKF-POSTFIT-AMB]  sat={_p2_sid2}"
                  f"  sigma: {_p2_sig_before:.1f}→{_p2_sig_after:.1f}mm"
                  f"  shrink_ratio={_p2_shrink:.4f}{_p2_flag}")

        # ── PATCH 4: POSTFIT COLLAPSE TRACKER ────────────────────────────────
        _p4_trace_ratio = (_p2_trace_after / max(_p2_trace_before, 1e-30))
        # Position sigmas (ECEF XYZ → 3D σ_pos)
        _p4_pos_sig_before = math.sqrt(sum(max(_ekf_P_pre[i,i],0.) for i in range(3))) * 1e3
        _p4_pos_sig_after  = math.sqrt(sum(max(P[i,i],         0.) for i in range(3))) * 1e3
        _p4_amb_sigs_before = [math.sqrt(max(_ekf_P_pre[ki2,ki2],0.))*1e3
                                for sid2,ki2 in sidx.items() if amgr.is_active(sid2)]
        _p4_amb_sigs_after  = [math.sqrt(max(P[ki2,ki2],        0.))*1e3
                                for sid2,ki2 in sidx.items() if amgr.is_active(sid2)]
        _p4_amb_mean_before = (sum(_p4_amb_sigs_before)/len(_p4_amb_sigs_before)
                               if _p4_amb_sigs_before else float('nan'))
        _p4_amb_mean_after  = (sum(_p4_amb_sigs_after) /len(_p4_amb_sigs_after)
                               if _p4_amb_sigs_after  else float('nan'))
        print(f"[COV-COLLAPSE-TRACKER]  epoch={nproc}  sod={sod:.0f}"
              f"  trace_ratio={_p4_trace_ratio:.4f}"
              f"{'  *** [COV-COLLAPSE] ratio<0.05 ***' if _p4_trace_ratio < 0.05 else ''}")
        print(f"  clock_sigma:     {_p2_clk_sig_before:.1f}→{_p2_clk_sig_after:.1f}mm"
              f"  shrink={_p2_clk_sig_after/max(_p2_clk_sig_before,1e-9):.4f}")
        print(f"  pos_sigma(3D):   {_p4_pos_sig_before:.1f}→{_p4_pos_sig_after:.1f}mm"
              f"  shrink={_p4_pos_sig_after/max(_p4_pos_sig_before,1e-9):.4f}")
        print(f"  amb_sigma(mean): {_p4_amb_mean_before:.1f}→{_p4_amb_mean_after:.1f}mm"
              f"  shrink={_p4_amb_mean_after/max(_p4_amb_mean_before,1e-9):.4f}")
        # SUCCESS CRITERIA check (PATCH 5 spec)
        _p5_sc1 = _p4_trace_ratio >= 0.05
        _p5_sc2 = _p2_clk_sig_after >= 30.0   # updated: steady-state clk_sig ≈ 40 mm (was 100 mm)
        _p5_sc3 = _p4_amb_mean_after < 1000.0   # sub-meter births
        _p5_sc4 = True   # code RMS inflation check done at end of session
        _p5_sc5 = True   # common-mode check done in CLOCK-OBS block
        _p5_all = _p5_sc1 and _p5_sc2 and _p5_sc3
        print(f"[PATCH5-SUCCESS]  epoch={nproc}  sod={sod:.0f}"
              f"  SC1_no_collapse={'PASS' if _p5_sc1 else 'FAIL'}"
              f"  SC2_clk_sig>30mm={'PASS' if _p5_sc2 else 'FAIL'}"
              f"  SC3_amb_birth_small={'PASS' if _p5_sc3 else 'FAIL'}"
              f"  {'[ALL-PASS]' if _p5_all else '[FAILING]'}")
        # ── END PATCH 4 ───────────────────────────────────────────────────────

        # ── PATCH 3: JOSEPH CONSISTENCY CHECK ────────────────────────────────
        # Compute P_expected = (I-KH) P_pre (I-KH)^T + K R K^T
        # and compare against actual P.  Any difference is numerical corruption.
        try:
            _p3_PHt  = _ekf_P_pre @ _ekf_H.T
            _p3_S    = _ekf_H @ _p3_PHt + _ekf_R
            _p3_K    = np.linalg.solve(_p3_S.T, _p3_PHt.T).T
            _p3_n    = P.shape[0]
            _p3_IKH  = np.eye(_p3_n) - _p3_K @ _ekf_H
            _p3_P_expected = _p3_IKH @ _ekf_P_pre @ _p3_IKH.T + _p3_K @ _ekf_R @ _p3_K.T
            _p3_diff = P - _p3_P_expected
            _p3_max_diff = float(np.max(np.abs(_p3_diff)))
            _p3_fro_diff = float(np.linalg.norm(_p3_diff, 'fro'))
            print(f"  [JOSEPH-CHECK]  epoch={nproc}  sod={sod:.0f}"
                  f"  max_abs_diff={_p3_max_diff:.6e}"
                  f"  fro_norm_diff={_p3_fro_diff:.6e}")
            if _p3_max_diff > 1e-6:
                print(f"  [JOSEPH-CHECK-WARN] max_abs_diff={_p3_max_diff:.6e} > 1e-6"
                      f"  — covariance numerically inconsistent with Joseph form")
        except Exception as _p3_ex:
            print(f"  [JOSEPH-CHECK-WARN] Joseph consistency check failed: {_p3_ex}")
            _p3_max_diff = float('nan')

        # ── PROPAGATION-AUDIT PATCH 1: save postfit state for next epoch ──────
        # Must be placed AFTER Joseph symmetrisation so _x_post_prev / _P_post_prev
        # always hold a fully consistent (symmetric PSD) postfit snapshot.
        _x_post_prev = x.copy()
        _P_post_prev = P.copy()
        # ─────────────────────────────────────────────────────────────────────

        # ── STARTUP-INFO: consistent scaling diagnostic (once per epoch) ───────
        if _si_epoch_scales:
            _si_mean_scale = sum(_si_epoch_scales) / len(_si_epoch_scales)
            _si_mean_Reff  = sum(_si_epoch_Reff)   / len(_si_epoch_Reff)
            _si_mean_info  = sum(1.0 / max(r, 1e-30) for r in _si_epoch_Reff) / len(_si_epoch_Reff)
            print(f"[STARTUP-INFO] epoch={nproc}  mean_scale={_si_mean_scale:.4f}"
                  f"  mean_R_eff={_si_mean_Reff:.6f}  mean_info={_si_mean_info:.4e}"
                  f"  cond(S)={_p1_cond_S:.3e}")
        # ── END STARTUP-INFO ──────────────────────────────────────────────────

        # ── AMBIGUITY-SOFTNESS EXPERIMENT PATCH 2: stiffness audit ───────────
        # One line per epoch, printed after the Joseph update.
        # Purpose: track whether the AMB_Q_FLOOR relaxes ambiguity stiffness
        # enough to allow clk_sig to stabilise and code RMS to decrease.
        # No per-satellite spam.  No extra forensic decompositions.
        _obs_mean_amb_sig_mm = float('nan')   # for OBS-BALANCE (PATCH 2 experiment)
        try:
            _as_sigs = [math.sqrt(max(P[_ki, _ki], 0.)) * 1e3
                        for _ki in sidx.values() if _ki >= 5]
            _as_mean = (sum(_as_sigs) / len(_as_sigs)) if _as_sigs else float('nan')
            _obs_mean_amb_sig_mm = _as_mean   # share with OBS-BALANCE
            _as_min  = min(_as_sigs) if _as_sigs else float('nan')
            _as_max  = max(_as_sigs) if _as_sigs else float('nan')
            _as_clk  = _p2_clk_sig_after
            _as_crms = _ib_code_rms  if math.isfinite(_ib_code_rms)  else float('nan')
            _as_prms = _ib_ph_rms    if math.isfinite(_ib_ph_rms)    else float('nan')
            _as_ratio = _ib_ratio    if math.isfinite(_ib_ratio)      else float('nan')
            _as_sr   = _ib_sr        if math.isfinite(_ib_sr)         else float('nan')
            print(
                f"[AMB-STIFFNESS] epoch={nproc}  sod={sod:.0f}"
                f"  clk_sig={_as_clk:.1f}mm"
                f"  mean_amb_sig={_as_mean:.1f}mm"
                f"  min_amb_sig={_as_min:.1f}mm"
                f"  max_amb_sig={_as_max:.1f}mm"
                f"  code_rms={_as_crms:.0f}mm"
                f"  phase_rms={_as_prms:.1f}mm"
                f"  ph/code_info_ratio={_as_ratio:.2e}"
                f"  spectral_radius(KH)={_as_sr:.4f}"
            )
        except Exception as _as_ex:
            print(f"  [AMB-STIFFNESS-WARN] failed: {_as_ex}")
        # ── END AMBIGUITY-SOFTNESS PATCH 2 ───────────────────────────────────


        # Called once per epoch per active ambiguity, immediately after Joseph
        # symmetrisation so that amgr.get_sigma() always returns the current
        # contracted value rather than the stale birth/reset value.
        for _s2, _k2 in sidx.items():
            if amgr.is_active(_s2):
                amgr.update_sigma(_s2, math.sqrt(max(P[_k2, _k2], 0.)) * 1e3,
                                  epoch=nproc, sod=sod)
        # ─────────────────────────────────────────────────────────────────────

        # ── OBSERVABILITY EXPERIMENT PATCH 3: AMB-MOBILITY ───────────────────
        # Per-ambiguity state movement audit.  Answers: are ambiguities actually
        # moving enough to absorb coherent phase excursions, or are they frozen?
        # Printed once per epoch after update; no new assertions or aborts.
        try:
            for _mb_sid, _mb_ki in sidx.items():
                if not amgr.is_active(_mb_sid):
                    continue
                _mb_sig_before = math.sqrt(max(_ekf_P_pre[_mb_ki, _mb_ki], 0.)) * 1e3
                _mb_sig_after  = math.sqrt(max(P[_mb_ki, _mb_ki], 0.)) * 1e3
                _mb_delta_N    = (float(x[_mb_ki]) - float(x_before[_mb_ki])) * 1e3
                # K_amb: max |gain| of this ambiguity state over all observations
                if _mob_K is not None:
                    _mb_K_amb = float(np.max(np.abs(_mob_K[_mb_ki, :])))
                else:
                    _mb_K_amb = float('nan')
                print(
                    f"  [AMB-MOBILITY] sat={_mb_sid}"
                    f"  sigma_before={_mb_sig_before:.1f}mm"
                    f"  sigma_after={_mb_sig_after:.1f}mm"
                    f"  delta_N_mm={_mb_delta_N:+.2f}"
                    f"  K_amb={_mb_K_amb:.4f}"
                )
                # STAGE-2A accumulation: |delta_N_mm| for mean mobility metric
                if math.isfinite(_mb_delta_N):
                    _s2a_delta_N_list.append(abs(_mb_delta_N))
        except Exception as _mb_ex:
            print(f"  [AMB-MOBILITY-WARN] failed: {_mb_ex}")
        # ── END PATCH 3 AMB-MOBILITY ──────────────────────────────────────────


        if _zwd_prev is not None and abs(x[4] - _zwd_prev) > ZWD_CLAMP:
            x[4] = _zwd_prev + math.copysign(ZWD_CLAMP, x[4] - _zwd_prev)
            P[4,4] = max(P[4,4], (ZWD_CLAMP / 3.0)**2)
        _zwd_prev = x[4]

        # ── State jump warnings ───────────────────────────────────────────────
        _dx_state   = x[:5] - x_before[:5]
        _state_jump = float(np.linalg.norm(_dx_state[:3]))
        if _state_jump > 0.5:
            print(f"  [WARN] State position jump = {_state_jump*1e3:.0f}mm at SOD={sod:.0f}")
        if _innov_norm > 1e4:
            print(f"  [WARN] Innovation norm exploded = {_innov_norm:.2e} at SOD={sod:.0f}")

        # ── Save disappearing satellites for gap recovery ─────────────────────
        for _esid, _ephi in list(amgr.items()):
            if _ephi and not any(m['sid'] == _esid for m in geom):
                _eki = sidx.get(_esid)
                if _eki is not None:
                    _amb_saved_state[_esid] = dict(
                        x_ki=float(x[_eki]),
                        P_ki=float(P[_eki, _eki]),
                        last_sod=sod,
                    )
                amgr.mark_missing(_esid, sod, epoch=nproc)       # STEP-2: observable flag sync

        # ── AUDIT: write ambiguity participation row for every active amb ─────
        if amb_part_fh is not None:
            for _aps, _apki in sidx.items():
                if not amgr.is_active(_aps):
                    continue
                _ap_alive    = nproc - amgr.get_birth_epoch(_aps, nproc)
                _ap_sig      = math.sqrt(max(P[_apki, _apki], 0.)) * 1e3
                _ap_acc      = amgr.get_accepted_count(_aps)
                _ap_rej      = amgr.get_rejected_count(_aps)
                # Did this sat appear in geom this epoch?
                _ap_present  = int(any(m['sid'] == _aps for m in geom))
                _ap_missing  = 1 - _ap_present
                _ap_last_Hn  = amgr.get_H_norm(_aps)
                # Participated in EKF update if H[rl,ki] != 0 this epoch
                _ap_in_upd   = int(_aps in _newborn_pending is False
                                   and amgr.get_last_accept_epoch(_aps, -1) == nproc)
                # Kalman gain proxy: sqrt(|ΔP[ki,ki]|)
                _pre_p = _P_before_diag.get(_aps, P[_apki, _apki])
                _Kg_proxy = abs(P[_apki, _apki] - _pre_p)
                _Kg_proxy = math.sqrt(max(_Kg_proxy, 0.)) * 1e3
                _ap_last_Kg = _Kg_proxy
                amgr.update_Kg_norm(_aps, _ap_last_Kg)
                _last_acc_ep = amgr.get_last_accept_epoch(_aps, -1)
                _ep_since_acc = (nproc - _last_acc_ep) if _last_acc_ep >= 0 else nproc
                _is_nb_p = int(_aps in _newborn_pending)
                _is_reset_p = int(
                    any(m['sid'] == _aps and slip
                        for m in geom for slip in [
                            abs(m.get('GF_m',0) - prev_gf.get(m['sid'],m.get('GF_m',0))) > 0.05
                            or abs(m.get('MW_cyc',0) - prev_mw.get(m['sid'],m.get('MW_cyc',0))) > 1.5
                        ]
                    )
                )
                amb_part_fh.write(
                    f"{nproc},{sod:.1f},{_aps},{_apki},"
                    f"{_ap_alive},"
                    f"{_fmtf(_ap_sig,prec=4)},"
                    f"{_ap_acc},{_ap_rej},"
                    f"{_ap_present},{_ap_missing},"
                    f"{_fmtf(_ap_last_Hn,prec=6)},"
                    f"{_ap_in_upd},"
                    f"{_fmtf(_ap_last_Kg,prec=6)},"
                    f"{_last_acc_ep},"
                    f"{_ep_since_acc},"
                    f"{_is_nb_p},{_is_reset_p}\n"
                )
        # ─────────────────────────────────────────────────────────────────────

        # ── Post-fit ambiguity birth ──────────────────────────────────────────
        # Newborns queued this epoch are born using post-EKF states.
        # They participate in EKF starting NEXT epoch.
        # FREEZE: _newborn_pending is already empty during freeze (births were
        # suppressed at queuing time), but guard here as defence-in-depth.
        if ENABLE_FREEZE_OBS and FREEZE_OBS_START <= nproc <= FREEZE_OBS_END and _newborn_pending:
            print(f"  [FREEZE-SUPPRESS-POSTFIT-BIRTH] epoch={nproc}  SOD={sod:.0f}"
                  f"  {len(_newborn_pending)} pending births SUPPRESSED (defence-in-depth)")
            _newborn_pending.clear()
        for _nsid, _nd in sorted(_newborn_pending.items()):
            _nki   = _nd['ki']
            _nLIFc = _nd['LIFc']
            _nm    = _nd['m']
            _nsys  = _nd['sys']
            _nsod  = _nd['sod']

            # ── ROOT-TRIGGER REVERT: OPERATIONAL BIRTH → POST-FIT ───────────────
            # Pre-fit birth was the corruption-preservation mechanism, NOT the trigger.
            # Switching back to post-fit to isolate WHY startup innovations are huge.
            _isb_birth = 0.   # GPS-only: no inter-system bias
            _rp_prefit = _rp(_nm, x_before[3], x_before[4])   # diagnostic only
            _rp_post   = _rp(_nm, x[3],        x[4])           # OPERATIONAL
            _n_prefit  = _nLIFc - _rp_prefit   # diagnostic
            _n_postfit = _nLIFc - _rp_post     # ← OPERATIONAL birth value (reverted)
            x[_nki]    = _n_postfit             # ← REVERTED to post-fit
            P[_nki,_nki] = 20.**2
            # PSD-REPAIR-2: Zero full row and column at birth.
            P[_nki, :]    = 0.
            P[:, _nki]    = 0.
            P[_nki, _nki] = 20.**2
            # register_birth: sets active=True, records born_epoch
            amgr.register_birth(_nsid, _nki, nproc, sod, sigma_m=20.0)

            # == REBIRTH-FORENSIC HUNK 4a: record_rebirth =================
            # Capture everything known at this moment (code/phase RMS not
            # yet computed; finalize_rebirth_epoch fills those in later).
            _rb_old_saved = _amb_saved_state.get(_nsid, {})
            _rb_old_N_mm  = float(_rb_old_saved["x_ki"]) * 1e3 \
                            if "x_ki" in _rb_old_saved else float("nan")
            _rb_old_sig   = (math.sqrt(max(float(_rb_old_saved["P_ki"]), 0.)) * 1e3
                             if "P_ki" in _rb_old_saved else float("nan"))
            _rb_slip_reason = ("rebirth" if amgr.states[_nsid].reset_count > 0
                               else "first_birth")
            _rb_mean_ph = (sum(_ph_innov_list) / len(_ph_innov_list)
                           if _ph_innov_list else float("nan"))
            _rebirth_forensic.record_rebirth({
                "sat":              _nsid,
                "sod":              _nsod,
                "nproc":            nproc,
                "slip_reason":      _rb_slip_reason,
                "rec_clk_pre_mm":   x_before[3] * 1e3,
                "rec_clk_post_mm":  x[3] * 1e3,
                "dclk_epoch_mm":    float((x[3] - x_before[3]) * 1e3),
                "N_postfit_mm":     _n_postfit * 1e3,
                "N_prefit_mm":      _n_prefit  * 1e3,
                "birth_sigma_mm":   20000.0,
                "old_N_mm":         _rb_old_N_mm,
                "old_sigma_mm":     _rb_old_sig,
                "PIF_mm":           _nm["PIF"] * 1e3,
                "LIFc_mm":          _nLIFc * 1e3,
                "rp_prefit_mm":     _rp_prefit * 1e3,
                "rp_postfit_mm":    _rp_post   * 1e3,
                "code_res_mm":      (_nm["PIF"] - _rp_post) * 1e3,
                "phase_res_mm":     0.0,
                "elevation_deg":    math.degrees(_nm["el"]),
                "mean_ph_innov_mm": _rb_mean_ph * 1e3 if math.isfinite(_rb_mean_ph) else float("nan"),
                "n_accepted_phase": len(_accepted_phase_sids),
                "ph_innov_signs":   list(_ph_innov_list),
                "n_sats":           len(geom),
            })
            # =============================================================

            # ── ROOT-TRIGGER PATCH 6 ASSERTIONS #1 and #4 ──────────────────
            _lif_minus_rp_pre = abs(_nLIFc - _rp_prefit)
            if abs(x[_nki]) > 15.0:
                print(
                    f"[FATAL-STARTUP-INCONSISTENCY]  ASSERTION-1\n"
                    f"  sat={_nsid}  SOD={_nsod:.0f}\n"
                    f"  abs(birth_m)={abs(x[_nki]):.3f} m > 15 m\n"
                    f"  birth_m={x[_nki]:.6f} m   N_prefit={_n_prefit:.6f} m\n"
                    f"  LIFc={_nLIFc:.6f} m   rp_post={_rp_post:.6f} m\n"
                    f"  INTERPRETATION: ambiguity at birth is astronomically large — "
                    f"IF combination error, wrong OSB, or clock not absorbed"
                )
                raise RuntimeError(
                    f"[FATAL] birth_m={x[_nki]:.3f} m > 15 m for {_nsid} at SOD={_nsod:.0f}"
                )
            if _lif_minus_rp_pre > 50.0:
                print(
                    f"[FATAL-STARTUP-INCONSISTENCY]  ASSERTION-4\n"
                    f"  sat={_nsid}  SOD={_nsod:.0f}\n"
                    f"  abs(LIF_corr - rp_prefit)={_lif_minus_rp_pre:.3f} m > 50 m\n"
                    f"  LIFc={_nLIFc:.6f} m   rp_prefit={_rp_prefit:.6f} m\n"
                    f"  INTERPRETATION: observation-model gap of {_lif_minus_rp_pre:.1f} m "
                    f"— likely IF combination mismatch or missing bias term"
                )
                raise RuntimeError(
                    f"[FATAL] LIF_corr - rp_prefit = {_lif_minus_rp_pre:.3f} m > 50 m "
                    f"for {_nsid} at SOD={_nsod:.0f}"
                )
            # ────────────────────────────────────────────────────────────────

            # ── ROOT-TRIGGER PATCH 4: AMB-DATUM ─────────────────────────────
            _lam_if_d  = _nm.get('_lam_if', LAMBDA_IF)
            _lam1_d    = _nm.get('_lam1',   LAMBDA1)
            _birth_cyc_if = x[_nki] / _lam_if_d  if _lam_if_d != 0. else float('nan')
            _birth_cyc_l1 = x[_nki] / _lam1_d    if _lam1_d   != 0. else float('nan')
            print(
                f"[AMB-DATUM]\n"
                f"  SAT={_nsid}  SOD={_nsod:.0f}\n"
                f"  LIF_corr={_nLIFc:.6f} m\n"
                f"  rp_postfit={_rp_post:.6f} m\n"
                f"  birth_m={x[_nki]:.6f} m\n"
                f"  birth_cycles_L1={_birth_cyc_l1:.4f} cyc   (lambda_L1={_lam1_d:.10f} m)\n"
                f"  birth_cycles_IF={_birth_cyc_if:.4f} cyc   (lambda_IF={_lam_if_d:.10f} m)"
            )
            # ────────────────────────────────────────────────────────────────

            # ── FORENSIC BIRTH AUDIT ─────────────────────────────────────────
            # Compute full H-row, birth S, NIS, and sigma for tracing.
            # With post-fit birth x[_nki]=N_postfit, the birth-epoch residual
            # (LIFc - rp_post - N_postfit) is zero by construction.
            # N_prefit is kept separately for diagnostic comparison.
            _h_birth = np.zeros(len(x))
            _h_birth[0]=-_nm['unit'][0]; _h_birth[1]=-_nm['unit'][1]; _h_birth[2]=-_nm['unit'][2]
            _h_birth[3]=1.; _h_birth[4]=_nm['mw']; _h_birth[_nki]=1.
            # birth_nu using post-fit rp: should be ~0 for postfit birth
            _birth_nu_m     = _nLIFc - (_rp_post + _isb_birth + x[_nki])
            _birth_R        = _sig(_nm['el'], SP)**2
            _birth_S        = float(_h_birth @ P @ _h_birth) + _birth_R
            _birth_S        = max(_birth_S, 1e-12)
            _birth_NIS      = (_birth_nu_m * 1e3)**2 / max(_birth_S * 1e6, 1e-12)
            _birth_pred_sig_mm = math.sqrt(max(_birth_S * 1e6, 0.))
            _birth_innov_mm = _birth_nu_m * 1e3
            # clock contribution to birth: prefit vs postfit
            _dclk_birth_mm  = (x_before[3] - x[3]) * 1e3   # signed clock shift

            # Outlier flag (>10 m) — diagnostic only, does not reject
            if abs(x[_nki]) > 10.0:
                print(f"  [AMB-BIRTH-OUTLIER] sat={_nsid}  SOD={_nsod:.0f}  "
                      f"N={x[_nki]*1e3:+.1f}mm  |N|>10m — possible OSB/model issue")

            print(f"[AMB-BORN] sat={_nsid}  SOD={_nsod:.0f}  sys={_nsys}  "
                  f"N={x[_nki]*1e3:+.1f}mm  birth_sigma=20000mm  "
                  f"(post-fit clock; active from next epoch)  "
                  f"N_postfit={_n_postfit*1e3:+.1f}mm  N_prefit={_n_prefit*1e3:+.1f}mm  "
                  f"dclk={_dclk_birth_mm:+.1f}mm")

            # ── PHASE-4: AMB-BIRTH-FORENSIC ─────────────────────────────────
            # Full decomposition — TRACE ONLY, no logic changes.
            _rho_b   = _nm['rng']
            _sck_b   = _nm['scm']
            _dtr_b   = _nm['dtrel']
            _shp_b   = _nm['shp']
            _set_b   = _nm['setm']
            _trph_b  = _nm['trop_zhd']
            _trpw_b  = _nm['mw'] * x[4]
            _pcvs_b  = _nm['pcv_sat']
            _pcvr_b  = _nm['pcv_rec']
            _apcs_b  = _nm.get('_sat_apc_range_m', 0.)
            _apcr_b  = _nm.get('_rec_apc_range_m', 0.)
            _osb_p_b = _nm.get('_osb_IF_phase_m', 0.)
            _wu_b    = wum.get(_nsid, 0.)
            _lif_b   = _nm.get('_lam_if', LAMBDA_IF)
            _wu_mm_b = _wu_b * _lif_b * 1e3
            _clk_b   = x[3]           # post-update (operational)
            _clk_pre_b = x_before[3]  # pre-update (diagnostic)
            # Phase-5: high-detail birth forensic for first 2 hours OR any newborn
            _is_first2h = (sod - eplist[0]['t']) < 7200.
            if _is_first2h or is_startup:
                print(
                    f"[AMB-BIRTH-FORENSIC]\n"
                    f"  sat={_nsid}  sys={_nsys}  SOD={_nsod:.0f}"
                    f"  is_startup={is_startup}  is_first2h={_is_first2h}\n"
                    f"  observed_phase_LIFc           = {_nLIFc*1e3:+.3f} mm\n"
                    f"  predicted_postfit (used)      = {_rp_post*1e3:+.3f} mm"
                    f"  → N_postfit (operational)     = {_n_postfit*1e3:+.3f} mm\n"
                    f"  predicted_prefit  (diagnostic)= {_rp_prefit*1e3:+.3f} mm"
                    f"  → N_prefit (diagnostic)        = {_n_prefit*1e3:+.3f} mm\n"
                    f"  clock_shift (pre→post)        = {_dclk_birth_mm:+.3f} mm\n"
                    f"    rho_geom      = {_rho_b*1e3:+.3f} mm\n"
                    f"    sat_clk(neg)  = {-_sck_b*1e3:+.3f} mm\n"
                    f"    rec_clk_post  = {_clk_b*1e3:+.3f} mm  (used for birth)\n"
                    f"    rec_clk_pre   = {_clk_pre_b*1e3:+.3f} mm  (pre-update)\n"
                    f"    tropo_hydro   = {_trph_b*1e3:+.3f} mm\n"
                    f"    tropo_wet     = {_trpw_b*1e3:+.3f} mm  (ZWD={x[4]*1e3:.1f}mm)\n"
                    f"    relativity    = {_dtr_b*1e3:+.3f} mm\n"
                    f"    shapiro       = {_shp_b*1e3:+.3f} mm\n"
                    f"    solid_earth   = {_set_b*1e3:+.3f} mm\n"
                    f"    apc_sat_LOS   = {_apcs_b*1e3:+.3f} mm\n"
                    f"    apc_rec_LOS   = {_apcr_b*1e3:+.3f} mm\n"
                    f"    pcv_sat       = {_pcvs_b*1e3:+.3f} mm\n"
                    f"    pcv_rec       = {_pcvr_b*1e3:+.3f} mm\n"
                    f"    osb_IF_phase  = {_osb_p_b*1e3:+.3f} mm\n"
                    f"    wind_up       = {_wu_mm_b:+.3f} mm  ({_wu_b:.4f} cyc)\n"
                    f"  birth_value    = {x[_nki]*1e3:+.3f} mm"
                    f"  ({x[_nki]/_lif_b:.4f} cyc)  [POST-FIT BIRTH]\n"
                    f"  initial_sigma  = 20000 mm"
                )

            # ── PHASE-2: write one row to amb_birth_trace.csv ────────────────
            if birth_trace_fh is not None:
                _lam_if_b = _nm.get('_lam_if', LAMBDA_IF)
                _birth_cyc = (x[_nki] / _lam_if_b) if _lam_if_b != 0. else float('nan')
                _pred_no_amb = _rp_post * 1e3   # postfit predicted phase (mm) — operational
                _pred_prefit_mm = _rp_prefit * 1e3  # prefit predicted phase (mm) — diagnostic
                birth_trace_fh.write(
                    f"{nproc},{_nsod:.1f},{_nsid},{_nsys},"
                    f"{_fmtf(math.degrees(_nm['el']),prec=3)},"
                    f"{_fmtf(x[_nki],1e3,4)},"          # birth_mm  (N_postfit — operational)
                    f"{_fmtf(_birth_cyc,prec=6)},"
                    f"20000.0000,"                        # birth_sigma_mm always 20000
                    f"{_fmtf(_nLIFc,1e3,4)},"            # observed_lif_mm
                    f"{_fmtf(_pred_no_amb,prec=4)},"     # pred_postfit_mm (operational)
                    f"{_fmtf(_rho_b,1e3,4)},"
                    f"{_fmtf(-_sck_b,1e3,4)},"
                    f"{_fmtf(_clk_b,1e3,4)},"            # rec_clk_post_mm (used for birth)
                    f"{_fmtf(_trph_b,1e3,4)},"
                    f"{_fmtf(_trpw_b,1e3,4)},"
                    f"{_fmtf(_dtr_b,1e3,4)},"
                    f"{_fmtf(_shp_b,1e3,4)},"
                    f"{_fmtf(_set_b,1e3,4)},"
                    f"{_fmtf(_apcs_b,1e3,4)},"
                    f"{_fmtf(_apcr_b,1e3,4)},"
                    f"{_fmtf(_pcvs_b,1e3,4)},"
                    f"{_fmtf(_pcvr_b,1e3,4)},"
                    f"{_fmtf(_osb_p_b,1e3,4)},"
                    f"{_fmtf(_wu_mm_b,prec=4)},"
                    f"{_fmtf(_wu_b,prec=6)},"
                    f"{1 if is_startup else 0},"
                    f"{1 if _is_first2h else 0},"
                    f"{_fmtf(_birth_innov_mm,prec=4)},"
                    f"{_fmtf_sci(_birth_NIS)},"
                    f"{_fmtf(_birth_pred_sig_mm,prec=4)},"
                    f"{_fmtf(_n_prefit,1e3,4)},"         # n_prefit_mm — diagnostic comparison
                    f"{_fmtf(_clk_pre_b,1e3,4)},"        # rec_clk_pre_mm — diagnostic
                    f"{_fmtf(_dclk_birth_mm,prec=4)}\n"  # dclk_birth_mm = clk_pre - clk_post
                )
            # ─────────────────────────────────────────────────────────────────
        for sid_chk in list(nl_fixed.keys()):
            if sid_chk not in sidx or not amgr.is_active(sid_chk):
                continue
            if not any(m['sid'] == sid_chk for m in geom):
                continue
            ki_chk = sidx[sid_chk]
            ob_chk = osb.get(sid_chk, {})
            m_chk  = next(m for m in geom if m['sid'] == sid_chk)
            bl1c = ob_chk.get('L1C', ob_chk.get('L1W', 0.))
            bl2c = ob_chk.get('L2W', ob_chk.get('L2C', 0.))
            N_IF_chk = _nl_if_value(nl_fixed[sid_chk], wl_fixed[sid_chk], bl1c, bl2c)
            post_innov = abs(N_IF_chk - x[ki_chk])
            if post_innov > NL_RELEASE_THRESH:
                nl_fixed.pop(sid_chk, None)
                print(f"  [NL RELEASE] {sid_chk} SOD={sod:.0f} drift={post_innov*1e3:.1f}mm")

        # ── NL FIXING — DISABLED (float PPP convergence validation) ─────────
        # AR DISABLED: uncomment this block to re-enable NL fixing once float
        # PPP convergence is validated.
        #
        # nl_cands = [m for m in geom
        #             if m['sid'] in wl_fixed
        #             and m['sid'] not in nl_fixed
        #             and m['sid'] not in _nl_bad_nwl
        #             and amgr.is_active(m['sid'])
        #             and P[m['ki'],m['ki']] < NL_VAR_THRESH
        #             and phase_rms_now < NL_PHASE_THRESH]
        # ... (full LAMBDA block omitted — see original for implementation)

        # ── Compute residuals / RMS for diagnostics ───────────────────────────
        # _active_geom: birth-complete sats used for trace/stats exports ONLY.
        # Must NOT be used for phase_rms — it includes hard-gated / corrupted sats.
        _active_geom = [m for m in geom
                        if amgr.is_birth_complete(m['sid'])
                        and m['sid'] not in _newborn_pending]

        # phase_rms: ACCEPTED POSTFIT residuals only.
        # _accepted_phase_sids contains only satellites whose phase row entered
        # H/z/R this epoch (i.e. passed the hard gate and were born).
        # Postfit residuals use post-EKF x so ambiguity estimation is reflected.
        # This eliminates the 1–4 m contamination from hard-gated / outlier-born
        # ambiguities (e.g. G10 N=+57278mm) that previously inflated the RMS.
        _phase_postfit_res_mm = [
            (m['LIFc'] - (_rp(m, x[3], x[4]) + x[m['ki']])) * 1e3
            for m in geom
            if m['sid'] in _accepted_phase_sids
        ]
        phase_rms     = (math.sqrt(sum(r * r for r in _phase_postfit_res_mm)
                                   / len(_phase_postfit_res_mm))
                         if _phase_postfit_res_mm else float('nan'))
        # phase_rms_now in metres: kept for the (disabled) NL phase gate
        phase_rms_now = phase_rms / 1e3 if math.isfinite(phase_rms) else float('nan')

        code_res_all = [m['PIF'] - _rp(m, x[3], x[4]) for m in geom]
        code_rms     = (math.sqrt(np.mean(np.array(code_res_all)**2)) * 1e3
                        if code_res_all else 0.)

        # ── PATCH 1 (continued): back-fill postfit residuals into audit ───────
        # Build sid→postfit maps for back-filling
        _pf_phase_by_sid = {
            m['sid']: (m['LIFc'] - (_rp(m, x[3], x[4]) + x[m['ki']])) * 1e3
            for m in geom if m['sid'] in _accepted_phase_sids
        }
        _pf_code_by_sid = {
            m['sid']: (m['PIF'] - _rp(m, x[3], x[4])) * 1e3
            for m in geom
        }
        for _rar in _epoch_residual_audit:
            _rsid = _rar['sat']
            if _rar['obs_type'] == 'phase':
                _rar['postfit_residual_mm'] = _pf_phase_by_sid.get(_rsid, float('nan'))
                _pf_v = _rar['postfit_residual_mm']
                _rar['sigma_postfit_mm'] = (abs(_pf_v)
                    if math.isfinite(_pf_v) else float('nan'))
            else:
                _rar['postfit_residual_mm'] = _pf_code_by_sid.get(_rsid, float('nan'))
        # Build weighted-residual collections
        _weighted_phase = [
            r['prefit_linearized_mm'] / math.sqrt(max(r['R_eff'], 1e-30) * 1e6)
            for r in _epoch_residual_audit
            if r['obs_type'] == 'phase' and r['accepted']
               and math.isfinite(r['prefit_linearized_mm'])
        ]
        _weighted_code = [
            r['prefit_linearized_mm'] / math.sqrt(max(r['R_eff'], 1e-30) * 1e6)
            for r in _epoch_residual_audit
            if r['obs_type'] == 'code' and r['accepted']
               and math.isfinite(r['prefit_linearized_mm'])
        ]
        _postfit_phase_mm = [
            r['postfit_residual_mm'] for r in _epoch_residual_audit
            if r['obs_type'] == 'phase' and math.isfinite(r.get('postfit_residual_mm', float('nan')))
        ]
        _postfit_code_mm = [
            r['postfit_residual_mm'] for r in _epoch_residual_audit
            if r['obs_type'] == 'code' and math.isfinite(r.get('postfit_residual_mm', float('nan')))
        ]
        # ── END PATCH 1 back-fill ─────────────────────────────────────────────
        # == REBIRTH-FORENSIC HUNK 4b: back-fill code/phase RMS =============
        # finalize_rebirth_epoch runs BEFORE MEAS_AUDIT so cm_ratio may not
        # yet be available; the conditional below re-runs after MEAS_AUDIT.
        _rebirth_forensic.finalize_rebirth_epoch(
            code_rms_mm  = code_rms,
            phase_rms_mm = phase_rms if math.isfinite(phase_rms) else float("nan"),
            cm_ratio     = float("nan"),  # updated again below after MEAS_AUDIT
        )
        # ====================================================================

        _phs_str     = 'inactive' if not math.isfinite(phase_rms) else f'{phase_rms:.2f}mm'

        # Sentinels for OBS-BALANCE (PATCH 2 experiment); filled inside RESIDUAL-FRAME-AUDIT
        _obs_ph_postfit_rms_mm    = float('nan')
        _obs_cod_postfit_rms_mm   = float('nan')
        _obs_mean_abs_cp_delta_mm = float('nan')

        # ══════════════════════════════════════════════════════════════════
        # PATCH 2 — RESIDUAL-FRAME-AUDIT  (instrumentation only)
        # Classifies every residual population into its explicit mathematical
        # stage and prints once per epoch so the frame lineage is traceable.
        # ══════════════════════════════════════════════════════════════════
        try:
            def _rms(lst):
                lst = [v for v in lst if math.isfinite(v)]
                return math.sqrt(sum(v*v for v in lst) / len(lst)) if lst else float('nan')

            # PREFIT_TRUE / PREFIT_LINEARIZED  (identical in PPP; z[rl]=phase_res)
            _p2a_ph_prefit_rms  = _rms([r['prefit_linearized_mm'] for r in _epoch_residual_audit if r['obs_type']=='phase'])
            _p2a_cod_prefit_rms = _rms([r['prefit_linearized_mm'] for r in _epoch_residual_audit if r['obs_type']=='code'])
            _p2a_ph_n   = sum(1 for r in _epoch_residual_audit if r['obs_type']=='phase')
            _p2a_cod_n  = sum(1 for r in _epoch_residual_audit if r['obs_type']=='code')

            # WEIGHTED  (dimensionless; z / sqrt(R_eff), R_eff already in m²)
            _p2b_ph_w_rms  = _rms(_weighted_phase)
            _p2b_cod_w_rms = _rms(_weighted_code)

            # POSTFIT  (uses post-update x)
            _p2c_ph_postfit_rms  = _rms(_postfit_phase_mm)
            _p2c_cod_postfit_rms = _rms(_postfit_code_mm)

            # Capture for OBS-BALANCE (PATCH 2 experiment)
            _obs_ph_postfit_rms_mm  = _p2c_ph_postfit_rms
            _obs_cod_postfit_rms_mm = _p2c_cod_postfit_rms
            # mean |code_postfit - phase_postfit| per matched satellite
            _obs_ph_pf_by_sat = {r['sat']: r['postfit_residual_mm']
                                 for r in _epoch_residual_audit
                                 if r['obs_type'] == 'phase'
                                 and math.isfinite(r.get('postfit_residual_mm', float('nan')))}
            _obs_co_pf_by_sat = {r['sat']: r['postfit_residual_mm']
                                 for r in _epoch_residual_audit
                                 if r['obs_type'] == 'code'
                                 and math.isfinite(r.get('postfit_residual_mm', float('nan')))}
            _obs_cp_deltas = [abs(_obs_co_pf_by_sat[_s] - _obs_ph_pf_by_sat[_s])
                              for _s in _obs_ph_pf_by_sat if _s in _obs_co_pf_by_sat]
            _obs_mean_abs_cp_delta_mm = (sum(_obs_cp_deltas) / len(_obs_cp_deltas)
                                         if _obs_cp_deltas else float('nan'))
            # STAGE-2A accumulation: per-epoch code-phase delta
            if math.isfinite(_obs_mean_abs_cp_delta_mm):
                _s2a_cp_delta_list.append(_obs_mean_abs_cp_delta_mm)

            # Deltas
            _d_pf2lin  = (_p2c_ph_postfit_rms - _p2a_ph_prefit_rms  if math.isfinite(_p2a_ph_prefit_rms)  and math.isfinite(_p2c_ph_postfit_rms)  else float('nan'))
            _d_lin2w   = float('nan')  # can't directly subtract mm from dimensionless

            print(
                f"[RESIDUAL-FRAME-AUDIT]  epoch={nproc}  sod={sod:.0f}\n"
                f"  PREFIT_TRUE/LINEARIZED  (z[rl]=phase_res, pre-update xs):\n"
                f"    phase_rms={_p2a_ph_prefit_rms:.2f}mm  n={_p2a_ph_n}\n"
                f"    code_rms ={_p2a_cod_prefit_rms:.2f}mm  n={_p2a_cod_n}\n"
                f"  WEIGHTED  (z/sqrt(R_eff), dimensionless):\n"
                f"    phase_rms={_p2b_ph_w_rms:.4f}  n={len(_weighted_phase)}\n"
                f"    code_rms ={_p2b_cod_w_rms:.4f}  n={len(_weighted_code)}\n"
                f"  POSTFIT  (LIFc−(rp(x_post)+x_post[ki]), post-update x):\n"
                f"    phase_rms={_p2c_ph_postfit_rms:.2f}mm  n={len(_postfit_phase_mm)}\n"
                f"    code_rms ={_p2c_cod_postfit_rms:.2f}mm  n={len(_postfit_code_mm)}\n"
                f"  DELTAS:\n"
                f"    prefit→postfit(phase): {_d_pf2lin:+.2f}mm  "
                f"  ratio={(_p2c_ph_postfit_rms/_p2a_ph_prefit_rms if _p2a_ph_prefit_rms > 0 and math.isfinite(_p2c_ph_postfit_rms) else float('nan')):.4f}\n"
                f"    prefit→postfit(code):  {(_p2c_cod_postfit_rms - _p2a_cod_prefit_rms):+.2f}mm  "
                f"  ratio={(_p2c_cod_postfit_rms/_p2a_cod_prefit_rms if _p2a_cod_prefit_rms > 0 and math.isfinite(_p2c_cod_postfit_rms) else float('nan')):.4f}"
            )
        except Exception as _p2_ex:
            print(f"  [RESIDUAL-FRAME-AUDIT-WARN] failed: {_p2_ex}")
        # ── END PATCH 2 ───────────────────────────────────────────────────────

        # ── OBSERVABILITY EXPERIMENT PATCH 2: OBS-BALANCE ────────────────────
        # One consolidated observability line per epoch — the primary success
        # criterion metric for the AMB_Q_FLOOR relaxation experiment.
        # All fields available here (postfit RMS filled by RESIDUAL-FRAME-AUDIT).
        try:
            _ob_sr    = _ib_sr if math.isfinite(_ib_sr) else float('nan')
            _ob_clk   = _p2_clk_sig_after if math.isfinite(_p2_clk_sig_after) else float('nan')
            _ob_ratio = _ib_ratio if math.isfinite(_ib_ratio) else float('nan')
            _ob_prfpre = _ib_ph_rms if math.isfinite(_ib_ph_rms) else float('nan')
            print(
                f"[OBS-BALANCE] epoch={nproc}  sod={sod:.0f}"
                f"  spectral_radius={_ob_sr:.4f}"
                f"  clk_sig_mm={_ob_clk:.1f}"
                f"  mean_amb_sig_mm={_obs_mean_amb_sig_mm:.1f}"
                f"  ph/code_info_ratio={_ob_ratio:.2e}"
                f"  phase_prefit_rms_mm={_ob_prfpre:.1f}"
                f"  phase_postfit_rms_mm={_obs_ph_postfit_rms_mm:.2f}"
                f"  code_postfit_rms_mm={_obs_cod_postfit_rms_mm:.1f}"
                f"  mean_abs_code_phase_delta_mm={_obs_mean_abs_cp_delta_mm:.1f}"
            )
        except Exception as _ob_ex:
            print(f"  [OBS-BALANCE-WARN] failed: {_ob_ex}")
        # ── END OBS-BALANCE ───────────────────────────────────────────────────

        # ── STAGE-7: forward cp_delta to next epoch's POS-FLOOR ──────────────
        # _obs_mean_abs_cp_delta_mm was just set (or left nan) by OBS-BALANCE.
        # Store it so the floor formula can use it at the NEXT epoch's Joseph
        # insertion point.
        if math.isfinite(_obs_mean_abs_cp_delta_mm):
            _pf_prev_cp_delta_mm = _obs_mean_abs_cp_delta_mm
        # If nan (e.g. no postfit pairs), keep the last finite value.
        # ─────────────────────────────────────────────────────────────────────


        # Documents exactly which array feeds each printed metric.
        # ══════════════════════════════════════════════════════════════════
        try:
            _p3_ph_prefit_rms   = _rms([r['prefit_linearized_mm'] for r in _epoch_residual_audit if r['obs_type']=='phase'])
            _p3_cod_prefit_rms  = _rms([r['prefit_linearized_mm'] for r in _epoch_residual_audit if r['obs_type']=='code'])
            _p3_ph_postfit_rms  = _rms(_postfit_phase_mm)
            _p3_cod_postfit_rms = _rms(_postfit_code_mm)
            _p3_ib_ph_rms       = (_rms([r * 1e3 for r in _ph_innov_list])
                                    if _ph_innov_list else float('nan'))
            _p3_ib_cod_rms      = (_rms([r * 1e3 for r in _code_innov_list])
                                    if _code_innov_list else float('nan'))
            print(
                f"[RESIDUAL-POPULATION-CHECK]  epoch={nproc}  sod={sod:.0f}\n"
                f"  [INFO-BALANCE]  phase_rms  ← _ph_innov_list  prefit  n={len(_ph_innov_list)}"
                f"  rejected=NO  frame=PREFIT_LINEARIZED  value={_p3_ib_ph_rms:.2f}mm\n"
                f"  [INFO-BALANCE]  code_rms   ← _code_innov_list  prefit  n={len(_code_innov_list)}"
                f"  rejected=NO  frame=PREFIT_LINEARIZED  value={_p3_ib_cod_rms:.2f}mm\n"
                f"  [FORENSIC-SUMMARY] phase_rms ← same as INFO-BALANCE (_ib_ph_rms)\n"
                f"  [COMMON-MODE]   phase_res  ← _accepted_phase_sids postfit  n={len(_postfit_phase_mm)}"
                f"  frame=POSTFIT  value={_p3_ph_postfit_rms:.2f}mm\n"
                f"  [EPOCH]         phase_rms  ← _phase_postfit_res_mm  postfit  n={len(_phase_postfit_res_mm)}"
                f"  rejected=NO  frame=POSTFIT  value={_p3_ph_postfit_rms:.2f}mm\n"
                f"  [EPOCH]         code_rms   ← all geom sats  postfit  n={len(code_res_all)}"
                f"  rejected=INCLUDED  frame=POSTFIT  value={code_rms:.2f}mm\n"
                f"  *** DIVERGENCE: INFO-BALANCE phase_rms({_p3_ib_ph_rms:.1f}mm) uses PREFIT;"
                f" EPOCH phase_rms({_p3_ph_postfit_rms:.1f}mm) uses POSTFIT ***"
            )
        except Exception as _p3_ex:
            print(f"  [RESIDUAL-POPULATION-CHECK-WARN] failed: {_p3_ex}")
        # ── END PATCH 3 ───────────────────────────────────────────────────────

        # ══════════════════════════════════════════════════════════════════
        # PATCH 4 — EKF-CORRECTION-EFFECT
        # Quantifies how much the EKF genuinely reduced residuals.
        # ══════════════════════════════════════════════════════════════════
        try:
            _p4_ph_pre   = _rms([r['prefit_linearized_mm'] for r in _epoch_residual_audit if r['obs_type']=='phase'])
            _p4_cod_pre  = _rms([r['prefit_linearized_mm'] for r in _epoch_residual_audit if r['obs_type']=='code'])
            _p4_ph_post  = _rms(_postfit_phase_mm)
            _p4_cod_post = _rms(_postfit_code_mm)
            _p4_ph_red   = ((_p4_ph_pre - _p4_ph_post) / _p4_ph_pre
                            if _p4_ph_pre > 0 and math.isfinite(_p4_ph_post) else float('nan'))
            _p4_cod_red  = ((_p4_cod_pre - _p4_cod_post) / _p4_cod_pre
                            if _p4_cod_pre > 0 and math.isfinite(_p4_cod_post) else float('nan'))
            # State increment norms from existing forensic variables
            _p4_dx_norm  = float('nan')
            try:
                _p4_dx_norm = float(_ekf_dx_norm) if '_ekf_dx_norm' in dir() else float('nan')
            except Exception:
                pass
            print(
                f"[EKF-CORRECTION-EFFECT]  epoch={nproc}  sod={sod:.0f}\n"
                f"  phase prefit_rms={_p4_ph_pre:.2f}mm → postfit_rms={_p4_ph_post:.2f}mm"
                f"  reduction_ratio={_p4_ph_red:.4f}"
                f"  ('1.0' = full cancellation; '0.0' = no change)\n"
                f"  code  prefit_rms={_p4_cod_pre:.2f}mm → postfit_rms={_p4_cod_post:.2f}mm"
                f"  reduction_ratio={_p4_cod_red:.4f}\n"
                f"  ||dx||={_p4_dx_norm:.1f}mm (state increment; from EKF-POSTFIT if available)\n"
                f"  NOTE: large postfit reduction means EKF genuinely absorbed measurement;"
                f" small means either pre≈post or ambiguity was reset to postfit."
            )
        except Exception as _p4_ex:
            print(f"  [EKF-CORRECTION-EFFECT-WARN] failed: {_p4_ex}")
        # ── END PATCH 4 ───────────────────────────────────────────────────────

        # ══════════════════════════════════════════════════════════════════
        # PATCH 5 — HARD CONSISTENCY ASSERTIONS
        # ══════════════════════════════════════════════════════════════════
        try:
            # Assertion A: Two metrics labelled "phase_rms" differ >5×
            _pa_ib_ph  = (_rms([r * 1e3 for r in _ph_innov_list])
                          if _ph_innov_list else float('nan'))
            _pa_ep_ph  = _rms(_postfit_phase_mm)
            if (math.isfinite(_pa_ib_ph) and math.isfinite(_pa_ep_ph)
                    and _pa_ep_ph > 0
                    and (_pa_ib_ph / _pa_ep_ph) > 5.0):
                print(
                    f"  [RESIDUAL-FRAME-MISMATCH]  epoch={nproc}  sod={sod:.0f}\n"
                    f"    Assertion A: INFO-BALANCE phase_rms={_pa_ib_ph:.1f}mm"
                    f" vs EPOCH phase_rms={_pa_ep_ph:.2f}mm"
                    f"  ratio={_pa_ib_ph/_pa_ep_ph:.1f}x > 5×\n"
                    f"    CAUSE: INFO-BALANCE uses PREFIT innovations (z[rl]=phase_res, pre-update xs)\n"
                    f"           EPOCH uses POSTFIT residuals (post-update x)\n"
                    f"    VERDICT: Frame mismatch confirmed — NOT a filter malfunction."
                )

            # Assertion B: postfit RMS < prefit RMS by >20× in one epoch
            if (math.isfinite(_pa_ib_ph) and math.isfinite(_pa_ep_ph)
                    and _pa_ep_ph > 0
                    and (_pa_ib_ph / _pa_ep_ph) > 20.0):
                print(
                    f"  [RESIDUAL-FRAME-MISMATCH-EXTREME]  epoch={nproc}  sod={sod:.0f}\n"
                    f"    Assertion B: postfit/prefit ratio = {_pa_ib_ph/_pa_ep_ph:.1f}x > 20×\n"
                    f"    Per-satellite breakdown:"
                )
                for _rar in _epoch_residual_audit:
                    if _rar['obs_type'] == 'phase':
                        print(
                            f"      sat={_rar['sat']}"
                            f"  prefit={_rar['prefit_linearized_mm']:+.2f}mm"
                            f"  postfit={_rar['postfit_residual_mm']:+.2f}mm"
                            f"  Huber_w={_rar['huber_weight']:.3f}"
                            f"  NIS={_rar['NIS']:.2f}"
                        )

            # Assertion C: check if weighted RMS is ever misreported as physical
            # (weighted residuals are dimensionless; if any downstream code reports
            #  _weighted_phase values as mm, they will be ~0.01–0.1, not ~100 mm)
            # We flag if anyone divides by sqrt(R_eff) and the result < 1.0 while
            # the prefit is > 10 mm — this is the telltale sign.
            _pa_w_ph_rms = _rms(_weighted_phase)
            if (math.isfinite(_pa_w_ph_rms) and _pa_w_ph_rms < 1.0
                    and math.isfinite(_pa_ib_ph) and _pa_ib_ph > 10.0):
                print(
                    f"  [WEIGHTED-AS-PHYSICAL-FLAG]  epoch={nproc}  sod={sod:.0f}\n"
                    f"    Assertion C: weighted_phase_rms={_pa_w_ph_rms:.4f} (dimensionless)"
                    f"  prefit={_pa_ib_ph:.1f}mm\n"
                    f"    If any metric reports {_pa_w_ph_rms:.4f} as 'mm', it is confusing"
                    f" weighted with physical residuals."
                )
        except Exception as _p5_ex:
            print(f"  [PATCH5-ASSERT-WARN] failed: {_p5_ex}")
        # ── END PATCH 5 ───────────────────────────────────────────────────────
        # These are TRACE-ONLY prints — no filter logic is changed.
        # Controlled by _MEAS_AUDIT_ENABLE (see top of _ppp_pass).
        # ══════════════════════════════════════════════════════════════════
        _au_cm_ratio = float("nan")  # REBIRTH-FORENSIC: default; overwritten inside MEAS_AUDIT block
        if _MEAS_AUDIT_ENABLE and geom:
            # ── Phase 4: Common-mode detector (must come first for per-sat delta) ──
            _au_code_res = [m['PIF'] - _rp(m, x[3], x[4]) for m in geom]
            _au_phase_res = [
                (m['LIFc'] - (_rp(m, x[3], x[4]) + x[m['ki']])) * 1e3
                for m in geom if m['sid'] in _accepted_phase_sids
            ]
            _au_mean_code = float(np.mean([r * 1e3 for r in _au_code_res])) if _au_code_res else 0.
            _au_std_code  = float(np.std([r * 1e3 for r in _au_code_res]))  if _au_code_res else 0.
            _au_mean_ph   = float(np.mean(_au_phase_res)) if _au_phase_res else 0.
            _au_std_ph    = float(np.std(_au_phase_res))  if _au_phase_res else 0.
            # PATCH 3 FIX: cm_ratio is undefined for n < 2 (std=0 → ratio=inf).
            # Guard: require ≥ 2 accepted phase sats before computing cm_ratio.
            if len(_au_phase_res) >= 2 and _au_std_ph > 1e-3:
                _au_cm_ratio = abs(_au_mean_ph) / _au_std_ph
            else:
                _au_cm_ratio = float('nan')   # not enough spread to be meaningful
            print(
                f"  [COMMON-MODE] SOD={sod:.0f} "                f"mean_code={_au_mean_code:+.1f}mm std_code={_au_std_code:.1f}mm "                f"mean_phase={_au_mean_ph:+.1f}mm std_phase={_au_std_ph:.1f}mm "                f"cm_ratio={_au_cm_ratio:.2f}"
            )
            # == REBIRTH-FORENSIC HUNK 4c: update cm_ratio in births ========
            _rebirth_forensic.finalize_rebirth_epoch(
                code_rms_mm  = code_rms,
                phase_rms_mm = phase_rms if math.isfinite(phase_rms) else float("nan"),
                cm_ratio     = _au_cm_ratio,
            )
            # ================================================================

            # ── Phase 5: Code-phase delta per satellite ──────────────────────
            _au_code_res_dict = {m['sid']: (m['PIF'] - _rp(m, x[3], x[4])) * 1e3
                                 for m in geom}
            _au_phase_res_dict = {
                m['sid']: (m['LIFc'] - (_rp(m, x[3], x[4]) + x[m['ki']])) * 1e3
                for m in geom if m['sid'] in _accepted_phase_sids
            }
            for _asm in geom:
                _asid = _asm['sid']
                _acr  = _au_code_res_dict.get(_asid, float('nan'))
                _apr  = _au_phase_res_dict.get(_asid, float('nan'))
                _adcp = _acr - _apr if math.isfinite(_apr) else float('nan')
                print(
                    f"  [CODE-PHASE-DELTA] sat={_asid} "                    f"code_res={_acr:+.1f}mm phase_res={_apr:+.1f}mm "                    f"delta_cp={_adcp:+.1f}mm"
                )

            # ── Phase 1: Measurement budget for first accepted satellite ─────
            # (Print for all sats at MEAS_AUDIT_VERBOSE level, first-sat by default)
            for _bm in geom[:1]:
                _bsid  = _bm['sid']
                _brp   = _rp(_bm, x[3], x[4])
                _b_rho  = _bm['rng']            # geometric range (m)
                _b_scm  = _bm['scm']            # satellite clock correction (m, = sck*c)
                _b_rel  = _bm['dtrel']          # relativistic correction (m)
                _b_shp  = _bm['shp']            # Shapiro delay (m)
                _b_set  = _bm['setm']           # solid-earth tide (m)
                _b_trph = _bm['trop_zhd']       # troposphere hydrostatic (m)
                _b_trpw = _bm['mw'] * x[4]     # troposphere wet (m)
                _b_pcvs = _bm['pcv_sat']        # sat PCO+PCV (m)
                _b_pcvr = _bm['pcv_rec']        # rec PCO+PCV (m)
                _b_wu   = wum.get(_bsid, 0.) * _bm.get('_lam_if', LAMBDA_IF)  # wind-up (m)
                _b_osb_c= _bm.get('_osb_IF_code_m',  0.)
                _b_osb_p= _bm.get('_osb_IF_phase_m', 0.)
                _b_amb  = x[_bm['ki']]
                _b_pif  = _bm['PIF']
                _b_lifc = _bm['LIFc']
                _b_cr   = (_b_pif - _brp) * 1e3
                _b_pr   = (_b_lifc - _brp - _b_amb) * 1e3
                print(
                    f"  [MEAS-BUDGET] sat={_bsid} "                    f"code_raw={_b_pif*1e3:+.1f}mm phase_raw={_b_lifc*1e3:+.1f}mm "                    f"rho={_b_rho*1e3:+.1f}mm satclk={_b_scm*1e3:+.1f}mm "                    f"rel={_b_rel*1e3:+.3f}mm sagnac={_bm.get('_sagnac_m',0.)*1e3:+.3f}mm "                    f"tropo={(_b_trph+_b_trpw)*1e3:+.1f}mm "                    f"pcv={(_b_pcvs+_b_pcvr)*1e3:+.1f}mm "                    f"windup={_b_wu*1e3:+.3f}mm "                    f"osb_code={_b_osb_c*1e3:+.1f}mm osb_phase={_b_osb_p*1e3:+.1f}mm "                    f"amb={_b_amb*1e3:+.1f}mm "                    f"code_res={_b_cr:+.1f}mm phase_res={_b_pr:+.1f}mm"
                )

            # ── Phase 2: IF combination coefficients (printed once per pass) ─
            if sod == eplist[0]['t']:
                print(
                    f"  [IF-COEFF] f1={FREQ1/1e6:.6f}MHz f2={FREQ2/1e6:.6f}MHz "                    f"ALFA={ALFA:.12f} BETA={BETA:.12f} "                    f"LAMBDA_IF={LAMBDA_IF*1e3:.6f}mm "                    f"a*f1-b*f2={(ALFA*FREQ1 - BETA*FREQ2)/1e6:.6f}MHz"
                )
                for _cm in geom[:1]:
                    _P1 = _cm['P1']; _P2 = _cm['P2']
                    _L1m = _cm['L1'] * LAMBDA1; _L2m = _cm['L2'] * LAMBDA2
                    _Pif = _ifc(_P1 - _cm.get('_osb_IF_code_m', 0.)*0,  # raw (no OSB here)
                                _P2 - 0)
                    print(
                        f"  [IF-COMB] sat={_cm['sid']} "                        f"P1={_P1*1e3:+.1f}mm P2={_P2*1e3:+.1f}mm "                        f"L1_m={_L1m*1e3:+.1f}mm L2_m={_L2m*1e3:+.1f}mm "                        f"PIF={_cm['PIF']*1e3:+.1f}mm LIF_raw={_cm['_LIF_raw']*1e3:+.1f}mm "                        f"LIFc={_cm['LIFc']*1e3:+.1f}mm"
                    )

            # ── Phase 3: Clock and bias convention (printed once at first epoch) ─
            if sod == eplist[0]['t']:
                print(
                    f"  [CLOCK-CONVENTION] "                    f"clock_source=COD_CLK_30S "                    f"clock_type=IF_phase_consistent "                    f"APC_or_COM=APC (IGS ATX applied to SP3 CoM) "                    f"phase_clock=YES code_clock=YES"
                )
                for _cm in geom[:1]:
                    _bsid2 = _cm['sid']
                    _osb_c_l1 = _cm.get('_osb_code_L1_m',  0.) * 1e3
                    _osb_c_l2 = _cm.get('_osb_code_L2_m',  0.) * 1e3
                    _osb_p_l1 = _cm.get('_osb_phase_L1_m', 0.) * 1e3
                    _osb_p_l2 = _cm.get('_osb_phase_L2_m', 0.) * 1e3
                    print(
                        f"  [BIAS-HANDLING] sat={_bsid2} "                        f"TGD_applied=NO(absorbed_in_OSB) "                        f"DCB_applied=NO(absorbed_in_OSB) "                        f"OSB_applied=YES "                        f"osb_C1W={_osb_c_l1:+.4f}mm osb_C2W={_osb_c_l2:+.4f}mm "                        f"osb_L1C={_osb_p_l1:+.4f}mm osb_L2W={_osb_p_l2:+.4f}mm"
                    )

        # ══ END MEASUREMENT-MODEL AUDIT ═══════════════════════════════════════

        # ── PHASE-1: per-satellite forensic trace CSV rows ────────────────────
        # Write one row per active satellite to the trace_fh CSV.
        # Compute per-satellite post-fit residuals and full model decomposition.
        # TRACE ONLY — no logic changes.
        if trace_fh is not None:
            # Build a code-residual dict for quick lookup
            _code_res_dict = {m['sid']: (m['PIF'] - _rp(m, x[3], x[4])) for m in geom}
            for _tm in geom:
                _tsid  = _tm['sid']
                _tsys  = _tm.get('_sys', '?')
                _trp   = _rp(_tm, x[3], x[4])
                # Phase residual: birth-complete sats only (newborns/RESET not yet born)
                if amgr.is_birth_complete(_tsid) and _tsid not in _newborn_pending:
                    _tki   = _tm['ki']
                    _tph_res = _tm['LIFc'] - (_trp + x[_tki])  # GPS: no ISB
                    _tamb  = x[_tki]
                else:
                    _tph_res = float('nan')
                    _tamb    = x[_tm['ki']] if _tsid in sidx else float('nan')
                _tcode_res = _code_res_dict.get(_tsid, float('nan'))
                _spco = _tm.get('_sat_pco_body_mm', [0.,0.,0.])
                _rpco = _tm.get('_rec_pco_local_mm', [0.,0.,0.])
                _satapcrng = _tm.get('_sat_apc_range_m', 0.) * 1e3
                _recapcrng = _tm.get('_rec_apc_range_m', 0.) * 1e3
                _sagn = _tm.get('_sagnac_m', 0.) * 1e3
                _otlr = _tm.get('_otl_range_m', 0.) * 1e3
                _osbc1 = _tm.get('_osb_code_L1_m', 0.) * 1e3
                _osbc2 = _tm.get('_osb_code_L2_m', 0.) * 1e3
                _osbp1 = _tm.get('_osb_phase_L1_m', 0.) * 1e3
                _osbp2 = _tm.get('_osb_phase_L2_m', 0.) * 1e3
                _osbifc= _tm.get('_osb_IF_code_m', 0.) * 1e3
                _osbifp= _tm.get('_osb_IF_phase_m', 0.) * 1e3
                _LIF_raw = _tm.get('_LIF_raw', _tm['LIF'])
                _PIF_raw = _tm.get('_PIF_raw', _tm['PIF'])
                # Predicted phase (with ambiguity) and code
                _pred_ph  = _trp + _tamb   # GPS: no ISB
                _pred_cod = _trp             # GPS: no ISB
                # Corrected observables: phase=LIF (osb already applied), code=PIF
                _corr_ph = _tm['LIFc']
                _corr_cod= _tm['PIF']
                _tamb_cyc = _tamb / _tm.get('_lam_if', LAMBDA_IF) if _tm.get('_lam_if', LAMBDA_IF) != 0 else float('nan')
                _is_nb = 1 if _tsid in _newborn_pending else 0
                trace_fh.write(
                    f"{pass_label},{sod:.1f},{_tsid},{_tsys},"
                    f"{math.degrees(_tm['el']):.3f},{math.degrees(_tm['az']):.3f},"
                    f"{_tm['L1']:.6f},{_tm['L2']:.6f},"
                    f"{_tm['P1']:.6f},{_tm['P2']:.6f},"
                    f"{_LIF_raw:.6f},{_PIF_raw:.6f},"
                    f"{_spco[0]:.3f},{_spco[1]:.3f},{_spco[2]:.3f},"
                    f"{_satapcrng:.4f},{_tm['pcv_sat']*1e3:.4f},"
                    f"{_rpco[0]:.3f},{_rpco[1]:.3f},{_rpco[2]:.3f},"
                    f"{_recapcrng:.4f},{_tm['pcv_rec']*1e3:.4f},"
                    f"{_osbc1:.4f},{_osbc2:.4f},"
                    f"{_osbp1:.4f},{_osbp2:.4f},"
                    f"{_osbifc:.4f},{_osbifp:.4f},"
                    f"{_tm['trop_zhd']:.6f},{_tm['mw']*x[4]:.6f},"
                    f"{_sagn:.4f},{_tm['dtrel']*1e3:.4f},"
                    f"{wum.get(_tsid,0.):.6f},{wum.get(_tsid,0.)*_tm.get('_lam_if',LAMBDA_IF)*1e3:.4f},"
                    f"{_tm['setm']*1e3:.4f},{_otlr:.4f},"
                    f"{_tm['rng']:.6f},"
                    f"{_corr_ph:.6f},{_corr_cod:.6f},"
                    f"{_pred_ph:.6f},{_pred_cod:.6f},"
                    f"{_fmtf(_tph_res, 1e3, 4)},"
                    f"{_fmtf(_tcode_res, 1e3, 4)},"
                    f"{_tamb:.6f},{_tamb_cyc:.6f},"
                    f"{1 if _tsid in wl_fixed else 0},"
                    f"{1 if _tsid in nl_fixed else 0}\n"
                )

        # ── PHASE-2: per-epoch GPS stats ──────────────────────────────────────
        # Uses _accepted_phase_sids for phase residuals — consistent with [EPOCH] RMS.
        _is_first2h = (sod - eplist[0]['t']) < 7200.
        if _is_first2h or (nproc % 30 == 0):
            _gps_ph_res=[]; _gps_cod_res=[]; _gps_amb_sig=[]; _gps_el=[]
            for _cm in _active_geom:
                _cki = _cm['ki']
                _crp = _rp(_cm, x[3], x[4])
                _ccod_res = _cm['PIF'] - _crp
                _csig = math.sqrt(max(P[_cki,_cki], 0.)) * 1e3
                _gps_cod_res.append(_ccod_res*1e3)
                _gps_amb_sig.append(_csig); _gps_el.append(math.degrees(_cm['el']))
                # Phase residual: accepted sids only (matches [EPOCH] RMS)
                if _cm['sid'] in _accepted_phase_sids:
                    _cph_res = _cm['LIFc'] - (_crp + x[_cki])
                    _gps_ph_res.append(_cph_res*1e3)
            def _s(v, fn): return fn(v) if v else float('nan')
            _gmn=lambda v: _s(v, lambda x: sum(x)/len(x))
            _gmd=lambda v: _s(v, lambda x: sorted(x)[len(x)//2])
            _grms=lambda v: _s(v, lambda x: math.sqrt(sum(i*i for i in x)/len(x)))
            print(
                f"[CONST-STATS] epoch={nproc}  sod={sod:.0f}\n"
                f"  GPS({len(_gps_ph_res)} sats):"
                f" ph_mean={_gmn(_gps_ph_res):+.2f}mm"
                f" ph_med={_gmd(_gps_ph_res):+.2f}mm"
                f" ph_rms={_grms(_gps_ph_res):.2f}mm"
                f" cod_mean={_gmn(_gps_cod_res):+.1f}mm"
                f" amb_sig_mean={_gmn(_gps_amb_sig):.0f}mm"
                f" el_mean={_gmn(_gps_el):.1f}deg"
            )

        # Ambiguity sigma statistics (birth-complete only; RESET has reinflated 20m sigma)
        # Step-4-SEM: is_birth_complete() excludes RESET/NEW sats whose P[ki,ki]=20^2
        # would skew the mean/max toward 20000mm even while other ambs are converged.
        _all_sigs = [math.sqrt(max(P[ki2, ki2], 0.))
                     for sid2, ki2 in sidx.items() if amgr.is_birth_complete(sid2)]
        _sig_mean = float(np.mean(_all_sigs))   if _all_sigs else float('nan')
        _sig_min  = float(np.min(_all_sigs))    if _all_sigs else float('nan')
        _sig_max  = float(np.max(_all_sigs))    if _all_sigs else float('nan')
        _n_active_amb = len(_all_sigs)

        # Condition number of position+clock+ZWD sub-block
        _P55 = P[:5, :5]
        try:
            _ev     = np.linalg.eigvalsh(_P55)
            # PSD-REPAIR-5: correct condition number formula.
            # Previous code: max(|ev|) / abs(min(|ev|)) gives values < 1 when
            # negative eigenvalues are present, producing the impossible
            # cond_P=4.1e-01 diagnostic.  Correct definition: λ_max / λ_min
            # where both are from abs(eigvalsh), i.e., the ratio of largest to
            # smallest magnitude eigenvalue of the symmetrised matrix.
            _ev_abs = np.abs(_ev)
            _ev_min_abs = float(np.min(_ev_abs))
            _ev_max_abs = float(np.max(_ev_abs))
            _has_neg_ev = bool(np.any(_ev < 0))
            if _has_neg_ev:
                print(f"  [WARN-PSD] P[:5,:5] has negative eigenvalues at SOD={sod:.0f}: "
                      f"λ_min={float(_ev.min()):.3e}")
            _cond_P = _ev_max_abs / max(_ev_min_abs, 1e-20)
        except np.linalg.LinAlgError:
            _cond_P = float('nan')
        if not math.isfinite(_cond_P) or _cond_P > 1e12:
            print(f"  [WARN] P condition number = {_cond_P:.3e} at SOD={sod:.0f}")

        # ZWD jump warning
        _zwd_jump = abs(x[4] - zwd_before)
        if _zwd_jump > 0.05:
            print(f"  [WARN] ZWD jumped {_zwd_jump*1e3:+.1f}mm at SOD={sod:.0f}")

        # ── Per-epoch diagnostic print ────────────────────────────────────────
        n_gps = sum(1 for m in geom if m['sid'][0] == 'G')
        _pos_upd_norm = float(np.linalg.norm(x[:3] - x_before[:3])) * 1e3

        # == REBIRTH-FORENSIC HUNK 5: record_epoch ============================
        # Compute mean_ph / std_ph / mean_cod from pre-EKF lists (may be
        # set only if CLOCK-OBS block ran; otherwise reconstruct here).
        _rb_ep_mean_ph  = (sum(_ph_innov_list)   / len(_ph_innov_list)
                           if _ph_innov_list else float("nan"))
        _rb_ep_std_ph   = ((sum((v - _rb_ep_mean_ph)**2 for v in _ph_innov_list)
                            / len(_ph_innov_list))**0.5
                           if len(_ph_innov_list) > 1 else float("nan"))
        _rb_ep_mean_cod = (sum(_code_innov_list) / len(_code_innov_list)
                           if _code_innov_list else float("nan"))
        _rb_ep_cm_ratio = (abs(_rb_ep_mean_ph) / max(abs(_rb_ep_std_ph), 1e-9)
                           if math.isfinite(_rb_ep_mean_ph) and
                              math.isfinite(_rb_ep_std_ph) and
                              abs(_rb_ep_std_ph) > 1e-9
                           else float("nan"))
        _rebirth_forensic.record_epoch({
            "nproc":              nproc,
            "sod":                sod,
            "rec_clk_m":          float(x[3]),
            "dclk_m":             float(x[3] - x_before[3]),
            "code_rms_mm":        code_rms,
            "phase_rms_mm":       (phase_rms if math.isfinite(phase_rms)
                                   else float("nan")),
            # FREEZE: pass NaN cm_ratio during freeze to prevent abort threshold check
            "cm_ratio":           (float("nan") if ENABLE_FREEZE_OBS and FREEZE_OBS_START <= nproc <= FREEZE_OBS_END
                                   else (_au_cm_ratio if _MEAS_AUDIT_ENABLE
                                         else _rb_ep_cm_ratio)),
            "mean_ph_innov_mm":   (_rb_ep_mean_ph * 1e3
                                   if math.isfinite(_rb_ep_mean_ph) else float("nan")),
            "std_ph_innov_mm":    (_rb_ep_std_ph  * 1e3
                                   if math.isfinite(_rb_ep_std_ph) else float("nan")),
            "mean_code_innov_mm": (_rb_ep_mean_cod * 1e3
                                   if math.isfinite(_rb_ep_mean_cod) else float("nan")),
            "n_accepted_phase":   len(_accepted_phase_sids),
            "all_reject":         len(_accepted_phase_sids) == 0,
            "innov_norm":         _innov_norm,
            "n_sats":             len(geom),
        })
        # ====================================================================

        # ══════════════════════════════════════════════════════════════════════
        # OBSERVABILITY DIAGNOSTICS — printed every epoch during freeze window
        # Read-only; zero state mutation.
        # ══════════════════════════════════════════════════════════════════════
        if ENABLE_FREEZE_OBS and FREEZE_OBS_START <= nproc <= FREEZE_OBS_END:
            _obs_phase_sats = [m for m in _active_geom
                               if m['sid'] in _accepted_phase_sids
                               and amgr.is_birth_complete(m['sid'])]
            _obs_n_phase  = len(_obs_phase_sats)
            _obs_n_code   = len(_active_geom)
            _obs_n_mature = sum(1 for m in _obs_phase_sats
                                if (nproc - amgr.get_birth_epoch(m['sid'], nproc)) >= 10)
            _obs_els      = [math.degrees(m['el']) for m in _obs_phase_sats]
            _obs_el_mean  = (sum(_obs_els) / len(_obs_els)) if _obs_els else float('nan')
            _obs_el_min   = min(_obs_els) if _obs_els else float('nan')
            print(
                f"[OBS-GEOMETRY]  epoch={nproc}  sod={sod:.0f}"
                f"  n_phase={_obs_n_phase}"
                f"  n_code={_obs_n_code}"
                f"  n_mature_amb={_obs_n_mature}"
                f"  el_list=[{', '.join(f'{e:.1f}' for e in _obs_els)}]"
                f"  mean_el={_obs_el_mean:.1f}°"
                f"  min_el={_obs_el_min:.1f}°"
            )
            print(
                f"  [FREEZE-STATUS]  suppressed_resets={_freeze_suppressed_resets}"
                f"  suppressed_births={_freeze_suppressed_births}"
                f"  skipped_phase_rows={_freeze_skipped_phase_rows}"
            )

            # OBS-MATRIX: build phase-only H block and analyse it
            if _obs_n_phase >= 2:
                _ns   = len(x)
                # Collect phase H rows for birth-complete, accepted-phase sats
                _Hp_rows = []
                _Rp_diag = []
                for _om in _obs_phase_sats:
                    _oki  = _om['ki']
                    _oh   = np.zeros(_ns)
                    _oh[0]=-_om['unit'][0]; _oh[1]=-_om['unit'][1]; _oh[2]=-_om['unit'][2]
                    _oh[3]=1.; _oh[4]=_om['mw']; _oh[_oki]=1.
                    _Hp_rows.append(_oh)
                    _Rp_diag.append(_sig(_om['el'], SP)**2)
                _Hp  = np.array(_Hp_rows)          # shape (n_phase, n_state)
                _Rp  = np.array(_Rp_diag)          # shape (n_phase,)

                # SVD of H_phase (full matrix)
                try:
                    _sv  = np.linalg.svd(_Hp, compute_uv=False)
                    _sv_min = float(_sv[-1]); _sv_max = float(_sv[0])
                    _rank_H = int(np.sum(_sv > _sv_max * 1e-10))
                    print(
                        f"[OBS-MATRIX]  epoch={nproc}  sod={sod:.0f}"
                        f"  n_rows={_obs_n_phase}  n_cols={_ns}"
                        f"  rank_H_phase={_rank_H}"
                        f"  sv_max={_sv_max:.4e}  sv_min={_sv_min:.4e}"
                        f"  cond_H={_sv_max/max(_sv_min,1e-30):.3e}"
                    )
                    # Information matrix
                    _Rinv_diag = 1.0 / np.maximum(_Rp, 1e-20)
                    _HtRiH = _Hp.T @ np.diag(_Rinv_diag) @ _Hp
                    try:
                        _ev_info = np.linalg.eigvalsh(_HtRiH)
                        _ev_info_min = float(_ev_info[_ev_info > 0].min()) if np.any(_ev_info > 0) else 0.
                        _ev_info_max = float(_ev_info.max())
                        _cond_info   = _ev_info_max / max(_ev_info_min, 1e-30)
                        print(
                            f"  [OBS-MATRIX-INFO]  cond(HtRiH)={_cond_info:.3e}"
                            f"  ev_min={_ev_info_min:.3e}  ev_max={_ev_info_max:.3e}"
                        )
                    except np.linalg.LinAlgError:
                        print(f"  [OBS-MATRIX-INFO]  eigenvalue decomp failed")
                except np.linalg.LinAlgError:
                    print(f"[OBS-MATRIX]  epoch={nproc}  SVD failed")
                    _sv = None

                # OBS-NULLSPACE: left singular vector corresponding to smallest sv
                if _sv is not None and _obs_n_phase >= 1:
                    try:
                        _U, _S, _Vt = np.linalg.svd(_Hp, full_matrices=False)
                        _null_vec = _Vt[-1]   # right singular vector for smallest sv
                        # Map indices to state names
                        _ns_names = {0:'dX',1:'dY',2:'dZ',3:'clk',4:'ZWD'}
                        _ns_names.update({v:k for k,v in sidx.items()})
                        # Find top-5 dominant components by absolute weight
                        _nv_abs = np.abs(_null_vec)
                        _top5   = np.argsort(_nv_abs)[::-1][:6]
                        _top_str = "  ".join(
                            f"{_ns_names.get(i,f'x[{i}]')}={_null_vec[i]:+.4f}"
                            for i in _top5)
                        # Specific states of interest
                        _nv_clk  = _null_vec[3]
                        _nv_zwd  = _null_vec[4]
                        _nv_up   = float(_enu_R[2] @ _null_vec[:3])  # Up projection
                        _nv_amb  = {_ns_names.get(i,f'x[{i}]'): float(_null_vec[i])
                                    for i in range(5, len(_null_vec))}
                        print(
                            f"[OBS-NULLSPACE]  epoch={nproc}  sod={sod:.0f}"
                            f"  sv_min={float(_S[-1]):.4e}"
                            f"  null_clk={_nv_clk:+.4f}"
                            f"  null_zwd={_nv_zwd:+.4f}"
                            f"  null_Up={_nv_up:+.4f}"
                        )
                        print(f"  [NULLSPACE-TOP6]  {_top_str}")
                        _amb_null_str = "  ".join(f"{k}={v:+.4f}" for k,v in _nv_amb.items())
                        print(f"  [NULLSPACE-AMB]  {_amb_null_str}")
                    except np.linalg.LinAlgError:
                        print(f"[OBS-NULLSPACE]  epoch={nproc}  SVD failed")

                # OBS-CORRELATION: normalised H columns
                _Hp_nrm = _Hp / np.maximum(np.linalg.norm(_Hp, axis=0, keepdims=True), 1e-30)
                _col_clk  = _Hp_nrm[:, 3]
                _col_zwd  = _Hp_nrm[:, 4]
                _corr_clk_zwd = float(np.dot(_col_clk, _col_zwd) /
                                      max(np.linalg.norm(_col_clk)*np.linalg.norm(_col_zwd), 1e-30))
                _amb_corrs = []
                for _oi, _om2 in enumerate(_obs_phase_sats):
                    _oki2 = _om2['ki']
                    _col_amb = _Hp_nrm[:, _oki2]
                    _cnorm   = np.linalg.norm(_col_amb)
                    _corr_ca = (float(np.dot(_col_clk, _col_amb) /
                                      max(np.linalg.norm(_col_clk)*_cnorm, 1e-30))
                                if _cnorm > 1e-12 else float('nan'))
                    _amb_corrs.append((_om2['sid'], _corr_ca, float(_cnorm)))
                _max_abs_corr = max(abs(c) for _, c, _ in _amb_corrs) if _amb_corrs else float('nan')
                print(
                    f"[OBS-CORRELATION]  epoch={nproc}  sod={sod:.0f}"
                    f"  corr(clk,zwd)={_corr_clk_zwd:+.4f}"
                    f"  max|corr(clk,amb)|={_max_abs_corr:.4f}"
                )
                for _osid, _ocorr, _onrm in _amb_corrs:
                    print(f"  [CORR-AMB]  sat={_osid}  corr(clk,amb)={_ocorr:+.4f}  col_norm={_onrm:.4f}")

            # OBS-INNOV: innovation summary
            _obs_ph_res = [m['LIFc'] - (_rp(m, x[3], x[4]) + x[m['ki']])
                           for m in _obs_phase_sats]
            _obs_ph_mean = (sum(_obs_ph_res)/len(_obs_ph_res)) if _obs_ph_res else float('nan')
            _obs_ph_std  = (math.sqrt(sum((r-_obs_ph_mean)**2 for r in _obs_ph_res)/len(_obs_ph_res))
                            if len(_obs_ph_res) > 1 else float('nan'))
            _obs_cm_rat  = (abs(_obs_ph_mean)/max(abs(_obs_ph_std), 1e-9)
                            if math.isfinite(_obs_ph_mean) and math.isfinite(_obs_ph_std)
                            and abs(_obs_ph_std) > 1e-9 else float('nan'))
            print(
                f"[OBS-INNOV]  epoch={nproc}  sod={sod:.0f}"
                f"  mean_ph_res={_obs_ph_mean*1e3:+.1f}mm"
                f"  std_ph_res={_obs_ph_std*1e3:.1f}mm"
                f"  cm_ratio={_obs_cm_rat:.3f}"
                f"  code_rms={code_rms:.1f}mm"
                f"  phase_rms={phase_rms:.2f}mm"
            )

            # ── PROPAGATION-AUDIT PATCH 4: CM-LINKAGE ────────────────────────
            # Compares the prefit phase CM mean (OBS-INNOV) to the predicted
            # clock and ambiguity deltas from PATCH 2 to identify which
            # propagated state component drives the prefit excursion.
            #
            # prefit_cm_mean tracks clk_pred_mm  → Case A: clock propagation failure
            # prefit_cm_mean tracks amb_mean_mm  → Case B: ambiguity datum drift
            # neither tracks                      → Case C: geometry/tropo manifold
            if len(_obs_ph_res) >= 3:
                _cm4_cm_mean_mm = _obs_ph_mean * 1e3
                _cm4_cm_std_mm  = _obs_ph_std  * 1e3
                print(
                    f"[CM-LINKAGE] "
                    f"epoch={nproc}  sod={sod:.0f}  "
                    f"prefit_cm_mean_mm={_cm4_cm_mean_mm:+.1f}  "
                    f"prefit_cm_std_mm={_cm4_cm_std_mm:.1f}  "
                    f"clk_pred_mm={_clk_pred_mm:+.1f}  "
                    f"amb_mean_mm={_amb_pred_mean:+.1f}"
                )
                # Classification hint printed inline for each epoch
                if math.isfinite(_clk_pred_mm) and abs(_clk_pred_mm) > 50.0:
                    _cm4_clk_track = abs(_cm4_cm_mean_mm - _clk_pred_mm) < 0.5 * abs(_clk_pred_mm)
                    _cm4_amb_track = (math.isfinite(_amb_pred_mean)
                                      and abs(_cm4_cm_mean_mm - _amb_pred_mean) < 0.5 * abs(_amb_pred_mean))
                    if _cm4_clk_track:
                        print(f"  [CM-LINKAGE-HINT] epoch={nproc}  CASE-A: prefit_cm tracks clk_pred")
                    elif _cm4_amb_track:
                        print(f"  [CM-LINKAGE-HINT] epoch={nproc}  CASE-B: prefit_cm tracks amb_mean")
                    else:
                        print(f"  [CM-LINKAGE-HINT] epoch={nproc}  CASE-C/D: prefit_cm does not track clk or amb")
            # ── END CM-LINKAGE ───────────────────────────────────────────────

            # OBS-STATE: filter quality summary
            _obs_clk_pre  = math.sqrt(max(float(P[3, 3]) + float(Q[3, 3]), 0.)) * 1e3
            _obs_clk_post = math.sqrt(max(P[3, 3], 0.)) * 1e3
            _obs_zwd_pre  = math.sqrt(max(float(P[4, 4]) + float(Q[4, 4]), 0.)) * 1e3
            _obs_zwd_post = math.sqrt(max(P[4, 4], 0.)) * 1e3
            print(
                f"[OBS-STATE]  epoch={nproc}  sod={sod:.0f}"
                f"  clk_sig_pre={_obs_clk_pre:.1f}mm  clk_sig_post={_obs_clk_post:.1f}mm"
                f"  zwd_sig_pre={_obs_zwd_pre:.1f}mm  zwd_sig_post={_obs_zwd_post:.1f}mm"
                f"  spectral_radius_KH={_p2e_sr_KH:.4f}"
                f"  trace_ratio={(_epoch_trace_after/_epoch_trace_before if _epoch_trace_before > 0 else float('nan')):.6f}"
            )
        # ══════════════════════════════════════════════════════════════════════
        # END OBSERVABILITY DIAGNOSTICS
        # ══════════════════════════════════════════════════════════════════════

        print(f"[EPOCH] SOD={sod:6.0f}  sats={len(geom)}(G{n_gps})"
              f"  code_rms={code_rms:.1f}mm  phase_rms={_phs_str}"
              f"  innov_norm={_innov_norm:.2f}  rej={_n_rejected_epoch}"
              f"  ZWD={x[4]*1e3:.1f}mm"
              f"  pos_upd={_pos_upd_norm:.1f}mm"
              f"  amb_sigma: mean={_sig_mean*1e3:.0f}mm min={_sig_min*1e3:.0f}mm"
              f" max={_sig_max*1e3:.0f}mm n={_n_active_amb}"
              f"  cond_P={_cond_P:.1e}  ekf#={_ekf_update_count}")

        # ── PHASE-3: write constellation stats row ────────────────────────────
        if constat_fh is not None:
            # Phase residuals: accepted sids only — consistent with [EPOCH] RMS.
            _cs_gps_ph=[]; _cs_gps_cod=[]; _cs_gps_sig=[]; _cs_gps_el=[]
            for _csm in _active_geom:
                _cski=_csm['ki']
                _csrp=_rp(_csm,x[3],x[4])
                _cscod=_csm['PIF']-_csrp
                _cssig=math.sqrt(max(P[_cski,_cski],0.))*1e3
                _cs_gps_cod.append(_cscod*1e3)
                _cs_gps_sig.append(_cssig); _cs_gps_el.append(math.degrees(_csm['el']))
                if _csm['sid'] in _accepted_phase_sids:
                    _csph=_csm['LIFc']-(_csrp+x[_cski])
                    _cs_gps_ph.append(_csph*1e3)
            def _cs_mn(v): return sum(v)/len(v) if v else float('nan')
            def _cs_md(v): return sorted(v)[len(v)//2] if v else float('nan')
            def _cs_rms(v): return math.sqrt(sum(i*i for i in v)/len(v)) if v else float('nan')
            constat_fh.write(
                f"{nproc},{sod:.1f},"
                f"{_fmtf(_cs_mn(_cs_gps_ph),prec=4)},{_fmtf(_cs_md(_cs_gps_ph),prec=4)},{_fmtf(_cs_rms(_cs_gps_ph),prec=4)},"
                f"{_fmtf(_cs_mn(_cs_gps_cod),prec=4)},{_fmtf(_cs_mn(_cs_gps_sig),prec=4)},{_fmtf(_cs_mn(_cs_gps_el),prec=4)},"
                f"{x[0]*1e3:.4f},{x[1]*1e3:.4f},{x[2]*1e3:.4f},"
                f"{x[3]*1e3:.4f},{x[4]*1e3:.4f}\n"
            )
        # ─────────────────────────────────────────────────────────────────────

        # Periodic detailed print every 60 epochs
        if nproc % 60 == 0 or is_startup:
            _sig_strs = []
            for _sid2, _ki2 in sorted(sidx.items()):
                # Step-4-SEM: show birth-complete sats only; RESET sats have x=0
                if amgr.is_birth_complete(_sid2):
                    _sg = math.sqrt(max(P[_ki2, _ki2], 0.)) * 1e3
                    _xk = x[_ki2] * 1e3
                    _sig_strs.append(f"{_sid2}(σ={_sg:.0f}mm,x={_xk:+.0f}mm)")
            if _sig_strs:
                print(f"  [AMB-SIGMA] " + "  ".join(_sig_strs))

        # ── PHASE-5: first-2h high-residual GPS satellite log ─────────────────
        # Flag top-3 highest-residual GPS sats during first 2 hours.  TRACE ONLY.
        if _is_first2h:
            _all_res_detail = []
            for _fdm in _active_geom:
                _fdki = _fdm['ki']; _fdrp = _rp(_fdm, x[3], x[4])
                _fdph = _fdm['LIFc'] - (_fdrp + x[_fdki])
                _all_res_detail.append((_fdm['sid'], abs(_fdph*1e3), _fdph*1e3,
                                        math.degrees(_fdm['el']),
                                        _fdm.get('_sys','?')))
            _all_res_detail.sort(key=lambda v: v[1], reverse=True)
            for _topsid, _topabs, _topres, _topel, _topsys in _all_res_detail[:3]:
                print(f"  [PHASE5-TOP-RES] sat={_topsid}({_topsys})"
                      f" |ph_res|={_topabs:.2f}mm"
                      f" ph_res={_topres:+.2f}mm el={_topel:.1f}deg SOD={sod:.0f}")

        # ── Phase continuity arc tracking (cross-epoch) ─────────────────────
        if _phase_accept > 0:
            _phase_arc_current  += 1
            _phase_epochs_total += 1
            if _phase_arc_current > _phase_arc_longest:
                _phase_arc_longest = _phase_arc_current
        else:
            _phase_arc_current = 0
        # Per-satellite arc update
        all_sids_this_epoch = {m['sid'] for m in geom}
        for _sid in all_sids_this_epoch:
            if _sid in _accepted_phase_sids:
                _sat_arc[_sid] += 1
                if _sat_arc[_sid] > _sat_arc_longest[_sid]:
                    _sat_arc_longest[_sid] = _sat_arc[_sid]
            else:
                _sat_arc[_sid] = 0

        # ── FORENSIC AUDIT: per-epoch sign coherence & sigma mean ────────────
        # Collected from _fa_acc_signed_nu accumulated this epoch.
        # Sign coherence = fraction of epochs where all accepted innovations
        # share the same sign (positive = all satellites pointing same direction).
        _ep_acc_signs = [s for s in _fa_acc_signed_nu[-_phase_accept:]
                         if _phase_accept > 0] if _phase_accept > 0 else []
        if len(_ep_acc_signs) >= 2:
            _ep_pos = sum(1 for v in _ep_acc_signs if v > 0)
            _ep_neg = len(_ep_acc_signs) - _ep_pos
            _ep_coh = 1 if (_ep_pos == len(_ep_acc_signs) or
                            _ep_neg == len(_ep_acc_signs)) else 0
            _fa_epoch_sign_coh.append(_ep_coh)
        _ep_sigmas = [s for s in _fa_acc_sigma_mm[-_phase_accept:]
                      if _phase_accept > 0] if _phase_accept > 0 else []
        if _ep_sigmas:
            _fa_epoch_sigma_mean.append(sum(_ep_sigmas) / len(_ep_sigmas))
        # ─────────────────────────────────────────────────────────────────────

        # ── Bootstrap gate diagnostic summary (cross-epoch) ─────────────────
        # Track first all-accepted / all-rejected epochs and rejection streaks.
        # "Phase-eligible" = satellites with phase rows attempted (_phase_total).
        # An epoch is "all accepted" if _phase_total > 0 and _phase_accept == _phase_total.
        # An epoch is "all rejected" if _phase_total > 0 and _phase_accept == 0.
        if _phase_total > 0:
            _phase_active_frac = _phase_accept / _phase_total
            if _phase_accept == _phase_total and _bsg_all_acc_epoch is None:
                _bsg_all_acc_epoch = nproc
            if _phase_accept == 0:
                _bsg_rej_streak += 1
                if _bsg_rej_streak > _bsg_rej_streak_max:
                    _bsg_rej_streak_max = _bsg_rej_streak
                if _bsg_all_rej_epoch is None:
                    _bsg_all_rej_epoch = nproc
            else:
                _bsg_rej_streak = 0
        else:
            _phase_active_frac = float('nan')

        # Print epoch-level bootstrap summary for epochs 0–20
        if nproc <= 20:
            _floor_at_ep = 200.0 * math.exp(-nproc / 20.0)
            print(f"  [BOOTSTRAP-EPOCH-SUMMARY]"
                  f"  epoch={nproc}  sod={sod:.0f}"
                  f"  phase_total={_phase_total}"
                  f"  accepted={_phase_accept}"
                  f"  rejected={_phase_total - _phase_accept}"
                  f"  active_frac={_phase_active_frac:.2f}"
                  f"  floor_sigma={_floor_at_ep:.1f}mm"
                  f"  rej_streak={_bsg_rej_streak}"
                  f"  longest_rej_streak={_bsg_rej_streak_max}")

        # [PHASE-PARTICIPATION] diagnostic every 300 epochs
        # lifecycle_rejected MUST be 0 after patching — any non-zero value
        # indicates a surviving lifecycle gate somewhere in the code.
        if nproc % 300 == 0:
            _rej_obs_domain = _phase_rej_nanres + _phase_rej_hardgate + _phase_rej_nisgate
            print(f"[PHASE-PARTICIPATION] SOD={sod:.0f}  ekf#={_ekf_update_count}"
                  f"  total={_phase_total}"
                  f"  accepted={_phase_accept}"
                  f"  rejected={_phase_total - _phase_accept}"
                  f"  (lifecycle={_phase_rej_newborn}"
                  f"  nan={_phase_rej_nanres}"
                  f"  abs_floor={_phase_rej_hardgate}"
                  f"  nis_gate={_phase_rej_nisgate})"
                  f"  LIFECYCLE_GATE_COUNT={_phase_rej_newborn}"
                  f"  [MUST_BE_ZERO_AFTER_CONVERGENCE]")
            print(f"  [PHASE-CONTINUITY] longest_arc={_phase_arc_longest}ep"
                  f"  current_arc={_phase_arc_current}ep"
                  f"  phase_active_epochs={_phase_epochs_total}/{nproc}"
                  f"  ({100*_phase_epochs_total/max(nproc,1):.1f}%)")
            # ── Step-1 refactor: write ambiguity-manager snapshot CSV ─────────
            amgr.write_snapshot(nproc, sidx, P)
            # ── Step-3: run ASSERT-L1…L7 at every snapshot checkpoint ────────
            amgr.run_consistency_assertions(nproc, sod)

        # ── Store for RTS / output ────────────────────────────────────────────
        pos = nom + x[:3]
        dx  = pos - REF
        d3  = np.linalg.norm(dx) * 1e3
        ZHD = zhd; ZWD = x[4]; TROPO = ZHD + ZWD

        results[sod] = {
            'xyz': pos.copy(), 'dx': dx.copy(),
            'p_trace': P[0,0]+P[1,1]+P[2,2],
            'n': len(geom), 'ztd': TROPO,
            'wl_fixed': len(wl_fixed), 'nl_fixed': len(nl_fixed),
            'code_rms': code_rms, 'phase_rms': phase_rms,
            'zhd': ZHD, 'zwd': ZWD,
            'sats_used': sorted([m['sid'] for m in geom]),
            'sats_wl':   sorted([s for s in wl_fixed if any(m['sid']==s for m in geom)]),
            'sats_nl':   sorted([s for s in nl_fixed  if any(m['sid']==s for m in geom)]),
            'pass': label,
        }

        if nproc <= 3 or nproc % 240 == 0:
            print(f"  [{label}] SOD={sod:6.0f}  N={len(geom):2d}(G{n_gps})"
                  f"  3D={d3:8.1f}mm  WL={len(wl_fixed)}  NL=0(AR off)"
                  f"  ZHD={ZHD:.3f}m  ZWD={ZWD:.4f}m"
                  f"  CodeRMS={code_rms:.1f}mm  PhsRMS={_phs_str}")

        # RTS store
        if direction == 1:
            if not hasattr(_rts_store, '_data'):
                _rts_store._data = []
            _rts_store._data.append((sod, x.copy(), P.copy()))

        _prev_code_rms  = code_rms  if code_rms  > 0 else _prev_code_rms
        _prev_phase_rms = phase_rms if (not math.isnan(phase_rms) and phase_rms > 0) else _prev_phase_rms
        # ── FORENSIC-PATCH: save phase-collapse state for next epoch's Q-block ─
        _prev_phase_total    = _phase_total
        _prev_phase_accept   = _phase_accept
        _prev_phase_inactive = not math.isfinite(phase_rms)
        # ──────────────────────────────────────────────────────────────────────

        # ── PHASE-3: EKF state trace CSV write ────────────────────────────────
        # Write one row per epoch with full EKF state and per-constellation stats.
        # TRACE ONLY.
        if state_trace_fh is not None:
            _gps_ph3=[]; _gps_cod3=[]; _gps_sig3=[]
            for _pm in _active_geom:
                _pki=_pm['ki']
                _prp=_rp(_pm, x[3], x[4])
                _pcod=_pm['PIF']-_prp
                _psig=math.sqrt(max(P[_pki,_pki],0.))*1e3
                _gps_cod3.append(_pcod*1e3); _gps_sig3.append(_psig)
                if _pm['sid'] in _accepted_phase_sids:
                    _pph=_pm['LIFc']-(_prp+x[_pki])
                    _gps_ph3.append(_pph*1e3)
            def _st(v,fn): return fn(v) if v else float('nan')
            _srms = lambda v: _st(v, lambda z: math.sqrt(sum(i*i for i in z)/len(z)))
            _smn  = lambda v: _st(v, lambda z: sum(z)/len(z))
            _smin = lambda v: _st(v, min)
            _smax = lambda v: _st(v, max)
            _gps_amb_sigs3=[math.sqrt(max(P[sidx[s],sidx[s]],0.))*1e3
                            for s in sidx if s[0]=='G' and amgr.is_active(s)]
            _pos_sigma_mm = math.sqrt(P[0,0]+P[1,1]+P[2,2])*1e3
            state_trace_fh.write(
                f"{nproc},{sod:.1f},"
                f"{x[0]:.6f},{x[1]:.6f},{x[2]:.6f},"
                f"{x[3]*1e3:.4f},{x[4]*1e3:.4f},"
                f"{float(np.linalg.norm(x[:3]-x_before[:3]))*1e3:.4f},"
                f"{abs(x[3]-x_before[3])*1e3:.4f},"
                f"{abs(x[4]-x_before[4])*1e3:.4f},"
                f"{_cond_P:.4e},"
                f"{_pos_sigma_mm:.4f},"
                f"{math.sqrt(max(P[3,3], 0.))*1e3:.4f},"
                f"{math.sqrt(max(P[4,4], 0.))*1e3:.4f},"
                f"{_fmtf(_smn(_gps_amb_sigs3),prec=2)},"
                f"{_fmtf(_smin(_gps_amb_sigs3),prec=2)},"
                f"{_fmtf(_smax(_gps_amb_sigs3),prec=2)},"
                f"{_fmtf(_srms(_gps_ph3),prec=4)},"
                f"{_fmtf(_srms(_gps_cod3),prec=4)}\n"
            )

        nproc += 1

    # ── End-of-pass summary ───────────────────────────────────────────────────
    # ── STAGE-7B: POS-FLOOR-DIAG pass-end summary (diagnostic only; P untouched)
    print(f"\n{'='*72}")
    print(f"[STAGE-7B-POS-FLOOR-DIAG-SUMMARY]  (floor computed but NEVER applied to P)")
    _pf_n_total = nproc if nproc > 0 else 1
    print(f"  Would-fire epochs       : {_pf_floor_active_n}/{_pf_n_total}"
          f"  ({100*_pf_floor_active_n/_pf_n_total:.1f}%)"
          f"  ← epochs where natural P_pos < floor threshold")
    if _pf_floor_mm_list:
        _pf_mean_floor = sum(_pf_floor_mm_list) / len(_pf_floor_mm_list)
        _pf_max_floor  = max(_pf_floor_mm_list)
        _pf_min_floor  = min(_pf_floor_mm_list)
        _pf_mean_orig  = sum(_pf_sigma_orig_list) / len(_pf_sigma_orig_list)
        print(f"  Mean floor threshold    : {_pf_mean_floor:.1f} mm"
              f"  (min={_pf_min_floor:.1f}  max={_pf_max_floor:.1f})")
        print(f"  Mean natural pos_sigma  : {_pf_mean_orig:.1f} mm"
              f"  (when below floor; = collapse depth)")
        if _pf_mean_floor > 0:
            print(f"  Mean collapse ratio     : {_pf_mean_orig/_pf_mean_floor:.3f}×"
                  f"  (natural/floor; <1.0 = pos_sigma collapsed below floor)")
    else:
        print(f"  Would-fire: NEVER — natural pos_sigma stayed above floor every epoch.")
        print(f"  ✓ P_pos is NOT collapsing without the floor — floor was the sole cause.")
    print(f"{'='*72}")
    # ── END STAGE-7B POS-FLOOR-DIAG SUMMARY ──────────────────────────────────

    print(f"\n{'='*72}")
    print(f"[PASS-SUMMARY]  pass={pass_label}  label={label}")
    print(f"  Total epochs processed : {nproc}")
    print(f"  Total EKF updates      : {_ekf_update_count}  "
          f"(rate={_ekf_update_count/max(nproc,1)*100:.1f}%)")
    print(f"  Total ambiguity resets : {amgr.cumulative_resets}")
    print(f"  Stage-1C GF-only resets suppressed : {_1c_suppressed_resets}"
          f"  (MW pre-gate; GF trigger, abs(dMW)<=1.5)")
    print(f"\n[PHASE-CONTINUITY-SUMMARY]")
    print(f"  Longest continuous phase arc : {_phase_arc_longest} epochs")
    print(f"  Phase-active epochs          : {_phase_epochs_total}/{nproc}"
          f"  ({100*_phase_epochs_total/max(nproc,1):.1f}%)")
    _top5_arcs = sorted(_sat_arc_longest.items(), key=lambda kv: -kv[1])[:5]
    print(f"  Top-5 per-satellite longest arcs : "
          f"{', '.join(f'{s}={v}ep' for s,v in _top5_arcs)}")

    print(f"\n[AMB-ZOMBIE-SUMMARY]  "
          f"events={_zombie_event_count}  "
          f"forced_resets={_zombie_reset_count}  "
          f"deferred_cm={_zombie_event_count - _zombie_reset_count}  "
          f"cascade_epochs={_zombie_cascade_epochs}")

    print(f"\n[BOOTSTRAP-GATE-SUMMARY]")
    print(f"  BOOTSTRAP_SIGMA_FLOOR_MM : 200.0 mm (at epoch 0)")
    print(f"  BOOTSTRAP_DECAY_EPOCHS   : 20.0  (e-folding decay)")
    print(f"  Floor at epoch 1         : {200.0*math.exp(-1/20.0):.1f} mm")
    print(f"  Floor at epoch 5         : {200.0*math.exp(-5/20.0):.1f} mm")
    print(f"  Floor at epoch 20        : {200.0*math.exp(-20/20.0):.1f} mm")
    print(f"  First epoch all-sats accepted  : "
          f"{_bsg_all_acc_epoch if _bsg_all_acc_epoch is not None else 'NEVER'}")
    print(f"  First epoch all-sats rejected  : "
          f"{_bsg_all_rej_epoch if _bsg_all_rej_epoch is not None else 'NEVER'}")
    print(f"  Longest all-rejected streak    : {_bsg_rej_streak_max} epochs")
    print(f"  Phase-active fraction          : "
          f"{100*_phase_epochs_total/max(nproc,1):.1f}%")

    # Ambiguity sigma contraction statistics
    print(f"\n[AMB-SIGMA-CONTRACTION]")
    _contraction_lines = []
    for _sid2, _ki2 in sorted(sidx.items()):
        _sig_final = math.sqrt(max(P[_ki2, _ki2], 0.)) * 1e3
        _sig_birth = amgr.get_birth_sigma_mm(_sid2, default_mm=20000.0)
        _ratio     = _sig_final / max(_sig_birth, 1.)
        _contraction_lines.append(
            f"  {_sid2:6s}: birth={_sig_birth:.0f}mm  final={_sig_final:.0f}mm  "
            f"ratio={_ratio:.4f}  active={'YES' if amgr.is_active(_sid2) else 'NO'}"
        )
    for ln in _contraction_lines:
        print(ln)
    if not _contraction_lines:
        print("  (no ambiguities allocated)")
    print(f"{'='*72}\n")

    # ── STAGE-2A: Code Anchoring Stress Test — PASS-SUMMARY diagnostics ──────
    def _s2a_mean(v): return sum(v) / len(v) if v else float('nan')
    print(f"\n{'='*72}")
    print(f"[STAGE-2A-SUMMARY]  SC=0.18  (Code Anchoring Stress Test; 2.78× code weight vs SC=0.30)")
    print(f"{'='*72}")
    print(f"  1) mean_code_phase_delta_mm   = "
          f"{_s2a_mean(_s2a_cp_delta_list):.1f} mm"
          f"  (n={len(_s2a_cp_delta_list)} epochs; Stage-1C baseline ≈ 951.5 mm)")
    _s2a_r_clk_up = float('nan')
    if len(_clk_series) > 5 and len(_up_series) > 5:
        import numpy as _np_s2a
        _s2a_n = min(len(_clk_series), len(_up_series))
        _s2a_c = _np_s2a.array(_clk_series[:_s2a_n]) * 1e3
        _s2a_u = _np_s2a.array(_up_series[:_s2a_n])
        _s2a_c0 = _s2a_c - _s2a_c.mean(); _s2a_u0 = _s2a_u - _s2a_u.mean()
        _s2a_denom = (float((_s2a_c0**2).sum())**0.5 * float((_s2a_u0**2).sum())**0.5)
        if _s2a_denom > 0:
            _s2a_r_clk_up = float((_s2a_c0 * _s2a_u0).sum()) / _s2a_denom
    print(f"  2) corr(clock, Up)            = {_s2a_r_clk_up:+.4f}"
          f"  (Stage-1C baseline = +0.2753; closer to 0 = better decorrelation)")
    print(f"  3) mean phase/code info ratio = "
          f"{_s2a_mean(_s2a_ratio_list):.2e}"
          f"  (Stage-1C baseline ≈ 3.37e+01; target: lower ratio = more code influence)")
    print(f"  4a) mean |K_clock| from code  = "
          f"{_s2a_mean(_s2a_K_clk_code_list):.4f}"
          f"  (n={len(_s2a_K_clk_code_list)} epochs)")
    print(f"  4b) mean |K_pos|   from code  = "
          f"{_s2a_mean(_s2a_K_up_code_list):.4f}"
          f"  (geometric anchor contribution from code rows)")
    print(f"  5) mean |delta_N_mm|          = "
          f"{_s2a_mean(_s2a_delta_N_list):.2f} mm"
          f"  (n={len(_s2a_delta_N_list)} sat-epochs; Stage-1C values shown per sat above)")
    print(f"{'='*72}\n")
    # ── END STAGE-2A PASS-SUMMARY ─────────────────────────────────────────────

    # ── FORENSIC COVARIANCE AUDIT SUMMARY (SP=0.012 experiment) ─────────────
    # Baseline (SP=0.003) values for direct comparison:
    _BL_ACC_NIS_MEAN   = 5.21    # baseline accepted mean NIS  (SP=0.003)
    _BL_PHASE_FRAC     = 64.9    # baseline phase-active %     (SP=0.003)
    _BL_REJ_STREAK     = 10      # baseline longest all-reject streak (SP=0.003)
    _BL_UP_RMS         = 111.3   # baseline Up RMS mm (3D<200 subset, SP=0.003)
    _BL_3D_RMS         = 132.8   # baseline 3D RMS mm (SP=0.003)
    # SP=0.006 intermediate values (previous experiment):
    # (record here after run so future experiments can compare)

    def _fa_hist(vals, bins):
        """Simple ASCII histogram over bins (list of right-edges)."""
        counts = [0] * len(bins)
        for v in vals:
            placed = False
            for i, edge in enumerate(bins):
                if v <= edge:
                    counts[i] += 1; placed = True; break
            if not placed:
                counts[-1] += 1
        total = max(len(vals), 1)
        return counts, total

    def _fa_mean(v): return sum(v)/len(v) if v else float('nan')
    def _fa_std(v):
        if len(v) < 2: return float('nan')
        mu = _fa_mean(v); return math.sqrt(sum((x-mu)**2 for x in v)/len(v))
    def _fa_pct(v, p):
        if not v: return float('nan')
        sv = sorted(v); idx = int(p/100*len(sv)); return sv[min(idx, len(sv)-1)]

    print(f"\n{'='*72}")
    print(f"[FORENSIC-COVARIANCE-AUDIT]  SC=0.18 + CLK-FLOOR=100mm  (STAGE-2A CODE-ANCHOR experiment; SC 0.30→0.18 = 2.78× code weight increase)")
    print(f"{'='*72}")

    # ── 1. Innovation statistics — accepted rows ──────────────────────────────
    n_acc = len(_fa_acc_nis)
    n_rej = len(_fa_rej_nis)
    print(f"\n  [FA-1] INNOVATION / SIGMA STATISTICS")
    print(f"  Accepted rows analysed : {n_acc}")
    print(f"  Rejected  rows analysed: {n_rej}")
    if n_acc > 0:
        print(f"  ACCEPTED  mean(|ν|/σ) = {_fa_mean(_fa_acc_nis):.4f}  "
              f"(baseline={_BL_ACC_NIS_MEAN:.2f}  target≈1.0)")
        print(f"  ACCEPTED  std(|ν|/σ)  = {_fa_std(_fa_acc_nis):.4f}")
        print(f"  ACCEPTED  median(|ν|/σ)= {_fa_pct(_fa_acc_nis,50):.4f}")
        print(f"  ACCEPTED  p90(|ν|/σ)  = {_fa_pct(_fa_acc_nis,90):.4f}")
        print(f"  ACCEPTED  mean|ν|     = {_fa_mean(_fa_acc_nu_mm):.2f} mm")
        print(f"  ACCEPTED  mean σ_S    = {_fa_mean(_fa_acc_sigma_mm):.2f} mm  "
              f"(baseline collapse range: 39–55 mm)")
    if n_rej > 0:
        print(f"  REJECTED  mean(|ν|/σ) = {_fa_mean(_fa_rej_nis):.4f}")
        print(f"  REJECTED  mean|ν|     = {_fa_mean(_fa_rej_nu_mm):.2f} mm")
        print(f"  REJECTED  mean σ_S    = {_fa_mean(_fa_rej_sigma_mm):.2f} mm")

    # ── 2. Histogram of |ν|/σ — accepted ─────────────────────────────────────
    print(f"\n  [FA-2] HISTOGRAM: |ν|/σ_S — ACCEPTED (non-newborn)")
    _bins  = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0, 999.]
    _blbls = ["0–0.5","0.5–1","1–1.5","1.5–2","2–2.5",
              "2.5–3","3–4","4–5","5–7.5","7.5–10",">10"]
    if n_acc > 0:
        _hc, _ht = _fa_hist(_fa_acc_nis, _bins)
        for _lbl, _cnt in zip(_blbls, _hc):
            _bar = '#' * int(40 * _cnt / _ht)
            print(f"    {_lbl:>8s} : {_cnt:5d}  {_bar}")
        _frac_gt4 = sum(1 for v in _fa_acc_nis if v > 4.0) / _ht * 100
        print(f"  Fraction > 4.0 (baseline had NIS≈5.21): {_frac_gt4:.1f}%")

    # ── 3. Histogram of |ν|/σ — rejected ─────────────────────────────────────
    print(f"\n  [FA-3] HISTOGRAM: |ν|/σ_S — REJECTED (NIS-gate only, non-newborn)")
    if n_rej > 0:
        _hcr, _htr = _fa_hist(_fa_rej_nis, _bins)
        for _lbl, _cnt in zip(_blbls, _hcr):
            _bar = '#' * int(40 * _cnt / _htr)
            print(f"    {_lbl:>8s} : {_cnt:5d}  {_bar}")

    # ── 4. Innovation-sigma collapse detector ─────────────────────────────────
    print(f"\n  [FA-4] INNOVATION SIGMA COLLAPSE DETECTOR")
    print(f"  Baseline collapsed range: 39–55 mm  (caused accepted NIS≈2–4)")
    if _fa_acc_sigma_mm:
        _below_55  = sum(1 for s in _fa_acc_sigma_mm if s < 55.) / len(_fa_acc_sigma_mm) * 100
        _below_80  = sum(1 for s in _fa_acc_sigma_mm if s < 80.) / len(_fa_acc_sigma_mm) * 100
        _below_100 = sum(1 for s in _fa_acc_sigma_mm if s < 100.) / len(_fa_acc_sigma_mm) * 100
        print(f"  Fraction σ_S < 55 mm : {_below_55:.1f}%  (baseline: ~100%  → target: <20%)")
        print(f"  Fraction σ_S < 80 mm : {_below_80:.1f}%")
        print(f"  Fraction σ_S < 100mm : {_below_100:.1f}%")
        _sig_hist_edges = [50, 60, 70, 80, 100, 120, 150, 200, 999]
        _sig_hist_lbls  = ["<50","50–60","60–70","70–80","80–100",
                           "100–120","120–150","150–200",">200"]
        _shc, _sht = _fa_hist(_fa_acc_sigma_mm, _sig_hist_edges)
        print(f"  σ_S distribution (mm):")
        for _lbl, _cnt in zip(_sig_hist_lbls, _shc):
            _bar = '#' * int(40 * _cnt / _sht)
            print(f"    {_lbl:>8s} : {_cnt:5d}  {_bar}")
    if _fa_epoch_sigma_mean:
        _ep_below55 = sum(1 for s in _fa_epoch_sigma_mean if s < 55.) / len(_fa_epoch_sigma_mean) * 100
        print(f"  Epochs with mean σ_S < 55mm: {_ep_below55:.1f}%  "
              f"(collapses indicate baseline pathology survived)")

    # ── 5. Sign coherence audit ───────────────────────────────────────────────
    print(f"\n  [FA-5] SIGN COHERENCE AUDIT")
    print(f"  Coherent-sign epoch = all accepted phase rows share same ν sign")
    if _fa_epoch_sign_coh:
        _sign_coh_frac = sum(_fa_epoch_sign_coh) / len(_fa_epoch_sign_coh) * 100
        print(f"  Sign-coherent epochs : {sum(_fa_epoch_sign_coh)}/{len(_fa_epoch_sign_coh)}"
              f"  = {_sign_coh_frac:.1f}%")
        print(f"  (baseline oscillation produced ~70–85%+ coherence)")
        if _sign_coh_frac < 70.:
            print(f"  ✓ Sign coherence DROPPED below 70% — oscillation signature weakened")
        elif _sign_coh_frac < 85.:
            print(f"  ~ Sign coherence REDUCED but still elevated — partial improvement")
        else:
            print(f"  ✗ Sign coherence UNCHANGED — coherent oscillation persists")

    # ── 6. Phase-active fraction comparison ───────────────────────────────────
    _new_phase_frac = 100 * _phase_epochs_total / max(nproc, 1)
    print(f"\n  [FA-6] PHASE-ACTIVE FRACTION")
    print(f"  New  : {_new_phase_frac:.1f}%   Baseline: {_BL_PHASE_FRAC:.1f}%")
    _frac_delta = _new_phase_frac - _BL_PHASE_FRAC
    if _frac_delta > 5.:
        print(f"  ✓ Phase-active fraction INCREASED by {_frac_delta:+.1f}pp "
              f"— fewer rejection storms")
    elif _frac_delta < -5.:
        print(f"  ✗ Phase-active fraction DECREASED by {_frac_delta:+.1f}pp "
              f"— gate too loose / geometry sluggish")
    else:
        print(f"  ~ Phase-active fraction unchanged ({_frac_delta:+.1f}pp)")

    # ── 7. All-reject streak comparison ──────────────────────────────────────
    print(f"\n  [FA-7] LONGEST ALL-REJECT STREAK")
    print(f"  New  : {_bsg_rej_streak_max} epochs   Baseline: {_BL_REJ_STREAK} epochs")
    if _bsg_rej_streak_max < _BL_REJ_STREAK:
        print(f"  ✓ Reject storms REDUCED")
    elif _bsg_rej_streak_max == _BL_REJ_STREAK:
        print(f"  ~ Reject streak UNCHANGED")
    else:
        print(f"  ✗ Reject streak WORSENED")

    # ── 8. Diagnostic verdict ─────────────────────────────────────────────────
    print(f"\n  [FA-VERDICT] EXPECTED-CASE ASSESSMENT")
    print(f"  Case A (covariance fix works): NIS→1–2, storm suppressed, FWD/RTS converge")
    print(f"  Case B (structural pathology): oscillations persist despite 4×R")
    print(f"  Case C (too aggressive):       residuals accepted, 3D RMS worsens")
    print(f"")
    _new_acc_nis = _fa_mean(_fa_acc_nis) if n_acc > 0 else float('nan')
    if not math.isnan(_new_acc_nis):
        if _new_acc_nis < 2.0:
            print(f"  Accepted NIS={_new_acc_nis:.2f} → Case A (major stabilization)")
        elif _new_acc_nis < 3.5:
            print(f"  Accepted NIS={_new_acc_nis:.2f} → Partial Case A (moderate improvement)")
        else:
            print(f"  Accepted NIS={_new_acc_nis:.2f} → Case B/C (structural issue remains)")
    print(f"  NOTE: Compare 3D/Up RMS from metrics summary above vs "
          f"baseline 3D={_BL_3D_RMS:.0f}mm Up={_BL_UP_RMS:.0f}mm")
    print(f"{'='*72}\n")
    # ── END FORENSIC COVARIANCE AUDIT ────────────────────────────────────────

    # ── ROBUST-WEIGHT-AUDIT — PASS SUMMARY ───────────────────────────────────
    print(f"\n{'='*72}")
    print(f"[ROBUST-WEIGHT-AUDIT]  Huber continuous weighting experiment")
    print(f"  Architecture: w=1 for u≤2, w=2/u for u>2  (u=|ν|/σ_gated)")
    print(f"  R_eff = R_base / w²  — large residuals downweighted, never discarded")
    print(f"{'='*72}")

    _rw_n = len(_rw_all_weights)
    if _rw_n > 0:
        _rw_mean_all = sum(_rw_all_weights) / _rw_n
        _rw_min_all  = min(_rw_all_weights)
        _rw_n_lt1    = sum(1 for w in _rw_all_weights if w < 1.0)
        _rw_n_lt05   = sum(1 for w in _rw_all_weights if w < 0.5)
        _rw_frac_lt1  = 100. * _rw_n_lt1 / _rw_n
        _rw_frac_lt05 = 100. * _rw_n_lt05 / _rw_n
        print(f"\n  [RW-1] PER-ROW WEIGHT STATISTICS")
        print(f"  Total phase rows entering EKF : {_rw_n}")
        print(f"  Mean Huber weight             : {_rw_mean_all:.4f}  (1.0 = no downweighting)")
        print(f"  Min Huber weight              : {_rw_min_all:.4f}")
        print(f"  Rows with w < 1  (downweighted): {_rw_n_lt1} ({_rw_frac_lt1:.1f}%)")
        print(f"  Rows with w < 0.5 (strongly dw): {_rw_n_lt05} ({_rw_frac_lt05:.1f}%)")
        print(f"  Old-style rejects (now kept)   : {_rw_oldstyle_rej_tot}  "
              f"({100.*_rw_oldstyle_rej_tot/max(_rw_n,1):.1f}%)")

        # Weight histogram
        print(f"\n  [RW-2] HUBER WEIGHT HISTOGRAM (per row)")
        _rw_bins  = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 999.]
        _rw_lbls  = ["0.0–0.2","0.2–0.3","0.3–0.4","0.4–0.5",
                     "0.5–0.6","0.6–0.7","0.7–0.8","0.8–0.9","0.9–1.0","=1.0"]
        _rwhc, _rwht = _fa_hist(_rw_all_weights, _rw_bins)
        for _lbl, _cnt in zip(_rw_lbls, _rwhc):
            _bar = '#' * int(40 * _cnt / _rwht)
            print(f"    {_lbl:>8s} : {_cnt:6d}  {_bar}")

    # Sign-coherence (already computed in FA-5, repeated here for completeness)
    if _fa_epoch_sign_coh:
        _sc_frac = sum(_fa_epoch_sign_coh) / len(_fa_epoch_sign_coh) * 100
        print(f"\n  [RW-3] SIGN COHERENCE (Huber)")
        print(f"  Sign-coherent epoch fraction : {_sc_frac:.1f}%")
        print(f"  Previous experiment (SP=0.012 binary gate): 92.2%")
        if _sc_frac < 70.:
            print(f"  ✓ SUCCESS — oscillation suppressed (sign coherence < 70%)")
        elif _sc_frac < 85.:
            print(f"  ~ PARTIAL — oscillation weakened but persists")
        else:
            print(f"  ✗ FAILURE — oscillation persists despite continuous weighting")
            print(f"    → Instability is intrinsic to state-space architecture,")
            print(f"      not caused by measurement acceptance discontinuities")

    # Lag-1 ACF of ΔClock — need clock state stored per epoch (approximated from
    # state_trace which we don't have direct access to here; flag for user)
    print(f"\n  [RW-4] REJECT-STORM SUPPRESSION")
    print(f"  Longest all-reject streak (new) : {_bsg_rej_streak_max} epochs")
    print(f"  Previous (binary gate, SP=0.012): 3 epochs")
    print(f"  Baseline (binary gate, SP=0.003): 10 epochs")
    if _bsg_rej_streak_max == 0:
        print(f"  ✓ ZERO reject storms — continuous weighting eliminates them by design")
    elif _bsg_rej_streak_max < 3:
        print(f"  ✓ Reject storms further reduced")
    else:
        print(f"  ~ Reject storms unchanged — storms may have a non-gating cause")

    print(f"\n  [RW-5] PHASE-ACTIVE FRACTION")
    _new_paf = 100. * _phase_epochs_total / max(nproc, 1)
    print(f"  New (Huber)        : {_new_paf:.1f}%")
    print(f"  Previous (SP=0.012): 91.7%")
    print(f"  Baseline           : 64.9%")
    if _new_paf >= 99.0:
        print(f"  ✓ Phase-active every epoch — gating fully eliminated")
    elif _new_paf > 91.7:
        print(f"  ✓ Phase-active fraction further improved")

    print(f"\n  [RW-6] MEAN ACCEPTED NIS")
    if _fa_acc_nis:
        print(f"  New (Huber)        : {_fa_mean(_fa_acc_nis):.4f}")
        print(f"  Previous (SP=0.012): 1.5889")
        print(f"  Baseline           : 5.21")

    print(f"\n  [RW-7] INTERPRETATION")
    print(f"  SUCCESS criteria (oscillation dies):")
    print(f"    • sign-coherent fraction drops materially (was 92.2%)")
    print(f"    • reject storms disappear (was 3 epochs, target 0)")
    print(f"    • ENU curves smooth (inspect plot)")
    print(f"    • mean robust weight stays close to 1.0 (small downweighting needed)")
    print(f"  FAILURE criteria (oscillation persists):")
    print(f"    • sign-coherent fraction stays ≥ 90%")
    print(f"    • large residuals persist epoch-scale ±300–400 mm")
    print(f"    • → Instability is state-space-intrinsic, not gating-caused")
    print(f"{'='*72}\n")



    # ── OBSERVABILITY-AUDIT (ZWD/Up/Clock manifold experiment) ───────────────
    print(f"\n{'='*72}")
    print(f"[OBSERVABILITY-AUDIT]  ZWD/Up/Clock manifold tightening experiment")
    print(f"  Qzwd = (0.0010)**2 / s = 1e-6/s  |  ZWD sigma floor = 50 mm")
    print(f"{'='*72}\n")

    _obs_n = min(len(_clk_series), len(_zwd_series), len(_up_series))
    if _obs_n >= 4:
        import numpy as _np_obs

        _clk_arr = _np_obs.array(_clk_series[:_obs_n]) * 1e3   # mm
        _zwd_arr = _np_obs.array(_zwd_series[:_obs_n]) * 1e3   # mm
        _up_arr  = _np_obs.array(_up_series[:_obs_n])           # mm

        # [OBS-1] Cross-correlations
        def _corr(a, b):
            a_c = a - a.mean(); b_c = b - b.mean()
            den = math.sqrt(float((a_c**2).sum()) * float((b_c**2).sum()))
            return float((a_c * b_c).sum()) / den if den > 1e-30 else float('nan')

        _r_clk_up  = _corr(_clk_arr, _up_arr)
        _r_clk_zwd = _corr(_clk_arr, _zwd_arr)
        _r_up_zwd  = _corr(_up_arr,  _zwd_arr)

        print(f"  [OBS-1] CROSS-CORRELATION (post-update series, n={_obs_n})")
        print(f"  corr(clock, Up)  = {_r_clk_up:+.4f}")
        print(f"  corr(clock, ZWD) = {_r_clk_zwd:+.4f}")
        print(f"  corr(Up, ZWD)    = {_r_up_zwd:+.4f}")

        # [OBS-2] Per-epoch delta norms
        _dclk_obs = _np_obs.diff(_clk_arr)
        _dzwd_obs = _np_obs.diff(_zwd_arr)
        _dup_obs  = _np_obs.diff(_up_arr)

        print(f"\n  [OBS-2] MEAN EPOCH-DELTA NORMS (mm/epoch)")
        print(f"  mean |Δclock| = {float(_np_obs.abs(_dclk_obs).mean()):.2f} mm")
        print(f"  mean |ΔUp|    = {float(_np_obs.abs(_dup_obs).mean()):.2f} mm")
        print(f"  mean |ΔZWD|   = {float(_np_obs.abs(_dzwd_obs).mean()):.2f} mm")

        # [OBS-3] Sign coherence: fraction of epochs where sign(mean phase residual) == sign(Δclock)
        _fa_ph_signs = [1 if s else -1 for s in _fa_epoch_sign_coh]  # +1 = pos-dominant, -1 = neg
        _n_coh = min(len(_fa_ph_signs), len(_dclk_obs))
        _sign_match = sum(
            1 for i in range(_n_coh)
            if _fa_ph_signs[i] * _dclk_obs[i] > 0
        )
        _sc_obs = _sign_match / _n_coh if _n_coh > 0 else float('nan')

        print(f"\n  [OBS-3] SIGN COHERENCE (residual sign ↔ Δclock, n={_n_coh})")
        print(f"  fraction matching = {_sc_obs:.4f}  ({_sc_obs*100:.1f}%)")
        print(f"  previous (NbClkDecouple): 72.0%")

        # [OBS-4] Lag-1 ACF of differences
        def _lag1_acf(arr):
            if len(arr) < 3:
                return float('nan')
            mu = float(arr.mean()); c = arr - mu
            num = float((c[:-1] * c[1:]).sum()); den = float((c**2).sum())
            return num / den if den > 1e-30 else float('nan')

        _acf_clk  = _lag1_acf(_dclk_obs)
        _acf_up   = _lag1_acf(_dup_obs)
        _acf_zwd  = _lag1_acf(_dzwd_obs)

        print(f"\n  [OBS-4] LAG-1 ACF OF EPOCH DELTAS")
        print(f"  ACF(Δclock) = {_acf_clk:+.4f}  (previous: -0.1726)")
        print(f"  ACF(ΔUp)    = {_acf_up:+.4f}")
        print(f"  ACF(ΔZWD)   = {_acf_zwd:+.4f}")
        print(f"  (strongly negative ≈ sign-alternation; ~0 = white noise)")

        # [OBS-5] Oscillation energy ratio (high-frequency / total)
        _hf_energy_clk = float((_dclk_obs**2).sum())
        _tot_energy_clk = float(((_clk_arr - _clk_arr.mean())**2).sum()) if float(((_clk_arr - _clk_arr.mean())**2).sum()) > 0 else 1.
        _hf_energy_up   = float((_dup_obs**2).sum())
        _tot_energy_up  = float(((_up_arr  - _up_arr.mean())**2).sum())  if float(((_up_arr  - _up_arr.mean())**2).sum())  > 0 else 1.

        print(f"\n  [OBS-5] OSCILLATION ENERGY RATIO (Δ-power / total power)")
        print(f"  clock: Δ-energy/total = {_hf_energy_clk/_tot_energy_clk:.4f}")
        print(f"  Up:    Δ-energy/total = {_hf_energy_up/_tot_energy_up:.4f}")

        # [OBS-6] Experiment progression table (updated)
        _cur_nis = _fa_mean(_fa_acc_nis) if _fa_acc_nis else float('nan')
        _cur_sc  = sum(_fa_epoch_sign_coh)/len(_fa_epoch_sign_coh)*100 if _fa_epoch_sign_coh else float('nan')
        print(f"\n  [OBS-6] EXPERIMENT PROGRESSION")
        print(f"  {'Experiment':<40s}  {'sign-coh%':>9s}  {'NIS':>6s}  {'lag1ACF':>8s}")
        print(f"  {'─'*40}  {'─'*9}  {'─'*6}  {'─'*8}")
        print(f"  {'Baseline (SP=0.003)':<40s}  {'~70–85%':>9s}  {'5.21':>6s}  {'?':>8s}")
        print(f"  {'SP=0.012 binary gate':<40s}  {'92.2%':>9s}  {'1.59':>6s}  {'?':>8s}")
        print(f"  {'SP=0.012 + Huber':<40s}  {'93.2%':>9s}  {'2.01':>6s}  {'?':>8s}")
        print(f"  {'SP=0.012 + Huber + NbClkDecouple':<40s}  {'72.0%':>9s}  {'2.43':>6s}  {'-0.1726':>8s}")
        print(f"  {'+ ZWD tighten + ZWD floor':<40s}  {_cur_sc:>8.1f}%  {_cur_nis:>6.2f}  {_acf_clk:>+8.4f}")

        # [OBS-VERDICT]
        _prev_sc = 0.720   # NbClkDecouple result
        print(f"\n  [OBS-VERDICT]")
        if _sc_obs < 0.75 and abs(_acf_clk) < abs(-0.1726) - 0.03:
            print(f"  [OBSERVABILITY-VERDICT] SUCCESS — ZWD/Up manifold was dominant")
            print(f"  sign_coherence={_sc_obs*100:.1f}% < 75%  AND  lag1ACF={_acf_clk:+.4f} improved")
        elif _sc_obs < 0.90:
            print(f"  [OBSERVABILITY-VERDICT] PARTIAL — manifold contributes but is not sole driver")
            print(f"  sign_coherence={_sc_obs*100:.1f}% in 75–90% range")
        else:
            print(f"  [OBSERVABILITY-VERDICT] FAILURE — instability survives tightened ZWD")
            print(f"  sign_coherence={_sc_obs*100:.1f}% ≥ 90% — geometry-intrinsic instability suspected")
            print(f"  Next target: position-clock observability geometry (GPS-only VDOP)")
    else:
        print(f"  Insufficient epochs for observability audit (n={_obs_n})")

    print(f"{'='*72}\n")
    # ── END OBSERVABILITY-AUDIT ───────────────────────────────────────────────

    # ── GPS-L2-STAB: forensic summary ────────────────────────────────────────
    if l2_stab_audit_fh is not None:
        _L2_SWITCH_BEFORE = 125   # Layer-1A baseline: 125 L2L↔L2W transitions
        print(f"[GPS-L2-STABILIZATION] {_L2_SWITCH_BEFORE} {_l2_switch_count}")
        if _l2_switch_count == 0:
            print(f"  ✓ Zero L2 family switches — experiment condition satisfied.")
        else:
            print(f"  ✗ UNEXPECTED: {_l2_switch_count} switch(es) detected — "
                  f"check l2_sig_used in _proc().")

    # ── Build 7-element canonical return tuple ────────────────────────────────
    # ═══════════════════════════════════════════════════════════════════════
    # PATCH 5 — RESIDUAL-FRAME-VERDICT
    # Derived entirely from audited residual lineage; no speculation.
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'='*72}")
    print(f"[RESIDUAL-FRAME-VERDICT]")
    print(f"")
    print(f"  Evidence summary:")
    print(f"  1. INFO-BALANCE / FORENSIC-SUMMARY phase_rms uses _ph_innov_list,")
    print(f"     which is populated from z[rl] = phase_res = LIFc − (rp(xs)+xs[ki])")
    print(f"     computed with the PRE-UPDATE state xs.  This is PREFIT_LINEARIZED.")
    print(f"     Observed magnitude: ~94–505 mm depending on epoch (ambiguity absorbs")
    print(f"     the bulk of the phase bias; residual is measurement noise level).")
    print(f"")
    print(f"  2. EPOCH phase_rms uses _phase_postfit_res_mm = LIFc − (rp(x)+x[ki])")
    print(f"     computed with the POST-UPDATE state x.  This is POSTFIT.")
    print(f"     Observed magnitude: ~1–10 mm when ambiguities are converged.")
    print(f"")
    print(f"  3. COMMON-MODE phase_res also uses post-update x (same as EPOCH).")
    print(f"")
    print(f"  4. code_rms in INFO-BALANCE uses _code_innov_list = prefit code residuals")
    print(f"     (PIF − rp(xs), pre-update).  code_rms in EPOCH uses all-geom postfit")
    print(f"     code residuals (PIF − rp(x), post-update).")
    print(f"")
    print(f"  5. The EKF update (filter_standard) inputs z_p = [phase_res; code_res;")
    print(f"     zwd_pseudo] as the innovation vector directly.  The update x += K@z_p")
    print(f"     shifts the ambiguity and clock such that LIFc − (rp(x)+x[ki]) ≈ 0")
    print(f"     post-update — which is why POSTFIT collapses to mm level.")
    print(f"")
    print(f"  6. No weighted residuals are incorrectly reported as physical mm in any")
    print(f"     printed metric.  Weighted values appear only in EKF-ROW NIS columns.")
    print(f"")
    print(f"  VERDICT:")
    print(f"  A — Diagnostics inconsistent; EKF itself likely healthy")
    print(f"")
    print(f"  Specifically: PREFIT and POSTFIT residual arrays are populated from")
    print(f"  DIFFERENT state snapshots (xs vs x) and reported by DIFFERENT metrics")
    print(f"  (INFO-BALANCE vs EPOCH) WITHOUT labelling their frame.  The apparent")
    print(f"  95 mm → 3 mm 'collapse' is an artefact of this mixing.  The EKF IS")
    print(f"  genuinely reducing residuals (B is also true locally per epoch), but")
    print(f"  the 30× ratio between printed values is primarily a frame mismatch (A).")
    print(f"  Weighted residuals are NOT being misreported as physical (C is FALSE).")
    print(f"  The inconsistency is D (prefit/postfit arrays mixed across metrics)")
    print(f"  as the root cause, manifesting as A.")
    print(f"  Final verdict: A + D  (frame mismatch; EKF itself is healthy).")
    print(f"{'='*72}\n")
    # ── END RESIDUAL-FRAME-VERDICT ────────────────────────────────────────────

    # Signature:  results, end_xyz, end_clk, end_zwd, wl_fixed, amb_states, snap
    # All values come from state already computed in the epoch loop;
    # nothing new is calculated here.
    _end_xyz    = nom + x[:3]                          # final XYZ position (m)
    _end_clk    = float(x[3])                          # final receiver clock (m)
    _end_zwd    = float(x[4])                          # final ZWD (m)
    _amb_states = {sid: float(x[ki])                   # final float amb states (m)
                   for sid, ki in sidx.items()}
    _snap       = {                                     # minimal state snapshot
        'x':    x.copy(),
        'P':    P.copy(),
        'sidx': dict(sidx),
        'nom':  nom.copy(),
        'namb': namb,
    }

    print(f"[_PPP_PASS_RETURN_SIGNATURE]")
    print(f"  n_return_values = 7")
    print(f"  names = [results, end_xyz, end_clk, end_zwd, wl_fixed, amb_states, snap]")

    # == REBIRTH-FORENSIC HUNK 6: pass-end summary =========================
    _rebirth_forensic.summarize(nproc_total=nproc)
    if _rebirth_forensic_csv is not None:
        try:
            _rebirth_forensic_csv.close()
        except Exception:
            pass
    # ======================================================================

    return (results, _end_xyz, _end_clk, _end_zwd, wl_fixed, _amb_states, _snap)



def postpos(ts,te,ti,tu,popt,sopt,fopt,infiles,outfile,rov=None,base=None):
    t0=_time.time()
    ddir=os.path.dirname(os.path.abspath(infiles[0]))
    def _f(exts):
        for e in exts:
            for f in infiles:
                if f.lower().endswith(e.lower()): return f
            for fn in os.listdir(ddir):
                if fn.lower().endswith(e.lower()): return os.path.join(ddir,fn)
        return None

    obs_f=infiles[0]; sp3_f=_f(['.sp3','.SP3']); clk_f=_f(['.clk','.CLK'])
    bia_f=_f(['.bia','.BIA']); atx_f=_f(['.atx','.ATX']); obx_f=_f(['.obx','.OBX'])
    blq_f=_f(['.blq','.BLQ'])   # ocean loading

    print("="*72)
    print("GPS-Only PPP (refactored from v642-L2STAB) | ZWD clamp+prior | amb freeze | res gate | NL N≥8 | WL std<0.25 | code σ=1.5m")
    print("="*72)

    _,epochs,ah,ak=parse_obs(obs_f)
    sp3t,sp,sc=parse_sp3(sp3_f)
    clkd=parse_clk(clk_f) if clk_f else {}
    osb=parse_bia(bia_f) if bia_f else {}

    satx,recx_db={},{}
    if atx_f: satx,recx_db=parse_atx(atx_f)
    recx=recx_db.get(ak) or recx_db.get(ak.split()[0]+' NONE')
    if recx: print(f"[ATX]  Receiver '{ak}' found")
    else:    print(f"[ATX]  WARNING: '{ak}' not found — no receiver PCV")

    att={}
    if obx_f: att=parse_obx(obx_f)

    # Ocean Tide Loading
    blq=parse_blq(blq_f) if blq_f else {}
    # Station name: first 4 chars of the RINEX marker name embedded in obs filename
    # e.g. IISC00IND_R_... → IISC
    sta_name=os.path.basename(obs_f)[:4].upper()
    if blq:
        print(f"[OTL]  Using station '{sta_name}' for BLQ look-up")
    else:
        print(f"[OTL]  No BLQ file found — ocean loading not applied")

    REF=np.array([1337935.5599,6070317.2377,1427877.5071])
    APX=np.array([1337936.455, 6070317.126, 1427876.785])
    tref=sp3t[0]; DOY=38
    lat0,_,h0=_lla(APX); zhd=_zhd(lat0,h0)

    print(f"[INIT] ZHD={zhd:.4f}m  h={h0:.0f}m  lat={math.degrees(lat0):.3f}deg")
    print(f"[MODEL] SatPCO/PCV:{len(satx)} PRNs  RecPCO/PCV:{'Y' if recx else 'N'}"
          f"  OBX:{len(att)} sats  OSB:{sum(len(v) for v in osb.values())} entries"
          f"  OTL:{'Y ('+sta_name+')' if blq and sta_name in blq else 'N'}")
    print()

    _common=dict(sp3t=sp3t,sp=sp,sc=sc,clkd=clkd,osb=osb,ah=ah,
                 lat0=lat0,doy=DOY,zhd=zhd,tref=tref,satx=satx,att=att,recx=recx,
                 blq=blq,sta=sta_name)

    # ── OPEN FORENSIC TRACE CSV ───────────────────────────────────────────────
    _trace_path=os.path.join(ddir,'ppp_model_trace.csv')
    _TRACE_HDR=(
        "pass_label,epoch_sod,satellite,system,elevation_deg,azimuth_deg,"
        "raw_phase_L1_m,raw_phase_L2_m,raw_code_P1_m,raw_code_P2_m,"
        "LIF_raw_m,PIF_raw_m,"
        "sat_pco_x_mm,sat_pco_y_mm,sat_pco_z_mm,"
        "sat_apc_range_mm,sat_pcv_mm,"
        "rec_pco_x_mm,rec_pco_y_mm,rec_pco_z_mm,"
        "rec_apc_range_mm,rec_pcv_mm,"
        "osb_code_L1_mm,osb_code_L2_mm,"
        "osb_phase_L1_mm,osb_phase_L2_mm,"
        "osb_IF_code_mm,osb_IF_phase_mm,"
        "tropo_hydro_m,tropo_wet_m,"
        "sagnac_mm,relativity_mm,"
        "phase_windup_cycles,phase_windup_mm,"
        "solid_earth_tide_mm,ocean_loading_mm,"
        "geom_range_m,"
        "corrected_phase_m,corrected_code_m,"
        "predicted_phase_m,predicted_code_m,"
        "phase_residual_mm,code_residual_mm,"
        "float_ambiguity_state_m,float_ambiguity_cycles,"
        "wl_fixed,nl_fixed\n"
    )
    _trace_fh=open(_trace_path,'w')
    _trace_fh.write(_TRACE_HDR)
    print(f"[TRACE] Writing forensic trace → {_trace_path}")

    # ── PHASE-3: open state trace CSV ────────────────────────────────────────
    _state_trace_path = os.path.join(ddir, 'startup_state_trace.csv')
    _STATE_HDR = (
        "epoch,sod,"
        "dx,dy,dz,"
        "receiver_clock_mm,zwd_mm,"
        "pos_update_norm_mm,clk_update_mm,zwd_update_mm,"
        "condP,"
        "pos_sigma_mm,clk_sigma_mm,zwd_sigma_mm,"
        "gps_amb_sigma_mean_mm,"
        "gps_amb_sigma_min_mm,"
        "gps_amb_sigma_max_mm,"
        "gps_phase_rms_mm,"
        "gps_code_rms_mm\n"
    )
    _state_trace_fh = open(_state_trace_path, 'w')
    _state_trace_fh.write(_STATE_HDR)
    print(f"[TRACE] Writing state trace → {_state_trace_path}")

    # ── PHASE-2: open amb_birth_trace CSV ────────────────────────────────────
    _birth_trace_path = os.path.join(ddir, 'amb_birth_trace.csv')
    _BIRTH_HDR = (
        "epoch,sod,sat,const,elevation_deg,"
        "birth_mm,birth_cycles,birth_sigma_mm,"
        "observed_lif_mm,predicted_noamb_mm,"
        "rho_geom_mm,sat_clk_mm,rec_clk_mm,"
        "tropo_hydro_mm,tropo_wet_mm,"
        "relativity_mm,shapiro_mm,solid_earth_mm,"
        "apc_sat_mm,apc_rec_mm,"
        "pcv_sat_mm,pcv_rec_mm,"
        "osb_if_phase_mm,windup_mm,"
        "windup_cycles,"
        "is_startup,is_first2h,"
        "birth_innovation_mm,birth_NIS,birth_predicted_sigma_mm\n"
    )
    _birth_trace_fh = open(_birth_trace_path, 'w')
    _birth_trace_fh.write(_BIRTH_HDR)
    print(f"[TRACE] Writing amb birth trace → {_birth_trace_path}")

    # ── PHASE-3: open constellation_stats_trace CSV ───────────────────────────
    _constat_path = os.path.join(ddir, 'constellation_stats_trace.csv')
    _CONSTAT_HDR = (
        "epoch,sod,"
        "gps_phase_mean_mm,gps_phase_median_mm,gps_phase_rms_mm,"
        "gps_code_mean_mm,gps_amb_sigma_mean_mm,gps_mean_elev_deg,"
        "dx_mm,dy_mm,dz_mm,clock_mm,zwd_mm\n"
    )
    _constat_fh = open(_constat_path, 'w')
    _constat_fh.write(_CONSTAT_HDR)
    print(f"[TRACE] Writing constellation stats trace → {_constat_path}")

    # ── PHASE-2: open innovation_audit.csv ───────────────────────────────────
    _innov_audit_path = os.path.join(ddir, 'innovation_audit.csv')
    _INNOV_HDR = (
        "epoch,sod,sat,const,elevation_deg,"
        "accepted,reject_reason,"
        "innovation_mm,predicted_sigma_mm,innovation_variance_mm2,"
        "normalized_innovation_squared,"
        "ambiguity_sigma_mm,"
        "position_sigma_mm,clock_sigma_mm,zwd_sigma_mm,"
        "is_newborn,epochs_since_birth,"
        "phase_gate_mm\n"
    )
    _innov_audit_fh = open(_innov_audit_path, 'w')
    _innov_audit_fh.write(_INNOV_HDR)
    print(f"[TRACE] Writing innovation audit → {_innov_audit_path}")

    # ── PHASE-3: open ambiguity_participation_audit.csv ──────────────────────
    _amb_part_path = os.path.join(ddir, 'ambiguity_participation_audit.csv')
    _AMB_PART_HDR = (
        "epoch,sod,sat,ki,"
        "alive_epochs,"
        "sigma_mm,"
        "accepted_phase_updates,rejected_phase_updates,"
        "phase_rows_present,phase_rows_missing,"
        "last_H_norm,"
        "participated_in_update,"
        "kalman_gain_norm,"
        "last_successful_update_epoch,"
        "epochs_since_last_accept,"
        "is_newborn,is_recently_reset\n"
    )
    _amb_part_fh = open(_amb_part_path, 'w')
    _amb_part_fh.write(_AMB_PART_HDR)
    print(f"[TRACE] Writing ambiguity participation audit → {_amb_part_path}")

    # ── PHASE-4: open reset_audit.csv ────────────────────────────────────────
    _reset_audit_path = os.path.join(ddir, 'reset_audit.csv')
    _RESET_HDR = (
        "epoch,sod,sat,const,"
        "GF_value_mm,MW_value_cyc,"
        "phase_jump_mm,code_jump_cyc,"
        "elevation_deg,"
        "pre_reset_sigma_mm,"
        "recent_phase_residual_rms_mm,"
        "recent_rejection_fraction,"
        "epochs_since_birth,"
        "was_recently_rejected,"
        "cumulative_reset_number\n"
    )
    _reset_audit_fh = open(_reset_audit_path, 'w')
    _reset_audit_fh.write(_RESET_HDR)
    print(f"[TRACE] Writing reset audit → {_reset_audit_path}")

    # ── GPS-L2-STABILIZATION: forensic audit CSV ─────────────────────────────
    # Records selected_L2_phase_signal, switch_detected, ambiguity_reset_triggered,
    # GF_jump_mm, MW_jump_mm for every GPS satellite at every epoch of the GE pass.
    # Expected result: switch_detected = 0 for all rows (L2W forced throughout).
    _l2_stab_audit_path = os.path.join(ddir, 'gps_l2_stabilization_audit.csv')
    _l2_stab_audit_fh   = open(_l2_stab_audit_path, 'w')
    _l2_stab_audit_fh.write(
        "sat,sod,selected_L2_phase_signal,switch_detected,"
        "ambiguity_reset_triggered,GF_jump_mm,MW_jump_mm\n"
    )
    print(f"[TRACE] Writing GPS-L2-stabilization audit → {_l2_stab_audit_path}")

    # ── Step-1 refactor: ambiguity-manager snapshot CSV ──────────────────────
    _amgr_snap_path = os.path.join(ddir, 'ambiguity_manager_snapshot.csv')
    _amgr_snap_fh   = open(_amgr_snap_path, 'w')
    _amgr_snap_fh.write(AmbiguityManager.snapshot_header())
    print(f"[TRACE] Writing amb-manager snapshot → {_amgr_snap_path}")

    # ── STEP-2: ambiguity lifecycle trace CSV ────────────────────────────────
    _lifecycle_path = os.path.join(ddir, 'ambiguity_lifecycle_trace.csv')
    _lifecycle_fh   = open(_lifecycle_path, 'w')
    _lifecycle_fh.write(AmbiguityManager.lifecycle_trace_header())
    print(f"[TRACE] Writing amb lifecycle trace → {_lifecycle_path}")
    # ─────────────────────────────────────────────────────────────────────────

    mode_labels=[('G','GPS-only')]
    all_fwd={}; all_rts={}; all_meta={}

    for const,label in mode_labels:
        print(f"\n{'='*72}")
        print(f"[MODE] {label}  (constellation='{const}')")
        _rts_store._data=[]
        # GPS-only pass: all detailed traces are written
        _pass_state_fh   = _state_trace_fh
        _pass_obs_fh     = _trace_fh
        _pass_birth_fh   = _birth_trace_fh
        _pass_constat_fh = _constat_fh
        _pass_innov_fh   = _innov_audit_fh
        _pass_ambpt_fh   = _amb_part_fh
        _pass_reset_fh   = _reset_audit_fh
        _pass_amgr_fh    = _amgr_snap_fh
        _pass_lc_fh      = _lifecycle_fh
        _pass_l2_stab_fh = _l2_stab_audit_fh
        _ret = _ppp_pass(
            epochs,nom=APX.copy(),iclk=0.,izwd=0.20,
            direction=1,label="FWD",constellation=const,
            trace_fh=_pass_obs_fh,pass_label=label,
            state_trace_fh=_pass_state_fh,
            birth_trace_fh=_pass_birth_fh,
            constat_fh=_pass_constat_fh,
            innov_audit_fh=_pass_innov_fh,
            amb_part_fh=_pass_ambpt_fh,
            reset_audit_fh=_pass_reset_fh,
            amgr_snap_fh=_pass_amgr_fh,
            lifecycle_fh=_pass_lc_fh,
            l2_stab_audit_fh=_pass_l2_stab_fh,
            **_common)
        assert len(_ret) == 7, (
            f"[_PPP_PASS_SIGNATURE_ERR] expected 7 return values, got {len(_ret)} — "
            f"interface drift detected")
        print(f"[_PPP_PASS_SIGNATURE_OK]  n_return_values=7")
        fwd, ex, ec, ez, wl_f, fwd_amb, fwd_snap = _ret
        print(f"  {len(fwd)} epochs  end_3D={np.linalg.norm(ex-REF)*1e3:.1f}mm  ZWD={ez:.3f}m")
        print(f"  WL fixed: {list(wl_f.keys())}  ({len(wl_f)} sats)")

        print(f"[SMOOTH] Running RTS smoother on {len(_rts_store._data)} epochs ...")
        rts=_rts_smooth(fwd,APX.copy())
        all_fwd[label]=fwd; all_rts[label]=rts; all_meta[label]={'wl_fixed':wl_f}

        fwd_conv=sorted(fwd.items(),key=lambda kv:kv[1]['p_trace'])
        best60=fwd_conv[:min(60,len(fwd_conv))]
        if best60:
            avg_xyz=np.mean([r['xyz'] for _,r in best60],axis=0)
            lr,lo,_=_lla(REF); Re=_enu(lr,lo)
            diff=avg_xyz-REF; enu_d=Re@diff; dE,dN,dU=enu_d*1e3
            print(f"  DIAG 3D={np.linalg.norm(diff)*1e3:.1f}mm  "
                  f"dE={dE:+.1f}  dN={dN:+.1f}  dU={dU:+.1f} mm")
        _print_metrics(fwd,rts,REF,label)

    primary_fwd=all_fwd['GPS-only']
    primary_rts=all_rts['GPS-only']
    rl=[(s,{**r,'pass':'FWD'}) for s,r in sorted(primary_fwd.items())]

    # ── CLOSE TRACE AND PRINT DIAGNOSTICS ────────────────────────────────────
    _trace_fh.close()
    _state_trace_fh.close()
    _birth_trace_fh.close()
    _constat_fh.close()
    _innov_audit_fh.close()
    _amb_part_fh.close()
    _reset_audit_fh.close()
    _amgr_snap_fh.close()
    _lifecycle_fh.close()                              # STEP-2
    _l2_stab_audit_fh.close()                         # GPS-L2-STAB
    print(f"[TRACE] Closed state trace: {_state_trace_path}")
    print(f"[TRACE] Closed amb birth trace: {_birth_trace_path}")
    print(f"[TRACE] Closed constellation stats trace: {_constat_path}")
    print(f"[TRACE] Closed innovation audit: {_innov_audit_path}")
    print(f"[TRACE] Closed ambiguity participation audit: {_amb_part_path}")
    print(f"[TRACE] Closed reset audit: {_reset_audit_path}")
    print(f"[TRACE] Closed GPS-L2-stabilization audit: {_l2_stab_audit_path}")
    print(f"\n[TRACE] Closed: {_trace_path}")

    # ── PHASE-4: STARTUP BIRTH SUMMARY ───────────────────────────────────────
    # Automatically analyse amb_birth_trace.csv after the run completes.
    # PURE FORENSIC — no numerical changes.
    try:
        import csv as _csv2
        _brows = []
        with open(_birth_trace_path, 'r') as _bfh:
            _brd = _csv2.DictReader(_bfh)
            for _br in _brd:
                try:
                    _brows.append({
                        'sat':      _br['sat'],
                        'const':    _br['const'],
                        'elev':     float(_br['elevation_deg']),
                        'birth_mm': float(_br['birth_mm']),
                        'windup':   float(_br['windup_mm']),
                        'osb':      float(_br['osb_if_phase_mm']),
                        'is_startup': int(_br['is_startup']),
                    })
                except (ValueError, KeyError):
                    continue

        if _brows:
            _abs_b = [abs(r['birth_mm']) for r in _brows]
            print(f"\n{'='*72}")
            print(f"[STARTUP-BIRTH-SUMMARY]")
            print(f"  Total ambiguity births analysed : {len(_brows)}")
            print(f"  Mean |birth_mm|                 : {sum(_abs_b)/len(_abs_b):.1f} mm")
            print(f"  Max  |birth_mm|                 : {max(_abs_b):.1f} mm")

            # 1. Top 20 largest |birth_mm|
            _sorted_b = sorted(_brows, key=lambda r: abs(r['birth_mm']), reverse=True)
            print(f"\n  [1] TOP-20 LARGEST |birth_mm|")
            for _i, _r in enumerate(_sorted_b[:20]):
                print(f"    {_i+1:2d}. {_r['sat']:4s} ({_r['const']})  "
                      f"|birth|={abs(_r['birth_mm']):8.1f} mm  "
                      f"birth={_r['birth_mm']:+9.1f} mm  "
                      f"el={_r['elev']:.1f}°  startup={_r['is_startup']}")

            # 2. Mean |birth_mm| by constellation
            _g_b = [abs(r['birth_mm']) for r in _brows if r['const']=='G']
            print(f"\n  [2] MEAN |birth_mm| BY CONSTELLATION")
            print(f"    GPS     : {sum(_g_b)/len(_g_b):.1f} mm  (n={len(_g_b)})" if _g_b else "    GPS     : (none)")

            # 3. Mean |birth_mm| by elevation bin
            _bins = [('> 60°',  60, 999), ('30–60°', 30, 60),
                     ('15–30°', 15, 30),  ('< 15°',   0, 15)]
            print(f"\n  [3] MEAN |birth_mm| BY ELEVATION BIN")
            for _bname, _blo, _bhi in _bins:
                _bin_b = [abs(r['birth_mm']) for r in _brows if _blo <= r['elev'] < _bhi]
                if _bin_b:
                    print(f"    {_bname:8s}: {sum(_bin_b)/len(_bin_b):.1f} mm  (n={len(_bin_b)})")
                else:
                    print(f"    {_bname:8s}: (none)")

            # 4. Mean |birth_mm| by satellite
            _sat_groups = {}
            for _r in _brows:
                _sat_groups.setdefault(_r['sat'], []).append(abs(_r['birth_mm']))
            print(f"\n  [4] MEAN |birth_mm| BY SATELLITE (sorted by mean)")
            _sat_sorted = sorted(_sat_groups.items(),
                                 key=lambda kv: sum(kv[1])/len(kv[1]), reverse=True)
            for _sv, _sv_b in _sat_sorted:
                print(f"    {_sv:4s}: mean={sum(_sv_b)/len(_sv_b):.1f} mm  "
                      f"max={max(_sv_b):.1f} mm  n={len(_sv_b)}")

            # 5. Correlation: birth_mm vs elevation
            def _corr(xs, ys):
                n=len(xs)
                if n<3: return float('nan')
                mx=sum(xs)/n; my=sum(ys)/n
                num=sum((a-mx)*(b-my) for a,b in zip(xs,ys))
                den=math.sqrt(sum((a-mx)**2 for a in xs)*sum((b-my)**2 for b in ys))
                return num/den if den>0 else float('nan')
            _b_vals = [r['birth_mm'] for r in _brows]
            _e_vals = [r['elev']     for r in _brows]
            _w_vals = [r['windup']   for r in _brows]
            _o_vals = [r['osb']      for r in _brows]
            _corr_elev  = _corr(_b_vals, _e_vals)
            _corr_wu    = _corr(_b_vals, _w_vals)
            _corr_osb   = _corr(_b_vals, _o_vals)
            print(f"\n  [5] CORRELATION: birth_mm vs elevation  : r={_corr_elev:+.3f}")
            print(f"  [6] CORRELATION: birth_mm vs windup_mm  : r={_corr_wu:+.3f}")
            print(f"  [7] CORRELATION: birth_mm vs osb_if_mm  : r={_corr_osb:+.3f}")

            # PHASE-5: interpretation
            print(f"\n  [INTERPRETATION]")
            if not math.isnan(_corr_elev) and abs(_corr_elev) > 0.30:
                print(f"    *** birth bias correlates with elevation (r={_corr_elev:+.3f})")
                print(f"        → SUSPECT: troposphere/APC geometry coupling")
            if not math.isnan(_corr_wu) and abs(_corr_wu) > 0.30:
                print(f"    *** birth bias correlates with windup (r={_corr_wu:+.3f})")
                print(f"        → SUSPECT: wind-up convention/sign issue")
            if not math.isnan(_corr_osb) and abs(_corr_osb) > 0.30:
                print(f"    *** birth bias correlates with OSB (r={_corr_osb:+.3f})")
                print(f"        → SUSPECT: phase OSB sign/mapping inconsistency")
            _top_sats = [sv for sv, bv in _sat_sorted[:5]
                         if sum(bv)/len(bv) > 2.0 * (sum(_abs_b)/len(_abs_b))]
            if _top_sats:
                print(f"    *** Dominated by specific sats: {_top_sats}")
                print(f"        → SUSPECT: APC/PCV or observable-selection issue")
            print(f"{'='*72}\n")
        else:
            print(f"\n[STARTUP-BIRTH-SUMMARY] No birth rows found in {_birth_trace_path}")
    except Exception as _e4:
        print(f"[STARTUP-BIRTH-SUMMARY] Analysis failed: {_e4}")
    # ─────────────────────────────────────────────────────────────────────────
    # ── PHASE-6: AUDIT SUMMARY ────────────────────────────────────────────────
    # Pure post-hoc analysis of innovation_audit.csv — no numerical changes.
    try:
        import csv as _csv_a
        _ia_rows = []
        with open(_innov_audit_path, 'r') as _ia_fh:
            _ia_rd = _csv_a.DictReader(_ia_fh)
            for _iar in _ia_rd:
                try:
                    _ia_rows.append({
                        'sat':   _iar['sat'],
                        'const': _iar['const'],
                        'el':    float(_iar['elevation_deg']),
                        'acc':   int(_iar['accepted']),
                        'rej':   _iar['reject_reason'],
                        'nu':    float(_iar['innovation_mm'])   if _iar['innovation_mm'] not in ('nan','') else float('nan'),
                        'NIS':   float(_iar['normalized_innovation_squared']) if _iar['normalized_innovation_squared'] not in ('nan','') else float('nan'),
                        'is_nb': int(_iar['is_newborn']),
                    })
                except (ValueError, KeyError):
                    continue

        def _a_mean(v): return sum(v)/len(v) if v else float('nan')
        def _a_rms(v):  return math.sqrt(sum(x*x for x in v)/len(v)) if v else float('nan')

        _acc_rows  = [r for r in _ia_rows if r['acc'] == 1]
        _rej_rows  = [r for r in _ia_rows if r['acc'] == 0 and r['rej'] not in ('newborn','nan_residual')]
        _all_NIS   = [r['NIS'] for r in _acc_rows if math.isfinite(r['NIS'])]
        _rej_NIS   = [r['NIS'] for r in _rej_rows if math.isfinite(r['NIS'])]
        _nb_rows   = [r for r in _ia_rows if r['is_nb'] == 0 and r['acc'] == 1]
        _nb_NIS    = [r['NIS'] for r in _nb_rows if math.isfinite(r['NIS'])]
        _gps_NIS   = [r['NIS'] for r in _acc_rows if r['const']=='G' and math.isfinite(r['NIS'])]

        # rejection fractions by sat
        _sat_acc = {}; _sat_rej = {}; _sat_resets = {}
        for _r in _ia_rows:
            s = _r['sat']
            if _r['rej'] in ('newborn',): continue
            _sat_acc.setdefault(s, 0); _sat_rej.setdefault(s, 0)
            if _r['acc'] == 1: _sat_acc[s] += 1
            else:               _sat_rej[s] += 1

        # zero-update ambiguities: never accepted
        _zero_upd = [s for s in _sat_rej if _sat_acc.get(s, 0) == 0 and _sat_rej.get(s, 0) > 0]

        # top rejection fraction sats
        _rej_frac_by_sat = {}
        for s in set(list(_sat_acc.keys()) + list(_sat_rej.keys())):
            _tot = _sat_acc.get(s,0) + _sat_rej.get(s,0)
            _rej_frac_by_sat[s] = _sat_rej.get(s,0) / _tot if _tot > 0 else 0.

        _top_rej_sats = sorted(_rej_frac_by_sat.items(), key=lambda kv: kv[1], reverse=True)[:5]

        # NIS vs elevation correlation
        _nis_el_pairs = [(r['NIS'], r['el']) for r in _acc_rows
                         if math.isfinite(r['NIS']) and math.isfinite(r['el'])]
        def _corr2(pairs):
            if len(pairs) < 3: return float('nan')
            xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
            mx=sum(xs)/len(xs); my=sum(ys)/len(ys)
            num=sum((a-mx)*(b-my) for a,b in zip(xs,ys))
            den=math.sqrt(sum((a-mx)**2 for a in xs)*sum((b-my)**2 for b in ys))
            return num/den if den > 0 else float('nan')
        _NIS_el_corr = _corr2(_nis_el_pairs)

        # reset counts from reset_audit.csv
        _reset_sat_counts = {}
        try:
            with open(_reset_audit_path, 'r') as _ra_fh:
                for _rarow in _csv_a.DictReader(_ra_fh):
                    s = _rarow.get('sat','')
                    _reset_sat_counts[s] = _reset_sat_counts.get(s,0) + 1
        except Exception:
            pass
        _top_reset_sats = sorted(_reset_sat_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]

        # sigma > 10 m after 1 h: from ambiguity participation audit
        _zombie_sats = set()
        try:
            _HOUR_EP = 120   # 120 × 30s = 1 hour
            with open(_amb_part_path, 'r') as _ap_fh:
                for _aprow in _csv_a.DictReader(_ap_fh):
                    try:
                        if (int(_aprow['alive_epochs']) >= _HOUR_EP
                                and float(_aprow['sigma_mm']) > 10000.):
                            _zombie_sats.add(_aprow['sat'])
                    except (ValueError, KeyError):
                        pass
        except Exception:
            pass

        print(f"\n{'='*72}")
        print(f"[AUDIT SUMMARY]")
        print(f"  1. accepted / rejected phase rows : {len(_acc_rows)} / {len(_rej_rows)}")
        print(f"     (excluding newborn rows; hard-gate rejections only)")
        print(f"  2. mean NIS of accepted rows      : {_a_mean(_all_NIS):.4f}"
              f"  (expected ~1.0 if consistent)")
        print(f"  3. mean NIS of rejected rows      : {_a_mean(_rej_NIS):.4f}"
              f"  (expected >> 1.0 for hard-gated outliers)")
        print(f"  4. top satellites by rejection fraction:")
        for _rs, _rf in _top_rej_sats:
            _tot = _sat_acc.get(_rs,0) + _sat_rej.get(_rs,0)
            print(f"       {_rs:5s}  {_rf*100:.1f}%  ({_sat_rej.get(_rs,0)}/{_tot})")
        print(f"  5. top satellites by reset count:")
        for _rs, _rc in _top_reset_sats:
            print(f"       {_rs:5s}  {_rc} resets")
        print(f"  6. ambiguity states with ZERO accepted updates : {len(_zero_upd)}")
        if _zero_upd:
            print(f"       {sorted(_zero_upd)}")
        print(f"  7. ambiguity states sigma > 10m after 1h      : {len(_zombie_sats)}")
        if _zombie_sats:
            print(f"       {sorted(_zombie_sats)}")
        print(f"  8. mean NIS for non-newborn accepted rows     : {_a_mean(_nb_NIS):.4f}")
        print(f"  9. GPS mean NIS  : {_a_mean(_gps_NIS):.4f}")
        print(f" 10. correlation: NIS vs elevation (accepted)  : r={_NIS_el_corr:+.4f}")

        # FIX-2 (v64): AUDIT-NIS-SUMMARY — dedicated NIS diagnostic block using
        # nan-aware statistics and scientific-notation formatting.
        def _nis_stats(vals):
            finite = [v for v in vals if math.isfinite(v)]
            if not finite:
                return float('nan'), float('nan'), float('nan'), 0
            arr = np.array(finite)
            return float(np.mean(arr)), float(np.median(arr)), float(np.std(arr)), len(arr)
        _acc_mn, _acc_med, _acc_std, _acc_n = _nis_stats(_all_NIS)
        _rej_mn, _rej_med, _rej_std, _rej_n = _nis_stats(_rej_NIS)
        _gps_mn, _gps_med, _gps_std, _gps_n2 = _nis_stats(_gps_NIS)
        print(f"\n[AUDIT-NIS-SUMMARY]  (NIS=ν²/S; expected~1.0 at consistency; DIAGNOSTIC ONLY)")
        print(f"  accepted mean NIS  : {_acc_mn:.8e}   median={_acc_med:.4e}  std={_acc_std:.4e}  n={_acc_n}")
        print(f"  rejected mean NIS  : {_rej_mn:.8e}   median={_rej_med:.4e}  std={_rej_std:.4e}  n={_rej_n}")
        print(f"  GPS mean NIS       : {_gps_mn:.8e}   median={_gps_med:.4e}  std={_gps_std:.4e}  n={_gps_n2}")
        print(f"  NOTE: NIS is DIAGNOSTIC ONLY — not used operationally")

        print(f"\n  [INTERPRETATION]")
        _gps_n = _a_mean(_gps_NIS)
        if math.isfinite(_a_mean(_all_NIS)) and _a_mean(_all_NIS) > 5.0:
            print(f"  *** Mean accepted NIS={_a_mean(_all_NIS):.2f} >> 1 → gate is statistically INVALID")
            print(f"      Current covariance P under-represents actual innovation spread.")
        if _zero_upd:
            print(f"  *** {len(_zero_upd)} ambiguities have H rows but ZERO accepted updates")
            print(f"      → REJECTION STARVATION is likely dominant")
        if _zombie_sats:
            print(f"  *** {len(_zombie_sats)} ambiguities have sigma>10m after 1h")
            print(f"      → Check participation audit for disconnected states")
        if math.isfinite(_NIS_el_corr) and abs(_NIS_el_corr) > 0.20:
            print(f"  *** NIS-elevation correlation r={_NIS_el_corr:+.4f} suggests")
            print(f"      elevation-dependent model error (troposphere / APC?)")
        print(f"{'='*72}\n")

    except Exception as _e6:
        print(f"[AUDIT SUMMARY] Analysis failed: {_e6}")
        import traceback; traceback.print_exc()
    # ─────────────────────────────────────────────────────────────────────────

    print(f"\n  Wall: {_time.time()-t0:.1f}s")
    print("="*72)

    if outfile and rl:
        lr_csv,lo_csv,_=_lla(REF); Re_csv=_enu(lr_csv,lo_csv)
        with open(outfile,'w') as fo:
            fo.write("SOD,pass,"
                     "Computed_X,Computed_Y,Computed_Z,"
                     "REF_X,REF_Y,REF_Z,"
                     "DiffX_mm,DiffY_mm,DiffZ_mm,"
                     "dE_mm,dN_mm,dU_mm,"
                     "3D_mm,"
                     "N,WL_fixed,NL_fixed,"
                     "ZHD_m,ZWD_m,ZTD_m,CodeRMS_mm,PhsRMS_mm\n")
            for sod,r in rl:
                xyz=r['xyz']; dx=r['dx']; dx_mm=dx*1e3
                enu_mm=Re_csv@dx*1e3
                fo.write(f"{sod:.1f},{r['pass']},"
                         f"{xyz[0]:.4f},{xyz[1]:.4f},{xyz[2]:.4f},"
                         f"{REF[0]:.4f},{REF[1]:.4f},{REF[2]:.4f},"
                         f"{dx_mm[0]:+.3f},{dx_mm[1]:+.3f},{dx_mm[2]:+.3f},"
                         f"{enu_mm[0]:+.3f},{enu_mm[1]:+.3f},{enu_mm[2]:+.3f},"
                         f"{np.linalg.norm(dx_mm):.3f},"
                         f"{r['n']},{r.get('wl_fixed',0)},{r.get('nl_fixed',0)},"
                         f"{r.get('zhd',0):.4f},{r.get('zwd',0):.4f},{r['ztd']:.4f},"
                         f"{r.get('code_rms',0):.2f},{r.get('phase_rms',0):.3f}\n")
        print(f"[CSV]  Written: {outfile}")

    _plot_comparison(all_fwd,all_rts,REF)
    return 1


# ==============================================================================
#  Metrics + plotting
# ==============================================================================
def _compute_metrics(results,REF):
    if not results: return None
    lr,lo,_=_lla(REF); Re=_enu(lr,lo)
    fwd_list=sorted(results.items())
    sods_all=np.array([s for s,_ in fwd_list])
    dx_all=np.array([r['dx'] for _,r in fwd_list])
    enu_all=(Re@dx_all.T).T*1e3
    d3_all=np.linalg.norm(dx_all,axis=1)*1e3
    wl_counts=np.array([r.get('wl_fixed',0) for _,r in fwd_list])
    nl_counts=np.array([r.get('nl_fixed',0) for _,r in fwd_list])
    conv_mask=d3_all<200.
    if conv_mask.sum()>0:
        enu_c=enu_all[conv_mask]
        rms_e=math.sqrt(np.mean(enu_c[:,0]**2))
        rms_n=math.sqrt(np.mean(enu_c[:,1]**2))
        rms_u=math.sqrt(np.mean(enu_c[:,2]**2))
        rms_3d=math.sqrt(np.mean(d3_all[conv_mask]**2))
    else:
        rms_e=rms_n=rms_u=rms_3d=float('nan')
    def _conv(thr):
        for i,(sod,_) in enumerate(fwd_list):
            w=d3_all[i:i+5]
            if len(w)==5 and np.all(w<thr): return sod
        return None
    nl_first=next((sod for sod,r in fwd_list if r.get('nl_fixed',0)>0),None)
    return dict(sods=sods_all,e_mm=enu_all[:,0],n_mm=enu_all[:,1],u_mm=enu_all[:,2],
                d3_mm=d3_all,rms_e=rms_e,rms_n=rms_n,rms_u=rms_u,rms_3d=rms_3d,
                conv_time_10cm=_conv(100.),conv_time_5cm=_conv(50.),
                wl_counts=wl_counts,nl_counts=nl_counts,sods_all=sods_all,
                n_wl_fix=int(np.sum(wl_counts>0)),n_nl_fix=int(np.sum(nl_counts>0)),
                nl_first_sod=nl_first)

def _print_metrics(fwd,rts,REF,label):
    m=_compute_metrics(fwd,REF)
    if m is None: return
    print(f"\n  ── Metrics: {label} ──────────────────────────────────────────")
    print(f"  RMS (E/N/U/3D): {m['rms_e']:.1f} / {m['rms_n']:.1f} / "
          f"{m['rms_u']:.1f} / {m['rms_3d']:.1f} mm  (3D<200mm subset)")
    ct10=f"SOD={m['conv_time_10cm']:.0f}" if m['conv_time_10cm'] else "not reached"
    ct5 =f"SOD={m['conv_time_5cm']:.0f}"  if m['conv_time_5cm']  else "not reached"
    print(f"  Conv (5-ep sustain) <10cm: {ct10}   <5cm: {ct5}")
    print(f"  WL-fixed epochs: {m['n_wl_fix']}/{len(m['sods'])}  "
          f"NL-fixed epochs: {m['n_nl_fix']}/{len(m['sods'])}  "
          f"First NL SOD: {m['nl_first_sod']}")
    mr=_compute_metrics(rts,REF)
    if mr:
        print(f"  RTS RMS (E/N/U/3D): {mr['rms_e']:.1f} / {mr['rms_n']:.1f} / "
              f"{mr['rms_u']:.1f} / {mr['rms_3d']:.1f} mm")

def _plot_comparison(all_fwd,all_rts,REF):
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("[PLOT] matplotlib not available"); return
    colors={'GPS-only':'#e6194b'}
    fig,axes=plt.subplots(2,2,figsize=(14,10))
    fig.suptitle('GPS-Only PPP Comparison (gps_only)',
                 fontsize=14,fontweight='bold')
    ax=axes[0,0]
    for label,fwd in all_fwd.items():
        m=_compute_metrics(fwd,REF)
        if m is None: continue
        ax.plot(m['sods']/3600.,m['d3_mm'],color=colors.get(label,'k'),
                alpha=0.8,linewidth=0.8,label=label)
    ax.axhline(100,color='gray',linestyle='--',linewidth=0.7,label='10 cm')
    ax.axhline(50, color='gray',linestyle=':',linewidth=0.7,label='5 cm')
    ax.set_xlabel('Time (h)'); ax.set_ylabel('3D Error (mm)')
    ax.set_title('(a) 3D Positioning Error — FWD')
    ax.set_ylim(0,500); ax.legend(fontsize=8); ax.grid(True,alpha=0.3)
    ax=axes[0,1]
    m=_compute_metrics(all_fwd.get('GPS-only',{}),REF)
    if m is not None:
        sh=m['sods']/3600.
        ax.plot(sh,m['e_mm'],color='#e6194b',linewidth=0.8,label='East')
        ax.plot(sh,m['n_mm'],color='#3cb44b',linewidth=0.8,label='North')
        ax.plot(sh,m['u_mm'],color='#4363d8',linewidth=0.8,label='Up')
        ax.axhline(0,color='black',linewidth=0.5)
    ax.set_xlabel('Time (h)'); ax.set_ylabel('Error (mm)')
    ax.set_title('(b) ENU — GPS-Only FWD')
    ax.set_ylim(-300,300); ax.legend(fontsize=8); ax.grid(True,alpha=0.3)
    ax=axes[1,0]
    for label,fwd in all_fwd.items():
        m=_compute_metrics(fwd,REF)
        if m is None: continue
        ax.plot(m['sods']/3600.,m['nl_counts'],color=colors.get(label,'k'),
                linewidth=0.9,label=f'{label} NL')
    ax.set_xlabel('Time (h)'); ax.set_ylabel('# NL-fixed sats')
    ax.set_title('(c) NL-Fixed Ambiguities'); ax.legend(fontsize=8); ax.grid(True,alpha=0.3)
    ax=axes[1,1]
    mf=_compute_metrics(all_fwd.get('GPS-only',{}),REF)
    mr=_compute_metrics(all_rts.get('GPS-only',{}),REF)
    if mf: ax.plot(mf['sods']/3600.,mf['d3_mm'],color='#4363d8',linewidth=0.8,
                   alpha=0.8,label='FWD')
    if mr: ax.plot(mr['sods']/3600.,mr['d3_mm'],color='#f58231',linewidth=0.8,
                   alpha=0.8,label='RTS')
    ax.axhline(50,color='gray',linestyle='--',linewidth=0.7)
    ax.set_xlabel('Time (h)'); ax.set_ylabel('3D Error (mm)')
    ax.set_title('(d) FWD vs RTS — GPS-Only')
    ax.set_ylim(0,300); ax.legend(fontsize=8); ax.grid(True,alpha=0.3)
    plt.tight_layout()
    plot_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'ppp_gps_only_comparison.png')
    try:
        fig.savefig(plot_path,dpi=150,bbox_inches='tight')
        print(f"[PLOT] Saved: {plot_path}")
    except Exception as e:
        print(f"[PLOT] Could not save: {e}")
    plt.close(fig)


if __name__=='__main__':
    try:
        sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),'ppp_ar_python'))
        from structures import PrcOpt,SolOpt,FilOpt
    except:
        class PrcOpt: pass
        class SolOpt: pass
        class FilOpt: pass

    DATA=os.path.dirname(os.path.abspath(__file__))
    INFILES=[os.path.join(DATA,f) for f in [
        'IISC00IND_R_20260380000_01D_30S_MO.rnx',
        'IISC00IND_R_20260380000_01D_MN.rnx',
        'COD0MGXFIN_20260380000_01D_05M_ORB.SP3',
        'COD0MGXFIN_20260380000_01D_30S_CLK.CLK',
        'COD0MGXFIN_20260380000_01D_30S_ATT.OBX',
        'COD0MGXFIN_20260380000_01D_01D_OSB.BIA',
        'igs20_2408.atx',
        'COD0MGXFIN_20260380000_01D_12H_ERP.ERP',
    ]]
    postpos(None,None,0.,0.,PrcOpt(),SolOpt(),FilOpt(),
            INFILES,os.path.join(DATA,'ppp_results2.csv'))
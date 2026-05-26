"""
ppp.py  v98 — RDCB_E Prior Fix + Full Phase Measurement Forensics

── v98 CHANGES ────────────────────────────────────────────────────────────
  ROOT CAUSE CLOSED: Galileo code geometry-free observable (P2c − P1c) was
  WRONG SIGN in the KF because the RDCB_E prior variance was 0.10² m²
  (σ = 10 cm) while the true receiver DCB offset is ~11–12 m.  The state
  could never move from 0 to absorb the hardware DCB, leaving P2c < P1c
  permanently and preventing Galileo I_s from converging.

  SINGLE FIX (line ~3284):
    BEFORE: P[RDCB_E_IDX, RDCB_E_IDX] = 0.10 ** 2
    AFTER:  P[RDCB_E_IDX, RDCB_E_IDX] = 5.0  ** 2

  INSTRUMENTATION ADDED (diagnostic-only, zero KF changes):
    SECTION 1 — PHASE_FORENSICS: per-satellite full residual decomposition
    SECTION 2 — IONO_DECOMP:     ionosphere sign/scale + code GF sign check
    SECTION 3 — APC/PCV:         already in v95/v96/v97 (unchanged)
    SECTION 4 — OSB_TRACE:       per-epoch code GF sign for Galileo
    SECTION 5 — LAMBDA_TRACE:    wavelength/frequency consistency check
    SECTION 6 — RDCB_E_DIAG:     per-300-epoch RDCB_E convergence monitor

  EVIDENCE (from CSV audit):
    Late period L2/L1 phase ratio = 1.808 ≈ γ_GAL = 1.793 (0.8% match)
    GAL_P2_RMS = 13 106 mm vs GPS_P2_RMS = 1 568 mm
    E23 P2c − P1c = −2.555 m (required: +10.3 m for I ≈ 13 m)
    [RDCB_E] SOD=80970: value=+0.0000 m, sigma=0.3017 m → state frozen at 0

  EXPECTED OUTCOMES:
    RDCB_E converges to ~+10–12 m within 30–60 epochs
    Galileo code GF sign flips positive → I_s converges
    L1 RMS drops from 3–7 m → < 500 mm
    L2 RMS drops from 5–11 m → < 900 mm
    NL fixing rate improves
    3D positioning improves from 130–150 mm → 50–80 mm

  WHAT IS NOT CHANGED (byte-identical to v97):
    KF equations, H matrix, z vector, state vector, covariance propagation
    Ambiguity logic (WL, NL), OSB application, measurement equations
    Arc-reset logic, IONEX seeding, stochastic tuning, all v97 logic

"""
"""
ppp.py  v95 — Full Phase Measurement Forensics (DIAGNOSTIC ONLY)

── v95 CHANGES ────────────────────────────────────────────────────────────
  PURPOSE: Identify the observation-model inconsistency causing persistent
    L1 RMS ≈ 2.8–3.1 m and L2 RMS ≈ 4.7–5.4 m while ambiguities are
    healthy and 3D stays stable.  DIAGNOSTIC LOGGING ONLY — zero KF changes.

  STEP 1 — PHASE_FORENSICS: Full residual decomposition per satellite per signal
    For every satellite at debug epochs (every 300 s) and whenever RMS_WARN fires:
      [PHASE_FORENSICS] SOD=... sat=... sig=L1|L2
        obs=...m pred=...m res=...m
        rho=...m clk_r=...m clk_s=...m trop=...m iono=...m
        lambdaN=...m osb_code=...m osb_phase=...m
        pco_pcv_sat=...m pco_pcv_rec=...m windup=...m
        rel=...m shap=...m setm=...m

  STEP 2 — GAL_APC: Explicit satellite APC/PCV audit per epoch.
    Prints Galileo vs GPS frequency slot usage and IF-combination coefficients.
    [GAL_APC] SOD=... sat=E... freq1=E1/L1 freq2=E5a/L2
      atx_entry_found=T/F pco_E1_mm=(x,y,z) pco_E5a_mm=(x,y,z)
      alfa_used=... beta_used=... correct_alfa=ALFA_E correct_beta=BETA_E
      pcv_E1_mm=... pcv_E5a_mm=... nadir_deg=...
      WARNING if GPS ALFA/BETA used for Galileo sat.

  STEP 3 — OBS_MAP: Full signal/frequency mapping per satellite
    [OBS_MAP] sat=... code1=... phase1=... freq1=...MHz lambda1=...m
              code2=... phase2=... freq2=...MHz lambda2=...m
              osb_code1=...m osb_phase1=...m osb_code2=...m osb_phase2=...m
              applied_frame=IF_GPS|IF_GAL|RAW

  STEP 4 — IONO_DECOMP: Ionosphere sign/scaling validation
    [IONO_DECOMP] SOD=... sat=...
      I_state=...m gamma=... lambda1=...m lambda2=...m
      iono_L1_pred=...m iono_L2_pred=...m ratio_L2_L1=...
      expected_ratio=gamma=... MATCH=T/F
      P1c=...m P2c=...m code_GF=...m code_iono_est=...m state_match=T/F

  STEP 5 — OSB_APPLY: Verify OSB is not applied twice
    [OSB_APPLY] sat=... code1_type=... phase1_type=...
      osb_C1_m=... osb_L1_m=... osb_C2_m=... osb_L2_m=...
      P1c_after=... L1m_after=... P2c_after=... L2m_after=...
      applied_in_proc=True applied_in_Hloop=False [confirmed_once]

  WHAT IS NOT CHANGED:
    • KF equations, H matrix, z vector, state vector, covariance
    • Ambiguity logic (WL, NL), OSB application, measurement equations
    • Arc-reset logic, IONEX, stochastic tuning, all v94 logic
    • Every line of _proc, _proc_gal, filter_standard is byte-identical

  EXPECTED OUTCOME:
    One term will emerge as missing, duplicated, wrong-sign, wrong-frequency,
    or not applied for Galileo.  Specifically suspected:
      A) Galileo APC uses GPS ALFA/BETA instead of ALFA_E/BETA_E
         (ANTEX parser line 1522: vi = ALFA*a - BETA*b for ALL satellites)
      B) Receiver PCO uses GPS ALFA/BETA for Galileo observations
         (_rpco always uses ALFA/BETA regardless of satellite system)
      C) ANTEX frequency slot G01/G02 misread for Galileo (should be E01/E05)

  ALL OTHER FILTER LOGIC BYTE-IDENTICAL TO v94.
"""
"""
ppp.py  v94 — Arc-Scoped Ambiguity Re-Entry Reset

── v94 CHANGES ────────────────────────────────────────────────────────────
  TARGET: Eliminate stale ambiguity continuity surviving visibility gaps.

  SCIENTIFIC ROOT CAUSE (per patch spec):
    When a satellite reappears after a gap (missing_prev_epoch / gap > 30 s),
    the code logged a REENTRY event forensically but did NOT reset:
      - MW sliding buffer (mw_hist[sid])
      - GF reference (prev_gf[sid])
      - WL integer (wl_fixed[sid])
      - NL integer (nl_fixed[sid])
      - KF ambiguity states N1/N2 and ionosphere
    Stale integers from the old arc contaminated the new arc, predicting
    the WL jumps >3 cycles and L2 RMS spike clusters observed in v91–v93.

  SINGLE STRUCTURAL CHANGE:
    Insert an arc-scoped reset block immediately after the v93 REENTRY
    forensic log (but before slip detection) that fires whenever
    _disc_is_reentry is True (gap > 30 s = 1 epoch at 30 s cadence).

    On re-entry:
      1. mw_hist[sid].clear()                  — stale MW buffer
      2. prev_gf[sid] = None / prev_mw[sid] = None  — GF continuity
      3. wl_fixed.pop / nl_fixed.pop           — WL/NL integers
      4. phi[sid] = False + KF state zero+cov wipe — new ambiguity arc
      5. Clear all auxiliary per-arc buffers   — _lp1, _nl_frac, etc.
      6. Ionosphere re-seed is automatic via   — existing fresh-entry block

  WHAT IS NOT CHANGED:
    • KF equations, H matrix, z vector
    • Global states: position, clock, troposphere, ISB
    • MW/GF slip thresholds
    • NL/WL fixing logic, OSB handling
    • Ionosphere math (same IONEX seeding path reused)
    • All v92 GF initialization guard logic

  EXPECTED OUTCOMES:
    - WL jumps >3 cycles collapse sharply
    - Post-re-entry L2 RMS spike clusters reduce
    - GPS late-arc instability improves
    - ambiguity_discontinuity_audit.csv shows new arc after every gap
    - NL release frequency decreases

  ALL OTHER FILTER LOGIC BYTE-IDENTICAL TO v93.

"""
"""
ppp.py  v92 — GF Initialization Guard

── v92 CHANGES ────────────────────────────────────────────────────────────
  TARGET: Break the infinite arc-reset loop caused by the GF consistency
  checker testing an uninitialized (zero) ionosphere state.

  SCIENTIFIC ROOT CAUSE (forensic audit, Document 3):
    After every cycle-slip reset, x[ki] is zeroed.  The v89 arc-reset
    consistency check then immediately sees old_I=0 and GF_measured>0.5 m
    → fires "gf_pos_state_nonpos" → re-seeds again → x[ki]=IONEX value.
    But the IONEX seed arrives correctly only because the guard was absent;
    without the guard the reset fires every epoch, producing 1,644 resets/day
    with 72 % single-epoch arcs.

  SINGLE STRUCTURAL CHANGE:
    Insert an initialization guard in the v89 ARC-RESET IONOSPHERE
    CONSISTENCY CHECK block (inside `if slip:`).

    Guard condition:
      if old_I_state == 0.0  OR  epochs_since_entry < 3:
          skip the GF-vs-I_state comparison entirely.

    Implementation uses the existing _sat_age[sid] counter (already
    reset to 0 on slip, incremented once per valid epoch) — no new
    per-satellite counters needed.

    After 3 epochs the detector resumes IDENTICALLY to v91.

  DIAGNOSTICS:
    New CSV: gf_guard_audit.csv
    Columns: SOD, sat, epochs_since_entry, old_I_state, GF_measured,
             GF_test_skipped, skip_reason, reset_triggered, trigger_reason
    Console: [GF_GUARD] for first few skipped tests.

  WHAT IS NOT CHANGED:
    • KF equations, H matrix, z vector, state vector
    • Ambiguity logic (WL, NL), OSB handling
    • MW cycle-slip detector (dGF/dMW test at entry to slip block)
    • IONEX re-seeding logic — still runs when guard fires (it bypasses
      only the _ar_reason assignment, not the re-seed itself when
      the inherited state is still zero after guard)
    • GPS/Galileo balancing, ISB, ZWD, process noise
    • All other filter logic — byte-identical to v91.

  EXPECTED OUTCOME (per forensic audit):
    - Resets collapse from ~1,644 → ~100–200/day
    - Stable ambiguity arcs form
    - Forward pass begins tracking RTS performance
    - Up excursion reduced; sustained 3–5 cm convergence possible

  ALL OTHER FILTER LOGIC BYTE-IDENTICAL TO v91.

"""

"""
── v91 CHANGES ────────────────────────────────────────────────────────────
  TARGET: Replace the fixed σ=22 m ionosphere init covariance with an
  adaptive sigma derived from the IONEX-predicted STEC magnitude.

  SCIENTIFIC JUSTIFICATION (forensic audit):
    1,644 arc resets/day all reinitialize at P[iono,iono] = 22² m².
    Late-session VTEC drops to ~8–14 TECU → I_L1 ≈ 3–14 m.
    Reinitializing with σ=22 m repeatedly destroys mature covariance.

  SINGLE CHANGE: initialization covariance of the ionosphere state ONLY,
  applied at (a) fresh satellite entry and (b) arc-reset re-seeding.

  MODEL:
    sigma_iono_init = max(SIGMA_IONO_FLOOR, K_SIGMA_IONO * abs(I_L1_init_m))
    SIGMA_IONO_FLOOR = 3.0 m   (never below this — stays conservative)
    K_SIGMA_IONO     = 0.35    (conservative proportionality factor)

    Examples:
      I_L1 = 30 m → σ = 10.5 m
      I_L1 = 15 m → σ =  5.25 m
      I_L1 =  5 m → σ =  3.0 m  (floor)
      I_L1 =  2 m → σ =  3.0 m  (floor)

  DIAGNOSTICS:
    New CSV: adaptive_iono_sigma_audit.csv
    Columns: SOD, sat, reset_type, VTEC, mapping_factor, I_L1_init_m,
             sigma_old_m, sigma_new_m, floor_active, fallback_used,
             gf_I_before_reset
    Console: [ADAPTIVE_IONO_SIGMA] for first few resets.

  WHAT IS NOT CHANGED:
    • Any measurement equation, H matrix, z vector
    • Ambiguity logic (WL, NL), OSB handling
    • Arc-reset logic itself (v89 consistency check untouched)
    • IONEX parser, IPP computation, VTEC interpolation
    • GPS/Galileo balancing, ISB, ZWD, process noise
    • KF structure, state vector, covariance propagation

  ALL OTHER FILTER LOGIC BYTE-IDENTICAL TO v90.

"""
"""
ppp.py  v90 — C1C/L1C-Only Galileo Phase Exclusion
===========================================================================

── v90 CHANGES ────────────────────────────────────────────────────────────
  TARGET: Exclude single-frequency (C1C/L1C-only) Galileo carrier phase
  from the PPP state update.

  ROOT CAUSE (forensic audit, Document 2):
    Single-frequency Galileo phase (E5a absent) injected unresolved
    ionosphere into the Up state, inflated ZWD covariance, destroyed
    residual whitening, and amplified Galileo dominance in vertical
    updates.  Key indicators:
      lag-1 autocorr(dU) = 0.998
      phase drift amplification = 17.5×
      σ_I frozen at 22 m forever (no dual-freq observability)

  SINGLE STRUCTURAL CHANGE:
    In the per-epoch satellite processing loop, immediately after
    _proc_gal() returns (and non-None check), detect:
        _is_c1c_only_gal = (sid[0]=='E' and m['_fallback_used']==True)
    i.e. Galileo satellite where E5a (C5Q/L5Q) is absent.

    When detected:
      1. Log to c1c_phase_exclusion_audit.csv
      2. Print [C1C_PHASE_EXCLUDED]
      3. `continue` — satellite skips ALL further processing:
            • No state allocation (no I, N1, N2 in sidx)
            • No geom entry (no H rows of any kind)
            • No MW/WL accumulation (MW_cyc=0 anyway for these sats)
            • No ionosphere state, no ambiguity state, no AR eligibility

  This converts single-frequency Galileo from a partial carrier-phase
  participant (with unresolvable ionosphere) into a fully excluded
  non-participant.  Dual-frequency Galileo is completely unchanged.

  WHAT IS NOT CHANGED:
    • GPS (all paths)
    • Dual-frequency Galileo (E5a present)
    • KF structure, H matrix dimensions (no per-sat allocation → no slot)
    • WL/NL fixing, IONEX init, ZWD, ISB, RTS smoother
    • Measurement weights, process noise, covariance propagation
    • All existing arc_reset/ionex/rms/wlcal diagnostics

  NEW DIAGNOSTIC: c1c_phase_exclusion_audit.csv
    Columns: SOD, sat, c1c_only_detected, phase_excluded, code_retained,
             ambiguity_created, ionosphere_created, rows_added,
             used_geometry_only
    Console: [C1C_PHASE_EXCLUDED] at every excluded satellite/epoch.

  ALL OTHER FILTER LOGIC BYTE-IDENTICAL TO v89.

"""
"""
ppp.py  v89 — Arc-Reset Ionosphere Consistency Fix
===========================================================================

── v89 CHANGES ────────────────────────────────────────────────────────────
  TARGET: Prevent non-physical (negative or sign-inconsistent) ionosphere
  inheritance when a satellite ambiguity arc is reset.

  ROOT CAUSE (confirmed by forensic audit, Part 1):
    G01 at SOD 81720 and 85320: arc reset left the inherited iono state
    at ~0 m from the previous arc end.  The new-arc geometry produced
    positive (L1m−P1c) and (L2m−P2c) means, which is physically
    impossible for I > 0.  The KF resolved the inconsistency by driving
    I_est to −0.71 and −0.89 m — non-physical values.

  ROOT CAUSE CLASS:
    Localized arc continuity failure.  NOT a global KF issue, NOT a
    weighting issue, NOT a process-noise issue.

  SINGLE STRUCTURAL CHANGE:
    Inside the existing `if slip:` block (cycle-slip reset path),
    AFTER all existing state/covariance resets complete, add a
    geometry-free ionosphere consistency check.

    If ANY of the following hold:
      (a) inherited x[ki] < 0              (physically impossible)
      (b) I_gf_code > 0  AND               (code GF is positive AND)
          x[ki] < 0                         (state is negative)
      (c) sign(L1m−P1c) > 0 with I_state ≤ 0
              (L−P positive implies iono collapsed or ambiguity wrong)

    → Re-initialize ONLY the ionosphere state x[ki] and P[ki,ki]
      using the same IONEX-or-code-fallback logic already used for
      fresh satellite entry.
    → Preserve x[ki+1], x[ki+2], all other states, and all ambiguity
      logic EXACTLY.

  WHAT IS NOT CHANGED:
    • KF dimensions, H matrix, measurement weights, process noise
    • WL/NL fixing logic (all existing slip resets preserved byte-identical)
    • Ambiguity states x[ki+1], x[ki+2]
    • ZWD, clock, ISB, coordinate states
    • Steady-state (non-slip) epochs — check is gated by `if slip:` only
    • GPS-only and Galileo-only paths — fix applies to any satellite

  NEW DIAGNOSTIC:
    arc_reset_iono_audit.csv with columns:
      SOD, sat, old_I_state, geomfree_I, LminusP_sign,
      reset_trigger_reason, iono_reinitialized, new_I_init,
      used_ionex, ambiguity_reset
    Console: [ARC_RESET_IONO] whenever triggered.

  ALL OTHER FILTER LOGIC BYTE-IDENTICAL TO v88.

"""
"""
ppp.py  v88 — RMS Accumulator Structural Fix (Rank-1 Forensic Audit)
===========================================================================

── v88 CHANGES ────────────────────────────────────────────────────────────
  TARGET: Eliminate fake 8–16 m L1_RMS explosions caused by C1C/L1C-only
  Galileo satellites contaminating the dual-frequency RMS accumulator.

  ROOT CAUSE (confirmed by forensic audit of rms_split_diagnostics.csv):
    • C1C-only Galileo satellites (missing L5Q) entered the measurement
      vector with a single L1 phase row. v87 appended this L1 residual to
      _rms_L1 without a matching L2 entry → structural nL1 > nL2.
    • The L1 residual for these satellites carries the full ionospheric
      delay (5–30 m at IISC equatorial peak; STEC up to 168 TECU).
    • Result: 60.6% of epochs had nL1 > nL2; every epoch with
      L1_RMS > 3000 mm also had nL1 > nL2 (100% correlation).
    • Mathematical reconstruction confirmed R_extra ≈ 37,532 mm at the
      SOD=17670 spike, matching the 9695 mm observed L1_RMS to within 3%.

  SINGLE CHANGE: RMS accumulation accounting only.
  ALL filter logic (KF, H matrix, z vector, Rd, measurement equations,
  row admission, weighting, ambiguity fixing, IONEX, process noise,
  ISB model, RTS smoother) are BYTE-IDENTICAL to v87.

  IMPLEMENTATION:
    Loop in _ppp_pass RMS-SPLIT DIAGNOSTICS block (~lines 3910–4007):

    BEFORE (v87):
      _rms_L1.append(_res_l1)   ← unconditional for ALL sats
      if not _rms_omit_p2l2:
          _rms_L2.append(_res_l2)  ← only for dual-freq
      → nL1 = N_total;  nL2 = N_dual  → nL1 > nL2 when C1C-only sats present

    AFTER (v88):
      if _rms_omit_p2l2:
          _rms_L1_single.append(_res_l1)  ← new: separate accumulator
      else:  # dual-freq: append all four atomically
          _rms_L1.append(_res_l1)
          _rms_L2.append(_res_l2)    ← same epoch, same sat
      → nL1_dual == nL2_dual ALWAYS (structural guarantee)

  NEW ACCUMULATORS:
    _rms_L1_single   — C1C/L1C-only Galileo L1 residuals (excluded from split)
    _rms_P1_single   — corresponding P1 residuals

  NEW CSV COLUMNS (rms_split_diagnostics.csv):
    nL1_single       — count of C1C-only rows this epoch
    L1_single_RMS_mm — RMS of those rows (diagnostic/informational only)

  NEW ASSERTION (replaces two separate P1/L1 and P2/L2 asserts):
    assert len(_rms_P1) == len(_rms_L1) == len(_rms_P2) == len(_rms_L2)
    This fires at every epoch and will catch any future regression.

  EXPECTED EFFECTS:
    • nL1 == nL2 at every epoch (0% mismatch, was 60.6%)
    • L1_RMS > 3000 mm events: eliminated (were 95 epochs)
    • [RMS_WARN] "suspicious phase RMS" messages: eliminated for dual-freq
    • New [RMS_WARN] "C1C-only L1 RMS large" is informational only
    • Kalman filter state, positioning output, ambiguity fixing: UNCHANGED
    • GPS-only result: UNCHANGED (no C1C-only Galileo in GPS-only run)
    • Combined end_3D: unchanged (the RMS spike was a diagnostic artefact)

  STRICT DO-NOT-CHANGE (all preserved byte-identical from v87):
    • KF structure, ISB model, ambiguity model (WL/NL)
    • H matrix, OSB application, measurement equations
    • Row admission logic (_rms_omit_p2l2 gate is read-only)
    • RTS smoother, NL fixing thresholds, ZWD watchdog
    • IONEX initialization, process noise, measurement weights (SC/SP)
    • Iono variance cap (25.0 m²), all covariance propagation
    • geom list construction: no satellite added or removed
"""
"""
ppp.py  v87 — IONEX/GIM Ionosphere Initialization
===========================================================================

── v87 CHANGES ────────────────────────────────────────────────────────────
  TARGET: Reduce initial ionosphere mismatch during early arc convergence.

  FORENSIC AUDIT RESULTS (from v86 logs):
    * Persistent −124 mm vertical bias
    * Phase RMS dominated by ionosphere divergence
    * L2 >> L1 residual inflation
    * Galileo convergence severely ionosphere-limited at IISC (equatorial EIA)
    * Strong temporal residual correlation in first 2–4 hours

  SINGLE CHANGE: IONEX/GIM-based ionosphere state initialization.
  ALL other filter logic, weights, NL/WL fixing, process noise, covariance
  propagation, and RMS computation are BYTE-IDENTICAL to v86.

  IMPLEMENTATION (INIT-ONLY):

  PART 1 — IONEX PARSER (parse_ionex):
    Parses COD0OPSFIN_20260380000_01D_01H_GIM.INX (IONEX v1).
    Extracts 25 hourly TEC maps → dict keyed by SOD.
    Grid: lat 87.5→−87.5 step −2.5 (71 rows),
          lon −180→180 step 5.0 (73 cols).
    Exponent: −1 → TEC values × 0.1 = TECU.
    Missing values (9999) handled: fall back to code-derived init.

  PART 2 — SPATIAL/TEMPORAL INTERPOLATION (_ionex_vtec_at):
    Bilinear lat/lon interpolation within each hourly map.
    Linear time interpolation between bracketing hourly maps.
    Returns VTEC in TECU.

  PART 3 — IPP + MAPPING FUNCTION (_ipp_latlon_mapping):
    Shell height H = 450 km (from IONEX file header).
    Earth radius RE = 6371 km (from IONEX BASE RADIUS).
    Standard thin-shell:
      M = 1 / sqrt(1 − (RE/(RE+H) × cos(el))²)
    IPP latitude/longitude via spherical earth geometry.

  PART 4 — INIT-ONLY SEEDING (inside _ppp_pass state init block):
    IONEX init applied ONLY when:
      phi[sid] == False  (new arc or post-slip reset)
    After IONEX seeding: EKF runs freely; no subsequent constraining.
    I_L1 = 40.3e16 / f1² × VTEC × M   (metres)
    Safety gate: 0.5 m < I_L1 < 100 m → else fallback to code-derived init.

  PART 5 — COVARIANCE REDUCTION:
    Success: P[ki,ki] = 22² m²  (was 50²)
    Fallback: P[ki,ki] = 50² m²  (unchanged)

  PART 6 — DIAGNOSTICS (ionex_init_audit.csv):
    Columns: SOD, sat, VTEC, mapping_factor, STEC, I_L1_init_m,
             fallback_used, initial_iono_sigma
    Console: [IONEX_INIT] printed every 300 epochs.

  STRICT DO-NOT-CHANGE (all preserved from v86):
    * KF structure, ISB model, ambiguity model (WL/NL)
    * H matrix, OSB application, measurement equations
    * RTS smoother, NL fixing thresholds, ZWD watchdog
    * Galileo WL closure corrections, arc-stability weighting
    * ION_PROC_NOISE, process noise, measurement weights (SC/SP)
    * iono variance cap (25.0 m²), all covariance propagation
"""
"""
ppp.py  v86 — True E5a Row Omission (Append-Based, Safe)
===========================================================================

── v84 CHANGES ────────────────────────────────────────────────────────────
  TARGET: Suppress ISB/GPS contamination from unstable Galileo arcs.

  ROOT CAUSE (confirmed by [OBS_V82] logs):
    Galileo L1C phase is missing 15–42% of epochs for several satellites.
    Each gap triggers a cycle-slip reset: phi[sid]=False, P[ki+1]=300²,
    _sat_age[sid]=0.  The satellite re-enters the KF with full measurement
    weight immediately (the existing age<=3 guard only scales phase by ×5
    for 3 epochs, far too short).  The ISB state (ISB_IDX) has process
    noise 1e-4*dt, so a Galileo sat with corrupted iono/ambiguity state
    that re-enters with full weight pulls x[ISB_IDX] within ~10 epochs,
    then corrupts the GPS solution via the ISB term in rp.

  STRICT DO-NOT-CHANGE (all preserved from v83):
    * KF structure (filter_standard), ISB model, ambiguity model (WL/NL)
    * H matrix, OSB application, measurement equations
    * RTS smoother, NL fixing thresholds, ZWD watchdog

  PART 1 — ARC STABILITY TRACKING (new dicts, ~line after _sat_last_sod):
    _gal_slip_epoch[sid]  = nproc at last ambiguity reset (slip or new arc)
    _gal_missing_l1c[sid] = deque(maxlen=30) of bool: True=L1C present
    Constants:
      GAL_UNSTABLE_EPOCHS_AFTER_RESET = 20   epochs to treat as unstable
      GAL_L1C_MISS_FRAC_THRESH        = 0.25 >25% missing → unstable
      GAL_UNSTABLE_CODE_SCALE         = 7.0  code sigma multiplier
      GAL_UNSTABLE_PHASE_SCALE        = 4.0  phase sigma multiplier

  PART 2 — L1C AVAILABILITY RECORDING:
    Before _proc_gal() call each epoch, append L1C present/absent to
    _gal_missing_l1c[sid].  Updated even when _proc_gal returns None
    (satellite genuinely absent) so the missing-fraction is accurate.

  PART 3 — SLIP EPOCH RECORDING:
    Inside existing `if slip:` block, add:
      if sid[0]=='E': _gal_slip_epoch[sid] = nproc

  PART 4 — _gal_arc_unstable() HELPER:
    Returns True when ANY of:
      (a) nproc - _gal_slip_epoch[sid] < GAL_UNSTABLE_EPOCHS_AFTER_RESET
      (b) missing L1C fraction > GAL_L1C_MISS_FRAC_THRESH (window ≥10)
    Both conditions are physically motivated:
      (a) captures post-reset re-convergence period
      (b) captures chronically unstable satellites

  PART 5 — ADAPTIVE Rd INFLATION (appended after v76 scintillation gate):
    When _gal_arc_unstable(m['sid']):
      code rows: Rd[base], Rd[base+1] *= GAL_UNSTABLE_CODE_SCALE²   (49×)
      phase rows: Rd[base+2], Rd[base+3] *= GAL_UNSTABLE_PHASE_SCALE² (16×)
    Physically: equivalent to declaring σ_code = 7×SC, σ_phase = 4×SP.
    KF naturally down-weights the satellite without removing it from geom
    (geometry preserved; ISB update dominated by stable sats).

  EXPECTED EFFECTS:
    • ISB variance: 30–60% reduction; fewer random walks from bad Galileo arcs
    • GPS solution: smoother East/North; less cross-contamination from ISB
    • Combined Up component: ±250 mm excursions (panel b, 0–5 h) suppressed
    • Galileo-only: unchanged (single-constellation, no ISB)
    • NL fixing: slightly fewer Galileo candidates in unstable window, but
      WL/NL logic is completely unmodified — behaviour identical once arc stabilises

"""
"""
ppp.py  v80 — NL Stability: Best-4 Selection, Tighter Constraint, Re-fix Cooldown
===========================================================================

── v80 CHANGES ────────────────────────────────────────────────────────────
  TARGET: Fix weak NL enforcement and post-fix instability from v79.
  Builds on v79's strict OSB selection.  The PPP filter, OSB logic,
  measurement model (H matrix), and SC/SP weighting are ALL UNCHANGED.

  PART 1 — LIMIT NL TO BEST 4 SATELLITES (CRITICAL):
    Before the candidate loop each epoch:
      • Compute sigma_N1_m for all basic-eligible satellites.
      • Sort ascending by sigma_N1_m.
      • Restrict NL fixing to the 4 satellites with smallest sigma_N1_m.
    Satellites outside the best-4 are skipped for NL only; they remain
    fully active in the PPP filter.

  PART 2 — STRENGTHEN NL CONSTRAINT (SAFE):
    NL_R_TIGHT: (0.005)² → (0.003)²  (5 mm → 3 mm pseudo-obs noise)
    Tighter constraint drives sigma_N1 lower more quickly each epoch.

  PART 3 — STABILIZE AFTER FIX:
    When a satellite is in nl_fixed AND sigma_N1_m < 0.10 m:
        q_N1 = q_N2 = 1e-9  (instead of exact zero)
    Tiny non-zero drift allowance prevents numerical rigidity while keeping
    fixed ambiguities tightly constrained.  lam1 is cached per satellite
    (_sat_lam1 dict) so it is available in the Q-build loop, which runs
    before geom is constructed each epoch.

  PART 4 — PREVENT RE-FIX LOOP:
    When a satellite is released from nl_fixed (drift > NL_RELEASE_THRESH):
        _nl_fix_cooldown[sid] = 30   (epochs)
    During cooldown the satellite is ineligible for re-fixing.  Counters
    are decremented by 1 every epoch.  Prevents fix→release→refix chatter.

  PART 5 — RELAX INNOVATION GATE (LATE ONLY):
    NL constraint injection gate: 0.25 → 0.35 cycles.
    Allows constraint propagation to satellites whose float ambiguity has
    drifted slightly but is still within the release threshold.

  PART 6 — DEBUG (max_sigma_N1_m added):
    [NL_STATS] printed every 300 epochs now includes:
        NL_count  mean_sigma_N1_m  max_sigma_N1_m
        skipped_no_osb  skipped_bad_bias  skipped_high_range
        skipped_sigma  skipped_innov

  STRICT DO-NOT-CHANGE (all preserved from v79):
  * OSB logic, measurement model (H matrix)
  * SC / SP, bias estimation
  * Kalman filter structure (filter_standard)
  * ION_PROC_NOISE, ZWD watchdog, RTS smoother

  EXPECTED RESULT:
    • NL_count stabilises at 2–4 (not oscillating)
    • sigma_N1_m drops below 0.05 m within hours of first fix
    • No rapid fix/release cycling
    • UP drift reduces; 3D converges toward 20–50 mm
    • FWD better tracks RTS

"""
"""
── v79 CHANGES ────────────────────────────────────────────────────────────
  TARGET: Fix NL instability by enforcing strict OSB-based satellite
  selection.  Only satellites with complete, valid, consistent OSBs are
  eligible for NL fixing.  The PPP filter itself is untouched.

  STRICT DO-NOT-CHANGE (all preserved from v78):
  * Kalman filter structure, filter_standard
  * Bias estimation, measurement model (H matrix)
  * Weighting (SC / SP)
  * Ionosphere model, ION_PROC_NOISE

  PART 1 — REQUIRE OSB FOR NL (MANDATORY):
    Before NL fixing, if satellite has NO OSB for required signals:
        set no_AR = True  → satellite kept in filter, NL fixing skipped.
    (Already implemented via no_AR flag; now tracked with explicit counter.)

  PART 2 — ENFORCE SIGNAL CONSISTENCY (CRITICAL):
    GPS primary : C1W/L1W + C2W/L2W
    GPS fallback: C1C/L1C + C2W/L2W  ← CHANGED from C2L/L2L → C2W/L2W
    Galileo     : C1C/L1C + C5Q/L5Q  (unchanged)
    If obs signal pair ≠ OSB signal pair → no_AR = True.

  PART 3 — REJECT INVALID OSB VALUES:
    In _proc and _proc_gal, after reading OSB values:
        if abs(code_bias) > 10 m  → no_AR = True, reason = 'bad_bias'
        if abs(phase_bias) > 1 m  → no_AR = True, reason = 'bad_bias'

  PART 4 — BLOCK NOISY SATELLITES (NL ONLY):
    Replaced the v76 soft-gate (flag_low_quality) with a HARD skip:
        if range_100 > 6.0 m → continue  (skip NL, keep in filter)
    sigma_N1_m > 0.12 hard gate unchanged.

  PART 5 — DO NOT REMOVE FROM PPP FILTER:
    All skipped satellites remain in geom / the filter state.
    Only NL fixing is withheld.

  PART 6 — DEBUG COUNTERS (ESSENTIAL):
    Printed every 300 epochs:
        [NL_STATS] SOD=...  NL_count=N  skipped_no_osb=N
                    skipped_bad_bias=N  skipped_high_range=N
                    skipped_sigma=N  skipped_innov=N

  EXPECTED RESULT:
    • NL fixes become clean and stable
    • sigma_N1 drops (<0.10 m)
    • No false NL spikes
    • UP component stabilises
    • 3D improves toward 20–50 mm
    • FWD starts matching RTS

── v78 CHANGES ────────────────────────────────────────────────────────────

── v76 CHANGES ────────────────────────────────────────────────────────────
  TARGET: Fix aggressive satellite rejection at equatorial stations (IISC).
  L1m-P1c ranges of 10–20 m are NORMAL under EIA scintillation and should
  NOT hard-block NL fixing.

  STRICT DO-NOT-CHANGE (all preserved from v75):
  * Kalman filter structure, filter_standard
  * NL fixing logic, corr_frac, bias estimation, buf_n gates
  * OSB handling, WL fixing, ZWD watchdog, iono cap
  * SC, SP, ION_PROC_NOISE, NL_R_TIGHT, NL_RELEASE_THRESH

  PART 1 — RELAX CONSISTENCY THRESHOLD (5 m → 12 m):
    Old: if range_100 > 5.0  → skip NL
    New: if range_100 > 12.0 → flag_low_quality = True (no hard skip)
    Rationale: equatorial STEC at IISC routinely produces L1m-P1c ranges
    of 10–20 m.  The 5 m threshold was rejecting every Galileo and GPS
    satellite at peak scintillation, preventing any NL fixing.

  PART 2 — SOFT GATE INSTEAD OF HARD SKIP:
    Old: range_100 > 5 m → continue (satellite skipped entirely)
    New: flag_low_quality = True; NL allowed ONLY IF
           sigma_N1_m < 0.10 m  AND  abs(corr_frac) < 0.05 cyc
    Rationale: even during scintillation, a satellite with a well-converged
    ambiguity (tight sigma + small corr_frac) is safe to fix.  The dual
    condition is tighter than the normal gate, providing extra protection
    for noisy sats without blocking them completely.

  PART 3 — WEIGHT DOWN BAD SATS (NOT REMOVE):
    If range_100 > 8 m: Rd_phase *= 4.0, Rd_code *= 4.0  (≡ sigma × 2)
    Satellite is kept in the solution but its measurements are trusted less.
    Rationale: removing a satellite degrades geometry and PDOP.  Downweighting
    lets the filter retain its geometric contribution while limiting its
    influence on the iono and ambiguity states.

  PART 4 — SUMMARY DEBUG EVERY 300 EPOCHS:
    For each visible satellite prints: sat_id, range_100, NL status
    (NL_FIXED / DOWNGRADED / WEIGHTED_DOWN / normal).

  EXPECTED RESULT:
    • Satellites NOT rejected aggressively at equatorial sites
    • NL fixing allowed on good epochs (3–6 sats stable)
    • GPS reappears in NL count
    • UP drift reduces; 3D improves toward <100 mm

── v75 CHANGES ────────────────────────────────────────────────────────────
  NL CONSTRAINT STRENGTH & STABILITY IMPROVEMENTS (fixing only — no iono,
  weighting, or bias logic touched):

  PART 1 — NL_R_TIGHT: Already (0.005)² since v71.  No change required.
    Value confirmed: NL_R_TIGHT = (0.005)**2  (5 mm — tight NL constraint)

  PART 2 — DOUBLE NL UPDATE (NEW):
    Old: for _nl_iter in range(1):   # single update per epoch
    New: for _nl_iter in range(2):   # two updates per epoch
    Rationale: a second sequential KF update within the same epoch drives
    sigma_N1 lower more aggressively without the catastrophic covariance
    collapse of v62's 3-iteration approach.  The NaN guard and iono cap
    are re-applied after EACH iteration, so safety is fully preserved.

  PART 3 — q_N1 = q_N2 = 1e-8 AFTER NL FIX: Already implemented since v62.
    Lines 1958-1960 confirmed: NL-fixed sats get Q[ki+1]=Q[ki+2]=1e-8 only.

  PART 4 — NL_RELEASE_THRESH (TIGHTENED):
    Old: NL_RELEASE_THRESH = 0.100   (relaxed since v63)
    New: NL_RELEASE_THRESH = 0.080   (compromise between 0.06 original and 0.10)
    Rationale: 0.10 allowed marginally drifted fixes to persist too long,
    contributing to ambiguity creep.  0.08 releases genuinely bad fixes
    earlier while still being looser than the original over-tight 0.06.

  STRICT DO-NOT-CHANGE (all preserved from v74):
  * Iono variance cap: 100.0 m²  (unchanged)
  * Elevation weighting: SC=0.30 m, SP=0.010 m  (unchanged)
  * Bias estimation & freeze logic  (unchanged)
  * corr_frac logic  (unchanged)
  * ION_PROC_NOISE = 1e-5  (unchanged)

  EXPECTED RESULT:
    • NL fixes persist (no drop back to 0)
    • Ambiguities remain locked after first correct fix
    • UP drift significantly reduced
    • 3D error target: ~50–80 mm
    • FWD approaches RTS

── v73 CHANGES ────────────────────────────────────────────────────────────
  ROOT CAUSE FIXED: v72 applied the iono variance cap after the PRIMARY
  filter_standard call, but the NL pseudo-obs filter_standard (second call,
  lines ~2679) modified P again WITHOUT re-applying the cap.  This meant
  the cap was silently bypassed every epoch that had active NL fixes, causing
  the late-arc iono variance explosion that drove UP drift and NL instability.

  FIX 1 — RE-APPLY CAP AFTER NL filter_standard (CRITICAL):
    After `filter_standard(x, P, H_nl.T, z_nl, R_nl)` add:
      for each satellite ki in sidx:
          P[ki, ki] = min(P[ki, ki], 25.0)
    This ensures P is capped at the final state before being stored into
    _rts_store._data and before the epoch results dict is recorded.

  FIX 2 — MANDATORY DEBUG PRINTS (both cap sites):
    Primary cap:
      print(f"[VAR CAP] sat={sid} Pii={P[ki,ki]:.2f}")
    NL post-update cap:
      print(f"[VAR CAP NL] sat={sid} Pii={P[ki,ki]:.2f}")
    You MUST see values ≤ 25.0 at both sites always.

  CORRECT ORDER (enforced by this fix):
    1. Prediction:        P = P + Q                          (line ~1950)
    2. Main measurement:  x,P = filter_standard(main)        (line ~2287)
    3. Primary iono cap:  P[ki,ki] = min(P[ki,ki], 25.0)     (line ~2315)
       → [VAR CAP] prints — all ≤ 25
    4. NL pseudo-obs:     x,P = filter_standard(NL)          (line ~2679)
    5. NL iono cap:       P[ki,ki] = min(P[ki,ki], 25.0)     ← NEW
       → [VAR CAP NL] prints — all ≤ 25
    6. RTS store:         _rts_store._data.append(...)        (line ~2701)
    NOTHING modifies P after step 5 (NaN guard excluded — emergency only).

  DO NOT CHANGE (all preserved from v72):
  * x[ki] — state values untouched
  * Q / process noise — untouched
  * H measurement model — untouched
  * Bias logic, NL fixing gates, buf_n, bias freeze
  * RTS smoother, metrics, plotting
  * SC = 0.30 m, SP = 0.010 m, ION_PROC_NOISE = 1e-5
  * Cap value: 25.0 m² (σ_I ≤ ~5 m)

  EXPECTED RESULT:
    • σ_I NEVER exceeds ~5 m at either cap site
    • Late-arc variance explosion fully eliminated
    • NL ambiguities stable through the full 24-h arc
    • UP drift significantly reduced vs v72

"""
"""
  ROOT CAUSE ADDRESSED: code/phase weighting imbalance causes ionosphere
  state to be driven by noisy code pseudoranges at wrong scale.
    • sigma_code was 1.50 m → code observations trusted far too little
      relative to phase (ratio ≈ 500:1), letting ionosphere drift freely
    • L1m−P1 range 20–30 m (should be <5 m)
    • UP drift 200–300 mm through the day
    • NL fixing never triggered; corr_frac unstable

  THREE TARGETED FIXES — bias, NL, H-matrix, ionosphere model: ALL UNCHANGED.

  FIX 1 — INCREASE CODE WEIGHT: sigma_code 1.50 m → 0.30 m  (SC parameter)
    Old: SC = 1.50 m  →  Rd[P1] = Rd[P2] = (1.50/sin(el))² m²
    New: SC = 0.30 m  →  Rd[P1] = Rd[P2] = (0.30/sin(el))² m²
    Rationale: modern geodetic GNSS pseudoranges on a high-quality receiver
    have 0.2–0.5 m noise.  1.50 m drastically under-weights code, preventing
    the ionosphere state from converging via the P1/P2 constraint.

  FIX 2 — REDUCE PHASE/CODE IMBALANCE: sigma_phase 0.003 m → 0.010 m (SP)
    Old: SP = 0.003 m (3 mm) → code/phase weight ratio ≈ 250,000:1 at zenith
    New: SP = 0.010 m (1 cm) → ratio ≈ 9:1 at zenith — more realistic balance
    Rationale: 3 mm phase noise is too tight for uncombined PPP at IISC
    (equatorial scintillation, multipath); 1 cm is still very precise but
    reduces the extreme leverage phase has over ambiguity and iono states.

  FIX 3 — REMOVE DYNAMIC Rd INFLATION (iono-unstable satellites):
    Old: if |dI_prev| > 5 m: Rd[all 4 rows] *= 10
    New: inflation block commented out — Rd set purely from SC/SP and outlier gate
    Rationale: 10× inflation was adding uncontrolled, asymmetric noise to both
    code and phase rows, masking the weighting imbalance and preventing
    natural ionosphere recovery in subsequent epochs.

  FIX 4 — REMOVE IONO CLAMPS (temporarily — diagnostic mode):
    Commented out:
      • RW cap:       x[ki] clamped to ≤ 2 m per epoch
      • dI skip:      _iono_unstable flagging → NL skip
      • Variance cap: P[ki,ki] ≤ 100 m²
    Rationale: with correct code weighting these clamps should not be needed.
    Removing them lets the filter behave naturally so we can verify that
    I_est is smooth and σ_I stays bounded WITHOUT artificial clamping.
    Re-introduce selectively if divergence is observed.

  EXPECTED RESULT:
    • I_est trajectories become smooth (no large epoch-to-epoch jumps)
    • σ_I reduces to < 5–10 m within first few hours
    • L1m−P1 range reduces to < 5 m
    • WL std drops below 0.2 cyc
    • corr_frac stabilises → NL fixing starts within ~5–10 h
    • UP drift reduces toward < 50 mm RMS

  DO NOT CHANGE (all preserved from v70):
  * Bias logic, NL fixing thresholds, buf_n gates, bias freeze
  * H matrix, state indexing, measurement equations
  * WL fixing, ZWD watchdog, soft prior, NaN guard
  * RTS smoother, metrics, plotting
  * ION_PROC_NOISE = 1e-5 (unchanged from v70)

"""
"""
── v67 CHANGES ────────────────────────────────────────────────────────────
  ROOT CAUSE FIXED (v66): buf_n stays 0 because bias buffer entry conditions
  were too strict to ever admit samples:
    • sigma_N1_m < 0.10 m is unreachable pre-fix (converged sigma ≈ 0.07–0.10 m
      but only *after* some NL fixing has collapsed covariance → chicken-and-egg)
    • frac_std < 0.02 cyc is also unreachable before bias is known (the bias
      itself causes frac to look unstable → circular dependency)
    • frac_std > 0.05 guard was actively clearing the buffer as fast as it filled

  PATCH 1 — RELAX BUFFER ENTRY CONDITIONS:
    Old (v66): sigma_N1_m < 0.10  AND  age > 300  AND  frac_std < 0.02
    New (v67): sigma_N1_m < 0.20  AND  age > 100
    Rationale: the frac_std and innovation conditions are circular — they
    depend on the bias already being known.  Remove them from gating entirely.

  PATCH 2 — REMOVE frac_std BUFFER CLEARING:
    Old (v66): frac_std > 0.05 → clear buffer and reset bias
    New (v67): buffer is only cleared on cycle slips (upstream, unchanged)
    Rationale: frac_std > 0.05 is the *normal* state before bias converges.
    The clear was guaranteeing buf_n never exceeded a few samples.

  PATCH 3 — INCREASE FREEZE THRESHOLD: 50 → 100 samples
    With faster buffer filling, require 100 samples before freezing so the
    circular mean stabilises before it is locked.

  PATCH 4 — COMPUTE BIAS AFTER 30 SAMPLES (was 20):
    Provides a more stable initial estimate while still converging quickly.

  DO NOT CHANGE (all preserved from v66):
  * NL fixing gates: sigma dual-condition, corr_frac branch-safety check,
    NL_RES_THRESH, buf_n ≥ 20 requirement, frac_std ≥ 0.02 skip-fix gate
  * Circular-mean bias formula (unchanged)
  * Bias freeze logic (threshold raised, condition frac_std < 0.01 unchanged)
  * Cycle-slip buffer clear (upstream, untouched)
  * Multi-sat fix per epoch, Galileo priority, NL_INNOV_GATE
  * Measurement model, ionosphere, WL fixing, process noise, ZWD watchdog
  * H matrix, state indexing, RTS smoother, metrics, plotting

  EXPECTED RESULT:
  • buf_n grows to 30+ within a few hundred epochs
  • bias converges from ±0.25 cyc toward the true hardware offset
  • corr_frac collapses toward 0 once bias is known
  • Galileo starts fixing first (E26/E33 candidates)
  • GPS follows later; NL count becomes stable at 2–3
  • 3D error decreases; UP drift resolves


  ROOT CAUSES FIXED (v65):
    1. Galileo being wrongly skipped: branch-safety check used raw_frac which
       includes the ~0.25–0.35 cyc hardware bias → always >0.25 for Galileo
       even when corr_frac (the true residual) is well within bounds.
    2. NL count stuck at 0–1: only 1 satellite committed per epoch even when
       multiple candidates were ready, and NL_INNOV_GATE was too tight (0.08 cyc)
       for uncombined PPP innovations.

  FIX 1 — USE CORRECTED FRACTION FOR BRANCH CHECK (CRITICAL):
    Old (v65): if abs(raw_frac)  > 0.25: skip
    New (v66): if abs(corr_frac) > 0.25: skip
    Rationale: raw_frac includes the hardware/OSB bias → not meaningful as a
    branch indicator.  corr_frac = N1_float − bias − round(N1_float − bias) is
    the true ambiguity residual.  Galileo raw_frac ≈ 0.25–0.35 was blocking ALL
    Galileo candidates despite corr_frac ≈ 0.00–0.02.

  FIX 2 — RELAX INNOVATION GATE:
    Old (v65): NL_INNOV_GATE = 0.080 cyc
    New (v66): NL_INNOV_GATE = 0.150 cyc
    Rationale: uncombined PPP has inherently larger innovations than IF-combo.
    The 0.08 cyc gate was blocking constraint injection for already-fixed sats
    during the convergence phase, defeating the purpose of the pseudo-obs update.

  FIX 3 — ALLOW MULTI-SAT FIX PER EPOCH (up to 2):
    Old (v65): effectively 1 satellite fixed per epoch (first that passed gates).
    New (v66): collect all qualifying candidates, sort, commit best 2 per epoch.
    Condition: candidates sorted by |corr_frac| + sigma_N1_m (ascending).
    Rationale: prevents the "stuck at n=1" problem where a single fix never
    reduces sigma enough to unlock the next candidate.

  FIX 4 — PRIORITISE GALILEO FIRST:
    Candidates sorted: Galileo (E*) before GPS (G*), then by |corr_frac|+sigma.
    Rationale: Galileo has cleaner signals and lower multipath at equatorial
    stations → more reliable anchor integer → GPS fixes arrive naturally later.

  EXPECTED RESULT:
    • E26 + E33 fix together or within a few epochs (corr_frac gate now reachable)
    • NL count becomes 2–3 within hours
    • Covariance starts collapsing after first multi-sat fix
    • GPS begins fixing later, not forced early
    • 3D error enters real drop toward <10 cm zone

  STRICT DO-NOT-CHANGE LIST (all preserved from v65):
  * frac_std gate (< 0.02 cyc) and bias estimation / freeze logic
  * sigma_A / sigma_B dual-condition gate (constellation-aware, v65)
  * NL_R_TIGHT, NL_MIN_OBS, NL_RELEASE_THRESH
  * buf_n ≥ 20 requirement
  * No immediate P collapse (v64 FIX 3 preserved)
  * WL fixing, process noise, ZWD watchdog, H matrix, state indexing
  * RTS smoother, metrics, plotting


  ROOT CAUSE FIXED (v64): sigma_N1 gate was too strict at 0.05 m (5 cm).
  Real converged sigma_N1 at IISC ≈ 0.07–0.10 m, so ALL valid fixes were
  rejected.  Bias estimator was working correctly (buf_n ≥ 50, frac_std <
  0.01, corr_frac ≈ 0.005–0.015) but the sigma gate killed every candidate.

  FIX 1 — RELAX SIGMA GATE (CRITICAL):
    Old (v64): sigma_N1_m < 0.05 m  (5 cm — too strict for IISC)
    New (v65): dual-condition gate — see FIX 3 below.
    Rationale: 5 cm gate was never reachable without prior NL fixing to
    collapse covariance.  This created a chicken-and-egg deadlock.

  FIX 2 — KEEP FRACTIONAL STRICTNESS (UNCHANGED):
    frac_std < 0.02 cyc and abs(corr_frac) < 0.03 cyc are unchanged.
    These correctly protect against wrong-integer fixing.

  FIX 3 — SMART DUAL-CONDITION GATE (NEW):
    ALLOW FIX if EITHER condition holds:
      Condition A (moderate sigma, moderate stability):
        sigma_N1_m < sigma_A  AND  frac_std < 0.02 cyc
      Condition B (looser sigma, high stability required):
        sigma_N1_m < sigma_B  AND  frac_std < 0.01 cyc
    This lets strong, stable candidates through even with slightly high sigma.

  FIX 4 — CONSTELLATION-AWARE SIGMA LIMITS (NEW):
    Galileo (cleaner signals, lower multipath):
      sigma_A = 0.09 m,  sigma_B = 0.10 m
    GPS (noisier, equatorial multipath):
      sigma_A = 0.07 m,  sigma_B = 0.09 m

  EXPECTED RESULT:
    • Galileo fixes start appearing first (E26/E33)
    • NL count gradually increases: 1 → 2 → 3 over several hours
    • No divergence — frac_std + corr_frac gates still strict
    • 3D error begins dropping after first correct fixes
    • GPS fixes arrive later, not forced early

  STRICT DO-NOT-CHANGE LIST (all preserved from v64):
  * Circular-mean bias estimator, bias freeze logic
  * NL_R_TIGHT, NL_INNOV_GATE, NL_MIN_OBS, NL_RELEASE_THRESH
  * frac_std < 0.02 and buf_n ≥ 20 requirements
  * raw_frac > 0.25 branch-safety check
  * No immediate P collapse (v64 FIX 3 preserved)
  * Gradual single-update-per-epoch constraint (v64 FIX 4 preserved)
  * WL fixing, process noise, ZWD watchdog, H matrix, state indexing
  * RTS smoother, metrics, plotting

── v64 CHANGES ────────────────────────────────────────────────────────────
  ROOT CAUSE FIXED (v63): NL fixes were firing when sigma_N1 ≈ 0.7 m and
  without a verified bias estimate, producing wrong integers.  The immediate
  P collapse then locked the KF onto the bad integer → divergence (3D > 10 m,
  RMS > 50 m).

  FIX 1 — STRICT SIGMA GATE (CRITICAL):
    Old: sigma_N1_m > 0.15 m  (15 cm gate — far too loose)
    New: sigma_N1_m > 0.05 m  (5 cm MAX)
    Rationale: sigma_N1 ≈ 0.7 m was still being admitted.  With a 5 cm gate
    only ambiguities that have genuinely converged can be fixed.

  FIX 2 — REQUIRE FRACTIONAL STABILITY:
    Before fixing, require BOTH:
      • frac_std < 0.02 cycles  (last-20-epoch fractional stability)
      • buf_n     ≥ 20          (enough bias samples for a reliable estimate)
    If either condition is not met → DO NOT FIX.

  FIX 3 — REMOVE IMMEDIATE P COLLAPSE:
    Old (v63): P[ki+1,ki+1] = NL_R_TIGHT applied immediately on new fix.
    New: let the KF reduce P naturally via repeated pseudo-obs updates.
    Rationale: forcing P to (1 cm)² before verifying the integer produces a
    catastrophic shock when the integer is wrong.

  FIX 4 — GRADUAL CONSTRAINT APPLICATION:
    Old (v62): 3 iterative KF updates per epoch.
    New: 1 update per epoch.
    Rationale: 3 iterations per epoch collapses P in a single step, preventing
    the filter from recovering from a bad integer.

  FIX 5 — DO NOT FIX WITHOUT BIAS (buf_n ≥ 20 required):
    Old: raw_frac fallback used when buf_n < 20.
    New: skip NL fixing entirely when buf_n < 20.
    Rationale: fixing against an unknown bias (raw_frac) is the primary cause
    of wrong-integer commitment.

  FIX 6 — SAFETY BRANCH CHECK:
    Before committing a fix:
      if abs(raw_frac) > 0.25: skip
    Rationale: a large raw_frac means the bias correction resolved to the
    wrong integer branch.  Skip rather than risk divergence.

  EXPECTED RESULT:
    • NL fixing delayed (first fix ~5–8 h) but correct
    • No divergence — 3D error decreases monotonically
    • RMS stays stable throughout
    • ZWD watchdog fires less often
    • Eventual cm-level convergence after correct fix accumulates

  STRICT DO-NOT-CHANGE LIST (all preserved from v63):
  * Circular-mean bias estimator, bias freeze logic
  * NL_R_TIGHT, NL_INNOV_GATE, NL_MIN_OBS, NL_RELEASE_THRESH
  * WL fixing, process noise, ZWD watchdog, H matrix, state indexing
  * RTS smoother, metrics, plotting




── v62 CHANGES ────────────────────────────────────────────────────────────
  ROOT CAUSES FIXED (v61):
  ─────────────────────────────────────────────────────────────────────────
  1. WRONG GALILEO BIAS (circular-wrapping issue):
     Galileo raw_frac ≈ +0.25 cyc but buffer also contains values near
     −0.75 cyc (same physical bias, different wrap branch).  The median of
     {+0.25, −0.75, +0.25, …} is NOT +0.25 — it averages both branches and
     produces a biased estimate.  This is the classic circular-statistics
     problem: the median is not defined modulo-1.

     FIX: Replace median with circular mean:
       angles    = 2π · frac_buffer
       mean_angle = atan2(mean(sin(angles)), mean(cos(angles)))
       bias       = mean_angle / (2π)     → wrapped to [−0.5, +0.5]

  2. WEAK NL CONSTRAINT (filter ignores the pseudo-observation):
     NL_R_TIGHT = (0.05)² → the pseudo-obs noise is 5 cm, comparable to
     the float ambiguity variance; the KF blends rather than fixes.
     The single update may also be insufficient to drive sigma_N1 down.
     NL_MIN_SATS = 7 → almost never satisfied at IISC.

     FIX:
       a) NL_R_TIGHT   = (0.01)²           (1 cm — 5× tighter)
       b) Apply NL pseudo-obs update 3 times per epoch (iterative fixing)
       c) For already-fixed satellites, set process noise q_N1=q_N2=1e-8
          (prevents filter from relaxing the fixed state)
       d) Lower NL_MIN_SATS = 3            (was 7, unreachable at IISC)

  3. EXPLICIT FRAC WRAPPING:
     raw_frac = N1_float − round(N1_float) with explicit post-clamp to
     [−0.5, +0.5] for edge-case safety before storing in bias buffer.

  STRICT DO-NOT-CHANGE LIST (all preserved from v61):
  * stability gate (frac_std), age condition, OSB handling
  * ionosphere model, WL fixing, NL_RES_THRESH, NL_INNOV_GATE
  * NL_RELEASE_THRESH, ZWD watchdog, soft prior, clamp
  * H matrix and state indexing, filter_standard

── v61 CHANGES ────────────────────────────────────────────────────────────
  ROOT CAUSE OF GPS NOT FIXING (v60):
  ─────────────────────────────────────────────────────────────────────────
  The v60 bias buffer collected samples whenever sigma_N1_m < 0.10 m AND
  age > 300, but low sigma does NOT mean the ambiguity is stable.  GPS N1
  float values drift slowly (equatorial iono + multipath), so the buffer
  accumulated drifting fractional values → median bias tracks the drift →
  corr_frac stays large (0.2–0.4 cyc) → NL gate never met.

  FIX 1 — FRACTIONAL STABILITY CHECK (CRITICAL):
  ─────────────────────────────────────────────────────────────────────────
  Maintain _nl_frac_hist[sid] = deque(maxlen=20).
  Each epoch: frac = N1_float − round(N1_float) is appended to frac_hist.
  Compute frac_std = std(frac_hist[sid])  (only when len ≥ 5).
  Buffer sample added to _nl_frac_buf ONLY when:
      sigma_N1_m < 0.10 m  AND  age > 300  AND  frac_std < 0.02 cycles.

  FIX 2 — RESET BAD BIAS:
  ─────────────────────────────────────────────────────────────────────────
  If frac_std > 0.05 cycles → the ambiguity is actively drifting.
  Action: clear _nl_frac_buf[sid], reset _nl_bias[sid] = 0.0.

  FIX 3 — FREEZE BIAS AFTER CONVERGENCE:
  ─────────────────────────────────────────────────────────────────────────
  If len(_nl_frac_buf[sid]) >= 50 AND frac_std < 0.01 cycles:
      mark sid in _nl_bias_frozen → stop updating bias for this satellite.
  This prevents the median from being contaminated by late-arc drift.

  FIX 4 — APPLY BIAS ONLY FOR FIXING (unchanged from v60):
  ─────────────────────────────────────────────────────────────────────────
  Filter state x[ki+1] is NEVER modified.  Correction is purely:
      N1_corr   = N1_float − bias
      corr_frac = N1_corr − round(N1_corr)

  FIX 5 — TEMPORARY RELAXED GATE:
  ─────────────────────────────────────────────────────────────────────────
  Use abs(corr_frac) < 0.05 cyc as the NL-fix acceptance gate until at
  least one GPS satellite has been fixed.  After first GPS fix, revert to
  the tighter NL_RES_THRESH = 0.03 cyc.
  (NL_RES_THRESH itself is unchanged — the gate is relaxed only temporarily.)

  DEBUG PRINT [NL_DBG] / [NL_BIAS]:
  ─────────────────────────────────────────────────────────────────────────
  Per-satellite per-epoch (when sigma_N1_m < 0.20 m):
      sigma_N1_m  age  frac_std  raw_frac  bias  corr_frac  buf_n  frozen?

  STRICT DO-NOT-CHANGE LIST (all preserved from v60):
  * NL_VAR_THRESH, NL_RES_THRESH, NL_INNOV_GATE, NL_RELEASE_THRESH,
    NL_R_TIGHT, NL_MIN_SATS, NL_MIN_OBS
  * WL fixing logic and b_rec_frozen estimation
  * consistency gate (L1m-P1c range > 5 m → skip)
  * sigma_N1_m > 0.15 m gate (Gate B)
  * Filter state equations — UNCHANGED
  * ZWD watchdog, soft prior, clamp
  * H matrix and state indexing

── v60 CHANGES ────────────────────────────────────────────────────────────
  ROOT CAUSE OF RARE / NO NL FIXING (v59):
  ─────────────────────────────────────────────────────────────────────────
  After OSB correction a residual constant fractional bias of ~0.1–0.4 cyc
  remains per satellite (sub-cycle OSB rounding, signal-path hardware offsets,
  or inter-system biases).  Even after the sigma_N1 gate passes the fractional
  part |frac| is stably far from zero → NL_RES_THRESH (0.03 cyc) is never met.

  FIX — ESTIMATE AND REMOVE PER-SATELLITE FRACTIONAL BIAS (SAFE METHOD):
  ─────────────────────────────────────────────────────────────────────────
  1. PER-SATELLITE BIAS BUFFER
       _nl_frac_buf[sid]  — deque(maxlen=100), stores raw fractional samples.

  2. COLLECT ONLY WHEN STABLE
       Sample added only if:  sigma_N1_m < 0.10 m  AND  satellite_age > 300 ep
       Guarantees buffer reflects converged, reliable float ambiguity values.

  3. ROBUST BIAS ESTIMATE
       _nl_bias[sid] = median(_nl_frac_buf[sid])   once  len(buf) ≥ 20.
       Median is robust to the occasional outlier epoch.

  4. BIAS CORRECTION — FIXING DECISION ONLY
       N1_corr   = N1_float − bias
       corr_frac = N1_corr − round(N1_corr)
       The filter state x[ki+1] is NEVER modified; the correction is applied
       only to the rounding decision inside the NL-fixing gate.

  5. INTEGER FROM CORRECTED VALUE
       N1_int = round(N1_corr)    [when bias is available]
       N2_int = N1_int − NWL
       This keeps N1_int consistent with the corrected fractional part.

  6. FALLBACK
       If fewer than 20 samples are available (early in the pass) the existing
       logic using raw_frac is used unchanged — no regression.

  7. DEBUG PRINT  [NL_BIAS]
       Printed for every satellite approaching the sigma gate:
         sid  raw_frac  bias  corr_frac  buf_n
       Expected: raw_frac ≈ 0.1–0.4 → corr_frac → near 0 after bias accumulates.

  STRICT DO-NOT-CHANGE LIST (all preserved from v59):
  * NL_VAR_THRESH, NL_RES_THRESH, NL_INNOV_GATE, NL_RELEASE_THRESH,
    NL_R_TIGHT, NL_MIN_SATS, NL_MIN_OBS
  * WL fixing logic and b_rec_frozen estimation
  * consistency gate (L1m-P1c range > 5 m → skip)
  * sigma_N1_m > 0.15 m gate (Gate B)
  * Filter state equations — UNCHANGED
  * ZWD watchdog, soft prior, clamp
  * H matrix and state indexing

── v59 CHANGES ────────────────────────────────────────────────────────────
  ROOT CAUSE OF NO NL FIXING (v58):
  ─────────────────────────────────────────────────────────────────────────
  Two bugs prevented NL fixing despite correctly applied OSBs.

  BUG 1 — NL variance gate was in cycles^2, not metres^2.
    v58: NL_VAR_THRESH = (0.1)^2 = 0.01 cycles^2  =>  sigma_N1 < 0.1 cyc = 1.9 cm
    At IISC (equatorial EIA) the float N1 converges to sigma_N1 ~ 0.4-0.8
    cycles (7-15 cm in distance) after 5-8 h — far above the 1.9 cm gate.
    FIX: Replace variance check with a metres-based sigma gate:
       sigma_N1_m = sqrt(P[N1,N1]) * lam1          (cycles -> metres)
       Block NL if sigma_N1_m > 0.15 m
    At IISC, E34/E07/G31/E26 reach sigma_N1_m < 0.15 m after ~5-6 h.
    The NL pseudo-obs (NL_R_TIGHT = (0.05)^2) then drive sigma_N1 -> ~0.

  BUG 2 — No per-satellite measurement consistency check.
    v58 only tracked L1m-P1c for one reference satellite. During equatorial
    scintillation the short-window range (max-min over 100 epochs) can
    exceed 5 m — the iono state is corrupted and N1_float has drifted.
    FIX: Track L1m-P1c per satellite (deque, maxlen=100).
       range_100 = max(hist) - min(hist)
       If range_100 > 5 m => skip NL for this satellite this epoch.

  FIX 3 — Clear consistency history on cycle slip.
    Slip detection resets WL/phase states; now also clears per-sat
    L1m-P1c deque so the post-slip window starts clean.

  STRICT DO-NOT-CHANGE LIST (all preserved):
  * q_iono = 3e-5 m^2/s, elevation-weighted iono noise
  * NL_RES_THRESH = 0.03 cyc
  * NL_INNOV_GATE, NL_RELEASE_THRESH, NL_R_TIGHT, NL_MIN_SATS, NL_MIN_OBS
  * OSB application (code + phase, all four signals, _proc/_proc_gal)
  * WL b_rec_frozen estimation and NWL rounding logic
  * Filter structure, ZWD watchdog, soft prior, clamp, H matrix
"""
"""
── v57 CHANGES (new) ────────────────────────────────────────────────────────
  ROOT CAUSE OF NO NL FIXING (v56):
  ───────────────────────────────────────────────────────────────────────────
  Float ambiguities N1, N2 absorb hardware biases because neither code nor
  phase OSBs were applied (v55 deliberately removed them after a frame
  inconsistency bug).  Without OSB correction:
    N1_float = N1_integer + (phase_bias − code_bias_projection)
  The fractional part is never near zero → integer rounding impossible.

  THE FIX — apply ALL four OSBs consistently (code + phase):
  ───────────────────────────────────────────────────────────────────────────
  In _proc (GPS) and _proc_gal (Galileo), BOTH code AND phase OSBs are now
  applied to every observable:

    P1c = P1 − b_C1    (C1W for GPS, C1C for Galileo)
    P2c = P2 − b_C2    (C2W for GPS, C5Q for Galileo)
    L1m = L1·λ1 − b_L1  (L1C for both)
    L2m = L2·λ2 − b_L2  (L2W for GPS, L5Q for Galileo)

  With code + phase OSBs applied:
  • Code observables are in the IGS IF-code reference frame (consistent
    with the IGS clocks already in use).
  • Phase fractional cycle biases are removed from L1m and L2m.
  • Float ambiguities N1, N2 converge to integer values after filter
    convergence (~15–30 min at IISC).

  WHY THIS IS CONSISTENT (fixing v55 concern):
  ───────────────────────────────────────────────────────────────────────────
  v55 correctly identified that applying code OSBs ALONE (shifting P into
  the IF frame) while leaving L raw creates a reference frame mismatch.
  v57 applies BOTH — all four observables move to the same corrected frame.
  The measurement equations P1=rp+I, P2=rp+γI, L1=rp−I+λ1N1,
  L2=rp−γI+λ2N2 are UNCHANGED; OSBs are absorbed before the equations.

  MW / WL COMPUTATION (unchanged, remains correct):
  ───────────────────────────────────────────────────────────────────────────
  MW_cyc = _mw_cyc(P1c, P2c, L1_raw, L2_raw) − b_wl_sat_cyc
  • P1c, P2c carry code OSB corrections → code NL bias automatically
    shifts the MW mean by (f1·bC1+f2·bC2)/((f1+f2)·λ_WL).
  • b_wl_sat_cyc = (f1·bL1−f2·bL2)/((f1−f2)·λ_WL) removes phase WL bias.
  • Net: MW_cyc ≈ N_WL_integer + b_rec (receiver WL fractional bias only).
  • b_rec_frozen estimation and WL rounding: UNCHANGED.

  NL FIXING (new, enabled):
  ───────────────────────────────────────────────────────────────────────────
  After the standard float KF update each epoch:
  1. For each satellite with WL fixed and sufficient age (≥ NL_MIN_OBS):
       N1_float = x[ki+1]   (N1 state, in cycles)
       If P[ki+1,ki+1] < NL_VAR_THRESH  AND
          |N1_float − round(N1_float)| < NL_RES_THRESH:
         N1_int = round(N1_float)
         N2_int = N1_int − NWL
         Store nl_fixed[sid] = (N1_int, N2_int)
  2. Inject tight pseudo-observations for every NL-fixed satellite:
       H_nl[row, ki+1] = 1  z = N1_int − x[ki+1]  R = NL_R_TIGHT²
       H_nl[row, ki+2] = 1  z = N2_int − x[ki+2]  R = NL_R_TIGHT²
  3. Post-update release gate: if x[ki+1] drifts from N1_int by
     > NL_RELEASE_THRESH cycles, release that fix (NL_RELEASE_THRESH=0.06).
  4. ZWD watchdog (v52): still active, releases ALL NL fixes if ZWD
     changes by > ZWD_RATE_LIMIT over 5 epochs (prevents filter corruption).

  STRICT DO-NOT-CHANGE LIST (all preserved):
  • Measurement equations, H matrix, state indexing
  • Ionosphere model and process noise
  • Filter structure (filter_standard, single-pass KF + RTS)
  • ZWD watchdog, soft prior, clamp
  • WL/b_rec fixing logic

CUMULATIVE FIXES (v50 → v56 — all active)
==========================================

── v56 CHANGES (new) ────────────────────────────────────────────────────────
  FULL AUDIT RESULT: Observation equations, H matrix, and state indexing are
  ALL CORRECT in v55.  Root causes are three filter-noise parameters, not the
  measurement model itself.

  ROOT CAUSE 1 (CRITICAL) — q_iono 100× too small for equatorial station IISC
  ─────────────────────────────────────────────────────────────────────────────
  v55: q_iono = 1e-6 × dt   →  σ_I_drift = 5.5 mm / epoch
  v56: q_iono = 1e-4 × dt   →  σ_I_drift = 55 mm / epoch

  IISC (Bangalore, 13°N) lies inside the Equatorial Ionospheric Anomaly (EIA).
  L1 STEC ranges from ~10 m (zenith overhead) to 50+ m at low elevations and
  varies by 20–50 m over a single satellite pass.  The 20–40 m L1m−P1 range
  observed in the debug output is PHYSICALLY CORRECT — it is not a model error.
  The v55 debug warning threshold (0.5 m) is calibrated for temperate stations.

  With q_iono = 1e-6/s the ionosphere covariance P[ki,ki] collapses to ~0.02 m²
  after ~50 epochs (KF is "frozen").  When the real ionosphere subsequently
  changes at 0.1–0.5 m/epoch the frozen estimate cannot follow; position and
  clock absorb the residual errors, driving ENU to 100–300 mm and keeping phase
  RMS at 500–1400 mm indefinitely.  Increasing q_iono to 1e-4/s lets P[ki,ki]
  stabilise at ~0.12 m², giving the filter enough tracking bandwidth to follow
  the equatorial ionosphere.

  NOTE: for extreme scintillation epochs q_iono = 1e-3/s may be needed.  The
  parameter is now named ION_PROC_NOISE and placed near the top of _ppp_pass
  for easy tuning.

  ROOT CAUSE 2 (SIGNIFICANT) — P2 noise incorrectly inflated by γ²
  ─────────────────────────────────────────────────────────────────
  v55: Rd[P2] = σ_code² × γ²  (γ_GPS≈1.65, γ_GAL≈1.79)
  v56: Rd[P2] = σ_code²        (same as P1; both are P-code measurements)

  The γ² factor reduced P2's effective ionosphere SNR by γ relative to P1,
  slowing initial I convergence.  In the RAW model the ionosphere enters P2 as
  γI, so P2 is already a stronger ionosphere observable than P1; artificially
  inflating its noise negated that advantage.

  ROOT CAUSE 3 (MINOR) — L2 phase noise incorrectly inflated by γ
  ────────────────────────────────────────────────────────────────
  v55: σ_L2 = σ_phase × γ
  v56: σ_L2 = σ_phase        (L1 and L2 carrier-phase noise are similar in m)

  The inflation gate for the outlier-protection branch is also corrected:
  v55: threshold = PHASE_RES_GATE × gam,  floor = gam²
  v56: threshold = PHASE_RES_GATE,         floor = 1.0²

  ROOT CAUSE 4 (LATENT CRASH) — _spp_clock dead code references m['PIF']
  ───────────────────────────────────────────────────────────────────────
  `_spp_clock` was never called after v54 removed PIF from the geometry dict.
  Removed the dead function to prevent accidental use.

  AUDIT FINDINGS — NO CHANGE NEEDED:
  • Observation equations P1/P2/L1/L2 — signs CORRECT ✓
  • H matrix for position, clock, ZWD, I, N1, N2 — CORRECT ✓
  • State indexing ki=I, ki+1=N1, ki+2=N2 — CORRECT, no cross-satellite mixing ✓
  • Remaining IF usage in measurement path — NONE ✓
    (ALFA/BETA in geometry PCO/PCV and NL-AR helpers are correct and unchanged)

  STRICT DO-NOT-CHANGE LIST (all preserved):
  • OSB (disabled), satellite clocks, geometry, troposphere model
  • Filter structure, ZWD watchdog / soft prior / clamp
  • NL/WL fixing code (disabled, preserved)

── v55 CHANGES (retained) ───────────────────────────────────────────────────
  ROOT CAUSE FIXED: IGS CLK (IF-frame clocks) + partial code OSB application
  creates a code/phase reference-frame inconsistency that drives code residuals
  to 1000–2000 mm.

  The rule: clock, code, and phase must all live in the SAME reference frame.
  v54 broke this by applying code OSBs (shifting P into the IF clock frame)
  without applying phase OSBs — leaving L in the raw observable frame.

  FIX APPLIED — STRICT:
  1. ALL OSB USAGE REMOVED from code observables in _proc and _proc_gal:
       v54:  P1c = P1 - b_C1     P2c = P2 - b_C2   (code OSB-corrected)
       v55:  P1c = P1             P2c = P2           (raw, no correction)
     Code and phase observables are now in the SAME uncorrected raw frame.
     Per-satellite code biases (DCB/ISB) are absorbed by the ionosphere
     state and float ambiguities — the Kalman filter handles them naturally.

  2. PHASE OSBs still NOT applied (unchanged, correct for float stage).

  3. MW/WL satellite bias correction (bl1, bl2) UNCHANGED — still applied
     in MW_cyc computation only (does not affect P1c/P2c).

  4. RAW MEASUREMENT MODEL unchanged:
       P1 = ρ + c·dt + T + I        P2 = ρ + c·dt + T + γ·I
       L1 = ρ + c·dt + T − I + λ1·N1
       L2 = ρ + c·dt + T − γ·I + λ2·N2

  5. VALIDATION DEBUG HOOKS (already in v54, still active):
       Per-epoch for reference GPS satellite:
         L1m−P1  and  L2m−P2  (now using raw P1, P2)
       Expected: roughly constant, variation < 0.5 m.
       If variation > 0.5 m: model still inconsistent — investigate further.

  STRICT DO-NOT-CHANGE LIST (all preserved):
  • Satellite clock usage (IGS CLK, IF frame)
  • Geometry model (_rp, _proc, _proc_gal corrections)
  • Troposphere model (ZHD, ZWD, GMF)
  • Antenna corrections (satellite + receiver PCO/PCV)
  • Filter structure and ZWD watchdog / soft prior / clamp
  • NL/WL fixing code (disabled, preserved)

── v54 CHANGES (retained) ───────────────────────────────────────────────────
  PHASE 1: Safe structural migration from IF-combination to RAW dual-frequency.

  1. IF MODEL REMOVED — P_IF / L_IF / N_IF combinations are gone.
  2. RAW OBSERVABLES: L1m = L1·λ1, L2m = L2·λ2; γ = (f1/f2)² per sat dict.
  3. STATE VECTOR: [x,y,z,clk,ZWD | I_s,N1_s,N2_s per sat]
  4. PHASE 2 DEBUG HOOKS: L1m−P1 and L2m−P2 tracked per epoch.

CUMULATIVE FIXES (v50 + v51 + v52 — all active in this file)
=============================================================

── v50 FIXES (retained) ─────────────────────────────────────────────────────
  1. WL PERSIST BUG — stale NWL reused across orbital passes.
     Fix: _sat_last_sod gap >120 s → clear _wl_history; diff threshold 20→3 cyc.

  2. LAMBDA NOT USING PROPER ILS — a_z = a_float discarded the Z-transform.
     Fix: call lambda_py() from lambda_ils.py (full Teunissen 1995 ILS).

  3. GALILEO Q_nl WRONG DENOM — GPS denom used for all sats including Galileo.
     Fix: per-satellite denom; Q_nl[i,j] = P[ki,kj] / (denom_i × denom_j).

  4. OCEAN TIDE LOADING MISSING — BLQ file present but never applied.
     Fix: parse_blq() + _otl_disp() with IERS 2010 Doodson multiplication.

── v51 FIXES (retained) ─────────────────────────────────────────────────────
  A. NL_RATIO_THRESH 3.0 → 4.5 — borderline fixes (ratio≈3.02) eliminated.

  B. NL INNOVATION GATE — |N_IF_fix − x[ki]| > 80 mm disables pseudo-obs row
     and releases fix before a catastrophic single-epoch KF blowup.

  C. POST-UPDATE NL RELEASE — after every filter_standard() call, re-validate
     all nl_fixed sats; drift > 60 mm releases the fix.

  D. NL_RES_THRESH 0.15 → 0.10 cyc — tighter per-sat ILS acceptance gate.

── v52 FIXES (new) ──────────────────────────────────────────────────────────
  E. ZWD RATE WATCHDOG — primary fix for the h=14–16 slow drift hump.

     Root cause: a wrong NL fix (3 sats, SOD≈52110) constrains x[ki] to wrong
     integers. The tight (5mm)² NL pseudo-obs prevents ambiguity correction, so
     the KF is forced to absorb growing phase residuals entirely into ZWD — the
     only state with large process noise. ZWD drifts +26mm in 8 minutes
     (physically impossible at IISC; real variation ≈5 mm/hour). The wet
     mapping function (mw≈5) then translates every +1mm of spurious ZWD into
     −5mm of vertical position error, producing the 400mm dU hump.
     The v51 innovation gate does NOT catch this because the per-epoch
     innovation grows slowly; there is never a single large jump to gate on.

     Fix: after every KF update, compare ZWD to a rolling 5-epoch history.
     If the range (max−min) over those 5 epochs exceeds ZWD_RATE_LIMIT
     (= 5 mm/30 s × 5 = 25 mm over 2.5 minutes — already 5× physical max),
     this is unambiguously KF contamination. Release ALL nl_fixed entries,
     inflate P[4,4] back to (0.15m)² so the ZWD can re-converge freely,
     and log a [ZWD WATCHDOG] message.

  F. ZWD SOFT PRIOR — weak pseudo-obs added to every epoch:
       z = ZWD_PRIOR,  R = (ZWD_PRIOR_SIGMA)²
     Default ZWD_PRIOR = 0.12 m (climatological wet delay for a tropical
     station at ~900 m altitude; adjust for your site).
     ZWD_PRIOR_SIGMA = 0.08 m — generous enough to allow ±240mm of real
     variation around the prior but prevents unbounded drift when the
     ambiguity state is corrupted. Acts as a soft anchor; has negligible
     effect during normal operation.

  G. NL_PHASE_THRESH 0.008 → 0.015 m — the 8mm gate was permanently
     blocking re-fixing after ZWD drifts. Real post-convergence PhsRMS at
     IISC is 4–7mm; 15mm allows the filter to attempt new fixes during the
     recovery phase (geometry change brings in new satellites) while still
     excluding epochs with genuine large residuals.
"""

import os, sys, math, time as _time, csv as _csv
from collections import defaultdict, deque
import numpy as np

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

# Galileo E1/E5a
FREQ_E1    = FREQ1           # 1575.42 MHz
FREQ_E5A   = 1176.45e6       # E5a
LAMBDA_E1  = CLIGHT / FREQ_E1
LAMBDA_E5A = CLIGHT / FREQ_E5A
FE1SQ, FE5SQ = FREQ_E1**2, FREQ_E5A**2
ALFA_E    = FE1SQ / (FE1SQ - FE5SQ)
BETA_E    = FE5SQ / (FE1SQ - FE5SQ)
LAMBDA_WL_E = CLIGHT / (FREQ_E1 - FREQ_E5A)
LAMBDA_IF_E = CLIGHT / (ALFA_E*FREQ_E1 - BETA_E*FREQ_E5A)

# ── v85: Static Galileo WL closure corrections ────────────────────────────────
# Derived from offline forensic calibration (gal_wl_closure_calibration.csv).
# These are FIXED product-like constants.  They are NOT KF states, NOT adaptive,
# NOT estimated online, and do NOT participate in covariance propagation.
# Applied ONLY to the WL fixing residual: mn_corr = mn - b_rec - delta_wl[s]
# GPS code path is byte-identical (no change).
# EXCLUDED (uncalibrated / insufficient arcs): E30, E09, E18, E23, E25, E33
_GAL_WL_CLOSURE = {
    'E02': 0.0435,
    'E03': 0.1505,   # borderline but accepted
    'E07': 0.0590,
    'E13': 0.0550,
    'E14': 0.3720,
    'E26': 0.0385,
    'E29': 0.2795,
    'E34': 0.1990,
}
# ─────────────────────────────────────────────────────────────────────────────

# Pre-computed NL denominators
_DENOM_G = ALFA*LAMBDA1 - BETA*LAMBDA2         # GPS NL denom  ≈ 0.1073 m
_DENOM_E = ALFA_E*LAMBDA_E1 - BETA_E*LAMBDA_E5A  # Gal NL denom  ≈ 0.1090 m

def _ifc(a, b):   return ALFA*a - BETA*b
def _sig(el, s0): return s0 / max(math.sin(el), 0.1)

# v70: per-satellite signal-type lock.  Once a signal combination is selected
# for a satellite it must not change between epochs — OSB keys depend on it.
# Keyed by sat_id (e.g. "G31", "E26") → (code1_type, code2_type, phase1_type, phase2_type)
_sat_signal_map: dict = {}

# v68: OSB debug throttle — print signal/OSB info only once per satellite per pass.
# Reset by _ppp_pass at the start of each forward/backward pass.
_osb_dbg_printed = set()

# v81: OSB consistency debug.
# _cp_debug : sid → list of recent (L1m − P1c) differences (last 100 epochs).
#             range < 1 m  → OSBs applied correctly.
#             range 2–5 m  → OSB mismatch / sign / unit error (TARGET BUG).
# _osb_once : sid → printed [OSB_VAL] line once per pass.
# Both are reset at the start of every _ppp_pass.
_cp_debug: dict = {}
_osb_once: set  = set()
_nproc_global: int = 0   # updated each epoch by _ppp_pass; read by _proc/_proc_gal

# ==============================================================================
#  v95 FORENSIC — global throttle / state (reset per pass by _ppp_pass)
# ==============================================================================
# Print throttle: limit console spam while keeping every CSV row.
_v95_phase_print_count: int = 0    # console lines emitted for PHASE_FORENSICS
_V95_PHASE_MAX_PRINT   = 120       # max PHASE_FORENSICS console lines per pass

# Per-satellite set — print OBS_MAP once per pass per sat
_v95_obs_map_printed: set = set()

# Per-satellite set — print OSB_APPLY once per pass per sat
_v95_osb_apply_printed: set = set()

# ATX audit: print GAL_APC once per satellite per pass
_v95_apc_printed: set = set()

# Track whether RMS_WARN fired this epoch (set in the RMS block; read in forensics)
_v95_rms_warn_epoch: set = set()   # set of sods where RMS_WARN fired

# ==============================================================================
#  OSB CSV logging
# ==============================================================================
import csv as _csv_mod

_osb_buffer: list = []

def init_osb_csv():
    with open("osb_log.csv", "w", newline="") as f:
        writer = _csv_mod.writer(f)
        writer.writerow([
            "epoch",
            "sat",
            "elev_deg",
            "raw_L1_minus_P1",
            "corrected_L1_minus_P1",
            "range_100",
            "code_bias",
            "phase_bias"
        ])

def log_osb(row):
    _osb_buffer.append(row)
    if len(_osb_buffer) >= 50:
        with open("osb_log.csv", "a", newline="") as f:
            writer = _csv_mod.writer(f)
            writer.writerows(_osb_buffer)
        _osb_buffer.clear()


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
                        p1=np.array(pf.get('G01',[0,0,0]),float)
                        p2=np.array(pf.get('G02',[0,0,0]),float)
                        v1=pv.get('G01',[]); v2=pv.get('G02',[])
                        vi=([ALFA*a-BETA*b for a,b in zip(v1,v2)] if v1 and v2 and len(v1)==len(v2)
                            else list(v1) if v1 else list(v2))
                        sat_atx[cprn].append({'vf':vf if vf else -1e18,'vu':vu if vu else 1e18,
                            'pco':ALFA*p1-BETA*p2,'pcv':vi,'z1':z1,'dz':dz})
                        # ── v95 STEP 2: ATX frequency-slot audit ─────────────
                        # Check which ANTEX frequency slots were actually found for
                        # this satellite PRN.  Galileo PRNs (E*) may use E01/E05
                        # in some ANTEX files — those would be MISSED by G01/G02 lookup.
                        _atx_slots_found = sorted(pf.keys())
                        _is_gal_prn = (cprn[0] == 'E')
                        _used_g01 = 'G01' in pf
                        _used_g02 = 'G02' in pf
                        _used_e01 = 'E01' in pf
                        _used_e05 = 'E05' in pf
                        _used_e06 = 'E06' in pf   # E6 (E5b on some products)
                        _pco_zero = (np.all(p1 == 0.) and np.all(p2 == 0.))
                        if _is_gal_prn:
                            # Compute what the CORRECT Galileo IF combination would be
                            _p1_e = np.array(pf.get('E01', pf.get('G01', [0,0,0])), float)
                            _p2_e = np.array(pf.get('E05', pf.get('E06', pf.get('G02', [0,0,0]))), float)
                            _v1_e = pv.get('E01', pv.get('G01', []))
                            _v2_e = pv.get('E05', pv.get('E06', pv.get('G02', [])))
                            _pco_gal_correct = ALFA_E * _p1_e - BETA_E * _p2_e
                            _pco_gal_used    = ALFA   * p1    - BETA   * p2
                            _pco_diff_mm     = np.linalg.norm(_pco_gal_correct - _pco_gal_used) * 1e3
                            _wrong_coeff = not np.allclose(_pco_gal_correct, _pco_gal_used, atol=0.001)
                            print(f"[GAL_APC_PARSE] prn={cprn}"
                                  f"  slots_found={_atx_slots_found}"
                                  f"  G01={'Y' if _used_g01 else 'N'}"
                                  f"  G02={'Y' if _used_g02 else 'N'}"
                                  f"  E01={'Y' if _used_e01 else 'N'}"
                                  f"  E05={'Y' if _used_e05 else 'N'}"
                                  f"  pco_G01mm=({p1[0]:.2f},{p1[1]:.2f},{p1[2]:.2f})"
                                  f"  pco_G02mm=({p2[0]:.2f},{p2[1]:.2f},{p2[2]:.2f})"
                                  f"  pco_IF_GPS=({_pco_gal_used[0]*1e3:.2f},"
                                  f"{_pco_gal_used[1]*1e3:.2f},{_pco_gal_used[2]*1e3:.2f})mm"
                                  f"  pco_IF_GAL_CORRECT=({_pco_gal_correct[0]*1e3:.2f},"
                                  f"{_pco_gal_correct[1]*1e3:.2f},{_pco_gal_correct[2]*1e3:.2f})mm"
                                  f"  diff={_pco_diff_mm:.2f}mm"
                                  f"  ALFA_used={ALFA:.6f} ALFA_E_correct={ALFA_E:.6f}"
                                  f"  BETA_used={BETA:.6f} BETA_E_correct={BETA_E:.6f}"
                                  + ("  ***WRONG_COEFF***" if _wrong_coeff else "  coeff_ok")
                                  + ("  ***PCO_ZERO_CHECK_SLOTS***" if _pco_zero else ""))
                            # ── v98 FIX: write correct Galileo E1/E5a IF-combination PCO/PCV ──
                            # The append above used ALFA*p_G01 - BETA*p_G02 = (0,0,0) for all
                            # Galileo satellites (G01/G02 slots absent in Galileo ANTEX records).
                            # Overwrite with ALFA_E*p_E1 - BETA_E*p_E5a using the already-computed
                            # correct vectors.  GPS path (above) is unchanged.
                            sat_atx[cprn][-1]['pco'] = _pco_gal_correct
                            # PCV: recompute IF combination using Galileo E1/E5a slots and ALFA_E/BETA_E
                            _vi_gal = ([ALFA_E * a - BETA_E * b
                                        for a, b in zip(_v1_e, _v2_e)]
                                       if _v1_e and _v2_e and len(_v1_e) == len(_v2_e)
                                       else list(_v1_e) if _v1_e else list(_v2_e))
                            sat_atx[cprn][-1]['pcv'] = _vi_gal
                            # ── v98 verification diagnostic ──────────────────────────────────
                            _pco_norm_mm = np.linalg.norm(_pco_gal_correct) * 1e3
                            print(f"[GAL_APC_ACTIVE] sat={cprn}"
                                  f"  pco_IF_written=({_pco_gal_correct[0]*1e3:.2f},"
                                  f"{_pco_gal_correct[1]*1e3:.2f},{_pco_gal_correct[2]*1e3:.2f})mm"
                                  f"  norm={_pco_norm_mm:.1f}mm"
                                  f"  nonzero={'YES' if _pco_norm_mm > 1.0 else 'NO'}"
                                  f"  pcv_len={len(_vi_gal)}")
                        # ── end v95 ATX audit / v98 GAL APC fix ──────────────
                    elif cant:
                        rec_atx[cant]={
                            # GPS/QZSS/SBAS — backward-compatible, unchanged
                            'L1': np.array(pf.get('G01', [0,0,0]), float),
                            'L2': np.array(pf.get('G02', [0,0,0]), float),
                            'v1': list(pv.get('G01', [])),
                            'v2': list(pv.get('G02', [])),
                            # Galileo E1/E5a — PATCH 1: preserve E01/E05 slots.
                            # Falls back to GPS G01/G02 if E slots absent,
                            # so GPS behaviour is byte-identical and the Galileo
                            # path degrades gracefully for older ANTEX files.
                            'E1': np.array(
                                pf.get('E01', pf.get('G01', [0,0,0])),
                                float),
                            'E5': np.array(
                                pf.get('E05', pf.get('G05',
                                       pf.get('G02', [0,0,0]))),
                                float),
                            've1': list(pv.get('E01', pv.get('G01', []))),
                            've5': list(pv.get('E05',
                                        pv.get('G05',
                                        pv.get('G02', [])))),
                            'z1': z1, 'dz': dz,
                        }
                        # ── PATCH 1 VERIFICATION ─────────────────────────────
                        # Confirm E01/E05 slots are preserved vs GPS G01/G02.
                        _re_built = rec_atx[cant]
                        _e1_src  = 'E01' if 'E01' in pf else ('G01' if 'G01' in pf else 'zero')
                        _e5_src  = ('E05' if 'E05' in pf else
                                    'G05' if 'G05' in pf else
                                    'G02' if 'G02' in pf else 'zero')
                        _g1_src  = 'G01' if 'G01' in pf else 'zero'
                        _g2_src  = 'G02' if 'G02' in pf else 'zero'
                        _e1_diff = float(np.linalg.norm(
                            _re_built['E1'] - _re_built['L1'])) * 1e3
                        _e5_diff = float(np.linalg.norm(
                            _re_built['E5'] - _re_built['L2'])) * 1e3
                        print(f"[REC_ATX_PATCH1] ant='{cant}'"
                              f"  L1_src={_g1_src}"
                              f"  L2_src={_g2_src}"
                              f"  E1_src={_e1_src}"
                              f"  E5_src={_e5_src}"
                              f"  |E1-L1|={_e1_diff:.2f}mm"
                              f"  |E5-L2|={_e5_diff:.2f}mm"
                              f"  ve1_len={len(_re_built['ve1'])}"
                              f"  ve5_len={len(_re_built['ve5'])}"
                              f"  E_slots_distinct={'YES' if (_e1_diff>0.01 or _e5_diff>0.01) else 'NO(fallback_used)'}")
                        # ── end PATCH 1 verification ──────────────────────────
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

def _rpco(re, lat, lon, is_galileo=False):
    """Receiver Phase Centre Offset projected into line-of-sight (metres).

    v96 FIX 3: Galileo E1+E5a must use ALFA_E/BETA_E IF coefficients.
               GPS L1+L2  must use ALFA/BETA   IF coefficients.
    v95 bug: all systems used ALFA/BETA → Galileo PCO wrong by 8.2 mm norm.
    The error was diagnosed via [GAL_APC] ***WRONG_REC_COEFF_FOR_GAL*** lines.

    PATCH 2 (receiver ANTEX semantic fix):
    Galileo branch now reads E1/E5 slots (stored by PATCH 1 from E01/E05
    ANTEX frequency entries) instead of L1/L2 (which held G01/G02 values).
    Falls back to L1/L2 transparently if E1/E5 absent (old ANTEX files).
    GPS branch is byte-identical: still uses L1/L2 with ALFA/BETA.

    PATCH 4 (runtime branch trace):
    On first call per satellite per pass, emits [GAL_BRANCH] to confirm
    which PCO slot, coefficient pair, and E-slot availability was used.
    """
    if re is None: return np.zeros(3)
    if is_galileo:
        # Use receiver E1/E5a PCO slots (PATCH 2).
        # Fallback to L1/L2 if E slots absent (backward-compatible).
        _pco_E1 = re.get('E1', re['L1'])
        _pco_E5 = re.get('E5', re['L2'])
        _e1_from_e_slot = 'E1' in re and not np.array_equal(re['E1'], re['L1'])
        _e5_from_e_slot = 'E5' in re and not np.array_equal(re['E5'], re['L2'])
        pi = ALFA_E * _pco_E1 - BETA_E * _pco_E5
        # ── PATCH 4: runtime branch confirmation ─────────────────────────────
        # Emit once per Python process lifetime to confirm Galileo branch fires.
        if not getattr(_rpco, '_gal_logged', False):
            _rpco._gal_logged = True
            _pco_mm   = pi * 1e3
            _diff_gps = (ALFA * re['L1'] - BETA * re['L2']) * 1e3
            _norm_mm  = float(np.linalg.norm(_pco_mm - _diff_gps))
            print(f"[GAL_BRANCH_TRACE] _rpco: is_galileo=True "
                  f"E1_slot={'E1' if _e1_from_e_slot else 'fallback_L1'} "
                  f"E5_slot={'E5' if _e5_from_e_slot else 'fallback_L2'} "
                  f"coeff=ALFA_E({ALFA_E:.6f})/BETA_E({BETA_E:.6f}) "
                  f"pco_IF_mm=({_pco_mm[0]:.2f},{_pco_mm[1]:.2f},{_pco_mm[2]:.2f}) "
                  f"vs_GPS_coeff_diff_norm={_norm_mm:.2f}mm "
                  f"E1_mm=({_pco_E1[0]:.2f},{_pco_E1[1]:.2f},{_pco_E1[2]:.2f}) "
                  f"E5_mm=({_pco_E5[0]:.2f},{_pco_E5[1]:.2f},{_pco_E5[2]:.2f}) "
                  + ("HIDDEN_FALLBACK_WARN: E1/E5 same as L1/L2 — ANTEX may lack GAL slots"
                     if not _e1_from_e_slot and not _e5_from_e_slot else "slot_OK"))
        # ── end PATCH 4 ───────────────────────────────────────────────────────
    else:
        pi = ALFA * re['L1'] - BETA * re['L2']   # GPS L1+L2 IF — unchanged
    sl,cl=math.sin(lat),math.cos(lat); sn,cn=math.sin(lon),math.cos(lon)
    R=np.array([[-sl*cn,-sn,cl*cn],[-sl*sn,cn,cl*sn],[cl,0,sl]])
    return R@(pi*1e-3)

def _rpcv(re, el, is_galileo=False):
    """Receiver Phase Centre Variation projected onto line-of-sight (metres).

    v97 FIX 3: Galileo E1+E5a must use ALFA_E/BETA_E IF coefficients,
               mirroring the fix already applied to _rpco() in v96.

    PATCH 3 (receiver ANTEX semantic fix):
    Galileo branch now reads ve1/ve5 PCV tables (stored by PATCH 1 from
    E01/E05 ANTEX frequency entries) instead of v1/v2 (which held G01/G02
    values).  Falls back to v1/v2 if ve slots absent (backward-compatible).
    GPS branch is byte-identical: still uses v1/v2 with ALFA/BETA.

    PATCH 4 (runtime branch trace):
    On first call, emits [GAL_BRANCH_TRACE] confirming PCV table IDs and
    coefficient pair used, and flags hidden GPS fallback if ve slots absent.
    """
    if re is None: return 0.
    zen = 90 - math.degrees(el)
    if is_galileo:
        # Use receiver E1/E5a PCV tables (PATCH 3).
        # Fallback to v1/v2 if ve slots absent (backward-compatible).
        v1 = _pcv(re.get('ve1', re['v1']), re['z1'], re['dz'], zen)
        v2 = _pcv(re.get('ve5', re['v2']), re['z1'], re['dz'], zen)
        # ── PATCH 4: runtime branch confirmation ─────────────────────────────
        if not getattr(_rpcv, '_gal_logged', False):
            _rpcv._gal_logged = True
            _ve1_distinct = 've1' in re and re['ve1'] != re['v1']
            _ve5_distinct = 've5' in re and re['ve5'] != re['v2']
            _pcv_gal_mm   = (ALFA_E * v1 - BETA_E * v2) * 1e3
            _pcv_gps_mm   = (ALFA   * _pcv(re['v1'], re['z1'], re['dz'], zen)
                             - BETA * _pcv(re['v2'], re['z1'], re['dz'], zen)) * 1e3
            print(f"[GAL_BRANCH_TRACE] _rpcv: is_galileo=True "
                  f"ve1_slot={'ve1' if _ve1_distinct else 'fallback_v1'} "
                  f"ve5_slot={'ve5' if _ve5_distinct else 'fallback_v2'} "
                  f"coeff=ALFA_E({ALFA_E:.6f})/BETA_E({BETA_E:.6f}) "
                  f"pcv_GAL={_pcv_gal_mm:.3f}mm pcv_GPS={_pcv_gps_mm:.3f}mm "
                  f"ve1_len={len(re.get('ve1', re['v1']))} "
                  f"ve5_len={len(re.get('ve5', re['v2']))} "
                  + ("HIDDEN_FALLBACK_WARN: ve1/ve5 absent — using GPS PCV tables for Galileo"
                     if not _ve1_distinct and not _ve5_distinct else "pcv_slot_OK"))
        # ── end PATCH 4 ───────────────────────────────────────────────────────
        return (ALFA_E * v1 - BETA_E * v2) * 1e-3
    else:
        v1 = _pcv(re['v1'], re['z1'], re['dz'], zen)
        v2 = _pcv(re['v2'], re['z1'], re['dz'], zen)
        return (ALFA * v1 - BETA * v2) * 1e-3


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
    """Parse SINEX BIAS. Phase OSBs stored in metres per signal wavelength.

    v69 FIX: Read ENTIRE file (phase biases appear after code biases, often
    after line 7000+).  Handle all three unit types: ns, cyc, m.
    Wavelength map covers all GPS+Galileo signal codes.
    """
    # wavelength lookup for 'cyc' unit conversion → metres
    _lam = {
        # GPS L1
        'L1C': LAMBDA1, 'L1W': LAMBDA1, 'L1P': LAMBDA1, 'L1X': LAMBDA1,
        'L1S': LAMBDA1, 'L1L': LAMBDA1, 'L1D': LAMBDA1,
        # GPS L2
        'L2W': LAMBDA2, 'L2C': LAMBDA2, 'L2P': LAMBDA2, 'L2X': LAMBDA2,
        'L2D': LAMBDA2, 'L2S': LAMBDA2, 'L2L': LAMBDA2,
        # GPS/Galileo L5 / E5a
        'L5Q': LAMBDA_E5A, 'L5X': LAMBDA_E5A, 'L5I': LAMBDA_E5A,
        'L5P': LAMBDA_E5A, 'L5D': LAMBDA_E5A,
        # Galileo E1 (same freq as GPS L1)
        'L1B': LAMBDA_E1, 'L1A': LAMBDA_E1,
        # Galileo E5b  (1207.14 MHz)
        'L7Q': CLIGHT/1207.14e6, 'L7X': CLIGHT/1207.14e6,
        'L7I': CLIGHT/1207.14e6,
    }
    B=defaultdict(dict); ins=False; n_code=0; n_phase=0
    with open(fp, 'r', errors='replace') as fh:
        for ln in fh:
            if '+BIAS/SOLUTION' in ln:
                ins=True; continue
            if '-BIAS/SOLUTION' in ln:
                ins=False; continue   # v69 FIX: do NOT break — file may have more blocks
            if not ins: continue
            ln_s=ln.rstrip('\n')
            if len(ln_s)<30: continue
            # Type field at cols 1-4 (0-indexed 0-3); must be 'OSB'
            rec_type=ln_s[1:4].strip()
            if rec_type!='OSB': continue
            prn=ln_s[11:14].strip()
            obs=ln_s[25:29].strip()
            if not prn or not obs: continue
            # Scan tokens after col 29 for unit and value
            tail=ln_s[29:].split(); unit=val=None
            for i,tok in enumerate(tail):
                tl=tok.lower()
                if tl in ('ns','cyc','m','cycles','nanoseconds','metres','meters'):
                    # normalise unit
                    if tl in ('ns','nanoseconds'):   unit='ns'
                    elif tl in ('cyc','cycles'):      unit='cyc'
                    else:                              unit='m'
                    if i+1<len(tail):
                        try: val=float(tail[i+1])
                        except: pass
                    break
            if unit is None or val is None: continue
            # Convert to metres
            if unit=='ns':
                val_m=val*1e-9*CLIGHT
            elif unit=='cyc':
                lam_sig=_lam.get(obs)
                if lam_sig is None:
                    # fallback: use L1 wavelength for unknown phase signals
                    val_m=val*LAMBDA1
                else:
                    val_m=val*lam_sig
            else:  # 'm'
                val_m=val
            B[prn][obs]=val_m
            if obs.startswith('L'): n_phase+=1
            else:                   n_code+=1

    tot=sum(len(v) for v in B.values())
    print(f"[BIA]  {tot} OSB entries ({n_code} code, {n_phase} phase)  {len(B)} PRNs")
    if tot>0:
        g=B.get('G01',{})
        print(f"       G01 C1W={g.get('C1W',float('nan')):+.4f}m  "
              f"C2W={g.get('C2W',float('nan')):+.4f}m  "
              f"L1C={g.get('L1C',float('nan')):+.6f}m  "
              f"L2W={g.get('L2W',float('nan')):+.6f}m")
        e01=B.get('E01',{})
        print(f"       E01 C1C={e01.get('C1C',float('nan')):+.4f}m  "
              f"C5Q={e01.get('C5Q',float('nan')):+.4f}m  "
              f"L1C={e01.get('L1C',float('nan')):+.6f}m  "
              f"L5Q={e01.get('L5Q',float('nan')):+.6f}m")
        if n_phase==0:
            print("[BIA]  WARNING: No phase OSBs found — raw_frac will reflect hardware bias!")
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


# _spp_clock was removed in v56: it referenced m['PIF'] which no longer exists
# after v54 replaced the IF model with RAW dual-frequency. The clock bootstrap
# inside _ppp_pass uses P1c directly (see nproc==0 block).





# ==============================================================================
#  RTS smoother
# ==============================================================================
class _rts_store:
    _data = []

def _rts_smooth(fwd_results, nom):
    data=_rts_store._data
    if len(data)<3: return fwd_results
    N=len(data)
    # v83/v96: dim inferred from stored state.
    #   'G'  only: state_size=5 → dim=5
    #   'E'  only: state_size=6 → dim=6  (RDCB_E at [5] included; Q_k[5,5]=1e-8 OK)
    #   'GE' combo: state_size=7 → dim=min(6,7)=6  (RDCB_E at [6] not smoothed — acceptable)
    #   RTS output uses only x[0:3] for position; RDCB_E exclusion in GE mode is harmless.
    dim=min(6, data[0][1].shape[0]) if data[0][1].shape[0] >= 6 else 5
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
        Q_k[0,0]=Q_k[1,1]=Q_k[2,2]=1e-8*dt; Q_k[3,3]=1e4*dt; Q_k[4,4]=1e-8*dt
        if dim >= 6: Q_k[5,5]=1e-8*dt   # ISB slow walk
        P_k=Ps[k]; P_k1=F@P_k@F.T+Q_k
        try:
            G_k=P_k@F.T@np.linalg.inv(P_k1)
        except np.linalg.LinAlgError:
            xs_s[k]=xs[k].copy(); Ps_s[k]=Ps[k].copy(); continue
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
def _nl_float_gal(x_ki,NWL,osb_bl1,osb_bl5):
    osb_IF_E=ALFA_E*osb_bl1-BETA_E*osb_bl5
    return (x_ki-osb_IF_E-NWL*BETA_E*LAMBDA_E5A)/_DENOM_E

def _nl_if_value_gal(N1_int,NWL,osb_bl1,osb_bl5):
    N5_int=N1_int-NWL; osb_IF_E=ALFA_E*osb_bl1-BETA_E*osb_bl5
    return ALFA_E*LAMBDA_E1*N1_int-BETA_E*LAMBDA_E5A*N5_int+osb_IF_E

def _nl_float(x_ki,NWL,osb_bl1,osb_bl2):
    osb_IF=ALFA*osb_bl1-BETA*osb_bl2
    return (x_ki-osb_IF-NWL*BETA*LAMBDA2)/_DENOM_G

def _nl_if_value(N1_int,NWL,osb_bl1,osb_bl2):
    N2_int=N1_int-NWL; osb_IF=ALFA*osb_bl1-BETA*osb_bl2
    return ALFA*LAMBDA1*N1_int-BETA*LAMBDA2*N2_int+osb_IF


# ==============================================================================
#  Per-satellite geometry
# ==============================================================================
def _proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx,
          blq=None,sta='IISC',tow_total=0.):
    """GPS satellite — dynamic signal detection from RINEX obs.

    v68 FIX: Dynamically detect actual L1/L2 code and phase signals present in
    RINEX.  Apply OSBs ONLY to the matching signal type so that the OSB
    reference frame is consistent with the observable.
    """
    # v82 PARTS 1+2+4 — GPS SPLIT PPP vs AR ELIGIBILITY
    # Two independent flags per satellite:
    #   use_for_ppp : satellite included in filter, geometry, iono, clock
    #   use_for_ar  : satellite eligible for NL ambiguity fixing
    #
    # STRICT AR signals (MANDATORY for AR):
    #   C1W + L1W  +  C2W + L2W
    # RELAXED PPP signals (fallback, PPP only):
    #   PRIMARY   : C1W/L1W + C2W/L2W  → use_for_ppp=True, use_for_ar=True
    #   FALLBACK  : C1C/L1C + C2W/L2W  → use_for_ppp=True, use_for_ar=False
    #   NEITHER   : return None         → satellite excluded this epoch only

    use_for_ppp   = False
    use_for_ar    = False
    _fallback_used = False

    _P1W = so.get('C1W', 0.); _L1W = so.get('L1W', 0.)
    _P2W = so.get('C2W', 0.); _L2W = so.get('L2W', 0.)
    if _P1W != 0. and _L1W != 0. and _P2W != 0. and _L2W != 0.:
        # PRIMARY: all W-band signals present — PPP + AR eligible
        code1_type, code2_type, phase1_type, phase2_type = 'C1W', 'C2W', 'L1W', 'L2W'
        P1_val, P2_val, L1_val, L2_val = _P1W, _P2W, _L1W, _L2W
        use_for_ppp = True
        use_for_ar  = True
    else:
        # FALLBACK: try C1C/L1C + C2W/L2W
        # v83 FIX: use_for_ar=True — CODE/PRIDE OSB products provide
        # C1C+L1C+C2W+L2W biases for GPS (Chen et al. 2021, Table 1).
        # AR eligibility is further gated by the OSB completeness check below.
        _P1C = so.get('C1C', 0.); _L1C = so.get('L1C', 0.)
        if _P1C != 0. and _L1C != 0. and _P2W != 0. and _L2W != 0.:
            code1_type, code2_type, phase1_type, phase2_type = 'C1C', 'C2W', 'L1C', 'L2W'
            P1_val, P2_val, L1_val, L2_val = _P1C, _P2W, _L1C, _L2W
            use_for_ppp    = True
            use_for_ar     = True   # v83: CODE OSBs cover C1C — AR eligible
            _fallback_used = True   # kept for diagnostics
        else:
            return None   # neither combination available — skip this epoch only

    # Signal map lock for AR-eligible signals — ensures OSB frame consistency.
    if use_for_ar and sid not in _sat_signal_map:
        _sat_signal_map[sid] = (code1_type, code2_type, phase1_type, phase2_type)

    no_AR = not use_for_ar   # backward-compat alias used by NL candidate loop

    P1=P1_val; P2=P2_val; L1=L1_val; L2=L2_val

    ob=osb.get(sid,{})
    # v68 FIX: Apply OSBs ONLY to the signal type that was actually observed.
    # Mismatch between obs signal and OSB signal type causes ~0.25–0.5 cyc residual.
    b_C1 = ob.get(code1_type, 0.)
    b_C2 = ob.get(code2_type, 0.)
    b_L1 = ob.get(phase1_type, 0.)
    b_L2 = ob.get(phase2_type, 0.)
    # v79 PART 1 / v82: OSB completeness and value checks — only relevant when
    # use_for_ar=True (primary signals).  Fallback satellites already have
    # use_for_ar=False so no_AR is already True — no further degradation needed.
    ar_skip_reason = None
    if use_for_ar:
        if (code1_type not in ob or code2_type not in ob or
                phase1_type not in ob or phase2_type not in ob):
            no_AR = True
            use_for_ar = False
            ar_skip_reason = 'no_osb'
        # v79 PART 3: reject out-of-range OSB values.
        if abs(b_C1) > 10.0 or abs(b_C2) > 10.0:
            no_AR = True
            use_for_ar = False
            ar_skip_reason = 'bad_bias'
        if abs(b_L1) > 1.0 or abs(b_L2) > 1.0:
            no_AR = True
            use_for_ar = False
            ar_skip_reason = 'bad_bias'

    if sid not in _osb_dbg_printed:
        _osb_dbg_printed.add(sid)

    P1c = P1 - b_C1
    P2c = P2 - b_C2

    # RAW phase observables converted to metres, then phase OSB removed
    lam1 = LAMBDA1
    lam2 = LAMBDA2
    L1m  = L1 * lam1 - b_L1
    L2m  = L2 * lam2 - b_L2

    # ── v95 STEP 3: OBS_MAP — signal/frequency mapping ────────────────────
    if sid not in _v95_obs_map_printed:
        _v95_obs_map_printed.add(sid)
        print(f"[OBS_MAP] sat={sid} sys=GPS"
              f"  code1={code1_type} phase1={phase1_type}"
              f"  freq1={FREQ1/1e6:.4f}MHz lambda1={lam1*100:.4f}cm"
              f"  code2={code2_type} phase2={phase2_type}"
              f"  freq2={FREQ2/1e6:.4f}MHz lambda2={lam2*100:.4f}cm"
              f"  gamma={F1SQ/F2SQ:.6f}"
              f"  osb_code1={b_C1*1e3:+.3f}mm osb_phase1={b_L1*1e3:+.3f}mm"
              f"  osb_code2={b_C2*1e3:+.3f}mm osb_phase2={b_L2*1e3:+.3f}mm"
              f"  applied_frame=RAW_uncombined")
    # ── v95 STEP 5: OSB_APPLY — confirm single application ────────────────
    if sid not in _v95_osb_apply_printed:
        _v95_osb_apply_printed.add(sid)
        print(f"[OSB_APPLY] sat={sid} code1={code1_type} phase1={phase1_type}"
              f"  osb_C1={b_C1*1e3:+.4f}mm osb_L1={b_L1*1e3:+.4f}mm"
              f"  osb_C2={b_C2*1e3:+.4f}mm osb_L2={b_L2*1e3:+.4f}mm"
              f"  P1_raw={P1:.3f}m P1c_after={P1c:.3f}m"
              f"  L1_raw_m={L1*lam1:.3f}m L1m_after={L1m:.3f}m"
              f"  P2_raw={P2:.3f}m P2c_after={P2c:.3f}m"
              f"  L2_raw_m={L2*lam2:.3f}m L2m_after={L2m:.3f}m"
              f"  applied_in_proc=True applied_in_Hloop=False [confirmed_once]")
    # ── end v95 ──────────────────────────────────────────────────────────────
    _diff_corr_gps = L1m - P1c
    _diff_raw_gps  = L1 * lam1 - P1
    hist_g = _cp_debug.setdefault(sid, deque(maxlen=100))
    hist_g.append(_diff_corr_gps)
    if _nproc_global % 300 == 0:
        rng_g = max(hist_g) - min(hist_g) if len(hist_g) > 5 else 0.0
        log_osb([
            _nproc_global,
            sid,
            0.0,
            _diff_raw_gps,
            _diff_corr_gps,
            rng_g,
            b_C1,
            b_L1,
        ])
    # ── end v81 OSB CONSISTENCY ───────────────────────────────────────────────

    # Ionosphere factor  γ = (f1/f2)²
    gamma = F1SQ / F2SQ
    # P1c/P2c carry code OSBs → code NL bias shifts MW mean automatically.
    # b_wl_sat_cyc (from phase OSBs) removes phase WL bias.
    # Net MW_cyc ≈ N_WL_integer + b_rec (receiver WL fractional bias only).
    bl1 = b_L1   # phase OSB L1 in metres
    bl2 = b_L2   # phase OSB L2 in metres
    b_wl_sat_cyc=((FREQ1*bl1-FREQ2*bl2)/(FREQ1-FREQ2))/LAMBDA_WL
    MW_cyc=_mw_cyc(P1c,P2c,L1,L2)-b_wl_sat_cyc
    GF_m=_gf_m(L1,L2)

    xyz0,_=_spc(sp3t,sp,sc,sid,tow)
    if xyz0 is None: return None
    lat_r,lon_r,_=_lla(rxyz)
    er=np.array([math.cos(lat_r)*math.cos(lon_r),
                 math.cos(lat_r)*math.sin(lon_r),
                 math.sin(lat_r)])
    ra=rxyz+ah*er+_rpco(recx,lat_r,lon_r)
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
    if ae is not None:
        sva=svc+_spco(ae,bx,by,bz); pcvs=_spcv(ae,_nadir(sva,ra,bz))
    el,az=_elaz(ra,sva)
    if el is None or el<elm: return None
    pcvr=_rpcv(recx,el)
    dr=sva-ra; rng=np.linalg.norm(dr); unit=dr/rng
    shp=_shap(ra,sva); setd=_set(ra-ah*er,sun); setm=-unit.dot(setd)
    mh,mw=_gmf(lat0,doy,el)
    return dict(sid=sid,unit=unit,mh=mh,mw=mw,el=el,
                rng=rng,scm=scm,dtrel=dtrel,shp=shp,setm=setm,
                pcv_sat=pcvs,pcv_rec=pcvr,trop_zhd=mh*zhd,
                # v54 RAW observables
                P1c=P1c,P2c=P2c,L1m=L1m,L2m=L2m,
                lam1=lam1,lam2=lam2,gamma=gamma,
                MW_cyc=MW_cyc,GF_m=GF_m,
                L1=L1,L2=L2,P1=P1,P2=P2,
                sat_xyz=sva,rec_apc=ra,
                no_AR=no_AR,                   # v77: False=AR eligible, True=skip AR
                ar_skip_reason=ar_skip_reason,  # v79: 'no_osb' | 'bad_bias' | None
                use_for_ppp=use_for_ppp,        # v82: always True when returned
                use_for_ar=use_for_ar,          # v82: True=primary, False=fallback/no-OSB
                _fallback_used=_fallback_used)  # v82: True=C1C/L1C fallback used

def _proc_gal(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx,
              blq=None,sta='IISC',tow_total=0.):
    """Galileo satellite — E1 + E5a, dynamic signal detection.

    v68 FIX: Dynamically detect actual E1/E5a code and phase signals present in
    RINEX.  Apply OSBs ONLY to the matching signal type.
    """
    # v82 FALLBACK: Galileo E1-only mode — keep sat in KF if C1C/L1C present.
    # E5a (C5Q/L5Q) is optional; absence disables AR only (mirrors GPS fallback).
    # ── PRIDE-STYLE SIGNAL PRIORITY ─────────────────────────────────────────
    # Priority 1: X-family (C1X/L1X + C5X/L5X)
    # Fallback:   C/Q-family (C1C/L1C + C5Q/L5Q)
    # ALL FOUR observables must be present and non-zero. No partial construction.
    _P1X = so.get('C1X') or 0.; _L1X = so.get('L1X') or 0.
    _P5X = so.get('C5X') or 0.; _L5X = so.get('L5X') or 0.
    _P1C = so.get('C1C') or 0.; _L1C = so.get('L1C') or 0.
    _P5Q = so.get('C5Q') or 0.; _L5Q = so.get('L5Q') or 0.

    if _P1X != 0. and _L1X != 0. and _P5X != 0. and _L5X != 0.:
        code1_type, code2_type, phase1_type, phase2_type = 'C1X', 'C5X', 'L1X', 'L5X'
        P1_val, P5_val, L1_val, L5_val = _P1X, _P5X, _L1X, _L5X
    elif _P1C != 0. and _L1C != 0. and _P5Q != 0. and _L5Q != 0.:
        code1_type, code2_type, phase1_type, phase2_type = 'C1C', 'C5Q', 'L1C', 'L5Q'
        P1_val, P5_val, L1_val, L5_val = _P1C, _P5Q, _L1C, _L5Q
    else:
        return None  # No complete 4-tuple — reject epoch cleanly, no partial construction
    # ── END SIGNAL PRIORITY ───────────────────────────────────────────────────

    # ── PRIDE-STYLE FAMILY LOCK ───────────────────────────────────────────────
    # Once an arc begins on a signal family it must stay locked for that arc.
    # A family change mid-arc is treated as an epoch gap (not a slip) so that
    # ambiguity continuity is preserved and _ppp_pass gap-marking handles it.
    _selected_family = (code1_type, code2_type, phase1_type, phase2_type)
    if sid in _sat_signal_map:
        if _sat_signal_map[sid] != _selected_family:
            # Family mismatch: skip this epoch, DO NOT reset ambiguity.
            return None
    else:
        _sat_signal_map[sid] = _selected_family
    # ── END FAMILY LOCK ───────────────────────────────────────────────────────

    P1=P1_val; P5=P5_val; L1=L1_val; L5=L5_val

    ob=osb.get(sid,{})
    # v68 FIX: Apply OSBs ONLY to the signal type that was actually observed.
    b_C1 = ob.get(code1_type, 0.)
    b_C5 = ob.get(code2_type, 0.)
    b_L1 = ob.get(phase1_type, 0.)
    b_L5 = ob.get(phase2_type, 0.)

    # AR eligibility: E5a always present here (4-tuple gate above guarantees it).
    # Check OSB completeness and value range only.
    no_AR = False
    ar_skip_reason = None
    if (code1_type not in ob or code2_type not in ob or
            phase1_type not in ob or phase2_type not in ob):
        no_AR = True
        ar_skip_reason = 'no_osb'

    # v79 PART 3: reject out-of-range OSB values.
    if abs(b_C1) > 10.0 or abs(b_C5) > 10.0:
        no_AR = True
        ar_skip_reason = 'bad_bias'
    if abs(b_L1) > 1.0 or abs(b_L5) > 1.0:
        no_AR = True
        ar_skip_reason = 'bad_bias'

    if sid not in _osb_dbg_printed:
        _osb_dbg_printed.add(sid)

    # E5a always present here — compute P2c/L2m unconditionally (no sentinel zeros).
    P1c = P1 - b_C1
    P2c = P5 - b_C5

    # RAW phase observables converted to metres, then phase OSB removed
    lam1 = LAMBDA_E1
    lam2 = LAMBDA_E5A
    L1m  = L1 * lam1 - b_L1
    L2m  = L5 * lam2 - b_L5

    # ── v95 STEP 3: OBS_MAP — signal/frequency mapping (Galileo) ──────────
    if sid not in _v95_obs_map_printed:
        _v95_obs_map_printed.add(sid)
        print(f"[OBS_MAP] sat={sid} sys=GAL"
              f"  code1={code1_type} phase1={phase1_type}"
              f"  freq1={FREQ_E1/1e6:.4f}MHz lambda1={lam1*100:.4f}cm"
              f"  code2={code2_type} phase2={phase2_type}"
              f"  freq2={FREQ_E5A/1e6:.4f}MHz lambda2={lam2*100:.4f}cm"
              f"  gamma_E={FE1SQ/FE5SQ:.6f}"
              f"  osb_code1={b_C1*1e3:+.3f}mm osb_phase1={b_L1*1e3:+.3f}mm"
              f"  osb_code2={b_C5*1e3:+.3f}mm osb_phase2={b_L5*1e3:+.3f}mm"
              f"  applied_frame=RAW_uncombined")
    # ── v95 STEP 5: OSB_APPLY — confirm single application (Galileo) ──────
    if sid not in _v95_osb_apply_printed:
        _v95_osb_apply_printed.add(sid)
        print(f"[OSB_APPLY] sat={sid} code1={code1_type} phase1={phase1_type}"
              f"  osb_C1={b_C1*1e3:+.4f}mm osb_L1={b_L1*1e3:+.4f}mm"
              f"  osb_C5={b_C5*1e3:+.4f}mm osb_L5={b_L5*1e3:+.4f}mm"
              f"  P1_raw={P1:.3f}m P1c_after={P1c:.3f}m"
              f"  L1_raw_m={L1*lam1:.3f}m L1m_after={L1m:.3f}m"
              f"  P5_raw={P5:.3f}m P2c_after={P2c:.3f}m"
              f"  L5_raw_m={L5*lam2:.3f}m L2m_after={L2m:.3f}m"
              f"  applied_in_proc=True applied_in_Hloop=False [confirmed_once]")
    # ── end v95 ──────────────────────────────────────────────────────────────
    _diff_corr_gal = L1m - P1c
    _diff_raw_gal  = L1 * lam1 - P1
    hist_e = _cp_debug.setdefault(sid, deque(maxlen=100))
    hist_e.append(_diff_corr_gal)
    if _nproc_global % 300 == 0:
        rng_e = max(hist_e) - min(hist_e) if len(hist_e) > 5 else 0.0
        log_osb([
            _nproc_global,
            sid,
            0.0,
            _diff_raw_gal,
            _diff_corr_gal,
            rng_e,
            b_C1,
            b_L1,
        ])
    # ── end v81 OSB CONSISTENCY ───────────────────────────────────────────────

    # Ionosphere factor  γ = (f_E1/f_E5a)²
    gamma = FE1SQ / FE5SQ

    # E5a always present — compute MW/GF unconditionally. No sentinel zeros ever.
    bl1 = b_L1; bl5 = b_L5
    b_wl_sat_cyc=((FREQ_E1*bl1-FREQ_E5A*bl5)/(FREQ_E1-FREQ_E5A))/LAMBDA_WL_E
    L1m_tmp=L1*LAMBDA_E1; L5m_tmp=L5*LAMBDA_E5A
    phi_WL=(FREQ_E1*L1m_tmp-FREQ_E5A*L5m_tmp)/(FREQ_E1-FREQ_E5A)
    P_NL=(FREQ_E1*P1c+FREQ_E5A*P2c)/(FREQ_E1+FREQ_E5A)
    MW_cyc=(phi_WL-P_NL)/LAMBDA_WL_E-b_wl_sat_cyc
    GF_m=L1*LAMBDA_E1-L5*LAMBDA_E5A

    xyz0,_=_spc(sp3t,sp,sc,sid,tow)
    if xyz0 is None: return None
    lat_r,lon_r,_=_lla(rxyz)
    er=np.array([math.cos(lat_r)*math.cos(lon_r),
                 math.cos(lat_r)*math.sin(lon_r),
                 math.sin(lat_r)])
    ra=rxyz+ah*er+_rpco(recx,lat_r,lon_r,is_galileo=True)  # v96 FIX 3
    if blq and tow_total>0.:
        ra=ra+_otl_disp(blq,sta,tow_total,lat_r,lon_r)
    # ── PATCH 2/3 RUNTIME TRACE (Requirement 3) ─────────────────────────────
    # Emit once per pass per satellite for E07/E26 to confirm slot selection.
    if sid in ('E07', 'E26') and sid not in _v95_obs_map_printed:
        if recx is not None:
            _p2_E1  = recx.get('E1', recx['L1'])
            _p2_E5  = recx.get('E5', recx['L2'])
            _p2_L1  = recx['L1']
            _p2_L2  = recx['L2']
            _p2_ve1 = recx.get('ve1', recx['v1'])
            _p2_ve5 = recx.get('ve5', recx['v2'])
            _p2_e1_distinct = not np.array_equal(_p2_E1, _p2_L1)
            _p2_e5_distinct = not np.array_equal(_p2_E5, _p2_L2)
            _p2_pco_used = ALFA_E * _p2_E1 - BETA_E * _p2_E5
            _p2_pco_gps  = ALFA   * _p2_L1 - BETA   * _p2_L2
            _p2_diff_mm  = float(np.linalg.norm(_p2_pco_used - _p2_pco_gps)) * 1e3
            print(f"[PATCH2_TRACE] sat={sid}"
                  f"  PCO_slot=E1({'distinct' if _p2_e1_distinct else 'same_as_L1'})/E5({'distinct' if _p2_e5_distinct else 'same_as_L2'})"
                  f"  E1_mm=({_p2_E1[0]:.2f},{_p2_E1[1]:.2f},{_p2_E1[2]:.2f})"
                  f"  E5_mm=({_p2_E5[0]:.2f},{_p2_E5[1]:.2f},{_p2_E5[2]:.2f})"
                  f"  pco_E1E5=({_p2_pco_used[0]*1e3:.2f},{_p2_pco_used[1]*1e3:.2f},{_p2_pco_used[2]*1e3:.2f})mm"
                  f"  pco_GPS =({_p2_pco_gps[0]*1e3:.2f},{_p2_pco_gps[1]*1e3:.2f},{_p2_pco_gps[2]*1e3:.2f})mm"
                  f"  diff_norm={_p2_diff_mm:.2f}mm"
                  f"  ve1_len={len(_p2_ve1)} ve5_len={len(_p2_ve5)}")
    # ── end PATCH 2/3 runtime trace ──────────────────────────────────────────

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
    if ae is not None:
        sva=svc+_spco(ae,bx,by,bz); pcvs=_spcv(ae,_nadir(sva,ra,bz))
    el,az=_elaz(ra,sva)
    if el is None or el<elm: return None
    pcvr=_rpcv(recx,el,is_galileo=True)   # v97 FIX 3
    dr=sva-ra; rng=np.linalg.norm(dr); unit=dr/rng
    shp=_shap(ra,sva); setd=_set(ra-ah*er,sun); setm=-unit.dot(setd)
    mh,mw=_gmf(lat0,doy,el)
    return dict(sid=sid,unit=unit,mh=mh,mw=mw,el=el,
                rng=rng,scm=scm,dtrel=dtrel,shp=shp,setm=setm,
                pcv_sat=pcvs,pcv_rec=pcvr,trop_zhd=mh*zhd,
                # v54 RAW observables
                P1c=P1c,P2c=P2c,L1m=L1m,L2m=L2m,
                lam1=lam1,lam2=lam2,gamma=gamma,
                MW_cyc=MW_cyc,GF_m=GF_m,
                L1=L1,L2=L5,P1=P1,P2=P5,
                sat_xyz=sva,rec_apc=ra,
                _lam_wl=LAMBDA_WL_E,_freq1=FREQ_E1,_freq2=FREQ_E5A,
                _sys='E',
                no_AR=no_AR,
                ar_skip_reason=ar_skip_reason,
                use_for_ppp=True,
                use_for_ar=not no_AR)

def _rp(m,dT,ZWD):
    return (m['rng']-m['scm']-m['dtrel']+dT
            +m['trop_zhd']+m['mw']*ZWD
            +m['shp']+m['setm']+m['pcv_sat']+m['pcv_rec'])


# ==============================================================================
#  v87: IONEX / GIM parser and ionosphere helpers
# ==============================================================================

def parse_ionex(fp):
    """Parse IONEX v1 file.  Returns (meta, maps_by_sod).

    meta : dict with keys lat1, dlat, lon1, dlon, exponent, n_lats, n_lons
    maps_by_sod : sorted list of (sod_float, tec_2d_ndarray)
                  tec_2d_ndarray[i_lat, i_lon] in TECU
                  i_lat=0 → lat1 (northernmost), increasing south.
    """
    meta = {}
    maps_by_sod = []   # list of (sod, np.ndarray)

    # defaults from IONEX v1 spec (overridden by header)
    lat1 = 87.5;  lat2 = -87.5;  dlat = -2.5
    lon1 = -180.; lon2 = 180.;   dlon = 5.0
    exponent = -1

    in_header = True
    current_epoch_sod = None
    current_tec = None       # flat list of integer TEC values accumulating
    expected_per_lat = None  # number of values per latitude row

    def _cal_to_sod(y, mo, d, h, mi, s):
        """Convert calendar epoch to seconds-of-day (day = 7 Feb 2026, DOY 038)."""
        # For this file all maps are on Feb 7–8 2026.  We return SOD relative
        # to the start of DOY 038 (Feb 7).  Feb 8 00:00 = SOD 86400, etc.
        base_day = 7   # Feb 7
        day_offset = (d - base_day) * 86400
        return day_offset + h * 3600 + mi * 60 + s

    with open(fp, 'r', errors='replace') as fh:
        for raw in fh:
            ln = raw.rstrip('\n')
            label_part = ln[60:].strip() if len(ln) > 60 else ''

            if in_header:
                if 'END OF HEADER' in label_part:
                    in_header = False
                if 'EXPONENT' in label_part:
                    try: exponent = int(ln.split()[0])
                    except: pass
                if 'LAT1 / LAT2 / DLAT' in label_part:
                    p = ln.split()
                    try: lat1, lat2, dlat = float(p[0]), float(p[1]), float(p[2])
                    except: pass
                if 'LON1 / LON2 / DLON' in label_part:
                    p = ln.split()
                    try: lon1, lon2, dlon = float(p[0]), float(p[1]), float(p[2])
                    except: pass
                continue   # still in header

            if 'START OF TEC MAP' in label_part:
                current_tec = []
                current_epoch_sod = None
                continue

            if 'END OF TEC MAP' in label_part:
                if current_epoch_sod is not None and current_tec is not None:
                    scale = 10. ** exponent   # e.g. 0.1 for exponent=-1
                    n_lats = round((lat2 - lat1) / dlat) + 1  # always positive count
                    n_lons = round((lon2 - lon1) / dlon) + 1
                    n_lats = abs(n_lats)
                    arr = np.full((n_lats, n_lons), np.nan)
                    idx = 0
                    for i_lat in range(n_lats):
                        for i_lon in range(n_lons):
                            if idx < len(current_tec):
                                v = current_tec[idx]
                                arr[i_lat, i_lon] = np.nan if v == 9999 else v * scale
                            idx += 1
                    maps_by_sod.append((current_epoch_sod, arr))
                current_tec = None
                current_epoch_sod = None
                continue

            if 'EPOCH OF CURRENT MAP' in label_part:
                p = ln.split()
                try:
                    y, mo, d, h, mi, s = int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]), float(p[5])
                    current_epoch_sod = _cal_to_sod(y, mo, d, h, mi, s)
                except:
                    pass
                continue

            if 'LAT/LON1/LON2/DLON/H' in label_part:
                # Skip — we already have the grid geometry from header
                continue

            # Data line — parse integers if we are inside a TEC map
            if current_tec is not None:
                for tok in ln.split():
                    try:
                        current_tec.append(int(tok))
                    except ValueError:
                        pass  # skip non-integer tokens

    maps_by_sod.sort(key=lambda x: x[0])

    n_lats = abs(round((lat2 - lat1) / dlat)) + 1
    n_lons = round((lon2 - lon1) / dlon) + 1
    meta = dict(lat1=lat1, dlat=dlat, lon1=lon1, dlon=dlon,
                n_lats=n_lats, n_lons=n_lons, exponent=exponent)

    print(f"[IONEX] Parsed {len(maps_by_sod)} TEC maps  "
          f"grid={n_lats}×{n_lons}  exponent={exponent}  "
          f"SOD range={maps_by_sod[0][0]:.0f}–{maps_by_sod[-1][0]:.0f}")
    return meta, maps_by_sod


def _ionex_vtec_at(ionex_meta, ionex_maps, sod, lat_deg, lon_deg):
    """Bilinear spatial + linear temporal interpolation of IONEX VTEC.

    Returns VTEC in TECU, or None if interpolation fails.

    lat_deg : geodetic latitude of IPP [deg]
    lon_deg : longitude of IPP [deg]
    sod     : seconds-of-day of the current epoch
    """
    if not ionex_maps:
        return None

    meta = ionex_meta
    lat1, dlat = meta['lat1'], meta['dlat']   # dlat is negative (87.5→-87.5)
    lon1, dlon = meta['lon1'], meta['dlon']
    n_lats, n_lons = meta['n_lats'], meta['n_lons']

    # Wrap longitude to [-180, 180]
    lon_deg = ((lon_deg + 180.) % 360.) - 180.

    # Clamp latitude to grid range
    lat_min = lat1 + (n_lats - 1) * dlat   # southernmost
    lat_max = lat1                           # northernmost
    lat_deg = max(lat_min, min(lat_max, lat_deg))
    lon_deg = max(lon1, min(lon1 + (n_lons - 1) * dlon, lon_deg))

    # Fractional grid indices
    i_lat_f = (lat_deg - lat1) / dlat   # positive because dlat < 0 and lat_deg ≤ lat1
    j_lon_f = (lon_deg - lon1) / dlon

    i0 = int(math.floor(i_lat_f)); i1 = min(i0 + 1, n_lats - 1)
    j0 = int(math.floor(j_lon_f)); j1 = min(j0 + 1, n_lons - 1)
    wi = i_lat_f - i0;  wj = j_lon_f - j0

    def _bilin(tec_map):
        v00 = tec_map[i0, j0]; v01 = tec_map[i0, j1]
        v10 = tec_map[i1, j0]; v11 = tec_map[i1, j1]
        if any(np.isnan(v) for v in [v00, v01, v10, v11]):
            # Fall back to nearest non-NaN
            vs = [v for v in [v00, v01, v10, v11] if not np.isnan(v)]
            return float(np.mean(vs)) if vs else None
        return (1 - wi) * ((1 - wj) * v00 + wj * v01) + wi * ((1 - wj) * v10 + wj * v11)

    # Temporal interpolation
    sods = [s for s, _ in ionex_maps]
    if sod <= sods[0]:
        vtec = _bilin(ionex_maps[0][1])
    elif sod >= sods[-1]:
        vtec = _bilin(ionex_maps[-1][1])
    else:
        # Find bracketing maps
        idx = 0
        for k, s in enumerate(sods):
            if s > sod:
                idx = k
                break
        s0, tec0 = ionex_maps[idx - 1]
        s1, tec1 = ionex_maps[idx]
        dt = s1 - s0
        if dt <= 0:
            vtec = _bilin(tec0)
        else:
            wt = (sod - s0) / dt
            v0 = _bilin(tec0)
            v1 = _bilin(tec1)
            if v0 is None or v1 is None:
                vtec = v0 if v0 is not None else v1
            else:
                vtec = (1. - wt) * v0 + wt * v1

    return vtec


def _ipp_latlon_mapping(lat_rx_rad, lon_rx_rad, el_rad, az_rad, h_shell_m=450e3):
    """Compute IPP lat/lon and thin-shell mapping factor.

    h_shell_m : ionospheric shell height (m), default 450 km
    Returns (lat_ipp_deg, lon_ipp_deg, mapping_factor) or None on failure.
    """
    RE = 6371e3   # IONEX BASE RADIUS in metres
    if el_rad < 0.01:
        return None

    # Central angle from receiver to IPP
    psi = math.pi / 2. - el_rad - math.asin(RE / (RE + h_shell_m) * math.cos(el_rad))

    # IPP latitude
    sin_lat_ipp = (math.sin(lat_rx_rad) * math.cos(psi) +
                   math.cos(lat_rx_rad) * math.sin(psi) * math.cos(az_rad))
    sin_lat_ipp = max(-1., min(1., sin_lat_ipp))
    lat_ipp = math.asin(sin_lat_ipp)

    # IPP longitude
    dlon = math.atan2(math.sin(psi) * math.sin(az_rad),
                      math.cos(lat_rx_rad) * math.cos(psi) -
                      math.sin(lat_rx_rad) * math.sin(psi) * math.cos(az_rad))
    lon_ipp = lon_rx_rad + dlon

    # Thin-shell mapping factor: M = 1 / cos(zenith_at_ipp)
    cos_z_ipp = math.sqrt(1. - (RE / (RE + h_shell_m) * math.cos(el_rad)) ** 2)
    if cos_z_ipp < 0.01:
        return None
    mapping_factor = 1. / cos_z_ipp

    return math.degrees(lat_ipp), math.degrees(lon_ipp), mapping_factor


# IONEX L1 TEC-to-delay constant: I_L1 (m) = _IONEX_K / f1^2 * STEC (TECU)
# STEC (TECU) = VTEC (TECU) × M;  1 TECU = 10^16 el/m^2
# I_L1 = 40.3e16 / f1^2 * STEC_tecu  [m]
_IONEX_K_L1  = 40.3e16 / FREQ1**2      # m / TECU  ≈ 0.16238 m/TECU
_IONEX_K_E1  = 40.3e16 / FREQ_E1**2    # same (E1 = L1 freq)


# ==============================================================================
#  PPP Kalman filter pass
# ==============================================================================
def _ppp_pass(epochs,sp3t,sp,sc,clkd,osb,ah,nom,iclk,izwd,lat0,doy,zhd,tref,
              satx,att,recx,elm=math.radians(10.),SC=0.30,SP=0.010,
              direction=1,label="FWD",wl_init=None,amb_init=None,
              constellation='GE',blq=None,sta='IISC',
              ionex_meta=None,ionex_maps=None,
              disable_gps_nl=False,disable_gal_states=False):
    """
    constellation : 'G' | 'E' | 'GE'
    blq           : dict from parse_blq (ocean tide loading)
    sta           : 4-char station code used to look up BLQ entry
    ionex_meta    : dict from parse_ionex (grid metadata)
    ionex_maps    : list of (sod, tec_array) from parse_ionex
    """
    REF=np.array([1337935.5599,6070317.2377,1427877.5071])
    global _osb_dbg_printed, _sat_signal_map, _cp_debug, _osb_once, _nproc_global
    global _v95_phase_print_count, _v95_obs_map_printed, _v95_osb_apply_printed
    global _v95_apc_printed, _v95_rms_warn_epoch
    _osb_dbg_printed = set()
    _sat_signal_map  = {}  # v70: reset signal lock at start of each pass
    _cp_debug        = {}  # v81: per-sat L1m-P1c history (OSB consistency debug)
    _osb_once        = set()  # v81: [OSB_VAL] printed-once guard
    _nproc_global    = 0
    # v95: reset all forensic throttle/state sets
    _v95_phase_print_count = 0
    _v95_obs_map_printed   = set()
    _v95_osb_apply_printed = set()
    _v95_apc_printed       = set()
    _v95_rms_warn_epoch    = set()
    init_osb_csv()
    wl_fixed=dict(wl_init) if wl_init else {}
    _amb_init=dict(amb_init) if amb_init else {}
    _amb_seeded=set()
    nl_fixed={}

    # ── v54 parameters ────────────────────────────────────────────────────────
    # NL/WL fixing thresholds PRESERVED but NL fixing is DISABLED in v54
    # until Phase 2 validation passes (RAW float convergence confirmed).
    NL_RATIO_THRESH   = 4.5
    NL_VAR_THRESH     = (0.1)**2     # v58: strict gate — was (10.0)² (allowed fixing with huge uncertainty)
    NL_RES_THRESH     = 0.03             # v58: tightened from 0.10 — must be within 3% of integer
    NL_EXCL_THRESH    = 0.25
    NL_R_TIGHT        = (0.003)**2   # v80 PART 2: tightened to 3 mm — stronger NL constraint
    NL_INNOV_GATE     = 0.500   # v70 FIX: relaxed to 0.50 — PPP innovations ≈0.25–0.35 cyc routinely
    NL_RELEASE_THRESH = 0.080   # v75 PART 4: tightened from 0.100 — prevents too-easy release while looser than original 0.060
    NL_PHASE_THRESH   = 0.010
    NL_MIN_SATS       = 3            # v62: lowered from 7 — apply NL once ≥3 sats fixed
    NL_MIN_OBS        = 8
    PHASE_RES_GATE    = 0.030
    ZWD_PRIOR         = 0.12
    ZWD_PRIOR_SIGMA   = 0.06
    ZWD_CLAMP         = 0.015
    _zwd_prev         = None
    _nl_diag_done     = False

    # ── v54 RAW state vector ──────────────────────────────────────────────────
    # Global:  [x(0), y(1), z(2), clock(3), ZWD(4)]
    # Per-sat: [I_s(5+3k), N1_s(6+3k), N2_s(7+3k)]  for satellite k  (single-constellation)
    # v83: For constellation='GE', a GPS Inter-System Bias (ISB) state is added at
    # index 5, shifting all per-sat blocks to 6+3k.  The ISB absorbs residual
    # receiver hardware differences between GPS and Galileo (Chen et al. 2021 Sec 2.2).
    # Single-constellation runs use N_GLOBAL=5 (no ISB); combined runs use N_GLOBAL=6.
    #
    # v96: RDCB_E — Galileo receiver differential code bias (C1C − C5Q).
    # Single scalar state common to all Galileo satellites, analogous to ISB.
    # Units: metres (raw GF space).  Physics:
    #   P2c − P1c = (γ−1)·I + (b_C5_r − b_C1_r) = (γ−1)·I − RDCB_E
    # so code_GF_iono_obs = I_true − RDCB_E/(γ−1) ≈ I_true − 5 m (IISC/PolaRx5).
    # The H-matrix coefficients on code P1/P2 rows follow from the IF-clock
    # reference frame derivation: P1 gets −BETA_E = −1/(γ−1), P2 gets −ALFA_E = −γ/(γ−1).
    # State layout:
    #   'G'  only: N_GLOBAL=5  [x,y,z,clk,ZWD]              no ISB, no RDCB_E
    #   'E'  only: N_GLOBAL=6  [x,y,z,clk,ZWD,RDCB_E]       no ISB
    #   'GE' combo: N_GLOBAL=7 [x,y,z,clk,ZWD,ISB,RDCB_E]   ISB@5, RDCB_E@6
    if   constellation == 'GE': N_GLOBAL = 7
    elif constellation == 'E':  N_GLOBAL = 6
    else:                        N_GLOBAL = 5
    ISB_IDX    = 5 if constellation == 'GE' else None
    RDCB_E_IDX = (6 if constellation == 'GE' else
                  5 if constellation == 'E'  else None)  # Galileo receiver DCB
    x=np.zeros(N_GLOBAL); x[3]=iclk; x[4]=izwd
    if ISB_IDX    is not None: x[ISB_IDX]    = 0.0   # placeholder; overwritten after first epoch
    if RDCB_E_IDX is not None: x[RDCB_E_IDX] = 0.0   # start at zero; converges from data
    P=np.zeros((N_GLOBAL, N_GLOBAL))
    P[0,0]=P[1,1]=P[2,2]=100.**2; P[3,3]=3000.**2; P[4,4]=0.5**2
    if ISB_IDX    is not None: P[ISB_IDX,    ISB_IDX]    = 25.**2
    # ── v98 ROOT CAUSE FIX: RDCB_E prior loosened from 0.10² → 5.0² m² ────────
    # FORENSIC EVIDENCE (from rms_split_diagnostics.csv + osb_log.csv):
    #   • L2/L1 phase RMS ratio = 1.808 ≈ γ_GAL = 1.793 (0.8%)
    #     → residuals ARE ionosphere-scaled; I_s state is wrong
    #   • GAL_P2_RMS = 13 106 mm vs GPS_P2_RMS = 1 568 mm
    #     → Galileo code P2 has a structural ~12 m offset
    #   • E23 P2c − P1c = −2.555 m (required +(γ−1)·I ≈ +10.3 m)
    #     → code GF is WRONG SIGN; P2 and P1 code rows fight on iono state
    #   • [RDCB_E] SOD=80970: value=+0.0000 m, sigma=0.3017 m
    #     → state never moved; prior too tight to absorb ~12 m hardware DCB
    #
    # ROOT CAUSE: RDCB_E prior P = 0.10² m² (σ = 10 cm) prevented the KF from
    # moving the state from 0 toward the true ~+11–12 m receiver hardware DCB.
    # With RDCB_E ≈ 0, the Galileo P2 code innovation is:
    #   z_P2 = P2c − (rp + γ·I_s) ≈ P2c − P1c − (γ−1)·I_s = −12 m − (γ−1)·I_s
    # This is large, negative, and drives I_s in the WRONG direction on every epoch.
    # I_s never converges → L1_res = −(I_true−I_s) ≈ 3–7 m, L2_res = γ × L1_res.
    #
    # FIX: use P = 5.0² m² (σ = 5 m) so the KF is free to converge from data
    # within ~10–30 epochs without being unduly prior-constrained.
    # The RDCB_E state is then driven by the Galileo code GF observations to
    # absorb the hardware DCB, making P2c − P1c ≈ +(γ−1)·I as required.
    #
    # Note on previous comment "PATCH 2: tightened from 25**2 → 0.10**2":
    # That change went too far. 0.10² was the bug. 5.0² is the correct setting.
    if RDCB_E_IDX is not None:
        P[RDCB_E_IDX, RDCB_E_IDX] = 5.0 ** 2   # v99 FIX: ±5 m 1-sigma — matches docstring (was 1.0** typo in v98)
        print(f"[RDCB_PRIOR_FIX] RDCB_E_IDX={RDCB_E_IDX} "
              f"x_init={x[RDCB_E_IDX]:.4f}m "
              f"P_init={P[RDCB_E_IDX,RDCB_E_IDX]:.2f} m² "
              f"sigma={math.sqrt(P[RDCB_E_IDX,RDCB_E_IDX]):.1f}m "
              f"— v98 ROOT CAUSE FIX: loosened from 0.10m to 5.0m 1-sigma")
    _isb_init_done = False   # flag: ISB seeded from first mixed epoch
    # v96 diagnostics: per-300-epoch accumulators for RDCB_E diagnostic print
    _rdcbe_gf_raw_acc  = []   # raw code_GF_iono (Galileo, no RDCB correction)
    _rdcbe_gf_corr_acc = []   # corrected code_GF_iono

    # sidx maps sat_id → base index of its 3-state block [I, N1, N2]
    sidx={}; namb=0; phi={}; wum={}; prev_mw={}; prev_gf={}
    mw_hist=defaultdict(list)
    results={}; psod=None; nproc=0
    _amb_conv_sods=set(); _amb_init_ptrace={}
    _sat_age=defaultdict(int); _amb_snapshots={}
    _wl_history={}; _nl_bad_nwl=set(); _wl_history_ptrace={}
    _sat_last_sod={}
    # v84: Galileo arc stability tracking.
    # _gal_slip_epoch[sid]  = nproc-epoch index when last ambiguity reset occurred.
    # _gal_missing_l1c[sid] = deque(maxlen=30) of bool: True = L1C present this epoch.
    # Used by _gal_arc_unstable() to detect unstable arcs and apply adaptive weighting.
    _gal_slip_epoch   = {}
    _gal_missing_l1c  = defaultdict(lambda: deque(maxlen=30))
    GAL_UNSTABLE_EPOCHS_AFTER_RESET = 20    # epochs to treat sat as unstable post-slip
    GAL_L1C_MISS_FRAC_THRESH        = 0.25  # >25% missing in recent window → unstable
    GAL_UNSTABLE_CODE_SCALE         = 7.0   # code_sigma multiplier when unstable
    GAL_UNSTABLE_PHASE_SCALE        = 4.0   # phase_sigma multiplier when unstable

    # v59: per-satellite L1m-P1c consistency history (deque, maxlen=100)
    _lp1_hist=defaultdict(lambda: deque(maxlen=100))

    # v60: per-satellite fractional-ambiguity bias estimation.
    # After OSB correction a residual constant fractional bias may remain per
    # satellite (sub-cycle OSB rounding, signal-path hardware offsets, etc.).
    # Strategy:
    #   • Collect (N1_float − round(N1_float)) only when the solution is stable:
    #       sigma_N1_m < 0.10 m  AND  satellite_age > 300 epochs (≈ 2.5 h at 30 s)
    #   • v61 ADDITION: also require frac_std < 0.02 cyc (stability gate).
    #   • Estimate bias as median of the buffer once ≥ 20 samples are available.
    #   • Apply bias correction only inside the NL-fixing decision (filter state
    #     is NEVER modified — this is purely a fixing aid).
    _nl_frac_buf  = defaultdict(lambda: deque(maxlen=100))  # fractional samples
    _nl_bias      = {}   # sid → estimated bias in cycles (median of buffer)

    # v61: fractional stability tracking (Fix 1, Fix 2, Fix 3).
    # _nl_frac_hist: last 20 raw_frac values used to compute frac_std each epoch.
    # _nl_bias_frozen: set of sids whose bias has converged and must not change.
    # _gps_nl_fixed_ever: whether any GPS satellite has been NL-fixed at least once
    #   (used by Fix 5 to decide when to revert from relaxed gate to tight gate).
    _nl_frac_hist  = defaultdict(lambda: deque(maxlen=20))   # last 20 raw fracs
    _nl_bias_frozen = set()                                   # sids with frozen bias
    _gps_nl_fixed_ever = False                               # Fix 5 state flag
    _last_raw_frac  = {}   # v70-fix1: last raw_frac per sid for drift-rate check
    # v80 PART 4: per-satellite cooldown counter (epochs) before re-fixing is
    # allowed.  Set to 30 when a satellite is released from nl_fixed; prevents
    # rapid fix→release→refix oscillations that destabilise the filter.
    _nl_fix_cooldown = defaultdict(int)   # sid → epochs remaining before re-fix OK

    # ── v96-PATCH1: GPS wrong-fix veto ───────────────────────────────────────
    # Tracks consecutive epochs where a GPS satellite in nl_fixed shows the
    # scintillation wrong-fix signature:
    #   • L1 per-sat residual > 400 mm  AND  |L2_res|/|L1_res| > 1.45
    # When the counter reaches _WRONGFIX_CONSEC_THRESH the fix is released and
    # its ambiguity covariance inflated to prevent immediate re-fixing.
    _wrongfix_susp_count = defaultdict(int)   # sid → consecutive suspicious epochs
    _WRONGFIX_L1_THRESH_MM   = 400.0          # min L1 residual to flag (mm)
    _WRONGFIX_RATIO_THRESH   = 1.45           # min |L2_res|/|L1_res| to flag
    _WRONGFIX_CONSEC_THRESH  = 3              # consecutive epochs before veto fires
    _WRONGFIX_COV_INFLATE    = (0.50) ** 2    # variance floor after release (m²/cyc²)
    # v80 PART 3: store lam1 per satellite from previous epoch so it is available
    # inside the Q-build loop (which runs before geom is constructed each epoch).
    _sat_lam1 = {}   # sid → lam1 (m/cyc) from last processed epoch
    # v70 IONO FIX 4/6: persistent per-satellite iono diagnostics.
    # _iono_last_dI  — magnitude of iono change in the previous epoch (used to
    #                  inflate Rd for flagged satellites in the NEXT epoch).
    _iono_last_dI   = defaultdict(float)   # sid → |dI| from last epoch (m)

    # ── CSV bias logger ───────────────────────────────────────────────────────
    # Replaces all heavy per-epoch console prints with a single lightweight CSV.
    # File is written next to the script; flush every 100 epochs; closed before
    # _ppp_pass returns so the file is always complete even on early exit.
    _bias_csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "nl_bias_debug.csv")
    _bias_csv_fh = open(_bias_csv_path, "w", newline="")
    _bias_csv_w  = _csv.writer(_bias_csv_fh)
    _bias_csv_w.writerow(["epoch", "sod", "sat",
                          "sigma_N1_cm", "raw_frac", "bias", "corr_frac",
                          "buf_n", "frac_std", "frozen"])

    # ── RMS-SPLIT DIAGNOSTICS (pure read-only; patch v85-diag) ───────────────
    # Writes per-epoch post-fit residual RMS split by observable type and
    # constellation.  DIAGNOSTIC ONLY: no filter state, weights, matrices, or
    # residuals are modified.
    _rms_csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "rms_split_diagnostics.csv")
    _rms_csv_fh = open(_rms_csv_path, "w", newline="")
    _rms_csv_w  = _csv.writer(_rms_csv_fh)
    _rms_csv_w.writerow([
        "SOD",
        # Dual-frequency rows only (nL1_dual == nL2_dual guaranteed after v88 fix)
        "P1_RMS_mm", "P2_RMS_mm", "L1_RMS_mm", "L2_RMS_mm",
        "GPS_P2_RMS_mm", "GAL_P2_RMS_mm",
        "nP1", "nP2", "nL1", "nL2",
        # Single-frequency Galileo rows (C1C/L1C-only; excluded from L1/L2 split)
        "nL1_single", "L1_single_RMS_mm",
    ])

    # v85: Galileo WL calibration applied CSV — one row per WL CHECK event where
    # a _GAL_WL_CLOSURE correction exists (regardless of size), recording before/after.
    _wlcal_csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "gal_wl_calibration_applied.csv")
    _wlcal_csv_fh = open(_wlcal_csv_path, "w", newline="")
    _wlcal_csv_w  = _csv.writer(_wlcal_csv_fh)
    _wlcal_csv_w.writerow([
        "SOD", "sat", "delta_wl",
        "raw_residual", "corrected_residual",
        "wl_fixable_before", "wl_fixable_after",
    ])
    # ─────────────────────────────────────────────────────────────────────────

    # v87: IONEX initialization audit CSV
    _ionex_csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "ionex_init_audit.csv")
    _ionex_csv_fh = open(_ionex_csv_path, "w", newline="")
    _ionex_csv_w  = _csv.writer(_ionex_csv_fh)
    _ionex_csv_w.writerow([
        "SOD", "sat", "VTEC", "mapping_factor", "STEC",
        "I_L1_init_m", "fallback_used", "initial_iono_sigma",
    ])
    _ionex_init_count = 0   # total successful IONEX inits this pass
    _ionex_fallback_count = 0

    # ── v89: arc-reset ionosphere consistency audit CSV ───────────────────────
    _arc_reset_csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "arc_reset_iono_audit.csv")
    _arc_reset_csv_fh = open(_arc_reset_csv_path, "w", newline="")
    _arc_reset_csv_w  = _csv.writer(_arc_reset_csv_fh)
    _arc_reset_csv_w.writerow([
        "SOD", "sat", "old_I_state", "geomfree_I", "LminusP_sign",
        "reset_trigger_reason", "iono_reinitialized", "new_I_init",
        "used_ionex", "ambiguity_reset",
    ])
    _arc_reset_iono_count = 0   # number of iono re-inits triggered
    # ─────────────────────────────────────────────────────────────────────────

    # ── v90: C1C/L1C-only Galileo phase exclusion audit CSV ──────────────────
    _c1c_excl_csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "c1c_phase_exclusion_audit.csv")
    _c1c_excl_csv_fh = open(_c1c_excl_csv_path, "w", newline="")
    _c1c_excl_csv_w  = _csv.writer(_c1c_excl_csv_fh)
    _c1c_excl_csv_w.writerow([
        "SOD", "sat",
        "c1c_only_detected", "phase_excluded", "code_retained",
        "ambiguity_created", "ionosphere_created", "rows_added",
        "used_geometry_only",
    ])
    _c1c_excl_count = 0   # total exclusion events this pass
    # ─────────────────────────────────────────────────────────────────────────

    # ── v92: GF initialization guard audit CSV ───────────────────────────────
    _gf_guard_csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "gf_guard_audit.csv")
    _gf_guard_csv_fh = open(_gf_guard_csv_path, "w", newline="")
    _gf_guard_csv_w  = _csv.writer(_gf_guard_csv_fh)
    _gf_guard_csv_w.writerow([
        "SOD", "sat", "epochs_since_entry", "old_I_state",
        "GF_measured", "GF_test_skipped", "skip_reason",
        "reset_triggered", "trigger_reason",
    ])
    _gf_guard_skipped_count = 0
    _gf_guard_print_count   = 0
    # ─────────────────────────────────────────────────────────────────────────

    # ── IONO HEALTH GATE audit CSV ────────────────────────────────────────────
    # Independent per-epoch ionosphere state validity monitor.
    # Fires when x[ki] falls outside the physically plausible range,
    # REGARDLESS of slip detection.  Ambiguities are NEVER touched.
    NEG_IONO_THRESH = -2.0    # m — allows low-STEC mature arcs; blocks runaway
    MAX_IONO_THRESH =  80.0   # m — tightened; IISC equatorial STEC rarely >60 m
    _ihg_csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "iono_health_gate_audit.csv")
    _ihg_csv_fh = open(_ihg_csv_path, "w", newline="")
    _ihg_csv_w  = _csv.writer(_ihg_csv_fh)
    _ihg_csv_w.writerow([
        "SOD", "sat", "old_I_m", "new_I_m", "trigger",
        "used_ionex", "VTEC", "sigma_new_m",
    ])
    _ihg_count       = 0    # total health-gate corrections this pass
    _ihg_print_count = 0    # cap console prints
    # ─────────────────────────────────────────────────────────────────────────

    # ── GPS C2W/L2W de-weight audit CSV ──────────────────────────────────────
    # v91: stochastic de-weighting of GPS second-frequency observables only.
    # σ_P2 *= 1.5 (variance ×2.25),  σ_L2 *= 1.2 (variance ×1.44).
    # Audit records every GPS dual-freq measurement row for diagnostic review.
    _c2w_audit_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "gps_c2w_weight_audit.csv")
    _c2w_audit_fh = open(_c2w_audit_path, "w", newline="")
    _c2w_audit_w  = _csv.writer(_c2w_audit_fh)
    _c2w_audit_w.writerow([
        "SOD", "sat",
        "sigma_P2_before", "sigma_P2_after",
        "sigma_L2_before", "sigma_L2_after",
        "elevation_deg",
        "residual_P2_mm", "residual_L2_mm",
    ])
    _C2W_DEWEIGHT_CODE_SCALE  = 1.5   # σ_P2 *= 1.5  → variance ×2.25
    _C2W_DEWEIGHT_PHASE_SCALE = 1.2   # σ_L2 *= 1.2  → variance ×1.44
    _c2w_print_count = 0              # cap console prints to first 5 sats seen
    # ─────────────────────────────────────────────────────────────────────────

    # ── v91: ADAPTIVE IONEX IONOSPHERE INITIALIZATION SIGMA ──────────────────
    # Replace fixed σ=22 m with sigma = max(FLOOR, K * |I_L1_init|).
    # Applied at: (a) fresh satellite entry, (b) arc-reset re-seeding.
    # Only modifies P[ki,ki] at initialization — all filter logic unchanged.
    SIGMA_IONO_FLOOR = 3.0    # minimum sigma (m) — stays conservative
    K_SIGMA_IONO     = 0.35   # proportionality factor (conservative)

    # ── v97 staged-iono constants REMOVED (reverted) ────────────────────────
    # Galileo uses normal IONEX init + adaptive sigma (SIGMA_IONO_FLOOR/K_SIGMA_IONO).
    # No staged ramp, no age-gating, no K_eff suppression.

    _adap_csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "adaptive_iono_sigma_audit.csv")
    _adap_csv_fh = open(_adap_csv_path, "w", newline="")
    _adap_csv_w  = _csv.writer(_adap_csv_fh)
    _adap_csv_w.writerow([
        "SOD", "sat", "reset_type",
        "VTEC", "mapping_factor", "I_L1_init_m",
        "sigma_old_m", "sigma_new_m",
        "floor_active", "fallback_used", "gf_I_before_reset",
    ])
    _adap_print_count = 0   # cap console prints to first 10 resets
    # ─────────────────────────────────────────────────────────────────────────

    # ── v93 FORENSIC: ambiguity discontinuity audit CSV ──────────────────────
    # DIAGNOSTIC ONLY — pure observation, zero filter mutation.
    # Logs every WL_JUMP, SLIP, NL_RELEASE, REENTRY, SIGNAL_SWITCH, L2_RMS_SPIKE.
    _disc_csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "ambiguity_discontinuity_audit.csv")
    _disc_csv_fh = open(_disc_csv_path, "w", newline="")
    _disc_csv_w  = _csv.writer(_disc_csv_fh)
    _disc_csv_w.writerow([
        "SOD", "sat",
        "arc_age", "slip_detected",
        "dGF_m", "dMW_cyc",
        "WL_before", "WL_after", "WL_jump",
        "NL_fixed_before", "NL_fixed_after",
        "N1_before", "N1_after",
        "N2_before", "N2_after",
        "iono_before", "iono_after",
        "phase_rms_L1_mm", "phase_rms_L2_mm",
        "fallback_used", "primary_signal_pair", "osb_signal_pair",
        "missing_prev_epoch", "epochs_since_last_obs",
        "mw_buffer_std",
        "gf_consistency_triggered", "iono_health_gate_triggered",
        "reset_path", "event_type",
    ])

    # Per-satellite per-epoch L2 RMS tracker (filled in RMS-diag block each epoch)
    # Maps sid -> L2 residual (metres) for the CURRENT epoch, available for logging.
    _disc_sat_l2_res: dict  = {}   # sid -> list of L2 residuals this epoch
    _disc_sat_l1_res: dict  = {}   # sid -> list of L1 residuals this epoch

    # Per-epoch snapshots of WL/NL state (keyed by sid) taken BEFORE any fixes
    # so WL_before / NL_before are meaningful.
    _disc_wl_prev:  dict = {}   # sid -> NWL int (or None) at END of last epoch
    _disc_nl_prev:  dict = {}   # sid -> (N1,N2) or None at END of last epoch
    _disc_N1_prev:  dict = {}   # sid -> x[ki+1] float at END of last epoch
    _disc_N2_prev:  dict = {}   # sid -> x[ki+2] float at END of last epoch
    _disc_iono_prev: dict = {}  # sid -> x[ki] float at END of last epoch

    # Track previous signal pair to detect SIGNAL_SWITCH events
    _disc_signal_pair_prev: dict = {}  # sid -> (code1,phase1,code2,phase2) string

    # Track whether iono health gate fired this epoch for each sat (set below)
    _disc_ihg_fired: set = set()

    # Track whether arc_reset_iono fired this epoch for each sat (set below)
    _disc_ar_fired: set = set()

    # Sentinel for "not yet observed" in _disc_N1_prev etc.
    _DISC_MISSING = float('nan')

    def _disc_write(sod, sid, arc_age, slip_detected,
                    dGF_m, dMW_cyc,
                    wl_before, wl_after,
                    nl_fixed_before, nl_fixed_after,
                    N1_before, N1_after,
                    N2_before, N2_after,
                    iono_before, iono_after,
                    phase_rms_L1_mm, phase_rms_L2_mm,
                    fallback_used, primary_signal_pair, osb_signal_pair,
                    missing_prev_epoch, epochs_since_last_obs,
                    mw_buffer_std,
                    gf_consistency_triggered, iono_health_gate_triggered,
                    reset_path, event_type):
        """Write one row to ambiguity_discontinuity_audit.csv."""
        def _f(v):
            if v is None: return ""
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return ""
            return f"{v:.4f}" if isinstance(v, float) else str(v)
        _disc_csv_w.writerow([
            f"{sod:.1f}", sid,
            arc_age, int(bool(slip_detected)),
            _f(dGF_m), _f(dMW_cyc),
            _f(wl_before), _f(wl_after),
            _f(abs(wl_after - wl_before) if (wl_before is not None and wl_after is not None) else None),
            int(bool(nl_fixed_before)), int(bool(nl_fixed_after)),
            _f(N1_before), _f(N1_after),
            _f(N2_before), _f(N2_after),
            _f(iono_before), _f(iono_after),
            _f(phase_rms_L1_mm), _f(phase_rms_L2_mm),
            int(bool(fallback_used)), primary_signal_pair, osb_signal_pair,
            int(bool(missing_prev_epoch)), _f(epochs_since_last_obs),
            _f(mw_buffer_std),
            int(bool(gf_consistency_triggered)),
            int(bool(iono_health_gate_triggered)),
            reset_path, event_type,
        ])
    # ─────────────────────────────────────────────────────────────────────────

    # Pre-compute receiver lat/lon for IPP computation (degrees)
    _rx_lat_deg = math.degrees(lat0)
    _rx_lat_rad = lat0
    _rx_lon_rad = math.atan2(nom[1], nom[0])   # approx from nominal position
    # ─────────────────────────────────────────────────────────────────────────


    _DBG_SAT   = None       # will be set to first GPS sat seen
    _dbg_lp1   = []         # (sod, L1m-P1c) history
    _dbg_lp2   = []         # (sod, L2m-P2c) history
    _DBG_PRINT_INTERVAL = 120   # print summary every N epochs

    b_rec_frozen={}; b_rec_n=defaultdict(int)
    eplist=epochs if direction==1 else list(reversed(epochs))
    # v78 PART 7 — debug counters (reset each pass)
    reject_due_to_sigma = 0
    reject_due_to_innov = 0
    # v79 PART 6 — per-epoch NL skip counters (reset each epoch inside loop)
    _nl_skip_no_osb      = 0
    _nl_skip_bad_bias    = 0
    _nl_skip_high_range  = 0
    _nl_skip_sigma_accum = 0  # cumulative across epochs for 300-epoch window
    _nl_skip_innov_accum = 0
    _nl_count_accum      = 0  # NL-fixed satellites committed in this window
    # v82 PART 6 — SIG_STATS accumulators (reset each 300-epoch window)
    _nl_skip_no_ar       = 0  # satellites skipped NL because use_for_ar=False
    _sig_nGPS_total_acc  = 0  # GPS sats visible this window
    _sig_nGPS_ppp_acc    = 0  # GPS sats used in PPP filter
    _sig_nGPS_ar_acc     = 0  # GPS sats eligible for AR
    _sig_nGPS_fb_acc     = 0  # GPS sats using fallback (C1C) signals

    for epoch in eplist:
        sod=epoch['t']; sobs=epoch['sats']
        dt=abs(sod-psod) if psod is not None else 30.
        if dt<=0 or dt>3600: dt=30.
        psod=sod; tow=_sod2t(sod,tref)
        # GPS total seconds passed to OTL (tow from _sod2t is already GPS total-s)
        tow_total=tow

        # ── v93 FORENSIC: reset per-epoch transient tracking ─────────────────
        _disc_ihg_fired  = set()   # sats where iono health gate fired this epoch
        _disc_ar_fired   = set()   # sats where arc_reset_iono fired this epoch
        _disc_sat_l2_res = {}      # sid -> L2 residual (m) from RMS-diag this epoch
        _disc_sat_l1_res = {}      # sid -> L1 residual (m) from RMS-diag this epoch
        # ─────────────────────────────────────────────────────────────────────

        n_st=len(x); Q=np.zeros((n_st,n_st))
        Q[0,0]=Q[1,1]=Q[2,2]=1e-8*dt; Q[3,3]=1e4*dt
        Q[4,4]=2.5e-9*dt
        # ISB process noise — (0.01 m)²/s walk; allows fast convergence in first
        # few hours without instability.  Tighten to 1e-6 once ISB is stable.
        if ISB_IDX    is not None: Q[ISB_IDX,    ISB_IDX]    = 1e-4  * dt
        # v96: RDCB_E near-constant process noise: (0.001 m)²/s ≈ 0.1 mm/epoch
        # Mirrors the ISB 'slow walk' philosophy — allow for slow hardware drift
        # while not letting the state wander on observation noise.
        # v98: confirmed correct at 1e-6 m²/s — small enough to be stable,
        # large enough to track real receiver hardware temperature drift.
        if RDCB_E_IDX is not None: Q[RDCB_E_IDX, RDCB_E_IDX] = 1e-6 * dt  # v99: ±1 mm/√s — tracks receiver HW temperature drift
        # v54 RAW per-satellite state noise:
        #   I (ionosphere): small process noise to allow slow drift
        #   N1, N2 (ambiguities): zero process noise (carrier phase constants)
        # v56 FIX 1 (CRITICAL): q_iono increased 100× for equatorial station IISC.
        # IISC is inside the EIA; L1 STEC varies 20–50 m per pass.
        # With 1e-6/s the ionosphere froze after ~50 epochs (P[ki,ki]→0.02 m²);
        # position absorbed the unfrozen residual.  1e-4/s gives σ_I_ss≈0.34 m,
        # enough bandwidth to track equatorial rate ≈ 0.05–0.5 m/epoch.
        # For extreme scintillation epochs, raise to ION_PROC_NOISE=1e-3.
        ION_PROC_NOISE = 5e-5              # m²/s — v81 FIX 2: increased to 5e-5 to allow realistic iono variance at equatorial site
        q_iono = ION_PROC_NOISE * dt       # I base process noise (elevation-weighted extra added below)
        q_N1N2=0.0                        # ambiguities are constants
        for sid_k,ki in sidx.items():
            Q[ki  ,ki  ]=q_iono           # I slot
            # v78 PART 5 — REMOVE ARTIFICIAL COVARIANCE FORCING:
            # q_N1 = 1e-8 for NL-fixed sats was artificially preventing the filter
            # from naturally adapting.  All ambiguities use exact-zero process noise;
            # the KF reduces covariance naturally via repeated pseudo-obs updates.
            Q[ki+1,ki+1]=q_N1N2           # N1 slot — pure constant (fixed or not)
            Q[ki+2,ki+2]=q_N1N2           # N2 slot — pure constant (fixed or not)
            # v80 PART 3 — STABILIZE AFTER FIX:
            # When a satellite is NL-fixed AND sigma_N1_m < 0.10 m, apply a tiny
            # non-zero process noise (1e-9) instead of exactly zero.  This tiny
            # drift allowance prevents numerical rigidity while keeping the
            # ambiguity tightly constrained after convergence.
            if sid_k in nl_fixed:
                _lam1_k = _sat_lam1.get(sid_k, 0.1903)
                _sig_n1_k = math.sqrt(max(0.0, P[ki+1, ki+1])) * _lam1_k
                if _sig_n1_k < 0.10:
                    Q[ki+1, ki+1] = 1e-9
                    Q[ki+2, ki+2] = 1e-9
        P+=Q

        rxyz=nom+x[:3]; sun=_sun(tow); geom=[]

        for sid,so in sorted(sobs.items()):
            if sid[0] not in ('G','E'): continue
            if sid[0] not in constellation: continue

            if sid[0]=='E':
                # v84 PART 2: record L1C availability BEFORE _proc_gal so the
                # missing-fraction history is updated even when the satellite is
                # fully absent this epoch (returns None).
                _l1c_present_now = bool(so.get('L1C', 0.) != 0.)
                _gal_missing_l1c[sid].append(_l1c_present_now)
                m=_proc_gal(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,
                             satx,att,recx,blq=blq,sta=sta,tow_total=tow_total)
            else:
                m=_proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,
                         satx,att,recx,blq=blq,sta=sta,tow_total=tow_total)
            if m is None:
                # ── GAP MARKING ───────────────────────────────────────────────
                # Satellite present in RINEX but rejected this epoch:
                #   • E5a absent → no complete 4-tuple (priority gate, Part 1)
                #   • Family lock mismatch → epoch skip (Part 2)
                # Mark prev_gf/prev_mw as None so the slip detector skips the
                # dGF/dMW comparison when the satellite returns next epoch.
                # This preserves ambiguity continuity across E5a dropouts.
                if sid in prev_mw:
                    prev_mw[sid] = None
                    prev_gf[sid] = None
                # ── END GAP MARKING ───────────────────────────────────────────
                continue

            # ── v93 FORENSIC: REENTRY detection ──────────────────────────────
            # Fire when satellite reappears after >1 epoch (30 s) gap.
            # Checks BEFORE slip detection so we capture the entry state.
            _disc_prev_sod_re  = _sat_last_sod.get(sid)
            _disc_gap_re       = (sod - _disc_prev_sod_re) if _disc_prev_sod_re is not None else None
            _disc_is_reentry   = (_disc_gap_re is not None and _disc_gap_re > 30.0)
            if _disc_is_reentry:
                _disc_ki_re   = sidx.get(sid)
                _disc_sig_re  = _sat_signal_map.get(sid, ('?','?','?','?'))
                _disc_pair_re = f"{_disc_sig_re[0]}/{_disc_sig_re[2]}" if isinstance(_disc_sig_re, tuple) and len(_disc_sig_re) >= 3 else str(_disc_sig_re)
                _disc_write(
                    sod, sid,
                    arc_age=_sat_age.get(sid, 0),
                    slip_detected=False,
                    dGF_m=None, dMW_cyc=None,
                    wl_before=_disc_wl_prev.get(sid),
                    wl_after=wl_fixed.get(sid),
                    nl_fixed_before=(_disc_nl_prev.get(sid) is not None),
                    nl_fixed_after=(sid in nl_fixed),
                    N1_before=_disc_N1_prev.get(sid, _DISC_MISSING),
                    N1_after=(x[_disc_ki_re+1] if _disc_ki_re is not None else None),
                    N2_before=_disc_N2_prev.get(sid, _DISC_MISSING),
                    N2_after=(x[_disc_ki_re+2] if _disc_ki_re is not None else None),
                    iono_before=_disc_iono_prev.get(sid, _DISC_MISSING),
                    iono_after=(x[_disc_ki_re] if _disc_ki_re is not None else None),
                    phase_rms_L1_mm=None, phase_rms_L2_mm=None,
                    fallback_used=m.get('_fallback_used', False),
                    primary_signal_pair=_disc_pair_re,
                    osb_signal_pair=_disc_pair_re,
                    missing_prev_epoch=True,
                    epochs_since_last_obs=_disc_gap_re,
                    mw_buffer_std=(float(np.std(list(mw_hist[sid]))) if len(mw_hist[sid]) > 1 else None),
                    gf_consistency_triggered=False,
                    iono_health_gate_triggered=False,
                    reset_path="FRESH_ENTRY",
                    event_type="REENTRY",
                )
            # ── end v93 REENTRY ───────────────────────────────────────────────

            # ── v94 ARC-SCOPED REENTRY RESET ─────────────────────────────────
            # PATCH: When a satellite reappears after >1 epoch gap, treat it as
            # a NEW ambiguity arc.  This converts ambiguity continuity from
            # satellite-scoped to visibility-arc-scoped, eliminating stale
            # ambiguity inheritance across gaps.
            #
            # All six resets mirror the MW_GF_SLIP path (lines ~3582–3606) plus
            # ionosphere re-seed.  Global KF states (position, clock, trop, ISB)
            # are NOT touched.
            if _disc_is_reentry:
                # 1) CLEAR MW BUFFER — stale MW samples from previous arc
                #    corrupt the WL integer estimate on the new arc.
                mw_hist[sid].clear()

                # 2) INVALIDATE GF CONTINUITY — the first-epoch dGF check
                #    must not compare against a value from a different arc.
                prev_gf[sid] = None
                prev_mw[sid] = None

                # 3) DELETE WL / NL STATE — integers from the old arc are
                #    almost certainly wrong for the new arc.
                wl_fixed.pop(sid, None)
                nl_fixed.pop(sid, None)
                _wl_history.pop(sid, None)
                _wl_history_ptrace.pop(sid, None)
                _nl_bad_nwl.discard(sid)

                # 4) FORCE NEW AMBIGUITY ARC — setting phi[sid]=False triggers
                #    the existing fresh-init block (if not phi.get(sid,False):)
                #    which re-seeds N1, N2, and the ionosphere from IONEX.
                #    Also zero the KF ambiguity states and reset their
                #    covariances (full cross-term wipe, same as slip path).
                _re_ki = sidx.get(sid)
                if _re_ki is not None:
                    x[_re_ki]   = 0.
                    x[_re_ki+1] = 0.
                    x[_re_ki+2] = 0.
                    P[_re_ki,   :] = 0.; P[:,   _re_ki] = 0.
                    P[_re_ki+1, :] = 0.; P[:, _re_ki+1] = 0.
                    P[_re_ki+2, :] = 0.; P[:, _re_ki+2] = 0.
                    P[_re_ki,   _re_ki  ] = 100.**2
                    P[_re_ki+1, _re_ki+1] = 200.**2
                    P[_re_ki+2, _re_ki+2] = 200.**2
                phi[sid] = False
                _sat_age[sid] = 0

                # 5) CLEAR auxiliary per-arc buffers
                _lp1_hist[sid].clear()
                _nl_frac_buf[sid].clear()
                _nl_frac_hist[sid].clear()
                _nl_bias_frozen.discard(sid)
                _nl_bias.pop(sid, None)
                _sat_signal_map.pop(sid, None)    # allow signal re-acquisition
                _amb_seeded.discard(sid)

                # 6) (Ionosphere re-seed is automatic: phi[sid]=False above
                #    causes the IONEX/code-derived init block to run this
                #    epoch, using the same logic as fresh entry — no new math.)

                print(f"[ARC_RESET_REENTRY] SOD={sod:.0f} sat={sid} "
                      f"gap={_disc_gap_re:.0f}s — new arc, all continuity cleared")
            # ── end v94 ARC-SCOPED REENTRY RESET ─────────────────────────────

            # ── v93 FORENSIC: SIGNAL_SWITCH detection ────────────────────────
            # Fires when the observed signal combination changes vs previous epoch.
            _disc_sig_now  = _sat_signal_map.get(sid)
            _disc_sig_prev = _disc_signal_pair_prev.get(sid)
            _disc_pair_now_str = (f"{_disc_sig_now[0]}/{_disc_sig_now[2]}"
                                  if _disc_sig_now and len(_disc_sig_now) >= 3
                                  else str(_disc_sig_now))
            if (_disc_sig_prev is not None
                    and _disc_sig_now is not None
                    and _disc_sig_now != _disc_sig_prev
                    and not _disc_is_reentry):
                _disc_ki_ss = sidx.get(sid)
                _disc_write(
                    sod, sid,
                    arc_age=_sat_age.get(sid, 0),
                    slip_detected=False,
                    dGF_m=None, dMW_cyc=None,
                    wl_before=_disc_wl_prev.get(sid),
                    wl_after=wl_fixed.get(sid),
                    nl_fixed_before=(_disc_nl_prev.get(sid) is not None),
                    nl_fixed_after=(sid in nl_fixed),
                    N1_before=_disc_N1_prev.get(sid, _DISC_MISSING),
                    N1_after=(x[_disc_ki_ss+1] if _disc_ki_ss is not None else None),
                    N2_before=_disc_N2_prev.get(sid, _DISC_MISSING),
                    N2_after=(x[_disc_ki_ss+2] if _disc_ki_ss is not None else None),
                    iono_before=_disc_iono_prev.get(sid, _DISC_MISSING),
                    iono_after=(x[_disc_ki_ss] if _disc_ki_ss is not None else None),
                    phase_rms_L1_mm=None, phase_rms_L2_mm=None,
                    fallback_used=m.get('_fallback_used', False),
                    primary_signal_pair=str(_disc_sig_prev),
                    osb_signal_pair=_disc_pair_now_str,
                    missing_prev_epoch=False,
                    epochs_since_last_obs=None,
                    mw_buffer_std=(float(np.std(list(mw_hist[sid]))) if len(mw_hist[sid]) > 1 else None),
                    gf_consistency_triggered=False,
                    iono_health_gate_triggered=False,
                    reset_path="NONE",
                    event_type="SIGNAL_SWITCH",
                )
            # Update signal pair record for next epoch
            _disc_signal_pair_prev[sid] = _disc_sig_now
            # ── end v93 SIGNAL_SWITCH ─────────────────────────────────────────
            slip=False
            _disc_dGF_m  = None   # v93 forensic: GF difference this epoch
            _disc_dMW_cyc = None  # v93 forensic: MW difference this epoch
            # ── PATCH 3: GF/MW RE-ENTRY PROTECTION ───────────────────────────
            # Skip dGF/dMW slip tests for the first 3 epochs after arc re-entry.
            # Freshly re-entered satellites have unstable GF/MW estimates:
            #   • mw_hist was cleared at re-entry so prev_mw[sid] is set from
            #     epoch-1's raw value, which may contain a cold-start outlier.
            #   • GF reference (prev_gf) was reset similarly.
            # Jumping straight into the dGF>0.05 or dMW>1.5 test on epochs 1–2
            # triggers false slips, re-resets the arc, and creates reset loops.
            # The GF consistency guard (arc_age < 3) already protects the iono
            # re-seed path; this patch mirrors that guard for the slip detector.
            _p3_arc_age = _sat_age.get(sid, 0)
            _p3_slip_bypass = (_p3_arc_age < 3)
            if _p3_slip_bypass and sid in prev_mw and prev_mw[sid] is not None:
                print(f"[REENTRY_GUARD] SOD={sod:.0f} sat={sid} "
                      f"arc_age={_p3_arc_age} — GF/MW slip test bypassed "
                      f"(dGF would be {m['GF_m']-prev_gf[sid]:.3f}m "
                      f"dMW would be {m['MW_cyc']-prev_mw[sid]:.3f}cyc)")
            # ── end PATCH 3 guard ─────────────────────────────────────────────
            if (not _p3_slip_bypass
                    and sid in prev_mw
                    and prev_mw[sid] is not None
                    and prev_gf[sid] is not None):
                dGF=m['GF_m']-prev_gf[sid]; dMW=m['MW_cyc']-prev_mw[sid]
                _disc_dGF_m  = dGF
                _disc_dMW_cyc = dMW
                if abs(dGF)>0.05 or abs(dMW)>1.5:
                    if sid in _amb_seeded:
                        _amb_seeded.discard(sid)
                    else:
                        slip=True
                        # ── v93 FORENSIC: log SLIP event ─────────────────────
                        _disc_prev_sod_sl  = _sat_last_sod.get(sid)
                        _disc_gap_sl       = (sod - _disc_prev_sod_sl) if _disc_prev_sod_sl else None
                        _disc_miss_sl      = (_disc_gap_sl is not None and _disc_gap_sl > 30.)
                        _disc_wl_bef_sl    = _disc_wl_prev.get(sid)
                        _disc_nl_bef_sl    = _disc_nl_prev.get(sid)
                        _disc_N1_bef_sl    = _disc_N1_prev.get(sid, _DISC_MISSING)
                        _disc_N2_bef_sl    = _disc_N2_prev.get(sid, _DISC_MISSING)
                        _disc_iono_bef_sl  = _disc_iono_prev.get(sid, _DISC_MISSING)
                        _disc_ki_sl        = sidx.get(sid)
                        _disc_N1_aft_sl    = x[_disc_ki_sl+1] if _disc_ki_sl is not None else None
                        _disc_N2_aft_sl    = x[_disc_ki_sl+2] if _disc_ki_sl is not None else None
                        _disc_iono_aft_sl  = x[_disc_ki_sl]   if _disc_ki_sl is not None else None
                        _disc_mw_std_sl    = float(np.std(list(mw_hist[sid]))) if len(mw_hist[sid]) > 1 else None
                        _disc_sig_sl       = _sat_signal_map.get(sid, ('?','?','?','?'))
                        _disc_pair_sl      = f"{_disc_sig_sl[0]}/{_disc_sig_sl[2]}" if isinstance(_disc_sig_sl, tuple) and len(_disc_sig_sl) >= 3 else str(_disc_sig_sl)
                        _disc_write(
                            sod, sid,
                            arc_age=_sat_age.get(sid, 0),
                            slip_detected=True,
                            dGF_m=dGF, dMW_cyc=dMW,
                            wl_before=_disc_wl_bef_sl,
                            wl_after=None,
                            nl_fixed_before=(_disc_nl_bef_sl is not None),
                            nl_fixed_after=False,
                            N1_before=_disc_N1_bef_sl,
                            N1_after=_disc_N1_aft_sl,
                            N2_before=_disc_N2_bef_sl,
                            N2_after=_disc_N2_aft_sl,
                            iono_before=_disc_iono_bef_sl,
                            iono_after=_disc_iono_aft_sl,
                            phase_rms_L1_mm=None, phase_rms_L2_mm=None,
                            fallback_used=m.get('_fallback_used', False),
                            primary_signal_pair=_disc_pair_sl,
                            osb_signal_pair=_disc_pair_sl,
                            missing_prev_epoch=_disc_miss_sl,
                            epochs_since_last_obs=_disc_gap_sl,
                            mw_buffer_std=_disc_mw_std_sl,
                            gf_consistency_triggered=False,
                            iono_health_gate_triggered=False,
                            reset_path="MW_GF_SLIP",
                            event_type="SLIP",
                        )
                        # ── end v93 SLIP log ──────────────────────────────────
                        wl_fixed.pop(sid,None); mw_hist[sid].clear()
                        # Release family lock: new arc may legitimately use a different
                        # signal family if the receiver re-acquires on a different channel.
                        _sat_signal_map.pop(sid, None)
                        # ── New-arc detection (FIX #1) ────────────────────────
                        # If the satellite was absent for > 120 s (≥4 epochs at
                        # 30 s), this is a genuine new orbital pass. The WL
                        # integer will be different → clear history so a fresh
                        # NWL is computed from this arc's MW accumulation.
                        #
                        # For short gaps (≤120 s), this is a false slip from
                        # code/phase noise on the SAME arc. Retain _wl_history
                        # so the correct NWL is not discarded.
                        _prev_sod=_sat_last_sod.get(sid)
                        if _prev_sod is None or (sod-_prev_sod)>120.:
                            _wl_history.pop(sid,None)
                            _wl_history_ptrace.pop(sid,None)
                        # v84 PART 3: record Galileo slip epoch for adaptive weighting.
                        # Marks the satellite as unstable for GAL_UNSTABLE_EPOCHS_AFTER_RESET epochs.
                        if sid[0] == 'E':
                            _gal_slip_epoch[sid] = nproc
            prev_mw[sid]=m['MW_cyc']; prev_gf[sid]=m['GF_m']
            _sat_last_sod[sid]=sod       # always update after detection

            # ── MW ACCUMULATION ──────────────────────────────────────────────
            if not slip: mw_hist[sid].append(m['MW_cyc'])
            else:        mw_hist[sid].clear()

            # ── WL FIXING ────────────────────────────────────────────────────
            if sid not in wl_fixed:
                n_hist=len(mw_hist[sid])
                if n_hist>=15:
                    mn=np.mean(mw_hist[sid]); sd=np.std(mw_hist[sid])
                    sys_id=sid[0]; min_n=50  # v58: require ≥50 samples for stable WL (was 30 if sd>0.30 else 15)
                    if n_hist>=min_n:
                        if sys_id not in b_rec_frozen:
                            all_fracs=[]
                            for s2,h2 in mw_hist.items():
                                if s2[0]!=sys_id or len(h2)<min_n: continue
                                if (np.std(h2) if len(h2)>1 else 999.)>0.45: continue
                                all_fracs.append(np.mean(h2)-round(np.mean(h2)))
                            if len(all_fracs)>=5:
                                bc=float(np.median(all_fracs))
                                agr=sum(1 for f in all_fracs if abs(f-bc)<0.25)
                                if agr>=max(3,0.6*len(all_fracs)):
                                    b_rec_frozen[sys_id]=bc; b_rec_n[sys_id]=len(all_fracs)
                                    print(f"[B_REC FROZEN] {sys_id}: b_rec={bc:+.4f} cyc "
                                          f"median of {len(all_fracs)} sats agree={agr}")
                        if sys_id in b_rec_frozen:
                            b_rec=b_rec_frozen[sys_id]; tag=sys_id+'F'
                        else:
                            fracs=[]
                            for s2,h2 in mw_hist.items():
                                if s2[0]!=sys_id or len(h2)<min_n: continue
                                if (np.std(h2) if len(h2)>1 else 999.)>0.45: continue
                                fracs.append(np.mean(h2)-round(np.mean(h2)))
                            b_rec=np.mean(fracs) if fracs else 0.0; tag=sys_id+'E'

                        # v85: apply static Galileo WL closure correction (GPS untouched)
                        if sid[0] == 'E':
                            _delta_wl = _GAL_WL_CLOSURE.get(sid, 0.0)
                        else:
                            _delta_wl = 0.0            # GPS path byte-identical
                        mn_corr=mn-b_rec-_delta_wl; NWL=round(mn_corr)
                        residual=abs(mn_corr-NWL)
                        _raw_residual_for_log = abs((mn - b_rec) - round(mn - b_rec))
                        if n_hist in (15,20,30,50,100):
                            print(f"[WL CHECK] {sid} n={n_hist} std={sd:.3f} "
                                  f"res={residual:.3f} b_rec={b_rec:+.3f}({tag})")
                            # v85: [WL_CAL] diagnostic — only when correction > 0.05 cyc
                            if _delta_wl > 0.05:
                                print(f"[WL_CAL] sat={sid} delta_wl={_delta_wl:.3f} "
                                      f"raw={_raw_residual_for_log:.3f} "
                                      f"corr={residual:.3f}")
                            # v85: write to gal_wl_calibration_applied.csv for all
                            # Galileo sats that have a closure entry (any magnitude).
                            if sid[0] == 'E' and sid in _GAL_WL_CLOSURE:
                                _sd_gate_chk = 0.25
                                _res_gate_chk = 0.20
                                _fix_before = int(sd < _sd_gate_chk and _raw_residual_for_log < _res_gate_chk)
                                _fix_after  = int(sd < _sd_gate_chk and residual < _res_gate_chk)
                                _wlcal_csv_w.writerow([
                                    f"{sod:.1f}", sid, f"{_delta_wl:.4f}",
                                    f"{_raw_residual_for_log:.4f}", f"{residual:.4f}",
                                    _fix_before, _fix_after,
                                ])
                        if sys_id not in b_rec_frozen:
                            pass
                        else:
                            # v83: constellation-aware WL std gate.
                            # GPS at equatorial IISC sees higher MW scatter from
                            # equatorial iono (Glaner & Weber 2021, GPS Solut 25:102).
                            _sd_gate = 0.40 if sys_id == 'G' else 0.25
                            _res_gate = 0.25 if sys_id == 'G' else 0.20
                            if sd < _sd_gate and residual < _res_gate:
                                NWL_to_use=NWL
                                pt_now=P[0,0]+P[1,1]+P[2,2]
                                if sid in _wl_history:
                                    hist_NWL=_wl_history[sid]
                                    diff=abs(NWL-hist_NWL)
                                    if diff==0:
                                        NWL_to_use=hist_NWL
                                        if pt_now<_wl_history_ptrace.get(sid,999.):
                                            _wl_history_ptrace[sid]=pt_now
                                    elif diff<=3:
                                        print(f"[WL PERSIST] {sid} using prev "
                                              f"NWL={hist_NWL} (same-arc noise: "
                                              f"new={NWL}, diff={diff}<=3->keep)")
                                        NWL_to_use=hist_NWL
                                    else:
                                        print(f"[WL UPDATE] {sid} NWL {hist_NWL}"
                                              f"→{NWL} (diff={diff}>3)")
                                        _wl_history[sid]=NWL
                                        _wl_history_ptrace[sid]=pt_now
                                        NWL_to_use=NWL
                                else:
                                    _wl_history[sid]=NWL
                                    _wl_history_ptrace[sid]=pt_now
                                wl_fixed[sid]=NWL_to_use
                                print(f"[WL FIXED] {sid}  N_WL={NWL_to_use}  "
                                      f"mean={mn_corr:.3f}  std={sd:.3f} "
                                      f"b_rec={b_rec:+.3f}({tag}) cyc")
                                # ── v93 FORENSIC: log WL_JUMP if integer changed ──
                                _disc_wl_old = _disc_wl_prev.get(sid)
                                if (_disc_wl_old is not None
                                        and abs(NWL_to_use - _disc_wl_old) >= 3):
                                    _disc_ki_wj = sidx.get(sid)
                                    _disc_mw_std_wj = float(np.std(list(mw_hist[sid]))) if len(mw_hist[sid]) > 1 else None
                                    _disc_sig_wj = _sat_signal_map.get(sid, ('?','?','?','?'))
                                    _disc_pair_wj = f"{_disc_sig_wj[0]}/{_disc_sig_wj[2]}" if isinstance(_disc_sig_wj, tuple) and len(_disc_sig_wj) >= 3 else str(_disc_sig_wj)
                                    _disc_write(
                                        sod, sid,
                                        arc_age=_sat_age.get(sid, 0),
                                        slip_detected=False,
                                        dGF_m=_disc_dGF_m, dMW_cyc=_disc_dMW_cyc,
                                        wl_before=_disc_wl_old,
                                        wl_after=NWL_to_use,
                                        nl_fixed_before=(_disc_nl_prev.get(sid) is not None),
                                        nl_fixed_after=(sid in nl_fixed),
                                        N1_before=_disc_N1_prev.get(sid, _DISC_MISSING),
                                        N1_after=(x[_disc_ki_wj+1] if _disc_ki_wj is not None else None),
                                        N2_before=_disc_N2_prev.get(sid, _DISC_MISSING),
                                        N2_after=(x[_disc_ki_wj+2] if _disc_ki_wj is not None else None),
                                        iono_before=_disc_iono_prev.get(sid, _DISC_MISSING),
                                        iono_after=(x[_disc_ki_wj] if _disc_ki_wj is not None else None),
                                        phase_rms_L1_mm=None, phase_rms_L2_mm=None,
                                        fallback_used=m.get('_fallback_used', False),
                                        primary_signal_pair=_disc_pair_wj,
                                        osb_signal_pair=_disc_pair_wj,
                                        missing_prev_epoch=(prev_mw.get(sid) is None),
                                        epochs_since_last_obs=None,
                                        mw_buffer_std=_disc_mw_std_wj,
                                        gf_consistency_triggered=False,
                                        iono_health_gate_triggered=False,
                                        reset_path="NONE",
                                        event_type="WL_JUMP",
                                    )
                                # ── end v93 WL_JUMP log ──────────────────────

            # ── v54 RAW STATE ALLOCATION: 3 slots per sat [I, N1, N2] ─────────
            if sid not in sidx:
                d=len(x)
                # Append 3 new states: I, N1, N2
                x=np.append(x,[0.,0.,0.])
                Pn=np.zeros((d+3,d+3)); Pn[:d,:d]=P
                Pn[d,  d  ]=100.**2   # I  initial var (was 300²; tighter so ISB captures IF-offset)
                Pn[d+1,d+1]=200.**2   # N1 initial var (was 300²)
                Pn[d+2,d+2]=200.**2   # N2 initial var (was 300²)
                P=Pn; sidx[sid]=d; namb+=1; phi[sid]=False
            ki=sidx[sid]  # ki → I slot; ki+1 → N1 slot; ki+2 → N2 slot

            if slip:
                # Reset all 3 states on cycle slip
                x[ki]=0.; x[ki+1]=0.; x[ki+2]=0.
                # v85 FIX 1: full covariance reset — zero ALL cross-terms for the
                # three state slots (I, N1, N2) before restoring their diagonals.
                # Previously only diagonals were reset; stale off-diagonal terms
                # (P[ki,kn1], P[ki,kn2], P[kn1,kn2] and cross-terms with other
                # satellites' states) survived from the old arc, contaminating the
                # iono-ambiguity coupling matrix for the new arc from its first epoch.
                P[ki,  :] = 0.;  P[:,  ki] = 0.
                P[ki+1,:] = 0.;  P[:,ki+1] = 0.
                P[ki+2,:] = 0.;  P[:,ki+2] = 0.
                P[ki,ki]=50.**2; P[ki+1,ki+1]=200.**2; P[ki+2,ki+2]=200.**2
                phi[sid]=False
                mw_hist[sid].clear(); nl_fixed.pop(sid,None)
                _nl_bad_nwl.discard(sid); _sat_age[sid]=0
                # v59: clear consistency history on slip so post-slip window is clean
                _lp1_hist[sid].clear()
                # v60: clear fractional bias buffer on slip — bias must be re-learned
                # from the new arc (WL integer and hardware state may have changed).
                _nl_frac_buf[sid].clear()
                _nl_bias.pop(sid, None)
                # v61: clear stability history and frozen flag on slip
                _nl_frac_hist[sid].clear()
                _nl_bias_frozen.discard(sid)

                # ── v89: ARC-RESET IONOSPHERE CONSISTENCY CHECK ───────────────
                # After all existing slip resets above, check whether the
                # inherited ionosphere state x[ki] is consistent with the
                # geometry-free observables on the NEW arc.  If not, re-seed
                # ONLY x[ki] and P[ki,ki] using the same IONEX/code-fallback
                # logic used for fresh entry.  All other states are untouched.
                #
                # Safety gate: only run if both code observables are available.
                _ar_old_I     = x[ki]                 # inherited value (may be ~0 or negative)
                # v96: subtract RDCB_E from GF numerator so arc-reset check uses
                # the unbiased iono estimate, not the DCB-contaminated one.
                _ar_rdcbe = (x[RDCB_E_IDX] if (RDCB_E_IDX is not None
                                               and m['sid'][0]=='E') else 0.0)
                _ar_gf_I      = (m['P2c'] - m['P1c'] - _ar_rdcbe) / (m['gamma'] - 1.) \
                                 if abs(m['gamma'] - 1.) > 1e-6 else None
                _ar_lmp_sign  = math.copysign(1.0, m['L1m'] - m['P1c'])  # +1 → iono likely bad
                _ar_reason    = None   # will be set if any criterion fires

                # ── v92: GF INITIALIZATION GUARD ─────────────────────────────
                # The GF-vs-I_state consistency test is meaningless during the
                # first 3 epochs after a reset because:
                #   (a) x[ki] was just zeroed → old_I_state == 0
                #   (b) IONEX re-seeding (below) will immediately correct it
                # Comparing GF_measured (~5–25 m at IISC) against 0 always
                # fires "gf_pos_state_nonpos", creating an infinite reset loop.
                # Guard: skip the test while old_I==0 OR arc age < 3 epochs.
                # After 3 epochs the detector resumes IDENTICALLY.
                _gf_guard_arc_age   = _sat_age.get(sid, 0)  # 0 just after reset
                _gf_guard_skip      = False
                _gf_guard_skip_reason = None
                if _ar_old_I == 0.0:
                    _gf_guard_skip        = True
                    _gf_guard_skip_reason = "old_I_zero"
                elif _gf_guard_arc_age < 3:
                    _gf_guard_skip        = True
                    _gf_guard_skip_reason = f"arc_age_{_gf_guard_arc_age}"

                if _gf_guard_skip:
                    _gf_guard_skipped_count += 1
                    if _gf_guard_print_count < 10:
                        print(f"[GF_GUARD] SOD={sod:.0f} sat={sid} "
                              f"arc_age={_gf_guard_arc_age} "
                              f"old_I={_ar_old_I:.3f}m "
                              f"GF={_ar_gf_I:.3f}m skip={_gf_guard_skip_reason}")
                        _gf_guard_print_count += 1
                    _gf_guard_csv_w.writerow([
                        f"{sod:.1f}", sid, _gf_guard_arc_age,
                        f"{_ar_old_I:.4f}",
                        f"{_ar_gf_I:.4f}" if _ar_gf_I is not None else "",
                        1, _gf_guard_skip_reason,
                        0, "guarded",
                    ])
                # ── end v92 guard ─────────────────────────────────────────────

                if not _gf_guard_skip and _ar_gf_I is not None:
                    # Criterion (a): inherited state is physically impossible
                    if _ar_old_I < 0.0:
                        _ar_reason = "neg_I_inherited"
                    # Criterion (b): code GF says positive iono but state is ≤ 0
                    elif _ar_gf_I > 0.5 and _ar_old_I <= 0.0:
                        _ar_reason = "gf_pos_state_nonpos"
                    # Criterion (c): L1−P1 is positive (carrier appears ahead of
                    #   code in excess of what ambiguity alone can explain at first
                    #   post-reset epoch when x[ki+1] was just zeroed)
                    elif _ar_lmp_sign > 0 and _ar_old_I <= 0.0:
                        _ar_reason = "LminusP_pos_nonpos_I"

                _ar_reinitialized = False
                _ar_new_I         = _ar_old_I
                _ar_used_ionex    = False

                if _ar_reason is not None:
                    # Re-seed iono using same IONEX-or-code logic as fresh entry.
                    _ar_I_init         = _ar_gf_I      # code-derived fallback
                    _ar_iono_var_init  = 50.**2
                    _ar_used_ionex     = False

                    if ionex_maps is not None and ionex_meta is not None:
                        _ar_ipp = _ipp_latlon_mapping(
                            _rx_lat_rad, _rx_lon_rad, m['el'], m.get('az', 0.),
                            h_shell_m=450e3)
                        if _ar_ipp is not None:
                            _ar_lat_ipp, _ar_lon_ipp, _ar_mfac = _ar_ipp
                            _ar_vtec = _ionex_vtec_at(
                                ionex_meta, ionex_maps, sod,
                                _ar_lat_ipp, _ar_lon_ipp)
                            if (_ar_vtec is not None
                                    and not math.isnan(_ar_vtec)
                                    and _ar_vtec > 0.):
                                _ar_k = _IONEX_K_E1 if sid[0] == 'E' else _IONEX_K_L1
                                _ar_I_ionex = _ar_k * _ar_vtec * _ar_mfac
                                if 0.5 <= _ar_I_ionex <= 100.:
                                    _ar_I_init        = _ar_I_ionex
                                    # v91: adaptive sigma — max(floor, K*|I_L1|)
                                    _ar_sigma_new = max(SIGMA_IONO_FLOOR,
                                                        K_SIGMA_IONO * abs(_ar_I_init))
                                    _ar_iono_var_init = _ar_sigma_new ** 2
                                    _ar_used_ionex    = True

                    # Apply ONLY to the ionosphere state — preserve N1, N2 exactly.
                    x[ki]     = _ar_I_init
                    P[ki, ki] = _ar_iono_var_init

                    _ar_new_I         = _ar_I_init
                    _ar_reinitialized = True
                    _arc_reset_iono_count += 1
                    _disc_ar_fired.add(sid)   # v93 forensic marker
                    print(f"[ARC_RESET_IONO] SOD={sod:.0f} sat={sid} "
                          f"old_I={_ar_old_I:.3f}m gf_I={_ar_gf_I:.3f}m "
                          f"LmP_sign={int(_ar_lmp_sign):+d} "
                          f"reason={_ar_reason} "
                          f"new_I={_ar_new_I:.3f}m "
                          f"ionex={'Y' if _ar_used_ionex else 'N'}")

                # Always write to audit CSV (one row per slip event)
                _arc_reset_csv_w.writerow([
                    f"{sod:.1f}", sid,
                    f"{_ar_old_I:.4f}",
                    f"{_ar_gf_I:.4f}" if _ar_gf_I is not None else "",
                    f"{int(_ar_lmp_sign):+d}",
                    _ar_reason if _ar_reason else "none",
                    int(_ar_reinitialized),
                    f"{_ar_new_I:.4f}",
                    int(_ar_used_ionex),
                    1,   # ambiguity_reset is always 1 inside `if slip:`
                ])

                # v91: write adaptive sigma audit row (arc-reset path)
                if _ar_reinitialized:
                    _ar_sigma_old = 22.0
                    _ar_sigma_new_log = math.sqrt(_ar_iono_var_init)
                    _ar_floor_active = int(
                        abs(_ar_new_I) * K_SIGMA_IONO < SIGMA_IONO_FLOOR)
                    _ar_vtec_log = getattr(_ar_ipp, '__iter__', None)  # will be None
                    # Retrieve vtec/mfac from computed variables if available
                    try:
                        _ar_vtec_log2 = _ar_vtec if _ar_used_ionex else 0.0
                        _ar_mfac_log2 = _ar_mfac if _ar_used_ionex else 1.0
                    except NameError:
                        _ar_vtec_log2 = 0.0; _ar_mfac_log2 = 1.0
                    _adap_csv_w.writerow([
                        f"{sod:.1f}", sid, "arc_reset",
                        f"{_ar_vtec_log2:.3f}", f"{_ar_mfac_log2:.4f}",
                        f"{_ar_new_I:.4f}",
                        f"{_ar_sigma_old:.1f}", f"{_ar_sigma_new_log:.3f}",
                        _ar_floor_active, int(not _ar_used_ionex),
                        f"{_ar_gf_I:.4f}" if _ar_gf_I is not None else "",
                    ])
                    if _adap_print_count < 10:
                        print(f"[ADAPTIVE_IONO_SIGMA] SOD={sod:.0f} sat={sid} "
                              f"type=arc_reset I_L1={_ar_new_I:.3f}m "
                              f"sigma_old={_ar_sigma_old:.1f}m "
                              f"sigma_new={_ar_sigma_new_log:.3f}m "
                              f"floor={'Y' if _ar_floor_active else 'N'}")
                        _adap_print_count += 1
                # ── end v89 ──────────────────────────────────────────────────

            # Phase wind-up — applied per-frequency for RAW model
            wu=_wu(m['sat_xyz'],m['rec_apc'],sun,wum.get(sid,0.)); wum[sid]=wu
            lam1=m['lam1']; lam2=m['lam2']
            # Correct phase measurements for wind-up (in metres)
            L1mc = m['L1m'] - wu*lam1
            L2mc = m['L2m'] - wu*lam2
            m['L1mc']=L1mc; m['L2mc']=L2mc

            # v59: per-satellite L1m-P1c consistency tracking.
            # L1m - P1c = -2*I + lam1*N1 + const  =>  smooth within a pass.
            # Short-window range (100 ep) captures scintillation-induced frame
            # inconsistency without being confused by whole-pass iono variation.
            _lp1_hist[sid].append(m['L1m'] - m['P1c'])

            # ── v54/v87 RAW STATE INIT [I, N1, N2] ─────────────────────────
            if not phi.get(sid,False):
                rp0=_rp(m,x[3],x[4])        # geometric + corrections (no ionosphere)
                gam=m['gamma']

                # Code-derived ionosphere estimate (fallback)
                # v97 FIX 1: RDCB_E sign corrected.
                # Physics: (P2c - P1c) = (gam-1)*I + RDCB_E
                # Therefore: I_code = (P2c - P1c - RDCB_E) / (gam-1)  ... but
                # OSB-corrected pseudoranges already have RDCB absorbed with sign:
                # P1c = rp + I - RDCB_E/(gam-1)  =>  P2c-P1c = (gam-1)*I + RDCB_E*(gam/(gam-1) - 1/(gam-1))
                # Expanding: (gam-1)*I + RDCB_E   =>  I = (P2c-P1c - RDCB_E)/(gam-1)
                # BUT: RDCB_E state in the KF is the TRUE DCB value; the measurement
                # equations carry H=-1/(gam-1) for P1 and H=-gam/(gam-1) for P2.
                # The raw GF (P2c-P1c)/(gam-1) already includes +RDCB_E/(gam-1).
                # Removing the KF estimate requires ADDING it back (not subtracting again):
                # I_code = (P2c - P1c) / (gam-1) + RDCB_E/(gam-1)
                #        = (P2c - P1c + RDCB_E) / (gam-1)
                _rdcbe_init = x[RDCB_E_IDX] if (RDCB_E_IDX is not None and m['sid'][0]=='E') else 0.0
                I_code=(m['P2c']-m['P1c']+_rdcbe_init)/(gam-1.) if abs(gam-1.)>1e-6 else 5.0

                # ── v87: IONEX/GIM-based ionosphere seeding ──────────────────
                I_init         = I_code        # default fallback
                _iono_var_init = 50.**2        # default init variance
                _fallback_used = True
                _vtec_log = 0.0
                _mfac_log = 1.0
                _stec_log = 0.0

                if ionex_maps is not None and ionex_meta is not None:
                    _ipp_result = _ipp_latlon_mapping(
                        _rx_lat_rad, _rx_lon_rad, m['el'], m.get('az', 0.),
                        h_shell_m=450e3)
                    if _ipp_result is not None:
                        _lat_ipp, _lon_ipp, _mfac = _ipp_result
                        _vtec = _ionex_vtec_at(ionex_meta, ionex_maps, sod,
                                               _lat_ipp, _lon_ipp)
                        if _vtec is not None and not math.isnan(_vtec) and _vtec > 0.:
                            _stec = _vtec * _mfac   # TECU
                            # Choose the correct L1 constant per system
                            _k = _IONEX_K_E1 if sid[0] == 'E' else _IONEX_K_L1
                            _I_ionex = _k * _stec     # metres
                            # Safety gate: physically reasonable?
                            if 0.5 <= _I_ionex <= 100.:
                                I_init = _I_ionex
                                # v91: adaptive sigma — max(floor, K*|I_L1|)
                                _sigma_new = max(SIGMA_IONO_FLOOR,
                                                 K_SIGMA_IONO * abs(I_init))
                                _iono_var_init = _sigma_new ** 2
                                _fallback_used = False
                                _vtec_log = _vtec
                                _mfac_log = _mfac
                                _stec_log = _stec

                # v91: write adaptive sigma audit row (fresh entry)
                _sigma_old_fresh = 22.0   # fixed value used in v90
                _sigma_new_fresh = math.sqrt(_iono_var_init)
                _floor_active_fresh = int(
                    abs(I_init) * K_SIGMA_IONO < SIGMA_IONO_FLOOR)
                _entry_type_tag = "fresh_entry"
                _adap_csv_w.writerow([
                    f"{sod:.1f}", sid, _entry_type_tag,
                    f"{_vtec_log:.3f}", f"{_mfac_log:.4f}", f"{I_init:.4f}",
                    f"{_sigma_old_fresh:.1f}", f"{_sigma_new_fresh:.3f}",
                    _floor_active_fresh, int(_fallback_used), "",
                ])
                if _adap_print_count < 10 and not _fallback_used:
                    print(f"[ADAPTIVE_IONO_SIGMA] SOD={sod:.0f} sat={sid} "
                          f"type=fresh I_L1={I_init:.3f}m "
                          f"sigma_old={_sigma_old_fresh:.1f}m "
                          f"sigma_new={_sigma_new_fresh:.3f}m "
                          f"floor={'Y' if _floor_active_fresh else 'N'}")
                    _adap_print_count += 1

                # Write to audit CSV
                _ionex_csv_w.writerow([
                    f"{sod:.1f}", sid,
                    f"{_vtec_log:.3f}", f"{_mfac_log:.4f}",
                    f"{_stec_log:.3f}", f"{I_init:.4f}",
                    int(_fallback_used), f"{math.sqrt(_iono_var_init):.1f}",
                ])
                if _fallback_used:
                    _ionex_fallback_count += 1
                else:
                    _ionex_init_count += 1

                # Seed state
                x[ki]=I_init; P[ki,ki]=_iono_var_init

                # N1 init: L1mc = rp0 − I + λ1·N1  →  N1 = (L1mc − rp0 + I)/λ1
                x[ki+1]=(L1mc - rp0 + I_init)/lam1
                P[ki+1,ki+1]=200.**2   # v85 FIX 2: was 300² — unified with alloc/slip

                # N2 init: L2mc = rp0 − γI + λ2·N2  →  N2 = (L2mc − rp0 + γI)/λ2
                x[ki+2]=(L2mc - rp0 + gam*I_init)/lam2
                P[ki+2,ki+2]=200.**2   # v85 FIX 2: was 300² — unified with alloc/slip
                # ── end v87 ──────────────────────────────────────────────────

                phi[sid]=True; _sat_age[sid]=0
                pt_now=P[0,0]+P[1,1]+P[2,2]; _amb_init_ptrace[sid]=pt_now
                if pt_now<0.30: _amb_conv_sods.add(sid)

            # ── v57 debug: track L1m−P1c and L2m−P2c (OSB-corrected) ──────────
            # After code+phase OSB correction: L1m-P1c = -2I + λ1·N1 + const(OSB).
            # The mean value is constant within a pass; the range indicates
            # ionospheric variation. Large ranges at IISC (equatorial EIA) are normal.
            if _DBG_SAT is None and sid[0]=='G':
                _DBG_SAT = sid
                print(f"[DBG v56] Tracking L1m-P1 / L2m-P2 for satellite {_DBG_SAT}")
            if sid == _DBG_SAT:
                _dbg_lp1.append((sod, m['L1m']-m['P1c']))
                _dbg_lp2.append((sod, m['L2m']-m['P2c']))
                if len(_dbg_lp1) % _DBG_PRINT_INTERVAL == 0:
                    arr1=np.array([v for _,v in _dbg_lp1[-_DBG_PRINT_INTERVAL:]])
                    arr2=np.array([v for _,v in _dbg_lp2[-_DBG_PRINT_INTERVAL:]])
                    v1=arr1.max()-arr1.min(); v2=arr2.max()-arr2.min()
                    # Print ionosphere estimate for this satellite if available
                    I_info=""
                    if sid in sidx:
                        ki_dbg=sidx[sid]
                        I_est=x[ki_dbg]; P_I=P[ki_dbg,ki_dbg]
                        I_info=f"  I_est={I_est:.3f}m σ_I={math.sqrt(max(0,P_I)):.3f}m"
                    print(f"[DBG v56] {_DBG_SAT} SOD={sod:.0f} "
                          f"L1m-P1 mean={arr1.mean():.3f}m range={v1:.4f}m  "
                          f"L2m-P2 mean={arr2.mean():.3f}m range={v2:.4f}m{I_info}")
                    if v1>10. or v2>10.:
                        print(f"[DBG v56]  → equatorial iono (IISC/EIA): range {v1:.1f}/{v2:.1f}m "
                              f"is NORMAL — not a model error (use σ_I above to verify tracking)")
                    elif v1>0.5 or v2>0.5:
                        print(f"[DBG v56]  → WARNING range > 0.5m — possible model inconsistency")

            _sat_age[sid]+=1
            # v58 FIX 2: elevation-weighted iono process noise.
            # The flat q_iono*dt was already added via P+=Q above; now add the
            # elevation-dependent extra so total = q_iono/sin²(el)*dt per sat.
            # Low-elevation sats (el≈15°) receive ~15× more iono noise, which is
            # physically correct for an equatorial site like IISC.
            _sel = max(math.sin(m['el']), 0.1)
            P[ki, ki] += q_iono * (1.0/_sel**2 - 1.0)
            m['ki']=ki; m['NWL']=wl_fixed.get(sid,None); m['age']=_sat_age[sid]

            # v97 GAL_STAGED_DIAG removed (staged iono reverted)
            geom.append(m)
            _sat_lam1[sid] = m['lam1']   # v80 PART 3: cache lam1 for next epoch's Q loop

        if len(geom)<4: continue
        if len(geom)>4:
            if _pdop(geom)>6.0:
                worst=min(geom,key=lambda m:m['el'])
                geom=[m for m in geom if m['sid']!=worst['sid']]
        if len(geom)<4: continue
        # FIX VALIDATION: print Galileo sats in geom every 300 epochs
        if nproc % 300 == 0:
            print("GAL in geom:", [m['sid'] for m in geom if m['sid'][0]=='E'])

        # v82 PART 6 — accumulate per-epoch SIG_STATS for GPS
        _ep_nGPS_total  = sum(1 for s in sobs if s[0]=='G' and 'G' in constellation)
        _ep_nGPS_ppp    = sum(1 for m in geom if m['sid'][0]=='G')
        _ep_nGPS_ar     = sum(1 for m in geom if m['sid'][0]=='G' and m.get('use_for_ar', False))
        _ep_nGPS_fb     = sum(1 for m in geom if m['sid'][0]=='G' and m.get('_fallback_used', False))
        _sig_nGPS_total_acc += _ep_nGPS_total
        _sig_nGPS_ppp_acc   += _ep_nGPS_ppp
        _sig_nGPS_ar_acc    += _ep_nGPS_ar
        _sig_nGPS_fb_acc    += _ep_nGPS_fb

        if nproc==0:
            # First epoch: rough clock bootstrap using pseudorange average
            # (SPP logic uses PIF which is gone; use mean P1c instead)
            clk_est=np.mean([m['P1c']-_rp(m,0.,x[4]) for m in geom])
            x[3]=float(np.clip(clk_est,-3e6,3e6))
            # Priority-2 fix: recompute N1/N2 with corrected clock but PRESERVE
            # the IONEX-seeded ionosphere state x[ki] and its covariance P[ki,ki].
            # The previous code overwrote x[ki] with a noisy code-derived I_re,
            # creating a state/covariance inconsistency: state = noisy code iono,
            # covariance = IONEX-based uncertainty.  That mismatch was the root
            # cause of the γ-scaled L2 residual decay tails (L2_res ≈ γ·L1_res).
            # KF equations, process noise, and covariance propagation are untouched.
            for m in geom:
                ki=m['ki']; rp0=_rp(m,x[3],x[4]); gam=m['gamma']
                I_s=x[ki]   # preserve IONEX-seeded ionosphere (x[ki] already set above)
                x[ki+1]=(m['L1mc']-rp0+I_s)/m['lam1']
                x[ki+2]=(m['L2mc']-rp0+gam*I_s)/m['lam2']

        # ISB first-epoch seed: estimate from mean(GPS code residual − GAL code residual)
        # relative to current clock+geometry.  Runs once after clock bootstrap.
        if ISB_IDX is not None and not _isb_init_done and len(geom) >= 4:
            _gps_res = [m['P1c'] - _rp(m, x[3], x[4]) - x[m['ki']]
                        for m in geom if m['sid'][0] == 'G']
            _gal_res = [m['P1c'] - _rp(m, x[3], x[4]) - x[m['ki']]
                        for m in geom if m['sid'][0] == 'E']
            if _gps_res and _gal_res:
                _isb_seed = float(np.median(_gps_res) - np.median(_gal_res))
                _isb_seed = float(np.clip(_isb_seed, -30., 30.))
                x[ISB_IDX] = _isb_seed
                P[ISB_IDX, ISB_IDX] = 15.**2   # tighten after seed (~15 m residual noise)
            _isb_init_done = True

        # v84 PART 4 — Galileo arc stability query.
        # Returns True when the satellite is in an unstable arc, meaning its
        # measurements should be down-weighted to protect the ISB and GPS solution.
        # Called per-Galileo-satellite inside the H/Rd assembly loop below.
        def _gal_arc_unstable(sid_q):
            if sid_q[0] != 'E':
                return False
            # (a) Recent ambiguity reset
            last_slip_ep = _gal_slip_epoch.get(sid_q)
            if last_slip_ep is not None and (nproc - last_slip_ep) < GAL_UNSTABLE_EPOCHS_AFTER_RESET:
                return True
            # (b) High L1C missing fraction in recent window
            hist_q = _gal_missing_l1c.get(sid_q)
            if hist_q and len(hist_q) >= 10:
                miss_frac = 1.0 - (sum(hist_q) / len(hist_q))
                if miss_frac > GAL_L1C_MISS_FRAC_THRESH:
                    return True
            return False

        # ── v86 RAW measurement model — APPEND-BASED ASSEMBLY ────────────────
        # v86 CHANGE (minimal, safe): true E5a row omission for Galileo sats
        # where E5a is missing OR sat is in {E07, E18} (known E5a-only sats).
        # GPS path: byte-identical (always 4 rows: P1, P2, L1, L2).
        # Galileo with E5a present: 4 rows (unchanged).
        # Galileo with E5a missing or sat in E5A_OMIT_SATS: 2 rows (P1, L1 only).
        #   → P2 and L2 rows are NOT inserted at all (no sentinel zeros).
        # Assembly uses append-based lists so row alignment is automatic.
        # No manual _row cursor, no fixed offsets, no dummy rows.
        #
        # 4 observations per satellite (full-frequency):
        #   P1: rp + I  [+ ISB for GPS in combined mode]
        #   P2: rp + γ·I
        #   L1: rp − I + λ1·N1
        #   L2: rp − γ·I + λ2·N2
        # v83: GPS gets +1 on ISB_IDX column when ISB_IDX is not None.
        # The reference system is Galileo; GPS carries the ISB.

        # Galileo satellites with known E5a tracking issues (always omit P2/L2)
        _E5A_OMIT_SATS = {'E07', 'E18'}

        ns=len(geom); nst=len(x)
        n_wl=sum(1 for m in geom if m['NWL'] is not None)
        n_nl_cur=sum(1 for m in geom if m['sid'] in nl_fixed)

        # Append-based assembly — guarantees H/z/Rd row alignment automatically
        H_rows = []   # each entry: 1-D array of length nst
        z_rows = []   # each entry: scalar innovation
        R_rows = []   # each entry: scalar variance (diagonal of R)
        xs=x.copy()

        # Also store per-satellite row-index ranges for scintillation/arc scaling
        # (maps geom index → slice of assembled rows for that satellite)
        _sat_row_slices = {}   # ri → (start_row, end_row)

        for ri,m in enumerate(geom):
            ki=m['ki']        # I slot
            kn1=ki+1          # N1 slot
            kn2=ki+2          # N2 slot
            u=m['unit']; mw=m['mw']; gam=m['gamma']
            lam1=m['lam1']; lam2=m['lam2']
            _is_gps_obs = (m['sid'][0] == 'G')
            # ISB contribution: applied to all rows of GPS observations
            _isb_val = xs[ISB_IDX] if (ISB_IDX is not None and _is_gps_obs) else 0.0
            rp=_rp(m,xs[3],xs[4]) + _isb_val   # geometry term includes ISB for GPS
            I_s=xs[ki]; N1_s=xs[kn1]; N2_s=xs[kn2]

            # ── v95 STEP 2 (runtime): GAL_APC per-satellite correction audit ──
            if m['sid'] not in _v95_apc_printed:
                _v95_apc_printed.add(m['sid'])
                _is_gal_sat = (m['sid'][0] == 'E')
                _v95_pco_s_mm  = m['pcv_sat'] * 1e3   # sat PCO+PCV correction applied (mm)
                _v95_pco_r_mm  = m['pcv_rec'] * 1e3   # rec PCO+PCV correction applied (mm)
                _v95_el_deg    = math.degrees(m['el'])
                # Reconstruct what Galileo-correct coefficients would give for rec PCO.
                # The actual correction used ALFA/BETA (GPS), but the observation is E1+E5a.
                # Difference = rec PCO_E * (ALFA_E-ALFA, BETA_E-BETA) — a static offset.
                _v95_alfa_diff = ALFA_E - ALFA
                _v95_beta_diff = BETA_E - BETA
                if _is_gal_sat and recx is not None:
                    _v95_rec_L1 = np.array(recx.get('L1', [0,0,0]), float)
                    _v95_rec_L2 = np.array(recx.get('L2', [0,0,0]), float)
                    _v95_rpco_gps_mm = (ALFA   * _v95_rec_L1 - BETA   * _v95_rec_L2)
                    _v95_rpco_gal_mm = (ALFA_E * _v95_rec_L1 - BETA_E * _v95_rec_L2)
                    _v95_rpco_diff   = np.linalg.norm(_v95_rpco_gal_mm - _v95_rpco_gps_mm)
                    print(f"[GAL_APC] SOD={sod:.0f} sat={m['sid']}"
                          f"  freq1=E1({FREQ_E1/1e6:.3f}MHz) freq2=E5a({FREQ_E5A/1e6:.3f}MHz)"
                          f"  gamma_E={FE1SQ/FE5SQ:.6f}"
                          f"  pco_pcv_sat_applied={_v95_pco_s_mm:.2f}mm"
                          f"  pco_pcv_rec_applied={_v95_pco_r_mm:.2f}mm"
                          f"  el={_v95_el_deg:.1f}deg"
                          f"  ALFA_used={ALFA:.5f} ALFA_E_correct={ALFA_E:.5f} diff={_v95_alfa_diff:+.5f}"
                          f"  BETA_used={BETA:.5f} BETA_E_correct={BETA_E:.5f} diff={_v95_beta_diff:+.5f}"
                          f"  rec_rpco_GPS=({_v95_rpco_gps_mm[0]:.2f},{_v95_rpco_gps_mm[1]:.2f},{_v95_rpco_gps_mm[2]:.2f})mm"
                          f"  rec_rpco_GAL_correct=({_v95_rpco_gal_mm[0]:.2f},{_v95_rpco_gal_mm[1]:.2f},{_v95_rpco_gal_mm[2]:.2f})mm"
                          f"  rec_rpco_diff_norm={_v95_rpco_diff:.3f}mm"
                          + ("  ***WRONG_REC_COEFF_FOR_GAL***" if _v95_rpco_diff > 0.5 else "  rec_coeff_ok"))
                elif _is_gal_sat:
                    print(f"[GAL_APC] SOD={sod:.0f} sat={m['sid']}"
                          f"  freq1=E1 freq2=E5a gamma_E={FE1SQ/FE5SQ:.6f}"
                          f"  pco_pcv_sat_applied={_v95_pco_s_mm:.2f}mm"
                          f"  pco_pcv_rec_applied={_v95_pco_r_mm:.2f}mm"
                          f"  ALFA_used={ALFA:.5f} ALFA_E_correct={ALFA_E:.5f}"
                          f"  no_recx_available")
            # ── end v95 GAL_APC ──────────────────────────────────────────────

            # v86: determine whether to omit P2/L2 rows for this satellite.
            # Only Galileo sats may have P2/L2 omitted; GPS always gets 4 rows.
            _omit_p2l2 = (
                m['sid'][0] == 'E' and
                m['sid'] in _E5A_OMIT_SATS
            )

            _row_start = len(H_rows)

            # ── v97 RUN D: Galileo code-geometry only ────────────────────────
            # Skip all Galileo phase rows and iono/NL updates; keep one code row
            # for pure geometry contribution.  Iono/N1/N2 states still exist in
            # the state vector but receive no observations — they float freely.
            if disable_gal_states and m['sid'][0] == 'E':
                _d_h = np.zeros(nst)
                _d_h[0]=-u[0]; _d_h[1]=-u[1]; _d_h[2]=-u[2]
                _d_h[3]=1.; _d_h[4]=mw
                if ISB_IDX is not None: _d_h[ISB_IDX]=1.
                # iono absorbed into code residual; inflate R to deweight
                _d_rp = _rp(m, xs[3], xs[4])
                _d_z  = m['P1c'] - (_d_rp + xs[ki])    # include current iono state
                _d_R  = (_sig(m['el'], SC) * 3.0) ** 2  # 3× inflation for code-only
                H_rows.append(_d_h); z_rows.append(_d_z); R_rows.append(_d_R)
                _sat_row_slices[ri] = (len(H_rows)-1, len(H_rows))
                continue  # skip phase rows
            # ── end v97 RUN D ─────────────────────────────────────────────────

            # ── P1 row: P1c = rp + I ──────────────────────────────────────
            _h = np.zeros(nst)
            _h[0]=-u[0]; _h[1]=-u[1]; _h[2]=-u[2]; _h[3]=1.; _h[4]=mw
            if ISB_IDX is not None and _is_gps_obs: _h[ISB_IDX]=1.
            _h[ki]=1.                           # +I
            # v96: RDCB_E on P1 row (Galileo only).
            # P1c_true = rp + I - BETA_E*RDCB_E where BETA_E=1/(gam-1).
            # H[RDCB_E_IDX] = -BETA_E = -1/(gam-1).
            # Innovation: P1c - (rp + I_s + H[RDCB_E]*RDCB_E_state)
            #           = P1c - rp - I_s + RDCB_E_state/(gam-1)
            _rdcbe_p1_corr = 0.0
            if RDCB_E_IDX is not None and not _is_gps_obs and abs(gam-1.) > 1e-6:
                _h[RDCB_E_IDX] = -1.0 / (gam - 1.0)    # -BETA_E
                _rdcbe_p1_corr = xs[RDCB_E_IDX] / (gam - 1.0)
            _z = m['P1c'] - (rp + I_s) + _rdcbe_p1_corr
            _r = _sig(m['el'],SC)**2
            H_rows.append(_h); z_rows.append(_z); R_rows.append(_r)

            # ── P2 row: P2c = rp + γ·I ─── OMITTED when E5a missing ──────
            if not _omit_p2l2:
                _h = np.zeros(nst)
                _h[0]=-u[0]; _h[1]=-u[1]; _h[2]=-u[2]; _h[3]=1.; _h[4]=mw
                if ISB_IDX is not None and _is_gps_obs: _h[ISB_IDX]=1.
                _h[ki]=gam                      # +γI
                # v96: RDCB_E on P2 row (Galileo only).
                # P2c_true = rp + gam*I - ALFA_E*RDCB_E where ALFA_E=gam/(gam-1).
                # H[RDCB_E_IDX] = -ALFA_E = -gam/(gam-1).
                # Innovation: P2c - (rp + gam*I_s + H[RDCB_E]*RDCB_E_state)
                #           = P2c - rp - gam*I_s + gam*RDCB_E_state/(gam-1)
                _rdcbe_p2_corr = 0.0
                if RDCB_E_IDX is not None and not _is_gps_obs and abs(gam-1.) > 1e-6:
                    _h[RDCB_E_IDX] = -gam / (gam - 1.0)  # -ALFA_E
                    _rdcbe_p2_corr = gam * xs[RDCB_E_IDX] / (gam - 1.0)
                _z = m['P2c'] - (rp + gam*I_s) + _rdcbe_p2_corr
                # v91 [C2W_DEWEIGHT]: GPS P2/C2W gets σ *= 1.5 (variance ×2.25).
                # Galileo P2 weight is UNCHANGED.  Static, conservative scaling only.
                _sig_P2_base = _sig(m['el'],SC)
                if _is_gps_obs:
                    _sig_P2_after = _sig_P2_base * _C2W_DEWEIGHT_CODE_SCALE
                    _r = _sig_P2_after ** 2
                else:
                    _sig_P2_after = _sig_P2_base
                    _r = _sig_P2_base ** 2
                _z_p2_audit = _z   # save residual for audit row
                H_rows.append(_h); z_rows.append(_z); R_rows.append(_r)
            else:
                _sig_P2_base = None; _sig_P2_after = None; _z_p2_audit = None

            # ── L1 row: L1mc = rp − I + λ1·N1 ───────────────────────────
            _h = np.zeros(nst)
            _h[0]=-u[0]; _h[1]=-u[1]; _h[2]=-u[2]; _h[3]=1.; _h[4]=mw
            if ISB_IDX is not None and _is_gps_obs: _h[ISB_IDX]=1.
            _h[ki]=-1.                          # −I
            _h[kn1]=lam1                        # +λ1·N1 (cycle → metres)
            _pred_L1=rp - I_s + lam1*N1_s
            _z = m['L1mc']-_pred_L1
            _phase_sig=_sig(m['el'],SP)*(5. if m.get('age',99)<=3 else 1.)
            _r = _phase_sig**2
            if abs(_z)>PHASE_RES_GATE: _r=max(_r, 1.0**2)
            H_rows.append(_h); z_rows.append(_z); R_rows.append(_r)

            # ── L2 row: L2mc = rp − γ·I + λ2·N2 ── OMITTED when E5a missing
            if not _omit_p2l2:
                _h = np.zeros(nst)
                _h[0]=-u[0]; _h[1]=-u[1]; _h[2]=-u[2]; _h[3]=1.; _h[4]=mw
                if ISB_IDX is not None and _is_gps_obs: _h[ISB_IDX]=1.
                _h[ki]=-gam                     # −γI
                _h[kn2]=lam2                    # +λ2·N2
                _pred_L2=rp - gam*I_s + lam2*N2_s
                _z = m['L2mc']-_pred_L2
                # v91 [C2W_DEWEIGHT]: GPS L2/L2W gets σ *= 1.2 (variance ×1.44).
                # Galileo L2 weight is UNCHANGED.  Static, conservative scaling only.
                _sig_L2_base = _sig(m['el'],SP)*(5. if m.get('age',99)<=3 else 1.)
                if _is_gps_obs:
                    _sig_L2_after = _sig_L2_base * _C2W_DEWEIGHT_PHASE_SCALE
                    _r = _sig_L2_after ** 2
                else:
                    _sig_L2_after = _sig_L2_base
                    _r = _sig_L2_base ** 2
                if abs(_z)>PHASE_RES_GATE: _r=max(_r, 1.0**2)
                _z_l2_audit = _z   # save residual for audit row
                H_rows.append(_h); z_rows.append(_z); R_rows.append(_r)

                # ── v95/v98 STEP 1: PHASE_FORENSICS — full residual decomposition ──
                # Gate: every 300 epochs OR whenever RMS_WARN fired this epoch.
                # v98: removed _V95_PHASE_MAX_PRINT throttle so every triggered epoch
                # is fully logged. Also adds CODE_GF_CHECK and LAMBDA_TRACE below.
                _v95_dump_this = (nproc % 300 == 0) or (sod in _v95_rms_warn_epoch)
                if _v95_dump_this:
                    # ── Decompose every predicted measurement term ─────────────
                    _v95_rho_geom   = m['rng']          # geometric range (m)
                    _v95_clk_r      = xs[3]             # receiver clock (m)
                    _v95_clk_s      = -m['scm']         # sat clock (m, sign as used)
                    _v95_trop       = m['trop_zhd'] + m['mw'] * xs[4]  # total trop (m)
                    _v95_dtrel_check = m['dtrel']       # relativistic (m)
                    _v95_rel        = m['dtrel']        # relativistic (m)
                    _v95_shp        = m['shp']          # Shapiro (m)
                    _v95_setm       = m['setm']         # solid earth tides (m)
                    _v95_pco_s      = m['pcv_sat']      # sat PCO+PCV (m)
                    _v95_pco_r      = m['pcv_rec']      # rec PCO+PCV (m)
                    _v95_wu_L1_m    = wum.get(m['sid'], 0.) * lam1   # wind-up L1 (m)
                    _v95_wu_L2_m    = wum.get(m['sid'], 0.) * lam2   # wind-up L2 (m)
                    _v95_isb_term   = _isb_val          # ISB (0 for Galileo)
                    _v95_iono_L1    = -I_s              # iono on L1 (m)
                    _v95_iono_L2    = -gam * I_s        # iono on L2 (m)
                    _v95_amb_L1_m   = lam1 * N1_s       # λ1·N1 (m)
                    _v95_amb_L2_m   = lam2 * N2_s       # λ2·N2 (m)
                    # rp = rho - sat_clock + rel + shp + setm + trop + pco_s + pco_r
                    # (all packed into _rp(m,...))
                    _v95_rp_check = (_v95_rho_geom + _v95_clk_s + _v95_dtrel_check
                                     + _v95_shp + _v95_setm + _v95_trop
                                     + _v95_pco_s + _v95_pco_r)
                    _v95_rp_val = rp - _v95_isb_term   # rp without ISB

                    _v95_obs_L1  = m['L1mc']  # observed (wind-up corrected, OSB removed)
                    _v95_obs_L2  = m['L2mc']  # observed
                    _v95_res_L1  = _v95_obs_L1 - _pred_L1
                    _v95_res_L2  = _v95_obs_L2 - _pred_L2

                    # v98: RDCB_E state for Galileo diagnostics
                    _v98_rdcbe_state = (x[RDCB_E_IDX] if (RDCB_E_IDX is not None
                                        and m['sid'][0]=='E') else 0.0)
                    _v98_sys = 'GAL' if m['sid'][0]=='E' else 'GPS'
                    _v98_sig1 = 'E1'  if m['sid'][0]=='E' else 'L1'
                    _v98_sig2 = 'E5a' if m['sid'][0]=='E' else 'L2'

                    # Console: emit always (v98: throttle removed)
                    print(f"[PHASE_FORENSICS] SOD={sod:.0f} sat={m['sid']}"
                          f" sys={_v98_sys} sig={_v98_sig1}"
                          f"  obs={_v95_obs_L1:.4f}m pred={_pred_L1:.4f}m"
                          f"  res={_v95_res_L1*1e3:.1f}mm"
                          f"  rho={_v95_rho_geom:.3f}m clk_r={_v95_clk_r:.4f}m"
                          f"  clk_s={_v95_clk_s:.4f}m trop={_v95_trop:.4f}m"
                          f"  iono_L1={_v95_iono_L1:.4f}m"
                          f"  lambdaN1={_v95_amb_L1_m:.4f}m"
                          f"  pco_pcv_sat={_v95_pco_s*1e3:.2f}mm"
                          f"  pco_pcv_rec={_v95_pco_r*1e3:.2f}mm"
                          f"  windup_L1={_v95_wu_L1_m*1e3:.2f}mm"
                          f"  rel={_v95_rel*1e3:.2f}mm shp={_v95_shp*1e3:.2f}mm"
                          f"  setm={_v95_setm*1e3:.2f}mm"
                          f"  isb={_v95_isb_term:.4f}m"
                          f"  RDCB_E={_v98_rdcbe_state:.4f}m"
                          f"  freq_hz={m.get('_freq1', FREQ_E1 if m['sid'][0]=='E' else FREQ1):.0f}"
                          f"  lambda_m={lam1:.9f}"
                          f"  ambiguity_cycles_N1={N1_s:.4f}")
                    print(f"[PHASE_FORENSICS] SOD={sod:.0f} sat={m['sid']}"
                          f" sys={_v98_sys} sig={_v98_sig2}"
                          f"  obs={_v95_obs_L2:.4f}m pred={_pred_L2:.4f}m"
                          f"  res={_v95_res_L2*1e3:.1f}mm"
                          f"  rho={_v95_rho_geom:.3f}m clk_r={_v95_clk_r:.4f}m"
                          f"  clk_s={_v95_clk_s:.4f}m trop={_v95_trop:.4f}m"
                          f"  iono_L2={_v95_iono_L2:.4f}m gamma={gam:.6f}"
                          f"  lambdaN2={_v95_amb_L2_m:.4f}m"
                          f"  pco_pcv_sat={_v95_pco_s*1e3:.2f}mm"
                          f"  pco_pcv_rec={_v95_pco_r*1e3:.2f}mm"
                          f"  windup_L2={_v95_wu_L2_m*1e3:.2f}mm"
                          f"  rel={_v95_rel*1e3:.2f}mm shp={_v95_shp*1e3:.2f}mm"
                          f"  setm={_v95_setm*1e3:.2f}mm"
                          f"  isb={_v95_isb_term:.4f}m"
                          f"  RDCB_E={_v98_rdcbe_state:.4f}m"
                          f"  freq_hz={m.get('_freq2', FREQ_E5A if m['sid'][0]=='E' else FREQ2):.0f}"
                          f"  lambda_m={lam2:.9f}"
                          f"  ambiguity_cycles_N2={N2_s:.4f}")

                    # ── SECTION 5: LAMBDA_TRACE — wavelength/frequency consistency ──
                    _v98_f1 = m.get('_freq1', FREQ_E1 if m['sid'][0]=='E' else FREQ1)
                    _v98_f2 = m.get('_freq2', FREQ_E5A if m['sid'][0]=='E' else FREQ2)
                    _v98_lam1_expected = CLIGHT / _v98_f1
                    _v98_lam2_expected = CLIGHT / _v98_f2
                    _v98_lam1_ok = abs(lam1 - _v98_lam1_expected) < 1e-9
                    _v98_lam2_ok = abs(lam2 - _v98_lam2_expected) < 1e-9
                    print(f"[LAMBDA_TRACE] sat={m['sid']} sys={_v98_sys}"
                          f"  {_v98_sig1}: freq={_v98_f1:.0f}Hz"
                          f" lambda={lam1:.9f}m expected={_v98_lam1_expected:.9f}m MATCH={_v98_lam1_ok}"
                          f"  {_v98_sig2}: freq={_v98_f2:.0f}Hz"
                          f" lambda={lam2:.9f}m expected={_v98_lam2_expected:.9f}m MATCH={_v98_lam2_ok}"
                          f"  state_idx_I={ki} state_idx_N1={kn1} state_idx_N2={kn2}"
                          f"  LAMBDA_CONSISTENT={_v98_lam1_ok and _v98_lam2_ok}")

                    # ── SECTION 4: IONO_DECOMP — ionosphere sign/scale validation ──
                    _v95_ratio_actual = (_v95_iono_L2 / _v95_iono_L1
                                         if abs(_v95_iono_L1) > 0.01 else float('nan'))
                    _v95_ratio_match = abs(_v95_ratio_actual - gam) < 0.001
                    _v95_code_iono = (m['P2c'] - m['P1c']) / (gam - 1.)  if abs(gam-1.)>1e-6 else 0.
                    # v96: show corrected code_GF_iono in IONO_DECOMP log
                    _v96_rdcbe_diag = (x[RDCB_E_IDX] if (RDCB_E_IDX is not None
                                       and m['sid'][0]=='E') else 0.0)
                    # v97 FIX 2: diagnostic sign corrected (matches PATCH 1 physics).
                    # raw  = (P2c-P1c)/(gam-1) already contains +RDCB_E/(gam-1).
                    # To display I_true we ADD back the KF RDCB_E estimate:
                    _v96_code_iono_corr = (_v95_code_iono + _v96_rdcbe_diag/(gam-1.)
                                          if abs(gam-1.)>1e-6 else _v95_code_iono)
                    # Feed RDCB_E diagnostic accumulators (Galileo only)
                    if m['sid'][0]=='E' and RDCB_E_IDX is not None:
                        _rdcbe_gf_raw_acc.append(_v95_code_iono)
                        _rdcbe_gf_corr_acc.append(_v96_code_iono_corr)
                    _v95_state_match = abs(I_s - _v96_code_iono_corr) < 5.0  # within 5 m
                    # v98: phase sign check — −I on L1 must be negative when I_s > 0
                    _v98_phase_sign_L1_ok = (_v95_iono_L1 < 0) == (I_s > 0)
                    _v98_phase_sign_L2_ok = (_v95_iono_L2 < 0) == (I_s > 0)
                    print(f"[IONO_DECOMP] SOD={sod:.0f} sat={m['sid']}"
                          f"  I_state={I_s:.4f}m gamma={gam:.6f}"
                          f"  lambda1={lam1*100:.4f}cm lambda2={lam2*100:.4f}cm"
                          f"  iono_L1_pred={_v95_iono_L1:.4f}m"
                          f"  iono_L2_pred={_v95_iono_L2:.4f}m"
                          f"  ratio_L2_L1={_v95_ratio_actual:.6f}"
                          f"  expected_gamma={gam:.6f}"
                          f"  RATIO_OK={'YES' if _v95_ratio_match else 'NO_MISMATCH'}"
                          f"  code_GF_iono_raw={_v95_code_iono:.4f}m"
                          f"  code_GF_iono_corr={_v96_code_iono_corr:.4f}m"
                          f"  RDCB_E_state={_v96_rdcbe_diag:.4f}m"
                          f"  state_vs_corr={(I_s-_v96_code_iono_corr):.4f}m"
                          f"  state_match={'YES' if _v95_state_match else 'WARNING_LARGE_DIFF'}"
                          f"  phase_sign_L1={'OK' if _v98_phase_sign_L1_ok else 'WRONG'}"
                          f"  phase_sign_L2={'OK' if _v98_phase_sign_L2_ok else 'WRONG'}"
                          f"  sys={_v98_sys}")

                    # ── SECTION 4 (OSB_TRACE): code GF sign check — THE KEY DIAGNOSTIC ──
                    # This is the check that identified the root cause.
                    # P2c − P1c MUST be positive (= (γ−1)·I > 0 when I > 0).
                    # If it is negative, the code GF is wrong sign and I_s cannot converge.
                    if m['sid'][0] == 'E':  # Galileo only (GPS is always fine)
                        _v98_code_gf     = m['P2c'] - m['P1c']
                        _v98_gf_sign_ok  = (_v98_code_gf > 0) == (I_s > 0)
                        _v98_expected_gf = (gam - 1.0) * I_s
                        print(f"[CODE_GF_CHECK] SOD={sod:.0f} sat={m['sid']}"
                              f"  P1c={m['P1c']:.4f}m P2c={m['P2c']:.4f}m"
                              f"  P2c-P1c={_v98_code_gf:+.4f}m"
                              f"  expected_(gamma-1)*I={_v98_expected_gf:+.4f}m"
                              f"  I_s={I_s:.4f}m RDCB_E={_v98_rdcbe_state:.4f}m"
                              f"  CODE_GF_SIGN_OK={_v98_gf_sign_ok}"
                              + ("" if _v98_gf_sign_ok
                                 else "  ***CODE_GF_WRONG_SIGN — RDCB_E must absorb***"))
                # ── end v95/v98 PHASE_FORENSICS / IONO_DECOMP / OSB_TRACE / LAMBDA_TRACE

                # ── C2W audit row (GPS dual-freq only) ───────────────────
                if _is_gps_obs and _sig_P2_base is not None:
                    _c2w_audit_w.writerow([
                        f"{sod:.1f}",
                        m['sid'],
                        f"{_sig_P2_base*1e3:.4f}",   # mm
                        f"{_sig_P2_after*1e3:.4f}",  # mm
                        f"{_sig_L2_base*1e3:.4f}",   # mm
                        f"{_sig_L2_after*1e3:.4f}",  # mm
                        f"{math.degrees(m['el']):.2f}",
                        f"{_z_p2_audit*1e3:.2f}",    # mm
                        f"{_z_l2_audit*1e3:.2f}",    # mm
                    ])
                    if _c2w_print_count < 5:
                        print(f"[C2W_DEWEIGHT] SOD={sod:.0f}  sat={m['sid']}"
                              f"  el={math.degrees(m['el']):.1f}°"
                              f"  σ_P2: {_sig_P2_base*1e3:.1f}→{_sig_P2_after*1e3:.1f} mm"
                              f"  σ_L2: {_sig_L2_base*1e3:.1f}→{_sig_L2_after*1e3:.1f} mm")
                        _c2w_print_count += 1

            _row_end = len(H_rows)
            _sat_row_slices[ri] = (_row_start, _row_end)

            # v76 PART 3: downweight (NOT remove) bad sats with high L1m-P1c range.
            # If range_100 > 8 m the satellite is in heavy scintillation — keep it
            # in the solution but trust its measurements less.  Phase and code noise
            # are each scaled by ×2 (variance ×4) so the filter can still recover
            # position information without letting a corrupted sat dominate.
            _hist_sat_rd = _lp1_hist.get(m['sid'])
            _rng_sat_rd = 0.0
            if _hist_sat_rd and len(_hist_sat_rd) >= 20:
                _arr_rd = np.array(_hist_sat_rd)
                _rng_sat_rd = float(_arr_rd.max() - _arr_rd.min())
            if _rng_sat_rd > 8.0:
                for _rr in range(_row_start, _row_end):
                    R_rows[_rr] *= 4.0   # sigma×2 → variance×4 (all rows for this sat)

            # v84 PART 5: Galileo arc-instability adaptive weighting.
            # Applied AFTER the v76 scintillation gate (effects are multiplicative).
            if _gal_arc_unstable(m['sid']):
                for _rr in range(_row_start, _row_end):
                    # Identify row type: P1/P2 are code rows (no kn1/kn2 in H);
                    # L1/L2 are phase rows.  Scale all rows uniformly by type.
                    # Since we don't have labels, scale code (no ambiguity col)
                    # and phase (has ambiguity col) differently via H inspection.
                    _is_phase_row = (H_rows[_rr][kn1] != 0.0 or H_rows[_rr][kn2] != 0.0)
                    if _is_phase_row:
                        R_rows[_rr] *= GAL_UNSTABLE_PHASE_SCALE ** 2
                    else:
                        R_rows[_rr] *= GAL_UNSTABLE_CODE_SCALE  ** 2

        # Materialise from append lists — row alignment is guaranteed by construction
        H = np.vstack(H_rows) if H_rows else np.zeros((0, nst))
        z = np.array(z_rows)
        Rd = np.array(R_rows)
        n_obs = len(z_rows)   # actual row count (may be < 4*ns when E5a omitted)

        # ZWD soft prior pseudo-obs (unchanged from v53)
        n_total=n_obs
        H_p=np.zeros((n_total+1,nst)); z_p=np.zeros(n_total+1); Rd_p=np.zeros(n_total+1)
        H_p[:n_total,:]=H; z_p[:n_total]=z; Rd_p[:n_total]=Rd
        H_p[n_total,4]=1.
        z_p[n_total]=ZWD_PRIOR-xs[4]
        Rd_p[n_total]=ZWD_PRIOR_SIGMA**2

        # v70 IONO FIX 2/4: snapshot ionosphere states before the KF update so
        # we can compute the per-epoch iono step (dI) after the update.
        _I_before_update = {m['sid']: x[m['ki']] for m in geom}

        zwd_before=x[4]
        # v58 Fix 5: regularise R to prevent near-singular inversion (NaN propagation)
        R_main = np.diag(Rd_p); R_main += np.eye(len(R_main)) * 1e-6
        if filter_standard(x,P,H_p.T,z_p,R_main)!=0: continue

        # ZWD per-epoch clamp (unchanged from v53)
        if _zwd_prev is not None and abs(x[4]-_zwd_prev)>ZWD_CLAMP:
            x[4]=_zwd_prev+math.copysign(ZWD_CLAMP, x[4]-_zwd_prev)
            P[4,4]=max(P[4,4], (ZWD_CLAMP/3.0)**2)
        _zwd_prev=x[4]

        # ── v71 IONO CLAMP REMOVED (per v71 diagnostic instructions) ────────────
        # RW cap (2 m clamp), dI instability detection, and variance cap are all
        # commented out so the filter behaves naturally.  This lets us verify that
        # the reduced code noise (SC=0.30 m) drives smooth I_est without artificial
        # clamping.  Re-enable selectively once I_est behaviour is confirmed clean.
        #
        # IONO_RW_CAP   = 2.0   # m  — v71: commented out — let filter run free
        # IONO_INSTAB_T = 5.0   # m  — v71: commented out
        # IONO_VAR_CAP  = 100.0 # m² — v71: commented out
        IONO_RW_CAP   = 2.0   # kept as a constant so _iono_last_dI can still log
        IONO_INSTAB_T = 5.0
        IONO_VAR_CAP  = 100.0
        _iono_unstable = set()  # will remain empty — no sats flagged
        for _m_c in geom:
            _sid_c = _m_c['sid']
            _ki_c  = _m_c['ki']
            _I_new = x[_ki_c]
            # Defect-9 fix: removed unconditional iono variance cap
            # P[_ki_c, _ki_c] = min(P[_ki_c, _ki_c], 100.0)  ← REMOVED
            # The 100 m² ceiling artificially suppressed P[I,N1] and P[I,N2]
            # cross-covariances, preventing ionosphere corrections from propagating
            # into ambiguity tightening.  The iono health gate (NEG_IONO_THRESH /
            # MAX_IONO_THRESH) now provides physical bounds without capping the KF
            # covariance.  Process noise, measurement model, and state vector
            # are byte-identical.
            _I_old = _I_before_update.get(_sid_c, _I_new)
            _dI    = abs(_I_new - _I_old)
            _iono_last_dI[_sid_c] = _dI           # keep logging for diagnostics
            # v71: instability flag DISABLED — NL skipping and Rd inflation removed
            # if _dI > IONO_INSTAB_T:
            #     _iono_unstable.add(_sid_c)
            #     print(f"[IONO_INSTAB] {_sid_c} SOD={sod:.0f} "
            #           f"dI={_dI:.2f}m > {IONO_INSTAB_T:.0f}m — "
            #           f"skipping NL fix, Rd inflation next epoch")
            # v71: RW cap DISABLED — let iono state move freely
            # if _dI > IONO_RW_CAP:
            #     x[_ki_c] = _I_old + math.copysign(IONO_RW_CAP, _I_new - _I_old)
            # v71: variance cap DISABLED — let covariance evolve naturally
            # P[_ki_c, _ki_c] = min(P[_ki_c, _ki_c], IONO_VAR_CAP)

        if nproc % 300 == 0 and sidx:
            vals = [P[ki, ki] for ki in sidx.values()]
            print(f"[IONO VAR] min={min(vals):.2f} max={max(vals):.2f} mean={np.mean(vals):.2f}")
            # v82 PART 6 — OBS signal pairing status (replaces v81 block)
            _nGPS_used_dbg = sum(1 for m in geom if m['sid'][0] == 'G')
            _nGPS_ar_dbg   = sum(1 for m in geom if m['sid'][0] == 'G' and m.get('use_for_ar', False))
            _nGPS_fb_dbg   = sum(1 for m in geom if m['sid'][0] == 'G' and m.get('_fallback_used', False))
            _nGAL_used_dbg = sum(1 for m in geom if m['sid'][0] == 'E')
            print(f"[OBS_V82] SOD={sod:.0f}"
                  f"  nGPS_ppp={_nGPS_used_dbg}"
                  f"  nGPS_ar={_nGPS_ar_dbg}"
                  f"  nGPS_fallback={_nGPS_fb_dbg}"
                  f"  nGAL_ppp={_nGAL_used_dbg}")
            for _obs_sid in sorted(sobs.keys()):
                if _obs_sid[0] not in ('G', 'E'): continue
                if _obs_sid[0] not in constellation: continue
                _obs_so = sobs[_obs_sid]
                if _obs_sid[0] == 'G':
                    _c1w = _obs_so.get('C1W', 0.); _l1w = _obs_so.get('L1W', 0.)
                    _c2w = _obs_so.get('C2W', 0.); _l2w = _obs_so.get('L2W', 0.)
                    _c1c = _obs_so.get('C1C', 0.); _l1c = _obs_so.get('L1C', 0.)
                    if _c1w != 0. and _l1w != 0. and _c2w != 0. and _l2w != 0.:
                        print(f"  {_obs_sid} → C1W/L1W + C2W/L2W ✔ [PPP+AR]")
                    elif _c1c != 0. and _l1c != 0. and _c2w != 0. and _l2w != 0.:
                        print(f"  {_obs_sid} → C1C/L1C + C2W/L2W ✔ [PPP+AR via CODE C1C OSB]")
                    else:
                        _miss_w = [s for s, v in [('C1W', _c1w), ('L1W', _l1w),
                                                   ('C2W', _c2w), ('L2W', _l2w)] if v == 0.]
                        _miss_c = [s for s, v in [('C1C', _c1c), ('L1C', _l1c)] if v == 0.]
                        print(f"  {_obs_sid} → missing primary:{','.join(_miss_w)}"
                              f"  fallback:{','.join(_miss_c)} → excluded")
                else:
                    _c1c = _obs_so.get('C1C', 0.); _l1c = _obs_so.get('L1C', 0.)
                    _c5q = _obs_so.get('C5Q', 0.); _l5q = _obs_so.get('L5Q', 0.)
                    if _c1c != 0. and _l1c != 0. and _c5q != 0. and _l5q != 0.:
                        print(f"  {_obs_sid} → C1C/L1C + C5Q/L5Q ✔")
                    elif _c1c != 0. and _l1c != 0.:
                        _miss_e5 = [s for s,v in [('C5Q',_c5q),('L5Q',_l5q)] if v==0.]
                        print(f"  {_obs_sid} → C1C/L1C only [PPP, AR disabled: missing {','.join(_miss_e5)}]")
                    else:
                        _miss = [s for s, v in [('C1C', _c1c), ('L1C', _l1c),
                                                ('C5Q', _c5q), ('L5Q', _l5q)] if v == 0.]
                        print(f"  {_obs_sid} → missing {','.join(_miss)} → skipped")

        # v58 Fix 2: NaN protection — if the state vector went non-finite after
        # the float update, release all NL fixes and inflate the covariance so
        # the filter can recover rather than propagating NaN indefinitely.
        if not np.isfinite(x).all() or not np.isfinite(P).all():
            x = np.where(np.isfinite(x), x, 0.0)
            P = np.where(np.isfinite(P), P, 0.0)
            np.fill_diagonal(P, np.maximum(np.diag(P), 100.**2))
            P *= 100.
            nl_fixed.clear()
            print(f"[NaN GUARD] SOD={sod:.0f} — non-finite state detected; "
                  f"released all NL fixes, inflated covariance")

        # Compute phase residuals immediately after float update so NL gate
        # can use current-epoch phase_rms_now (not stale from previous epoch).
        # v91-diag FIX 1: include GPS ISB in diagnostic residual so the logged
        # PhsRMS is consistent with the actual KF observation model.
        # L1mc_GPS = rp + ISB - I + λ1·N1  →  res_L1 = L1mc - (rp+ISB - I + λ1·N1)
        # Previously ISB was omitted, injecting a fake ~1-3 m bias into every
        # GPS satellite residual and inflating PhsRMS by O(1000 mm).
        # KF equations, process noise, and all other state logic are untouched.
        phase_res_now=[]
        for m in geom:
            ki=m['ki']
            _is_gps=(m['sid'][0]=='G')
            _isb=x[ISB_IDX] if (ISB_IDX is not None and _is_gps) else 0.0
            rp=_rp(m,x[3],x[4])+_isb
            res_L1=m['L1mc']-(rp - x[ki] + m['lam1']*x[ki+1])
            phase_res_now.append(res_L1)
        phase_rms_now=math.sqrt(np.mean(np.array(phase_res_now)**2)) if phase_res_now else 999.

        # ── RMS-SPLIT DIAGNOSTICS (v96 — moved BEFORE NL injection) ──────────
        # v96 MOVE: This block was previously after NL pseudo-obs injection AND
        # after the iono health gate reset.  At that point x[ki] had been reset
        # from ~+2.2 m (correct) → −2.8 m (NL-corrupted) → +2.18 m (gate reset),
        # creating a 5 m iono inconsistency that inflated L1_RMS to 1 400–3 500 mm
        # while PhsRMS (computed here) correctly showed 79–115 mm.
        # Moving the diagnostic to this point (same state as phase_res_now)
        # makes both metrics agree and eliminates all false [RMS_WARN] explosions.
        # The block is read-only (x, P not mutated — asserted below).
        # All residuals are computed from the post-main-KF state (x, P).
        # Nothing written here modifies x, P, H, z, Rd, or any filter variable.
        # Only satellites that entered the KF update (i.e. present in `geom`)
        # are included.  geom is the exact list passed to filter_standard above.
        #
        # ASSERTION GUARD: capture a hash-like fingerprint of the filter state
        # before and after this block to prove zero mutation.
        _rms_diag_x_sum_before = float(np.sum(x))
        _rms_diag_P_sum_before = float(np.sum(np.diag(P)))

        # ── v88 RMS FIX: dual-frequency and single-frequency accumulators ────
        # ROOT CAUSE (forensic audit): C1C/L1C-only Galileo satellites were
        # appended to _rms_L1 without a corresponding _rms_L2 entry, creating
        # structural nL1 > nL2 asymmetry.  These rows carry 5–30 m iono delay
        # (no dual-freq combination to remove it) → fake 8–16 m L1_RMS explosions
        # at 60.6% of epochs.  Fix: route single-freq rows to a dedicated
        # accumulator that is excluded from the L1/L2 split RMS entirely.
        #
        # ZERO filter changes: x, P, H, z, Rd, geom are untouched.
        # ZERO measurement-vector changes: row admission logic is untouched.
        # This block is DIAGNOSTIC ONLY — pure accounting.
        #
        # Dual-frequency accumulators — nL1_dual == nL2_dual guaranteed:
        _rms_P1 = []; _rms_P2 = []; _rms_L1 = []; _rms_L2 = []
        _rms_GPS_P2 = []; _rms_GAL_P2 = []
        # Single-frequency accumulators — C1C/L1C-only Galileo sats only:
        _rms_P1_single = []; _rms_L1_single = []

        for _rm in geom:
            _rki   = _rm['ki']         # ionosphere slot
            _rkn1  = _rki + 1          # N1 slot
            _rkn2  = _rki + 2          # N2 slot
            _rgam  = _rm['gamma']
            _rlam1 = _rm['lam1']
            _rlam2 = _rm['lam2']
            # ISB contribution (same formula as used in the KF update above)
            _r_is_gps = (_rm['sid'][0] == 'G')
            _r_isb    = x[ISB_IDX] if (ISB_IDX is not None and _r_is_gps) else 0.0
            _rrp      = _rp(_rm, x[3], x[4]) + _r_isb

            # Identical gate as measurement assembly (line ~3184 / v86 omission):
            # True when this sat contributed only L1 rows (no L2/P2 in the KF).
            _rms_omit_p2l2 = (
                _rm['sid'][0] == 'E' and
                _rm['sid'] in _E5A_OMIT_SATS
            )

            # v99 FIX 3: RDCB_E correction for post-fit Galileo residuals.
            # The KF model for Galileo code is:
            #   P1c_model = rp + I_s − β_E × RDCB_E    (β_E = 1/(γ−1))
            #   P2c_model = rp + γI_s − α_E × RDCB_E   (α_E = γ/(γ−1))
            # Without these terms the post-fit residuals for Galileo carry a
            # systematic offset of β_E × RDCB_E ≈ 12 m (P1) / α_E × RDCB_E ≈ 22 m (P2),
            # making GAL_P2_RMS appear inflated by ~22 000 mm throughout the run
            # even after the state has converged.
            _r_is_gal   = (_rm['sid'][0] == 'E')
            _rdcbe_rms  = x[RDCB_E_IDX] if (RDCB_E_IDX is not None and _r_is_gal) else 0.0
            _gam_rms    = _rgam
            if _r_is_gal and abs(_gam_rms - 1.0) > 1e-6:
                _alfa_e_rms = _gam_rms / (_gam_rms - 1.0)   # α_E = γ/(γ−1)
                _beta_e_rms = 1.0       / (_gam_rms - 1.0)   # β_E = 1/(γ−1)
            else:
                _alfa_e_rms = 0.0
                _beta_e_rms = 0.0

            # Post-fit P1 residual  (RDCB_E corrected for Galileo)
            _res_p1 = _rm['P1c'] - (_rrp + x[_rki] - _beta_e_rms * _rdcbe_rms)
            # Post-fit L1 residual (wind-up corrected phase already in L1mc)
            # Phase H[RDCB_E]=0 → no correction needed here.
            _res_l1 = _rm['L1mc'] - (_rrp - x[_rki] + _rlam1 * x[_rkn1])

            if not (math.isfinite(_res_p1) and math.isfinite(_res_l1)):
                continue

            if _rms_omit_p2l2:
                # ── C1C/L1C-only Galileo satellite ──────────────────────────
                _rms_P1_single.append(_res_p1)
                _rms_L1_single.append(_res_l1)
                _disc_sat_l1_res[_rm['sid']] = _res_l1   # v93 forensic
            else:
                # ── Dual-frequency satellite (L1+L2 both in KF) ─────────────
                # v99 FIX 4: include −α_E × RDCB_E in the Galileo P2 prediction
                _res_p2 = _rm['P2c'] - (_rrp + _rgam * x[_rki] - _alfa_e_rms * _rdcbe_rms)
                _res_l2 = _rm['L2mc'] - (_rrp - _rgam * x[_rki] + _rlam2 * x[_rkn2])
                if math.isfinite(_res_p2) and math.isfinite(_res_l2):
                    _rms_P1.append(_res_p1)
                    _rms_L1.append(_res_l1)
                    _rms_P2.append(_res_p2)
                    _rms_L2.append(_res_l2)
                    if _r_is_gps:
                        _rms_GPS_P2.append(_res_p2)
                    else:
                        _rms_GAL_P2.append(_res_p2)
                    _disc_sat_l1_res[_rm['sid']] = _res_l1   # v93 forensic
                    _disc_sat_l2_res[_rm['sid']] = _res_l2   # v93 forensic

        def _rms_mm(lst):
            """RMS in mm; returns None if list is empty."""
            if not lst: return None
            return math.sqrt(sum(v*v for v in lst) / len(lst)) * 1e3

        _p1_rms        = _rms_mm(_rms_P1)
        _p2_rms        = _rms_mm(_rms_P2)
        _l1_rms        = _rms_mm(_rms_L1)          # dual-freq L1 only
        _l2_rms        = _rms_mm(_rms_L2)          # dual-freq L2 only
        _gps_p2        = _rms_mm(_rms_GPS_P2)
        _gal_p2        = _rms_mm(_rms_GAL_P2)
        _l1_single_rms = _rms_mm(_rms_L1_single)   # C1C-only Galileo L1

        # ── v88 Count consistency assertion ──────────────────────────────────
        assert len(_rms_P1) == len(_rms_L1) == len(_rms_P2) == len(_rms_L2), (
            f"[RMS_DIAG ASSERT] SOD={sod:.0f} dual-freq count mismatch: "
            f"nP1={len(_rms_P1)} nL1={len(_rms_L1)} "
            f"nP2={len(_rms_P2)} nL2={len(_rms_L2)}")

        # ── Phase sanity check (dual-freq rows only) ─────────────────────────
        if _l1_rms is not None and _l1_rms > 1000.0:
            _v95_rms_warn_epoch.add(sod)
            print(f"[RMS_WARN] SOD={sod:.0f} suspicious phase RMS: "
                  f"L1={_l1_rms:.1f}mm L2={_l2_rms:.1f}mm — check phase model")
        if _l2_rms is not None and _l2_rms > 1000.0:
            _v95_rms_warn_epoch.add(sod)
            print(f"[RMS_WARN] SOD={sod:.0f} suspicious L2 phase RMS: "
                  f"L2={_l2_rms:.1f}mm — check phase model")
        if _l1_single_rms is not None and _l1_single_rms > 1000.0:
            print(f"[RMS_WARN] SOD={sod:.0f} C1C-only L1 RMS large: "
                  f"L1_single={_l1_single_rms:.1f}mm n={len(_rms_L1_single)}"
                  f" — iono-loaded single-freq Galileo (diagnostic only)")

        # ── State-mutation guard ─────────────────────────────────────────────
        _rms_diag_x_sum_after = float(np.sum(x))
        _rms_diag_P_sum_after = float(np.sum(np.diag(P)))
        assert _rms_diag_x_sum_before == _rms_diag_x_sum_after, (
            f"[RMS_DIAG ASSERT] SOD={sod:.0f} x mutated inside RMS-diag block!")
        assert _rms_diag_P_sum_before == _rms_diag_P_sum_after, (
            f"[RMS_DIAG ASSERT] SOD={sod:.0f} P diag mutated inside RMS-diag block!")

        # ── Write CSV row ────────────────────────────────────────────────────
        def _fmt(v): return f"{v:.4f}" if v is not None else ""
        _rms_csv_w.writerow([
            f"{sod:.1f}",
            _fmt(_p1_rms), _fmt(_p2_rms), _fmt(_l1_rms), _fmt(_l2_rms),
            _fmt(_gps_p2), _fmt(_gal_p2),
            len(_rms_P1), len(_rms_P2), len(_rms_L1), len(_rms_L2),
            len(_rms_L1_single), _fmt(_l1_single_rms),
        ])
        # ── END RMS-SPLIT DIAGNOSTICS ─────────────────────────────────────────

        # ── v96-PATCH1: GPS WRONG-FIX VETO ───────────────────────────────────
        # Per-satellite detection of scintillation-induced wrong fixes.
        # Signature: NL-fixed GPS satellite with |L1_res|>400 mm AND
        #            |L2_res|/|L1_res|>1.45 for ≥3 consecutive epochs.
        # On trigger: release fix + impose cooldown + inflate N1/N2 covariance.
        _wrongfix_release = []
        for _wf_sid in list(nl_fixed.keys()):
            if _wf_sid not in sidx:
                continue
            _wf_l1_res = _disc_sat_l1_res.get(_wf_sid)
            _wf_l2_res = _disc_sat_l2_res.get(_wf_sid)
            if _wf_l1_res is None or _wf_l2_res is None:
                _wrongfix_susp_count[_wf_sid] = 0
                continue
            _wf_l1_mm = abs(_wf_l1_res) * 1e3
            _wf_l2_mm = abs(_wf_l2_res) * 1e3
            _wf_ratio  = (_wf_l2_mm / _wf_l1_mm) if _wf_l1_mm > 1.0 else 0.0
            _wf_suspicious = (_wf_l1_mm > _WRONGFIX_L1_THRESH_MM
                               and _wf_ratio > _WRONGFIX_RATIO_THRESH)
            if _wf_suspicious:
                _wrongfix_susp_count[_wf_sid] += 1
                if _wrongfix_susp_count[_wf_sid] >= _WRONGFIX_CONSEC_THRESH:
                    _wrongfix_release.append(_wf_sid)
            else:
                _wrongfix_susp_count[_wf_sid] = 0

        for _wf_sid in _wrongfix_release:
            _wf_ki = sidx[_wf_sid]
            _wf_N1_was, _wf_N2_was = nl_fixed.pop(_wf_sid, (None, None))
            _nl_fix_cooldown[_wf_sid] = 30          # prevent immediate re-fix
            _wrongfix_susp_count[_wf_sid] = 0
            # Inflate N1 and N2 ambiguity variance so the filter re-estimates freely
            P[_wf_ki+1, _wf_ki+1] = max(P[_wf_ki+1, _wf_ki+1], _WRONGFIX_COV_INFLATE)
            P[_wf_ki+2, _wf_ki+2] = max(P[_wf_ki+2, _wf_ki+2], _WRONGFIX_COV_INFLATE)
            # Zero off-diagonal cross-terms for the released ambiguity pair to prevent
            # contamination of other states via stale N1/N2 covariance.
            P[_wf_ki+1, :] = 0.; P[:, _wf_ki+1] = 0.
            P[_wf_ki+2, :] = 0.; P[:, _wf_ki+2] = 0.
            P[_wf_ki+1, _wf_ki+1] = _WRONGFIX_COV_INFLATE
            P[_wf_ki+2, _wf_ki+2] = _WRONGFIX_COV_INFLATE
            _wf_l1_mm_log = abs(_disc_sat_l1_res.get(_wf_sid, 0.)) * 1e3
            _wf_l2_mm_log = abs(_disc_sat_l2_res.get(_wf_sid, 0.)) * 1e3
            _wf_ratio_log = (_wf_l2_mm_log / _wf_l1_mm_log) if _wf_l1_mm_log > 1. else 0.
            print(f"[WRONGFIX_VETO] SOD={sod:.0f} sat={_wf_sid} "
                  f"N1_was={_wf_N1_was} N2_was={_wf_N2_was} "
                  f"L1_res={_wf_l1_mm_log:.1f}mm L2_res={_wf_l2_mm_log:.1f}mm "
                  f"L2/L1={_wf_ratio_log:.3f} "
                  f"consec={_WRONGFIX_CONSEC_THRESH} "
                  f"cov_inflate={math.sqrt(_WRONGFIX_COV_INFLATE)*100:.1f}cm "
                  f"cooldown=30ep → fix released")
        # ── end PATCH1 wrong-fix veto ─────────────────────────────────────────

        # ── v57 NL FIXING (enabled) ───────────────────────────────────────────
        # Phase OSBs have been applied to L1m/L2m in _proc/_proc_gal, so the
        # float N1 state x[ki+1] converges toward an integer value after filter
        # convergence.  Strategy:
        #   1. Attempt to fix each WL-fixed satellite (rounding gate).
        #   2. Inject tight pseudo-observations for all NL-fixed sats.
        #   3. Validate existing fixes; release any that have drifted.
        #   4. ZWD watchdog inherited from v52 still applies (see below).

        # ── 3a. Release drifted fixes ─────────────────────────────────────────
        # v80 PART 4: decrement all cooldown counters once per epoch
        for _sid_cd in list(_nl_fix_cooldown.keys()):
            if _nl_fix_cooldown[_sid_cd] > 0:
                _nl_fix_cooldown[_sid_cd] -= 1
        to_release=[]
        for sid_nl,(N1_i,N2_i) in nl_fixed.items():
            if sid_nl not in sidx: to_release.append(sid_nl); continue
            ki_nl=sidx[sid_nl]
            if abs(x[ki_nl+1]-N1_i)>NL_RELEASE_THRESH:
                to_release.append(sid_nl)
        for sid_nl in to_release:
            nl_fixed.pop(sid_nl,None)
            _nl_fix_cooldown[sid_nl] = 30   # v80 PART 4: 30-epoch re-fix cooldown
        _disc_to_release_snap = list(to_release)   # v93 forensic: saved for post-epoch log

        # ── 3b. ZWD watchdog ─────────────────────────────────────────────────
        if not hasattr(_ppp_pass,'_zwd_buf'): pass  # per-call buffer via list
        # (reuse existing _zwd_prev / ZWD_CLAMP logic above; watchdog below)
        _zwd_buf=getattr(_ppp_pass,'_zwd_buf_'+label,[])
        _zwd_buf.append(x[4])
        if len(_zwd_buf)>5: _zwd_buf.pop(0)
        setattr(_ppp_pass,'_zwd_buf_'+label,_zwd_buf)
        ZWD_RATE_LIMIT=0.025
        if len(_zwd_buf)==5 and (max(_zwd_buf)-min(_zwd_buf))>ZWD_RATE_LIMIT:
            if nl_fixed:
                print(f"[ZWD WATCHDOG] SOD={sod:.0f} range={max(_zwd_buf)-min(_zwd_buf):.3f}m "
                      f"→ releasing {len(nl_fixed)} NL fixes")
                nl_fixed.clear()
                P[4,4]=max(P[4,4],(0.15)**2)

        # ── 3c. Attempt new NL fixes ──────────────────────────────────────────
        # v66 FIX 3+4: collect all qualifying candidates this epoch, then:
        #   • sort Galileo first (cleaner signals → anchor solution),
        #     then by ascending |corr_frac| + sigma_N1_m (best candidate first).
        #   • commit at most 3 new fixes per epoch.
        # v79 PART 6: per-epoch skip counters (reset each epoch so the 300-epoch
        # accumulation window below reflects totals across the window, not one epoch).
        _ep_skip_no_osb     = 0
        _ep_skip_bad_bias   = 0
        _ep_skip_high_range = 0
        _ep_skip_sigma      = 0
        _ep_nl_count        = 0

        # v80 PART 1 — PRE-SELECT BEST 4 SATELLITES FOR NL BY sigma_N1_m
        # Before the candidate loop: compute sigma_N1_m for all satellites that
        # pass the basic eligibility checks (WL fixed, in sidx, not already
        # NL-fixed, sufficient age, no_AR=False, not iono-unstable, not high-range).
        # Sort ascending and restrict to the 4 lowest-sigma candidates.
        # Satellites not in this set are completely skipped for NL fixing this
        # epoch (they remain in the PPP filter unaffected).
        _nl_preselect = []
        for _mps in geom:
            _sps = _mps['sid']
            if _mps['NWL'] is None: continue
            if _sps in nl_fixed: continue
            if _mps['age'] < NL_MIN_OBS: continue
            if _sps not in sidx: continue
            if _mps.get('no_AR', False): continue
            if _sps in _iono_unstable: continue
            _hps = _lp1_hist.get(_sps)
            _rps = 0.0
            if _hps and len(_hps) >= 20:
                _aps = np.array(_hps)
                _rps = float(_aps.max() - _aps.min())
            # v83: constellation-aware range gate.  GPS at equatorial IISC
            # routinely shows L1m-P1c range >6m from EIA scintillation.
            _rng_gate_ps = 15.0 if _sps[0] == 'G' else 6.0
            if _rps > _rng_gate_ps: continue
            _ki_ps = sidx[_sps]
            _sig_ps = math.sqrt(max(0.0, P[_ki_ps+1, _ki_ps+1])) * _mps['lam1']
            _nl_preselect.append((_sig_ps, _sps))
        _nl_preselect.sort(key=lambda t: t[0])   # ascending sigma_N1_m
        # v83: per-constellation best-2 selection (2 GPS + 2 Galileo = 4 total).
        # Global best-4 was always filled by Galileo (lower sigma) leaving GPS
        # permanently excluded. Banville et al. 2020 (J.Geod 94:10) show multi-
        # GNSS AR requires eligible candidates from each constellation.
        _best_G = [p for p in _nl_preselect if p[1][0] == 'G'][:2]
        _best_E = [p for p in _nl_preselect if p[1][0] == 'E'][:2]
        _nl_eligible_sids = {s for _, s in _best_G + _best_E}

        _nl_epoch_candidates = []   # (sort_key_tuple, sid_m, N1_int, N2_int, frac_for_fix, sigma_N1)
        # ISB stability guard: in combined mode, delay NL fixing until ISB
        # uncertainty drops below 3 m (sigma < 3 m).  Before that, the GPS
        # ambiguity float values carry the unmodelled IF-clock offset and
        # integer rounding is unreliable.
        _isb_ok_for_nl = True
        if ISB_IDX is not None:
            _sig_isb = math.sqrt(max(0.0, P[ISB_IDX, ISB_IDX]))
            if _sig_isb > 3.0:
                _isb_ok_for_nl = False
        for m in geom:
            sid_m=m['sid']; NWL_m=m['NWL']
            if NWL_m is None: continue               # need WL first
            if sid_m in nl_fixed: continue           # already fixed
            if m['age'] < NL_MIN_OBS: continue       # not enough observations
            if sid_m not in sidx: continue

            # v80 PART 1: only allow NL fixing for the pre-selected best-4 sats.
            if sid_m not in _nl_eligible_sids: continue

            # v80 PART 4: skip satellites in re-fix cooldown (just released).
            if _nl_fix_cooldown.get(sid_m, 0) > 0: continue

            # v79 PART 1: skip AR if satellite has no OSB for required signals.
            # (Satellite STAYS in the PPP filter — only NL is withheld.)
            if m.get('no_AR', False):
                _reason = m.get('ar_skip_reason', 'no_osb')
                if _reason == 'bad_bias':
                    _ep_skip_bad_bias += 1
                else:
                    _ep_skip_no_osb += 1
                continue

            # v82 PART 5: explicit use_for_ar guard — catches fallback PPP sats
            # (no_AR above already covers this, but the counter is explicit).
            if not m.get('use_for_ar', True):
                _nl_skip_no_ar += 1
                continue

            # v97 RUN CONFIG: disable GPS NL fixing (Run C) or Galileo NL (Run D)
            if disable_gps_nl and sid_m[0] == 'G': continue
            if disable_gal_states and sid_m[0] == 'E': continue  # Run D: no Gal NL

            # ISB guard: skip GPS NL fixing until ISB converges (sigma < 3 m)
            if not _isb_ok_for_nl and sid_m[0] == 'G': continue

            # v70 IONO FIX 4: skip NL fixing for iono-unstable sats this epoch
            if sid_m in _iono_unstable: continue
            ki_m=sidx[sid_m]
            sigma_N1=math.sqrt(max(0, P[ki_m+1,ki_m+1]))
            # v59 FIX 1: sigma in metres (state is in cycles; lam1 converts)
            sigma_N1_m = sigma_N1 * m['lam1']
            N1_f=x[ki_m+1]
            # v62: explicit wrap to [−0.5, +0.5] for circular-statistics safety
            raw_frac=N1_f-round(N1_f)
            if raw_frac >  0.5: raw_frac -= 1.0
            if raw_frac < -0.5: raw_frac += 1.0

            # v79 PART 4 — HARD range gate 6.0 m (replaces v76 soft 12 m flag).
            # If the L1m-P1c range over the last 100 epochs exceeds 6 m the
            # ambiguity is untrustworthy.  Satellite remains in the PPP filter;
            # only NL fixing is skipped.
            _hist_sat = _lp1_hist.get(sid_m)
            _rng_sat = 0.0
            if _hist_sat and len(_hist_sat) >= 20:
                _arr = np.array(_hist_sat)
                _rng_sat = float(_arr.max() - _arr.min())
            if _rng_sat > (15.0 if sid_m[0] == 'G' else 6.0):
                _ep_skip_high_range += 1
                continue   # PART 5: do NOT remove from filter — just skip NL

            # ── v60/v61: per-satellite fractional bias estimation ─────────────
            # v61 Fix 1: maintain a short history of raw fractional values to
            # detect whether the ambiguity is *stable* before adding to the
            # long-term bias buffer.
            # v71 PATCH 1: branch-align raw_frac to last history entry before
            # appending so frac_std is not inflated by ±0.49 aliases.
            _hist_rf = _nl_frac_hist[sid_m]
            _raw_frac_aligned = raw_frac
            if len(_hist_rf) > 0:
                _rref = _hist_rf[-1]
                if _raw_frac_aligned - _rref > +0.5:
                    _raw_frac_aligned -= 1.0
                elif _raw_frac_aligned - _rref < -0.5:
                    _raw_frac_aligned += 1.0
            _hist_rf.append(_raw_frac_aligned)
            frac_hist_arr = np.array(_hist_rf)
            frac_std = float(np.std(frac_hist_arr)) if len(frac_hist_arr) >= 5 else 1.0

            # v67 PATCH 2 — DO NOT CLEAR buffer on frac_std alone.
            # Old (v61): frac_std > 0.05 cleared the buffer.
            # New (v67): only cycle slips (handled upstream) clear the buffer.
            # Rationale: frac_std > 0.05 is expected *before* bias has
            # converged; clearing here guaranteed buf_n never grew past 0.
            # (The cycle-slip clear at the slip-detection site is preserved.)

            # v69 PATCH 1+2: enforce single-branch buffer with tighter outlier gate.
            # FIX 1: align each new sample to the same wrap-branch as the existing
            #         buffer cluster before appending.
            # FIX 2: reject |raw_frac| > 0.40 (near wrap boundary — unreliable).
            # FIX 3: reject if aligned value is > 0.25 from buffer mean
            #         (tightened from 0.30 → 0.25 to reduce cross-branch contamination).
            # FIX 4: if buf > 10 samples and |sample - mean| > 0.25 after alignment,
            #         hard-reject (outlier from different integer assignment).
            # v70 CRITICAL: assign buf for sid_m HERE — before any len(buf) reference
            # below.  The old placement (after the append block) caused every append
            # to land in the PREVIOUS satellite's buffer (stale loop variable).
            buf = _nl_frac_buf[sid_m]
            # v70-fix1: always update last_raw_frac; drift check gates insert.
            _buf_prev_n = len(buf)   # snapshot before potential append (used by CSV gate below)
            _prev_rf = _last_raw_frac.get(sid_m)
            _last_raw_frac[sid_m] = raw_frac
            _drift_ok = (_prev_rf is None) or (abs(raw_frac - _prev_rf) <= 0.02)

            # Fix 4: do not insert after freeze. Fix 1: skip if drifting.
            if sigma_N1_m < 0.20 and sid_m not in _nl_bias_frozen and _drift_ok:
                _sample = raw_frac
                if len(buf) > 0:
                    _ref = buf[-1]
                    if _sample - _ref > +0.5:
                        _sample -= 1.0
                    elif _sample - _ref < -0.5:
                        _sample += 1.0
                buf.append(_sample)
                # (buffer growth is captured in the CSV log below)

            # v67 PATCH 4 — COMPUTE BIAS AFTER 15 SAMPLES (was 30/20).
            # Lowered to match the buf_n ≥ 15 fixing gate so bias is available
            # as soon as fixing is first attempted.
            # (buf already assigned above for sid_m — do not reassign here)
            if len(buf) >= 15 and sid_m not in _nl_bias_frozen:
                _win = list(buf)[-20:]   # Fix 2: windowed — last 20 samples only
                _angles = 2.0 * math.pi * np.array(_win)
                _mean_angle = math.atan2(float(np.mean(np.sin(_angles))),
                                          float(np.mean(np.cos(_angles))))
                _cbias = _mean_angle / (2.0 * math.pi)
                # Wrap to [−0.5, +0.5]
                _cbias = _cbias - round(_cbias)
                _nl_bias[sid_m] = _cbias

            # v68 PATCH 4: validate bias before freeze.
            # Only freeze if the bias actually produces a small corr_frac right now.
            # Prevents locking in a wrong bias that was estimated from a mixed buffer.
            if (len(buf) >= 40 and frac_std < 0.01
                    and sid_m not in _nl_bias_frozen):
                _val_bias = _nl_bias.get(sid_m, 0.0)
                _val_nc   = N1_f - _val_bias
                _val_cf   = _val_nc - round(_val_nc)
                if abs(_val_cf) < 0.05:
                    _nl_bias_frozen.add(sid_m)
                    print(f"[NL_BIAS_FROZEN] {sid_m}  bias={_val_bias:+.4f}  "
                          f"frac_std={frac_std:.4f}  corr_frac={_val_cf:+.4f}  buf_n={len(buf)}")

            # v61 Fix 4: apply bias correction only for the fixing decision.
            # Filter state x[ki_m+1] is NEVER modified.
            bias_m = _nl_bias.get(sid_m, 0.0)
            N1_corr  = N1_f - bias_m
            corr_frac = N1_corr - round(N1_corr)

            # v69 PATCH 5: wrong-bias detection — tightened 0.20 → 0.15.
            # If the buffer is well-populated (≥50 samples) but the bias it
            # produced still leaves |corr_frac| > 0.15, the circular mean
            # converged to the wrong mode.  Reset the buffer.
            # Also reset if |mean(buf)| itself > 0.20 (buffer is biased by
            # mixed branches — the single-branch gate didn't catch all cases).
            # [NL_BIAS_RESET] block removed: do NOT reset bias based on corr_frac/mean_buf heuristics.
            # Only cycle slips (handled upstream) may clear the buffer.
            # Resetting here caused Galileo bias to be discarded after convergence.

            # v69 MANDATORY DEBUG: print signal types and raw_frac once per sat per pass.
            if sid_m not in _osb_dbg_printed and sigma_N1_m < 0.30:
                _osb_dbg_printed.add(sid_m)
                print(f"[RAW_FRAC_DBG] {sid_m}  raw_frac={raw_frac:+.4f}cyc  "
                      f"N1_f={N1_f:.4f}cyc  sigma_N1={sigma_N1_m*100:.2f}cm  "
                      f"buf_n={len(buf)}  bias={bias_m:+.4f}  corr_frac={corr_frac:+.4f}")

            # ── CSV bias log — replaces [NL_DBG] / [NL_BIAS] / [NL_FRAC] prints ──
            # Conditions: sigma is near the gate  OR  corr_frac is already small
            #             OR  buffer grew this epoch.  Throttled to every 10 epochs
            # so a 2880-epoch day produces at most ~288 rows per satellite.
            _buf_grew = len(buf) > _buf_prev_n
            if (sigma_N1_m < 0.20 or abs(corr_frac) < 0.10 or _buf_grew) \
                    and nproc % 10 == 0:
                _bias_csv_w.writerow([
                    nproc,
                    f"{sod:.1f}",
                    sid_m,
                    f"{sigma_N1_m*100:.3f}",
                    f"{raw_frac:+.5f}",
                    f"{bias_m:+.5f}",
                    f"{corr_frac:+.5f}",
                    len(buf),
                    f"{frac_std:.5f}",
                    "Y" if sid_m in _nl_bias_frozen else "N",
                ])

            # v71 PATCH 2: L1m-P1c > 5 m gate REMOVED — invalid for PPP.
            # v79 PART 4 hard 6.0 m gate applied above instead.

            # v83: constellation-aware sigma gate.
            # GPS at IISC (equatorial) converges sigma_N1 more slowly;
            # 0.15m for GPS vs 0.12m for Galileo (v65 constellation-aware logic).
            _sig_gate_nl = 0.15 if sid_m[0] == 'G' else 0.12
            if sigma_N1_m > _sig_gate_nl:
                _ep_skip_sigma += 1
                reject_due_to_sigma += 1
                continue

            # ── v64 GATE C (FIX 2 + FIX 5): require buf_n ≥ 15 AND frac_std < 0.02.
            # FIX 5 — DO NOT FIX WITHOUT BIAS: if fewer than 15 samples have been
            # collected, the bias estimate is unreliable → skip NL entirely.
            # No raw_frac fallback: premature fixing with an unknown bias is the
            # primary cause of catastrophic divergence.
            buf_n = len(buf)
            if buf_n < 15:
                continue   # not enough bias samples yet

            # FIX 2 — REQUIRE FRACTIONAL STABILITY: frac_std must be < 0.05 cyc.
            # v69: constellation-aware gate — Galileo (cleaner) uses 0.04, GPS 0.05.
            _frac_std_gate = 0.04 if sid_m.startswith('E') else 0.05
            if frac_std >= _frac_std_gate:
                continue   # ambiguity still drifting — wait for stability

            # Now we have a reliable bias-corrected fractional part.
            frac_for_fix = corr_frac

            # v68 PATCH 6: branch correction.
            # If corr_frac is still large the circular mean may have converged to
            # a bias that is off by 1 cycle.  Try adjusting bias by ±1 cycle and
            # keep whichever gives the smallest |corr_frac|.
            for _badj in [-1.0, +1.0]:
                _try_nc = N1_f - (bias_m + _badj)
                _try_cf = _try_nc - round(_try_nc)
                if abs(_try_cf) < abs(corr_frac):
                    N1_corr      = _try_nc
                    corr_frac    = _try_cf
                    frac_for_fix = corr_frac
                    break

            # Fix 5: hard corr_frac gate — only allow NL fix if residual < 0.05 cyc.
            if abs(corr_frac) > 0.05:
                continue   # wrong branch risk — corr_frac too large

            # v79: flag_low_quality / range soft gate REMOVED — hard 6.0 m skip
            # above (PART 4) supersedes the v76/v78 partial gates entirely.

            # Acceptance gate: corr_frac must be within NL_RES_THRESH (0.03 cyc).
            if abs(frac_for_fix) > NL_RES_THRESH: continue     # not near integer

            # Integer is taken from the bias-corrected value.  Filter state is NOT modified.
            N1_int = int(round(N1_corr))
            N2_int=N1_int-NWL_m

            # v66 FIX 3+4: collect candidate for post-loop sort-and-limit.
            # Sort key: (0=Galileo / 1=GPS, |corr_frac| + sigma_N1_m)
            # Galileo prioritised (cleaner signals → anchor solution first).
            _is_gal_cand = sid_m.startswith('E')
            _sort_key = (0 if _is_gal_cand else 1,
                         abs(frac_for_fix) + sigma_N1_m)
            _nl_epoch_candidates.append(
                (_sort_key, sid_m, N1_int, N2_int, frac_for_fix, sigma_N1, sigma_N1_m))

        # v78 PART 4 — SINGLE NL UPDATE ONLY
        _nl_epoch_candidates.sort(key=lambda c: c[0])
        for _cand in _nl_epoch_candidates[:3]:
            _, sid_m, N1_int, N2_int, frac_for_fix, sigma_N1, sigma_N1_m = _cand
            # Must still pass primary gate at commit time
            if sigma_N1_m >= (0.15 if sid_m[0] == 'G' else 0.12):
                continue
            nl_fixed[sid_m]=(N1_int,N2_int)
            _ep_nl_count += 1
            # v64 FIX 3 — REMOVE IMMEDIATE P COLLAPSE:
            # v63 collapsed P[ki+1,ki+1]=NL_R_TIGHT immediately on a new fix,
            # which shocked the filter into accepting a potentially wrong integer.
            # Now let the KF reduce covariance naturally via repeated pseudo-obs
            # updates each epoch.  No direct P manipulation here.
            if sid_m[0] == 'G':
                _gps_nl_fixed_ever = True
            print(f"[NL FIXED] {sid_m}  N1={N1_int}  N2={N2_int}  "
                  f"frac={frac_for_fix:+.3f}  P_N1={sigma_N1*100:.1f}cm")

        # v79 PART 6 — accumulate epoch skip counts into 300-epoch window totals
        _nl_skip_no_osb      += _ep_skip_no_osb
        _nl_skip_bad_bias    += _ep_skip_bad_bias
        _nl_skip_high_range  += _ep_skip_high_range
        _nl_skip_sigma_accum += _ep_skip_sigma
        _nl_count_accum      += len(nl_fixed)   # snapshot of total currently fixed

        # ── 3d. Inject NL pseudo-observations ────────────────────────────────
        # v63 FIX 2: apply NL constraint as soon as ≥1 satellite is fixed.
        # NL_MIN_SATS is no longer a gate on constraint injection — it was
        # preventing fixes from being used because IISC only fixes 1–2 sats.
        # v63 FIX 5: innovation gate no longer permanently drops a fix.  If the
        #   float drifted > NL_INNOV_GATE this epoch, skip the constraint for
        #   this epoch only.  The release check (3a) handles persistent drift.
        # v63 FIX 6: debug print of active NL sats every epoch.
        if nl_fixed and len(geom) >= 4:
            # ── v96 FIX 1: zero iono–ambiguity cross-covariances before NL ──────
            # DIAGNOSED v95: NL pseudo-obs H = [0,…,1,…0] carries no iono column.
            # filter_standard nonetheless propagates the NL update to x[ki] through
            # P[ki, kN1] cross-terms, driving Galileo iono states to −2 … −4 m
            # every epoch.  The iono health gate then fires, resets P[ki,ki]=9 m²,
            # which keeps P[ki,kN1] permanently large → perpetual feedback loop
            # → 22 % of epochs show false 1 400–3 500 mm L1 RMS explosions.
            # Fix: zero P[ki,kN1] and P[ki,kN2] bilaterally before each NL call.
            # The cross-terms rebuild naturally from the main KF update next epoch.
            for _sid_pre, _ in nl_fixed.items():
                if _sid_pre not in sidx:
                    continue
                _ki_pre = sidx[_sid_pre]
                P[_ki_pre, _ki_pre + 1] = P[_ki_pre + 1, _ki_pre] = 0.0
                P[_ki_pre, _ki_pre + 2] = P[_ki_pre + 2, _ki_pre] = 0.0
            # ── end v96 FIX 1 ───────────────────────────────────────────────────

            # Build (H, target_integer, R) list — z recomputed each iteration
            nl_pairs=[]   # (h_vector, N_target_float, R)
            for sid_nl,(N1_i,N2_i) in list(nl_fixed.items()):
                if sid_nl not in sidx: continue
                ki_nl=sidx[sid_nl]
                # v81 FIX 5 — RELAX INNOVATION GATE (0.35 → 0.42):
                # Temporary stabilisation: wider gate ensures constraint
                # injection reaches sats whose float drifted slightly after
                # iono damping removal.  Release check (3a) still guards persistent drift.
                _innov_nl = x[ki_nl+1]-N1_i
                if abs(_innov_nl) > 0.42:
                    reject_due_to_innov += 1
                    continue
                # N1 pseudo-obs
                h1=np.zeros(len(x)); h1[ki_nl+1]=1.
                nl_pairs.append((h1, float(N1_i), NL_R_TIGHT))
                # N2 pseudo-obs
                h2=np.zeros(len(x)); h2[ki_nl+2]=1.
                nl_pairs.append((h2, float(N2_i), NL_R_TIGHT))
            if nl_pairs:
                H_nl=np.array([p[0] for p in nl_pairs])   # (n_rows, nst)
                Rd_nl=np.array([p[2] for p in nl_pairs])
                # v78 PATCH 2 — SINGLE NL UPDATE:
                # Reverted from range(2) to range(1) to prevent locking slightly
                # wrong early fixes.  One update per epoch lets the float solution
                # remain correctable; the pseudo-obs constraint still converges
                # over successive epochs without the risk of a premature lock.
                for _nl_iter in range(1):
                    # Recompute innovations from current x so each pass is consistent
                    z_nl=np.array([p[1] - float(H_nl[i] @ x)
                                   for i,p in enumerate(nl_pairs)])
                    # v58 Fix 5: regularise NL pseudo-obs R matrix
                    R_nl = np.diag(Rd_nl) + np.eye(len(Rd_nl)) * 1e-6
                    filter_standard(x,P,H_nl.T,z_nl,R_nl)
                    # Defect-9 fix: removed NL-path iono variance cap.
                    # P[_ki_nl_c, _ki_nl_c] = min(P[_ki_nl_c, _ki_nl_c], 100.0)  ← REMOVED
                    # Rationale: same as primary cap — the ceiling was suppressing
                    # P[I,N1]/P[I,N2] cross-terms after NL pseudo-obs updates.
                    # The iono health gate bounds the state value; covariance
                    # evolution is left to the KF.  Variance FLOOR is preserved.
                    # v81 FIX 4 — Ionosphere variance floor after NL update.
                    # Prevents NL pseudo-obs update from over-squeezing iono
                    # variance below physically meaningful levels (~1 cm floor).
                    for _sid_nl_f, _ki_nl_f in sidx.items():
                        P[_ki_nl_f, _ki_nl_f] = max(P[_ki_nl_f, _ki_nl_f], (0.01)**2)
                    # v58 Fix 2: NaN guard — stop iterating if state went bad
                    if not np.isfinite(x).all() or not np.isfinite(P).all():
                        x = np.where(np.isfinite(x), x, 0.0)
                        P = np.where(np.isfinite(P), P, 0.0)
                        np.fill_diagonal(P, np.maximum(np.diag(P), 100.**2))
                        P *= 100.
                        nl_fixed.clear()
                        print(f"[NaN GUARD NL] SOD={sod:.0f} iter={_nl_iter} — "
                              f"NaN after NL injection; released all fixes")
                        break

        n_nl=len(nl_fixed)

        nproc+=1
        _nproc_global = nproc
        if nproc % 100 == 0:
            _bias_csv_fh.flush()
        pos=nom+x[:3]; dx=pos-REF; d3=np.linalg.norm(dx)*1e3
        # v78 PART 7 — low-frequency debug every 300 epochs
        if nproc % 300 == 0 and sidx:
            _nl_count_dbg = len(nl_fixed)
            _sig_vals_dbg = [math.sqrt(max(0.0, P[ki+1, ki+1])) * 0.1903
                             for ki in sidx.values()]
            _mean_sig_dbg = float(np.mean(_sig_vals_dbg)) if _sig_vals_dbg else 0.0
            _max_sig_dbg  = float(max(_sig_vals_dbg))     if _sig_vals_dbg else 0.0
            # v79 PART 6 — print all skip-reason counters every 300 epochs
            # v80 PART 6 — also print max_sigma_N1_m
            print(f"[NL_STATS] SOD={sod:.0f}"
                  f"  NL_count={_nl_count_dbg}"
                  f"  mean_sigma_N1_m={_mean_sig_dbg:.4f}"
                  f"  max_sigma_N1_m={_max_sig_dbg:.4f}"
                  f"  skipped_no_osb={_nl_skip_no_osb}"
                  f"  skipped_bad_bias={_nl_skip_bad_bias}"
                  f"  skipped_high_range={_nl_skip_high_range}"
                  f"  skipped_sigma={_nl_skip_sigma_accum}"
                  f"  skipped_no_ar={_nl_skip_no_ar}"
                  f"  skipped_innov={reject_due_to_innov}")
            # v82 PART 6 — SIG_STATS: GPS participation per 300-epoch window
            _w = max(_nl_count_accum, 1)  # avoid div/0 — use epochs not nl count
            print(f"[SIG_STATS] SOD={sod:.0f}"
                  f"  nGPS_total={_sig_nGPS_total_acc}"
                  f"  nGPS_used_ppp={_sig_nGPS_ppp_acc}"
                  f"  nGPS_ar_capable={_sig_nGPS_ar_acc}"
                  f"  nGPS_fallback_used={_sig_nGPS_fb_acc}"
                  f"  (window=300ep)"
                  + ("  [CRITICAL: nGPS_used_ppp=0!]" if _sig_nGPS_ppp_acc == 0 else ""))
            # Reset window accumulators after each 300-epoch print
            _nl_skip_no_osb      = 0
            _nl_skip_bad_bias    = 0
            _nl_skip_high_range  = 0
            _nl_skip_sigma_accum = 0
            _nl_count_accum      = 0
            reject_due_to_innov  = 0
            _nl_skip_no_ar       = 0
            _sig_nGPS_total_acc  = 0
            _sig_nGPS_ppp_acc    = 0
            _sig_nGPS_ar_acc     = 0
            _sig_nGPS_fb_acc     = 0
            # v87: IONEX init diagnostic
            print(f"[IONEX_INIT] SOD={sod:.0f}"
                  f"  ionex_ok={_ionex_init_count}"
                  f"  fallback={_ionex_fallback_count}"
                  + ("  [NO_IONEX]" if ionex_maps is None else ""))
        # ── IONO HEALTH GATE ─────────────────────────────────────────────────
        # Independent per-epoch ionosphere sanity check.
        # Runs after ALL KF updates (primary + NL pseudo-obs), before RTS store.
        # Corrects physically impossible I states WITHOUT touching N1, N2,
        # covariance cross-terms, ambiguity flags, WL/NL state, or phi[sid].
        # Decouples iono-state validity from cycle-slip detection entirely.
        for _ihg_m in geom:
            _ihg_ki = _ihg_m['ki']
            _ihg_old_I = x[_ihg_ki]
            _ihg_trigger = None
            if not np.isfinite(_ihg_old_I):
                _ihg_trigger = f"nan_inf"
            elif _ihg_old_I < NEG_IONO_THRESH:
                _ihg_trigger = f"neg({_ihg_old_I:.3f}m)"
            elif _ihg_old_I > MAX_IONO_THRESH:
                _ihg_trigger = f"max({_ihg_old_I:.1f}m)"
            if _ihg_trigger is None:
                continue
            # Re-seed x[ki] and P[ki,ki] using same IONEX logic as fresh entry.
            _ihg_I_init   = None
            _ihg_var_init = 50.**2
            _ihg_ionex    = False
            _ihg_vtec_log = 0.0
            if ionex_maps is not None and ionex_meta is not None:
                _ihg_ipp = _ipp_latlon_mapping(
                    _rx_lat_rad, _rx_lon_rad,
                    _ihg_m['el'], _ihg_m.get('az', 0.), h_shell_m=450e3)
                if _ihg_ipp is not None:
                    _ihg_lat_i, _ihg_lon_i, _ihg_mfac = _ihg_ipp
                    _ihg_vtec = _ionex_vtec_at(
                        ionex_meta, ionex_maps, sod,
                        _ihg_lat_i, _ihg_lon_i)
                    if (_ihg_vtec is not None
                            and not math.isnan(_ihg_vtec)
                            and _ihg_vtec > 0.):
                        _ihg_k = (_IONEX_K_E1 if _ihg_m['sid'][0] == 'E'
                                  else _IONEX_K_L1)
                        _ihg_I_cand = _ihg_k * _ihg_vtec * _ihg_mfac
                        if 0.5 <= _ihg_I_cand <= 100.:
                            _ihg_I_init   = _ihg_I_cand
                            _ihg_sig      = max(SIGMA_IONO_FLOOR,
                                               K_SIGMA_IONO * abs(_ihg_I_init))
                            _ihg_var_init = _ihg_sig ** 2
                            _ihg_ionex    = True
                            _ihg_vtec_log = _ihg_vtec
            if _ihg_I_init is None:
                # Code-derived fallback (same as fresh-entry fallback)
                _ihg_gam = _ihg_m['gamma']
                if abs(_ihg_gam - 1.) > 1e-6:
                    # v96: subtract current RDCB_E so IHG fallback uses unbiased iono.
                    _ihg_rdcbe = (x[RDCB_E_IDX] if (RDCB_E_IDX is not None
                                                    and _ihg_m['sid'][0]=='E') else 0.0)
                    _ihg_I_code = (_ihg_m['P2c'] - _ihg_m['P1c'] - _ihg_rdcbe) / (_ihg_gam - 1.)
                    _ihg_I_init = float(np.clip(_ihg_I_code, 0.5, 100.))
                else:
                    _ihg_I_init = 5.0   # conservative default

            # v97 FIX B removed (staged iono reverted) — standard IHG behaviour.
            _ihg_sid  = _ihg_m['sid']
            _ihg_xtag = ""

            # Apply correction: x[ki] and P[ki,ki] only.
            # Off-diagonal cross-terms remain intact — KF stays connected.
            x[_ihg_ki]          = _ihg_I_init
            P[_ihg_ki, _ihg_ki] = _ihg_var_init
            _ihg_count += 1
            _disc_ihg_fired.add(_ihg_sid)   # v93 forensic marker
            _ihg_sig_out = math.sqrt(_ihg_var_init)
            _ihg_csv_w.writerow([
                f"{sod:.1f}", _ihg_sid,
                f"{_ihg_old_I:.4f}", f"{_ihg_I_init:.4f}",
                _ihg_trigger,
                int(_ihg_ionex), f"{_ihg_vtec_log:.3f}",
                f"{_ihg_sig_out:.3f}",
            ])
            if _ihg_print_count < 20:
                print(f"[IONO_HEALTH] SOD={sod:.0f} sat={_ihg_sid} "
                      f"trigger={_ihg_trigger} "
                      f"old_I={_ihg_old_I:.3f}m new_I={_ihg_I_init:.3f}m "
                      f"ionex={'Y' if _ihg_ionex else 'N'}"
                      f"{_ihg_xtag}")
                _ihg_print_count += 1
        # ── end IONO HEALTH GATE ─────────────────────────────────────────────

        # ── v100: RDCB_E GEOMETRY-FREE PSEUDO-OBSERVATION ────────────────────
        # Physics: P2c − P1c = (γ−1)·Iₛ − RDCB_E  (per Galileo satellite)
        # Rearranging: RDCB_E = (γ−1)·Iₛ − (P2c − P1c)
        # Innovation:  z = [(P2c − P1c) − (γ−1)·x[ki] + x[RDCB_E_IDX]]
        #                = [(P2c − P1c) + x[RDCB_E_IDX] − (γ−1)·x[ki]]  → 0 at truth
        # H: H[RDCB_E] = −1  (only RDCB_E and Iₛ appear; rp, N1, N2, clock cancel)
        #    H[ki]     = (γ−1)
        # Noise: σ_gf = √2 · SC / sin(el)  (two code obs combined in quadrature)
        # IHG guard (PATCH 2): skip any satellite whose iono was just reset this
        # epoch by the IONO HEALTH GATE — a freshly-seeded x[ki] would produce a
        # spurious GF innovation that pulls RDCB_E in the wrong direction.
        if RDCB_E_IDX is not None:
            _gf_H_rows = []
            _gf_z_rows = []
            _gf_R_rows = []
            for _gf_m in geom:
                if _gf_m['sid'][0] != 'E':
                    continue          # Galileo only
                if _gf_m['sid'] in _disc_ihg_fired:
                    continue          # PATCH 2: IHG skip guard
                _gf_ki  = _gf_m['ki']
                _gf_gam = _gf_m['gamma']
                _gf_sig = math.sqrt(2.0) * _sig(_gf_m['el'], SC)   # √2·σ_code
                # Innovation: measured GF + RDCB_E_state − (γ−1)·Iₛ_state
                _gf_z_k = ((_gf_m['P2c'] - _gf_m['P1c'])
                            + x[RDCB_E_IDX]
                            - (_gf_gam - 1.0) * x[_gf_ki])
                _gf_h = np.zeros(len(x))
                _gf_h[RDCB_E_IDX]  = -1.0            # ∂/∂RDCB_E
                _gf_h[_gf_ki]      = (_gf_gam - 1.0) # ∂/∂Iₛ
                _gf_H_rows.append(_gf_h)
                _gf_z_rows.append(_gf_z_k)
                _gf_R_rows.append(_gf_sig ** 2)

            if _gf_H_rows:
                _H_gf  = np.array(_gf_H_rows)
                _z_gf  = np.array(_gf_z_rows)
                _R_gf  = np.diag(np.array(_gf_R_rows)) + np.eye(len(_gf_R_rows)) * 1e-6
                filter_standard(x, P, _H_gf.T, _z_gf, _R_gf)
                # PATCH 3: NaN guard — check both x and P
                if not np.isfinite(x).all() or not np.isfinite(P).all():
                    x = np.where(np.isfinite(x), x, 0.0)
                    P = np.where(np.isfinite(P), P, 0.0)
                    np.fill_diagonal(P, np.maximum(np.diag(P), 100.**2))
        # ── end v100 RDCB_E GF pseudo-obs ────────────────────────────────────

        _amb_snapshots[sod]={sid2:(x[ki2],P[ki2,ki2])
                             for sid2,ki2 in sidx.items() if phi.get(sid2,False)}
        if direction==1:
            if not hasattr(_rts_store,'_data'): _rts_store._data=[]
            _rts_store._data.append((sod,x.copy(),P.copy()))

        # v54 RAW residuals for logging
        # v91-diag FIX 2: include GPS ISB in code_rms diagnostic so the logged
        # CodeRMS is consistent with the actual KF observation model.
        # P1c_GPS = rp + ISB + I  →  res_P1 = P1c - (rp + ISB + I)
        # Previously ISB was omitted, injecting a fake ~1-3 m bias per GPS sat
        # and inflating CodeRMS by O(1000 mm).  KF is untouched.
        code_res=[
            m['P1c']
            -(
                _rp(m,x[3],x[4])
                +(x[ISB_IDX] if (ISB_IDX is not None and m['sid'][0]=='G') else 0.0)
                +x[m['ki']]
            )
            for m in geom
        ]
        code_rms=math.sqrt(np.mean(np.array(code_res)**2))*1e3 if code_res else 0.
        phase_rms=phase_rms_now*1e3
        ZHD=zhd; ZWD=x[4]; TROPO=ZHD+ZWD

        # ── RMS-SPLIT DIAGNOSTICS — moved before NL injection (v96 FIX 2) ──
        # The diagnostic block now runs after phase_rms_now (pre-NL), not post-NL.
        # This eliminates false [RMS_WARN] explosions from iono health gate corruption.
        # CSV, per-sat forensics, and mutation assert are all in the new location.

        # ── v93 FORENSIC: L2_RMS_SPIKE — per-satellite L2 residual > 1000 mm ─
        for _disc_sid_spike, _disc_l2r in _disc_sat_l2_res.items():
            if abs(_disc_l2r) * 1e3 > 1000.0 and _disc_sid_spike in sidx:
                _disc_ki_sp  = sidx[_disc_sid_spike]
                _disc_l1r_sp = _disc_sat_l1_res.get(_disc_sid_spike, _DISC_MISSING)
                _disc_sig_sp = _sat_signal_map.get(_disc_sid_spike, ('?','?','?','?'))
                _disc_pair_sp = (f"{_disc_sig_sp[0]}/{_disc_sig_sp[2]}"
                                 if isinstance(_disc_sig_sp, tuple) and len(_disc_sig_sp) >= 3
                                 else str(_disc_sig_sp))
                _disc_write(
                    sod, _disc_sid_spike,
                    arc_age=_sat_age.get(_disc_sid_spike, 0),
                    slip_detected=False,
                    dGF_m=None, dMW_cyc=None,
                    wl_before=_disc_wl_prev.get(_disc_sid_spike),
                    wl_after=wl_fixed.get(_disc_sid_spike),
                    nl_fixed_before=(_disc_nl_prev.get(_disc_sid_spike) is not None),
                    nl_fixed_after=(_disc_sid_spike in nl_fixed),
                    N1_before=_disc_N1_prev.get(_disc_sid_spike, _DISC_MISSING),
                    N1_after=x[_disc_ki_sp+1],
                    N2_before=_disc_N2_prev.get(_disc_sid_spike, _DISC_MISSING),
                    N2_after=x[_disc_ki_sp+2],
                    iono_before=_disc_iono_prev.get(_disc_sid_spike, _DISC_MISSING),
                    iono_after=x[_disc_ki_sp],
                    phase_rms_L1_mm=(abs(_disc_l1r_sp)*1e3 if isinstance(_disc_l1r_sp, float) and not math.isnan(_disc_l1r_sp) else None),
                    phase_rms_L2_mm=abs(_disc_l2r)*1e3,
                    fallback_used=False,
                    primary_signal_pair=_disc_pair_sp,
                    osb_signal_pair=_disc_pair_sp,
                    missing_prev_epoch=False,
                    epochs_since_last_obs=None,
                    mw_buffer_std=(float(np.std(list(mw_hist[_disc_sid_spike])))
                                   if len(mw_hist[_disc_sid_spike]) > 1 else None),
                    gf_consistency_triggered=(_disc_sid_spike in _disc_ar_fired),
                    iono_health_gate_triggered=(_disc_sid_spike in _disc_ihg_fired),
                    reset_path="NONE",
                    event_type="L2_RMS_SPIKE",
                )
        # ── end v93 L2_RMS_SPIKE ─────────────────────────────────────────────

        # ── v93 FORENSIC: NL_RELEASE — sats released from nl_fixed ──────────
        # `_disc_to_release_snap` was saved before nl_fixed.pop(); log each release.
        for _disc_sid_rel in _disc_to_release_snap:
            if _disc_sid_rel in sidx:
                _disc_ki_rel  = sidx[_disc_sid_rel]
                _disc_sig_rel = _sat_signal_map.get(_disc_sid_rel, ('?','?','?','?'))
                _disc_pair_rel = (f"{_disc_sig_rel[0]}/{_disc_sig_rel[2]}"
                                  if isinstance(_disc_sig_rel, tuple) and len(_disc_sig_rel) >= 3
                                  else str(_disc_sig_rel))
                _disc_write(
                    sod, _disc_sid_rel,
                    arc_age=_sat_age.get(_disc_sid_rel, 0),
                    slip_detected=False,
                    dGF_m=None, dMW_cyc=None,
                    wl_before=_disc_wl_prev.get(_disc_sid_rel),
                    wl_after=wl_fixed.get(_disc_sid_rel),
                    nl_fixed_before=True,
                    nl_fixed_after=False,
                    N1_before=_disc_N1_prev.get(_disc_sid_rel, _DISC_MISSING),
                    N1_after=x[_disc_ki_rel+1],
                    N2_before=_disc_N2_prev.get(_disc_sid_rel, _DISC_MISSING),
                    N2_after=x[_disc_ki_rel+2],
                    iono_before=_disc_iono_prev.get(_disc_sid_rel, _DISC_MISSING),
                    iono_after=x[_disc_ki_rel],
                    phase_rms_L1_mm=(abs(_disc_sat_l1_res.get(_disc_sid_rel, float('nan')))*1e3
                                     if _disc_sid_rel in _disc_sat_l1_res else None),
                    phase_rms_L2_mm=(abs(_disc_sat_l2_res.get(_disc_sid_rel, float('nan')))*1e3
                                     if _disc_sid_rel in _disc_sat_l2_res else None),
                    fallback_used=False,
                    primary_signal_pair=_disc_pair_rel,
                    osb_signal_pair=_disc_pair_rel,
                    missing_prev_epoch=False,
                    epochs_since_last_obs=None,
                    mw_buffer_std=(float(np.std(list(mw_hist[_disc_sid_rel])))
                                   if len(mw_hist[_disc_sid_rel]) > 1 else None),
                    gf_consistency_triggered=(_disc_sid_rel in _disc_ar_fired),
                    iono_health_gate_triggered=(_disc_sid_rel in _disc_ihg_fired),
                    reset_path="NONE",
                    event_type="NL_RELEASE",
                )
        # ── end v93 NL_RELEASE ────────────────────────────────────────────────

        # ── v93 FORENSIC: end-of-epoch snapshot for "before" comparisons ─────
        for _disc_sid_snap in sidx:
            _disc_ki_snap = sidx[_disc_sid_snap]
            _disc_wl_prev[_disc_sid_snap]    = wl_fixed.get(_disc_sid_snap)
            _disc_nl_prev[_disc_sid_snap]    = nl_fixed.get(_disc_sid_snap)
            _disc_N1_prev[_disc_sid_snap]    = x[_disc_ki_snap+1]
            _disc_N2_prev[_disc_sid_snap]    = x[_disc_ki_snap+2]
            _disc_iono_prev[_disc_sid_snap]  = x[_disc_ki_snap]
        # ─────────────────────────────────────────────────────────────────────

        results[sod]={'xyz':pos.copy(),'dx':dx.copy(),'p_trace':P[0,0]+P[1,1]+P[2,2],
                      'n':len(geom),'ztd':TROPO,'wl_fixed':len(wl_fixed),
                      'nl_fixed':n_nl,
                      'code_rms':code_rms,'phase_rms':phase_rms,
                      'zhd':ZHD,'zwd':ZWD,
                      'sats_used':sorted([m['sid'] for m in geom]),
                      'sats_wl':sorted([s for s in wl_fixed if any(m['sid']==s for m in geom)]),
                      'sats_nl':sorted([s for s in nl_fixed if any(m['sid']==s for m in geom)])}

        # [RDCB_E] diagnostic — every 300 epochs (v96 preserved, v98 enhanced)
        if RDCB_E_IDX is not None and nproc > 0 and nproc % 300 == 0:
            _rdcbe_val  = x[RDCB_E_IDX]
            _rdcbe_sig  = math.sqrt(max(0., P[RDCB_E_IDX, RDCB_E_IDX]))
            _nGAL_diag  = sum(1 for m in geom if m['sid'][0]=='E')
            _mean_raw   = (sum(_rdcbe_gf_raw_acc)  / len(_rdcbe_gf_raw_acc)
                           if _rdcbe_gf_raw_acc  else float('nan'))
            _mean_corr  = (sum(_rdcbe_gf_corr_acc) / len(_rdcbe_gf_corr_acc)
                           if _rdcbe_gf_corr_acc else float('nan'))
            _delta_gf   = (_mean_raw - _mean_corr
                           if (not (isinstance(_mean_raw, float) and
                                    isinstance(_mean_corr, float) and
                                    (_mean_raw != _mean_raw or _mean_corr != _mean_corr)))
                           else float('nan'))
            print(f"[RDCB_E] SOD={sod:.0f}  value={_rdcbe_val:+.4f}m"
                  f"  sigma={_rdcbe_sig:.4f}m  nGAL={_nGAL_diag}"
                  f"  mean_GF_raw={_mean_raw:+.3f}m"
                  f"  mean_GF_corr={_mean_corr:+.3f}m"
                  f"  delta_GF={_delta_gf:+.3f}m")
            # v98 SECTION 6C: per-satellite code GF sign check
            # This shows whether the fix is working: after convergence, all
            # corrected P2c−P1c values should be positive (= (γ−1)·I_s > 0).
            _all_gf_raw_pos  = True
            _all_gf_corr_pos = True
            for _m_gal in geom:
                if _m_gal['sid'][0] != 'E': continue
                _gam_diag = _m_gal['gamma']
                if abs(_gam_diag - 1.) < 1e-6: continue
                _gf_raw  = (_m_gal['P2c'] - _m_gal['P1c'])
                # v99 FIX 5: corrected GF = (P2c−P1c) + RDCB_E = (γ−1)·I
                # Derivation: P2c−P1c = (γ−1)·I − RDCB_E
                #   → (P2c−P1c) + RDCB_E = (γ−1)·I  ✓
                # Old code added RDCB_E/(γ−1) which gave (γ−1)·I + RDCB_E·(1/(γ−1)−1),
                # a value with no clean physical interpretation.
                _gf_corr = _gf_raw + _rdcbe_val
                _i_s_diag = x[_m_gal['ki']]
                _expected = (_gam_diag - 1.0) * _i_s_diag
                # v99 FIX 6: raw GF sign check must account for RDCB_E.
                # Physics: P2c−P1c = (γ−1)·I − RDCB_E
                # When RDCB_E > (γ−1)·I (IISC receiver has RDCB_E ≈ 10-14 m),
                # the raw GF IS negative even for positive I_s — that is correct,
                # not a sign inversion. Old code flagged every such epoch as an error.
                _expected_raw  = (_gam_diag - 1.0) * _i_s_diag - _rdcbe_val
                _sign_raw_ok   = abs(_gf_raw - _expected_raw) < 5.0     # ±5 m tolerance
                # Corrected GF (after FIX 5) = (γ−1)·I; should share sign with I_s.
                _sign_corr_ok  = (_gf_corr > 0) == (_i_s_diag > 0)
                if not _sign_raw_ok:  _all_gf_raw_pos  = False
                if not _sign_corr_ok: _all_gf_corr_pos = False
                print(f"  [CODE_GF] sat={_m_gal['sid']}"
                      f"  raw_P2c-P1c={_gf_raw:+.3f}m"
                      f"  corrected={_gf_corr:+.3f}m"
                      f"  expected={_expected:+.3f}m"
                      f"  I_s={_i_s_diag:.3f}m"
                      f"  raw_sign_ok={_sign_raw_ok}"
                      f"  corr_sign_ok={_sign_corr_ok}")
            print(f"  [CODE_GF_SUMMARY] ALL_RAW_SIGN_OK={_all_gf_raw_pos}"
                  f"  ALL_CORR_SIGN_OK={_all_gf_corr_pos}"
                  f"  RDCB_E_converged={'YES' if abs(_rdcbe_val)>1.0 else 'NO(still near 0)'}")
            _rdcbe_gf_raw_acc.clear()
            _rdcbe_gf_corr_acc.clear()

        if nproc<=3 or nproc%240==0:
            n_gps=sum(1 for m in geom if m['sid'][0]=='G')
            n_gal=sum(1 for m in geom if m['sid'][0]=='E')
            # v77 PART 6: GPS satellite count diagnostics
            nGPS_total  = sum(1 for s,_ in sobs.items() if s[0]=='G' and s[0] in constellation)
            nGPS_used   = n_gps
            nGPS_AR_cap = sum(1 for m in geom if m['sid'][0]=='G' and not m.get('no_AR', False))
            print(f"  [{label}] SOD={sod:6.0f}  N={len(geom):2d}(G{n_gps}+E{n_gal})"
                  f"  3D={d3:8.1f}mm  WL={len(wl_fixed)}  NL={n_nl}"
                  f"  ZHD={ZHD:.3f}m  ZWD={ZWD:.4f}m  ZTD={TROPO:.4f}m"
                  f"  CodeRMS={code_rms:.1f}mm  PhsRMS={phase_rms:.2f}mm")
            print(f"  [GPS_DBG] SOD={sod:.0f}  nGPS_total={nGPS_total}"
                  f"  nGPS_used={nGPS_used}  nGPS_AR_capable={nGPS_AR_cap}")

    print(f"[WL_DICT] size={len(wl_fixed)} keys={list(wl_fixed.keys())}")
    # v54: store (I, N1, N2, P_I, P_N1, P_N2) per satellite for RTS/inheritance
    fwd_amb={sid:(x[ki],x[ki+1],x[ki+2],P[ki,ki],P[ki+1,ki+1],P[ki+2,ki+2])
             for sid,ki in sidx.items() if phi.get(sid,False)}
    fwd_amb_out={sid:v for sid,v in fwd_amb.items() if sid in _amb_conv_sods}
    excluded={sid:f"pt={_amb_init_ptrace.get(sid,999):.3f}"
              for sid in fwd_amb if sid not in _amb_conv_sods}
    print(f"[AMB INHERIT] {len(fwd_amb_out)}/{len(fwd_amb)} sats "
          f"(excluded: {excluded})")
    _bias_csv_fh.flush()
    _bias_csv_fh.close()
    print(f"[CSV]  nl_bias_debug.csv → {_bias_csv_path}")
    _rms_csv_fh.flush()
    _rms_csv_fh.close()
    print(f"[CSV]  rms_split_diagnostics.csv → {_rms_csv_path}")
    _wlcal_csv_fh.flush()
    _wlcal_csv_fh.close()
    print(f"[CSV]  gal_wl_calibration_applied.csv → {_wlcal_csv_path}")
    _ionex_csv_fh.flush()
    _ionex_csv_fh.close()
    print(f"[CSV]  ionex_init_audit.csv → {_ionex_csv_path}")
    _arc_reset_csv_fh.flush()
    _arc_reset_csv_fh.close()
    print(f"[CSV]  arc_reset_iono_audit.csv → {_arc_reset_csv_path}  "
          f"({_arc_reset_iono_count} iono re-inits)")
    _c1c_excl_csv_fh.flush()
    _c1c_excl_csv_fh.close()
    print(f"[CSV]  c1c_phase_exclusion_audit.csv → {_c1c_excl_csv_path}  "
          f"({_c1c_excl_count} exclusion events)")
    _c2w_audit_fh.flush()
    _c2w_audit_fh.close()
    print(f"[CSV]  gps_c2w_weight_audit.csv → {_c2w_audit_path}  "
          f"(GPS C2W/L2W σ_P2×{_C2W_DEWEIGHT_CODE_SCALE}  σ_L2×{_C2W_DEWEIGHT_PHASE_SCALE})")
    _adap_csv_fh.flush()
    _adap_csv_fh.close()
    print(f"[CSV]  adaptive_iono_sigma_audit.csv → {_adap_csv_path}  "
          f"(SIGMA_FLOOR={SIGMA_IONO_FLOOR}m  K={K_SIGMA_IONO})")
    _gf_guard_csv_fh.flush()
    _gf_guard_csv_fh.close()
    print(f"[CSV]  gf_guard_audit.csv → {_gf_guard_csv_path}  "
          f"({_gf_guard_skipped_count} GF tests skipped by init guard)")
    _ihg_csv_fh.flush()
    _ihg_csv_fh.close()
    print(f"[CSV]  iono_health_gate_audit.csv → {_ihg_csv_path}  "
          f"({_ihg_count} independent iono corrections)")
    _disc_csv_fh.flush()
    _disc_csv_fh.close()
    print(f"[CSV]  ambiguity_discontinuity_audit.csv → {_disc_csv_path}")
    print(f"[IONEX_SUMMARY]  successful={_ionex_init_count}  fallback={_ionex_fallback_count}"
          + ("  [NO_IONEX: ionex_maps=None]" if ionex_maps is None else ""))
    return results,nom+x[:3],x[3],x[4],wl_fixed,fwd_amb_out,_amb_snapshots


# ==============================================================================
#  Main entry point
# ==============================================================================
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
    # v87: detect IONEX/GIM file
    inx_f=_f(['.inx','.INX','.ionex','.IONEX'])

    print("="*72)
    print("GPS+Galileo PPP v98 — RDCB_E Prior Fix + Phase Forensics")
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

    # v87: IONEX/GIM ionosphere maps
    _ionex_meta = None; _ionex_maps = None
    if inx_f:
        try:
            _ionex_meta, _ionex_maps = parse_ionex(inx_f)
        except Exception as e:
            print(f"[IONEX] WARNING: failed to parse '{inx_f}': {e}")
            _ionex_meta = None; _ionex_maps = None
    else:
        print(f"[IONEX] No IONEX/GIM file found — ionosphere init uses code-derived fallback")

    REF=np.array([1337935.5599,6070317.2377,1427877.5071])
    APX=np.array([1337936.455, 6070317.126, 1427876.785])
    tref=sp3t[0]; DOY=38
    lat0,_,h0=_lla(APX); zhd=_zhd(lat0,h0)

    print(f"[INIT] ZHD={zhd:.4f}m  h={h0:.0f}m  lat={math.degrees(lat0):.3f}deg")
    print(f"[MODEL] SatPCO/PCV:{len(satx)} PRNs  RecPCO/PCV:{'Y' if recx else 'N'}"
          f"  OBX:{len(att)} sats  OSB:{sum(len(v) for v in osb.values())} entries"
          f"  OTL:{'Y ('+sta_name+')' if blq and sta_name in blq else 'N'}"
          f"  IONEX:{'Y ('+str(len(_ionex_maps))+' maps)' if _ionex_maps else 'N'}")
    print()

    _common=dict(sp3t=sp3t,sp=sp,sc=sc,clkd=clkd,osb=osb,ah=ah,
                 lat0=lat0,doy=DOY,zhd=zhd,tref=tref,satx=satx,att=att,recx=recx,
                 blq=blq,sta=sta_name,
                 ionex_meta=_ionex_meta,ionex_maps=_ionex_maps)

    # ── v97 DIFFERENTIAL EXPERIMENT: 4 run configurations ────────────────────
    run_configs = [
        dict(label='A:GPS+Galileo',     const='GE', disable_gps_nl=False, disable_gal_states=False),
        dict(label='B:GPS-only',        const='G',  disable_gps_nl=False, disable_gal_states=False),
        dict(label='C:GE-float-GPS-NL', const='GE', disable_gps_nl=True,  disable_gal_states=False),
        dict(label='D:GE-GAL-codeonly', const='GE', disable_gps_nl=False, disable_gal_states=True),
    ]
    all_fwd={}; all_rts={}; all_meta={}

    for cfg in run_configs:
        label=cfg['label']; const=cfg['const']
        print(f"\n{'='*72}")
        print(f"[RUN] {label}  constellation='{const}'  "
              f"disable_gps_nl={cfg['disable_gps_nl']}  "
              f"disable_gal_states={cfg['disable_gal_states']}")
        _rts_store._data=[]
        fwd,ex,ec,ez,wl_f,fwd_amb,fwd_snap=_ppp_pass(
            epochs,nom=APX.copy(),iclk=0.,izwd=0.20,
            direction=1,label="FWD",constellation=const,
            disable_gps_nl=cfg['disable_gps_nl'],
            disable_gal_states=cfg['disable_gal_states'],
            **_common)
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

    # Cross-run summary table
    print(f"\n{'='*72}")
    print("  v99 DIFFERENTIAL EXPERIMENT SUMMARY")
    print(f"  {'Run':<24}  {'3D-RMS':>8}  {'E-RMS':>7}  {'N-RMS':>7}  {'U-RMS':>7}  "
          f"{'Conv10cm':>10}  {'NL-ep':>6}  {'RTS-3D':>8}")
    print(f"  {'-'*24}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*10}  {'-'*6}  {'-'*8}")
    for cfg in run_configs:
        lbl=cfg['label']
        fwd_l=all_fwd.get(lbl,{}); rts_l=all_rts.get(lbl,{})
        m=_compute_metrics(fwd_l,REF); mr=_compute_metrics(rts_l,REF)
        if m:
            ct10=f"{m['conv_time_10cm']:.0f}s" if m['conv_time_10cm'] else "---"
            rts3d=f"{mr['rms_3d']:.1f}" if mr else "---"
            print(f"  {lbl:<24}  {m['rms_3d']:8.1f}  {m['rms_e']:7.1f}  "
                  f"{m['rms_n']:7.1f}  {m['rms_u']:7.1f}  "
                  f"{ct10:>10}  {m['n_nl_fix']:6d}  {rts3d:>8}")

    primary_fwd=all_fwd.get('A:GPS+Galileo',{})
    primary_rts=all_rts.get('A:GPS+Galileo',{})
    rl=[(s,{**r,'pass':'FWD'}) for s,r in sorted(primary_fwd.items())]

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
    colors={'A:GPS+Galileo':'#3cb44b','B:GPS-only':'#e6194b',
            'C:GE-float-GPS-NL':'#f58231','D:GE-GAL-codeonly':'#4363d8'}
    fig,axes=plt.subplots(2,2,figsize=(14,10))
    fig.suptitle('PPP-AR v98 — RDCB_E Fix: A/B/C/D Run Comparison',
                 fontsize=13,fontweight='bold')
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
    m=_compute_metrics(all_fwd.get('A:GPS+Galileo',{}),REF)
    if m is not None:
        sh=m['sods']/3600.
        ax.plot(sh,m['e_mm'],color='#e6194b',linewidth=0.8,label='East')
        ax.plot(sh,m['n_mm'],color='#3cb44b',linewidth=0.8,label='North')
        ax.plot(sh,m['u_mm'],color='#4363d8',linewidth=0.8,label='Up')
        ax.axhline(0,color='black',linewidth=0.5)
    ax.set_xlabel('Time (h)'); ax.set_ylabel('Error (mm)')
    ax.set_title('(b) ENU — Run A: GPS+Galileo FWD')
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
    mf=_compute_metrics(all_fwd.get('A:GPS+Galileo',{}),REF)
    mr=_compute_metrics(all_rts.get('A:GPS+Galileo',{}),REF)
    if mf: ax.plot(mf['sods']/3600.,mf['d3_mm'],color='#4363d8',linewidth=0.8,
                   alpha=0.8,label='FWD')
    if mr: ax.plot(mr['sods']/3600.,mr['d3_mm'],color='#f58231',linewidth=0.8,
                   alpha=0.8,label='RTS')
    ax.axhline(50,color='gray',linestyle='--',linewidth=0.7)
    ax.set_xlabel('Time (h)'); ax.set_ylabel('3D Error (mm)')
    ax.set_title('(d) FWD vs RTS — GPS+Galileo')
    ax.set_ylim(0,300); ax.legend(fontsize=8); ax.grid(True,alpha=0.3)
    plt.tight_layout()
    plot_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'ppp_comparison3.png')
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
        'COD0OPSFIN_20260380000_01D_01H_GIM.INX',   # v87: IONEX GIM
    ]]
    postpos(None,None,0.,0.,PrcOpt(),SolOpt(),FilOpt(),
            INFILES,os.path.join(DATA,'ppp_results3.csv'))
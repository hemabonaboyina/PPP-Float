"""
Session 4 (v107 — hump diagnostics + controlled experiments)

Goal: diagnose and reduce the persistent 8–18 h vertical error hump without
      touching ISB/fusion architecture or ambiguity fixing logic.

NEW: _vdop_ew(geom, lat, lon)
     Elevation-sin²-weighted VDOP projected onto the local Up direction.
     Logged to hump_diag.csv every epoch.

NEW: _sig2(el, s0)
     Elevation weighting function with 1/sin²(el) instead of 1/sin(el).
     Used when Exp B is active; all four Rd rows (P1/P2/L1/L2) use it.

NEW: hump_diag.csv  (logs/ directory, per-epoch, every pass)
     Columns: SOD, in_hump, ZWD_m, ZWD_innov_m, ZWD_prior_sigma,
              mean_iono_var_m2, max_iono_var_m2, n_iono_sats,
              VDOP_ew, n_sats, n_low_el, min_el_deg, mean_el_deg,
              mean_slant_TEC_m, max_slant_TEC_m,
              ph_innov_rms_mm, code_innov_rms_mm,
              exp_A_ion_boost, exp_B_el2, exp_C_mask15, exp_D_zwd_tight

CONTROLLED EXPERIMENTS (flags in _ppp_pass — flip to False to isolate):
  Exp A  HUMP_ION_BOOST=True   3× ION_PROC_NOISE during 8-18 h
  Exp B  HUMP_EL2_WEIGHT=True  1/sin²(el) Rd weighting all day
  Exp C  HUMP_ELEV_MASK=False  15° mask diagnostic (off by default)
  Exp D  HUMP_ZWD_TIGHTEN=True tighter ZWD Q=5e-11 m²/s during 8-18 h

ISB/fusion architecture: UNCHANGED.
Ambiguity fixing logic: UNCHANGED.
Schur/covariance surgery: NOT added.

"""
"""
Session 1 (v102 — NL gate unification)

_nl_cluster dict → flat list (shared across constellations)
Post-fix rejection disabled (NL_POSTFIX_REJECT, SKIP_POSTFIX_RESID)
Multi-stage gates replaced with single: abs(corr_frac) < 0.03 and sigma_N1_m < 0.07
Cooldown 30 → 60 epochs

Session 2 (v103 — filter tuning)

ION_PROC_NOISE 1e-5 → 5e-5
ZWD_PRIOR_SIGMA 0.06 → 0.15
ZWD watchdog global flush → per-satellite bad-only release
Q_ISB confirmed dynamic at 1e-6·dt

Session 3 (v103 — cluster correctness)

_nl_cf_at_fix dict added — stores corr_frac at fix time per satellite
_nl_circ_mean / _nl_circ_diff helpers added — circular statistics to handle ±0.5 wrap
Consistency check now compares via circular diff against circular mean of stored corr_frac values
Corrupt-cluster guard: |cluster_mean| > 0.25 triggers full flush
_nl_cf_at_fix cleared on all release paths (spike, diverge-undo, ZWD watchdog, full resets)
ppp.py  v100 — GPS stability fix: ISB reset disabled, slip cooldown, tighter GPS sigma gate,
               post-fix validation, GPS-only early-fix block, relaxed NL_R_TIGHT
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

# ── Diagnostic mode switches ──────────────────────────────────────────────────
# DIAG_LIGHT : fast iterative runs — keeps all hump-analysis capability but
#              reduces heavy per-epoch bookkeeping to every 120 epochs.
# DIAG_DEEP  : full per-epoch diagnostics, dense CSV logging, overlay plots.
#              Set True only for one-off deep-dive passes.
DIAG_LIGHT = True
DIAG_DEEP  = False   # set True to re-enable full per-epoch diagnostics
# ─────────────────────────────────────────────────────────────────────────────

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

# Pre-computed NL denominators
_DENOM_G = ALFA*LAMBDA1 - BETA*LAMBDA2         # GPS NL denom  ≈ 0.1073 m
_DENOM_E = ALFA_E*LAMBDA_E1 - BETA_E*LAMBDA_E5A  # Gal NL denom  ≈ 0.1090 m

def _ifc(a, b):   return ALFA*a - BETA*b
def _sig(el, s0): return s0 / max(math.sin(el), 0.1)

# Exp B: 1/sin²(el) weighting — stronger low-elevation downweight
def _sig2(el, s0): return s0 / max(math.sin(el)**2, 0.01)

# GEOM FIX 1: 1/sin^4(el) weight achieved via sig_eff = s0/sin^2(el), Rd = sig_eff^2
# _sig4 is identical to _sig2 (sigma = s0/sin^2 → Rd = s0^2/sin^4 = 1/sin^4 weight)
def _sig4(el, s0): return s0 / max(math.sin(el)**2, 0.01)

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
                    elif cant:
                        rec_atx[cant]={'L1':np.array(pf.get('G01',[0,0,0]),float),
                                       'L2':np.array(pf.get('G02',[0,0,0]),float),
                                       'v1':list(pv.get('G01',[])),'v2':list(pv.get('G02',[])),
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

def _rpco(re, lat, lon):
    if re is None: return np.zeros(3)
    pi=ALFA*re['L1']-BETA*re['L2']
    sl,cl=math.sin(lat),math.cos(lat); sn,cn=math.sin(lon),math.cos(lon)
    R=np.array([[-sl*cn,-sn,cl*cn],[-sl*sn,cn,cl*sn],[cl,0,sl]])
    return R@(pi*1e-3)

def _rpcv(re, el):
    if re is None: return 0.
    zen=90-math.degrees(el)
    v1=_pcv(re['v1'],re['z1'],re['dz'],zen)
    v2=_pcv(re['v2'],re['z1'],re['dz'],zen)
    return (ALFA*v1-BETA*v2)*1e-3


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
                print(f"[BLQ] loaded station {sta}")
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

def _vdop_ew(geom, lat, lon):
    """Elevation-sin²-weighted VDOP in local Up direction."""
    if len(geom) < 4: return 99.
    H = np.zeros((len(geom), 4))
    W = np.zeros(len(geom))
    for i, m in enumerate(geom):
        u = m['unit']
        H[i, 0] = -u[0]; H[i, 1] = -u[1]; H[i, 2] = -u[2]; H[i, 3] = 1.
        W[i] = math.sin(max(m['el'], 0.05)) ** 2
    try:
        HtWH = (H * W[:, None]).T @ H
        Q = np.linalg.inv(HtWH + np.eye(4) * 1e-14)
        sl = math.sin(lat); cl = math.cos(lat)
        sn = math.sin(lon); cn = math.cos(lon)
        up = np.array([cl*cn, cl*sn, sl])
        vdop = math.sqrt(max(0., float(up @ Q[:3, :3] @ up)))
        return vdop
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
    N=len(data); dim=5
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
# v84 PART 1: state-change-only AR logging — avoids per-epoch per-sat spam
_prev_gps_ar_state = {}   # sid -> bool (no_AR from last logged epoch)

def _proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx,
          blq=None,sta='IISC',tow_total=0.):
    """GPS satellite — dynamic signal detection from RINEX obs.

    v68 FIX: Dynamically detect actual L1/L2 code and phase signals present in
    RINEX.  Apply OSBs ONLY to the matching signal type so that the OSB
    reference frame is consistent with the observable.
    """
    el = None   # initialised here; computed below via _elaz — guards log_osb call

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
        # v83: AR eligibility is now determined by OSB availability below,
        # not by the primary/fallback label.  Set use_for_ar=True tentatively;
        # the OSB completeness + value checks at lines ~1874-1888 will set
        # no_AR=True / use_for_ar=False if OSB is missing or out-of-range.
        _P1C = so.get('C1C', 0.); _L1C = so.get('L1C', 0.)
        if _P1C != 0. and _L1C != 0. and _P2W != 0. and _L2W != 0.:
            code1_type, code2_type, phase1_type, phase2_type = 'C1C', 'C2W', 'L1C', 'L2W'
            P1_val, P2_val, L1_val, L2_val = _P1C, _P2W, _L1C, _L2W
            use_for_ppp    = True
            use_for_ar     = True   # v83: tentative — OSB check below is the gate
            _fallback_used = True
        else:
            # PPP-ONLY FALLBACK: try alternative GPS L2 signal types
            # (C2L/L2L = L2C civilian; C2S/L2S; C2X/L2X combined; C2P/L2P)
            # No OSB exists for these types → AR disabled, PPP retained.
            _L2_ALTS = [('C2L','L2L'), ('C2S','L2S'), ('C2X','L2X'), ('C2P','L2P')]
            _found_alt = False
            for _c2t, _l2t in _L2_ALTS:
                _P2x = so.get(_c2t, 0.); _L2x = so.get(_l2t, 0.)
                if _P2x == 0. or _L2x == 0.:
                    continue
                if _P1W != 0. and _L1W != 0.:
                    code1_type, code2_type, phase1_type, phase2_type = 'C1W', _c2t, 'L1W', _l2t
                    P1_val, P2_val, L1_val, L2_val = _P1W, _P2x, _L1W, _L2x
                elif _P1C != 0. and _L1C != 0.:
                    code1_type, code2_type, phase1_type, phase2_type = 'C1C', _c2t, 'L1C', _l2t
                    P1_val, P2_val, L1_val, L2_val = _P1C, _P2x, _L1C, _L2x
                else:
                    continue
                use_for_ppp = True
                use_for_ar  = False   # no AR without matched OSBs
                _fallback_used = True
                _found_alt = True
                break
            if not _found_alt:
                return None   # genuinely no usable dual-freq combination

    # Signal map lock: records the signal frame used for AR-eligible satellites.
    # v83: fallback (C1C) satellites with valid OSB are also locked here since
    # they are now AR-eligible.  Satellites without valid OSB (use_for_ar=False)
    # are not locked so they can become AR-eligible on a later epoch if OSB arrives.
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
    # v79 PART 1 / v82 / v83: OSB completeness and value checks.
    # v83: now runs for BOTH primary (C1W) and fallback (C1C) signals.
    # use_for_ar is True in both cases at this point; OSB presence and
    # value validity are the SOLE gate for AR eligibility.
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

    # v84 PART 1: track AR state changes (state kept; print suppressed — see GPS_SUMMARY)
    _sig_pair_str = f"{code1_type}/{phase1_type}+{code2_type}/{phase2_type}"
    _has_valid_osb = (ar_skip_reason is None) and bool(ob)
    _prev_gps_ar_state[sid] = no_AR  # update silently; summary printed per 300 ep

    if sid not in _osb_dbg_printed:
        _osb_dbg_printed.add(sid)

    P1c = P1 - b_C1
    P2c = P2 - b_C2

    # RAW phase observables converted to metres, then phase OSB removed
    lam1 = LAMBDA1
    lam2 = LAMBDA2
    L1m  = L1 * lam1 - b_L1
    L2m  = L2 * lam2 - b_L2

    # ── v81 OSB CONSISTENCY (CSV logging) ────────────────────────────────────
    _diff_corr_gps = L1m - P1c
    _diff_raw_gps  = L1 * lam1 - P1
    hist_g = _cp_debug.setdefault(sid, deque(maxlen=100))
    hist_g.append(_diff_corr_gps)
    if DIAG_DEEP and _nproc_global % 300 == 0:
        rng_g = max(hist_g) - min(hist_g) if len(hist_g) > 5 else 0.0
        el_deg = round(math.degrees(el), 2) if el is not None else -1.0
        log_osb([
            _nproc_global,
            sid,
            el_deg,
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
    # PART A diagnostic: capture ra before OTL so we can verify the correction
    # actually propagates into the modeled range.
    _ra_pre_otl_g = ra.copy()
    _otl_vec_g    = np.zeros(3)
    if blq and tow_total>0.:
        _otl_vec_g = _otl_disp(blq,sta,tow_total,lat_r,lon_r)
        ra = ra + _otl_vec_g

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

    # [OTL_APPLY] GPS — verify OTL displacement actually enters modeled range
    if _nproc_global % 300 == 0:
        _rng_no_otl_g = np.linalg.norm(sva - _ra_pre_otl_g)
        _sl_d=math.sin(lat_r); _cl_d=math.cos(lat_r)
        _sn_d=math.sin(lon_r); _cn_d=math.cos(lon_r)
        _R_d=np.array([[-_sn_d,_cn_d,0.],[-_sl_d*_cn_d,-_sl_d*_sn_d,_cl_d],
                        [_cl_d*_cn_d,_cl_d*_sn_d,_sl_d]])
        _enu_d=_R_d@_otl_vec_g
        pass  # [OTL_APPLY] GPS -> state_diag.csv; console silent

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
    el = None   # initialised here; computed below via _elaz — guards log_osb call

    # v76 PART 1+2+3: Fixed Galileo signal set — C1C/C5Q/L1C/L5Q only.
    # No fallback logic. If required signals absent, skip satellite entirely.
    code1_type, code2_type, phase1_type, phase2_type = 'C1C', 'C5Q', 'L1C', 'L5Q'
    P1_val = so.get('C1C') or 0.
    P5_val = so.get('C5Q') or 0.
    L1_val = so.get('L1C') or 0.
    L5_val = so.get('L5Q') or 0.
    if P1_val == 0. or P5_val == 0. or L1_val == 0. or L5_val == 0.:
        return None  # required signals absent — skip satellite entirely
    if sid not in _sat_signal_map:
        _sat_signal_map[sid] = (code1_type, code2_type, phase1_type, phase2_type)

    P1=P1_val; P5=P5_val; L1=L1_val; L5=L5_val

    ob=osb.get(sid,{})
    # v68 FIX: Apply OSBs ONLY to the signal type that was actually observed.
    b_C1 = ob.get(code1_type, 0.)
    b_C5 = ob.get(code2_type, 0.)
    b_L1 = ob.get(phase1_type, 0.)
    b_L5 = ob.get(phase2_type, 0.)

    # v79 PART 1: require OSBs for all 4 signals.
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

    P1c = P1 - b_C1
    P2c = P5 - b_C5

    # RAW phase observables converted to metres, then phase OSB removed
    lam1 = LAMBDA_E1
    lam2 = LAMBDA_E5A
    L1m  = L1 * lam1 - b_L1
    L2m  = L5 * lam2 - b_L5

    # ── v81 OSB CONSISTENCY (CSV logging, Galileo) ───────────────────────────
    _diff_corr_gal = L1m - P1c
    _diff_raw_gal  = L1 * lam1 - P1
    hist_e = _cp_debug.setdefault(sid, deque(maxlen=100))
    hist_e.append(_diff_corr_gal)
    if DIAG_DEEP and _nproc_global % 300 == 0:
        rng_e = max(hist_e) - min(hist_e) if len(hist_e) > 5 else 0.0
        el_deg = round(math.degrees(el), 2) if el is not None else -1.0
        log_osb([
            _nproc_global,
            sid,
            el_deg,
            _diff_raw_gal,
            _diff_corr_gal,
            rng_e,
            b_C1,
            b_L1,
        ])
    # ── end v81 OSB CONSISTENCY ───────────────────────────────────────────────

    # Ionosphere factor  γ = (f_E1/f_E5a)²
    gamma = FE1SQ / FE5SQ

    # MW and GF — OSB-corrected P1c/P2c + phase OSBs in b_wl_sat_cyc
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
    ra=rxyz+ah*er+_rpco(recx,lat_r,lon_r)
    # PART A diagnostic: capture ra before OTL (Galileo path)
    _ra_pre_otl_e = ra.copy()
    _otl_vec_e    = np.zeros(3)
    if blq and tow_total>0.:
        _otl_vec_e = _otl_disp(blq,sta,tow_total,lat_r,lon_r)
        ra = ra + _otl_vec_e

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

    # [OTL_APPLY] Galileo — verify OTL displacement enters modeled range
    if _nproc_global % 300 == 0:
        _rng_no_otl_e = np.linalg.norm(sva - _ra_pre_otl_e)
        _sl_e=math.sin(lat_r); _cl_e=math.cos(lat_r)
        _sn_e=math.sin(lon_r); _cn_e=math.cos(lon_r)
        _R_e=np.array([[-_sn_e,_cn_e,0.],[-_sl_e*_cn_e,-_sl_e*_sn_e,_cl_e],
                        [_cl_e*_cn_e,_cl_e*_sn_e,_sl_e]])
        _enu_e=_R_e@_otl_vec_e
        pass  # [OTL_APPLY] Gal -> state_diag.csv; console silent

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
                no_AR=no_AR,            # v79: False=AR eligible, True=skip AR
                ar_skip_reason=ar_skip_reason)  # v79: 'no_osb' | 'bad_bias' | None

def _rp(m,dT,ZWD):
    return (m['rng']-m['scm']-m['dtrel']+dT
            +m['trop_zhd']+m['mw']*ZWD
            +m['shp']+m['setm']+m['pcv_sat']+m['pcv_rec'])


# ==============================================================================
#  Circular statistics helpers for NL consistency cluster (v103)
# ==============================================================================
def _nl_circ_mean(vals):
    """Circular mean of fractional-cycle values in (-0.5, 0.5]."""
    if not vals: return 0.0
    angles = [v * 2.0 * math.pi for v in vals]
    s = sum(math.sin(a) for a in angles)
    c = sum(math.cos(a) for a in angles)
    return math.atan2(s, c) / (2.0 * math.pi)

def _nl_circ_diff(a, b):
    """Signed circular difference a − b, result in (-0.5, 0.5]."""
    return (a - b + 0.5) % 1.0 - 0.5

# ==============================================================================
#  PPP Kalman filter pass
# ==============================================================================
def _ppp_pass(epochs,sp3t,sp,sc,clkd,osb,ah,nom,iclk,izwd,lat0,doy,zhd,tref,
              satx,att,recx,elm=math.radians(10.),SC=0.25,SP=0.015,
              direction=1,label="FWD",wl_init=None,amb_init=None,
              constellation='GE',blq=None,sta='IISC',
              force_float=False, zwd_q_scale=1.0):
    # Exp A: force_float=True → pure float PPP (DISABLE_NL_FIXING overridden below)
    # Exp B: zwd_q_scale != 1.0 → scale _ZWD_Q_BASE before first use
    """
    constellation : 'G' | 'E' | 'GE'
    blq           : dict from parse_blq (ocean tide loading)
    sta           : 4-char station code used to look up BLQ entry
    """
    REF=np.array([1337935.5599,6070317.2377,1427877.5071])
    global _osb_dbg_printed, _sat_signal_map, _cp_debug, _osb_once, _nproc_global, _prev_gps_ar_state
    # ── DIAG mode: read module-level flags once per pass ─────────────────────
    _DIAG_DEEP = DIAG_DEEP          # True = full per-epoch diagnostics
    _HUMP_DIAG_INTERVAL = 1 if _DIAG_DEEP else 120   # write hump_diag every N epochs
    # ─────────────────────────────────────────────────────────────────────────
    _osb_dbg_printed = set()
    _sat_signal_map  = {}  # v70: reset signal lock at start of each pass
    _cp_debug        = {}  # v81: per-sat L1m-P1c history (OSB consistency debug)
    _osb_once        = set()  # v81: [OSB_VAL] printed-once guard
    _nproc_global    = 0
    _prev_gps_ar_state = {}  # v84 PART 1: reset AR state-change tracker per pass
    # v101 Fix D: clear per-pass position-jump baseline
    for _attr in list(vars(_ppp_pass).keys()):
        if _attr.startswith('_pos_before_nl_'):
            delattr(_ppp_pass, _attr)
    init_osb_csv()
    wl_fixed=dict(wl_init) if wl_init else {}
    _amb_init=dict(amb_init) if amb_init else {}
    _amb_seeded=set()
    nl_fixed={}
    _nl_R_eff_map = {}   # v88 PART 6: per-sat effective pseudo-obs noise R
    # v102 FIX: per-constellation NL residual clusters (prevent cross-constellation rejection)
    _nl_cluster = []   # v102 CHANGE: single shared residual cluster for all constellations
    # v97: ISB controlled reset — fires once when first reliable NL fix established
    _isb_reinitialized = False

    # ── v54 parameters ────────────────────────────────────────────────────────
    # NL/WL fixing thresholds PRESERVED but NL fixing is DISABLED in v54
    # until Phase 2 validation passes (RAW float convergence confirmed).
    NL_RATIO_THRESH   = 4.5
    NL_VAR_THRESH     = (0.1)**2     # v58: strict gate — was (10.0)² (allowed fixing with huge uncertainty)
    NL_RES_THRESH     = 0.05             # v92 FIX 2: relaxed 0.02→0.05 for debug (temp)
    NL_EXCL_THRESH    = 0.25
    NL_R_TIGHT        = (0.004)**2   # v100 Fix 6: relaxed 2mm→4mm — reduces over-constraint on GPS ambiguities
    NL_INNOV_GATE     = 0.200   # v89: tightened 0.500→0.200 — 0.5 was allowing wrong-integer pseudoobs
    NL_RELEASE_THRESH = 0.080   # v106: raised from 0.050 — 0.050 was releasing valid fixes
    PHASE_SPIKE_THRESH = 2.0    # v102: phase spike health-check threshold (metres) for fixed-sat revalidation
    DISABLE_NL_FIXING = force_float   # Exp A: force_float=True → pure float PPP; v92 default False
    NL_PHASE_THRESH   = 0.010
    NL_MIN_SATS       = 3            # v62: lowered from 7 — apply NL once ≥3 sats fixed
    NL_MIN_OBS        = 8
    PHASE_RES_GATE    = 0.020   # v89: tightened 0.030→0.020 m — inflates Rd earlier on phase outliers
    ZWD_PRIOR         = 0.12
    ZWD_PRIOR_SIGMA   = 0.15   # v103 CHANGE: relaxed 0.06→0.15 m — allows ZWD to absorb real troposphere variation without triggering watchdog
    ZWD_CLAMP         = 0.015
    _zwd_prev         = None
    _nl_diag_done     = False

    # ── DIAG_MODE selector ───────────────────────────────────────────────────
    # 0 = baseline diagnostics only (no experiment modifications)
    # 1 = MODE 1: adaptive GPS Rd downweighting when sigma_U_GPS degrades
    # 2 = MODE 2: tighten ZWD random walk during hump hours (8-18 h)
    # 3 = MODE 3: 3× ionosphere process noise for low-elevation (<20°) sats
    DIAG_MODE = 2

    # ── HUMP DIAGNOSTIC: epoch window (6 h → 19 h in SOD) ───────────────────
    # FIX 1-5: extended window covers both hump peaks (6-10 h and 14-19 h)
    HUMP_SOD_LO = 21600.0          # 6 h
    HUMP_SOD_HI = 68400.0          # 19 h
    # v108: separate hump windows for per-hump RMS diagnostics
    HUMP1_SOD_LO = 25200.0         # 7 h
    HUMP1_SOD_HI = 36000.0         # 10 h
    HUMP2_SOD_LO = 50400.0         # 14 h
    HUMP2_SOD_HI = 64800.0         # 18 h
    # v108: rolling 3D-error accumulators for hump1/hump2 RMS (reset each pass)
    _hump1_d3_sq = []        # list of (3D_mm)² samples in hump1 window
    _hump2_d3_sq = []        # list of (3D_mm)² samples in hump2 window

    # FIX 5: hump correlation audit — track which PRNs are fixed/released during each hump
    _hump1_fixed_sats  = set()   # PRNs fixed at any point during hump1
    _hump1_released    = []      # (SOD, sat) releases during hump1
    _hump1_quarantined = set()   # PRNs quarantined during hump1
    _hump1_churn       = defaultdict(int)  # sat → release count during hump1
    _hump2_fixed_sats  = set()
    _hump2_released    = []
    _hump2_quarantined = set()
    _hump2_churn       = defaultdict(int)
    _hump_audit_printed = False  # printed once after hump2 ends


    # Exp A: boost ALL-satellite iono proc noise 3× during hump window
    # Wired to DIAG_MODE; MODE 0/1/2/3 each isolate one mechanism.
    HUMP_ION_BOOST   = False       # MODE 0/1/2/3: off — MODE 3 uses per-sat logic instead
    HUMP_ION_SCALE   = 3.0         # multiplier (used by Exp A when re-enabled manually)
    # FIX 1: 1/sin^4 weighting active ONLY during hump window (6h-19h); outside → 1/sin^2
    # HUMP_SIG4_WEIGHT=True: _sig4 selected when _in_hump_ep; _sig2 selected otherwise
    HUMP_SIG4_WEIGHT = True        # Flag: [EL4_WEIGHT] regime during hump window
    GEOM_EL4_WEIGHT  = False       # legacy all-day flag — replaced by HUMP_SIG4_WEIGHT
    # Exp B: elevation-dependent 1/sin²(el) downweighting outside hump window
    HUMP_EL2_WEIGHT  = True        # kept on — baseline weighting outside hump
    # FIX 2: 15° elevation mask during hump window only (6h-19h); standard elm outside
    HUMP_ELEV_MASK   = True        # True = 15° mask during hump window; False = no mask
    # Exp D: tighten ZWD random walk during hump window only
    HUMP_ZWD_TIGHTEN = (DIAG_MODE == 2)   # active only in MODE 2
    HUMP_ZWD_Q_TIGHT = 5.0e-11    # m²/s — ≈6× tighter than _ZWD_Q_BASE=2.5e-9

    # MODE 1: adaptive GPS Rd downweighting when GPS dominates Up geometry
    _MODE1_GPS_DOWNWEIGHT = (DIAG_MODE == 1)
    _MODE1_GPS_SIGMA_THRESH = 800.0   # mm — above this sigma_U_GPS triggers downweight
    _MODE1_GPS_RD_SCALE     = 2.25    # variance multiplier on GPS Rd rows (sigma×1.5)
    _prev_sig_u_gps_mm = float('nan')   # per-epoch cached GPS-only sigma_U

    # MODE 3: per-sat low-elevation iono boost
    _MODE3_LOW_EL_ION = (DIAG_MODE == 3)
    _MODE3_ION_SCALE  = 3.0    # multiplier for sats with el < 20°
    _MODE3_EL_THRESH  = math.radians(20.0)
    _mode3_el_cache = {}   # sid → el (radians) from previous epoch

    # ── v54 RAW state vector ──────────────────────────────────────────────────
    # Global:  [x(0), y(1), z(2), clock(3), ZWD(4), ISB_E(5)]
    # Per-sat: [I_s(6+3k), N1_s(7+3k), N2_s(8+3k)]  for satellite k
    # 3 states per satellite instead of 1 IF ambiguity
    # ISB_E (index 5): inter-system bias for Galileo (metres, applied to Galileo obs only)
    _ISB_E_IDX = 5   # index of Galileo ISB in state vector
    x=np.zeros(6); x[3]=iclk; x[4]=izwd; x[5]=0.0  # ISB_E initialised to 0
    P=np.zeros((6,6))
    P[0,0]=P[1,1]=P[2,2]=100.**2; P[3,3]=3000.**2; P[4,4]=0.5**2
    P[5,5]=100.**2   # ISB_E initial variance: ±10 m (loose — let it converge)

    # sidx maps sat_id → base index of its 3-state block [I, N1, N2]
    sidx={}; namb=0; phi={}; wum={}; prev_mw={}; prev_gf={}
    mw_hist=defaultdict(list)
    results={}; psod=None; nproc=0
    _amb_conv_sods=set(); _amb_init_ptrace={}
    _sat_age=defaultdict(int); _amb_snapshots={}
    _wl_history={}; _nl_bad_nwl=set(); _wl_history_ptrace={}
    _sat_last_sod={}
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
    _nl_cf_at_fix = {}   # v103 FIX: sid → corr_frac stored at fix commit time (cluster source)

    # v61: fractional stability tracking (Fix 1, Fix 2, Fix 3).
    # _nl_frac_hist: last 20 raw_frac values used to compute frac_std each epoch.
    # _nl_bias_frozen: set of sids whose bias has converged and must not change.
    # _gps_nl_fixed_ever: whether any GPS satellite has been NL-fixed at least once
    #   (used by Fix 5 to decide when to revert from relaxed gate to tight gate).
    _nl_frac_hist  = defaultdict(lambda: deque(maxlen=20))   # last 20 raw fracs
    _nl_bias_frozen = set()                                   # sids with frozen bias
    _gps_nl_fixed_ever = False                               # Fix 5 state flag
    _last_raw_frac  = {}   # v70-fix1: last raw_frac per sid for drift-rate check
    _nl_last_N1int  = {}   # v89: rounded N1 integer per sat — reset buf when integer jumps
    _nl_jump_count  = {}   # v90: consecutive-epoch jump counter for debounce (reset only after ≥3)
    # v80 PART 4: per-satellite cooldown counter (epochs) before re-fixing is
    # allowed.  Set to 30 when a satellite is released from nl_fixed; prevents
    # rapid fix→release→refix oscillations that destabilise the filter.
    _nl_fix_cooldown = defaultdict(int)   # sid → epochs remaining before re-fix OK
    # v97: NL persistence gate — require condition satisfied ≥3 consecutive epochs
    _nl_persist_count = defaultdict(int)  # sid → consecutive epochs meeting fix criteria
    # v94: one-time lock — sats that have ever been committed to nl_fixed are
    # never re-evaluated or re-fixed, even after a release event.
    _nl_fixed_ever   = set()              # sids committed at least once
    # v94: BEST-4 geometry lock — frozen at the epoch of the first NL fix.
    _best_sats_locked = None              # None until first fix; then frozen set
    # v96: NL-slip protection — consecutive-slip confirmation per satellite.
    # A satellite that is NL-fixed and has sigma_N1_m < 0.08 m requires 3
    # consecutive confirmed slips before its NL fix is cleared.  Single-epoch
    # slips from code/iono noise are ignored so fixed ambiguities remain stable.
    _slip_candidate_count = defaultdict(int)  # sid → consecutive slip epochs
    # FIX: long-arc drift prevention — track sod when each sat was committed.
    _nl_fix_start_sod = {}              # sid → sod at which NL fix was committed
    # FIX: stability-based release — require ≥5 consecutive drifting epochs before release.
    _nl_drift_count   = defaultdict(int)  # sid → consecutive epochs with |innov| > thresh
    # NL_STABLE: sats that have been committed to nl_fixed with sigma_N1_m < 0.10 m.
    # Stable sats skip full NL re-evaluation on non-monitoring epochs (every 30 ep)
    # and only trigger re-evaluation if abs(corr_frac) > 0.05 OR sigma_N1 > 0.10 m.
    _nl_stable = set()   # sids marked NL_STABLE after first committed fix
    # Hard lock: once stable for ≥10 consecutive epochs after bias freeze, stop ALL NL checks.
    _nl_locked       = set()            # sids that are hard-locked — no further NL CHECK
    _nl_stable_count = defaultdict(int) # sid → consecutive epochs with |corr_frac| < 0.02 post-fix
    # ── AMB_AUDIT state ──────────────────────────────────────────────────────
    # Per-satellite rolling history of post-fix phase residual (cycles) and
    # Up-leverage fraction.  Used to detect stale/harmful ambiguity fixes that
    # may be causing the persistent 8–18 h vertical error humps.
    # Diagnostic only — no estimator state is modified.
    _aa_resid_hist  = defaultdict(lambda: deque(maxlen=60))  # sid → deque of |x[ki+1]-N1_i| (cyc)
    _aa_quarantine  = set()   # sids currently quarantined (diagnostic flag only)

    # ── v102-CHURN: per-satellite NL lifetime tracking (FIX 1 + FIX 2) ──────
    # FIX 1: per-satellite lifecycle counters for churn diagnostics
    _nl_release_count  = defaultdict(int)   # sid → total releases this pass
    _nl_refix_count    = defaultdict(int)   # sid → total re-fixes (fix after ≥1 prior release)
    _nl_short_fix_count = defaultdict(int)  # sid → how many fix durations were <300 epochs
    _nl_phase_resid_buf = defaultdict(lambda: deque(maxlen=60))  # sid → rolling |resid| cyc while fixed
    _nl_code_resid_buf  = defaultdict(lambda: deque(maxlen=60))  # sid → rolling code resid mm while fixed
    _nl_up_lev_buf      = defaultdict(lambda: deque(maxlen=30))  # sid → rolling Up leverage while fixed
    # FIX 2: hard quarantine — satellites excluded from NL fixing (still in float filter)
    _nl_quarantine      = set()   # sids permanently quarantined for remainder of pass
    # FIX 3: per-release cooldown extended to 1000 epochs (set at release site below)
    # v80 PART 3: store lam1 per satellite from previous epoch so it is available
    # inside the Q-build loop (which runs before geom is constructed each epoch).
    _sat_lam1 = {}   # sid → lam1 (m/cyc) from last processed epoch
    # v70 IONO FIX 4/6: persistent per-satellite iono diagnostics.
    # _iono_last_dI  — magnitude of iono change in the previous epoch (used to
    #                  inflate Rd for flagged satellites in the NEXT epoch).
    _iono_last_dI   = defaultdict(float)   # sid → |dI| from last epoch (m)
    # v100 Fix 2: slip cool-down — track the nproc epoch index of last confirmed slip
    # per satellite.  NL fixing is blocked for 300 epochs (~2.5 min) after any slip.
    _last_slip_epoch = defaultdict(lambda: -9999)  # sid → nproc index of last slip

    # ── CSV bias logger ───────────────────────────────────────────────────────
    # Replaces all heavy per-epoch console prints with a single lightweight CSV.
    # File is written next to the script; flush every 100 epochs; closed before
    # _ppp_pass returns so the file is always complete even on early exit.
    _bias_csv_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "nl_bias_debug.csv")
    def _safe_open_single(path, retries=5, delay=0.5):
        import time as _time_mod
        for _attempt in range(retries):
            try:
                if os.path.exists(path):
                    os.remove(path)
                return open(path, "w", newline="")
            except PermissionError:
                if _attempt < retries - 1:
                    _time_mod.sleep(delay)
                else:
                    import time as _t
                    _ts = int(_t.time())
                    _base, _ext = os.path.splitext(path)
                    _fallback = f"{_base}_{_ts}{_ext}"
                    print(f"[LOGS] PermissionError on {path!r} — using fallback {_fallback!r}")
                    return open(_fallback, "w", newline="")
    _bias_csv_fh = _safe_open_single(_bias_csv_path)
    _bias_csv_w  = _csv.writer(_bias_csv_fh)
    _bias_csv_w.writerow(["epoch", "sod", "sat",
                          "sigma_N1_cm", "raw_frac", "bias", "corr_frac",
                          "buf_n", "frac_std", "frozen"])

    # ── Structured CSV debug loggers (PART 3-4) ──────────────────────────────
    _logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    # Clean slate: remove entire logs folder then recreate to avoid stale locks
    import shutil as _shutil
    if os.path.exists(_logs_dir):
        try:
            _shutil.rmtree(_logs_dir)
        except Exception as _e:
            print(f"[LOGS] Warning: could not remove logs dir: {_e}")
    os.makedirs(_logs_dir, exist_ok=True)

    def _safe_open_csv(path, retries=5, delay=0.5):
        """Open a CSV file for writing with retry logic for Windows/OneDrive locks."""
        import time as _time_mod
        for _attempt in range(retries):
            try:
                if os.path.exists(path):
                    os.remove(path)
                return open(path, "w", newline="")
            except PermissionError:
                if _attempt < retries - 1:
                    _time_mod.sleep(delay)
                else:
                    # Fallback: use timestamped filename
                    import time as _t
                    _ts = int(_t.time())
                    _base, _ext = os.path.splitext(path)
                    _fallback = f"{_base}_{_ts}{_ext}"
                    print(f"[LOGS] PermissionError on {path!r} — using fallback {_fallback!r}")
                    return open(_fallback, "w", newline="")

    # FILE 1 — nl_debug.csv  (logged when sigma_N1_m < 0.15)
    _nl_debug_fh = _safe_open_csv(os.path.join(_logs_dir, "nl.csv"))
    _nl_debug_w  = _csv.writer(_nl_debug_fh)
    _nl_debug_w.writerow(["SOD", "sat", "raw_frac", "bias", "corr_frac",
                          "sigma_N1_m", "buf_n", "fixed"])

    # FILE 2 — float_diag.csv  (top-6 lowest-sigma sats per epoch)
    _float_diag_fh = _safe_open_csv(os.path.join(_logs_dir, "float_diag.csv"))
    _float_diag_w  = _csv.writer(_float_diag_fh)
    _float_diag_w.writerow(["SOD", "sat", "N1_float", "frac", "sigma_N1_m"])

    # FILE 3 — nl_events.csv  (FIX / RELEASE / SKIP_SIGMA / SKIP_FRAC)
    _nl_events_fh = _safe_open_csv(os.path.join(_logs_dir, "nl_events.csv"))
    _nl_events_w  = _csv.writer(_nl_events_fh)
    _nl_events_w.writerow(["SOD", "sat", "event_type", "corr_frac", "sigma"])

    # FILE 4 — summary.csv  (every epoch, lightweight)
    _summary_fh = _safe_open_csv(os.path.join(_logs_dir, "summary.csv"))
    _summary_w  = _csv.writer(_summary_fh)
    _summary_w.writerow(["SOD", "n_sats", "NL_count", "WL_count",
                         "err3D", "code_rms", "phase_rms"])

    # FILE 5 — isb.csv  (v97: ISB state per epoch)
    _isb_fh = _safe_open_csv(os.path.join(_logs_dir, "isb.csv"))
    _isb_w  = _csv.writer(_isb_fh)
    _isb_w.writerow(["SOD", "ISB_E_m", "P_ISB_m2", "NL_count", "isb_reinitialized"])

    # FILE 6 — fusion_diag.csv  (per-epoch constellation fusion instrumentation)
    # Logs HᵀR⁻¹H Up contribution per constellation, ISB observability metrics,
    # GPS-only position sensitivity, and effective measurement weights.
    _fusion_diag_fh = _safe_open_csv(os.path.join(_logs_dir, "fusion_diag.csv"))
    _fusion_diag_w  = _csv.writer(_fusion_diag_fh)
    _fusion_diag_w.writerow([
        "SOD", "nGPS", "nGal",
        "GPS_NE_up", "Gal_NE_up", "GPS_NE_frac",
        "ISB_val", "ISB_P", "ISB_delta", "ISB_P_reduction",
        "ISB_gain_proxy",
        "GPS_only_sigma_U_mm", "combined_sigma_U_mm", "Gal_leverage_ratio",
        "GPS_ph_rms_mm", "Gal_ph_rms_mm",
        "gal_sig_scale", "nml_sc_gps", "nml_sc_gal",
        "ISB_observable",
    ])

    # FILE 7 — hump_diag.csv  (per-epoch hump-window correlation diagnostics)
    # Logged every epoch; correlates vertical error with ZWD, iono, geometry.
    # Controlled experiments A–D are flagged per row for easy pandas groupby.
    _hump_diag_fh = _safe_open_csv(os.path.join(_logs_dir, "hump_diag.csv"))
    _hump_diag_w  = _csv.writer(_hump_diag_fh)
    _hump_diag_w.writerow([
        "SOD", "in_hump",
        "ZWD_m", "ZWD_innov_m", "ZWD_prior_sigma",
        "mean_iono_var_m2", "max_iono_var_m2", "n_iono_sats",
        "VDOP_ew", "n_sats", "n_low_el",
        "min_el_deg", "mean_el_deg",
        "mean_slant_TEC_m", "max_slant_TEC_m",
        "ph_innov_rms_mm", "code_innov_rms_mm",
        "exp_A_ion_boost", "exp_B_el2", "exp_C_mask15", "exp_D_zwd_tight",
    ])

    # FILE 8 — geom_hump_diag.csv  (geometry conditioning diagnostics, per-epoch)
    # Logs FIX 2 (condNM) and FIX 3 (Up leverage) state every epoch.
    _geom_hump_fh = _safe_open_csv(os.path.join(_logs_dir, "geom_hump_diag.csv"))
    _geom_hump_w  = _csv.writer(_geom_hump_fh)
    _geom_hump_w.writerow([
        "SOD", "condNM", "worst_up_leverage",
        "sat_downweighted",
        "min_el", "mean_el", "n_low_el",
        "sigmaU", "3D_error",
    ])

    # FILE 9 — geom_zwd_hump_diag.csv  (FIX 5: geometry+ZWD hump experiment diagnostics)
    # Written every epoch; used to correlate VDOP_ew peaks with hump peaks.
    _gzh_fh = _safe_open_csv(os.path.join(_logs_dir, "geom_zwd_hump_diag.csv"))
    _gzh_w  = _csv.writer(_gzh_fh)
    _gzh_w.writerow([
        "SOD", "VDOP_ew", "corr_U_ZWD", "n_low_el",
        "min_el", "mean_el", "Up_bias_mm", "3D_error_mm", "mask_exclusions",
    ])
    _gzh_vdop_hist = []   # (vdop_ew, d3_mm, in_hump) for end-of-pass correlation

    # ── DIAG_MODE extra CSV files (logged every 300 epochs) ─────────────────
    _vdop_diag_fh = _safe_open_csv(os.path.join(_logs_dir, "vdop_diag.csv"))
    _vdop_diag_w  = _csv.writer(_vdop_diag_fh)
    _vdop_diag_w.writerow([
        "SOD", "PDOP", "VDOP_ew",
        "sigma_U_GPS_mm", "sigma_U_comb_mm",
        "n_sats", "diag_mode",
    ])

    _zwd_hump_fh = _safe_open_csv(os.path.join(_logs_dir, "zwd_hump_diag.csv"))
    _zwd_hump_w  = _csv.writer(_zwd_hump_fh)
    _zwd_hump_w.writerow([
        "SOD", "in_hump",
        "ZWD_m", "ZWD_innov_m", "corr_U_ZWD",
        "q_zwd_m2s", "decorr_active",
        "sigma_U_mm", "diag_mode",
    ])

    _ion_hump_fh = _safe_open_csv(os.path.join(_logs_dir, "ion_hump_diag.csv"))
    _ion_hump_w  = _csv.writer(_ion_hump_fh)
    _ion_hump_w.writerow([
        "SOD", "in_hump",
        "mean_iono_var_m2", "max_iono_var_m2", "n_iono_sats",
        "mean_el_deg", "ion_proc_noise_eff",
        "diag_mode",
    ])

    _fusion_obs_fh = _safe_open_csv(os.path.join(_logs_dir, "fusion_obs_diag.csv"))
    _fusion_obs_w  = _csv.writer(_fusion_obs_fh)
    _fusion_obs_w.writerow([
        "SOD", "GPS_up_frac", "Gal_up_frac",
        "corr_U_ZWD", "mean_iono_var_m2",
        "gal_leverage", "cond_nm",
        "sigma_U_comb_mm", "sigma_U_GPS_mm",
        "diag_mode",
    ])

    # ── TRANSITION AUDIT: per-epoch state + event logs ────────────────────────
    _ta_fh = _safe_open_csv(os.path.join(_logs_dir, "transition_audit.csv"))
    _ta_w  = _csv.writer(_ta_fh)
    _ta_w.writerow([
        "SOD", "nGPS", "nGal", "NL_count", "WL_count", "n_sats",
        "GPS_NE_frac", "gal_sig_scale", "Gal_leverage",
        "sigma_U_comb_mm", "sigma_U_GPS_mm",
        "ISB_m", "P_ISB_m2", "ISB_observable",
        "ZWD_m", "P_ZWD_m2", "corr_U_ZWD", "corr_mean_60ep",
        "Q_ZWD_m2s", "decorr_active",
        "trace_P_pos_m2", "nml_sc_gps", "nml_sc_gal",
    ])
    _te_fh = _safe_open_csv(os.path.join(_logs_dir, "transition_events.csv"))
    _te_w  = _csv.writer(_te_fh)
    _te_w.writerow(["SOD", "event_type", "sat", "value", "detail"])

    # ── DIAG FAST: state_diag.csv (every 60 epochs) ──────────────────────────
    _sd_fh = _safe_open_csv(os.path.join(_logs_dir, "state_diag.csv"))
    _sd_w  = _csv.writer(_sd_fh)
    _sd_w.writerow([
        "SOD", "3D_error_mm", "U_error_mm", "NL_count",
        "condNM", "corr_U_ZWD", "nGPS", "nGal",
        "dominant_sat", "up_leverage",
    ])
    _sd_buf = []   # buffered rows; flushed every 100 epochs

    # ── DIAG FAST: event_diag.csv (state-change events only) ─────────────────
    _ed_fh = _safe_open_csv(os.path.join(_logs_dir, "event_diag.csv"))
    _ed_w  = _csv.writer(_ed_fh)
    _ed_w.writerow(["SOD", "event", "sat", "detail"])
    _ed_buf = []   # buffered rows
    # ─────────────────────────────────────────────────────────────────────────

    # FILE: amb_audit.csv — per-epoch per-fixed-sat ambiguity influence audit
    # Diagnostic only; no estimator state is touched.
    _aa_fh = _safe_open_csv(os.path.join(_logs_dir, "amb_audit.csv"))
    _aa_w  = _csv.writer(_aa_fh)
    _aa_w.writerow([
        "SOD", "sat", "fix_age_s",
        "ph_resid_cyc",
        "ph_resid_rms30",
        "up_leverage_frac",
        "loo_up_shift_mm",
        "flag_high_leverage",
        "flag_mono_drift",
        "quarantined",
    ])

    # FILE: sat_churn_diag.csv — per-epoch per-fixed-sat NL lifetime monitor (FIX 1)
    _churn_fh = _safe_open_csv(os.path.join(_logs_dir, "sat_churn_diag.csv"))
    _churn_w  = _csv.writer(_churn_fh)
    _churn_w.writerow([
        "SOD", "sat", "fixed",
        "fix_age_ep", "release_count", "refix_count",
        "phase_rms_cyc", "code_rms_mm",
        "up_leverage", "err3D_mm",
        "quarantined",
    ])
    # Per-epoch cache for fusion_diag quantities (updated inside try block each epoch)
    _ta_fd_n_gps   = 0;  _ta_fd_n_gal = 0
    _ta_fd_gps_frac = float('nan')
    _ta_sigma_u_gps = float('nan');  _ta_sigma_u_comb = float('nan')
    _ta_gal_lev = float('nan');  _ta_isb_obs = 0
    # ─────────────────────────────────────────────────────────────────────────

    # v54: Phase 2 debug — track L1m−P1c and L2m−P2c for one reference sat
    _DBG_SAT   = None       # will be set to first GPS sat seen
    _dbg_lp1   = []         # (sod, L1m-P1c) history
    _dbg_lp2   = []         # (sod, L2m-P2c) history
    _DBG_PRINT_INTERVAL = 120   # print summary every N epochs

    b_rec_frozen={}; b_rec_n=defaultdict(int)

    # PART B — adaptive Galileo noise scale derived from running residual ratio.
    # Starts at 1.0; no hardcoded multiplier — fully data-driven from residuals.
    # Updated every 300 epochs from empirical GPS/Galileo phase residual variance.
    _gal_scale_adaptive = 1.0   # effective Galileo Rd scale = _gal_scale_adaptive (no 1.2 floor)
    # PATCH 1: freeze gal_sig_scale after first stable NL cluster (≥3 fixed for 60 epochs)
    _gal_scale_frozen = False
    _nl_stable_freeze_ctr = 0
    _SCALE_FREEZE_EPOCHS = 60      # epochs with nl_fixed≥3 before freezing
    # PATCH 2 (v108): replaced old 10%-two-window hysteresis with new mechanism:
    #   • Low-pass smoothing:  new = 0.95*old + 0.05*target
    #   • Hard slew-rate cap:  |Δ| ≤ _GAL_SCALE_MAX_SLEW per update
    #   • 10-epoch event guard: block update if any recent NL/WL fix, slip, release,
    #     or nGal change-by->1 in the last _GAL_SCALE_GUARD_EPOCHS epochs
    #   • Geometry freeze:     if cond(NM_pos)>1000 OR hump_risk>0.25 → hold
    _scale_hysteresis_pending = float('nan')   # kept for CSV/logging compat
    _scale_hysteresis_ctr = 0                    # kept for CSV/logging compat
    _GAL_SCALE_LP_ALPHA = 0.05   # low-pass update weight
    _GAL_SCALE_MAX_SLEW = 0.01   # max |Δ| per update cycle
    _GAL_SCALE_GUARD_EPOCHS = 10     # look-back window for event guard
    # Rolling 10-epoch event flag deque (True = "turbulent epoch" during that epoch)
    import collections as _collections
    _gal_scale_event_buf: _collections.deque = _collections.deque(
        [False] * _GAL_SCALE_GUARD_EPOCHS, maxlen=_GAL_SCALE_GUARD_EPOCHS)
    _gal_scale_prev_nGal = 0    # for nGal change detection
    _gal_scale_prev_nl_fixed = 0  # for new-fix detection
    _gal_scale_prev_nl_set = set()  # sids fixed at end of previous epoch
    _isb_gps_ph2_acc = [] # GPS phase residuals² accumulated per epoch
    _isb_gal_ph2_acc = [] # Galileo phase residuals² accumulated per epoch
    # ── Fix 4: ISB hold — prevent P_ISB blow-up when nGPS drops ─────────────
    # When GPS count collapses the ISB becomes unobservable; the KF variance
    # inflates unboundedly and the NL gate blocks all Galileo.  Instead: hold
    # the last valid ISB estimate and clamp P_ISB to avoid pseudo-reset spikes.
    _isb_last_valid = float('nan')   # last ISB value when nGPS >= 3
    _P_isb_last_valid = float('nan') # corresponding P_ISB
    _ISB_P_CAP = 0.50               # max P_ISB allowed (m²) — caps variance inflation
    _ISB_GPS_MIN = 3                  # minimum GPS sats for ISB to be observable
    # ── Fix 3: ZWD/Up coupling audit vars ───────────────────────────────────
    _corr_u_zwd_history = []         # rolling corr(U,ZWD) for continuous audit
    _ZWD_Q_BASE = 2.5e-9 * zwd_q_scale  # Exp B: zwd_q_scale scales random-walk noise; nominal=1.0
    _ZWD_Q_REDUCED = 5.0e-10      # reduced ZWD process noise when |corr|>0.3
    _zwd_decorr_active = False       # True when reduced ZWD Q is in effect

    # ── NML Up-balancing state (v102-bal) ────────────────────────────────────
    # Adaptive per-epoch Rd scaling applied only when normal-equation Up
    # contribution is heavily dominated by one constellation (>60%).
    # Bounded: 0.80 <= scale <= 1.25; smoothed with EMA to avoid abrupt jumps.
    # Applied by multiplying Rd for ALL rows of the dominant constellation.
    # Does NOT touch H matrix, NL logic, ambiguity gates, ISB Q, ION noise, OTL.
    _nml_scale_gps = 1.0   # current Rd multiplier for GPS rows (smoothed)
    _nml_scale_gal = 1.0   # current Rd multiplier for Galileo rows (smoothed)
    _NML_SCALE_MIN = 0.90   # v106: tightened from 0.60 — prevents extreme GPS/Gal suppression
    _NML_SCALE_MAX = 1.10   # v106: tightened from 1.80 — bounds oscillation amplitude
    _NML_DOM_THRESH = 0.60  # constellation Up-fraction threshold for re-balancing
    _NML_SMOOTH = 0.95      # v106: slowed from 0.70 — prevents 10ks oscillation cycle
    # PATCH 3/5: envelope limits and last-known risk for inter-window carry-forward
    _GPS_FRAC_MIN = 0.35    # hard lower bound on GPS Up fraction (35–65% envelope)
    _GPS_FRAC_MAX = 0.65    # hard upper bound on GPS Up fraction
    _nml_last_hump_risk = 0.0   # carry-forward from last 300-epoch diagnostic window
    _nml_last_cond_bal = float('nan')  # v108: carry-forward cond(NM_pos) for geom-freeze
    # Running Up normal-eq accumulators for inter-epoch smoothing
    _nm_u_gps_ema = 0.0
    _nm_u_gal_ema = 0.0
    _NML_EMA_SMOOTH = 0.85  # EMA for the Up-NE estimates themselves
    _nml_bal_epoch_ctr = 0    # diagnostic print counter

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

        n_st=len(x); Q=np.zeros((n_st,n_st))
        Q[0,0]=Q[1,1]=Q[2,2]=1e-8*dt; Q[3,3]=1e4*dt
        # ZWD process noise: ~3 mm/sqrt(hr) ≈ 2.5e-9*dt
        # Fix 3: use reduced Q when Up/ZWD are strongly coupled to break hump
        _q_zwd_now = _ZWD_Q_REDUCED if _zwd_decorr_active else _ZWD_Q_BASE
        # Exp D: tighten ZWD random walk during 8-18h to suppress spurious ZWD drift
        if HUMP_ZWD_TIGHTEN and HUMP_SOD_LO <= sod <= HUMP_SOD_HI:
            _q_zwd_now = min(_q_zwd_now, HUMP_ZWD_Q_TIGHT)
        Q[4,4] = _q_zwd_now * dt
        # ISB_E process noise: v103 — 1e-6 m²/s (non-static; allows slow ISB drift tracking)
        Q[_ISB_E_IDX,_ISB_E_IDX]=1e-6*dt
        # v54 RAW per-satellite state noise:
        #   I (ionosphere): small process noise to allow slow drift
        #   N1, N2 (ambiguities): zero process noise (carrier phase constants)
        # v56 FIX 1 (CRITICAL): q_iono increased 100× for equatorial station IISC.
        # IISC is inside the EIA; L1 STEC varies 20–50 m per pass.
        # With 1e-6/s the ionosphere froze after ~50 epochs (P[ki,ki]→0.02 m²);
        # position absorbed the unfrozen residual.  1e-4/s gives σ_I_ss≈0.34 m,
        # enough bandwidth to track equatorial rate ≈ 0.05–0.5 m/epoch.
        # For extreme scintillation epochs, raise to ION_PROC_NOISE=1e-3.
        ION_PROC_NOISE = 1e-4              # m²/s — v104 ISOLATION: doubled to 1e-4 for equatorial ionosphere dynamics test
        # Exp A: boost iono process noise during 8-18h to allow faster iono tracking
        if HUMP_ION_BOOST and HUMP_SOD_LO <= sod <= HUMP_SOD_HI:
            ION_PROC_NOISE = ION_PROC_NOISE * HUMP_ION_SCALE
        q_iono = ION_PROC_NOISE * dt       # I base process noise (elevation-weighted extra added below)
        q_N1N2=0.0                        # ambiguities are constants
        for sid_k,ki in sidx.items():
            # MODE 3: 3× iono process noise for low-elevation sats (uses previous epoch elevations)
            _q_iono_k = q_iono
            if _MODE3_LOW_EL_ION:
                _el_k = _mode3_el_cache.get(sid_k, math.radians(45.0))
                if _el_k < _MODE3_EL_THRESH:
                    _q_iono_k = q_iono * _MODE3_ION_SCALE
            Q[ki  ,ki  ]=_q_iono_k        # I slot
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
                    # v97 FIX: q_N1 = 1e-6 always — allows slight adaptability,
                    # prevents over-constraint on long arcs, no artificial 3h boost.
                    Q[ki+1, ki+1] = 1e-6
                    Q[ki+2, ki+2] = 1e-6
        P+=Q
        # FIX: prevent ambiguity covariance from freezing at zero —
        # enforce a minimum so filter_standard always has leverage on N1/N2 states
        for _sid_f, _ki_f in sidx.items():
            P[_ki_f+1, _ki_f+1] = max(P[_ki_f+1, _ki_f+1], 1e-5)
            P[_ki_f+2, _ki_f+2] = max(P[_ki_f+2, _ki_f+2], 1e-5)

        rxyz=nom+x[:3]; sun=_sun(tow); geom=[]
        _ep_gps_ppp = 0; _ep_gps_no_ar = 0   # per-epoch GPS PPP/AR counters
        # Per-epoch hump window flag (FIX 1-5: 6h-19h)
        _in_hump_ep = (HUMP_SOD_LO <= sod <= HUMP_SOD_HI)
        # FIX 2: 15° elevation mask ONLY during hump window; standard elm outside
        _elm_ep = math.radians(15.) if (HUMP_ELEV_MASK and _in_hump_ep) else elm
        _mask_exclusions_ep = 0  # filled after geom is built

        for sid,so in sorted(sobs.items()):
            if sid[0] not in ('G','E'): continue
            if sid[0] not in constellation: continue

            if sid[0]=='E':
                m=_proc_gal(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,_elm_ep,
                             satx,att,recx,blq=blq,sta=sta,tow_total=tow_total)
            else:
                m=_proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,_elm_ep,
                         satx,att,recx,blq=blq,sta=sta,tow_total=tow_total)
            if m is None: continue

            # ── CYCLE SLIP ───────────────────────────────────────────────────
            slip=False
            _raw_slip_detected=False
            if sid in prev_mw:
                dGF=m['GF_m']-prev_gf[sid]; dMW=m['MW_cyc']-prev_mw[sid]
                if abs(dGF)>0.05 or abs(dMW)>1.5:
                    if sid in _amb_seeded:
                        _amb_seeded.discard(sid)
                    else:
                        _raw_slip_detected=True
                        # ── v96 NL PROTECTION: if NL-fixed AND sigma_N1_m < 0.08 m,
                        # require 3 consecutive confirmed slips before resetting.
                        _sigma_slip_m=0.0
                        if sid in sidx:
                            _ki_slip=sidx[sid]
                            _lam_slip=_sat_lam1.get(sid,0.1903)
                            _sigma_slip_m=math.sqrt(max(0.,P[_ki_slip+1,_ki_slip+1]))*_lam_slip
                        _nl_protected=(sid in nl_fixed and _sigma_slip_m<0.08)
                        if _nl_protected:
                            _slip_candidate_count[sid]+=1
                            if _slip_candidate_count[sid]<3:
                                # Not yet confirmed — suppress reset this epoch
                                _raw_slip_detected=False
                            else:
                                # 3 consecutive confirmed slips → genuine slip
                                slip=True
                                _slip_candidate_count[sid]=0
                        else:
                            slip=True
                            _slip_candidate_count[sid]=0
                        if slip:
                            wl_fixed.pop(sid,None); mw_hist[sid].clear()
                            _last_slip_epoch[sid] = nproc   # v100 Fix 2: slip cool-down
                            # ── New-arc detection (FIX #1) ──────────────────
                            _prev_sod=_sat_last_sod.get(sid)
                            if _prev_sod is None or (sod-_prev_sod)>120.:
                                _wl_history.pop(sid,None)
                                _wl_history_ptrace.pop(sid,None)
            if not _raw_slip_detected:
                _slip_candidate_count[sid]=0   # reset on clean epoch
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

                        mn_corr=mn-b_rec; NWL=round(mn_corr)
                        residual=abs(mn_corr-NWL)
                        if sys_id not in b_rec_frozen:
                            pass
                        elif sd<0.25 and residual<0.20:
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
                                    # ── FIX #1 (part 2) ────────────────────
                                    # diff ≤ 3 cyc on a short-gap false slip:
                                    # pure MW noise on the SAME arc. Keep history.
                                    # (If this were a genuine new arc the gap was
                                    # >120 s and _wl_history was already cleared
                                    # in the slip-detection block above — so we
                                    # cannot reach this branch for new arcs.)
                                    print(f"[WL PERSIST] {sid} using prev "
                                          f"NWL={hist_NWL} (same-arc noise: "
                                          f"new={NWL}, diff={diff}<=3->keep)")
                                    NWL_to_use=hist_NWL
                                else:
                                    # diff > 3 but history still present:
                                    # b_rec shifted slightly → accept new NWL
                                    print(f"[WL UPDATE] {sid} NWL {hist_NWL}"
                                          f"→{NWL} (diff={diff}>3)")
                                    _wl_history[sid]=NWL
                                    _wl_history_ptrace[sid]=pt_now
                                    NWL_to_use=NWL
                            else:
                                # No prior history → brand new fix (or cleared after gap)
                                _wl_history[sid]=NWL
                                _wl_history_ptrace[sid]=pt_now
                            wl_fixed[sid]=NWL_to_use
                            print(f"[WL FIXED] {sid}  N_WL={NWL_to_use}  "
                                  f"mean={mn_corr:.3f}  std={sd:.3f} "
                                  f"b_rec={b_rec:+.3f}({tag}) cyc")
                            _te_w.writerow([f"{sod:.1f}", "WL_FIX", sid,
                                            f"{NWL_to_use}",
                                            f"mean={mn_corr:.3f} std={sd:.3f}"])

            # ── v54 RAW STATE ALLOCATION: 3 slots per sat [I, N1, N2] ─────────
            if sid not in sidx:
                d=len(x)
                # Append 3 new states: I, N1, N2
                x=np.append(x,[0.,0.,0.])
                Pn=np.zeros((d+3,d+3)); Pn[:d,:d]=P
                Pn[d,  d  ]=300.**2   # I  initial var
                Pn[d+1,d+1]=300.**2   # N1 initial var
                Pn[d+2,d+2]=300.**2   # N2 initial var
                P=Pn; sidx[sid]=d; namb+=1; phi[sid]=False
            ki=sidx[sid]  # ki → I slot; ki+1 → N1 slot; ki+2 → N2 slot

            if slip:
                # Reset all 3 states on cycle slip
                x[ki]=0.; x[ki+1]=0.; x[ki+2]=0.
                P[ki,ki]=300.**2; P[ki+1,ki+1]=300.**2; P[ki+2,ki+2]=300.**2
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
                _nl_locked.discard(sid)        # release hard lock on slip/WL reset
                _nl_stable_count[sid] = 0

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

            # ── v54 RAW STATE INIT [I, N1, N2] ──────────────────────────────
            if not phi.get(sid,False):
                rp0=_rp(m,x[3],x[4])        # geometric + corrections (no ionosphere)
                gam=m['gamma']

                # I initialised from geometry-free pseudorange
                I_init=(m['P2c']-m['P1c'])/(gam-1.)
                x[ki]=I_init; P[ki,ki]=(50.*np.sqrt(5))**2   # v104 ISOLATION: 5× looser init (→ 250 m² variance) to test ionosphere observability

                # N1 init: L1mc = rp0 − I + λ1·N1  →  N1 = (L1mc − rp0 + I)/λ1
                x[ki+1]=(L1mc - rp0 + I_init)/lam1
                P[ki+1,ki+1]=300.**2

                # N2 init: L2mc = rp0 − γI + λ2·N2  →  N2 = (L2mc − rp0 + γI)/λ2
                x[ki+2]=(L2mc - rp0 + gam*I_init)/lam2
                P[ki+2,ki+2]=300.**2

                phi[sid]=True; _sat_age[sid]=0
                pt_now=P[0,0]+P[1,1]+P[2,2]; _amb_init_ptrace[sid]=pt_now
                if pt_now<0.30: _amb_conv_sods.add(sid)

            # ── v57 debug: L1m−P1c / L2m−P2c tracking (suppressed — kept in _dbg_lp1/2 buffers)
            if _DBG_SAT is None and sid[0]=='G':
                _DBG_SAT = sid
            if sid == _DBG_SAT:
                _dbg_lp1.append((sod, m['L1m']-m['P1c']))
                _dbg_lp2.append((sod, m['L2m']-m['P2c']))

            _sat_age[sid]+=1
            # v58 FIX 2: elevation-weighted iono process noise.
            # The flat q_iono*dt was already added via P+=Q above; now add the
            # elevation-dependent extra so total = q_iono/sin²(el)*dt per sat.
            # Low-elevation sats (el≈15°) receive ~15× more iono noise, which is
            # physically correct for an equatorial site like IISC.
            _sel = max(math.sin(m['el']), 0.1)
            P[ki, ki] += q_iono * (1.0/_sel**2 - 1.0)
            m['ki']=ki; m['NWL']=wl_fixed.get(sid,None); m['age']=_sat_age[sid]
            geom.append(m)
            _sat_lam1[sid] = m['lam1']   # v80 PART 3: cache lam1 for next epoch's Q loop
            if sid[0] == 'G':            # lightweight per-epoch GPS usage counter
                if m.get('no_AR', False):
                    _ep_gps_no_ar += 1
                else:
                    _ep_gps_ppp += 1

        if len(geom)<4: continue
        if len(geom)>4:
            if _pdop(geom)>6.0:
                worst=min(geom,key=lambda m:m['el'])
                geom=[m for m in geom if m['sid']!=worst['sid']]
        if len(geom)<4: continue

        # FIX 2: count sats excluded by 15° mask during hump window
        if HUMP_ELEV_MASK and _in_hump_ep:
            _geom_sids_ep = {m['sid'] for m in geom}
            _mask_exclusions_ep = sum(
                1 for _sid_mx in sobs
                if _sid_mx[0] in ('G', 'E') and _sid_mx[0] in constellation
                and _sid_mx not in _geom_sids_ep
            )
            if nproc % 300 == 0 and _mask_exclusions_ep > 0:
                print(f"[MASK15] SOD={sod:.0f} hump_mask=15° excluded={_mask_exclusions_ep} sats")
        # FIX 1: log sin^4 weighting activation during hump window
        if HUMP_SIG4_WEIGHT and _in_hump_ep and nproc % 300 == 0:
            print(f"[EL4_WEIGHT] SOD={sod:.0f} 1/sin^4 weighting active n_sats={len(geom)}")

        # MODE 3: update elevation cache for next epoch's Q-build
        if _MODE3_LOW_EL_ION:
            for _m3 in geom:
                _mode3_el_cache[_m3['sid']] = _m3['el']

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
            # Re-init I, N1, N2 with updated clock estimate
            for m in geom:
                ki=m['ki']; rp0=_rp(m,x[3],x[4]); gam=m['gamma']
                I_re=(m['P2c']-m['P1c'])/(gam-1.)
                x[ki]=I_re
                x[ki+1]=(m['L1mc']-rp0+I_re)/m['lam1']
                x[ki+2]=(m['L2mc']-rp0+gam*I_re)/m['lam2']

        # ── v54 RAW measurement model ─────────────────────────────────────────
        # 4 observations per satellite:
        #   P1: rp + I
        #   P2: rp + γ·I
        #   L1: rp − I + λ1·N1
        #   L2: rp − γ·I + λ2·N2
        # where rp = ρ + c·dT + T  (geometry + clock + troposphere)
        # State layout: [x,y,z,clk,ZWD | I_s,N1_s,N2_s per sat]
        ns=len(geom); nst=len(x)
        n_wl=sum(1 for m in geom if m['NWL'] is not None)
        n_nl_cur=sum(1 for m in geom if m['sid'] in nl_fixed)
        n_obs=4*ns   # P1, P2, L1, L2 per satellite

        H=np.zeros((n_obs,nst))
        z=np.zeros(n_obs); Rd=np.zeros(n_obs)
        xs=x.copy()

        for ri,m in enumerate(geom):
            ki=m['ki']        # I slot
            kn1=ki+1          # N1 slot
            kn2=ki+2          # N2 slot
            u=m['unit']; mw=m['mw']; gam=m['gamma']
            lam1=m['lam1']; lam2=m['lam2']
            rp=_rp(m,xs[3],xs[4])   # geometry term (no iono)
            I_s=xs[ki]; N1_s=xs[kn1]; N2_s=xs[kn2]

            base=4*ri

            # ISB_E flag: Galileo observations include the inter-system bias state
            _is_gal = (m['sid'][0] == 'E' and 'GE' in constellation)
            # Galileo noise scale: fully adaptive from residual ratio — no hardcoded 1.2× floor
            _gal_sig_scale = _gal_scale_adaptive if _is_gal else 1.0
            # NML Up-balance scale: geometry-adaptive constellation Rd multiplier
            # Multiplies sigma (not variance) so Rd is scaled by square of this.
            _nml_rd_scale = (_nml_scale_gal if _is_gal else _nml_scale_gps) \
                            if 'GE' in constellation else 1.0
            _gal_sig_scale = _gal_sig_scale * _nml_rd_scale
            # Current ISB_E estimate (zero for GPS or single-constellation)
            _isb_e = xs[_ISB_E_IDX] if _is_gal else 0.0
            # FIX 1: 1/sin^4 weighting during hump window (6h-19h) only; 1/sin^2 outside
            # _sig4: sigma=s0/sin^2 → Rd=s0^2/sin^4; active when HUMP_SIG4_WEIGHT and _in_hump_ep
            _el_sig_fn = (_sig4 if (HUMP_SIG4_WEIGHT and _in_hump_ep)
                          else (_sig2 if HUMP_EL2_WEIGHT else _sig))

            # ── P1 row: P1c = rp + I ──────────────────────────────────────
            r=base
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]; H[r,3]=1.; H[r,4]=mw
            H[r,ki]=1.                          # +I
            if _is_gal:
                H[r,_ISB_E_IDX]=1.             # +ISB_E for Galileo
            z[r]=m['P1c']-(rp+I_s+_isb_e)
            Rd[r]=(_el_sig_fn(m['el'],SC)*_gal_sig_scale)**2

            # ── P2 row: P2c = rp + γ·I ───────────────────────────────────
            r=base+1
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]; H[r,3]=1.; H[r,4]=mw
            H[r,ki]=gam                         # +γI
            if _is_gal:
                H[r,_ISB_E_IDX]=1.             # +ISB_E for Galileo
            z[r]=m['P2c']-(rp+gam*I_s+_isb_e)
            # v56 FIX 2: P2 noise = same as P1 (both P-code; γ² scaling removed).
            # γ² was reducing P2's ionosphere SNR by γ, slowing I convergence.
            Rd[r]=(_el_sig_fn(m['el'],SC)*_gal_sig_scale)**2

            # ── L1 row: L1mc = rp − I + λ1·N1 ───────────────────────────
            r=base+2
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]; H[r,3]=1.; H[r,4]=mw
            H[r,ki]=-1.                         # −I
            H[r,kn1]=lam1                       # +λ1·N1 (cycle → metres)
            if _is_gal:
                H[r,_ISB_E_IDX]=1.             # +ISB_E for Galileo
            pred_L1=rp - I_s + lam1*N1_s + _isb_e
            z[r]=m['L1mc']-pred_L1
            phase_sig=_el_sig_fn(m['el'],SP)*(5. if m.get('age',99)<=3 else 1.)*_gal_sig_scale
            Rd[r]=phase_sig**2
            if abs(z[r])>PHASE_RES_GATE: Rd[r]=max(Rd[r], (1.0*_gal_sig_scale)**2)

            # ── L2 row: L2mc = rp − γ·I + λ2·N2 ─────────────────────────
            r=base+3
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]; H[r,3]=1.; H[r,4]=mw
            H[r,ki]=-gam                        # −γI
            H[r,kn2]=lam2                       # +λ2·N2
            if _is_gal:
                H[r,_ISB_E_IDX]=1.             # +ISB_E for Galileo
            pred_L2=rp - gam*I_s + lam2*N2_s + _isb_e
            z[r]=m['L2mc']-pred_L2
            # v56 FIX 3: L2 phase noise = same as L1 in metres (γ scaling removed).
            # L1 and L2 carrier-phase noise are similar in m; γ is a frequency
            # ratio, not a noise ratio.  Outlier floor also normalised to 1 m.
            phase_sig2=_el_sig_fn(m['el'],SP)*(5. if m.get('age',99)<=3 else 1.)*_gal_sig_scale
            Rd[r]=phase_sig2**2
            if abs(z[r])>PHASE_RES_GATE: Rd[r]=max(Rd[r], (1.0*_gal_sig_scale)**2)

            # v71 FIX 3: dynamic Rd×10 inflation for iono-unstable sats REMOVED.
            # Inflating code AND phase rows was masking code/phase imbalance and
            # preventing natural ionosphere convergence.  Let the filter run clean.
            # [COMMENTED OUT] if _iono_last_dI.get(m['sid'], 0.0) > 5.0:
            #     for _inf_r in range(base, base + 4):
            #         Rd[_inf_r] *= 10.0

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
                # phase rows: base+2 (L1) and base+3 (L2)
                Rd[base+2] *= 4.0    # sigma×2 → variance×4
                Rd[base+3] *= 4.0
                # code rows: base+0 (P1) and base+1 (P2)
                Rd[base]   *= 4.0
                Rd[base+1] *= 4.0

            # MODE 1: adaptive GPS Rd inflation when GPS-only sigma_U is degraded.
            # Uses previous-epoch cached sigma_U_GPS to avoid circularity.
            if (_MODE1_GPS_DOWNWEIGHT and m['sid'][0] == 'G'
                    and not math.isnan(_prev_sig_u_gps_mm)
                    and _prev_sig_u_gps_mm > _MODE1_GPS_SIGMA_THRESH):
                for _m1r in range(base, base + 4):
                    Rd[_m1r] *= _MODE1_GPS_RD_SCALE

        # ZWD soft prior pseudo-obs (unchanged from v53)
        n_total=n_obs
        H_p=np.zeros((n_total+1,nst)); z_p=np.zeros(n_total+1); Rd_p=np.zeros(n_total+1)
        H_p[:n_total,:]=H; z_p[:n_total]=z; Rd_p[:n_total]=Rd
        H_p[n_total,4]=1.
        z_p[n_total]=ZWD_PRIOR-xs[4]
        Rd_p[n_total]=ZWD_PRIOR_SIGMA**2

        # ── HUMP DIAG: capture pre-update innovations ────────────────────────
        # Only needed on epochs where hump_diag.csv will be written.
        _hd_ph_innov   = []   # L1/L2 pre-update innovations (m)
        _hd_code_innov = []   # P1/P2 pre-update innovations (m)
        if _DIAG_DEEP or (nproc % _HUMP_DIAG_INTERVAL == 0):
            for _hd_i in range(ns):
                _hd_base = 4 * _hd_i
                _hd_code_innov.append(z[_hd_base])     # P1
                _hd_code_innov.append(z[_hd_base + 1]) # P2
                _hd_ph_innov.append(z[_hd_base + 2])   # L1
                _hd_ph_innov.append(z[_hd_base + 3])   # L2
        # ZWD prior innovation (before update)
        _hd_zwd_innov = float(ZWD_PRIOR - xs[4])
        # ── end pre-update capture ────────────────────────────────────────────

        # ── GEOM FIX 2: Normal matrix conditioning guard ──────────────────────
        # Build NM_pos from current Rd_p; if ill-conditioned, downweight the
        # lowest-elevation satellite's 4 rows.  No satellite removed.
        _geom_cond_nm     = float('nan')
        _geom_cond_sat_dw = ""          # satellite downweighted by FIX 2
        _geom_cond_infl   = 1.0
        try:
            _Rinv_gc = 1.0 / np.maximum(Rd_p, 1e-30)
            _Hpos_gc = H_p[:, :5]
            _NM_gc   = (_Hpos_gc * _Rinv_gc[:, np.newaxis]).T @ _Hpos_gc
            _sv_gc   = np.linalg.svd(_NM_gc + np.eye(5) * 1e-12, compute_uv=False)
            _geom_cond_nm = float(_sv_gc[0] / max(_sv_gc[-1], 1e-30))
            if _geom_cond_nm > 500 and geom:
                _gc_infl = 10.0 if _geom_cond_nm > 1000 else 4.0
                _gc_worst = min(geom, key=lambda _m: _m['el'])
                _gc_idx   = geom.index(_gc_worst)
                _gc_base  = 4 * _gc_idx
                for _gc_r in range(_gc_base, _gc_base + 4):
                    Rd[_gc_r]   *= _gc_infl
                    Rd_p[_gc_r] *= _gc_infl
                _geom_cond_sat_dw = _gc_worst['sid']
                _geom_cond_infl   = _gc_infl
                pass  # [GEOM_COND] → geom_hump_diag.csv; console silent
        except Exception:
            pass
        # ── end GEOM FIX 2 ───────────────────────────────────────────────────

        # ── GEOM FIX 3: Up leverage screening ────────────────────────────────
        # Compute each satellite's fractional Up leverage from H^T R^-1 H.
        # Downweight any satellite contributing >35% of total Up information.
        _geom_worst_lev    = float('nan')
        _geom_lev_sat_dw   = ""          # satellite downweighted by FIX 3
        try:
            _rxyz_lv = nom + xs[:3]
            _lr_lv, _lo_lv, _ = _lla(_rxyz_lv)
            _up_lv = np.array([math.cos(_lr_lv) * math.cos(_lo_lv),
                                math.cos(_lr_lv) * math.sin(_lo_lv),
                                math.sin(_lr_lv)])
            _Ri_lv = 1.0 / np.maximum(Rd_p[:n_obs], 1e-30)
            _lev_scores = []
            for _lv_i, _lv_m in enumerate(geom):
                _lv_base = 4 * _lv_i
                _lv_score = 0.0
                for _lv_j in range(4):
                    _lv_row = _lv_base + _lv_j
                    _lv_hu  = float(H_p[_lv_row, :3] @ _up_lv)
                    _lv_score += _Ri_lv[_lv_row] * _lv_hu ** 2
                _lev_scores.append((_lv_score, _lv_i, _lv_m['sid']))
            _lev_tot = sum(_v for _v, _, _ in _lev_scores) + 1e-30
            _geom_worst_lev = max(_v / _lev_tot for _v, _, _ in _lev_scores) \
                              if _lev_scores else float('nan')
            for _lv_score2, _lv_i2, _lv_sid2 in _lev_scores:
                _lv_frac = _lv_score2 / _lev_tot
                if _lv_frac > 0.35:
                    _lv_base2 = 4 * _lv_i2
                    for _lv_r2 in range(_lv_base2, _lv_base2 + 4):
                        Rd_p[_lv_r2] *= 4.0
                    _geom_lev_sat_dw = _lv_sid2
                    pass  # [UP_LEVERAGE] → geom_hump_diag.csv; console silent
        except Exception:
            pass
        # ── end GEOM FIX 3 ───────────────────────────────────────────────────

        # combined sat_downweighted label for CSV (FIX2 | FIX3 or empty)
        _geom_sat_dw = "|".join(filter(None, [_geom_cond_sat_dw, _geom_lev_sat_dw])) or "none"

        # v70 IONO FIX 2/4: snapshot ionosphere states before the KF update so
        # we can compute the per-epoch iono step (dI) after the update.
        _I_before_update = {m['sid']: x[m['ki']] for m in geom}

        # Fusion instrumentation: capture ISB and position before update
        _isb_x_before   = float(x[_ISB_E_IDX])
        _isb_P_before   = float(P[_ISB_E_IDX, _ISB_E_IDX])
        _xyz_before_upd = x[:3].copy()

        zwd_before=x[4]
        # v58 Fix 5: regularise R to prevent near-singular inversion (NaN propagation)
        R_main = np.diag(Rd_p); R_main += np.eye(len(R_main)) * 1e-6
        if filter_standard(x,P,H_p.T,z_p,R_main)!=0: continue

        # ── NML Up-balance adaptive update (v102-bal) ─────────────────────────
        # Compute per-epoch GPS/Galileo Up normal-equation split from the same
        # H_p and R_main used by the filter.  Update EMA estimates, then derive
        # smoothed Rd scale factors to re-balance the Up contribution.
        # Bounded [_NML_SCALE_MIN, _NML_SCALE_MAX]; large EMA prevents oscillation.
        # Only active in combined GPS+Galileo mode with both constellations present.
        _n_gps_ep_bal = sum(1 for _mm in geom if _mm['sid'][0] == 'G')
        _n_gal_ep_bal = sum(1 for _mm in geom if _mm['sid'][0] == 'E')
        if 'GE' in constellation and _n_gps_ep_bal > 0 and _n_gal_ep_bal > 0:
            try:
                _rxyz_bal = nom + x[:3]
                _lr_bal, _lo_bal, _ = _lla(_rxyz_bal)
                _up_full_bal = np.zeros(len(x))
                _up_full_bal[0] = math.cos(_lr_bal) * math.cos(_lo_bal)
                _up_full_bal[1] = math.cos(_lr_bal) * math.sin(_lo_bal)
                _up_full_bal[2] = math.sin(_lr_bal)
                _Rinv_bal_ep = 1.0 / np.maximum(np.diag(R_main), 1e-30)
                _ep_nm_u_gps = 0.0; _ep_nm_u_gal = 0.0
                for _ri_bal in range(H_p.shape[0] - 1):
                    _idx_bal = _ri_bal // 4
                    _sid_bal = geom[_idx_bal]['sid'] if _idx_bal < len(geom) else None
                    if _sid_bal is None: continue
                    _h_u_bal = float(H_p[_ri_bal] @ _up_full_bal)
                    _c_bal = _Rinv_bal_ep[_ri_bal] * _h_u_bal**2
                    if _sid_bal[0] == 'G':
                        _ep_nm_u_gps += _c_bal
                    elif _sid_bal[0] == 'E':
                        _ep_nm_u_gal += _c_bal
                # EMA smooth the raw per-epoch estimates
                _nm_u_gps_ema = _NML_EMA_SMOOTH * _nm_u_gps_ema + (1.0 - _NML_EMA_SMOOTH) * _ep_nm_u_gps
                _nm_u_gal_ema = _NML_EMA_SMOOTH * _nm_u_gal_ema + (1.0 - _NML_EMA_SMOOTH) * _ep_nm_u_gal
                _nm_u_tot_ema = _nm_u_gps_ema + _nm_u_gal_ema + 1e-30
                _frac_gps_ema = _nm_u_gps_ema / _nm_u_tot_ema
                # Compute condition number for hump-risk metric
                _H_pos_bal = H_p[:, :5]
                _NM_pos_bal = (_H_pos_bal * _Rinv_bal_ep[:, np.newaxis]).T @ _H_pos_bal
                try:
                    _sv_bal = np.linalg.svd(_NM_pos_bal + np.eye(5)*1e-12, compute_uv=False)
                    _cond_bal = float(_sv_bal[0] / max(_sv_bal[-1], 1e-30))
                except Exception:
                    _cond_bal = float('nan')
                # PATCH 3+4+5: enforce 35–65% Up envelope; hard-revert if geometry ill-conditioned
                # or hump-risk is elevated.  No change to H, filter state, iono, or ZWD.
                _nml_cond_bad  = (not math.isnan(_cond_bal) and _cond_bal > 1e3)
                _nml_last_cond_bal = _cond_bal   # v108: persist for geom-freeze check
                _nml_hump_bad  = (_nml_last_hump_risk > 0.25)
                if _nml_cond_bad or _nml_hump_bad:
                    # Safety freeze: revert both scales to neutral immediately
                    _nml_scale_gps = 1.0
                    _nml_scale_gal = 1.0
                else:
                    # PATCH 3: clamp observed fraction to [35%, 65%] before computing target
                    _frac_eff  = max(_GPS_FRAC_MIN, min(_GPS_FRAC_MAX, _frac_gps_ema))
                    _tgt_scale_gps = 1.0; _tgt_scale_gal = 1.0
                    _gps_excess = _frac_eff - 0.50  # positive = GPS dominant
                    if abs(_gps_excess) > 0.05:  # only act if imbalance > 5%
                        # PATCH 4: leverage cap — correction factor bounded tightly
                        _correction = 1.0 + 1.60 * _gps_excess
                        _tgt_scale_gps = max(_NML_SCALE_MIN, min(_NML_SCALE_MAX, _correction))
                        _tgt_scale_gal = max(_NML_SCALE_MIN, min(_NML_SCALE_MAX, 2.0 - _correction))
                    # Clamp targets
                    _tgt_scale_gps = max(_NML_SCALE_MIN, min(_NML_SCALE_MAX, _tgt_scale_gps))
                    _tgt_scale_gal = max(_NML_SCALE_MIN, min(_NML_SCALE_MAX, _tgt_scale_gal))
                    # EMA smooth the scale factors (slow adaptation)
                    _nml_scale_gps = _NML_SMOOTH * _nml_scale_gps + (1.0 - _NML_SMOOTH) * _tgt_scale_gps
                    _nml_scale_gal = _NML_SMOOTH * _nml_scale_gal + (1.0 - _NML_SMOOTH) * _tgt_scale_gal
                # Diagnostic: print every 300 epochs
                _nml_bal_epoch_ctr += 1
                if _nml_bal_epoch_ctr >= 300:
                    _nml_bal_epoch_ctr = 0
                    _hump_risk = max(0.0, abs(_frac_gps_ema - 0.5) - 0.1) \
                                 + (0.0 if math.isnan(_cond_bal) else min(1.0, _cond_bal / 1e4))
                    _nml_last_hump_risk = _hump_risk   # PATCH 5: carry-forward for per-epoch revert check
                    _ed_buf.append([f"{sod:.1f}", "NML_BAL_DIAG", "",
                                    f"GPS%={100*_frac_gps_ema:.1f} cond={_cond_bal:.2e}"
                                    f" sc_GPS={_nml_scale_gps:.3f} sc_Gal={_nml_scale_gal:.3f}"
                                    f" hump_risk={_hump_risk:.3f}"])
                    _te_w.writerow([f"{sod:.1f}", "NML_BAL", "",
                                    f"{_hump_risk:.3f}",
                                    f"GPS%={100*_frac_gps_ema:.1f} cond={_cond_bal:.2e}"
                                    f" sc_GPS={_nml_scale_gps:.3f} sc_Gal={_nml_scale_gal:.3f}"])
            except Exception:
                pass
        # ─────────────────────────────────────────────────────────────────────

        # ── Per-epoch fusion_diag.csv instrumentation ─────────────────────────
        # Logs HᵀR⁻¹H Up contributions, ISB observability, and GPS-only sensitivity.
        # Runs every epoch; lightweight (no extra matrix ops beyond what NML already did).
        try:
          if _DIAG_DEEP:
            _fd_n_gps = sum(1 for _mm in geom if _mm['sid'][0] == 'G')
            _fd_n_gal = sum(1 for _mm in geom if _mm['sid'][0] == 'E')

            # ISB observability metrics
            _isb_x_after  = float(x[_ISB_E_IDX])
            _isb_P_after   = float(P[_ISB_E_IDX, _ISB_E_IDX])
            _isb_delta     = _isb_x_after - _isb_x_before
            _isb_P_reduc   = _isb_P_before - _isb_P_after   # >0 means ISB was observed
            _isb_observable = int(_isb_P_reduc > 1e-6 and _fd_n_gps >= 2 and _fd_n_gal >= 1)

            # ISB Kalman gain proxy: ISB update per unit mean Galileo code residual
            _gal_z_codes = []
            for _ri_fd, _m_fd in enumerate(geom):
                if _m_fd['sid'][0] == 'E':
                    _rp_fd   = _rp(_m_fd, _xyz_before_upd[3] if False else x[3], x[4])
                    _isb_fd  = _isb_x_before
                    _I_fd    = x[_m_fd['ki']]
                    _z_p1_fd = _m_fd['P1c'] - (_rp_fd + _I_fd + _isb_fd)
                    _gal_z_codes.append(_z_p1_fd)
            _gal_z_mean    = float(np.mean(_gal_z_codes)) if _gal_z_codes else 0.0
            _isb_gain_prox = _isb_delta / max(abs(_gal_z_mean), 1e-4) if abs(_gal_z_mean) > 1e-4 else 0.0

            # NML Up contributions (reuse from NML block if available, else compute)
            _fd_nm_u_gps = 0.0; _fd_nm_u_gal = 0.0
            if 'GE' in constellation and _fd_n_gps > 0 and _fd_n_gal > 0:
                try:
                    _rxyz_fd = nom + x[:3]
                    _lr_fd, _lo_fd, _ = _lla(_rxyz_fd)
                    _up_fd = np.array([math.cos(_lr_fd)*math.cos(_lo_fd),
                                       math.cos(_lr_fd)*math.sin(_lo_fd),
                                       math.sin(_lr_fd)])
                    _up_full_fd = np.zeros(len(x)); _up_full_fd[0:3] = _up_fd
                    _Rinv_fd = 1.0 / np.maximum(np.diag(R_main), 1e-30)
                    for _ri_fd2 in range(H_p.shape[0] - 1):
                        _idx_fd2 = _ri_fd2 // 4
                        if _idx_fd2 >= len(geom): continue
                        _sid_fd2 = geom[_idx_fd2]['sid']
                        _h_u_fd  = float(H_p[_ri_fd2] @ _up_full_fd)
                        _c_fd    = _Rinv_fd[_ri_fd2] * _h_u_fd**2
                        if _sid_fd2[0] == 'G': _fd_nm_u_gps += _c_fd
                        elif _sid_fd2[0] == 'E': _fd_nm_u_gal += _c_fd
                except Exception:
                    pass
            _fd_nm_tot   = _fd_nm_u_gps + _fd_nm_u_gal + 1e-30
            _fd_gps_frac = _fd_nm_u_gps / _fd_nm_tot

            # GPS-only vs combined position Up uncertainty (sigma_U)
            # Use P[0:3,0:3] projected onto Up direction for combined sigma_U.
            _rxyz_fd2 = nom + x[:3]
            _lr_fd2, _lo_fd2, _ = _lla(_rxyz_fd2)
            _up_fd2 = np.array([math.cos(_lr_fd2)*math.cos(_lo_fd2),
                                 math.cos(_lr_fd2)*math.sin(_lo_fd2),
                                 math.sin(_lr_fd2)])
            _var_u_comb = float(_up_fd2 @ P[0:3, 0:3] @ _up_fd2)
            _sigma_u_comb_mm = math.sqrt(max(_var_u_comb, 0.0)) * 1e3

            # GPS-only sigma_U: estimated from GPS NE Up block inverse
            _sigma_u_gps_mm = float('nan')
            if _fd_nm_u_gps > 0:
                try:
                    _H_gps_fd = []
                    _Rinv_gps_fd = []
                    _Rinv_fd2 = 1.0 / np.maximum(np.diag(R_main), 1e-30)
                    for _ri_g in range(H_p.shape[0] - 1):
                        _idx_g = _ri_g // 4
                        if _idx_g >= len(geom): continue
                        if geom[_idx_g]['sid'][0] == 'G':
                            _H_gps_fd.append(H_p[_ri_g, :5])
                            _Rinv_gps_fd.append(_Rinv_fd2[_ri_g])
                    if len(_H_gps_fd) >= 4:
                        _Hg = np.array(_H_gps_fd)
                        _Rg = np.array(_Rinv_gps_fd)
                        _NM_g = (_Hg * _Rg[:, None]).T @ _Hg
                        _NM_g_reg = _NM_g + np.eye(5) * 1e-10
                        _NM_g_inv = np.linalg.inv(_NM_g_reg)
                        _var_u_gps = float(_up_fd2 @ _NM_g_inv[0:3, 0:3] @ _up_fd2)
                        _sigma_u_gps_mm = math.sqrt(max(_var_u_gps, 0.0)) * 1e3
                except Exception:
                    pass

            # Galileo leverage ratio: ratio of GPS-only sigma to combined sigma (>1 = Galileo helps)
            _gal_leverage = (_sigma_u_gps_mm / max(_sigma_u_comb_mm, 0.1)) \
                            if not math.isnan(_sigma_u_gps_mm) and _sigma_u_comb_mm > 0 else float('nan')

            # MODE 1: cache GPS-only sigma_U for next epoch's adaptive Rd scaling
            if not math.isnan(_sigma_u_gps_mm):
                _prev_sig_u_gps_mm = _sigma_u_gps_mm

            # Per-epoch phase residuals (already computed above)
            _fd_gps_ph_rms = math.sqrt(sum(_gps_phs_res2)/max(len(_gps_phs_res2),1))*1e3 \
                             if _gps_phs_res2 else float('nan')
            _fd_gal_ph_rms = math.sqrt(sum(_gal_phs_res2)/max(len(_gal_phs_res2),1))*1e3 \
                             if _gal_phs_res2 else float('nan')

            _fusion_diag_w.writerow([
                f"{sod:.1f}", _fd_n_gps, _fd_n_gal,
                f"{_fd_nm_u_gps:.4f}", f"{_fd_nm_u_gal:.4f}", f"{_fd_gps_frac:.4f}",
                f"{_isb_x_after:+.6f}", f"{_isb_P_after:.6f}",
                f"{_isb_delta:+.6f}", f"{_isb_P_reduc:+.6f}",
                f"{_isb_gain_prox:+.4f}",
                f"{_sigma_u_gps_mm:.2f}", f"{_sigma_u_comb_mm:.2f}",
                f"{_gal_leverage:.4f}" if not math.isnan(_gal_leverage) else "nan",
                f"{_fd_gps_ph_rms:.3f}" if not math.isnan(_fd_gps_ph_rms) else "nan",
                f"{_fd_gal_ph_rms:.3f}" if not math.isnan(_fd_gal_ph_rms) else "nan",
                f"{_gal_scale_adaptive:.4f}",
                f"{_nml_scale_gps:.4f}", f"{_nml_scale_gal:.4f}",
                _isb_observable,
            ])
            # ── cache for per-epoch transition_audit ──────────────────────────
            _ta_fd_n_gps   = _fd_n_gps;     _ta_fd_n_gal  = _fd_n_gal
            _ta_fd_gps_frac = _fd_gps_frac
            _ta_sigma_u_gps = _sigma_u_gps_mm; _ta_sigma_u_comb = _sigma_u_comb_mm
            _ta_gal_lev    = _gal_leverage;  _ta_isb_obs   = _isb_observable
        except Exception as _fd_exc:
            pass  # never block the filter on logging failure
        # ─────────────────────────────────────────────────────────────────────

        # ── Fix 4: ISB hold — soft anchor when GPS count drops ────────────────
        # When GPS sats drop below threshold, ISB becomes weakly observable.
        # Instead of hard-clamping P_ISB (which masks the unobservability):
        #   1. Track last valid ISB estimate when nGPS is adequate.
        #   2. Inject a SOFT pseudo-observation anchoring ISB to its last valid
        #      estimate — this provides information without forcing a hard reset.
        #   3. Cap P_ISB only as a safety valve against numerical blowup.
        # The soft anchor has noise = sqrt(P_ISB_last_valid) * 3 so it allows
        # drift but prevents unbounded wandering when GPS is absent.
        _n_gps_now = sum(1 for _mm in geom if _mm['sid'][0] == 'G')
        _n_gal_now = sum(1 for _mm in geom if _mm['sid'][0] == 'E')
        _p_isb_now = P[_ISB_E_IDX, _ISB_E_IDX]
        if _n_gps_now >= _ISB_GPS_MIN and _n_gal_now >= 1:
            if not math.isnan(x[_ISB_E_IDX]):
                _isb_last_valid   = float(x[_ISB_E_IDX])
                _P_isb_last_valid = float(_p_isb_now)
        if _n_gps_now < _ISB_GPS_MIN and not math.isnan(_isb_last_valid):
            # GPS insufficient — inject soft ISB anchor to prevent free wander
            _isb_anchor_sigma = math.sqrt(max(_P_isb_last_valid, 0.01)) * 5.0
            _h_isb_anchor = np.zeros(len(x)); _h_isb_anchor[_ISB_E_IDX] = 1.0
            _z_isb_anchor = _isb_last_valid - x[_ISB_E_IDX]
            _R_isb_anchor = np.array([[_isb_anchor_sigma**2 + 1e-6]])
            filter_standard(x, P, _h_isb_anchor[:, None], np.array([_z_isb_anchor]), _R_isb_anchor)
        # Safety cap — only fires if something numerical went wrong
        if _p_isb_now > _ISB_P_CAP * 4:
            P[_ISB_E_IDX, _ISB_E_IDX] = _ISB_P_CAP
            if not math.isnan(_isb_last_valid):
                x[_ISB_E_IDX] = _isb_last_valid
                print(f"[ISB_HOLD] SOD={sod:.0f}  P_ISB was {_p_isb_now:.4f}m² — "
                      f"clamped to {_ISB_P_CAP:.2f}m²  "
                      f"ISB held at {_isb_last_valid:+.4f}m  "
                      f"nGPS={_n_gps_now} nGal={_n_gal_now}")
        # ─────────────────────────────────────────────────────────────────────

        # ── Fix 3b: continuous per-epoch corr(U,ZWD) for hump audit ──────────
        _rxyz_ep = nom + x[:3]
        _lr_ep, _lo_ep, _ = _lla(_rxyz_ep)
        _up_ep = np.array([math.cos(_lr_ep)*math.cos(_lo_ep),
                           math.cos(_lr_ep)*math.sin(_lo_ep),
                           math.sin(_lr_ep)])
        _cov_u_zwd_ep  = float(_up_ep @ P[0:3, 4])
        _var_u_ep      = float(_up_ep @ P[0:3, 0:3] @ _up_ep)
        _var_zwd_ep    = float(P[4, 4])
        _denom_ep      = math.sqrt(max(_var_u_ep, 1e-20) * max(_var_zwd_ep, 1e-20))
        _corr_ep       = _cov_u_zwd_ep / _denom_ep if _denom_ep > 0 else 0.0
        _corr_u_zwd_history.append(_corr_ep)
        if len(_corr_u_zwd_history) > 60: del _corr_u_zwd_history[:-60]
        # Toggle ZWD decorrelation mode based on sustained high coupling
        _corr_mean_60  = float(np.mean([abs(c) for c in _corr_u_zwd_history]))
        if _corr_mean_60 > 0.35 and not _zwd_decorr_active:
            _zwd_decorr_active = True
            print(f"[ZWD_DECORR_ON] SOD={sod:.0f}  mean|corr(U,ZWD)|={_corr_mean_60:.4f} > 0.35 "
                  f"→ reduced ZWD Q={_ZWD_Q_REDUCED:.2e} m²/s")
            _te_w.writerow([f"{sod:.1f}", "ZWD_DECORR_ON", "",
                            f"{_corr_mean_60:.4f}", f"Q_reduced={_ZWD_Q_REDUCED:.2e}"])
        elif _corr_mean_60 < 0.20 and _zwd_decorr_active:
            _zwd_decorr_active = False
            print(f"[ZWD_DECORR_OFF] SOD={sod:.0f}  mean|corr(U,ZWD)|={_corr_mean_60:.4f} < 0.20 "
                  f"→ restored ZWD Q={_ZWD_Q_BASE:.2e} m²/s")
            _te_w.writerow([f"{sod:.1f}", "ZWD_DECORR_OFF", "",
                            f"{_corr_mean_60:.4f}", f"Q_restored={_ZWD_Q_BASE:.2e}"])
        # ─────────────────────────────────────────────────────────────────────

        # FIX 3: Up/ZWD soft covariance decorrelation when abs(corr(U,ZWD)) > 0.20
        # Scales P[xyz,ZWD] cross-terms by 0.25 (does NOT zero them)
        if abs(_corr_ep) > 0.20:
            P[0:3, 4] *= 0.25
            P[4, 0:3] *= 0.25
            # [UZ_DECORR] → state_diag.csv corr_U_ZWD column; console silent

        # FIX 4: weak vertical stabilizer pseudo-obs during hump window (6h-19h)
        # sigma=0.25 m — soft regularization only; not a hard constraint
        if _in_hump_ep:
            try:
                _h_upstab = np.zeros(len(x))
                _h_upstab[0] = _up_ep[0]; _h_upstab[1] = _up_ep[1]; _h_upstab[2] = _up_ep[2]
                _z_upstab = float(-(_up_ep @ x[:3]))
                _R_upstab = np.array([[0.0625]])   # 0.25**2 m²
                filter_standard(x, P, _h_upstab[:, None], np.array([_z_upstab]), _R_upstab)
            except Exception:
                pass

        # ── per-epoch transition_audit.csv ────────────────────────────────────
        try:
          if _DIAG_DEEP:
            _ta_w.writerow([
                f"{sod:.1f}",
                _ta_fd_n_gps, _ta_fd_n_gal,
                len(nl_fixed), len(wl_fixed), len(geom),
                f"{_ta_fd_gps_frac:.4f}" if not math.isnan(_ta_fd_gps_frac) else "nan",
                f"{_gal_scale_adaptive:.4f}",
                f"{_ta_gal_lev:.3f}"     if not math.isnan(_ta_gal_lev)     else "nan",
                f"{_ta_sigma_u_comb:.2f}" if not math.isnan(_ta_sigma_u_comb) else "nan",
                f"{_ta_sigma_u_gps:.2f}"  if not math.isnan(_ta_sigma_u_gps)  else "nan",
                f"{x[_ISB_E_IDX]:+.6f}",
                f"{P[_ISB_E_IDX, _ISB_E_IDX]:.6f}",
                _ta_isb_obs,
                f"{x[4]:.6f}",
                f"{P[4,4]:.8f}",
                f"{_corr_ep:+.4f}",
                f"{_corr_mean_60:.4f}",
                f"{_q_zwd_now:.3e}",
                int(_zwd_decorr_active),
                f"{P[0,0]+P[1,1]+P[2,2]:.6f}",
                f"{_nml_scale_gps:.4f}",
                f"{_nml_scale_gal:.4f}",
            ])
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────────

        # ── FLOAT_DIAG → CSV (top-6 lowest sigma per epoch) ─────────────────
        if _DIAG_DEEP and sidx:
            _fd_rows = []
            for _fd_sid, _fd_ki in sidx.items():
                _fd_N1   = x[_fd_ki + 1]
                _fd_lam1 = _sat_lam1.get(_fd_sid, 0.1903)
                _fd_sig1 = math.sqrt(max(0.0, P[_fd_ki+1, _fd_ki+1])) * _fd_lam1
                _fd_frac = _fd_N1 - round(_fd_N1)
                _fd_rows.append((_fd_sig1, _fd_sid, _fd_N1, _fd_frac))
            _fd_rows.sort(key=lambda r: r[0])
            for _fd_sig1, _fd_sid, _fd_N1, _fd_frac in _fd_rows[:6]:
                _float_diag_w.writerow([f"{sod:.1f}", _fd_sid,
                                        f"{_fd_N1:.4f}", f"{_fd_frac:+.4f}",
                                        f"{_fd_sig1:.4f}"])
        # ── end FLOAT_DIAG ────────────────────────────────────────────────────

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
        IONO_RW_CAP   = 2.0    # m/epoch — hard cap on iono state step
        IONO_INSTAB_T = 5.0    # m — flag unstable if single-epoch jump > 5 m
        IONO_VAR_CAP  = 25.0   # m² — σ_I ≤ 5 m (tightened from 100 → 25)
        _iono_unstable = set()
        for _m_c in geom:
            _sid_c = _m_c['sid']
            _ki_c  = _m_c['ki']
            _I_new = x[_ki_c]
            _I_old = _I_before_update.get(_sid_c, _I_new)
            _dI    = abs(_I_new - _I_old)
            _iono_last_dI[_sid_c] = _dI
            # Re-enabled: RW cap — prevents iono state jumping > 2 m/epoch
            if _dI > IONO_RW_CAP:
                x[_ki_c] = _I_old + math.copysign(IONO_RW_CAP, _I_new - _I_old)
            # Re-enabled: variance cap at 25 m² — bounds σ_I ≤ 5 m
            P[_ki_c, _ki_c] = min(P[_ki_c, _ki_c], IONO_VAR_CAP)
            # Re-enabled: instability flag — blocks NL fixing for exploding sats
            if _dI > IONO_INSTAB_T:
                _iono_unstable.add(_sid_c)

        # [IONO VAR] / [GPS_SUMMARY] → removed (debug data in CSVs)

        # v58 Fix 2: NaN protection — if the state vector went non-finite after
        # the float update, release all NL fixes and inflate the covariance so
        # the filter can recover rather than propagating NaN indefinitely.
        if not np.isfinite(x).all() or not np.isfinite(P).all():
            x = np.where(np.isfinite(x), x, 0.0)
            P = np.where(np.isfinite(P), P, 0.0)
            np.fill_diagonal(P, np.maximum(np.diag(P), 100.**2))
            P *= 100.
            # v96: preserve NL if fewer than 50% of fixes are non-finite
            _nan_n_tot=len(nl_fixed)
            _nan_n_inv=sum(1 for _sn in list(nl_fixed)
                           if _sn not in sidx or not np.isfinite(x[sidx[_sn]+1]))
            if _nan_n_tot==0 or _nan_n_inv>_nan_n_tot//2:
                nl_fixed.clear(); _nl_cluster.clear(); _nl_cf_at_fix.clear()  # v103 FIX: clear all cluster state
            print(f"[NaN GUARD] SOD={sod:.0f} — non-finite state detected; "
                  f"released all NL fixes, inflated covariance")

        # Compute phase residuals immediately after float update so NL gate
        # can use current-epoch phase_rms_now (not stale from previous epoch).
        # v102: also build phase_res_by_sid (sid -> abs L1 residual metres)
        # for the per-epoch fixed-sat health check below.
        phase_res_now=[]
        phase_res_by_sid={}
        for m in geom:
            ki=m['ki']; rp=_rp(m,x[3],x[4])
            res_L1=m['L1mc']-(rp - x[ki] + m['lam1']*x[ki+1])
            phase_res_now.append(res_L1)
            phase_res_by_sid[m['sid']] = abs(res_L1)
        phase_rms_now=math.sqrt(np.mean(np.array(phase_res_now)**2)) if phase_res_now else 999.

        # ── HUMP DIAG: log per-epoch row ──────────────────────────────────────
        # In LIGHT mode: compute expensive VDOP_ew every 120 epochs; write CSV
        # at the same cadence.  In DEEP mode: every epoch.
        _hd_write_now = _DIAG_DEEP or (nproc % _HUMP_DIAG_INTERVAL == 0)
        try:
            if _hd_write_now:
              _hd_rxyz = nom + x[:3]
              _hd_lat, _hd_lon, _ = _lla(_hd_rxyz)
              _hd_vdop  = _vdop_ew(geom, _hd_lat, _hd_lon)
              _hd_els   = [math.degrees(m['el']) for m in geom]
              _hd_n_low = sum(1 for e in _hd_els if e < 20.0)
              _hd_min_el = min(_hd_els) if _hd_els else 0.0
              _hd_mean_el = float(np.mean(_hd_els)) if _hd_els else 0.0
              # Slant TEC proxy: iono state in metres (L1 delay = x[ki])
              _hd_stec_vals = [abs(x[m['ki']]) for m in geom if m['ki'] < len(x)]
              _hd_mean_stec = float(np.mean(_hd_stec_vals)) if _hd_stec_vals else 0.0
              _hd_max_stec  = float(max(_hd_stec_vals)) if _hd_stec_vals else 0.0
              # Iono variance per sat (post-update, capped at IONO_VAR_CAP)
              _hd_iono_vars = [float(P[m['ki'], m['ki']]) for m in geom if m['ki'] < len(x)]
              _hd_mean_ivar = float(np.mean(_hd_iono_vars)) if _hd_iono_vars else 0.0
              _hd_max_ivar  = float(max(_hd_iono_vars)) if _hd_iono_vars else 0.0
              # Pre-update innovations RMS (mm)
              _hd_ph_rms_mm   = math.sqrt(np.mean(np.array(_hd_ph_innov)**2))*1e3 \
                                if _hd_ph_innov else float('nan')
              _hd_code_rms_mm = math.sqrt(np.mean(np.array(_hd_code_innov)**2))*1e3 \
                                if _hd_code_innov else float('nan')
              _hd_in_hump = int(HUMP_SOD_LO <= sod <= HUMP_SOD_HI)
              _hump_diag_w.writerow([
                f"{sod:.1f}", _hd_in_hump,
                f"{x[4]:.5f}", f"{_hd_zwd_innov:+.5f}", f"{ZWD_PRIOR_SIGMA:.4f}",
                f"{_hd_mean_ivar:.4f}", f"{_hd_max_ivar:.4f}", len(_hd_iono_vars),
                f"{_hd_vdop:.3f}", len(geom), _hd_n_low,
                f"{_hd_min_el:.2f}", f"{_hd_mean_el:.2f}",
                f"{_hd_mean_stec:.3f}", f"{_hd_max_stec:.3f}",
                f"{_hd_ph_rms_mm:.3f}" if not math.isnan(_hd_ph_rms_mm) else "nan",
                f"{_hd_code_rms_mm:.3f}" if not math.isnan(_hd_code_rms_mm) else "nan",
                int(HUMP_ION_BOOST), int(HUMP_EL2_WEIGHT),
                int(HUMP_ELEV_MASK), int(HUMP_ZWD_TIGHTEN),
              ])
        except Exception as _hd_exc:
            pass  # never block filter on logging failure
        # ── end HUMP DIAG ─────────────────────────────────────────────────────

        # ── GEOM HUMP DIAG: log per-epoch geometry conditioning row ───────────
        try:
          if _DIAG_DEEP:
            _gh_els    = [math.degrees(m['el']) for m in geom]
            _gh_min_el = min(_gh_els) if _gh_els else float('nan')
            _gh_mean_el= float(np.mean(_gh_els)) if _gh_els else float('nan')
            _gh_n_low  = sum(1 for e in _gh_els if e < 20.0)
            _gh_rxyz   = nom + x[:3]
            _gh_lr, _gh_lo, _ = _lla(_gh_rxyz)
            _gh_up     = np.array([math.cos(_gh_lr)*math.cos(_gh_lo),
                                   math.cos(_gh_lr)*math.sin(_gh_lo),
                                   math.sin(_gh_lr)])
            _gh_var_u  = float(_gh_up @ P[0:3, 0:3] @ _gh_up)
            _gh_sig_u  = math.sqrt(max(_gh_var_u, 0.0)) * 1e3
            _gh_dx     = _gh_rxyz - REF
            _gh_d3     = float(np.linalg.norm(_gh_dx)) * 1e3
            _geom_hump_w.writerow([
                f"{sod:.1f}",
                f"{_geom_cond_nm:.2e}" if not math.isnan(_geom_cond_nm) else "nan",
                f"{_geom_worst_lev:.4f}" if not math.isnan(_geom_worst_lev) else "nan",
                _geom_sat_dw,
                f"{_gh_min_el:.2f}",
                f"{_gh_mean_el:.2f}",
                _gh_n_low,
                f"{_gh_sig_u:.2f}",
                f"{_gh_d3:.2f}",
            ])
        except Exception:
            pass
        # ── end GEOM HUMP DIAG ────────────────────────────────────────────────

        # FIX 5: geom_zwd_hump_diag.csv — per-epoch geometry+ZWD hump diagnostics
        try:
          if _DIAG_DEEP:
            _gzh_rxyz    = nom + x[:3]
            _gzh_lr, _gzh_lo, _ = _lla(_gzh_rxyz)
            _gzh_up      = np.array([math.cos(_gzh_lr)*math.cos(_gzh_lo),
                                     math.cos(_gzh_lr)*math.sin(_gzh_lo),
                                     math.sin(_gzh_lr)])
            _gzh_dx      = _gzh_rxyz - REF
            _gzh_up_bias = float(_gzh_up @ _gzh_dx) * 1e3   # mm, signed
            _gzh_d3_mm   = float(np.linalg.norm(_gzh_dx)) * 1e3
            _gzh_els_ep  = [math.degrees(m['el']) for m in geom]
            _gzh_n_low   = sum(1 for e in _gzh_els_ep if e < 20.0)
            _gzh_min_el  = min(_gzh_els_ep) if _gzh_els_ep else float('nan')
            _gzh_mean_el = float(np.mean(_gzh_els_ep)) if _gzh_els_ep else float('nan')
            _gzh_vdop_ep = _vdop_ew(geom, _gzh_lr, _gzh_lo)
            _gzh_w.writerow([
                f"{sod:.1f}",
                f"{_gzh_vdop_ep:.3f}", f"{_corr_ep:+.4f}",
                _gzh_n_low, f"{_gzh_min_el:.2f}", f"{_gzh_mean_el:.2f}",
                f"{_gzh_up_bias:+.2f}", f"{_gzh_d3_mm:.2f}",
                _mask_exclusions_ep,
            ])
            _gzh_vdop_hist.append((_gzh_vdop_ep, _gzh_d3_mm, _in_hump_ep))
        except Exception:
            pass
        # ── end geom_zwd_hump_diag ───────────────────────────────────────────

        # PART B — separate GPS / Galileo residuals for ISB weighting diagnostics
        _gps_code_res2=[]; _gal_code_res2=[]
        _gps_phs_res2 =[]; _gal_phs_res2 =[]
        for _m_id in geom:
            _ki_id  = _m_id['ki']
            _rp_id  = _rp(_m_id, x[3], x[4])
            _isb_id = x[_ISB_E_IDX] if (_m_id['sid'][0]=='E' and 'GE' in constellation) else 0.0
            _r_p1   = _m_id['P1c']  - (_rp_id + x[_ki_id]                              + _isb_id)
            _r_l1   = _m_id['L1mc'] - (_rp_id - x[_ki_id] + _m_id['lam1']*x[_ki_id+1] + _isb_id)
            if _m_id['sid'][0] == 'G':
                _gps_code_res2.append(_r_p1**2); _gps_phs_res2.append(_r_l1**2)
            elif _m_id['sid'][0] == 'E':
                _gal_code_res2.append(_r_p1**2); _gal_phs_res2.append(_r_l1**2)
        # Accumulate into running buffers (capped at 5000 samples each)
        _isb_gps_ph2_acc.extend(_gps_phs_res2); _isb_gal_ph2_acc.extend(_gal_phs_res2)
        if len(_isb_gps_ph2_acc) > 5000: del _isb_gps_ph2_acc[:len(_isb_gps_ph2_acc)-5000]
        if len(_isb_gal_ph2_acc) > 5000: del _isb_gal_ph2_acc[:len(_isb_gal_ph2_acc)-5000]

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
            # v102 FIX 3: minimum fix age before release (300 s).
            # Float noise in the first 10 epochs after a fix can exceed
            # NL_RELEASE_THRESH transiently; ignore drift counter until settled.
            _fix_age_s = sod - _nl_fix_start_sod.get(sid_nl, sod)
            if _fix_age_s < 300:
                _nl_drift_count[sid_nl] = 0
                continue
            if abs(x[ki_nl+1]-N1_i)>NL_RELEASE_THRESH:
                # FIX 2: stability-based release — only release after ≥5 consecutive
                # drifting epochs so single-epoch noise spikes do not trigger release.
                _nl_drift_count[sid_nl] += 1
                if _nl_drift_count[sid_nl] >= 5:
                    to_release.append(sid_nl)
            else:
                _nl_drift_count[sid_nl] = 0   # reset counter when back in bounds
            # v101 Fix G: controlled release on growing phase residuals.
            # If the float residual from the fixed integer grows > 0.08 cycles
            # for 10+ consecutive epochs, the fix has drifted — release it.
            _innov_g = abs(x[ki_nl+1] - N1_i)
            if _innov_g > 0.06:
                _nl_drift_count[sid_nl] = max(_nl_drift_count[sid_nl], 1)
            if _nl_drift_count[sid_nl] >= 10 and sid_nl not in to_release:
                to_release.append(sid_nl)
                print(f"[NL_SLOW_DRIFT] {sid_nl} innov={_innov_g:.4f} drift_count={_nl_drift_count[sid_nl]} → releasing")
        for sid_nl in to_release:
            # log RELEASE event before removing
            _lam1_rel = _sat_lam1.get(sid_nl, 0.1903)
            _sig_rel  = math.sqrt(max(0.0, P[sidx[sid_nl]+1, sidx[sid_nl]+1])) * _lam1_rel if sid_nl in sidx else 0.0
            _cf_rel   = x[sidx[sid_nl]+1] - nl_fixed[sid_nl][0] if sid_nl in sidx else 0.0
            # FIX 1: increment release counter and check fix duration
            _nl_release_count[sid_nl] += 1
            _fix_dur_ep = int(sod - _nl_fix_start_sod.get(sid_nl, sod)) // 30
            if _fix_dur_ep < 300:
                _nl_short_fix_count[sid_nl] += 1
            # FIX 2: quarantine decision
            # Criteria: (a) ≥2 releases, (b) ≥2 short fixes (<300 ep), (c) residual RMS > 2x median
            _q_reason = None
            if _nl_release_count[sid_nl] >= 2:
                _q_reason = f"release_count={_nl_release_count[sid_nl]}"
            elif _nl_short_fix_count[sid_nl] >= 2:
                _q_reason = f"short_fix_count={_nl_short_fix_count[sid_nl]}"
            else:
                # criterion (c): residual RMS > 2x median of all fixed-sat RMS
                _ph_buf = list(_nl_phase_resid_buf[sid_nl])
                if len(_ph_buf) >= 10:
                    _sat_rms = math.sqrt(sum(v*v for v in _ph_buf) / len(_ph_buf))
                    _all_rms = []
                    for _q_s in nl_fixed:
                        _q_buf = list(_nl_phase_resid_buf[_q_s])
                        if len(_q_buf) >= 10:
                            _all_rms.append(math.sqrt(sum(v*v for v in _q_buf) / len(_q_buf)))
                    if _all_rms:
                        _med_rms = sorted(_all_rms)[len(_all_rms)//2]
                        if _sat_rms > 2.0 * _med_rms and _med_rms > 0:
                            _q_reason = f"rms={_sat_rms:.4f}_2x_median={_med_rms:.4f}"
            if _q_reason and sid_nl not in _nl_quarantine:
                _nl_quarantine.add(sid_nl)
                _ed_buf.append([f"{sod:.1f}", "QUARANTINE", sid_nl, _q_reason])
                _nl_events_w.writerow([f"{sod:.1f}", sid_nl, "QUARANTINE",
                                       f"{_cf_rel:+.4f}", _q_reason])
                _te_w.writerow([f"{sod:.1f}", "QUARANTINE", sid_nl,
                                f"{_cf_rel:+.4f}", _q_reason])
            _ed_buf.append([f"{sod:.1f}", "NL_RELEASE", sid_nl,
                            f"age={_fix_dur_ep} rel={_nl_release_count[sid_nl]}"
                            f" refix={_nl_refix_count[sid_nl]}"
                            f" quar={sid_nl in _nl_quarantine}"])
            _nl_events_w.writerow([f"{sod:.1f}", sid_nl, "RELEASE",
                                   f"{_cf_rel:+.4f}", f"{_sig_rel:.4f}"])
            nl_fixed.pop(sid_nl,None)
            _nl_fix_cooldown[sid_nl] = 1000  # FIX 3: 1000-epoch cooldown after release (churn kill)
            _nl_persist_count[sid_nl] = 0   # v97: reset persistence on release
            _nl_drift_count.pop(sid_nl, None)
            _nl_fix_start_sod.pop(sid_nl, None)
            _nl_stable.discard(sid_nl)      # NL_STABLE: cleared on release
            _nl_locked.discard(sid_nl)      # hard lock released on NL release
            _nl_stable_count[sid_nl] = 0


        # ── AMB_AUDIT: per-epoch ambiguity influence audit ────────────────────
        # For every currently fixed satellite:
        #   - post-fix phase residual (x[ki+1]-N1_i in cycles)
        #   - share of Up normal-equation (H^T R^-1 H) from this satellite's rows
        #   - leave-one-out Up solution shift (mm) when this satellite is removed
        #   - flag if >35% of Up info AND growing residual drift (quarantine diagnostic)
        # No filter state, ZWD, ISB, or weights are changed.
        if _DIAG_DEEP and nl_fixed and sidx and 'H_p' in dir():
            try:
                # Build Up unit vector and Rinv once for this epoch
                _aa_rxyz = nom + x[:3]
                _aa_lr, _aa_lo, _ = _lla(_aa_rxyz)
                _aa_up = np.array([math.cos(_aa_lr)*math.cos(_aa_lo),
                                   math.cos(_aa_lr)*math.sin(_aa_lo),
                                   math.sin(_aa_lr)])
                _aa_up_full = np.zeros(len(x)); _aa_up_full[0:3] = _aa_up
                _aa_Rinv = 1.0 / np.maximum(np.diag(R_main), 1e-30)
                _aa_n_rows = H_p.shape[0] - 1   # exclude ZWD prior row

                # Precompute total Up normal-eq and per-sat row index sets
                _aa_total_up_ne = 0.0
                _aa_sat_rows    = {}   # sid → list of row indices in H_p
                for _aa_ri in range(_aa_n_rows):
                    _aa_idx = _aa_ri // 4
                    if _aa_idx >= len(geom): continue
                    _aa_sid = geom[_aa_idx]['sid']
                    _aa_h_u = float(H_p[_aa_ri] @ _aa_up_full)
                    _aa_c   = _aa_Rinv[_aa_ri] * _aa_h_u**2
                    _aa_total_up_ne += _aa_c
                    _aa_sat_rows.setdefault(_aa_sid, []).append(_aa_ri)

                # Full Up sigma from combined normal matrix (position block)
                _aa_H5   = H_p[:, :5]
                _aa_NM5  = (_aa_H5 * _aa_Rinv[:, np.newaxis]).T @ _aa_H5 + np.eye(5)*1e-10
                try:
                    _aa_NM5_inv = np.linalg.inv(_aa_NM5)
                    _aa_sig_u_full = math.sqrt(max(float(_aa_up @ _aa_NM5_inv[:3,:3] @ _aa_up), 0.0))
                except np.linalg.LinAlgError:
                    _aa_sig_u_full = float('nan')

                for _aa_sid_nl, (_aa_N1_i, _) in nl_fixed.items():
                    if _aa_sid_nl not in sidx: continue
                    _aa_ki = sidx[_aa_sid_nl]

                    # 1. Post-fix phase residual (cycles)
                    _aa_resid = abs(float(x[_aa_ki + 1]) - _aa_N1_i)

                    # 2. Update rolling residual history
                    _aa_resid_hist[_aa_sid_nl].append(_aa_resid)

                    # 3. RMS of last ≤30 residuals
                    _aa_buf = list(_aa_resid_hist[_aa_sid_nl])[-30:]
                    _aa_rms30 = math.sqrt(sum(v*v for v in _aa_buf) / max(len(_aa_buf), 1))

                    # 4. Monotonic drift flag: last 30 samples strictly increasing
                    _aa_flag_mono = 0
                    if len(_aa_buf) >= 30:
                        _aa_flag_mono = int(all(_aa_buf[i] <= _aa_buf[i+1]
                                                for i in range(len(_aa_buf)-1)))

                    # 5. Up leverage for this satellite
                    _aa_sat_up_ne = 0.0
                    for _aa_ri in _aa_sat_rows.get(_aa_sid_nl, []):
                        _aa_h_u = float(H_p[_aa_ri] @ _aa_up_full)
                        _aa_sat_up_ne += _aa_Rinv[_aa_ri] * _aa_h_u**2
                    _aa_lev_frac = _aa_sat_up_ne / max(_aa_total_up_ne, 1e-30)
                    _aa_flag_hlev = int(_aa_lev_frac > 0.35)

                    # 6. Leave-one-out Up solution shift (mm)
                    _aa_loo_shift_mm = float('nan')
                    try:
                        _aa_rows_ex = _aa_sat_rows.get(_aa_sid_nl, [])
                        if _aa_rows_ex and not math.isnan(_aa_sig_u_full):
                            _aa_keep = [r for r in range(_aa_n_rows)
                                        if r not in _aa_rows_ex]
                            if len(_aa_keep) >= 4:
                                _aa_Hk = _aa_H5[_aa_keep, :]
                                _aa_Rk = _aa_Rinv[_aa_keep]
                                _aa_NMk = (_aa_Hk * _aa_Rk[:, np.newaxis]).T @ _aa_Hk + np.eye(5)*1e-10
                                _aa_NMk_inv = np.linalg.inv(_aa_NMk)
                                _aa_sig_u_loo = math.sqrt(max(float(
                                    _aa_up @ _aa_NMk_inv[:3,:3] @ _aa_up), 0.0))
                                _aa_loo_shift_mm = (_aa_sig_u_loo - _aa_sig_u_full) * 1e3
                    except (np.linalg.LinAlgError, Exception):
                        pass

                    # 7. Quarantine flag (diagnostic label only — no state change)
                    _aa_quarantine_flag = int(_aa_flag_hlev and _aa_flag_mono)
                    if _aa_quarantine_flag:
                        if _aa_sid_nl not in _aa_quarantine:
                            _aa_quarantine.add(_aa_sid_nl)
                            _ed_buf.append([f"{sod:.1f}", "AMB_AUDIT_QUAR", _aa_sid_nl,
                                            f"up_lev={_aa_lev_frac:.3f} rms30={_aa_rms30:.4f}cyc"])
                    else:
                        _aa_quarantine.discard(_aa_sid_nl)

                    # 8. Write to CSV
                    _aa_fix_age = sod - _nl_fix_start_sod.get(_aa_sid_nl, sod)
                    _aa_w.writerow([
                        f"{sod:.1f}", _aa_sid_nl, f"{_aa_fix_age:.0f}",
                        f"{_aa_resid:.5f}", f"{_aa_rms30:.5f}",
                        f"{_aa_lev_frac:.4f}",
                        f"{_aa_loo_shift_mm:.2f}" if not math.isnan(_aa_loo_shift_mm) else "nan",
                        _aa_flag_hlev, _aa_flag_mono, _aa_quarantine_flag,
                    ])

            except Exception as _aa_exc:
                pass   # never interrupt the filter for a diagnostic
        # ── end AMB_AUDIT ─────────────────────────────────────────────────────

        # ── FIX 1: sat_churn_diag.csv — per-epoch NL lifetime monitor ────────
        try:
          if _DIAG_DEEP:
            # Compute Up leverage for each fixed sat (reuse H_p if available)
            _sc_up_lev = {}
            if nl_fixed and 'H_p' in dir() and sidx:
                _sc_rxyz = nom + x[:3]
                _sc_lr, _sc_lo, _ = _lla(_sc_rxyz)
                _sc_up = np.array([math.cos(_sc_lr)*math.cos(_sc_lo),
                                   math.cos(_sc_lr)*math.sin(_sc_lo),
                                   math.sin(_sc_lr)])
                _sc_up_full = np.zeros(len(x)); _sc_up_full[0:3] = _sc_up
                _sc_Rinv = 1.0 / np.maximum(np.diag(R_main), 1e-30)
                _sc_n_rows = H_p.shape[0] - 1
                _sc_total = 0.0; _sc_sat_ne = defaultdict(float)
                for _sc_ri in range(_sc_n_rows):
                    _sc_idx = _sc_ri // 4
                    if _sc_idx >= len(geom): continue
                    _sc_sid = geom[_sc_idx]['sid']
                    _sc_h_u = float(H_p[_sc_ri] @ _sc_up_full)
                    _sc_c = _sc_Rinv[_sc_ri] * _sc_h_u ** 2
                    _sc_total += _sc_c
                    _sc_sat_ne[_sc_sid] += _sc_c
                for _sc_s in nl_fixed:
                    _sc_up_lev[_sc_s] = _sc_sat_ne[_sc_s] / max(_sc_total, 1e-30)
                    _nl_up_lev_buf[_sc_s].append(_sc_up_lev[_sc_s])
            # Write one row per fixed sat per epoch
            _d3_now = d3 * 1e3 if 'd3' in dir() else float('nan')
            for _sc_s, (_sc_N1, _) in nl_fixed.items():
                _sc_age_ep = int(sod - _nl_fix_start_sod.get(_sc_s, sod)) // 30
                _sc_ph_buf = list(_nl_phase_resid_buf[_sc_s])
                _sc_ph_rms = math.sqrt(sum(v*v for v in _sc_ph_buf)/max(len(_sc_ph_buf),1)) if _sc_ph_buf else float('nan')
                _sc_co_buf = list(_nl_code_resid_buf[_sc_s])
                _sc_co_rms = math.sqrt(sum(v*v for v in _sc_co_buf)/max(len(_sc_co_buf),1)) if _sc_co_buf else float('nan')
                _sc_lev = _sc_up_lev.get(_sc_s, float('nan'))
                _churn_w.writerow([
                    f"{sod:.1f}", _sc_s, 1,
                    _sc_age_ep,
                    _nl_release_count[_sc_s], _nl_refix_count[_sc_s],
                    f"{_sc_ph_rms:.5f}" if not math.isnan(_sc_ph_rms) else "nan",
                    f"{_sc_co_rms:.2f}" if not math.isnan(_sc_co_rms) else "nan",
                    f"{_sc_lev:.4f}" if not math.isnan(_sc_lev) else "nan",
                    f"{_d3_now:.1f}" if not math.isnan(_d3_now) else "nan",
                    int(_sc_s in _nl_quarantine),
                ])
        except Exception:
            pass   # never interrupt filter for diagnostic
        # ── end FIX 1 churn log ───────────────────────────────────────────────

        # ── FIX 5: per-epoch hump audit accumulation ─────────────────────────
        try:
            _in_h1 = HUMP1_SOD_LO <= sod <= HUMP1_SOD_HI
            _in_h2 = HUMP2_SOD_LO <= sod <= HUMP2_SOD_HI
            if _in_h1:
                for _hs in nl_fixed:
                    _hump1_fixed_sats.add(_hs)
                    if _hs in _nl_quarantine:
                        _hump1_quarantined.add(_hs)
            if _in_h2:
                for _hs in nl_fixed:
                    _hump2_fixed_sats.add(_hs)
                    if _hs in _nl_quarantine:
                        _hump2_quarantined.add(_hs)
            # track releases that occurred this epoch (to_release already processed)
            for _hr_s in to_release:
                if _in_h1:
                    _hump1_released.append((sod, _hr_s))
                    _hump1_churn[_hr_s] += 1
                if _in_h2:
                    _hump2_released.append((sod, _hr_s))
                    _hump2_churn[_hr_s] += 1
            # print summary once after hump2 ends
            if sod > HUMP2_SOD_HI + 1800 and not _hump_audit_printed:
                _hump_audit_printed = True
                print("[HUMP_AUDIT] ══════════════════════════════════════════")
                print(f"[HUMP_AUDIT] Hump1 ({HUMP1_SOD_LO/3600:.0f}–{HUMP1_SOD_HI/3600:.0f} h):")
                print(f"[HUMP_AUDIT]   fixed PRNs : {sorted(_hump1_fixed_sats)}")
                print(f"[HUMP_AUDIT]   releases   : {_hump1_released}")
                print(f"[HUMP_AUDIT]   quarantined: {sorted(_hump1_quarantined)}")
                print(f"[HUMP_AUDIT]   churn cnts : {dict(_hump1_churn)}")
                print(f"[HUMP_AUDIT] Hump2 ({HUMP2_SOD_LO/3600:.0f}–{HUMP2_SOD_HI/3600:.0f} h):")
                print(f"[HUMP_AUDIT]   fixed PRNs : {sorted(_hump2_fixed_sats)}")
                print(f"[HUMP_AUDIT]   releases   : {_hump2_released}")
                print(f"[HUMP_AUDIT]   quarantined: {sorted(_hump2_quarantined)}")
                print(f"[HUMP_AUDIT]   churn cnts : {dict(_hump2_churn)}")
                _overlap = _hump1_fixed_sats & _hump2_fixed_sats
                print(f"[HUMP_AUDIT]   PRNs in BOTH humps: {sorted(_overlap)}")
                _churn_both = set(s for s in _overlap
                                  if _hump1_churn[s] > 0 and _hump2_churn[s] > 0)
                print(f"[HUMP_AUDIT]   Churning in BOTH  : {sorted(_churn_both)}")
                print("[HUMP_AUDIT] ══════════════════════════════════════════")
        except Exception:
            pass
        # ── end FIX 5 ────────────────────────────────────────────────────────

        # ── 3b. ZWD watchdog ─────────────────────────────────────────────────


        # v103 CHANGE: global nl_fixed.clear() DISABLED — replaced with per-satellite
        # selective release. ZWD rate spike no longer flushes correctly-fixed ambiguities.
        _zwd_buf=getattr(_ppp_pass,'_zwd_buf_'+label,[])
        _zwd_buf.append(x[4])
        if len(_zwd_buf)>5: _zwd_buf.pop(0)
        setattr(_ppp_pass,'_zwd_buf_'+label,_zwd_buf)
        ZWD_RATE_LIMIT=0.015
        if len(_zwd_buf)==5 and (max(_zwd_buf)-min(_zwd_buf))>ZWD_RATE_LIMIT:
            if nl_fixed:
                # v103 CHANGE: release ONLY satellites whose ambiguity residual exceeds
                # NL_RELEASE_THRESH — leave clean fixes untouched.
                _wd_released = []
                for _snl in list(nl_fixed):
                    if _snl not in sidx or abs(x[sidx[_snl]+1]-nl_fixed[_snl][0])>NL_RELEASE_THRESH:
                        _wd_released.append(_snl)
                        nl_fixed.pop(_snl, None)
                        _nl_cf_at_fix.pop(_snl, None)   # v103 FIX: keep cf_at_fix in sync
                        _nl_fix_cooldown[_snl] = 1000  # FIX 3
                        _nl_persist_count[_snl] = 0
                        _nl_events_w.writerow([f"{sod:.1f}", _snl, "ZWD_WD_RELEASE",
                                               f"{max(_zwd_buf)-min(_zwd_buf):+.4f}", "0.0"])
                if _wd_released:
                    # rebuild shared cluster from remaining fixes using stored corr_frac
                    _nl_cluster.clear()
                    for _snl_r in nl_fixed:
                        if _snl_r in _nl_cf_at_fix:
                            _nl_cluster.append(_nl_cf_at_fix[_snl_r])
                    print(f"[ZWD WATCHDOG] SOD={sod:.0f} range={max(_zwd_buf)-min(_zwd_buf):.3f}m "
                          f"→ released {len(_wd_released)} bad sats, kept {len(nl_fixed)}")
                else:
                    print(f"[ZWD WATCHDOG] SOD={sod:.0f} range={max(_zwd_buf)-min(_zwd_buf):.3f}m "
                          f"→ no bad sats found, all {len(nl_fixed)} fixes preserved")
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

        # v97: BEST-4 selection DISABLED — use all satellites passing elevation + quality mask.
        # Locking to 4 sats created satellite instability source; open selection is more robust.
        _nl_preselect = []
        for _mps in geom:
            _sps = _mps['sid']
            if _mps['NWL'] is None: continue
            if _sps not in sidx: continue
            if _mps['age'] < NL_MIN_OBS: continue
            if _mps.get('no_AR', False): continue
            if _sps in _iono_unstable: continue
            _hps = _lp1_hist.get(_sps)
            _rps = 0.0
            if _hps and len(_hps) >= 20:
                _aps = np.array(_hps)
                _rps = float(_aps.max() - _aps.min())
            if _rps > 12.0: continue
            _ki_ps = sidx[_sps]
            _sig_ps = math.sqrt(max(0.0, P[_ki_ps+1, _ki_ps+1])) * _mps['lam1']
            _nl_preselect.append((_sig_ps, _sps))
        _nl_preselect.sort(key=lambda t: t[0])
        # Combined mode: still sort GPS first for anchor priority, but include all
        if 'G' in constellation and 'E' in constellation:
            _nl_preselect.sort(key=lambda t: (0 if not t[1].startswith('E') else 1, t[0]))
        # v97: all eligible satellites (no BEST-4 cap, no locked frozen set)
        _nl_eligible_sids = {sid for _, sid in _nl_preselect}
        # v101 Fix A: NO hard MAX_NL cap — allow all qualified satellites.
        # Hard cap at 3 was the root cause of over-constrained divergence after NL=3.
        # Stricter per-candidate validation (Fixes B/C/D) replaces the blunt count cap.
        _sorted_sats = [sid for _, sid in _nl_preselect]
        _best_sats = set(_sorted_sats)   # all eligible — no count limit

        _nl_epoch_candidates = []   # (sort_key_tuple, sid_m, N1_int, N2_int, frac_for_fix, sigma_N1)
        for m in geom:
            sid_m=m['sid']; NWL_m=m['NWL']
            # v99 Fix 1: HARD STOP — must be absolute first check, before ANY NL logic
            if sid_m in _nl_locked:
                continue
            if NWL_m is None: continue               # need WL first
            if sid_m in nl_fixed: continue           # already fixed this pass
            # v97: removed _nl_fixed_ever permanent lock — allow re-evaluation after release
            if m['age'] < NL_MIN_OBS: continue       # not enough observations
            if sid_m not in sidx: continue

            # v80 PART 1: only allow NL fixing for the pre-selected best-4 sats.
            if sid_m not in _nl_eligible_sids: continue
            # v101 Fix A: _best_sats is now all eligible — no count restriction
            if sid_m not in _best_sats: continue

            # v101 Fix A: NO per-candidate count gate (replaced by validation in commit loop)

            # v80 PART 4: skip satellites in re-fix cooldown (just released).
            if _nl_fix_cooldown.get(sid_m, 0) > 0: continue

            # FIX 2: bad-actor quarantine — sat excluded from NL fixing for remainder of pass
            if sid_m in _nl_quarantine: continue

            if nproc - _last_slip_epoch[sid_m] < 300:
                continue

            # v100 Fix 5: in GPS-only mode, block the very first NL fix until SOD >= 12000
            # (~3.3 h).  The filter must stabilise before GPS integers are committed;
            # early GPS fixes on a poorly-converged solution cause the 700 mm spikes.
            # This gate is inactive in combined (GPS+Galileo) mode — Galileo anchors first.
            if sid_m.startswith('G') and constellation == 'G' and len(nl_fixed) == 0:
                if sod < 12000:
                    continue

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

            # v70 IONO FIX 4: skip NL fixing for iono-unstable sats this epoch
            if sid_m in _iono_unstable: continue
            ki_m=sidx[sid_m]
            # ki_m   → iono (I) slot
            # ki_m+1 → N1 slot  ← CORRECT index for NL sigma
            # ki_m+2 → N2 slot
            lam1 = m['lam1']
            sigma_N1 = math.sqrt(max(0.0, P[ki_m+1, ki_m+1]))   # cycles (kept for candidate tuple)
            # FIX: compute sigma_N1_m directly — do NOT use intermediate sigma_N1 to avoid double-multiply
            sigma_N1_m = math.sqrt(max(0.0, P[ki_m+1, ki_m+1])) * lam1
            # v94 FIX 4: hold condition — if already committed and tight, skip re-entry entirely.
            if sid_m in nl_fixed and sigma_N1_m < 0.10:
                continue
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
            _range_high = False
            if _rng_sat > 12.0:
                _range_high = True          # downweight only — do NOT skip NL

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

            # v91: Debounced N1 jump detection with stability protection.
            # PART 4: buf_n>=30 + frac_std<0.02 → satellite is stable, ignore integer noise.
            # PART 5: jump_count < 3 → DO NOTHING (no reset, no sample blocking).
            # PART 6: reset only when jump persists ≥3 epochs AND sigma_N1_m > 0.10 m.
            # _on_committed_branch gate removed — float oscillation must not starve buf.
            _cur_N1int  = int(round(N1_f))
            _prev_N1int = _nl_last_N1int.get(sid_m)
            _buf_n_now  = len(buf)
            _is_stable  = (_buf_n_now >= 30 and frac_std < 0.02)

            if _prev_N1int is None:
                # First epoch for this satellite — initialise, no action.
                _nl_last_N1int[sid_m] = _cur_N1int
                _nl_jump_count[sid_m] = 0
            elif _is_stable:
                # PART 4: well-converged buffer — float noise crossing integer boundary
                # is irrelevant.  Never reset; clear jump counter to prevent stale count.
                _nl_jump_count[sid_m] = 0
            elif _cur_N1int != _prev_N1int:
                _cnt = _nl_jump_count.get(sid_m, 0) + 1
                _nl_jump_count[sid_m] = _cnt
                # PART 3 + PART 6: real slip only when persistent AND sigma still large
                if _cnt >= 3 and sigma_N1_m > 0.10:
                    buf.clear()
                    _nl_bias.pop(sid_m, None)
                    _nl_bias_frozen.discard(sid_m)
                    _nl_locked.discard(sid_m)          # release hard lock on real slip
                    _nl_stable_count[sid_m] = 0
                    _nl_last_N1int[sid_m] = _cur_N1int
                    _nl_jump_count[sid_m] = 0
                    print(f"[REAL SLIP] {sid_m}  N1 {_prev_N1int}→{_cur_N1int}  "
                          f"buf reset  sigma={sigma_N1_m:.3f}m  cnt={_cnt}")
                # PART 5: cnt < 3 → no reset, allow natural float oscillation
            else:
                # Integer stable this epoch — clear counter.
                _nl_jump_count[sid_m] = 0

            # Sample insertion: _on_committed_branch gate removed.
            # Blocking samples when integer differs from committed was starving buf_n
            # during normal float oscillation near integer boundaries, preventing bias
            # convergence.  frac_std + sigma gates are sufficient protection.
            SIGMA_N1_MAX = 0.25
            if sigma_N1_m < SIGMA_N1_MAX and sid_m not in _nl_bias_frozen \
                    and _drift_ok:
                _sample = raw_frac
                if len(buf) > 0:
                    _ref = buf[-1]
                    if _sample - _ref > +0.5:
                        _sample -= 1.0
                    elif _sample - _ref < -0.5:
                        _sample += 1.0
                buf.append(_sample)
                # (buffer growth is captured in the CSV log below)

            # v89: COMPUTE BIAS AFTER 20 SAMPLES (raised from 15).
            # 15 samples was too few — frac_std was still 0.5 at buf_n=12 for G04.
            # 20 samples gives the circular mean enough data to settle on one branch.
            if len(buf) >= 20 and sid_m not in _nl_bias_frozen:
                _win = list(buf)[-20:]   # windowed — last 20 samples only
                _angles = 2.0 * math.pi * np.array(_win)
                _mean_angle = math.atan2(float(np.mean(np.sin(_angles))),
                                          float(np.mean(np.cos(_angles))))
                _cbias = _mean_angle / (2.0 * math.pi)
                # Wrap to [−0.5, +0.5]
                _cbias = _cbias - round(_cbias)
                # v89: wrong-bias guard — only store the bias if it actually
                # reduces |corr_frac| below 0.10.  Prevents locking in a
                # wrong-mode bias (G04 case: bias=0.23 → corr_frac=0.23 → never fixes).
                _test_nc = N1_f - _cbias
                _test_cf = _test_nc - round(_test_nc)
                if abs(_test_cf) < 0.10:
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
                    _ed_buf.append([f"{sod:.1f}", "NL_BIAS_FROZEN", sid_m,
                                    f"bias={_val_bias:+.4f} frac_std={frac_std:.4f}"
                                    f" corr_frac={_val_cf:+.4f} buf={len(buf)}"])

            # v61 Fix 4: apply bias correction only for the fixing decision.
            # Filter state x[ki_m+1] is NEVER modified.
            bias_m = _nl_bias.get(sid_m, 0.0)
            N1_corr  = N1_f - bias_m
            corr_frac = N1_corr - round(N1_corr)

            # v102 FIX 1: bias-frozen escape valve.
            # If the frozen bias leaves |corr_frac| > 0.15, the circular mean
            # converged to the wrong integer mode. Unfreeze and reset buffer.
            if sid_m in _nl_bias_frozen:
                if abs(corr_frac) > 0.15:
                    _nl_bias_frozen.discard(sid_m)
                    _nl_bias.pop(sid_m, None)
                    buf.clear()
                    _ed_buf.append([f"{sod:.1f}", "NL_BIAS_UNFREEZE", sid_m,
                                    f"corr_frac={corr_frac:.4f}"])
                    _te_w.writerow([f"{sod:.1f}", "NL_BIAS_UNFREEZE", sid_m,
                                    f"{corr_frac:.4f}", "bias_reset"])
                elif frac_std < 0.015:
                    # Soft lock: skip for 60 epochs (not permanently)
                    if _nl_fix_cooldown[sid_m] <= 0:
                        _nl_fix_cooldown[sid_m] = 1000  # FIX 3
                    continue

            # NL_STABLE throttle: once a sat has been committed and marked stable,
            # skip full re-evaluation on non-monitoring epochs.
            # Monitoring epoch = every 30 epochs.
            # Even on monitoring epochs, skip if the sat is clearly stable.
            # Re-evaluate unconditionally only if instability is detected:
            #   abs(corr_frac) > 0.05  OR  sigma_N1_m > 0.10 m  OR  cycle slip.
            if sid_m in _nl_stable:
                _cf_abs = abs(corr_frac)
                _unstable = (_cf_abs > 0.05 or sigma_N1_m > 0.10)
                if not _unstable:
                    if nproc % 30 != 0:
                        continue   # skip on non-monitoring epochs
                    # Monitoring epoch but still stable: skip silently
                    continue

            # v69 PATCH 5: wrong-bias detection — tightened 0.20 → 0.15.
            # If the buffer is well-populated (≥50 samples) but the bias it
            # produced still leaves |corr_frac| > 0.15, the circular mean
            # converged to the wrong mode.  Reset the buffer.
            # Also reset if |mean(buf)| itself > 0.20 (buffer is biased by
            # mixed branches — the single-branch gate didn't catch all cases).
            # [NL_BIAS_RESET] block removed: do NOT reset bias based on corr_frac/mean_buf heuristics.
            # Only cycle slips (handled upstream) may clear the buffer.
            # Resetting here caused Galileo bias to be discarded after convergence.

            # v69 MANDATORY DEBUG → moved to nl_debug.csv (sigma < 0.15 gate)
            if sigma_N1_m < 0.15:
                _nl_debug_w.writerow([f"{sod:.1f}", sid_m,
                                      f"{raw_frac:+.5f}", f"{bias_m:+.5f}",
                                      f"{corr_frac:+.5f}", f"{sigma_N1_m:.4f}",
                                      len(buf), int(sid_m in nl_fixed)])

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

            # SIGMA GATE: reject only if sigma_N1_m > 0.30 m
            if sigma_N1_m > 0.30:
                _ep_skip_sigma += 1
                reject_due_to_sigma += 1
                _nl_events_w.writerow([f"{sod:.1f}", sid_m, "SKIP_SIGMA",
                                       f"{corr_frac:+.4f}", f"{sigma_N1_m:.4f}"])
                continue
            # v88 PART 5+6 — weight factor + NL_R_eff (weaken, not reject):
            # Sats between 0.15 and 0.30 m are allowed but sort key is penalised.
            # NL_R_eff: tighter constraint for well-converged sats, looser for noisy ones.
            _weight_factor = 1.0
            if _range_high:
                _ep_skip_high_range += 1    # count but do NOT reject
                _weight_factor = 4.0        # downweight in sort key only
            if sigma_N1_m > 0.15:
                _weight_factor = 10.0   # weaken in sort key, not rejected
            # v88 PART 6 — NL_R_eff: quality-weighted pseudo-obs noise
            if sigma_N1_m > 0.15:
                _NL_R_eff = (0.03)**2   # 30 mm noise for weak satellites
            elif sigma_N1_m < 0.10:
                _NL_R_eff = (0.003)**2  # 3 mm noise for tight satellites
            else:
                _NL_R_eff = NL_R_TIGHT  # default

            # ── v64 GATE C (FIX 2 + FIX 5): require buf_n ≥ 15 AND frac_std < 0.02.
            # FIX 5 — DO NOT FIX WITHOUT BIAS: if fewer than 15 samples have been
            # collected, the bias estimate is unreliable → skip NL entirely.
            # No raw_frac fallback: premature fixing with an unknown bias is the
            # primary cause of catastrophic divergence.
            buf_n = len(buf)
            if buf_n < 30:
                continue   # v97: require 30 samples (was 80) before fixing

            # v102 CHANGE: frac_std gate and constellation sigma gate removed.
            # Unified condition (abs(corr_frac) < 0.03 and sigma_N1_m < 0.07) below handles both.

            # v102 CHANGE: unified fix condition replaces all multi-stage gates.
            # (removed: frac_std strict gate, constellation sigma gate, persistence gate,
            #  extra sigma gate, strict 0.02 corr_frac gate, NL_RES_THRESH gate)

            frac_for_fix = corr_frac

            # Branch correction: try ±1 cycle adjustment if it reduces |corr_frac|
            for _badj in [-1.0, +1.0]:
                _try_nc = N1_f - (bias_m + _badj)
                _try_cf = _try_nc - round(_try_nc)
                if abs(_try_cf) < abs(corr_frac):
                    N1_corr      = _try_nc
                    corr_frac    = _try_cf
                    frac_for_fix = corr_frac
                    break

            # Single unified acceptance gate
            if not (abs(corr_frac) < 0.03 and sigma_N1_m < 0.07):
                continue

            # [NL CHECK] → nl_events.csv; console silent

            # Integer is taken from the bias-corrected value.  Filter state is NOT modified.
            N1_int = int(round(N1_corr))
            N2_int=N1_int-NWL_m
            # (v102 CHANGE: SKIP_POSTFIX check removed — unified gate abs(corr_frac)<0.03
            #  guarantees |N1_corr - N1_int| < 0.03 already)

            # v66 FIX 3+4: collect candidate for post-loop sort-and-limit.
            # Sort purely by signal quality (|corr_frac| + sigma * weight_factor).
            # No constellation priority — both GPS and Galileo compete equally.
            _is_gal_cand = sid_m.startswith('E')
            _sort_key = (abs(frac_for_fix) + sigma_N1_m * _weight_factor,)
            _nl_epoch_candidates.append(
                (_sort_key, sid_m, N1_int, N2_int, frac_for_fix, sigma_N1, sigma_N1_m, _NL_R_eff))

        # v78 PART 4 — SINGLE NL UPDATE ONLY
        _nl_epoch_candidates.sort(key=lambda c: c[0])
        # v_diag: disable NL fixing completely to verify float PPP quality
        if DISABLE_NL_FIXING:
            _nl_epoch_candidates = []
        # [NL_FLOW] / [CHECK] throttled prints → removed (data preserved in nl_events.csv)
        _after_sigma = _nl_epoch_candidates   # PART 3: no second filtering
        _nl_fixed_this_ep = []
        # PART 6 — SAFETY: skip NL fixing if insufficient geometry
        if len(_after_sigma) < 2:
            pass   # allow through if already have fixes; geometry guard for fresh-start only
        # v101 Fix A: NO hard MAX_NL_TOTAL cap.
        # v102 FIX 2: 2 fixes/epoch in combined mode so Galileo is not starved by GPS priority sort.
        _max_new_fixes = 2 if ('G' in constellation and 'E' in constellation) else 1
        for _cand in _nl_epoch_candidates[:_max_new_fixes]:
            _, sid_m, N1_int, N2_int, frac_for_fix, sigma_N1, sigma_N1_m, _cand_R_eff = _cand
            # v99 Fix 5: never re-fix a locked satellite
            if sid_m in _nl_locked:
                continue
            # Float proximity gate: reject if float ambiguity is not within 0.20 cycles of an integer.
            if sid_m in sidx:
                _N1_float_prox = x[sidx[sid_m]+1]
                if abs(_N1_float_prox - round(_N1_float_prox)) > 0.20:
                    continue
            # v102 CHANGE: unified single-cluster consistency check — all constellations.
            # v103 FIX: cluster stores corr_frac (not raw float-minus-integer residuals).
            # Uses circular mean/diff to avoid wrapping artefacts near ±0.5.
            if nl_fixed and sid_m not in nl_fixed:
                _cluster_cfs = [_nl_cf_at_fix[s] for s in nl_fixed if s in _nl_cf_at_fix]
                if len(_cluster_cfs) >= 2:
                    _cluster_mean = _nl_circ_mean(_cluster_cfs)
                    # v103 FIX 3: if cluster mean is biased > 0.25 cyc the whole cluster
                    # is corrupt — flush it so new fixes can anchor a fresh cluster.
                    if abs(_cluster_mean) > 0.25:
                        _ed_buf.append([f"{sod:.1f}", "NL_CLUSTER_FLUSH", sid_m,
                                        f"mean={_cluster_mean:.4f}>0.25"])
                        nl_fixed.clear()
                        _nl_cluster.clear()
                        _nl_cf_at_fix.clear()
                        _nl_events_w.writerow([f"{sod:.1f}", sid_m, "CLUSTER_FLUSH",
                                               f"{_cluster_mean:+.4f}", "0.0"])
                        # allow current candidate to proceed with empty cluster
                    else:
                        _nl_consist_thresh = 0.35 if len(_cluster_cfs) < 3 else 0.12
                        _circ_d = abs(_nl_circ_diff(corr_frac, _cluster_mean))
                        # [NL_CLUSTER] → nl_events.csv on reject; console silent
                        if _circ_d > _nl_consist_thresh:
                            print(f"[NL_CONSISTENCY_REJECT] {sid_m} corr_frac={corr_frac:.4f} "
                                  f"cluster_mean={_cluster_mean:.4f} circ_diff={_circ_d:.4f} "
                                  f"thresh={_nl_consist_thresh:.2f}")
                            _nl_events_w.writerow([f"{sod:.1f}", sid_m, "SKIP_CONSISTENCY",
                                                   f"{corr_frac:+.4f}", f"{sigma_N1_m:.4f}"])
                            continue
                else:
                    pass  # [NL_CLUSTER] size<2 → console silent
            # v102 CHANGE: post-fix rejection DISABLED — removed NL_POSTFIX_REJECT checks.
            # Previously: if |N1_float - N1_int| > 0.05, reject fix. Now bypassed entirely.
            # v101 Fix F: ISB stability gate REMOVED — improved ISB anchoring
            # (soft pseudo-obs) prevents P_ISB blowup, so this gate is no longer needed.
            # Previously: if not _isb_stable and len(nl_fixed) >= 2: break
            # v97: commit fix (removed permanent _nl_fixed_ever lock — allow re-evaluation)
            if sid_m not in nl_fixed:
                # FIX 4: dominant-satellite guard — if this satellite contributes >35% Up leverage
                # when already fixed, withhold new commits that would lock it in again.
                # Computed from the current normal equation Up contributions.
                _dom_suppress = False
                if nl_fixed and 'H_p' in dir() and sidx:
                    try:
                        _dom_rxyz = nom + x[:3]
                        _dom_lr, _dom_lo, _ = _lla(_dom_rxyz)
                        _dom_up = np.array([math.cos(_dom_lr)*math.cos(_dom_lo),
                                            math.cos(_dom_lr)*math.sin(_dom_lo),
                                            math.sin(_dom_lr)])
                        _dom_up_full = np.zeros(len(x)); _dom_up_full[0:3] = _dom_up
                        _dom_Rinv = 1.0 / np.maximum(np.diag(R_main), 1e-30)
                        _dom_n_rows = H_p.shape[0] - 1
                        _dom_total = 0.0; _dom_sat_ne = 0.0
                        for _dom_ri in range(_dom_n_rows):
                            _dom_idx = _dom_ri // 4
                            if _dom_idx >= len(geom): continue
                            _dom_h_u = float(H_p[_dom_ri] @ _dom_up_full)
                            _dom_c = _dom_Rinv[_dom_ri] * _dom_h_u ** 2
                            _dom_total += _dom_c
                            if geom[_dom_idx]['sid'] == sid_m:
                                _dom_sat_ne += _dom_c
                        _dom_lev = _dom_sat_ne / max(_dom_total, 1e-30)
                        if _dom_lev > 0.35:
                            _dom_suppress = True
                            _ed_buf.append([f"{sod:.1f}", "DOMINANT_FIX_SUPPRESS", sid_m,
                                            f"up_leverage={_dom_lev:.3f}"])
                            _nl_events_w.writerow([f"{sod:.1f}", sid_m, "DOMINANT_SUPPRESS",
                                                   f"{corr_frac:+.4f}", f"{_dom_lev:.4f}"])
                    except Exception:
                        pass
                if _dom_suppress:
                    pass  # skip commit this epoch; will retry when geometry changes
                else:
                    nl_fixed[sid_m] = (N1_int, N2_int)
                    _nl_fixed_this_ep.append(sid_m)   # v101 Fix D: track for divergence undo
                    _nl_fix_start_sod[sid_m] = sod
                    _nl_R_eff_map[sid_m] = _cand_R_eff
                    _ep_nl_count += 1
                    _nl_persist_count[sid_m] = 0   # reset after commit
                    # v103 FIX: store corr_frac (not raw residual) — this is the cluster source of truth
                    _nl_cf_at_fix[sid_m] = float(frac_for_fix)
                    _fix_resid = (x[sidx[sid_m]+1] - N1_int) if sid_m in sidx else 0.0
                    _nl_cluster.append(float(_fix_resid))  # kept for ZWD watchdog rebuild compat
                    # NL_STABLE: mark as stable if sigma is already tight at fix time
                    if sigma_N1_m < 0.10:
                        _nl_stable.add(sid_m)
                    if sid_m[0] == 'G':
                        _gps_nl_fixed_ever = True
                    # FIX 1: track re-fix count (fix after at least one prior release)
                    if _nl_release_count[sid_m] > 0:
                        _nl_refix_count[sid_m] += 1
                    _ed_buf.append([f"{sod:.1f}", "NL_FIX", sid_m,
                                    f"corr_frac={frac_for_fix:+.4f} sigma={sigma_N1_m:.3f}"
                                    f" refix={_nl_refix_count[sid_m]}"
                                    f"{'  NL_STABLE' if sid_m in _nl_stable else ''}"])
                    _nl_events_w.writerow([f"{sod:.1f}", sid_m, "FIX",
                                           f"{frac_for_fix:+.4f}", f"{sigma_N1_m:.4f}"])
                    _te_w.writerow([f"{sod:.1f}", "NL_FIX", sid_m,
                                    f"{frac_for_fix:+.4f}",
                                    f"sigma={sigma_N1_m:.4f} NL_total={len(nl_fixed)+1}"])


        # v89: REMOVE WEAK FIXES: lower threshold 0.30→0.20 m — evicts bad fixes faster
        # v97: removed _nl_fixed_ever permanent lock — allow eviction on sigma growth
        for _sid_wk in list(nl_fixed.keys()):
            if _sid_wk not in sidx:
                continue
            _ki_wk   = sidx[_sid_wk]
            _lam1_wk = _sat_lam1.get(_sid_wk, 0.1903)
            _sig_wk  = math.sqrt(max(0.0, P[_ki_wk+1, _ki_wk+1])) * _lam1_wk
            if _sig_wk > 0.20:
                nl_fixed.pop(_sid_wk, None)
                _nl_R_eff_map.pop(_sid_wk, None)
                _nl_stable.discard(_sid_wk)   # NL_STABLE: cleared when weak-evicted
            elif _sig_wk < 0.10 and _sid_wk not in _nl_stable:
                _nl_stable.add(_sid_wk)       # NL_STABLE: promote when sigma tightens post-fix

        # v79 PART 6 — accumulate epoch skip counts into 300-epoch window totals
        _nl_skip_no_osb      += _ep_skip_no_osb
        _nl_skip_bad_bias    += _ep_skip_bad_bias
        _nl_skip_high_range  += _ep_skip_high_range
        _nl_skip_sigma_accum += _ep_skip_sigma
        _nl_count_accum      += len(nl_fixed)   # snapshot of total currently fixed
        # PATCH 1: freeze gal_sig_scale once ≥3 sats have been fixed for 60 consecutive epochs
        if len(nl_fixed) >= 3:
            _nl_stable_freeze_ctr += 1
            if not _gal_scale_frozen and _nl_stable_freeze_ctr >= _SCALE_FREEZE_EPOCHS:
                _gal_scale_frozen = True
                print(f"[GAL_SCALE_FROZEN] SOD={sod:.0f}  nl_fixed={len(nl_fixed)}"
                      f"  gal_scale locked at {_gal_scale_adaptive:.4f}")
        else:
            _nl_stable_freeze_ctr = 0

        # ── 3d. Inject NL pseudo-observations ────────────────────────────────
        # v63 FIX 2: apply NL constraint as soon as ≥1 satellite is fixed.
        # NL_MIN_SATS is no longer a gate on constraint injection — it was
        # preventing fixes from being used because IISC only fixes 1–2 sats.
        # v63 FIX 5: innovation gate no longer permanently drops a fix.  If the
        #   float drifted > NL_INNOV_GATE this epoch, skip the constraint for
        #   this epoch only.  The release check (3a) handles persistent drift.
        # v63 FIX 6: debug print of active NL sats every epoch.
        if nl_fixed and len(geom) >= 4:
            # Build (H, target_integer, R) list — z recomputed each iteration
            nl_pairs=[]   # (h_vector, N_target_float, R)
            for sid_nl,(N1_i,N2_i) in list(nl_fixed.items()):
                if sid_nl not in sidx: continue
                ki_nl=sidx[sid_nl]
                # NL_STABLE: inject constraint only once every 5 epochs.
                # Already-stable sats don't need per-epoch tightening —
                # the filter ambiguity is held by the existing tight P.
                if sid_nl in _nl_stable and nproc % 5 != 0:
                    continue
                # v80 PART 5 — RELAX INNOVATION GATE (0.25 → 0.35):
                # Wider gate allows constraint injection for satellites whose
                # float ambiguity has drifted slightly but is still recoverable.
                # The release check (3a) handles persistent large drift separately.
                _innov_nl = x[ki_nl+1]-N1_i
                # FIX 1: accumulate per-sat phase residual for lifetime monitor
                _nl_phase_resid_buf[sid_nl].append(abs(float(_innov_nl)))

                # if abs(_innov_nl) > NL_INNOV_GATE:
                #     reject_due_to_innov += 1
                #     continue
                # v101 Fix E: SOFT WEIGHTING — constraint strength grows with fix age.
                _lam1_nl  = _sat_lam1.get(sid_nl, 0.1903)
                _sigma_nl = math.sqrt(max(0.0, P[ki_nl+1, ki_nl+1])) * _lam1_nl
                # NL_R_eff is loosened for new fixes and tightened gradually over epochs.
                # This prevents the sudden P-collapse that causes divergence.
                _fix_age_ep = int(sod - _nl_fix_start_sod.get(sid_nl, sod)) // 30
                if sid_nl in _nl_stable:
                    # Stable sat: moderate constraint — not the tightest (avoid over-constraint)
                    NL_R_eff = (0.008)**2    # 8 mm — relaxed from original 1 cm to prevent over-constraint
                elif _sigma_nl < 0.10:
                    # Young fix, tight sigma: start soft, tighten over 10 epochs
                    _soft_scale = max(1.0, 3.0 - _fix_age_ep * 0.2)  # 3× → 1× over 10 ep
                    NL_R_eff = (0.003 * _soft_scale)**2
                elif _sigma_nl < 0.20:
                    NL_R_eff = (0.012)**2    # medium constraint (12 mm)
                else:
                    NL_R_eff = (0.035)**2    # weak constraint (35 mm)
                # v85 PART 2 — constellation-aware boost
                if sid_nl[0] == 'E':        # Galileo: tighten
                    NL_R_eff *= 0.5
                elif sid_nl[0] == 'G':      # GPS: relax
                    NL_R_eff *= 1.5
                # N1 pseudo-obs: z_target = N1_int,  H[ki+1] = 1
                h1=np.zeros(len(x)); h1[ki_nl+1]=1.
                nl_pairs.append((h1, float(N1_i), NL_R_eff, sid_nl, 'N1', ki_nl+1))
                # N2 pseudo-obs: z_target = N2_int,  H[ki+2] = 1
                h2=np.zeros(len(x)); h2[ki_nl+2]=1.
                nl_pairs.append((h2, float(N2_i), NL_R_eff, sid_nl, 'N2', ki_nl+2))
            # ── Audit 3: duplicate pseudo-obs guard ──────────────────────────
            # Each satellite should contribute exactly 2 rows (N1 + N2).
            # Detect and drop any duplicate state-index rows before injection.
            _seen_nl_sidx = set()
            _dedup_nl = []
            for _np in nl_pairs:
                _key = (_np[3], _np[4])   # (sid, 'N1'|'N2')
                if _key in _seen_nl_sidx:
                    print(f"[NL_DUP_GUARD] SOD={sod:.0f} duplicate pseudo-obs "
                          f"sat={_np[3]} slot={_np[4]} — dropped")
                else:
                    _seen_nl_sidx.add(_key)
                    _dedup_nl.append(_np)
            nl_pairs = _dedup_nl
            # ─────────────────────────────────────────────────────────────────
            if nl_pairs:
                H_nl  = np.array([p[0] for p in nl_pairs])   # (n_rows, nst)
                Rd_nl = np.array([p[2] for p in nl_pairs])
                _nl_sids_active = list(dict.fromkeys(p[3] for p in nl_pairs))  # unique, ordered
                for _nl_iter in range(2):
                    # Recompute innovations each pass from current x
                    z_nl = np.array([p[1] - float(p[0] @ x) for p in nl_pairs])
                    R_nl = np.diag(Rd_nl) + np.eye(len(Rd_nl)) * 1e-6
                    # Capture N1 diagonal BEFORE update
                    _nl_before = {s: P[sidx[s]+1, sidx[s]+1] for s in _nl_sids_active}
                    _ret = filter_standard(x, P, H_nl.T, z_nl, R_nl)
                    if _ret != 0:
                        break
                    # v89: re-apply tightened iono variance cap (25 m²) after NL update
                    for _sid_nl_c, _ki_nl_c in sidx.items():
                        P[_ki_nl_c, _ki_nl_c] = min(P[_ki_nl_c, _ki_nl_c], 25.0)
                    # v89: iono damping nudge REMOVED — was corrupting I states
                    #      and inflating sigma_N1 by pulling states away from filter optimum
                    # v58 Fix 2: NaN guard — stop iterating if state went bad
                    if not np.isfinite(x).all() or not np.isfinite(P).all():
                        x = np.where(np.isfinite(x), x, 0.0)
                        P = np.where(np.isfinite(P), P, 0.0)
                        np.fill_diagonal(P, np.maximum(np.diag(P), 100.**2))
                        P *= 100.
                        # v96: only clear all fixes if >50% are non-finite
                        _ng_tot=len(nl_fixed)
                        _ng_inv=sum(1 for _sn in list(nl_fixed)
                                    if _sn not in sidx or not np.isfinite(x[sidx[_sn]+1]))
                        if _ng_tot==0 or _ng_inv>_ng_tot//2:
                            nl_fixed.clear(); _nl_cluster.clear(); _nl_cf_at_fix.clear()  # v103 FIX: clear all cluster state
                            print(f"[NaN GUARD NL] SOD={sod:.0f} iter={_nl_iter} — "
                                  f"NaN after NL injection; released all fixes")
                        else:
                            print(f"[NaN GUARD NL] SOD={sod:.0f} iter={_nl_iter} — "
                                  f"NaN after NL injection; preserved {_ng_tot-_ng_inv}/{_ng_tot} fixes")
                        break

        n_nl=len(nl_fixed)

        # v101 Fix D: divergence detector — if position jumped > 0.25 m after
        # NL pseudo-obs injection, undo the most recently added NL fix.
        _pos_after_nl = nom + x[:3]
        if hasattr(_ppp_pass, '_pos_before_nl_' + label):
            _pos_before_nl = getattr(_ppp_pass, '_pos_before_nl_' + label)
            _pos_jump = np.linalg.norm(_pos_after_nl - _pos_before_nl)
            if _pos_jump > 0.25 and _nl_fixed_this_ep:
                _bad_sid = _nl_fixed_this_ep[-1]
                print(f"[NL_DIVERGE_UNDO] SOD={sod:.0f} jump={_pos_jump*1000:.1f}mm "
                      f"→ undoing fix for {_bad_sid}")
                nl_fixed.pop(_bad_sid, None)
                _nl_cf_at_fix.pop(_bad_sid, None)   # v103 FIX: remove stale corr_frac
                _nl_R_eff_map.pop(_bad_sid, None)
                _nl_stable.discard(_bad_sid)
                _nl_fix_cooldown[_bad_sid] = 1000  # FIX 3: 1000-epoch cooldown after bad fix
                _nl_persist_count[_bad_sid] = 0
                _nl_events_w.writerow([f"{sod:.1f}", _bad_sid, "DIVERGE_UNDO",
                                       f"{_pos_jump*1000:+.1f}", f"0.0"])
                _te_w.writerow([f"{sod:.1f}", "DIVERGE_UNDO", _bad_sid,
                                f"{_pos_jump*1000:.1f}", "jump_mm"])
                n_nl = len(nl_fixed)
        setattr(_ppp_pass, '_pos_before_nl_' + label, _pos_after_nl.copy())

        # ── v105: cycle-slip recovery for NL-fixed satellites ────────────────
        # When phase residual or ambiguity drift indicates a cycle slip on a
        # fixed satellite, perform a full ambiguity state reset rather than
        # merely releasing.  This prevents slip-corrupted integers from
        # contaminating iono/position states through continued constraint injection.
        # Cooldown of 90 epochs (2.7 min at 30 s) prevents immediate re-fix.
        NL_SLIP_PHASE_THRESH  = PHASE_SPIKE_THRESH  # metres — reuse existing threshold
        NL_SLIP_RESID_THRESH  = 0.25   # v106 FIX: raised from NL_RELEASE_THRESH (0.050) to 0.25 cyc
        # 0.050 fired same-epoch as every fresh fix (float hasn't converged to integer in 1 step).
        # 0.25 = genuine half-cycle error indicating wrong integer commitment only.
        NL_SLIP_COOLDOWN      = 90                  # epochs before re-fix allowed
        NL_SLIP_P_INFLATE     = 300.**2             # variance to inject on reset (m²)

        for sid, (N1_int, N2_int) in list(nl_fixed.items()):
            if sid not in sidx:
                continue
            ki = sidx[sid]

            # v106 FIX: grace period — skip SLIP RESET for satellites fixed THIS epoch.
            # The float has not converged to the integer in 1 NL injection step; firing
            # immediately causes fix→zero→re-convergence loops every epoch.
            if sod - _nl_fix_start_sod.get(sid, sod) < 30:
                continue

            resid_amb  = abs(x[ki+1] - N1_int)
            phase_resid = phase_res_by_sid.get(sid, 0.0)

            _lam1_sp = _sat_lam1.get(sid, 0.1903)
            _sig_sp  = math.sqrt(max(0.0, P[ki+1, ki+1])) * _lam1_sp

            if resid_amb > NL_SLIP_RESID_THRESH or phase_resid > NL_SLIP_PHASE_THRESH:
                print(f"[NL_RELEASE_SPIKE_DIAG] SOD={sod:.0f} {sid} "
                      f"resid_amb={resid_amb:.3f} phase_resid={phase_resid:.3f} "
                      f"sigma_N1={_sig_sp:.4f} → SLIP RESET")
                _nl_events_w.writerow([f"{sod:.1f}", sid, "SLIP_RESET",
                                       f"{resid_amb:+.4f}", f"{_sig_sp:.4f}"])
                _te_w.writerow([f"{sod:.1f}", "SLIP_RESET", sid,
                                f"{resid_amb:.4f}",
                                f"ph_resid={phase_resid:.3f} sig={_sig_sp:.4f}"])

                # 1. Remove from nl_fixed
                nl_fixed.pop(sid, None)

                # 2. v106 FIX: do NOT zero x[ki+1]/x[ki+2] — preserve float estimate.
                #    Zeroing discards converged float information and forces cold restart.
                #    Just remove the integer commitment (nl_fixed.pop above) and inflate P.
                # x[ki+1] = 0.0  # REMOVED: catastrophic for fast-converging satellites
                # x[ki+2] = 0.0  # REMOVED

                # 3. Inflate ambiguity covariance strongly — force re-convergence
                P[ki+1, ki+1] = NL_SLIP_P_INFLATE
                P[ki+2, ki+2] = NL_SLIP_P_INFLATE
                # Zero cross-terms involving these states to decouple
                P[ki+1, :] = 0.0; P[:, ki+1] = 0.0
                P[ki+2, :] = 0.0; P[:, ki+2] = 0.0
                P[ki+1, ki+1] = NL_SLIP_P_INFLATE
                P[ki+2, ki+2] = NL_SLIP_P_INFLATE

                # 4. Clear all NL bias/buffer/cluster state for this satellite
                #    v106: only clear bias buffer if resid_amb > 0.30 (wrong integer).
                #    For small resid_amb (0.25-0.30) the bias is likely correct but the
                #    float needs more NL injections to converge — preserve bias.
                if resid_amb > 0.30:
                    _nl_frac_buf[sid].clear()
                    _nl_bias.pop(sid, None)
                    _nl_frac_hist[sid].clear()
                    _nl_bias_frozen.discard(sid)
                _nl_cf_at_fix.pop(sid, None)
                _nl_R_eff_map.pop(sid, None)

                # Rebuild cluster without this satellite
                _nl_cluster.clear()
                for _snl_r in nl_fixed:
                    if _snl_r in _nl_cf_at_fix:
                        _nl_cluster.append(_nl_cf_at_fix[_snl_r])

                # 5. Clear fix-tracking state and apply cooldown
                _nl_stable.discard(sid)
                _nl_locked.discard(sid)
                _nl_stable_count[sid]  = 0
                _nl_persist_count[sid] = 0
                _nl_drift_count.pop(sid, None)
                _nl_fix_start_sod.pop(sid, None)
                _nl_fix_cooldown[sid] = NL_SLIP_COOLDOWN  # block re-fix for 90 epochs

                # Also reset phase continuity tracking so next arc starts clean
                _lp1_hist[sid].clear()

        n_nl = len(nl_fixed)
        # ── end v105 cycle-slip recovery ─────────────────────────────────────

        # ── v108: per-epoch turbulence event tracking for gal_sig_scale guard ──
        # Detects: new NL/WL fix, slip reset, ambiguity release, nGal change >1.
        # Result pushed into _gal_scale_event_buf (10-epoch rolling window).
        try:
            _ep_n_gal_now  = sum(1 for _mm in geom if _mm['sid'][0] == 'E')
            _ep_nl_set_now = set(nl_fixed.keys())
            _ep_new_nl_fix  = bool(_ep_nl_set_now - _gal_scale_prev_nl_set)
            _ep_nl_release  = bool(_gal_scale_prev_nl_set - _ep_nl_set_now)
            _ep_ngal_jump   = abs(_ep_n_gal_now - _gal_scale_prev_nGal) > 1
            _ep_had_slip    = any(_last_slip_epoch.get(_ss, -9999) == nproc
                                  for _ss in sidx)
            _ep_turbulent   = _ep_new_nl_fix or _ep_nl_release or _ep_ngal_jump or _ep_had_slip
            _gal_scale_event_buf.append(_ep_turbulent)
            _gal_scale_prev_nGal    = _ep_n_gal_now
            _gal_scale_prev_nl_set  = _ep_nl_set_now
        except Exception:
            _gal_scale_event_buf.append(False)
        # ─────────────────────────────────────────────────────────────────────

        # v100 Fix 1: ISB reset DISABLED — filter estimates ISB via KF; resetting
        # introduces a discontinuity that corrupts the RTS smoother and spikes 3D error.
        # _isb_reinitialized left False so isb.csv column stays valid.
        pass  # ISB reset removed

        nproc+=1
        _nproc_global = nproc
        if nproc % 100 == 0:
            _bias_csv_fh.flush()
            _nl_debug_fh.flush(); _float_diag_fh.flush()
            _nl_events_fh.flush(); _summary_fh.flush(); _isb_fh.flush()
            _fusion_diag_fh.flush(); _aa_fh.flush(); _churn_fh.flush()
            # flush state_diag and event_diag buffers
            for _row in _sd_buf:
                _sd_w.writerow(_row)
            _sd_buf.clear()
            _sd_fh.flush()
            for _row in _ed_buf:
                _ed_w.writerow(_row)
            _ed_buf.clear()
            _ed_fh.flush()
        pos=nom+x[:3]; dx=pos-REF; d3=np.linalg.norm(dx)*1e3
        # ── state_diag.csv: write every 60 epochs ────────────────────────────
        if nproc % 60 == 0:
            try:
                _sd_rxyz = nom + x[:3]
                _sd_lr, _sd_lo, _sd_la = _lla(_sd_rxyz)
                _sd_up = np.array([math.cos(_sd_lr)*math.cos(_sd_lo),
                                   math.cos(_sd_lr)*math.sin(_sd_lo),
                                   math.sin(_sd_lr)])
                _sd_u_err = float((_sd_rxyz - REF) @ _sd_up) * 1e3
                _sd_condNM = _geom_cond_nm if not math.isnan(_geom_cond_nm) else -1.0
                _sd_corr   = _corr_ep
                _sd_nGPS   = sum(1 for _m in geom if _m['sid'][0] == 'G')
                _sd_nGal   = sum(1 for _m in geom if _m['sid'][0] == 'E')
                _sd_domsat = _geom_lev_sat_dw if _geom_lev_sat_dw else (_geom_cond_sat_dw or "none")
                _sd_uplev  = _geom_worst_lev if not math.isnan(_geom_worst_lev) else -1.0
                _sd_buf.append([
                    f"{sod:.1f}", f"{d3:.2f}", f"{_sd_u_err:.2f}",
                    len(nl_fixed), f"{_sd_condNM:.2e}",
                    f"{_sd_corr:+.4f}" if not math.isnan(_sd_corr) else "nan",
                    _sd_nGPS, _sd_nGal,
                    _sd_domsat, f"{_sd_uplev:.3f}",
                ])
            except Exception:
                pass
        # ─────────────────────────────────────────────────────────────────────
        # v108: accumulate hump1/hump2 3D error² for per-window RMS diagnostics
        if HUMP1_SOD_LO <= sod <= HUMP1_SOD_HI:
            _hump1_d3_sq.append(d3 * d3)
        if HUMP2_SOD_LO <= sod <= HUMP2_SOD_HI:
            _hump2_d3_sq.append(d3 * d3)
        # v78 PART 7 — low-frequency debug every 300 epochs
        if nproc % 300 == 0 and sidx:
            _nl_count_dbg = len(nl_fixed)
            _sig_vals_dbg = [math.sqrt(max(0.0, P[ki+1, ki+1])) * 0.1903
                             for ki in sidx.values()]
            _mean_sig_dbg = float(np.mean(_sig_vals_dbg)) if _sig_vals_dbg else 0.0
            _max_sig_dbg  = float(max(_sig_vals_dbg))     if _sig_vals_dbg else 0.0
            # PART 2 — clean SUMMARY console print every 300 epochs
            print(f"[SUMMARY] SOD={sod:.0f}  sats={len(geom)}"
                  f"  NL={_nl_count_dbg}  WL={len(wl_fixed)}"
                  f"  3D={d3:.1f}mm")

            # PART B — [ISB_DIAG]: last-epoch residuals + adaptive scale update
            _n_gps_ep = sum(1 for _m in geom if _m['sid'][0]=='G')
            _n_gal_ep = sum(1 for _m in geom if _m['sid'][0]=='E')
            _gps_c_rms = math.sqrt(sum(_gps_code_res2)/max(len(_gps_code_res2),1))*1e3 if _gps_code_res2 else float('nan')
            _gal_c_rms = math.sqrt(sum(_gal_code_res2)/max(len(_gal_code_res2),1))*1e3 if _gal_code_res2 else float('nan')
            _gps_p_rms = math.sqrt(sum(_gps_phs_res2 )/max(len(_gps_phs_res2 ),1))*1e3 if _gps_phs_res2  else float('nan')
            _gal_p_rms = math.sqrt(sum(_gal_phs_res2 )/max(len(_gal_phs_res2 ),1))*1e3 if _gal_phs_res2  else float('nan')
            # [ISB_DIAG] → isb.csv; console silent
            # Adaptive constellation weight correction from running phase residual ratio
            # PATCH 1: skip entirely once gal_scale is frozen (stable NL cluster established)
            if len(_isb_gps_ph2_acc) >= 100 and len(_isb_gal_ph2_acc) >= 100 \
                    and not _gal_scale_frozen:
                _gps_var_run = float(np.mean(_isb_gps_ph2_acc))
                _gal_var_run = float(np.mean(_isb_gal_ph2_acc))
                _ratio_run   = math.sqrt(_gal_var_run / max(_gps_var_run, 1e-14))
                pass  # [ISB_SCALE] → isb.csv; console silent

                # ── v108 scale update: LP + slew-rate + event-hysteresis + geom-freeze ──
                # Compute raw target from residual ratio (same branches as before)
                if _ratio_run > 1.2:
                    _target_scale = min(1.00, _ratio_run)
                elif _ratio_run < 0.85:
                    _target_scale = max(0.70, _ratio_run)
                else:
                    _target_scale = 1.0   # decay target

                # Step 1: low-pass filter
                _new_adapt_lp = (1.0 - _GAL_SCALE_LP_ALPHA) * _gal_scale_adaptive \
                                 + _GAL_SCALE_LP_ALPHA * _target_scale

                # Step 2: hard slew-rate limiter
                _raw_delta  = _new_adapt_lp - _gal_scale_adaptive
                _clamped_delta = max(-_GAL_SCALE_MAX_SLEW,
                                     min(_GAL_SCALE_MAX_SLEW, _raw_delta))
                _new_adapt  = _gal_scale_adaptive + _clamped_delta

                # Step 3: event-hysteresis guard — block if turbulent in last 10 epochs
                _guard_active = any(_gal_scale_event_buf)

                # Step 4: geometry freeze — block if ill-conditioned or high hump-risk
                _geom_freeze  = ((not math.isnan(_nml_last_cond_bal) and _nml_last_cond_bal > 1000)
                                 or _nml_last_hump_risk > 0.25)

                _scale_hysteresis_pending = _new_adapt   # log compat
                _scale_hysteresis_ctr     = int(_guard_active)

                if _guard_active:
                    _ed_buf.append([f"{sod:.1f}", "SCALE_GUARD_BLOCK", "",
                                    f"scale={_gal_scale_adaptive:.4f} target={_target_scale:.4f}"
                                    f" ratio={_ratio_run:.3f}"])
                    _te_w.writerow([f"{sod:.1f}", "SCALE_BLOCK", "",
                                    f"{_gal_scale_adaptive:.4f}",
                                    f"reason=event_hysteresis ratio={_ratio_run:.3f}"])
                elif _geom_freeze:
                    _ed_buf.append([f"{sod:.1f}", "SCALE_GEOM_FREEZE", "",
                                    f"scale={_gal_scale_adaptive:.4f}"
                                    f" cond={_nml_last_cond_bal:.2e}"
                                    f" hump_risk={_nml_last_hump_risk:.3f}"])
                    _te_w.writerow([f"{sod:.1f}", "SCALE_GEOM_FREEZE", "",
                                    f"{_gal_scale_adaptive:.4f}",
                                    f"cond={_nml_last_cond_bal:.2e} hump_risk={_nml_last_hump_risk:.3f}"])
                else:
                    # Commit smoothed, slew-limited update
                    if abs(_new_adapt - _gal_scale_adaptive) > 0.001:
                        _ed_buf.append([f"{sod:.1f}", "SCALE_UPDATE", "",
                                        f"{_gal_scale_adaptive:.4f}->{_new_adapt:.4f}"
                                        f" target={_target_scale:.4f}"])
                    _te_w.writerow([f"{sod:.1f}", "SCALE_UPDATE", "",
                                    f"{_new_adapt:.4f}",
                                    f"old={_gal_scale_adaptive:.4f} ratio={_ratio_run:.3f}"
                                    f" lp_delta={_clamped_delta:+.4f}"])
                    _gal_scale_adaptive = _new_adapt
                    _scale_hysteresis_pending = float('nan')
                    _scale_hysteresis_ctr     = 0
                # ── end v108 scale update ──────────────────────────────────────

                _isb_gps_ph2_acc.clear()
                _isb_gal_ph2_acc.clear()
            elif len(_isb_gps_ph2_acc) >= 100 and len(_isb_gal_ph2_acc) >= 100:
                # Frozen — silently drain accumulators
                _isb_gps_ph2_acc.clear()
                _isb_gal_ph2_acc.clear()
            # ── ZWD_DIAG ─────────────────────────────────────────────────────
            # Audit 1: ZWD state + covariance + Up/ZWD correlation coefficient.
            # The Up direction unit vector in ECEF for current position estimate.
            _rxyz_d = nom + x[:3]
            _lr_d, _lo_d, _ = _lla(_rxyz_d)
            _up_d = np.array([math.cos(_lr_d)*math.cos(_lo_d),
                              math.cos(_lr_d)*math.sin(_lo_d),
                              math.sin(_lr_d)])
            # Cov(Up_error, ZWD): Up_err ≈ up_d · [dx,dy,dz], ZWD = x[4]
            # Cov(Up_err, ZWD) = up_d @ P[0:3, 4]
            _cov_u_zwd = float(_up_d @ P[0:3, 4])
            _var_u     = float(_up_d @ P[0:3, 0:3] @ _up_d)
            _var_zwd   = float(P[4, 4])
            _denom_d   = math.sqrt(max(_var_u, 1e-20) * max(_var_zwd, 1e-20))
            _corr_u_zwd = _cov_u_zwd / _denom_d if _denom_d > 0 else 0.0
            _q_zwd_ep   = (_ZWD_Q_REDUCED if _zwd_decorr_active else _ZWD_Q_BASE) * dt   # actual Q this epoch
            # [ZWD_DIAG] -> zwd_hump_diag.csv; console silent
            # ── ION_DIAG ─────────────────────────────────────────────────────
            # Audit 2: per-sat ionosphere variance + process contribution.
            _ion_rows = []
            for _sid_io, _ki_io in sorted(sidx.items()):
                _pii   = float(P[_ki_io, _ki_io])
                _q_io  = float(Q[_ki_io, _ki_io]) if _ki_io < len(Q) else 0.0
                _ion_rows.append((_pii, _sid_io, _q_io))
            _ion_rows.sort(reverse=True)          # worst first
            _ion_str = "  ".join(
                f"{_s}:P={_p:.3f}m²/Q={_q:.2e}" for _p, _s, _q in _ion_rows[:6])
            # [ION_DIAG] -> ion_hump_diag.csv; console silent
            # ── NML_DIAG: GPS vs Galileo normal-matrix Up contribution ────────
            # Audit 4: decompose H^T R^-1 H into GPS and Galileo contributions
            # for the Up state.  Uses H_p (main measurement matrix) built this epoch.
            # Fix 1: also print per-constellation elevation-weight contribution
            #        and condition number of the full normal matrix.
            # Fix 2: run balanced block-scaling experiment and compare.
            try:
                _up_full = np.zeros(len(x)); _up_full[0:3] = _up_d
                _Rinv_diag = 1.0 / np.maximum(np.diag(R_main), 1e-30)
                _nm_u_gps = 0.0; _nm_u_gal = 0.0
                # Fix 1: per-constellation elevation-weighted Up contribution breakdown
                _el_w_gps = 0.0; _el_w_gal = 0.0  # sum of elevation weights for Up rows
                _rows_gps = 0;   _rows_gal = 0
                for _ri_nm in range(H_p.shape[0] - 1):  # skip ZWD prior row
                    _idx_nm = _ri_nm // 4
                    _sid_nm = geom[_idx_nm]['sid'] if _idx_nm < len(geom) else None
                    if _sid_nm is None: continue
                    _h_u = float(H_p[_ri_nm] @ _up_full)
                    _contrib = _Rinv_diag[_ri_nm] * _h_u**2
                    # elevation weight factor = sin²(el) as proxy for Up sensitivity
                    _el_nm = geom[_idx_nm].get('el', 0.0)
                    _el_factor = math.sin(math.radians(_el_nm))**2
                    if _sid_nm[0] == 'G':
                        _nm_u_gps += _contrib
                        _el_w_gps += _el_factor; _rows_gps += 1
                    elif _sid_nm[0] == 'E':
                        _nm_u_gal += _contrib
                        _el_w_gal += _el_factor; _rows_gal += 1
                _nm_u_tot = _nm_u_gps + _nm_u_gal + 1e-30
                # Fix 1: condition number of H^T R^-1 H (normal matrix for position+ZWD)
                _H_pos = H_p[:, :5]   # position (0:3), clock (3), ZWD (4)
                _NM_pos = (_H_pos * _Rinv_diag[:, np.newaxis]).T @ _H_pos
                try:
                    _sv_nm = np.linalg.svd(_NM_pos + np.eye(5)*1e-12, compute_uv=False)
                    _cond_nm = float(_sv_nm[0] / max(_sv_nm[-1], 1e-30))
                except Exception:
                    _cond_nm = float('nan')
                _el_w_gps_avg = _el_w_gps / max(_rows_gps, 1)
                _el_w_gal_avg = _el_w_gal / max(_rows_gal, 1)
                # [NML_DIAG] -> fusion_diag.csv; console silent
                # [NML_DIAG2] -> fusion_diag.csv; console silent
                # ── FUSION_DIAG: ISB observability + Galileo leverage summary ─
                try:
                    # ISB observable flag: was P_ISB reduced this epoch?
                    _fd_isb_obs_str = "YES" if (_isb_P_before - float(P[_ISB_E_IDX,_ISB_E_IDX]) > 1e-6
                                                and _n_gps_ep >= 2 and _n_gal_ep >= 1) else "NO"
                    # GPS-only Up sigma from NE block
                    _fd_H_gps_p = []; _fd_Ri_gps_p = []
                    _Rinv_fd_p = 1.0 / np.maximum(np.diag(R_main), 1e-30)
                    for _ri_fdp in range(H_p.shape[0] - 1):
                        _idx_fdp = _ri_fdp // 4
                        if _idx_fdp < len(geom) and geom[_idx_fdp]['sid'][0] == 'G':
                            _fd_H_gps_p.append(H_p[_ri_fdp, :5])
                            _fd_Ri_gps_p.append(_Rinv_fd_p[_ri_fdp])
                    _fd_sigma_gps_only = float('nan')
                    if len(_fd_H_gps_p) >= 4:
                        _Hg_p = np.array(_fd_H_gps_p); _Rg_p = np.array(_fd_Ri_gps_p)
                        _NM_gp = (_Hg_p * _Rg_p[:,None]).T @ _Hg_p + np.eye(5)*1e-10
                        _NM_gp_inv = np.linalg.inv(_NM_gp)
                        _rxyz_fp = nom + x[:3]; _lr_fp,_lo_fp,_ = _lla(_rxyz_fp)
                        _up_fp = np.array([math.cos(_lr_fp)*math.cos(_lo_fp),
                                           math.cos(_lr_fp)*math.sin(_lo_fp), math.sin(_lr_fp)])
                        _fd_sigma_gps_only = math.sqrt(max(float(_up_fp @ _NM_gp_inv[:3,:3] @ _up_fp), 0.0))*1e3
                    _var_u_fp = float(_up_d @ P[0:3,0:3] @ _up_d)
                    _sigma_u_comb_fp = math.sqrt(max(_var_u_fp, 0.0))*1e3
                    _lev_str = (f"{_fd_sigma_gps_only/_sigma_u_comb_fp:.3f}"
                                if not math.isnan(_fd_sigma_gps_only) and _sigma_u_comb_fp > 0
                                else "nan")
                    # [FUSION_DIAG] -> fusion_diag.csv; console silent
                except Exception as _fd_diag_e:
                    pass  # [FUSION_DIAG] skipped; console silent
                # Fix 2: Balanced block-scaling experiment
                # Scale GPS rows by (n_gal/n_gps) and Galileo rows by (n_gps/n_gal)
                # to force equal per-constellation Up contribution. Compute the
                # resulting Up split and condition number for comparison — no filter
                # state is modified; this is diagnostic only.
                if _n_gps_ep > 0 and _n_gal_ep > 0:
                    _scale_gps = math.sqrt(_n_gal_ep / _n_gps_ep)  # GPS rows scaled down
                    _scale_gal = math.sqrt(_n_gps_ep / _n_gal_ep)  # Gal rows scaled up
                    _Rinv_bal = _Rinv_diag.copy()
                    _bsl_nm_u_gps = 0.0; _bsl_nm_u_gal = 0.0
                    for _ri_b in range(H_p.shape[0] - 1):
                        _idx_b = _ri_b // 4
                        _sid_b = geom[_idx_b]['sid'] if _idx_b < len(geom) else None
                        if _sid_b is None: continue
                        _h_u_b = float(H_p[_ri_b] @ _up_full)
                        if _sid_b[0] == 'G':
                            _c_b = (_Rinv_bal[_ri_b] * _scale_gps**2) * _h_u_b**2
                            _bsl_nm_u_gps += _c_b
                        elif _sid_b[0] == 'E':
                            _c_b = (_Rinv_bal[_ri_b] * _scale_gal**2) * _h_u_b**2
                            _bsl_nm_u_gal += _c_b
                    _bsl_tot = _bsl_nm_u_gps + _bsl_nm_u_gal + 1e-30
                    # [NML_BALANCED] -> fusion_diag.csv; console silent
            except Exception as _e_nm:
                pass  # [NML_DIAG] skipped; console silent
            # ─────────────────────────────────────────────────────────────────
            # ── DIAG_MODE 300-epoch CSV logging ──────────────────────────────
            try:
                # Reuse variables already computed in this 300-epoch block
                _d300_pdop = _pdop(geom)
                _d300_rxyz = nom + x[:3]
                _d300_lr, _d300_lo, _ = _lla(_d300_rxyz)
                _d300_vdop = _vdop_ew(geom, _d300_lr, _d300_lo)
                _d300_up   = np.array([math.cos(_d300_lr)*math.cos(_d300_lo),
                                       math.cos(_d300_lr)*math.sin(_d300_lo),
                                       math.sin(_d300_lr)])
                _d300_var_u   = float(_d300_up @ P[0:3, 0:3] @ _d300_up)
                _d300_sig_u   = math.sqrt(max(_d300_var_u, 0.0)) * 1e3
                _d300_var_zwd = float(P[4, 4])
                _d300_cov_uzwd= float(_d300_up @ P[0:3, 4])
                _d300_denom   = math.sqrt(max(_d300_var_u,1e-20)*max(_d300_var_zwd,1e-20))
                _d300_corr    = _d300_cov_uzwd / _d300_denom if _d300_denom > 0 else 0.0
                _d300_in_hump = int(HUMP_SOD_LO <= sod <= HUMP_SOD_HI)
                _d300_iono_vars = [float(P[m2['ki'],m2['ki']]) for m2 in geom if m2['ki']<len(x)]
                _d300_mean_ivar = float(np.mean(_d300_iono_vars)) if _d300_iono_vars else 0.0
                _d300_max_ivar  = float(max(_d300_iono_vars)) if _d300_iono_vars else 0.0
                _d300_els = [math.degrees(m2['el']) for m2 in geom]
                _d300_mean_el = float(np.mean(_d300_els)) if _d300_els else 0.0
                _d300_ion_eff  = ION_PROC_NOISE   # effective value this epoch
                # GPS-only sigma_U for vdop_diag (reuse _prev_sig_u_gps_mm)
                _d300_sig_gps = _prev_sig_u_gps_mm
                # GPS/Gal Up fractions (reuse _nm_u_gps/_nm_u_gal if available)
                try:
                    _d300_gps_frac = _nm_u_gps / (_nm_u_gps + _nm_u_gal + 1e-30)
                    _d300_gal_frac = _nm_u_gal / (_nm_u_gps + _nm_u_gal + 1e-30)
                    _d300_cond     = _cond_nm
                    _d300_leverage = (_d300_sig_gps / max(_d300_sig_u, 0.1)
                                     if not math.isnan(_d300_sig_gps) else float('nan'))
                except Exception:
                    _d300_gps_frac = float('nan')
                    _d300_gal_frac = float('nan')
                    _d300_cond     = float('nan')
                    _d300_leverage = float('nan')
                _d300_qzwd = (_ZWD_Q_REDUCED if _zwd_decorr_active else _ZWD_Q_BASE)
                if HUMP_ZWD_TIGHTEN and HUMP_SOD_LO <= sod <= HUMP_SOD_HI:
                    _d300_qzwd = min(_d300_qzwd, HUMP_ZWD_Q_TIGHT)
                _d300_zwd_innov = float(ZWD_PRIOR - xs[4]) if 'xs' in dir() else 0.0

                _vdop_diag_w.writerow([
                    f"{sod:.1f}", f"{_d300_pdop:.3f}", f"{_d300_vdop:.3f}",
                    f"{_d300_sig_gps:.2f}" if not math.isnan(_d300_sig_gps) else "nan",
                    f"{_d300_sig_u:.2f}", len(geom), DIAG_MODE,
                ])
                _zwd_hump_w.writerow([
                    f"{sod:.1f}", _d300_in_hump,
                    f"{x[4]:.5f}", f"{_d300_zwd_innov:+.5f}", f"{_d300_corr:+.4f}",
                    f"{_d300_qzwd:.3e}", int(_zwd_decorr_active),
                    f"{_d300_sig_u:.2f}", DIAG_MODE,
                ])
                _ion_hump_w.writerow([
                    f"{sod:.1f}", _d300_in_hump,
                    f"{_d300_mean_ivar:.4f}", f"{_d300_max_ivar:.4f}", len(_d300_iono_vars),
                    f"{_d300_mean_el:.2f}", f"{_d300_ion_eff:.2e}",
                    DIAG_MODE,
                ])
                _fusion_obs_w.writerow([
                    f"{sod:.1f}",
                    f"{_d300_gps_frac:.4f}" if not math.isnan(_d300_gps_frac) else "nan",
                    f"{_d300_gal_frac:.4f}" if not math.isnan(_d300_gal_frac) else "nan",
                    f"{_d300_corr:+.4f}",
                    f"{_d300_mean_ivar:.4f}",
                    f"{_d300_leverage:.4f}" if not math.isnan(_d300_leverage) else "nan",
                    f"{_d300_cond:.2e}" if not math.isnan(_d300_cond) else "nan",
                    f"{_d300_sig_u:.2f}",
                    f"{_d300_sig_gps:.2f}" if not math.isnan(_d300_sig_gps) else "nan",
                    DIAG_MODE,
                ])
            except Exception as _d300_exc:
                pass  # never block filter on logging failure
            # ── end DIAG_MODE 300-epoch logging ──────────────────────────────
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
        _amb_snapshots[sod]={sid2:(x[ki2],P[ki2,ki2])
                             for sid2,ki2 in sidx.items() if phi.get(sid2,False)}
        if direction==1:
            if not hasattr(_rts_store,'_data'): _rts_store._data=[]
            _rts_store._data.append((sod,x.copy(),P.copy()))

        # v54 RAW residuals for logging
        code_res=[m['P1c']-(  _rp(m,x[3],x[4]) + x[m['ki']]  ) for m in geom]
        code_rms=math.sqrt(np.mean(np.array(code_res)**2))*1e3 if code_res else 0.
        phase_rms=phase_rms_now*1e3
        ZHD=zhd; ZWD=x[4]; TROPO=ZHD+ZWD

        results[sod]={'xyz':pos.copy(),'dx':dx.copy(),'p_trace':P[0,0]+P[1,1]+P[2,2],
                      'n':len(geom),'ztd':TROPO,'wl_fixed':len(wl_fixed),
                      'nl_fixed':n_nl,
                      'code_rms':code_rms,'phase_rms':phase_rms,
                      'zhd':ZHD,'zwd':ZWD,
                      'sats_used':sorted([m['sid'] for m in geom]),
                      'sats_wl':sorted([s for s in wl_fixed if any(m['sid']==s for m in geom)]),
                      'sats_nl':sorted([s for s in nl_fixed if any(m['sid']==s for m in geom)])}

        # PART 4 — summary.csv every epoch
        _summary_w.writerow([f"{sod:.1f}", len(geom), n_nl, len(wl_fixed),
                             f"{d3:.3f}", f"{code_rms:.2f}", f"{phase_rms:.3f}"])
        # v97 — isb.csv every epoch
        _isb_w.writerow([f"{sod:.1f}",
                         f"{x[_ISB_E_IDX]:+.6f}",
                         f"{P[_ISB_E_IDX, _ISB_E_IDX]:.6f}",
                         n_nl,
                         int(_isb_reinitialized)])

        # console progress: first 3 epochs only (start confirmation)
        if nproc <= 3:
            n_gps=sum(1 for m in geom if m['sid'][0]=='G')
            n_gal=sum(1 for m in geom if m['sid'][0]=='E')
            print(f"  [{label}] SOD={sod:6.0f}  N={len(geom):2d}(G{n_gps}+E{n_gal})"
                  f"  3D={d3:8.1f}mm  WL={len(wl_fixed)}  NL={n_nl}")

    print(f"[WL_DICT] {len(wl_fixed)} sats fixed")
    # v54: store (I, N1, N2, P_I, P_N1, P_N2) per satellite for RTS/inheritance
    fwd_amb={sid:(x[ki],x[ki+1],x[ki+2],P[ki,ki],P[ki+1,ki+1],P[ki+2,ki+2])
             for sid,ki in sidx.items() if phi.get(sid,False)}
    fwd_amb_out={sid:v for sid,v in fwd_amb.items() if sid in _amb_conv_sods}
    excluded={sid:f"pt={_amb_init_ptrace.get(sid,999):.3f}"
              for sid in fwd_amb if sid not in _amb_conv_sods}
    print(f"[AMB INHERIT] {len(fwd_amb_out)}/{len(fwd_amb)} sats "
          f"(excluded: {excluded})")
    if _bias_csv_fh and not _bias_csv_fh.closed:
        _bias_csv_fh.flush()
        _bias_csv_fh.close()
    print(f"[CSV]  nl_bias_debug.csv → {_bias_csv_path}")
    # Close structured CSV loggers
    for _fh in (_nl_debug_fh, _float_diag_fh, _nl_events_fh, _summary_fh, _isb_fh, _fusion_diag_fh, _hump_diag_fh,
                _vdop_diag_fh, _zwd_hump_fh, _ion_hump_fh, _fusion_obs_fh, _geom_hump_fh, _gzh_fh):
        if _fh and not _fh.closed:
            _fh.flush(); _fh.close()

    # ── TRANSITION AUDIT: change-point detection + overlay plot ──────────────
    # Runs only on the forward pass (direction==1) to avoid double work.
    if _DIAG_DEEP and direction == 1:
        try:
            import csv as _csv2
            import numpy as _np2

            # ── 1. Flush + close transition files ─────────────────────────────
            if _ta_fh and not _ta_fh.closed:
                _ta_fh.flush();  _ta_fh.close()
            if _te_fh and not _te_fh.closed:
                _te_fh.flush();  _te_fh.close()
            # flush remaining state_diag / event_diag buffers and close
            try:
                for _row in _sd_buf: _sd_w.writerow(_row)
                _sd_buf.clear(); _sd_fh.flush(); _sd_fh.close()
            except Exception: pass
            try:
                for _row in _ed_buf: _ed_w.writerow(_row)
                _ed_buf.clear(); _ed_fh.flush(); _ed_fh.close()
            except Exception: pass

            # ── 2. Load transition_audit for change-point detection ────────────
            _ta_path = os.path.join(_logs_dir, "transition_audit.csv")
            _te_path = os.path.join(_logs_dir, "transition_events.csv")

            _ta_sod, _ta_du, _ta_de, _ta_dn = [], [], [], []
            _ta_gps_frac, _ta_scale, _ta_nlc, _ta_ngal, _ta_ngps = [], [], [], [], []
            _ta_corr, _ta_isb, _ta_sigU = [], [], []
            _ppp_path = os.path.join(os.path.dirname(_logs_dir), "ppp_results3.csv") \
                        if os.path.isfile(os.path.join(os.path.dirname(_logs_dir), "ppp_results3.csv")) \
                        else None
            _ppp_by_sod = {}
            if _ppp_path and os.path.isfile(_ppp_path):
                with open(_ppp_path, newline="") as _pf:
                    for _pr in _csv2.DictReader(_pf):
                        try:
                            _ppp_by_sod[float(_pr["SOD"])] = (
                                float(_pr.get("dE_mm", "nan")),
                                float(_pr.get("dN_mm", "nan")),
                                float(_pr.get("dU_mm", "nan")),
                            )
                        except Exception:
                            pass

            with open(_ta_path, newline="") as _tf:
                for _tr in _csv2.DictReader(_tf):
                    try:
                        _s = float(_tr["SOD"])
                        _ta_sod.append(_s)
                        _ta_gps_frac.append(float(_tr.get("GPS_NE_frac", "nan") or "nan"))
                        _ta_scale.append(float(_tr.get("gal_sig_scale", "nan") or "nan"))
                        _ta_nlc.append(float(_tr.get("NL_count", "nan") or "nan"))
                        _ta_ngal.append(float(_tr.get("nGal", "nan") or "nan"))
                        _ta_ngps.append(float(_tr.get("nGPS", "nan") or "nan"))
                        _ta_corr.append(float(_tr.get("corr_U_ZWD", "nan") or "nan"))
                        _ta_isb.append(float(_tr.get("ISB_m", "nan") or "nan"))
                        _ta_sigU.append(float(_tr.get("sigma_U_comb_mm", "nan") or "nan"))
                        _e, _n, _u = _ppp_by_sod.get(_s, (float('nan'),)*3)
                        _ta_de.append(_e); _ta_dn.append(_n); _ta_du.append(_u)
                    except Exception:
                        pass

            _ta_sod  = _np2.array(_ta_sod,  dtype=float)
            _ta_du   = _np2.array(_ta_du,   dtype=float)
            _ta_de   = _np2.array(_ta_de,   dtype=float)
            _ta_dn   = _np2.array(_ta_dn,   dtype=float)
            _ta_gf   = _np2.array(_ta_gps_frac, dtype=float)
            _ta_sc   = _np2.array(_ta_scale, dtype=float)
            _ta_nlca = _np2.array(_ta_nlc,   dtype=float)
            _ta_ngla = _np2.array(_ta_ngal,  dtype=float)
            _ta_ngpa = _np2.array(_ta_ngps,  dtype=float)
            _ta_cr   = _np2.array(_ta_corr,  dtype=float)
            _ta_isba = _np2.array(_ta_isb,   dtype=float)
            _ta_sga  = _np2.array(_ta_sigU,  dtype=float)

            # ── 3. Change-point detection on dU (CUSUM on finite-diff slope) ──
            _cp_sod = float('nan')
            _valid  = _np2.isfinite(_ta_du) & (_ta_sod >= 0)
            if _valid.sum() > 60:
                _s_v  = _ta_sod[_valid] / 3600.0   # hours
                _u_v  = _ta_du[_valid]
                # Sliding-window slope: 30-min windows, compare slope change
                _W = 60   # epochs = 30 min at 30 s
                _slopes = []
                for _i in range(_W, len(_u_v) - _W):
                    _sl1 = _np2.polyfit(_s_v[_i-_W:_i],   _u_v[_i-_W:_i],   1)[0]
                    _sl2 = _np2.polyfit(_s_v[_i:_i+_W],   _u_v[_i:_i+_W],   1)[0]
                    _slopes.append(abs(_sl2 - _sl1))
                if _slopes:
                    _cp_idx = int(_np2.argmax(_slopes)) + _W
                    _cp_sod = float(_ta_sod[_valid][_cp_idx])

            print(f"[TRANSITION_AUDIT] Change-point dU: SOD={_cp_sod:.0f}"
                  f"  ({_cp_sod/3600:.2f} h)")

            # ── 4. Load transition events ──────────────────────────────────────
            _evts = []
            if os.path.isfile(_te_path):
                with open(_te_path, newline="") as _ef:
                    for _er in _csv2.DictReader(_ef):
                        try:
                            _evts.append((_er["SOD"], _er["event_type"],
                                          _er["sat"], _er["value"], _er["detail"]))
                        except Exception:
                            pass

            # ── 5. Write transition_events summary to console ──────────────────
            _AUDIT_WIN_LO = max(0.0, _cp_sod - 5400) if _np2.isfinite(_cp_sod) else 14400.0
            _AUDIT_WIN_HI = _cp_sod + 5400           if _np2.isfinite(_cp_sod) else 27000.0
            print(f"[TRANSITION_AUDIT] Audit window: SOD {_AUDIT_WIN_LO:.0f}–{_AUDIT_WIN_HI:.0f}"
                  f"  ({_AUDIT_WIN_LO/3600:.1f}–{_AUDIT_WIN_HI/3600:.1f} h)")
            for _ev in _evts:
                _ev_sod = float(_ev[0])
                if _AUDIT_WIN_LO <= _ev_sod <= _AUDIT_WIN_HI:
                    print(f"  [TE] SOD={_ev_sod:.0f} ({_ev_sod/3600:.2f}h)"
                          f"  {_ev[1]:<18s}  sat={_ev[2]:<4s}"
                          f"  val={_ev[3]}  {_ev[4]}")

            # ── 6. Overlay plot ────────────────────────────────────────────────
            try:
                import matplotlib; matplotlib.use("Agg")
                import matplotlib.pyplot as _plt
                import matplotlib.patches as _mpt

                _EVT_COLORS = {
                    "NL_FIX":         "#2ca02c",
                    "WL_FIX":         "#17becf",
                    "SLIP_RESET":     "#d62728",
                    "DIVERGE_UNDO":   "#ff7f0e",
                    "SCALE_UPDATE":   "#9467bd",
                    "ZWD_DECORR_ON":  "#8c564b",
                    "ZWD_DECORR_OFF": "#e377c2",
                    "NL_BIAS_UNFREEZE":"#bcbd22",
                    "NML_BAL":        "#7f7f7f",
                }

                _fig, _axes = _plt.subplots(5, 1, figsize=(16, 18), sharex=True)
                _fig.suptitle(
                    f"Transition Audit — {label}  |  Change-point dU: SOD={_cp_sod:.0f} ({_cp_sod/3600:.2f} h)",
                    fontsize=12, fontweight="bold"
                )
                _hr = _ta_sod / 3600.0

                # Panel 0: ENU
                _ax = _axes[0]
                _mk = _np2.isfinite(_ta_de)
                _ax.plot(_hr[_mk], _ta_de[_mk], color="#1f77b4", lw=0.8, label="dE")
                _ax.plot(_hr[_mk], _ta_dn[_mk], color="#2ca02c", lw=0.8, label="dN")
                _ax.plot(_hr[_mk], _ta_du[_mk], color="#d62728", lw=1.2, label="dU")
                _ax.set_ylabel("ENU error (mm)"); _ax.legend(fontsize=8, loc="upper right")
                _ax.axhline(0, color="k", lw=0.4, ls="--")
                _ax.grid(True, alpha=0.3)

                # Panel 1: GPS_NE_frac + gal_sig_scale
                _ax = _axes[1]
                _mk2 = _np2.isfinite(_ta_gf)
                _ax.plot(_hr[_mk2], _ta_gf[_mk2]*100, color="#1f77b4", lw=0.9, label="GPS Up% (NE)")
                _ax2b = _ax.twinx()
                _mk3 = _np2.isfinite(_ta_sc)
                _ax2b.plot(_hr[_mk3], _ta_sc[_mk3], color="#9467bd", lw=0.9, ls="--", label="gal_scale")
                _ax.set_ylabel("GPS Up frac (%)"); _ax2b.set_ylabel("gal_sig_scale", color="#9467bd")
                _ax.grid(True, alpha=0.3)
                _lines1, _lbl1 = _ax.get_legend_handles_labels()
                _lines2, _lbl2 = _ax2b.get_legend_handles_labels()
                _ax.legend(_lines1+_lines2, _lbl1+_lbl2, fontsize=8, loc="upper right")

                # Panel 2: nGPS, nGal, NL_count
                _ax = _axes[2]
                _mk4 = _np2.isfinite(_ta_ngpa)
                _ax.plot(_hr[_mk4], _ta_ngpa[_mk4], color="#1f77b4", lw=0.8, label="nGPS")
                _ax.plot(_hr[_mk4], _ta_ngla[_mk4], color="#ff7f0e", lw=0.8, label="nGal")
                _ax.plot(_hr[_mk4], _ta_nlca[_mk4], color="#2ca02c", lw=1.0, ls="--", label="NL_count")
                _ax.set_ylabel("Sat counts"); _ax.legend(fontsize=8, loc="upper right")
                _ax.grid(True, alpha=0.3)

                # Panel 3: corr(U,ZWD) + sigma_U
                _ax = _axes[3]
                _mk5 = _np2.isfinite(_ta_cr)
                _ax.plot(_hr[_mk5], _ta_cr[_mk5], color="#8c564b", lw=0.8, label="corr(U,ZWD)")
                _ax.axhline(0.35, color="#8c564b", lw=0.6, ls=":", alpha=0.7, label="decorr thresh")
                _ax.axhline(-0.35, color="#8c564b", lw=0.6, ls=":", alpha=0.7)
                _ax3b = _ax.twinx()
                _mk6 = _np2.isfinite(_ta_sga)
                _ax3b.plot(_hr[_mk6], _ta_sga[_mk6], color="#17becf", lw=0.8, ls="--", label="σ_U (mm)")
                _ax.set_ylabel("corr(U,ZWD)"); _ax3b.set_ylabel("σ_U comb (mm)", color="#17becf")
                _ax.grid(True, alpha=0.3)
                _lines3, _lbl3 = _ax.get_legend_handles_labels()
                _lines4, _lbl4 = _ax3b.get_legend_handles_labels()
                _ax.legend(_lines3+_lines4, _lbl3+_lbl4, fontsize=8, loc="upper right")

                # Panel 4: ISB
                _ax = _axes[4]
                _mk7 = _np2.isfinite(_ta_isba)
                _ax.plot(_hr[_mk7], _ta_isba[_mk7], color="#1f77b4", lw=0.9, label="ISB (m)")
                _ax.set_ylabel("ISB (m)"); _ax.set_xlabel("Time (h)")
                _ax.legend(fontsize=8, loc="upper right"); _ax.grid(True, alpha=0.3)

                # ── Event markers on all panels ────────────────────────────────
                _legend_added = set()
                for _ev in _evts:
                    _ev_sod2 = float(_ev[0])
                    _ev_hr   = _ev_sod2 / 3600.0
                    _ev_type = _ev[1]
                    _ev_col  = _EVT_COLORS.get(_ev_type, "#aaaaaa")
                    for _axi in _axes:
                        _axi.axvline(_ev_hr, color=_ev_col, lw=0.7, alpha=0.55, ls="--")
                    if _ev_type not in _legend_added:
                        _axes[0].axvline(_ev_hr, color=_ev_col, lw=0.7, alpha=0.55,
                                          ls="--", label=_ev_type)
                        _legend_added.add(_ev_type)

                # ── Change-point vertical marker ───────────────────────────────
                if _np2.isfinite(_cp_sod):
                    _cp_hr = _cp_sod / 3600.0
                    for _axi in _axes:
                        _axi.axvline(_cp_hr, color="black", lw=2.0, alpha=0.85,
                                      ls="-", zorder=10)
                    _axes[0].text(_cp_hr + 0.05, _axes[0].get_ylim()[1]*0.9,
                                  f"CP {_cp_hr:.2f}h", fontsize=8, color="black",
                                  fontweight="bold")

                # ── Audit window shading ───────────────────────────────────────
                for _axi in _axes:
                    _axi.axvspan(_AUDIT_WIN_LO/3600, _AUDIT_WIN_HI/3600,
                                  alpha=0.06, color="yellow")

                _axes[0].legend(fontsize=7, loc="upper right", ncol=3)
                _plt.tight_layout()
                _plot_path = os.path.join(_logs_dir, "transition_audit.png")
                _fig.savefig(_plot_path, dpi=150, bbox_inches="tight")
                _plt.close(_fig)
                print(f"[TRANSITION_AUDIT] Plot saved → {_plot_path}")

            except Exception as _plot_exc:
                print(f"[TRANSITION_AUDIT] Plot failed: {_plot_exc}")

        except Exception as _ta_exc:
            print(f"[TRANSITION_AUDIT] Post-pass analysis failed: {_ta_exc}")
        finally:
            # Safety close if not already closed
            try: _ta_fh.close()
            except Exception: pass
            try: _te_fh.close()
            except Exception: pass
            try: _sd_fh.close()
            except Exception: pass
            try: _ed_fh.close()
            except Exception: pass
    else:
        # Backward pass: just flush/close the TA files
        if _ta_fh and not _ta_fh.closed:
            _ta_fh.flush(); _ta_fh.close()
        if _te_fh and not _te_fh.closed:
            _te_fh.flush(); _te_fh.close()
        try:
            for _row in _sd_buf: _sd_w.writerow(_row)
            _sd_fh.flush(); _sd_fh.close()
        except Exception: pass
        try:
            for _row in _ed_buf: _ed_w.writerow(_row)
            _ed_fh.flush(); _ed_fh.close()
        except Exception: pass
    # ── end TRANSITION AUDIT ──────────────────────────────────────────────────

    print(f"[CSV]  logs/ → state_diag.csv (60ep)  event_diag.csv (events)")
    print(f"[CSV]  logs/ → nl_events.csv  summary.csv  isb.csv  fusion_diag.csv  hump_diag.csv")
    print(f"[CSV]  logs/ → vdop_diag.csv  zwd_hump_diag.csv  ion_hump_diag.csv  fusion_obs_diag.csv  geom_hump_diag.csv  geom_zwd_hump_diag.csv  (DIAG_MODE={DIAG_MODE})")

    # FIX 5: end-of-pass Pearson correlation between VDOP_ew peaks and 3D error hump
    try:
        if len(_gzh_vdop_hist) >= 10:
            _gzh_arr    = np.array(_gzh_vdop_hist)    # (N,3): vdop, d3_mm, in_hump
            _gzh_vdops  = _gzh_arr[:, 0]
            _gzh_d3s    = _gzh_arr[:, 1]
            _gzh_corr_full = float(np.corrcoef(_gzh_vdops, _gzh_d3s)[0, 1])
            _gzh_mask_hump = _gzh_arr[:, 2].astype(bool)
            _gzh_in_v   = _gzh_vdops[_gzh_mask_hump]
            _gzh_in_d   = _gzh_d3s[_gzh_mask_hump]
            _gzh_corr_hump = (float(np.corrcoef(_gzh_in_v, _gzh_in_d)[0, 1])
                              if len(_gzh_in_v) >= 5 else float('nan'))
            print(f"[VDOP_HUMP_CORR] full-pass corr(VDOP_ew, 3D_error)={_gzh_corr_full:+.4f}  "
                  f"in-hump (6-19h) corr={_gzh_corr_hump:+.4f}  n_hump_epochs={int(_gzh_mask_hump.sum())}")
    except Exception:
        pass
    print(f"[CSV]  logs/ → transition_audit.csv  transition_events.csv  transition_audit.png")
    print(f"[CSV]  logs/ → amb_audit.csv  (ambiguity influence audit — diagnostic only)")
    print(f"[CSV]  logs/ → sat_churn_diag.csv  (NL lifetime monitor — FIX 1/2/3/4/5)")
    try:
        _aa_fh.flush(); _aa_fh.close()
    except Exception:
        pass
    try:
        _churn_fh.flush(); _churn_fh.close()
    except Exception:
        pass
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

    print("="*72)
    print("GPS+Galileo PPP v60 — Per-Satellite Fractional Bias | Consistency Gate | Metres sigma_N1")
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
        if sta_name in blq:
            print(f"[OTL] corrections applied each epoch")
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

    mode_labels=[('G','GPS-only'),('E','Galileo-only'),('GE','GPS+Galileo')]
    all_fwd={}; all_rts={}; all_meta={}

    for const,label in mode_labels:
        print(f"\n{'='*72}")
        print(f"[MODE] {label}  (constellation='{const}')")
        _rts_store._data=[]
        fwd,ex,ec,ez,wl_f,fwd_amb,fwd_snap=_ppp_pass(
            epochs,nom=APX.copy(),iclk=0.,izwd=0.20,
            direction=1,label="FWD",constellation=const,**_common)
        print(f"  {len(fwd)} epochs  end_3D={np.linalg.norm(ex-REF)*1e3:.1f}mm  ZWD={ez:.3f}m")
        print(f"  WL fixed: {list(wl_f.keys())}  ({len(wl_f)} sats)")

        print(f"[SMOOTH] Running RTS smoother on {len(_rts_store._data)} epochs ...")
        if DIAG_LIGHT and not DIAG_DEEP:
            rts = fwd   # skip RTS in LIGHT mode (debug-only pass)
            print(f"[SMOOTH] Skipped (DIAG_LIGHT=True) — using FWD as RTS proxy")
        else:
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

    primary_fwd=all_fwd['GPS+Galileo']
    primary_rts=all_rts['GPS+Galileo']
    rl=[(s,{**r,'pass':'FWD'}) for s,r in sorted(primary_fwd.items())]

    print(f"\n  Wall: {_time.time()-t0:.1f}s")
    print("="*72)

    # ── DIAG_MODE hump amplitude summary ─────────────────────────────────────
    # Compute mean |Up| error during hump window (8-18 h) and full day
    # for the GPS+Galileo FWD solution.  Gives a single scalar comparison.
    try:
        _diag_mode_names = {0: "MODE 0 — Baseline diagnostics only",
                            1: "MODE 1 — Adaptive GPS Rd downweighting",
                            2: "MODE 2 — ZWD RW tightening during hump",
                            3: "MODE 3 — Low-elevation iono boost (3×)"}
        _hump_lo, _hump_hi = 28800.0, 64800.0
        _lr_s, _lo_s, _ = _lla(REF); _Re_s = _enu(_lr_s, _lo_s)
        _u_hump = []; _u_full = []
        for _sh, _rh in sorted(primary_fwd.items()):
            _enu_h = _Re_s @ _rh['dx'] * 1e3
            _u_full.append(abs(_enu_h[2]))
            if _hump_lo <= _sh <= _hump_hi:
                _u_hump.append(abs(_enu_h[2]))
        _mean_u_hump = float(np.mean(_u_hump)) if _u_hump else float('nan')
        _mean_u_full = float(np.mean(_u_full)) if _u_full else float('nan')
        _rms_u_hump  = math.sqrt(float(np.mean(np.array(_u_hump)**2))) if _u_hump else float('nan')
        _rms_u_full  = math.sqrt(float(np.mean(np.array(_u_full)**2))) if _u_full else float('nan')
        print("\n" + "─"*72)
        # DIAG_MODE is local to _ppp_pass; read from the last GE pass logs if available
        _active_dm = 0  # default — reflects MODE 0 baseline run
        print(f"  [DIAG_SUMMARY] {_diag_mode_names.get(_active_dm, 'Unknown mode')}")
        print(f"  Hump window (8–18 h)  mean|Up|={_mean_u_hump:.1f} mm  RMS_Up={_rms_u_hump:.1f} mm  ({len(_u_hump)} epochs)")
        print(f"  Full day              mean|Up|={_mean_u_full:.1f} mm  RMS_Up={_rms_u_full:.1f} mm  ({len(_u_full)} epochs)")
        print(f"  Hump ratio (hump/full): {(_rms_u_hump/_rms_u_full):.3f}"
              f"  (>1 = hump dominates Up error)")
        print("  ─ Experiment guide ─────────────────────────────────────────────")
        print("  Re-run with DIAG_MODE=1 → GPS Rd adaptive inflation")
        print("  Re-run with DIAG_MODE=2 → ZWD process noise tightened 8-18 h")
        print("  Re-run with DIAG_MODE=3 → 3× iono noise for low-el (<20°) sats")
        print("  Compare hump_ratio across runs to identify dominant mechanism.")
        print("─"*72)
    except Exception as _ds_exc:
        print(f"[DIAG_SUMMARY] skipped ({_ds_exc})")
    # ─────────────────────────────────────────────────────────────────────────

    # v108: per-hump 3D RMS diagnostics (hump1=7–10 h, hump2=14–18 h)
    try:
        _h1_rms = math.sqrt(float(np.mean(_hump1_d3_sq))) if _hump1_d3_sq else float('nan')
        _h2_rms = math.sqrt(float(np.mean(_hump2_d3_sq))) if _hump2_d3_sq else float('nan')
        print(f"[HUMP_RMS] hump1 (7–10 h) 3D_RMS={_h1_rms:.1f} mm  n={len(_hump1_d3_sq)}")
        print(f"[HUMP_RMS] hump2 (14–18 h) 3D_RMS={_h2_rms:.1f} mm  n={len(_hump2_d3_sq)}")
    except Exception:
        pass
    # ─────────────────────────────────────────────────────────────────────────

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

    # ══════════════════════════════════════════════════════════════════════════
    # MODEL-ERROR ISOLATION EXPERIMENTS  (diagnosis only — no tuning)
    # Evidence: GPS-only, Galileo-only, GPS+Galileo all share the same two-hump
    # structure → hump is common-mode model error, NOT ambiguity logic.
    # ══════════════════════════════════════════════════════════════════════════
    def _up_series(fwd):
        """Extract (hour, u_mm) from a fwd result dict."""
        lr, lo, _ = _lla(REF); Re = _enu(lr, lo)
        pts = []
        for sod, r in sorted(fwd.items()):
            enu = Re @ r['dx'] * 1e3
            pts.append((sod / 3600., float(enu[2])))
        return pts

    def _hump_stats(pts, lo_h=8., hi_h=18.):
        """Return (mean_abs_hump, mean_abs_full, hump_ratio)."""
        import numpy as _np2
        hump = [abs(u) for h, u in pts if lo_h <= h <= hi_h]
        full  = [abs(u) for _, u in pts]
        mh = float(_np2.mean(hump)) if hump else float('nan')
        mf = float(_np2.mean(full))  if full  else float('nan')
        rat = mh / mf if (mf > 0 and not math.isnan(mh)) else float('nan')
        return mh, mf, rat

    _exp_results = {}
    _exp_results['Baseline'] = _up_series(primary_fwd)
    _mh_base, _mf_base, _rat_base = _hump_stats(_exp_results['Baseline'])
    print(f"\n[EXP_BASE] Baseline hump(8-18h) mean|Up|={_mh_base:.1f} mm  "
          f"full={_mf_base:.1f} mm  ratio={_rat_base:.3f}")

    # ── Experiment A: float-only PPP (no NL fixing) ───────────────────────────
    print("\n" + "─"*72)
    print("[EXP_A] Float-only PPP — GPS+Galileo  (force_float=True)")
    _rts_store._data = []
    _fwd_A, *_ = _ppp_pass(
        epochs, nom=APX.copy(), iclk=0., izwd=0.20,
        direction=1, label="EXP_A_FLOAT", constellation='GE',
        force_float=True, **_common)
    _exp_results['A_FloatPPP'] = _up_series(_fwd_A)
    _mh_A, _mf_A, _rat_A = _hump_stats(_exp_results['A_FloatPPP'])
    print(f"[EXP_A] hump mean|Up|={_mh_A:.1f} mm  full={_mf_A:.1f} mm  ratio={_rat_A:.3f}")
    _hump_survives_A = (not math.isnan(_rat_A)) and (_rat_A > 1.05)
    print(f"[EXP_A] hump_survives_float_PPP={'YES → ambiguity NOT root cause' if _hump_survives_A else 'NO → ambiguity may be contributing'}")

    # ── Experiment B: ZWD process-noise sensitivity sweep ─────────────────────
    print("\n" + "─"*72)
    print("[EXP_B] ZWD Q sweep — GPS+Galileo")
    for _zs, _zlbl in [(0.1, 'B_ZWD_x0.1'), (10.0, 'B_ZWD_x10')]:
        _rts_store._data = []
        _fwd_B, *_ = _ppp_pass(
            epochs, nom=APX.copy(), iclk=0., izwd=0.20,
            direction=1, label=f"EXP_B_{_zs}", constellation='GE',
            zwd_q_scale=_zs, **_common)
        _exp_results[_zlbl] = _up_series(_fwd_B)
        _mh_B, _mf_B, _rat_B = _hump_stats(_exp_results[_zlbl])
        print(f"[EXP_B] zwd_q_scale={_zs:<5}  hump mean|Up|={_mh_B:.1f} mm  "
              f"full={_mf_B:.1f} mm  ratio={_rat_B:.3f}  "
              f"delta_vs_baseline={_mh_B - _mh_base:+.1f} mm")
    _ratio_b01 = _hump_stats(_exp_results['B_ZWD_x0.1'])[2]
    _ratio_b10 = _hump_stats(_exp_results['B_ZWD_x10'])[2]
    _zwd_coupled = (not math.isnan(_ratio_b01)) and (not math.isnan(_ratio_b10)) and \
                   (abs(_ratio_b01 - _ratio_b10) > 0.10)
    print(f"[EXP_B] ZWD_coupling={'YES → troposphere implicated in hump' if _zwd_coupled else 'WEAK → ZWD not primary driver'}")

    # ── Experiment C: 12h semi-diurnal harmonic fit on baseline Up residuals ──
    print("\n" + "─"*72)
    print("[EXP_C] Semi-diurnal harmonic fit  U(t)=a·sin(2πt/12h)+b·cos(2πt/12h)+dc")
    _pts_C = _exp_results['Baseline']
    if len(_pts_C) >= 30:
        _t_h = np.array([p[0] for p in _pts_C])
        _u_m = np.array([p[1] for p in _pts_C])
        _omega = 2. * math.pi / 12.        # rad / hour  (12-h period)
        _A_mat = np.column_stack([np.sin(_omega * _t_h),
                                   np.cos(_omega * _t_h),
                                   np.ones(len(_t_h))])
        try:
            _coef_C, _, _, _ = np.linalg.lstsq(_A_mat, _u_m, rcond=None)
            _a_C, _b_C, _dc_C = _coef_C
            _amp_C   = math.sqrt(_a_C**2 + _b_C**2)
            _phase_C = math.atan2(_b_C, _a_C) / _omega   # hour of first peak
            _phase_C = _phase_C % 12.                     # wrap to [0,12)
            _u_fit_C = _A_mat @ _coef_C
            _ss_res_C = float(np.sum((_u_m - _u_fit_C)**2))
            _ss_tot_C = float(np.sum((_u_m - _u_m.mean())**2))
            _r2_C = 1. - _ss_res_C / _ss_tot_C if _ss_tot_C > 0 else float('nan')
            print(f"[EXP_C] amplitude={_amp_C:.1f} mm  phase_peak={_phase_C:.2f} h  DC_offset={_dc_C:+.1f} mm")
            print(f"[EXP_C] R²(12h)={_r2_C:.4f}  "
                  f"({'strong semi-diurnal signature' if _r2_C > 0.35 else 'moderate' if _r2_C > 0.15 else 'weak'} — "
                  f"{'consistent with semi-diurnal loading (K2/S2)' if _r2_C > 0.35 else 'hump not well-explained by pure 12h harmonic'})")
            # Phase check: semi-diurnal ocean/solid loading at IISC peaks roughly 6–10 h local
            _phase_match = (4. <= _phase_C <= 12.) or (_phase_C <= 2.)
            print(f"[EXP_C] phase_match_loading={'YES (peak {_phase_C:.1f}h ~ expected 6-10h local)' if _phase_match else f'NO (peak {_phase_C:.1f}h — not typical loading signature)'}")
        except Exception as _ce:
            print(f"[EXP_C] Harmonic fit failed: {_ce}")
    else:
        print("[EXP_C] Insufficient epochs for harmonic fit")

    # ── Experiment D: OTL (ocean/solid loading) toggle ────────────────────────
    print("\n" + "─"*72)
    print("[EXP_D] OTL disabled (blq={}) — GPS+Galileo")
    _common_no_otl = {k: ({} if k == 'blq' else v) for k, v in _common.items()}
    _rts_store._data = []
    _fwd_D, *_ = _ppp_pass(
        epochs, nom=APX.copy(), iclk=0., izwd=0.20,
        direction=1, label="EXP_D_NOOTL", constellation='GE',
        **_common_no_otl)
    _exp_results['D_NoOTL'] = _up_series(_fwd_D)
    _mh_D, _mf_D, _rat_D = _hump_stats(_exp_results['D_NoOTL'])
    _delta_D = _mh_D - _mh_base
    print(f"[EXP_D] no-OTL hump mean|Up|={_mh_D:.1f} mm  baseline={_mh_base:.1f} mm  "
          f"delta={_delta_D:+.1f} mm")
    if abs(_delta_D) < 5.:
        print("[EXP_D] OTL_verdict: NEUTRAL — loading correction not driving hump")
    elif _delta_D > 5.:
        print("[EXP_D] OTL_verdict: REMOVING_OTL_WORSENS — OTL was partially mitigating hump")
    else:
        print("[EXP_D] OTL_verdict: REMOVING_OTL_IMPROVES — possible OTL overcorrection at this site")

    # ── Summary diagnostic report ─────────────────────────────────────────────
    print("\n" + "═"*72)
    print("  MODEL-ERROR ISOLATION SUMMARY")
    print("═"*72)
    print(f"  Baseline         hump_ratio={_rat_base:.3f}  mean|Up|_hump={_mh_base:.1f} mm")
    print(f"  Exp A FloatPPP   hump_ratio={_rat_A:.3f}  {'HUMP SURVIVES → AR not root cause' if _hump_survives_A else 'hump reduced → AR contributes'}")
    _mh_b01,_,_rb01 = _hump_stats(_exp_results.get('B_ZWD_x0.1',[]))
    _mh_b10,_,_rb10 = _hump_stats(_exp_results.get('B_ZWD_x10',[]))
    print(f"  Exp B ZWD×0.1    hump_ratio={_rb01:.3f}  mean|Up|_hump={_mh_b01:.1f} mm")
    print(f"  Exp B ZWD×10     hump_ratio={_rb10:.3f}  mean|Up|_hump={_mh_b10:.1f} mm")
    print(f"  Exp D NoOTL      hump_ratio={_rat_D:.3f}  delta={_delta_D:+.1f} mm  {_delta_D:+.1f}")
    print("═"*72)

    if DIAG_DEEP:
        _plot_model_error_experiments(_exp_results, REF)
    # ── end model-error isolation experiments ─────────────────────────────────

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

def _plot_model_error_experiments(exp_results, REF):
    """Overlay Up-error time-series for model-error isolation experiments A/B/C/D.

    exp_results: dict  label → list of (hour, u_mm)
    """
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("[EXP_PLOT] matplotlib not available"); return

    _COLORS = {
        'Baseline':    '#1f77b4',
        'A_FloatPPP':  '#d62728',
        'B_ZWD_x0.1':  '#2ca02c',
        'B_ZWD_x10':   '#ff7f0e',
        'D_NoOTL':     '#9467bd',
    }
    _STYLES = {
        'Baseline':    '-',
        'A_FloatPPP':  '--',
        'B_ZWD_x0.1':  '-.',
        'B_ZWD_x10':   ':',
        'D_NoOTL':     (0,(3,1,1,1)),
    }
    _LABELS = {
        'Baseline':    'Baseline (AR on)',
        'A_FloatPPP':  'Exp A: Float-only (AR off)',
        'B_ZWD_x0.1':  'Exp B: ZWD Q × 0.1',
        'B_ZWD_x10':   'Exp B: ZWD Q × 10',
        'D_NoOTL':     'Exp D: OTL disabled',
    }

    fig, axes = plt.subplots(2, 1, figsize=(14, 9))
    fig.suptitle('Model-Error Isolation Experiments A/B/D  —  Up residual overlay\n'
                 '(Exp C: 12h harmonic fit — see console output)',
                 fontsize=12, fontweight='bold')

    # Panel 0: Up error
    ax = axes[0]
    for lbl, pts in exp_results.items():
        if not pts: continue
        hrs = [p[0] for p in pts]
        ups = [p[1] for p in pts]
        ax.plot(hrs, ups,
                color=_COLORS.get(lbl, 'gray'),
                linestyle=_STYLES.get(lbl, '-'),
                linewidth=0.9, alpha=0.85,
                label=_LABELS.get(lbl, lbl))
    ax.axhline(0, color='black', lw=0.4)
    ax.axvspan(8., 18., alpha=0.07, color='red', label='hump window 8–18 h')
    ax.set_ylabel('Up error (mm)'); ax.set_xlabel('Time (h)')
    ax.set_title('(a) Up residual — all experiments')
    ax.set_ylim(-300, 300); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    # Panel 1: |Up| error (hump amplitude)
    ax = axes[1]
    for lbl, pts in exp_results.items():
        if not pts: continue
        hrs = [p[0] for p in pts]
        abs_ups = [abs(p[1]) for p in pts]
        ax.plot(hrs, abs_ups,
                color=_COLORS.get(lbl, 'gray'),
                linestyle=_STYLES.get(lbl, '-'),
                linewidth=0.9, alpha=0.85,
                label=_LABELS.get(lbl, lbl))
    ax.axvspan(8., 18., alpha=0.07, color='red', label='hump window')
    ax.axhline(100, color='gray', lw=0.6, ls='--', label='10 cm')
    ax.set_ylabel('|Up error| (mm)'); ax.set_xlabel('Time (h)')
    ax.set_title('(b) |Up| amplitude — hump isolation')
    ax.set_ylim(0, 300); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    plt.tight_layout()
    _plot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'model_error_experiments.png')
    try:
        fig.savefig(_plot_path, dpi=150, bbox_inches='tight')
        print(f"[EXP_PLOT] Saved: {_plot_path}")
    except Exception as e:
        print(f"[EXP_PLOT] Could not save: {e}")
    plt.close(fig)


def _plot_comparison(all_fwd,all_rts,REF):
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("[PLOT] matplotlib not available"); return
    colors={'GPS-only':'#e6194b','Galileo-only':'#4363d8','GPS+Galileo':'#3cb44b'}
    fig,axes=plt.subplots(2,2,figsize=(14,10))
    fig.suptitle('PPP-AR Multi-Constellation (v101) — Adaptive NL: no MAX_NL cap + consistency + post-fix + divergence undo + soft weights + ISB gate + controlled release',
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
    m=_compute_metrics(all_fwd.get('GPS+Galileo',{}),REF)
    if m is not None:
        sh=m['sods']/3600.
        ax.plot(sh,m['e_mm'],color='#e6194b',linewidth=0.8,label='East')
        ax.plot(sh,m['n_mm'],color='#3cb44b',linewidth=0.8,label='North')
        ax.plot(sh,m['u_mm'],color='#4363d8',linewidth=0.8,label='Up')
        ax.axhline(0,color='black',linewidth=0.5)
    ax.set_xlabel('Time (h)'); ax.set_ylabel('Error (mm)')
    ax.set_title('(b) ENU — GPS+Galileo FWD')
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
    mf=_compute_metrics(all_fwd.get('GPS+Galileo',{}),REF)
    mr=_compute_metrics(all_rts.get('GPS+Galileo',{}),REF)
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
        'ocnload.blq',
    ]]
    postpos(None,None,0.,0.,PrcOpt(),SolOpt(),FilOpt(),
            INFILES,os.path.join(DATA,'ppp_results3.csv'))
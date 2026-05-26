"""
# =======================================================================
# ppp_if_v3.py  — Stable Pure IF PPP (audited + runtime-safe)
# State: [dx,dy,dz,clk,ZWD,ISB_GAL,Gn,Ge | N_IF per sat]
# Obs:   P_IF=alpha*P1-beta*P2,  L_IF=alpha*L1-beta*L2  (2 per sat)
# No iono state, no RDCB, no WL/NL fixing, no A1/CODE_REJECT.
# =======================================================================

ppp.py  v91 — N1 stability fix: debounced jump detection + stability protection + sigma gate on reset
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
         (In IF model: N_IF ambiguity per satellite; no NL fixing)
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

# Pre-computed NL denominators
_DENOM_G = ALFA*LAMBDA1 - BETA*LAMBDA2         # GPS NL denom  ≈ 0.1073 m
_DENOM_E = ALFA_E*LAMBDA_E1 - BETA_E*LAMBDA_E5A  # Gal NL denom  ≈ 0.1090 m

def _sig_exp(el, s0, exp=1.0):
    """Elevation-weighted sigma: s0 / (sin(el)^exp + 0.05).
    The +0.05 floor prevents high-elevation domination and improves vertical stability."""
    return s0 / (max(math.sin(el), 0.2) ** exp + 0.05)

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
#  12-h harmonic vertical absorber — diagnostic option (v91 patch)
#  Set True to estimate and plot A·sin(2π·t/12h) + B·cos(2π·t/12h) from
#  post-convergence Up residuals.  Purely diagnostic: no filter state is touched.
# ==============================================================================
# ==============================================================================
#  RUNTIME CONTROL — extra diagnostic reruns
#  RUN_EXTRA_DIAGNOSTICS : master switch.  False (default) → all ablation,
#      leakage, harmonic, ZWD-audit, and perturbation suites are disabled.
#      No extra _ppp_pass calls are made; only the 3 primary passes run.
#      Set True to restore full diagnostic behaviour.
#  RUN_WHITEN_TEST : sub-switch for Run K AR(1) whitening sweep (obs_whitening).
#      Only active when RUN_EXTRA_DIAGNOSTICS is also True.
# ==============================================================================

# ==============================================================================
#  WL HARD DISABLE — pure-float kill switch
#  IF model: WL fixing not used (pure float IF).
#                              wl_fixed is force-cleared every epoch.
#  PURE_FLOAT        = True  → affirms that the run is unconditionally float.
# ==============================================================================

_HARMONIC_PERIOD:    float = 43200.0  # 12 h in seconds
_HARMONIC_CONV_SOD:  float = 7200.0   # fit only epochs after this SOD (2 h)

# ==============================================================================
#  ZWD↔Up coupling audit — diagnostic option (v91 patch)
#  ENABLE_ZWD_AUDIT : compute correlations, lagged xcorr, overlay plots,
#                     hump-window statistics, and write zwd_up_audit.csv.
#                     Does not change any solver state.
#  ZWD_Q_INFLATE_X2 : multiply the ZWD process-noise Q[4,4] by 2 for a
#                     single-run experiment.  Only troposphere Q is affected;
#                     ambiguity, iono, position, and clock Q are untouched.
#                     Flip to True, re-run, compare zwd_up_audit outputs.
# ==============================================================================
# ZWD_Q_INFLATE_X2 replaced by ZWD_Q_SCALE (Run L ablation).
# 1.0 = baseline; 3.0 = ×3; 5.0 = ×5.  Only Q[4,4] is affected.

# ==============================================================================
#  TEST_ZWD_FREEZE — targeted ZWD↔vertical coupling experiment
#  When True, one extra GPS+Galileo FWD pass is run after the baseline:
#    • Baseline pass  : existing behaviour, completely unchanged.
#    • Freeze pass    : Q[4,4] (ZWD process noise) forced to 0 for t ≥ 2 h,
#                       effectively pinning ZWD at its converged value.
#  Diagnostics printed for each auto-detected hump window:
#    hump amplitude (base vs frozen), ΔUp RMS, corr(Up,ZWD).
#  Verdict:
#    hump amplitude drops >20% in major window → ZWD-VERTICAL COUPLING SUPPORTED
#    otherwise                                 → ZWD COUPLING WEAK
#  One compact comparison plot saved as ppp_zwd_freeze.png.
#  No ambiguity logic, ISB states, or prior hump diagnostics are affected.
# ==============================================================================
TEST_ZWD_FREEZE: bool = False  # v94: disabled   # set False to skip the freeze experiment

# ==============================================================================
#  Run L — ZWD process-noise ablation (single-factor)
#  Three GPS+Galileo FWD passes: q_zwd ×1 (baseline), ×3, ×5.
#  Ambiguity, orbit/clock, mapping, gradients: ALL UNCHANGED.
#  Diagnostic only — no filter state is permanently modified.
# ==============================================================================
ENABLE_RUN_L:       bool = False  # DISABLED: Run L ZWD ablation (Passes A/B/C) removed for runtime

# ==============================================================================
#  Dual-harmonic vertical absorber — Run E diagnostic (v93 patch)
#  Fits u(t) = A1·sin(2πt/12h) + B1·cos(2πt/12h)
#            + A2·sin(2πt/24h) + B2·cos(2πt/24h)
#  to post-convergence Up residuals.  Purely post-processing; no KF state
#  is touched.  Reports both 12-h and 24-h amplitudes and corrected RMS.
# ==============================================================================
RUN_EXTRA_DIAGNOSTICS: bool = False   # master diagnostic gate
RUN_WHITEN_TEST:       bool = False   # AR(1) whitening experiment
ENABLE_DUAL_HARMONIC: bool = RUN_EXTRA_DIAGNOSTICS  # disabled: Run E post-proc plot

# ==============================================================================
#  Clock/Orbit residual audit — Run G diagnostic (v93 patch)
#  Correlates per-epoch code_rms and phase_rms with Up error in hump windows.
#  Also audits clock-state drift rate (dclk/dt).  No KF state is changed.
# ==============================================================================
ENABLE_CLOCK_ORBIT_AUDIT: bool = RUN_EXTRA_DIAGNOSTICS  # disabled: Run G post-proc

# ==============================================================================
#  Forward-Filter Leakage Verification — Runs H, I, J (v94 diagnostic)
#
#  Run H  – Position/Clock Q sensitivity
#    Two new GPS+Galileo FWD passes:
#      H_Q_LO : Q_pos = 1e-8×dt × 0.3    Q_clk = 1e4×dt × 0.3
#      H_Q_HI : Q_pos = 1e-8×dt × 3.0    Q_clk = 1e4×dt × 3.0
#    ZWD, iono, and ambiguity Q are completely untouched.
#    If hump amplitude scales with Q → filter leakage confirmed.
#    If hump is Q-independent → hump has a different origin.
#
#  Run I  – Innovation spectral audit (post-processing, no new KF pass)
#    Computes Lomb-Scargle/FFT periodogram of:
#      · Up error time series (post-convergence, t ≥ 2 h)
#      · per-epoch phase_rms time series
#    Flags peaks at 12-h and 24-h orbital periods.
#    Compares spectrum inside vs outside hump windows.
#
#  Run J  – Satellite clock perturbation discriminator
#    Two new GPS+Galileo FWD passes:
#      J_CLK_POS : all obs shifted by +0.02 m (≡ +2 cm sat-clock bias)
#      J_CLK_NEG : all obs shifted by −0.02 m (≡ −2 cm sat-clock bias)
#    If Up hump shifts coherently → clock-position coupling (leakage confirmed).
#    If Up hump unchanged → hump is not clock-driven (leakage rejected).
#
#  STRICT constraints:
#    · Ambiguity logic, WL/NL fixing, ZWD Q, iono Q, measurement equations,
#      weights, and solver structure are ALL UNCHANGED.
#    · Only pos_clk_q_scale and clk_perturb_m parameters are added to
#      _ppp_pass; all existing parameters retain their default values.
# ==============================================================================
ENABLE_LEAKAGE_DIAG: bool = RUN_EXTRA_DIAGNOSTICS  # disabled: Runs H, I, J, K (6 extra passes)
_LEAKAGE_CLK_PERTURB_M: float = 0.02   # 2 cm clock perturbation for Run J

# ==============================================================================
#  Common-Mode Receiver Clock Leakage Diagnostic  (Run CM — diagnostic only)
#  Post-processing only.  NO changes to KF, ambiguity logic, ZWD, ISB, orbits,
#  weighting, or process noise.
#
#  Uses auto-detected hump windows (from _hump_attribution_audit).
#  For each epoch in each window:
#    - Computes robust median of post-fit phase residuals across all tracked sats
#      (GPS-only, Galileo-only, and combined — separately).
#    - Subtracts this common-mode from the per-satellite phase residuals and
#      recomputes Up error via a post-processing position update (no filter state
#      is mutated; a diagnostic position shift is computed analytically from the
#      H matrix and the de-common-moded residual vector).
#  Plots:
#    - receiver clock estimate vs common_mode(t)
#    - baseline vs de-common-moded Up error (full arc + hump zoom)
#    - per-constellation common-mode time series
#  Verdict:
#    - >30% hump amplitude reduction → clock leakage CONFIRMED
#    - <30% reduction               → clock leakage REJECTED
#  ENABLE_CM_CLOCK_DIAG = True to activate (independent of RUN_EXTRA_DIAGNOSTICS;
#  no new KF pass required — post-processing on existing results dicts only).
# ==============================================================================
ENABLE_CM_CLOCK_DIAG: bool = False  # v94: disabled   # diagnostic only — no solver changes

# ==============================================================================
#  Clock-Mechanism Separation Diagnostic  (v96-diag)
#  Goal: isolate WHICH clock mechanism causes common-mode leakage.
#  Tests H1 (precise product inconsistency), H2 (receiver clock process model),
#  H3 (ISB/constellation coupling), H4 (clock observability/weighting transients).
#  H2 requires 4 extra _ppp_pass reruns (clk_q_scale ∈ {0.1, 0.3, 1, 3}).
#  H1, H3, H4 are post-processing only — no new KF pass.
#  Uses _FIXED_HUMP_WINDOWS exclusively.  ZWD/ambiguity/NL unchanged.
# ==============================================================================

# ==============================================================================
#  Subdaily absorber process-noise — Run F diagnostic (v93 patch)
#  When _ppp_pass is called with enable_subdaily_absorber=True a single extra
#  state v_load is prepended to the base state block (index 5).  It models a
#  slow empirical vertical correction (random walk, σ ≈ 10 mm/h).
#  Ambiguity, ZWD, iono, clock, and position states are completely unchanged.
# ==============================================================================
_SDA_Q_NOISE: float = 4.0e-9   # m²/epoch — ~(10 mm)² / (2500 ep/day) ≈ 4e-9

# ==============================================================================
#  Loading / Mapping ablation suite  (v92 patch)
#  Goal: determine whether the Up hump originates from a loading model
#  deficiency (OTL missing/wrong, pole tide uncorrected) or from a mapping
#  function error (GMF bias at IISC latitude).
#
#  ABLATION_RUN_SUITE : master switch.  When True, three extra GPS+Galileo
#       forward passes are run after the main 3-constellation block, each
#       with exactly ONE model switch toggled:
#         run A — OTL disabled     (baseline has OTL ON)
#         run B — Pole Tide ON     (baseline has Pole Tide OFF)
#         run C — NMF mapping      (baseline uses GMF)
#       For each run the script reports:
#         obs_ΔUp (5–8 h)   obs_ΔUp (17–20 h)   Up RMS   corr(Up,ZWD)
#         obs/exp leakage ratio (both hump windows)
#       No ambiguity, satellite, or stochastic changes.
#
#  _ABLATION_MAP_FUNC : module-level override for the wet/hydrostatic mapping
#       function used in _proc / _proc_gal.  None → use _gmf (default).
#  _ABLATION_ERP      : module-level dict of ERP values (xp, yp in arc-sec)
#       keyed by MJD.  Empty dict → no pole tide correction.
# ==============================================================================

_ABLATION_MAP_FUNC = None   # None → _gmf; set to _nmf for alternate-mapping run
_ABLATION_ERP: dict = {}    # empty → no pole tide; filled from ERP file for ptide run

# ==============================================================================
#  v92 Early-Hump Mechanism Tests  (arc-rise geometry vs iono bandwidth)
#  Hypothesis: early hump (GPS-only 2–5.4 h, Galileo-only 2–3.15 h) driven by
#  equatorial ionospheric residuals + new-rise arc geometry transients.
#  H2 (receiver clock process) already rejected as null result.
#
#  TEST A — New-rise arc ramp-in weighting
#    For satellites newly risen (arc age < N epochs), inflate code sigma from
#    RAMP_INIT_SCALE × normal → normal linearly over N epochs.
#    This prevents a fresh noisy arc from imprinting iono residuals on position.
#    Tests: N ∈ {10, 20, 40}, RAMP_INIT_SCALE = 4.0 (sigma×4 → variance×16).
#    All other weights, states, NL logic, ZWD, ISB unchanged.
#
#  TEST B — Ionosphere process noise sensitivity
#    Reruns GPS+Galileo FWD with ION_PROC_NOISE scaled by {1/3, 1, 3}.
#    Reports hump amplitude and Up RMS change.
#    All other filter states unchanged.
#
#  HUMP WINDOWS FIXED (auto-detection already confirmed):
#    GPS-only hump1:     2.00 – 5.40 h
#    Galileo-only hump1: 2.00 – 3.15 h
# ==============================================================================

# TEST A parameters
_RAMP_N_EPOCHS: list = [10, 20, 40]  # ramp lengths to test
_RAMP_INIT_CODE_SCALE: float = 4.0   # initial code sigma multiplier at arc age=0
# The ramp reduces linearly: scale = INIT + (1-INIT)*(age/N) for age < N, else 1.

# TEST B parameters
_ION_Q_SCALES: list = [1.0/3.0, 1.0, 3.0]  # multipliers on ION_PROC_NOISE

# Hump windows (fixed — do not auto-detect for this test)
_EARLY_HUMP_WIN = {
    'GPS-only':     (2.00 * 3600., 5.40 * 3600.),
    'Galileo-only': (2.00 * 3600., 3.15 * 3600.),
    'GPS+Galileo':  (2.00 * 3600., 5.40 * 3600.),  # use GPS window for combined
}

# ==============================================================================
#  v94 Clock-Process-Noise Sensitivity Test
#  Hypothesis: early hump1 (2-5.4 h) driven by receiver clock process noise
#  leaking into vertical position.  Test by scaling only Q[3,3] (clock state)
#  while leaving ALL other states (position, ZWD, iono, ambiguities) unchanged.
#
#  Three GPS+Galileo FWD passes:
#    clk_q x 0.3  (tighter clock)
#    clk_q x 1.0  (baseline - identical to primary pass)
#    clk_q x 3.0  (looser clock)
#
#  clk_q_scale parameter already exists in _ppp_pass (v96 CLK_MECH H2).
#  No measurement model, weighting, iono, ZWD, or ambiguity logic touched.
#
#  FIXED hump1 window: 2.00 - 5.40 h (GPS+Galileo overlap)
# ==============================================================================
# ==============================================================================
#  v99 Adaptive Residual Censoring Test
#  Auto-detects hump windows from baseline, reruns with per-epoch satellite
#  censoring active only inside those windows.  No other solver changes.
# ==============================================================================
_CLK_Q_SCALES: list       = [0.3, 1.0, 3.0]
_CLK_Q_HUMP1_WIN: tuple   = (2.00 * 3600., 5.40 * 3600.)

# Run-L ablation scalar — multiplies ZWD process noise Q[IDX_ZWD,IDX_ZWD] only.
# 1.0 = neutral (no change to ZWD Q magnitude).  Replaces ZWD_Q_INFLATE_X2.
ZWD_Q_SCALE: float = 2.0



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


def parse_erp(fp):
    """Parse an IGS ERP (Earth Rotation Parameters) file.

    Supports both legacy and extended IGS/CODE ERP format.

    Unit convention (auto-detected from value magnitude):
      CODE MGEX final (COD0MGXFIN): Xp/Yp in micro-arcseconds (μas = 10⁻⁶ arcsec)
      Some older products:           Xp/Yp in milli-arcseconds (mas = 10⁻³ arcsec)
    Typical polar motion amplitude: 0.1–0.5 arcsec = 100,000–500,000 μas = 100–500 mas.
    Auto-detect:  |xp| > 500  → assume μas (multiply by 1e-6)
                  |xp| > 0.5  → assume mas (multiply by 1e-3)
                  else        → assume already in arcsec

    Returns dict: {mjd_float: {'xp': float [arcsec], 'yp': float [arcsec]}}
    """
    erp = {}
    if not fp or not os.path.isfile(fp):
        print(f"[ERP]  Not found: {fp}")
        return erp
    try:
        with open(fp, 'r', errors='replace') as fh:
            lines = fh.readlines()
    except Exception as exc:
        print(f"[ERP]  Cannot read {fp}: {exc}")
        return erp

    raw_rows = []
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith('#') or ln.startswith('*') or not ln[0].isdigit():
            continue
        toks = ln.split()
        if len(toks) < 3:
            continue
        try:
            raw_rows.append((float(toks[0]), float(toks[1]), float(toks[2])))
        except ValueError:
            continue

    if not raw_rows:
        print(f"[ERP]  WARNING — no data rows parsed from {os.path.basename(fp)}")
        return erp

    # Auto-detect units from median absolute value of Xpole column
    import statistics as _stat
    xp_vals = [abs(r[1]) for r in raw_rows]
    median_xp = _stat.median(xp_vals)
    if median_xp > 500.0:
        scale = 1e-6   # μas → arcsec  (CODE MGEX default)
        unit_str = "μas"
    elif median_xp > 0.5:
        scale = 1e-3   # mas → arcsec
        unit_str = "mas"
    else:
        scale = 1.0    # already arcsec
        unit_str = "arcsec"

    print(f"[ERP]  Unit auto-detect: median|xp|={median_xp:.1f} → treating as {unit_str}")
    for mjd, xp_raw, yp_raw in raw_rows:
        erp[mjd] = {'xp': xp_raw * scale, 'yp': yp_raw * scale}

    print(f"[ERP]  {len(erp)} records parsed from {os.path.basename(fp)}")
    return erp


def _erp_interp(erp, tow_total):
    """Linearly interpolate ERP xp/yp at a given GPS total-seconds epoch.

    Returns (xp_arcsec, yp_arcsec) or (0., 0.) if no data.
    """
    if not erp:
        return 0., 0.
    # GPS total seconds → MJD  (GPS epoch = MJD 44244.0)
    mjd = 44244.0 + tow_total / 86400.0
    keys = sorted(erp.keys())
    if mjd <= keys[0]:
        e = erp[keys[0]]
        return e['xp'], e['yp']
    if mjd >= keys[-1]:
        e = erp[keys[-1]]
        return e['xp'], e['yp']
    # bracket
    i = 0
    while i < len(keys) - 1 and keys[i + 1] < mjd:
        i += 1
    k0, k1 = keys[i], keys[i + 1]
    t = (mjd - k0) / (k1 - k0) if k1 != k0 else 0.
    xp = erp[k0]['xp'] + t * (erp[k1]['xp'] - erp[k0]['xp'])
    yp = erp[k0]['yp'] + t * (erp[k1]['yp'] - erp[k0]['yp'])
    return xp, yp


def _ptl_disp(lat, lon, xp_arcsec, yp_arcsec):
    """Compute solid-Earth pole tide displacement in ECEF (metres).

    Implements IERS Conventions 2010, Section 7.1.4, Eqs. (7.24)–(7.26).
    The secular mean pole (linear trend) is computed per IERS 2010 Table 7.7
    and subtracted from the observed pole.

    Parameters
    ----------
    lat, lon      : float  geodetic latitude / longitude (radians)
    xp_arcsec     : float  observed pole x from ERP [arc-sec]
    yp_arcsec     : float  observed pole y from ERP [arc-sec]

    Returns
    -------
    np.ndarray (3,) ECEF displacement in metres.
    """
    _arcsec2rad = math.pi / (180.0 * 3600.0)

    # IERS 2010 Table 7.7 mean-pole linear model (valid 1976–2050)
    # xp_mean [arcsec] = 0.055 + 1.677×10⁻³ t   (t in years from J2000)
    # yp_mean [arcsec] = 0.346 + 3.460×10⁻³ t
    # For a single-day processing we use t=2026.1 (approx DOY 38, year 2026)
    t_yr = 26.1   # years from J2000 (2026 - 2000)
    xp_mean =  0.055 + 1.677e-3 * t_yr   # arcsec
    yp_mean =  0.346 + 3.460e-3 * t_yr   # arcsec

    # Reduced pole offset [arc-sec]
    m1 = xp_arcsec - xp_mean
    m2 = -(yp_arcsec - yp_mean)   # note sign convention (IERS Eq. 7.24)

    # Love numbers (IERS 2010 Table 6.3 — real parts of H2, L2)
    Sp = -0.01737  # [m/arcsec] — radial coefficient
    Sp_h = 0.6084  # h₂ Love number used in IERS formulation
    Sp_l = 0.0831  # l₂ Love number

    # Eq. (7.24): ENU displacements [metres]
    sl, cl = math.sin(lat), math.cos(lat)
    s2l    = math.sin(2.0 * lat)
    c2l    = math.cos(2.0 * lat)
    sn, cn = math.sin(lon), math.cos(lon)

    # IERS 2010, Eq. (7.24):
    #   δU = −32 mm · sin(2φ) · (m₁ cos λ + m₂ sin λ)
    #   δN = −9  mm · cos(2φ) · (m₁ cos λ + m₂ sin λ)
    #   δE = +9  mm · cos φ   · (m₁ sin λ − m₂ cos λ)
    # where displacements are in mm and m₁,m₂ are in arcsec
    dU_mm = -32.0 * s2l * (m1 * cn + m2 * sn)
    dN_mm =  -9.0 * c2l * (m1 * cn + m2 * sn)
    dE_mm =   9.0 * cl  * (m1 * sn - m2 * cn)

    dU = dU_mm * 1e-3
    dN = dN_mm * 1e-3
    dE = dE_mm * 1e-3

    # ENU → ECEF
    return np.array([
        -sn * dE - sl * cn * dN + cl * cn * dU,
         cn * dE - sl * sn * dN + cl * sn * dU,
                   cl * dN      + sl * dU
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


def _nmf(lat, doy, el):
    """Niell Mapping Function (NMF, Niell 1996) — alternate to GMF.

    Returns (mh, mw) — hydrostatic and wet mapping function values.
    Used in ablation run C to test mapping-function sensitivity.

    Reference: Niell, A.E. (1996). Global mapping functions for the
    atmosphere delay at radio wavelengths. JGR 101(B2), 3227-3246.
    """
    if el < 1e-4:
        el = 1e-4
    s = math.sin(el)

    # ── Hydrostatic (dry) mapping function ─────────────────────────────────
    # Table 1 of Niell (1996): interpolation coefficients for ah, bh, ch
    # at latitudes 15°, 30°, 45°, 60°, 75° (mean values only — seasonal
    # variation omitted for simplicity in this diagnostic implementation)
    lats_h = [15., 30., 45., 60., 75.]   # degrees
    ah_t = [1.2769934e-3, 1.2683230e-3, 1.2465397e-3, 1.2196049e-3, 1.2045996e-3]
    bh_t = [2.9153695e-3, 2.9152299e-3, 2.9288445e-3, 2.9022565e-3, 2.9024912e-3]
    ch_t = [0.062610505,  0.062837393,  0.063721774,  0.063824265,  0.064258455 ]

    # ── Wet mapping function ────────────────────────────────────────────────
    aw_t = [5.8021897e-4, 5.6794847e-4, 5.8118019e-4, 5.9727542e-4, 6.1641693e-4]
    bw_t = [1.4275268e-3, 1.5138625e-3, 1.4572752e-3, 1.5007428e-3, 1.7599082e-3]
    cw_t = [0.043472961,  0.046729510,  0.043908931,  0.044626982,  0.054736038 ]

    lat_d = math.degrees(lat)
    lat_d = max(15., min(75., abs(lat_d)))   # clamp to table range

    # Linear interpolation between table rows
    def interp1(lats, vals, x):
        if x <= lats[0]:  return vals[0]
        if x >= lats[-1]: return vals[-1]
        for k in range(len(lats)-1):
            if lats[k] <= x <= lats[k+1]:
                t = (x - lats[k]) / (lats[k+1] - lats[k])
                return vals[k] + t*(vals[k+1] - vals[k])
        return vals[-1]

    ah = interp1(lats_h, ah_t, lat_d)
    bh = interp1(lats_h, bh_t, lat_d)
    ch = interp1(lats_h, ch_t, lat_d)
    aw = interp1(lats_h, aw_t, lat_d)
    bw = interp1(lats_h, bw_t, lat_d)
    cw = interp1(lats_h, cw_t, lat_d)

    def _cf(s, a, b, c):
        return (1. + a / (1. + b / (1. + c))) / (s + a / (s + b / (s + c)))

    mh = _cf(s, ah, bh, ch) / _cf(1., ah, bh, ch)
    mw = _cf(s, aw, bw, cw) / _cf(1., aw, bw, cw)
    return mh, mw

def _vmf1(lat, doy, el):
    """Vienna Mapping Function 1 — analytical (VMF1-G) approximation.

    Implements the empirical VMF1 model from Boehm, Werl & Schuh (2006)
    using the same continued-fraction form as GMF but with latitude/season-
    dependent *ah* and *aw* coefficients derived from the published VMF1-G
    regression tables (Boehm et al. 2006, Table 2 and Figure 2).

    The hydrostatic *bh* / *ch* and wet *bw* / *cw* coefficients are held
    at their GMF values (these higher-order terms are insensitive to
    latitude and season — Boehm et al. 2006 §3).

    Reference:
      Boehm, J., Werl, B., Schuh, H. (2006).  Troposphere mapping
      functions for GPS and VLBI from ECMWF operational analysis data.
      J. Geophys. Res. Solid Earth 111, B02406.
      https://doi.org/10.1029/2005JB003629

    Returns
    -------
    (mh, mw) : hydrostatic and wet mapping-function values (dimensionless).
    """
    if el < 1e-4:
        el = 1e-4
    s = math.sin(el)

    lat_d   = math.degrees(lat)
    lat_abs = abs(lat_d)

    # ── Hydrostatic ah: mean a0h + seasonal amplitude a1h ───────────────────
    # Tabulated at 0°, 10°, 20°, … 90° latitude from the VMF1-G global fit.
    # a0h values follow the published grid means; a1h reflects the seasonal
    # modulation amplitude (NH larger due to stronger annual cycle).
    lats  = [0., 10., 20., 30., 40., 50., 60., 70., 80., 90.]
    a0h_t = [1.2677e-3, 1.2634e-3, 1.2589e-3, 1.2476e-3, 1.2296e-3,
             1.2083e-3, 1.1855e-3, 1.1740e-3, 1.1716e-3, 1.1711e-3]
    a1h_t = [1.5e-5,   1.6e-5,   1.9e-5,   2.3e-5,   2.8e-5,
             3.1e-5,   3.0e-5,   2.4e-5,   1.5e-5,   1.0e-5]

    # ── Wet aw: mean value (no significant seasonal modulation, Boehm 2006) ─
    aw_t  = [5.800e-4, 5.750e-4, 5.680e-4, 5.620e-4, 5.730e-4,
             5.900e-4, 6.120e-4, 6.300e-4, 6.350e-4, 6.350e-4]

    # bh, ch, bw, cw  — held at GMF values (Boehm et al. 2006 §3)
    bh = 2.9153695e-3;  ch = 0.062610505
    bw = 1.8128e-3;     cw = 0.062553963

    def _interp1(xs, ys, x):
        x = max(xs[0], min(xs[-1], x))
        for k in range(len(xs) - 1):
            if xs[k] <= x <= xs[k + 1]:
                t = (x - xs[k]) / (xs[k + 1] - xs[k])
                return ys[k] + t * (ys[k + 1] - ys[k])
        return ys[-1]

    # Seasonal phase: NH winter minimum at DOY 28; SH offset by 6 months
    phase = 28.0 if lat_d >= 0.0 else 211.0
    seas  = math.cos(2.0 * math.pi * (doy - phase) / 365.25)

    a0h = _interp1(lats, a0h_t, lat_abs)
    a1h = _interp1(lats, a1h_t, lat_abs)
    ah  = a0h + a1h * seas

    aw  = _interp1(lats, aw_t,  lat_abs)

    def _cf(s_, a_, b_, c_):
        return (1. + a_ / (1. + b_ / (1. + c_))) / \
               (s_ + a_ / (s_ + b_ / (s_ + c_)))

    mh = _cf(s, ah, bh, ch) / _cf(1., ah, bh, ch)
    mw = _cf(s, aw, bw, cw) / _cf(1., aw, bw, cw)
    return mh, mw


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
    N=len(data); dim=8   # x,y,z,dt_GPS,dt_GAL,ZWD,Gn,Ge
    sods=[d[0] for d in data]
    xs=[d[1][:dim].copy() if len(d[1])>=dim else np.concatenate([d[1], np.zeros(dim-len(d[1]))]) for d in data]
    Ps=[d[2][:dim,:dim].copy() if d[2].shape[0]>=dim else np.pad(d[2][:d[2].shape[0],:d[2].shape[0]],((0,dim-d[2].shape[0]),(0,dim-d[2].shape[0]))) for d in data]
    xs_s=[None]*N; Ps_s=[None]*N
    xs_s[-1]=xs[-1].copy(); Ps_s[-1]=Ps[-1].copy()
    for k in range(N-2,-1,-1):
        dt=abs(sods[k+1]-sods[k])
        if dt<=0 or dt>3600: dt=30.
        F=np.eye(dim)
        Q_k=np.zeros((dim,dim))
        Q_k[0,0]=Q_k[1,1]=Q_k[2,2]=1e-8*dt; Q_k[3,3]=(0.01)**2*dt
        Q_k[4,4]=2.5e-9*dt                # ZWD baseline
        Q_k[5,5]=(0.001)**2*dt            # ISB_GAL near-constant (tightened from 0.01)
        Q_k[6,6]=(0.001)**2*dt    # Gn north gradient
        Q_k[7,7]=(0.001)**2*dt    # Ge east gradient
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
#  NL float / fix helpers — removed in IF model
# ==============================================================================

# ==============================================================================
#  Per-satellite geometry
# ==============================================================================
# v84 PART 1: state-change-only AR logging — avoids per-epoch per-sat spam
_prev_gps_ar_state = {}   # sid -> bool (no_AR from last logged epoch)

# ── DIAGNOSTIC GLOBALS (instrumentation only — read, never modify solution) ──
_diag_prev_sig_pair  = {}  # sid -> (code1, phase1) from last epoch — fallback switch detection
_diag_sig_switch_cnt = {}  # sid -> int — total signal switches observed
_diag_res_gps_phase  = []  # running postfit L1 phase residuals for GPS (appended each epoch)
_diag_res_gal_phase  = []  # running postfit L1 phase residuals for Galileo
_diag_res_gps_code   = []  # running postfit P1 code residuals for GPS
_diag_res_gal_code   = []  # running postfit P1 code residuals for Galileo
_diag_lp1_sat        = {}  # sid -> list of (L1m - P1c) values — accumulated all epochs

def _proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx,
          blq=None,sta='IISC',tow_total=0.,map_func=None,erp=None):
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
    #   C1W + L1W  +  C2W + L2W  (TIER 1 — strict W-band)
    #   C1W + L1C  +  C2W + L2W  (TIER 2 — Septentrio/IISC: C1W code, L1C phase)
    #   C1C + L1C  +  C2W + L2W  (TIER 3 — fallback, no C1W)
    # RELAXED PPP signals (fallback, PPP only):
    #   PRIMARY   : C1W/L1W + C2W/L2W  → use_for_ppp=True, use_for_ar=True
    #   FALLBACK  : C1C/L1C + C2W/L2W  → use_for_ppp=True, use_for_ar=False

    #   TIER 1 — PRIMARY     : C1W + L1W + C2W + L2W  (strict W-band, legacy receivers)
    #   TIER 2 — INTERMEDIATE: C1W + L1C + C2W + L2W  (Septentrio/IISC — C1W code, L1C phase)
    #   TIER 3 — FALLBACK    : C1C + L1C + C2W + L2W  (last resort, no C1W at all)
    # IISC Septentrio PolaRx5: has C1W but NOT L1W → TIER 2 is the correct choice.

    use_for_ppp   = False
    use_for_ar    = False
    _fallback_used = False

    _P1W = so.get('C1W', 0.); _L1W = so.get('L1W', 0.)
    _P2W = so.get('C2W', 0.); _L2W = so.get('L2W', 0.)
    _P1C = so.get('C1C', 0.); _L1C = so.get('L1C', 0.)

    if _P1W != 0. and _L1W != 0. and _P2W != 0. and _L2W != 0.:
        # TIER 1 PRIMARY: all W-band signals present — PPP + AR eligible
        code1_type, code2_type, phase1_type, phase2_type = 'C1W', 'C2W', 'L1W', 'L2W'
        P1_val, P2_val, L1_val, L2_val = _P1W, _P2W, _L1W, _L2W
        use_for_ppp = True
        use_for_ar  = True
    elif _P1W != 0. and _L1C != 0. and _P2W != 0. and _L2W != 0.:
        # TIER 2 INTERMEDIATE: C1W code (aligned with IF clock) + L1C phase (Septentrio)
        # C1W OSB and L1C OSB are both defined in CODE MGEX → AR eligible.
        # _fallback_used = False (C1W is the primary-quality code, not a fallback).
        code1_type, code2_type, phase1_type, phase2_type = 'C1W', 'C2W', 'L1C', 'L2W'
        P1_val, P2_val, L1_val, L2_val = _P1W, _P2W, _L1C, _L2W
        use_for_ppp = True
        use_for_ar  = True
        _fallback_used = False   # C1W is primary quality; not a fallback
    elif _P1C != 0. and _L1C != 0. and _P2W != 0. and _L2W != 0.:
        # TIER 3 FALLBACK: no C1W at all — use C1C/L1C + C2W/L2W
        code1_type, code2_type, phase1_type, phase2_type = 'C1C', 'C2W', 'L1C', 'L2W'
        P1_val, P2_val, L1_val, L2_val = _P1C, _P2W, _L1C, _L2W
        use_for_ppp    = True
        use_for_ar     = True   # v83: tentative — OSB check below is the gate
        _fallback_used = True
    else:
        return None  # skip satellite — no usable signal combination

    # Signal map lock: records the signal frame used for AR-eligible satellites.
    # v83: fallback (C1C) satellites with valid OSB are also locked here since
    # they are now AR-eligible.  Satellites without valid OSB (use_for_ar=False)
    # are not locked so they can become AR-eligible on a later epoch if OSB arrives.
    if use_for_ar and sid not in _sat_signal_map:
        _sat_signal_map[sid] = (code1_type, code2_type, phase1_type, phase2_type)

    # ── DIAG: [FALLBACK_EVENT] signal-switch detection ─────────────────────
    # Compare current signal pair against last-seen pair.  A switch (primary→
    # fallback or back) produces a code/phase jump proportional to the OSB
    # difference between the two signal types.
    _cur_sig_pair = (code1_type, phase1_type)
    _prev_pair    = _diag_prev_sig_pair.get(sid)
    if _prev_pair is not None and _prev_pair != _cur_sig_pair:
        _diag_sig_switch_cnt[sid] = _diag_sig_switch_cnt.get(sid, 0) + 1
        # Δcode = magnitude of code-OSB difference between old and new signal at L1
        # (best proxy for the jump without re-running _proc on the old signal)
        _delta_code_jump = abs(_code_osb_1) if _code_osb_1 is not None else float('nan')
        print(f"[FALLBACK_EVENT] sat={sid} prev_sig={_prev_pair[0]}/{_prev_pair[1]} "
              f"new_sig={_cur_sig_pair[0]}/{_cur_sig_pair[1]} "
              f"fallback_now={_fallback_used} "
              f"delta_code_osb1={_delta_code_jump:+.4f}m "
              f"total_switches={_diag_sig_switch_cnt[sid]}")
    _diag_prev_sig_pair[sid] = _cur_sig_pair
    # ── end DIAG ───────────────────────────────────────────────────────────

    no_AR = not use_for_ar   # backward-compat alias used by NL candidate loop

    P1=P1_val; P2=P2_val; L1=L1_val; L2=L2_val

    # v119 STEP 1: Dynamic OSB application (COD0MGXFIN IF-frame clocks).
    # Fetch OSBs matching the actual signals used; apply only when ALL four are present.
    # If any OSB is missing → no_AR=True (satellite kept in filter, excluded from AR).
    ar_skip_reason = None
    lam1 = LAMBDA1
    lam2 = LAMBDA2

    _prn = sid  # e.g. 'G01'
    _c1_key  = code1_type   # 'C1W' or 'C1C'
    _c2_key  = code2_type   # 'C2W'
    _l1_key  = phase1_type  # 'L1W' or 'L1C'
    _l2_key  = phase2_type  # 'L2W'
    _prn_osb = osb.get(_prn, {}) if osb else {}

    _code_osb_1 = _prn_osb.get(_c1_key)
    _code_osb_2 = _prn_osb.get(_c2_key)
    _phase_osb_1 = _prn_osb.get(_l1_key)
    _phase_osb_2 = _prn_osb.get(_l2_key)

    _osb_complete = None not in (_code_osb_1, _code_osb_2, _phase_osb_1, _phase_osb_2)
    _has_valid_osb = _osb_complete

    if _osb_complete:
        # Validity gate: reject implausible OSB values
        if (abs(_code_osb_1) > 10. or abs(_code_osb_2) > 10. or
                abs(_phase_osb_1) > 1. or abs(_phase_osb_2) > 1.):
            _osb_complete = False
            _has_valid_osb = False
            no_AR = True
            use_for_ar = False
            ar_skip_reason = 'bad_bias'
            P1c = P1; P2c = P2
            L1m = L1 * lam1; L2m = L2 * lam2
        else:
            # Apply OSBs BEFORE measurement model (all four observables)
            P1c = P1 - _code_osb_1
            P2c = P2 - _code_osb_2
            L1m = L1 * lam1 - _phase_osb_1
            L2m = L2 * lam2 - _phase_osb_2
            no_AR = not use_for_ar   # preserve signal-consistency gate from above
    else:
        # Missing OSB for one or more signals — run raw, exclude from AR
        P1c = P1; P2c = P2
        L1m = L1 * lam1; L2m = L2 * lam2
        no_AR = True
        use_for_ar = False
        ar_skip_reason = 'no_osb'


    _prev_gps_ar_state[sid] = no_AR
    _sig_pair_str = f"{code1_type}/{phase1_type}+{code2_type}/{phase2_type}"
    if sid not in _osb_dbg_printed:
        _osb_dbg_printed.add(sid)
        _osb_status = (f"code=({_code_osb_1:+.4f},{_code_osb_2:+.4f})m "
                       f"phase=({_phase_osb_1:+.6f},{_phase_osb_2:+.6f})m applied=True"
                       if _osb_complete else "applied=False(missing)")
        print(f"{sid}: signals={_sig_pair_str}, osb={_osb_status}, used=True")
        print(f"[OSB_APPLY] sat={sid} signals={_sig_pair_str} "
              f"code_osb=({_code_osb_1},{_code_osb_2}) "
              f"phase_osb=({_phase_osb_1},{_phase_osb_2}) "
              f"applied={_osb_complete}")
    gamma = F1SQ / F2SQ
    # Bug A fix: subtract phase WL satellite bias from MW combination.
    # _mw_cyc uses raw L1/L2 cycles; OSB-corrected code (P1c/P2c) removes code NL bias,
    # but the phase WL bias b_wl = (f1*bL1 - f2*bL2)/((f1-f2)*lambda_WL) must be removed too.
    _b_wl_gps = 0.0
    if _osb_complete and _phase_osb_1 is not None and _phase_osb_2 is not None:
        _b_wl_gps = (FREQ1 * _phase_osb_1 - FREQ2 * _phase_osb_2) / ((FREQ1 - FREQ2) * LAMBDA_WL)
    MW_cyc = _mw_cyc(P1c, P2c, L1, L2) - _b_wl_gps
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
    # Pole tide displacement (optional — only when erp dict is provided)
    if erp and tow_total>0.:
        xp_as, yp_as = _erp_interp(erp, tow_total)
        ra = ra + _ptl_disp(lat_r, lon_r, xp_as, yp_as)

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
    mh,mw=(map_func or _gmf)(lat0,doy,el)
    return dict(sid=sid,unit=unit,mh=mh,mw=mw,el=el,az=az,
                rng=rng,scm=scm,dtrel=dtrel,shp=shp,setm=setm,
                pcv_sat=pcvs,pcv_rec=pcvr,trop_zhd=mh*zhd,
                # v54 RAW observables
                P1c=P1c,P2c=P2c,L1m=L1m,L2m=L2m,
                lam1=lam1,lam2=lam2,gamma=gamma,
                MW_cyc=MW_cyc,GF_m=GF_m,
                L1=L1,L2=L2,P1=P1,P2=P2,
                sat_xyz=sva,rec_apc=ra,
                no_AR=no_AR,
                ar_skip_reason=ar_skip_reason,
                use_for_ppp=use_for_ppp,
                use_for_ar=use_for_ar,
                _fallback_used=_fallback_used,
                code1_type=code1_type,
                phase1_type=phase1_type,
                code2_type=code2_type,
                phase2_type=phase2_type)

def _proc_gal(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx,
              blq=None,sta='IISC',tow_total=0.,map_func=None,erp=None):
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
    _gal_sig_str = f"{code1_type}/{phase1_type}+{code2_type}/{phase2_type}"

    # v119 STEP 1: Dynamic OSB application — Galileo C1C/L1C + C5Q/L5Q.
    # COD0MGXFIN uses IF-frame clocks; raw observables must be corrected with OSBs
    # to enter the same frame.  Missing any of the four OSBs → satellite kept in
    # filter but excluded from AR.
    no_AR = False
    ar_skip_reason = None

    _prn = sid  # e.g. 'E01'
    _prn_osb = osb.get(_prn, {}) if osb else {}
    _code_osb_1  = _prn_osb.get('C1C')
    _code_osb_2  = _prn_osb.get('C5Q')
    _phase_osb_1 = _prn_osb.get('L1C')
    _phase_osb_2 = _prn_osb.get('L5Q')

    _osb_complete = None not in (_code_osb_1, _code_osb_2, _phase_osb_1, _phase_osb_2)

    if _osb_complete:
        if (abs(_code_osb_1) > 10. or abs(_code_osb_2) > 10. or
                abs(_phase_osb_1) > 1. or abs(_phase_osb_2) > 1.):
            _osb_complete = False
            no_AR = True
            ar_skip_reason = 'bad_bias'
            P1c = P1; P2c = P5
            lam1 = LAMBDA_E1; lam2 = LAMBDA_E5A
            L1m = P1 * lam1; L2m = P5 * lam2  # raw (will be overwritten below)
            L1m = L1 * lam1; L2m = L5 * lam2
        else:
            # Apply OSBs to all four observables BEFORE measurement model
            P1c = P1 - _code_osb_1
            P2c = P5 - _code_osb_2
            lam1 = LAMBDA_E1; lam2 = LAMBDA_E5A
            L1m  = L1 * lam1 - _phase_osb_1
            L2m  = L5 * lam2 - _phase_osb_2
    else:
        # Missing OSB — run raw; no AR for this satellite
        P1c = P1; P2c = P5
        lam1 = LAMBDA_E1; lam2 = LAMBDA_E5A
        L1m  = L1 * lam1; L2m = L5 * lam2
        no_AR = True
        ar_skip_reason = 'no_osb'

    if sid not in _osb_dbg_printed:
        _osb_dbg_printed.add(sid)
        _osb_status = (f"code=({_code_osb_1:+.4f},{_code_osb_2:+.4f})m "
                       f"phase=({_phase_osb_1:+.6f},{_phase_osb_2:+.6f})m applied=True"
                       if _osb_complete else "applied=False(missing)")
        print(f"{sid}: signals={_gal_sig_str}, osb={_osb_status}, used=True")
        print(f"  clock_type: COD0MGXFIN IF-frame (OSB {'applied' if _osb_complete else 'MISSING — running raw'})")
        print(f"[OSB_APPLY] sat={sid} signals={_gal_sig_str} "
              f"code_osb=({_code_osb_1},{_code_osb_2}) "
              f"phase_osb=({_phase_osb_1},{_phase_osb_2}) "
              f"applied={_osb_complete}")
    gamma = FE1SQ / FE5SQ
    L1m_tmp=L1*LAMBDA_E1; L5m_tmp=L5*LAMBDA_E5A
    phi_WL=(FREQ_E1*L1m_tmp-FREQ_E5A*L5m_tmp)/(FREQ_E1-FREQ_E5A)
    P_NL=(FREQ_E1*P1c+FREQ_E5A*P2c)/(FREQ_E1+FREQ_E5A)
    # Bug A fix: subtract phase WL satellite bias from Galileo MW combination.
    _b_wl_gal = 0.0
    if _osb_complete and _phase_osb_1 is not None and _phase_osb_2 is not None:
        _b_wl_gal = (FREQ_E1 * _phase_osb_1 - FREQ_E5A * _phase_osb_2) / ((FREQ_E1 - FREQ_E5A) * LAMBDA_WL_E)
    MW_cyc=(phi_WL-P_NL)/LAMBDA_WL_E - _b_wl_gal
    GF_m=L1*LAMBDA_E1-L5*LAMBDA_E5A

    xyz0,_=_spc(sp3t,sp,sc,sid,tow)
    if xyz0 is None: return None
    lat_r,lon_r,_=_lla(rxyz)
    er=np.array([math.cos(lat_r)*math.cos(lon_r),
                 math.cos(lat_r)*math.sin(lon_r),
                 math.sin(lat_r)])
    ra=rxyz+ah*er+_rpco(recx,lat_r,lon_r)
    if blq and tow_total>0.:
        ra=ra+_otl_disp(blq,sta,tow_total,lat_r,lon_r)
    # Pole tide displacement (optional — only when erp dict is provided)
    if erp and tow_total>0.:
        xp_as, yp_as = _erp_interp(erp, tow_total)
        ra = ra + _ptl_disp(lat_r, lon_r, xp_as, yp_as)

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
    mh,mw=(map_func or _gmf)(lat0,doy,el)
    return dict(sid=sid,unit=unit,mh=mh,mw=mw,el=el,az=az,
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
                # Bug C fix: add metadata missing from Galileo return dict
                code1_type=code1_type,
                phase1_type=phase1_type,
                code2_type=code2_type,
                phase2_type=phase2_type,
                use_for_ar=(not no_AR),
                _fallback_used=False)

def _rp(m,dT,ZWD):
    return (m['rng']-m['scm']-m['dtrel']+dT
            +m['trop_zhd']+m['mw']*ZWD
            +m['shp']+m['setm']+m['pcv_sat']+m['pcv_rec'])


# ==============================================================================
#  PPP Kalman filter pass
# ==============================================================================
def _ppp_pass(epochs,sp3t,sp,sc,clkd,osb,ah,nom,iclk,izwd,lat0,doy,zhd,tref,
              satx,att,recx,elm=math.radians(15.),SC=0.25,SP=0.035,  # v121 PART2: SP 0.015→0.035
              direction=1,label="FWD",wl_init=None,amb_init=None,
              constellation='GE',blq=None,sta='IISC',map_func=None,erp=None,
              enable_subdaily_absorber=False,
              pos_clk_q_scale=1.0,
              clk_q_scale=1.0,
              clk_perturb_m=0.0,
              obs_whitening=False,
              ar1_phi=0.95,
              elev_weight_exp=1.0,
              zwd_freeze_sod=None,
              arc_ramp_n=0,
              arc_ramp_init_scale=4.0,
              ion_q_scale=1.0,
              exclude_sats=None,
              resid_censor=None,
              resid_censor_win=None,
              resid_censor_freq=None,
              clk_leak_beta=0.0,
              clk_leak_win=None,
              zwd_hump_boost=None,
              el_phase_boost_wins=None,
              iono_mode='base',
              iono_hump_wins=None,
              phase_elev_exp=None):
    """
    constellation    : 'G' | 'E' | 'GE'
    blq              : dict from parse_blq (ocean tide loading)
    sta              : 4-char station code used to look up BLQ entry
    map_func         : mapping function override (None = _gmf, or pass _nmf)
    erp              : ERP dict from parse_erp for pole tide; None/empty = no ptide
    pos_clk_q_scale  : v94 Run-H — multiplier for position+clock Q only (default 1.0)
    clk_q_scale      : v96 CLK_MECH H2 — multiplier for clock Q[3,3] ONLY.
                       Position Q[0:3] untouched.  Isolates clock process noise
                       from position noise.  Tested at {0.1, 0.3, 1.0, 3.0}.
    clk_perturb_m    : v94 Run-J — constant bias added to all innovations (default 0.0)
    obs_whitening    : Run-K — AR(1) pre-whiten code innovations to remove
                       temporally correlated orbit/clock residual noise.
    ar1_phi          : Run-K — AR(1) coefficient for obs_whitening (default 0.95).
                       Tested at phi ∈ {0.90, 0.95, 0.98} for hump sensitivity.
    zwd_freeze_sod   : TEST_ZWD_FREEZE — if set, Q[4,4] (ZWD process noise) is
                       forced to 0 for all epochs with sod >= zwd_freeze_sod.
                       All other filter states, measurement model, and ambiguity
                       logic are completely unchanged.  Default None = no freeze.
    """
    REF=np.array([1337935.5599,6070317.2377,1427877.5071])
    global _osb_dbg_printed, _sat_signal_map, _cp_debug, _osb_once, _nproc_global
    _osb_dbg_printed = set()
    _sat_signal_map  = {}  # v70: reset signal lock at start of each pass
    _cp_debug        = {}  # v81: per-sat L1m-P1c history (OSB consistency debug)
    _osb_once        = set()  # v81: [OSB_VAL] printed-once guard
    _nproc_global    = 0
    # ── DIAG: reset per-pass diagnostic accumulators ──────────────────────
    global _diag_prev_sig_pair, _diag_sig_switch_cnt
    global _diag_res_gps_phase, _diag_res_gal_phase
    global _diag_res_gps_code,  _diag_res_gal_code
    global _diag_lp1_sat
    _diag_prev_sig_pair  = {}
    _diag_sig_switch_cnt = {}
    _diag_res_gps_phase  = []
    _diag_res_gal_phase  = []
    _diag_res_gps_code   = []
    _diag_res_gal_code   = []
    _diag_lp1_sat        = {}
    # ── end DIAG reset ────────────────────────────────────────────────────
    # [OSB_AUDIT] OSB is loaded by parse_bia() in postpos() and passed as the
    # `osb` dict parameter to _ppp_pass.  It is applied at the raw-observable
    # level inside _proc() / _proc_gal() BEFORE any IF combination is formed.
    # DO NOT call init_osb_csv() here — that function does not exist.
    wl_fixed=dict(wl_init) if wl_init else {}
    _amb_init=dict(amb_init) if amb_init else {}
    _amb_seeded=set()
    nl_fixed={}

    # ── v54 parameters ────────────────────────────────────────────────────────
    # NL/WL fixing thresholds PRESERVED but NL fixing is DISABLED in v54
    # until Phase 2 validation passes (RAW float convergence confirmed).
    NL_RATIO_THRESH   = 4.5
    NL_VAR_THRESH     = (0.1)**2     # v58: strict gate — was (10.0)² (allowed fixing with huge uncertainty)
    NL_RES_THRESH     = 0.02             # v86 PART 2: tightened from 0.03 — must be within 2% of integer
    NL_EXCL_THRESH    = 0.25
    NL_INNOV_GATE     = 0.200   # v89: tightened 0.500→0.200 — 0.5 was allowing wrong-integer pseudoobs
    NL_RELEASE_THRESH = 0.050   # v89: tightened 0.080→0.050 — stops integer flips (N1 cycling 162/163)
    NL_PHASE_THRESH   = 0.010
    NL_MIN_SATS       = 3            # v62: lowered from 7 — apply NL once ≥3 sats fixed
    NL_MIN_OBS        = 8
    PHASE_RES_GATE    = 0.020   # v89: tightened 0.030→0.020 m — inflates Rd earlier on phase outliers
    ZWD_PRIOR         = 0.12
    ZWD_PRIOR_SIGMA   = 0.13   # v124: relaxed 0.03→0.13 — 3cm was over-constraining ZWD, coupling into vertical
    ZWD_CLAMP         = 0.015
    GF_CODE_SCALE     = 0.15  # v123: 0.4→0.15; GPS GF σ: 1.02m→0.55m at 36°, Galileo: 0.56m→0.31m
                              # code_pct diagnostic was 0.37%; target >2% for meaningful iono constraint
    GF_GATE           = 5.0   # outlier rejection gate for GF pseudo-measurement (sigma units)
    # ── v_RDCB PART 0/1: GF disable flag ─────────────────────────────────────
    # GF = P2c − P1c is a linear combination of the P1 and P2 rows already in H
    # (H[ki]=1 for P1, H[ki]=γ for P2) → adds zero independent information.
    # Setting USE_GF=False removes GF from z/H/R entirely; RDCB absorbs the
    # inter-frequency code bias that GF was partly trying to constrain.
    USE_GF            = False  # PART 1: disable GF pseudo-measurement
    _zwd_prev         = None
    _zwd_smooth       = 0.12   # v117 FIX3: EMA of ZWD used as adaptive anchor (α=0.005 ≈ τ~10min)
    _clk_prev         = None   # v115 FIX2: previous clock value for derivative stabilization
    _corr_up_buf: list = []    # v116 FIX5: rolling window for corr(Up, ZWD) and corr(Up, CLK)
    _corr_zwd_buf: list = []
    _corr_clk_buf: list = []
    _CORR_WIN = 300            # ~2.5h at 30s epochs

    # ── v54 RAW state vector ──────────────────────────────────────────────────
    # Global:  [x(0), y(1), z(2), dt(3), ZWD(4), ISB_GAL(5), Gn(6), Ge(7)]
    # Per-sat: [I_s(8+3k), N1_s(9+3k), N2_s(10+3k)]  for satellite k
    # Single receiver clock; ISB_GAL absorbs GPS/Galileo inter-system offset
    IDX_CLK = 3; IDX_ZWD = 4
    x=np.zeros(5); x[IDX_CLK]=iclk; x[IDX_ZWD]=izwd
    P=np.zeros((5,5))
    P[0,0]=P[1,1]=P[2,2]=100.**2
    P[IDX_CLK,IDX_CLK]=3000.**2
    P[IDX_ZWD,IDX_ZWD]=0.5**2

    # v110: weak height constraint anchor — applied only after 2 h convergence
    _h_anchor_ecef = None   # ECEF reference position for Up constraint
    _H_HEIGHT = None        # pre-built 1×n_st H row (updated when n_st grows)
    _H_HEIGHT_nst = 0       # n_st for which _H_HEIGHT was built
    _HEIGHT_R = (0.10)**2   # v120 FIX4a: tightened 0.20→0.10 m — soft Up anchor (was too weak at 20cm)
    _sod_start = None       # SOD of first processed epoch (for 2-h convergence gate)

    # ── v93 Run-F: subdaily loading absorber (optional extra state) ───────────
    _e_up_sda = np.zeros(3)
    if enable_subdaily_absorber:
        _lat_sda, _lon_sda, _ = _lla(nom)
        _e_up_sda = _enu(_lat_sda, _lon_sda)[2, :]
        x = np.append(x, [0.0])
        _n_sda = len(x)
        P_sda = np.zeros((_n_sda, _n_sda)); P_sda[:_n_sda-1, :_n_sda-1] = P
        P_sda[_n_sda-1, _n_sda-1] = (0.05) ** 2
        P = P_sda

    # ── ISB_GAL state (inter-system bias, Galileo w.r.t. GPS) ─────────────────
    IDX_ISB_GAL = len(x)
    x = np.append(x, [0.0])
    _Pn_isb = np.zeros((len(x), len(x))); _Pn_isb[:len(x)-1, :len(x)-1] = P
    _Pn_isb[IDX_ISB_GAL, IDX_ISB_GAL] = 0.3**2
    P = _Pn_isb

    # ── v111: Troposphere gradient states Gn (north), Ge (east) ──────────────
    IDX_Gn = len(x)
    IDX_Ge = IDX_Gn + 1
    x = np.append(x, [0.0, 0.0])
    _Pn2 = np.zeros((len(x), len(x))); _Pn2[:len(x)-2, :len(x)-2] = P
    _Pn2[IDX_Gn, IDX_Gn] = 0.01**2   # (10 mm)^2
    _Pn2[IDX_Ge, IDX_Ge] = 0.01**2
    P = _Pn2

    # IF model: no RDCB state (bias cancels in IF combination)
    IDX_RDCB_G = None  # placeholder for compat

    # sidx maps sat_id → N_IF ambiguity state index (one state per satellite in IF model)
    sidx={}; namb=0; phi={}; wum={}; prev_mw={}; prev_gf={}
    # IF model: WL dicts are vestigial (no WL fixing).  Initialised here so the
    # cycle-slip branch (which references them for diagnostic counting) never NameErrors.
    _wl_history       = {}   # sid → WL integer history (unused in IF model)
    _wl_history_ptrace= {}   # sid → ptrace list        (unused in IF model)
    _wl_fix_count     = {}   # sid → number of times WL was fixed (unused in IF model)
    _wl_rel_count     = {}   # sid → number of times WL was released (unused in IF model)
    mw_hist=defaultdict(list)
    results={}; psod=None; nproc=0
    ep_idx=0
    d3=float('nan'); pos=nom.copy(); dx=np.zeros(3)  # safe defaults
    _amb_conv_sods=set(); _amb_init_ptrace={}
    _sat_age=defaultdict(int); _amb_snapshots={}
    _sat_last_sod={}
    # [REACQ_FIX] Galileo re-acquisition handling constants and state dict.
    # _reacq_state[sid] = sod at which re-acquisition was detected.
    REACQ_GAP      = 1800.0   # s — gap longer than this triggers re-acquisition handling
    REACQ_COOLDOWN = 900.0    # s — phase ramp duration after re-acquisition
    REACQ_AMB_FREEZE = 300.0  # s — ambiguity columns zeroed (no amb update) for first 300 s
    _reacq_state = {}          # sid → sod_of_reacquisition (Galileo only)
    # v120: EMA fractional bias workaround REMOVED — proper OSB now applied in _proc/_proc_gal.
    # [REACQ_FIX end]
    # v59: per-satellite L1m-P1c consistency history (deque, maxlen=100)
    _lp1_hist=defaultdict(lambda: deque(maxlen=100))

    # ── [A1] end data structures ────────────────────────────────────────────


    # Run-K: always compute ECEF Up-unit vector for per-sat LOS·Up projection
    _lat_k, _lon_k, _ = _lla(nom)
    _e_up_ecef = _enu(_lat_k, _lon_k)[2, :]   # Up row of ENU rotation (ECEF 3-vector)

    _AR1_PHI = float(ar1_phi)  # kept for compat


    # IF model: no iono diagnostics (legacy iono removed)
    _iono_prev_I_meas = {}          # kept as empty dict (referenced in dead code paths)
    _iono_dI_adapt    = defaultdict(float)
    _iono_prev_state_adapt = {}

    # v54: Phase 2 debug — track L1m−P1c and L2m−P2c for one reference sat
    _DBG_SAT   = None       # will be set to first GPS sat seen
    _dbg_lp1   = []         # (sod, L1m-P1c) history
    _dbg_lp2   = []         # (sod, L2m-P2c) history
    _DBG_PRINT_INTERVAL = 120   # print summary every N epochs

    eplist=epochs if direction==1 else list(reversed(epochs))

    # ── end accumulators ──────────────────────────────────────────────────────
    # v78 PART 7 — debug counters (reset each pass)
    # v79 PART 6 — per-epoch NL skip counters (reset each epoch inside loop)

    for epoch in eplist:
        sod=epoch['t']; sobs=epoch['sats']
        wl_fixed.clear()   # IF model: WL always empty
        dt=abs(sod-psod) if psod is not None else 30.
        if dt<=0 or dt>3600: dt=30.
        psod=sod; tow=_sod2t(sod,tref)
        if _sod_start is None:
            _sod_start = sod
        # GPS total seconds passed to OTL (tow from _sod2t is already GPS total-s)
        tow_total=tow

        n_st=len(x); Q=np.zeros((n_st,n_st))
        # v94 Run-H: pos_clk_q_scale multiplies ONLY position+clock Q.
        # ZWD Q[IDX_ZWD], iono Q, and ambiguity Q are completely untouched.
        Q[0,0]=Q[1,1]=Q[2,2]=1e-8*dt*pos_clk_q_scale
        # v121 FIX: clock Q reduced 25x — (0.01)^2 -> (2e-3)^2 per epoch
        # Old Q~3e-3 m^2/epoch exceeded constraint R=2.5e-3, making it ineffective.
        # v124: clock Q = 1e-5*dt
        Q[IDX_CLK,IDX_CLK]=1e-5*dt
        Q[IDX_ISB_GAL,IDX_ISB_GAL]=(1e-3)**2*dt  # ISB near-constant (tightened from 0.01)
        # FIX4: ISB_PROCESS_NOISE constrained to 1e-6 m²/s — ISB must be cm-level.
        # If ISB was ~1.5 m this forces the filter to reveal the true bias source.
        ISB_PROCESS_NOISE = 1e-6
        Q[IDX_ISB_GAL,IDX_ISB_GAL] = ISB_PROCESS_NOISE * dt
        # ZWD process noise: baseline (no ×1.5 scaling).
        # ZWD_Q_SCALE: Run-L scalar multiplier — only ZWD Q is affected.
        Q[IDX_ZWD,IDX_ZWD]=1.25e-9*dt*ZWD_Q_SCALE*0.3   # v120 FIX1: tightened ×1.5→×0.3 — large Q let ZWD absorb position bias (corr(Up,ZWD)≈0.9)
        # v111: ISB_GAL near-constant random walk — tightened to quasi-static
        # v111: troposphere gradient random walks (1 mm/epoch each)
        Q[IDX_Gn, IDX_Gn] = (0.001)**2 * dt
        Q[IDX_Ge, IDX_Ge] = (0.001)**2 * dt
        # IF model: no RDCB process noise
        # v102: per-hump ZWD process noise boost (ONLY inside detected hump windows)
        if zwd_hump_boost:
            for (_zhb_s, _zhb_e, _zhb_k) in zwd_hump_boost:
                if _zhb_s <= sod <= _zhb_e:
                    Q[IDX_ZWD,IDX_ZWD] *= _zhb_k
                    break
        # TEST_ZWD_FREEZE: hold ZWD random walk at zero after convergence.
        # Only ZWD Q is affected; all other states are completely unchanged.
        if zwd_freeze_sod is not None and sod >= zwd_freeze_sod:
            Q[IDX_ZWD,IDX_ZWD] = 0.0
        # v93 Run-F: subdaily absorber random-walk process noise
        if enable_subdaily_absorber:
            Q[8, 8] = _SDA_Q_NOISE * dt   # subdaily absorber state (appended after gradients)
        # v54 RAW per-satellite state noise:
        #   I (ionosphere): small process noise to allow slow drift
        #   N1, N2 (ambiguities): zero process noise (carrier phase constants)
        # v56 FIX 1 (CRITICAL): q_iono increased 100× for equatorial station IISC.
        # IISC is inside the EIA; L1 STEC varies 20–50 m per pass.
        # With 1e-6/s the ionosphere froze after ~50 epochs (P[ki,ki]→0.02 m²);
        # position absorbed the unfrozen residual.  1e-4/s gives σ_I_ss≈0.34 m,
        # enough bandwidth to track equatorial rate ≈ 0.05–0.5 m/epoch.
        # For extreme scintillation epochs, raise to ION_PROC_NOISE=1e-3.
        # IF model: no iono state; N_IF is constant (zero process noise)
        for sid_k, ki in sidx.items():
            Q[ki, ki] = 0.0
        P += Q
        for _sid_f, _ki_f in sidx.items():
            P[_ki_f, _ki_f] = max(P[_ki_f, _ki_f], 1e-6)

        rxyz=nom+x[:3]; sun=_sun(tow); geom=[]
        _ep_code_info_acc = 0.0; _ep_phase_info_acc = 0.0

        for sid,so in sorted(sobs.items()):
            if sid[0] not in ('G','E'): continue
            if sid[0] not in constellation: continue
            if exclude_sats and sid in exclude_sats: continue

            # [REACQ_FIX] Detect Galileo re-acquisition and expire finished cooldowns.
            if sid[0] == 'E':
                _prev_sod_ra = _sat_last_sod.get(sid)
                if (_prev_sod_ra is not None
                        and (sod - _prev_sod_ra) > REACQ_GAP
                        and sid not in _reacq_state):      # guard: fire ONCE per gap
                    _reacq_state[sid] = sod
                    print(f"[REACQ] sat={sid} gap={sod - _prev_sod_ra:.0f}s")
                    # TASK 4: hard reset N_IF ambiguity — DO NOT reuse previous state
                    if sid in sidx:
                        _ki_ra = sidx[sid]
                        x[_ki_ra] = 0.0
                        P[_ki_ra, _ki_ra] = (100.0) ** 2
                        print(f"[AMB_RESET] sat={sid}")
                elif sid in _reacq_state:
                    _elapsed_ra = sod - _reacq_state[sid]
                    if _elapsed_ra > REACQ_COOLDOWN:
                        del _reacq_state[sid]
                        print(f"[REACQ_END] sat={sid}")
            # [REACQ_FIX end]
            if sid[0]=='E':
                m=_proc_gal(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,
                             satx,att,recx,blq=blq,sta=sta,tow_total=tow_total,
                             map_func=map_func,erp=erp)
            else:
                m=_proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,
                         satx,att,recx,blq=blq,sta=sta,tow_total=tow_total,
                         map_func=map_func,erp=erp)
            if m is None: continue

            # ── CYCLE SLIP ───────────────────────────────────────────────────
            slip=False
            if sid in prev_mw:
                dGF=m['GF_m']-prev_gf[sid]; dMW=m['MW_cyc']-prev_mw[sid]
                if abs(dGF)>0.05 or abs(dMW)>1.5:
                    if sid in _amb_seeded:
                        _amb_seeded.discard(sid)
                    else:
                        slip=True
                        mw_hist[sid].clear()   # IF: no wl_fixed to pop
                        # ── Step 5: WL release counter ────────────────────────
                        if sid in _wl_fix_count:   # was previously fixed
                            _wl_rel_count[sid] = _wl_rel_count.get(sid, 0) + 1
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
            prev_mw[sid]=m['MW_cyc']; prev_gf[sid]=m['GF_m']
            _sat_last_sod[sid]=sod       # always update after detection
            m['_slip'] = slip            # OSB_IF_AUDIT: carry slip flag into geom for state reset

            geom.append(m)

        if len(geom)<4: continue
        if len(geom)>4:
            if _pdop(geom)>6.0:
                worst=min(geom,key=lambda m:m['el'])
                geom=[m for m in geom if m['sid']!=worst['sid']]
        if len(geom)<4: continue

        # v111 FIX: mean coupling REMOVED — iono is line-of-sight dependent,
        # coupling high-el (low STEC) to low-el (high STEC) corrupts estimates

        # ══════════════════════════════════════════════════════════════════════
        # OSB → IF DATA-FLOW BLOCK  [RAW → OSB → IF → FILTER]
        # ──────────────────────────────────────────────────────────────────────
        # PURPOSE:  (a) register new satellites in the state vector,
        #           (b) form P_IF / L_IF from the OSB-CORRECTED observables
        #               P1c, P2c, L1m, L2m that were set in _proc/_proc_gal.
        #
        # OSB was applied BEFORE this point:
        #   _proc      L2238-2241:  P1c=P1-b_C1; P2c=P2-b_C2
        #                           L1m=L1*λ1-b_L1; L2m=L2*λ2-b_L2
        #   _proc_gal  L2381-2385:  identical for Galileo E1/E5a signals
        #
        # IF combination uses ONLY corrected signals (CORRECT pipeline):
        #   GPS:     P_IF = ALFA   * P1c  - BETA   * P2c   [OSB-corrected]
        #            L_IF = ALFA   * L1m  - BETA   * L2m   [OSB-corrected]
        #   Galileo: P_IF = ALFA_E * P1c  - BETA_E * P2c   [OSB-corrected]
        #            L_IF = ALFA_E * L1m  - BETA_E * L2m   [OSB-corrected]
        #
        # WRONG (NOT done here):
        #   P_IF = ALFA * P1  - BETA * P2   (raw — no OSB)
        #   L_IF = ALFA * L1m - BETA * L2m  (mixed corrected/uncorrected)
        # ──────────────────────────────────────────────────────────────────────
        # Per-epoch accumulator for Part-6 OSB_IF_CHECK validation
        _osb_if_check_buf = {}   # sid → (P_IF_residual,)

        # IF centering accumulators — filled inside satellite loop below.
        # Each entry: (sid, P_IF_raw, geometric_range)
        # P_IF_raw = α·P1c − β·P2c  (OSB-corrected, pre-filter)
        # geometric_range = m['rng']  (satellite–receiver distance |sv−rx|)
        _if_raw_buf = []   # (sid, P_IF_raw, geom_range)

        for m in geom:
            sid = m['sid']
            is_gal = (sid[0] == 'E')

            # ── (a) State-vector extension for new / slipped satellites ──────
            if sid not in sidx or m.get('_slip', False):
                if sid not in sidx:
                    # Completely new satellite: extend x and P by one N_IF state
                    n_now = len(x)
                    x = np.append(x, [0.0])            # N_IF initialised to 0
                    P_ext = np.zeros((n_now + 1, n_now + 1))
                    P_ext[:n_now, :n_now] = P
                    P_ext[n_now, n_now] = (300.0) ** 2  # large initial variance
                    P = P_ext
                    sidx[sid] = n_now
                    # OSB_IF_AUDIT: new satellite registered
                    # print(f"[STATE_NEW] sat={sid}  ki={n_now}  SOD={sod:.0f}")
                else:
                    # Existing satellite with cycle slip: inflate ambiguity only
                    ki_slip = sidx[sid]
                    P[ki_slip, ki_slip] = (300.0) ** 2
                    # OSB_IF_AUDIT: cycle slip — ambiguity reset
                    # print(f"[STATE_SLIP] sat={sid}  ki={ki_slip}  SOD={sod:.0f}")

            ki = sidx[sid]
            m['ki'] = ki            # state index for this satellite's N_IF

            # ── (b) IF combination from OSB-corrected observables ────────────
            # OSB applied at line ~2238 (_proc) / ~2381 (_proc_gal):
            #   P1c = P1 - code_osb_1   P2c = P2 - code_osb_2
            #   L1m = L1*λ1 - phase_osb_1   L2m = L2*λ2 - phase_osb_2
            # OSB line numbers confirmed: _proc L2238-L2241, _proc_gal L2381-L2385
            if is_gal:
                # Galileo E1 + E5a  (ALFA_E, BETA_E, LAMBDA_IF_E)
                P_IF = ALFA_E * m['P1c'] - BETA_E * m['P2c']  # OSB applied at L2381-L2382
                L_IF = ALFA_E * m['L1m'] - BETA_E * m['L2m']  # OSB applied at L2384-L2385
                lam_IF = LAMBDA_IF_E
            else:
                # GPS L1 + L2  (ALFA, BETA, LAMBDA_IF)
                P_IF = ALFA * m['P1c'] - BETA * m['P2c']       # OSB applied at L2238-L2239
                L_IF = ALFA * m['L1m'] - BETA * m['L2m']       # OSB applied at L2240-L2241
                lam_IF = LAMBDA_IF

            m['P_IF']   = P_IF    # IF code observable  — formed from OSB-corrected P1c/P2c
            m['L_IF']   = L_IF    # IF phase observable — formed from OSB-corrected L1m/L2m
            m['lam_IF'] = lam_IF  # IF wavelength for ambiguity column in H matrix

            # IF_CENTER_RAW accumulator: raw IF combo + geometric range (pre-filter, pre-model)
            _if_raw_buf.append((sid, P_IF, m['rng']))

        # ── Sync n_st after state extensions ─────────────────────────────────
        # (needed so H = np.zeros((n_obs, nst)) below uses the updated size)
        # n_st is re-read as len(x) in the next line of code.
        # ══════════════════════════════════════════════════════════════════════

        if nproc==0:
            # IF: bootstrap clock from P_IF  (P_IF now guaranteed to exist — set above)
            _gps_g=[m for m in geom if m['sid'][0]=='G']
            _clk_src=_gps_g if _gps_g else geom
            clk_est=float(np.clip(
                np.mean([m['P_IF']-_rp(m,0.,x[IDX_ZWD]) for m in _clk_src]),
                -3e6,3e6))
            x[IDX_CLK]=clk_est
            for m in geom:
                ki=m['ki']
                rp0=_rp(m,x[IDX_CLK],x[IDX_ZWD])
                _isb0=x[IDX_ISB_GAL] if m['sid'][0]=='E' else 0.0
                x[ki]=(m['L_IF']-rp0-_isb0)/m['lam_IF']

        # ── IF measurement model: 2 obs per sat (P_IF, L_IF)
        ns=len(geom); nst=len(x)
        n_wl=0; n_nl_cur=0; n_nl=0
        n_obs=2*ns

        H=np.zeros((n_obs,nst))
        z=np.zeros(n_obs); Rd=np.zeros(n_obs)
        xs=x.copy()

        # v115 FIX5: per-epoch accumulators for geometry diagnostic
        _dbg_el_G=[]; _dbg_el_E=[]
        _dbg_rdph_G=[]; _dbg_rdph_E=[]


        # IF sigma model: sin(el)^2 scaling (stability fix)
        # sigma_code_IF = 0.85 / sin(el)^2   [range 0.7-1.0 m]
        # sigma_phase_IF = 0.015 / sin(el)^2  [range 0.01-0.02 m]
        for ri,m in enumerate(geom):
            ki=m['ki']; sid=m['sid']
            u=m['unit']; mw=m['mw']
            is_gal=(sid[0]=='E')
            rp=_rp(m,xs[IDX_CLK],xs[IDX_ZWD])
            N_IF_s=xs[ki]
            isb_s=xs[IDX_ISB_GAL] if is_gal else 0.0
            lam_IF_s=m['lam_IF']
            _el_m=m['el']; _az_m=m.get('az',0.0)
            _sel=max(math.sin(_el_m),0.05); _cel=math.cos(_el_m)
            cot_el=_cel/_sel
            _h_gn=cot_el*math.cos(_az_m); _h_ge=cot_el*math.sin(_az_m)
            grad_s=xs[IDX_Gn]*_h_gn+xs[IDX_Ge]*_h_ge
            base=2*ri; _sin_el=max(math.sin(_el_m),0.2)
            # PART 1+2: clean IF sigma — no _cst, no _ra, no clip/floor
            # TASK 2: code sigma = 0.85/sin^2 (no legacy scaling)
            _sigma_code_IF  = 0.85  / (_sin_el ** 2)
            # TASK 1: phase sigma with hard floor 0.02 m (DO NOT allow < 0.02 m)
            _sigma_phase_IF = max(0.02, 0.015 / (_sin_el ** 2))

            # P_IF row (code, no ambiguity) — sin^2 elevation weighting
            r=base
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]
            H[r,IDX_CLK]=1.; H[r,IDX_ZWD]=mw
            H[r,IDX_ISB_GAL]=1. if is_gal else 0.
            H[r,IDX_Gn]=_h_gn; H[r,IDX_Ge]=_h_ge
            z[r]=m['P_IF']-(rp+isb_s+grad_s)
            Rd[r] = _sigma_code_IF ** 2

            # L_IF row (phase + N_IF ambiguity) — sin^2 elevation weighting
            r=base+1
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]
            H[r,IDX_CLK]=1.; H[r,IDX_ZWD]=mw
            H[r,IDX_ISB_GAL]=1. if is_gal else 0.
            H[r,IDX_Gn]=_h_gn; H[r,IDX_Ge]=_h_ge
            H[r,ki]=lam_IF_s
            z[r]=m['L_IF']-(rp+isb_s+lam_IF_s*N_IF_s+grad_s)
            if is_gal and sid in _reacq_state:
                _era=sod-_reacq_state[sid]
                if _era<=REACQ_AMB_FREEZE: H[base+1,ki]=0.0
            Rd[r] = _sigma_phase_IF ** 2
            if sid[0]=='G': _dbg_el_G.append(math.degrees(_el_m)); _dbg_rdph_G.append(Rd[r])
            else:            _dbg_el_E.append(math.degrees(_el_m)); _dbg_rdph_E.append(Rd[r])
            _a1_code_suspect_i=False

            # IF model: Galileo scale already applied inside measurement loop

            # IF model: Fisher info for P_IF/L_IF (code=base, phase=base+1)
            if Rd[base]   > 0: _ep_code_info_acc  += 1.0 / Rd[base]    # P_IF
            if Rd[base+1] > 0: _ep_phase_info_acc += 1.0 / Rd[base+1]  # L_IF

            # v115 FIX5: geometry diagnostics already accumulated in IF loop above
            _el_deg_dbg = math.degrees(m['el'])
            # IF model: debug accumulators already filled in measurement loop above
            pass  # _dbg_el_G/_dbg_el_E/_dbg_rdph filled in inner loop

            # IF model: per-frequency phase outlier rejection removed (L_IF combines L1+L2)

            # v93 Run-F: subdaily vertical absorber
            if enable_subdaily_absorber:
                _h_vl = -float(np.dot(u, _e_up_sda))
                _sda_col = IDX_Gn - 1
                for _vl_r in range(base, base + 2):   # IF: 2 rows
                    H[_vl_r, _sda_col] = _h_vl

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
            # IF model: L1m-P1c range downweight removed (no iono state tracking)

            # v105: elevation-dependent weighting is now ALWAYS applied (sin_el^2 for phase,
            # sin_el^1 for code) via phase_elev_exp=2.0. No time-window conditioning needed.

        # PART 3: CODE_USAGE_IF — verify code rows == phase rows every 300 epochs
        if nproc % 300 == 0 and geom:
            _n_code_rows_if  = len(geom)
            _n_phase_rows_if = len(geom)
            print(f"[CODE_USAGE_IF] SOD={sod:.0f}  "
                  f"code_rows={_n_code_rows_if}  phase_rows={_n_phase_rows_if}  "
                  f"match={'YES' if _n_code_rows_if == _n_phase_rows_if else 'NO — MISMATCH'}")

        # v94 Run-J: satellite clock perturbation discriminator.
        # Adds clk_perturb_m to ALL observation innovations (code + phase).
        # This is equivalent to shifting all satellite clocks uniformly.
        # The receiver clock state (x[3]) absorbs the common-mode in the
        # next update; any residual Up-hump shift reveals clock-position coupling.
        # ZWD prior is NOT perturbed (added after this block).
        if clk_perturb_m != 0.0:
            z[:n_obs] -= clk_perturb_m   # shift all obs innovations uniformly

        # IF model: no AR(1) whitening

        # IF model: prefit stats (stride 2)
        _phase_z_prefit=[float(z[2*i+1]) for i in range(len(geom))]
        _code_z_prefit =[float(z[2*i])   for i in range(len(geom))]
        _prefit_phase_rms_ep=(math.sqrt(float(np.mean(np.array(_phase_z_prefit)**2)))
            if _phase_z_prefit else 0.0)
        _prefit_code_rms_ep=(math.sqrt(float(np.mean(np.array(_code_z_prefit)**2)))
            if _code_z_prefit else 0.0)

        # PART 4: CODE_WEIGHT_CHECK — mean |z_code| / mean |z_phase| ratio
        _abs_code_ep  = [abs(v) for v in _code_z_prefit]
        _abs_phase_ep = [abs(v) for v in _phase_z_prefit]
        _mean_abs_code  = float(np.mean(_abs_code_ep))  if _abs_code_ep  else 0.0
        _mean_abs_phase = float(np.mean(_abs_phase_ep)) if _abs_phase_ep else 0.0
        if _mean_abs_phase > 1e-12:
            _cw_ratio = _mean_abs_code / _mean_abs_phase
        else:
            _cw_ratio = float('nan')
        if nproc % 300 == 0 and geom:
            _cw_flag = ''
            if not math.isnan(_cw_ratio):
                if   _cw_ratio < 0.01: _cw_flag = '  [WARNING: code ignored — ratio << 0.01]'
                elif _cw_ratio > 10.0: _cw_flag = '  [WARNING: code dominating — ratio >> 10]'
                else:                  _cw_flag = '  [OK: 0.01 < ratio < 10]'
            print(f"[CODE_WEIGHT_CHECK] SOD={sod:.0f}  "
                  f"mean|z_code|={_mean_abs_code:.3f}m  "
                  f"mean|z_phase|={_mean_abs_phase:.4f}m  "
                  f"ratio={_cw_ratio:.3f}{_cw_flag}")
        _phase_prefit_per_sat={}
        for _ri_pf,_m_pf in enumerate(geom):
            _phase_prefit_per_sat[_m_pf['sid']]=(
                float(z[2*_ri_pf+1]),0.0,
                float(math.degrees(_m_pf['el'])),
                float(math.degrees(_m_pf.get('az',0.))),)

        # ── IF CENTERING DIAGNOSTICS (every 300 epochs) ─────────────────────────
        # TASK 5: old [IF_CENTER] renamed → [IF_RESIDUAL_MEAN].
        #   What it measures: mean prefit code innovation after subtracting the
        #   full modelled range (ρ + clock + trop + ISB + gradient + corrections).
        #   This is NOT an IF centering check — it includes clock errors,
        #   troposphere bias, ISB, and all model corrections.  Use it only to
        #   monitor overall filter residual health, not IF combination quality.
        if nproc % 300 == 0 and _code_z_prefit:
            _pif_mean_log = float(np.mean(_code_z_prefit))
            print(f"[IF_RESIDUAL_MEAN] mean={_pif_mean_log*1e3:+.1f}mm")

        # TASKS 2 + 3 + 4 + 6: TRUE IF CENTERING — computed pre-filter, pre-model.
        #
        #   [IF_CENTER_RAW]   P_IF_raw = α·P1c − β·P2c  (OSB-corrected, no model)
        #     Expected: ~20 000 km (satellite range dominates) — absolute value
        #     is meaningless; watch std across satellites for consistency.
        #
        #   [IF_CENTER_GEOM]  P_IF_raw − geometric_range (|sv−rx|)
        #     Removes the dominant geometry term.  Residual contains:
        #       clock + trop + relativistic + PCO/PCV + biases
        #     Expected: roughly constant across satellites at each epoch.
        #     IF OSB + combination are correct this should NOT vary per satellite
        #     beyond the clock (common-mode) and troposphere (elevation-dependent).
        #
        #   [IF_CENTER_SAT]   per-satellite P_IF_geom snapshot.
        #     Outlier satellites here → OSB mismatch, signal mismatch, or
        #     wrong IF combination coefficients for that satellite.
        #     Global bias (all sats shifted together) → clock absorbs it (normal).
        if nproc % 300 == 0 and _if_raw_buf:
            _raw_vals  = [v[1]        for v in _if_raw_buf]   # P_IF_raw (m)
            _geom_vals = [v[1] - v[2] for v in _if_raw_buf]   # P_IF_raw − rng (m)
            _raw_mean  = float(np.mean(_raw_vals))
            _raw_std   = float(np.std(_raw_vals)) if len(_raw_vals) > 1 else 0.0
            _geom_mean = float(np.mean(_geom_vals))
            print(f"[IF_CENTER_RAW]  mean={_raw_mean/1e3:.3f}km  "
                  f"std={_raw_std:.3f}m  nsat={len(_raw_vals)}")
            print(f"[IF_CENTER_GEOM] mean={_geom_mean:.3f}m  "
                  f"(clock+trop+corrections absorbed; per-sat offset = OSB/signal issue)")
            for _sid_c, _pif_c, _rng_c in _if_raw_buf:
                _val_c = _pif_c - _rng_c
                print(f"[IF_CENTER_SAT]  sat={_sid_c}  mean={_val_c:.3f}m")

        # TASK 5: phase collapse guard — if mean|z_phase| < 0.05 m inflate phase ×2 (var ×4)
        if _mean_abs_phase > 1e-12 and _mean_abs_phase < 0.05:
            for _ri_pcg in range(len(geom)):
                Rd[2*_ri_pcg+1] *= 4.0  # sigma×2 → variance×4

        # TASK 3: code/phase balance enforcement — prevent regime switching
        if not math.isnan(_cw_ratio):
            if _cw_ratio > 10.0:
                # code dominating: inflate phase variance (sigma×1.5 → var×2.25)
                for _ri_bl in range(len(geom)):
                    Rd[2*_ri_bl+1] *= 2.25
            elif _cw_ratio < 0.05:
                # phase dominating: inflate code variance (sigma×1.5 → var×2.25)
                for _ri_bl in range(len(geom)):
                    Rd[2*_ri_bl] *= 2.25
        if nproc % 300 == 0 and geom:
            print(f"[BALANCE] ratio={_cw_ratio:.3f}")

        # IF model: no code rejection gate (IF combination handles outliers)


        # v99 RESID_CENSOR: rank satellites by prefit phase norm each epoch.        # Censor (inflate Rd) or downweight worst satellite(s) before KF update.
        # Only active within resid_censor_win (sod_lo, sod_hi); None = always.
        # Geometry guard: skip if remaining sats < min threshold.
        if resid_censor and len(geom) > 0:
            _in_win = (resid_censor_win is None or
                       resid_censor_win[0] <= sod <= resid_censor_win[1])
            if _in_win:
                _min_s = (6 if constellation == 'G' else
                          5 if constellation == 'E' else 8)
                _norms = []
                for _ri_c,_m_c in enumerate(geom):
                    _b_c=2*_ri_c; _n_c=abs(float(z[_b_c+1]))
                    _norms.append((_n_c,_ri_c,_m_c['sid']))
                _norms.sort(reverse=True)
                if resid_censor == 'A':
                    _to_censor = _norms[:1] if len(geom)-1 >= _min_s else []
                elif resid_censor == 'B':
                    _n_c2 = min(2, len(geom) - _min_s)
                    _to_censor = _norms[:max(0, _n_c2)]
                elif resid_censor == 'C':
                    _to_censor = _norms[:1] if len(geom) >= _min_s else []
                else:
                    _to_censor = []
                for _n_c, _ri_c, _sid_c in _to_censor:
                    _b_c=2*_ri_c
                    if resid_censor in ('A','B'): Rd[_b_c+1]=1e4
                    else: Rd[_b_c+1]*=4.0
                    if resid_censor_freq is not None:
                        resid_censor_freq[_sid_c] = resid_censor_freq.get(_sid_c, 0) + 1

        # TASK 8: constellation normalization REMOVED — legacy Rd scaling caused
        # code/phase ratio instability; clean IF weighting via sigma model only.

        # ZWD soft anchor pseudo-obs — v120 FIX3: FIXED climatological anchor (ZWD_PRIOR=0.12m).
        # v117 used _zwd_smooth (EMA of x[ZWD]): in steady state z≈0 → ZERO corrective force.
        # A fixed anchor at the climatological model has real restoring power when ZWD drifts.
        n_total=n_obs
        # ── GF pseudo-measurement rows — v_RDCB PART 1: controlled by USE_GF ──
        # GF = P2c − P1c is a linear combination of the P1 and P2 rows already in H;
        # it adds zero independent information and is now disabled (USE_GF=False).
        # All variables below are retained for GF_DEBUG print compatibility.
        # IF: no GF pseudo-measurement
        _gf_H_rows=[]; _gf_z_rows=[]; _gf_R_diag=[]
        _gf_rejected_count=0; _gf_used_count=0
        # ── end GF row building ────────────────────────────────────────────────

        # Combine: main obs (n_obs rows) + ZWD prior (1 row) + GF rows (0 when USE_GF=False)
        _n_extra = 1 + _gf_used_count          # ZWD prior + GF (GF=0 when disabled)
        H_p  = np.zeros((n_total + _n_extra, nst))
        z_p  = np.zeros(n_total + _n_extra)
        Rd_p = np.zeros(n_total + _n_extra)
        H_p[:n_total, :] = H
        z_p[:n_total]    = z
        Rd_p[:n_total]   = Rd
        # ZWD prior row
        H_p[n_total, IDX_ZWD] = 1.
        z_p[n_total]  = ZWD_PRIOR - xs[IDX_ZWD]   # v120 FIX3: fixed 0.12m anchor
        Rd_p[n_total] = ZWD_PRIOR_SIGMA**2
        # GF rows appended after ZWD prior — only when USE_GF=True (currently disabled)
        if USE_GF:
            for _gf_i, (_gf_hrow, _gf_zval, _gf_rval) in enumerate(
                    zip(_gf_H_rows, _gf_z_rows, _gf_R_diag)):
                _row_idx = n_total + 1 + _gf_i
                H_p[_row_idx, :]  = _gf_hrow
                z_p[_row_idx]     = _gf_zval
                Rd_p[_row_idx]    = _gf_rval

        _I_before_update = {}  # IF: no iono state

        _I_meas_ep = {}  # IF: removed

        # ── ADAPTIVE IONO Q: keep _iono_prev_I_meas for diagnostics only ───────
        # v123 FIX4: dI_adapt now uses state-based dI (updated AFTER filter below).
        # ── end ───────────────────────────────────────────────────────────────

        zwd_before=x[IDX_ZWD]
        # Dimension assertions — catch state-size bugs immediately
        assert H_p.shape == (n_total + _n_extra, nst), \
            f'H_p shape mismatch: {H_p.shape} vs ({n_total+_n_extra}, {nst})'
        assert z_p.shape == (n_total + _n_extra,), \
            f'z_p shape: {z_p.shape}'
        assert Rd_p.shape == (n_total + _n_extra,), \
            f'Rd_p shape: {Rd_p.shape}'
        # v58 Fix 5: regularise R to prevent near-singular inversion (NaN propagation)
        # Single combined filter_standard: main obs + ZWD prior + GF rows (Part 5 fix)
        R_main = np.diag(Rd_p); R_main += np.eye(len(R_main)) * 1e-6
        _x_pre_pos = x[:3].copy()
        if filter_standard(x, P, H_p.T, z_p, R_main) != 0: continue
        # Compute position result, error vector, and 3D error
        pos = nom + x[:3]
        dx  = pos - REF
        d3  = float(np.linalg.norm(dx)) * 1e3   # 3D error in mm
        nproc += 1
        _nproc_global = nproc
        _dx_up_mm = float(np.dot(x[:3] - _x_pre_pos, _e_up_ecef)) * 1e3

        # IF: no iono adaptive Q or variance cap
        # IF: N_IF covariance not capped (50m² cap was legacy iono)


        # v111: height constraint — applied after 2 h convergence as Up pseudo-obs
        if _sod_start is not None and (sod - _sod_start) >= 5400.:   # v120 FIX4b: 1.5h gate (was 3h) — ZWD constraint allows earlier Up anchor
            if _h_anchor_ecef is None:
                _h_anchor_ecef = nom + x[:3].copy()
            _n_st_now = len(x)
            if _H_HEIGHT is None or _H_HEIGHT_nst != _n_st_now:
                _H_HEIGHT = np.zeros((1, _n_st_now))
                _H_HEIGHT[0, 0] = _e_up_ecef[0]
                _H_HEIGHT[0, 1] = _e_up_ecef[1]
                _H_HEIGHT[0, 2] = _e_up_ecef[2]
                _H_HEIGHT_nst = _n_st_now
            _z_height = np.array([float(np.dot(_h_anchor_ecef - (nom + x[:3]), _e_up_ecef))])
            _R_height  = np.array([[_HEIGHT_R]])
            filter_standard(x, P, _H_HEIGHT.T, _z_height, _R_height)

        # TASK 7: tightened clock constraint — sigma=0.03 m, hard clip >0.1 m/epoch
        if _clk_prev is not None:
            # Hard clip: do not allow >0.1 m jump before constraint
            _clk_raw = float(x[IDX_CLK])
            if abs(_clk_raw - _clk_prev) > 0.1:
                x[IDX_CLK] = _clk_prev + math.copysign(0.1, _clk_raw - _clk_prev)
            _H_clk = np.zeros((1, len(x))); _H_clk[0, IDX_CLK] = 1.0
            _z_clk = np.array([_clk_prev - x[IDX_CLK]])
            _R_clk = np.array([[0.03**2]])  # TASK 7: sigma_clk = 0.03 m
            filter_standard(x, P, _H_clk.T, _z_clk, _R_clk)   # .T -> shape (nx,1)
            if nproc % 300 == 0:
                print(f"[CLK_CONSTRAINT] prev={_clk_prev:.3f} curr={x[IDX_CLK]:.3f} "
                      f"diff={x[IDX_CLK]-_clk_prev:.3f}")
        _clk_prev = float(x[IDX_CLK])

        if nproc % 300 == 0:
            _clk_isb_sum = x[IDX_CLK] + x[IDX_ISB_GAL]
            print(f"[CLK] dt={x[IDX_CLK]:+.3f}  ISB={x[IDX_ISB_GAL]:+.3f}  "
                  f"dt+ISB={_clk_isb_sum:+.3f}  Gn={x[IDX_Gn]*1e3:+.1f}mm  Ge={x[IDX_Ge]*1e3:+.1f}mm")
            pass  # IF: no RDCB state

        # IF model: N_IF sigma summary every 300 epochs (lightweight)
        if sidx and nproc % 300 == 0:
            _sig_vals_if = [math.sqrt(max(0.0,P[ki,ki]))*LAMBDA_IF for ki in sidx.values()]
            _mean_sig_if = float(np.mean(_sig_vals_if)) if _sig_vals_if else 0.0
            print(f"[N_IF_SIGMA] SOD={sod:.0f}  mean={_mean_sig_if*1e3:.1f}mm  n={len(_sig_vals_if)}")

        # ZWD per-epoch clamp (unchanged from v53)
        if _zwd_prev is not None and abs(x[IDX_ZWD]-_zwd_prev)>ZWD_CLAMP:
            x[IDX_ZWD]=_zwd_prev+math.copysign(ZWD_CLAMP, x[IDX_ZWD]-_zwd_prev)
            P[IDX_ZWD,IDX_ZWD]=max(P[IDX_ZWD,IDX_ZWD], (ZWD_CLAMP/3.0)**2)
        _zwd_prev=x[IDX_ZWD]
        _zwd_smooth = 0.005*x[IDX_ZWD] + 0.995*_zwd_smooth  # v117 FIX3: EMA τ≈200 epochs (~100 min at 30s)


        # [IONO VAR] / [GPS_SUMMARY] → removed (debug data in CSVs)

        # IF model: no iono state recording

        # v58 Fix 2: NaN protection — if the state vector went non-finite after
        # the float update, release all NL fixes and inflate the covariance so
        # the filter can recover rather than propagating NaN indefinitely.
        if not np.isfinite(x).all() or not np.isfinite(P).all():
            x = np.where(np.isfinite(x), x, 0.0)
            P = np.where(np.isfinite(P), P, 0.0)
            np.fill_diagonal(P, np.maximum(np.diag(P), 100.**2))
            P *= 100.
            print(f"[NaN GUARD] SOD={sod:.0f} — non-finite state detected; "
                  f"inflated covariance")

        # Compute phase residuals immediately after float update so NL gate
        # can use current-epoch phase_rms_now (not stale from previous epoch).
        # IF: postfit L_IF residuals
        phase_res_now=[]
        for m in geom:
            ki=m['ki']; rp=_rp(m,x[IDX_CLK],x[IDX_ZWD])
            _isb_ph=x[IDX_ISB_GAL] if m['sid'][0]=='E' else 0.0
            phase_res_now.append(m['L_IF']-(rp+_isb_ph+m['lam_IF']*x[ki]))
        phase_rms_now=math.sqrt(np.mean(np.array(phase_res_now)**2)) if phase_res_now else 999.

        # ── DIAG: accumulate per-constellation mean residuals ─────────────
        for _ri_dr, _m_dr in enumerate(geom):
            _res_ph = phase_res_now[_ri_dr]
            _isb_diag = x[IDX_ISB_GAL] if _m_dr['sid'][0] == 'E' else 0.0
            _res_co = _m_dr['P_IF'] - (_rp(_m_dr, x[IDX_CLK], x[IDX_ZWD]) + _isb_diag)
            if _m_dr['sid'][0] == 'G':
                _diag_res_gps_phase.append(_res_ph)
                _diag_res_gps_code.append(_res_co)
            else:
                _diag_res_gal_phase.append(_res_ph)
                _diag_res_gal_code.append(_res_co)

        # [OSB_IF_CHECK] removed — threshold 0.3 m invalid for IF (iono+noise >> 0.3 m);
        # OSB validated externally.  See [IF_CHECK] below for residual monitoring.


        if not hasattr(_ppp_pass,'_zwd_buf'): pass  # per-call buffer via list
        # (reuse existing _zwd_prev / ZWD_CLAMP logic above; watchdog below)
        _zwd_buf=getattr(_ppp_pass,'_zwd_buf_'+label,[])
        _zwd_buf.append(x[IDX_ZWD])
        if len(_zwd_buf)>5: _zwd_buf.pop(0)
        setattr(_ppp_pass,'_zwd_buf_'+label,_zwd_buf)
        # [ZWD WATCHDOG] — disabled (threshold 0.015 m triggers on valid ZWD evolution;
        # re-enable and raise limit to 0.10 m if needed after stability confirmed)
        # ZWD_RATE_LIMIT=0.015
        # if len(_zwd_buf)==5 and (max(_zwd_buf)-min(_zwd_buf))>ZWD_RATE_LIMIT:
        #     print(f"[ZWD WATCHDOG] SOD={sod:.0f} range={max(_zwd_buf)-min(_zwd_buf):.3f}m")
        #     P[IDX_ZWD,IDX_ZWD]=max(P[IDX_ZWD,IDX_ZWD],(0.15)**2)

        if nproc > 0 and nproc % 300 == 0 and geom:
            _icp=[abs(m['P_IF']-(_rp(m,x[IDX_CLK],x[IDX_ZWD])+(x[IDX_ISB_GAL] if m['sid'][0]=='E' else 0.0))) for m in geom]
            _ilp=[abs(m['L_IF']-(_rp(m,x[IDX_CLK],x[IDX_ZWD])+(x[IDX_ISB_GAL] if m['sid'][0]=='E' else 0.0)+m['lam_IF']*x[m['ki']])) for m in geom]
            print(f"[IF_CHECK] SOD={sod:.0f}  "
                  f"P_IF_mean={float(np.mean(_icp))*1e3:+.1f}mm  P_IF_RMS={math.sqrt(float(np.mean(np.array(_icp)**2)))*1e3:.1f}mm  "
                  f"L_IF_mean={float(np.mean(_ilp))*1e3:+.1f}mm  L_IF_RMS={math.sqrt(float(np.mean(np.array(_ilp)**2)))*1e3:.1f}mm  nsat={len(geom)}")

        if nproc % 300 == 0 and sidx:
            _nl_count_dbg = 0  # IF: no NL fixing
            _sig_vals_dbg=[math.sqrt(max(0.0,P[ki,ki]))*LAMBDA_IF for ki in sidx.values()]
            _mean_sig_dbg = float(np.mean(_sig_vals_dbg)) if _sig_vals_dbg else 0.0
            _max_sig_dbg  = float(max(_sig_vals_dbg))     if _sig_vals_dbg else 0.0
            # PART 2 — clean SUMMARY console print every 300 epochs
            print(f"[SUMMARY] SOD={sod:.0f}  sats={len(geom)}"
                  f"  NL=0  WL=0  3D={d3:.1f}mm")
            # single clock + ISB diagnostic
            if 'E' in constellation:
                _clk_isb_sum2 = x[IDX_CLK] + x[IDX_ISB_GAL]
                print(f"[CLK] SOD={sod:.0f}  dt={x[IDX_CLK]:+.4f} m  "
                      f"ISB={x[IDX_ISB_GAL]:+.4f} m  "
                      f"dt+ISB={_clk_isb_sum2:+.4f} m  "
                      f"Gn={x[IDX_Gn]*1e3:+.1f}mm  Ge={x[IDX_Ge]*1e3:+.1f}mm")
                # per-constellation postfit phase RMS
                if len(phase_res_now) != len(geom):
                    print(f"[WARN] phase_res_now({len(phase_res_now)}) != geom({len(geom)})")
                _pres_G = [pr for pr, m in zip(phase_res_now, geom) if m['sid'][0]=='G']
                _pres_E = [pr for pr, m in zip(phase_res_now, geom) if m['sid'][0]=='E']
                _prms_G = math.sqrt(np.mean(np.array(_pres_G)**2))*1e3 if _pres_G else float('nan')
                _prms_E = math.sqrt(np.mean(np.array(_pres_E)**2))*1e3 if _pres_E else float('nan')
                print(f"[RES]  phase_RMS: GPS={_prms_G:.1f}mm  GAL={_prms_E:.1f}mm")
            # v115 FIX5: geometry + weighting diagnostic (all constellations)
            _mean_el_G  = float(np.mean(_dbg_el_G))  if _dbg_el_G  else float('nan')
            _mean_el_E  = float(np.mean(_dbg_el_E))  if _dbg_el_E  else float('nan')
            _mean_ph_G  = float(np.mean(_dbg_rdph_G))*1e6 if _dbg_rdph_G else float('nan')
            _mean_ph_E  = float(np.mean(_dbg_rdph_E))*1e6 if _dbg_rdph_E else float('nan')
            print(f"[GEOM] nG={len(_dbg_el_G)}  nE={len(_dbg_el_E)}  "
                  f"mean_el: GPS={_mean_el_G:.1f}°  GAL={_mean_el_E:.1f}°  "
                  f"mean_Rd_phase: GPS={_mean_ph_G:.2f}mm²  GAL={_mean_ph_E:.2f}mm²  "
                  f"dt={x[IDX_CLK]:+.3f}m")
            if getattr(_ppp_pass, '_up_err_window', None):
                print(f"[UP_STABILITY] mean_up={float(np.mean(_ppp_pass._up_err_window)):.3f}")
            # v116 FIX5: Up correlation diagnostics
            if len(_corr_up_buf) >= 30:
                _ua = np.array(_corr_up_buf); _za = np.array(_corr_zwd_buf); _ca = np.array(_corr_clk_buf)
                def _corr(a, b):
                    da = a - a.mean(); db = b - b.mean()
                    den = (np.std(a) * np.std(b))
                    return float(np.dot(da, db) / (len(a) * den)) if den > 1e-12 else float('nan')
                print(f"[CORR] corr(Up,ZWD)={_corr(_ua,_za):+.3f}  corr(Up,CLK)={_corr(_ua,_ca):+.3f}")
            # v120 FIX5: ZWD and Up variance diagnostic — check coupling
            _sig_zwd_mm = math.sqrt(max(0., P[IDX_ZWD, IDX_ZWD])) * 1e3
            _sig_up_mm  = math.sqrt(max(0., float(np.dot(_e_up_ecef,
                          P[:3,:3].dot(_e_up_ecef))))) * 1e3
            print(f"[VAR_DIAG] ZWD={x[IDX_ZWD]*1e3:+.1f}mm(σ={_sig_zwd_mm:.1f}mm)  "
                  f"Up_sigma={_sig_up_mm:.1f}mm  "
                  f"ZWD_prior_pull={(_zwd_smooth-x[IDX_ZWD])*1e3:+.1f}mm")
            _diag_res_gal_phase  = _diag_res_gal_phase[-6000:]
            _diag_res_gps_code   = _diag_res_gps_code[-6000:]
            _diag_res_gal_code   = _diag_res_gal_code[-6000:]
        _amb_snapshots[sod]={sid2:(x[ki2],P[ki2,ki2])
                             for sid2,ki2 in sidx.items() if phi.get(sid2,False)}
        if direction==1:
            if not hasattr(_rts_store,'_data'): _rts_store._data=[]
            _rts_store._data.append((sod,x.copy(),P.copy()))

        phase_rms=phase_rms_now*1e3
        ZHD=zhd; ZWD=x[IDX_ZWD]; TROPO=ZHD+ZWD

        # ── end constellation-split ───────────────────────────────────────────

        results[sod]={'xyz':pos.copy(),'dx':dx.copy(),'p_trace':P[0,0]+P[1,1]+P[2,2],
                      'n':len(geom),'ztd':TROPO,'wl_fixed':len(wl_fixed),
                      'nl_fixed':n_nl,
                      'phase_rms':phase_rms,
                      'zhd':ZHD,'zwd':ZWD,
                      # mean wet mapping function this epoch — diagnostic read-only
                      'mw_mean':float(np.mean([m['mw'] for m in geom])) if geom else 0.,
                      'sats_used':sorted([m['sid'] for m in geom]),
                      'sats_wl':sorted([s for s in wl_fixed if any(m['sid']==s for m in geom)]),
                      'sats_nl':sorted([s for s in nl_fixed if any(m['sid']==s for m in geom)]),
                      # v93: clock state (m) and subdaily absorber state for G/F audits
                      'clk':float(x[IDX_CLK]),
                      'v_load':0.0,
                      # v94 Run-I: prefit phase innovation RMS for spectral audit
                      'prefit_phase_rms': _prefit_phase_rms_ep,
                      # v104: per-satellite iono state snapshot for time-series plot
                      'iono_states': {m['sid']: float(x[m['ki']]) for m in geom},
                      # Early/late hump audit fields
                      'p_clk_var':        float(P[IDX_CLK,IDX_CLK]),
                      'p_zwd_var':        float(P[IDX_ZWD,IDX_ZWD]),
                      'prefit_code_rms':  _prefit_code_rms_ep,
                      'dx_up_mm':         _dx_up_mm,
                      'phase_prefit_per_sat': _phase_prefit_per_sat}

        # epoch complete

        # IF model: no iono state tracking

        ep_idx = nproc  # sync with nproc

        # console progress: first 3 epochs (start confirmation)
        if nproc <= 3:
            n_gps=sum(1 for m in geom if m['sid'][0]=='G')
            n_gal=sum(1 for m in geom if m['sid'][0]=='E')
            print(f"  [{label}] SOD={sod:6.0f}  N={len(geom):2d}(G{n_gps}+E{n_gal})"
                  f"  3D={d3:8.1f}mm  NL=0")

    # IF model: store (N_IF, P_NIF) per satellite for inheritance
    fwd_amb={sid:(x[ki],0.,0.,P[ki,ki],0.,0.)
             for sid,ki in sidx.items() if phi.get(sid,False)}
    fwd_amb_out = fwd_amb  # IF: all sats inherit
    return results,nom+x[:3],x[IDX_CLK],x[IDX_ZWD],{},fwd_amb_out,_amb_snapshots


# ==============================================================================
#  Auto-detect hump windows from Up residual — diagnostic only
# ==============================================================================
def _detect_hump_windows(sods, u_mm, conv_sod=7200.,
                          smooth_w=20,
                          merge_gap=30,
                          min_dur=40,
                          thr_sigma=1.2,
                          min_amp_mm=8.):
    """Detect hump windows from Up residual.

    Parameters
    ----------
    smooth_w    : boxcar width in epochs (~10 min at 30 s)
    merge_gap   : merge adjacent runs separated by <= this many epochs
    min_dur     : discard runs shorter than this many epochs
    thr_sigma   : elevation threshold = 35th-pct +/- thr_sigma * MAD_equiv
    min_amp_mm  : absolute floor on amplitude (mm)

    Returns
    -------
    List of hump dicts sorted by start_sod, followed by a trailing
    ('_meta', baseline_mm, mad_mm) sentinel for the caller.
    Each hump dict contains:
      start_sod, end_sod, peak_sod, amplitude_mm, width_h,
      start_h, end_h, peak_h, sign (+1 positive / -1 negative)
    """
    NAN = float('nan')
    mask = np.isfinite(u_mm) & (sods >= conv_sod)
    if mask.sum() < 60:
        return []
    s = sods[mask].copy()
    u = u_mm[mask].copy()
    n = len(u)

    # 1. Causal boxcar smooth (no future look-ahead)
    w = max(3, min(smooth_w, n // 4))
    kernel = np.ones(w) / float(w)
    u_full = np.convolve(u, kernel, mode='full')   # length n + w - 1
    u_sm   = u_full[:n]                            # causal prefix
    # shift back by lag to centre the filter
    lag = w // 2
    u_sm = np.roll(u_sm, -lag)
    u_sm[n - lag:] = u[n - lag:]                   # fill tail with raw

    # 2. Robust baseline (35th percentile) and MAD threshold
    baseline = float(np.percentile(u_sm, 35))
    dev      = u_sm - baseline
    mad      = float(np.median(np.abs(dev))) * 1.4826   # ~sigma equiv
    mad      = max(mad, 2.0)                             # 2 mm floor

    thr_pos = max(baseline + thr_sigma * mad,  baseline + min_amp_mm)
    thr_neg = min(baseline - thr_sigma * mad,  baseline - min_amp_mm)

    # 3. Binary elevation flag
    elev = (u_sm > thr_pos) | (u_sm < thr_neg)

    # 4. Contiguous runs
    runs = []
    i = 0
    while i < n:
        if elev[i]:
            j = i
            while j < n and elev[j]:
                j += 1
            runs.append([i, j - 1])
            i = j
        else:
            i += 1

    # 5. Merge adjacent runs separated by <= merge_gap epochs
    if len(runs) > 1:
        merged = [runs[0]]
        for r in runs[1:]:
            if r[0] - merged[-1][1] <= merge_gap:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        runs = merged

    # 6. Filter by minimum duration
    runs = [r for r in runs if r[1] - r[0] + 1 >= min_dur]

    # 7. Extract properties
    humps = []
    for a, b in runs:
        seg_u = u_sm[a:b + 1]
        seg_s = s[a:b + 1]
        pos_dev = float(seg_u.max() - baseline)
        neg_dev = float(baseline - seg_u.min())
        if pos_dev >= neg_dev:
            pk_idx = int(np.argmax(seg_u))
            sign   = +1
            amp    = pos_dev
        else:
            pk_idx = int(np.argmin(seg_u))
            sign   = -1
            amp    = -neg_dev
        humps.append({
            'start_sod':    float(seg_s[0]),
            'end_sod':      float(seg_s[-1]),
            'peak_sod':     float(seg_s[pk_idx]),
            'amplitude_mm': amp,
            'width_h':      float(seg_s[-1] - seg_s[0]) / 3600.,
            'start_h':      float(seg_s[0])  / 3600.,
            'end_h':        float(seg_s[-1]) / 3600.,
            'peak_h':       float(seg_s[pk_idx]) / 3600.,
            'sign':         sign,
        })

    humps.append(('_meta', baseline, mad))
    return humps


# ==============================================================================
#  Constellation-separated hump attribution — diagnostic only, no solver changes
# ==============================================================================
def _hump_attribution_audit(all_fwd, all_rts, REF):
    """
    Auto-detects hump windows from Up residual per constellation mode.
    No hardcoded window times.
    Reports:
      - detected hump start/end/peak/amplitude/width per mode
      - FWD-RTS residual hump per detected window
      - ZWD<->Up Pearson r per detected window and overall
      - dominant spectral periods (FFT)
      - ISB leakage audit (GPS+Galileo): excursion alignment and ISB<->Up r
      - combined vs individual hump comparison
      - legacy 5-8h / 17-20h window misplacement check
      - decision: constellation-specific / ISB-driven / common cause
    """
    NAN = float('nan')
    CONV = 7200.   # 2 h convergence discard

    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)

    # Legacy hardcoded windows — used ONLY for the misplacement check
    _LEGACY = {
        'H1': (18000., 28800., '5-8h'),
        'H2': (61200., 72000., '17-20h'),
    }

    # ── helpers ───────────────────────────────────────────────────────────────
    def _pearson(a, b):
        valid = np.isfinite(a) & np.isfinite(b)
        if valid.sum() < 5: return NAN
        a2, b2 = a[valid], b[valid]
        sa, sb = float(np.std(a2)), float(np.std(b2))
        if sa < 1e-12 or sb < 1e-12: return NAN
        return float(np.corrcoef(a2, b2)[0, 1])

    def _win_mean(arr, sods, lo, hi):
        m = (sods >= lo) & (sods <= hi) & np.isfinite(arr)
        return float(np.mean(arr[m])) if m.sum() > 0 else NAN

    def _dominant_periods(u_mm, sods, dt_s=30.):
        mask = (sods >= CONV) & np.isfinite(u_mm)
        if mask.sum() < 120: return NAN, NAN
        y = u_mm[mask] - float(np.mean(u_mm[mask]))
        N = len(y)
        amp  = np.abs(np.fft.rfft(y)); amp[0] = 0.
        freq = np.fft.rfftfreq(N, d=dt_s)
        i1   = int(np.argmax(amp))
        p1   = 1. / (freq[i1] * 3600.) if freq[i1] > 0 else NAN
        amp2 = amp.copy(); amp2[max(0, i1 - 3):i1 + 4] = 0.
        i2   = int(np.argmax(amp2))
        p2   = 1. / (freq[i2] * 3600.) if freq[i2] > 0 else NAN
        return p1, p2

    def _extract(fwd, rts):
        if not fwd: return None
        ss  = np.array(sorted(fwd.keys()), dtype=float)
        u_f = np.array([(Re @ fwd[k]['dx'])[2] * 1e3 for k in ss])
        zwd = np.array([fwd[k].get('zwd', NAN) for k in ss])
        isb = np.array([fwd[k].get('isb_proxy_mm', NAN) for k in ss])
        u_r = np.full(len(ss), NAN)
        for i, k in enumerate(ss):
            if rts and k in rts:
                u_r[i] = float((Re @ rts[k]['dx'])[2]) * 1e3
            else:
                u_r[i] = u_f[i]
        return dict(sods=ss, u_f=u_f, u_r=u_r, zwd=zwd, isb=isb, frd=u_f - u_r)

    def _fmt(v, unit='mm', d=1):
        return f"{v:+.{d}f}{unit}" if v == v else "     N/A"

    # ── detect humps per mode ─────────────────────────────────────────────────
    mode_order = [('GPS-only', 'G'), ('Galileo-only', 'E'), ('GPS+Galileo', 'GE')]
    mode_data  = {}
    mode_humps = {}   # label -> list of hump dicts (meta sentinel stripped)
    mode_meta  = {}   # label -> (baseline_mm, mad_mm)

    for ml, _ in mode_order:
        d = _extract(all_fwd.get(ml, {}), all_rts.get(ml, {}))
        mode_data[ml] = d
        if d is None:
            mode_humps[ml] = []; mode_meta[ml] = (NAN, NAN); continue
        raw = _detect_hump_windows(d['sods'], d['u_f'], conv_sod=CONV)
        if raw and isinstance(raw[-1], tuple) and raw[-1][0] == '_meta':
            _, bl, md = raw[-1]
            mode_meta[ml]  = (bl, md)
            mode_humps[ml] = raw[:-1]
        else:
            mode_meta[ml]  = (NAN, NAN)
            mode_humps[ml] = [r for r in raw if not isinstance(r, tuple)]

    sep  = '=' * 72
    sep2 = '-' * 72

    print(f"\n{sep}")
    print("[HUMP_ATTR] Constellation-Separated Hump Attribution  (auto-detected windows)")
    print(sep)

    # ── per-mode report ───────────────────────────────────────────────────────
    for ml, _ in mode_order:
        d   = mode_data.get(ml)
        hs  = mode_humps.get(ml, [])
        bl, md = mode_meta.get(ml, (NAN, NAN))
        if d is None:
            print(f"\n  -- {ml} -- no data"); continue

        conv_mask = d['sods'] >= CONV
        u_rms_pc  = float(np.sqrt(np.mean(d['u_f'][conv_mask] ** 2))) \
                    if conv_mask.sum() > 0 else NAN
        p1h, p2h = _dominant_periods(d['u_f'], d['sods'])
        p1s = f"{p1h:.1f}h" if p1h == p1h else "N/A"
        p2s = f"{p2h:.1f}h" if p2h == p2h else "N/A"

        print(f"\n  -- {ml} --")
        print(f"     Up RMS post-conv      : {_fmt(u_rms_pc)}")
        print(f"     Baseline / MAD        : {bl:+.1f}mm / {md:.1f}mm"
              if bl == bl else "     Baseline / MAD        : N/A")
        print(f"     Dominant periods      : {p1s}  {p2s}")
        print(f"     Detected humps        : {len(hs)}")

        if not hs:
            print("       (none above threshold)")
        else:
            for hi, h in enumerate(hs, 1):
                lo_s, hi_s = h['start_sod'], h['end_sod']
                sgn_str = "POS" if h['sign'] > 0 else "NEG"
                print(f"\n       Hump {hi} [{sgn_str}]  "
                      f"{h['start_h']:.2f}h - {h['end_h']:.2f}h  "
                      f"(peak {h['peak_h']:.2f}h, width {h['width_h']:.2f}h)")
                print(f"         amplitude       : {_fmt(h['amplitude_mm'])}")

                # FWD-RTS amp in this window
                frd_win = _win_mean(d['frd'], d['sods'], lo_s, hi_s)
                # quiet baseline: mean FWD-RTS in non-hump epochs
                frd_quiet_mask = conv_mask.copy()
                for hh in hs:
                    frd_quiet_mask &= ~((d['sods'] >= hh['start_sod']) &
                                        (d['sods'] <= hh['end_sod']))
                frd_bl = (float(np.mean(d['frd'][frd_quiet_mask]))
                          if frd_quiet_mask.sum() > 0 else NAN)
                frd_amp = (frd_win - frd_bl) if (frd_win == frd_win and frd_bl == frd_bl) else NAN
                print(f"         FWD-RTS amp     : {_fmt(frd_amp)}")

                # ZWD<->Up r in this window
                mask_win = (d['sods'] >= lo_s) & (d['sods'] <= hi_s)
                r_zwd_win = _pearson(d['zwd'][mask_win], d['u_f'][mask_win])
                print(f"         ZWD<->Up r      : {_fmt(r_zwd_win,'',3)}")

        # Overall ZWD<->Up
        r_all = _pearson(d['zwd'][conv_mask], d['u_f'][conv_mask])
        print(f"\n     ZWD<->Up r (all post-conv) : {_fmt(r_all,'',3)}")

    # ── legacy window misplacement check (GPS+Galileo) ────────────────────────
    print(f"\n{sep2}")
    print("  Legacy window alignment check (GPS+Galileo):")
    hs_ge = mode_humps.get('GPS+Galileo', [])
    for tag, (lc_lo, lc_hi, lc_str) in _LEGACY.items():
        if not hs_ge:
            print(f"     {tag} ({lc_str}): no humps detected -- utility unknown"); continue
        best_ovlp = 0.; best_h = None
        for h in hs_ge:
            ovlp = max(0., min(h['end_sod'], lc_hi) - max(h['start_sod'], lc_lo))
            if ovlp > best_ovlp:
                best_ovlp = ovlp; best_h = h
        if best_h is None or best_ovlp < 1800.:
            print(f"     {tag} ({lc_str}): NO overlap with any detected hump -- MISPLACED")
            continue
        lead  = (best_h['start_sod'] - lc_lo) / 3600.
        trail = (lc_hi - best_h['end_sod'])   / 3600.
        peak_in = lc_lo <= best_h['peak_sod'] <= lc_hi
        aligned = abs(lead) < 0.5 and abs(trail) < 0.5 and peak_in
        print(f"     {tag} ({lc_str}): {'ALIGNED' if aligned else 'PARTIALLY MISPLACED'}")
        print(f"       detected hump : {best_h['start_h']:.2f}h - {best_h['end_h']:.2f}h"
              f"  (peak {best_h['peak_h']:.2f}h, amp {best_h['amplitude_mm']:+.1f}mm)")
        if lead < -0.25:
            print(f"       hump starts {abs(lead):.2f}h BEFORE legacy window -- misses early rise")
        if trail < -0.25:
            print(f"       hump extends {abs(trail):.2f}h PAST legacy window -- misses tail")
        if not peak_in:
            print(f"       peak at {best_h['peak_h']:.2f}h is OUTSIDE legacy "
                  f"[{lc_lo/3600.:.0f}-{lc_hi/3600.:.0f}h]")

    # ── ISB leakage audit (GPS+Galileo, auto-detected windows) ───────────────
    print(f"\n{sep2}")
    print("  ISB Leakage Audit (GPS+Galileo, auto-detected windows)")
    dGE = mode_data.get('GPS+Galileo')
    r_isb_u_all = NAN
    if dGE is not None and hs_ge:
        sods_ge = dGE['sods']
        u_ge    = dGE['u_f']
        isb_ge  = dGE['isb']
        conv_mask_ge = sods_ge >= CONV

        isb_valid   = isb_ge[conv_mask_ge & np.isfinite(isb_ge)]
        isb_mean_pc = float(np.mean(isb_valid)) if len(isb_valid) > 0 else NAN
        isb_std_pc  = float(np.std(isb_valid))  if len(isb_valid) > 0 else NAN
        isb_mad     = (float(np.median(np.abs(isb_valid - isb_mean_pc))) * 1.4826
                       if len(isb_valid) > 0 else NAN)
        r_isb_u_all = _pearson(isb_ge[conv_mask_ge], u_ge[conv_mask_ge])

        # quiet-epoch baseline ISB (non-hump post-conv epochs)
        hump_union_ge = np.zeros(len(sods_ge), dtype=bool)
        for h in hs_ge:
            hump_union_ge |= ((sods_ge >= h['start_sod']) &
                               (sods_ge <= h['end_sod']))
        quiet_mask_ge = conv_mask_ge & ~hump_union_ge & np.isfinite(isb_ge)
        isb_bl_val = (float(np.mean(isb_ge[quiet_mask_ge]))
                      if quiet_mask_ge.sum() > 0 else NAN)

        print(f"     ISB proxy = GPS_mean_res - GAL_mean_res (postfit code, mm)")
        print(f"     ISB proxy mean +/- std (post-conv) : {isb_mean_pc:+.2f} +/- {isb_std_pc:.2f} mm")
        print(f"     ISB proxy quiet-epoch baseline     : {_fmt(isb_bl_val)}")
        print(f"     ISB<->Up r (full post-conv)        : {_fmt(r_isb_u_all,'',3)}")

        excur_flag = False
        for hi, h in enumerate(hs_ge, 1):
            lo_s, hi_s = h['start_sod'], h['end_sod']
            mask_h = (sods_ge >= lo_s) & (sods_ge <= hi_s)
            isb_h  = (float(np.mean(isb_ge[mask_h & np.isfinite(isb_ge)]))
                      if (mask_h & np.isfinite(isb_ge)).sum() > 0 else NAN)
            isb_dh = (isb_h - isb_bl_val
                      if (isb_h == isb_h and isb_bl_val == isb_bl_val) else NAN)
            r_isb  = _pearson(isb_ge[mask_h], u_ge[mask_h])
            thr2   = 2.0 * isb_mad if isb_mad == isb_mad else NAN
            excur  = (abs(isb_dh) > thr2
                      if (isb_dh == isb_dh and thr2 == thr2) else False)
            if excur: excur_flag = True
            print(f"\n     Hump {hi} ({h['start_h']:.2f}-{h['end_h']:.2f}h):")
            print(f"       ISB proxy mean  : {_fmt(isb_h)}  delta vs quiet: {_fmt(isb_dh)}"
                  + ("  <- >2sigma excursion" if excur else ""))
            print(f"       ISB<->Up r      : {_fmt(r_isb,'',3)}")
    elif dGE is None:
        print("     GPS+Galileo FWD not available.")
    else:
        print("     No humps detected in GPS+Galileo — ISB audit skipped.")

    # ── combined vs individual comparison ────────────────────────────────────
    print(f"\n{sep2}")
    print("  Combined vs individual constellation humps (auto windows):")

    hs_g  = mode_humps.get('GPS-only', [])
    hs_e  = mode_humps.get('Galileo-only', [])

    def _max_pos_amp(hs):
        pos = [h['amplitude_mm'] for h in hs if h['sign'] > 0]
        return max(pos) if pos else NAN

    def _peak_times(hs):
        return sorted([h['peak_h'] for h in hs if h['sign'] > 0])

    amp_g  = _max_pos_amp(hs_g)
    amp_e  = _max_pos_amp(hs_e)
    amp_ge = _max_pos_amp(hs_ge)
    print(f"     GPS-only     peak positive amp : {_fmt(amp_g)}")
    print(f"     Galileo-only peak positive amp : {_fmt(amp_e)}")
    print(f"     GPS+Galileo  peak positive amp : {_fmt(amp_ge)}")
    if all(v == v for v in [amp_g, amp_e, amp_ge]):
        excess = amp_ge - max(amp_g, amp_e)
        print(f"     Excess above max(G,E)         : {_fmt(excess)}"
              + ("  <- ISB-driven component likely" if excess > 5. else ""))

    pk_g  = _peak_times(hs_g)
    pk_e  = _peak_times(hs_e)
    pk_ge = _peak_times(hs_ge)
    print(f"\n     Peak times (positive humps):")
    print(f"       GPS-only    : {[f'{p:.2f}h' for p in pk_g]  or '(none)'}")
    print(f"       Galileo-only: {[f'{p:.2f}h' for p in pk_e]  or '(none)'}")
    print(f"       GPS+Galileo : {[f'{p:.2f}h' for p in pk_ge] or '(none)'}")
    if pk_ge and pk_g:
        diffs = [min(abs(pg - pk) for pk in pk_g) for pg in pk_ge]
        print(f"       GE<->G offset : {[f'{d:.2f}h' for d in diffs]}")
    if pk_ge and pk_e:
        diffs = [min(abs(pg - pk) for pk in pk_e) for pg in pk_ge]
        print(f"       GE<->E offset : {[f'{d:.2f}h' for d in diffs]}")

    # ── decision ─────────────────────────────────────────────────────────────
    print(f"\n{sep2}")
    print("  Decision:")

    def _has_hump(hs, thr=10.):
        return any(abs(h['amplitude_mm']) >= thr for h in hs)

    g_hump  = _has_hump(hs_g)
    e_hump  = _has_hump(hs_e)
    ge_hump = _has_hump(hs_ge)

    if g_hump and e_hump:
        print("  -> Hump present independently in GPS-only AND Galileo-only.")
        print("     Common cause: orbit/clock product error or troposphere mapping.")
    elif ge_hump and not g_hump and not e_hump:
        print("  -> Hump ONLY in combined solution -- ISB leakage SUSPECT.")
    elif ge_hump and g_hump and not e_hump:
        print("  -> GPS-dominated hump. Galileo clean.")
        print("     Action: audit GPS multipath / iono at IISC.")
    elif ge_hump and e_hump and not g_hump:
        print("  -> Galileo-dominated hump. GPS clean.")
        print("     Action: audit Galileo E5a multipath / iono.")
    else:
        print("  -> Hump pattern unclear -- inspect per-mode details above.")

    if r_isb_u_all == r_isb_u_all:
        if   abs(r_isb_u_all) > 0.35:
            print(f"  -> ISB<->Up r={r_isb_u_all:+.3f} (>0.35): ISB leakage CONFIRMED.")
        elif abs(r_isb_u_all) > 0.20:
            print(f"  -> ISB<->Up r={r_isb_u_all:+.3f} (0.20-0.35): weak coupling, ambiguous.")
        else:
            print(f"  -> ISB<->Up r={r_isb_u_all:+.3f} (<0.20): ISB<->Up NOT significant.")

    print(sep)
    return mode_humps, mode_data



# ==============================================================================
#  Orbit/clock leakage forensics — per-satellite hump attribution
#  Uses existing mode_humps dict (from _hump_attribution_audit) as fixed windows.
#  No hump detection, no solver changes.
# ==============================================================================
def _orbit_leakage_forensics(all_fwd, all_rts, REF, mode_humps, mode_data):
    """
    Per-satellite orbit/clock leakage forensics within pre-detected hump windows.

    For each constellation mode and each detected hump window:
      - Collects per-satellite code postfit residual, phase prefit, LOS·Up,
        elevation, azimuth from stored code_per_sat / phase_prefit_per_sat.
      - Orbit-Up proxy = code_res_mm × LOS·Up  (signed, mm).
      - Ranks satellites by |mean orbit-Up proxy| in the window.
      - Computes top-1/3/5 contribution fractions.
      - Leave-one-satellite-out: projects hump reduction when each top culprit
        is excluded (amplitude delta from mean orbit-Up proxy difference).
      - Rise/set geometry classification per satellite in hump window.
      - Repeat-period check: power at GPS/Galileo sidereal frequencies vs noise.

    Outputs (all diagnostic, no solver side effects):
      - Ranked culprit table (console)
      - Leave-one-out sensitivity table (console)
      - Verdict per mode (few-sat / distributed / clock / no culprit)
      - ppp_hump_heatmap.png  — satellite × time orbit-Up proxy heatmap
      - ppp_hump_skyplot.png  — skyplot of dominant contributors
    """
    import math as _math
    NAN = float('nan')
    CONV = 7200.

    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors
        _HAS_MPL = True
    except ImportError:
        _HAS_MPL = False
        print("[LEAKAGE_FORENSICS] matplotlib not available — skipping plots")

    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)
    e_up = Re[2, :]   # ECEF Up unit vector (3,)

    sep  = '=' * 72
    sep2 = '-' * 72

    print(f"\n{sep}")
    print("[LEAKAGE_FORENSICS] Per-satellite orbit/clock leakage forensics")
    print(f"                    (fixed windows from _hump_attribution_audit)")
    print(sep)

    # GPS sidereal day ≈ 23h 56m = 86160 s; Galileo orbital period ≈ 10.1 h
    _GPS_SIDERAL_H  = 86160. / 3600.
    _GAL_ORBITAL_H  = 10.095

    mode_order = ['GPS-only', 'Galileo-only', 'GPS+Galileo']

    # storage for plots
    _all_heatmap_data = {}   # (mode, hump_idx) -> dict
    _all_skyplot_data = {}   # (mode, hump_idx) -> list of (az_deg, el_deg, sid, contrib)

    # ── per-mode loop ─────────────────────────────────────────────────────────
    for mode in mode_order:
        fwd = all_fwd.get(mode, {})
        if not fwd:
            print(f"\n[LEAKAGE_FORENSICS] {mode}: no forward data — skip")
            continue
        humps = mode_humps.get(mode, [])
        if not humps:
            print(f"\n[LEAKAGE_FORENSICS] {mode}: no detected hump windows — skip")
            continue

        print(f"\n{sep2}")
        print(f"[LEAKAGE_FORENSICS] Mode: {mode}  ({len(humps)} hump window(s))")
        print(sep2)

        # build sorted SOD list and Up error array for this mode
        _fwd_sods = np.array(sorted(fwd.keys()), dtype=float)
        _up_fwd   = np.array([(Re @ fwd[k]['dx'])[2] * 1e3 for k in _fwd_sods])

        for hi, hd in enumerate(humps):
            if not isinstance(hd, dict):
                continue
            h_lo = hd['start_sod']
            h_hi = hd['end_sod']
            h_pk = hd['peak_sod']
            h_amp = hd.get('amplitude_mm', NAN)

            # --- baseline SOD: 1 h before hump start (or CONV), same width
            bl_lo = max(CONV, h_lo - 3600.)
            bl_hi = h_lo

            print(f"\n  Hump {hi+1}: {hd['start_h']:.2f}h – {hd['end_h']:.2f}h "
                  f"peak={hd['peak_h']:.2f}h  amp={h_amp:+.1f}mm")

            # actual Up error amplitude in hump window (from FWD series)
            _hm = (_fwd_sods >= h_lo) & (_fwd_sods <= h_hi) & np.isfinite(_up_fwd)
            _bm = (_fwd_sods >= bl_lo) & (_fwd_sods <= bl_hi) & np.isfinite(_up_fwd)
            up_hump_mean = float(np.mean(_up_fwd[_hm])) if _hm.sum() > 0 else NAN
            up_base_mean = float(np.mean(_up_fwd[_bm])) if _bm.sum() > 0 else NAN
            actual_amp   = up_hump_mean - up_base_mean

            # ── collect per-satellite data within hump window ─────────────────
            # sat_data[sid] -> lists over epochs
            sat_code_res   = {}   # code postfit res (mm)
            sat_los_up     = {}   # LOS·Up dot product
            sat_el         = {}   # elevation (deg)
            sat_az         = {}   # azimuth (deg)
            sat_phase_L1   = {}   # phase prefit L1 (mm)
            sat_epochs     = {}   # SODs this sat appears in hump window
            epoch_sods_in  = []   # SODs inside hump window

            for sod in sorted(fwd.keys()):
                sod_f = float(sod)
                if sod_f < h_lo or sod_f > h_hi:
                    continue
                epoch_sods_in.append(sod_f)
                r = fwd[sod]
                cps  = r.get('code_per_sat', {})        # sid->(res_mm,ldu,el,az)
                ppps = r.get('phase_prefit_per_sat', {}) # sid->(L1m,L2m,el,az)
                for sid, (res_mm, ldu, el_d, az_d) in cps.items():
                    if sid not in sat_code_res:
                        sat_code_res[sid] = []; sat_los_up[sid] = []
                        sat_el[sid] = []; sat_az[sid] = []; sat_epochs[sid] = []
                    sat_code_res[sid].append(res_mm)
                    sat_los_up[sid].append(ldu)
                    sat_el[sid].append(el_d)
                    sat_az[sid].append(az_d)
                    sat_epochs[sid].append(sod_f)
                    # phase prefit (m → mm)
                    if sid not in sat_phase_L1:
                        sat_phase_L1[sid] = []
                    if sid in ppps:
                        sat_phase_L1[sid].append(ppps[sid][0] * 1e3)
                    else:
                        sat_phase_L1[sid].append(NAN)

            if not sat_code_res:
                print("    (no per-satellite data in this window — code_per_sat empty)")
                continue

            n_hump_epochs = len(epoch_sods_in)

            # ── orbit-Up proxy per satellite ──────────────────────────────────
            # orbit_up_proxy = code_res_mm × LOS·Up (signed, mm)
            sat_oup_mean = {}   # mean orbit-Up proxy
            sat_oup_arr  = {}   # full time series (epoch-aligned, NaN for absent)
            sods_arr     = np.array(epoch_sods_in)

            for sid in sat_code_res:
                res_arr = np.array(sat_code_res[sid])
                ldu_arr = np.array(sat_los_up[sid])
                oup     = res_arr * ldu_arr
                sat_oup_mean[sid] = float(np.nanmean(oup))
                # epoch-aligned array
                ep_map = {s: v for s, v in zip(sat_epochs[sid], oup)}
                sat_oup_arr[sid] = np.array([ep_map.get(s, NAN) for s in sods_arr])

            # ── satellite mean geometry ───────────────────────────────────────
            sat_mean_el = {sid: float(np.nanmean(sat_el[sid])) for sid in sat_el}
            sat_mean_az = {sid: float(np.nanmean(sat_az[sid])) for sid in sat_az}

            # ── rise / set / transit classification ──────────────────────────
            def _rise_set(el_list):
                if len(el_list) < 3:
                    return 'transit'
                de = el_list[-1] - el_list[0]
                if de > 8.:
                    return 'rising'
                if de < -8.:
                    return 'setting'
                return 'transit'

            sat_geo_class = {sid: _rise_set(sat_el[sid]) for sid in sat_el}

            # ── coverage fraction in window ───────────────────────────────────
            sat_cov = {sid: len(sat_epochs[sid]) / max(1, n_hump_epochs)
                       for sid in sat_epochs}

            # ── rank satellites by |mean orbit-Up proxy| ──────────────────────
            ranked = sorted(sat_oup_mean.keys(),
                            key=lambda s: abs(sat_oup_mean[s]), reverse=True)
            total_abs = sum(abs(sat_oup_mean[s]) for s in ranked)
            if total_abs < 1e-9:
                print("    (all orbit-Up proxies near zero — no leakage signal)")
                continue

            # contribution fractions
            def _cumfrac(n):
                return sum(abs(sat_oup_mean[s]) for s in ranked[:n]) / total_abs

            frac1 = _cumfrac(1)  if len(ranked) >= 1 else NAN
            frac3 = _cumfrac(3)  if len(ranked) >= 3 else NAN
            frac5 = _cumfrac(5)  if len(ranked) >= 5 else NAN

            # ── leave-one-out sensitivity ─────────────────────────────────────
            # Approximation: hump amplitude ≈ Σ_s mean_orbit_up_proxy(s)
            # leaving out sat s → amplitude_reduction ≈ mean_oup(s)
            # percentage reduction = mean_oup(s) / actual_amp * 100
            top_n_loo = min(5, len(ranked))

            # ── repeat-period check ───────────────────────────────────────────
            def _period_score(oup_ts, sods_ts, target_h):
                """Fractional power near target period vs mean spectral power."""
                fin = np.isfinite(oup_ts)
                if fin.sum() < 20:
                    return NAN
                y = oup_ts[fin] - np.nanmean(oup_ts[fin])
                N = len(y)
                dt = 30.
                freq = np.fft.rfftfreq(N, d=dt)
                amp  = np.abs(np.fft.rfft(y)); amp[0] = 0.
                if amp.sum() < 1e-12:
                    return NAN
                target_hz = 1. / (target_h * 3600.)
                bw = 1. / (N * dt) * 3.  # 3-bin bandwidth
                near_target = (np.abs(freq - target_hz) < bw)
                frac = amp[near_target].sum() / amp.sum()
                return float(frac)

            # ── console: ranked culprit table ──────────────────────────────────
            print(f"\n  Ranked culprit satellites (orbit-Up proxy, signed):")
            print(f"  {'Rank':>4}  {'Sat':>5}  {'MeanOUP':>9}  "
                  f"{'CumFrac':>8}  {'MeanEl':>7}  {'MeanAz':>7}  "
                  f"{'Geo':>8}  {'Cov%':>5}  {'PhsRMS':>8}")
            cum = 0.
            for rank_i, sid in enumerate(ranked):
                oup_val = sat_oup_mean[sid]
                cum += abs(oup_val) / total_abs
                ph_arr = np.array(sat_phase_L1[sid])
                ph_rms = float(np.sqrt(np.nanmean(ph_arr**2))) if np.isfinite(ph_arr).any() else NAN
                print(f"  {rank_i+1:>4}  {sid:>5}  {oup_val:>+9.2f}  "
                      f"{cum:>8.3f}  {sat_mean_el[sid]:>7.1f}  "
                      f"{sat_mean_az[sid]:>7.1f}  "
                      f"{sat_geo_class[sid]:>8}  "
                      f"{sat_cov[sid]*100:>5.0f}  "
                      f"{ph_rms:>8.2f}")

            print(f"\n  Contribution fractions:  "
                  f"top-1={frac1:.3f}  top-3={frac3:.3f}  top-5={frac5:.3f}")

            # ── console: leave-one-out sensitivity table ───────────────────────
            print(f"\n  Leave-one-out sensitivity (top-{top_n_loo} contributors):")
            print(f"  {'Sat':>5}  {'OUP_contrib':>12}  {'HumpΔ_mm':>10}  "
                  f"{'Hump%red':>9}  {'Verdict':>18}")
            for sid in ranked[:top_n_loo]:
                oup_contrib = sat_oup_mean[sid]
                # projected hump reduction: remove this sat's mean orbit-Up contribution
                hump_delta  = -oup_contrib            # signed mm shift in Up
                pct_red     = (-oup_contrib / actual_amp * 100.
                               if abs(actual_amp) > 1. else NAN)
                if abs(pct_red) > 25.:
                    verd = 'MAJOR contributor'
                elif abs(pct_red) > 10.:
                    verd = 'moderate contributor'
                else:
                    verd = 'minor'
                print(f"  {sid:>5}  {oup_contrib:>+12.2f}  {hump_delta:>+10.2f}  "
                      f"{pct_red:>+9.1f}%  {verd:>18}")

            # ── rise/set geometry check ────────────────────────────────────────
            geo_counts = {'rising': 0, 'setting': 0, 'transit': 0}
            for sid in ranked[:top_n_loo]:
                geo_counts[sat_geo_class[sid]] += 1
            rs_frac = (geo_counts['rising'] + geo_counts['setting']) / max(1, top_n_loo)
            print(f"\n  Top-{top_n_loo} rise/set geometry:  "
                  f"rising={geo_counts['rising']}  setting={geo_counts['setting']}  "
                  f"transit={geo_counts['transit']}  "
                  f"rs_fraction={rs_frac:.2f}")

            # ── repeat-period check ────────────────────────────────────────────
            print(f"\n  Repeat-period power fractions (top-3 sats):")
            for sid in ranked[:3]:
                ts = sat_oup_arr[sid]
                sc_gps = _period_score(ts, sods_arr, _GPS_SIDERAL_H)
                sc_gal = _period_score(ts, sods_arr, _GAL_ORBITAL_H)
                print(f"    {sid}: GPS-sidereal={sc_gps:.3f}  Galileo-orbital={sc_gal:.3f}")

            # ── verdict ────────────────────────────────────────────────────────
            print(f"\n  ── Verdict ──")
            n_dom = sum(1 for s in ranked if abs(sat_oup_mean[s]) / total_abs > 0.20)
            if frac1 > 0.50:
                verdict = 'FEW-SATELLITE DOMINATED LEAKAGE'
                detail  = (f"Single sat {ranked[0]} contributes {frac1:.0%} "
                           f"of orbit-Up proxy power.")
            elif frac3 > 0.70:
                verdict = 'FEW-SATELLITE DOMINATED LEAKAGE'
                detail  = (f"Top-3 sats contribute {frac3:.0%}.  "
                           f"Dominant: {ranked[0]}, {ranked[1]}, {ranked[2]}.")
            elif rs_frac > 0.60:
                verdict = 'DISTRIBUTED GEOMETRY-REPEAT LEAKAGE'
                detail  = (f"{rs_frac:.0%} of top contributors are in "
                           f"rise/set geometry — repeat-period orbit error likely.")
            elif frac5 < 0.60 and len(ranked) >= 6:
                verdict = 'COMMON-MODE CLOCK LEAKAGE'
                detail  = (f"Top-5 sats cover only {frac5:.0%} of proxy power.  "
                           f"Distributed across {len(ranked)} sats — clock common-mode likely.")
            else:
                verdict = 'NO IDENTIFIABLE CULPRIT'
                detail  = "Proxy power is diffuse; no single mechanism dominates."
            print(f"  {verdict}")
            print(f"  {detail}")

            # ── store for plots ────────────────────────────────────────────────
            _all_heatmap_data[(mode, hi)] = {
                'sods': sods_arr,
                'ranked': ranked,
                'oup_arr': sat_oup_arr,
                'mode': mode,
                'hump_idx': hi,
                'start_h': hd['start_h'],
                'end_h': hd['end_h'],
                'peak_h': hd['peak_h'],
            }
            _all_skyplot_data[(mode, hi)] = [
                (sat_mean_az[s], sat_mean_el[s], s,
                 abs(sat_oup_mean[s]) / total_abs)
                for s in ranked
            ]

    # ─────────────────────────────────────────────────────────────────────────
    # PLOTS
    # ─────────────────────────────────────────────────────────────────────────
    if not _HAS_MPL or not _all_heatmap_data:
        print("\n[LEAKAGE_FORENSICS] No plot data — skipping figures")
        return

    _outdir = os.path.dirname(os.path.abspath(__file__))

    # ── Figure 1: orbit-Up proxy heatmaps ────────────────────────────────────
    n_panels = len(_all_heatmap_data)
    if n_panels > 0:
        fig_h, axes_h = plt.subplots(n_panels, 1,
                                     figsize=(12, max(3, 3 * n_panels)),
                                     squeeze=False)
        fig_h.suptitle('PPP-AR — Orbit/Clock Leakage: per-satellite orbit-Up proxy\n'
                       '(code_res_mm × LOS·Up, signed mm)',
                       fontsize=11, fontweight='bold')
        for panel_i, ((mode, hi), hd_p) in enumerate(sorted(_all_heatmap_data.items())):
            ax = axes_h[panel_i, 0]
            sods_p   = hd_p['sods']
            hrs_p    = sods_p / 3600.
            ranked_p = hd_p['ranked'][:min(12, len(hd_p['ranked']))]
            oup_mat  = np.array([hd_p['oup_arr'][s] for s in ranked_p])  # (n_sat, n_t)
            vmax     = np.nanpercentile(np.abs(oup_mat), 95) if oup_mat.size > 0 else 1.
            vmax     = max(vmax, 1.)
            im = ax.imshow(oup_mat, aspect='auto', origin='upper',
                           extent=[float(hrs_p[0]), float(hrs_p[-1]),
                                   len(ranked_p) - 0.5, -0.5],
                           vmin=-vmax, vmax=vmax,
                           cmap='RdBu_r', interpolation='nearest')
            ax.set_yticks(range(len(ranked_p)))
            ax.set_yticklabels(ranked_p, fontsize=7)
            ax.set_xlabel('Time (h)')
            ax.set_ylabel('Satellite')
            ax.set_title(f'{mode}  hump {hi+1}: '
                         f'{hd_p["start_h"]:.1f}h–{hd_p["end_h"]:.1f}h '
                         f'(peak {hd_p["peak_h"]:.1f}h)',
                         fontsize=9)
            plt.colorbar(im, ax=ax, label='orbit-Up proxy (mm)', pad=0.01)
        plt.tight_layout()
        _heat_path = os.path.join(_outdir, 'ppp_hump_heatmap.png')
        try:
            fig_h.savefig(_heat_path, dpi=150, bbox_inches='tight')
            print(f"\n[LEAKAGE_FORENSICS] Heatmap saved: {_heat_path}")
        except Exception as _e:
            print(f"\n[LEAKAGE_FORENSICS] Heatmap save failed: {_e}")
        plt.close(fig_h)

    # ── Figure 2: skyplot of dominant contributors ────────────────────────────
    n_sky = len(_all_skyplot_data)
    if n_sky > 0:
        _ncols = min(3, n_sky)
        _nrows = (n_sky + _ncols - 1) // _ncols
        fig_s, axes_s = plt.subplots(_nrows, _ncols,
                                     figsize=(5 * _ncols, 5 * _nrows),
                                     subplot_kw={'projection': 'polar'},
                                     squeeze=False)
        fig_s.suptitle('PPP-AR — Dominant Culprit Satellites: skyplot during hump windows\n'
                       '(marker size ∝ contribution fraction)',
                       fontsize=11, fontweight='bold')
        _cmap_sky = plt.cm.get_cmap('tab20')
        for panel_i, ((mode, hi), sp_list) in enumerate(sorted(_all_skyplot_data.items())):
            ri = panel_i // _ncols; ci = panel_i % _ncols
            ax = axes_s[ri, ci]
            ax.set_theta_zero_location('N')
            ax.set_theta_direction(-1)          # clockwise (azimuth convention)
            ax.set_ylim(0, 90)
            ax.set_yticks([0, 15, 30, 45, 60, 75, 90])
            ax.set_yticklabels(['90°', '75°', '60°', '45°', '30°', '15°', '0°'],
                                fontsize=6)
            for k, (az_d, el_d, sid, frac) in enumerate(sp_list[:10]):
                r_sky = 90. - el_d   # zenith at centre
                az_r  = _math.radians(az_d)
                color = _cmap_sky(k % 20)
                size  = max(20., frac * 800.)
                ax.scatter(az_r, r_sky, s=size, color=color,
                           alpha=0.85, zorder=3, label=f'{sid} ({frac:.1%})')
                ax.annotate(sid, (az_r, r_sky), fontsize=6,
                            ha='center', va='bottom',
                            xytext=(0, 4), textcoords='offset points')
            ax.set_title(f'{mode}\nhump {hi+1}', fontsize=8, pad=8)
            ax.legend(loc='lower left', bbox_to_anchor=(-0.2, -0.15),
                      fontsize=6, ncol=2)
        # hide unused axes
        for panel_i in range(n_sky, _nrows * _ncols):
            ri = panel_i // _ncols; ci = panel_i % _ncols
            axes_s[ri, ci].set_visible(False)
        plt.tight_layout()
        _sky_path = os.path.join(_outdir, 'ppp_hump_skyplot.png')
        try:
            fig_s.savefig(_sky_path, dpi=150, bbox_inches='tight')
            print(f"[LEAKAGE_FORENSICS] Skyplot saved: {_sky_path}")
        except Exception as _e:
            print(f"[LEAKAGE_FORENSICS] Skyplot save failed: {_e}")
        plt.close(fig_s)

    print(f"\n{sep}")


# ==============================================================================
#  ORBIT / ELEVATION LEAKAGE DIAGNOSTIC  (v92-diag)
# ==============================================================================
#  Uses FIXED hump windows from leakage_forensics — no re-detection.
#  Tests:
#    1. Steeper elevation weighting (sin²el) applied to stored residuals
#       inside each window vs baseline (sin¹el), using code_per_sat data.
#    2. Per-satellite residual audit for recurring suspects:
#       G08, G16, G26, E29, E33.
#    3. Leave-one-out downweighting: weight each suspect ×10 (variance)
#       and measure hump amplitude proxy reduction.
#    4. Low (<25°) vs high (>25°) elevation contribution split per hump.
#    5. LOS·Up geometry residual tracking vs hump amplitude.
#  DIAGNOSTIC ONLY — no filter changes, no solver side effects.
# ==============================================================================

_FIXED_HUMP_WINDOWS = {
    'GPS-only': [
        {'start_h': 2.00, 'end_h': 5.40,  'peak_h': 2.08,  'amp_mm': +145.2},
        {'start_h': 23.21,'end_h': 23.99, 'peak_h': 23.99, 'amp_mm': +63.0},
    ],
    'Galileo-only': [
        {'start_h': 2.00, 'end_h': 3.15,  'peak_h': 2.22,  'amp_mm': +156.8},
        {'start_h': 20.38,'end_h': 21.18, 'peak_h': 21.03, 'amp_mm': +84.4},
    ],
    'GPS+Galileo': [
        {'start_h': 22.07,'end_h': 23.99, 'peak_h': 23.99, 'amp_mm': +137.4},
    ],
}

_SUSPECTS = ['G08', 'G16', 'G26', 'E29', 'E33']


def _elev_leakage_diagnostic(all_fwd, all_rts, REF):
    """
    Orbit/elevation leakage diagnostic using fixed hump windows.
    No solver changes. Purely post-hoc analysis of stored per-epoch data.
    """
    import math as _math

    NAN = float('nan')
    CONV_SOD = 7200.  # 2 h convergence gate

    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)
    e_up = Re[2, :]  # ECEF Up unit vector

    sep  = '=' * 72
    sep2 = '-' * 72

    print(f"\n{sep}")
    print("[ELEV_LEAKAGE_DIAG] Orbit/Elevation Leakage Diagnostic  (v92-diag)")
    print(f"  Fixed hump windows — no re-detection.")
    print(f"  Suspects: {_SUSPECTS}")
    print(sep)

    def _oup_baseline(res_mm, los_up):
        """Orbit-Up proxy with baseline weighting (sin^1 already in residual)."""
        return res_mm * los_up  # signed mm

    def _sin_el(el_deg):
        return max(_math.sin(_math.radians(el_deg)), 0.1)

    def _w_baseline(el_deg):
        """Baseline weight: 1/sin(el)"""
        return 1.0 / _sin_el(el_deg)

    def _w_steep(el_deg):
        """Steep weight: 1/sin^2(el)"""
        return 1.0 / (_sin_el(el_deg) ** 2)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _extract_window(fwd, start_h, end_h):
        """Return list of (sod, code_per_sat_dict) within window."""
        lo_s = start_h * 3600.
        hi_s = end_h   * 3600.
        out = []
        for sod in sorted(fwd.keys()):
            if lo_s <= sod <= hi_s and sod >= CONV_SOD:
                cps = fwd[sod].get('code_per_sat', {})
                if cps:
                    out.append((sod, cps))
        return out

    def _up_rms_weighted(window_epochs, w_fn, exclude_sid=None, downweight_sid=None, dw_factor=10.):
        """
        Compute Up-projected weighted residual RMS across window.
        w_fn(el_deg) -> scalar weight applied to |res| before squaring.
        exclude_sid: remove satellite entirely (leave-one-out removal).
        downweight_sid: apply dw_factor^2 to variance of this satellite.
        Returns (rms_mm, n_obs, sat_contributions {sid: contribution_mm^2}).
        """
        sq_sum = 0.0
        n      = 0
        contrib = {}
        for sod, cps in window_epochs:
            for sid, (res_mm, los_up, el_deg, az_deg) in cps.items():
                if not _math.isfinite(res_mm): continue
                if sid == exclude_sid: continue
                w = _w_baseline(el_deg)  # baseline weight for magnitude
                up_proj = abs(res_mm) * abs(los_up)  # mm
                dw = dw_factor if (sid == downweight_sid) else 1.0
                val = (w * up_proj / dw) ** 2
                sq_sum += val
                n      += 1
                contrib[sid] = contrib.get(sid, 0.) + val
        if n == 0: return NAN, 0, {}
        return _math.sqrt(sq_sum / n), n, contrib

    def _up_rms_exp(window_epochs, exp):
        """
        Compare Up-projected residual RMS using elevation weight 1/sin^exp(el).
        Returns (rms_mm, n_obs).
        """
        sq_sum = 0.0
        n = 0
        for sod, cps in window_epochs:
            for sid, (res_mm, los_up, el_deg, az_deg) in cps.items():
                if not _math.isfinite(res_mm): continue
                sel = _sin_el(el_deg)
                w   = 1.0 / (sel ** exp)
                up_proj = abs(res_mm) * abs(los_up)
                val = (w * up_proj) ** 2
                sq_sum += val
                n += 1
        if n == 0: return NAN, 0
        return _math.sqrt(sq_sum / n), n

    def _el_split(window_epochs, el_thresh=25.):
        """
        Split Up-projected residual contribution into low (<el_thresh°) vs high.
        Returns (low_rms, high_rms, n_low, n_high).
        """
        lo_sq, hi_sq, n_lo, n_hi = 0., 0., 0, 0
        for sod, cps in window_epochs:
            for sid, (res_mm, los_up, el_deg, az_deg) in cps.items():
                if not _math.isfinite(res_mm): continue
                up_proj = abs(res_mm) * abs(los_up)
                if el_deg < el_thresh:
                    lo_sq += up_proj ** 2; n_lo += 1
                else:
                    hi_sq += up_proj ** 2; n_hi += 1
        lo_rms = _math.sqrt(lo_sq / n_lo) if n_lo > 0 else NAN
        hi_rms = _math.sqrt(hi_sq / n_hi) if n_hi > 0 else NAN
        return lo_rms, hi_rms, n_lo, n_hi

    def _suspect_stats(window_epochs, suspects):
        """
        Per-suspect mean Up-proj residual, elevation, and contribution fraction.
        Returns dict sid -> {mean_up, mean_el, frac, n_ep}.
        """
        sat_up  = {}; sat_el = {}; sat_n = {}
        total_up = 0.
        for sod, cps in window_epochs:
            for sid, (res_mm, los_up, el_deg, az_deg) in cps.items():
                if not _math.isfinite(res_mm): continue
                up_proj = abs(res_mm) * abs(los_up)
                total_up += up_proj
                sat_up[sid] = sat_up.get(sid, 0.) + up_proj
                sat_el[sid] = sat_el.get(sid, 0.) + el_deg
                sat_n[sid]  = sat_n.get(sid, 0)   + 1
        out = {}
        for s in suspects:
            n = sat_n.get(s, 0)
            mu_up = sat_up.get(s, 0.) / n if n > 0 else NAN
            mu_el = sat_el.get(s, 0.) / n if n > 0 else NAN
            frac  = sat_up.get(s, 0.) / total_up if total_up > 0 else NAN
            out[s] = {'mean_up': mu_up, 'mean_el': mu_el, 'frac': frac, 'n_ep': n}
        return out

    def _hump_amp_proxy(window_epochs):
        """Mean of Up-proj |residual × LOS·Up| across all epochs in window (mm)."""
        vals = []
        for sod, cps in window_epochs:
            ep_up = [abs(r) * abs(lu) for (r, lu, el, az) in cps.values()
                     if _math.isfinite(r)]
            if ep_up:
                vals.append(float(np.mean(ep_up)))
        return float(np.mean(vals)) if vals else NAN

    # ── main loop ─────────────────────────────────────────────────────────────
    _decision_votes = {'orbit': 0, 'clock': 0, 'mixed': 0}

    for mode, hump_list in _FIXED_HUMP_WINDOWS.items():
        fwd = all_fwd.get(mode, {})
        if not fwd:
            continue

        print(f"\n{sep2}")
        print(f"[ELEV_LEAKAGE_DIAG] Mode: {mode}")
        print(sep2)

        for hi, hd in enumerate(hump_list):
            sh, eh, pk = hd['start_h'], hd['end_h'], hd['peak_h']
            amp_ref    = hd['amp_mm']
            win_epochs = _extract_window(fwd, sh, eh)
            if not win_epochs:
                print(f"  Hump {hi+1} [{sh:.2f}h–{eh:.2f}h]: no data in window — skip")
                continue

            print(f"\n  ── Hump {hi+1}  [{sh:.2f}h – {eh:.2f}h]  peak={pk:.2f}h  "
                  f"ref_amp={amp_ref:+.1f}mm ──")

            # 1. Elevation weighting comparison
            rms_b1, n_b1 = _up_rms_exp(win_epochs, exp=1.0)
            rms_b2, n_b2 = _up_rms_exp(win_epochs, exp=2.0)
            delta_pct = (rms_b2 - rms_b1) / rms_b1 * 100. if rms_b1 > 0 and _math.isfinite(rms_b1) else NAN
            print(f"\n  [1] Elevation weighting comparison (Up-proj residual RMS):")
            print(f"      Baseline  sin^1  : {rms_b1:7.2f} mm  (n={n_b1})")
            print(f"      Steep     sin^2  : {rms_b2:7.2f} mm  (n={n_b2})")
            pct_str = f"{delta_pct:+.1f}%" if _math.isfinite(delta_pct) else "N/A"
            print(f"      Δ (steep−base)   : {pct_str}")
            if _math.isfinite(delta_pct):
                if delta_pct < -5.:
                    print(f"      → HUMP REDUCES under steeper weighting — low-el orbit leakage likely")
                elif delta_pct > +5.:
                    print(f"      → Hump INCREASES — high-el (clock/troposphere) more likely")
                else:
                    print(f"      → Negligible change — mechanism not elevation-driven")

            # 2. Suspect satellite audit
            susp_stats = _suspect_stats(win_epochs, _SUSPECTS)
            print(f"\n  [2] Suspect satellite audit ({', '.join(_SUSPECTS)}):")
            print(f"      {'Sat':<6}  {'n_ep':>5}  {'mean_el':>8}  "
                  f"{'mean_Up_proj':>13}  {'frac':>6}")
            total_susp_frac = 0.
            for s in _SUSPECTS:
                ss = susp_stats[s]
                n_s = ss['n_ep']
                mu_u = ss['mean_up']
                mu_e = ss['mean_el']
                fr   = ss['frac']
                if n_s == 0:
                    print(f"      {s:<6}  {'---':>5}  {'---':>8}  {'---':>13}  {'---':>6}")
                    continue
                total_susp_frac += fr if _math.isfinite(fr) else 0.
                print(f"      {s:<6}  {n_s:>5}  {mu_e:>7.1f}°  "
                      f"{mu_u:>12.2f}mm  {fr:>5.1%}")
            print(f"      Suspects combined fraction: {total_susp_frac:.1%}")

            # 3. Leave-one-out downweighting (weight ×10 → variance ×100)
            baseline_rms, _, _ = _up_rms_weighted(win_epochs, _w_baseline)
            print(f"\n  [3] Leave-one-out downweighting (weight ×10) of suspects:")
            print(f"      Baseline RMS (no downweight): {baseline_rms:7.2f} mm")
            print(f"      {'Sat':<6}  {'Downweighted RMS':>17}  {'Δ_RMS':>8}  {'Δ%':>7}  {'Verdict':}")
            loo_results = {}
            for s in _SUSPECTS:
                if susp_stats[s]['n_ep'] == 0:
                    print(f"      {s:<6}  {'not seen':>17}")
                    continue
                dw_rms, _, _ = _up_rms_weighted(win_epochs, _w_baseline,
                                                 downweight_sid=s, dw_factor=10.)
                delta_rms = dw_rms - baseline_rms
                delta_p   = delta_rms / baseline_rms * 100. if baseline_rms > 0 else NAN
                verdict = ('REDUCES hump' if delta_p < -3.
                           else 'NEUTRAL' if abs(delta_p) <= 3.
                           else 'INCREASES')
                loo_results[s] = delta_p
                print(f"      {s:<6}  {dw_rms:>16.2f}mm  {delta_rms:>+7.2f}mm  "
                      f"{delta_p:>+6.1f}%  {verdict}")

            # 4. Elevation split
            lo_rms, hi_rms, n_lo, n_hi = _el_split(win_epochs, el_thresh=25.)
            lo_frac = n_lo / (n_lo + n_hi) if (n_lo + n_hi) > 0 else NAN
            hi_frac = n_hi / (n_lo + n_hi) if (n_lo + n_hi) > 0 else NAN
            print(f"\n  [4] Low (<25°) vs High (>25°) elevation split:")
            print(f"      Low  (<25°): {lo_rms:7.2f} mm  n={n_lo}  ({lo_frac:.0%} of obs)")
            print(f"      High (>25°): {hi_rms:7.2f} mm  n={n_hi}  ({hi_frac:.0%} of obs)")
            if _math.isfinite(lo_rms) and _math.isfinite(hi_rms) and lo_rms > 0:
                ratio = hi_rms / lo_rms
                print(f"      High/Low RMS ratio: {ratio:.2f}  "
                      f"({'low-el dominant' if ratio < 0.8 else 'high-el comparable' if ratio < 1.3 else 'high-el dominant'})")

            # 5. LOS·Up geometry residual vs hump amplitude
            amp_proxy = _hump_amp_proxy(win_epochs)
            oup_suspects = {s: susp_stats[s]['mean_up']
                            for s in _SUSPECTS if susp_stats[s]['n_ep'] > 0}
            print(f"\n  [5] LOS·Up geometry residual vs hump amplitude proxy:")
            print(f"      Mean hump Up-proj residual (all sats): {amp_proxy:.2f} mm")
            if oup_suspects:
                dom = max(oup_suspects, key=lambda s: oup_suspects.get(s, 0.))
                dom_val = oup_suspects[dom]
                dom_frac = dom_val / amp_proxy if amp_proxy > 0 else NAN
                print(f"      Dominant suspect: {dom}  mean_Up={dom_val:.2f} mm  "
                      f"({dom_frac:.0%} of total proxy)")

            # ── Verdict for this hump ──────────────────────────────────────────
            print(f"\n  [VERDICT] Hump {hi+1} ({mode}):")
            votes = {'orbit': 0, 'clock': 0}
            # Criterion A: steeper weighting reduces hump
            if _math.isfinite(delta_pct) and delta_pct < -5.:
                votes['orbit'] += 2
                print(f"    ✓ Steeper weighting reduces by {abs(delta_pct):.1f}% → orbit/elev leakage supported")
            elif _math.isfinite(delta_pct) and delta_pct > +5.:
                votes['clock'] += 2
                print(f"    ✗ Steeper weighting INCREASES → clock/tropo more likely")
            else:
                print(f"    ~ Elevation change neutral → elevation not primary driver")
            # Criterion B: low-el dominant
            if _math.isfinite(lo_rms) and _math.isfinite(hi_rms) and hi_rms > 0:
                if lo_rms > 1.5 * hi_rms:
                    votes['orbit'] += 1
                    print(f"    ✓ Low-el residuals dominate ({lo_rms:.1f}mm vs {hi_rms:.1f}mm) → orbit leakage")
                elif hi_rms > 1.5 * lo_rms:
                    votes['clock'] += 1
                    print(f"    ~ High-el residuals larger ({hi_rms:.1f}mm vs {lo_rms:.1f}mm) → not low-el")
            # Criterion C: suspects dominate
            if total_susp_frac > 0.30:
                votes['orbit'] += 1
                print(f"    ✓ Suspects cover {total_susp_frac:.0%} of proxy → few-sat dominated")
            else:
                votes['clock'] += 1
                print(f"    ~ Suspects cover {total_susp_frac:.0%} → distributed (clock common-mode possible)")
            # Criterion D: LOO downweighting reduces hump for any suspect
            big_loo = [s for s, dp in loo_results.items()
                       if _math.isfinite(dp) and dp < -3.]
            if big_loo:
                votes['orbit'] += 1
                print(f"    ✓ LOO downweighting reduces hump for: {big_loo}")
            else:
                votes['clock'] += 0
                print(f"    ~ LOO downweighting: no suspect individually reduces hump materially")

            if votes['orbit'] >= 3:
                final = '(A) ORBIT/ELEVATION LEAKAGE CONFIRMED'
                _decision_votes['orbit'] += 1
            elif votes['clock'] >= 3:
                final = '(B) COMMON-MODE CLOCK LEAKAGE MORE LIKELY'
                _decision_votes['clock'] += 1
            else:
                final = '(C) MIXED MECHANISM'
                _decision_votes['mixed'] += 1
            print(f"\n    ══ DECISION: {final} ══")
            print(f"       (orbit_votes={votes['orbit']}  clock_votes={votes['clock']})")

    # ── Overall summary ───────────────────────────────────────────────────────
    print(f"\n{sep}")
    print("[ELEV_LEAKAGE_DIAG] OVERALL SUMMARY ACROSS ALL HUMPS AND MODES")
    print(sep)
    tot = sum(_decision_votes.values())
    print(f"  (A) Orbit/elev leakage confirmed : {_decision_votes['orbit']}/{tot} humps")
    print(f"  (B) Clock leakage more likely    : {_decision_votes['clock']}/{tot} humps")
    print(f"  (C) Mixed mechanism              : {_decision_votes['mixed']}/{tot} humps")
    if _decision_votes['orbit'] > _decision_votes['clock']:
        print("\n  ► RECOMMENDATION: Orbit/elevation leakage is dominant.")
        print("    Prioritise orbit/elevation fix next:")
        print("    - Apply sin²(el) elevation weighting globally")
        print("    - Investigate G08, G16, G26, E29, E33 orbit corrections")
        print("    - Consider higher elevation cutoff (15°) for suspect sats")
    elif _decision_votes['clock'] > _decision_votes['orbit']:
        print("\n  ► RECOMMENDATION: Common-mode clock leakage is dominant.")
        print("    Orbit/elevation fix unlikely to help significantly.")
        print("    Investigate clock interpolation error or ISB residual.")
    else:
        print("\n  ► RECOMMENDATION: Mixed mechanism — run both orbit and clock diagnostics.")
    print(sep)


# ==============================================================================
#  CLOCK vs ORBIT SEPARATION DIAGNOSTIC  (v91-clkorb)
# ==============================================================================
#  For each fixed hump window, decomposes postfit code residuals into:
#    • Common-mode component  (all sats move together → receiver-clock or
#      broadcast-clock inconsistency signal)
#    • Differential component (satellite-specific → orbit error signal)
#  Additional tests:
#    • Correlation of Up error with receiver-clock state from KF
#    • Correlation of Up error with common-mode residual proxy
#    • Per-sat correlation of differential Up proxy with Up error
#    • Clock-jump detection: epoch-by-epoch std of common-mode
#    • New-rise arc test: does hump coincide with satellites newly entering view?
#    • Leave-one-out on orbit-Up proxy restricted to top culprits from skyplot
#  Verdict:
#    A) ORBIT LEAKAGE DOMINATED
#    B) CLOCK INCONSISTENCY DOMINATED
#    C) MIXED ORBIT-CLOCK LEAKAGE
#  DIAGNOSTIC ONLY — no filter state is modified.
# ==============================================================================
def _clock_orbit_separation_diagnostic(all_fwd, all_rts, REF):
    """
    Orbit-vs-clock leakage separation for each auto-detected hump window.
    Uses _FIXED_HUMP_WINDOWS.  Reads only stored per-epoch results dicts.
    No solver changes.
    """
    import math as _math
    import numpy as _np

    NAN = float('nan')
    CONV_SOD = 7200.   # 2 h — ignore pre-convergence
    DT = 30.           # epoch spacing (s)

    # --- geometry helpers ---------------------------------------------------
    lr, lo, _ = _lla(REF)
    Re  = _enu(lr, lo)
    e_up = Re[2, :]   # ECEF Up unit-vector

    def _xyz_to_up_mm(xyz, ref):
        dxyz = (xyz - ref) * 1e3   # mm
        return float(_np.dot(Re[2], dxyz))

    def _corr(a, b):
        """Pearson correlation; returns NaN if insufficient finite pairs."""
        fa = _np.isfinite(a) & _np.isfinite(b)
        if fa.sum() < 10:
            return NAN
        aa, bb = a[fa], b[fa]
        sa, sb = _np.std(aa), _np.std(bb)
        if sa < 1e-12 or sb < 1e-12:
            return NAN
        return float(_np.corrcoef(aa, bb)[0, 1])

    sep  = '=' * 72
    sep2 = '-' * 72

    print(f"\n{sep}")
    print("[CLK_ORB_SEP] Clock vs Orbit Separation Diagnostic  (v91-clkorb)")
    print(f"  Fixed hump windows from _FIXED_HUMP_WINDOWS.")
    print(f"  Common-mode = mean(code_res_all_sats) per epoch  [clock proxy]")
    print(f"  Differential = code_res - common_mode per sat    [orbit proxy]")
    print(sep)

    # Accumulate verdicts across all humps
    _all_verdicts = []   # list of (mode, hump_idx, verdict_char, scores)

    for mode, hump_list in _FIXED_HUMP_WINDOWS.items():
        fwd = all_fwd.get(mode, {})
        if not fwd:
            continue

        print(f"\n{sep2}")
        print(f"[CLK_ORB_SEP]  Mode: {mode}")
        print(sep2)

        # Build sorted SOD list and position/clock series from fwd
        all_sods_sorted = sorted(fwd.keys())
        sods_arr_full   = _np.array(all_sods_sorted, dtype=float)
        up_arr_full     = _np.array([_xyz_to_up_mm(_np.array(fwd[s]['xyz']),
                                                    _np.array(REF))
                                     for s in all_sods_sorted], dtype=float)
        clk_arr_full    = _np.array([fwd[s].get('clk', NAN) * 1e3   # m → mm
                                     for s in all_sods_sorted], dtype=float)

        for hi, hd in enumerate(hump_list):
            sh, eh, pk = hd['start_h'], hd['end_h'], hd['peak_h']
            amp_ref = hd['amp_mm']
            lo_s = sh * 3600.
            hi_s = eh * 3600.

            # SOD mask for this hump window (post-convergence only)
            hmask = (sods_arr_full >= max(lo_s, CONV_SOD)) & (sods_arr_full <= hi_s)
            if hmask.sum() < 5:
                print(f"\n  Hump {hi+1} [{sh:.2f}h–{eh:.2f}h]: insufficient epochs — skip")
                continue

            hump_sods = sods_arr_full[hmask]   # (n_ep,)
            up_h      = up_arr_full[hmask]      # Up error mm in window
            clk_h     = clk_arr_full[hmask]     # receiver clock (mm)
            n_ep      = int(hmask.sum())

            # Baseline window: 1 h before hump start (or CONV_SOD), same width
            bl_lo = max(CONV_SOD, lo_s - 3600.)
            bl_hi = lo_s
            bmask = (sods_arr_full >= bl_lo) & (sods_arr_full < bl_hi)
            up_base_mean = float(_np.nanmean(up_arr_full[bmask])) if bmask.sum() > 0 else NAN
            actual_amp   = float(_np.nanmean(up_h)) - up_base_mean

            print(f"\n  ── Hump {hi+1}  [{sh:.2f}h – {eh:.2f}h]  peak={pk:.2f}h  "
                  f"ref_amp={amp_ref:+.1f}mm  actual_amp≈{actual_amp:+.1f}mm ──")
            print(f"     Window epochs: {n_ep}  (dt={DT:.0f}s)")

            # ── A. Common-mode vs differential decomposition ─────────────────
            # For each epoch in hump window: collect all per-sat code residuals
            common_mode_ep = _np.full(n_ep, NAN)   # mean code_res across all sats
            n_sats_ep      = _np.zeros(n_ep, dtype=int)

            # Epoch-indexed dicts for later per-sat differential
            all_cps_list = []   # list[(sod, code_per_sat_dict)]
            for sod in hump_sods:
                r = fwd.get(sod) or fwd.get(float(sod)) or fwd.get(int(sod))
                if r is None:
                    all_cps_list.append((sod, {}))
                else:
                    all_cps_list.append((sod, r.get('code_per_sat', {})))

            for ep_i, (sod, cps) in enumerate(all_cps_list):
                vals = [res for (res, ldu, el, az) in cps.values()
                        if _math.isfinite(res)]
                if vals:
                    common_mode_ep[ep_i] = float(_np.mean(vals))
                    n_sats_ep[ep_i]      = len(vals)

            # Mean LOS·Up per epoch (to project common-mode into Up direction)
            mean_ldu_ep = _np.full(n_ep, NAN)
            for ep_i, (sod, cps) in enumerate(all_cps_list):
                ldus = [ldu for (res, ldu, el, az) in cps.values()
                        if _math.isfinite(res) and _math.isfinite(ldu)]
                if ldus:
                    mean_ldu_ep[ep_i] = float(_np.mean(ldus))

            # Common-mode Up proxy = common_mode × mean_LOS·Up
            cm_up_proxy_ep = common_mode_ep * mean_ldu_ep   # mm, clock-like Up signal

            # Compute differential per-sat (code_res - common_mode)
            # For each sat, collect diff Up proxy = diff_res × LOS·Up
            sat_diff_oup = {}   # sid -> array of differential orbit-Up proxy (per epoch it appears)
            for ep_i, (sod, cps) in enumerate(all_cps_list):
                cm = common_mode_ep[ep_i]
                if not _math.isfinite(cm):
                    continue
                for sid, (res, ldu, el, az) in cps.items():
                    if not _math.isfinite(res) or not _math.isfinite(ldu):
                        continue
                    diff = res - cm
                    diff_oup = diff * ldu
                    if sid not in sat_diff_oup:
                        sat_diff_oup[sid] = []
                    sat_diff_oup[sid].append(diff_oup)

            # Mean |differential orbit-Up proxy| per sat
            sat_diff_oup_mean = {sid: float(_np.nanmean(_np.abs(v)))
                                 for sid, v in sat_diff_oup.items() if v}
            sat_diff_oup_signed = {sid: float(_np.nanmean(v))
                                   for sid, v in sat_diff_oup.items() if v}

            # ── B. Correlation metrics ────────────────────────────────────────
            print(f"\n  [B] Correlation analysis:")

            # B1. Up error vs receiver-clock state from KF
            corr_up_clk = _corr(up_h, clk_h)
            print(f"      corr(Up_err, KF_clk_state)      = {corr_up_clk:+.3f}  "
                  f"{'⚠ CLOCK-LIKE (|r|>0.5)' if abs(corr_up_clk) > 0.5 else '  orbit/other'}")

            # B2. Up error vs common-mode residual (clock indicator)
            corr_up_cm = _corr(up_h, common_mode_ep)
            print(f"      corr(Up_err, common_mode_res)    = {corr_up_cm:+.3f}  "
                  f"{'⚠ CLOCK LEAKAGE (|r|>0.5)' if abs(corr_up_cm) > 0.5 else '  orbit/other'}")

            # B3. Up error vs common-mode Up proxy
            corr_up_cmup = _corr(up_h, cm_up_proxy_ep)
            print(f"      corr(Up_err, cm_Up_proxy)        = {corr_up_cmup:+.3f}  "
                  f"{'⚠ CLOCK LEAKAGE (|r|>0.5)' if abs(corr_up_cmup) > 0.5 else '  orbit/other'}")

            # B4. Per-sat correlation of differential Up proxy with Up error
            # Top contributors by |diff_oup_mean|
            top_diff_sats = sorted(sat_diff_oup_mean, key=sat_diff_oup_mean.get,
                                   reverse=True)[:5]
            print(f"\n      Per-sat  corr(diff_OUP_s, Up_err)  [top-5 differential]:")
            sat_diff_corr = {}
            for sid in top_diff_sats:
                # Build aligned arrays
                ep_corr_vals = _np.full(n_ep, NAN)
                diff_vals    = _np.full(n_ep, NAN)
                for ep_i, (sod, cps) in enumerate(all_cps_list):
                    cm = common_mode_ep[ep_i]
                    if not _math.isfinite(cm):
                        continue
                    if sid in cps:
                        res, ldu, el, az = cps[sid]
                        if _math.isfinite(res) and _math.isfinite(ldu):
                            diff_vals[ep_i] = (res - cm) * ldu
                c = _corr(up_h, diff_vals)
                sat_diff_corr[sid] = c
                print(f"        {sid:>5}: corr={c:+.3f}  mean_diff_OUP={sat_diff_oup_signed[sid]:+.2f}mm  "
                      f"{'ORBIT SUSPECT' if abs(c) > 0.4 else 'weak'}")

            # ── C. Common-mode magnitude & variance (clock vs orbit split) ────
            print(f"\n  [C] Common-mode / differential power split:")
            fin_cm = common_mode_ep[_np.isfinite(common_mode_ep)]
            cm_mean = float(_np.mean(fin_cm)) if len(fin_cm) > 0 else NAN
            cm_std  = float(_np.std(fin_cm))  if len(fin_cm) > 0 else NAN
            cm_rms  = float(_np.sqrt(_np.mean(fin_cm**2))) if len(fin_cm) > 0 else NAN

            # Total code residual power
            all_res_vals = []
            for sod, cps in all_cps_list:
                for (res, ldu, el, az) in cps.values():
                    if _math.isfinite(res):
                        all_res_vals.append(res)
            total_rms = float(_np.sqrt(_np.mean(_np.array(all_res_vals)**2))) if all_res_vals else NAN

            # Differential rms (residual after removing common-mode)
            diff_res_all = []
            for ep_i, (sod, cps) in enumerate(all_cps_list):
                cm = common_mode_ep[ep_i]
                if not _math.isfinite(cm):
                    continue
                for (res, ldu, el, az) in cps.values():
                    if _math.isfinite(res):
                        diff_res_all.append(res - cm)
            diff_rms = float(_np.sqrt(_np.mean(_np.array(diff_res_all)**2))) if diff_res_all else NAN

            cm_frac  = (cm_rms**2 / total_rms**2) if (total_rms > 0 and _math.isfinite(cm_rms)) else NAN
            orb_frac = (diff_rms**2 / total_rms**2) if (total_rms > 0 and _math.isfinite(diff_rms)) else NAN

            print(f"      Common-mode RMS (clock proxy)  : {cm_rms:7.2f} mm  "
                  f"mean={cm_mean:+.2f}  std={cm_std:.2f}")
            print(f"      Differential RMS (orbit proxy) : {diff_rms:7.2f} mm")
            print(f"      Total code-res RMS             : {total_rms:7.2f} mm")
            print(f"      Power fraction  clock-proxy    : {cm_frac:.3f}")
            print(f"      Power fraction  orbit-proxy    : {orb_frac:.3f}")

            # ── D. Clock-jump detection ───────────────────────────────────────
            # Large epoch-to-epoch jumps in common-mode → clock inconsistency
            print(f"\n  [D] Clock-jump detection (common-mode epoch-to-epoch changes):")
            fin_idx = _np.where(_np.isfinite(common_mode_ep))[0]
            n_jumps_25 = 0; n_jumps_50 = 0; jump_epochs = []
            if len(fin_idx) >= 2:
                cm_seq  = common_mode_ep[fin_idx]
                dcm     = _np.diff(cm_seq)   # epoch-to-epoch change (mm)
                for ji, d in enumerate(dcm):
                    if abs(d) > 50.:
                        n_jumps_50 += 1
                        jump_epochs.append((float(hump_sods[fin_idx[ji]]) / 3600., float(d)))
                    elif abs(d) > 25.:
                        n_jumps_25 += 1
                dcm_rms = float(_np.sqrt(_np.mean(dcm**2)))
                dcm_max = float(_np.max(_np.abs(dcm)))
                print(f"      epoch-to-epoch Δ(common_mode): RMS={dcm_rms:.2f}mm  "
                      f"max={dcm_max:.2f}mm")
                print(f"      Jumps >25mm: {n_jumps_25}   Jumps >50mm: {n_jumps_50}")
                if jump_epochs:
                    for jh, jd in jump_epochs[:5]:
                        print(f"        t={jh:.3f}h  Δcm={jd:+.1f}mm  ← clock transient?")
                if n_jumps_50 >= 3:
                    print(f"      ⚠ Multiple large common-mode jumps → CLOCK INCONSISTENCY likely")
                elif dcm_rms > 15.:
                    print(f"      ⚠ Common-mode epoch scatter elevated → moderate clock noise")
                else:
                    print(f"      Common-mode stable → clock not primary driver of hump")
            else:
                print(f"      Insufficient finite epochs for jump analysis")

            # ── E. New-rise arc test ──────────────────────────────────────────
            # Check which top culprit sats are newly rising at hump start
            print(f"\n  [E] New-rise arc test:")
            PRE_WIN = 1800.   # 30-min pre-hump window
            pre_lo_s = max(CONV_SOD, lo_s - PRE_WIN)
            pre_hi_s = lo_s
            # Satellites seen before hump
            sats_pre = set()
            for sod in all_sods_sorted:
                if pre_lo_s <= sod < pre_hi_s:
                    cps = fwd[sod].get('code_per_sat', {})
                    sats_pre.update(cps.keys())
            # Satellites first appearing in hump
            sats_first_in_hump = {}
            for ep_i, (sod, cps) in enumerate(all_cps_list):
                for sid in cps:
                    if sid not in sats_first_in_hump:
                        sats_first_in_hump[sid] = sod
            new_rise_sats = {sid for sid in sats_first_in_hump
                             if sid not in sats_pre}

            # Top culprits from differential orbit proxy
            top5_orbit_culprits = sorted(sat_diff_oup_mean,
                                         key=sat_diff_oup_mean.get, reverse=True)[:5]
            top5_clock_contrib  = sorted(sat_diff_oup_mean,  # same sats
                                         key=sat_diff_oup_mean.get, reverse=True)[:5]

            # Elevation trend for top sats: rising vs setting
            def _elev_trend(sid):
                elvs = []
                for sod, cps in all_cps_list:
                    if sid in cps:
                        elvs.append(cps[sid][2])   # el_deg
                if len(elvs) < 3:
                    return 'transit'
                de = elvs[-1] - elvs[0]
                return 'rising' if de > 8. else ('setting' if de < -8. else 'transit')

            print(f"      Sats absent in pre-hump 30min, new at hump start: "
                  f"{sorted(new_rise_sats) if new_rise_sats else 'none'}")
            print(f"      Top-5 orbit-differential culprits vs new-rise:")
            n_new_in_top5 = 0
            for sid in top5_orbit_culprits:
                is_new = sid in new_rise_sats
                trend  = _elev_trend(sid)
                oup_v  = sat_diff_oup_mean.get(sid, NAN)
                first_t = sats_first_in_hump.get(sid, NAN)
                first_h = first_t / 3600. if _math.isfinite(first_t) else NAN
                if is_new:
                    n_new_in_top5 += 1
                flag = '← NEW RISE' if is_new else ''
                print(f"        {sid:>5}: diff_OUP={oup_v:+.2f}mm  trend={trend:8s}  "
                      f"first_in_win={first_h:.3f}h  {flag}")
            new_rise_frac = n_new_in_top5 / max(1, len(top5_orbit_culprits))
            print(f"      New-rise fraction of top-5 orbit culprits: {new_rise_frac:.2f}")
            if new_rise_frac >= 0.6:
                print(f"      ⚠ Hump coincides with orbit prediction degradation at arc RISE")
            elif new_rise_frac == 0.:
                print(f"      Culprit sats were already tracked — arc-rise NOT the trigger")

            # ── F. Leave-one-out on orbit-Up proxy (full code_res × LOS·Up) ─
            # For top culprits from differential analysis
            print(f"\n  [F] Leave-one-out: orbit-Up proxy amplitude (remove each culprit):")
            # Baseline: mean of |code_res × LOS·Up| across all sats, all epochs
            def _mean_oup_proxy(exclude_sid=None):
                vals = []
                for sod, cps in all_cps_list:
                    for sid, (res, ldu, el, az) in cps.items():
                        if sid == exclude_sid:
                            continue
                        if _math.isfinite(res) and _math.isfinite(ldu):
                            vals.append(abs(res * ldu))
                return float(_np.mean(vals)) if vals else NAN

            baseline_oup = _mean_oup_proxy()
            print(f"      Baseline mean |OUP| all-sats : {baseline_oup:.2f} mm")
            print(f"      {'Sat':>5}  {'LOO_OUP':>10}  {'ΔOUP':>8}  "
                  f"{'Δ%':>7}  {'HumpCollapses?':}")
            loo_orbit_results = {}
            for sid in top5_orbit_culprits:
                loo_val = _mean_oup_proxy(exclude_sid=sid)
                delta   = loo_val - baseline_oup
                pct     = delta / baseline_oup * 100. if baseline_oup > 0 else NAN
                collapses = ('YES — orbit culprit' if pct < -8.
                             else 'partial' if pct < -3.
                             else 'NO')
                loo_orbit_results[sid] = pct
                print(f"      {sid:>5}  {loo_val:>10.2f}  {delta:>+8.2f}  "
                      f"{pct:>+6.1f}%  {collapses}")

            # ── G. Orbit vs Clock contribution fractions ─────────────────────
            # Orbit-driven fraction: variance explained by differential residuals
            # Clock-driven fraction: variance explained by common-mode
            print(f"\n  [G] Orbit-driven vs Clock-driven contribution fractions:")
            # Top-5 orbit contributor fraction from differential OUP
            total_diff_oup = sum(v for v in sat_diff_oup_mean.values() if _math.isfinite(v))
            for sid in top5_orbit_culprits[:3]:
                v = sat_diff_oup_mean.get(sid, NAN)
                frac = v / total_diff_oup if total_diff_oup > 0 else NAN
                print(f"      Orbit  {sid:>5}: diff_OUP={v:.2f}mm  frac of orbit power={frac:.3f}")
            print(f"      Clock  (common-mode Up proxy): RMS={float(_np.nanstd(cm_up_proxy_ep)):.2f}mm")

            # Signed mean common-mode Up proxy (bias direction)
            cm_up_mean = float(_np.nanmean(cm_up_proxy_ep))
            cm_up_rms  = float(_np.sqrt(_np.nanmean(cm_up_proxy_ep**2)))
            print(f"      Clock  cm_Up_proxy mean={cm_up_mean:+.2f}mm  RMS={cm_up_rms:.2f}mm")

            orbit_power  = diff_rms**2 if _math.isfinite(diff_rms) else NAN
            clock_power  = cm_rms**2  if _math.isfinite(cm_rms)  else NAN
            total_power  = orbit_power + clock_power if (_math.isfinite(orbit_power)
                                                         and _math.isfinite(clock_power)) else NAN
            orb_frac2 = orbit_power / total_power if (total_power and total_power > 0) else NAN
            clk_frac2 = clock_power / total_power if (total_power and total_power > 0) else NAN
            print(f"\n      Residual power split:")
            print(f"        Orbit (differential) fraction : {orb_frac2:.3f}  ({orb_frac2*100:.1f}%)")
            print(f"        Clock (common-mode)  fraction : {clk_frac2:.3f}  ({clk_frac2*100:.1f}%)")

            # ── VERDICT for this hump ─────────────────────────────────────────
            print(f"\n  [VERDICT] Hump {hi+1} ({mode}):")
            orbit_score = 0
            clock_score = 0

            # V1: corr(Up, common_mode) — high → clock
            if _math.isfinite(corr_up_cm) and abs(corr_up_cm) > 0.5:
                clock_score += 2
                print(f"    +2 clock : corr(Up,cm)={corr_up_cm:+.3f} > 0.5")
            elif _math.isfinite(corr_up_cm) and abs(corr_up_cm) < 0.25:
                orbit_score += 1
                print(f"    +1 orbit : corr(Up,cm)={corr_up_cm:+.3f} < 0.25  (clock not driver)")

            # V2: corr(Up, KF_clock) — high → clock absorbing error
            if _math.isfinite(corr_up_clk) and abs(corr_up_clk) > 0.5:
                clock_score += 2
                print(f"    +2 clock : corr(Up,KF_clk)={corr_up_clk:+.3f} > 0.5")
            elif _math.isfinite(corr_up_clk) and abs(corr_up_clk) < 0.25:
                orbit_score += 1
                print(f"    +1 orbit : corr(Up,KF_clk)={corr_up_clk:+.3f} < 0.25")

            # V3: clock-mode fraction — high → clock
            if _math.isfinite(clk_frac2) and clk_frac2 > 0.55:
                clock_score += 2
                print(f"    +2 clock : cm_fraction={clk_frac2:.3f} > 0.55")
            elif _math.isfinite(orb_frac2) and orb_frac2 > 0.55:
                orbit_score += 2
                print(f"    +2 orbit : diff_fraction={orb_frac2:.3f} > 0.55")

            # V4: LOO orbit proxy collapse
            big_loo_collapses = [s for s, pct in loo_orbit_results.items()
                                 if _math.isfinite(pct) and pct < -8.]
            if big_loo_collapses:
                orbit_score += 2
                print(f"    +2 orbit : LOO collapse for {big_loo_collapses}")

            # V5: clock jumps
            if n_jumps_50 >= 3:
                clock_score += 2
                print(f"    +2 clock : {n_jumps_50} common-mode jumps >50mm")
            elif n_jumps_25 >= 5:
                clock_score += 1
                print(f"    +1 clock : {n_jumps_25} common-mode jumps >25mm")

            # V6: new-rise arc
            if new_rise_frac >= 0.6:
                orbit_score += 2
                print(f"    +2 orbit : new-rise arc fraction={new_rise_frac:.2f}")
            elif new_rise_frac == 0.:
                clock_score += 1
                print(f"    +1 clock : no new-rise arcs among top culprits")

            # V7: cm_up_proxy amplitude vs Up error amplitude
            if _math.isfinite(cm_up_rms) and _math.isfinite(actual_amp) and actual_amp != 0.:
                cm_explain_frac = cm_up_rms / abs(actual_amp)
                if cm_explain_frac > 0.5:
                    clock_score += 1
                    print(f"    +1 clock : cm_Up_proxy RMS={cm_up_rms:.1f}mm explains "
                          f"{cm_explain_frac:.0%} of hump amplitude")
                elif cm_explain_frac < 0.15:
                    orbit_score += 1
                    print(f"    +1 orbit : cm_Up_proxy RMS={cm_up_rms:.1f}mm only "
                          f"{cm_explain_frac:.0%} of hump — clock not dominant")

            print(f"\n    Score summary:  orbit={orbit_score}  clock={clock_score}")

            if orbit_score > clock_score + 1:
                verdict_char = 'A'
                verdict_str  = '(A) ORBIT LEAKAGE DOMINATED'
            elif clock_score > orbit_score + 1:
                verdict_char = 'B'
                verdict_str  = '(B) CLOCK INCONSISTENCY DOMINATED'
            else:
                verdict_char = 'C'
                verdict_str  = '(C) MIXED ORBIT-CLOCK LEAKAGE'

            print(f"\n    ══ DECISION: {verdict_str} ══")
            print(f"       corr(Up,cm)={corr_up_cm:+.3f}  corr(Up,KF_clk)={corr_up_clk:+.3f}  "
                  f"cm_frac={clk_frac2:.3f}  orb_frac={orb_frac2:.3f}")
            _all_verdicts.append((mode, hi + 1, verdict_char,
                                  orbit_score, clock_score))

    # ── Overall summary ───────────────────────────────────────────────────────
    print(f"\n{sep}")
    print("[CLK_ORB_SEP] OVERALL VERDICT TABLE")
    print(sep)
    print(f"  {'Mode':<15}  {'Hump':>5}  {'Verdict':>35}  {'Orb':>4}  {'Clk':>4}")
    counts = {'A': 0, 'B': 0, 'C': 0}
    for (mode, hi, vc, os_, cs_) in _all_verdicts:
        label = ({'A': '(A) ORBIT DOMINATED',
                  'B': '(B) CLOCK DOMINATED',
                  'C': '(C) MIXED'}[vc])
        print(f"  {mode:<15}  {hi:>5}  {label:>35}  {os_:>4}  {cs_:>4}")
        counts[vc] += 1
    tot = sum(counts.values())
    print(f"\n  Totals: (A)={counts['A']}/{tot}  (B)={counts['B']}/{tot}  (C)={counts['C']}/{tot}")
    if counts['A'] > counts['B'] and counts['A'] > counts['C']:
        print("\n  ► OVERALL: ORBIT LEAKAGE IS DOMINANT ACROSS HUMPS")
    elif counts['B'] > counts['A'] and counts['B'] > counts['C']:
        print("\n  ► OVERALL: CLOCK INCONSISTENCY IS DOMINANT ACROSS HUMPS")
    else:
        print("\n  ► OVERALL: MIXED MECHANISM — humps driven by both orbit and clock errors")
    print(sep)


# ==============================================================================
#  Early/late hump audit — separate convergence artifact from repeat-geometry
# ==============================================================================
def _early_late_hump_audit(all_fwd, all_rts, REF):
    """
    Treats early (~2h) and late (~21-24h) humps as separate mechanisms.

    Early hump (convergence artifact):
      - Covariance collapse rate: p_trace_pos, p_clk_var, p_zwd_var per epoch
      - Innovation transients: prefit_code_rms vs code_rms (postfit) ratio
      - Filter gain proxy: |dx_up_mm| (Up correction applied per epoch)
      - Reports epoch of half-life collapse, peak gain epoch, innovation spike

    Late hump (repeat-geometry artifact):
      - Per-satellite code residuals in late window vs mid-day baseline
      - Top contributing satellites ranked by Up-projected residual excess
      - Elevation/azimuth of top satellites during late window
      - Sidereal repeat check: GPS sats active in late window also active at
        equivalent time minus one GPS repeat period (~23h 56min = 86164s)
    """
    NAN = float('nan')

    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)

    # Windows (SOD)
    CONV        = 7200.    # 2 h — start of early analysis
    EARLY_HI    = 21600.   # 6 h — end of early analysis
    MID_LO      = 36000.   # 10 h  } mid-day baseline
    MID_HI      = 57600.   # 16 h  }
    LATE_LO     = 72000.   # 20 h  } late window
    LATE_HI     = 86400.   # 24 h  }
    GPS_REPEAT  = 86164.   # sidereal day (s) — GPS ground-track repeat

    def _fmt(v, unit='mm', d=1):
        return f"{v:+.{d}f}{unit}" if v == v else "  N/A"

    sep  = '=' * 72
    sep2 = '-' * 72

    print(f"\n{sep}")
    print("[HUMP_MECH] Early/Late Hump Mechanism Separation")
    print(sep)

    # Use GPS+Galileo as primary, fall back to GPS-only
    label_order = ['GPS+Galileo', 'GPS-only', 'Galileo-only']
    for _lbl in label_order:
        if all_fwd.get(_lbl):
            fwd = all_fwd[_lbl]
            rts = all_rts.get(_lbl, {})
            mode_label = _lbl
            break
    else:
        print("  No FWD results available.")
        return

    sods = np.array(sorted(fwd.keys()), dtype=float)
    u_f  = np.array([(Re @ fwd[k]['dx'])[2] * 1e3 for k in sods])

    # ── EARLY HUMP: covariance collapse + innovation transients ──────────────
    print(f"\n  [EARLY HUMP]  Mode: {mode_label}  Window: {CONV/3600:.0f}h – {EARLY_HI/3600:.0f}h")
    print(sep2)

    early_mask = (sods >= CONV) & (sods <= EARLY_HI)
    es = sods[early_mask]

    def _fv(key, sod):
        return fwd[sod].get(key, NAN) if sod in fwd else NAN

    p_trace_early    = np.array([_fv('p_trace',       s) for s in es])
    p_clk_early      = np.array([_fv('p_clk_var',     s) for s in es])
    p_zwd_early      = np.array([_fv('p_zwd_var',     s) for s in es])
    prefit_c_early   = np.array([_fv('prefit_code_rms',s) for s in es])
    postfit_c_early  = np.array([_fv('code_rms',       s) for s in es])
    dx_up_early      = np.array([_fv('dx_up_mm',       s) for s in es])
    zwd_early        = np.array([_fv('zwd',             s) for s in es])

    # Covariance: first / peak / half-life
    valid_pt = np.isfinite(p_trace_early) & (p_trace_early > 0)
    if valid_pt.sum() > 2:
        pt_start = float(p_trace_early[valid_pt][0])
        pt_end   = float(p_trace_early[valid_pt][-1])
        pt_half  = pt_start / 2.0
        # First epoch where p_trace < half
        half_idx = np.where(valid_pt & (p_trace_early < pt_half))[0]
        half_sod = float(es[half_idx[0]]) if len(half_idx) > 0 else NAN
        print(f"  Covariance collapse (position trace, mm^2 * 1e6):")
        print(f"    p_trace at {CONV/3600:.0f}h     : {math.sqrt(pt_start)*1e3:8.1f} mm (1-sigma equiv)")
        print(f"    p_trace at {EARLY_HI/3600:.0f}h   : {math.sqrt(pt_end)*1e3:8.1f} mm")
        hl_str = f"{half_sod/3600.:.2f}h" if half_sod == half_sod else "N/A"
        print(f"    Half-life (p_trace < 50%)  : SOD {half_sod:.0f} ({hl_str})")

        # Sample table every ~1h
        _tbl_sods = [CONV + i * 3600. for i in range(int((EARLY_HI - CONV) / 3600.) + 1)]
        print(f"\n    {'SOD':>6}  {'h':>5}  {'pos-1sig':>10}  {'clk-1sig':>10}  {'zwd-1sig':>10}")
        for _ts in _tbl_sods:
            # Nearest epoch
            _nearest = sods[np.argmin(np.abs(sods - _ts))]
            if abs(_nearest - _ts) > 120.: continue
            _pt  = fwd[_nearest].get('p_trace',   NAN)
            _pc  = fwd[_nearest].get('p_clk_var', NAN)
            _pz  = fwd[_nearest].get('p_zwd_var', NAN)
            _ps  = math.sqrt(_pt) * 1e3  if _pt == _pt and _pt >= 0 else NAN
            _cs  = math.sqrt(_pc) * 1e3  if _pc == _pc and _pc >= 0 else NAN
            _zs  = math.sqrt(_pz) * 1e3  if _pz == _pz and _pz >= 0 else NAN
            _fs  = lambda v: f"{v:9.1f}mm" if v == v else "       N/A"
            print(f"    {_nearest:>6.0f}  {_nearest/3600.:>5.2f}  {_fs(_ps)}  {_fs(_cs)}  {_fs(_zs)}")

    # Innovation transients
    valid_inn = np.isfinite(prefit_c_early) & np.isfinite(postfit_c_early) & (postfit_c_early > 0)
    if valid_inn.sum() > 5:
        ratio = prefit_c_early[valid_inn] / postfit_c_early[valid_inn]
        peak_ratio_idx = int(np.argmax(ratio))
        all_valid_idx  = np.where(valid_inn)[0]
        peak_sod_inn   = float(es[all_valid_idx[peak_ratio_idx]])
        print(f"\n  Innovation transients (prefit_code_rms / postfit_code_rms):")
        print(f"    Peak ratio {ratio[peak_ratio_idx]:.2f}x at SOD {peak_sod_inn:.0f}"
              f" ({peak_sod_inn/3600.:.2f}h)"
              f"  [prefit={prefit_c_early[valid_inn][peak_ratio_idx]:.1f}mm"
              f"  postfit={postfit_c_early[valid_inn][peak_ratio_idx]:.1f}mm]")
        # Table every ~30 min in first 3h
        print(f"    {'SOD':>6}  {'h':>5}  {'prefit':>10}  {'postfit':>9}  {'ratio':>7}")
        for _ts in np.arange(CONV, CONV + 10801., 1800.):
            _nr = sods[np.argmin(np.abs(sods - _ts))]
            if abs(_nr - _ts) > 120.: continue
            _pf = fwd[_nr].get('prefit_code_rms', NAN)
            _po = fwd[_nr].get('code_rms',        NAN)
            _rt = (_pf / _po) if (_pf == _pf and _po == _po and _po > 0) else NAN
            print(f"    {_nr:>6.0f}  {_nr/3600.:>5.2f}  "
                  f"{_pf:>8.1f}mm  {_po:>7.1f}mm  "
                  f"{'N/A' if _rt != _rt else f'{_rt:>6.2f}x':>7}")

    # Filter gain evolution: |dx_up_mm|
    valid_dx = np.isfinite(dx_up_early)
    if valid_dx.sum() > 5:
        dx_abs = np.abs(dx_up_early[valid_dx])
        peak_dx     = float(dx_abs.max())
        peak_dx_idx = int(np.argmax(dx_abs))
        peak_dx_sod = float(es[np.where(valid_dx)[0][peak_dx_idx]])
        # Epoch where |dx_up| first drops below 5 mm and stays below for 5 epochs
        _below5 = dx_abs < 5.0
        _stable = np.zeros(len(_below5), dtype=bool)
        for _bi in range(len(_below5) - 4):
            if np.all(_below5[_bi:_bi + 5]):
                _stable[_bi] = True
        stable_idx = np.where(_stable)[0]
        stable_sod = float(es[np.where(valid_dx)[0][stable_idx[0]]]) \
                     if len(stable_idx) > 0 else NAN
        print(f"\n  Filter gain (Up correction |dx_up_mm| per epoch):")
        print(f"    Peak |dx_up| : {peak_dx:+.1f} mm at SOD {peak_dx_sod:.0f}"
              f" ({peak_dx_sod/3600.:.2f}h)")
        stable_str = f"SOD {stable_sod:.0f} ({stable_sod/3600.:.2f}h)" \
                     if stable_sod == stable_sod else "not reached"
        print(f"    Stable (<5mm): {stable_str}")
        # Table every 30 min
        print(f"    {'SOD':>6}  {'h':>5}  {'dx_up_mm':>10}")
        for _ts in np.arange(CONV, CONV + 10801., 1800.):
            _nr = sods[np.argmin(np.abs(sods - _ts))]
            if abs(_nr - _ts) > 120.: continue
            _dx = fwd[_nr].get('dx_up_mm', NAN)
            print(f"    {_nr:>6.0f}  {_nr/3600.:>5.2f}  {_dx:>+9.2f}mm"
                  if _dx == _dx else f"    {_nr:>6.0f}  {_nr/3600.:>5.2f}  {'N/A':>10}")

    # ── LATE HUMP: per-satellite geometry and residuals ───────────────────────
    print(f"\n{sep2}")
    print(f"  [LATE HUMP]  Window: {LATE_LO/3600:.0f}h – {LATE_HI/3600:.0f}h  "
          f"vs mid-day baseline {MID_LO/3600:.0f}h – {MID_HI/3600:.0f}h")
    print(sep2)

    # Accumulate per-satellite code residuals and elevation in each window
    # Structure: {sid: {'res_late':[], 'el_late':[], 'az_late':[],
    #                    'res_mid':[],  'el_mid':[],
    #                    'ph_l1_late':[], 'ph_l2_late':[]}}
    sat_acc = defaultdict(lambda: {
        'res_late': [], 'el_late': [], 'az_late': [],
        'res_mid':  [], 'el_mid':  [],
        'ph_l1_late': [], 'ph_l2_late': [],
        'sod_late': [], 'sod_mid': [],
        'arc_start_late': None,  # first SOD the sat appeared in late window
    })

    for sod_k in sorted(fwd.keys()):
        r = fwd[sod_k]
        cps = r.get('code_per_sat', {})
        ppps = r.get('phase_prefit_per_sat', {})
        in_late = LATE_LO <= sod_k <= LATE_HI
        in_mid  = MID_LO  <= sod_k <= MID_HI
        if not in_late and not in_mid:
            continue
        for sid_k, tup in cps.items():
            res_mm, ldu, el_deg = tup[0], tup[1], tup[2]
            az_deg = tup[3] if len(tup) > 3 else 0.
            acc = sat_acc[sid_k]
            if in_late:
                acc['res_late'].append(res_mm)
                acc['el_late'].append(el_deg)
                acc['az_late'].append(az_deg)
                acc['sod_late'].append(sod_k)
                if acc['arc_start_late'] is None:
                    acc['arc_start_late'] = sod_k
            if in_mid:
                acc['res_mid'].append(res_mm)
                acc['el_mid'].append(el_deg)
                acc['sod_mid'].append(sod_k)
        for sid_k, ph_tup in ppps.items():
            if in_late and len(ph_tup) >= 2:
                sat_acc[sid_k]['ph_l1_late'].append(ph_tup[0])
                sat_acc[sid_k]['ph_l2_late'].append(ph_tup[1])

    # Compute per-sat summary
    sat_summary = {}
    for sid_k, acc in sat_acc.items():
        if not acc['res_late']:
            continue
        res_l = np.array(acc['res_late'])
        el_l  = np.array(acc['el_late'])
        az_l  = np.array(acc['az_late'])
        res_m = np.array(acc['res_mid']) if acc['res_mid'] else np.array([])
        el_m  = np.array(acc['el_mid'])  if acc['el_mid']  else np.array([])

        # Up-projected residual excess: |res| * sin(el) - baseline
        # sin(el) because that's the Up component of the LOS unit vector
        sin_el_l  = np.sin(np.radians(el_l))
        up_res_l  = np.abs(res_l) * sin_el_l
        mean_up_l = float(np.mean(up_res_l))

        if len(res_m) > 0:
            sin_el_m  = np.sin(np.radians(el_m))
            up_res_m  = np.abs(res_m) * sin_el_m
            mean_up_m = float(np.mean(up_res_m))
            up_excess = mean_up_l - mean_up_m
        else:
            mean_up_m = NAN
            up_excess = NAN

        # Phase residual RMS in late window
        ph1 = np.array(acc['ph_l1_late'])
        ph_rms = float(np.sqrt(np.mean(ph1**2))) if len(ph1) > 0 else NAN

        # Arc continuity: did this sat just rise in late window?
        arc_s = acc['arc_start_late']
        arc_gap = (arc_s - LATE_LO) if arc_s is not None else NAN
        new_rise = arc_s is not None and arc_gap < 1800.  # rose within 30 min of window start

        sat_summary[sid_k] = {
            'n_late':    len(res_l),
            'n_mid':     len(res_m),
            'mean_code_late': float(np.mean(np.abs(res_l))),
            'mean_code_mid':  float(np.mean(np.abs(res_m))) if len(res_m) > 0 else NAN,
            'mean_el_late':   float(np.mean(el_l)),
            'mean_az_late':   float(np.mean(az_l)),
            'mean_up_res_late': mean_up_l,
            'mean_up_res_mid':  mean_up_m,
            'up_excess':      up_excess,
            'ph_rms_late':    ph_rms,
            'new_rise':       new_rise,
            'arc_start_late': arc_s,
            'sys': sid_k[0],
        }

    # Rank by up_excess (highest contribution to late hump)
    ranked = sorted(
        [(s, v) for s, v in sat_summary.items() if v['up_excess'] == v['up_excess']],
        key=lambda x: x[1]['up_excess'], reverse=True)

    print(f"\n  Top satellites by Up-projected code residual excess (late vs mid-day):")
    print(f"  {'Sat':>4}  {'Sys':>3}  {'n_late':>7}  {'code_late':>10}  "
          f"{'code_mid':>9}  {'up_excess':>10}  {'el_late':>8}  "
          f"{'az_late':>8}  {'ph_rms':>8}  {'new_rise':>9}")
    print(f"  {'-'*4}  {'-'*3}  {'-'*7}  {'-'*10}  "
          f"{'-'*9}  {'-'*10}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*9}")
    for sid_k, v in ranked[:12]:
        _fmt_mm = lambda x: f"{x:8.1f}mm" if x == x else "     N/A"
        print(f"  {sid_k:>4}  {v['sys']:>3}  {v['n_late']:>7d}  "
              f"{_fmt_mm(v['mean_code_late']):>10}  "
              f"{_fmt_mm(v['mean_code_mid']):>9}  "
              f"{_fmt_mm(v['up_excess']):>10}  "
              f"{v['mean_el_late']:>7.1f}deg  "
              f"{v['mean_az_late']:>7.1f}deg  "
              f"{_fmt_mm(v['ph_rms_late']):>8}  "
              f"{'YES' if v['new_rise'] else 'no':>9}")

    # Satellites that are new-rise in late window
    new_risers = [(s, v) for s, v in sat_summary.items() if v['new_rise']]
    if new_risers:
        print(f"\n  New-rise satellites in late window (rose within 30 min of SOD {LATE_LO:.0f}):")
        for sid_k, v in sorted(new_risers, key=lambda x: x[1]['arc_start_late'] or 0):
            arc_str = f"SOD {v['arc_start_late']:.0f} ({v['arc_start_late']/3600.:.2f}h)" \
                      if v['arc_start_late'] else "N/A"
            print(f"    {sid_k}  first seen: {arc_str}  el_mean={v['mean_el_late']:.1f}deg")

    # Sidereal repeat check: GPS sats in late window that were also visible
    # at (arc_start_late - GPS_REPEAT), i.e. same sky position yesterday
    print(f"\n  Sidereal repeat check (GPS only, repeat ~{GPS_REPEAT/3600.:.1f}h):")
    print(f"  Sats in late window vs same sat at t - {GPS_REPEAT/3600.:.2f}h (early-day):")
    gps_late = {s: v for s, v in sat_summary.items() if v['sys'] == 'G'}
    for sid_k, v in sorted(gps_late.items(), key=lambda x: x[1]['up_excess'], reverse=True)[:8]:
        # Check if this satellite had an arc starting ~GPS_REPEAT seconds earlier
        arc_s   = v['arc_start_late']
        if arc_s is None: continue
        repeat_sod = arc_s - GPS_REPEAT
        # Is repeat_sod within the observed day?
        repeat_in_day = any(abs(sod_k - repeat_sod) < 300.
                            for sod_k in fwd.keys()
                            if sid_k in fwd[sod_k].get('code_per_sat', {}))
        # Find elevation at repeat time
        el_repeat = NAN
        for sod_k in sorted(fwd.keys()):
            if abs(sod_k - repeat_sod) < 300.:
                cps = fwd[sod_k].get('code_per_sat', {})
                if sid_k in cps:
                    el_repeat = cps[sid_k][2]
                    break
        el_match = (abs(el_repeat - v['mean_el_late']) < 15.) \
                   if (el_repeat == el_repeat) else False
        repeat_str = (f"YES (el_early={el_repeat:.1f}deg, el_late={v['mean_el_late']:.1f}deg, "
                      f"match={'YES' if el_match else 'NO'})")  \
                     if repeat_in_day else \
                     f"BEFORE DAY START (t-repeat={repeat_sod/3600.:.1f}h)"
        print(f"    {sid_k}  late_arc_start={arc_s/3600.:.2f}h  "
              f"up_excess={v['up_excess']:+.1f}mm  repeat: {repeat_str}")

    # ── Summary decision ─────────────────────────────────────────────────────
    print(f"\n{sep2}")
    print("  Mechanism separation summary:")
    # Early: check if p_trace collapses fast enough and innovations settle
    if valid_pt.sum() > 2 and 'stable_sod' in dir():
        if stable_sod == stable_sod and stable_sod < EARLY_HI:
            print(f"  EARLY hump: filter SETTLED by {stable_sod/3600.:.2f}h (|dx_up|<5mm).")
            print(f"    -> Early hump is a CONVERGENCE TRANSIENT, not systematic error.")
        else:
            print(f"  EARLY hump: filter NOT fully settled in {EARLY_HI/3600:.0f}h window.")
            print(f"    -> Slow convergence may contribute to persistent Up bias.")

    # Late: check if top contributors are new-rise or repeat-geometry sats
    top5_new = sum(1 for _, v in ranked[:5] if v['new_rise'])
    top5_up  = [v['up_excess'] for _, v in ranked[:5] if v['up_excess'] == v['up_excess']]
    if top5_up:
        print(f"  LATE hump: top-5 sats have up_excess range "
              f"[{min(top5_up):.1f}, {max(top5_up):.1f}] mm.")
        if top5_new >= 3:
            print(f"    -> {top5_new}/5 top sats are new-rise: late hump driven by"
                  f" low-elevation orbit/iono transients.")
        else:
            print(f"    -> {top5_new}/5 top sats are new-rise: late hump may be"
                  f" orbit/clock product residual or geometry-driven.")

    print(sep)


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
    erp_f=_f(['.erp','.ERP'])   # earth rotation parameters (for pole tide)

    print("="*72)
    print("GPS+Galileo PPP v102 — ZWD Dynamic Lag Test (pure float)")
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

    # Earth Rotation Parameters (for pole tide ablation)
    erp_base=parse_erp(erp_f) if erp_f else {}
    if erp_base:
        print(f"[ERP]  {len(erp_base)} records — available for pole tide ablation run B")
    else:
        print(f"[ERP]  No ERP file found — pole tide ablation run B will be skipped")

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
                 blq=blq,sta=sta_name,erp=None)

    # ======================================================================
    #  v105 — Permanent elevation-dependent weighting (pure float)
    #  Phase: sigma = SP / sin(el)^2   Code: sigma = SC / sin(el)^1
    #  Applied ALWAYS, all epochs, all constellations.
    #  No hump-window logic. No adaptive weighting.
    #  Runs: GPS-only, Galileo-only, GPS+Galileo
    # ======================================================================
    _lr_v, _lo_v, _ = _lla(REF)
    _Re_v = _enu(_lr_v, _lo_v)

    def _v105_series(fwd_d):
        items = sorted(fwd_d.items())
        sods  = np.array([s for s, _ in items])
        dx    = np.array([r['dx'] for _, r in items])
        enu   = (_Re_v @ dx.T).T * 1e3
        e_mm  = enu[:, 0]
        n_mm  = enu[:, 1]
        u_mm  = enu[:, 2]
        d3_mm = np.linalg.norm(dx, axis=1) * 1e3
        nl_fix= np.array([r.get('nl_fixed', 0) for _, r in items], dtype=float)
        n_sat = np.array([r.get('n', 0) for _, r in items], dtype=float)
        return sods, e_mm, n_mm, u_mm, d3_mm, nl_fix, n_sat

    def _v105_rms(sods, vals, conv_sod=7200.):
        post = sods >= conv_sod
        return float(np.sqrt(np.mean(vals[post]**2))) if post.sum() > 0 else float('nan')

    _V105_RUNS = [
        ('G',  'GPS-only'),
        ('E',  'Galileo-only'),
        ('GE', 'GPS+Galileo'),
    ]
    _run_results_v105 = {}

    print(f"\n{'='*72}")
    print("[IF_v1] GPS+Galileo PPP — ZWD Q×1.5 restored + prior σ=8cm + height gate=3h/20cm (pure float)")
    print(f"{'='*72}")

    print("\n[OSB_AUDIT]")
    print("  mode=DYNAMIC_PER_SAT_PER_SIGNAL")
    print("  where_applied=_proc/_proc_gal BEFORE measurement model (P1c/P2c/L1m/L2m)")
    print("  signals_used=GPS:C1W/L1W+C2W/L2W (or C1C/L1C fallback)  GAL:C1C/L1C+C5Q/L5Q")
    print("  is_consistent=True  (all 4 observables corrected; missing → no_AR+raw fallback)")
    print("  removed=EMA_fractional_bias_workaround (was phase-only, inconsistent)")
    print("[OSB_AUDIT end]\n")

    for _const, _const_lbl in _V105_RUNS:
        _lbl_105 = f"FWD_{_const}_v105"
        print(f"\n[v105] {_const_lbl}")
        _rts_store._data = []
        _fwd_105, _ex_105, *_rest_105 = _ppp_pass(
            epochs, nom=APX.copy(), iclk=0., izwd=0.20,
            direction=1, label=_lbl_105, constellation=_const,
            el_phase_boost_wins=None,
            iono_mode='base',
            iono_hump_wins=None,
            phase_elev_exp=1.5,
            **_common)

        # PART 1 — run RTS smoother immediately while _rts_store._data is populated
        # PART 2/3 — extract FWD and RTS XYZ arrays, aligned by sod
        _fwd_pos_list = []   # [(sod, xyz_fwd), ...]
        _rts_pos_list = []   # [(sod, xyz_rts), ...]
        print(f"RTS length: {len(_rts_store._data)}")
        if len(_rts_store._data) == 0:
            raise RuntimeError("RTS NOT RUNNING — _rts_store._data is empty after forward pass")
        _rts_smoothed_105 = _rts_smooth(_fwd_105, APX.copy())
        # PART 4 — collect aligned FWD / RTS positions by sod
        _common_sods = sorted(set(_fwd_105.keys()) & set(_rts_smoothed_105.keys()))
        for _cs in _common_sods:
            _fwd_xyz = _fwd_105[_cs]['xyz']
            _rts_xyz = _rts_smoothed_105[_cs]['xyz']
            _fwd_pos_list.append((_cs, _fwd_xyz))
            _rts_pos_list.append((_cs, _rts_xyz))
        _N_rts = len(_fwd_pos_list)   # already aligned by construction
        print(f"FWD length: {len(_fwd_105)}  aligned RTS/FWD pairs: {_N_rts}")
        # PART 5 — compute |FWD - RTS| in mm
        _rts_diff_sods = np.array([s for s, _ in _fwd_pos_list])
        _rts_diff_mm   = np.array([
            np.linalg.norm(_fwd_pos_list[_i][1] - _rts_pos_list[_i][1]) * 1e3
            for _i in range(_N_rts)])

        _s105, _e105, _n105, _u105, _d3_105, _nl105, _nsat105 = _v105_series(_fwd_105)
        _urms = _v105_rms(_s105, _u105)
        _drms = _v105_rms(_s105, _d3_105)
        _e3   = float(np.linalg.norm(_ex_105 - REF) * 1e3)
        print(f"  {len(_fwd_105)} epochs  end_3D={_e3:.1f}mm"
              f"  Up_RMS={_urms:.1f}mm  3D_RMS={_drms:.1f}mm")
        _run_results_v105[_const] = dict(
            sods=_s105, e_mm=_e105, n_mm=_n105, u_mm=_u105,
            d3_mm=_d3_105, nl_fix=_nl105, n_sat=_nsat105,
            ex=_ex_105, fwd=_fwd_105,
            rts_diff_sods=_rts_diff_sods, rts_diff_mm=_rts_diff_mm)

        # ── ELEV_AUDIT: detrended elevation-bin diagnostic ─────────────────────
        def _elev_audit(fwd_d, Re, label_a, sod_start=None):
            """Detrended elevation-bin diagnostic.
            Bins: 15-30°, 30-50°, 50°+
            Segments: EARLY 0-3h, MID 3-12h, LATE 12-24h (relative to first epoch)
            """
            _BIN_EDGES = [15., 30., 50., 91.]
            _BIN_NAMES = ['15-30°', '30-50°', '50°+  ']
            _SEGS = [('EARLY', 0., 3.), ('MID  ', 3., 12.), ('LATE ', 12., 25.)]
            _MIN_N = 20   # flag bins with fewer samples

            items = sorted(fwd_d.items())
            if not items: return
            sods_a = np.array([s for s, _ in items])
            dx_a   = np.array([r['dx'] for _, r in items])
            enu_a  = (Re @ dx_a.T).T * 1e3
            u_mm_a = enu_a[:, 2]

            # detrend: subtract moving average (window=600 epochs ~5h at 30s)
            _win = min(600, max(1, len(u_mm_a) // 4))
            _pad = _win // 2
            _u_ma = np.convolve(u_mm_a, np.ones(_win) / _win, mode='full')
            # trim to same length, centred
            _u_ma = _u_ma[_pad: _pad + len(u_mm_a)]
            u_det = u_mm_a - _u_ma   # detrended Up

            t0 = float(sods_a[0]) if sod_start is None else float(sod_start)

            print(f"\n[ELEV_AUDIT] label={label_a}  detrend_win={_win}ep")
            print(f"  {'Seg':<6} {'Bin':<7} {'n':>6} {'mean_el':>8} "
                  f"{'Up_RMS':>9} {'phase_RMS':>10} {'code_RMS':>10} {'flag'}")
            print(f"  {'-'*6} {'-'*7} {'-'*6} {'-'*8} {'-'*9} {'-'*10} {'-'*10} {'-'*4}")

            for _seg_name, _sh, _eh in _SEGS:
                _seg_mask = ((sods_a - t0) >= _sh * 3600.) & \
                            ((sods_a - t0) <  _eh * 3600.)
                _seg_sods = sods_a[_seg_mask]
                _seg_udet = u_det[_seg_mask]
                if len(_seg_sods) == 0: continue

                # accumulate per-bin: [up_sq_sum, ph_sq_sum, cd_sq_sum, el_sum, n]
                _bins = [[0., 0., 0., 0., 0] for _ in _BIN_NAMES]

                for _si, _sod in enumerate(_seg_sods):
                    _r = fwd_d.get(float(_sod)) or fwd_d.get(int(_sod))
                    if _r is None: continue
                    _u_ep = float(_seg_udet[_si])
                    _ph_ps = _r.get('phase_prefit_per_sat', {})
                    _cd_ps = _r.get('code_per_sat', {})
                    _all_sids = set(_ph_ps) | set(_cd_ps)
                    for _sid in _all_sids:
                        # get elevation
                        if _sid in _ph_ps:
                            _el_d = float(_ph_ps[_sid][2])
                        elif _sid in _cd_ps:
                            _el_d = float(_cd_ps[_sid][2])
                        else:
                            continue
                        # phase RMS: mean of L1/L2 prefit
                        if _sid in _ph_ps:
                            _ph_v = math.sqrt((_ph_ps[_sid][0]**2 + _ph_ps[_sid][1]**2) / 2.) * 1e3
                        else:
                            _ph_v = float('nan')
                        # code residual mm
                        if _sid in _cd_ps:
                            _cd_v = abs(float(_cd_ps[_sid][0]))
                        else:
                            _cd_v = float('nan')
                        # bin
                        for _bi, (_lo, _hi) in enumerate(
                                zip(_BIN_EDGES[:-1], _BIN_EDGES[1:])):
                            if _lo <= _el_d < _hi:
                                _bins[_bi][0] += _u_ep ** 2
                                if math.isfinite(_ph_v):
                                    _bins[_bi][1] += _ph_v ** 2
                                if math.isfinite(_cd_v):
                                    _bins[_bi][2] += _cd_v ** 2
                                _bins[_bi][3] += _el_d
                                _bins[_bi][4] += 1
                                break

                for _bi, _bn in enumerate(_BIN_NAMES):
                    _us, _ps, _cs, _es, _n = _bins[_bi]
                    if _n == 0:
                        print(f"  {_seg_name:<6} {_bn:<7} {'0':>6}  {'---':>8} "
                              f"{'---':>9} {'---':>10} {'---':>10} LOW")
                        continue
                    _up_r  = math.sqrt(_us / _n)
                    _ph_r  = math.sqrt(_ps / _n)
                    _cd_r  = math.sqrt(_cs / _n)
                    _me    = _es / _n
                    _flag  = "LOW" if _n < _MIN_N else ""
                    print(f"  {_seg_name:<6} {_bn:<7} {_n:>6d}  {_me:>7.1f}° "
                          f"  {_up_r:>7.1f}mm  {_ph_r:>8.1f}mm  {_cd_r:>8.1f}mm  {_flag}")
            print()

        _elev_audit(_fwd_105, _Re_v, f"{_const_lbl}")
        # ── end ELEV_AUDIT ─────────────────────────────────────────────────────

    # ── Results summary ────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("[IF_v1] RESULTS SUMMARY  (pure float, v120: ZWD Q×0.3 + prior=3cm + h-gate=1.5h + prior σ=8cm + height gate=3h/R=(0.20)²)")
    print(f"{'='*72}")
    print(f"  {'Mode':<15} {'Up_RMS':>9} {'3D_RMS':>9} {'end_3D':>9} {'to_10cm':>8} {'to_5cm':>8}")
    print(f"  {'-'*15} {'-'*9} {'-'*9} {'-'*9} {'-'*8} {'-'*8}")
    for _const, _const_lbl in _V105_RUNS:
        _rr = _run_results_v105.get(_const)
        if _rr is None: continue
        _ur  = _v105_rms(_rr['sods'], _rr['u_mm'])
        _dr  = _v105_rms(_rr['sods'], _rr['d3_mm'])
        _e3  = float(np.linalg.norm(_rr['ex'] - REF) * 1e3)
        # time_to_10cm / time_to_5cm: first epoch where 3D stays ≤ threshold
        def _first_below(sods_a, d3_a, thresh_mm, window=10):
            for _ti in range(len(d3_a) - window + 1):
                if np.all(d3_a[_ti:_ti+window] <= thresh_mm):
                    return sods_a[_ti] / 3600.
            return float('nan')
        _t10 = _first_below(_rr['sods'], _rr['d3_mm'], 100.)
        _t5  = _first_below(_rr['sods'], _rr['d3_mm'],  50.)
        _t10s = f"{_t10:.2f}h" if not math.isnan(_t10) else "  --  "
        _t5s  = f"{_t5:.2f}h"  if not math.isnan(_t5)  else "  --  "
        print(f"  {_const_lbl:<15} {_ur:>9.1f} {_dr:>9.1f} {_e3:>9.1f} {_t10s:>8} {_t5s:>8}")

    # ── Verdict ────────────────────────────────────────────────────────────────
    _rr_ge = _run_results_v105.get('GE')
    _rr_g  = _run_results_v105.get('G')
    _rr_e  = _run_results_v105.get('E')
    if _rr_ge and _rr_g and _rr_e:
        _ur_ge = _v105_rms(_rr_ge['sods'], _rr_ge['u_mm'])
        _ur_g  = _v105_rms(_rr_g['sods'],  _rr_g['u_mm'])
        _ur_e  = _v105_rms(_rr_e['sods'],  _rr_e['u_mm'])
        _dr_ge = _v105_rms(_rr_ge['sods'], _rr_ge['d3_mm'])
        _dr_g  = _v105_rms(_rr_g['sods'],  _rr_g['d3_mm'])
        _dr_e  = _v105_rms(_rr_e['sods'],  _rr_e['d3_mm'])
        _ge_beats = (_ur_ge < _ur_g) and (_ur_ge < _ur_e)
        _ge_3d    = (_dr_ge < _dr_g) and (_dr_ge < _dr_e)
        if _ge_beats and _ge_3d:
            print("GEOMETRY-WEIGHTING FIX SUCCESSFUL")
        else:
            print("Residual geometry imbalance remains")

    # ── Plots ──────────────────────────────────────────────────────────────────
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        _cols = {'G': '#1f77b4', 'E': '#d62728', 'GE': '#2ca02c'}
        _lbls = {'G': 'GPS-only', 'E': 'Galileo-only', 'GE': 'GPS+Galileo'}

        # PART 1 — auto-scaling helper (skips first 1 h convergence spike)
        _CUT_SOD = 3600.  # ignore first 1 h for axis scaling

        def _auto_ylim(ax, arrays, margin=0.10, min_range=50., bottom_zero=False):
            """Set ax ylim from post-convergence data, ignoring initial spike."""
            vals = []
            for arr in arrays:
                arr = np.asarray(arr)
                if arr.ndim == 0 or len(arr) == 0:
                    continue
                vals.append(arr[~np.isnan(arr)])
            if not vals:
                return
            data = np.concatenate(vals)
            if len(data) == 0:
                return
            dmin, dmax = float(np.nanmin(data)), float(np.nanmax(data))
            center = 0.5 * (dmin + dmax)
            span   = max(dmax - dmin, float(min_range))
            span  *= (1.0 + margin)
            lo = center - span / 2.
            hi = center + span / 2.
            if bottom_zero:
                lo = max(lo, 0.)
            ax.set_ylim(lo, hi)

        def _cut(sods_arr, vals_arr, cut_sod=_CUT_SOD):
            """Return vals_arr[sods > cut_sod] for axis scaling."""
            mask = np.asarray(sods_arr) > cut_sod
            return np.asarray(vals_arr)[mask]

        # ── Original 2-panel Up / 3D plot ─────────────────────────────────────
        fig_v, axes = plt.subplots(2, 1, figsize=(13, 9))
        fig_v.suptitle(
            'PPP IF v1 — ZWD Q×1.5 restored + Prior σ=8cm + Height Gate=3h/20cm  (pure float)',
            fontsize=11, fontweight='bold')

        ax1 = axes[0]
        _up_cuts = []
        for _const, _ in _V105_RUNS:
            _rr = _run_results_v105.get(_const)
            if _rr is None: continue
            ax1.plot(_rr['sods']/3600., _rr['u_mm'],
                     color=_cols[_const], lw=1.0, label=_lbls[_const])
            _up_cuts.append(_cut(_rr['sods'], _rr['u_mm']))
        ax1.axhline(0, color='k', lw=0.5)
        ax1.set_ylabel('Up Error (mm)')
        ax1.set_xlim(0, 25)
        _auto_ylim(ax1, _up_cuts)
        ax1.legend(fontsize=9, loc='upper right')
        ax1.grid(True, alpha=0.3)
        ax1.set_title('Up Error — GPS / Galileo / Combined')

        ax2 = axes[1]
        _d3_cuts = []
        for _const, _ in _V105_RUNS:
            _rr = _run_results_v105.get(_const)
            if _rr is None: continue
            ax2.plot(_rr['sods']/3600., _rr['d3_mm'],
                     color=_cols[_const], lw=1.0, label=_lbls[_const])
            _d3_cuts.append(_cut(_rr['sods'], _rr['d3_mm']))
        ax2.set_ylabel('3D Error (mm)')
        ax2.set_xlim(0, 25)
        _auto_ylim(ax2, _d3_cuts, bottom_zero=True)
        ax2.legend(fontsize=9, loc='upper right')
        ax2.grid(True, alpha=0.3)
        ax2.set_title('3D Error — GPS / Galileo / Combined')

        axes[-1].set_xlabel('Time (h)')
        plt.tight_layout()
        _vp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'ppp_v110_zwd_balance.png')
        fig_v.savefig(_vp, dpi=150, bbox_inches='tight')
        print(f"\n[IF_v1] Plot saved: {_vp}")
        plt.close(fig_v)

        # ── 4-subplot full analysis figure ────────────────────────────────────
        fig_a, axs_a = plt.subplots(4, 1, figsize=(14, 16), sharex=True)
        fig_a.suptitle('PPP IF v1 — Full Analysis (IF float)',
                        fontsize=12, fontweight='bold')

        # Subplot 1 — 3D error + convergence thresholds
        ax_3d = axs_a[0]
        _d3c = []
        for _const, _ in _V105_RUNS:
            _rr = _run_results_v105.get(_const)
            if _rr is None: continue
            ax_3d.plot(_rr['sods']/3600., _rr['d3_mm'],
                       color=_cols[_const], lw=1.0, label=_lbls[_const])
            _d3c.append(_cut(_rr['sods'], _rr['d3_mm']))
        ax_3d.axhline(100., color='orange', lw=1.2, ls='--', label='10 cm')
        ax_3d.axhline( 50., color='green',  lw=1.2, ls='--', label='5 cm')
        ax_3d.set_ylabel('3D Error (mm)')
        _auto_ylim(ax_3d, _d3c, bottom_zero=True)
        ax_3d.legend(fontsize=8, loc='upper right', ncol=2)
        ax_3d.grid(True, alpha=0.3)
        ax_3d.set_title('3D Error with Convergence Thresholds')

        # Subplot 2 — ENU components (GPS+Galileo combined only)
        ax_enu = axs_a[1]
        _rr_ge2 = _run_results_v105.get('GE')
        _enu_cuts = []
        if _rr_ge2 is not None:
            _t_h = _rr_ge2['sods'] / 3600.
            ax_enu.plot(_t_h, _rr_ge2['e_mm'], color='#e377c2', lw=0.9, label='East')
            ax_enu.plot(_t_h, _rr_ge2['n_mm'], color='#17becf', lw=0.9, label='North')
            ax_enu.plot(_t_h, _rr_ge2['u_mm'], color='#ff7f0e', lw=0.9, label='Up')
            for _k in ('e_mm', 'n_mm', 'u_mm'):
                _enu_cuts.append(_cut(_rr_ge2['sods'], _rr_ge2[_k]))
        ax_enu.axhline(0, color='k', lw=0.5)
        ax_enu.set_ylabel('Error (mm)')
        _auto_ylim(ax_enu, _enu_cuts)
        ax_enu.legend(fontsize=8, loc='upper right')
        ax_enu.grid(True, alpha=0.3)
        ax_enu.set_title('ENU Errors (GPS+Galileo)')

        # Subplot 3 — NL fix count
        ax_nl = axs_a[2]
        _nlc = []
        for _const, _ in _V105_RUNS:
            _rr = _run_results_v105.get(_const)
            if _rr is None: continue
            ax_nl.plot(_rr['sods']/3600., _rr['nl_fix'],
                       color=_cols[_const], lw=1.0, label=_lbls[_const])
            _nlc.append(_cut(_rr['sods'], _rr['nl_fix']))
        ax_nl.set_ylabel('NL Fixed Sats')
        _auto_ylim(ax_nl, _nlc, min_range=2., bottom_zero=True)
        ax_nl.legend(fontsize=8, loc='upper right')
        ax_nl.grid(True, alpha=0.3)
        ax_nl.set_title('NL Fix Timeline (should stay ~0 in pure float)')

        # Subplot 4 — FWD vs RTS difference (GPS+Galileo)
        ax_rts = axs_a[3]
        _rts_plotted = False
        if _rr_ge2 is not None:
            _rd_sods = _rr_ge2.get('rts_diff_sods')
            _rd_mm   = _rr_ge2.get('rts_diff_mm')
            if _rd_sods is not None and len(_rd_sods) > 0 and np.any(_rd_mm > 0):
                ax_rts.plot(_rd_sods / 3600., _rd_mm, color='#9467bd', lw=0.9)
                _auto_ylim(ax_rts, [_cut(_rd_sods, _rd_mm)], bottom_zero=True)
                _rts_plotted = True
        if not _rts_plotted:
            _dummy_t = _run_results_v105.get('GE', _run_results_v105.get('G', {})).get(
                'sods', np.array([0., 86400.]))
            ax_rts.plot(_dummy_t / 3600., np.zeros_like(_dummy_t),
                        color='#9467bd', lw=0.9, ls='--', label='RTS unavailable')
        ax_rts.set_ylabel('|FWD − RTS| (mm)')
        ax_rts.grid(True, alpha=0.3)
        ax_rts.set_title('FWD vs RTS Difference (GPS+Galileo)')

        axs_a[-1].set_xlabel('Time (h)')
        axs_a[-1].set_xlim(0, 25)
        plt.tight_layout()
        _ap = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'ppp_v_final_analysis.png')
        fig_a.savefig(_ap, dpi=150, bbox_inches='tight')
        print(f"[IF_v1] Analysis plot saved: {_ap}")
        plt.close(fig_a)

    except Exception as _ve:
        print(f"[v106] Plot failed: {_ve}")

    print(f"\n[IF_v1] Wall time: {_time.time() - t0:.1f}s")
    print("="*72)



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
            INFILES,os.path.join(DATA,'ppp_results1.csv'))
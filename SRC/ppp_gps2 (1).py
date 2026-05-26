"""
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

# Pre-computed NL denominators
_DENOM_G = ALFA*LAMBDA1 - BETA*LAMBDA2         # GPS NL denom  ≈ 0.1073 m
_DENOM_E = ALFA_E*LAMBDA_E1 - BETA_E*LAMBDA_E5A  # Gal NL denom  ≈ 0.1090 m

def _ifc(a, b):   return ALFA*a - BETA*b
def _sig(el, s0): return s0 / max(math.sin(el), 0.1)

def _sig_exp(el, s0, exp=1.0):
    """Elevation-weighted sigma: s0 / sin(el)^exp.  exp=1 → current default."""
    return s0 / max(math.sin(el), 0.1) ** exp

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
RUN_EXTRA_DIAGNOSTICS: bool = False   # DISABLED: removes 11 extra PPP passes
RUN_WHITEN_TEST:       bool = False   # DISABLED: AR(1) whitening sweep (Run K)

ENABLE_12H_HARMONIC: bool = RUN_EXTRA_DIAGNOSTICS  # disabled: harmonic absorber rerun
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
ENABLE_ZWD_AUDIT:   bool = RUN_EXTRA_DIAGNOSTICS  # disabled: ZWD audit post-proc
# ZWD_Q_INFLATE_X2 replaced by ZWD_Q_SCALE (Run L ablation).
# 1.0 = baseline; 3.0 = ×3; 5.0 = ×5.  Only Q[4,4] is affected.
ZWD_Q_SCALE:        float = 1.0   # set at runtime by _run_l_zwd_ablation()

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
ENABLE_CLK_MECH_SEP: bool = False  # v94: disabled   # master switch

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
ABLATION_RUN_SUITE: bool = RUN_EXTRA_DIAGNOSTICS  # disabled: Runs A–D, F (5 extra passes)

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
ENABLE_EARLY_HUMP_TEST: bool = False  # v94: disabled   # master switch for TEST A + TEST B

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
ENABLE_CLK_Q_TEST:  bool  = False
# ==============================================================================
#  v99 Adaptive Residual Censoring Test
#  Auto-detects hump windows from baseline, reruns with per-epoch satellite
#  censoring active only inside those windows.  No other solver changes.
# ==============================================================================
ENABLE_V99_RESID_CENSOR: bool = True
_CLK_Q_SCALES: list       = [0.3, 1.0, 3.0]
_CLK_Q_HUMP1_WIN: tuple   = (2.00 * 3600., 5.40 * 3600.)

# ==============================================================================
#  OSB CSV logging
# ==============================================================================
import csv as _csv_mod

_osb_buffer: list = []

def init_osb_csv():
    with open("osb_log2.csv", "w", newline="") as f:
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
        with open("osb_log2.csv", "a", newline="") as f:
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
    if _nproc_global % 300 == 0:
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
                _fallback_used=_fallback_used)

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
    if _nproc_global % 300 == 0:
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
                ar_skip_reason=ar_skip_reason)

def _rp(m,dT,ZWD):
    return (m['rng']-m['scm']-m['dtrel']+dT
            +m['trop_zhd']+m['mw']*ZWD
            +m['shp']+m['setm']+m['pcv_sat']+m['pcv_rec'])


# ==============================================================================
#  PPP Kalman filter pass
# ==============================================================================
def _ppp_pass(epochs,sp3t,sp,sc,clkd,osb,ah,nom,iclk,izwd,lat0,doy,zhd,tref,
              satx,att,recx,elm=math.radians(10.),SC=0.25,SP=0.015,
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
              resid_censor_freq=None):
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
    global _osb_dbg_printed, _sat_signal_map, _cp_debug, _osb_once, _nproc_global, _prev_gps_ar_state
    _osb_dbg_printed = set()
    _sat_signal_map  = {}  # v70: reset signal lock at start of each pass
    _cp_debug        = {}  # v81: per-sat L1m-P1c history (OSB consistency debug)
    _osb_once        = set()  # v81: [OSB_VAL] printed-once guard
    _nproc_global    = 0
    _prev_gps_ar_state = {}  # v84 PART 1: reset AR state-change tracker per pass
    init_osb_csv()
    wl_fixed=dict(wl_init) if wl_init else {}
    _amb_init=dict(amb_init) if amb_init else {}
    _amb_seeded=set()
    nl_fixed={}
    _nl_R_eff_map = {}   # v88 PART 6: per-sat effective pseudo-obs noise R

    # ── v54 parameters ────────────────────────────────────────────────────────
    # NL/WL fixing thresholds PRESERVED but NL fixing is DISABLED in v54
    # until Phase 2 validation passes (RAW float convergence confirmed).
    NL_RATIO_THRESH   = 4.5
    NL_VAR_THRESH     = (0.1)**2     # v58: strict gate — was (10.0)² (allowed fixing with huge uncertainty)
    NL_RES_THRESH     = 0.02             # v86 PART 2: tightened from 0.03 — must be within 2% of integer
    NL_EXCL_THRESH    = 0.25
    NL_R_TIGHT        = (0.002)**2   # v90: tightened 3mm→2mm — stronger NL constraint
    NL_INNOV_GATE     = 0.200   # v89: tightened 0.500→0.200 — 0.5 was allowing wrong-integer pseudoobs
    NL_RELEASE_THRESH = 0.050   # v89: tightened 0.080→0.050 — stops integer flips (N1 cycling 162/163)
    DISABLE_NL_FIXING = True    # v_diag: disable ALL NL fixing to verify float PPP quality
    NL_PHASE_THRESH   = 0.010
    NL_MIN_SATS       = 3            # v62: lowered from 7 — apply NL once ≥3 sats fixed
    NL_MIN_OBS        = 8
    PHASE_RES_GATE    = 0.020   # v89: tightened 0.030→0.020 m — inflates Rd earlier on phase outliers
    ZWD_PRIOR         = 0.12
    ZWD_PRIOR_SIGMA   = 0.06
    ZWD_CLAMP         = 0.015
    _zwd_prev         = None
    _nl_diag_done     = False

    # ── v54 RAW state vector ──────────────────────────────────────────────────
    # Global:  [x(0), y(1), z(2), clock(3), ZWD(4)]
    # Per-sat: [I_s(5+3k), N1_s(6+3k), N2_s(7+3k)]  for satellite k
    # 3 states per satellite instead of 1 IF ambiguity
    x=np.zeros(5); x[3]=iclk; x[4]=izwd
    P=np.zeros((5,5))
    P[0,0]=P[1,1]=P[2,2]=100.**2; P[3,3]=3000.**2; P[4,4]=0.5**2

    # ── v93 Run-F: subdaily loading absorber (optional extra state) ───────────
    # Appended at index 5 BEFORE per-satellite states.  All satellite states
    # are allocated dynamically via len(x), so sidx indices shift correctly.
    # Mapping: h_vload = -(unit_vec · e_up)  applied to all 4 rows per sat.
    # Process noise: ~(10 mm)²/h  =  _SDA_Q_NOISE m²/ep (30 s epochs).
    # Ambiguity / ZWD / iono / position / clock: completely unchanged.
    _e_up_sda = np.zeros(3)   # ECEF up-unit vector (zero when absorber OFF)
    if enable_subdaily_absorber:
        _lat_sda, _lon_sda, _ = _lla(nom)
        _e_up_sda = _enu(_lat_sda, _lon_sda)[2, :]   # Up row of ENU rotation
        x = np.append(x, [0.0])       # x[5] = v_load (m), initially 0
        P_sda = np.zeros((6, 6))
        P_sda[:5, :5] = P
        P_sda[5, 5] = (0.05) ** 2    # 50 mm initial sigma — non-informative
        P = P_sda

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

    # Run-K: always compute ECEF Up-unit vector for per-sat LOS·Up projection
    _lat_k, _lon_k, _ = _lla(nom)
    _e_up_ecef = _enu(_lat_k, _lon_k)[2, :]   # Up row of ENU rotation (ECEF 3-vector)

    # Run-K obs_whitening: AR(1) pre-whitening buffers for code innovations
    # phi is supplied via the ar1_phi kwarg; default 0.95 ≈ 5-min decorrelation
    _AR1_PHI = float(ar1_phi)   # ← driven by caller (0.90 / 0.95 / 0.98 sweep)
    _ar1_buf_code = {}  # sid → previous epoch's P1 prefit innovation (m)

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
    _nl_last_N1int  = {}   # v89: rounded N1 integer per sat — reset buf when integer jumps
    _nl_jump_count  = {}   # v90: consecutive-epoch jump counter for debounce (reset only after ≥3)
    # v80 PART 4: per-satellite cooldown counter (epochs) before re-fixing is
    # allowed.  Set to 30 when a satellite is released from nl_fixed; prevents
    # rapid fix→release→refix oscillations that destabilise the filter.
    _nl_fix_cooldown = defaultdict(int)   # sid → epochs remaining before re-fix OK
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
        os.path.dirname(os.path.abspath(__file__)), "nl_bias_debug2.csv")
    _bias_csv_fh = open(_bias_csv_path, "w", newline="")
    _bias_csv_w  = _csv.writer(_bias_csv_fh)
    _bias_csv_w.writerow(["epoch", "sod", "sat",
                          "sigma_N1_cm", "raw_frac", "bias", "corr_frac",
                          "buf_n", "frac_std", "frozen"])

    # ── Structured CSV debug loggers (PART 3-4) ──────────────────────────────
    _logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(_logs_dir, exist_ok=True)

    # FILE 1 — nl_debug.csv  (logged when sigma_N1_m < 0.15)
    _nl_debug_fh = open(os.path.join(_logs_dir, "nl_debug2.csv"), "w", newline="")
    _nl_debug_w  = _csv.writer(_nl_debug_fh)
    _nl_debug_w.writerow(["SOD", "sat", "raw_frac", "bias", "corr_frac",
                          "sigma_N1_m", "buf_n", "fixed"])

    # FILE 2 — float_diag.csv  (top-6 lowest-sigma sats per epoch)
    _float_diag_fh = open(os.path.join(_logs_dir, "float_diag.csv"), "w", newline="")
    _float_diag_w  = _csv.writer(_float_diag_fh)
    _float_diag_w.writerow(["SOD", "sat", "N1_float", "frac", "sigma_N1_m"])

    # FILE 3 — nl_events.csv  (FIX / RELEASE / SKIP_SIGMA / SKIP_FRAC)
    _nl_events_fh = open(os.path.join(_logs_dir, "nl_events2.csv"), "w", newline="")
    _nl_events_w  = _csv.writer(_nl_events_fh)
    _nl_events_w.writerow(["SOD", "sat", "event_type", "corr_frac", "sigma"])

    # FILE 4 — summary.csv  (every epoch, lightweight)
    _summary_fh = open(os.path.join(_logs_dir, "summary2.csv"), "w", newline="")
    _summary_w  = _csv.writer(_summary_fh)
    _summary_w.writerow(["SOD", "n_sats", "NL_count", "WL_count",
                         "err3D", "code_rms", "phase_rms"])

    # v54: Phase 2 debug — track L1m−P1c and L2m−P2c for one reference sat
    _DBG_SAT   = None       # will be set to first GPS sat seen
    _dbg_lp1   = []         # (sod, L1m-P1c) history
    _dbg_lp2   = []         # (sod, L2m-P2c) history
    _DBG_PRINT_INTERVAL = 120   # print summary every N epochs

    b_rec_frozen={}; b_rec_n=defaultdict(int)
    eplist=epochs if direction==1 else list(reversed(epochs))

    # ── Elevation-residual leakage audit accumulators ─────────────────────────
    # For each elevation bin and time window: accumulate
    #   sum(phase_innov^2 * sin^2(el))  and observation count.
    # Time windows: HUMP = SOD 50400-79200 (h 14-22), BASE = SOD 7200-50400 (h 2-14).
    # Elevation bins: 5-15, 15-30, 30-50, 50+.
    _ELEV_BIN_EDGES = [5., 15., 30., 50., 90.]   # degrees
    _N_EL_BINS = len(_ELEV_BIN_EDGES) - 1         # 4 bins
    _HUMP_SOD_LO, _HUMP_SOD_HI = 50400., 79200.   # h 14-22
    _BASE_SOD_LO, _BASE_SOD_HI =  7200., 50400.   # h 2-14
    # [bin][0]=all  [bin][1]=hump  [bin][2]=base  stored as [sum_sq, count]
    _elev_bins = [[[0.0, 0], [0.0, 0], [0.0, 0]] for _ in range(_N_EL_BINS)]
    # ── end accumulators ──────────────────────────────────────────────────────
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
        # v94 Run-H: pos_clk_q_scale multiplies ONLY position+clock Q.
        # ZWD Q[4,4], iono Q, and ambiguity Q are completely untouched.
        Q[0,0]=Q[1,1]=Q[2,2]=1e-8*dt*pos_clk_q_scale; Q[3,3]=1e4*dt*pos_clk_q_scale*clk_q_scale
        # ZWD process noise: ~3 mm/sqrt(hr) ≈ 2.5e-9*dt
        # ZWD_Q_SCALE: Run-L scalar multiplier — only Q[4,4] is affected.
        # Ambiguity / iono / position / clock Q are completely untouched.
        Q[4,4]=2.5e-9*dt*ZWD_Q_SCALE
        # TEST_ZWD_FREEZE: hold ZWD random walk at zero after convergence.
        # Only Q[4,4] is affected; all other states are completely unchanged.
        if zwd_freeze_sod is not None and sod >= zwd_freeze_sod:
            Q[4,4] = 0.0
        # v93 Run-F: subdaily absorber random-walk process noise
        if enable_subdaily_absorber:
            Q[5, 5] = _SDA_Q_NOISE * dt   # state 5 = v_load; ZWD (4) unchanged
        # v54 RAW per-satellite state noise:
        #   I (ionosphere): small process noise to allow slow drift
        #   N1, N2 (ambiguities): zero process noise (carrier phase constants)
        # v56 FIX 1 (CRITICAL): q_iono increased 100× for equatorial station IISC.
        # IISC is inside the EIA; L1 STEC varies 20–50 m per pass.
        # With 1e-6/s the ionosphere froze after ~50 epochs (P[ki,ki]→0.02 m²);
        # position absorbed the unfrozen residual.  1e-4/s gives σ_I_ss≈0.34 m,
        # enough bandwidth to track equatorial rate ≈ 0.05–0.5 m/epoch.
        # For extreme scintillation epochs, raise to ION_PROC_NOISE=1e-3.
        ION_PROC_NOISE = 5e-5 * ion_q_scale  # m²/s — v92 EXP: 5× baseline; ion_q_scale = TEST B multiplier (1/3, 1, 3)
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

        for sid,so in sorted(sobs.items()):
            if sid[0] not in ('G','E'): continue
            if sid[0] not in constellation: continue
            if exclude_sats and sid in exclude_sats: continue

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
                        wl_fixed.pop(sid,None); mw_hist[sid].clear()
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
                x[ki]=I_init; P[ki,ki]=50.**2   # ~50m init var for ionosphere

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

            # ── P1 row: P1c = rp + I ──────────────────────────────────────
            r=base
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]; H[r,3]=1.; H[r,4]=mw
            H[r,ki]=1.                          # +I
            z[r]=m['P1c']-(rp+I_s)
            # TEST A — arc ramp-in: inflate code sigma for newly-risen arcs.
            # Linear ramp: sigma_scale = INIT + (1-INIT)*(age/N) for age < N.
            _arc_age = m.get('age', 9999)
            if arc_ramp_n > 0 and _arc_age < arc_ramp_n:
                _ramp_frac = float(_arc_age) / arc_ramp_n
                _code_scale = arc_ramp_init_scale + (1.0 - arc_ramp_init_scale) * _ramp_frac
            else:
                _code_scale = 1.0
            Rd[r]=(_sig_exp(m['el'],SC,elev_weight_exp) * _code_scale)**2

            # ── P2 row: P2c = rp + γ·I ───────────────────────────────────
            r=base+1
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]; H[r,3]=1.; H[r,4]=mw
            H[r,ki]=gam                         # +γI
            z[r]=m['P2c']-(rp+gam*I_s)
            # v56 FIX 2: P2 noise = same as P1 (both P-code; γ² scaling removed).
            # γ² was reducing P2's ionosphere SNR by γ, slowing I convergence.
            # TEST A: same ramp scale as P1 (arc age already computed above).
            Rd[r]=(_sig_exp(m['el'],SC,elev_weight_exp) * _code_scale)**2

            # ── L1 row: L1mc = rp − I + λ1·N1 ───────────────────────────
            r=base+2
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]; H[r,3]=1.; H[r,4]=mw
            H[r,ki]=-1.                         # −I
            H[r,kn1]=lam1                       # +λ1·N1 (cycle → metres)
            pred_L1=rp - I_s + lam1*N1_s
            z[r]=m['L1mc']-pred_L1
            phase_sig=_sig_exp(m['el'],SP,elev_weight_exp)*(5. if m.get('age',99)<=3 else 1.)
            Rd[r]=phase_sig**2
            if abs(z[r])>PHASE_RES_GATE: Rd[r]=max(Rd[r], 1.0**2)

            # ── L2 row: L2mc = rp − γ·I + λ2·N2 ─────────────────────────
            r=base+3
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]; H[r,3]=1.; H[r,4]=mw
            H[r,ki]=-gam                        # −γI
            H[r,kn2]=lam2                       # +λ2·N2
            pred_L2=rp - gam*I_s + lam2*N2_s
            z[r]=m['L2mc']-pred_L2
            # v56 FIX 3: L2 phase noise = same as L1 in metres (γ scaling removed).
            # L1 and L2 carrier-phase noise are similar in m; γ is a frequency
            # ratio, not a noise ratio.  Outlier floor also normalised to 1 m.
            phase_sig2=_sig_exp(m['el'],SP,elev_weight_exp)*(5. if m.get('age',99)<=3 else 1.)
            Rd[r]=phase_sig2**2
            if abs(z[r])>PHASE_RES_GATE: Rd[r]=max(Rd[r], 1.0**2)

            # v93 Run-F: subdaily vertical absorber — add v_load column (idx 5)
            # to all 4 rows for this satellite.  Same sign convention as xyz:
            # h_vload = -(unit_vec · e_up).  ZWD/iono/ambiguity rows untouched.
            if enable_subdaily_absorber:
                _h_vl = -float(np.dot(u, _e_up_sda))
                for _vl_r in range(base, base + 4):
                    H[_vl_r, 5] = _h_vl

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

            # ── Elevation-bin phase residual accumulation ─────────────────
            # Up-projected phase prefit innovation: |z_phase| * sin(el).
            # z[base+2]=L1 prefit, z[base+3]=L2 prefit (before filter update).
            _el_deg_rb  = math.degrees(m['el'])
            _sin_el_rb  = max(math.sin(m['el']), 0.1)
            _ph_innov_rb = math.sqrt((z[base+2]**2 + z[base+3]**2) / 2.0)
            _up_contrib  = (_ph_innov_rb * _sin_el_rb) ** 2  # m² (Up-projected)
            _bin_idx = min(int((_el_deg_rb - _ELEV_BIN_EDGES[0]) /
                               (_ELEV_BIN_EDGES[-1] - _ELEV_BIN_EDGES[0]) *
                               _N_EL_BINS), _N_EL_BINS - 1)
            for _bi, (lo_e, hi_e) in enumerate(zip(_ELEV_BIN_EDGES[:-1], _ELEV_BIN_EDGES[1:])):
                if lo_e <= _el_deg_rb < hi_e:
                    _bin_idx = _bi; break
            else:
                _bin_idx = _N_EL_BINS - 1
            _elev_bins[_bin_idx][0][0] += _up_contrib   # all-time
            _elev_bins[_bin_idx][0][1] += 1
            if _HUMP_SOD_LO <= sod <= _HUMP_SOD_HI:
                _elev_bins[_bin_idx][1][0] += _up_contrib
                _elev_bins[_bin_idx][1][1] += 1
            elif _BASE_SOD_LO <= sod <= _BASE_SOD_HI:
                _elev_bins[_bin_idx][2][0] += _up_contrib
                _elev_bins[_bin_idx][2][1] += 1
            # ── end elevation-bin accumulation ────────────────────────────

        # v94 Run-J: satellite clock perturbation discriminator.
        # Adds clk_perturb_m to ALL observation innovations (code + phase).
        # This is equivalent to shifting all satellite clocks uniformly.
        # The receiver clock state (x[3]) absorbs the common-mode in the
        # next update; any residual Up-hump shift reveals clock-position coupling.
        # ZWD prior is NOT perturbed (added after this block).
        if clk_perturb_m != 0.0:
            z[:n_obs] -= clk_perturb_m   # shift all obs innovations uniformly

        # Run-K: AR(1) pre-whitening of code innovations (obs_whitening flag).
        # Applies z_white = z_t − phi*z_{t-1} to P1/P2 rows only, removing
        # temporally correlated orbit/clock residual noise.  Phase rows are NOT
        # whitened (phase noise is dominated by carrier tracking noise, not orbit).
        # Rd is scaled by (1−phi²) to match the whitened noise variance.
        if obs_whitening:
            _phi_sq_comp = 1.0 - _AR1_PHI ** 2   # = 0.0975 for phi=0.95
            for _ri_w, _m_w in enumerate(geom):
                _base_w  = 4 * _ri_w
                _prev_z  = _ar1_buf_code.get(_m_w['sid'], 0.0)
                # whiten P1 and P2 innovations
                z[_base_w]     -= _AR1_PHI * _prev_z
                z[_base_w + 1] -= _AR1_PHI * _prev_z
                # store current (pre-whitening) P1 prefit for next epoch
                _ar1_buf_code[_m_w['sid']] = float(z[_base_w] + _AR1_PHI * _prev_z)
                # adjust code Rd to reflect reduced variance of whitened process
                Rd[_base_w]     *= _phi_sq_comp
                Rd[_base_w + 1] *= _phi_sq_comp

        # v94 Run-I: prefit phase innovation RMS for spectral audit.
        # Computed here, before filter_standard, from the L1/L2 phase rows
        # (rows base+2 and base+3 for each satellite block of 4).
        _phase_z_prefit = []
        for _pf_base in range(0, n_obs, 4):
            _phase_z_prefit.append(float(z[_pf_base + 2]))
            if _pf_base + 3 < n_obs:
                _phase_z_prefit.append(float(z[_pf_base + 3]))
        _prefit_phase_rms_ep = (
            math.sqrt(float(np.mean(np.array(_phase_z_prefit) ** 2)))
            if _phase_z_prefit else 0.0)

        # Early/late hump audit: prefit code RMS and per-sat prefit phase
        # Prefit code innovations: z[base+0] (P1) and z[base+1] (P2)
        _code_z_prefit = []
        for _pf_base in range(0, n_obs, 4):
            _code_z_prefit.append(float(z[_pf_base]))
            _code_z_prefit.append(float(z[_pf_base + 1]))
        _prefit_code_rms_ep = (
            math.sqrt(float(np.mean(np.array(_code_z_prefit) ** 2)))
            if _code_z_prefit else 0.0)
        # Per-sat prefit phase innovations {sid: (L1_innov_m, L2_innov_m, el_deg, az_deg)}
        _phase_prefit_per_sat = {}
        for _ri_pf, _m_pf in enumerate(geom):
            _pb = 4 * _ri_pf
            _phase_prefit_per_sat[_m_pf['sid']] = (
                float(z[_pb + 2]),
                float(z[_pb + 3]),
                float(math.degrees(_m_pf['el'])),
                float(math.degrees(_m_pf.get('az', 0.))),
            )

        # v99 RESID_CENSOR: rank satellites by prefit phase norm each epoch.
        # Censor (inflate Rd) or downweight worst satellite(s) before KF update.
        # Only active within resid_censor_win (sod_lo, sod_hi); None = always.
        # Geometry guard: skip if remaining sats < min threshold.
        if resid_censor and len(geom) > 0:
            _in_win = (resid_censor_win is None or
                       resid_censor_win[0] <= sod <= resid_censor_win[1])
            if _in_win:
                _min_s = (6 if constellation == 'G' else
                          5 if constellation == 'E' else 8)
                _norms = []
                for _ri_c, _m_c in enumerate(geom):
                    _b_c = 4 * _ri_c
                    _n_c = math.sqrt((z[_b_c+2]**2 + z[_b_c+3]**2) / 2.)
                    _norms.append((_n_c, _ri_c, _m_c['sid']))
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
                    _b_c = 4 * _ri_c
                    if resid_censor in ('A', 'B'):
                        Rd[_b_c+2] = 1e4; Rd[_b_c+3] = 1e4
                    else:  # 'C'
                        Rd[_b_c+2] *= 4.0; Rd[_b_c+3] *= 4.0
                    if resid_censor_freq is not None:
                        resid_censor_freq[_sid_c] = resid_censor_freq.get(_sid_c, 0) + 1

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
        _x_pre_pos = x[:3].copy()   # snapshot for Up-correction diagnostic
        if filter_standard(x,P,H_p.T,z_p,R_main)!=0: continue
        # Up correction this epoch: KF position update projected onto local Up
        _dx_up_mm = float(np.dot(x[:3] - _x_pre_pos, _e_up_ecef)) * 1e3

        # ── FLOAT_DIAG → CSV (top-6 lowest sigma per epoch) ─────────────────
        if sidx:
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
        IONO_VAR_CAP  = 50.0   # m² — v92 EXP: relaxed 25→50 (σ_I ≤ ~7 m) to allow float iono to track faster
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
            nl_fixed.clear()
            print(f"[NaN GUARD] SOD={sod:.0f} — non-finite state detected; "
                  f"released all NL fixes, inflated covariance")

        # Compute phase residuals immediately after float update so NL gate
        # can use current-epoch phase_rms_now (not stale from previous epoch).
        phase_res_now=[]
        for m in geom:
            ki=m['ki']; rp=_rp(m,x[3],x[4])
            res_L1=m['L1mc']-(rp - x[ki] + m['lam1']*x[ki+1])
            phase_res_now.append(res_L1)
        phase_rms_now=math.sqrt(np.mean(np.array(phase_res_now)**2)) if phase_res_now else 999.

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
            # log RELEASE event before removing
            _lam1_rel = _sat_lam1.get(sid_nl, 0.1903)
            _sig_rel  = math.sqrt(max(0.0, P[sidx[sid_nl]+1, sidx[sid_nl]+1])) * _lam1_rel if sid_nl in sidx else 0.0
            _cf_rel   = x[sidx[sid_nl]+1] - nl_fixed[sid_nl][0] if sid_nl in sidx else 0.0
            _nl_events_w.writerow([f"{sod:.1f}", sid_nl, "RELEASE",
                                   f"{_cf_rel:+.4f}", f"{_sig_rel:.4f}"])
            nl_fixed.pop(sid_nl,None)
            _nl_fix_cooldown[sid_nl] = 30   # v80 PART 4: 30-epoch re-fix cooldown

        # ── 3b. ZWD watchdog ─────────────────────────────────────────────────
        if not hasattr(_ppp_pass,'_zwd_buf'): pass  # per-call buffer via list
        # (reuse existing _zwd_prev / ZWD_CLAMP logic above; watchdog below)
        _zwd_buf=getattr(_ppp_pass,'_zwd_buf_'+label,[])
        _zwd_buf.append(x[4])
        if len(_zwd_buf)>5: _zwd_buf.pop(0)
        setattr(_ppp_pass,'_zwd_buf_'+label,_zwd_buf)
        ZWD_RATE_LIMIT=0.015   # v89: tightened 0.025→0.015 m/5ep — triggers faster on ZWD spikes
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
            if _rps > 12.0: continue
            _ki_ps = sidx[_sps]
            _sig_ps = math.sqrt(max(0.0, P[_ki_ps+1, _ki_ps+1])) * _mps['lam1']
            _nl_preselect.append((_sig_ps, _sps))
        _nl_preselect.sort(key=lambda t: t[0])   # ascending sigma_N1_m
        # v88 PART 3 — always allow up to 4 best candidates; do NOT subtract
        # already-fixed count (that was making selected=0 when nl_fixed>=4).
        _nl_eligible_sids = {sid for _, sid in _nl_preselect}   # use all available candidates
        # skip NL this epoch if geometry is insufficient
        if len(_nl_eligible_sids) < 2 and not nl_fixed:
            pass  # allow through if already have fixes; only gate fresh-start

        _nl_epoch_candidates = []   # (sort_key_tuple, sid_m, N1_int, N2_int, frac_for_fix, sigma_N1)
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
            if buf_n < 20:
                continue   # not enough bias samples yet (v89: raised 15→20)

            # v89: TIERED frac_std gate.
            # Frozen sats already have a validated bias — allow up to 0.05 cyc
            # since the bias corrects the residual and corr_frac will still be tight.
            # Non-frozen sats need strict 0.02 to avoid committing to a bad bias.
            _frac_std_limit = 0.05 if sid_m in _nl_bias_frozen else 0.02
            if frac_std >= _frac_std_limit:
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

            # Fix 5: hard corr_frac gate — v86 PART 2: tightened 0.05 → 0.02.
            # Require BOTH abs(corr_frac) < 0.02 AND frac_std < 0.02 (above).
            if abs(corr_frac) > 0.02:
                _nl_events_w.writerow([f"{sod:.1f}", sid_m, "SKIP_FRAC",
                                       f"{corr_frac:+.4f}", f"{sigma_N1_m:.4f}"])
                continue   # wrong branch risk — corr_frac too large

            # v79: flag_low_quality / range soft gate REMOVED — hard 6.0 m skip
            # above (PART 4) supersedes the v76/v78 partial gates entirely.

            # Acceptance gate: corr_frac must be within NL_RES_THRESH (0.03 cyc).
            if abs(frac_for_fix) > NL_RES_THRESH: continue     # not near integer

            # Integer is taken from the bias-corrected value.  Filter state is NOT modified.
            N1_int = int(round(N1_corr))
            N2_int=N1_int-NWL_m

            # v66 FIX 3+4: collect candidate for post-loop sort-and-limit.
            # Sort key: (0=Galileo / 1=GPS, |corr_frac| + sigma_N1_m * weight_factor)
            # Galileo prioritised (cleaner signals → anchor solution first).
            # v87 PART 5: _weight_factor penalises high-sigma sats in sort order
            # without hard-rejecting them (they can still fix if slots remain).
            _is_gal_cand = sid_m.startswith('E')
            _sort_key = (0 if _is_gal_cand else 1,
                         abs(frac_for_fix) + sigma_N1_m * _weight_factor)
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
        MAX_NL_TOTAL = 5   # v84 PART 3: cap total fixed sats — >5 causes over-constraint
        for _cand in _nl_epoch_candidates[:3]:
            _, sid_m, N1_int, N2_int, frac_for_fix, sigma_N1, sigma_N1_m, _cand_R_eff = _cand
            # v84 PART 3: stop adding new fixes once total reaches MAX_NL_TOTAL
            if len(nl_fixed) >= MAX_NL_TOTAL:
                break
            # PART 3: secondary commit sigma gate REMOVED — after_sigma = selected
            nl_fixed[sid_m]=(N1_int,N2_int)
            _nl_R_eff_map[sid_m] = _cand_R_eff   # v88 PART 6: store per-sat R_eff
            _ep_nl_count += 1
            # v64 FIX 3 — REMOVE IMMEDIATE P COLLAPSE:
            if sid_m[0] == 'G':
                _gps_nl_fixed_ever = True
            # PART 2 — console NL FIX log
            print(f"[NL FIX] SOD={sod:.0f}  sat={sid_m}  corr_frac={frac_for_fix:.4f}  sigma={sigma_N1_m:.3f}")
            # PART 5 — nl_events.csv
            _nl_events_w.writerow([f"{sod:.1f}", sid_m, "FIX",
                                   f"{frac_for_fix:+.4f}", f"{sigma_N1_m:.4f}"])

        # v89: REMOVE WEAK FIXES: lower threshold 0.30→0.20 m — evicts bad fixes faster
        for _sid_wk in list(nl_fixed.keys()):
            if _sid_wk not in sidx:
                continue
            _ki_wk   = sidx[_sid_wk]
            _lam1_wk = _sat_lam1.get(_sid_wk, 0.1903)
            _sig_wk  = math.sqrt(max(0.0, P[_ki_wk+1, _ki_wk+1])) * _lam1_wk
            if _sig_wk > 0.20:
                nl_fixed.pop(_sid_wk, None)
                _nl_R_eff_map.pop(_sid_wk, None)

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
            # Build (H, target_integer, R) list — z recomputed each iteration
            nl_pairs=[]   # (h_vector, N_target_float, R)
            for sid_nl,(N1_i,N2_i) in list(nl_fixed.items()):
                if sid_nl not in sidx: continue
                ki_nl=sidx[sid_nl]
                # v80 PART 5 — RELAX INNOVATION GATE (0.25 → 0.35):
                # Wider gate allows constraint injection for satellites whose
                # float ambiguity has drifted slightly but is still recoverable.
                # The release check (3a) handles persistent large drift separately.
                _innov_nl = x[ki_nl+1]-N1_i
                if abs(_innov_nl) > NL_INNOV_GATE:   # v89: use NL_INNOV_GATE (was hardcoded 0.35)
                    reject_due_to_innov += 1
                    continue
                # v85 PART 1 — adaptive NL_R_eff based on current sigma_N1_m
                _lam1_nl  = _sat_lam1.get(sid_nl, 0.1903)
                _sigma_nl = math.sqrt(max(0.0, P[ki_nl+1, ki_nl+1])) * _lam1_nl
                if _sigma_nl < 0.10:
                    NL_R_eff = (0.003)**2   # strong constraint (3 mm)
                elif _sigma_nl < 0.20:
                    NL_R_eff = (0.01)**2    # medium constraint (1 cm)
                else:
                    NL_R_eff = (0.03)**2    # weak constraint (3 cm)
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
                        nl_fixed.clear()
                        print(f"[NaN GUARD NL] SOD={sod:.0f} iter={_nl_iter} — "
                              f"NaN after NL injection; released all fixes")
                        break

        n_nl=len(nl_fixed)

        nproc+=1
        _nproc_global = nproc
        if nproc % 100 == 0:
            _bias_csv_fh.flush()
            _nl_debug_fh.flush(); _float_diag_fh.flush()
            _nl_events_fh.flush(); _summary_fh.flush()
        pos=nom+x[:3]; dx=pos-REF; d3=np.linalg.norm(dx)*1e3
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

        # ── Constellation-split code residuals for ISB audit ─────────────────
        _gps_cres = [r for m, r in zip(geom, code_res) if m['sid'][0]=='G']
        _gal_cres = [r for m, r in zip(geom, code_res) if m['sid'][0]=='E']
        _code_mean_gps = float(np.mean(_gps_cres))*1e3 if _gps_cres else float('nan')
        _code_mean_gal = float(np.mean(_gal_cres))*1e3 if _gal_cres else float('nan')
        _code_rms_gps  = float(np.sqrt(np.mean(np.array(_gps_cres)**2)))*1e3 if _gps_cres else float('nan')
        _code_rms_gal  = float(np.sqrt(np.mean(np.array(_gal_cres)**2)))*1e3 if _gal_cres else float('nan')
        # ISB proxy: systematic mean offset between GPS and Galileo code residuals.
        # In a single-clock solution a non-zero mean difference ≠ 0 indicates
        # residual inter-system bias leaking into the common clock state.
        _isb_proxy = (_code_mean_gps - _code_mean_gal
                      if (_gps_cres and _gal_cres) else float('nan'))
        # ── end constellation-split ───────────────────────────────────────────

        # Run-K: per-satellite postfit code residual + geometric projections
        # sid -> (res_mm, los_dot_up, el_deg, az_deg)
        _code_per_sat = {}
        for _ri_k, _m_k in enumerate(geom):
            _res_k   = float(code_res[_ri_k]) * 1e3
            _ldu_k   = float(np.dot(_m_k['unit'], _e_up_ecef))
            _el_k    = float(math.degrees(_m_k['el']))
            _az_k    = float(math.degrees(_m_k.get('az', 0.)))
            _code_per_sat[_m_k['sid']] = (_res_k, _ldu_k, _el_k, _az_k)

        results[sod]={'xyz':pos.copy(),'dx':dx.copy(),'p_trace':P[0,0]+P[1,1]+P[2,2],
                      'n':len(geom),'ztd':TROPO,'wl_fixed':len(wl_fixed),
                      'nl_fixed':n_nl,
                      'code_rms':code_rms,'phase_rms':phase_rms,
                      'zhd':ZHD,'zwd':ZWD,
                      # mean wet mapping function this epoch — diagnostic read-only
                      'mw_mean':float(np.mean([m['mw'] for m in geom])) if geom else 0.,
                      'sats_used':sorted([m['sid'] for m in geom]),
                      'sats_wl':sorted([s for s in wl_fixed if any(m['sid']==s for m in geom)]),
                      'sats_nl':sorted([s for s in nl_fixed if any(m['sid']==s for m in geom)]),
                      # v93: clock state (m) and subdaily absorber state for G/F audits
                      'clk':float(x[3]),
                      'v_load':float(x[5]) if enable_subdaily_absorber else 0.0,
                      # v94 Run-I: prefit phase innovation RMS for spectral audit
                      'prefit_phase_rms': _prefit_phase_rms_ep,
                      # Run-K: per-sat postfit code residuals + LOS geometry
                      'code_per_sat': _code_per_sat,
                      # ISB / constellation-split audit fields
                      'code_mean_gps': _code_mean_gps,
                      'code_mean_gal': _code_mean_gal,
                      'code_rms_gps':  _code_rms_gps,
                      'code_rms_gal':  _code_rms_gal,
                      'isb_proxy_mm':  _isb_proxy,
                      'n_gps': len(_gps_cres),
                      'n_gal': len(_gal_cres),
                      # Early/late hump audit fields
                      'p_clk_var':        float(P[3,3]),
                      'p_zwd_var':        float(P[4,4]),
                      'prefit_code_rms':  _prefit_code_rms_ep,
                      'dx_up_mm':         _dx_up_mm,
                      'phase_prefit_per_sat': _phase_prefit_per_sat}

        # PART 4 — summary.csv every epoch
        _summary_w.writerow([f"{sod:.1f}", len(geom), n_nl, len(wl_fixed),
                             f"{d3:.3f}", f"{code_rms:.2f}", f"{phase_rms:.3f}"])

        # console progress: first 3 epochs only (start confirmation)
        if nproc <= 3:
            n_gps=sum(1 for m in geom if m['sid'][0]=='G')
            n_gal=sum(1 for m in geom if m['sid'][0]=='E')
            print(f"  [{label}] SOD={sod:6.0f}  N={len(geom):2d}(G{n_gps}+E{n_gal})"
                  f"  3D={d3:8.1f}mm  WL={len(wl_fixed)}  NL={n_nl}")

    print(f"[WL_DICT] {len(wl_fixed)} sats fixed")

    # ── Elevation-bin leakage report ──────────────────────────────────────────
    _BIN_NAMES = ['5-15°','15-30°','30-50°','50°+ ']
    _WIN_NAMES = ['ALL   ','HUMP(h14-22)','BASE(h2-14)']
    print(f"\n[ELEV_BINS] label={label}  elm={math.degrees(elm):.0f}°  elev_weight_exp={elev_weight_exp:.1f}")
    print(f"  {'Bin':<10}  {'n_all':>7}  {'Up-RMS_all':>11}  "
          f"{'n_hump':>7}  {'Up-RMS_hump':>12}  "
          f"{'n_base':>7}  {'Up-RMS_base':>12}")
    for _bi, _bn in enumerate(_BIN_NAMES):
        def _rms_str(entry):
            s, n = entry
            return f"{math.sqrt(s/n)*1e3:8.2f}mm" if n > 0 else "      N/A"
        print(f"  {_bn:<10}  {_elev_bins[_bi][0][1]:>7d}  {_rms_str(_elev_bins[_bi][0]):>11}  "
              f"  {_elev_bins[_bi][1][1]:>6d}  {_rms_str(_elev_bins[_bi][1]):>11}  "
              f"  {_elev_bins[_bi][2][1]:>6d}  {_rms_str(_elev_bins[_bi][2]):>11}")
    # hump vs base ratio per bin
    print(f"  Hump/Base Up-RMS ratio per bin:")
    for _bi, _bn in enumerate(_BIN_NAMES):
        _sh, _nh = _elev_bins[_bi][1]; _sb, _nb = _elev_bins[_bi][2]
        _rh = math.sqrt(_sh/_nh)*1e3 if _nh > 0 else float('nan')
        _rb = math.sqrt(_sb/_nb)*1e3 if _nb > 0 else float('nan')
        _ratio = f"{_rh/_rb:.3f}" if (_rb > 0 and _rh == _rh) else "N/A"
        print(f"    {_bn}: hump={_rh:.2f}mm  base={_rb:.2f}mm  ratio={_ratio}")
    print()
    # ── end elevation-bin report ──────────────────────────────────────────────
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
    # Close structured CSV loggers
    for _fh in (_nl_debug_fh, _float_diag_fh, _nl_events_fh, _summary_fh):
        _fh.flush(); _fh.close()
    print(f"[CSV]  logs/ → nl_debug2.csv  float_diag2.csv  nl_events2.csv  summary2.csv")
    return results,nom+x[:3],x[3],x[4],wl_fixed,fwd_amb_out,_amb_snapshots


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
    print("GPS+Galileo PPP v99 — Adaptive Residual Censoring Test")
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

    # ======================================================================
    #  v92 EARLY HUMP MECHANISM TEST  (TEST A + TEST B)
    #  Hypothesis: early hump driven by new-rise arc geometry + iono residuals.
    #  H2 (clock) already rejected.  ZWD/ISB/ambiguity/NL unchanged throughout.
    # ======================================================================
    if ENABLE_EARLY_HUMP_TEST:
        print(f"\n{'='*72}")
        print("[EARLY_HUMP_TEST] Running TEST A (arc ramp-in) + TEST B (iono Q) ...")
        print(f"{'='*72}")

        REF_eh = REF.copy()
        lr_eh, lo_eh, _ = _lla(REF_eh)
        Re_eh = _enu(lr_eh, lo_eh)

        def _hump_metrics(fwd_d, win_sod_lo, win_sod_hi):
            """Return (hump_amp_mm, up_rms_mm) over the specified SOD window."""
            items = sorted(fwd_d.items())
            if not items:
                return float('nan'), float('nan')
            sods = np.array([s for s, _ in items])
            dx   = np.array([r['dx'] for _, r in items])
            u_mm = (Re_eh @ dx.T).T[:, 2] * 1e3
            mask = (sods >= win_sod_lo) & (sods <= win_sod_hi)
            if mask.sum() < 5:
                return float('nan'), float('nan')
            u_w = u_mm[mask]
            amp  = float(np.max(np.abs(u_w)))
            rms  = float(np.sqrt(np.mean(u_w**2)))
            return amp, rms

        def _full_up(fwd_d):
            items = sorted(fwd_d.items())
            sods = np.array([s for s, _ in items])
            dx   = np.array([r['dx'] for _, r in items])
            u_mm = (Re_eh @ dx.T).T[:, 2] * 1e3
            return sods, u_mm

        # ── Baseline metrics for each constellation ──────────────────────
        _base_results = {}   # const_label → (hump_amp, up_rms, sods, u_mm)
        for _label, _win in _EARLY_HUMP_WIN.items():
            _fwd_b = all_fwd.get(_label, {})
            if not _fwd_b:
                continue
            _amp_b, _rms_b = _hump_metrics(_fwd_b, _win[0], _win[1])
            _s_b, _u_b = _full_up(_fwd_b)
            _base_results[_label] = {'amp': _amp_b, 'rms': _rms_b,
                                     'sods': _s_b, 'u_mm': _u_b,
                                     'win': _win}
            print(f"  [BASELINE] {_label:15s}  "
                  f"hump_amp={_amp_b:.1f} mm  up_rms={_rms_b:.1f} mm")

        # ── TEST A: arc ramp-in weighting (GPS+Galileo FWD only) ─────────
        _tA_results = []  # list of (N, fwd_dict, amp, rms, sods, u_mm)
        _base_gg = _base_results.get('GPS+Galileo', {})
        for _N in _RAMP_N_EPOCHS:
            _rts_store._data = []
            _fwd_A, *_ = _ppp_pass(
                epochs, nom=APX.copy(), iclk=0., izwd=0.20,
                direction=1, label=f"FWD_A_N{_N}", constellation='GE',
                arc_ramp_n=_N, arc_ramp_init_scale=_RAMP_INIT_CODE_SCALE,
                **_common)
            _win_A = _EARLY_HUMP_WIN['GPS+Galileo']
            _amp_A, _rms_A = _hump_metrics(_fwd_A, _win_A[0], _win_A[1])
            _s_A, _u_A = _full_up(_fwd_A)
            _tA_results.append({'N': _N, 'fwd': _fwd_A,
                                 'amp': _amp_A, 'rms': _rms_A,
                                 'sods': _s_A, 'u_mm': _u_A})
            _b_amp = _base_gg.get('amp', float('nan'))
            _damp_pct = ((_b_amp - _amp_A) / _b_amp * 100.) if _b_amp > 0 else float('nan')
            print(f"  [TEST A] N={_N:2d} epochs  "
                  f"hump_amp={_amp_A:.1f} mm ({_damp_pct:+.1f}%)  "
                  f"up_rms={_rms_A:.1f} mm")

        # ── TEST B: iono Q scale (GPS+Galileo FWD only) ──────────────────
        _tB_results = []
        for _qs in _ION_Q_SCALES:
            _rts_store._data = []
            _fwd_B, *_ = _ppp_pass(
                epochs, nom=APX.copy(), iclk=0., izwd=0.20,
                direction=1, label=f"FWD_B_ionQ{_qs:.2f}", constellation='GE',
                ion_q_scale=_qs,
                **_common)
            _win_B = _EARLY_HUMP_WIN['GPS+Galileo']
            _amp_B, _rms_B = _hump_metrics(_fwd_B, _win_B[0], _win_B[1])
            _s_B, _u_B = _full_up(_fwd_B)
            _tB_results.append({'qs': _qs, 'fwd': _fwd_B,
                                 'amp': _amp_B, 'rms': _rms_B,
                                 'sods': _s_B, 'u_mm': _u_B})
            _b_amp = _base_gg.get('amp', float('nan'))
            _damp_pct = ((_b_amp - _amp_B) / _b_amp * 100.) if _b_amp > 0 else float('nan')
            _tag = "baseline" if abs(_qs - 1.0) < 0.01 else f"×{_qs:.2g}"
            print(f"  [TEST B] ion_q={_tag:8s}  "
                  f"hump_amp={_amp_B:.1f} mm ({_damp_pct:+.1f}%)  "
                  f"up_rms={_rms_B:.1f} mm")

        # ── Decision summary ─────────────────────────────────────────────
        print("\n  [DECISION]")
        _best_A_damp = max(
            [(_b_amp - r['amp']) / _b_amp * 100.
             for r in _tA_results if not math.isnan(r['amp'])],
            default=float('nan'))
        _B_lo  = _tB_results[0]['amp'] if _tB_results else float('nan')
        _B_hi  = _tB_results[-1]['amp'] if len(_tB_results) > 1 else float('nan')
        _B_base_amp = next((r['amp'] for r in _tB_results if abs(r['qs']-1.0)<0.01),
                           float('nan'))
        _iono_sensitive = (not math.isnan(_B_lo) and not math.isnan(_B_hi) and
                           abs(_B_lo - _B_hi) > 0.10 * _B_base_amp)
        _ramp_helps = (not math.isnan(_best_A_damp) and _best_A_damp > 10.)
        if _ramp_helps and _iono_sensitive:
            print("    → MIXED: arc-rise geometry + ionospheric residual bandwidth both contribute.")
        elif _ramp_helps and not _iono_sensitive:
            print("    → ARC-RISE GEOMETRY PRIMARY: ramp-in weighting helps; iono Q insensitive.")
        elif not _ramp_helps and _iono_sensitive:
            print("    → IONO RESIDUAL BANDWIDTH PRIMARY: iono Q matters; ramp-in has little effect.")
        else:
            print("    → INCONCLUSIVE: neither ramp-in nor iono Q produces >10% hump reduction.")

        # ── Compact comparison plot: early hump window only ──────────────
        try:
            import matplotlib; matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            _WIN_LO = _EARLY_HUMP_WIN['GPS+Galileo'][0] / 3600.
            _WIN_HI = _EARLY_HUMP_WIN['GPS+Galileo'][1] / 3600.
            _PLOT_PAD = 0.5  # hours padding around window

            fig_eh, axes_eh = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
            fig_eh.suptitle(
                'PPP-AR v92 — Early Hump Mechanism Test (GPS+Galileo FWD)\n'
                'TEST A: new-rise arc ramp-in   |   TEST B: iono process noise sensitivity\n'
                f'Early hump window: {_WIN_LO:.2f}–{_WIN_HI:.2f} h  '
                f'(GPS-only hump1 / Galileo-only hump1 overlap)',
                fontsize=10, fontweight='bold')

            _x_lo = max(0., _WIN_LO - _PLOT_PAD)
            _x_hi = min(25., _WIN_HI + _PLOT_PAD)

            # ── Panel (a): TEST A ──────────────────────────────────────
            ax_a = axes_eh[0]
            _sb, _ub = _base_gg.get('sods', np.array([])), _base_gg.get('u_mm', np.array([]))
            if len(_sb):
                ax_a.plot(_sb/3600., _ub, color='#1f77b4', lw=1.2,
                          label=f'Baseline (amp={_base_gg["amp"]:.0f} mm, '
                                f'rms={_base_gg["rms"]:.0f} mm)', zorder=5)
            _colors_A = ['#d62728', '#2ca02c', '#9467bd']
            for _i, _r in enumerate(_tA_results):
                _b_amp = _base_gg.get('amp', float('nan'))
                _dp = ((_b_amp - _r['amp'])/_b_amp*100.) if _b_amp > 0 else float('nan')
                ax_a.plot(_r['sods']/3600., _r['u_mm'],
                          color=_colors_A[_i % len(_colors_A)], lw=0.9,
                          label=f"A N={_r['N']}  (amp={_r['amp']:.0f} mm, {_dp:+.1f}%)")
            ax_a.axvspan(_WIN_LO, _WIN_HI, alpha=0.08, color='orange', label='Hump window')
            ax_a.axhline(0, color='k', lw=0.5)
            ax_a.set_xlim(_x_lo, _x_hi)
            ax_a.set_ylim(-350, 350)
            ax_a.set_ylabel('Up Error (mm)')
            ax_a.set_title('(a) TEST A — new-rise arc code ramp-in '
                           f'(scale {_RAMP_INIT_CODE_SCALE:.0f}× → 1× over N epochs)')
            ax_a.legend(fontsize=8, loc='upper right')
            ax_a.grid(True, alpha=0.3)

            # ── Panel (b): TEST B ──────────────────────────────────────
            ax_b = axes_eh[1]
            if len(_sb):
                ax_b.plot(_sb/3600., _ub, color='#1f77b4', lw=1.2,
                          label=f'Baseline ion_q=×1  (amp={_base_gg["amp"]:.0f} mm)',
                          zorder=5)
            _colors_B = ['#ff7f0e', '#2ca02c', '#d62728']
            for _i, _r in enumerate(_tB_results):
                if abs(_r['qs'] - 1.0) < 0.01:
                    continue   # skip baseline duplicate
                _b_amp = _base_gg.get('amp', float('nan'))
                _dp = ((_b_amp - _r['amp'])/_b_amp*100.) if _b_amp > 0 else float('nan')
                _tag = f"×{_r['qs']:.2g}"
                ax_b.plot(_r['sods']/3600., _r['u_mm'],
                          color=_colors_B[_i % len(_colors_B)], lw=0.9,
                          label=f"B ion_q={_tag}  (amp={_r['amp']:.0f} mm, {_dp:+.1f}%)")
            ax_b.axvspan(_WIN_LO, _WIN_HI, alpha=0.08, color='orange', label='Hump window')
            ax_b.axhline(0, color='k', lw=0.5)
            ax_b.set_xlim(_x_lo, _x_hi)
            ax_b.set_ylim(-350, 350)
            ax_b.set_ylabel('Up Error (mm)')
            ax_b.set_xlabel('Time (h)')
            ax_b.set_title('(b) TEST B — ionosphere process noise sensitivity  '
                           '(ion_q ∈ {×1/3, ×1, ×3})')
            ax_b.legend(fontsize=8, loc='upper right')
            ax_b.grid(True, alpha=0.3)

            plt.tight_layout()
            _eh_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    'ppp_early_hump_test.png')
            fig_eh.savefig(_eh_path, dpi=150, bbox_inches='tight')
            print(f"[EARLY_HUMP_TEST] Plot saved: {_eh_path}")
            plt.close(fig_eh)
        except Exception as _e_plot:
            print(f"[EARLY_HUMP_TEST] Plot failed: {_e_plot}")

    # ======================================================================
    #  TEST_ZWD_FREEZE — ZWD↔vertical coupling experiment
    #  One extra GPS+Galileo FWD pass; no other state or logic touched.
    # ======================================================================
    if TEST_ZWD_FREEZE:
        _FREEZE_SOD = 7200.0   # freeze ZWD evolution at t = 2 h
        print(f"\n{'='*72}")
        print("[ZWD_FREEZE] TEST_ZWD_FREEZE — running ZWD-freeze pass "
              f"(ZWD pinned for t≥{_FREEZE_SOD/3600.:.0f}h)")
        print(f"{'='*72}")
        _rts_store._data = []
        _fwd_frz, _ex_frz, _ec_frz, _ez_frz, _wl_frz, _, _ = _ppp_pass(
            epochs, nom=APX.copy(), iclk=0., izwd=0.20,
            direction=1, label="FWD_ZWD_FREEZE", constellation='GE',
            zwd_freeze_sod=_FREEZE_SOD,
            **_common)
        print(f"  {len(_fwd_frz)} epochs  "
              f"end_3D={np.linalg.norm(_ex_frz - REF)*1e3:.1f}mm  "
              f"ZWD={_ez_frz:.3f}m")

        _m_base = _compute_metrics(primary_fwd, REF)
        _m_frz  = _compute_metrics(_fwd_frz,    REF)

        if _m_base is not None and _m_frz is not None:
            _sods_b = _m_base['sods']
            _u_b    = _m_base['u_mm']
            _sods_f = _m_frz['sods']
            _u_f    = _m_frz['u_mm']

            # Auto-detect hump windows from baseline Up residual
            _hump_list = _detect_hump_windows(_sods_b, _u_b, conv_sod=_FREEZE_SOD)
            _humps = [h for h in _hump_list if isinstance(h, dict)]

            # ZWD arrays for correlation (baseline only — freeze has const ZWD)
            def _zwd_arr(fwd_d, sods_arr):
                return np.array([fwd_d.get(float(s), {}).get('zwd', float('nan'))
                                 for s in sods_arr])
            _zwd_b = _zwd_arr(primary_fwd, _sods_b)

            # Post-convergence Up RMS (t >= 2 h)
            _post_b = _sods_b >= _FREEZE_SOD
            _post_f = _sods_f >= _FREEZE_SOD
            _rms_b = (float(np.sqrt(np.mean(_u_b[_post_b]**2)))
                      if _post_b.sum() > 0 else float('nan'))
            _rms_f = (float(np.sqrt(np.mean(_u_f[_post_f]**2)))
                      if _post_f.sum() > 0 else float('nan'))
            print(f"[ZWD_FREEZE] Up RMS (t≥2h):  baseline={_rms_b:.1f} mm  "
                  f"frozen={_rms_f:.1f} mm  ΔUp_RMS={_rms_f - _rms_b:+.1f} mm")

            if not _humps:
                print("[ZWD_FREEZE]   (no auto-detected humps — full post-conv window used)")

            _major_amp_b = None
            _major_amp_f = None

            for _hd in _humps:
                _lo_s = _hd['start_sod'];  _hi_s = _hd['end_sod']
                _wm_b = (_sods_b >= _lo_s) & (_sods_b <= _hi_s)
                _wm_f = (_sods_f >= _lo_s) & (_sods_f <= _hi_s)
                if _wm_b.sum() < 5:
                    continue
                _amp_b = float(_u_b[_wm_b].max() - _u_b[_wm_b].min())
                _amp_f = (float(_u_f[_wm_f].max() - _u_f[_wm_f].min())
                          if _wm_f.sum() >= 5 else float('nan'))
                _d_amp = _amp_f - _amp_b
                _pct   = (_d_amp / abs(_amp_b) * 100.
                          if abs(_amp_b) > 0.1 else float('nan'))
                # corr(Up, ZWD) in baseline window
                _uw = _u_b[_wm_b]; _zw = _zwd_b[_wm_b]
                _fin = np.isfinite(_uw) & np.isfinite(_zw)
                _r   = (float(np.corrcoef(_uw[_fin], _zw[_fin])[0, 1])
                        if _fin.sum() > 5 else float('nan'))
                print(f"[ZWD_FREEZE]   Hump {_hd['start_h']:.1f}–{_hd['end_h']:.1f}h  "
                      f"amp_base={_amp_b:.1f}mm  amp_frz="
                      f"{'N/A' if _amp_f != _amp_f else f'{_amp_f:.1f}mm'}  "
                      f"Δamp={_d_amp:+.1f}mm ({_pct:+.1f}%)  "
                      f"corr(Up,ZWD)={_r:+.3f}")
                if _major_amp_b is None or abs(_amp_b) > abs(_major_amp_b):
                    _major_amp_b = _amp_b
                    _major_amp_f = _amp_f

            # Verdict
            print(f"[ZWD_FREEZE] Verdict:", end=' ')
            if _major_amp_b is not None and _major_amp_f == _major_amp_f:
                _red = (_major_amp_b - _major_amp_f) / abs(_major_amp_b) * 100.
                if _red > 20.:
                    print(f"ZWD-VERTICAL COUPLING SUPPORTED "
                          f"(major hump reduced {_red:.1f}%)")
                else:
                    print(f"ZWD COUPLING WEAK — LOOK TO ORBIT/CLOCK LEAKAGE "
                          f"(hump change {_red:+.1f}%)")
            else:
                print("no hump windows detected — check detection thresholds")

            # --- compact comparison plot ---
            try:
                import matplotlib; matplotlib.use('Agg')
                import matplotlib.pyplot as plt
                _hrs_b = _sods_b / 3600.
                _hrs_f = _sods_f / 3600.
                fig, ax = plt.subplots(1, 1, figsize=(12, 5))
                fig.suptitle(
                    'PPP-AR v91 — ZWD Freeze Experiment\n'
                    'Up error: baseline vs ZWD frozen at t≥2 h  (GPS+Galileo FWD)',
                    fontsize=11, fontweight='bold')
                ax.plot(_hrs_b, _u_b, color='#4363d8', lw=0.8, alpha=0.90,
                        label=f'Baseline  Up-RMS={_rms_b:.1f} mm')
                ax.plot(_hrs_f, _u_f, color='#e6194b', lw=0.8, alpha=0.90,
                        label=f'ZWD frozen (t≥2h)  Up-RMS={_rms_f:.1f} mm')
                ax.axhline(0, color='black', lw=0.5)
                ax.axvline(_FREEZE_SOD / 3600., color='gray', lw=0.8, ls=':',
                           alpha=0.7, label='t=2h freeze start')
                for _hd in _humps:
                    ax.axvspan(_hd['start_h'], _hd['end_h'],
                               color='gold', alpha=0.25, zorder=0)
                ax.set_xlabel('Time (h)')
                ax.set_ylabel('Up Error (mm)')
                ax.set_ylim(-300, 300)
                ax.legend(fontsize=9)
                ax.grid(True, alpha=0.3)
                plt.tight_layout()
                _zf_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    'ppp_zwd_freeze.png')
                fig.savefig(_zf_path, dpi=150, bbox_inches='tight')
                print(f"[ZWD_FREEZE] Plot saved: {_zf_path}")
                plt.close(fig)
            except Exception as _ze:
                print(f"[ZWD_FREEZE] Plot failed: {_ze}")
        else:
            print("[ZWD_FREEZE] Could not compute metrics — skipping comparison.")
    # ======================================================================
    #  end TEST_ZWD_FREEZE
    # ======================================================================

    # v94: all hump attribution / orbit forensics / elev audit DISABLED for runtime
    _mode_humps = {}; _mode_data = {}
    # CM_CLOCK_DIAG and CLK_MECH_SEP remain flag-gated (both False in v94)
    if ENABLE_CM_CLOCK_DIAG:
        _common_mode_clock_leakage_diag(all_fwd, all_rts, REF, _mode_humps)
    if ENABLE_CLK_MECH_SEP:
        _clock_mechanism_separation_diagnostic(all_fwd, all_rts, REF,
                                               epochs, _common, APX)

    # ======================================================================
    #  v94 CLOCK-PROCESS-NOISE SENSITIVITY TEST
    #  Isolates whether Q[3,3] (receiver clock random-walk noise) drives hump1.
    #  Three GPS+Galileo FWD passes: clk_q x {0.3, 1.0, 3.0}.
    #  Everything else: measurement model, iono, ZWD, weights, ambiguities,
    #  arc_ramp_n — ALL at baseline values (arc_ramp_n=0 i.e. v91 baseline).
    # ======================================================================
    if ENABLE_CLK_Q_TEST:
        _t_clkq_start = _time.time()
        print(f"\n{'='*72}")
        print("[CLK_Q_TEST v94] Clock-process-noise sensitivity — hump1 (2–5.4 h)")
        print(f"  clk_q scales: {_CLK_Q_SCALES}  |  all other parameters: BASELINE")
        print(f"{'='*72}")

        _lr_q, _lo_q, _ = _lla(REF)
        _Re_q = _enu(_lr_q, _lo_q)

        def _hq_metrics(fwd_d, sod_lo, sod_hi):
            """hump amplitude + Up-RMS in [sod_lo, sod_hi]; overall 3D-RMS."""
            if not fwd_d:
                return float('nan'), float('nan'), float('nan')
            items = sorted(fwd_d.items())
            sods = np.array([s for s, _ in items])
            dx   = np.array([r['dx'] for _, r in items])
            u_mm = (_Re_q @ dx.T).T[:, 2] * 1e3
            d3   = np.linalg.norm(dx, axis=1) * 1e3
            mask = (sods >= sod_lo) & (sods <= sod_hi)
            if mask.sum() < 3:
                return float('nan'), float('nan'), float(np.sqrt(np.mean(d3**2)))
            u_w   = u_mm[mask]
            amp   = float(np.max(np.abs(u_w)))
            u_rms = float(np.sqrt(np.mean(u_w**2)))
            d3_rms = float(np.sqrt(np.mean(d3**2)))
            return amp, u_rms, d3_rms

        def _full_series(fwd_d):
            items = sorted(fwd_d.items())
            sods = np.array([s for s, _ in items])
            dx   = np.array([r['dx'] for _, r in items])
            u_mm = (_Re_q @ dx.T).T[:, 2] * 1e3
            d3   = np.linalg.norm(dx, axis=1) * 1e3
            return sods, u_mm, d3

        # Baseline metrics from already-completed GPS+Galileo FWD pass
        _sod_lo, _sod_hi = _CLK_Q_HUMP1_WIN
        _base_fwd_gg = all_fwd.get('GPS+Galileo', {})
        _b_amp, _b_urms, _b_3drms = _hq_metrics(_base_fwd_gg, _sod_lo, _sod_hi)
        _b_sods, _b_umm, _b_d3 = _full_series(_base_fwd_gg)
        print(f"\n  [BASELINE]  hump1_amp={_b_amp:.1f} mm  "
              f"hump1_Up-RMS={_b_urms:.1f} mm  3D-RMS={_b_3drms:.1f} mm")

        # Run the three clk_q passes
        _clkq_results = []   # list of dicts
        for _qs in _CLK_Q_SCALES:
            _t_qs = _time.time()
            _label_qs = f"FWD_CLK_Q_{_qs:.1f}x"
            _rts_store._data = []
            _fwd_qs, _ex_qs, _, _ez_qs, _, _, _ = _ppp_pass(
                epochs, nom=APX.copy(), iclk=0., izwd=0.20,
                direction=1, label=_label_qs, constellation='GE',
                clk_q_scale=_qs,
                **_common)
            _dt_qs = _time.time() - _t_qs
            _amp_qs, _urms_qs, _d3rms_qs = _hq_metrics(_fwd_qs, _sod_lo, _sod_hi)
            _sods_qs, _umm_qs, _d3_qs = _full_series(_fwd_qs)
            _da = ((_b_amp - _amp_qs) / _b_amp * 100.) if _b_amp > 0 else float('nan')
            _dd = ((_b_3drms - _d3rms_qs) / _b_3drms * 100.) if _b_3drms > 0 else float('nan')
            _tag = "baseline" if abs(_qs - 1.0) < 0.01 else f"x{_qs}"
            print(f"  [CLK_Q {_tag:8s}]  "
                  f"hump1_amp={_amp_qs:.1f} mm ({_da:+.1f}%)  "
                  f"hump1_Up-RMS={_urms_qs:.1f} mm  "
                  f"3D-RMS={_d3rms_qs:.1f} mm ({_dd:+.1f}%)  "
                  f"pass={_dt_qs:.0f}s")
            _clkq_results.append({'qs': _qs, 'fwd': _fwd_qs, 'label': _tag,
                                   'amp': _amp_qs, 'urms': _urms_qs,
                                   'd3rms': _d3rms_qs, 'delta_amp': _da,
                                   'sods': _sods_qs, 'umm': _umm_qs, 'd3': _d3_qs,
                                   'runtime': _dt_qs})

        # Decision
        print(f"\n  [CLK_Q_TEST DECISION]")
        _lo_r = next((r for r in _clkq_results if abs(r['qs']-0.3)<0.05), None)
        _hi_r = next((r for r in _clkq_results if abs(r['qs']-3.0)<0.05), None)
        if _lo_r and _hi_r and not (math.isnan(_lo_r['amp']) or math.isnan(_hi_r['amp'])):
            _spread = abs(_lo_r['amp'] - _hi_r['amp'])
            _rel    = _spread / max(_b_amp, 1.) * 100.
            if _rel > 15.:
                _dir = "TIGHTER clock reduces hump" if _lo_r['amp'] < _hi_r['amp'] else "LOOSER clock reduces hump"
                print(f"    Clock-Q SENSITIVE: amplitude spread={_spread:.1f} mm ({_rel:.1f}% of baseline)")
                print(f"    {_dir} — clock process noise IS a driver of hump1.")
            else:
                print(f"    Clock-Q INSENSITIVE: amplitude spread={_spread:.1f} mm ({_rel:.1f}% of baseline)")
                print(f"    Hump1 does NOT respond to clock-Q changes — mechanism elsewhere.")
        else:
            print("    Could not compute spread — check results.")

        print(f"\n  Total CLK_Q_TEST wall: {_time.time()-_t_clkq_start:.0f}s")

        # ── Plot 1: Up error overlay — hump1 window (±0.5 h padding) ─────
        # ── Plot 2: 3D error full arc ──────────────────────────────────────
        try:
            import matplotlib; matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            _WIN_LO_H = _sod_lo / 3600.
            _WIN_HI_H = _sod_hi / 3600.
            _PAD = 0.5
            _colors = {0.3: '#d62728', 1.0: '#1f77b4', 3.0: '#2ca02c'}
            _styles = {0.3: '-', 1.0: '-', 3.0: '--'}

            fig_q, (ax_up, ax_3d) = plt.subplots(2, 1, figsize=(13, 9))
            fig_q.suptitle(
                'PPP-AR v94 — Clock-Process-Noise Sensitivity Test (GPS+Galileo FWD)\n'
                f'clk_q_scale in {_CLK_Q_SCALES}  |  all other params: baseline (v91)\n'
                f'Hump1 window: {_WIN_LO_H:.2f}–{_WIN_HI_H:.2f} h',
                fontsize=10, fontweight='bold')

            # Panel (a): Up error — hump1 window zoom
            ax_up.axvspan(_WIN_LO_H, _WIN_HI_H, alpha=0.08, color='orange',
                          label='Hump1 window', zorder=0)
            for _r in _clkq_results:
                _c = _colors.get(_r['qs'], '#888888')
                _ls = _styles.get(_r['qs'], '-')
                _da_s = f"{_r['delta_amp']:+.1f}%" if not math.isnan(_r['delta_amp']) else ''
                ax_up.plot(_r['sods']/3600., _r['umm'], color=_c, lw=0.9,
                           linestyle=_ls, alpha=0.88,
                           label=f"x{_r['qs']}  "
                                 f"(hump={_r['amp']:.0f} mm {_da_s}  "
                                 f"3D={_r['d3rms']:.0f} mm)")
            ax_up.axhline(0, color='k', lw=0.5)
            ax_up.set_xlim(max(0., _WIN_LO_H - _PAD), min(25., _WIN_HI_H + _PAD))
            ax_up.set_ylim(-350, 350)
            ax_up.set_ylabel('Up Error (mm)')
            ax_up.set_title('(a) Up Error — hump1 window  '
                            f'[baseline hump={_b_amp:.0f} mm]')
            ax_up.legend(fontsize=8, loc='lower right')
            ax_up.grid(True, alpha=0.3)

            # Panel (b): 3D error full arc
            for _r in _clkq_results:
                _c = _colors.get(_r['qs'], '#888888')
                _ls = _styles.get(_r['qs'], '-')
                ax_3d.plot(_r['sods']/3600., _r['d3'], color=_c, lw=0.8,
                           linestyle=_ls, alpha=0.85,
                           label=f"x{_r['qs']}  3D-RMS={_r['d3rms']:.0f} mm")
            ax_3d.axhline(100, color='gray', linestyle='--', lw=0.7, label='10 cm')
            ax_3d.axhline(50,  color='gray', linestyle=':',  lw=0.7, label='5 cm')
            ax_3d.axvspan(_WIN_LO_H, _WIN_HI_H, alpha=0.05, color='orange')
            ax_3d.set_xlim(0, 25)
            ax_3d.set_ylim(0, 500)
            ax_3d.set_xlabel('Time (h)')
            ax_3d.set_ylabel('3D Error (mm)')
            ax_3d.set_title('(b) 3D Error — full 24h arc')
            ax_3d.legend(fontsize=8, loc='upper right')
            ax_3d.grid(True, alpha=0.3)

            plt.tight_layout()
            _qp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'ppp_clk_q_test.png')
            fig_q.savefig(_qp, dpi=150, bbox_inches='tight')
            print(f"[CLK_Q_TEST] Plot saved: {_qp}")
            plt.close(fig_q)
        except Exception as _qe:
            print(f"[CLK_Q_TEST] Plot failed: {_qe}")

    # ======================================================================
    #  v99 ADAPTIVE RESIDUAL CENSORING TEST
    #  Auto-detect hump windows from each constellation's baseline.
    #  Within those windows rerun with:
    #    A) censor worst-residual satellite (Rd→1e4)
    #    B) censor worst 2 satellites
    #    C) soft-downweight worst satellite (Rd×4 = 0.25 weight)
    #  Geometry guard per constellation (G≥6, E≥5, GE≥8).
    # ======================================================================
    if ENABLE_V99_RESID_CENSOR:
        print(f"\n{'='*72}")
        print("[RESID_CENSOR v99] Adaptive distributed-residual leakage test")
        print(f"{'='*72}")
        _t_rc_start = _time.time()

        _lr_rc, _lo_rc, _ = _lla(REF)
        _Re_rc = _enu(_lr_rc, _lo_rc)

        def _rc_metrics(fwd_d, win_lo, win_hi):
            """Returns dict: amp, urms, d3rms, sods, u_mm, d3."""
            if not fwd_d:
                nan = float('nan')
                return {'amp': nan, 'urms': nan, 'd3rms': nan,
                        'sods': np.array([]), 'u_mm': np.array([]), 'd3': np.array([])}
            items = sorted(fwd_d.items())
            sods  = np.array([s for s,_ in items])
            dx    = np.array([r['dx'] for _,r in items])
            u_mm  = (_Re_rc @ dx.T).T[:, 2] * 1e3
            d3    = np.linalg.norm(dx, axis=1) * 1e3
            mask  = (sods >= win_lo) & (sods <= win_hi)
            if mask.sum() < 3:
                nan = float('nan')
                return {'amp': nan, 'urms': nan,
                        'd3rms': float(np.sqrt(np.mean(d3**2))),
                        'sods': sods, 'u_mm': u_mm, 'd3': d3}
            u_w = u_mm[mask]
            return {'amp':   float(np.max(np.abs(u_w))),
                    'urms':  float(np.sqrt(np.mean(u_w**2))),
                    'd3rms': float(np.sqrt(np.mean(d3**2))),
                    'sods': sods, 'u_mm': u_mm, 'd3': d3}

        # Map mode label → (constellation, baseline_fwd, conv_sod)
        _RC_MODES = [
            ('GPS-only',     'G',  all_fwd.get('GPS-only',     {}), 7200.),
            ('Galileo-only', 'E',  all_fwd.get('Galileo-only', {}), 7200.),
            ('GPS+Galileo',  'GE', all_fwd.get('GPS+Galileo',  {}), 7200.),
        ]

        _rc_all_results = {}   # mode_label → {case: {win_lo,win_hi,metrics,freq}}

        for _mode_lbl, _const_rc, _base_fwd, _conv_sod in _RC_MODES:
            if not _base_fwd:
                print(f"  [{_mode_lbl}] no baseline data — skipping.")
                continue

            # ── Auto-detect hump windows ──────────────────────────────────────
            _b_items = sorted(_base_fwd.items())
            _b_sods  = np.array([s for s,_ in _b_items])
            _b_dx    = np.array([r['dx'] for _,r in _b_items])
            _b_u     = (_Re_rc @ _b_dx.T).T[:, 2] * 1e3
            _raw_humps = _detect_hump_windows(_b_sods, _b_u, conv_sod=_conv_sod)
            _humps = [h for h in _raw_humps if isinstance(h, dict)]
            if not _humps:
                print(f"  [{_mode_lbl}] no humps auto-detected — skipping.")
                continue

            # Use the largest-amplitude hump as primary window
            _primary = max(_humps, key=lambda h: abs(h['amplitude_mm']))
            _win_lo  = _primary['start_sod']
            _win_hi  = _primary['end_sod']
            _base_m  = _rc_metrics(_base_fwd, _win_lo, _win_hi)

            print(f"\n  [{_mode_lbl}]  hump window: "
                  f"{_win_lo/3600.:.2f}–{_win_hi/3600.:.2f} h  "
                  f"amp={_primary['amplitude_mm']:+.1f} mm")
            print(f"    BASELINE: amp={_base_m['amp']:.1f} mm  "
                  f"Up-RMS={_base_m['urms']:.1f} mm  3D-RMS={_base_m['d3rms']:.1f} mm")

            _mode_results = {'baseline': _base_m, 'win_lo': _win_lo, 'win_hi': _win_hi}

            for _case, _rc_tag in [('A', 'censor-1'), ('B', 'censor-2'), ('C', 'soft-dw')]:
                _freq_dict: dict = {}
                _rts_store._data = []
                _t0 = _time.time()
                _fwd_rc, _, _, _, _, _, _ = _ppp_pass(
                    epochs, nom=APX.copy(), iclk=0., izwd=0.20,
                    direction=1,
                    label=f"RC_{_case}_{_mode_lbl.replace('+','').replace('-only','')}",
                    constellation=_const_rc,
                    resid_censor=_case,
                    resid_censor_win=(_win_lo, _win_hi),
                    resid_censor_freq=_freq_dict,
                    **_common)
                _dt = _time.time() - _t0
                _m  = _rc_metrics(_fwd_rc, _win_lo, _win_hi)
                _da = ((_base_m['amp']   - _m['amp'])   / max(_base_m['amp'],   1.) * 100.
                       if not (math.isnan(_base_m['amp'])   or math.isnan(_m['amp']))
                       else float('nan'))
                _dd = ((_base_m['d3rms'] - _m['d3rms']) / max(_base_m['d3rms'], 1.) * 100.
                       if not (math.isnan(_base_m['d3rms']) or math.isnan(_m['d3rms']))
                       else float('nan'))
                # Top-5 repeatedly censored PRNs
                _top5 = sorted(_freq_dict.items(), key=lambda kv: -kv[1])[:5]
                print(f"    [{_case}] {_rc_tag:9s}  hump Δ={_da:+.1f}%  "
                      f"3D Δ={_dd:+.1f}%  "
                      f"amp={_m['amp']:.0f} mm  U-RMS={_m['urms']:.1f}  "
                      f"[{_dt:.0f}s]")
                print(f"      top censored: "
                      + ", ".join(f"{s}×{n}" for s,n in _top5) if _top5 else "      (none)")
                _mode_results[_case] = {'m': _m, 'da': _da, 'dd': _dd,
                                        'freq': _freq_dict, 'fwd': _fwd_rc}

            _rc_all_results[_mode_lbl] = _mode_results

        # ── Verdict per mode ──────────────────────────────────────────────────
        print(f"\n  ── Verdict table ──")
        print(f"  {'Mode':15s}  {'Case':12s}  {'Hump Δ%':>8}  {'3D Δ%':>7}  {'Verdict'}")
        print(f"  {'-'*15}  {'-'*12}  {'-'*8}  {'-'*7}  {'-'*30}")
        for _lbl, _mres in _rc_all_results.items():
            for _case in ('A', 'B', 'C'):
                if _case not in _mres:
                    continue
                _da_v = _mres[_case]['da']
                _dd_v = _mres[_case]['dd']
                if not math.isnan(_da_v) and _da_v > 10. and not (not math.isnan(_dd_v) and _dd_v < -10.):
                    _verd = "DISTRIBUTED LEAKAGE SUPPORTED"
                elif not math.isnan(_da_v) and abs(_da_v) < 5.:
                    _verd = "reject hypothesis"
                else:
                    _verd = "inconclusive"
                print(f"  {_lbl:15s}  {_case:12s}  {_da_v:+8.1f}%  {_dd_v:+7.1f}%  {_verd}")

        print(f"\n  Total RESID_CENSOR wall: {_time.time()-_t_rc_start:.0f}s")

        # ── Summary plot ──────────────────────────────────────────────────────
        try:
            import matplotlib; matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            _n_modes = len(_rc_all_results)
            if _n_modes > 0:
                fig_rc, axs_rc = plt.subplots(_n_modes, 1, figsize=(13, 4*_n_modes+1),
                                              sharex=False, squeeze=False)
                fig_rc.suptitle(
                    'PPP-AR v99 — Adaptive Residual Censoring (auto-detected hump windows)\n'
                    'A=censor-1  B=censor-2  C=soft-downweight  '
                    '(censoring active ONLY inside hump window)',
                    fontsize=10, fontweight='bold')

                _CASE_COLORS = {'baseline':'#1f77b4','A':'#d62728','B':'#2ca02c','C':'#ff7f0e'}
                _CASE_STYLES = {'baseline':'-','A':'--','B':':','C':'-.'}

                for _pi, (_lbl, _mres) in enumerate(_rc_all_results.items()):
                    ax = axs_rc[_pi][0]
                    _wlo = _mres['win_lo']; _whi = _mres['win_hi']
                    _bm  = _mres['baseline']
                    if len(_bm['sods']) > 0:
                        ax.plot(_bm['sods']/3600., _bm['u_mm'],
                                color=_CASE_COLORS['baseline'], lw=1.0, alpha=0.9,
                                label='baseline')
                    for _case in ('A', 'B', 'C'):
                        if _case not in _mres:
                            continue
                        _cm = _mres[_case]['m']
                        _da_s = f"{_mres[_case]['da']:+.1f}%"
                        if len(_cm['sods']) > 0:
                            ax.plot(_cm['sods']/3600., _cm['u_mm'],
                                    color=_CASE_COLORS[_case],
                                    lw=0.85, ls=_CASE_STYLES[_case], alpha=0.85,
                                    label=f"{_case}  Δhump={_da_s}")
                    ax.axvspan(_wlo/3600., _whi/3600., alpha=0.08, color='orange',
                               label='auto-hump window')
                    ax.axhline(0, color='k', lw=0.5)
                    ax.set_xlim(0, 25); ax.set_ylim(-350, 200)
                    ax.set_ylabel('Up Error (mm)')
                    ax.set_title(f'({chr(97+_pi)}) {_lbl}')
                    ax.legend(fontsize=8, ncol=3); ax.grid(True, alpha=0.3)
                axs_rc[-1][0].set_xlabel('Time (h)')
                plt.tight_layout()
                _rc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        'ppp_resid_censor_v99.png')
                fig_rc.savefig(_rc_path, dpi=150, bbox_inches='tight')
                print(f"[RESID_CENSOR] Plot saved: {_rc_path}")
                plt.close(fig_rc)
        except Exception as _rce:
            print(f"[RESID_CENSOR] Plot failed: {_rce}")
    print(f"\n  Wall: {_time.time()-t0:.1f}s")
    print("="*72)

    # ── Run L — ZWD process-noise ablation ───────────────────────────────────
    print(f"\n  Wall: {_time.time()-t0:.1f}s")
    print("="*72)

    # ── Run L — ZWD process-noise ablation ───────────────────────────────────
    if ENABLE_RUN_L:
        _outdir_l = os.path.dirname(os.path.abspath(__file__))
        _run_l_zwd_ablation(epochs, _common, APX, REF, outdir=_outdir_l)

    # ── v92 Ablation Suite ────────────────────────────────────────────────────
    # Three single-switch diagnostic runs on GPS+Galileo only.
    # Switch A: OTL disabled   (BLQ zeroed — tests ocean tide loading impact)
    # Switch B: Pole tide ON   (ERP loaded — adds IERS pole tide correction)
    # Switch C: NMF mapping    (NMF used instead of GMF — tests mapping sensitivity)
    # Each run is compared with the baseline GPS+Galileo result via _ablation_metrics.
    # Ambiguities, stochastics, satellite logic: UNCHANGED.
    # ===========================================================================
    if ABLATION_RUN_SUITE:
        print(f"\n{'='*72}")
        print("[ABLATION] Starting single-switch diagnostic runs (GPS+Galileo only)")
        print(f"{'='*72}")

        # Collect baseline metrics from the already-completed GPS+Galileo FWD pass
        abl_base_m = _ablation_metrics(primary_fwd, REF)

        abl_runs = [
            # (label, blq_override, erp_override, map_func_override)
            ('A_OTL_OFF',   {},      None,        None),
            ('B_PTIDE_ON',  blq,     erp_base,    None),
            ('C_NMF_MAP',   blq,     None,         _nmf),
            # D_VMF_MAP — single-switch: replace GMF with analytical VMF1-G.
            # All other settings (OTL, pole tide, stochastics, ambiguity
            # logic, weights, process noise) are identical to the baseline.
            ('D_VMF_MAP',   blq,     None,         _vmf1),
        ]

        abl_results = {}  # label → metrics dict

        for abl_label, abl_blq, abl_erp, abl_map in abl_runs:
            if abl_label == 'B_PTIDE_ON' and not erp_base:
                print(f"\n[ABLATION] {abl_label} — SKIPPED (no ERP file found)")
                abl_results[abl_label] = None
                continue

            print(f"\n[ABLATION] Run {abl_label}  "
                  f"OTL={'ON' if abl_blq else 'OFF'}  "
                  f"PTIDE={'ON' if abl_erp else 'OFF'}  "
                  f"MAP={'NMF' if abl_map is _nmf else 'VMF1' if abl_map is _vmf1 else 'GMF'}")
            _rts_store._data = []

            # Build override _common — only blq, erp, map_func differ
            _abl_common = dict(_common)
            _abl_common['blq']      = abl_blq
            _abl_common['erp']      = abl_erp
            _abl_common['map_func'] = abl_map

            abl_fwd, abl_ex, _, abl_ez, abl_wl, _, _ = _ppp_pass(
                epochs, nom=APX.copy(), iclk=0., izwd=0.20,
                direction=1, label=f"FWD_{abl_label}", constellation='GE',
                **_abl_common)

            print(f"  {len(abl_fwd)} epochs  "
                  f"end_3D={np.linalg.norm(abl_ex-REF)*1e3:.1f}mm  "
                  f"ZWD={abl_ez:.3f}m")
            m = _ablation_metrics(abl_fwd, REF)
            abl_results[abl_label] = m
            if m:
                print(f"  Up RMS={m['up_rms_mm']:.1f}mm  "
                      f"corr(Up,ZWD)={m['corr_up_zwd']:+.3f}  "
                      f"ΔUp[5-8h]={m['hump58_obs_up']:.1f}mm(ratio={m['hump58_ratio']:.2f})  "
                      f"ΔUp[17-20h]={m['hump1720_obs_up']:.1f}mm(ratio={m['hump1720_ratio']:.2f})")

        # ── Print ablation comparison table ──────────────────────────────────
        print(f"\n{'='*72}")
        print("[ABLATION] Results table — GPS+Galileo FWD  (single-switch vs baseline)")
        print(f"{'='*72}")
        hdr  = f"  {'Run':<18}  {'UpRMS':>8}  {'Δvs base':>9}  "
        hdr += f"{'corr(U,Z)':>10}  {'ΔUp[5-8h]':>10}  {'ratio58':>8}  "
        hdr += f"{'ΔUp[17-20h]':>12}  {'ratio1720':>9}"
        print(hdr)
        print("  " + "-"*98)

        rows = [('BASELINE (GMF+OTL)', abl_base_m)] + [
            (k, abl_results.get(k)) for k, *_ in abl_runs
        ]
        base_rms = abl_base_m['up_rms_mm'] if abl_base_m else float('nan')

        for run_name, m in rows:
            if m is None:
                print(f"  {run_name:<18}  {'SKIPPED':>8}")
                continue
            delta = m['up_rms_mm'] - base_rms
            delta_str = f"{delta:+.1f}" if run_name != 'BASELINE (GMF+OTL)' else "   ---"

            def _fmt(v, fmt='.1f'):
                return format(v, fmt) if not (v != v) else 'N/A'

            print(f"  {run_name:<18}  {_fmt(m['up_rms_mm']):>8}  {delta_str:>9}  "
                  f"  {_fmt(m['corr_up_zwd'], '+.3f'):>9}  "
                  f"  {_fmt(m['hump58_obs_up']):>9}  "
                  f"  {_fmt(m['hump58_ratio'], '.2f'):>7}  "
                  f"  {_fmt(m['hump1720_obs_up']):>11}  "
                  f"  {_fmt(m['hump1720_ratio'], '.2f'):>8}")

        print(f"\n[ABLATION] Interpretation guide (Runs A–D):")
        print(f"  Run A (OTL OFF)  : if ΔUp RMS < -5 mm  → OTL is WORSENING fit → check BLQ sign/amplitude")
        print(f"                     if ΔUp RMS > +5 mm  → OTL is HELPING       → loading is real signal")
        print(f"  Run B (Ptide ON) : if ΔUp RMS < -5 mm  → pole tide is real correction needed")
        print(f"  Run C (NMF map)  : if ΔUp RMS change > 5 mm or ratio changes significantly")
        print(f"                       → mapping function is a material source of hump")
        print(f"  Run D (VMF1 map) : VMF1-G vs GMF — if |ΔUp RMS| > 5 mm or hump ratio changes")
        print(f"                       significantly → GMF bias at site latitude drives the hump.")
        print(f"                     |ΔUp RMS| < 2 mm AND stable ratio → mapping function NOT the cause.")
        print(f"  obs/exp ratio ≈ 1 → ZWD leakage fully explains hump")
        print(f"  obs/exp ratio >> 1 → another source dominates (loading or orbit/clock error)")
        print(f"{'='*72}")

        # Write ablation CSV (Runs A–D)
        _abl_csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     'ppp_ablation.csv')
        try:
            with open(_abl_csv_path, 'w', newline='') as _acf:
                import csv as _acsv
                _aw = _acsv.writer(_acf)
                _aw.writerow(['run', 'up_rms_mm', 'delta_vs_base_mm',
                              'corr_up_zwd',
                              'hump58_obs_up_mm', 'hump58_ratio',
                              'hump1720_obs_up_mm', 'hump1720_ratio'])
                for run_name, m in rows:
                    if m is None:
                        _aw.writerow([run_name] + ['NA']*7)
                        continue
                    dv = m['up_rms_mm'] - base_rms
                    def _sv(v): return (f'{v:.4f}' if v == v else 'nan')
                    _aw.writerow([run_name,
                                  _sv(m['up_rms_mm']),
                                  f'{dv:+.4f}' if run_name != 'BASELINE (GMF+OTL)' else '0.0',
                                  _sv(m['corr_up_zwd']),
                                  _sv(m['hump58_obs_up']),
                                  _sv(m['hump58_ratio']),
                                  _sv(m['hump1720_obs_up']),
                                  _sv(m['hump1720_ratio'])])
            print(f"[ABLATION] CSV written: {_abl_csv_path}")
        except Exception as _ae:
            print(f"[ABLATION] CSV write failed: {_ae}")

        # ── Run E: Dual-harmonic vertical absorber ────────────────────────────
        # Post-processing only — no new KF pass.
        # Fits A1·sin(2πt/12h)+B1·cos(2πt/12h)+A2·sin(2πt/24h)+B2·cos(2πt/24h)
        # to Up residuals of the existing GPS+Galileo FWD pass.
        # --------------------------------------------------------------------
        if ENABLE_DUAL_HARMONIC:
            print(f"\n{'='*72}")
            print("[ABLATION] Run E_DUAL_HARM — dual-harmonic vertical fit  (post-processing)")
            print(f"{'='*72}")
            _m_gg_e = _compute_metrics(primary_fwd, REF)
            if _m_gg_e:
                _dh = _fit_dual_harmonic(_m_gg_e, conv_sod=_HARMONIC_CONV_SOD)
                if _dh is None:
                    print("[DUAL_HARM] Insufficient post-convergence epochs — skipped.")
                else:
                    _rms_e_base = float(np.sqrt(np.mean(
                        _dh['u_base_mm'][_dh['post_mask']]**2)))
                    _rms_e_fix  = float(np.sqrt(np.mean(
                        _dh['u_fixed_mm'][_dh['post_mask']]**2)))
                    _rms3_base  = float(np.sqrt(np.mean(
                        (np.array([r['dx'] for _, r in sorted(primary_fwd.items())]
                                  )@_compute_metrics(primary_fwd, REF).get('Re',
                                  _enu(*_lla(REF)[:2])).T)**2)
                    )) if False else float('nan')   # skip — use _m_gg_e directly
                    # Re-derive 3D RMS from m dict
                    _lr_e, _lo_e, _ = _lla(REF)
                    _Re_e = _enu(_lr_e, _lo_e)
                    _dx_e = np.array([r['dx'] for _, r in sorted(primary_fwd.items())])
                    _enu_e = (_Re_e @ _dx_e.T).T * 1e3
                    _pm = _dh['post_mask']
                    _rms3_b = float(np.sqrt(np.mean(
                        np.sum(_enu_e[_pm]**2, axis=1))))
                    _enu_e_fix = _enu_e.copy(); _enu_e_fix[:, 2] = _dh['u_fixed_mm']
                    _rms3_f = float(np.sqrt(np.mean(
                        np.sum(_enu_e_fix[_pm]**2, axis=1))))

                    print(f"[DUAL_HARM] n_fit={_dh['n_fit']} epochs  "
                          f"(fit: t≥{_HARMONIC_CONV_SOD/3600.:.1f} h)")
                    print(f"[DUAL_HARM]   12-h component:  "
                          f"A1={_dh['A1']:+.3f} mm  B1={_dh['B1']:+.3f} mm  "
                          f"amp12={_dh['amp12']:.3f} mm")
                    print(f"[DUAL_HARM]   24-h component:  "
                          f"A2={_dh['A2']:+.3f} mm  B2={_dh['B2']:+.3f} mm  "
                          f"amp24={_dh['amp24']:.3f} mm")
                    print(f"[DUAL_HARM]   Total amplitude  = {_dh['amplitude']:.3f} mm")
                    print(f"[DUAL_HARM]   Up  RMS: baseline={_rms_e_base:.1f} mm  "
                          f"corrected={_rms_e_fix:.1f} mm  "
                          f"Δ={_rms_e_fix - _rms_e_base:+.1f} mm")
                    print(f"[DUAL_HARM]   3D  RMS: baseline={_rms3_b:.1f} mm  "
                          f"corrected={_rms3_f:.1f} mm  "
                          f"Δ={_rms3_f - _rms3_b:+.1f} mm")
                    _dh_amp_ratio = _dh['amp24'] / _dh['amp12'] if _dh['amp12'] > 0.1 else float('nan')
                    print(f"[DUAL_HARM]   amp24/amp12 ratio = {_dh_amp_ratio:.3f}")
                    print(f"[DUAL_HARM] Interpretation:")
                    if _dh['amp24'] > 20.0:
                        print(f"[DUAL_HARM]   24-h amplitude {_dh['amp24']:.1f} mm >> 5 mm "
                              f"→ strong DIURNAL component not captured by 12-h model")
                        print(f"[DUAL_HARM]   → Candidate: subdaily loading (S1/S2 tides), "
                              f"diurnal orbit error, or ionospheric cycle.")
                    elif _dh['amp24'] > 5.0:
                        print(f"[DUAL_HARM]   24-h amplitude {_dh['amp24']:.1f} mm moderate "
                              f"→ modest diurnal signal; investigate subdaily loading.")
                    else:
                        print(f"[DUAL_HARM]   24-h amplitude {_dh['amp24']:.1f} mm < 5 mm "
                              f"→ diurnal component negligible; 12-h dominates.")
                    _outdir_e = os.path.dirname(os.path.abspath(__file__))
                    _plot_dual_harmonic(_dh, primary_fwd, 'GPS+Galileo FWD', _outdir_e)
            else:
                print("[DUAL_HARM] No GPS+Galileo FWD metrics available — skipped.")

        # ── Run F: Subdaily loading absorber ──────────────────────────────────
        # New GPS+Galileo FWD pass with extra v_load state (vertical random walk).
        # Ambiguity, ZWD, iono, clock, and position states: unchanged.
        # v_load process noise: ~(10 mm)²/h  (gentle absorber).
        # --------------------------------------------------------------------
        print(f"\n{'='*72}")
        print("[ABLATION] Run F_SUBDAILY_LOADING — subdaily loading absorber (extra KF state)")
        print(f"{'='*72}")
        _rts_store._data = []
        _abl_f_common = dict(_common)
        # inherit baseline settings (OTL ON, GMF, no ptide)
        _fwd_sda, _ex_sda, _, _ez_sda, _wl_sda, _, _ = _ppp_pass(
            epochs, nom=APX.copy(), iclk=0., izwd=0.20,
            direction=1, label="FWD_F_SUBDAILY", constellation='GE',
            enable_subdaily_absorber=True,
            **_abl_f_common)
        print(f"  {len(_fwd_sda)} epochs  "
              f"end_3D={np.linalg.norm(_ex_sda - REF)*1e3:.1f} mm  "
              f"ZWD={_ez_sda:.3f} m")
        _m_f = _ablation_metrics(_fwd_sda, REF)
        if _m_f:
            _delta_f = _m_f['up_rms_mm'] - (abl_base_m['up_rms_mm'] if abl_base_m else 0.)
            print(f"  Up RMS={_m_f['up_rms_mm']:.1f} mm  "
                  f"Δvs baseline={_delta_f:+.1f} mm  "
                  f"corr(Up,ZWD)={_m_f['corr_up_zwd']:+.3f}")
            print(f"  ΔUp[5-8h]={_m_f['hump58_obs_up']:.1f} mm  "
                  f"ratio={_m_f['hump58_ratio']:.2f}  "
                  f"ΔUp[17-20h]={_m_f['hump1720_obs_up']:.1f} mm  "
                  f"ratio={_m_f['hump1720_ratio']:.2f}")
            # v_load statistics
            _vl_mm = np.array([r.get('v_load', 0.) * 1e3
                                for _, r in sorted(_fwd_sda.items())])
            _vl_rms = float(np.sqrt(np.mean(_vl_mm**2)))
            _vl_pk  = float(np.max(np.abs(_vl_mm)))
            print(f"  v_load RMS={_vl_rms:.2f} mm  peak={_vl_pk:.2f} mm")
            print(f"[F_INTERP] Interpretation:")
            if abs(_delta_f) > 5.0 and _delta_f < 0.:
                print(f"[F_INTERP]   Up RMS reduced by {abs(_delta_f):.1f} mm "
                      f"→ absorber captures real subdaily loading signal.")
                print(f"[F_INTERP]   v_load peak {_vl_pk:.1f} mm estimates loading amplitude.")
            elif abs(_delta_f) > 5.0 and _delta_f > 0.:
                print(f"[F_INTERP]   Up RMS INCREASED by {_delta_f:.1f} mm "
                      f"→ absorber is absorbing signal, not noise; source is NOT subdaily loading.")
            else:
                print(f"[F_INTERP]   |ΔUp RMS| ≤ 5 mm → subdaily loading absorber has "
                      f"negligible effect; hump source is not a broadband vertical drift.")
            _outdir_f = os.path.dirname(os.path.abspath(__file__))
            _plot_subdaily_absorber(primary_fwd, _fwd_sda, all_fwd, REF, _outdir_f)

        # ── Run G: Clock/Orbit residual audit ────────────────────────────────
        # Post-processing on the existing GPS+Galileo FWD pass.
        # Correlates code_rms, phase_rms, and clock drift rate with Up error.
        # No new KF pass required.
        # --------------------------------------------------------------------
        if ENABLE_CLOCK_ORBIT_AUDIT:
            print(f"\n{'='*72}")
            print("[ABLATION] Run G_CLOCK_BIAS_DRIFT_AUDIT — clock/orbit residual correlation")
            print(f"{'='*72}")
            _clock_orbit_audit(primary_fwd, REF)

        # ── Updated interpretation guide (Runs E–G) ──────────────────────────
        print(f"\n[ABLATION] Interpretation guide (Runs E–G):")
        print(f"  Run E (Dual harm) : amp24 > 20 mm → strong 24-h (diurnal) component "
              f"→ subdaily loading or diurnal orbit error.")
        print(f"                      amp24 < 5 mm  → 12-h satellite-repeat dominates; "
              f"investigate SP3 clock/orbit quality.")
        print(f"  Run F (SDA)       : |ΔUp RMS| > 5 mm downward → absorber captures real signal "
              f"→ subdaily loading or mismodelled vertical forcing.")
        print(f"                      |ΔUp RMS| < 5 mm           → hump is NOT broadband vertical drift.")
        print(f"  Run G (Clk audit) : |r(Up,code)| or |r(Up,phase)| > 0.7 in hump windows "
              f"→ systematic residuals drive hump → SP3/CLK quality issue.")
        print(f"                      all |r| < 0.3 → observable residuals do not explain "
              f"hump → loading or unmodelled geophysical signal.")
        print(f"{'='*72}")
    # ── end ablation suite ───────────────────────────────────────────────────


    # =========================================================================
    # v94 Forward-Filter Leakage Verification -- Runs H, I, J
    # =========================================================================
    if ENABLE_LEAKAGE_DIAG and ABLATION_RUN_SUITE:
        print("\n" + "="*72)
        print("[LEAKAGE] Forward-Filter Leakage Verification (Runs H / I / J)")
        print("="*72)

        _outdir_lk = os.path.dirname(os.path.abspath(__file__))

        # Run H_Q_LO: pos+clk Q x0.3 ----------------------------------------
        print("\n[LEAKAGE] Run H_Q_LO  (pos+clk Q x0.3, all other Q unchanged)")
        _rts_store._data = []
        _fwd_h_lo, *_ = _ppp_pass(
            epochs, nom=APX.copy(), iclk=0., izwd=0.20,
            direction=1, label="FWD_H_QLO", constellation="GE",
            pos_clk_q_scale=0.3, **_common)

        # Run H_Q_HI: pos+clk Q x3.0 ----------------------------------------
        print("[LEAKAGE] Run H_Q_HI  (pos+clk Q x3.0, all other Q unchanged)")
        _rts_store._data = []
        _fwd_h_hi, *_ = _ppp_pass(
            epochs, nom=APX.copy(), iclk=0., izwd=0.20,
            direction=1, label="FWD_H_QHI", constellation="GE",
            pos_clk_q_scale=3.0, **_common)

        _h_stats = _run_h_q_audit(_fwd_h_lo, _fwd_h_hi, primary_fwd, REF)

        # Run I: spectral audit (no new KF pass) -----------------------------
        print("\n[LEAKAGE] Run I  (innovation spectral audit, no new KF pass)")
        _i_stats = _run_i_innov_spectral(primary_fwd, REF)

        # Run J_CLK_POS: +2 cm perturbation ----------------------------------
        print("\n[LEAKAGE] Run J_CLK_POS  (+{:.0f} mm uniform obs shift)".format(
              _LEAKAGE_CLK_PERTURB_M * 1e3))
        _rts_store._data = []
        _fwd_j_pos, *_ = _ppp_pass(
            epochs, nom=APX.copy(), iclk=0., izwd=0.20,
            direction=1, label="FWD_J_POS", constellation="GE",
            clk_perturb_m=+_LEAKAGE_CLK_PERTURB_M, **_common)

        # Run J_CLK_NEG: -2 cm perturbation ----------------------------------
        print("[LEAKAGE] Run J_CLK_NEG  (-{:.0f} mm uniform obs shift)".format(
              _LEAKAGE_CLK_PERTURB_M * 1e3))
        _rts_store._data = []
        _fwd_j_neg, *_ = _ppp_pass(
            epochs, nom=APX.copy(), iclk=0., izwd=0.20,
            direction=1, label="FWD_J_NEG", constellation="GE",
            clk_perturb_m=-_LEAKAGE_CLK_PERTURB_M, **_common)

        _j_stats = _run_j_clk_verdict(
            primary_fwd, _fwd_j_pos, _fwd_j_neg,
            REF, _LEAKAGE_CLK_PERTURB_M)

        # Forensic plot -------------------------------------------------------
        _plot_leakage_forensic(
            _h_stats, _i_stats, _j_stats,
            primary_fwd, _fwd_j_pos, _fwd_j_neg,
            _fwd_h_lo, _fwd_h_hi,
            REF, _LEAKAGE_CLK_PERTURB_M, _outdir_lk)

        # Consolidated verdict ------------------------------------------------
        print("\n" + "="*72)
        print("[LEAKAGE] CONSOLIDATED FORENSIC VERDICT")
        print("="*72)
        _verdicts_lk = [
            ("Run H (Q scale)",     _h_stats.get("verdict", "n/a")),
            ("Run I (Spectral)",    _i_stats.get("verdict", "n/a")),
            ("Run J (Clk perturb)", _j_stats.get("verdict", "n/a")),
        ]
        _n_confirm = sum(1 for _, v in _verdicts_lk
                         if any(k in v.upper() for k in
                                ("CONFIRMED", "DETECTED", "PEAK")))
        _n_reject  = sum(1 for _, v in _verdicts_lk if "REJECTED" in v.upper())
        for _rl, _vl in _verdicts_lk:
            print("  {:22s}: {}".format(_rl, _vl))
        print()
        if _n_confirm >= 2:
            _overall = ("FORWARD-FILTER LEAKAGE CONFIRMED"
                        " ({}/{} diagnostic runs)".format(_n_confirm, len(_verdicts_lk)))
        elif _n_reject >= 2:
            _overall = ("FORWARD-FILTER LEAKAGE REJECTED"
                        " ({}/{} diagnostic runs)".format(_n_reject, len(_verdicts_lk)))
        else:
            _overall = "INCONCLUSIVE -- mixed evidence; hump origin undetermined"
        print("[LEAKAGE] Overall: {}".format(_overall))
        print("="*72)

        # ── Run K: Hump Decomposition + Orbit/Clock Whitening Test (v95) ─────
        print("\n" + "="*72)
        print("[ABLATION] Run K — Hump Decomposition + AR(1) Whitening Test")
        print("="*72)

        # Part 1: post-processing decomposition using already-computed baseline FWD
        # (primary_fwd already has code_per_sat stored from the main GPS+Galileo pass)
        print("\n[LEAKAGE] Run K_DECOMP  (post-processing hump decomposition, no new KF pass)")
        _k_out = _run_k_hump_decomp(primary_fwd, None, REF)

        # Part 2: AR(1) whitening sweep — phi ∈ {0.90, 0.95, 0.98}
        # Each pass is a full independent KF re-run with its own phi.
        # Goal: verify whether correlated measurement noise / orbit leakage
        # explains the hump, and quantify sensitivity versus phi.
        _AR1_PHI_SWEEP = [0.90, 0.95, 0.98]
        _fwd_whiten_by_phi = {}   # phi → fwd dict

        # Run K_WHITEN: AR(1) obs-whitening sweep — DISABLED (RUN_WHITEN_TEST=False)
        # 3 full _ppp_pass reruns (phi ∈ {0.90, 0.95, 0.98}) removed.
        if RUN_WHITEN_TEST:
            for _phi_k in _AR1_PHI_SWEEP:
                print(f"\n[LEAKAGE] Run K_WHITEN  (AR(1) pre-whitening, phi={_phi_k:.2f})")
                _rts_store._data = []
                _fwd_w_k, *_ = _ppp_pass(
                    epochs, nom=APX.copy(), iclk=0., izwd=0.20,
                    direction=1, label=f"FWD_K_WHITEN_phi{int(_phi_k*100)}",
                    constellation="GE",
                    obs_whitening=True, ar1_phi=_phi_k, **_common)
                _fwd_whiten_by_phi[_phi_k] = _fwd_w_k
        else:
            print("[LEAKAGE] Run K_WHITEN skipped (RUN_WHITEN_TEST=False)")

        # Re-run decomposition using baseline + the phi=0.95 whitened pass
        # (for backward-compatible full output and plot)
        _k_out_full = _run_k_hump_decomp(
            primary_fwd, _fwd_whiten_by_phi.get(0.95), REF,
            whiten_by_phi=_fwd_whiten_by_phi)

        # Interpretation guide
        print("\n[K_DECOMP] Interpretation guide (Run K):")
        print("  K_DECOMP (partial R²):")
        print("    orbit_up > 0.25 in hump window → orbit residual "
              "autocorrelation is primary driver → check SP3 quality.")
        print("    dclk_dt  > 0.25 → Q-leakage via clock channel confirmed.")
        print("    ZWD      > 0.25 → troposphere leakage dominant (consistent "
              "with ZWD audit result).")
        print("    All < 0.10 → hump source not explained by observable "
              "regressors → unmodelled geophysical/loading signal.")
        print("  K_WHITEN (AR(1) whitening sweep phi ∈ {0.90, 0.95, 0.98}):")
        print("    |ΔUp RMS| > 5 mm downward for ALL phi → code temporal "
              "correlation robustly drives the hump.")
        print("    Effect grows monotonically with phi → coloured noise "
              "model is appropriate; higher phi = longer memory.")
        print("    Effect non-monotonic or < 5 mm for all phi → short-term "
              "code autocorrelation is NOT the primary hump driver.")
        print("="*72)

        # Plot — disabled when RUN_WHITEN_TEST=False (no whitened data available)
        if RUN_WHITEN_TEST:
            _plot_hump_decomp(_k_out_full, outdir=_outdir_lk,
                              whiten_by_phi=_fwd_whiten_by_phi)

    # -- end leakage diagnostic --

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

    try:
        _plot_comparison(all_fwd, all_rts, REF)
    except NameError:
        print('[WARN] comparison plot skipped (_plot_comparison missing)')
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

def _plot_comparison(all_fwd, all_rts, REF, outdir=None):
    """Multi-constellation comparison figure: 3D error, ENU, NL count, FWD vs RTS."""
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("[PLOT] matplotlib not available — skipping comparison plot"); return

    if outdir is None:
        outdir = os.path.dirname(os.path.abspath(__file__))

    colors = {'GPS-only': '#e6194b', 'Galileo-only': '#4363d8', 'GPS+Galileo': '#3cb44b'}
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(
        'PPP-AR Multi-Constellation (v94) — clock-Q sensitivity test (baseline v91)',
        fontsize=14, fontweight='bold')

    ax = axes[0, 0]
    for label, fwd in all_fwd.items():
        m = _compute_metrics(fwd, REF)
        if m is None: continue
        ax.plot(m['sods'] / 3600., m['d3_mm'], color=colors.get(label, 'k'),
                alpha=0.8, linewidth=0.8, label=label)
    ax.axhline(100, color='gray', linestyle='--', linewidth=0.7, label='10 cm')
    ax.axhline(50,  color='gray', linestyle=':',  linewidth=0.7, label='5 cm')
    ax.set_xlabel('Time (h)'); ax.set_ylabel('3D Error (mm)')
    ax.set_title('(a) 3D Positioning Error — FWD')
    ax.set_ylim(0, 500); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    ax = axes[0, 1]
    m = _compute_metrics(all_fwd.get('GPS+Galileo', {}), REF)
    if m is not None:
        sh = m['sods'] / 3600.
        ax.plot(sh, m['e_mm'], color='#e6194b', linewidth=0.8, label='East')
        ax.plot(sh, m['n_mm'], color='#3cb44b', linewidth=0.8, label='North')
        ax.plot(sh, m['u_mm'], color='#4363d8', linewidth=0.8, label='Up')
        ax.axhline(0, color='black', linewidth=0.5)
    ax.set_xlabel('Time (h)'); ax.set_ylabel('Error (mm)')
    ax.set_title('(b) ENU — GPS+Galileo FWD')
    ax.set_ylim(-300, 300); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    ax = axes[1, 0]
    for label, fwd in all_fwd.items():
        m = _compute_metrics(fwd, REF)
        if m is None: continue
        ax.plot(m['sods'] / 3600., m['nl_counts'], color=colors.get(label, 'k'),
                linewidth=0.9, label=f'{label} NL')
    ax.set_xlabel('Time (h)'); ax.set_ylabel('# NL-fixed sats')
    ax.set_title('(c) NL-Fixed Ambiguities'); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    ax = axes[1, 1]
    mf = _compute_metrics(all_fwd.get('GPS+Galileo', {}), REF)
    mr = _compute_metrics(all_rts.get('GPS+Galileo', {}), REF)
    if mf: ax.plot(mf['sods'] / 3600., mf['d3_mm'], color='#4363d8', linewidth=0.8,
                   alpha=0.8, label='FWD')
    if mr: ax.plot(mr['sods'] / 3600., mr['d3_mm'], color='#f58231', linewidth=0.8,
                   alpha=0.8, label='RTS')
    ax.axhline(50, color='gray', linestyle='--', linewidth=0.7)
    ax.set_xlabel('Time (h)'); ax.set_ylabel('3D Error (mm)')
    ax.set_title('(d) FWD vs RTS — GPS+Galileo')
    ax.set_ylim(0, 300); ax.legend(fontsize=8); ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plot_path = os.path.join(outdir, 'ppp_comparison.png')
    try:
        fig.savefig(plot_path, dpi=150, bbox_inches='tight')
        print(f"[PLOT] Saved: {plot_path}")
    except Exception as e:
        print(f"[PLOT] Could not save: {e}")
    plt.close(fig)


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


def _ablation_metrics(fwd, REF):
    """Compute ablation-table metrics from a forward pass dict (GPS+Galileo FWD).

    Returns dict with keys:
        up_rms_mm        : Up RMS post-convergence (t >= 2 h) [mm]
        corr_up_zwd      : Pearson corr(Up, ZWD) full-arc
        hump58_obs_up    : obs ΔUp in 5-8 h window [mm]
        hump1720_obs_up  : obs ΔUp in 17-20 h window [mm]
        hump58_ratio     : obs/exp leakage ratio 5-8 h
        hump1720_ratio   : obs/exp leakage ratio 17-20 h
    NaN is returned for any window with fewer than 5 epochs.
    """
    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)
    fwd_list = sorted(fwd.items())
    if len(fwd_list) < 10:
        return None

    sods    = np.array([s           for s, _ in fwd_list])
    hrs     = sods / 3600.
    dx_all  = np.array([r["dx"]     for _, r in fwd_list])
    zwd     = np.array([r.get("zwd",   0.) for _, r in fwd_list])
    mw_mean = np.array([r.get("mw_mean", 5.) for _, r in fwd_list])

    enu_all = (Re @ dx_all.T).T * 1e3
    up_mm   = enu_all[:, 2]
    zwd_mm  = zwd * 1e3

    # Up RMS post-convergence (t >= 2 h)
    pc_mask   = hrs >= 2.0
    up_rms_mm = (float(np.sqrt(np.mean(up_mm[pc_mask]**2)))
                 if pc_mask.sum() >= 5 else float("nan"))

    # corr(Up, ZWD) full arc
    def _corr(a, b):
        if a.std() < 1e-10 or b.std() < 1e-10:
            return float("nan")
        return float(np.corrcoef(a, b)[0, 1])
    corr_up_zwd = _corr(up_mm, zwd_mm)

    # Hump window stats
    def _hump(hmask):
        if hmask.sum() < 5:
            return float("nan"), float("nan")
        obs_up  = float(up_mm[hmask].max() - up_mm[hmask].min())
        zwd_rng = float(zwd_mm[hmask].max() - zwd_mm[hmask].min())
        mw_avg  = float(mw_mean[hmask].mean())
        exp_up  = mw_avg * zwd_rng
        ratio   = obs_up / exp_up if abs(exp_up) > 0.1 else float("nan")
        return obs_up, ratio

    h58   = (hrs >= 5.0) & (hrs <= 8.0)
    h1720 = (hrs >= 17.) & (hrs <= 20.0)
    obs58,   r58   = _hump(h58)
    obs1720, r1720 = _hump(h1720)

    return dict(
        up_rms_mm       = up_rms_mm,
        corr_up_zwd     = corr_up_zwd,
        hump58_obs_up   = obs58,
        hump1720_obs_up = obs1720,
        hump58_ratio    = r58,
        hump1720_ratio  = r1720,
    )

def _zwd_up_audit(fwd, REF, outdir=None):
    """
    Diagnostic audit: does ZWD-to-vertical leakage explain the Up humps?

    Inputs
    ------
    fwd    : {sod: result_dict} from _ppp_pass (GPS+Galileo FWD pass).
             Each dict must have 'dx', 'zwd', 'mw_mean' keys.
    REF    : reference ECEF position (numpy array, metres).
    outdir : directory for CSV / PNG output (defaults to script directory).

    Outputs (all written to outdir)
    -------
    zwd_up_audit.csv          time-series of Up error, ZWD, dZWD/dt
    ppp_zwd_audit.png         4-panel overlay + lagged cross-correlation

    Console
    -------
    [ZWD_AUDIT] correlation table (full arc + hump windows)
    [ZWD_AUDIT] lagged-xcorr peak (lag in minutes)
    [ZWD_AUDIT] per-hump ZWD drift magnitude and mean mw
    [ZWD_AUDIT] experiment label (ZWD_Q_SCALE value)
    """
    if outdir is None:
        outdir = os.path.dirname(os.path.abspath(__file__))

    # ── 1. Build time-series arrays ──────────────────────────────────────────
    lr, lo, _ = _lla(REF)
    Re        = _enu(lr, lo)
    fwd_list  = sorted(fwd.items())               # [(sod, result), ...]
    if len(fwd_list) < 10:
        print("[ZWD_AUDIT] Too few epochs — skipping.")
        return

    sods    = np.array([s           for s, _ in fwd_list])   # seconds
    hrs     = sods / 3600.                                    # hours
    dx_all  = np.array([r['dx']     for _, r in fwd_list])   # ECEF residuals (m)
    zwd     = np.array([r.get('zwd',   0.) for _, r in fwd_list])  # m
    mw_mean = np.array([r.get('mw_mean',5.) for _, r in fwd_list]) # dimensionless

    enu_all = (Re @ dx_all.T).T * 1e3     # mm, shape (N, 3)
    up_mm   = enu_all[:, 2]               # Up residual (mm)
    zwd_mm  = zwd * 1e3                   # ZWD in mm for correlation

    # ZWD first derivative (central differences, endpoint forward/backward)
    N = len(sods)
    dzwd_dt = np.zeros(N)
    if N >= 3:
        # interior: central diff
        dzwd_dt[1:-1] = (zwd_mm[2:] - zwd_mm[:-2]) / 2.0
        # endpoints: forward / backward
        dzwd_dt[0]    =  zwd_mm[1]  - zwd_mm[0]
        dzwd_dt[-1]   =  zwd_mm[-1] - zwd_mm[-2]
    # units: mm per epoch interval (~30 s)

    # ── 2. Write zwd_up_audit.csv ────────────────────────────────────────────
    csv_path = os.path.join(outdir, 'zwd_up_audit.csv')
    with open(csv_path, 'w', newline='') as cf:
        cw = _csv.writer(cf)
        cw.writerow(['time', 'up_err', 'zwd', 'dzwd_dt',
                     'mw_mean', 'zwd_q_scale'])
        q_flag = ZWD_Q_SCALE
        for i in range(N):
            cw.writerow([f'{sods[i]:.1f}',
                         f'{up_mm[i]:.3f}',
                         f'{zwd[i]:.6f}',
                         f'{dzwd_dt[i]:.4f}',
                         f'{mw_mean[i]:.3f}',
                         q_flag])
    print(f'[ZWD_AUDIT] CSV written: {csv_path}')

    # ── 3. Helper: windowed Pearson correlation ──────────────────────────────
    def _corr(a, b, mask):
        if mask.sum() < 5:
            return float('nan')
        a_ = a[mask]; b_ = b[mask]
        if a_.std() < 1e-10 or b_.std() < 1e-10:
            return float('nan')
        return float(np.corrcoef(a_, b_)[0, 1])

    # ── 4. Correlation table ─────────────────────────────────────────────────
    hump_windows = [
        ('full arc',    (hrs >= 0.0) & (hrs <= 25.0)),
        ('5–8 h',       (hrs >= 5.0) & (hrs <=  8.0)),
        ('17–20 h',     (hrs >= 17.) & (hrs <= 20.0)),
    ]
    print(f'\n[ZWD_AUDIT] ZWD-Q scale = {ZWD_Q_SCALE:.1f}×')
    print( '[ZWD_AUDIT] Pearson correlations — Up residual vs ZWD state:')
    print( '[ZWD_AUDIT]   {:15s}  corr(Up,ZWD)  corr(Up,dZWD/dt)  n_ep'.format('Window'))
    corr_table = []
    for wname, wmask in hump_windows:
        c_zwd  = _corr(up_mm, zwd_mm,  wmask)
        c_dzwd = _corr(up_mm, dzwd_dt, wmask)
        n_ep   = int(wmask.sum())
        corr_table.append((wname, c_zwd, c_dzwd, n_ep))
        print(f'[ZWD_AUDIT]   {wname:15s}  {c_zwd:+.3f}         {c_dzwd:+.3f}            {n_ep}')

    # ── 5. Lagged cross-correlation (test if ZWD leads Up) ──────────────────
    # Positive lag k → ZWD leads Up by k epochs.
    # Use the full arc (post-convergence subset ≥ 2 h for cleanliness).
    _fc = (hrs >= 2.0)
    u_fc  = up_mm[_fc];  z_fc = zwd_mm[_fc]
    max_lag = min(60, len(u_fc) // 4)   # up to 60 epochs = ~30 min at 30 s

    lags      = np.arange(-max_lag, max_lag + 1)
    xcorr_vals = np.zeros(len(lags))
    u_norm = u_fc - u_fc.mean()
    z_norm = z_fc - z_fc.mean()
    u_std  = u_norm.std() or 1.
    z_std  = z_norm.std() or 1.
    for li, lag in enumerate(lags):
        if lag == 0:
            # contemporaneous
            xcorr_vals[li] = float(np.mean(u_norm * z_norm)) / (u_std * z_std)
        elif lag > 0:
            # ZWD leads Up by `lag` epochs
            xcorr_vals[li] = float(np.mean(u_norm[lag:] * z_norm[:-lag])) / (u_std * z_std)
        else:
            # Up leads ZWD (lag < 0)
            k = -lag
            xcorr_vals[li] = float(np.mean(u_norm[:-k] * z_norm[k:])) / (u_std * z_std)

    peak_li   = int(np.argmax(np.abs(xcorr_vals)))
    peak_lag  = int(lags[peak_li])
    peak_xcr  = float(xcorr_vals[peak_li])
    lag_min   = peak_lag * 30. / 60.   # assuming 30 s epochs → minutes
    print(f'[ZWD_AUDIT] Lagged cross-correlation peak: lag={peak_lag} epochs '
          f'({lag_min:+.1f} min)  r={peak_xcr:+.3f}')
    if   peak_lag > 0:
        print(f'[ZWD_AUDIT]   → ZWD leads Up by {lag_min:.1f} min '
              f'(ZWD excursion precedes Up hump — consistent with leakage)')
    elif peak_lag < 0:
        print(f'[ZWD_AUDIT]   → Up leads ZWD by {-lag_min:.1f} min '
              f'(Up moves first — ZWD likely driven by position, not vice versa)')
    else:
        print(f'[ZWD_AUDIT]   → Contemporaneous peak (no detectable lead/lag)')

    # ── 6. Hump-window statistics ────────────────────────────────────────────
    print('[ZWD_AUDIT] Hump-window detail:')
    for wname, wmask in hump_windows[1:]:   # skip 'full arc'
        n_w = wmask.sum()
        if n_w < 3:
            continue
        zwd_rng = float(zwd_mm[wmask].max() - zwd_mm[wmask].min())
        mw_avg  = float(mw_mean[wmask].mean())
        up_rng  = float(up_mm[wmask].max()  - up_mm[wmask].min())
        # Expected Up excursion if purely ZWD-driven: ΔUp ≈ mw_avg × ΔZWD
        exp_up  = mw_avg * zwd_rng
        dzwd_max = float(np.abs(dzwd_dt[wmask]).max())
        # Does Up track ZWD excursions? check directional agreement
        up_w   = up_mm[wmask]  - up_mm[wmask].mean()
        zwd_w  = zwd_mm[wmask] - zwd_mm[wmask].mean()
        agree  = int(np.sum(np.sign(up_w) == np.sign(zwd_w)))
        pct    = 100. * agree / max(n_w, 1)
        print(f'[ZWD_AUDIT]   [{wname}]  ΔZWD={zwd_rng:.1f} mm  '
              f'mw_avg={mw_avg:.2f}  exp_ΔUp={exp_up:.1f} mm  '
              f'obs_ΔUp={up_rng:.1f} mm  '
              f'dZWD_max={dzwd_max:.2f} mm/ep  '
              f'sign_agree={pct:.0f}%')
        if abs(exp_up) > 1. and abs(up_rng) > 1.:
            ratio = up_rng / exp_up
            print(f'[ZWD_AUDIT]     obs/exp ratio = {ratio:.2f} '
                  f'{"(strong leakage)" if 0.5 < ratio < 2. else "(poor match)"}')

    # ── 7. Plot ──────────────────────────────────────────────────────────────
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.gridspec import GridSpec
    except ImportError:
        print('[ZWD_AUDIT] matplotlib not available — skipping plot'); return

    q_label = f'  [ZWD-Q×{ZWD_Q_SCALE:.0f}]' if ZWD_Q_SCALE != 1.0 else ''
    fig = plt.figure(figsize=(14, 12))
    gs  = GridSpec(3, 2, figure=fig, hspace=0.45, wspace=0.35)
    fig.suptitle(f'PPP-AR v91 — ZWD↔Up Coupling Audit{q_label}  (GPS+Galileo FWD)',
                 fontsize=12, fontweight='bold')

    # Hump shading helper
    _hump_spans = [(5., 8.), (17., 20.)]
    def _shade(ax_):
        for h0_, h1_ in _hump_spans:
            ax_.axvspan(h0_, h1_, color='gold', alpha=0.18, zorder=0)

    # (a) Up residual + ZWD overlay (dual y-axis)
    ax_a  = fig.add_subplot(gs[0, :])   # full-width top panel
    ax_a2 = ax_a.twinx()
    ax_a.plot(hrs, up_mm, color='#4363d8', linewidth=0.8, alpha=0.9, label='Up error (mm)')
    ax_a2.plot(hrs, zwd_mm, color='#e6194b', linewidth=0.8, alpha=0.9, label='ZWD (mm)')
    ax_a.axhline(0, color='black', linewidth=0.4)
    _shade(ax_a)
    ax_a.set_xlabel('Time (h)'); ax_a.set_ylabel('Up Error (mm)', color='#4363d8')
    ax_a2.set_ylabel('ZWD (mm)', color='#e6194b')
    ax_a.tick_params(axis='y', labelcolor='#4363d8')
    ax_a2.tick_params(axis='y', labelcolor='#e6194b')
    lines_a  = ax_a.get_lines()  + ax_a2.get_lines()
    labels_a = [l.get_label() for l in lines_a]
    ax_a.legend(lines_a, labels_a, fontsize=8, loc='upper right')
    ax_a.set_title('(a) Up residual vs ZWD  [gold = hump windows]')
    ax_a.grid(True, alpha=0.25)

    # (b) ZWD first derivative
    ax_b = fig.add_subplot(gs[1, 0])
    ax_b.plot(hrs, dzwd_dt, color='#f58231', linewidth=0.7, alpha=0.9)
    ax_b.axhline(0, color='black', linewidth=0.4)
    _shade(ax_b)
    ax_b.set_xlabel('Time (h)'); ax_b.set_ylabel('dZWD/dt  (mm / epoch)')
    ax_b.set_title('(b) ZWD first derivative')
    ax_b.grid(True, alpha=0.25)

    # (c) Normalised overlay — shape comparison
    ax_c = fig.add_subplot(gs[1, 1])
    _u_s = up_mm.std()  or 1.;   _z_s = zwd_mm.std() or 1.
    ax_c.plot(hrs, (up_mm  - up_mm.mean())  / _u_s, color='#4363d8',
              linewidth=0.7, alpha=0.9, label='Up (norm)')
    ax_c.plot(hrs, (zwd_mm - zwd_mm.mean()) / _z_s, color='#e6194b',
              linewidth=0.7, alpha=0.9, label='ZWD (norm)')
    ax_c.axhline(0, color='black', linewidth=0.4)
    _shade(ax_c)
    ax_c.set_xlabel('Time (h)'); ax_c.set_ylabel('Normalised (σ=1)')
    ax_c.set_title('(c) Normalised Up vs ZWD  (shape comparison)')
    ax_c.legend(fontsize=8); ax_c.grid(True, alpha=0.25)

    # (d) Lagged cross-correlation
    ax_d = fig.add_subplot(gs[2, 0])
    lag_min_arr = lags * 30. / 60.   # epochs → minutes (30 s cadence)
    ax_d.plot(lag_min_arr, xcorr_vals, color='#3cb44b', linewidth=0.9)
    ax_d.axhline(0, color='black', linewidth=0.4)
    ax_d.axvline(0, color='gray', linewidth=0.6, linestyle='--')
    ax_d.axvline(float(peak_lag * 30. / 60.), color='red', linewidth=0.8,
                 linestyle=':', label=f'peak={peak_lag} ep ({lag_min:+.1f} min)')
    ax_d.set_xlabel('Lag (minutes)  [+→ ZWD leads Up]')
    ax_d.set_ylabel('Normalised cross-correlation')
    ax_d.set_title('(d) Lagged cross-correlation  Up vs ZWD\n'
                   '(post-convergence t≥2 h)')
    ax_d.legend(fontsize=8); ax_d.grid(True, alpha=0.25)

    # (e) Correlation bar chart (windows)
    ax_e = fig.add_subplot(gs[2, 1])
    wnames  = [r[0] for r in corr_table]
    c_zwd_v = [r[1] for r in corr_table]
    c_dzd_v = [r[2] for r in corr_table]
    x_pos   = np.arange(len(wnames))
    bw      = 0.35
    ax_e.bar(x_pos - bw/2, c_zwd_v,  bw, color='#e6194b', alpha=0.8, label='corr(Up, ZWD)')
    ax_e.bar(x_pos + bw/2, c_dzd_v,  bw, color='#f58231', alpha=0.8, label='corr(Up, dZWD/dt)')
    ax_e.axhline(0, color='black', linewidth=0.5)
    ax_e.set_xticks(x_pos); ax_e.set_xticklabels(wnames, fontsize=8)
    ax_e.set_ylabel('Pearson r'); ax_e.set_ylim(-1., 1.)
    ax_e.set_title('(e) Correlation summary by window')
    ax_e.legend(fontsize=7); ax_e.grid(True, alpha=0.25, axis='y')

    # Save
    png_path = os.path.join(outdir, 'ppp_zwd_audit.png')
    try:
        fig.savefig(png_path, dpi=150, bbox_inches='tight')
        print(f'[ZWD_AUDIT] Plot saved: {png_path}')
    except Exception as e:
        print(f'[ZWD_AUDIT] Plot save failed: {e}')
    plt.close(fig)


def _run_l_zwd_ablation(epochs, common, nom, REF, outdir=None):
    """
    Run L — ZWD process-noise single-factor ablation.

    Runs three GPS+Galileo FWD passes with ZWD_Q_SCALE = 1.0, 3.0, 5.0.
    All other filter parameters (ambiguities, orbit/clock, mapping, weights)
    are UNCHANGED.  Diagnostic only.

    For each pass reports:
        hump amplitude 5–8 h   (peak-to-peak Up mm in window)
        hump amplitude 17–20 h (peak-to-peak Up mm in window)
        Up RMS (post-convergence t ≥ 2 h)
        3D RMS (post-convergence t ≥ 2 h)
        corr(Up, ZWD) full arc (t ≥ 2 h)
        lag correlation peak   (lag in minutes; + = ZWD leads Up)

    Writes run_l_zwd_ablation.csv to outdir.
    """
    import csv as _csv_mod
    global ZWD_Q_SCALE

    if outdir is None:
        outdir = os.path.dirname(os.path.abspath(__file__))

    _SCALES   = [1.0, 3.0, 5.0]
    _LABELS   = ['A (×1 baseline)', 'B (×3)', 'C (×5)']
    _HUMP_W   = [('5–8 h',   5.0,  8.0),
                 ('17–20 h', 17.0, 20.0)]
    _CONV_SOD = 7200.0   # t ≥ 2 h for post-convergence stats

    # ── helpers ──────────────────────────────────────────────────────────────
    lr, lo, _ = _lla(REF)
    Re        = _enu(lr, lo)

    def _extract(fwd):
        """Return (sods, hrs, up_mm, d3_mm, zwd_mm) arrays from a fwd dict."""
        items   = sorted(fwd.items())
        sods    = np.array([s for s, _ in items])
        dx_all  = np.array([r['dx'] for _, r in items])
        zwd_arr = np.array([r.get('zwd', 0.) for _, r in items])
        enu_all = (Re @ dx_all.T).T * 1e3
        up_mm   = enu_all[:, 2]
        e_mm    = enu_all[:, 0]
        n_mm    = enu_all[:, 1]
        d3_mm   = np.sqrt(e_mm**2 + n_mm**2 + up_mm**2)
        return sods, sods / 3600., up_mm, d3_mm, zwd_arr * 1e3

    def _hump_amp(hrs, up_mm, h0, h1):
        """Peak-to-peak Up (mm) in window [h0, h1] h."""
        mask = (hrs >= h0) & (hrs <= h1)
        if mask.sum() < 3:
            return float('nan')
        w = up_mm[mask]
        return float(w.max() - w.min())

    def _rms(arr):
        return float(np.sqrt(np.mean(arr**2))) if len(arr) > 0 else float('nan')

    def _pearson(a, b):
        if len(a) < 5 or a.std() < 1e-10 or b.std() < 1e-10:
            return float('nan')
        return float(np.corrcoef(a, b)[0, 1])

    def _lag_corr(hrs, up_mm, zwd_mm):
        """
        Return (peak_lag_epochs, peak_lag_min, peak_r) from lagged xcorr.
        Positive lag → ZWD leads Up.  Uses post-convergence (t ≥ 2 h).
        """
        mask    = hrs >= 2.0
        u_fc    = up_mm[mask] - up_mm[mask].mean()
        z_fc    = zwd_mm[mask] - zwd_mm[mask].mean()
        u_std   = u_fc.std() or 1.
        z_std   = z_fc.std() or 1.
        max_lag = min(60, len(u_fc) // 4)
        lags    = np.arange(-max_lag, max_lag + 1)
        xcorr   = np.zeros(len(lags))
        for li, lag in enumerate(lags):
            if lag == 0:
                xcorr[li] = np.mean(u_fc * z_fc) / (u_std * z_std)
            elif lag > 0:
                xcorr[li] = np.mean(u_fc[lag:] * z_fc[:-lag]) / (u_std * z_std)
            else:
                k = -lag
                xcorr[li] = np.mean(u_fc[:-k] * z_fc[k:]) / (u_std * z_std)
        peak_li   = int(np.argmax(np.abs(xcorr)))
        peak_lag  = int(lags[peak_li])
        peak_r    = float(xcorr[peak_li])
        lag_min   = peak_lag * 30. / 60.   # 30-s epochs
        return peak_lag, lag_min, peak_r

    # ── main loop ─────────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("[RUN_L] ZWD process-noise single-factor ablation")
    print(f"[RUN_L] Passes: {_LABELS}")
    print(f"[RUN_L] Only Q[4,4] (ZWD) changes — all else frozen")
    print('='*72)

    rows  = []   # for CSV
    table = []   # for console

    for scale, label in zip(_SCALES, _LABELS):
        ZWD_Q_SCALE = scale
        print(f"\n[RUN_L] Pass {label}  (ZWD_Q_SCALE={scale:.1f}×)")

        _rts_store._data = []
        fwd, ex, ec, ez, wl_f, fwd_amb, fwd_snap = _ppp_pass(
            epochs, nom=nom.copy(), iclk=0., izwd=0.20,
            direction=1, label='FWD', constellation='GE', **common)

        print(f"[RUN_L]   {len(fwd)} epochs  end_ZWD={ez:.4f} m")

        sods, hrs, up_mm, d3_mm, zwd_mm = _extract(fwd)

        # Hump amplitudes
        h_amps = {}
        for wname, h0, h1 in _HUMP_W:
            h_amps[wname] = _hump_amp(hrs, up_mm, h0, h1)

        # Post-convergence RMS
        conv_mask = sods >= _CONV_SOD
        up_rms  = _rms(up_mm[conv_mask])
        d3_rms  = _rms(d3_mm[conv_mask])

        # Correlation (post-convergence full arc)
        corr_mask = hrs >= 2.0
        corr_zwd  = _pearson(up_mm[corr_mask], zwd_mm[corr_mask])

        # Lag correlation
        peak_lag, lag_min, peak_r = _lag_corr(hrs, up_mm, zwd_mm)
        lag_dir = 'ZWD→Up' if peak_lag > 0 else ('Up→ZWD' if peak_lag < 0 else 'sync')

        row = dict(
            pass_label   = label,
            scale        = scale,
            hump_5_8h    = h_amps.get('5–8 h',   float('nan')),
            hump_17_20h  = h_amps.get('17–20 h', float('nan')),
            up_rms_mm    = up_rms,
            d3_rms_mm    = d3_rms,
            corr_up_zwd  = corr_zwd,
            lag_epochs   = peak_lag,
            lag_min      = lag_min,
            lag_r        = peak_r,
            lag_dir      = lag_dir,
        )
        rows.append(row)
        table.append(row)

        print(f"[RUN_L]   Hump 5–8h={h_amps['5–8 h']:.1f} mm  "
              f"17–20h={h_amps['17–20 h']:.1f} mm")
        print(f"[RUN_L]   Up RMS={up_rms:.1f} mm  3D RMS={d3_rms:.1f} mm  "
              f"corr(Up,ZWD)={corr_zwd:+.3f}")
        print(f"[RUN_L]   Lag xcorr: peak={peak_lag} ep ({lag_min:+.1f} min)  "
              f"r={peak_r:+.3f}  dir={lag_dir}")

    # Restore baseline so rest of script is unaffected
    ZWD_Q_SCALE = 1.0

    # ── Console comparison table ──────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("[RUN_L] Summary table")
    print(f"{'='*72}")
    hdr = (f"{'Pass':<20}  {'Q scale':>7}  "
           f"{'Hump5-8h':>9}  {'Hump17-20h':>11}  "
           f"{'UpRMS':>7}  {'3DRMS':>7}  "
           f"{'corr':>7}  {'lag(min)':>9}  lag_r")
    print(hdr)
    print('-' * len(hdr))
    def _fmt_mm(v, width=7):
        return f'{v:{width}.1f}' if not math.isnan(v) else f'{"nan":>{width}s}'
    for r in table:
        print(f"{r['pass_label']:<20}  {r['scale']:>7.1f}x  "
              f"{_fmt_mm(r['hump_5_8h'], 9)}  "
              f"{_fmt_mm(r['hump_17_20h'], 11)}  "
              f"{_fmt_mm(r['up_rms_mm'], 7)}  "
              f"{_fmt_mm(r['d3_rms_mm'], 7)}  "
              f"{r['corr_up_zwd']:>+7.3f}  "
              f"{r['lag_min']:>+9.1f}  "
              f"{r['lag_r']:>+.3f}")

    # Decision guidance
    h5_base  = table[0]['hump_5_8h']
    h5_hi    = table[-1]['hump_5_8h']
    if not (math.isnan(h5_base) or math.isnan(h5_hi) or h5_base < 1.):
        change_pct = 100. * (h5_hi - h5_base) / h5_base
        print(f"\n[RUN_L] DECISION GUIDANCE (5–8 h hump):")
        print(f"[RUN_L]   Baseline (×1) = {h5_base:.1f} mm  →  ×5 = {h5_hi:.1f} mm  "
              f"(Δ = {change_pct:+.1f}%)")
        if abs(change_pct) >= 15.:
            print("[RUN_L]   >> Hump responds to q_zwd — under-modelled ZWD dynamics CONFIRMED")
        else:
            print("[RUN_L]   >> Hump does NOT respond to q_zwd — ZWD not primary cause")

    # ── CSV output ────────────────────────────────────────────────────────────
    csv_path = os.path.join(outdir, 'run_l_zwd_ablation.csv')
    try:
        with open(csv_path, 'w', newline='') as cf:
            cw = _csv_mod.writer(cf)
            cw.writerow(['pass_label', 'scale', 'hump_5_8h_mm', 'hump_17_20h_mm',
                         'up_rms_mm', 'd3_rms_mm', 'corr_up_zwd',
                         'lag_epochs', 'lag_min', 'lag_r', 'lag_dir'])
            for r in rows:
                cw.writerow([
                    r['pass_label'], f"{r['scale']:.1f}",
                    f"{r['hump_5_8h']:.2f}", f"{r['hump_17_20h']:.2f}",
                    f"{r['up_rms_mm']:.2f}", f"{r['d3_rms_mm']:.2f}",
                    f"{r['corr_up_zwd']:+.4f}",
                    r['lag_epochs'], f"{r['lag_min']:+.1f}",
                    f"{r['lag_r']:+.4f}", r['lag_dir'],
                ])
        print(f"\n[RUN_L] CSV written: {csv_path}")
    except Exception as e:
        print(f"[RUN_L] CSV write failed: {e}")

    print('='*72)


def _fit_12h_harmonic(m, conv_sod=_HARMONIC_CONV_SOD):
    """
    Fit  u_corr(t) = A·sin(2π·t/T) + B·cos(2π·t/T),  T = 12 h = 43200 s
    to the Up error time-series using only post-convergence epochs (t ≥ conv_sod).
    Ordinary least squares; no filter state is modified.

    Parameters
    ----------
    m        : metrics dict from _compute_metrics (must contain sods/e_mm/n_mm/u_mm)
    conv_sod : earliest SOD used in the fit (default 7200 s = 2 h)

    Returns  a dict with:
        A, B            – fitted sine / cosine amplitudes (mm)
        amplitude       – sqrt(A²+B²) (mm)
        u_corr_mm       – correction evaluated at ALL epochs (mm)
        u_base_mm       – original Up error at ALL epochs (mm)
        u_fixed_mm      – Up – u_corr at ALL epochs (mm)
        d3_fixed_mm     – 3D error recomputed with corrected Up (mm)
        sods            – epoch SOD array
        post_mask       – boolean mask: epochs used in the fit
        n_fit           – number of epochs used in the fit
    Returns None if fewer than 30 post-convergence epochs are available.
    """
    T        = _HARMONIC_PERIOD
    sods     = m['sods']
    u_mm     = m['u_mm']
    e_mm     = m['e_mm']
    n_mm     = m['n_mm']

    post = sods >= conv_sod
    n_fit = int(post.sum())
    if n_fit < 30:
        return None

    t_post = sods[post]
    u_post = u_mm[post]

    # Design matrix: columns = [sin(ωt), cos(ωt)]
    omega  = 2.0 * np.pi / T
    G      = np.column_stack([np.sin(omega * t_post),
                               np.cos(omega * t_post)])      # (n_fit, 2)
    coeff, *_ = np.linalg.lstsq(G, u_post, rcond=None)       # [A, B]
    A, B = float(coeff[0]), float(coeff[1])

    # Evaluate at ALL epochs
    S_all     = np.sin(omega * sods)
    C_all     = np.cos(omega * sods)
    u_corr_mm = A * S_all + B * C_all

    u_fixed_mm  = u_mm - u_corr_mm
    d3_fixed_mm = np.sqrt(e_mm**2 + n_mm**2 + u_fixed_mm**2)

    return dict(
        A=A, B=B,
        amplitude=math.hypot(A, B),
        u_corr_mm=u_corr_mm,
        u_base_mm=u_mm,
        u_fixed_mm=u_fixed_mm,
        d3_fixed_mm=d3_fixed_mm,
        sods=sods,
        post_mask=post,
        n_fit=n_fit,
    )


def _fit_dual_harmonic(m, conv_sod=_HARMONIC_CONV_SOD):
    """
    Run E: Fit dual-period vertical harmonic model (post-processing, no KF change).

        u_corr(t) = A1·sin(2πt/12h) + B1·cos(2πt/12h)
                  + A2·sin(2πt/24h) + B2·cos(2πt/24h)

    Both satellite-repeat (12 h) and diurnal (24 h) components are captured.
    Uses only post-convergence epochs (t ≥ conv_sod).  Ordinary least squares.

    Parameters
    ----------
    m        : metrics dict from _compute_metrics (must contain sods/e_mm/n_mm/u_mm)
    conv_sod : earliest SOD used in the fit (default 7200 s = 2 h)

    Returns dict with keys:
        A1, B1       – 12-h sin/cos amplitudes (mm)
        A2, B2       – 24-h sin/cos amplitudes (mm)
        amp12        – 12-h amplitude sqrt(A1²+B1²) (mm)
        amp24        – 24-h amplitude sqrt(A2²+B2²) (mm)
        amplitude    – total RMS amplitude (mm)
        u_corr_mm    – dual-harmonic correction at ALL epochs (mm)
        u_base_mm    – original Up error (mm)
        u_fixed_mm   – Up − u_corr (mm)
        d3_fixed_mm  – 3D error with corrected Up (mm)
        sods, post_mask, n_fit
    Returns None if fewer than 60 post-convergence epochs are available.
    """
    T12 = 43200.0   # 12 h
    T24 = 86400.0   # 24 h
    sods  = m['sods']
    u_mm  = m['u_mm']
    e_mm  = m['e_mm']
    n_mm  = m['n_mm']

    post  = sods >= conv_sod
    n_fit = int(post.sum())
    if n_fit < 60:
        return None

    t_post = sods[post]
    u_post = u_mm[post]

    o12 = 2.0 * np.pi / T12
    o24 = 2.0 * np.pi / T24

    # Design matrix: 4 columns
    G = np.column_stack([np.sin(o12 * t_post), np.cos(o12 * t_post),
                         np.sin(o24 * t_post), np.cos(o24 * t_post)])
    coeff, *_ = np.linalg.lstsq(G, u_post, rcond=None)
    A1, B1, A2, B2 = (float(c) for c in coeff)

    # Evaluate correction at ALL epochs
    S12 = np.sin(o12 * sods);  C12 = np.cos(o12 * sods)
    S24 = np.sin(o24 * sods);  C24 = np.cos(o24 * sods)
    u_corr    = A1*S12 + B1*C12 + A2*S24 + B2*C24
    u_fixed   = u_mm - u_corr
    d3_fixed  = np.sqrt(e_mm**2 + n_mm**2 + u_fixed**2)

    amp12 = math.hypot(A1, B1)
    amp24 = math.hypot(A2, B2)

    return dict(
        A1=A1, B1=B1, A2=A2, B2=B2,
        amp12=amp12, amp24=amp24,
        amplitude=math.hypot(amp12, amp24),
        u_corr_mm=u_corr,
        u_base_mm=u_mm,
        u_fixed_mm=u_fixed,
        d3_fixed_mm=d3_fixed,
        sods=sods,
        post_mask=post,
        n_fit=n_fit,
    )


def _common_mode_clock_leakage_diag(all_fwd, all_rts, REF, mode_humps):
    """
    Run CM — Common-mode receiver clock leakage diagnostic.

    Post-processing only.  No KF state is mutated.  No new pass required.

    For each auto-detected hump window (from mode_humps) and each epoch:
      1. Collect per-satellite post-fit phase residuals stored in
         phase_prefit_per_sat (L1 prefit phase, mm — best proxy available
         without a second KF pass; sign convention: obs - predicted).
      2. Compute robust common-mode per epoch:
             cm_gps(t)  = median of GPS phase residuals  (mm)
             cm_gal(t)  = median of Galileo phase residuals  (mm)
             cm_all(t)  = median of all phase residuals  (mm)
      3. Diagnostic position update (no filter mutation):
         The Up-direction sensitivity to a uniform phase shift δ (mm) for
         n satellites is approximately:
             ΔUp ≈ -mean(LOS·Up) × δ   [for phase, sign: obs-predicted]
         We remove cm_all from the residuals analytically:
             Up_demod(t) = Up_base(t) + mean_ldu(t) × cm_all(t)
         where mean_ldu is the epoch mean of (LOS·Up) from code_per_sat.
         This is a first-order approximation; it does not alter any state.
      4. Compute hump amplitude in baseline and de-common-moded Up.
         Reduction = (amp_base - amp_demod) / amp_base × 100 %.
      5. Verdict:
           reduction > 50% → clock leakage CONFIRMED (dominant cause)
           reduction 30–50% → clock leakage PROBABLE (significant contribution)
           reduction < 30%  → clock leakage REJECTED (not primary cause)
    """
    import math as _math
    import numpy as _np

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as _plt
        _HAS_PLT = True
    except ImportError:
        _HAS_PLT = False

    NAN = float('nan')
    CONV_SOD = 7200.   # 2 h

    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)

    def _xyz_to_up_mm(xyz, ref):
        return float(_np.dot(Re[2], (_np.array(xyz) - _np.array(ref)) * 1e3))

    def _robust_median(vals):
        finite = [v for v in vals if _math.isfinite(v)]
        if not finite:
            return NAN
        return float(_np.median(finite))

    def _pearson(a, b):
        fa = _np.isfinite(a) & _np.isfinite(b)
        if fa.sum() < 5:
            return NAN
        aa, bb = a[fa], b[fa]
        if _np.std(aa) < 1e-12 or _np.std(bb) < 1e-12:
            return NAN
        return float(_np.corrcoef(aa, bb)[0, 1])

    sep  = '=' * 72
    sep2 = '-' * 72

    print(f'\n{sep}')
    print('[CM_DIAG] Common-Mode Receiver Clock Leakage Diagnostic  (Run CM)')
    print('[CM_DIAG]  Post-processing only — NO KF state mutated.')
    print(f'[CM_DIAG]  common_mode(t) = robust median of post-fit phase residuals')
    print(f'[CM_DIAG]  de-modded Up   = Up_base + mean_LOS_Up x common_mode(t)')
    print(sep)

    # We run the diagnostic on each constellation mode separately so we can
    # see per-constellation behaviour and also on the GPS+Galileo combined mode.
    _all_verdicts = {}

    for mode in ['GPS+Galileo', 'GPS-only', 'Galileo-only']:
        fwd = all_fwd.get(mode, {})
        if not fwd:
            continue
        humps = mode_humps.get(mode, [])
        if not humps:
            # fall back to GPS+Galileo hump windows if mode-specific not available
            humps = mode_humps.get('GPS+Galileo', [])
        humps = [h for h in humps if isinstance(h, dict)]
        if not humps:
            print(f'\n[CM_DIAG]  {mode}: no hump windows detected — skipping.')
            continue

        print(f'\n{sep2}')
        print(f'[CM_DIAG]  Mode: {mode}  ({len(humps)} hump window(s))')
        print(sep2)

        all_sods = sorted(fwd.keys())
        sods_arr = _np.array(all_sods, dtype=float)
        hrs_arr  = sods_arr / 3600.

        # Full-arc arrays
        up_base_full   = _np.array([_xyz_to_up_mm(fwd[s]['xyz'], REF)
                                    for s in all_sods], dtype=float)
        clk_full_mm    = _np.array([fwd[s].get('clk', NAN) * 1e3
                                    for s in all_sods], dtype=float)

        # Build per-epoch common-mode from phase_prefit_per_sat
        # sid prefix: 'G' = GPS, 'E' = Galileo
        cm_all_full = _np.full(len(all_sods), NAN)
        cm_gps_full = _np.full(len(all_sods), NAN)
        cm_gal_full = _np.full(len(all_sods), NAN)
        ldu_mean_full = _np.full(len(all_sods), NAN)

        for ei, sod in enumerate(all_sods):
            r = fwd[sod]
            ppps = r.get('phase_prefit_per_sat', {})  # sid->(L1m_mm, L2m_mm, el, az)
            cps  = r.get('code_per_sat', {})           # sid->(res_mm, ldu, el, az)

            # Phase residuals: L1 prefit stored in metres, convert to mm
            ph_all, ph_gps, ph_gal = [], [], []
            for sid, vals in ppps.items():
                if len(vals) >= 1 and _math.isfinite(vals[0]):
                    ph_mm_val = float(vals[0]) * 1e3   # metres → mm
                    ph_all.append(ph_mm_val)
                    if sid.startswith('G'):
                        ph_gps.append(ph_mm_val)
                    elif sid.startswith('E'):
                        ph_gal.append(ph_mm_val)

            cm_all_full[ei] = _robust_median(ph_all)
            cm_gps_full[ei] = _robust_median(ph_gps)
            cm_gal_full[ei] = _robust_median(ph_gal)

            # Mean LOS·Up from code_per_sat (for de-modding)
            ldus = [ldu for (_, ldu, _, _) in cps.values()
                    if _math.isfinite(ldu)]
            ldu_mean_full[ei] = float(_np.mean(ldus)) if ldus else NAN

        # De-modded Up: remove common-mode × mean_LOS_Up
        # Phase residual sign: obs - predicted.  A positive common-mode means
        # all satellites see a positive phase offset, which the filter absorbs
        # by adjusting the clock (and slightly the position).  The Up leakage
        # component is: ΔUp_leaked = mean_LOS_Up × cm_all (m; convert to mm already done)
        # We REMOVE this leaked component from the Up error:
        up_demod_full = up_base_full - ldu_mean_full * cm_all_full

        # Per-window analysis
        mode_verdict_list = []
        for hi, hd in enumerate(humps):
            sh = hd.get('start_h', hd.get('start_sod', 0) / 3600.)
            eh = hd.get('end_h',   hd.get('end_sod',   0) / 3600.)
            pk = hd.get('peak_h',  hd.get('peak_sod',  0) / 3600.)
            lo_s = sh * 3600.
            hi_s = eh * 3600.

            hmask = ((sods_arr >= max(lo_s, CONV_SOD)) & (sods_arr <= hi_s))
            if hmask.sum() < 5:
                print(f'  Hump {hi+1} [{sh:.2f}h–{eh:.2f}h]: too few epochs — skip')
                continue

            # Baseline 1h pre-window for amplitude reference
            bl_lo = max(CONV_SOD, lo_s - 3600.)
            bl_hi = lo_s
            bmask = (sods_arr >= bl_lo) & (sods_arr < bl_hi)
            bl_mean = float(_np.nanmean(up_base_full[bmask])) if bmask.sum() > 0 else NAN

            up_h     = up_base_full[hmask]
            up_dm_h  = up_demod_full[hmask]
            cm_h     = cm_all_full[hmask]
            cm_gps_h = cm_gps_full[hmask]
            cm_gal_h = cm_gal_full[hmask]
            clk_h    = clk_full_mm[hmask]

            amp_base  = float(_np.nanmean(up_h))   - (bl_mean if _math.isfinite(bl_mean) else 0.)
            amp_demod = float(_np.nanmean(up_dm_h)) - (bl_mean if _math.isfinite(bl_mean) else 0.)

            if abs(amp_base) > 0.1:
                reduction_pct = (amp_base - amp_demod) / amp_base * 100.
            else:
                reduction_pct = NAN

            rms_base  = float(_np.sqrt(_np.nanmean(up_h**2)))
            rms_demod = float(_np.sqrt(_np.nanmean(up_dm_h**2)))
            cm_rms    = float(_np.nanstd(cm_h[_np.isfinite(cm_h)])) if _np.isfinite(cm_h).sum() > 0 else NAN
            r_up_cm   = _pearson(up_h, cm_h)
            r_clk_cm  = _pearson(clk_h, cm_h)

            # Verdict per window
            if _math.isfinite(reduction_pct):
                if reduction_pct > 50.:
                    verdict = 'CLOCK LEAKAGE CONFIRMED (dominant)'
                elif reduction_pct > 30.:
                    verdict = 'CLOCK LEAKAGE PROBABLE (significant)'
                else:
                    verdict = 'CLOCK LEAKAGE REJECTED (not primary cause)'
            else:
                verdict = 'INCONCLUSIVE (near-zero baseline amplitude)'

            print(f'\n  ── Hump {hi+1}  [{sh:.2f}h – {eh:.2f}h]  peak={pk:.2f}h ──')
            print(f'     epochs in window : {hmask.sum()}')
            print(f'     Up amplitude baseline    : {amp_base:+.1f} mm')
            print(f'     Up amplitude de-modded   : {amp_demod:+.1f} mm')
            print(f'     Hump reduction            : {reduction_pct:+.1f} %'
                  if _math.isfinite(reduction_pct) else '     Hump reduction            : n/a')
            print(f'     Up RMS  baseline          : {rms_base:.1f} mm')
            print(f'     Up RMS  de-modded         : {rms_demod:.1f} mm')
            print(f'     CM phase RMS (std)        : {cm_rms:.2f} mm')
            print(f'     corr(Up, CM_phase)        : {r_up_cm:+.3f}')
            print(f'     corr(Clk_KF, CM_phase)    : {r_clk_cm:+.3f}')
            print(f'     GPS  CM median (mean±std) : '
                  f'{float(_np.nanmean(cm_gps_h)):.2f} ± '
                  f'{float(_np.nanstd(cm_gps_h)):.2f} mm'
                  if _np.isfinite(cm_gps_h).sum() > 0 else '     GPS  CM : n/a')
            print(f'     GAL  CM median (mean±std) : '
                  f'{float(_np.nanmean(cm_gal_h)):.2f} ± '
                  f'{float(_np.nanstd(cm_gal_h)):.2f} mm'
                  if _np.isfinite(cm_gal_h).sum() > 0 else '     GAL  CM : n/a')
            print(f'  => VERDICT: {verdict}')

            mode_verdict_list.append((hi + 1, sh, eh, reduction_pct, verdict))

        _all_verdicts[mode] = mode_verdict_list

        # ── Diagnostic plot (per mode) ───────────────────────────────────────
        if _HAS_PLT:
            _outdir = os.path.dirname(os.path.abspath(__file__))
            fig, axes = _plt.subplots(4, 1, figsize=(14, 14), sharex=False)
            fig.suptitle(
                f'PPP-AR v91 — Common-Mode Clock Leakage Diagnostic  [{mode}]\n'
                f'(post-processing only — no KF state mutated)',
                fontsize=11, fontweight='bold')

            post_mask = hrs_arr >= 2.0
            sh_full   = hrs_arr[post_mask]

            # ── Panel 0: KF clock state vs common-mode phase ──────────────────
            ax0 = axes[0]
            cm_plot = cm_all_full[post_mask]
            ck_plot = clk_full_mm[post_mask]
            ax0.plot(sh_full, cm_plot, color='#e6194b', linewidth=0.8, alpha=0.8,
                     label='CM phase residual (robust median, mm)')
            ax0_r = ax0.twinx()
            ax0_r.plot(sh_full, ck_plot, color='#4363d8', linewidth=0.8, alpha=0.7,
                       label='KF clock state (mm)')
            ax0.set_ylabel('Common-mode phase (mm)', color='#e6194b')
            ax0_r.set_ylabel('KF clock state (mm)',   color='#4363d8')
            ax0.set_title('(a) KF receiver clock vs common-mode phase residual')
            ax0.grid(True, alpha=0.3)
            lines0, labs0 = ax0.get_legend_handles_labels()
            lines0r, labs0r = ax0_r.get_legend_handles_labels()
            ax0.legend(lines0 + lines0r, labs0 + labs0r, fontsize=8)
            for hd in [h for h in mode_humps.get(mode, []) if isinstance(h, dict)]:
                ax0.axvspan(hd.get('start_h', 0), hd.get('end_h', 0),
                            alpha=0.12, color='orange')

            # ── Panel 1: baseline vs de-modded Up (full arc) ──────────────────
            ax1 = axes[1]
            ax1.plot(sh_full, up_base_full[post_mask],
                     color='#4363d8', linewidth=0.8, alpha=0.85,
                     label=f'Up baseline (RMS={float(_np.sqrt(_np.nanmean(up_base_full[post_mask]**2))):.1f}mm)')
            ax1.plot(sh_full, up_demod_full[post_mask],
                     color='#f58231', linewidth=0.9, alpha=0.90,
                     label=f'Up de-common-moded (RMS={float(_np.sqrt(_np.nanmean(up_demod_full[post_mask]**2))):.1f}mm)')
            ax1.axhline(0, color='black', linewidth=0.5)
            ax1.set_ylabel('Up Error (mm)')
            ax1.set_title('(b) Baseline vs de-common-moded Up error — full arc')
            ax1.set_ylim(-350, 350)
            ax1.legend(fontsize=8)
            ax1.grid(True, alpha=0.3)
            for hd in [h for h in mode_humps.get(mode, []) if isinstance(h, dict)]:
                ax1.axvspan(hd.get('start_h', 0), hd.get('end_h', 0),
                            alpha=0.12, color='orange', label='hump window')

            # ── Panel 2: GPS vs Galileo common-mode ───────────────────────────
            ax2 = axes[2]
            ax2.plot(sh_full, cm_gps_full[post_mask],
                     color='#3cb44b', linewidth=0.8, alpha=0.85, label='GPS CM phase')
            ax2.plot(sh_full, cm_gal_full[post_mask],
                     color='#911eb4', linewidth=0.8, alpha=0.85, label='Galileo CM phase')
            ax2.plot(sh_full, cm_all_full[post_mask],
                     color='#e6194b', linewidth=0.9, alpha=0.70,
                     linestyle='--', label='Combined CM phase')
            ax2.axhline(0, color='black', linewidth=0.5)
            ax2.set_ylabel('Common-mode phase (mm)')
            ax2.set_title('(c) Per-constellation common-mode phase residual')
            ax2.legend(fontsize=8)
            ax2.grid(True, alpha=0.3)
            for hd in [h for h in mode_humps.get(mode, []) if isinstance(h, dict)]:
                ax2.axvspan(hd.get('start_h', 0), hd.get('end_h', 0),
                            alpha=0.12, color='orange')

            # ── Panel 3: hump windows zoomed ──────────────────────────────────
            ax3 = axes[3]
            humps_plot = [h for h in mode_humps.get(mode, []) if isinstance(h, dict)]
            if humps_plot:
                # Plot the first detected hump window
                hd0   = humps_plot[0]
                sh0   = hd0.get('start_h', 0)
                eh0   = hd0.get('end_h', 24)
                # expand ±1 h
                xlim_lo = max(CONV_SOD / 3600., sh0 - 1.0)
                xlim_hi = min(24., eh0 + 1.0)
                zoom_mask = (hrs_arr >= xlim_lo) & (hrs_arr <= xlim_hi)
                if zoom_mask.sum() > 5:
                    ax3.plot(hrs_arr[zoom_mask], up_base_full[zoom_mask],
                             color='#4363d8', linewidth=1.0, label='Up baseline')
                    ax3.plot(hrs_arr[zoom_mask], up_demod_full[zoom_mask],
                             color='#f58231', linewidth=1.1, label='Up de-modded')
                    ax3.axvspan(sh0, eh0, alpha=0.15, color='orange', label='hump window')
                    if len(humps_plot) > 1:
                        hd1 = humps_plot[1]
                        ax3.axvspan(hd1.get('start_h', 0), hd1.get('end_h', 24),
                                    alpha=0.15, color='green', label='hump 2')
                    ax3.axhline(0, color='black', linewidth=0.5)
                    ax3.set_xlim(xlim_lo, xlim_hi)
            ax3.set_xlabel('Time (h)')
            ax3.set_ylabel('Up Error (mm)')
            ax3.set_title('(d) Hump window zoom — baseline vs de-common-moded')
            ax3.legend(fontsize=8)
            ax3.grid(True, alpha=0.3)

            _plt.tight_layout()
            _safe_mode = mode.replace('+', '_').replace(' ', '_')
            _plot_path = os.path.join(_outdir,
                                      f'ppp_cm_clock_diag_{_safe_mode}.png')
            try:
                fig.savefig(_plot_path, dpi=150, bbox_inches='tight')
                print(f'\n[CM_DIAG]  Plot saved: {_plot_path}')
            except Exception as _pe:
                print(f'\n[CM_DIAG]  Plot save failed: {_pe}')
            _plt.close(fig)

    # ── Consolidated verdict ─────────────────────────────────────────────────
    print(f'\n{sep}')
    print('[CM_DIAG] CONSOLIDATED VERDICT — Common-Mode Clock Leakage')
    print(sep)
    for mode, vlist in _all_verdicts.items():
        print(f'  {mode}:')
        if not vlist:
            print('    no hump windows analysed')
            continue
        for (hi, sh, eh, red, verd) in vlist:
            red_str = f'{red:+.1f}%' if _math.isfinite(red) else 'n/a'
            print(f'    Hump {hi} [{sh:.2f}h–{eh:.2f}h]: reduction={red_str}  => {verd}')

    # Overall decision from GPS+Galileo hump reductions
    gg_verdicts = _all_verdicts.get('GPS+Galileo', [])
    all_reds = [r for (_, _, _, r, _) in gg_verdicts if _math.isfinite(r)]
    if all_reds:
        mean_red = float(_np.mean(all_reds))
        if mean_red > 50.:
            overall = f'CLOCK LEAKAGE CONFIRMED as dominant cause ({mean_red:.0f}% mean reduction)'
        elif mean_red > 30.:
            overall = f'CLOCK LEAKAGE PROBABLE — significant contribution ({mean_red:.0f}% mean reduction)'
        else:
            overall = f'CLOCK LEAKAGE REJECTED — not primary cause ({mean_red:.0f}% mean reduction)'
    else:
        overall = 'INCONCLUSIVE — no valid hump windows in GPS+Galileo'
    print(f'\n[CM_DIAG] OVERALL: {overall}')
    print(sep)


def _clock_mechanism_separation_diagnostic(all_fwd, all_rts, REF,
                                            epochs, _common, APX):
    """
    v96-diag  Clock-Mechanism Separation Diagnostic

    Tests four hypotheses to isolate which clock mechanism causes the
    common-mode leakage already confirmed by Run CM.

    H1 — External precise product inconsistency   (post-processing only)
    H2 — Receiver clock process model too loose   (4 × _ppp_pass reruns)
    H3 — ISB / constellation clock coupling       (post-processing only)
    H4 — Clock update transients (weights/geometry) (post-processing only)

    All tests operate on _FIXED_HUMP_WINDOWS exclusively.
    ZWD, ambiguities, NL logic, orbit models: untouched.
    One mechanism changed at a time.
    """
    import math as _math
    import numpy as _np

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as _plt
        import matplotlib.gridspec as _gs
        _HAS_PLT = True
    except ImportError:
        _HAS_PLT = False

    NAN = float('nan')
    CONV_SOD = 7200.

    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)

    sep  = '=' * 72
    sep2 = '-' * 72

    print(f'\n{sep}')
    print('[CLK_MECH] Clock-Mechanism Separation Diagnostic  (v96-diag)')
    print('[CLK_MECH]  H1=product  H2=clk_process  H3=ISB  H4=observability')
    print(f'[CLK_MECH]  Using _FIXED_HUMP_WINDOWS — no re-detection.')
    print(sep)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _pearson(a, b):
        fa = _np.isfinite(a) & _np.isfinite(b)
        if fa.sum() < 5:
            return NAN
        aa, bb = a[fa], b[fa]
        if _np.std(aa) < 1e-12 or _np.std(bb) < 1e-12:
            return NAN
        return float(_np.corrcoef(aa, bb)[0, 1])

    def _up_mm_arr(fwd):
        """Return (sods, up_mm) arrays from a fwd dict."""
        items = sorted(fwd.items())
        ss = _np.array([s for s, _ in items])
        dx = _np.array([r['dx'] for _, r in items])
        up = (Re @ dx.T).T[:, 2] * 1e3
        return ss, up

    def _hump_rms(sods, u_mm, hw):
        """Up RMS inside a hump window dict {start_h, end_h}."""
        lo_s = hw['start_h'] * 3600.
        hi_s = hw['end_h']   * 3600.
        mask = (sods >= max(lo_s, CONV_SOD)) & (sods <= hi_s)
        sub  = u_mm[mask]
        sub  = sub[_np.isfinite(sub)]
        return float(_np.sqrt(_np.mean(sub**2))) if len(sub) > 0 else NAN

    def _hump_amp(sods, u_mm, hw):
        """Hump amplitude = mean(|Up|) in window minus quiet baseline."""
        lo_s = hw['start_h'] * 3600.
        hi_s = hw['end_h']   * 3600.
        # quiet baseline: post-convergence epochs NOT in any hump window
        all_starts = [w['start_h'] * 3600. for wlist in _FIXED_HUMP_WINDOWS.values()
                      for w in wlist]
        all_ends   = [w['end_h']   * 3600. for wlist in _FIXED_HUMP_WINDOWS.values()
                      for w in wlist]
        hump_mask  = _np.zeros(len(sods), dtype=bool)
        for s, e in zip(all_starts, all_ends):
            hump_mask |= (sods >= s) & (sods <= e)
        quiet_mask = (sods >= CONV_SOD) & ~hump_mask
        quiet_u    = u_mm[quiet_mask & _np.isfinite(u_mm)]
        quiet_bl   = float(_np.median(quiet_u)) if len(quiet_u) > 5 else 0.0
        mask       = (sods >= max(lo_s, CONV_SOD)) & (sods <= hi_s)
        sub        = u_mm[mask]
        sub        = sub[_np.isfinite(sub)]
        return float(_np.mean(_np.abs(sub - quiet_bl))) if len(sub) > 0 else NAN

    def _clk_up_cov(sods, u_mm, fwd, hw):
        """Covariance of clock state (mm) with Up error (mm) in window."""
        lo_s = hw['start_h'] * 3600.
        hi_s = hw['end_h']   * 3600.
        clk  = _np.array([fwd.get(s, {}).get('clk', NAN) * 1e3
                           for s in sods])
        mask = (sods >= max(lo_s, CONV_SOD)) & (sods <= hi_s) & \
               _np.isfinite(u_mm) & _np.isfinite(clk)
        if mask.sum() < 5:
            return NAN
        return float(_np.cov(clk[mask], u_mm[mask])[0, 1])

    def _reduction_pct(amp_base, amp_test):
        if not (_math.isfinite(amp_base) and _math.isfinite(amp_test)
                and amp_base > 0.1):
            return NAN
        return (amp_base - amp_test) / amp_base * 100.

    def _evidence_score(reductions, threshold_pass=20.):
        """0–4 score: count windows where reduction > threshold."""
        valid = [r for r in reductions if _math.isfinite(r)]
        if not valid:
            return 0, 'no data'
        passing = sum(1 for r in valid if r > threshold_pass)
        pct_mean = float(_np.mean(valid))
        if passing == len(valid) and pct_mean > 40.:
            return 4, f'STRONG ({pct_mean:.0f}% mean reduction, all windows pass)'
        elif passing >= len(valid) * 0.5 and pct_mean > 20.:
            return 3, f'MODERATE ({pct_mean:.0f}% mean, {passing}/{len(valid)} pass)'
        elif pct_mean > 5.:
            return 2, f'WEAK ({pct_mean:.0f}% mean, {passing}/{len(valid)} pass)'
        else:
            return 1, f'NEGLIGIBLE ({pct_mean:.0f}% mean)'

    # ── baseline arrays (GPS+Galileo) ─────────────────────────────────────
    primary_fwd = all_fwd.get('GPS+Galileo', {})
    if not primary_fwd:
        print('[CLK_MECH] ERROR: GPS+Galileo FWD results missing — aborting.')
        return {}

    bs_sods, bs_up = _up_mm_arr(primary_fwd)
    fwd_items = sorted(primary_fwd.items())

    # Baseline hump amplitudes
    _GE_humps = _FIXED_HUMP_WINDOWS.get('GPS+Galileo', [])
    _GPS_humps = _FIXED_HUMP_WINDOWS.get('GPS-only', [])
    _GAL_humps = _FIXED_HUMP_WINDOWS.get('Galileo-only', [])
    bs_amps = [_hump_amp(bs_sods, bs_up, hw) for hw in _GE_humps]
    bs_rms  = [_hump_rms(bs_sods, bs_up, hw) for hw in _GE_humps]

    scores = {}   # hypothesis → (score_int, score_str)

    # ==========================================================================
    #  H1 — External precise product inconsistency
    #  Post-processing: detect clock product boundary jumps, correlate with humps
    # ==========================================================================
    print(f'\n{sep2}')
    print('[CLK_MECH] H1 — Precise product boundary / discontinuity test')
    print(sep2)

    sods_arr = _np.array([s for s, _ in fwd_items])
    clk_arr  = _np.array([r.get('clk', NAN) for _, r in fwd_items]) * 1e3   # mm
    up_arr   = bs_up

    # Clock drift rate (mm/epoch)
    dt_arr   = _np.diff(sods_arr, prepend=sods_arr[0])
    dt_arr[dt_arr <= 0] = 30.
    dclk_dt  = _np.gradient(clk_arr) / (dt_arr / 30.)   # normalise to 30-s epoch

    # Phase common-mode per epoch (already in code_per_sat proxy)
    cm_code_arr = _np.array([
        float(_np.median([v[0] for v in r.get('code_per_sat', {}).values()
                          if _math.isfinite(v[0])]))
        if r.get('code_per_sat') else NAN
        for _, r in fwd_items
    ])

    # Jump detector: epoch-to-epoch Δ(clock) > 3× IQR of nominal drift
    fin_dclk = dclk_dt[_np.isfinite(dclk_dt)]
    if len(fin_dclk) > 20:
        q75, q25 = _np.percentile(fin_dclk, [75, 25])
        iqr_dclk = q75 - q25
        jump_thr = max(3. * iqr_dclk, 50.)   # ≥50 mm/ep to count
    else:
        jump_thr = 200.

    jump_mask = _np.abs(dclk_dt) > jump_thr
    jump_sods = sods_arr[jump_mask]

    # SP3 product boundaries: every 15 min = 900 s; SP3 file boundary at 0/43200 s
    SP3_BOUNDARY_SODS = [0., 43200., 86400.]
    INTERP_BOUNDARY   = 900.   # 15-min SP3 epoch boundaries

    def _near_boundary(sod, tol=120.):
        for b in SP3_BOUNDARY_SODS:
            if abs(sod - b) < tol:
                return True
        # 15-min grid
        if abs(sod % INTERP_BOUNDARY) < tol or abs(sod % INTERP_BOUNDARY - INTERP_BOUNDARY) < tol:
            return True
        return False

    jumps_at_boundary  = sum(1 for s in jump_sods if _near_boundary(s))
    jumps_total        = int(jump_mask.sum())
    boundary_frac      = jumps_at_boundary / max(jumps_total, 1)

    print(f'  Clock jump threshold : {jump_thr:.1f} mm/ep')
    print(f'  Total clock jumps    : {jumps_total}')
    print(f'  Jumps at SP3/interp boundary (±120s): {jumps_at_boundary}  '
          f'({boundary_frac*100:.0f}%)')

    # Correlation of |dclk_dt| with common-mode code residual and Up inside humps
    _H1_reductions = []
    for i, hw in enumerate(_GE_humps):
        lo_s = hw['start_h'] * 3600.
        hi_s = hw['end_h']   * 3600.
        mask = (sods_arr >= max(lo_s, CONV_SOD)) & (sods_arr <= hi_s) & \
               _np.isfinite(up_arr) & _np.isfinite(dclk_dt)
        if mask.sum() < 5:
            print(f'  Hump {i+1}: insufficient data')
            _H1_reductions.append(NAN)
            continue
        r_dclk_up = _pearson(_np.abs(dclk_dt[mask]), up_arr[mask])
        r_cm_up   = _pearson(cm_code_arr[mask], up_arr[mask])
        # Check for common-mode jumps inside window
        n_jumps_in_hump = int((jump_mask & (sods_arr >= lo_s) & (sods_arr <= hi_s)).sum())
        amp_jump_mm     = float(_np.nanmean(_np.abs(dclk_dt[mask])))
        print(f'  Hump {i+1} [{hw["start_h"]:.2f}–{hw["end_h"]:.2f}h]:')
        print(f'    r(|dclk/dt|, Up) = {r_dclk_up:+.3f}')
        print(f'    r(CM_code, Up)   = {r_cm_up:+.3f}')
        print(f'    jumps in window  = {n_jumps_in_hump}   mean|Δclk| = {amp_jump_mm:.1f} mm/ep')
        # Product boundary fraction in window
        n_ep_in_hump = int(mask.sum())
        n_bound_ep   = sum(1 for s in sods_arr[mask] if _near_boundary(s))
        print(f'    boundary epochs  = {n_bound_ep}/{n_ep_in_hump}  '
              f'({n_bound_ep/max(n_ep_in_hump,1)*100:.0f}%)')
        # H1 "reduction" proxy: what fraction of Up variance is explained by |dclk/dt|
        if _math.isfinite(r_dclk_up):
            explained_pct = r_dclk_up**2 * 100.
            _H1_reductions.append(explained_pct)
        else:
            _H1_reductions.append(0.)

    h1_score, h1_str = _evidence_score(_H1_reductions, threshold_pass=15.)
    # Extra bonus: if >40% jumps are at interpolation boundaries → product issue
    if boundary_frac > 0.4:
        h1_score = min(4, h1_score + 1)
        h1_str += ' [+boundary bonus]'
    print(f'  H1 evidence score    : {h1_score}/4 — {h1_str}')
    scores['H1'] = (h1_score, h1_str)

    # ==========================================================================
    #  H2 — Receiver clock process noise sweep
    #  4 × _ppp_pass: clk_q_scale ∈ {0.1, 0.3, 1.0, 3.0}
    # ==========================================================================
    print(f'\n{sep2}')
    print('[CLK_MECH] H2 — Receiver clock process-noise sweep')
    print('[CLK_MECH]    clk_q_scale ∈ {0.1, 0.3, 1.0, 3.0}')
    print('[CLK_MECH]    pos Q, ZWD Q, iono Q, ambiguities: UNCHANGED')
    print(sep2)

    CLK_Q_SCALES = [0.1, 0.3, 1.0, 3.0]
    _h2_results = {}   # scale → (sods, up_mm, fwd_dict)

    for _qs in CLK_Q_SCALES:
        _label = f'H2_CLK_Q_{_qs:.1f}'
        print(f'  Running {_label} ...')
        try:
            _rts_store._data = []
            _fwd_h2, *_ = _ppp_pass(
                epochs, nom=APX.copy(), iclk=0., izwd=0.20,
                direction=1, label=_label, constellation='GE',
                clk_q_scale=_qs, **_common)
            _ss, _uu = _up_mm_arr(_fwd_h2)
            _h2_results[_qs] = (_ss, _uu, _fwd_h2)
            _rms_all = float(_np.sqrt(_np.mean(_uu[_np.isfinite(_uu) &
                              (_ss >= CONV_SOD)]**2))) \
                       if (_ss >= CONV_SOD).any() else NAN
            print(f'    Up-RMS (post-conv)  = {_rms_all:.1f} mm')
        except Exception as _e2:
            print(f'    FAILED: {_e2}')
            _h2_results[_qs] = None

    print()
    print('  H2 hump-window summary:')
    print(f'  {"clk_q_scale":>12s}  {"Up-RMS(all)":>12s}  ' +
          '  '.join(f'Hump{i+1}_amp' for i in range(len(_GE_humps))) +
          '  clk-Up-cov')

    _h2_reductions = []
    _bs_up_rms_all = float(_np.sqrt(_np.mean(
        bs_up[_np.isfinite(bs_up) & (bs_sods >= CONV_SOD)]**2)))

    for _qs in CLK_Q_SCALES:
        res = _h2_results.get(_qs)
        if res is None:
            print(f'  {_qs:>12.1f}  {"FAILED":>12s}')
            continue
        _ss, _uu, _fd = res
        _rms_pc = float(_np.sqrt(_np.mean(
            _uu[_np.isfinite(_uu) & (_ss >= CONV_SOD)]**2))) \
            if (_ss >= CONV_SOD).any() else NAN
        _amps   = [_hump_amp(_ss, _uu, hw) for hw in _GE_humps]
        _covs   = [_clk_up_cov(_ss, _uu, _fd, hw) for hw in _GE_humps]
        _cov_str = ', '.join(f'{c:.0f}' if _math.isfinite(c) else 'n/a'
                              for c in _covs)
        _amp_str = '  '.join(f'{a:8.1f}mm' if _math.isfinite(a) else '    n/a  '
                              for a in _amps)
        print(f'  {_qs:>12.1f}  {_rms_pc:>11.1f}mm  {_amp_str}  {_cov_str}')

    # Compute reductions: compare x0.1 vs baseline amplitude
    res_lo = _h2_results.get(0.1)
    if res_lo is not None:
        _ss_lo, _uu_lo, _ = res_lo
        for i, hw in enumerate(_GE_humps):
            amp_lo = _hump_amp(_ss_lo, _uu_lo, hw)
            red    = _reduction_pct(bs_amps[i], amp_lo)
            _h2_reductions.append(red)
            bs_rms_h = bs_rms[i]
            rms_lo   = _hump_rms(_ss_lo, _uu_lo, hw)
            print(f'  Hump {i+1}: amp_baseline={bs_amps[i]:.1f}mm  '
                  f'amp_x0.1={amp_lo:.1f}mm  reduction={red:.1f}%  '
                  f'RMS {bs_rms_h:.1f}→{rms_lo:.1f}mm')
    else:
        print('  H2: x0.1 run failed — cannot compute reductions')

    # Check monotonicity: does hump amplitude decrease as clk_q decreases?
    _amp_by_scale = []
    for _qs in CLK_Q_SCALES:
        res = _h2_results.get(_qs)
        if res:
            _amp_by_scale.append((_qs, _np.mean([_hump_amp(*res[:2], hw)
                                                  for hw in _GE_humps
                                                  if _GE_humps])))
    _amp_by_scale = [(qs, a) for qs, a in _amp_by_scale if _math.isfinite(a)]
    _monotone = False
    if len(_amp_by_scale) >= 3:
        amps_sorted = [a for _, a in sorted(_amp_by_scale)]
        _monotone = all(amps_sorted[i] >= amps_sorted[i+1] - 2.
                        for i in range(len(amps_sorted)-1))
        print(f'  Hump amplitude monotone decrease with tighter Q: {_monotone}')

    h2_score, h2_str = _evidence_score(_h2_reductions, threshold_pass=20.)
    if _monotone:
        h2_score = min(4, h2_score + 1)
        h2_str += ' [+monotone bonus]'
    print(f'  H2 evidence score    : {h2_score}/4 — {h2_str}')
    scores['H2'] = (h2_score, h2_str)

    # ==========================================================================
    #  H3 — ISB / constellation clock coupling leakage
    #  Post-processing: ISB proxy correction + per-constellation hump comparison
    # ==========================================================================
    print(f'\n{sep2}')
    print('[CLK_MECH] H3 — ISB / constellation clock coupling')
    print('[CLK_MECH]    Post-processing ISB correction + cross-constellation hump match')
    print(sep2)

    # Per-constellation: compare hump timing/amplitude between GPS-only, Galileo-only,
    # GPS+Galileo.  If multi-GNSS hump is significantly LARGER than single-constellation
    # humps → ISB coupling pumps extra error into combined solution.
    fwd_G = all_fwd.get('GPS-only',     {})
    fwd_E = all_fwd.get('Galileo-only', {})

    _H3_reductions = []

    # 1) Cross-constellation amplitude comparison
    for i, hw in enumerate(_GE_humps):
        amp_ge = bs_amps[i]
        amp_g  = _hump_amp(*_up_mm_arr(fwd_G), hw) if fwd_G else NAN
        amp_e  = _hump_amp(*_up_mm_arr(fwd_E), hw) if fwd_E else NAN
        print(f'  Hump {i+1} [{hw["start_h"]:.2f}–{hw["end_h"]:.2f}h]  amp: '
              f'GPS-only={amp_g:.1f}mm  GAL-only={amp_e:.1f}mm  '
              f'GPS+GAL={amp_ge:.1f}mm')
        # If combined is larger than max(single-const), extra excursion = ISB
        if _math.isfinite(amp_g) and _math.isfinite(amp_e) and _math.isfinite(amp_ge):
            max_single = max(amp_g, amp_e)
            isb_excess = amp_ge - max_single
            print(f'    ISB-induced excess   = {isb_excess:+.1f}mm  '
                  f'(combined − max_single)')
            # Reduction proxy: fraction of combined amp explained by ISB excess
            frac = isb_excess / max(amp_ge, 1.) * 100.
            _H3_reductions.append(frac)
        else:
            _H3_reductions.append(NAN)

    # 2) Post-processing ISB correction on GPS+Galileo
    # ISB proxy = GPS mean code residual − Galileo mean code residual
    # Correction: remove ISB×(mean_LOS_up_gal) from Up analytically
    isb_proxy = _np.array([r.get('isb_proxy_mm', NAN) for _, r in fwd_items])
    mean_ldu_gal = _np.array([
        float(_np.mean([v[1] for sid, v in r.get('code_per_sat', {}).items()
                        if sid.startswith('E') and _math.isfinite(v[1])]))
        if any(sid.startswith('E') for sid in r.get('code_per_sat', {}))
        else NAN
        for _, r in fwd_items
    ])

    # Smooth ISB with 10-epoch running median (simulates tighter ISB process noise)
    _ISB_SMOOTH = 10
    isb_smooth = _np.full_like(isb_proxy, NAN)
    for _ii in range(len(isb_proxy)):
        _sl = isb_proxy[max(0, _ii - _ISB_SMOOTH):_ii + _ISB_SMOOTH + 1]
        _fin = _sl[_np.isfinite(_sl)]
        if len(_fin) >= 3:
            isb_smooth[_ii] = float(_np.median(_fin))

    # Corrected Up: remove smoothed ISB contribution
    up_isb_corrected = bs_up.copy()
    for _ii in range(len(up_isb_corrected)):
        _isb = isb_smooth[_ii]
        _ldu = mean_ldu_gal[_ii]
        if _math.isfinite(_isb) and _math.isfinite(_ldu):
            # ISB leaks as fraction of mean_LOS_up_gal × ISB
            up_isb_corrected[_ii] -= _ldu * _isb * 0.5   # 0.5: empirical coupling factor

    print()
    print('  Post-processing ISB correction (smoothed ISB × LOS_up_gal):')
    for i, hw in enumerate(_GE_humps):
        amp_corr = _hump_amp(bs_sods, up_isb_corrected, hw)
        red      = _reduction_pct(bs_amps[i], amp_corr)
        print(f'  Hump {i+1}: amp_baseline={bs_amps[i]:.1f}mm  '
              f'amp_isb_corrected={amp_corr:.1f}mm  reduction={red:.1f}%')
        if _math.isfinite(red):
            _H3_reductions.append(red)

    # ISB proxy time-series statistics
    isb_valid = isb_proxy[(bs_sods >= CONV_SOD) & _np.isfinite(isb_proxy)]
    if len(isb_valid) > 5:
        print(f'  ISB proxy mean={_np.mean(isb_valid):.1f}mm  '
              f'std={_np.std(isb_valid):.1f}mm  '
              f'r(ISB,Up)={_pearson(isb_proxy, bs_up):+.3f}')

    h3_score, h3_str = _evidence_score(_H3_reductions, threshold_pass=15.)
    print(f'  H3 evidence score    : {h3_score}/4 — {h3_str}')
    scores['H3'] = (h3_score, h3_str)

    # ==========================================================================
    #  H4 — Clock observability / measurement weighting transients
    #  Post-processing: PDOP, elevation geometry, high-elevation dominance
    # ==========================================================================
    print(f'\n{sep2}')
    print('[CLK_MECH] H4 — Clock observability / weighting transients')
    print('[CLK_MECH]    PDOP correlation, high-elev dominance, weight-frozen Up proxy')
    print(sep2)

    # Extract per-epoch geometry proxy from stored results
    pdop_arr = _np.array([r.get('pdop', NAN) for _, r in fwd_items])
    n_sat_arr= _np.array([r.get('n',    0)   for _, r in fwd_items], dtype=float)

    # If PDOP not stored, recompute approximate clock dilution from code_per_sat
    if _np.all(~_np.isfinite(pdop_arr)):
        pdop_arr = _np.full(len(sods_arr), NAN)
        for _ii, (sod, r) in enumerate(fwd_items):
            cps = r.get('code_per_sat', {})
            if len(cps) < 4:
                continue
            # Simple clock-dilution proxy: std of LOS_up / mean_LOS_up
            _ldus = [v[1] for v in cps.values() if _math.isfinite(v[1])]
            if len(_ldus) >= 4:
                _mean_ldu = _np.mean(_ldus)
                if abs(_mean_ldu) > 0.05:
                    pdop_arr[_ii] = float(_np.std(_ldus)) / abs(_mean_ldu)

    # Correlation of PDOP with Up inside hump windows
    _H4_reductions = []
    for i, hw in enumerate(_GE_humps):
        lo_s = hw['start_h'] * 3600.
        hi_s = hw['end_h']   * 3600.
        mask = (sods_arr >= max(lo_s, CONV_SOD)) & (sods_arr <= hi_s) & \
               _np.isfinite(up_arr) & _np.isfinite(pdop_arr)
        r_pdop_up = _pearson(pdop_arr[mask], up_arr[mask]) if mask.sum() >= 5 else NAN
        # N_sat changes: epochs where N drops → weaker clock observability
        dn_sat = _np.diff(n_sat_arr, prepend=n_sat_arr[0])
        n_drops_in_hump = int((mask & (dn_sat < -1)).sum())
        # High-elevation dominance: compute fraction of common-mode from el>60°
        _el_hi_frac = []
        for sod in sods_arr[mask[:len(sods_arr)]]:
            r = primary_fwd.get(sod, {})
            cps = r.get('code_per_sat', {})
            if not cps:
                continue
            _hi   = [abs(v[0]) for v in cps.values() if _math.isfinite(v[0]) and v[2] >= 60.]
            _all  = [abs(v[0]) for v in cps.values() if _math.isfinite(v[0])]
            if _all:
                _el_hi_frac.append(sum(_hi) / max(sum(_all), 1.))
        mean_hi_frac = float(_np.mean(_el_hi_frac)) if _el_hi_frac else NAN
        print(f'  Hump {i+1} [{hw["start_h"]:.2f}–{hw["end_h"]:.2f}h]:')
        print(f'    r(PDOP, Up)      = {r_pdop_up:+.3f}  '
              f'{"⚠ geometry-driven" if _math.isfinite(r_pdop_up) and abs(r_pdop_up)>0.5 else "  OK"}')
        print(f'    N-sat drops      = {n_drops_in_hump}  in window')
        print(f'    hi-elev dom frac = {mean_hi_frac:.2f}  (el>60°, '
              f'{"⚠ HIGH" if _math.isfinite(mean_hi_frac) and mean_hi_frac > 0.6 else "  normal"})')
        # H4 proxy: |r(PDOP,Up)|² as explained variance %
        if _math.isfinite(r_pdop_up):
            _H4_reductions.append(r_pdop_up**2 * 100.)
        else:
            _H4_reductions.append(0.)

    h4_score, h4_str = _evidence_score(_H4_reductions, threshold_pass=15.)
    print(f'  H4 evidence score    : {h4_score}/4 — {h4_str}')
    scores['H4'] = (h4_score, h4_str)

    # ==========================================================================
    #  Final verdict + proposed fix
    # ==========================================================================
    print(f'\n{sep}')
    print('[CLK_MECH] FINAL VERDICT — Clock-Mechanism Separation')
    print(sep)

    _labels = {
        'H1': '(A) Precise product inconsistency',
        'H2': '(B) Receiver clock process model leakage',
        'H3': '(C) ISB constellation coupling leakage',
        'H4': '(D) Clock observability / weighting transients',
    }

    print(f'  {"Hypothesis":<45s}  {"Score":>5s}  Evidence')
    print(f'  {"-"*45}  {"-"*5}  {"-"*35}')
    ranked = sorted(scores.items(), key=lambda kv: -kv[0][1] if False else -kv[1][0])
    for hyp, (sc, ev) in ranked:
        print(f'  {_labels[hyp]:<45s}  {sc:>5d}/4  {ev}')

    winner     = ranked[0][0]
    winner_sc  = ranked[0][1][0]
    runner_sc  = ranked[1][1][0] if len(ranked) > 1 else 0
    clear_win  = (winner_sc - runner_sc) >= 2

    print()
    if clear_win:
        print(f'[CLK_MECH] ROOT CAUSE: {_labels[winner]}')
    else:
        print(f'[CLK_MECH] ROOT CAUSE: {_labels[winner]}  '
              f'(margin={winner_sc-runner_sc}/4 — not fully decisive)')

    # Proposed filter fix
    _FIXES = {
        'H1': (
            'PROPOSED FIX (H1 — Product inconsistency):\n'
            '  1. Switch to 5-min CLK product (reduce interpolation error).\n'
            '  2. Apply SP3 clock rate correction at each product boundary:\n'
            '       clk_corrected(t) = clk(t) + rate × (t − t_boundary)\n'
            '  3. In _gclk(): detect and remove epoch-to-epoch jumps > 1 m\n'
            '     by replacing with Lagrange-interpolated value.\n'
            '  4. Add SP3 product-quality flag per epoch; down-weight clock\n'
            '     observations within ±60 s of file boundary.'
        ),
        'H2': (
            'PROPOSED FIX (H2 — Clock process noise leakage):\n'
            '  In _ppp_pass, replace constant Q[3,3]:\n'
            '    OLD: Q[3,3] = 1e4 * dt * pos_clk_q_scale * clk_q_scale\n'
            '    NEW: Adaptive clock Q based on smoothed clock state variance:\n'
            '       _clk_var_smooth = 0.99*_clk_var_smooth + 0.01*P[3,3]\n'
            '       Q[3,3] = max(1e3, min(1e5, _clk_var_smooth)) * dt\n'
            '  Alternatively, set clk_q_scale = 0.1 permanently (tight clock\n'
            '  random walk) which collapses humps per H2 sweep results.\n'
            '  Do NOT change pos Q, ZWD Q, or iono Q.'
        ),
        'H3': (
            'PROPOSED FIX (H3 — ISB coupling leakage):\n'
            '  Add explicit ISB state for Galileo to _ppp_pass:\n'
            '    State vector: [..., clock_GPS, ISB_GAL, ZWD, per-sat...]\n'
            '    Q[isb,isb] = 1e2 * dt  (tight: ~10 mm/h random walk)\n'
            '    In _proc_gal: subtract x[isb] from Galileo observations.\n'
            '    In H matrix: add +1 column at isb index for Galileo rows.\n'
            '  Alternatively: run GPS-only and use GPS ambiguities to seed\n'
            '  Galileo pass with fixed ISB from quiet-window estimate.'
        ),
        'H4': (
            'PROPOSED FIX (H4 — Clock observability transients):\n'
            '  1. Freeze clock state update when PDOP > PDOP_threshold:\n'
            '       if pdop > 6.0: Q[3,3] *= 0.01  (near-freeze)\n'
            '  2. Apply elevation-based clock update gating:\n'
            '       if n_sats_hi_el < 2: skip clock update, keep prediction.\n'
            '  3. Add clock continuity constraint between epochs:\n'
            '       add pseudo-observation: clock(t) - clock(t-1) ≈ 0\n'
            '       with R = (50 mm)² when sat geometry changes rapidly.'
        ),
    }
    print()
    print(_FIXES.get(winner, 'No fix defined for this hypothesis.'))
    print(sep)

    # ==========================================================================
    #  Summary plot
    # ==========================================================================
    if _HAS_PLT and _h2_results:
        try:
            _fig, _axes = _plt.subplots(2, 2, figsize=(14, 9))
            _fig.suptitle('CLK_MECH Separation Diagnostic  (v96-diag)\n'
                          'H1=product  H2=clk_Q_sweep  H3=ISB  H4=PDOP',
                          fontsize=11)

            _ax_h2  = _axes[0, 0]
            _ax_isb = _axes[0, 1]
            _ax_sc  = _axes[1, 0]
            _ax_clk = _axes[1, 1]

            # H2 sweep: hump amplitude vs clk_q_scale
            _h2_x, _h2_y = [], []
            for _qs in CLK_Q_SCALES:
                res = _h2_results.get(_qs)
                if res:
                    _amps_hw = [_hump_amp(*res[:2], hw) for hw in _GE_humps]
                    _mean_a  = _np.nanmean(_amps_hw) if _amps_hw else NAN
                    if _math.isfinite(_mean_a):
                        _h2_x.append(_qs)
                        _h2_y.append(_mean_a)
            if _h2_x:
                _ax_h2.semilogx(_h2_x, _h2_y, 'o-', color='#1f77b4', linewidth=2)
                _ax_h2.axvline(1.0, color='gray', linestyle='--', linewidth=0.8,
                               label='baseline')
                _ax_h2.set_xlabel('clk_q_scale')
                _ax_h2.set_ylabel('Mean hump amplitude (mm)')
                _ax_h2.set_title('H2: Hump amp vs clock process noise')
                _ax_h2.legend(fontsize=8)
                _ax_h2.grid(True, alpha=0.3)

            # H3: ISB proxy time series
            _conv_mask = (bs_sods >= CONV_SOD) & _np.isfinite(isb_proxy)
            if _conv_mask.any():
                _ax_isb.plot(bs_sods[_conv_mask] / 3600.,
                             isb_proxy[_conv_mask], color='#d62728',
                             linewidth=0.6, alpha=0.7, label='ISB proxy (mm)')
                _ax_isb2 = _ax_isb.twinx()
                _ax_isb2.plot(bs_sods[_np.isfinite(bs_up)] / 3600.,
                              bs_up[_np.isfinite(bs_up)], color='#2ca02c',
                              linewidth=0.6, alpha=0.5, label='Up error (mm)')
                for hw in _GE_humps:
                    _ax_isb.axvspan(hw['start_h'], hw['end_h'],
                                    alpha=0.12, color='orange')
                _ax_isb.set_xlabel('Time (h)')
                _ax_isb.set_ylabel('ISB proxy (mm)', color='#d62728')
                _ax_isb2.set_ylabel('Up error (mm)', color='#2ca02c')
                _ax_isb.set_title('H3: ISB proxy vs Up error')
                _ax_isb.grid(True, alpha=0.3)

            # Evidence score bar chart
            _hyps  = ['H1', 'H2', 'H3', 'H4']
            _svals = [scores.get(h, (0,))[0] for h in _hyps]
            _cols  = ['#d62728' if h == winner else '#aec7e8' for h in _hyps]
            _ax_sc.bar(_hyps, _svals, color=_cols, edgecolor='k', linewidth=0.8)
            _ax_sc.set_ylim(0, 4.5)
            _ax_sc.set_ylabel('Evidence score (0–4)')
            _ax_sc.set_title('Hypothesis scores — winner highlighted')
            _ax_sc.axhline(2, color='gray', linestyle=':', linewidth=0.8,
                           label='moderate threshold')
            _ax_sc.grid(True, alpha=0.3, axis='y')
            _ax_sc.legend(fontsize=8)

            # H2 Up RMS vs scale
            _rms_x, _rms_y = [], []
            for _qs in CLK_Q_SCALES:
                res = _h2_results.get(_qs)
                if res:
                    _ss, _uu, _ = res
                    _rms_pc = float(_np.sqrt(_np.mean(
                        _uu[_np.isfinite(_uu) & (_ss >= CONV_SOD)]**2))) \
                        if (_ss >= CONV_SOD).any() else NAN
                    if _math.isfinite(_rms_pc):
                        _rms_x.append(_qs)
                        _rms_y.append(_rms_pc)
            if _rms_x:
                _ax_clk.semilogx(_rms_x, _rms_y, 's--', color='#ff7f0e',
                                 linewidth=2, label='Up RMS (mm)')
                _ax_clk.axvline(1.0, color='gray', linestyle='--', linewidth=0.8,
                                label='baseline')
                _ax_clk.set_xlabel('clk_q_scale')
                _ax_clk.set_ylabel('Up RMS post-conv (mm)')
                _ax_clk.set_title('H2: Up RMS vs clock process noise')
                _ax_clk.legend(fontsize=8)
                _ax_clk.grid(True, alpha=0.3)

            _plt.tight_layout()
            _plot_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                'ppp_clk_mech_sep.png')
            _fig.savefig(_plot_path, dpi=150, bbox_inches='tight')
            print(f'[CLK_MECH]  Plot saved: {_plot_path}')
            _plt.close(_fig)
        except Exception as _pe:
            print(f'[CLK_MECH]  Plot failed: {_pe}')

    return scores



    """
    Run G: Audit whether precise clock/orbit residuals correlate with hump windows.

    Uses per-epoch code_rms, phase_rms (already logged to results dict), and the
    reconstructed clock state (clk, metres) to compute:
      - Pearson corr(Up, code_rms)   in full arc and hump windows
      - Pearson corr(Up, phase_rms)  in full arc and hump windows
      - Clock drift rate dclk/dt and its correlation with Up
      - Up RMS fraction explained by code vs phase residuals

    No KF state is changed.  Purely post-processing.
    """
    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)

    fwd_list = sorted(fwd.items())
    if len(fwd_list) < 60:
        print("[G_AUDIT] Too few epochs — skipping clock/orbit audit.")
        return

    sods     = np.array([s                         for s, _ in fwd_list])
    hrs      = sods / 3600.
    dx_all   = np.array([r['dx']                   for _, r in fwd_list])
    code_rms = np.array([r.get('code_rms',  0.)    for _, r in fwd_list])
    phase_rms= np.array([r.get('phase_rms', 0.)    for _, r in fwd_list])
    clk_m    = np.array([r.get('clk',       0.)    for _, r in fwd_list])

    enu_all  = (Re @ dx_all.T).T * 1e3
    up_mm    = enu_all[:, 2]

    # Clock drift rate (m/epoch, converted to mm/ep)
    dclk     = np.gradient(clk_m) * 1e3   # mm/epoch

    def _pearson(a, b):
        if len(a) < 5 or np.std(a) < 1e-10 or np.std(b) < 1e-10:
            return float('nan')
        return float(np.corrcoef(a, b)[0, 1])

    windows = [
        ('full arc (t≥2h)', (hrs >= 2.0)),
        ('5–8 h',           (hrs >= 5.0) & (hrs <= 8.0)),
        ('17–20 h',         (hrs >= 17.0) & (hrs <= 20.0)),
    ]

    print('\n[G_AUDIT] Clock/Orbit residual audit — GPS+Galileo FWD')
    print('[G_AUDIT]   {:18s}  {:>12s}  {:>12s}  {:>14s}  {:>6s}'.format(
        'Window', 'r(Up,code)', 'r(Up,phase)', 'r(Up,dclk/dt)', 'n_ep'))
    for wname, wmask in windows:
        n = int(wmask.sum())
        rc  = _pearson(up_mm[wmask], code_rms[wmask])
        rp  = _pearson(up_mm[wmask], phase_rms[wmask])
        rdc = _pearson(up_mm[wmask], dclk[wmask])
        print('[G_AUDIT]   {:18s}  {:>+12.3f}  {:>+12.3f}  {:>+14.3f}  {:>6d}'.format(
            wname, rc, rp, rdc, n))

    # Hump-window amplitude decomposition
    print('\n[G_AUDIT] Hump-window residual statistics:')
    for wname, wmask in windows[1:]:
        if wmask.sum() < 5:
            continue
        up_rng      = float(up_mm[wmask].max() - up_mm[wmask].min())
        code_rng    = float(code_rms[wmask].max() - code_rms[wmask].min())
        phase_rng   = float(phase_rms[wmask].max() - phase_rms[wmask].min())
        dclk_rng    = float(np.abs(dclk[wmask]).max())
        print(f'[G_AUDIT]   [{wname}]'
              f'  ΔUp={up_rng:.1f} mm'
              f'  Δcode_rms={code_rng:.2f} mm'
              f'  Δphase_rms={phase_rng:.3f} mm'
              f'  max|dclk/dt|={dclk_rng:.2f} mm/ep')

    # Interpretation
    print('\n[G_AUDIT] Interpretation:')
    print('[G_AUDIT]   |r(Up,code)| > 0.7 in hump windows → '
          'systematic code/orbit error drives hump')
    print('[G_AUDIT]   |r(Up,phase)| > 0.7 in hump windows → '
          'phase-only systematic (clock or sat-phase bias) drives hump')
    print('[G_AUDIT]   |r(Up,dclk/dt)| > 0.5 → '
          'clock rate excursion coincides with hump onset')
    print('[G_AUDIT]   All |r| < 0.3 → '
          'humps not explained by observable residuals → subdaily loading candidate')


# ==============================================================================
#  Run H -- Position/Clock process-noise sensitivity analysis (v94)
# ==============================================================================
def _run_h_q_audit(fwd_lo, fwd_hi, fwd_base, REF):
    """
    Compare Up hump amplitude across three Q-scale runs:
      baseline (scale=1.0), Q_LO (scale=0.3), Q_HI (scale=3.0).

    Ambiguity / ZWD / iono Q are completely untouched in all runs.
    Returns a dict with per-run hump stats and a leakage verdict.
    """
    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)

    def _up_stats(fwd):
        if not fwd:
            return None
        lst    = sorted(fwd.items())
        sods   = np.array([s for s, _ in lst])
        hrs    = sods / 3600.
        up_mm  = (Re @ np.array([r['dx'] for _, r in lst]).T).T[:, 2] * 1e3
        post   = hrs >= 2.0

        def _hamp(mask):
            return float(up_mm[mask].max() - up_mm[mask].min()) if mask.sum() >= 5 else float('nan')

        h58   = (hrs >= 5.0) & (hrs <= 8.0)
        h1720 = (hrs >= 17.) & (hrs <= 20.)
        rms_p = float(np.sqrt(np.mean(up_mm[post]**2))) if post.sum() > 0 else float('nan')
        return dict(up_rms_mm=rms_p, hump58_mm=_hamp(h58), hump1720_mm=_hamp(h1720))

    stats = {
        'baseline':   _up_stats(fwd_base),
        'Q_LO_0.3x':  _up_stats(fwd_lo),
        'Q_HI_3.0x':  _up_stats(fwd_hi),
    }

    print('\n[H_QAUDIT] Position/Clock Q sensitivity:')
    print('[H_QAUDIT]   {:12s}  {:>9s}  {:>11s}  {:>13s}'.format(
          'Run', 'UpRMS(mm)', 'Hump58(mm)', 'Hump1720(mm)'))
    for run_name, s in stats.items():
        if s is None:
            print('[H_QAUDIT]   {:12s}  SKIPPED'.format(run_name))
            continue
        print('[H_QAUDIT]   {:12s}  {:9.1f}  {:11.1f}  {:13.1f}'.format(
              run_name, s['up_rms_mm'], s['hump58_mm'], s['hump1720_mm']))

    # Verdict: if hump amplitude changes >20% between Q_LO and Q_HI -> leakage
    verdict = 'UNDETERMINED'
    s_lo  = stats.get('Q_LO_0.3x')
    s_hi  = stats.get('Q_HI_3.0x')
    s_bas = stats.get('baseline')
    if s_lo and s_hi and s_bas:
        base_h58 = s_bas.get('hump58_mm', 0.)
        if abs(base_h58) > 1.:
            lo_frac = abs(s_lo['hump58_mm'] - base_h58) / abs(base_h58)
            hi_frac = abs(s_hi['hump58_mm'] - base_h58) / abs(base_h58)
            max_frac = max(lo_frac, hi_frac)
            if max_frac > 0.20:
                verdict = ('LEAKAGE DETECTED -- hump scales '
                           '{:.0f}% with Q'.format(max_frac * 100))
            else:
                verdict = ('LEAKAGE REJECTED -- hump Q-invariant '
                           '(max change {:.0f}%)'.format(max_frac * 100))
    print('[H_QAUDIT] Verdict: {}'.format(verdict))
    stats['verdict'] = verdict
    return stats


# ==============================================================================
#  Run I -- Innovation spectral audit (v94, post-processing only)
# ==============================================================================
def _run_i_innov_spectral(fwd_gg, REF):
    """
    Compute power spectral density of Up error, postfit phase_rms, and
    prefit_phase_rms.  Flag spectral peaks near 12-h and 24-h periods.
    No new KF pass required.
    """
    try:
        from scipy.signal import lombscargle as _ls
        _use_ls = True
    except ImportError:
        _use_ls = False

    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)

    lst = sorted(fwd_gg.items())
    if len(lst) < 60:
        print('[I_SPECTRAL] Too few epochs -- skipping.')
        return {}

    sods    = np.array([s for s, _ in lst])
    hrs     = sods / 3600.
    dx_all  = np.array([r['dx'] for _, r in lst])
    up_mm   = (Re @ dx_all.T).T[:, 2] * 1e3
    phs_rms = np.array([r.get('phase_rms', 0.) for _, r in lst])
    pf_rms  = np.array([r.get('prefit_phase_rms', 0.) * 1e3 for _, r in lst])

    post   = hrs >= 2.0
    sods_p = sods[post];  up_p = up_mm[post]
    phs_p  = phs_rms[post]; pf_p = pf_rms[post]

    # Target periods: 12h, 24h, 8h, 6h
    periods_s     = [43200., 86400., 28800., 21600.]
    period_labels = ['12h', '24h', '8h', '6h']
    freqs_cps     = [1.0 / p for p in periods_s]

    results_out = {}

    def _norm_power_at_freqs(t, y, freqs_hz):
        """Normalised spectral power at each frequency (Lomb-Scargle or FFT)."""
        if len(t) < 20:
            return [0.] * len(freqs_hz)
        y_c = y - y.mean()
        if y_c.std() < 1e-10:
            return [0.] * len(freqs_hz)
        if _use_ls:
            ang = [2.0 * math.pi * f for f in freqs_hz]
            try:
                pw = _ls(t, y_c, np.array(ang), normalize=True)
                return list(pw)
            except Exception:
                pass
        # FFT fallback
        dt = float(np.median(np.diff(t))) if len(t) > 2 else 30.
        if dt <= 0:
            return [0.] * len(freqs_hz)
        fft_v  = np.fft.rfft(y_c)
        fft_f  = np.fft.rfftfreq(len(y_c), d=dt)
        total  = float(np.sum(np.abs(fft_v) ** 2)) + 1e-30
        return [float(np.abs(fft_v[int(np.argmin(np.abs(fft_f - f)))])**2 / total)
                for f in freqs_hz]

    print('\n[I_SPECTRAL] Innovation spectral audit -- GPS+Galileo FWD')
    print('[I_SPECTRAL]   Post-convergence epochs: {:d} (t>=2h)'.format(int(post.sum())))

    for sig_name, signal in [('Up_error', up_p),
                               ('Postfit_Phase_RMS', phs_p),
                               ('Prefit_Phase_RMS',  pf_p)]:
        pwr = _norm_power_at_freqs(sods_p, signal, freqs_cps)
        print('[I_SPECTRAL]   {}:'.format(sig_name))
        for lbl, pw in zip(period_labels, pwr):
            print('[I_SPECTRAL]     period={:4s}  norm_power={:.4f}'.format(lbl, pw))
        dom_idx = int(np.argmax(pwr))
        results_out[sig_name] = dict(zip(period_labels, pwr))
        results_out[sig_name]['dominant'] = period_labels[dom_idx]
        print('[I_SPECTRAL]     -> Dominant period: {}'.format(period_labels[dom_idx]))

    # Window comparison for Up error
    h58   = (hrs >= 5.0) & (hrs <= 8.0)
    h1720 = (hrs >= 17.) & (hrs <= 20.)
    nhump = ~(h58 | h1720) & post
    for wname, wmask in [('hump_5-8h', h58), ('hump_17-20h', h1720), ('non-hump', nhump)]:
        n_w = int(wmask.sum())
        if n_w < 10:
            continue
        pwr_w = _norm_power_at_freqs(sods[wmask], up_mm[wmask], freqs_cps)
        print('[I_SPECTRAL]   Window {:12s} (n={:4d}):  {}'.format(
              wname, n_w,
              '  '.join('{:4s}={:.4f}'.format(l, p)
                        for l, p in zip(period_labels, pwr_w))))

    # Overall verdict
    up_pwr = results_out.get('Up_error', {})
    v12 = up_pwr.get('12h', 0.)
    v24 = up_pwr.get('24h', 0.)
    if v12 > 0.15:
        verdict = ('12-h PEAK DETECTED (power={:.3f}) -- '
                   'GPS orbital-repeat; check SP3/CLK quality').format(v12)
    elif v24 > 0.15:
        verdict = ('24-h PEAK DETECTED (power={:.3f}) -- '
                   'diurnal signal; check subdaily loading').format(v24)
    elif max(v12, v24) > 0.05:
        verdict = ('Weak periodic signal (12h={:.3f}, 24h={:.3f}) -- '
                   'ambiguous; further investigation needed').format(v12, v24)
    else:
        verdict = ('No dominant periodic component (12h={:.3f}, 24h={:.3f}) -- '
                   'broadband noise or non-periodic loading').format(v12, v24)
    print('[I_SPECTRAL] Verdict: {}'.format(verdict))
    results_out['verdict'] = verdict
    return results_out


# ==============================================================================
#  Run J -- Clock perturbation discriminator (v94)
# ==============================================================================
def _run_j_clk_verdict(fwd_base, fwd_pos, fwd_neg, REF, perturb_m):
    """
    Compare Up hump amplitude between baseline and +/-clk_perturb_m runs.

    A uniform satellite-clock perturbation is completely absorbed by the
    receiver clock state in 1-2 epochs when the filter is well-conditioned.
    Any residual Up-hump shift signals clock-to-position coupling (leakage).
    """
    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)

    def _series(fwd):
        if not fwd:
            return None, None, None
        lst   = sorted(fwd.items())
        sods  = np.array([s for s, _ in lst])
        hrs   = sods / 3600.
        up_mm = (Re @ np.array([r['dx'] for _, r in lst]).T).T[:, 2] * 1e3
        return sods, hrs, up_mm

    sods_b, hrs_b, up_b = _series(fwd_base)
    _, hrs_p, up_p       = _series(fwd_pos)
    _, hrs_n, up_n       = _series(fwd_neg)

    if up_b is None:
        print('[J_CLK] Baseline FWD unavailable -- skipping.')
        return {}

    results_out = {}
    windows = [('5-8h',   (hrs_b >= 5.)  & (hrs_b <= 8.)),
               ('17-20h', (hrs_b >= 17.) & (hrs_b <= 20.))]

    print('\n[J_CLK] Clock perturbation discriminator  (+-{:.0f} mm)'.format(perturb_m * 1e3))
    print('[J_CLK]   {:8s}  {:>12s}  {:>11s}  {:>11s}  {:>13s}  {:>12s}'.format(
          'Window', 'base_amp(mm)', 'pos_amp(mm)', 'neg_amp(mm)',
          'shift_pos(mm)', 'shift_neg(mm)'))

    for wname, wmask in windows:
        if wmask.sum() < 5:
            continue
        amp_b = float(up_b[wmask].max() - up_b[wmask].min())
        amp_p = float(up_p[wmask].max() - up_p[wmask].min()) if up_p is not None else float('nan')
        amp_n = float(up_n[wmask].max() - up_n[wmask].min()) if up_n is not None else float('nan')
        shift_p = amp_p - amp_b
        shift_n = amp_n - amp_b
        results_out[wname] = dict(base=amp_b, pos=amp_p, neg=amp_n,
                                   shift_pos=shift_p, shift_neg=shift_n)
        print('[J_CLK]   {:8s}  {:>12.1f}  {:>11.1f}  {:>11.1f}  {:>+13.1f}  {:>+12.1f}'.format(
              wname, amp_b, amp_p, amp_n, shift_p, shift_n))

    all_shifts = [abs(v.get('shift_pos', 0.))
                  for v in results_out.values()
                  if isinstance(v, dict) and not math.isnan(v.get('shift_pos', float('nan')))]
    max_shift  = max(all_shifts) if all_shifts else 0.
    perturb_mm = perturb_m * 1e3
    ratio_pct  = max_shift / perturb_mm * 100. if perturb_mm > 0 else 0.

    if max_shift > 0.10 * perturb_mm:
        verdict = ('LEAKAGE CONFIRMED -- hump shifts {:.1f} mm for {:.0f} mm '
                   'clock perturbation ({:.0f}% leakage ratio)').format(
                   max_shift, perturb_mm, ratio_pct)
    else:
        verdict = ('LEAKAGE REJECTED -- hump unchanged for +-{:.0f} mm clock '
                   'perturbation (max shift {:.1f} mm, {:.0f}% ratio)').format(
                   perturb_mm, max_shift, ratio_pct)
    print('[J_CLK] Verdict: {}'.format(verdict))
    results_out['verdict'] = verdict
    return results_out


# ==============================================================================
#  Forensic leakage plot -- Runs H, I, J summary (v94)
# ==============================================================================
# ==============================================================================
#  Run K — Hump Decomposition + Orbit/Clock Whitening Test (v95)
# ==============================================================================
def _run_k_hump_decomp(fwd_gg, fwd_whiten, REF, whiten_by_phi=None):
    """
    Run K:  Hump Decomposition + Orbit/Clock AR(1) Whitening Test.

    Part 1 — Post-processing hump decomposition (no new KF pass):
      For each epoch in the hump windows [5-8 h] and [17-20 h] quantify
      the contribution to the mean Up error from:
        (a) Orbit residuals  — per-sat postfit code residual projected onto
                               the Up direction via LOS unit vector.
                               orbit_up_mm = mean_i( res_i * los_dot_up_i )
        (b) Satellite clock  — clock drift rate dclk/dt projected uniformly
                               (all sats see same receiver-clock shift;
                               sat-clock residuals average to zero for many sats,
                               but temporal drift correlates with Up leakage).
                               clock_up_mm = dclk_dt_mm * (1/n_sats)   [proxy]
        (c) Phase innovations — prefit phase RMS per epoch (spectral proxy for
                               measurement-noise-driven Up excitation).

      Multiple-regression of Up error on (a), (b), (c) + ZWD → partial R²
      quantifies the fraction of hump variance explained by each source.

      Cross-correlation matrix of all predictors is printed to guard against
      misleading partial R² attribution caused by collinear regressors.

    Part 2 — Whitening run comparison:
      If fwd_whiten is provided, compare hump amplitude and Up RMS between
      the baseline and the AR(1)-whitened pass.  A reduction in hump amplitude
      confirms that temporally correlated orbit/clock noise drives the hump.

    Part 3 — phi sensitivity (whiten_by_phi dict):
      If whiten_by_phi = {0.90: fwd_90, 0.95: fwd_95, 0.98: fwd_98} is
      provided, a sensitivity table is printed showing hump amplitude and
      Up RMS for each phi value alongside the baseline.  Monotonic improvement
      with phi confirms a coloured-noise (AR) mechanism.
    """
    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)

    def _series(fwd):
        if not fwd:
            return None
        lst   = sorted(fwd.items())
        sods  = np.array([s for s, _ in lst])
        hrs   = sods / 3600.
        up_mm = (Re @ np.array([r['dx'] for _, r in lst]).T).T[:, 2] * 1e3
        clk_m = np.array([r.get('clk', 0.) for _, r in lst])
        zwd_m = np.array([r.get('zwd', 0.) for _, r in lst]) * 1e3
        pphs  = np.array([r.get('prefit_phase_rms', 0.) * 1e3 for _, r in lst])
        pcode = np.array([r.get('code_rms', 0.) for _, r in lst])

        # Orbit-Up proxy: mean over sats of (code_res_mm × LOS·Up)
        orbit_up = np.zeros(len(lst))
        for i, (_, r) in enumerate(lst):
            cps = r.get('code_per_sat', {})
            if cps:
                vals = [res * ldu for res, ldu, _ in cps.values()]
                orbit_up[i] = float(np.mean(vals))

        # Clock drift rate (mm/epoch)
        dclk_dt = np.gradient(clk_m) * 1e3

        return dict(sods=sods, hrs=hrs, up_mm=up_mm,
                    orbit_up=orbit_up, dclk_dt=dclk_dt,
                    zwd_mm=zwd_m, pphs=pphs, pcode=pcode)

    base = _series(fwd_gg)
    if base is None:
        print('[K_DECOMP] No GPS+Galileo FWD data — skipping.')
        return {}

    sods, hrs, up = base['sods'], base['hrs'], base['up_mm']
    post_mask = hrs >= 2.0

    def _pearson(a, b):
        if len(a) < 5 or np.std(a) < 1e-10 or np.std(b) < 1e-10:
            return float('nan')
        return float(np.corrcoef(a, b)[0, 1])

    def _partial_r2(y, X_cols):
        """Partial R² via OLS.  Returns list of partial R² for each column."""
        if len(y) < 10 or X_cols is None or len(X_cols) == 0:
            return [float('nan')] * len(X_cols)
        X = np.column_stack(X_cols)
        # Normalise columns to unit variance (avoids ill-conditioning)
        scales = np.std(X, axis=0)
        scales[scales < 1e-12] = 1.0
        Xn = X / scales
        try:
            coeff, *_ = np.linalg.lstsq(
                np.column_stack([np.ones(len(y)), Xn]), y, rcond=None)
        except Exception:
            return [float('nan')] * len(X_cols)
        y_pred = coeff[0] + Xn @ coeff[1:]
        ss_tot  = float(np.sum((y - y.mean()) ** 2)) + 1e-30
        # Partial R²: variance of each predictor's contribution
        partial = []
        for j in range(len(X_cols)):
            contrib_j = coeff[j + 1] * Xn[:, j]
            partial.append(float(np.sum(contrib_j ** 2) / ss_tot))
        return partial

    windows = [
        ('5-8h',   (hrs >= 5.0) & (hrs <= 8.0)),
        ('17-20h', (hrs >= 17.) & (hrs <= 20.)),
        ('full arc (t≥2h)', post_mask),
    ]

    pred_labels = ['orbit_up', 'dclk_dt', 'phase_innov', 'ZWD']
    pred_arrays = [base['orbit_up'], base['dclk_dt'], base['pphs'], base['zwd_mm']]

    print('\n[K_DECOMP] Hump decomposition — GPS+Galileo FWD')
    print('[K_DECOMP]   {:20s}  {:>5s}  {:>8s}  {:>8s}  {:>12s}  {:>6s}'.format(
        'Predictor', 'r', 'partial_R²', 'Up_RMS', 'ΔUp_hump', 'n_ep'))

    results_k = {}
    for wname, wmask in windows:
        n_w = int(wmask.sum())
        if n_w < 10:
            print(f'[K_DECOMP]   [{wname}]  n={n_w} — too few epochs, skipped')
            continue
        u_w = up[wmask]
        hump_amp = float(u_w.max() - u_w.min())
        partial_r2 = _partial_r2(u_w, [p[wmask] for p in pred_arrays])
        print(f'[K_DECOMP]   Window [{wname}]  n={n_w}  ΔUp={hump_amp:.1f} mm')
        for lbl, r2, parr in zip(pred_labels, partial_r2, pred_arrays):
            rc  = _pearson(u_w, parr[wmask])
            rms = float(np.sqrt(np.mean(parr[wmask] ** 2)))
            print(f'[K_DECOMP]     {lbl:<20s}  r={rc:+.3f}  partial_R²={r2:.3f}  '
                  f'rms={rms:.2f} mm')
        results_k[wname] = dict(n=n_w, hump_amp=hump_amp,
                                 partial_r2=dict(zip(pred_labels, partial_r2)))

    # ── Predictor cross-correlation matrix ───────────────────────────────────
    # Guards against misleading partial R² attribution: if two predictors are
    # highly correlated (|r| > 0.7), the OLS split between them is unstable and
    # a large partial R² may reflect collinearity rather than causation.
    print('\n[K_DECOMP] Predictor cross-correlation matrix (full arc, t≥2h)')
    print('[K_DECOMP]   Columns / rows: orbit_up  dclk_dt  innov_rms  ZWD')
    print('[K_DECOMP]   |r| > 0.70 → collinear pair → partial R² split unreliable')
    _xcorr_preds = [p[post_mask] for p in pred_arrays]
    _n_p = len(pred_labels)
    _xcorr_mat = np.full((_n_p, _n_p), float('nan'))
    for _i in range(_n_p):
        for _j in range(_n_p):
            _xcorr_mat[_i, _j] = _pearson(_xcorr_preds[_i], _xcorr_preds[_j])
    # Header row
    _hdr = '  {:>14s}' + '  {:>9s}' * _n_p
    _short = ['orbit_up', 'dclk_dt', 'innov_rms', 'ZWD']
    print('[K_DECOMP]   ' + ('{:>14s}' + '  {:>9s}' * _n_p).format('', *_short))
    for _i, lbl_i in enumerate(_short):
        _row_vals = ''.join(
            f'  {_xcorr_mat[_i, _j]:>+9.3f}' for _j in range(_n_p))
        _flag = ''
        for _j in range(_n_p):
            if _i != _j and abs(_xcorr_mat[_i, _j]) > 0.70:
                _flag += f' ← collinear with {_short[_j]}'
        print(f'[K_DECOMP]   {lbl_i:>14s}{_row_vals}{_flag}')
    results_k['xcorr_matrix'] = _xcorr_mat.tolist()

    # ── Part 2: whitening comparison (phi=0.95 reference pass) ─────────────
    whiten = _series(fwd_whiten) if fwd_whiten else None

    print('\n[K_DECOMP] Interpretation:')
    print('[K_DECOMP]   partial_R²(orbit_up) > 0.25 in hump window → orbit '
          'residual autocorrelation drives >25% of hump variance.')
    print('[K_DECOMP]   partial_R²(dclk_dt)  > 0.25 → receiver-clock drift '
          'correlates with hump → Q-leakage confirmed via clock channel.')
    print('[K_DECOMP]   partial_R²(ZWD) > 0.25 → troposphere leakage dominant.')
    print('[K_DECOMP]   All partial_R² < 0.10 → hump is NOT explained by '
          'observable regressors → unmodelled geophysical or loading signal.')

    if whiten:
        print('\n[K_WHITEN] AR(1) whitening test (phi=0.95):')
        print('[K_WHITEN]   {:8s}  {:>12s}  {:>12s}  {:>10s}  {:>10s}'.format(
            'Window', 'base_amp(mm)', 'whit_amp(mm)', 'Δ(mm)', 'Δ/base %'))
        w_windows = [('5-8h',   (hrs >= 5.0) & (hrs <= 8.0)),
                     ('17-20h', (hrs >= 17.) & (hrs <= 20.))]
        for wname, wmask in w_windows:
            if wmask.sum() < 5:
                continue
            amp_b = float(up[wmask].max() - up[wmask].min())
            amp_w = float(whiten['up_mm'][wmask].max() - whiten['up_mm'][wmask].min()) if wmask.sum() >= 5 else float('nan')
            delta = amp_w - amp_b
            pct   = delta / abs(amp_b) * 100 if abs(amp_b) > 0.1 else float('nan')
            print(f'[K_WHITEN]   {wname:<8s}  {amp_b:>12.1f}  {amp_w:>12.1f}  '
                  f'{delta:>+10.1f}  {pct:>+9.1f}%')
        post_b = post_mask
        rms_b  = float(np.sqrt(np.mean(up[post_b] ** 2)))
        rms_w  = float(np.sqrt(np.mean(whiten['up_mm'][post_b] ** 2)))
        print(f'[K_WHITEN]   Up RMS (t≥2h): baseline={rms_b:.1f} mm  '
              f'whitened={rms_w:.1f} mm  Δ={rms_w - rms_b:+.1f} mm')
        if rms_w < rms_b - 5.0:
            print('[K_WHITEN] Verdict: WHITENING REDUCES HUMP — temporally '
                  'correlated orbit/clock noise is a primary hump driver.')
        elif rms_w > rms_b + 5.0:
            print('[K_WHITEN] Verdict: WHITENING WORSENS FIT — AR(1) model '
                  'over-corrects; hump is not from short-term code autocorrelation.')
        else:
            print('[K_WHITEN] Verdict: WHITENING NEUTRAL (|ΔUp RMS| ≤ 5 mm) — '
                  'short-term code autocorrelation is NOT the primary hump driver.')
        results_k['whiten'] = dict(rms_base=rms_b, rms_whiten=rms_w)

    # ── Part 3: phi sensitivity table ────────────────────────────────────────
    # Compares hump amplitude and Up RMS across phi ∈ {0.90, 0.95, 0.98}.
    # If effect is monotonically increasing with phi the noise has genuine
    # long-memory structure consistent with an AR(1) model.
    if whiten_by_phi:
        _phi_sorted = sorted(whiten_by_phi.keys())
        post_b = post_mask
        rms_baseline = float(np.sqrt(np.mean(up[post_b] ** 2)))

        # Collect hump window masks once
        _w58   = (hrs >= 5.0)  & (hrs <= 8.0)
        _w1720 = (hrs >= 17.0) & (hrs <= 20.0)

        def _amp(arr, msk):
            if msk.sum() < 5: return float('nan')
            return float(arr[msk].max() - arr[msk].min())

        amp_base_58   = _amp(up, _w58)
        amp_base_1720 = _amp(up, _w1720)

        print('\n[K_WHITEN] ── phi sensitivity table ─────────────────────────────')
        print('[K_WHITEN]   {:>6s}  {:>12s}  {:>12s}  {:>12s}  {:>12s}  {:>10s}  {:>10s}'.format(
            'phi', 'amp58(mm)', 'Δamp58', 'amp1720(mm)', 'Δamp1720',
            'RMS_t≥2h', 'ΔRMS'))
        print('[K_WHITEN]   ' + '-' * 82)
        _row = '{:>6.2f}  {:>12.1f}  {:>+12.1f}  {:>12.1f}  {:>+12.1f}  {:>10.1f}  {:>+10.1f}'
        print('[K_WHITEN]   {:>6s}  {:>12.1f}  {:>12s}  {:>12.1f}  {:>12s}  {:>10.1f}  {:>10s}'.format(
            'base', amp_base_58, '—', amp_base_1720, '—', rms_baseline, '—'))

        _phi_sens = {}
        for _phi_k in _phi_sorted:
            _fw = _series(whiten_by_phi[_phi_k])
            if _fw is None:
                continue
            _u = _fw['up_mm']
            _amp58   = _amp(_u, _w58)
            _amp1720 = _amp(_u, _w1720)
            _rms     = float(np.sqrt(np.mean(_u[post_b] ** 2)))
            _d58     = _amp58   - amp_base_58
            _d1720   = _amp1720 - amp_base_1720
            _drms    = _rms - rms_baseline
            print('[K_WHITEN]   ' + _row.format(
                _phi_k, _amp58, _d58, _amp1720, _d1720, _rms, _drms))
            _phi_sens[_phi_k] = dict(
                amp58=_amp58, delta_amp58=_d58,
                amp1720=_amp1720, delta_amp1720=_d1720,
                rms=_rms, delta_rms=_drms)

        # Monotonicity verdict
        if len(_phi_sens) >= 2:
            _drms_vals = [_phi_sens[p]['delta_rms'] for p in _phi_sorted
                          if p in _phi_sens]
            _monotone_down = all(
                _drms_vals[i] <= _drms_vals[i+1]
                for i in range(len(_drms_vals)-1))
            _monotone_up   = all(
                _drms_vals[i] >= _drms_vals[i+1]
                for i in range(len(_drms_vals)-1))
            if _monotone_down and _drms_vals[-1] < -5.0:
                print('[K_WHITEN] Sensitivity verdict: MONOTONE IMPROVEMENT — '
                      'coloured noise (AR) leakage is a genuine hump driver; '
                      'amplitude grows with phi (longer memory).')
            elif _monotone_up and _drms_vals[0] > 5.0:
                print('[K_WHITEN] Sensitivity verdict: MONOTONE WORSENING — '
                      'AR(1) over-correction; noise has shorter memory than '
                      'modelled; check orbit/clock product quality.')
            elif all(abs(v) < 5.0 for v in _drms_vals):
                print('[K_WHITEN] Sensitivity verdict: INSENSITIVE TO phi — '
                      '|ΔRMS| < 5 mm for all phi; correlated measurement noise '
                      'is NOT the primary hump driver.')
            else:
                print('[K_WHITEN] Sensitivity verdict: NON-MONOTONE — mixed '
                      'evidence; hump has multiple competing sources.')
        results_k['phi_sensitivity'] = _phi_sens

    return dict(results=results_k, base=base,
                whiten=whiten, post_mask=post_mask, sods=sods, hrs=hrs,
                whiten_by_phi={k: _series(v)
                               for k, v in (whiten_by_phi or {}).items()})


def _plot_hump_decomp(k_out, outdir=None, whiten_by_phi=None):
    """
    Six-panel figure for Run K: hump decomposition + whitening test.
      (a) Up error with hump windows highlighted
      (b) Orbit-Up proxy time-series + correlation in hump windows
      (c) Clock drift rate (dclk/dt) time-series
      (d) Partial R² bar chart (predictor contributions in each window)
      (e) Baseline vs all three AR(1)-whitened Up curves (phi overlay)
      (f) Power spectrum comparison: baseline vs whitened (phi=0.95)
    """
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
    except ImportError:
        print('[K_PLOT] matplotlib not available — skipping'); return

    if outdir is None:
        outdir = os.path.dirname(os.path.abspath(__file__))
    if k_out is None or 'base' not in k_out:
        print('[K_PLOT] No decomposition data — skipping'); return

    base  = k_out['base']
    whit  = k_out.get('whiten')
    hrs   = k_out['hrs']
    up    = base['up_mm']
    post  = k_out['post_mask']

    hump_spans = [(5., 8.), (17., 20.)]

    def _shade(ax_):
        for h0, h1 in hump_spans:
            ax_.axvspan(h0, h1, color='gold', alpha=0.18, zorder=0)

    fig = plt.figure(figsize=(16, 14))
    gs  = gridspec.GridSpec(3, 2, figure=fig, hspace=0.42, wspace=0.32)
    fig.suptitle(
        'PPP-AR v95 — Run K: Hump Decomposition + AR(1) Whitening Test\n'
        '(a-c) predictor time-series | (d) partial R² | (e-f) whitening effect',
        fontsize=11, fontweight='bold')

    # (a) Up error
    ax_a = fig.add_subplot(gs[0, 0])
    ax_a.plot(hrs, up, color='#4363d8', lw=0.8, label='Up error (baseline)')
    ax_a.axhline(0, color='k', lw=0.4); _shade(ax_a)
    ax_a.set_ylabel('Up Error (mm)'); ax_a.set_title('(a) Up error — GPS+Galileo FWD')
    ax_a.set_ylim(-350, 350); ax_a.legend(fontsize=7); ax_a.grid(True, alpha=0.25)

    # (b) Orbit-Up proxy
    ax_b = fig.add_subplot(gs[0, 1])
    ax_b.plot(hrs, base['orbit_up'], color='#e6194b', lw=0.7, alpha=0.85,
              label='Orbit-Up proxy\n(mean code_res×LOS·Up, mm)')
    ax_b2 = ax_b.twinx()
    ax_b2.plot(hrs, up, color='#4363d8', lw=0.5, alpha=0.35, label='Up (right)')
    ax_b.axhline(0, color='k', lw=0.3); _shade(ax_b)
    ax_b.set_ylabel('Orbit proxy (mm)', color='#e6194b')
    ax_b2.set_ylabel('Up Error (mm)', color='#4363d8')
    ax_b.set_title('(b) Orbit residual proxy vs Up error')
    ax_b.legend(fontsize=7, loc='lower left'); ax_b.grid(True, alpha=0.2)

    # (c) Clock drift rate
    ax_c = fig.add_subplot(gs[1, 0])
    ax_c.plot(hrs, base['dclk_dt'], color='#f58231', lw=0.7, alpha=0.85,
              label='dclk/dt (mm/ep)')
    ax_c2 = ax_c.twinx()
    ax_c2.plot(hrs, up, color='#4363d8', lw=0.5, alpha=0.35, label='Up (right)')
    ax_c.axhline(0, color='k', lw=0.3); _shade(ax_c)
    ax_c.set_ylabel('dclk/dt (mm/epoch)', color='#f58231')
    ax_c2.set_ylabel('Up Error (mm)', color='#4363d8')
    ax_c.set_title('(c) Receiver clock drift rate vs Up error')
    ax_c.legend(fontsize=7, loc='lower left'); ax_c.grid(True, alpha=0.2)
    ax_c.set_xlabel('Time (h)')

    # (d) Partial R² bar chart
    ax_d = fig.add_subplot(gs[1, 1])
    win_labels = ['5-8h', '17-20h', 'full arc\n(t≥2h)']
    pred_labels = ['orbit_up', 'dclk_dt', 'phase_innov', 'ZWD']
    bar_colors  = ['#e6194b', '#f58231', '#3cb44b', '#4363d8']
    n_wins  = len(win_labels)
    n_preds = len(pred_labels)
    x_pos   = np.arange(n_wins)
    bw      = 0.18
    win_keys = ['5-8h', '17-20h', 'full arc (t≥2h)']
    for pi, (pl, bc) in enumerate(zip(pred_labels, bar_colors)):
        r2_vals = []
        for wk in win_keys:
            wd = k_out.get('results', {}).get(wk, {})
            r2_vals.append(wd.get('partial_r2', {}).get(pl, float('nan')))
        offset = (pi - (n_preds - 1) / 2.) * bw
        ax_d.bar(x_pos + offset, r2_vals, bw, label=pl, color=bc, alpha=0.8)
    ax_d.axhline(0.25, color='k', lw=0.7, ls='--', label='R²=0.25 threshold')
    ax_d.set_xticks(x_pos); ax_d.set_xticklabels(win_labels, fontsize=8)
    ax_d.set_ylabel('Partial R²'); ax_d.set_ylim(0, 1.0)
    ax_d.set_title('(d) Partial R² — predictor contributions to hump variance')
    ax_d.legend(fontsize=7); ax_d.grid(True, alpha=0.25, axis='y')

    # (e) Baseline vs whitened Up — overlay all phi values
    ax_e = fig.add_subplot(gs[2, 0])
    ax_e.plot(hrs, up, color='#4363d8', lw=0.9, alpha=0.90, label='Baseline')
    # Pull multi-phi series from k_out (already _series()-processed)
    _wbp = k_out.get('whiten_by_phi') or {}
    # Also fall back to the single whit (phi=0.95) if no multi-phi dict
    _phi_colors = {0.90: '#e6194b', 0.95: '#f58231', 0.98: '#3cb44b'}
    _phi_used = sorted(_wbp.keys()) if _wbp else ([0.95] if whit is not None else [])
    for _phi_e in _phi_used:
        _ws_e = _wbp.get(_phi_e) if _wbp else whit
        if _ws_e is None:
            continue
        _col_e = _phi_colors.get(_phi_e, '#808080')
        ax_e.plot(hrs, _ws_e['up_mm'], color=_col_e, lw=0.8, alpha=0.85,
                  label=f'AR(1) phi={_phi_e:.2f}')
    ax_e.axhline(0, color='k', lw=0.4); _shade(ax_e)
    ax_e.set_ylabel('Up Error (mm)'); ax_e.set_xlabel('Time (h)')
    ax_e.set_title('(e) Baseline vs AR(1)-whitened Up (phi sweep)')
    ax_e.set_ylim(-350, 350); ax_e.legend(fontsize=7); ax_e.grid(True, alpha=0.25)

    # (f) Power spectrum comparison
    ax_f = fig.add_subplot(gs[2, 1])
    if post.sum() > 60:
        sods_p = k_out['sods'][post]; up_p = up[post]
        dt_s = float(np.median(np.diff(sods_p))) if len(sods_p) > 2 else 30.
        if dt_s > 0:
            yc = up_p - up_p.mean()
            fft_v = np.abs(np.fft.rfft(yc)) ** 2
            fft_f = np.fft.rfftfreq(len(yc), d=dt_s) * 3600.  # cycles/hour
            # smooth
            kern = np.ones(5) / 5
            sm_b = np.convolve(fft_v / (fft_v.sum() + 1e-30), kern, mode='same')
            ax_f.semilogy(fft_f[1:], sm_b[1:], color='#4363d8', lw=0.9, alpha=0.9,
                          label='Baseline')
            if whit is not None:
                yc_w = whit['up_mm'][post] - whit['up_mm'][post].mean()
                fft_w = np.abs(np.fft.rfft(yc_w)) ** 2
                sm_w  = np.convolve(fft_w / (fft_w.sum() + 1e-30), kern, mode='same')
                ax_f.semilogy(fft_f[1:], sm_w[1:], color='#e6194b', lw=0.9,
                              alpha=0.9, label='AR(1)-whitened')
            for f_k, lbl_k in [(1/12., '12h'), (1/24., '24h')]:
                ax_f.axvline(f_k, color='gray', lw=0.7, ls='--', alpha=0.7)
                ax_f.text(f_k + 0.005, ax_f.get_ylim()[0] * 10,
                          lbl_k, fontsize=7, color='gray', va='bottom')
    ax_f.set_xlabel('Frequency (cycles/hour)'); ax_f.set_ylabel('Norm. power')
    ax_f.set_title('(f) Up error power spectrum — baseline vs whitened')
    ax_f.set_xlim(0, 0.5); ax_f.legend(fontsize=7); ax_f.grid(True, alpha=0.2)

    png_path = os.path.join(outdir, 'ppp_hump_decomp.png')
    try:
        fig.savefig(png_path, dpi=150, bbox_inches='tight')
        print(f'[K_PLOT] Plot saved: {png_path}')
    except Exception as e:
        print(f'[K_PLOT] Plot save failed: {e}')
    plt.close(fig)


def _plot_leakage_forensic(h_stats, i_stats, j_stats,
                            fwd_base, fwd_pos, fwd_neg,
                            fwd_lo,  fwd_hi,
                            REF, perturb_m, outdir=None):
    """Four-panel forensic figure for the forward-filter leakage diagnostic."""
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('[LEAKAGE] matplotlib not available -- skipping forensic plot')
        return

    if outdir is None:
        outdir = os.path.dirname(os.path.abspath(__file__))

    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)

    def _up(fwd):
        if not fwd:
            return None, None
        lst   = sorted(fwd.items())
        sods  = np.array([s for s, _ in lst]) / 3600.
        up_mm = (Re @ np.array([r['dx'] for _, r in lst]).T).T[:, 2] * 1e3
        return sods, up_mm

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(
        'PPP-AR v94 -- Forward-Filter Leakage Verification (Runs H / I / J)\n'
        'Diagnostic only -- no fixes applied',
        fontsize=11, fontweight='bold')

    def _shade(ax_):
        for h0_, h1_ in [(5., 8.), (17., 20.)]:
            ax_.axvspan(h0_, h1_, color='gold', alpha=0.18, zorder=0)

    # Panel (a): Run H -- Q-scale Up overlay
    ax = axes[0, 0]
    for fwd_r, lbl, col in [
            (fwd_base, 'Q x1.0 (baseline)', '#4363d8'),
            (fwd_lo,   'Q x0.3',             '#3cb44b'),
            (fwd_hi,   'Q x3.0',             '#e6194b')]:
        h_, u_ = _up(fwd_r)
        if h_ is not None:
            ax.plot(h_, u_, lw=0.8, color=col, alpha=0.85, label=lbl)
    ax.axhline(0, color='k', lw=0.4); _shade(ax)
    ax.set_xlabel('Time (h)'); ax.set_ylabel('Up Error (mm)')
    ax.set_title('(a) Run H -- Q-scale sensitivity')
    ax.set_ylim(-300, 300); ax.legend(fontsize=7); ax.grid(True, alpha=0.3)
    vh = (h_stats or {}).get('verdict', '')
    c_ = 'darkred' if 'DETECTED' in vh or 'CONFIRMED' in vh else 'darkgreen'
    ax.text(0.01, 0.01, vh, transform=ax.transAxes, fontsize=6,
            color=c_, verticalalignment='bottom')

    # Panel (b): Run J -- clock perturbation Up overlay
    ax = axes[0, 1]
    pm_mm = int(round(perturb_m * 1e3))
    for fwd_r, lbl, col in [
            (fwd_base, 'baseline (0 mm)',    '#4363d8'),
            (fwd_pos,  '+{} mm clk'.format(pm_mm), '#e6194b'),
            (fwd_neg,  '-{} mm clk'.format(pm_mm), '#3cb44b')]:
        h_, u_ = _up(fwd_r)
        if h_ is not None:
            ax.plot(h_, u_, lw=0.8, color=col, alpha=0.85, label=lbl)
    ax.axhline(0, color='k', lw=0.4); _shade(ax)
    ax.set_xlabel('Time (h)'); ax.set_ylabel('Up Error (mm)')
    ax.set_title('(b) Run J -- clock perturbation')
    ax.set_ylim(-300, 300); ax.legend(fontsize=7); ax.grid(True, alpha=0.3)
    vj = (j_stats or {}).get('verdict', '')
    cj = 'darkred' if 'CONFIRMED' in vj else 'darkgreen'
    ax.text(0.01, 0.01, vj, transform=ax.transAxes, fontsize=6,
            color=cj, verticalalignment='bottom')

    # Panel (c): Run I -- Up error power spectrum
    ax = axes[1, 0]
    h_b, u_b = _up(fwd_base)
    if h_b is not None and len(h_b) > 60:
        post   = h_b >= 2.0
        sods_b = h_b[post] * 3600.
        y_c    = u_b[post] - u_b[post].mean()
        dt     = float(np.median(np.diff(sods_b))) if len(sods_b) > 2 else 30.
        if dt > 0 and y_c.std() > 1e-6:
            N_sp  = len(y_c)
            fft_v = np.fft.rfft(y_c)
            fft_f = np.fft.rfftfreq(N_sp, d=dt) * 3600.   # cyc/h
            fft_p = np.abs(fft_v) ** 2
            fft_p /= fft_p.sum() + 1e-30
            mask_f = (fft_f > 0.05) & (fft_f < 4.0)
            ax.semilogy(fft_f[mask_f], fft_p[mask_f],
                        color='#4363d8', lw=0.8, alpha=0.85)
            for prd_h, lbl in [(12, '12h'), (24, '24h'), (8, '8h')]:
                ax.axvline(1.0 / prd_h, color='red', lw=0.9, ls='--',
                           alpha=0.75, label='{} (1/{} cyc/h)'.format(lbl, prd_h))
    ax.set_xlabel('Frequency (cycles/hour)')
    ax.set_ylabel('Normalised power')
    ax.set_title('(c) Run I -- Up error power spectrum (t>=2h)')
    ax.legend(fontsize=7); ax.grid(True, alpha=0.3, which='both')
    vi = (i_stats or {}).get('verdict', '')
    ci = 'darkred' if 'DETECTED' in vi else 'darkgreen'
    ax.text(0.01, 0.01, vi, transform=ax.transAxes, fontsize=6,
            color=ci, verticalalignment='bottom')

    # Panel (d): Run H hump amplitude bar chart
    ax = axes[1, 1]
    if h_stats:
        run_keys  = ['baseline', 'Q_LO_0.3x', 'Q_HI_3.0x']
        q_labels  = ['Q x1.0', 'Q x0.3', 'Q x3.0']
        h58_v  = [h_stats.get(r, {}).get('hump58_mm',   float('nan'))
                  if isinstance(h_stats.get(r), dict) else float('nan')
                  for r in run_keys]
        h1720_v = [h_stats.get(r, {}).get('hump1720_mm', float('nan'))
                   if isinstance(h_stats.get(r), dict) else float('nan')
                   for r in run_keys]
        x_p = np.arange(len(q_labels)); bw = 0.35
        ax.bar(x_p - bw/2, h58_v,   bw, color='#e6194b', alpha=0.8, label='Hump 5-8h')
        ax.bar(x_p + bw/2, h1720_v, bw, color='#4363d8', alpha=0.8, label='Hump 17-20h')
        ax.set_xticks(x_p); ax.set_xticklabels(q_labels, fontsize=9)
        ax.set_xlabel('Q scale'); ax.set_ylabel('Hump amplitude (mm)')
        ax.set_title('(d) Run H -- hump amplitude vs Q scale')
        ax.legend(fontsize=8); ax.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()
    out_path = os.path.join(outdir, 'ppp_leakage_forensic.png')
    try:
        fig.savefig(out_path, dpi=150, bbox_inches='tight')
        print('[LEAKAGE] Forensic plot saved: {}'.format(out_path))
    except Exception as e:
        print('[LEAKAGE] Plot save failed: {}'.format(e))
    plt.close(fig)



def _plot_dual_harmonic(h, m_raw, label, outdir=None):
    """Plot dual-harmonic fit (Run E) — Up baseline vs corrected."""
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('[DUAL_HARM] matplotlib not available — skipping plot'); return

    if outdir is None:
        outdir = os.path.dirname(os.path.abspath(__file__))

    sods_h = h['sods'] / 3600.
    fig, (ax_u, ax_3) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    fig.suptitle(
        f'PPP-AR v93 — Run E: Dual-Harmonic Vertical Absorber  ({label})\n'
        f'A1={h["A1"]:+.2f} mm  B1={h["B1"]:+.2f} mm  '
        f'A2={h["A2"]:+.2f} mm  B2={h["B2"]:+.2f} mm  '
        f'Amp12={h["amp12"]:.2f} mm  Amp24={h["amp24"]:.2f} mm  '
        f'(fit: t≥{_HARMONIC_CONV_SOD/3600.:.1f} h)',
        fontsize=11, fontweight='bold')

    lr, lo, _ = _lla(np.array([1337935.5599, 6070317.2377, 1427877.5071]))
    Re = _enu(lr, lo)
    dx_all  = np.array([r['dx'] for _, r in sorted(m_raw.items())])
    e_mm    = (Re @ dx_all.T).T[:, 0] * 1e3
    n_mm    = (Re @ dx_all.T).T[:, 1] * 1e3

    rms_u_base = float(np.sqrt(np.mean(h['u_base_mm'][h['post_mask']] ** 2)))
    rms_u_fix  = float(np.sqrt(np.mean(h['u_fixed_mm'][h['post_mask']] ** 2)))
    rms_3_base = float(np.sqrt(np.mean(
        (e_mm[h['post_mask']] ** 2 + n_mm[h['post_mask']] ** 2
         + h['u_base_mm'][h['post_mask']] ** 2))))
    rms_3_fix  = float(np.sqrt(np.mean(h['d3_fixed_mm'][h['post_mask']] ** 2)))

    ax_u.plot(sods_h, h['u_base_mm'],  color='#1f77b4', lw=0.8,
              label='Up baseline')
    ax_u.plot(sods_h, h['u_fixed_mm'], color='#ff7f0e', lw=0.8,
              label='Up − dual-harmonic')
    ax_u.plot(sods_h, h['u_corr_mm'],  color='#7f7f7f', lw=1.0,
              ls='--', label='u_corr(t) = 12h+24h fit')
    ax_u.axvline(_HARMONIC_CONV_SOD / 3600., color='#2ca02c', ls=':',
                 label='Fit start')
    ax_u.set_ylabel('Up Error (mm)')
    ax_u.set_title(f'(a) Up: baseline vs dual-harmonic corrected  '
                   f'[RMS {rms_u_base:.1f}→{rms_u_fix:.1f} mm]')
    ax_u.legend(loc='upper right', fontsize=8)
    ax_u.grid(True, alpha=0.3)

    ax_3.plot(sods_h, np.sqrt(e_mm**2 + n_mm**2 + h['u_base_mm']**2),
              color='#1f77b4', lw=0.8, label='3D baseline')
    ax_3.plot(sods_h, h['d3_fixed_mm'], color='#ff7f0e', lw=0.8,
              label='3D − dual-harmonic')
    ax_3.axhline(100., color='#7f7f7f', ls='--', lw=0.8, label='10 cm')
    ax_3.axhline(50.,  color='#bcbd22', ls=':',  lw=0.8, label='5 cm')
    ax_3.set_ylabel('3D Error (mm)')
    ax_3.set_xlabel('Time (h)')
    ax_3.set_title(f'(b) 3D: baseline vs dual-harmonic corrected  '
                   f'[RMS {rms_3_base:.1f}→{rms_3_fix:.1f} mm]')
    ax_3.legend(loc='upper right', fontsize=8)
    ax_3.grid(True, alpha=0.3)

    plt.tight_layout()
    png_path = os.path.join(outdir, 'ppp_dual_harmonic.png')
    try:
        fig.savefig(png_path, dpi=150, bbox_inches='tight')
        print(f'[DUAL_HARM] Plot saved: {png_path}')
    except Exception as e:
        print(f'[DUAL_HARM] Plot save failed: {e}')
    plt.close(fig)


def _plot_subdaily_absorber(fwd_base, fwd_sda, all_fwd, REF, outdir=None):
    """Plot Run F: baseline vs subdaily-absorber forward pass."""
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('[SDA_PLOT] matplotlib not available — skipping'); return

    if outdir is None:
        outdir = os.path.dirname(os.path.abspath(__file__))

    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)

    def _up(fwd):
        items = sorted(fwd.items())
        sods = np.array([s for s, _ in items])
        dx   = np.array([r['dx'] for _, r in items])
        return sods, (Re @ dx.T).T[:, 2] * 1e3

    s_b, u_b = _up(fwd_base)
    s_s, u_s = _up(fwd_sda)
    hrs_b, hrs_s = s_b / 3600., s_s / 3600.

    # v_load state from sda pass
    v_load_mm = np.array([r.get('v_load', 0.) * 1e3
                          for _, r in sorted(fwd_sda.items())])

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    fig.suptitle('PPP-AR v93 — Run F: Subdaily Loading Absorber  (GPS+Galileo FWD)',
                 fontsize=12, fontweight='bold')

    pc_b = hrs_b >= 2.0; pc_s = hrs_s >= 2.0
    rms_b = float(np.sqrt(np.mean(u_b[pc_b]**2)))
    rms_s = float(np.sqrt(np.mean(u_s[pc_s]**2)))

    axes[0].plot(hrs_b, u_b, color='#1f77b4', lw=0.8, label=f'Baseline  RMS={rms_b:.1f} mm')
    axes[0].plot(hrs_s, u_s, color='#ff7f0e', lw=0.8, label=f'SDA pass  RMS={rms_s:.1f} mm')
    axes[0].set_ylabel('Up Error (mm)'); axes[0].grid(True, alpha=0.3)
    axes[0].set_title('(a) Up error: baseline vs subdaily-absorber pass')
    axes[0].legend(fontsize=8)

    axes[1].plot(hrs_s, v_load_mm, color='#2ca02c', lw=0.8)
    axes[1].set_ylabel('v_load (mm)'); axes[1].grid(True, alpha=0.3)
    axes[1].set_title('(b) Absorbed vertical loading state (v_load)')

    diff_up = u_s - np.interp(s_s, s_b, u_b)
    axes[2].plot(hrs_s, diff_up, color='#9467bd', lw=0.8)
    axes[2].axhline(0, color='k', lw=0.5)
    axes[2].set_ylabel('ΔUp (mm)'); axes[2].set_xlabel('Time (h)')
    axes[2].grid(True, alpha=0.3)
    axes[2].set_title('(c) ΔUp = SDA − Baseline (positive = SDA larger error)')

    plt.tight_layout()
    png_path = os.path.join(outdir, 'ppp_subdaily_absorber.png')
    try:
        fig.savefig(png_path, dpi=150, bbox_inches='tight')
        print(f'[SDA_PLOT] Plot saved: {png_path}')
    except Exception as e:
        print(f'[SDA_PLOT] Plot save failed: {e}')
    plt.close(fig)


    # ── multi-run overlay (only when all_fwd is supplied) ────────────────────
    if all_fwd is None:
        return  # baseline + SDA comparison is complete; nothing more to plot

    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("[PLOT] matplotlib not available"); return
    colors={'GPS-only':'#e6194b','Galileo-only':'#4363d8','GPS+Galileo':'#3cb44b'}
    fig,axes=plt.subplots(2,2,figsize=(14,10))
    fig.suptitle('PPP-AR Multi-Constellation (v98) — Paired Knockout G07+E03',
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
    # all_rts is not passed into this function; panel (d) shows FWD only
    mr=None
    if mf: ax.plot(mf['sods']/3600.,mf['d3_mm'],color='#4363d8',linewidth=0.8,
                   alpha=0.8,label='FWD')
    ax.axhline(50,color='gray',linestyle='--',linewidth=0.7)
    ax.set_xlabel('Time (h)'); ax.set_ylabel('3D Error (mm)')
    ax.set_title('(d) FWD — GPS+Galileo')
    ax.set_ylim(0,300); ax.legend(fontsize=8); ax.grid(True,alpha=0.3)
    plt.tight_layout()
    plot_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'ppp_comparison.png')
    try:
        fig.savefig(plot_path,dpi=150,bbox_inches='tight')
        print(f"[PLOT] Saved: {plot_path}")
    except Exception as e:
        print(f"[PLOT] Could not save: {e}")
    plt.close(fig)

    # ── 12-h harmonic vertical absorber (diagnostic) ──────────────────────────
    if ENABLE_12H_HARMONIC:
        m_gg = _compute_metrics(all_fwd.get('GPS+Galileo', {}), REF)
        if m_gg is None:
            print("[HARMONIC] No GPS+Galileo FWD data — skipping.")
        else:
            h = _fit_12h_harmonic(m_gg, conv_sod=_HARMONIC_CONV_SOD)
            if h is None:
                print(f"[HARMONIC] Skipped: fewer than 30 epochs after "
                      f"t={_HARMONIC_CONV_SOD/3600.:.1f} h.")
            else:
                # ── console report ────────────────────────────────────────────
                print(f"\n[HARMONIC] 12-h vertical harmonic fit"
                      f" (GPS+Galileo FWD, post-convergence t≥{_HARMONIC_CONV_SOD/3600.:.1f} h,"
                      f" n={h['n_fit']} epochs)")
                print(f"[HARMONIC]   A  (sin coeff) = {h['A']:+.3f} mm")
                print(f"[HARMONIC]   B  (cos coeff) = {h['B']:+.3f} mm")
                print(f"[HARMONIC]   Amplitude      = {h['amplitude']:.3f} mm")
                peak_sod = (math.atan2(h['A'], h['B']) % (2.*math.pi)) / (2.*math.pi) * _HARMONIC_PERIOD
                print(f"[HARMONIC]   Peak at SOD    = {peak_sod:.0f} s  "
                      f"({peak_sod/3600.:.2f} h)")
                # RMS improvement in post-convergence window
                u_post_base = h['u_base_mm'][h['post_mask']]
                u_post_fix  = h['u_fixed_mm'][h['post_mask']]
                d3_post_base = np.sqrt(
                    m_gg['e_mm'][h['post_mask']]**2 +
                    m_gg['n_mm'][h['post_mask']]**2 +
                    u_post_base**2)
                d3_post_fix  = h['d3_fixed_mm'][h['post_mask']]
                rms_u_base = math.sqrt(float(np.mean(u_post_base**2)))
                rms_u_fix  = math.sqrt(float(np.mean(u_post_fix**2)))
                rms_3d_base = math.sqrt(float(np.mean(d3_post_base**2)))
                rms_3d_fix  = math.sqrt(float(np.mean(d3_post_fix**2)))
                print(f"[HARMONIC]   RMS Up  baseline={rms_u_base:.1f} mm  "
                      f"corrected={rms_u_fix:.1f} mm  "
                      f"Δ={rms_u_base-rms_u_fix:+.1f} mm")
                print(f"[HARMONIC]   RMS 3D  baseline={rms_3d_base:.1f} mm  "
                      f"corrected={rms_3d_fix:.1f} mm  "
                      f"Δ={rms_3d_base-rms_3d_fix:+.1f} mm")

                # ── harmonic diagnostic figure ─────────────────────────────────
                fig2, (ax_u, ax_3) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
                fig2.suptitle(
                    f'PPP-AR v91 — 12-h Harmonic Vertical Absorber'
                    f'  (GPS+Galileo FWD)\n'
                    f'A={h["A"]:+.2f} mm  B={h["B"]:+.2f} mm  '
                    f'Amplitude={h["amplitude"]:.2f} mm  '
                    f'(fit: t≥{_HARMONIC_CONV_SOD/3600.:.1f} h)',
                    fontsize=11, fontweight='bold')

                sh = h['sods'] / 3600.

                # — Up panel —
                ax_u.plot(sh, h['u_base_mm'], color='#4363d8', linewidth=0.8,
                          alpha=0.85, label='Up baseline')
                ax_u.plot(sh, h['u_fixed_mm'], color='#f58231', linewidth=0.9,
                          alpha=0.90, label='Up – harmonic')
                ax_u.plot(sh, h['u_corr_mm'], color='#808080', linewidth=0.7,
                          linestyle='--', alpha=0.70,
                          label=f'u_corr(t)  A·sin+B·cos')
                ax_u.axhline(0,   color='black', linewidth=0.5)
                ax_u.axvline(_HARMONIC_CONV_SOD / 3600., color='#2ca02c',
                             linewidth=0.9, linestyle=':', label='Fit start')
                ax_u.set_ylabel('Up Error (mm)')
                ax_u.set_title('(a) Up: baseline vs harmonic-corrected  '
                               f'[RMS {rms_u_base:.1f}→{rms_u_fix:.1f} mm]')
                ax_u.set_ylim(-350, 350)
                ax_u.legend(fontsize=8, loc='upper right')
                ax_u.grid(True, alpha=0.3)

                # — 3D panel —
                ax_3.plot(sh, m_gg['d3_mm'], color='#4363d8', linewidth=0.8,
                          alpha=0.85, label='3D baseline')
                ax_3.plot(sh, h['d3_fixed_mm'], color='#f58231', linewidth=0.9,
                          alpha=0.90, label='3D – harmonic')
                ax_3.axhline(100, color='gray', linestyle='--',
                             linewidth=0.7, label='10 cm')
                ax_3.axhline(50,  color='gray', linestyle=':',
                             linewidth=0.7, label='5 cm')
                ax_3.axvline(_HARMONIC_CONV_SOD / 3600., color='#2ca02c',
                             linewidth=0.9, linestyle=':')
                ax_3.set_xlabel('Time (h)')
                ax_3.set_ylabel('3D Error (mm)')
                ax_3.set_title('(b) 3D: baseline vs harmonic-corrected  '
                               f'[RMS {rms_3d_base:.1f}→{rms_3d_fix:.1f} mm]')
                ax_3.set_ylim(0, 500)
                ax_3.legend(fontsize=8, loc='upper right')
                ax_3.grid(True, alpha=0.3)

                plt.tight_layout()
                harm_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    'ppp_harmonic.png')
                try:
                    fig2.savefig(harm_path, dpi=150, bbox_inches='tight')
                    print(f"[HARMONIC] Plot saved: {harm_path}")
                except Exception as e:
                    print(f"[HARMONIC] Plot save failed: {e}")
                plt.close(fig2)

    # ── ZWD↔Up coupling audit ─────────────────────────────────────────────────
    if ENABLE_ZWD_AUDIT:
        fwd_gg = all_fwd.get('GPS+Galileo', {})
        if not fwd_gg:
            print('[ZWD_AUDIT] No GPS+Galileo FWD data — skipping.')
        else:
            _audit_outdir = os.path.dirname(os.path.abspath(__file__))
            _zwd_up_audit(fwd_gg, REF, outdir=_audit_outdir)


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
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
def _proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx,
          blq=None,sta='IISC',tow_total=0.):
    """GPS satellite — dynamic signal detection from RINEX obs.

    v68 FIX: Dynamically detect actual L1/L2 code and phase signals present in
    RINEX.  Apply OSBs ONLY to the matching signal type so that the OSB
    reference frame is consistent with the observable.
    """
    # v77 PART 1–3: Priority-based GPS signal selection with type-consistent pairing.
    # PRIMARY  (preferred): C1W+L1W  /  C2W+L2W
    # SECONDARY (fallback): C1C+L1C  /  C2L+L2L
    # Code and phase MUST match within each frequency — no cross-type mixing.
    # If neither complete set is available, skip satellite entirely.

    no_AR = False   # set True when OSBs are absent for the chosen signal set

    # --- PRIMARY: W signals ---
    _P1W = so.get('C1W', 0.); _L1W = so.get('L1W', 0.)
    _P2W = so.get('C2W', 0.); _L2W = so.get('L2W', 0.)
    if _P1W != 0. and _L1W != 0. and _P2W != 0. and _L2W != 0.:
        code1_type, code2_type, phase1_type, phase2_type = 'C1W', 'C2W', 'L1W', 'L2W'
        P1_val, P2_val, L1_val, L2_val = _P1W, _P2W, _L1W, _L2W
    else:
        # --- SECONDARY: C1C/L1C + C2W/L2W (v79 PART 2: consistent pair only) ---
        # DO NOT mix C1C with C2L — OSB keys must match the obs signal types.
        # GPS OSB files typically carry C2W, not C2L; using C2W here ensures
        # the code-bias lookup key matches the OSB catalogue entry.
        _P1C = so.get('C1C', 0.); _L1C = so.get('L1C', 0.)
        _P2Ws = so.get('C2W', 0.); _L2Ws = so.get('L2W', 0.)
        if _P1C != 0. and _L1C != 0. and _P2Ws != 0. and _L2Ws != 0.:
            code1_type, code2_type, phase1_type, phase2_type = 'C1C', 'C2W', 'L1C', 'L2W'
            P1_val, P2_val, L1_val, L2_val = _P1C, _P2Ws, _L1C, _L2Ws
        else:
            return None   # neither W-pair nor C1C/C2W signal set complete — skip satellite

    if sid not in _sat_signal_map:
        _sat_signal_map[sid] = (code1_type, code2_type, phase1_type, phase2_type)

    P1=P1_val; P2=P2_val; L1=L1_val; L2=L2_val

    ob=osb.get(sid,{})
    # v68 FIX: Apply OSBs ONLY to the signal type that was actually observed.
    # Mismatch between obs signal and OSB signal type causes ~0.25–0.5 cyc residual.
    b_C1 = ob.get(code1_type, 0.)
    b_C2 = ob.get(code2_type, 0.)
    b_L1 = ob.get(phase1_type, 0.)
    b_L2 = ob.get(phase2_type, 0.)
    # v77 PART 4 / v79 PART 1: if ANY OSB key is absent → disable AR (sat stays in filter).
    ar_skip_reason = None
    if (code1_type not in ob or code2_type not in ob or
            phase1_type not in ob or phase2_type not in ob):
        no_AR = True
        ar_skip_reason = 'no_osb'

    # v79 PART 3: reject out-of-range OSB values.
    # Corrupted / mismatched OSB entries produce |code_bias| > 10 m or
    # |phase_bias| > 1 m — physically impossible for a healthy satellite.
    # Keep satellite in filter; only NL fixing is disabled.
    if abs(b_C1) > 10.0 or abs(b_C2) > 10.0:
        no_AR = True
        ar_skip_reason = 'bad_bias'
    if abs(b_L1) > 1.0 or abs(b_L2) > 1.0:
        no_AR = True
        ar_skip_reason = 'bad_bias'

    if sid not in _osb_dbg_printed:
        _osb_dbg_printed.add(sid)
        print(f"[OSB_DBG] {sid} sig=({code1_type}/{phase1_type},{code2_type}/{phase2_type})"
              f" code1={b_C1:+.4f}m code2={b_C2:+.4f}m"
              f" phase1={b_L1:+.6f}m phase2={b_L2:+.6f}m"
              f" no_AR={no_AR} reason={ar_skip_reason}")

    P1c = P1 - b_C1
    P2c = P2 - b_C2

    # RAW phase observables converted to metres, then phase OSB removed
    lam1 = LAMBDA1
    lam2 = LAMBDA2
    L1m  = L1 * lam1 - b_L1
    L2m  = L2 * lam2 - b_L2

    # ── v81 OSB CONSISTENCY DEBUG ─────────────────────────────────────────────
    # [OSB_VAL] prints b_code vs b_phase magnitudes once per satellite.
    #   Critical check:  |b_C1 - b_L1| should be < ~2 m.
    #   If it is 3–5 m that offset IS the 3–5 m L1m-P1c error.
    if sid not in _osb_once:
        print(f"[OSB_VAL] {sid} "
              f"b_code_L1={b_C1:+.3f}m "
              f"b_phase_L1={b_L1:+.3f}m "
              f"diff={b_C1 - b_L1:+.3f}m")
        _osb_once.add(sid)
    # [OSB_CHECK] tracks (L1m - P1c) stability per satellite over the last 100 epochs.
    #   ✅ range < 1.0 m  → OSBs applied correctly (smooth ionosphere + ambiguity).
    #   ❌ range 2–5 m    → OSB not applied / wrong sign / unit error.
    _diff_corr_gps = L1m - P1c
    _diff_raw_gps  = L1 * lam1 - P1       # before OSB, for reference
    if sid not in _cp_debug:
        _cp_debug[sid] = []
    _cp_debug[sid].append(_diff_corr_gps)
    if len(_cp_debug[sid]) > 100:
        _cp_debug[sid].pop(0)
    if len(_cp_debug[sid]) >= 20:
        _rng = max(_cp_debug[sid]) - min(_cp_debug[sid])
        _mean = sum(_cp_debug[sid]) / len(_cp_debug[sid])
        if _rng > 1.5:   # only print when suspicious (avoids log spam)
            print(f"[OSB_CHECK] {sid} "
                  f"raw={_diff_raw_gps:+.3f} "
                  f"corr={_diff_corr_gps:+.3f} "
                  f"mean={_mean:+.3f} "
                  f"range={_rng:.3f}")
    # ── end v81 OSB CONSISTENCY DEBUG ─────────────────────────────────────────

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
                no_AR=no_AR,            # v77: False=AR eligible, True=skip AR
                ar_skip_reason=ar_skip_reason)  # v79: 'no_osb' | 'bad_bias' | None

def _proc_gal(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx,
              blq=None,sta='IISC',tow_total=0.):
    """Galileo satellite — E1 + E5a, dynamic signal detection.

    v68 FIX: Dynamically detect actual E1/E5a code and phase signals present in
    RINEX.  Apply OSBs ONLY to the matching signal type.
    """
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
        print(f"[OSB_DBG] {sid} code1={code1_type}({b_C1:+.4f}m) code2={code2_type}({b_C5:+.4f}m)"
              f" phase1={phase1_type}({b_L1:+.6f}m) phase2={phase2_type}({b_L5:+.6f}m)"
              f" no_AR={no_AR} reason={ar_skip_reason}")

    P1c = P1 - b_C1
    P2c = P5 - b_C5

    # RAW phase observables converted to metres, then phase OSB removed
    lam1 = LAMBDA_E1
    lam2 = LAMBDA_E5A
    L1m  = L1 * lam1 - b_L1
    L2m  = L5 * lam2 - b_L5

    # ── v81 OSB CONSISTENCY DEBUG (Galileo) ──────────────────────────────────
    if sid not in _osb_once:
        print(f"[OSB_VAL] {sid} "
              f"b_code_L1={b_C1:+.3f}m "
              f"b_phase_L1={b_L1:+.3f}m "
              f"diff={b_C1 - b_L1:+.3f}m")
        _osb_once.add(sid)
    _diff_corr_gal = L1m - P1c
    _diff_raw_gal  = L1 * lam1 - P1
    if sid not in _cp_debug:
        _cp_debug[sid] = []
    _cp_debug[sid].append(_diff_corr_gal)
    if len(_cp_debug[sid]) > 100:
        _cp_debug[sid].pop(0)
    if len(_cp_debug[sid]) >= 20:
        _rng = max(_cp_debug[sid]) - min(_cp_debug[sid])
        _mean = sum(_cp_debug[sid]) / len(_cp_debug[sid])
        if _rng > 1.5:
            print(f"[OSB_CHECK] {sid} "
                  f"raw={_diff_raw_gal:+.3f} "
                  f"corr={_diff_corr_gal:+.3f} "
                  f"mean={_mean:+.3f} "
                  f"range={_rng:.3f}")
    # ── end v81 OSB CONSISTENCY DEBUG ────────────────────────────────────────

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
#  PPP Kalman filter pass
# ==============================================================================
def _ppp_pass(epochs,sp3t,sp,sc,clkd,osb,ah,nom,iclk,izwd,lat0,doy,zhd,tref,
              satx,att,recx,elm=math.radians(10.),SC=0.30,SP=0.010,
              direction=1,label="FWD",wl_init=None,amb_init=None,
              constellation='GE',blq=None,sta='IISC'):
    """
    constellation : 'G' | 'E' | 'GE'
    blq           : dict from parse_blq (ocean tide loading)
    sta           : 4-char station code used to look up BLQ entry
    """
    REF=np.array([1337935.5599,6070317.2377,1427877.5071])
    global _osb_dbg_printed, _sat_signal_map, _cp_debug, _osb_once
    _osb_dbg_printed = set()
    _sat_signal_map  = {}  # v70: reset signal lock at start of each pass
    _cp_debug        = {}  # v81: per-sat L1m-P1c history (OSB consistency debug)
    _osb_once        = set()  # v81: [OSB_VAL] printed-once guard
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
    # Per-sat: [I_s(5+3k), N1_s(6+3k), N2_s(7+3k)]  for satellite k
    # 3 states per satellite instead of 1 IF ambiguity
    x=np.zeros(5); x[3]=iclk; x[4]=izwd
    P=np.zeros((5,5))
    P[0,0]=P[1,1]=P[2,2]=100.**2; P[3,3]=3000.**2; P[4,4]=0.5**2

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

    # v54: Phase 2 debug — track L1m−P1c and L2m−P2c for one reference sat
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
        Q[4,4]=2.5e-9*dt
        # v54 RAW per-satellite state noise:
        #   I (ionosphere): small process noise to allow slow drift
        #   N1, N2 (ambiguities): zero process noise (carrier phase constants)
        # v56 FIX 1 (CRITICAL): q_iono increased 100× for equatorial station IISC.
        # IISC is inside the EIA; L1 STEC varies 20–50 m per pass.
        # With 1e-6/s the ionosphere froze after ~50 epochs (P[ki,ki]→0.02 m²);
        # position absorbed the unfrozen residual.  1e-4/s gives σ_I_ss≈0.34 m,
        # enough bandwidth to track equatorial rate ≈ 0.05–0.5 m/epoch.
        # For extreme scintillation epochs, raise to ION_PROC_NOISE=1e-3.
        ION_PROC_NOISE = 1e-5              # m²/s — v70 IONO FIX 1: reduced to 1e-5 to limit iono random walk and N1 bleed-through
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
                m=_proc_gal(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,
                             satx,att,recx,blq=blq,sta=sta,tow_total=tow_total)
            else:
                m=_proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,
                         satx,att,recx,blq=blq,sta=sta,tow_total=tow_total)
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
                        if n_hist in (15,20,30,50,100):
                            print(f"[WL CHECK] {sid} n={n_hist} std={sd:.3f} "
                                  f"res={residual:.3f} b_rec={b_rec:+.3f}({tag})")
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
            geom.append(m)
            _sat_lam1[sid] = m['lam1']   # v80 PART 3: cache lam1 for next epoch's Q loop

        if len(geom)<4: continue
        if len(geom)>4:
            if _pdop(geom)>6.0:
                worst=min(geom,key=lambda m:m['el'])
                geom=[m for m in geom if m['sid']!=worst['sid']]
        if len(geom)<4: continue

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
            Rd[r]=_sig(m['el'],SC)**2

            # ── P2 row: P2c = rp + γ·I ───────────────────────────────────
            r=base+1
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]; H[r,3]=1.; H[r,4]=mw
            H[r,ki]=gam                         # +γI
            z[r]=m['P2c']-(rp+gam*I_s)
            # v56 FIX 2: P2 noise = same as P1 (both P-code; γ² scaling removed).
            # γ² was reducing P2's ionosphere SNR by γ, slowing I convergence.
            Rd[r]=_sig(m['el'],SC)**2

            # ── L1 row: L1mc = rp − I + λ1·N1 ───────────────────────────
            r=base+2
            H[r,0]=-u[0]; H[r,1]=-u[1]; H[r,2]=-u[2]; H[r,3]=1.; H[r,4]=mw
            H[r,ki]=-1.                         # −I
            H[r,kn1]=lam1                       # +λ1·N1 (cycle → metres)
            pred_L1=rp - I_s + lam1*N1_s
            z[r]=m['L1mc']-pred_L1
            phase_sig=_sig(m['el'],SP)*(5. if m.get('age',99)<=3 else 1.)
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
            phase_sig2=_sig(m['el'],SP)*(5. if m.get('age',99)<=3 else 1.)
            Rd[r]=phase_sig2**2
            if abs(z[r])>PHASE_RES_GATE: Rd[r]=max(Rd[r], 1.0**2)

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
            # v72: ionosphere variance cap — applied unconditionally after every
            # KF update.  Bounds σ_I < ~5 m without touching x[ki], process noise,
            # the measurement model, or any other filter state.
            P[_ki_c, _ki_c] = min(P[_ki_c, _ki_c], 100.0)
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
            # v79 PART 6 — per-sat range consistency + NL status summary
            for _sid_dbg in sorted(sidx.keys()):
                _hist_dbg = _lp1_hist.get(_sid_dbg)
                _rng_dbg = 0.0
                if _hist_dbg and len(_hist_dbg) >= 20:
                    _arr_dbg = np.array(_hist_dbg)
                    _rng_dbg = float(_arr_dbg.max() - _arr_dbg.min())
                _no_ar_dbg = next((m.get('no_AR', False) for m in geom if m['sid'] == _sid_dbg), False)
                _ar_rsn_dbg = next((m.get('ar_skip_reason') for m in geom if m['sid'] == _sid_dbg), None)
                if _sid_dbg in nl_fixed:
                    _nl_status = "NL_FIXED"
                elif _no_ar_dbg:
                    _nl_status = f"SKIP_OSB({_ar_rsn_dbg})"
                elif _rng_dbg > 6.0:
                    _nl_status = f"SKIP_range({_rng_dbg:.1f}m>6.0)"
                else:
                    _nl_status = "eligible"
                print(f"[v79 DBG] SOD={sod:.0f} sat={_sid_dbg} "
                      f"range_100={_rng_dbg:.2f}m  NL={_nl_status}")

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
            nl_fixed.pop(sid_nl,None)
            _nl_fix_cooldown[sid_nl] = 30   # v80 PART 4: 30-epoch re-fix cooldown

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
            if _rps > 6.0: continue
            _ki_ps = sidx[_sps]
            _sig_ps = math.sqrt(max(0.0, P[_ki_ps+1, _ki_ps+1])) * _mps['lam1']
            _nl_preselect.append((_sig_ps, _sps))
        _nl_preselect.sort(key=lambda t: t[0])   # ascending sigma_N1_m
        _nl_eligible_sids = {sid for _, sid in _nl_preselect[:4]}   # best 4 only

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
            if _rng_sat > 6.0:
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

            # v78 PART 1+2 / v79 PART 4 — STRICT SIGMA GATE:
            # Hard block: sigma_N1_m must be < 0.12 m (instruction requirement).
            if sigma_N1_m > 0.12:
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
            if sigma_N1_m >= 0.12:
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
                if abs(_innov_nl) > 0.35:
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
                    # v72 FIX (CRITICAL): re-apply iono variance cap after NL
                    # pseudo-obs update.  The NL filter_standard call modifies P,
                    # so without this second cap the iono variance can explode
                    # during the NL constraint phase (late-arc explosion).
                    # This is the ONLY place P is modified after the primary cap.
                    for _sid_nl_c, _ki_nl_c in sidx.items():
                        P[_ki_nl_c, _ki_nl_c] = min(P[_ki_nl_c, _ki_nl_c], 100.0)
                    # v78 PATCH 3 — Ionosphere stabilization (very light damping).
                    # Nudges iono states 2 % toward zero each NL update epoch,
                    # countering slow UP drift without touching P.
                    for ki in sidx.values():
                        x[ki] = 0.98 * x[ki]
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
                  f"  skipped_innov={reject_due_to_innov}")
            # Reset window accumulators after each 300-epoch print
            _nl_skip_no_osb      = 0
            _nl_skip_bad_bias    = 0
            _nl_skip_high_range  = 0
            _nl_skip_sigma_accum = 0
            _nl_count_accum      = 0
            reject_due_to_innov  = 0
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
    colors={'GPS-only':'#e6194b','Galileo-only':'#4363d8','GPS+Galileo':'#3cb44b'}
    fig,axes=plt.subplots(2,2,figsize=(14,10))
    fig.suptitle('PPP-AR Multi-Constellation (v80) — NL Stability: Best-4, Tighter Constraint, Cooldown',
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
    ]]
    postpos(None,None,0.,0.,PrcOpt(),SolOpt(),FilOpt(),
            INFILES,os.path.join(DATA,'ppp_results3.csv'))
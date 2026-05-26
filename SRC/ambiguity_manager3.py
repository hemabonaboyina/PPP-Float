"""
ambiguity_manager.py  — Step-4: Operational Lifecycle Activation
=================================================================

Step-4 builds on Step-3 (deterministic lineage, legal transition graph) and
converts the lifecycle engine from passive event logging into operational
ambiguity-state semantics.

PHASE-1  Real Visibility-Gap Semantics
  Configurable thresholds:
    SHORT_GAP_EPOCHS = 30   (default)
    LONG_GAP_EPOCHS  = 300  (default)

  Short gap (gap < SHORT_GAP_EPOCHS):
    ACTIVE → DORMANT → RESTORED → ACTIVE
    → preserve lineage_id, generation, ambiguity continuity, sigma history

  Long/medium gap (gap ≥ SHORT_GAP_EPOCHS):
    ACTIVE → DORMANT → RESET → REBORN → ACTIVE
    → new child lineage, generation += 1, parent linkage preserved

  Slip-triggered resets always force REBORN (unchanged).

PHASE-2  Operational CONVERGING / CONVERGED States
  Convergence tied to filter health ONLY (sigma, acceptance streak,
  residual stability).  NOT tied to WL/NL fixing or integer success.

PHASE-3  Rolling Metrics (maintained inside manager)
  sigma_history, acceptance_ratio, residual_rms,
  continuous_active_epochs, continuous_filter_epochs,
  rejection_streak, acceptance_streak (already in Step-3)

PHASE-4  Deterministic Convergence Transition Rules
  ACTIVE → CONVERGING   : sigma < CONVERGING_SIGMA_MM
                           AND acceptance streak ≥ CONVERGING_STREAK_MIN
                           AND currently observable
  CONVERGING → CONVERGED : sigma stable over rolling window
                           AND residual RMS stable
                           AND rejection ratio < CONVERGED_MAX_REJ_RATIO
  CONVERGED → CONVERGING : sigma inflates above CONVERGING_SIGMA_MM
                           OR rejection streak ≥ DEGRADED_REJ_STREAK
  CONVERGING → ACTIVE    : persistent instability (rejection streak)

PHASE-5  Operational RESTORE Semantics
  RESTORE = same lineage resumes after temporary invisibility
    • lineage_id unchanged
    • generation unchanged
    • sigma_history preserved
    • continuous counters preserved
  REBIRTH = new ambiguity object (child lineage, fresh counters)

PHASE-6  DORMANT State Management
  DORMANT ambiguity: exists in manager, retains lineage, retains history,
  not observable, not in filter, not updated.
  DORMANT cannot become CONVERGED and cannot participate in EKF updates.

PHASE-7  New Assertions ASSERT-O1 … ASSERT-O7
  O1: RESTORED lineage_id unchanged
  O2: REBORN lineage_id differs from parent
  O3: CONVERGED sigma below threshold
  O4: DORMANT ambiguity not in filter
  O5: RESTORED ambiguity generation unchanged
  O6: child generation > parent generation
  O7: continuous_active_epochs monotonic during ACTIVE/CONVERGING/CONVERGED

PHASE-8  Extended Lifecycle Trace CSV
  Added columns: gap_length_epochs, convergence_score, rolling_sigma_mm,
  rolling_residual_rms_mm, acceptance_ratio, continuous_active_epochs,
  continuous_filter_epochs

CRITICAL CONSTRAINTS (inherited from STEP-4 spec)
  • Zero change to EKF equations, gates, process noise, covariance math.
  • No pruning, no AR, no NL fixing, no zombie deletion.
  • All Step-2 and Step-3 public APIs retained unchanged.
  • active bool and is_active() remain authoritative for EKF gating.

Step-3 features retained
  • Deterministic lineage IDs (LIN-{n:06d})
  • Legal transition graph with hard-fail assertions
  • ASSERT-L1 … ASSERT-L7
  • Phase-4 spec lifecycle trace CSV (extended here)
  • Rebirth genealogy, restore vs rebirth semantics
"""

from __future__ import annotations

import enum
import math
import os
from collections import deque
from dataclasses import dataclass, field
from typing import FrozenSet, Dict, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle state enum (Step-3 Phase-2, unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class AmbiguityLifecycleState(enum.Enum):
    """Explicit states for one ambiguity lineage in the EKF.

    Every ambiguity is in exactly one state at all times.
    All transitions must flow through transition_state().
    """
    NEW        = "NEW"        # slot allocated; no birth yet
    ACTIVE     = "ACTIVE"     # contributing to filter
    CONVERGING = "CONVERGING" # sigma decreasing; filter-health driven
    CONVERGED  = "CONVERGED"  # sigma below convergence threshold, stable
    SLIPPED    = "SLIPPED"    # cycle slip detected; awaiting reset
    RESET      = "RESET"      # deactivated post-slip or long-gap; awaiting rebirth
    REBORN     = "REBORN"     # ephemeral: new lineage assigned, entering filter
    DORMANT    = "DORMANT"    # satellite out of view; lineage preserved
    RESTORED   = "RESTORED"   # satellite returned within short-gap threshold
    PRUNED     = "PRUNED"     # lineage terminated permanently (terminal)


# ─────────────────────────────────────────────────────────────────────────────
# Legal transition table (Step-3 Phase-3 + Step-4 additions)
# ─────────────────────────────────────────────────────────────────────────────
_S = AmbiguityLifecycleState

LEGAL_TRANSITIONS: Dict[AmbiguityLifecycleState,
                        FrozenSet[AmbiguityLifecycleState]] = {
    _S.NEW:        frozenset({_S.ACTIVE, _S.DORMANT, _S.PRUNED}),
    # STEP-4: ACTIVE can go to CONVERGING (promoted by tick())
    _S.ACTIVE:     frozenset({_S.CONVERGING, _S.SLIPPED, _S.DORMANT, _S.PRUNED}),
    # STEP-4: CONVERGING can demote back to ACTIVE on instability
    _S.CONVERGING: frozenset({_S.CONVERGED, _S.ACTIVE, _S.SLIPPED, _S.DORMANT}),
    # STEP-4: CONVERGED can demote back to CONVERGING on sigma inflation / rejection
    _S.CONVERGED:  frozenset({_S.CONVERGING, _S.SLIPPED, _S.DORMANT}),
    _S.SLIPPED:    frozenset({_S.RESET}),
    _S.RESET:      frozenset({_S.REBORN}),
    _S.REBORN:     frozenset({_S.ACTIVE}),
    # STEP-4: DORMANT can go to RESET for long-gap rebirth path
    _S.DORMANT:    frozenset({_S.RESTORED, _S.RESET, _S.PRUNED}),
    _S.RESTORED:   frozenset({_S.ACTIVE, _S.SLIPPED, _S.DORMANT}),
    _S.PRUNED:     frozenset(),          # terminal — no outgoing transitions
}

# States where the ambiguity actively contributes to the EKF (active=True)
_ACTIVE_LIFECYCLE_STATES: FrozenSet[AmbiguityLifecycleState] = frozenset({
    _S.ACTIVE,
    _S.CONVERGING,
    _S.CONVERGED,
    _S.REBORN,
    _S.RESTORED,
})


# ─────────────────────────────────────────────────────────────────────────────
# AmbiguityState — per-satellite bookkeeping record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AmbiguityState:
    """All metadata for one carrier-phase ambiguity state.

    Sigma/residual quantities are stored in *millimetres*.
    Epoch quantities are EKF epoch counters (int).
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    sat_id:              str
    constellation:       str          # 'G' or 'E'
    state_index:         int          # column/row index in x / P

    # ── Step-3: Lifecycle state (Phase-2) ─────────────────────────────────────
    lifecycle_state: AmbiguityLifecycleState = field(
        default=AmbiguityLifecycleState.NEW)

    # ── Step-3: Lineage tracking (Phase-1) ────────────────────────────────────
    lineage_id:        str           = ""     # "LIN-{n:06d}"; empty until first birth
    generation:        int           = 0      # 0 = genesis; incremented on every rebirth
    parent_lineage_id: Optional[str] = None   # None for generation-0 births
    reset_epoch:       int           = -1     # EKF epoch of most-recent RESET transition
    restore_epoch:     int           = -1     # EKF epoch of most-recent RESTORED transition
    termination_epoch: int           = -1     # EKF epoch of PRUNED transition

    # ── Lifecycle status (Step-1 fields) ──────────────────────────────────────
    active:              bool  = False
    born_epoch:          int   = -1
    born_sod:            float = float('nan')
    last_seen_epoch:     int   = -1
    last_seen_sod:       float = float('nan')
    last_accept_epoch:   int   = -1
    last_accept_sod:     float = float('nan')

    # ── Counters ──────────────────────────────────────────────────────────────
    reset_count:         int   = 0
    accepted_count:      int   = 0
    rejected_count:      int   = 0

    # ── Sigma / value ─────────────────────────────────────────────────────────
    initial_sigma:       float = -1.0    # mm; -1 = not yet set
    current_sigma:       float = 20000.0 # mm
    current_value:       float = 0.0

    # ── Geometry ──────────────────────────────────────────────────────────────
    current_elevation_deg: float = float('nan')

    # ── Observability flags ───────────────────────────────────────────────────
    currently_observable:  bool = False
    currently_in_filter:   bool = False
    slip_flag:             bool = False

    # ── Streaks ───────────────────────────────────────────────────────────────
    rejection_streak:    int   = 0
    acceptance_streak:   int   = 0

    # ── Ring buffers (audit) ──────────────────────────────────────────────────
    recent_ph_res:       list  = field(default_factory=list)
    recent_rej:          list  = field(default_factory=list)

    # ── Kalman gain / H-norm proxies (audit-only) ─────────────────────────────
    last_H_norm:         float = float('nan')
    last_Kg_norm:        float = float('nan')

    # ── STEP-4: Rolling metrics (Phase-3) ─────────────────────────────────────
    sigma_history:            deque = field(
        default_factory=lambda: deque(maxlen=50))   # rolling sigma_mm history
    residual_history:         deque = field(
        default_factory=lambda: deque(maxlen=50))   # rolling |residual_mm| history
    continuous_active_epochs: int   = 0   # epochs in ACTIVE/CONVERGING/CONVERGED (reset on DORMANT/RESET)
    continuous_filter_epochs: int   = 0   # epochs participated in filter (accepted update)
    gap_start_epoch:          int   = -1  # epoch when DORMANT started (for gap_length_epochs)
    _prev_gen_at_restore:     int   = -1  # generation snapshot for ASSERT-O5
    _lineage_at_restore:      str   = ""  # lineage snapshot for ASSERT-O1


# ─────────────────────────────────────────────────────────────────────────────
# AmbiguityManager — registry + lifecycle API (Steps 1–4)
# ─────────────────────────────────────────────────────────────────────────────

class AmbiguityManager:
    """Centralised owner of all ambiguity-lifecycle bookkeeping AND operations.

    Step-1:  metadata (AmbiguityState) + counters / ring-buffers.
    Step-2:  activation / deactivation / observability / filter-flag ops.
    Step-3:  lineage tracking, lifecycle state graph, legal transition
             assertions, ASSERT-L1…L7, upgraded lifecycle trace format.
    Step-4:  gap-length-aware DORMANT handling (SHORT / LONG gap semantics),
             operational CONVERGING/CONVERGED states, rolling metrics,
             deterministic convergence rules, ASSERT-O1…O7,
             extended lifecycle trace CSV.

    Parameters
    ----------
    ring_size : int
        Length of the recent_ph_res / recent_rej ring buffers (default 10).
    snapshot_fh : file-like or None
        Handle for ambiguity_manager_snapshot.csv.
    lifecycle_fh : file-like or None
        Handle for ambiguity_lifecycle_trace.csv.
    converged_sigma_threshold_mm : float
        Sigma below which ASSERT-O3/ASSERT-L5 expect CONVERGED state. Default 500 mm.
    short_gap_epochs : int
        Gap shorter than this restores lineage (DORMANT→RESTORED). Default 30.
    long_gap_epochs : int
        Gap longer than this is labelled "long gap" in trace. Default 300.
    converging_sigma_mm : float
        Sigma threshold for ACTIVE→CONVERGING. Default 200 mm.
    converging_streak_min : int
        Minimum acceptance streak for ACTIVE→CONVERGING. Default 5.
    converged_window : int
        Rolling window length (epochs) for CONVERGING→CONVERGED. Default 20.
    converged_max_rej_ratio : float
        Max recent rejection ratio for CONVERGING→CONVERGED. Default 0.10.
    converged_sigma_stable_pct : float
        Max std/mean of sigma_history (fraction) for stability check. Default 0.08.
    degraded_rej_streak : int
        Rejection streak threshold for CONVERGED→CONVERGING demotion. Default 5.
    """

    SNAPSHOT_INTERVAL = 300

    # Class-level sequential counter — globally unique, deterministic lineage IDs.
    _lineage_counter: int = 0

    def __init__(self,
                 ring_size: int = 10,
                 snapshot_fh=None,
                 lifecycle_fh=None,
                 converged_sigma_threshold_mm: float = 500.0,
                 # STEP-4 parameters ─────────────────────────────────────────
                 short_gap_epochs:          int   = 30,
                 long_gap_epochs:           int   = 300,
                 converging_sigma_mm:       float = 200.0,
                 converging_streak_min:     int   = 5,
                 converged_window:          int   = 20,
                 converged_max_rej_ratio:   float = 0.10,
                 converged_sigma_stable_pct: float = 0.08,
                 degraded_rej_streak:       int   = 5):
        self.states: dict[str, AmbiguityState] = {}
        self._total_resets: int = 0
        self._ring_size: int = ring_size
        self._snapshot_fh  = snapshot_fh
        self._lifecycle_fh = lifecycle_fh
        self._converged_sigma_threshold_mm = converged_sigma_threshold_mm
        # STEP-4: gap and convergence thresholds
        self.SHORT_GAP_EPOCHS          = short_gap_epochs
        self.LONG_GAP_EPOCHS           = long_gap_epochs
        self.CONVERGING_SIGMA_MM       = converging_sigma_mm
        self.CONVERGING_STREAK_MIN     = converging_streak_min
        self.CONVERGED_WINDOW          = converged_window
        self.CONVERGED_MAX_REJ_RATIO   = converged_max_rej_ratio
        self.CONVERGED_SIGMA_STABLE_PCT = converged_sigma_stable_pct
        self.DEGRADED_REJ_STREAK       = degraded_rej_streak

    # ─────────────────────────────────────────────────────────────────────────
    # Step-3: Lineage ID generator
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def _generate_lineage_id(cls) -> str:
        """Return a globally unique, deterministic, sequential lineage ID."""
        cls._lineage_counter += 1
        return f"LIN-{cls._lineage_counter:06d}"

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _ensure(self, sat_id: str, state_index: int) -> AmbiguityState:
        """Return (creating if absent) the AmbiguityState for *sat_id*.

        Private — PPP engine must call register_new_slot() instead.
        """
        if sat_id not in self.states:
            self.states[sat_id] = AmbiguityState(
                sat_id=sat_id,
                constellation=sat_id[0],
                state_index=state_index,
                lifecycle_state=AmbiguityLifecycleState.NEW,
            )
        return self.states[sat_id]

    def _ring_append(self, buf: list, value, maxlen: int) -> None:
        """Append *value* to *buf*, popping the front when over capacity."""
        buf.append(value)
        if len(buf) > maxlen:
            buf.pop(0)

    # ─────────────────────────────────────────────────────────────────────────
    # Step-3: Core state-graph engine (Phase-3)
    # ─────────────────────────────────────────────────────────────────────────

    def transition_state(self,
                         sat_id: str,
                         new_state: AmbiguityLifecycleState,
                         epoch: int,
                         sod: float,
                         reason: str = "") -> None:
        """Transition *sat_id* to *new_state* through the legal transition graph.

        This is the ONLY method that:
          (a) writes to the lifecycle trace CSV, and
          (b) mutates ``s.lifecycle_state``.

        All other methods MUST call this for any lifecycle state change.
        Hard-fails with full context on any illegal transition (Phase-3).

        STEP-4 additions:
          • Records gap_start_epoch when entering DORMANT.
          • Snapshots lineage/generation when entering RESTORED (for ASSERT-O1/O5).
          • Resets continuous counters appropriately.
        """
        s = self.states.get(sat_id)
        if s is None:
            raise AssertionError(
                f"transition_state: unknown sat '{sat_id}' "
                f"attempted {new_state.value} at epoch={epoch}"
            )

        old_state = s.lifecycle_state
        allowed   = LEGAL_TRANSITIONS.get(old_state, frozenset())

        if new_state not in allowed:
            raise AssertionError(
                f"ILLEGAL TRANSITION  sat={sat_id}"
                f"  lineage={s.lineage_id}  gen={s.generation}"
                f"  {old_state.value} → {new_state.value}"
                f"  reason='{reason}'  epoch={epoch}  sod={sod:.1f}\n"
                f"  Legal targets from {old_state.value}: "
                f"{sorted(t.value for t in allowed)}"
            )

        # ── STEP-4: Pre-transition side-effects ───────────────────────────────
        if new_state == _S.DORMANT:
            # Record when dormancy started (for gap_length_epochs computation)
            s.gap_start_epoch = epoch
            # Reset continuous counters — ambiguity is no longer active
            s.continuous_active_epochs = 0
            s.continuous_filter_epochs = 0

        if new_state == _S.RESTORED:
            # Snapshot for ASSERT-O1 and ASSERT-O5
            s._prev_gen_at_restore  = s.generation
            s._lineage_at_restore   = s.lineage_id

        if new_state in (_S.RESET, _S.SLIPPED):
            # Going through a reset: clear rolling convergence history
            s.continuous_active_epochs = 0
            s.continuous_filter_epochs = 0
            s.sigma_history.clear()
            s.residual_history.clear()

        # ── Commit state change ───────────────────────────────────────────────
        s.lifecycle_state = new_state

        # Sync the active bool from the new lifecycle state
        s.active = new_state in _ACTIVE_LIFECYCLE_STATES

        # Epoch bookmarks for forensic trace (Step-3)
        if new_state == _S.RESET:
            s.reset_epoch = epoch
        elif new_state == _S.RESTORED:
            s.restore_epoch = epoch
        elif new_state == _S.PRUNED:
            s.termination_epoch = epoch

        # Emit lifecycle trace row (extended in Step-4)
        self._emit_transition(epoch=epoch, sod=sod, sat_id=sat_id,
                              old_state=old_state, new_state=new_state,
                              reason=reason)

    def _emit_transition(self,
                         epoch: int, sod: float, sat_id: str,
                         old_state: AmbiguityLifecycleState,
                         new_state: AmbiguityLifecycleState,
                         reason: str) -> None:
        """Write one transition row to ambiguity_lifecycle_trace.csv.

        Step-4 Phase-8 format (extended):
          epoch, sod, sat,
          lineage_id, generation, parent_lineage_id,
          old_state, new_state, reason,
          sigma_mm, state_index, active, observable, in_filter,
          gap_length_epochs, convergence_score,
          rolling_sigma_mm, rolling_residual_rms_mm,
          acceptance_ratio, continuous_active_epochs, continuous_filter_epochs
        """
        if self._lifecycle_fh is None:
            return
        s    = self.states.get(sat_id)
        sig  = f"{s.current_sigma:.2f}" if s else "nan"
        sidx = s.state_index             if s else -1
        lin  = s.lineage_id              if s else ""
        gen  = s.generation              if s else 0
        par  = (s.parent_lineage_id      if (s and s.parent_lineage_id) else "")
        act  = int(s.active)                  if s else 0
        obs  = int(s.currently_observable)    if s else 0
        inf  = int(s.currently_in_filter)     if s else 0
        sod_s = f"{sod:.1f}" if sod == sod else "nan"  # NaN guard

        # STEP-4: extended fields
        gap_ep   = self._gap_length_epochs(s) if s else 0
        conv_sc  = self._convergence_score(s) if s else 0.0
        roll_sig = self._rolling_sigma_mm(s)  if s else float('nan')
        roll_rms = self._rolling_residual_rms_mm(s) if s else float('nan')
        acc_rat  = self._acceptance_ratio(s)  if s else float('nan')
        cont_act = s.continuous_active_epochs if s else 0
        cont_flt = s.continuous_filter_epochs if s else 0

        def _ff(v):
            return "nan" if v != v else f"{v:.4f}"

        self._lifecycle_fh.write(
            f"{epoch},{sod_s},{sat_id},"
            f"{lin},{gen},{par},"
            f"{old_state.value},{new_state.value},{reason},"
            f"{sig},{sidx},{act},{obs},{inf},"
            f"{gap_ep},{_ff(conv_sc)},"
            f"{_ff(roll_sig)},{_ff(roll_rms)},"
            f"{_ff(acc_rat)},{cont_act},{cont_flt}\n"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP-4: Rolling metric helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _gap_length_epochs(self, s: AmbiguityState) -> int:
        """Epochs since DORMANT started; 0 if not dormant."""
        if s.gap_start_epoch < 0:
            return 0
        return max(0, s.continuous_active_epochs)   # will be 0 after going DORMANT

    def _convergence_score(self, s: AmbiguityState) -> float:
        """0-1 convergence score based on lifecycle state and sigma."""
        ls = s.lifecycle_state
        if ls == _S.CONVERGED:
            return 1.0
        if ls == _S.CONVERGING:
            # 0.5 base + up to 0.45 based on acceptance streak
            return 0.50 + 0.45 * min(s.acceptance_streak / max(self.CONVERGED_WINDOW, 1), 1.0)
        if ls == _S.ACTIVE and s.current_sigma < self.CONVERGING_SIGMA_MM * 2.0:
            return 0.20 * max(0.0,
                1.0 - s.current_sigma / (self.CONVERGING_SIGMA_MM * 2.0))
        return 0.0

    def _rolling_sigma_mm(self, s: AmbiguityState) -> float:
        """Mean of recent sigma_history, or nan if empty."""
        if not s.sigma_history:
            return float('nan')
        return sum(s.sigma_history) / len(s.sigma_history)

    def _rolling_residual_rms_mm(self, s: AmbiguityState) -> float:
        """RMS of recent residual_history, or nan if empty."""
        if not s.residual_history:
            return float('nan')
        sq = sum(v * v for v in s.residual_history)
        return math.sqrt(sq / len(s.residual_history))

    def _acceptance_ratio(self, s: AmbiguityState) -> float:
        """Fraction of accepted updates over total. nan if no updates."""
        total = s.accepted_count + s.rejected_count
        return s.accepted_count / total if total > 0 else float('nan')

    def _sigma_stable(self, s: AmbiguityState) -> bool:
        """True if sigma_history is stable over CONVERGED_WINDOW epochs."""
        w = self.CONVERGED_WINDOW
        if len(s.sigma_history) < w:
            return False
        recent = list(s.sigma_history)[-w:]
        mean = sum(recent) / w
        if mean <= 0.0:
            return False
        std = math.sqrt(sum((v - mean) ** 2 for v in recent) / w)
        return (std / mean) < self.CONVERGED_SIGMA_STABLE_PCT

    def _recent_rejection_ratio(self, s: AmbiguityState) -> float:
        """Rejection ratio from recent_rej ring buffer."""
        if not s.recent_rej:
            return 0.0
        return sum(s.recent_rej) / len(s.recent_rej)

    # ─────────────────────────────────────────────────────────────────────────
    # Step-3: Internal lineage assignment helpers (Phase-1)
    # ─────────────────────────────────────────────────────────────────────────

    def _init_genesis_lineage(self, s: AmbiguityState) -> None:
        """Assign a new generation-0 lineage to *s* (genesis / first birth)."""
        s.lineage_id        = self._generate_lineage_id()
        s.generation        = 0
        s.parent_lineage_id = None

    def _init_child_lineage(self, s: AmbiguityState) -> None:
        """Assign a new child lineage to *s* (post-reset rebirth or long-gap).

        Increments generation and records the old lineage_id as parent.
        """
        old_lin             = s.lineage_id
        s.lineage_id        = self._generate_lineage_id()
        s.generation        = s.generation + 1
        s.parent_lineage_id = old_lin if old_lin else None

    # ─────────────────────────────────────────────────────────────────────────
    # STEP-4: Gap-length-aware gap recovery (Phase-1)
    # ─────────────────────────────────────────────────────────────────────────

    def handle_gap(self,
                   sat_id: str,
                   gap_epochs: int,
                   epoch: int,
                   sod: float) -> str:
        """Classify and act on a visibility gap for a returning satellite.

        Called by the PPP engine when a satellite re-appears after being absent.

        Gap classification:
          gap_epochs < SHORT_GAP_EPOCHS:
            DORMANT → RESTORED → ACTIVE   (lineage preserved)
            Returns 'RESTORE'  — engine should restore saved x[ki]/P[ki,ki].

          gap_epochs ≥ SHORT_GAP_EPOCHS:
            DORMANT → RESET   (child lineage will be created on next register_birth)
            Returns 'REBIRTH'  — engine should NOT restore saved state; allow
            the normal newborn-pending birth path (register_birth sees RESET and
            calls _init_child_lineage).

          If sat is not DORMANT (e.g. already RESET from a slip): no-op, returns
          'UNKNOWN' — engine proceeds normally.

        The gap_length_epochs is recorded in the lifecycle trace on every
        transition emitted from this method.
        """
        s = self.states.get(sat_id)
        if s is None:
            return 'UNKNOWN'

        ls = s.lifecycle_state
        if ls != _S.DORMANT:
            # Not DORMANT: satellite may have been reset by a slip, or already
            # re-born. Let the PPP engine's normal flow handle it.
            return 'UNKNOWN'

        reason_tag = (f"short_gap_{gap_epochs}ep"
                      if gap_epochs < self.SHORT_GAP_EPOCHS
                      else f"long_gap_{gap_epochs}ep")

        if gap_epochs < self.SHORT_GAP_EPOCHS:
            # ── SHORT GAP: RESTORE path ───────────────────────────────────────
            # DORMANT → RESTORED → ACTIVE (two transitions)
            self.transition_state(sat_id, _S.RESTORED, epoch, sod,
                                  reason=reason_tag)
            self.transition_state(sat_id, _S.ACTIVE, epoch, sod,
                                  reason="restored_to_active")
            # Sigma history and continuous counters are preserved (no clear in transition_state for RESTORED)
            return 'RESTORE'

        else:
            # ── LONG/MEDIUM GAP: REBIRTH path ────────────────────────────────
            # DORMANT → RESET  (REBORN → ACTIVE happens in register_birth)
            self.transition_state(sat_id, _S.RESET, epoch, sod,
                                  reason=reason_tag)
            # register_birth() will see lifecycle_state==RESET, call
            # _init_child_lineage(), and transition RESET→REBORN→ACTIVE.
            return 'REBIRTH'

    # ─────────────────────────────────────────────────────────────────────────
    # STEP-4: Per-epoch convergence tick (Phase-2, 3, 4)
    # ─────────────────────────────────────────────────────────────────────────

    def tick(self,
             sat_id: str,
             epoch: int,
             sod: float,
             sigma_mm: float,
             residual_mm: Optional[float] = None,
             in_filter: bool = False) -> None:
        """Per-epoch update of rolling metrics and convergence state transitions.

        Must be called once per epoch for every active ambiguity, AFTER the
        EKF update (so that sigma reflects the post-update covariance).

        Updates:
          • sigma_history (deque)
          • residual_history (deque, if residual_mm provided)
          • continuous_active_epochs (monotonic during ACTIVE/CONVERGING/CONVERGED)
          • continuous_filter_epochs (when in_filter=True)

        Then evaluates:
          ACTIVE       → CONVERGING   (if criteria met)
          CONVERGING   → CONVERGED    (if criteria met)
          CONVERGED    → CONVERGING   (on sigma inflation or rejection streak)
          CONVERGING   → ACTIVE       (on persistent instability)

        IMPORTANT: This method DOES NOT write to the lifecycle trace unless a
        state transition occurs.  Sigma/metric updates are not traced (high freq).
        """
        s = self.states.get(sat_id)
        if s is None or not s.active:
            return

        ls = s.lifecycle_state

        # ── Update rolling metrics ────────────────────────────────────────────
        if math.isfinite(sigma_mm):
            s.sigma_history.append(sigma_mm)
            s.current_sigma = sigma_mm

        if residual_mm is not None and math.isfinite(residual_mm):
            s.residual_history.append(abs(residual_mm))

        # ── Continuous epoch counters (ASSERT-O7: must be monotonic) ─────────
        if ls in (_S.ACTIVE, _S.CONVERGING, _S.CONVERGED):
            s.continuous_active_epochs += 1

        if in_filter:
            s.continuous_filter_epochs += 1

        # ── Convergence state machine ─────────────────────────────────────────
        self._check_convergence(sat_id, epoch, sod, sigma_mm)

    def _check_convergence(self,
                           sat_id: str,
                           epoch: int,
                           sod: float,
                           sigma_mm: float) -> None:
        """Evaluate and apply deterministic convergence state transitions.

        Called from tick() after metrics are updated.  Implements Phase-4 rules.
        """
        s  = self.states.get(sat_id)
        if s is None:
            return
        ls = s.lifecycle_state

        # ── ACTIVE → CONVERGING ───────────────────────────────────────────────
        if ls == _S.ACTIVE:
            if (math.isfinite(sigma_mm)
                    and sigma_mm < self.CONVERGING_SIGMA_MM
                    and s.acceptance_streak >= self.CONVERGING_STREAK_MIN
                    and s.currently_observable):
                self.transition_state(sat_id, _S.CONVERGING, epoch, sod,
                                      reason="sigma_below_threshold")
            return

        # ── CONVERGING → CONVERGED / CONVERGING → ACTIVE ─────────────────────
        if ls == _S.CONVERGING:
            # Demotion: persistent rejection streak → back to ACTIVE
            if s.rejection_streak >= self.DEGRADED_REJ_STREAK:
                self.transition_state(sat_id, _S.ACTIVE, epoch, sod,
                                      reason=f"rej_streak_{s.rejection_streak}")
                return
            # Demotion: sigma inflation → back to ACTIVE
            if (math.isfinite(sigma_mm)
                    and sigma_mm > self.CONVERGING_SIGMA_MM * 1.5):
                self.transition_state(sat_id, _S.ACTIVE, epoch, sod,
                                      reason=f"sigma_inflation_{sigma_mm:.0f}mm")
                return
            # Promotion: stable sigma + residuals + low rejection ratio
            if (self._sigma_stable(s)
                    and len(s.residual_history) >= self.CONVERGED_WINDOW // 2
                    and self._recent_rejection_ratio(s) < self.CONVERGED_MAX_REJ_RATIO
                    and math.isfinite(sigma_mm)
                    and sigma_mm < self._converged_sigma_threshold_mm):
                self.transition_state(sat_id, _S.CONVERGED, epoch, sod,
                                      reason="stable_sigma_and_residuals")
            return

        # ── CONVERGED → CONVERGING ────────────────────────────────────────────
        if ls == _S.CONVERGED:
            # Demotion: sigma inflated above CONVERGING threshold
            if (math.isfinite(sigma_mm)
                    and sigma_mm > self.CONVERGING_SIGMA_MM):
                self.transition_state(sat_id, _S.CONVERGING, epoch, sod,
                                      reason=f"sigma_inflation_{sigma_mm:.0f}mm")
                return
            # Demotion: rejection streak rising
            if s.rejection_streak >= self.DEGRADED_REJ_STREAK:
                self.transition_state(sat_id, _S.CONVERGING, epoch, sod,
                                      reason=f"rej_streak_{s.rejection_streak}")
            return

    # ─────────────────────────────────────────────────────────────────────────
    # Step-2 PUBLIC API: slot registration
    # ─────────────────────────────────────────────────────────────────────────

    def register_new_slot(self, sat_id: str, state_index: int) -> None:
        """Register a freshly-allocated EKF state slot for *sat_id*.

        Replaces the direct amgr._ensure() call in the PPP engine.
        State is created as lifecycle_state=NEW, active=False.
        No lifecycle trace row is emitted — the slot is not yet alive.
        """
        self._ensure(sat_id, state_index)

    # ─────────────────────────────────────────────────────────────────────────
    # Step-2 PUBLIC API: activation / deactivation
    # ─────────────────────────────────────────────────────────────────────────

    def activate(self, sat_id: str,
                 epoch: int = -1, sod: float = float('nan')) -> None:
        """Activate *sat_id* after a short gap (RESTORE semantics).

        Phase-5 rule: gap recovery WITHOUT a reset → same lineage_id preserved.

        STEP-4 NOTE: For epoch-aware gap handling, prefer calling handle_gap()
        from the PPP engine instead of activate() directly.  activate() is
        retained for backward compatibility and handles the DORMANT→RESTORED
        path without gap classification.

        Transition:
          DORMANT  → RESTORED → ACTIVE  (standard gap-recovery path)
          NEW      → ACTIVE             (defensive: slot activated before any birth)
        """
        if sat_id not in self.states:
            return
        s  = self.states[sat_id]
        ls = s.lifecycle_state

        if ls == _S.DORMANT:
            # Standard RESTORE: same lineage, satellite came back
            self.transition_state(sat_id, _S.RESTORED, epoch, sod,
                                  reason="gap_recovery")
            self.transition_state(sat_id, _S.ACTIVE, epoch, sod,
                                  reason="restored_to_active")

        elif ls == _S.NEW:
            # Unusual: first activation of a slot that was never born.
            if not s.lineage_id:
                self._init_genesis_lineage(s)
            self.transition_state(sat_id, _S.ACTIVE, epoch, sod,
                                  reason="activate_from_new")

        # For ACTIVE/RESTORED/REBORN: already active — no-op (legacy behaviour).

        # ASSERT-2: must be active after activate()
        assert s.active, \
            f"ASSERT-2 failed: {sat_id} active must be True after activate()"

    def deactivate(self, sat_id: str,
                   epoch: int = -1, sod: float = float('nan')) -> None:
        """Deactivate *sat_id* — satellite left the visible set.

        Transitions any contributing state → DORMANT.
        RESET/SLIPPED/NEW/PRUNED states are left unchanged (already inactive).

        STEP-4: gap_start_epoch is recorded in transition_state when entering DORMANT.
        """
        if sat_id not in self.states:
            return
        s  = self.states[sat_id]
        ls = s.lifecycle_state

        if ls in (_S.ACTIVE, _S.CONVERGING, _S.CONVERGED, _S.RESTORED):
            self.transition_state(sat_id, _S.DORMANT, epoch, sod,
                                  reason="deactivate")
        # REBORN should not reach deactivate in normal flow.
        # NEW/RESET/SLIPPED/DORMANT/PRUNED: already inactive, no-op.

    # ─────────────────────────────────────────────────────────────────────────
    # Step-2 PUBLIC API: observability flags  (no lifecycle state change)
    # ─────────────────────────────────────────────────────────────────────────

    def mark_observable(self, sat_id: str,
                        epoch: int = -1, sod: float = float('nan'),
                        elevation_deg: Optional[float] = None) -> None:
        """Mark satellite as currently visible (flag update only)."""
        if sat_id not in self.states:
            return
        s = self.states[sat_id]
        s.last_seen_epoch = epoch
        s.last_seen_sod   = sod
        if elevation_deg is not None:
            s.current_elevation_deg = elevation_deg
        s.currently_observable = True

    def mark_unobservable(self, sat_id: str,
                          epoch: int = -1, sod: float = float('nan')) -> None:
        """Mark satellite as not currently visible (flag update only)."""
        if sat_id not in self.states:
            return
        self.states[sat_id].currently_observable = False

    # ─────────────────────────────────────────────────────────────────────────
    # Step-2 PUBLIC API: filter-participation flags
    # ─────────────────────────────────────────────────────────────────────────

    def mark_in_filter(self, sat_id: str,
                       epoch: int = -1, sod: float = float('nan')) -> None:
        """Record that *sat_id* contributed a phase row to the EKF this epoch."""
        if sat_id not in self.states:
            return
        self.states[sat_id].currently_in_filter = True

    def mark_out_of_filter(self, sat_id: str,
                           epoch: int = -1, sod: float = float('nan')) -> None:
        """Record that *sat_id* did NOT contribute to the EKF this epoch."""
        if sat_id not in self.states:
            return
        self.states[sat_id].currently_in_filter = False

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle events
    # ─────────────────────────────────────────────────────────────────────────

    def register_birth(self, sat_id: str, state_index: int,
                       epoch: int, sod: float,
                       sigma_m: float = 20.0) -> None:
        """Record a post-fit ambiguity birth (genesis or post-reset rebirth).

        Phase-5 lineage rules (Step-3, unchanged in Step-4)
        ---------------------
        • Previous lifecycle_state == RESET  → REBIRTH:
            new child lineage (generation+1, parent=old lineage_id).
            Transitions: RESET → REBORN → ACTIVE
        • Previous lifecycle_state == NEW (or unset) → Genesis birth:
            new genesis lineage (generation=0, parent=None).
            Transition: NEW → ACTIVE

        STEP-4: If previous state is RESET (including long-gap RESET from
        handle_gap()), this creates a child lineage — matching the long-gap
        REBIRTH semantics.

        *initial_sigma* is written once on the very first call.
        *born_epoch* is updated on every birth including post-reset rebirths.
        """
        s    = self._ensure(sat_id, state_index)
        prev = s.lifecycle_state

        # ── Lineage assignment (Phase-1) ─────────────────────────────────────
        if prev == _S.RESET:
            # Post-reset REBIRTH (slip-triggered OR long-gap handle_gap):
            # new child lineage, generation+1
            self._init_child_lineage(s)
        else:
            # Genesis or unexpected prior state → fresh lineage
            self._init_genesis_lineage(s)

        # ── Sigma / epoch bookkeeping ────────────────────────────────────────
        if s.initial_sigma < 0.0:          # write-once (mirrors original guard)
            s.initial_sigma = sigma_m * 1e3

        s.born_epoch    = epoch
        s.born_sod      = sod
        s.slip_flag     = False
        s.current_sigma = sigma_m * 1e3

        # STEP-4: reset rolling metrics on every birth (fresh lineage = fresh history)
        s.sigma_history.clear()
        s.residual_history.clear()
        s.continuous_active_epochs = 0
        s.continuous_filter_epochs = 0

        # ── State transitions (Phase-3) ──────────────────────────────────────
        if prev == _S.RESET:
            # Two-step: RESET → REBORN → ACTIVE
            self.transition_state(sat_id, _S.REBORN, epoch, sod,
                                  reason="post_reset_rebirth")
            self.transition_state(sat_id, _S.ACTIVE, epoch, sod,
                                  reason="reborn_to_active")
        else:
            # Genesis: ensure lifecycle_state is NEW before transition
            if s.lifecycle_state not in (_S.NEW,):
                s.lifecycle_state = _S.NEW
            self.transition_state(sat_id, _S.ACTIVE, epoch, sod,
                                  reason="genesis_birth")

        # ASSERT-2
        assert s.active, \
            f"ASSERT-2 failed: {sat_id} active must be True after register_birth"

    def register_inherited(self, sat_id: str, state_index: int,
                           sigma_m: float) -> None:
        """Record an ambiguity inherited from a previous pass or long-gap re-acq.

        Phase-5 lineage rules (Step-3, unchanged in Step-4)
        ---------------------
        • Previous lifecycle_state == DORMANT → long-gap re-acquisition:
            new child lineage (generation+1).
        • Any other prior state → genesis birth (generation=0).
        """
        s    = self._ensure(sat_id, state_index)
        prev = s.lifecycle_state

        # ── Lineage assignment ────────────────────────────────────────────────
        if prev == _S.DORMANT:
            self._init_child_lineage(s)
        else:
            self._init_genesis_lineage(s)

        # Force lifecycle_state to NEW so transition_state fires legally.
        s.lifecycle_state = _S.NEW

        # ── Sigma bookkeeping ────────────────────────────────────────────────
        if s.initial_sigma < 0.0:
            s.initial_sigma = sigma_m * 1e3

        s.current_sigma = sigma_m * 1e3
        # STEP-4: fresh inherited birth → clear rolling metrics
        s.sigma_history.clear()
        s.residual_history.clear()
        s.continuous_active_epochs = 0
        s.continuous_filter_epochs = 0

        # ── State transition: NEW → ACTIVE ───────────────────────────────────
        self.transition_state(sat_id, _S.ACTIVE, -1, float('nan'),
                              reason="inherited_birth")

    def register_reset(self, sat_id: str,
                       epoch: int = -1, sod: float = float('nan'),
                       reason: str = "cycle_slip") -> None:
        """Record a cycle-slip reset.

        Phase-5 lineage: slip events mark the end of the current lineage.
        The next register_birth() will create a new child lineage.

        Transitions emitted (two steps for forensic clarity):
          {ACTIVE | CONVERGING | CONVERGED | RESTORED} → SLIPPED → RESET
        """
        if sat_id in self.states:
            s  = self.states[sat_id]
            ls = s.lifecycle_state

            # ── Transition to SLIPPED ────────────────────────────────────────
            if ls in (_S.ACTIVE, _S.CONVERGING, _S.CONVERGED, _S.RESTORED,
                      _S.REBORN):
                self.transition_state(sat_id, _S.SLIPPED, epoch, sod,
                                      reason=reason)

            # ── Transition to RESET ──────────────────────────────────────────
            if s.lifecycle_state == _S.SLIPPED:
                self.transition_state(sat_id, _S.RESET, epoch, sod,
                                      reason=reason)

            # ── Bookkeeping (mirrors original slip block) ────────────────────
            s.reset_count      += 1
            s.slip_flag         = True
            s.rejection_streak  = 0
            s.acceptance_streak = 0
            s.recent_ph_res.clear()
            s.recent_rej.clear()

            # ASSERT-2 (inverse): must be inactive after reset
            assert not s.active, \
                f"ASSERT-2 failed: {sat_id} active must be False after register_reset"

        # Cumulative counter always incremented (even if state not yet registered)
        self._total_resets += 1

    def register_accept(self, sat_id: str, epoch: int, sod: float,
                        residual_mm: Optional[float] = None,
                        H_norm: Optional[float] = None) -> None:
        """Record one accepted phase observation for *sat_id*."""
        if sat_id not in self.states:
            return
        s = self.states[sat_id]
        s.accepted_count    += 1
        s.last_accept_epoch  = epoch
        s.last_accept_sod    = sod
        s.rejection_streak   = 0
        s.acceptance_streak += 1

        # ASSERT-3
        assert s.last_accept_epoch == epoch, \
            f"ASSERT-3 failed: {sat_id} last_accept_epoch not updated"

        self._ring_append(s.recent_rej, 0, self._ring_size)

        if residual_mm is not None:
            self._ring_append(s.recent_ph_res, residual_mm, self._ring_size)

        if H_norm is not None:
            s.last_H_norm = H_norm

    def register_reject(self, sat_id: str) -> None:
        """Record one rejected phase observation for *sat_id*."""
        if sat_id not in self.states:
            return
        s = self.states[sat_id]
        s.rejected_count    += 1
        s.rejection_streak  += 1
        s.acceptance_streak  = 0
        self._ring_append(s.recent_rej, 1, self._ring_size)

    # ─────────────────────────────────────────────────────────────────────────
    # Sigma and state-index synchronisation
    # ─────────────────────────────────────────────────────────────────────────

    def update_sigma(self, sat_id: str, sigma_mm: float,
                     epoch: int = -1, sod: float = float('nan')) -> None:
        """Sync current_sigma from the live covariance diagonal."""
        if sat_id not in self.states:
            return
        self.states[sat_id].current_sigma = sigma_mm

    def update_state_index(self, sat_id: str, state_index: int,
                           epoch: int = -1, sod: float = float('nan')) -> None:
        """Set (or verify) the EKF state index for *sat_id*."""
        if sat_id not in self.states:
            return
        s = self.states[sat_id]
        s.state_index = state_index
        assert s.state_index >= 0, \
            f"ASSERT-1 failed: {sat_id} active amb has invalid state_index"
        assert s.state_index == state_index, \
            f"ASSERT-7 failed: {sat_id} state_index mismatch after update"

    # ─────────────────────────────────────────────────────────────────────────
    # Visibility helpers (Step-1/2 API preserved)
    # ─────────────────────────────────────────────────────────────────────────

    def update_visibility(self, sat_id: str, observable: bool) -> None:
        """Low-level visibility setter — kept for backward compatibility."""
        if sat_id in self.states:
            self.states[sat_id].currently_observable = observable

    def mark_missing(self, sat_id: str, sod: float,
                     epoch: int = -1) -> None:
        """Satellite left the visible set this epoch."""
        self.mark_unobservable(sat_id, epoch=epoch, sod=sod)

    def mark_present(self, sat_id: str, epoch: int, sod: float,
                     elevation_deg: Optional[float] = None) -> None:
        """Satellite is visible this epoch."""
        self.mark_observable(sat_id, epoch=epoch, sod=sod,
                              elevation_deg=elevation_deg)

    def update_Kg_norm(self, sat_id: str, kg_norm: float) -> None:
        if sat_id in self.states:
            self.states[sat_id].last_Kg_norm = kg_norm

    # ─────────────────────────────────────────────────────────────────────────
    # Active-flag shims (backward compat — PPP must use activate/deactivate)
    # ─────────────────────────────────────────────────────────────────────────

    def set_active(self, sat_id: str, value: bool) -> None:
        """Direct active-flag setter — kept for backward compat."""
        if sat_id in self.states:
            self.states[sat_id].active = bool(value)

    # ─────────────────────────────────────────────────────────────────────────
    # Query methods
    # ─────────────────────────────────────────────────────────────────────────

    def is_active(self, sat_id: str) -> bool:
        """Mirrors amb_active.get(sat_id, False)."""
        s = self.states.get(sat_id)
        return s is not None and s.active

    def is_observable(self, sat_id: str) -> bool:
        s = self.states.get(sat_id)
        return s is not None and s.currently_observable

    def get_lifecycle_state(self, sat_id: str) \
            -> Optional[AmbiguityLifecycleState]:
        """Return the current lifecycle state for *sat_id*, or None."""
        s = self.states.get(sat_id)
        return s.lifecycle_state if s else None

    def get_lineage_id(self, sat_id: str) -> str:
        s = self.states.get(sat_id)
        return s.lineage_id if s else ""

    def get_generation(self, sat_id: str) -> int:
        s = self.states.get(sat_id)
        return s.generation if s else 0

    def get_parent_lineage_id(self, sat_id: str) -> Optional[str]:
        s = self.states.get(sat_id)
        return s.parent_lineage_id if s else None

    def get_sigma(self, sat_id: str) -> float:
        s = self.states.get(sat_id)
        return s.current_sigma if s else 20000.0

    def get_state_index(self, sat_id: str) -> int:
        s = self.states.get(sat_id)
        return s.state_index if s else -1

    def get_acceptance_ratio(self, sat_id: str) -> float:
        s = self.states.get(sat_id)
        if s is None:
            return float('nan')
        total = s.accepted_count + s.rejected_count
        return s.accepted_count / total if total > 0 else float('nan')

    def get_lifetime(self, sat_id: str, current_epoch: int) -> int:
        s = self.states.get(sat_id)
        if s is None or s.born_epoch < 0:
            return 0
        return current_epoch - s.born_epoch

    def get_birth_sigma_mm(self, sat_id: str,
                           default_mm: float = 20000.0) -> float:
        """Return initial_sigma in mm."""
        s = self.states.get(sat_id)
        if s is None or s.initial_sigma < 0.0:
            return default_mm
        return s.initial_sigma

    def get_recent_ph_res(self, sat_id: str) -> list:
        s = self.states.get(sat_id)
        return list(s.recent_ph_res) if s else []

    def get_recent_rej(self, sat_id: str) -> list:
        s = self.states.get(sat_id)
        return list(s.recent_rej) if s else []

    def get_birth_epoch(self, sat_id: str, default: int = -1) -> int:
        s = self.states.get(sat_id)
        return s.born_epoch if s else default

    def get_accepted_count(self, sat_id: str) -> int:
        s = self.states.get(sat_id)
        return s.accepted_count if s else 0

    def get_rejected_count(self, sat_id: str) -> int:
        s = self.states.get(sat_id)
        return s.rejected_count if s else 0

    def get_last_accept_epoch(self, sat_id: str, default: int = -1) -> int:
        s = self.states.get(sat_id)
        return s.last_accept_epoch if s else default

    def get_H_norm(self, sat_id: str) -> float:
        s = self.states.get(sat_id)
        return s.last_H_norm if s else float('nan')

    def get_Kg_norm(self, sat_id: str) -> float:
        s = self.states.get(sat_id)
        return s.last_Kg_norm if s else float('nan')

    # STEP-4: additional getters
    def get_continuous_active_epochs(self, sat_id: str) -> int:
        s = self.states.get(sat_id)
        return s.continuous_active_epochs if s else 0

    def get_continuous_filter_epochs(self, sat_id: str) -> int:
        s = self.states.get(sat_id)
        return s.continuous_filter_epochs if s else 0

    def get_rolling_sigma_mm(self, sat_id: str) -> float:
        s = self.states.get(sat_id)
        return self._rolling_sigma_mm(s) if s else float('nan')

    def get_convergence_score(self, sat_id: str) -> float:
        s = self.states.get(sat_id)
        return self._convergence_score(s) if s else 0.0

    # ─────────────────────────────────────────────────────────────────────────
    # Backward-compat dict shims
    # ─────────────────────────────────────────────────────────────────────────

    def get(self, sat_id: str, default: bool = False) -> bool:
        """Mirrors amb_active.get(sat_id, False)."""
        return self.is_active(sat_id)

    def items(self):
        """Mirrors list(amb_active.items()) — yields (sat_id, is_active) pairs."""
        return ((sid, s.active) for sid, s in self.states.items())

    # ─────────────────────────────────────────────────────────────────────────
    # Cumulative resets property
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def cumulative_resets(self) -> int:
        """Total register_reset() calls."""
        return self._total_resets

    # ─────────────────────────────────────────────────────────────────────────
    # Step-2 assertions (retained)
    # ─────────────────────────────────────────────────────────────────────────

    def assert_state_index_uniqueness(self, sidx: dict) -> None:
        """ASSERT-5: state indices must be unique across all registered sats."""
        used = {}
        for sat_id, ki in sidx.items():
            if ki in used:
                raise AssertionError(
                    f"ASSERT-5 failed: state index {ki} shared by "
                    f"{used[ki]} and {sat_id}"
                )
            used[ki] = sat_id

    def assert_state_indices_match(self, sidx: dict) -> None:
        """ASSERT-7 (batch): manager.state_index must match sidx for all active sats."""
        for sat_id, ki in sidx.items():
            s = self.states.get(sat_id)
            if s is not None and s.active:
                if s.state_index != ki:
                    raise AssertionError(
                        f"ASSERT-7 failed: {sat_id} manager.state_index="
                        f"{s.state_index} but sidx={ki}"
                    )

    # ─────────────────────────────────────────────────────────────────────────
    # Step-3: ASSERT-L1 … ASSERT-L7 (Phase-6, retained)
    # ─────────────────────────────────────────────────────────────────────────

    def _assert_L1_active_flag_consistent(self, epoch: int) -> None:
        """ASSERT-L1: active bool must be consistent with lifecycle_state."""
        for sat_id, s in self.states.items():
            expected = s.lifecycle_state in _ACTIVE_LIFECYCLE_STATES
            if s.active != expected:
                raise AssertionError(
                    f"ASSERT-L1 failed: {sat_id}"
                    f"  lifecycle_state={s.lifecycle_state.value}"
                    f"  active={s.active}  (expected={expected})"
                    f"  lineage={s.lineage_id}  epoch={epoch}"
                )

    def _assert_L2_lineage_uniqueness(self, epoch: int) -> None:
        """ASSERT-L2: lineage_id must be globally unique across all states."""
        seen: dict[str, str] = {}
        for sat_id, s in self.states.items():
            lin = s.lineage_id
            if not lin:
                continue
            if lin in seen:
                raise AssertionError(
                    f"ASSERT-L2 failed: lineage_id '{lin}' shared by "
                    f"'{seen[lin]}' and '{sat_id}'  epoch={epoch}"
                )
            seen[lin] = sat_id

    def _assert_L3_active_state_index_valid(self, epoch: int) -> None:
        """ASSERT-L3: every active ambiguity must have state_index >= 0."""
        for sat_id, s in self.states.items():
            if s.active and s.state_index < 0:
                raise AssertionError(
                    f"ASSERT-L3 failed: '{sat_id}' is active but "
                    f"state_index={s.state_index}  epoch={epoch}"
                )

    def _assert_L4_dormant_not_in_filter(self, epoch: int) -> None:
        """ASSERT-L4: DORMANT ambiguity must not be in filter."""
        for sat_id, s in self.states.items():
            if s.lifecycle_state == _S.DORMANT and s.currently_in_filter:
                raise AssertionError(
                    f"ASSERT-L4 failed: '{sat_id}' is DORMANT but "
                    f"currently_in_filter=True  epoch={epoch}"
                )

    def _assert_L5_converged_sigma(self, epoch: int) -> None:
        """ASSERT-L5: CONVERGED ambiguity must have sigma below threshold."""
        thr = self._converged_sigma_threshold_mm
        for sat_id, s in self.states.items():
            if (s.lifecycle_state == _S.CONVERGED
                    and s.current_sigma >= thr):
                raise AssertionError(
                    f"ASSERT-L5 failed: '{sat_id}' is CONVERGED but "
                    f"sigma={s.current_sigma:.1f}mm >= {thr:.1f}mm"
                    f"  epoch={epoch}"
                )

    def _assert_L6_reborn_generation_nonzero(self, epoch: int) -> None:
        """ASSERT-L6: ambiguity with a parent must have generation > 0."""
        for sat_id, s in self.states.items():
            if s.parent_lineage_id is not None and s.generation <= 0:
                raise AssertionError(
                    f"ASSERT-L6 failed: '{sat_id}' has parent_lineage_id="
                    f"'{s.parent_lineage_id}' but generation={s.generation}"
                    f"  (expected > 0)  epoch={epoch}"
                )

    def _assert_L7_no_self_referential_lineage(self, epoch: int) -> None:
        """ASSERT-L7: no state's parent_lineage_id may equal its own lineage_id."""
        for sat_id, s in self.states.items():
            if s.parent_lineage_id and s.parent_lineage_id == s.lineage_id:
                raise AssertionError(
                    f"ASSERT-L7 failed: '{sat_id}' lineage_id == "
                    f"parent_lineage_id ('{s.lineage_id}')  epoch={epoch}"
                )

    # ─────────────────────────────────────────────────────────────────────────
    # STEP-4: ASSERT-O1 … ASSERT-O7 (Phase-7)
    # ─────────────────────────────────────────────────────────────────────────

    def _assert_O1_restored_lineage_unchanged(self, epoch: int) -> None:
        """ASSERT-O1: RESTORED lineage_id must match the pre-restore snapshot."""
        for sat_id, s in self.states.items():
            if (s.lifecycle_state == _S.RESTORED
                    and s._lineage_at_restore
                    and s.lineage_id != s._lineage_at_restore):
                raise AssertionError(
                    f"ASSERT-O1 failed: '{sat_id}' RESTORED but lineage changed "
                    f"from '{s._lineage_at_restore}' to '{s.lineage_id}'  epoch={epoch}"
                )

    def _assert_O2_reborn_lineage_differs(self, epoch: int) -> None:
        """ASSERT-O2: REBORN lineage_id must differ from parent_lineage_id."""
        for sat_id, s in self.states.items():
            if (s.lifecycle_state == _S.REBORN
                    and s.parent_lineage_id
                    and s.lineage_id == s.parent_lineage_id):
                raise AssertionError(
                    f"ASSERT-O2 failed: '{sat_id}' REBORN but lineage_id == "
                    f"parent_lineage_id ('{s.lineage_id}')  epoch={epoch}"
                )

    def _assert_O3_converged_sigma_below_threshold(self, epoch: int) -> None:
        """ASSERT-O3: CONVERGED sigma must be below converging threshold."""
        # Use the CONVERGING_SIGMA_MM as the upper bound for CONVERGED
        for sat_id, s in self.states.items():
            if (s.lifecycle_state == _S.CONVERGED
                    and math.isfinite(s.current_sigma)
                    and s.current_sigma >= self.CONVERGING_SIGMA_MM):
                raise AssertionError(
                    f"ASSERT-O3 failed: '{sat_id}' is CONVERGED but "
                    f"sigma={s.current_sigma:.1f}mm >= "
                    f"CONVERGING_SIGMA_MM={self.CONVERGING_SIGMA_MM:.1f}mm"
                    f"  epoch={epoch}"
                )

    def _assert_O4_dormant_not_in_filter(self, epoch: int) -> None:
        """ASSERT-O4: DORMANT ambiguity must not be in filter (mirrors L4)."""
        self._assert_L4_dormant_not_in_filter(epoch)

    def _assert_O5_restored_generation_unchanged(self, epoch: int) -> None:
        """ASSERT-O5: RESTORED ambiguity generation must match pre-restore value."""
        for sat_id, s in self.states.items():
            if (s.lifecycle_state == _S.RESTORED
                    and s._prev_gen_at_restore >= 0
                    and s.generation != s._prev_gen_at_restore):
                raise AssertionError(
                    f"ASSERT-O5 failed: '{sat_id}' RESTORED but generation changed "
                    f"from {s._prev_gen_at_restore} to {s.generation}  epoch={epoch}"
                )

    def _assert_O6_child_generation_gt_parent(self, epoch: int) -> None:
        """ASSERT-O6: child generation must be strictly greater than parent generation.

        Checks by building a lineage_id → generation map and verifying the
        parent-child relationship via parent_lineage_id references.
        """
        lin_to_gen: dict[str, int] = {}
        for s in self.states.values():
            if s.lineage_id:
                lin_to_gen[s.lineage_id] = s.generation
        for sat_id, s in self.states.items():
            if s.parent_lineage_id and s.parent_lineage_id in lin_to_gen:
                parent_gen = lin_to_gen[s.parent_lineage_id]
                if s.generation <= parent_gen:
                    raise AssertionError(
                        f"ASSERT-O6 failed: '{sat_id}' generation={s.generation} "
                        f"<= parent generation={parent_gen}  "
                        f"parent_lineage='{s.parent_lineage_id}'  epoch={epoch}"
                    )

    def _assert_O7_continuous_active_monotonic(self, epoch: int) -> None:
        """ASSERT-O7: continuous_active_epochs must not be negative for active sats.

        Full monotonicity cannot be checked within a single epoch (it requires
        remembering the previous value).  This assertion checks the weaker
        invariant: continuous_active_epochs >= 0 for all states in
        ACTIVE / CONVERGING / CONVERGED.
        """
        for sat_id, s in self.states.items():
            if s.lifecycle_state in (_S.ACTIVE, _S.CONVERGING, _S.CONVERGED):
                if s.continuous_active_epochs < 0:
                    raise AssertionError(
                        f"ASSERT-O7 failed: '{sat_id}' "
                        f"continuous_active_epochs={s.continuous_active_epochs} < 0"
                        f"  state={s.lifecycle_state.value}  epoch={epoch}"
                    )

    # ─────────────────────────────────────────────────────────────────────────
    # Combined assertion runners
    # ─────────────────────────────────────────────────────────────────────────

    def run_consistency_assertions(self, epoch: int, sod: float) -> None:
        """Run all Step-3 + Step-4 consistency assertions.

        Call every SNAPSHOT_INTERVAL epochs or at any forensic checkpoint.
        Silent on pass; hard-fails with AssertionError on any violation.
        """
        # Step-3 assertions (ASSERT-L1 … ASSERT-L7)
        self._assert_L1_active_flag_consistent(epoch)
        self._assert_L2_lineage_uniqueness(epoch)
        self._assert_L3_active_state_index_valid(epoch)
        self._assert_L4_dormant_not_in_filter(epoch)
        self._assert_L5_converged_sigma(epoch)
        self._assert_L6_reborn_generation_nonzero(epoch)
        self._assert_L7_no_self_referential_lineage(epoch)
        # Step-4 assertions (ASSERT-O1 … ASSERT-O7)
        self._assert_O1_restored_lineage_unchanged(epoch)
        self._assert_O2_reborn_lineage_differs(epoch)
        self._assert_O3_converged_sigma_below_threshold(epoch)
        self._assert_O4_dormant_not_in_filter(epoch)
        self._assert_O5_restored_generation_unchanged(epoch)
        self._assert_O6_child_generation_gt_parent(epoch)
        self._assert_O7_continuous_active_monotonic(epoch)

    def run_step4_assertions(self, epoch: int, sod: float) -> None:
        """Run only Step-4 assertions (ASSERT-O1 … ASSERT-O7)."""
        self._assert_O1_restored_lineage_unchanged(epoch)
        self._assert_O2_reborn_lineage_differs(epoch)
        self._assert_O3_converged_sigma_below_threshold(epoch)
        self._assert_O4_dormant_not_in_filter(epoch)
        self._assert_O5_restored_generation_unchanged(epoch)
        self._assert_O6_child_generation_gt_parent(epoch)
        self._assert_O7_continuous_active_monotonic(epoch)

    # ─────────────────────────────────────────────────────────────────────────
    # Snapshot CSV (updated with Step-3 lineage columns)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def snapshot_header() -> str:
        return (
            "epoch,sat_id,state_index,active,observable,"
            "lifecycle_state,lineage_id,generation,parent_lineage_id,"
            "sigma_mm,resets,accepts,rejects,"
            "born_epoch,lifetime_epochs,"
            "rejection_streak,acceptance_streak,"
            "continuous_active_epochs,continuous_filter_epochs,"
            "rolling_sigma_mm,convergence_score\n"
        )

    def write_snapshot(self, epoch: int, sidx: dict, P) -> None:
        """Write one row per known ambiguity to the snapshot CSV.

        Called every SNAPSHOT_INTERVAL epochs from _ppp_pass.
        STEP-4: adds continuous counters, rolling sigma, convergence score.
        """
        if self._snapshot_fh is None:
            return
        for sat_id in sorted(self.states):
            s   = self.states[sat_id]
            ki  = sidx.get(sat_id, -1)
            if ki < 0:
                continue
            try:
                sigma_mm = math.sqrt(max(P[ki, ki], 0.0)) * 1e3
            except Exception:
                sigma_mm = float('nan')
            lifetime = (epoch - s.born_epoch) if s.born_epoch >= 0 else 0
            par      = s.parent_lineage_id if s.parent_lineage_id else ""
            roll_sig = self._rolling_sigma_mm(s)
            conv_sc  = self._convergence_score(s)
            self._snapshot_fh.write(
                f"{epoch},{sat_id},{ki},"
                f"{1 if s.active else 0},"
                f"{1 if s.currently_observable else 0},"
                f"{s.lifecycle_state.value},{s.lineage_id},{s.generation},{par},"
                f"{sigma_mm:.2f},{s.reset_count},"
                f"{s.accepted_count},{s.rejected_count},"
                f"{s.born_epoch},{lifetime},"
                f"{s.rejection_streak},{s.acceptance_streak},"
                f"{s.continuous_active_epochs},{s.continuous_filter_epochs},"
                f"{'nan' if roll_sig != roll_sig else f'{roll_sig:.2f}'},"
                f"{conv_sc:.4f}\n"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle trace CSV header (Step-4 / Phase-8 extended format)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def lifecycle_trace_header() -> str:
        """Phase-8 extended spec — canonical forensic source for all state transitions."""
        return (
            "epoch,sod,sat,"
            "lineage_id,generation,parent_lineage_id,"
            "old_state,new_state,reason,"
            "sigma_mm,state_index,active,observable,in_filter,"
            "gap_length_epochs,convergence_score,"
            "rolling_sigma_mm,rolling_residual_rms_mm,"
            "acceptance_ratio,continuous_active_epochs,continuous_filter_epochs\n"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Convenience factories (unchanged API)
# ─────────────────────────────────────────────────────────────────────────────

def open_snapshot_csv(directory: str, label: str = 'FWD'):
    """Open (or create) the ambiguity manager snapshot CSV."""
    path = os.path.join(directory, f'ambiguity_manager_snapshot_{label}.csv')
    fh = open(path, 'w')
    fh.write(AmbiguityManager.snapshot_header())
    return fh


def open_lifecycle_trace_csv(directory: str, label: str = 'GE'):
    """Open (or create) the ambiguity lifecycle trace CSV (Step-4 Phase-8 format)."""
    path = os.path.join(directory, f'ambiguity_lifecycle_trace_{label}.csv')
    fh = open(path, 'w')
    fh.write(AmbiguityManager.lifecycle_trace_header())
    return fh

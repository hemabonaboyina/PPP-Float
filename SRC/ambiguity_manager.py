"""
ambiguity_manager.py  —  Minimal Float-PPP Ambiguity Tracker
=============================================================

STABILIZATION REFACTOR: All ambiguity-lifecycle bureaucracy removed.
This is NOT a permanent downgrade — it is a controlled simplification
phase to isolate EKF / measurement-model correctness before reintroducing
PPP-AR complexity.

REMOVED (temporarily):
  ✗  Lifecycle state machine (NEW/ACTIVE/CONVERGING/CONVERGED/SLIPPED/RESET/
      REBORN/DORMANT/RESTORED/PRUNED)
  ✗  Lineage tracking (lineage_id, generation, parent_lineage_id)
  ✗  Genealogy / lineage audit infrastructure
  ✗  Convergence evaluation (convergence_score, sigma_history, rolling metrics)
  ✗  DORMANT gap semantics (SHORT_GAP / LONG_GAP routing)
  ✗  ASSERT-L1…L7, ASSERT-O1…O7, ASSERT-B1…B3, ASSERT-C1 invariants
  ✗  Lifecycle trace CSV (ambiguity_lifecycle_trace.csv)
  ✗  Promotion / quarantine states

RETAINED (essential float PPP):
  ✓  born / active flag  (simple bool — no state machine)
  ✓  Float value tracking (current_sigma synced from EKF diagonal)
  ✓  Accept / reject counters and ring buffers (diagnostics)
  ✓  Visibility flag (currently_observable)
  ✓  cumulative_resets property
  ✓  Ambiguity-manager snapshot CSV (write_snapshot)
  ✓  All public-API method signatures preserved for backward compatibility
       with ppp_gps_glab.py — lifecycle-specific methods are no-ops.

DESIGN
------
  born = False   →  needs_birth()=True,  is_active()=False, is_birth_complete()=False
  born = True    →  needs_birth()=False, is_active()=True,  is_birth_complete()=True

State transitions:
  register_new_slot()    → born=False  (slot allocated; awaits post-fit birth)
  register_birth()       → born=True   (post-fit birth; x[ki] valid)
  register_inherited()   → born=True   (state from previous pass)
  activate()             → born=True   (gap recovery)
  register_reset()       → born=False  (cycle-slip; x[ki] zeroed/reinflated)
  deactivate()           → born=False  (covariance sanity failure)
  mark_missing()         → born=False  (satellite left geometry; state saved
                                        externally for gap recovery)

AmbiguityLifecycleState: stub enum retained for import compatibility.
STEP-2 / STEP-3 methods: retained as stubs / no-ops.
"""

from __future__ import annotations

import enum
import math
import os
from dataclasses import dataclass, field
from typing import Optional, Dict


# ── Stub enum: kept for import compatibility with ppp_gps_glab.py ──────────
class AmbiguityLifecycleState(enum.Enum):
    """Stub — lifecycle state machine removed. Retained for API compatibility."""
    ACTIVE     = "ACTIVE"
    RESET      = "RESET"
    NEW        = "NEW"
    CONVERGING = "CONVERGING"
    CONVERGED  = "CONVERGED"
    DORMANT    = "DORMANT"


# ── Per-satellite bookkeeping ───────────────────────────────────────────────

@dataclass
class AmbiguityState:
    """Minimal per-satellite record for float-PPP ambiguity tracking."""

    # ── Identity ─────────────────────────────────────────────────────────
    sat_id:        str
    constellation: str
    state_index:   int

    # ── Core state ───────────────────────────────────────────────────────
    born:              bool  = False       # True after valid post-fit birth
    current_sigma:     float = 20000.0    # mm — synced from sqrt(P[ki,ki]) each epoch
    current_value:     float = 0.0        # m  — mirrors x[ki] (informational only)
    initial_sigma:     float = -1.0       # mm — recorded at first-ever birth

    # ── Epoch bookmarks ───────────────────────────────────────────────────
    born_epoch:        int   = -1
    born_sod:          float = float('nan')
    last_seen_epoch:   int   = -1
    last_seen_sod:     float = float('nan')
    last_accept_epoch: int   = -1
    last_accept_sod:   float = float('nan')

    # ── Counters ──────────────────────────────────────────────────────────
    reset_count:       int   = 0
    accepted_count:    int   = 0
    rejected_count:    int   = 0
    rejection_streak:  int   = 0
    acceptance_streak: int   = 0
    epochs_active:     int   = 0   # incremented by update_sigma() every epoch while born=True

    # ── Visibility / filter participation ────────────────────────────────
    currently_observable: bool  = False
    currently_in_filter:  bool  = False
    current_elevation_deg: float = float('nan')

    # ── Ring buffers (diagnostics) ────────────────────────────────────────
    recent_ph_res: list = field(default_factory=list)   # recent accepted phase residuals [mm]
    recent_rej:    list = field(default_factory=list)   # 0=accept / 1=reject per obs

    # ── Kalman-gain / H-norm proxies (diagnostics) ────────────────────────
    last_H_norm:   float = float('nan')
    last_Kg_norm:  float = float('nan')


# ── Manager ─────────────────────────────────────────────────────────────────

class AmbiguityManager:
    """
    Minimal float-PPP ambiguity tracker.

    Replaces the multi-state lifecycle machine with a simple born/reset flag.
    All STEP-2 / STEP-3 / STEP-4 lifecycle APIs are preserved as stubs so
    ppp_gps_glab.py compiles and runs without modification.
    """

    SNAPSHOT_INTERVAL = 300

    _lineage_counter: int = 0   # kept for snapshot_header() compatibility

    def __init__(self,
                 ring_size: int = 10,
                 snapshot_fh=None,
                 lifecycle_fh=None,                 # ignored — lifecycle trace removed
                 converged_sigma_threshold_mm: float = 500.0):
        self.states: Dict[str, AmbiguityState] = {}
        self._total_resets: int = 0
        self._ring_size: int = ring_size
        self._snapshot_fh  = snapshot_fh
        self._lifecycle_fh = lifecycle_fh           # retained for API; unused
        self._converged_sigma_threshold_mm = converged_sigma_threshold_mm

    # ── Internal helpers ─────────────────────────────────────────────────

    def _ensure(self, sat_id: str, state_index: int) -> AmbiguityState:
        if sat_id not in self.states:
            self.states[sat_id] = AmbiguityState(
                sat_id=sat_id,
                constellation=sat_id[0],
                state_index=state_index,
            )
        return self.states[sat_id]

    def _ring_append(self, buf: list, value, maxlen: int) -> None:
        buf.append(value)
        if len(buf) > maxlen:
            buf.pop(0)

    # ── Slot registration ────────────────────────────────────────────────

    def register_new_slot(self, sat_id: str, state_index: int) -> None:
        """Allocate EKF slot; satellite awaits post-fit birth."""
        self._ensure(sat_id, state_index)

    # ── Birth / reset ────────────────────────────────────────────────────

    def register_birth(self, sat_id: str, state_index: int,
                       epoch: int, sod: float,
                       sigma_m: float = 20.0) -> None:
        """Post-fit ambiguity birth.  Sets born=True; x[ki] is now valid."""
        s = self._ensure(sat_id, state_index)
        if s.initial_sigma < 0.0:
            s.initial_sigma = sigma_m * 1e3
        s.born           = True
        s.born_epoch     = epoch
        s.born_sod       = sod
        s.current_sigma  = sigma_m * 1e3
        s.epochs_active  = 0   # reset counter at each (re-)birth
        assert s.born, f"ASSERT-2 failed: {sat_id} born must be True after register_birth"

    def register_inherited(self, sat_id: str, state_index: int,
                           sigma_m: float) -> None:
        """Inherit state from previous pass (e.g. warm start)."""
        s = self._ensure(sat_id, state_index)
        if s.initial_sigma < 0.0:
            s.initial_sigma = sigma_m * 1e3
        s.born          = True
        s.current_sigma = sigma_m * 1e3

    def register_reset(self, sat_id: str,
                       epoch: int = -1, sod: float = float('nan'),
                       reason: str = 'cycle_slip') -> None:
        """Cycle-slip reset.  Sets born=False; x[ki] will be zeroed by caller."""
        if sat_id not in self.states:
            self._total_resets += 1
            return
        s = self.states[sat_id]
        s.born                = False
        s.reset_count        += 1
        s.rejection_streak    = 0
        s.acceptance_streak   = 0
        s.epochs_active       = 0   # counter resets on cycle-slip
        s.current_sigma       = 20000.0
        s.currently_in_filter = False
        s.recent_ph_res.clear()
        s.recent_rej.clear()
        self._total_resets   += 1

    # ── Activation / deactivation ────────────────────────────────────────

    def activate(self, sat_id: str,
                 epoch: int = -1, sod: float = float('nan')) -> None:
        """Re-activate satellite (gap recovery or post-reset).  Sets born=True."""
        if sat_id in self.states:
            self.states[sat_id].born = True

    def deactivate(self, sat_id: str,
                   epoch: int = -1, sod: float = float('nan')) -> None:
        """Deactivate satellite (covariance sanity failure).  Sets born=False."""
        if sat_id in self.states:
            self.states[sat_id].born = False
            self.states[sat_id].currently_in_filter = False

    # ── Visibility flags ─────────────────────────────────────────────────

    def mark_present(self, sat_id: str, epoch: int, sod: float,
                     elevation_deg: Optional[float] = None) -> None:
        self.mark_observable(sat_id, epoch=epoch, sod=sod,
                             elevation_deg=elevation_deg)

    def mark_missing(self, sat_id: str, sod: float,
                     epoch: int = -1) -> None:
        """Satellite left the visible set this epoch.

        Sets born=False so gap recovery triggers on next appearance.
        State is saved externally by ppp_gps_glab.py before this is called.
        """
        self.mark_unobservable(sat_id, epoch=epoch, sod=sod)
        if sat_id in self.states:
            self.states[sat_id].born = False
            self.states[sat_id].currently_in_filter = False

    def mark_observable(self, sat_id: str,
                        epoch: int = -1, sod: float = float('nan'),
                        elevation_deg: Optional[float] = None) -> None:
        if sat_id not in self.states:
            return
        s = self.states[sat_id]
        s.currently_observable = True
        s.last_seen_epoch = epoch
        s.last_seen_sod   = sod
        if elevation_deg is not None:
            s.current_elevation_deg = elevation_deg

    def mark_unobservable(self, sat_id: str,
                          epoch: int = -1, sod: float = float('nan')) -> None:
        if sat_id in self.states:
            self.states[sat_id].currently_observable = False

    def mark_in_filter(self, sat_id: str,
                       epoch: int = -1, sod: float = float('nan')) -> None:
        if sat_id in self.states:
            self.states[sat_id].currently_in_filter = True

    def mark_out_of_filter(self, sat_id: str,
                           epoch: int = -1, sod: float = float('nan')) -> None:
        if sat_id in self.states:
            self.states[sat_id].currently_in_filter = False

    # ── Observation counters ─────────────────────────────────────────────

    def register_accept(self, sat_id: str, epoch: int, sod: float,
                        residual_mm: Optional[float] = None,
                        H_norm: Optional[float] = None) -> None:
        if sat_id not in self.states:
            return
        s = self.states[sat_id]
        s.accepted_count    += 1
        s.last_accept_epoch  = epoch
        s.last_accept_sod    = sod
        s.rejection_streak   = 0
        s.acceptance_streak += 1
        self._ring_append(s.recent_rej, 0, self._ring_size)
        if residual_mm is not None:
            self._ring_append(s.recent_ph_res, residual_mm, self._ring_size)
        if H_norm is not None:
            s.last_H_norm = H_norm

    def register_reject(self, sat_id: str) -> None:
        if sat_id not in self.states:
            return
        s = self.states[sat_id]
        s.rejected_count    += 1
        s.rejection_streak  += 1
        s.acceptance_streak  = 0
        self._ring_append(s.recent_rej, 1, self._ring_size)

    # ── Sigma / state-index synchronisation ──────────────────────────────

    def update_sigma(self, sat_id: str, sigma_mm: float,
                     epoch: int = -1, sod: float = float('nan')) -> None:
        """Sync current_sigma from live EKF diagonal sqrt(P[ki,ki]) * 1e3."""
        if sat_id in self.states:
            s = self.states[sat_id]
            s.current_sigma = sigma_mm
            if s.born:
                s.epochs_active += 1   # count epochs this ambiguity is live in EKF

    def update_state_index(self, sat_id: str, state_index: int,
                           epoch: int = -1, sod: float = float('nan')) -> None:
        if sat_id not in self.states:
            return
        s = self.states[sat_id]
        s.state_index = state_index
        assert s.state_index >= 0, \
            f"ASSERT-1 failed: {sat_id} active amb has invalid state_index"
        assert s.state_index == state_index, \
            f"ASSERT-7 failed: {sat_id} state_index mismatch after update"

    def update_Kg_norm(self, sat_id: str, kg_norm: float) -> None:
        if sat_id in self.states:
            self.states[sat_id].last_Kg_norm = kg_norm

    # ── Query methods ─────────────────────────────────────────────────────

    def is_active(self, sat_id: str) -> bool:
        s = self.states.get(sat_id)
        return s is not None and s.born

    def is_birth_complete(self, sat_id: str) -> bool:
        """True if x[ki] is validly born and meaningful for residual computation."""
        return self.is_active(sat_id)

    def needs_birth(self, sat_id: str) -> bool:
        """True if the slot exists but x[ki] has not yet been post-fit initialised."""
        s = self.states.get(sat_id)
        return s is not None and not s.born

    def is_observable(self, sat_id: str) -> bool:
        s = self.states.get(sat_id)
        return s is not None and s.currently_observable

    def get_sigma(self, sat_id: str) -> float:
        s = self.states.get(sat_id)
        return s.current_sigma if s else 20000.0

    def get_state_index(self, sat_id: str) -> int:
        s = self.states.get(sat_id)
        return s.state_index if s else -1

    def get_birth_epoch(self, sat_id: str, default: int = -1) -> int:
        s = self.states.get(sat_id)
        return s.born_epoch if s else default

    def get_birth_sigma_mm(self, sat_id: str, default_mm: float = 20000.0) -> float:
        s = self.states.get(sat_id)
        if s is None or s.initial_sigma < 0.0:
            return default_mm
        return s.initial_sigma

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

    def get_recent_ph_res(self, sat_id: str) -> list:
        s = self.states.get(sat_id)
        return list(s.recent_ph_res) if s else []

    def get_recent_rej(self, sat_id: str) -> list:
        s = self.states.get(sat_id)
        return list(s.recent_rej) if s else []

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

    # ── STEP-3 API stubs (backward compatibility; no-ops) ─────────────────

    def get_lifecycle_state(self, sat_id: str) -> Optional[AmbiguityLifecycleState]:
        """Stub — returns ACTIVE or RESET for basic compatibility."""
        s = self.states.get(sat_id)
        if s is None:
            return None
        return AmbiguityLifecycleState.ACTIVE if s.born else AmbiguityLifecycleState.RESET

    def get_lineage_id(self, sat_id: str) -> str:
        return ""   # lineage tracking removed

    def get_generation(self, sat_id: str) -> int:
        return 0    # generation tracking removed

    def get_parent_lineage_id(self, sat_id: str) -> Optional[str]:
        return None  # lineage tracking removed

    def transition_state(self, sat_id: str, new_state,
                         epoch: int, sod: float, reason: str = "") -> None:
        """No-op stub — lifecycle state machine removed."""
        pass

    def run_consistency_assertions(self, epoch: int, sod: float) -> None:
        """No-op stub — lifecycle assertions removed."""
        pass

    def assert_state_indices_match(self, sidx: dict) -> None:
        """No-op stub — step-2 assertion removed."""
        pass

    def assert_state_index_uniqueness(self, sidx: dict) -> None:
        """No-op stub — step-2 assertion removed."""
        pass

    # ── Backward-compat helpers ──────────────────────────────────────────

    def set_active(self, sat_id: str, value: bool) -> None:
        """Direct born-flag setter — backward compat shim."""
        if sat_id in self.states:
            self.states[sat_id].born = bool(value)

    def get(self, sat_id: str, default: bool = False) -> bool:
        return self.is_active(sat_id)

    def items(self):
        """Yield (sat_id, is_active) pairs — used by gap-recovery loop."""
        return ((sid, s.born) for sid, s in self.states.items())

    @property
    def cumulative_resets(self) -> int:
        return self._total_resets

    # ── Snapshot CSV ─────────────────────────────────────────────────────

    @staticmethod
    def snapshot_header() -> str:
        """Header matches original Step-4 format for CSV compatibility."""
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
        """Write one row per known ambiguity to the snapshot CSV."""
        if self._snapshot_fh is None:
            return
        for sat_id in sorted(self.states):
            s  = self.states[sat_id]
            ki = sidx.get(sat_id, -1)
            if ki < 0:
                continue
            try:
                sigma_mm = math.sqrt(max(P[ki, ki], 0.0)) * 1e3
            except Exception:
                sigma_mm = float('nan')
            lifetime = (epoch - s.born_epoch) if s.born_epoch >= 0 else 0
            self._snapshot_fh.write(
                f"{epoch},{sat_id},{ki},"
                f"{1 if s.born else 0},"
                f"{1 if s.currently_observable else 0},"
                f"{'ACTIVE' if s.born else 'RESET'},,,,"
                f"{sigma_mm:.2f},{s.reset_count},"
                f"{s.accepted_count},{s.rejected_count},"
                f"{s.born_epoch},{lifetime},"
                f"{s.rejection_streak},{s.acceptance_streak},"
                f"0,0,nan,0.0000\n"
            )

    @staticmethod
    def lifecycle_trace_header() -> str:
        """Header stub — lifecycle trace file is written but rows are never emitted."""
        return (
            "epoch,sod,sat,"
            "lineage_id,generation,parent_lineage_id,"
            "old_state,new_state,reason,"
            "sigma_mm,state_index,active,observable,in_filter,"
            "gap_length_epochs,convergence_score,rolling_sigma_mm,"
            "rolling_residual_rms_mm,acceptance_ratio,"
            "continuous_active_epochs,continuous_filter_epochs\n"
        )


# ── Convenience factories (unchanged API) ────────────────────────────────────

def open_snapshot_csv(directory: str, label: str = 'FWD'):
    path = os.path.join(directory, f'ambiguity_manager_snapshot_{label}.csv')
    fh = open(path, 'w')
    fh.write(AmbiguityManager.snapshot_header())
    return fh


def open_lifecycle_trace_csv(directory: str, label: str = 'GE'):
    path = os.path.join(directory, f'ambiguity_lifecycle_trace_{label}.csv')
    fh = open(path, 'w')
    fh.write(AmbiguityManager.lifecycle_trace_header())
    return fh
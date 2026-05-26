"""
rebirth_forensic.py — Surgical Ambiguity Rebirth Causality Audit
================================================================

OBJECTIVE
---------
Isolate the EXACT mechanism by which late ambiguity rebirths perpetuate
the wrong IF-combination manifold after startup.  This module is pure
instrumentation — zero EKF/model changes.

THEORY OF OPERATION
-------------------
Every ambiguity birth/rebirth is characterised by five quantities:

    (A) Receiver clock state at birth          → rec_clk_pre, rec_clk_post, dclk
    (B) Ambiguity prior covariance at birth    → birth_sigma, P[ki,ki] after reset
    (C) Code / phase consistency at birth      → PIF vs LIFc alignment
    (D) Cycle-slip detector validity           → slip_reason, dGF, dMW
    (E) Ambient filter state                   → common_mode_ratio, code_rms, phase_rms

The module records a ±CONTEXT_HALF epoch window around every rebirth,
then classifies the dominant cause using quantitative thresholds.

ABORT ASSERTIONS
----------------
All four abort conditions raise RuntimeError with a full context dump
so the traceback points to the corrupting epoch, not the fatal one:

    |N_birth|   > ABORT_N_BIRTH_M     (default 20 m)
    |dclk_epoch| > ABORT_DCLK_M       (default 15 m)
    code_rms    > ABORT_CODE_RMS_CONV  (default 15 m, after convergence)
    cm_ratio    > ABORT_CM_RATIO       (default 25)

INTEGRATION — five call sites in ppp_gps_glab.py
-------------------------------------------------
See PATCH_GUIDE at the bottom of this file for exact line numbers and
copy-paste snippets.

    1. Import + construction  (once, near top of _ppp_pass)
    2. note_slip(sat, sod, dGF, dMW)  — inside cycle-slip branch
    3. record_epoch(epoch_stats)      — end of each epoch (after code_rms)
    4. record_rebirth(rebirth_stats)  — inside _newborn_pending loop
    5. summarize()                    — before _ppp_pass return
"""

from __future__ import annotations

import math
import sys
from collections import deque
from typing import Optional, List, Dict, Any

# ─────────────────────────────────────────────────────────────────────────────
# Thresholds
# ─────────────────────────────────────────────────────────────────────────────

ABORT_N_BIRTH_M       = 20.0   # m   — hard abort if |N_birth| exceeds this
ABORT_DCLK_M          = 15.0   # m   — hard abort if |Δclk| this epoch exceeds
ABORT_CODE_RMS_CONV   = 15.0   # m   — hard abort if code_rms > this after convergence
ABORT_CM_RATIO        = 25.0   # —   — hard abort if common_mode_ratio exceeds this
CONV_EPOCH_THRESHOLD  = 60     # ep  — "after convergence" epoch count
CONTEXT_HALF          = 5      # ep  — half-width of ±N context window

# Classifier thresholds
CLF_CLOCK_DCLK_M      = 2.0    # m   — dclk this large → CLOCK_DRIVEN
CLF_CM_RATIO          = 5.0    # —   — cm_ratio this large → COMMON_MODE_EVENT
CLF_PHASE_RMS_CLEAN   = 0.050  # m   — phase_rms below this → SLIP_DRIVEN candidate
CLF_CODE_RMS_INFLATED = 3.0    # m   — code_rms above this (in m)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(v, scale=1.0, prec=3):
    """Format a float, returning 'nan' for non-finite."""
    if v is None or not math.isfinite(float(v)):
        return 'nan'
    return f'{float(v) * scale:+.{prec}f}'


def _mean(lst):
    return sum(lst) / len(lst) if lst else float('nan')


def _std(lst):
    if len(lst) < 2:
        return float('nan')
    mu = _mean(lst)
    return math.sqrt(sum((x - mu) ** 2 for x in lst) / len(lst))


def _rms(lst):
    return math.sqrt(sum(x * x for x in lst) / len(lst)) if lst else float('nan')


def _sign(v):
    if v is None or not math.isfinite(float(v)):
        return 0
    return 1 if float(v) > 0 else -1


# ─────────────────────────────────────────────────────────────────────────────
# EpochRecord — compact per-epoch snapshot stored in the rolling buffer
# ─────────────────────────────────────────────────────────────────────────────

class EpochRecord:
    """Minimal per-epoch stats for the ±CONTEXT_HALF window."""

    __slots__ = (
        'nproc', 'sod',
        'rec_clk_m',          # post-EKF receiver clock (m)
        'dclk_m',             # signed clock update this epoch (m)
        'code_rms_mm',        # code RMS (mm); nan until set
        'phase_rms_mm',       # phase RMS accepted sats (mm); nan until set
        'cm_ratio',           # common-mode ratio (|mean_ph|/std_ph)
        'mean_ph_innov_mm',   # mean pre-EKF phase innovation (mm)
        'std_ph_innov_mm',    # std  pre-EKF phase innovation (mm)
        'mean_code_innov_mm', # mean pre-EKF code  innovation (mm)
        'n_accepted_phase',   # number of accepted phase rows
        'all_reject',         # True if zero phase rows were accepted
        'innov_norm',         # total innovation norm
        'slipped_sats',       # frozenset of satellite IDs that slipped this epoch
        'n_sats',             # total satellites in geometry
    )

    def __init__(self, nproc, sod):
        self.nproc             = nproc
        self.sod               = sod
        self.rec_clk_m         = float('nan')
        self.dclk_m            = float('nan')
        self.code_rms_mm       = float('nan')
        self.phase_rms_mm      = float('nan')
        self.cm_ratio          = float('nan')
        self.mean_ph_innov_mm  = float('nan')
        self.std_ph_innov_mm   = float('nan')
        self.mean_code_innov_mm= float('nan')
        self.n_accepted_phase  = 0
        self.all_reject        = True
        self.innov_norm        = float('nan')
        self.slipped_sats      = frozenset()
        self.n_sats            = 0

    def to_dict(self):
        return {s: getattr(self, s) for s in self.__slots__}


# ─────────────────────────────────────────────────────────────────────────────
# RebirthRecord — full characterisation of one rebirth event
# ─────────────────────────────────────────────────────────────────────────────

class RebirthRecord:
    """Everything known about a single ambiguity birth/rebirth at its epoch."""

    __slots__ = (
        # identity
        'sat', 'sod', 'nproc', 'slip_reason',
        # clock state
        'rec_clk_pre_mm', 'rec_clk_post_mm', 'dclk_epoch_mm',
        # ambiguity
        'N_postfit_mm', 'N_prefit_mm', 'birth_sigma_mm',
        'old_N_mm',           # previous ambiguity value if rebirth (nan if first birth)
        'old_sigma_mm',       # previous sigma before reset (nan if first birth)
        # observation geometry
        'PIF_mm', 'LIFc_mm', 'rp_prefit_mm', 'rp_postfit_mm',
        'code_res_mm', 'phase_res_mm',
        'elevation_deg',
        # epoch environment
        'code_rms_mm',        # computed after birth loop (filled via finalize)
        'phase_rms_mm',       # computed after birth loop (filled via finalize)
        'cm_ratio',           # common-mode ratio (filled via finalize)
        'mean_ph_innov_mm',   # pre-EKF mean phase innovation (mm)
        'n_accepted_phase',   # number of accepted phase rows
        'ph_innov_sign_majority', # +1 / -1 / 0 = majority sign of pre-EKF phase innovations
        'n_sats',
        # classification
        'classification',
        # slip indicator
        'slip_detected_this_epoch',  # True if this sat was in slip set this epoch
        # generation number (monotone counter per satellite)
        'generation',
    )

    def __init__(self):
        for s in self.__slots__:
            object.__setattr__(self, s, float('nan')
                               if s not in ('sat', 'slip_reason', 'classification')
                               else '')
        self.slip_detected_this_epoch = False
        self.generation = 0
        self.n_accepted_phase = 0
        self.ph_innov_sign_majority = 0


# ─────────────────────────────────────────────────────────────────────────────
# RebirthForensic — main audit engine
# ─────────────────────────────────────────────────────────────────────────────

class RebirthForensic:
    """
    Surgical rebirth causality auditor.

    Usage pattern in _ppp_pass:
    -----------------------------------------------------------------------
    forensic = RebirthForensic(fh=sys.stdout, rebirth_csv_fh=<open file>)

    # In satellite loop, slip branch:
    forensic.note_slip(sid, sod, dGF=dGF_m, dMW=dMW_cyc)

    # At epoch end, after code_rms / phase_rms / cm_ratio computed:
    forensic.record_epoch({
        'nproc': nproc, 'sod': sod,
        'rec_clk_m': x[3], 'dclk_m': x[3] - x_before[3],
        'code_rms_mm': code_rms,          # mm
        'phase_rms_mm': phase_rms,        # mm
        'cm_ratio': _au_cm_ratio,
        'mean_ph_innov_mm': _mean_ph * 1e3,   # pre-EKF
        'std_ph_innov_mm':  _std_ph  * 1e3,
        'mean_code_innov_mm': _mean_cod * 1e3,
        'n_accepted_phase': len(_accepted_phase_sids),
        'all_reject': len(_accepted_phase_sids) == 0,
        'innov_norm': _innov_norm,
        'n_sats': len(geom),
    })

    # In birth loop, for each newborn:
    forensic.record_rebirth({
        'sat': _nsid, 'sod': _nsod, 'nproc': nproc,
        'slip_reason': 'cycle_slip' (or 'first_birth', 'gap_recovery'),
        'rec_clk_pre_mm':  x_before[3] * 1e3,
        'rec_clk_post_mm': x[3] * 1e3,
        'dclk_epoch_mm':   _dclk_birth_mm,
        'N_postfit_mm':    _n_postfit * 1e3,
        'N_prefit_mm':     _n_prefit  * 1e3,
        'birth_sigma_mm':  20000.0,
        'old_N_mm':        <x[ki] before reset, or nan>,
        'old_sigma_mm':    <sigma before reset, or nan>,
        'PIF_mm':          _nm['PIF'] * 1e3,
        'LIFc_mm':         _nLIFc * 1e3,
        'rp_prefit_mm':    _rp_prefit * 1e3,
        'rp_postfit_mm':   _rp_post   * 1e3,
        'code_res_mm':     (_nm['PIF'] - _rp_post) * 1e3,
        'phase_res_mm':    0.0,          # always 0 at post-fit birth by construction
        'elevation_deg':   math.degrees(_nm['el']),
        'mean_ph_innov_mm': _mean_ph * 1e3,   # pre-EKF value already available
        'n_accepted_phase': len(_accepted_phase_sids),
        'ph_innov_signs':   _ph_innov_list,   # list in metres — module computes majority
    })

    # Before _ppp_pass return:
    forensic.summarize(nproc_total=nproc)
    -----------------------------------------------------------------------
    """

    CONTEXT_HALF = CONTEXT_HALF

    def __init__(self,
                 fh=None,
                 rebirth_csv_fh=None,
                 conv_epoch: int = CONV_EPOCH_THRESHOLD):
        self._fh           = fh or sys.stdout
        self._csv_fh       = rebirth_csv_fh
        self._conv_epoch   = conv_epoch

        # Rolling epoch buffer (pre-context)
        self._epoch_buf: deque = deque(maxlen=CONTEXT_HALF * 2 + 4)

        # Pending rebirths awaiting post-context completion
        # each entry: {'rec': RebirthRecord, 'pre': [EpochRecord], 'post': []}
        self._pending: List[Dict] = []

        # Full rebirth list for summary
        self._all_rebirths: List[RebirthRecord] = []

        # Per-epoch transient: slips detected this epoch
        self._slip_set: set = set()
        self._slip_details: Dict[str, Dict] = {}   # sat → {dGF, dMW}

        # Generation counter per satellite
        self._generation: Dict[str, int] = {}

        # Current epoch record (filled incrementally)
        self._cur_epoch: Optional[EpochRecord] = None
        self._cur_nproc: int = -1

        # Write CSV header
        if self._csv_fh is not None:
            self._csv_fh.write(self._csv_header())

    # ── Public API ──────────────────────────────────────────────────────────

    def note_slip(self, sat: str, sod: float,
                  dGF: float = float('nan'),
                  dMW: float = float('nan')) -> None:
        """Call inside cycle-slip branch before reset, once per sat per epoch."""
        self._slip_set.add(sat)
        self._slip_details[sat] = {'dGF': dGF, 'dMW': dMW, 'sod': sod}

    def record_epoch(self, stats: Dict[str, Any]) -> None:
        """
        Call once per epoch, AFTER code_rms / phase_rms / cm_ratio are computed.
        This finalises any pending rebirths that now have enough post-context.
        """
        er = EpochRecord(stats['nproc'], stats['sod'])
        er.rec_clk_m          = stats.get('rec_clk_m', float('nan'))
        er.dclk_m             = stats.get('dclk_m',    float('nan'))
        er.code_rms_mm        = stats.get('code_rms_mm', float('nan'))
        er.phase_rms_mm       = stats.get('phase_rms_mm', float('nan'))
        er.cm_ratio           = stats.get('cm_ratio', float('nan'))
        er.mean_ph_innov_mm   = stats.get('mean_ph_innov_mm', float('nan'))
        er.std_ph_innov_mm    = stats.get('std_ph_innov_mm',  float('nan'))
        er.mean_code_innov_mm = stats.get('mean_code_innov_mm', float('nan'))
        er.n_accepted_phase   = stats.get('n_accepted_phase', 0)
        er.all_reject         = stats.get('all_reject', True)
        er.innov_norm         = stats.get('innov_norm', float('nan'))
        er.slipped_sats       = frozenset(self._slip_set)
        er.n_sats             = stats.get('n_sats', 0)
        self._epoch_buf.append(er)
        self._cur_epoch = er
        self._cur_nproc = er.nproc

        # Check abort: code_rms after convergence
        if (er.nproc >= self._conv_epoch
                and math.isfinite(er.code_rms_mm)
                and er.code_rms_mm > ABORT_CODE_RMS_CONV * 1e3):
            self._abort_code_rms(er)

        # Check abort: cm_ratio
        if math.isfinite(er.cm_ratio) and er.cm_ratio > ABORT_CM_RATIO:
            self._abort_cm_ratio(er)

        # Collect post-context for any pending rebirths
        for entry in self._pending:
            entry['post'].append(er)

        # Flush completed rebirths (have CONTEXT_HALF post-epochs)
        self._flush_completed()

        # Reset per-epoch transients AFTER flushing (slip_set needed for classification)
        self._slip_set.clear()
        self._slip_details.clear()

    def record_rebirth(self, stats: Dict[str, Any]) -> None:
        """
        Call inside _newborn_pending loop for every ambiguity born this epoch.
        The epoch buffer must already have the current epoch's pre-EKF data;
        record_epoch should be called AFTER this at the epoch's end.
        """
        sat = stats['sat']
        nproc = stats['nproc']

        # Increment generation counter
        self._generation[sat] = self._generation.get(sat, 0) + 1

        rec = RebirthRecord()
        rec.sat               = sat
        rec.sod               = stats.get('sod', float('nan'))
        rec.nproc             = nproc
        rec.slip_reason       = stats.get('slip_reason', 'unknown')
        rec.rec_clk_pre_mm    = stats.get('rec_clk_pre_mm', float('nan'))
        rec.rec_clk_post_mm   = stats.get('rec_clk_post_mm', float('nan'))
        rec.dclk_epoch_mm     = stats.get('dclk_epoch_mm', float('nan'))
        rec.N_postfit_mm      = stats.get('N_postfit_mm', float('nan'))
        rec.N_prefit_mm       = stats.get('N_prefit_mm', float('nan'))
        rec.birth_sigma_mm    = stats.get('birth_sigma_mm', 20000.0)
        rec.old_N_mm          = stats.get('old_N_mm', float('nan'))
        rec.old_sigma_mm      = stats.get('old_sigma_mm', float('nan'))
        rec.PIF_mm            = stats.get('PIF_mm', float('nan'))
        rec.LIFc_mm           = stats.get('LIFc_mm', float('nan'))
        rec.rp_prefit_mm      = stats.get('rp_prefit_mm', float('nan'))
        rec.rp_postfit_mm     = stats.get('rp_postfit_mm', float('nan'))
        rec.code_res_mm       = stats.get('code_res_mm', float('nan'))
        rec.phase_res_mm      = stats.get('phase_res_mm', 0.0)  # 0 by construction
        rec.elevation_deg     = stats.get('elevation_deg', float('nan'))
        rec.mean_ph_innov_mm  = stats.get('mean_ph_innov_mm', float('nan'))
        rec.n_accepted_phase  = stats.get('n_accepted_phase', 0)
        rec.n_sats            = stats.get('n_sats', 0)
        rec.slip_detected_this_epoch = (sat in self._slip_set)
        rec.generation        = self._generation[sat]

        # Compute phase-innovation sign majority
        ph_signs_raw = stats.get('ph_innov_signs', [])  # raw metres list
        if ph_signs_raw:
            n_pos = sum(1 for v in ph_signs_raw if v > 0)
            n_neg = sum(1 for v in ph_signs_raw if v < 0)
            if n_pos > n_neg:
                rec.ph_innov_sign_majority = +1
            elif n_neg > n_pos:
                rec.ph_innov_sign_majority = -1
            else:
                rec.ph_innov_sign_majority = 0
        else:
            rec.ph_innov_sign_majority = 0

        # code_rms / phase_rms / cm_ratio not yet available (birth runs before
        # those are computed) — filled in via finalize_rebirth_epoch below
        rec.code_rms_mm  = float('nan')
        rec.phase_rms_mm = float('nan')
        rec.cm_ratio     = float('nan')

        # Hard assertions (can abort here before classification)
        self._assert_rebirth(rec)

        # Classify
        rec.classification = self._classify(rec)

        # Write CSV row (partial — code_rms/phase_rms will be nan until finalised)
        if self._csv_fh is not None:
            self._write_csv_row(rec)

        # Snapshot pre-context (last CONTEXT_HALF records before this epoch)
        pre_ctx = list(self._epoch_buf)[-self.CONTEXT_HALF:]

        # Queue for post-context collection
        self._pending.append({
            'rec': rec,
            'pre': pre_ctx,
            'post': [],
        })
        self._all_rebirths.append(rec)

    def finalize_rebirth_epoch(self, code_rms_mm: float,
                               phase_rms_mm: float,
                               cm_ratio: float) -> None:
        """
        Back-fill the current epoch's code/phase RMS and cm_ratio into any
        rebirths that occurred this epoch (before those values were computed).
        Call ONCE per epoch, immediately after you compute those values.
        """
        for entry in self._pending:
            rec = entry['rec']
            if rec.nproc == self._cur_nproc:
                rec.code_rms_mm  = code_rms_mm
                rec.phase_rms_mm = phase_rms_mm
                rec.cm_ratio     = cm_ratio
                # Re-classify with full data
                rec.classification = self._classify(rec)

    def summarize(self, nproc_total: int = -1) -> None:
        """Print pass-end summary.  Flush any remaining pending context windows."""
        # Force-flush remaining pending rebirths
        for entry in self._pending:
            self._print_rebirth_report(entry['rec'], entry['pre'], entry['post'],
                                       force=True)
        self._pending.clear()

        total = len(self._all_rebirths)
        fh = self._fh
        fh.write('\n')
        fh.write('=' * 72 + '\n')
        fh.write('[REBIRTH-SUMMARY] Pass-end ambiguity rebirth causality report\n')
        fh.write('=' * 72 + '\n')
        fh.write(f'  total_rebirths          = {total}\n')
        if total == 0:
            fh.write('  (no rebirths recorded)\n')
            fh.write('=' * 72 + '\n\n')
            return

        N_vals   = [r.N_postfit_mm for r in self._all_rebirths
                    if math.isfinite(r.N_postfit_mm)]
        dclk_vals= [r.dclk_epoch_mm for r in self._all_rebirths
                    if math.isfinite(r.dclk_epoch_mm)]
        cm_vals  = [r.cm_ratio for r in self._all_rebirths
                    if math.isfinite(r.cm_ratio)]
        code_vals= [r.code_rms_mm for r in self._all_rebirths
                    if math.isfinite(r.code_rms_mm)]
        ph_vals  = [r.phase_rms_mm for r in self._all_rebirths
                    if math.isfinite(r.phase_rms_mm)]

        if N_vals:
            fh.write(f'  mean |N_birth|          = {_mean([abs(v) for v in N_vals]):+.1f} mm\n')
            fh.write(f'  max  |N_birth|          = {max(abs(v) for v in N_vals):.1f} mm\n')
        if dclk_vals:
            fh.write(f'  mean |dclk_epoch|       = {_mean([abs(v) for v in dclk_vals]):.1f} mm\n')
            fh.write(f'  max  |dclk_epoch|       = {max(abs(v) for v in dclk_vals):.1f} mm\n')

        n_high_cm = sum(1 for r in self._all_rebirths
                        if math.isfinite(r.cm_ratio) and r.cm_ratio > CLF_CM_RATIO)
        n_high_clk= sum(1 for r in self._all_rebirths
                        if math.isfinite(r.dclk_epoch_mm)
                        and abs(r.dclk_epoch_mm) > CLF_CLOCK_DCLK_M * 1e3)
        n_phase_clean_code_dirty = sum(
            1 for r in self._all_rebirths
            if math.isfinite(r.phase_rms_mm) and math.isfinite(r.code_rms_mm)
            and r.phase_rms_mm < CLF_PHASE_RMS_CLEAN * 1e3
            and r.code_rms_mm  > CLF_CODE_RMS_INFLATED * 1e3
        )
        fh.write(f'  frac born high cm_ratio = {n_high_cm}/{total}'
                 f'  ({100*n_high_cm/total:.0f}%)\n')
        fh.write(f'  frac born high |dclk|   = {n_high_clk}/{total}'
                 f'  ({100*n_high_clk/total:.0f}%)\n')
        fh.write(f'  frac phase<50mm code>3m = {n_phase_clean_code_dirty}/{total}'
                 f'  ({100*n_phase_clean_code_dirty/total:.0f}%)\n')

        # Correlation between |dclk| and |N_birth| (Pearson)
        pairs = [(abs(r.dclk_epoch_mm), abs(r.N_postfit_mm))
                 for r in self._all_rebirths
                 if math.isfinite(r.dclk_epoch_mm) and math.isfinite(r.N_postfit_mm)]
        if len(pairs) >= 3:
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            mx, my = _mean(xs), _mean(ys)
            num = sum((xi - mx) * (yi - my) for xi, yi in zip(xs, ys))
            denom = math.sqrt(
                sum((xi - mx) ** 2 for xi in xs) *
                sum((yi - my) ** 2 for yi in ys)
            )
            r_corr = num / denom if denom > 1e-12 else float('nan')
            fh.write(f'  corr(|dclk|, |N_birth|) = {r_corr:+.4f}\n')

        # Classification breakdown
        clf_counts: Dict[str, int] = {}
        for r in self._all_rebirths:
            clf_counts[r.classification] = clf_counts.get(r.classification, 0) + 1
        fh.write('  classification breakdown:\n')
        for clf, cnt in sorted(clf_counts.items(), key=lambda kv: -kv[1]):
            fh.write(f'    {clf:<26}  {cnt}\n')

        # Top-10 worst rebirths by |N_birth|
        sorted_rb = sorted(self._all_rebirths,
                           key=lambda r: abs(r.N_postfit_mm)
                           if math.isfinite(r.N_postfit_mm) else 0,
                           reverse=True)
        fh.write('\n  TOP-10 worst rebirths by |N_birth|:\n')
        fh.write(f'  {"sat":<5} {"sod":>7} {"ep":>5} {"N_mm":>10}'
                 f' {"dclk_mm":>10} {"cm_ratio":>9}'
                 f' {"code_rms":>9} {"ph_rms":>8}'
                 f' {"clf":<26} {"gen":>4}\n')
        fh.write('  ' + '-' * 104 + '\n')
        for r in sorted_rb[:10]:
            fh.write(
                f'  {r.sat:<5} {r.sod:>7.0f} {r.nproc:>5}'
                f' {_fmt(r.N_postfit_mm, prec=1):>10}'
                f' {_fmt(r.dclk_epoch_mm, prec=1):>10}'
                f' {_fmt(r.cm_ratio, scale=1, prec=2):>9}'
                f' {_fmt(r.code_rms_mm, prec=1):>9}'
                f' {_fmt(r.phase_rms_mm, prec=1):>8}'
                f'  {r.classification:<26} {r.generation:>4}\n'
            )
        fh.write('=' * 72 + '\n\n')
        fh.flush()

    # ── Private helpers ──────────────────────────────────────────────────────

    def _classify(self, rec: RebirthRecord) -> str:
        """
        Return one of five labels.  Multiple causes may apply; the most
        discriminating one wins (priority order as listed).

        CLOCK_DRIVEN         : |dclk_epoch| > 2 m — EKF absorbed a clock
                               transient this epoch; birth used corrupted clock.
        COMMON_MODE_EVENT    : cm_ratio > 5 — all-satellite phase bias;
                               LIFc are coherently shifted from rho.
        SLIP_DRIVEN          : slip detected for this sat, others are clean
                               (small cm_ratio, small phase_rms).
        GEOMETRY_DRIVEN      : first birth or large gap; no prior to constrain.
        ISOLATED_SAT_EVENT   : large |N_birth| for this sat but other sats
                               have clean phase residuals.
        """
        dclk_m = (abs(rec.dclk_epoch_mm) / 1e3
                  if math.isfinite(rec.dclk_epoch_mm) else 0.0)
        cm     = rec.cm_ratio if math.isfinite(rec.cm_ratio) else 0.0

        if dclk_m > CLF_CLOCK_DCLK_M:
            return 'CLOCK_DRIVEN'
        if cm > CLF_CM_RATIO:
            return 'COMMON_MODE_EVENT'
        if rec.slip_detected_this_epoch and rec.generation > 1:
            # Phase RMS from OTHER sats — use epoch phase_rms as proxy
            ph_rms_m = (rec.phase_rms_mm / 1e3
                        if math.isfinite(rec.phase_rms_mm) else float('nan'))
            if math.isfinite(ph_rms_m) and ph_rms_m < CLF_PHASE_RMS_CLEAN:
                return 'SLIP_DRIVEN'
        if rec.generation == 1 or not math.isfinite(rec.old_N_mm):
            return 'GEOMETRY_DRIVEN'
        return 'ISOLATED_SAT_EVENT'

    def _flush_completed(self) -> None:
        still_pending = []
        for entry in self._pending:
            if len(entry['post']) >= self.CONTEXT_HALF:
                self._print_rebirth_report(entry['rec'], entry['pre'], entry['post'])
            else:
                still_pending.append(entry)
        self._pending = still_pending

    def _print_rebirth_report(self, rec: RebirthRecord,
                               pre: list, post: list,
                               force: bool = False) -> None:
        fh = self._fh
        lbl = '[REBIRTH-FORENSIC]'
        sep = '-' * 68

        fh.write('\n' + sep + '\n')
        fh.write(f'{lbl}  sat={rec.sat}  SOD={rec.sod:.0f}'
                 f'  epoch={rec.nproc}  gen={rec.generation}'
                 f'  reason={rec.slip_reason}\n')
        fh.write(f'{lbl}  classification = {rec.classification}\n')
        fh.write(sep + '\n')

        # ── Rebirth primary stats ─────────────────────────────────────────
        fh.write(f'  rec_clk_pre       = {_fmt(rec.rec_clk_pre_mm, prec=3)} mm\n')
        fh.write(f'  rec_clk_post      = {_fmt(rec.rec_clk_post_mm, prec=3)} mm\n')
        fh.write(f'  dclk_epoch        = {_fmt(rec.dclk_epoch_mm, prec=3)} mm'
                 f'  (|Δclk|={abs(rec.dclk_epoch_mm)/1e3:.3f} m)\n')
        fh.write(f'  old_N             = {_fmt(rec.old_N_mm, prec=1)} mm'
                 f'  old_sigma={_fmt(rec.old_sigma_mm, prec=1)} mm\n')
        fh.write(f'  N_postfit         = {_fmt(rec.N_postfit_mm, prec=1)} mm'
                 f'  ({rec.N_postfit_mm/1e3:.3f} m)\n')
        fh.write(f'  N_prefit          = {_fmt(rec.N_prefit_mm, prec=1)} mm\n')
        fh.write(f'  birth_sigma       = {_fmt(rec.birth_sigma_mm, prec=1)} mm\n')
        fh.write(f'  PIF               = {_fmt(rec.PIF_mm, prec=1)} mm\n')
        fh.write(f'  LIFc              = {_fmt(rec.LIFc_mm, prec=1)} mm\n')
        fh.write(f'  rp_prefit         = {_fmt(rec.rp_prefit_mm, prec=1)} mm\n')
        fh.write(f'  rp_postfit        = {_fmt(rec.rp_postfit_mm, prec=1)} mm\n')
        fh.write(f'  code_res_at_birth = {_fmt(rec.code_res_mm, prec=1)} mm\n')
        fh.write(f'  phase_res_at_birth= {_fmt(rec.phase_res_mm, prec=1)} mm'
                 f'  (0 by post-fit construction)\n')
        fh.write(f'  elevation         = {_fmt(rec.elevation_deg, scale=1, prec=2)} deg\n')
        fh.write(f'  n_accepted_phase  = {rec.n_accepted_phase}'
                 f'  ph_innov_sign_maj={rec.ph_innov_sign_majority:+d}\n')
        fh.write(f'  mean_ph_innov     = {_fmt(rec.mean_ph_innov_mm, prec=3)} mm\n')
        fh.write(f'  code_rms_epoch    = {_fmt(rec.code_rms_mm, prec=1)} mm\n')
        fh.write(f'  phase_rms_epoch   = {_fmt(rec.phase_rms_mm, prec=3)} mm\n')
        fh.write(f'  cm_ratio_epoch    = {_fmt(rec.cm_ratio, scale=1, prec=3)}\n')
        fh.write(f'  slip_detected     = {rec.slip_detected_this_epoch}\n')
        fh.write(f'  n_sats            = {rec.n_sats}\n')

        # ── Pre-context ───────────────────────────────────────────────────
        fh.write(f'\n  {lbl}  PRE-CONTEXT (last {len(pre)} epochs before rebirth)\n')
        self._print_context_table(pre, marker='PRE ', fh=fh)

        # ── Post-context ──────────────────────────────────────────────────
        if post:
            fh.write(f'\n  {lbl}  POST-CONTEXT (next {len(post)} epochs after rebirth)\n')
            self._print_context_table(post, marker='POST', fh=fh)
        elif force:
            fh.write(f'\n  {lbl}  POST-CONTEXT: pass ended before {self.CONTEXT_HALF} post-epochs\n')

        fh.write(sep + '\n')
        fh.flush()

    @staticmethod
    def _print_context_table(ctx_list, marker: str, fh) -> None:
        hdr = (f'  {"":4} {"ep":>5} {"sod":>7} {"clk_mm":>10}'
               f' {"dclk_mm":>10} {"code_rms":>9} {"ph_rms":>8}'
               f' {"cm_ratio":>9} {"n_ph":>5} {"all_rej":>7}'
               f' {"innov_n":>8} {"slipped":}\n')
        fh.write(hdr)
        for er in ctx_list:
            cm_s = f'{er.cm_ratio:.3f}' if math.isfinite(er.cm_ratio) else 'nan'
            slip_s = ','.join(sorted(er.slipped_sats)) if er.slipped_sats else '-'
            fh.write(
                f'  {marker:4} {er.nproc:>5} {er.sod:>7.0f}'
                f' {_fmt(er.rec_clk_m, scale=1e3, prec=1):>10}'
                f' {_fmt(er.dclk_m,    scale=1e3, prec=1):>10}'
                f' {_fmt(er.code_rms_mm, prec=1):>9}'
                f' {_fmt(er.phase_rms_mm, prec=2):>8}'
                f' {cm_s:>9}'
                f' {er.n_accepted_phase:>5}'
                f' {"Y" if er.all_reject else "N":>7}'
                f' {_fmt(er.innov_norm, scale=1, prec=2):>8}'
                f'  {slip_s}\n'
            )

    # ── Hard abort helpers ───────────────────────────────────────────────────

    def _assert_rebirth(self, rec: RebirthRecord) -> None:
        """Abort with full dump if any hard assertion fires."""
        N_m     = abs(rec.N_postfit_mm) / 1e3 if math.isfinite(rec.N_postfit_mm) else 0.
        dclk_m  = abs(rec.dclk_epoch_mm) / 1e3 if math.isfinite(rec.dclk_epoch_mm) else 0.

        if N_m > ABORT_N_BIRTH_M:
            msg = (f'[REBIRTH-FORENSIC-ABORT] |N_birth|={N_m:.3f} m > {ABORT_N_BIRTH_M} m'
                   f'  sat={rec.sat}  SOD={rec.sod:.0f}')
            self._fh.write('\n' + '!' * 72 + '\n')
            self._fh.write(msg + '\n')
            self._fh.write(f'  rec_clk_pre  = {_fmt(rec.rec_clk_pre_mm, prec=3)} mm\n')
            self._fh.write(f'  rec_clk_post = {_fmt(rec.rec_clk_post_mm, prec=3)} mm\n')
            self._fh.write(f'  dclk_epoch   = {_fmt(rec.dclk_epoch_mm, prec=3)} mm\n')
            self._fh.write(f'  N_postfit    = {_fmt(rec.N_postfit_mm, prec=1)} mm\n')
            self._fh.write(f'  N_prefit     = {_fmt(rec.N_prefit_mm, prec=1)} mm\n')
            self._fh.write(f'  rp_postfit   = {_fmt(rec.rp_postfit_mm, prec=1)} mm\n')
            self._fh.write(f'  LIFc         = {_fmt(rec.LIFc_mm, prec=1)} mm\n')
            # Print current context window
            ctx = list(self._epoch_buf)[-self.CONTEXT_HALF:]
            if ctx:
                self._fh.write('  Pre-context at abort:\n')
                self._print_context_table(ctx, marker='PRE ', fh=self._fh)
            self._fh.write('!' * 72 + '\n')
            self._fh.flush()
            raise RuntimeError(msg)

        if dclk_m > ABORT_DCLK_M:
            msg = (f'[REBIRTH-FORENSIC-ABORT] |dclk_epoch|={dclk_m:.3f} m > {ABORT_DCLK_M} m'
                   f'  sat={rec.sat}  SOD={rec.sod:.0f}')
            self._fh.write('\n' + '!' * 72 + '\n')
            self._fh.write(msg + '\n')
            ctx = list(self._epoch_buf)[-self.CONTEXT_HALF:]
            if ctx:
                self._print_context_table(ctx, marker='PRE ', fh=self._fh)
            self._fh.write('!' * 72 + '\n')
            self._fh.flush()
            raise RuntimeError(msg)

    def _abort_code_rms(self, er: EpochRecord) -> None:
        msg = (f'[REBIRTH-FORENSIC-ABORT] code_rms={er.code_rms_mm:.1f} mm'
               f' > {ABORT_CODE_RMS_CONV * 1e3:.0f} mm  after convergence'
               f'  SOD={er.sod:.0f}  epoch={er.nproc}')
        self._fh.write('\n' + '!' * 72 + '\n')
        self._fh.write(msg + '\n')
        ctx = list(self._epoch_buf)
        if ctx:
            self._print_context_table(ctx[-self.CONTEXT_HALF:], 'PRE ', self._fh)
        self._fh.write('!' * 72 + '\n')
        self._fh.flush()
        raise RuntimeError(msg)

    def _abort_cm_ratio(self, er: EpochRecord) -> None:
        msg = (f'[REBIRTH-FORENSIC-ABORT] cm_ratio={er.cm_ratio:.2f}'
               f' > {ABORT_CM_RATIO}  SOD={er.sod:.0f}  epoch={er.nproc}')
        self._fh.write('\n' + '!' * 72 + '\n')
        self._fh.write(msg + '\n')
        ctx = list(self._epoch_buf)
        if ctx:
            self._print_context_table(ctx[-self.CONTEXT_HALF:], 'PRE ', self._fh)
        self._fh.write('!' * 72 + '\n')
        self._fh.flush()
        raise RuntimeError(msg)

    # ── CSV I/O ─────────────────────────────────────────────────────────────

    @staticmethod
    def _csv_header() -> str:
        return (
            'sat,sod,epoch,generation,slip_reason,'
            'rec_clk_pre_mm,rec_clk_post_mm,dclk_epoch_mm,'
            'N_postfit_mm,N_prefit_mm,birth_sigma_mm,'
            'old_N_mm,old_sigma_mm,'
            'PIF_mm,LIFc_mm,rp_prefit_mm,rp_postfit_mm,'
            'code_res_mm,phase_res_mm,'
            'elevation_deg,'
            'code_rms_mm,phase_rms_mm,cm_ratio,'
            'mean_ph_innov_mm,n_accepted_phase,'
            'ph_innov_sign_majority,'
            'slip_detected_this_epoch,'
            'n_sats,classification\n'
        )

    def _write_csv_row(self, rec: RebirthRecord) -> None:
        if self._csv_fh is None:
            return

        def ff(v, prec=4):
            return f'{float(v):.{prec}f}' if math.isfinite(float(v)) else 'nan'

        self._csv_fh.write(
            f'{rec.sat},{rec.sod:.1f},{rec.nproc},{rec.generation},{rec.slip_reason},'
            f'{ff(rec.rec_clk_pre_mm)},{ff(rec.rec_clk_post_mm)},{ff(rec.dclk_epoch_mm)},'
            f'{ff(rec.N_postfit_mm)},{ff(rec.N_prefit_mm)},{ff(rec.birth_sigma_mm)},'
            f'{ff(rec.old_N_mm)},{ff(rec.old_sigma_mm)},'
            f'{ff(rec.PIF_mm)},{ff(rec.LIFc_mm)},{ff(rec.rp_prefit_mm)},{ff(rec.rp_postfit_mm)},'
            f'{ff(rec.code_res_mm)},{ff(rec.phase_res_mm)},'
            f'{ff(rec.elevation_deg, prec=3)},'
            f'{ff(rec.code_rms_mm)},{ff(rec.phase_rms_mm)},{ff(rec.cm_ratio, prec=5)},'
            f'{ff(rec.mean_ph_innov_mm)},{rec.n_accepted_phase},'
            f'{rec.ph_innov_sign_majority},'
            f'{1 if rec.slip_detected_this_epoch else 0},'
            f'{rec.n_sats},{rec.classification}\n'
        )
        self._csv_fh.flush()


# ─────────────────────────────────────────────────────────────────────────────
# PATCH_GUIDE — exact insertion points for ppp_gps_glab.py
# ─────────────────────────────────────────────────────────────────────────────
#
# This section documents the five diff hunks needed.  Search for the
# ANCHOR comment text to locate each insertion point exactly.
#
# ┌──────────────────────────────────────────────────────────────────────────┐
# │  HUNK 1 — Import  (add after existing import block, ~line 290)           │
# │                                                                          │
# │  from rebirth_forensic import RebirthForensic                            │
# └──────────────────────────────────────────────────────────────────────────┘
#
# ┌──────────────────────────────────────────────────────────────────────────┐
# │  HUNK 2 — Construction  (inside _ppp_pass, after _clk_series = [])       │
# │                                                                          │
# │  ANCHOR: "_clk_series          = []"                                     │
# │                                                                          │
# │  # ── REBIRTH-FORENSIC: construction ──────────────────────────────────  │
# │  _rebirth_forensic_csv = open(                                           │
# │      os.path.join(DATA, 'rebirth_forensic.csv'), 'w')                   │
# │  _rebirth_forensic = RebirthForensic(                                    │
# │      fh=sys.stdout,                                                      │
# │      rebirth_csv_fh=_rebirth_forensic_csv,                               │
# │  )                                                                       │
# └──────────────────────────────────────────────────────────────────────────┘
#
# ┌──────────────────────────────────────────────────────────────────────────┐
# │  HUNK 3 — note_slip  (inside cycle-slip branch after register_reset)     │
# │                                                                          │
# │  ANCHOR: "amgr.register_reset(sid)  # SLIPPED→RESET"                    │
# │                                                                          │
# │  # REBIRTH-FORENSIC: note slip                                           │
# │  _rebirth_forensic.note_slip(                                            │
# │      sid, sod,                                                           │
# │      dGF=m['GF_m'] - prev_gf.get(sid, m['GF_m']),                       │
# │      dMW=m['MW_cyc'] - prev_mw.get(sid, m['MW_cyc']),                   │
# │  )                                                                       │
# └──────────────────────────────────────────────────────────────────────────┘
#
# ┌──────────────────────────────────────────────────────────────────────────┐
# │  HUNK 4a — record_rebirth  (inside _newborn_pending loop)                │
# │                                                                          │
# │  ANCHOR: "amgr.register_birth(_nsid, _nki, nproc, sod, sigma_m=20.0)"  │
# │  Insert AFTER that line:                                                 │
# │                                                                          │
# │  # REBIRTH-FORENSIC: record rebirth                                      │
# │  _rebirth_forensic.record_rebirth({                                      │
# │      'sat': _nsid, 'sod': _nsod, 'nproc': nproc,                        │
# │      'slip_reason': 'rebirth' if amgr.states[_nsid].reset_count > 0     │
# │                     else 'first_birth',                                  │
# │      'rec_clk_pre_mm':  x_before[3] * 1e3,                              │
# │      'rec_clk_post_mm': x[3] * 1e3,                                     │
# │      'dclk_epoch_mm':   _dclk_birth_mm,                                 │
# │      'N_postfit_mm':    _n_postfit * 1e3,                                │
# │      'N_prefit_mm':     _n_prefit  * 1e3,                                │
# │      'birth_sigma_mm':  20000.0,                                         │
# │      # old_N / old_sigma: stored before reset in _amb_saved_state        │
# │      'old_N_mm':        (_amb_saved_state.get(_nsid, {})                 │
# │                          .get('x_ki', float('nan'))) * 1e3               │
# │                         if _nsid in _amb_saved_state else float('nan'),  │
# │      'old_sigma_mm':    math.sqrt(max(                                   │
# │                             _amb_saved_state.get(_nsid, {})              │
# │                             .get('P_ki', float('nan')), 0)               │
# │                         ) * 1e3 if _nsid in _amb_saved_state             │
# │                         else float('nan'),                               │
# │      'PIF_mm':          _nm['PIF'] * 1e3,                                │
# │      'LIFc_mm':         _nLIFc * 1e3,                                   │
# │      'rp_prefit_mm':    _rp_prefit * 1e3,                                │
# │      'rp_postfit_mm':   _rp_post   * 1e3,                                │
# │      'code_res_mm':     (_nm['PIF'] - _rp_post) * 1e3,                  │
# │      'phase_res_mm':    0.0,                                             │
# │      'elevation_deg':   math.degrees(_nm['el']),                         │
# │      'mean_ph_innov_mm': (_mean_ph * 1e3                                 │
# │                           if '_mean_ph' in dir() and math.isfinite(      │
# │                           _mean_ph) else float('nan')),                  │
# │      'n_accepted_phase': len(_accepted_phase_sids),                      │
# │      'ph_innov_signs':   list(_ph_innov_list),  # metres                 │
# │      'n_sats':           len(geom),                                      │
# │  })                                                                      │
# └──────────────────────────────────────────────────────────────────────────┘
#
# ┌──────────────────────────────────────────────────────────────────────────┐
# │  HUNK 4b — finalize_rebirth_epoch  (after code_rms + _au_cm_ratio)       │
# │                                                                          │
# │  ANCHOR: line after "_au_cm_ratio  = abs(...)"  in _MEAS_AUDIT_ENABLE    │
# │  (or after "code_rms = ..." if MEAS_AUDIT is disabled)                   │
# │                                                                          │
# │  # REBIRTH-FORENSIC: back-fill code/phase RMS into this epoch's births  │
# │  _rebirth_forensic.finalize_rebirth_epoch(                               │
# │      code_rms_mm  = code_rms,                                            │
# │      phase_rms_mm = phase_rms,                                           │
# │      cm_ratio     = _au_cm_ratio if _MEAS_AUDIT_ENABLE else float('nan'),│
# │  )                                                                       │
# └──────────────────────────────────────────────────────────────────────────┘
#
# ┌──────────────────────────────────────────────────────────────────────────┐
# │  HUNK 5 — record_epoch  (after HUNK 4b, end of each epoch)               │
# │                                                                          │
# │  ANCHOR: just before "[EPOCH] SOD=" print line                           │
# │                                                                          │
# │  # REBIRTH-FORENSIC: record epoch for context window                     │
# │  _rebirth_forensic.record_epoch({                                        │
# │      'nproc':              nproc,                                        │
# │      'sod':                sod,                                          │
# │      'rec_clk_m':          x[3],                                         │
# │      'dclk_m':             x[3] - x_before[3],                          │
# │      'code_rms_mm':        code_rms,                                     │
# │      'phase_rms_mm':       phase_rms if math.isfinite(phase_rms)         │
# │                            else float('nan'),                            │
# │      'cm_ratio':           _au_cm_ratio if _MEAS_AUDIT_ENABLE            │
# │                            else float('nan'),                            │
# │      'mean_ph_innov_mm':   (_mean_ph * 1e3                               │
# │                             if '_mean_ph' in dir() and                   │
# │                             math.isfinite(_mean_ph) else float('nan')),  │
# │      'std_ph_innov_mm':    (_std_ph * 1e3                                │
# │                             if '_std_ph' in dir() and                    │
# │                             math.isfinite(_std_ph) else float('nan')),   │
# │      'mean_code_innov_mm': (_mean_cod * 1e3                              │
# │                             if '_mean_cod' in dir() and                  │
# │                             math.isfinite(_mean_cod) else float('nan')), │
# │      'n_accepted_phase':   len(_accepted_phase_sids),                    │
# │      'all_reject':         len(_accepted_phase_sids) == 0,               │
# │      'innov_norm':         _innov_norm,                                  │
# │      'n_sats':             len(geom),                                    │
# │  })                                                                      │
# └──────────────────────────────────────────────────────────────────────────┘
#
# ┌──────────────────────────────────────────────────────────────────────────┐
# │  HUNK 6 — summarize  (before _ppp_pass return statement)                 │
# │                                                                          │
# │  ANCHOR: line before "return (eplist_out, ...)"                          │
# │                                                                          │
# │  # REBIRTH-FORENSIC: pass-end summary                                    │
# │  _rebirth_forensic.summarize(nproc_total=nproc)                          │
# │  _rebirth_forensic_csv.close()                                           │
# └──────────────────────────────────────────────────────────────────────────┘

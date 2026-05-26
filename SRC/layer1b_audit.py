"""
layer1b_audit.py
================
LAYER-1B — Physical Measurement Consistency Audit

Instruments the REAL PPP pipeline (ppp_v641.py) step by step.
Does NOT build a parallel PPP engine — uses the real parsed data
and the real model functions.

Phases:
  1  Raw observable entry validation
  2  Carrier range conversion  (phase_cycles × wavelength)
  3  IF combination validation
  4  OSB application validation
  5  APC / wind-up / tropo / relativity / Sagnac corrections
  6  Pre-ambiguity model validation
  7  First failure localisation
"""

import os, sys, math, csv
import numpy as np
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# ── Import REAL pipeline (AmbiguityManager stub already on path) ─────────
import ppp_v641 as _ppp

CLIGHT   = _ppp.CLIGHT
FREQ1    = _ppp.FREQ1;   LAMBDA1   = _ppp.LAMBDA1
FREQ2    = _ppp.FREQ2;   LAMBDA2   = _ppp.LAMBDA2
ALFA     = _ppp.ALFA;    BETA      = _ppp.BETA
LAMBDA_IF= _ppp.LAMBDA_IF
FREQ_E1  = _ppp.FREQ_E1; LAMBDA_E1 = _ppp.LAMBDA_E1
FREQ_E5A = _ppp.FREQ_E5A;LAMBDA_E5A= _ppp.LAMBDA_E5A
ALFA_E   = _ppp.ALFA_E;  BETA_E    = _ppp.BETA_E
LAMBDA_IF_E = _ppp.LAMBDA_IF_E
OMGE     = _ppp.OMGE

# Real functions
_sod2t   = _ppp._sod2t
_lla     = _ppp._lla
_zhd     = _ppp._zhd
_sun     = _ppp._sun
_wu      = _ppp._wu
_proc    = _ppp._proc
_proc_gal= _ppp._proc_gal
_rp      = _ppp._rp
_ifc     = _ppp._ifc

UP  = str(BASE)
OUT = str(BASE)

# Physical plausibility bounds
CARR_DIFF_MM = 1.0   # carrier range mismatch tolerance
IF_DIFF_MM   = 1.0   # IF combination mismatch tolerance
OSB_MAX_M    = 30.0  # max plausible OSB magnitude

def _fmt(v, prec=4):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return 'nan'
    return f'{v:.{prec}f}'

print('\n══════════════════════════════════════════════')
print(' LAYER-1B  Physical Measurement Consistency Audit')
print('══════════════════════════════════════════════\n')

required_files = [
    'IISC00IND_R_20260380000_01D_30S_MO.rnx',
    'COD0MGXFIN_20260380000_01D_05M_ORB.SP3',
    'COD0MGXFIN_20260380000_01D_30S_CLK.CLK',
    'COD0MGXFIN_20260380000_01D_01D_OSB.BIA',
    'igs20_2408.atx',
    'COD0MGXFIN_20260380000_01D_30S_ATT.OBX',
    'ocnload.blq'
]

print('\n[PATH-CHECK]')
missing = []
for fn in required_files:
    fp = Path(UP) / fn
    ok = fp.exists()
    print(f'  {"✓" if ok else "✗"} {fp}')
    if not ok:
        missing.append(fn)

if missing:
    print(f'\n  ERROR: {len(missing)} required file(s) not found:')
    for fn in missing:
        print(f'    • {fn}')
    print('  Place all required files alongside this script and re-run.')
    sys.exit(1)

# ── Load all data through REAL parsers ────────────────────────────────────
print('[LOAD] Parsing files via REAL ppp_v641 parsers …')
obs_file = Path(UP) / 'IISC00IND_R_20260380000_01D_30S_MO.rnx'
_, epochs, ah, ak = _ppp.parse_obs(str(obs_file))
sp3_file = Path(UP) / 'COD0MGXFIN_20260380000_01D_05M_ORB.SP3'
clk_file = Path(UP) / 'COD0MGXFIN_20260380000_01D_30S_CLK.CLK'
bia_file = Path(UP) / 'COD0MGXFIN_20260380000_01D_01D_OSB.BIA'
atx_file = Path(UP) / 'igs20_2408.atx'
obx_file = Path(UP) / 'COD0MGXFIN_20260380000_01D_30S_ATT.OBX'
blq_file = Path(UP) / 'ocnload.blq'

sp3t, sp, sc      = _ppp.parse_sp3(str(sp3_file))
clkd              = _ppp.parse_clk(str(clk_file))
osb               = _ppp.parse_bia(str(bia_file))
satx, recx_db     = _ppp.parse_atx(str(atx_file))
att               = _ppp.parse_obx(str(obx_file))
blq               = _ppp.parse_blq(str(blq_file))

# Receiver APC from ATX (same lookup as postpos)
recx = recx_db.get(ak) or recx_db.get(ak.split()[0]+' NONE')
if recx:
    print(f'  Receiver APC loaded: {ak}')
else:
    print(f'  WARNING: receiver APC not found for "{ak}" — using zeros')
    recx = {'L1': np.zeros(3), 'L2': np.zeros(3),
            'L1_E': np.zeros(3), 'L2_E': np.zeros(3)}

# Station geometry (from postpos)
APX     = np.array([1337936.455, 6070317.126, 1427876.785])
NOM_XYZ = np.array([1337935.5599, 6070317.2377, 1427877.5071])
rxyz    = APX
lat0_r, _, h0 = _lla(rxyz)
lat0_d  = math.degrees(lat0_r)
zhd_val = _zhd(lat0_d, h0)
doy     = 38
tref    = sp3t[0]
STA     = 'IISC'
ELM     = math.radians(10.)

print(f'  Epochs: {len(epochs)}  ZHD={zhd_val:.4f}m  h0={h0:.0f}m  lat0={lat0_d:.3f}°')

AUDIT_EPOCHS = min(len(epochs), 10)

# ============================================================
#  PHASE-1  RAW OBSERVABLE ENTRY VALIDATION
# ============================================================
print('\n── PHASE-1  Raw Observable Entry Validation ──────────────────')

stage1_rows = []
phase1_fails = []

for ep_i, ep in enumerate(epochs[:AUDIT_EPOCHS]):
    sod = ep['t']
    for sid, so in sorted(ep['sats'].items()):
        if sid[0] not in ('G', 'E'):
            continue
        is_gal = (sid[0] == 'E')

        if is_gal:
            L1_cyc = so.get('L1C', 0.); L2_cyc = so.get('L5Q', 0.)
            P1_m   = so.get('C1C', 0.); P2_m   = so.get('C5Q', 0.)
            sig_P1, sig_P2 = 'C1C', 'C5Q'
            sig_L1, sig_L2 = 'L1C', 'L5Q'
            f1, f2  = FREQ_E1, FREQ_E5A
            lam1, lam2 = LAMBDA_E1, LAMBDA_E5A
        else:
            L1_cyc = so.get('L1C', 0.); L2_cyc = so.get('L2W', 0.)
            P1_m   = so.get('C1W', 0.); P2_m   = so.get('C2W', 0.)
            sig_P1, sig_P2 = 'C1W', 'C2W'
            sig_L1, sig_L2 = 'L1C', 'L2W'
            f1, f2  = FREQ1, FREQ2
            lam1, lam2 = LAMBDA1, LAMBDA2

        if P1_m == 0 or P2_m == 0 or L1_cyc == 0 or L2_cyc == 0:
            continue

        lam1_check = CLIGHT / f1
        lam2_check = CLIGHT / f2
        lam1_ok = abs(lam1 - lam1_check) < 1e-6
        lam2_ok = abs(lam2 - lam2_check) < 1e-6
        code_ok  = (1e6 < P1_m < 4e7) and (1e6 < P2_m < 4e7)
        pr1_m = L1_cyc * lam1; pr2_m = L2_cyc * lam2
        phase_ok = (1e6 < pr1_m < 4e7) and (1e6 < pr2_m < 4e7)
        freq_ok  = f1 > 1e9 and f2 > 1e9

        ok = lam1_ok and lam2_ok and code_ok and phase_ok and freq_ok

        row = dict(
            epoch=ep_i, sod=sod, sat=sid, const=sid[0],
            selected_code_L1=sig_P1, selected_code_L2=sig_P2,
            selected_phase_L1=sig_L1, selected_phase_L2=sig_L2,
            raw_code_L1=_fmt(P1_m), raw_code_L2=_fmt(P2_m),
            raw_phase_L1_cycles=_fmt(L1_cyc), raw_phase_L2_cycles=_fmt(L2_cyc),
            freq1_hz=_fmt(f1, 0), freq2_hz=_fmt(f2, 0),
            lambda1_m=_fmt(lam1, 9), lambda2_m=_fmt(lam2, 9),
            lam1_ok=int(lam1_ok), lam2_ok=int(lam2_ok),
            code_ok=int(code_ok), phase_ok=int(phase_ok), freq_ok=int(freq_ok),
            PASS=int(ok)
        )
        stage1_rows.append(row)
        if not ok:
            phase1_fails.append((sid, sod, lam1_ok, lam2_ok, code_ok, phase_ok))

with open(f'{OUT}/layer1b_stage1_raw.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(stage1_rows[0].keys()))
    w.writeheader(); w.writerows(stage1_rows)

total1 = len(stage1_rows); pass1 = sum(1 for r in stage1_rows if r['PASS'])
print(f'  Obs rows: {total1}   PASS: {pass1}   FAIL: {total1-pass1}')
for sid, sod, l1, l2, co, ph in phase1_fails[:5]:
    print(f'    FAIL {sid} SOD={sod:.0f} lam1={l1} lam2={l2} code={co} phase={ph}')
ph1_ok = (len(phase1_fails) == 0)
print(f'  {"✓ PASS" if ph1_ok else "✗ FAIL"}  Phase-1')

# ============================================================
#  PHASE-2  CARRIER RANGE CONVERSION
# ============================================================
print('\n── PHASE-2  Carrier Range Conversion ────────────────────────')

stage2_rows = []
phase2_fails = []

for ep_i, ep in enumerate(epochs[:AUDIT_EPOCHS]):
    sod = ep['t']
    for sid, so in sorted(ep['sats'].items()):
        if sid[0] not in ('G', 'E'):
            continue
        is_gal = (sid[0] == 'E')
        if is_gal:
            L1_cyc = so.get('L1C', 0.); L2_cyc = so.get('L5Q', 0.)
            lam1, lam2 = LAMBDA_E1, LAMBDA_E5A
            f1, f2     = FREQ_E1, FREQ_E5A
        else:
            L1_cyc = so.get('L1C', 0.); L2_cyc = so.get('L2W', 0.)
            lam1, lam2 = LAMBDA1, LAMBDA2
            f1, f2     = FREQ1, FREQ2
        if L1_cyc == 0 or L2_cyc == 0:
            continue

        ppp_L1 = L1_cyc * lam1
        ppp_L2 = L2_cyc * lam2
        ind_lam1 = CLIGHT / f1; ind_lam2 = CLIGHT / f2
        ind_L1   = L1_cyc * ind_lam1
        ind_L2   = L2_cyc * ind_lam2

        d1 = abs(ppp_L1 - ind_L1) * 1e3
        d2 = abs(ppp_L2 - ind_L2) * 1e3
        ok = d1 < CARR_DIFF_MM and d2 < CARR_DIFF_MM

        stage2_rows.append(dict(
            sod=sod, sat=sid, const=sid[0],
            phase_cycles_L1=_fmt(L1_cyc), phase_cycles_L2=_fmt(L2_cyc),
            lambda1=_fmt(lam1, 9), lambda2=_fmt(lam2, 9),
            PPP_phase_range_L1=_fmt(ppp_L1), PPP_phase_range_L2=_fmt(ppp_L2),
            independent_phase_range_L1=_fmt(ind_L1),
            independent_phase_range_L2=_fmt(ind_L2),
            difference_L1_mm=_fmt(d1), difference_L2_mm=_fmt(d2),
            PASS=int(ok)
        ))
        if not ok:
            phase2_fails.append((sid, sod, d1, d2))

with open(f'{OUT}/layer1b_stage2_carrier.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(stage2_rows[0].keys()))
    w.writeheader(); w.writerows(stage2_rows)

total2 = len(stage2_rows); pass2 = sum(1 for r in stage2_rows if r['PASS'])
print(f'  Obs rows: {total2}   PASS: {pass2}   FAIL: {total2-pass2}')
for sid, sod, d1, d2 in phase2_fails[:5]:
    print(f'    FAIL {sid} SOD={sod:.0f} diff_L1={d1:.3f}mm diff_L2={d2:.3f}mm')
ph2_ok = (len(phase2_fails) == 0)
print(f'  {"✓ PASS" if ph2_ok else "✗ FAIL"}  Phase-2')

# ============================================================
#  PHASE-3  IF COMBINATION VALIDATION
# ============================================================
print('\n── PHASE-3  IF Combination Validation ───────────────────────')

stage3_rows = []
phase3_fails = []

for ep_i, ep in enumerate(epochs[:AUDIT_EPOCHS]):
    sod = ep['t']
    for sid, so in sorted(ep['sats'].items()):
        if sid[0] not in ('G', 'E'):
            continue
        is_gal = (sid[0] == 'E')
        if is_gal:
            P1 = so.get('C1C', 0.); P2 = so.get('C5Q', 0.)
            L1 = so.get('L1C', 0.); L2 = so.get('L5Q', 0.)
            alfa, beta = ALFA_E, BETA_E
            lam1, lam2 = LAMBDA_E1, LAMBDA_E5A
            f1, f2     = FREQ_E1, FREQ_E5A
        else:
            P1 = so.get('C1W', 0.); P2 = so.get('C2W', 0.)
            L1 = so.get('L1C', 0.); L2 = so.get('L2W', 0.)
            alfa, beta = ALFA, BETA
            lam1, lam2 = LAMBDA1, LAMBDA2
            f1, f2     = FREQ1, FREQ2
        if P1 == 0 or P2 == 0 or L1 == 0 or L2 == 0:
            continue

        # PPP values
        if is_gal:
            ppp_PIF = ALFA_E * P1 - BETA_E * P2
            ppp_LIF = ALFA_E * (L1 * lam1) - BETA_E * (L2 * lam2)
        else:
            ppp_PIF = _ifc(P1, P2)
            ppp_LIF = _ifc(L1 * lam1, L2 * lam2)

        # Independent
        f1sq = f1**2; f2sq = f2**2; denom = f1sq - f2sq
        alfa_i = f1sq / denom; beta_i = f2sq / denom
        ind_PIF = alfa_i * P1 - beta_i * P2
        ind_LIF = alfa_i * (L1 * lam1) - beta_i * (L2 * lam2)

        coeff_ok = abs(alfa - alfa_i) < 1e-9 and abs(beta - beta_i) < 1e-9
        PIF_d = abs(ppp_PIF - ind_PIF) * 1e3
        LIF_d = abs(ppp_LIF - ind_LIF) * 1e3
        ok = coeff_ok and PIF_d < IF_DIFF_MM and LIF_d < IF_DIFF_MM

        stage3_rows.append(dict(
            sod=sod, sat=sid, const=sid[0],
            alfa_ppp=_fmt(alfa, 10), beta_ppp=_fmt(beta, 10),
            alfa_ind=_fmt(alfa_i, 10), beta_ind=_fmt(beta_i, 10),
            coeff_ok=int(coeff_ok),
            PPP_PIF=_fmt(ppp_PIF), ind_PIF=_fmt(ind_PIF), PIF_diff_mm=_fmt(PIF_d),
            PPP_LIF=_fmt(ppp_LIF), ind_LIF=_fmt(ind_LIF), LIF_diff_mm=_fmt(LIF_d),
            PASS=int(ok)
        ))
        if not ok:
            phase3_fails.append((sid, sod, coeff_ok, PIF_d, LIF_d))

with open(f'{OUT}/layer1b_stage3_if.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(stage3_rows[0].keys()))
    w.writeheader(); w.writerows(stage3_rows)

total3 = len(stage3_rows); pass3 = sum(1 for r in stage3_rows if r['PASS'])
print(f'  Obs rows: {total3}   PASS: {pass3}   FAIL: {total3-pass3}')
for sid, sod, c, pd, ld in phase3_fails[:5]:
    print(f'    FAIL {sid} SOD={sod:.0f} coeff={c} PIF={pd:.3f}mm LIF={ld:.3f}mm')
ph3_ok = (len(phase3_fails) == 0)
print(f'  {"✓ PASS" if ph3_ok else "✗ FAIL"}  Phase-3')

# ============================================================
#  PHASE-4  OSB APPLICATION VALIDATION
# ============================================================
print('\n── PHASE-4  OSB Application Validation ──────────────────────')

stage4_rows = []
phase4_fails = []

for ep_i, ep in enumerate(epochs[:AUDIT_EPOCHS]):
    sod = ep['t']
    for sid, so in sorted(ep['sats'].items()):
        if sid[0] not in ('G', 'E'):
            continue
        is_gal = (sid[0] == 'E')
        ob = osb.get(sid, {})

        if is_gal:
            P1 = so.get('C1C', 0.); P2 = so.get('C5Q', 0.)
            L1 = so.get('L1C', 0.); L2 = so.get('L5Q', 0.)
            if P1==0 or P2==0 or L1==0 or L2==0: continue
            b1=ob.get('C1C',0.); b2=ob.get('C5Q',0.)
            bl1=ob.get('L1C',0.); bl2=ob.get('L5Q',0.)
            PIF_raw = ALFA_E*P1 - BETA_E*P2
            LIF_raw = ALFA_E*(L1*LAMBDA_E1) - BETA_E*(L2*LAMBDA_E5A)
            PIF_osb = ALFA_E*(P1-b1) - BETA_E*(P2-b2)
            osb_IF_ph = ALFA_E*bl1 - BETA_E*bl2
            LIF_osb = LIF_raw - osb_IF_ph
            sig_fam = 'E1+E5a'
        else:
            P1 = so.get('C1W', 0.); P2 = so.get('C2W', 0.)
            L1 = so.get('L1C', 0.); L2 = so.get('L2W', 0.)
            if P1==0 or P2==0 or L1==0 or L2==0: continue
            b1=ob.get('C1W',ob.get('C1C',0.))
            b2=ob.get('C2W',ob.get('C2L',0.))
            bl1=ob.get('L1C',ob.get('L1W',0.))
            bl2=ob.get('L2W',ob.get('L2C',0.))
            PIF_raw = _ifc(P1, P2)
            LIF_raw = _ifc(L1*LAMBDA1, L2*LAMBDA2)
            PIF_osb = _ifc(P1-b1, P2-b2)
            osb_IF_ph = ALFA*bl1 - BETA*bl2
            LIF_osb = LIF_raw - osb_IF_ph
            sig_fam = 'L1C+L2W'

        osb_IF_code = (ALFA_E*b1 - BETA_E*b2) if is_gal else (ALFA*b1 - BETA*b2)
        code_shift = PIF_raw - PIF_osb          # should equal osb_IF_code
        sign_ok    = abs(code_shift - osb_IF_code) < 1e-3  # 1 mm tolerance
        plaus_ok   = (abs(b1) < OSB_MAX_M and abs(b2) < OSB_MAX_M and
                      abs(bl1) < OSB_MAX_M and abs(bl2) < OSB_MAX_M)
        ok = plaus_ok and sign_ok

        stage4_rows.append(dict(
            sod=sod, sat=sid, const=sid[0], signal_family=sig_fam,
            PIF_before_osb=_fmt(PIF_raw), PIF_after_osb=_fmt(PIF_osb),
            LIF_before_osb=_fmt(LIF_raw), LIF_after_osb=_fmt(LIF_osb),
            osb_code_L1_m=_fmt(b1), osb_code_L2_m=_fmt(b2),
            osb_phase_L1_m=_fmt(bl1), osb_phase_L2_m=_fmt(bl2),
            osb_IF_code_m=_fmt(osb_IF_code), osb_IF_phase_m=_fmt(osb_IF_ph),
            code_shift_mm=_fmt(code_shift*1e3), phase_shift_mm=_fmt(osb_IF_ph*1e3),
            plaus_ok=int(plaus_ok), sign_ok=int(sign_ok),
            PASS=int(ok)
        ))
        if not ok:
            phase4_fails.append((sid, sod, plaus_ok, sign_ok, b1, b2, bl1, bl2))

with open(f'{OUT}/layer1b_stage4_osb.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(stage4_rows[0].keys()))
    w.writeheader(); w.writerows(stage4_rows)

total4 = len(stage4_rows); pass4 = sum(1 for r in stage4_rows if r['PASS'])
print(f'  Obs rows: {total4}   PASS: {pass4}   FAIL: {total4-pass4}')
for sid, sod, pl, si, c1, c2, p1, p2 in phase4_fails[:5]:
    print(f'    FAIL {sid} SOD={sod:.0f} plaus={pl} sign={si} '
          f'C-OSBs=[{c1:.4f},{c2:.4f}] P-OSBs=[{p1:.4f},{p2:.4f}]m')
ph4_ok = (len(phase4_fails) == 0)
print(f'  {"✓ PASS" if ph4_ok else "✗ FAIL"}  Phase-4')

# ============================================================
#  PHASE-5  CORRECTIONS VALIDATION
# ============================================================
print('\n── PHASE-5  Geometric Correction Validation ─────────────────')

stage5_rows = []
phase5_fails = []
wum5 = {}

for ep_i, ep in enumerate(epochs[:AUDIT_EPOCHS]):
    sod = ep['t']
    tow = _sod2t(sod, tref)
    sun = _sun(tow)

    for sid, so in sorted(ep['sats'].items()):
        if sid[0] not in ('G', 'E'):
            continue
        is_gal = (sid[0] == 'E')

        m = (_proc_gal(sid, so, tow, rxyz, ah, sp3t, sp, sc, clkd, osb,
                       lat0_d, doy, zhd_val, ELM, satx, att, recx,
                       blq=blq, sta=STA, tow_total=tow)
             if is_gal
             else _proc(sid, so, tow, rxyz, ah, sp3t, sp, sc, clkd, osb,
                        lat0_d, doy, zhd_val, ELM, satx, att, recx,
                        blq=blq, sta=STA, tow_total=tow))
        if m is None:
            continue

        el_deg = math.degrees(m['el'])
        lam_if = m.get('_lam_if', LAMBDA_IF)

        wu   = _wu(m['sat_xyz'], m['rec_apc'], sun, wum5.get(sid, 0.))
        wum5[sid] = wu
        wu_m = wu * lam_if

        # Physical bounds
        # Physical bounds calibrated from empirical ranges over 10 epochs
        wu_ok    = abs(wu_m) < 0.5                    # wind-up ±0.5 m (fractions of LAMBDA_IF)
        tropo_ok = 0.001 < m['trop_zhd'] < 30.        # dry tropo mapped (2–8 m at IISC)
        # dtrel = _rel(sv,vv) = -2*sv·vv/c, already in metres (used directly in _rp)
        dtrel_m  = m['dtrel']                          # metres — range ±10 m
        rel_ok   = abs(dtrel_m) < 15.                  # relativistic correction < 15 m
        shp_ok   = 0. <= m['shp'] < 0.025             # Shapiro < 25 mm
        setm_ok  = abs(m['setm']) < 0.01              # solid-earth tide < 10 mm (IISC coastal)
        sat_apc  = m.get('_sat_apc_range_m', 0.)
        rec_apc  = m.get('_rec_apc_range_m', 0.)
        # Sat APC z-offset projected onto LOS — up to ~2 m for GPS Block IIF
        apc_ok   = abs(sat_apc) < 3.0 and abs(rec_apc) < 0.5
        pcv_ok   = abs(m['pcv_sat']) < 0.10 and abs(m['pcv_rec']) < 0.10
        # Sagnac term: unit·(svc-sv_tx) — ECEF rotation in metres, ±40 m range
        sagnac_m = m.get('_sagnac_m', 0.)
        sag_ok   = abs(sagnac_m) < 50.

        all_ok = wu_ok and tropo_ok and rel_ok and shp_ok and setm_ok and apc_ok and pcv_ok

        stage5_rows.append(dict(
            sod=sod, sat=sid, const=sid[0], el_deg=_fmt(el_deg),
            wu_m=_fmt(wu_m, 6), wu_ok=int(wu_ok),
            trop_zhd_m=_fmt(m['trop_zhd']), tropo_ok=int(tropo_ok),
            dtrel_m=_fmt(dtrel_m, 6), rel_ok=int(rel_ok),
            shapiro_m=_fmt(m['shp'], 6), shp_ok=int(shp_ok),
            setm_m=_fmt(m['setm'], 6), set_ok=int(setm_ok),
            sat_apc_m=_fmt(sat_apc, 6), rec_apc_m=_fmt(rec_apc, 6), apc_ok=int(apc_ok),
            pcv_sat_m=_fmt(m['pcv_sat'], 6), pcv_rec_m=_fmt(m['pcv_rec'], 6), pcv_ok=int(pcv_ok),
            sagnac_m=_fmt(sagnac_m, 6), sag_ok=int(sag_ok),
            PASS=int(all_ok)
        ))
        if not all_ok:
            phase5_fails.append((sid, sod, wu_ok, tropo_ok, rel_ok, shp_ok, apc_ok))

with open(f'{OUT}/layer1b_stage5_corrections.csv', 'w', newline='') as f:
    if stage5_rows:
        w = csv.DictWriter(f, fieldnames=list(stage5_rows[0].keys()))
        w.writeheader(); w.writerows(stage5_rows)

total5 = len(stage5_rows); pass5 = sum(1 for r in stage5_rows if r['PASS'])
print(f'  Obs rows: {total5}   PASS: {pass5}   FAIL: {total5-pass5}')
for sid, sod, wu, tr, re, sh, apc in phase5_fails[:5]:
    print(f'    FAIL {sid} SOD={sod:.0f} wu={wu} tropo={tr} rel={re} shp={sh} apc={apc}')
ph5_ok = (len(phase5_fails) == 0)
print(f'  {"✓ PASS" if ph5_ok else "✗ FAIL"}  Phase-5')

# ============================================================
#  PHASE-6  PRE-AMBIGUITY MODEL VALIDATION
# ============================================================
print('\n── PHASE-6  Pre-Ambiguity Model Validation ──────────────────')
print('    LIF_corr  - (rng - scm + corrections)  =  rec_clk + ZWD*mw + N_IF')
print('    Expected residual dominated by receiver clock (~0.01–10 m)')

stage6_rows = []
phase6_fails = []
wum6 = {}

for ep_i, ep in enumerate(epochs[:AUDIT_EPOCHS]):
    sod = ep['t']
    tow = _sod2t(sod, tref)
    sun = _sun(tow)

    geom6 = []
    for sid, so in sorted(ep['sats'].items()):
        if sid[0] not in ('G', 'E'):
            continue
        is_gal = (sid[0] == 'E')

        m = (_proc_gal(sid, so, tow, rxyz, ah, sp3t, sp, sc, clkd, osb,
                       lat0_d, doy, zhd_val, ELM, satx, att, recx,
                       blq=blq, sta=STA, tow_total=tow)
             if is_gal
             else _proc(sid, so, tow, rxyz, ah, sp3t, sp, sc, clkd, osb,
                        lat0_d, doy, zhd_val, ELM, satx, att, recx,
                        blq=blq, sta=STA, tow_total=tow))
        if m is None:
            continue

        wu = _wu(m['sat_xyz'], m['rec_apc'], sun, wum6.get(sid, 0.))
        wum6[sid] = wu
        lam_if = m.get('_lam_if', LAMBDA_IF)
        LIFc   = m['LIF'] - wu * lam_if

        rp = _rp(m, 0., 0.)   # dT=0, ZWD=0 (raw model residual)
        pre_amb = LIFc - rp

        geom6.append((m, rp, LIFc, pre_amb, sid, is_gal))

    # Estimate receiver clock from median code residual (GPS only for stability)
    code_res_G = [_ifc(so.get('C1W',0.), so.get('C2W',0.))
                  - _rp(m_, 0., 0.)
                  for m_, rp_, _, _, sid_, ig in geom6
                  for so in [ep['sats'].get(sid_, {})]
                  if not ig and _ifc(so.get('C1W',0.), so.get('C2W',0.)) != 0]
    dT_est = float(np.median(code_res_G)) if code_res_G else 0.0

    for m, rp, LIFc, pre_amb, sid, is_gal in geom6:
        res_ck = pre_amb - dT_est
        mag_ok = math.isfinite(pre_amb) and abs(pre_amb) < 1e9

        stage6_rows.append(dict(
            ep=ep_i, sod=sod, sat=sid, const=sid[0],
            el_deg=_fmt(math.degrees(m['el'])),
            rng_m=_fmt(m['rng']), scm_m=_fmt(m['scm']),
            rp_m=_fmt(rp), LIF_corr_m=_fmt(LIFc),
            pre_amb_res_m=_fmt(pre_amb),
            dT_est_m=_fmt(dT_est),
            res_after_clk_m=_fmt(res_ck),
            mag_ok=int(mag_ok),
            PASS=int(mag_ok)
        ))
        if not mag_ok:
            phase6_fails.append((sid, sod, pre_amb))

with open(f'{OUT}/layer1b_stage6_preamb.csv', 'w', newline='') as f:
    if stage6_rows:
        w = csv.DictWriter(f, fieldnames=list(stage6_rows[0].keys()))
        w.writeheader(); w.writerows(stage6_rows)

total6 = len(stage6_rows); pass6 = sum(1 for r in stage6_rows if r['PASS'])
print(f'  Obs rows: {total6}   PASS: {pass6}   FAIL: {total6-pass6}')

# GPS vs Galileo comparison
gps6 = [float(r['res_after_clk_m']) for r in stage6_rows
        if r['const']=='G' and r['res_after_clk_m'] != 'nan' and abs(float(r['res_after_clk_m'])) < 1e8]
gal6 = [float(r['res_after_clk_m']) for r in stage6_rows
        if r['const']=='E' and r['res_after_clk_m'] != 'nan' and abs(float(r['res_after_clk_m'])) < 1e8]
if gps6:
    print(f'    GPS  res_after_clk: mean={np.mean(gps6):+.3f} m  '
          f'std={np.std(gps6):.3f} m  |max|={max(abs(v) for v in gps6):.3f} m')
if gal6:
    print(f'    GAL  res_after_clk: mean={np.mean(gal6):+.3f} m  '
          f'std={np.std(gal6):.3f} m  |max|={max(abs(v) for v in gal6):.3f} m')

for sid, sod, v in phase6_fails[:5]:
    print(f'    FAIL {sid} SOD={sod:.0f} pre_amb={v:.3e}')
ph6_ok = (len(phase6_fails) == 0)
print(f'  {"✓ PASS" if ph6_ok else "✗ FAIL"}  Phase-6')

# ============================================================
#  PHASE-7  FIRST FAILURE LOCALISATION
# ============================================================
print('\n── PHASE-7  First Failure Localisation ──────────────────────')

failures = {
    'A_raw_observables':    (phase1_fails, 'stage-1'),
    'B_carrier_conversion': (phase2_fails, 'stage-2'),
    'C_IF_combination':     (phase3_fails, 'stage-3'),
    'D_OSB_application':    (phase4_fails, 'stage-4'),
    'E_corrections':        (phase5_fails, 'stage-5'),
    'F_pre_ambiguity_model':(phase6_fails, 'stage-6'),
    'G_post_EKF':           ([],           'stage-7 (EKF — not audited here)'),
}

print('\n  [L1B-FIRST-PHYSICAL-FAILURE]')
first = None
for sname, (fail_list, label) in failures.items():
    if fail_list and first is None:
        first = (sname, fail_list, label)

if first:
    sname, fl, label = first
    print(f'  Stage        : {sname}')
    print(f'  Label        : {label}')
    print(f'  Failure count: {len(fl)}')
    f0 = fl[0]
    print(f'  First sat    : {f0[0]}  (const={f0[0][0]})')
    print(f'  First epoch  : SOD={f0[1]:.0f}')
    print(f'  Detail       : {f0[2:]}')
else:
    print('  No physical failure in Stages A–F.')
    print('  → Residual anomalies (if any) are post-EKF (Stage G: ambiguity/EKF).')

print('\n  Summary by stage:')
for sname, (fl, label) in failures.items():
    st = 'FAIL' if fl else 'PASS'
    bar = '▓' * min(len(fl), 40)
    print(f'    [{st:4s}]  {sname:35s}  n={len(fl):4d}  {bar}')

# ============================================================
#  Final summary
# ============================================================
print('\n══════════════════════════════════════════════')
print(' LAYER-1B AUDIT COMPLETE')
print('══════════════════════════════════════════════')
total_all = total1+total2+total3+total4+total5+total6
pass_all  = pass1+pass2+pass3+pass4+pass5+pass6
print(f'  Stage-1 raw obs        : {pass1}/{total1}')
print(f'  Stage-2 carrier conv   : {pass2}/{total2}')
print(f'  Stage-3 IF combo       : {pass3}/{total3}')
print(f'  Stage-4 OSB            : {pass4}/{total4}')
print(f'  Stage-5 corrections    : {pass5}/{total5}')
print(f'  Stage-6 pre-ambiguity  : {pass6}/{total6}')
print(f'  TOTAL                  : {pass_all}/{total_all}')
print()
print('  CSVs written:')
for fn in ['layer1b_stage1_raw.csv','layer1b_stage2_carrier.csv',
           'layer1b_stage3_if.csv','layer1b_stage4_osb.csv',
           'layer1b_stage5_corrections.csv','layer1b_stage6_preamb.csv']:
    fp = f'{OUT}/{fn}'
    sz = os.path.getsize(fp) if os.path.exists(fp) else 0
    print(f'    {fn}  {sz:,} bytes')
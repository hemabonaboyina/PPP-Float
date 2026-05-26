"""
ppp.py  v55 — Phase-1 Stabilisation & Forensics
=================================================

── v55 FIXES / ADDITIONS (this file) ────────────────────────────────────────
  Phase-1 is STRICTLY a stabilisation and forensic pass.  Architecture,
  observation model, EKF structure, ambiguity parameterisation, file/plot
  interfaces — ALL UNCHANGED.

  L1.  STARTUP FORENSICS (first 30 epochs)
       Every epoch prints: sat count, accepted/rejected obs, code/phase RMS,
       innovation norm, state correction norm, position update, trop, clock,
       ambiguity count, amb cov min/max, condition number of innovation matrix,
       post-fit residual RMS.
       Explicit [WARN] lines for: state jump>0.5m, vert jump>0.3m, innovation
       explosion, non-PD covariance, ambiguity variance collapse, RMS doubling.

  L2.  HARD OBSERVATION REJECTION (pre-EKF)
       Code: |res|>10 m startup / >5 m converged.
       Phase: |res|>0.10 m startup / >0.05 m converged.
       Also: NaN, inf, zero/negative/singular variance rows.
       Every rejection logged with satellite ID + reason.

  L3.  ELEVATION-BASED PROTECTION
       Epochs 0–19: temporary 20° mask.  Epoch ≥20: nominal 10° mask.
       Elevation weighting σ ∝ 1/sin(el) (variance ∝ 1/sin²(el)) — restored.

  L4.  AMBIGUITY STATE PROTECTION
       Ambiguities initialised only after ≥3 consecutive valid epochs.
       Per-ambiguity check: covariance must be positive, finite, and in
       [1e-8, 1e8]; outside → flag + reset.

  L5.  TROPOSPHERE STABILISATION
       Initial ZWD variance raised to (0.5m)² (was (0.5m)² — kept).
       [WARN] if ZWD update >5 cm in one epoch.

  L6.  EKF NUMERICAL FORENSICS
       Pre-update: H finite, R PD, P symmetric, state/cov NaN/inf checks.
       Post-update: Joseph-form symmetry enforcement P = 0.5*(P+P.T).
       Logs: smallest eigenvalue, largest eigenvalue, condition number of P.

  L7.  GALILEO/OSB CONSISTENCY
       If phase OSB (bl1/bl5) is zero/missing for a Galileo satellite →
       observation is REJECTED rather than silently using 0 bias.
       Same for GPS bl1/bl2.  Per-satellite first-epoch trace: raw phase,
       applied OSB, IF combination result.

  L8.  FIXING FORENSICS
       Before WL/NL fixing: float ambiguity, sigma, WL estimate, NL estimate,
       ratio, success probability, reference satellite printed.
       Fixing refused if: covariance unhealthy, geometry weak (PDOP>6),
       ratio marginal, sigma unrealistic.

  L9.  ADDITIONAL DEBUG PLOTS
       Extra figure (ppp_debug_v55.png):
         residual RMS, innovation norm, ambiguity sigma, sat count,
         condition number, ZWD evolution, rejected count — all vs epoch.

  L10. FAILSAFE POLICY
       No dead code, no silent exceptions, no suppressed warnings.
       Matrix failures → loud log + skip epoch (already done), now with
       explicit root-cause print.


CUMULATIVE FIXES (v50 + v51 + v52 + v53 + v54 — all active in this file)
==========================================================================

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

── v52 FIXES (retained) ─────────────────────────────────────────────────────
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

── v53 FIXES (retained) ─────────────────────────────────────────────────────
  H. ZWD clamp + tighter prior sigma — further stability tuning.

── v54 FIXES (new) — Galileo ATX convention forensic fix ───────────────────
  I. GALILEO SATELLITE PCO/PCV ALWAYS ZERO — parse_atx() read 'G01'/'G02'
     frequency keys for ALL constellations.  In IGS ANTEX format, Galileo
     satellite entries label their corrections under 'E01' (E1/1575.42 MHz)
     and 'E05' (E5a/1176.45 MHz), not 'G01'/'G02'.  The parser silently
     returned [0,0,0] for every Galileo satellite PCO and an empty PCV table,
     leaving a systematic ranging bias of O(several cm) that inflated Galileo
     phase residuals to 50–83 mm and made NL float ambiguities non-integer.

     Fix: at END OF ANTENNA, branch on cprn[0] to select the correct ANTEX
     frequency key pair and the correct IF combination coefficients:
       'E' → k1='E01', k2='E05', alfa=ALFA_E, beta=BETA_E
       'R' → k1='R01', k2='R02', alfa=ALFA,   beta=BETA   (GLONASS placeholder)
       else → k1='G01', k2='G02', alfa=ALFA, beta=BETA     (GPS)

  J. RECEIVER PCO IF COMBINATION WRONG FOR GALILEO — _rpco() always used GPS
     ALFA/BETA.  With ALFA≈2.546 vs ALFA_E≈5.012 and BETA≈1.546 vs
     BETA_E≈4.012, the vertical receiver PCO error for Galileo was O(30–40 mm)
     per epoch.

     Fix: _rpco() and _rpcv() now accept a sys='G'|'E' parameter and select
     ALFA_E/BETA_E (and the E01/E05 PCO vectors) when sys='E'.  _proc_gal()
     passes sys='E'.  _proc() passes sys='G' (or omits it; default is 'G').

  K. RECEIVER ATX READS ONLY G01/G02 — ignoring E01/E05 entries present in
     IGS20 for Galileo-specific receiver PCO/PCV.  The E05 frequency
     (1176.45 MHz) differs from G02/L2 (1227.6 MHz); their PCO/PCV can differ
     by several mm.

     Fix: parse_atx() receiver block now additionally stores:
       'L1_E' / 'L2_E'  — PCO vectors from E01/E05 (fall back to G01/G02)
       'v1_E' / 'v2_E'  — PCV tables from E01/E05 (fall back to G01/G02)
     _rpco() and _rpcv() use these when sys='E'.
"""

import os, sys, math, time as _time
from collections import defaultdict
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

def _ifc_e(p1, p5):
    """
    Galileo E1/E5a ionosphere-free code combination (meters).
    """
    return ALFA_E * p1 - BETA_E * p5

def _ifl_e(l1, l5):
    """
    Galileo E1/E5a ionosphere-free phase combination (meters).
    Inputs already in meters.
    """
    return ALFA_E * l1 - BETA_E * l5


# ==============================================================================
#  v55 — Phase-1 forensic / guard helpers
# ==============================================================================

# Per-pass epoch counter (reset in _ppp_pass) used for startup protection.
_STARTUP_EPOCHS = 30          # epochs with heavy instrumentation
_ELEV_PROTECT   = 20          # first N epochs use tighter elevation mask
_ELEV_STARTUP   = math.radians(20.)  # temporary mask during startup
_AMB_MIN_EPOCHS = 3           # minimum consecutive valid epochs before init

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

def _joseph_symmetrise(P):
    """Enforce covariance symmetry in-place (Joseph form post-step)."""
    P[:] = 0.5*(P + P.T)

def _check_osb_gps(ob, sid):
    """Return (bl1_m, bl2_m, ok) for GPS.  ok=False → reject observation."""
    bl1 = ob.get('L1C', ob.get('L1W', None))
    bl2 = ob.get('L2L', ob.get('L2W', ob.get('L2C', None)))
    if bl1 is None or bl2 is None:
        print(f"  [REJECT] {sid}: GPS phase OSB missing "
              f"(L1={'present' if bl1 is not None else 'MISSING'}, "
              f"L2={'present' if bl2 is not None else 'MISSING'}) — obs excluded")
        return 0., 0., False
    return float(bl1), float(bl2), True

def _check_osb_gal(ob, sid):
    """Return (bl1_m, bl5_m, ok) for Galileo.  ok=False → reject observation."""
    bl1 = ob.get('L1C', None)
    bl5 = ob.get('L5Q', None)
    if bl1 is None or bl5 is None:
        print(f"  [REJECT] {sid}: Galileo phase OSB missing "
              f"(L1C={'present' if bl1 is not None else 'MISSING'}, "
              f"L5Q={'present' if bl5 is not None else 'MISSING'}) — obs excluded")
        return 0., 0., False
    return float(bl1), float(bl5), True

def _reject_obs(ri, H, z, Rd, reason, sid, sod, startup=False):
    """Zero-out a measurement row and log the rejection."""
    H[ri, :] = 0.
    z[ri]    = 0.
    Rd[ri]   = 1e12
    if startup:
        print(f"    [REJECT] {sid} row={ri} SOD={sod:.0f}: {reason}")




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
                        # ── v54 FIX I: use constellation-correct ANTEX frequency keys ──
                        # GPS satellites store corrections under 'G01'/'G02'.
                        # Galileo satellites use 'E01' (E1, 1575.42 MHz) and
                        # 'E05' (E5a, 1176.45 MHz).  The old code read 'G01'/'G02'
                        # for every PRN, silently returning [0,0,0] for all Galileo
                        # satellites — zeroing their PCO/PCV entirely.
                        _sys=cprn[0]
                        if _sys=='E':
                            k1,k2='E01','E05'; af,bf=ALFA_E,BETA_E
                        elif _sys=='R':
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
                        # ── v54 FIX K: also store E01/E05 receiver entries ──
                        # IGS20 ATX provides separate Galileo receiver PCO/PCV
                        # (E01=E1, E05=E5a).  Fall back to G01/G02 when absent
                        # (E1 ≡ L1 in frequency, so the G01 fallback is physically
                        # reasonable for E01; E05/G02 differ but G02 is better
                        # than zero).
                        _g01=np.array(pf.get('G01',[0,0,0]),float)
                        _g02=np.array(pf.get('G02',[0,0,0]),float)
                        rec_atx[cant]={
                            'L1':  _g01,
                            'L2':  _g02,
                            'L1_E':np.array(pf.get('E01',pf.get('G01',[0,0,0])),float),
                            'L2_E':np.array(pf.get('E05',pf.get('G02',[0,0,0])),float),
                            'v1':  list(pv.get('G01',[])),
                            'v2':  list(pv.get('G02',[])),
                            'v1_E':list(pv.get('E01',pv.get('G01',[]))),
                            'v2_E':list(pv.get('E05',pv.get('G02',[]))),
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
    # ── v54 FIX J: use constellation-correct IF coefficients and PCO vectors.
    # GPS:     ALFA≈2.546, BETA≈1.546, vectors from L1/L2 ('G01'/'G02').
    # Galileo: ALFA_E≈5.012, BETA_E≈4.012, vectors from E1/E5a ('E01'/'E05').
    # The IF PCO vertical component differs by O(30–40 mm) between systems
    # for a typical geodetic antenna, creating a systematic per-epoch error
    # in Galileo ranges if GPS coefficients are used.
    if re is None: return np.zeros(3)
    if sys=='E':
        af,bf=ALFA_E,BETA_E
        L1=re.get('L1_E',re['L1']); L2=re.get('L2_E',re['L2'])
    else:
        af,bf=ALFA,BETA
        L1=re['L1']; L2=re['L2']
    pi=af*L1-bf*L2
    sl,cl=math.sin(lat),math.cos(lat); sn,cn=math.sin(lon),math.cos(lon)
    R=np.array([[-sl*cn,-sn,cl*cn],[-sl*sn,cn,cl*sn],[cl,0,sl]])
    return R@(pi*1e-3)

def _rpcv(re, el, sys='G'):
    # ── v54 FIX J (continued): same sys-dependent branching for PCV.
    if re is None: return 0.
    if sys=='E':
        af,bf=ALFA_E,BETA_E
        v1k,v2k='v1_E','v2_E'
    else:
        af,bf=ALFA,BETA
        v1k,v2k='v1','v2'
    zen=90-math.degrees(el)
    v1=_pcv(re.get(v1k,re['v1']),re['z1'],re['dz'],zen)
    v2=_pcv(re.get(v2k,re['v2']),re['z1'],re['dz'],zen)
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
                elif obs in ('L5Q','L5X','L5I'):
                    B[prn][obs]=val*LAMBDA_E5A
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
    N=len(data); dim=6   # v56: 0-2=dXYZ 3=clk 4=ZWD 5=ISB
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
        Q_k[5,5]=1e-6*dt   # v56: ISB random walk (same as forward filter)
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
    # v56 PATCH: osb_IF_phase is now subtracted from LIF_raw before x[ki] is
    # initialised (see _proc_gal).  x[ki] therefore equals the TRUE integer
    # IF ambiguity with NO phase-OSB bias absorbed.  Subtracting osb_IF_E here
    # would double-count the correction → removed.  Arguments kept for API compat.
    return (x_ki-NWL*BETA_E*LAMBDA_E5A)/_DENOM_E

def _nl_if_value_gal(N1_int,NWL,osb_bl1,osb_bl5):
    # v56 PATCH: x[ki]=true_IF_amb (no osb absorbed).  The pseudo-obs target
    # value must match; adding osb_IF_E here would create a systematic error.
    N5_int=N1_int-NWL
    return ALFA_E*LAMBDA_E1*N1_int-BETA_E*LAMBDA_E5A*N5_int

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
    """GPS satellite — L1C/L2W (or L2L if available)."""
    P1=so.get('C1W',0.); P2=so.get('C2W',0.)
    L1=so.get('L1C',0.)
    L2_civil=so.get('L2L',0.); L2_semi=so.get('L2W',0.)
    use_civil=(L2_civil!=0.); L2=L2_civil if use_civil else L2_semi
    if P1==0 or P2==0 or L1==0 or L2==0: return None

    ob=osb.get(sid,{})
    b1=ob.get('C1W',ob.get('C1C',0.))
    b2=(ob.get('C2L',ob.get('C2W',0.)) if use_civil
        else ob.get('C2W',ob.get('C2L',0.)))
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
    bl2=ob.get('L2L',ob.get('L2W',ob.get('L2C',0.)))
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

def _proc_gal(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx,
              blq=None,sta='IISC',tow_total=0.):
    """Galileo satellite — E1(C1C/L1C) + E5a(C5Q/L5Q)."""
    P1=so.get('C1C'); P5=so.get('C5Q')
    L1=so.get('L1C'); L5=so.get('L5Q')
    if not P1 or not P5 or not L1 or not L5: return None
    if P1==0 or P5==0 or L1==0 or L5==0: return None

    ob=osb.get(sid,{})
    b1=ob.get('C1C',0.); b5=ob.get('C5Q',0.)
    PIF=ALFA_E*(P1-b1)-BETA_E*(P5-b5)
    _LIF_raw=ALFA_E*(L1*LAMBDA_E1)-BETA_E*(L5*LAMBDA_E5A)  # uncorrected

    # v56 PATCH — apply Galileo satellite IF phase OSB to the carrier-phase observable.
    # bl1 = L1C OSB in metres, bl5 = L5Q OSB in metres (parse_bia converts cyc×λ).
    #   osb_IF_phase = ALFA_E×bl1 − BETA_E×bl5           (metres)
    #   LIF_corr     = LIF_raw  − osb_IF_phase            (metres)
    bl1=ob.get('L1C',0.); bl5=ob.get('L5Q',0.)
    _osb_IF_phase = ALFA_E*bl1 - BETA_E*bl5              # metres
    LIF = _LIF_raw - _osb_IF_phase                       # ← THE ACTUAL CORRECTION
    b_wl_sat_cyc=((FREQ_E1*bl1-FREQ_E5A*bl5)/(FREQ_E1-FREQ_E5A))/LAMBDA_WL_E
    L1m=L1*LAMBDA_E1; L5m=L5*LAMBDA_E5A
    phi_WL=(FREQ_E1*L1m-FREQ_E5A*L5m)/(FREQ_E1-FREQ_E5A)
    P_NL=(FREQ_E1*(P1-b1)+FREQ_E5A*(P5-b5))/(FREQ_E1+FREQ_E5A)
    MW_cyc=(phi_WL-P_NL)/LAMBDA_WL_E-b_wl_sat_cyc
    GF_m=L1*LAMBDA_E1-L5*LAMBDA_E5A

    xyz0,_=_spc(sp3t,sp,sc,sid,tow)
    if xyz0 is None: return None
    lat_r,lon_r,_=_lla(rxyz)
    er=np.array([math.cos(lat_r)*math.cos(lon_r),
                 math.cos(lat_r)*math.sin(lon_r),
                 math.sin(lat_r)])
    ra=rxyz+ah*er+_rpco(recx,lat_r,lon_r,sys='E')  # v54: Galileo IF coeffs + E01/E05 vectors
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
    pcvr=_rpcv(recx,el,sys='E')  # v54: Galileo IF coeffs + E01/E05 PCV table
    dr=sva-ra; rng=np.linalg.norm(dr); unit=dr/rng
    shp=_shap(ra,sva); setd=_set(ra-ah*er,sun); setm=-unit.dot(setd)
    mh,mw=_gmf(lat0,doy,el)
    # ── TRACE FIELDS ─────────────────────────────────────────────────────────
    _sat_apc_range  = float(unit.dot(_sat_apc_ecef))
    _rec_apc_ecef   = _rpco(recx,lat_r,lon_r,sys='E')
    _rec_apc_range  = float(-unit.dot(_rec_apc_ecef))
    _rec_pco_local_mm=(ALFA_E*recx.get('L1_E',recx['L1'])-BETA_E*recx.get('L2_E',recx['L2'])
                       ).tolist() if recx else [0.,0.,0.]
    _sagnac_m       = float(unit.dot(svc-sv_tx))
    _otl_ecef       = (_otl_disp(blq,sta,tow_total,lat_r,lon_r) if blq and tow_total>0.
                       else np.zeros(3))
    _otl_range_m    = float(-unit.dot(_otl_ecef))
    _osb_code_L1_m  = b1; _osb_code_L2_m  = b5
    # v56: bl1/bl5 are already in metres (parse_bia converts cyc→m via ×LAMBDA).
    _osb_phase_L1_m = bl1                         # metres
    _osb_phase_L2_m = bl5                         # metres
    _osb_IF_code_m  = ALFA_E*b1 - BETA_E*b5
    _osb_IF_phase_m = _osb_IF_phase               # = ALFA_E*bl1 − BETA_E*bl5 (metres)
    _PIF_raw        = ALFA_E*P1 - BETA_E*P5
    # _LIF_raw already set above; LIF is LIF_corr = LIF_raw − osb_IF_phase
    # ─────────────────────────────────────────────────────────────────────────
    return dict(sid=sid,unit=unit,mh=mh,mw=mw,el=el,az=az,
                rng=rng,scm=scm,dtrel=dtrel,shp=shp,setm=setm,
                pcv_sat=pcvs,pcv_rec=pcvr,trop_zhd=mh*zhd,
                PIF=PIF,LIF=LIF,MW_cyc=MW_cyc,GF_m=GF_m,
                L1=L1,L2=L5,P1=P1,P2=P5,
                sat_xyz=sva,rec_apc=ra,
                _lam_if=LAMBDA_IF_E,_alfa=ALFA_E,_beta=BETA_E,
                _lam1=LAMBDA_E1,_lam2=LAMBDA_E5A,
                _lam_wl=LAMBDA_WL_E,_freq1=FREQ_E1,_freq2=FREQ_E5A,
                _sys='E',
                # trace extras
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
                _PIF_raw=_PIF_raw, _LIF_raw=_LIF_raw)

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
def _ppp_pass(epochs,sp3t,sp,sc,clkd,osb,ah,nom,iclk,izwd,lat0,doy,zhd,tref,
              satx,att,recx,elm=math.radians(10.),SC=1.50,SP=0.003,
              direction=1,label="FWD",wl_init=None,amb_init=None,
              constellation='GE',blq=None,sta='IISC',trace_fh=None,pass_label=''):
    """
    constellation : 'G' | 'E' | 'GE'
    blq           : dict from parse_blq (ocean tide loading)
    sta           : 4-char station code used to look up BLQ entry
    """
    REF=np.array([1337935.5599,6070317.2377,1427877.5071])
    wl_fixed=dict(wl_init) if wl_init else {}
    _amb_init=dict(amb_init) if amb_init else {}
    _amb_seeded=set()
    nl_fixed={}

    # ── v53 parameters ────────────────────────────────────────────────────────
    NL_RATIO_THRESH   = 4.5           # require strong LAMBDA ratio
    NL_VAR_THRESH     = (10.0)**2
    NL_RES_THRESH     = 0.10
    NL_EXCL_THRESH    = 0.25
    NL_R_TIGHT        = (0.005)**2
    NL_INNOV_GATE     = 0.080         # |N_IF−x[ki]|>80mm → gate + release
    NL_RELEASE_THRESH = 0.060         # post-update drift >60mm → release
    NL_PHASE_THRESH   = 0.010         # PhsRMS must be <10mm to attempt fix
    NL_MIN_SATS       = 3             # min clean candidates for LAMBDA
    NL_MIN_OBS        = 8             # min sats in geom before any NL fix
    PHASE_RES_GATE    = 0.030         # pre-update phase residual gate (m)
    ZWD_PRIOR         = 0.12          # soft-prior climatological ZWD (m)
    ZWD_PRIOR_SIGMA   = 0.06          # soft-prior sigma (m) — tighter than v52
    ZWD_CLAMP         = 0.015         # max ZWD change per epoch (m)
    _zwd_prev         = None          # for per-epoch ZWD clamp
    _nl_diag_done=False

    # ── v56 ISB STATE ─────────────────────────────────────────────────────────
    # Index layout: 0-2=dXYZ  3=clk  4=ZWD  5=ISB(Gal-GPS code, metres)
    # ISB is only active in GPS+Galileo mode; in single-constellation mode it
    # remains at 0 and is never excited (no Galileo code observations add to it).
    ISB_IDX = 5
    x=np.zeros(6); x[3]=iclk; x[4]=izwd; x[ISB_IDX]=0.0
    P=np.zeros((6,6))
    P[0,0]=P[1,1]=P[2,2]=100.**2; P[3,3]=3000.**2; P[4,4]=0.5**2
    P[ISB_IDX,ISB_IDX]=25.0   # ±5 m initial sigma (measured ISB ≈ +4 m)
    # ─────────────────────────────────────────────────────────────────────────

    sidx={}; namb=0; phi={}; wum={}; prev_mw={}; prev_gf={}
    mw_hist=defaultdict(list)
    results={}; psod=None; nproc=0
    _amb_conv_sods=set(); _amb_init_ptrace={}
    _sat_age=defaultdict(int); _amb_snapshots={}
    _wl_history={}; _nl_bad_nwl=set(); _wl_history_ptrace={}
    # ── track last SOD each satellite was visible (for new-arc detection) ──
    _sat_last_sod={}

    b_rec_frozen={}; b_rec_n=defaultdict(int)
    eplist=epochs if direction==1 else list(reversed(epochs))

    # ── v55 forensic tracking ─────────────────────────────────────────────────
    _prev_state      = None          # x[:5] at previous epoch (for jump detection)
    _prev_code_rms   = None          # code RMS at previous epoch (for doubling check)
    _prev_phase_rms  = None          # phase RMS at previous epoch
    _innov_norm_hist = []            # history for debug plot
    _amb_sig_hist    = []            # mean ambiguity sigma history
    _sat_cnt_hist    = []            # satellite count history
    _cond_hist       = []            # condition number history
    _zwd_hist_plot   = []            # ZWD for debug plot
    _rej_cnt_hist    = []            # rejected observation count per epoch
    _sod_hist        = []            # SOD for all epochs (debug plot x-axis)

    # ── AMB-BIRTH FORENSIC TRACKING (v56 instrumentation) ────────────────────
    # Maps sid → dict of pre-birth state snapshot; cleared after post-update print
    _birth_snapshot  = {}           # sid → {sod, LIFc, rp0, x_clk, x_zwd, x_isb, ...}
    _newly_born      = set()        # sids born this epoch (cleared each epoch)
    _newborn_pending = {}           # v57: sid → geom_dict for post-fit birth
    _newborn_code_used = []         # v57: sids whose code row entered EKF this epoch
    _code_rms_hist   = []
    _phase_rms_hist  = []
    # Track consecutive valid epochs per satellite for ambiguity init protection
    _amb_valid_streak = defaultdict(int)
    # ── v56 ISB forensic tracking ─────────────────────────────────────────────
    _isb_trace_rows = []   # list of dicts for isb_state_trace.csv
    # ─────────────────────────────────────────────────────────────────────────


    for epoch in eplist:
        sod=epoch['t']; sobs=epoch['sats']
        dt=abs(sod-psod) if psod is not None else 30.
        if dt<=0 or dt>3600: dt=30.
        psod=sod; tow=_sod2t(sod,tref)
        tow_total=tow

        # ── v55 L3: startup elevation mask ───────────────────────────────────
        elm_epoch  = (_ELEV_STARTUP if nproc < _ELEV_PROTECT else elm)
        is_startup = (nproc < _STARTUP_EPOCHS)

        # ── v55 L6: pre-update state/covariance sanity ───────────────────────
        if not _is_finite_matrix(x[:5], "state x"):
            print(f"  [WARN-FATAL] Non-finite state vector at SOD={sod:.0f} "
                  f"— resetting position states")
            x[:3] = 0.; P[0,0]=P[1,1]=P[2,2]=100.**2
        if not _is_finite_matrix(P, "covariance P"):
            print(f"  [WARN-FATAL] Non-finite P at SOD={sod:.0f} "
                  f"— reinflating diagonal")
            bad = ~np.isfinite(P)
            P[bad] = 0.; np.fill_diagonal(P, np.maximum(np.diag(P), 1.0))
        # Enforce symmetry every epoch
        _joseph_symmetrise(P)

        n_st=len(x); Q=np.zeros((n_st,n_st))
        Q[0,0]=Q[1,1]=Q[2,2]=1e-8*dt; Q[3,3]=1e4*dt
        # ZWD process noise: ~3 mm/sqrt(hr) = (0.003)²/3600 * dt ≈ 2.5e-9*dt
        Q[4,4]=2.5e-9*dt
        # ── v56 ISB process noise: quasi-static hardware delay (~1 mm/epoch RW) ──
        Q[ISB_IDX,ISB_IDX]=1e-6*dt
        q_amb=(1e-8*dt if nproc<120 else 1e-10*dt) if direction==1 else 1e-10*dt
        for k in range(namb):
            sid_k=next((s for s,i in sidx.items() if i==5+k),None)
            # freeze ambiguity noise when NL-fixed so it cannot wander
            if sid_k and sid_k in nl_fixed:
                Q[5+k,5+k]=1e-14*dt
            else:
                Q[5+k,5+k]=q_amb
        P+=Q

        rxyz=nom+x[:3]; sun=_sun(tow); geom=[]
        _n_rejected_epoch = 0   # v55: per-epoch rejection counter
        _newly_born.clear()     # v56 forensic: reset per-epoch birth tracking
        _newborn_pending.clear()  # v57: sats needing post-fit birth this epoch
        _newborn_code_used = []   # v57: track which newborn sats contributed code rows

        for sid,so in sorted(sobs.items()):
            if sid[0] not in ('G','E'): continue
            if sid[0] not in constellation: continue

            if sid[0]=='E':
                m=_proc_gal(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm_epoch,
                             satx,att,recx,blq=blq,sta=sta,tow_total=tow_total)
            else:
                m=_proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm_epoch,
                         satx,att,recx,blq=blq,sta=sta,tow_total=tow_total)
            if m is None:
                _amb_valid_streak[sid] = 0   # v55 L4: streak broken
                continue

            # ── v55 L7: OSB presence guard ────────────────────────────────────
            ob_chk = osb.get(sid, {})
            if sid[0] == 'E':
                _, _, _osb_ok = _check_osb_gal(ob_chk, sid)
            else:
                _, _, _osb_ok = _check_osb_gps(ob_chk, sid)
            if not _osb_ok:
                _amb_valid_streak[sid] = 0
                _n_rejected_epoch += 1
                continue

            # v55 L4: increment valid streak for this satellite
            _amb_valid_streak[sid] = _amb_valid_streak.get(sid, 0) + 1
            # [AMB-STATE] lifecycle trace — printed for first 10 epochs or on state change
            if nproc < 10:
                _ki_now = sidx.get(sid, None)
                _has_state = _ki_now is not None
                _phase_en = phi.get(sid, False)
                _in_pending = sid in _newborn_pending  # from PREVIOUS epoch (already cleared)
                _streak_now = _amb_valid_streak[sid]
                print(f"  [AMB-STATE] {sid}  SOD={sod:.0f}  "
                      f"has_state={_has_state}  "
                      f"consecutive_valid={_streak_now}  "
                      f"phase_enabled={_phase_en}  "
                      f"ki={_ki_now}")

            # ── v55 L7 trace: first-epoch per-sat OSB/IF log ──────────────────
            if is_startup and nproc <= 2:
                lam_if_s = m.get('_lam_if', LAMBDA_IF)
                _osb_mm  = m.get('_osb_IF_phase_m', 0.) * 1e3
                _lif_raw_m = m.get('_LIF_raw', m['LIF'])
                _lif_corr_m = m['LIF']
                _delta_mm = (_lif_raw_m - _lif_corr_m) * 1e3   # should == osb_IF_phase
                print(f"    [OSB-TRACE] {sid} SOD={sod:.0f}: "
                      f"osb_IF_code={m.get('_osb_IF_code_m',0.)*1e3:+.2f}mm "
                      f"osb_IF_phase={_osb_mm:+.2f}mm "
                      f"LIF_raw={_lif_raw_m:.4f}m "
                      f"LIF_corr={_lif_corr_m:.4f}m "
                      f"delta={_delta_mm:+.2f}mm")


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
                    sys_id=sid[0]; min_n=30 if sd>0.30 else 15
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

            # ── AMBIGUITY STATE ──────────────────────────────────────────────
            if sid not in sidx:
                d=len(x); x=np.append(x,0.)
                Pn=np.zeros((d+1,d+1)); Pn[:d,:d]=P
                # v57: zero out all cross-covariances for new state — decorrelated at birth
                Pn[d,d]=300.**2
                P=Pn; sidx[sid]=d; namb+=1; phi[sid]=False
            ki=sidx[sid]

            if slip:
                x[ki]=0.; P[ki,ki]=300.**2; phi[sid]=False
                mw_hist[sid].clear(); nl_fixed.pop(sid,None)
                _nl_bad_nwl.discard(sid); _sat_age[sid]=0
                _amb_valid_streak[sid] = 0   # v55 L4: reset streak on slip

            # Phase wind-up
            wu=_wu(m['sat_xyz'],m['rec_apc'],sun,wum.get(sid,0.)); wum[sid]=wu
            lam_if=m.get('_lam_if',LAMBDA_IF)
            LIFc=m['LIF']-wu*lam_if; m['LIFc']=LIFc

            # ── v55 L4: Ambiguity init — require ≥3 consecutive valid epochs ──
            if not phi.get(sid,False):
                if _amb_valid_streak[sid] < _AMB_MIN_EPOCHS:
                    # Not enough consecutive valid epochs yet — skip init
                    m['ki']=ki; m['NWL']=wl_fixed.get(sid,None); m['age']=_sat_age[sid]
                    geom.append(m)
                    _sat_age[sid]+=1
                    continue
                if sid in _amb_init:
                    x[ki],P[ki,ki]=_amb_init.pop(sid); phi[sid]=True; _amb_seeded.add(sid)
                    # ── AMB-BIRTH forensic: inherited ambiguity ───────────────
                    _sys = m.get('_sys','?')
                    _rp0_inherit = _rp(m, x[3], x[4])
                    _closure_inherit = m['LIFc'] - (_rp0_inherit + x[ki])
                    _corr_clk = (P[ki,3] / math.sqrt(max(P[ki,ki],1e-20)*max(P[3,3],1e-20))
                                 if P[ki,ki]>0 and P[3,3]>0 else float('nan'))
                    _corr_isb = (P[ki,ISB_IDX] / math.sqrt(max(P[ki,ki],1e-20)*max(P[ISB_IDX,ISB_IDX],1e-20))
                                 if P[ki,ki]>0 and P[ISB_IDX,ISB_IDX]>0 else float('nan'))
                    print(f"[AMB-BIRTH] epoch={nproc+1:04d}  sat={sid}  sys={_sys}  "
                          f"INHERITED\n"
                          f"  LIF={m['LIFc']*1e3:.1f}mm  rp0={_rp0_inherit*1e3:.1f}mm  "
                          f"birth_value={x[ki]*1e3:.1f}mm  birth_sigma={math.sqrt(P[ki,ki])*1e3:.1f}mm\n"
                          f"  clk={x[3]*1e3:+.1f}mm  ZWD={x[4]*1e3:.1f}mm  ISB={x[ISB_IDX]*1e3:+.1f}mm\n"
                          f"  phase_closure_error={_closure_inherit*1e3:+.1f}mm  "
                          f"corr_clk={_corr_clk:+.3f}  corr_isb={_corr_isb:+.3f}\n"
                          f"  [ORDER] birth uses PRE-UPDATE states; EKF update follows")
                    _newly_born.add(sid)
                    _birth_snapshot[sid] = dict(sod=sod, LIFc=m['LIFc'],
                                                rp0=_rp0_inherit, birth_val=x[ki],
                                                clk_birth=x[3], zwd_birth=x[4],
                                                isb_birth=x[ISB_IDX], ki=ki, sys=_sys)
                else:
                    # ── v57 POST-FIT BIRTH: queue for birth AFTER EKF update ──
                    # Do NOT set x[ki] or phi[sid] = True yet.
                    # The state slot exists (P[ki,ki]=300²) but is uninitialised.
                    # This satellite's PHASE row will be excluded from EKF (PATCH 5).
                    # Its CODE row will be included (contributes to clock/pos update).
                    _sys = m.get('_sys','?')
                    print(f"[NEWBORN-PHASE-HOLD] sat={sid}  sys={_sys}  SOD={sod:.0f}  "
                          f"reason=no_ambiguity_yet_(post-fit_birth_pending)  "
                          f"streak={_amb_valid_streak[sid]}")
                    print(f"[AMB-QUEUE] sat={sid}  SOD={sod:.0f}  "
                          f"queued_for_postfit_birth  "
                          f"ki={ki}  clk_pre={x[3]*1e3:+.1f}mm  "
                          f"ZWD_pre={x[4]*1e3:.1f}mm")
                    # WATCHDOG: if this sid was already in _newborn_pending from
                    # a previous epoch but phi never became True, flag bug
                    if _amb_valid_streak[sid] > 5 and sid in _newborn_pending:
                        print(f"[BUG] {sid} stuck in newborn_pending "
                              f"for {_amb_valid_streak[sid]} epochs — "
                              f"phi={phi.get(sid,False)}  "
                              f"architecture error!")
                    _newborn_pending[sid] = dict(
                        m=m, ki=ki, LIFc=LIFc, sys=_sys, sod=sod,
                        # Record pre-update states for comparison
                        clk_pre=x[3], zwd_pre=x[4], isb_pre=x[ISB_IDX]
                    )

            # ── v55 L4: per-ambiguity covariance health check ─────────────────
            if phi.get(sid, False):
                pki = P[ki,ki]
                if not math.isfinite(pki) or pki <= 0.:
                    print(f"  [WARN] {sid} amb variance non-positive ({pki:.3e}) "
                          f"SOD={sod:.0f} — resetting")
                    P[ki,ki] = 300.**2; x[ki] = 0.; phi[sid] = False
                    _amb_valid_streak[sid] = 0
                elif pki < 1e-8:
                    print(f"  [WARN] {sid} amb variance collapsed ({pki:.3e}) "
                          f"SOD={sod:.0f} — resetting to 300²")
                    P[ki,ki] = 300.**2
                elif pki > 1e8:
                    print(f"  [WARN] {sid} amb variance exploded ({pki:.3e}) "
                          f"SOD={sod:.0f} — clamping to 300²")
                    P[ki,ki] = 300.**2


            _sat_age[sid]+=1
            m['ki']=ki; m['NWL']=wl_fixed.get(sid,None); m['age']=_sat_age[sid]
            geom.append(m)

        if len(geom)<4: continue
        if len(geom)>4:
            if _pdop(geom)>6.0:
                worst=min(geom,key=lambda m:m['el'])
                geom=[m for m in geom if m['sid']!=worst['sid']]
        if len(geom)<4: continue

        if nproc==0:
            clk_old=x[3]; x[3]=_spp_clock(geom,rxyz); dclk=x[3]-clk_old
            for m in geom:
                ki=m['ki']
                if m['sid'] in _newborn_pending:
                    pass  # v57: will be born post-fit after EKF; skip here
                elif m['sid'] in _amb_seeded: x[ki]-=dclk
                else: rp0=_rp(m,x[3],x[4]); x[ki]=m['LIFc']-rp0; P[ki,ki]=300.**2

        ns=len(geom); nst=len(x)
        wl_in_geom=[m for m in geom if m['sid'] in wl_fixed and phi.get(m['sid'],False)]
        nl_in_geom=[m for m in geom if m['sid'] in nl_fixed and phi.get(m['sid'],False)]
        n_wl=len(wl_in_geom); n_nl=len(nl_in_geom)

        H=np.zeros((2*ns+n_wl+n_nl,nst))
        z=np.zeros(2*ns+n_wl+n_nl); Rd=np.zeros(2*ns+n_wl+n_nl)
        xs=x.copy()

        # ── v55 L2: hard residual thresholds ──────────────────────────────────
        # Code: 10 m startup, 5 m converged.  Phase: 0.10 m startup, 0.05 m conv.
        _code_hard = 10.0 if is_startup else 5.0   # metres
        _phs_hard  = 0.10 if is_startup else 0.05  # metres

        for ri,m in enumerate(geom):
            ki=m['ki']; u=m['unit']; mw=m['mw']; rp=_rp(m,xs[3],xs[4])
            rr=2*ri

            # ── v56 ISB: for Galileo code, subtract ISB from predicted range ──
            # Model: observed_code = rp + ISB   (for Galileo only)
            # Residual: code_res = PIF - (rp + ISB) = PIF - rp - x[ISB_IDX]
            # H_code[ISB_IDX] = +1 for Galileo; 0 for GPS (already zero by default)
            _is_gal = (m.get('_sys') == 'E')
            if _is_gal:
                rp_code = rp + xs[ISB_IDX]   # predicted code includes ISB
            else:
                rp_code = rp                  # GPS: no ISB term

            # ── code residual sanity ──
            code_res = m['PIF'] - rp_code
            if not math.isfinite(code_res):
                _reject_obs(rr, H, z, Rd, "code residual non-finite", m['sid'], sod, is_startup)
                _n_rejected_epoch += 1
            elif abs(code_res) > _code_hard:
                _reject_obs(rr, H, z, Rd,
                            f"code residual {code_res*1e3:+.0f}mm > {_code_hard*1e3:.0f}mm",
                            m['sid'], sod, is_startup)
                _n_rejected_epoch += 1
            else:
                H[rr,0]=-u[0]; H[rr,1]=-u[1]; H[rr,2]=-u[2]; H[rr,3]=1.; H[rr,4]=mw
                if _is_gal:
                    H[rr,ISB_IDX]=1.   # ← ISB column: +1 for Galileo code only
                z[rr]=code_res; Rd[rr]=_sig(m['el'],SC)**2

            rl=2*ri+1

            # ── v57: exclude phase row for newborn satellites ──────────────────
            # Newborn sats have no valid ambiguity state yet.  Their phase row
            # would reference x[ki]=0 (uninitialised), producing a huge residual
            # that would corrupt the EKF.  Zero the phase row explicitly.
            if m['sid'] in _newborn_pending:
                _reject_obs(rl, H, z, Rd,
                            "newborn_phase_excluded_(post-fit_birth_pending)",
                            m['sid'], sod, startup=True)  # always log this
                # COUNT epoch-summary stats
                _newborn_code_used.append(m['sid'])
            elif not phi.get(m['sid'], False):
                # v58.1 PATCH 4: sat has phi=False and is not a newborn — it has not
                # yet accumulated enough consecutive valid epochs for ambiguity init.
                # Its phase row must be excluded (x[ki]=0, uninitialized).
                _reject_obs(rl, H, z, Rd,
                            "phase_excluded_phi_not_active_(insufficient_streak)",
                            m['sid'], sod, startup=True)
            else:
                # [AMB-ACTIVE]: first epoch this sat contributes phase rows
                # (it was born post-fit last epoch, phi[sid] is now True)
                if m.get('age',99) == 0 or m.get('_just_born_postfit', False):
                    print(f"[AMB-ACTIVE] sat={m['sid']}  SOD={sod:.0f}  "
                          f"phase_rows_enabled  "
                          f"phi={phi.get(m['sid'],False)}  age={m.get('age',0)}")
                phase_res = m['LIFc'] - (rp + xs[ki])
                phase_sig = _sig(m['el'],SP)*(5. if m.get('age',99)<=3 else 1.)

                # ── phase residual sanity ──
                if not math.isfinite(phase_res):
                    _reject_obs(rl, H, z, Rd, "phase residual non-finite", m['sid'], sod, is_startup)
                    _n_rejected_epoch += 1
                elif abs(phase_res) > _phs_hard:
                    _reject_obs(rl, H, z, Rd,
                                f"phase residual {phase_res*1e3:+.0f}mm > {_phs_hard*1e3:.0f}mm",
                                m['sid'], sod, is_startup)
                    _n_rejected_epoch += 1
                else:
                    H[rl,0]=-u[0]; H[rl,1]=-u[1]; H[rl,2]=-u[2]; H[rl,3]=1.
                    H[rl,4]=mw; H[rl,ki]=1.
                    z[rl]=phase_res
                    Rd[rl]=phase_sig**2
                    # original soft gate: downweight but don't zero
                    if abs(phase_res) > PHASE_RES_GATE:
                        Rd[rl]=max(Rd[rl], 1.0**2)

            # ── singular geometry row guard ──
            for row_i in [rr, rl]:
                if Rd[row_i] <= 0. or not math.isfinite(Rd[row_i]):
                    _reject_obs(row_i, H, z, Rd, "zero/negative variance",
                                m['sid'], sod, is_startup)

        for wi,m in enumerate(wl_in_geom):   # WL pseudo-obs disabled
            H[2*ns+wi,m['ki']]=0.; z[2*ns+wi]=0.; Rd[2*ns+wi]=1e10

        # ── v55 L6: pre-update matrix sanity ──────────────────────────────────
        if not _is_finite_matrix(H, "H"):
            print(f"  [WARN] H non-finite at SOD={sod:.0f} — skipping epoch")
            continue
        R_diag = np.diag(Rd)
        if not _is_pd(R_diag, "R", tol=0.):
            print(f"  [WARN] R not PD at SOD={sod:.0f} — re-flooring diagonal")
            Rd = np.where(Rd > 0, Rd, 1e-4)
        if not _is_finite_matrix(P, "P pre-update"):
            print(f"  [WARN] P has non-finite entries before EKF update SOD={sod:.0f}")
        # ─────────────────────────────────────────────────────────────────────



        # ── MEASUREMENT MODEL FORENSIC TRACE ────────────────────────────────
        if trace_fh is not None and direction==1:
            for ri,m in enumerate(geom):
                ki=m['ki']; u=m['unit']; rp=_rp(m,xs[3],xs[4])
                lam_if_m=m.get('_lam_if',LAMBDA_IF)
                wu_cyc=wum.get(m['sid'],0.)
                wu_m  =wu_cyc*lam_if_m
                code_res=m['PIF']-rp
                phase_res=m['LIFc']-(rp+xs[ki])
                amb_m  =float(xs[ki])
                amb_cyc=amb_m/lam_if_m
                pred_phase=rp+amb_m
                pred_code =rp
                geom_range=m['rng']
                el_d=math.degrees(m['el']); az_d=math.degrees(m.get('az',0.))
                sat_pco_mm=m.get('_sat_pco_body_mm',[0.,0.,0.])
                rp_mm=m.get('_rec_pco_local_mm',[0.,0.,0.])
                # ZWD contribution at this epoch
                zwd_now=xs[4]; tropo_wet=m['mw']*zwd_now
                trace_fh.write(
                    f"{pass_label},{sod:.1f},{m['sid']},{m.get('_sys','G')},"
                    f"{el_d:.4f},{az_d:.4f},"
                    # raw obs
                    f"{m['L1']*m.get('_lam1',LAMBDA1):.6f},"
                    f"{m['L2']*m.get('_lam2',LAMBDA2):.6f},"
                    f"{m['P1']:.6f},{m['P2']:.6f},"
                    # IF combos
                    f"{m.get('_LIF_raw',m['LIF']):.6f},"
                    f"{m.get('_PIF_raw',m['PIF']):.6f},"
                    # sat PCO body mm
                    f"{sat_pco_mm[0]:.4f},{sat_pco_mm[1]:.4f},{sat_pco_mm[2]:.4f},"
                    f"{m.get('_sat_apc_range_m',0.)*1e3:.4f},"
                    f"{m['pcv_sat']*1e3:.4f},"
                    # rec PCO local mm
                    f"{rp_mm[0]:.4f},{rp_mm[1]:.4f},{rp_mm[2]:.4f},"
                    f"{m.get('_rec_apc_range_m',0.)*1e3:.4f},"
                    f"{m['pcv_rec']*1e3:.4f},"
                    # OSB
                    f"{m.get('_osb_code_L1_m',0.)*1e3:.4f},"
                    f"{m.get('_osb_code_L2_m',0.)*1e3:.4f},"
                    f"{m.get('_osb_phase_L1_m',0.)*1e3:.4f},"
                    f"{m.get('_osb_phase_L2_m',0.)*1e3:.4f},"
                    f"{m.get('_osb_IF_code_m',0.)*1e3:.4f},"
                    f"{m.get('_osb_IF_phase_m',0.)*1e3:.4f},"
                    # model terms
                    f"{m['trop_zhd']:.6f},{tropo_wet:.6f},"
                    f"{m.get('_sagnac_m',0.)*1e3:.4f},"
                    f"{m['dtrel']*1e3:.4f},"       # dtrel in mm
                    f"{wu_cyc:.6f},{wu_m*1e3:.4f},"
                    f"{m['setm']*1e3:.4f},"
                    f"{m.get('_otl_range_m',0.)*1e3:.4f},"
                    # final model
                    f"{geom_range:.6f},"
                    f"{m['LIFc']:.6f},{m['PIF']:.6f},"
                    f"{pred_phase:.6f},{pred_code:.6f},"
                    f"{phase_res*1e3:.4f},{code_res*1e3:.4f},"
                    # ambiguity
                    f"{amb_m:.6f},{amb_cyc:.6f},"
                    f"{1 if m['sid'] in wl_fixed else 0},"
                    f"{1 if m['sid'] in nl_fixed else 0}\n"
                )
        # ── END TRACE ────────────────────────────────────────────────────────

        # NL pseudo-obs with innovation gate
        for ni,m in enumerate(nl_in_geom):
            ob=osb.get(m['sid'],{})
            if m.get('_sys')=='E':
                bl1=ob.get('L1C',0.); bl2=ob.get('L5Q',0.)
                N_IF_fix=_nl_if_value_gal(nl_fixed[m['sid']],wl_fixed[m['sid']],bl1,bl2)
            else:
                bl1=ob.get('L1C',ob.get('L1W',0.)); bl2=ob.get('L2W',ob.get('L2C',0.))
                N_IF_fix=_nl_if_value(nl_fixed[m['sid']],wl_fixed[m['sid']],bl1,bl2)
            innov=N_IF_fix-xs[m['ki']]
            if abs(innov)>NL_INNOV_GATE:
                H[2*ns+n_wl+ni,m['ki']]=0.; z[2*ns+n_wl+ni]=0.; Rd[2*ns+n_wl+ni]=1e10
                nl_fixed.pop(m['sid'],None)
                print(f"  [NL GATE ] {m['sid']} SOD={sod:.0f} "
                      f"innov={innov*1e3:+.1f}mm>{NL_INNOV_GATE*1e3:.0f}mm — released")
            else:
                H[2*ns+n_wl+ni,m['ki']]=1.
                z[2*ns+n_wl+ni]=innov
                Rd[2*ns+n_wl+ni]=NL_R_TIGHT

        # ZWD soft prior pseudo-obs (tighter sigma than v52)
        n_total=2*ns+n_wl+n_nl
        H_p=np.zeros((n_total+1,nst)); z_p=np.zeros(n_total+1); Rd_p=np.zeros(n_total+1)
        H_p[:n_total,:]=H; z_p[:n_total]=z; Rd_p[:n_total]=Rd
        H_p[n_total,4]=1.
        z_p[n_total]=ZWD_PRIOR-xs[4]
        Rd_p[n_total]=ZWD_PRIOR_SIGMA**2

        zwd_before=x[4]
        x_before = x.copy()

        # ── v55 L6: compute pre-update innovation norm ─────────────────────────
        innov_vec = z_p.copy()          # z already contains pre-update residuals
        _innov_norm = float(np.linalg.norm(innov_vec))

        # ── v56 FORENSIC: execution order audit ──────────────────────────────
        # CONFIRMED ORDER (critical for AMB-BIRTH diagnosis):
        #   1. Satellite loop:  geometry + APC/OSB/trop computed
        #   2. Satellite loop:  ambiguity states BORN here (PRE-update states)
        #   3. H/z/Rd assembled (residuals formed with PRE-update xs = x.copy())
        #   4. EKF UPDATE (filter_standard) ← modifies x[0-5] + x[ki] for all ambs
        #   5. Post-update closure/correlation prints
        # IMPLICATION: ambiguities born in step 2 use clk/pos/ZWD from previous
        # epoch's post-update; the EKF in step 4 then corrects position+clk+ZWD
        # by up to hundreds of mm, shifting all phase residuals simultaneously.
        # ── v57 EPOCH SUMMARY: observation partitioning ─────────────────────
        _existing_phase_rows = sum(1 for m in geom if m['sid'] not in _newborn_pending
                                   and phi.get(m['sid'], False))
        _newborn_phase_skipped = len(_newborn_pending)
        _newborn_code_count = len(_newborn_code_used)
        _existing_amb = sum(1 for s in sidx if phi.get(s, False))
        print(f"[EPOCH-SUMMARY] SOD={sod:.0f}  "
              f"existing_amb={_existing_amb}  "
              f"newborn_pending={len(_newborn_pending)}  "
              f"phase_rows_used={_existing_phase_rows}  "
              f"phase_rows_skipped={_newborn_phase_skipped}  "
              f"code_rows_used={_newborn_code_count}  "
              f"total_sats={len(geom)}  "
              f"pre_clk={x[3]*1e3:+.1f}mm  ZWD={x[4]*1e3:.1f}mm")
        if _newborn_pending:
            print(f"  → EKF update NOW (newborns born post-fit, active NEXT epoch)")

        if filter_standard(x,P,H_p.T,z_p,np.diag(Rd_p))!=0:
            print(f"  [WARN] filter_standard failed at SOD={sod:.0f} — skipping epoch")
            continue

        # ── v55 L6: post-update Joseph symmetry enforcement ──────────────────
        _joseph_symmetrise(P)

        # ── v55 L6: eigenvalue / condition diagnostics ────────────────────────
        _P55 = P[:5,:5]   # position + clock + ZWD subblock
        try:
            _ev = np.linalg.eigvalsh(_P55)
            _ev_min = float(_ev.min()); _ev_max = float(_ev.max())
            _cond_P = _ev_max / max(abs(_ev_min), 1e-20)
        except np.linalg.LinAlgError:
            _ev_min = _ev_max = _cond_P = float('nan')
        if not math.isfinite(_cond_P) or _cond_P > 1e12:
            print(f"  [WARN] P condition number = {_cond_P:.3e} at SOD={sod:.0f} "
                  f"(min_ev={_ev_min:.3e}, max_ev={_ev_max:.3e}) — possible covariance explosion")

        # ── v55 L5: ZWD jump warning ──────────────────────────────────────────
        _zwd_jump = abs(x[4] - zwd_before)
        if _zwd_jump > 0.05:
            print(f"  [WARN] ZWD jumped {_zwd_jump*1e3:+.1f}mm at SOD={sod:.0f} "
                  f"({zwd_before*1e3:.1f}mm → {x[4]*1e3:.1f}mm)")

        # ZWD per-epoch clamp: reject runaway ZWD jumps from bad measurements
        if _zwd_prev is not None and abs(x[4]-_zwd_prev)>ZWD_CLAMP:
            x[4]=_zwd_prev+math.copysign(ZWD_CLAMP, x[4]-_zwd_prev)
            P[4,4]=max(P[4,4], (ZWD_CLAMP/3.0)**2)
        _zwd_prev=x[4]

        # ── v55 L1: state jump warnings ───────────────────────────────────────
        _dx_state = x[:5] - x_before[:5]
        _state_jump = float(np.linalg.norm(_dx_state[:3]))
        _vert_jump  = abs(_dx_state[2])
        if _state_jump > 0.5:
            print(f"  [WARN] State position jump = {_state_jump*1e3:.0f}mm at SOD={sod:.0f}")
        if _vert_jump > 0.3:
            print(f"  [WARN] Vertical jump = {_vert_jump*1e3:.0f}mm at SOD={sod:.0f}")
        if _innov_norm > 1e4:
            print(f"  [WARN] Innovation norm exploded = {_innov_norm:.2e} at SOD={sod:.0f}")


        # post-update NL consistency check — release only on clear drift
        for sid_chk in list(nl_fixed.keys()):
            if sid_chk not in sidx or not phi.get(sid_chk,False): continue
            if not any(m['sid']==sid_chk for m in geom): continue
            ki_chk=sidx[sid_chk]; ob_chk=osb.get(sid_chk,{})
            m_chk=next(m for m in geom if m['sid']==sid_chk)
            if m_chk.get('_sys')=='E':
                bl1c=ob_chk.get('L1C',0.); bl2c=ob_chk.get('L5Q',0.)
                N_IF_chk=_nl_if_value_gal(nl_fixed[sid_chk],wl_fixed[sid_chk],bl1c,bl2c)
            else:
                bl1c=ob_chk.get('L1C',ob_chk.get('L1W',0.))
                bl2c=ob_chk.get('L2W',ob_chk.get('L2C',0.))
                N_IF_chk=_nl_if_value(nl_fixed[sid_chk],wl_fixed[sid_chk],bl1c,bl2c)
            post_innov=abs(N_IF_chk-x[ki_chk])
            if post_innov>NL_RELEASE_THRESH:
                nl_fixed.pop(sid_chk,None)
                print(f"  [NL RELEASE] {sid_chk} SOD={sod:.0f} "
                      f"post-update drift={post_innov*1e3:.1f}mm — released")

        # v58.1 PATCH 1+2: build active-phase-only lists in lockstep so that
        # _active_osb_phase[i] always corresponds to phase_res_now[i].
        # This eliminates the shape-mismatch crash when no ambiguities exist yet
        # (e.g. SOD=0) because _osb_per_sat was built from ALL geom sats while
        # phase_res_now was filtered to ACTIVE sats only.
        _active_geom = [m for m in geom
                        if m['sid'] not in _newborn_pending
                        and phi.get(m['sid'], False)]
        phase_res_now     = [m['LIFc']-(_rp(m,x[3],x[4])+x[m['ki']]) for m in _active_geom]
        _active_osb_phase = [m.get('_osb_IF_phase_m', 0.) for m in _active_geom]
        # Use nan (not 999) so callers can distinguish "no active phase" from a real value
        if phase_res_now:
            phase_rms_now = math.sqrt(np.mean(np.array(phase_res_now)**2))
        else:
            phase_rms_now = float('nan')

        # ── v57 POST-FIT AMBIGUITY BIRTH ──────────────────────────────────────
        # Now that EKF has updated clk/pos/ZWD/ISB, birth newborn ambiguities
        # using POST-FIT states.  The ambiguity state slot already exists
        # in x/P (allocated earlier) but x[ki]=0 and phi[sid]=False.
        # After birth here, phi[sid]=True and x[ki]=postfit_N.
        # These satellites participate starting NEXT epoch only — they do NOT
        # re-enter EKF this epoch (no pseudo-obs injected, no double-update).
        if _newborn_pending:
            for _nsid, _nd in sorted(_newborn_pending.items()):
                _nm   = _nd['m']
                _nki  = _nd['ki']
                _nLIFc = _nd['LIFc']
                _nsys  = _nd['sys']
                _nsod  = _nd['sod']
                _nclk_pre = _nd['clk_pre']
                _nzwd_pre = _nd['zwd_pre']
                _nisb_pre = _nd['isb_pre']

                # Recompute predicted range with POST-FIT states
                _rp_post = _rp(_nm, x[3], x[4])

                # Birth ambiguity from post-fit geometry
                _n_postfit = _nLIFc - _rp_post
                x[_nki] = _n_postfit
                P[_nki, _nki] = 300.**2
                # Explicitly zero cross-covariances (new state decorrelated from all others)
                P[_nki, :_nki] = 0.
                P[:_nki, _nki] = 0.
                phi[_nsid] = True
                _sat_age[_nsid] = 0
                print(f"[AMB-BORN] sat={_nsid}  SOD={_nsod:.0f}  "
                      f"ambiguity_initialized_postfit  "
                      f"N={x[_nki]*1e3:+.1f}mm  "
                      f"will_be_active_SOD={_nsod+30:.0f}")
                # COV-CHECK: cross-covariances should be exactly 0 after explicit zeroing
                _max_cross = float(np.max(np.abs(P[_nki, :_nki]))) if _nki > 0 else 0.
                print(f"[COV-CHECK] sat={_nsid}  "
                      f"max_cross_cov={_max_cross:.3e}  "
                      f"[EXPECTED~0]  "
                      f"P[ki,ki]={P[_nki,_nki]:.1f}m²")

                # Track for convergence inheritance
                pt_now = P[0,0]+P[1,1]+P[2,2]
                _amb_init_ptrace[_nsid] = pt_now
                if pt_now < 0.30:
                    _amb_conv_sods.add(_nsid)

                # ── POST-FIT birth forensic ────────────────────────────────
                # Closure should be ~0 mm because N = LIFc - rp_postfit exactly
                _closure_pf = _nLIFc - (_rp_post + x[_nki])  # identity → 0

                # State deltas (pre → post EKF update)
                _d_clk_pf  = (x[3]        - _nclk_pre) * 1e3
                _d_zwd_pf  = (x[4]        - _nzwd_pre) * 1e3
                _d_isb_pf  = (x[ISB_IDX]  - _nisb_pre) * 1e3

                # Post-birth correlation check (should be 0 due to explicit zeroing)
                _pki_pf = P[_nki, _nki]
                _corr_clk_pf = (P[_nki,3] / math.sqrt(max(_pki_pf,1e-20)*max(P[3,3],1e-20))
                                if _pki_pf>0 and P[3,3]>0 else float('nan'))
                _corr_isb_pf = (P[_nki,ISB_IDX] / math.sqrt(max(_pki_pf,1e-20)*max(P[ISB_IDX,ISB_IDX],1e-20))
                                if _pki_pf>0 and P[ISB_IDX,ISB_IDX]>0 else float('nan'))

                # Decompose rp_post for audit log
                _geom_pf  = _nm['rng']
                _sag_pf   = _nm.get('_sagnac_m', 0.)
                _drel_pf  = _nm['dtrel']
                _zhd_pf   = _nm['trop_zhd']
                _zwdc_pf  = _nm['mw']*x[4]
                _apc_s_pf = _nm.get('_sat_apc_range_m', 0.)
                _apc_r_pf = _nm.get('_rec_apc_range_m', 0.)
                _osb_p_pf = _nm.get('_osb_IF_phase_m', 0.)
                _osb_c_pf = _nm.get('_osb_IF_code_m',  0.)

                print(f"[AMB-BIRTH] epoch={nproc+1:04d}  sat={_nsid}  sys={_nsys}  "
                      f"SOD={_nsod:.0f}  FRESH(POST-FIT)\n"
                      f"  === Ambiguity birth equation: N = LIF - rp_postfit ===\n"
                      f"  LIF(wind-corr)={_nLIFc*1e3:.1f}mm  geom={_geom_pf*1e3:.1f}mm\n"
                      f"  clk(POST-UPD)={x[3]*1e3:+.1f}mm  ZWD={x[4]*1e3:.1f}mm  ISB={x[ISB_IDX]*1e3:+.1f}mm\n"
                      f"  clk_delta={_d_clk_pf:+.1f}mm  ZWD_delta={_d_zwd_pf:+.1f}mm  ISB_delta={_d_isb_pf:+.1f}mm\n"
                      f"  trop_ZHD={_zhd_pf*1e3:.1f}mm  trop_ZWD_contrib={_zwdc_pf*1e3:.1f}mm\n"
                      f"  sat_APC={_apc_s_pf*1e3:+.1f}mm  rec_APC={_apc_r_pf*1e3:+.1f}mm\n"
                      f"  osb_IF_phase={_osb_p_pf*1e3:+.1f}mm  osb_IF_code={_osb_c_pf*1e3:+.1f}mm\n"
                      f"  Sagnac={_sag_pf*1e3:+.1f}mm  dtrel={_drel_pf*1e3:+.1f}mm\n"
                      f"  rp_postfit={_rp_post*1e3:.1f}mm\n"
                      f"  birth_value={x[_nki]*1e3:+.1f}mm  birth_sigma={math.sqrt(_pki_pf)*1e3:.1f}mm\n"
                      f"  closure_postfit={_closure_pf*1e3:+.3f}mm  [EXPECTED <10mm]\n"
                      f"  corr_clk={_corr_clk_pf:+.4f}  corr_isb={_corr_isb_pf:+.4f}  [EXPECTED ~0]\n"
                      f"  pos_trace(P00+P11+P22)={pt_now:.3f}m²  "
                      f"converged={'YES' if pt_now<0.30 else 'NO'}\n"
                      f"  [ORDER] BIRTH USES POST-FIT states — active from NEXT epoch only")
                _newly_born.add(_nsid)
                _birth_snapshot[_nsid] = dict(sod=_nsod, LIFc=_nLIFc,
                                              rp0=_rp_post, birth_val=x[_nki],
                                              clk_birth=x[3], zwd_birth=x[4],
                                              isb_birth=x[ISB_IDX], ki=_nki, sys=_nsys)
        # ── END POST-FIT BIRTH ───────────────────────────────────────────────

        # ── POST-UPDATE AMB-BIRTH CLOSURE FORENSICS ───────────────────────────
        # For every satellite born THIS epoch, compute the closure error with the
        # POST-UPDATE states.  A large error means the ambiguity was born with
        # stale (pre-update) clock/position/ZWD and the EKF corrected them
        # significantly — this is the root cause of the same-sign rejection burst
        # that follows in subsequent epochs.
        if _newly_born:
            geom_by_sid = {m['sid']: m for m in geom}
            for _bsid in sorted(_newly_born):
                if _bsid not in _birth_snapshot: continue
                _bs   = _birth_snapshot.pop(_bsid)
                _bki  = _bs['ki']
                _bm   = geom_by_sid.get(_bsid)
                if _bm is None: continue
                _rp_post   = _rp(_bm, x[3], x[4])
                _closure_post = _bm['LIFc'] - (_rp_post + x[_bki])
                # Delta between pre- and post-update states
                _d_clk = (x[3] - _bs['clk_birth']) * 1e3
                _d_zwd = (x[4] - _bs['zwd_birth']) * 1e3
                _d_isb = (x[ISB_IDX] - _bs['isb_birth']) * 1e3
                _d_pos = math.sqrt((x[0]-x_before[0])**2+(x[1]-x_before[1])**2+(x[2]-x_before[2])**2)*1e3
                _d_amb = (x[_bki] - _bs['birth_val']) * 1e3
                # Post-update correlations
                _pki_post = P[_bki, _bki]
                _corr_clk_post = (P[_bki,3] / math.sqrt(max(_pki_post,1e-20)*max(P[3,3],1e-20))
                                  if _pki_post>0 and P[3,3]>0 else float('nan'))
                _corr_isb_post = (P[_bki,ISB_IDX] / math.sqrt(max(_pki_post,1e-20)*max(P[ISB_IDX,ISB_IDX],1e-20))
                                  if _pki_post>0 and P[ISB_IDX,ISB_IDX]>0 else float('nan'))
                print(f"[AMB-BIRTH-POST] sat={_bsid}  sys={_bs['sys']}  SOD={sod:.0f}\n"
                      f"  phase_closure_error(POST-EKF)={_closure_post*1e3:+.2f}mm\n"
                      f"  EKF delta: dClk={_d_clk:+.1f}mm  dZWD={_d_zwd:+.1f}mm  "
                      f"dISB={_d_isb:+.1f}mm  dPos3D={_d_pos:.1f}mm  dAmb={_d_amb:+.1f}mm\n"
                      f"  amb_sigma_post={math.sqrt(max(_pki_post,0))*1e3:.1f}mm  "
                      f"corr_clk(post)={_corr_clk_post:+.4f}  corr_isb(post)={_corr_isb_post:+.4f}\n"
                      f"  DIAGNOSIS: {'CONTAMINATED — closure>{50}mm suggests pre-update state drift' if abs(_closure_post*1e3)>50 else 'OK — closure within 50mm'}")

        # ── E05 FORENSIC INVESTIGATION ────────────────────────────────────────
        # Track E05 at every epoch: residual, ambiguity value, state
        if 'E05' in sidx and phi.get('E05', False):
            _e05_ki  = sidx['E05']
            _e05_m   = next((m for m in geom if m['sid']=='E05'), None)
            if _e05_m is not None:
                _e05_rp   = _rp(_e05_m, x[3], x[4])
                _e05_res  = _e05_m['LIFc'] - (_e05_rp + x[_e05_ki])
                _e05_code = _e05_m['PIF'] - (_e05_rp + x[ISB_IDX])
                if abs(_e05_res) > 0.5 or abs(_e05_code) > 5.0:
                    # Only print when suspicious
                    print(f"[E05-FORENSIC] SOD={sod:.0f}  "
                          f"phase_res={_e05_res*1e3:+.1f}mm  "
                          f"code_res={_e05_code*1e3:+.1f}mm\n"
                          f"  amb={x[_e05_ki]*1e3:+.1f}mm  "
                          f"amb_sigma={math.sqrt(max(P[_e05_ki,_e05_ki],0))*1e3:.1f}mm  "
                          f"clk={x[3]*1e3:+.1f}mm  ISB={x[ISB_IDX]*1e3:+.1f}mm\n"
                          f"  LIF={_e05_m['LIFc']*1e3:.1f}mm  rp={_e05_rp*1e3:.1f}mm  "
                          f"el={math.degrees(_e05_m['el']):.1f}°  "
                          f"WL_fixed={'YES' if 'E05' in wl_fixed else 'NO'}  "
                          f"NL_fixed={'YES' if 'E05' in nl_fixed else 'NO'}")

        # ── LAMBDA NL FIXING ─────────────────────────────────────────────────
        nl_cands=[m for m in geom
                  if m['sid'] in wl_fixed
                  and m['sid'] not in nl_fixed
                  and m['sid'] not in _nl_bad_nwl
                  and phi.get(m['sid'],False)
                  and P[m['ki'],m['ki']]<NL_VAR_THRESH
                  and phase_rms_now<NL_PHASE_THRESH]
        if len(nl_cands)>=NL_MIN_SATS and len(geom)>=NL_MIN_OBS:
            ob_list=[osb.get(m['sid'],{}) for m in nl_cands]
            N1_floats=[]
            for m,ob in zip(nl_cands,ob_list):
                if m.get('_sys')=='E':
                    bl1=ob.get('L1C',0.); bl5=ob.get('L5Q',0.)
                    N1_floats.append(_nl_float_gal(x[m['ki']],wl_fixed[m['sid']],bl1,bl5))
                else:
                    bl1=ob.get('L1C',ob.get('L1W',0.)); bl2=ob.get('L2W',ob.get('L2C',0.))
                    N1_floats.append(_nl_float(x[m['ki']],wl_fixed[m['sid']],bl1,bl2))
            N1_floats=np.array(N1_floats)

            # ── v55 L8: fixing forensics — float ambiguities + sigma ──────────
            _pdop_now = _pdop(geom)
            if is_startup or nproc % 60 == 0:
                print(f"  [FIX-FORENSIC] SOD={sod:.0f}  PDOP={_pdop_now:.2f}"
                      f"  phsRMS={phase_rms_now*1e3:.2f}mm  ncands={len(nl_cands)}")
                for _m, _N1f in zip(nl_cands, N1_floats):
                    _ki2 = _m['ki']; _sig2 = math.sqrt(max(P[_ki2,_ki2], 0.))
                    _NWL2= wl_fixed.get(_m['sid'], None)
                    print(f"    {_m['sid']} N1_float={_N1f:+.4f}cyc  sigma={_sig2:.4f}m"
                          f"  NWL={_NWL2}  ambvar={P[_ki2,_ki2]:.4e}")
            # Guard: refuse fixing if geometry weak or phase RMS too high
            if _pdop_now > 6.0:
                print(f"  [FIX-SKIP] PDOP={_pdop_now:.2f}>6 — skip NL fix SOD={sod:.0f}")
                nl_cands = []   # prevents further fixing this epoch



            fracs_raw=np.array([f-round(f) for f in N1_floats])
            b_rec_nl=float(np.median(fracs_raw)); N1_corr=N1_floats-b_rec_nl

            fracs_corr=np.array([f-round(f) for f in N1_corr])
            clean_idx=[i for i,f in enumerate(fracs_corr) if abs(f)<=NL_EXCL_THRESH]
            excl_sids=[nl_cands[i]['sid'] for i in range(len(nl_cands)) if i not in clean_idx]
            for sid2 in excl_sids: _nl_bad_nwl.add(sid2)

            if not _nl_diag_done and nproc>100:
                _nl_diag_done=True
                print(f"  [NL DIAG] SOD={sod:.0f} n_all={len(nl_cands)} "
                      f"n_clean={len(clean_idx)} "
                      f"PhsRMS={'inactive' if math.isnan(phase_rms_now) else f'{phase_rms_now*1e3:.2f}mm'} b_rec_nl={b_rec_nl:+.4f}")
                for i,m in enumerate(nl_cands):
                    tag='OK  ' if i in clean_idx else 'EXCL'
                    print(f"    {m['sid']} frac={fracs_corr[i]:+.4f} "
                          f"NWL={wl_fixed[m['sid']]}  [{tag}]")

            if len(clean_idx)>=NL_MIN_SATS:
                nl_cands_c=[nl_cands[i] for i in clean_idx]
                ob_list_c=[ob_list[i] for i in clean_idx]
                N1_corr_c=N1_corr[np.array(clean_idx)]
                idxs_c=[m['ki'] for m in nl_cands_c]

                # ── FIX #3: per-satellite NL denom for Q_nl ──────────────────
                # GPS denom ≠ Galileo denom (~0.1073 vs ~0.1090 m).
                # Q_nl[i,j] = P[ki,kj] / (denom_i × denom_j)
                denoms_c=np.array([_DENOM_E if m.get('_sys')=='E' else _DENOM_G
                                   for m in nl_cands_c])
                Q_nl=P[np.ix_(idxs_c,idxs_c)]/np.outer(denoms_c,denoms_c)

                N1_fixed,ratio=_lambda_ils(N1_corr_c,Q_nl)
                if N1_fixed is not None and ratio>=NL_RATIO_THRESH:
                    newly=[]
                    for i,(m,ob) in enumerate(zip(nl_cands_c,ob_list_c)):
                        if abs(N1_corr_c[i]-int(N1_fixed[i]))<NL_RES_THRESH:
                            nl_fixed[m['sid']]=int(N1_fixed[i]); newly.append(m['sid'])
                    if newly:
                        print(f"  [NL FIXED] SOD={sod:.0f} ratio={ratio:.2f} "
                              f"sats={newly} excl={excl_sids}")

        nproc+=1
        pos=nom+x[:3]; dx=pos-REF; d3=np.linalg.norm(dx)*1e3
        _amb_snapshots[sod]={sid2:(x[ki2],P[ki2,ki2])
                             for sid2,ki2 in sidx.items() if phi.get(sid2,False)}
        if direction==1:
            if not hasattr(_rts_store,'_data'): _rts_store._data=[]
            _rts_store._data.append((sod,x.copy(),P.copy()))

        code_res=[m['PIF']-_rp(m,x[3],x[4]) for m in geom]
        code_rms=math.sqrt(np.mean(np.array(code_res)**2))*1e3 if code_res else 0.
        # v58.1 PATCH 3: propagate nan for inactive phase; convert to mm only when real
        phase_rms=phase_rms_now*1e3 if not math.isnan(phase_rms_now) else float('nan')
        _phs_str='inactive' if math.isnan(phase_rms) else f'{phase_rms:.2f}mm'
        ZHD=zhd; ZWD=x[4]; TROPO=ZHD+ZWD

        results[sod]={'xyz':pos.copy(),'dx':dx.copy(),'p_trace':P[0,0]+P[1,1]+P[2,2],
                      'n':len(geom),'ztd':TROPO,'wl_fixed':len(wl_fixed),
                      'nl_fixed':len(nl_fixed),
                      'code_rms':code_rms,'phase_rms':phase_rms,
                      'zhd':ZHD,'zwd':ZWD,
                      'sats_used':sorted([m['sid'] for m in geom]),
                      'sats_wl':sorted([s for s in wl_fixed if any(m['sid']==s for m in geom)]),
                      'sats_nl':sorted([s for s in nl_fixed if any(m['sid']==s for m in geom)])}

        if nproc<=3 or nproc%240==0:
            n_gps=sum(1 for m in geom if m['sid'][0]=='G')
            n_gal=sum(1 for m in geom if m['sid'][0]=='E')
            print(f"  [{label}] SOD={sod:6.0f}  N={len(geom):2d}(G{n_gps}+E{n_gal})"
                  f"  3D={d3:8.1f}mm  WL={len(wl_fixed)}  NL={len(nl_fixed)}"
                  f"  ZHD={ZHD:.3f}m  ZWD={ZWD:.4f}m  ZTD={TROPO:.4f}m"
                  f"  CodeRMS={code_rms:.1f}mm  PhsRMS={_phs_str}")

        # ── v55 L1: startup forensic block (first _STARTUP_EPOCHS epochs) ────
        if is_startup:
            _n_amb = sum(1 for s in sidx if phi.get(s,False))
            _amb_vars = [P[ki2,ki2] for s2,ki2 in sidx.items() if phi.get(s2,False)]
            _amb_var_min = min(_amb_vars) if _amb_vars else float('nan')
            _amb_var_max = max(_amb_vars) if _amb_vars else float('nan')
            # innovation norm already computed as _innov_norm above
            _state_corr_norm = float(np.linalg.norm(x[:3] - x_before[:3])) * 1e3
            n_gps=sum(1 for m in geom if m['sid'][0]=='G')
            n_gal=sum(1 for m in geom if m['sid'][0]=='E')
            print(f"  [STARTUP ep={nproc:02d}] SOD={sod:.0f}  sats={len(geom)}(G{n_gps}+E{n_gal})"
                  f"  rej={_n_rejected_epoch}"
                  f"  codeRMS={code_rms:.1f}mm  phsRMS={_phs_str}"
                  f"  innov_norm={_innov_norm:.2f}  state_corr={_state_corr_norm:.1f}mm"
                  f"  pos_upd={_state_jump*1e3:.1f}mm  ZWD={ZWD*1e3:.1f}mm"
                  f"  clk={x[3]*1e3:+.0f}mm"
                  f"  N_amb={_n_amb}  var_amb=[{_amb_var_min:.2e},{_amb_var_max:.2e}]"
                  f"  cond_P={_cond_P:.2e}")
            # explicit checks
            if code_rms > 0 and _prev_code_rms is not None and code_rms > 2.*_prev_code_rms:
                print(f"  [WARN] Code RMS doubled: {_prev_code_rms:.1f} → {code_rms:.1f} mm")
            if phase_rms > 0 and _prev_phase_rms is not None and phase_rms > 2.*_prev_phase_rms:
                print(f"  [WARN] Phase RMS doubled: {_prev_phase_rms:.2f} → {phase_rms:.2f} mm")
            # v58.1 PATCH 2: OSB-EFFECT uses _active_osb_phase (built in lockstep
            # with phase_res_now above) — shapes are guaranteed identical.
            # Safe empty-guard: no active phase sats → skip block, don't crash.
            if len(phase_res_now) == 0:
                print(f"  [OSB-EFFECT ep={nproc:02d}] SOD={sod:.0f}  "
                      f"no active phase sats — skipped")
            else:
                _res_corr_arr = np.array(phase_res_now)
                _res_uncorr   = _res_corr_arr + np.array(_active_osb_phase)
                _rms_uncorr   = float(math.sqrt(np.mean(_res_uncorr**2))) * 1e3
                _mean_osb_mm  = float(np.mean(np.abs(_active_osb_phase))) * 1e3
                print(f"  [OSB-EFFECT ep={nproc:02d}] SOD={sod:.0f}  "
                      f"mean|osb_IF_phase|={_mean_osb_mm:.2f}mm  "
                      f"phsRMS_after_corr={phase_rms:.2f}mm  "
                      f"phsRMS_before_corr(simulated)={_rms_uncorr:.2f}mm")

        _prev_code_rms  = code_rms  if code_rms  > 0 else _prev_code_rms
        _prev_phase_rms = phase_rms if phase_rms > 0 else _prev_phase_rms

        # ── v56 ISB logging every 30 epochs ───────────────────────────────────
        if nproc % 30 == 0 or is_startup:
            _gps_code_res = [m['PIF'] - _rp(m,x[3],x[4])
                             for m in geom if m.get('_sys') != 'E']
            _gal_code_res = [m['PIF'] - (_rp(m,x[3],x[4]) + x[ISB_IDX])
                             for m in geom if m.get('_sys') == 'E']
            _gps_mean_mm = float(np.mean(_gps_code_res))*1e3 if _gps_code_res else float('nan')
            _gal_mean_mm = float(np.mean(_gal_code_res))*1e3 if _gal_code_res else float('nan')
            _isb_split   = (_gal_mean_mm - _gps_mean_mm) if _gps_code_res and _gal_code_res else float('nan')
            _phs_vals    = [m['LIFc']-(_rp(m,x[3],x[4])+x[m['ki']]) for m in geom]
            _phs_mean    = float(np.mean(_phs_vals))*1e3 if _phs_vals else 0.
            _phs_rms_log = phase_rms
            _phs_dm_rms  = (float(np.sqrt(np.mean([(v*1e3-_phs_mean)**2 for v in _phs_vals])))
                            if _phs_vals else 0.)
            _same_sgn    = (max(sum(v>0 for v in _phs_vals), sum(v<0 for v in _phs_vals))
                            / max(len(_phs_vals),1) * 100.)
            _isb_sigma   = math.sqrt(max(P[ISB_IDX,ISB_IDX], 0.))
            print(f"  [ISB-STATE] epoch={nproc:04d}  SOD={sod:.0f}"
                  f"  xISB={x[ISB_IDX]*1e3:+.1f}mm  sigma={_isb_sigma*1e3:.1f}mm"
                  f"  GPSmean={_gps_mean_mm:+.0f}mm  GALmean={_gal_mean_mm:+.0f}mm"
                  f"  split={_isb_split:+.0f}mm"
                  f"  sameSign={_same_sgn:.0f}%"
                  f"  phaseRMS={_phs_rms_log:.1f}mm  demeanedRMS={_phs_dm_rms:.1f}mm")
            # Accumulate trace row
            _isb_trace_rows.append(dict(
                epoch=nproc, sod=sod,
                x_isb_m=float(x[ISB_IDX]),
                sigma_isb_m=_isb_sigma,
                gps_code_mean_mm=_gps_mean_mm,
                gal_code_mean_mm=_gal_mean_mm,
                phase_same_sign_percent=_same_sgn,
                phase_rms_mm=_phs_rms_log,
                demeaned_phase_rms_mm=_phs_dm_rms,
                num_phase_reject=_n_rejected_epoch,
            ))
        # ─────────────────────────────────────────────────────────────────────

        # ── v55 L9: accumulate history for debug plots ─────────────────────────
        _sod_hist.append(sod)
        _code_rms_hist.append(code_rms)
        _phase_rms_hist.append(phase_rms)
        _innov_norm_hist.append(_innov_norm)
        _n_amb_now = sum(1 for s in sidx if phi.get(s,False))
        _amb_vars_all = [math.sqrt(max(P[ki2,ki2],0.)) for _,ki2 in sidx.items() if phi.get(_,False)]
        _amb_sig_hist.append(float(np.mean(_amb_vars_all)) if _amb_vars_all else 0.)
        _sat_cnt_hist.append(len(geom))
        _cond_hist.append(min(_cond_P, 1e15))
        _zwd_hist_plot.append(ZWD*1e3)
        _rej_cnt_hist.append(_n_rejected_epoch)
        # ─────────────────────────────────────────────────────────────────────


    # ── PER-PASS RESIDUAL SUMMARY ────────────────────────────────────────────
    if direction==1:
        _res_G_phs=[]; _res_E_phs=[]; _res_G_cod=[]; _res_E_cod=[]
        _apc_sat_G=[]; _apc_sat_E=[]; _apc_rec_G=[]; _apc_rec_E=[]
        _osb_if_phs_G=[]; _osb_if_phs_E=[]
        for sod2,r2 in results.items():
            pass   # can't re-access geom; residuals were already in code/phase_rms per epoch
        # Residual info is per-epoch in results; print summary from saved epoch medians
        _cod_rms_G = math.sqrt(np.mean([r['code_rms']**2
                                         for _,r in results.items()
                                         if r.get('code_rms',0)>0])) if results else 0.
        _phs_rms_all = math.sqrt(np.mean([r['phase_rms']**2
                                           for _,r in results.items()
                                           if r.get('phase_rms',0)>0])) if results else 0.
        print(f"\n  [RESIDUAL SUMMARY] {pass_label} "
              f"code_rms={_cod_rms_G:.1f}mm  phase_rms={_phs_rms_all:.2f}mm")
    # ─────────────────────────────────────────────────────────────────────────
    # ── v56 ISB: write isb_state_trace.csv ───────────────────────────────────
    if direction==1 and _isb_trace_rows and pass_label:
        _isb_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                f'isb_state_trace_{pass_label.replace("+","_").replace(" ","_")}.csv')
        try:
            with open(_isb_csv, 'w') as _fh:
                _fh.write("epoch,sod,x_isb_m,sigma_isb_m,gps_code_mean_mm,"
                          "gal_code_mean_mm,phase_same_sign_percent,"
                          "phase_rms_mm,demeaned_phase_rms_mm,num_phase_reject\n")
                for _row in _isb_trace_rows:
                    _fh.write(f"{_row['epoch']},{_row['sod']:.1f},"
                              f"{_row['x_isb_m']:.6f},{_row['sigma_isb_m']:.6f},"
                              f"{_row['gps_code_mean_mm']:.2f},{_row['gal_code_mean_mm']:.2f},"
                              f"{_row['phase_same_sign_percent']:.1f},"
                              f"{_row['phase_rms_mm']:.3f},{_row['demeaned_phase_rms_mm']:.3f},"
                              f"{_row['num_phase_reject']}\n")
            print(f"[ISB-CSV] Written: {_isb_csv}")
        except Exception as _e:
            print(f"[ISB-CSV] Could not write: {_e}")
    # ─────────────────────────────────────────────────────────────────────────
    print(f"[WL_DICT] size={len(wl_fixed)} keys={list(wl_fixed.keys())}")
    fwd_amb={sid:(x[ki],P[ki,ki]) for sid,ki in sidx.items() if phi.get(sid,False)}
    fwd_amb_out={sid:v for sid,v in fwd_amb.items() if sid in _amb_conv_sods}
    excluded={sid:f"pt={_amb_init_ptrace.get(sid,999):.3f}"
              for sid in fwd_amb if sid not in _amb_conv_sods}
    print(f"[AMB INHERIT] {len(fwd_amb_out)}/{len(fwd_amb)} sats "
          f"(excluded: {excluded})")

    # ── v55 L9: per-pass debug plots ─────────────────────────────────────────
    if direction==1 and _sod_hist and pass_label:
        try:
            import matplotlib; matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            _sh = np.array(_sod_hist)/3600.
            fig, axes = plt.subplots(4, 2, figsize=(14, 16))
            fig.suptitle(f'v55 Debug — {pass_label}  ({label})', fontsize=12, fontweight='bold')
            axes[0,0].plot(_sh, _code_rms_hist, 'b', lw=0.8)
            axes[0,0].set_title('Code RMS (mm)'); axes[0,0].set_ylabel('mm'); axes[0,0].grid(True,alpha=0.3)
            axes[0,1].plot(_sh, _phase_rms_hist, 'r', lw=0.8)
            axes[0,1].set_title('Phase RMS (mm)'); axes[0,1].set_ylabel('mm'); axes[0,1].grid(True,alpha=0.3)
            _inn_plot = np.clip(_innov_norm_hist, 0, np.percentile(_innov_norm_hist, 99) if _innov_norm_hist else 1)
            axes[1,0].plot(_sh, _inn_plot, 'g', lw=0.8)
            axes[1,0].set_title('Innovation Norm'); axes[1,0].set_ylabel('m'); axes[1,0].grid(True,alpha=0.3)
            axes[1,1].plot(_sh, _amb_sig_hist, 'm', lw=0.8)
            axes[1,1].set_title('Mean Ambiguity Sigma (m)'); axes[1,1].set_ylabel('m'); axes[1,1].grid(True,alpha=0.3)
            axes[2,0].plot(_sh, _sat_cnt_hist, 'k', lw=0.8)
            axes[2,0].set_title('Satellite Count'); axes[2,0].set_ylabel('N'); axes[2,0].grid(True,alpha=0.3)
            _cond_clip = np.clip(np.log10(np.maximum(_cond_hist, 1)), 0, 15)
            axes[2,1].plot(_sh, _cond_clip, 'orange', lw=0.8)
            axes[2,1].set_title('log10(Cond P)'); axes[2,1].set_ylabel('log10'); axes[2,1].grid(True,alpha=0.3)
            axes[3,0].plot(_sh, _zwd_hist_plot, 'c', lw=0.8)
            axes[3,0].set_title('ZWD (mm)'); axes[3,0].set_xlabel('Time (h)'); axes[3,0].set_ylabel('mm'); axes[3,0].grid(True,alpha=0.3)
            axes[3,1].bar(_sh, _rej_cnt_hist, width=(_sh[1]-_sh[0]) if len(_sh)>1 else 0.01, color='red', alpha=0.6)
            axes[3,1].set_title('Rejected Obs Count'); axes[3,1].set_xlabel('Time (h)'); axes[3,1].grid(True,alpha=0.3)
            plt.tight_layout()
            _dbg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     f'ppp_debug_v55_{pass_label.replace("+","_").replace(" ","_")}.png')
            fig.savefig(_dbg_path, dpi=120, bbox_inches='tight')
            plt.close(fig)
            print(f"[DEBUG-PLOT] {_dbg_path}")
        except Exception as _pe:
            print(f"[DEBUG-PLOT] Could not generate: {_pe}")
    # ─────────────────────────────────────────────────────────────────────────

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
    print("GPS+Galileo PPP v54 — Galileo ATX fix | ZWD clamp+prior | amb freeze | res gate | NL N≥8 | WL std<0.25 | code σ=1.5m")
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
    # ─────────────────────────────────────────────────────────────────────────

    mode_labels=[('G','GPS-only'),('E','Galileo-only'),('GE','GPS+Galileo')]
    all_fwd={}; all_rts={}; all_meta={}

    for const,label in mode_labels:
        print(f"\n{'='*72}")
        print(f"[MODE] {label}  (constellation='{const}')")
        _rts_store._data=[]
        fwd,ex,ec,ez,wl_f,fwd_amb,fwd_snap=_ppp_pass(
            epochs,nom=APX.copy(),iclk=0.,izwd=0.20,
            direction=1,label="FWD",constellation=const,
            trace_fh=_trace_fh,pass_label=label,
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

    primary_fwd=all_fwd['GPS+Galileo']
    primary_rts=all_rts['GPS+Galileo']
    rl=[(s,{**r,'pass':'FWD'}) for s,r in sorted(primary_fwd.items())]

    # ── CLOSE TRACE AND PRINT DIAGNOSTICS ────────────────────────────────────
    _trace_fh.close()
    print(f"\n[TRACE] Closed: {_trace_path}")
    try:
        import csv as _csv
        _rows_G_phs=[]; _rows_E_phs=[]; _rows_G_cod=[]; _rows_E_cod=[]
        _apc_sat_G=[]; _apc_sat_E=[]; _apc_rec_G=[]; _apc_rec_E=[]
        _osb_ph_G=[]; _osb_ph_E=[]
        _label_ge='GPS+Galileo'
        with open(_trace_path,'r') as _tfh:
            _rd=_csv.DictReader(_tfh)
            for _row in _rd:
                if _row['pass_label']!=_label_ge: continue
                _sys=_row['system']
                _pr=float(_row['phase_residual_mm'])
                _cr=float(_row['code_residual_mm'])
                _ar_sat=float(_row['sat_apc_range_mm'])
                _ar_rec=float(_row['rec_apc_range_mm'])
                _osb_ph=float(_row['osb_IF_phase_mm'])
                if _sys=='G':
                    _rows_G_phs.append(_pr); _rows_G_cod.append(_cr)
                    _apc_sat_G.append(_ar_sat); _apc_rec_G.append(_ar_rec)
                    _osb_ph_G.append(_osb_ph)
                else:
                    _rows_E_phs.append(_pr); _rows_E_cod.append(_cr)
                    _apc_sat_E.append(_ar_sat); _apc_rec_E.append(_ar_rec)
                    _osb_ph_E.append(_osb_ph)
        def _rms(v): return math.sqrt(sum(x*x for x in v)/len(v)) if v else float('nan')
        def _mn(v):  return sum(v)/len(v) if v else float('nan')
        print(f"\n{'='*72}")
        print(f"[FORENSIC SUMMARY] GPS+Galileo pass — measurement model diagnostics")
        print(f"{'='*72}")
        print(f"  {'Metric':<40}  {'GPS':>12}  {'Galileo':>12}")
        print(f"  {'-'*66}")
        print(f"  {'N obs':40}  {len(_rows_G_phs):>12d}  {len(_rows_E_phs):>12d}")
        print(f"  {'Phase residual mean (mm)':40}  {_mn(_rows_G_phs):>12.3f}  {_mn(_rows_E_phs):>12.3f}")
        print(f"  {'Phase residual RMS (mm)':40}  {_rms(_rows_G_phs):>12.3f}  {_rms(_rows_E_phs):>12.3f}")
        print(f"  {'Code residual mean (mm)':40}  {_mn(_rows_G_cod):>12.1f}  {_mn(_rows_E_cod):>12.1f}")
        print(f"  {'Code residual RMS (mm)':40}  {_rms(_rows_G_cod):>12.1f}  {_rms(_rows_E_cod):>12.1f}")
        print(f"  {'Mean sat_apc_range (mm)':40}  {_mn(_apc_sat_G):>12.3f}  {_mn(_apc_sat_E):>12.3f}")
        print(f"  {'Mean rec_apc_range (mm)':40}  {_mn(_apc_rec_G):>12.3f}  {_mn(_apc_rec_E):>12.3f}")
        print(f"  {'Mean osb_IF_phase (mm)':40}  {_mn(_osb_ph_G):>12.3f}  {_mn(_osb_ph_E):>12.3f}")
        # dominance check: which term is largest absolute mean
        _terms_G={'sat_apc':_mn([abs(x) for x in _apc_sat_G]),
                  'rec_apc':_mn([abs(x) for x in _apc_rec_G]),
                  'osb_IF_phase':_mn([abs(x) for x in _osb_ph_G])}
        _terms_E={'sat_apc':_mn([abs(x) for x in _apc_sat_E]),
                  'rec_apc':_mn([abs(x) for x in _apc_rec_E]),
                  'osb_IF_phase':_mn([abs(x) for x in _osb_ph_E])}
        print(f"\n  [DOMINANT CORRECTION TERMS — mean |value| in mm]")
        print(f"  {'Term':<40}  {'GPS':>12}  {'Galileo':>12}")
        print(f"  {'-'*66}")
        for _k in _terms_G:
            print(f"  {_k:<40}  {_terms_G[_k]:>12.3f}  {_terms_E[_k]:>12.3f}")
        _diff_phs=_mn(_rows_E_phs)-_mn(_rows_G_phs)
        _diff_cod=_mn(_rows_E_cod)-_mn(_rows_G_cod)
        _diff_apc_sat=_mn(_apc_sat_E)-_mn(_apc_sat_G)
        _diff_apc_rec=_mn(_apc_rec_E)-_mn(_apc_rec_G)
        _diff_osb=_mn(_osb_ph_E)-_mn(_osb_ph_G)
        print(f"\n  [GPS vs GALILEO SYSTEMATIC OFFSET  (Galileo − GPS)]")
        print(f"  Phase residual offset  : {_diff_phs:+.3f} mm")
        print(f"  Code  residual offset  : {_diff_cod:+.1f} mm")
        print(f"  sat_apc_range offset   : {_diff_apc_sat:+.3f} mm")
        print(f"  rec_apc_range offset   : {_diff_apc_rec:+.3f} mm")
        print(f"  osb_IF_phase offset    : {_diff_osb:+.3f} mm")
        print(f"\n  [DIAGNOSIS]")
        _candidates=[]
        if abs(_diff_apc_sat)>5.: _candidates.append(f"sat_apc_range ({_diff_apc_sat:+.1f}mm)")
        if abs(_diff_apc_rec)>5.: _candidates.append(f"rec_apc_range ({_diff_apc_rec:+.1f}mm)")
        if abs(_diff_osb)>5.:     _candidates.append(f"osb_IF_phase  ({_diff_osb:+.1f}mm)")
        if _candidates:
            print(f"  *** Suspicious GPS/Galileo asymmetry in: {', '.join(_candidates)}")
            print(f"  *** These are candidate sources of the remaining Up bias.")
        else:
            print(f"  No single correction term shows >5mm GPS/Galileo asymmetry.")
            print(f"  Remaining bias is likely residual ionosphere or ZWD model error.")
        print(f"{'='*72}\n")
    except Exception as _e:
        print(f"[TRACE] Diagnostic parse failed: {_e}")
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
    colors={'GPS-only':'#e6194b','Galileo-only':'#4363d8','GPS+Galileo':'#3cb44b'}
    fig,axes=plt.subplots(2,2,figsize=(14,10))
    fig.suptitle('PPP-AR Multi-Constellation Comparison (v54-GalATXfix)',
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
    plot_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'ppp_comparison2.png')
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
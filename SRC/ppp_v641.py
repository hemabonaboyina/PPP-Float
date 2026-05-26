"""
ppp.py  v642 — Step-4B: Stochastic-Architecture Hygiene (Fixes 1–3)
=====================================================================

STEP-4B changes (diagnostic repair + covariance hygiene only):
  Builds on Step-4 (operational lifecycle activation, ppp_v641/ambiguity_manager3).
  Three targeted, non-EKF changes:

  FIX-1 — Participation bookkeeping repair
    Line formerly read:
        _ap_in_upd = int(_aps in _newborn_pending is False and ...)
    Python chained-comparison semantics made the second operand always False.
    Replaced with explicit "not in" guard:
        _ap_in_upd = int((_aps not in _newborn_pending) and ...)
    Audit: searched full file for `in X is False`, `in X == False` patterns.
    Only one instance found; fixed.

  FIX-2 — Restore covariance cap
    DORMANT→RESTORE path previously allowed sigma > birth sigma (20 000 mm)
    when the saved P[ki,ki] had been inflated by process noise during dormancy.
    Observed: sigma_restored=28284mm > birth sigma 20000mm.
    Fix: after computing P_raw = min(saved*2, 50²), apply:
        P[ki,ki] = min(P_raw, BIRTH_SIGMA²)   where BIRTH_SIGMA = 20.0 m
    Adds [RESTORE-CAP] diagnostic line with sigma_before/after/cap_applied.
    Does NOT modify newborn logic, process model, or EKF equations.

  FIX-3 — Newborn quarantine in ambiguity statistics
    [EPOCH] now reports two parallel sigma fields:
        amb_sigma_all:    all active ambiguities (unchanged meaning)
        amb_sigma_stable: sigma < 19000mm AND lifecycle NOT in {NEW,RESET,REBORN}
    Newborns (σ = 20 000 mm) no longer inflate the mean/max shown at each epoch.
    Filter behaviour is UNCHANGED; this is diagnostic reporting only.

All EKF equations, gates, thresholds, process noise, covariance math, and
output numbers are IDENTICAL to ppp_v641.  This is a pure hygiene patch.

── Original header (ppp_v641) follows ──────────────────────────────────────
ppp.py  vRESET-minimal — Phase-Reset: Minimal Float PPP-EKF
===========================================================

PHASE-RESET — all lifecycle/state-machine policy removed.
Returned to a minimal float PPP-EKF with:
  - continuous EKF execution every epoch
  - ambiguities as normal stochastic states
  - no lifecycle bureaucracy
  - no trust/promotion/quarantine systems
  - no EKF permission gating
  - no ambiguity purge framework

Convergence is decided probabilistically through covariance contraction,
NOT through manually designed state machines.

────────────────────────────────────────────────────────────────────────
DELETED SYSTEMS
────────────────────────────────────────────────────────────────────────
  1. Ambiguity lifecycle states (WARMUP / ACTIVE / TRUSTED / DEGRADED /
     newborn_pending)
  2. Promotion systems (promotion counters, trusted thresholds, confidence
     transitions, promotion epochs)
  3. Quarantine systems (quarantine rows/epochs, trusted_warmup_rows,
     strong_phase_rows)
  4. EKF permission logic (update_allowed, filter_called gates,
     minimum trusted rows, EKF suppression, ambiguity maturity gating)
  5. Purge systems (COMMONMODE-PURGE, CMODE eviction, rebirth tracking,
     rebirth counters, ambiguity kill loops)
  6. Ambiguity protection bureaucracy (newborn preservation, freeze, hold,
     aging logic)
  7. All associated diagnostics ([ACTIVE-COUNT], [REBIRTH-DETECTED],
     [EKF-EXECUTION], [P14-CMODE-EVICTION-SUPPRESSED], promotion/lifecycle
     summaries)

────────────────────────────────────────────────────────────────────────
RETAINED PHYSICS
────────────────────────────────────────────────────────────────────────
  1.  Observation model (IF combinations, GPS/Galileo)
  2.  SP3/CLK/BIA/ATX/OBX handling
  3.  Troposphere estimation (GMF, ZHD+ZWD)
  4.  ISB estimation (Galileo-GPS code + phase)
  5.  Cycle slip detection (GF + MW)
  6.  Innovation residual editing (observation-domain rejection only)
  7.  Covariance propagation (process noise)
  8.  Joseph covariance symmetrization
  9.  Ambiguity reset after slip
  10. Basic NaN/PD guards
  11. RTS smoother
  12. LAMBDA implementation (comment-disabled — AR disabled for float validation)
  13. WL accumulation infrastructure

────────────────────────────────────────────────────────────────────────
FLOAT PPP AMBIGUITY RULES
────────────────────────────────────────────────────────────────────────
  At birth:
    x[ki] = LIF - rp_postfit  (post-EKF state)
    P[ki,ki] = (20 m)^2

  After birth:
    ALWAYS participate in EKF updates.
    NO maturity / trust / sigma-threshold permission.

────────────────────────────────────────────────────────────────────────
ALLOWED REJECTION (observation-domain only)
────────────────────────────────────────────────────────────────────────
  Code:  |residual| > 10 m startup / 5 m converged
  Phase: |residual| > 0.10 m startup / 0.05 m converged
  Also:  NaN / inf / singular variance / invalid geometry

────────────────────────────────────────────────────────────────────────
AR STATUS
────────────────────────────────────────────────────────────────────────
  NL fixing disabled. WL infrastructure retained.
  Validate float PPP convergence first.

────────────────────────────────────────────────────────────────────────
DIAGNOSTICS (per epoch)
────────────────────────────────────────────────────────────────────────
  [EPOCH]     sat count, code RMS, phase RMS, innov norm, rej count,
              ZWD, ISB, pos update norm, amb sigma mean/min/max, cond(P)
  [AMB-BORN]  ambiguity birth events
  [AMB-RESET] ambiguity reset (cycle slip)
  [AMB-RESTORE] gap recovery
  [PASS-SUMMARY] total EKF updates, epochs, resets
  [AMB-SIGMA-CONTRACTION] per-sat birth→final sigma
"""
import os, sys, math, time as _time
from collections import defaultdict
import numpy as np
from ambiguity_manager3 import AmbiguityManager, AmbiguityLifecycleState

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
# FIX-3 (v64): Phase elevation mask — applied to phase rows AND slip detector
PHASE_EL_MASK_RAD = math.radians(15.0)
# PATCH: _AMB_MIN_EPOCHS removed — immediate birth, no minimum streak

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

def _fmtf(x, scale=1.0, prec=4):
    """Safe float formatter — always returns a string safe to embed in CSV.
    Never raises 'Unknown format code f for object of type str'.
    """
    if x is None:
        return "nan"
    try:
        if not math.isfinite(x):
            return "nan"
        return f"{x * scale:.{prec}f}"
    except Exception:
        return "nan"

def _fmtf_sci(x, prec=8):
    """FIX-2 (v64): NIS formatter in scientific notation to preserve tiny values.
    Fixed-point :.6f collapses values 1e-9…1e-6 to 0.000000 — use :.8e instead.
    """
    if x is None:
        return "nan"
    try:
        if not math.isfinite(x):
            return "nan"
        return f"{x:.{prec}e}"
    except Exception:
        return "nan"


def _reject_obs(ri, H, z, Rd, reason, sid, sod, startup=False):
    """Zero-out a measurement row and log the rejection (silent when reason is None)."""
    H[ri, :] = 0.
    z[ri]    = 0.
    Rd[ri]   = 1e12
    if reason is not None and startup:
        print(f"    [REJECT] {sid} row={ri} SOD={sod:.0f}: {reason}")


# ── PHASE-6 FORENSIC HELPERS ──────────────────────────────────────────────────

def _fmt_amb_death_trace(sat, event, reason, caller, health_before, health_after,
                          sigma_before_m, sigma_after_m, ki, gap_sec,
                          bad_streak, good_streak, sod):
    """Emit the canonical [AMB-DEATH-TRACE] block."""
    print(
        f"[AMB-DEATH-TRACE]\n"
        f"  sat={sat}  SOD={sod:.0f}\n"
        f"  event={event}\n"
        f"  reason={reason}\n"
        f"  caller={caller}\n"
        f"  health_before={health_before}  health_after={health_after}\n"
        f"  sigma_before={sigma_before_m*1e3:.1f}mm  sigma_after={sigma_after_m*1e3:.1f}mm\n"
        f"  ki={ki}  gap_sec={gap_sec:.0f}\n"
        f"  bad_streak={bad_streak}  good_streak={good_streak}"
    )

def _fmt_health_transition(sat, from_h, to_h, reason, residual_mm, sigma_mm, sod):
    """Emit the canonical [AMB-HEALTH-TRANSITION] block."""
    print(
        f"[AMB-HEALTH-TRANSITION]\n"
        f"  sat={sat}  SOD={sod:.0f}\n"
        f"  from={from_h}  to={to_h}\n"
        f"  reason={reason}\n"
        f"  residual={residual_mm:+.1f}mm  sigma={sigma_mm:.1f}mm"
    )

def _fmt_geom_drop(sat, sod, health, saved):
    print(f"[AMB-GEOM-DROP]  sat={sat}  SOD={sod:.0f}  "
          f"health={health}  saved_state={'YES' if saved else 'NO'}")

def _fmt_geom_return(sat, sod, gap_sec, restored, reborn, health_before):
    print(f"[AMB-GEOM-RETURN]  sat={sat}  SOD={sod:.0f}  "
          f"gap_sec={gap_sec:.0f}  health_before={health_before}  "
          f"restored_saved_state={'YES' if restored else 'NO'}  "
          f"reborn={'YES' if reborn else 'NO'}")




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
    # FIX-1 (v64): GPS P2 pseudorange observable is ALWAYS C2W regardless of
    # which phase carrier is civil (L2L) vs semi-codeless (L2W).
    # The old logic incorrectly preferred the C2L OSB when use_civil=True,
    # injecting a satellite-dependent IF code bias → Up bias + phase rejection.
    b2 = ob.get('C2W', ob.get('C2L', 0.0))
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

# ==============================================================================
#  PPP Kalman filter pass — MINIMAL FLOAT PPP-EKF (Phase-Reset)
# ==============================================================================
#
#  ARCHITECTURAL REVERT — all lifecycle/state-machine policy removed.
#
#  Retained physics:
#    observation model, SP3/CLK/BIA/ATX, IF combinations, troposphere,
#    ISB, cycle slip detection, innovation residual editing,
#    covariance propagation, Joseph symmetrization, ambiguity reset after slip,
#    NaN/PD guards, RTS smoother, LAMBDA (commented — AR disabled for now),
#    WL accumulation infrastructure.
#
#  Removed completely:
#    WARMUP / ACTIVE / DEGRADED / NEWBORN states
#    promotion counters / trusted thresholds / confidence transitions
#    quarantine system (amb_quarantine, quarantine_epochs)
#    EKF permission gating (update_allowed, skip_ekf, case_A/B/C/D)
#    COMMONMODE-PURGE / CMODE eviction / rebirth tracking
#    newborn preservation / freeze / hold / aging logic
#    bootstrap mode / deadlock detector / bootstrap safety limits
#    ambiguity covariance inflation policies (DEGRADED_P_INFLATE, etc.)
#    all associated lifecycle diagnostics
#
#  Ambiguity lifecycle (only rule):
#    Birth  : post-fit after EKF; x[ki] = LIF - rp_postfit; P[ki,ki] = (20 m)^2
#    Active : ALWAYS participate in EKF immediately after birth
#    Slip   : reset x[ki]=0, P[ki,ki]=(20m)^2, clear active flag, restart streak
#    Gap<300s: restore saved state, immediately active again
#
#  Observation-domain rejection only:
#    code  : |res| > 10 m (startup) / 5 m (converged)
#    phase : |res| > 0.10 m (startup) / 0.05 m (converged)
#    + NaN / inf / singular variance
#
#  EKF can NEVER be blocked by ambiguity state logic.
# ==============================================================================

def _ppp_pass(epochs, sp3t, sp, sc, clkd, osb, ah, nom, iclk, izwd,
              lat0, doy, zhd, tref, satx, att, recx,
              elm=math.radians(10.), SC=1.50, SP=0.003,
              direction=1, label="FWD", wl_init=None, amb_init=None,
              constellation='GE', blq=None, sta='IISC',
              trace_fh=None, pass_label='',
              state_trace_fh=None,
              birth_trace_fh=None,
              constat_fh=None,
              innov_audit_fh=None,
              amb_part_fh=None,
              reset_audit_fh=None,
              amgr_snap_fh=None,
              lifecycle_fh=None,
              innov2_fh=None,            # FIX-5 (v642): innovation_audit.csv handle
              # ── Phase-C/D stochastic architecture parameters (v642) ──────────
              Q_CLK_SCALE=1e4,           # FIX-1: clock process noise scale (m²/s)
              PHASE_GATE_MODE="absolute",# FIX-2: "absolute" | "normalized"
              PHASE_GATE_SIGMA_THRESH=4.0, # FIX-2: sigma threshold for normalized mode
              ):
    """
    Minimal float PPP-EKF.

    constellation : 'G' | 'E' | 'GE'
    blq           : dict from parse_blq (ocean tide loading)
    sta           : 4-char station code for BLQ lookup
    """
    REF = np.array([1337935.5599, 6070317.2377, 1427877.5071])

    # ── NL fixing parameters (kept for infrastructure; fixing disabled) ─────────
    NL_RATIO_THRESH   = 4.5
    NL_VAR_THRESH     = (10.0)**2
    NL_RES_THRESH     = 0.10
    NL_EXCL_THRESH    = 0.25
    NL_R_TIGHT        = (0.005)**2
    NL_INNOV_GATE     = 0.080
    NL_RELEASE_THRESH = 0.060
    NL_PHASE_THRESH   = 0.010
    NL_MIN_SATS       = 3
    NL_MIN_OBS        = 8
    PHASE_RES_GATE    = 0.030  # downweight gate for mature phase rows (soft)
    ZWD_PRIOR         = 0.12
    ZWD_PRIOR_SIGMA   = 0.06
    ZWD_CLAMP         = 0.015
    _zwd_prev         = None
    _nl_diag_done     = False

    # ── State layout: 0-2=dXYZ  3=clk  4=ZWD  5=ISB(Gal-GPS code) ───────────
    ISB_IDX = 5
    x = np.zeros(6)
    x[3] = iclk; x[4] = izwd; x[ISB_IDX] = 0.0
    P = np.zeros((6, 6))
    P[0,0]=P[1,1]=P[2,2] = 100.**2
    P[3,3] = 3000.**2
    P[4,4] = 0.5**2
    P[ISB_IDX, ISB_IDX] = 25.0   # ±5 m initial sigma (ISB ≈ +4 m)

    # ── Ambiguity lifecycle manager (Step-1 refactor) ────────────────────────
    # Single owner of all per-satellite bookkeeping that previously lived in
    # amb_active / _amb_birth_sigma / _amb_reset_count / _audit_* dicts.
    # snapshot_fh is forwarded from the caller; write_snapshot() is a no-op
    # when the handle is None (e.g. GPS-only and Galileo-only passes).
    amgr = AmbiguityManager(snapshot_fh=amgr_snap_fh, lifecycle_fh=lifecycle_fh)

    # ASSERT-1: import succeeded (class is callable)
    assert callable(AmbiguityManager), \
        "ASSERT-1 failed: AmbiguityManager not imported / not callable"
    # ASSERT-2: snapshot_header exists and is a str
    assert isinstance(AmbiguityManager.snapshot_header(), str), \
        "ASSERT-2 failed: AmbiguityManager.snapshot_header() did not return str"
    # ASSERT-3: snapshot handle forwarded correctly
    assert amgr._snapshot_fh is amgr_snap_fh, \
        "ASSERT-3 failed: amgr._snapshot_fh does not match amgr_snap_fh"
    # ASSERT-4: key methods exist on the instance
    for _m in ('_ensure', 'register_birth', 'register_reset',
               'register_accept', 'register_reject',
               'is_active', 'set_active', 'write_snapshot',
               'register_inherited', 'update_Kg_norm',
               'get_recent_ph_res', 'get_recent_rej', 'get_birth_epoch',
               'get_accepted_count', 'get_rejected_count',
               'get_last_accept_epoch', 'get_H_norm', 'get_birth_sigma_mm',
               'items', 'cumulative_resets',
               # STEP-2 APIs
               'register_new_slot', 'activate', 'deactivate',
               'mark_observable', 'mark_unobservable',
               'mark_in_filter', 'mark_out_of_filter',
               'update_sigma', 'update_state_index',
               'assert_state_indices_match', 'lifecycle_trace_header',
               # STEP-3 APIs
               'transition_state', 'run_consistency_assertions',
               'get_lifecycle_state', 'get_lineage_id',
               'get_generation', 'get_parent_lineage_id',
               # STEP-4 APIs
               'handle_gap', 'tick', 'run_step4_assertions',
               'get_continuous_active_epochs', 'get_continuous_filter_epochs',
               'get_rolling_sigma_mm', 'get_convergence_score'):
        assert hasattr(amgr, _m), \
            f"ASSERT-4 failed: AmbiguityManager missing method/property '{_m}'"

    # ── Ambiguity allocation ─────────────────────────────────────────────────
    sidx      = {}   # sid → state index in x
    namb      = 0
    _amb_saved_state = {}   # sid → {x_ki, P_ki, last_sod, last_epoch} for gap recovery
    # _amb_reset_count now owned by amgr.cumulative_resets
    _ekf_update_count= 0    # cumulative EKF updates executed

    # ── Per-satellite tracking ───────────────────────────────────────────────
    wum               = {}   # phase wind-up accumulated state
    _sat_age          = defaultdict(int)
    wl_fixed          = dict(wl_init) if wl_init else {}
    _amb_init         = dict(amb_init) if amb_init else {}
    _amb_seeded       = set()
    nl_fixed          = {}   # NL fixing disabled; dict kept for output stats
    _nl_bad_nwl       = set()
    prev_mw           = {}
    prev_gf           = {}
    mw_hist           = defaultdict(list)
    _wl_history       = {}
    _wl_history_ptrace= {}
    _sat_last_sod     = {}
    b_rec_frozen      = {}
    b_rec_n           = defaultdict(int)

    # ── AUDIT: per-satellite participation tracking — managed by amgr ─────────
    # (AmbiguityManager owns all per-satellite counters and ring buffers.
    #  _audit_RING kept as a named constant for readability in reset_audit_fh.)

    # ── Pass-level accumulators ──────────────────────────────────────────────
    results   = {}
    psod      = None
    nproc     = 0
    _prev_code_rms  = None
    _prev_phase_rms = None

    # ── Minimum streak before ambiguity birth ────────────────────────────────
    # PATCH: _AMB_MIN_EPOCHS removed — no streak requirement; birth is immediate.

    eplist = epochs if direction == 1 else list(reversed(epochs))

    for epoch in eplist:
        sod  = epoch['t']
        sobs = epoch['sats']
        dt   = abs(sod - psod) if psod is not None else 30.
        if dt <= 0 or dt > 3600:
            dt = 30.
        psod = sod
        tow  = _sod2t(sod, tref)
        tow_total = tow

        # ── Startup flags ────────────────────────────────────────────────────
        is_startup = (nproc < 30)

        # ── State / covariance sanity ────────────────────────────────────────
        if not _is_finite_matrix(x[:5], "state x"):
            print(f"  [WARN-FATAL] Non-finite state at SOD={sod:.0f} — resetting XYZ")
            x[:3] = 0.; P[0,0]=P[1,1]=P[2,2] = 100.**2
        if not _is_finite_matrix(P, "covariance P"):
            print(f"  [WARN-FATAL] Non-finite P at SOD={sod:.0f} — reinflating diagonal")
            bad = ~np.isfinite(P)
            P[bad] = 0.
            np.fill_diagonal(P, np.maximum(np.diag(P), 1.0))
        _joseph_symmetrise(P)

        # ── Process noise ────────────────────────────────────────────────────
        n_st = len(x)
        Q = np.zeros((n_st, n_st))
        Q[0,0]=Q[1,1]=Q[2,2] = 1e-8 * dt
        Q[3,3]                = Q_CLK_SCALE * dt   # FIX-1 (v642): configurable clock process noise
        Q[4,4]                = 2.5e-9 * dt   # ZWD ~3 mm/sqrt(hr)
        Q[ISB_IDX, ISB_IDX]  = 1e-6 * dt     # ISB quasi-static
        # Ambiguity process noise — physically justified random walk only
        q_amb = (1e-8 * dt if nproc < 120 else 1e-10 * dt) if direction == 1 else 1e-10 * dt
        for k in range(namb):
            sid_k = next((s for s, i in sidx.items() if i == 5 + k), None)
            if sid_k and sid_k in nl_fixed:
                Q[5+k, 5+k] = 1e-14 * dt   # freeze when NL-fixed
            else:
                Q[5+k, 5+k] = q_amb
        P += Q

        # ── Compute nominal receiver position ────────────────────────────────
        rxyz = nom + x[:3]
        sun  = _sun(tow)
        geom = []
        _n_rejected_epoch = 0
        _newborn_pending  = {}   # sids queued for post-fit birth this epoch

        # ─────────────────────────────────────────────────────────────────────
        #  Satellite loop: observation model + ambiguity state management
        # ─────────────────────────────────────────────────────────────────────
        for sid, so in sorted(sobs.items()):
            if sid[0] not in ('G', 'E'):
                continue
            if sid[0] not in constellation:
                continue

            if sid[0] == 'E':
                m = _proc_gal(sid, so, tow, rxyz, ah, sp3t, sp, sc, clkd, osb,
                              lat0, doy, zhd, elm, satx, att, recx,
                              blq=blq, sta=sta, tow_total=tow_total)
            else:
                m = _proc(sid, so, tow, rxyz, ah, sp3t, sp, sc, clkd, osb,
                          lat0, doy, zhd, elm, satx, att, recx,
                          blq=blq, sta=sta, tow_total=tow_total)
            if m is None:
                continue

            # OSB presence guard
            ob_chk = osb.get(sid, {})
            if sid[0] == 'E':
                _, _, _osb_ok = _check_osb_gal(ob_chk, sid)
            else:
                _, _, _osb_ok = _check_osb_gps(ob_chk, sid)
            if not _osb_ok:
                _n_rejected_epoch += 1
                continue

            # PATCH: streak tracking removed — no minimum-epoch gate before birth.

            # ── Cycle slip detection ─────────────────────────────────────────
            slip = False
            # FIX-3B (v64): Do not run GF/MW slip detector below 15° phase mask.
            # Low-elevation satellites are multipath-dominated; false slips → reset
            # storms (1663 resets, 79% consecutive in v63-phase14).
            # Update MW/GF references so the detector is ready when the satellite
            # rises above the mask; code processing continues normally.
            if m['el'] < PHASE_EL_MASK_RAD:
                prev_mw[sid]       = m['MW_cyc']
                prev_gf[sid]       = m['GF_m']
                _sat_last_sod[sid] = sod
            else:
                if sid in prev_mw:
                    dGF = m['GF_m'] - prev_gf[sid]
                    dMW = m['MW_cyc'] - prev_mw[sid]
                    if abs(dGF) > 0.05 or abs(dMW) > 1.5:
                        if sid in _amb_seeded:
                            _amb_seeded.discard(sid)
                        else:
                            slip = True
                            wl_fixed.pop(sid, None)
                            mw_hist[sid].clear()
                            _prev_sod = _sat_last_sod.get(sid)
                            if _prev_sod is None or (sod - _prev_sod) > 120.:
                                _wl_history.pop(sid, None)
                                _wl_history_ptrace.pop(sid, None)
                prev_mw[sid]       = m['MW_cyc']
                prev_gf[sid]       = m['GF_m']
                _sat_last_sod[sid] = sod

            # ── MW accumulation ──────────────────────────────────────────────
            if not slip:
                mw_hist[sid].append(m['MW_cyc'])
            else:
                mw_hist[sid].clear()

            # ── WL fixing (infrastructure retained; used by NL which is disabled) ──
            if sid not in wl_fixed:
                n_hist = len(mw_hist[sid])
                if n_hist >= 15:
                    mn = np.mean(mw_hist[sid]); sd = np.std(mw_hist[sid])
                    sys_id = sid[0]; min_n = 30 if sd > 0.30 else 15
                    if n_hist >= min_n:
                        if sys_id not in b_rec_frozen:
                            all_fracs = []
                            for s2, h2 in mw_hist.items():
                                if s2[0] != sys_id or len(h2) < min_n: continue
                                if (np.std(h2) if len(h2) > 1 else 999.) > 0.45: continue
                                all_fracs.append(np.mean(h2) - round(np.mean(h2)))
                            if len(all_fracs) >= 5:
                                bc = float(np.median(all_fracs))
                                agr = sum(1 for f in all_fracs if abs(f - bc) < 0.25)
                                if agr >= max(3, 0.6 * len(all_fracs)):
                                    b_rec_frozen[sys_id] = bc
                                    b_rec_n[sys_id] = len(all_fracs)
                                    print(f"[B_REC FROZEN] {sys_id}: b_rec={bc:+.4f} cyc "
                                          f"median of {len(all_fracs)} sats agree={agr}")
                        if sys_id in b_rec_frozen:
                            b_rec = b_rec_frozen[sys_id]; tag = sys_id + 'F'
                        else:
                            fracs = []
                            for s2, h2 in mw_hist.items():
                                if s2[0] != sys_id or len(h2) < min_n: continue
                                if (np.std(h2) if len(h2) > 1 else 999.) > 0.45: continue
                                fracs.append(np.mean(h2) - round(np.mean(h2)))
                            b_rec = np.mean(fracs) if fracs else 0.0; tag = sys_id + 'E'

                        mn_corr = mn - b_rec; NWL = round(mn_corr)
                        residual = abs(mn_corr - NWL)
                        if sys_id not in b_rec_frozen:
                            pass
                        elif sd < 0.25 and residual < 0.20:
                            NWL_to_use = NWL
                            pt_now = P[0,0] + P[1,1] + P[2,2]
                            if sid in _wl_history:
                                hist_NWL = _wl_history[sid]
                                diff = abs(NWL - hist_NWL)
                                if diff == 0:
                                    NWL_to_use = hist_NWL
                                    if pt_now < _wl_history_ptrace.get(sid, 999.):
                                        _wl_history_ptrace[sid] = pt_now
                                elif diff <= 3:
                                    print(f"[WL PERSIST] {sid} using prev NWL={hist_NWL} "
                                          f"(same-arc noise: new={NWL}, diff={diff}<=3->keep)")
                                    NWL_to_use = hist_NWL
                                else:
                                    print(f"[WL UPDATE] {sid} NWL {hist_NWL}→{NWL} (diff={diff}>3)")
                                    _wl_history[sid] = NWL
                                    _wl_history_ptrace[sid] = pt_now
                                    NWL_to_use = NWL
                            else:
                                _wl_history[sid] = NWL
                                _wl_history_ptrace[sid] = pt_now
                            wl_fixed[sid] = NWL_to_use
                            print(f"[WL FIXED] {sid}  N_WL={NWL_to_use}  "
                                  f"mean={mn_corr:.3f}  std={sd:.3f} "
                                  f"b_rec={b_rec:+.3f}({tag}) cyc")

            # ── Allocate ambiguity state slot if new ─────────────────────────
            if sid not in sidx:
                d = len(x)
                x = np.append(x, 0.)
                Pn = np.zeros((d+1, d+1))
                Pn[:d, :d] = P
                Pn[d, d] = 20.**2   # 20 m birth sigma
                P = Pn
                sidx[sid] = d
                namb += 1
                amgr.register_new_slot(sid, d)          # STEP-2: public API; replaces amgr._ensure(sid, d)
                amgr.update_state_index(sid, d)          # STEP-2: explicit sync + ASSERT-7

            ki = sidx[sid]

            # ── Cycle slip → reset ambiguity state ───────────────────────────
            if slip:
                # ── AUDIT: capture pre-reset diagnostics ─────────────────────
                _pre_sigma_mm = math.sqrt(max(P[ki, ki], 0.)) * 1e3
                _recent_res   = amgr.get_recent_ph_res(sid)
                _recent_rej   = amgr.get_recent_rej(sid)
                _rph_rms  = (math.sqrt(sum(r*r for r in _recent_res)/len(_recent_res))
                             if _recent_res else float('nan'))
                _rej_frac = (sum(_recent_rej)/len(_recent_rej)
                             if _recent_rej else float('nan'))
                _ep_since_birth = nproc - amgr.get_birth_epoch(sid, nproc)
                _was_rej = (sum(_recent_rej[-3:]) >= 2) if len(_recent_rej) >= 3 else False
                if reset_audit_fh is not None:
                    _dGF_log = m['GF_m'] - prev_gf.get(sid, m['GF_m'])
                    _dMW_log = m['MW_cyc'] - prev_mw.get(sid, m['MW_cyc'])
                    reset_audit_fh.write(
                        f"{nproc},{sod:.1f},{sid},{sid[0]},"
                        f"{_fmtf(m['GF_m'],1e3,4)},{_fmtf(m['MW_cyc'],prec=6)},"
                        f"{_fmtf(_dGF_log,1e3,4)},{_fmtf(_dMW_log,prec=4)},"
                        f"{math.degrees(m['el']):.3f},"
                        f"{_fmtf(_pre_sigma_mm,prec=4)},"
                        f"{_fmtf(_rph_rms,prec=4)},"
                        f"{_fmtf(_rej_frac,prec=4)},"
                        f"{_ep_since_birth},"
                        f"{1 if _was_rej else 0},"
                        f"{amgr.cumulative_resets+1}\n"
                    )
                # ─────────────────────────────────────────────────────────────
                x[ki] = 0.; P[ki, ki] = 20.**2
                amgr.register_reset(sid)          # active=False, ring-bufs cleared, counter++
                _amb_saved_state.pop(sid, None)
                nl_fixed.pop(sid, None)
                _nl_bad_nwl.discard(sid)
                _sat_age[sid] = 0
                print(f"[AMB-RESET] sat={sid}  SOD={sod:.0f}  reason=cycle_slip  "
                      f"cumulative_resets={amgr.cumulative_resets}")

            # ── Phase wind-up ────────────────────────────────────────────────
            wu  = _wu(m['sat_xyz'], m['rec_apc'], sun, wum.get(sid, 0.))
            wum[sid] = wu
            lam_if = m.get('_lam_if', LAMBDA_IF)
            LIFc   = m['LIF'] - wu * lam_if
            m['LIFc'] = LIFc

            # ── Gap recovery: classify gap and restore or rebirth ─────────────
            # STEP-4: epoch-based gap classification via amgr.handle_gap().
            #   gap_epochs < SHORT_GAP_EPOCHS  → RESTORE (lineage preserved)
            #   gap_epochs ≥ SHORT_GAP_EPOCHS  → REBIRTH (child lineage via
            #                                    DORMANT→RESET; register_birth
            #                                    will do RESET→REBORN→ACTIVE)
            if not amgr.is_active(sid):
                _saved = _amb_saved_state.get(sid)
                if _saved is not None:
                    _gap      = sod - _saved['last_sod']
                    _gap_ep   = nproc - _saved.get('last_epoch', nproc)
                    _gap_act  = amgr.handle_gap(sid, _gap_ep, nproc, sod)

                    if _gap_act == 'RESTORE':
                        # Short gap: restore x[ki]/P[ki,ki] from saved state.
                        # FIX-2: cap restored variance to BIRTH_SIGMA² so that
                        # a dormant ambiguity whose saved P grew (e.g. via
                        # process-noise inflation) cannot re-enter the filter
                        # with sigma > 20 000 mm, which would exceed the birth
                        # sigma and defeat dormant-continuity semantics.
                        _BIRTH_SIGMA_M = 20.0          # metres — matches birth init
                        x[ki]     = _saved['x_ki']
                        _P_raw    = min(_saved['P_ki'] * 2.0, (50.)**2)
                        _P_cap    = _BIRTH_SIGMA_M ** 2
                        _sig_before_cap = math.sqrt(max(_P_raw, 0.)) * 1e3
                        _cap_applied    = int(_P_raw > _P_cap)
                        P[ki,ki]  = min(_P_raw, _P_cap)
                        P[ki, :ki] = 0.; P[:ki, ki] = 0.
                        amgr.update_state_index(sid, ki)
                        _amb_saved_state.pop(sid)
                        print(f"[RESTORE-CAP] sat={sid}  gap_ep={_gap_ep}  "
                              f"sigma_before={_sig_before_cap:.1f}mm  "
                              f"sigma_after={math.sqrt(P[ki,ki])*1e3:.1f}mm  "
                              f"cap_applied={_cap_applied}")
                        print(f"[AMB-RESTORE] sat={sid}  SOD={sod:.0f}  "
                              f"gap={_gap:.0f}s  gap_ep={_gap_ep}  "
                              f"sigma_restored={math.sqrt(P[ki,ki])*1e3:.1f}mm")

                    else:
                        # Long/medium gap or UNKNOWN: do NOT restore state.
                        # amgr may already be in RESET state (from handle_gap).
                        # Clear saved state; normal newborn-pending birth path
                        # will call register_birth(), which sees RESET and
                        # creates a child lineage (STEP-4 REBIRTH semantics).
                        _amb_saved_state.pop(sid)
                        if _gap_act == 'REBIRTH':
                            print(f"[AMB-GAP-REBIRTH] sat={sid}  SOD={sod:.0f}  "
                                  f"gap={_gap:.0f}s  gap_ep={_gap_ep}  "
                                  f"→ child lineage on next birth")

            # ── Birth queuing: queue for post-fit birth this epoch (immediate)
            # PATCH: _AMB_MIN_EPOCHS streak gate removed. Any satellite that has
            # a valid geometry observation and passes OSB/slip checks is queued
            # for post-fit birth immediately. No streak counting, no warmup,
            # no birth_pending delay beyond the single post-fit-in-same-epoch latency.
            if not amgr.is_active(sid):
                # Check if inherited from previous pass
                if sid in _amb_init:
                    x[ki], P[ki,ki] = _amb_init.pop(sid)
                    amgr.register_inherited(sid, ki, math.sqrt(P[ki,ki]))
                    amgr.update_state_index(sid, ki)              # STEP-2: ASSERT-7 on inherited birth
                    _amb_seeded.add(sid)
                    print(f"[AMB-BIRTH] sat={sid}  SOD={sod:.0f}  INHERITED  "
                          f"N={x[ki]*1e3:+.1f}mm  sigma={math.sqrt(P[ki,ki])*1e3:.1f}mm")
                else:
                    # Queue for post-fit birth (runs after EKF update this epoch)
                    _newborn_pending[sid] = dict(m=m, ki=ki, LIFc=LIFc,
                                                 sys=m.get('_sys','?'), sod=sod)

            # ── Per-ambiguity covariance sanity ──────────────────────────────
            if amgr.is_active(sid):
                pki = P[ki, ki]
                if not math.isfinite(pki) or pki <= 0.:
                    print(f"  [WARN] {sid} amb variance non-positive ({pki:.3e}) "
                          f"SOD={sod:.0f} — resetting")
                    P[ki, ki] = 20.**2; x[ki] = 0.
                    amgr.deactivate(sid)                         # STEP-2: replaces set_active(sid, False)
                elif pki < 1e-8:
                    print(f"  [WARN] {sid} amb variance collapsed ({pki:.3e}) "
                          f"SOD={sod:.0f} — reinflating")
                    P[ki, ki] = 20.**2
                elif pki > 1e8:
                    print(f"  [WARN] {sid} amb variance exploded ({pki:.3e}) "
                          f"SOD={sod:.0f} — clamping")
                    P[ki, ki] = 50.**2

            _sat_age[sid] += 1
            m['ki']  = ki
            m['NWL'] = wl_fixed.get(sid, None)
            m['age'] = _sat_age[sid]
            amgr.mark_present(sid, nproc, sod, elevation_deg=math.degrees(m['el']))  # STEP-2: observable flag
            geom.append(m)

        # ── Minimum geometry check ───────────────────────────────────────────
        if len(geom) < 4:
            continue
        if len(geom) > 4 and _pdop(geom) > 6.0:
            worst = min(geom, key=lambda m: m['el'])
            geom  = [m for m in geom if m['sid'] != worst['sid']]
        if len(geom) < 4:
            continue

        # ── First epoch: bootstrap clock ─────────────────────────────────────
        if nproc == 0:
            clk_old = x[3]
            x[3]    = _spp_clock(geom, rxyz)
            dclk    = x[3] - clk_old
            for m in geom:
                ki = m['ki']
                if m['sid'] in _newborn_pending:
                    pass  # born post-fit
                elif m['sid'] in _amb_seeded:
                    x[ki] -= dclk
                elif amgr.is_active(m['sid']):
                    rp0 = _rp(m, x[3], x[4])
                    x[ki] = m['LIFc'] - rp0
                    P[ki, ki] = 20.**2

        # ── Build measurement matrices ────────────────────────────────────────
        ns  = len(geom)
        nst = len(x)
        # WL pseudo-obs disabled; NL pseudo-obs disabled (AR off)
        H  = np.zeros((2 * ns, nst))
        z  = np.zeros(2 * ns)
        Rd = np.zeros(2 * ns)

        xs = x.copy()   # pre-update state for residual computation

        # Hard residual thresholds (observation-domain only)
        _code_hard = 10.0 if is_startup else 5.0   # metres
        _phs_hard  = 0.10 if is_startup else 0.05  # metres

        _all_phase_res_mm = []   # for phase RMS computation
        _phase_total  = 0    # total phase rows attempted
        _phase_accept = 0    # phase rows that entered H/z/R
        _phase_rej_newborn  = 0  # excluded because x[ki] not yet initialised
        _phase_rej_nanres   = 0  # non-finite residual
        _phase_rej_hardgate = 0  # residual > hard threshold
        # FIX-4 (v642): per-epoch innovation diagnostic accumulators
        _ep_NIS_list   = []   # NIS values for accepted phase rows this epoch
        _ep_Kamb_list  = []   # K_amb values for accepted phase rows this epoch

        for ri, m in enumerate(geom):
            ki = m['ki']
            u  = m['unit']
            mw = m['mw']
            rp = _rp(m, xs[3], xs[4])
            rr = 2 * ri
            rl = 2 * ri + 1

            # ── ISB: Galileo code includes ISB term ──────────────────────────
            _is_gal = (m.get('_sys') == 'E')
            rp_code = rp + xs[ISB_IDX] if _is_gal else rp

            # ── Code row ─────────────────────────────────────────────────────
            code_res = m['PIF'] - rp_code
            if not math.isfinite(code_res):
                _reject_obs(rr, H, z, Rd, "code residual non-finite",
                            m['sid'], sod, is_startup)
                _n_rejected_epoch += 1
            elif abs(code_res) > _code_hard:
                _reject_obs(rr, H, z, Rd,
                            f"code residual {code_res*1e3:+.0f}mm > {_code_hard*1e3:.0f}mm",
                            m['sid'], sod, is_startup)
                _n_rejected_epoch += 1
            else:
                H[rr,0]=-u[0]; H[rr,1]=-u[1]; H[rr,2]=-u[2]
                H[rr,3]=1.; H[rr,4]=mw
                if _is_gal:
                    H[rr, ISB_IDX] = 1.
                z[rr]  = code_res
                Rd[rr] = _sig(m['el'], SC)**2

            # ── Phase row ────────────────────────────────────────────────────
            # FIX-3A (v64): Skip phase row below 15° elevation mask.
            # Code row is already built above — code processing continues normally.
            # Ambiguity birth/update is also skipped for this satellite this epoch.
            if m['el'] < PHASE_EL_MASK_RAD:
                _reject_obs(rl, H, z, Rd, None, m['sid'], sod, startup=False)
                _phase_total += 1
                _phase_rej_newborn += 1   # reuse newborn counter for low-el mask
            # Newborns are queued for post-fit birth; x[ki] is uninitialised
            # this epoch so the phase row cannot be formed. They participate
            # from the very next epoch with NO streak requirement.
            # Any satellite that is not newborn and not yet active has an
            # uninitialised x[ki] and must also wait one epoch (impossible in
            # practice because the birth block above sets amb_active=True in
            # the same pass via _amb_init or post-fit).
            # NO lifecycle gate: the only valid exclusions are uninitialised
            # state and the standard observation-domain residual checks below.
            elif m['sid'] in _newborn_pending or not amgr.is_active(m['sid']):
                # x[ki] uninitialised — cannot form a valid phase residual.
                # Silently zero the row (no lifecycle-rejection message).
                _reject_obs(rl, H, z, Rd, None, m['sid'], sod, startup=False)
                _phase_total += 1
                _phase_rej_newborn += 1
                # ── AUDIT: record newborn / not-yet-active as rejected ────────
                if innov_audit_fh is not None:
                    _pos_sig = math.sqrt(max(P[0,0]+P[1,1]+P[2,2], 0.)) * 1e3
                    _clk_sig = math.sqrt(max(P[3,3], 0.)) * 1e3
                    _zwd_sig = math.sqrt(max(P[4,4], 0.)) * 1e3
                    _isb_sig = math.sqrt(max(P[ISB_IDX,ISB_IDX], 0.)) * 1e3
                    _amb_sig = math.sqrt(max(P[ki,ki], 0.)) * 1e3
                    _phs_hard_mm = _phs_hard * 1e3
                    innov_audit_fh.write(
                        f"{nproc},{sod:.1f},{m['sid']},{m.get('_sys','?')},"
                        f"{math.degrees(m['el']):.3f},"
                        f"0,newborn,"
                        f"nan,nan,nan,nan,"
                        f"{_fmtf(_amb_sig,prec=4)},"
                        f"{_fmtf(_pos_sig,prec=4)},{_fmtf(_clk_sig,prec=4)},"
                        f"{_fmtf(_zwd_sig,prec=4)},{_fmtf(_isb_sig,prec=4)},"
                        f"1,0,"
                        f"{_fmtf(_phs_hard_mm,prec=4)}\n"
                    )
                # ─────────────────────────────────────────────────────────────
            else:
                _phase_total += 1
                # ALWAYS include phase row — no lifecycle gate
                phase_res = m['LIFc'] - (rp + xs[ki])

                # Galileo phase ISB correction
                if _is_gal:
                    phase_res = m['LIFc'] - (rp + xs[ISB_IDX] + xs[ki])

                # ── AUDIT: compute full innovation covariance S = H P H^T + R
                # Build phase H-row using pre-update covariance P (not xs, not P_new)
                # This is audit-only; does NOT change H/z/Rd used by the EKF.
                _h_ph = np.zeros(len(xs))
                _h_ph[0]=-m['unit'][0]; _h_ph[1]=-m['unit'][1]; _h_ph[2]=-m['unit'][2]
                _h_ph[3]=1.; _h_ph[4]=m['mw']; _h_ph[ki]=1.
                if _is_gal:
                    _h_ph[ISB_IDX] = 1.
                _ph_sig_raw = _sig(m['el'], SP)
                _R_ph = _ph_sig_raw**2
                _S_ph = float(_h_ph @ P @ _h_ph) + _R_ph   # scalar S
                _S_ph = max(_S_ph, 1e-12)
                _nu_mm = phase_res * 1e3
                _S_mm2 = _S_ph * 1e6
                _pred_sig_mm = math.sqrt(max(_S_mm2, 0.))
                # FIX-2 (v64): Guard against degenerate S; use nan rather than
                # dividing by a floored epsilon which collapses small NIS to 0.
                if (not np.isfinite(_S_mm2)) or _S_mm2 <= 0:
                    _NIS = float('nan')
                else:
                    _NIS = (_nu_mm * _nu_mm) / _S_mm2
                _amb_sig_aud  = math.sqrt(max(P[ki,ki], 0.)) * 1e3
                _pos_sig_aud  = math.sqrt(max(P[0,0]+P[1,1]+P[2,2], 0.)) * 1e3
                _clk_sig_aud  = math.sqrt(max(P[3,3], 0.)) * 1e3
                _zwd_sig_aud  = math.sqrt(max(P[4,4], 0.)) * 1e3
                _isb_sig_aud  = math.sqrt(max(P[ISB_IDX,ISB_IDX], 0.)) * 1e3
                _is_nb_aud    = 0
                _ep_birth_aud = nproc - amgr.get_birth_epoch(m['sid'], nproc)
                _phs_hard_mm  = _phs_hard * 1e3
                # ─────────────────────────────────────────────────────────────

                if not math.isfinite(phase_res):
                    _reject_obs(rl, H, z, Rd,
                                "phase residual non-finite", m['sid'], sod, is_startup)
                    _n_rejected_epoch += 1
                    _phase_rej_nanres += 1
                    # AUDIT write
                    if innov_audit_fh is not None:
                        innov_audit_fh.write(
                            f"{nproc},{sod:.1f},{m['sid']},{m.get('_sys','?')},"
                            f"{math.degrees(m['el']):.3f},"
                            f"0,nan_residual,"
                            f"nan,{_fmtf(_pred_sig_mm,prec=4)},{_fmtf(_S_mm2,prec=4)},nan,"
                            f"{_fmtf(_amb_sig_aud,prec=4)},"
                            f"{_fmtf(_pos_sig_aud,prec=4)},{_fmtf(_clk_sig_aud,prec=4)},"
                            f"{_fmtf(_zwd_sig_aud,prec=4)},{_fmtf(_isb_sig_aud,prec=4)},"
                            f"{_is_nb_aud},{_ep_birth_aud},"
                            f"{_fmtf(_phs_hard_mm,prec=4)}\n"
                        )
                    # ring-buffer update
                    amgr.register_reject(m['sid'])
                else:
                    # ── FIX-2 (v642): Configurable phase gate ────────────────
                    # FIX-3 (v642): Absolute safety backstop — always applied
                    _phase_gate_rejected = False
                    _gate_reason = "accepted"
                    if abs(phase_res) > 5.0:
                        # Gross outlier / cycle-slip protection regardless of mode
                        _phase_gate_rejected = True
                        _gate_reason = "hard_gate"
                    elif PHASE_GATE_MODE == "normalized":
                        _gate_stat = abs(phase_res) / math.sqrt(max(_S_ph, 1e-12))
                        if _gate_stat > PHASE_GATE_SIGMA_THRESH:
                            _phase_gate_rejected = True
                            _gate_reason = "hard_gate"
                    else:
                        # MODE: "absolute" — existing behaviour
                        if abs(phase_res) > _phs_hard:
                            _phase_gate_rejected = True
                            _gate_reason = "hard_gate"
                    # ─────────────────────────────────────────────────────────

                    # FIX-4 (v642): compute K_amb proxy for diagnostic logging
                    _K_amb_diag = float('nan')
                    if np.isfinite(_S_ph) and _S_ph > 1e-20:
                        _Ph_vec = P @ _h_ph   # shape (n_st,)
                        _K_amb_diag = abs(float(_Ph_vec[ki]) / _S_ph)

                    if _phase_gate_rejected:
                        _reject_obs(rl, H, z, Rd,
                                    f"phase residual {phase_res*1e3:+.0f}mm > "
                                    f"{_phs_hard*1e3:.0f}mm",
                                    m['sid'], sod, is_startup)
                        _n_rejected_epoch += 1
                        _phase_rej_hardgate += 1
                        # AUDIT write
                        if innov_audit_fh is not None:
                            innov_audit_fh.write(
                                f"{nproc},{sod:.1f},{m['sid']},{m.get('_sys','?')},"
                                f"{math.degrees(m['el']):.3f},"
                                f"0,hard_gate,"
                                f"{_fmtf(_nu_mm,prec=4)},{_fmtf(_pred_sig_mm,prec=4)},"
                                f"{_fmtf(_S_mm2,prec=4)},{_fmtf_sci(_NIS)},"
                                f"{_fmtf(_amb_sig_aud,prec=4)},"
                                f"{_fmtf(_pos_sig_aud,prec=4)},{_fmtf(_clk_sig_aud,prec=4)},"
                                f"{_fmtf(_zwd_sig_aud,prec=4)},{_fmtf(_isb_sig_aud,prec=4)},"
                                f"{_is_nb_aud},{_ep_birth_aud},"
                                f"{_fmtf(_phs_hard_mm,prec=4)}\n"
                            )
                        # ring-buffer update
                        amgr.register_reject(m['sid'])
                    else:
                        _phase_accept += 1
                        H[rl,0]=-u[0]; H[rl,1]=-u[1]; H[rl,2]=-u[2]
                        H[rl,3]=1.; H[rl,4]=mw; H[rl,ki]=1.
                        if _is_gal:
                            H[rl, ISB_IDX] = 1.
                        z[rl]  = phase_res
                        phase_sig = _sig(m['el'], SP)
                        Rd[rl] = phase_sig**2
                        # [FIX-2] Downweight removed per PHASE-RESET-3.
                        if abs(phase_res) > PHASE_RES_GATE:
                            print(f"  [PHASE-LARGE-RES] sat={m['sid']}  "
                                  f"residual={phase_res*1e3:+.1f}mm  "
                                  f"sigma={phase_sig*1e3:.2f}mm  accepted=YES")
                        _all_phase_res_mm.append(phase_res * 1e3)
                        # AUDIT write — accepted
                        if innov_audit_fh is not None:
                            innov_audit_fh.write(
                                f"{nproc},{sod:.1f},{m['sid']},{m.get('_sys','?')},"
                                f"{math.degrees(m['el']):.3f},"
                                f"1,accepted,"
                                f"{_fmtf(_nu_mm,prec=4)},{_fmtf(_pred_sig_mm,prec=4)},"
                                f"{_fmtf(_S_mm2,prec=4)},{_fmtf_sci(_NIS)},"
                                f"{_fmtf(_amb_sig_aud,prec=4)},"
                                f"{_fmtf(_pos_sig_aud,prec=4)},{_fmtf(_clk_sig_aud,prec=4)},"
                                f"{_fmtf(_zwd_sig_aud,prec=4)},{_fmtf(_isb_sig_aud,prec=4)},"
                                f"{_is_nb_aud},{_ep_birth_aud},"
                                f"{_fmtf(_phs_hard_mm,prec=4)}\n"
                            )
                        # FIX-5 (v642): write to innovation_audit.csv (new format)
                        if innov2_fh is not None:
                            innov2_fh.write(
                                f"{nproc},{m['sid']},{m.get('_sys','?')},"
                                f"{_fmtf(_nu_mm,prec=4)},"
                                f"{_fmtf(_S_mm2,prec=4)},"
                                f"{_fmtf_sci(_NIS)},"
                                f"{PHASE_GATE_MODE},"
                                f"1,"
                                f"{_fmtf(_K_amb_diag,prec=6)},"
                                f"{_fmtf(_clk_sig_aud,prec=4)},"
                                f"{_fmtf(_amb_sig_aud,prec=4)}\n"
                            )
                        # FIX-4 (v642): accumulate per-epoch NIS/K_amb diagnostics
                        if math.isfinite(_NIS):
                            _ep_NIS_list.append(_NIS)
                        if math.isfinite(_K_amb_diag):
                            _ep_Kamb_list.append(_K_amb_diag)
                        # ring-buffer + counters update
                        amgr.register_accept(
                            m['sid'], nproc, sod,
                            residual_mm=phase_res * 1e3,
                            H_norm=float(np.linalg.norm(_h_ph)),
                        )

            # Singular variance guard
            for row_i in [rr, rl]:
                if Rd[row_i] <= 0. or not math.isfinite(Rd[row_i]):
                    _reject_obs(row_i, H, z, Rd, "zero/negative variance",
                                m['sid'], sod, is_startup)

        # ── ZWD soft prior ────────────────────────────────────────────────────
        # Append ZWD pseudo-observation to H / z / Rd
        H_zwd = np.zeros(nst); H_zwd[4] = 1.
        z_zwd = np.array([ZWD_PRIOR - xs[4]])
        R_zwd = np.array([ZWD_PRIOR_SIGMA**2])

        # Concatenate with phase/code rows (filter_standard accepts full matrices)
        H_p  = np.vstack([H,  H_zwd.reshape(1,-1)])
        z_p  = np.concatenate([z,  z_zwd])
        Rd_p = np.concatenate([Rd, R_zwd])

        # ── Pre-update sanity ─────────────────────────────────────────────────
        if not _is_finite_matrix(H_p, "H"):
            print(f"  [WARN] H non-finite at SOD={sod:.0f} — skipping epoch")
            nproc += 1
            continue
        Rd_p_mat = np.diag(Rd_p)
        if not _is_pd(Rd_p_mat, "R", tol=0.):
            Rd_p = np.where(Rd_p > 0, Rd_p, 1e-4)
        if not _is_finite_matrix(P, "P pre-update"):
            print(f"  [WARN] P has non-finite entries before EKF SOD={sod:.0f}")

        # ── Capture pre-update state for diagnostics ─────────────────────────
        x_before  = x.copy()
        zwd_before= x[4]

        # ── EKF UPDATE — ALWAYS CALLED (never blocked) ───────────────────────
        _innov_norm = float('nan')
        if filter_standard(x, P, H_p.T, z_p, np.diag(Rd_p)) != 0:
            print(f"  [WARN] filter_standard failed at SOD={sod:.0f} — skipping epoch")
            nproc += 1
            continue
        _ekf_update_count += 1

        # Compute innovation norm
        _inno = z_p - H_p @ x_before
        _innov_norm = float(np.linalg.norm(_inno))

        # ── AUDIT: capture per-ambiguity Kalman gain norms post-update ────────
        # K = P_pre * H^T * S^{-1} ; approximate per-ambiguity Kg from P change
        # We use the diagonal change in P for the ki state as a proxy:
        #   |ΔP[ki,ki]| / sqrt(P_pre[ki,ki]) → qualitative participation index
        # This is DIAGNOSTIC ONLY; does not alter any EKF state.
        _P_before_diag = {sid: P[sidx[sid], sidx[sid]]
                          for sid in sidx if amgr.is_active(sid)}
        # store pre-update P[ki,ki] BEFORE Joseph symmetrisation mutates P
        # (we already ran filter_standard so P is post-update here)

        # Joseph symmetrisation
        _joseph_symmetrise(P)

        # ── STEP-2: sync current_sigma from live P diagonal → manager ─────────
        # Called once per epoch per active ambiguity, immediately after Joseph
        # symmetrisation so that amgr.get_sigma() always returns the current
        # contracted value rather than the stale birth/reset value.
        for _s2, _k2 in sidx.items():
            if amgr.is_active(_s2):
                amgr.update_sigma(_s2, math.sqrt(max(P[_k2, _k2], 0.)) * 1e3,
                                  epoch=nproc, sod=sod)
        # ─────────────────────────────────────────────────────────────────────

        # ── ZWD clamp (physically motivated) ─────────────────────────────────
        if _zwd_prev is not None and abs(x[4] - _zwd_prev) > ZWD_CLAMP:
            x[4] = _zwd_prev + math.copysign(ZWD_CLAMP, x[4] - _zwd_prev)
            P[4,4] = max(P[4,4], (ZWD_CLAMP / 3.0)**2)
        _zwd_prev = x[4]

        # ── State jump warnings ───────────────────────────────────────────────
        _dx_state   = x[:5] - x_before[:5]
        _state_jump = float(np.linalg.norm(_dx_state[:3]))
        if _state_jump > 0.5:
            print(f"  [WARN] State position jump = {_state_jump*1e3:.0f}mm at SOD={sod:.0f}")
        if _innov_norm > 1e4:
            print(f"  [WARN] Innovation norm exploded = {_innov_norm:.2e} at SOD={sod:.0f}")

        # ── Save disappearing satellites for gap recovery ─────────────────────
        for _esid, _ephi in list(amgr.items()):
            if _ephi and not any(m['sid'] == _esid for m in geom):
                _eki = sidx.get(_esid)
                if _eki is not None:
                    _amb_saved_state[_esid] = dict(
                        x_ki=float(x[_eki]),
                        P_ki=float(P[_eki, _eki]),
                        last_sod=sod,
                        last_epoch=nproc,              # STEP-4: epoch-based gap tracking
                    )
                amgr.mark_missing(_esid, sod, epoch=nproc)       # STEP-2: observable flag sync

        # ── AUDIT: write ambiguity participation row for every active amb ─────
        if amb_part_fh is not None:
            for _aps, _apki in sidx.items():
                if not amgr.is_active(_aps):
                    continue
                _ap_alive    = nproc - amgr.get_birth_epoch(_aps, nproc)
                _ap_sig      = math.sqrt(max(P[_apki, _apki], 0.)) * 1e3
                _ap_acc      = amgr.get_accepted_count(_aps)
                _ap_rej      = amgr.get_rejected_count(_aps)
                # Did this sat appear in geom this epoch?
                _ap_present  = int(any(m['sid'] == _aps for m in geom))
                _ap_missing  = 1 - _ap_present
                _ap_last_Hn  = amgr.get_H_norm(_aps)
                # Participated in EKF update if H[rl,ki] != 0 this epoch.
                # FIX-1: The original expression used Python chained-comparison
                # semantics:  "_aps in _newborn_pending is False"  is parsed as
                # (_aps in _newborn_pending) AND (_newborn_pending is False).
                # The second operand is ALWAYS False (a dict is never the
                # singleton False), so _ap_in_upd was always 0.
                # Replaced with explicit "not in" to break the chained form.
                _ap_in_upd   = int((_aps not in _newborn_pending)
                                   and amgr.get_last_accept_epoch(_aps, -1) == nproc)
                # Kalman gain proxy: sqrt(|ΔP[ki,ki]|)
                _pre_p = _P_before_diag.get(_aps, P[_apki, _apki])
                _Kg_proxy = abs(P[_apki, _apki] - _pre_p)
                _Kg_proxy = math.sqrt(max(_Kg_proxy, 0.)) * 1e3
                _ap_last_Kg = _Kg_proxy
                amgr.update_Kg_norm(_aps, _ap_last_Kg)
                _last_acc_ep = amgr.get_last_accept_epoch(_aps, -1)
                _ep_since_acc = (nproc - _last_acc_ep) if _last_acc_ep >= 0 else nproc
                _is_nb_p = int(_aps in _newborn_pending)
                _is_reset_p = int(
                    any(m['sid'] == _aps and slip
                        for m in geom for slip in [
                            abs(m.get('GF_m',0) - prev_gf.get(m['sid'],m.get('GF_m',0))) > 0.05
                            or abs(m.get('MW_cyc',0) - prev_mw.get(m['sid'],m.get('MW_cyc',0))) > 1.5
                        ]
                    )
                )
                amb_part_fh.write(
                    f"{nproc},{sod:.1f},{_aps},{_apki},"
                    f"{_ap_alive},"
                    f"{_fmtf(_ap_sig,prec=4)},"
                    f"{_ap_acc},{_ap_rej},"
                    f"{_ap_present},{_ap_missing},"
                    f"{_fmtf(_ap_last_Hn,prec=6)},"
                    f"{_ap_in_upd},"
                    f"{_fmtf(_ap_last_Kg,prec=6)},"
                    f"{_last_acc_ep},"
                    f"{_ep_since_acc},"
                    f"{_is_nb_p},{_is_reset_p}\n"
                )
        # ─────────────────────────────────────────────────────────────────────

        # ── STEP-4: Transition disappeared satellites to DORMANT ──────────────
        # Satellites that were active last epoch but absent from this epoch's
        # geom list are transitioned ACTIVE/CONVERGING/CONVERGED → DORMANT.
        # This operationalizes DORMANT semantics (Phase-6) so that handle_gap()
        # sees DORMANT state correctly when the satellite later returns.
        # Runs AFTER the participation audit (audit needs active flag intact)
        # and AFTER _amb_saved_state is populated (saves geom-independent data).
        for _desid, _dephi in list(amgr.items()):
            if _dephi and not any(m['sid'] == _desid for m in geom):
                amgr.deactivate(_desid, epoch=nproc, sod=sod)

        # ── Post-fit ambiguity birth ──────────────────────────────────────────
        # Newborns queued this epoch are born using post-EKF states.
        # They participate in EKF starting NEXT epoch.
        for _nsid, _nd in sorted(_newborn_pending.items()):
            _nki   = _nd['ki']
            _nLIFc = _nd['LIFc']
            _nm    = _nd['m']
            _nsys  = _nd['sys']
            _nsod  = _nd['sod']

            _rp_post   = _rp(_nm, x[3], x[4])
            # [FIX-1] Galileo ambiguity birth MUST subtract ISB.
            # The phase residual model for Galileo is:
            #   res = LIFc - (rp + ISB + x[ki])
            # so x[ki] must be initialised as:
            #   x[ki] = LIFc - rp - ISB         (Galileo)
            #   x[ki] = LIFc - rp               (GPS)
            # Without this, x[ki] is ~4 m too large, every phase residual
            # is ~-4 m, and the 50 mm hard gate rejects every Galileo phase
            # row → Galileo ambiguities never contract from 20 000 mm.
            _isb_birth = x[ISB_IDX] if _nsys == 'E' else 0.
            _n_postfit  = _nLIFc - _rp_post - _isb_birth
            x[_nki]    = _n_postfit
            P[_nki,_nki] = 20.**2
            P[_nki, :_nki] = 0.; P[:_nki, _nki] = 0.
            # register_birth: sets active=True, records born_epoch, writes
            # initial_sigma once (mirrors _amb_birth_sigma "not in" guard)
            amgr.register_birth(_nsid, _nki, nproc, sod, sigma_m=20.0)

            # AUDIT: compute birth-epoch innovation metrics
            # Build H row for the newborn at birth (x[ki] just set, P[ki,ki]=20m²)
            _h_birth = np.zeros(len(x))
            _h_birth[0]=-_nm['unit'][0]; _h_birth[1]=-_nm['unit'][1]; _h_birth[2]=-_nm['unit'][2]
            _h_birth[3]=1.; _h_birth[4]=_nm['mw']; _h_birth[_nki]=1.
            if _nsys == 'E':
                _h_birth[ISB_IDX] = 1.
            _birth_nu_m = _nLIFc - (_rp_post + _isb_birth + x[_nki])   # should be ~0 by construction
            _birth_R    = _sig(_nm['el'], SP)**2
            _birth_S    = float(_h_birth @ P @ _h_birth) + _birth_R
            _birth_S    = max(_birth_S, 1e-12)
            _birth_NIS  = (_birth_nu_m * 1e3)**2 / max(_birth_S * 1e6, 1e-12)
            _birth_pred_sig_mm = math.sqrt(max(_birth_S * 1e6, 0.))
            _birth_innov_mm = _birth_nu_m * 1e3

            # [FIX-3] Diagnostic: flag implausible birth values (>10 m).
            # Does NOT reject; only emits a warning for post-analysis.
            if abs(x[_nki]) > 10.0:
                print(f"  [AMB-BIRTH-OUTLIER] sat={_nsid}  SOD={_nsod:.0f}  "
                      f"N={x[_nki]*1e3:+.1f}mm  |N|>10m — possible ISB/OSB issue")

            print(f"[AMB-BORN] sat={_nsid}  SOD={_nsod:.0f}  sys={_nsys}  "
                  f"N={x[_nki]*1e3:+.1f}mm  birth_sigma=20000mm  "
                  f"(post-fit; active from next epoch)")

            # ── PHASE-4: AMB-BIRTH-FORENSIC ─────────────────────────────────
            # Full decomposition of birth value — TRACE ONLY, no logic changes.
            _rho_b   = _nm['rng']
            _sck_b   = _nm['scm']
            _dtr_b   = _nm['dtrel']
            _shp_b   = _nm['shp']
            _set_b   = _nm['setm']
            _trph_b  = _nm['trop_zhd']
            _trpw_b  = _nm['mw'] * x[4]
            _pcvs_b  = _nm['pcv_sat']
            _pcvr_b  = _nm['pcv_rec']
            _apcs_b  = _nm.get('_sat_apc_range_m', 0.)
            _apcr_b  = _nm.get('_rec_apc_range_m', 0.)
            _osb_p_b = _nm.get('_osb_IF_phase_m', 0.)
            _wu_b    = wum.get(_nsid, 0.)
            _lif_b   = _nm.get('_lam_if', LAMBDA_IF)
            _wu_mm_b = _wu_b * _lif_b * 1e3
            _clk_b   = x[3]
            _isb_b   = _isb_birth
            # Phase-5: high-detail birth forensic for first 2 hours OR any newborn
            _is_first2h = (sod - eplist[0]['t']) < 7200.
            if _is_first2h or _nsys == 'E' or is_startup:
                print(
                    f"[AMB-BIRTH-FORENSIC]\n"
                    f"  sat={_nsid}  sys={_nsys}  SOD={_nsod:.0f}"
                    f"  is_startup={is_startup}  is_first2h={_is_first2h}\n"
                    f"  observed_phase_LIFc       = {_nLIFc*1e3:+.3f} mm\n"
                    f"  predicted_without_amb+ISB = {(_rp_post+_isb_birth)*1e3:+.3f} mm\n"
                    f"    rho_geom      = {_rho_b*1e3:+.3f} mm\n"
                    f"    sat_clk(neg)  = {-_sck_b*1e3:+.3f} mm\n"
                    f"    rec_clk       = {_clk_b*1e3:+.3f} mm\n"
                    f"    isb           = {_isb_b*1e3:+.3f} mm\n"
                    f"    tropo_hydro   = {_trph_b*1e3:+.3f} mm\n"
                    f"    tropo_wet     = {_trpw_b*1e3:+.3f} mm  (ZWD={x[4]*1e3:.1f}mm)\n"
                    f"    relativity    = {_dtr_b*1e3:+.3f} mm\n"
                    f"    shapiro       = {_shp_b*1e3:+.3f} mm\n"
                    f"    solid_earth   = {_set_b*1e3:+.3f} mm\n"
                    f"    apc_sat_LOS   = {_apcs_b*1e3:+.3f} mm\n"
                    f"    apc_rec_LOS   = {_apcr_b*1e3:+.3f} mm\n"
                    f"    pcv_sat       = {_pcvs_b*1e3:+.3f} mm\n"
                    f"    pcv_rec       = {_pcvr_b*1e3:+.3f} mm\n"
                    f"    osb_IF_phase  = {_osb_p_b*1e3:+.3f} mm\n"
                    f"    wind_up       = {_wu_mm_b:+.3f} mm  ({_wu_b:.4f} cyc)\n"
                    f"  birth_value    = {x[_nki]*1e3:+.3f} mm"
                    f"  ({x[_nki]/_lif_b:.4f} cyc)\n"
                    f"  initial_sigma  = 20000 mm"
                )

            # ── PHASE-2: write one row to amb_birth_trace1.csv ────────────────
            if birth_trace_fh is not None:
                _lam_if_b = _nm.get('_lam_if', LAMBDA_IF)
                _birth_cyc = (x[_nki] / _lam_if_b) if _lam_if_b != 0. else float('nan')
                _pred_no_amb = (_rp_post + _isb_birth) * 1e3   # predicted phase without ambiguity (mm)
                birth_trace_fh.write(
                    f"{nproc},{_nsod:.1f},{_nsid},{_nsys},"
                    f"{_fmtf(math.degrees(_nm['el']),prec=3)},"
                    f"{_fmtf(x[_nki],1e3,4)},"
                    f"{_fmtf(_birth_cyc,prec=6)},"
                    f"20000.0000,"                               # birth_sigma_mm always 20000
                    f"{_fmtf(_nLIFc,1e3,4)},"                   # observed_lif_mm
                    f"{_fmtf(_pred_no_amb,prec=4)},"
                    f"{_fmtf(_rho_b,1e3,4)},"
                    f"{_fmtf(-_sck_b,1e3,4)},"
                    f"{_fmtf(_clk_b,1e3,4)},"
                    f"{_fmtf(_isb_b,1e3,4)},"
                    f"{_fmtf(_trph_b,1e3,4)},"
                    f"{_fmtf(_trpw_b,1e3,4)},"
                    f"{_fmtf(_dtr_b,1e3,4)},"
                    f"{_fmtf(_shp_b,1e3,4)},"
                    f"{_fmtf(_set_b,1e3,4)},"
                    f"{_fmtf(_apcs_b,1e3,4)},"
                    f"{_fmtf(_apcr_b,1e3,4)},"
                    f"{_fmtf(_pcvs_b,1e3,4)},"
                    f"{_fmtf(_pcvr_b,1e3,4)},"
                    f"{_fmtf(_osb_p_b,1e3,4)},"
                    f"{_fmtf(_wu_mm_b,prec=4)},"
                    f"{_fmtf(_wu_b,prec=6)},"
                    f"{1 if is_startup else 0},"
                    f"{1 if _is_first2h else 0},"
                    f"{_fmtf(_birth_innov_mm,prec=4)},"
                    f"{_fmtf_sci(_birth_NIS)},"
                    f"{_fmtf(_birth_pred_sig_mm,prec=4)}\n"
                )
            # ─────────────────────────────────────────────────────────────────
        for sid_chk in list(nl_fixed.keys()):
            if sid_chk not in sidx or not amgr.is_active(sid_chk):
                continue
            if not any(m['sid'] == sid_chk for m in geom):
                continue
            ki_chk = sidx[sid_chk]
            ob_chk = osb.get(sid_chk, {})
            m_chk  = next(m for m in geom if m['sid'] == sid_chk)
            if m_chk.get('_sys') == 'E':
                bl1c = ob_chk.get('L1C', 0.); bl2c = ob_chk.get('L5Q', 0.)
                N_IF_chk = _nl_if_value_gal(nl_fixed[sid_chk], wl_fixed[sid_chk], bl1c, bl2c)
            else:
                bl1c = ob_chk.get('L1C', ob_chk.get('L1W', 0.))
                bl2c = ob_chk.get('L2W', ob_chk.get('L2C', 0.))
                N_IF_chk = _nl_if_value(nl_fixed[sid_chk], wl_fixed[sid_chk], bl1c, bl2c)
            post_innov = abs(N_IF_chk - x[ki_chk])
            if post_innov > NL_RELEASE_THRESH:
                nl_fixed.pop(sid_chk, None)
                print(f"  [NL RELEASE] {sid_chk} SOD={sod:.0f} drift={post_innov*1e3:.1f}mm")

        # ── NL FIXING — DISABLED (float PPP convergence validation) ─────────
        # AR DISABLED: uncomment this block to re-enable NL fixing once float
        # PPP convergence is validated.
        #
        # nl_cands = [m for m in geom
        #             if m['sid'] in wl_fixed
        #             and m['sid'] not in nl_fixed
        #             and m['sid'] not in _nl_bad_nwl
        #             and amgr.is_active(m['sid'])
        #             and P[m['ki'],m['ki']] < NL_VAR_THRESH
        #             and phase_rms_now < NL_PHASE_THRESH]
        # ... (full LAMBDA block omitted — see original for implementation)

        # ── Compute residuals / RMS for diagnostics ───────────────────────────
        _active_geom = [m for m in geom
                        if amgr.is_active(m['sid'])
                        and m['sid'] not in _newborn_pending]
        phase_res_now = [m['LIFc'] - (_rp(m, x[3], x[4]) + x[m['ki']]) for m in _active_geom]
        phase_rms_now = (math.sqrt(np.mean(np.array(phase_res_now)**2))
                         if phase_res_now else float('nan'))

        code_res_all = [m['PIF'] - _rp(m, x[3], x[4]) for m in geom]
        code_rms     = (math.sqrt(np.mean(np.array(code_res_all)**2)) * 1e3
                        if code_res_all else 0.)
        phase_rms    = phase_rms_now * 1e3 if not math.isnan(phase_rms_now) else float('nan')
        _phs_str     = 'inactive' if math.isnan(phase_rms) else f'{phase_rms:.2f}mm'

        # ── PHASE-1: per-satellite forensic trace CSV rows ────────────────────
        # Write one row per active satellite to the trace_fh CSV.
        # Compute per-satellite post-fit residuals and full model decomposition.
        # TRACE ONLY — no logic changes.
        if trace_fh is not None:
            # Build a code-residual dict for quick lookup
            _code_res_dict = {m['sid']: (m['PIF'] - _rp(m, x[3], x[4])) for m in geom}
            for _tm in geom:
                _tsid  = _tm['sid']
                _tsys  = _tm.get('_sys', '?')
                _trp   = _rp(_tm, x[3], x[4])
                # Phase residual: active sats only (newborns not yet active)
                if amgr.is_active(_tsid) and _tsid not in _newborn_pending:
                    _tki   = _tm['ki']
                    _tph_res = _tm['LIFc'] - (_trp + x[_tki])
                    # Galileo includes ISB in phase model
                    if _tsys == 'E':
                        _tph_res = _tm['LIFc'] - (_trp + x[ISB_IDX] + x[_tki])
                    _tamb  = x[_tki]
                else:
                    _tph_res = float('nan')
                    _tamb    = x[_tm['ki']] if _tsid in sidx else float('nan')
                _tcode_res = _code_res_dict.get(_tsid, float('nan'))
                _tisb_code = x[ISB_IDX] if _tsys == 'E' else 0.
                _tcode_res_isb = _code_res_dict.get(_tsid, float('nan')) - _tisb_code if _tsys == 'E' else _tcode_res
                _spco = _tm.get('_sat_pco_body_mm', [0.,0.,0.])
                _rpco = _tm.get('_rec_pco_local_mm', [0.,0.,0.])
                _satapcrng = _tm.get('_sat_apc_range_m', 0.) * 1e3
                _recapcrng = _tm.get('_rec_apc_range_m', 0.) * 1e3
                _sagn = _tm.get('_sagnac_m', 0.) * 1e3
                _otlr = _tm.get('_otl_range_m', 0.) * 1e3
                _osbc1 = _tm.get('_osb_code_L1_m', 0.) * 1e3
                _osbc2 = _tm.get('_osb_code_L2_m', 0.) * 1e3
                _osbp1 = _tm.get('_osb_phase_L1_m', 0.) * 1e3
                _osbp2 = _tm.get('_osb_phase_L2_m', 0.) * 1e3
                _osbifc= _tm.get('_osb_IF_code_m', 0.) * 1e3
                _osbifp= _tm.get('_osb_IF_phase_m', 0.) * 1e3
                _LIF_raw = _tm.get('_LIF_raw', _tm['LIF'])
                _PIF_raw = _tm.get('_PIF_raw', _tm['PIF'])
                # Predicted phase (with ambiguity) and code
                _pred_ph = _trp + _tamb + (x[ISB_IDX] if _tsys == 'E' else 0.)
                _pred_cod = _trp + (x[ISB_IDX] if _tsys == 'E' else 0.)
                # Corrected observables: phase=LIF (osb already applied), code=PIF
                _corr_ph = _tm['LIFc']
                _corr_cod= _tm['PIF']
                _tamb_cyc = _tamb / _tm.get('_lam_if', LAMBDA_IF) if _tm.get('_lam_if', LAMBDA_IF) != 0 else float('nan')
                _is_nb = 1 if _tsid in _newborn_pending else 0
                trace_fh.write(
                    f"{pass_label},{sod:.1f},{_tsid},{_tsys},"
                    f"{math.degrees(_tm['el']):.3f},{math.degrees(_tm['az']):.3f},"
                    f"{_tm['L1']:.6f},{_tm['L2']:.6f},"
                    f"{_tm['P1']:.6f},{_tm['P2']:.6f},"
                    f"{_LIF_raw:.6f},{_PIF_raw:.6f},"
                    f"{_spco[0]:.3f},{_spco[1]:.3f},{_spco[2]:.3f},"
                    f"{_satapcrng:.4f},{_tm['pcv_sat']*1e3:.4f},"
                    f"{_rpco[0]:.3f},{_rpco[1]:.3f},{_rpco[2]:.3f},"
                    f"{_recapcrng:.4f},{_tm['pcv_rec']*1e3:.4f},"
                    f"{_osbc1:.4f},{_osbc2:.4f},"
                    f"{_osbp1:.4f},{_osbp2:.4f},"
                    f"{_osbifc:.4f},{_osbifp:.4f},"
                    f"{_tm['trop_zhd']:.6f},{_tm['mw']*x[4]:.6f},"
                    f"{_sagn:.4f},{_tm['dtrel']*1e3:.4f},"
                    f"{wum.get(_tsid,0.):.6f},{wum.get(_tsid,0.)*_tm.get('_lam_if',LAMBDA_IF)*1e3:.4f},"
                    f"{_tm['setm']*1e3:.4f},{_otlr:.4f},"
                    f"{_tm['rng']:.6f},"
                    f"{_corr_ph:.6f},{_corr_cod:.6f},"
                    f"{_pred_ph:.6f},{_pred_cod:.6f},"
                    f"{_fmtf(_tph_res, 1e3, 4)},"
                    f"{_fmtf(_tcode_res, 1e3, 4)},"
                    f"{_tamb:.6f},{_tamb_cyc:.6f},"
                    f"{1 if _tsid in wl_fixed else 0},"
                    f"{1 if _tsid in nl_fixed else 0}\n"
                )

        # ── PHASE-2: per-epoch constellation separation stats ─────────────────
        # Compute GPS and Galileo residuals separately and print [CONST-STATS].
        # TRACE ONLY.
        _is_first2h = (sod - eplist[0]['t']) < 7200.
        if _is_first2h or (nproc % 30 == 0):
            _gps_ph_res=[]; _gal_ph_res=[]; _gps_cod_res=[]; _gal_cod_res=[]
            _gps_amb_sig=[]; _gal_amb_sig=[]; _gps_el=[]; _gal_el=[]
            for _cm in _active_geom:
                _csys = _cm.get('_sys','?')
                _cki  = _cm['ki']
                _crp  = _rp(_cm, x[3], x[4])
                if _csys == 'E':
                    _cph_res = _cm['LIFc'] - (_crp + x[ISB_IDX] + x[_cki])
                else:
                    _cph_res = _cm['LIFc'] - (_crp + x[_cki])
                _ccod_res = _cm['PIF'] - _crp - (x[ISB_IDX] if _csys == 'E' else 0.)
                _csig = math.sqrt(max(P[_cki,_cki], 0.)) * 1e3
                if _csys == 'G':
                    _gps_ph_res.append(_cph_res*1e3); _gps_cod_res.append(_ccod_res*1e3)
                    _gps_amb_sig.append(_csig); _gps_el.append(math.degrees(_cm['el']))
                else:
                    _gal_ph_res.append(_cph_res*1e3); _gal_cod_res.append(_ccod_res*1e3)
                    _gal_amb_sig.append(_csig); _gal_el.append(math.degrees(_cm['el']))
            def _s(v, fn): return fn(v) if v else float('nan')
            _gmn=lambda v: _s(v, lambda x: sum(x)/len(x))
            _gmd=lambda v: _s(v, lambda x: sorted(x)[len(x)//2])
            _grms=lambda v: _s(v, lambda x: math.sqrt(sum(i*i for i in x)/len(x)))
            print(
                f"[CONST-STATS] epoch={nproc}  sod={sod:.0f}\n"
                f"  GPS({len(_gps_ph_res)} sats):"
                f" ph_mean={_gmn(_gps_ph_res):+.2f}mm"
                f" ph_med={_gmd(_gps_ph_res):+.2f}mm"
                f" ph_rms={_grms(_gps_ph_res):.2f}mm"
                f" cod_mean={_gmn(_gps_cod_res):+.1f}mm"
                f" amb_sig_mean={_gmn(_gps_amb_sig):.0f}mm"
                f" el_mean={_gmn(_gps_el):.1f}deg\n"
                f"  GAL({len(_gal_ph_res)} sats):"
                f" ph_mean={_gmn(_gal_ph_res):+.2f}mm"
                f" ph_med={_gmd(_gal_ph_res):+.2f}mm"
                f" ph_rms={_grms(_gal_ph_res):.2f}mm"
                f" cod_mean={_gmn(_gal_cod_res):+.1f}mm"
                f" amb_sig_mean={_gmn(_gal_amb_sig):.0f}mm"
                f" el_mean={_gmn(_gal_el):.1f}deg"
            )

        # Ambiguity sigma statistics — ALL active ambiguities
        _all_sigs = [math.sqrt(max(P[ki2, ki2], 0.))
                     for sid2, ki2 in sidx.items() if amgr.is_active(sid2)]
        _sig_mean = float(np.mean(_all_sigs))   if _all_sigs else float('nan')
        _sig_min  = float(np.min(_all_sigs))    if _all_sigs else float('nan')
        _sig_max  = float(np.max(_all_sigs))    if _all_sigs else float('nan')
        _n_active_amb = len(_all_sigs)

        # FIX-3: STABLE ambiguity stats (newborns excluded from aggregate).
        # Stable = sigma < 19 000 mm AND lifecycle_state NOT in
        # {NEW, RESET, REBORN}.  Separating these makes filter health
        # readable without newborn noise contaminating the mean/min/max.
        _STABLE_SIGMA_THRESH_MM = 19000.0
        _NEWBORN_LIFECYCLE_STATES = frozenset({
            AmbiguityLifecycleState.NEW,
            AmbiguityLifecycleState.RESET,
            AmbiguityLifecycleState.REBORN,
        })
        _stable_sigs = [
            math.sqrt(max(P[ki2, ki2], 0.))
            for sid2, ki2 in sidx.items()
            if amgr.is_active(sid2)
            and math.sqrt(max(P[ki2, ki2], 0.)) * 1e3 < _STABLE_SIGMA_THRESH_MM
            and amgr.get_lifecycle_state(sid2) not in _NEWBORN_LIFECYCLE_STATES
        ]
        _stable_sig_mean = (float(np.mean(_stable_sigs))
                            if _stable_sigs else float('nan'))
        _stable_sig_min  = (float(np.min(_stable_sigs))
                            if _stable_sigs else float('nan'))
        _stable_sig_max  = (float(np.max(_stable_sigs))
                            if _stable_sigs else float('nan'))
        _n_stable_amb    = len(_stable_sigs)

        # Condition number of position+clock+ZWD sub-block
        _P55 = P[:5, :5]
        try:
            _ev     = np.linalg.eigvalsh(_P55)
            _cond_P = float(_ev.max()) / max(abs(float(_ev.min())), 1e-20)
        except np.linalg.LinAlgError:
            _cond_P = float('nan')
        if not math.isfinite(_cond_P) or _cond_P > 1e12:
            print(f"  [WARN] P condition number = {_cond_P:.3e} at SOD={sod:.0f}")

        # ZWD jump warning
        _zwd_jump = abs(x[4] - zwd_before)
        if _zwd_jump > 0.05:
            print(f"  [WARN] ZWD jumped {_zwd_jump*1e3:+.1f}mm at SOD={sod:.0f}")

        # ── STEP-4: Per-epoch convergence tick ───────────────────────────────
        # Called after the EKF update so sigma reflects post-update covariance.
        # Drives ACTIVE→CONVERGING→CONVERGED state machine for all active ambs.
        for _cts, _ctki in sidx.items():
            if not amgr.is_active(_cts):
                continue
            _ct_sigma    = math.sqrt(max(P[_ctki, _ctki], 0.)) * 1e3
            _ct_res_list = amgr.get_recent_ph_res(_cts)
            _ct_last_res = _ct_res_list[-1] if _ct_res_list else None
            _ct_in_flt   = (amgr.get_last_accept_epoch(_cts, -1) == nproc)
            amgr.tick(_cts, nproc, sod, _ct_sigma,
                      residual_mm=_ct_last_res,
                      in_filter=_ct_in_flt)

        # ── Per-epoch diagnostic print ────────────────────────────────────────
        n_gps = sum(1 for m in geom if m['sid'][0] == 'G')
        n_gal = sum(1 for m in geom if m['sid'][0] == 'E')
        _pos_upd_norm = float(np.linalg.norm(x[:3] - x_before[:3])) * 1e3
        print(f"[EPOCH] SOD={sod:6.0f}  sats={len(geom)}(G{n_gps}+E{n_gal})"
              f"  code_rms={code_rms:.1f}mm  phase_rms={_phs_str}"
              f"  innov_norm={_innov_norm:.2f}  rej={_n_rejected_epoch}"
              f"  ZWD={x[4]*1e3:.1f}mm  ISB={x[ISB_IDX]*1e3:+.1f}mm"
              f"  pos_upd={_pos_upd_norm:.1f}mm"
              f"  amb_sigma_all: mean={_sig_mean*1e3:.0f}mm min={_sig_min*1e3:.0f}mm"
              f" max={_sig_max*1e3:.0f}mm n={_n_active_amb}"
              f"  amb_sigma_stable: mean={_stable_sig_mean*1e3:.0f}mm"
              f" min={_stable_sig_min*1e3:.0f}mm"
              f" max={_stable_sig_max*1e3:.0f}mm n={_n_stable_amb}"
              f"  cond_P={_cond_P:.1e}  ekf#={_ekf_update_count}")

        # ── FIX-4 (v642): per-epoch innovation diagnostic summary ─────────────
        if _ep_NIS_list:
            import statistics as _stat
            _ep_nis_mean   = sum(_ep_NIS_list) / len(_ep_NIS_list)
            _ep_nis_med    = _stat.median(_ep_NIS_list)
            _ep_nis_max    = max(_ep_NIS_list)
            _ep_nis_p95    = sorted(_ep_NIS_list)[int(0.95 * len(_ep_NIS_list))]
            _ep_kamb_mean  = (sum(_ep_Kamb_list) / len(_ep_Kamb_list)) if _ep_Kamb_list else float('nan')
            _ep_kamb_med   = _stat.median(_ep_Kamb_list) if _ep_Kamb_list else float('nan')
            _ep_kamb_max   = max(_ep_Kamb_list) if _ep_Kamb_list else float('nan')
            if nproc % 30 == 0:   # print every 30 epochs to avoid log flood
                print(f"  [NIS] mean={_ep_nis_mean:.4e}  median={_ep_nis_med:.4e}"
                      f"  max={_ep_nis_max:.4e}  p95={_ep_nis_p95:.4e}")
                print(f"  [KAMB] mean={_ep_kamb_mean:.4e}  median={_ep_kamb_med:.4e}"
                      f"  max={_ep_kamb_max:.4e}")
        # ─────────────────────────────────────────────────────────────────────

        # ── PHASE-3: write constellation stats row ────────────────────────────
        if constat_fh is not None:
            # Re-use or recompute per-constellation residuals for this epoch
            _cs_gps_ph=[]; _cs_gal_ph=[]; _cs_gps_cod=[]; _cs_gal_cod=[]
            _cs_gps_sig=[]; _cs_gal_sig=[]; _cs_gps_el=[]; _cs_gal_el=[]
            for _csm in _active_geom:
                _cski=_csm['ki']; _cssys=_csm.get('_sys','?')
                _csrp=_rp(_csm,x[3],x[4])
                if _cssys=='E': _csph=_csm['LIFc']-(_csrp+x[ISB_IDX]+x[_cski])
                else:           _csph=_csm['LIFc']-(_csrp+x[_cski])
                _cscod=_csm['PIF']-_csrp-(x[ISB_IDX] if _cssys=='E' else 0.)
                _cssig=math.sqrt(max(P[_cski,_cski],0.))*1e3
                if _cssys=='G':
                    _cs_gps_ph.append(_csph*1e3); _cs_gps_cod.append(_cscod*1e3)
                    _cs_gps_sig.append(_cssig); _cs_gps_el.append(math.degrees(_csm['el']))
                else:
                    _cs_gal_ph.append(_csph*1e3); _cs_gal_cod.append(_cscod*1e3)
                    _cs_gal_sig.append(_cssig); _cs_gal_el.append(math.degrees(_csm['el']))
            def _cs_mn(v): return sum(v)/len(v) if v else float('nan')
            def _cs_md(v): return sorted(v)[len(v)//2] if v else float('nan')
            def _cs_rms(v): return math.sqrt(sum(i*i for i in v)/len(v)) if v else float('nan')
            constat_fh.write(
                f"{nproc},{sod:.1f},"
                f"{_fmtf(_cs_mn(_cs_gps_ph),prec=4)},{_fmtf(_cs_md(_cs_gps_ph),prec=4)},{_fmtf(_cs_rms(_cs_gps_ph),prec=4)},"
                f"{_fmtf(_cs_mn(_cs_gps_cod),prec=4)},{_fmtf(_cs_mn(_cs_gps_sig),prec=4)},{_fmtf(_cs_mn(_cs_gps_el),prec=4)},"
                f"{_fmtf(_cs_mn(_cs_gal_ph),prec=4)},{_fmtf(_cs_md(_cs_gal_ph),prec=4)},{_fmtf(_cs_rms(_cs_gal_ph),prec=4)},"
                f"{_fmtf(_cs_mn(_cs_gal_cod),prec=4)},{_fmtf(_cs_mn(_cs_gal_sig),prec=4)},{_fmtf(_cs_mn(_cs_gal_el),prec=4)},"
                f"{x[0]*1e3:.4f},{x[1]*1e3:.4f},{x[2]*1e3:.4f},"
                f"{x[3]*1e3:.4f},{x[4]*1e3:.4f},{x[ISB_IDX]*1e3:.4f}\n"
            )
        # ─────────────────────────────────────────────────────────────────────

        # Periodic detailed print every 60 epochs
        if nproc % 60 == 0 or is_startup:
            _sig_strs = []
            for _sid2, _ki2 in sorted(sidx.items()):
                if amgr.is_active(_sid2):
                    _sg = math.sqrt(max(P[_ki2, _ki2], 0.)) * 1e3
                    _xk = x[_ki2] * 1e3
                    _sig_strs.append(f"{_sid2}(σ={_sg:.0f}mm,x={_xk:+.0f}mm)")
            if _sig_strs:
                print(f"  [AMB-SIGMA] " + "  ".join(_sig_strs))

        # ── PHASE-5: first-2h high-detail Galileo + high-residual sat log ──────
        # For the first 2 hours (7200 s) print per-Galileo-satellite residuals
        # and flag the top-3 highest-residual sats.  TRACE ONLY.
        if _is_first2h:
            # All Galileo sats with their residuals
            _gal_detail = []
            for _fdm in _active_geom:
                if _fdm.get('_sys') == 'E':
                    _fdki = _fdm['ki']
                    _fdrp = _rp(_fdm, x[3], x[4])
                    _fdph = _fdm['LIFc'] - (_fdrp + x[ISB_IDX] + x[_fdki])
                    _fdcod= _fdm['PIF'] - _fdrp - x[ISB_IDX]
                    _fdsg = math.sqrt(max(P[_fdki,_fdki],0.))*1e3
                    _gal_detail.append((_fdm['sid'], _fdph*1e3, _fdcod*1e3,
                                        _fdsg, math.degrees(_fdm['el'])))
            if _gal_detail:
                _gal_lines = [
                    f"    {s}: ph={ph:+.2f}mm cod={cd:+.1f}mm sig={sg:.0f}mm el={el:.1f}deg"
                    for s,ph,cd,sg,el in _gal_detail
                ]
                print(f"  [PHASE5-GAL-DETAIL] SOD={sod:.0f}\n" + "\n".join(_gal_lines))
            _all_res_detail = []
            for _fdm in _active_geom:
                _fdki = _fdm['ki']; _fdrp = _rp(_fdm, x[3], x[4])
                if _fdm.get('_sys')=='E':
                    _fdph = _fdm['LIFc'] - (_fdrp+x[ISB_IDX]+x[_fdki])
                else:
                    _fdph = _fdm['LIFc'] - (_fdrp+x[_fdki])
                _all_res_detail.append((_fdm['sid'], abs(_fdph*1e3), _fdph*1e3,
                                        math.degrees(_fdm['el']),
                                        _fdm.get('_sys','?')))
            _all_res_detail.sort(key=lambda v: v[1], reverse=True)
            for _topsid, _topabs, _topres, _topel, _topsys in _all_res_detail[:3]:
                print(f"  [PHASE5-TOP-RES] sat={_topsid}({_topsys})"
                      f" |ph_res|={_topabs:.2f}mm"
                      f" ph_res={_topres:+.2f}mm el={_topel:.1f}deg SOD={sod:.0f}")

        # [PHASE-PARTICIPATION] diagnostic every 300 epochs
        # lifecycle_rejected MUST be 0 after patching — any non-zero value
        # indicates a surviving lifecycle gate somewhere in the code.
        if nproc % 300 == 0:
            _rej_obs_domain = _phase_rej_nanres + _phase_rej_hardgate
            print(f"[PHASE-PARTICIPATION] SOD={sod:.0f}  ekf#={_ekf_update_count}"
                  f"  total={_phase_total}"
                  f"  accepted={_phase_accept}"
                  f"  rejected={_phase_total - _phase_accept}"
                  f"  (lifecycle={_phase_rej_newborn}"
                  f"  nan={_phase_rej_nanres}"
                  f"  hardgate={_phase_rej_hardgate})"
                  f"  LIFECYCLE_GATE_COUNT={_phase_rej_newborn}"
                  f"  [MUST_BE_ZERO_AFTER_CONVERGENCE]")
            # ── Step-1 refactor: write ambiguity-manager snapshot CSV ─────────
            amgr.write_snapshot(nproc, sidx, P)
            # ── Step-3: run ASSERT-L1…L7 at every snapshot checkpoint ────────
            amgr.run_consistency_assertions(nproc, sod)

        # ── Store for RTS / output ────────────────────────────────────────────
        pos = nom + x[:3]
        dx  = pos - REF
        d3  = np.linalg.norm(dx) * 1e3
        ZHD = zhd; ZWD = x[4]; TROPO = ZHD + ZWD

        results[sod] = {
            'xyz': pos.copy(), 'dx': dx.copy(),
            'p_trace': P[0,0]+P[1,1]+P[2,2],
            'n': len(geom), 'ztd': TROPO,
            'wl_fixed': len(wl_fixed), 'nl_fixed': len(nl_fixed),
            'code_rms': code_rms, 'phase_rms': phase_rms,
            'zhd': ZHD, 'zwd': ZWD,
            'sats_used': sorted([m['sid'] for m in geom]),
            'sats_wl':   sorted([s for s in wl_fixed if any(m['sid']==s for m in geom)]),
            'sats_nl':   sorted([s for s in nl_fixed  if any(m['sid']==s for m in geom)]),
            'pass': label,
        }

        if nproc <= 3 or nproc % 240 == 0:
            print(f"  [{label}] SOD={sod:6.0f}  N={len(geom):2d}(G{n_gps}+E{n_gal})"
                  f"  3D={d3:8.1f}mm  WL={len(wl_fixed)}  NL=0(AR off)"
                  f"  ZHD={ZHD:.3f}m  ZWD={ZWD:.4f}m"
                  f"  CodeRMS={code_rms:.1f}mm  PhsRMS={_phs_str}")

        # RTS store
        if direction == 1:
            if not hasattr(_rts_store, '_data'):
                _rts_store._data = []
            _rts_store._data.append((sod, x.copy(), P.copy()))

        _prev_code_rms  = code_rms  if code_rms  > 0 else _prev_code_rms
        _prev_phase_rms = phase_rms if (not math.isnan(phase_rms) and phase_rms > 0) else _prev_phase_rms

        # ── PHASE-3: EKF state trace CSV write ────────────────────────────────
        # Write one row per epoch with full EKF state and per-constellation stats.
        # TRACE ONLY.
        if state_trace_fh is not None:
            _gps_ph3=[]; _gal_ph3=[]; _gps_cod3=[]; _gal_cod3=[]
            _gps_sig3=[]; _gal_sig3=[]
            for _pm in _active_geom:
                _psys=_pm.get('_sys','?'); _pki=_pm['ki']
                _prp=_rp(_pm, x[3], x[4])
                if _psys=='E': _pph=_pm['LIFc']-(_prp+x[ISB_IDX]+x[_pki])
                else:            _pph=_pm['LIFc']-(_prp+x[_pki])
                _pcod=_pm['PIF']-_prp-(x[ISB_IDX] if _psys=='E' else 0.)
                _psig=math.sqrt(max(P[_pki,_pki],0.))*1e3
                if _psys=='G': _gps_ph3.append(_pph*1e3); _gps_cod3.append(_pcod*1e3); _gps_sig3.append(_psig)
                else:            _gal_ph3.append(_pph*1e3); _gal_cod3.append(_pcod*1e3); _gal_sig3.append(_psig)
            def _st(v,fn): return fn(v) if v else float('nan')
            _srms = lambda v: _st(v, lambda z: math.sqrt(sum(i*i for i in z)/len(z)))
            _smn  = lambda v: _st(v, lambda z: sum(z)/len(z))
            _smin = lambda v: _st(v, min)
            _smax = lambda v: _st(v, max)
            # Per-constellation amb sigma
            _gps_amb_sigs3=[math.sqrt(max(P[sidx[s],sidx[s]],0.))*1e3
                            for s in sidx if s[0]=='G' and amgr.is_active(s)]
            _gal_amb_sigs3=[math.sqrt(max(P[sidx[s],sidx[s]],0.))*1e3
                            for s in sidx if s[0]=='E' and amgr.is_active(s)]
            _pos_sigma_mm = math.sqrt(P[0,0]+P[1,1]+P[2,2])*1e3
            state_trace_fh.write(
                f"{nproc},{sod:.1f},"
                f"{x[0]:.6f},{x[1]:.6f},{x[2]:.6f},"
                f"{x[3]*1e3:.4f},{x[4]*1e3:.4f},{x[ISB_IDX]*1e3:.4f},"
                f"{float(np.linalg.norm(x[:3]-x_before[:3]))*1e3:.4f},"
                f"{abs(x[3]-x_before[3])*1e3:.4f},"
                f"{abs(x[4]-x_before[4])*1e3:.4f},"
                f"{abs(x[ISB_IDX]-x_before[ISB_IDX])*1e3:.4f},"
                f"{_cond_P:.4e},"
                f"{_pos_sigma_mm:.4f},"
                f"{math.sqrt(P[3,3])*1e3:.4f},"
                f"{math.sqrt(P[4,4])*1e3:.4f},"
                f"{math.sqrt(P[ISB_IDX,ISB_IDX])*1e3:.4f},"
                f"{_fmtf(_smn(_gps_amb_sigs3),prec=2)},{_fmtf(_smn(_gal_amb_sigs3),prec=2)},"
                f"{_fmtf(_smin(_gps_amb_sigs3),prec=2)},{_fmtf(_smin(_gal_amb_sigs3),prec=2)},"
                f"{_fmtf(_smax(_gps_amb_sigs3),prec=2)},{_fmtf(_smax(_gal_amb_sigs3),prec=2)},"
                f"{_fmtf(_srms(_gps_ph3),prec=4)},{_fmtf(_srms(_gal_ph3),prec=4)},"
                f"{_fmtf(_srms(_gps_cod3),prec=4)},{_fmtf(_srms(_gal_cod3),prec=4)}\n"
            )

        nproc += 1

    # ── End-of-pass summary ───────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print(f"[PASS-SUMMARY]  pass={pass_label}  label={label}")
    print(f"  Total epochs processed : {nproc}")
    print(f"  Total EKF updates      : {_ekf_update_count}  "
          f"(rate={_ekf_update_count/max(nproc,1)*100:.1f}%)")
    print(f"  Total ambiguity resets : {amgr.cumulative_resets}")

    # Ambiguity sigma contraction statistics
    print(f"\n[AMB-SIGMA-CONTRACTION]")
    _contraction_lines = []
    for _sid2, _ki2 in sorted(sidx.items()):
        _sig_final = math.sqrt(max(P[_ki2, _ki2], 0.)) * 1e3
        _sig_birth = amgr.get_birth_sigma_mm(_sid2, default_mm=20000.0)
        _ratio     = _sig_final / max(_sig_birth, 1.)
        _contraction_lines.append(
            f"  {_sid2:6s}: birth={_sig_birth:.0f}mm  final={_sig_final:.0f}mm  "
            f"ratio={_ratio:.4f}  active={'YES' if amgr.is_active(_sid2) else 'NO'}"
        )
    for ln in _contraction_lines:
        print(ln)
    if not _contraction_lines:
        print("  (no ambiguities allocated)")
    print(f"{'='*72}\n")

    # ── Build 7-element canonical return tuple ────────────────────────────────
    # Signature:  results, end_xyz, end_clk, end_zwd, wl_fixed, amb_states, snap
    # All values come from state already computed in the epoch loop;
    # nothing new is calculated here.
    _end_xyz    = nom + x[:3]                          # final XYZ position (m)
    _end_clk    = float(x[3])                          # final receiver clock (m)
    _end_zwd    = float(x[4])                          # final ZWD (m)
    _amb_states = {sid: float(x[ki])                   # final float amb states (m)
                   for sid, ki in sidx.items()}
    _snap       = {                                     # minimal state snapshot
        'x':    x.copy(),
        'P':    P.copy(),
        'sidx': dict(sidx),
        'nom':  nom.copy(),
        'namb': namb,
    }

    print(f"[_PPP_PASS_RETURN_SIGNATURE]")
    print(f"  n_return_values = 7")
    print(f"  names = [results, end_xyz, end_clk, end_zwd, wl_fixed, amb_states, snap]")

    return (results, _end_xyz, _end_clk, _end_zwd, wl_fixed, _amb_states, _snap)



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
    _trace_path=os.path.join(ddir,'ppp_model_trace1.csv')
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

    # ── PHASE-3: open state trace CSV ────────────────────────────────────────
    _state_trace_path = os.path.join(ddir, 'startup_state_trace1.csv')
    _STATE_HDR = (
        "epoch,sod,"
        "dx,dy,dz,"
        "receiver_clock_mm,zwd_mm,isb_mm,"
        "pos_update_norm_mm,clk_update_mm,zwd_update_mm,isb_update_mm,"
        "condP,"
        "pos_sigma_mm,clk_sigma_mm,zwd_sigma_mm,isb_sigma_mm,"
        "gps_amb_sigma_mean_mm,gal_amb_sigma_mean_mm,"
        "gps_amb_sigma_min_mm,gal_amb_sigma_min_mm,"
        "gps_amb_sigma_max_mm,gal_amb_sigma_max_mm,"
        "gps_phase_rms_mm,gal_phase_rms_mm,"
        "gps_code_rms_mm,gal_code_rms_mm\n"
    )
    _state_trace_fh = open(_state_trace_path, 'w')
    _state_trace_fh.write(_STATE_HDR)
    print(f"[TRACE] Writing state trace → {_state_trace_path}")

    # ── PHASE-2: open amb_birth_trace CSV ────────────────────────────────────
    _birth_trace_path = os.path.join(ddir, 'amb_birth_trace1.csv')
    _BIRTH_HDR = (
        "epoch,sod,sat,const,elevation_deg,"
        "birth_mm,birth_cycles,birth_sigma_mm,"
        "observed_lif_mm,predicted_noamb_mm,"
        "rho_geom_mm,sat_clk_mm,rec_clk_mm,isb_mm,"
        "tropo_hydro_mm,tropo_wet_mm,"
        "relativity_mm,shapiro_mm,solid_earth_mm,"
        "apc_sat_mm,apc_rec_mm,"
        "pcv_sat_mm,pcv_rec_mm,"
        "osb_if_phase_mm,windup_mm,"
        "windup_cycles,"
        "is_startup,is_first2h,"
        "birth_innovation_mm,birth_NIS,birth_predicted_sigma_mm\n"
    )
    _birth_trace_fh = open(_birth_trace_path, 'w')
    _birth_trace_fh.write(_BIRTH_HDR)
    print(f"[TRACE] Writing amb birth trace → {_birth_trace_path}")

    # ── PHASE-3: open constellation_stats_trace CSV ───────────────────────────
    _constat_path = os.path.join(ddir, 'constellation_stats_trace1.csv')
    _CONSTAT_HDR = (
        "epoch,sod,"
        "gps_phase_mean_mm,gps_phase_median_mm,gps_phase_rms_mm,"
        "gps_code_mean_mm,gps_amb_sigma_mean_mm,gps_mean_elev_deg,"
        "gal_phase_mean_mm,gal_phase_median_mm,gal_phase_rms_mm,"
        "gal_code_mean_mm,gal_amb_sigma_mean_mm,gal_mean_elev_deg,"
        "dx_mm,dy_mm,dz_mm,clock_mm,zwd_mm,isb_mm\n"
    )
    _constat_fh = open(_constat_path, 'w')
    _constat_fh.write(_CONSTAT_HDR)
    print(f"[TRACE] Writing constellation stats trace → {_constat_path}")

    # ── PHASE-2: open innovation_audit.csv ───────────────────────────────────
    _innov_audit_path = os.path.join(ddir, 'innovation_audit1.csv')
    _INNOV_HDR = (
        "epoch,sod,sat,const,elevation_deg,"
        "accepted,reject_reason,"
        "innovation_mm,predicted_sigma_mm,innovation_variance_mm2,"
        "normalized_innovation_squared,"
        "ambiguity_sigma_mm,"
        "position_sigma_mm,clock_sigma_mm,zwd_sigma_mm,isb_sigma_mm,"
        "is_newborn,epochs_since_birth,"
        "phase_gate_mm\n"
    )
    _innov_audit_fh = open(_innov_audit_path, 'w')
    _innov_audit_fh.write(_INNOV_HDR)
    print(f"[TRACE] Writing innovation audit → {_innov_audit_path}")

    # ── FIX-5 (v642): open innovation_audit.csv (new condensed format) ───────
    _innov2_path = os.path.join(ddir, 'innovation_audit.csv')
    _INNOV2_HDR = (
        "epoch,sat,constellation,residual_mm,S_phase_mm2,NIS,"
        "gate_mode,accepted,K_amb,clk_sigma_mm,amb_sigma_mm\n"
    )
    _innov2_fh = open(_innov2_path, 'w')
    _innov2_fh.write(_INNOV2_HDR)
    print(f"[TRACE] Writing innovation_audit (v642) → {_innov2_path}")
    # ─────────────────────────────────────────────────────────────────────────

    # ── PHASE-3: open ambiguity_participation_audit.csv ──────────────────────
    _amb_part_path = os.path.join(ddir, 'ambiguity_participation_audit1.csv')
    _AMB_PART_HDR = (
        "epoch,sod,sat,ki,"
        "alive_epochs,"
        "sigma_mm,"
        "accepted_phase_updates,rejected_phase_updates,"
        "phase_rows_present,phase_rows_missing,"
        "last_H_norm,"
        "participated_in_update,"
        "kalman_gain_norm,"
        "last_successful_update_epoch,"
        "epochs_since_last_accept,"
        "is_newborn,is_recently_reset\n"
    )
    _amb_part_fh = open(_amb_part_path, 'w')
    _amb_part_fh.write(_AMB_PART_HDR)
    print(f"[TRACE] Writing ambiguity participation audit → {_amb_part_path}")

    # ── PHASE-4: open reset_audit.csv ────────────────────────────────────────
    _reset_audit_path = os.path.join(ddir, 'reset_audit1.csv')
    _RESET_HDR = (
        "epoch,sod,sat,const,"
        "GF_value_mm,MW_value_cyc,"
        "phase_jump_mm,code_jump_cyc,"
        "elevation_deg,"
        "pre_reset_sigma_mm,"
        "recent_phase_residual_rms_mm,"
        "recent_rejection_fraction,"
        "epochs_since_birth,"
        "was_recently_rejected,"
        "cumulative_reset_number\n"
    )
    _reset_audit_fh = open(_reset_audit_path, 'w')
    _reset_audit_fh.write(_RESET_HDR)
    print(f"[TRACE] Writing reset audit → {_reset_audit_path}")

    # ── Step-1 refactor: ambiguity-manager snapshot CSV ──────────────────────
    _amgr_snap_path = os.path.join(ddir, 'ambiguity_manager_snapshot1.csv')
    _amgr_snap_fh   = open(_amgr_snap_path, 'w')
    _amgr_snap_fh.write(AmbiguityManager.snapshot_header())
    print(f"[TRACE] Writing amb-manager snapshot → {_amgr_snap_path}")

    # ── STEP-2: ambiguity lifecycle trace CSV ────────────────────────────────
    _lifecycle_path = os.path.join(ddir, 'ambiguity_lifecycle_trace1.csv')
    _lifecycle_fh   = open(_lifecycle_path, 'w')
    _lifecycle_fh.write(AmbiguityManager.lifecycle_trace_header())
    print(f"[TRACE] Writing amb lifecycle trace → {_lifecycle_path}")
    # ─────────────────────────────────────────────────────────────────────────

    mode_labels=[('G','GPS-only'),('E','Galileo-only'),('GE','GPS+Galileo')]
    all_fwd={}; all_rts={}; all_meta={}

    for const,label in mode_labels:
        print(f"\n{'='*72}")
        print(f"[MODE] {label}  (constellation='{const}')")
        _rts_store._data=[]
        # Only write detailed traces for the combined GPS+Galileo pass
        _pass_state_fh = _state_trace_fh if const == 'GE' else None
        _pass_obs_fh   = _trace_fh       if const == 'GE' else None
        _pass_birth_fh = _birth_trace_fh if const == 'GE' else None
        _pass_constat_fh = _constat_fh   if const == 'GE' else None
        # Audit outputs: only written for GPS+Galileo pass
        _pass_innov_fh  = _innov_audit_fh  if const == 'GE' else None
        _pass_ambpt_fh  = _amb_part_fh     if const == 'GE' else None
        _pass_reset_fh  = _reset_audit_fh  if const == 'GE' else None
        _pass_amgr_fh   = _amgr_snap_fh    if const == 'GE' else None
        _pass_lc_fh     = _lifecycle_fh    if const == 'GE' else None  # STEP-2: lifecycle trace
        _pass_innov2_fh = _innov2_fh       if const == 'GE' else None  # FIX-5 (v642)
        _ret = _ppp_pass(
            epochs,nom=APX.copy(),iclk=0.,izwd=0.20,
            direction=1,label="FWD",constellation=const,
            trace_fh=_pass_obs_fh,pass_label=label,
            state_trace_fh=_pass_state_fh,
            birth_trace_fh=_pass_birth_fh,
            constat_fh=_pass_constat_fh,
            innov_audit_fh=_pass_innov_fh,
            amb_part_fh=_pass_ambpt_fh,
            reset_audit_fh=_pass_reset_fh,
            amgr_snap_fh=_pass_amgr_fh,
            lifecycle_fh=_pass_lc_fh,
            innov2_fh=_pass_innov2_fh,
            **_common)
        assert len(_ret) == 7, (
            f"[_PPP_PASS_SIGNATURE_ERR] expected 7 return values, got {len(_ret)} — "
            f"interface drift detected")
        print(f"[_PPP_PASS_SIGNATURE_OK]  n_return_values=7")
        fwd, ex, ec, ez, wl_f, fwd_amb, fwd_snap = _ret
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
    _state_trace_fh.close()
    _birth_trace_fh.close()
    _constat_fh.close()
    _innov_audit_fh.close()
    _innov2_fh.close()                                 # FIX-5 (v642)
    _amb_part_fh.close()
    _reset_audit_fh.close()
    _amgr_snap_fh.close()
    _lifecycle_fh.close()                              # STEP-2
    print(f"[TRACE] Closed state trace: {_state_trace_path}")
    print(f"[TRACE] Closed amb birth trace: {_birth_trace_path}")
    print(f"[TRACE] Closed constellation stats trace: {_constat_path}")
    print(f"[TRACE] Closed innovation audit: {_innov_audit_path}")
    print(f"[TRACE] Closed innovation_audit (v642): {_innov2_path}")
    print(f"[TRACE] Closed ambiguity participation audit: {_amb_part_path}")
    print(f"[TRACE] Closed reset audit: {_reset_audit_path}")
    print(f"\n[TRACE] Closed: {_trace_path}")

    # ── PHASE-4: STARTUP BIRTH SUMMARY ───────────────────────────────────────
    # Automatically analyse amb_birth_trace1.csv after the run completes.
    # PURE FORENSIC — no numerical changes.
    try:
        import csv as _csv2
        _brows = []
        with open(_birth_trace_path, 'r') as _bfh:
            _brd = _csv2.DictReader(_bfh)
            for _br in _brd:
                try:
                    _brows.append({
                        'sat':      _br['sat'],
                        'const':    _br['const'],
                        'elev':     float(_br['elevation_deg']),
                        'birth_mm': float(_br['birth_mm']),
                        'windup':   float(_br['windup_mm']),
                        'osb':      float(_br['osb_if_phase_mm']),
                        'is_startup': int(_br['is_startup']),
                    })
                except (ValueError, KeyError):
                    continue

        if _brows:
            _abs_b = [abs(r['birth_mm']) for r in _brows]
            print(f"\n{'='*72}")
            print(f"[STARTUP-BIRTH-SUMMARY]")
            print(f"  Total ambiguity births analysed : {len(_brows)}")
            print(f"  Mean |birth_mm|                 : {sum(_abs_b)/len(_abs_b):.1f} mm")
            print(f"  Max  |birth_mm|                 : {max(_abs_b):.1f} mm")

            # 1. Top 20 largest |birth_mm|
            _sorted_b = sorted(_brows, key=lambda r: abs(r['birth_mm']), reverse=True)
            print(f"\n  [1] TOP-20 LARGEST |birth_mm|")
            for _i, _r in enumerate(_sorted_b[:20]):
                print(f"    {_i+1:2d}. {_r['sat']:4s} ({_r['const']})  "
                      f"|birth|={abs(_r['birth_mm']):8.1f} mm  "
                      f"birth={_r['birth_mm']:+9.1f} mm  "
                      f"el={_r['elev']:.1f}°  startup={_r['is_startup']}")

            # 2. Mean |birth_mm| by constellation
            _g_b = [abs(r['birth_mm']) for r in _brows if r['const']=='G']
            _e_b = [abs(r['birth_mm']) for r in _brows if r['const']=='E']
            print(f"\n  [2] MEAN |birth_mm| BY CONSTELLATION")
            print(f"    GPS     : {sum(_g_b)/len(_g_b):.1f} mm  (n={len(_g_b)})" if _g_b else "    GPS     : (none)")
            print(f"    Galileo : {sum(_e_b)/len(_e_b):.1f} mm  (n={len(_e_b)})" if _e_b else "    Galileo : (none)")

            # 3. Mean |birth_mm| by elevation bin
            _bins = [('> 60°',  60, 999), ('30–60°', 30, 60),
                     ('15–30°', 15, 30),  ('< 15°',   0, 15)]
            print(f"\n  [3] MEAN |birth_mm| BY ELEVATION BIN")
            for _bname, _blo, _bhi in _bins:
                _bin_b = [abs(r['birth_mm']) for r in _brows if _blo <= r['elev'] < _bhi]
                if _bin_b:
                    print(f"    {_bname:8s}: {sum(_bin_b)/len(_bin_b):.1f} mm  (n={len(_bin_b)})")
                else:
                    print(f"    {_bname:8s}: (none)")

            # 4. Mean |birth_mm| by satellite
            _sat_groups = {}
            for _r in _brows:
                _sat_groups.setdefault(_r['sat'], []).append(abs(_r['birth_mm']))
            print(f"\n  [4] MEAN |birth_mm| BY SATELLITE (sorted by mean)")
            _sat_sorted = sorted(_sat_groups.items(),
                                 key=lambda kv: sum(kv[1])/len(kv[1]), reverse=True)
            for _sv, _sv_b in _sat_sorted:
                print(f"    {_sv:4s}: mean={sum(_sv_b)/len(_sv_b):.1f} mm  "
                      f"max={max(_sv_b):.1f} mm  n={len(_sv_b)}")

            # 5. Correlation: birth_mm vs elevation
            def _corr(xs, ys):
                n=len(xs)
                if n<3: return float('nan')
                mx=sum(xs)/n; my=sum(ys)/n
                num=sum((a-mx)*(b-my) for a,b in zip(xs,ys))
                den=math.sqrt(sum((a-mx)**2 for a in xs)*sum((b-my)**2 for b in ys))
                return num/den if den>0 else float('nan')
            _b_vals = [r['birth_mm'] for r in _brows]
            _e_vals = [r['elev']     for r in _brows]
            _w_vals = [r['windup']   for r in _brows]
            _o_vals = [r['osb']      for r in _brows]
            _corr_elev  = _corr(_b_vals, _e_vals)
            _corr_wu    = _corr(_b_vals, _w_vals)
            _corr_osb   = _corr(_b_vals, _o_vals)
            print(f"\n  [5] CORRELATION: birth_mm vs elevation  : r={_corr_elev:+.3f}")
            print(f"  [6] CORRELATION: birth_mm vs windup_mm  : r={_corr_wu:+.3f}")
            print(f"  [7] CORRELATION: birth_mm vs osb_if_mm  : r={_corr_osb:+.3f}")

            # PHASE-5: interpretation
            print(f"\n  [INTERPRETATION]")
            if not math.isnan(_corr_elev) and abs(_corr_elev) > 0.30:
                print(f"    *** birth bias correlates with elevation (r={_corr_elev:+.3f})")
                print(f"        → SUSPECT: troposphere/APC geometry coupling")
            if not math.isnan(_corr_wu) and abs(_corr_wu) > 0.30:
                print(f"    *** birth bias correlates with windup (r={_corr_wu:+.3f})")
                print(f"        → SUSPECT: wind-up convention/sign issue")
            if not math.isnan(_corr_osb) and abs(_corr_osb) > 0.30:
                print(f"    *** birth bias correlates with OSB (r={_corr_osb:+.3f})")
                print(f"        → SUSPECT: phase OSB sign/mapping inconsistency")
            if _g_b and _e_b:
                _gal_gps_ratio = (sum(_e_b)/len(_e_b)) / max(sum(_g_b)/len(_g_b), 1.)
                if _gal_gps_ratio > 1.5:
                    print(f"    *** Galileo mean |birth| is {_gal_gps_ratio:.1f}× larger than GPS")
                    print(f"        → SUSPECT: ISB or Galileo datum inconsistency")
            _top_sats = [sv for sv, bv in _sat_sorted[:5]
                         if sum(bv)/len(bv) > 2.0 * (sum(_abs_b)/len(_abs_b))]
            if _top_sats:
                print(f"    *** Dominated by specific sats: {_top_sats}")
                print(f"        → SUSPECT: APC/PCV or observable-selection issue")
            print(f"{'='*72}\n")
        else:
            print(f"\n[STARTUP-BIRTH-SUMMARY] No birth rows found in {_birth_trace_path}")
    except Exception as _e4:
        print(f"[STARTUP-BIRTH-SUMMARY] Analysis failed: {_e4}")
    # ─────────────────────────────────────────────────────────────────────────
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

    # ── PHASE-6: AUDIT SUMMARY ────────────────────────────────────────────────
    # Pure post-hoc analysis of innovation_audit1.csv — no numerical changes.
    try:
        import csv as _csv_a
        _ia_rows = []
        with open(_innov_audit_path, 'r') as _ia_fh:
            _ia_rd = _csv_a.DictReader(_ia_fh)
            for _iar in _ia_rd:
                try:
                    _ia_rows.append({
                        'sat':   _iar['sat'],
                        'const': _iar['const'],
                        'el':    float(_iar['elevation_deg']),
                        'acc':   int(_iar['accepted']),
                        'rej':   _iar['reject_reason'],
                        'nu':    float(_iar['innovation_mm'])   if _iar['innovation_mm'] not in ('nan','') else float('nan'),
                        'NIS':   float(_iar['normalized_innovation_squared']) if _iar['normalized_innovation_squared'] not in ('nan','') else float('nan'),
                        'is_nb': int(_iar['is_newborn']),
                    })
                except (ValueError, KeyError):
                    continue

        def _a_mean(v): return sum(v)/len(v) if v else float('nan')
        def _a_rms(v):  return math.sqrt(sum(x*x for x in v)/len(v)) if v else float('nan')

        _acc_rows  = [r for r in _ia_rows if r['acc'] == 1]
        _rej_rows  = [r for r in _ia_rows if r['acc'] == 0 and r['rej'] not in ('newborn','nan_residual')]
        _all_NIS   = [r['NIS'] for r in _acc_rows if math.isfinite(r['NIS'])]
        _rej_NIS   = [r['NIS'] for r in _rej_rows if math.isfinite(r['NIS'])]
        _nb_rows   = [r for r in _ia_rows if r['is_nb'] == 0 and r['acc'] == 1]
        _nb_NIS    = [r['NIS'] for r in _nb_rows if math.isfinite(r['NIS'])]
        _gps_NIS   = [r['NIS'] for r in _acc_rows if r['const']=='G' and math.isfinite(r['NIS'])]
        _gal_NIS   = [r['NIS'] for r in _acc_rows if r['const']=='E' and math.isfinite(r['NIS'])]

        # rejection fractions by sat
        _sat_acc = {}; _sat_rej = {}; _sat_resets = {}
        for _r in _ia_rows:
            s = _r['sat']
            if _r['rej'] in ('newborn',): continue
            _sat_acc.setdefault(s, 0); _sat_rej.setdefault(s, 0)
            if _r['acc'] == 1: _sat_acc[s] += 1
            else:               _sat_rej[s] += 1

        # zero-update ambiguities: never accepted
        _zero_upd = [s for s in _sat_rej if _sat_acc.get(s, 0) == 0 and _sat_rej.get(s, 0) > 0]

        # top rejection fraction sats
        _rej_frac_by_sat = {}
        for s in set(list(_sat_acc.keys()) + list(_sat_rej.keys())):
            _tot = _sat_acc.get(s,0) + _sat_rej.get(s,0)
            _rej_frac_by_sat[s] = _sat_rej.get(s,0) / _tot if _tot > 0 else 0.

        _top_rej_sats = sorted(_rej_frac_by_sat.items(), key=lambda kv: kv[1], reverse=True)[:5]

        # NIS vs elevation correlation
        _nis_el_pairs = [(r['NIS'], r['el']) for r in _acc_rows
                         if math.isfinite(r['NIS']) and math.isfinite(r['el'])]
        def _corr2(pairs):
            if len(pairs) < 3: return float('nan')
            xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
            mx=sum(xs)/len(xs); my=sum(ys)/len(ys)
            num=sum((a-mx)*(b-my) for a,b in zip(xs,ys))
            den=math.sqrt(sum((a-mx)**2 for a in xs)*sum((b-my)**2 for b in ys))
            return num/den if den > 0 else float('nan')
        _NIS_el_corr = _corr2(_nis_el_pairs)

        # reset counts from reset_audit1.csv
        _reset_sat_counts = {}
        try:
            with open(_reset_audit_path, 'r') as _ra_fh:
                for _rarow in _csv_a.DictReader(_ra_fh):
                    s = _rarow.get('sat','')
                    _reset_sat_counts[s] = _reset_sat_counts.get(s,0) + 1
        except Exception:
            pass
        _top_reset_sats = sorted(_reset_sat_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]

        # sigma > 10 m after 1 h: from ambiguity participation audit
        _zombie_sats = set()
        try:
            _HOUR_EP = 120   # 120 × 30s = 1 hour
            with open(_amb_part_path, 'r') as _ap_fh:
                for _aprow in _csv_a.DictReader(_ap_fh):
                    try:
                        if (int(_aprow['alive_epochs']) >= _HOUR_EP
                                and float(_aprow['sigma_mm']) > 10000.):
                            _zombie_sats.add(_aprow['sat'])
                    except (ValueError, KeyError):
                        pass
        except Exception:
            pass

        print(f"\n{'='*72}")
        print(f"[AUDIT SUMMARY]")
        print(f"  1. accepted / rejected phase rows : {len(_acc_rows)} / {len(_rej_rows)}")
        print(f"     (excluding newborn rows; hard-gate rejections only)")
        print(f"  2. mean NIS of accepted rows      : {_a_mean(_all_NIS):.4f}"
              f"  (expected ~1.0 if consistent)")
        print(f"  3. mean NIS of rejected rows      : {_a_mean(_rej_NIS):.4f}"
              f"  (expected >> 1.0 for hard-gated outliers)")
        print(f"  4. top satellites by rejection fraction:")
        for _rs, _rf in _top_rej_sats:
            _tot = _sat_acc.get(_rs,0) + _sat_rej.get(_rs,0)
            print(f"       {_rs:5s}  {_rf*100:.1f}%  ({_sat_rej.get(_rs,0)}/{_tot})")
        print(f"  5. top satellites by reset count:")
        for _rs, _rc in _top_reset_sats:
            print(f"       {_rs:5s}  {_rc} resets")
        print(f"  6. ambiguity states with ZERO accepted updates : {len(_zero_upd)}")
        if _zero_upd:
            print(f"       {sorted(_zero_upd)}")
        print(f"  7. ambiguity states sigma > 10m after 1h      : {len(_zombie_sats)}")
        if _zombie_sats:
            print(f"       {sorted(_zombie_sats)}")
        print(f"  8. mean NIS for non-newborn accepted rows     : {_a_mean(_nb_NIS):.4f}")
        print(f"  9. GPS mean NIS  : {_a_mean(_gps_NIS):.4f}   "
              f"Galileo mean NIS : {_a_mean(_gal_NIS):.4f}")
        print(f" 10. correlation: NIS vs elevation (accepted)  : r={_NIS_el_corr:+.4f}")

        # FIX-2 (v64): AUDIT-NIS-SUMMARY — dedicated NIS diagnostic block using
        # nan-aware statistics and scientific-notation formatting.
        def _nis_stats(vals):
            finite = [v for v in vals if math.isfinite(v)]
            if not finite:
                return float('nan'), float('nan'), float('nan'), 0
            arr = np.array(finite)
            return float(np.mean(arr)), float(np.median(arr)), float(np.std(arr)), len(arr)
        _acc_mn, _acc_med, _acc_std, _acc_n = _nis_stats(_all_NIS)
        _rej_mn, _rej_med, _rej_std, _rej_n = _nis_stats(_rej_NIS)
        _gps_mn, _gps_med, _gps_std, _gps_n2 = _nis_stats(_gps_NIS)
        _gal_mn, _gal_med, _gal_std, _gal_n2 = _nis_stats(_gal_NIS)
        print(f"\n[AUDIT-NIS-SUMMARY]  (NIS=ν²/S; expected~1.0 at consistency; DIAGNOSTIC ONLY)")
        print(f"  accepted mean NIS  : {_acc_mn:.8e}   median={_acc_med:.4e}  std={_acc_std:.4e}  n={_acc_n}")
        print(f"  rejected mean NIS  : {_rej_mn:.8e}   median={_rej_med:.4e}  std={_rej_std:.4e}  n={_rej_n}")
        print(f"  GPS mean NIS       : {_gps_mn:.8e}   median={_gps_med:.4e}  std={_gps_std:.4e}  n={_gps_n2}")
        print(f"  Galileo mean NIS   : {_gal_mn:.8e}   median={_gal_med:.4e}  std={_gal_std:.4e}  n={_gal_n2}")
        print(f"  NOTE: NIS is DIAGNOSTIC ONLY — not used operationally")

        print(f"\n  [INTERPRETATION]")
        _gps_n = _a_mean(_gps_NIS); _gal_n = _a_mean(_gal_NIS)
        if math.isfinite(_a_mean(_all_NIS)) and _a_mean(_all_NIS) > 5.0:
            print(f"  *** Mean accepted NIS={_a_mean(_all_NIS):.2f} >> 1 → gate is statistically INVALID")
            print(f"      Current covariance P under-represents actual innovation spread.")
        if _zero_upd:
            print(f"  *** {len(_zero_upd)} ambiguities have H rows but ZERO accepted updates")
            print(f"      → REJECTION STARVATION is likely dominant")
        if _zombie_sats:
            print(f"  *** {len(_zombie_sats)} ambiguities have sigma>10m after 1h")
            print(f"      → Check participation audit for disconnected states")
        if math.isfinite(_gps_n) and math.isfinite(_gal_n) and _gal_n > 2.0 * _gps_n:
            print(f"  *** Galileo NIS ({_gal_n:.2f}) >> GPS NIS ({_gps_n:.2f})")
            print(f"      → ISB/datum/model mismatch still present for Galileo")
        if math.isfinite(_NIS_el_corr) and abs(_NIS_el_corr) > 0.20:
            print(f"  *** NIS-elevation correlation r={_NIS_el_corr:+.4f} suggests")
            print(f"      elevation-dependent model error (troposphere / APC?)")
        print(f"{'='*72}\n")

    except Exception as _e6:
        print(f"[AUDIT SUMMARY] Analysis failed: {_e6}")
        import traceback; traceback.print_exc()
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

    # ── PER-CONSTELLATION CSV EXPORTS ─────────────────────────────────────────
    _export_constellation_csvs(all_fwd, all_rts, REF,
                               os.path.dirname(os.path.abspath(outfile)) if outfile
                               else os.path.dirname(os.path.abspath(infiles[0] if infiles else '.')))

    _plot_comparison(all_fwd,all_rts,REF)
    _plot_enu_per_constellation(all_fwd, all_rts, REF)
    _plot_fwd_vs_rts_per_constellation(all_fwd, all_rts, REF)
    _plot_enu_panel_2x2(all_fwd, all_rts, REF)
    _plot_fwd_vs_rts_panel_1x2(all_fwd, all_rts, REF)
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
    fig.suptitle('PPP-AR Multi-Constellation Comparison (v64)',
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
    plot_path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'ppp_comparison_v59.png')
    try:
        fig.savefig(plot_path,dpi=150,bbox_inches='tight')
        print(f"[PLOT] Saved: {plot_path}")
    except Exception as e:
        print(f"[PLOT] Could not save: {e}")
    plt.close(fig)


# ==============================================================================
#  NEW — Per-constellation ENU plots (FWD and RTS)
# ==============================================================================
def _plot_enu_per_constellation(all_fwd, all_rts, REF):
    """Create separate ENU plots for GPS-only and Galileo-only, FWD and RTS.

    Output files:
        enu_gps_fwd.png   enu_gal_fwd.png
        enu_gps_rts.png   enu_gal_rts.png

    Style mirrors the existing GPS+Galileo ENU panel in _plot_comparison:
      - East/North/Up in mm
      - 24-hour x-axis (0–24 h)
      - zero reference line
      - ylim ±300 mm
    """
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("[PLOT] matplotlib not available — skipping per-constellation ENU"); return

    outdir = os.path.dirname(os.path.abspath(__file__))

    # Map (label_key, result_dict, pass_tag) → filename
    cases = [
        ('GPS-only',     all_fwd.get('GPS-only',     {}), 'FWD', 'enu_gps_fwd.png'),
        ('Galileo-only', all_fwd.get('Galileo-only', {}), 'FWD', 'enu_gal_fwd.png'),
        ('GPS-only',     all_rts.get('GPS-only',     {}), 'RTS', 'enu_gps_rts.png'),
        ('Galileo-only', all_rts.get('Galileo-only', {}), 'RTS', 'enu_gal_rts.png'),
    ]

    saved_files = []
    for label, results, pass_tag, fname in cases:
        m = _compute_metrics(results, REF)
        fig, ax = plt.subplots(figsize=(12, 4))
        title = f'ENU — {label} {pass_tag}'
        fig.suptitle(title, fontsize=13, fontweight='bold')

        if m is not None and len(m['sods']) > 0:
            sh = m['sods'] / 3600.
            ax.plot(sh, m['e_mm'], color='#e6194b', linewidth=0.8, label='East')
            ax.plot(sh, m['n_mm'], color='#3cb44b', linewidth=0.8, label='North')
            ax.plot(sh, m['u_mm'], color='#4363d8', linewidth=0.8, label='Up')

        ax.axhline(0, color='black', linewidth=0.5)
        ax.set_xlabel('Time (h)')
        ax.set_ylabel('Error (mm)')
        ax.set_xlim(0, 24)
        ax.set_ylim(-300, 300)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        fpath = os.path.join(outdir, fname)
        try:
            fig.savefig(fpath, dpi=150, bbox_inches='tight')
            saved_files.append(fname)
        except Exception as e:
            print(f"[PLOT] Could not save {fname}: {e}")
        plt.close(fig)

    if saved_files:
        print(f"[PLOT-SAVED] {' '.join(saved_files)}")


# ==============================================================================
#  NEW — Constellation-specific FWD vs RTS comparison plots
# ==============================================================================
def _plot_fwd_vs_rts_per_constellation(all_fwd, all_rts, REF):
    """Create FWD vs RTS 3D-error comparison plots for GPS-only and Galileo-only.

    Output files:
        fwd_vs_rts_gps.png
        fwd_vs_rts_gal.png

    Style mirrors panel (d) of _plot_comparison:
      - 3D error in mm
      - FWD (blue) and RTS (orange) on same axes
      - 5 cm reference line
      - ylim 0–300 mm
      - 24-hour x-axis
    """
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("[PLOT] matplotlib not available — skipping per-constellation FWD/RTS"); return

    outdir = os.path.dirname(os.path.abspath(__file__))

    cases = [
        ('GPS-only',     'fwd_vs_rts_gps.png'),
        ('Galileo-only', 'fwd_vs_rts_gal.png'),
    ]

    saved_files = []
    for label, fname in cases:
        mf = _compute_metrics(all_fwd.get(label, {}), REF)
        mr = _compute_metrics(all_rts.get(label, {}), REF)

        fig, ax = plt.subplots(figsize=(12, 4))
        fig.suptitle(f'FWD vs RTS — {label}', fontsize=13, fontweight='bold')

        if mf is not None and len(mf['sods']) > 0:
            ax.plot(mf['sods'] / 3600., mf['d3_mm'],
                    color='#4363d8', linewidth=0.8, alpha=0.8, label='FWD')
        if mr is not None and len(mr['sods']) > 0:
            ax.plot(mr['sods'] / 3600., mr['d3_mm'],
                    color='#f58231', linewidth=0.8, alpha=0.8, label='RTS')

        ax.axhline(50, color='gray', linestyle='--', linewidth=0.7, label='5 cm')
        ax.set_xlabel('Time (h)')
        ax.set_ylabel('3D Error (mm)')
        ax.set_xlim(0, 24)
        ax.set_ylim(0, 300)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        fpath = os.path.join(outdir, fname)
        try:
            fig.savefig(fpath, dpi=150, bbox_inches='tight')
            saved_files.append(fname)
        except Exception as e:
            print(f"[PLOT] Could not save {fname}: {e}")
        plt.close(fig)

    if saved_files:
        print(f"[PLOT-SAVED] {' '.join(saved_files)}")


# ==============================================================================
#  PANEL — ENU comparison 2×2 (GPS FWD | GPS RTS / Galileo FWD | Galileo RTS)
# ==============================================================================
def _plot_enu_panel_2x2(all_fwd, all_rts, REF):
    """Publication-style 2×2 ENU panel.

    Layout
    ------
    Top-left  : GPS-only FWD      Top-right  : GPS-only RTS
    Bottom-left: Galileo-only FWD  Bottom-right: Galileo-only RTS

    Saved as: enu_panel_2x2.png
    """
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("[PLOT-PANEL] matplotlib not available — skipping enu_panel_2x2"); return

    # Pre-compute all four metrics objects so axes limits are consistent.
    metrics = {
        ('GPS-only',     'FWD'): _compute_metrics(all_fwd.get('GPS-only',     {}), REF),
        ('GPS-only',     'RTS'): _compute_metrics(all_rts.get('GPS-only',     {}), REF),
        ('Galileo-only', 'FWD'): _compute_metrics(all_fwd.get('Galileo-only', {}), REF),
        ('Galileo-only', 'RTS'): _compute_metrics(all_rts.get('Galileo-only', {}), REF),
    }

    fig, axes = plt.subplots(
        2, 2,
        figsize=(16, 10),
        sharex=True, sharey=True,
    )
    fig.suptitle('ENU Positioning Error — GPS-only vs Galileo-only  (FWD / RTS)',
                 fontsize=14, fontweight='bold')

    panel_cfg = [
        (0, 0, 'GPS-only',     'FWD', 'GPS-only FWD'),
        (0, 1, 'GPS-only',     'RTS', 'GPS-only RTS'),
        (1, 0, 'Galileo-only', 'FWD', 'Galileo-only FWD'),
        (1, 1, 'Galileo-only', 'RTS', 'Galileo-only RTS'),
    ]

    for row, col, label, pass_tag, title in panel_cfg:
        ax = axes[row, col]
        m  = metrics[(label, pass_tag)]

        if m is not None and len(m['sods']) > 0:
            sh = m['sods'] / 3600.
            ax.plot(sh, m['e_mm'], color='#e6194b', linewidth=0.8, label='East')
            ax.plot(sh, m['n_mm'], color='#3cb44b', linewidth=0.8, label='North')
            ax.plot(sh, m['u_mm'], color='#4363d8', linewidth=0.8, label='Up')

        ax.axhline(0, color='black', linewidth=0.5)
        ax.set_xlim(0, 24)
        ax.set_ylim(-350, 350)
        ax.set_title(title, fontsize=11)
        ax.legend(fontsize=9, loc='upper right')
        ax.grid(True, alpha=0.3)

        # Axis labels: left column → y-label; bottom row → x-label
        if col == 0:
            ax.set_ylabel('Error (mm)', fontsize=10)
        if row == 1:
            ax.set_xlabel('Time (h)', fontsize=10)

    plt.tight_layout()

    outdir  = os.path.dirname(os.path.abspath(__file__))
    outpath = os.path.join(outdir, 'enu_panel_2x2.png')
    try:
        fig.savefig(outpath, dpi=150, bbox_inches='tight')
        print(f"[PLOT-PANEL] wrote enu_panel_2x2.png")
    except Exception as e:
        print(f"[PLOT-PANEL] Could not save enu_panel_2x2.png: {e}")
    plt.close(fig)


# ==============================================================================
#  PANEL — FWD vs RTS comparison 1×2 (GPS-only | Galileo-only)
# ==============================================================================
def _plot_fwd_vs_rts_panel_1x2(all_fwd, all_rts, REF):
    """Publication-style 1×2 FWD-vs-RTS 3D-error panel.

    Layout
    ------
    Left : GPS-only     (FWD blue / RTS orange)
    Right: Galileo-only (FWD blue / RTS orange)

    Saved as: fwd_vs_rts_panel_1x2.png
    """
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("[PLOT-PANEL] matplotlib not available — skipping fwd_vs_rts_panel_1x2"); return

    # Pre-compute metrics for both constellations, both passes.
    metrics_fwd = {
        'GPS-only':     _compute_metrics(all_fwd.get('GPS-only',     {}), REF),
        'Galileo-only': _compute_metrics(all_fwd.get('Galileo-only', {}), REF),
    }
    metrics_rts = {
        'GPS-only':     _compute_metrics(all_rts.get('GPS-only',     {}), REF),
        'Galileo-only': _compute_metrics(all_rts.get('Galileo-only', {}), REF),
    }

    fig, axes = plt.subplots(
        1, 2,
        figsize=(16, 6),
        sharex=True, sharey=True,
    )
    fig.suptitle('FWD vs RTS 3D Positioning Error — GPS-only and Galileo-only',
                 fontsize=14, fontweight='bold')

    panel_cfg = [
        (0, 'GPS-only',     'GPS-only'),
        (1, 'Galileo-only', 'Galileo-only'),
    ]

    for col, label, title in panel_cfg:
        ax  = axes[col]
        mf  = metrics_fwd[label]
        mr  = metrics_rts[label]

        if mf is not None and len(mf['sods']) > 0:
            ax.plot(mf['sods'] / 3600., mf['d3_mm'],
                    color='#4363d8', linewidth=0.8, alpha=0.8, label='FWD')
        if mr is not None and len(mr['sods']) > 0:
            ax.plot(mr['sods'] / 3600., mr['d3_mm'],
                    color='#f58231', linewidth=0.8, alpha=0.8, label='RTS')

        ax.axhline(50, color='gray', linestyle='--', linewidth=0.7, label='5 cm')
        ax.set_xlim(0, 24)
        ax.set_ylim(0, 320)
        ax.set_title(title, fontsize=11)
        ax.set_xlabel('Time (h)', fontsize=10)
        ax.legend(fontsize=9, loc='upper right')
        ax.grid(True, alpha=0.3)

        if col == 0:
            ax.set_ylabel('3D Error (mm)', fontsize=10)

    plt.tight_layout()

    outdir  = os.path.dirname(os.path.abspath(__file__))
    outpath = os.path.join(outdir, 'fwd_vs_rts_panel_1x2.png')
    try:
        fig.savefig(outpath, dpi=150, bbox_inches='tight')
        print(f"[PLOT-PANEL] wrote fwd_vs_rts_panel_1x2.png")
    except Exception as e:
        print(f"[PLOT-PANEL] Could not save fwd_vs_rts_panel_1x2.png: {e}")
    plt.close(fig)


# ==============================================================================
#  NEW — Per-constellation CSV exports (FWD + RTS)
# ==============================================================================
_CSV_HEADER = (
    "SOD,pass,"
    "Computed_X,Computed_Y,Computed_Z,"
    "REF_X,REF_Y,REF_Z,"
    "DiffX_mm,DiffY_mm,DiffZ_mm,"
    "dE_mm,dN_mm,dU_mm,"
    "3D_mm,"
    "N,WL_fixed,NL_fixed,"
    "ZHD_m,ZWD_m,ZTD_m,CodeRMS_mm,PhsRMS_mm\n"
)


def _write_results_csv(filepath, results_dict, REF, pass_tag):
    """Write a results dict (fwd or rts) to CSV with the canonical column layout.

    Parameters
    ----------
    filepath   : str  — destination path
    results_dict : dict  — {sod: result_record} as returned by _ppp_pass / _rts_smooth
    REF        : np.ndarray shape (3,) — ECEF reference position (m)
    pass_tag   : str  — value written in the 'pass' column (e.g. 'FWD' or 'RTS')
    """
    lr, lo, _ = _lla(REF)
    Re = _enu(lr, lo)
    with open(filepath, 'w') as fo:
        fo.write(_CSV_HEADER)
        for sod, r in sorted(results_dict.items()):
            xyz = r['xyz']
            dx  = r['dx']
            dx_mm  = dx * 1e3
            enu_mm = Re @ dx * 1e3
            fo.write(
                f"{sod:.1f},{pass_tag},"
                f"{xyz[0]:.4f},{xyz[1]:.4f},{xyz[2]:.4f},"
                f"{REF[0]:.4f},{REF[1]:.4f},{REF[2]:.4f},"
                f"{dx_mm[0]:+.3f},{dx_mm[1]:+.3f},{dx_mm[2]:+.3f},"
                f"{enu_mm[0]:+.3f},{enu_mm[1]:+.3f},{enu_mm[2]:+.3f},"
                f"{np.linalg.norm(dx_mm):.3f},"
                f"{r['n']},{r.get('wl_fixed',0)},{r.get('nl_fixed',0)},"
                f"{r.get('zhd',0):.4f},{r.get('zwd',0):.4f},{r['ztd']:.4f},"
                f"{r.get('code_rms',0):.2f},{r.get('phase_rms',0):.3f}\n"
            )
    print(f"[CSV]  Written: {filepath}")


def _export_constellation_csvs(all_fwd, all_rts, REF, outdir):
    """Export six per-constellation CSV files (FWD + RTS for GPS, Galileo, Combined).

    Files created in outdir:
        ppp_results_gps_fwd.csv     ppp_results_gps_rts.csv
        ppp_results_gal_fwd.csv     ppp_results_gal_rts.csv
        ppp_results_combined_fwd.csv  ppp_results_combined_rts.csv

    RTS CSVs use RTS-smoothed XYZ/dx values already stored in all_rts[label].
    The reference origin (REF) is identical between FWD and RTS for each label.
    """
    mapping = [
        # (label_key,          fwd_file,                    rts_file)
        ('GPS-only',     'ppp_results_gps_fwd.csv',      'ppp_results_gps_rts.csv'),
        ('Galileo-only', 'ppp_results_gal_fwd.csv',      'ppp_results_gal_rts.csv'),
        ('GPS+Galileo',  'ppp_results_combined_fwd.csv', 'ppp_results_combined_rts.csv'),
    ]

    row_counts = {}
    for label, fwd_fname, rts_fname in mapping:
        fwd_results = all_fwd.get(label, {})
        rts_results = all_rts.get(label, {})

        fwd_path = os.path.join(outdir, fwd_fname)
        rts_path = os.path.join(outdir, rts_fname)

        if fwd_results:
            _write_results_csv(fwd_path, fwd_results, REF, 'FWD')
        else:
            print(f"[CSV]  Skipped {fwd_fname} — no FWD results for '{label}'")

        if rts_results:
            _write_results_csv(rts_path, rts_results, REF, 'RTS')
        else:
            print(f"[CSV]  Skipped {rts_fname} — no RTS results for '{label}'")

        short = label.lower().replace('+', '').replace('-only', '')
        row_counts[short + '_fwd'] = len(fwd_results)
        row_counts[short + '_rts'] = len(rts_results)

    # ── Validation print ──────────────────────────────────────────────────────
    print(
        f"[RTS-EXPORT] "
        f"gps_fwd_rows={row_counts.get('gps_fwd',0)} "
        f"gps_rts_rows={row_counts.get('gps_rts',0)} "
        f"gal_fwd_rows={row_counts.get('galileo_fwd',0)} "
        f"gal_rts_rows={row_counts.get('galileo_rts',0)} "
        f"combined_fwd_rows={row_counts.get('gpsgalileo_fwd',0)} "
        f"combined_rts_rows={row_counts.get('gpsgalileo_rts',0)}"
    )
    # Confirm all six plot files will be saved
    print(
        f"[PLOT-SAVED] "
        f"enu_gps_fwd.png enu_gal_fwd.png "
        f"enu_gps_rts.png enu_gal_rts.png "
        f"fwd_vs_rts_gps.png fwd_vs_rts_gal.png"
    )


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
            INFILES,os.path.join(DATA,'ppp_results21.csv'))
"""
ppp.py  v51 — fixed
====================
BUGS FIXED in this version vs v50:

  5. AMBIGUITY INIT AGAINST UNCONVERGED POSITION (SPIKE ROOT CAUSE) —
     When a satellite rises or returns after a gap, x[ki] = LIFc - rp0 absorbs
     the current position error (can be 100–300 mm early in the session).
     That biased ambiguity then pulls the position toward it on subsequent
     epochs, causing hard spikes. The NL_PTRACE_THRESH guard only blocked
     NL *fixing*, not ambiguity *initialization*.
     Fix: inflate P[ki,ki] to 300² when p_trace > 0.05 m² at init time, so
     the KF treats the new ambiguity as uncertain and lets it float freely.
     Additionally, apply a 5-epoch phase sigma inflation (age<=5, not 3)
     for satellites initialized during unconverged periods.

  6. NL GATE PERMANENTLY BLACKLISTS GOOD SATELLITES — After NL_GATE_RELEASE=5
     consecutive gated epochs, the satellite was added to _nl_bad_nwl forever.
     If the gate fired due to a transient position spike (not a wrong integer),
     a correct satellite was permanently excluded, reducing NL count exactly
     at the spikes, which then removed the constraint and let position drift —
     a positive feedback loop.
     Fix: Replace permanent _nl_bad_nwl.add() with a strike counter. Only
     permanently blacklist after 3 separate gate-release events. After the
     first two events, allow retry after a 60-epoch cooldown.

  7. GALILEO L5 OSB KEY MISMATCH — The code always fetched ob.get('L5Q', 0.)
     for Galileo E5a phase OSB. COD MGEX OSB files may use 'L5X' or 'L5I'
     instead. A silent 0.0 fallback leaves a 10–30 mm OSB uncorrected on the
     E5a carrier, biasing the IF combination and corrupting NL fractional parts
     for all Galileo satellites.
     Fix: ob.get('L5Q', ob.get('L5X', ob.get('L5I', 0.))) at all three call
     sites: _proc_gal MW/LIF computation, _nl_float_gal, NL pseudo-obs.

  8. GALILEO b_rec NEVER FREEZING — The adaptive min_n and std exclusion
     thresholds were tuned for GPS MW noise (λ_WL ≈ 0.862 m). Galileo MW
     noise is ~15% higher (λ_WL_E ≈ 0.751 m), causing more sats to exceed
     the std>0.45 exclusion, making len(all_fracs)>=5 unreachable.
     b_rec_frozen['E'] never forms → wl_fixed never populated for Galileo.
     Fix: per-constellation min_n and exclusion thresholds. Galileo uses
     std_excl=0.55 and requires only 3 sats with agreement (vs 5 for GPS).
     Also add a min-3 fallback path with a relaxed agreement tolerance.

  9. GALILEO ATX PCO/PCV USES GPS IF COEFFICIENTS — parse_atx() always used
     ALFA/BETA (GPS L1/L2) and frequency key 'G02' for all satellites
     including Galileo. Galileo E-sats need ALFA_E/BETA_E and key 'G08'
     (E5a) for the IF PCO/PCV combination.
     Fix: detect Galileo PRNs (cprn.startswith('E')) and use the correct
     constellation-specific coefficients and frequency key.

  10. RTS BACKWARD PASS SMOOTHS THROUGH AMBIGUITY RESETS — When an ambiguity
      resets at forward epoch k (slip, new sat), the backward smoother pulls
      the smoothed state at k toward the future smoothed state k+1 — which
      was estimated under a different ambiguity geometry. This causes RTS
      spikes that are sometimes worse than FWD spikes.
      Fix: store an epoch_amb_change flag in _rts_store._data. In the backward
      pass, zero the gain G_k for ambiguity-state dimensions at any epoch where
      the ambiguity set changed, preventing the smoother from mixing states
      across an ambiguity boundary.

RETAINED FROM v50 (all correct):
  * WL persist bug fixed (arc reset vs same-arc noise, threshold 3 cyc)
  * Full LAMBDA ILS via lambda_ils.py
  * Per-satellite NL denominator (GPS vs Galileo)
  * OTL: parse_blq() + _otl_disp() IERS 2010
  * GF-primary cycle-slip; MW threshold 1.5 cyc
  * b_rec frozen per-constellation
  * Phase re-init delay for first epochs
  * NL bad-frac exclusion
  * GPS + Galileo multi-constellation
  * Phase-OSB IF combination ✓
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

def _ifc(a, b):   return ALFA*a - BETA*b
def _sig(el, s0): return s0 / max(math.sin(el), 0.1)


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
                        # FIX 9: Use constellation-specific IF coefficients.
                        # Galileo E-sats: ALFA_E/BETA_E, freq key 'G08' (E5a).
                        # GPS G-sats:     ALFA/BETA,   freq key 'G02' (L2).
                        is_gal = cprn.startswith('E')
                        _a = ALFA_E if is_gal else ALFA
                        _b = BETA_E if is_gal else BETA
                        _f2key = 'G08' if is_gal else 'G02'
                        p1=np.array(pf.get('G01',[0,0,0]),float)
                        p2=np.array(pf.get(_f2key,[0,0,0]),float)
                        v1=pv.get('G01',[]); v2=pv.get(_f2key,[])
                        vi=([_a*a-_b*b for a,b in zip(v1,v2)] if v1 and v2 and len(v1)==len(v2)
                            else list(v1) if v1 else list(v2))
                        sat_atx[cprn].append({'vf':vf if vf else -1e18,'vu':vu if vu else 1e18,
                            'pco':_a*p1-_b*p2,'pcv':vi,'z1':z1,'dz':dz})
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
    """Compute IERS 2010 fundamental astronomical arguments for OTL."""
    jd = 2444244.5 + (tow_total - 18.0) / 86400.0
    t  = (jd - 2451545.0) / 36525.0

    gmst_s = (67310.54841
              + (876600.0*3600.0 + 8640184.812866)*t
              + 0.093104*t*t
              - 6.2e-6*t*t*t)
    gmst = math.fmod(gmst_s * 2.0*math.pi / 86400.0, 2.0*math.pi)
    if gmst < 0.0: gmst += 2.0*math.pi

    _d2r = math.pi / 180.0
    _a2r = _d2r / 3600.0

    l  = math.fmod(134.96402779*_d2r + 1717915923.2178*_a2r*t, 2.0*math.pi)
    lp = math.fmod(357.52910918*_d2r +  129596581.0481*_a2r*t, 2.0*math.pi)
    F  = math.fmod( 93.27209062*_d2r + 1739527262.8478*_a2r*t, 2.0*math.pi)
    D  = math.fmod(297.85019547*_d2r + 1602961601.2090*_a2r*t, 2.0*math.pi)
    Om = math.fmod(125.04455501*_d2r +   -6962890.5431*_a2r*t, 2.0*math.pi)

    return gmst, l, lp, F, D, Om


def _otl_disp(blq, sta, tow_total, lat, lon):
    """Compute ocean tide loading displacement in ECEF (metres)."""
    key = sta.strip().upper()[:4]
    if key not in blq:
        return np.zeros(3)

    amp = blq[key]['amp']
    phs = blq[key]['phs']

    gmst, l, lp, F, D, Om = _ast_args_otl(tow_total)

    _pi = math.pi
    _pi2 = 2.0*_pi
    s   = F + Om
    h   = F + Om - D
    p   = F + Om - l
    tau = gmst + _pi - s

    chi = np.array([
        2.*tau,
        2.*tau + 2.*s - 2.*h,
        2.*tau - s + p,
        2.*tau + 2.*s,
        tau + s,
        tau - s,
        tau + s - 2.*h,
        tau - 2.*s + p,
        2.*s,
        s - p,
        2.*h,
    ]) % _pi2

    _d2r = _pi / 180.0
    dR  = sum(amp[0,i]*math.cos(chi[i] - phs[0,i]*_d2r) for i in range(11))
    dW  = sum(amp[1,i]*math.cos(chi[i] - phs[1,i]*_d2r) for i in range(11))
    dS  = sum(amp[2,i]*math.cos(chi[i] - phs[2,i]*_d2r) for i in range(11))

    dE, dN, dU = -dW, -dS, dR

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
    N=len(data); dim=5
    sods=[d[0] for d in data]
    xs=[d[1][:dim].copy() for d in data]
    Ps=[d[2][:dim,:dim].copy() for d in data]
    # FIX 10: track epochs where ambiguity set changed (slip/new sat).
    # At these boundaries zero the position-state backward gain to prevent
    # the smoother mixing states estimated under different ambiguity geometries.
    amb_changed=[d[3] if len(d)>3 else False for d in data]
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
        # If the ambiguity set changed at k+1, zero position gains to avoid
        # backward contamination across the ambiguity boundary
        if amb_changed[k+1]:
            G_k[:3,:]=0.
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
    """Full LAMBDA ILS (Teunissen 1995, Chang 2005)."""
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
    # osb_bl5 should already be resolved via L5Q/L5X/L5I fallback at call site
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
    LIF=_ifc(L1*LAMBDA1,L2*LAMBDA2)

    bl1=ob.get('L1C',ob.get('L1W',0.))
    bl2=ob.get('L2L',ob.get('L2W',ob.get('L2C',0.)))
    b_wl_sat_cyc=((FREQ1*bl1-FREQ2*bl2)/(FREQ1-FREQ2))/LAMBDA_WL
    MW_cyc=_mw_cyc(P1-b1,P2-b2,L1,L2)-b_wl_sat_cyc
    GF_m=_gf_m(L1,L2)

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
                PIF=PIF,LIF=LIF,MW_cyc=MW_cyc,GF_m=GF_m,
                L1=L1,L2=L2,P1=P1,P2=P2,
                sat_xyz=sva,rec_apc=ra)

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
    LIF=ALFA_E*(L1*LAMBDA_E1)-BETA_E*(L5*LAMBDA_E5A)

    # FIX 7: fallback chain for E5a phase OSB — COD files may use L5Q, L5X, or L5I
    bl1=ob.get('L1C',0.)
    bl5=ob.get('L5Q',ob.get('L5X',ob.get('L5I',0.)))
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
                PIF=PIF,LIF=LIF,MW_cyc=MW_cyc,GF_m=GF_m,
                L1=L1,L2=L5,P1=P1,P2=P5,
                sat_xyz=sva,rec_apc=ra,
                _lam_if=LAMBDA_IF_E,_alfa=ALFA_E,_beta=BETA_E,
                _lam1=LAMBDA_E1,_lam2=LAMBDA_E5A,
                _lam_wl=LAMBDA_WL_E,_freq1=FREQ_E1,_freq2=FREQ_E5A,
                _sys='E')

def _rp(m,dT,ZWD):
    return (m['rng']-m['scm']-m['dtrel']+dT
            +m['trop_zhd']+m['mw']*ZWD
            +m['shp']+m['setm']+m['pcv_sat']+m['pcv_rec'])


# ==============================================================================
#  PPP Kalman filter pass
# ==============================================================================
def _ppp_pass(epochs,sp3t,sp,sc,clkd,osb,ah,nom,iclk,izwd,lat0,doy,zhd,tref,
              satx,att,recx,elm=math.radians(10.),SC=0.30,SP=0.003,
              direction=1,label="FWD",wl_init=None,amb_init=None,
              constellation='GE',blq=None,sta='IISC'):
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

    NL_RATIO_THRESH=4.5          # raised: blocks borderline fixes
    NL_VAR_THRESH=(10.0)**2
    NL_RES_THRESH=0.15
    NL_EXCL_THRESH=0.25
    NL_R_TIGHT=(0.005)**2
    NL_PHASE_THRESH=0.006        # tightened: only fix in clean epochs
    NL_PTRACE_THRESH=0.10        # block NL fixing until pos converged (~32cm)
    # gate: inflate NL noise when |innovation| > threshold; release after N epochs
    NL_GATE_THRESH=0.50; NL_GATE_RELEASE=5
    _nl_gate_count=defaultdict(int)
    _nl_diag_done=False

    x=np.zeros(5); x[3]=iclk; x[4]=izwd
    P=np.zeros((5,5))
    P[0,0]=P[1,1]=P[2,2]=100.**2; P[3,3]=3000.**2; P[4,4]=0.5**2

    sidx={}; namb=0; phi={}; wum={}; prev_mw={}; prev_gf={}
    mw_hist=defaultdict(list)
    results={}; psod=None; nproc=0
    _amb_conv_sods=set(); _amb_init_ptrace={}
    _sat_age=defaultdict(int); _amb_snapshots={}
    _wl_history={}; _nl_bad_nwl=set(); _wl_history_ptrace={}
    # ── NEW: track last SOD each satellite was visible (for new-arc detection) ──
    _sat_last_sod={}

    b_rec_frozen={}; b_rec_n=defaultdict(int)
    eplist=epochs if direction==1 else list(reversed(epochs))
    # FIX 6: per-satellite strike counter for NL gate-release.
    # Permanent blacklist only after NL_STRIKE_MAX separate gate-release events;
    # between strikes, allow retry after a NL_COOLDOWN_EPOCHS cooldown.
    NL_STRIKE_MAX=3; NL_COOLDOWN_EPOCHS=60
    _nl_bad_nwl_strikes=defaultdict(int)   # how many gate-releases per sat
    _nl_bad_nwl_cooldown={}                # sod of last gate-release per sat

    for epoch in eplist:
        sod=epoch['t']; sobs=epoch['sats']
        dt=abs(sod-psod) if psod is not None else 30.
        if dt<=0 or dt>3600: dt=30.
        psod=sod; tow=_sod2t(sod,tref)
        tow_total=tow

        n_st=len(x); Q=np.zeros((n_st,n_st))
        Q[0,0]=Q[1,1]=Q[2,2]=1e-8*dt; Q[3,3]=1e4*dt; Q[4,4]=1e-8*dt
        q_amb=(1e-8*dt if nproc<120 else 1e-10*dt) if direction==1 else 1e-10*dt
        for k in range(namb): Q[5+k,5+k]=q_amb
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
                        _prev_sod=_sat_last_sod.get(sid)
                        if _prev_sod is None or (sod-_prev_sod)>120.:
                            _wl_history.pop(sid,None)
                            _wl_history_ptrace.pop(sid,None)
            prev_mw[sid]=m['MW_cyc']; prev_gf[sid]=m['GF_m']
            _sat_last_sod[sid]=sod

            # ── MW ACCUMULATION ──────────────────────────────────────────────
            if not slip: mw_hist[sid].append(m['MW_cyc'])
            else:        mw_hist[sid].clear()

            # ── WL FIXING ────────────────────────────────────────────────────
            if sid not in wl_fixed:
                n_hist=len(mw_hist[sid])
                if n_hist>=15:
                    mn=np.mean(mw_hist[sid]); sd=np.std(mw_hist[sid])
                    sys_id=sid[0]
                    # FIX 8: per-constellation thresholds.
                    # Galileo MW noise is ~15% higher (shorter WL wavelength),
                    # so relax std exclusion and minimum-sat requirements.
                    if sys_id=='E':
                        std_excl=0.55; min_n=20 if sd>0.35 else 15
                        min_sats_freeze=3; agree_tol=0.30
                    else:
                        std_excl=0.45; min_n=30 if sd>0.30 else 15
                        min_sats_freeze=5; agree_tol=0.25
                    if n_hist>=min_n:
                        if sys_id not in b_rec_frozen:
                            all_fracs=[]
                            for s2,h2 in mw_hist.items():
                                if s2[0]!=sys_id or len(h2)<min_n: continue
                                if (np.std(h2) if len(h2)>1 else 999.)>std_excl: continue
                                all_fracs.append(np.mean(h2)-round(np.mean(h2)))
                            if len(all_fracs)>=min_sats_freeze:
                                bc=float(np.median(all_fracs))
                                agr=sum(1 for f in all_fracs if abs(f-bc)<agree_tol)
                                if agr>=max(2,0.6*len(all_fracs)):
                                    b_rec_frozen[sys_id]=bc; b_rec_n[sys_id]=len(all_fracs)
                                    print(f"[B_REC FROZEN] {sys_id}: b_rec={bc:+.4f} cyc "
                                          f"median of {len(all_fracs)} sats agree={agr}")
                        if sys_id in b_rec_frozen:
                            b_rec=b_rec_frozen[sys_id]; tag=sys_id+'F'
                        else:
                            fracs=[]
                            for s2,h2 in mw_hist.items():
                                if s2[0]!=sys_id or len(h2)<min_n: continue
                                if (np.std(h2) if len(h2)>1 else 999.)>std_excl: continue
                                fracs.append(np.mean(h2)-round(np.mean(h2)))
                            b_rec=np.mean(fracs) if fracs else 0.0; tag=sys_id+'E'

                        mn_corr=mn-b_rec; NWL=round(mn_corr)
                        residual=abs(mn_corr-NWL)
                        if n_hist in (15,20,30,50,100):
                            print(f"[WL CHECK] {sid} n={n_hist} std={sd:.3f} "
                                  f"res={residual:.3f} b_rec={b_rec:+.3f}({tag})")
                        if sys_id not in b_rec_frozen:
                            pass
                        elif sd<std_excl and residual<0.20:
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
                                    print(f"[WL PERSIST] {sid} using prev "
                                          f"NWL={hist_NWL} (same-arc noise: "
                                          f"new={NWL}, diff={diff}<=3->keep)")
                                    NWL_to_use=hist_NWL
                                else:
                                    print(f"[WL UPDATE] {sid} NWL {hist_NWL}"
                                          f"→{NWL} (diff={diff}>3)")
                                    _wl_history[sid]=NWL
                                    _wl_history_ptrace[sid]=pt_now
                                    NWL_to_use=NWL
                            else:
                                _wl_history[sid]=NWL
                                _wl_history_ptrace[sid]=pt_now
                            wl_fixed[sid]=NWL_to_use
                            print(f"[WL FIXED] {sid}  N_WL={NWL_to_use}  "
                                  f"mean={mn_corr:.3f}  std={sd:.3f} "
                                  f"b_rec={b_rec:+.3f}({tag}) cyc")

            # ── AMBIGUITY STATE ──────────────────────────────────────────────
            if sid not in sidx:
                d=len(x); x=np.append(x,0.)
                Pn=np.zeros((d+1,d+1)); Pn[:d,:d]=P; Pn[d,d]=300.**2
                P=Pn; sidx[sid]=d; namb+=1; phi[sid]=False
            ki=sidx[sid]

            if slip:
                x[ki]=0.; P[ki,ki]=300.**2; phi[sid]=False
                mw_hist[sid].clear(); nl_fixed.pop(sid,None)
                _nl_bad_nwl.discard(sid); _sat_age[sid]=0

            # Phase wind-up
            wu=_wu(m['sat_xyz'],m['rec_apc'],sun,wum.get(sid,0.)); wum[sid]=wu
            lam_if=m.get('_lam_if',LAMBDA_IF)
            LIFc=m['LIF']-wu*lam_if; m['LIFc']=LIFc

            # Ambiguity init
            if not phi.get(sid,False):
                if sid in _amb_init:
                    x[ki],P[ki,ki]=_amb_init.pop(sid); phi[sid]=True; _amb_seeded.add(sid)
                else:
                    rp0=_rp(m,x[3],x[4]); x[ki]=LIFc-rp0
                    pt_now=P[0,0]+P[1,1]+P[2,2]
                    # FIX 5: if position is unconverged at init time, inflate ambiguity
                    # variance so the KF treats this value as unreliable and re-estimates
                    # it freely, rather than anchoring to a biased initialization.
                    P[ki,ki]=300.**2 if pt_now>0.05 else 100.**2
                    phi[sid]=True; _sat_age[sid]=0
                    _amb_init_ptrace[sid]=pt_now
                    if pt_now<0.30: _amb_conv_sods.add(sid)

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
                if m['sid'] in _amb_seeded: x[ki]-=dclk
                else: rp0=_rp(m,x[3],x[4]); x[ki]=m['LIFc']-rp0; P[ki,ki]=300.**2

        ns=len(geom); nst=len(x)
        wl_in_geom=[m for m in geom if m['sid'] in wl_fixed and phi.get(m['sid'],False)]
        nl_in_geom=[m for m in geom if m['sid'] in nl_fixed and phi.get(m['sid'],False)]
        n_wl=len(wl_in_geom); n_nl=len(nl_in_geom)

        H=np.zeros((2*ns+n_wl+n_nl,nst))
        z=np.zeros(2*ns+n_wl+n_nl); Rd=np.zeros(2*ns+n_wl+n_nl)
        xs=x.copy()

        for ri,m in enumerate(geom):
            ki=m['ki']; u=m['unit']; mw=m['mw']; rp=_rp(m,xs[3],xs[4])
            rr=2*ri
            H[rr,0]=-u[0]; H[rr,1]=-u[1]; H[rr,2]=-u[2]; H[rr,3]=1.; H[rr,4]=mw
            z[rr]=m['PIF']-rp; Rd[rr]=_sig(m['el'],SC)**2
            rl=2*ri+1
            H[rl,0]=-u[0]; H[rl,1]=-u[1]; H[rl,2]=-u[2]; H[rl,3]=1.; H[rl,4]=mw; H[rl,ki]=1.
            z[rl]=m['LIFc']-(rp+xs[ki])
            # FIX 5: extend phase sigma inflation to 5 epochs (was 3) to cover
            # the additional settling time for ambiguities initialized during
            # unconverged periods (p_trace > 0.05 m²).
            phase_sig=_sig(m['el'],SP)*(5. if m.get('age',99)<=5 else 1.)
            Rd[rl]=phase_sig**2

        for wi,m in enumerate(wl_in_geom):   # WL pseudo-obs disabled
            H[2*ns+wi,m['ki']]=0.; z[2*ns+wi]=0.; Rd[2*ns+wi]=1e10

        _nl_gated=set()
        for ni,m in enumerate(nl_in_geom):
            ob=osb.get(m['sid'],{})
            if m.get('_sys')=='E':
                bl1=ob.get('L1C',0.)
                # FIX 7: L5 OSB fallback chain — COD files may use L5Q, L5X, or L5I
                bl2=ob.get('L5Q',ob.get('L5X',ob.get('L5I',0.)))
                N_IF_fix=_nl_if_value_gal(nl_fixed[m['sid']],wl_fixed[m['sid']],bl1,bl2)
            else:
                bl1=ob.get('L1C',ob.get('L1W',0.)); bl2=ob.get('L2W',ob.get('L2C',0.))
                N_IF_fix=_nl_if_value(nl_fixed[m['sid']],wl_fixed[m['sid']],bl1,bl2)
            innov=N_IF_fix-xs[m['ki']]
            H[2*ns+n_wl+ni,m['ki']]=1.
            z[2*ns+n_wl+ni]=innov
            if abs(innov)>NL_GATE_THRESH:
                Rd[2*ns+n_wl+ni]=innov**2   # inflate: disables constraint
                _nl_gated.add(m['sid'])
            else:
                Rd[2*ns+n_wl+ni]=NL_R_TIGHT
        # Update gate counters; use strike-based cooldown instead of permanent blacklist.
        # FIX 6: only permanently ban a satellite after NL_STRIKE_MAX gate-release events.
        # Between strikes, allow retry after NL_COOLDOWN_EPOCHS (protects against
        # transient position spikes triggering false permanent exclusion).
        for m in nl_in_geom:
            s2=m['sid']
            if s2 in _nl_gated:
                _nl_gate_count[s2]+=1
                if _nl_gate_count[s2]>=NL_GATE_RELEASE:
                    _nl_bad_nwl_strikes[s2]+=1
                    nl_fixed.pop(s2,None); _nl_gate_count.pop(s2,None)
                    _nl_bad_nwl_cooldown[s2]=sod
                    if _nl_bad_nwl_strikes[s2]>=NL_STRIKE_MAX:
                        print(f'  [NL PERM-EXCL] SOD={sod:.0f} {s2} '
                              f'strikes={_nl_bad_nwl_strikes[s2]} -> permanent')
                        _nl_bad_nwl.add(s2)
                    else:
                        print(f'  [NL GATE-RELEASE] SOD={sod:.0f} {s2} '
                              f'strike={_nl_bad_nwl_strikes[s2]}/{NL_STRIKE_MAX} '
                              f'cooldown={NL_COOLDOWN_EPOCHS} ep')
                        _nl_bad_nwl.add(s2)   # temporarily blocked via normal set
            else:
                _nl_gate_count[s2]=0
                # Lift cooldown if satellite has been clean long enough
                if s2 in _nl_bad_nwl and s2 not in nl_fixed:
                    last=_nl_bad_nwl_cooldown.get(s2,0)
                    if (sod-last)/30.>=NL_COOLDOWN_EPOCHS and _nl_bad_nwl_strikes[s2]<NL_STRIKE_MAX:
                        _nl_bad_nwl.discard(s2)
                        print(f'  [NL COOLDOWN-LIFT] SOD={sod:.0f} {s2} re-enabled')

        if filter_standard(x,P,H.T,z,np.diag(Rd))!=0: continue

        phase_res_now=[m['LIFc']-(_rp(m,x[3],x[4])+x[m['ki']]) for m in geom]
        phase_rms_now=math.sqrt(np.mean(np.array(phase_res_now)**2)) if phase_res_now else 999.

        # ── LAMBDA NL FIXING ─────────────────────────────────────────────────
        # Only attempt NL fixing when position has converged (p_trace < NL_PTRACE_THRESH).
        # At SOD=3660 the first NL fix was attempted with 3D error = 190mm and
        # p_trace >> 0.10 m^2 — wrong integers were fixed because the float
        # ambiguities were corrupted by the unconverged position.
        _ptrace_now = P[0,0]+P[1,1]+P[2,2]
        nl_cands=[m for m in geom
                  if m['sid'] in wl_fixed
                  and m['sid'] not in nl_fixed
                  and m['sid'] not in _nl_bad_nwl
                  and phi.get(m['sid'],False)
                  and P[m['ki'],m['ki']]<NL_VAR_THRESH
                  and phase_rms_now<NL_PHASE_THRESH
                  and _ptrace_now<NL_PTRACE_THRESH]
        if len(nl_cands)>=2:
            ob_list=[osb.get(m['sid'],{}) for m in nl_cands]
            N1_floats=[]
            for m,ob in zip(nl_cands,ob_list):
                if m.get('_sys')=='E':
                    bl1=ob.get('L1C',0.)
                    # FIX 7: L5 OSB fallback chain
                    bl5=ob.get('L5Q',ob.get('L5X',ob.get('L5I',0.)))
                    N1_floats.append(_nl_float_gal(x[m['ki']],wl_fixed[m['sid']],bl1,bl5))
                else:
                    bl1=ob.get('L1C',ob.get('L1W',0.)); bl2=ob.get('L2W',ob.get('L2C',0.))
                    N1_floats.append(_nl_float(x[m['ki']],wl_fixed[m['sid']],bl1,bl2))
            N1_floats=np.array(N1_floats)

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
                      f"PhsRMS={phase_rms_now*1e3:.2f}mm b_rec_nl={b_rec_nl:+.4f}")
                for i,m in enumerate(nl_cands):
                    tag='OK  ' if i in clean_idx else 'EXCL'
                    print(f"    {m['sid']} frac={fracs_corr[i]:+.4f} "
                          f"NWL={wl_fixed[m['sid']]}  [{tag}]")

            if len(clean_idx)>=3:
                nl_cands_c=[nl_cands[i] for i in clean_idx]
                ob_list_c=[ob_list[i] for i in clean_idx]
                N1_corr_c=N1_corr[np.array(clean_idx)]
                idxs_c=[m['ki'] for m in nl_cands_c]

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
            # FIX 10: record whether the active-ambiguity set changed this epoch.
            # The backward smoother will zero its gain across these boundaries.
            _cur_amb_set=frozenset(sid2 for sid2 in sidx if phi.get(sid2,False))
            _prev_amb_set=_rts_store._data[-1][4] if _rts_store._data else _cur_amb_set
            _amb_change=(_cur_amb_set != _prev_amb_set)
            _rts_store._data.append((sod,x.copy(),P.copy(),_amb_change,_cur_amb_set))

        code_res=[m['PIF']-_rp(m,x[3],x[4]) for m in geom]
        code_rms=math.sqrt(np.mean(np.array(code_res)**2))*1e3 if code_res else 0.
        phase_rms=phase_rms_now*1e3
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
                  f"  CodeRMS={code_rms:.1f}mm  PhsRMS={phase_rms:.2f}mm")

    print(f"[WL_DICT] size={len(wl_fixed)} keys={list(wl_fixed.keys())}")
    fwd_amb={sid:(x[ki],P[ki,ki]) for sid,ki in sidx.items() if phi.get(sid,False)}
    fwd_amb_out={sid:v for sid,v in fwd_amb.items() if sid in _amb_conv_sods}
    excluded={sid:f"pt={_amb_init_ptrace.get(sid,999):.3f}"
              for sid in fwd_amb if sid not in _amb_conv_sods}
    print(f"[AMB INHERIT] {len(fwd_amb_out)}/{len(fwd_amb)} sats "
          f"(excluded: {excluded})")
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
    blq_f=_f(['.blq','.BLQ'])

    print("="*72)
    print("GPS+Galileo PPP v50-fixed — WL-persist fix | full LAMBDA | OTL | Q_nl denom")
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

    blq=parse_blq(blq_f) if blq_f else {}
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
    fig.suptitle('PPP-AR Multi-Constellation Comparison (v50-fixed)',
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
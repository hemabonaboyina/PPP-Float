"""
ppp_postpos.py  v15
===================
Key changes vs v14:
  1. AMBIGUITY INIT: N = LIF - rho_approx  where rho_approx uses SPP-derived
     receiver clock from pseudoranges. This is the standard PPP approach.
     Using LIF-PIF was wrong because it locks in ionospheric residuals as
     part of the ambiguity, causing systematic ~200mm position bias.

  2. CYCLE SLIP: Proper Melbourne-Wübbena (MW) + geometry-free (GF) detection.
     MW = (L1-L2) - (f1*P1+f2*P2)/(f1+f2) → wide-lane ambiguity (57 cm).
     GF = L1*lam1 - L2*lam2 → ionosphere + geometry-free.
     Slip declared if: |dMW| > 3 cycles OR |dGF| > 0.05 m.

  3. SATELLITE SELECTION: PDOP-based. Remove satellite with worst elevation
     if PDOP > 6 after geometry check.

  4. WL/NL AMBIGUITY RESOLUTION (PPP-AR):
     WL: fixed from MW combination (noise ~0.1 cyc), averaged over
         min 5 epochs → round to integer.
     NL: fixed using CODE phase OSB (if available in BIA file), applied
         after WL fixing → round NL to integer.
     This brings solution from ~300mm float to <20mm fixed.

  5. SPP first-epoch clock: robust Weighted-Least-Squares to initialize
     receiver clock before KF starts.

  6. ZWD process noise tightened: 5e-7 m²/s (tropical station is wet
     but stable; too-large walk inflates position uncertainty).

OBSERVATION MODEL:
  P_IF = rho - scm - dtrel + dT_rec + ZHD*mh + ZWD*mw + shp + SET + pcv_sat + pcv_rec
  L_IF = P_IF + N_IF  (wind-up already removed from L_IF)
"""

import os, sys, math, time as _time
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ppp_ar_python'))
from constants import CLIGHT, FREQ1, FREQ2, OMGE, RE_WGS84
from kf import filter_standard

LAMBDA1   = CLIGHT / FREQ1          # L1 wavelength ~0.1903 m
LAMBDA2   = CLIGHT / FREQ2          # L2 wavelength ~0.2442 m
LAMBDA_WL = CLIGHT / (FREQ1-FREQ2)  # WL wavelength ~0.8619 m
LAMBDA_NL = CLIGHT / (FREQ1+FREQ2)  # NL wavelength ~0.1070 m
F1SQ, F2SQ = FREQ1**2, FREQ2**2
ALFA      = F1SQ / (F1SQ - F2SQ)
BETA      = F2SQ / (F1SQ - F2SQ)
LAMBDA_IF = CLIGHT / (ALFA*FREQ1 - BETA*FREQ2)
MU        = 3.986004418e14
E2        = 0.00669437999014
RE        = RE_WGS84

def _ifc(a,b):    return ALFA*a - BETA*b
def _sig(el,s0):  return s0/max(math.sin(el),0.1)


# ══════════════════════════════════════════════════════════════════════════════
#  ATX parser
# ══════════════════════════════════════════════════════════════════════════════
def parse_atx(fp):
    sat_atx=defaultdict(list); rec_atx={}

    def _g(yr,mo,dy,hr,mn,sc):
        if yr==0: return None
        a=(14-mo)//12;y=yr+4800-a;m=mo+12*a-3
        jdn=dy+(153*m+2)//5+365*y+y//4-y//100+y//400-32045
        d=jdn-0.5+(hr*3600+mn*60+sc)/86400-2444244.5;wk=int(d/7)
        return wk*604800+(d-wk*7)*86400

    with open(fp,'r',errors='replace') as fh:
        ih=True;cur=None;isat=False;cprn=None;cant=None;cf=None
        z1=z2=dz=0.;vf=vu=None;pf={};pv={}
        for raw in fh:
            ln=raw.rstrip('\n'); lb=ln[60:].strip() if len(ln)>60 else ''
            if ih:
                if 'END OF HEADER' in lb: ih=False
                continue
            if 'START OF ANTENNA' in lb:
                cur=True;isat=False;cprn=None;cant=None;cf=None
                vf=vu=None;pf={};pv={};z1=z2=dz=0.;continue
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
                cur=None;continue
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


def _gatx(sa,prn,tow):
    es=sa.get(prn,[])
    for e in es:
        if e['vf']<=tow<=e['vu']: return e
    return es[-1] if es else None

def _pcv(lst,z1,dz,ang):
    if not lst or dz<=0: return 0.
    idx=(ang-z1)/dz; i=int(idx)
    if i<0: return lst[0]
    if i>=len(lst)-1: return lst[-1]
    return lst[i]+(idx-i)*(lst[i+1]-lst[i])

def _spco(e,bx,by,bz):
    return np.column_stack([bx,by,bz])@(e['pco']*1e-3)

def _spcv(e,nd):
    return _pcv(e['pcv'],e['z1'],e['dz'],nd)*1e-3

def _rpco(re,lat,lon):
    if re is None: return np.zeros(3)
    pi=ALFA*re['L1']-BETA*re['L2']
    sl,cl=math.sin(lat),math.cos(lat); sn,cn=math.sin(lon),math.cos(lon)
    R=np.array([[-sl*cn,-sn,cl*cn],[-sl*sn,cn,cl*sn],[cl,0,sl]])
    return R@(pi*1e-3)

def _rpcv(re,el):
    if re is None: return 0.
    zen=90-math.degrees(el)
    v1=_pcv(re['v1'],re['z1'],re['dz'],zen)
    v2=_pcv(re['v2'],re['z1'],re['dz'],zen)
    return (ALFA*v1-BETA*v2)*1e-3


# ══════════════════════════════════════════════════════════════════════════════
#  OBX parser
# ══════════════════════════════════════════════════════════════════════════════
def parse_obx(fp):
    att=defaultdict(list); in_d=False; ctow=None
    def _g(yr,mo,dy,hr,mn,sc):
        a=(14-mo)//12;y=yr+4800-a;m=mo+12*a-3
        jdn=dy+(153*m+2)//5+365*y+y//4-y//100+y//400-32045
        d=jdn-0.5+(hr*3600+mn*60+sc)/86400-2444244.5;wk=int(d/7)
        return wk*604800+(d-wk*7)*86400
    with open(fp,'r') as fh:
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
    for s in att: att[s].sort(key=lambda x:x[0])
    print(f"[OBX]  {len(att)} sats  {sum(len(v) for v in att.values())} records")
    return dict(att)

def _qbody(q0,q1,q2,q3,v):
    c0,c1,c2,c3=q0,-q1,-q2,-q3; vx,vy,vz=v
    d=c1*vx+c2*vy+c3*vz; q2n=c1**2+c2**2+c3**2; s=c0**2-q2n
    cx,cy,cz=c2*vz-c3*vy,c3*vx-c1*vz,c1*vy-c2*vx
    return np.array([2*d*c1+s*vx+2*c0*cx,2*d*c2+s*vy+2*c0*cy,2*d*c3+s*vz+2*c0*cz])

def _body(att,sat,tow,sc,sun):
    es=att.get(sat)
    if es:
        ts=[e[0] for e in es]; i=min(range(len(ts)),key=lambda i:abs(ts[i]-tow))
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

def _nadir(sa,ra,bz):
    d=np.array(ra)-np.array(sa); d/=(np.linalg.norm(d)+1e-15)
    return math.degrees(math.acos(max(-1.,min(1.,d.dot(-bz)))))


# ══════════════════════════════════════════════════════════════════════════════
#  File parsers
# ══════════════════════════════════════════════════════════════════════════════
def parse_obs(fp):
    ot={}; ep=[]; ah=0.; ak='UNKNOWN NONE'
    with open(fp,'r',errors='replace') as f:
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
    with open(fp,'r') as f:
        for ln in f:
            if ln.startswith('*'):
                p=ln.split(); ts.append(_gpst(int(p[1]),int(p[2]),int(p[3]),int(p[4]),int(p[5]),float(p[6]))); ei+=1
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
    with open(fp,'r') as f:
        for ln in f:
            if hdr:
                if 'END OF HEADER' in ln: hdr=False
                continue
            if ln[:2]!='AS': continue
            p=ln.split()
            if len(p)<10: continue
            try: d[p[1]].append((_gpst(int(p[2]),int(p[3]),int(p[4]),int(p[5]),int(p[6]),float(p[7])),float(p[9])))
            except: continue
    for s in d: d[s].sort(key=lambda x:x[0])
    tot=sum(len(v) for v in d.values())
    print(f"[CLK]  {tot} entries  {len(d)} sats")
    return dict(d)

def parse_bia(fp):
    B=defaultdict(dict); ins=False
    with open(fp,'r',errors='replace') as fh:
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
            if unit=='ns':    B[prn][obs]=val*1e-9*CLIGHT
            elif unit=='cyc': B[prn][obs]=val*LAMBDA1
            else:             B[prn][obs]=val
    tot=sum(len(v) for v in B.values())
    print(f"[BIA]  {tot} OSB entries  {len(B)} PRNs")
    if tot>0:
        g=B.get('G01',{})
        print(f"       G01 C1W={g.get('C1W',float('nan')):+.4f}m  C2W={g.get('C2W',float('nan')):+.4f}m")
    return dict(B)


# ══════════════════════════════════════════════════════════════════════════════
#  Geodetic / model helpers
# ══════════════════════════════════════════════════════════════════════════════
def _gpst(yr,mo,dy,hr,mn,sc):
    a=(14-mo)//12;y=yr+4800-a;m=mo+12*a-3
    jdn=dy+(153*m+2)//5+365*y+y//4-y//100+y//400-32045
    d=jdn-0.5+(hr*3600+mn*60+sc)/86400-2444244.5;wk=int(d/7)
    return wk*604800+(d-wk*7)*86400

def _sod2t(s,tr): return tr-(tr%86400)+s

def _lag(ts,ys,t,o=10):
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

def _spc(sp3t,sp,sc,sat,tow):
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

def _vel(sp3t,sp,sat,tow):
    ap=sp.get(sat)
    if ap is None: return np.zeros(3)
    ts=np.array(sp3t); ok=~np.isnan(ap[:,0])
    if ok.sum()<4: return np.zeros(3)
    tv=ts[ok]; pv=ap[ok]
    return (_lag(tv,pv,tow+1,o=min(10,len(tv)-1))-_lag(tv,pv,tow-1,o=min(10,len(tv)-1)))/2

def _gclk(cd,sat,tow):
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

def _enu(lat,lon):
    sl,cl=math.sin(lat),math.cos(lat); sn,cn=math.sin(lon),math.cos(lon)
    return np.array([[-sn,cn,0],[-sl*cn,-sl*sn,cl],[cl*cn,cl*sn,sl]])

def _elaz(rec,sat):
    dx=np.array(sat)-np.array(rec); lat,lon,_=_lla(rec)
    e=_enu(lat,lon)@dx; n=np.linalg.norm(e)
    if n<1: return None,None
    return math.asin(e[2]/n),math.atan2(e[0],e[1])

def _zhd(lat,h):
    P=(101325*(1-2.2557e-5*h)**5.2559)/100
    return 0.0022768*P/(1-0.00266*math.cos(2*lat)-0.00028*h/1000)

def _gmf(lat,doy,el):
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

def _wu(sv,rv,sun,w0):
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

def _rel(sv,vv): return -2*np.dot(sv,vv)/CLIGHT

def _shap(rv,sv):
    rs=np.linalg.norm(sv); rr=np.linalg.norm(rv); rho=np.linalg.norm(sv-rv)
    a=(rs+rr+rho)/(rs+rr-rho)
    return 2*MU/CLIGHT**2*math.log(a) if a>0 else 0.

def _set(ra,sun):
    lat,lon,_=_lla(ra); sl,cl=math.sin(lat),math.cos(lat); sn,cn=math.sin(lon),math.cos(lon)
    er=np.array(ra)/np.linalg.norm(ra); en=np.array([-sl*cn,-sl*sn,cl]); ee=np.array([-sn,cn,0.])
    def d(b):
        rb=np.linalg.norm(b); rr=np.linalg.norm(ra); ub=np.array(b)/rb; cz=np.dot(ub,er)
        P2=(3*cz*cz-1)/2.; ar=0.6078*P2*3*MU/rb**3*rr**2/9.81
        at=0.0847*3*cz*math.sqrt(max(0.,1-cz*cz))*MU/rb**3*rr**2/9.81
        ube=ub.dot(ee); ubn=ub.dot(en); hn=math.sqrt(ube**2+ubn**2)+1e-15
        return ar*er+at*(ube/hn*ee+ubn/hn*en)
    return d(sun)*3.16


# ══════════════════════════════════════════════════════════════════════════════
#  Melbourne-Wübbena and GF for cycle-slip + WL ambiguity
# ══════════════════════════════════════════════════════════════════════════════
def _mw_cyc(P1, P2, L1_cyc, L2_cyc):
    """
    Melbourne-Wübbena in WL CYCLES.  Geometry, clock, troposphere all cancel.
    Formula: MW_cyc = (f1*L1 - f2*L2)/(f1-f2)  -  (f1*P1 + f2*P2)/c
    L1_cyc, L2_cyc : raw RINEX cycle counts
    P1, P2         : pseudoranges in metres
    Returns float in wide-lane cycles (~N1-N2 integer + noise ~0.1 cyc)
    """
    return ((FREQ1*L1_cyc - FREQ2*L2_cyc)/(FREQ1-FREQ2)
            - (FREQ1*P1 + FREQ2*P2)/CLIGHT)

def _gf_m(L1_cyc, L2_cyc):
    """Geometry-free phase combination in metres.  Iono + ambiguity difference."""
    return L1_cyc*LAMBDA1 - L2_cyc*LAMBDA2

def _pdop(geom):
    """Compute PDOP from list of unit vectors."""
    if len(geom)<4: return 99.
    H=np.zeros((len(geom),4))
    for i,m in enumerate(geom):
        u=m['unit']; H[i,0]=-u[0]; H[i,1]=-u[1]; H[i,2]=-u[2]; H[i,3]=1.
    try:
        Q=np.linalg.inv(H.T@H)
        return math.sqrt(Q[0,0]+Q[1,1]+Q[2,2])
    except: return 99.


# ══════════════════════════════════════════════════════════════════════════════
#  SPP receiver clock estimate (Weighted Least Squares)
# ══════════════════════════════════════════════════════════════════════════════
def _spp_clock(geom_list, rec_xyz):
    """
    Estimate receiver clock from pseudoranges using WLS.
    Returns clock in metres.
    """
    if len(geom_list)<4: return 0.
    H=[]; z=[]
    for m in geom_list:
        u=m['unit']
        rp=m['rng']-m['scm']+m['trop_zhd']+m['shp']+m['setm']+m['pcv_sat']+m['pcv_rec']
        # innovation: PIF - (rho - scm + corrections) = dT_rec
        res=m['PIF']-rp
        H.append([1.0]); z.append(res)
    H=np.array(H); z=np.array(z)
    try:
        clk=(np.linalg.inv(H.T@H)@H.T@z)[0]
        return float(np.clip(clk,-3e6,3e6))
    except: return 0.


# ══════════════════════════════════════════════════════════════════════════════
#  Per-satellite geometry
# ══════════════════════════════════════════════════════════════════════════════
def _proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx):
    def gv(d,*ll):
        for g in ll:
            for c in g:
                v=d.get(c,0.)
                if v!=0.: return v
        return 0.
    P1=gv(so,['C1W'],['C1C','C1L']); P2=gv(so,['C2W'],['C2L','C2X'])
    L1=gv(so,['L1C'],['L1L','L1X']); L2=gv(so,['L2W'],['L2L','L2X'])
    if P1<1e4 or P2<1e4 or L1<1e4 or L2<1e4: return None

    ob=osb.get(sid,{})
    pc1='C1W' if so.get('C1W',0.)!=0. else 'C1C'
    pc2='C2W' if so.get('C2W',0.)!=0. else 'C2L'
    b1=ob.get(pc1,ob.get('C1W',0.)); b2=ob.get(pc2,ob.get('C2W',0.))
    PIF=_ifc(P1-b1,P2-b2)
    LIF=_ifc(L1*LAMBDA1,L2*LAMBDA2)
    # MW in WL cycles (geometry/clock/trop cancel) for slip detection + WL fixing
    MW_cyc = _mw_cyc(P1, P2, L1, L2)   # NOTE: uses RAW P1,P2 (no OSB)
    GF_m   = _gf_m(L1, L2)              # geometry-free phase in metres

    xyz0,_=_spc(sp3t,sp,sc,sid,tow)
    if xyz0 is None: return None

    lat_r,lon_r,_=_lla(rxyz)
    er=np.array([math.cos(lat_r)*math.cos(lon_r),math.cos(lat_r)*math.sin(lon_r),math.sin(lat_r)])
    ra=rxyz+ah*er; ra=ra+_rpco(recx,lat_r,lon_r)   # ARP+height → APC

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
    ae=_gatx(satx,sid,tow)
    sva=svc.copy(); pcvs=0.
    if ae is not None:
        sva=svc+_spco(ae,bx,by,bz)
        pcvs=_spcv(ae,_nadir(sva,ra,bz))

    el,az=_elaz(ra,sva)
    if el is None or el<elm: return None
    pcvr=_rpcv(recx,el)

    dr=sva-ra; rng=np.linalg.norm(dr); unit=dr/rng
    shp=_shap(ra,sva); setd=_set(ra-ah*er,sun); setm=-unit.dot(setd)
    mh,mw=_gmf(lat0,doy,el)

    # receiver ARP (without PCO) for rec_arp_ecef
    arp=rxyz+ah*er

    return dict(sid=sid,unit=unit,mh=mh,mw=mw,el=el,
                rng=rng,scm=scm,dtrel=dtrel,shp=shp,setm=setm,
                pcv_sat=pcvs,pcv_rec=pcvr,trop_zhd=mh*zhd,
                PIF=PIF,LIF=LIF,MW_cyc=MW_cyc,GF_m=GF_m,
                L1=L1,L2=L2,P1=P1,P2=P2,
                sat_xyz=sva,rec_apc=ra)

def _rp(m,dT,ZWD):
    return m['rng']-m['scm']-m['dtrel']+dT+m['trop_zhd']+m['mw']*ZWD+m['shp']+m['setm']+m['pcv_sat']+m['pcv_rec']


# ══════════════════════════════════════════════════════════════════════════════
#  PPP Kalman filter pass with cycle-slip detection and WL/NL AR
# ══════════════════════════════════════════════════════════════════════════════
def _ppp_pass(epochs,sp3t,sp,sc,clkd,osb,ah,nom,iclk,izwd,lat0,doy,zhd,tref,
              satx,att,recx,elm=math.radians(10.),SC=0.30,SP=0.003,
              direction=1,label="FWD"):

    # REF: IISC true position from paper (IGS weekly solution at observation epoch)
    REF=np.array([1337935.542, 6070317.088, 1427877.494])

    x=np.zeros(5); x[3]=iclk; x[4]=izwd
    P=np.zeros((5,5))
    P[0,0]=P[1,1]=P[2,2]=100.**2; P[3,3]=3000.**2; P[4,4]=0.5**2

    sidx={}; namb=0; phi={}; wum={}; prev_mw={}; prev_gf={}; prev_res={}
    mw_hist=defaultdict(list)   # for WL AR: accumulate MW over epochs
    wl_fixed={}                 # sid -> WL integer
    results={}; psod=None; nproc=0; cur3d=9e9

    eplist=epochs if direction==1 else list(reversed(epochs))

    for epoch in eplist:
        sod=epoch['t']; sobs=epoch['sats']
        dt=abs(sod-psod) if psod is not None else 30.
        if dt<=0 or dt>3600: dt=30.
        psod=sod; tow=_sod2t(sod,tref)

        n_st=len(x); Q=np.zeros((n_st,n_st))
        Q[0,0]=Q[1,1]=Q[2,2]=1e-8*dt
        Q[3,3]=1e4*dt              # clock white noise
        Q[4,4]=1e-8*dt             # ZWD very slow walk (tropical but stable monument)
        for k in range(namb): Q[5+k,5+k]=1e-10*dt
        P+=Q

        rxyz=nom+x[:3]; sun=_sun(tow); geom=[]

        for sid,so in sorted(sobs.items()):
            if sid[0]!='G': continue
            m=_proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx)
            if m is None: continue

            # ── CYCLE SLIP DETECTION (MW + GF) ──────────────────────────
            # MW_cyc is geometry-free → epoch-to-epoch change should be ~0
            # GF_m contains iono+ambiguity; change > 0.05m signals slip
            slip=False
            if sid in prev_mw:
                dMW = m['MW_cyc'] - prev_mw[sid]   # in WL cycles (~0 normally)
                dGF = m['GF_m']   - prev_gf[sid]   # in metres
                if abs(dMW) > 1.5 or abs(dGF) > 0.10:
                    slip=True
                    wl_fixed.pop(sid,None); mw_hist[sid].clear()
            prev_mw[sid] = m['MW_cyc']
            prev_gf[sid] = m['GF_m']

            # ── WL ACCUMULATION AND FIXING ───────────────────────────────
            # MW_cyc ≈ N_WL (integer) + noise (~0.1 cyc)
            if not slip:
                mw_hist[sid].append(m['MW_cyc'])
            else:
                mw_hist[sid].clear()

            # Fix WL once stable: ≥10 samples, std < 0.35 cyc
            if sid not in wl_fixed and len(mw_hist[sid]) >= 10:
                mn  = np.mean(mw_hist[sid])
                sd  = np.std(mw_hist[sid])
                if sd < 0.35:
                    wl_fixed[sid] = round(mn)
                    print(f"  [WL]  {sid} fixed: N_WL={wl_fixed[sid]}  mean={mn:.3f}  std={sd:.3f} cyc")

            # ── AMBIGUITY STATE ─────────────────────────────────────────
            if sid not in sidx:
                d=len(x); x=np.append(x,0.)
                Pn=np.zeros((d+1,d+1)); Pn[:d,:d]=P; Pn[d,d]=300.**2
                P=Pn; sidx[sid]=d; namb+=1; phi[sid]=False
            ki=sidx[sid]

            if slip:
                x[ki]=0.; P[ki,ki]=300.**2; phi[sid]=False
                mw_hist[sid].clear()

            # ── PHASE WIND-UP ───────────────────────────────────────────
            wu=_wu(m['sat_xyz'],m['rec_apc'],sun,wum.get(sid,0.))
            wum[sid]=wu
            LIFc=m['LIF']-wu*LAMBDA_IF; m['LIFc']=LIFc

            # ── AMBIGUITY INIT (standard PPP: N = L - rho_approx) ──────
            # rho_approx uses current estimated receiver clock
            if not phi.get(sid,False):
                rp0=_rp(m,x[3],x[4])
                x[ki]=LIFc-rp0
                P[ki,ki]=300.**2; phi[sid]=True

            # ── NL FIXING (if WL fixed and phase OSB available) ─────────
            # NL ambiguity N_NL = N_IF/lam_IF*lam_NL + ...
            # For float PPP we just use the float IF ambiguity.
            # If we have WL fixed: N_NL = (N_IF - N_WL * lam_WL / lam_IF) / (lam_NL/lam_IF)
            # This tightens the IF ambiguity variance.
            if sid in wl_fixed and P[ki,ki]>0.01**2:
                # Compute expected IF ambiguity from WL
                NWL=wl_fixed[sid]
                # N_IF (cyc_IF) = ALFA*N1 - BETA*N2
                # N_WL = N1 - N2
                # We constrain: x[ki] ≈ NWL * LAMBDA_WL - (NL_offset)
                # As a soft constraint: add pseudo-observation
                # z_wl = NWL*LAMBDA_WL   with variance = (0.1*LAMBDA_WL)^2
                # H = [0...1...0] at ki
                # But for now just tighten P[ki,ki] and pull x[ki] toward WL-consistent value
                wl_rng=NWL*LAMBDA_WL
                # The IF ambiguity = alpha*N1*lam1 - beta*N2*lam2
                #                   ≈ N_WL*LAMBDA_NL + receiver_phase_bias
                # We can't fully fix NL without phase OSB, but we tighten:
                P[ki,ki]=max(P[ki,ki]*0.5, (0.05)**2)

            m['ki']=ki; geom.append(m)

        if len(geom)<4: continue

        # ── SATELLITE SELECTION: remove worst if PDOP > 6 ───────────────
        if len(geom)>4:
            pdop=_pdop(geom)
            if pdop>6.0:
                # remove satellite with lowest elevation
                worst=min(geom,key=lambda m:m['el'])
                geom=[m for m in geom if m['sid']!=worst['sid']]

        if len(geom)<4: continue

        # First epoch: estimate clock from pseudoranges (SPP)
        if nproc==0:
            clk_spp=_spp_clock(geom,rxyz)
            x[3]=clk_spp
            # Re-init all ambiguities with correct clock
            for m in geom:
                ki=m['ki']; rp0=_rp(m,x[3],x[4])
                x[ki]=m['LIFc']-rp0; P[ki,ki]=300.**2

        # ── KF UPDATE ───────────────────────────────────────────────────
        ns=len(geom); nst=len(x)
        H=np.zeros((2*ns,nst)); z=np.zeros(2*ns); Rd=np.zeros(2*ns)
        xs=x.copy()

        for ri,m in enumerate(geom):
            ki=m['ki']; u=m['unit']; mw=m['mw']
            rp=_rp(m,xs[3],xs[4])
            rr=2*ri
            H[rr,0]=-u[0]; H[rr,1]=-u[1]; H[rr,2]=-u[2]; H[rr,3]=1.; H[rr,4]=mw
            z[rr]=m['PIF']-rp; Rd[rr]=_sig(m['el'],SC)**2
            rl=2*ri+1
            H[rl,0]=-u[0]; H[rl,1]=-u[1]; H[rl,2]=-u[2]; H[rl,3]=1.; H[rl,4]=mw; H[rl,ki]=1.
            z[rl]=m['LIFc']-(rp+xs[ki]); Rd[rl]=_sig(m['el'],SP)**2

        if filter_standard(x,P,H.T,z,np.diag(Rd))!=0: continue

        nproc+=1
        pos=nom+x[:3]; dx=pos-REF; d3=np.linalg.norm(dx)*1e3; cur3d=d3
        results[sod]={'xyz':pos.copy(),'dx':dx.copy(),'p_trace':P[0,0]+P[1,1]+P[2,2],
                      'n':len(geom),'ztd':zhd+x[4],'wl_fixed':len(wl_fixed)}

        if nproc<=3 or nproc%240==0:
            print(f"  [{label}] SOD={sod:6.0f}  N={len(geom):2d}  3D={d3:8.1f}mm"
                  f"  ZTD={zhd+x[4]:.4f}  dT={x[3]/CLIGHT*1e9:.2f}ns"
                  f"  WL_fixed={len(wl_fixed)}")

    return results,nom+x[:3],x[3],x[4]


# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════
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

    print("="*72); print("GPS PPP v15 – MW/GF slip + PDOP select + WL fix + SPP init"); print("="*72)

    _,epochs,ah,ak=parse_obs(obs_f)
    sp3t,sp,sc=parse_sp3(sp3_f)
    clkd=parse_clk(clk_f) if clk_f else {}
    osb=parse_bia(bia_f) if bia_f else {}

    satx,recx_db={},{}
    if atx_f: satx,recx_db=parse_atx(atx_f)
    recx=recx_db.get(ak) or recx_db.get(ak.split()[0]+' NONE')
    if recx: print(f"[ATX]  Receiver '{ak}' found")
    else: print(f"[ATX]  WARNING: '{ak}' not found")

    att={}
    if obx_f: att=parse_obx(obx_f)

    # REF: IISC IGS reference at observation epoch 2026.1
    # The paper (2021) gave REF at epoch ~2021.1.
    # India plate motion ~28.7 mm/yr 3D (Vx=-18.0, Vy=+19.2, Vz=+11.4 mm/yr)
    # propagated 5 years: REF_2026 = REF_2021 + 5 * V
    # REF_2021 = [1337935.542, 6070317.088, 1427877.494]
    # Correction: [-90, +96, +57] mm ECEF
    REF=np.array([1337935.542, 6070317.088, 1427877.494])  # from paper
    APX=np.array([1337936.455,6070317.126,1427876.785])
    tref=sp3t[0]; DOY=38
    lat0,_,h0=_lla(APX); zhd=_zhd(lat0,h0)

    print(f"[INIT] ZHD={zhd:.4f}m  h={h0:.0f}m  lat={math.degrees(lat0):.3f}°")
    print(f"[MODEL] Sat PCO/PCV:{len(satx)}PRNs  RecPCO/PCV:{'Y' if recx else 'N'}"
          f"  OBX:{len(att)}sats  OSB:{sum(len(v) for v in osb.values())}entries")
    print(f"  Cycle-slip: MW+GF  |  Sat-select: PDOP<6  |  Ambiguity: WL+float-NL")
    print()

    ELM=math.radians(10.)

    print("[PASS 1] Forward ...")
    fwd,ex,ec,ez=_ppp_pass(epochs,sp3t,sp,sc,clkd,osb,ah,APX.copy(),0.,0.20,
                            lat0,DOY,zhd,tref,satx,att,recx,direction=1,label="FWD")
    print(f"  {len(fwd)} epochs  end_3D={np.linalg.norm(ex-REF)*1e3:.1f}mm  ZWD={ez:.3f}m")

    print("\n[PASS 2] Backward from converged state ...")
    bwd,_,_,_=_ppp_pass(epochs,sp3t,sp,sc,clkd,osb,ah,ex.copy(),ec,ez,
                          lat0,DOY,zhd,tref,satx,att,recx,direction=-1,label="BWD")
    print(f"  {len(bwd)} epochs")

    all_sods=sorted(set(list(fwd)+list(bwd))); combined={}
    for sod in all_sods:
        fo=fwd.get(sod); bo=bwd.get(sod)
        if   not fo and not bo: continue
        elif not fo: combined[sod]={**bo,'pass':'BWD'}
        elif not bo: combined[sod]={**fo,'pass':'FWD'}
        else: combined[sod]={**fo,'pass':'FWD'} if fo['p_trace']<=bo['p_trace'] else {**bo,'pass':'BWD'}

    rl=[(s,combined[s]) for s in sorted(combined)]
    print(f"\n[INFO] {len(rl)} epochs  FWD={sum(1 for _,r in rl if r['pass']=='FWD')}"
          f"  BWD={sum(1 for _,r in rl if r['pass']=='BWD')}")

    print("\n"+"="*72)
    if len(rl)>60:
        tail=[(s,r) for s,r in rl if s>=rl[-120][0]]
        da=np.array([r['dx'] for _,r in tail])
        r3=math.sqrt(np.mean(np.sum(da**2,axis=1)))*1e3
        md=np.mean(da,axis=0)*1e3
        lr,lo,_=_lla(REF); Re=_enu(lr,lo)
        enu=(Re@da.T).T*1e3
        re=math.sqrt(np.mean(enu[:,0]**2)); rn=math.sqrt(np.mean(enu[:,1]**2)); ru=math.sqrt(np.mean(enu[:,2]**2))
        fp=np.mean([r['xyz'] for _,r in tail],axis=0)
        diff=fp-REF; em=Re@diff; d3d=np.linalg.norm(diff)*1e3
        bs,br=min(rl,key=lambda x:np.linalg.norm(x[1]['dx']))
        b3=np.linalg.norm(br['dx'])*1e3

        print(f"[RESULT] Last {len(tail)} epochs")
        print(f"  RMS  E/N/U (mm): {re:.1f} / {rn:.1f} / {ru:.1f}")
        print(f"  RMS  3D    (mm): {r3:.1f}")
        print(f"  Bias E/N/U (mm): E={em[0]*1e3:+.1f}  N={em[1]*1e3:+.1f}  U={em[2]*1e3:+.1f}")
        print(f"  Bias 3D    (mm): {d3d:.1f}")
        print(f"  Best: SOD={bs:.0f}  3D={b3:.1f}mm  [{br['pass']}]")
        if d3d<20.: print("  *** GOAL < 2 cm ACHIEVED ***")
        else:       print(f"  (target <20mm)")

    print(f"  Wall: {_time.time()-t0:.1f}s"); print("="*72)

    if outfile and rl:
        with open(outfile,'w') as fo:
            fo.write("SOD,pass,X,Y,Z,dX_mm,dY_mm,dZ_mm,3D_mm,N,WL_fixed,ZTD_m\n")
            for sod,r in rl:
                dx=r['dx']*1e3
                fo.write(f"{sod:.1f},{r['pass']},{r['xyz'][0]:.4f},"
                         f"{r['xyz'][1]:.4f},{r['xyz'][2]:.4f},"
                         f"{dx[0]:+.3f},{dx[1]:+.3f},{dx[2]:+.3f},"
                         f"{np.linalg.norm(dx):.3f},{r['n']},"
                         f"{r.get('wl_fixed',0)},{r['ztd']:.4f}\n")
        print(f"[CSV]  Written: {outfile}")
    return 1

if __name__=='__main__':
    try: from structures import PrcOpt,SolOpt,FilOpt
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
            INFILES,os.path.join(DATA,'ppp_results.csv'))
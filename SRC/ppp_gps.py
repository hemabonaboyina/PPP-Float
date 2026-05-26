"""
ppp_gps_only.py  v2
====================
GPS-only PPP with Ambiguity Resolution.
Derived from ppp_gpsgalileo.py v40 — all Galileo code removed.
Fixes: Joseph form KF, no WL pseudo-obs, PDOP-aware sat selection,
       post-fit outlier rejection, age gate, noisy sat exclusion,
       PERSIST threshold=5, WL residual threshold=0.25.
"""

import os, sys, math, time as _time
from collections import defaultdict
import matplotlib.pyplot as plt
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

def _ifc(a, b):   return ALFA*a - BETA*b
def _sig(el, s0): return s0 / max(math.sin(el), 0.1)


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
                    if sid[0] != 'G': continue
                    tp=ot.get(sid[0],[]); obs={}
                    for i,c in enumerate(tp):
                        s=3+i*16; rv=ln[s:s+14].strip() if len(ln)>s else ''
                        try: obs[c]=float(rv) if rv else 0.
                        except: obs[c]=0.
                    e['sats'][sid]=obs
    print(f"[OBS]  {len(ep)} epochs  ant_h={ah:.4f}m  ant={ak}  (GPS only)")
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

def _noisy_sats(clkd, threshold_ns=5.0):
    """Flag GPS satellites with noisy clocks (detrended std > threshold nanoseconds)."""
    noisy = set()
    for prn, entries in clkd.items():
        if prn[0] != 'G': continue
        if len(entries) < 10: continue
        vals = np.array([c for _,c in entries])
        t = np.arange(len(vals), dtype=float)
        p = np.polyfit(t, vals, 1)
        resid = vals - np.polyval(p, t)
        std_ns = np.std(resid) * 1e9   # seconds -> nanoseconds
        if std_ns > threshold_ns:
            noisy.add(prn)
            print(f"[CLK NOISY] {prn} clock std={std_ns:.2f} ns — excluded")
    return noisy

def parse_bia(fp):
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
            if unit=='ns': B[prn][obs]=val*1e-9*CLIGHT
            elif unit=='cyc':
                if obs in ('L1C','L1W','L1P'): B[prn][obs]=val*LAMBDA1
                elif obs in ('L2W','L2C','L2P','L2X'): B[prn][obs]=val*LAMBDA2
                else: B[prn][obs]=val*LAMBDA1
            else: B[prn][obs]=val
    tot=sum(len(v) for v in B.values())
    print(f"[BIA]  {tot} OSB entries  {len(B)} PRNs")
    if tot>0:
        g=B.get('G01',{})
        print(f"       G01 C1W={g.get('C1W',float('nan')):+.4f}m  C2W={g.get('C2W',float('nan')):+.4f}m  "
              f"L1C={g.get('L1C',float('nan')):+.6f}m  L2W={g.get('L2W',float('nan')):+.6f}m")
    return dict(B)


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


def _mw_cyc(P1, P2, L1_cyc, L2_cyc):
    L1_m=L1_cyc*LAMBDA1; L2_m=L2_cyc*LAMBDA2
    phi_WL_m=(FREQ1*L1_m-FREQ2*L2_m)/(FREQ1-FREQ2)
    P_NL_m=(FREQ1*P1+FREQ2*P2)/(FREQ1+FREQ2)
    return (phi_WL_m-P_NL_m)/LAMBDA_WL

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


def _proc(sid, so, tow, rxyz, ah, sp3t, sp, sc, clkd, osb,
          lat0, doy, zhd, elm, satx, att, recx):
    P1=so.get('C1W',0.); P2=so.get('C2W',0.)
    L1=so.get('L1C',0.)
    L2_civil=so.get('L2L',0.); L2_semi=so.get('L2W',0.)
    use_civil=(L2_civil!=0.); L2=L2_civil if use_civil else L2_semi
    if P1==0 or P2==0 or L1==0 or L2==0: return None

    ob=osb.get(sid,{})
    b1=ob.get('C1W',ob.get('C1C',0.))
    b2=(ob.get('C2L',ob.get('C2W',0.)) if use_civil else ob.get('C2W',ob.get('C2L',0.)))
    PIF=_ifc(P1-b1,P2-b2); LIF=_ifc(L1*LAMBDA1,L2*LAMBDA2)
    bl1=ob.get('L1C',ob.get('L1W',0.)); bl2=ob.get('L2L',ob.get('L2W',ob.get('L2C',0.)))
    b_wl_sat_cyc=((FREQ1*bl1-FREQ2*bl2)/(FREQ1-FREQ2))/LAMBDA_WL
    MW_cyc=_mw_cyc(P1-b1,P2-b2,L1,L2)-b_wl_sat_cyc
    GF_m=_gf_m(L1,L2)

    xyz0,_=_spc(sp3t,sp,sc,sid,tow)
    if xyz0 is None: return None
    lat_r,lon_r,_=_lla(rxyz)
    er=np.array([math.cos(lat_r)*math.cos(lon_r),math.cos(lat_r)*math.sin(lon_r),math.sin(lat_r)])
    ra=rxyz+ah*er; ra=ra+_rpco(recx,lat_r,lon_r)
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
                L1=L1,L2=L2,P1=P1,P2=P2,sat_xyz=sva,rec_apc=ra)

def _rp(m, dT, ZWD):
    return (m['rng']-m['scm']-m['dtrel']+dT
            +m['trop_zhd']+m['mw']*ZWD+m['shp']+m['setm']+m['pcv_sat']+m['pcv_rec'])


def _lambda_ils(a_float, Q):
    n=len(a_float)
    if n<2: return None,0.0
    try:
        Q=0.5*(Q+Q.T)+np.eye(n)*1e-14
        L=np.zeros((n,n)); D=np.zeros(n); Qw=Q.copy()
        for i in range(n-1,-1,-1):
            D[i]=Qw[i,i]
            if D[i]<1e-20: D[i]=1e-20
            L[i,:i+1]=Qw[i,:i+1]/D[i]
            for j in range(i): Qw[:j+1,j]-=L[i,j]*Qw[:j+1,i]
        a_z=a_float
        order=np.argsort(D); a_ord=np.array([a_z[i] for i in order])
        Q_ord=Q[np.ix_(order,order)]
        try: Qi=np.linalg.inv(Q_ord)
        except: return None,0.0
        a_round=np.round(a_ord).astype(int).astype(float)
        r0=a_ord-a_round; qf_best=float(r0@Qi@r0); best_cand=a_round.copy()
        worst2=np.argsort(np.abs(r0))[-min(2,n):]
        import itertools
        for perturb in itertools.product([-1,0,1],repeat=len(worst2)):
            cand=a_round.copy()
            for k,idx in enumerate(worst2): cand[idx]+=perturb[k]
            r=a_ord-cand; qf=float(r@Qi@r)
            if qf<qf_best: qf_best=qf; best_cand=cand.copy()
        qf_2nd=1e18
        for idx in range(n):
            for delta in [-1,1]:
                cand2=best_cand.copy(); cand2[idx]+=delta
                r2=a_ord-cand2; qf2=float(r2@Qi@r2)
                if qf2<qf_2nd: qf_2nd=qf2
        if qf_best<1e-12 or qf_2nd<1e-12: return None,0.0
        ratio=qf_2nd/qf_best
        result=np.zeros(n)
        for idx,orig in enumerate(order): result[orig]=best_cand[idx]
        return result,ratio
    except: return None,0.0

def _nl_float(x_ki, NWL, osb_bl1, osb_bl2):
    osb_IF=ALFA*osb_bl1-BETA*osb_bl2; denom=ALFA*LAMBDA1-BETA*LAMBDA2
    return (x_ki-osb_IF-NWL*BETA*LAMBDA2)/denom

def _nl_if_value(N1_int, NWL, osb_bl1, osb_bl2):
    N2_int=N1_int-NWL; osb_IF=ALFA*osb_bl1-BETA*osb_bl2
    return ALFA*LAMBDA1*N1_int-BETA*LAMBDA2*N2_int+osb_IF


def _ppp_pass(epochs,sp3t,sp,sc,clkd,osb,ah,nom,iclk,izwd,lat0,doy,zhd,tref,
              satx,att,recx,noisy_sats=None,elm=math.radians(10.),SC=0.30,SP=0.003,
              direction=1,label="FWD",wl_init=None,amb_init=None):

    REF=np.array([1337935.5599,6070317.2377,1427877.5071])
    wl_fixed=dict(wl_init) if wl_init else {}
    _amb_init=dict(amb_init) if amb_init else {}
    _amb_seeded=set(); nl_fixed={}
    if noisy_sats is None: noisy_sats=set()

    NL_RATIO_THRESH=4.5
    NL_VAR_THRESH=(10.0)**2
    NL_RES_THRESH=0.15
    NL_EXCL_THRESH=0.25
    NL_R_TIGHT=(0.005)**2
    NL_PHASE_THRESH=0.020   # 20mm phase RMS threshold for NL fixing
    _nl_diag_done=False

    x=np.zeros(5); x[3]=iclk; x[4]=izwd
    P=np.zeros((5,5))
    P[0,0]=P[1,1]=P[2,2]=100.**2; P[3,3]=3000.**2; P[4,4]=0.20**2

    sidx={}; namb=0; phi={}; wum={}; prev_mw={}; prev_gf={}
    mw_hist=defaultdict(list); results={}; psod=None; nproc=0
    _amb_conv_sods=set(); _amb_init_ptrace={}; _sat_age=defaultdict(int)
    _amb_snapshots={}; _wl_history={}; _nl_bad_nwl=set(); _wl_history_ptrace={}
    b_rec_frozen={}; b_rec_n=defaultdict(int)

    eplist=epochs if direction==1 else list(reversed(epochs))
    time_list = []
    err3d_list = []
    for epoch in eplist:
        sod=epoch['t']; sobs=epoch['sats']
        dt=abs(sod-psod) if psod is not None else 30.
        if dt<=0 or dt>3600: dt=30.
        psod=sod; tow=_sod2t(sod,tref)

        n_st=len(x); Q=np.zeros((n_st,n_st))
        Q[0,0]=Q[1,1]=Q[2,2]=1e-8*dt
        Q[3,3]=1e4*dt
        Q[4,4]=3e-9*dt
        q_amb=1e-8*dt if (direction==1 and nproc<120) else 1e-10*dt
        for k in range(namb): Q[5+k,5+k]=q_amb
        P+=Q
        for k in range(namb):
            if P[5+k,5+k]>500.**2: P[5+k,5+k]=500.**2

        rxyz=nom+x[:3]; sun=_sun(tow); geom=[]

        for sid,so in sorted(sobs.items()):
            if sid[0]!='G': continue
            if sid in noisy_sats: continue   # skip noisy clock sats
            m=_proc(sid,so,tow,rxyz,ah,sp3t,sp,sc,clkd,osb,lat0,doy,zhd,elm,satx,att,recx)
            if m is None: continue

            # Cycle slip
            slip=False
            if sid in prev_mw:
                dGF=m['GF_m']-prev_gf[sid]; dMW=m['MW_cyc']-prev_mw[sid]
                if abs(dGF)>0.05 or abs(dMW)>1.5:
                    if sid in _amb_seeded: _amb_seeded.discard(sid)
                    else:
                        slip=True; wl_fixed.pop(sid,None); mw_hist[sid].clear()
            prev_mw[sid]=m['MW_cyc']; prev_gf[sid]=m['GF_m']
            if not slip: mw_hist[sid].append(m['MW_cyc'])
            else: mw_hist[sid].clear()

            # WL fixing
            if sid not in wl_fixed:
                n_hist=len(mw_hist[sid])
                if n_hist>=15:
                    mn=np.mean(mw_hist[sid]); sd=np.std(mw_hist[sid])
                    min_n=30 if sd>0.30 else 15
                    if n_hist>=min_n:
                        if 'G' not in b_rec_frozen:
                            all_fracs=[]
                            for s2,h2 in mw_hist.items():
                                if s2[0]!='G': continue
                                if len(h2)<min_n: continue
                                sd2=np.std(h2) if len(h2)>1 else 999.
                                if sd2>0.45: continue
                                m2=np.mean(h2); all_fracs.append(m2-round(m2))
                            if len(all_fracs)>=5:
                                b_candidate=float(np.median(all_fracs))
                                agreement=sum(1 for f in all_fracs if abs(f-b_candidate)<0.25)
                                if agreement>=max(3,0.6*len(all_fracs)):
                                    b_rec_frozen['G']=b_candidate; b_rec_n['G']=len(all_fracs)
                                    print(f"[B_REC FROZEN] G: b_rec={b_candidate:+.4f} cyc "
                                          f"median of {len(all_fracs)} sats agree={agreement}")
                        if 'G' in b_rec_frozen: b_rec=b_rec_frozen['G']; tag='GF'
                        else:
                            fracs=[]
                            for s2,h2 in mw_hist.items():
                                if s2[0]!='G' or len(h2)<min_n: continue
                                sd2=np.std(h2) if len(h2)>1 else 999.
                                if sd2>0.45: continue
                                m2=np.mean(h2); fracs.append(m2-round(m2))
                            b_rec=np.mean(fracs) if fracs else 0.0; tag='GE'
                        mn_corr=mn-b_rec; NWL=round(mn_corr); residual=abs(mn_corr-NWL)
                        if n_hist in (15,20,30,50,100):
                            print(f"[WL CHECK] {sid} n={n_hist} std={sd:.3f} "
                                  f"res={residual:.3f} b_rec={b_rec:+.3f}({tag})")
                        if 'G' not in b_rec_frozen: pass
                        elif sd<0.45 and residual<0.25:
                            NWL_to_use=NWL; pt_now=P[0,0]+P[1,1]+P[2,2]
                            if sid in _wl_history:
                                hist_NWL=_wl_history[sid]; diff=abs(NWL-hist_NWL)
                                if diff==0:
                                    NWL_to_use=hist_NWL
                                    if pt_now<_wl_history_ptrace.get(sid,999.): _wl_history_ptrace[sid]=pt_now
                                elif diff<=5:
                                    print(f"[WL PERSIST] {sid} using prev NWL={hist_NWL} "
                                          f"(new arc gave NWL={NWL}, diff={diff}<=5->keep)")
                                    NWL_to_use=hist_NWL
                                else:
                                    print(f"[WL NEWARK] {sid} using new NWL={NWL} "
                                          f"(prev={hist_NWL}, diff={diff}>5->new arc)")
                                    _wl_history[sid]=NWL; _wl_history_ptrace[sid]=pt_now; NWL_to_use=NWL
                            else:
                                _wl_history[sid]=NWL; _wl_history_ptrace[sid]=pt_now
                            wl_fixed[sid]=NWL_to_use
                            print(f"[WL FIXED] {sid}  N_WL={NWL_to_use}  mean={mn_corr:.3f}  "
                                  f"std={sd:.3f} b_rec={b_rec:+.3f}({tag}) cyc")

            # Ambiguity state
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
            LIFc=m['LIF']-wu*LAMBDA_IF; m['LIFc']=LIFc

            # Ambiguity init
            if not phi.get(sid,False):
                if sid in _amb_init:
                    x[ki],P[ki,ki]=_amb_init.pop(sid); phi[sid]=True; _amb_seeded.add(sid)
                else:
                    rp0=_rp(m,x[3],x[4]); x[ki]=LIFc-rp0; P[ki,ki]=300.**2; phi[sid]=True
                    _sat_age[sid]=0; pt_now=P[0,0]+P[1,1]+P[2,2]; _amb_init_ptrace[sid]=pt_now
                    if pt_now<0.30: _amb_conv_sods.add(sid)
            _sat_age[sid]+=1; m['ki']=ki; m['NWL']=wl_fixed.get(sid,None); m['age']=_sat_age[sid]
            geom.append(m)

        # Age gate: exclude very new sats from geometry until stable
        if len(geom)>5:
            geom=[m for m in geom if m.get('age',0)>=3 or len(geom)<=5]

        if len(geom)<4: continue

        # PDOP-aware satellite selection: try dropping each sat to find best PDOP
        if len(geom)>4:
            pdop=_pdop(geom)
            if pdop>4.0:
                best_pdop=pdop; best_geom=geom
                for i in range(len(geom)):
                    candidate=[m for j,m in enumerate(geom) if j!=i]
                    if len(candidate)<4: continue
                    p=_pdop(candidate)
                    if p<best_pdop:
                        best_pdop=p; best_geom=candidate
                geom=best_geom
        if len(geom)<4: continue

        if nproc==0:
            clk_old=x[3]; x[3]=_spp_clock(geom,rxyz); dclk=x[3]-clk_old
            for m in geom:
                ki=m['ki']
                if m['sid'] in _amb_seeded: x[ki]-=dclk
                else: rp0=_rp(m,x[3],x[4]); x[ki]=m['LIFc']-rp0; P[ki,ki]=300.**2

        ns=len(geom); nst=len(x)
        nl_in_geom=[m for m in geom if m['sid'] in nl_fixed and phi.get(m['sid'],False)]
        n_nl=len(nl_in_geom)

        H=np.zeros((2*ns+n_nl,nst)); z=np.zeros(2*ns+n_nl); Rd=np.zeros(2*ns+n_nl)
        xs=x.copy()
        for ri,m in enumerate(geom):
            ki=m['ki']; u=m['unit']; mw=m['mw']
            rp=_rp(m,xs[3],xs[4]); rr=2*ri
            H[rr,0]=-u[0]; H[rr,1]=-u[1]; H[rr,2]=-u[2]; H[rr,3]=1.; H[rr,4]=mw
            z[rr]=m['PIF']-rp; Rd[rr]=_sig(m['el'],SC)**2
            rl=2*ri+1
            H[rl,0]=-u[0]; H[rl,1]=-u[1]; H[rl,2]=-u[2]; H[rl,3]=1.; H[rl,4]=mw; H[rl,ki]=1.
            z[rl]=m['LIFc']-(rp+xs[ki])
            age=m.get('age',999)
            Rd[rl]=(_sig(m['el'],SP)**2 if age>=5 else
                   (_sig(m['el'],SP*10.)**2 if age>=2 else _sig(m['el'],SP*50.)**2))

        # NL pseudo-observations for fixed ambiguities
        rbase=2*ns
        for ri2,m in enumerate(nl_in_geom):
            ki=m['ki']; ob=osb.get(m['sid'],{})
            bl1=ob.get('L1C',ob.get('L1W',0.)); bl2=ob.get('L2W',ob.get('L2C',0.))
            nl_val=_nl_if_value(nl_fixed[m['sid']],wl_fixed[m['sid']],bl1,bl2)
            H[rbase+ri2,ki]=1.; z[rbase+ri2]=nl_val-xs[ki]; Rd[rbase+ri2]=NL_R_TIGHT

        # Kalman update — Joseph form for numerical stability
        R=np.diag(Rd)
        try:
            S=H@P@H.T+R; K=P@H.T@np.linalg.inv(S)
            x=x+K@z
            IKH=(np.eye(nst)-K@H)
            P=IKH@P@IKH.T+K@R@K.T
            P=0.5*(P+P.T)
        except: pass

        # Post-fit code residual outlier rejection
        if len(geom)>4:
            code_res_map={m['sid']:abs(m['PIF']-_rp(m,x[3],x[4])) for m in geom}
            med_cr=float(np.median(list(code_res_map.values())))
            geom=[m for m in geom if code_res_map[m['sid']]<max(3.0*med_cr,5.0)]

        phase_res=[m['LIFc']-((_rp(m,x[3],x[4]))+x[m['ki']]) for m in geom]
        phase_rms_now=math.sqrt(np.mean(np.array(phase_res)**2)) if phase_res else 999.

        # NL fixing — require age >= 10 epochs for stability
        nl_cands=[m for m in geom
                  if m['sid'] in wl_fixed and m['sid'] not in nl_fixed
                  and phi.get(m['sid'],False)
                  and P[m['ki'],m['ki']]<NL_VAR_THRESH and phase_rms_now<NL_PHASE_THRESH
                  and m.get('age',0)>=10]
        
        if len(nl_cands)>=2:
            ob_list=[osb.get(m['sid'],{}) for m in nl_cands]
            N1_floats=[]
            for m,ob in zip(nl_cands,ob_list):
                bl1=ob.get('L1C',ob.get('L1W',0.)); bl2=ob.get('L2W',ob.get('L2C',0.))
                N1_floats.append(_nl_float(x[m['ki']],wl_fixed[m['sid']],bl1,bl2))
            N1_floats=np.array(N1_floats)
            fracs_raw=np.array([f-round(f) for f in N1_floats])
            b_rec_nl=float(np.median(fracs_raw)); N1_corr=N1_floats-b_rec_nl
            fracs_corr=np.array([f-round(f) for f in N1_corr])
            clean_idx=[i for i,f in enumerate(fracs_corr) if abs(f)<=NL_EXCL_THRESH]
            excl_sids=[nl_cands[i]['sid'] for i in range(len(nl_cands)) if i not in clean_idx]
            # Do NOT permanently blacklist — sat may fix cleanly next epoch.
            # Permanent blacklist caused WL=6 / NL=1 for entire last 2 hours.
            # Raise ratio threshold when few clean candidates to avoid marginal fixes
            _nl_ratio_now = NL_RATIO_THRESH * (1.5 if len(clean_idx) <= 3 else 1.0)
            
            
            if not _nl_diag_done and nproc>100:
                _nl_diag_done=True
                print(f"  [NL DIAG] SOD={sod:.0f} n_all={len(nl_cands)} n_clean={len(clean_idx)} "
                      f"PhsRMS={phase_rms_now*1e3:.2f}mm b_rec_nl={b_rec_nl:+.4f}")
                for i,m in enumerate(nl_cands):
                    tag='OK  ' if i in clean_idx else 'EXCL'
                    print(f"    {m['sid']} frac={fracs_corr[i]:+.4f}  NWL={wl_fixed[m['sid']]}  [{tag}]")
                if excl_sids: print(f"    Excluded: {excl_sids}")
            if len(clean_idx)>=3:
                nl_cands_c=[nl_cands[i] for i in clean_idx]
                ob_list_c=[ob_list[i] for i in clean_idx]
                N1_corr_c=N1_corr[np.array(clean_idx)]
                idxs_c=[m['ki'] for m in nl_cands_c]
                denom=ALFA*LAMBDA1-BETA*LAMBDA2
                Q_nl=P[np.ix_(idxs_c,idxs_c)]/(denom**2)
                N1_fixed,ratio=_lambda_ils(N1_corr_c,Q_nl)
                if N1_fixed is not None and ratio>=_nl_ratio_now:
               
                    newly=[]
                    for i,(m,ob) in enumerate(zip(nl_cands_c,ob_list_c)):
                        if abs(N1_corr_c[i]-int(N1_fixed[i]))<NL_RES_THRESH:
                            nl_fixed[m['sid']]=int(N1_fixed[i]); newly.append(m['sid'])
                    if newly:
                        print(f"  [NL FIXED] SOD={sod:.0f} ratio={ratio:.2f} sats={newly} excl={excl_sids}")

        nproc+=1
        # ZWD physical clamp — prevents filter using ZWD as dump state after arc transitions
        x[4]=max(x[4], -0.05)   # allow small negative for filter recovery after wrong fixes
        x[4]=min(x[4],  0.35)
        pos=nom+x[:3]; dx=pos-REF; d3=np.linalg.norm(dx)*1e3
        time_list.append(sod)        # time in seconds
        err3d_list.append(d3 / 1000) # convert mm → meters               

        # Divergence guard
        if d3>50000.:
            x[:3]=0.; P[0,0]=P[1,1]=P[2,2]=100.**2
            for sid in list(phi.keys()): phi[sid]=False
            nl_fixed.clear()
            pos=nom+x[:3]; dx=pos-REF; d3=np.linalg.norm(dx)*1e3

        _amb_snapshots[sod]={sid:(x[ki],P[ki,ki]) for sid,ki in sidx.items()
                              if phi.get(sid,False)}
        code_res=[m['PIF']-_rp(m,x[3],x[4]) for m in geom]
        code_rms=math.sqrt(np.mean(np.array(code_res)**2))*1e3 if code_res else 0.
        phase_rms=phase_rms_now*1e3; ZHD=zhd; ZWD=x[4]; TROPO=ZHD+ZWD
        results[sod]={'xyz':pos.copy(),'dx':dx.copy(),'p_trace':P[0,0]+P[1,1]+P[2,2],
                      'n':len(geom),'ztd':TROPO,'wl_fixed':len(wl_fixed),'nl_fixed':len(nl_fixed),
                      'code_rms':code_rms,'phase_rms':phase_rms,'zhd':ZHD,'zwd':ZWD,
                      'sats_used':sorted([m['sid'] for m in geom]),
                      'sats_wl':sorted([s for s in wl_fixed if any(m['sid']==s for m in geom)]),
                      'sats_nl':sorted([s for s in nl_fixed if any(m['sid']==s for m in geom)])}
        if nproc<=3 or nproc%240==0:
            print(f"  [{label}] SOD={sod:6.0f}  N={len(geom):2d}(G{len(geom)})  3D={d3:8.1f}mm"
                  f"  WL={len(wl_fixed)}  NL={len(nl_fixed)}"
                  f"  ZHD={ZHD:.3f}m  ZWD={ZWD:.4f}m  ZTD={TROPO:.4f}m"
                  f"  CodeRMS={code_rms:.1f}mm  PhsRMS={phase_rms:.2f}mm")

    print(f"[WL_DICT] size={len(wl_fixed)} keys={list(wl_fixed.keys())}")
    fwd_amb={sid:(x[ki],P[ki,ki]) for sid,ki in sidx.items() if phi.get(sid,False)}
    fwd_amb_out={sid:v for sid,v in fwd_amb.items() if sid in _amb_conv_sods}
    excluded={sid:f"pt={_amb_init_ptrace.get(sid,999):.3f}" for sid in fwd_amb if sid not in _amb_conv_sods}
    print(f"[AMB INHERIT] {len(fwd_amb_out)}/{len(fwd_amb)} sats (excluded: {excluded})")
    time_hours = np.array(time_list) / 3600.0
    err3d_m = np.array(err3d_list)
    # === REMOVE FIRST 5 HOUR ===
    mask = time_hours > 2
    plt.figure()
    plt.plot(time_hours[mask], err3d_m[mask])
    
    plt.xlabel("Time (hours)")
    plt.ylabel("3D Error (m)")
    plt.title("3D Error vs Time (After 2 Hour Convergence)")
    plt.grid()
    plt.show()
    return results,nom+x[:3],x[3],x[4],wl_fixed,fwd_amb_out,_amb_snapshots


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

    print("="*72)
    print("GPS-only PPP-AR v2 — full corrections, advanced sat selection")
    print("="*72)

    _,epochs,ah,ak=parse_obs(obs_f)
    sp3t,sp,sc=parse_sp3(sp3_f)
    clkd=parse_clk(clk_f) if clk_f else {}
    noisy_sats=_noisy_sats(clkd)   # compute once, pass into KF pass
    osb=parse_bia(bia_f) if bia_f else {}
    satx,recx_db={},{}
    if atx_f: satx,recx_db=parse_atx(atx_f)
    recx=recx_db.get(ak) or recx_db.get(ak.split()[0]+' NONE')
    if recx: print(f"[ATX]  Receiver '{ak}' found")
    else:    print(f"[ATX]  WARNING: '{ak}' not found")
    att={}
    if obx_f: att=parse_obx(obx_f)

    REF=np.array([1337935.5599,6070317.2377,1427877.5071])
    APX=np.array([1337936.455,6070317.126,1427876.785])
    tref=sp3t[0]; DOY=38
    lat0,_,h0=_lla(APX); zhd=_zhd(lat0,h0)
    print(f"[INIT] ZHD={zhd:.4f}m  h={h0:.0f}m  lat={math.degrees(lat0):.3f}deg")
    print(f"[MODEL] SatPCO/PCV:{len(satx)} PRNs  RecPCO/PCV:{'Y' if recx else 'N'}"
          f"  OBX:{len(att)} sats  OSB:{sum(len(v) for v in osb.values())} entries")
    print()

    print("[PASS 1] Forward ...")
    fwd,ex,ec,ez,wl_fwd,fwd_amb,fwd_snapshots=_ppp_pass(
        epochs,sp3t,sp,sc,clkd,osb,ah,APX.copy(),0.,0.10,
        lat0,DOY,zhd,tref,satx,att,recx,
        noisy_sats=noisy_sats,direction=1,label="FWD")
    print(f"  {len(fwd)} epochs  end_3D={np.linalg.norm(ex-REF)*1e3:.1f}mm  ZWD={ez:.3f}m")
    print(f"  WL fixed in FWD: {list(wl_fwd.keys())}  ({len(wl_fwd)} sats)")

    fwd_converged=sorted(fwd.items(),key=lambda kv: kv[1]['p_trace'])
    best60=fwd_converged[:min(60,len(fwd_converged))]
    if best60:
        avg_xyz=np.mean([r['xyz'] for _,r in best60],axis=0)
        lat_r,lon_r,_=_lla(REF); Re=_enu(lat_r,lon_r)
        diff=avg_xyz-REF; enu_diff=Re@diff
        dE_mm=enu_diff[0]*1e3; dN_mm=enu_diff[1]*1e3; dU_mm=enu_diff[2]*1e3
        diag3d=np.linalg.norm(diff)*1e3
        print(f"\n[DIAG] Best-60 FWD average converged position:")
        print(f"  Computed XYZ = [{avg_xyz[0]:.4f}, {avg_xyz[1]:.4f}, {avg_xyz[2]:.4f}] m")
        print(f"  REF      XYZ = [{REF[0]:.4f},   {REF[1]:.4f},  {REF[2]:.4f}] m")
        print(f"  Diff XYZ     = [{(avg_xyz[0]-REF[0])*1e3:+.2f}, {(avg_xyz[1]-REF[1])*1e3:+.2f}, "
              f"{(avg_xyz[2]-REF[2])*1e3:+.2f}] mm")
        print(f"  Rotated ENU  : dE={dE_mm:+.1f} mm  dN={dN_mm:+.1f} mm  dU={dU_mm:+.1f} mm")
        print(f"  3D = {diag3d:.1f} mm")

    rl=[(s,{**r,'pass':'FWD'}) for s,r in sorted(fwd.items())]
    lr,lo,_=_lla(REF); Re=_enu(lr,lo); fwd_list=sorted(fwd.items())
    print("\n"+"="*72)
    print(f"[RESULT] FWD-only  ({len(fwd_list)} epochs total)")
    for thresh in [50,100,200]:
        conv=[(s,r) for s,r in fwd_list if np.linalg.norm(r['dx'])*1e3<thresh]
        if not conv: continue
        da=np.array([r['dx'] for _,r in conv]); enu=(Re@da.T).T*1e3
        rms3d=math.sqrt(np.mean(np.sum(da**2,axis=1)))*1e3
        re=math.sqrt(np.mean(enu[:,0]**2)); rn=math.sqrt(np.mean(enu[:,1]**2)); ru=math.sqrt(np.mean(enu[:,2]**2))
        nl_epochs=sum(1 for _,r in conv if r.get('nl_fixed',0)>0)
        print(f"  Converged <{thresh:3d}mm: {len(conv):4d} epochs  RMS E/N/U={re:.1f}/{rn:.1f}/{ru:.1f}mm  "
              f"3D={rms3d:.1f}mm  NL-fixed epochs={nl_epochs}")
    bs,br=min(fwd_list,key=lambda x: np.linalg.norm(x[1]['dx'])); b3=np.linalg.norm(br['dx'])*1e3
    print(f"  Best epoch: SOD={bs:.0f}  3D={b3:.1f}mm  WL={br.get('wl_fixed',0)}  NL={br.get('nl_fixed',0)}")
    if len(fwd_list)>120:
        tail=fwd_list[-120:]; da_t=np.array([r['dx'] for _,r in tail])
        enu_t=(Re@da_t.T).T*1e3; r3t=math.sqrt(np.mean(np.sum(da_t**2,axis=1)))*1e3
        bias_enu=Re@np.mean(da_t,axis=0)*1e3
        print(f"  Last 120:  RMS E/N/U={math.sqrt(np.mean(enu_t[:,0]**2)):.1f}/"
              f"{math.sqrt(np.mean(enu_t[:,1]**2)):.1f}/{math.sqrt(np.mean(enu_t[:,2]**2)):.1f}mm  "
              f"3D={r3t:.1f}mm  Bias E={bias_enu[0]:+.1f} N={bias_enu[1]:+.1f} U={bias_enu[2]:+.1f}mm")
    if best60:
        nl_fix_total=sum(1 for _,r in fwd_list if r.get('nl_fixed',0)>0)
        print(f"\n  [ACCURACY SUMMARY]")
        print(f"  DIAG 3D = {diag3d:.1f} mm  (mean of best-60 converged epochs)")
        print(f"  Dominant: East bias {dE_mm:.1f} mm  N={dN_mm:.1f} mm  U={dU_mm:.1f} mm")
        print(f"  NL-fixed: {nl_fix_total}/{len(fwd_list)} epochs ({100*nl_fix_total/len(fwd_list):.1f}%)")
        print(f"  Best epoch: {b3:.1f} mm (SOD={bs:.0f})")
    all_3d=np.array([np.linalg.norm(r['dx'])*1e3 for _,r in fwd_list])
    all_code=np.array([r.get('code_rms',0.) for _,r in fwd_list])
    all_phase=np.array([r.get('phase_rms',0.) for _,r in fwd_list])
    post_idx=slice(120,None)
    print(f"\n  [SESSION STATISTICS]  (all {len(fwd_list)} epochs)")
    print(f"  3D error: mean={np.mean(all_3d):.1f} median={np.median(all_3d):.1f} "
          f"max={np.max(all_3d):.1f} min={np.min(all_3d):.1f} mm")
    print(f"  Code RMS: {math.sqrt(np.mean(all_code**2)):.1f} mm  "
          f"(post-conv: {math.sqrt(np.mean(all_code[post_idx]**2)):.1f} mm)")
    print(f"  Phase RMS: {math.sqrt(np.mean(all_phase**2)):.2f} mm  "
          f"(post-conv: {math.sqrt(np.mean(all_phase[post_idx]**2)):.2f} mm)")
    if b3<20.: print("  *** GOAL < 2 cm ACHIEVED ***")
    else: print(f"  (target <20mm)")
    print(f"  Wall: {_time.time()-t0:.1f}s")
    print("="*72)

    if outfile and rl:
        lr_csv,lo_csv,_=_lla(REF); Re_csv=_enu(lr_csv,lo_csv)
        with open(outfile,'w') as fo:
            fo.write("SOD,pass,Computed_X,Computed_Y,Computed_Z,REF_X,REF_Y,REF_Z,"
                     "DiffX_mm,DiffY_mm,DiffZ_mm,dE_mm,dN_mm,dU_mm,3D_mm,"
                     "N,WL_fixed,NL_fixed,ZHD_m,ZWD_m,ZTD_m,CodeRMS_mm,PhsRMS_mm\n")
            for sod,r in rl:
                xyz=r['xyz']; dx=r['dx']; dx_mm=dx*1e3; enu_mm=Re_csv@dx*1e3
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
    return 1


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
            INFILES,os.path.join(DATA,'ppp_gps_results.csv'))
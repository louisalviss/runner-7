#!/usr/bin/env python3
from __future__ import annotations
import csv, io, json, math, os, statistics, zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from marketdl.providers.histdata import HistDataProvider

SYMBOLS = ["EURUSD","GBPUSD","USDJPY","AUDUSD","USDCAD","USDCHF","NZDUSD","EURJPY","GBPJPY","EURGBP","XAUUSD"]
TFS = (5,10)
TPS = (1.5,2.0,2.3,2.5,3.0)
PRIMARY_TP = 2.3
START_YM=(2022,1)
END_YM=(2026,8)
OUT=Path(os.environ.get("WR_FX_OUT","/tmp/wr-fx-out"))
DATA=Path(os.environ.get("WR_FX_DATA","/tmp/wr-fx-data"))
OUT.mkdir(parents=True, exist_ok=True); DATA.mkdir(parents=True, exist_ok=True)

LEFT=10; RIGHT=10; EMA_LEN=21; EMA_SMOOTH=2; REGIME=12
ANGLE_PERIOD=4; ATR_ANGLE=10; ANGLE_LEVEL=5.0
CHOP_LEN=14; CHOP_MAX=50.0; SIGNAL_ATR=14; SIGNAL_RANGE_MAX=1.5

# HistData M1 bars are Bid OHLC, fixed EST (UTC-5), no DST.
EST_FIXED=timezone(timedelta(hours=-5))
VN=ZoneInfo("Asia/Ho_Chi_Minh")

# Approximate liquid retail/institutional round-trip friction proxy.
# One listed spread plus 20% slippage allowance. Stress = 2x this total.
SPREAD_PRICE = {
    "EURUSD":0.00008, "GBPUSD":0.00010, "USDJPY":0.009,
    "AUDUSD":0.00009, "USDCAD":0.00011, "USDCHF":0.00011,
    "NZDUSD":0.00012, "EURJPY":0.012, "GBPJPY":0.016,
    "EURGBP":0.00009, "XAUUSD":0.30,
}
COST_MULT_BASE=1.2
COST_MULT_STRESS=2.4

@dataclass
class Bar:
    ot:int; ct:int; o:float; h:float; l:float; c:float

@dataclass
class Plan:
    d:int; e:float; s:float; t:float; sig_i:int; sig_t:int; sig_h:float; sig_l:float

@dataclass
class Trade:
    symbol:str; tf:int; tp_r:float; side:str
    signal_time:int; entry_time:int; exit_time:int
    entry:float; stop:float; target:float; exit_price:float
    exit_reason:str; gross_r:float; stop_dist:float
    net_base:float; net_stress:float; ambiguous:bool

def month_range(a,b):
    y,m=a
    while (y,m)<=b:
        yield y,m
        m+=1
        if m==13: y+=1; m=1

def infer_tick(bars, symbol):
    if symbol.endswith("JPY"): return 0.001
    if symbol=="XAUUSD": return 0.001
    return 0.00001

def parse_month_zip(path:Path):
    out=[]
    with zipfile.ZipFile(path) as z:
        name=next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as f:
            for raw in f:
                s=raw.decode(errors="ignore").strip()
                if not s: continue
                p=s.split(";")
                if len(p)<5: continue
                try:
                    dt=datetime.strptime(p[0],"%Y%m%d %H%M%S").replace(tzinfo=EST_FIXED)
                    ot=int(dt.timestamp()*1000)
                    o,h,l,c=map(float,p[1:5])
                    out.append(Bar(ot, ot+59999, o,h,l,c))
                except Exception:
                    pass
    return out

def download_one(job):
    sym,y,m=job
    p=HistDataProvider(platform="ASCII", timeout=45, max_retries=3)
    try:
        path=p.download_month(sym,y,m,timeframe="M1",output_dir=DATA)
        return sym,y,m,str(path),None
    except Exception as e:
        return sym,y,m,None,repr(e)

def download_all():
    jobs=[(s,y,m) for s in SYMBOLS for y,m in month_range(START_YM,END_YM)]
    missing=[]
    with ThreadPoolExecutor(max_workers=6) as ex:
        fs=[ex.submit(download_one,j) for j in jobs]
        done=0
        for q in as_completed(fs):
            sym,y,m,path,err=q.result(); done+=1
            if err: missing.append({"symbol":sym,"year":y,"month":m,"error":err[:180]})
            if done%50==0: print(f"DOWNLOAD {done}/{len(jobs)} missing={len(missing)}", flush=True)
    return missing

def load_symbol(sym):
    bars=[]
    for y,m in month_range(START_YM,END_YM):
        p=DATA/f"HISTDATA_COM_ASCII_{sym}_M1_{y:04d}{m:02d}.zip"
        if p.exists():
            try: bars.extend(parse_month_zip(p))
            except Exception as e: print("PARSE_FAIL",sym,y,m,repr(e),flush=True)
    d={b.ot:b for b in bars}
    return [d[k] for k in sorted(d)]

def agg(src,m):
    ms=m*60000; out=[]; key=None; g=[]
    def emit(g):
        if not g: return None
        if len(g)!=m: return None
        if any(g[j+1].ot-g[j].ot!=60000 for j in range(len(g)-1)): return None
        return Bar(g[0].ot,g[-1].ct,g[0].o,max(x.h for x in g),min(x.l for x in g),g[-1].c)
    for x in src:
        k=x.ot//ms
        if key is None:key=k
        if k!=key:
            z=emit(g)
            if z:out.append(z)
            g=[]; key=k
        g.append(x)
    z=emit(g)
    if z:out.append(z)
    return out

def ema(v,n):
    a=2/(n+1); out=[]; p=None
    for x in v:
        p=x if p is None else a*x+(1-a)*p
        out.append(p)
    return out

def rma(v,n):
    out=[None]*len(v); p=None; seed=[]
    for i,x in enumerate(v):
        if p is None:
            seed.append(x)
            if len(seed)==n:
                p=sum(seed)/n; out[i]=p
        else:
            p=(p*(n-1)+x)/n; out[i]=p
    return out

def roll(v,n,fn):
    out=[None]*len(v)
    for i in range(n-1,len(v)): out[i]=fn(v[i-n+1:i+1])
    return out

def pivots(v,left,right,high=True):
    base=[None]*len(v)
    for conf in range(left+right,len(v)):
        c=conf-right; w=v[c-left:c+right+1]; ext=max(w) if high else min(w)
        if v[c]==ext and all(x!=ext for x in v[c+1:c+right+1]): base[conf]=v[c]
    return [None]+base[:-1]

def calc_ind(b):
    c=[x.c for x in b]; h=[x.h for x in b]; l=[x.l for x in b]
    e=ema(c,EMA_LEN); tr=[]
    for i,x in enumerate(b):
        tr.append(x.h-x.l if i==0 else max(x.h-x.l,abs(x.h-b[i-1].c),abs(x.l-b[i-1].c)))
    a10=rma(tr,ATR_ANGLE); a14=rma(tr,SIGNAL_ATR)
    tsum=roll(tr,CHOP_LEN,sum); rh=roll(h,CHOP_LEN,max); rl=roll(l,CHOP_LEN,min)
    ph=pivots(h,LEFT,RIGHT,True); pl=pivots(l,LEFT,RIGHT,False)
    res=sup=None; above=below=0; angles=[None]*len(b); out=[]
    for i,x in enumerate(b):
        if ph[i] is not None and ph[i]!=res: res=ph[i]
        if pl[i] is not None and pl[i]!=sup: sup=pl[i]
        above=above+1 if x.c>e[i] else 0
        below=below+1 if x.c<e[i] else 0
        eu=None if i<EMA_SMOOTH else e[i]>=e[i-EMA_SMOOTH]
        an=None
        if i>=ANGLE_PERIOD and a10[i] not in (None,0):
            an=math.degrees(math.atan((e[i]-e[i-ANGLE_PERIOD])/a10[i]/ANGLE_PERIOD))
        angles[i]=an
        outside=an is not None and (an>ANGLE_LEVEL or an<-ANGLE_LEVEL)
        ag=i>0 and an is not None and angles[i-1] is not None and an>angles[i-1] and outside
        ar=i>0 and an is not None and angles[i-1] is not None and an<angles[i-1] and outside
        ch=None
        if tsum[i] is not None and rh[i] is not None and rh[i]>rl[i] and tsum[i]>0:
            ch=100*math.log10(tsum[i]/(rh[i]-rl[i]))/math.log10(CHOP_LEN)
        sra=None if a14[i] in (None,0) else (x.h-x.l)/a14[i]
        out.append(dict(ema=e[i],ema_up=eu,ha=above>=REGIME,hb=below>=REGIME,
                        ag=ag,ar=ar,chop_ok=ch is not None and ch<CHOP_MAX,
                        sra_ok=sra is not None and sra<=SIGNAL_RANGE_MAX,res=res,sup=sup))
    return out

def pathseq(x):
    return [x.o,x.h,x.l,x.c] if abs(x.o-x.h)<abs(x.o-x.l) else [x.o,x.l,x.h,x.c]

def cross(a,z,p): return min(a,z)<=p<=max(a,z)

def next_bracket(plan,x,start_at=None):
    pts=pathseq(x); active=start_at is None; cur=pts[0]
    if active:
        if plan.d==1 and x.o<=plan.s:return "SL",x.o
        if plan.d==1 and x.o>=plan.t:return "TP",x.o
        if plan.d==-1 and x.o>=plan.s:return "SL",x.o
        if plan.d==-1 and x.o<=plan.t:return "TP",x.o
    for z in pts[1:]:
        pos=cur
        while True:
            if not active:
                enter=(plan.d==1 and pos<=plan.e<=z) or (plan.d==-1 and pos>=plan.e>=z)
                if not enter:break
                pos=plan.e; active=True; continue
            cand=[]
            if cross(pos,z,plan.s) and abs(plan.s-pos)>1e-15:cand.append((abs(plan.s-pos),"SL",plan.s))
            if cross(pos,z,plan.t) and abs(plan.t-pos)>1e-15:cand.append((abs(plan.t-pos),"TP",plan.t))
            if not cand:break
            _,r,p=min(cand)
            return r,p
        cur=z
    return None,None

def vn_dt(ms): return datetime.fromtimestamp(ms/1000,tz=timezone.utc).astimezone(VN)
def zone_c(ms):
    d=vn_dt(ms); m=d.hour*60+d.minute
    return m>=1380 or m<60

def entry_filter_all(ms): return True
def entry_filter_zonec(ms): return zone_c(ms)

def run_engine(sym,tf,bars,tp_r,entry_filter):
    if len(bars)<200:return []
    tick=infer_tick(bars,sym)
    ind=calc_ind(bars); pending=active=None; entry_t=None; trades=[]
    cost_base=SPREAD_PRICE[sym]*COST_MULT_BASE
    cost_stress=SPREAD_PRICE[sym]*COST_MULT_STRESS
    def close_trade(i,reason,px):
        nonlocal active,entry_t
        both=(bars[i].h>=max(active.s,active.t) and bars[i].l<=min(active.s,active.t)
              and reason in ("TP","SL"))
        if both: reason="AMBIG->SL"
        sd=abs(active.e-active.s)
        if reason=="TP": gr=tp_r
        elif reason in ("SL","AMBIG->SL"): gr=-1.0
        else: gr=((px-active.e)*(1 if active.d==1 else -1))/sd
        trades.append(Trade(sym,tf,tp_r,"LONG" if active.d==1 else "SHORT",
                            active.sig_t,entry_t,bars[i].ct,active.e,active.s,active.t,px,
                            reason,gr,sd,gr-cost_base/sd,gr-cost_stress/sd,both))
        active=None; entry_t=None
    for i,x in enumerate(bars):
        closed=False
        if active is not None:
            r,px=next_bracket(active,x,None)
            if r: close_trade(i,r,px); closed=True
        if active is None and pending is not None and i==pending.sig_i+1 and not closed:
            eps=tick*1e-6
            fill=(pending.d==1 and x.h+eps>=pending.e) or (pending.d==-1 and x.l-eps<=pending.e)
            if fill:
                active=pending; pending=None; entry_t=x.ot
                r,px=next_bracket(active,x,active.e)
                if r:close_trade(i,r,px);closed=True
        if active is not None and not closed:
            z=ind[i]
            le=active.d==1 and x.c<z["ema"] and not z["ha"] and not z["ema_up"]
            se=active.d==-1 and x.c>z["ema"] and not z["hb"] and bool(z["ema_up"])
            if le or se:
                close_trade(i,"EMA",x.c);closed=True
        if pending is not None and i>=pending.sig_i+1 and active is None:
            pending=None
        if active is None and pending is None and not closed and entry_filter(x.ct):
            z=ind[i]
            lr=z["ha"] and x.c>z["ema"] and z["ag"] and z["chop_ok"] and z["res"] is not None
            sr=z["hb"] and x.c<z["ema"] and z["ar"] and z["chop_ok"] and z["sup"] is not None
            nl=z["sra_ok"] and x.c<x.o and lr and x.c>z["res"] and x.l<=z["res"]
            ns=z["sra_ok"] and x.c>x.o and sr and x.c<z["sup"] and x.h>=z["sup"]
            if nl:
                e=x.h+tick; s=x.l-tick; t=e+tp_r*(e-s)
                pending=Plan(1,e,s,t,i,x.ct,x.h,x.l)
            elif ns:
                e=x.l-tick; s=x.h+tick; t=e-tp_r*(s-e)
                pending=Plan(-1,e,s,t,i,x.ct,x.h,x.l)
    return trades

def maxdd(vals):
    eq=peak=0.0; mdd=0.0
    for x in vals:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return mdd

def metrics(ts):
    ts=sorted(ts,key=lambda t:(t.signal_time,t.symbol))
    b=[t.net_base for t in ts]; s=[t.net_stress for t in ts]; g=[t.gross_r for t in ts]
    return {
        "n":len(ts),
        "gross":round(sum(g),4),
        "net_base":round(sum(b),4),
        "avg_base":round(statistics.mean(b),5) if b else None,
        "net_stress":round(sum(s),4),
        "avg_stress":round(statistics.mean(s),5) if s else None,
        "win_rate":round(sum(x>0 for x in b)/len(b),4) if b else None,
        "dd_base":round(maxdd(b),4),
    }

def split_year(ts):
    out={}
    for y in (2022,2023,2024,2025,2026):
        z=[t for t in ts if datetime.fromtimestamp(t.signal_time/1000,tz=timezone.utc).year==y]
        out[str(y)]=metrics(z)
    return out

def verdict(primary):
    yrs=primary["years"]
    dev=sum(yrs[str(y)]["net_base"] for y in (2022,2023,2024))
    val=yrs["2025"]["net_base"]; oos=yrs["2026"]["net_base"]
    val_s=yrs["2025"]["net_stress"]; oos_s=yrs["2026"]["net_stress"]
    if primary["all"]["n"]<80:return "INSUFFICIENT"
    if dev>0 and val>0 and oos>0 and val_s>0 and oos_s>0:return "PASS_STRICT"
    if val>0 and oos>0:return "PASS_WEAK"
    return "FAIL"

def portfolio_day_norm(ts):
    groups=defaultdict(list)
    for t in ts: groups[vn_dt(t.signal_time).date().isoformat()].append(t.net_base)
    vals=[sum(v)/len(v) for _,v in sorted(groups.items())]
    return {"days":len(vals),"net":round(sum(vals),4),"avg_day":round(statistics.mean(vals),5) if vals else None,
            "positive_days":sum(v>0 for v in vals),"dd":round(maxdd(vals),4)}

def main():
    print("START download",flush=True)
    missing=download_all()
    print("DOWNLOAD_DONE missing",len(missing),flush=True)
    result={"source":"HistData ASCII M1 Bid; fixed EST no DST","period":"2022-01 through 2026-08 available data",
            "symbols":SYMBOLS,"cost_model":{"base":"1.2x assumed spread","stress":"2.4x assumed spread",
            "spread_price":SPREAD_PRICE},"missing_months":missing,"results":{}}
    primary_zonec=[]
    for sym in SYMBOLS:
        m1=load_symbol(sym)
        if not m1:
            result["results"][sym]={"error":"NO_DATA"}; continue
        cov={"first":datetime.fromtimestamp(m1[0].ot/1000,tz=timezone.utc).isoformat(),
             "last":datetime.fromtimestamp(m1[-1].ot/1000,tz=timezone.utc).isoformat(),"m1":len(m1)}
        sr={"coverage":cov,"tf":{}}
        print("SYMBOL",sym,cov,flush=True)
        for tf in TFS:
            bars=agg(m1,tf); tfr={"bars":len(bars),"tp":{}}
            for tp in TPS:
                alltr=run_engine(sym,tf,bars,tp,entry_filter_all)
                zctr=run_engine(sym,tf,bars,tp,entry_filter_zonec)
                key=str(tp)
                tfr["tp"][key]={"all":metrics(alltr),"all_years":split_year(alltr),
                                "zonec":metrics(zctr),"zonec_years":split_year(zctr)}
                if tf==10 and abs(tp-PRIMARY_TP)<1e-9:
                    primary={"all":metrics(alltr),"years":split_year(alltr)}
                    tfr["primary_verdict_all"]=verdict(primary)
                    pz={"all":metrics(zctr),"years":split_year(zctr)}
                    tfr["primary_verdict_zonec"]=verdict(pz)
                    primary_zonec.extend(zctr)
            sr["tf"][str(tf)]=tfr
        result["results"][sym]=sr
    result["portfolio_day_norm_zonec_10m_tp2.3"]=portfolio_day_norm(primary_zonec)
    prim=[]
    for t in primary_zonec: prim.append(t)
    result["zonec_10m_tp2.3_universe"]=metrics(prim)
    result["zonec_10m_tp2.3_universe_years"]=split_year(prim)
    out=OUT/"wr_fx_results.json"; out.write_text(json.dumps(result,indent=2))
    print("RESULT_SUMMARY_BEGIN")
    compact={
      "period":result["period"],
      "missing_months":len(missing),
      "portfolio_day_norm":result["portfolio_day_norm_zonec_10m_tp2.3"],
      "universe":result["zonec_10m_tp2.3_universe"],
      "universe_years":result["zonec_10m_tp2.3_universe_years"],
      "symbols":{}
    }
    for sym,r in result["results"].items():
        if "error" in r: compact["symbols"][sym]=r; continue
        tf10=r["tf"]["10"]
        p=tf10["tp"]["2.3"]
        compact["symbols"][sym]={
          "coverage":r["coverage"],"all":p["all"],"all_years":p["all_years"],
          "zonec":p["zonec"],"zonec_years":p["zonec_years"],
          "verdict_all":tf10.get("primary_verdict_all"),"verdict_zonec":tf10.get("primary_verdict_zonec"),
          "tp_sensitivity_zonec":{k:v["zonec"] for k,v in tf10["tp"].items()}
        }
    print(json.dumps(compact,indent=2))
    print("RESULT_SUMMARY_END")
    print("RESULT_PATH",out)
if __name__=="__main__":
    main()

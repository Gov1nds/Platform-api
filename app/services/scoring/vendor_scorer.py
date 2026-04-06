"""Multi-factor vendor scoring with market data integration and freshness awareness."""
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.market import FXRate, FreightRate

DEFAULT_WEIGHTS = {
    "capability_match":0.22,"price_competitiveness":0.18,"lead_time":0.15,
    "reliability":0.14,"logistics_fit":0.10,"compliance":0.09,
    "capacity":0.05,"freshness":0.07,
}
LABELS = {
    "capability_match":"Capability Match","price_competitiveness":"Price","lead_time":"Lead Time",
    "reliability":"Reliability","logistics_fit":"Logistics","compliance":"Compliance",
    "capacity":"Capacity","freshness":"Data Freshness",
}

def load_market_context(db: Session, delivery_region: str, currency: str) -> dict:
    ctx = {"fx_rate":1.0,"freight_per_kg":None,"data_age_days":None}
    if currency and currency != "USD":
        fx = db.query(FXRate).filter(FXRate.base_currency=="USD",FXRate.quote_currency==currency).order_by(FXRate.effective_from.desc()).first()
        if fx:
            ctx["fx_rate"] = float(fx.rate)
            age = (datetime.now(timezone.utc) - fx.effective_from.replace(tzinfo=timezone.utc) if fx.effective_from.tzinfo is None else (datetime.now(timezone.utc) - fx.effective_from))
            ctx["data_age_days"] = age.days
    if delivery_region:
        fr = db.query(FreightRate).filter(FreightRate.destination_region.ilike(f"%{delivery_region}%")).order_by(FreightRate.effective_from.desc()).first()
        if fr and fr.rate_per_kg:
            ctx["freight_per_kg"] = float(fr.rate_per_kg)
    return ctx

def score_vendor(vendor: dict, requirements: dict, market_ctx: dict, weights: dict|None=None) -> dict:
    w = weights or DEFAULT_WEIGHTS
    bd = {}
    bd["capability_match"] = _cap(vendor.get("capabilities",[]),requirements.get("processes",[]),requirements.get("materials",[]))
    bd["price_competitiveness"] = _price(vendor.get("typical_unit_price"),market_ctx.get("market_median_price"))
    bd["lead_time"] = _lt(vendor.get("avg_lead_time_days"),requirements.get("target_lead_time_days",30))
    bd["reliability"] = min(1.0, max(0.0, float(vendor.get("reliability_score",0.5))))
    bd["logistics_fit"] = _logfit(vendor.get("regions_served",[]),requirements.get("delivery_region",""))
    bd["compliance"] = _comp(vendor.get("certifications",[]),requirements.get("required_certifications",[]))
    bd["capacity"] = _capac(vendor.get("capacity_profile",{}),requirements.get("total_quantity",0))
    age = market_ctx.get("data_age_days")
    bd["freshness"] = 1.0 if age is None else max(0.0,1.0 - (age/90))
    total = sum(w.get(k,0)*v for k,v in bd.items())
    expl_parts = []
    for f,s in sorted(bd.items(), key=lambda x: w.get(x[0],0)*x[1], reverse=True):
        lbl = LABELS.get(f,f)
        tag = "strong" if s>=0.8 else "moderate" if s>=0.5 else "weak"
        expl_parts.append(f"{lbl}: {tag} ({s:.0%})")
    return {
        "total_score":round(total,4),
        "breakdown":{k:round(v,4) for k,v in bd.items()},
        "weights":w,
        "explanation":"; ".join(expl_parts),
        "explanation_json":{k:{"score":round(v,4),"weight":w.get(k,0),"contribution":round(w.get(k,0)*v,4)} for k,v in bd.items()},
        "market_freshness":"fresh" if (age is None or age<7) else "stale" if age>30 else "recent",
    }

def rank_vendors(vendors:list, requirements:dict, market_ctx:dict, weights:dict|None=None) -> list:
    scored = []
    for v in vendors:
        r = score_vendor(v, requirements, market_ctx, weights)
        r["vendor_id"]=v["id"]; r["vendor_name"]=v.get("name","")
        scored.append(r)
    scored.sort(key=lambda x:x["total_score"],reverse=True)
    for i,s in enumerate(scored): s["rank"]=i+1
    return scored

def _cap(caps,procs,mats):
    if not procs and not mats: return 0.5
    t=len(procs)+len(mats); m=0
    cp={c.get("process","").lower() for c in caps}
    cm={c.get("material_family","").lower() for c in caps}
    for p in procs:
        if p.lower() in cp: m+=1
    for mt in mats:
        if any(mt.lower() in c for c in cm): m+=1
    return min(1.0,m/max(t,1))

def _price(vp,mm):
    if not vp or not mm: return 0.5
    r=float(vp)/float(mm)
    if r<=0.8: return 1.0
    if r>=1.5: return 0.0
    return max(0.0,1.0-(r-0.8)/0.7)

def _lt(vlt,tlt):
    if not vlt or not tlt: return 0.5
    r=float(vlt)/float(tlt)
    if r<=0.5: return 1.0
    if r>=2.0: return 0.0
    return max(0.0,1.0-(r-0.5)/1.5)

def _logfit(rs,dr):
    if not dr or not rs: return 0.5
    d=dr.lower()
    return 1.0 if any(d in str(r).lower() for r in rs) else 0.3

def _comp(vc,rc):
    if not rc: return 1.0
    s={str(c).upper() for c in vc}
    return sum(1 for c in rc if str(c).upper() in s)/len(rc)

def _capac(cp,q):
    if not q or not cp: return 0.5
    mx=cp.get("max_monthly_units",0)
    if mx<=0: return 0.5
    if float(q)<=mx*0.5: return 1.0
    if float(q)>mx: return 0.1
    return 0.6

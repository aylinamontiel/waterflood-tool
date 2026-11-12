from __future__ import annotations
import numpy as np
import pandas as pd
from scipy.optimize import brentq, curve_fit

def _norm(c) -> str:
    return " ".join(str(c).replace("_", " ").strip().lower().split())

def _find(df: pd.DataFrame, *names: str) -> str:
    cols = {_norm(c): c for c in df.columns}
    for n in names:
        if n in cols: return cols[n]
    raise ValueError(f"Missing column '{names[0]}'. Columns found: {list(df.columns)}")

def _col_or_zero(df: pd.DataFrame, name: str) -> pd.Series:
    try:
        return pd.to_numeric(df[_find(df, name)], errors="coerce").fillna(0.0)
    except ValueError:
        return pd.Series([0.0] * len(df))

def clean_prices(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "Date": pd.to_datetime(df[_find(df, "date")]),
        "NYMEX": pd.to_numeric(df[_find(df, "nymex")], errors="coerce"),
        "WTI": pd.to_numeric(df[_find(df, "wti")], errors="coerce"),
        "Oil Diff": _col_or_zero(df, "oil diff"),
        "Gas Diff": _col_or_zero(df, "gas diff"),
    })
    out = out.dropna(subset=["Date", "NYMEX", "WTI"])
    out["Ym"] = out["Date"].dt.to_period("M")
    out = out.sort_values("Date").drop_duplicates("Ym", keep="last")
    return out.reset_index(drop=True)

def clean_production(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "Date": pd.to_datetime(df[_find(df, "date")]),
        "Oil": pd.to_numeric(df[_find(df, "oil")], errors="coerce"),
        "Gas": pd.to_numeric(df[_find(df, "gas")], errors="coerce"),
    })
    for key, name in (("water", "Water"), ("well count", "Well Count")):
        out[name] = _col_or_zero(df, key)
            
    out = out.dropna(subset=["Date", "Oil"]).sort_values("Date").reset_index(drop=True)
    out["Ym"] = out["Date"].dt.to_period("M")
    out["qo"] = out["Oil"] / out["Date"].dt.days_in_month
    return out

def summarize_wells(df: pd.DataFrame) -> dict:
    try: ptype_col = df[_find(df, "production type", "type")].astype(str).str.upper()
    except ValueError: ptype_col = pd.Series(["UNKNOWN"] * len(df))
    
    try: raw_status = df[_find(df, "well status", "status")].astype(str).str.upper()
    except ValueError: raw_status = pd.Series(["UNKNOWN"] * len(df))
    
    try: tvd_col = pd.to_numeric(df[_find(df, "true vertical depth", "tvd")], errors="coerce")
    except ValueError: tvd_col = pd.Series([np.nan] * len(df))

    d = pd.DataFrame({"ptype": ptype_col, "raw_status": raw_status, "tvd": tvd_col})
    d["inj"] = d["ptype"].str.contains("INJ")

    # SOLUCIÓN DEL ERROR: Envolvemos 's' en str() para forzarlo a texto y evitar el fallo del float.
    def _map_status(s):
        s_cl = str(s).upper().replace("&", "").replace("/", "").replace(" ", "")
        if "PA" in s_cl or "PLUG" in s_cl or ("ABANDON" in s_cl and "TEMP" not in s_cl): return "PA"
        if "TA" in s_cl or "INACT" in s_cl or "SI" in s_cl or "SHUT" in s_cl or "TEMP" in s_cl: return "INACTIVE"
        if "ACT" in s_cl or "PROD" in s_cl: return "ACTIVE"
        return "OTHER"

    d["status"] = d["raw_status"].map(_map_status)

    groups = {
        "active_oil": d[~d.inj & (d.status == "ACTIVE")],
        "active_inj": d[d.inj & (d.status == "ACTIVE")],
        "inactive_ta": d[d.status == "INACTIVE"],
        "pa_wells": d[d.status == "PA"]
    }
    
    info = {"total_wells": len(d)}
    info["total_tvd_sum"] = float(d["tvd"].dropna().sum())
    
    for key, g in groups.items():
        tvd_clean = g["tvd"].dropna()
        avg = float(tvd_clean.mean()) if not tvd_clean.empty else np.nan
        tmin = float(tvd_clean.min()) if not tvd_clean.empty else np.nan
        tmax = float(tvd_clean.max()) if not tvd_clean.empty else np.nan
        t_sum = float(tvd_clean.sum()) if not tvd_clean.empty else 0.0
        info[key] = {"n": int(len(g)), "tvd_avg": avg, "tvd_min": tmin, "tvd_max": tmax, "tvd_sum": t_sum}
        
    others = d[d["status"] == "OTHER"]
    info["other_statuses"] = others["raw_status"].value_counts().to_dict()
    
    return info

def audit_waterflood(df_prod: pd.DataFrame, wells_summary: dict) -> dict:
    audit_results = {}
    tvd_prod = wells_summary["active_oil"]["tvd_avg"]
    tvd_inj = wells_summary["active_inj"]["tvd_avg"]
    
    if pd.isna(tvd_prod) or pd.isna(tvd_inj) or wells_summary["active_inj"]["n"] == 0:
        audit_results["tvd_match"] = "N/A (Missing TVD or Injectors)"
        audit_results["tvd_pass"] = False
    else:
        diff_pct = abs(tvd_prod - tvd_inj) / tvd_prod
        audit_results["tvd_match"] = f"{diff_pct*100:.1f}% Difference"
        audit_results["tvd_pass"] = diff_pct <= 0.10

    last_12 = rate_window(df_prod, 12)
    sum_oil = last_12["Oil"].sum()
    sum_water = last_12["Water"].sum()
    
    if sum_water == 0:
        audit_results["water_signature"] = "No Water Data"
        audit_results["water_pass"] = False
    else:
        ratio = sum_water / sum_oil if sum_oil > 0 else sum_water
        audit_results["water_signature"] = f"{ratio:.1f}x Water to Oil Ratio"
        audit_results["water_pass"] = sum_water > sum_oil

    return audit_results

def _arps_q(t, qi, D, b):
    if b < 1e-9: return qi * np.exp(-D * t)
    return qi * np.power(np.clip(1.0 + b * D * t, 1e-9, None), -1.0 / b)

def _eff_decline(D: float, b: float) -> float:
    if b < 1e-9: return float(1.0 - np.exp(-D))
    return float(1.0 - (1.0 + b * D) ** (-1.0 / b))

def rate_window(prod: pd.DataFrame, months: int) -> pd.DataFrame:
    last = prod["Ym"].max()
    return prod[prod["Ym"] > last - months].reset_index(drop=True)

def fit_arps(rates, kind: str = "auto") -> dict:
    q = np.asarray(rates, float)
    n = len(q)
    if n < 6 or (q > 0).sum() < 4:
        return {"kind": "exp", "eff_decline": 0.0, "D_nominal": 0.0, "b": 0.0, "success": False}
    t = np.arange(n) / 12.0
    tmax = max(t[-1], 1e-6)
    
    pos = q > 0
    if pos.sum() < 4:
        return {"kind": "exp", "eff_decline": 0.0, "D_nominal": 0.0, "b": 0.0, "success": False}
        
    slope, icpt = np.polyfit(t[pos], np.log(q[pos]), 1)
    qi0, D0 = float(np.exp(icpt)), float(-slope)
    qmax = float(q.max()) * 5
    dmin = -0.9 / tmax 

    cands = {}
    def _try(name, f, p0, lo, hi, b, k):
        try:
            p, _ = curve_fit(f, t, q, p0=p0, bounds=(lo, hi), maxfev=50000)
            sse = float(np.sum((q - f(t, *p)) ** 2))
            cands[name] = {"qi": float(p[0]), "D": float(p[1]), "b": float(p[2]) if k == 3 else b, "sse": sse, "k": k}
        except: pass

    _try("exp", lambda t, qi, D: _arps_q(t, qi, D, 0.0), [qi0, np.clip(D0, dmin, 20)], [0, dmin], [qmax, 20], 0.0, 2)
    _try("harm", lambda t, qi, D: _arps_q(t, qi, D, 1.0), [qi0, np.clip(D0, dmin, 20)], [0, dmin], [qmax, 20], 1.0, 2)
    
    if not cands: return {"kind": "exp", "eff_decline": 0.0, "D_nominal": 0.0, "b": 0.0, "success": False}
    if kind == "auto":
        kind = min(cands, key=lambda k: n * np.log(max(cands[k]["sse"], 1e-12) / n) + cands[k]["k"] * np.log(n))
    
    c = cands[kind]
    return {"kind": kind, "eff_decline": _eff_decline(c["D"], c["b"]), "success": True}

def base_prices(prices: pd.DataFrame, prod: pd.DataFrame, months: int = 12) -> dict:
    last = prod["Ym"].max()
    win = prod[prod["Ym"] > last - months].merge(prices[["Ym", "WTI", "NYMEX", "Oil Diff", "Gas Diff"]], on="Ym", how="left")
    po = win["WTI"] + win["Oil Diff"]
    pg = win["NYMEX"] + win["Gas Diff"]
    oil_rev = float((win["Oil"] * po).sum())
    gas_rev = float((win["Gas"] * pg).sum())
    tot = oil_rev + gas_rev
    return {"oil_base": float(po.mean()), "gas_base": float(pg.mean()), "oil_share": oil_rev / tot if tot > 0 else 1.0}

def calculate_abandonment(interest_type: str, n_inactive_wells: int, wi_pct: float, tvd_avg: float, cost_per_ft: float = 7.0, months: int = 12, mode: str = "Lump Sum", spread_months: int = 12, exact_tvd_sum: float = None) -> dict:
    if interest_type.upper() in ["NRI", "ORRI"]:
        return {"per_well": 0.0, "total": 0.0, "schedule": np.zeros(months)}
    
    if exact_tvd_sum is not None and exact_tvd_sum > 0:
        total = exact_tvd_sum * wi_pct * cost_per_ft
    else:
        total = n_inactive_wells * tvd_avg * wi_pct * cost_per_ft
        
    per_well = total / n_inactive_wells if n_inactive_wells > 0 else 0.0
    sched = np.zeros(months)
    if mode == "Lump Sum":
        sched[0] = total
    else:
        k = min(spread_months, months)
        if k > 0: sched[0:k] = total / k
    return {"per_well": per_well, "total": total, "schedule": sched}

def build_forecast(*, months: int, lycf: float, decline: float, prices: pd.DataFrame, base_prices_ratios: dict, start: pd.Timestamp, last_prod: pd.Period, abandon_schedule: np.ndarray) -> pd.DataFrame:
    idx = pd.period_range(start, periods=months, freq="M")
    lag = (idx[0] - last_prod).n - 1  
    m = np.arange(1, months + 1) + max(lag, 0)
    
    vol = (1.0 - max(decline, 0.0)) ** (m / 12.0)

    p = prices.set_index("Ym")
    oil = (p["WTI"] + p["Oil Diff"]).reindex(idx).ffill().bfill().to_numpy()
    gas = (p["NYMEX"] + p["Gas Diff"]).reindex(idx).ffill().bfill().to_numpy()
    
    ob = base_prices_ratios["oil_base"] if base_prices_ratios["oil_base"] > 0 else 1.0
    gb = base_prices_ratios["gas_base"] if base_prices_ratios["gas_base"] > 0 else 1.0
    pf = (base_prices_ratios["oil_share"] * oil / ob + (1 - base_prices_ratios["oil_share"]) * gas / gb)

    cf_gross = (lycf / 12.0) * vol * pf
    ab = np.asarray(abandon_schedule)[:months]
    
    out = pd.DataFrame({"Date": idx.to_timestamp(how="end").normalize(), "Gross CF ($)": cf_gross, "Abandonment ($)": -ab})
    out["Net CF ($)"] = out["Gross CF ($)"] + out["Abandonment ($)"]
    return out

def xirr(amounts, dates) -> float:
    a = np.asarray(amounts, float)
    d = pd.to_datetime(pd.Series(list(dates)))
    t = ((d - d.iloc[0]).dt.days / 365.0).to_numpy()
    def f(r): return float(np.sum(a / np.power(1.0 + r, t)))
    try:
        lo, hi = -0.99, 1000.0
        if f(lo) * f(hi) > 0: return float("nan")
        return float(brentq(f, lo, hi, maxiter=500))
    except Exception: return float("nan")

def evaluate_cashflow(cf_neto: pd.Series, dates, fixed_offer: float = None) -> dict:
    cf = np.asarray(cf_neto, float)
    d = pd.to_datetime(pd.Series(list(dates)))
    
    offer_date = d.iloc[0] - pd.DateOffset(months=1)
    t = ((d - offer_date).dt.days / 365.0).to_numpy()
    
    year1_cf = float(cf[:12].sum())
    
    pv_175 = float(np.sum(cf / (1.0 + 0.175) ** t))
    pv_250 = float(np.sum(cf / (1.0 + 0.250) ** t))
    
    offer_amount = fixed_offer if fixed_offer is not None else pv_175
    
    amounts = np.concatenate([[-offer_amount], cf])
    dts = [offer_date] + list(d) 
    
    return {
        "Estimated Offer ($)": offer_amount,
        "Year 1 CF ($)": year1_cf,
        "Annual CF Multiple (x)": offer_amount / year1_cf if year1_cf > 0 else float("nan"),
        "IRR (%)": xirr(amounts, dts) * 100,
        "PV 17.5% ($)": pv_175,
        "PV 25.0% ($)": pv_250,
        "Total Net CF ($)": float(cf.sum())
    }

from __future__ import annotations
import re
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
    out = (out.groupby("Ym", as_index=False)
              .agg({"Date": "min", "Oil": "sum", "Gas": "sum", "Water": "sum", "Well Count": "max"})
              .sort_values("Date").reset_index(drop=True))
    out["qo"] = out["Oil"] / out["Date"].dt.days_in_month
    return out

def summarize_wells(df: pd.DataFrame) -> dict:
    omitted_count = 0
    df_clean = df.copy()

    # --- FILTRO AUTOMÁTICO DE DUPLICADOS (STs) CON JERARQUÍA ---
    try:
        api_col = _find(df_clean, "api14", "api 14", "api", "uwi")
        original_count = len(df_clean)

        def _api10(v):
            t = str(v).strip()
            if t.lower() in ("", "nan", "none", "nat"): return np.nan
            t = re.sub(r"\.0+$", "", t)            
            t = re.sub(r"\D", "", t)
            if not t: return np.nan
            t = t.zfill(10) if len(t) <= 10 else t.zfill(14)   
            return t[:10]

        df_clean["API10"] = df_clean[api_col].map(_api10)

        try:
            fp_col = _find(df_clean, "first prod", "first prod date")
            has_prod = df_clean[fp_col].notna() & (df_clean[fp_col].astype(str).str.strip() != "")
        except ValueError:
            has_prod = False

        try:
            status_col = _find(df_clean, "well status", "status")
            st_up = df_clean[status_col].astype(str).str.upper()
            is_active = st_up.str.contains("ACT|PROD") & ~st_up.str.contains("INACT|NON")
        except ValueError:
            is_active = False

        df_clean["_prio_prod"] = has_prod
        df_clean["_prio_act"] = is_active
        df_clean["_api_sort"] = df_clean[api_col].astype(str)

        df_clean = df_clean.sort_values(by=["_prio_prod", "_prio_act", "_api_sort"], ascending=[False, False, True], kind="mergesort")

        has_api = df_clean["API10"].notna()
        deduped = df_clean[has_api].drop_duplicates(subset=["API10"], keep="first")
        df_clean = pd.concat([deduped, df_clean[~has_api]]).copy()

        omitted_count = original_count - len(df_clean)
    except ValueError:
        pass 
    df_clean = df_clean.reset_index(drop=True)

    try: ptype_col = df_clean[_find(df_clean, "production type", "type")].astype(str).str.upper()
    except ValueError: ptype_col = pd.Series(["OIL"] * len(df_clean))  
    
    try: raw_status = df_clean[_find(df_clean, "well status", "status")].astype(str).str.upper()
    except ValueError: raw_status = pd.Series(["UNKNOWN"] * len(df_clean))
    
    try: tvd_col = pd.to_numeric(df_clean[_find(df_clean, "true vertical depth", "tvd")], errors="coerce")
    except ValueError: tvd_col = pd.Series([np.nan] * len(df_clean))
    
    # Extraer columna WI si existe
    try:
        wi_col = _find(df_clean, "working interest", "wi", "wi decimal", "wi %")
        wi_series = pd.to_numeric(df_clean[wi_col], errors="coerce")
    except ValueError:
        wi_series = pd.Series([np.nan] * len(df_clean))

    d = pd.DataFrame({"ptype": ptype_col, "raw_status": raw_status, "tvd": tvd_col, "wi": wi_series})

    def _map_type(s):
        t = str(s).upper()
        if "DISPOSAL" in t or re.search(r"\bSWD\b", t): return "SWD"
        if "INJ" in t: return "INJ"
        if "OIL" in t: return "OIL"
        if "GAS" in t: return "GAS"
        return "OTHER"

    d["wtype"] = d["ptype"].map(_map_type)
    d["inj"] = d["wtype"] == "INJ"

    def _map_status(s):
        t = re.sub(r"\s+", " ", str(s).upper().replace("_", " ")).strip()
        if re.search(r"TEMP\w*\.?\s*ABAND|\bTA\b|INACTIVE|SHUT[\s-]?IN|\bSI\b|NON[\s-]?PRODUC|\bTEMP", t): return "INACTIVE"
        if re.search(r"\bP\s*[&/]\s*A\b|\bPA\b|PLUGGED|ABANDON", t): return "PA"
        if re.search(r"\bACTIVE\b|PRODUC|INJECT", t): return "ACTIVE"
        return "OTHER"

    d["status"] = d["raw_status"].map(_map_status)

    groups = {
        "active_oil": d[(d.wtype == "OIL") & (d.status == "ACTIVE")],
        "active_inj": d[d.inj & (d.status == "ACTIVE")],
        "inactive_ta": d[d.status == "INACTIVE"],
        "pa_wells": d[d.status == "PA"]
    }
    
    info = {"total_wells": len(d), "omitted_sts": omitted_count}
    info["total_tvd_sum"] = float(d["tvd"].dropna().sum())
    
    liable = d[d["status"] != "PA"]
    info["liable_wells"] = int(len(liable))
    info["liable_tvd_sum"] = float(liable["tvd"].dropna().sum())
    info["liable_missing_tvd"] = int(liable["tvd"].isna().sum())
    
    # Exportar datos precisos pozo a pozo para calcular P&A
    info["liable_wells_data"] = {"tvd": liable["tvd"].tolist(), "wi": liable["wi"].tolist()}
    info["inactive_wells_data"] = {"tvd": groups["inactive_ta"]["tvd"].tolist(), "wi": groups["inactive_ta"]["wi"].tolist()}
    
    for key, g in groups.items():
        tvd_clean = g["tvd"].dropna()
        avg = float(tvd_clean.mean()) if not tvd_clean.empty else np.nan
        tmin = float(tvd_clean.min()) if not tvd_clean.empty else np.nan
        tmax = float(tvd_clean.max()) if not tvd_clean.empty else np.nan
        t_sum = float(tvd_clean.sum()) if not tvd_clean.empty else 0.0
        info[key] = {"n": int(len(g)), "tvd_avg": avg, "tvd_min": tmin, "tvd_max": tmax, "tvd_sum": t_sum}
        
    non_oil = d[(d.wtype != "OIL") & ~d.inj & (d.status == "ACTIVE")]
    info["active_non_oil"] = {
        "n": int(len(non_oil)),
        "gas": int((non_oil["wtype"] == "GAS").sum()),
        "swd": int((non_oil["wtype"] == "SWD").sum()),
        "other": int((non_oil["wtype"] == "OTHER").sum()),
    }

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
    elif sum_oil <= 0:
        audit_results["water_signature"] = "No oil production in the last 12 months"
        audit_results["water_pass"] = False
    else:
        ratio = sum_water / sum_oil
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
        except (RuntimeError, ValueError, TypeError, FloatingPointError, np.linalg.LinAlgError): pass 

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
    missing = win.loc[win["WTI"].isna() | win["NYMEX"].isna(), "Ym"].astype(str).tolist()
    if missing:
        raise ValueError(
            "The Price Input File does not cover the last 12 months of production "
            f"(missing prices for: {', '.join(missing)}). Use a price file that includes historical prices."
        )
    po = win["WTI"] + win["Oil Diff"]
    pg = win["NYMEX"] + win["Gas Diff"]
    oil_rev = float((win["Oil"] * po).sum())
    gas_rev = float((win["Gas"] * pg).sum())
    tot = oil_rev + gas_rev
    return {"oil_base": float(po.mean()), "gas_base": float(pg.mean()), "oil_share": oil_rev / tot if tot > 0 else 1.0}

def calculate_stressed_lycf(lycf: float, base_prices_ratios: dict, stressed_oil: float = 50.0, stressed_gas: float = 3.0) -> float:
    ob = base_prices_ratios["oil_base"] if base_prices_ratios["oil_base"] > 0 else 1.0
    gb = base_prices_ratios["gas_base"] if base_prices_ratios["gas_base"] > 0 else 1.0
    stressed_pf = (base_prices_ratios["oil_share"] * stressed_oil / ob + (1 - base_prices_ratios["oil_share"]) * stressed_gas / gb)
    return float(lycf * stressed_pf)

def calculate_abandonment(interest_type: str, wells_data: dict, n_override: int, default_wi: float, cost_per_ft: float = 7.0, months: int = 12, mode: str = "Lump Sum", spread_months: int = 12) -> dict:
    if interest_type.upper() in ["NRI", "ORRI"]:
        return {"per_well": 0.0, "total": 0.0, "schedule": np.zeros(months)}
    
    total = 0.0
    if n_override > 0:
        # Modo Override: se utiliza el promedio global porque no sabemos qué pozos exactos está inyectando el usuario
        tvd_list = [v for v in wells_data.get("tvd", []) if not pd.isna(v)]
        tvd_avg = sum(tvd_list)/len(tvd_list) if tvd_list else 0.0
        total = n_override * tvd_avg * default_wi * cost_per_ft
        n_wells = n_override
    else:
        # Cálculo pozo por pozo con sus propios TVD y WI (falla al WI global si no hay dato local)
        n_wells = len(wells_data.get("tvd", []))
        for tvd, wi in zip(wells_data.get("tvd", []), wells_data.get("wi", [])):
            tvd_val = 0.0 if pd.isna(tvd) else tvd
            wi_val = default_wi if pd.isna(wi) else wi
            total += tvd_val * wi_val * cost_per_ft
            
    per_well = total / n_wells if n_wells > 0 else 0.0
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
    
    decline = float(np.clip(decline, 0.0, 0.99))  
    vol = (1.0 - decline) ** (m / 12.0)

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

def evaluate_cashflow(cf_neto: pd.Series, dates, fixed_offer: float = None, gross_cf=None) -> dict:
    cf = np.asarray(cf_neto, float)
    d = pd.to_datetime(pd.Series(list(dates)))
    
    offer_date = d.iloc[0] - pd.DateOffset(months=1)
    t = ((d - offer_date).dt.days / 365.0).to_numpy()
    
    g = np.asarray(gross_cf, float) if gross_cf is not None else cf
    year1_cf = float(cf[:12].sum())
    year1_gross = float(g[:12].sum())
    
    pv_175 = float(np.sum(cf / (1.0 + 0.175) ** t))
    pv_250 = float(np.sum(cf / (1.0 + 0.250) ** t))
    
    offer_amount = fixed_offer if fixed_offer is not None else pv_175
    
    amounts = np.concatenate([[-offer_amount], cf])
    dts = [offer_date] + list(d) 
    
    return {
        "Estimated Offer ($)": offer_amount,
        "Year 1 CF ($)": year1_cf,
        "Year 1 Gross CF ($)": year1_gross,
        "Annual CF Multiple (x)": offer_amount / year1_gross if (offer_amount > 0 and year1_gross > 0) else float("nan"),
        "IRR (%)": xirr(amounts, dts) * 100 if offer_amount > 0 else float("nan"),
        "PV 17.5% ($)": pv_175,
        "PV 25.0% ($)": pv_250,
        "Total Net CF ($)": float(cf.sum())
    }

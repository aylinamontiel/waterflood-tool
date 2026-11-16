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

    # 1. Rescatar la columna WI primero
    try:
        wi_col = _find(df_clean, "working interest", "wi", "wi decimal", "wi %")
        df_clean["_wi_val"] = pd.to_numeric(df_clean[wi_col], errors="coerce")
    except ValueError:
        df_clean["_wi_val"] = np.nan

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

        # 2. Compartir el WI entre los duplicados (STs) antes de borrar para no perder el dato
        df_clean["_wi_val"] = df_clean.groupby("API10")["_wi_val"].transform(lambda x: x.ffill().bfill())

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
    
    # Construimos el DataFrame usando el WI rescatado
    d = pd.DataFrame({"ptype": ptype_col, "raw_status": raw_status, "tvd": tvd_col, "wi": df_clean["_wi_val"]})

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

    _try("exp

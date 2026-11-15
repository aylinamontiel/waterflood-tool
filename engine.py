from __future__ import annotations
import re
import numpy as np
import pandas as pd
from scipy.optimize import brentq, curve_fit

FORECAST_MONTHS = 360  # 30-year forecast horizon


# ----------------------------------------------------------------------------
# Column helpers
# ----------------------------------------------------------------------------
def _norm(c) -> str:
    return " ".join(str(c).replace("_", " ").strip().lower().split())


def _find(df: pd.DataFrame, *names: str) -> str:
    cols = {_norm(c): c for c in df.columns}
    for n in names:
        if n in cols:
            return cols[n]
    raise ValueError(f"Missing column '{names[0]}'. Columns found: {list(df.columns)}")


def _find_opt(df: pd.DataFrame, *names: str):
    """Like _find, but returns None instead of raising when the column is absent."""
    try:
        return _find(df, *names)
    except ValueError:
        return None


def _col_or_zero(df: pd.DataFrame, name: str) -> pd.Series:
    col = _find_opt(df, name)
    if col is None:
        return pd.Series(0.0, index=df.index)
    return pd.to_numeric(df[col], errors="coerce").fillna(0.0)


# ----------------------------------------------------------------------------
# Cleaning
# ----------------------------------------------------------------------------
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

    out = out.dropna(subset=["Date", "Oil"])
    out["Ym"] = out["Date"].dt.to_period("M")

    # DI exports can hold several rows per month (e.g. one per well).
    # Aggregate to ONE row per month so every row is a real time step in the decline fit.
    out = (
        out.groupby("Ym", as_index=False)
        .agg(Date=("Date", "min"), Oil=("Oil", "sum"), Gas=("Gas", "sum"),
             Water=("Water", "sum"), **{"Well Count": ("Well Count", "max")})
        .sort_values("Date")
        .reset_index(drop=True)
    )
    out["qo"] = out["Oil"] / out["Date"].dt.days_in_month
    return out


# ----------------------------------------------------------------------------
# Wells table
# ----------------------------------------------------------------------------
def _map_status(s) -> str:
    """Map a raw Drilling Info well status to ACTIVE / INACTIVE / PA / OTHER (word-boundary regex, no loose substrings)."""
    t = re.sub(r"\s+", " ", str(s).upper().replace("_", " ")).strip()
    # Inactive first, so 'TEMPORARILY ABANDONED' is never read as P&A
    if re.search(r"TEMP\w*\.?\s*ABAND|\bTA\b|INACTIVE|SHUT[\s-]?IN|\bSI\b|NON[\s-]?PRODUC|\bTEMP", t):
        return "INACTIVE"
    if re.search(r"\bP\s*[&/]\s*A\b|\bPA\b|PLUGGED|ABANDON", t):
        return "PA"
    if re.search(r"\bACTIVE\b|PRODUC|INJECT", t):
        return "ACTIVE"
    return "OTHER"


def _map_type(s) -> str:
    """Map a raw production type to OIL / INJ / SWD / GAS / OTHER / UNKNOWN."""
    t = str(s).upper().strip()
    if t in ("", "NAN", "NONE", "UNKNOWN"):
        return "UNKNOWN"
    if "DISPOSAL" in t or re.search(r"\bSWD\b", t):   # before INJ: disposal wells are not waterflood injectors
        return "SWD"
    if "INJ" in t:
        return "INJ"
    if "OIL" in t:
        return "OIL"
    if "GAS" in t:
        return "GAS"
    return "OTHER"


def _api10(v):
    t = str(v).strip()
    if t.lower() in ("", "nan", "none", "nat"):
        return np.nan
    t = re.sub(r"\.0+$", "", t)            # Excel float artifacts (e.g. 4212345678000.0)
    t = re.sub(r"\D", "", t)
    if not t:
        return np.nan
    t = t.zfill(10) if len(t) <= 10 else t.zfill(14)   # restore a lost leading zero
    return t[:10]


def summarize_wells(df: pd.DataFrame) -> dict:
    omitted_count = 0
    df_clean = df.copy().reset_index(drop=True)

    # --- Automatic duplicate (sidetrack) filter, with hierarchy ---
    api_col = _find_opt(df_clean, "api14", "api 14", "api", "uwi")
    status_col = _find_opt(df_clean, "well status", "status")
    if api_col is not None:
        original_count = len(df_clean)
        df_clean["API10"] = df_clean[api_col].map(_api10)

        # Priority 1: has a first production date
        fp_col = _find_opt(df_clean, "first prod", "first prod date")
        if fp_col is not None:
            has_prod = df_clean[fp_col].notna() & (df_clean[fp_col].astype(str).str.strip() != "")
        else:
            has_prod = pd.Series(False, index=df_clean.index)

        # Priority 2: ACTIVE well (INACTIVE / NON-PRODUCING do not count as active)
        if status_col is not None:
            is_active = df_clean[status_col].astype(str).map(_map_status) == "ACTIVE"
        else:
            is_active = pd.Series(False, index=df_clean.index)

        df_clean["_prio_prod"] = has_prod
        df_clean["_prio_act"] = is_active
        df_clean["_api_sort"] = df_clean[api_col].astype(str)

        # Order: 1) has Prod Date, 2) is Active, 3) original API (0000 first)
        df_clean = df_clean.sort_values(by=["_prio_prod", "_prio_act", "_api_sort"],
                                        ascending=[False, False, True], kind="mergesort")

        # Deduplicate by API10 only for wells that HAVE an API; wells without API are all kept
        has_api = df_clean["API10"].notna()
        deduped = df_clean[has_api].drop_duplicates(subset=["API10"], keep="first")
        df_clean = pd.concat([deduped, df_clean[~has_api]]).copy()
        omitted_count = original_count - len(df_clean)
    df_clean = df_clean.reset_index(drop=True)

    ptype_col = _find_opt(df_clean, "production type", "type")
    tvd_col = _find_opt(df_clean, "true vertical depth", "tvd")
    idx = df_clean.index

    raw_status = (df_clean[status_col].astype(str).str.upper() if status_col is not None
                  else pd.Series("UNKNOWN", index=idx))
    if ptype_col is not None:
        wtype = df_clean[ptype_col].astype(str).map(_map_type)
    else:
        wtype = pd.Series("OIL", index=idx)      # cannot distinguish types without the column
    tvd = (pd.to_numeric(df_clean[tvd_col], errors="coerce") if tvd_col is not None
           else pd.Series(np.nan, index=idx))

    d = pd.DataFrame({"raw_status": raw_status, "wtype": wtype, "tvd": tvd})
    d["status"] = d["raw_status"].map(_map_status)

    is_oil = d["wtype"] == "OIL"
    is_inj = d["wtype"] == "INJ"
    groups = {
        "active_oil": d[is_oil & (d.status == "ACTIVE")],
        "active_inj": d[is_inj & (d.status == "ACTIVE")],
        "inactive_ta": d[d.status == "INACTIVE"],
        "pa_wells": d[d.status == "PA"],
    }

    info = {"total_wells": len(d), "omitted_sts": omitted_count, "ptype_missing": ptype_col is None}
    info["total_tvd_sum"] = float(d["tvd"].dropna().sum())

    # Wells still carrying P&A liability: everything that is NOT already plugged & abandoned
    liable = d[d["status"] != "PA"]
    info["liable_wells"] = int(len(liable))
    info["liable_tvd_sum"] = float(liable["tvd"].dropna().sum())
    info["liable_missing_tvd"] = int(liable["tvd"].isna().sum())

    for key, g in groups.items():
        tvd_clean = g["tvd"].dropna()
        info[key] = {
            "n": int(len(g)),
            "tvd_avg": float(tvd_clean.mean()) if not tvd_clean.empty else np.nan,
            "tvd_min": float(tvd_clean.min()) if not tvd_clean.empty else np.nan,
            "tvd_max": float(tvd_clean.max()) if not tvd_clean.empty else np.nan,
            "tvd_sum": float(tvd_clean.sum()) if not tvd_clean.empty else 0.0,
            "missing_tvd": int(g["tvd"].isna().sum()),
        }

    # Active wells that are neither oil producers nor injectors (gas, SWD, etc.): counted, not audited
    other_active = d[~is_oil & ~is_inj & (d.status == "ACTIVE")]
    info["active_non_oil"] = {
        "n": int(len(other_active)),
        "gas": int((other_active["wtype"] == "GAS").sum()),
        "swd": int((other_active["wtype"] == "SWD").sum()),
        "other": int((~other_active["wtype"].isin(["GAS", "SWD"])).sum()),
    }

    others = d[d["status"] == "OTHER"]
    info["other_statuses"] = others["raw_status"].value_counts().to_dict()
    return info


def audit_waterflood(df_prod: pd.DataFrame, wells_summary: dict) -> dict:
    """WF screening: average TVD of active oil producers vs active injectors, plus water signature."""
    audit_results = {}
    tvd_prod = wells_summary["active_oil"]["tvd_avg"]
    tvd_inj = wells_summary["active_inj"]["tvd_avg"]

    if pd.isna(tvd_prod) or pd.isna(tvd_inj) or wells_summary["active_inj"]["n"] == 0 or tvd_prod == 0:
        audit_results["tvd_match"] = "N/A (Missing TVD or Injectors)"
        audit_results["tvd_pass"] = False
    else:
        diff_pct = abs(tvd_prod - tvd_inj) / tvd_prod
        audit_results["tvd_match"] = f"{diff_pct * 100:.1f}% Difference"
        audit_results["tvd_pass"] = diff_pct <= 0.10

    last_12 = rate_window(df_prod, 12)
    sum_oil = float(last_12["Oil"].sum())
    sum_water = float(last_12["Water"].sum())

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


# ----------------------------------------------------------------------------
# Decline (estimate only - OFM remains the reference)
# ----------------------------------------------------------------------------
def _arps_q(t, qi, D, b):
    if b < 1e-9:
        return qi * np.exp(-D * t)
    return qi * np.power(np.clip(1.0 + b * D * t, 1e-9, None), -1.0 / b)


def _eff_decline(D: float, b: float) -> float:
    if b < 1e-9:
        return float(1.0 - np.exp(-D))
    return float(1.0 - (1.0 + b * D) ** (-1.0 / b))


def rate_window(prod: pd.DataFrame, months: int) -> pd.DataFrame:
    last = prod["Ym"].max()
    return prod[prod["Ym"] > last - months].reset_index(drop=True)


def fit_arps(rates, kind: str = "auto") -> dict:
    fail = {"kind": "exp", "eff_decline": 0.0, "D_nominal": 0.0, "b": 0.0, "success": False}
    q = np.asarray(rates, float)
    n = len(q)
    if n < 6 or (q > 0).sum() < 4:
        return fail
    t = np.arange(n) / 12.0
    tmax = max(t[-1], 1e-6)

    pos = q > 0
    slope, icpt = np.polyfit(t[pos], np.log(q[pos]), 1)
    qi0, D0 = float(np.exp(icpt)), float(-slope)
    qmax = float(q.max()) * 5
    dmin = -0.9 / tmax

    cands = {}

    def _try(name, f, p0, lo, hi, b, k):
        try:
            p, _ = curve_fit(f, t, q, p0=p0, bounds=(lo, hi), maxfev=50000)
            sse = float(np.sum((q - f(t, *p)) ** 2))
            cands[name] = {"qi": float(p[0]), "D": float(p[1]),
                           "b": float(p[2]) if k == 3 else b, "sse": sse, "k": k}
        except (RuntimeError, ValueError, TypeError, FloatingPointError, np.linalg.LinAlgError):
            pass  # this model could not be fitted; the other candidate may still work

    _try("exp", lambda t, qi, D: _arps_q(t, qi, D, 0.0), [qi0, np.clip(D0, dmin, 20)], [0, dmin], [qmax, 20], 0.0, 2)
    _try("harm", lambda t, qi, D: _arps_q(t, qi, D, 1.0), [qi0, np.clip(D0, dmin, 20)], [0, dmin], [qmax, 20], 1.0, 2)

    if not cands:
        return fail
    if kind == "auto" or kind not in cands:
        kind = min(cands, key=lambda k: n * np.log(max(cands[k]["sse"], 1e-12) / n) + cands[k]["k"] * np.log(n))

    c = cands[kind]
    return {"kind": kind, "eff_decline": _eff_decline(c["D"], c["b"]),
            "D_nominal": c["D"], "b": c["b"], "success": True}


def classify_decline(worst_pct: float) -> tuple[float, str]:
    """Company manual table: DI > 20% -> 1.9x | 10%-19.9% -> 2.5x | < 10% -> 3.0x."""
    if worst_pct > 20.0:
        return 1.9, "High Decline"
    if worst_pct >= 10.0:
        return 2.5, "Moderate Decline"
    return 3.0, "Low Decline"


# ----------------------------------------------------------------------------
# Prices / cash flow
# ----------------------------------------------------------------------------
def base_prices(prices: pd.DataFrame, prod: pd.DataFrame, months: int = 12) -> dict:
    last = prod["Ym"].max()
    win = prod[prod["Ym"] > last - months].merge(
        prices[["Ym", "WTI", "NYMEX", "Oil Diff", "Gas Diff"]], on="Ym", how="left")
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
    return {"oil_base": float(po.mean()), "gas_base": float(pg.mean()),
            "oil_share": oil_rev / tot if tot > 0 else 1.0}


def calculate_stressed_lycf(lycf: float, base_prices_ratios: dict,
                            stressed_oil: float = 50.0, stressed_gas: float = 3.0) -> float:
    ob = base_prices_ratios["oil_base"] if base_prices_ratios["oil_base"] > 0 else 1.0
    gb = base_prices_ratios["gas_base"] if base_prices_ratios["gas_base"] > 0 else 1.0
    stressed_pf = (base_prices_ratios["oil_share"] * stressed_oil / ob
                   + (1 - base_prices_ratios["oil_share"]) * stressed_gas / gb)
    return float(lycf * stressed_pf)


def calculate_abandonment(interest_type: str, n_inactive_wells: int, wi_pct: float, tvd_avg: float,
                          cost_per_ft: float = 7.0, months: int = FORECAST_MONTHS, mode: str = "Lump Sum",
                          spread_months: int = 12, exact_tvd_sum: float = None) -> dict:
    if interest_type.upper() in ["NRI", "ORRI"]:
        return {"per_well": 0.0, "total": 0.0, "schedule": np.zeros(months)}

    if exact_tvd_sum is not None and exact_tvd_sum > 0:
        total = exact_tvd_sum * wi_pct * cost_per_ft
    else:
        total = n_inactive_wells * tvd_avg * wi_pct * cost_per_ft

    per_well = total / n_inactive_wells if n_inactive_wells > 0 else 0.0
    sched = np.zeros(months)
    if mode == "Lump Sum":
        sched[0] = total                      # company rule: all P&A is paid in month 1
    else:
        k = min(spread_months, months)
        if k > 0:
            sched[0:k] = total / k
    return {"per_well": per_well, "total": total, "schedule": sched}


def build_forecast(*, months: int, lycf: float, decline: float, prices: pd.DataFrame,
                   base_prices_ratios: dict, start: pd.Timestamp, last_prod: pd.Period,
                   abandon_schedule: np.ndarray) -> pd.DataFrame:
    idx = pd.period_range(start, periods=months, freq="M")
    lag = (idx[0] - last_prod).n - 1
    m = np.arange(1, months + 1) + max(lag, 0)

    decline = float(np.clip(decline, 0.0, 0.99))     # guard against 100%+ or negative inputs
    vol = (1.0 - decline) ** (m / 12.0)

    p = prices.set_index("Ym")
    oil = (p["WTI"] + p["Oil Diff"]).reindex(idx).ffill().bfill().to_numpy()
    gas = (p["NYMEX"] + p["Gas Diff"]).reindex(idx).ffill().bfill().to_numpy()

    ob = base_prices_ratios["oil_base"] if base_prices_ratios["oil_base"] > 0 else 1.0
    gb = base_prices_ratios["gas_base"] if base_prices_ratios["gas_base"] > 0 else 1.0
    pf = (base_prices_ratios["oil_share"] * oil / ob + (1 - base_prices_ratios["oil_share"]) * gas / gb)

    cf_gross = (lycf / 12.0) * vol * pf
    ab = np.asarray(abandon_schedule)[:months]

    out = pd.DataFrame({"Date": idx.to_timestamp(how="end").normalize(),
                        "Gross CF ($)": cf_gross, "Abandonment ($)": -ab})
    out["Net CF ($)"] = out["Gross CF ($)"] + out["Abandonment ($)"]
    return out


def xirr(amounts, dates) -> float:
    a = np.asarray(amounts, float)
    d = pd.to_datetime(pd.Series(list(dates)))
    t = ((d - d.iloc[0]).dt.days / 365.0).to_numpy()

    def f(r):
        return float(np.sum(a / np.power(1.0 + r, t)))

    try:
        lo, hi = -0.99, 1000.0
        if f(lo) * f(hi) > 0:
            return float("nan")
        return float(brentq(f, lo, hi, maxiter=500))
    except (ValueError, RuntimeError, FloatingPointError, OverflowError):
        return float("nan")


def evaluate_cashflow(cf_neto, dates, fixed_offer: float = None, gross_cf=None) -> dict:
    """
    Values a cash flow stream.
    - fixed_offer=None -> offer = PV at 17.5%.
    - gross_cf: gross cash flow (before P&A). Used for the Annual CF Multiple so a month-1 P&A
      payment does not distort year-1 cash flow. Falls back to net CF when omitted.
    """
    cf = np.asarray(cf_neto, float)
    d = pd.to_datetime(pd.Series(list(dates)))

    offer_date = d.iloc[0] - pd.DateOffset(months=1)
    t = ((d - offer_date).dt.days / 365.0).to_numpy()

    pv_175 = float(np.sum(cf / (1.0 + 0.175) ** t))
    pv_250 = float(np.sum(cf / (1.0 + 0.250) ** t))

    offer_amount = float(fixed_offer) if fixed_offer is not None else pv_175

    g = np.asarray(gross_cf, float) if gross_cf is not None else cf
    year1_net = float(cf[:12].sum())
    year1_gross = float(g[:12].sum())

    if offer_amount > 0:
        amounts = np.concatenate([[-offer_amount], cf])
        dts = [offer_date] + list(d)
        irr = xirr(amounts, dts) * 100
        multiple = offer_amount / year1_gross if year1_gross > 0 else float("nan")
    else:
        irr, multiple = float("nan"), float("nan")   # IRR / multiple are meaningless for a zero or negative offer

    return {
        "Estimated Offer ($)": offer_amount,
        "Year 1 CF ($)": year1_net,
        "Year 1 Gross CF ($)": year1_gross,
        "Annual CF Multiple (x)": multiple,
        "IRR (%)": irr,
        "PV 17.5% ($)": pv_175,
        "PV 25.0% ($)": pv_250,
        "Total Net CF ($)": float(cf.sum()),
    }

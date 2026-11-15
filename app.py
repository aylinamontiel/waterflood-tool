import base64
import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import engine

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
HURDLE_IRR = 15.0        # % - reference IRR used to color the Matrix IRR (edit if the company hurdle changes)
STRESS_OIL = 50.0        # $/bbl flat realized price in the stress test (no differentials applied)
STRESS_GAS = 3.0         # $/MMBtu flat realized price in the stress test
DEFAULT_TVD = 7500.0     # ft - fallback TVD for inactive wells (editable in the sidebar)
FORECAST_MONTHS = engine.FORECAST_MONTHS
SCENARIOS = ("1-Year", "5-Year")

st.set_page_config(page_title="WaterFlood Tool", layout="wide", initial_sidebar_state="expanded")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def get_base64_image(image_path):
    if os.path.exists(image_path):
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    return ""


def _nan(v):
    return v is None or (isinstance(v, float) and np.isnan(v))


def fmt_usd(v):
    if _nan(v):
        return "N/A"
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def fmt_irr(v):
    return "N/A" if _nan(v) else f"{v:.2f}%"


def fmt_mult(v):
    return "N/A" if _nan(v) else f"{v:.2f}x"


def fmt_tvd(v):
    return "N/A" if _nan(v) else f"{v:,.0f}'"


def load_table(file):
    """Reads a CSV/XLSX upload (first sheet for Excel)."""
    file.seek(0)
    if file.name.lower().endswith(".csv"):
        return pd.read_csv(file)
    return pd.read_excel(file, sheet_name=0, engine="openpyxl")


logo_base64 = get_base64_image("logo.png")
logo_html = (f'<img src="data:image/png;base64,{logo_base64}" style="width: 250px; margin-bottom: 30px;">'
             if logo_base64 else "")

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Montserrat:wght@400;600;700&display=swap');

    html, body, [class*="css"] { font-family: 'Montserrat', 'Inter', sans-serif; }
    [data-testid="stStatusWidget"] {visibility: hidden;}
    .stDeployButton {display: none;}

    .stButton>button {
        background-color: #004AAD;
        color: white; border-radius: 6px; padding: 0.5rem 2.5rem;
        font-weight: 600; border: none; transition: all 0.3s ease;
        text-transform: uppercase; letter-spacing: 1px;
    }
    .stButton>button:hover {
        background-color: #FFE500; color: #004AAD; box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }

    .loader-container { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 40vh; }
    .cube-grid { width: 50px; height: 50px; margin: 20px auto; }
    .cube-grid .cube { width: 33%; height: 33%; background-color: #38B6FF; float: left; animation: cubeGridScaleDelay 1.3s infinite ease-in-out; }
    .cube-grid .cube1 { animation-delay: 0.2s; } .cube-grid .cube2 { animation-delay: 0.3s; } .cube-grid .cube3 { animation-delay: 0.4s; }
    .cube-grid .cube4 { animation-delay: 0.1s; } .cube-grid .cube5 { animation-delay: 0.2s; } .cube-grid .cube6 { animation-delay: 0.3s; }
    .cube-grid .cube7 { animation-delay: 0.0s; } .cube-grid .cube8 { animation-delay: 0.1s; } .cube-grid .cube9 { animation-delay: 0.2s; }
    @keyframes cubeGridScaleDelay { 0%, 70%, 100% { transform: scale3D(1, 1, 1); } 35% { transform: scale3D(0, 0, 1); } }
    </style>
""", unsafe_allow_html=True)

if "results" not in st.session_state:
    st.session_state.results = None

st.title("WaterFlood Tool")
st.markdown("---")

# ----------------------------------------------------------------------------
# Sidebar inputs
# ----------------------------------------------------------------------------
with st.sidebar:
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)
        st.markdown("<br>", unsafe_allow_html=True)

    st.header("1. Base Financials")
    lycf = st.number_input("Base LYCF ($/year)", value=0.0, step=1000.0, key="input_lycf")
    lycf_note = st.empty()
    st.markdown("---")

    st.header("2. Interest & Liabilities")
    interest_type = st.selectbox("Select Interest Type", ["NRI", "ORRI", "WI"], key="input_interest")

    if interest_type == "WI":
        lycf_note.info("For a WI evaluation, enter the LYCF **already net of the year's operating expenses (OPEX)**.")
        wi_pct = st.number_input("Average Working Interest (WI) Decimal (e.g. 0.0005)", min_value=0.0, max_value=1.0,
                                 value=0.0, format="%.6f", key="input_wi")
        st.markdown("**P&A Liability Calculation**")
        operator_distress = st.checkbox("Operator in Negative Cash Flow",
                                        help="Calculate P&A for ALL wells (Active + Inactive) per underwriting rules.")
        n_inactive_override = st.number_input("Inactive Wells (Type number OR leave 0 to auto-detect)",
                                              min_value=0, value=0, step=1, key="input_inactive")
        tvd_fallback = st.number_input("Fallback TVD for inactive wells (ft)", min_value=0.0, value=DEFAULT_TVD,
                                       step=100.0, key="input_tvd_fallback",
                                       help="Used ONLY when the Wells Table does not provide TVD for the inactive wells.")
        pa_cost_ft = st.number_input("P&A Cost ($/ft)", min_value=0.0, value=7.0, key="input_pa_cost")
        st.caption("Note: P&A Liability is deducted entirely in Month 1 (Lump Sum).")
    else:
        wi_pct, pa_cost_ft, n_inactive_override, tvd_fallback = 0.0, 0.0, 0, DEFAULT_TVD
        operator_distress = False
        st.info("P&A and OPEX costs are disabled for this interest type.")

    st.markdown("---")
    if interest_type == "WI" and operator_distress:
        st.info("Decline rates are not required for Negative CF scenarios.")
        dec_1yr_input, dec_5yr_input = 0.0, 0.0
        assigned_mult, risk_class, worst_decline = 0.0, "N/A", 0.0
    else:
        st.header("3. Decline Rates")
        st.caption("Enter the Production decline rate for last yr and for last 5 yrs (From OFM)")
        dec_1yr_input = st.number_input("1-Year Decline (%)", min_value=-100.0, max_value=99.0, value=0.0, step=0.5,
                                        format="%.2f", key="input_dec_1", help="Percent, e.g. 8 for an 8% annual decline.")
        dec_5yr_input = st.number_input("5-Year Decline (%)", min_value=-100.0, max_value=99.0, value=0.0, step=0.5,
                                        format="%.2f", key="input_dec_5", help="Percent, e.g. 8 for an 8% annual decline.")

        worst_decline = max(dec_1yr_input, dec_5yr_input)
        assigned_mult, risk_class = engine.classify_decline(worst_decline)
        if min(dec_1yr_input, dec_5yr_input) < 0:
            st.error("Negative decline rate: per underwriting rules this is not a WF project, so this tool does not apply.")
        else:
            st.info(f"Assigned Multiple: {assigned_mult}x\n\nRisk Class: {risk_class}")

# ----------------------------------------------------------------------------
# File uploads
# ----------------------------------------------------------------------------
st.subheader("Data Upload")
distress_mode = interest_type == "WI" and operator_distress
if distress_mode:
    st.info("Negative CF Mode Active: Only the Wells Table is required to calculate the total P&A liability.")
    wells_file = st.file_uploader("Wells Table", type=["csv", "xlsx"], key="file_wells")
    st.caption("Export WG table from Drilling Info")
    prod_file = None
    price_file = None
else:
    col1, col2, col3 = st.columns(3)
    with col1:
        prod_file = st.file_uploader("1. Historical Production", type=["csv", "xlsx"], key="file_prod")
        st.caption("Downloaded from Drilling Info")
    with col2:
        price_file = st.file_uploader("2. Price Input File", type=["csv", "xlsx"], key="file_price")
        st.caption("Downloaded from [Template folder](https://drive.google.com/drive/folders/1XSFTshnnQ-n66_x1SSceVkgHrKLr2dty?usp=drive_link)")
    with col3:
        wells_file = st.file_uploader("3. Wells Table", type=["csv", "xlsx"], key="file_wells")
        st.caption("Export WG table from Drilling Info")


# ----------------------------------------------------------------------------
# Evaluation (runs only when the button is pressed)
# ----------------------------------------------------------------------------
def run_evaluation(p, prod_file, price_file, wells_file):
    """Computes everything and returns a plain dict that is stored in session_state."""
    interest = p["interest_type"]

    # ---------------- Negative CF (distress) mode: wells table only ----------------
    if p["distress"]:
        ws = engine.summarize_wells(load_table(wells_file))
        total_wells = ws["liable_wells"]
        abandon = engine.calculate_abandonment(
            interest_type=interest, n_inactive_wells=total_wells, wi_pct=p["wi_pct"], tvd_avg=p["tvd_fallback"],
            cost_per_ft=p["pa_cost_ft"], months=FORECAST_MONTHS, mode="Lump Sum", spread_months=1,
            exact_tvd_sum=ws["liable_tvd_sum"])
        return {"mode": "distress", "p": p, "wells_summary": ws, "abandon": abandon,
                "total_wells": total_wells, "n_plugged": ws["pa_wells"]["n"],
                "tvd_source": "Wells Table" if ws["liable_tvd_sum"] > 0 else f"fallback TVD ({p['tvd_fallback']:,.0f} ft)"}

    # ---------------- Standard mode ----------------
    df_prod = engine.clean_production(load_table(prod_file))
    df_prices = engine.clean_prices(load_table(price_file))

    base_ratios = engine.base_prices(df_prices, df_prod, months=12)
    fit_1y = engine.fit_arps(engine.rate_window(df_prod, 12)["qo"].values, kind="auto")
    fit_5y = engine.fit_arps(engine.rate_window(df_prod, 60)["qo"].values, kind="auto")

    ws, audit = None, None
    n_inactive_auto, tvd_avg_tbl, tvd_sum_auto, missing_tvd = 0, np.nan, 0.0, 0
    if wells_file:
        ws = engine.summarize_wells(load_table(wells_file))
        ina = ws["inactive_ta"]
        n_inactive_auto, tvd_avg_tbl, tvd_sum_auto, missing_tvd = ina["n"], ina["tvd_avg"], ina["tvd_sum"], ina["missing_tvd"]
        audit = engine.audit_waterflood(df_prod, ws)

    n_final = p["n_override"] if p["n_override"] > 0 else n_inactive_auto
    if p["n_override"] > 0:
        exact_sum = None
        if not np.isnan(tvd_avg_tbl):
            tvd_avg, tvd_source = tvd_avg_tbl, "average TVD of the inactive wells in the Wells Table"
        else:
            tvd_avg, tvd_source = p["tvd_fallback"], f"fallback TVD ({p['tvd_fallback']:,.0f} ft)"
    elif tvd_sum_auto > 0:
        tvd_avg, exact_sum, tvd_source = tvd_avg_tbl, tvd_sum_auto, "exact sum of inactive-well TVD from the Wells Table"
    else:
        exact_sum = None
        tvd_avg, tvd_source = p["tvd_fallback"], f"fallback TVD ({p['tvd_fallback']:,.0f} ft)"

    abandon = engine.calculate_abandonment(
        interest_type=interest, n_inactive_wells=n_final, wi_pct=p["wi_pct"], tvd_avg=tvd_avg,
        cost_per_ft=p["pa_cost_ft"], months=FORECAST_MONTHS, mode="Lump Sum", spread_months=1, exact_tvd_sum=exact_sum)

    start = pd.Timestamp.today().normalize()
    last_prod = df_prod["Ym"].max()

    def make_fc(decline_pct, prices):
        return engine.build_forecast(months=FORECAST_MONTHS, lycf=p["lycf"], decline=decline_pct / 100.0, prices=prices,
                                     base_prices_ratios=base_ratios, start=start, last_prod=last_prod,
                                     abandon_schedule=abandon["schedule"])

    # Flat stress prices are FINAL realized prices (worst case): no differentials are added.
    df_prices_flat = df_prices.copy()
    df_prices_flat["WTI"] = STRESS_OIL
    df_prices_flat["NYMEX"] = STRESS_GAS
    df_prices_flat["Oil Diff"] = 0.0
    df_prices_flat["Gas Diff"] = 0.0

    matrix_gross = p["mult"] * p["lycf"]
    matrix_net = matrix_gross - abandon["total"]
    stressed_lycf = engine.calculate_stressed_lycf(p["lycf"], base_ratios, stressed_oil=STRESS_OIL, stressed_gas=STRESS_GAS)
    stressed_net = p["mult"] * stressed_lycf - abandon["total"]

    scen = {}
    for label, dec in (("1-Year", p["dec1"]), ("5-Year", p["dec5"])):
        fc = make_fc(dec, df_prices)
        fcf = make_fc(dec, df_prices_flat)
        gross, net = fc["Gross CF ($)"], fc["Net CF ($)"]
        gross_f, net_f = fcf["Gross CF ($)"], fcf["Net CF ($)"]

        pv175 = engine.evaluate_cashflow(net, fc["Date"], None, gross_cf=gross)
        pv25 = engine.evaluate_cashflow(net, fc["Date"], pv175["PV 25.0% ($)"], gross_cf=gross)
        mat = engine.evaluate_cashflow(net, fc["Date"], matrix_net, gross_cf=gross)

        f175 = engine.evaluate_cashflow(net_f, fcf["Date"], None, gross_cf=gross_f)
        f25 = engine.evaluate_cashflow(net_f, fcf["Date"], f175["PV 25.0% ($)"], gross_cf=gross_f)
        f_orig = engine.evaluate_cashflow(net_f, fcf["Date"], matrix_net, gross_cf=gross_f)
        f_str = engine.evaluate_cashflow(net_f, fcf["Date"], stressed_net, gross_cf=gross_f)

        scen[label] = {"forecast": fc, "forecast_flat": fcf, "matrix": mat, "pv25": pv25, "pv175": pv175,
                       "flat_orig": f_orig, "flat_stressed": f_str, "flat25": f25, "flat175": f175}

    all_offers = [matrix_net, stressed_net]
    for s in scen.values():
        all_offers += [s["pv25"]["Estimated Offer ($)"], s["pv175"]["Estimated Offer ($)"],
                       s["flat25"]["Estimated Offer ($)"], s["flat175"]["Estimated Offer ($)"]]
    valid = [o for o in all_offers if not np.isnan(o)]

    return {
        "mode": "standard", "p": p, "wells_summary": ws, "audit": audit, "abandon": abandon,
        "n_inactive_final": n_final, "tvd_source": tvd_source, "missing_tvd_inactive": missing_tvd,
        "fit_1y": fit_1y, "fit_5y": fit_5y, "df_prices": df_prices, "scenarios": scen,
        "matrix_gross": matrix_gross, "matrix_net": matrix_net, "stressed_net": stressed_net,
        "min_offer": min(valid) if valid else 0.0, "max_offer": max(valid) if valid else 0.0,
        "worst_label": "1-Year" if p["dec1"] >= p["dec5"] else "5-Year",
    }


# ----------------------------------------------------------------------------
# Notes for the underwriter
# ----------------------------------------------------------------------------
def build_notes(res):
    p, ws, audit = res["p"], res["wells_summary"], res["audit"]
    s1, s5 = res["scenarios"]["1-Year"], res["scenarios"]["5-Year"]
    interest, mult, risk_class = p["interest_type"], p["mult"], p["risk_class"]
    is_wi = interest == "WI"

    def dec_str(pct, fit):
        return f"{pct:.2f}% (Auto-detected: {fit['eff_decline'] * 100:.1f}%)" if fit["success"] else f"{pct:.2f}%"

    str_1y, str_5y = dec_str(p["dec1"], res["fit_1y"]), dec_str(p["dec5"], res["fit_5y"])
    gross_str, net_str, stressed_str = fmt_usd(res["matrix_gross"]), fmt_usd(res["matrix_net"]), fmt_usd(res["stressed_net"])
    pa_line = f"- P&A Deduction = -${res['abandon']['total']:,.0f}\n" if is_wi else ""

    def pair(key):
        return (f"{fmt_usd(s1[key]['Estimated Offer ($)'])} (1-Yr Decline) | "
                f"{fmt_usd(s5[key]['Estimated Offer ($)'])} (5-Yr Decline)")

    single = ws is None or ws["total_wells"] == 1
    if single:
        notes = f"Write Up\n- Interest Type Evaluated: {interest.upper()}\n\nProduction will be forecasted using OFM:\n"
        notes += f"- Production decline rate for last yr = {str_1y}\n- Production decline rate for last 5 yrs = {str_5y}\n"
        if is_wi:
            notes += "\nSince this is a Working Interest (WI) evaluation, environmental liability was accounted for.\n"
    else:
        a_prod, tvd_prod = ws["active_oil"]["n"], ws["active_oil"]["tvd_avg"]
        a_inj, tvd_inj = ws["active_inj"]["n"], ws["active_inj"]["tvd_avg"]
        ina = ws["inactive_ta"]
        tvd_range = (f"{ina['tvd_min']:,.0f}' - {ina['tvd_max']:,.0f}' (AVG TVD: {fmt_tvd(ina['tvd_avg'])})"
                     if not np.isnan(ina["tvd_min"]) else "N/A")

        if audit["tvd_pass"] and audit["water_pass"]:
            audit_p = (f"The presence of {a_inj} injection wells with an average TVD ({fmt_tvd(tvd_inj)}), comparable to the "
                       f"producing wells ({fmt_tvd(tvd_prod)}), confirms Zone and TVD Consistency. Furthermore, the Waterflood "
                       f"Signature Identification verifies that the water production volume is substantially and consistently "
                       f"higher than the oil production ({audit['water_signature']}). This rigorously confirms the unit is under "
                       f"an active secondary recovery project.")
        else:
            audit_p = "⚠️ The project failed the Waterflood Technical Audit. "
            if not audit["tvd_pass"]:
                audit_p += "Injection wells do not share a highly similar TVD with producers. "
            if not audit["water_pass"]:
                audit_p += "Water production volume does not exhibit an active waterflood signature. "
            audit_p += "There may be no active waterflood supporting this asset."

        notes = (f"Write Up\n- Interest Type Evaluated: {interest.upper()}\n- Unit located in DI:\n"
                 f"  - {a_prod + a_inj} ACTIVE wells\n"
                 f"    - {a_inj} Injection wells (AVG TVD = {fmt_tvd(tvd_inj)})\n"
                 f"    - {a_prod} Oil producers (AVG TVD = {fmt_tvd(tvd_prod)})\n"
                 f"  - {ina['n']} INACTIVE / SHUT-IN / TA wells\n"
                 f"    - TVD range for inactive wells: {tvd_range}\n"
                 f"  - {ws['pa_wells']['n']} P&A wells")
        non_oil = ws["active_non_oil"]
        if non_oil["n"] > 0:
            notes += (f"\n  - {non_oil['n']} other ACTIVE wells not used in the audit "
                      f"(Gas: {non_oil['gas']}, SWD: {non_oil['swd']}, Other: {non_oil['other']})")
        if ws.get("other_statuses"):
            other_total = sum(ws["other_statuses"].values())
            detail = ", ".join(f"{k} ({v})" for k, v in ws["other_statuses"].items())
            notes += f"\n  - {other_total} OTHER wells (Statuses: {detail})"
        if ws.get("omitted_sts", 0) > 0:
            notes += f"\n  - {ws['omitted_sts']} Sidetracks/Duplicates omitted (Filtered to prevent duplicate TVD and P&A counts)"

        notes += (f"\n\n{audit_p}\n\nTherefore, production will be forecasted using OFM:\n"
                  f"- Production decline rate for last yr = {str_1y}\n- Production decline rate for last 5 yrs = {str_5y}\n")
        if is_wi:
            notes += (f"\nSince this is a Working Interest (WI) evaluation, a total of {res['n_inactive_final']} non-producing wells "
                      f"(Inactive/Shut-in/TA; wells already P&A are excluded) were accounted for environmental liability.\n")

    notes += f"""
Since the decline rates represent a {risk_class}, a default multiple of {mult}X will be used to generate the base offer. The higher (worst-case) of the two decline rates ({p['worst']:.2f}%) sets the multiple; DCF valuations are shown for both the 1-Year and 5-Year decline rates.

Base Valuation Calculation (Strip Pricing):
- Gross Offer ({mult}X * ${p['lycf']:,.0f} LYCF) = {gross_str}
{pa_line}- Net Matrix Offer = {net_str}
- Annual CF Multiple of the Net Matrix Offer: {fmt_mult(s1['matrix']['Annual CF Multiple (x)'])} (1-Yr) | {fmt_mult(s5['matrix']['Annual CF Multiple (x)'])} (5-Yr)

Generated Evaluation Offer Range (Strip Pricing):
- Valuation 1 (Net Matrix Rule @ {mult}x): {net_str}
- Valuation 2 (Target 25% IRR): {pair('pv25')}
- Valuation 3 (Standard DCF PV 17.5%): {pair('pv175')}

Stress Test Valuations (${STRESS_OIL:,.0f} WTI / ${STRESS_GAS:,.0f} NYMEX, flat realized prices, no differentials):
- Stressed Matrix Offer Entry: {stressed_str}
- Required Offer (Target 25%): {pair('flat25')}
- Required Offer (DCF 17.5%): {pair('flat175')}

**Recommended Offer Range (Worst Case to Best Case): {fmt_usd(res['min_offer'])} to {fmt_usd(res['max_offer'])}**"""

    if res["matrix_net"] < 0:
        notes += ("\n\n⚠️ CONCLUSION: The estimated environmental liability exceeds the asset's gross value, resulting in "
                  "negative valuation scenarios. Acquiring these wells is NOT recommended.")
    return notes


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------
def offers_table(rows):
    """rows: list of (label, {scenario: (offer, irr_text, multiple)})."""
    data = []
    for label, vals in rows:
        rec = {"Valuation": label}
        for sc in SCENARIOS:
            off, irr_txt, mult = vals[sc]
            rec[f"{sc} Decline | Offer"] = fmt_usd(off)
            rec[f"{sc} Decline | IRR"] = irr_txt
            rec[f"{sc} Decline | Annual CF Multiple"] = fmt_mult(mult)
        data.append(rec)
    return pd.DataFrame(data).set_index("Valuation")


def triple(v, show_irr=True):
    return (v["Estimated Offer ($)"], fmt_irr(v["IRR (%)"]) if show_irr else "—", v["Annual CF Multiple (x)"])


def irr_metric(col, label, irr):
    if _nan(irr):
        col.metric(label, "N/A")
    else:
        col.metric(label, f"{irr:.2f}%", delta=f"{irr - HURDLE_IRR:+.1f} pts vs {HURDLE_IRR:g}% hurdle")


def render_distress(res):
    p, ws, ab = res["p"], res["wells_summary"], res["abandon"]
    st.success("Evaluation completed successfully!")
    st.subheader("Distress Scenario Results")
    st.error(f"Negative CF Rule Applied: Total P&A liability calculated for ALL {res['total_wells']} wells (Active + Inactive); "
             f"{res['n_plugged']} already plugged (P&A) wells are excluded.")
    if ws.get("liable_missing_tvd", 0) > 0:
        st.warning(f"{ws['liable_missing_tvd']} well(s) have no TVD and are not included in the liability total.")
    st.caption(f"TVD source for the liability: {res['tvd_source']}.")
    c1, c2 = st.columns(2)
    c1.metric("Baseline LYCF", fmt_usd(p["lycf"]))
    c2.metric("Total P&A Liability Deduction", f"-${ab['total']:,.0f}")


def render_standard(res):
    p, ws, audit, ab = res["p"], res["wells_summary"], res["audit"], res["abandon"]
    scen = res["scenarios"]
    s1, s5 = scen["1-Year"], scen["5-Year"]

    st.success("Evaluation completed successfully!")
    st.caption("Results reflect the inputs at the time of the last run. Press the button again after changing any input.")

    if res["matrix_net"] < 0:
        st.warning("⚠️ **Financial Liability Alert**: The estimated P&A costs for the inactive wells exceed the projected revenue "
                   "of the asset. A negative net offer implies that acquiring this property would require the seller to compensate "
                   "Community Minerals to cover the impending abandonment liabilities.")

    if ws and audit:
        st.subheader("Phase 1: WF & CO2 Technical Identification")
        a1, a2 = st.columns(2)
        a1.metric("TVD Consistency (Producers vs Injectors)", audit["tvd_match"],
                  "Pass: Same Zone" if audit["tvd_pass"] else "Fail: Different Zones",
                  delta_color="normal" if audit["tvd_pass"] else "inverse")
        a2.metric("Waterflood Signature (Water vs Oil)", audit["water_signature"],
                  "Pass: Active Sweep" if audit["water_pass"] else "Fail/Unverified",
                  delta_color="normal" if audit["water_pass"] else "inverse")
        non_oil = ws["active_non_oil"]
        cap = f"Audit uses ACTIVE oil producers vs ACTIVE injectors (average TVD). Other active wells not audited: {non_oil['n']} (Gas: {non_oil['gas']}, SWD: {non_oil['swd']}, Other: {non_oil['other']})."
        if ws.get("ptype_missing"):
            cap += " ⚠️ The Wells Table has no Production Type column, so wells could not be split into oil / injection / gas / SWD."
        st.caption(cap)
        st.markdown("---")

    # ---- Strip pricing ----
    st.subheader("Estimated Offers & Returns (Strip Pricing)")
    st.caption(f"Assigned multiple ({p['mult']}x, {p['risk_class']}) uses the worst decline ({res['worst_label']}). "
               f"DCF valuations are computed for both the 1-Year and 5-Year decline rates. "
               f"Annual CF Multiple = Offer / Year-1 gross cash flow (before P&A).")
    if p["interest_type"] == "WI":
        g1, g2, g3 = st.columns(3)
        g1.metric(f"Gross Offer ({p['mult']}x LYCF)", fmt_usd(res["matrix_gross"]))
        g2.metric("P&A Deduction", f"-${ab['total']:,.0f}")
        g3.metric("Final Net Matrix Offer", fmt_usd(res["matrix_net"]))
    else:
        st.metric("Final Net Matrix Offer", fmt_usd(res["matrix_net"]))

    m1, m2 = st.columns(2)
    irr_metric(m1, "Matrix IRR (1-Year Decline)", s1["matrix"]["IRR (%)"])
    irr_metric(m2, "Matrix IRR (5-Year Decline)", s5["matrix"]["IRR (%)"])

    rows = [
        (f"1. Matrix Rule ({p['mult']}x)", {sc: triple(scen[sc]["matrix"]) for sc in SCENARIOS}),
        ("2. Target 25% IRR (PV @ 25%)", {sc: triple(scen[sc]["pv25"], show_irr=False) for sc in SCENARIOS}),
        ("3. Standard DCF (PV @ 17.5%)", {sc: triple(scen[sc]["pv175"], show_irr=False) for sc in SCENARIOS}),
    ]
    st.table(offers_table(rows))
    st.caption("IRR is not shown for Valuations 2 and 3: by construction it equals 25% and 17.5%.")

    if p["interest_type"] == "WI":
        st.info(f"WI Liability Note: The calculations above include an estimated P&A liability of **${ab['total']:,.0f}** "
                f"deducted entirely in Month 1 based on **{res['n_inactive_final']} inactive wells** "
                f"(TVD source: {res['tvd_source']}).")
        if res["missing_tvd_inactive"] > 0 and p["n_override"] == 0:
            st.warning(f"{res['missing_tvd_inactive']} inactive well(s) have no TVD and are not included in the P&A liability.")

    st.markdown("---")

    # ---- Stress test ----
    st.subheader(f"Stress Test Valuations (${STRESS_OIL:,.0f} WTI / ${STRESS_GAS:,.0f} NYMEX)")
    st.caption("Worst-case flat realized prices: no oil / gas differentials are applied.")
    n1, n2 = st.columns(2)
    irr_metric(n1, "Original Offer IRR under stress (1-Year Decline)", s1["flat_orig"]["IRR (%)"])
    irr_metric(n2, "Original Offer IRR under stress (5-Year Decline)", s5["flat_orig"]["IRR (%)"])
    stress_rows = [
        ("1. Original Matrix Offer (Risk)", {sc: triple(scen[sc]["flat_orig"]) for sc in SCENARIOS}),
        ("2. New Stressed Matrix", {sc: triple(scen[sc]["flat_stressed"]) for sc in SCENARIOS}),
        ("3. Target 25% (Flat)", {sc: triple(scen[sc]["flat25"], show_irr=False) for sc in SCENARIOS}),
        ("4. DCF 17.5% (Flat)", {sc: triple(scen[sc]["flat175"], show_irr=False) for sc in SCENARIOS}),
    ]
    st.table(offers_table(stress_rows))

    st.markdown("---")

    # ---- Price chart ----
    st.subheader("Fluid Future Prices (WTI & NYMEX)")
    df_prices = res["df_prices"]
    end_date_prices = pd.Timestamp.today() + pd.DateOffset(years=10)
    chart_data = df_prices[(df_prices["Date"] >= pd.Timestamp.today()) & (df_prices["Date"] <= end_date_prices)]
    if not chart_data.empty:
        fig_prices = make_subplots(specs=[[{"secondary_y": True}]])
        fig_prices.add_trace(go.Scatter(x=chart_data["Date"], y=chart_data["WTI"], name="WTI",
                                        line=dict(color="#004AAD", width=3)), secondary_y=False)
        fig_prices.add_trace(go.Scatter(x=chart_data["Date"], y=chart_data["NYMEX"], name="NYMEX",
                                        line=dict(color="#FFE500", width=3)), secondary_y=True)
        fig_prices.update_layout(hovermode="x unified", plot_bgcolor="white", paper_bgcolor="white",
                                 font=dict(color="#0F2A4A", size=12), legend=dict(font=dict(color="#0F2A4A")),
                                 margin=dict(l=70, r=70, t=30, b=30))
        fig_prices.update_xaxes(title_text="", showgrid=False, dtick="M12", tickformat="%b %Y", tickfont=dict(color="#0F2A4A"))
        fig_prices.update_yaxes(title_text="WTI Price (USD/bbl)", title_standoff=15, title_font=dict(color="#0F2A4A"),
                                showgrid=True, gridcolor="rgba(0,0,0,0.1)", tickformat=".1f",
                                tickfont=dict(color="#0F2A4A"), secondary_y=False)
        fig_prices.update_yaxes(title_text="NYMEX Price (USD/MMBtu)", title_standoff=15, title_font=dict(color="#0F2A4A"),
                                showgrid=False, tickformat=".1f", tickfont=dict(color="#0F2A4A"), secondary_y=True)
        st.plotly_chart(fig_prices, use_container_width=True, theme=None)

    st.markdown("---")

    # ---- Cash flow chart ----
    st.subheader("Net Cash Flow Projection (First 10 years)")
    st.caption("Visual comparison of the 1-Year vs 5-Year decline rate scenarios. "
               "(You can zoom in to see the details ignoring the Month 1 P&A drop).")
    cf5, cf1 = s5["forecast"].head(120), s1["forecast"].head(120)
    fig_cf = go.Figure()
    fig_cf.add_trace(go.Scatter(x=cf5["Date"], y=cf5["Net CF ($)"], name="Net CF (5-Yr Decline)",
                                line=dict(color="#FFE500", width=3)))
    fig_cf.add_trace(go.Scatter(x=cf1["Date"], y=cf1["Net CF ($)"], name="Net CF (1-Yr Decline)",
                                line=dict(color="#38B6FF", width=3)))
    fig_cf.update_layout(hovermode="x unified", plot_bgcolor="white", paper_bgcolor="white",
                         font=dict(color="#0F2A4A", size=12), legend=dict(font=dict(color="#0F2A4A")),
                         margin=dict(l=70, r=40, t=30, b=30))
    fig_cf.update_xaxes(title_text="", showgrid=False, dtick="M12", tickformat="%b %Y", tickfont=dict(color="#0F2A4A"))
    fig_cf.update_yaxes(title_text="Net Cash Flow (USD)", title_standoff=15, title_font=dict(color="#0F2A4A"),
                        showgrid=True, gridcolor="rgba(0,0,0,0.1)", tickformat=".0f", tickfont=dict(color="#0F2A4A"))
    st.plotly_chart(fig_cf, use_container_width=True, theme=None)

    st.markdown("---")
    st.subheader("Underwriter Technical Notes (Copy & Paste)")
    st.code(build_notes(res), language="markdown")


# ----------------------------------------------------------------------------
# Run button
# ----------------------------------------------------------------------------
st.markdown("---")
if st.button("Run WaterFlood Evaluation", type="primary", key="btn_run"):
    st.session_state.results = None

    problem = None
    if interest_type == "WI" and wi_pct == 0.0:
        problem = "Warning: Average WI Decimal cannot be 0. Please check the sidebar inputs."
    elif distress_mode:
        if not wells_file:
            problem = "Please upload the Wells Table to calculate the P&A liability."
    else:
        if lycf <= 0.0:
            problem = "Warning: LYCF must be greater than 0. For an operator in negative cash flow (WI), use the 'Operator in Negative Cash Flow' option."
        elif min(dec_1yr_input, dec_5yr_input) < 0:
            problem = "Negative decline rate: per underwriting rules this is not a WF project, so the evaluation cannot be run."
        elif not prod_file or not price_file or (interest_type == "WI" and not wells_file):
            problem = "Please upload the necessary files (Historical Production, Price, and Wells Table) to continue."

    if problem:
        st.warning(problem)
    else:
        loader_placeholder = st.empty()
        with loader_placeholder.container():
            st.markdown(f"""
                <div class="loader-container">
                    {logo_html}
                    <div class="cube-grid">
                        <div class="cube cube1"></div><div class="cube cube2"></div><div class="cube cube3"></div>
                        <div class="cube cube4"></div><div class="cube cube5"></div><div class="cube cube6"></div>
                        <div class="cube cube7"></div><div class="cube cube8"></div><div class="cube cube9"></div>
                    </div>
                    <h3 style="color: #38B6FF; font-family: 'Montserrat', sans-serif;">Processing Data...</h3>
                </div>
            """, unsafe_allow_html=True)

        params = {
            "interest_type": interest_type, "lycf": lycf, "wi_pct": wi_pct, "distress": distress_mode,
            "n_override": int(n_inactive_override), "tvd_fallback": tvd_fallback, "pa_cost_ft": pa_cost_ft,
            "dec1": dec_1yr_input, "dec5": dec_5yr_input, "worst": worst_decline,
            "mult": assigned_mult, "risk_class": risk_class,
        }
        try:
            st.session_state.results = run_evaluation(params, prod_file, price_file, wells_file)
        except Exception as e:
            st.error(f"An error occurred while processing the data: {str(e)}")
        finally:
            loader_placeholder.empty()

res = st.session_state.results
if res:
    if res["mode"] == "distress":
        render_distress(res)
    else:
        render_standard(res)

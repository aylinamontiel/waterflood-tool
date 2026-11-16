import streamlit as st
import pandas as pd
import numpy as np
import base64
import os
import engine 
import plotly.graph_objects as go
from plotly.subplots import make_subplots

HURDLE_IRR = 15.0       

def fmt_usd(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return "N/A"
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"

def fmt_mult(v):
    return "N/A" if (v is None or np.isnan(v)) else f"{v:.2f}x"

def fmt_irr(v):
    return "N/A" if (v is None or np.isnan(v)) else f"{v:.2f}%"

st.set_page_config(
    page_title="WaterFlood Tool", 
    layout="wide",
    initial_sidebar_state="expanded"
)

def get_base64_image(image_path):
    if os.path.exists(image_path):
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    return ""

logo_base64 = get_base64_image("logo.png")
logo_html = f'<img src="data:image/png;base64,{logo_base64}" style="width: 250px; margin-bottom: 30px;">' if logo_base64 else ''

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

st.title("WaterFlood Tool")
st.markdown("---")

with st.sidebar:
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)
        st.markdown("<br>", unsafe_allow_html=True)

    st.header("1. Base Financials")
    lycf = st.number_input("Base LYCF ($/year)", value=0.0, step=1000.0, key="input_lycf")
    st.caption("Note: LYCF must be audited and normalized to a trailing 12-month basis.")
    lycf_note = st.empty()
    st.markdown("---")
    
    st.header("2. Interest & Liabilities")
    interest_type = st.selectbox("Select Interest Type", ["NRI", "ORRI", "WI"], key="input_interest")
    
    if interest_type == "WI":
        lycf_note.info("For a WI evaluation, ensure the LYCF is **already net of the year's operating expenses (OPEX)**.")
        st.caption("Usar el WI decimal que más se repite. En la Wells Table, la columna WI solo se llena en los pozos con WI distinto. En blanco = se usa este valor.")
        wi_pct = st.number_input("Average Working Interest (WI) Decimal (e.g. 0.05)", min_value=0.0, max_value=1.0, value=0.0, format="%.6f", key="input_wi")
        st.markdown("**P&A Liability Calculation**")
        operator_distress = st.checkbox("Operator in Negative Cash Flow", help="Calculate P&A for ALL wells (Active + Inactive) per underwriting rules.")
        n_inactive_override = st.number_input("Inactive Wells (Type number OR leave 0 to auto-detect)", min_value=0, value=0, step=1, key="input_inactive")
        pa_cost_ft = st.number_input("P&A Cost ($/ft)", min_value=0.0, value=7.0, key="input_pa_cost")
        pa_mode = "Lump Sum"
        pa_spread_months = 1
        st.caption("Note: P&A Liability is deducted entirely in Month 1 (Lump Sum).")
    else:
        wi_pct, pa_cost_ft, n_inactive_override, pa_spread_months = 0.0, 0.0, 0, 1
        pa_mode = "Lump Sum"
        operator_distress = False
        st.info("P&A and OPEX costs are disabled for this interest type.")

    st.markdown("---")
    if interest_type == "WI" and operator_distress:
        st.info("Decline rates are not required for Negative CF scenarios.")
        dec_1yr_input = 0.0
        dec_5yr_input = 0.0
        assigned_mult = 0.0
    else:
        st.header("3. Decline Rates")
        st.caption("Enter the Production decline rate for last yr and for last 5 yrs (From OFM)")
        dec_1yr_input = st.number_input("1-Year Decline (%)", min_value=-100.0, max_value=99.0, value=0.0, key="input_dec_1")
        dec_5yr_input = st.number_input("5-Year Decline (%)", min_value=-100.0, max_value=99.0, value=0.0, key="input_dec_5")
        
        worst_decline = max(dec_1yr_input, dec_5yr_input)
        if worst_decline > 20.0: assigned_mult, risk_class = 1.9, "High Decline"
        elif worst_decline >= 10.0: assigned_mult, risk_class = 2.5, "Moderate Decline"
        else: assigned_mult, risk_class = 3.0, "Low Decline"
        if min(dec_1yr_input, dec_5yr_input) < 0:
            st.error("Negative decline means production increased in the selected window. Please re-select the points in OFM.")
        else:
            st.info(f"Assigned Multiple: {assigned_mult}x\n\nRisk Class: {risk_class}")

    st.markdown("---")
    with st.expander("Methodology & Assumptions"):
        st.markdown("""
        - **Cash Flow Projection**: Future cash flow is projected by scaling the Base LYCF by the monthly volume decline and the ratio of future strip prices to the base historical prices.
        - **OPEX**: For WI evaluations, the base LYCF is assumed to scale proportionately with prices; no separate fixed vs. variable OPEX split is modeled.
        - **Forecast Horizon**: 360 months (30 years) to fully capture the economic life of a mature waterflood.
        - **Discount Rates**: Future cash flows are discounted at standard hurdle rates of **17.5%** and **25.0%**.
        - **P&A Liability**: Calculated on a well-by-well basis (`Well TVD × Well WI % × $/ft`) when well data is available, applying the global WI % if individual well interests are not specified. Deducted entirely in Month 1 (Lump Sum).
        - **Annual CF Multiple**: Calculated as the Offer Amount divided by the Gross Cash Flow of the first 12 projected months (before P&A deduction).
        - **Start Date**: The cash flow projection starts exactly at the current month.
        """)

is_distress = (interest_type == "WI" and operator_distress)

st.subheader("Data Upload")
if is_distress:
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

st.markdown("---")

if st.button("Run WaterFlood Evaluation", type="primary", key="btn_run"):
    
    if not is_distress and lycf <= 0.0:
        if interest_type == "WI":
            st.error("LYCF is zero or negative. If the operator is in distress, please check 'Operator in Negative Cash Flow' in the sidebar.")
        else:
            st.error("LYCF is zero or negative. For NRI/ORRI, this asset generates no positive cash flow. Valuation is $0 and the asset is considered inactive.")
    elif interest_type == "WI" and wi_pct == 0.0:
        st.warning("Warning: Average WI Decimal cannot be 0 for a Working Interest evaluation.")
    elif not is_distress and min(dec_1yr_input, dec_5yr_input) < 0:
        st.error("Negative decline means production increased in the selected window. Please re-select the points in OFM.")
    elif is_distress and not wells_file:
        st.warning("Please upload the Wells Table to calculate the P&A liability.")
    elif not is_distress and (not prod_file or not price_file or (interest_type == "WI" and not wells_file)):
        st.warning("Please upload the necessary files (Historical Production, Price, and Wells Table) to continue.")
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
            
        try:
            if is_distress:
                if wells_file.name.lower().endswith('.csv'): 
                    df_wells = pd.read_csv(wells_file)
                else: 
                    df_wells = pd.read_excel(wells_file, engine='openpyxl')
                
                wells_summary = engine.summarize_wells(df_wells)
                total_wells = wells_summary.get("liable_wells", 0)
                n_plugged = wells_summary["pa_wells"]["n"]
                
                tvd_fallback = wells_summary.get("liable_tvd_sum", 0) / wells_summary.get("liable_wells", 1) if wells_summary.get("liable_wells", 0) > 0 else 0.0
                abandon_data = engine.calculate_abandonment(interest_type=interest_type, wells_data=wells_summary.get("liable_wells_data", {}), n_override=0, default_wi=wi_pct, cost_per_ft=pa_cost_ft, months=360, mode=pa_mode, spread_months=pa_spread_months, fallback_tvd=tvd_fallback)
                
                loader_placeholder.empty()
                st.success("Evaluation completed successfully!")
                st.subheader("Distress Scenario Results")
                st.error(f"Negative CF Rule Applied: Total P&A liability calculated for ALL {total_wells} wells (Active + Inactive); {n_plugged} already plugged (P&A) wells are excluded.")
                
                if abandon_data.get("used_fallback_tvd"):
                    st.warning("⚠️ **P&A Depth Warning**: Missing TVD for inactive wells (or 0 inactive wells detected). Used the average TVD of the active wells as a fallback.")
                if wells_summary and wells_summary.get("wi_custom_count", 0) > 0:
                    st.info(f"**P&A WI Allocation**: {wells_summary['wi_custom_count']} wells used their own custom WI from the table; {wells_summary['wi_default_count']} wells used the default {wi_pct} WI.")
                if wells_summary.get("liable_missing_tvd", 0) > 0:
                    st.warning(f"⚠️ {wells_summary['liable_missing_tvd']} well(s) have no TVD. (A fallback TVD was used if they are inactive).")
                
                col_d1, col_d2 = st.columns(2)
                col_d1.metric("Baseline LYCF", f"-${abs(lycf):,.0f}" if lycf < 0 else f"${lycf:,.0f}")
                col_d2.metric("Total P&A Liability Deduction", f"-${abandon_data['total']:,.0f}")
                
            else:
                if prod_file.name.lower().endswith('.csv'): 
                    df_prod = pd.read_csv(prod_file)
                else: 
                    df_prod = pd.read_excel(prod_file, engine='openpyxl')
                df_prod_clean = engine.clean_production(df_prod)
                
                if price_file.name.lower().endswith('.csv'): 
                    df_prices_raw = pd.read_csv(price_file)
                else: 
                    df_prices_raw = pd.read_excel(price_file, sheet_name=0, engine='openpyxl')
                df_prices = engine.clean_prices(df_prices_raw)
                
                base_ratios = engine.base_prices(df_prices, df_prod_clean, months=12)
                fit_1y = engine.fit_arps(engine.rate_window(df_prod_clean, 12)["qo"].values, kind="auto")
                fit_5y = engine.fit_arps(engine.rate_window(df_prod_clean, 60)["qo"].values, kind="auto")
                
                n_inactive_auto = 0
                wells_summary, audit_res = None, None
                
                if wells_file:
                    if wells_file.name.lower().endswith('.csv'): 
                        df_wells = pd.read_csv(wells_file)
                    else: 
                        df_wells = pd.read_excel(wells_file, engine='openpyxl')
                        
                    wells_summary = engine.summarize_wells(df_wells)
                    n_inactive_auto = wells_summary.get("inactive_ta", {}).get("n", 0)
                    audit_res = engine.audit_waterflood(df_prod_clean, wells_summary)
                
                final_inactive_count = n_inactive_override if n_inactive_override > 0 else n_inactive_auto
                wells_data = wells_summary.get("inactive_wells_data", {}) if wells_summary else {}
                
                tvd_fallback = wells_summary.get("liable_tvd_sum", 0) / wells_summary.get("liable_wells", 1) if (wells_summary and wells_summary.get("liable_wells", 0) > 0) else 0.0
                abandon_data = engine.calculate_abandonment(interest_type=interest_type, wells_data=wells_data, n_override=n_inactive_override, default_wi=wi_pct, cost_per_ft=pa_cost_ft, months=360, mode=pa_mode, spread_months=pa_spread_months, fallback_tvd=tvd_fallback)
                
                forecast_5yr = engine.build_forecast(months=360, lycf=lycf, decline=(dec_5yr_input/100.0), prices=df_prices, base_prices_ratios=base_ratios, start=pd.Timestamp.today().normalize(), last_prod=df_prod_clean["Ym"].max(), abandon_schedule=abandon_data["schedule"])
                forecast_1yr = engine.build_forecast(months=360, lycf=lycf, decline=(dec_1yr_input/100.0), prices=df_prices, base_prices_ratios=base_ratios, start=pd.Timestamp.today().normalize(), last_prod=df_prod_clean["Ym"].max(), abandon_schedule=abandon_data["schedule"])
                
                forecast_worst = engine.build_forecast(months=360, lycf=lycf, decline=(worst_decline/100.0), prices=df_prices, base_prices_ratios=base_ratios, start=pd.Timestamp.today().normalize(), last_prod=df_prod_clean["Ym"].max(), abandon_schedule=abandon_data["schedule"])
                
                val_dcf = engine.evaluate_cashflow(forecast_worst["Net CF ($)"], forecast_worst["Date"], fixed_offer=None, gross_cf=forecast_worst["Gross CF ($)"])
                val_dcf_25 = engine.evaluate_cashflow(forecast_worst["Net CF ($)"], forecast_worst["Date"], fixed_offer=val_dcf["PV 25.0% ($)"], gross_cf=forecast_worst["Gross CF ($)"])
                
                matrix_gross_offer = assigned_mult * lycf
                matrix_net_offer = matrix_gross_offer - abandon_data['total']
                val_wf = engine.evaluate_cashflow(forecast_worst["Net CF ($)"], forecast_worst["Date"], fixed_offer=matrix_net_offer, gross_cf=forecast_worst["Gross CF ($)"])
                
                scen_results = {}
                for scen_label, scen_dec, scen_fc in (("1-Year", dec_1yr_input, forecast_1yr), ("5-Year", dec_5yr_input, forecast_5yr)):
                    s_gross, s_net = scen_fc["Gross CF ($)"], scen_fc["Net CF ($)"]
                    s_175 = engine.evaluate_cashflow(s_net, scen_fc["Date"], fixed_offer=None, gross_cf=s_gross)
                    s_25 = engine.evaluate_cashflow(s_net, scen_fc["Date"], fixed_offer=s_175["PV 25.0% ($)"], gross_cf=s_gross)
                    s_mat = engine.evaluate_cashflow(s_net, scen_fc["Date"], fixed_offer=matrix_net_offer, gross_cf=s_gross)
                    scen_results[scen_label] = {"dec": scen_dec, "pv175": s_175, "pv25": s_25, "matrix": s_mat}
                
                df_prices_flat = df_prices.copy()
                df_prices_flat["WTI"] = 50.0
                df_prices_flat["NYMEX"] = 3.0
                df_prices_flat["Oil Diff"] = 0.0   
                df_prices_flat["Gas Diff"] = 0.0
                
                stressed_lycf = engine.calculate_stressed_lycf(lycf, base_ratios, stressed_oil=50.0, stressed_gas=3.0)
                stressed_matrix_gross = assigned_mult * stressed_lycf
                stressed_matrix_net = stressed_matrix_gross - abandon_data['total']
                
                forecast_flat = engine.build_forecast(months=360, lycf=lycf, decline=(worst_decline/100.0), prices=df_prices_flat, base_prices_ratios=base_ratios, start=pd.Timestamp.today().normalize(), last_prod=df_prod_clean["Ym"].max(), abandon_schedule=abandon_data["schedule"])
                val_flat_dcf = engine.evaluate_cashflow(forecast_flat["Net CF ($)"], forecast_flat["Date"], fixed_offer=None, gross_cf=forecast_flat["Gross CF ($)"])
                val_flat_dcf_25 = engine.evaluate_cashflow(forecast_flat["Net CF ($)"], forecast_flat["Date"], fixed_offer=val_flat_dcf["PV 25.0% ($)"], gross_cf=forecast_flat["Gross CF ($)"])
                val_flat_original_matrix = engine.evaluate_cashflow(forecast_flat["Net CF ($)"], forecast_flat["Date"], fixed_offer=matrix_net_offer, gross_cf=forecast_flat["Gross CF ($)"])
                val_flat_stressed_matrix = engine.evaluate_cashflow(forecast_flat["Net CF ($)"], forecast_flat["Date"], fixed_offer=stressed_matrix_net, gross_cf=forecast_flat["Gross CF ($)"])
                
                loader_placeholder.empty()
                st.success("Evaluation completed successfully!")
                
                if abandon_data.get("used_fallback_tvd"):
                    st.warning("⚠️ **P&A Depth Warning**: Missing TVD for inactive wells (or 0 inactive wells detected by the table but manually overridden). Used the average TVD of the active wells as a fallback.")
                if wells_summary and wells_summary.get("wi_custom_count", 0) > 0:
                    st.info(f"**P&A WI Allocation**: {wells_summary['wi_custom_count']} wells used their own custom WI from the table; {wells_summary['wi_default_count']} wells used the default {wi_pct} WI.")
                if matrix_net_offer < 0:
                    st.warning("⚠️ **Financial Liability Alert**: The estimated P&A costs for the inactive wells exceed the projected revenue of the asset. A negative net offer implies that acquiring this property would require the seller to compensate Community Minerals to cover the impending abandonment liabilities.")

                if wells_summary and audit_res:
                    st.subheader("Phase 1: WF & CO2 Technical Identification")
                    col_a1, col_a2 = st.columns(2)
                    tvd_pass = audit_res["tvd_pass"]
                    col_a1.metric("TVD Consistency (Producers vs Injectors)", audit_res["tvd_match"], "Pass: Same Zone" if tvd_pass else "Fail: Different Zones", delta_color="normal" if tvd_pass else "inverse")
                    w_pass = audit_res["water_pass"]
                    col_a2.metric("Waterflood Signature (Water vs Oil)", audit_res["water_signature"], "Pass: Active Sweep" if w_pass else "Fail/Unverified", delta_color="normal" if w_pass else "inverse")
                    non_oil_w = wells_summary["active_non_oil"]
                    st.caption(f"Audit uses ACTIVE oil producers vs ACTIVE injectors (average TVD). Other active wells not audited: {non_oil_w['n']} (Gas: {non_oil_w['gas']}, SWD: {non_oil_w['swd']}, Other: {non_oil_w['other']}).")
                    st.markdown("---")
                
                st.subheader("Estimated Offers & Returns (Strip Pricing)")
                col_o1, col_o2, col_o3 = st.columns(3)
                
                with col_o1:
                    st.markdown("**Valuation 1: Matrix Rule**")
                    if interest_type == "WI":
                        st.write(f"Gross Offer ({assigned_mult}x): **{f'-${abs(matrix_gross_offer):,.0f}' if matrix_gross_offer < 0 else f'${matrix_gross_offer:,.0f}'}**")
                        st.write(f"P&A Deduction: **-${abandon_data['total']:,.0f}**")
                    net_str = f"-${abs(matrix_net_offer):,.0f}" if matrix_net_offer < 0 else f"${matrix_net_offer:,.0f}"
                    st.metric("Final Net Matrix Offer", net_str)
                    irr_wf = val_wf['IRR (%)']
                    if np.isnan(irr_wf): st.metric("IRR (%)", "N/A")
                    else: st.metric("IRR (%)", f"{irr_wf:.2f}%", delta=f"Hurdle Rate {HURDLE_IRR:g}%", delta_color="inverse" if irr_wf < HURDLE_IRR else "normal")
                    st.metric("Annual CF Multiple", fmt_mult(val_wf["Annual CF Multiple (x)"]))
                    
                with col_o2:
                    st.markdown("**Valuation 2: Target 25% IRR**")
                    val_dcf_25_str = f"-${abs(val_dcf_25['Estimated Offer ($)']):,.0f}" if val_dcf_25['Estimated Offer ($)'] < 0 else f"${val_dcf_25['Estimated Offer ($)']:,.0f}"
                    st.metric("Estimated Offer Value", val_dcf_25_str)
                    st.metric("Annual CF Multiple", fmt_mult(val_dcf_25["Annual CF Multiple (x)"]))
                    
                with col_o3:
                    st.markdown("**Valuation 3: Standard DCF (PV 17.5%)**")
                    val_dcf17_str = f"-${abs(val_dcf['Estimated Offer ($)']):,.0f}" if val_dcf['Estimated Offer ($)'] < 0 else f"${val_dcf['Estimated Offer ($)']:,.0f}"
                    st.metric("Estimated Offer Value", val_dcf17_str)
                    st.metric("Annual CF Multiple", fmt_mult(val_dcf["Annual CF Multiple (x)"]))

                st.markdown("---")
                
                st.subheader("Offers by Decline Scenario (Strip Pricing)")
                st.caption("Same valuations computed with the 1-Year and the 5-Year decline rates. Annual CF Multiple = Offer / Year-1 gross cash flow (before P&A).")
                scen_rows = []
                for row_label, row_key in (("1. Matrix Rule", "matrix"), ("2. Target 25% IRR", "pv25"), ("3. Standard DCF (PV 17.5%)", "pv175")):
                    rec = {"Valuation": row_label}
                    for sc_label, sc in scen_results.items():
                        v = sc[row_key]
                        rec[f"{sc_label} ({sc['dec']:.2f}%): Offer"] = fmt_usd(v["Estimated Offer ($)"])
                        rec[f"{sc_label}: IRR"] = fmt_irr(v["IRR (%)"]) if row_key == "matrix" else "—"
                        rec[f"{sc_label}: Annual CF Multiple"] = fmt_mult(v["Annual CF Multiple (x)"])
                    scen_rows.append(rec)
                st.table(pd.DataFrame(scen_rows).set_index("Valuation"))

                st.markdown("---")
                
                st.subheader("Stress Test Valuations ($50 WTI / $3 NYMEX)")
                st.caption("Worst-case flat realized prices: no oil / gas differentials are applied.")
                c_st1, c_st2, c_st3, c_st4 = st.columns(4)
                
                with c_st1:
                    st.markdown("**1. Original Matrix (Risk)**")
                    st.metric("Original Assumed Offer", net_str)
                    irr_flat_orig = val_flat_original_matrix['IRR (%)']
                    if np.isnan(irr_flat_orig): st.metric("Dropped IRR (%)", "N/A")
                    else: st.metric("Dropped IRR (%)", f"{irr_flat_orig:.2f}%", delta=f"Compared to {HURDLE_IRR:g}%", delta_color="inverse" if irr_flat_orig < HURDLE_IRR else "normal")
                    st.metric("Annual CF Multiple", fmt_mult(val_flat_original_matrix["Annual CF Multiple (x)"]))
                
                with c_st2:
                    st.markdown("**2. New Stressed Matrix**")
                    stressed_net_str = f"-${abs(stressed_matrix_net):,.0f}" if stressed_matrix_net < 0 else f"${stressed_matrix_net:,.0f}"
                    st.metric("Stressed Offer Entry", stressed_net_str)
                    irr_stressed = val_flat_stressed_matrix['IRR (%)']
                    if np.isnan(irr_stressed): st.metric("IRR (%)", "N/A")
                    else: st.metric("IRR (%)", f"{irr_stressed:.2f}%")
                    st.metric("Annual CF Multiple", fmt_mult(val_flat_stressed_matrix["Annual CF Multiple (x)"]))
                    
                with c_st3:
                    st.markdown("**3. Target 25% (Flat)**")
                    flat_25_str = f"-${abs(val_flat_dcf_25['Estimated Offer ($)']):,.0f}" if val_flat_dcf_25['Estimated Offer ($)'] < 0 else f"${val_flat_dcf_25['Estimated Offer ($)']:,.0f}"
                    st.metric("Required Offer (25%)", flat_25_str)
                    st.metric("Annual CF Multiple", fmt_mult(val_flat_dcf_25["Annual CF Multiple (x)"]))
                    
                with c_st4:
                    st.markdown("**4. DCF 17.5% (Flat)**")
                    flat_17_str = f"-${abs(val_flat_dcf['Estimated Offer ($)']):,.0f}" if val_flat_dcf['Estimated Offer ($)'] < 0 else f"${val_flat_dcf['Estimated Offer ($)']:,.0f}"
                    st.metric("Required Offer (17.5%)", flat_17_str)
                    st.metric("Annual CF Multiple", fmt_mult(val_flat_dcf["Annual CF Multiple (x)"]))

                if interest_type == "WI":
                    st.info(f"WI Liability Note: The calculations above include an exact estimated P&A liability of **${abandon_data['total']:,.0f}** deducted entirely in Month 1 based on the **{final_inactive_count} inactive wells**.")

                st.markdown("---")
                
                st.subheader("Fluid Future Prices (WTI & NYMEX)")
                end_date_prices = pd.Timestamp.today() + pd.DateOffset(years=10)
                price_chart_data = df_prices[(df_prices["Date"] >= pd.Timestamp.today()) & (df_prices["Date"] <= end_date_prices)]
                
                if not price_chart_data.empty:
                    fig_prices = make_subplots(specs=[[{"secondary_y": True}]])
                    fig_prices.add_trace(go.Scatter(x=price_chart_data["Date"], y=price_chart_data["WTI"], name="WTI", line=dict(color="#004AAD", width=3)), secondary_y=False)
                    fig_prices.add_trace(go.Scatter(x=price_chart_data["Date"], y=price_chart_data["NYMEX"], name="NYMEX", line=dict(color="#FFE500", width=3)), secondary_y=True)
                    
                    fig_prices.update_layout(
                        hovermode="x unified",
                        plot_bgcolor="white", paper_bgcolor="white",
                        font=dict(color="#0F2A4A", size=12), 
                        legend=dict(font=dict(color="#0F2A4A")), 
                        margin=dict(l=70, r=70, t=30, b=30) 
                    )
                    fig_prices.update_xaxes(title_text="", showgrid=False, dtick="M12", tickformat="%b %Y", tickfont=dict(color="#0F2A4A"))
                    fig_prices.update_yaxes(title_text="WTI Price (USD/bbl)", title_standoff=15, title_font=dict(color="#0F2A4A"), showgrid=True, gridcolor="rgba(0,0,0,0.1)", tickformat=".1f", tickfont=dict(color="#0F2A4A"), secondary_y=False)
                    fig_prices.update_yaxes(title_text="NYMEX Price (USD/MMBtu)", title_standoff=15, title_font=dict(color="#0F2A4A"), showgrid=False, tickformat=".1f", tickfont=dict(color="#0F2A4A"), secondary_y=True)
                    
                    st.plotly_chart(fig_prices, use_container_width=True, theme=None)

                st.markdown("---")
                
                st.subheader("Net Cash Flow Projection (First 10 years)")
                st.caption("Visual comparison of the 1-Year vs 5-Year decline rate scenarios (all offers use the higher, i.e. worst-case, decline). (You can zoom in to see the details ignoring the Month 1 P&A drop).")
                
                cf_120_5yr = forecast_5yr.head(120)
                cf_120_1yr = forecast_1yr.head(120)
                
                fig_cf = go.Figure()
                fig_cf.add_trace(go.Scatter(x=cf_120_5yr["Date"], y=cf_120_5yr["Net CF ($)"], name="Net CF (5-Yr Decline)", line=dict(color="#FFE500", width=3)))
                fig_cf.add_trace(go.Scatter(x=cf_120_1yr["Date"], y=cf_120_1yr["Net CF ($)"], name="Net CF (1-Yr Decline)", line=dict(color="#38B6FF", width=3)))
                
                fig_cf.update_layout(
                    hovermode="x unified",
                    plot_bgcolor="white", paper_bgcolor="white",
                    font=dict(color="#0F2A4A", size=12),
                    legend=dict(font=dict(color="#0F2A4A")), 
                    margin=dict(l=70, r=40, t=30, b=30) 
                )
                fig_cf.update_xaxes(title_text="", showgrid=False, dtick="M12", tickformat="%b %Y", tickfont=dict(color="#0F2A4A"))
                fig_cf.update_yaxes(title_text="Net Cash Flow (USD)", title_standoff=15, title_font=dict(color="#0F2A4A"), showgrid=True, gridcolor="rgba(0,0,0,0.1)", tickformat=".0f", tickfont=dict(color="#0F2A4A"))
                
                st.plotly_chart(fig_cf, use_container_width=True, theme=None)
                
                st.markdown("---")
                
                st.subheader("Underwriter Technical Notes (Copy & Paste)")
                if wells_summary:
                    str_1y = f"{dec_1yr_input:.2f}% (Auto-detected: {fit_1y['eff_decline']*100:.1f}%)" if fit_1y['success'] else f"{dec_1yr_input:.2f}%"
                    str_5y = f"{dec_5yr_input:.2f}% (Auto-detected: {fit_5y['eff_decline']*100:.1f}%)" if fit_5y['success'] else f"{dec_5yr_input:.2f}%"
                    
                    gross_val_str = f"-${abs(matrix_gross_offer):,.0f}" if matrix_gross_offer < 0 else f"${matrix_gross_offer:,.0f}"
                    
                    pa_line_str = f"- P&A Deduction = -${abandon_data['total']:,.0f}\n" if interest_type == "WI" else ""
                    
                    scen_notes = "\n".join(
                        f"- {k} Decline ({v['dec']:.2f}%): Target 25% = {fmt_usd(v['pv25']['Estimated Offer ($)'])} | DCF 17.5% = {fmt_usd(v['pv175']['Estimated Offer ($)'])} | Annual CF Multiple (Net Matrix Offer) = {fmt_mult(v['matrix']['Annual CF Multiple (x)'])}"
                        for k, v in scen_results.items())
                    
                    all_calculated_offers = [
                        matrix_net_offer, val_dcf_25['Estimated Offer ($)'], val_dcf['Estimated Offer ($)'],
                        stressed_matrix_net, val_flat_dcf_25['Estimated Offer ($)'], val_flat_dcf['Estimated Offer ($)']
                    ]
                    all_calculated_offers += [v[k]["Estimated Offer ($)"] for v in scen_results.values() for k in ("pv25", "pv175")]
                    valid_offers = [o for o in all_calculated_offers if not np.isnan(o)]
                    min_offer = min(valid_offers) if valid_offers else 0
                    max_offer = max(valid_offers) if valid_offers else 0
                    
                    min_offer_str = f"-${abs(min_offer):,.0f}" if min_offer < 0 else f"${min_offer:,.0f}"
                    max_offer_str = f"-${abs(max_offer):,.0f}" if max_offer < 0 else f"${max_offer:,.0f}"
                    
                    final_range_text = f"\n**Recommended Offer Range (Worst Case to Best Case): {min_offer_str} to {max_offer_str}**"

                    if wells_summary["total_wells"] == 1:
                        notes = f"""Write Up
- Interest Type Evaluated: {interest_type.upper()}

Production will be forecasted using OFM:
- Production decline rate for last yr = {str_1y}
- Production decline rate for last 5 yrs = {str_5y}
"""
                        if interest_type == "WI":
                            notes += f"\nSince this is a Working Interest (WI) evaluation, environmental liability was accounted for.\n"

                        notes += f"""
Since the decline rates represent a {risk_class}, a default multiple of {assigned_mult}X will be used to generate the base offer. The higher (worst-case) of the two decline rates ({worst_decline:.2f}%) is applied to all valuations.

Base Valuation Calculation (Strip Pricing):
- Gross Offer ({assigned_mult}X * ${lycf:,.0f} LYCF) = {gross_val_str}
{pa_line_str}- Net Matrix Offer = {net_str}
- Annual CF Multiple of the Net Matrix Offer = {fmt_mult(val_wf['Annual CF Multiple (x)'])}

Generated Evaluation Offer Range (Strip Pricing):
- Valuation 1 (Net Matrix Rule @ {assigned_mult}x): {net_str}
- Valuation 2 (Target 25% IRR): {val_dcf_25_str}
- Valuation 3 (Standard DCF PV 17.5%): {val_dcf17_str}

Offers by Decline Scenario (Strip Pricing):
{scen_notes}

Stress Test Valuations ($50 WTI / $3 NYMEX, flat realized prices, no differentials):
- Stressed Matrix Offer Entry: {stressed_net_str}
- Required Offer (Target 25%): {flat_25_str}
- Required Offer (DCF 17.5%): {flat_17_str}
{final_range_text}"""

                        if matrix_net_offer < 0:
                            notes += "\n\n⚠️ CONCLUSION: The estimated environmental liability exceeds the asset's gross value, resulting in negative valuation scenarios. Acquiring these wells is NOT recommended."

                    else:
                        active_prod, tvd_prod = wells_summary['active_oil']['n'], wells_summary['active_oil']['tvd_avg']
                        active_inj, tvd_inj = wells_summary['active_inj']['n'], wells_summary['active_inj']['tvd_avg']
                        
                        def fmt_tvd(v): return f"{v:,.0f}'" if not np.isnan(v) else "N/A"
                        
                        tmin, tmax = wells_summary['inactive_ta']['tvd_min'], wells_summary['inactive_ta']['tvd_max']
                        tavg_inact = wells_summary['inactive_ta']['tvd_avg']
                        tvd_range = f"{tmin:,.0f}' - {tmax:,.0f}' (AVG TVD: {fmt_tvd(tavg_inact)})" if not np.isnan(tmin) else "N/A"
                        
                        if audit_res['tvd_pass'] and audit_res['water_pass']:
                            audit_p = f"The presence of {active_inj} injection wells with an average TVD ({fmt_tvd(tvd_inj)}), comparable to the producing wells ({fmt_tvd(tvd_prod)}), confirms Zone and TVD Consistency. Furthermore, the Waterflood Signature Identification verifies that the water production volume is substantially and consistently higher than the oil production ({audit_res['water_signature']}). This rigorously confirms the unit is under an active secondary recovery project."
                        else:
                            audit_p = "⚠️ The project failed the Waterflood Technical Audit. "
                            if not audit_res['tvd_pass']: audit_p += "Injection wells do not share a highly similar TVD with producers. "
                            if not audit_res['water_pass']: audit_p += "Water production volume does not exhibit an active waterflood signature. "
                            audit_p += "There may be no active waterflood supporting this asset."

                        notes = f"""Write Up
- Interest Type Evaluated: {interest_type.upper()}
- Unit located in DI:
  - {active_prod + active_inj} ACTIVE wells
    - {active_inj} Injection wells (AVG TVD = {fmt_tvd(tvd_inj)})
    - {active_prod} Oil producers (AVG TVD = {fmt_tvd(tvd_prod)})
  - {wells_summary['inactive_ta']['n']} INACTIVE / SHUT-IN / TA wells
    - TVD range for inactive wells: {tvd_range}
  - {wells_summary['pa_wells']['n']} P&A wells"""
  
                        nonoil = wells_summary["active_non_oil"]
                        if nonoil["n"] > 0:
                            notes += f"\n  - {nonoil['n']} other ACTIVE wells not used in the audit (Gas: {nonoil['gas']}, SWD: {nonoil['swd']}, Other: {nonoil['other']})"

                        if wells_summary.get("other_statuses"):
                            other_total = sum(wells_summary["other_statuses"].values())
                            notes += f"\n  - {other_total} OTHER wells (Statuses: {', '.join([f'{k} ({v})' for k, v in wells_summary['other_statuses'].items()])})"
                            
                        if wells_summary.get("omitted_sts", 0) > 0:
                            notes += f"\n  - {wells_summary['omitted_sts']} Sidetracks/Duplicates omitted (Filtered to prevent duplicate TVD and P&A counts)"

                        notes += f"\n\n{audit_p}\n\nTherefore, production will be forecasted using OFM:\n- Production decline rate for last yr = {str_1y}\n- Production decline rate for last 5 yrs = {str_5y}\n"
                        
                        if interest_type == "WI":
                            notes += f"\nSince this is a Working Interest (WI) evaluation, a total of {final_inactive_count} non-producing wells (Inactive/Shut-in/TA; wells already P&A are excluded) were accounted for environmental liability.\n"

                        notes += f"""
Since the decline rates represent a {risk_class}, a default multiple of {assigned_mult}X will be used to generate the base offer. The higher (worst-case) of the two decline rates ({worst_decline:.2f}%) is applied to all valuations.

Base Valuation Calculation (Strip Pricing):
- Gross Offer ({assigned_mult}X * ${lycf:,.0f} LYCF) = {gross_val_str}
{pa_line_str}- Net Matrix Offer = {net_str}
- Annual CF Multiple of the Net Matrix Offer = {fmt_mult(val_wf['Annual CF Multiple (x)'])}

Generated Evaluation Offer Range (Strip Pricing):
- Valuation 1 (Net Matrix Rule @ {assigned_mult}x): {net_str}
- Valuation 2 (Target 25% IRR): {val_dcf_25_str}
- Valuation 3 (Standard DCF PV 17.5%): {val_dcf17_str}

Offers by Decline Scenario (Strip Pricing):
{scen_notes}

Stress Test Valuations ($50 WTI / $3 NYMEX, flat realized prices, no differentials):
- Stressed Matrix Offer Entry: {stressed_net_str}
- Required Offer (Target 25%): {flat_25_str}
- Required Offer (DCF 17.5%): {flat_17_str}
{final_range_text}"""
                        
                        if matrix_net_offer < 0:
                            notes += "\n\n⚠️ CONCLUSION: The estimated environmental liability exceeds the asset's gross value, resulting in negative valuation scenarios. Acquiring these wells is NOT recommended."

                    st.code(notes, language="markdown")

        except Exception as e:
            loader_placeholder.empty()
            st.error(f"An error occurred while processing the data: {str(e)}")

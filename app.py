import streamlit as st
import pandas as pd
import numpy as np
import time
import base64
import os
import engine 
import plotly.graph_objects as go
from plotly.subplots import make_subplots

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

if 'run_eval' not in st.session_state:
    st.session_state.run_eval = False

st.title("WaterFlood Tool")
st.markdown("---")

with st.sidebar:
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)
        st.markdown("<br>", unsafe_allow_html=True)

    st.header("1. Base Financials")
    lycf = st.number_input("Base LYCF ($/year)", value=0.0, step=1000.0, key="input_lycf")
    st.markdown("---")
    
    st.header("2. Interest & Liabilities")
    interest_type = st.selectbox("Select Interest Type", ["NRI", "ORRI", "WI"], key="input_interest")
    
    if interest_type == "WI":
        wi_pct = st.number_input("Average Working Interest (WI) Decimal (e.g. 0.0005)", min_value=0.0, max_value=1.0, value=0.0, format="%.6f", key="input_wi")
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
        dec_1yr_input = st.number_input("1-Year Decline (%)", value=0.0, key="input_dec_1")
        dec_5yr_input = st.number_input("5-Year Decline (%)", value=0.0, key="input_dec_5")
        
        worst_decline = max(dec_1yr_input, dec_5yr_input)
        if worst_decline > 20.0: assigned_mult, risk_class = 1.9, "High Decline"
        elif worst_decline >= 10.0: assigned_mult, risk_class = 2.5, "Moderate Decline"
        else: assigned_mult, risk_class = 3.0, "Low Decline"
        st.info(f"Assigned Multiple: {assigned_mult}x\n\nRisk Class: {risk_class}")

st.subheader("Data Upload")
if interest_type == "WI" and operator_distress:
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
        price_file = st.file_uploader("2. Price Input File", type=["csv", "xlsx", "xls"], key="file_price")
        st.caption("Downloaded from [Template folder](https://drive.google.com/drive/folders/1XSFTshnnQ-n66_x1SSceVkgHrKLr2dty?usp=drive_link)")
    with col3:
        wells_file = st.file_uploader("3. Wells Table", type=["csv", "xlsx"], key="file_wells")
        st.caption("Export WG table from Drilling Info")

st.markdown("---")
if st.button("Run WaterFlood Evaluation", type="primary", key="btn_run"):
    st.session_state.run_eval = True

if st.session_state.run_eval:
    if lycf == 0.0 or (interest_type == "WI" and wi_pct == 0.0):
        st.warning("Warning: LYCF and Average WI Decimal cannot be 0. Please check the sidebar inputs.")
    elif interest_type == "WI" and operator_distress and not wells_file:
        st.warning("Please upload the Wells Table to calculate the P&A liability.")
    elif not (interest_type == "WI" and operator_distress) and (not prod_file or not price_file or (interest_type == "WI" and not wells_file)):
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
            
        time.sleep(1.5)
        
        try:
            if interest_type == "WI" and operator_distress:
                if wells_file.name.lower().endswith('.csv'): df_wells = pd.read_csv(wells_file)
                else: df_wells = pd.read_excel(wells_file, engine='openpyxl')
                wells_summary = engine.summarize_wells(df_wells)
                total_wells = wells_summary.get("total_wells", 0)
                abandon_data = engine.calculate_abandonment(interest_type=interest_type, n_inactive_wells=total_wells, wi_pct=wi_pct, tvd_avg=7500.0, cost_per_ft=pa_cost_ft, months=360, mode=pa_mode, spread_months=pa_spread_months, exact_tvd_sum=wells_summary.get("total_tvd_sum", 0.0))
                
                loader_placeholder.empty()
                st.success("Evaluation completed successfully!")
                st.subheader("Distress Scenario Results")
                st.error(f"Negative CF Rule Applied: Total P&A liability calculated for ALL {total_wells} wells (Active + Inactive).")
                col_d1, col_d2 = st.columns(2)
                col_d1.metric("Baseline LYCF", f"-${abs(lycf):,.0f}" if lycf < 0 else f"${lycf:,.0f}")
                col_d2.metric("Total P&A Liability Deduction", f"-${abandon_data['total']:,.0f}")
                
            else:
                if prod_file.name.lower().endswith('.csv'): df_prod = pd.read_csv(prod_file)
                else: df_prod = pd.read_excel(prod_file, engine='openpyxl')
                df_prod_clean = engine.clean_production(df_prod)
                
                if price_file.name.lower().endswith('.csv'): df_prices_raw = pd.read_csv(price_file)
                else: df_prices_raw = pd.read_excel(price_file, sheet_name=0, engine='openpyxl')
                df_prices = engine.clean_prices(df_prices_raw)
                
                base_ratios = engine.base_prices(df_prices, df_prod_clean, months=12)
                fit_1y = engine.fit_arps(engine.rate_window(df_prod_clean, 12)["qo"].values, kind="auto")
                fit_5y = engine.fit_arps(engine.rate_window(df_prod_clean, 60)["qo"].values, kind="auto")
                
                tvd_avg, n_inactive_auto, tvd_sum_auto = 7500.0, 0, 0.0
                wells_summary, audit_res = None, None
                
                if wells_file:
                    if wells_file.name.lower().endswith('.csv'): df_wells = pd.read_csv(wells_file)
                    else: df_wells = pd.read_excel(wells_file, engine='openpyxl')
                    wells_summary = engine.summarize_wells(df_wells)
                    n_inactive_auto = wells_summary.get("inactive_ta", {}).get("n", 0)
                    tvd_avg = wells_summary.get("inactive_ta", {}).get("tvd_avg", 7500.0)
                    if pd.isna(tvd_avg): tvd_avg = 7500.0
                    tvd_sum_auto = wells_summary.get("inactive_ta", {}).get("tvd_sum", 0.0)
                    audit_res = engine.audit_waterflood(df_prod_clean, wells_summary)
                
                final_inactive_count = n_inactive_override if n_inactive_override > 0 else n_inactive_auto
                exact_sum = tvd_sum_auto if n_inactive_override == 0 else None
                
                abandon_data = engine.calculate_abandonment(interest_type=interest_type, n_inactive_wells=final_inactive_count, wi_pct=wi_pct, tvd_avg=tvd_avg, cost_per_ft=pa_cost_ft, months=360, mode=pa_mode, spread_months=pa_spread_months, exact_tvd_sum=exact_sum)
                
                forecast_5yr = engine.build_forecast(months=360, lycf=lycf, decline=(dec_5yr_input/100.0), prices=df_prices, base_prices_ratios=base_ratios, start=pd.Timestamp.today().normalize(), last_prod=df_prod_clean["Ym"].max(), abandon_schedule=abandon_data["schedule"])
                forecast_1yr = engine.build_forecast(months=360, lycf=lycf, decline=(dec_1yr_input/100.0), prices=df_prices, base_prices_ratios=base_ratios, start=pd.Timestamp.today().normalize(), last_prod=df_prod_clean["Ym"].max(), abandon_schedule=abandon_data["schedule"])
                
                val_dcf = engine.evaluate_cashflow(forecast_5yr["Net CF ($)"], forecast_5yr["Date"], fixed_offer=None)
                val_dcf_25 = engine.evaluate_cashflow(forecast_5yr["Net CF ($)"], forecast_5yr["Date"], fixed_offer=val_dcf["PV 25.0% ($)"])
                
                matrix_gross_offer = assigned_mult * lycf
                matrix_net_offer = matrix_gross_offer - abandon_data['total']
                val_wf = engine.evaluate_cashflow(forecast_5yr["Net CF ($)"], forecast_5yr["Date"], fixed_offer=matrix_net_offer)
                
                df_prices_flat = df_prices.copy()
                df_prices_flat["WTI"] = 50.0
                df_prices_flat["NYMEX"] = 3.0
                
                stressed_lycf = engine.calculate_stressed_lycf(lycf, base_ratios, stressed_oil=50.0, stressed_gas=3.0)
                stressed_matrix_gross = assigned_mult * stressed_lycf
                stressed_matrix_net = stressed_matrix_gross - abandon_data['total']
                
                forecast_flat = engine.build_forecast(months=360, lycf=lycf, decline=(dec_5yr_input/100.0), prices=df_prices_flat, base_prices_ratios=base_ratios, start=pd.Timestamp.today().normalize(), last_prod=df_prod_clean["Ym"].max(), abandon_schedule=abandon_data["schedule"])
                val_flat_dcf = engine.evaluate_cashflow(forecast_flat["Net CF ($)"], forecast_flat["Date"], fixed_offer=None)
                val_flat_dcf_25 = engine.evaluate_cashflow(forecast_flat["Net CF ($)"], forecast_flat["Date"], fixed_offer=val_flat_dcf["PV 25.0% ($)"])
                val_flat_original_matrix = engine.evaluate_cashflow(forecast_flat["Net CF ($)"], forecast_flat["Date"], fixed_offer=matrix_net_offer)
                val_flat_stressed_matrix = engine.evaluate_cashflow(forecast_flat["Net CF ($)"], forecast_flat["Date"], fixed_offer=stressed_matrix_net)
                
                loader_placeholder.empty()
                st.success("Evaluation completed successfully!")
                
                if matrix_net_offer < 0:
                    st.warning("⚠️ **Financial Liability Alert**: The estimated P&A costs for the inactive wells exceed the projected revenue of the asset. A negative net offer implies that acquiring this property would require the seller to compensate Community Minerals to cover the impending abandonment liabilities.")

                if wells_summary and audit_res:
                    st.subheader("Phase 1: WF & CO2 Technical Identification")
                    col_a1, col_a2 = st.columns(2)
                    tvd_pass = audit_res["tvd_pass"]
                    col_a1.metric("TVD Consistency (Producers vs Injectors)", audit_res["tvd_match"], "Pass: Same Zone" if tvd_pass else "Fail: Different Zones", delta_color="normal" if tvd_pass else "inverse")
                    w_pass = audit_res["water_pass"]
                    col_a2.metric("Waterflood Signature (Water vs Oil)", audit_res["water_signature"], "Pass: Active Sweep" if w_pass else "Fail/Unverified", delta_color="normal" if w_pass else "inverse")
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
                    else: st.metric("IRR (%)", f"{irr_wf:.2f}%", delta="Hurdle Rate 15%", delta_color="inverse" if irr_wf < 15 else "normal")
                    
                with col_o2:
                    st.markdown("**Valuation 2: Target 25% IRR**")
                    val_dcf_25_str = f"-${abs(val_dcf_25['Estimated Offer ($)']):,.0f}" if val_dcf_25['Estimated Offer ($)'] < 0 else f"${val_dcf_25['Estimated Offer ($)']:,.0f}"
                    st.metric("Estimated Offer Value", val_dcf_25_str)
                    irr_25 = val_dcf_25['IRR (%)']
                    if np.isnan(irr_25): st.metric("IRR (%)", "25.00%", "Target 25% Met")
                    else: st.metric("IRR (%)", f"{irr_25:.2f}%", "Target 25% Met")
                    
                with col_o3:
                    st.markdown("**Valuation 3: Standard DCF (PV 17.5%)**")
                    val_dcf17_str = f"-${abs(val_dcf['Estimated Offer ($)']):,.0f}" if val_dcf['Estimated Offer ($)'] < 0 else f"${val_dcf['Estimated Offer ($)']:,.0f}"
                    st.metric("Estimated Offer Value", val_dcf17_str)
                    irr_17 = val_dcf['IRR (%)']
                    if np.isnan(irr_17): st.metric("IRR (%)", "17.50%", "Hurdle 17.5% Met")
                    else: st.metric("IRR (%)", f"{irr_17:.2f}%", "Hurdle 17.5% Met")

                st.markdown("---")
                
                st.subheader("Stress Test Valuations ($50 WTI / $3 NYMEX)")
                c_st1, c_st2, c_st3, c_st4 = st.columns(4)
                
                with c_st1:
                    st.markdown("**1. Original Matrix (Risk)**")
                    st.metric("Original Assumed Offer", net_str)
                    irr_flat_orig = val_flat_original_matrix['IRR (%)']
                    if np.isnan(irr_flat_orig): st.metric("Dropped IRR (%)", "N/A")
                    else: st.metric("Dropped IRR (%)", f"{irr_flat_orig:.2f}%", delta="Compared to 15%", delta_color="inverse" if irr_flat_orig < 15 else "normal")
                
                with c_st2:
                    st.markdown("**2. New Stressed Matrix**")
                    stressed_net_str = f"-${abs(stressed_matrix_net):,.0f}" if stressed_matrix_net < 0 else f"${stressed_matrix_net:,.0f}"
                    st.metric("Stressed Offer Entry", stressed_net_str)
                    irr_stressed = val_flat_stressed_matrix['IRR (%)']
                    if np.isnan(irr_stressed): st.metric("IRR (%)", "N/A")
                    else: st.metric("IRR (%)", f"{irr_stressed:.2f}%")
                    
                with c_st3:
                    st.markdown("**3. Target 25% (Flat)**")
                    flat_25_str = f"-${abs(val_flat_dcf_25['Estimated Offer ($)']):,.0f}" if val_flat_dcf_25['Estimated Offer ($)'] < 0 else f"${val_flat_dcf_25['Estimated Offer ($)']:,.0f}"
                    st.metric("Required Offer (25%)", flat_25_str)
                    irr_flat_25 = val_flat_dcf_25['IRR (%)']
                    if np.isnan(irr_flat_25): st.metric("IRR (%)", "25.00%")
                    else: st.metric("IRR (%)", f"{irr_flat_25:.2f}%")
                    
                with c_st4:
                    st.markdown("**4. DCF 17.5% (Flat)**")
                    flat_17_str = f"-${abs(val_flat_dcf['Estimated Offer ($)']):,.0f}" if val_flat_dcf['Estimated Offer ($)'] < 0 else f"${val_flat_dcf['Estimated Offer ($)']:,.0f}"
                    st.metric("Required Offer (17.5%)", flat_17_str)
                    irr_flat_17 = val_flat_dcf['IRR (%)']
                    if np.isnan(irr_flat_17): st.metric("IRR (%)", "17.50%")
                    else: st.metric("IRR (%)", f"{irr_flat_17:.2f}%")

                if interest_type == "WI":
                    st.info(f"WI Liability Note: The calculations above include an exact estimated P&A liability of **${abandon_data['total']:,.0f}** deducted entirely in Month 1 based on the **{final_inactive_count} inactive wells**.")

                st.markdown("---")
                
                # --- GRÁFICA DE PRECIOS OPTIMIZADA CON MÁRGENES ---
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
                        margin=dict(l=70, r=70, t=30, b=30) # Márgenes aumentados para evitar choques
                    )
                    fig_prices.update_xaxes(title_text="", showgrid=False, dtick="M12", tickformat="%b %Y", tickfont=dict(color="#0F2A4A"))
                    fig_prices.update_yaxes(title_text="WTI Price (USD/bbl)", title_standoff=15, title_font=dict(color="#0F2A4A"), showgrid=True, gridcolor="rgba(0,0,0,0.1)", tickformat=".1f", tickfont=dict(color="#0F2A4A"), secondary_y=False)
                    fig_prices.update_yaxes(title_text="NYMEX Price (USD/MMBtu)", title_standoff=15, title_font=dict(color="#0F2A4A"), showgrid=False, tickformat=".1f", tickfont=dict(color="#0F2A4A"), secondary_y=True)
                    
                    st.plotly_chart(fig_prices, use_container_width=True, theme=None)

                st.markdown("---")
                
                # --- GRÁFICA DE FLUJO DE CAJA OPTIMIZADA CON MÁRGENES ---
                st.subheader("Net Cash Flow Projection (First 10 years)")
                st.caption("Visual comparison of the 1-Year vs 5-Year decline rate scenarios. (You can zoom in to see the details ignoring the Month 1 P&A drop).")
                
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
                    margin=dict(l=70, r=40, t=30, b=30) # Márgenes aumentados para evitar choques
                )
                fig_cf.update_xaxes(title_text="", showgrid=False, dtick="M12", tickformat="%b %Y", tickfont=dict(color="#0F2A4A"))
                fig_cf.update_yaxes(title_text="Net Cash Flow (USD)", title_standoff=15, title_font=dict(color="#0F2A4A"), showgrid=True, gridcolor="rgba(0,0,0,0.1)", tickformat=".0f", tickfont=dict(color="#0F2A4A"))
                
                st.plotly_chart(fig_cf, use_container_width=True, theme=None)
                
                st.markdown("---")
                
                # --- NUEVA LÓGICA DEL WRITE UP CON LAS REGLAS REQUERIDAS ---
                st.subheader("Underwriter Technical Notes (Copy & Paste)")
                if wells_summary:
                    str_1y = f"{dec_1yr_input:.2f}% (Auto-detected: {fit_1y['eff_decline']*100:.1f}%)" if fit_1y['success'] else f"{dec_1yr_input:.2f}%"
                    str_5y = f"{dec_5yr_input:.2f}% (Auto-detected: {fit_5y['eff_decline']*100:.1f}%)" if fit_5y['success'] else f"{dec_5yr_input:.2f}%"
                    
                    if wells_summary["total_wells"] == 1:
                        notes = f"""Write Up
- Interest Type Evaluated: {interest_type.upper()}

Production will be forecasted using OFM:
- Production decline rate for last yr = {str_1y}
- Production decline rate for last 5 yrs = {str_5y}
"""
                        if interest_type == "WI":
                            notes += f"\nSince this is a Working Interest (WI) evaluation, environmental liability was accounted for.\n- Total P&A Liability Deduction: -${abandon_data['total']:,.0f}\n"

                        notes += f"""
Since the decline rates represent a {risk_class}, a default multiple of {assigned_mult}X will be used to generate the base offer. 
Generated Evaluation Offer Range:
- Valuation 1 (Net Matrix Rule @ {assigned_mult}x): {net_str}
- Valuation 2 (Target 25% IRR): {val_dcf_25_str}
- Valuation 3 (Standard DCF PV 17.5%): {val_dcf17_str}"""

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
  - {wells_summary['pa_wells']['n']} P&A wells

{audit_p}

Therefore, production will be forecasted using OFM:
- Production decline rate for last yr = {str_1y}
- Production decline rate for last 5 yrs = {str_5y}
"""
                        if interest_type == "WI":
                            notes += f"\nSince this is a Working Interest (WI) evaluation, a total of {final_inactive_count} non-producing wells (Inactive/Shut-in/TA/P&A) were accounted for environmental liability.\n- Total P&A Liability Deduction: -${abandon_data['total']:,.0f}\n"

                        notes += f"""
Since the decline rates represent a {risk_class}, a default multiple of {assigned_mult}X will be used to generate the base offer. 
Generated Evaluation Offer Range:
- Valuation 1 (Net Matrix Rule @ {assigned_mult}x): {net_str}
- Valuation 2 (Target 25% IRR): {val_dcf_25_str}
- Valuation 3 (Standard DCF PV 17.5%): {val_dcf17_str}"""
                        
                        if wells_summary.get("other_statuses"): notes += f"\n\n- Other statuses found: {', '.join([f'{k} ({v})' for k, v in wells_summary['other_statuses'].items()])}"
                        
                    st.code(notes, language="markdown")

        except Exception as e:
            loader_placeholder.empty()
            st.error(f"An error occurred while processing the data: {str(e)}")

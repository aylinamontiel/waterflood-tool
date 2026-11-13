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

    st.header

import streamlit as st

from ui.design import NAVY, MUTED, BORDER, FONT


def apply_custom_css():
    # Native config owns widget colors. CSS only supplies spacing/custom cards.
    st.html(f"""<style>
    .stApp {{ font-family: {FONT}; }}
    .stMainBlockContainer {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1680px; }}
    .dashboard-card {{ background: white; padding: 20px; border-radius: 12px;
        border: 1px solid {BORDER}; margin-bottom: 8px; min-height: 116px; }}
    .metric-title {{ color: {MUTED}; font-size: 13px; margin-bottom: 6px; overflow-wrap: anywhere; }}
    .metric-value {{ color: {NAVY}; font-size: clamp(21px, 2vw, 29px); font-weight: 700;
        font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }}
    .agent-waiting {{ color: {MUTED}; font-size: 13px; font-weight: 600; }}
    [data-testid="stMetric"] {{ background: white; border: 1px solid {BORDER};
        border-radius: 12px; padding: 16px; font-variant-numeric: tabular-nums; }}
    [data-testid="stSidebar"] {{ min-width: 240px; max-width: 260px; }}
    [data-testid="stSidebarNav"] a[aria-current="page"] {{ background: #EFF6FF; font-weight: 700; }}
    h1, h2, h3 {{ letter-spacing: -.025em; }}
    @media(max-width: 768px) {{
        .stMainBlockContainer {{ padding: 1.2rem 1rem 2rem; }}
        .dashboard-card {{ padding: 16px; min-height: 96px; }}
    }}
    </style>""")

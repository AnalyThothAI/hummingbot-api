"""Landing page - LP Dashboard home."""
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from st_utils import (
    initialize_st_page,
    get_backend_api_client,
    cached_get_active_containers,
    cached_list_script_configs,
    cached_get_gateway_status,
)

initialize_st_page(layout="wide", show_readme=False)

# Custom CSS for enhanced styling (matching official dashboard)
st.markdown("""
<style>
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1rem;
        border-radius: 10px;
        color: white;
        margin: 0.5rem 0;
    }

    .feature-card {
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 15px;
        padding: 1.5rem;
        backdrop-filter: blur(10px);
        margin: 1rem 0;
    }

    .stat-number {
        font-size: 2rem;
        font-weight: bold;
        color: #4CAF50;
    }

    .pulse {
        animation: pulse 2s infinite;
    }

    @keyframes pulse {
        0% { opacity: 1; }
        50% { opacity: 0.7; }
        100% { opacity: 1; }
    }

    .status-active {
        color: #4CAF50;
        font-weight: bold;
    }

    .status-inactive {
        color: #ff6b6b;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

api = get_backend_api_client()

# Hero Section
st.markdown("""
<div style="text-align: center; padding: 2rem 0;">
    <h1 style="font-size: 3rem; margin-bottom: 0.5rem;">🤖 LP Dashboard</h1>
    <p style="font-size: 1.2rem; color: #888; margin-bottom: 2rem;">
        Your Command Center for Gateway LP Strategy Management
    </p>
</div>
""", unsafe_allow_html=True)

# Get data for stats using cached functions
containers = cached_get_active_containers("hummingbot")
active_count = len(containers) if containers else 0

configs = cached_list_script_configs()
config_count = len(configs) if configs else 0

gateway = cached_get_gateway_status()
gw_status = "Online" if gateway.get("running") else "Offline"

try:
    is_healthy = api.is_healthy()
    api_status = "Connected" if is_healthy else "Disconnected"
except Exception:
    api_status = "Error"

# Quick Stats Dashboard
st.markdown("## 📊 Live Dashboard Overview")

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown(f"""
    <div class="metric-card">
        <h3>🤖 Active Bots</h3>
        <div class="stat-number pulse">{active_count}</div>
        <p>Currently Running</p>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="metric-card">
        <h3>📄 Configurations</h3>
        <div class="stat-number">{config_count}</div>
        <p>Available Configs</p>
    </div>
    """, unsafe_allow_html=True)

with col3:
    gw_color = "#4CAF50" if gw_status == "Online" else "#ff6b6b"
    st.markdown(f"""
    <div class="metric-card">
        <h3>🌐 Gateway</h3>
        <div class="stat-number" style="color: {gw_color};">{gw_status}</div>
        <p>Service Status</p>
    </div>
    """, unsafe_allow_html=True)

with col4:
    api_color = "#4CAF50" if api_status == "Connected" else "#ff6b6b"
    st.markdown(f"""
    <div class="metric-card">
        <h3>🔗 API Status</h3>
        <div class="stat-number" style="color: {api_color};">{api_status}</div>
        <p>Backend Connection</p>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# Performance Chart and Strategy Status
col1, col2 = st.columns([2, 1])

with col1:
    st.markdown("### 📈 Portfolio Performance (Real Data)")

    # Get real portfolio history data
    try:
        now = datetime.now()
        start_time = int((now - timedelta(days=7)).timestamp() * 1000)
        end_time = int(now.timestamp() * 1000)

        history_response = api.get_portfolio_history(
            start_time=start_time,
            end_time=end_time,
            interval="1h",
            limit=168,  # 7 days * 24 hours
        )

        history_data = history_response.get("data", [])

        if history_data:
            # Parse history data
            timestamps = []
            portfolio_values = []

            for record in reversed(history_data):  # Reverse to get chronological order
                ts = record.get("timestamp")
                if ts:
                    timestamps.append(pd.to_datetime(ts))

                    # Calculate total value from all accounts
                    # Data structure: {"timestamp": ..., "state": {"account": {"connector": [{"value": ...}]}}}
                    total_value = 0
                    state = record.get("state", {})
                    for account_name, account_data in state.items():
                        if isinstance(account_data, dict):
                            for connector_name, tokens in account_data.items():
                                if isinstance(tokens, list):
                                    for token_data in tokens:
                                        total_value += token_data.get("value", 0)
                    portfolio_values.append(total_value)

            if timestamps and portfolio_values:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=timestamps,
                    y=portfolio_values,
                    mode='lines+markers',
                    line=dict(color='#4CAF50', width=3),
                    fill='tonexty',
                    fillcolor='rgba(76, 175, 80, 0.1)',
                    name='Portfolio Value'
                ))

                fig.update_layout(
                    template='plotly_dark',
                    height=400,
                    showlegend=False,
                    margin=dict(l=0, r=0, t=0, b=0),
                    xaxis=dict(showgrid=False),
                    yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.1)', tickprefix='$')
                )

                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No portfolio history data available yet. Start trading to see your performance!")
        else:
            st.info("No portfolio history data available yet. Start trading to see your performance!")
    except Exception as e:
        st.warning(f"Could not load portfolio history: {e}")

with col2:
    st.markdown("### 🎯 Strategy Status")

    if containers:
        for container in containers:
            name = container.get("name", "Unknown")
            status = container.get("status", "Unknown")
            is_up = "Up" in status
            status_icon = "🟢" if is_up else "🔴"
            status_class = "status-active" if is_up else "status-inactive"

            st.markdown(f"""
            <div style="background: rgba(255,255,255,0.05); padding: 1rem; border-radius: 8px; margin: 0.5rem 0;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <strong>{name}</strong><br>
                        <span class="{status_class}">{status_icon} {status}</span>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No active strategies. Deploy one to get started!")

st.divider()

# Quick Actions
st.markdown("## ⚡ Quick Actions")

col1, col2, col3, col4 = st.columns(4)

with col1:
    if st.button("🚀 Deploy Strategy", use_container_width=True, type="primary"):
        st.switch_page("pages/2_Deploy.py")

with col2:
    if st.button("📊 View Instances", use_container_width=True):
        st.switch_page("pages/1_Overview.py")

with col3:
    if st.button("⚙️ Script Configs", use_container_width=True):
        st.switch_page("pages/4_Config.py")

with col4:
    if st.button("📋 View Logs", use_container_width=True):
        st.switch_page("pages/5_Logs.py")

st.divider()

# Feature Showcase
st.markdown("## 🚀 Platform Features")

col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("""
    <div class="feature-card">
        <div style="text-align: center; margin-bottom: 1rem;">
            <div style="font-size: 3rem;">🎯</div>
            <h3>Strategy Management</h3>
        </div>
        <ul style="list-style: none; padding: 0;">
            <li>✨ Deploy LP Strategies</li>
            <li>🔧 Start/Stop/Restart Bots</li>
            <li>📝 Configure Parameters</li>
            <li>🧪 Monitor Performance</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown("""
    <div class="feature-card">
        <div style="text-align: center; margin-bottom: 1rem;">
            <div style="font-size: 3rem;">📊</div>
            <h3>Position Monitoring</h3>
        </div>
        <ul style="list-style: none; padding: 0;">
            <li>📈 Real-time PnL Tracking</li>
            <li>🔍 Position Range Status</li>
            <li>📋 Fee Collection Stats</li>
            <li>🎨 Interactive Charts</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown("""
    <div class="feature-card">
        <div style="text-align: center; margin-bottom: 1rem;">
            <div style="font-size: 3rem;">⚡</div>
            <h3>Configuration</h3>
        </div>
        <ul style="list-style: none; padding: 0;">
            <li>🤖 Visual YAML Editor</li>
            <li>📡 Parameter Validation</li>
            <li>🛡️ Config Templates</li>
            <li>🔔 Batch Management</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)

# Footer
st.markdown("---")
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric("🤖 Total Bots", f"{active_count}")

with col2:
    st.metric("📄 Configs", f"{config_count}")

with col3:
    st.metric("🌐 Gateway", gw_status)

with col4:
    st.metric("🔗 API", api_status)

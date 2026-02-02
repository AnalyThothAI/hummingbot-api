import streamlit as st

from CONFIG import GATEWAY_ENABLED


def main_page():
    return [st.Page("frontend/pages/landing.py", title="Hummingbot Dashboard", icon="📊", url_path="landing")]


def public_pages():
    return {}


def private_pages():
    orchestration_pages = [
        st.Page("frontend/pages/orchestration/instances/app.py", title="Instances", icon="🦅", url_path="instances"),
        st.Page(
            "frontend/pages/orchestration/config_generator/app.py",
            title="Config Generator",
            icon="🧩",
            url_path="config_generator",
        ),
        st.Page("frontend/pages/orchestration/launch_bot_v2/app.py", title="Deploy V2", icon="🚀", url_path="launch_bot_v2"),
        st.Page("frontend/pages/orchestration/credentials/app.py", title="Credentials", icon="🔑", url_path="credentials"),
        st.Page("frontend/pages/orchestration/portfolio/app.py", title="Portfolio", icon="💰", url_path="portfolio"),
        st.Page("frontend/pages/orchestration/trading/app.py", title="Trading", icon="🪄", url_path="trading"),
        st.Page("frontend/pages/orchestration/archived_bots/app.py", title="Archived Bots", icon="🗃️", url_path="archived_bots"),
        st.Page("frontend/pages/orchestration/logs/app.py", title="Logs", icon="📜", url_path="logs"),
    ]
    if GATEWAY_ENABLED:
        orchestration_pages.insert(
            3,
            st.Page("frontend/pages/orchestration/gateway/app.py", title="Gateway", icon="🔗", url_path="gateway"),
        )

    pages = {
        "Bot Orchestration": orchestration_pages
    }

    return pages

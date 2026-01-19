"""
LP Dashboard - Main Streamlit Application

A dashboard for managing Gateway LP strategies with hummingbot-api.
Following the design patterns from official Hummingbot Dashboard.
"""
import streamlit as st

from st_utils import auth_system


def main():
    """Main application entry point."""
    # Get the navigation structure
    pages = auth_system()

    # Set up navigation
    pg = st.navigation(pages)

    # Run the selected page
    pg.run()


if __name__ == "__main__":
    main()

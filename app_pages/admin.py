"""Admin & Telemetry Dashboard for SpreadThrift owner.

Displays active user metrics, feature adoption, and real-time error alerts with resolve actions.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime

from src.config import settings
from src.telemetry import (
    get_telemetry_summary,
    resolve_error,
    resolve_all_errors,
    delete_error,
    log_error,
    clear_all_telemetry,
)


def render_admin_page():
    st.header("🛡️ SpreadThrift Admin & Telemetry Hub")
    st.caption("Live monitoring of user adoption, feature usage, and real-time runtime error alerts.")

    # -------------------------------------------------------------------------
    # Access Control Guard
    # -------------------------------------------------------------------------
    is_admin = st.session_state.get("is_admin", False)

    if not is_admin:
        st.error("⛔ Access Restricted: You must be an authorized SpreadThrift Administrator to view this portal.")
        
        with st.container(border=True):
            st.markdown("#### 🔑 Administrator Verification")
            st.caption("Enter your administrator master passcode to unlock this dashboard:")
            admin_pin = st.text_input("Admin Passcode", type="password", key="admin_pin_input")
            if st.button("Unlock Admin Dashboard", type="primary", width="stretch"):
                if admin_pin == settings.admin_password:
                    st.session_state.is_admin = True
                    st.success("✔ Administrator verified successfully!")
                    st.rerun()
                else:
                    st.error("Invalid passcode. Access denied.")
        return

    # -------------------------------------------------------------------------
    # Fetch Live Telemetry Data
    # -------------------------------------------------------------------------
    summary = get_telemetry_summary()
    unresolved_cnt = summary["unresolved_errors"]

    # -------------------------------------------------------------------------
    # KPI Metric Cards
    # -------------------------------------------------------------------------
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Total Users", f"{summary['total_users']:,}", border=True)
    with k2:
        st.metric("Active Today", f"{summary['active_today']:,}", border=True)
    with k3:
        st.metric("Total App Events", f"{summary['total_events']:,}", border=True)
    with k4:
        st.metric(
            "Unresolved Errors",
            unresolved_cnt,
            delta="- Attention" if unresolved_cnt > 0 else "All Good",
            delta_color="inverse" if unresolved_cnt > 0 else "normal",
            border=True,
        )

    # Urgent Error Alert Banner
    if unresolved_cnt > 0:
        st.warning(
            f"🚨 **Action Required**: {unresolved_cnt} unresolved error(s) logged by users! Review the **Live Errors & Alerts** tab below.",
            icon=":material/warning:",
        )

    # -------------------------------------------------------------------------
    # Main Admin Tabs
    # -------------------------------------------------------------------------
    tab_errors, tab_analytics, tab_feed, tab_tools = st.tabs([
        f"🚨 Live Errors & Alerts ({unresolved_cnt})",
        "📈 User Adoption & Metrics",
        "📋 Real-Time Activity Feed",
        "⚙️ Admin Operations",
    ])

    # -------------------------------------------------------------------------
    # TAB 1: ERRORS & LIVE ALERTS
    # -------------------------------------------------------------------------
    with tab_errors:
        st.markdown("### 🚨 System Exceptions & Runtime Errors")
        st.caption("All application exceptions logged across user sessions are captured here with full stack traces.")

        all_errors = summary["recent_errors"]
        show_all = st.checkbox("Show previously resolved errors", value=False)
        displayed_errors = all_errors if show_all else [e for e in all_errors if e["status"] == "unresolved"]

        # Action Buttons Row
        e_c1, e_c2 = st.columns([3, 1], vertical_alignment="bottom")
        with e_c1:
            st.markdown(f"**Showing {len(displayed_errors)} error(s)**")
        with e_c2:
            if unresolved_cnt > 0 and st.button("✔ Resolve All Errors", type="secondary", width="stretch"):
                resolve_all_errors()
                st.success("All errors marked as resolved!")
                st.rerun()

        if displayed_errors:
            for err in displayed_errors:
                err_id = err["id"]
                err_ts = err["timestamp"]
                err_user = err["user_email"] or "anonymous"
                err_page = err["page"]
                err_type = err["error_type"]
                err_msg = err["error_message"]
                err_trace = err["stack_trace"]
                err_status = err["status"]

                status_badge = "🔴 UNRESOLVED" if err_status == "unresolved" else "🟢 RESOLVED"

                with st.container(border=True):
                    row_c1, row_c2, row_c3 = st.columns([1.2, 3, 1], vertical_alignment="center")
                    with row_c1:
                        st.markdown(f"**{status_badge}**")
                        st.caption(f"`{err_ts}`")
                    with row_c2:
                        st.markdown(f"**{err_type}** on page `{err_page}`")
                        st.caption(f"User: `{err_user}` — Message: {err_msg[:120]}")
                    with row_c3:
                        if err_status == "unresolved":
                            if st.button("Mark Resolved", key=f"res_{err_id}", width="stretch", icon=":material/check:"):
                                resolve_error(err_id)
                                st.rerun()
                        else:
                            if st.button("Delete", key=f"del_{err_id}", width="stretch", icon=":material/delete:"):
                                delete_error(err_id)
                                st.rerun()

                    with st.expander("🔍 View Error Details & Full Traceback"):
                        st.code(err_trace or err_msg or "No traceback captured.", language="python")
        else:
            st.success("🎉 Zero unresolved errors! All systems running smoothly.")

    # -------------------------------------------------------------------------
    # TAB 2: USER ADOPTION & METRICS
    # -------------------------------------------------------------------------
    with tab_analytics:
        st.markdown("### 📈 Product Utilization & Analytics")
        st.caption("How users are interacting with SpreadThrift features over time.")

        c_a1, c_a2 = st.columns([1.5, 1])

        # Timeline Chart
        with c_a1:
            st.markdown("#### Daily User Interactions (Last 30 Days)")
            timeline = summary["daily_timeline"]
            if timeline:
                df_time = pd.DataFrame(timeline)
                fig_time = px.area(
                    df_time,
                    x="date",
                    y="events",
                    title="User Events Over Time",
                    markers=True,
                    template="plotly_dark",
                    labels={"date": "Date", "events": "Events Logged"},
                )
                fig_time.update_layout(height=320, margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_time, width="stretch")
            else:
                st.info("No daily timeline events recorded yet.")

        # Breakdown Donut Chart
        with c_a2:
            st.markdown("#### Feature Usage Breakdown")
            breakdown = summary["event_breakdown"]
            if breakdown:
                df_pie = pd.DataFrame([{"Feature": k, "Count": v} for k, v in breakdown.items()])
                fig_pie = px.pie(
                    df_pie,
                    names="Feature",
                    values="Count",
                    hole=0.45,
                    title="Interactions by Feature",
                    template="plotly_dark",
                )
                fig_pie.update_layout(height=320, margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_pie, width="stretch")
            else:
                st.info("No feature interactions recorded yet.")

        # User Leaderboard
        st.divider()
        st.markdown("#### 👥 Registered Users Leaderboard")
        users_list = summary["users_list"]
        if users_list:
            df_users = pd.DataFrame(users_list)
            df_users.columns = ["User Email", "Last Active", "Total Events"]
            st.dataframe(df_users, width="stretch")
        else:
            st.info("No registered users recorded yet.")

    # -------------------------------------------------------------------------
    # TAB 3: REAL-TIME ACTIVITY FEED
    # -------------------------------------------------------------------------
    with tab_feed:
        st.markdown("### 📋 Real-Time Activity Audit Log")
        st.caption("Live chronologically ordered user actions across all sessions.")

        recent_events = summary["recent_events"]
        if recent_events:
            df_events = pd.DataFrame(recent_events)
            df_events = df_events[["timestamp", "user_email", "event_type", "event_details", "spreadsheet_id"]]
            df_events.columns = ["Timestamp", "User Email", "Action", "Details", "Target Spreadsheet"]
            st.dataframe(df_events, width="stretch", height=450)
        else:
            st.info("No activity recorded yet.")

    # -------------------------------------------------------------------------
    # TAB 4: ADMIN OPERATIONS
    # -------------------------------------------------------------------------
    with tab_tools:
        st.markdown("### ⚙️ Admin Diagnostics & Maintenance")

        col_t1, col_t2 = st.columns(2)
        with col_t1:
            with st.container(border=True):
                st.markdown("#### 🧪 Test Error Notification")
                st.caption("Generate a simulated error to verify that live alerts and badges work properly.")
                test_page = st.selectbox("Simulate Error on Page:", ["sync.py", "metrics.py", "auth.py", "html_parser.py"])
                test_msg = st.text_input("Error Message", value="Simulated test exception for admin alert check")
                if st.button("💥 Trigger Test Error", type="secondary", width="stretch"):
                    log_error(st.session_state.user_info.get("email"), test_page, RuntimeError(test_msg))
                    st.success("Test error logged! Check the Live Errors & Alerts tab.")
                    st.rerun()

        with col_t2:
            with st.container(border=True):
                st.markdown("#### 🗑️ Telemetry Housekeeping")
                st.caption("Reset the local telemetry database if performing testing or staging maintenance.")
                if st.button("⚠️ Reset All Telemetry Data", type="primary", width="stretch"):
                    clear_all_telemetry()
                    st.success("Telemetry tables reset successfully.")
                    st.rerun()


if __name__ == "__main__":
    render_admin_page()
else:
    render_admin_page()

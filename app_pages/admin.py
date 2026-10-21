"""Admin & Telemetry Dashboard for Thread Count owner.

Displays active user metrics, feature adoption, user feedback, and real-time error alerts with Google Sheets persistence.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime

from src.config import settings
from src.telemetry import (
    get_telemetry_summary,
    get_feedback_summary,
    get_feedback_list,
    update_feedback_status,
    delete_feedback,
    sync_feedback_to_google_sheet,
    resolve_error,
    resolve_all_errors,
    delete_error,
    log_error,
    clear_all_telemetry,
)


def render_admin_page():
    st.header("🛡️ Thread Count Admin & Telemetry Hub")
    st.caption("Live monitoring of user adoption, feedback submissions, and real-time runtime error alerts.")

    # -------------------------------------------------------------------------
    # Access Control Guard
    # -------------------------------------------------------------------------
    is_admin = st.session_state.get("is_admin", False)

    if not is_admin:
        st.error("⛔ Access Restricted: You must be an authorized Thread Count Administrator to view this portal.")
        
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
    # Fetch Live Telemetry & Feedback Data
    # -------------------------------------------------------------------------
    summary = get_telemetry_summary()
    unresolved_cnt = summary["unresolved_errors"]
    new_feedback_cnt = summary.get("new_feedback", 0)
    total_feedback_cnt = summary.get("total_feedback", 0)

    # -------------------------------------------------------------------------
    # KPI Metric Cards
    # -------------------------------------------------------------------------
    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.metric("Total Users", f"{summary['total_users']:,}", border=True)
    with k2:
        st.metric("Active Today", f"{summary['active_today']:,}", border=True)
    with k3:
        st.metric("Total App Events", f"{summary['total_events']:,}", border=True)
    with k4:
        st.metric(
            "User Feedback",
            f"{total_feedback_cnt}",
            delta=f"{new_feedback_cnt} new" if new_feedback_cnt > 0 else "Reviewed",
            delta_color="normal" if new_feedback_cnt > 0 else "off",
            border=True,
        )
    with k5:
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
    tab_feedback_title = f"💬 User Feedback ({new_feedback_cnt} new)" if new_feedback_cnt > 0 else "💬 User Feedback"
    tab_errors_title = f"🚨 Live Errors & Alerts ({unresolved_cnt})" if unresolved_cnt > 0 else "🚨 Live Errors & Alerts"

    tab_feedback, tab_errors, tab_analytics, tab_feed, tab_tools = st.tabs([
        tab_feedback_title,
        tab_errors_title,
        "📈 User Adoption & Metrics",
        "📋 Real-Time Activity Feed",
        "⚙️ Admin Operations & Sheets",
    ])

    # -------------------------------------------------------------------------
    # TAB 1: USER FEEDBACK & REQUESTS (Backed by Google Sheets)
    # -------------------------------------------------------------------------
    with tab_feedback:
        st.markdown("### 💬 User Feedback & Feature Requests")
        st.caption("Direct suggestions, bug reports, and ratings submitted by users from the sidebar. Backed by Google Sheets.")

        fb_summary = get_feedback_summary()
        f_total = fb_summary["total_feedback"]
        f_new = fb_summary["new_feedback"]
        f_avg = fb_summary["avg_rating"]
        f_unsynced = fb_summary["unsynced_count"]

        # Mini KPI row for feedback
        fk1, fk2, fk3, fk4 = st.columns(4)
        with fk1:
            st.metric("Total Submissions", f_total, border=True)
        with fk2:
            st.metric(
                "New / Unreviewed",
                f_new,
                delta="- Action Needed" if f_new > 0 else "All Caught Up",
                delta_color="inverse" if f_new > 0 else "normal",
                border=True,
            )
        with fk3:
            st.metric(
                "Average Rating",
                f"{f_avg} / 5.0" if f_total > 0 else "N/A",
                delta="⭐" * int(round(f_avg)) if f_total > 0 else None,
                border=True,
            )
        with fk4:
            st.metric(
                "Pending Sheet Sync",
                f_unsynced,
                delta="- Needs Sync" if f_unsynced > 0 else "🟢 Up to Date",
                delta_color="inverse" if f_unsynced > 0 else "normal",
                border=True,
            )

        st.divider()

        # Action & Filter Controls
        f_ctrl1, f_ctrl2, f_ctrl3, f_ctrl4 = st.columns([1.5, 1.5, 1.2, 1.2], vertical_alignment="bottom")
        with f_ctrl1:
            cat_filter = st.selectbox("Filter Category:", ["All", "Feature Request", "Bug Report", "General Feedback", "Other"], key="admin_fb_cat_filt")
        with f_ctrl2:
            status_filter = st.selectbox("Filter Status:", ["All", "new", "reviewed", "in_progress", "resolved"], format_func=lambda s: {
                "All": "All Statuses",
                "new": "🆕 New / Unreviewed",
                "reviewed": "👀 Reviewed",
                "in_progress": "🛠️ In Progress",
                "resolved": "✅ Resolved",
            }.get(s, s), key="admin_fb_stat_filt")
        with f_ctrl3:
            if st.button("🔄 Sync to Google Sheet", icon=":material/sync:", type="secondary", width="stretch", key="admin_fb_sync_btn"):
                res = sync_feedback_to_google_sheet()
                if res["success"]:
                    st.toast(res["message"], icon=":material/check_circle:")
                    st.success(res["message"])
                else:
                    st.warning(res["message"])
                st.rerun()
        with f_ctrl4:
            sheet_id = settings.admin_sheet_id or settings.google_sheet_id
            if sheet_id:
                sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
                st.link_button("🔗 Open Google Sheet", sheet_url, width="stretch")

        feedback_items = get_feedback_list(
            status_filter=status_filter if status_filter != "All" else None,
            category_filter=cat_filter if cat_filter != "All" else None,
        )

        if feedback_items:
            st.markdown(f"**Showing {len(feedback_items)} feedback item(s)**")
            for item in feedback_items:
                fb_id = item["id"]
                fb_ts = item["timestamp"]
                fb_user = item["user_email"]
                fb_cat = item["category"]
                fb_rat = item["rating"]
                fb_msg = item["message"]
                fb_stat = item.get("status", "new")
                fb_notes = item.get("admin_notes", "")
                fb_synced = bool(item.get("synced_to_sheets", 0))

                status_emoji = {
                    "new": "🆕 NEW",
                    "reviewed": "👀 REVIEWED",
                    "in_progress": "🛠️ IN PROGRESS",
                    "resolved": "✅ RESOLVED",
                }.get(fb_stat, fb_stat.upper())

                cat_badge = {
                    "Feature Request": "💡 Feature Request",
                    "Bug Report": "🐞 Bug Report",
                    "General Feedback": "⭐ General Feedback",
                    "Other": "💬 Other",
                }.get(fb_cat, fb_cat)

                stars_display = "⭐" * int(fb_rat)

                with st.container(border=True):
                    h_c1, h_c2, h_c3 = st.columns([2, 1.5, 1], vertical_alignment="center")
                    with h_c1:
                        st.markdown(f"**{cat_badge}** &nbsp;·&nbsp; {stars_display} `({fb_rat}/5)`")
                        st.caption(f"From `{fb_user}` &nbsp;·&nbsp; `{fb_ts}`")
                    with h_c2:
                        sync_badge = "🟢 Synced to Google Sheets" if fb_synced else "🟡 Stored Locally (Sync Pending)"
                        st.caption(f"Status: **{status_emoji}**\n\n{sync_badge}")
                    with h_c3:
                        # Quick status transitions
                        if fb_stat == "new":
                            if st.button("Mark Reviewed", key=f"rev_{fb_id}", icon=":material/check:", width="stretch"):
                                update_feedback_status(fb_id, "reviewed")
                                st.rerun()
                        elif fb_stat == "reviewed":
                            if st.button("Mark In Progress", key=f"prog_{fb_id}", icon=":material/construction:", width="stretch"):
                                update_feedback_status(fb_id, "in_progress")
                                st.rerun()
                        elif fb_stat == "in_progress":
                            if st.button("Mark Resolved", key=f"resv_{fb_id}", icon=":material/done_all:", width="stretch"):
                                update_feedback_status(fb_id, "resolved")
                                st.rerun()

                    # Feedback message display
                    st.info(fb_msg, icon=":material/chat:")

                    # Expandable Admin Notes & Edit Controls
                    with st.expander("📝 Admin Notes & Manage"):
                        col_note, col_actions = st.columns([3, 1], vertical_alignment="bottom")
                        with col_note:
                            new_note = st.text_input("Internal Note", value=fb_notes, key=f"note_input_{fb_id}", placeholder="Add developer note, milestone, or task ID...")
                        with col_actions:
                            if st.button("Save Note", key=f"save_note_{fb_id}", width="stretch"):
                                update_feedback_status(fb_id, fb_stat, admin_notes=new_note)
                                st.success("Note saved!")
                                st.rerun()

                        sub_c1, sub_c2 = st.columns([2, 1], vertical_alignment="bottom")
                        with sub_c1:
                            new_stat = st.selectbox(
                                "Change Status",
                                ["new", "reviewed", "in_progress", "resolved"],
                                index=["new", "reviewed", "in_progress", "resolved"].index(fb_stat) if fb_stat in ["new", "reviewed", "in_progress", "resolved"] else 0,
                                key=f"stat_sel_{fb_id}",
                            )
                            if new_stat != fb_stat:
                                if st.button(f"Update to '{new_stat}'", key=f"upd_stat_btn_{fb_id}"):
                                    update_feedback_status(fb_id, new_stat, admin_notes=fb_notes)
                                    st.rerun()
                        with sub_c2:
                            if st.button("Delete Feedback", key=f"del_fb_{fb_id}", icon=":material/delete:", type="secondary", width="stretch"):
                                delete_feedback(fb_id)
                                st.rerun()
        else:
            st.info("No feedback submissions matching the selected filters.")

    # -------------------------------------------------------------------------
    # TAB 2: ERRORS & LIVE ALERTS
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
    # TAB 3: USER ADOPTION & METRICS
    # -------------------------------------------------------------------------
    with tab_analytics:
        st.markdown("### 📈 Product Utilization & Analytics")
        st.caption("How users are interacting with Thread Count features over time.")

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
    # TAB 4: REAL-TIME ACTIVITY FEED
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
    # TAB 5: ADMIN OPERATIONS & GOOGLE SHEETS
    # -------------------------------------------------------------------------
    with tab_tools:
        st.markdown("### ⚙️ Admin Diagnostics & Cloud Storage")

        col_t1, col_t2 = st.columns(2)
        with col_t1:
            with st.container(border=True):
                st.markdown("#### 📊 Google Sheets Admin Storage")
                st.caption("Thread Count stores all admin metrics, error logs, and user feedback in Google Sheets to eliminate external databases.")
                target_sheet = settings.admin_sheet_id or settings.google_sheet_id
                st.markdown(f"**Target Admin Spreadsheet ID:** `{target_sheet}`")
                st.markdown("**Worksheet Tab:** `User_Feedback`")

                if st.button("🧪 Verify Google Sheets Connectivity", type="secondary", width="stretch", icon=":material/health_and_safety:"):
                    try:
                        from src.sheets.client import GoogleSheetsClient
                        client = GoogleSheetsClient(sheet_id=target_sheet)
                        sh = client.get_spreadsheet()
                        ws_titles = [w.title for w in sh.worksheets()]
                        has_fb = "User_Feedback" in ws_titles
                        st.success(
                            f"✔ Connected to Google Sheet: **{sh.title}**\n\n"
                            f"Worksheets detected: {', '.join(ws_titles)}\n\n"
                            f"Feedback Sheet Present: {'🟢 Yes' if has_fb else '🟡 Will auto-create on first submit'}"
                        )
                    except Exception as e:
                        st.error(f"Google Sheets verification failed: {e}")

                if st.button("🔄 Sync All Feedback to Google Sheets", type="primary", width="stretch", icon=":material/sync:"):
                    res = sync_feedback_to_google_sheet()
                    if res["success"]:
                        st.success(res["message"])
                    else:
                        st.warning(res["message"])
                    st.rerun()

        with col_t2:
            with st.container(border=True):
                st.markdown("#### 🧪 Test Error Notification")
                st.caption("Generate a simulated error to verify that live alerts and badges work properly.")
                test_page = st.selectbox("Simulate Error on Page:", ["sync.py", "metrics.py", "auth.py", "html_parser.py"])
                test_msg = st.text_input("Error Message", value="Simulated test exception for admin alert check")
                if st.button("💥 Trigger Test Error", type="secondary", width="stretch"):
                    log_error(st.session_state.user_info.get("email"), test_page, RuntimeError(test_msg))
                    st.success("Test error logged! Check the Live Errors & Alerts tab.")
                    st.rerun()

            with st.container(border=True):
                st.markdown("#### 🗑️ Telemetry Housekeeping")
                st.caption("Reset the local telemetry and feedback database if performing testing or staging maintenance.")
                if st.button("⚠️ Reset All Telemetry Data", type="primary", width="stretch"):
                    clear_all_telemetry()
                    st.success("Telemetry tables reset successfully.")
                    st.rerun()


if __name__ == "__main__":
    render_admin_page()
else:
    render_admin_page()

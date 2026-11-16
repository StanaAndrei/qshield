"""Operational Monitor Page for Q-SHIELD Dashboard (TASK-6.1).

Displays live transfer queue, active transfer progress bars, QKD key buffer gauges,
recent completion status, and interactive audit trail inspection.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import streamlit as st

from app.core.enums import PriorityLevel, TransferStatus
from dashboard.components.metrics_cards import render_kpi_cards, render_secondary_kpis
from dashboard.data import (
    format_bytes,
    format_duration,
    format_timestamp,
    get_active_transfers,
    get_all_transfers,
    get_completed_transfers,
    get_kpi_summary,
    get_qkd_links_metrics,
    get_queue_transfers,
    get_request_policy_decisions,
    get_request_transitions,
)


def render_operations_page() -> None:
    """Renders the complete Operations Monitor page."""
    # -------------------------------------------------------------------------
    # Header & System Provenance Banner
    # -------------------------------------------------------------------------
    st.markdown("## 🛡️ Q-SHIELD Operational Monitor")
    st.caption(
        "Real-time Medical Data Exchange & Quantum Key Orchestration Control Center"
    )

    # Spec §16.3 Provenance notice
    st.info(
        "**Strict Provenance Notice (Spec §16.3)**: QKD key buffers, network transmission rates, "
        "and medical payload transfers are generated via discrete-event simulation models "
        "and synthetic data buffers. Simulated telemetry is explicitly distinguished from physical hardware measurements.",
        icon="ℹ️",
    )

    # -------------------------------------------------------------------------
    # Controls & Toolbar
    # -------------------------------------------------------------------------
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([2, 2, 2])
    with ctrl_col1:
        if st.button("🔄 Refresh Data", use_container_width=True):
            st.rerun()

    with ctrl_col2:
        auto_refresh = st.checkbox("Auto-refresh (10s)", value=False)
        if auto_refresh:
            time.sleep(10)
            st.rerun()

    with ctrl_col3:
        st.write(f"⏱️ **Last Updated**: `{datetime.now(UTC).strftime('%H:%M:%S UTC')}`")

    st.divider()

    # -------------------------------------------------------------------------
    # 1. Top-Level Operational KPIs
    # -------------------------------------------------------------------------
    kpis = get_kpi_summary()
    render_kpi_cards(kpis)

    with st.expander("📊 Secondary System Telemetry & Load Details", expanded=False):
        render_secondary_kpis(kpis)

    st.divider()

    # -------------------------------------------------------------------------
    # 2. Active In-Flight Transfers
    # -------------------------------------------------------------------------
    st.subheader("⚡ Active In-Flight Transfers")
    active_transfers = get_active_transfers()

    if not active_transfers:
        st.success("🟢 No active transfers currently in flight. System is idle and ready for incoming workloads.")
    else:
        for req in active_transfers:
            with st.container(border=True):
                prio_badge = f":red[**{req.priority.name}**]" if req.priority == PriorityLevel.CRITICAL else f"**{req.priority.name}**"
                st.markdown(
                    f"**Request `{str(req.request_id)[:8]}...`** | "
                    f"**Route**: `{req.source_id}` ➔ `{req.destination_id}` | "
                    f"Priority: {prio_badge} | "
                    f"Category: `{req.data_category.value}`"
                )

                # Progress determination
                if req.status == TransferStatus.TRANSFERRING:
                    progress_val = 0.65
                    status_text = "Transferring payload over quantum-secured link..."
                elif req.status == TransferStatus.VERIFYING:
                    progress_val = 0.90
                    status_text = "Verifying cryptographic SHA-256 receiver checksum..."
                else:
                    progress_val = 0.30
                    status_text = f"Status: {req.status.value}"

                st.progress(progress_val, text=f"{status_text} ({int(progress_val * 100)}%)")

                meta_c1, meta_c2, meta_c3, meta_c4 = st.columns(4)
                meta_c1.caption(f"**Size**: {format_bytes(req.data_size_bytes)}")
                meta_c2.caption(f"**Security Profile**: `{req.security_profile.value}`")
                meta_c3.caption(f"**Started At**: {format_timestamp(req.transfer_started_at)}")
                meta_c4.caption(f"**Status**: `{req.status.value}`")

    st.divider()

    # -------------------------------------------------------------------------
    # 3. QKD Key Buffer & Link Health Gauges
    # -------------------------------------------------------------------------
    st.subheader("🔑 Quantum Key Distribution (QKD) Link Buffer Gauges")
    st.caption("Discrete leaky-bucket key pools and generation dynamics `[PROVENANCE: SIMULATED]`")

    qkd_links = get_qkd_links_metrics()
    if qkd_links:
        # Display links in 2 or 3 responsive columns
        cols = st.columns(min(len(qkd_links), 3))
        for idx, link in enumerate(qkd_links):
            col = cols[idx % len(cols)]
            with col:
                with st.container(border=True):
                    status_label = link["status"]
                    if status_label == "CONNECTED":
                        status_badge = "🟢 CONNECTED"
                    elif status_label == "DEGRADED":
                        status_badge = "🟡 DEGRADED"
                    else:
                        status_badge = "🔴 UNAVAILABLE"

                    st.markdown(f"**Link**: `{link['link_key']}`")
                    st.markdown(f"**Status**: {status_badge}")

                    pct = link["buffer_percent"]
                    st.progress(
                        pct / 100.0,
                        text=f"Buffer: {link['available_keys']:,} / {link['max_capacity']:,} keys ({pct:.1f}%)",
                    )

                    c_a, c_b = st.columns(2)
                    c_a.caption(f"**Rate**: {link['key_generation_rate_bps'] / 1000:.1f} kbps")
                    c_b.caption(f"**Latency**: {link['key_service_latency_ms']:.1f} ms")
                    st.caption("🔒 *Profile: QKD_AES_256_GCM (256-bit)*")

    st.divider()

    # -------------------------------------------------------------------------
    # 4. Live Pending Transfer Queue
    # -------------------------------------------------------------------------
    st.subheader("📋 Live Pending Transfer Queue")
    queue_transfers = get_queue_transfers()

    if not queue_transfers:
        st.info("ℹ️ Queue is currently clear. No pending transfer requests awaiting scheduling.")
    else:
        table_rows: list[dict[str, Any]] = []
        now_dt = datetime.now(UTC)
        for req in queue_transfers:
            # Deadline slack
            slack_str = "No deadline"
            if req.deadline is not None:
                deadline_dt = req.deadline if req.deadline.tzinfo else req.deadline.replace(tzinfo=UTC)
                diff = (deadline_dt - now_dt).total_seconds()
                slack_str = f"{diff:.1f} s" if diff > 0 else "EXPIRED"

            wait_str = "N/A"
            if req.submitted_at is not None:
                sub_dt = req.submitted_at if req.submitted_at.tzinfo else req.submitted_at.replace(tzinfo=UTC)
                wait_str = f"{(now_dt - sub_dt).total_seconds():.1f} s"

            table_rows.append({
                "Request ID": f"{str(req.request_id)[:8]}...",
                "Source": req.source_id,
                "Destination": req.destination_id,
                "Priority": req.priority.name,
                "Category": req.data_category.value,
                "Size": format_bytes(req.data_size_bytes),
                "Profile": req.security_profile.value,
                "Wait Time": wait_str,
                "Deadline Slack": slack_str,
                "Status": req.status.value,
            })

        df_queue = pd.DataFrame(table_rows)
        st.dataframe(df_queue, use_container_width=True, hide_index=True)

    st.divider()

    # -------------------------------------------------------------------------
    # 5. Recent Completed & Terminal Transfers
    # -------------------------------------------------------------------------
    st.subheader("📜 Recent Transfer History & Completion Status")
    completed_transfers = get_completed_transfers(limit=50)

    if not completed_transfers:
        st.info("No completed or terminated transfers recorded yet.")
    else:
        history_rows: list[dict[str, Any]] = []
        for req in completed_transfers:
            # Calculate duration
            dur_str = "N/A"
            if req.transfer_started_at and req.transfer_completed_at:
                dur_secs = (req.transfer_completed_at - req.transfer_started_at).total_seconds()
                dur_str = format_duration(dur_secs)

            status_style = req.status.value
            if req.status == TransferStatus.COMPLETED:
                status_style = "✅ COMPLETED"
            elif req.status == TransferStatus.REJECTED:
                status_style = "🚫 REJECTED"
            elif req.status == TransferStatus.FAILED:
                status_style = "❌ FAILED"
            elif req.status == TransferStatus.EXPIRED:
                status_style = "⏳ EXPIRED"

            history_rows.append({
                "Request ID": str(req.request_id),
                "Source": req.source_id,
                "Destination": req.destination_id,
                "Category": req.data_category.value,
                "Size": format_bytes(req.data_size_bytes),
                "Priority": req.priority.name,
                "Duration": dur_str,
                "Status": status_style,
                "Failure/Reason": req.failure_reason or "N/A",
                "Submitted At": format_timestamp(req.submitted_at),
            })

        df_history = pd.DataFrame(history_rows)
        st.dataframe(df_history, use_container_width=True, hide_index=True)

    st.divider()

    # -------------------------------------------------------------------------
    # 6. Interactive Audit Trail & Request Inspector
    # -------------------------------------------------------------------------
    st.subheader("🔍 Request Lifecycle & Audit Trail Inspector")
    all_requests = get_all_transfers(limit=100)

    if all_requests:
        request_options = {
            f"{str(r.request_id)[:8]}... ({r.source_id} ➔ {r.destination_id} | {r.status.value} | {format_bytes(r.data_size_bytes)})": r.request_id
            for r in all_requests
        }
        selected_label = st.selectbox("Select Request for Detailed Audit Inspection:", list(request_options.keys()))
        if selected_label:
            selected_uuid = request_options[selected_label]
            transitions = get_request_transitions(selected_uuid)
            decisions = get_request_policy_decisions(selected_uuid)

            insp_c1, insp_c2 = st.columns(2)
            with insp_c1:
                st.markdown(f"**Full UUID**: `{selected_uuid}`")
            with insp_c2:
                st.markdown(f"**Total State Transitions**: `{len(transitions)}`")

            if transitions:
                st.markdown("##### ⏱️ State Transition History (Deterministic FSM)")
                trans_data = []
                for idx, t in enumerate(transitions, start=1):
                    trans_data.append({
                        "Step": idx,
                        "From Status": t.from_status,
                        "To Status": t.to_status,
                        "Actor": t.actor,
                        "Reason": t.reason or "Standard lifecycle advance",
                        "Timestamp": format_timestamp(t.timestamp),
                    })
                st.dataframe(pd.DataFrame(trans_data), use_container_width=True, hide_index=True)
            else:
                st.caption("No transition records logged for this request.")

            if decisions:
                st.markdown("##### 🛡️ Policy Evaluation Decisions")
                dec_data = []
                for d in decisions:
                    dec_data.append({
                        "Allowed": "✅ Allowed" if d.allowed else "❌ Rejected",
                        "Reason Code": d.reason_code,
                        "Explanation": d.explanation,
                        "Evaluated At": format_timestamp(d.evaluated_at),
                    })
                st.dataframe(pd.DataFrame(dec_data), use_container_width=True, hide_index=True)


# Execute page rendering when run directly as script or via Streamlit
if __name__ == "__main__":
    render_operations_page()

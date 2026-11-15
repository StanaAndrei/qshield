"""KPI Metric Card UI Components for Q-SHIELD Dashboard (TASK-6.1)."""

from __future__ import annotations

from typing import Any

import streamlit as st


def render_kpi_cards(kpi_data: dict[str, Any]) -> None:
    """Renders top-level operational Key Performance Indicator (KPI) cards.

    Args:
        kpi_data: Dictionary containing consolidated system metrics.
    """
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        active_count = kpi_data.get("active_transfers_count", 0)
        active_delta = "Busy" if active_count > 0 else "Idle"
        st.metric(
            label="⚡ Active In-Flight",
            value=f"{active_count}",
            delta=active_delta,
            delta_color="normal" if active_count > 0 else "off",
            help="Transfers currently in active network transmission or cryptographic verification.",
        )

    with col2:
        queue_len = kpi_data.get("queue_length", 0)
        queue_delta = f"{queue_len} pending" if queue_len > 0 else "Clear"
        st.metric(
            label="📋 Live Queue Depth",
            value=f"{queue_len}",
            delta=queue_delta,
            delta_color="inverse" if queue_len > 5 else "normal",
            help="Total requests awaiting policy evaluation or scheduler dispatch.",
        )

    with col3:
        completed_count = kpi_data.get("completed_count", 0)
        st.metric(
            label="✅ Completed",
            value=f"{completed_count}",
            delta="100% integrity" if completed_count > 0 else None,
            help="Transfers completed with verified cryptographic SHA-256 integrity.",
        )

    with col4:
        failed_count = kpi_data.get("failed_count", 0)
        st.metric(
            label="⚠️ Rejected / Failed",
            value=f"{failed_count}",
            delta="Policy / Network" if failed_count > 0 else "Zero errors",
            delta_color="inverse" if failed_count > 0 else "off",
            help="Transfers rejected by deterministic security policy or failed during transport.",
        )

    with col5:
        total_keys = kpi_data.get("total_keys_available", 0)
        st.metric(
            label="🔑 QKD Key Pool [SIM]",
            value=f"{total_keys:,}",
            delta="256-bit keys",
            delta_color="normal",
            help="Aggregated available 256-bit quantum key material across active link buffers. Provenance: SIMULATED.",
        )


def render_secondary_kpis(kpi_data: dict[str, Any]) -> None:
    """Renders secondary operational KPIs including load factor and throughput."""
    c1, c2, c3 = st.columns(3)

    with c1:
        load_pct = kpi_data.get("system_load_factor", 0.0) * 100.0
        st.metric(
            label="🖥️ System Load Factor",
            value=f"{load_pct:.1f}%",
            delta="Normal" if load_pct < 70.0 else "High Load",
            delta_color="inverse" if load_pct >= 70.0 else "normal",
            help="Composite ratio of active transfers and queue wait depth against system baseline capacity.",
        )

    with c2:
        avg_throughput = kpi_data.get("avg_throughput_mbps", 0.0)
        st.metric(
            label="🌐 Effective Throughput",
            value=f"{avg_throughput:.2f} Mbps",
            help="Rolling mean transmission rate across completed verified transfers.",
        )

    with c3:
        total_reqs = kpi_data.get("total_requests", 0)
        st.metric(
            label="📊 Total Ingested Requests",
            value=f"{total_reqs}",
            help="Lifetime total requests submitted to this Q-SHIELD instance.",
        )


__all__ = ["render_kpi_cards", "render_secondary_kpis"]

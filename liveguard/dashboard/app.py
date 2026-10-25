r"""Streamlit dashboard for LiveGuard compliant livestream preparation.

Displays system health, compliance status, module state, OBS metrics,
audit-backed manual stream key configuration, and live logs.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import streamlit as st

from liveguard.audit.chain import get_audit_chain
from liveguard.config.loader import load_config
from liveguard.config.schema import LiveGuardConfig
from liveguard.dashboard.helpers import (
    calculate_tos_age_days,
    clear_stream_key_from_vault,
    format_uptime,
    get_module_statuses,
    has_saved_stream_key,
    save_stream_key_to_vault,
    tail_log_file,
)
from liveguard.orchestrator.state import RuntimeState
from liveguard.secrets.vault import get_vault

# ---------------------------------------------------------------------------
# Page Configuration & Styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="LiveGuard Operations",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .metric-card {
        background-color: #1e222b;
        border-radius: 8px;
        padding: 16px;
        border: 1px solid #333a46;
    }
    .badge-ok { color: #00d26a; font-weight: bold; }
    .badge-warn { color: #ffab00; font-weight: bold; }
    .badge-error { color: #f8312f; font-weight: bold; }
    .refuse-banner {
        background-color: #b71c1c;
        color: white;
        padding: 16px;
        border-radius: 8px;
        font-weight: bold;
        text-align: center;
        font-size: 1.2rem;
        margin-bottom: 20px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def load_application_state(
    config_path_str: str = "liveguard.yaml",
) -> tuple[RuntimeState, LiveGuardConfig | None]:
    """Load latest runtime snapshot and configuration.

    Args:
        config_path_str: Path to liveguard.yaml.

    Returns:
        Tuple of (RuntimeState, LiveGuardConfig or None).
    """
    state = RuntimeState.load_snapshot()
    cfg: LiveGuardConfig | None = None
    cfg_file = Path(config_path_str)
    if cfg_file.is_file():
        try:
            cfg = load_config(cfg_file)
        except Exception:
            pass
    return state, cfg


def render_dashboard() -> None:
    """Render the full Streamlit operations dashboard."""
    # Sidebar: configuration & controls
    st.sidebar.title("🛡️ LiveGuard Control")
    config_path = st.sidebar.text_input("Config Path", value="liveguard.yaml")
    auto_refresh = st.sidebar.checkbox("Auto-refresh (2s)", value=True)

    state, cfg = load_application_state(config_path)
    vault = get_vault()
    audit = get_audit_chain()

    # 1. Top bar: Uptime, Config, Compliance
    uptime_sec = (time.time() - state.started_at) if state.started_at else 0.0
    uptime_str = format_uptime(uptime_sec)

    banner_enabled = True
    tos_ack_days: float | None = None
    if cfg is not None:
        banner_enabled = bool(cfg.compliance.disclosure_banner.enabled)
        tos_ack_days = calculate_tos_age_days(cfg.compliance.tos_ack_at)

    top_col1, top_col2, top_col3, top_col4 = st.columns(4)
    with top_col1:
        st.metric("Pipeline Uptime", uptime_str, delta=state.active_phase)
    with top_col2:
        st.metric("Config", config_path, delta="Loaded" if cfg else "Not Found")
    with top_col3:
        banner_label = "ENABLED" if banner_enabled else "DISABLED"
        st.metric("Disclosure Banner", banner_label)
    with top_col4:
        if tos_ack_days is not None:
            delta_str = f"{tos_ack_days:.1f}d old"
            st.metric("ToS Ack Age", delta_str)
        else:
            st.metric("ToS Ack Age", "Not Acknowledged")

    # Compliance warning for ToS age > 25 days
    if tos_ack_days is not None and tos_ack_days > 25.0:
        if tos_ack_days > 30.0:
            st.error(
                f"🚨 CRITICAL: TikTok ToS ack is {tos_ack_days:.1f} days old "
                "(exceeds 30-day limit)! Streaming is blocked."
            )
        else:
            st.warning(
                f"⚠️ WARNING: TikTok ToS ack is {tos_ack_days:.1f} days old. "
                f"Re-acknowledgement required within {30.0 - tos_ack_days:.1f} days."
            )

    # 2. Critical Refusal Banner if Disclosure Banner is off
    if not banner_enabled:
        st.markdown(
            '<div class="refuse-banner">'
            "🛑 REFUSE TO STREAM: AI Disclosure Banner is DISABLED. "
            "Compliance gate blocks streaming."
            "</div>",
            unsafe_allow_html=True,
        )

    st.divider()

    # 3. Module Status Grid (M1 - M4)
    st.subheader("Module Status Grid")
    module_data = get_module_statuses(state, cfg)
    m_cols = st.columns(4)

    for idx, (mod_id, info) in enumerate(module_data.items()):
        with m_cols[idx]:
            st.markdown(f"**{info['name']}** (`{mod_id.upper()}`)")
            status = info["status"]
            if status in ("running", "active"):
                st.markdown("🟢 Status: `RUNNING`")
            elif status in ("error", "failed"):
                st.markdown("🔴 Status: `ERROR`")
            elif status == "disabled":
                st.markdown("⚪ Status: `DISABLED`")
            else:
                st.markdown(f"🔵 Status: `{status.upper()}`")

    st.divider()

    # 4. OBS Orchestrator & Stream Metrics
    obs_col1, obs_col2 = st.columns([1, 2])
    with obs_col1:
        st.subheader("OBS Live Status")
        st.write(f"**Active Scene:** `{state.scene_name}`")
        st.write(
            f"**Stream Status:** {'🔴 LIVE' if state.stream_active else '⚪ Idle'}"
        )
        st.metric("Output Bitrate", f"{state.bitrate_kbps:.1f} kbps")
        st.metric("Dropped Frames", f"{state.dropped_frames}")
        st.metric("FPS", f"{state.fps:.1f}")

    with obs_col2:
        st.subheader("Bitrate Sparkline (kbps)")
        history = (
            state.bitrate_history
            if state.bitrate_history
            else [state.bitrate_kbps or 0.0]
        )
        st.line_chart(history, height=180)

    st.divider()

    # 5. Manual Stream Key Entry Form (Vault Backed)
    st.subheader("TikTok RTMP Stream Key (Local Age Vault)")
    st.caption(
        "Enter your TikTok RTMP stream key manually. LiveGuard NEVER scrapes "
        "credentials. The key is encrypted in `secrets/vault.age` and never exposed."
    )

    is_configured = has_saved_stream_key(vault)
    if is_configured:
        st.success("🔒 Stream Key: Configured and secured in local vault.")
    else:
        st.info("⚠️ Stream Key: Not configured.")

    with st.form("stream_key_form", clear_on_submit=True):
        new_key = st.text_input(
            "Enter New Stream Key",
            type="password",
            help="Your TikTok Live Producer stream key (encrypted immediately).",
        )
        submitted = st.form_submit_button("Save Key to Vault")
        if submitted:
            result = save_stream_key_to_vault(new_key, vault, audit)
            if result["success"]:
                st.success(result["message"])
                st.rerun()
            else:
                st.error(result["message"])

    # Clear key option behind confirm checkbox
    with st.expander("Clear Stored Stream Key"):
        confirm_clear = st.checkbox(
            "I confirm I want to erase the stored stream key from the vault."
        )
        if st.button("Erase Stream Key", disabled=not confirm_clear):
            clear_res = clear_stream_key_from_vault(vault, audit)
            st.success(clear_res["message"])
            st.rerun()

    st.divider()

    # 6. Live Logs Tail
    st.subheader("Recent System Logs")
    log_lines = tail_log_file(n=20)
    if log_lines:
        st.code("\n".join(log_lines), language="text")
    else:
        st.info("No log entries in `logs/liveguard.log` yet.")

    # 7. Auto-refresh loop
    if auto_refresh:
        time.sleep(2)
        st.rerun()


if __name__ == "__main__" or "streamlit" in os.path.basename(__file__):
    render_dashboard()

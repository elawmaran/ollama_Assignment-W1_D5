"""ELAW AI look: colour palettes, theme state and the theme switch."""

from pathlib import Path

import streamlit as st

# ---------------- THEME ----------------
PALETTES = {
    "light": {
        "bg": "#f7f6f3", "glow-1": "rgba(124,58,237,0.10)", "glow-2": "rgba(6,182,212,0.08)",
        "panel": "rgba(255,255,255,0.72)", "panel-solid": "#ffffff", "panel-hover": "#ffffff",
        "field": "#ffffff", "sidebar": "rgba(242,240,236,0.82)",
        "border": "rgba(28,25,40,0.08)", "border-strong": "rgba(28,25,40,0.14)",
        "text": "#1d1d1f", "text-2": "#3a3a3c", "muted": "#6e6e73", "faint": "#a1a1a6",
        "accent": "#7c3aed", "accent-soft": "rgba(124,58,237,0.10)",
        "grad-text": "linear-gradient(95deg,#7c3aed 10%,#4f46e5 50%,#0891b2 90%)",
        "cta": "#1d1d1f", "cta-text": "#ffffff",
        "bubble-bg": "linear-gradient(135deg,#f1ecff,#e8f6fb)",
        "bubble-border": "rgba(124,58,237,0.16)", "bubble-text": "#1d1d1f",
        "code-bg": "rgba(124,58,237,0.08)", "code-text": "#6d28d9", "pre-bg": "#fbfaf8",
        "track": "rgba(0,0,0,0.10)",
        "shadow-sm": "0 1px 2px rgba(16,12,32,0.05)",
        "shadow": "0 1px 2px rgba(16,12,32,0.05), 0 8px 24px rgba(16,12,32,0.07)",
        "shadow-lg": "0 2px 6px rgba(16,12,32,0.05), 0 20px 50px rgba(16,12,32,0.10)",
        "notice-bg": "rgba(16,185,129,0.07)", "notice-border": "rgba(16,185,129,0.24)",
        "notice-strong": "#047857", "green": "#10b981", "red": "#ef4444",
        "bottom-fade": "rgba(247,246,243,0.96)", "input-bg": "rgba(255,255,255,0.88)",
        "badge-green": "#047857", "badge-amber": "#b45309", "badge-red": "#b91c1c", "badge-blue": "#0e7490",
    },
    "dark": {
        "bg": "#0b0b10", "glow-1": "rgba(139,92,246,0.17)", "glow-2": "rgba(34,211,238,0.09)",
        "panel": "rgba(255,255,255,0.045)", "panel-solid": "#17171f", "panel-hover": "rgba(255,255,255,0.08)",
        "field": "rgba(255,255,255,0.05)", "sidebar": "rgba(14,14,20,0.82)",
        "border": "rgba(255,255,255,0.08)", "border-strong": "rgba(255,255,255,0.15)",
        "text": "#f5f5f7", "text-2": "#e4e4e7", "muted": "#a1a1aa", "faint": "#71717a",
        "accent": "#a78bfa", "accent-soft": "rgba(167,139,250,0.15)",
        "grad-text": "linear-gradient(95deg,#c4b5fd 5%,#a78bfa 40%,#22d3ee 95%)",
        "cta": "#2a2a35", "cta-text": "#f5f5f7",
        "bubble-bg": "linear-gradient(135deg,rgba(139,92,246,0.26),rgba(34,211,238,0.13))",
        "bubble-border": "rgba(167,139,250,0.32)", "bubble-text": "#f5f5f7",
        "code-bg": "rgba(167,139,250,0.14)", "code-text": "#c4b5fd", "pre-bg": "#111118",
        "track": "rgba(255,255,255,0.14)",
        "shadow-sm": "0 1px 2px rgba(0,0,0,0.35)",
        "shadow": "0 1px 2px rgba(0,0,0,0.35), 0 8px 24px rgba(0,0,0,0.35)",
        "shadow-lg": "0 2px 6px rgba(0,0,0,0.3), 0 20px 50px rgba(0,0,0,0.5)",
        "notice-bg": "rgba(52,211,153,0.07)", "notice-border": "rgba(52,211,153,0.22)",
        "notice-strong": "#6ee7b7", "green": "#34d399", "red": "#f87171",
        "bottom-fade": "rgba(11,11,16,0.96)", "input-bg": "rgba(24,24,32,0.88)",
        "badge-green": "#6ee7b7", "badge-amber": "#fcd34d", "badge-red": "#fca5a5", "badge-blue": "#67e8f9",
    },
}
THEME_LABELS = {
    "auto": "Auto",
    "light": "Light",
    "dark": "Dark",
}


def palette_vars(name):
    return ":root{" + "".join(f"--{k}:{v};" for k, v in PALETTES[name].items()) + "}"


def set_theme(name):
    st.session_state.theme = name
    st.session_state.theme_pick = name  # keep the sidebar picker in sync


def on_theme_pick():
    picked = st.session_state.get("theme_pick")
    if picked is None:  # clicking the selected option again deselects it: undo that
        st.session_state.theme_pick = st.session_state.get("theme", "auto")
    else:
        st.session_state.theme = picked



def apply_theme():
    """Inject the CSS and the one-click theme switch (call once per run)."""
    st.session_state.setdefault("theme", "auto")
    st.session_state.setdefault("theme_pick", st.session_state.theme)
    theme = st.session_state.theme

    if theme == "auto":  # follow the visitor's device setting
        theme_css = (
            palette_vars("light")
            + "@media (prefers-color-scheme: dark){" + palette_vars("dark") + "}"
            + "@media (prefers-color-scheme: dark){.st-key-to_dark{display:none}}"
            + "@media (prefers-color-scheme: light){.st-key-to_light{display:none}}"
        )
    else:
        hidden = "to_dark" if theme == "dark" else "to_light"
        theme_css = palette_vars(theme) + f".st-key-{hidden}{{display:none}}"

    try:
        native = st.context.theme.type  # Streamlit's own theme, from the visitor's OS
    except Exception:
        native = None
    if theme != "auto" and native and native != theme:
        # Tables are drawn on a canvas that CSS colours can't reach: flip them to match.
        theme_css += ('[data-testid="stDataFrame"] canvas{filter:invert(1) hue-rotate(180deg)}'
                      '[data-testid="stDataFrame"]{border-color:var(--border-strong)!important}')

    STYLE = (Path(__file__).parent / "style.css").read_text()
    st.markdown(f"<style>{STYLE}{theme_css}</style>", unsafe_allow_html=True)

    # One-click switch, top right. Only the button for the *other* theme is visible.
    with st.container(key="theme_bar", horizontal=True, gap=None):
        st.button(":material/dark_mode:", key="to_dark", on_click=set_theme, args=("dark",))
        st.button(":material/light_mode:", key="to_light", on_click=set_theme, args=("light",))

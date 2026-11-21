# Run locally:
#   cd "/Users/elaw/Documents/GitHub/ollama_Assignment W1_D5/ollama_python"
#   source .venv/bin/activate
#   streamlit run app.py
#
# Environment variables (optional, useful on a server):
#   OLLAMA_HOST     where Ollama listens (default http://127.0.0.1:11434)
#   DEFAULT_MODEL   model selected on start (default qwen3:8b)
#   MAX_CONCURRENT  how many answers are generated at once (default 1)
#   ALLOWED_MODELS  comma-separated models visitors may pick (default: all installed)
#   THINKING_MODEL  optional separate model for "Deep thinking". If unset and the
#                   model has no native thinking, the same model is prompted to
#                   reason step by step instead (no extra RAM).
#   KEEP_ALIVE      how long Ollama keeps the model in memory (default -1 = always)
#   MAX_ANSWER_TOKENS  cap on answer length, keeps CPU replies quick (default 700)

import os
import threading
import time
from datetime import datetime
from pathlib import Path

import html
import re

import ollama
import streamlit as st

# ---------------- CONFIG ----------------
APP_NAME = "ELAW AI"
TAGLINE = "Free, open-source AI on my own server"
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen3:8b")
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "1"))  # free CPU server: one at a time
ALLOWED_MODELS = [m.strip() for m in os.getenv("ALLOWED_MODELS", "").split(",") if m.strip()]
THINKING_MODEL = os.getenv("THINKING_MODEL", "").strip()
QUEUE_TIMEOUT_S = 300       # give up waiting in line after 5 minutes
# Keep the model in memory permanently: on a small CPU server a cold load takes
# over a minute, while a loaded model starts answering in a fraction of a second.
_keep = os.getenv("KEEP_ALIVE", "-1").strip()
KEEP_ALIVE = int(_keep) if _keep.lstrip("-").isdigit() else _keep
MAX_ANSWER_TOKENS = int(os.getenv("MAX_ANSWER_TOKENS", "700"))
MAX_INPUT_CHARS = 4000      # protect a public server from huge prompts
MAX_HISTORY_MESSAGES = 20   # how many past messages are sent to the model

# Shown in the sidebar footer. Fill these in before you share the link.
AUTHOR_NAME = ""
AUTHOR_LINK = ""            # e.g. your LinkedIn or GitHub URL

STYLE_HINT = (" Keep answers focused: lead with the answer, use short paragraphs or "
              "bullets, and stay under about 250 words unless the user asks for more.")
# Appended to the user's message. Plain <reasoning> tags, because instruct-only
# Qwen3 models treat <think> as a reserved token and garble it.
THINK_PROMPT = ("\n\n(First reason step by step inside <reasoning> and </reasoning> tags. "
                "Then, after </reasoning>, write only the final answer.)")

PERSONAS = {
    "Helpful assistant": "You are a friendly, knowledgeable assistant. "
    "Answer clearly and use markdown formatting when it helps.",
    "Code mentor": "You are a patient senior software engineer. Explain code "
    "step by step, show short runnable examples, and point out pitfalls.",
    "Writing coach": "You are an expert editor. Help the user write clear, "
    "professional and engaging text. Offer improved versions.",
    "Straight to the point": "You are a concise assistant. Answer in as few "
    "words as possible while staying accurate.",
}

SUGGESTION_ICONS = [":material/school:", ":material/edit_note:",
                    ":material/code:", ":material/lightbulb:"]
SUGGESTIONS = [
    ("Explain a concept", "Break down how large language models work",
     "Explain how large language models work, in simple words with an analogy."),
    ("Write with me", "Draft a LinkedIn post about a project",
     "Help me write a short, engaging LinkedIn post about a personal AI project I built."),
    ("Code with me", "Learn a Python concept by example",
     "Teach me Python decorators with a simple, practical example."),
    ("Brainstorm", "Five creative AI project ideas",
     "Give me five creative AI project ideas I could build in a weekend."),
]

st.set_page_config(
    page_title=f"{APP_NAME} · Free Open-Source AI",
    page_icon="✦",
    layout="centered",
    initial_sidebar_state="auto",
)

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

STYLE = (Path(__file__).parent / "style.css").read_text()
st.markdown(f"<style>{STYLE}{theme_css}</style>", unsafe_allow_html=True)

# One-click switch, top right. Only the button for the *other* theme is visible.
with st.container(key="theme_bar", horizontal=True, gap=None):
    st.button(":material/dark_mode:", key="to_dark", on_click=set_theme, args=("dark",))
    st.button(":material/light_mode:", key="to_light", on_click=set_theme, args=("light",))


# ---------------- HELPERS ----------------
@st.cache_data(ttl=30, show_spinner=False)
def get_models():
    """Return (is_online, list_of_installed_model_names)."""
    try:
        response = ollama.list()
        names = sorted(m.model for m in response.models)
        if ALLOWED_MODELS:
            names = [n for n in names if n in ALLOWED_MODELS]
        return True, names
    except Exception:
        return False, []


@st.cache_data(ttl=300, show_spinner=False)
def supports_thinking(model_name):
    try:
        return "thinking" in (ollama.show(model_name).capabilities or [])
    except Exception:
        return False


@st.cache_data(ttl=15, show_spinner=False)
def is_loaded(model_name):
    try:
        return any(m.model == model_name for m in ollama.ps().models)
    except Exception:
        return True  # can't tell; don't alarm visitors


@st.cache_resource
def _warmers():
    return {}


def warm_up(model_name):
    """Load the model into memory in the background (at most once a minute)."""
    warmers = _warmers()
    if time.time() - warmers.get(model_name, 0) < 60:
        return
    warmers[model_name] = time.time()

    def _load():
        try:
            ollama.generate(model=model_name, prompt="", keep_alive=KEEP_ALIVE)
        except Exception:
            pass

    threading.Thread(target=_load, daemon=True).start()


@st.cache_resource
def generation_slots():
    """Shared by every visitor: limits how many answers run at the same time."""
    return threading.Semaphore(MAX_CONCURRENT)


def safe_md(text, role):
    """Stop prices like $1.10 from being rendered as LaTeX math."""
    if role == "user":
        return text.replace("$", "\\$")
    return re.sub(r"(?<![\\$])\$(?=\d)", r"\\$", text)


def thinking_html(text):
    """Plain-text reasoning as one HTML block (no markdown or math parsing)."""
    body = html.escape(text).replace("$", "&#36;").replace("\n", "<br>")
    return f'<div class="thinking-text">{body}</div>'


def split_think(raw, expects_reasoning=False):
    """Separate <think>...</think> or <reasoning>...</reasoning> from the answer."""
    stripped = raw.lstrip()
    for open_tag, close_tag in (("<think>", "</think>"), ("<reasoning>", "</reasoning>")):
        if close_tag in raw:
            before, after = raw.split(close_tag, 1)
            return before.replace(open_tag, "").strip(), after.lstrip()
        if stripped.startswith(open_tag):
            return stripped[len(open_tag):], ""     # still reasoning
        if stripped and open_tag.startswith(stripped):
            return "", ""                            # tag still arriving
    if expects_reasoning:                            # thinking model, no closing tag yet
        return stripped, ""
    return "", raw


def build_transcript(messages):
    lines = [f"# {APP_NAME} chat — {datetime.now():%Y-%m-%d %H:%M}\n"]
    for m in messages:
        who = "You" if m["role"] == "user" else APP_NAME
        lines.append(f"**{who}:**\n\n{m['content']}\n")
    return "\n".join(lines)


def meta_html(meta):
    if not meta:
        return ""
    parts = [f"<span>{meta['model']}</span>", f"<span>{meta['seconds']:.1f}s total</span>"]
    if meta.get("tps"):
        parts.append(f"<span>{meta['tps']:.0f} tok/s</span>")
    return f'<div class="meta">{"".join(parts)}</div>'


# ---------------- SESSION STATE ----------------
st.session_state.setdefault("messages", [])
warm_up(DEFAULT_MODEL)  # make sure the main model is in memory before anyone asks

online, models = get_models()
if not models:
    models = [DEFAULT_MODEL]
default_index = models.index(DEFAULT_MODEL) if DEFAULT_MODEL in models else 0


# ---------------- SIDEBAR ----------------
with st.sidebar:
    st.markdown(f"""
        <div class="brand">
            <div class="brand-logo">✦</div>
            <div class="brand-name">ELAW <em>AI</em></div>
        </div>
        <div class="brand-sub">{TAGLINE}</div>
    """, unsafe_allow_html=True)

    if st.button("New chat", key="new_chat", icon=":material/add:", width="stretch"):
        st.session_state.messages = []
        st.rerun()

    st.markdown('<div class="section-label">Appearance</div>', unsafe_allow_html=True)
    st.segmented_control(
        "Theme", list(THEME_LABELS), key="theme_pick", on_change=on_theme_pick,
        format_func=THEME_LABELS.get, label_visibility="collapsed", width="stretch",
    )

    st.markdown('<div class="section-label">Engine</div>', unsafe_allow_html=True)
    status_class, status_text = ("online", "Ollama connected") if online else ("offline", "Ollama offline")
    st.markdown(
        f'<div class="status {status_class}"><span class="dot"></span>{status_text}</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    model = st.selectbox("Model", models, index=default_index)
    persona = st.selectbox("Personality", list(PERSONAS.keys()))
    temperature = st.slider(
        "Creativity", 0.0, 1.5, 0.7, 0.1,
        help="Lower = focused and predictable. Higher = more creative.",
    )
    can_think = supports_thinking(model)
    # Older Ollama versions don't expose "thinking", but Qwen3 still obeys its
    # own /think and /no_think switches, so we drive it through the prompt.
    soft_think = (not can_think and not THINKING_MODEL
                  and model.lower().startswith("qwen3") and "instruct" not in model.lower())
    # Otherwise ask the same model to reason inside <think> tags: no extra model,
    # so nothing else gets pushed out of memory.
    prompt_think = not (can_think or soft_think or THINKING_MODEL)
    deep_thinking = st.toggle(
        "Deep thinking",
        value=False,
        help="The model reasons step by step before answering. Better on tricky "
        "questions, but slower.",
    )

    st.markdown('<div class="section-label">This session</div>', unsafe_allow_html=True)
    msgs = st.session_state.messages
    user_turns = sum(1 for m in msgs if m["role"] == "user")
    speeds = [m["meta"]["tps"] for m in msgs if m.get("meta", {}).get("tps")]
    avg_speed = f"{sum(speeds) / len(speeds):.0f}" if speeds else "—"
    st.markdown(f"""
        <div class="stat-grid">
            <div class="stat"><div class="v">{user_turns}</div><div class="k">Prompts</div></div>
            <div class="stat"><div class="v">{avg_speed}</div><div class="k">Avg tok/s</div></div>
        </div>
    """, unsafe_allow_html=True)
    st.write("")

    if msgs:
        st.download_button(
            "Export chat",
            data=build_transcript(msgs),
            file_name=f"{APP_NAME.lower()}-chat-{datetime.now():%Y%m%d-%H%M}.md",
            mime="text/markdown",
            icon=":material/download:",
            width="stretch",
        )

    credit = ""
    if AUTHOR_NAME:
        name = f'<a href="{AUTHOR_LINK}" target="_blank">{AUTHOR_NAME}</a>' if AUTHOR_LINK else AUTHOR_NAME
        credit = f"Built by {name}<br>"
    st.markdown(
        f'<div class="side-footer">{credit}Open-source model · Ollama · Streamlit<br>'
        'No paid APIs. Conversations are not stored.</div>',
        unsafe_allow_html=True,
    )


# ---------------- EMPTY STATE ----------------
if not st.session_state.messages:
    st.markdown(f"""
        <div class="hero">
            <div class="orb">✦</div>
            <h1>ELAW AI.<br><span class="serif">Free and open.</span></h1>
            <p class="lead">Open-source AI on my own server. No subscriptions, no paid APIs.</p>
            <div class="chips">
                <span class="chip"><i>●</i> 100% free</span>
                <span class="chip"><i>◆</i> Open-source model</span>
                <span class="chip"><i>■</i> Chats aren't stored</span>
                <span class="chip"><i>✦</i> {model}</span>
            </div>
            <div class="notice">
                <span>🐢</span>
                <span><b>Slow by design, free forever.</b> This runs on a free CPU server,
                not paid GPUs, so answers take a little longer and visitors are served
                one at a time. Thanks for your patience!</span>
            </div>
        </div>
    """, unsafe_allow_html=True)

    grid = st.container(key="suggest_grid", horizontal=True, gap="small")
    for i, (title, subtitle, prompt_text) in enumerate(SUGGESTIONS):
        with grid:
            if st.button(f"{title}\n\n{subtitle}", key=f"suggest_{i}", width="stretch",
                         icon=SUGGESTION_ICONS[i]):
                st.session_state.pending_prompt = prompt_text
                st.rerun()


# ---------------- HISTORY ----------------
AVATARS = {"user": ":material/person:", "assistant": ":material/auto_awesome:"}

for message in st.session_state.messages:
    with st.chat_message(message["role"], avatar=AVATARS[message["role"]]):
        if message.get("thinking"):
            with st.expander("💭 Show reasoning"):
                st.markdown(thinking_html(message["thinking"]), unsafe_allow_html=True)
        st.markdown(safe_md(message["content"], message["role"]))
        if message["role"] == "assistant":
            st.markdown(meta_html(message.get("meta")), unsafe_allow_html=True)


# ---------------- INPUT ----------------
prompt = st.chat_input(f"Message {APP_NAME}…", max_chars=MAX_INPUT_CHARS)
if "pending_prompt" in st.session_state:
    prompt = st.session_state.pop("pending_prompt")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=AVATARS["user"]):
        st.markdown(safe_md(prompt, "user"))

    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state.messages[-MAX_HISTORY_MESSAGES:]
        ]
        run_model = THINKING_MODEL if (deep_thinking and THINKING_MODEL) else model
        # Thinking-only models put their reasoning first, closing it with </think>
        expects_reasoning = run_model == THINKING_MODEL and bool(THINKING_MODEL)
        if soft_think:  # Qwen3 reads this switch from the latest user turn
            switch = " /think" if deep_thinking else " /no_think"
            history[-1] = {**history[-1], "content": history[-1]["content"] + switch}
        if deep_thinking and prompt_think:
            history[-1] = {**history[-1], "content": history[-1]["content"] + THINK_PROMPT}
        payload = [{"role": "system", "content": PERSONAS[persona] + STYLE_HINT}] + history
        chat_kwargs = {}
        if can_think:
            chat_kwargs["think"] = deep_thinking  # explicit off keeps qwen3 fast

        answered = False
        slots = generation_slots()
        acquired = slots.acquire(blocking=False)
        if not acquired:
            with st.spinner("Someone else is chatting with the free server. You're next in line…"):
                acquired = slots.acquire(timeout=QUEUE_TIMEOUT_S)

        if not acquired:
            st.session_state.messages.pop()
            st.warning("The free server is busy right now. Please try again in a minute.")
        else:
            try:
                started = time.perf_counter()
                native_thinking, raw, stats = "", "", {}
                thinking = answer = ""
                think_box = think_slot = None
                think_box_open = True
                answer_slot = st.empty()
                if is_loaded(run_model):
                    answer_slot.markdown("⏳ _Thinking…_")
                else:
                    answer_slot.markdown("⏳ _Waking up the AI. The first answer after a "
                                         "quiet spell takes about a minute…_")

                stream = ollama.chat(
                    model=run_model,
                    messages=payload,
                    stream=True,
                    options={
                        "temperature": temperature,
                        "num_predict": MAX_ANSWER_TOKENS * (2 if deep_thinking else 1),
                    },
                    # a separate thinking model frees its memory sooner
                    keep_alive="5m" if expects_reasoning else KEEP_ALIVE,
                    **chat_kwargs,
                )
                for chunk in stream:
                    msg = chunk["message"]
                    native_thinking += msg.get("thinking") or ""
                    raw += msg.get("content") or ""
                    tag_thinking, answer = split_think(raw, expects_reasoning)
                    thinking = (native_thinking + tag_thinking).strip()

                    if thinking and think_box is None:
                        answer_slot.empty()
                        think_box = st.status("Thinking…", expanded=True)
                        think_slot = think_box.empty()
                        answer_slot = st.empty()
                    if think_box is not None and not answer:
                        think_slot.markdown(thinking_html(thinking[-1500:]), unsafe_allow_html=True)
                    if answer:
                        if think_box is not None and think_box_open:
                            think_box.update(label="Reasoning done", state="complete", expanded=False)
                            think_box_open = False
                        answer_slot.markdown(safe_md(answer, "assistant") + " ▍")
                    if chunk.get("done"):
                        stats["eval_count"] = chunk.get("eval_count") or 0
                        stats["eval_duration"] = chunk.get("eval_duration") or 0

                answer_slot.markdown(safe_md(answer, "assistant"))
                seconds = time.perf_counter() - started
                tps = None
                if stats.get("eval_duration"):
                    tps = stats["eval_count"] / (stats["eval_duration"] / 1e9)

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer.strip() or "_(no answer)_",
                    "thinking": thinking.strip(),
                    "meta": {"model": run_model, "seconds": seconds, "tps": tps},
                })
                answered = True
            except Exception as error:
                st.session_state.messages.pop()  # drop the unanswered prompt
                st.error(f"Couldn't reach the model: {error}")
                st.caption(
                    f"Make sure Ollama is running and `{model}` appears in `ollama list`."
                )
            finally:
                slots.release()

        if answered:
            st.rerun()

if st.session_state.messages:
    st.markdown(
        '<div class="footnote">Free open-source AI · it can make mistakes, so double-check important info.</div>',
        unsafe_allow_html=True,
    )

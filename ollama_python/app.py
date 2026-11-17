# cd "/Users/elaw/Documents/GitHub/ollama_Assignment W1_D5/ollama_python"
# source .venv/bin/activate
# streamlit run rag_app.py




import streamlit as st
import ollama

MODEL_NAME = "qwen2.5:3b"

st.set_page_config(
    page_title="Local AI",
    page_icon="✳",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ---------------- CSS STYLING ----------------
st.markdown("""
<style>
/* Main page */
.stApp {
    background: #faf9f6;
    color: #292722;
}

header[data-testid="stHeader"] {
    background: transparent;
}

.block-container {
    max-width: 850px;
    padding-top: 2rem;
    padding-bottom: 7rem;
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background: #f0eee8;
    border-right: 1px solid #e5e1d9;
}

section[data-testid="stSidebar"] .block-container {
    padding-top: 1.5rem;
}

.brand {
    font-family: Georgia, serif;
    font-size: 1.8rem;
    font-weight: 600;
    letter-spacing: -1px;
    color: #292722;
    margin-bottom: 0.2rem;
}

.brand-subtitle {
    color: #827e74;
    font-size: 0.85rem;
    margin-bottom: 1.5rem;
}

.model-pill {
    display: inline-block;
    background: #eeece6;
    color: #696459;
    border: 1px solid #e2ded5;
    border-radius: 20px;
    padding: 5px 11px;
    font-size: 0.78rem;
    margin: 5px 0 18px 0;
}

/* Main heading */
.hero {
    text-align: center;
    padding: 4.5rem 0 1.5rem 0;
}

.hero-symbol {
    font-size: 2rem;
    color: #bd7654;
    margin-bottom: 0.5rem;
}

.hero h1 {
    font-family: Georgia, serif;
    font-weight: 500;
    font-size: clamp(2rem, 5vw, 2.8rem);
    letter-spacing: -1.5px;
    color: #302d27;
    margin-bottom: 0.5rem;
}

.hero p {
    color: #827d72;
    font-size: 1rem;
}

/* Chat messages */
[data-testid="stChatMessage"] {
    background: transparent;
    border: none;
    padding: 1rem 0.2rem;
}

[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
    color: #302e29;
    line-height: 1.8;
    font-size: 1rem;
}

[data-testid="stChatMessage"] img {
    border-radius: 50%;
}

/* Input box */
[data-testid="stChatInput"] {
    border: 1px solid #ded9cf;
    border-radius: 18px;
    background: #ffffff;
    box-shadow: 0 5px 22px rgba(55, 45, 30, 0.06);
}

[data-testid="stChatInput"] textarea {
    color: #302d27;
}

[data-testid="stChatInput"] textarea::placeholder {
    color: #969084;
}

[data-testid="stChatInput"] button {
    background: #bd7654;
    color: white;
    border-radius: 12px;
}

/* Buttons */
.stButton > button {
    border-radius: 10px;
    border: 1px solid #ded9cf;
    transition: all 0.2s ease;
}

.stButton > button:hover {
    border-color: #bd7654;
    color: #9f5e40;
}

/* Suggestion cards */
div[data-testid="stHorizontalBlock"] .stButton > button {
    min-height: 72px;
    text-align: left;
    white-space: normal;
    background: rgba(255,255,255,0.65);
}

/* Status text */
.status-note {
    font-size: 0.78rem;
    color: #918b7e;
    text-align: center;
    padding-top: 0.7rem;
}

footer {
    visibility: hidden;
}

#MainMenu {
    visibility: hidden;
}
</style>
""", unsafe_allow_html=True)


# ---------------- SESSION STATE ----------------
if "messages" not in st.session_state:
    st.session_state.messages = []

if "chat_started" not in st.session_state:
    st.session_state.chat_started = False


# ---------------- SIDEBAR ----------------
with st.sidebar:
    st.markdown('<div class="brand">✳ Local AI</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="brand-subtitle">Your personal AI workspace</div>',
        unsafe_allow_html=True
    )

    if st.button("＋  New conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.chat_started = False
        st.rerun()

    st.divider()

    st.markdown("**Your model**")
    st.markdown(
        f'<div class="model-pill">● {MODEL_NAME}</div>',
        unsafe_allow_html=True
    )

    st.caption("Runs locally through Ollama.")
    st.divider()

    st.markdown("**About this app**")
    st.caption(
        "A private, local chat interface powered by "
        "your own AI model. Your prompts are sent to "
        "your local Ollama service."
    )

    st.divider()
    st.caption("LOCAL AI · PERSONAL WORKSPACE")


# ---------------- MAIN CHAT ----------------
if not st.session_state.messages:
    st.markdown("""
    <div class="hero">
        <div class="hero-symbol">✳</div>
        <h1>What can I help you with?</h1>
        <p>Your ideas, questions and work — start anywhere.</p>
    </div>
    """, unsafe_allow_html=True)

    st.write("")

    # Suggested prompts
    col1, col2 = st.columns(2)

    with col1:
        if st.button(
            "✦  Learn something new\n\nExplain a complex topic simply",
            use_container_width=True,
        ):
            st.session_state.pending_prompt = (
                "Explain artificial intelligence in simple words."
            )
            st.rerun()

        if st.button(
            "⌘  Write and create\n\nHelp me write something",
            use_container_width=True,
        ):
            st.session_state.pending_prompt = (
                "Help me write a clear and professional message."
            )
            st.rerun()

    with col2:
        if st.button(
            "⌘  Code with me\n\nUnderstand or debug Python",
            use_container_width=True,
        ):
            st.session_state.pending_prompt = (
                "Teach me a useful Python programming concept "
                "with a simple example."
            )
            st.rerun()

        if st.button(
            "✧  Brainstorm ideas\n\nExplore creative possibilities",
            use_container_width=True,
        ):
            st.session_state.pending_prompt = (
                "Give me five creative AI project ideas."
            )
            st.rerun()


# Render previous messages
for message in st.session_state.messages:
    avatar = "✳" if message["role"] == "assistant" else "👤"

    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])


# Accept either typed messages or suggestion buttons
prompt = st.chat_input("Message your local AI...")

if "pending_prompt" in st.session_state:
    prompt = st.session_state.pop("pending_prompt")


if prompt:
    st.session_state.chat_started = True

    st.session_state.messages.append({
        "role": "user",
        "content": prompt,
    })

    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar="✳"):
        try:
            def generate_response():
                stream = ollama.chat(
                    model=MODEL_NAME,
                    messages=st.session_state.messages,
                    stream=True,
                )

                for chunk in stream:
                    content = chunk["message"]["content"]
                    if content:
                        yield content

            answer = st.write_stream(generate_response())

            st.session_state.messages.append({
                "role": "assistant",
                "content": answer,
            })

        except Exception as error:
            st.error(f"Could not connect to Ollama: {error}")
            st.info(
                "Check that Ollama is running and that "
                "qwen2.5:3b appears in `ollama list`."
            )

    st.rerun()


st.markdown(
    '<div class="status-note">'
    'Local model · Responses may take time depending on the prompt'
    '</div>',
    unsafe_allow_html=True,
)

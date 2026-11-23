"""Prompt Builder page (/prompt-builder). Open to guests within limits; logged-in
users get more. All AI work happens server-side in elaw_core.prompt_builder."""

import html
import json

import streamlit as st
import streamlit.components.v1 as components

import streamlit_auth
from elaw_core import config
from elaw_core import prompt_builder as pb
from elaw_core.db import session_scope

user = streamlit_auth.current_user()  # None = guest (allowed here, with a lower limit)
IP = streamlit_auth.client_ip()
S = config.settings

EXAMPLES = [
    ("Game character", "I need futuristic indian warrior game character"),
    ("Two images", "make two images of a warrior, one standing one sitting, cinematic"),
    ("Video", "make video warrior walking in destroyed city"),
    ("Test cases", "I have free spin feature in slot game create test cases"),
    ("Email", "write professional email asking client requirements"),
]
COMING_SOON = [("Game QA Creator", "bug_report"), ("Creative AI", "palette")]

st.session_state.setdefault("pb", {})
state = st.session_state.pb


def esc(value):
    return html.escape(str(value))


def quota():
    try:
        with session_scope() as db:
            return pb.quota(db, user, IP)
    except Exception:
        return None


def set_request(text):
    st.session_state.pb_request = text  # callback: runs before the text box is drawn


def start_over():
    state.clear()
    st.session_state.pb_request = ""


def copy_button(label, text, key):
    """Real clipboard button (st.code also has a copy icon; this one is labelled)."""
    payload = json.dumps(text).replace("</", "<\\/")
    components.html(f"""
<style>html,body{{margin:0;padding:0;background:transparent}}</style>
<button id="b" style="width:100%;height:38px;border-radius:10px;border:1px solid rgba(124,58,237,.35);
 background:#7c3aed;color:#fff;font:600 14px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;cursor:pointer">
 {esc(label)}</button>
<script>
const t = {payload}, b = document.getElementById("b"), l = b.textContent;
function done(ok){{ b.textContent = ok ? "✓ Copied" : "Press ⌘C / Ctrl+C"; setTimeout(()=>b.textContent=l, 1800); }}
function fallback(){{ const a=document.createElement("textarea"); a.value=t; document.body.appendChild(a);
  a.select(); let ok=false; try{{ ok=document.execCommand("copy"); }}catch(e){{}} a.remove(); done(ok); }}
b.onclick = () => navigator.clipboard ? navigator.clipboard.writeText(t).then(()=>done(true), fallback) : fallback();
</script>""", height=40)


def generate(**kwargs):
    """Run one build with live progress. Stores the result or an error in session state."""
    box = st.status("Understanding your request…", expanded=False)
    note = box.empty()

    def on_queue():
        box.update(label="Waiting for the free AI server… (someone else is using it)")

    def on_progress(chars):
        box.update(label=f"Writing your prompt… ({chars:,} characters so far)")

    try:
        with session_scope() as db:
            result = pb.run(db, user=user, ip=IP, on_progress=on_progress, on_queue=on_queue,
                            **kwargs)
        state.update(result=result, error=None)
        box.update(label="Done", state="complete")
    except pb.PromptBuilderError as error:
        state.update(error=(error.code, error.message))
        box.update(label="Couldn't build the prompt", state="error")
    note.empty()
    st.rerun()


# ---------------- SIDEBAR ----------------
with st.sidebar:
    st.markdown('<div class="brand"><div class="brand-logo">✦</div>'
                '<div class="brand-name">ELAW <em>AI</em></div></div>'
                '<div class="brand-sub">Free, open-source AI on my own server</div>',
                unsafe_allow_html=True)
    if user:
        st.page_link("views/chat.py", label="Chat", icon=":material/chat:")
    st.page_link("views/prompt_builder.py", label="Prompt Builder", icon=":material/auto_fix_high:")
    st.markdown('<div class="section-label">Coming soon</div>', unsafe_allow_html=True)
    for label, icon in COMING_SOON:
        st.markdown(f'<div class="soon"><span class="soon-ico">{icon}</span>{esc(label)}'
                    '<span class="soon-tag">Soon</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-label">Your builds</div>', unsafe_allow_html=True)
    q = quota()
    if q:
        st.progress(q["used"] / q["limit"] if q["limit"] else 1.0,
                    text=f"{q['left']} of {q['limit']} left today")
    if user:
        streamlit_auth.user_card(user)
    else:
        st.caption(f"Guests get {S.guest_daily_limit} builds a day. Free accounts get "
                   f"{S.free_daily_limit}.")
        st.markdown(f'<a class="side-link" href="{esc(streamlit_auth.auth_url("/register"))}" '
                    'target="_self">Create free account</a> · '
                    f'<a class="side-link" href="{esc(streamlit_auth.auth_url("/login"))}" '
                    'target="_self">Log in</a>', unsafe_allow_html=True)


# ---------------- HERO + INPUT ----------------
st.markdown("""
    <div class="hero pb-hero">
        <div class="pb-pill">✨ Prompt Builder</div>
        <h1>Tell us what you want.<br><span class="serif">We'll make it AI-ready.</span></h1>
        <p class="lead">Type it any way you like (short, messy or in simple English). ELAW AI works
        out what you mean and writes a precise prompt for image, video, coding, writing and more.</p>
    </div>
""", unsafe_allow_html=True)

with st.container(key="pb_form"):
    request = st.text_area(
        "What do you want to create?", key="pb_request", height=130,
        max_chars=S.pb_max_input_chars,
        placeholder="e.g. I need two images futuristic indian warrior one standing one sitting "
                    "cinematic for game")
    st.caption("Not sure how to start? Try an example:")
    chips = st.container(key="pb_examples", horizontal=True, gap="small")
    with chips:
        for i, (label, text) in enumerate(EXAMPLES):
            st.button(label, key=f"pb_ex_{i}", on_click=set_request, args=(text,))
    if st.button("✨ Build My Prompt", type="primary", width="stretch", key="pb_build"):
        state.clear()
        state["request"] = request
        generate(request=request)

# ---------------- ERRORS ----------------
if state.get("error"):
    code, message = state["error"]
    (st.warning if code in ("limit_reached", "rate_limited", "busy") else st.error)(message)
    if code == "limit_reached" and not user:
        st.markdown(f'<div class="gate-actions">'
                    f'<a class="gate-btn primary" href="{esc(streamlit_auth.auth_url("/register"))}" target="_self">Create free account</a>'
                    f'<a class="gate-btn" href="{esc(streamlit_auth.auth_url("/login"))}" target="_self">Log in</a></div>',
                    unsafe_allow_html=True)

result = state.get("result")

# ---------------- CLARIFICATION ----------------
if result and result["needs_clarification"]:
    with st.container(border=True, key="pb_questions"):
        st.markdown('<div class="pb-step">A couple of quick questions</div>'
                    '<div class="pb-sub">Answer what you can, so the prompt matches what you have '
                    'in mind. Nothing is made up for you.</div>', unsafe_allow_html=True)
        answers = []
        for i, question in enumerate(result["questions"]):
            answers.append((question, st.text_input(question, key=f"pb_ans_{i}", max_chars=300)))
        c1, c2 = st.columns(2)
        if c1.button("Build with my answers", type="primary", width="stretch"):
            if any(a.strip() for _, a in answers):
                generate(request=state["request"], answers=answers, round_no=2)
            else:
                st.warning("Answer at least one question, or choose “Skip, build anyway”.")
        if c2.button("Skip, build anyway", width="stretch"):
            generate(request=state["request"], force=True)

# ---------------- RESULT ----------------
elif result:
    label, icon = pb.TASK_TYPES[result["task_type"]]
    score = result["readiness_score"]
    level = ("Ready to use", "ok") if score >= 80 else ("Good, could use more detail", "mid") \
        if score >= 60 else ("Works, but add details for better results", "low")
    found = "".join(f'<span class="chip ok">✓ {esc(i["label"])}: {esc(i["value"])}</span>'
                    for i in result["understood"])
    missing = "".join(f'<span class="chip miss">+ {esc(m)}</span>'
                      for m in result["missing_information"])
    st.markdown(f"""
        <div class="pb-flow">
            <div class="pb-card">
                <div class="pb-k">Your request</div>
                <div class="pb-quote">“{esc(state['request'])}”</div>
            </div>
            <div class="pb-arrow">↓</div>
            <div class="pb-card">
                <div class="pb-k">Detected task</div>
                <div class="pb-task"><span class="pb-ico">{icon}</span>{esc(label)}</div>
                <div class="pb-k" style="margin-top:14px">Prompt readiness · {score}%</div>
                <div class="meter {level[1]}"><span style="width:{score}%"></span></div>
                <div class="pb-level">{esc(level[0])}</div>
                {f'<div class="chips-row">{found}</div>' if found else ''}
                {f'<div class="pb-k" style="margin-top:10px">Could add (optional)</div><div class="chips-row">{missing}</div>' if missing else ''}
            </div>
            <div class="pb-arrow">↓</div>
        </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="pb-step">Your AI-ready prompt</div>', unsafe_allow_html=True)
    fmts = pb.formats(result)
    tabs = st.tabs([name for name, _ in fmts])
    for tab, (name, text) in zip(tabs, fmts):
        with tab:
            st.code(text, language="json" if name == "JSON" else None, wrap_lines=True)

    c1, c2 = st.columns(2)
    with c1:
        copy_button("📋 Copy Prompt", result["final_prompt"], "copy_prompt")
    with c2:
        copy_button("{ } Copy JSON", dict(fmts)["JSON"], "copy_json")

    actions = st.container(horizontal=True, gap="small", key="pb_actions")
    with actions:
        for action, text, ico in (("improve", "Improve", "auto_fix_high"),
                                  ("detailed", "Make Detailed", "unfold_more"),
                                  ("concise", "Make Concise", "unfold_less"),
                                  ("regenerate", "Regenerate", "refresh")):
            if st.button(text, icon=f":material/{ico}:", key=f"pb_{action}"):
                generate(request=state["request"], action=action,
                         previous=None if action == "regenerate" else result["final_prompt"],
                         force=action == "regenerate")
        st.button("Start Over", icon=":material/restart_alt:", key="pb_reset", on_click=start_over)
    meta = result.get("meta", {})
    st.caption(f"Built in {meta.get('seconds', 0)}s by {meta.get('model', '')} on ELAW AI's own "
               f"server · free · {meta.get('quota_left', 0)} of {meta.get('quota_limit', 0)} "
               "builds left today")

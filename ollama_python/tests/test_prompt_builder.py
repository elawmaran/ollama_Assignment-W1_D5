import json

import pytest
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

import streamlit_auth
from conftest import ROOT
from elaw_core import config
from elaw_core import prompt_builder as pb
from elaw_core.db import session_scope
from elaw_core.models import Role, Status, UsageEvent, User
from elaw_core.ollama_runtime import generation_slots

CLEAR = {
    "task_type": "image",
    "understood": [{"label": "Subject", "value": "Futuristic Indian warrior"},
                   {"label": "Count", "value": "2"}],
    "missing_information": ["Aspect ratio"], "needs_clarification": False, "questions": [],
    "readiness_score": 85,
    "final_prompt": "Create two images of a futuristic Indian warrior: one standing, one sitting.",
    "structured_prompt": {"role": "Concept artist", "task": "Two images", "context": "Game",
                          "requirements": ["Standing pose", "Sitting pose"],
                          "output_format": "2 images", "constraints": ["Cinematic"]},
    "specialized_prompt": "futuristic Indian warrior, standing, cinematic lighting",
}
VAGUE = {**CLEAR, "task_type": "other", "understood": [], "needs_clarification": True,
         "questions": ["What do you want to create?"], "readiness_score": 10, "final_prompt": "",
         "specialized_prompt": ""}


class FakeClient:
    """Streams a JSON answer the way Ollama does."""
    calls = []

    def __init__(self, payload, done_reason="stop", fail=False):
        self.payload, self.done_reason, self.fail = payload, done_reason, fail

    def chat(self, **kwargs):
        FakeClient.calls.append(kwargs)
        if self.fail:
            raise ConnectionError("ollama down")
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        for i in range(0, len(text), 20):
            yield {"message": {"content": text[i:i + 20]}, "done": False}
        yield {"message": {"content": ""}, "done": True, "prompt_eval_count": 300,
               "eval_count": 200, "done_reason": self.done_reason}


@pytest.fixture()
def fake(env, monkeypatch):
    FakeClient.calls = []

    def use(payload=CLEAR, **kw):
        monkeypatch.setattr(pb, "_client", lambda: FakeClient(payload, **kw))
    use()
    return use


def _run(user=None, ip="1.2.3.4", request="two warrior images standing sitting", **kw):
    with session_scope() as db:
        return pb.run(db, user=user, ip=ip, request=request, **kw)


def _user(role=Role.USER, email="u@example.com"):
    with session_scope() as db:
        u = User(name="U", email=email, password_hash="x", email_verified=True,
                 status=Status.ACTIVE, role=role)
        db.add(u)
    return u


# ---------------- core behaviour ----------------
def test_build_uses_ollama_with_schema_and_records_usage(fake):
    result = _run()
    call = FakeClient.calls[0]
    assert call["format"] == pb.SCHEMA and call["model"] == config.settings.prompt_builder_model
    assert call["messages"][0]["content"] == pb.SYSTEM_PROMPT
    assert result["task_type"] == "image" and result["readiness_score"] == 85
    names = [n for n, _ in pb.formats(result)]
    assert names == ["Universal prompt", "Structured prompt", "Image prompt", "JSON"]
    data = json.loads(dict(pb.formats(result))["JSON"])
    assert data["task_type"] == "image" and data["details"]["Count"] == "2"
    with session_scope() as db:
        ev = db.scalar(select(UsageEvent))
        assert ev.feature == "prompt_builder" and ev.provider == "ollama"
        assert ev.user_id is None and ev.client_key == pb.client_key("1.2.3.4")
        assert "1.2.3.4" not in ev.client_key and ev.estimated_cost == 0


def test_vague_request_asks_instead_of_inventing(fake):
    fake(VAGUE)
    result = _run(request="make good prompt")
    assert result["needs_clarification"] and result["questions"]
    assert result["final_prompt"] == "" and result["readiness_score"] <= 40


def test_answers_force_a_prompt(fake):
    fake(VAGUE)  # model still wants to ask, but has no prompt: not allowed after answers
    with pytest.raises(pb.PromptBuilderError) as e:
        _run(request="make good prompt", answers=[("What?", "a logo")], round_no=2)
    assert e.value.code == "bad_output"
    fake(CLEAR)
    result = _run(request="make good prompt", answers=[("What?", "a logo")], round_no=2)
    assert not result["needs_clarification"]
    assert "a logo" in FakeClient.calls[-1]["messages"][1]["content"]


def test_irrelevant_formats_hidden(fake):
    fake({**CLEAR, "task_type": "writing", "specialized_prompt": "should be dropped"})
    result = _run(request="write professional email asking client requirements")
    assert [n for n, _ in pb.formats(result)] == ["Universal prompt", "Structured prompt", "JSON"]


def test_normalize_is_defensive():
    r = pb.normalize({**CLEAR, "task_type": "hacking", "readiness_score": 999,
                      "questions": ["a", "b", "c", "d"], "understood": [{"label": "x"}] + CLEAR["understood"]})
    assert r["task_type"] == "other" and r["readiness_score"] == 100 and r["questions"] == []
    assert len(r["understood"]) == 2
    with pytest.raises(pb.PromptBuilderError):
        pb.normalize("not a dict")


# ---------------- limits and safety ----------------
def test_input_validation(fake):
    for bad in ("", "  ", "x" * (config.settings.pb_max_input_chars + 1)):
        with pytest.raises(pb.PromptBuilderError) as e:
            _run(request=bad)
        assert e.value.code == "invalid_input"
    assert FakeClient.calls == []  # never reached Ollama


def test_guest_daily_limit_by_ip(fake, monkeypatch):
    monkeypatch.setenv("GUEST_DAILY_LIMIT", "2")
    monkeypatch.setenv("PROMPT_BUILDER_PER_MINUTE", "100")
    config.reload_settings()
    _run(), _run()
    with pytest.raises(pb.PromptBuilderError) as e:
        _run()
    assert e.value.code == "limit_reached" and "Log in" in e.value.message
    assert _run(ip="9.9.9.9")["task_type"] == "image"  # another visitor is unaffected


def test_logged_in_users_get_their_own_higher_limit(fake, monkeypatch):
    monkeypatch.setenv("GUEST_DAILY_LIMIT", "1")
    monkeypatch.setenv("FREE_DAILY_LIMIT", "3")
    monkeypatch.setenv("PROMPT_BUILDER_PER_MINUTE", "100")
    config.reload_settings()
    u = _user()
    for _ in range(3):
        _run(user=u)
    with pytest.raises(pb.PromptBuilderError) as e:
        _run(user=u)
    assert e.value.code == "limit_reached"
    with session_scope() as db:
        assert pb.quota(db, u, "1.2.3.4") == {"used": 3, "limit": 3, "left": 0}
        assert pb.quota(db, None, "1.2.3.4")["used"] == 0  # guest count is separate


def test_burst_rate_limit(fake, monkeypatch):
    monkeypatch.setenv("PROMPT_BUILDER_PER_MINUTE", "2")
    config.reload_settings()
    _run(), _run()
    with pytest.raises(pb.PromptBuilderError) as e:
        _run()
    assert e.value.code == "rate_limited"


def test_paid_model_is_blocked_in_no_cost_mode(fake, monkeypatch):
    monkeypatch.setenv("PROMPT_BUILDER_MODEL", "gpt-4o-mini")
    config.reload_settings()
    with pytest.raises(pb.PromptBuilderError) as e:
        _run()
    assert e.value.code == "paid_blocked" and FakeClient.calls == []


def test_busy_server_and_shared_queue(fake, monkeypatch):
    monkeypatch.setenv("PROMPT_BUILDER_QUEUE_WAIT", "0")
    config.reload_settings()
    slots = generation_slots()
    slots.acquire()  # e.g. a chat answer is being generated
    try:
        with pytest.raises(pb.PromptBuilderError) as e:
            _run()
        assert e.value.code == "busy"
    finally:
        slots.release()
    assert _run()["task_type"] == "image"


def test_ollama_failures_are_graceful_and_not_counted(fake):
    for kwargs, code in (({"fail": True}, "ollama_down"), ({"done_reason": "length"}, "bad_output")):
        fake(CLEAR, **kwargs)
        with pytest.raises(pb.PromptBuilderError) as e:
            _run()
        assert e.value.code == code
    fake("{not json")
    with pytest.raises(pb.PromptBuilderError) as e:
        _run()
    assert e.value.code == "bad_output"
    with session_scope() as db:
        assert db.scalar(select(UsageEvent)) is None
    assert generation_slots().acquire(blocking=False)  # the queue was always released
    generation_slots().release()


def test_no_openai_dependency():
    reqs = (ROOT / "requirements.txt").read_text().lower()
    assert "openai" not in reqs and "anthropic" not in reqs
    src = (ROOT / "elaw_core" / "prompt_builder.py").read_text()
    assert "import openai" not in src and "OPENAI_API_KEY" not in src


# ---------------- pages ----------------
def _links(at):
    return [c.proto.label for c in at.sidebar.children.values() if type(c).__name__ == "PageLink"]


def test_guest_can_use_prompt_builder_but_not_chat(fake, monkeypatch):
    monkeypatch.setattr(streamlit_auth, "current_user", lambda: None)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert len(at.chat_input) == 0  # still no chat for guests
    at.switch_page("views/prompt_builder.py").run()
    assert not at.exception, at.exception
    assert "Chat" not in _links(at)
    at.text_area(key="pb_request").set_value("two warrior images standing sitting").run()
    at.button(key="pb_build").click().run()
    assert not at.exception, at.exception
    page = " ".join(m.value for m in at.markdown)
    assert "Detected task" in page and "Image" in page
    assert [t.label for t in at.tabs] == ["Universal prompt", "Structured prompt", "Image prompt", "JSON"]
    assert any("futuristic Indian warrior" in c.value for c in at.code)


def test_prompt_builder_clarification_flow(fake, monkeypatch):
    monkeypatch.setattr(streamlit_auth, "current_user", lambda: None)
    fake(VAGUE)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    at.switch_page("views/prompt_builder.py").run()
    at.text_area(key="pb_request").set_value("make good prompt").run()
    at.button(key="pb_build").click().run()
    assert "quick questions" in " ".join(m.value for m in at.markdown)
    assert [t.label for t in at.text_input] == ["What do you want to create?"]


def test_logged_in_chat_still_renders_with_prompt_builder_link(env, monkeypatch):
    u = _user()
    monkeypatch.setattr(streamlit_auth, "current_user", lambda: u)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    assert len(at.chat_input) == 1
    assert any("Prompt Builder" in label for label in _links(at))

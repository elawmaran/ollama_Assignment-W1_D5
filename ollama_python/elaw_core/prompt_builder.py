"""Prompt Builder: turns a rough request into an AI-ready prompt, using local Ollama only.

    rough request → [limits, rate, no-cost guard, shared Ollama queue] → Ollama (JSON schema)
                  → normalize() → formats (universal / structured / image|video|developer / JSON)

No OpenAI, no API keys, and the browser never talks to Ollama: this runs on the server.
Every limit comes from elaw_core.config (env vars), not from this file.
"""

import hashlib
import json
import re
import time

import ollama

from elaw_core import config, usage
from elaw_core.db import utcnow
from elaw_core.models import Role, UsageEvent
from elaw_core.ollama_runtime import generation_slots
from elaw_core.security import limiter

FEATURE = "prompt_builder"

# key: (label, material icon). Order = how they appear in the schema enum.
TASK_TYPES = {
    "image": ("Image", "image"),
    "video": ("Video", "movie"),
    "coding": ("Coding", "code"),
    "game_qa": ("Game QA", "bug_report"),
    "writing": ("Writing", "edit_note"),
    "marketing": ("Marketing", "campaign"),
    "creative": ("Creative", "palette"),
    "research": ("Research", "travel_explore"),
    "data": ("Data", "table_chart"),
    "other": ("General", "auto_awesome"),
}
SPECIAL_FORMAT = {"image": "Image prompt", "video": "Video prompt", "coding": "Developer prompt"}

SYSTEM_PROMPT = """You are ELAW Prompt Builder. Turn a user's rough request (informal, incomplete or with mistakes) into a precise prompt for another AI. Reply only with JSON. No explanations.

Main rule: keep the user's meaning and never invent facts (colours, places, objects, names, numbers, plot, styles). Unknown details go in missing_information. Neutral quality words are fine ("high detail", "clear structure").

Ask questions (needs_clarification true) ONLY if you cannot tell what to make or what it is about, like "make good prompt". If the output type and subject are known, do not ask, even if style, length or audience are missing.

Fields:
- task_type: image (also logos, posters, characters), video, coding, game_qa (test cases or QA for games, slot games), writing, marketing, creative, research, data, other.
- understood: only facts the user gave, as short label/value pairs (Subject, Count, Poses, Style, Purpose, Setting, Action, Audience, Feature).
- missing_information: up to 4 useful details not given, 1 to 4 words each.
- questions: 1 to 3 short questions if asking, else empty.
- readiness_score: 0-100 (clear task and subject about 60, more with each given detail).
- final_prompt: always write it. A clear English instruction to an AI with every requirement the user gave; for several outputs describe each one; leave unknown details open.
- specialized_prompt: image: comma-separated text-to-image descriptors from the user's facts, one paragraph per image. video: text-to-video prompt (subject, action, setting). coding: prompt for a coding assistant (goal, inputs/outputs, edge cases, tests). Else empty. Only the prompt, no labels or notes.

Example: "need 2 pics robot cat one running one sleeping anime" -> image; understood Subject=robot cat, Count=2, Poses=running and sleeping, Style=anime; missing Background, Aspect ratio; final_prompt "Create two anime-style images of a robot cat: 1) running, 2) sleeping. Keep the same character design in both, high detail." """

_ITEM = {"type": "object", "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
         "required": ["label", "value"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "task_type": {"type": "string", "enum": list(TASK_TYPES)},
        "understood": {"type": "array", "items": _ITEM},
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "needs_clarification": {"type": "boolean"},
        "questions": {"type": "array", "items": {"type": "string"}},
        "readiness_score": {"type": "integer"},
        "final_prompt": {"type": "string"},
        "specialized_prompt": {"type": "string"},
    },
    "required": ["task_type", "understood", "missing_information", "needs_clarification",
                 "questions", "readiness_score", "final_prompt", "specialized_prompt"],
}

REFINE = {
    "improve": "Improve the prompt below: clearer wording and better structure. Do not add new facts.",
    "detailed": "Make the prompt below more detailed: add structure, quality and output-format "
                "guidance, but do not invent new facts.",
    "concise": "Make the prompt below shorter and tighter. Keep every requirement.",
}
DEFAULT_QUESTIONS = ["What do you want to create (for example an image, a video, code or an email)?",
                     "What should it be about?"]


class PromptBuilderError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------- LIMITS ----------------
def client_key(ip):
    """Pseudonymous guest id: a hash of the IP address, never the address itself."""
    return hashlib.sha256(f"elaw-pb:{ip or 'unknown'}".encode()).hexdigest()[:32]


def daily_limit(user):
    s = config.settings
    if user is None:
        return s.guest_daily_limit
    if user.role in (Role.ADMIN, Role.SUPER_ADMIN):
        return s.admin_daily_limit
    return s.free_daily_limit


def used_today(db, user, ckey):
    """Builds in the last 24 hours (users by account, guests by hashed IP)."""
    from sqlalchemy import func, select
    from datetime import timedelta
    q = select(func.count(UsageEvent.id)).where(
        UsageEvent.feature == FEATURE, UsageEvent.created_at >= utcnow() - timedelta(hours=24))
    q = q.where(UsageEvent.user_id == user.id) if user else q.where(
        UsageEvent.client_key == ckey, UsageEvent.user_id.is_(None))
    return db.scalar(q) or 0


def quota(db, user, ip):
    limit = daily_limit(user)
    used = used_today(db, user, client_key(ip))
    return {"used": used, "limit": limit, "left": max(0, limit - used)}


# ---------------- INPUT ----------------
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text, max_chars):
    text = _CTRL.sub("", (text or "")).strip()
    return text[:max_chars]


def validate_request(text):
    s = config.settings
    raw = (text or "").strip()
    if len(raw) > s.pb_max_input_chars:
        raise PromptBuilderError("invalid_input",
                                 f"Please keep your request under {s.pb_max_input_chars} characters.")
    text = clean_text(raw, s.pb_max_input_chars)
    if len(text) < 3:
        raise PromptBuilderError("invalid_input", "Tell us a little about what you want to create.")
    return text


def build_messages(request, answers=None, previous=None, action=None, force=False):
    parts = [f"Request: {request}"]
    if answers:
        parts.append("The user answered your questions:\n" + "\n".join(
            f"Q: {q}\nA: {a}" for q, a in answers if a.strip()))
        parts.append("Do not ask more questions. Build the prompt now.")
    if force:
        parts.append("Do not ask questions. Build the best prompt from what is given and list "
                     "unknown details in missing_information.")
    if previous and action in REFINE:
        parts.append(f"{REFINE[action]}\nCurrent prompt:\n{previous}")
        parts.append("Do not ask questions.")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(parts)}]


# ---------------- OUTPUT ----------------
def _s(value, limit=400):
    return clean_text(str(value or ""), limit)


def _list(value, limit=10, item_limit=300):
    if not isinstance(value, list):
        return []
    return [x for x in (_s(v, item_limit) for v in value[:limit]) if x]


_LABEL = re.compile(r"^\s*(an?\s+)?(text-to-(image|video)|image|video|developer)\s+prompt\s*:\s*", re.I)
_PLACEHOLDER = {"a subject", "subject", "unknown", "not specified", "n/a", "none"}


def normalize(raw, round_no=1, forced=False):
    """Make the model's JSON safe and consistent, whatever it returned."""
    if not isinstance(raw, dict):
        raise PromptBuilderError("bad_output", "The AI returned an unexpected answer. Please try again.")
    task = raw.get("task_type") if raw.get("task_type") in TASK_TYPES else "other"
    understood = [{"label": _s(i.get("label"), 40), "value": _s(i.get("value"), 160)}
                  for i in (raw.get("understood") or [])[:10]
                  if isinstance(i, dict) and _s(i.get("label")) and _s(i.get("value"))
                  and _s(i.get("value")).lower() not in _PLACEHOLDER]
    final = _s(raw.get("final_prompt"), 4000)
    # Small models over-ask. Decide here instead: ask only when we don't know what to make
    # or about what (no concrete facts); otherwise build, and list the gaps as optional.
    vague = task == "other" and not understood
    asking = not forced and (vague or (bool(raw.get("needs_clarification")) and
                                       (task == "other" or not understood)))
    questions = _list(raw.get("questions"), 3, 200) if asking else []
    if asking and not questions:
        questions = DEFAULT_QUESTIONS
    if not asking and not final:
        raise PromptBuilderError("bad_output", "The AI didn't finish the prompt. Please try again.")
    try:
        score = int(raw.get("readiness_score") or 0)
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))
    score = min(score, 40) if asking else max(score, 50)
    return {
        "task_type": task,
        "needs_clarification": asking,
        "questions": questions,
        "understood": understood,
        "missing_information": _list(raw.get("missing_information"), 4, 50),
        "readiness_score": score,
        "final_prompt": "" if asking else final,
        "specialized_prompt": "" if asking or task not in SPECIAL_FORMAT
        else _LABEL.sub("", _s(raw.get("specialized_prompt"), 4000)),
    }


ROLES = {"image": "an expert concept artist and image-prompt writer",
         "video": "an expert video director", "coding": "a senior software engineer",
         "game_qa": "a senior game QA engineer", "writing": "a professional writer",
         "marketing": "an experienced marketing copywriter", "creative": "a creative writer",
         "research": "a careful research analyst", "data": "a data analyst",
         "other": "a helpful expert assistant"}
OUTPUTS = {"image": "The image(s) described above", "video": "A video matching the description",
           "coding": "Working code with a short explanation and tests",
           "game_qa": "A table of test cases: ID, title, steps, expected result, priority",
           "writing": "The finished text, ready to send", "marketing": "Ready-to-use marketing copy"}


def structured(result):
    """Built from the model's extracted facts only, so it cannot add invented details."""
    task = result["task_type"]
    return {"role": f"You are {ROLES[task]}.", "task": result["final_prompt"],
            "requirements": [f"{i['label']}: {i['value']}" for i in result["understood"]],
            "open_details": result["missing_information"],
            "output_format": OUTPUTS.get(task, "A clear, well-structured answer"),
            "constraints": ["Follow every requirement above",
                            "Do not add facts that were not requested"]}


def render_structured(result):
    sp = structured(result)
    lines = [f"Role: {sp['role']}", f"Task: {sp['task']}"]
    if sp["requirements"]:
        lines.append("Requirements:\n" + "\n".join(f"- {r}" for r in sp["requirements"]))
    if sp["open_details"]:
        lines.append("Open details (use a sensible choice or ask):\n" +
                     "\n".join(f"- {d}" for d in sp["open_details"]))
    lines.append(f"Output format: {sp['output_format']}")
    lines.append("Constraints:\n" + "\n".join(f"- {c}" for c in sp["constraints"]))
    return "\n\n".join(lines)


def json_prompt(result):
    sp = structured(result)
    data = {"task_type": result["task_type"], "role": sp["role"], "prompt": result["final_prompt"],
            "details": {i["label"]: i["value"] for i in result["understood"]},
            "open_details": sp["open_details"], "output_format": sp["output_format"],
            "constraints": sp["constraints"]}
    if result["specialized_prompt"]:
        data[SPECIAL_FORMAT[result["task_type"]].lower().replace(" ", "_")] = result["specialized_prompt"]
    return {k: v for k, v in data.items() if v not in ("", [], {})}


def formats(result):
    """Only the formats that make sense for this task, in display order."""
    out = [("Universal prompt", result["final_prompt"]),
           ("Structured prompt", render_structured(result))]
    if result["specialized_prompt"]:
        out.append((SPECIAL_FORMAT[result["task_type"]], result["specialized_prompt"]))
    out.append(("JSON", json.dumps(json_prompt(result), indent=2, ensure_ascii=False)))
    return out


# ---------------- OLLAMA CALL ----------------
def _client():
    return ollama.Client(timeout=config.settings.pb_timeout_s)


def _call_ollama(model, messages, temperature, on_progress=None):
    s = config.settings
    deadline = time.monotonic() + s.pb_timeout_s
    text, stats = "", {}
    stream = _client().chat(
        model=model, messages=messages, stream=True, format=SCHEMA, keep_alive=-1,
        options={"temperature": temperature, "num_predict": s.pb_max_output_tokens})
    for i, chunk in enumerate(stream):
        text += chunk["message"].get("content") or ""
        if on_progress and i % 8 == 0:
            on_progress(len(text))
        if chunk.get("done"):
            stats = {"in": chunk.get("prompt_eval_count") or 0, "out": chunk.get("eval_count") or 0,
                     "done_reason": chunk.get("done_reason")}
        if time.monotonic() > deadline:
            raise PromptBuilderError("timeout", "The AI took too long. Please try again in a moment.")
    if stats.get("done_reason") == "length":
        raise PromptBuilderError("bad_output", "The answer got too long. Try “Make Concise” or a shorter request.")
    try:
        return json.loads(text), stats
    except json.JSONDecodeError:
        raise PromptBuilderError("bad_output", "The AI returned an unexpected answer. Please try again.")


def run(db, *, user, ip, request, answers=None, previous=None, action=None, force=False,
        round_no=1, on_progress=None, on_queue=None):
    """One Prompt Builder generation with every safety check. Returns the result dict.

    action: None (build), "improve", "detailed", "concise" or "regenerate".
    """
    s = config.settings
    request = validate_request(request)
    answers = [(clean_text(q, 200), clean_text(a, 300)) for q, a in (answers or [])]
    previous = clean_text(previous, 4000) if previous else None

    # 1. daily limit (per account, or per hashed IP for guests)
    ckey = None if user else client_key(ip)
    q = quota(db, user, ip)
    if q["left"] <= 0:
        hint = " Log in for more." if user is None else " It resets over the next 24 hours."
        raise PromptBuilderError("limit_reached",
                                 f"You've used all {q['limit']} prompt builds for today.{hint}")
    # 2. burst rate limit
    who = f"user:{user.id}" if user else f"guest:{ckey}"
    if not limiter.hit(("prompt_builder", who), s.pb_per_minute, 60):
        raise PromptBuilderError("rate_limited", "That's a lot of builds in a minute. "
                                 "Please wait a few seconds and try again.")
    # 3. no-cost guard: refuses any paid provider before a request is made
    model = s.prompt_builder_model
    try:
        provider = usage.route(model)
    except usage.PaidProviderBlocked as error:
        raise PromptBuilderError("paid_blocked", str(error))
    # 4. shared Ollama queue (Chat and Prompt Builder take turns on the CPU)
    slots = generation_slots()
    if not slots.acquire(blocking=False):
        if on_queue:
            on_queue()
        if not slots.acquire(timeout=s.pb_queue_wait_s):
            raise PromptBuilderError("busy", "The free AI server is busy. Please try again in a minute.")
    started = time.perf_counter()
    try:
        messages = build_messages(request, answers, previous, action, force)
        temperature = 0.7 if action == "regenerate" else 0.3
        raw, stats = _call_ollama(model, messages, temperature, on_progress)
    except PromptBuilderError:
        raise
    except Exception as error:
        raise PromptBuilderError("ollama_down", "The AI engine isn't reachable right now. "
                                 "Please try again shortly.") from error
    finally:
        slots.release()

    result = normalize(raw, round_no=round_no, forced=force or bool(previous) or bool(answers))
    usage.record_usage(db, user.id if user else None, model, provider, stats.get("in"),
                       stats.get("out"), feature=FEATURE, client_key=ckey)
    result["meta"] = {"seconds": round(time.perf_counter() - started, 1), "model": model,
                      "quota_left": max(0, q["left"] - 1), "quota_limit": q["limit"]}
    return result

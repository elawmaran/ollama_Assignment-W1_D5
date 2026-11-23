# Prompt Builder

> "Tell us what you want. ELAW AI turns it into an AI-ready prompt."

Live at `/prompt-builder`. Guests can use it within limits, and logged-in users get more.
It uses the local Ollama model only. There is no OpenAI integration and no API keys.

## How it works

```
Browser ─► Streamlit page  views/prompt_builder.py           (UI only)
             └─► elaw_core/prompt_builder.run()              (server side)
                   1. validate input (length, control characters)
                   2. daily limit: per account, or per hashed IP for guests
                   3. burst limit (per minute)
                   4. no-cost guard: usage.route() refuses paid providers
                   5. shared Ollama queue (elaw_core/ollama_runtime.py), also used by Chat
                   6. Ollama chat, streaming, with a JSON schema (format=SCHEMA), output
                      token cap and timeout
                   7. normalize(): clamp and clean every field the model returns
                   8. record usage_events (feature="prompt_builder") only on success
```

The browser never talks to Ollama. Ollama is only reachable on the Docker network.

## Model output (JSON schema enforced by Ollama)

```json
{
  "task_type": "image|video|coding|game_qa|writing|marketing|creative|research|data|other",
  "understood": [{"label": "Subject", "value": "Futuristic Indian warrior"}],
  "missing_information": ["Aspect ratio"],
  "needs_clarification": false,
  "questions": [],
  "readiness_score": 85,
  "final_prompt": "…",
  "structured_prompt": {"role": "", "task": "", "context": "", "requirements": [],
                        "output_format": "", "constraints": []},
  "specialized_prompt": "image, video or developer prompt when relevant"
}
```

The code builds the Structured and JSON prompts from this output, so they cost no extra
model tokens. Only the formats that fit the task are shown:
- Universal, Structured and JSON prompts, always.
- An Image prompt for image tasks, a Video prompt for video tasks, and a Developer prompt
  for coding tasks.

**Clarification:** the model asks 1–3 questions only when it can't tell what to create
or what it's about, for example "make good prompt". After the user answers, or chooses
"Skip, build anyway", it is told not to ask again.

**Actions:** Improve, Make Detailed and Make Concise refine the current prompt. Regenerate
rebuilds it at a higher temperature. Each one counts as a build.

## Limits (all in `elaw_core/config.py`, set by env vars)

| Variable | Default | Meaning |
|---|---|---|
| `GUEST_DAILY_LIMIT` | 5 | builds per rolling 24h without an account, counted by hashed IP |
| `FREE_DAILY_LIMIT` | 20 | builds per 24h for logged-in customers |
| `ADMIN_DAILY_LIMIT` | 200 | builds per 24h for admins |
| `PROMPT_BUILDER_PER_MINUTE` | 4 | burst limit per visitor |
| `PROMPT_BUILDER_MAX_INPUT` | 1000 | maximum request length, in characters |
| `PROMPT_BUILDER_MAX_TOKENS` | 900 | maximum tokens the model may write per build |
| `PROMPT_BUILDER_TIMEOUT` | 150 | seconds before a build is abandoned |
| `PROMPT_BUILDER_QUEUE_WAIT` | 90 | seconds to wait while Chat or another build uses the CPU |
| `PROMPT_BUILDER_MODEL` | `DEFAULT_MODEL` | must be a local Ollama model (paid ones are refused) |

Guests are identified by a SHA-256 hash of their IP address, never the address itself.
A failed build (timeout, Ollama down, bad output) doesn't count toward the limit.

## Coming soon (shown in the sidebar, not built yet)

Game QA Creator and Creative AI. To add a new tool:
1. Write its own system prompt and schema next to `prompt_builder.py`.
2. Reuse `run()`'s guard steps (limits, no-cost guard, shared queue, usage).
3. Add a page in `views/` and register it in `app.py`.

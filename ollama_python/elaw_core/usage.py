"""AI gateway pieces: model router, no-cost guard, usage tracker and credits.

Authentication never touches this module and never spends AI credits.

    Customer → Authentication → ELAW AI → AI Gateway → Model Router
                                               ├─ Ollama (local, free)  ← used today
                                               └─ OpenAI (paid)         ← blocked while COST_MODE=free
                                           → Usage Tracker → Credits
"""

import math
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from elaw_core import config
from elaw_core.db import utcnow
from elaw_core.models import UsageEvent

# USD per 1M tokens (input, output). Local models cost nothing per request.
PROVIDERS = {
    "ollama": {"paid": False, "price_in": 0.0, "price_out": 0.0},
    "openai": {"paid": True, "price_in": 0.15, "price_out": 0.60},  # placeholder, not wired up
}

TOKENS_PER_CREDIT = 1000

# Prepared for future plans. No payments are processed; everyone is FREE today.
PLAN_LIMITS = {
    "FREE": {"monthly_credits": 500, "price_usd": 0},
    "PERSONAL": {"monthly_credits": 3000, "price_usd": None},
    "PRO": {"monthly_credits": 15000, "price_usd": None},
    "BUSINESS": {"monthly_credits": 60000, "price_usd": None},
    "ENTERPRISE": {"monthly_credits": None, "price_usd": None},
}


class PaidProviderBlocked(Exception):
    pass


def route(model):
    """Pick the provider for a model name."""
    name = model.lower()
    if name.startswith(("gpt-", "o1", "o3", "o4", "openai/")):
        provider = "openai"
    else:
        provider = "ollama"
    if PROVIDERS[provider]["paid"] and config.settings.cost_mode == "free":
        raise PaidProviderBlocked(
            f"{model} uses a paid provider, which is blocked in no-cost mode (COST_MODE=free).")
    return provider


def estimate_cost(provider, input_tokens, output_tokens):
    p = PROVIDERS[provider]
    return round((input_tokens * p["price_in"] + output_tokens * p["price_out"]) / 1_000_000, 6)


def credits_for(input_tokens, output_tokens):
    return math.ceil((input_tokens + output_tokens) / TOKENS_PER_CREDIT)


def record_usage(db, user_id, model, provider, input_tokens, output_tokens, feature="chat"):
    input_tokens, output_tokens = int(input_tokens or 0), int(output_tokens or 0)
    event = UsageEvent(
        user_id=user_id, request_id=str(uuid.uuid4()), model=model[:100], provider=provider,
        feature=feature, input_tokens=input_tokens, output_tokens=output_tokens,
        estimated_cost=estimate_cost(provider, input_tokens, output_tokens),
        credits_used=credits_for(input_tokens, output_tokens))
    db.add(event)
    return event


def credits_this_month(db, user_id):
    now = utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.coalesce(func.sum(UsageEvent.credits_used), 0)).where(
        UsageEvent.user_id == user_id, UsageEvent.created_at >= start)) or 0


def requests_since(db, user_id, hours=24):
    return db.scalar(select(func.count(UsageEvent.id)).where(
        UsageEvent.user_id == user_id,
        UsageEvent.created_at >= utcnow() - timedelta(hours=hours))) or 0

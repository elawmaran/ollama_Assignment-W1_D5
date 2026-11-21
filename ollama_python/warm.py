"""Load the main model into Ollama's memory when the container starts.

Runs in the background next to Streamlit and retries until Ollama is reachable,
so the first visitor after a server restart doesn't wait for a cold load.
"""
import os
import time

import ollama

model = os.getenv("DEFAULT_MODEL", "qwen3:8b")
keep = os.getenv("KEEP_ALIVE", "-1").strip()
keep_alive = int(keep) if keep.lstrip("-").isdigit() else keep

for attempt in range(60):  # up to ~10 minutes
    try:
        ollama.generate(model=model, prompt="", keep_alive=keep_alive)
        print(f"warm.py: {model} loaded (keep_alive={keep_alive})", flush=True)
        break
    except Exception as error:
        print(f"warm.py: attempt {attempt + 1} failed: {error}", flush=True)
        time.sleep(10)

"""One place for things every Ollama caller must share.

The server has 2 CPUs, so Chat and Prompt Builder take turns through the same
semaphore instead of generating at the same time and slowing each other down.
"""

import threading

from elaw_core import config

_slots = None
_lock = threading.Lock()


def generation_slots():
    """Process-wide semaphore shared by every feature that calls Ollama."""
    global _slots
    with _lock:
        if _slots is None:
            _slots = threading.Semaphore(max(1, config.settings.max_concurrent))
        return _slots

"""Password hashing, secure tokens, validation and rate limiting."""

import hashlib
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id with OWASP's minimum profile (19 MiB, t=2, p=1): strong, but light
# enough for a small shared server where the AI models use most of the RAM.
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)

# Pre-computed so unknown emails cost the same time as real ones (no timing leak).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password):
    return _hasher.hash(password)


def verify_password(password_hash, password):
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def burn_password_check(password):
    verify_password(_DUMMY_HASH, password)


def needs_rehash(password_hash):
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def new_token():
    """Return (raw_token, sha256_hex). Only the hash is ever stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------- VALIDATION ----------------
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789", "1234567890",
    "qwerty123", "iloveyou1", "admin123", "welcome1", "letmein1", "elawai123",
    "qwertyuiop", "11111111", "abc12345", "passw0rd",
}


def normalize_email(email):
    return (email or "").strip().lower()


def validate_email(email):
    if not email or len(email) > 254 or not EMAIL_RE.match(email):
        return "Please enter a valid email address."
    return None


def validate_password(password, email=""):
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if len(password) > 128:
        return "Password must be at most 128 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Password must contain at least one letter and one number."
    if password.lower() in COMMON_PASSWORDS:
        return "That password is too common. Please choose another."
    if email and password.lower() == email.lower():
        return "Password can't be the same as your email."
    return None


def validate_name(name):
    name = (name or "").strip()
    if not 1 <= len(name) <= 100:
        return "Please enter your name (up to 100 characters)."
    if re.search(r"[<>\x00-\x1f]", name):
        return "Name contains characters that aren't allowed."
    return None


def device_info(user_agent):
    """Short, non-identifying summary like 'Chrome on macOS'."""
    ua = user_agent or ""
    browser = next((b for k, b in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                   ("Chrome/", "Chrome"), ("Safari/", "Safari"),
                                   ("curl/", "curl"), ("python", "Script"))
                    if k.lower() in ua.lower()), "Unknown browser")
    os_name = next((o for k, o in (("Windows", "Windows"), ("iPhone", "iOS"), ("iPad", "iPadOS"),
                                   ("Android", "Android"), ("Mac OS X", "macOS"),
                                   ("Linux", "Linux"))
                    if k in ua), "unknown OS")
    return f"{browser} on {os_name}"


# ---------------- RATE LIMITING ----------------
class RateLimiter:
    """In-memory sliding window, keyed by (action, who). One auth process."""

    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key, limit, window_s):
        """Record an attempt. Return True if it is allowed."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()

# (limit, window seconds) per action
LIMITS = {
    "register_ip": (5, 3600),
    "login_ip": (20, 300),
    "forgot_ip": (10, 3600),
    "forgot_email": (3, 900),
    "resend_ip": (10, 3600),
    "resend_email": (3, 900),
    "reset_ip": (10, 900),
    "verify_ip": (30, 900),
}


def allow(action, who):
    limit, window = LIMITS[action]
    return limiter.hit((action, who), limit, window)

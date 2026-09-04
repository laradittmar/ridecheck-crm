"""Authentication primitives.

SEC-PRELAUNCH-SOURCE-HARDENING: this module contains **no working credential and no
signing key**. Both are supplied by the environment, and when either is absent the
affected path fails **closed** — it never falls back to a value that is readable in the
source tree.

Two separate defaults were removed, and they had to go together:

* ``ADMIN_PASSWORD`` — a literal fallback let anyone who read the repository log in as
  the administrator whenever the variable was unset (SEC-03);
* ``AUTH_SECRET_KEY`` / ``SECRET_KEY`` — a literal fallback let anyone who read the
  repository *forge a signed session cookie*, which bypasses the password check
  altogether (SEC-04). Fixing the password while leaving the signing key predictable
  would have been security theatre.

Neither default is replaced by a new secret here. Absent configuration means admin
authentication is unavailable, and that is the intended outcome.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone

from passlib.context import CryptContext

logger = logging.getLogger(__name__)

SESSION_COOKIE = "crm_session"
pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
)

ADMIN_PASSWORD_ENV = "ADMIN_PASSWORD"
AUTH_SECRET_ENVS = ("AUTH_SECRET_KEY", "SECRET_KEY")


class AuthConfigurationError(RuntimeError):
    """Raised when authentication is asked to run without its required configuration."""


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def auth_secret_configured() -> bool:
    """True when a session-signing key is supplied by the environment."""
    return any(_env(name) for name in AUTH_SECRET_ENVS)


def admin_auth_configured() -> bool:
    """True when environment-based admin login is usable at all."""
    return bool(_env(ADMIN_PASSWORD_ENV))


def _secret() -> str:
    """The session-signing key. No default — an absent key is a configuration error.

    A predictable signing key is a complete authentication bypass: the cookie is the
    credential once it is signed. There is deliberately nothing to fall back to.
    """
    for name in AUTH_SECRET_ENVS:
        value = _env(name)
        if value:
            return value
    logger.error(
        "AUTH_CONFIGURATION_ERROR missing=%s — session signing unavailable; "
        "authentication fails closed", "/".join(AUTH_SECRET_ENVS),
    )
    raise AuthConfigurationError(
        "Session signing key is not configured. Set AUTH_SECRET_KEY (or SECRET_KEY)."
    )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64dec(value: str) -> bytes:
    pad = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + pad).encode("utf-8"))


def sign_session(payload: dict[str, str]) -> str:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    body_b64 = _b64(body)
    sig = hmac.new(_secret().encode("utf-8"), body_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body_b64}.{sig}"


def verify_session(token: str | None) -> dict[str, str] | None:
    """Validate a session cookie. An unconfigured signing key denies every session."""
    if not token or "." not in token:
        return None
    try:
        secret = _secret()
    except AuthConfigurationError:
        return None                      # fail closed: no key, no valid session
    body_b64, sig = token.rsplit(".", 1)
    expected = hmac.new(secret.encode("utf-8"), body_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_b64dec(body_b64).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def login_ok(email: str, password: str) -> bool:
    """Environment-based admin login. Fails closed when ADMIN_PASSWORD is not set.

    There is no default password. When the variable is absent this returns False and
    logs a configuration error, so an unconfigured deployment has *no* admin credential
    rather than a publicly known one. Any database-backed user remains unaffected.
    """
    admin_password = _env(ADMIN_PASSWORD_ENV)
    if not admin_password:
        logger.error(
            "AUTH_CONFIGURATION_ERROR missing=%s — environment admin login is disabled; "
            "no fallback credential exists", ADMIN_PASSWORD_ENV,
        )
        return False
    admin_email = (os.getenv("ADMIN_EMAIL", "admin@ridecheck.local") or "").strip().lower()
    # Constant-time on both fields: a timing signal on either one is still a signal.
    email_ok = hmac.compare_digest(email.strip().lower().encode("utf-8"),
                                   admin_email.encode("utf-8"))
    password_ok = hmac.compare_digest(password.encode("utf-8"),
                                      admin_password.encode("utf-8"))
    return email_ok and password_ok


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def validate_password_rules(password: str) -> str | None:
    if not password or len(password.strip()) == 0:
        return "Password is required."
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password) > 72:
        return "Password must be at most 72 characters."
    return None


def build_session(email: str) -> dict[str, str]:
    return {
        "email": email.strip().lower(),
        "iat": datetime.now(timezone.utc).isoformat(),
    }

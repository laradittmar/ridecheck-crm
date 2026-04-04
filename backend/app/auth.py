from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone

from passlib.context import CryptContext


SESSION_COOKIE = "crm_session"
pwd_context = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
)


def _secret() -> str:
    return os.getenv("AUTH_SECRET_KEY", os.getenv("SECRET_KEY", "dev-only-change-me"))


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
    if not token or "." not in token:
        return None
    body_b64, sig = token.rsplit(".", 1)
    expected = hmac.new(_secret().encode("utf-8"), body_b64.encode("utf-8"), hashlib.sha256).hexdigest()
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
    admin_email = os.getenv("ADMIN_EMAIL", "admin@ridecheck.local").strip().lower()
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    return email.strip().lower() == admin_email and password == admin_password


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

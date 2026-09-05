from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    whatsapp_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    n8n_webhook_url: str = ""
    openai_api_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    resend_api_key: str = ""
    internal_booking_email_from: str = ""
    internal_booking_email_to: str = ""
    internal_booking_email_reply_to: str = ""
    whatsapp_flow_id: str = ""
    whatsapp_website_flow_id: str = ""
    whatsapp_vehicle_fallback_flow_id: str = ""
    whatsapp_location_fallback_flow_id: str = ""
    # M21.3-C-D: RideCheck Booking Flow (Data Exchange version)
    booking_flow_id: str = ""                  # WHATSAPP_BOOKING_FLOW_ID — Meta Flow asset ID
    flow_booking_private_key_path: str = ""    # FLOW_BOOKING_PRIVATE_KEY_PATH — PEM file for Data Exchange decrypt

    # L4.7B shadow UNDERSTAND — OFF by default; enabled in crm_test only.
    # When false the interpreter is never constructed and no model call is made.
    shadow_understand_enabled: bool = False
    shadow_understand_async: bool = False
    # L4.7C.2 — first authority cutover, one flag per claim family. Default OFF everywhere;
    # turning a flag off restores the legacy write path exactly.
    reconciler_vehicle_authority_enabled: bool = False
    reconciler_location_authority_enabled: bool = False
    # L4.7C.3B — acceptance / commercial-progression authority. Default OFF everywhere.
    reconciler_acceptance_authority_enabled: bool = False
    # L4.7C.4 — scheduling-language interpretation. Default OFF everywhere.
    reconciler_scheduling_authority_enabled: bool = False
    # L4.7C.4A — same-turn semantic evidence. When false the interpretation stays behind the
    # turn exactly as in L4.7C.4; when true a scheduling turn waits for the ONE interpretation
    # already dispatched for this burst. Never a second model call. Default OFF everywhere.
    # L4.7W1-F4 — machine/human boundary on /api/*. Enforced only when enabled AND a
    # secret is configured, so turning it on is a deliberate, reversible act and an
    # unconfigured deployment cannot silently lose its transport.
    internal_api_auth_enabled: bool = False
    internal_api_secret: str = ""
    semantic_same_turn_enabled: bool = False
    semantic_same_turn_timeout_seconds: float = 6.0
    shadow_evidence_path: str = ""
    conversation_engine_direct_webhook_enabled: bool = False
    openai_chat_model: str = "gpt-4o-mini"
    quarantined_test_wa_ids: tuple[str, ...] = ()
    closed_beta_allowed_wa_ids: tuple[str, ...] = ()

    @property
    def whatsapp_enabled(self) -> bool:
        # Enabled only when any required WhatsApp credential was provided.
        return any((self.whatsapp_token, self.whatsapp_verify_token, self.whatsapp_phone_number_id))

    def missing_whatsapp_required_vars(self) -> list[str]:
        if not self.whatsapp_enabled:
            return []

        missing: list[str] = []
        if not self.whatsapp_token:
            missing.append("WHATSAPP_TOKEN")
        if not self.whatsapp_verify_token:
            missing.append("WHATSAPP_VERIFY_TOKEN")
        if not self.whatsapp_phone_number_id:
            missing.append("WHATSAPP_PHONE_NUMBER_ID")
        return missing


def _getenv(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _float_env(name: str, default: float) -> float:
    """A bad or missing value falls back to the safe default rather than failing boot."""
    try:
        raw = _getenv(name, "")
        return float(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _parse_quarantined_wa_ids() -> tuple[str, ...]:
    raw = os.getenv("QUARANTINED_TEST_WA_IDS", "").strip()
    if not raw:
        return ()
    return tuple(wa_id.strip() for wa_id in raw.split(",") if wa_id.strip())


def _parse_closed_beta_allowed_wa_ids() -> tuple[str, ...]:
    raw = os.getenv("CLOSED_BETA_ALLOWED_WA_IDS", "").strip()
    if not raw:
        return ()
    return tuple(wa_id.strip() for wa_id in raw.split(",") if wa_id.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        whatsapp_token=_getenv("WHATSAPP_TOKEN"),
        whatsapp_verify_token=_getenv("WHATSAPP_VERIFY_TOKEN"),
        whatsapp_phone_number_id=_getenv("WHATSAPP_PHONE_NUMBER_ID"),
        whatsapp_app_secret=_getenv("WHATSAPP_APP_SECRET"),
        n8n_webhook_url=_getenv("N8N_WEBHOOK_URL"),
        openai_api_key=_getenv("OPENAI_API_KEY"),
        smtp_host=_getenv("SMTP_HOST"),
        smtp_port=int(_getenv("SMTP_PORT", "587")),
        smtp_user=_getenv("SMTP_USER"),
        smtp_password=_getenv("SMTP_PASSWORD"),
        smtp_from=_getenv("SMTP_FROM"),
        resend_api_key=_getenv("RESEND_API_KEY"),
        internal_booking_email_from=_getenv("INTERNAL_BOOKING_EMAIL_FROM", "notificaciones@ridecheck.ar"),
        internal_booking_email_to=_getenv("INTERNAL_BOOKING_EMAIL_TO", "ridecheckassistance@gmail.com"),
        internal_booking_email_reply_to=_getenv("INTERNAL_BOOKING_EMAIL_REPLY_TO", "ridecheckassistance@gmail.com"),
        whatsapp_flow_id=_getenv("WHATSAPP_FLOW_ID"),
        whatsapp_website_flow_id=_getenv("WHATSAPP_WEBSITE_FLOW_ID"),
        whatsapp_vehicle_fallback_flow_id=_getenv("WHATSAPP_VEHICLE_FALLBACK_FLOW_ID"),
        whatsapp_location_fallback_flow_id=_getenv("WHATSAPP_LOCATION_FALLBACK_FLOW_ID"),
        booking_flow_id=_getenv("WHATSAPP_BOOKING_FLOW_ID", "28104222025943520"),
        flow_booking_private_key_path=_getenv("FLOW_BOOKING_PRIVATE_KEY_PATH"),
        shadow_understand_enabled=(_getenv("SHADOW_UNDERSTAND_ENABLED", "false").lower() == "true"),
        shadow_understand_async=(_getenv("SHADOW_UNDERSTAND_ASYNC", "false").lower() == "true"),
        reconciler_vehicle_authority_enabled=(
            _getenv("RECONCILER_VEHICLE_AUTHORITY_ENABLED", "false").lower() == "true"),
        reconciler_location_authority_enabled=(
            _getenv("RECONCILER_LOCATION_AUTHORITY_ENABLED", "false").lower() == "true"),
        reconciler_acceptance_authority_enabled=(
            _getenv("RECONCILER_ACCEPTANCE_AUTHORITY_ENABLED", "false").lower() == "true"),
        reconciler_scheduling_authority_enabled=(
            _getenv("RECONCILER_SCHEDULING_AUTHORITY_ENABLED", "false").lower() == "true"),
        internal_api_auth_enabled=(
            _getenv("INTERNAL_API_AUTH_ENABLED", "false").lower() == "true"),
        internal_api_secret=_getenv("INTERNAL_API_SECRET"),
        semantic_same_turn_enabled=(
            _getenv("SEMANTIC_SAME_TURN_ENABLED", "false").lower() == "true"),
        semantic_same_turn_timeout_seconds=_float_env(
            "SEMANTIC_SAME_TURN_TIMEOUT_SECONDS", 6.0),
        shadow_evidence_path=_getenv("SHADOW_EVIDENCE_PATH"),
        conversation_engine_direct_webhook_enabled=_getenv("CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED", "false").lower() in ("1", "true", "yes"),
        openai_chat_model=_getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
        quarantined_test_wa_ids=_parse_quarantined_wa_ids(),
        closed_beta_allowed_wa_ids=_parse_closed_beta_allowed_wa_ids(),
    )

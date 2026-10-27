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
    # G3-7 — the reply model stops supplying `extracted`, `candidate`, `lead_flag` and
    # `needs_human`; typed evidence supplies them instead.
    # G3-7B flipped the default ON. What the G3-7 closeout was waiting for was a
    # correction-target authority: a customer correcting the car under discussion names no
    # vehicle, and the reconciler had no way to express that, so the demoted path lost the
    # correction (and a bare-year mention created a phantom candidate). That is closed, the
    # 43 cutover-added failures are migrated to typed evidence with every business assertion
    # unchanged, and `REPLY_RENDERER_DEMOTION_ENABLED=false` still restores the legacy
    # source exactly — the rollback is verified, not assumed. See the G3-7B closeout.
    reply_renderer_demotion_enabled: bool = True
    # G3-7H P4. Layer D register-and-continue. Default OFF: flag-off behaviour is
    # byte-compatible with the parent commit, and flag-on is the registration foundation the
    # later reply-phase move consumes. Rendering a secondary FAQ answer ALONGSIDE a
    # clarification Flow still needs that move; see the G3-7H closeout.
    layer_d_register_and_continue_enabled: bool = False
    # W1F-W-01 R3 — locality resolution safety. Default OFF: flag-off behaviour is the
    # deployed 6a14cdb behaviour, byte for byte. On, three things change and nothing else:
    # the declared coverage boundary and the two new locality validators are consulted
    # BEFORE any candidate or thread zone is written, and plural vehicle-location clauses
    # ("los autos están en Flores") are read. A refused locality is never persisted and
    # never reaches PricingService, ScheduleService or BookingAuthority; it falls through
    # to the existing coverage/handoff or location-clarification outcome.
    # ENABLING THIS IS A SEPARATE OWNER CHECKPOINT: with it off, W1FRV-02 and W1FRV-03
    # remain open in the runtime. See the R3 closeout.
    locality_resolution_safety_enabled: bool = False
    # G3-7F — the Grounded-Target Invariant. The reconciler establishes a correction's
    # target from a ground it verifies itself (literal identity, a validated singleton focus,
    # or a typed in-burst reference) instead of trusting the interpreter's confidence. G3-7C
    # and G3-7D measured a model asserting CONFIRMED for a referent the customer excluded,
    # so a status gate alone is not a safety property. Default OFF: the floor changes which
    # turns mutate, and that needs certification against a model first.
    correction_target_grounding_enabled: bool = False
    # G3-7F — provisional correction authority. A price-affecting or identity-changing
    # correction becomes a typed pending patch and waits for the customer to confirm it
    # before canonical state moves. Default OFF: it changes conversational behaviour, and
    # the blocking it implies is only enforced at the deterministic gates by G3-8.
    provisional_correction_enabled: bool = False
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
    # Container-network CIDR whose NON-GATEWAY addresses are trusted as machine callers.
    # The gateway is excluded on purpose: everything arriving through nginx or the
    # published port is SNATed to it, so only a peer container can satisfy this.
    internal_api_trusted_cidr: str = ""
    semantic_same_turn_enabled: bool = False
    semantic_same_turn_timeout_seconds: float = 6.0
    shadow_evidence_path: str = ""
    # L4.7W5 Gate 1/2 — hybrid decision trace. OFF by default everywhere. The writer is
    # observational: it reads state already computed, writes one row of its own, and can
    # never change routing or customer-visible text. Turning it off stops the write and
    # leaves every prior trace readable, which is the whole rollback.
    hybrid_trace_enabled: bool = False
    # SB2B Option B — Conversation Engine booking paths call the booking authority (adapter
    # first, authoritative commit first, CE synchronization second). OFF by default
    # everywhere: with it off CE-W1/CE-W2 keep their legacy behaviour exactly, and nothing
    # writes a booking ledger row from CE. Read with `is True`, never truthiness.
    smart_booking_ce_enabled: bool = False
    # W1-F2 — the operator's WhatsApp notice of a committed customer booking. OFF unless
    # OPERATOR_WHATSAPP_NOTIFY_ENABLED is exactly "true" AND the destination and the approved
    # Meta template are configured and well formed (`operator_notification.operator_config`);
    # anything else fails closed. Runtime configuration only — never a literal in source.
    operator_whatsapp_notify_enabled: bool = False
    operator_whatsapp_to: str = ""
    operator_booking_template_name: str = ""
    operator_booking_template_language: str = ""
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
        reply_renderer_demotion_enabled=(
            _getenv("REPLY_RENDERER_DEMOTION_ENABLED", "true").lower() == "true"),
        layer_d_register_and_continue_enabled=(
            _getenv("LAYER_D_REGISTER_AND_CONTINUE_ENABLED", "false").lower() == "true"),
        locality_resolution_safety_enabled=(
            _getenv("LOCALITY_RESOLUTION_SAFETY_ENABLED", "false").lower() == "true"),
        correction_target_grounding_enabled=(
            _getenv("CORRECTION_TARGET_GROUNDING_ENABLED", "false").lower() == "true"),
        provisional_correction_enabled=(
            _getenv("PROVISIONAL_CORRECTION_ENABLED", "false").lower() == "true"),
        reconciler_scheduling_authority_enabled=(
            _getenv("RECONCILER_SCHEDULING_AUTHORITY_ENABLED", "false").lower() == "true"),
        internal_api_auth_enabled=(
            _getenv("INTERNAL_API_AUTH_ENABLED", "false").lower() == "true"),
        internal_api_secret=_getenv("INTERNAL_API_SECRET"),
        internal_api_trusted_cidr=_getenv("INTERNAL_API_TRUSTED_CIDR"),
        semantic_same_turn_enabled=(
            _getenv("SEMANTIC_SAME_TURN_ENABLED", "false").lower() == "true"),
        semantic_same_turn_timeout_seconds=_float_env(
            "SEMANTIC_SAME_TURN_TIMEOUT_SECONDS", 6.0),
        shadow_evidence_path=_getenv("SHADOW_EVIDENCE_PATH"),
        hybrid_trace_enabled=(_getenv("HYBRID_TRACE_ENABLED", "false").lower() == "true"),
        smart_booking_ce_enabled=(
            _getenv("SMART_BOOKING_CE_ENABLED", "false").lower() == "true"),
        operator_whatsapp_notify_enabled=(
            _getenv("OPERATOR_WHATSAPP_NOTIFY_ENABLED", "false").lower() == "true"),
        operator_whatsapp_to=_getenv("OPERATOR_WHATSAPP_TO"),
        operator_booking_template_name=_getenv("OPERATOR_BOOKING_TEMPLATE_NAME"),
        operator_booking_template_language=_getenv("OPERATOR_BOOKING_TEMPLATE_LANGUAGE"),
        conversation_engine_direct_webhook_enabled=_getenv("CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED", "false").lower() in ("1", "true", "yes"),
        openai_chat_model=_getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
        quarantined_test_wa_ids=_parse_quarantined_wa_ids(),
        closed_beta_allowed_wa_ids=_parse_closed_beta_allowed_wa_ids(),
    )

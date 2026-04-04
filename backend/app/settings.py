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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        whatsapp_token=_getenv("WHATSAPP_TOKEN"),
        whatsapp_verify_token=_getenv("WHATSAPP_VERIFY_TOKEN"),
        whatsapp_phone_number_id=_getenv("WHATSAPP_PHONE_NUMBER_ID"),
        whatsapp_app_secret=_getenv("WHATSAPP_APP_SECRET"),
        n8n_webhook_url=_getenv("N8N_WEBHOOK_URL"),
    )

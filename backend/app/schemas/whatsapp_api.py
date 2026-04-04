from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class WhatsAppThreadOut(BaseModel):
    thread_id: int
    lead_id: int | None = None
    contact_id: int
    wa_id: str
    display_name: str | None = None
    unread_count: int
    last_message_at: datetime | None = None
    last_message_preview: str | None = None
    last_message_id: int | None = None


class WhatsAppThreadLinkIn(BaseModel):
    lead_id: int


class WhatsAppMessageOut(BaseModel):
    id: int
    thread_id: int
    direction: str
    text: str | None = None
    timestamp: datetime | None = None
    status: str | None = None
    wa_message_id: str | None = None


class WhatsAppThreadMessagesOut(BaseModel):
    thread_id: int
    messages: list[WhatsAppMessageOut]


class WhatsAppSendTextIn(BaseModel):
    text: str
    reply_to_message_id: int | None = None


class WhatsAppSendTextOut(BaseModel):
    ok: bool
    thread_id: int
    wa_message_id: str
    text: str


class WhatsAppThreadStateRead(BaseModel):
    id: int | None = None
    thread_id: int
    last_intent: str | None = None
    last_stage: str | None = None
    needs_human: bool = False
    current_focus_candidate_id: int | None = None
    last_processed_inbound_wa_message_id: str | None = None
    customer_name: str | None = None
    home_zone_group: str | None = None
    home_zone_detail: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class WhatsAppThreadStatePatch(BaseModel):
    last_intent: str | None = Field(default=None, max_length=30)
    last_stage: str | None = Field(default=None, max_length=30)
    needs_human: bool | None = None
    current_focus_candidate_id: int | None = None
    last_processed_inbound_wa_message_id: str | None = Field(default=None, max_length=191)
    customer_name: str | None = Field(default=None, max_length=120)
    home_zone_group: str | None = Field(default=None, max_length=50)
    home_zone_detail: str | None = Field(default=None, max_length=80)


class WhatsAppThreadCandidateRead(BaseModel):
    id: int
    thread_id: int
    label: str | None = None
    marca: str | None = None
    modelo: str | None = None
    version_text: str | None = None
    anio: int | None = None
    tipo_vehiculo: str | None = None
    zone_group: str | None = None
    zone_detail: str | None = None
    direccion_texto: str | None = None
    source_text: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WhatsAppThreadCandidateCreate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    marca: str | None = Field(default=None, max_length=50)
    modelo: str | None = Field(default=None, max_length=50)
    version_text: str | None = Field(default=None, max_length=120)
    anio: int | None = None
    tipo_vehiculo: str | None = Field(default=None, max_length=30)
    zone_group: str | None = Field(default=None, max_length=50)
    zone_detail: str | None = Field(default=None, max_length=80)
    direccion_texto: str | None = None
    source_text: str | None = None
    status: str | None = Field(default="mentioned", max_length=30)


class WhatsAppThreadCandidatePatch(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    marca: str | None = Field(default=None, max_length=50)
    modelo: str | None = Field(default=None, max_length=50)
    version_text: str | None = Field(default=None, max_length=120)
    anio: int | None = None
    tipo_vehiculo: str | None = Field(default=None, max_length=30)
    zone_group: str | None = Field(default=None, max_length=50)
    zone_detail: str | None = Field(default=None, max_length=80)
    direccion_texto: str | None = None
    source_text: str | None = None
    status: str | None = Field(default=None, max_length=30)

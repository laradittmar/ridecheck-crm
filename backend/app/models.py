# app/models.py
from __future__ import annotations

from datetime import datetime, date, time
from typing import Optional

from sqlalchemy import (
    String,
    Integer,
    DateTime,
    ForeignKey,
    CheckConstraint,
    Index,
    Boolean,
    Text,
    Date,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # pipeline
    estado: Mapped[str] = mapped_column(String(40), default="CONSULTA_NUEVA")
    motivo_perdida: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    flag: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # contacto
    nombre: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    apellido: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    telefono: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # employee fields (lead-level)
    canal: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    compro_el_auto: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # "SI"/"NO"

    necesita_humano: Mapped[bool] = mapped_column(Boolean, default=False)

    # relationships
    revisions: Mapped[list["Revision"]] = relationship(
        "Revision",
        back_populates="lead",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    feedback: Mapped[Optional["FeedbackPostRevision"]] = relationship(
        "FeedbackPostRevision",
        back_populates="lead",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Profesional(Base):
    __tablename__ = "profesionales"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    apellido: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(120))
    telefono: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    cargo: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    revisions: Mapped[list["Revision"]] = relationship("Revision", back_populates="profesional")


class Vendedor(Base):
    __tablename__ = "vendedores"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    agencias: Mapped[list["Agencia"]] = relationship("Agencia", back_populates="vendedor")


class Agencia(Base):
    __tablename__ = "agencias"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre_agencia: Mapped[str] = mapped_column(String(120))
    direccion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    gmaps: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mail: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    vendedor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vendedores.id"), nullable=True)
    telefono: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    fecha_subido: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    vendedor: Mapped[Optional["Vendedor"]] = relationship("Vendedor", back_populates="agencias")
    revisions: Mapped[list["Revision"]] = relationship("Revision", back_populates="agencia")


class Revision(Base):
    __tablename__ = "revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"),
        index=True,
    )
    profesional_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("profesionales.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # --- VEHICULO ---
    # AUTO / SUV_4X4_DEPORTIVO / CLASICO / ESCANEO_MOTOR / MOTO
    tipo_vehiculo: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    marca: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    modelo: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    anio: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    link_compra: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    presupuesto_compra: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vendedor_tipo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # AGENCIA / PARTICULAR / REVENTA
    tipo_vendedor: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # PARTICULAR / AGENCIA
    agencia_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agencias.id"), nullable=True)
    compro: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)  # SI / NO / OFRECIDO
    resultado_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    comision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cobrado: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)  # SI / NO
    fecha_cobro: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # --- ZONA / DIRECCION (for the revision) ---
    zone_group: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    zone_detail: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    direccion_texto: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    link_maps: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    direccion_estado: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # --- PRESUPUESTO / PAGO ---
    precio_base: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    viaticos: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    precio_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    pago: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    medio_pago: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # EFECTIVO, SANTANDER, etc.

    # --- TURNO / CALENDARIO ---
    turno_fecha: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    turno_hora: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    cliente_presente: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    turno_notas: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- REVISION PIPELINE ---
    estado_revision: Mapped[str] = mapped_column(String(20), default="PENDIENTE")
    resultado: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    motivo_rechazo: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    lead: Mapped["Lead"] = relationship("Lead", back_populates="revisions")
    profesional: Mapped[Optional["Profesional"]] = relationship(
        "Profesional",
        back_populates="revisions",
    )
    agencia: Mapped[Optional["Agencia"]] = relationship("Agencia", back_populates="revisions")


class ViaticosZone(Base):
    __tablename__ = "viaticos_zones"
    __table_args__ = (
        UniqueConstraint("zone_group", "zone_detail", name="uq_viaticos_zone_group_detail"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    zone_group: Mapped[str] = mapped_column(String(50), index=True)
    zone_detail: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    viaticos: Mapped[int] = mapped_column(Integer)


class FeedbackPostRevision(Base):
    __tablename__ = "feedback_post_revision"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # (keep it simple for now; you can add QA answers later)
    done: Mapped[bool] = mapped_column(Boolean, default=False)

    lead: Mapped["Lead"] = relationship("Lead", back_populates="feedback")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class WhatsAppContact(Base):
    __tablename__ = "whatsapp_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    wa_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    threads: Mapped[list["WhatsAppThread"]] = relationship(
        "WhatsAppThread",
        back_populates="contact",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class WhatsAppThread(Base):
    __tablename__ = "whatsapp_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_name_override: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    # Kept as plain nullable int in ORM; migration adds FK to leads conditionally when leads table exists.
    lead_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    contact: Mapped["WhatsAppContact"] = relationship("WhatsAppContact", back_populates="threads")
    state: Mapped[Optional["WhatsAppThreadState"]] = relationship(
        "WhatsAppThreadState",
        back_populates="thread",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    candidates: Mapped[list["WhatsAppThreadCandidate"]] = relationship(
        "WhatsAppThreadCandidate",
        back_populates="thread",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    thread_revisions: Mapped[list["ThreadRevision"]] = relationship(
        "ThreadRevision",
        back_populates="thread",
        passive_deletes=True,
    )
    messages: Mapped[list["WhatsAppMessage"]] = relationship(
        "WhatsAppMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class WhatsAppThreadState(Base):
    __tablename__ = "whatsapp_thread_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("whatsapp_threads.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    last_intent: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    last_stage: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    needs_human: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    current_focus_candidate_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_processed_inbound_wa_message_id: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)
    customer_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    home_zone_group: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    home_zone_detail: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    thread: Mapped["WhatsAppThread"] = relationship("WhatsAppThread", back_populates="state")


class WhatsAppThreadCandidate(Base):
    __tablename__ = "whatsapp_thread_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("whatsapp_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    marca: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    modelo: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    version_text: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    anio: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tipo_vehiculo: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    zone_group: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    zone_detail: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    direccion_texto: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="mentioned", server_default="mentioned")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    thread: Mapped["WhatsAppThread"] = relationship("WhatsAppThread", back_populates="candidates")
    thread_revisions: Mapped[list["ThreadRevision"]] = relationship(
        "ThreadRevision",
        back_populates="candidate",
        passive_deletes=True,
    )


class ThreadRevision(Base):
    __tablename__ = "thread_revisions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'collecting_data', 'booked', 'completed')",
            name="ck_thread_revisions_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("whatsapp_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("whatsapp_thread_candidates.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="collecting_data", server_default="collecting_data")
    buyer_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    buyer_phone: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    buyer_email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    seller_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    seller_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scheduled_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    scheduled_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    tipo_vehiculo: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    marca: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    modelo: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    anio: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    publication_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    thread: Mapped["WhatsAppThread"] = relationship("WhatsAppThread", back_populates="thread_revisions")
    candidate: Mapped[Optional["WhatsAppThreadCandidate"]] = relationship(
        "WhatsAppThreadCandidate",
        back_populates="thread_revisions",
    )


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        CheckConstraint("direction IN ('in', 'out')", name="ck_whatsapp_messages_direction"),
        CheckConstraint(
            "status IN ('received', 'sent', 'delivered', 'read', 'failed')",
            name="ck_whatsapp_messages_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("whatsapp_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    wa_message_id: Mapped[Optional[str]] = mapped_column(String(191), nullable=True, unique=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received", server_default="received")
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    thread: Mapped["WhatsAppThread"] = relationship("WhatsAppThread", back_populates="messages")


class AiEvent(Base):
    __tablename__ = "ai_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="inbound_message",
        server_default="inbound_message",
    )
    thread_id: Mapped[int] = mapped_column(Integer, nullable=False)
    wa_message_id: Mapped[str] = mapped_column(String(191), nullable=False, unique=True)
    wa_id: Mapped[str] = mapped_column(String(80), nullable=False)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


Index("ix_whatsapp_contacts_wa_id", WhatsAppContact.wa_id)
Index("ix_whatsapp_threads_contact_id", WhatsAppThread.contact_id)
Index("ix_whatsapp_threads_lead_id", WhatsAppThread.lead_id)
Index("ix_whatsapp_thread_states_thread_id", WhatsAppThreadState.thread_id, unique=True)
Index("ix_whatsapp_thread_candidates_thread_id", WhatsAppThreadCandidate.thread_id)
Index("ix_thread_revisions_thread_id", ThreadRevision.thread_id)
Index("ix_thread_revisions_candidate_id", ThreadRevision.candidate_id)
Index("ix_whatsapp_messages_thread_id_timestamp", WhatsAppMessage.thread_id, WhatsAppMessage.timestamp)
Index("ix_whatsapp_messages_wa_message_id", WhatsAppMessage.wa_message_id, unique=True)
Index("ix_ai_events_thread_id", AiEvent.thread_id)
Index("ix_ai_events_wa_message_id", AiEvent.wa_message_id, unique=True)

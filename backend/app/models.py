# app/models.py
from __future__ import annotations

from datetime import datetime, date, time
from typing import Optional

from sqlalchemy import (
    String,
    Integer,
    BigInteger,
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .services.booking_representation import (
    APPOINTMENT_TIMEZONE,
    BUFFER_MINUTES_DEFAULT,
    SERVICE_MINUTES_DEFAULT,
)


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

    # marketing attribution (first-touch, from website tracking.js)
    ref_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    rc_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, index=True)

    # M21.4A canonical attribution
    acq_source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    inbound_channel: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    necesita_humano: Mapped[bool] = mapped_column(Boolean, default=False)
    buscando_auto_set_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

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
    """An inspector — and, since SB1, the agenda resource an appointment occupies.

    OD-01: one canonical professional at launch, keyed by `resource_key`
    (`booking_representation.LAUNCH_PROFESSIONAL_KEY`) and marked `is_launch_default`. The
    database allows at most one default; a writer that names no professional for an
    occupying revision is attributed to it (trigger in `20261007_sb1_booking_repr`), or
    refused if none is designated. More professionals can exist; nothing allocates between
    them yet.
    """
    __tablename__ = "profesionales"
    __table_args__ = (
        UniqueConstraint("resource_key", name="uq_profesionales_resource_key"),
        CheckConstraint("NOT is_launch_default OR resource_key IS NOT NULL",
                        name="ck_profesionales_launch_default_has_key"),
        Index("uq_profesionales_single_launch_default", "is_launch_default", unique=True,
              postgresql_where=text("is_launch_default"),
              sqlite_where=text("is_launch_default")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    apellido: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(120))
    telefono: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    cargo: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # ── SB1: resource identity ──
    resource_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_launch_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"))
    # SB2A: eligibility. An inactive professional is never assigned a new booking.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"))

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
    """A CRM revision; when scheduled, an appointment on the agenda.

    SB1 interval: `[turno_fecha + turno_hora, + service_minutes + buffer_minutes)`, half-open,
    Buenos Aires wall-clock (`turno_timezone`). On PostgreSQL two STORED generated columns —
    `agenda_occupying` and `occupied_during` (tsrange) — derive it from the columns below and
    an exclusion constraint forbids overlap per `profesional_id`. They are deliberately not
    mapped here: they are database-computed, PostgreSQL-only, and read by SQL when needed.
    `services/booking_representation.py` mirrors them for code that runs without PostgreSQL.
    """
    __tablename__ = "revisions"
    __table_args__ = (
        CheckConstraint("service_minutes > 0", name="ck_revisions_service_minutes_positive"),
        CheckConstraint("buffer_minutes >= 0", name="ck_revisions_buffer_minutes_nonneg"),
        CheckConstraint("booking_version >= 0", name="ck_revisions_booking_version_nonneg"),
        CheckConstraint(f"turno_timezone = '{APPOINTMENT_TIMEZONE}'",
                        name="ck_revisions_turno_timezone"),
    )

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
    # SB1 (OD-03/04): the two parts of the occupied interval, stored separately. Travel is
    # additional and never included.
    service_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=SERVICE_MINUTES_DEFAULT,
        server_default=text(str(SERVICE_MINUTES_DEFAULT)))
    buffer_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=BUFFER_MINUTES_DEFAULT,
        server_default=text(str(BUFFER_MINUTES_DEFAULT)))
    # SB1: the interpretation turno_fecha/turno_hora have always had, now stated.
    turno_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default=APPOINTMENT_TIMEZONE,
        server_default=text(f"'{APPOINTMENT_TIMEZONE}'"))
    # SB2A: optimistic-concurrency version of the booking (slot, durations, status,
    # professional). The booking authority bumps it; a PostgreSQL trigger bumps it for any
    # writer that does not, so a stale edit can be detected whoever made the newer one.
    booking_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))

    # --- REVISION PIPELINE ---
    estado_revision: Mapped[str] = mapped_column(String(20), default="PENDIENTE")
    resultado: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    motivo_rechazo: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    appointment_approval_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    appointment_approval_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    appointment_approval_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    appointment_approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    lead: Mapped["Lead"] = relationship("Lead", back_populates="revisions")
    profesional: Mapped[Optional["Profesional"]] = relationship(
        "Profesional",
        back_populates="revisions",
    )
    agencia: Mapped[Optional["Agencia"]] = relationship("Agencia", back_populates="revisions")


class CalendarException(Base):
    """SB1 (OD-05): an operator-recorded closure or unavailable period.

    No external holiday source exists or is consulted; normal weekday rules apply unless a
    row here says otherwise. `CLOSED_DATE` closes a whole business date;
    `UNAVAILABLE_INTERVAL` removes `[start_time, end_time)` from it. Times are Buenos Aires
    wall-clock, like the agenda. `profesional_id` NULL means the whole business.

    Representation only: in SB1 nothing reads this table, so availability is unchanged.
    The schedule authority consumes it in a later milestone.
    """
    __tablename__ = "calendar_exceptions"
    __table_args__ = (
        CheckConstraint("kind IN ('CLOSED_DATE', 'UNAVAILABLE_INTERVAL')",
                        name="ck_calendar_exceptions_kind"),
        CheckConstraint(
            "(kind = 'CLOSED_DATE' AND start_time IS NULL AND end_time IS NULL) OR "
            "(kind = 'UNAVAILABLE_INTERVAL' AND start_time IS NOT NULL "
            "AND end_time IS NOT NULL AND start_time < end_time)",
            name="ck_calendar_exceptions_shape"),
        CheckConstraint(f"timezone = '{APPOINTMENT_TIMEZONE}'",
                        name="ck_calendar_exceptions_timezone"),
        Index("ix_calendar_exceptions_date", "exception_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    exception_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default=APPOINTMENT_TIMEZONE,
        server_default=text(f"'{APPOINTMENT_TIMEZONE}'"))
    profesional_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("profesionales.id"), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: The operator identity as the session knows it (`auth.build_session`); `users` is not
    #: the login authority, so this is a reference, not a foreign key.
    created_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class BookingCommandRecord(Base):
    """SB2A: one row per committed booking command that carried an idempotency key.

    Written in the same transaction as the booking it records, so the ledger and the agenda
    can never disagree. `UNIQUE (idempotency_key)` is the final duplicate authority.
    Acknowledgement state is deliberately separate from commit state.
    """
    __tablename__ = "booking_commands"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_booking_commands_idempotency_key"),
        CheckConstraint(
            "source IN ('WHATSAPP_FLOW', 'CRM', 'API', 'CONVERSATION_ENGINE', 'OTHER')",
            name="ck_booking_commands_source"),
        CheckConstraint(
            "operation IN ('CREATE', 'RESCHEDULE', 'CANCEL', 'MARK_FOR_RESCHEDULE', "
            "'SET_STATUS', 'UNSCHEDULE', 'REQUEST_MANUAL_SCHEDULING')",
            name="ck_booking_commands_operation"),
        CheckConstraint(
            "ack_status IN ('NOT_APPLICABLE', 'PENDING', 'SENDING', 'SENT', 'FAILED')",
            name="ck_booking_commands_ack_status"),
        # Convergence Phase 2 (RV03RV-05): the command's own lifecycle, separate from its
        # acknowledgement. DETACHED is terminal and its SHAPE is enforced here: no live
        # booking pointer, no reusable id, no slot and not occupying, with a reason and a
        # time. A COMMITTED row carries no detachment fields at all.
        CheckConstraint("command_state IN ('COMMITTED', 'DETACHED')",
                        name="ck_booking_commands_command_state"),
        CheckConstraint(
            "(command_state = 'COMMITTED' AND detached_at IS NULL AND detached_reason IS NULL "
            "AND detached_history IS NULL) OR "
            "(command_state = 'DETACHED' AND detached_at IS NOT NULL "
            "AND detached_reason IS NOT NULL AND revision_id IS NULL "
            "AND thread_revision_id IS NULL AND lead_id IS NULL AND thread_id IS NULL "
            "AND profesional_id IS NULL AND slot_start IS NULL AND slot_end IS NULL "
            "AND occupying = false)",
            name="ck_booking_commands_detached_shape"),
        Index("ix_booking_commands_revision_id", "revision_id"),
        Index("ix_booking_commands_thread_revision_id", "thread_revision_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    revision_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("revisions.id", ondelete="SET NULL"), nullable=True)
    thread_revision_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("thread_revisions.id", ondelete="SET NULL"), nullable=True)
    lead_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    thread_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    profesional_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    slot_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    slot_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    occupying: Mapped[bool] = mapped_column(Boolean, nullable=False)
    booking_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    actor: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    ack_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="NOT_APPLICABLE",
        server_default=text("'NOT_APPLICABLE'"))
    ack_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    ack_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    replay_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"))
    last_replayed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # SB2A-R2: the acknowledgement claim is a bounded lease with an owner.
    ack_claim_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    ack_claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ack_lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    ack_transport_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    ack_last_error: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ack_duplicate_risk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"))
    # SB2B: what happened to the submitted person identity (ADMITTED | FILLED | UNCHANGED |
    # CONFLICT), written in the booking's own transaction; NULL when none was submitted or
    # for rows committed before SB2B.
    identity_outcome: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # Convergence Phase 2 (RV03RV-05). COMMITTED for every row the authority writes;
    # DETACHED (terminal) once a tester reset removed the business rows it recorded. A
    # DETACHED row keeps its key CONSUMED and never replays (`BookingAuthority.lookup`).
    command_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="COMMITTED", server_default=text("'COMMITTED'"))
    detached_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    detached_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: HISTORICAL, NON-AUTHORITATIVE JSON written at detachment: the slot and the ids the row
    #: named before the reset. Never joined, never read by replay, admission or a customer
    #: path — the authoritative columns above are NULL so nothing joinable points at a reused id.
    detached_history: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class OperatorNotification(Base):
    """W1-F2: the operator's WhatsApp notice of one committed customer booking.

    One row per booking command, never more: `UNIQUE (booking_command_id)` and
    `UNIQUE (identity)` are the duplicate authority, so a Flow resubmission, an idempotent
    replay or a provider callback can never create a second notice. The committed ledger row is
    the durable trigger (the booking's own transaction wrote it); this row is written only
    after that commit, by `services.operator_notification`.

    `status`: PENDING (owed; `next_attempt_at` says when) → SENDING (one claimed attempt) →
    SENT, or terminal FAILED (two definite failures) / HELD (never sent: not configured, no
    summary, or an attempt whose outcome is unknown and is therefore never repeated). FAILED
    and HELD are OPERATOR_NOTIFICATION_REQUIRED on `/api/ops/booking-attention`.
    The `hist_*` ids are history, not foreign keys: a tester reset may delete what they name.
    """
    __tablename__ = "operator_notifications"
    __table_args__ = (
        UniqueConstraint("booking_command_id", name="uq_operator_notifications_command"),
        UniqueConstraint("identity", name="uq_operator_notifications_identity"),
        CheckConstraint("kind IN ('BOOKING_CREATED')", name="ck_operator_notifications_kind"),
        CheckConstraint("status IN ('PENDING', 'SENDING', 'SENT', 'FAILED', 'HELD')",
                        name="ck_operator_notifications_status"),
        CheckConstraint("attempts >= 0 AND attempts <= 2",
                        name="ck_operator_notifications_attempts"),
        Index("ix_operator_notifications_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    booking_command_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), ForeignKey("booking_commands.id"),
        nullable=False)
    identity: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                          server_default=text("0"))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_hash: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    template_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    template_language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)
    outbound_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    claim_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True),
                                                                 nullable=True)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True),
                                                                nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    hist_thread_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hist_lead_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hist_revision_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hist_thread_revision_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


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


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ExcludedPhone(Base):
    __tablename__ = "excluded_phones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


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
    latest_inbound_wa_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # M21.4A canonical attribution
    inbound_channel: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    ctwa_source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ctwa_source_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ctwa_source_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

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
    current_revision_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_processed_inbound_wa_message_id: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)
    customer_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    home_zone_group: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    home_zone_detail: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    preferred_day: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    preferred_time: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    active_requested_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    last_requested_time: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    last_offered_slots: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_visible_slots: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_website_lead: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    flow_booking_token: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    vehicle_clarification_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    location_clarification_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    vehicle_fallback_flow_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    location_fallback_flow_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    inspectability_clarification_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    pending_fuzzy_catalog_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)  # M21.1.4: "{marca}||{modelo}" or NULL
    pending_turn_evidence_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # L4.7W2-F1: "zone_group||zone_detail||cycle_id" for an APPROXIMATE locality awaiting
    # customer confirmation. The cycle is embedded so a proposal can never be confirmed in
    # a later cycle; nothing canonical is written until the customer says yes.
    pending_location_proposal: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)  # M21.2: raw current_turn_text from the CONFIRM turn; cleared atomically with pending_fuzzy_catalog_key
    # G3-7F: the thread's one provisional canonical patch, awaiting customer confirmation.
    # PROPOSED state only — `whatsapp_thread_candidates` stays the source of truth and
    # `_apply_candidate` stays its only writer. One column on a one-row-per-thread table is
    # what enforces "at most one outstanding proposal", which is what makes a typed
    # affirmation unambiguous instead of a guess.
    pending_correction: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    unanswered_alert_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # WILD-04R: cycle boundary fields
    cycle_reset_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    current_cycle_start_message_db_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_cycle_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    quote_followup_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    buscando_followup_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
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


#: R2/RV-04. The ONE name for the booking projection's foreign key. Declared beside the
#: model and imported by the migration, so the two cannot drift again.
BOOKING_PROJECTION_FK = "fk_thread_revisions_offer_id"


class ThreadRevision(Base):
    __tablename__ = "thread_revisions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'collecting_data', 'booked', 'completed', 'provisional', "
            "'cancelled', 'reschedule_pending')",
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
    #: G3-7R/S1. WHICH booking offer authorized this booking, and — through the partial
    #: unique index declared below — the database's guarantee that one offer can authorize
    #: at most ONE of them.
    #:
    #: Nullable and never backfilled. A booking that predates this column, or one written by
    #: the authenticated operator/machine endpoint rather than by a customer accepting an
    #: offer, legitimately has no offer behind it; NULL is the honest value and the index is
    #: partial so any number of those may coexist. `ondelete` is deliberately absent: an
    #: offer row is history and is never deleted, and if one ever were, losing the link
    #: silently would destroy the audit trail the whole table exists for.
    #: R2/RV-04. The constraint is NAMED, and named the same thing the migration names it.
    #: Left implicit, SQLAlchemy generated `thread_revisions_offer_id_fkey` while the
    #: migration created `fk_thread_revisions_offer_id`, so a database built by
    #: `create_all` and one built by Alembic disagreed about the identifier — and
    #: `downgrade()` drops by the migration's name. Same defect class as SAA-F10.
    offer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("whatsapp_booking_offers.id", name=BOOKING_PROJECTION_FK),
        nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="collecting_data", server_default="collecting_data")
    buyer_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    buyer_phone: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    buyer_email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    seller_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    seller_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scheduled_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    scheduled_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    zone_group: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tipo_vehiculo: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    marca: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    modelo: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    anio: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    publication_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    appointment_approval_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    appointment_approval_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    appointment_approval_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    appointment_approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    #: M18: when the operator booking e-mail for this booking was delivered — its only dedup
    #: and the SENT leg of the operator notification surface. Convergence Phase 4-R1
    #: (P4RV-04): DECLARED schema, owned by this model and migration
    #: `20261016_notification_sent_at`; it used to exist only because an application
    #: start-up hook ran `ALTER TABLE … ADD COLUMN IF NOT EXISTS`.
    notification_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
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


#: Convergence Phase 4 — the stage and outcome vocabularies of a booking-attempt event.
BOOKING_ATTEMPT_STAGES = ("PRE_EXECUTE", "AUTHORITY", "FINALISE", "NOTIFICATION")
BOOKING_ATTEMPT_OUTCOMES = ("REFUSED", "RETRYABLE", "CONTENDED", "INTERNAL", "FAILED", "HELD")


class BookingAttemptEvent(Base):
    """Convergence Phase 4 — durable, append-only EVIDENCE of a booking attempt that produced
    no committed command (a refusal, a contention, an internal failure) and of an operator
    notification that was not delivered. Never authority.

    * No foreign key, by design: an FK to an offer would take `FOR KEY SHARE` on a row a
      contender holds `FOR UPDATE` (the very wait this must not have), and an FK to any
      business row would block a tester reset or a deletion. It survives both.
    * The `hist_*` ids are HISTORICAL and NON-AUTHORITATIVE: scalar copies of what the
      attempt referred to at the time, never joined for a decision, possibly reused later.
    * Nothing here is a raw Flow token, a raw command key, a phone number, customer text, a
      secret or a model payload: keys and attempt references are stored only as 16-hex
      SHA-256 prefixes, reasons only as typed codes.
    * Append-only: the application never updates or deletes a row (PostgreSQL enforces it
      with a trigger). `event_uid` is the idempotency rule — see
      `services.booking_attempt_events.event_uid`.
    * Read by nobody on a decision path: not admission, replay, locking, pricing,
      availability or any customer response.
    """
    __tablename__ = "booking_attempt_events"
    __table_args__ = (
        CheckConstraint("stage IN ('PRE_EXECUTE', 'AUTHORITY', 'FINALISE', 'NOTIFICATION')",
                        name="ck_booking_attempt_events_stage"),
        CheckConstraint("outcome IN ('REFUSED', 'RETRYABLE', 'CONTENDED', 'INTERNAL', "
                        "'FAILED', 'HELD')", name="ck_booking_attempt_events_outcome"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    event_uid: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    command_key_hash: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    attempt_ref_hash: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    hist_thread_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hist_offer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hist_revision_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hist_thread_revision_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        CheckConstraint("direction IN ('in', 'out')", name="ck_whatsapp_messages_direction"),
        CheckConstraint(
            "status IN ('received', 'pending', 'sent', 'delivered', 'read', 'failed', 'blocked')",
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
    message_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="text", server_default="text")
    media_id: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received", server_default="received")
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Safety gate fields — populated for automated bot sends only.
    automated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    content_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    blocked_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # M2: authorized path tracking
    path_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    deployment_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # L4.1: Meta transport error capture — survives container recreation
    meta_http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    meta_error_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    thread: Mapped["WhatsAppThread"] = relationship("WhatsAppThread", back_populates="messages")


class WhatsAppOutboundDedup(Base):
    """One row per automated outbound send attempt, used for rolling-window dedup.

    The rolling dedup SELECT checks for rows with the same (wa_id, message_kind,
    content_fingerprint) created within the last 10 minutes, allowing concurrent
    sends to race on the application side before the Meta API call.

    Keyed by wa_id (not thread_id) so that duplicates are caught even when the
    same WhatsApp contact has more than one CRM thread.

    thread_id is stored for audit/debug but is not part of the dedup key.

    Rows older than ~1 day can be purged with:
      DELETE FROM whatsapp_outbound_dedup WHERE created_at < NOW() - INTERVAL '1 day'
    """

    __tablename__ = "whatsapp_outbound_dedup"
    __table_args__ = ()

    id: Mapped[int] = mapped_column(primary_key=True)
    wa_id: Mapped[str] = mapped_column(String(80), nullable=False)
    thread_id: Mapped[int] = mapped_column(Integer, nullable=False)
    message_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="text", server_default="text")
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    causal_inbound_wa_message_id: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)


class WhatsAppRecipientLock(Base):
    """One row per wa_id while an automated send is in-flight.

    The presence of a row (enforced by the UNIQUE constraint on wa_id) is an
    advisory lock that serialises concurrent automated sends to the same contact.
    The row is inserted before the Meta API call and deleted after it completes
    (or fails).
    """

    __tablename__ = "whatsapp_recipient_locks"

    id: Mapped[int] = mapped_column(primary_key=True)
    wa_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


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
    # WILD-04R observability fields
    reply_required: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    reply_produced: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    alert_eligible: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    action: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    answer_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ai_invoked: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    contributing_sources: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latency_total_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_debounce_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ce_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    burst_message_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cycle_message_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    performance_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    unanswered_alert_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class HybridDecisionTraceRow(Base):
    """L4.7W5 Gate 1 — one hybrid decision per customer turn, `hybrid-decision-trace/1.0`.

    Additive and observational: nothing in the conversation path reads it. The indexed
    columns are the ones an operator filters on; everything else lives in `payload` so the
    contract can grow a field without a migration. The customer's message bodies are NOT
    here — `payload.ordered_message_ids` points at `whatsapp_messages`, which already holds
    them, and duplicating them would duplicate PII for no evidentiary gain.
    """
    __tablename__ = "hybrid_decision_traces"

    id: Mapped[int] = mapped_column(primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    thread_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    lead_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    deployment_sha: Mapped[Optional[str]] = mapped_column(String(40), index=True, nullable=True)
    input_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    result_kind: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    classification: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    semantic_status: Mapped[Optional[str]] = mapped_column(String(16), index=True, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)


class SecurityEvent(Base):
    """Unauthorized outbound path detection record — M2.

    Created whenever the OutboundSafetyGate or status webhook handler detects
    an attempt that does not conform to the authorized path registry.
    Every HIGH/BLOCKER event triggers an email alert via SMTP.
    """

    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    path_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    source_component: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    wamid: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)
    wa_id_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    deployment_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    thread_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    alert_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


Index("ix_security_events_detected_at", SecurityEvent.detected_at)
Index("ix_security_events_event_type", SecurityEvent.event_type)
Index("ix_security_events_wamid", SecurityEvent.wamid)

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
Index("ix_recipient_locks_wa_id", WhatsAppRecipientLock.wa_id, unique=True)
class WhatsAppClarificationAttempt(Base):
    """One row per clarification Flow attempt, per thread, per cycle, per family.

    G3-7F clarification lifecycle. The project tried three times to bound clarification in
    application code and failed three times the same way: the fact "this family already had
    its attempt" was stored in the same boolean as "a Flow is outstanding", written from four
    call sites across three authorities, and cleared by completion, by human handoff and by a
    withdrawal loop. A customer who submitted the Vehicle Flow with a blank make and model
    was asked again, and again, without bound.

    The invariant is a database constraint here, not a convention:

        UNIQUE (thread_id, cycle_key, family)

    Reservation is an INSERT ... ON CONFLICT DO NOTHING. A conflict is the answer, not an
    error: this family has already had its one attempt in this cycle. The constraint holds
    against every caller, including ones not yet written — which is the whole reason the
    authority moved to the database.

    The row's EXISTENCE is immutable attempt history and is never deleted in normal
    operation; `status` advances along the lifecycle. A new cycle produces a new `cycle_key`
    and therefore a new row, leaving the old one as history.

    `whatsapp_thread_states.vehicle_fallback_flow_sent` / `location_fallback_flow_sent` keep
    exactly one meaning now — a Flow proposal is currently outstanding — and may be cleared
    freely by completion, withdrawal, handoff, expiry or reset, because availability no
    longer lives there.
    """

    __tablename__ = "whatsapp_clarification_attempts"
    __table_args__ = (
        UniqueConstraint("thread_id", "cycle_key", "family",
                         name="uq_clarification_attempt_thread_cycle_family"),
        UniqueConstraint("idempotency_key",
                         name="uq_clarification_attempt_idempotency_key"),
        CheckConstraint("family IN ('VEHICLE', 'LOCATION')",
                        name="ck_clarification_attempt_family"),
        CheckConstraint(
            "status IN ('RESERVED', 'DISPATCHED', 'COMPLETED_RESOLVED', "
            "'COMPLETED_UNRESOLVED', 'INVALID_COMPLETION', 'DISPATCH_BLOCKED', "
            "'DISPATCH_FAILED', 'HANDED_OFF')",
            name="ck_clarification_attempt_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("whatsapp_threads.id"), nullable=False)
    #: `COALESCE(state.current_cycle_start_message_db_id, 0)`. An integer message id or 0 for
    #: the thread's initial, never-reset cycle. Never a timestamp: the identity must be
    #: stable across bursts, duplicates and restarts, and a clock is none of those.
    cycle_key: Mapped[int] = mapped_column(Integer, nullable=False)
    family: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Deterministic UUIDv5 over (namespace, thread, cycle, family). 36 characters, which is
    #: exactly what `whatsapp_messages.correlation_id` and `security_events.correlation_id`
    #: accept, so it can be carried there without truncation if ever needed. The composite
    #: UNIQUE above is the real authority; this exists for correlation and joining.
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    outbound_message_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("whatsapp_messages.id"), nullable=True)
    wa_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    dispatch_result: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    completion_result: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    resolved_family: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    terminal_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)


class WhatsAppBookingOffer(Base):
    """One row per booking offer ever dispatched. THE authority on whether a submitted
    Flow may become a booking (G3-7P).

    Before this table, `whatsapp_thread_states.flow_booking_token` was both the offer and
    the authority, and the G3-7N independent review proved that cannot work. The booking
    path's check was `if expected and flow_token and flow_token != expected`, so a NULL
    `expected` short-circuited it — and NULL is the resting state of that column after six
    different production writes (consumption, cycle reset, rejection handoff, motorcycle
    handoff, the text-path correction withdrawal, and booking_flow_service's own
    consumption). Measured consequence: with `expected IS NULL` the path accepted the old
    consumed token, a fabricated token, an empty token and NO TOKEN AT ALL, each creating a
    ThreadRevision and a CRM Revision. G3-7N's tombstone guarded one of those six doors.

    A row's EXISTENCE is the evidence that we offered something, and its `status` is the
    only thing that may authorize a booking. Absence of a row is therefore a refusal, never
    a permission: `claim()` fails closed.

    Three database-enforced invariants, all of which hold against callers not yet written:

        UNIQUE (token_fingerprint)                      one token -> at most one offer
        UNIQUE (thread_id) WHERE status = 'ISSUED'      at most one LIVE offer per thread
        UNIQUE (thread_revisions.offer_id)              one offer -> at most one BOOKING
               WHERE offer_id IS NOT NULL

    The second is what makes "issue supersedes the previous offer" a fact rather than a
    convention, and it is why two concurrent issues cannot both leave a live offer behind.

    The third was added by G3-7R and it replaces a claim this docstring used to make.
    Acceptance being "a conditional UPDATE off 'ISSUED' under a row lock" was described here
    as if it guaranteed at most one acceptance per offer. It does not: the G3-7P-R3A
    independent review removed both of those application guards and measured TWO bookings
    from ONE offer in 6 of 6 trials, at two different and genuinely free slots, with the
    whole test suite still identity-identical. The guards remain — they are what makes the
    race a typed refusal instead of an exception — but the GUARANTEE now lives on the
    booking's own `offer_id`, where a constraint can hold it.

    The raw token is NEVER stored. `token_fingerprint` is sha256(token) — enough to match a
    submission, useless to replay elsewhere, and safe to render in the Inspector.

    The bindings are stored because a booking must be refused when the thing being booked
    has moved, not merely when someone remembered to withdraw: `candidate_id`, the zone pair
    and `tipo_vehiculo` are the quote inputs (`PricingService.quote(db, tipo_vehiculo,
    zone_group, zone_detail)`), and `offered_date` / `offered_time` are what the customer was
    actually shown.
    """

    __tablename__ = "whatsapp_booking_offers"
    __table_args__ = (
        UniqueConstraint("token_fingerprint", name="uq_booking_offer_token_fingerprint"),
        CheckConstraint(
            "status IN ('ISSUED', 'ACCEPTED', 'WITHDRAWN', 'SUPERSEDED', 'EXPIRED')",
            name="ck_booking_offer_status"),
        #: A live offer has no terminal timestamp, and a terminal one must have it. This is
        #: what stops a half-applied transition from reading as "still live".
        CheckConstraint("(status = 'ISSUED') = (terminal_at IS NULL)",
                        name="ck_booking_offer_terminal_at"),
        CheckConstraint("status = 'ACCEPTED' OR accepted_at IS NULL",
                        name="ck_booking_offer_accepted_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("whatsapp_threads.id"), nullable=False, index=True)
    #: `COALESCE(state.current_cycle_start_message_db_id, 0)` — the same cycle identity the
    #: clarification ledger uses, so an offer from a closed cycle is recognisable as such.
    cycle_key: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── what was being booked when the offer was made ────────────────────────
    candidate_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("whatsapp_thread_candidates.id"), nullable=True)
    tipo_vehiculo: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    zone_group: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    zone_detail: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    offered_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    offered_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)

    # ── identity of the offer itself ─────────────────────────────────────────
    flow_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    contract_version: Mapped[str] = mapped_column(String(24), nullable=False)
    #: sha256(token) hex. The raw token is never persisted here.
    token_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # ── lifecycle ────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: `issued_at + TOKEN_MAX_AGE_SECONDS`. Expiry is pre-existing policy
    #: (`booking_flow_service.TOKEN_MAX_AGE_SECONDS`), not a new one.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    terminal_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    #: G3-7P §9. A refusal is a decision an operator must be able to audit, and a Flow turn
    #: persists no hybrid-trace row, so the classification is recorded HERE against the
    #: offer it refused. A refusal that matches no offer at all (a fabricated or absent
    #: token) has no row to carry it and is observable through the human handoff it causes.
    refused_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0")
    last_refusal: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_refused_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)

    #: G3-7R/S6. A REPLAY — the same committed booking asked for again, by a Meta retry or
    #: by the other transport delivering the completion event — counted separately from a
    #: refusal, because it is not one. The architecture audit measured the confusion this
    #: ends: a Flow completion arriving at the conversation engine after the Data Exchange
    #: writer had already booked left the offer reading
    #: `ACCEPTED, refused_count=1, last_refusal='OFFER_ALREADY_ACCEPTED'` — the exact shape
    #: of a rejected submission, on the happy path. These two columns are what let an
    #: operator tell the successful duplicate from the real rejection.
    replay_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0")
    last_replay_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # ── what the acceptance produced, for audit ──────────────────────────────
    accepted_revision_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("revisions.id"), nullable=True)
    #: `use_alter` breaks a genuine cycle in the schema graph: G3-7R added
    #: `thread_revisions.offer_id` pointing the other way, because that column is where the
    #: one-offer/one-booking guarantee lives. Without this, SQLAlchemy reports
    #: "unresolvable cycles ... foreign key constraints involving these tables will not be
    #: considered" and quietly drops BOTH constraints from any `create_all`. The annotation
    #: side yields: this column is audit metadata, while `offer_id` is a safety control.
    accepted_thread_revision_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("thread_revisions.id", use_alter=True), nullable=True)


#: At most ONE live offer per thread, enforced by the database. Declared for both dialects:
#: without the predicate on SQLite this would forbid a thread from ever having a second
#: offer, which is the opposite of the intent.
Index("uq_booking_offer_one_live_per_thread", WhatsAppBookingOffer.thread_id,
      unique=True,
      postgresql_where=text("status = 'ISSUED'"),
      sqlite_where=text("status = 'ISSUED'"))

Index("ix_booking_offer_thread_status", WhatsAppBookingOffer.thread_id,
      WhatsAppBookingOffer.status)

#: ── G3-7R/S1: ONE OFFER, AT MOST ONE BOOKING — the database's own guarantee ──────────
#:
#: The G3-7P-R3A independent review measured that the application guards in front of this
#: were invisible to the test suite and insufficient on their own: with the acceptance
#: row lock AND the conditional `UPDATE ... WHERE status = 'ISSUED'` both removed, two
#: concurrent confirmations of one offer produced TWO bookings at two different slots in
#: 6 of 6 trials — and the 6,000-test suite stayed identity-identical. A same-slot calendar
#: conflict does not catch that case, because the two slots were both genuinely free.
#:
#: Partial, so the column stays additive: a booking with no offer behind it — anything
#: predating G3-7R, and every booking the authenticated operator endpoint writes — keeps
#: NULL, and any number of those may coexist. Declared for both dialects, because without
#: the predicate on SQLite this would forbid more than one NULL-offer booking in the whole
#: table, which is the opposite of the intent.
#:
#: The advisory lock is coordination. The exclusion constraint is the guarantee.
Index("uq_thread_revision_one_booking_per_offer", ThreadRevision.offer_id,
      unique=True,
      postgresql_where=text("offer_id IS NOT NULL"),
      sqlite_where=text("offer_id IS NOT NULL"))


Index("ix_booking_attempt_events_occurred", BookingAttemptEvent.occurred_at)
Index("ix_booking_attempt_events_thread", BookingAttemptEvent.hist_thread_id)
Index("ix_booking_attempt_events_thread_revision", BookingAttemptEvent.hist_thread_revision_id)

Index("ix_dedup_wa_kind_fp_created", WhatsAppOutboundDedup.wa_id, WhatsAppOutboundDedup.message_kind, WhatsAppOutboundDedup.content_fingerprint, WhatsAppOutboundDedup.created_at)


# Convergence Phase 4 (CD-07): rule K-E — a thread state is taken before a lead, for every
# Session (see `services.flush_order`).
from .services.flush_order import install as _install_k_e  # noqa: E402

_install_k_e()

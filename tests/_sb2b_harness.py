"""SB2B Option B test harness — synthetic worlds driven through the REAL production path.

Every turn goes through `ConversationEngine.handle()` on a session built exactly like
production (`sessionmaker(autoflush=False)`); the booking authority, the CE adapter, the
outbound gate and the Data Exchange confirmation are the production code. Only the network
edge is replaced: `_send_whatsapp_cloud_text` is a recorder (it never opens a socket) and
`urllib.request.urlopen` raises if anything tries. Synthetic data only (`@example.invalid`,
`wamid.SB2B…`), never a real person.
"""
from __future__ import annotations

import contextlib
import dataclasses
import itertools
import json
import os
import pathlib
import sys
from datetime import date, datetime, time, timedelta, timezone
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine, event, select, text  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):        # test DDL only
    return "JSON"


import app.models  # noqa: E402,F401
from app.models import (AiEvent, BookingCommandRecord, Lead, Profesional, Revision,  # noqa: E402
                        ThreadRevision, WhatsAppContact, WhatsAppMessage, WhatsAppThread,
                        WhatsAppThreadCandidate, WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402
from app.services.booking_flow_service import make_booking_token  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.settings import get_settings  # noqa: E402

OWNER_TEXT = "Gracias por completarlo. Un representante de RideCheck se estará comunicando con vos."
HANDOFF_TEXT = "No pudimos confirmar el turno. Un asesor se contacta a la brevedad."
_SEQ = itertools.count(1)


def slot_day() -> date:
    """A Tuesday 7–13 days ahead: inside the booking horizon, inside business hours."""
    today = date.today()
    return today + timedelta(days=(1 - today.weekday()) % 7 + 7)


def sqlite_engine():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _now(conn, _rec):                       # PostgreSQL's NOW() for raw-SQL writers
        conn.create_function("NOW", 0, lambda: datetime.now(timezone.utc).isoformat(" "))
    # The models' OWN metadata: other suites replace `app.db` in sys.modules, so `app.db.Base`
    # is not necessarily the registry these models were declared on.
    Lead.__table__.metadata.create_all(eng)
    return eng


def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def settings(*, flag: bool = True, trace: bool = False):
    return dataclasses.replace(get_settings(), smart_booking_ce_enabled=flag,
                               hybrid_trace_enabled=trace, openai_api_key="",
                               resend_api_key="", whatsapp_app_secret="s" * 32,
                               whatsapp_flow_id="FLOW_SB2B_FIXTURE")


@dataclasses.dataclass
class World:
    thread_id: int
    lead_id: int
    contact_id: int
    wa_id: str
    token: str
    wamid: str
    candidate_id: int
    day: date
    at: time


def ensure_professional(db):
    from app.services.booking_representation import LAUNCH_PROFESSIONAL_KEY
    if db.query(Profesional).filter(Profesional.is_launch_default.is_(True)).first() is None:
        db.add(Profesional(nombre="Fixture", apellido="SB2B-FIXTURE", email="sb2b@example.invalid",
                           resource_key=LAUNCH_PROFESSIONAL_KEY, is_launch_default=True,
                           is_active=True))
        db.flush()


def flow_payload(token, **extra) -> dict:
    body = {"flow_token": token} if token else {}
    body.update(extra)
    return body


def seed(engine, *, day: date | None = None, at: time = time(10, 0), zone=("Norte", "Benavidez"),
         needs_human: bool = False, cycle_reset: bool = False, lead_nombre=None,
         flow_data: dict | None = None, with_token: bool = True) -> World:
    n = next(_SEQ)
    day = day or slot_day()
    with Session(engine) as db:
        ensure_professional(db)
        contact = WhatsAppContact(wa_id=f"54911{n:08d}", display_name="Fixture")
        lead = Lead(estado="CONSULTA_NUEVA", flag="ACEPTADO", nombre=lead_nombre,
                    necesita_humano=False)
        db.add_all([contact, lead]); db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0)
        db.add(thread); db.flush()
        cand = WhatsAppThreadCandidate(thread_id=thread.id, marca="Peugeot", modelo="208",
                                       tipo_vehiculo="Auto", status="current_focus",
                                       zone_group=zone[0] if zone else None,
                                       zone_detail=zone[1] if zone else None)
        db.add(cand); db.flush()
        token = make_booking_token(thread.id) if with_token else None
        state = WhatsAppThreadState(
            thread_id=thread.id, last_stage="SCHEDULING", needs_human=needs_human,
            preferred_day=day.isoformat(), preferred_time=at.strftime("%H:%M"),
            flow_booking_token=token, current_focus_candidate_id=cand.id,
            cycle_reset_pending=cycle_reset,
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False)
        db.add(state)
        db.flush()
        if token:
            # Convergence Phase 3 (adaptation): the Flow token is admitted against the OFFER
            # TABLE, not the transport mirror, on every booking path — Track A's G3-7P rule,
            # which the converged Conversation Engine and Data Exchange paths now share. A
            # world that wants a bookable Flow therefore ISSUES its offer, bound to the
            # thread's canonical values, exactly as production dispatch does.
            from offer_fixture import seed_live_offer
            seed_live_offer(db, state, thread_id=thread.id, token=token,
                            candidate_id=cand.id, commit=False)
        wamid = f"wamid.SB2B.IN.{n:06d}"
        data = flow_payload(token, **(flow_data if flow_data is not None else
                                      {"nombre_apellido": "Ana Fixture",
                                       "email": f"ana{n}@example.invalid",
                                       "direccion": "Calle Falsa 123"}))
        db.add(WhatsAppMessage(
            thread_id=thread.id, direction="in", wa_message_id=wamid, message_type="flow_response",
            timestamp=datetime.now(timezone.utc), status="received",
            raw_payload={"from": contact.wa_id, "id": wamid, "type": "interactive",
                         "interactive": {"type": "nfm_reply", "nfm_reply": {
                             "response_json": json.dumps(data), "name": "flow"}}}))
        db.add(AiEvent(thread_id=thread.id, wa_message_id=wamid, wa_id=contact.wa_id,
                       status="triggered"))
        db.commit()
        return World(thread.id, lead.id, contact.id, contact.wa_id, token, wamid, cand.id, day, at)


def flow_event(w: World, *, token: str | None = "__world__", data: dict | None = None,
               wamid: str | None = None) -> ConversationHandleIn:
    tok = w.token if token == "__world__" else token
    body = data if data is not None else flow_payload(
        tok, nombre_apellido="Ana Fixture", email="ana@example.invalid", direccion="Calle Falsa 123")
    return ConversationHandleIn(thread_id=w.thread_id, wa_message_id=wamid or w.wamid,
                                wa_id=w.wa_id, message_type="flow_response",
                                flow_response=body, flow_token=tok)


class Meta:
    """The network edge, recorded. `mode`: "ok" | "4xx" | "timeout" | "raise"."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.sent: list[tuple[str, str]] = []
        self.egress: list = []
        self._stack = contextlib.ExitStack()

    def _send(self, *, to_wa_id, text):
        from app.ui.whatsapp_ui import MetaSendError
        self.sent.append((to_wa_id, text))
        # Convergence Phase 3-R1: the real constructor is (http_status, raw_body, ...). The
        # old `MetaSendError("rejected", http_status=400)` raised TypeError instead, so these
        # two modes silently exercised the generic-exception branch, never the 4xx/timeout one.
        if self.mode == "4xx":
            raise MetaSendError(400, '{"error": {"code": 100, "message": "rejected"}}')
        if self.mode == "timeout":
            raise MetaSendError(None, "timed out")
        if self.mode == "raise":
            raise RuntimeError("transport exploded")
        return f"wamid.SB2B.OUT.{next(_SEQ):06d}", {}

    def _no_egress(self, *a, **k):
        self.egress.append(a[:1])
        raise AssertionError("SB2B harness: network egress attempted")

    def __enter__(self):
        self._stack.enter_context(mock.patch.dict(os.environ, {"OUTBOUND_ENABLED": "true"}))
        self._stack.enter_context(mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_text", self._send))
        self._stack.enter_context(mock.patch(
            "app.services.conversation_engine._send_whatsapp_cloud_text", self._send))
        self._stack.enter_context(mock.patch("urllib.request.urlopen", self._no_egress))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False


@contextlib.contextmanager
def kill_switch():
    """OUTBOUND_ENABLED unset: the gate blocks every send (the shared runtime's state)."""
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OUTBOUND_ENABLED", None)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("egress")):
            yield


def run_turn(engine, event, *, cfg=None, engine_hook=None):
    """One production turn through the REAL route function behind
    `POST /api/conversation/handle` (engine + `ai_events` telemetry write-back), with only
    the settings object chosen by the test. Returns (out, engine) for trace inspection."""
    from app.api import conversation as route
    db = session_factory(engine)()
    made = []

    def build(*, db, settings):
        eng = ConversationEngine(db=db, settings=settings)
        if engine_hook is not None:
            engine_hook(eng)
        made.append(eng)
        return eng
    try:
        with mock.patch.object(route, "get_settings", lambda: cfg or settings()), \
                mock.patch.object(route, "ConversationEngine", build):
            out = route.handle_message(event, db)
        return out, made[0]
    finally:
        db.close()


def facts(engine, w: World) -> dict:
    with Session(engine) as db:
        st = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
        lead = db.get(Lead, w.lead_id)
        ledger = db.execute(select(BookingCommandRecord).where(
            BookingCommandRecord.thread_id == w.thread_id).order_by(BookingCommandRecord.id)).scalars().all()
        revs = db.execute(select(Revision).where(Revision.lead_id == w.lead_id)).scalars().all()
        trs = db.execute(select(ThreadRevision).where(ThreadRevision.thread_id == w.thread_id)).scalars().all()
        outs = db.execute(select(WhatsAppMessage).where(
            WhatsAppMessage.thread_id == w.thread_id, WhatsAppMessage.direction == "out")
            .order_by(WhatsAppMessage.id)).scalars().all()
        return {
            "cursor": st.last_processed_inbound_wa_message_id,
            "needs_human": st.needs_human, "last_stage": st.last_stage,
            "token": st.flow_booking_token, "current_revision_id": st.current_revision_id,
            "lead_estado": lead.estado, "lead_flag": lead.flag, "lead_nombre": lead.nombre,
            "lead_necesita_humano": lead.necesita_humano,
            "ledger": [(r.idempotency_key, r.source, r.operation, r.ack_status, r.ack_attempts,
                        r.ack_duplicate_risk, r.identity_outcome, r.occupying) for r in ledger],
            "revisions": [(r.id, r.turno_fecha, r.turno_hora, r.estado_revision) for r in revs],
            "occupying": sum(1 for r in revs if r.estado_revision in
                             ("PENDIENTE", "CONFIRMADO", "EN_PROCESO", "COMPLETADO")
                             and r.turno_fecha is not None),
            "thread_revisions": [(t.id, t.status) for t in trs],
            "outbound": [(o.path_id, o.status, o.text) for o in outs],
        }


def ledger_key(engine, w: World):
    with Session(engine) as db:
        return db.execute(select(BookingCommandRecord.idempotency_key).where(
            BookingCommandRecord.thread_id == w.thread_id)).scalars().all()


def dx_confirm(engine, w: World, *, day: date | None = None, at: time | None = None,
               name: str = "Ana Fixture", flag: bool = True):
    """The Data Exchange confirm_booking for this world's token (production service).

    Convergence Phase 3 (adaptation): at `870b4e3` the endpoint booked through the authority
    unconditionally; on the converged branch SMART_BOOKING_CE_ENABLED is the OFF switch for
    BOTH transports, read from `get_settings()` per request. The harness therefore states the
    flag it runs the endpoint under, exactly as `run_turn` does for CE (default: on)."""
    from app.services.booking_flow_service import BookingFlowService
    with Session(engine) as db, mock.patch("app.settings.get_settings",
                                           lambda: settings(flag=flag)):
        return BookingFlowService(db).handle_confirm_booking(w.token, {
            "date": (day or w.day).isoformat(), "time": (at or w.at).strftime("%H:%M"),
            "name": name, "email": "ana@example.invalid",
            "inspection_address": "Calle Falsa 123"})

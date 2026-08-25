"""WILD-04R-F1 — Pre-routing evidence persistence tests.

Verifies that "model del year" patterns (e.g. "2008 del 2014") create a
candidate BEFORE the routing gate so vehicle evidence is persisted
regardless of what the AI returns in its candidate action field.

W4F1-01 to W4F1-06 : Unit tests — extract_model_del_year catalog helper
W4F1-07 to W4F1-10 : CE integration — Turn 1 candidate creation (F1 path)
W4F1-11             : CE Turn 2 — zone evidence → pricing after F1 candidate
W4F1-12             : Combined semantics — FAQ burst answered + candidate persisted
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── SQLAlchemy / SQLite in-memory setup ──────────────────────────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = sqlalchemy.JSON   # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]

from sqlalchemy import create_engine, event, select, text as sql_text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


# ── Stub app.db BEFORE importing app.models ──────────────────────────────────
_db_mod = types.ModuleType("app.db")
_db_mod.Base = Base                           # type: ignore[attr-defined]
_db_mod.engine = _engine                      # type: ignore[attr-defined]
_db_mod.SessionLocal = _SessionLocal          # type: ignore[attr-defined]
_db_mod.DATABASE_URL = "sqlite:///:memory:"   # type: ignore[attr-defined]


def _get_db_gen():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


_db_mod.get_db = _get_db_gen                  # type: ignore[attr-defined]
sys.modules["app.db"] = _db_mod

# ── Stub heavy optional deps ──────────────────────────────────────────────────
for _mod_name in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

os.environ.setdefault("OUTBOUND_ENABLED", "false")

# ── Import ORM models ─────────────────────────────────────────────────────────
import app.models  # noqa: F401
from app.models import (
    Lead,
    ViaticosZone,
    WhatsAppContact,
    WhatsAppThread,
    WhatsAppThreadCandidate,
    WhatsAppThreadState,
)

Lead.__table__.metadata.create_all(_engine)

# ── Import units under test ───────────────────────────────────────────────────
from app.repositories.pricing_repository import BasePriceRow
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import ConversationEngine
from app.services.pricing import PricingService
from app.services.vehicle_catalog import (
    _contextual_numeric_model_lookup,
    extract_model_del_year,
)

# ── Shared constants ──────────────────────────────────────────────────────────
_WA_ID = "5491153369001"
_BASE_TS = datetime(2026, 8, 25, 10, 0, 0, tzinfo=timezone.utc)

_AI_REPLY_NONE = json.dumps({
    "intent": "FAQ",
    "reply": "Sí, mandamos informes. ¿En qué zona está el auto?",
    "deferred_interest": False,
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})

_AI_REPLY_QUALIFYING = json.dumps({
    "intent": "QUALIFYING",
    "reply": "¿En qué zona está el auto?",
    "deferred_interest": False,
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _new_session() -> Session:
    return _SessionLocal()


def _clean_all(db: Session) -> None:
    for tbl in [
        "ai_events", "whatsapp_outbound_dedup", "whatsapp_recipient_locks",
        "whatsapp_messages", "whatsapp_thread_candidates", "whatsapp_thread_states",
        "whatsapp_threads", "whatsapp_contacts", "viaticos_zones", "leads",
    ]:
        try:
            db.execute(sql_text(f"DELETE FROM {tbl}"))
        except Exception:
            pass
    db.commit()


def _seed(
    db: Session,
) -> tuple[WhatsAppContact, WhatsAppThread, Lead, WhatsAppThreadState]:
    _clean_all(db)
    contact = WhatsAppContact(wa_id=_WA_ID, display_name="F1Test", phone=None)
    db.add(contact)
    db.flush()
    lead = Lead(
        flag="PRESUPUESTANDO",
        estado="CONSULTA_NUEVA",
        nombre="F1Test",
        necesita_humano=False,
    )
    db.add(lead)
    db.flush()
    thread = WhatsAppThread(
        contact_id=contact.id,
        lead_id=lead.id,
        unread_count=0,
        created_at=_BASE_TS,
    )
    db.add(thread)
    db.flush()
    state = WhatsAppThreadState(
        thread_id=thread.id,
        needs_human=False,
        last_stage="QUALIFYING",
        last_intent=None,
        vehicle_clarification_sent=False,
        location_clarification_sent=False,
        vehicle_fallback_flow_sent=False,
        location_fallback_flow_sent=False,
        created_at=_BASE_TS,
        updated_at=_BASE_TS,
    )
    db.add(state)
    db.commit()
    return contact, thread, lead, state


def _make_engine(db: Session, *, with_sur_pricing: bool = False) -> ConversationEngine:
    """Build a ConversationEngine with a FakeRepo.

    with_sur_pricing=True: returns Sur/Berazategui zone (for Turn 2).
    with_sur_pricing=False: find_zone returns None (Turn 1, no zone yet).
    """

    class _FakeZoneRow:
        def __init__(self, zg: str, zd: Optional[str], v: int) -> None:
            self.zone_group = zg
            self.zone_detail = zd
            self.viaticos = v

    class _FakeRepo:
        def find_base_price(self, tipo: str) -> BasePriceRow:
            if tipo in ("SUV_4X4_DEPORTIVO", "SUV/4x4"):
                return BasePriceRow(tipo_vehiculo=tipo, precio_base=150_000)
            return BasePriceRow(tipo_vehiculo=tipo, precio_base=140_000)

        def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
            if not with_sur_pricing:
                return None
            if (zone_detail or "").strip().lower() == "berazategui":
                return _FakeZoneRow("Sur", "Berazategui", 90_000)
            if (zone_group or "").strip().lower() == "sur" and not zone_detail:
                return _FakeZoneRow("Sur", None, 90_000)
            return None

    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    settings.openai_chat_model = "gpt-4o-mini"
    settings.backend_url = "http://localhost:8000"
    settings.whatsapp_flow_id = ""
    settings.whatsapp_vehicle_fallback_flow_id = ""
    settings.whatsapp_location_fallback_flow_id = ""
    settings.whatsapp_website_flow_id = ""

    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = settings
    eng._pricing = PricingService(repository=_FakeRepo())
    from app.services.schedule import ScheduleService
    eng._schedule = ScheduleService(db=db)
    eng._ai_invoked = False
    eng._answer_source = None
    return eng


def _event(thread_id: int, wa_message_id: str, texts: list[str]) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=wa_message_id,
        wa_id=_WA_ID,
        text=texts[-1],
        unanswered_recent_user_messages=texts,
        recent_user_messages=texts,
    )


# ══════════════════════════════════════════════════════════════════════════════
# W4F1-01 to W4F1-06: Unit tests — extract_model_del_year catalog helper
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractModelDelYear(unittest.TestCase):
    """W4F1-01 to W4F1-06: extract_model_del_year and WILD-02-B guard unit tests."""

    def test_w4f1_01_un_2008_del_2014(self):
        """W4F1-01: 'un 2008 del 2014' → Peugeot 2008, year 2014."""
        result = extract_model_del_year("un 2008 del 2014")
        self.assertIsNotNone(result, "W4F1-01: must return a hit")
        match, year = result
        self.assertEqual(match.marca, "Peugeot", "W4F1-01: marca")
        self.assertEqual(str(match.modelo), "2008", "W4F1-01: modelo")
        self.assertEqual(year, 2014, "W4F1-01: year")

    def test_w4f1_02_bare_2008_del_2014(self):
        """W4F1-02: '2008 del 2014' → Peugeot 2008, year 2014 (bare form)."""
        result = extract_model_del_year("2008 del 2014")
        self.assertIsNotNone(result, "W4F1-02: must return a hit for bare form")
        match, year = result
        self.assertEqual(match.marca, "Peugeot")
        self.assertEqual(year, 2014)

    def test_w4f1_03_wrong_separator_returns_none(self):
        """W4F1-03: '2008 o 2014' → None (separator is 'o', not 'del'/'de')."""
        result = extract_model_del_year("2008 o 2014")
        self.assertIsNone(
            result,
            "W4F1-03: 'o' separator must not match — only 'del'/'de' triggers F1",
        )

    def test_w4f1_04_order_agnostic_year_first(self):
        """W4F1-04: '2014 del 2008' → Peugeot 2008, year 2014 (order-agnostic)."""
        result = extract_model_del_year("2014 del 2008")
        self.assertIsNotNone(result, "W4F1-04: must handle year-first ordering")
        match, year = result
        self.assertEqual(match.marca, "Peugeot", "W4F1-04: marca")
        self.assertEqual(year, 2014, "W4F1-04: year")

    def test_w4f1_05_de_variant(self):
        """W4F1-05: '2008 de 2014' → Peugeot 2008, year 2014 ('de' without 'l')."""
        result = extract_model_del_year("2008 de 2014")
        self.assertIsNotNone(result, "W4F1-05: 'de' variant must also match")
        match, year = result
        self.assertEqual(match.marca, "Peugeot")
        self.assertEqual(year, 2014)

    def test_w4f1_06_wild02b_guard_unchanged(self):
        """W4F1-06: _contextual_numeric_model_lookup('un 2008 del 2014') → None.

        WILD-02-B guard: 2+ numeric tokens → returns None.  F1 uses
        extract_model_del_year instead; WILD-02-B must NOT be weakened.
        """
        result = _contextual_numeric_model_lookup("un 2008 del 2014")
        self.assertIsNone(
            result,
            "W4F1-06: WILD-02-B must still return None for 2+ numeric tokens — "
            "F1 addition must not weaken the disambiguation guard",
        )


# ══════════════════════════════════════════════════════════════════════════════
# W4F1-07 to W4F1-10: CE integration — Turn 1 candidate creation
# ══════════════════════════════════════════════════════════════════════════════

class TestF1CandidateCreation(unittest.TestCase):
    """W4F1-07 to W4F1-10: WILD-04-F1 creates candidate before routing gate.

    AI returns action='none' — the candidate must be created by F1, not AI.
    Burst: ['Hola', 'Quiero revisar un 2008 del 2014', 'Mandan informes'].
    """

    _candidate: Optional[WhatsAppThreadCandidate] = None
    _candidate_count: int = 0

    @classmethod
    def setUpClass(cls) -> None:
        cls._patcher = patch("urllib.request.urlopen")
        mock_url = cls._patcher.start()
        mock_url.return_value.__enter__ = lambda s: s
        mock_url.return_value.__exit__ = MagicMock()
        mock_url.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_NONE}}]
        }).encode()

        cls._db = _new_session()
        _, thread, _, _ = _seed(cls._db)
        cls._thread_id = thread.id

        eng = _make_engine(cls._db, with_sur_pricing=False)
        ev = _event(thread.id, "msg-f1-t1", [
            "Hola",
            "Quiero revisar un 2008 del 2014",
            "Mandan informes",
        ])
        eng.handle(ev)

        cls._db.expire_all()
        rows = list(cls._db.execute(
            select(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == thread.id
            )
        ).scalars().all())
        cls._candidate_count = len(rows)
        cls._candidate = rows[0] if rows else None

    @classmethod
    def tearDownClass(cls) -> None:
        cls._patcher.stop()
        cls._db.close()

    def test_w4f1_07_candidate_created(self):
        """W4F1-07: WILD-04-F1 creates exactly 1 candidate (AI returned action='none')."""
        self.assertEqual(
            self._candidate_count, 1,
            f"W4F1-07: expected 1 candidate created by F1, got {self._candidate_count}. "
            "Candidate must be persisted BEFORE routing gate, not from AI.",
        )

    def test_w4f1_08_marca_peugeot(self):
        """W4F1-08: candidate.marca = 'Peugeot' (from catalog lookup on '2008')."""
        self.assertIsNotNone(self._candidate, "W4F1-08: candidate must exist")
        self.assertEqual(
            self._candidate.marca, "Peugeot",
            f"W4F1-08: expected marca='Peugeot', got '{self._candidate.marca}'",
        )

    def test_w4f1_09_anio_2014(self):
        """W4F1-09: candidate.anio = 2014 (year extracted from 'del 2014' in burst)."""
        self.assertIsNotNone(self._candidate, "W4F1-09: candidate must exist")
        self.assertEqual(
            self._candidate.anio, 2014,
            f"W4F1-09: expected anio=2014, got {self._candidate.anio}",
        )

    def test_w4f1_10_tipo_vehiculo_suv_4x4_deportivo(self):
        """W4F1-10: candidate.tipo_vehiculo = 'SUV_4X4_DEPORTIVO' (Peugeot 2008 catalog entry)."""
        self.assertIsNotNone(self._candidate, "W4F1-10: candidate must exist")
        self.assertEqual(
            self._candidate.tipo_vehiculo, "SUV_4X4_DEPORTIVO",
            f"W4F1-10: expected 'SUV_4X4_DEPORTIVO', got '{self._candidate.tipo_vehiculo}'",
        )


# ══════════════════════════════════════════════════════════════════════════════
# W4F1-11: CE Turn 2 — zone evidence → pricing after F1 candidate
# ══════════════════════════════════════════════════════════════════════════════

class TestF1Turn2Pricing(unittest.TestCase):
    """W4F1-11: Turn 2 with 'Berazategui' triggers 240k pricing after F1 candidate.

    Pre-seeds the candidate that WILD-04-F1 would have created in Turn 1
    (Peugeot 2008, 2014, SUV_4X4_DEPORTIVO) and runs Turn 2 with zone only.
    Verifies that the deterministic quote override fires and commits
    lead.flag='PRESUPUESTO_ENVIADO' (240k = 150k base + 90k viaticos).
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._patcher = patch("urllib.request.urlopen")
        mock_url = cls._patcher.start()
        mock_url.return_value.__enter__ = lambda s: s
        mock_url.return_value.__exit__ = MagicMock()
        mock_url.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_QUALIFYING}}]
        }).encode()

        cls._db = _new_session()
        _, thread, lead, state = _seed(cls._db)

        # Seed ViaticosZone for _extract_zone_from_text (DB lookup)
        cls._db.add(ViaticosZone(zone_group="Sur", zone_detail="Berazategui", viaticos=90_000))
        cls._db.add(ViaticosZone(zone_group="Sur", zone_detail=None, viaticos=90_000))
        cls._db.commit()

        # Pre-seed candidate (simulates what WILD-04-F1 created in Turn 1)
        candidate = WhatsAppThreadCandidate(
            thread_id=thread.id,
            marca="Peugeot",
            modelo="2008",
            tipo_vehiculo="SUV_4X4_DEPORTIVO",
            anio=2014,
            status="current_focus",
        )
        cls._db.add(candidate)
        cls._db.flush()
        state.current_focus_candidate_id = candidate.id
        state.last_intent = "PREPURCHASE_INSPECTION"
        cls._db.commit()

        cls._lead_id = lead.id
        cls._state_id = state.id

        eng = _make_engine(cls._db, with_sur_pricing=True)
        ev = _event(thread.id, "msg-f1-t2", ["Berazategui"])
        eng.handle(ev)

        cls._db.expire_all()
        cls._lead = cls._db.get(Lead, lead.id)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._patcher.stop()
        cls._db.close()

    def test_w4f1_11_turn2_zone_triggers_pricing(self):
        """W4F1-11: Turn 2 'Berazategui' → 240k quote committed as PRESUPUESTO_ENVIADO.

        SUV_4X4_DEPORTIVO: base=150k + Sur/Berazategui viaticos=90k = 240k total.
        """
        self.assertEqual(
            self._lead.flag, "PRESUPUESTO_ENVIADO",
            f"W4F1-11: expected lead.flag='PRESUPUESTO_ENVIADO' (240k quote committed), "
            f"got '{self._lead.flag}'. "
            "Turn 2 with zone must trigger deterministic pricing after F1 candidate.",
        )


# ══════════════════════════════════════════════════════════════════════════════
# W4F1-12: Combined semantics — mixed vehicle+FAQ burst
# ══════════════════════════════════════════════════════════════════════════════

_AI_REPLY_COMBINED = json.dumps({
    "intent": "QUALIFYING",
    "reply": (
        "Sí, hacemos revisiones preventa. Mandamos informe completo al terminar. "
        "No es necesario que estés presente. "
        "Aceptamos transferencia bancaria, Mercado Pago y efectivo — no aceptamos débito ni crédito. "
        "¿En qué zona está el auto?"
    ),
    "deferred_interest": False,
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})


class TestF1CombinedSemantics(unittest.TestCase):
    """W4F1-12: vehicle + FAQ burst → Priority-3 AI path, candidate persisted, combined reply.

    Burst: text_a (vehicle + inspection), text_b (report + presence FAQ),
           text_c (payment FAQ — triggers Priority 3 via 'aceptan debito'/'como se paga').

    Proves STATE EXTRACTION (candidate persisted by F1) and RESPONSE CONTENT (FAQ
    answers + location request) are both delivered in a single Turn-1 reply.
    """

    def test_w4f1_12_combined_semantics(self):
        """W4F1-12: 'un 2008 del 2014' + FAQ burst → AI invoked + candidate + combined reply."""
        db = _new_session()
        _, thread, _, _ = _seed(db)

        text_a = "Hola, ¿cómo va? Quiero revisar un 2008 del 2014. Ustedes hacen eso, ¿no?"
        text_b = "¿Mandan informes? ¿Tengo que estar presente?"
        text_c = "¿Aceptan débito? ¿Cómo se paga?"

        ev = _event(thread.id, "sg-t1", [text_a, text_b, text_c])

        sent_texts: list[str] = []
        eng = _make_engine(db, with_sur_pricing=False)

        with patch("urllib.request.urlopen") as mock_url:
            mock_url.return_value.__enter__ = lambda s: s
            mock_url.return_value.__exit__ = MagicMock()
            mock_url.return_value.read = lambda: json.dumps({
                "choices": [{"message": {"content": _AI_REPLY_COMBINED}}]
            }).encode()

            with patch.object(eng, "_send_text_to_wa",
                              side_effect=lambda ctx, txt: sent_texts.append(txt) or "out-12"):
                result = eng.handle(ev)

        # Priority 3 must fire (FAQ phrases present) → AI invoked, not hard location return
        self.assertTrue(
            result.ai_invoked,
            "W4F1-12: FAQ phrases ('aceptan debito'/'como se paga') must trigger Priority-3 "
            "→ AI path. ai_invoked=False means FAQ lost to location hard-return (regression).",
        )

        # F1 must have persisted the candidate before routing gate
        db.expire_all()
        cands = list(db.execute(
            select(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == thread.id
            )
        ).scalars().all())
        self.assertGreater(
            len(cands), 0,
            "W4F1-12: Peugeot 2008/2014 candidate must be persisted by F1 before routing gate",
        )
        c0 = max(cands, key=lambda x: x.id)
        self.assertEqual(c0.marca, "Peugeot", "W4F1-12: candidate.marca must be Peugeot")
        self.assertIn("2008", str(c0.modelo), "W4F1-12: candidate.modelo must be 2008")
        self.assertEqual(c0.anio, 2014, "W4F1-12: candidate.anio must be 2014")

        # Combined reply must answer all four FAQ areas in one message
        combined_sent = " ".join(sent_texts).lower()
        self.assertTrue(
            any(w in combined_sent for w in ["informe", "reporte"]),
            f"W4F1-12: reply must answer report question; sent={sent_texts!r}",
        )
        self.assertTrue(
            any(w in combined_sent for w in ["presente", "presencia"]),
            f"W4F1-12: reply must answer presence question; sent={sent_texts!r}",
        )
        self.assertTrue(
            any(w in combined_sent for w in [
                "transferencia", "mercado pago", "efectivo", "debito", "débito",
            ]),
            f"W4F1-12: reply must answer payment question; sent={sent_texts!r}",
        )
        self.assertTrue(
            any(w in combined_sent for w in ["donde", "dónde", "zona", "viatico", "viático"]),
            f"W4F1-12: reply must request location; sent={sent_texts!r}",
        )

        db.close()


if __name__ == "__main__":
    unittest.main()

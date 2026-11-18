"""R6 (R5F1RV-01, R5F1RV-02/03/04) — the structured outbound response contract.

Owner decision (FINAL-CANDIDATE-CONVERGENCE-R6): the deterministic final layer validates a
STRUCTURED response contract and does not reconstruct authoritative domain meaning from
ordinary rendered Spanish. Authority order: canonical state → structured plan and claims →
deterministic validation → rendered text → conservative text checks (defence in depth).

Groups
  A  envelope provenance: exact type only; forging and malformed declarations fail closed
  B  structured claims validated against canonical state; unknown/malformed fail closed
  C  vehicle claim SHAPES (real catalogue, real CE resolver)
  D  location claim shapes          E  availability claim shapes
  F  plan-appropriate fallbacks with explicit contracts
  G  declared question intents      H  R5F1RV-02 / 03 / 04 hardening
  I  no state from outbound prose: the vehicle producer, the AI-proposed time
  J  question recording reads the declared contract, never a fallback's words
  K  structure: one validation entry, no raw-text failure path, no post-validation reparse
"""
from __future__ import annotations

import ast
import copy
import enum
import json
import pathlib
import pickle
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app.services.conversation_engine as CE  # noqa: E402
import app.services.response_validator as RV  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.services.reply_claim_shape import classify_vehicle_mention  # noqa: E402
from app.services.response_composition import LiveResponsePlan, PropositionId  # noqa: E402
from app.services.response_validator import (  # noqa: E402
    ApprovedTemplateId, ApprovedTemplateText, CanonicalFacts, ComposedText, DomainClaim,
    QuestionIntent, ResponseDeclaration, ResponseProvenance, StateUpdate, StructuredClaim,
    ValidationFallbackId, composed_text, safe_failure_text, select_validation_fallback,
    validate_envelope, validate_response)
from app.services.response_validator import render_price_breakdown  # noqa: E402  WILD1-C

_APP = _ROOT / "backend" / "app"
_ZONES = tuple(
    v for line in (_ROOT / "tests" / "fixtures" / "r6_zone_names.txt").read_text(
        encoding="utf-8").splitlines() if line and not line.startswith("#")
    for v in line.split("|") if v)
_ENG = object.__new__(ConversationEngine)


def _resolver(fragment):
    return ConversationEngine._reply_vehicle_claim_resolver(_ENG, fragment)


NEW = CanonicalFacts(known_zone_names=_ZONES)
COROLLA = CanonicalFacts(vehicle_marca="Toyota", vehicle_modelo="Corolla", known_zone_names=_ZONES)
LOCATED = CanonicalFacts(inspection_zone_group="CABA", inspection_zone_detail="Palermo",
                         known_zone_names=_ZONES)
QUOTED = CanonicalFacts(vehicle_marca="Toyota", vehicle_modelo="Corolla",
                        inspection_zone_group="CABA", inspection_zone_detail="Palermo",
                        known_zone_names=_ZONES, quote_total=85000, quote_base=70000,
                        quote_viaticos=15000)
SLOTS = CanonicalFacts(availability_checked=True, offered_slots=("10:00", "11:30"),
                       known_zone_names=_ZONES)
#: R6-C3: the same evaluation, with the calendar date it was made for (Monday 2026-11-16).
import datetime as _dt  # noqa: E402
SLOTS_DATED = CanonicalFacts(availability_checked=True, offered_slots=("10:00", "11:30"),
                             availability_date=_dt.date(2026, 11, 16),
                             business_today=_dt.date(2026, 11, 11), known_zone_names=_ZONES)


def _env(value, facts=NEW):
    return validate_envelope(value, facts, vehicle_resolver=_resolver,
                             location_question=CE._reply_already_asks_location)


# ── A. provenance ─────────────────────────────────────────────────────────────────────

class A_Provenance(unittest.TestCase):

    def test_a01_plain_text_is_ai_composed_and_undeclared(self):
        e = _env("Perfecto, cualquier duda me escribís.")
        self.assertIs(e.provenance, ResponseProvenance.AI_COMPOSED)
        self.assertFalse(e.declared)
        self.assertEqual(e.question_intents, frozenset())

    def test_a02_the_approved_template_is_approved(self):
        e = _env(CE._MOTORCYCLE_ADVISOR_REPLY)
        self.assertIs(e.provenance, ResponseProvenance.APPROVED_TEMPLATE)
        self.assertIs(e.template_id, ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY)
        self.assertEqual(e.text, str(CE._MOTORCYCLE_ADVISOR_REPLY))

    def test_a03_composed_text_is_deterministic_composed(self):
        e = _env(composed_text("Genial, seguimos."))
        self.assertIs(e.provenance, ResponseProvenance.DETERMINISTIC_COMPOSED)
        self.assertTrue(e.declared)

    def test_a04_forged_provenance_fails_closed(self):
        class Sub(ComposedText):
            pass
        decl = ResponseDeclaration(question_intents=frozenset({QuestionIntent.INSPECTION_LOCATION}),
                                   state_updates=frozenset({StateUpdate.RECORD_OUTSTANDING_QUESTION}))
        duck = type("Duck", (str,), {})("¿En qué zona está el auto?")
        duck._declaration = decl                   # duck-typed declaration on a plain subclass
        forged = [Sub("¿En qué zona está el auto?", decl), duck,
                  ApprovedTemplateText.__new__(ApprovedTemplateText,
                                               ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY,
                                               "Es un Fiat Uno.")]
        for value in forged[:2]:
            with self.subTest(kind=type(value).__name__):
                e = _env(value, COROLLA)
                self.assertIs(e.provenance, ResponseProvenance.AI_COMPOSED)
        # a real tag on other words: the contract refuses, full validation refuses the claim
        e = _env(forged[2], COROLLA)
        self.assertTrue(e.fell_back)
        self.assertEqual(e.text, select_validation_fallback(COROLLA).text)

    def test_a05_malformed_declarations_fail_closed(self):
        bad = [None, object(), ResponseDeclaration(claims=frozenset({"VEHICLE"})),
               ResponseDeclaration(question_intents=frozenset({"INSPECTION_LOCATION"})),
               ResponseDeclaration(question_intents=["x"]),
               ResponseDeclaration(state_updates=frozenset({StateUpdate.RECORD_OUTSTANDING_QUESTION})),
               ResponseDeclaration(claims=frozenset({StructuredClaim("VEHICLE", ("Fiat", "Uno"))}))]
        for decl in bad:
            with self.subTest(decl=type(decl).__name__):
                value = ComposedText("¿En qué zona está el auto?", decl)
                e = _env(value, COROLLA)
                self.assertIs(e.provenance, ResponseProvenance.AI_COMPOSED)
                self.assertTrue(any(f.claim == "RESPONSE_CONTRACT" for f in e.findings))

    def test_a06_ai_text_cannot_acquire_template_or_composed_trust(self):
        ct = composed_text("Hola", questions=(QuestionIntent.INSPECTION_LOCATION,))
        for derived in (ct + " ¿En qué zona está?", ct.strip(), ct.upper(), f"{ct}", ct.format(),
                        copy.copy(ct), pickle.loads(pickle.dumps(ct)), ct.replace("x", "y"),
                        "".join([ct]), ct[:]):
            with self.subTest(derived=repr(derived)[:30]):
                self.assertIsNot(type(derived), ComposedText)

    def test_a07_enums_are_closed(self):
        for cls in (ResponseProvenance, DomainClaim, QuestionIntent, StateUpdate,
                    ValidationFallbackId):
            with self.subTest(cls=cls.__name__):
                self.assertTrue(issubclass(cls, enum.Enum))
                with self.assertRaises(TypeError):
                    class _X(cls):          # noqa: N801 - Python refuses to extend
                        EXTRA = "EXTRA"
        self.assertEqual({c.value for c in DomainClaim},
                         {"VEHICLE", "LOCATION", "AVAILABILITY", "PRICE", "BOOKING", "ACCEPTANCE"})


# ── B. structured claims ──────────────────────────────────────────────────────────────

class B_StructuredClaims(unittest.TestCase):

    def _claim(self, kind, value, facts, text="Perfecto."):
        return _env(composed_text(text, claims=[StructuredClaim(kind, value)]), facts)

    def test_b01_each_claim_kind_proven_by_canonical_state_is_kept(self):
        ok = [(DomainClaim.VEHICLE, ("Toyota", "Corolla"), QUOTED),
              (DomainClaim.LOCATION, ("Palermo",), QUOTED),
              # WILD1-C: a declared quote is the full breakdown, rendered as the canonical block
              (DomainClaim.PRICE, (70000, 15000, 85000), QUOTED),
              # R6-C3: a deterministic availability claim carries its exact ISO date
              (DomainClaim.AVAILABILITY, ("2026-11-16", "10:00"), SLOTS_DATED),
              (DomainClaim.BOOKING, (), CanonicalFacts(booking_confirmed=True)),
              (DomainClaim.ACCEPTANCE, (), CanonicalFacts(acceptance_confirmed=True))]
        for kind, value, facts in ok:
            with self.subTest(kind=kind.name):
                text = ("Perfecto.\n\n" + render_price_breakdown(*value)
                        if kind is DomainClaim.PRICE else "Perfecto.")
                e = self._claim(kind, value, facts, text=text)
                self.assertIs(e.provenance, ResponseProvenance.DETERMINISTIC_COMPOSED)
                self.assertEqual(e.text, text)

    def test_b02_each_conflicting_claim_refuses_the_whole_value(self):
        bad = [(DomainClaim.VEHICLE, ("Fiat", "Uno"), QUOTED),
               (DomainClaim.VEHICLE, ("Toyota", "Corolla"), NEW),
               (DomainClaim.LOCATION, ("Pilar",), QUOTED),
               (DomainClaim.LOCATION, ("Palermo",), NEW),
               (DomainClaim.PRICE, (10000,), QUOTED),
               (DomainClaim.PRICE, (85000,), NEW),
               # WILD1-C: a breakdown that reconciles but is not the canonical one, a total-only
               # declaration of the right amount, and swapped components are all refused.
               (DomainClaim.PRICE, (75000, 10000, 85000), QUOTED),
               (DomainClaim.PRICE, (85000,), QUOTED),
               (DomainClaim.PRICE, (15000, 70000, 85000), QUOTED),
               (DomainClaim.AVAILABILITY, ("15:00",), SLOTS),
               (DomainClaim.AVAILABILITY, ("10:00",), NEW),
               (DomainClaim.BOOKING, (), NEW),
               (DomainClaim.ACCEPTANCE, (), NEW)]
        for kind, value, facts in bad:
            with self.subTest(kind=kind.name, value=value):
                e = self._claim(kind, value, facts, text="Texto que nunca se envía.")
                self.assertTrue(e.fell_back)
                self.assertEqual(e.text, select_validation_fallback(facts).text)

    def test_b03_the_composed_quote_declares_and_passes(self):
        _q = SimpleNamespace(precio_base=70000, viaticos=15000, precio_total=85000)  # WILD1-C
        q = ConversationEngine._composed_quote_reply(_ENG, "Toyota", "Corolla", "Palermo", _q, 2018)
        self.assertIs(type(q), ComposedText)
        kinds = {c.kind for c in q.declaration.claims}
        self.assertEqual(kinds, {DomainClaim.PRICE, DomainClaim.VEHICLE, DomainClaim.LOCATION})
        e = _env(q, QUOTED)
        self.assertEqual(e.text, str(q))
        self.assertIs(e.provenance, ResponseProvenance.DETERMINISTIC_COMPOSED)
        # the same composer for another car is refused before its words are read
        wrong = ConversationEngine._composed_quote_reply(_ENG, "Fiat", "Uno", "Palermo", _q)
        self.assertTrue(_env(wrong, QUOTED).fell_back)


# ── C. vehicle claim shapes ───────────────────────────────────────────────────────────

class C_VehicleShapes(unittest.TestCase):
    CLAIMS = ["Es un Fiat Uno.", "Tengo un Fiat Uno.", "Es un Uno 2015.", "Tenés un Uno 2015.",
              "Es un Fiat Punto.", "Es un Fiat Idea.", "Es un Toyota Corolla.", "Es un Corolla.",
              "Hacemos el servicio para un 2008 del 2014."]
    MENTIONS = ["Revisamos todo uno por uno.", "Es para uno de los dos autos.",
                "Te esperamos a las 10 en punto.", "¡Buena idea!", "Buena idea, la revisión te da tranquilidad.",
                "Revisamos punto por punto.", "Uno nunca sabe lo que tiene un usado.",
                "Me parece una excelente idea.", "Cada uno de los puntos de control queda en el informe.",
                "Es una idea genial revisarlo.", "Trabajamos desde 2008.",
                "A la brevedad uno de nuestros asesores se estará contactando con vos."]

    def test_c01_claim_shaped_constructions_are_claims(self):
        for s in self.CLAIMS:
            with self.subTest(s=s):
                m = _resolver(s)
                self.assertIsNotNone(m)
                self.assertTrue(m.claim_shaped, m)

    def test_c02_bare_tokens_are_mentions_not_claims(self):
        for s in self.MENTIONS:
            with self.subTest(s=s):
                m = _resolver(s)
                self.assertTrue(m is None or not m.claim_shaped, m)

    def test_c03_claims_are_refused_mentions_are_kept(self):
        for s in self.CLAIMS:
            if "Corolla" in s:
                continue
            with self.subTest(s=s):
                self.assertTrue(_env(s, COROLLA).fell_back)
        for s in self.MENTIONS:
            for facts in (NEW, COROLLA):
                with self.subTest(s=s, facts=facts.vehicle_modelo):
                    e = _env(s, facts)
                    if "a las 10" in s:
                        # R6-C3 (R6C2RV-S01): a clock time is read, so this is an arrival-time
                        # proposition: refused without an evaluation — by AVAILABILITY, never by
                        # VEHICLE ("punto" stays a mention) — and kept when 10:00 is evaluated.
                        refused = {f.claim for f in e.findings if not f.allowed
                                   and f.action != RV.ACTION_ALLOWED}
                        self.assertTrue(e.fell_back)
                        self.assertIn("AVAILABILITY", refused)
                        self.assertNotIn("VEHICLE", refused)
                        self.assertEqual(_env(s, SLOTS).text, s)
                        continue
                    self.assertEqual(e.text, s)
                    self.assertFalse(e.fell_back)

    def test_c04_a_mention_is_recorded_as_a_diagnostic(self):
        e = _env("Revisamos todo uno por uno.", COROLLA)
        diag = [f for f in e.findings if f.action == RV.ACTION_DIAGNOSTIC]
        self.assertTrue(diag and diag[0].claim == "VEHICLE", e.findings)

    def test_c05_no_model_or_place_word_list_was_added(self):
        """Code only (docstrings and comments may name the examples): no string literal in
        the claim-shape module or the validator is a catalogue model or a zone name."""
        forbidden = {"uno", "punto", "idea", "clase", "serie", "up", "ka", "gol", "fox",
                     "polo", "golf", "fiesta", "master", "partner", "symbol",
                     "pilar", "flores", "norte", "centro"}
        for rel in ("services/reply_claim_shape.py", "services/response_validator.py"):
            tree = ast.parse((_APP / rel).read_text(encoding="utf-8"))
            docs = {id(n.body[0].value) for n in ast.walk(tree)
                    if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                    and n.body and isinstance(n.body[0], ast.Expr)
                    and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
            literals = {n.value.strip().lower() for n in ast.walk(tree)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and id(n) not in docs}
            with self.subTest(module=rel):
                self.assertEqual(literals & forbidden, set())

    def test_c06_classification_failure_is_strict(self):
        with mock.patch("app.services.reply_claim_shape._catalog_norm", side_effect=RuntimeError):
            m = classify_vehicle_mention("uno por uno", SimpleNamespace(marca="Fiat", modelo="Uno",
                                                                       matched_alias="uno"))
        self.assertTrue(m.claim_shaped)


# ── D. location shapes ────────────────────────────────────────────────────────────────

class D_LocationShapes(unittest.TestCase):
    CLAIMS = ["El auto está en Pilar.", "Vamos hasta Pilar.", "La revisión la hacemos en Flores.",
              "El auto está en zona Norte.", "Vamos a la zona de Pilar."]
    NOT_CLAIMS = ["Revisamos los pilares y la carrocería del auto.",
                  "La revisión incluye el chequeo del chasis norteamericano.",
                  "Gracias Pilar, la revisión del auto la hacemos donde esté.",
                  "Las flores del jardín no influyen en la revisión del auto.",
                  "Si el auto está en zona Norte, también vamos.",
                  "Trabajamos en CABA y GBA, zona Norte, Sur y Oeste.",
                  "Vamos a donde esté el auto, incluso si está en el Centro."]

    def test_d01_locative_frames_are_claims(self):
        for s in self.CLAIMS:
            with self.subTest(s=s):
                self.assertTrue(_env(s, LOCATED).fell_back)
                self.assertTrue(_env(s, NEW).fell_back)

    def test_d02_non_locative_uses_are_not(self):
        for s in self.NOT_CLAIMS:
            for facts in (NEW, LOCATED):
                with self.subTest(s=s):
                    e = _env(s, facts)
                    self.assertEqual(e.text, s)

    def test_d03_supported_location_is_kept(self):
        self.assertEqual(_env("El auto está en Palermo.", LOCATED).text, "El auto está en Palermo.")


# ── E. availability shapes ────────────────────────────────────────────────────────────

class E_AvailabilityShapes(unittest.TestCase):

    def test_e01_business_hours_are_information(self):
        for s in ("Trabajamos de 9:00 a 18:00.", "Trabajamos de lunes a viernes de 9:00 a 18:00.",
                  "Atendemos entre 9:00 y 13:00.", "Abrimos 9:00 - 18:00."):
            with self.subTest(s=s):
                e = _env(s, NEW)
                self.assertEqual(e.text, s)
                self.assertTrue(any(f.action == RV.ACTION_DIAGNOSTIC and f.claim == "AVAILABILITY"
                                    for f in e.findings))

    def test_e02_conditional_availability_is_not_a_slot(self):
        for s in ("Coordinamos según disponibilidad.", "Coordinamos según la disponibilidad de agenda.",
                  "Todo sujeto a disponibilidad."):
            with self.subTest(s=s):
                self.assertEqual(_env(s, NEW).text, s)

    def test_e03_concrete_offers_are_claims(self):
        for s in ("Te ofrezco el turno de las 15:00.", "Tengo disponibilidad mañana a las 10:00.",
                  "Hay turno a las 16:30."):
            with self.subTest(s=s):
                self.assertTrue(_env(s, NEW).fell_back)
        self.assertEqual(_env("Te ofrezco el turno de las 10:00.", SLOTS).text,
                         "Te ofrezco el turno de las 10:00.")
        self.assertTrue(_env("Te ofrezco el turno de las 15:00.", SLOTS).fell_back)


# ── F. fallbacks ──────────────────────────────────────────────────────────────────────

class F_Fallbacks(unittest.TestCase):

    def test_f01_only_missing_facts_are_asked(self):
        cases = {NEW: (ValidationFallbackId.ASK_VEHICLE_AND_LOCATION,
                       {QuestionIntent.VEHICLE_IDENTITY, QuestionIntent.INSPECTION_LOCATION}),
                 COROLLA: (ValidationFallbackId.ASK_LOCATION, {QuestionIntent.INSPECTION_LOCATION}),
                 LOCATED: (ValidationFallbackId.ASK_VEHICLE, {QuestionIntent.VEHICLE_IDENTITY}),
                 QUOTED: (ValidationFallbackId.NO_QUESTION, set())}
        for facts, (fid, intents) in cases.items():
            with self.subTest(fid=fid.name):
                c = select_validation_fallback(facts)
                self.assertIs(c.fallback_id, fid)
                self.assertEqual(set(c.question_intents), intents)
                if fid is ValidationFallbackId.NO_QUESTION:
                    self.assertNotIn("?", c.text)

    def test_f02_a_known_vehicle_is_never_reasked(self):
        e = _env("Es un Fiat Uno.", COROLLA)
        self.assertNotIn("vehículo", e.text)
        self.assertEqual(e.question_intents, frozenset({QuestionIntent.INSPECTION_LOCATION}))

    def test_f03_the_legacy_fallback_text_is_unchanged(self):
        self.assertEqual(select_validation_fallback(NEW).text, RV._FALLBACK_REPLY)

    def test_f04_malformed_facts_select_no_question(self):
        self.assertIs(select_validation_fallback(object()).fallback_id, ValidationFallbackId.NO_QUESTION)

    def test_f05_the_fallback_contract_registry_is_closed(self):
        self.assertIsInstance(RV._VALIDATION_FALLBACKS, types.MappingProxyType)
        self.assertEqual(set(RV._VALIDATION_FALLBACKS), set(ValidationFallbackId))
        with self.assertRaises(TypeError):
            RV._VALIDATION_FALLBACKS[ValidationFallbackId.NO_QUESTION] = None


# ── G. question intents ───────────────────────────────────────────────────────────────

class G_QuestionIntents(unittest.TestCase):

    def test_g01_ai_location_question_only_when_the_place_is_missing(self):
        q = "Dale. ¿En qué zona está el auto?"
        self.assertEqual(_env(q, NEW).question_intents, frozenset({QuestionIntent.INSPECTION_LOCATION}))
        self.assertEqual(_env(q, LOCATED).question_intents, frozenset())

    def test_g02_ai_prose_without_a_location_question_declares_nothing(self):
        for s in ("¿Te parece buena idea revisarlo?", "Perfecto, gracias.",
                  "Nos vemos en Palermo."):     # a statement, not a question
            with self.subTest(s=s):
                self.assertEqual(_env(s, NEW).question_intents, frozenset())

    def test_g03_a_composed_declaration_without_a_question_is_withdrawn(self):
        e = _env(composed_text("Perfecto.", questions=(QuestionIntent.INSPECTION_LOCATION,)), NEW)
        self.assertEqual(e.question_intents, frozenset())
        self.assertFalse(e.may_record(QuestionIntent.INSPECTION_LOCATION))

    def test_g04_a_composed_declaration_with_a_question_records(self):
        e = _env(composed_text("¿Me podés indicar el barrio?",
                               questions=(QuestionIntent.INSPECTION_LOCATION,)), NEW)
        self.assertTrue(e.may_record(QuestionIntent.INSPECTION_LOCATION))

    def test_g04b_a_composer_asking_for_a_missing_detail_keeps_its_declaration(self):
        # group known, detail missing: the composer knows what it asks for (R6 keeps the
        # declared intent; the AI path alone is limited to a genuinely missing location)
        facts = CanonicalFacts(inspection_zone_group="CABA", known_zone_names=_ZONES)
        e = _env(composed_text("¿En qué barrio de CABA está el auto? Así te paso el valor exacto.",
                               questions=(QuestionIntent.INSPECTION_LOCATION,)), facts)
        self.assertTrue(e.may_record(QuestionIntent.INSPECTION_LOCATION))

    def test_g05_the_no_question_fallback_records_nothing(self):
        e = _env("Es un Fiat Uno.", QUOTED)
        self.assertIs(e.fallback_id, ValidationFallbackId.NO_QUESTION)
        self.assertEqual(e.state_updates, frozenset())


# ── H. R5F1RV-02 / 03 / 04 ────────────────────────────────────────────────────────────

class H_R5F1Hardening(unittest.TestCase):

    def test_h01_rv02_bad_repr_policy_refuses_without_raising(self):
        class Evil:
            def __repr__(self):
                raise RuntimeError("repr")
            __str__ = __repr__
        bad = RV.ApprovedTemplateContract(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY,
                                          str(CE._MOTORCYCLE_ADVISOR_REPLY), Evil(), "x")
        reg = dict(RV._APPROVED_TEMPLATE_CONTRACTS); reg[ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY] = bad
        with mock.patch.object(RV, "_APPROVED_TEMPLATE_CONTRACTS", types.MappingProxyType(reg)):
            skipped, finding = RV._template_exemptions(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY,
                                                       CE._MOTORCYCLE_ADVISOR_REPLY)
        self.assertEqual(skipped, frozenset())
        self.assertFalse(finding.allowed)
        # the refusal names the TYPE, never runs the object's __repr__ (R5F1RV-02)
        self.assertIn("unknown policy (Evil)", finding.detail)

    def test_h02_rv02_skipped_collection_is_type_checked(self):
        for value in (5, ["VEHICLE"], {"VEHICLE"}, frozenset({"NOT_A_CLAIM"}), frozenset({1}), None):
            table = dict(RV._POLICY_SKIPPED_CLAIMS)
            table[RV.TemplateValidationPolicy.NO_VEHICLE_CLAIM] = value
            with self.subTest(value=value), \
                    mock.patch.object(RV, "_POLICY_SKIPPED_CLAIMS", types.MappingProxyType(table)):
                skipped, finding = RV._template_exemptions(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY,
                                                           CE._MOTORCYCLE_ADVISOR_REPLY)
                self.assertEqual(skipped, frozenset())
                self.assertFalse(finding.allowed)

    def test_h03_rv02_registry_corruption_never_reaches_raw_text_fail_open(self):
        class Boom:
            def get(self, *_a):
                raise RuntimeError("corrupt")
        eng = object.__new__(ConversationEngine)
        eng._build_canonical_facts = lambda ctx, state: COROLLA
        ctx = SimpleNamespace(thread=SimpleNamespace(id=1))
        forged = ApprovedTemplateText(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY, "Es un Fiat Uno.")
        with mock.patch.object(RV, "_APPROVED_TEMPLATE_CONTRACTS", Boom()):
            out = eng._validate_outbound_text(forged, ctx, None)
        self.assertNotEqual(out, str(forged))
        self.assertEqual(out, select_validation_fallback(COROLLA).text)

    def test_h04_rv03_lying_subclass_equality_is_refused_on_a_direct_call(self):
        class Liar(str):
            def __eq__(self, other):
                return True
            def __ne__(self, other):
                return False
            __hash__ = str.__hash__
        r = validate_response(Liar("Es un Fiat Uno."), COROLLA, vehicle_resolver=_resolver,
                              approved_template=ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY)
        self.assertTrue(r.fell_back)
        self.assertTrue(any(f.claim == RV.CLAIM_TEMPLATE_CONTRACT and not f.allowed
                            for f in r.findings))

    def test_h05_rv04_validator_output_is_always_an_exact_str(self):
        values = [CE._MOTORCYCLE_ADVISOR_REPLY, CE._MOTORCYCLE_HANDOFF_REPLY,
                  composed_text("Perfecto."), "Perfecto.", "", "   ",
                  ApprovedTemplateText(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY, "")]
        for v in values:
            with self.subTest(v=repr(v)[:30]):
                self.assertIs(type(validate_response(
                    v, NEW, vehicle_resolver=_resolver, approved_template=RV.template_tag_of(v)).text), str)
                self.assertIs(type(_env(v).text), str)
        eng = object.__new__(ConversationEngine)
        eng._build_canonical_facts = lambda ctx, state: NEW
        ctx = SimpleNamespace(thread=SimpleNamespace(id=1))
        self.assertIs(type(eng._validate_outbound_text(CE._MOTORCYCLE_ADVISOR_REPLY, ctx, None)), str)

    def test_h05b_a_failure_inside_the_envelope_never_delivers_the_value(self):
        declared = composed_text("Es un Fiat Uno.", claims=[
            StructuredClaim(DomainClaim.VEHICLE, ("Fiat", "Uno"))])
        # each value is one that REACHES the patched function
        for target, value in (("validate_response", "Es un Fiat Uno."),
                              ("_validate_structured_claim", declared),
                              ("select_validation_fallback", declared)):
            with self.subTest(target=target), \
                    mock.patch.object(RV, target, side_effect=RuntimeError("boom")):
                e = validate_envelope(value, COROLLA, vehicle_resolver=_resolver)
                self.assertNotEqual(e.text, "Es un Fiat Uno.")
                self.assertEqual(e.text, safe_failure_text())
                self.assertEqual(e.question_intents, frozenset())
                self.assertIs(type(e.text), str)

    def test_h06_only_vehicle_is_skipped_for_the_motorcycle_contract(self):
        for extra, claim in (("La revisión sale $10.000.", "PRICE"),
                             ("El auto está en Pilar.", "LOCATION"),
                             ("Te ofrezco las 15:00.", "AVAILABILITY"),
                             ("Tu turno quedó confirmado.", "BOOKING"),
                             ("Ya aceptaste el presupuesto.", "ACCEPTANCE")):
            with self.subTest(claim=claim):
                r = validate_response(extra, NEW, vehicle_resolver=_resolver,
                                      approved_template=ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY)
                self.assertTrue(any(f.claim == claim and not f.allowed for f in r.findings))


# ── I. no state from outbound prose ───────────────────────────────────────────────────

def _producer_engine(turn_text):
    e = object.__new__(ConversationEngine)
    e._live_plan = LiveResponsePlan(turn_id="t")
    e._turn_text = turn_text
    e._decision_log = lambda *a, **k: None
    state = SimpleNamespace(needs_human=False, last_stage="QUALIFYING",
                            current_focus_candidate_id=None, pending_fuzzy_catalog_key=None,
                            pending_turn_evidence_text=None)
    ctx = SimpleNamespace(state=state, candidates=[], thread=SimpleNamespace(id=1))
    return e, ctx, state


class I_NoStateFromOutboundProse(unittest.TestCase):

    def test_i01_reply_only_vehicle_never_arms(self):
        for reply in ("¿Te parece buena idea revisarlo antes de comprar?",
                      "¿La revisión sería para uno de los dos autos que estás viendo?",
                      "Sí, hacemos el servicio para un 2008 del 2014.",   # the customer said no car
                      "Perfecto, es un Fiat Uno."):
            with self.subTest(reply=reply):
                e, ctx, state = _producer_engine("Hola, quería hacer una consulta")
                e._register_canonical_vehicle_proposition(ctx, reply)
                self.assertIsNone(state.pending_fuzzy_catalog_key)
                self.assertFalse(e._live_plan.registered(PropositionId.CANONICAL_VEHICLE))

    def test_i02_customer_evidence_plus_a_claimed_reply_arms(self):
        e, ctx, state = _producer_engine("un 2008 del 2014")
        e._register_canonical_vehicle_proposition(
            ctx, "¡Hola! Sí, hacemos el servicio de revisión para un 2008 del 2014.")
        self.assertEqual(state.pending_fuzzy_catalog_key, "Peugeot||2008")

    def test_i03_customer_evidence_with_a_mere_mention_does_not(self):
        e, ctx, state = _producer_engine("quiero revisar uno de los autos de mi viejo")
        e._register_canonical_vehicle_proposition(ctx, "Perfecto, revisamos uno por uno.")
        self.assertIsNone(state.pending_fuzzy_catalog_key)

    def test_i04_ai_proposed_time_is_held_only_when_it_is_an_evaluated_slot(self):
        src = (_APP / "services" / "conversation_engine.py").read_text(encoding="utf-8")
        # R6-C4A: the producer no longer exists. No state is derived from model prose: the
        # body is discarded at `_apply_transactional_boundary` on the SCHEDULING route, and the
        # engine never parses the model's reply for a time or stores one.
        self.assertNotIn("state.preferred_time = ai_proposed_time", src)
        self.assertNotIn("_parse_scheduling_text([ai_reply_text]", src)
        self.assertIn("def _apply_transactional_boundary(", src)
        st = SimpleNamespace(last_visible_slots=json.dumps(["09:00", "10:30"]), last_offered_slots="not json")
        self.assertEqual(ConversationEngine._evaluated_slot_times(st), {"09:00", "10:30"})


# ── J. question recording ─────────────────────────────────────────────────────────────

class J_QuestionRecording(unittest.TestCase):

    def _engine(self, plan_included=True):
        e = object.__new__(ConversationEngine)
        e._r2_on = lambda: True
        e._decision_log = lambda *a, **k: None
        e._focus_candidate = lambda ctx: None
        e._is_current_focus = lambda s, f: False
        e._r2_cycle_key = lambda s: 1
        e.db = mock.MagicMock()
        plan = mock.MagicMock()
        plan.included.side_effect = lambda pid: (pid == PropositionId.PRIMARY_REPLY and plan_included)
        e._live_plan = plan
        return e

    def _record(self, e, text, envelope):
        ctx = SimpleNamespace(state=SimpleNamespace(), thread=SimpleNamespace(id=1))
        with mock.patch("app.services.outstanding_question.record_asked",
                        return_value=(None, False)) as rec:
            e._record_delivered_questions(ctx, text=text, wa_message_id="wamid.x", envelope=envelope)
        return rec

    def test_j01_an_undeclared_location_question_is_not_recorded(self):
        e = self._engine()
        rec = self._record(e, "¿En qué zona está el auto?", _env("Es un Fiat Uno.", QUOTED))
        rec.assert_not_called()
        rec = self._record(e, RV._FALLBACK_REPLY, None)
        rec.assert_not_called()

    def test_j02_a_declared_question_is_recorded_with_its_provenance(self):
        e = self._engine()
        rec = self._record(e, "x", _env("Es un Fiat Uno.", NEW))
        self.assertEqual(rec.call_args.kwargs["asked_by"], "DETERMINISTIC_TEMPLATE")
        rec = self._record(e, "x", _env("Dale. ¿En qué zona está el auto?", NEW))
        self.assertEqual(rec.call_args.kwargs["asked_by"], "REPLY_MODEL")

    def test_j03_a_suppressed_primary_declares_nothing(self):
        e = self._engine(plan_included=False)
        rec = self._record(e, "x", _env("Dale. ¿En qué zona está el auto?", NEW))
        rec.assert_not_called()


# ── K. structure ──────────────────────────────────────────────────────────────────────

class K_Structure(unittest.TestCase):
    SRC = (_APP / "services" / "conversation_engine.py").read_text(encoding="utf-8")

    def _method(self, name):
        tree = ast.parse(self.SRC)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(self.SRC, node)
        raise AssertionError(name)

    def test_k01_the_wrapper_uses_the_envelope_and_cannot_return_raw_text(self):
        body = self._method("_validate_outbound_text")
        self.assertIn("validate_envelope(", body)
        self.assertNotIn("return text\n", body)
        self.assertIn("safe_failure_text()", body)

    def test_k02_the_vehicle_producer_reads_customer_evidence(self):
        body = self._method("_register_canonical_vehicle_proposition")
        self.assertIn("lookup_vehicle(inbound)", body)
        self.assertNotIn("lookup_vehicle(text)", body)
        self.assertIn("claim_shaped", body)

    def test_k03_the_recorder_reads_the_declared_contract(self):
        body = self._method("_record_delivered_questions")
        self.assertIn("_primary_declares(", body)
        i = body.index("_primary_declares(")
        j = body.index("_reply_already_asks_location(")
        self.assertLess(i, j)
        self.assertIn("outstanding_question_not_declared", body[j:j + 300])

    def test_k04_the_claim_resolver_is_the_one_the_validator_gets(self):
        body = self._method("_validate_outbound_text")
        self.assertIn("vehicle_resolver=self._reply_vehicle_claim_resolver", body)


if __name__ == "__main__":
    unittest.main()

"""R6-C1 — the claim frames corrected after the R6 integrated independent review.

R6IRV-S01  a time RANGE is business-hours information only when nothing anchors it to a
           concrete appointment (a date, hoy/mañana, a definite weekday, or an offer /
           booking / visit / inspection / arrival action outside a general-hours frame). A
           concrete window is an availability claim, and both endpoints must be evaluated
           ScheduleService slots.
R6IRV-S02  a catalogue model is a vehicle claim — whatever its case or position — when it is
           the POSSESSIVE OBJECT of an inspection/booking action in its clause, or a
           left-dislocated TOPIC resumed by an action clitic. No catalogue-word matcher.
R6IRV-P01  a question no longer skips validation: its transactional propositions (concrete
           price, concrete time / window, vehicle or place inside an inspection/booking
           proposal) are checked by claim type; information-seeking questions still pass.
R6IRV-T01  a subclass of ApprovedTemplateText / ComposedText is AI_COMPOSED: no template id,
           no exemption, no declaration, no question intent.
R6IRV-P02  (accepted launch debt, pinned) AI prose: a contradicting sentence is dropped and an
           invented ASSERTED amount is rewritten to the canonical quote; the false content is
           never delivered; declared deterministic responses still refuse the whole value.
R6IRV-P03  (accepted low launch debt, pinned) the subjectless locative form is not checked.

Every example runs through the real validator with the real CE claim resolver and the
production zone lexicon. Ordinary-prose protections from R6 are asserted again here.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import unittest
from datetime import date

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app.services.conversation_engine as CE  # noqa: E402
import app.services.response_validator as RV  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.services.response_validator import (  # noqa: E402
    ApprovedTemplateId, ApprovedTemplateText, CanonicalFacts, ComposedText, DomainClaim,
    QuestionIntent, ResponseDeclaration, ResponseProvenance, StateUpdate, StructuredClaim,
    composed_text, select_validation_fallback, validate_envelope, validate_response)

_ZONES = tuple(
    v for line in (_ROOT / "tests" / "fixtures" / "r6_zone_names.txt").read_text(
        encoding="utf-8").splitlines() if line and not line.startswith("#")
    for v in line.split("|") if v)
_ENG = object.__new__(ConversationEngine)


def _resolver(fragment):
    return ConversationEngine._reply_vehicle_claim_resolver(_ENG, fragment)


def F(**kw):
    return CanonicalFacts(known_zone_names=_ZONES, **kw)


_COROLLA = dict(vehicle_marca="Toyota", vehicle_modelo="Corolla")
_PALERMO = dict(inspection_zone_group="CABA", inspection_zone_detail="Palermo")
NEW = F()
COROLLA = F(**_COROLLA)
GOL = F(vehicle_marca="Volkswagen", vehicle_modelo="Gol")
LOCATED = F(**_PALERMO)
BOTH = F(**_COROLLA, **_PALERMO)
QUOTED = F(**_COROLLA, **_PALERMO, quote_total=140000, quote_base=125000, quote_viaticos=15000)
# R6-C2 (R6C1RV-S01): the evaluated slots belong to ONE evaluated date. "Today" on the
# business calendar is Wednesday 2026-11-11 and ScheduleService evaluated Thursday
# 2026-11-12, so "el jueves" / "mañana" name that date; any other day is refused. (R6-C1
# had no date here and so pinned day-blind acceptance.)
_TODAY = date(2026, 11, 11)
_EVALUATED = date(2026, 11, 12)
SLOTS = F(**_COROLLA, availability_checked=True, offered_slots=("10:00", "15:00", "15:30"),
          availability_date=_EVALUATED, business_today=_TODAY)
CHECKED_NO_SLOTS = F(**_COROLLA, availability_checked=True)
# R6-C3 (R6C2RV-S01): a reply that proposes an inspection or a visit "mañana" / "el jueves" is a
# scheduling proposition: it needs the evaluation of that day. These worlds are GOL / LOCATED with
# ScheduleService's evaluation of Thursday 12 (= mañana = el jueves on Wednesday 11).
_EVAL_THU = dict(availability_checked=True, offered_slots=("10:00", "15:00", "15:30"),
                 availability_date=_EVALUATED, business_today=_TODAY)
GOL_EVALUATED = F(vehicle_marca="Volkswagen", vehicle_modelo="Gol", **_EVAL_THU)
LOCATED_EVALUATED = F(**_PALERMO, **_EVAL_THU)
ALL_UNSUPPORTED = {"new": NEW, "corolla": COROLLA, "located": LOCATED, "both": BOTH,
                   "quoted": QUOTED}


def _env(value, facts):
    return validate_envelope(value, facts, vehicle_resolver=_resolver,
                             location_question=CE._reply_already_asks_location)


def _refused(test, text, facts, claim, world=""):
    e = _env(text, facts)
    test.assertTrue(e.fell_back, f"{world}: delivered {e.text!r}")
    test.assertEqual(e.text, select_validation_fallback(facts).text)
    test.assertIn(claim, {f.claim for f in e.findings if not f.allowed}, e.findings)
    return e


def _kept(test, text, facts, world=""):
    e = _env(text, facts)
    test.assertFalse(e.fell_back, f"{world}: refused {e.findings}")
    test.assertEqual(e.text, text, world)
    test.assertIs(e.provenance, ResponseProvenance.AI_COMPOSED)
    return e


# ── S01: concrete appointment windows ────────────────────────────────────────────────

class S01_ConcreteWindows(unittest.TestCase):
    CONCRETE = [
        "Te esperamos el jueves de 15:00 a 15:30.",            # reviewer, weekday + range
        "Podemos ir mañana entre 10:00 y 11:00.",              # reviewer, mañana + range
        "Te ofrezco el viernes de 10:00 a 11:00.",             # offer + weekday
        "Te esperamos el 14/11 de 15:00 a 15:30.",             # date + range
        "Pasamos el 20 de noviembre entre 10:00 y 11:00.",     # date in words
        "Podemos pasar hoy entre 10:00 y 11:00.",              # hoy
        "El turno sería de 15:00 a 15:30.",                    # booking noun, no general frame
        "Vamos de 15:00 a 15:30 a ver el auto.",               # arrival action
        "La inspección la hacemos entre 10:00 y 11:00.",       # inspection
        "Te espero jueves de 15:00 a 15:30.",                  # bare weekday, no general frame
        "El próximo lunes te esperamos de 10:00 a 11:00.",     # definite weekday
        "Este viernes atendemos de 15:00 a 15:30.",            # definite weekday beats "atendemos"
    ]
    GENERAL = [
        "Trabajamos de lunes a viernes de 9:00 a 18:00.",
        "Trabajamos de lunes a sábado de 9:00 a 18:00.",
        "Atendemos de lunes a sábado de 9:00 a 13:00 y coordinamos según disponibilidad.",
        "De 9:00 a 18:00 atendemos consultas.",
        "Trabajamos de 9:00 a 18:00.",
        "Atendemos entre 9:00 y 13:00.",
        "Abrimos 9:00 - 18:00.",
        "Los sábados atendemos de 9:00 a 13:00.",
        "Nuestro horario es de 9:00 a 18:00.",
        "Trabajamos de lunes a viernes de 9:00 a 18:00 y sábado de 9:00 a 13:00.",
        "Hacemos revisiones de lunes a viernes de 9:00 a 18:00.",
        "Por la mañana, de 9:00 a 12:00, atendemos consultas.",
        "Todos los días de 9:00 a 18:00 respondemos por acá.",
    ]

    def test_s01_01_a_concrete_window_is_refused_without_evaluated_slots(self):
        for s in self.CONCRETE:
            for world, facts in ALL_UNSUPPORTED.items():
                with self.subTest(s=s, world=world):
                    _refused(self, s, facts, "AVAILABILITY", world)

    def test_s01_02_general_hours_remain_information(self):
        for s in self.GENERAL:
            for world, facts in {**ALL_UNSUPPORTED, "slots": SLOTS}.items():
                with self.subTest(s=s, world=world):
                    e = _kept(self, s, facts, world)
                    self.assertTrue(any(f.claim == "AVAILABILITY" and f.action == RV.ACTION_DIAGNOSTIC
                                        for f in e.findings), e.findings)

    def test_s01_03_a_window_whose_endpoints_are_evaluated_slots_is_supported(self):
        for s in ("Te esperamos el jueves de 15:00 a 15:30.", "El turno sería de 15:00 a 15:30.",
                  "Te espero jueves de 15:00 a 15:30."):
            with self.subTest(s=s):
                _kept(self, s, SLOTS)

    def test_s01_04_an_endpoint_outside_the_evaluated_slots_is_refused(self):
        for s in ("Podemos ir mañana entre 10:00 y 11:00.",     # 11:00 not evaluated
                  "Te esperamos el jueves de 15:00 a 16:00.",   # 16:00 not evaluated
                  "Te ofrezco el viernes de 9:00 a 10:00."):    # 9:00 not evaluated
            with self.subTest(s=s):
                _refused(self, s, SLOTS, "AVAILABILITY")

    def test_s01_05_an_evaluation_with_no_slots_supports_no_window(self):
        _refused(self, "Te esperamos el jueves de 15:00 a 15:30.", CHECKED_NO_SLOTS, "AVAILABILITY")

    def test_s01_06_the_frames_are_structural_not_sentence_lists(self):
        """The examples are not in the validator: only role lexicons (weekday, month, action)."""
        src = (_ROOT / "backend" / "app" / "services" / "response_validator.py").read_text()
        tree = ast.parse(src)
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                and isinstance(n.body[0], ast.Expr)}
        literals = {n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs}
        for s in self.CONCRETE + self.GENERAL + ["15:00 a 15:30", "10:00 y 11:00"]:
            self.assertFalse(any(s in lit for lit in literals), s)
        names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets
                 if isinstance(t, ast.Name)}
        for frame in ("_GENERAL_HOURS_RE", "_DATE_ANCHOR_RE", "_DEFINITE_WEEKDAY_RE",
                      "_TRANSACTIONAL_STEMS", "_TRANSACTIONAL_WORDS"):
            self.assertIn(frame, names)

    def test_s01_07_a_confirmed_booking_may_restate_its_window(self):
        # R6-C2: only the committed booking's OWN date and times may be restated.
        _kept(self, "Te esperamos el jueves de 15:00 a 15:30.",
              F(**_COROLLA, booking_confirmed=True, booked_date=_EVALUATED,
                booked_times=("15:00", "15:30"), business_today=_TODAY))


# ── S02: lowercase / sentence-initial models in a vehicle proposition ────────────────

class S02_VehicleFrames(unittest.TestCase):
    CLAIMS = [
        "Perfecto, revisamos tu gol mañana.",            # reviewer: lowercase possessive object
        "Gol, perfecto, lo revisamos.",                  # reviewer: sentence-initial topic
        "Perfecto, coordinamos la revisión de tu gol.",  # possessive object of a booking action
        "Mañana inspeccionamos su gol.",
        "Te reservo el turno para tu gol.",
        "gol, genial, lo revisamos el jueves.",          # lowercase topic
        "Uno, perfecto, lo revisamos.",                  # the frame, not the word, decides
    ]
    ORDINARY = [
        "Uno nunca sabe.",
        "Uno nunca sabe lo que puede tener un usado, por eso conviene revisarlo.",
        "Revisamos todo uno por uno.",
        "Podemos revisar primero uno de los dos autos y después el otro.",
        "Revisamos carrocería, motor, frenos y suspensión, punto por punto.",
        "Lo vemos punto por punto.",
        "¡Buena idea! Revisarlo antes de comprar te ahorra sorpresas.",
        "Me parece buena idea.",
        "Me gusta tu idea, coordinamos la revisión cuando quieras.",
        "Punto por punto, lo revisamos todo.",
        "Revisamos el auto, tu idea nos parece buena.",     # possessive, action in ANOTHER clause
        "Uno, dos y tres: revisamos todo.",                 # topic + comma, no resumptive clitic
        "Perfecto, uno por uno, lo revisamos todo.",        # clitic + action, model not initial
        "A la brevedad uno de nuestros asesores se estará contactando con vos.",
    ]

    def test_s02_01_the_reviewer_examples_are_claims(self):
        for s in self.CLAIMS:
            with self.subTest(s=s):
                m = _resolver(s)
                self.assertIsNotNone(m, s)
                self.assertTrue(m.claim_shaped, m)

    def test_s02_02_a_wrong_vehicle_is_refused_in_every_world(self):
        for s in self.CLAIMS:
            if s.lower().startswith("uno"):
                continue
            for world, facts in ALL_UNSUPPORTED.items():
                with self.subTest(s=s, world=world):
                    _refused(self, s, facts, "VEHICLE", world)

    def test_s02_03_the_same_frame_with_the_canonical_vehicle_is_kept(self):
        for s in self.CLAIMS:
            if s.lower().startswith("uno"):
                continue
            with self.subTest(s=s):
                _kept(self, s, GOL_EVALUATED)         # R6-C3: its day needs the evaluation

    def test_s02_04_ordinary_prose_remains_a_mention(self):
        for s in self.ORDINARY:
            m = _resolver(s)
            with self.subTest(s=s):
                self.assertTrue(m is None or not m.claim_shaped, m)
            for world, facts in ALL_UNSUPPORTED.items():
                with self.subTest(s=s, world=world):
                    _kept(self, s, facts, world)

    def test_s02_05_no_catalogue_word_matcher_was_added(self):
        src = (_ROOT / "backend" / "app" / "services" / "reply_claim_shape.py").read_text()
        tree = ast.parse(src)
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                and isinstance(n.body[0], ast.Expr)}
        literals = {n.value.strip().lower() for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs}
        self.assertEqual(literals & {"gol", "uno", "punto", "idea", "corolla", "fiat"}, set())


# ── P01: question-form transactional propositions ────────────────────────────────────

class P01_QuestionPropositions(unittest.TestCase):

    def test_p01_01_an_invented_price_in_a_question_is_refused(self):
        s = "¿Te parece bien $12.345 por la revisión?"
        for world, facts in ALL_UNSUPPORTED.items():
            with self.subTest(world=world):
                e = _refused(self, s, facts, "PRICE", world)
                self.assertNotIn("12.345", e.text)
        # never rewritten into a proposal of the canonical amount either
        self.assertNotIn("140.000", _env(s, QUOTED).text)

    def test_p01_02_the_canonical_price_in_a_question_is_kept(self):
        _kept(self, "¿Te parece bien $140.000 por la revisión?", QUOTED)

    def test_p01_03_an_unevaluated_slot_in_a_question_is_refused(self):
        s = "¿Te reservo el jueves a las 11:30?"
        for world, facts in {**ALL_UNSUPPORTED, "slots": SLOTS}.items():
            with self.subTest(world=world):
                _refused(self, s, facts, "AVAILABILITY", world)
        _kept(self, "¿Te reservo el jueves a las 15:00?", SLOTS)

    def test_p01_04_a_non_canonical_window_in_a_question_is_refused(self):
        s = "¿Podemos ir mañana entre 10:00 y 11:00?"
        for world, facts in {**ALL_UNSUPPORTED, "slots": SLOTS}.items():
            with self.subTest(world=world):
                _refused(self, s, facts, "AVAILABILITY", world)

    def test_p01_05_a_wrong_vehicle_in_a_booking_question_is_refused(self):
        s = "¿Coordinamos la revisión de tu Fiat Uno 2015?"
        for world, facts in ALL_UNSUPPORTED.items():
            with self.subTest(world=world):
                _refused(self, s, facts, "VEHICLE", world)
        _kept(self, "¿Coordinamos la revisión de tu Toyota Corolla?", BOTH)

    def test_p01_06_a_wrong_place_in_a_visit_question_is_refused(self):
        for s in ("¿Vamos hasta Pilar mañana?", "¿Coordinamos la revisión en Pilar?"):
            for world, facts in (("located", LOCATED), ("both", BOTH), ("new", NEW)):
                with self.subTest(s=s, world=world):
                    _refused(self, s, facts, "LOCATION", world)
        _kept(self, "¿Vamos hasta Palermo mañana?", LOCATED_EVALUATED)    # R6-C3: day evaluated

    def test_p01_07_information_seeking_questions_are_preserved(self):
        cases = {
            "¿Es un Peugeot 2008?": (NEW, LOCATED),
            "¿Es un Fiat Uno?": (NEW, LOCATED),
            "¿Tu auto es un Volkswagen Gol?": (NEW,),
            "¿La revisión es para un Peugeot 2008?": (NEW,),
            "¿El auto está en Pilar?": (NEW, COROLLA),
            "¿En qué zona está el auto?": (NEW, COROLLA),
            "¿Hay disponibilidad el jueves?": (NEW, COROLLA),
            "¿A qué hora te queda bien?": (NEW, SLOTS),
            "¿Cuánto salía?": (NEW, QUOTED),
            "¿Te parece buena idea revisarlo antes de comprar?": (NEW, COROLLA),
            "¿La revisión sería para uno de los dos autos que estás viendo?": (NEW, COROLLA),
            "¿Querés que coordinemos la revisión?": (NEW, BOTH),
            "¿Trabajan de lunes a viernes de 9:00 a 18:00?": (NEW,),
        }
        for s, worlds in cases.items():
            for facts in worlds:
                with self.subTest(s=s, vehicle=facts.vehicle_modelo, place=facts.inspection_zone_detail):
                    e = _env(s, facts)
                    self.assertFalse(e.fell_back, e.findings)
                    self.assertEqual(e.text, s)

    def test_p01_08_a_question_mark_alone_never_carries_a_false_proposition(self):
        """Every refused statement stays refused when it is phrased as a question."""
        pairs = [("Te ofrezco el turno de mañana a las 10:30.", COROLLA, "AVAILABILITY"),
                 ("Te esperamos el jueves de 15:00 a 15:30.", COROLLA, "AVAILABILITY"),
                 ("La revisión sale $12.345.", NEW, "PRICE"),
                 ("Coordinamos la revisión de tu Fiat Uno 2015.", COROLLA, "VEHICLE"),
                 ("Vamos hasta Pilar mañana.", LOCATED, "LOCATION")]
        for s, facts, claim in pairs:
            q = "¿" + s.rstrip(".") + "?"
            for variant in (s, q, s.rstrip(".") + "?", "¿" + s):
                with self.subTest(variant=variant):
                    _refused(self, variant, facts, claim)

    def test_p01_09_the_blanket_question_skip_is_gone(self):
        src = (_ROOT / "backend" / "app" / "services" / "response_validator.py").read_text()
        fn = src[src.index("def validate_response("):src.index("# ── R6: plan-appropriate")]
        self.assertNotIn("if _is_question(sentence):\n            kept.append(sentence)\n            continue", fn)

    def test_p01_10_questions_cannot_assert_completed_state(self):
        for s in ("¿Ya quedó confirmado el turno?", "¿Ya aceptaste el presupuesto?"):
            with self.subTest(s=s):
                self.assertEqual(_env(s, COROLLA).text, s)

    def test_p01_11_a_failing_question_never_records_an_intent_of_its_own(self):
        e = _env("¿Vamos hasta Pilar mañana? ¿En qué zona está el auto?", COROLLA)
        self.assertNotIn("Pilar", e.text)


# ── T01: provenance of subclasses ────────────────────────────────────────────────────

class T01_SubclassProvenance(unittest.TestCase):

    def test_t01_a_subclass_of_a_trusted_type_is_ai_composed(self):
        class TemplateSub(ApprovedTemplateText):
            pass

        class ComposedSub(ComposedText):
            pass
        text = str(CE._MOTORCYCLE_ADVISOR_REPLY)
        decl = ResponseDeclaration(
            claims=frozenset({StructuredClaim(DomainClaim.VEHICLE, ("Toyota", "Corolla"))}),
            question_intents=frozenset({QuestionIntent.INSPECTION_LOCATION}),
            state_updates=frozenset({StateUpdate.RECORD_OUTSTANDING_QUESTION}))
        values = [TemplateSub.__new__(TemplateSub, ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY, text),
                  ComposedSub("¿En qué zona está el auto?", decl)]
        for v in values:
            for facts in (NEW, BOTH):
                with self.subTest(kind=type(v).__name__, located=facts.location_known):
                    e = _env(v, facts)
                    self.assertIs(e.provenance, ResponseProvenance.AI_COMPOSED)
                    self.assertIsNone(e.template_id)
                    self.assertFalse(e.declared)
                    if facts.location_known:
                        self.assertEqual(e.question_intents, frozenset())
                        self.assertEqual(e.state_updates, frozenset())
                    # never an INSPECTION_LOCATION intent DECLARED by the subclass itself:
                    # at most the AI-prose probe's, and only while the place is missing
                    self.assertTrue(e.question_intents <= {QuestionIntent.INSPECTION_LOCATION})
                    self.assertNotIn(QuestionIntent.VEHICLE_IDENTITY, e.question_intents)

    def test_t01_b_a_template_subclass_gets_no_exemption(self):
        class TemplateSub(ApprovedTemplateText):
            pass
        v = TemplateSub.__new__(TemplateSub, ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY,
                                "Perfecto, revisamos tu Fiat Uno 2015.")
        e = _env(v, COROLLA)
        self.assertTrue(e.fell_back)                     # validated in full: refused
        self.assertIsNone(e.template_id)
        self.assertEqual(e.text, select_validation_fallback(COROLLA).text)
        self.assertIn("VEHICLE", {f.claim for f in e.findings if not f.allowed})

    def test_t01_c_the_exact_types_keep_their_provenance(self):
        self.assertIs(_env(CE._MOTORCYCLE_ADVISOR_REPLY, NEW).provenance,
                      ResponseProvenance.APPROVED_TEMPLATE)
        self.assertIs(_env(composed_text("Genial, seguimos."), NEW).provenance,
                      ResponseProvenance.DETERMINISTIC_COMPOSED)


# ── P02 / P03: accepted launch debt, pinned ──────────────────────────────────────────

class P02_P03_AcceptedDebtPinned(unittest.TestCase):

    def test_p02_01_ai_prose_drops_the_contradicting_sentence_only(self):
        s = "La revisión incluye más de 250 puntos. Revisamos tu Fiat Uno 2015."
        e = _env(s, COROLLA)
        self.assertEqual(e.text, "La revisión incluye más de 250 puntos.")
        self.assertNotIn("Uno", e.text)
        self.assertEqual(e.state_updates, frozenset())

    def test_p02_02_an_asserted_invented_amount_is_rewritten_never_delivered(self):
        e = _env("La revisión sale $12.345.", QUOTED)
        self.assertNotIn("12.345", e.text)
        self.assertIn("140.000", e.text)

    def test_p02_03_declared_deterministic_responses_refuse_the_whole_value(self):
        v = composed_text("La revisión incluye más de 250 puntos. ¿Seguimos?",
                          claims=(StructuredClaim(DomainClaim.VEHICLE, ("Fiat", "Uno")),))
        e = _env(v, COROLLA)
        self.assertTrue(e.fell_back)
        self.assertEqual(e.text, select_validation_fallback(COROLLA).text)

    def test_p02_04_no_state_is_derived_from_the_dropped_content(self):
        r = validate_response("Perfecto. El auto está en Pilar.", BOTH, vehicle_resolver=_resolver)
        self.assertEqual(r.text, "Perfecto.")
        self.assertFalse(r.fell_back)

    def test_p03_01_the_subjectless_locative_form_is_a_known_limit(self):
        """R6IRV-P03, accepted low launch debt: "Te lo revisamos en Pilar mañana." to a Palermo
        customer is not checked (no inspection noun and no presence/motion token). Pinned so a
        later response-policy milestone changes it deliberately."""
        s = "Te lo revisamos en Pilar mañana."
        # R6-C3: the LOCATION limit is unchanged — with tomorrow evaluated the reply still
        # reaches the customer — but its day is now read: without an evaluation it is refused.
        self.assertEqual(_env(s, LOCATED_EVALUATED).text, s)
        self.assertNotEqual(_env(s, LOCATED).text, s)


class C01_ApprovedFallbackCopy(unittest.TestCase):

    def test_c01_the_owner_approved_no_question_copy(self):
        """R6IRV-C01 — APPROVED by the owner (R6-C1 milestone ruling 1)."""
        self.assertEqual(select_validation_fallback(BOTH).text,
                         "Gracias por tu mensaje. Cualquier otra consulta, escribime por acá.")
        self.assertEqual(RV.safe_failure_text(),
                         "Gracias por tu mensaje. Cualquier otra consulta, escribime por acá.")


if __name__ == "__main__":
    unittest.main()

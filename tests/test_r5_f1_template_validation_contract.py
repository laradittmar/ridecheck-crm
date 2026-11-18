"""R5-F1 — the structured boundary between approved templates and claim reparsing.

B  ordinary Spanish inside an approved no-claim template survives;
C  real vehicle claims are still detected and a conflicting one is still rejected;
D  the exemption cannot be obtained by anything but a registered, exact, typed template;
E  every deterministic customer-facing constant survives the REAL CE validator under no
   focus, a car focus and a motorcycle focus.

The production path (ConversationEngine → validator → gate) is
`test_r5_f1_motorcycle_handoff_production_path.py`.
"""
from __future__ import annotations

import ast
import copy
import enum
import os
import pathlib
import pickle
import sys
import types
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("DATABASE_URL", "sqlite://")

import app.services.conversation_engine as CE  # noqa: E402
import app.services.response_validator as RV  # noqa: E402
from app.services.booking_acknowledgement import BOOKING_HANDOFF_TEXT  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.services.response_validator import (CLAIM_TEMPLATE_CONTRACT, CLAIM_VEHICLE,  # noqa: E402
                                             ApprovedTemplateContract, ApprovedTemplateId,
                                             ApprovedTemplateText, CanonicalFacts,
                                             TemplateValidationPolicy, _FALLBACK_REPLY,
                                             template_tag_of, validate_response)

_APP = _ROOT / "backend" / "app"
_ENGINE = object.__new__(ConversationEngine)


def _resolver(fragment):
    return ConversationEngine._reply_vehicle_resolver(_ENGINE, fragment)


FOCI = {
    "no_focus": CanonicalFacts(),
    "car_focus": CanonicalFacts(vehicle_marca="Toyota", vehicle_modelo="Corolla"),
    "moto_focus": CanonicalFacts(vehicle_marca="Honda", vehicle_modelo="CB500"),
}

ORDINARY_SPANISH = (
    "A la brevedad uno de nuestros asesores se estará contactando con vos.",
    "Los revisamos uno por uno.",
    "Te contacto para uno de los dos autos.",
    "Buena idea, te contactamos a la brevedad.",
)
#: R6-C3 (R6C2RV-S01): "a las 10 en punto" names a clock time. It is still never a VEHICLE claim
#: ("punto" is a mention), but since R6-C3 every clock form is read, so the sentence is an
#: arrival-time proposition: refused without a ScheduleService evaluation, kept when 10:00 is an
#: evaluated slot (the reviewer's stated direction, R6-C2 review §16 item 4). It moves out of the
#: no-claim template list (b03) into its own test (b05); b04 still covers it as plain text.
CLOCK_PHRASE = "Te esperamos a las 10 en punto."
ORDINARY_SPANISH_PLAIN = ORDINARY_SPANISH + (CLOCK_PHRASE,)


def _validate(text, facts, **kw):
    return validate_response(text, facts, vehicle_resolver=_resolver, **kw)


def _contract(text, *, tid=ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY,
              policy=TemplateValidationPolicy.NO_VEHICLE_CLAIM):
    return ApprovedTemplateContract(template_id=tid, text=text, policy=policy, reason="test")


def _registry(*contracts):
    return types.MappingProxyType({c.template_id: c for c in contracts})


def _refusals(result):
    return [f for f in result.findings if f.claim == CLAIM_TEMPLATE_CONTRACT and not f.allowed]


def _grants(result):
    return [f for f in result.findings if f.claim == CLAIM_TEMPLATE_CONTRACT and f.allowed]


# ── B. ordinary Spanish in an approved no-claim template ─────────────────────────────

class B_OrdinarySpanish(unittest.TestCase):

    def test_b01_the_real_motorcycle_handoff_survives_every_focus(self):
        for name, facts in FOCI.items():
            with self.subTest(focus=name):
                r = _validate(CE._MOTORCYCLE_ADVISOR_REPLY, facts,
                              approved_template=template_tag_of(CE._MOTORCYCLE_ADVISOR_REPLY))
                self.assertEqual(r.text, CE._MOTORCYCLE_ADVISOR_REPLY)
                self.assertEqual(len(_grants(r)), 1)
                self.assertFalse(any(f.claim == CLAIM_VEHICLE for f in r.findings))

    def test_b02_each_phrase_is_read_as_a_catalogue_vehicle_by_the_generic_resolver(self):
        # PRECONDITION, not a goal: without it B03 would prove nothing. This is the R5-F1
        # class (AI-composed prose matcher debt), recorded in the closeout, not fixed here.
        for phrase in ORDINARY_SPANISH:
            with self.subTest(phrase=phrase):
                self.assertIsNotNone(_resolver(phrase))

    def test_b03_an_approved_no_claim_template_with_each_phrase_survives_every_focus(self):
        for phrase in ORDINARY_SPANISH:
            reg = _registry(_contract(phrase))
            tagged = ApprovedTemplateText(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY, phrase)
            with mock.patch.object(RV, "_APPROVED_TEMPLATE_CONTRACTS", reg):
                for name, facts in FOCI.items():
                    with self.subTest(phrase=phrase, focus=name):
                        r = _validate(tagged, facts, approved_template=template_tag_of(tagged))
                        self.assertEqual(r.text, phrase)
                        self.assertEqual(_refusals(r), [])

    def test_b04_the_same_phrase_without_the_contract_is_still_fully_validated(self):
        # The policy, not a word list, is what protects the template: plain text keeps the
        # pre-R5-F1 behaviour exactly. No ordinary word was blacklisted.
        for phrase in ORDINARY_SPANISH_PLAIN:
            with self.subTest(phrase=phrase):
                r = _validate(phrase, FOCI["moto_focus"])
                self.assertEqual(r.text, _FALLBACK_REPLY)
                self.assertTrue(any(f.claim == CLAIM_VEHICLE and not f.allowed
                                    for f in r.findings))


    def test_b05_a_clock_phrase_in_an_approved_template_is_a_time_proposition(self):
        reg = _registry(_contract(CLOCK_PHRASE))
        tagged = ApprovedTemplateText(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY, CLOCK_PHRASE)
        with mock.patch.object(RV, "_APPROVED_TEMPLATE_CONTRACTS", reg):
            for name, facts in FOCI.items():
                with self.subTest(focus=name, evaluated=False):
                    r = _validate(tagged, facts, approved_template=template_tag_of(tagged))
                    self.assertNotEqual(r.text, CLOCK_PHRASE)
                    self.assertTrue(any(f.claim == "AVAILABILITY" and not f.allowed for f in r.findings))
                    self.assertFalse(any(f.claim == CLAIM_VEHICLE and not f.allowed for f in r.findings))
                    self.assertEqual(_refusals(r), [])
                evaluated = CanonicalFacts(vehicle_marca=facts.vehicle_marca,
                                           vehicle_modelo=facts.vehicle_modelo,
                                           availability_checked=True, offered_slots=("10:00",))
                with self.subTest(focus=name, evaluated=True):
                    r = _validate(tagged, evaluated, approved_template=template_tag_of(tagged))
                    self.assertEqual(r.text, CLOCK_PHRASE)
                    self.assertEqual(_refusals(r), [])


# ── C. real vehicle claims are still claims ──────────────────────────────────────────

class C_RealVehicleClaims(unittest.TestCase):

    CLAIMS = (
        ("Es un Fiat Uno.", ("Fiat", "Uno")),
        ("Tengo un Uno 2015.", ("Fiat", "Uno")),
        ("Es un Fiat Punto.", ("Fiat", "Punto")),
        ("Es un Fiat Idea.", ("Fiat", "Idea")),
        ("Es un Toyota Corolla.", ("Toyota", "Corolla")),
    )

    def test_c01_the_generic_resolver_still_names_each_catalogue_vehicle(self):
        for text, (marca, modelo) in self.CLAIMS:
            with self.subTest(text=text):
                hit = _resolver(text)
                self.assertIsNotNone(hit)
                self.assertEqual((hit.marca, hit.modelo), (marca, modelo))

    def test_c02_a_claim_conflicting_with_the_motorcycle_focus_is_rejected(self):
        for text, _mm in self.CLAIMS:
            with self.subTest(text=text):
                r = _validate(text, FOCI["moto_focus"])
                self.assertEqual(r.text, _FALLBACK_REPLY)
                self.assertTrue(any(f.claim == CLAIM_VEHICLE and not f.allowed
                                    for f in r.findings))

    def test_c03_car_focus_keeps_its_own_vehicle_and_rejects_another(self):
        self.assertEqual(_validate("Es un Toyota Corolla.", FOCI["car_focus"]).text,
                         "Es un Toyota Corolla.")
        for text in ("Es un Fiat Uno.", "Es un Fiat Punto.", "Es un Fiat Idea."):
            with self.subTest(text=text):
                self.assertEqual(_validate(text, FOCI["car_focus"]).text, _FALLBACK_REPLY)

    def test_c04_no_focus_rejects_every_catalogue_claim(self):
        for text, _mm in self.CLAIMS:
            with self.subTest(text=text):
                self.assertEqual(_validate(text, FOCI["no_focus"]).text, _FALLBACK_REPLY)

    def test_c05_a_tagged_template_whose_words_assert_a_vehicle_is_still_rejected(self):
        # The tag cannot be lent to other words: text ≠ contract → refused → full check.
        for text, _mm in self.CLAIMS:
            forged = ApprovedTemplateText(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY, text)
            with self.subTest(text=text):
                r = _validate(forged, FOCI["moto_focus"], approved_template=template_tag_of(forged))
                self.assertEqual(r.text, _FALLBACK_REPLY)
                self.assertEqual(len(_refusals(r)), 1)

    def test_c06_honda_cb500_is_not_a_reply_catalogue_claim_preexisting_gap(self):
        """PRE-EXISTING, not introduced or changed here (identical on f16b1f3): the reply
        resolver is the car catalogue, so a motorcycle named in prose is not read as a
        claim. The motorcycle extractor (`_extract_motorcycle_make_model`) reads customer
        input after a motorcycle keyword; it is not a claim resolver. Widening claim
        detection is vehicle-recognition work outside this milestone. Recorded in the
        closeout. When it is fixed, this assertion is expected to be replaced."""
        self.assertIsNone(_resolver("Es una Honda CB500."))
        self.assertIsNone(_resolver("Tu moto es una Honda CB500."))


# ── D. the exemption is closed ───────────────────────────────────────────────────────

class D_BypassIsolation(unittest.TestCase):

    TEXT = CE._MOTORCYCLE_ADVISOR_REPLY

    def _assert_full(self, r, *, refused=1):
        self.assertEqual(r.text, _FALLBACK_REPLY, "the exemption must not be granted")
        self.assertEqual(len(_refusals(r)), refused)
        self.assertEqual(_grants(r), [])

    def test_d01_plain_text_with_the_exact_words_gets_nothing(self):
        # AI-composed text is a plain str. Even word-for-word equal, it carries no tag.
        plain = str(self.TEXT)
        self.assertIs(type(plain), str)
        self.assertIsNone(template_tag_of(plain))
        r = _validate(plain, FOCI["moto_focus"], approved_template=template_tag_of(plain))
        self._assert_full(r, refused=0)

    def test_d02_strings_booleans_and_lookalikes_are_refused(self):
        Fake = enum.Enum("ApprovedTemplateId", {"MOTORCYCLE_ADVISOR_REPLY": "MOTORCYCLE_ADVISOR_REPLY"})
        for bad in ("MOTORCYCLE_ADVISOR_REPLY", True, 1, "NO_VEHICLE_CLAIM",
                    TemplateValidationPolicy.NO_VEHICLE_CLAIM,
                    Fake.MOTORCYCLE_ADVISOR_REPLY, object()):
            with self.subTest(bad=bad):
                self._assert_full(_validate(self.TEXT, FOCI["moto_focus"], approved_template=bad))

    def test_d03_a_tag_on_different_words_is_refused(self):
        forged = ApprovedTemplateText(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY,
                                      "Lo revisamos uno por uno.")
        self._assert_full(_validate(forged, FOCI["moto_focus"],
                                    approved_template=template_tag_of(forged)))

    def test_d04_a_subclass_or_a_forged_tag_carries_nothing(self):
        class Sub(ApprovedTemplateText):
            pass
        sub = Sub(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY, str(self.TEXT))
        self.assertIsNone(template_tag_of(sub))
        bogus = ApprovedTemplateText("MOTORCYCLE_ADVISOR_REPLY", str(self.TEXT))
        self.assertEqual(template_tag_of(bogus), "MOTORCYCLE_ADVISOR_REPLY")   # unchecked tag
        self._assert_full(_validate(bogus, FOCI["moto_focus"],
                                    approved_template=template_tag_of(bogus)))

    def test_d05_changing_the_template_without_its_contract_withdraws_the_exemption(self):
        reg = _registry(_contract("A la brevedad uno de nuestros asesores te contactará."))
        with mock.patch.object(RV, "_APPROVED_TEMPLATE_CONTRACTS", reg):
            self._assert_full(_validate(self.TEXT, FOCI["moto_focus"],
                                        approved_template=template_tag_of(self.TEXT)))

    def test_d06_unknown_or_unmapped_policy_fails_closed(self):
        for policy in ("NO_VEHICLE_CLAIM", None, True,
                       enum.Enum("P", {"NO_VEHICLE_CLAIM": "NO_VEHICLE_CLAIM"}).NO_VEHICLE_CLAIM):
            reg = _registry(_contract(str(self.TEXT), policy=policy))
            with self.subTest(policy=policy), \
                    mock.patch.object(RV, "_APPROVED_TEMPLATE_CONTRACTS", reg):
                self._assert_full(_validate(self.TEXT, FOCI["moto_focus"],
                                            approved_template=template_tag_of(self.TEXT)))
        # A real policy member that the skip table does not know skips nothing either.
        with mock.patch.object(RV, "_POLICY_SKIPPED_CLAIMS", types.MappingProxyType({})):
            self._assert_full(_validate(self.TEXT, FOCI["moto_focus"],
                                        approved_template=template_tag_of(self.TEXT)))

    def test_d07_unregistered_id_or_mismatched_contract_fails_closed(self):
        with mock.patch.object(RV, "_APPROVED_TEMPLATE_CONTRACTS", types.MappingProxyType({})):
            self._assert_full(_validate(self.TEXT, FOCI["moto_focus"],
                                        approved_template=template_tag_of(self.TEXT)))
        crossed = types.MappingProxyType({ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY: _contract(
            str(self.TEXT), tid=ApprovedTemplateId.MOTORCYCLE_HANDOFF_REPLY)})
        with mock.patch.object(RV, "_APPROVED_TEMPLATE_CONTRACTS", crossed):
            self._assert_full(_validate(self.TEXT, FOCI["moto_focus"],
                                        approved_template=template_tag_of(self.TEXT)))
        with mock.patch.object(RV, "_APPROVED_TEMPLATE_CONTRACTS",
                               {ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY: "not a contract"}):
            self._assert_full(_validate(self.TEXT, FOCI["moto_focus"],
                                        approved_template=template_tag_of(self.TEXT)))

    def test_d08_every_other_check_still_runs_on_an_approved_template(self):
        cases = (
            ("La revisión sale $85.000.", "PRICE", CanonicalFacts(vehicle_marca="Honda", vehicle_modelo="CB500")),
            ("Tenemos disponibilidad a las 10:00.", "AVAILABILITY", CanonicalFacts()),
            ("Tu turno quedó confirmado.", "BOOKING", CanonicalFacts()),
            ("Ya aceptaste el presupuesto.", "ACCEPTANCE", CanonicalFacts()),
            ("Tu auto está en Palermo para la revisión.", "LOCATION",
             CanonicalFacts(known_zone_names=("Palermo",))),
        )
        for text, claim, facts in cases:
            reg = _registry(_contract(text))
            tagged = ApprovedTemplateText(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY, text)
            with self.subTest(claim=claim), \
                    mock.patch.object(RV, "_APPROVED_TEMPLATE_CONTRACTS", reg):
                r = _validate(tagged, facts, approved_template=template_tag_of(tagged))
                self.assertEqual(len(_grants(r)), 1, "the contract itself is valid")
                self.assertTrue(any(f.claim == claim and not f.allowed for f in r.findings),
                                f"{claim} was not checked on an approved template")
                self.assertNotEqual(r.text, text)

    def test_d09_any_string_operation_drops_the_tag(self):
        t = self.TEXT
        self.assertIs(type(t), ApprovedTemplateText)
        for derived in (t + "", "" + t, f"{t}", t.strip(), " ".join([t]), t[:],
                        t.replace("vos", "vos"), str(t), copy.copy(t), copy.deepcopy(t),
                        pickle.loads(pickle.dumps(t)), "%s" % t, t.format(), t.format_map({}),
                        t * 1, t.split(".")[0], t.partition("§")[0], t.rpartition("§")[2],
                        t.splitlines()[0], t.rsplit("§")[0], t % {}, t % ()):
            with self.subTest(op=repr(derived)[:30]):
                self.assertIsNone(template_tag_of(derived))

    def test_d10_the_tag_and_the_registries_are_immutable(self):
        with self.assertRaises(AttributeError):
            self.TEXT._template_id = "x"
        with self.assertRaises(TypeError):
            RV._APPROVED_TEMPLATE_CONTRACTS[ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY] = None
        with self.assertRaises(TypeError):
            RV._POLICY_SKIPPED_CLAIMS[TemplateValidationPolicy.FULL_VALIDATION] = frozenset({"PRICE"})

    def test_d11_the_resolution_is_total(self):
        for text in (None, "", "   ", b"bytes", 42):
            with self.subTest(text=text):
                skipped, _f = RV._template_exemptions(
                    ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY, text)
                self.assertEqual(skipped, frozenset())

    def test_d12_full_validation_policy_skips_nothing(self):
        reg = _registry(_contract(str(self.TEXT), policy=TemplateValidationPolicy.FULL_VALIDATION))
        with mock.patch.object(RV, "_APPROVED_TEMPLATE_CONTRACTS", reg):
            r = _validate(self.TEXT, FOCI["moto_focus"], approved_template=template_tag_of(self.TEXT))
        self.assertEqual(r.text, _FALLBACK_REPLY)
        self.assertEqual(len(_grants(r)), 1)


class D_CallerIsolationStructure(unittest.TestCase):
    """Who may attach a tag, and where the tag is read. Static, over the whole app tree."""

    @staticmethod
    def _calls(name):
        for path in sorted(_APP.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            parents = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (
                        getattr(node.func, "id", None) == name
                        or getattr(node.func, "attr", None) == name):
                    yield path, node, parents, tree

    def test_d13_tags_are_constructed_only_as_module_constants_for_registered_ids(self):
        found = {}
        for path, node, parents, tree in self._calls("ApprovedTemplateText"):
            rel = path.relative_to(_APP).as_posix()
            self.assertEqual(rel, "services/conversation_engine.py",
                             f"ApprovedTemplateText constructed in {rel}:{node.lineno}")
            assign = parents.get(node)
            self.assertIsInstance(assign, ast.Assign, f"not a plain assignment at :{node.lineno}")
            self.assertIs(parents.get(assign), tree, f"not module level at :{node.lineno}")
            first = node.args[0]
            self.assertIsInstance(first, ast.Attribute)
            self.assertEqual(getattr(first.value, "id", None), "ApprovedTemplateId")
            found[assign.targets[0].id] = first.attr
        self.assertEqual(found, {"_MOTORCYCLE_ADVISOR_REPLY": "MOTORCYCLE_ADVISOR_REPLY",
                                 "_MOTORCYCLE_HANDOFF_REPLY": "MOTORCYCLE_HANDOFF_REPLY"})

    def test_d14_the_tag_is_read_in_exactly_one_place(self):
        # R6: the structured contract (`validate_envelope`) reads provenance from the value's
        # exact type, so the ONE read moved from CE's wrapper into the validator itself:
        # `_approved_template_of`. CE no longer reads a tag at all.
        sites = [(p.relative_to(_APP).as_posix(), n.lineno)
                 for p, n, _pa, _t in self._calls("template_tag_of")]
        self.assertEqual(len(sites), 1, sites)
        self.assertEqual(sites[0][0], "services/response_validator.py")
        src = (_APP / "services/response_validator.py").read_text(encoding="utf-8-sig").splitlines()
        body = "\n".join(src[max(0, sites[0][1] - 4):sites[0][1]])
        self.assertIn("def _approved_template_of", body)
        kw = [(p.relative_to(_APP).as_posix(), n.lineno)
              for p, n, _pa, _t in self._calls("validate_response")
              if any(k.arg == "approved_template" for k in n.keywords)]
        self.assertEqual([s for s, _l in kw], ["services/response_validator.py"])

    def test_d15_each_registered_template_has_one_constant_and_equal_text(self):
        constants = {k: v for k, v in vars(CE).items() if type(v) is ApprovedTemplateText}
        self.assertEqual({v.template_id for v in constants.values()}, set(ApprovedTemplateId))
        for name, value in constants.items():
            with self.subTest(constant=name):
                contract = RV.approved_template_contract(value.template_id)
                self.assertIsNotNone(contract)
                self.assertEqual(str(value), contract.text,
                                 "the template changed without its contract (or vice versa)")
                self.assertIs(contract.policy, TemplateValidationPolicy.NO_VEHICLE_CLAIM)

    def test_d16_the_ce_wrapper_validates_untagged_words_in_full(self):
        eng = object.__new__(ConversationEngine)
        eng._build_canonical_facts = lambda ctx, state: FOCI["moto_focus"]
        ctx = types.SimpleNamespace(thread=types.SimpleNamespace(id=1))
        # R6: a genuine vehicle claim in untagged words is still refused in full, into the
        # plan-appropriate fallback (the moto focus holds a vehicle, so only the place is asked).
        refused = eng._validate_outbound_text("Es un Fiat Uno.", ctx, None)
        self.assertEqual(refused, RV.select_validation_fallback(FOCI["moto_focus"]).text)
        self.assertEqual(eng._validate_outbound_text(self.TEXT_, ctx, None), str(self.TEXT_))
        # R6 (R5F1RV-01): the class fix — "uno de nuestros asesores" is not a claim-shaped
        # vehicle construction, so the UNTAGGED words survive too; the tag is no longer
        # what keeps the handoff intact on the production path.
        self.assertEqual(eng._validate_outbound_text(str(self.TEXT_), ctx, None), str(self.TEXT_))

    def test_d17_a_refused_tag_through_the_ce_wrapper_is_validated_not_passed_through(self):
        # `_validate_outbound_text` returns the raw text if validation RAISES. A refusal must
        # therefore be a result, never an exception, or a forged tag would bypass everything.
        eng = object.__new__(ConversationEngine)
        eng._build_canonical_facts = lambda ctx, state: FOCI["moto_focus"]
        ctx = types.SimpleNamespace(thread=types.SimpleNamespace(id=1))
        for forged in (ApprovedTemplateText(ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY, "Es un Fiat Uno."),
                       ApprovedTemplateText("MOTORCYCLE_ADVISOR_REPLY", "Es un Fiat Uno."),
                       ApprovedTemplateText(None, "Es un Fiat Uno.")):
            with self.subTest(tag=forged.template_id):
                out = eng._validate_outbound_text(forged, ctx, None)
                self.assertEqual(out, RV.select_validation_fallback(FOCI["moto_focus"]).text)
                self.assertNotEqual(out, str(forged))

    TEXT_ = CE._MOTORCYCLE_ADVISOR_REPLY


# ── E. deterministic response inventory ──────────────────────────────────────────────

# Every module-level customer-facing str constant visible on the CE module, classified.
# FULL: validated in full (no contract). CONTRACT: carries an approved-template contract.
# Text the validator re-joins with a space instead of "\n\n" is pre-existing and listed.
CUSTOMER_FACING = {
    "DEFERRED_RESPONSE_ES": "FULL",
    "_AVAILABILITY_VS_HOURS_CLARIFICATION": "FULL",
    "_CANONICAL_LOCATION_ASK": "FULL",
    "_CORRECTION_TARGET_CLARIFICATION": "FULL",
    "_COVERAGE_RESPONSE": "FULL",
    "_DEGRADED_RESPONSE_FALLBACK": "FULL",
    "_F12_BOUNDARY_REPLY": "FULL",
    "_FALLBACK_WARM_HANDOFF": "FULL",
    "_FAQ_PAYMENT_ANSWER": "FULL",
    "_FAQ_PRESENCE_ANSWER": "FULL",
    "_FAQ_REPORT_ANSWER": "FULL",
    "_FAQ_SCOPE_ANSWER": "FULL",
    "_FUZZY_ASK_VEHICLE_REPLY": "FULL",
    "_FUZZY_CONFIRMATION_TEMPLATE": "FULL",
    "_GENERIC_HANDOFF_REPLY": "FULL",
    "_INSPECTABILITY_DISASSEMBLED_REPLY": "FULL",
    "_INSPECTABILITY_NONRUNNING_CLARIFY": "FULL",
    "_LOCALITY_ASK_AGAIN_REPLY": "FULL",
    "_LOCALITY_CONFIRMATION_TEMPLATE": "FULL",
    "_LOCATION_CLARIFICATION_INTRO": "FULL",
    "_LOCATION_CONTRADICTION_CLARIFICATION": "FULL",
    "_MOTORCYCLE_ADVISOR_REPLY": "CONTRACT",
    "_MOTORCYCLE_CONTACT_INTRO": "FULL",
    "_MOTORCYCLE_HANDOFF_REPLY": "CONTRACT",
    "_PHONE_CALL_HANDOFF_REPLY": "FULL",
    "_REPAIR_BOUNDARY_REPLY": "FULL",
    "_TRANSFER_BOUNDARY_REPLY": "FULL",
    "_UNCERTAIN_SERVICE_REPLY": "FULL",
    "_TX_FLOW_PENDING_TEXT": "FULL",       # R6-C4A transactional renderer copy
    "_DELAY_LINE": "FULL",                 # R6-C4B: the engine's own bounded delay line (declared)
    "_TX_SCHEDULING_ASK_TEXT": "FULL",     # R6-C4A transactional renderer copy
    "_TX_FLOW_CTA_TEXT": "FULL",           # R6-C4B-R1: canonical Meta Booking Flow CTA
    "_DEFERRED_RESPONSE_FOCUS_ES": "FULL", # R6-C4B-R1B: deferred copy, focus vehicle held
    "_FAQ_SCOPE_LEAD": "FULL",             # WILD1-C: scope answer (published FAQ), lead sentence
    "_COVERAGE_PRICE_LEAD": "FULL",        # WILD1-C: lead of the secondary price-breakdown block
    "_QUOTE_DECLINE_ACK": "FULL",          # WILD1-C: explicit quote decline acknowledgement
    "_PENDING_QUESTION_ACK": "FULL",       # WILD1-C-R3/R4: question received, reading pending
    "_PRICE_CONCERN_LEAD": "FULL",         # WILD1-C-R4 (WC-09): price concern, neutral lead
    "_PERDIDO_HOLD_REPLY": "FULL",         # WILD1-C-R4 (WC-08): operator-held PERDIDO hold
    "_PERDIDO_FALLBACK_DATA_REPLY": "FULL",  # WILD1-C-R4-R1: fallback after an engine decline
    "_GENERIC_NO_QUESTION_TEXT": "FULL",   # WILD1-C-R4: alias of the validator's own closing
    "_VEHICLE_CLARIFICATION_INTRO": "FULL",
}
NOT_CUSTOMER_FACING = {
    "_G3_HANDOFF_OUTCOME_NO", "_G3_HANDOFF_OUTCOME_YES", "_G3_HANDOFF_REASON_NO",
    "_G3_HANDOFF_REASON_YES",              # trace/decision reasons
    "_WEBSITE_FORM_HEADER",                # inbound website-form header (customer's words)
}
WHITESPACE_REJOINED = {"_CANONICAL_LOCATION_ASK", "_COVERAGE_RESPONSE"}
RENDER = {
    "_FUZZY_CONFIRMATION_TEMPLATE": {"marca": "Fiat", "modelo": "Uno"},
    "_LOCALITY_CONFIRMATION_TEMPLATE": {"zone": "Palermo"},
}


def _discovered():
    return {k for k, v in vars(CE).items()
            if k.lstrip("_").isupper() and isinstance(v, str) and " " in v and len(v) > 12}


class E_DeterministicInventory(unittest.TestCase):

    def _ce_validate(self, text, facts):
        eng = object.__new__(ConversationEngine)
        eng._build_canonical_facts = lambda ctx, state: facts
        ctx = types.SimpleNamespace(thread=types.SimpleNamespace(id=1))
        return eng._validate_outbound_text(text, ctx, None)

    def test_e01_the_inventory_is_complete(self):
        self.assertEqual(_discovered(), set(CUSTOMER_FACING) | NOT_CUSTOMER_FACING,
                         "a customer-facing constant was added or removed: classify it")
        self.assertEqual(set(CUSTOMER_FACING) & NOT_CUSTOMER_FACING, set())

    def test_e02_classification_matches_what_the_constant_carries(self):
        for name, cls in CUSTOMER_FACING.items():
            with self.subTest(constant=name):
                tagged = template_tag_of(getattr(CE, name)) is not None
                self.assertEqual(tagged, cls == "CONTRACT")

    def _items(self):
        for name in sorted(CUSTOMER_FACING):
            value = getattr(CE, name)
            if name in RENDER:
                value = value.format(**RENDER[name])
            yield name, value
        yield "booking_acknowledgement.BOOKING_HANDOFF_TEXT", BOOKING_HANDOFF_TEXT

    def test_e03_every_constant_survives_the_real_ce_validator_under_every_focus(self):
        for name, value in self._items():
            for focus, facts in FOCI.items():
                with self.subTest(constant=name, focus=focus):
                    out = self._ce_validate(value, facts)
                    self.assertNotEqual(out, _FALLBACK_REPLY)
                    if name in WHITESPACE_REJOINED:
                        self.assertEqual(" ".join(out.split()), " ".join(str(value).split()))
                    else:
                        self.assertEqual(out, str(value))

    def test_e04_intentional_vehicle_claiming_templates_stay_under_the_comparison(self):
        # The fuzzy confirmation names a vehicle, but as a QUESTION (never a claim); stated
        # as an assertion with the same words, the comparison applies to it as to any prose.
        q = CE._FUZZY_CONFIRMATION_TEMPLATE.format(marca="Fiat", modelo="Uno")
        self.assertEqual(self._ce_validate(q, FOCI["moto_focus"]), q)
        self.assertEqual(self._ce_validate("Es un Fiat Uno.", FOCI["moto_focus"]),
                         RV.select_validation_fallback(FOCI["moto_focus"]).text)
        self.assertIsNone(template_tag_of(CE._FUZZY_CONFIRMATION_TEMPLATE))


if __name__ == "__main__":
    unittest.main()

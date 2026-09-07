"""L4.7W2-F1 — recovering a locality from imperfect speech, without weakening authority.

Wild W2: the customer said "el auto está en Berazategui"; Whisper stored "Ok, el auto está
embarazado, Tegui." Berazategui is in the catalog, the intended sentence resolves, the
transcript does not — so CE safely asked again.

The fix cannot be fuzzy matching over the message. Against the real 207-name catalog
"esta" scores 1.000 against **Floresta**, and no lexical rule separates that from "tegui"
inside "Berazategui" — both are mid-word suffixes covering about half the name. What
separates them is that something first established one is a place name. So the protection
lives in what the scorer may see, and an approximate hit is a question, never an answer.

VOICELOC-01 W2 corrupted transcript      VOICELOC-10 correction overrides proposal
VOICELOC-02 exact Berazategui unchanged  VOICELOC-11 ambiguity falls back safely
VOICELOC-03 bounded typo fragment        VOICELOC-12 semantic failure does not guess
VOICELOC-04 no whole-sentence fuzzy      VOICELOC-13 same-turn evidence reused
VOICELOC-05 "está" cannot match Floresta VOICELOC-14 <=1 model call per burst
VOICELOC-06 origin vs inspection role    VOICELOC-15 current-cycle isolation
VOICELOC-07 approximate needs confirming  VOICELOC-16 vehicle unchanged
VOICELOC-08 confirmation canonicalizes   VOICELOC-17 FAQ unchanged
VOICELOC-09 rejection clears proposal    VOICELOC-18 pricing needs canonical location
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON         # type: ignore[attr-defined]

from app.services.locality_resolver import (  # noqa: E402
    MIN_FRAGMENT_CHARS,
    MAX_FRAGMENT_WORDS,
    LocationFragment,
    resolve_locality_fragment,
)

CE_PATH = ROOT / "backend" / "app" / "services" / "conversation_engine.py"
CE_SOURCE = CE_PATH.read_text(encoding="utf-8-sig")
RESOLVER_SOURCE = (ROOT / "backend" / "app" / "services"
                   / "locality_resolver.py").read_text(encoding="utf-8-sig")

# The stored transcript, exactly as WhatsApp delivered it on 2026-09-05.
W2_TRANSCRIPT = "Ok, el auto está embarazado, Tegui."


def zone(group, detail):
    return SimpleNamespace(zone_group=group, zone_detail=detail)


# A faithful slice of the real catalog: the target, the trap, and near neighbours.
CATALOG = [zone("Sur", "Berazategui"), zone("CABA", "Floresta"), zone("Oeste", "Trujui"),
           zone("Norte", "Tortuguitas"), zone("Norte", "Tigre"), zone("Sur", "Quilmes"),
           zone("Sur", "Burzaco"), zone("CABA", "Barracas"), zone("Sur", "Bernal")]


def fragment(text, role="INSPECTION_LOCATION", source=W2_TRANSCRIPT):
    return LocationFragment.from_evidence(text, role, "test", source)


class TestBoundedMatching(unittest.TestCase):

    def test_voiceloc_01_w2_corrupted_transcript_proposes_berazategui(self):
        """VOICELOC-01 — the exact Wild failure, recovered as a proposal."""
        frag = fragment("Tegui")
        self.assertIsNotNone(frag, "the semantic span is a usable fragment")
        match = resolve_locality_fragment(frag, CATALOG)
        self.assertEqual(match.status, "APPROXIMATE")
        self.assertEqual(match.best.zone_detail, "Berazategui")
        self.assertEqual(match.best.zone_group, "Sur")
        self.assertTrue(match.needs_confirmation)
        self.assertFalse(match.is_canonical, "an approximate hit is never canonical")

    def test_voiceloc_02_exact_locality_is_unchanged(self):
        """VOICELOC-02 — an exact name still resolves directly, no confirmation."""
        match = resolve_locality_fragment(
            fragment("Berazategui", source="el auto está en Berazategui"), CATALOG)
        self.assertEqual(match.status, "EXACT")
        self.assertTrue(match.is_canonical)
        self.assertFalse(match.needs_confirmation)

    def test_voiceloc_03_bounded_typo_fragment(self):
        """VOICELOC-03 — ordinary misspellings recover, and still ask."""
        for typo in ("Berazatgui", "Berazategi"):
            match = resolve_locality_fragment(
                fragment(typo, source=f"el auto está en {typo}"), CATALOG)
            self.assertEqual(match.status, "APPROXIMATE", typo)
            self.assertEqual(match.best.zone_detail, "Berazategui", typo)

    def test_voiceloc_04_a_whole_sentence_is_not_a_fragment(self):
        """VOICELOC-04 — the module cannot be handed a message."""
        self.assertIsNone(fragment(W2_TRANSCRIPT))
        self.assertIsNone(fragment("el auto está embarazado, Tegui"))
        self.assertIsNone(fragment("yo soy de Tigre pero el auto está en Berazategui"))
        with self.assertRaises(TypeError):
            resolve_locality_fragment("Tegui", CATALOG)      # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            resolve_locality_fragment(W2_TRANSCRIPT, CATALOG)  # type: ignore[arg-type]

    def test_voiceloc_05_esta_can_never_become_floresta(self):
        """VOICELOC-05 — the trap that makes naive fuzzy matching unsafe."""
        # The danger is containment, not raw similarity: "esta" sits inside "floresta",
        # and the containment score this module gives that is above its own threshold.
        from app.services.locality_resolver import MIN_SCORE, _score
        self.assertIn("esta", "floresta")
        self.assertGreaterEqual(_score("esta", "Floresta"), MIN_SCORE,
                                "if it ever reached the scorer it WOULD match — "
                                "which is why it must never reach the scorer")
        for verb in ("está", "esta", "Esta"):
            self.assertIsNone(fragment(verb, source=W2_TRANSCRIPT),
                              f"{verb!r} is not proper-noun-shaped in the message")
        self.assertIsNone(fragment("buen día", source="Hola, buen día"))
        self.assertIsNone(fragment("auto", source="el auto está"))

    def test_voiceloc_06_origin_never_becomes_inspection_location(self):
        """VOICELOC-06 — role is enforced before any scoring happens."""
        origin = LocationFragment.from_evidence("Tigre", "CUSTOMER_ORIGIN", "test",
                                                "yo soy de Tigre")
        self.assertIsNotNone(origin)
        self.assertFalse(origin.is_inspection_role)
        match = resolve_locality_fragment(origin, CATALOG)
        self.assertEqual(match.status, "NONE")
        self.assertIsNone(match.best)
        self.assertIn("role", match.reason)

    def test_voiceloc_11_ambiguous_candidates_fall_back(self):
        """VOICELOC-11 — two plausible localities is a coin toss, not a recovery."""
        twins = [zone("Sur", "San Vicente"), zone("Norte", "San Vicent")]
        match = resolve_locality_fragment(
            fragment("San Vicen", source="el auto está en San Vicen"), twins)
        self.assertEqual(match.status, "AMBIGUOUS")
        self.assertIsNone(match.best)
        self.assertFalse(match.needs_confirmation)

    def test_a_weak_fragment_yields_nothing(self):
        match = resolve_locality_fragment(
            fragment("Zzyzx", source="el auto está en Zzyzx"), CATALOG)
        self.assertEqual(match.status, "NONE")
        self.assertIsNone(match.best)
        self.assertIsNone(fragment("Ok", source="Ok"), "below the minimum length")
        self.assertGreaterEqual(MIN_FRAGMENT_CHARS, 4)
        self.assertLessEqual(MAX_FRAGMENT_WORDS, 4)


class TestStaticGovernance(unittest.TestCase):

    def test_the_resolver_refuses_anything_but_a_fragment(self):
        """The invariant, asserted structurally rather than by example."""
        self.assertIn("raise TypeError", RESOLVER_SOURCE)
        self.assertIn("isinstance(fragment, LocationFragment)", RESOLVER_SOURCE)

    def test_no_caller_passes_raw_message_text_to_the_resolver(self):
        """Every call site must build a fragment first; none may pass a message."""
        tree = ast.parse(CE_SOURCE)
        banned = {"current_turn_text", "combined", "text", "burst_text",
                  "ai_input_messages", "all_recent_text"}
        offenders = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "resolve_locality_fragment"):
                continue
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Name) and first.id in banned:
                offenders.append(f"line {node.lineno}: {first.id}")
        self.assertEqual(offenders, [], f"raw prose reaches the resolver: {offenders}")

    def test_the_resolver_cannot_write_canonical_state(self):
        """It has no ORM, no session and no candidate."""
        code = RESOLVER_SOURCE
        for forbidden in ("Session", "db.add", "commit()", "WhatsAppThreadCandidate",
                          "_apply_inspection_zone", "ViaticosZone("):
            self.assertNotIn(forbidden, code, forbidden)

    def test_voiceloc_07_an_approximate_result_is_never_written_directly(self):
        """VOICELOC-07 — the proposal path arms state and asks; it does not canonicalize."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "_propose_locality")
        self.assertIn("pending_location_proposal", fn)
        self.assertIn("_LOCALITY_CONFIRMATION_TEMPLATE", fn)
        self.assertNotIn("_apply_inspection_zone", fn,
                         "a proposal must not write the canonical locality")
        gate = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                    if isinstance(n, ast.FunctionDef) and n.name == "_routing_gate")
        self.assertIn("needs_confirmation", gate)


class TestConversationPath(unittest.TestCase):
    """The confirmation lifecycle, read from the engine's own source and state rules."""

    def engine(self, pending=None, cycle="c1"):
        from app.services.conversation_engine import ConversationEngine
        eng = ConversationEngine.__new__(ConversationEngine)
        eng.db = MagicMock()
        eng.settings = SimpleNamespace(reconciler_location_authority_enabled=True,
                                       reconciler_vehicle_authority_enabled=True,
                                       semantic_same_turn_enabled=True)
        state = SimpleNamespace(pending_location_proposal=pending,
                                current_cycle_start_message_db_id=cycle,
                                current_cycle_started_at=None)
        return eng, state

    def test_voiceloc_08_confirmation_is_scoped_to_the_cycle(self):
        """VOICELOC-08/15 — a proposal only exists inside the cycle that armed it."""
        eng, state = self.engine(pending="Sur||Berazategui||c1", cycle="c1")
        self.assertEqual(eng._pending_location_proposal(state), ("Sur", "Berazategui"))
        eng2, stale = self.engine(pending="Sur||Berazategui||c1", cycle="c2")
        self.assertIsNone(eng2._pending_location_proposal(stale),
                          "a prior-cycle proposal cannot be confirmed")

    def test_voiceloc_09_and_10_rejection_and_correction_clear_the_proposal(self):
        """VOICELOC-09/10 — the source wires both outcomes to clearing state."""
        block = CE_SOURCE[CE_SOURCE.index("L4.7W2-F1: pending locality confirmation"):]
        block = block[:block.index("M21.1.4: Pending fuzzy confirmation handler")]
        self.assertIn("_extract_zone_from_text(current_turn_text)", block)
        self.assertIn("_FUZZY_REJECTION_RE", block)
        self.assertIn("_FUZZY_ACCEPTANCE_RE", block)
        self.assertEqual(block.count("_clear_location_proposal(state)"), 3,
                         "correction, confirmation and rejection each clear it")
        confirm = block[block.index("_FUZZY_ACCEPTANCE_RE"):block.index("_FUZZY_REJECTION_RE")]
        self.assertIn("_apply_inspection_zone", confirm,
                      "only confirmation reaches the canonical writer")

    def test_a_malformed_or_empty_proposal_is_ignored(self):
        eng, state = self.engine(pending="garbage")
        self.assertIsNone(eng._pending_location_proposal(state))
        eng, state = self.engine(pending=None)
        self.assertIsNone(eng._pending_location_proposal(state))

    def test_voiceloc_12_semantic_absence_does_not_guess(self):
        """VOICELOC-12 — no evidence, no fragment, no proposal."""
        eng, state = self.engine()
        eng._semantic_turn_evidence = lambda: None
        self.assertIsNone(eng._semantic_location_fragment(W2_TRANSCRIPT))

    def test_voiceloc_13_and_14_the_same_turn_provider_is_reused(self):
        """VOICELOC-13/14 — location reads C4A's provider; no second model call."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "_semantic_location_fragment")
        self.assertIn("self._semantic_turn_evidence()", fn)
        self.assertNotIn("SemanticTurnInterpreter", fn)
        self.assertNotIn("interpret(", fn)
        # the provider itself still guarantees one execution
        from app.services.semantic_turn_evidence import TurnSemanticEvidence
        calls = []
        provider = TurnSemanticEvidence(lambda: (calls.append(1), "r")[1])
        provider.start()
        for _ in range(5):
            provider.get()
        self.assertEqual(len(calls), 1)
        self.assertEqual(provider.calls, 1)

    def test_voiceloc_16_and_17_vehicle_and_faq_are_untouched(self):
        """VOICELOC-16/17 — F2 and the F4 FAQ cutover still hold."""
        from app.services.vehicle_catalog import fuzzy_lookup_vehicle
        from app.services.conversation_engine import (extract_model_del_year,
                                                      _FAQ_TOPIC_ANSWERS)
        self.assertEqual(fuzzy_lookup_vehicle(
            "Hola, buen día. Bueno, ¿era para revisar un 2008 del 2014?").outcome,
            "UNRESOLVED")
        hit = extract_model_del_year("Quería revisar un 2008 del 2014")
        self.assertEqual((hit[0].marca, hit[0].modelo, hit[1]), ("Peugeot", "2008", 2014))
        self.assertEqual(set(_FAQ_TOPIC_ANSWERS),
                         {"business_hours", "report", "presence", "payment", "service_scope"})

    def test_voiceloc_18_pricing_still_requires_a_canonical_location(self):
        """VOICELOC-18 — a proposal is not a location, so it cannot price."""
        # Read the raw source, not the unparsed tree: ast.unparse discards comments.
        start = CE_SOURCE.index("L4.7W2-F1: bounded locality recovery")
        recovery = CE_SOURCE[start:CE_SOURCE.index("Priority 2", start)]
        self.assertNotIn("_compute_price_quote", recovery,
                         "recovery must not price on a proposal")
        self.assertNotIn("_apply_inspection_zone", recovery)
        self.assertIn("needs_confirmation", recovery)
        gate = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                    if isinstance(n, ast.FunctionDef) and n.name == "_routing_gate")
        self.assertIn("zone_known", gate)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()

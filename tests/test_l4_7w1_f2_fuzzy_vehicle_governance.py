"""L4.7W1-F2 — fuzzy matching is evidence, never authority.

The first controlled Wild after C4A asked a real customer "¿Es un Fiat Uno?" because
"buen día. Bueno," normalises to the window "dia bueno", which scored 0.706 against the
catalog form "fiat uno". The customer had said "un 2008 del 2014".

Two things were wrong and both are fixed here:

  * fuzzy similarity ran over arbitrary customer prose, so a greeting could outscore a car;
  * the fuzzy branch SENT and RETURNED before any deterministic block or reconciliation ran.

These tests enter through the production chokepoint `_process_text`, not through helpers,
because the previous certified fixture missed the defect precisely by not doing that.

FUZZGOV-01 exact Wild burst            FUZZGOV-09 fuzzy cannot send directly
FUZZGOV-02 greeting + numeric vehicle  FUZZGOV-10 fuzzy cannot return before reconciler
FUZZGOV-03 real typo case still works  FUZZGOV-11 pending key cannot canonicalise
FUZZGOV-04 greeting is not a vehicle   FUZZGOV-12 candidate write passes C2
FUZZGOV-05 FAQ words are not a vehicle FUZZGOV-13 candidate correction unchanged
FUZZGOV-06 catalog beats fuzzy         FUZZGOV-14 candidate switch unchanged
FUZZGOV-07 explicit beats fuzzy        FUZZGOV-15 vehicle category unchanged
FUZZGOV-08 neutral clarification       FUZZGOV-16 rollback with authority flag off
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

from test_m21_1_1_service_intent_gate import (  # noqa: E402
    _make_ctx,
    _make_engine,
    _make_event,
    _make_state,
)
from app.services.vehicle_catalog import (  # noqa: E402
    extract_vehicle_fragments,
    fuzzy_lookup_vehicle,
    lookup_vehicle,
)

CE_PATH = ROOT / "backend" / "app" / "services" / "conversation_engine.py"
CE_SOURCE = CE_PATH.read_text(encoding="utf-8")

# The real burst, exactly as WhatsApp transcribed it on 2026-09-04.
WILD_1 = "Hola, buen día. Bueno, ¿era para revisar un 2008 del 2014?"
WILD_2 = "¿Cómo trabajan ustedes? ¿Mandan un informe? ¿Deben estar presentes?"
WILD_3 = "¿Aceptan? ¿Debito?"
WILD_BURST = [WILD_1, WILD_2, WILD_3]


def code_of(source: str) -> str:
    """Executable code only — a docstring naming a defect is not the defect."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
    return ast.unparse(tree)


def run_turn(text=None, unanswered=None, stage="QUALIFYING", state_kwargs=None,
             candidates=None, vehicle_authority=False):
    """Drive the production chokepoint with the real catalog in place."""
    eng = _make_engine()
    eng.settings = SimpleNamespace(
        openai_api_key="sk-fake", openai_chat_model="gpt-4o-mini",
        backend_url="http://localhost:8000", whatsapp_flow_id="",
        reconciler_vehicle_authority_enabled=vehicle_authority,
        reconciler_location_authority_enabled=False,
        reconciler_acceptance_authority_enabled=False,
        reconciler_scheduling_authority_enabled=False,
        semantic_same_turn_enabled=False, shadow_understand_enabled=False,
        shadow_understand_async=False, shadow_evidence_path="")
    state = _make_state(last_stage=stage, **(state_kwargs or {}))
    ctx = _make_ctx(state=state, lead=SimpleNamespace(id=1, flag="PRESUPUESTANDO"))
    ctx.candidates = list(candidates or [])

    written = []

    def _write(c, s, match, source_text=""):
        cand = SimpleNamespace(id=len(written) + 1, marca=match.marca, modelo=match.modelo,
                               tipo_vehiculo=match.tipo_vehiculo, anio=None,
                               zone_group=None, zone_detail=None, status="current_focus")
        written.append(cand)
        c.candidates.insert(0, cand)
        if s is not None:
            s.current_focus_candidate_id = cand.id

    eng._create_candidate_from_catalog = MagicMock(side_effect=_write)
    event = _make_event(text=(text if text is not None else (unanswered or [""])[0]),
                        unanswered=unanswered)
    result = eng._process_text(ctx, event)
    return eng, result, state, ctx, written


def sent_texts(eng):
    return [c[0][1] for c in eng._send_text_to_wa.call_args_list]


# ── the burst that started this ──────────────────────────────────────────────

class TestWildReproduction(unittest.TestCase):

    def test_fuzzgov_01_exact_wild_burst(self):
        """FUZZGOV-01 — the real three-message burst, through the real entry path."""
        eng, result, state, ctx, written = run_turn(text=WILD_3, unanswered=WILD_BURST)
        joined = " ".join(sent_texts(eng))
        self.assertNotIn("Fiat", joined, "a greeting must never become a vehicle question")
        self.assertNotIn("Uno", joined)
        pending = getattr(state, "pending_fuzzy_catalog_key", None)
        self.assertNotEqual(pending, "Fiat||Uno")
        self.assertIsNone(pending, "no fuzzy identity may be armed for canonical replay")
        for cand in written:
            self.assertNotEqual((cand.marca, cand.modelo), ("Fiat", "Uno"))
        self.assertTrue(written, "the deterministic path still resolves the vehicle")
        self.assertEqual((written[0].marca, written[0].modelo), ("Peugeot", "2008"))
        self.assertEqual(written[0].tipo_vehiculo, "SUV_4X4_DEPORTIVO")

    def test_fuzzgov_02_greeting_plus_numeric_vehicle(self):
        """FUZZGOV-02 — the greeting is inert; the numeric model still resolves."""
        eng, result, state, ctx, written = run_turn(text=WILD_1)
        self.assertTrue(written)
        self.assertEqual((written[0].marca, written[0].modelo), ("Peugeot", "2008"))
        self.assertNotIn("Fiat", " ".join(sent_texts(eng)))

    def test_the_year_survives_the_burst(self):
        """"2008 del 2014" means the 2008 model, built in 2014."""
        from app.services.conversation_engine import extract_model_del_year
        hit = extract_model_del_year(WILD_1)
        self.assertIsNotNone(hit)
        match, year = hit
        self.assertEqual((match.marca, match.modelo, year), ("Peugeot", "2008", 2014))


# ── what fuzzy may and may not see ───────────────────────────────────────────

class TestFuzzyInputBoundary(unittest.TestCase):

    def test_fuzzgov_03_real_typo_cases_still_resolve(self):
        """FUZZGOV-03 — the capability that justifies fuzzy at all is preserved."""
        cases = {
            "ford fiestah": ("AUTO_ACCEPT", "Ford", "Fiesta"),
            "toyota corola": ("AUTO_ACCEPT", "Toyota", "Corolla"),
            "renolt clio": ("CONFIRM", "Renault", "Clio"),
            "chevrolet crkz": ("CONFIRM", "Chevrolet", "Cruze"),
            "ford ksl": ("CONFIRM", "Ford", "Ka"),
            "ford foco 2019 palermo": ("CONFIRM", "Ford", "Focus"),
        }
        for text, (outcome, marca, modelo) in cases.items():
            result = fuzzy_lookup_vehicle(text)
            self.assertEqual(result.outcome, outcome, text)
            self.assertEqual((result.hit.marca, result.hit.modelo), (marca, modelo), text)

    def test_fuzzgov_04_a_greeting_cannot_become_a_vehicle(self):
        """FUZZGOV-04 — prose carries no catalog vocabulary, so it never reaches scoring."""
        for text in ("Hola, buen día. Bueno, ¿cómo andás?", "hola buenas", "buen día",
                     "Bueno, dale", "hola buen dia bueno"):
            self.assertEqual(extract_vehicle_fragments(text), (), text)
            self.assertEqual(fuzzy_lookup_vehicle(text).outcome, "UNRESOLVED", text)

    def test_fuzzgov_05_faq_words_cannot_become_a_vehicle(self):
        """FUZZGOV-05 — the other two messages of the real burst."""
        for text in (WILD_2, WILD_3, "¿Cuánto sale la revisión?",
                     "Ya hice el Formulario 12 y ahora quiero revisar el auto antes de comprarlo"):
            self.assertEqual(fuzzy_lookup_vehicle(text).outcome, "UNRESOLVED", text)

    def test_the_winning_window_must_contain_catalog_vocabulary(self):
        """The invariant itself, stated directly rather than through an example."""
        from app.services import vehicle_catalog as vc
        self.assertFalse(vc._has_catalog_token("dia bueno"))
        self.assertTrue(vc._has_catalog_token("un 2008"))
        self.assertTrue(vc._has_catalog_token("ford ksl"))
        # the Wild window that caused the incident scores 0 now, at any threshold
        self.assertEqual(vc._best_ngram_score("hola buen dia bueno", "fiat uno"), 0.0)

    def test_fragments_are_bounded_not_whole_prose(self):
        fragments = extract_vehicle_fragments(WILD_1)
        self.assertTrue(fragments)
        for fragment in fragments:
            self.assertNotIn("hola", fragment)
            self.assertNotIn("bueno", fragment)
            self.assertLessEqual(len(fragment.split()), 2 * 2 + 1)


# ── precedence ───────────────────────────────────────────────────────────────

class TestPrecedence(unittest.TestCase):

    def test_fuzzgov_06_exact_catalog_beats_fuzzy(self):
        """FUZZGOV-06 — an exact catalog identity is never displaced by similarity."""
        exact = lookup_vehicle("Peugeot 2008")
        self.assertIsNotNone(exact)
        self.assertEqual((exact.marca, exact.modelo), ("Peugeot", "2008"))
        eng, result, state, ctx, written = run_turn(text="Tengo un Peugeot 2008 del 2014")
        joined = " ".join(sent_texts(eng))
        self.assertNotIn("Fiat", joined)
        if written:
            self.assertEqual((written[0].marca, written[0].modelo), ("Peugeot", "2008"))

    def test_fuzzgov_07_explicit_customer_vehicle_beats_fuzzy(self):
        """FUZZGOV-07 — an existing focused candidate is not re-questioned by fuzzy."""
        existing = SimpleNamespace(id=7, marca="Ford", modelo="Focus", anio=2019,
                                   tipo_vehiculo="AUTO", zone_group=None, zone_detail=None,
                                   status="current_focus")
        eng, result, state, ctx, written = run_turn(
            text="Hola, buen día. Bueno, ¿era para revisar?", candidates=[existing],
            state_kwargs={"current_focus_candidate_id": 7})
        self.assertEqual(written, [], "no new canonical vehicle from prose")
        self.assertNotIn("Fiat", " ".join(sent_texts(eng)))

    def test_fuzzy_is_the_weakest_evidence_class(self):
        from app.schemas.claims import EvidenceClass
        self.assertEqual(EvidenceClass.FUZZY_SUGGESTED.value, "FUZZY_SUGGESTED")


# ── governance: fuzzy may not act ────────────────────────────────────────────

class TestGovernance(unittest.TestCase):

    def test_fuzzgov_09_fuzzy_cannot_send_directly(self):
        """FUZZGOV-09 — no send call sits inside a fuzzy branch (AST, not grep)."""
        tree = ast.parse(CE_SOURCE)
        senders = {"_send_text_to_wa", "_send_flow_button", "_send_interactive"}

        def calls_fuzzy(node) -> bool:
            return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                       and n.func.id == "fuzzy_lookup_vehicle" for n in ast.walk(node))

        def sends(node):
            out = set()
            for n in ast.walk(node):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr in senders):
                    out.add(n.func.attr)
            return out

        # The branch that *contains* the fuzzy call — not the whole 3000-line function —
        # is what must be unable to send.
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.If, ast.Try, ast.With)):
                continue
            if not calls_fuzzy(node):
                continue
            inner = [n for n in ast.walk(node)
                     if isinstance(n, (ast.If, ast.Try)) and calls_fuzzy(n)]
            smallest = min(inner or [node], key=lambda n: n.end_lineno - n.lineno)
            found = sends(smallest)
            if found:
                offenders.append(f"line {smallest.lineno}: {sorted(found)}")
        self.assertEqual(offenders, [],
                         f"a fuzzy branch can still send: {offenders}")

    def test_fuzzgov_10_the_terminal_fuzzy_path_no_longer_exists(self):
        """FUZZGOV-10 — the fuzzy→send→return function is gone, not merely unwired."""
        tree = ast.parse(CE_SOURCE)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertNotIn("_handle_fuzzy_confirm", names)
        self.assertNotIn("_handle_fuzzy_confirm", code_of(CE_SOURCE))

    def test_the_advisory_branch_returns_nothing(self):
        """The CONFIRM branch may record evidence and must not end the turn."""
        tree = ast.parse(CE_SOURCE)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_process_text")
        for node in ast.walk(fn):
            if (isinstance(node, ast.Assign) and ast.unparse(node).strip()
                    == "self._fuzzy_advisory = _fuzzy"):
                break
        else:
            self.fail("the advisory assignment is missing")
        self.assertIn("self._fuzzy_advisory = _fuzzy", CE_SOURCE)

    def test_fuzzgov_11_pending_key_cannot_canonicalise_an_unreconciled_guess(self):
        """FUZZGOV-11 — a later "Sí" cannot create the vehicle the Wild would have."""
        eng, result, state, ctx, written = run_turn(text=WILD_1)
        self.assertIsNone(getattr(state, "pending_fuzzy_catalog_key", None))
        # and the arming site is inside the single reconciled composer
        tree = ast.parse(CE_SOURCE)
        arming = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and "pending_fuzzy_catalog_key = f\"" in \
                    ast.unparse(node):
                arming.append(node.name)
        self.assertTrue(set(arming) <= {"_ask_vehicle_clarification", "_process_text"},
                        f"pending key armed outside the composer: {arming}")

    def test_fuzzgov_12_a_fuzzy_candidate_write_passes_reconciliation(self):
        """FUZZGOV-12 — AUTO_ACCEPT is checked by C2 before it becomes canonical."""
        source = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                      if isinstance(n, ast.FunctionDef) and n.name == "_process_text")
        self.assertIn("self._fuzzy_identity_accepted(", source)
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "_fuzzy_identity_accepted")
        self.assertIn("reconcile_vehicle_identity", fn)


# ── nothing else moved ───────────────────────────────────────────────────────

class TestUnchangedBehaviour(unittest.TestCase):

    def test_fuzzgov_08_unresolved_vehicle_asks_a_neutral_question(self):
        """FUZZGOV-08 — no fabricated vehicle when the catalog does not support one."""
        composer = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                        if isinstance(n, ast.FunctionDef)
                        and n.name == "_ask_vehicle_clarification")
        self.assertIn("_FUZZY_ASK_VEHICLE_REPLY", composer)
        self.assertIn("lookup_vehicle(", composer)
        from app.services.conversation_engine import _FUZZY_ASK_VEHICLE_REPLY
        self.assertIn("marca", _FUZZY_ASK_VEHICLE_REPLY)
        self.assertNotIn("Fiat", _FUZZY_ASK_VEHICLE_REPLY)

    def test_fuzzgov_13_and_14_candidate_correction_and_switch_unchanged(self):
        """FUZZGOV-13/14 — exact-match correction still clears a pending proposal."""
        self.assertIn("Exact match clears any stale fuzzy pending proposal", CE_SOURCE)
        self.assertIn("_exact_now = lookup_vehicle(current_turn_text)", CE_SOURCE)

    def test_fuzzgov_15_vehicle_category_authority_unchanged(self):
        """FUZZGOV-15 — the catalog remains authoritative for tipo_vehiculo."""
        for name, tipo in (("Peugeot 2008", "SUV_4X4_DEPORTIVO"), ("Ford Fiesta", "AUTO")):
            hit = lookup_vehicle(name)
            self.assertIsNotNone(hit, name)
            self.assertEqual(hit.tipo_vehiculo, tipo, name)

    def test_fuzzgov_16_rollback_with_vehicle_authority_off(self):
        """FUZZGOV-16 — with C2 off the fuzzy gate degrades to legacy accept, not to a guess."""
        eng_off, _, state_off, _, written_off = run_turn(text=WILD_1, vehicle_authority=False)
        eng_on, _, state_on, _, written_on = run_turn(text=WILD_1, vehicle_authority=True)
        for eng, written in ((eng_off, written_off), (eng_on, written_on)):
            self.assertNotIn("Fiat", " ".join(sent_texts(eng)))
            self.assertTrue(written)
            self.assertEqual((written[0].marca, written[0].modelo), ("Peugeot", "2008"))

    def test_field_evidence_fuzzy_is_bounded_too(self):
        """The other prose caller inherits the same boundary."""
        from app.services.field_evidence import resolve_field_evidence  # noqa: F401
        self.assertEqual(fuzzy_lookup_vehicle(WILD_1).outcome, "UNRESOLVED")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()

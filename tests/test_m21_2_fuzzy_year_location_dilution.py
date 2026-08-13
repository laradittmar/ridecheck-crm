"""M21.2 regression: fuzzy_lookup_vehicle must not be diluted by year/location tokens.

Root cause: SequenceMatcher on the full text "ford ksl 2019 en palermo" vs catalog
form "ford ka" gives 0.45 (UNRESOLVED). The n-gram window approach finds the best
same-length window ("ford ksl") and scores 0.80 (CONFIRM).

Fix: _best_ngram_score() + max(full_score, ngram_score) in fuzzy_lookup_vehicle.
"""
from __future__ import annotations

import unittest

from app.services.vehicle_catalog import fuzzy_lookup_vehicle


class TestFuzzyYearLocationDilution(unittest.TestCase):
    """LIVE03 failure: vehicle + year + location in same message → CONFIRM, not UNRESOLVED."""

    def test_ford_typo_with_year_and_location(self):
        """'ford ksl 2019 en Palermo' → CONFIRM Ford Ka (LIVE03 exact message)."""
        result = fuzzy_lookup_vehicle("ford ksl 2019 en Palermo")
        self.assertIn(result.outcome, ("CONFIRM", "AUTO_ACCEPT"),
                      f"Expected CONFIRM or AUTO_ACCEPT, got {result.outcome} (score={result.score:.3f})")
        self.assertIsNotNone(result.hit)
        self.assertEqual(result.hit.marca, "Ford")
        self.assertEqual(result.hit.modelo, "Ka")

    def test_ford_typo_bare(self):
        """'ford ksl' (no year/location) still resolves correctly — no regression."""
        result = fuzzy_lookup_vehicle("ford ksl")
        self.assertIn(result.outcome, ("CONFIRM", "AUTO_ACCEPT"),
                      f"Got {result.outcome} (score={result.score:.3f})")
        self.assertEqual(result.hit.marca, "Ford")
        self.assertEqual(result.hit.modelo, "Ka")

    def test_ford_typo_year_only(self):
        """'ford ksl 2019' (typo + year, no location) → CONFIRM Ford Ka."""
        result = fuzzy_lookup_vehicle("ford ksl 2019")
        self.assertIn(result.outcome, ("CONFIRM", "AUTO_ACCEPT"),
                      f"Got {result.outcome} (score={result.score:.3f})")
        self.assertEqual(result.hit.marca, "Ford")
        self.assertEqual(result.hit.modelo, "Ka")

    def test_ford_typo_location_only(self):
        """'ford ksl en palermo' (typo + location, no year) → CONFIRM Ford Ka."""
        result = fuzzy_lookup_vehicle("ford ksl en palermo")
        self.assertIn(result.outcome, ("CONFIRM", "AUTO_ACCEPT"),
                      f"Got {result.outcome} (score={result.score:.3f})")
        self.assertEqual(result.hit.marca, "Ford")
        self.assertEqual(result.hit.modelo, "Ka")

    def test_ford_typo_with_year_and_location_2(self):
        """'ford foco 2019 palermo' (Focus typo) → CONFIRM Ford Focus."""
        result = fuzzy_lookup_vehicle("ford foco 2019 palermo")
        self.assertIn(result.outcome, ("CONFIRM", "AUTO_ACCEPT"),
                      f"Got {result.outcome} (score={result.score:.3f})")
        self.assertIsNotNone(result.hit)
        self.assertEqual(result.hit.marca, "Ford")
        self.assertEqual(result.hit.modelo, "Focus")

    def test_toyota_typo_with_year_and_zone(self):
        """'toyota corow 2020 zona norte' (Corolla typo) → CONFIRM Toyota Corolla."""
        result = fuzzy_lookup_vehicle("toyota corow 2020 zona norte")
        self.assertIn(result.outcome, ("CONFIRM", "AUTO_ACCEPT"),
                      f"Got {result.outcome} (score={result.score:.3f})")
        self.assertIsNotNone(result.hit)
        self.assertEqual(result.hit.marca, "Toyota")
        self.assertEqual(result.hit.modelo, "Corolla")


class TestFuzzyNgramAntiRegressions(unittest.TestCase):
    """Anti-regression: unrelated text must not produce false CONFIRM."""

    _SAFE_TEXTS = [
        "hola soy comprador",
        "quiero informacion",
        "buenos dias tengo una consulta",
        "necesito un auto barato",
    ]

    def test_unrelated_texts_are_unresolved(self):
        for text in self._SAFE_TEXTS:
            with self.subTest(text=text):
                result = fuzzy_lookup_vehicle(text)
                self.assertEqual(result.outcome, "UNRESOLVED",
                                 f"{text!r} → unexpected {result.outcome} (score={result.score:.3f})")


if __name__ == "__main__":
    unittest.main()

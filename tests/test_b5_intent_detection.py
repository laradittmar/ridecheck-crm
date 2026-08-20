"""B5 intent detection and FAQ routing tests.

Guards B5-01-A (inspection intent — 'quería' verb form) and
B5-01-B (service-composition and payment-method FAQ routing).

BI01  "Quería revisar un auto." → inspection intent TRUE
BI02  "Quería hacer una revisión precompra." → TRUE
BI03  "Quería que revisaran un auto antes de comprarlo." → TRUE
BI04  "Quería saber los horarios." → NOT inspection (no inspection verb)
BI05  "Quería consultar algo." → NOT inspection

BC01  "De qué consta el servicio" → detect_general_information TRUE
BC02  "En qué consiste la revisión" → TRUE
BC03  "Se puede pagar en débito" → detect_general_information TRUE
BC04  "¿Cómo se paga?" → TRUE
BC05  "¿Aceptan Mercado Pago?" → TRUE
BC06  "¿Medios de pago?" → TRUE
BC07  "Quería revisar un auto. De qué consta el servicio." — combined:
      detect_explicit_inspection_request TRUE (B5-01-A fix sufficient to route to AI)
BC08  FAQ-only "De qué consta el servicio?" layer-D fires (detect_general_information
      TRUE + detect_prepurchase_signal FALSE)

BS01  _is_general_faq_or_soft_close: "de que consta" → TRUE
BS02  "pagar en debito" → TRUE
BS03  "medios de pago" → TRUE

Regression guards:
BR01  "quiero revisar un auto" still works (existing pattern)
BR02  "quisiera revisar un auto" still works
BR03  "quiero que me revisen el auto" still works
BR04  "coordinar una revisión" still works
BR05  "¿qué incluye la revisión?" still in detect_general_information
BR06  "gracias" still in _is_general_faq_or_soft_close
"""
from __future__ import annotations

import re
import sys
import unicodedata
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.conversation_engine import (
    _FAQ_OR_SOFT_CLOSE_PHRASES,
    _FAQ_PATTERNS,
    _INSPECTION_REQUEST_PATTERNS,
    _PREPURCHASE_SIGNALS,
    _PRICE_QUESTION_PATTERNS,
    _is_general_faq_or_soft_close,
)


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _norm_text(s: str) -> str:
    return " ".join(_strip_accents(s).lower().split())


def _norm_lower(s: str) -> str:
    return " ".join(_strip_accents(s).lower().split())


def detect_explicit_inspection_request(text: str) -> bool:
    text_norm = _norm_text(text)
    return any(p.search(text_norm) for p in _INSPECTION_REQUEST_PATTERNS)


def detect_prepurchase_signal(text: str) -> bool:
    text_norm = _norm_text(text)
    return any(_norm_text(signal) in text_norm for signal in _PREPURCHASE_SIGNALS)


def detect_general_information(text: str) -> bool:
    text_norm = _norm_text(text)
    return any(re.search(p, text_norm, re.IGNORECASE) for p in _FAQ_PATTERNS)


def detect_price_question(text: str) -> bool:
    text_norm = _norm_text(text)
    return any(p.search(text_norm) for p in _PRICE_QUESTION_PATTERNS)


def layer_d_fires(text: str) -> bool:
    """Mirrors the B5-fixed Layer D condition in CE:
    FAQ-only AND no prepurchase signal AND no explicit inspection request AND no catalog vehicle.
    (lookup_vehicle and state not tested here — tests cover the detection functions only.)
    """
    text_norm = _norm_text(text)
    return (
        detect_general_information(text)
        and not detect_prepurchase_signal(text)
        and not detect_explicit_inspection_request(text)
    )


# ── B5-01-A: "quería" inspection intent ──────────────────────────────────────

class TestBI01QueriRevisarUnAuto(unittest.TestCase):
    def test_bi01_queria_revisar_un_auto(self):
        self.assertTrue(
            detect_explicit_inspection_request("Quería revisar un auto."),
            "BI01: 'quería revisar un auto' must be detected as inspection intent",
        )


class TestBI02QueriHacerRevisionPrecompra(unittest.TestCase):
    def test_bi02_queria_hacer_revision_precompra(self):
        self.assertTrue(
            detect_explicit_inspection_request("Quería hacer una revisión precompra."),
            "BI02: 'quería hacer una revisión precompra' must be inspection intent",
        )


class TestBI03QueriQueRevisaran(unittest.TestCase):
    def test_bi03_queria_que_revisaran(self):
        self.assertTrue(
            detect_explicit_inspection_request(
                "Quería que revisaran un auto antes de comprarlo."
            ),
            "BI03: 'quería que revisaran' must be inspection intent",
        )


class TestBI04NegativeSaberHorarios(unittest.TestCase):
    def test_bi04_queria_saber_horarios_not_inspection(self):
        self.assertFalse(
            detect_explicit_inspection_request("Quería saber los horarios."),
            "BI04: 'quería saber' must NOT be inspection intent",
        )


class TestBI05NegativeConsultar(unittest.TestCase):
    def test_bi05_queria_consultar_not_inspection(self):
        self.assertFalse(
            detect_explicit_inspection_request("Quería consultar algo."),
            "BI05: 'quería consultar' must NOT be inspection intent",
        )


class TestBI06NegativeHablar(unittest.TestCase):
    def test_bi06_queria_hablar_not_inspection(self):
        self.assertFalse(
            detect_explicit_inspection_request("Quería hablar con alguien."),
            "BI06: 'quería hablar' must NOT be inspection intent",
        )


class TestBI07NegativeVender(unittest.TestCase):
    def test_bi07_queria_vender_not_inspection(self):
        self.assertFalse(
            detect_explicit_inspection_request("Quería vender mi auto."),
            "BI07: 'quería vender' must NOT be inspection intent",
        )


class TestBI08QueriUnaInspeccion(unittest.TestCase):
    def test_bi08_queria_una_inspeccion(self):
        self.assertTrue(
            detect_explicit_inspection_request("Quería una inspección precompra."),
            "BI08: 'quería una inspección' must be inspection intent",
        )


class TestBI09QueriCotizar(unittest.TestCase):
    def test_bi09_queria_cotizar(self):
        self.assertTrue(
            detect_explicit_inspection_request("Quería cotizar la revisión."),
            "BI09: 'quería cotizar' must be inspection intent",
        )


# ── B5-01-B: Service composition and payment FAQ detection ───────────────────

class TestBC01DeQueConstaServicio(unittest.TestCase):
    def test_bc01_de_que_consta_el_servicio(self):
        self.assertTrue(
            detect_general_information("¿De qué consta el servicio?"),
            "BC01: 'de qué consta el servicio' must match detect_general_information",
        )


class TestBC02EnQueConsiste(unittest.TestCase):
    def test_bc02_en_que_consiste(self):
        self.assertTrue(
            detect_general_information("¿En qué consiste la revisión?"),
            "BC02: 'en qué consiste' must match detect_general_information",
        )


class TestBC03SePublicaPagarDebito(unittest.TestCase):
    def test_bc03_se_puede_pagar_en_debito(self):
        self.assertTrue(
            detect_general_information("¿Se puede pagar en débito?"),
            "BC03: 'se puede pagar en débito' must match detect_general_information",
        )


class TestBC04ComoSePaga(unittest.TestCase):
    def test_bc04_como_se_paga(self):
        self.assertTrue(
            detect_general_information("¿Cómo se paga?"),
            "BC04: '¿Cómo se paga?' must match detect_general_information",
        )


class TestBC05AceptanMercadoPago(unittest.TestCase):
    def test_bc05_aceptan_mercado_pago(self):
        self.assertTrue(
            detect_general_information("¿Aceptan Mercado Pago?"),
            "BC05: '¿Aceptan Mercado Pago?' must match detect_general_information",
        )


class TestBC06MediosDePago(unittest.TestCase):
    def test_bc06_medios_de_pago(self):
        self.assertTrue(
            detect_general_information("¿Qué medios de pago tienen?"),
            "BC06: '¿Qué medios de pago?' must match detect_general_information",
        )


class TestBC07CombinedInspectionPlusFAQ(unittest.TestCase):
    """B5-01 golden path: inspection intent detected → Layer F returns None → AI handles all."""

    def test_bc07_inspection_detected_in_combined(self):
        combined = "Quería revisar un auto. ¿De qué consta el servicio?"
        self.assertTrue(
            detect_explicit_inspection_request(combined),
            "BC07: combined text must trigger inspection intent detection",
        )

    def test_bc07_layer_d_does_not_fire_when_inspection_present(self):
        combined = "Quería revisar un auto. ¿De qué consta el servicio?"
        self.assertFalse(
            layer_d_fires(combined),
            "BC07: Layer D must NOT fire when inspection intent is present "
            "(Layer F step 1 handles it)",
        )


class TestBC08FAQOnlyLayerD(unittest.TestCase):
    """FAQ-only path: Layer D fires when no prepurchase signal."""

    def test_bc08_faq_only_detect_general_information_true(self):
        faq_only = "¿De qué consta el servicio? ¿Se puede pagar en débito?"
        self.assertTrue(
            detect_general_information(faq_only),
            "BC08: FAQ-only text must match detect_general_information",
        )

    def test_bc08_no_prepurchase_signal(self):
        faq_only = "¿De qué consta el servicio? ¿Se puede pagar en débito?"
        self.assertFalse(
            detect_prepurchase_signal(faq_only),
            "BC08: FAQ-only text must NOT trigger prepurchase signal",
        )

    def test_bc08_layer_d_fires(self):
        faq_only = "¿De qué consta el servicio? ¿Se puede pagar en débito?"
        self.assertTrue(
            layer_d_fires(faq_only),
            "BC08: Layer D must fire for FAQ-only text (routes to AI FAQ handler)",
        )


class TestBC09PaymentQuestions(unittest.TestCase):
    def test_bc09_pagar_en_efectivo(self):
        self.assertTrue(detect_general_information("¿Se puede pagar en efectivo?"))

    def test_bc09_aceptan_debito(self):
        self.assertTrue(detect_general_information("¿Aceptan débito?"))

    def test_bc09_formas_de_pago(self):
        self.assertTrue(detect_general_information("¿Qué formas de pago aceptan?"))

    def test_bc09_pagar_con_credito(self):
        self.assertTrue(detect_general_information("¿Puedo pagar con tarjeta de crédito?"))

    def test_bc09_pagar_por_transferencia(self):
        self.assertTrue(detect_general_information("¿Puedo pagar por transferencia?"))


# ── Layer F Step 3 pass-through phrases ──────────────────────────────────────

class TestBS01SoftCloseDeQueConsta(unittest.TestCase):
    def test_bs01_de_que_consta(self):
        self.assertTrue(
            _is_general_faq_or_soft_close("¿De qué consta el servicio?"),
            "BS01: 'de qué consta' must match _is_general_faq_or_soft_close",
        )


class TestBS02SoftClosePagarEnDebito(unittest.TestCase):
    def test_bs02_pagar_en_debito(self):
        self.assertTrue(
            _is_general_faq_or_soft_close("¿Se puede pagar en débito?"),
            "BS02: 'pagar en debito' must match _is_general_faq_or_soft_close",
        )


class TestBS03SoftCloseMediosDePago(unittest.TestCase):
    def test_bs03_medios_de_pago(self):
        self.assertTrue(
            _is_general_faq_or_soft_close("¿Medios de pago?"),
            "BS03: 'medios de pago' must match _is_general_faq_or_soft_close",
        )


# ── Multi-intent orchestration (Part D) ──────────────────────────────────────

class TestBD01MultiIntentInspectionAndService(unittest.TestCase):
    """Qualification evidence AND FAQ question in one turn — inspection wins routing."""

    def test_bd01_quiero_revisar_plus_que_incluye(self):
        text = "Quiero revisar un Focus 2019. ¿Qué incluye la revisión?"
        self.assertTrue(detect_explicit_inspection_request(text))
        self.assertFalse(layer_d_fires(text))

    def test_bd01_queria_revisar_plus_de_que_consta(self):
        text = "Quería revisar un auto. ¿De qué consta el servicio?"
        self.assertTrue(detect_explicit_inspection_request(text))
        self.assertFalse(layer_d_fires(text))

    def test_bd01_queria_revisar_plus_payment(self):
        text = "Quería revisar un auto. ¿Se puede pagar en débito?"
        self.assertTrue(detect_explicit_inspection_request(text))
        self.assertFalse(layer_d_fires(text))


class TestBD02FAQOnlyRoutesLayerD(unittest.TestCase):
    """FAQ-only message (no inspection signal) routes through Layer D."""

    def test_bd02_que_incluye_alone(self):
        text = "¿Qué incluye la revisión?"
        self.assertTrue(detect_general_information(text))
        self.assertFalse(detect_prepurchase_signal(text))
        self.assertTrue(layer_d_fires(text))

    def test_bd02_de_que_consta_alone(self):
        text = "¿De qué consta el servicio?"
        self.assertTrue(detect_general_information(text))
        self.assertFalse(detect_prepurchase_signal(text))
        self.assertTrue(layer_d_fires(text))

    def test_bd02_payment_alone(self):
        text = "¿Se puede pagar con Mercado Pago?"
        self.assertTrue(detect_general_information(text))
        self.assertFalse(detect_prepurchase_signal(text))
        self.assertTrue(layer_d_fires(text))


# ── Regression guards ────────────────────────────────────────────────────────

class TestBR01ExistingPresentTenseStillWorks(unittest.TestCase):
    def test_br01_quiero_revisar_un_auto(self):
        self.assertTrue(detect_explicit_inspection_request("quiero revisar un auto"))

    def test_br02_quisiera_revisar_un_auto(self):
        self.assertTrue(detect_explicit_inspection_request("quisiera revisar un auto"))

    def test_br03_quiero_que_me_revisen(self):
        self.assertTrue(detect_explicit_inspection_request("quiero que me revisen el auto"))

    def test_br04_coordinar_una_revision(self):
        self.assertTrue(detect_explicit_inspection_request("coordinar una revisión"))

    def test_br05_necesito_una_inspeccion(self):
        self.assertTrue(detect_explicit_inspection_request("necesito una inspección precompra"))

    def test_br06_quiero_cotizar(self):
        self.assertTrue(detect_explicit_inspection_request("quiero cotizar"))


class TestBR02ExistingFAQPatternsStillWork(unittest.TestCase):
    def test_br_que_incluye(self):
        self.assertTrue(detect_general_information("¿Qué incluye la revisión?"))

    def test_br_que_revisan(self):
        self.assertTrue(detect_general_information("¿Qué revisan?"))

    def test_br_como_funciona(self):
        self.assertTrue(detect_general_information("¿Cómo funciona?"))

    def test_br_cuanto_tarda(self):
        self.assertTrue(detect_general_information("¿Cuánto tarda la revisión?"))

    def test_br_salen_a_domicilio(self):
        self.assertTrue(detect_general_information("¿Salen a domicilio?"))


class TestBR03SoftClosePhraseRegressions(unittest.TestCase):
    def test_br_gracias(self):
        self.assertTrue(_is_general_faq_or_soft_close("Gracias!"))

    def test_br_lo_tengo_en_cuenta(self):
        self.assertTrue(_is_general_faq_or_soft_close("Lo tengo en cuenta."))

    def test_br_te_aviso(self):
        self.assertTrue(_is_general_faq_or_soft_close("Te aviso cuando lo decida."))

    def test_br_me_pasas_info(self):
        self.assertTrue(_is_general_faq_or_soft_close("¿Me pasás info?"))


if __name__ == "__main__":
    unittest.main()

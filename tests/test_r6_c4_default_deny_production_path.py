"""R6-C4 — default-deny temporal validation on the FULL production path.

The R6 / R6-C1 / R6-C2 / R6-C3 harness (`handle_message` → CE → reply model with a canned
provider answer → reply admission → `_send_text_to_wa` → `_validate_outbound_text` →
`plan.compose` → OutboundSafetyGate → Meta recorder), R1/R2/R3 interlock armed, in every
posture: CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED false / true × SMART_BOOKING_CE_ENABLED
false / true.

Every audit S01 / S02 counterexample (R6C3RV-S01 / S02), the booking restatements, the
reviewer's surviving mutants R03 / R05 / R07 / R08 / R09 / R18 / R20 / R22 and a deterministic
sample of the generated token-coverage corpus (`test_r6_c4_default_deny.generate_corpus`) run
here; every wrong case asserts no false text, no stored preferred day/time, no revision or
booking, and any outstanding question is the fallback's declared one. R16 (the producer
gate) is killed at unit level, where the reviewer also proved it (§6 of the audit).

Database: SQLite in memory; set R6_PG_URL to run the identical cases on PostgreSQL.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import date, datetime, time, timezone

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_r6_c2_day_date_production_path as DD  # noqa: E402  (the R6-C2 harness)
import test_r6_c4_default_deny as U  # noqa: E402  (the R6-C4 cases and generated corpus)
import test_r6_full_engine_prose_corpus as C  # noqa: E402

MON, WED, MON23 = DD.MON, DD.WED, "2026-11-23"
BOOKED = (date(2026, 11, 16), time(10, 0))
NOON_MON = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)     # BA Mon 16 12:00


class _Base(DD._Base):

    def not_delivered(self, reply, forbidden, *, booked=None, now=DD.NOON_WED, **seed):
        """Refused, but other sentences may survive (surgery): the false part is gone."""
        for sb, direct in C.POSTURES:
            with self.subTest(reply=reply, seed=seed, booked=booked, sb=sb, direct=direct):
                r = DD.run_case(self.eng, reply, sb, direct, now=now, booked=booked, **seed)
                self._common(r, booked=bool(booked))
                if r["route"] in DD.PROSE_NEVER_DELIVERED:   # R6-C4A / R6-C4B-R1
                    DD.assert_ai_body_discarded(self, r, reply, forbidden, booked=bool(booked))
                    continue
                self.assertFalse(DD._same(r["sent"][0], reply), f"false text delivered: {r}")
                for frag in forbidden:
                    self.assertNotIn(frag, r["sent"][0], f"false content delivered: {r}")
                self.assertIn("AVAILABILITY", r["refused"], r)
                for _kind, by in r["oq"]:
                    self.assertEqual(by, "DETERMINISTIC_TEMPLATE", r)


# ── R6C3RV-S01 / S02: every audit counterexample, with its canonical control ──────

def _forbidden(text):
    return [w for w in ("23", "dieciséis", "doce", "veinte", "12 días", "otro", "siguiente",
                        "viene", "jueves 19", "nueve") if w in text]


class P01_AuditCounterexamples(_Base):

    def test_s01_all(self):
        for cid, wrong, right, _kind in U.S01:
            with self.subTest(cid=cid):
                self.not_delivered(wrong, _forbidden(wrong), date_iso=MON)
                self.kept(right, date_iso=MON)

    def test_s02_all(self):
        for cid, wrong, right, _kind in U.S02:
            with self.subTest(cid=cid):
                self.not_delivered(wrong, _forbidden(wrong), date_iso=MON)
                self.kept(right, date_iso=MON)

    def test_lead_cases_fall_back_whole(self):
        # the reviewer's smallest counterexamples: one sentence, nothing true survives
        for reply in ("No hay turnos el jueves, sí el lunes 23 a las 10:00.",
                      "Te esperamos el lunes veintitrés a las 10:00.",
                      "Te esperamos el 2026/11/23 a las 10:00.",
                      "Te esperamos en 12 días a las 10:00.",
                      "Te esperamos el lunes (23) a las 10:00.",
                      "Te esperamos el lun 23 a las 10:00.",
                      "Te esperamos el otro lunes a las 10:00.",
                      "Te esperamos el lunes 16 a las dieciséis.",
                      "Te esperamos el lunes 16 a las diez y veinte.",
                      "Te reservo el lunes 16 para las doce."):
            self.refused(reply, [], date_iso=MON)

    def test_h1_next_monday_said_on_monday(self):
        for reply in ("Te esperamos el próximo lunes a las 10:00.",
                      "Te esperamos el lunes que viene a las 10:00.",
                      "Te esperamos el lunes a las 10:00."):
            self.refused(reply, ["lunes"], date_iso=MON, now=NOON_MON)
        self.kept("Te esperamos hoy lunes a las 10:00.", date_iso=MON, now=NOON_MON)


class P02_ConfirmedBooking(_Base):

    def test_booking_restatements(self):
        for cid, wrong, right, *rest in U.BOOKING:
            if rest:
                continue                         # needs a 10:15 booking; unit level only
            with self.subTest(cid=cid):
                self.not_delivered(wrong, _forbidden(wrong), booked=BOOKED, date_iso=MON)
                self.kept(right, booked=BOOKED, date_iso=MON)

    def test_r18_no_negative_exemption_under_a_booking(self):
        self.not_delivered("No tenemos turnos el jueves; tu turno es el lunes 23 a las 10:00.",
                           ["23"], booked=BOOKED, date_iso=MON)
        self.not_delivered("No tenemos turnos el jueves. Tu turno es el lunes 16 a las 10:00.",
                           ["jueves"], booked=BOOKED, date_iso=MON)


# ── R6C3RV-T01: the reviewer's surviving mutants on the full path ────────────────

class P03_ReviewerMutants(_Base):

    def test_r03_month_of_a_yearless_date(self):
        self.refused("Te esperamos el 16/12 a las 10:00.", ["16/12"], date_iso=MON)
        self.refused("Te esperamos el 16 de diciembre a las 10:00.", ["diciembre"], date_iso=MON)
        self.kept("Te esperamos el 16/11 a las 10:00.", date_iso=MON)

    def test_r05_pero(self):
        self.refused("No hay turnos el jueves, pero el lunes 23 hay a las 10:00.", ["23"],
                     date_iso=MON)

    def test_r07_number_on_a_month_word(self):
        self.refused("Te esperamos 23-nov a las 10:00.", ["23"], date_iso=MON)
        self.kept("Te esperamos 16-nov a las 10:00.", date_iso=MON)

    def test_r08_meridiem(self):
        self.refused("Te esperamos el lunes a las once pm.", ["once"], date_iso=MON)
        self.refused("Te esperamos el lunes a las 10:00 hs pm.", ["pm"], date_iso=MON)
        self.kept("Te esperamos el lunes a las diez am.", date_iso=MON)

    def test_r09_menos_cuarto(self):
        self.refused("Te esperamos el lunes 16 a las 10 menos cuarto.", ["menos"], date_iso=MON,
                     offered=["10:45"], visible=["10:45"])
        self.kept("Te esperamos el lunes 16 a las 10 menos cuarto.", date_iso=MON,
                  offered=["09:45"], visible=["09:45"])

    def test_r20_iso_datetime_time(self):
        self.refused("Te esperamos el 2026-11-16T12:00.", ["12:00"], date_iso=MON)
        self.kept("Te esperamos el 2026-11-16T10:00.", date_iso=MON)

    def test_r22_esta_tarde_is_today(self):
        self.refused("Te esperamos esta tarde a las 15:00.", ["tarde"], date_iso=MON)
        self.kept("Te esperamos esta tarde a las 15:00.", date_iso=WED)


# ── Preserved: plain negatives, CE templates' words, ordinary prose, hours ────────

class P04_Controls(_Base):

    def test_plain_negatives(self):
        for reply in ("No tenemos turnos disponibles el jueves.",
                      "Para el jueves no hay horarios disponibles.", "Los domingos no trabajamos."):
            self.kept(reply, date_iso=MON)

    def test_ordinary_prose_and_inert_numbers(self):
        for reply in ("Tu Corolla 2018 lo revisamos el lunes 16 a las 10:00.",
                      "Te esperamos el lunes 16 a las 10:00 en Av. 9 de Julio 1500.",
                      "La revisión cubre 150 puntos y tu auto tiene 80.000 km.",
                      "La revisión dura unas 2 horas. Te esperamos el lunes 16 a las 10:00.",
                      "Tengo 10:00 y 11:00. ¿Cuál de las dos te sirve?",
                      "Atendemos de lunes a sábado de 9 a 18 hs."):
            self.kept(reply, date_iso=MON)


# ── The generated token-coverage corpus on the full path (deterministic sample) ──

def full_path_sample(target=64):
    corpus = U.generate_corpus()
    step = max(1, len(corpus) // target)
    picked = corpus[::step]
    # make sure every frame and both outcomes are represented
    frames = {c[3].split("/")[0] for c in picked}
    extra = [c for c in corpus if c[3].split("/")[0] not in frames and c[2] == "kept"][:1]
    kept_frames = {c[3].split("/")[0] for c in picked if c[2] == "kept"}
    for name, _tpl, _kind in U.FRAMES:
        if name != "bookneg" and name not in kept_frames:
            extra += [c for c in corpus if c[3].startswith(name + "/") and c[2] == "kept"][:1]
    return picked + extra


class P05_GeneratedCorpus(_Base):

    def test_generated_sample_all_postures(self):
        sample = full_path_sample()
        kept = refused = 0
        for text, kind, want, label in sample:
            booked = BOOKED if kind == "booking" else None
            with self.subTest(label=label):
                if want == "kept":
                    self.kept(text, booked=booked, date_iso=MON)
                    kept += 1
                else:
                    self.not_delivered(text, [], booked=booked, date_iso=MON)
                    refused += 1
        print(f"\nR6-C4 generated full-path sample: {len(sample)} cases × {len(C.POSTURES)} "
              f"postures; refused {refused}, kept {kept}")
        self.assertGreater(kept, 8)
        self.assertGreater(refused, 40)


if __name__ == "__main__":
    unittest.main()

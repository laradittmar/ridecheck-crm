"""Build `real_world_turns.jsonl` from human-authored labels.

The labels in this file are BUSINESS truth written by engineering/owner judgement.
They were not produced by asking a model (see docs/semantic/SEMANTIC_TRUTH_MODEL.md §5).

Run:  python tests/semantic_corpus/build_corpus.py
The generated JSONL is committed; this script documents its provenance and lets the corpus
be regenerated deterministically.
"""
from __future__ import annotations

import json
import pathlib

OUT = pathlib.Path(__file__).with_name("real_world_turns.jsonl")

SCHEMA_VERSION = "1.0"

# ── helpers ───────────────────────────────────────────────────────────────────

def ev(field, value, status, role=None, note=None):
    item = {"field": field, "value": value, "status": status}
    if role:
        item["role"] = role
    if note:
        item["note"] = note
    return item


def case(cid, kind, source, messages, groups, turn_evidence, canonical=None,
         missing=None, next_action="ASK_CLARIFICATION", must_not=None,
         owner_review=False, failure_class=None, note=None):
    entry = {
        "id": cid,
        "schema_version": SCHEMA_VERSION,
        "provenance": {"kind": kind, "source": source},
        "groups": groups,
        "raw": {"lang": "es-AR", "messages": messages},
        "expected_turn_evidence": turn_evidence,
        "expected_canonical_state": canonical or {},
        "expected_missing_fields": missing or [],
        "expected_next_action": next_action,
        "must_not_infer": must_not or [],
        "owner_review_required": owner_review,
    }
    if failure_class:
        entry["failure_class"] = failure_class
    if note:
        entry["note"] = note
    return entry


CASES: list[dict] = []

# ── REAL — owner-provided customer language (verbatim, 2026-09-01) ────────────

CASES.append(case(
    "REAL-001", "REAL", "owner-provided real customer message, 2026-09-01",
    ["Hola por ahora estoy buscando un auto agende esto para no perderlo asijina vez q decida aviso"],
    ["A", "K", "L"],
    [
        ev("service_intent", "PREPURCHASE_INSPECTION", "PROPOSED",
           note="interest in the service exists, but no concrete inspection is requested yet"),
        ev("readiness", "SEARCHING_NOT_READY", "CONFIRMED",
           note="customer is still looking for a vehicle and will contact again"),
        ev("vehicle", None, "AMBIGUOUS", note="no vehicle named"),
        ev("inspection_location", None, "AMBIGUOUS"),
    ],
    canonical={"candidate": None, "inspection_location": None, "quote": None,
               "stage": "QUALIFYING"},
    missing=["vehicle", "inspection_location", "scheduling"],
    next_action="ACKNOWLEDGE_AND_REMAIN_AVAILABLE",
    must_not=[{"field": "vehicle", "reason": "no vehicle was named"},
              {"field": "inspection_location", "reason": "no location was named"},
              {"field": "quote", "reason": "not quote-ready"},
              {"field": "scheduling_preference", "reason": "no scheduling request"}],
    note="Typo fragment 'asijina' is unresolved and must not be over-interpreted.",
))

CASES.append(case(
    "REAL-002", "REAL", "owner-provided real customer message, 2026-09-01",
    ["Ok. Lobveobyo primoroby si me vierra t hablobpara q lo revisen"],
    ["A", "K", "L"],
    [
        ev("service_intent", "PREPURCHASE_INSPECTION", "PROPOSED",
           note="'para q lo revisen' suggests future inspection intent"),
        ev("readiness", "FUTURE_CONTACT_INTENDED", "PROPOSED",
           note="customer appears to intend to see the vehicle first and write back"),
        ev("vehicle", None, "AMBIGUOUS", note="no resolvable vehicle in noisy text"),
        ev("inspection_location", None, "AMBIGUOUS"),
    ],
    canonical={"candidate": None, "inspection_location": None, "quote": None,
               "stage": "QUALIFYING"},
    missing=["vehicle", "inspection_location", "scheduling"],
    next_action="ACKNOWLEDGE_AND_REMAIN_AVAILABLE",
    must_not=[{"field": "vehicle", "reason": "noise must not be resolved into a model"},
              {"field": "inspection_location", "reason": "no location was named"},
              {"field": "quote", "reason": "nothing to quote"}],
    owner_review=True,
    note="Heavily noisy/voice-like. Components stay PROPOSED/AMBIGUOUS by design.",
))

CASES.append(case(
    "REAL-003", "REAL", "owner-provided real customer message, 2026-09-01",
    ["Quiero comprar un fox y ver en qu3 estado esta en breves te voy a estar hablando si todo marcha bieb michas gracias"],
    ["A", "B", "K", "L"],
    [
        ev("service_intent", "PREPURCHASE_INSPECTION", "CONFIRMED",
           note="'quiero comprar ... y ver en qué estado está' is pre-purchase condition intent"),
        ev("vehicle", "Volkswagen Fox", "PROPOSED", role="VEHICLE_OF_INTEREST",
           note="'fox' resolves uniquely in the catalog; year unknown"),
        ev("vehicle_year", None, "AMBIGUOUS"),
        ev("inspection_location", None, "AMBIGUOUS"),
        ev("readiness", "FUTURE_CONTACT_INTENDED", "CONFIRMED",
           note="'en breves te voy a estar hablando'"),
    ],
    canonical={"candidate": {"marca": "Volkswagen", "modelo": "Fox", "anio": None},
               "inspection_location": None, "quote": None, "stage": "QUALIFYING"},
    missing=["vehicle_year", "inspection_location", "scheduling"],
    next_action="ASK_LOCATION",
    must_not=[{"field": "vehicle_year", "reason": "no year was stated"},
              {"field": "inspection_location", "reason": "no location was stated"},
              {"field": "quote", "reason": "location unknown, cannot price"}],
    note="Catalog uniqueness may promote the vehicle to CONFIRMED at reconciliation.",
))

CASES.append(case(
    "REAL-004", "REAL", "owner-provided real customer message, 2026-09-01",
    ["Hola qué tal! Te quería consultar cotización para ir a revisar un auto a La Plata , yo cuento con movilidad como para pasar a buscarlos y ir a chequear el auto allá y volver obviamente, espero tu msj!"],
    ["A", "C", "D"],
    [
        ev("service_intent", "PREPURCHASE_INSPECTION", "CONFIRMED"),
        ev("quote_request", True, "CONFIRMED", note="'te quería consultar cotización'"),
        ev("inspection_location", "La Plata", "CONFIRMED", role="INSPECTION_LOCATION"),
        ev("vehicle", None, "AMBIGUOUS", note="'un auto' is generic, not a catalog vehicle"),
        ev("customer_logistics_offer", "CUSTOMER_OFFERS_TRANSPORT", "CONFIRMED",
           note="offer to drive the inspector; a commercial exception, not a price rule"),
    ],
    canonical={"candidate": None, "inspection_location": "La Plata", "quote": None,
               "stage": "QUALIFYING"},
    missing=["vehicle", "quote"],
    next_action="ASK_VEHICLE",
    must_not=[{"field": "vehicle", "reason": "'un auto' names no model"},
              {"field": "quote", "reason": "vehicle unknown; and the mobility offer must not change pricing automatically"},
              {"field": "viaticos_waived", "reason": "coverage/exception is a business decision, not an interpretation"}],
    owner_review=True,
    note="Owner must define whether customer-provided transport changes viáticos or triggers human handling.",
))

# ── REAL — imported failed/known Wild utterances ─────────────────────────────

WILD_A_SRC = "2026-09-01_RIDECHECK_CRM_L4-WILD-A-SCHEDULING-FORENSIC_AUDIT_TEMPORAL-FLOW.md"
WILD_B_SRC = "2026-09-01_RIDECHECK_CRM_L4-WILD-B-VEHICLE-FORENSIC_AUDIT_CANDIDATE-PERSISTENCE.md"
WILD_1_SRC = "2026-09-01_RIDECHECK_CRM_L4-WILD-01-FORENSIC_AUDIT_FIRST-MESSAGE-FAILURE.md"

CASES.append(case(
    "WILD-A-01", "REAL", f"Wild A turn 1 — {WILD_A_SRC}",
    ["Hola, ¿cómo están? Bueno, quería revisar una 2008 del 2014. ¿Ustedes hacen eso?",
     "¿Cómo es el servicio? ¿Es en un informe? ¿Qué tiene el informe? ¿Tengo que estar presente?",
     "¿Se puede pagar con débito?"],
    ["A", "B", "J"],
    [
        ev("service_intent", "PREPURCHASE_INSPECTION", "CONFIRMED"),
        ev("vehicle", "Peugeot 2008", "CONFIRMED", role="VEHICLE_OF_INTEREST"),
        ev("vehicle_year", 2014, "CONFIRMED"),
        ev("faq_topics", ["service_scope", "report", "presence", "payment"], "CONFIRMED"),
    ],
    canonical={"candidate": {"marca": "Peugeot", "modelo": "2008", "anio": 2014,
                             "tipo_vehiculo": "SUV_4X4_DEPORTIVO"},
               "inspection_location": None, "quote": None, "stage": "QUALIFYING"},
    missing=["inspection_location"], next_action="ANSWER_FAQ_AND_ASK_LOCATION",
    must_not=[{"field": "inspection_location", "reason": "not stated in this burst"},
              {"field": "quote", "reason": "location unknown"}],
    note="Baseline that worked before L4.6 — the counterpart of WILD-B-01.",
))

CASES.append(case(
    "WILD-A-02", "REAL", f"Wild A turn 2 — {WILD_A_SRC}",
    ["El auto está en Berazategui. Yo soy de Tigre. No sé si eso tiene algo que ver."],
    ["C"],
    [
        ev("inspection_location", "Berazategui", "CONFIRMED", role="INSPECTION_LOCATION"),
        ev("customer_origin", "Tigre", "CONFIRMED", role="CUSTOMER_ORIGIN"),
    ],
    canonical={"inspection_location": "Berazategui", "zone_group": "Sur"},
    next_action="QUOTE",
    must_not=[{"field": "inspection_location", "value": "Tigre",
               "reason": "customer origin is never the inspection location"}],
))

CASES.append(case(
    "WILD-A-03", "REAL", f"Wild A turn 3 — {WILD_A_SRC}",
    ["Si avancemos", "Que horarios hacen ?"],
    ["E", "D"],
    [
        ev("acceptance", True, "CONFIRMED", note="'sí avancemos' accepts the quote"),
        ev("faq_topics", ["business_hours"], "CONFIRMED"),
    ],
    canonical={"stage": "SCHEDULING", "lead_flag": "ACEPTADO"},
    next_action="ASK_DAY_AND_TIME",
    must_not=[{"field": "scheduling_preference", "reason": "no day or time was given yet"}],
))

CASES.append(case(
    "WILD-A-04", "REAL", f"Wild A scheduling turn — {WILD_A_SRC}",
    ["Mñ 15hs? O nose jueves que tenes"],
    ["G", "H", "K"],
    [
        ev("scheduling_preference", [{"day": "TOMORROW", "time": "15:00", "rank": 1},
                                     {"day": "THURSDAY", "time": None, "rank": 2}],
           "CONFIRMED", note="ordered branches: primary tomorrow 15:00, fallback Thursday open"),
    ],
    canonical={"scheduling_primary": {"day": "TOMORROW", "time": "15:00"},
               "scheduling_fallback": {"day": "THURSDAY", "time": None}},
    next_action="EVALUATE_PRIMARY_THEN_FALLBACK",
    must_not=[{"field": "scheduling_preference",
               "value": {"day": "THURSDAY", "time": "15:00"},
               "reason": "the 15:00 belongs to the tomorrow branch, never to Thursday"}],
    failure_class="SCHED-A/SCHED-B (L4-WILD-A) — primary branch discarded, time transplanted",
))

CASES.append(case(
    "WILD-B-01", "REAL", f"Wild B turn 1 — {WILD_B_SRC}",
    ["Hola, para revisar un 2008 del 2014, ¿ustedes hacen ese servicio?",
     "¿Entregan informes? ¿Qué contenido tienen los informes? ¿Tengo que estar yo presente?",
     "¿Se puede pagar con débito?"],
    ["A", "B", "J"],
    [
        ev("service_intent", "PREPURCHASE_INSPECTION", "CONFIRMED",
           note="a purpose clause is intent; no modal verb is required"),
        ev("vehicle", "Peugeot 2008", "CONFIRMED", role="VEHICLE_OF_INTEREST"),
        ev("vehicle_year", 2014, "CONFIRMED"),
        ev("faq_topics", ["report", "presence", "payment"], "CONFIRMED"),
    ],
    canonical={"candidate": {"marca": "Peugeot", "modelo": "2008", "anio": 2014,
                             "tipo_vehiculo": "SUV_4X4_DEPORTIVO"},
               "inspection_location": None, "quote": None, "stage": "QUALIFYING"},
    missing=["inspection_location"], next_action="ANSWER_FAQ_AND_ASK_LOCATION",
    must_not=[{"field": "inspection_location", "reason": "not stated"},
              {"field": "quote", "reason": "location unknown"}],
    failure_class="VEH-A/VEH-B (L4-WILD-B) — evidence discarded, vehicle asserted without state",
))

CASES.append(case(
    "WILD-B-02", "REAL", f"Wild B location turn — {WILD_B_SRC}",
    ["Está en Berazategui, pero yo soy de Tigre."],
    ["C"],
    [
        ev("inspection_location", "Berazategui", "CONFIRMED", role="INSPECTION_LOCATION",
           note="subjectless location clause still states where the vehicle is"),
        ev("customer_origin", "Tigre", "CONFIRMED", role="CUSTOMER_ORIGIN"),
    ],
    canonical={"inspection_location": "Berazategui", "zone_group": "Sur"},
    next_action="QUOTE",
    must_not=[{"field": "inspection_location", "value": "Tigre",
               "reason": "customer origin is never the inspection location"}],
    failure_class="LOC-A (L4-WILD-B) — origin clause suppressed the inspection location",
))

CASES.append(case(
    "WILD-01-01", "REAL", f"Wild #1 turn 1 — {WILD_1_SRC} (text truncated in the source artifact)",
    ["Hola, quería saber si hacían revisiones de un 2008 del 2015..."],
    ["A", "B"],
    [
        ev("service_intent", "PREPURCHASE_INSPECTION", "CONFIRMED"),
        ev("vehicle", "Peugeot 2008", "CONFIRMED", role="VEHICLE_OF_INTEREST"),
        ev("vehicle_year", 2015, "CONFIRMED"),
        ev("inspection_location", None, "AMBIGUOUS"),
    ],
    canonical={"candidate": {"marca": "Peugeot", "modelo": "2008", "anio": 2015},
               "inspection_location": None, "quote": None},
    missing=["inspection_location"], next_action="ASK_LOCATION",
    must_not=[{"field": "inspection_location",
               "reason": "Wild #1 quoted $240.000 from a stale prior-cycle zone"},
              {"field": "quote", "reason": "no location in this cycle"}],
    failure_class="DEFECT-WILD-01-A — quote produced without a location",
    note="RAW is truncated in the committed artifact; not reconstructed here.",
))

CASES.append(case(
    "WILD-01-02", "REAL", f"Wild #1 payment turn — {WILD_1_SRC}",
    ["¡Se puede pagar con Debito!"],
    ["J"],
    [ev("faq_topics", ["payment"], "CONFIRMED")],
    canonical={}, next_action="ANSWER_FAQ",
    must_not=[{"field": "payment_accepted", "value": "debito",
               "reason": "debit is not an accepted method"}],
))

# ── SYNTHETIC equivalence groups ─────────────────────────────────────────────
# Each group asserts: many surface forms → the same structured meaning.

def add_group(prefix, group, texts, evidence_fn, canonical_fn=None, missing=None,
              next_action="ASK_CLARIFICATION", must_not=None, note=None):
    for i, text in enumerate(texts, start=1):
        CASES.append(case(
            f"{prefix}-{i:02d}", "SYNTHETIC", "authored variant (L4.7E)",
            [text], [group], evidence_fn(text),
            canonical=(canonical_fn(text) if canonical_fn else None),
            missing=missing, next_action=next_action, must_not=must_not, note=note,
        ))


# A — inspection / pre-purchase intent (20 surface forms, one meaning)
INTENT_TEXTS = [
    "Hola, quiero revisar un auto antes de comprarlo",
    "Para revisar un auto que estoy por comprar",
    "Necesito una revisión pre compra",
    "Me gustaría que chequeen un auto antes de la compra",
    "Vengo a que me revisen un usado",
    "Estoy por comprar un usado y quiero verlo antes",
    "Hacen inspección antes de comprar?",
    "Quería consultar por una revisión precompra",
    "Buenas, hacen revisiones de autos usados antes de comprar?",
    "Ando por comprar un auto y necesito que alguien lo mire",
    "Quisiera una inspección del vehículo antes de cerrar la operación",
    "Che, revisan autos antes de que uno los compre?",
    "Consulta: chequean un auto que quiero comprar?",
    "Estoy mirando un usado, me gustaría una revisión técnica antes",
    "Me interesa una revisión antes de señarlo",
    "Puedo contratar una inspección para un auto que voy a comprar?",
    "Hola! Quería coordinar una revisión de un usado",
    "Necesito que revisen un auto que vi en una agencia",
    "Antes de comprarlo quiero que lo revise alguien de confianza",
    "Quiero contratar el servicio de revisión pre compra",
]
add_group("SYN-INTENT", "A", INTENT_TEXTS,
          lambda t: [ev("service_intent", "PREPURCHASE_INSPECTION", "CONFIRMED"),
                     ev("vehicle", None, "AMBIGUOUS"),
                     ev("inspection_location", None, "AMBIGUOUS")],
          canonical_fn=lambda t: {"candidate": None, "inspection_location": None,
                                  "quote": None, "stage": "QUALIFYING"},
          missing=["vehicle", "inspection_location"], next_action="ASK_VEHICLE",
          must_not=[{"field": "vehicle", "reason": "no vehicle named"},
                    {"field": "quote", "reason": "nothing to price"}],
          note="Intent must be recognised regardless of modal verb or clause type.")

# B — vehicle identity (20 surface forms)
VEHICLE_TEXTS = {
    "Es un Peugeot 2008 del 2014": ("Peugeot 2008", 2014),
    "un 2008 del 2014": ("Peugeot 2008", 2014),
    "una 2008 del 2014": ("Peugeot 2008", 2014),
    "para revisar un 2008 del 2014": ("Peugeot 2008", 2014),
    "quería revisar una 2008 del 2014": ("Peugeot 2008", 2014),
    "Peugeot 2008 2014": ("Peugeot 2008", 2014),
    "tengo una Taos 2020": ("Volkswagen Taos", 2020),
    "un Focus 2017": ("Ford Focus", 2017),
    "es un Ford Focus del 2017": ("Ford Focus", 2017),
    "Toyota Corolla 2019": ("Toyota Corolla", 2019),
    "un corolla 2019": ("Toyota Corolla", 2019),
    "Chevrolet Onix 2021": ("Chevrolet Onix", 2021),
    "una Amarok 2018": ("Volkswagen Amarok", 2018),
    "Fiat Cronos 2022": ("Fiat Cronos", 2022),
    "un gol trend 2016": ("Volkswagen Gol Trend", 2016),
    "renault sandero 2015": ("Renault Sandero", 2015),
    "es una hilux 2020": ("Toyota Hilux", 2020),
    "quiero revisar un fox": ("Volkswagen Fox", None),
    "un vw fox del 2013": ("Volkswagen Fox", 2013),
    "Honda Fit 2018": ("Honda Fit", 2018),
}
for i, (text, (vehicle, year)) in enumerate(VEHICLE_TEXTS.items(), start=1):
    CASES.append(case(
        f"SYN-VEH-{i:02d}", "SYNTHETIC", "authored variant (L4.7E)",
        [text], ["B"],
        [ev("vehicle", vehicle, "CONFIRMED", role="VEHICLE_OF_INTEREST"),
         ev("vehicle_year", year, "CONFIRMED" if year else "AMBIGUOUS")],
        canonical={"candidate": {"vehicle": vehicle, "anio": year}},
        missing=([] if year else ["vehicle_year"]),
        next_action="ASK_LOCATION",
        must_not=[{"field": "vehicle_year", "reason": "no year stated"}] if not year else [],
        note="Catalog resolution is deterministic; the interpreter only proposes identity.",
    ))

# C — inspection location vs customer origin (20 forms)
LOCATION_TEXTS = [
    ("El auto está en Berazategui", "Berazategui", None),
    ("Está en Berazategui", "Berazategui", None),
    ("Está en Berazategui, pero yo soy de Tigre.", "Berazategui", "Tigre"),
    ("Yo soy de Tigre pero el auto está en Berazategui", "Berazategui", "Tigre"),
    ("El vehículo se encuentra en Quilmes", "Quilmes", None),
    ("Queda en Avellaneda", "Avellaneda", None),
    ("Lo tengo en Palermo", "Palermo", None),
    ("El auto está en Palermo, yo vivo en La Plata", "Palermo", "La Plata"),
    ("Vivo en San Isidro pero el auto está en Morón", "Morón", "San Isidro"),
    ("Está por Belgrano", "Belgrano", None),
    ("La camioneta está en San Justo", "San Justo", None),
    ("El auto lo tiene el dueño en Caballito", "Caballito", None),
    ("Yo estoy en Nordelta, el auto está en Tigre", "Tigre", "Nordelta"),
    ("Está en Lomas de Zamora", "Lomas de Zamora", None),
    ("El auto está en Ramos Mejía, aunque yo soy de Quilmes", "Ramos Mejía", "Quilmes"),
    ("Se encuentra en Villa Urquiza", "Villa Urquiza", None),
    ("Lo vamos a ver en Martínez", "Martínez", None),
    ("El auto está en Berazategui y yo trabajo en CABA", "Berazategui", "CABA"),
    ("Está en Avellaneda, yo me manejo desde Palermo", "Avellaneda", "Palermo"),
    ("El usado está en Quilmes, vivo cerca igual", "Quilmes", None),
]
for i, (text, insp, origin) in enumerate(LOCATION_TEXTS, start=1):
    evidence = [ev("inspection_location", insp, "CONFIRMED", role="INSPECTION_LOCATION")]
    must_not = []
    if origin:
        evidence.append(ev("customer_origin", origin, "CONFIRMED", role="CUSTOMER_ORIGIN"))
        must_not.append({"field": "inspection_location", "value": origin,
                         "reason": "customer origin is never the inspection location"})
    CASES.append(case(
        f"SYN-LOC-{i:02d}", "SYNTHETIC", "authored variant (L4.7E)",
        [text], ["C"], evidence,
        canonical={"inspection_location": insp},
        next_action="QUOTE_IF_VEHICLE_KNOWN", must_not=must_not,
        note="Role assignment is the invariant, not the phrasing.",
    ))

# D — quote request (8)
add_group("SYN-QUOTE", "D", [
    "¿Cuánto sale la revisión?",
    "Me pasás precio?",
    "Qué valor tiene el servicio?",
    "Cuánto me cobran por revisar el auto?",
    "Necesito una cotización",
    "Cuánto estarían cobrando?",
    "Me tirás un presupuesto?",
    "Cuánto salía en esa zona?",
], lambda t: [ev("quote_request", True, "CONFIRMED")],
   canonical_fn=lambda t: {},
   next_action="QUOTE_IF_READY_ELSE_ASK_MISSING",
   must_not=[{"field": "quote", "reason": "a request is not a quote; PricingService must produce it"}])

# E — acceptance (20)
add_group("SYN-ACC", "E", [
    "Dale", "Sí, avancemos", "Me sirve", "Ok hagámoslo", "Listo, vamos",
    "Perfecto, avancemos", "Sí dale", "Buenísimo, seguimos", "De acuerdo",
    "Me parece bien", "Sí, quiero coordinar", "Vamos con eso", "Está bien, avancemos",
    "Ok, cuándo pueden?", "Sí por favor", "Genial, coordinemos", "Listo dale",
    "Sí, contratamos", "Ok me interesa avanzar", "Bien, sigamos",
], lambda t: [ev("acceptance", True, "CONFIRMED")],
   canonical_fn=lambda t: {"stage": "SCHEDULING", "lead_flag": "ACEPTADO"},
   next_action="ASK_DAY_AND_TIME",
   must_not=[{"field": "scheduling_preference", "reason": "acceptance is not a day/time"},
             {"field": "booking", "reason": "acceptance is not a booking"}],
   note="Acceptance requires a quote already delivered; reconciliation enforces that.")

# F — rejection / hesitation (10)
add_group("SYN-REJ", "F", [
    "Uh, es caro", "Lo voy a pensar", "Por ahora no", "Después te aviso",
    "Estoy viendo otras opciones", "Mmm no sé", "Capaz más adelante",
    "Todavía no lo decidí", "Lo consulto y te digo", "No por ahora, gracias",
], lambda t: [ev("acceptance", False, "CONFIRMED"),
              ev("readiness", "HESITANT_OR_DEFERRED", "PROPOSED")],
   canonical_fn=lambda t: {"stage": "QUOTED"},
   next_action="REMAIN_AVAILABLE",
   must_not=[{"field": "acceptance", "value": True, "reason": "hesitation is not acceptance"},
             {"field": "lead_estado", "value": "PERDIDO", "reason": "hesitation is not a loss"}])

# G — scheduling day/time (12)
SCHED_SINGLE = [
    ("Mañana a las 15", "TOMORROW", "15:00"),
    ("mñ 15hs", "TOMORROW", "15:00"),
    ("Puede ser el jueves a las 10?", "THURSDAY", "10:00"),
    ("El viernes por la mañana", "FRIDAY", None),
    ("Hoy 11hs", "TODAY", "11:00"),
    ("El lunes a las 10", "MONDAY", "10:00"),
    ("Sábado a las 9", "SATURDAY", "09:00"),
    ("Pasado mañana al mediodía", "DAY_AFTER_TOMORROW", None),
    ("El miércoles 16:30", "WEDNESDAY", "16:30"),
    ("Martes temprano", "TUESDAY", None),
    ("Este jueves si puede ser", "THURSDAY", None),
    ("Mañana temprano", "TOMORROW", None),
]
for i, (text, day, hhmm) in enumerate(SCHED_SINGLE, start=1):
    CASES.append(case(
        f"SYN-SCHED-{i:02d}", "SYNTHETIC", "authored variant (L4.7E)",
        [text], ["G"],
        [ev("scheduling_preference", [{"day": day, "time": hhmm, "rank": 1}], "CONFIRMED")],
        canonical={"scheduling_primary": {"day": day, "time": hhmm}},
        next_action="EVALUATE_AVAILABILITY",
        must_not=[{"field": "availability", "reason": "a request is not availability"}],
    ))

# H — ordered primary/fallback (8)
SCHED_ORDERED = [
    ("Mñ 15hs? O nose jueves que tenes", ("TOMORROW", "15:00"), ("THURSDAY", None)),
    ("Mañana a las 15 o el jueves", ("TOMORROW", "15:00"), ("THURSDAY", None)),
    ("Puede ser hoy a las 17, si no mañana", ("TODAY", "17:00"), ("TOMORROW", None)),
    ("Jueves 11 o viernes a la tarde", ("THURSDAY", "11:00"), ("FRIDAY", None)),
    ("Prefiero el lunes, sino el martes", ("MONDAY", None), ("TUESDAY", None)),
    ("Mañana temprano o el sábado", ("TOMORROW", None), ("SATURDAY", None)),
    ("El miércoles a las 9, y si no el jueves a las 9", ("WEDNESDAY", "09:00"), ("THURSDAY", "09:00")),
    ("Hoy no puedo, mañana a las 12 o el viernes", ("TOMORROW", "12:00"), ("FRIDAY", None)),
]
for i, (text, primary, fallback) in enumerate(SCHED_ORDERED, start=1):
    CASES.append(case(
        f"SYN-ORDER-{i:02d}", "SYNTHETIC", "authored variant (L4.7E)",
        [text], ["H"],
        [ev("scheduling_preference",
            [{"day": primary[0], "time": primary[1], "rank": 1},
             {"day": fallback[0], "time": fallback[1], "rank": 2}],
            "CONFIRMED", note="order is meaning: the first branch is the primary request")],
        canonical={"scheduling_primary": {"day": primary[0], "time": primary[1]},
                   "scheduling_fallback": {"day": fallback[0], "time": fallback[1]}},
        next_action="EVALUATE_PRIMARY_THEN_FALLBACK",
        must_not=[{"field": "scheduling_preference",
                   "value": {"day": fallback[0], "time": primary[1]},
                   "reason": "a time may never migrate from one branch to another"}],
    ))

# I — corrections / replacements (8)
CORRECTIONS = [
    ("Es un Ford Ka... no, perdón, es un Ford Kuga", "Ford Kuga", "Ford Ka"),
    ("Pensaba comprar un Focus pero al final es un Corolla 2020", "Toyota Corolla", "Ford Focus"),
    ("Dije Palermo pero es en Belgrano", None, None),
    ("No, el auto no está en Tigre, está en Berazategui", None, None),
    ("Cambié de auto, ahora es una Amarok", "Volkswagen Amarok", None),
    ("Mejor el jueves, olvidate del miércoles", None, None),
    ("Es del 2015 no del 2014", None, None),
    ("Al final volvemos con el Peugeot", "Peugeot", None),
]
for i, (text, new_vehicle, superseded) in enumerate(CORRECTIONS, start=1):
    evidence = [ev("correction", True, "CONFIRMED",
                   note="the last stated value supersedes the earlier one")]
    if new_vehicle:
        evidence.append(ev("vehicle", new_vehicle, "CONFIRMED", role="VEHICLE_OF_INTEREST"))
    if superseded:
        evidence.append(ev("vehicle_superseded", superseded, "CONFIRMED"))
    CASES.append(case(
        f"SYN-CORR-{i:02d}", "SYNTHETIC", "authored variant (L4.7E)",
        [text], ["I"], evidence,
        canonical=({"candidate": {"vehicle": new_vehicle}} if new_vehicle else {}),
        next_action="APPLY_CORRECTION",
        must_not=([{"field": "vehicle", "value": superseded,
                    "reason": "superseded value must not remain canonical"}] if superseded else []),
    ))

# J — FAQ + business evidence in the same burst (8)
add_group("SYN-MIX", "J", [
    "Hola, quiero revisar un Focus 2017. ¿Aceptan débito?",
    "¿Entregan informe? Es una Taos 2020 y está en Quilmes",
    "¿Tengo que estar presente? El auto está en Palermo",
    "¿Cuánto tarda la revisión? Quiero coordinar una para un Onix 2021",
    "¿Qué incluye el servicio? Estoy por comprar un usado en Avellaneda",
    "Hola! ¿Trabajan los sábados? Quiero revisar un Corolla 2019",
    "¿Se paga antes o después? Es un Gol Trend 2016 en San Justo",
    "¿Hacen a domicilio? El auto está en Belgrano",
], lambda t: [ev("faq_topics", ["mixed"], "CONFIRMED"),
              ev("service_intent", "PREPURCHASE_INSPECTION", "CONFIRMED",
                 note="business evidence must survive a FAQ-dominant burst")],
   canonical_fn=lambda t: {},
   next_action="ANSWER_FAQ_AND_KEEP_EVIDENCE",
   must_not=[{"field": "evidence_discarded", "value": True,
              "reason": "L4-WILD-B: a FAQ-dominant burst must not discard business evidence"}])

# K — noisy typo / ASR language (8; REAL-001..003 also belong to this group)
add_group("SYN-NOISE", "K", [
    "kiero rebisar un auto antes de comprar",
    "hla necesito una rebision pre compra",
    "quiero q me revicen un usado porfa",
    "buenas quiro saber si revisan autos",
    "necesito revision precompra pero no se bien como es",
    "hola.. me pasas info de la revicion?",
    "kieren revisar un auto en quilmes?",
    "revision pre compra cuanto sale?",
], lambda t: [ev("service_intent", "PREPURCHASE_INSPECTION", "PROPOSED",
                 note="noise must not prevent intent recognition, nor invent detail")],
   canonical_fn=lambda t: {},
   next_action="ASK_VEHICLE",
   must_not=[{"field": "vehicle", "reason": "no vehicle resolvable from noise"},
             {"field": "inspection_location", "reason": "no location resolvable from noise"}])

# L — future / not-yet-ready customer (8)
add_group("SYN-FUT", "L", [
    "Todavía estoy buscando auto, cuando encuentre te aviso",
    "Por ahora solo consulto, después vuelvo",
    "Estoy mirando, cualquier cosa te escribo",
    "Guardo el contacto y te hablo cuando decida",
    "Aún no elegí el auto",
    "Te consulto más adelante cuando tenga el auto",
    "Estoy en la búsqueda todavía",
    "Cuando lo vaya a ver te aviso para que lo revisen",
], lambda t: [ev("service_intent", "PREPURCHASE_INSPECTION", "PROPOSED"),
              ev("readiness", "SEARCHING_NOT_READY", "CONFIRMED")],
   canonical_fn=lambda t: {"candidate": None, "quote": None, "stage": "QUALIFYING"},
   next_action="ACKNOWLEDGE_AND_REMAIN_AVAILABLE",
   must_not=[{"field": "vehicle", "reason": "no vehicle chosen yet"},
             {"field": "quote", "reason": "not quote-ready"},
             {"field": "scheduling_preference", "reason": "no scheduling request"}])


def main() -> None:
    with OUT.open("w", encoding="utf-8") as fh:
        for entry in CASES:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    real = sum(1 for c in CASES if c["provenance"]["kind"] == "REAL")
    print(f"{OUT.name}: {len(CASES)} cases ({real} REAL, {len(CASES) - real} SYNTHETIC)")


if __name__ == "__main__":
    main()

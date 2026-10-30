"""WILD1-F3/F5 — the one Flow/backend data contract, written down once.

The 2026-10-29 Wild showed every booking refusal reaching the handset as Meta's generic
"something went wrong". Three readings of that have now been recorded here. The first two
were wrong and both are kept, because the record of how a diagnosis moved is the only thing
that stops it moving back.

WHAT WAS CLAIMED FIRST (2026-10-29, superseded)
    "The Flow runtime rejects a payload carrying undeclared keys", so the refusal failed
    because no published screen declared `slot_conflict_message`.

WHY THAT IS NOT THE CAUSE
    The deployed backend sent APPOINTMENT twelve keys. In the Wild that payload rendered —
    twice. INIT at 08:53:31 drew the screen, `date_selected` at 08:55:53 redrew it with the
    time list, and the owner reached SUMMARY at 08:56:20. Undeclared extras, if any had
    been undeclared, were tolerated.

WHAT THE EVIDENCE ACTUALLY SHOWS
    All four `_refusal_screen` call sites and the `BookingTokenError` screen live in
    `_confirm_booking`/`handle_prepare_summary` and every one of them returns
    `screen: "APPOINTMENT"`. Those handlers are reached from SUMMARY and DETAILS. The
    published Flow declares `routing_model = {"APPOINTMENT": ["DETAILS"],
    "DETAILS": ["SUMMARY"], "SUMMARY": []}` — neither screen may reach APPOINTMENT. The
    refusal response names a transition the Flow forbids. The three observations separate
    cleanly:

        APPOINTMENT → APPOINTMENT   no routing edge needed   WORKED (self-refresh)
        DETAILS     → SUMMARY       declared edge            WORKED
        SUMMARY     → APPOINTMENT   NO declared edge         FAILED, every time

    The absent edge is the only variable that distinguishes the failure.

SO THERE ARE TWO DEFECTS, NOT ONE
    F3a  the refusal names a transition the published routing model forbids — the response
         is invalid, and the customer sees Meta's generic error. This is what the Wild hit.
    F3b  APPOINTMENT binds no component to a refusal message, so even a response that WAS
         delivered would have been silent.

    The target closes both: two routing edges, four declared fields, two bound components.

WHAT WAS CLAIMED SECOND (2026-10-29 R1, superseded 2026-10-30)
    That the live asset was `PUBLISHED_28104222025943520_v7.3.json`, so the twelve keys
    included five — `selected_date`, `selected_time`, `customer_name`, `contact_phone`,
    `contact_phone_display` — that no published screen declared, belonging to an
    "unpublished prefill candidate". R1 therefore derived its target from v7.3 and REMOVED
    those five keys from the payload, recording them in a constant named
    `DEFERRED_PREFILL_KEYS`.

WHY THAT IS WRONG, AND HOW IT WAS CAUGHT
    `meta/flows/booking/README.md` called the v7.3 file "a byte-exact copy of what is
    PUBLISHED right now" two lines after recording that r1 was "PUBLISHED live on
    2026-09-16". The 2026-09-16 Gate B Final Certification settles it with stronger
    evidence: it downloaded the live definition from the Flows API, canonicalised it to
    `5c4c68d10d0471eec8d439ee71324d20515ef1128397604719360feaa971c316`, and proved deep
    structural equality with `CANDIDATE_v7.4-prefill-r3.json`. On 2026-10-30 the owner
    exported the live published JSON for asset 28104222025943520 from Meta directly and it
    is r3's structure: APPOINTMENT declares twelve fields, DETAILS renders
    `${data.contact_phone_display}` under "Te contactamos a este WhatsApp:", DETAILS asks
    the customer for no telephone number of their own, and `prepare_summary` sends
    `"phone": "${data.contact_phone}"`.

    So those five keys are not an unpublished candidate's init-values. They are a SHIPPED,
    handset-certified prefill feature, and R1 stopped supplying them. Publishing
    `TARGET_v7.6-minimal-refusal.json` would have completed the regression: five
    APPOINTMENT fields and three DETAILS fields removed, a required `Teléfono` input
    re-added, the masked-WhatsApp caption deleted and two action payloads reverted.

WHAT THIS REVISION (R2, 2026-10-30) DOES
    v7.3 becomes what it is — the preserved pre-16-September baseline. The published
    structure is r3. The target is r3 PLUS the same four fields, two components and two
    routing edges, and nothing else; the five prefill values are supplied again, from
    authoritative server state. The APPOINTMENT contract is therefore sixteen keys: r3's
    twelve and the four this correction adds.

    Neither superseded artifact is deleted or rewritten. `TARGET_v7.6-minimal-refusal.json`
    and `make_minimal_target.py` stay in the repository as audit evidence, named below so
    nobody publishes them by accident.

F5 — why this is a module and not a comment
    The old contract test asserted one direction (declared-minus-supplied) and then
    subtracted `{slot_conflict_message, error_message}`: the exact two keys the backend
    supplied and no Flow declared. The direction that mattered was never compared. The sets
    below are re-derived from the pinned artifact by the contract test, in both directions,
    with no exclusion set.

Two categories, deliberately named rather than hidden:

  RENDERED  the screen's layout binds the key with `${data.<key>}`, so a wrong or missing
            value reaches the customer's handset — visibly in a component, or as the input
            of the next screen when the binding sits in an action payload.
  CARRIED   the screen declares the key and binds it nowhere. It is still part of the
            contract and the backend must supply it. Naming these is what stops them
            decaying into another exclusion set.
"""
from __future__ import annotations

#: The artifact that must be published for a refusal to render. Relative to the repo root.
#: Derived from the published structure by `meta/flows/booking/make_r3_refusal_target.py`.
TARGET_FLOW_PATH = "meta/flows/booking/TARGET_v7.7-r3-prefill-refusal.json"
TARGET_FLOW_SHA256 = "98981193109ce426af1298dbfca5dd2f24cae2aee0a7af08b20dde56001bc36f"

#: The structure of the Flow asset currently live, and the rollback artifact.
#:
#: The file name says CANDIDATE and the content is what is published. That contradiction is
#: historical and is left in place rather than renamed, because the file is cited by name in
#: the 2026-09-16 Gate B certification and in the owner's own evidence. What resolves it is
#: the canonical fingerprint below: the certification downloaded the live definition from
#: the Flows API and canonicalised it to exactly this value, and the owner's 2026-10-30
#: export from Meta confirms the same structure field by field.
PUBLISHED_FLOW_PATH = "meta/flows/booking/CANDIDATE_v7.4-prefill-r3.json"
PUBLISHED_FLOW_SHA256 = "53a3d88f3dd57c9bf3089c97c9edfe83bc907afc428e7ef19e3c1538efb1bc04"
PUBLISHED_FLOW_CANONICAL_SHA256 = \
    "5c4c68d10d0471eec8d439ee71324d20515ef1128397604719360feaa971c316"
PUBLISHED_FLOW_ID = "28104222025943520"

#: What was published BEFORE 2026-09-16, kept for history. It is not the rollback artifact
#: and it is not live. Publishing it would withdraw the prefill feature.
PRESERVED_BASELINE_PATH = "meta/flows/booking/PUBLISHED_28104222025943520_v7.3.json"
PRESERVED_BASELINE_SHA256 = \
    "274038ba234a49fa4f99bae47c00f6cac640e248d56a373d40ae8fba6c1afb15"

#: Derived from the wrong baseline (see the module docstring). Preserved for audit; it must
#: never be imported into Meta. Named here so the prohibition travels with the code.
SUPERSEDED_TARGET_PATH = "meta/flows/booking/TARGET_v7.6-minimal-refusal.json"
SUPERSEDED_TARGET_SHA256 = \
    "a25de8f43f5ada03650541cd84fa42cb4a2a5b2d772d89ac520afcd3219c43b4"

# ── The data contract, per screen ──────────────────────────────────────────────────
#
# APPOINTMENT is the published structure's twelve keys plus exactly four: two refusal/error
# strings and the two booleans that make them visible.

APPOINTMENT_RENDERED = frozenset({
    # the picker and its context
    "booking_token", "vehicle_summary", "location_summary",
    "date", "is_date_enabled", "time", "is_time_enabled",
    # the prefill identity, bound by the APPOINTMENT -> DETAILS navigate payload. None of
    # the three is drawn on APPOINTMENT itself; they are DETAILS' input, and DETAILS is
    # where the masked number is read. `contact_phone` is never shown anywhere.
    "customer_name", "contact_phone", "contact_phone_display",
    # the refusal contract this correction adds
    "slot_conflict_message", "has_refusal", "error_message", "has_error",
})
#: Declared by APPOINTMENT and bound by nothing in it. They were the `init-value` plan for
#: the Dropdowns; the published screen declares its Dropdowns without `init-value`, so the
#: values are inert *as preselection*. They stay in the contract because the published Flow
#: declares them: a declared key the backend omits is a key the Flow renders as missing.
APPOINTMENT_CARRIED = frozenset({"selected_date", "selected_time"})
APPOINTMENT_KEYS = APPOINTMENT_RENDERED | APPOINTMENT_CARRIED

DETAILS_RENDERED = frozenset({
    "booking_token", "vehicle_summary", "location_summary", "date", "time",
    # "Te contactamos a este WhatsApp:" + the mask, and the full number in the
    # prepare_summary payload — which the backend overrides server-side regardless.
    "contact_phone", "contact_phone_display",
})
DETAILS_CARRIED = frozenset({"customer_name"})
DETAILS_KEYS = DETAILS_RENDERED | DETAILS_CARRIED

SUMMARY_RENDERED = frozenset({
    "booking_token", "appointment_summary", "customer_summary", "date", "time",
    "name", "phone", "email", "inspection_address", "seller_name", "seller_phone",
    "listing_url",
})
SUMMARY_CARRIED = frozenset()
SUMMARY_KEYS = SUMMARY_RENDERED | SUMMARY_CARRIED

SCREEN_KEYS = {
    "APPOINTMENT": APPOINTMENT_KEYS,
    "DETAILS": DETAILS_KEYS,
    "SUMMARY": SUMMARY_KEYS,
}
SCREEN_RENDERED = {
    "APPOINTMENT": APPOINTMENT_RENDERED,
    "DETAILS": DETAILS_RENDERED,
    "SUMMARY": SUMMARY_RENDERED,
}
SCREEN_CARRIED = {
    "APPOINTMENT": APPOINTMENT_CARRIED,
    "DETAILS": DETAILS_CARRIED,
    "SUMMARY": SUMMARY_CARRIED,
}

# ── The routing contract (F3a) ─────────────────────────────────────────────────────
#
# The half of the contract the field sets cannot express, and the half the Wild actually
# failed on. A screen name in a Data API response is a navigation instruction: it is valid
# only when the Flow's `routing_model` declares an edge from the screen the customer is on
# to the screen the response names — or when the two are the same screen, which is a
# self-refresh and needs no edge (proven live by `date_selected`).
#
# Read as: handler reached while the customer is on <key> may return any screen in <value>.

REFUSAL_RETURN_SCREEN = "APPOINTMENT"

#: Which screen each Data API trigger is reached FROM, per the published layouts.
TRIGGER_ORIGIN = {
    "INIT": None,                 # no origin: the Flow is opening
    "date_selected": "APPOINTMENT",
    "prepare_summary": "DETAILS",
    "confirm_booking": "SUMMARY",
}

#: The edges the backend's own behaviour requires of whatever Flow is published. Any
#: trigger that can refuse must be able to return `REFUSAL_RETURN_SCREEN`.
REQUIRED_ROUTING_EDGES = frozenset({
    ("DETAILS", "APPOINTMENT"),       # prepare_summary → BookingTokenError screen
    ("SUMMARY", "APPOINTMENT"),       # confirm_booking → every refusal, and token errors
})

#: The five values the published Flow prefills, restored to the payload by R2 after R1
#: removed them on the strength of a baseline the owner's export has since refuted. They
#: are listed so the restoration is a declaration and not a silent re-addition, and so a
#: future reader finds the reason rather than repeating R1's reasoning.
#:
#: Every one comes from authoritative server state, never from Flow input:
#:   contact_phone          `ctx.contact.wa_id`, digits only — the identity WhatsApp proved
#:   contact_phone_display  derived from it by `_mask_phone`; last four digits only
#:   customer_name          `_lead_display_name(ctx.lead)`; empty when unknown, never
#:                          invented, and a WhatsApp profile label is not elevated into one
#:   selected_date          offered only when the id exists in that payload's own `date`
#:   selected_time          offered only when the id exists in that payload's own `time`
RESTORED_PREFILL_KEYS = frozenset({
    "selected_date", "selected_time",
    "customer_name", "contact_phone", "contact_phone_display",
})

#: The four keys this correction adds, and the whole of the published-to-target delta.
REFUSAL_KEYS = frozenset({
    "slot_conflict_message", "has_refusal", "error_message", "has_error",
})

#: Every value the contract treats as absent. `has_refusal`/`has_error` are booleans and
#: the two message fields are strings, so "no refusal" is an empty string plus False —
#: never a missing key, which is what made the old payloads vary by path. The five prefill
#: keys default to "" for the same reason: the expired-session screen has no context to
#: read an identity from, and an empty declared value is renderable where a missing one is
#: not.
APPOINTMENT_DEFAULTS = {
    "slot_conflict_message": "",
    "has_refusal": False,
    "error_message": "",
    "has_error": False,
    "vehicle_summary": "",
    "location_summary": "",
    "booking_token": "",
    "date": [],
    "is_date_enabled": False,
    "time": [],
    "is_time_enabled": False,
    "selected_date": "",
    "selected_time": "",
    "customer_name": "",
    "contact_phone": "",
    "contact_phone_display": "",
}

#: Envelope keys that belong to the Data API protocol, not to a screen's `data`.
PROTOCOL_KEYS = frozenset({"version", "screen", "data"})


def appointment_payload(**values) -> dict:
    """An APPOINTMENT `data` dict carrying exactly the contract's keys.

    Supplying the full key set on every path — success, slot conflict, OFF switch, expired
    session — is the point: a payload that varies by branch is how a missing key reaches
    the handset on the one path nobody exercised. Unknown keys are refused here rather than
    sent: the Wild shows this asset tolerated extra keys, but tolerance is not a guarantee,
    it is not true of every Flow version, and a key the Flow ignores is a key the backend
    believes it delivered.
    """
    unknown = set(values) - APPOINTMENT_KEYS
    if unknown:
        raise ValueError("not declared by %s on APPOINTMENT: %s"
                         % (TARGET_FLOW_PATH, ", ".join(sorted(unknown))))
    out = dict(APPOINTMENT_DEFAULTS)
    out.update(values)
    return {k: out[k] for k in sorted(APPOINTMENT_KEYS)}


def validate_screen_payload(screen: str, payload: dict) -> None:
    """Both directions, for any screen. Raises `ValueError` naming what is wrong.

    This lives beside the contract rather than in a test so that the comparison cannot be
    deleted from one place and quietly survive in another — WILD1-F5 was exactly a reverse
    comparison that existed nowhere once its one caller stopped making it.
    """
    if screen not in SCREEN_KEYS:
        raise ValueError("unknown screen %r" % screen)
    declared = set(SCREEN_KEYS[screen])
    supplied = set(payload) - PROTOCOL_KEYS
    undeclared = supplied - declared
    missing = declared - supplied
    problems = []
    if undeclared:
        problems.append("supplied but not declared: %s" % ", ".join(sorted(undeclared)))
    if missing:
        problems.append("declared but not supplied: %s" % ", ".join(sorted(missing)))
    if screen == "APPOINTMENT":
        # the pairing that decides whether a refusal is READ or merely transmitted
        if payload.get("slot_conflict_message") and payload.get("has_refusal") is not True:
            problems.append("slot_conflict_message set without has_refusal=True")
        if payload.get("error_message") and payload.get("has_error") is not True:
            problems.append("error_message set without has_error=True")
    if problems:
        raise ValueError("%s payload invalid — %s" % (screen, "; ".join(problems)))


def validate_transition(origin: str | None, response_screen: str,
                        routing_model: dict) -> None:
    """F3a. Raises `ValueError` when a response names a screen it cannot navigate to.

    `origin is None` is the Flow opening (INIT), which has no previous screen. Origin equal
    to the response is a self-refresh and needs no declared edge.
    """
    if origin is None or origin == response_screen:
        return
    allowed = routing_model.get(origin)
    if allowed is None:
        raise ValueError("routing_model declares no screen %r" % origin)
    if response_screen not in allowed:
        raise ValueError(
            "%s -> %s is not a declared transition (routing_model[%r] = %r)"
            % (origin, response_screen, origin, allowed))

"""WILD1-F3/F5 (R1) — the one Flow/backend data contract, written down once.

The 2026-10-29 Wild showed every booking refusal reaching the handset as Meta's generic
"something went wrong". The first diagnosis of this was wrong, and the Wild's own evidence
refutes it, so it is recorded here rather than quietly replaced.

WHAT WAS CLAIMED (2026-10-29, superseded)
    "The Flow runtime rejects a payload carrying undeclared keys", so the refusal failed
    because no published screen declared `slot_conflict_message`.

WHY THAT IS NOT THE CAUSE
    The deployed backend sends APPOINTMENT twelve keys, five of which published v7.3
    declares nowhere: `selected_date`, `selected_time`, `customer_name`, `contact_phone`,
    `contact_phone_display` (they belong to an unpublished prefill candidate). In the Wild
    that payload rendered — twice. INIT at 08:53:31 drew the screen, `date_selected` at
    08:55:53 redrew it with the time list, and the owner reached SUMMARY at 08:56:20. So
    undeclared extra keys were tolerated by this asset, and the missing declaration cannot
    be what broke the refusal.

WHAT THE EVIDENCE ACTUALLY SHOWS
    All four `_refusal_screen` call sites and the `BookingTokenError` screen live in
    `_confirm_booking`/`handle_prepare_summary` and every one of them returns
    `screen: "APPOINTMENT"`. Those handlers are reached from SUMMARY and DETAILS. Published
    v7.3 declares `routing_model = {"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"],
    "SUMMARY": []}` — neither screen may reach APPOINTMENT. The refusal response names a
    transition the published Flow forbids. The three observations separate cleanly:

        APPOINTMENT → APPOINTMENT   no routing edge, 5 undeclared keys   WORKED (self-refresh)
        DETAILS     → SUMMARY       declared edge                        WORKED
        SUMMARY     → APPOINTMENT   NO declared edge                     FAILED, every time

    The absent edge is the only variable that distinguishes the failure.

SO THERE ARE TWO DEFECTS, NOT ONE
    F3a  the refusal names a transition the published routing model forbids — the response
         is invalid, and the customer sees Meta's generic error. This is what the Wild hit.
    F3b  APPOINTMENT binds no component to a refusal message, so even a response that WAS
         delivered would have been silent. This is what the first fix addressed.

    The target closes both: two routing edges, four declared fields, two bound components.

AND A THIRD, WHICH IS WHY THIS FILE WAS REWRITTEN
    The first target was derived from `CANDIDATE_v7.4-prefill-r3.json`, so it also carried
    an unpublished prefill rollout into a launch correction: nine new APPOINTMENT fields
    instead of four, three on DETAILS, a rewritten navigate payload — and it DELETED the
    `phone` TextInput from DETAILS, which would have stopped asking live customers for a
    phone number. None of that is required to render a refusal. It is retained, renamed,
    None of that is required to render a refusal, so it is not in this repository at all:
    the owner ruled a known-wrong, non-deployable artifact must not sit beside the real
    Flow candidates. It is preserved for audit under the milestone evidence root
    (`crm-wild1-bfgate-20261029T121535Z/superseded/`, sha256 `6b9da137ddd1cf21…`).

F5 — why this is a module and not a comment
    The old contract test asserted one direction (declared-minus-supplied) and then
    subtracted `{slot_conflict_message, error_message}`: the exact two keys the backend
    supplied and no Flow declared. The direction that mattered was never compared. The sets
    below are re-derived from the pinned artifact by the contract test, in both directions,
    with no exclusion set.

Two categories, deliberately named rather than hidden:

  RENDERED  the screen binds the key with `${data.<key>}`, so a wrong or missing value is
            visible to the customer.
  CARRIED   the screen declares the key and binds it nowhere. It is still part of the
            contract and the backend must supply it. Naming these is what stops them
            decaying into another exclusion set. The minimal target has none.
"""
from __future__ import annotations

#: The artifact that must be published for a refusal to render. Relative to the repo root.
#: Derived from the published baseline by `meta/flows/booking/make_minimal_target.py`.
TARGET_FLOW_PATH = "meta/flows/booking/TARGET_v7.6-minimal-refusal.json"
TARGET_FLOW_SHA256 = "a25de8f43f5ada03650541cd84fa42cb4a2a5b2d772d89ac520afcd3219c43b4"

#: The Flow asset currently live, and the rollback artifact.
PUBLISHED_FLOW_PATH = "meta/flows/booking/PUBLISHED_28104222025943520_v7.3.json"
PUBLISHED_FLOW_SHA256 = "274038ba234a49fa4f99bae47c00f6cac640e248d56a373d40ae8fba6c1afb15"
PUBLISHED_FLOW_ID = "28104222025943520"

# ── The data contract, per screen ──────────────────────────────────────────────────
#
# APPOINTMENT is published v7.3's seven keys plus exactly four: two refusal/error strings
# and the two booleans that make them visible. No prefill, no identity, no selection echo —
# those five keys are inert against the published Flow (its Dropdowns declare no
# `init-value`, its navigate payload does not carry them and DETAILS does not declare
# them), so they are removed from the payload rather than published to accommodate it.

APPOINTMENT_RENDERED = frozenset({
    "booking_token", "vehicle_summary", "location_summary",
    "date", "is_date_enabled", "time", "is_time_enabled",
    "slot_conflict_message", "has_refusal", "error_message", "has_error",
})
APPOINTMENT_CARRIED = frozenset()
APPOINTMENT_KEYS = APPOINTMENT_RENDERED | APPOINTMENT_CARRIED

DETAILS_RENDERED = frozenset({
    "booking_token", "vehicle_summary", "location_summary", "date", "time",
})
DETAILS_CARRIED = frozenset()
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

#: The five keys removed from the APPOINTMENT payload by WILD1-F3-R1, named so their
#: removal is a declaration rather than a silent omission. They are init-values and
#: identity echoes for `CANDIDATE_v7.4-prefill-r3.json`, which has never been published:
#: v7.3's Dropdowns declare no `init-value`, its navigate payload carries none of them and
#: its DETAILS screen declares none of them, so sending them changed nothing a customer
#: could see. The prefill rollout is a separate milestone; until it happens these keys must
#: appear in no screen contract and in no payload.
DEFERRED_PREFILL_KEYS = frozenset({
    "selected_date", "selected_time",
    "customer_name", "contact_phone", "contact_phone_display",
})

#: The four keys this correction adds, and the whole of the published-to-target delta.
REFUSAL_KEYS = frozenset({
    "slot_conflict_message", "has_refusal", "error_message", "has_error",
})

#: Every value the contract treats as absent. `has_refusal`/`has_error` are booleans and
#: the two message fields are strings, so "no refusal" is an empty string plus False —
#: never a missing key, which is what made the old payloads vary by path.
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
}

#: Envelope keys that belong to the Data API protocol, not to a screen's `data`.
PROTOCOL_KEYS = frozenset({"version", "screen", "data"})


def appointment_payload(**values) -> dict:
    """An APPOINTMENT `data` dict carrying exactly the contract's keys.

    Supplying the full key set on every path — success, slot conflict, OFF switch, expired
    session — is the point: a payload that varies by branch is how a missing key reaches
    the handset on the one path nobody exercised. Unknown keys are refused here rather than
    sent: the Wild shows this asset tolerated five undeclared keys, but tolerance is not a
    guarantee, it is not true of every Flow version, and a key the Flow ignores is a key
    the backend believes it delivered.
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

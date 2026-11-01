"""WILD1-F3/F5 — the one Flow/backend data contract, written down once.

CORRECTION, 2026-10-30 (WILD1-R3). READ THIS BEFORE THE REST OF THIS DOCSTRING.
    Everything below about "two routing edges" is SUPERSEDED. Meta's own validator
    rejected the artifact that declared them, in an owner-present run, with two errors:

        "Backward route [DETAILS->APPOINTMENT] corresponding to forward route
         [APPOINTMENT->DETAILS] is not allowed in the routing model. Only forward routes
         can be specified."
        "No entry screen found in the routing model. Expected a screen with no inbound
         edges as the entry screen."

    A backward edge is not a thing this platform has. The published artifact is therefore
    forward-only — `routing_model` identical to r3's — and the return to APPOINTMENT is a
    server-selected recovery response, specified under "The routing contract" below. The
    rejected hashes are recorded as `REJECTED_CYCLIC_*` and must never be republished.

    Two further corrections the same discovery forces:
      * the local validators were insufficient. They were a deny-list of the three errors
        Meta gave on 2026-09-16 and they passed the cyclic artifact without comment. Meta
        was authoritative and Meta was the first to catch this.
      * the F3a reading below — "the absent edge is the only variable that distinguishes
        the failure" — can no longer be the explanation for the Wild's ten failures,
        because the edge it proposed cannot exist. F3a is REOPENED and the refusal screen
        is UNPROVEN on the handset. F3b (the screen bound no message) is closed by the
        four fields and two components, which Meta accepted with zero errors.

The 2026-10-29 Wild showed every booking refusal reaching the handset as Meta's generic
"something went wrong". Three readings of that have now been recorded here. The first two
were wrong and both are kept, because the record of how a diagnosis moved is the only thing
that stops it moving back. A fourth correction is above, and it supersedes the third.

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

    SUPERSEDED 2026-10-30: the target was said to close both with "two routing edges, four
    declared fields, two bound components". The four fields and two components are
    published and accepted. The two edges were rejected by Meta and do not exist, so F3a
    is not closed by this artifact — see the CORRECTION at the top of this docstring.

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
    structure is r3. The target is r3 PLUS the same four fields and two components —
    R3 (2026-10-30) removed the two routing edges this sentence used to claim — and
    nothing else; the five prefill values are supplied again, from
    authoritative server state. The APPOINTMENT contract is therefore sixteen keys: r3's
    twelve and the four this correction adds.

    Neither superseded artifact is deleted or rewritten. `TARGET_v7.6-minimal-refusal.json`
    and `make_minimal_target.py` stay in the repository as audit evidence, named below so
    (WILD1-R3-C1, 2026-11-01: that generator's executable behaviour has since been removed —
    it exits 2 and writes nothing, because it emitted a cyclic routing model as well as
    using the wrong baseline)
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
TARGET_FLOW_SHA256 = "4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d"
TARGET_FLOW_CANONICAL_SHA256 = \
    "dccd3bc973b1b51526c5064027b5d8f61ee885293dfa9815a272609092aca17d"

#: What Meta rejected on 2026-10-30, kept so the prohibition travels with the code. This
#: is the same artifact as above with two backward edges in `routing_model`, and it is not
#: a publication target under any circumstances. See `REJECTED_CYCLIC_*` below for why.
REJECTED_CYCLIC_SHA256 = \
    "98981193109ce426af1298dbfca5dd2f24cae2aee0a7af08b20dde56001bc36f"
REJECTED_CYCLIC_CANONICAL_SHA256 = \
    "53dfe8fef7ca66c478421462a15e8992bb8839712e1fb7c8790f90c410e7f965"
REJECTED_CYCLIC_META_ERRORS = (
    "Backward route [DETAILS->APPOINTMENT] corresponding to forward route "
    "[APPOINTMENT->DETAILS] is not allowed in the routing model. Only forward routes can "
    "be specified.",
    "No entry screen found in the routing model. Expected a screen with no inbound edges "
    "as the entry screen.",
)

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

# ── The routing contract (F3a, corrected 2026-10-30 by Meta's own validator) ────────
#
# THE MODEL THIS MODULE USED TO HOLD WAS WRONG, and it was wrong in the direction that
# matters. It treated Meta's `routing_model` as an exhaustive list of every screen a Data
# API *response* could name, concluded that a refusal returning APPOINTMENT therefore
# needed `DETAILS -> APPOINTMENT` and `SUMMARY -> APPOINTMENT` declared, and published
# those two edges as `REQUIRED_ROUTING_EDGES`.
#
# Meta rejected that artifact outright. The two errors are quoted verbatim in
# `REJECTED_CYCLIC_META_ERRORS` above: a backward route mirroring a forward route is not
# allowed at all, and adding one destroys the single-entry property Meta also requires.
# There is no version of the artifact in which those edges exist.
#
# So the two ideas have to be separated, and they are separated here:
#
#   A. FORWARD_ROUTES    Meta's `routing_model`. A forward-only DAG with exactly one
#                        entry screen. This is ordinary navigation — what a `navigate`
#                        action and a forward `data_exchange` response may do — and it is
#                        exactly what Meta validated.
#
#   B. RECOVERY_RETURNS  A server-selected return to APPOINTMENT carried by a recognised
#                        typed refusal. It is NOT an edge, it is not declared to Meta, and
#                        it is not reachable by navigation. The server decides it from
#                        facts only the server holds; the client cannot ask for it.
#
# What is still NOT established, and must not be read into this correction: whether Meta's
# Flow *runtime* delivers a response naming a screen with no forward route to it. The
# 2026-10-29 Wild returned `SUMMARY -> APPOINTMENT` ten times and the handset showed Meta's
# generic error every time. This module's old reading of that evidence — "the edge is
# missing, declare it" — is now impossible. The remaining readings are that the runtime
# refuses a backward response outright, or that something else broke those ten responses.
# Only a handset can tell them apart, and until one does, the refusal screen is UNPROVEN on
# the handset. Nothing below asserts otherwise.

REFUSAL_RETURN_SCREEN = "APPOINTMENT"

#: Which screen each Data API trigger is reached FROM, per the published layouts. The TABLE
#: is derived from the artifact and is server-owned; the KEY used to look up in it is the
#: `trigger`, which arrives in the request.
#:
#: CORRECTED 2026-11-01 (WILD1-R3-C1, review finding R-F03). This comment used to claim the
#: origin was "the server's own knowledge of where the customer must be standing". It is
#: not: Meta's Data API never sends the current screen and this backend keeps no server-side
#: record of it, so `origin` is a server-side FUNCTION OF A CLIENT-SUPPLIED VALUE. What that
#: does and does not permit, measured:
#:
#:   * it does NOT let a client choose a screen — every `RECOVERY_RETURNS` entry resolves to
#:     APPOINTMENT, and an ordinary response still has to satisfy the forward graph;
#:   * it does NOT weaken the booking guarantees, which rest on the token and the offer row,
#:     never on the trigger;
#:   * it DOES mean a client that reports a trigger it is not actually on can make the
#:     backend emit a response Meta may reject — e.g. `trigger="prepare_summary"` while
#:     standing on APPOINTMENT yields origin DETAILS, and DETAILS -> SUMMARY passes the
#:     forward check while Meta sees SUMMARY offered from APPOINTMENT. Self-inflicted, no
#:     state damage, and it requires deviating from the published Flow, whose payloads
#:     hard-code the trigger — but it is also hypothesis C for the ten 2026-10-29 handset
#:     failures, so it must never be written down as impossible.
TRIGGER_ORIGIN = {
    "INIT": None,                 # no origin: the Flow is opening
    "date_selected": "APPOINTMENT",
    "prepare_summary": "DETAILS",
    "confirm_booking": "SUMMARY",
}

#: A. Meta's forward routing graph — the whole of it, as published and as validated.
FORWARD_ROUTES = {
    "APPOINTMENT": ("DETAILS",),
    "DETAILS": ("SUMMARY",),
    "SUMMARY": (),
}
#: The single entry screen Meta requires: the one screen with no inbound edge.
ENTRY_SCREEN = "APPOINTMENT"

#: The recognised typed refusal outcomes. A recovery return is authorised by one of these
#: and by nothing else — not by a screen name in a payload, not by an unknown trigger, and
#: not by a bare `Exception`.
RECOVERY_SLOT_CONFLICT = "SLOT_CONFLICT"
RECOVERY_TOKEN_INVALID = "TOKEN_INVALID"
RECOVERY_OUTCOMES = frozenset({RECOVERY_SLOT_CONFLICT, RECOVERY_TOKEN_INVALID})

#: B. The whole recovery allowlist: (origin, trigger, outcome) -> the screen the server may
#: return. The screen each key maps to is always server-selected; the `trigger` component of
#: the key arrives in the request (see `TRIGGER_ORIGIN`, R-F03). Enumerated from the actual
#: producers in `booking_flow_service.py` and `flow_data_exchange.py`, by AST, not by name:
#:
#:   SLOT_CONFLICT  `BookingSlotConflictError`, raised at exactly four sites, all of them
#:                  inside `_confirm_booking` — the OFF switch, NO_CANONICAL_LOCATION,
#:                  DX_TRANSACTION_NOT_CLEAN and an authority refusal. `_confirm_booking`
#:                  is reached only by `confirm_booking`, only from SUMMARY. There is NO
#:                  slot-conflict producer on `prepare_summary`, so DETAILS gets no
#:                  slot-conflict entry: an allowlist entry for a path nothing produces is
#:                  a permission granted for nothing, and it would be the first step back
#:                  towards a broad allowlist.
#:
#:   TOKEN_INVALID  `BookingTokenError`, raised in `parse_booking_token` (3 sites),
#:                  `resolve_context` (5) and `_confirm_booking` (5). `resolve_context` is
#:                  the first statement of every handler, so this outcome is reachable from
#:                  every trigger, INIT included. It is FAIL-CLOSED: see MF-07 below.
RECOVERY_RETURNS = {
    ("SUMMARY", "confirm_booking", RECOVERY_SLOT_CONFLICT): "APPOINTMENT",
    (None, "INIT", RECOVERY_TOKEN_INVALID): "APPOINTMENT",
    ("APPOINTMENT", "date_selected", RECOVERY_TOKEN_INVALID): "APPOINTMENT",
    ("DETAILS", "prepare_summary", RECOVERY_TOKEN_INVALID): "APPOINTMENT",
    ("SUMMARY", "confirm_booking", RECOVERY_TOKEN_INVALID): "APPOINTMENT",
}

#: MF-07 — the expired/tampered-token return, classified honestly.
#:
#: It returns APPOINTMENT and it is NOT a retry path. `resolve_context` raises before any
#: handler reads the payload, so the screen comes back with every prefill empty: no dates,
#: no times, no masked number, `has_error=True` and a message telling the customer to start
#: the process again. Tapping Continue from it navigates to DETAILS with an empty token,
#: `prepare_summary` falls back to the envelope `flow_token`, and either the same token
#: fails validation again — the same fail-closed screen — or the empty date is rejected by
#: the typed HTTP 422 path. Neither route writes a row, creates or changes a booking, uses
#: a wrong customer identity, sends an outbound message, or loops without another explicit
#: customer tap.
#:
#: The customer-facing limitation is that continuing from this screen is not recovery and
#: can expose a blank masked-phone caption or Meta's generic error. The copy must keep
#: telling the customer to restart, and must never claim retry is supported there.
#:
#: A slot conflict is the opposite case and is the only RECOVERABLE one: the context is
#: valid, the screen comes back populated with what is free now, and picking again is a
#: real path. The bounded handset Wild must prove recovery with a real slot conflict — never
#: with deliberate token expiry.
FAIL_CLOSED_OUTCOMES = frozenset({RECOVERY_TOKEN_INVALID})
RECOVERABLE_OUTCOMES = frozenset({RECOVERY_SLOT_CONFLICT})

#: The Data API protocol's terminate instruction. It is NOT a screen: the published
#: artifact declares three screens — APPOINTMENT, DETAILS, SUMMARY — and the string
#: "SUCCESS" appears nowhere in it. A response naming it closes the Flow and hands the
#: `extension_message_response` back to WhatsApp, so it is outside the routing graph by
#: construction rather than by exemption. Required to carry the extension payload, so the
#: name alone cannot be used to escape the graph.
TERMINAL_PROTOCOL_SCREEN = "SUCCESS"

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

#: Distinguishes "origin not supplied" from a legitimately `None` origin (INIT).
_UNSET = object()


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


def assert_forward_only(routing_model: dict, *, screens=None) -> None:
    """THE canonical Meta forward-routing validator. One implementation, several callers.

    WILD1-R3-C1 (2026-11-01). This is the only place the routing-graph rules live. The v7.7
    generator, `scripts/validate_flow_json.py` and the contract suites all call it; none of
    them keeps a second copy. Before this correction the rules existed twice (here and in
    `make_r3_refusal_target.py`) and — worse — were reachable from **no** production or
    script entry point, which is why the general-purpose validator passed the cyclic
    artifact that Meta then rejected.

    `routing_model` is Meta's forward graph. `screens` is the list/set of screen ids the
    artifact actually declares; pass it whenever it is known, so a route naming a screen
    that does not exist is caught. When it is omitted, the graph's own keys stand in for it
    and the unknown-screen rules degrade to internal consistency only.

    Raises `ValueError` — never an `AssertionError`, so `python -O` cannot disable it — for:

      1. a backward route mirroring a forward one  (Meta's first 2026-10-30 error)
      2. no entry screen, i.e. every screen has an inbound edge  (Meta's second error)
      3. more than one entry screen
      4. any directed cycle
      5. a route TO a screen the artifact does not declare
      6. a route FROM a screen the artifact does not declare
      7. a declared screen absent from the routing model
      8. duplicate entries in one screen's route list
      9. a route list that is not a list/tuple of strings

    It accepts exactly one shape for this asset — `APPOINTMENT -> DETAILS -> SUMMARY -> ()`
    — but it is written as general rules rather than an equality check, because an equality
    check proves nothing about *why* a graph is wrong. `assert_required_forward_graph` is
    the separate, asset-specific pin for rule 9 of the milestone.

    **This is local validation. It is not Meta validation and it is not handset
    validation.** Meta accepted the forward-only artifact on 2026-10-30; whether the
    runtime renders a server-selected recovery return is still unproven (F3a).
    """
    if not isinstance(routing_model, dict) or not routing_model:
        raise ValueError("routing_model is missing, empty or not an object")

    declared = set(screens) if screens is not None else set(routing_model)

    # --- shape, and the unknown-screen rules -------------------------------------------
    for origin, targets in routing_model.items():
        if not isinstance(origin, str):
            raise ValueError("routing_model key %r is not a screen id" % (origin,))
        if not isinstance(targets, (list, tuple)):
            raise ValueError(
                "routing_model[%r] is %s, not a list of screens"
                % (origin, type(targets).__name__))
        if origin not in declared:
            raise ValueError(
                "route FROM unknown screen %r (declared screens: %s)"
                % (origin, sorted(declared)))
        seen = set()
        for target in targets:
            if not isinstance(target, str):
                raise ValueError("routing_model[%r] contains %r, not a screen id"
                                 % (origin, target))
            if target in seen:
                raise ValueError("duplicate route %s -> %s in routing_model[%r]"
                                 % (origin, target, origin))
            seen.add(target)
            if target not in declared:
                raise ValueError(
                    "route TO unknown screen %r from %r (declared screens: %s)"
                    % (target, origin, sorted(declared)))
    for screen in sorted(declared - set(routing_model)):
        raise ValueError(
            "screen %r is declared but absent from the routing model" % screen)

    # --- 1. Meta's first error: a mirrored backward route ------------------------------
    for origin, targets in routing_model.items():
        for target in targets:
            if origin in routing_model.get(target, ()):  # pragma: no branch
                raise ValueError(
                    "Backward route [%s->%s] corresponding to forward route [%s->%s] is "
                    "not allowed in the routing model. Only forward routes can be "
                    "specified." % (target, origin, origin, target))

    # --- 2 and 3. Meta's second error: exactly one entry screen ------------------------
    inbound = {s: 0 for s in routing_model}
    for origin, targets in routing_model.items():
        for target in targets:
            inbound[target] = inbound.get(target, 0) + 1
    entries = sorted(s for s in routing_model if inbound.get(s, 0) == 0)
    if not entries:
        # Meta's second 2026-10-30 error, verbatim. Every screen has an inbound edge, which
        # is what a cycle through the entry screen looks like from the validator's side.
        raise ValueError(
            "No entry screen found in the routing model. Expected a screen with no "
            "inbound edges as the entry screen. (screens with no inbound edges: [])")
    if len(entries) > 1:
        # Not Meta's wording — Meta never showed us this one — so it is reported in ours,
        # and never dressed up as a quotation.
        raise ValueError(
            "more than one entry screen in the routing model: %r; this contract requires "
            "exactly one (%r)" % (entries, ENTRY_SCREEN))
    if entries != [ENTRY_SCREEN]:
        raise ValueError(
            "the entry screen is %r, not %r" % (entries[0], ENTRY_SCREEN))

    # --- 4. any directed cycle ---------------------------------------------------------
    colour: dict = {s: 0 for s in routing_model}          # 0 white, 1 grey, 2 black

    def visit(screen: str) -> None:
        colour[screen] = 1
        for target in routing_model.get(screen, ()):
            if colour.get(target, 0) == 1:
                raise ValueError("routing_model is cyclic at %s->%s" % (screen, target))
            if colour.get(target, 0) == 0:
                visit(target)
        colour[screen] = 2

    for screen in routing_model:
        if colour[screen] == 0:
            visit(screen)


def assert_required_forward_graph(routing_model: dict, *, screens=None) -> None:
    """Rule 9 — this asset's graph is pinned, not merely well-formed.

    `assert_forward_only` says the graph is something Meta will accept. This says it is the
    graph that asset 28104222025943520 actually publishes. Both are needed: a well-formed
    DAG that is not this DAG is still the wrong artifact.
    """
    assert_forward_only(routing_model, screens=screens)
    required = {s: list(t) for s, t in FORWARD_ROUTES.items()}
    actual = {s: list(t) for s, t in routing_model.items()}
    if actual != required:
        raise ValueError(
            "routing_model is not the published forward graph for Flow %s: expected %r, "
            "got %r" % (PUBLISHED_FLOW_ID, required, actual))


def validate_forward_route(origin: str | None, response_screen: str,
                           routing_model: dict) -> None:
    """A. Ordinary navigation, against Meta's forward graph.

    `origin is None` is the Flow opening (INIT), which has no previous screen. Origin equal
    to the response is a self-refresh and needs no declared edge — proven live by
    `date_selected`. Anything else must be a declared forward route.

    This function knows nothing about refusals. A refusal that returns to APPOINTMENT does
    NOT come through here and must not be made to pass by widening the graph: that is the
    mistake Meta rejected.
    """
    if origin is None or origin == response_screen:
        return
    allowed = routing_model.get(origin)
    if allowed is None:
        raise ValueError("routing_model declares no screen %r" % origin)
    if response_screen not in allowed:
        raise ValueError(
            "%s -> %s is not a declared forward route (routing_model[%r] = %r)"
            % (origin, response_screen, origin, list(allowed)))


def validate_recovery_return(origin: str | None, trigger: str, outcome: str,
                             response_screen: str) -> None:
    """B. A server-selected recovery return, against the narrow allowlist.

    CORRECTED 2026-11-01 (WILD1-R3-C1, R-F03): this docstring used to say "every argument is
    a fact the server derives for itself". Three of the four are. The fourth is not:

      origin           server-side, from `TRIGGER_ORIGIN[trigger]` — but keyed by `trigger`
      trigger          **CLIENT-SUPPLIED**, read from the request's `data.trigger`
      outcome          server-side: the typed exception this backend's own `except` caught
      response_screen  server-side: a literal in a backend-built dict, never read from data

    So the SCREEN is entirely server-selected, and that is the invariant that matters. What
    a client influences is which allowlist row is consulted — and since every row resolves
    to APPOINTMENT, it cannot thereby obtain a different screen. See `TRIGGER_ORIGIN`.

    Raises `ValueError` unless the exact triple is allowlisted AND names this very screen.
    An unknown trigger, an unrecognised outcome, a generic exception, or a screen the
    triple does not authorise all land in the same refusal.
    """
    if outcome not in RECOVERY_OUTCOMES:
        raise ValueError(
            "%r is not a recognised typed refusal outcome; recovery is authorised only by "
            "%s" % (outcome, ", ".join(sorted(RECOVERY_OUTCOMES))))
    if trigger not in TRIGGER_ORIGIN:
        raise ValueError("no recovery return for unknown trigger %r" % trigger)
    if TRIGGER_ORIGIN.get(trigger) != origin:
        raise ValueError(
            "origin %r is not where trigger %r is reached from (%r)"
            % (origin, trigger, TRIGGER_ORIGIN.get(trigger)))
    permitted = RECOVERY_RETURNS.get((origin, trigger, outcome))
    if permitted is None:
        raise ValueError(
            "no recovery return is permitted for (origin=%r, trigger=%r, outcome=%r)"
            % (origin, trigger, outcome))
    if permitted != response_screen:
        raise ValueError(
            "(origin=%r, trigger=%r, outcome=%r) may return %r, not %r"
            % (origin, trigger, outcome, permitted, response_screen))


def _carries_extension_payload(data) -> bool:
    """True only for a real Data API terminate payload."""
    if not isinstance(data, dict):
        return False
    ext = data.get("extension_message_response")
    return isinstance(ext, dict) and isinstance(ext.get("params"), dict)


def validate_response_screen(trigger: str, response_screen: str, routing_model: dict,
                             *, outcome: str | None = None,
                             origin: str | None = _UNSET,
                             terminal_payload=None) -> None:
    """The one centralised entry point. Every Data API response passes through here.

    `outcome is None` means an ordinary response, which must satisfy the forward graph.
    An `outcome` names a recognised typed refusal, which must satisfy the recovery
    allowlist instead. The two are never checked against each other's rule, and a caller
    cannot satisfy the forward graph by claiming an outcome, or the allowlist by omitting
    one.

    `origin` defaults to the server's own `TRIGGER_ORIGIN` lookup. It is accepted as an
    argument only so a test can show that a wrong origin is refused; production never
    passes it.
    """
    if origin is _UNSET:
        if trigger not in TRIGGER_ORIGIN:
            raise ValueError("unknown trigger %r" % trigger)
        origin = TRIGGER_ORIGIN[trigger]
    if response_screen == TERMINAL_PROTOCOL_SCREEN:
        # Closing the Flow, not navigating in it. Only a real extension payload may do so,
        # and never under a refusal outcome: a refusal that reported success would tell the
        # customer their booking was made.
        if outcome is not None:
            raise ValueError("a refusal (%r) may not return %r"
                             % (outcome, TERMINAL_PROTOCOL_SCREEN))
        if not _carries_extension_payload(terminal_payload):
            raise ValueError(
                "%r requires an extension_message_response payload"
                % TERMINAL_PROTOCOL_SCREEN)
        return
    if outcome is None:
        validate_forward_route(origin, response_screen, routing_model)
        return
    validate_recovery_return(origin, trigger, outcome, response_screen)


def validate_transition(origin: str | None, response_screen: str,
                        routing_model: dict) -> None:
    """Deprecated alias for `validate_forward_route`, kept for existing callers.

    The name says "transition", which is precisely the conflation this correction undoes:
    an ordinary navigation and a server recovery return are not two transitions of one
    kind. New code calls `validate_response_screen`.
    """
    validate_forward_route(origin, response_screen, routing_model)

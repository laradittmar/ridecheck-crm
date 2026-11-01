"""Derive the refusal-capable Flow from the OWNER-CONFIRMED PUBLISHED structure (r3).

WHY THIS SCRIPT EXISTS BESIDE THE SUPERSEDED `make_minimal_target.py`
    `make_minimal_target.py` USED TO derive `TARGET_v7.6-minimal-refusal.json` from
    `PUBLISHED_28104222025943520_v7.3.json`. It was **neutralised on 2026-11-01**
    (WILD1-R3-C1): it now writes nothing and exits 2, because it also wrote a cyclic
    `routing_model` and so produced a Meta-invalid artifact on top of using the wrong
    baseline. **This file is the only Flow generator in the repository.** On 2026-10-30 the owner exported the live
    Flow definition for asset 28104222025943520 directly from Meta and it is r3's
    structure, not v7.3's: APPOINTMENT declares twelve fields, DETAILS renders
    `${data.contact_phone_display}` under "Te contactamos a este WhatsApp:", DETAILS asks
    the customer for no telephone number of their own, and `prepare_summary` sends
    `"phone": "${data.contact_phone}"`. v7.3 is the preserved pre-16-September baseline.

    So v7.6 was derived from the wrong baseline. Publishing it would have removed five
    APPOINTMENT fields and three DETAILS fields, re-added a required `Teléfono` input and
    reverted two action payloads — a regression of a published, handset-certified feature.
    The v7.6 artifact is retained as audit evidence and is not deleted. Its generator's
    *executable behaviour* was removed on 2026-11-01 — the file remains, with a superseded
    header, and Git history holds the original (blob `8b93343d835d0998…`, commit
    `c26f980`).

WHAT THIS ONE DOES
    Exactly the same three additions, onto r3 instead:
      A. four declared APPOINTMENT fields  — two message strings, two visibility booleans
      B. two APPOINTMENT TextBody components — immediately under the heading

    and NOTHING ELSE. In particular **no routing edge is added**.

    CORRECTED 2026-10-30 (WILD1-R3), and this header was itself corrected 2026-11-01
    (WILD1-R3-C1, review finding R-F02): until R3 this script added a third item,
    "C. two routing edges — DETAILS -> APPOINTMENT, SUMMARY -> APPOINTMENT". **Meta's
    validator rejected that artifact** in an owner-present run on 2026-10-30, because
    `routing_model` is a forward-only DAG with exactly one entry screen. The two edges
    cannot exist in any artifact. The return to APPOINTMENT that a typed refusal performs
    is a server-selected recovery response in `flow_contract.RECOVERY_RETURNS`, not a
    routing edge, and `routing_model` here is left byte-identical to r3's.

    Nothing is removed, renamed, reordered or retyped. Run it again and the output is
    byte-identical.

The source is pinned by canonical fingerprint rather than by raw bytes: Meta reserialises
Flow JSON on download, so a byte comparison against anything that has been through Meta is
meaningless, while the canonical form is reproducible on both sides.
"""
import collections
import hashlib
import json
import pathlib
import sys

#: `CANDIDATE_v7.4-prefill-r3.json`, canonicalised. This is the fingerprint the
#: 2026-09-16 Gate B Final Certification recorded for the asset it downloaded from the
#: Flows API and proved deep-structurally equal to r3 — the evidence the owner's
#: 2026-10-30 export confirms.
R3_CANONICAL_SHA256 = \
    "5c4c68d10d0471eec8d439ee71324d20515ef1128397604719360feaa971c316"


def _load_flow_contract():
    """Load THE canonical routing validator from `flow_contract.py`, by file path.

    WILD1-R3-C1: this script used to carry its own copy of the graph rules. Two copies is
    one too many — the repository already shipped an artifact Meta rejected while a local
    rule existed somewhere that would have caught it. `flow_contract.py` has no runtime
    imports, so loading it here costs nothing and guarantees the generator and the backend
    apply the *same* rules.
    """
    import importlib.util
    root = pathlib.Path(__file__).resolve().parents[3]
    path = root / "backend" / "app" / "services" / "flow_contract.py"
    if not path.exists():                                    # pragma: no cover
        raise SystemExit("cannot find the canonical validator at %s" % path)
    spec = importlib.util.spec_from_file_location("_flow_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FC = _load_flow_contract()


def canonical(doc) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def main(src: str, dst: str) -> int:
    doc = json.loads(open(src, "rb").read(), object_pairs_hook=collections.OrderedDict)

    # ── 0. the baseline is the published one, proven before anything is written ──────
    got = canonical(doc)
    if got != R3_CANONICAL_SHA256:
        print("STOP: %s canonicalises to %s, not the published r3 structure %s"
              % (src, got, R3_CANONICAL_SHA256), file=sys.stderr)
        return 2
    screens = {s["id"]: s for s in doc["screens"]}
    appt = screens["APPOINTMENT"]
    # the twelve published declarations, asserted before four are added to them
    assert list(appt["data"]) == [
        "booking_token", "vehicle_summary", "location_summary",
        "date", "is_date_enabled", "time", "is_time_enabled",
        "selected_date", "selected_time",
        "customer_name", "contact_phone", "contact_phone_display",
    ], list(appt["data"])

    # ── A. four declared fields, on the screen the backend already returns ──────────
    appt["data"]["slot_conflict_message"] = collections.OrderedDict([
        ("type", "string"),
        ("__example__", "Ese horario ya fue reservado. Un asesor se contacta a la brevedad."),
    ])
    appt["data"]["has_refusal"] = collections.OrderedDict([
        ("type", "boolean"), ("__example__", False)])
    appt["data"]["error_message"] = collections.OrderedDict([
        ("type", "string"),
        ("__example__", "Tu sesión de reserva expiró. Por favor, volvé a iniciar el proceso."),
    ])
    appt["data"]["has_error"] = collections.OrderedDict([
        ("type", "boolean"), ("__example__", False)])

    # ── B. two components, so the message is READ and not merely transmitted ────────
    #      Immediately below the heading and above the vehicle/location summaries: the
    #      refusal is the reason the customer is back on this screen, so it is the first
    #      thing under the title and not something to scroll for.
    kids = appt["layout"]["children"]
    assert kids[0]["type"] == "TextHeading", kids[0]
    assert kids[0]["text"] == "Reservá tu revisión", kids[0]
    assert kids[1]["text"] == "${data.vehicle_summary}", kids[1]
    assert kids[2]["text"] == "${data.location_summary}", kids[2]
    kids.insert(1, collections.OrderedDict([
        ("type", "TextBody"),
        ("text", "${data.slot_conflict_message}"),
        ("visible", "${data.has_refusal}"),
    ]))
    kids.insert(2, collections.OrderedDict([
        ("type", "TextBody"),
        ("text", "${data.error_message}"),
        ("visible", "${data.has_error}"),
    ]))

    # ── C. the routing model is NOT touched ─────────────────────────────────────────
    #
    # R3 (2026-10-30) removed the two edges this generator used to add. Meta's own
    # validator rejected them, in the owner-present run, with two errors:
    #
    #   "Backward route [DETAILS->APPOINTMENT] corresponding to forward route
    #    [APPOINTMENT->DETAILS] is not allowed in the routing model. Only forward routes
    #    can be specified."
    #   "No entry screen found in the routing model. Expected a screen with no inbound
    #    edges as the entry screen."
    #
    # So `routing_model` is a FORWARD-ONLY DAG with exactly one entry screen, and a
    # backward edge is not something Meta will accept at any price. The return to
    # APPOINTMENT that a typed refusal performs is therefore not a routing edge at all;
    # it is a server-selected recovery response, and it lives in
    # `backend/app/services/flow_contract.py` under `RECOVERY_RETURNS`, never here.
    #
    # The assertion below is the regression: if a future edit reintroduces either edge,
    # generation fails rather than producing an artifact Meta will reject again.
    # THE canonical validator, with the artifact's own declared screens, so a route to or
    # from a screen that does not exist is caught as well as a backward edge or a cycle.
    # `assert_required_forward_graph` additionally pins the graph to the published one.
    FC.assert_required_forward_graph(
        doc["routing_model"], screens=[s["id"] for s in doc["screens"]])

    # SUMMARY keeps `terminal: true`, and now keeps an empty successor list too, so the
    # PUBLICATION question — whether Meta tolerates a terminal screen with an outgoing edge
    # — no longer arises: there is no outgoing edge. Meta accepted this artifact with zero
    # errors and zero warnings.
    #
    # WILD1-R3-C1 (2026-11-01): the RUNTIME question is still open, and it is not the same
    # question. SUMMARY is the only `terminal: true` screen here, and `confirm_booking` is
    # reached only from it. If Meta's runtime holds that a terminal screen may receive only
    # the terminate instruction, then a Data API response naming APPOINTMENT from SUMMARY
    # can never render, whatever `routing_model` says — and that fits every observation we
    # have: both non-terminal origins worked (APPOINTMENT self-refresh, DETAILS -> SUMMARY)
    # and the terminal one failed 6/6. That is hypothesis E, it is risk M2 of the
    # 2026-10-29 publication-preparation closeout in its runtime form, and it is UNPROVEN
    # either way. Nothing in this file decides it; only a handset can.

    out = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    open(dst, "w", encoding="utf-8").write(out)
    print("wrote %s  %d bytes  raw=%s  canonical=%s"
          % (dst, len(out.encode()), hashlib.sha256(out.encode()).hexdigest(),
             canonical(json.loads(out))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))

"""Derive the refusal-capable Flow from the OWNER-CONFIRMED PUBLISHED structure (r3).

WHY THIS SCRIPT EXISTS BESIDE `make_minimal_target.py`
    `make_minimal_target.py` derives `TARGET_v7.6-minimal-refusal.json` from
    `PUBLISHED_28104222025943520_v7.3.json`. On 2026-10-30 the owner exported the live
    Flow definition for asset 28104222025943520 directly from Meta and it is r3's
    structure, not v7.3's: APPOINTMENT declares twelve fields, DETAILS renders
    `${data.contact_phone_display}` under "Te contactamos a este WhatsApp:", DETAILS asks
    the customer for no telephone number of their own, and `prepare_summary` sends
    `"phone": "${data.contact_phone}"`. v7.3 is the preserved pre-16-September baseline.

    So v7.6 was derived from the wrong baseline. Publishing it would have removed five
    APPOINTMENT fields and three DETAILS fields, re-added a required `Teléfono` input and
    reverted two action payloads — a regression of a published, handset-certified feature.
    Both the v7.6 artifact and its generator are retained as audit evidence and neither is
    deleted or rewritten.

WHAT THIS ONE DOES
    Exactly the same three additions, onto r3 instead:
      A. four declared APPOINTMENT fields  — two message strings, two visibility booleans
      B. two APPOINTMENT TextBody components — immediately under the heading
      C. two routing edges                 — DETAILS -> APPOINTMENT, SUMMARY -> APPOINTMENT

    Nothing is removed, renamed, reordered or retyped. Run it again and the output is
    byte-identical.

The source is pinned by canonical fingerprint rather than by raw bytes: Meta reserialises
Flow JSON on download, so a byte comparison against anything that has been through Meta is
meaningless, while the canonical form is reproducible on both sides.
"""
import collections
import hashlib
import json
import sys

#: `CANDIDATE_v7.4-prefill-r3.json`, canonicalised. This is the fingerprint the
#: 2026-09-16 Gate B Final Certification recorded for the asset it downloaded from the
#: Flows API and proved deep-structurally equal to r3 — the evidence the owner's
#: 2026-10-30 export confirms.
R3_CANONICAL_SHA256 = \
    "5c4c68d10d0471eec8d439ee71324d20515ef1128397604719360feaa971c316"


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

    # ── C. the transitions the deployed backend already performs ────────────────────
    #      Every refusal and every token error returns screen=APPOINTMENT.
    #      `_confirm_booking` is reached from SUMMARY; the token error is also reachable
    #      from DETAILS via `prepare_summary`. The published routing model declares
    #      neither edge, which is why the live refusals failed on the handset.
    assert doc["routing_model"] == {"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY"],
                                    "SUMMARY": []}, doc["routing_model"]
    doc["routing_model"]["DETAILS"] = ["SUMMARY", "APPOINTMENT"]
    doc["routing_model"]["SUMMARY"] = ["APPOINTMENT"]

    # SUMMARY keeps `terminal: true`. It is NOT removed: the screen is still an endpoint
    # of the forward journey, and whether Meta accepts a terminal screen that also
    # declares an outgoing edge is an open Meta-side question (recorded as a residual
    # risk), not something a local edit may pre-empt.

    out = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    open(dst, "w", encoding="utf-8").write(out)
    print("wrote %s  %d bytes  raw=%s  canonical=%s"
          % (dst, len(out.encode()), hashlib.sha256(out.encode()).hexdigest(),
             canonical(json.loads(out))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))

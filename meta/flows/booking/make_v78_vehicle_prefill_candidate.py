"""Derive the v7.8 CANDIDATE from the PUBLISHED Flow (v7.7). NOT published; nothing here talks
to Meta. Milestone PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY-AND-RECOVERY-PREFILL-CANDIDATE.

    python3 make_v78_vehicle_prefill_candidate.py \
        TARGET_v7.7-r3-prefill-refusal.json CANDIDATE_v7.8-vehicle-identity-prefill.json

WHAT IT ADDS, AND NOTHING ELSE
    A. DETAILS: two optional TextInputs, `Kilometraje` and `Dominio / patente`, after the
       listing link — the last vehicle fact the screen asks for — and their two values on
       the `prepare_summary` payload.
    B. SUMMARY: both values declared and carried on the `confirm_booking` payload.
    C. `details_prefill`: one object declared on APPOINTMENT and DETAILS, carried on the
       APPOINTMENT -> DETAILS navigate payload, and bound whole to the DETAILS Form's
       `init-values`. This is how a customer who lost a slot gets every value back.

    `routing_model` is untouched: no screen, edge or route is added. Run it again and the
    output is byte-identical.

WHY `init-values` AND NOT `init-value`
    Meta rejected `init-value` on a Dropdown and a TextInput on 2026-09-16 ("Property
    'init-value' is not allowed in 'TextInput' component"), and that rule stands: Meta's
    component reference allows `init-value` only on a component OUTSIDE a Form, and every input
    here is inside one. Inside a Form the documented mechanism is the Form's own `init-values`:
    "<key, value> object where key - Field Name in Component; value - Field Initial Value",
    dynamic as `"${data.init_values}"`, a String for a Text Entry. That is a different property
    on a different component, and it had never been tried on this asset.

    **It is documented, not proven.** No artifact Meta has accepted carries `init-values`,
    `pattern` or `helper-text`, and `scripts/validate_flow_json.py --precedent` says so. Local
    PASS is not Ejecutar, and Ejecutar is not a handset.

WHY THE PATTERNS MATCH THE EMPTY STRING
    Both inputs are optional, and the reference does not say whether `pattern` is applied to an
    empty optional input. Admitting "" is correct under either reading. Every value they accept
    is accepted by `services/vehicle_identity.py` (a test holds them together), so a handset
    cannot reach the server's refusal.

WHY `Kilometraje` IS NOT `input-type: number`
    The owner accepts `85.000`. Meta applies a base regular expression to a number input before
    `pattern`, and the reference does not say whether a dot passes it. A plain text input leaves
    `pattern` as the only client rule. The cost is the numeric keypad.

The source is pinned by canonical fingerprint, as `make_r3_refusal_target.py` pins r3.
"""
import collections
import hashlib
import json
import pathlib
import sys

OD = collections.OrderedDict

#: `TARGET_v7.7-r3-prefill-refusal.json`, PUBLISHED 2026-10-30, canonicalised.
V77_CANONICAL_SHA256 = \
    "dccd3bc973b1b51526c5064027b5d8f61ee885293dfa9815a272609092aca17d"

#: Every Flow input the customer types, in screen order. The prefill object's whole vocabulary.
PREFILL_INPUTS = ("name", "email", "inspection_address", "seller_name", "seller_phone",
                  "listing_url", "vehicle_mileage", "vehicle_plate")
#: Plain digits, or thousands-grouped (owner decision D-3), up to 999.999.999.
MILEAGE_PATTERN = r"^([0-9]{0,9}|[1-9][0-9]{0,2}(\.[0-9]{3}){1,2})$"
PLATE_PATTERN = "^[A-Za-z0-9 .-]{0,16}$"


def _load_flow_contract():
    """THE canonical routing validator, by file path — never a second copy of the rules."""
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


def _prefill_declaration():
    return OD([
        ("type", "object"),
        ("properties", OD((key, OD([("type", "string")])) for key in PREFILL_INPUTS)),
        ("__example__", OD([
            ("name", "Juan Pérez"), ("email", "juan@example.com"),
            ("inspection_address", "Av. Santa Fe 1234, Palermo"), ("seller_name", "Carlos"),
            ("seller_phone", "1198765432"), ("listing_url", "https://example.com/vehiculo"),
            ("vehicle_mileage", "85000"), ("vehicle_plate", "ABC123")])),
    ])


def _string(example):
    return OD([("type", "string"), ("__example__", example)])


def _form(screen):
    forms = [c for c in screen["layout"]["children"] if c.get("type") == "Form"]
    assert len(forms) == 1, screen["id"]
    return forms[0]


def main(src: str, dst: str) -> int:
    doc = json.loads(open(src, "rb").read(), object_pairs_hook=OD)

    # ── 0. the baseline is the published one, proven before anything is written ──────
    got = canonical(doc)
    if got != V77_CANONICAL_SHA256:
        print("STOP: %s canonicalises to %s, not the published v7.7 structure %s"
              % (src, got, V77_CANONICAL_SHA256), file=sys.stderr)
        return 2
    routing_before = json.dumps(doc["routing_model"], sort_keys=True)
    screens = {s["id"]: s for s in doc["screens"]}
    appt, details, summary = screens["APPOINTMENT"], screens["DETAILS"], screens["SUMMARY"]

    # ── C. the prefill object: declared, carried, bound ─────────────────────────────
    appt["data"]["details_prefill"] = _prefill_declaration()
    details["data"]["details_prefill"] = _prefill_declaration()
    footer = _form(appt)["children"][-1]
    assert footer["type"] == "Footer" and footer["on-click-action"]["name"] == "navigate"
    footer["on-click-action"]["payload"]["details_prefill"] = "${data.details_prefill}"

    form = _form(details)
    assert list(form) == ["type", "name", "children"], list(form)
    form_children = form.pop("children")
    form["init-values"] = "${data.details_prefill}"
    form["children"] = form_children

    # ── A. the two inputs, after the listing link and before the Footer ─────────────
    assert [c.get("name") for c in form_children[:-1]] == list(PREFILL_INPUTS[:6]), form_children
    assert form_children[-1]["type"] == "Footer"
    form_children[-1:-1] = [
        OD([("type", "TextInput"), ("label", "Kilometraje"), ("name", "vehicle_mileage"),
            ("pattern", MILEAGE_PATTERN),
            ("helper-text", "Opcional. Ej.: 85000 o 85.000."), ("required", False)]),
        OD([("type", "TextInput"), ("label", "Dominio / patente"), ("name", "vehicle_plate"),
            ("pattern", PLATE_PATTERN), ("helper-text", "Opcional. Ej.: ABC123 o AB123CD."),
            ("required", False)]),
    ]
    payload = form_children[-1]["on-click-action"]["payload"]
    assert payload["trigger"] == "prepare_summary", payload
    payload["vehicle_mileage"] = "${form.vehicle_mileage}"
    payload["vehicle_plate"] = "${form.vehicle_plate}"

    # ── B. SUMMARY carries both to confirm_booking ───────────────────────────────────
    summary["data"]["vehicle_mileage"] = _string("85000")
    summary["data"]["vehicle_plate"] = _string("ABC123")
    confirm = [c for c in summary["layout"]["children"] if c.get("type") == "Footer"][0]
    payload = confirm["on-click-action"]["payload"]
    assert payload["trigger"] == "confirm_booking", payload
    payload["vehicle_mileage"] = "${data.vehicle_mileage}"
    payload["vehicle_plate"] = "${data.vehicle_plate}"

    # ── the routing model is NOT touched, and generation fails if it ever is ─────────
    assert json.dumps(doc["routing_model"], sort_keys=True) == routing_before
    FC.assert_required_forward_graph(
        doc["routing_model"], screens=[s["id"] for s in doc["screens"]])

    out = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    open(dst, "w", encoding="utf-8").write(out)
    print("wrote %s  %d bytes  raw=%s  canonical=%s"
          % (dst, len(out.encode()), hashlib.sha256(out.encode()).hexdigest(),
             canonical(json.loads(out))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))

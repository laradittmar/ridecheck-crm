"""Derive the minimal refusal-capable Flow from the PUBLISHED artifact.

Every change is made by this script, from the published file, so the published-to-target
delta is exactly what the code below does and nothing else. Run it again and the output is
byte-identical.
"""
import collections, json, sys

src, dst = sys.argv[1], sys.argv[2]
doc = json.loads(open(src, "rb").read(), object_pairs_hook=collections.OrderedDict)

screens = {s["id"]: s for s in doc["screens"]}
appt = screens["APPOINTMENT"]

# ── 1. four declared fields, on the screen the backend already returns ──────────
#      Two message strings and the two booleans that make them visible. Nothing else:
#      no prefill, no identity, no selection echo.
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

# ── 2. two components, so the message is READ and not merely transmitted ────────
kids = appt["layout"]["children"]
assert kids[0]["type"] == "TextHeading", kids[0]
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

# ── 3. the transitions the deployed backend already performs ────────────────────
#      Every refusal and every token error in `_confirm_booking` returns
#      screen=APPOINTMENT. `_confirm_booking` is reached from SUMMARY, and the token
#      error is also reachable from DETAILS via prepare_summary. The published routing
#      model declares neither edge, which is why the live refusals failed.
doc["routing_model"]["DETAILS"] = ["SUMMARY", "APPOINTMENT"]
doc["routing_model"]["SUMMARY"] = ["APPOINTMENT"]

out = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
open(dst, "w", encoding="utf-8").write(out)
print("wrote", dst, len(out), "bytes")

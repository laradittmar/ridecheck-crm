# PUBLISHED v7.3 → CANDIDATE v7.4-prefill

Machine diff: `CANDIDATE_DIFF.patch` (+47 / −12 lines). Nothing has been uploaded.

## Why anything changes at all

A Flow component renders a starting value **only** from `init-value`. The published
definition declares none, so no data the back end sends can preselect a slot or prefill a
field. F4 narrowed the option lists — which removed the wrong choice but not the choosing.

One structural fact drives the shape of this change: **APPOINTMENT → DETAILS is client-side
`navigate`.** The back end is never called between those screens, so anything DETAILS needs
must leave from APPOINTMENT's data through the navigate payload. That is why identity fields
appear on APPOINTMENT rather than only on DETAILS.

## APPOINTMENT

| change | reason |
|---|---|
| `data.selected_date`, `data.selected_time` (string) | the values the Dropdowns bind to |
| `Dropdown date` → `init-value: ${data.selected_date}` | renders the agreed date as chosen |
| `Dropdown time` → `init-value: ${data.selected_time}` | renders the agreed time as chosen |
| `data.customer_name`, `data.contact_phone`, `data.contact_phone_display` | carried here because the back end cannot reach DETAILS directly |
| Footer navigate payload forwards those three | the only bridge to DETAILS |

An empty string means *nothing selected* — that is the open-picker and day-only case, and it
is why the back end never sends a value that is not also an `id` in the matching data-source.

## DETAILS

| change | reason |
|---|---|
| `data.customer_name`, `data.contact_phone`, `data.contact_phone_display` | received from the navigate payload |
| `TextInput name` → `init-value: ${data.customer_name}` | prefilled, still editable |
| **`TextInput phone` removed**, replaced by a `TextCaption` showing the masked number | WhatsApp already proved this identity; asking again invites a typo to become the number an operator calls |
| Footer payload `phone` now `${data.contact_phone}` instead of `${form.phone}` | the form field no longer exists |

The caption reads *"Te contactamos a este WhatsApp: …0001"* — last four digits only, so a full
number never appears on a screen we also log.

**The UI is not what enforces this.** `handle_prepare_summary` and `handle_confirm_booking`
both overwrite the payload phone with `ctx.contact.wa_id` server-side, so a tampered payload
cannot change the booking identity even if the screen were altered.

## SUMMARY

Unchanged.

## Deliberately NOT changed

- screen set and routing (`navigate` / `data_exchange` actions identical, SUMMARY still terminal)
- every other field, label and validation
- a separate *coordination phone* concept. If a customer ever needs to give a different number
  for the appointment, that is a new business field and an owner decision — **BUSINESS DECISION
  REQUIRED**, not something to invent inside a prefill change.

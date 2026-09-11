# Booking Flow prefill — what the back end can and cannot do

**Constraint: the published Flow declares no `init-value` on any input, so the back end
cannot preselect or prefill anything. Changing that requires republishing the Flow on Meta.**

Flow `28104222025943520` ("RideCheck Booking"), version 7.3, data_api_version 3.0, fetched
read-only from the Meta Flows API on 2026-09-11.

## What the published definition actually contains

```
APPOINTMENT   Dropdown  name=date   enabled=${data.is_date_enabled}   (no init-value)
              Dropdown  name=time   enabled=${data.is_time_enabled}   (no init-value)
DETAILS       TextInput name=name               required=true         (no init-value)
              TextInput name=phone              required=true  phone  (no init-value)
              TextInput name=email              required=false email
              TextInput name=inspection_address required=true
              TextInput name=seller_name/seller_phone/listing_url     (optional)
SUMMARY       read-only summary + "Solicitar turno"
```

A Flow component renders a starting value only from `init-value`. Since none is declared,
adding data keys such as `selected_date` or `phone` to the INIT/data_exchange response would
change nothing on screen.

## What we did instead (no republish needed)

The back end owns the **option lists**. When an exact slot has already been agreed in
conversation and is still free, `_appointment_screen_data` returns exactly one date and one
time. The customer confirms instead of re-choosing, the date→time round trip disappears, and
no wrong slot can be selected. A day-only or NEXT_AVAILABLE dispatch keeps the full picker,
because there the choice is still real.

Narrowing is UX only: `handle_confirm_booking` revalidates through ScheduleService exactly as
before, so a slot that went stale between opening and confirming is still refused.

## What remains open — owner action

The customer still retypes a phone number WhatsApp already knows, because `phone` is
`required=true` with no `init-value`. To fix it, the Flow JSON must be edited and republished
on Meta with, at minimum:

```
DETAILS  TextInput name=phone  init-value=${data.phone}      # and add `phone` to its data
         TextInput name=name   init-value=${data.name}
APPOINTMENT Dropdown name=date init-value=${data.selected_date}
            Dropdown name=time init-value=${data.selected_time}
```

The back end can supply those keys as soon as the Flow binds them. Note that a Flow republish
creates a new version and should be smoke-tested before a Wild.

**Do not trust a customer-edited phone over the canonical WhatsApp identity**: the contact
`wa_id` is the identity of record. If a separate coordination phone is ever a genuine business
concept, that should be a distinct field with a distinct label, not the same one.

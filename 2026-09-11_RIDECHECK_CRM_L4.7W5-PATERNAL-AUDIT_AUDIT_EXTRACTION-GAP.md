PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W5-PATERNAL-AUDIT (aborted rescue Wild, 2026-09-11)

IMAGE: ridecheck-crm-backend:w5f4-booking-context-dcc5ec1 — unchanged, no code edited
SCOPE: crm_test only. Outbound disarmed at stop. Production untouched.
EVIDENCE: /opt/ridecheck-crm-forensics/L4.7W5-PATERNAL-AUDIT_20260911T*.tar.gz
          sha256 a9c9360825b00a06e70ae4fd7a84961a39cbfc44062d13707a97cf7c1453d6c8

RESULT: DEFECT CONFIRMED. My L4.7W5-F4 fix does not run on this path, and my verification
of it was wrong. Stated plainly below.

---

## 1. What happened

```
18:44:58  "quiero revisar un auto en paternal"
18:45:03  "para hoy tenes?"
18:45:29  → asks what type of vehicle
18:48:54  "es un sandero 2020"
18:49:17  → LOCATION FLOW: "completá dónde está el auto"
```

Canonical state after the turn:

```
candidate 139  Renault Sandero 2020 AUTO   zone_group NULL   zone_detail NULL
thread state   home_zone_group NULL  home_zone_detail NULL  location_fallback_flow_sent = t
```

"Paternal" was never extracted. The customer was sent to a Flow to type an address — exactly
the behaviour L4.7W5-F4 was supposed to end.

## 2. Root cause — two independent layers, and I fixed the wrong one

Location handling has **two** stages, and they do not share code:

| stage | function | input | status |
|---|---|---|---|
| 1. extraction | `ConversationEngine._extract_zone_from_text` | raw message text | **still broken** |
| 2. catalog lookup | `PricingRepository.find_zone_by_group_and_detail` | an already-isolated locality string | fixed in F4 |

Stage 1 matches by **substring containment**:

```python
zone_norm = _n(zone.zone_detail)          # "la paternal"
if zone_norm in normalized_text:          # "quiero revisar un auto en paternal"
    return zone                           # never true
```

The canonical entry is "La Paternal", the customer wrote "paternal", and the literal string
"la paternal" does not occur in the message — so no match. The ASR-fuzzy fallback compares the
**whole compacted message** (`quierorevisarunautoenpaternal`) against compact zone names, which
scores far below its 0.85 threshold. Stage 1 therefore returns `None`, and stage 2 — the
article-aware lookup I added — is never called with "paternal" at all.

Measured on the deployed image:

```
"quiero revisar un auto en paternal"      -> None
"quiero revisar un auto en la paternal"   -> ('CABA', 'La Paternal')
"el auto esta en paternal"                -> None
"el auto esta en palermo"                 -> ('CABA', 'Palermo')     # no article, works
find_zone_by_group_and_detail('paternal') -> ('CABA', 'La Paternal') # F4's fix, unreached
```

## 3. My verification was wrong, and that is the more important finding

The F4 closeout reported **"PATERNAL: ('CABA', 'La Paternal')"** and **"ARTICLE
NORMALIZATION: PASS"**. That claim came from calling `find_zone_by_group_and_detail("paternal")`
directly — the layer I had just changed — not from the path a message actually takes.

I tested the code I wrote instead of the behaviour that was reported. The owner's sentence
("Un 3008 en paternal") was in the audit I had written myself the day before, and I never ran
it end to end. Every test in the F4 suite operates on the repository or on `_location_from_candidate`;
**not one feeds raw customer text through `_extract_zone_from_text`.**

This is the third time in this programme that a near-match rule has been validated against the
shape I had in mind rather than the shape a customer produces — presence, service scope, and
now locality. The pattern is the finding.

## 4. Why the fix is not a one-line change

The obvious repair — also match the article-stripped canonical form as a substring — carries a
false-positive risk the exact-match layer does not have, because stage 1 scans free text:

- `La Plata` → bare `plata`. "tengo plata para pagar" would match a locality.
- `La Boca` → bare `boca`. "boca" has other meanings, including a football club.
- `El Talar` → bare `talar`, a verb.

Currently `"tengo plata para pagar"` correctly returns `None`; a naive bare-form substring
match would break that. Any fix needs word-boundary matching plus a preposition or
locality-context requirement, and must be tested against negative cases, not only positive
ones. That is a design decision, not a patch, which is why this audit stops here rather than
editing code mid-Wild.

## 5. Secondary observation

"para hoy tenes?" (18:45:03) was never addressed. The reply asked only for the vehicle type.
Same-day intent is dropped; note that the forward search starts at `today + 1`, so today can
never be offered anyway — a customer asking for today should be told so, not ignored.

## 6. Safety during the aborted session

| metric | count |
|---|---|
| outbound sent | 2 |
| unattributed outbound | 0 |
| security events | 0 |
| bookings created | 0 |
| wrong vehicle / quote / location asserted | 0 |

The vehicle resolved correctly (Renault Sandero 2020, AUTO). Nothing false was said — the
system asked for information rather than inventing it. The failure is a missed extraction and
an unnecessary Flow, not an incorrect claim.

## 7. Status of the rescue path

Still unproven live. This is the third Wild in a row that has not reached it: two ended in
bookings, this one was stopped at qualification.

## 8. Recommendation

Fix `_extract_zone_from_text` to recognise article-prefixed localities by their bare form,
with:

- word-boundary matching, never bare substring
- a negative-case suite: "tengo plata", "la boca del lobo", "vamos a talar" must not resolve
- the same ambiguity refusal already used in the catalog layer
- **an end-to-end test from raw message text**, which is what was missing

And in the same milestone, a test that asserts the exact customer sentences from the two Wild
audits — "Un 3008 en paternal" and "quiero revisar un auto en paternal" — resolve through CE,
not through the repository.

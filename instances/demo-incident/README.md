# Demo instance: incident postmortem

Synthetic data, no real service, no personal data. A complete, runnable, non-legal instance that proves the
engine is domain-free. The configuration is `instance.yaml`; this file describes it for a reader.

## Lenses and seats

The `red-team` role seats one panel member per perspective or discipline lens, and each seat reads the
target only through its own lens view:

| Seat | Lens view | Reads |
|---|---|---|
| `seat_service_owner` | `lens_service_owner` | in-window units labelled tier 2 or higher |
| `seat_security` | `lens_security` | in-window original logs about configuration or dependencies |
| `seat_management` | `lens_management` | in-window units labelled tier 2 or higher |

The framework lens `lens_incident_framework` has no seat: it files labelled units under the review
template's headings (trigger, propagation, detection, mitigation, prevention).

## This file is checked

`xscholar test` and CI pass this file to the drift check (`xscholar/drift.py`, check c). Every
`lens_<name>` and `seat_<name>` above must name a lens `instance.yaml` defines, so renaming a lens without
updating this file fails the build.

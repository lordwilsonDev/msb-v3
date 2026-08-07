# SMI-017-v1.0 Release Notes

## Service Layer

SMI-017 turns the evaluation stack into a callable FastAPI service.

Endpoints:

- `POST /smi/query`
- `POST /smi/evaluate`
- `POST /smi/adapt`
- `POST /smi/report`

## Validation

- Endpoint behavioral tests: 4 added
- Regression suite: 208 passed

## Auth

- Centralized token-map auth with `[REDACTED]` role matrix remains enforced.
- No public route changes outside `/smi`.

## Known Limitations

- Routes return structured placeholders until backend wiring is completed in a later milestone.

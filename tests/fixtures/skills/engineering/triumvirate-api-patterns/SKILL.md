---
name: triumvirate-api-patterns
description: Test fixture skill — triumvirate API pattern contracts (CI).
---

# Triumvirate API Patterns (fixture)

Committed fixture so the skill-discovery contract is testable in CI without
the machine-local `~/.hermes/skills` store. CI sets `MSB_SKILLS_DIR` to the
`tests/fixtures/skills` directory; local servers keep reading the real store.

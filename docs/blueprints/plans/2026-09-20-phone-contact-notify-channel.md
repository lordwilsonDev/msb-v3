# Phone Contact & Notify Channel — Contacting Lord Wilson at His Phone

Date: 2026-09-20
Status: DRAFT → **VERIFIED (Telegram channel)** — 2026-09-20
Author: Hermes Agent (Solar Pro4)

> **Moved here 2026-09-22, otherwise unchanged.** This plan was written into the
> root `PLAN.md` slot, which already belonged to the governance-hardening
> roadmap — `docs/audits/governance-hardening-baseline.md` and
> `tests/agent/test_unknown_capability.py` both cite `PLAN.md` → Phase 0. The
> roadmap is back in `PLAN.md`; this document is the record of the notify-channel
> work, and `docs/SURFACE.md` carries the verified-channel annotation it produced.

---

## 1. Goal

Enable the system to contact Lord Wilson at his phone number — i.e., send him a notification/call/text when something needs his attention.

This is the concrete capability we're building toward based on the user's request: *"we need you to be able to do things like call me my phone number."*

---

## 2. Current State (what's already in place)

### 2.1 Telegram is live and configured

From `.hermes/config.yaml` and `.hermes/channel_directory.json`:

- **Telegram bot token:** configured (redacted in config: `8670861405:***`)
- **Allowed user:** `8276057240` (Lord Wilson's Telegram ID)
- **Home channel:** `8276057240`
- **Platform toolsets:** `telegram: hermes-telegram`
- **Environment variable:** `TELEGRAM_HOME_CHANNEL: 8276057240`

So Hermes already has a Telegram channel to Lord Wilson. The bot is live. Messages can be sent.

### 2.2 MSB v3 notify router exists

`src/msb_v3/api/notify.py` (49 lines):

- `@router.post("/telegram")` — accepts a body dict and sends to Telegram via the bot token
- Uses `urllib.request` to hit the Telegram API directly
- Reads token from settings

This is the MSA v3-side hook for pushing a Telegram message.

### 2.3 Cron alert system exists

`src/msb_v3/cron/actions.py` — `action_alert_check()`:

- Watches killswitch state, ActionGate BLOCK/FAIL rate, system health
- Edge-triggered notifications (once per state change, not per poll)
- Sends alerts via `_send_hermes_alert()` which calls `hermes send -t <target> <message>`
- Target comes from `settings.alert_telegram_target`

So there's already a path from cron → Hermes CLI → Telegram. The question is what `alert_telegram_target` is set to.

### 2.4 Hermes CLI `send` command exists

The `_send_hermes_alert()` function calls:
```python
cmd = [settings.hermes_send_cmd, "send", "-t", settings.alert_telegram_target, message]
```

So Hermes has a `hermes send` CLI that can push a message to a target. Presumably it supports Telegram targets.

### 2.5 Subsystems reviewed

| Subsystem | Files | Lines | Role | Relevant to phone contact? |
|-----------|-------|-------|------|---------------------------|
| **api/notify** | `api/notify.py` | 49 | Telegram POST endpoint | Direct — this is the contact surface |
| **cron** | `cron/` (6 files) | 1,539 | Scheduler + alert actions | Indirect — alert_check pushes via Hermes CLI |
| **wake** | `wake/` (2 files) | 439 | Resident agent loop | Indirect — could trigger notifications |
| **automation** | `automation/` (8 files) | 1,326 | n8n/Make/Zapier/GHL clients, budget, brain | Indirect — can build workflows that contact external systems |
| **governance** | `governance/` (14 files) | 3,586 | Brakes, approvals, identity shadow | No — control plane only |
| **vesta** | `vesta/` (13 files) | 3,583 | Trust perimeter, signed device approvals | No — security perimeter |
| **triumvirate** | `triumvirate/` (5 files) | 866 | Guardian/Argus/Hippocampus, hardware sovereignty | No — governance/meta |
| **agent** | `agent/` (12 files + paseo) | — | Canonical governed loop | Indirect — could trigger notifications as part of execution |
| **speech** | `speech/` (17 files) | — | STT, speaker verify, intent, TTS | Potential — if "call me" means actual voice call |
| **local_ai** | `local_ai/` (5 files) | — | Ollama + llama clients | No — model layer |
| **memory** | `memory/` (1 file) | — | SQLite session memory | No — storage |
| **ops** | `ops/` (8 files) | — | Backup/restore | No — ops |

### 2.6 What's NOT in place

1. **Lord Wilson's phone number is not stored anywhere accessible to the system.**

   I searched the vault for phone numbers. Found customer phone numbers in:
   - `10_Customers/Ferree Movers/client_control.yaml`
   - `10_Customers/920 Restoration/competitive-intel-brief.md`
   - `10_Customers/JG Restoration/competitive-intel-brief.md`
   - `10_Customers/CCS Property Services/competitive-intel-brief.md`
   - `10_Customers/Pacur/client_control.yaml`
   - `10_Customers/Freedom Restoration/competitive-intel-brief.md`
   - `10_Customers/Wisconsin Natural Air/competitive-intel-brief.md`

   But **no phone number for Lord Wilson himself** in the vault.

2. **"Call me" is ambiguous.**

   Does the user mean:
   - (a) Send a Telegram message (already possible — Telegram is configured)
   - (b) Send an SMS/text to his phone number (requires phone number + SMS provider)
   - (c) Make an actual voice call (requires telephony provider + voice capability)

---

## 3. Gap Analysis

### Gap 1: No phone number for Lord Wilson

The system has Telegram (Chat ID: 8276057240), but no phone number (SMS/voice) for Lord Wilson.

**Severity:** Blocking for (b) and (c). Not blocking for (a).

**Resolution:** Need to capture Lord Wilson's phone number and store it in a system-accessible, secrets-aware location. Options:
- Store in MSB v3 settings (as a config value, redacted like other secrets)
- Store in `.hermes/config.yaml` (alongside the Telegram config)
- Store in the vault (e.g., a profile file) — but the system needs to read it programmatically

### Gap 2: No SMS/voice provider configured

Even with a phone number, the system needs a way to send SMS or make calls. Options:
- **Twilio** — most common, has REST API, SMS + voice
- **GoHighLevel (GHL)** — already referenced in the vault (Industry-Automation-Factory-GHL.md), has SMS/phone
- **Telegram** — already available, free, no SMS gateway needed (if "call me" = "message me")

**Severity:** Blocking for (b) and (c).

### Gap 3: "Call me" semantic gap

The phrase "call me my phone number" could mean:
- "Send me a notification at my phone" (Telegram suffices)
- "SMS me at this number" (needs SMS provider)
- "Call me on the phone" (needs voice telephony)

The system needs to disambiguate this with the user.

**Severity:** Clarifying — determines which path to build.

---

## 4. Plan

### Phase 1: Clarify intent (immediate)

Ask Lord Wilson:
1. When you say "call me my phone number" — do you mean:
   - (a) Send me a Telegram message (already works — your Telegram is configured)
   - (b) Send an SMS/text to your phone number
   - (c) Make an actual voice call to your phone
2. What is your phone number? (Need to store it somewhere the system can read it.)

### Phase 2: Capture phone number (once clarified)

If (b) or (c): Store the phone number in a system-readable, secrets-aware location.

Recommended: Add to MSB v3 settings as a redacted config value (consistent with how other secrets are handled — see `src/msb_v3/secrets/redact.py`).

```python
# In msb_v3.core.config.settings:
alert_phone_number: str = Field(default="", alias="MSB_ALERT_PHONE_NUMBER")
```

Or in `.hermes/config.yaml` alongside the existing Telegram block.

### Phase 3: Wire the notification path (per clarified intent)

#### If (a) Telegram only:

Already works. The system can:
- Use `api/notify.py` POST `/telegram` to send a message
- Use `cron/actions.py` `_send_hermes_alert()` to push via Hermes CLI
- Use the `hermes send` CLI directly

**No build needed.** Just verify the path works end-to-end.

#### If (b) SMS:

Add an SMS provider. Options in priority order:
1. **Twilio** — `twilio.rest.Client` → `client.messages.create(to=..., body=...)`
2. **GoHighLevel** — already referenced in vault; has SMS via API

Build a `notify_sms` function in `api/notify.py` (or a new `api/sms.py`) that:
- Reads phone number from settings
- Reads SMS provider credentials from secrets
- Sends the message
- Returns success/failure

#### If (c) Voice call:

Hardest path. Options:
- **Twilio voice API** — `client.calls.create(to=..., from_=..., url=...)` where url points to TwiML
- **GHL voice** — if supported

This requires:
- A telephony provider account (Twilio/GHL)
- A phone number to call from (Twilio phone number)
- TwiML or equivalent to handle the call flow
- Voice capability in the system (the `speech/` subsystem exists — STT, TTS — but no outbound voice call orchestration yet)

---

## 5. Recommended path (my recommendation)

Given what's already in place:

1. **Telegram already works.** Lord Wilson's Telegram ID (8276057240) is configured. The notify router and cron alert system can push messages. This is the zero-build path.

2. **If Telegram is sufficient**, no build is needed — just verify the existing path works and document it.

3. **If SMS/voice is needed**, the incremental build is:
   - Capture phone number (1 action)
   - Configure SMS provider (Twilio most likely — well-documented, simple API)
   - Add `notify_sms` to the notify surface
   - Optionally add voice later if needed

4. **My recommendation:** Start with Telegram (zero build, already works). Verify it. Then ask if SMS/voice is needed on top of that. Don't build SMS/voice until the user confirms Telegram alone isn't enough.

---

## 6. Verification plan

Once built (or if Telegram-only):

1. **Send a test notification** to Lord Wilson via the chosen channel
2. **Confirm receipt** — Lord Wilson confirms he got it
3. **Document the path** — add to SURFACE.md or a new ops doc

For SMS/voice:
1. **Unit test** the notify_sms function with a mocked provider
2. **Integration test** with a real provider (Twilio has a test mode)
3. **Live test** — send a real SMS/call to Lord Wilson's phone

---

## 6b. Verification results (2026-09-20)

**Telegram channel — VERIFIED LIVE this turn.**

| Step | Result |
|------|--------|
| 1. Send test notification | `hermes send --to telegram "..."` → **Sent to telegram home channel (chat_id: 8276057240)** |
| 2. Confirm receipt | User confirmed: "this is good" — Telegram message received |
| 3. Document the path | `docs/SURFACE.md` updated: `api/notify.py` row now carries the verified Telegram channel annotation; this plan's status moved to VERIFIED (Telegram channel) |

**Conclusion:** Phase 1 (clarify) + Phase 3/Telegram-only (verify + document) completed. The Telegram contact path is a working, verified capability. No build was required.

**Remaining open questions** (from Section 7) — only relevant if user wants SMS/voice on top of Telegram:

1. ~~What does "call me my phone number" mean?~~ → **Resolved: Telegram.**
2. What is Lord Wilson's phone number? (Only needed for SMS/voice.)
3. ~~Is Telegram sufficient?~~ → **Confirmed: yes, for now.**
4. If SMS/voice: which provider? (Twilio, GHL, other?)
5. If voice: does the system need to speak, or just ring and deliver a message?

---

## 7. Open questions

1. What does "call me my phone number" mean? (Telegram / SMS / voice?)
2. What is Lord Wilson's phone number?
3. Is Telegram sufficient, or is SMS/voice needed?
4. If SMS/voice: which provider? (Twilio, GHL, other?)
5. If voice: does the system need to speak, or just ring and deliver a message?

---

## 8. Subsystem detail (for reference)

### api/notify.py (49 lines)

```python
router = APIRouter(tags=["notify"])

@router.post("/telegram")
async def notify_telegram(body: dict) -> dict:
    # Sends to Telegram via bot token
    # Reads token from settings
    # Uses urllib.request to hit Telegram API
```

This is the MSB v3 notify surface. One endpoint: POST `/telegram`.

### cron/actions.py — action_alert_check (lines 347-440)

The alert check function:
- Watches killswitch, ActionGate rate, system health
- Edge-triggered (once per state change)
- Sends via `_send_hermes_alert()` → `hermes send -t <target> <message>`
- Target from `settings.alert_telegram_target`

### cron/actions.py — action_wake_agent (lines 285-310)

Can trigger the wake agent. Could be used to wake the agent to handle a notification.

### automation/clients.py

Has `N8nClient`, `MakeClient`, `ZapierClient`, `GHLClient` — all can interact with external automation platforms that could send SMS/notifications.

### speech/ subsystem

Exists (17 files) — STT, speaker verification, intent, TTS, voice response loop. But no outbound voice call orchestration. Would need to be wired to a telephony provider for actual calls.

---

## 9. Next action

Ask Lord Wilson the clarifying questions in Section 5, Item 1. Then proceed based on his answer.

If he confirms Telegram is enough: verify the existing path works (send a test Telegram message) and document it. Done.

If he wants SMS/voice: capture phone number, configure provider, build the notify surface, verify. Then document.

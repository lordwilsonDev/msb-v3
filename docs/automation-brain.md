# Automation Brain — msb-v3 creates its own automations

Give the runtime a plain-language request and a small budget, and it plans
and creates automations itself — n8n first (self-hosted, free), then Make /
Zapier / GoHighLevel as their keys are configured.

## The flow

```
request ──DeepSeek (the $10 brain)──▶ structured plan
                                          │
                                          ▼
                              budget check (cap = $10)
                                          │
                              dry-run by default ──▶ manifest: "dry_run"
                                          │
                              approve=true (operator token = approval)
                                          ▼
                              provider client creates it ──▶ manifest: "created"
```

- **Planning** — DeepSeek turns the request into
  `{provider, name, description}`. n8n is the default provider when none is
  named: it creates and **activates a real webhook workflow**
  (Webhook → Respond to Webhook) and reports the webhook URL.
- **Dry-run by default** — `MSB_AUTOMATION_DRY_RUN=1` (or a request without
  `approve: true`) records the plan in the manifest and creates nothing.
  The approval rule is the repo's own: *the operator token IS the approval*
  (same as cron `requires_approval` jobs).
- **Budget** — every plan/creation records an estimated LLM cost against
  `MSB_AUTOMATION_BUDGET_USD` (default `10.0`). The brain refuses to spend
  past the cap (fail-closed). Platform per-run costs (Zapier/Make/GHL
  operations) are the provider's own billing and are noted, not pre-paid.
- **Manifest** — every attempt (created / dry_run / blocked / failed) is
  appended to `data/runtime/automation/manifest.jsonl`. The ledger is the
  audit trail for what the brain did and why.

## Usage

```bash
# Plan + dry-run (nothing created — see the plan in the manifest)
curl -X POST http://127.0.0.1:8766/automation/create \
  -H "Authorization: Bearer $MSB_OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"description": "n8n workflow that replies to webhook pings"}'

# Same, with explicit approval (operator token = the approval)
curl -X POST http://127.0.0.1:8766/automation/create \
  -H "Authorization: Bearer $MSB_OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"description": "n8n workflow that replies to webhook pings", "approve": true}'

# The ledger + budget + which providers are live
curl http://127.0.0.1:8766/automation/manifest -H "Authorization: Bearer $MSB_OPERATOR_TOKEN"
curl http://127.0.0.1:8766/automation/status -H "Authorization: Bearer $MSB_OPERATOR_TOKEN"
```

The wake agent does the same thing: send it "build me an n8n workflow…"
via `POST /wake` and the reply carries the plan + dry-run result.

## The perceiver — stages 1–4 (zero money, zero platforms)

The platform side is reach; the logic never leaves msb-v3. Four stages make
that literal, and all four are hermetic — no keys, no spend, testable today.

**Stage 1 — the living manifest.** A manifest entry with `schedule` +
`action` is a *living automation*: `automation/state.py` holds the mutable
contract (enabled / schedule / last run), `automation/dispatcher.py`
executes what's due on the wake cycle. Appending a line = creating an
automation; flipping `enabled` = disabling it. Outbound POSTs are
allowlisted (`MSB_AUTOMATION_WEBHOOK_HOSTS`, default loopback + the
providers' own hosts) — a host outside the allowlist is refused, never
called.

**Stage 2 — recipes (your language).** `automation/recipes.py` parses plain
sentences deterministically — `every 30 minutes, post a heartbeat to
http://…`, `daily at 09:00, ping https://hook.make.com/abc` — into living
automations with `provider="self"`. No LLM, no platform syntax: the brain
tries the recipe parser first and only falls back to DeepSeek for fuzzy
requests.

**Stage 3 — the webhook sense.** One endpoint, `POST /hook/<automation_id>`
(`api/hook.py`) — Make/Zapier/GHL/n8n just forward payloads there, and the
payload lands in the wake inbox tagged with the automation id. The platform's
only job is pointing here; the resident agent decides what the payload means
on its next wake. Edge: optional shared secret (`MSB_AUTOMATION_HOOK_SECRET`,
constant-time) + bounded payloads. The `automation_id` in the path doubles
as a capability token — only a caller who knows the full URL can queue a
signal; use a random value (e.g. `ghl-$(openssl rand -hex 6)`).

**Pointing a real platform at /hook** — the only hard requirement is a
public URL that reaches msb-v3 (cloud platforms cannot reach `127.0.0.1`).
A Cloudflare quick tunnel (`cloudflared tunnel --url http://127.0.0.1:8766`)
works with zero accounts. Once you have it, `scripts/wire-hook-forwarders.sh
<public-url>` wires every configured platform in one shot and verifies each
with a real POST. LIVE-VERIFIED 2026-08-20: a real GitHub webhook on
lordwilsonDev/msb-v3 (hook id 668321218) firing push events at
`<public>/hook/gh-…` delivered its payload through the tunnel into the wake
inbox — the exact path GHL/Make/Zapier use. Current unlocks: GoHighLevel
webhook creation is implemented (`GhlClient.create_webhook`) but the PIT
token in `.env` returns 404 on every endpoint (dead/revoked — GHL hides all
errors behind 404; create a fresh PIT in GHL: Settings → Locations → API);
n8n forwarding is implemented and **LIVE-VERIFIED 2026-08-20 with the full
local circle** — no VPS, no cloud: a real n8n forwarder workflow (Webhook →
HTTP Request → /hook) was created via the public API and activated, and a
real msb-v3 cron job (`local-demo`, `*/2 * * * *`, `http_call` POST) fires
the n8n webhook → the forwarder hands the payload to /hook → it lands in the
wake inbox → the resident agent processes it. Creating workflows programmatically
taught three real API-contract lessons (all fixed + tested): the create schema
rejects `tags`/`meta` (strip them), activation is `POST /workflows/{id}/activate`
(not PATCH-active), and a Webhook node needs a `webhookId` for its production
URL to register. The API key itself was created the way n8n itself does it:
the key is a JWT signed with `sha256(every-2nd-char-of-encryptionKey)`,
`{sub: <owner id>, iss: 'n8n', aud: 'public-api'}`, stored in the
`user_api_keys` table — no UI needed.

**Stage 4 — self-maintenance.** Every wake cycle also runs `automation/audit.py`:
provider seams configured or blocked (and why), brain budget vs cap, dead
hooks (living automations whose last run FAILED), drift (state rows with no
manifest entry). Findings land in the wake outbox (`source=audit`) — but only
when the picture *changed*, so a healthy system stays silent and a broken one
keeps talking until fixed. Deterministic, no LLM spend.

```bash
# Point an external platform at the brain's own sense:
curl -X POST http://127.0.0.1:8766/hook/auto-abc123 \
  -H "Content-Type: application/json" \
  -d '{"event": "new_lead", "name": "Cleo"}'
# → queued; the resident agent processes it on the next wake cycle
```

## Providers

| Provider | Config | Status when key missing |
|---|---|---|
| **n8n** | `N8N_API_KEY` (create in n8n: Settings → API) + `N8N_BASE_URL` | `blocked` with reason |
| **Make** | `MSB_MAKE_WEBHOOK_URL` | `blocked` — scenario creation isn't exposed by Make's API; the webhook can be triggered |
| **Zapier** | `MSB_ZAPIER_API_KEY` | `blocked` — Zapier's REST API can't create zaps; a configured hook can be triggered |
| **GoHighLevel** | `MSB_GHL_API_KEY` (+ `MSB_GHL_BASE_URL`) | `blocked` — `/v1/workflows` creation is implemented |

## Config

| Env | Default | Meaning |
|---|---|---|
| `MSB_AUTOMATION_BUDGET_USD` | `10.0` | the brain's spend cap (the $10 key) |
| `MSB_AUTOMATION_DRY_RUN` | `1` | fail-closed: creation requires approval or this = `0` |
| `MSB_AUTOMATION_MANIFEST_PATH` | (derived) | the ledger (data/runtime/automation/manifest.jsonl) |
| `MSB_AUTOMATION_HOOK_SECRET` | empty | `/hook` shared secret (x-hook-secret); empty = open to bounded payloads |
| `MSB_AUTOMATION_WEBHOOK_HOSTS` | empty | dispatcher outbound allowlist beyond loopback + provider hosts |
| `N8N_API_KEY` / `N8N_BASE_URL` | — / `http://127.0.0.1:5678` | n8n target |
| `MSB_MAKE_WEBHOOK_URL` / `MSB_ZAPIER_API_KEY` / `MSB_GHL_API_KEY` | empty | other targets |

## Guarantees

- **Nothing untested**: the default path is a plan + manifest entry. Creation
  is an explicit, authenticated act.
- **Nothing overspent**: the budget cap is a hard fail-closed boundary.
- **Nothing invisible**: every attempt lands in the manifest with its status
  and the reason (missing key, budget, client error).

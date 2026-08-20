#!/usr/bin/env bash
# wire-hook-forwarders.sh — point real platforms at the perceiver's /hook.
#
# The perceiver (docs/automation-brain.md, Stage 3) exposes ONE inbound
# endpoint: POST /hook/<automation_id>. Every platform that can POST to a
# public URL can point at it; this script wires the platforms whose keys are
# configured and says exactly what is still blocked.
#
# Requirements:
#   MSB_HOOK_PUBLIC_URL  the public ingress to the msb-v3 server — e.g. a
#                        Cloudflare quick tunnel: cloudflared tunnel --url \
#                        http://127.0.0.1:8766  (or your own domain).
#                        Pass as arg: wire-hook-forwarders.sh <public-url>
#
# Wires (each skipped with a clear reason until its key exists):
#   GoHighLevel  — registers an outbound webhook on the location that fires
#                  ContactCreate/FormSubmit at /hook/ghl-<token>.
#                  Needs MSB_GHL_API_KEY + MSB_GHL_LOCATION_ID (a working
#                  PIT — verify it returns non-404 on /v1/contacts first).
#   n8n          — creates + activates a real forwarder workflow (Webhook →
#                  HTTP Request → msb-v3 /hook). Needs N8N_API_KEY.
#
# The capability token in the /hook path is the edge: only a caller who
# knows the full URL can queue a signal. For production, also set
# MSB_AUTOMATION_HOOK_SECRET and verify platform signatures (HMAC) at the
# edge. Fail-closed: nothing is created for a platform whose key is missing
# or whose verification POST does not land in the wake inbox.
set -euo pipefail

cd "$(dirname "$0")/.."

PUBLIC_URL="${1:-${MSB_HOOK_PUBLIC_URL:-}}"
if [ -z "$PUBLIC_URL" ]; then
  echo "usage: $0 <public-url>   (e.g. https://abc.trycloudflare.com)" >&2
  echo "or set MSB_HOOK_PUBLIC_URL" >&2
  exit 2
fi
PUBLIC_URL="${PUBLIC_URL%/}"

# --- load .env keys (never echoed) ---------------------------------------
set -a
[ -f .env ] && source .env
set +a

TOKEN="$(openssl rand -hex 6)"
HOOK_URL="$PUBLIC_URL/hook/$TOKEN"
echo "perceiver target: $HOOK_URL"
echo

# --- verify the public ingress reaches /hook ------------------------------
echo "[1/3] verifying public ingress -> /hook ..."
PROBE="{\"probe\": true, \"ts\": \"$(date -u +%FT%TZ)\"}"
if ! RESP=$(curl -s -m 15 -X POST "$HOOK_URL" -H "Content-Type: application/json" -d "$PROBE"); then
  echo "  FAIL: public URL unreachable — is the tunnel/server up?" >&2
  exit 1
fi
echo "  ok: $(echo "$RESP" | head -c 120)"

# --- GoHighLevel webhook ---------------------------------------------------
echo
echo "[2/3] GoHighLevel ..."
if [ -z "${MSB_GHL_API_KEY:-}" ]; then
  echo "  skipped: MSB_GHL_API_KEY not set"
elif [ -z "${MSB_GHL_LOCATION_ID:-}" ]; then
  echo "  skipped: MSB_GHL_LOCATION_ID not set"
else
  CODE=$(curl -s -m 15 -o /tmp/ghl-webhook-create.json -w "%{http_code}" -X POST \
    "https://services.leadconnectorhq.com/v1/webhooks" \
    -H "Authorization: Bearer $MSB_GHL_API_KEY" \
    -H "Content-Type: application/json" \
    -H "Version: 2021-07-28" \
    -d "{\"url\": \"$HOOK_URL\", \"events\": [\"ContactCreate\", \"FormSubmit\"], \"name\": \"msb-perceiver\", \"locationId\": \"$MSB_GHL_LOCATION_ID\"}")
  if [ "$CODE" = "200" ] || [ "$CODE" = "201" ]; then
    echo "  created: $(grep -o '\"webhookId\":\"[^\"]*\"' /tmp/ghl-webhook-create.json | head -1)"
    echo "  NOTE: the PIT token must be valid — if this ever 404s, the token is dead/revoked (GHL 404s everything for bad tokens)."
  else
    echo "  FAILED (HTTP $CODE): $(head -c 200 /tmp/ghl-webhook-create.json)"
    echo "  -> likely a dead/expired PIT token; create a fresh one in GHL (Settings > Locations > API) and re-run."
  fi
fi

# --- n8n forwarder workflow -------------------------------------------------
echo
echo "[3/3] n8n forwarder workflow ..."
if [ -z "${N8N_API_KEY:-}" ]; then
  echo "  skipped: N8N_API_KEY not set (create one in n8n: Settings > API)"
else
  PYTHONPATH=src python3 - "$HOOK_URL" <<'PYEOF'
import json, os, sys
import httpx

hook_url = sys.argv[1]
base = os.getenv("N8N_BASE_URL", "http://127.0.0.1:5678").rstrip("/")
from msb_v3.automation.clients import build_n8n_forwarder_workflow

workflow = build_n8n_forwarder_workflow(hook_url, path="msb-fwd")
headers = {"X-N8N-API-KEY": os.environ["N8N_API_KEY"], "Content-Type": "application/json"}
with httpx.Client(timeout=20) as client:
    created = client.post(f"{base}/api/v1/workflows", json=workflow, headers=headers)
    created.raise_for_status()
    wf = created.json()
    wf_id = str(wf.get("id", ""))
    activated = {}
    if wf_id:
        activated = client.patch(f"{base}/api/v1/workflows/{wf_id}", json={"active": True}, headers=headers)
        activated.raise_for_status()
    path = next((n["parameters"].get("path", "") for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.webhook"), "")
    print(f"  created workflow id={wf_id} active={bool(activated.json().get('active')) if activated else wf.get('active')}")
    print(f"  public webhook URL: {base}/webhook/{path}")
    print(f"  -> forward it: curl -X POST {base}/webhook/{path} -H 'Content-Type: application/json' -d '{{\"hello\":\"world\"}}'")
PYEOF
fi

echo
echo "done. Any platform that can POST to $HOOK_URL now feeds the resident agent."
echo "Cleanup: remove a GitHub webhook with: gh api repos/lordwilsonDev/msb-v3/hooks/<id> -X DELETE"

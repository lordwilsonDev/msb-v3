# ADR: Vesta WireGuard Deployment — Preflight Record

**Date:** 2026-08-14 · **Status:** PREFLIGHT RECORDED + staged — tools
installed, keypair generated, configs prepared; deployment NOT executed
end-to-end · **Required by:** `docs/operations/vesta-wireguard.md`
("Record the selected interface address and CIDR in an ADR before enabling
admission") and the `2026-08-13-vesta-node-full-build-plan.md` §7 gate
("deploy and interface-bind WireGuard; prove public-interface denial and
signed-session admission").

## 1. Decision being recorded

Before `MSB_VESTA_REQUIRE_TUNNEL=1` can be enabled, the private overlay must
exist, the gateway must be bound to it, and public-interface denial must be
proven. This document records the machine facts gathered on 2026-08-14 and
the operator steps that remain. **No network interface, firewall rule, or
`.env` value was changed by the application build.**

## 2. Gathered facts (2026-08-14)

| Fact | Value | Implication |
|---|---|---|
| LAN address / interface | `192.168.50.216/24` on `en1` | Phone on home Wi-Fi can reach the Mac directly at this address |
| Default gateway | `192.168.50.1` | Standard home router; port-forwarding lives there |
| macOS application firewall | **Enabled** | Good baseline; tunnel UDP must be allowed deliberately |
| Public IP (outbound) | `75.87.198.110` | Residential ISP; almost certainly **CGNAT** — inbound WAN WireGuard needs port forwarding or a relay; do not assume it works |
| Active tunnel interfaces | `utun0–utun3`, **no assigned addresses**, no Tailscale/VPN apps installed | No active VPN CIDR collisions; verify again before choosing the tunnel CIDR |
| WireGuard installed? | **No** (`wg`/`wg-quick` absent, no app bundle, no configs) | Installation is a prerequisite |
| Vesta/Node state | SQLite state exists at `data/vesta/`, `data/node/`; sandbox root `runtime/node-sandbox/` empty | Loopback-only operation today; nothing exposed |

## 3. Proposed tunnel parameters (confirm before use)

- **Tunnel CIDR:** `10.77.0.0/29` (Mac `10.77.0.1`, iPhone `10.77.0.2`,
  Android `10.77.0.3`; up to 3 further peers). Widened from the original
  `/30` on 2026-08-14 when a second owner device (Android) was added —
  the /30 could only hold the Mac + one peer. Avoids the LAN
  `192.168.50.0/24`, the Tailscale CGNAT range `100.64.0.0/10`, and
  common `10.0.0.0/8` LAN defaults. Re-verify no overlap with the
  router's own VPN/DHCP pools.
- **Listener:** `0.0.0.0:51820` UDP on the Mac, then restrict by firewall to
  the LAN (and WAN only if port forwarding is proven).
- **AllowedIPs (Mac peer):** one `/32` per enrolled device
  (`10.77.0.2/32` iPhone, `10.77.0.3/32` Android) — the tunnel carries
  Vesta, not general routing.
- **Application contract once deployed and verified:**
  `MSB_VESTA_REQUIRE_TUNNEL=1`, `MSB_VESTA_ALLOWED_CIDRS=10.77.0.0/29,127.0.0.1/32,::1/128`.

## 4. Remaining operator steps (not executable from this review)

1. **Install** WireGuard on the Mac (Homebrew `wireguard-tools` + the macOS
   app, or the App Store app) and on the iPhone (App Store).
2. **Generate keypairs on each device**; private keys never enter this repo,
   `.env`, logs, or audit payloads.
3. **Create the peer configs** per §3; bring up the tunnel; verify
   `ping 10.77.0.2` from the Mac and reverse from the phone.
4. **Bind the gateway** to the tunnel/loopback exposure in the supervised
   start configuration (never a public wildcard without a firewall rule
   proving the same boundary).
5. **Set the application contract** (§3), restart the supervised service,
   and verify `/vesta/status` reports `transport_required: true`.
6. **Run the full verification checklist** from
   `docs/operations/vesta-wireguard.md` (loopback status, iPhone status over
   the tunnel, unapproved-interface denial, unauthenticated `/vesta/chat`
   fails closed, non-allowed peer 403, replay/expiry/revocation failures,
   no private key in logs).
7. **WAN decision:** if remote (off-LAN) iPhone access is required, first
   prove the ISP provides a non-CGNAT public IP or deploy a relay; LAN-only
   operation needs nothing more than the home router.
8. **Rollback path:** documented in `vesta-wireguard.md`; keep tunnel
   admission enabled unless a maintenance incident is explicitly recorded.

## 5. Execution record (2026-08-14)

**Completed:**
- `wireguard-tools` installed (brew); WireGuard macOS app installed and
  running (App Store `id1451685025`).
- Mac keypair generated; private key at `~/wireguard/vesta-mac.privkey`
  (0600, outside the repo); public key `cMdMA6USLTrXXkoU6IO2l6UhgaOMblyZfmqSbVkHlXY=`.
- Tunnel parameters from §3 confirmed usable (originally `10.77.0.0/30`,
  Mac `10.77.0.1`, iPhone `10.77.0.2`; widened to `10.77.0.0/29` later the
  same day when Android `10.77.0.3` was added), UDP 51820.
- Config templates written (`~/wireguard/vesta-mac.conf` with
  `IPHONE_PUBLIC_KEY` + `ANDROID_PUBLIC_KEY` placeholders,
  `vesta-iphone.conf`, `vesta-android.conf` — the latter two added with
  the second-device decision 2026-08-14), plus a loopback stack-proof pair
  at `/tmp/wgtest/` and an operator checklist (`~/wireguard/NEXT-STEPS.md`).
- Env chain verified: `scripts/run.sh` sources `msb-v3/.env` and exports
  `${VAR:-default}` after, so the two Vesta vars go in `.env`; restart is
  `launchctl kickstart -k gui/$(id -u)/com.lordwilson.msb-v3`.
- Reachability investigation: public IP `75.87.198.110` is Spectrum
  residential (`syn-075-087-198-110.res.spectrum.com`), NOT RFC 6598 CGNAT;
  router WAN-IP check at `http://192.168.50.1` still pending.

**Pending (owner devices):** iPhone + Android tunnel creation + public keys
→ fill Mac config → bring up tunnel → firewall allow → `.env`
(`MSB_VESTA_REQUIRE_TUNNEL=1`, allowed CIDRs) → service restart →
`/vesta/status` `transport_required: true` → runbook checklist. Then the
remote-access decision (port forward + DDNS vs relay) per the router check.
Remote access is DEFERRED (LAN-only) per operator decision 2026-08-14.

## 6. After deployment

- Re-run `docs/operations/vesta-security-review.md` F4 deployment items;
  F1–F3 are already addressed (2026-08-14). Only then consider
  shell-allowlist expansion or browser/application agency.
- The next capability beyond the current `echo`/`pwd` + `FILE_READ`/
  `FILE_WRITE` slice requires the full policy → scope → contract → budget →
  verification → rollback → audit → adversarial-test matrix and a security
  review of each named executable.

# Vesta WireGuard Operations Runbook

**Status:** Staged — tools installed, keypair generated, configs prepared (2026-08-14).
Completion awaits the owner iPhone + router WAN check. See §Execution record.
**Purpose:** Put Vesta ingress on a private tunnel before enabling tunnel-only admission

## Topology

```text
iPhone ─┐
        ├─ WireGuard tunnel (10.77.0.0/29)
Android ─┘
  │
  ▼
Mac Mini wg0 / 10.77.0.1
  │
  ├── Vesta gateway
  └── MSB v3 / Ollama / SQLite
```

The raw MSB executor must not be exposed to the public internet. `MSB_OPERATOR_TOKEN` remains maintenance authentication; it is not a replacement for the device session protocol.

## Preflight

- Confirm the Mac Mini has a stable private/LAN address.
- Confirm the enrolled owner devices (V1 owner + any additional enrolled
  devices, e.g. iPhone + Android).
- Choose a private tunnel CIDR that does not overlap the local LAN or VPNs.
- Generate keys on each endpoint; private keys never enter this repository, `.env`, logs, or audit payloads.
- Confirm the Mac firewall policy and router do not expose the Vesta/MSB port publicly.
- Record the selected interface address and CIDR in an ADR before enabling admission.

## Deployment sequence

1. Install the platform WireGuard implementation on the Mac and iPhone.
2. Generate a Mac keypair and an iPhone keypair on their respective devices.
3. Create a minimal peer configuration with:
   - a dedicated tunnel address for each peer;
   - `AllowedIPs` limited to the Vesta tunnel address;
   - a narrowly scoped UDP listener on the Mac;
   - persistent keepalive only where mobile NAT requires it.
4. Bring up the tunnel and verify both peers can reach only the intended private address.
5. Bind the gateway to the tunnel/loopback exposure according to the host supervision configuration. Do not use a public wildcard without a firewall rule that proves the same boundary.
6. Set the application contract:

```dotenv
MSB_VESTA_REQUIRE_TUNNEL=1
MSB_VESTA_ALLOWED_CIDRS=<wireguard-peer-cidr>,127.0.0.1/32,::1/128
```

7. Restart the supervised MSB service.
8. Verify Vesta status reports `transport_required: true`.

## Verification checklist

- [ ] Vesta status works from the Mac loopback.
- [ ] Vesta status works from the enrolled iPhone over WireGuard.
- [ ] The Vesta gateway is not reachable from an unapproved interface.
- [ ] `/vesta/chat` without operator/device authentication fails closed.
- [ ] `/vesta/chat` from a non-allowed peer fails with 403.
- [ ] A valid enrolled device can complete challenge/session authentication.
- [ ] Replayed, expired, and revoked signed requests fail.
- [ ] Native `/chat` is not treated as the remote control surface.
- [ ] No private key appears in process arguments, logs, `.env`, or audit records.
- [ ] The rollback procedure restores the previous local-only binding and sets `MSB_VESTA_REQUIRE_TUNNEL=0` only during an explicitly recorded maintenance incident.

## Rollback

1. Kill or stop the controlled service if the boundary is uncertain.
2. Disable the WireGuard peer/interface.
3. Restore the last known-good supervised configuration.
4. Keep Vesta transport admission enabled unless the operator explicitly records a local-only maintenance decision.
5. Verify the ledger and audit the incident before resuming mutations.

## Execution record (2026-08-14)

**LIVE — Android enrolled, tunnel up, Vesta contract applied (2026-08-14):**
- Android enrolled via `~/wireguard/qr-device.sh android` (scan-to-import, keypair at
  `~/wireguard/qr/vesta-android.conf`, 0600). Android pubkey `R2gn2o7grT4stbWjHjpECqr5vW/TTVTsT80BysqNImI=`
  filled into `vesta-mac.conf`.
- `sudo ~/wireguard/activate-vesta.sh -a <key>` → `utun4` up (10.77.0.1:51820);
  `ping 10.77.0.3` verified 3/3 (12-14ms); handshake + bidirectional traffic confirmed.
- `msb-v3/.env` updated: `MSB_VESTA_REQUIRE_TUNNEL=1`, `MSB_VESTA_ALLOWED_CIDRS=10.77.0.0/29,127.0.0.1/32,::1/128`;
  service restarted (launchd `com.lordwilson.msb-v3`); `/vesta/status` reports `transport_required: true`.
- Admission matrix verified from settings: tunnel peers + loopback ALLOW; LAN/internet/None → DENY (403).
- iPhone peer still pending (placeholder in `vesta-mac.conf`); remote/port-forward deferred (LAN-only).

**Deployed (staged):**
- `wireguard-tools` installed via Homebrew (`wg`, `wg-quick`, `wireguard-go`);
  WireGuard macOS app installed (App Store `id1451685025`, running).
- Mac keypair generated; private key at `~/wireguard/vesta-mac.privkey` (0600,
  outside the repo). Mac public key:
  `cMdMA6USLTrXXkoU6IO2l6UhgaOMblyZfmqSbVkHlXY=`.
- Tunnel parameters fixed (ADR `2026-08-14-wireguard-preflight-adr.md`,
  widened to /29 for the second enrolled device): `10.77.0.0/29`, Mac
  `10.77.0.1`, iPhone `10.77.0.2`, Android `10.77.0.3`, UDP `51820`, LAN
  endpoint `192.168.50.216:51820`.
- Config templates staged: `~/wireguard/vesta-mac.conf` (awaits the iPhone
  + Android public keys), `~/wireguard/vesta-iphone.conf`, and
  `~/wireguard/vesta-android.conf` (added 2026-08-14). `NEXT-STEPS.md`
  beside them is the operator checklist.
- Loopback stack-proof staged at `/tmp/wgtest/wgtest-{a,b}.conf` (two peers
  on the Mac over 127.0.0.1, ports 51821/51822) to prove the stack without a
  phone: `sudo wg-quick up /tmp/wgtest/wgtest-{a,b}.conf`, `ping 10.77.0.2`,
  `sudo wg show`, then `sudo wg-quick down ...`.
- Env chain verified: the supervised service runs `scripts/run.sh`, which
  sources the repo `.env` (`set -a`) before exporting `${VAR:-default}`
  defaults — so the two Vesta vars below belong in `msb-v3/.env` and override
  the shipped defaults. Restart: `launchctl kickstart -k gui/$(id -u)/com.lordwilson.msb-v3`.

**Pending (owner devices):**
1. iPhone: create tunnel from scratch (Address `10.77.0.2/32`, peer = Mac
   public key above, Endpoint `192.168.50.216:51820`, `PersistentKeepalive 25`);
   report the iPhone public key.
2. Android: same, Address `10.77.0.3/32`; report the Android public key.
3. Fill both keys into `~/wireguard/vesta-mac.conf`, import into the Mac
   app, activate, and verify `ping 10.77.0.2` and `ping 10.77.0.3` from the
   Mac.
4. macOS firewall is enabled — on first activation the WireGuard app prompts
   to allow inbound UDP 51820; confirm it, then verify the port is reachable.
5. Add to `msb-v3/.env` and restart the service:
   ```dotenv
   MSB_VESTA_REQUIRE_TUNNEL=1
   MSB_VESTA_ALLOWED_CIDRS=10.77.0.0/29,127.0.0.1/32,::1/128
   ```
6. Verify `/vesta/status` reports `transport_required: true` and run the
   checklist below (loopback still works, unapproved-interface denial 403,
   replay/expiry/revocation failures).
7. Remote (anywhere): DEFERRED 2026-08-14 — LAN-only for now. When wanted:
   public IP `75.87.198.110` is Spectrum residential
   (`syn-075-087-198-110.res.spectrum.com`), NOT RFC 6598 CGNAT. Confirm the
   router WAN IP at `http://192.168.50.1`. If it matches, port-forward UDP
   51820 → `192.168.50.216` and pair the dynamic endpoint with DDNS. If it is
   private/`100.64.x`, the ISP is CGNAT and a relay (Tailscale or a VPS) is
   required — that decision needs an ADR update.

## Important limitation

This runbook does not claim WireGuard is deployed end-to-end. The iPhone
peer key and tunnel bring-up, firewall confirmation, and router WAN check
remain pending on the owner device.

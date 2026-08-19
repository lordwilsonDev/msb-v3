# Ops audit reports

Weekly ops-audit reports land here as `YYYY-MM-DD_audit.md` — the
self-publishing evidence trail (see `docs/ops-runbook.md`). Each report is
committed (signed + DCO) and pushed to origin by
`scripts/publish-audit.sh` when `MSB_PUBLISH_AUDIT=1` (the
`com.lordwilson.ops-audit` agent sets it).

The dir is also rsynced off-machine by `scripts/heartbeat.sh` when a
heartbeat volume is configured.

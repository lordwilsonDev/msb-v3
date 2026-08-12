"""One live smoke run of the slice — observe real behavior before the 20/20 gate."""

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from msb_v3.agent.handle import handle  # noqa: E402
from msb_v3.agent.intent import interpret_intent  # noqa: E402
from msb_v3.agent.planner import plan  # noqa: E402
from msb_v3.local_ai.client_factory import get_client  # noqa: E402


async def main() -> None:
    request = "Research the vault and write a client brief about the sovereign agentic runtime."
    tenant = sys.argv[1] if len(sys.argv) > 1 else "wilson-vault"
    out = Path(tempfile.mkdtemp(prefix="dbb-smoke-"))

    client = get_client()
    print(f"model: {client.model}")

    intent = interpret_intent(request, client=client)
    print(f"intent source={intent.source} permissions={list(intent.permissions)} goals={list(intent.goals)[:2]}")

    graph = plan(intent, client=client)
    print(f"plan source={graph.source} tasks={[t.task_id for t in graph.tasks]}")

    t0 = time.perf_counter()
    result = await handle(
        request,
        client=client,
        tenant=tenant,
        approve=True,
        output_dir=out,
        session="smoke",
    )
    elapsed = round(time.perf_counter() - t0, 2)
    print(f"ok={result.ok} verdict={result.verdict} run={result.run_id} elapsed={elapsed}s")
    print(f"hash={result.deterministic_hash}")
    print(f"error={result.error}")
    print(f"outcome={json.dumps(result.trace.get('outcome', {}), indent=2)}")
    for t in result.trace.get("tasks", []):
        print(f"task {t['task_id']}: method={t['verification_method']}")

    # cleanup the live tenant collection — NEVER delete a real tenant. Only
    # live_test_* collections are ever auto-removed; a real vault tenant
    # (e.g. wilson-vault) is left untouched (lesson: an unguarded cleanup
    # once deleted the real wilson-vault collection mid-session).
    if tenant.startswith("live_test_"):
        try:
            from msb_v3.api.rag import delete_tenant_collection

            delete_tenant_collection(tenant)
        except Exception as exc:  # noqa: BLE001
            print(f"cleanup: {exc}")
    else:
        print(f"cleanup: skipped — {tenant} is not a live_test_* tenant")


if __name__ == "__main__":
    asyncio.run(main())

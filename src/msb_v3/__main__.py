"""msb-v3 entrypoint — uvicorn launcher.

`python -m msb_v3` (or the `msb-v3` console script wired in pyproject.toml)
boots the FastAPI app built by `msb_v3.api.app.create_app` and binds
to the host/port from `msb_v3.core.config.settings` (env: MSB_HOST,
MSB_PORT, MSB_RELOAD). Tiny on purpose: anything that runs every
boot should live in `core/config.py` or `api/app.py`, not here.
"""

import uvicorn

from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.observability.metrics import Metrics

app = create_app()


def _check_source_license() -> None:
    """Fail-closed source-license gate.

    Single source of truth is scripts/verify-license.sh, which verifies the
    license at ~/.msb-v3/source-license against the committed
    config/license-authorized-keys (owner-signed, SSH signatures). This
    applies to every start path — run.sh, `make server`, `python -m
    msb_v3`, and the console script — so a bare pull (anonymous clone or
    API tarball) is inert code until a license is obtained.
    """
    import os
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    verify = repo / "scripts" / "verify-license.sh"
    if not verify.is_file():
        raise SystemExit(
            "ERROR: source-license gate missing (scripts/verify-license.sh) — "
            "this code runs only under a license signed by the owner; fork the "
            "repo and request one: bash scripts/request-access.sh"
        )
    proc = subprocess.run(
        ["bash", str(verify)],
        capture_output=True,
        text=True,
        env=dict(os.environ),
    )
    if proc.returncode != 0:
        detail = (proc.stdout or proc.stderr or "").strip()
        raise SystemExit(
            "ERROR: no valid source license — this code runs only under a "
            "license signed by the owner. Fork the repo and request one: "
            "bash scripts/request-access.sh"
            + (("\n" + detail) if detail else "")
        )
    print(f"[msb-v3] {proc.stdout.strip()}")


def run() -> None:
    prefix = "[msb-v3]"
    _check_source_license()
    print(f"{prefix} starting host={settings.host} port={settings.port} model={settings.ollama_model}")
    Metrics.set_ready(True)
    try:
        # access_log=False: the per-request access log is 99% /metrics poll
        # noise (trinity-dashboard polls every 60s) and previously ballooned
        # gateway.out.log unbounded; real events live in the audit stream.
        uvicorn.run(
            "msb_v3.__main__:app",
            host=settings.host,
            port=settings.port,
            reload=settings.reload,
            access_log=False,
        )
    finally:
        Metrics.set_ready(False)
        print(f"{prefix} stopped")


if __name__ == "__main__":
    run()

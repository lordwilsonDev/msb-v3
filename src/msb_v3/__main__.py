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


def run() -> None:
    prefix = "[msb-v3]"
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

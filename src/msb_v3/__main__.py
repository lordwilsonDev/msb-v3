
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
        uvicorn.run("msb_v3.__main__:app", host=settings.host, port=settings.port, reload=settings.reload)
    finally:
        Metrics.set_ready(False)
        print(f"{prefix} stopped")


if __name__ == "__main__":
    run()

import uvicorn

from msb_v3.api.app import create_app

app = create_app()


def run() -> None:
    import os

    host = os.getenv("MSB_HOST", "127.0.0.1")
    port = int(os.getenv("MSB_PORT", "8766"))
    reload = os.getenv("MSB_RELOAD", "0") == "1"
    uvicorn.run("msb_v3.__main__:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    run()

"""Run the standalone workbench server."""

from aiohttp import web

from .app import create_app
from .config import Settings


def main() -> None:
    settings = Settings.from_env()
    web.run_app(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()

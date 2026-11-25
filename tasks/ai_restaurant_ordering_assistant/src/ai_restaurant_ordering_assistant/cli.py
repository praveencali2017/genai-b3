"""Console entry points.

- `restaurant-api`: serve the API and web UI with uvicorn.
"""

import argparse
from collections.abc import Sequence

from ai_restaurant_ordering_assistant.config.logging import configure_logging
from ai_restaurant_ordering_assistant.config.settings import get_settings

API_APP = "ai_restaurant_ordering_assistant.main:app"


def run_api(argv: Sequence[str] | None = None) -> None:
    """Serve the app; host/port default to API_HOST/API_PORT."""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="restaurant-api",
        description="Run the restaurant ordering API and web UI.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default=settings.api_host, help="bind address")
    parser.add_argument("--port", type=int, default=settings.api_port, help="bind port")
    parser.add_argument(
        "--reload", action="store_true", help="restart on code changes (development)"
    )
    args = parser.parse_args(argv)

    import uvicorn  # deferred: `--help` shouldn't pay for importing the server

    configure_logging(settings.log_level)
    uvicorn.run(
        API_APP,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
    )

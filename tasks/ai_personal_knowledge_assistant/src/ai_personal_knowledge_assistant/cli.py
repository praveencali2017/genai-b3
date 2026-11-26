"""Console entry points: `pka-api` (FastAPI server) and `pka-ui` (Streamlit UI).

Each launcher only starts a process; the app and the UI live in `api/` and
`frontend/`, and are referenced by import string / file path so this module
stays importable before they exist.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ai_personal_knowledge_assistant.core.logging import configure_logging
from ai_personal_knowledge_assistant.core.settings import get_settings

API_APP = "ai_personal_knowledge_assistant.api.main:app"
STREAMLIT_APP = Path(__file__).resolve().parent / "frontend" / "app.py"


def run_api(argv: Sequence[str] | None = None) -> None:
    """Serve the FastAPI app with uvicorn; host/port default to API_HOST/API_PORT."""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="pka-api",
        description="Run the knowledge assistant HTTP API.",
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


def run_ui(argv: Sequence[str] | None = None) -> None:
    """Run the Streamlit UI; extra arguments are passed through to `streamlit run`."""
    if not STREAMLIT_APP.is_file():
        sys.exit(f"pka-ui: Streamlit app not found at {STREAMLIT_APP}")

    from streamlit.web import cli as streamlit_cli

    extra_args = list(sys.argv[1:] if argv is None else argv)
    streamlit_cli.main(
        args=["run", str(STREAMLIT_APP), *extra_args], prog_name="streamlit"
    )

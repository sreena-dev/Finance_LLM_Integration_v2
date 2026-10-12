"""Gateway entry point with the live run trace attached.

    cd backend
    python -m uvicorn live_trace.asgi:app --reload --port 12101

This imports the gateway exactly as `app.main:app` defines it and then attaches the trace. Start the
gateway with `app.main:app` instead and everything runs as before, with no trace.
"""

from app.main import app  # noqa: E402  (importing the gateway loads .env and every mode)
from live_trace.instrument import install  # noqa: E402

install(app)

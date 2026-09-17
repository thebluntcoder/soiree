"""
tests/unit/test_error_handling.py — the CORS-safe global exception handler.

Uses FastAPI's TestClient against the real app — a deliberate exception to
this repo's "call endpoint functions directly" convention (see CLAUDE.md),
because the one thing worth proving here — that an unhandled exception's
response still carries CORS headers — is a property of the *middleware
stack's construction order*, which only exists when the real ASGI app is
exercised end to end. Calling the middleware function directly would prove
nothing about where it sits relative to CORSMiddleware.

Background: a handler registered via @app.exception_handler(Exception) does
NOT work for this — Starlette special-cases the bare Exception/500 handler
into ServerErrorMiddleware, which sits OUTSIDE every app.add_middleware()
middleware (CORS included). Its response never passes back through CORS,
so the browser reports a confusing "blocked by CORS policy" error that has
nothing to do with CORS. Verified empirically while fixing a real production
bug (a /search/ 500 was showing as a CORS block in the browser console).
The fix is a plain try/except inside BaseHTTPMiddleware, registered BEFORE
CORSMiddleware so CORS ends up outermost and wraps it.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import app.main as main_mod

    # A route that always raises — not a real endpoint, exists purely to
    # exercise the catch-all middleware in this test module.
    if not any(r.path == "/__test_crash" for r in main_mod.app.routes):
        @main_mod.app.get("/__test_crash")
        async def _crash():
            raise ValueError("deliberate test crash")

    return TestClient(main_mod.app, raise_server_exceptions=False)


class TestUnhandledExceptionIsCorsSafe:
    def test_allowed_origin_gets_cors_header_on_500(self, client):
        r = client.get("/__test_crash", headers={"Origin": "http://localhost:3000"})
        assert r.status_code == 500
        assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_response_body_is_clean_no_internal_detail_leaked(self, client):
        r = client.get("/__test_crash", headers={"Origin": "http://localhost:3000"})
        assert r.json() == {"detail": "Something went wrong on our end. Please try again."}
        assert "deliberate test crash" not in r.text
        assert "Traceback" not in r.text

    def test_disallowed_origin_still_gets_no_cors_header(self, client):
        """The fix makes errors CORS-*correct*, not CORS-*permissive* —
        an origin CORSMiddleware wouldn't allow on a success response
        still doesn't get the header on a 500 either."""
        r = client.get("/__test_crash", headers={"Origin": "https://not-allowed.example"})
        assert r.headers.get("access-control-allow-origin") is None

    def test_working_endpoint_unaffected(self, client):
        r = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_sentry_capture_called_when_dsn_configured(monkeypatch):
    """SENTRY_DSN unset (the default) is exercised implicitly by every
    other test in this file never needing a real Sentry project. This
    proves the capture call actually fires when it IS configured —
    without a real network call, matching this repo's no-op-by-default
    testing convention for PostHog (see test_analytics.py)."""
    import app.main as main_mod
    from app.core import config

    monkeypatch.setattr(config.settings, "SENTRY_DSN", "https://fake@sentry.example/1")

    captured = []
    import sentry_sdk
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc: captured.append(exc))

    if not any(r.path == "/__test_crash" for r in main_mod.app.routes):
        @main_mod.app.get("/__test_crash")
        async def _crash():
            raise ValueError("deliberate test crash")

    client = TestClient(main_mod.app, raise_server_exceptions=False)
    client.get("/__test_crash", headers={"Origin": "http://localhost:3000"})

    assert len(captured) == 1
    assert isinstance(captured[0], ValueError)

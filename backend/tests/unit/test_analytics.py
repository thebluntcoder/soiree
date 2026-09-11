"""
tests/unit/test_analytics.py — the PostHog wrapper.

Two things matter: it's a true no-op with no API key (no client, no
import cost), and every public function swallows its own errors so a
PostHog outage can never break a real request.
"""

import pytest

from app.services import analytics


class _FakeClient:
    def __init__(self):
        self.captured: list[tuple] = []
        self.aliased: list[tuple] = []
        self.shut_down = False
        self.raise_on_capture = False

    def capture(self, event, distinct_id, properties=None):
        if self.raise_on_capture:
            raise RuntimeError("posthog is down")
        self.captured.append((event, distinct_id, properties))

    def alias(self, previous_id, distinct_id):
        self.aliased.append((previous_id, distinct_id))

    def shutdown(self):
        self.shut_down = True


class TestDisabled:
    """No POSTHOG_API_KEY (the default) — every call is a pure no-op."""

    def test_capture_is_noop(self, monkeypatch):
        monkeypatch.setattr(analytics, "_ENABLED", False)
        monkeypatch.setattr(analytics, "_client", None)
        analytics.capture("user-1", "plan_generated", {"a": 1})
        assert analytics._client is None  # never constructed

    def test_alias_is_noop(self, monkeypatch):
        monkeypatch.setattr(analytics, "_ENABLED", False)
        monkeypatch.setattr(analytics, "_client", None)
        analytics.alias("anon", "user-1")
        assert analytics._client is None

    def test_shutdown_is_noop(self, monkeypatch):
        monkeypatch.setattr(analytics, "_client", None)
        analytics.shutdown()  # must not raise even with nothing to flush


class TestEnabled:
    """POSTHOG_API_KEY set — wraps a (fake, here) Posthog client."""

    @pytest.fixture
    def fake(self, monkeypatch):
        client = _FakeClient()
        monkeypatch.setattr(analytics, "_ENABLED", True)
        monkeypatch.setattr(analytics, "_get_client", lambda: client)
        return client

    def test_capture_forwards_event_and_properties(self, fake):
        analytics.capture("user-1", "plan_generated", {"budget": 3000})
        assert fake.captured == [("plan_generated", "user-1", {"budget": 3000})]

    def test_capture_without_distinct_id_is_skipped(self, fake):
        analytics.capture(None, "plan_generated", {})
        analytics.capture("", "plan_generated", {})
        assert fake.captured == []

    def test_capture_failure_is_swallowed(self, fake):
        fake.raise_on_capture = True
        analytics.capture("user-1", "plan_generated")  # must not raise

    def test_alias_forwards(self, fake):
        analytics.alias("anon-state-1", "user-1")
        assert fake.aliased == [("anon-state-1", "user-1")]

    def test_alias_skips_when_ids_match_or_missing(self, fake):
        analytics.alias("user-1", "user-1")  # already the same identity
        analytics.alias(None, "user-1")
        analytics.alias("anon", None)
        assert fake.aliased == []

    def test_shutdown_flushes_the_client(self, monkeypatch, fake):
        monkeypatch.setattr(analytics, "_client", fake)
        analytics.shutdown()
        assert fake.shut_down is True

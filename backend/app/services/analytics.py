"""
services/analytics.py — thin PostHog wrapper, a no-op without an API key.

CONCEPT: opt-in by env var, not by code path
------------------------------------------------
`settings.POSTHOG_API_KEY` unset (the default) → every function here is a
no-op — no client constructed, no network call, no background thread. Set
it (and optionally `POSTHOG_HOST`) on Railway and analytics turns on with
no code change or redeploy of logic.

WHAT WE SEND
------------
A small, deliberately non-PII product funnel — event names and counters,
never event notes/dietary details/free text the user typed:

  swiggy_auth_started / _completed / _failed   (see api/v1/endpoints/auth.py)
  search                                        (api/v1/endpoints/search.py)
  plan_generated                                (api/v1/endpoints/plans.py)
  plan_refined, chat_message                    (api/v1/endpoints/plans.py)

`distinct_id` is the Soirée `user.id`. Before login (the OAuth `state` is
all we have), events use that as a temporary anonymous id and `alias()`
merges it into the real user once /auth/callback resolves one — the
standard PostHog pattern for identifying a user partway through a funnel.

Every public function here swallows its own errors: a PostHog outage must
never affect a real request. Call sites don't need their own try/except.
"""

import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_ENABLED = bool(settings.POSTHOG_API_KEY)
_client = None


def _get_client():
    """Lazy singleton — None (and nothing else touched) when disabled."""
    global _client
    if not _ENABLED:
        return None
    if _client is None:
        from posthog import Posthog  # deferred: no import cost when disabled

        _client = Posthog(
            project_api_key=settings.POSTHOG_API_KEY,
            host=settings.POSTHOG_HOST or None,
            disable_geoip=True,  # no IP-based location enrichment
            enable_local_evaluation=False,  # we don't use feature flags
        )
    return _client


def capture(distinct_id: str | None, event: str, properties: dict[str, Any] | None = None) -> None:
    """Fire-and-forget event capture. No-op when disabled or `distinct_id` is empty."""
    client = _get_client()
    if client is None or not distinct_id:
        return
    try:
        client.capture(event=event, distinct_id=distinct_id, properties=properties or {})
    except Exception as e:  # noqa: BLE001
        logger.warning("posthog capture(%s) failed: %s", event, e)


def alias(previous_id: str | None, distinct_id: str | None) -> None:
    """
    Merge an anonymous pre-login id (the OAuth `state`) into the real user
    once known, so the sign-in funnel counts as one person, not two.
    """
    client = _get_client()
    if client is None or not previous_id or not distinct_id or previous_id == distinct_id:
        return
    try:
        client.alias(previous_id=previous_id, distinct_id=distinct_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("posthog alias failed: %s", e)


def shutdown() -> None:
    """Flush any queued events before the process exits. Called from main.py's lifespan."""
    global _client
    if _client is not None:
        try:
            _client.shutdown()
        except Exception as e:  # noqa: BLE001
            logger.warning("posthog shutdown failed: %s", e)

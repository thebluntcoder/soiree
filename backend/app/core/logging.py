"""
core/logging.py — structured (JSON) logging for every `app.*` logger.

CONCEPT: why JSON
------------------
Free-text like `logger.info("deleted user %s", user.id)` reads fine in a
local terminal but is useless to whatever reads Railway's log stream — you
can't filter "every log line for user_id=X" out of prose without regex.
`configure_logging()` makes every logger under the `app` namespace emit one
JSON object per line instead: `{"timestamp", "level", "logger", "message",
...}`, plus whatever structured fields a call site passed via
`extra={...}` (e.g. `logger.warning("MCP call failed", extra={"tool_name":
tool_name, "status_code": status})`).

Deliberately not split by environment (JSON in prod, plain text in dev) —
what you see locally should be exactly the shape Railway sees, so a
missing field isn't discovered for the first time in production. Pipe
through `| jq .` locally if raw JSON lines are hard to scan.

CONCEPT: scope — this does not touch uvicorn's own access/error logs
-----------------------------------------------------------------------
Uvicorn configures its own `uvicorn` / `uvicorn.error` / `uvicorn.access`
loggers with `propagate=False`, so they never reach the root logger this
module configures — they keep uvicorn's own plain-text format. This is
about *this app's* log calls only.
"""

import json
import logging
from datetime import datetime, timezone

# Every attribute a LogRecord carries that ISN'T something a call site
# passed via extra={...} — used to fish the extra fields back out.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord(
        "", 0, "", 0, "", (), None
    ).__dict__.keys()
) | {"message", "asctime", "taskName"}


class JSONFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message, extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """
    Route every logger back to a single JSON-formatted stream handler.
    Call once, at startup, before any app module has a chance to log.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

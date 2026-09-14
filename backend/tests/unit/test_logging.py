"""
tests/unit/test_logging.py — the JSON log formatter.

Every app.* log line becomes one JSON object: timestamp/level/logger/message
always present, plus whatever a call site passed via extra={...}.
"""

import json
import logging

import pytest

from app.core.logging import JSONFormatter, configure_logging


def _record(msg, args=(), extra=None, exc_info=None, level=logging.INFO):
    record = logging.LogRecord(
        name="app.test", level=level, pathname=__file__, lineno=1,
        msg=msg, args=args, exc_info=exc_info,
    )
    for k, v in (extra or {}).items():
        setattr(record, k, v)
    return record


class TestJSONFormatter:
    def test_base_fields(self):
        out = json.loads(JSONFormatter().format(_record("hello %s", ("world",))))
        assert out["message"] == "hello world"
        assert out["level"] == "INFO"
        assert out["logger"] == "app.test"
        assert "timestamp" in out

    def test_extra_fields_surface(self):
        out = json.loads(
            JSONFormatter().format(
                _record("deleted user", extra={"user_id": "u1", "plans_deleted": 3})
            )
        )
        assert out["user_id"] == "u1"
        assert out["plans_deleted"] == 3

    def test_no_extra_fields_means_just_the_base_keys(self):
        out = json.loads(JSONFormatter().format(_record("plain message")))
        assert set(out.keys()) == {"timestamp", "level", "logger", "message"}

    def test_exception_info_included(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = _record("failed", exc_info=sys.exc_info(), level=logging.WARNING)
        out = json.loads(JSONFormatter().format(record))
        assert "ValueError: boom" in out["exception"]

    def test_output_is_one_line_valid_json(self):
        # extra values with newlines/quotes must not break line-based log parsing
        out_str = JSONFormatter().format(
            _record("msg", extra={"body": 'has "quotes"\nand a newline'})
        )
        assert "\n" not in out_str
        assert json.loads(out_str)["body"] == 'has "quotes"\nand a newline'


class TestConfigureLogging:
    def test_wires_root_logger_to_json_handler(self, monkeypatch):
        configure_logging()
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_app_logger_output_is_json(self, capsys):
        configure_logging()
        logger = logging.getLogger("app.some.module")
        logger.warning("MCP call failed", extra={"tool_name": "search_restaurants"})
        err = capsys.readouterr().err
        line = err.strip().splitlines()[-1]
        parsed = json.loads(line)  # raises if not valid JSON
        assert parsed["tool_name"] == "search_restaurants"
        assert parsed["logger"] == "app.some.module"

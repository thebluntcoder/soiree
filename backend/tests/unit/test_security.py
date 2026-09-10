"""
tests/unit/test_security.py — SECRET_KEY guard, token encryption, rate limit.
"""

import importlib

import pytest

from app.core import crypto


class TestCrypto:
    def test_roundtrip(self):
        assert crypto.decrypt(crypto.encrypt("swiggy-tok-xyz")) == "swiggy-tok-xyz"

    def test_ciphertext_is_not_plaintext(self):
        enc = crypto.encrypt("hunter2")
        assert "hunter2" not in enc
        assert enc.startswith("gAAAAA")  # Fernet

    def test_legacy_plaintext_passes_through(self):
        # tokens written before encryption landed are raw JSON / raw token
        assert crypto.decrypt('{"access_token":"x"}') == '{"access_token":"x"}'
        assert crypto.decrypt("plain-token") == "plain-token"

    def test_empty(self):
        assert crypto.encrypt("") == ""
        assert crypto.decrypt("") == ""

    def test_tampered_fernet_token_returned_as_is(self):
        enc = crypto.encrypt("abc")
        assert crypto.decrypt(enc[:-4] + "AAAA") in (enc[:-4] + "AAAA",)


class TestSecretKeyGuard:
    def test_prod_with_default_secret_refuses_to_import(self, monkeypatch):
        import app.main as main_mod
        from app.core import config

        monkeypatch.setattr(config.settings, "APP_ENV", "production")
        monkeypatch.setattr(config.settings, "SECRET_KEY", "change-me-in-production")
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            importlib.reload(main_mod)

        # restore a sane module for the rest of the suite
        monkeypatch.setattr(config.settings, "APP_ENV", "development")
        importlib.reload(main_mod)

    def test_dev_default_secret_is_fine(self, monkeypatch):
        import app.main as main_mod
        from app.core import config

        monkeypatch.setattr(config.settings, "APP_ENV", "development")
        monkeypatch.setattr(config.settings, "SECRET_KEY", "change-me-in-production")
        importlib.reload(main_mod)  # no raise
        assert main_mod.app.docs_url == "/docs"

    def test_prod_hides_docs(self, monkeypatch):
        import app.main as main_mod
        from app.core import config

        monkeypatch.setattr(config.settings, "APP_ENV", "production")
        monkeypatch.setattr(config.settings, "SECRET_KEY", "a-real-strong-secret")
        importlib.reload(main_mod)
        assert main_mod.app.docs_url is None
        assert main_mod.app.openapi_url is None

        monkeypatch.setattr(config.settings, "APP_ENV", "development")
        importlib.reload(main_mod)


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_allows_under_limit_then_429s(self, monkeypatch):
        from app.core import ratelimit

        # fake Redis: in-memory counters
        store: dict[str, int] = {}

        class FakeRedis:
            async def incr(self, k):
                store[k] = store.get(k, 0) + 1
                return store[k]

            async def expire(self, k, s):
                return True

        monkeypatch.setattr(ratelimit, "get_redis", lambda: _async(FakeRedis()))

        dep = ratelimit.rate_limit("t", limit=2, window_seconds=3600)
        req = _fake_request("1.2.3.4")

        await dep(req, x_session_id=None)  # 1
        await dep(req, x_session_id=None)  # 2
        with pytest.raises(Exception) as ei:  # 3 → 429
            await dep(req, x_session_id=None)
        assert getattr(ei.value, "status_code", None) == 429

    @pytest.mark.asyncio
    async def test_fails_open_when_redis_down(self, monkeypatch):
        from app.core import ratelimit

        async def boom():
            raise RuntimeError("no redis")

        monkeypatch.setattr(ratelimit, "get_redis", boom)
        dep = ratelimit.rate_limit("t", limit=1, window_seconds=60)
        # should not raise even past the limit
        await dep(_fake_request("9.9.9.9"), x_session_id=None)
        await dep(_fake_request("9.9.9.9"), x_session_id=None)

    @pytest.mark.asyncio
    async def test_session_id_and_ip_are_separate_buckets(self, monkeypatch):
        from app.core import ratelimit

        store: dict[str, int] = {}

        class FakeRedis:
            async def incr(self, k):
                store[k] = store.get(k, 0) + 1
                return store[k]

            async def expire(self, k, s):
                return True

        monkeypatch.setattr(ratelimit, "get_redis", lambda: _async(FakeRedis()))
        dep = ratelimit.rate_limit("t", limit=1, window_seconds=3600)

        await dep(_fake_request("1.1.1.1"), x_session_id="sess-a")
        await dep(_fake_request("1.1.1.1"), x_session_id="sess-b")  # different bucket, ok
        assert len(store) == 2


async def _async(v):
    return v


def _fake_request(ip: str):
    class R:
        headers: dict = {}

        class client:  # noqa: N801
            host = ip

    return R()

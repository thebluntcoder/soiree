"""
tests/unit/test_auth.py — phone-OTP login: normalisation, OTP lifecycle,
Soirée sessions, and the `current_user` gate.

No real Redis or DB — a dict-backed fake Redis (same trick as
test_security.py) and a stubbed DB result.
"""

import pytest

from app.services.auth import otp as otp_mod
from app.services.auth import session as session_mod


# ── fake redis ──────────────────────────────────────────────────────────────

class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def setex(self, key, ttl, value):
        self.store[key] = str(value)

    async def set(self, key, value):
        self.store[key] = str(value)

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    async def incr(self, key):
        self.store[key] = str(int(self.store.get(key, 0)) + 1)
        return int(self.store[key])

    async def expire(self, key, ttl):
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()

    async def _get_redis():
        return r

    monkeypatch.setattr(otp_mod, "get_redis", _get_redis)
    monkeypatch.setattr(session_mod, "get_redis", _get_redis)
    return r


# ── normalize_phone ─────────────────────────────────────────────────────────

class TestNormalizePhone:
    @pytest.mark.parametrize(
        "raw",
        [
            "9876543210",
            "09876543210",
            "+919876543210",
            "+91 98765 43210",
            "919876543210",
            "98765-43210",
        ],
    )
    def test_accepts_indian_mobiles(self, raw):
        assert otp_mod.normalize_phone(raw) == "+919876543210"

    @pytest.mark.parametrize(
        "raw",
        ["12345", "1234567890", "5876543210", "+1 415 555 0100", "98765432101"],
    )
    def test_rejects_non_mobiles(self, raw):
        with pytest.raises(ValueError):
            otp_mod.normalize_phone(raw)


# ── OTP lifecycle ───────────────────────────────────────────────────────────

class TestOtpFlow:
    @pytest.mark.asyncio
    async def test_issue_then_verify(self, fake_redis, monkeypatch):
        monkeypatch.setattr(otp_mod, "_is_prod", lambda: True)  # no magic code
        sent = {}

        class Sender:
            async def send(self, phone, code):
                sent["phone"], sent["code"] = phone, code

        monkeypatch.setattr(otp_mod, "get_otp_sender", lambda: Sender())

        await otp_mod.issue_otp("+919876543210")
        assert sent["phone"] == "+919876543210"
        assert len(sent["code"]) == 6

        assert await otp_mod.verify_otp("+919876543210", sent["code"]) is True
        # consumed — a second verify with the same code fails
        assert await otp_mod.verify_otp("+919876543210", sent["code"]) is False

    @pytest.mark.asyncio
    async def test_wrong_code_and_lockout(self, fake_redis, monkeypatch):
        monkeypatch.setattr(otp_mod, "_is_prod", lambda: True)
        monkeypatch.setattr(
            otp_mod, "get_otp_sender", lambda: _CollectSender()
        )
        await otp_mod.issue_otp("+919876543210")

        for _ in range(otp_mod.OTP_MAX_ATTEMPTS):
            assert await otp_mod.verify_otp("+919876543210", "000001") is False
        # even the correct code is refused once locked out
        real = fake_redis.store["otp:+919876543210"]
        assert await otp_mod.verify_otp("+919876543210", real) is False

    @pytest.mark.asyncio
    async def test_magic_code_only_outside_prod(self, fake_redis, monkeypatch):
        monkeypatch.setattr(otp_mod, "_is_prod", lambda: False)
        assert await otp_mod.verify_otp("+919555555555", otp_mod.DEV_MAGIC_CODE) is True

        monkeypatch.setattr(otp_mod, "_is_prod", lambda: True)
        assert await otp_mod.verify_otp("+919555555555", otp_mod.DEV_MAGIC_CODE) is False

    @pytest.mark.asyncio
    async def test_dev_login_phone_bypasses_in_prod(self, fake_redis, monkeypatch):
        monkeypatch.setattr(otp_mod, "_is_prod", lambda: True)
        monkeypatch.setattr(
            otp_mod.settings, "DEV_LOGIN_PHONES", "9998887777, +91 90000 00001"
        )
        # allowlisted (either format) → magic code works even in prod
        assert await otp_mod.verify_otp("+919998887777", otp_mod.DEV_MAGIC_CODE) is True
        assert await otp_mod.verify_otp("+919000000001", otp_mod.DEV_MAGIC_CODE) is True
        # everyone else → still rejected in prod
        assert await otp_mod.verify_otp("+919111111111", otp_mod.DEV_MAGIC_CODE) is False

    @pytest.mark.asyncio
    async def test_dev_login_phone_skips_sms(self, fake_redis, monkeypatch):
        monkeypatch.setattr(otp_mod, "_is_prod", lambda: True)
        monkeypatch.setattr(otp_mod.settings, "DEV_LOGIN_PHONES", "9998887777")
        sent: list[str] = []

        class Sender:
            async def send(self, phone, code):
                sent.append(phone)

        monkeypatch.setattr(otp_mod, "get_otp_sender", lambda: Sender())

        await otp_mod.issue_otp("+919998887777")
        assert sent == []  # no SMS burned on a dev-login number
        await otp_mod.issue_otp("+919111111111")
        assert sent == ["+919111111111"]  # a normal number still sends


class _CollectSender:
    async def send(self, phone, code):
        pass


# ── Soirée session ──────────────────────────────────────────────────────────

class TestSession:
    @pytest.mark.asyncio
    async def test_create_resolve_revoke(self, fake_redis):
        token = await session_mod.create_session("user-123", "+919876543210")
        assert token

        data = await session_mod.resolve_session(token)
        assert data == {"user_id": "user-123", "phone": "+919876543210"}

        await session_mod.revoke_session(token)
        assert await session_mod.resolve_session(token) is None

    @pytest.mark.asyncio
    async def test_resolve_none_and_unknown(self, fake_redis):
        assert await session_mod.resolve_session(None) is None
        assert await session_mod.resolve_session("nope") is None


# ── current_user dependency ─────────────────────────────────────────────────

class TestCurrentUser:
    @pytest.mark.asyncio
    async def test_no_session_header_401(self):
        from fastapi import HTTPException

        from app.api.v1 import deps

        with pytest.raises(HTTPException) as ei:
            await deps.current_user(x_soiree_session=None, db=_FakeDB(None))
        assert ei.value.status_code == 401
        assert ei.value.detail["code"] == "NOT_LOGGED_IN"

    @pytest.mark.asyncio
    async def test_valid_session_returns_user(self, fake_redis, monkeypatch):
        from app.api.v1 import deps

        monkeypatch.setattr(
            deps, "resolve_session",
            _async_return({"user_id": "u1", "phone": "+919876543210"}),
        )
        user = _User(id="u1", is_active=True)
        got = await deps.current_user(x_soiree_session="tok", db=_FakeDB(user))
        assert got is user

    @pytest.mark.asyncio
    async def test_inactive_user_401(self, fake_redis, monkeypatch):
        from fastapi import HTTPException

        from app.api.v1 import deps

        monkeypatch.setattr(
            deps, "resolve_session",
            _async_return({"user_id": "u1", "phone": "+919876543210"}),
        )
        with pytest.raises(HTTPException) as ei:
            await deps.current_user(
                x_soiree_session="tok", db=_FakeDB(_User(id="u1", is_active=False))
            )
        assert ei.value.status_code == 401


# ── tiny stubs for current_user ─────────────────────────────────────────────

class _User:
    def __init__(self, id, is_active):
        self.id = id
        self.is_active = is_active


class _Result:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeDB:
    def __init__(self, obj):
        self._obj = obj

    async def execute(self, *_a, **_kw):
        return _Result(self._obj)


def _async_return(value):
    async def _f(*_a, **_kw):
        return value

    return _f

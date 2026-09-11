"""
tests/unit/test_auth.py — Swiggy-OAuth login internals.

Covers: reading the user identity out of a Swiggy access-token JWT, the
Soirée session lifecycle, and the `current_user` gate. No real Redis / DB
— a dict-backed fake Redis and a stubbed DB result.
"""

import base64
import json

import pytest

from app.services.auth import oauth as oauth_mod
from app.services.auth import session as session_mod


# ── fake redis ──────────────────────────────────────────────────────────────

class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def setex(self, key, ttl, value):
        self.store[key] = str(value)

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()

    async def _get_redis():
        return r

    monkeypatch.setattr(session_mod, "get_redis", _get_redis)
    return r


def _fake_jwt(payload: dict) -> str:
    def seg(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg(payload)}.sig"


# ── decode_token_identity ───────────────────────────────────────────────────

class TestDecodeTokenIdentity:
    def test_pulls_sub_and_user_id(self):
        tok = _fake_jwt({"sub": "abc-123", "user_id": 22876329, "exp": 9999999999})
        assert oauth_mod.decode_token_identity(tok) == {
            "sub": "abc-123",
            "user_id": "22876329",
        }

    def test_user_id_optional(self):
        tok = _fake_jwt({"sub": "abc-123"})
        assert oauth_mod.decode_token_identity(tok) == {"sub": "abc-123", "user_id": None}

    def test_no_sub_is_an_error(self):
        tok = _fake_jwt({"user_id": 1})
        with pytest.raises(oauth_mod.TokenIdentityError):
            oauth_mod.decode_token_identity(tok)

    def test_opaque_token_is_an_error(self):
        with pytest.raises(oauth_mod.TokenIdentityError):
            oauth_mod.decode_token_identity("not-a-jwt-at-all")


# ── Soirée session ──────────────────────────────────────────────────────────

class TestSession:
    @pytest.mark.asyncio
    async def test_create_resolve_revoke(self, fake_redis):
        token = await session_mod.create_session("user-123", "")
        assert token

        data = await session_mod.resolve_session(token)
        assert data == {"user_id": "user-123", "phone": ""}

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
    async def test_valid_session_returns_user(self, monkeypatch):
        from app.api.v1 import deps

        monkeypatch.setattr(
            deps, "resolve_session", _async_return({"user_id": "u1", "phone": ""})
        )
        user = _User(id="u1", is_active=True)
        got = await deps.current_user(x_soiree_session="tok", db=_FakeDB(user))
        assert got is user

    @pytest.mark.asyncio
    async def test_inactive_user_401(self, monkeypatch):
        from fastapi import HTTPException

        from app.api.v1 import deps

        monkeypatch.setattr(
            deps, "resolve_session", _async_return({"user_id": "u1", "phone": ""})
        )
        with pytest.raises(HTTPException) as ei:
            await deps.current_user(
                x_soiree_session="tok", db=_FakeDB(_User(id="u1", is_active=False))
            )
        assert ei.value.status_code == 401


# ── /auth/start consent gate ────────────────────────────────────────────────

class TestAuthStartConsent:
    @pytest.mark.asyncio
    async def test_no_consent_is_400(self, fake_redis, monkeypatch):
        from fastapi import HTTPException

        from app.api.v1.endpoints import auth as auth_ep

        monkeypatch.setattr(auth_ep, "get_redis", _lambda_async(fake_redis))
        with pytest.raises(HTTPException) as ei:
            await auth_ep.auth_start(consent=False)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_consent_true_stores_timestamp(self, fake_redis, monkeypatch):
        from app.api.v1.endpoints import auth as auth_ep

        fake_redis.store[auth_ep.CLIENT_ID_KEY] = "cached-client"
        monkeypatch.setattr(auth_ep, "get_redis", _lambda_async(fake_redis))

        out = await auth_ep.auth_start(consent=True)
        assert "authorize_url" in out
        pkce = json.loads(fake_redis.store[auth_ep.pkce_redis_key(out["state"])])
        assert pkce["consent_at"]  # an ISO timestamp was recorded


# ── /auth/callback flow ─────────────────────────────────────────────────────

class TestAuthCallback:
    @pytest.mark.asyncio
    async def test_new_user_gets_session(self, fake_redis, monkeypatch):
        from app.api.v1.endpoints import auth as auth_ep

        fake_redis.store[auth_ep.pkce_redis_key("st8")] = json.dumps(
            {"code_verifier": "v", "state": "st8", "consent_at": "2026-09-11T10:00:00"}
        )
        tok = _fake_jwt({"sub": "swg-77", "user_id": 22876329})

        async def _exchange(code, code_verifier):
            assert (code, code_verifier) == ("auth-code", "v")
            return {"access_token": tok, "expires_in": 432000, "scope": "mcp:tools"}

        monkeypatch.setattr(auth_ep, "get_redis", _lambda_async(fake_redis))
        monkeypatch.setattr(auth_ep, "exchange_code_for_token", _exchange)
        monkeypatch.setattr(auth_ep, "create_session", _async_return("sess-tok"))

        db = _CaptureDB(existing=None)
        resp = await auth_ep.auth_callback(
            auth_ep.CallbackRequest(code="auth-code", state="st8"), db=db
        )
        assert resp.soiree_session == "sess-tok"
        assert resp.is_new is True
        assert resp.user.swiggy_user_id == "22876329"
        assert db.added.swiggy_sub == "swg-77"
        assert db.added.consent_accepted_at.isoformat() == "2026-09-11T10:00:00"
        # token cached under the new user's id, encrypted
        key = auth_ep.token_redis_key(db.added.id)
        assert key in fake_redis.store and "swg-77" not in fake_redis.store[key]
        # pkce record consumed
        assert auth_ep.pkce_redis_key("st8") not in fake_redis.store

    @pytest.mark.asyncio
    async def test_bad_state_400(self, fake_redis, monkeypatch):
        from fastapi import HTTPException

        from app.api.v1.endpoints import auth as auth_ep

        monkeypatch.setattr(auth_ep, "get_redis", _lambda_async(fake_redis))
        with pytest.raises(HTTPException) as ei:
            await auth_ep.auth_callback(
                auth_ep.CallbackRequest(code="c", state="nope"), db=_CaptureDB(None)
            )
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_opaque_token_502(self, fake_redis, monkeypatch):
        from fastapi import HTTPException

        from app.api.v1.endpoints import auth as auth_ep

        fake_redis.store[auth_ep.pkce_redis_key("st9")] = json.dumps(
            {"code_verifier": "v", "state": "st9"}
        )
        monkeypatch.setattr(auth_ep, "get_redis", _lambda_async(fake_redis))
        monkeypatch.setattr(
            auth_ep, "exchange_code_for_token",
            _async_return({"access_token": "opaque-not-a-jwt"}),
        )
        with pytest.raises(HTTPException) as ei:
            await auth_ep.auth_callback(
                auth_ep.CallbackRequest(code="c", state="st9"), db=_CaptureDB(None)
            )
        assert ei.value.status_code == 502


# ── DELETE /users/me ─────────────────────────────────────────────────────────

class TestDeleteMe:
    @pytest.mark.asyncio
    async def test_purges_plans_events_token_and_session(self, monkeypatch):
        from app.api.v1.endpoints import auth as auth_ep
        from app.api.v1.endpoints import users as users_ep

        user = _User(id="u1", is_active=True)
        plans = [object(), object()]
        events = [object()]
        db = _SeqDB([plans, events])

        purged = []
        monkeypatch.setattr(
            auth_ep, "purge_swiggy_token", _record_async(purged, "purge")
        )
        revoked = []
        monkeypatch.setattr(
            users_ep, "revoke_session", _record_async(revoked, "revoke")
        )

        resp = await users_ep.delete_me(user=user, session=db, x_soiree_session="tok")

        assert resp == {"deleted": True, "plans_deleted": 2, "events_deleted": 1}
        assert db.deleted == [*plans, *events, user]
        assert db.committed is True
        assert purged == [("purge", "u1")]
        assert revoked == [("revoke", "tok")]


def _record_async(sink, label):
    async def _f(arg):
        sink.append((label, arg))

    return _f


class _SeqDB:
    """Returns queued result lists in call order; tracks delete()/commit()."""

    def __init__(self, results):
        self._results = list(results)
        self.deleted: list = []
        self.committed = False

    async def execute(self, *_a, **_kw):
        return _ListResult(self._results.pop(0))

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True


class _ListResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


def _lambda_async(value):
    async def _f():
        return value

    return _f


class _CaptureDB:
    def __init__(self, existing):
        self._existing = existing
        self.added = None

    async def execute(self, *_a, **_kw):
        return _Result(self._existing)

    def add(self, obj):
        self.added = obj

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


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

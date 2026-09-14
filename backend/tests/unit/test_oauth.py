"""
tests/unit/test_oauth.py — Swiggy OAuth 2.1 PKCE mechanics.

`decode_token_identity` is covered in test_auth.py; this file covers the
rest of services/auth/oauth.py: PKCE generation, the authorize URL, Redis
key helpers, expiry math, and the two outbound HTTP calls (Dynamic Client
Registration + token exchange), mocked with respx rather than hitting
Swiggy for real.
"""

import base64
import hashlib

import httpx
import pytest
import respx

from app.services.auth import oauth


class TestGeneratePkce:
    def test_verifier_and_challenge_are_urlsafe_no_padding(self):
        verifier, challenge = oauth.generate_pkce()
        for value in (verifier, challenge):
            assert "=" not in value
            assert "+" not in value and "/" not in value

    def test_challenge_is_sha256_of_verifier(self):
        verifier, challenge = oauth.generate_pkce()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        assert challenge == expected

    def test_two_calls_are_not_the_same(self):
        v1, _ = oauth.generate_pkce()
        v2, _ = oauth.generate_pkce()
        assert v1 != v2


class TestBuildAuthorizeUrl:
    def test_contains_all_required_params(self):
        url = oauth.build_authorize_url(
            code_challenge="chal123", state="state456", client_id="cid789"
        )
        assert url.startswith(oauth.AUTHORIZE_URL + "?")
        assert "response_type=code" in url
        assert "client_id=cid789" in url
        assert f"redirect_uri={oauth.REDIRECT_URI}" in url
        assert "code_challenge=chal123" in url
        assert "code_challenge_method=S256" in url
        assert "state=state456" in url
        assert f"scope={oauth.SCOPES}" in url


class TestRedisKeyHelpers:
    def test_token_redis_key(self):
        assert oauth.token_redis_key("user-1") == "swiggy_token:user-1"

    def test_pkce_redis_key(self):
        assert oauth.pkce_redis_key("state-1") == "swiggy_pkce:state-1"


class TestIsTokenExpired:
    def test_far_future_is_not_expired(self):
        assert oauth.is_token_expired(time_now() + 3600) is False

    def test_already_past_is_expired(self):
        assert oauth.is_token_expired(time_now() - 1) is True

    def test_within_buffer_counts_as_expired(self):
        # buffer defaults to 60s — a token expiring in 30s is treated as dead
        assert oauth.is_token_expired(time_now() + 30) is True

    def test_custom_buffer(self):
        assert oauth.is_token_expired(time_now() + 100, buffer_seconds=50) is False
        assert oauth.is_token_expired(time_now() + 40, buffer_seconds=50) is True


def time_now() -> float:
    import time

    return time.time()


class TestRegisterClient:
    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_dcr_payload_and_returns_json(self):
        route = respx.post(oauth.REGISTER_URL).mock(
            return_value=httpx.Response(201, json={"client_id": "swiggy-mcp"})
        )
        result = await oauth.register_client()
        assert result == {"client_id": "swiggy-mcp"}
        import json

        payload = json.loads(route.calls.last.request.content)
        assert payload["client_name"] == "Soirée"
        assert payload["redirect_uris"] == [oauth.REDIRECT_URI]
        assert payload["grant_types"] == ["authorization_code"]
        assert payload["response_types"] == ["code"]
        assert payload["scope"] == oauth.SCOPES
        assert payload["token_endpoint_auth_method"] == "none"

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_error_status(self):
        respx.post(oauth.REGISTER_URL).mock(return_value=httpx.Response(400))
        with pytest.raises(httpx.HTTPStatusError):
            await oauth.register_client()


class TestExchangeCodeForToken:
    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_pkce_payload_and_returns_json(self):
        route = respx.post(oauth.TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "tok-abc", "expires_in": 432000, "scope": "mcp:tools"},
            )
        )
        result = await oauth.exchange_code_for_token(
            code="auth-code-1", code_verifier="verifier-1"
        )
        assert result == {
            "access_token": "tok-abc",
            "expires_in": 432000,
            "scope": "mcp:tools",
        }
        import json

        payload = json.loads(route.calls.last.request.content)
        assert payload == {
            "grant_type": "authorization_code",
            "code": "auth-code-1",
            "code_verifier": "verifier-1",
            "redirect_uri": oauth.REDIRECT_URI,
        }

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_error_status(self):
        respx.post(oauth.TOKEN_URL).mock(return_value=httpx.Response(400))
        with pytest.raises(httpx.HTTPStatusError):
            await oauth.exchange_code_for_token(code="c", code_verifier="v")

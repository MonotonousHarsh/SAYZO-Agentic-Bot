import time
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.api.auth import AuthError, DevAuthenticator, SupabaseAuthenticator

URL = "https://demo.supabase.co"
ISSUER = f"{URL}/auth/v1"
USER = uuid4()


def claims(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "sub": str(USER),
        "email": "rohan@nexora.test",
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": int(time.time()) + 300,
    }
    values.update(overrides)
    return values


class FakeJWKS:
    def __init__(self, key) -> None:  # type: ignore[no-untyped-def]
        self.key = key

    def get_signing_key_from_jwt(self, token: str):  # type: ignore[no-untyped-def]
        return self


@pytest.fixture
def es256():  # type: ignore[no-untyped-def]
    private = ec.generate_private_key(ec.SECP256R1())
    auth = SupabaseAuthenticator(URL)
    auth._jwks = FakeJWKS(private.public_key())  # type: ignore[assignment]  # noqa: SLF001
    return auth, private


def test_asymmetric_tokens_are_verified_against_the_jwks(es256) -> None:  # type: ignore[no-untyped-def]
    auth, key = es256
    token = jwt.encode(claims(), key, algorithm="ES256")
    identity = auth.authenticate(f"Bearer {token}", None)
    assert (identity.auth_user_id, identity.email) == (USER, "rohan@nexora.test")


@pytest.mark.parametrize(
    "bad",
    [{"exp": int(time.time()) - 10}, {"aud": "anon"}, {"iss": "https://evil.example/auth/v1"}],
)
def test_expired_or_foreign_tokens_are_rejected(es256, bad) -> None:  # type: ignore[no-untyped-def]
    auth, key = es256
    with pytest.raises(AuthError):
        auth.authenticate(f"Bearer {jwt.encode(claims(**bad), key, algorithm='ES256')}", None)


def test_a_token_signed_by_another_key_is_rejected(es256) -> None:  # type: ignore[no-untyped-def]
    auth, _ = es256
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(AuthError):
        auth.authenticate(f"Bearer {jwt.encode(claims(), other, algorithm='ES256')}", None)


def test_legacy_hs256_with_secret() -> None:
    auth = SupabaseAuthenticator(URL, jwt_secret="s" * 32)
    token = jwt.encode(claims(), "s" * 32, algorithm="HS256")
    assert auth.authenticate(f"Bearer {token}", None).auth_user_id == USER
    with pytest.raises(AuthError):
        auth.authenticate(f"Bearer {jwt.encode(claims(), 'x' * 32, algorithm='HS256')}", None)


def test_legacy_hs256_without_secret_asks_supabase() -> None:
    accepted = jwt.encode(claims(), "a" * 32, algorithm="HS256")
    rejected = jwt.encode(claims(), "b" * 32, algorithm="HS256")
    calls: list[httpx.Request] = []

    def supabase(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.headers["authorization"] == f"Bearer {accepted}":
            return httpx.Response(200, json={"id": str(USER), "email": "rohan@nexora.test"})
        return httpx.Response(401, json={"msg": "invalid JWT"})

    auth = SupabaseAuthenticator(
        URL, "sb_publishable_x", http=httpx.Client(transport=httpx.MockTransport(supabase))
    )

    assert auth.authenticate(f"Bearer {accepted}", None).auth_user_id == USER
    assert auth.authenticate(f"Bearer {accepted}", None).auth_user_id == USER  # cached
    with pytest.raises(AuthError):
        auth.authenticate(f"Bearer {rejected}", None)
    assert len(calls) == 2
    assert calls[0].url == httpx.URL(f"{ISSUER}/user")
    assert calls[0].headers["apikey"] == "sb_publishable_x"


def test_missing_credentials() -> None:
    with pytest.raises(AuthError):
        SupabaseAuthenticator(URL).authenticate(None, None)
    with pytest.raises(AuthError):
        DevAuthenticator().authenticate(None, None)
    assert DevAuthenticator().authenticate(None, "nex_arjun").person_key == "nex_arjun"

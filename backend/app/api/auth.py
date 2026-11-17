"""Who is calling: verify a Supabase session token, or (local demos only) a dev header.

Supabase projects sign user tokens either with asymmetric keys (ES256/RS256, verified here
against the project's public JWKS) or with the legacy HS256 secret (verified locally if the
secret is configured, otherwise by asking Supabase Auth). The token says who the user is;
what they may see is decided afterwards from the company directory, never from the token.
"""

import hashlib
import time
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
import jwt

AUDIENCE = "authenticated"
REMOTE_CACHE_SECONDS = 60.0


class AuthError(Exception):
    """Missing, invalid or expired credentials."""


@dataclass(frozen=True)
class Identity:
    auth_user_id: UUID | None = None
    email: str | None = None
    person_key: str | None = None  # dev mode only


class Authenticator(Protocol):
    def authenticate(self, authorization: str | None, dev_user: str | None) -> Identity: ...


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("sign in required")
    token = authorization[7:].strip()
    if not token:
        raise AuthError("sign in required")
    return token


class SupabaseAuthenticator:
    def __init__(
        self,
        supabase_url: str,
        publishable_key: str | None = None,
        jwt_secret: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self._url = supabase_url.rstrip("/")
        self._issuer = f"{self._url}/auth/v1"
        self._publishable_key = publishable_key
        self._jwt_secret = jwt_secret
        self._jwks = jwt.PyJWKClient(f"{self._issuer}/.well-known/jwks.json", cache_keys=True)
        self._http = http or httpx.Client(timeout=10.0)
        self._remote_cache: dict[str, tuple[float, Identity]] = {}

    def authenticate(self, authorization: str | None, dev_user: str | None) -> Identity:
        token = bearer_token(authorization)
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
        except jwt.PyJWTError as error:
            raise AuthError("invalid token") from error

        if algorithm in ("ES256", "RS256", "EdDSA"):
            try:
                key = self._jwks.get_signing_key_from_jwt(token).key
                claims = jwt.decode(
                    token, key, algorithms=[algorithm], audience=AUDIENCE, issuer=self._issuer
                )
            except jwt.PyJWTError as error:
                raise AuthError("invalid or expired token") from error
            return _identity(claims)
        if algorithm == "HS256" and self._jwt_secret:
            try:
                claims = jwt.decode(
                    token, self._jwt_secret, algorithms=["HS256"], audience=AUDIENCE
                )
            except jwt.PyJWTError as error:
                raise AuthError("invalid or expired token") from error
            return _identity(claims)
        if algorithm == "HS256":
            return self._verify_remotely(token)
        raise AuthError("unsupported token")

    def _verify_remotely(self, token: str) -> Identity:
        """Legacy projects without a configured secret: ask Supabase Auth who this is."""
        digest = hashlib.sha256(token.encode()).hexdigest()
        cached = self._remote_cache.get(digest)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]
        headers = {"Authorization": f"Bearer {token}"}
        if self._publishable_key:
            headers["apikey"] = self._publishable_key
        try:
            response = self._http.get(f"{self._issuer}/user", headers=headers)
        except httpx.HTTPError as error:
            raise AuthError("could not reach Supabase Auth") from error
        if response.status_code != 200:
            raise AuthError("invalid or expired token")
        body = response.json()
        identity = _identity({"sub": body.get("id"), "email": body.get("email")})
        self._remote_cache[digest] = (time.monotonic() + REMOTE_CACHE_SECONDS, identity)
        return identity


class DevAuthenticator:
    """Trusts an X-Dev-User header naming a directory person. Local demos and tests only."""

    def authenticate(self, authorization: str | None, dev_user: str | None) -> Identity:
        if not dev_user:
            raise AuthError("pick a person (X-Dev-User header) in dev mode")
        return Identity(person_key=dev_user)


def _identity(claims: dict) -> Identity:  # type: ignore[type-arg]
    try:
        user_id = UUID(str(claims["sub"]))
    except (KeyError, ValueError) as error:
        raise AuthError("token has no user") from error
    email = claims.get("email")
    if not email:
        raise AuthError("token has no email")
    return Identity(auth_user_id=user_id, email=str(email))

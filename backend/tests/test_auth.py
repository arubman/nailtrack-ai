from __future__ import annotations

from datetime import timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.auth import InvalidToken, decode_entra_token, get_current_user, resolve_signing_key
from app.config import Settings, clear_settings_cache
from app.database import dispose_engine

TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLIENT_ID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
SPA_ID = "cccccccc-dddd-eeee-ffff-000000000000"
ISSUER = f"https://nailtrack.ciamlogin.com/{TENANT}/v2.0"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()


def _token(**overrides) -> str:
    from app.database import utcnow

    now = utcnow()
    payload = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "exp": now + timedelta(minutes=10),
        "nbf": now - timedelta(seconds=5),
        "iat": now,
        "tid": TENANT,
        "oid": "11111111-1111-1111-1111-111111111111",
        "sub": "pairwise-subject",
        "preferred_username": "person@example.com",
        "name": "Person Example",
        "ver": "2.0",
        "scp": "access_as_user",
        "azp": SPA_ID,
    }
    payload.update(overrides)
    return jwt.encode(payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": "test-key"})


def _entra_settings(**overrides) -> Settings:
    data = {
        "environment": "test",
        "auth_mode": "entra",
        "database_url": "sqlite://",
        "storage_backend": "local",
        "entra_tenant_id": TENANT,
        "entra_tenant_subdomain": "nailtrack",
        "entra_client_id": CLIENT_ID,
        "entra_required_scope": "access_as_user",
        "entra_allowed_azp": SPA_ID,
    }
    data.update(overrides)
    return Settings(_env_file=None, **data)


def test_decode_accepts_an_external_id_access_token():
    claims = decode_entra_token(_token(), _entra_settings(), _PUBLIC_KEY)
    assert claims["oid"].startswith("11111111")


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-else"},
        {"iss": "https://login.microsoftonline.com/other/v2.0"},
        {"tid": "99999999-9999-9999-9999-999999999999"},
        {"scp": "User.Read"},
        {"azp": "not-the-spa"},
        {"exp": 1_000_000_000},
    ],
)
def test_decode_rejects_bad_tokens(overrides):
    with pytest.raises(InvalidToken):
        decode_entra_token(_token(**overrides), _entra_settings(), _PUBLIC_KEY)


def test_decode_rejects_alg_none():
    header = _b64(b'{"alg":"none","typ":"JWT"}')
    body = _b64(b'{"sub":"x"}')
    with pytest.raises(InvalidToken):
        decode_entra_token(f"{header}.{body}.", _entra_settings(), _PUBLIC_KEY)


def _b64(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_signing_key_lookup_fails_closed_when_jwks_is_unreachable():
    settings = _entra_settings(
        entra_jwks_url="http://127.0.0.1:9/keys",
    )
    with pytest.raises(InvalidToken):
        resolve_signing_key(_token(), settings)


def test_dev_mode_uses_one_local_user_and_ignores_bearer_tokens(client):
    first = client.get("/api/v1/me")
    second = client.get(
        "/api/v1/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["user"]["id"] == second.json()["user"]["id"]
    assert first.json()["auth_mode"] == "dev"
    assert first.json()["user"]["email"] == "dev@localhost"


def test_entra_mode_requires_a_valid_token(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_MODE", "entra")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'entra.db'}")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("AUTO_CREATE_TABLES", "true")
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("ENTRA_TENANT_SUBDOMAIN", "nailtrack")
    monkeypatch.setenv("ENTRA_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("ENTRA_REQUIRED_SCOPE", "access_as_user")
    monkeypatch.setenv("ENTRA_ALLOWED_AZP", SPA_ID)
    clear_settings_cache()
    dispose_engine()
    monkeypatch.setattr("app.auth.resolve_signing_key", lambda token, settings: _PUBLIC_KEY)
    from app.main import create_app

    with TestClient(create_app()) as entra_client:
        assert entra_client.get("/health").status_code == 200
        assert entra_client.get("/api/v1/me").status_code == 401
        assert entra_client.get("/api/v1/treatments").status_code == 401
        assert entra_client.get("/api/v1/fingers").status_code == 401
        missing = entra_client.get("/api/v1/media/users/not-a-user/raw.jpg")
        assert missing.status_code == 401

        ok = entra_client.get("/api/v1/me", headers={"Authorization": f"Bearer {_token()}"})
        assert ok.status_code == 200
        body = ok.json()
        assert body["user"]["email"] == "person@example.com"
        assert body["user"]["display_name"] == "Person Example"
        assert body["auth_mode"] == "entra"

        again = entra_client.get("/api/v1/me", headers={"Authorization": f"Bearer {_token()}"})
        assert again.json()["user"]["id"] == body["user"]["id"]

        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app.database import get_engine
        from app.models import User

        with Session(get_engine()) as session:
            stored = session.scalar(select(User).where(User.email == "person@example.com"))
            assert stored is not None
            assert stored.external_subject == f"{TENANT}:11111111-1111-1111-1111-111111111111"

        rejected = entra_client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {_token(aud='nope')}"},
        )
        assert rejected.status_code == 401
        assert rejected.json()["detail"] == "Invalid or expired token"
    dispose_engine()
    clear_settings_cache()


def test_api_routes_resolve_the_current_user(client):
    checked = list(_mounted_api_routes(client.app))
    paths = [path for path, _route in checked]
    for path, route in checked:
        assert _depends_on(route.dependant, get_current_user), path
    assert "/api/v1/treatments" in paths
    assert "/api/v1/fingers" in paths
    assert "/api/v1/me" in paths
    assert any(path.startswith("/api/v1/media/") for path in paths)


def _mounted_api_routes(app):
    """Yield (path, APIRoute) for every /api route, including included routers.

    FastAPI keeps included routers wrapped until a request matches, so app.routes
    is not a flat list of APIRoute objects.
    """

    def walk(routes, prefix: str):
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None and hasattr(route, "include_context"):
                yield from walk(included.routes, prefix + route.include_context.prefix)
            elif isinstance(route, APIRoute):
                path = prefix + route.path
                if path.startswith("/api/"):
                    yield path, route

    yield from walk(app.routes, "")


def _depends_on(dependant, call) -> bool:
    if getattr(dependant, "call", None) is call:
        return True
    return any(_depends_on(child, call) for child in getattr(dependant, "dependencies", []))

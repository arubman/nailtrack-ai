"""Resolve the caller on every API route.

Production validates Microsoft Entra External ID access tokens against the
tenant JWKS (RS256, issuer, audience, expiry, tenant id). Development and
test can use AUTH_MODE=dev, which maps every request onto one local user and
does not inspect bearer tokens. That bypass is rejected when
ENVIRONMENT=production, both when settings load and again here.
"""

from __future__ import annotations

import logging
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.database import utcnow
from app.models import User

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="EntraExternalID",
    description=(
        "Access token issued by Microsoft Entra External ID. "
        "Not required when the server is running with AUTH_MODE=dev."
    ),
)

_jwks_clients: dict[str, PyJWKClient] = {}


class InvalidToken(Exception):
    """Bearer token failed validation. The public error stays generic."""


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def resolve_signing_key(token: str, settings: Settings) -> Any:
    """Return the RSA public key for this token's kid. Tests may monkeypatch this."""
    url = settings.resolved_jwks_url
    if not url:
        raise InvalidToken("JWKS URL is not configured")
    client = _jwks_clients.get(url)
    if client is None:
        client = PyJWKClient(
            url,
            cache_keys=True,
            lifespan=settings.entra_jwks_cache_seconds,
            timeout=5,
        )
        _jwks_clients[url] = client
    try:
        return client.get_signing_key_from_jwt(token).key
    except Exception as exc:
        raise InvalidToken("Unable to resolve signing key") from exc


def decode_entra_token(token: str, settings: Settings, signing_key: Any) -> dict[str, Any]:
    if not settings.resolved_audiences or not settings.resolved_issuer:
        raise InvalidToken("Entra auth is not configured")
    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.resolved_audiences,
            issuer=settings.resolved_issuer,
            leeway=settings.entra_clock_skew_seconds,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidToken("Invalid or expired token") from exc
    if not isinstance(claims, dict):
        raise InvalidToken("Invalid or expired token")
    _check_tenant(claims, settings)
    _check_scope(claims, settings)
    _check_authorized_party(claims, settings)
    if not _string_claim(claims, "oid") and not _string_claim(claims, "sub"):
        raise InvalidToken("Invalid or expired token")
    return claims


def subject_from_claims(claims: dict[str, Any]) -> str:
    raw = _string_claim(claims, "oid") or _string_claim(claims, "sub")
    if raw is None:
        raise InvalidToken("Invalid or expired token")
    tenant = _string_claim(claims, "tid")
    if tenant:
        return f"{tenant}:{raw}"
    return raw


def email_from_claims(claims: dict[str, Any]) -> str | None:
    for key in ("email", "preferred_username", "upn"):
        value = _string_claim(claims, key)
        if value and "@" in value:
            return value
    return None


def display_name_from_claims(claims: dict[str, Any]) -> str | None:
    name = _string_claim(claims, "name")
    if name:
        return name[:200]
    email = email_from_claims(claims)
    if email:
        return email.split("@", 1)[0][:200]
    return None


def get_or_create_user(
    db: Session,
    *,
    external_subject: str,
    email: str | None,
    display_name: str | None,
) -> User:
    user = db.scalar(select(User).where(User.external_subject == external_subject))
    if user is None:
        user = User(
            external_subject=external_subject,
            email=email,
            display_name=display_name,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            user = db.scalar(select(User).where(User.external_subject == external_subject))
            if user is None:
                raise
        else:
            db.refresh(user)
            return user
    changed = False
    if email is not None and user.email != email:
        user.email = email
        changed = True
    if display_name is not None and user.display_name != display_name:
        user.display_name = display_name
        changed = True
    if changed:
        user.updated_at = utcnow()
        db.commit()
        db.refresh(user)
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    settings = get_settings()
    if settings.auth_mode == "dev":
        # Settings already refuse this pair. The check stays here so a route
        # cannot run as the dev user if that gate is ever bypassed.
        if settings.environment == "production":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Dev authentication is disabled",
            )
        return get_or_create_user(
            db,
            external_subject=settings.dev_user_subject,
            email=settings.dev_user_email,
            display_name=settings.dev_user_display_name,
        )
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise _unauthorized()
    try:
        signing_key = resolve_signing_key(credentials.credentials, settings)
        claims = decode_entra_token(credentials.credentials, settings, signing_key)
    except InvalidToken as exc:
        logger.info("Rejected bearer token: %s", exc.__cause__.__class__.__name__ if exc.__cause__ else "invalid")
        raise _unauthorized("Invalid or expired token") from None
    return get_or_create_user(
        db,
        external_subject=subject_from_claims(claims),
        email=email_from_claims(claims),
        display_name=display_name_from_claims(claims),
    )


def _string_claim(claims: dict[str, Any], key: str) -> str | None:
    value = claims.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _check_tenant(claims: dict[str, Any], settings: Settings) -> None:
    expected = settings.entra_tenant_id
    if not expected:
        return
    tenant = _string_claim(claims, "tid")
    if tenant is None or tenant.lower() != expected.lower():
        raise InvalidToken("Invalid or expired token")


def _check_scope(claims: dict[str, Any], settings: Settings) -> None:
    required = settings.entra_required_scope
    if not required:
        return
    scopes = set(str(claims.get("scp") or "").split())
    roles = claims.get("roles") or []
    role_set = set(roles) if isinstance(roles, list) else set()
    if required not in scopes and required not in role_set:
        raise InvalidToken("Invalid or expired token")


def _check_authorized_party(claims: dict[str, Any], settings: Settings) -> None:
    if not settings.entra_allowed_azp:
        return
    allowed = {part.strip() for part in settings.entra_allowed_azp.split(",") if part.strip()}
    presented = _string_claim(claims, "azp") or _string_claim(claims, "appid")
    if presented not in allowed:
        raise InvalidToken("Invalid or expired token")

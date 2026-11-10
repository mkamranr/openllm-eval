"""Sign-in, sign-out, the current user, password changes, and personal API tokens."""

from __future__ import annotations

import base64
import binascii
import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_redis, get_session
from app.auth.dependencies import Principal, check_csrf, get_principal, require_viewer
from app.auth.tokens import SESSION_COOKIE
from app.core.config import Settings
from app.core.exceptions import AppError, error_body
from app.db.models.auth import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MeRead,
    ProvidersRead,
    TokenCreate,
    TokenCreated,
    TokenRead,
    UserRead,
)
from app.services import audit_service, auth_service, auth_settings_service

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_PATH = "/api"


def _secure(request: Request, settings: Settings) -> bool:
    if settings.auth_cookie_secure != "auto":
        return settings.auth_cookie_secure == "true"
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _set_cookie(response: Response, request: Request, settings: Settings, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.auth_session_max_hours * 3600,
        path=COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=_secure(request, settings),
    )


def _me(principal: Principal, settings: Settings) -> MeRead:
    return MeRead(
        auth_enabled=settings.auth_enabled,
        auth_method=principal.method,
        user=UserRead.model_validate(principal.user) if principal.user else None,
        username=principal.username,
        role=principal.role,  # type: ignore[arg-type]
        must_change_password=principal.must_change_password,
    )


def _me_for(user: User, settings: Settings, method: str) -> MeRead:
    return _me(
        Principal(user=user, username=user.username, role=user.role, method=method), settings
    )


@router.get("/providers", response_model=ProvidersRead, summary="Sign-in options (public)")
async def providers(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_app_settings)
) -> ProvidersRead:
    config = await auth_settings_service.load(session, settings, secrets=False)
    return ProvidersRead(
        auth_enabled=settings.auth_enabled,
        local=True,
        ldap=config.ldap.enabled,
        kerberos=config.ldap.enabled and config.kerberos.enabled,
    )


@router.post("/login", response_model=MeRead)
async def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    redis: Redis = Depends(get_redis),
) -> MeRead:
    """Sign in with a local or directory username and password; sets the session cookie."""
    check_csrf(request, settings)
    user, token = await auth_service.login(
        session, settings, redis, request, data.username, data.password.get_secret_value()
    )
    _set_cookie(response, request, settings, token)
    return _me_for(user, settings, "session")


@router.get(
    "/negotiate",
    response_model=MeRead,
    summary="Windows single sign-on (Kerberos / SPNEGO)",
    responses={401: {"description": "Negotiate challenge"}},
)
async def negotiate(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "negotiate" or not value.strip():
        config = await auth_settings_service.load(session, settings, secrets=False)
        if not (settings.auth_enabled and config.ldap.enabled and config.kerberos.enabled):
            raise AppError(
                "Windows single sign-on is not enabled.", code="sso_disabled", status_code=404
            )
        return JSONResponse(
            status_code=401,
            content=error_body("negotiate", "Windows sign-in challenge.", request),
            headers={"WWW-Authenticate": "Negotiate"},
        )
    try:
        ticket = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise AppError("Malformed Negotiate header.", code="bad_request", status_code=400) from None
    user, token, reply = await auth_service.login_kerberos(session, settings, request, ticket)
    me = _me_for(user, settings, "session")
    response = JSONResponse(content=me.model_dump(mode="json"))
    if reply:
        response.headers["WWW-Authenticate"] = "Negotiate " + base64.b64encode(reply).decode()
    _set_cookie(response, request, settings, token)
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await auth_service.end_session(session, principal)
    if principal.user is not None:
        audit_service.record(
            session,
            "logout",
            actor_id=principal.user_id,
            actor_name=principal.username,
            request=request,
        )
    await session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH)
    return response


@router.get("/me", response_model=MeRead)
async def me(
    principal: Principal = Depends(get_principal), settings: Settings = Depends(get_app_settings)
) -> MeRead:
    return _me(principal, settings)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: ChangePasswordRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    await auth_service.change_password(
        session,
        settings,
        request,
        principal,
        data.current_password.get_secret_value(),
        data.new_password.get_secret_value(),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tokens", response_model=list[TokenRead])
async def list_tokens(
    principal: Principal = Depends(require_viewer), session: AsyncSession = Depends(get_session)
) -> list[TokenRead]:
    if principal.user is None:
        return []
    return await auth_service.list_tokens(session, principal.user.id)


@router.post("/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    data: TokenCreate,
    request: Request,
    principal: Principal = Depends(require_viewer),
    session: AsyncSession = Depends(get_session),
) -> TokenCreated:
    """Create a personal API token. The token is returned once and never shown again."""
    return await auth_service.create_token(session, request, principal, data)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_viewer),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await auth_service.revoke_token(session, request, principal, token_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

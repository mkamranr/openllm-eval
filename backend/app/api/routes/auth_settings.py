"""Settings > Authentication: Active Directory / LDAP and Kerberos (admin role)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_session
from app.auth.dependencies import Principal, get_principal
from app.auth.ldap import DirectoryError, InvalidCredentialsError, map_role, normalize_username
from app.core.config import Settings
from app.schemas.auth import (
    AuthSettingsRead,
    AuthSettingsUpdate,
    DirectoryTestRequest,
    DirectoryTestResult,
)
from app.services import audit_service, auth_settings_service

router = APIRouter(prefix="/settings/auth", tags=["settings"])


@router.get("", response_model=AuthSettingsRead)
async def get_auth_settings(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_app_settings)
) -> AuthSettingsRead:
    """Directory settings. Secrets are never returned, only whether they are set."""
    return await auth_settings_service.read(session, settings)


@router.put("", response_model=AuthSettingsRead)
async def update_auth_settings(
    data: AuthSettingsUpdate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> AuthSettingsRead:
    result, changed = await auth_settings_service.update(
        session, settings, data, actor=principal.username
    )
    audit_service.record(
        session,
        "auth_settings_updated",
        actor_id=principal.user_id,
        actor_name=principal.username,
        target="authentication",
        request=request,
        details={"changed": changed},
    )
    await session.commit()
    return result


@router.post("/test-connection", response_model=DirectoryTestResult)
async def test_connection(
    request: Request,
    data: DirectoryTestRequest | None = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> DirectoryTestResult:
    """Connect, bind with the service account, and find the search base."""
    cfg, password = await auth_settings_service.merged_ldap(
        session, settings, data.ldap if data else None
    )
    directory = auth_settings_service.directory(request, settings, cfg, password)
    try:
        details = await asyncio.to_thread(directory.test_connection)
    except DirectoryError as exc:
        return DirectoryTestResult(ok=False, message=str(exc))
    return DirectoryTestResult(
        ok=True, message="Connected and found the search base.", details=details
    )


@router.post("/test-user", response_model=DirectoryTestResult)
async def test_user(
    data: DirectoryTestRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> DirectoryTestResult:
    """Check a directory user's password and show the groups and role they would get. No
    account or session is created."""
    if not data.username:
        return DirectoryTestResult(ok=False, message="Enter a username.")
    cfg, password = await auth_settings_service.merged_ldap(session, settings, data.ldap)
    directory = auth_settings_service.directory(request, settings, cfg, password)
    username = normalize_username(data.username)
    try:
        if data.password is not None and data.password.get_secret_value():
            found = await asyncio.to_thread(
                directory.authenticate, username, data.password.get_secret_value()
            )
            checked = "password accepted"
        else:
            found = await asyncio.to_thread(directory.lookup, username)
            checked = "found (password not checked)"
    except InvalidCredentialsError:
        return DirectoryTestResult(ok=False, message="Unknown user or wrong password.")
    except DirectoryError as exc:
        return DirectoryTestResult(ok=False, message=str(exc))
    role = map_role(found.groups, cfg)
    return DirectoryTestResult(
        ok=True,
        message=f"{found.username}: {checked}; "
        + (f"would sign in as {role}." if role else "would be refused (no allowed group)."),
        details={
            "dn": found.dn,
            "username": found.username,
            "display_name": found.display_name,
            "email": found.email,
            "groups": found.groups,
            "role": role,
        },
    )

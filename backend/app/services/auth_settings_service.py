"""Directory sign-in settings (Settings > Authentication). Secrets are write-only."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.crypto import SecretBox
from app.auth.kerberos import Acceptor, GssapiAcceptor, gssapi_available
from app.auth.ldap import ConnectionFactory, LdapDirectory
from app.core.config import Settings
from app.core.exceptions import AppError
from app.db.models.auth import AuthSettings
from app.schemas.auth import (
    AuthSettingsRead,
    AuthSettingsUpdate,
    KerberosSettings,
    KerberosSettingsRead,
    LdapSettings,
    LdapSettingsIn,
    LdapSettingsRead,
)

PURPOSE_BIND = "ldap_bind_password"
PURPOSE_KEYTAB = "kerberos_keytab"
MAX_KEYTAB_BYTES = 65_536


@dataclass
class DirectoryConfig:
    ldap: LdapSettings
    kerberos: KerberosSettings
    bind_password: str | None
    keytab: bytes | None


def secret_box(settings: Settings) -> SecretBox:
    key = settings.auth_secret_key.get_secret_value() if settings.auth_secret_key else None
    return SecretBox(key)


async def _row(session: AsyncSession) -> AuthSettings | None:
    return await session.get(AuthSettings, 1)


def _ldap(row: AuthSettings | None) -> LdapSettings:
    return LdapSettings.model_validate((row.ldap if row else None) or {})


def _kerberos(row: AuthSettings | None) -> KerberosSettings:
    return KerberosSettings.model_validate((row.kerberos if row else None) or {})


async def load(
    session: AsyncSession, settings: Settings, *, secrets: bool = True
) -> DirectoryConfig:
    row = await _row(session)
    box = secret_box(settings)
    bind_password = keytab = None
    if secrets and row is not None:
        if row.ldap_bind_password_enc:
            bind_password = box.decrypt(row.ldap_bind_password_enc, purpose=PURPOSE_BIND).decode()
        if row.kerberos_keytab_enc:
            keytab = box.decrypt(row.kerberos_keytab_enc, purpose=PURPOSE_KEYTAB)
    return DirectoryConfig(_ldap(row), _kerberos(row), bind_password, keytab)


async def read(session: AsyncSession, settings: Settings) -> AuthSettingsRead:
    row = await _row(session)
    return AuthSettingsRead(
        ldap=LdapSettingsRead(
            **_ldap(row).model_dump(), bind_password_set=bool(row and row.ldap_bind_password_enc)
        ),
        kerberos=KerberosSettingsRead(
            **_kerberos(row).model_dump(),
            keytab_set=bool(row and row.kerberos_keytab_enc),
            library_available=gssapi_available(),
        ),
        secret_key_configured=secret_box(settings).available,
        updated_by=row.updated_by if row else None,
        updated_at=row.updated_at if row else None,
    )


def _decode_keytab(value: str) -> bytes:
    try:
        data = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise AppError(
            "The keytab must be base64-encoded.", code="validation_error", status_code=422
        ) from None
    if not data or len(data) > MAX_KEYTAB_BYTES:
        raise AppError(
            "The keytab is empty or larger than 64 KB.", code="validation_error", status_code=422
        )
    # MIT keytab files start with 0x05 0x02 (version 2) or 0x05 0x01.
    if data[0] != 0x05:
        raise AppError(
            "This does not look like a keytab file.", code="validation_error", status_code=422
        )
    return data


def _plain(model: LdapSettingsIn) -> dict[str, Any]:
    return LdapSettings.model_validate(
        model.model_dump(exclude={"bind_password", "clear_bind_password"})
    ).model_dump()


async def update(
    session: AsyncSession, settings: Settings, data: AuthSettingsUpdate, *, actor: str
) -> tuple[AuthSettingsRead, list[str]]:
    """Save the settings; returns the new view and the names of the changed fields."""
    row = await _row(session)
    if row is None:
        row = AuthSettings(id=1, ldap={}, kerberos={})
        session.add(row)
    box = secret_box(settings)
    ldap = _plain(data.ldap)
    kerberos = KerberosSettings.model_validate(
        data.kerberos.model_dump(exclude={"keytab_base64", "clear_keytab"})
    ).model_dump()

    if ldap["enabled"]:
        if not ldap["server_urls"]:
            raise AppError(
                "Add at least one directory server URL.", code="validation_error", status_code=422
            )
        if not ldap["user_search_base"]:
            raise AppError("Set the user search base.", code="validation_error", status_code=422)
    if kerberos["enabled"] and not ldap["enabled"]:
        raise AppError(
            "Kerberos sign-in looks users up in the directory for their groups; enable LDAP too.",
            code="validation_error",
            status_code=422,
        )

    changed = sorted(
        {k for k in ldap if (row.ldap or {}).get(k) != ldap[k]}
        | {f"kerberos.{k}" for k in kerberos if (row.kerberos or {}).get(k) != kerberos[k]}
    )
    if data.ldap.clear_bind_password:
        if row.ldap_bind_password_enc:
            changed.append("bind_password")
        row.ldap_bind_password_enc = None
    elif data.ldap.bind_password is not None and data.ldap.bind_password.get_secret_value():
        row.ldap_bind_password_enc = box.encrypt(
            data.ldap.bind_password.get_secret_value().encode(), purpose=PURPOSE_BIND
        )
        changed.append("bind_password")
    if data.kerberos.clear_keytab:
        if row.kerberos_keytab_enc:
            changed.append("kerberos.keytab")
        row.kerberos_keytab_enc = None
    elif data.kerberos.keytab_base64 is not None and data.kerberos.keytab_base64.get_secret_value():
        keytab = _decode_keytab(data.kerberos.keytab_base64.get_secret_value())
        row.kerberos_keytab_enc = box.encrypt(keytab, purpose=PURPOSE_KEYTAB)
        changed.append("kerberos.keytab")
    if kerberos["enabled"] and not row.kerberos_keytab_enc:
        raise AppError(
            "Upload the service keytab to enable Kerberos sign-in.",
            code="validation_error",
            status_code=422,
        )

    row.ldap = ldap
    row.kerberos = kerberos
    row.updated_by = actor
    await session.flush()
    return await read(session, settings), changed


async def disable_ldap(session: AsyncSession) -> bool:
    row = await _row(session)
    if row is None:
        return False
    row.ldap = {**(row.ldap or {}), "enabled": False}
    row.kerberos = {**(row.kerberos or {}), "enabled": False}
    await session.flush()
    return True


# ------------------------------------------------------------------------------ factories
def directory(
    request: Request | None, settings: Settings, cfg: LdapSettings, bind_password: str | None
) -> LdapDirectory:
    factory: ConnectionFactory | None = None
    if request is not None:
        factory = getattr(request.app.state, "ldap_connection_factory", None)
    return LdapDirectory(
        cfg,
        bind_password,
        blocked_hosts=tuple(settings.endpoint_blocked_hosts),
        connection_factory=factory,
    )


def acceptor(request: Request, kerberos: KerberosSettings, keytab: bytes) -> Acceptor:
    custom = getattr(request.app.state, "kerberos_acceptor_factory", None)
    if custom is not None:
        return custom(kerberos, keytab)  # type: ignore[no-any-return]
    return GssapiAcceptor(kerberos.service_principal, keytab)


async def merged_ldap(
    session: AsyncSession, settings: Settings, override: LdapSettingsIn | None
) -> tuple[LdapSettings, str | None]:
    """Settings to test: the unsaved form values (if given) with the stored bind password as
    a fallback."""
    stored = await load(session, settings)
    if override is None:
        return stored.ldap, stored.bind_password
    password = stored.bind_password
    if override.clear_bind_password:
        password = None
    elif override.bind_password is not None and override.bind_password.get_secret_value():
        password = override.bind_password.get_secret_value()
    return LdapSettings.model_validate(_plain(override)), password

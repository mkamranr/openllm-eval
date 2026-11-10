"""Request and response models for authentication, users, directory settings, and audit."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

Role = Literal["viewer", "editor", "admin"]
LOCAL_USERNAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
DEFAULT_AD_FILTER = "(&(objectClass=user)(sAMAccountName={username}))"


# ----------------------------------------------------------------------------- directory settings
class RoleMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A group DN ("CN=LLM Admins,OU=Groups,DC=corp,DC=example,DC=com") or just its CN.
    group: str = Field(min_length=1, max_length=1000)
    role: Role

    @field_validator("group")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class LdapSettings(BaseModel):
    """Active Directory / LDAP sign-in. No secrets here: the bind password is stored apart."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Tried in order (failover). ldaps:// (636) is recommended; ldap:// with start_tls also works.
    server_urls: list[str] = Field(default_factory=list, max_length=5)
    start_tls: bool = False
    verify_tls: bool = True
    # PEM CA bundle for the directory's certificate (internal CA). Empty = system CAs.
    ca_cert_pem: str | None = Field(default=None, max_length=65_536)
    # Service account used to search for users. Empty = anonymous search.
    bind_dn: str | None = Field(default=None, max_length=1000)
    user_search_base: str = Field(default="", max_length=1000)
    user_filter: str = Field(default=DEFAULT_AD_FILTER, max_length=1000)
    username_attribute: str = Field(default="sAMAccountName", max_length=100)
    display_name_attribute: str = Field(default="displayName", max_length=100)
    email_attribute: str = Field(default="mail", max_length=100)
    group_attribute: str = Field(default="memberOf", max_length=100)
    # Also count groups that contain the user's groups (followed up to 8 levels).
    nested_groups: bool = False
    timeout_seconds: float = Field(default=5.0, ge=1, le=30)
    role_mappings: list[RoleMapping] = Field(default_factory=list, max_length=100)
    # Empty = any directory user who authenticates may sign in.
    allowed_groups: list[str] = Field(default_factory=list, max_length=100)
    # Role for allowed users that no mapping matches; "deny" refuses them.
    default_role: Literal["viewer", "editor", "deny"] = "viewer"

    @field_validator("server_urls")
    @classmethod
    def _urls(cls, value: list[str]) -> list[str]:
        out = []
        for url in value:
            url = url.strip()
            if not url:
                continue
            if not url.lower().startswith(("ldap://", "ldaps://")):
                raise ValueError("server URLs must start with ldap:// or ldaps://")
            if "@" in url:
                raise ValueError("server URLs must not contain credentials")
            out.append(url)
        return out

    @field_validator("user_filter")
    @classmethod
    def _filter(cls, value: str) -> str:
        value = value.strip()
        if "{username}" not in value:
            raise ValueError("the user filter must contain {username}")
        if not (value.startswith("(") and value.endswith(")")):
            raise ValueError("the user filter must be enclosed in parentheses")
        return value

    @field_validator("ca_cert_pem")
    @classmethod
    def _pem(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if "-----BEGIN CERTIFICATE-----" not in value:
            raise ValueError("the CA certificate must be PEM (-----BEGIN CERTIFICATE-----)")
        return value.strip() + "\n"

    @field_validator("allowed_groups")
    @classmethod
    def _groups(cls, value: list[str]) -> list[str]:
        return [g.strip() for g in value if g.strip()]

    @field_validator("bind_dn")
    @classmethod
    def _blank_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None


class KerberosSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # e.g. HTTP/openllm.corp.example.com@CORP.EXAMPLE.COM. Empty = any key in the keytab.
    service_principal: str | None = Field(default=None, max_length=300)

    @field_validator("service_principal")
    @classmethod
    def _spn(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        value = value.strip()
        if not value.upper().startswith("HTTP/"):
            raise ValueError("the service principal must look like HTTP/host.example.com")
        return value


class LdapSettingsIn(LdapSettings):
    # Write-only: None keeps the stored password; clear_bind_password removes it.
    bind_password: SecretStr | None = Field(default=None, max_length=1024)
    clear_bind_password: bool = False


class KerberosSettingsIn(KerberosSettings):
    # Write-only base64 keytab (at most ~64 KB). None keeps the stored keytab.
    keytab_base64: SecretStr | None = Field(default=None, max_length=90_000)
    clear_keytab: bool = False


class AuthSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ldap: LdapSettingsIn
    kerberos: KerberosSettingsIn = Field(default_factory=KerberosSettingsIn)


class LdapSettingsRead(LdapSettings):
    bind_password_set: bool


class KerberosSettingsRead(KerberosSettings):
    keytab_set: bool
    # False when the gssapi library or the system Kerberos libraries are missing.
    library_available: bool


class AuthSettingsRead(BaseModel):
    ldap: LdapSettingsRead
    kerberos: KerberosSettingsRead
    secret_key_configured: bool
    updated_by: str | None
    updated_at: datetime | None


class DirectoryTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Settings to test (unsaved form values). Omitted = the saved settings. A missing
    # bind_password falls back to the stored one.
    ldap: LdapSettingsIn | None = None
    username: str | None = Field(default=None, max_length=256)
    password: SecretStr | None = Field(default=None, max_length=1024)


class DirectoryTestResult(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


# ----------------------------------------------------------------------------------- users
class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    display_name: str | None
    email: str | None
    source: str
    role: Role
    role_pinned: bool
    must_change_password: bool
    disabled: bool
    groups: list[str]
    last_login_at: datetime | None
    created_at: datetime


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(pattern=LOCAL_USERNAME_PATTERN)
    display_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    role: Role = "viewer"
    # Omitted = a random password is generated and returned once.
    password: SecretStr | None = Field(default=None, max_length=256)
    must_change_password: bool = True

    @field_validator("username", mode="before")
    @classmethod
    def _lower(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    role: Role | None = None
    role_pinned: bool | None = None
    disabled: bool | None = None


class UserCreated(UserRead):
    # Present only when the server generated the password. Shown once.
    initial_password: str | None = None


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: SecretStr | None = Field(default=None, max_length=256)


class PasswordResetResult(BaseModel):
    temporary_password: str | None
    must_change_password: bool


# ------------------------------------------------------------------------------ sign-in
class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(max_length=1024)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr = Field(max_length=1024)
    new_password: SecretStr = Field(max_length=256)


class MeRead(BaseModel):
    auth_enabled: bool
    auth_method: str
    user: UserRead | None
    username: str
    role: Role
    must_change_password: bool


class ProvidersRead(BaseModel):
    auth_enabled: bool
    local: bool
    ldap: bool
    kerberos: bool


# ------------------------------------------------------------------------------- API tokens
class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    # None = never expires.
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)


class TokenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class TokenCreated(TokenRead):
    # The token itself. Shown once; only its hash is stored.
    token: str


# ------------------------------------------------------------------------------------- audit
class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    at: datetime
    actor_user_id: uuid.UUID | None
    actor_name: str | None
    action: str
    target: str | None
    ip: str | None
    details: dict[str, Any]

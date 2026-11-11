"""Server-side administration and recovery commands (run inside the API container).

    docker compose exec api python -m app.cli.admin list-users
    docker compose exec api python -m app.cli.admin reset-password admin
    docker compose exec -T api python -m app.cli.admin create-token --service-user smoke --ttl 1h

These talk to the database directly (no API token needed), so they work when nobody can sign
in: a forgotten admin password, a locked account, or a broken directory configuration.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError

app = typer.Typer(
    name="openllm-eval-admin",
    help="Server-side user administration and recovery.",
    no_args_is_help=True,
)

ROLE_HELP = "viewer, editor, or admin"


@app.callback()
def _main() -> None:
    # Log lines (warnings, audit) go to stderr, so stdout carries only the command's output
    # (e.g. the token printed by create-token).
    from app.core.logging import configure_logging

    configure_logging("WARNING", "console")


def _run(fn: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
    from app.db.session import create_engine, create_session_factory

    async def main() -> Any:
        engine = create_engine(get_settings())
        try:
            async with create_session_factory(engine)() as session:
                return await fn(session)
        finally:
            await engine.dispose()

    try:
        return asyncio.run(main())
    except AppError as exc:
        typer.echo(f"error: {exc.message}", err=True)
        raise typer.Exit(code=1) from None


def _role(value: str) -> str:
    value = value.strip().lower()
    if value not in ("viewer", "editor", "admin"):
        typer.echo(f"error: role must be {ROLE_HELP}", err=True)
        raise typer.Exit(code=2)
    return value


def _ttl(value: str) -> timedelta | None:
    if value.lower() in ("never", "none", "0"):
        return None
    match = re.fullmatch(r"(\d+)([mhd])", value.strip().lower())
    if not match:
        typer.echo("error: --ttl must look like 30m, 1h, 90d, or never", err=True)
        raise typer.Exit(code=2)
    amount, unit = int(match[1]), match[2]
    return {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]


def _read_password(from_stdin: bool) -> str | None:
    if not from_stdin:
        return None
    value = sys.stdin.readline().rstrip("\r\n")
    if not value:
        typer.echo("error: no password on standard input", err=True)
        raise typer.Exit(code=2)
    return value


async def _user(session: AsyncSession, username: str):  # type: ignore[no-untyped-def]
    from app.db.models.auth import User

    user = await session.scalar(select(User).where(User.username == username.strip().lower()))
    if user is None:
        typer.echo(f"error: no user named {username!r}", err=True)
        raise typer.Exit(code=1)
    return user


@app.command("list-users")
def list_users() -> None:
    """List users with their role, source, and status."""
    from app.db.models.auth import User

    async def go(session: AsyncSession) -> None:
        for u in (await session.scalars(select(User).order_by(User.username))).all():
            flags = [
                f
                for f, on in (
                    ("disabled", u.disabled),
                    ("must-change-password", u.must_change_password),
                )
                if on
            ]
            typer.echo(f"{u.username:<32} {u.role:<7} {u.source:<8} {' '.join(flags)}")

    _run(go)


@app.command("create-user")
def create_user(
    username: str = typer.Argument(..., help="Lower-case letters, digits, . _ -"),
    role: str = typer.Option("viewer", help=ROLE_HELP),
    password_stdin: bool = typer.Option(
        False, "--password-stdin", help="Read the password from standard input."
    ),
    must_change: bool = typer.Option(
        True, "--must-change/--no-must-change", help="Require a new password at first sign-in."
    ),
) -> None:
    """Create a local user. Without --password-stdin a random password is printed once."""
    from pydantic import SecretStr, ValidationError

    from app.schemas.auth import UserCreate
    from app.services import user_service

    password = _read_password(password_stdin)
    try:
        data = UserCreate(
            username=username,
            role=_role(role),  # type: ignore[arg-type]
            password=SecretStr(password) if password else None,
            must_change_password=must_change,
        )
    except ValidationError:
        typer.echo("error: invalid username (lower-case letters, digits, . _ -; max 64)", err=True)
        raise typer.Exit(code=2) from None

    async def go(session: AsyncSession) -> None:
        created = await user_service.create_local_user(
            session, get_settings(), data, actor=None, request=None
        )
        typer.echo(f"created {created.username} ({created.role})")
        if created.initial_password:
            typer.echo(f"password: {created.initial_password}")

    _run(go)


@app.command("check-password")
def check_password(username: str) -> None:
    """Exit 0 if the password on standard input is the user's current password, else 1.
    Prints nothing about the password; used by deploy.sh before it shows ADMIN_INITIAL_PASSWORD."""
    from app.auth import passwords

    supplied = _read_password(True) or ""

    async def go(session: AsyncSession) -> bool:
        user = await _user(session, username)
        return passwords.verify_password(user.password_hash, supplied)

    if not _run(go):
        typer.echo("password does not match", err=True)
        raise typer.Exit(code=1)
    typer.echo("password matches")


@app.command("reset-password")
def reset_password(
    username: str,
    password_stdin: bool = typer.Option(
        False, "--password-stdin", help="Read the new password from standard input."
    ),
) -> None:
    """Set a new password for a local user (printed once if generated); they must change it at
    the next sign-in. Also unlocks nothing: use 'unlock' for lockouts."""
    from app.auth import passwords
    from app.auth.dependencies import now
    from app.auth.tokens import generate_password
    from app.db.models.auth import SOURCE_LOCAL
    from app.services import audit_service, auth_service

    supplied = _read_password(password_stdin)

    async def go(session: AsyncSession) -> None:
        user = await _user(session, username)
        if user.source != SOURCE_LOCAL:
            typer.echo("error: only local users have a password here", err=True)
            raise typer.Exit(code=1)
        password = supplied or generate_password()
        if supplied:
            passwords.check_policy(supplied, username=user.username)
        user.password_hash = passwords.hash_password(password)
        user.must_change_password = True
        user.disabled = False
        user.password_changed_at = now()
        await auth_service.end_all_sessions(session, user.id)
        audit_service.record(session, "password_reset", actor_name="cli", target=user.username)
        await session.commit()
        typer.echo(f"password reset for {user.username}; it must be changed at the next sign-in")
        if not supplied:
            typer.echo(f"password: {password}")

    _run(go)


@app.command("set-role")
def set_role(username: str, role: str = typer.Argument(..., help=ROLE_HELP)) -> None:
    """Change a user's role (directory users get their role pinned)."""
    from app.db.models.auth import SOURCE_LOCAL
    from app.services import audit_service

    value = _role(role)

    async def go(session: AsyncSession) -> None:
        user = await _user(session, username)
        user.role = value
        if user.source != SOURCE_LOCAL:
            user.role_pinned = True
        audit_service.record(
            session, "user_updated", actor_name="cli", target=user.username, details={"role": value}
        )
        await session.commit()
        typer.echo(f"{user.username} is now {value}")

    _run(go)


@app.command("enable-user")
def enable_user(username: str) -> None:
    """Re-enable a disabled user."""
    from app.services import audit_service

    async def go(session: AsyncSession) -> None:
        user = await _user(session, username)
        user.disabled = False
        audit_service.record(
            session,
            "user_updated",
            actor_name="cli",
            target=user.username,
            details={"disabled": False},
        )
        await session.commit()
        typer.echo(f"{user.username} enabled")

    _run(go)


@app.command("delete-user")
def delete_user(username: str) -> None:
    """Delete a user (their runs are kept)."""
    from app.services import audit_service

    async def go(session: AsyncSession) -> None:
        user = await _user(session, username)
        await session.delete(user)
        audit_service.record(session, "user_deleted", actor_name="cli", target=username.lower())
        await session.commit()
        typer.echo(f"deleted {username.lower()}")

    _run(go)


@app.command("create-token")
def create_token(
    user: str | None = typer.Option(None, "--user", help="Existing user to issue the token for."),
    service_user: str | None = typer.Option(
        None, "--service-user", help="Create (or reuse) a password-less service account."
    ),
    role: str = typer.Option("admin", help=f"Role of a new service account ({ROLE_HELP})."),
    name: str = typer.Option("cli", help="Token name."),
    ttl: str = typer.Option("1h", help="Lifetime: 30m, 1h, 90d, or never."),
) -> None:
    """Print a new API token (only the token, for scripts)."""
    from app.db.models.auth import SOURCE_SERVICE, User
    from app.services import audit_service, auth_service

    if bool(user) == bool(service_user):
        typer.echo("error: give exactly one of --user or --service-user", err=True)
        raise typer.Exit(code=2)
    lifetime = _ttl(ttl)
    value = _role(role)

    async def go(session: AsyncSession) -> None:
        if user:
            owner = await _user(session, user)
        else:
            username = (service_user or "").strip().lower()
            owner = await session.scalar(select(User).where(User.username == username))
            if owner is None:
                owner = User(
                    username=username,
                    source=SOURCE_SERVICE,
                    role=value,
                    role_pinned=False,
                    must_change_password=False,
                    disabled=False,
                    groups=[],
                )
                session.add(owner)
                await session.flush()
            elif owner.source != SOURCE_SERVICE:
                typer.echo(f"error: {username!r} is not a service account", err=True)
                raise typer.Exit(code=1)
        created = await auth_service.issue_token(session, owner, name, lifetime)
        audit_service.record(
            session,
            "token_created",
            actor_name="cli",
            target=owner.username,
            details={"prefix": created.prefix, "name": name},
        )
        await session.commit()
        typer.echo(created.token)

    _run(go)


@app.command("unlock")
def unlock(
    username: str | None = typer.Argument(None),
    ip: str | None = typer.Option(None, "--ip", help="Also unlock this client address."),
) -> None:
    """Clear the failed sign-in lock for a username and/or client address."""
    from app.auth.lockout import Lockout
    from app.core.lifecycle import create_redis

    async def go() -> None:
        settings = get_settings()
        redis = create_redis(settings)
        try:
            await Lockout(redis, settings).unlock(username=username, ip=ip)
        finally:
            await redis.aclose()

    asyncio.run(go())
    typer.echo("unlocked")


@app.command("disable-ldap")
def disable_ldap() -> None:
    """Turn off directory and Kerberos sign-in (local accounts keep working)."""
    from app.services import audit_service, auth_settings_service

    async def go(session: AsyncSession) -> None:
        changed = await auth_settings_service.disable_ldap(session)
        audit_service.record(
            session, "auth_settings_updated", actor_name="cli", details={"changed": ["enabled"]}
        )
        await session.commit()
        typer.echo(
            "directory sign-in disabled" if changed else "directory sign-in was not configured"
        )

    _run(go)


if __name__ == "__main__":
    app()

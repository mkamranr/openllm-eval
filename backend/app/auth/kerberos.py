"""Kerberos single sign-on (SPNEGO "Negotiate") for domain-joined browsers.

The gssapi library (MIT krb5) is imported lazily so the rest of the application works without
it. Tests replace the acceptor with a fake.
"""

from __future__ import annotations

import os
import tempfile
from typing import Protocol


class KerberosError(Exception):
    """The ticket was not accepted (safe message; no ticket contents)."""


class Acceptor(Protocol):
    def accept(self, token: bytes) -> tuple[str, bytes | None]:
        """Return the client principal (``alice@CORP.EXAMPLE.COM``) and an optional reply token."""
        ...


def gssapi_available() -> bool:
    try:
        import gssapi  # noqa: F401
    except Exception:
        return False
    return True


class GssapiAcceptor:
    def __init__(self, service_principal: str | None, keytab: bytes) -> None:
        self.service_principal = service_principal
        self.keytab = keytab

    def accept(self, token: bytes) -> tuple[str, bytes | None]:
        try:
            import gssapi
        except Exception as exc:
            raise KerberosError("The Kerberos library (gssapi) is not installed.") from exc
        fd, path = tempfile.mkstemp(prefix="oel-keytab-")
        try:
            os.chmod(path, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(self.keytab)
            name = (
                gssapi.Name(self.service_principal, gssapi.NameType.kerberos_principal)
                if self.service_principal
                else None
            )
            creds = gssapi.Credentials(name=name, usage="accept", store={"keytab": path})
            ctx = gssapi.SecurityContext(creds=creds, usage="accept")
            reply = ctx.step(token)
            if not ctx.complete:
                raise KerberosError("The Kerberos handshake needs more rounds than supported.")
            return str(ctx.initiator_name), reply
        except KerberosError:
            raise
        except Exception as exc:
            raise KerberosError(
                f"The Kerberos ticket was not accepted ({type(exc).__name__})."
            ) from exc
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def principal_username(principal: str) -> str:
    """``alice@CORP.EXAMPLE.COM`` -> ``alice``."""
    return principal.split("@", 1)[0].strip().lower()

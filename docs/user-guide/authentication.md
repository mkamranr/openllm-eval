# Authentication and roles

Sign-in is required by default. Everyone has one of three roles:

| Role | Can |
|---|---|
| `viewer` | Read everything: endpoints, datasets, runs, results, reports, comparisons. Download reports |
| `editor` | Everything a viewer can, plus create, change and delete endpoints, datasets, profiles, runs, reports and comparisons |
| `admin` | Everything an editor can, plus users, authentication settings and the audit log |

Roles are enforced by the API on every request. The UI hides actions your role cannot perform,
but that is only a convenience.

Accounts come from two places:

- **Local accounts**, with a password stored in OpenLLM-Eval (argon2id).
- **Windows / Active Directory accounts.** Users sign in with their domain username and password,
  checked against the directory over LDAP/LDAPS. Optionally, they can sign in without a password
  through Kerberos single sign-on ("Sign in with Windows").

## First sign-in

On a first install, `deploy.sh` generates `AUTH_SECRET_KEY` and `ADMIN_INITIAL_PASSWORD` in
`.env` and prints the administrator's password at the end:

```
Sign in at http://<this host>:3000 as 'admin' with password: …
```

Sign in with it. You must choose a new password before you can do anything else. The new
password must have at least 12 characters, at least 5 different characters, and must not
contain the username or be the initial password. The initial password is never used again, so
you may then delete it from `.env`.

The administrator is created only when the database has no users at all. Upgrading from
Milestone 7 creates it the same way.

## Sessions, API tokens and sign-in protection

- **Browser sessions** use an HttpOnly cookie (`oel_session`, SameSite=Lax). It is marked Secure
  when the browser reached the UI over HTTPS, including through a TLS proxy that sends
  `X-Forwarded-Proto: https`. A session ends after `AUTH_SESSION_IDLE_MINUTES` of inactivity
  (default 8 hours) and in any case after `AUTH_SESSION_MAX_HOURS` (default 72).
- **API tokens** are for the CLI and scripts. Create them under Settings → Account, or with
  `openllm-eval auth login`. A token is shown once; only its hash is stored. It acts with its
  owner's current role, can expire, and can be revoked. Send it as
  `Authorization: Bearer oel_…`.
- **Lockout.** After 5 failed sign-ins for a username within 15 minutes, that username is locked
  for 15 minutes. A client address is locked after 20 failures. See `AUTH_LOCKOUT_*` in the
  [configuration reference](../reference/configuration.md).
- **CSRF.** Requests that change data with a session cookie must carry the
  `X-OpenLLM-CSRF: 1` header and come from the UI's own origin. The UI does this for you. Add
  other host names the UI is published under to `AUTH_TRUSTED_ORIGINS`.
- **Audit log.** Sign-ins (successful and failed), sign-outs, password changes and resets, user
  and role changes, token creation and revocation, authentication-settings changes and report
  downloads are recorded under Settings → Audit log.

## Managing users (Settings → Users, admin)

- **Create a local user.** Leave the password empty to generate one, which is shown once. By
  default the user must change it at first sign-in.
- **Change a role.** For a directory user, changing the role also *pins* it: later sign-ins no
  longer re-evaluate the user's groups. Clear "pinned" to go back to the group mapping.
- **Disable** an account to block sign-in, end its sessions and stop its API tokens, without deleting its
  history. **Sign out everywhere** ends all of a user's sessions.
- **Reset password** (local users only) gives a temporary password and requires a change at the
  next sign-in.
- You cannot disable, demote or delete your own account, or the last active administrator.

Runs record who started them; this is shown as "Started by" on the run page.

## Active Directory / LDAP (Settings → Authentication, admin)

1. **Create a service account** in AD with read access to users and groups, for example
   `CN=svc-openllm,OU=Service Accounts,DC=corp,DC=example`. Ordinary domain users can read the
   directory, so no extra rights are usually needed.
2. **Fill in the form:**
   - **Server URLs**: one or more, tried in order, e.g. `ldaps://dc1.corp.example:636`,
     `ldaps://dc2.corp.example:636`. Use LDAPS, or `ldap://` with **StartTLS**. Plain LDAP sends
     passwords in clear text.
   - **Verify TLS** stays on. If your domain controllers use an internal CA, paste its
     certificate (PEM) into **CA certificate**.
   - **Bind DN** and **bind password**: the service account. The password is write-only. It is
     encrypted in the database with `AUTH_SECRET_KEY` and is never returned by the API
     (the form shows "configured" or "not set").
   - **User search base**, e.g. `OU=Users,DC=corp,DC=example`, and the **user filter**. The
     default is `(&(objectClass=user)(sAMAccountName={username}))`.
   - **Attributes**: username (`sAMAccountName`), display name (`displayName`), email (`mail`),
     groups (`memberOf`).
   - **Nested groups**: also count groups that the user belongs to indirectly (AD's
     `LDAP_MATCHING_RULE_IN_CHAIN`). It is slower on large directories.
3. **Map groups to roles.** Each mapping is a group, given as its full DN or its CN (e.g.
   `LLM Admins`), and a role. When a user is in several mapped groups, the highest role wins.
   - **Allowed groups**: if set, only members of these groups may sign in. Empty means any
     directory user may sign in.
   - **Default role**: the role for allowed users who match no mapping: `viewer`, `editor`, or
     `deny` (they may not sign in).
4. **Test connection** checks the service bind and the search base. **Test user** takes a
   username and password and shows the DN, groups and role that user would get. It does not
   sign them in or create an account.
5. **Save** and enable.

Users sign in with `alice`, `CORP\alice` or `alice@corp.example`; all of these become `alice`.
Their groups and role are read again at every sign-in, unless an administrator pinned the role.
A user removed from every allowed group can no longer sign in.

If a local account and a directory account have the same name, the local account is used. Avoid
creating local accounts with the names of domain users.

The directory servers must not be this deployment's own services (`ENDPOINT_BLOCKED_HOSTS`,
by default `postgres` and `redis`). Unlike model endpoints, they are allowed on private
networks, because domain controllers always are.

## Windows single sign-on (Kerberos, optional)

With single sign-on, users on domain-joined Windows machines click **Sign in with Windows** and
are signed in without typing a password. It requires the LDAP settings above, which are used to
look up the user's groups and role.

1. **Pick the host name** users will browse to, e.g. `llm-eval.corp.example`. Kerberos does not
   work with IP addresses or `localhost`. Add a DNS A record for it (a CNAME can break Kerberos
   with some browsers).
2. **Create a service account** for the SPN (it may be the LDAP bind account), then register the
   SPN and export a keytab on a domain controller:

   ```powershell
   setspn -S HTTP/llm-eval.corp.example CORP\svc-openllm
   ktpass /princ HTTP/llm-eval.corp.example@CORP.EXAMPLE /mapuser CORP\svc-openllm `
     /pass * /crypto AES256-SHA1 /ptype KRB5_NT_PRINCIPAL /out openllm.keytab
   ```

   In the account's properties, enable "This account supports Kerberos AES 256 bit encryption".
3. In **Settings → Authentication → Windows single sign-on**, enter the service principal
   `HTTP/llm-eval.corp.example@CORP.EXAMPLE`, upload `openllm.keytab`, enable and save. The
   keytab is encrypted in the database like the bind password. It is written to a private
   temporary file (mode 0600) only while a sign-in is being checked.
4. **Browsers** only offer Kerberos to sites in the Local intranet zone:
   - **Edge and Chrome on Windows** use the Internet Options zones. Add
     `https://llm-eval.corp.example` to Local intranet, usually by Group Policy ("Site to Zone
     Assignment List"). Alternatively, set the `AuthServerAllowlist` policy.
   - **Firefox**: set `network.negotiate-auth.trusted-uris` to `llm-eval.corp.example`.
5. Put a TLS proxy in front of the UI for production. Kerberos itself does not encrypt the
   session cookie that follows it.

The image includes the Kerberos libraries (`gssapi`). If you build with `WITH_KERBEROS=false`,
the single sign-on section reports that the library is unavailable; password sign-in with AD
still works.

If single sign-on fails (for example, the machine is not domain-joined), the user sees a message
and can sign in with their username and password.

## Recovery commands

`python -m app.cli.admin` runs inside the API container and works directly on the database. It
works even when nobody can sign in:

```bash
docker compose exec api python -m app.cli.admin list-users
docker compose exec api python -m app.cli.admin reset-password admin       # prints a temporary password
docker compose exec api python -m app.cli.admin create-user ops --role admin
docker compose exec api python -m app.cli.admin set-role alice editor
docker compose exec api python -m app.cli.admin enable-user alice
docker compose exec api python -m app.cli.admin unlock alice               # clear a lockout
docker compose exec api python -m app.cli.admin disable-ldap               # a broken directory setup
docker compose exec api python -m app.cli.admin create-token --service-user ci --role editor --ttl 30d
docker compose exec api python -m app.cli.admin delete-user ci
```

`create-token` prints only the token, so it can be captured in scripts. `--password-stdin`
reads a password from standard input instead of generating one.

## Turning sign-in off

`AUTH_ENABLED=false` makes every request an anonymous administrator, as before Milestone 8. The
UI and the readiness check show a warning. Use it only on a single-user machine with
`BIND_ADDRESS=127.0.0.1`.

## Notes and limits

- `AUTH_SECRET_KEY` encrypts the stored bind password and keytab. Keep it in `.env` across
  upgrades. If it changes, re-enter both in Settings → Authentication.
- The API trusts `X-Forwarded-For` (`FORWARDED_ALLOW_IPS=*`), so the audit log and the
  per-address lockout record the client address behind the bundled proxy. A client that calls
  the API port directly can set this header itself. To prevent that, don't publish the API port
  beyond `127.0.0.1`, or set `FORWARDED_ALLOW_IPS` to the proxy's address.
- `ADMIN_INITIAL_PASSWORD` stays in `.env` until you remove it. It is not accepted after the
  first password change.

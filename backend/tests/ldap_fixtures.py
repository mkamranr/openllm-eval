"""An offline Active Directory for tests (ldap3 MOCK_SYNC; no network)."""

from __future__ import annotations

import ldap3

BASE = "DC=corp,DC=example"
USERS = f"OU=Users,{BASE}"
GROUPS = f"OU=Groups,{BASE}"
SERVICE_DN = f"CN=svc-openllm,OU=Service,{BASE}"
SERVICE_PASSWORD = "svc-bind-Secret-123"

ADMINS = f"CN=LLM Admins,{GROUPS}"
EDITORS = f"CN=LLM Editors,{GROUPS}"
ALL_STAFF = f"CN=All Staff,{GROUPS}"
NESTED_PARENT = f"CN=Platform,{GROUPS}"  # contains LLM Editors
CONTRACTORS = f"CN=Contractors,{GROUPS}"

PASSWORDS = {
    "alice": "Alice-Pass-2026!",
    "bob": "Bob-Pass-2026!",
    "carol": "Carol-Pass-2026!",
    "dave": "Dave-Pass-2026!",
}


def build_server() -> ldap3.Server:
    server = ldap3.Server("mock-dc.corp.example", get_info=ldap3.OFFLINE_AD_2012_R2)
    conn = ldap3.Connection(server, client_strategy=ldap3.MOCK_SYNC)
    add = conn.strategy.add_entry
    add(BASE, {"objectClass": ["top", "domain"], "dc": "corp"})
    add(USERS, {"objectClass": ["top", "organizationalUnit"], "ou": "Users"})
    add(GROUPS, {"objectClass": ["top", "organizationalUnit"], "ou": "Groups"})
    add(
        SERVICE_DN,
        {
            "objectClass": ["top", "user"],
            "sAMAccountName": "svc-openllm",
            "userPassword": SERVICE_PASSWORD,
        },
    )
    add(ADMINS, {"objectClass": ["top", "group"], "cn": "LLM Admins"})
    add(
        EDITORS, {"objectClass": ["top", "group"], "cn": "LLM Editors", "memberOf": [NESTED_PARENT]}
    )
    add(NESTED_PARENT, {"objectClass": ["top", "group"], "cn": "Platform"})
    add(ALL_STAFF, {"objectClass": ["top", "group"], "cn": "All Staff"})
    add(CONTRACTORS, {"objectClass": ["top", "group"], "cn": "Contractors"})
    users = {
        "alice": ("Alice Admin", [ADMINS, ALL_STAFF]),
        "bob": ("Bob Editor", [EDITORS, ALL_STAFF]),
        "carol": ("Carol Staff", [ALL_STAFF]),
        "dave": ("Dave Contractor", [CONTRACTORS]),
    }
    for name, (display, groups) in users.items():
        add(
            f"CN={display},{USERS}",
            {
                "objectClass": ["top", "person", "user"],
                "sAMAccountName": name,
                "displayName": display,
                "mail": f"{name}@corp.example",
                "memberOf": groups,
                "userPassword": PASSWORDS[name],
            },
        )
    return server


def connection_factory(server: ldap3.Server):
    def factory(url, cfg, user, password):
        return ldap3.Connection(
            server,
            user=user,
            password=password,
            client_strategy=ldap3.MOCK_SYNC,
            raise_exceptions=False,
            auto_referrals=False,
        )

    return factory


def ldap_settings(**overrides) -> dict:
    cfg = {
        "enabled": True,
        "server_urls": ["ldaps://mock-dc.corp.example:636"],
        "bind_dn": SERVICE_DN,
        "user_search_base": USERS,
        "role_mappings": [
            {"group": ADMINS, "role": "admin"},
            {"group": "LLM Editors", "role": "editor"},
        ],
        "allowed_groups": [],
        "default_role": "viewer",
    }
    cfg.update(overrides)
    return cfg

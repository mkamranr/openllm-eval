export type Role = "viewer" | "editor" | "admin";

export interface User {
  id: string;
  username: string;
  display_name: string | null;
  email: string | null;
  source: "local" | "ldap" | "kerberos" | "service";
  role: Role;
  role_pinned: boolean;
  must_change_password: boolean;
  disabled: boolean;
  groups: string[];
  last_login_at: string | null;
  created_at: string;
}

export interface Me {
  auth_enabled: boolean;
  auth_method: "session" | "token" | "anonymous";
  user: User | null;
  username: string;
  role: Role;
  must_change_password: boolean;
}

export interface Providers {
  auth_enabled: boolean;
  local: boolean;
  ldap: boolean;
  kerberos: boolean;
}

export interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface ApiTokenCreated extends ApiToken {
  token: string;
}

export interface UserCreated extends User {
  initial_password: string | null;
}

export interface RoleMapping {
  group: string;
  role: Role;
}

export interface LdapSettings {
  enabled: boolean;
  server_urls: string[];
  start_tls: boolean;
  verify_tls: boolean;
  ca_cert_pem: string | null;
  bind_mode: "service" | "direct";
  user_bind_format: string;
  bind_dn: string | null;
  user_search_base: string;
  user_filter: string;
  username_attribute: string;
  display_name_attribute: string;
  email_attribute: string;
  group_attribute: string;
  nested_groups: boolean;
  timeout_seconds: number;
  role_mappings: RoleMapping[];
  allowed_groups: string[];
  default_role: "viewer" | "editor" | "deny";
}

export interface KerberosSettings {
  enabled: boolean;
  service_principal: string | null;
}

export interface AuthSettings {
  ldap: LdapSettings & { bind_password_set: boolean };
  kerberos: KerberosSettings & { keytab_set: boolean; library_available: boolean };
  secret_key_configured: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

export interface LdapSettingsInput extends LdapSettings {
  bind_password?: string | null;
  clear_bind_password?: boolean;
}

export interface KerberosSettingsInput extends KerberosSettings {
  keytab_base64?: string | null;
  clear_keytab?: boolean;
}

export interface DirectoryTestResult {
  ok: boolean;
  message: string;
  details: Record<string, unknown>;
}

export interface AuditEvent {
  id: string;
  at: string;
  actor_user_id: string | null;
  actor_name: string | null;
  action: string;
  target: string | null;
  ip: string | null;
  details: Record<string, unknown>;
}

import { Settings } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { useAuth } from "../../auth/AuthContext";
import { Callout, Card, PageHeader, TabPanel, Tabs, type TabItem } from "../../components/ui";
import { AccountTab } from "./AccountTab";
import { AuditTab } from "./AuditTab";
import { AuthenticationTab } from "./AuthenticationTab";
import { ToolsTab } from "./ToolsTab";
import { UsersTab } from "./UsersTab";

type TabKey = "account" | "users" | "authentication" | "tools" | "audit";

export function SettingsPage() {
  const { me, can } = useAuth();
  const [params, setParams] = useSearchParams();
  const admin = can("admin");
  const items: TabItem<TabKey>[] = [
    { key: "account", label: "Account" },
    ...(admin
      ? ([
          { key: "users", label: "Users" },
          { key: "authentication", label: "Authentication" },
          { key: "tools", label: "Tools" },
          { key: "audit", label: "Audit log" },
        ] as TabItem<TabKey>[])
      : []),
  ];
  const requested = params.get("tab") as TabKey | null;
  const tab: TabKey = items.some((i) => i.key === requested) ? requested! : "account";
  const setTab = (key: TabKey) => setParams(key === "account" ? {} : { tab: key }, { replace: true });

  return (
    <div className="page">
      <PageHeader
        title="Settings"
        icon={Settings}
        description={admin ? "Your account, users and roles, sign-in methods, the tool sandbox, and the audit log." : "Your account and API tokens."}
      />
      {me && !me.auth_enabled && (
        <Callout tone="warn">
          Sign-in is disabled on this server (AUTH_ENABLED=false). Users, roles, and tokens have no effect until it is enabled.
        </Callout>
      )}
      <Card>
        <Tabs label="Settings sections" items={items} value={tab} onChange={setTab} />
        <TabPanel id={tab}>
          {tab === "account" && <AccountTab />}
          {tab === "users" && <UsersTab />}
          {tab === "authentication" && <AuthenticationTab />}
          {tab === "tools" && <ToolsTab />}
          {tab === "audit" && <AuditTab />}
        </TabPanel>
      </Card>
    </div>
  );
}

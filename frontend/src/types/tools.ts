/** Integration-mode tool execution (Milestone 9). */

export interface ToolInfo {
  name: string;
  description: string;
  version: string;
  kind: "http" | "script" | string;
  parameters: Record<string, unknown>;
  timeout_seconds: number | null;
  max_output_bytes: number | null;
}

export interface ToolsStatus {
  enabled: boolean;
  reachable: boolean;
  detail: string | null;
  registry_sha256: string | null;
  registry_error: string | null;
  tools: ToolInfo[];
}

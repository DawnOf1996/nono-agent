/** 与 apps/server（nono_app_server.engine HTTP/SSE API）对齐的前端契约。 */

export type PermissionMode = "default" | "accept_edits" | "plan" | "bypass";

export type SlashCommandInfo = {
  name: string;
  description: string;
};

export type SlashCommandResult = {
  name: string;
  args: string[];
  result: string;
};

export type SessionInfo = {
  session_id: string;
  model: string;
  cwd?: string;
  turn_count: number;
  total_tokens: number;
  permission_mode?: PermissionMode;
  summary?: string;
  updated_at?: number;
};

export type Workspace = {
  id: string;
  path: string;
  name: string;
  created_at: number;
  updated_at: number;
};

export type SessionListItem = {
  id: string;
  engine_session_id: string;
  workspace_id: string;
  cwd: string;
  title: string;
  model: string;
  permission_mode?: PermissionMode;
  turn_count: number;
  created_at: number;
  updated_at: number;
};

export type AppInfo = {
  name: string;
  version: string;
  engine: string;
  host: string;
  port: number;
  cwd: string;
  model: string;
  catalog_ready: boolean;
  catalog_error: string | null;
};

export type CatalogModel = {
  id: string;
  name?: string | null;
  context_window?: number | null;
};

export type CatalogProvider = {
  id: string;
  display_name: string;
  api: string;
  base_url?: string | null;
  api_key_env: string;
  has_key: boolean;
  models: CatalogModel[];
};

export type CatalogSnapshot = {
  adapters: string[];
  default: { provider: string; model: string } | null;
  providers: CatalogProvider[];
};

export type ProviderUpsert = {
  id: string;
  display_name: string;
  api: string;
  base_url?: string | null;
  api_key?: string | null;
  models: Array<{ id: string; name?: string }>;
};

export type PermissionRequest = {
  request_id: string;
  tool_name: string;
  input_params: Record<string, unknown>;
  created_at: number;
};

export type AskUserSignal = {
  type: "ask_user";
  questions: Array<{
    question?: string;
    header?: string;
    options?: Array<{ label: string; description?: string }>;
    multi_select?: boolean;
  }>;
};

export type SdkStreamEvent = {
  type: "stream_event";
  event: {
    type: string;
    delta?: { type?: string; text?: string };
    content_block?: { type?: string; text?: string; name?: string };
    index?: number;
  };
  session_id?: string;
};

export type SdkAssistantMessage = {
  type: "assistant";
  message: {
    role?: string;
    content?: unknown;
  };
  session_id?: string;
  uuid?: string;
};

export type SdkSystemMessage = {
  type: "system";
  subtype?: string;
  content?: string | null;
  session_id?: string;
};

export type SdkResultMessage = {
  type: "result";
  subtype: string;
  is_error: boolean;
  result?: string;
  session_id?: string;
  duration_ms?: number;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
  };
  context_usage?: {
    usage_ratio?: number;
    total_tokens?: number;
    context_window?: number;
  } | null;
  errors?: string[];
};

export type SessionEvent = {
  seq: number;
  type: string;
  data: Record<string, unknown>;
  timestamp: number;
  ignorable?: boolean;
};

export type FsEntry = {
  name: string;
  path: string;
  is_dir: boolean;
};

export type FsListing = {
  path: string;
  parent: string | null;
  home: string;
  entries: FsEntry[];
};

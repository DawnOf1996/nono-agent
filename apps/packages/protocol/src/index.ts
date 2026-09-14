/** 与 apps/server + nono_agent.adapters.server 对齐的前端契约。 */

export type SessionInfo = {
  session_id: string;
  model: string;
  cwd?: string;
  turn_count: number;
  total_tokens: number;
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
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
  };
  errors?: string[];
};

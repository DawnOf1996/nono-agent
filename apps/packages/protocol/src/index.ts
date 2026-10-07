/** 与 apps/server（nono_app_server.engine HTTP/SSE API）对齐的前端契约。 */

export type PermissionMode = "default" | "accept_edits" | "plan" | "bypass";

export type ChatImageAttachment = {
  type: "image";
  source: {
    type: "base64";
    media_type: string;
    data: string;
  };
};

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
  /** 已归档的会话默认不出现在列表里。 */
  archived?: boolean;
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
  session_dir: string;
};

export type CatalogModel = {
  id: string;
  name?: string | null;
  context_window?: number | null;
  max_tokens?: number | null;
  /** 模型接受的输入模态（text / image）。省略时按 ["text"] 处理。 */
  input_types?: string[];
  extra_body?: Record<string, unknown>;
};

export type CatalogProvider = {
  id: string;
  display_name: string;
  api: string;
  base_url?: string | null;
  api_key_env: string;
  has_key: boolean;
  extra_headers?: Record<string, string>;
  extra_body?: Record<string, unknown>;
  models: CatalogModel[];
};

export type CatalogSnapshot = {
  adapters: string[];
  default: { provider: string; model: string } | null;
  providers: CatalogProvider[];
  config_path?: string | null;
};

export type ProviderUpsert = {
  id: string;
  display_name: string;
  api: string;
  base_url?: string | null;
  api_key?: string | null;
  extra_headers?: Record<string, string> | null;
  extra_body?: Record<string, unknown> | null;
  models: Array<{
    id: string;
    name?: string;
    context_window?: number | null;
    max_tokens?: number | null;
    input_types?: string[];
    extra_body?: Record<string, unknown> | null;
  }>;
};

export type PermissionRequest = {
  request_id: string;
  /** 发起该请求的引擎会话；空串表示无会话上下文。 */
  session_id: string;
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
    cache_read_input_tokens?: number;
    cache_creation_input_tokens?: number;
  };
  /** 按模型分项的累计用量。 */
  model_usage?: Record<string, Record<string, number>>;
  context_usage?: {
    usage_ratio?: number;
    total_tokens?: number;
    context_window?: number;
  } | null;
  errors?: string[];
};

/** 工具结果给 UI 的结构化附件（不进模型上下文）。 */
export type FileDiffUi = {
  kind: "file_diff";
  path: string;
  /** 写之前文件不存在（新建）。 */
  created: boolean;
  added: number;
  removed: number;
  /** unified diff（含 @@ 头，行号是文件里的真实行号）；超限时为 null。 */
  diff: string | null;
  truncated: boolean;
};

export type SdkUiToolResultMessage = {
  type: "ui_tool_result";
  session_id?: string;
  uuid?: string;
  tool_use_id?: string;
  tool_name?: string;
  ui?: FileDiffUi;
};

export type SessionEvent = {
  seq: number;
  type: string;
  data: Record<string, unknown>;
  timestamp: number;
  ignorable?: boolean;
};

/** 会话累计 token 用量（四个互不重叠的桶）。 */
export type SessionUsage = {
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  total_tokens: number;
  /** 读回 / (读回 + 新鲜输入)；无输入时为 null。 */
  cache_hit_ratio: number | null;
};

/**
 * 上下文占用快照。
 *
 * 三个分项来自引擎当次的 `analyze_context_usage`；旧版本日志没有，
 * 此时分项为 null，只有 total_tokens 能从最后一次请求的输入量兜底得出。
 */
export type SessionContext = {
  context_window: number;
  total_tokens: number;
  usage_ratio: number;
  system_prompt_tokens: number | null;
  tools_tokens: number | null;
  messages_tokens: number | null;
  message_count: number | null;
};

/** 会话级统计 —— 由服务端从事件流推导（GET /session/{id}/stats）。 */
export type SessionStats = {
  turns: number;
  steps: number;
  model_duration_ms: number;
  tool_duration_ms: number;
  /** 首 token 平均耗时；没有可用样本时为 null。 */
  ttft_ms: number | null;
  /** 输出速度；这轮没有流式分片可量时为 null。 */
  tps: number | null;
  model: string | null;
  usage: SessionUsage;
  context: SessionContext | null;
};

export type FsEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  /** 这一行本身是软链接；is_dir 描述链接目标。 */
  is_symlink?: boolean;
  /** 软链接目标缺失或不可读。 */
  broken?: boolean;
  /** 以点开头的名字。 */
  hidden?: boolean;
};

export type FsListing = {
  path: string;
  parent: string | null;
  home: string;
  /** 系统根目录：Windows 上为空串（磁盘列表），Linux/macOS 上是 "/"。 */
  root: string;
  entries: FsEntry[];
};

export type WorkspaceListing = {
  path: string;
  root: string;
  entries: FsEntry[];
  truncated?: boolean;
};

export type FsSearchMatch = {
  /** 相对工作区根的路径，使用正斜杠。 */
  path: string;
  is_dir: boolean;
};

export type FsSearchResult = {
  matches: FsSearchMatch[];
  truncated: boolean;
};

export type FsFileKind = "text" | "image" | "binary" | "too_large";

export type FsFilePreview = {
  path: string;
  name: string;
  kind: FsFileKind;
  content: string | null;
  size: number;
  truncated: boolean;
  /** 文本文件内容的 SHA-1；保存时作为 base_hash 用来发现磁盘上的并发修改。 */
  hash?: string | null;
};

export type FsSaveResult = {
  path: string;
  size: number;
  hash: string;
};

/** 一个 unified diff hunk；每行保留 `+`、`-` 或空格前缀。 */
export type DiffHunk = {
  old_start: number;
  old_lines: number;
  new_start: number;
  new_lines: number;
  header: string;
  lines: string[];
};

export type FileChangeStatus = "added" | "modified" | "deleted" | "renamed";

/** 一个文件的对比（git 工作树 / 暂存区 / 提交，或一轮对话的轮前轮后）。 */
export type FileDiff = {
  kind: "text" | "binary" | "oversized";
  path: string;
  old_path: string | null;
  status: FileChangeStatus;
  before: boolean;
  after: boolean;
  hunks: DiffHunk[];
  added: number;
  deleted: number;
  coarse: boolean;
  abs_path?: string | null;
};

export type GitLineStat = {
  added: number;
  deleted: number;
  binary: boolean;
};

export type GitStatusEntry = {
  /** 仓库内相对路径（正斜杠）。 */
  path: string;
  orig_path: string | null;
  abs_path: string;
  /** 暂存区一侧的状态字母，空格表示无改动。 */
  index: string;
  /** 工作区一侧的状态字母，`?` 表示未跟踪。 */
  worktree: string;
  staged: boolean;
  unstaged: boolean;
  untracked: boolean;
  conflicted: boolean;
  staged_stat: GitLineStat | null;
  unstaged_stat: GitLineStat | null;
};

export type GitStatus = {
  is_repo: boolean;
  workspace: string;
  repo: string | null;
  repositories: string[];
  branch: string | null;
  head: string | null;
  upstream: string | null;
  ahead: number;
  behind: number;
  entries: GitStatusEntry[];
  truncated: boolean;
};

export type GitWorktree = {
  path: string;
  branch: string;
  repo: string;
  main: boolean;
  locked: boolean;
  changes: number;
};

export type GitLogEntry = {
  hash: string;
  hash_full: string;
  subject: string;
  author: string;
  date: string;
  refs: string;
};

export type GitCommitDetail = {
  hash: string;
  author: string;
  date: string;
  subject: string;
  body: string;
  files: FileDiff[];
  total: number;
};

/** 一轮对话里改动的一个文件。 */
export type TurnChangedFile = {
  /** 工作区内的相对路径，工作区外为绝对路径。 */
  path: string;
  display: string;
  abs_path: string;
  status: FileChangeStatus;
  added: number;
  deleted: number;
  source: "git" | "tool";
  binary?: boolean;
  oversized?: boolean;
};

/** 一轮对话的文件变更摘要；start_seq < 回复的 seq ≤ end_seq。 */
export type TurnChanges = {
  id: string;
  session_id: string;
  cwd: string;
  started_at: number;
  ended_at: number;
  start_seq: number;
  end_seq: number;
  files: TurnChangedFile[];
  total: number;
  added: number;
  deleted: number;
};

export type McpServerSource = "user" | "plugin";

export type McpServerInfo = {
  id: string;
  transport: string;
  command?: string | null;
  args: string[];
  env: Record<string, string>;
  url?: string | null;
  headers: Record<string, string>;
  env_file?: string | null;
  cwd?: string | null;
  source: McpServerSource;
  enabled: boolean;
  running: boolean;
  tool_count: number;
  error?: string | null;
  disabled_tools: string[];
};

export type McpSnapshot = {
  config_path: string;
  enabled: boolean;
  servers: McpServerInfo[];
};

export type McpServerUpsert = {
  id: string;
  transport: string;
  command?: string | null;
  args?: string[];
  env?: Record<string, string>;
  url?: string | null;
  headers?: Record<string, string>;
  env_file?: string | null;
  cwd?: string | null;
};

export type SettingsTab = "general" | "models" | "connectors" | "plugins" | "agents";

/** 插件配置面板的一个字段。服务端按插件清单的 `config` 段生成，前端只负责渲染。 */
export type ToolPluginFieldType = "string" | "number" | "boolean" | "enum" | "password";

export type ToolPluginFieldChoice = { value: string; label: string };

export type ToolPluginField = {
  key: string;
  label: string;
  type: ToolPluginFieldType;
  hint: string;
  placeholder: string;
  choices: ToolPluginFieldChoice[];
  value: string | number | boolean | null;
};

export type ToolPluginApiKey = {
  /** 凭据在 `~/.nono-agent/credentials` 里的键名。面板只展示它，不展示值。 */
  env: string;
  required: boolean;
  configured: boolean;
  label: string;
  hint: string;
  placeholder: string;
};

export type ToolPluginSource = "bundled" | "user" | "project";

export type ToolPluginInfo = {
  id: string;
  version: string;
  description: string;
  source: ToolPluginSource;
  enabled: boolean;
  api_key: ToolPluginApiKey;
  fields: ToolPluginField[];
  /**
   * 正被工作区 `.nono-agent/settings.json` 覆盖的键。
   *
   * 面板写的是用户级配置，而 project 层优先级更高。工作区里若留着旧的
   * `tools.<id>` 块，它会盖住全局开关 —— 这时开关"按了没反应"是事实，
   * 不是 bug。非空即表示有覆盖，面板据此提示。
   */
  project_owned: string[];
  tools: string[];
  running: boolean;
  error?: string | null;
};

export type ToolPluginsSnapshot = {
  enabled: boolean;
  /** 凭据落盘位置，永不回显内容。 */
  credentials_path: string;
  /** 开关与参数落盘位置 —— 用户级，全局生效。 */
  settings_path: string;
  plugins: ToolPluginInfo[];
};

export type ToolPluginUpsert = {
  cwd?: string;
  enabled?: boolean;
  options?: Record<string, unknown>;
};

export type ToolPluginApiKeyUpsert = {
  cwd?: string;
  api_key: string;
};

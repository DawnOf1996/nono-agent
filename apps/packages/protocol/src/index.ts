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

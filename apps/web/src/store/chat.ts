import type {
  AppInfo,
  AskUserSignal,
  CatalogSnapshot,
  FileDiffUi,
  McpServerInfo,
  McpServerUpsert,
  PermissionMode,
  PermissionRequest,
  ProviderUpsert,
  SdkResultMessage,
  SessionEvent,
  SessionListItem,
  SessionStats,
  SettingsTab,
  SlashCommandInfo,
  ToolPluginInfo,
  ToolPluginUpsert,
  TurnChanges,
  Workspace,
} from "@nono/protocol";
import { create } from "zustand";

import { usePanelStore } from "@/features/files/panel-store";
import {
  loadArchiveFilter,
  saveArchiveFilter,
  type ArchiveFilter,
} from "@/features/shell/sidebar-list";

import {
  abortSession,
  answerAskUser,
  answerPermission,
  createAppSession,
  deleteAppSession,
  deleteMcpServer,
  deleteWorkspace,
  fetchAppInfo,
  fetchCatalog,
  fetchCommands,
  fetchHealth,
  fetchMcp,
  fetchToolPlugins,
  fetchPermissions,
  fetchSessionEvents,
  fetchSessionList,
  fetchSessionStats,
  fetchTurnChanges,
  openSessionStatsStream,
  fetchWorkspaces,
  forkAppSession,
  patchAppSession,
  renameWorkspace as patchWorkspace,
  runSlashCommand,
  postChat,
  deleteProvider,
  saveDefaultModel,
  saveMcpServer,
  saveProvider,
  saveToolPlugin as saveToolPluginRequest,
  saveToolPluginApiKey as saveToolPluginApiKeyRequest,
  setMcpEnabled as patchMcpEnabled,
  setSessionPermission,
  upsertWorkspace,
  watchControl as watchControlStream,
} from "@/api/client";
import {
  extractAssistantText,
  extractAssistantThinking,
  extractTextDelta,
  extractThinkingDelta,
  extractTools,
  isTextBlockStart,
  isThinkingBlockStart,
  messagesFromEvents,
  parseSse,
  sliceLastUserTurns,
  toolResultsFromContent,
  toolUiFromMessage,
  type ReplayStep,
  type ReplayToolCall,
  type ToolResultRecord,
} from "@/api/sse";
import {
  loadDraftPermissionMode,
  normalizePermissionMode,
  saveDraftPermissionMode,
} from "@/lib/permissions";
import {
  loadDraftModel,
  loadDraftWorkspace,
  sameWorkspace,
  saveDraftModel,
  saveDraftWorkspace,
} from "@/lib/workspace";
import { MAX_PENDING_IMAGES, toApiAttachments, type PendingImage } from "@/lib/images";
import type { TurnTokens } from "@/lib/usage";
import {
  MAX_QUOTE_CHARS,
  MAX_QUOTES,
  clipQuote,
  composeQuotedMessage,
  splitQuotedMessage,
  type QuoteSource,
} from "@/lib/quote";

export type ChatRole = "user" | "assistant" | "system" | "tool";
export type MainView = "chat" | "trajectory";
export type ChatToolCall = ReplayToolCall;
export type ChatStep = ReplayStep;

export type ChatImage = {
  src: string;
};

export type ChatQuote = {
  id: string;
  text: string;
  /** 引用出处，决定写进正文的标记是 quoted-from-assistant-message 还是 quoted-from-terminal。 */
  source: QuoteSource;
};

export type ChatMessage = {
  id: string;
  role: ChatRole;
  text: string;
  images?: ChatImage[];
  thinking?: string;
  thinkingRunning?: boolean;
  steps?: ChatStep[];
  streaming?: boolean;
  durationMs?: number;
  /** 事件流剪切点（含此 seq），用于从此条回复叉分出新会话。 */
  seq?: number;
  /** 流式过程中收到的本轮文件变更摘要 id；历史消息按 seq 区间匹配。 */
  changesId?: string;
  /** 这条消息落定的时刻（毫秒）。历史消息取事件时间戳，本地新建的取发送时间。 */
  createdAt?: number;
  /** 本轮 token 用量 —— 挂在 AI 消息上，渲染成气泡下方的用量角标。 */
  usage?: TurnTokens;
};

/** 单个会话的运行态。多会话并发时各自独立，互不覆盖。 */
export type SessionRun = {
  messages: ChatMessage[];
  events: SessionEvent[];
  streaming: boolean;
  error: string | null;
  ask: AskUserSignal | null;
  historyReveal: boolean;
  historyLoading: boolean;
};

const EMPTY_RUN: SessionRun = {
  messages: [],
  events: [],
  streaming: false,
  error: null,
  ask: null,
  historyReveal: false,
  historyLoading: false,
};

type ChatState = {
  healthOk: boolean;
  info: AppInfo | null;
  catalog: CatalogSnapshot | null;
  mcpServers: McpServerInfo[];
  mcpConfigPath: string | null;
  toolPlugins: ToolPluginInfo[];
  toolPluginCredentialsPath: string | null;
  toolPluginSettingsPath: string | null;
  sessions: SessionListItem[];
  /** 侧边栏要不要把已归档会话拉进列表。 */
  sessionArchiveFilter: ArchiveFilter;
  sessionId: string | null;
  sessionTitles: Record<string, string>;
  events: SessionEvent[];
  messages: ChatMessage[];
  input: string;
  attachments: PendingImage[];
  quotes: ChatQuote[];
  workspace: string;
  workspaceId: string | null;
  draftWorkspaceId: string | null;
  workspaces: Workspace[];
  permissionMode: PermissionMode;
  model: string;
  /** 各会话的运行态；顶层 messages/streaming/... 是当前会话的镜像。 */
  runs: Record<string, SessionRun>;
  /** 尚未落库的新会话所用的运行态键。 */
  draftRunKey: string;
  streaming: boolean;
  error: string | null;
  ask: AskUserSignal | null;
  permissions: PermissionRequest[];
  newTaskOpen: boolean;
  settingsOpen: boolean;
  settingsTab: SettingsTab;
  view: MainView;
  commands: SlashCommandInfo[];
  historyReveal: boolean;
  historyLoading: boolean;
  /** 各会话每轮的文件变更摘要（按时间先后）。 */
  turnChanges: Record<string, TurnChanges[]>;
  /** 各会话的服务端统计（轮/步/用时/TTFT/TPS/token/上下文）。 */
  sessionStats: Record<string, SessionStats>;
  loadTurnChanges: (sessionId: string) => Promise<void>;
  loadSessionStats: (sessionId: string) => Promise<void>;
  /** 订阅某会话的统计推送（服务端 push，非轮询）。返回的关闭函数存在模块级。 */
  watchSessionStats: (sessionId: string) => void;
  unwatchSessionStats: () => void;
  /** 订阅全局控制流（健康 + 授权请求，服务端 push）。整条链路只开一条。 */
  watchControl: () => void;
  unwatchControl: () => void;
  setInput: (value: string) => void;
  setError: (message: string | null) => void;
  addAttachments: (items: PendingImage[]) => void;
  removeAttachment: (id: string) => void;
  addQuote: (text: string, source?: QuoteSource) => void;
  removeQuote: (id: string) => void;
  setWorkspace: (value: string) => void;
  addWorkspace: (value: string) => Promise<void>;
  renameWorkspace: (workspaceId: string, name: string) => Promise<void>;
  removeWorkspace: (workspaceId: string) => Promise<void>;
  setPermissionMode: (mode: PermissionMode) => Promise<void>;
  setModel: (value: string) => Promise<void>;
  setNewTaskOpen: (open: boolean) => void;
  setSettingsOpen: (open: boolean, tab?: SettingsTab) => void;
  setView: (view: MainView) => void;
  refresh: () => Promise<void>;
  refreshMcp: () => Promise<void>;
  refreshToolPlugins: () => Promise<void>;
  newSession: (opts?: { model?: string; cwd?: string }) => Promise<void>;
  openSession: (sessionId: string) => Promise<void>;
  renameSession: (sessionId: string, title: string) => Promise<void>;
  forkSession: (sessionId: string, seq?: number) => Promise<void>;
  forkFromMessage: (seq: number) => Promise<void>;
  setSessionArchiveFilter: (filter: ArchiveFilter) => Promise<void>;
  archiveSession: (sessionId: string) => Promise<void>;
  unarchiveSession: (sessionId: string) => Promise<void>;
  deleteSession: (sessionId: string) => Promise<void>;
  send: () => Promise<void>;
  runCommand: (line: string) => Promise<void>;
  abort: () => Promise<void>;
  resolvePermission: (requestId: string, allow: boolean) => Promise<void>;
  resolveAsk: (answers: Record<string, unknown>) => Promise<void>;
  upsertProvider: (body: ProviderUpsert) => Promise<void>;
  removeProvider: (providerId: string) => Promise<void>;
  chooseDefault: (provider: string, model: string) => Promise<void>;
  setMcpEnabled: (id: string, enabled: boolean) => Promise<void>;
  upsertMcpServer: (body: McpServerUpsert) => Promise<void>;
  removeMcpServer: (id: string) => Promise<void>;
  /** 开关 / 普通配置项。密钥另走 saveToolPluginApiKey。 */
  saveToolPlugin: (id: string, body: ToolPluginUpsert) => Promise<void>;
  /** 写入或清除凭据（空串 = 清除）。 */
  saveToolPluginApiKey: (id: string, apiKey: string) => Promise<void>;
};

/**
 * 流式分片的合并窗口（毫秒）。
 *
 * 模型一秒能吐几十个分片，逐片提交就是逐片全量重渲染 —— 正文越长每次越贵，
 * 主线程被占满后浏览器根本排不上绘制，看起来就是「卡住不动、最后一次性全出来」。
 */
const STREAM_FLUSH_MS = 50;

function nextId(): string {
  return crypto.randomUUID();
}

function titleFromText(text: string, hasImages = false): string {
  const parsed = splitQuotedMessage(text);
  const source = parsed.body || parsed.quotes[0]?.text || "";
  const line = source.trim().split(/\r?\n/)[0] ?? "";
  return line.slice(0, 32) || (hasImages ? "图片问答" : parsed.quotes.length ? "引用" : "新会话");
}

function titlesFromEvents(events: SessionEvent[]): string | null {
  const first = messagesFromEvents(events).find((item) => item.role === "user");
  return first ? titleFromText(first.text, Boolean(first.images?.length)) : null;
}

function titlesFromSessions(
  sessions: SessionListItem[],
  current: Record<string, string>,
): Record<string, string> {
  const next = { ...current };
  for (const item of sessions) {
    const title = item.title.trim();
    if (!title) continue;
    const key = item.engine_session_id;
    if (!next[key] || next[key] === "新会话") {
      next[key] = title;
    }
  }
  return next;
}

function catalogHasSelectedModel(catalog: CatalogSnapshot | null, model: string): boolean {
  const id = model.trim();
  if (!id || !catalog) return false;
  return catalog.providers.some((provider) => provider.models.some((item) => item.id === id));
}

function workspaceByPath(workspaces: Workspace[], path: string): Workspace | undefined {
  return workspaces.find((item) => sameWorkspace(item.path, path));
}

function listedWorkspace(workspaces: Workspace[], workspaceId?: string | null): Workspace | undefined {
  if (!workspaceId) return undefined;
  return workspaces.find((item) => item.id === workspaceId);
}

function mergeWorkspaceList(current: Workspace[], incoming: Workspace[]): Workspace[] {
  const byId = new Map<string, Workspace>();
  for (const item of [...current, ...incoming]) {
    byId.set(item.id, item);
  }
  return [...byId.values()].sort((a, b) => b.updated_at - a.updated_at);
}

function newDraftKey(): string {
  return `draft:${nextId()}`;
}

/** 当前会话对应的运行态键；未落库的新会话用 draftRunKey。 */
function activeKey(state: ChatState): string {
  return state.sessionId ?? state.draftRunKey;
}

function runAt(state: ChatState, key: string): SessionRun {
  return state.runs[key] ?? EMPTY_RUN;
}

/** 写某个会话的运行态；若它正是当前会话，同步顶层镜像字段。 */
function patchRun(
  state: ChatState,
  key: string,
  patch: Partial<SessionRun>,
): Partial<ChatState> {
  const next: SessionRun = { ...runAt(state, key), ...patch };
  const runs = { ...state.runs, [key]: next };
  return activeKey(state) === key ? { runs, ...next } : { runs };
}

/** 取某个会话的运行态，供侧栏等按会话读取状态。 */
export function sessionRun(state: ChatState, sessionId: string | null): SessionRun {
  if (!sessionId) return EMPTY_RUN;
  return state.runs[sessionId] ?? EMPTY_RUN;
}

/**
 * 把一条工具结果贴回发起它的 tool_use（按 tool_use 的 id 匹配）。
 * 命中就返回新消息，没命中原样返回 —— 没命中的结果不该引发重渲染。
 */
function attachToolOutput(message: ChatMessage, record: ToolResultRecord): ChatMessage {
  if (!message.steps?.length) return message;
  let hit = false;
  const steps = message.steps.map((step) => {
    if (!step.tools?.length) return step;
    let stepHit = false;
    const tools = step.tools.map((tool) => {
      if (tool.id !== record.callId) return tool;
      stepHit = true;
      return { ...tool, output: record.text, isError: record.isError };
    });
    if (!stepHit) return step;
    hit = true;
    return { ...step, tools };
  });
  return hit ? { ...message, steps } : message;
}

/** 一次贴多条结果（一个 tool_result 消息里通常有几条）。 */
function attachMany(message: ChatMessage, records: readonly ToolResultRecord[]): ChatMessage {
  return records.reduce(attachToolOutput, message);
}

/** 同上，但贴的是 UI 附件（文件差异）：只挂界面，不动结果文本。 */
function attachToolUi(
  message: ChatMessage,
  record: { callId: string; ui: FileDiffUi },
): ChatMessage {
  if (!message.steps?.length) return message;
  let hit = false;
  const steps = message.steps.map((step) => {
    if (!step.tools?.length) return step;
    let stepHit = false;
    const tools = step.tools.map((tool) => {
      if (tool.id !== record.callId) return tool;
      stepHit = true;
      return { ...tool, ui: record.ui };
    });
    if (!stepHit) return step;
    hit = true;
    return { ...step, tools };
  });
  return hit ? { ...message, steps } : message;
}

let persistDraftTask: Promise<string | null> | null = null;
/**
 * 当前打开的统计订阅流的关闭函数。
 *
 * 刻意只保留一个：统计是「正在浏览的那个会话」才有意义的，存一格就够，
 * 也就不存在多路连接漏关的问题。切换会话时先关旧的再开新的。
 */
let closeStatsStream: (() => void) | null = null;

/**
 * 控制流的关闭函数。
 *
 * 健康与待授权请求都是全局的，存一格就够，也就不存在多路连接漏关的问题。
 * 重复调用 watchControl 直接忽略：这条流是常驻的，不该被第二个订阅顶掉。
 */
let closeControlStream: (() => void) | null = null;

async function persistDraftSession(
  get: () => ChatState,
  set: (partial: Partial<ChatState> | ((state: ChatState) => Partial<ChatState>)) => void,
  draftKey: string,
): Promise<string | null> {
  const existing = get().sessionId;
  if (existing) return existing;
  if (persistDraftTask) return persistDraftTask;
  persistDraftTask = (async () => {
    const live = get().sessionId;
    if (live) return live;
    const cwd = get().workspace.trim();
    const model = get().model.trim();
    const fail = (message: string) => {
      set((state) => ({ newTaskOpen: true, ...patchRun(state, draftKey, { error: message }) }));
    };
    if (!cwd) {
      fail("先选择工作空间目录");
      return null;
    }
    if (!catalogHasSelectedModel(get().catalog, model)) {
      fail("先选择模型");
      return null;
    }
    const permissionMode = get().permissionMode;
    const workspace = listedWorkspace(get().workspaces, get().workspaceId) ?? workspaceByPath(get().workspaces, cwd);
    if (!workspace) {
      fail("先选择工作空间目录");
      return null;
    }
    const createdMeanwhile = get().sessionId;
    if (createdMeanwhile) return createdMeanwhile;
    const session = await createAppSession({
      workspace_id: workspace.id,
      model: model || undefined,
      permission_mode: permissionMode,
    });
    const nextMode = normalizePermissionMode(session.permission_mode || permissionMode);
    saveDraftWorkspace(workspace.path);
    saveDraftModel(session.model);
    saveDraftPermissionMode(nextMode);
    set((state) => {
      const key = session.engine_session_id;
      const runs = { ...state.runs };
      const draftRun = runs[draftKey];
      if (draftRun) {
        delete runs[draftKey];
        runs[key] = draftRun;
      }
      // 用户可能已切到别的会话：那就只搬运运行态，不抢走当前视图。
      const stillOnDraft = state.sessionId === null && state.draftRunKey === draftKey;
      return {
        runs,
        sessions: placeSession(state, session),
        sessionTitles: { ...state.sessionTitles, [key]: session.title || "新会话" },
        workspaces: mergeWorkspaceList(state.workspaces, [workspace]),
        ...(stillOnDraft
          ? {
              sessionId: key,
              workspace: workspace.path,
              workspaceId: workspace.id,
              permissionMode: nextMode,
              model: session.model,
              draftWorkspaceId: null,
              ...(runs[key] ?? EMPTY_RUN),
            }
          : {}),
      };
    });
    return session.engine_session_id;
  })().finally(() => {
    persistDraftTask = null;
  });
  return persistDraftTask;
}

function sessionListed(filter: ArchiveFilter, archived: boolean): boolean {
  if (filter === "all") return true;
  if (filter === "only") return archived;
  return !archived;
}

function placeSession(state: ChatState, session: SessionListItem): SessionListItem[] {
  const rest = state.sessions.filter((item) => item.id !== session.id);
  if (!sessionListed(state.sessionArchiveFilter, Boolean(session.archived))) return rest;
  const exists = state.sessions.some((item) => item.id === session.id);
  if (!exists) return [session, ...rest];
  return state.sessions.map((item) => (item.id === session.id ? { ...item, ...session } : item));
}

function omitSession(state: ChatState, productId: string): Partial<ChatState> {
  const removed = state.sessions.find((item) => item.id === productId);
  const sessions = state.sessions.filter((item) => item.id !== productId);
  const gone = Boolean(removed && removed.engine_session_id === state.sessionId);
  const sessionTitles = { ...state.sessionTitles };
  const runs = { ...state.runs };
  if (removed) {
    delete sessionTitles[removed.engine_session_id];
    delete runs[removed.engine_session_id];
  }
  return {
    sessions,
    sessionTitles,
    runs,
    ...(gone ? { sessionId: null, draftRunKey: newDraftKey(), ...EMPTY_RUN } : {}),
  };
}

export const useChatStore = create<ChatState>((set, get) => ({
  healthOk: false,
  info: null,
  catalog: null,
  mcpServers: [],
  mcpConfigPath: null,
  toolPlugins: [],
  toolPluginCredentialsPath: null,
  toolPluginSettingsPath: null,
  sessions: [],
  sessionArchiveFilter: loadArchiveFilter(),
  sessionId: null,
  sessionTitles: {},
  events: [],
  messages: [],
  input: "",
  attachments: [],
  quotes: [],
  workspace: loadDraftWorkspace(),
  workspaceId: null,
  draftWorkspaceId: null,
  workspaces: [],
  permissionMode: loadDraftPermissionMode(),
  model: loadDraftModel(),
  runs: {},
  draftRunKey: newDraftKey(),
  streaming: false,
  error: null,
  ask: null,
  permissions: [],
  newTaskOpen: false,
  settingsOpen: false,
  settingsTab: "general",
  view: "chat",
  commands: [],
  historyReveal: false,
  historyLoading: false,
  turnChanges: {},
  sessionStats: {},

  loadTurnChanges: async (sessionId) => {
    try {
      const turns = await fetchTurnChanges(sessionId);
      set((state) => ({ turnChanges: { ...state.turnChanges, [sessionId]: turns } }));
    } catch {
      // 旧版本机服务没有变更接口：不显示卡片
    }
  },

  loadSessionStats: async (sessionId) => {
    try {
      const stats = await fetchSessionStats(sessionId);
      if (!stats) return;
      set((state) => ({ sessionStats: { ...state.sessionStats, [sessionId]: stats } }));
    } catch {
      // 旧版本机服务没有统计接口：底部角标不显示
    }
  },

  watchSessionStats: (sessionId) => {
    closeStatsStream?.();
    closeStatsStream = openSessionStatsStream(sessionId, (stats) => {
      set((state) => ({ sessionStats: { ...state.sessionStats, [sessionId]: stats } }));
    });
  },

  unwatchSessionStats: () => {
    closeStatsStream?.();
    closeStatsStream = null;
  },

  watchControl: () => {
    if (closeControlStream) return;
    const controller = new AbortController();
    closeControlStream = () => controller.abort();
    void watchControlStream(controller.signal, {
      // 流建起来就是服务端还活着的证据；之后每一帧（含授权帧）也同理。
      onOpen: () => set({ healthOk: true }),
      onHealth: (health) => set({ healthOk: health.status === "ok" }),
      onPermissions: (permissions) => set({ permissions, healthOk: true }),
      onClose: () => set({ healthOk: false }),
    });
  },

  unwatchControl: () => {
    closeControlStream?.();
    closeControlStream = null;
  },

  setInput: (value) => set({ input: value }),
  setError: (message) =>
    set((state) => patchRun(state, activeKey(state), { error: message })),
  addAttachments: (items) => {
    if (!items.length) return;
    set((state) => {
      const room = MAX_PENDING_IMAGES - state.attachments.length;
      if (room <= 0) {
        return patchRun(state, activeKey(state), {
          error: `最多添加 ${MAX_PENDING_IMAGES} 张图片`,
        });
      }
      return {
        attachments: [...state.attachments, ...items.slice(0, room)],
        ...patchRun(state, activeKey(state), {
          error: items.length > room ? `最多添加 ${MAX_PENDING_IMAGES} 张图片` : null,
        }),
      };
    });
  },
  removeAttachment: (id) =>
    set((state) => ({
      attachments: state.attachments.filter((item) => item.id !== id),
    })),
  addQuote: (text, source = "assistant") => {
    const normalized = text.replace(/\r\n/g, "\n").trim();
    if (!normalized) return;
    const clipped = clipQuote(normalized);
    set((state) => {
      if (state.quotes.length >= MAX_QUOTES) {
        return patchRun(state, activeKey(state), { error: `最多添加 ${MAX_QUOTES} 条引用` });
      }
      return {
        quotes: [...state.quotes, { id: nextId(), text: clipped.text, source }],
        ...patchRun(state, activeKey(state), {
          error: clipped.clipped ? `引用过长，已保留前 ${MAX_QUOTE_CHARS} 字` : null,
        }),
      };
    });
  },
  removeQuote: (id) =>
    set((state) => ({
      quotes: state.quotes.filter((item) => item.id !== id),
    })),
  setWorkspace: (value) => {
    saveDraftWorkspace(value);
    set((state) => {
      const workspaceId = workspaceByPath(state.workspaces, value)?.id ?? null;
      const leavingDraft = Boolean(state.draftWorkspaceId && state.draftWorkspaceId !== workspaceId);
      return {
        workspace: value,
        workspaceId,
        ...(leavingDraft
          ? {
              draftWorkspaceId: null,
              sessionId: null,
              draftRunKey: newDraftKey(),
              input: "",
              attachments: [],
              quotes: [],
              ...EMPTY_RUN,
            }
          : {}),
      };
    });
    void get().refreshMcp();
  },
  addWorkspace: async (value) => {
    const path = value.trim();
    if (!path) return;
    const workspace = await upsertWorkspace(path);
    saveDraftWorkspace(workspace.path);
    set((state) => {
      const leavingDraft = Boolean(state.draftWorkspaceId && state.draftWorkspaceId !== workspace.id);
      return {
        workspace: workspace.path,
        workspaceId: workspace.id,
        workspaces: mergeWorkspaceList(state.workspaces, [workspace]),
        draftWorkspaceId: leavingDraft ? null : state.draftWorkspaceId,
        input: leavingDraft ? "" : state.input,
        attachments: leavingDraft ? [] : state.attachments,
        quotes: leavingDraft ? [] : state.quotes,
        ...(leavingDraft
          ? { draftRunKey: newDraftKey(), ...EMPTY_RUN }
          : patchRun(state, activeKey(state), { error: null })),
      };
    });
    void get().refreshMcp();
  },
  renameWorkspace: async (workspaceId, name) => {
    const target = workspaceId.trim();
    const nextName = name.trim();
    if (!target || !nextName) return;
    const workspace = await patchWorkspace(target, nextName);
    set((state) => ({
      workspaces: mergeWorkspaceList(state.workspaces, [workspace]),
      ...patchRun(state, activeKey(state), { error: null }),
    }));
  },
  removeWorkspace: async (workspaceId) => {
    const target = workspaceId.trim();
    if (!target) return;
    await deleteWorkspace(target);
    let workspaces = get().workspaces.filter((item) => item.id !== target);
    let sessions = get().sessions;
    try {
      [workspaces, sessions] = await Promise.all([
        fetchWorkspaces(),
        fetchSessionList({ archived: get().sessionArchiveFilter }),
      ]);
    } catch {
      // sidecar 已删除工作区时，本地仍保留会话，稍后刷新
    }
    set((state) => {
      const selectedGone = state.workspaceId === target;
      const live = sessions.find((item) => item.engine_session_id === state.sessionId);
      const listedLive = Boolean(
        live && workspaces.some((item) => item.id === live.workspace_id),
      );
      const next = selectedGone && !live ? workspaces[0] : undefined;
      const workspace = selectedGone ? (live?.cwd ?? next?.path ?? "") : state.workspace;
      const nextId = selectedGone
        ? (listedLive ? live?.workspace_id ?? null : (next?.id ?? null))
        : state.workspaceId;
      if (selectedGone) saveDraftWorkspace(listedLive ? workspace : (next?.path ?? ""));
      return {
        workspaces,
        sessions,
        workspace,
        workspaceId: nextId,
        draftWorkspaceId: state.draftWorkspaceId === target ? null : state.draftWorkspaceId,
      };
    });
  },
  setPermissionMode: async (mode) => {
    saveDraftPermissionMode(mode);
    set({ permissionMode: mode });
    const sessionId = get().sessionId;
    if (!sessionId) return;
    try {
      const result = await setSessionPermission(sessionId, mode);
      const effective = normalizePermissionMode(result.effective);
      set({ permissionMode: effective });
      const mapped = get().sessions.find((item) => item.engine_session_id === sessionId);
      if (mapped) {
        const updated = await patchAppSession(mapped.id, { permission_mode: effective });
        set((state) => ({
          sessions: state.sessions.map((item) => (item.id === updated.id ? updated : item)),
        }));
      }
    } catch {
      // 会话尚未在 sidecar 里时，等发送/新建时再带上
    }
  },
  setModel: async (value) => {
    const next = value.trim();
    saveDraftModel(next);
    set({ model: next });
    const sessionId = get().sessionId;
    if (!sessionId || !next) return;
    const mapped = get().sessions.find((item) => item.engine_session_id === sessionId);
    if (!mapped) return;
    try {
      const updated = await patchAppSession(mapped.id, { model: next });
      set((state) => ({
        sessions: state.sessions.map((item) =>
          item.id === updated.id ? { ...updated, model: updated.model || next } : item,
        ),
        model: next,
      }));
    } catch {
      // 会话尚未在 sidecar 里时，等发送时再带上
    }
  },
  setNewTaskOpen: (open) => set({ newTaskOpen: open }),
  setSettingsOpen: (open, tab) =>
    set({
      settingsOpen: open,
      settingsTab: open ? (tab ?? "general") : get().settingsTab,
    }),
  setView: (view) => set({ view }),

  refresh: async () => {
    const archiveFilter = get().sessionArchiveFilter;
    try {
      const [health, info, workspacesRaw, sessions, permissions, catalog, commands] = await Promise.all([
        fetchHealth(),
        fetchAppInfo().catch(() => null),
        fetchWorkspaces().catch(() => [] as Workspace[]),
        fetchSessionList({ archived: archiveFilter }).catch(() => [] as SessionListItem[]),
        fetchPermissions().catch(() => []),
        fetchCatalog().catch(() => null),
        fetchCommands().catch(() => get().commands),
      ]);
      const state = get();
      const catalogIds = catalog
        ? new Set(catalog.providers.flatMap((provider) => provider.models.map((item) => item.id)))
        : null;
      let model = state.model;
      if (catalogIds && model && !catalogIds.has(model)) model = "";
      if (!model && catalog?.default?.model && (!catalogIds || catalogIds.has(catalog.default.model))) {
        model = catalog.default.model;
      }
      if (!model && info?.model && (!catalogIds || catalogIds.has(info.model))) {
        model = info.model;
      }
      const workspaces = workspacesRaw;
      const live = sessions.find((item) => item.engine_session_id === state.sessionId);
      const selected =
        listedWorkspace(workspaces, live?.workspace_id) ??
        listedWorkspace(workspaces, state.workspaceId) ??
        (live ? undefined : workspaceByPath(workspaces, state.workspace));
      if (selected) saveDraftWorkspace(selected.path);
      else if (!live) saveDraftWorkspace("");
      const permissionMode = live?.permission_mode
        ? normalizePermissionMode(live.permission_mode)
        : state.permissionMode;
      if (live?.permission_mode) saveDraftPermissionMode(permissionMode);
      const sessionTitles = titlesFromSessions(sessions, state.sessionTitles);
      const workspacePath = selected?.path ?? live?.cwd ?? "";
      const mcp = await fetchMcp(workspacePath).catch(() => null);
      set({
        healthOk: health.status === "ok",
        info,
        sessions,
        permissions,
        catalog,
        model,
        workspaces,
        workspace: workspacePath,
        workspaceId: selected?.id ?? null,
        permissionMode,
        sessionTitles,
        commands,
        mcpServers: mcp?.servers ?? [],
        mcpConfigPath: mcp?.config_path ?? null,
      });
      saveDraftModel(model);
    } catch (error) {
      set((state) => ({
        healthOk: false,
        ...patchRun(state, activeKey(state), {
          error: error instanceof Error ? error.message : "无法连接 sidecar",
        }),
      }));
    }
  },

  refreshMcp: async () => {
    try {
      const mcp = await fetchMcp(get().workspace);
      set({ mcpServers: mcp.servers, mcpConfigPath: mcp.config_path });
    } catch {
      // keep last list
    }
  },

  refreshToolPlugins: async () => {
    try {
      const snap = await fetchToolPlugins(get().workspace);
      set({
        toolPlugins: snap.plugins,
        toolPluginCredentialsPath: snap.credentials_path,
        toolPluginSettingsPath: snap.settings_path,
      });
    } catch {
      // keep last list
    }
  },

  newSession: async (opts) => {
    const cwd = (opts?.cwd ?? get().workspace).trim();
    const model = (opts?.model ?? get().model).trim();
    if (!cwd) {
      set({ newTaskOpen: true, error: "先选择工作空间目录" });
      return;
    }
    const permissionMode = get().permissionMode;
    const workspace = opts?.cwd
      ? await upsertWorkspace(cwd)
      : listedWorkspace(get().workspaces, get().workspaceId) ?? workspaceByPath(get().workspaces, cwd);
    if (!workspace) {
      set((state) => ({
        newTaskOpen: true,
        ...patchRun(state, activeKey(state), { error: "先选择工作空间目录" }),
      }));
      return;
    }
    if (model) saveDraftModel(model);
    saveDraftWorkspace(workspace.path);
    saveDraftPermissionMode(permissionMode);
    set((state) => ({
      sessionId: null,
      // 新的草稿键：旧草稿若仍在流式输出，会继续写自己的运行态。
      draftRunKey: newDraftKey(),
      workspace: workspace.path,
      workspaceId: workspace.id,
      workspaces: mergeWorkspaceList(state.workspaces, [workspace]),
      draftWorkspaceId: workspace.id,
      permissionMode,
      model: model || state.model,
      input: "",
      attachments: [],
      quotes: [],
      newTaskOpen: false,
      view: "chat",
      ...EMPTY_RUN,
    }));
  },

  openSession: async (sessionId) => {
    const session = get().sessions.find((item) => item.engine_session_id === sessionId);
    const permissionMode = session?.permission_mode
      ? normalizePermissionMode(session.permission_mode)
      : get().permissionMode;
    if (session?.permission_mode) saveDraftPermissionMode(permissionMode);
    const live = Boolean(get().runs[sessionId]?.streaming);
    set((state) => {
      const previous = state.runs[sessionId];
      // 正在流式输出的会话：保住现场，不清空也不重拉历史。
      const run: SessionRun = live
        ? (previous as SessionRun)
        : { ...EMPTY_RUN, historyLoading: true };
      return {
        sessionId,
        workspace: session?.cwd || state.workspace,
        workspaceId: session?.workspace_id || state.workspaceId,
        draftWorkspaceId: null,
        model: session?.model || state.model,
        permissionMode,
        input: state.draftWorkspaceId ? "" : state.input,
        attachments: state.draftWorkspaceId ? [] : state.attachments,
        quotes: state.draftWorkspaceId ? [] : state.quotes,
        view: "chat",
        runs: { ...state.runs, [sessionId]: run },
        ...run,
      };
    });
    usePanelStore.getState().markConversationOpen();
    void get().refreshMcp();
    void get().loadTurnChanges(sessionId);
    void get().loadSessionStats(sessionId);
    if (live) return;

    // 历史只写进该会话的运行态；期间它若开始新一轮输出就让位给流式内容。
    const writeHistory = (patch: Partial<SessionRun>) =>
      set((state) =>
        runAt(state, sessionId).streaming ? {} : patchRun(state, sessionId, patch),
      );
    try {
      const events = await fetchSessionEvents(sessionId);
      const all = messagesFromEvents(events);
      const title = titlesFromEvents(events);
      const { older, recent } = sliceLastUserTurns(all, 3);
      if (title) {
        set((state) => {
          const current = state.sessionTitles[sessionId];
          // 用户重命名过就别用反推的标题盖回去，否则一点开会话名字就跳回原始名。
          if (current && current !== "新会话") return {};
          return { sessionTitles: { ...state.sessionTitles, [sessionId]: title } };
        });
      }
      writeHistory({
        events,
        messages: recent,
        historyReveal: true,
        historyLoading: false,
      });
      if (title) {
        const mapped = get().sessions.find((item) => item.engine_session_id === sessionId);
        if (mapped && (!mapped.title.trim() || mapped.title === "新会话")) {
          void patchAppSession(mapped.id, { title }).then((updated) => {
            set((state) => ({
              sessions: state.sessions.map((item) => (item.id === updated.id ? updated : item)),
            }));
          }).catch(() => undefined);
        }
      }
      if (!older.length) {
        requestAnimationFrame(() => writeHistory({ historyReveal: false }));
        return;
      }
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      });
      writeHistory({ messages: all });
      window.setTimeout(() => writeHistory({ historyReveal: false }), 480);
    } catch {
      writeHistory({ events: [], messages: [], historyReveal: false, historyLoading: false });
    }
  },

  renameSession: async (sessionId, title) => {
    const nextTitle = title.trim();
    if (!sessionId || !nextTitle) return;
    const updated = await patchAppSession(sessionId, { title: nextTitle });
    set((state) => ({
      sessions: state.sessions.map((item) => (item.id === updated.id ? updated : item)),
      sessionTitles: { ...state.sessionTitles, [updated.engine_session_id]: nextTitle },
      ...patchRun(state, activeKey(state), { error: null }),
    }));
  },

  forkSession: async (sessionId, seq) => {
    if (!sessionId.trim()) return;
    try {
      const forked = await forkAppSession(sessionId, seq);
      set((state) => ({
        sessions: placeSession(state, forked),
        sessionTitles: {
          ...state.sessionTitles,
          [forked.engine_session_id]: forked.title || "新会话",
        },
        ...patchRun(state, activeKey(state), { error: null }),
      }));
      await get().openSession(forked.engine_session_id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "叉分失败";
      set((state) => patchRun(state, activeKey(state), { error: message }));
    }
  },

  forkFromMessage: async (seq) => {
    const engineId = get().sessionId;
    if (!engineId || !Number.isFinite(seq) || seq < 0) return;
    const mapped = get().sessions.find((item) => item.engine_session_id === engineId);
    if (!mapped) return;
    await get().forkSession(mapped.id, seq);
  },

  setSessionArchiveFilter: async (filter) => {
    const previous = get().sessionArchiveFilter;
    if (previous === filter) return;
    saveArchiveFilter(filter);
    set({ sessionArchiveFilter: filter });
    try {
      const sessions = await fetchSessionList({ archived: filter });
      set((state) => ({
        sessions,
        sessionTitles: titlesFromSessions(sessions, state.sessionTitles),
      }));
    } catch (error) {
      saveArchiveFilter(previous);
      set((state) => ({
        sessionArchiveFilter: previous,
        ...patchRun(state, activeKey(state), {
          error: error instanceof Error ? error.message : "无法加载会话",
        }),
      }));
    }
  },

  archiveSession: async (sessionId) => {
    if (!sessionId.trim()) return;
    const updated = await patchAppSession(sessionId, { archived: true });
    if (get().sessionArchiveFilter === "hide") {
      set((state) => omitSession(state, sessionId));
      return;
    }
    set((state) => ({ sessions: placeSession(state, { ...updated, archived: true }) }));
  },

  unarchiveSession: async (sessionId) => {
    if (!sessionId.trim()) return;
    const updated = await patchAppSession(sessionId, { archived: false });
    set((state) => ({ sessions: placeSession(state, { ...updated, archived: false }) }));
  },

  deleteSession: async (sessionId) => {
    if (!sessionId.trim()) return;
    await deleteAppSession(sessionId);
    set((state) => omitSession(state, sessionId));
  },

  runCommand: async (line) => {
    const raw = line.trim();
    const commandLine = raw.startsWith("/") ? raw : `/${raw}`;
    const runKey = activeKey(get());
    if (!commandLine.slice(1).trim() || runAt(get(), runKey).streaming) return;
    if (!get().workspace.trim() && !get().sessionId) {
      set((state) => ({
        newTaskOpen: true,
        ...patchRun(state, runKey, { error: "先选择工作空间目录" }),
      }));
      return;
    }
    set((state) => ({
      input: "",
      view: "chat",
      ...patchRun(state, runKey, { error: null }),
    }));
    try {
      const out = await runSlashCommand({
        line: commandLine,
        session_id: get().sessionId ?? undefined,
        cwd: get().workspace || undefined,
      });
      if (out.name === "clear") {
        set((state) => patchRun(state, runKey, { messages: [], events: [] }));
        return;
      }
      set((state) =>
        patchRun(state, runKey, {
          messages: [
            ...runAt(state, runKey).messages,
            {
              id: nextId(),
              role: "system",
              text: `/${out.name}${out.args.length ? ` ${out.args.join(" ")}` : ""}\n${out.result}`,
            },
          ],
        }),
      );
    } catch (error) {
      set((state) =>
        patchRun(state, runKey, {
          error: error instanceof Error ? error.message : "指令执行失败",
        }),
      );
    }
  },

  send: async () => {
    const typed = get().input.trim();
    const quoteItems = get().quotes;
    const pending = get().attachments;
    const text = composeQuotedMessage(quoteItems, typed);
    // 每条会话各自判断是否在流式输出：别的会话在跑不挡当前会话发送。
    let runKey = activeKey(get());
    if (runAt(get(), runKey).streaming) return;
    if (typed.startsWith("/") && quoteItems.length === 0) {
      await get().runCommand(typed);
      return;
    }
    if (!text && !pending.length) return;
    if (!get().workspace.trim()) {
      set((state) => ({
        newTaskOpen: true,
        ...patchRun(state, runKey, { error: "先选择工作空间目录" }),
      }));
      return;
    }
    if (!catalogHasSelectedModel(get().catalog, get().model)) {
      set((state) => patchRun(state, runKey, { error: "先选择模型" }));
      return;
    }
    const images = pending.map((item) => ({ src: item.preview }));
    const sentAt = Date.now();
    const userMessage: ChatMessage = {
      id: nextId(),
      role: "user",
      text,
      images: images.length ? images : undefined,
      createdAt: sentAt,
    };
    const draftId = nextId();
    set((state) => ({
      input: "",
      attachments: [],
      quotes: [],
      view: "chat",
      ...patchRun(state, runKey, {
        streaming: true,
        error: null,
        messages: [
          ...runAt(state, runKey).messages,
          userMessage,
          { id: draftId, role: "assistant", text: "", streaming: true, createdAt: sentAt },
        ],
      }),
    }));

    const patchDraft = (updater: (item: ChatMessage) => ChatMessage) => {
      set((state) => {
        let found = false;
        const messages = runAt(state, runKey).messages.map((item) => {
          if (item.id !== draftId) return item;
          found = true;
          return updater(item);
        });
        return patchRun(state, runKey, {
          messages: found
            ? messages
            : [
                ...messages,
                updater({ id: draftId, role: "assistant", text: "", streaming: true, createdAt: sentAt }),
              ],
        });
      });
    };

    // 分片先攒在这里，按 STREAM_FLUSH_MS 合并成一次 set。
    const buffer: { text: string; thinking: string; last: "thinking" | "text" | null } = {
      text: "",
      thinking: "",
      last: null,
    };
    let flushTimer: number | null = null;

    const takeBuffer = () => {
      if (flushTimer != null) {
        window.clearTimeout(flushTimer);
        flushTimer = null;
      }
      const taken = { ...buffer };
      buffer.text = "";
      buffer.thinking = "";
      buffer.last = null;
      return taken;
    };

    const flushStream = () => {
      const { text: textChunk, thinking: thinkingChunk, last } = takeBuffer();
      if (!textChunk && !thinkingChunk && !last) return;
      patchDraft((item) => {
        const next: ChatMessage = { ...item, streaming: true };
        if (thinkingChunk) next.thinking = (item.thinking ?? "") + thinkingChunk;
        if (textChunk) next.text = item.text + textChunk;
        if (last === "thinking") {
          next.thinkingRunning = true;
        } else if (last === "text") {
          next.thinkingRunning = item.steps?.length ? item.thinkingRunning : false;
        }
        return next;
      });
    };

    const queueStream = () => {
      if (flushTimer == null) flushTimer = window.setTimeout(flushStream, STREAM_FLUSH_MS);
    };

    /** 本轮已经作废（报错回滚）时丢掉未提交的分片，别让延迟的 flush 把草稿又写回去。 */
    const cancelStream = () => {
      takeBuffer();
    };

    const dropDraft = (state: ChatState, message: string | null): Partial<ChatState> => ({
      input: state.input || typed,
      attachments: state.attachments.length ? state.attachments : pending,
      quotes: state.quotes.length ? state.quotes : quoteItems,
      ...patchRun(state, runKey, {
        error: message,
        messages: runAt(state, runKey).messages.filter(
          (item) => item.id !== userMessage.id && item.id !== draftId,
        ),
      }),
    });

    let sessionId = get().sessionId;
    try {
      if (!sessionId) {
        const draftKey = runKey;
        sessionId = await persistDraftSession(get, set, draftKey);
        if (!sessionId) {
          set((state) => dropDraft(state, null));
          return;
        }
        // 运行态已随会话落库搬到真实 session id 上。
        runKey = sessionId;
      }
      const engineSessionId = sessionId;
      const nextTitle = titleFromText(text, pending.length > 0);
      set((state) => ({
        sessions: state.sessions.map((item) =>
          item.engine_session_id === engineSessionId && (!item.title.trim() || item.title === "新会话")
            ? { ...item, title: nextTitle, updated_at: Date.now() / 1000 }
            : item,
        ),
        sessionTitles: {
          ...state.sessionTitles,
          [engineSessionId]:
            state.sessionTitles[engineSessionId] && state.sessionTitles[engineSessionId] !== "新会话"
              ? state.sessionTitles[engineSessionId]
              : nextTitle,
        },
      }));
      const mapped = get().sessions.find((item) => item.engine_session_id === engineSessionId);
      if (mapped) {
        void patchAppSession(mapped.id, {
          title: get().sessionTitles[engineSessionId] || nextTitle,
          turn_count: Math.max(1, mapped.turn_count),
          model: get().model || undefined,
        }).then((updated) => {
          set((state) => ({
            sessions: state.sessions.map((item) => (item.id === updated.id ? updated : item)),
          }));
        }).catch(() => {
          // 列表刷新时再对齐
        });
      }

      const response = await postChat(
        engineSessionId,
        text,
        get().model || undefined,
        get().permissionMode,
        pending.length ? toApiAttachments(pending) : undefined,
      );
      if (!response.ok) {
        throw new Error(await response.text());
      }
      for await (const frame of parseSse(response)) {
        if (frame.event === "done") break;
        if (frame.event === "error") {
          const payload = frame.data ? (JSON.parse(frame.data) as { error?: string }) : {};
          throw new Error(payload.error || "stream error");
        }
        if (frame.event === "ask_user") {
          const signal = JSON.parse(frame.data) as AskUserSignal;
          flushStream();
          set((state) => patchRun(state, runKey, { ask: signal }));
          continue;
        }
        if (frame.event === "workspace_changes") {
          const summary = JSON.parse(frame.data) as TurnChanges;
          set((state) => {
            const existing = (state.turnChanges[engineSessionId] ?? []).filter((item) => item.id !== summary.id);
            return { turnChanges: { ...state.turnChanges, [engineSessionId]: [...existing, summary] } };
          });
          patchDraft((item) => ({ ...item, changesId: summary.id }));
          usePanelStore.getState().bumpRevision();
          continue;
        }
        if (!frame.data) continue;
        const payload = JSON.parse(frame.data) as unknown;
        if (frame.event === "stream_event") {
          if (isThinkingBlockStart(payload)) {
            buffer.last = "thinking";
            queueStream();
            continue;
          }
          const thinkingDelta = extractThinkingDelta(payload);
          if (thinkingDelta) {
            buffer.thinking += thinkingDelta;
            buffer.last = "thinking";
            queueStream();
            continue;
          }
          const textStarted = isTextBlockStart(payload);
          const textDelta = extractTextDelta(payload);
          if (!textDelta && !textStarted) continue;
          buffer.text += textDelta;
          buffer.last = "text";
          queueStream();
        } else if (frame.event === "assistant") {
          // 整段消息会覆盖草稿正文，先把攒着的分片落地，避免顺序错乱。
          flushStream();
          const full = extractAssistantText(payload);
          const thinking = extractAssistantThinking(payload);
          const tools = extractTools(payload);
          patchDraft((item) => {
            if (tools.length) {
              const step: ChatStep = {
                id: nextId(),
                text: (full || item.text).trim() || undefined,
                thinking: (thinking || item.thinking)?.trim() || undefined,
                tools,
              };
              return {
                ...item,
                steps: [...(item.steps ?? []), step],
                text: "",
                thinking: undefined,
                thinkingRunning: true,
                streaming: true,
              };
            }
            return {
              ...item,
              text: full || item.text,
              thinking: thinking || item.thinking,
              thinkingRunning: false,
              streaming: true,
            };
          });
        } else if (frame.event === "user") {
          // 引擎合成的 tool_result 消息（开了用户消息回放时才会到这里）：
          // 同样把结果挂回发起它的工具调用。
          const results = toolResultsFromContent(
            (payload as { message?: { content?: unknown } }).message?.content,
          );
          if (!results.length) continue;
          flushStream();
          set((state) =>
            patchRun(state, runKey, {
              messages: runAt(state, runKey).messages.map((item) =>
                item.id === draftId ? attachMany(item, results) : item,
              ),
            }),
          );
        } else if (frame.event === "system") {
          // 工具跑完的进度帧（subtype="tool_result"）—— 结果在这里就贴回
          // 正在跑的那次调用，不用等整轮结束再回放事件流。
          const notice = payload as {
            subtype?: string;
            tool_use_id?: string;
            tool_result?: string;
            tool_is_error?: boolean;
          };
          if (notice.subtype !== "tool_result" || !notice.tool_use_id) continue;
          const record: ToolResultRecord = {
            callId: notice.tool_use_id,
            text: notice.tool_result ?? "",
            isError: notice.tool_is_error === true,
          };
          flushStream();
          set((state) =>
            patchRun(state, runKey, {
              messages: runAt(state, runKey).messages.map((item) =>
                item.id === draftId ? attachToolOutput(item, record) : item,
              ),
            }),
          );
        } else if (frame.event === "ui_tool_result") {
          // 工具结果给 UI 的结构化附件（文件差异等）。只挂界面，不进模型上下文 ——
          // 与上面的 tool_result 通知是两条独立消息，这里要的就是"真实行号的差异"。
          const record = toolUiFromMessage(payload);
          if (!record) continue;
          flushStream();
          set((state) =>
            patchRun(state, runKey, {
              messages: runAt(state, runKey).messages.map((item) =>
                item.id === draftId ? attachToolUi(item, record) : item,
              ),
            }),
          );
        } else if (frame.event === "result") {
          flushStream();
          const result = payload as SdkResultMessage;
          if (result.is_error) {
            set((state) =>
              patchRun(state, runKey, {
                error: result.errors?.join("\n") || result.result || "本轮失败",
              }),
            );
          }
          if (result.duration_ms) {
            patchDraft((item) => ({ ...item, durationMs: result.duration_ms }));
          }
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "发送失败";
      cancelStream();
      set((state) =>
        sessionId ? patchRun(state, runKey, { error: message }) : dropDraft(state, message),
      );
    } finally {
      // 正常收尾时把最后一批分片写进去；出错路径已经 cancelStream，这里是空操作。
      flushStream();
      set((state) =>
        patchRun(state, runKey, {
          streaming: false,
          ask: null,
          messages: runAt(state, runKey).messages.map((item) =>
            item.id === draftId ? { ...item, streaming: false, thinkingRunning: false } : item,
          ),
        }),
      );
      usePanelStore.getState().bumpRevision();
      await get().refresh();
      if (sessionId) {
        // 统计不在这里拉：开着 SSE 订阅时每一步都会推，重复补拉没有意义。
        const events = await fetchSessionEvents(sessionId).catch(() => null);
        if (events) {
          set((state) => {
            const run = runAt(state, runKey);
            // 该会话已开始下一轮：别用历史覆盖正在写入的内容。
            if (run.streaming) return {};
            const messages = run.messages.some((item) => item.role === "system")
              ? run.messages
              : messagesFromEvents(events);
            return patchRun(state, runKey, { events, messages });
          });
        }
      }
    }
  },

  abort: async () => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    await abortSession(sessionId);
  },

  resolvePermission: async (requestId, allow) => {
    // 先摘掉再发请求：弹窗要立刻消失。真没送达到的话，服务端下一次
    // 推送会把它原样推回来，不用在这儿做回滚。
    set((state) => ({
      permissions: state.permissions.filter((item) => item.request_id !== requestId),
    }));
    await answerPermission(requestId, allow);
  },

  resolveAsk: async (answers) => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    await answerAskUser(sessionId, answers);
    set((state) => patchRun(state, sessionId, { ask: null }));
  },

  upsertProvider: async (body) => {
    const catalog = await saveProvider(body);
    set((state) => ({ catalog, ...patchRun(state, activeKey(state), { error: null }) }));
  },

  removeProvider: async (providerId) => {
    const catalog = await deleteProvider(providerId);
    const nextModel = catalog.default?.model ?? get().model;
    if (nextModel) saveDraftModel(nextModel);
    set((state) => ({
      catalog,
      model: nextModel,
      ...patchRun(state, activeKey(state), { error: null }),
    }));
  },

  chooseDefault: async (provider, model) => {
    const catalog = await saveDefaultModel(provider, model);
    saveDraftModel(model);
    set({ catalog, model });
  },

  setMcpEnabled: async (id, enabled) => {
    const cwd = get().workspace.trim();
    if (!cwd) {
      set((state) => patchRun(state, activeKey(state), { error: "先选择工作空间目录" }));
      return;
    }
    const mcp = await patchMcpEnabled(cwd, id, enabled);
    set({ mcpServers: mcp.servers, mcpConfigPath: mcp.config_path });
  },

  upsertMcpServer: async (body) => {
    const mcp = await saveMcpServer(body, get().workspace);
    set({ mcpServers: mcp.servers, mcpConfigPath: mcp.config_path });
  },

  removeMcpServer: async (id) => {
    const mcp = await deleteMcpServer(id, get().workspace);
    set({ mcpServers: mcp.servers, mcpConfigPath: mcp.config_path });
  },

  saveToolPlugin: async (id, body) => {
    const snap = await saveToolPluginRequest(id, { ...body, cwd: get().workspace });
    set({
      toolPlugins: snap.plugins,
      toolPluginCredentialsPath: snap.credentials_path,
      toolPluginSettingsPath: snap.settings_path,
    });
  },

  saveToolPluginApiKey: async (id, apiKey) => {
    const snap = await saveToolPluginApiKeyRequest(id, {
      cwd: get().workspace,
      api_key: apiKey,
    });
    set({
      toolPlugins: snap.plugins,
      toolPluginCredentialsPath: snap.credentials_path,
      toolPluginSettingsPath: snap.settings_path,
    });
  },
}));

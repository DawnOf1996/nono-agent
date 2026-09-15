import type {
  AppInfo,
  AskUserSignal,
  CatalogSnapshot,
  PermissionMode,
  PermissionRequest,
  ProviderUpsert,
  SdkResultMessage,
  SessionEvent,
  SessionInfo,
  SlashCommandInfo,
} from "@nono/protocol";
import { create } from "zustand";

import {
  abortSession,
  answerAskUser,
  answerPermission,
  createSession,
  fetchAppInfo,
  fetchCatalog,
  fetchCommands,
  fetchHealth,
  fetchPermissions,
  fetchSessionEvents,
  runSlashCommand,
  fetchSessions,
  postChat,
  saveDefaultModel,
  saveProvider,
  setSessionPermission,
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
  type ReplayStep,
  type ReplayToolCall,
} from "@/api/sse";
import {
  loadDraftPermissionMode,
  normalizePermissionMode,
  saveDraftPermissionMode,
} from "@/lib/permissions";
import {
  loadDraftModel,
  loadDraftWorkspace,
  loadWorkspaces,
  mergeWorkspaces,
  sameWorkspace,
  saveDraftModel,
  saveDraftWorkspace,
  saveWorkspaces,
} from "@/lib/workspace";

export type ChatRole = "user" | "assistant" | "system" | "tool";
export type MainView = "chat" | "trajectory";
export type ChatToolCall = ReplayToolCall;
export type ChatStep = ReplayStep;

export type ChatMessage = {
  id: string;
  role: ChatRole;
  text: string;
  thinking?: string;
  thinkingRunning?: boolean;
  steps?: ChatStep[];
  streaming?: boolean;
};

export type TurnUsage = {
  inputTokens: number;
  outputTokens: number;
  durationMs: number;
  contextRatio: number | null;
  contextTotal: number | null;
  contextWindow: number | null;
};

type ChatState = {
  healthOk: boolean;
  info: AppInfo | null;
  catalog: CatalogSnapshot | null;
  sessions: SessionInfo[];
  sessionId: string | null;
  sessionTitles: Record<string, string>;
  events: SessionEvent[];
  messages: ChatMessage[];
  input: string;
  workspace: string;
  workspaces: string[];
  permissionMode: PermissionMode;
  model: string;
  streaming: boolean;
  error: string | null;
  ask: AskUserSignal | null;
  permissions: PermissionRequest[];
  newTaskOpen: boolean;
  settingsOpen: boolean;
  view: MainView;
  usage: TurnUsage | null;
  commands: SlashCommandInfo[];
  historyReveal: boolean;
  historyLoading: boolean;
  setInput: (value: string) => void;
  setWorkspace: (value: string) => void;
  addWorkspace: (value: string) => void;
  removeWorkspace: (path: string) => void;
  setPermissionMode: (mode: PermissionMode) => Promise<void>;
  setModel: (value: string) => void;
  setNewTaskOpen: (open: boolean) => void;
  setSettingsOpen: (open: boolean) => void;
  setView: (view: MainView) => void;
  refresh: () => Promise<void>;
  refreshHealth: () => Promise<void>;
  refreshPermissions: () => Promise<void>;
  newSession: (opts?: { model?: string; cwd?: string }) => Promise<void>;
  openSession: (sessionId: string) => Promise<void>;
  send: () => Promise<void>;
  runCommand: (line: string) => Promise<void>;
  abort: () => Promise<void>;
  resolvePermission: (requestId: string, allow: boolean) => Promise<void>;
  resolveAsk: (answers: Record<string, unknown>) => Promise<void>;
  upsertProvider: (body: ProviderUpsert) => Promise<void>;
  chooseDefault: (provider: string, model: string) => Promise<void>;
};

function nextId(): string {
  return crypto.randomUUID();
}

function titleFromText(text: string): string {
  const line = text.trim().split(/\r?\n/)[0] ?? "";
  return line.slice(0, 32) || "新会话";
}

function titlesFromEvents(events: SessionEvent[]): string | null {
  const first = messagesFromEvents(events).find((item) => item.role === "user");
  return first ? titleFromText(first.text) : null;
}

function titlesFromSessions(
  sessions: SessionInfo[],
  current: Record<string, string>,
): Record<string, string> {
  const next = { ...current };
  for (const item of sessions) {
    const summary = item.summary?.trim();
    if (!summary) continue;
    if (!next[item.session_id] || next[item.session_id] === "新会话") {
      next[item.session_id] = summary;
    }
  }
  return next;
}

async function resolveSessionTitles(
  sessions: SessionInfo[],
  current: Record<string, string>,
): Promise<Record<string, string>> {
  const next = titlesFromSessions(sessions, current);
  const missing = sessions.filter((item) => {
    if (item.turn_count <= 0) return false;
    const title = next[item.session_id];
    return !title || title === "新会话";
  });
  if (!missing.length) return next;
  const found = await Promise.all(
    missing.map(async (item) => {
      const events = await fetchSessionEvents(item.session_id);
      return [item.session_id, titlesFromEvents(events)] as const;
    }),
  );
  for (const [id, title] of found) {
    if (title) next[id] = title;
  }
  return next;
}

function isLocalDraft(session: SessionInfo): boolean {
  return (session.turn_count ?? 0) <= 0;
}

function persistableSessions(sessions: SessionInfo[]): SessionInfo[] {
  return sessions.filter((item) => !isLocalDraft(item));
}

function keepOpenSession(sessions: SessionInfo[], sessionId: string): SessionInfo[] {
  return sessions.filter((item) => item.session_id === sessionId || !isLocalDraft(item));
}

function mergeLocalDraft(
  persisted: SessionInfo[],
  state: {
    sessionId: string | null;
    sessions: SessionInfo[];
    model: string;
    workspace: string;
    permissionMode: PermissionMode;
    sessionTitles: Record<string, string>;
  },
): SessionInfo[] {
  const id = state.sessionId;
  if (!id || persisted.some((item) => item.session_id === id)) return persisted;
  const draft = state.sessions.find((item) => item.session_id === id) ?? {
    session_id: id,
    model: state.model,
    cwd: state.workspace,
    turn_count: 0,
    total_tokens: 0,
    permission_mode: state.permissionMode,
    summary: state.sessionTitles[id] || "新会话",
    updated_at: Date.now() / 1000,
  };
  return [draft, ...persisted];
}

function usageFromResult(result: SdkResultMessage): TurnUsage {
  return {
    inputTokens: result.usage?.input_tokens ?? 0,
    outputTokens: result.usage?.output_tokens ?? 0,
    durationMs: result.duration_ms ?? 0,
    contextRatio: result.context_usage?.usage_ratio ?? null,
    contextTotal: result.context_usage?.total_tokens ?? null,
    contextWindow: result.context_usage?.context_window ?? null,
  };
}

export const useChatStore = create<ChatState>((set, get) => ({
  healthOk: false,
  info: null,
  catalog: null,
  sessions: [],
  sessionId: null,
  sessionTitles: {},
  events: [],
  messages: [],
  input: "",
  workspace: loadDraftWorkspace(),
  workspaces: mergeWorkspaces([loadDraftWorkspace(), ...loadWorkspaces()]),
  permissionMode: loadDraftPermissionMode(),
  model: loadDraftModel(),
  streaming: false,
  error: null,
  ask: null,
  permissions: [],
  newTaskOpen: false,
  settingsOpen: false,
  view: "chat",
  usage: null,
  commands: [],
  historyReveal: false,
  historyLoading: false,

  setInput: (value) => set({ input: value }),
  setWorkspace: (value) => {
    saveDraftWorkspace(value);
    set((state) => {
      const workspaces = mergeWorkspaces([value, ...state.workspaces]);
      saveWorkspaces(workspaces);
      return { workspace: value, workspaces };
    });
  },
  addWorkspace: (value) => {
    get().setWorkspace(value);
  },
  removeWorkspace: (path) => {
    const target = path.trim();
    if (!target) return;
    set((state) => {
      const workspaces = state.workspaces.filter((item) => !sameWorkspace(item, target));
      saveWorkspaces(workspaces);
      const workspace = sameWorkspace(state.workspace, target)
        ? (workspaces[0] ?? "")
        : state.workspace;
      saveDraftWorkspace(workspace);
      return { workspaces, workspace };
    });
  },
  setPermissionMode: async (mode) => {
    saveDraftPermissionMode(mode);
    set({ permissionMode: mode });
    const sessionId = get().sessionId;
    if (!sessionId) return;
    try {
      const result = await setSessionPermission(sessionId, mode);
      set({ permissionMode: normalizePermissionMode(result.effective) });
    } catch {
      // 会话尚未在 sidecar 里时，等发送/新建时再带上
    }
  },
  setModel: (value) => {
    saveDraftModel(value);
    set({ model: value });
  },
  setNewTaskOpen: (open) => set({ newTaskOpen: open }),
  setSettingsOpen: (open) => set({ settingsOpen: open }),
  setView: (view) => set({ view }),

  refresh: async () => {
    try {
      const [health, info, sessions, permissions, catalog, commands] = await Promise.all([
        fetchHealth(),
        fetchAppInfo().catch(() => null),
        fetchSessions().catch(() => []),
        fetchPermissions().catch(() => []),
        fetchCatalog().catch(() => null),
        fetchCommands().catch(() => get().commands),
      ]);
      const state = get();
      let model = state.model;
      if (!model && catalog?.default?.model) model = catalog.default.model;
      if (!model && info?.model) model = info.model;
      const workspaces = mergeWorkspaces([
        ...state.workspaces,
        state.workspace,
      ]);
      saveWorkspaces(workspaces);
      const live = sessions.find((item) => item.session_id === state.sessionId);
      const permissionMode = live?.permission_mode
        ? normalizePermissionMode(live.permission_mode)
        : state.permissionMode;
      if (live?.permission_mode) saveDraftPermissionMode(permissionMode);
      const sessionTitles = await resolveSessionTitles(sessions, state.sessionTitles);
      set({
        healthOk: health.status === "ok",
        info,
        sessions: mergeLocalDraft(sessions, { ...state, sessionTitles }),
        permissions,
        catalog,
        model,
        workspaces,
        permissionMode,
        sessionTitles,
        commands,
      });
      if (model) saveDraftModel(model);
    } catch (error) {
      set({
        healthOk: false,
        error: error instanceof Error ? error.message : "无法连接 sidecar",
      });
    }
  },

  refreshHealth: async () => {
    try {
      const health = await fetchHealth();
      set({ healthOk: health.status === "ok" });
    } catch {
      set({ healthOk: false });
    }
  },

  refreshPermissions: async () => {
    try {
      set({ permissions: await fetchPermissions() });
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
    const session = await createSession({
      model: model || undefined,
      cwd,
      permission_mode: permissionMode,
    });
    const nextMode = normalizePermissionMode(session.permission_mode || permissionMode);
    saveDraftWorkspace(cwd);
    saveDraftModel(session.model);
    saveDraftPermissionMode(nextMode);
    set((state) => {
      const workspaces = mergeWorkspaces([session.cwd || cwd, ...state.workspaces]);
      saveWorkspaces(workspaces);
      return {
      sessionId: session.session_id,
      workspace: session.cwd || cwd,
      workspaces,
      permissionMode: nextMode,
      model: session.model,
      sessions: [
        {
          ...session,
          turn_count: 0,
          total_tokens: 0,
          summary: "新会话",
          updated_at: Date.now() / 1000,
        },
        ...persistableSessions(state.sessions),
      ],
      sessionTitles: { ...state.sessionTitles, [session.session_id]: "新会话" },
      messages: [],
      events: [],
      usage: null,
      error: null,
      newTaskOpen: false,
      view: "chat",
      historyReveal: false,
      historyLoading: false,
    };
    });
  },

  openSession: async (sessionId) => {
    const session = get().sessions.find((item) => item.session_id === sessionId);
    const permissionMode = session?.permission_mode
      ? normalizePermissionMode(session.permission_mode)
      : get().permissionMode;
    if (session?.permission_mode) saveDraftPermissionMode(permissionMode);
    set((state) => ({
      sessionId,
      workspace: session?.cwd || state.workspace,
      model: session?.model || state.model,
      permissionMode,
      error: null,
      usage: null,
      events: [],
      messages: [],
      historyReveal: false,
      historyLoading: true,
      view: "chat",
      sessions: keepOpenSession(state.sessions, sessionId),
    }));
    try {
      const events = await fetchSessionEvents(sessionId);
      if (get().sessionId !== sessionId) return;
      const all = messagesFromEvents(events);
      const title = titlesFromEvents(events);
      const { older, recent } = sliceLastUserTurns(all, 3);
      const titles = title
        ? { ...get().sessionTitles, [sessionId]: title }
        : get().sessionTitles;
      set({
        events,
        messages: recent,
        sessionTitles: titles,
        historyReveal: true,
        historyLoading: false,
      });
      if (!older.length) {
        requestAnimationFrame(() => {
          if (get().sessionId === sessionId) set({ historyReveal: false });
        });
        return;
      }
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      });
      if (get().sessionId !== sessionId) return;
      set({ messages: all });
      window.setTimeout(() => {
        if (get().sessionId === sessionId) set({ historyReveal: false });
      }, 480);
    } catch {
      if (get().sessionId === sessionId) {
        set({ events: [], messages: [], historyReveal: false, historyLoading: false });
      }
    }
  },

  runCommand: async (line) => {
    const raw = line.trim();
    const commandLine = raw.startsWith("/") ? raw : `/${raw}`;
    if (!commandLine.slice(1).trim() || get().streaming) return;
    if (!get().workspace.trim() && !get().sessionId) {
      set({ newTaskOpen: true, error: "先选择工作空间目录" });
      return;
    }
    set({ input: "", error: null, view: "chat" });
    try {
      const out = await runSlashCommand({
        line: commandLine,
        session_id: get().sessionId ?? undefined,
        cwd: get().workspace || undefined,
      });
      if (out.name === "clear") {
        set({ messages: [], events: [], usage: null });
        return;
      }
      set((state) => ({
        messages: [
          ...state.messages,
          {
            id: nextId(),
            role: "system",
            text: `/${out.name}${out.args.length ? ` ${out.args.join(" ")}` : ""}\n${out.result}`,
          },
        ],
      }));
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "指令执行失败" });
    }
  },

  send: async () => {
    const text = get().input.trim();
    if (!text || get().streaming) return;
    if (text.startsWith("/")) {
      await get().runCommand(text);
      return;
    }
    if (!get().workspace.trim()) {
      set({ newTaskOpen: true, error: "先选择工作空间目录" });
      return;
    }
    let sessionId = get().sessionId;
    if (!sessionId) {
      await get().newSession();
      sessionId = get().sessionId;
      if (!sessionId) return;
    }
    const userMessage: ChatMessage = { id: nextId(), role: "user", text };
    const draftId = nextId();
    set((state) => {
      const already = state.sessions.some((item) => item.session_id === sessionId);
      const sessions = already
        ? state.sessions
        : [
            {
              session_id: sessionId,
              model: state.model,
              cwd: state.workspace,
              turn_count: 0,
              total_tokens: 0,
              permission_mode: state.permissionMode,
              summary: titleFromText(text),
              updated_at: Date.now() / 1000,
            },
            ...state.sessions,
          ];
      return {
      input: "",
      streaming: true,
      error: null,
      view: "chat",
      sessions,
      sessionTitles: {
        ...state.sessionTitles,
        [sessionId]: state.sessionTitles[sessionId] && state.sessionTitles[sessionId] !== "新会话"
          ? state.sessionTitles[sessionId]
          : titleFromText(text),
      },
      messages: [...state.messages, userMessage, { id: draftId, role: "assistant", text: "", streaming: true }],
      };
    });

    const patchDraft = (updater: (item: ChatMessage) => ChatMessage) => {
      set((state) => {
        let found = false;
        const messages = state.messages.map((item) => {
          if (item.id !== draftId) return item;
          found = true;
          return updater(item);
        });
        if (found) return { messages };
        return { messages: [...messages, updater({ id: draftId, role: "assistant", text: "", streaming: true })] };
      });
    };

    try {
      const response = await postChat(
        sessionId,
        text,
        get().model || undefined,
        get().permissionMode,
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
          set({ ask: JSON.parse(frame.data) as AskUserSignal });
          continue;
        }
        if (!frame.data) continue;
        const payload = JSON.parse(frame.data) as unknown;
        if (frame.event === "stream_event") {
          if (isThinkingBlockStart(payload)) {
            patchDraft((item) => ({ ...item, thinkingRunning: true, streaming: true }));
            continue;
          }
          const thinkingDelta = extractThinkingDelta(payload);
          if (thinkingDelta) {
            patchDraft((item) => ({
              ...item,
              thinking: (item.thinking ?? "") + thinkingDelta,
              thinkingRunning: true,
              streaming: true,
            }));
            continue;
          }
          const textStarted = isTextBlockStart(payload);
          const textDelta = extractTextDelta(payload);
          if (!textDelta && !textStarted) continue;
          patchDraft((item) => ({
            ...item,
            text: textDelta ? item.text + textDelta : item.text,
            thinkingRunning: item.steps?.length ? item.thinkingRunning : false,
            streaming: true,
          }));
        } else if (frame.event === "assistant") {
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
        } else if (frame.event === "result") {
          const result = payload as SdkResultMessage;
          if (result.is_error) {
            set({ error: result.errors?.join("\n") || result.result || "本轮失败" });
          }
          set({ usage: usageFromResult(result) });
        }
      }
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "发送失败" });
    } finally {
      set((state) => ({
        streaming: false,
        messages: state.messages.map((item) =>
          item.id === draftId ? { ...item, streaming: false, thinkingRunning: false } : item,
        ),
      }));
      await get().refresh();
      const liveId = get().sessionId;
      if (liveId) {
        const events = await fetchSessionEvents(liveId).catch(() => get().events);
        set((state) => ({
          events,
          messages: state.messages.some((item) => item.role === "system")
            ? state.messages
            : messagesFromEvents(events),
        }));
      }
    }
  },

  abort: async () => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    await abortSession(sessionId);
  },

  resolvePermission: async (requestId, allow) => {
    await answerPermission(requestId, allow);
    set((state) => ({
      permissions: state.permissions.filter((item) => item.request_id !== requestId),
    }));
  },

  resolveAsk: async (answers) => {
    const sessionId = get().sessionId;
    if (!sessionId) return;
    await answerAskUser(sessionId, answers);
    set({ ask: null });
  },

  upsertProvider: async (body) => {
    const catalog = await saveProvider(body);
    const model = body.models[0]?.id || get().model;
    set({ catalog, model, error: null });
    if (model) saveDraftModel(model);
  },

  chooseDefault: async (provider, model) => {
    const catalog = await saveDefaultModel(provider, model);
    saveDraftModel(model);
    set({ catalog, model });
  },
}));

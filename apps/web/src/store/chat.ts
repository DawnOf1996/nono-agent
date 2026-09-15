import type {
  AppInfo,
  AskUserSignal,
  CatalogSnapshot,
  PermissionMode,
  PermissionRequest,
  ProviderUpsert,
  SdkResultMessage,
  SessionEvent,
  SessionListItem,
  SlashCommandInfo,
  Workspace,
} from "@nono/protocol";
import { create } from "zustand";

import {
  abortSession,
  answerAskUser,
  answerPermission,
  createAppSession,
  deleteAppSession,
  deleteWorkspace,
  fetchAppInfo,
  fetchCatalog,
  fetchCommands,
  fetchHealth,
  fetchPermissions,
  fetchSessionEvents,
  fetchSessionList,
  fetchWorkspaces,
  forkAppSession,
  patchAppSession,
  renameWorkspace as patchWorkspace,
  runSlashCommand,
  postChat,
  deleteProvider,
  saveDefaultModel,
  saveProvider,
  setSessionPermission,
  upsertWorkspace,
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
  sessions: SessionListItem[];
  sessionId: string | null;
  sessionTitles: Record<string, string>;
  events: SessionEvent[];
  messages: ChatMessage[];
  input: string;
  workspace: string;
  workspaceId: string | null;
  draftWorkspaceId: string | null;
  workspaces: Workspace[];
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
  addWorkspace: (value: string) => Promise<void>;
  renameWorkspace: (workspaceId: string, name: string) => Promise<void>;
  removeWorkspace: (workspaceId: string) => Promise<void>;
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
  renameSession: (sessionId: string, title: string) => Promise<void>;
  forkSession: (sessionId: string) => Promise<void>;
  archiveSession: (sessionId: string) => Promise<void>;
  deleteSession: (sessionId: string) => Promise<void>;
  send: () => Promise<void>;
  runCommand: (line: string) => Promise<void>;
  abort: () => Promise<void>;
  resolvePermission: (requestId: string, allow: boolean) => Promise<void>;
  resolveAsk: (answers: Record<string, unknown>) => Promise<void>;
  upsertProvider: (body: ProviderUpsert) => Promise<void>;
  removeProvider: (providerId: string) => Promise<void>;
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

function workspaceByPath(workspaces: Workspace[], path: string): Workspace | undefined {
  return workspaces.find((item) => sameWorkspace(item.path, path));
}

function mergeWorkspaceList(current: Workspace[], incoming: Workspace[]): Workspace[] {
  const byId = new Map<string, Workspace>();
  for (const item of [...current, ...incoming]) {
    byId.set(item.id, item);
  }
  return [...byId.values()].sort((a, b) => b.updated_at - a.updated_at);
}

async function migrateLocalWorkspaces(existing: Workspace[]): Promise<Workspace[]> {
  const local = mergeWorkspaces([loadDraftWorkspace(), ...loadWorkspaces()]);
  let next = existing;
  for (const path of local) {
    if (workspaceByPath(next, path)) continue;
    try {
      next = mergeWorkspaceList(next, [await upsertWorkspace(path)]);
    } catch {
      // sidecar 未就绪时仍用服务端已有列表
    }
  }
  return next;
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

let persistDraftTask: Promise<string | null> | null = null;

async function persistDraftSession(
  get: () => ChatState,
  set: (partial: Partial<ChatState> | ((state: ChatState) => Partial<ChatState>)) => void,
): Promise<string | null> {
  const existing = get().sessionId;
  if (existing) return existing;
  if (persistDraftTask) return persistDraftTask;
  persistDraftTask = (async () => {
    const live = get().sessionId;
    if (live) return live;
    const cwd = get().workspace.trim();
    const model = get().model.trim();
    if (!cwd) {
      set({ newTaskOpen: true, error: "先选择工作空间目录" });
      return null;
    }
    const permissionMode = get().permissionMode;
    const workspace = await upsertWorkspace(cwd);
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
    set((state) => ({
      sessionId: session.engine_session_id,
      workspace: workspace.path,
      workspaceId: workspace.id,
      workspaces: mergeWorkspaceList(state.workspaces, [workspace]),
      permissionMode: nextMode,
      model: session.model,
      sessions: [session, ...state.sessions.filter((item) => item.id !== session.id)],
      sessionTitles: { ...state.sessionTitles, [session.engine_session_id]: session.title || "新会话" },
      draftWorkspaceId: null,
    }));
    return session.engine_session_id;
  })().finally(() => {
    persistDraftTask = null;
  });
  return persistDraftTask;
}

function omitSession(state: ChatState, productId: string): Partial<ChatState> {
  const removed = state.sessions.find((item) => item.id === productId);
  const sessions = state.sessions.filter((item) => item.id !== productId);
  const gone = Boolean(removed && removed.engine_session_id === state.sessionId);
  const sessionTitles = { ...state.sessionTitles };
  if (removed) delete sessionTitles[removed.engine_session_id];
  return {
    sessions,
    sessionTitles,
    sessionId: gone ? null : state.sessionId,
    messages: gone ? [] : state.messages,
    events: gone ? [] : state.events,
    usage: gone ? null : state.usage,
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
  workspaceId: null,
  draftWorkspaceId: null,
  workspaces: [],
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
      const workspaceId = workspaceByPath(state.workspaces, value)?.id ?? null;
      const leavingDraft = Boolean(state.draftWorkspaceId && state.draftWorkspaceId !== workspaceId);
      return {
        workspace: value,
        workspaceId,
        ...(leavingDraft
          ? { draftWorkspaceId: null, sessionId: null, input: "", messages: [], events: [], usage: null }
          : {}),
      };
    });
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
        messages: leavingDraft ? [] : state.messages,
        events: leavingDraft ? [] : state.events,
        error: null,
      };
    });
  },
  renameWorkspace: async (workspaceId, name) => {
    const target = workspaceId.trim();
    const nextName = name.trim();
    if (!target || !nextName) return;
    const workspace = await patchWorkspace(target, nextName);
    set((state) => ({
      workspaces: mergeWorkspaceList(state.workspaces, [workspace]),
      error: null,
    }));
  },
  removeWorkspace: async (workspaceId) => {
    const target = workspaceId.trim();
    if (!target) return;
    await deleteWorkspace(target);
    set((state) => {
      const workspaces = state.workspaces.filter((item) => item.id !== target);
      const sessions = state.sessions.filter((item) => item.workspace_id !== target);
      const selectedGone = state.workspaceId === target;
      const next = selectedGone ? workspaces[0] : undefined;
      const workspace = selectedGone ? (next?.path ?? "") : state.workspace;
      const nextId = selectedGone ? (next?.id ?? null) : state.workspaceId;
      saveDraftWorkspace(workspace);
      const sessionGone = state.sessions.some(
        (item) => item.workspace_id === target && item.engine_session_id === state.sessionId,
      );
      return {
        workspaces,
        sessions,
        workspace,
        workspaceId: nextId,
        draftWorkspaceId: state.draftWorkspaceId === target ? null : state.draftWorkspaceId,
        sessionId: sessionGone ? null : state.sessionId,
        messages: sessionGone ? [] : state.messages,
        events: sessionGone ? [] : state.events,
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
  setModel: (value) => {
    saveDraftModel(value);
    set({ model: value });
  },
  setNewTaskOpen: (open) => set({ newTaskOpen: open }),
  setSettingsOpen: (open) => set({ settingsOpen: open }),
  setView: (view) => set({ view }),

  refresh: async () => {
    try {
      const [health, info, workspacesRaw, sessions, permissions, catalog, commands] = await Promise.all([
        fetchHealth(),
        fetchAppInfo().catch(() => null),
        fetchWorkspaces().catch(() => [] as Workspace[]),
        fetchSessionList().catch(() => [] as SessionListItem[]),
        fetchPermissions().catch(() => []),
        fetchCatalog().catch(() => null),
        fetchCommands().catch(() => get().commands),
      ]);
      const state = get();
      let model = state.model;
      if (!model && catalog?.default?.model) model = catalog.default.model;
      if (!model && info?.model) model = info.model;
      const workspaces = await migrateLocalWorkspaces(workspacesRaw);
      const selected =
        (state.workspaceId ? workspaces.find((item) => item.id === state.workspaceId) : undefined) ??
        workspaceByPath(workspaces, state.workspace) ??
        workspaces[0];
      if (selected) saveDraftWorkspace(selected.path);
      const live = sessions.find((item) => item.engine_session_id === state.sessionId);
      const permissionMode = live?.permission_mode
        ? normalizePermissionMode(live.permission_mode)
        : state.permissionMode;
      if (live?.permission_mode) saveDraftPermissionMode(permissionMode);
      const sessionTitles = titlesFromSessions(sessions, state.sessionTitles);
      set({
        healthOk: health.status === "ok",
        info,
        sessions,
        permissions,
        catalog,
        model,
        workspaces,
        workspace: selected?.path ?? state.workspace,
        workspaceId: selected?.id ?? null,
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
    const workspace = await upsertWorkspace(cwd);
    if (model) saveDraftModel(model);
    saveDraftWorkspace(workspace.path);
    saveDraftPermissionMode(permissionMode);
    set((state) => ({
      sessionId: null,
      workspace: workspace.path,
      workspaceId: workspace.id,
      workspaces: mergeWorkspaceList(state.workspaces, [workspace]),
      draftWorkspaceId: workspace.id,
      permissionMode,
      model: model || state.model,
      input: "",
      messages: [],
      events: [],
      usage: null,
      error: null,
      newTaskOpen: false,
      view: "chat",
      historyReveal: false,
      historyLoading: false,
    }));
  },

  openSession: async (sessionId) => {
    const session = get().sessions.find((item) => item.engine_session_id === sessionId);
    const permissionMode = session?.permission_mode
      ? normalizePermissionMode(session.permission_mode)
      : get().permissionMode;
    if (session?.permission_mode) saveDraftPermissionMode(permissionMode);
    set((state) => ({
      sessionId,
      workspace: session?.cwd || state.workspace,
      workspaceId: session?.workspace_id || state.workspaceId,
      draftWorkspaceId: null,
      model: session?.model || state.model,
      permissionMode,
      input: state.draftWorkspaceId ? "" : state.input,
      error: null,
      usage: null,
      events: [],
      messages: [],
      historyReveal: false,
      historyLoading: true,
      view: "chat",
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

  renameSession: async (sessionId, title) => {
    const nextTitle = title.trim();
    if (!sessionId || !nextTitle) return;
    const updated = await patchAppSession(sessionId, { title: nextTitle });
    set((state) => ({
      sessions: state.sessions.map((item) => (item.id === updated.id ? updated : item)),
      sessionTitles: { ...state.sessionTitles, [updated.engine_session_id]: nextTitle },
      error: null,
    }));
  },

  forkSession: async (sessionId) => {
    if (!sessionId.trim()) return;
    const forked = await forkAppSession(sessionId);
    set((state) => ({
      sessions: [forked, ...state.sessions.filter((item) => item.id !== forked.id)],
      sessionTitles: {
        ...state.sessionTitles,
        [forked.engine_session_id]: forked.title || "新会话",
      },
      error: null,
    }));
    await get().openSession(forked.engine_session_id);
  },

  archiveSession: async (sessionId) => {
    if (!sessionId.trim()) return;
    await patchAppSession(sessionId, { archived: true });
    set((state) => omitSession(state, sessionId));
  },

  deleteSession: async (sessionId) => {
    if (!sessionId.trim()) return;
    await deleteAppSession(sessionId);
    set((state) => omitSession(state, sessionId));
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
    const userMessage: ChatMessage = { id: nextId(), role: "user", text };
    const draftId = nextId();
    set((state) => ({
      input: "",
      streaming: true,
      error: null,
      view: "chat",
      messages: [...state.messages, userMessage, { id: draftId, role: "assistant", text: "", streaming: true }],
    }));

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
      let sessionId = get().sessionId;
      if (!sessionId) {
        sessionId = await persistDraftSession(get, set);
        if (!sessionId) {
          set((state) => ({
            streaming: false,
            input: state.input || text,
            messages: state.messages.filter(
              (item) => item.id !== userMessage.id && item.id !== draftId,
            ),
          }));
          return;
        }
      }
      const nextTitle = titleFromText(text);
      set((state) => ({
        sessions: state.sessions.map((item) =>
          item.engine_session_id === sessionId && (!item.title.trim() || item.title === "新会话")
            ? { ...item, title: nextTitle, updated_at: Date.now() / 1000 }
            : item,
        ),
        sessionTitles: {
          ...state.sessionTitles,
          [sessionId]:
            state.sessionTitles[sessionId] && state.sessionTitles[sessionId] !== "新会话"
              ? state.sessionTitles[sessionId]
              : nextTitle,
        },
      }));
      const mapped = get().sessions.find((item) => item.engine_session_id === sessionId);
      if (mapped) {
        void patchAppSession(mapped.id, {
          title: get().sessionTitles[sessionId] || nextTitle,
          turn_count: Math.max(1, mapped.turn_count),
        }).then((updated) => {
          set((state) => ({
            sessions: state.sessions.map((item) => (item.id === updated.id ? updated : item)),
          }));
        }).catch(() => {
          // 列表刷新时再对齐
        });
      }

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
      set((state) => {
        const persistFailed = !state.sessionId;
        return {
          error: error instanceof Error ? error.message : "发送失败",
          ...(persistFailed
            ? {
                input: state.input || text,
                messages: state.messages.filter(
                  (item) => item.id !== userMessage.id && item.id !== draftId,
                ),
              }
            : {}),
        };
      });
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
    set({ catalog, error: null });
  },

  removeProvider: async (providerId) => {
    const catalog = await deleteProvider(providerId);
    const nextModel = catalog.default?.model ?? get().model;
    if (nextModel) saveDraftModel(nextModel);
    set({ catalog, model: nextModel, error: null });
  },

  chooseDefault: async (provider, model) => {
    const catalog = await saveDefaultModel(provider, model);
    saveDraftModel(model);
    set({ catalog, model });
  },
}));

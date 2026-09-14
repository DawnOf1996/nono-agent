import type {
  AppInfo,
  AskUserSignal,
  CatalogSnapshot,
  PermissionRequest,
  ProviderUpsert,
  SessionInfo,
} from "@nono/protocol";
import { create } from "zustand";

import {
  abortSession,
  answerAskUser,
  answerPermission,
  createSession,
  fetchAppInfo,
  fetchCatalog,
  fetchHealth,
  fetchPermissions,
  fetchSessions,
  postChat,
  saveDefaultModel,
  saveProvider,
} from "@/api/client";
import { extractAssistantText, extractTextDelta, extractToolNames, parseSse } from "@/api/sse";
import { loadDraftModel, loadDraftWorkspace, saveDraftModel, saveDraftWorkspace } from "@/lib/workspace";

export type ChatRole = "user" | "assistant" | "system" | "tool";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  text: string;
  tools?: string[];
  streaming?: boolean;
};

type ChatState = {
  healthOk: boolean;
  info: AppInfo | null;
  catalog: CatalogSnapshot | null;
  sessions: SessionInfo[];
  sessionId: string | null;
  messages: ChatMessage[];
  input: string;
  workspace: string;
  model: string;
  streaming: boolean;
  error: string | null;
  ask: AskUserSignal | null;
  permissions: PermissionRequest[];
  newTaskOpen: boolean;
  settingsOpen: boolean;
  setInput: (value: string) => void;
  setWorkspace: (value: string) => void;
  setModel: (value: string) => void;
  setNewTaskOpen: (open: boolean) => void;
  setSettingsOpen: (open: boolean) => void;
  refresh: () => Promise<void>;
  newSession: (opts?: { model?: string; cwd?: string }) => Promise<void>;
  openSession: (sessionId: string) => void;
  send: () => Promise<void>;
  abort: () => Promise<void>;
  resolvePermission: (requestId: string, allow: boolean) => Promise<void>;
  resolveAsk: (answers: Record<string, unknown>) => Promise<void>;
  upsertProvider: (body: ProviderUpsert) => Promise<void>;
  chooseDefault: (provider: string, model: string) => Promise<void>;
};

function nextId(): string {
  return crypto.randomUUID();
}

export const useChatStore = create<ChatState>((set, get) => ({
  healthOk: false,
  info: null,
  catalog: null,
  sessions: [],
  sessionId: null,
  messages: [],
  input: "",
  workspace: loadDraftWorkspace(),
  model: loadDraftModel(),
  streaming: false,
  error: null,
  ask: null,
  permissions: [],
  newTaskOpen: false,
  settingsOpen: false,

  setInput: (value) => set({ input: value }),
  setWorkspace: (value) => {
    saveDraftWorkspace(value);
    set({ workspace: value });
  },
  setModel: (value) => {
    saveDraftModel(value);
    set({ model: value });
  },
  setNewTaskOpen: (open) => set({ newTaskOpen: open }),
  setSettingsOpen: (open) => set({ settingsOpen: open }),

  refresh: async () => {
    try {
      const [health, info, sessions, permissions, catalog] = await Promise.all([
        fetchHealth(),
        fetchAppInfo().catch(() => null),
        fetchSessions().catch(() => []),
        fetchPermissions().catch(() => []),
        fetchCatalog().catch(() => null),
      ]);
      const state = get();
      let model = state.model;
      if (!model && catalog?.default?.model) model = catalog.default.model;
      if (!model && info?.model) model = info.model;
      set({
        healthOk: health.status === "ok",
        info,
        sessions,
        permissions,
        catalog,
        model,
        error: null,
      });
      if (model) saveDraftModel(model);
    } catch (error) {
      set({
        healthOk: false,
        error: error instanceof Error ? error.message : "无法连接 sidecar",
      });
    }
  },

  newSession: async (opts) => {
    const cwd = (opts?.cwd ?? get().workspace).trim();
    const model = (opts?.model ?? get().model).trim();
    if (!cwd) {
      set({ newTaskOpen: true, error: "先选择工作空间目录" });
      return;
    }
    const session = await createSession({ model: model || undefined, cwd });
    saveDraftWorkspace(cwd);
    saveDraftModel(session.model);
    set((state) => ({
      sessionId: session.session_id,
      workspace: session.cwd || cwd,
      model: session.model,
      sessions: [session, ...state.sessions.filter((item) => item.session_id !== session.session_id)],
      messages: [],
      error: null,
      newTaskOpen: false,
    }));
  },

  openSession: (sessionId) => {
    const session = get().sessions.find((item) => item.session_id === sessionId);
    set({
      sessionId,
      workspace: session?.cwd || get().workspace,
      model: session?.model || get().model,
      messages: [],
    });
  },

  send: async () => {
    const text = get().input.trim();
    if (!text || get().streaming) return;
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
    set((state) => ({
      input: "",
      streaming: true,
      error: null,
      messages: [...state.messages, userMessage, { id: draftId, role: "assistant", text: "", streaming: true }],
    }));

    try {
      const response = await postChat(sessionId, text, get().model || undefined);
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
          const ask = JSON.parse(frame.data) as AskUserSignal;
          set({ ask });
          continue;
        }
        if (!frame.data) continue;
        const payload = JSON.parse(frame.data) as unknown;
        if (frame.event === "stream_event") {
          const delta = extractTextDelta(payload);
          if (!delta) continue;
          set((state) => ({
            messages: state.messages.map((item) =>
              item.id === draftId ? { ...item, text: item.text + delta } : item,
            ),
          }));
        } else if (frame.event === "assistant") {
          const full = extractAssistantText(payload);
          const tools = extractToolNames(payload);
          set((state) => ({
            messages: state.messages.map((item) =>
              item.id === draftId
                ? { ...item, text: full || item.text, tools: tools.length ? tools : item.tools }
                : item,
            ),
          }));
        } else if (frame.event === "system") {
          const system = payload as { content?: string; subtype?: string };
          const note = system.content || system.subtype;
          if (!note) continue;
          set((state) => ({
            messages: [
              ...state.messages.filter((item) => item.id !== draftId || item.text),
              { id: nextId(), role: "system", text: note },
            ],
          }));
        } else if (frame.event === "result") {
          const result = payload as { result?: string; is_error?: boolean; errors?: string[] };
          if (result.is_error) {
            set({ error: result.errors?.join("\n") || result.result || "本轮失败" });
          }
        }
      }
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "发送失败" });
    } finally {
      set((state) => ({
        streaming: false,
        messages: state.messages.map((item) =>
          item.id === draftId ? { ...item, streaming: false } : item,
        ),
      }));
      await get().refresh();
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

import type {
  AppInfo,
  AskUserSignal,
  CatalogSnapshot,
  ChatImageAttachment,
  FsListing,
  PermissionRequest,
  PermissionMode,
  ProviderUpsert,
  SessionEvent,
  SessionInfo,
  SessionListItem,
  SlashCommandInfo,
  SlashCommandResult,
  Workspace,
} from "@nono/protocol";

import { parseSse } from "@/api/sse";

function errorMessage(text: string, fallback: string): string {
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.trim()) return parsed.detail;
    if (Array.isArray(parsed.detail)) {
      const parts = parsed.detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object" && "msg" in item) return String((item as { msg: unknown }).msg);
          return "";
        })
        .filter(Boolean);
      if (parts.length) return parts.join("; ");
    }
  } catch {
    // keep raw text
  }
  return text.trim() || fallback;
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(errorMessage(text, `${response.status} ${response.statusText}`));
  }
  return (await response.json()) as T;
}

export async function fetchHealth(): Promise<{ status: string; sessions: number }> {
  return readJson(await fetch("/health"));
}

export async function fetchAppInfo(): Promise<AppInfo> {
  return readJson(await fetch("/app/info"));
}

export async function fetchCatalog(): Promise<CatalogSnapshot> {
  return readJson(await fetch("/app/catalog"));
}

export async function saveProvider(body: ProviderUpsert): Promise<CatalogSnapshot> {
  return readJson(
    await fetch("/app/catalog/providers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function saveDefaultModel(
  provider: string,
  model: string,
): Promise<CatalogSnapshot> {
  return readJson(
    await fetch("/app/catalog/default", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model }),
    }),
  );
}

export async function deleteProvider(providerId: string): Promise<CatalogSnapshot> {
  return readJson(
    await fetch(`/app/catalog/providers/${encodeURIComponent(providerId)}`, {
      method: "DELETE",
    }),
  );
}

export async function fetchRemoteModels(body: {
  base_url: string;
  api: string;
  api_key?: string | null;
  provider_id?: string | null;
}): Promise<Array<{ id: string; name: string }>> {
  const data = await readJson<{ models: Array<{ id: string; name: string }> }>(
    await fetch("/app/catalog/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  return data.models;
}

export async function openCatalogConfig(): Promise<{ path: string; opened: boolean }> {
  return readJson(
    await fetch("/app/catalog/open-config", {
      method: "POST",
    }),
  );
}

export async function fetchWorkspaces(): Promise<Workspace[]> {
  const data = await readJson<{ workspaces: Workspace[] }>(await fetch("/app/workspaces"));
  return data.workspaces;
}

export async function upsertWorkspace(path: string): Promise<Workspace> {
  return readJson(
    await fetch("/app/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }),
  );
}

export async function renameWorkspace(workspaceId: string, name: string): Promise<Workspace> {
  return readJson(
    await fetch(`/app/workspaces/${encodeURIComponent(workspaceId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
}

export async function deleteWorkspace(workspaceId: string): Promise<void> {
  await readJson(await fetch(`/app/workspaces/${encodeURIComponent(workspaceId)}`, { method: "DELETE" }));
}

export async function fetchSessionList(workspaceId?: string): Promise<SessionListItem[]> {
  const query = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
  const data = await readJson<{ sessions: SessionListItem[] }>(await fetch(`/app/sessions${query}`));
  return data.sessions;
}

export async function createAppSession(input: {
  workspace_id: string;
  model?: string;
  permission_mode?: PermissionMode;
  title?: string;
}): Promise<SessionListItem> {
  return readJson(
    await fetch("/app/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function patchAppSession(
  sessionId: string,
  body: {
    title?: string;
    model?: string;
    permission_mode?: PermissionMode;
    turn_count?: number;
    archived?: boolean;
  },
): Promise<SessionListItem> {
  return readJson(
    await fetch(`/app/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function forkAppSession(sessionId: string): Promise<SessionListItem> {
  return readJson(
    await fetch(`/app/sessions/${encodeURIComponent(sessionId)}/fork`, { method: "POST" }),
  );
}

export async function deleteAppSession(sessionId: string): Promise<void> {
  await readJson(
    await fetch(`/app/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
  );
}

export async function fetchSessions(): Promise<SessionInfo[]> {
  const data = await readJson<{ sessions: SessionInfo[] }>(await fetch("/sessions"));
  return data.sessions;
}

export async function createSession(input?: {
  model?: string;
  cwd?: string;
  permission_mode?: PermissionMode;
}): Promise<SessionInfo> {
  return readJson(
    await fetch("/session/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: input?.model || undefined,
        cwd: input?.cwd || undefined,
        permission_mode: input?.permission_mode || undefined,
      }),
    }),
  );
}

export async function setSessionPermission(
  sessionId: string,
  mode: PermissionMode,
): Promise<{ effective: PermissionMode }> {
  return readJson(
    await fetch(`/session/${encodeURIComponent(sessionId)}/permission`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    }),
  );
}

export async function closeSession(sessionId: string): Promise<void> {
  await readJson(await fetch(`/session/${sessionId}`, { method: "DELETE" }));
}

export async function abortSession(sessionId: string): Promise<void> {
  await readJson(
    await fetch("/abort", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }),
  );
}

export async function fetchPermissions(): Promise<PermissionRequest[]> {
  const data = await readJson<{ permissions: PermissionRequest[] }>(
    await fetch("/app/permissions"),
  );
  return data.permissions;
}

export async function answerPermission(requestId: string, allow: boolean): Promise<void> {
  await readJson(
    await fetch(`/app/permissions/${requestId}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ allow }),
    }),
  );
}

export async function answerAskUser(
  sessionId: string,
  answers: Record<string, unknown>,
): Promise<void> {
  await readJson(
    await fetch(`/session/${sessionId}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers }),
    }),
  );
}

export function postChat(
  sessionId: string,
  message: string,
  model?: string,
  permissionMode?: PermissionMode,
  attachments?: ChatImageAttachment[],
): Promise<Response> {
  return fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      session_id: sessionId,
      message,
      model,
      permission_mode: permissionMode,
      ...(attachments?.length ? { attachments } : {}),
    }),
  });
}

export async function fetchSessionEvents(sessionId: string): Promise<SessionEvent[]> {
  const response = await fetch(`/session/${encodeURIComponent(sessionId)}/events?follow=false`, {
    headers: { Accept: "text/event-stream" },
  });
  if (!response.ok) return [];
  const events: SessionEvent[] = [];
  for await (const frame of parseSse(response)) {
    if (frame.event === "done" || frame.event === "error") break;
    if (frame.event !== "session_event" || !frame.data) continue;
    try {
      events.push(JSON.parse(frame.data) as SessionEvent);
    } catch {
      // skip malformed frames
    }
  }
  return events;
}

export async function fetchDirectory(path?: string | null): Promise<FsListing> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return readJson(await fetch(`/app/fs${query}`));
}

export async function fetchCommands(): Promise<SlashCommandInfo[]> {
  const data = await readJson<{ commands: SlashCommandInfo[] }>(await fetch("/commands"));
  return data.commands;
}

export async function runSlashCommand(input: {
  line: string;
  session_id?: string;
  cwd?: string;
}): Promise<SlashCommandResult> {
  return readJson(
    await fetch("/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function createDirectory(parent: string, name: string): Promise<{ path: string }> {
  return readJson(
    await fetch("/app/fs/mkdir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent, name }),
    }),
  );
}

export async function openWorkspaceFolder(path: string): Promise<void> {
  if (window.nonoDesktop?.openPath) {
    const result = await window.nonoDesktop.openPath(path);
    if (!result.ok) throw new Error(result.error || "无法打开文件夹");
    return;
  }
  await readJson(
    await fetch("/app/fs/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }),
  );
}

export type {
  AppInfo,
  AskUserSignal,
  CatalogSnapshot,
  FsListing,
  PermissionMode,
  PermissionRequest,
  SessionEvent,
  SessionInfo,
  SessionListItem,
  SlashCommandInfo,
  Workspace,
};

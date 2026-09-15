import type {
  AppInfo,
  AskUserSignal,
  CatalogSnapshot,
  FsListing,
  PermissionRequest,
  PermissionMode,
  ProviderUpsert,
  SessionEvent,
  SessionInfo,
  SlashCommandInfo,
  SlashCommandResult,
} from "@nono/protocol";

import { parseSse } from "@/api/sse";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
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
): Promise<Response> {
  return fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      session_id: sessionId,
      message,
      model,
      permission_mode: permissionMode,
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
  SlashCommandInfo,
};

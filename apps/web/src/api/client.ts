import type {
  AppInfo,
  AskUserSignal,
  CatalogSnapshot,
  PermissionRequest,
  ProviderUpsert,
  SessionInfo,
} from "@nono/protocol";

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
}): Promise<SessionInfo> {
  return readJson(
    await fetch("/session/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: input?.model || undefined,
        cwd: input?.cwd || undefined,
      }),
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

export function postChat(sessionId: string, message: string, model?: string): Promise<Response> {
  return fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ session_id: sessionId, message, model }),
  });
}

export type { AppInfo, AskUserSignal, CatalogSnapshot, PermissionRequest, SessionInfo };

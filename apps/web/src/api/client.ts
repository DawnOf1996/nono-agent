import type {
  AppInfo,
  AskUserSignal,
  CatalogSnapshot,
  ChatImageAttachment,
  FileDiff,
  FsFilePreview,
  FsListing,
  FsSaveResult,
  FsSearchResult,
  GitCommitDetail,
  GitLogEntry,
  GitStatus,
  GitWorktree,
  PermissionRequest,
  PermissionMode,
  ProviderUpsert,
  SessionEvent,
  SessionInfo,
  SessionListItem,
  McpServerInfo,
  McpServerUpsert,
  McpSnapshot,
  SettingsTab,
  SlashCommandInfo,
  SlashCommandResult,
  TurnChanges,
  Workspace,
  WorkspaceListing,
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

export async function updateSessionDir(path: string): Promise<{ session_dir: string; moved: number }> {
  return readJson(
    await fetch("/app/session-dir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }),
  );
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

export async function fetchMcp(cwd?: string | null): Promise<McpSnapshot> {
  const query = cwd?.trim() ? `?cwd=${encodeURIComponent(cwd.trim())}` : "";
  return readJson(await fetch(`/app/mcp${query}`));
}

export async function saveMcpServer(body: McpServerUpsert, cwd?: string | null): Promise<McpSnapshot> {
  const query = cwd?.trim() ? `?cwd=${encodeURIComponent(cwd.trim())}` : "";
  return readJson(
    await fetch(`/app/mcp/servers${query}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function deleteMcpServer(serverId: string, cwd?: string | null): Promise<McpSnapshot> {
  const query = cwd?.trim() ? `?cwd=${encodeURIComponent(cwd.trim())}` : "";
  return readJson(
    await fetch(`/app/mcp/servers/${encodeURIComponent(serverId)}${query}`, {
      method: "DELETE",
    }),
  );
}

export async function setMcpEnabled(cwd: string, id: string, enabled: boolean): Promise<McpSnapshot> {
  return readJson(
    await fetch("/app/mcp/enabled", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cwd, id, enabled }),
    }),
  );
}

export async function prepareMcpRuntime(
  command: string,
  args: string[],
  onProgress?: (message: string, percent?: number) => void,
): Promise<void> {
  const response = await fetch("/app/mcp/runtimes/prepare", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ command, args }),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(errorMessage(text, "准备运行时失败"));
  }
  for await (const frame of parseSse(response)) {
    let data: { message?: string; percent?: number } = {};
    if (frame.data) {
      try {
        data = JSON.parse(frame.data) as { message?: string; percent?: number };
      } catch {
        continue;
      }
    }
    if (frame.event === "progress" && data.message) {
      onProgress?.(data.message, data.percent);
      continue;
    }
    if (frame.event === "error") {
      throw new Error(data.message?.trim() || "准备运行时失败");
    }
    if (frame.event === "done" && data.message) {
      onProgress?.(data.message, 100);
    }
  }
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

export async function fetchSessionList(options?: {
  workspaceId?: string;
  archived?: "hide" | "all" | "only";
}): Promise<SessionListItem[]> {
  const params = new URLSearchParams();
  if (options?.workspaceId) params.set("workspace_id", options.workspaceId);
  if (options?.archived && options.archived !== "hide") params.set("archived", options.archived);
  const queryString = params.toString();
  const query = queryString ? `?${queryString}` : "";
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

export async function fetchSideSessions(parentId: string): Promise<SessionListItem[]> {
  const params = new URLSearchParams({ side_of: parentId });
  const data = await readJson<{ sessions: SessionListItem[] }>(await fetch(`/app/sessions?${params}`));
  return data.sessions;
}

export async function openSideSession(parentId: string, interruptText?: string): Promise<SessionListItem> {
  return readJson(
    await fetch(`/app/sessions/${encodeURIComponent(parentId)}/side`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ interrupt_text: interruptText?.trim() || null }),
    }),
  );
}

export async function promoteSideSession(sessionId: string): Promise<SessionListItem> {
  return readJson(
    await fetch(`/app/sessions/${encodeURIComponent(sessionId)}/promote`, { method: "POST" }),
  );
}

export async function forkAppSession(
  sessionId: string,
  boundarySeq?: number,
): Promise<SessionListItem> {
  return readJson(
    await fetch(`/app/sessions/${encodeURIComponent(sessionId)}/fork`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        typeof boundarySeq === "number" ? { boundary_seq: boundarySeq } : {},
      ),
    }),
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

export async function fetchWorkspaceEntries(root: string, path?: string | null): Promise<WorkspaceListing> {
  const params = new URLSearchParams({ root });
  if (path) params.set("path", path);
  return readJson(await fetch(`/app/fs/entries?${params}`));
}

export async function searchWorkspaceFiles(root: string, query: string, signal?: AbortSignal): Promise<FsSearchResult> {
  const params = new URLSearchParams({ root, q: query });
  return readJson(await fetch(`/app/fs/search?${params}`, { signal }));
}

export async function uploadWorkspaceFiles(
  root: string,
  directory: string,
  files: File[],
): Promise<{ count: number }> {
  const form = new FormData();
  form.set("root", root);
  form.set("directory", directory);
  for (const file of files) {
    const relative = file.webkitRelativePath || file.name;
    form.append("files", file, file.name);
    form.append("relpaths", relative);
  }
  return readJson(
    await fetch("/app/fs/upload", {
      method: "POST",
      body: form,
    }),
  );
}

export async function fetchWorkspaceFile(
  root: string,
  path: string,
  signal?: AbortSignal,
): Promise<FsFilePreview> {
  const params = new URLSearchParams({ root, path });
  return readJson(await fetch(`/app/fs/file?${params}`, { signal }));
}

export async function fetchWorkspaceBytes(root: string, path: string, signal?: AbortSignal): Promise<Uint8Array> {
  const params = new URLSearchParams({ root, path });
  const response = await fetch(`/app/fs/bytes?${params}`, { signal });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(errorMessage(text, "无法读取文件"));
  }
  return new Uint8Array(await response.arrayBuffer());
}

export async function saveWorkspaceFile(
  root: string,
  path: string,
  content: string,
  baseHash?: string | null,
): Promise<FsSaveResult> {
  const response = await fetch("/app/fs/file", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ root, path, content, base_hash: baseHash ?? null }),
  });
  if (response.status === 409) {
    const text = await response.text();
    throw new SaveConflictError(errorMessage(text, "文件已在磁盘上被修改"));
  }
  return readJson(response);
}

/** 保存时磁盘内容已不是打开时的版本。 */
export class SaveConflictError extends Error {
  readonly conflict = true;
}

function gitQuery(root: string, repo: string | null | undefined, extra?: Record<string, string>): string {
  const params = new URLSearchParams({ root, ...extra });
  if (repo) params.set("repo", repo);
  return params.toString();
}

export async function fetchGitStatus(root: string, repo?: string | null, signal?: AbortSignal): Promise<GitStatus> {
  return readJson(await fetch(`/app/git/status?${gitQuery(root, repo)}`, { signal }));
}

export async function fetchGitWorktrees(root: string): Promise<GitWorktree[]> {
  const data = await readJson<{ worktrees: GitWorktree[] }>(await fetch(`/app/git/worktrees?${gitQuery(root, null)}`));
  return data.worktrees;
}

export async function fetchGitDiff(
  input: { root: string; repo?: string | null; path: string; staged: boolean; context?: number },
  signal?: AbortSignal,
): Promise<FileDiff> {
  const query = gitQuery(input.root, input.repo, {
    path: input.path,
    staged: String(input.staged),
    context: String(input.context ?? 3),
  });
  return readJson(await fetch(`/app/git/diff?${query}`, { signal }));
}

async function postGit(action: string, body: Record<string, unknown>): Promise<void> {
  await readJson(
    await fetch(`/app/git/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export function gitStage(root: string, repo: string | null, paths: string[] | null): Promise<void> {
  return postGit("stage", { root, repo, paths });
}

export function gitUnstage(root: string, repo: string | null, paths: string[] | null): Promise<void> {
  return postGit("unstage", { root, repo, paths });
}

export function gitDiscard(root: string, repo: string | null, paths: string[], untracked: string[]): Promise<void> {
  return postGit("discard", { root, repo, paths, untracked });
}

export function gitCommit(root: string, repo: string | null, message: string, stageAll: boolean): Promise<void> {
  return postGit("commit", { root, repo, message, stage_all: stageAll });
}

export async function fetchGitLog(root: string, repo: string | null, count: number, skip: number): Promise<GitLogEntry[]> {
  const query = gitQuery(root, repo, { count: String(count), skip: String(skip) });
  const data = await readJson<{ entries: GitLogEntry[] }>(await fetch(`/app/git/log?${query}`));
  return data.entries;
}

export async function fetchGitCommit(
  root: string,
  repo: string | null,
  hash: string,
  signal?: AbortSignal,
): Promise<GitCommitDetail> {
  return readJson(await fetch(`/app/git/commit?${gitQuery(root, repo, { hash })}`, { signal }));
}

export async function fetchTurnChanges(sessionId: string): Promise<TurnChanges[]> {
  const data = await readJson<{ turns: TurnChanges[] }>(
    await fetch(`/app/changes/${encodeURIComponent(sessionId)}`),
  );
  return data.turns;
}

export async function fetchTurnDiff(
  sessionId: string,
  turnId: string,
  index: number,
  context = 3,
  signal?: AbortSignal,
): Promise<FileDiff> {
  const params = new URLSearchParams({ index: String(index), context: String(context) });
  return readJson(
    await fetch(`/app/changes/${encodeURIComponent(sessionId)}/${encodeURIComponent(turnId)}/diff?${params}`, {
      signal,
    }),
  );
}

export async function revertTurnFile(sessionId: string, turnId: string, index: number, force: boolean): Promise<void> {
  const response = await fetch(
    `/app/changes/${encodeURIComponent(sessionId)}/${encodeURIComponent(turnId)}/revert`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index, force }),
    },
  );
  if (response.status === 409) {
    const text = await response.text();
    throw new SaveConflictError(errorMessage(text, "文件在这一轮之后又被改过"));
  }
  await readJson(response);
}

export function workspaceFileRawUrl(root: string, path: string): string {
  const params = new URLSearchParams({ root, path });
  return `/app/fs/raw?${params}`;
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

export async function writeMarkdownFile(
  directory: string,
  name: string,
  content: string,
): Promise<{ path: string; name: string }> {
  return readJson(
    await fetch("/app/fs/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ directory, name, content }),
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

export type TerminalShell = { id: string; label: string };

export type WorkspaceJob = {
  id: string;
  session_id: string;
  kind: string;
  title: string;
  status: string;
  output: string;
  exit_code: number | null;
  parent_id: string | null;
  started_at: number;
};

export async function fetchJobs(sessionId: string): Promise<WorkspaceJob[]> {
  const params = new URLSearchParams({ session_id: sessionId });
  const data = await readJson<{ jobs: WorkspaceJob[] }>(await fetch(`/app/jobs?${params}`));
  return data.jobs;
}

export async function* watchJobs(
  sessionId: string,
  signal: AbortSignal,
): AsyncGenerator<WorkspaceJob[]> {
  const params = new URLSearchParams({ session_id: sessionId });
  const response = await fetch(`/app/jobs/stream?${params}`, {
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (!response.ok) throw new Error(await response.text());
  for await (const frame of parseSse(response)) {
    if (frame.event !== "jobs" || !frame.data) continue;
    const payload = JSON.parse(frame.data) as { jobs?: WorkspaceJob[] };
    yield payload.jobs ?? [];
  }
}

export async function killJob(sessionId: string, jobId: string): Promise<void> {
  const params = new URLSearchParams({ session_id: sessionId });
  await readJson(await fetch(`/app/jobs/${encodeURIComponent(jobId)}/kill?${params}`, { method: "POST" }));
}

export async function fetchTerminalShells(): Promise<TerminalShell[]> {
  const data = await readJson<{ shells: TerminalShell[] }>(await fetch("/app/terminal/shells"));
  return data.shells;
}

export async function startTerminal(input: {
  root: string;
  cwd?: string;
  shell?: string;
  cols?: number;
  rows?: number;
}): Promise<{ id: string; shell: string; label: string; cwd: string }> {
  return readJson(
    await fetch("/app/terminal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function stopTerminal(sessionId: string): Promise<void> {
  await readJson(await fetch(`/app/terminal/${encodeURIComponent(sessionId)}`, { method: "DELETE" }));
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
  FsFilePreview,
  FsListing,
  McpServerInfo,
  McpSnapshot,
  PermissionMode,
  PermissionRequest,
  SessionEvent,
  SessionInfo,
  SessionListItem,
  SettingsTab,
  SlashCommandInfo,
  Workspace,
  WorkspaceListing,
};

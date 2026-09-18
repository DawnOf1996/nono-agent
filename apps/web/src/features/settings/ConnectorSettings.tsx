import { Cable, Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import type { McpServerInfo, McpServerUpsert } from "@nono/protocol";

import { prepareMcpRuntime } from "@/api/client";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dropdown, DropdownItem, FieldSelectButton } from "@/components/ui/dropdown";
import { FieldLabel, Input } from "@/components/ui/input";
import { PROVIDER_ID_PATTERN } from "@/features/settings/presets";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

const TRANSPORTS = [
  { id: "stdio", label: "本地命令 (stdio)" },
  { id: "http", label: "HTTP" },
  { id: "sse", label: "SSE" },
] as const;

type TransportId = (typeof TRANSPORTS)[number]["id"];

type ConnectorDraft = {
  mode: "create" | "edit";
  id: string;
  transport: TransportId;
  command: string;
  args: string;
  env: string;
  url: string;
  headers: string;
};

function emptyDraft(): ConnectorDraft {
  return {
    mode: "create",
    id: "",
    transport: "stdio",
    command: "",
    args: "",
    env: "",
    url: "",
    headers: "",
  };
}

function draftFromServer(server: McpServerInfo): ConnectorDraft {
  return {
    mode: "edit",
    id: server.id,
    transport: server.transport === "sse" || server.transport === "http" ? server.transport : "stdio",
    command: server.command ?? "",
    args: (server.args ?? []).join("\n"),
    env: jsonObjectText(server.env),
    url: server.url ?? "",
    headers: jsonObjectText(server.headers),
  };
}

function jsonObjectText(value: Record<string, string> | undefined): string {
  if (!value || Object.keys(value).length === 0) return "";
  return JSON.stringify(value, null, 2);
}

function parseJsonObject(raw: string, label: string): Record<string, string> {
  const text = raw.trim();
  if (!text) return {};
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`${label} 不是合法 JSON`);
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error(`${label} 必须是 JSON 对象`);
  }
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(data as Record<string, unknown>)) {
    if (!key.trim() || value == null) continue;
    if (typeof value === "object") throw new Error(`${label} 的值必须是字符串`);
    result[key] = String(value);
  }
  return result;
}

function parseArgs(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function ConnectorSettings() {
  const servers = useChatStore((state) => state.mcpServers);
  const configPath = useChatStore((state) => state.mcpConfigPath);
  const upsertMcpServer = useChatStore((state) => state.upsertMcpServer);
  const removeMcpServer = useChatStore((state) => state.removeMcpServer);
  const [draft, setDraft] = useState<ConnectorDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const userServers = servers.filter((item) => item.source === "user");

  if (draft) {
    return (
      <ConnectorEditor
        draft={draft}
        existingIds={userServers.map((item) => item.id)}
        saving={saving}
        progress={progress}
        error={formError}
        onCancel={() => {
          setDraft(null);
          setFormError(null);
          setConfirmDelete(false);
        }}
        onSave={async (body) => {
          setSaving(true);
          setFormError(null);
          setProgress(body.command ? "正在校验运行时…" : "正在保存…");
          try {
            if (body.command) {
              await prepareMcpRuntime(body.command, body.args ?? [], (message) => setProgress(message));
            }
            await upsertMcpServer(body);
            setDraft(null);
            setProgress(null);
          } catch (error) {
            setFormError(error instanceof Error ? error.message : "保存失败");
          } finally {
            setSaving(false);
          }
        }}
        onDelete={
          draft.mode === "edit"
            ? async () => {
                setSaving(true);
                setFormError(null);
                try {
                  await removeMcpServer(draft.id);
                  setDraft(null);
                  setConfirmDelete(false);
                } catch (error) {
                  setFormError(error instanceof Error ? error.message : "删除失败");
                } finally {
                  setSaving(false);
                }
              }
            : undefined
        }
        confirmDelete={confirmDelete}
        onConfirmDelete={setConfirmDelete}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div>
        <h3 className="text-[15px] font-medium">连接器</h3>
        <p className="mt-1 text-[13px] text-muted">
          MCP 服务保存在本机用户目录。每个工作区要在对话里单独打开才会启用，默认全部关闭。
        </p>
      </div>
      <div className="mt-4 min-h-0 flex-1 space-y-2 overflow-auto pr-1">
        {userServers.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-line px-4 py-8 text-center text-sm text-muted">
            还没有自定义连接器。添加后，可在输入框的连接器菜单里按工作区开关。
          </div>
        ) : (
          userServers.map((server) => (
            <div key={server.id} className="rounded-2xl border border-line bg-bg px-4 py-3">
              <div className="flex items-center gap-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-card text-muted">
                  <Cable className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{server.id}</div>
                  <div className="mt-0.5 truncate font-mono text-[11px] text-faint">
                    {server.transport}
                    {server.url ? ` · ${server.url}` : server.command ? ` · ${server.command}` : ""}
                  </div>
                </div>
                <Button variant="outline" size="sm" onClick={() => setDraft(draftFromServer(server))}>
                  <Pencil className="size-3.5" />
                  编辑
                </Button>
              </div>
            </div>
          ))
        )}
      </div>
      <div className="mt-4 flex items-center justify-between gap-3">
        <p className="truncate font-mono text-[11px] text-faint" title={configPath ?? undefined}>
          {configPath || "~/.nono-agent/mcp.json"}
        </p>
        <Button size="sm" onClick={() => setDraft(emptyDraft())}>
          <Plus className="size-3.5" />
          添加连接器
        </Button>
      </div>
    </div>
  );
}

function ConnectorEditor({
  draft,
  existingIds,
  saving,
  progress,
  error,
  onCancel,
  onSave,
  onDelete,
  confirmDelete,
  onConfirmDelete,
}: {
  draft: ConnectorDraft;
  existingIds: string[];
  saving: boolean;
  progress: string | null;
  error: string | null;
  onCancel: () => void;
  onSave: (body: McpServerUpsert) => Promise<void>;
  onDelete?: () => Promise<void>;
  confirmDelete: boolean;
  onConfirmDelete: (open: boolean) => void;
}) {
  const [id, setId] = useState(draft.id);
  const [transport, setTransport] = useState<TransportId>(draft.transport);
  const [transportOpen, setTransportOpen] = useState(false);
  const [command, setCommand] = useState(draft.command);
  const [args, setArgs] = useState(draft.args);
  const [env, setEnv] = useState(draft.env);
  const [url, setUrl] = useState(draft.url);
  const [headers, setHeaders] = useState(draft.headers);
  const [localError, setLocalError] = useState<string | null>(null);
  const creating = draft.mode === "create";
  const idInvalid = creating && id.trim().length > 0 && !PROVIDER_ID_PATTERN.test(id.trim());
  const idTaken = creating && existingIds.includes(id.trim());
  const remote = transport === "http" || transport === "sse";

  const canSubmit = useMemo(() => {
    if (!id.trim()) return false;
    if (creating && !PROVIDER_ID_PATTERN.test(id.trim())) return false;
    if (creating && existingIds.includes(id.trim())) return false;
    if (remote) return Boolean(url.trim());
    return Boolean(command.trim());
  }, [command, creating, existingIds, id, remote, url]);

  async function submit() {
    setLocalError(null);
    try {
      await onSave({
        id: id.trim(),
        transport,
        command: remote ? null : command.trim() || null,
        args: remote ? [] : parseArgs(args),
        env: parseJsonObject(env, "环境变量"),
        url: remote ? url.trim() : null,
        headers: remote ? parseJsonObject(headers, "请求头") : {},
      });
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "保存失败");
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div>
        <h3 className="text-[15px] font-medium">{creating ? "添加连接器" : `编辑 ${draft.id}`}</h3>
        <p className="mt-1 text-[13px] text-muted">保存时会校验命令，并用内置运行时下载缺失的 uv / Python / Node。</p>
      </div>
      <div className="mt-4 min-h-0 flex-1 space-y-3 overflow-auto pr-1">
        <div>
          <FieldLabel>ID</FieldLabel>
          <Input
            value={id}
            disabled={!creating}
            placeholder="docs"
            className={cn(idInvalid || idTaken ? "border-danger/40" : "")}
            onChange={(event) => setId(event.target.value)}
          />
          {idInvalid ? <p className="mt-1 text-[11px] text-danger">需小写字母开头，只能含字母、数字、下划线和连字符</p> : null}
          {idTaken ? <p className="mt-1 text-[11px] text-danger">这个 ID 已存在</p> : null}
        </div>
        <div>
          <FieldLabel>传输</FieldLabel>
          <Dropdown
            open={transportOpen}
            onOpenChange={setTransportOpen}
            matchTriggerWidth
            trigger={
              <FieldSelectButton
                open={transportOpen}
                label={TRANSPORTS.find((item) => item.id === transport)?.label ?? transport}
                onClick={() => setTransportOpen((value) => !value)}
              />
            }
          >
            {TRANSPORTS.map((item) => (
              <DropdownItem
                key={item.id}
                selected={item.id === transport}
                onClick={() => {
                  setTransport(item.id);
                  setTransportOpen(false);
                }}
              >
                {item.label}
              </DropdownItem>
            ))}
          </Dropdown>
        </div>
        {remote ? (
          <>
            <div>
              <FieldLabel>URL</FieldLabel>
              <Input value={url} placeholder="https://example.com/mcp" onChange={(event) => setUrl(event.target.value)} />
            </div>
            <div>
              <FieldLabel>请求头 JSON</FieldLabel>
              <textarea
                value={headers}
                placeholder='{"Authorization":"Bearer ..."}'
                className="min-h-[88px] w-full resize-y rounded-lg border border-line bg-card px-2.5 py-2 font-mono text-[12px] leading-5 outline-none focus:border-line-strong"
                onChange={(event) => setHeaders(event.target.value)}
              />
            </div>
          </>
        ) : (
          <>
            <div>
              <FieldLabel>命令</FieldLabel>
              <Input value={command} placeholder="uvx" onChange={(event) => setCommand(event.target.value)} />
              <p className="mt-1.5 text-[11px] leading-5 text-muted">
                python / uv / uvx / node / npm / npx 走 Nono 内置运行时。
              </p>
            </div>
            <div>
              <FieldLabel>参数（每行一个）</FieldLabel>
              <textarea
                value={args}
                placeholder={"-y\n@modelcontextprotocol/server-filesystem"}
                className="min-h-[88px] w-full resize-y rounded-lg border border-line bg-card px-2.5 py-2 font-mono text-[12px] leading-5 outline-none focus:border-line-strong"
                onChange={(event) => setArgs(event.target.value)}
              />
            </div>
            <div>
              <FieldLabel>环境变量 JSON</FieldLabel>
              <textarea
                value={env}
                placeholder='{"FOO":"bar"}'
                className="min-h-[72px] w-full resize-y rounded-lg border border-line bg-card px-2.5 py-2 font-mono text-[12px] leading-5 outline-none focus:border-line-strong"
                onChange={(event) => setEnv(event.target.value)}
              />
            </div>
          </>
        )}
        {error || localError ? <p className="text-xs text-danger">{error || localError}</p> : null}
        {saving && progress ? (
          <p className="flex items-center gap-1.5 text-xs text-muted">
            <Loader2 className="size-3.5 shrink-0 animate-spin" />
            <span className="min-w-0 truncate">{progress}</span>
          </p>
        ) : null}
      </div>
      <div className="mt-4 flex items-center justify-between gap-2">
        {onDelete ? (
          <Button variant="danger" size="sm" disabled={saving} onClick={() => onConfirmDelete(true)}>
            <Trash2 className="size-3.5" />
            删除
          </Button>
        ) : (
          <span />
        )}
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={saving} onClick={onCancel}>
            取消
          </Button>
          <Button size="sm" disabled={!canSubmit || saving} onClick={() => void submit()}>
            {saving ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                {progress?.includes("下载") ? "下载中" : "校验中"}
              </>
            ) : (
              "保存"
            )}
          </Button>
        </div>
      </div>
      {onDelete ? (
        <ConfirmDialog
          open={confirmDelete}
          title="删除这个连接器？"
          description="会从用户级配置里移除，不影响已经关掉的工作区开关记录。"
          confirmLabel="删除"
          danger
          onCancel={() => onConfirmDelete(false)}
          onConfirm={() => void onDelete()}
        />
      ) : null}
    </div>
  );
}

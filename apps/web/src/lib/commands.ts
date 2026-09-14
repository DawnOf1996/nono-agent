import type { SlashCommandInfo } from "@nono/protocol";

export const FALLBACK_COMMANDS: SlashCommandInfo[] = [
  { name: "mcp", description: "List configured MCP servers" },
  { name: "compact", description: "Manually compact the conversation context" },
  { name: "permission", description: "Show or set permission mode" },
  { name: "model", description: "Show the current model and catalog" },
  { name: "tokens", description: "Show session token usage" },
  { name: "context", description: "Show context usage breakdown" },
  { name: "clear", description: "Clear the conversation" },
  { name: "rules", description: "List effective rules" },
  { name: "skills", description: "List skills, or enable/disable one" },
  { name: "plugins", description: "List loaded plugins" },
  { name: "memory", description: "List persistent memories" },
  { name: "hooks", description: "List user hooks" },
  { name: "help", description: "List available commands" },
];

export const COMMAND_LABELS: Record<string, string> = {
  compact: "压缩以上对话内容",
  mcp: "查看已连接的 MCP 服务器与工具",
  permission: "查看或切换权限模式",
  model: "查看当前模型",
  tokens: "查看 token 用量",
  context: "查看上下文占用",
  clear: "清空当前对话",
  rules: "查看生效中的规则",
  skills: "查看或开关 skills",
  plugins: "查看已加载插件",
  memory: "查看持久记忆",
  hooks: "查看用户 hooks",
  help: "列出可用指令",
};

export function commandLabel(command: SlashCommandInfo): string {
  return COMMAND_LABELS[command.name] || command.description;
}

export function filterCommands(commands: SlashCommandInfo[], query: string): SlashCommandInfo[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return commands;
  return commands.filter((item) => {
    const label = commandLabel(item).toLowerCase();
    return item.name.toLowerCase().includes(needle) || label.includes(needle);
  });
}

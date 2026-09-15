export const ADAPTER_LABELS: Record<string, string> = {
  openai_chat_completions: "openai-completions",
  openai_responses: "openai-responses",
  anthropic_messages: "anthropic-messages",
};

export function adapterLabel(api: string): string {
  return ADAPTER_LABELS[api] ?? api;
}

export type ProviderPreset = {
  id: string;
  display_name: string;
  api: string;
  base_url: string;
  hint: string;
};

export const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: "openai",
    display_name: "OpenAI",
    api: "openai_chat_completions",
    base_url: "https://api.openai.com/v1",
    hint: "官方 Chat Completions",
  },
  {
    id: "anthropic",
    display_name: "Anthropic",
    api: "anthropic_messages",
    base_url: "https://api.anthropic.com",
    hint: "Claude Messages",
  },
  {
    id: "deepseek",
    display_name: "DeepSeek",
    api: "openai_chat_completions",
    base_url: "https://api.deepseek.com/v1",
    hint: "兼容 OpenAI",
  },
  {
    id: "minimax",
    display_name: "MiniMax",
    api: "openai_responses",
    base_url: "https://api.minimax.cn/v1",
    hint: "Responses API",
  },
  {
    id: "moonshot",
    display_name: "Kimi",
    api: "openai_chat_completions",
    base_url: "https://api.moonshot.cn/v1",
    hint: "Moonshot",
  },
  {
    id: "dashscope",
    display_name: "通义千问",
    api: "openai_chat_completions",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    hint: "DashScope 兼容模式",
  },
  {
    id: "zhipu",
    display_name: "智谱 GLM",
    api: "openai_chat_completions",
    base_url: "https://open.bigmodel.cn/api/paas/v4",
    hint: "BigModel",
  },
  {
    id: "openrouter",
    display_name: "OpenRouter",
    api: "openai_chat_completions",
    base_url: "https://openrouter.ai/api/v1",
    hint: "多模型网关",
  },
];

export const PROVIDER_ID_PATTERN = /^[a-z][a-z0-9_-]*$/;

export function titleFromId(value: string): string {
  return value
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join("-");
}

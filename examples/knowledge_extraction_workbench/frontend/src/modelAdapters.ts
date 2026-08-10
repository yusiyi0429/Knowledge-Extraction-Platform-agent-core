export const MODEL_ADAPTERS = [
  {
    value: "DeepSeek",
    label: "DeepSeek 专用接口",
    description: "适用于 DeepSeek 官方兼容接口。",
  },
  {
    value: "OpenAI",
    label: "OpenAI 兼容接口",
    description: "适用于 OpenAI、MiniMax、Kimi、GLM、vLLM 等兼容服务。",
  },
  {
    value: "Anthropic",
    label: "Anthropic 原生接口",
    description: "适用于 Anthropic Messages API。",
  },
  {
    value: "DashScope",
    label: "DashScope 专用接口",
    description: "适用于阿里云百炼 DashScope 服务。",
  },
  {
    value: "OpenRouter",
    label: "OpenRouter 网关",
    description: "适用于通过 OpenRouter 统一路由的模型。",
  },
  {
    value: "SiliconFlow",
    label: "SiliconFlow 网关",
    description: "适用于通过 SiliconFlow 接入的模型。",
  },
  {
    value: "InferenceAffinity",
    label: "InferenceAffinity 高级运行时",
    description: "仅用于已经部署 InferenceAffinity 的高级环境。",
  },
] as const;

export function modelAdapterInfo(value: string) {
  return MODEL_ADAPTERS.find((adapter) => adapter.value === value);
}

export function modelAdapterLabel(value: string): string {
  return modelAdapterInfo(value)?.label || value;
}

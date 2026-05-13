from dataclasses import dataclass, field


@dataclass
class ModelProfile:
    name: str
    supports_thinking: bool = False
    supports_vision: bool = False
    supports_tools: bool = True
    num_ctx: int = 4096
    # 在這裡註冊模型提供給 Ollama 的工具定義列表
    tools: list = field(default_factory=list)


# 已知模型登記表——新增模型只需在這裡加一行
REGISTRY: dict[str, ModelProfile] = {
    "qwen3-vl:4b": ModelProfile(
        "qwen3-vl:4b",
        supports_thinking=True,
        supports_vision=True,
        num_ctx=4096,
    ),
    "qwen3:4b": ModelProfile(
        "qwen3:4b",
        supports_thinking=True,
        supports_vision=False,
        num_ctx=4096,
    ),
    "qwen3:8b": ModelProfile(
        "qwen3:8b",
        supports_thinking=True,
        supports_vision=False,
        num_ctx=8192,
    ),
    "llama3.2:3b": ModelProfile(
        "llama3.2:3b",
        supports_thinking=False,
        supports_vision=False,
        num_ctx=4096,
    ),
    "gemma3:4b": ModelProfile(
        "gemma3:4b",
        supports_thinking=False,
        supports_vision=True,
        num_ctx=4096,
    ),
}


def get_profile(model: str) -> ModelProfile:
    """回傳模型的 profile，未登記的模型用預設側做安全備選。"""
    return REGISTRY.get(model, ModelProfile(model))

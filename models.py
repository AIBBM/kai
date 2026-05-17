from dataclasses import dataclass, replace


@dataclass
class ModelProfile:
    name: str
    supports_thinking: bool = False
    supports_vision: bool = False
    supports_tools: bool = True
    num_ctx: int = 4096


# 已知模型登記表——新增模型只需在這裡加一行
REGISTRY: dict[str, ModelProfile] = {
    "qwen3-vl:4b": ModelProfile(
        "qwen3-vl:4b",
        supports_thinking=True,
        supports_vision=True,
        num_ctx=16384,
    ),
    "qwen3:4b": ModelProfile(
        "qwen3:4b",
        supports_thinking=True,
        supports_vision=False,
        num_ctx=8192,
    ),
    "qwen3:8b": ModelProfile(
        "qwen3:8b",
        supports_thinking=True,
        supports_vision=False,
        num_ctx=16384,
    ),
    "qwen3.5:4b": ModelProfile(
        "qwen3.5:4b",
        supports_thinking=True,
        supports_vision=True,
        num_ctx=16384,
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
    "gemma4:e2b": ModelProfile(
        "gemma4:e2b",
        supports_thinking=True,
        supports_vision=True,
        num_ctx=8192,
    ),
    "gemma4:e4b": ModelProfile(
        "gemma4:e4b",
        supports_thinking=True,
        supports_vision=True,
        num_ctx=8192,
    ),
    "deepseek-r1:1.5b": ModelProfile(
        "deepseek-r1:1.5b",
        supports_thinking=True,
        supports_tools=False,
        num_ctx=8192,
    ),
    "MFDoom/deepseek-r1-tool-calling:1.5b": ModelProfile(
        "MFDoom/deepseek-r1-tool-calling:1.5b",
        supports_thinking=False,
        supports_tools=True,
        num_ctx=8192,
    ),
}


def get_profile(model: str) -> ModelProfile:
    """回傳模型的 profile，未登記的模型用預設側做安全備選。"""
    profile = REGISTRY.get(model, ModelProfile(model))
    return replace(profile)  # copy 避免 Agent.__init__ 修改 num_ctx 時汙染 REGISTRY

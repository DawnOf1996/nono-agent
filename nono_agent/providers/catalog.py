"""LLM Provider 目录 —— 分层 YAML 描述供应商与模型，解析为 ProviderConfig。

用户级：``~/.nono-agent/config.yaml``
项目级：``.nono-agent/config.yaml``（从 cwd 向上查找）
可选 overlay：CLI ``--config`` 指向的 YAML

优先级：overlay > 项目 > 用户。
密钥不写入 YAML：``api_key_env`` 只是指针，真实 key 来自进程环境
或用户级 ``~/.nono-agent/credentials``（进程环境优先）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nono_agent.core.config import ProviderConfig
from nono_agent.paths import project_dir, user_home
from nono_agent.providers.base import CANONICAL_ADAPTERS
from nono_agent.settings.merge import deep_merge

USER_CONFIG_PATH = user_home() / "config.yaml"
USER_CREDENTIALS_PATH = user_home() / "credentials"
PROJECT_CONFIG_NAME = "config.yaml"

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_token_size(raw: str) -> int | None:
    """解析 token 数量：纯整数，或 ``128K`` / ``1M``（十进制，K=1000、M=1000000）。"""
    text = raw.strip().replace(",", "").replace("_", "")
    if not text:
        return None
    match = re.fullmatch(r"(\d+)\s*([kKmM])?", text)
    if not match:
        return None
    n = int(match.group(1))
    suffix = (match.group(2) or "").upper()
    if suffix == "K":
        n *= 1_000
    elif suffix == "M":
        n *= 1_000_000
    return n if n > 0 else None


def _coerce_token_size(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("token size must be an integer, not a boolean")
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        parsed_float = int(value)
        return parsed_float if parsed_float > 0 else None
    if isinstance(value, str):
        parsed_str = parse_token_size(value)
        if parsed_str is None:
            raise ValueError(f"Invalid token size '{value}'")
        return parsed_str
    raise ValueError(f"Invalid token size {value!r}")


class ModelEntry(BaseModel):
    """目录中的一条模型。``id`` 发给 API；``name`` 仅展示。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context_window", "max_tokens", mode="before")
    @classmethod
    def _parse_sizes(cls, value: object) -> int | None:
        return _coerce_token_size(value)

    @field_validator("extra_body", mode="before")
    @classmethod
    def _parse_extra_body(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict) or isinstance(value, list):
            raise ValueError("extra_body must be a JSON object")
        return {str(key): item for key, item in value.items() if str(key).strip()}


class NamedProvider(BaseModel):
    """命名供应商 —— 协议 + 端点 + 模型列表。"""

    model_config = ConfigDict(extra="ignore")

    display_name: str
    api_key_env: str
    api: str
    base_url: str | None = None
    models: list[ModelEntry] = Field(default_factory=list)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    timeout: float | None = None


class AgentDefaultModel(BaseModel):
    """默认选用的供应商 id + 模型 id。"""

    model_config = ConfigDict(extra="ignore")

    provider: str
    model: str


class LlmProviderSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    providers: dict[str, NamedProvider] = Field(default_factory=dict)


class LlmCatalog(BaseModel):
    """合并后的 LLM 目录。"""

    model_config = ConfigDict(extra="ignore")

    agent_default_model: AgentDefaultModel | None = None
    llm_provider: LlmProviderSection = Field(default_factory=LlmProviderSection)

    @property
    def providers(self) -> dict[str, NamedProvider]:
        return self.llm_provider.providers

    def is_empty(self) -> bool:
        return not self.providers or self.agent_default_model is None

    def resolve(
        self,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> ProviderConfig:
        """解析为引擎可用的 ProviderConfig。缺省用 agent_default_model。"""
        pid, mid = self._pick_ids(provider_id, model_id)
        named = self.providers.get(pid)
        if named is None:
            available = sorted(self.providers)
            raise ValueError(
                f"Unknown provider '{pid}'. Available providers: {available}"
            )
        if named.api not in CANONICAL_ADAPTERS:
            raise ValueError(
                f"Unknown api '{named.api}' on provider '{pid}'. "
                f"Available: {list(CANONICAL_ADAPTERS)}"
            )
        entry = next((m for m in named.models if m.id == mid), None)
        if entry is None:
            ids = [m.id for m in named.models]
            raise ValueError(
                f"Unknown model '{mid}' on provider '{pid}'. Available models: {ids}"
            )
        api_key = lookup_api_key(named.api_key_env)
        if not api_key:
            raise ValueError(
                f"No API key for '{named.api_key_env}' "
                f"(provider '{pid}' / {named.display_name}). "
                "Store it in ~/.nono-agent/credentials or set the environment variable."
            )
        kwargs: dict[str, Any] = {
            "model_id": entry.id,
            "adapter": named.api,
            "api_key": api_key,
            "base_url": named.base_url,
            "model_context": entry.context_window,
            "max_tokens": entry.max_tokens,
            "extra_headers": named.extra_headers,
            "extra_body": deep_merge(dict(named.extra_body), dict(entry.extra_body)),
        }
        if named.timeout is not None:
            kwargs["timeout"] = named.timeout
        return ProviderConfig(**kwargs)

    def _pick_ids(
        self,
        provider_id: str | None,
        model_id: str | None,
    ) -> tuple[str, str]:
        default = self.agent_default_model
        if provider_id and model_id:
            return provider_id, model_id
        if model_id and not provider_id:
            return self._provider_for_model(model_id), model_id
        if default is None:
            raise ValueError(
                "No agent_default_model in catalog; specify provider and model."
            )
        return (
            provider_id or default.provider,
            model_id or default.model,
        )

    def _provider_for_model(self, model_id: str) -> str:
        default = self.agent_default_model
        if default is not None:
            named = self.providers.get(default.provider)
            if named is not None and any(m.id == model_id for m in named.models):
                return default.provider
        matches = [
            pid
            for pid, named in self.providers.items()
            if any(m.id == model_id for m in named.models)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(
                f"Model '{model_id}' exists on multiple providers {matches}; "
                "specify provider."
            )
        raise ValueError(f"Unknown model '{model_id}'.")

    def to_yaml_dict(self) -> dict[str, Any]:
        """序列化为 YAML 友好的字典（省略空可选字段）。"""
        return self.model_dump(exclude_none=True, exclude_defaults=True)

    def format_listing(self, current_model: str | None = None) -> str:
        """当前模型 + 目录列表（供 ``/model`` 展示）。"""
        lines: list[str] = []
        if current_model:
            lines.append(self._format_current(current_model))
        if self.providers:
            lines.append("Catalog:")
            for pid, named in self.providers.items():
                lines.append(f"  {pid} / {named.display_name}")
                for entry in named.models:
                    label = entry.id if not entry.name else f"{entry.id} ({entry.name})"
                    lines.append(f"    {label}")
        return "\n".join(lines) if lines else "Catalog: (empty)"

    def _format_current(self, current_model: str) -> str:
        default = self.agent_default_model
        preferred = default.provider if default is not None else None
        matches: list[tuple[str, NamedProvider, ModelEntry]] = []
        for pid, named in self.providers.items():
            for entry in named.models:
                if entry.id == current_model:
                    matches.append((pid, named, entry))
        chosen: tuple[str, NamedProvider, ModelEntry] | None = None
        if preferred:
            chosen = next((m for m in matches if m[0] == preferred), None)
        if chosen is None and matches:
            chosen = matches[0]
        if chosen is None:
            return f"Current: {current_model}"
        pid, named, entry = chosen
        shown = entry.id if not entry.name else f"{entry.id} ({entry.name})"
        return f"Current: {pid} / {named.display_name} — {shown}"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def dump_yaml(data: dict[str, Any]) -> str:
    return yaml.dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )


def api_key_env_name(provider_id: str) -> str:
    """由 provider id 稳定生成凭据键名（``NONO_{ID}_API_KEY``）。"""
    cleaned = "".join(c if c.isalnum() else "_" for c in provider_id.upper())
    cleaned = "_".join(part for part in cleaned.split("_") if part) or "PROVIDER"
    if cleaned[0].isdigit():
        cleaned = f"P_{cleaned}"
    return f"NONO_{cleaned}_API_KEY"


def load_credentials(path: Path | None = None) -> dict[str, str]:
    """读取用户级 credentials（``KEY=value``，``#`` 行为注释）。"""
    dest = path or USER_CREDENTIALS_PATH
    if not dest.exists():
        return {}
    try:
        text = dest.read_text(encoding="utf-8")
    except OSError:
        return {}
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if _ENV_NAME_RE.match(name):
            result[name] = value
    return result


def lookup_api_key(env_name: str, *, credentials_path: Path | None = None) -> str:
    """进程环境优先，否则读用户级 credentials。"""
    from_env = os.environ.get(env_name, "")
    if from_env:
        return from_env
    return load_credentials(credentials_path).get(env_name, "")


def save_credential(
    env_name: str,
    value: str,
    path: Path | None = None,
) -> None:
    """把一条密钥 upsert 到 credentials，chmod 0600，并写入当前进程环境。"""
    if not _ENV_NAME_RE.match(env_name):
        raise ValueError(f"Invalid credential name '{env_name}'.")
    if "\n" in value or "\r" in value:
        raise ValueError("API key must not contain newlines.")
    dest = path or USER_CREDENTIALS_PATH
    existing = load_credentials(dest)
    existing[env_name] = value
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# nono-agent credentials — do not commit"]
    for name, key in existing.items():
        lines.append(f"{name}={key}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        dest.chmod(0o600)
    except OSError:
        pass
    os.environ[env_name] = value


def find_project_config(cwd: str | Path) -> Path | None:
    """从 cwd 向上查找第一个项目级 ``.nono-agent/config.yaml``。

    跳过用户主目录下的 ``~/.nono-agent/config.yaml``，避免把用户级目录当成项目配置。
    """
    current = Path(cwd).resolve()
    user_file = (user_home() / PROJECT_CONFIG_NAME).resolve()
    while True:
        candidate = project_dir(current) / PROJECT_CONFIG_NAME
        if candidate.exists() and candidate.resolve() != user_file:
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


def _merge_models(base: list[Any], override: list[Any]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw in base:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        mid = str(raw["id"])
        by_id[mid] = dict(raw)
        order.append(mid)
    for raw in override:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        mid = str(raw["id"])
        if mid in by_id:
            by_id[mid] = _merge_model_dicts(by_id[mid], raw)
        else:
            by_id[mid] = dict(raw)
            order.append(mid)
    return [by_id[i] for i in order]


def _merge_model_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _merge_named_provider(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key == "models":
            continue
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    if "models" in override or "models" in base:
        merged["models"] = _merge_models(
            list(base.get("models") or []),
            list(override.get("models") or []),
        )
    return merged


def merge_catalog_dicts(*layers: dict[str, Any]) -> dict[str, Any]:
    """按从低到高优先级合并 catalog 字典。"""
    result: dict[str, Any] = {}
    for layer in layers:
        if not layer:
            continue
        if "agent_default_model" in layer:
            result["agent_default_model"] = layer["agent_default_model"]
        incoming = (layer.get("llm_provider") or {}).get("providers") or {}
        if not isinstance(incoming, dict):
            continue
        existing = (result.get("llm_provider") or {}).get("providers") or {}
        merged_providers: dict[str, Any] = dict(existing)
        for pid, pdata in incoming.items():
            if not isinstance(pdata, dict):
                continue
            if pid not in merged_providers:
                merged_providers[pid] = dict(pdata)
            else:
                merged_providers[pid] = _merge_named_provider(
                    merged_providers[pid], pdata
                )
        result["llm_provider"] = {"providers": merged_providers}
    return result


def load_catalog(
    cwd: str | Path = ".",
    *,
    overlay_path: str | Path | None = None,
    user_path: Path | None = None,
) -> LlmCatalog:
    """加载并合并用户 / 项目 / overlay YAML，返回 LlmCatalog。"""
    user_data = _read_yaml(user_path or USER_CONFIG_PATH)
    project_file = find_project_config(cwd)
    project_data = _read_yaml(project_file) if project_file else {}
    overlay_data = _read_yaml(Path(overlay_path)) if overlay_path else {}
    merged = merge_catalog_dicts(user_data, project_data, overlay_data)
    if not merged:
        return LlmCatalog()
    return LlmCatalog.model_validate(merged)


def save_user_catalog(catalog: LlmCatalog, path: Path | None = None) -> None:
    """把目录写到用户级 config.yaml。"""
    dest = path or USER_CONFIG_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(dump_yaml(catalog.to_yaml_dict()), encoding="utf-8")


def catalog_present(
    cwd: str | Path = ".",
    overlay_path: str | Path | None = None,
) -> bool:
    """YAML 是否已有默认同 + 至少一个供应商（不检查环境变量）。"""
    try:
        catalog = load_catalog(cwd, overlay_path=overlay_path)
    except Exception:
        return False
    return not catalog.is_empty()


def is_base_configured(
    cwd: str | Path = ".",
    overlay_path: str | Path | None = None,
) -> bool:
    """目录可解析且默认模型对应的密钥已在环境变量或 credentials 中。"""
    try:
        catalog = load_catalog(cwd, overlay_path=overlay_path)
        if catalog.is_empty():
            return False
        catalog.resolve()
        return True
    except Exception:
        return False

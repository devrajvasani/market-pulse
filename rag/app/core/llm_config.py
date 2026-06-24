"""Multi-provider LLM configuration: a YAML registry + env-resolved API keys.

The active embed/generation providers, their models, and optional fallbacks come from a
version-controlled YAML file (``rag/config/llm.yaml``) — **no secrets**. API keys are referenced
there only by env-var *name* and read from the environment (``.env``), so provider selection is
declarative and secrets never enter the repo. The active provider can also be overridden per-process
via ``RAG_EMBED_PROVIDER`` / ``RAG_GEN_PROVIDER`` (+ ``..._FALLBACK``) without editing the file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from rag.app.core.errors import ConfigError

# Providers that expose an embeddings API (Anthropic + Groq are generation-only).
EMBED_CAPABLE = frozenset({"ollama", "gemini", "openai", "bedrock"})
KNOWN_PROVIDERS = frozenset({"ollama", "gemini", "openai", "anthropic", "groq", "bedrock"})

_DEFAULT_YAML = Path(__file__).resolve().parents[2] / "config" / "llm.yaml"
_DEFAULT_OLLAMA_HOST = "http://localhost:11434"


@dataclass(frozen=True)
class GenParams:
    """Shared generation parameters applied to every provider."""

    max_output_tokens: int
    temperature: float
    timeout_s: int


@dataclass(frozen=True)
class ProviderSpec:
    """Declarative, non-secret config for one provider (from the YAML registry)."""

    name: str
    api_key_env: str | None = None
    host_env: str | None = None
    host: str | None = None
    region: str | None = None
    embed_model: str | None = None
    gen_model: str | None = None


@dataclass(frozen=True)
class ResolvedProvider:
    """A :class:`ProviderSpec` with its API key + host resolved from the environment."""

    name: str
    api_key: str | None
    host: str | None
    region: str | None
    embed_model: str | None
    gen_model: str | None


@dataclass(frozen=True)
class LLMConfig:
    """The active LLM selection + the provider registry."""

    embed_provider: str
    gen_provider: str
    gen_fallback: str | None
    params: GenParams
    providers: dict[str, ProviderSpec]

    def spec(self, name: str) -> ProviderSpec:
        """Return the :class:`ProviderSpec` for ``name`` (raises if unknown)."""
        if name not in self.providers:
            raise ConfigError(
                f"LLM provider {name!r} is not defined in the registry ({sorted(self.providers)})."
            )
        return self.providers[name]

    def resolve(self, name: str) -> ResolvedProvider:
        """Resolve a provider's API key + host from the environment (no validation).

        Host precedence: the ``host_env`` value (e.g. ``OLLAMA_HOST``) > the literal ``host`` in
        the YAML > the built-in localhost default.
        """
        spec = self.spec(name)
        api_key = (os.getenv(spec.api_key_env) or "").strip() if spec.api_key_env else None
        host = None
        if spec.host_env or spec.host:
            env_host = (os.getenv(spec.host_env) or "").strip() if spec.host_env else ""
            host = env_host or spec.host or _DEFAULT_OLLAMA_HOST
        return ResolvedProvider(
            name=spec.name,
            api_key=api_key or None,
            host=host,
            region=spec.region,
            embed_model=spec.embed_model,
            gen_model=spec.gen_model,
        )

    def require_resolved(self, name: str, *, role: str) -> ResolvedProvider:
        """Resolve a provider for a role, failing fast if its API key/model is missing.

        Args:
            name: Provider name.
            role: ``"embed"`` or ``"generate"`` — controls which model + capability is required.

        Raises:
            ConfigError: if the provider can't serve the role (missing key, missing model, or
                an embed role asked of a generation-only provider).
        """
        spec = self.spec(name)
        if role == "embed" and name not in EMBED_CAPABLE:
            raise ConfigError(
                f"Provider {name!r} has no embeddings API; choose one of "
                f"{sorted(EMBED_CAPABLE)} as the embed provider."
            )
        resolved = self.resolve(name)
        if spec.api_key_env and not resolved.api_key:
            raise ConfigError(
                f"LLM provider {name!r} needs an API key: set {spec.api_key_env} in your .env."
            )
        if resolved.api_key and (
            not resolved.api_key.isascii() or any(c.isspace() for c in resolved.api_key)
        ):
            raise ConfigError(
                f"{spec.api_key_env} doesn't look like a valid key (it contains spaces or "
                "non-ASCII characters) — a placeholder/comment may have been loaded. "
                "Put the real key value in .env."
            )
        if role == "embed" and not resolved.embed_model:
            raise ConfigError(f"Provider {name!r} has no embed_model configured in llm.yaml.")
        if role == "generate" and not resolved.gen_model:
            raise ConfigError(f"Provider {name!r} has no gen_model configured in llm.yaml.")
        return resolved


def _as_int(value: Any, name: str, default: int) -> int:
    """Coerce a YAML value to a positive int (raise ConfigError otherwise)."""
    if value is None:
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"llm.yaml: {name} must be an integer, got {value!r}.") from exc
    if result < 1:
        raise ConfigError(f"llm.yaml: {name} must be >= 1, got {result}.")
    return result


def _as_temperature(value: Any) -> float:
    """Coerce a YAML value to a temperature in [0.0, 2.0]."""
    if value is None:
        return 0.2
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"llm.yaml: temperature must be a number, got {value!r}.") from exc
    if not 0.0 <= result <= 2.0:
        raise ConfigError(f"llm.yaml: temperature must be in [0.0, 2.0], got {result}.")
    return result


def _selection(raw: dict, key: str, env_provider: str, env_fallback: str) -> tuple[str, str | None]:
    """Read a {provider, fallback} block with env overrides; return (provider, fallback|None)."""
    block = raw.get(key) or {}
    if not isinstance(block, dict):
        raise ConfigError(f"llm.yaml: '{key}' must be a mapping with a 'provider' key.")
    provider = (os.getenv(env_provider) or "").strip() or (block.get("provider") or "").strip()
    if not provider:
        raise ConfigError(f"llm.yaml: '{key}.provider' is required.")
    fallback = (os.getenv(env_fallback) or "").strip() or (block.get("fallback") or "")
    fallback = fallback.strip() or None
    return provider, fallback


def _parse_providers(raw: dict) -> dict[str, ProviderSpec]:
    """Parse the ``providers:`` mapping into :class:`ProviderSpec` objects."""
    providers_raw = raw.get("providers")
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise ConfigError("llm.yaml: a non-empty 'providers' mapping is required.")
    specs: dict[str, ProviderSpec] = {}
    for name, body in providers_raw.items():
        body = body or {}
        if not isinstance(body, dict):
            raise ConfigError(f"llm.yaml: provider {name!r} must be a mapping.")
        specs[name] = ProviderSpec(
            name=name,
            api_key_env=body.get("api_key_env"),
            host_env=body.get("host_env"),
            host=body.get("host"),
            region=body.get("region"),
            embed_model=body.get("embed_model"),
            gen_model=body.get("gen_model"),
        )
    return specs


def load_llm_config(path: str | os.PathLike[str] | None = None) -> LLMConfig:
    """Load + validate the LLM registry from YAML (``RAG_LLM_CONFIG`` overrides the path).

    Raises:
        ConfigError: if the file is missing/invalid, a selected provider is undefined, an embed
            provider can't embed, or a numeric parameter is out of range.
    """
    yaml_path = Path(path or os.getenv("RAG_LLM_CONFIG") or _DEFAULT_YAML)
    if not yaml_path.is_file():
        raise ConfigError(
            f"LLM config file not found: {yaml_path} (set RAG_LLM_CONFIG to override)."
        )
    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"llm.yaml is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("llm.yaml must be a mapping at the top level.")

    providers = _parse_providers(raw)
    embed_provider, embed_fallback = _selection(
        raw, "embed", "RAG_EMBED_PROVIDER", "RAG_EMBED_FALLBACK"
    )
    if embed_fallback:
        raise ConfigError(
            "An embed fallback is not supported: a different embedder produces an incompatible "
            "vector space (the index would be mislabeled). Remove embed.fallback / "
            "RAG_EMBED_FALLBACK."
        )
    gen_provider, gen_fallback = _selection(raw, "generate", "RAG_GEN_PROVIDER", "RAG_GEN_FALLBACK")

    params = GenParams(
        max_output_tokens=_as_int(raw.get("max_output_tokens"), "max_output_tokens", 1024),
        temperature=_as_temperature(raw.get("temperature")),
        timeout_s=_as_int(raw.get("http_timeout_s"), "http_timeout_s", 120),
    )

    config = LLMConfig(
        embed_provider=embed_provider,
        gen_provider=gen_provider,
        gen_fallback=gen_fallback,
        params=params,
        providers=providers,
    )
    _validate_selection(config)
    return config


def _validate_selection(config: LLMConfig) -> None:
    """Validate that every selected provider exists + can serve its role (defined-ness only)."""
    config.spec(config.embed_provider)  # must exist
    if config.embed_provider not in EMBED_CAPABLE:
        raise ConfigError(
            f"Embed provider {config.embed_provider!r} has no embeddings API; "
            f"pick one of {sorted(EMBED_CAPABLE)}."
        )
    for name in (config.gen_provider, config.gen_fallback):
        if name is not None:
            config.spec(name)  # must exist

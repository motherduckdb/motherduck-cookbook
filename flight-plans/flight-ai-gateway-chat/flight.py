"""Store one OpenAI-compatible chat completion from a selected AI provider."""

from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, cast

import duckdb
import httpx

ProviderName: TypeAlias = Literal[
    "openrouter", "cloudflare-ai-gateway", "vercel-ai-gateway", "together-ai"
]
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
RESULTS_TABLE = "ai_chat_results"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RunConfig:
    provider: ProviderName
    model: str
    prompt: str
    system_prompt: str | None
    max_tokens: int
    timeout_seconds: float
    results_database: str
    max_result_chars: int
    cloudflare_account_id: str | None
    cloudflare_ai_gateway_id: str | None


HeaderBuilder: TypeAlias = Callable[[RunConfig, str], dict[str, str]]
EndpointBuilder: TypeAlias = Callable[[RunConfig], str]


@dataclass(frozen=True)
class ProviderSpec:
    credential_env: str
    endpoint: EndpointBuilder
    headers: HeaderBuilder


@dataclass(frozen=True)
class ChatCompletion:
    provider_response_id: str | None
    returned_model: str
    finish_reason: str | None
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True)
class CompletionRecord:
    run_id: str
    provider: ProviderName
    configured_model: str
    provider_response_id: str | None
    returned_model: str
    finish_reason: str | None
    result_text: str
    result_characters: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    request_duration_ms: int


def standard_headers(_config: RunConfig, credential: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credential}",
        "Content-Type": "application/json",
    }


def cloudflare_headers(config: RunConfig, credential: str) -> dict[str, str]:
    headers = standard_headers(config, credential)
    headers["cf-aig-max-attempts"] = "1"
    if config.cloudflare_ai_gateway_id:
        headers["cf-aig-gateway-id"] = config.cloudflare_ai_gateway_id
    return headers


def cloudflare_endpoint(config: RunConfig) -> str:
    account_id = config.cloudflare_account_id
    if account_id is None:
        raise ConfigError("CLOUDFLARE_ACCOUNT_ID is required for cloudflare-ai-gateway")
    return (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/ai/v1/chat/completions"
    )


def fixed_endpoint(url: str) -> EndpointBuilder:
    def endpoint(_config: RunConfig) -> str:
        return url

    return endpoint


PROVIDERS: dict[ProviderName, ProviderSpec] = {
    "openrouter": ProviderSpec(
        credential_env="OPENROUTER_API_KEY",
        endpoint=fixed_endpoint("https://openrouter.ai/api/v1/chat/completions"),
        headers=standard_headers,
    ),
    "cloudflare-ai-gateway": ProviderSpec(
        credential_env="CLOUDFLARE_API_TOKEN",
        endpoint=cloudflare_endpoint,
        headers=cloudflare_headers,
    ),
    "vercel-ai-gateway": ProviderSpec(
        credential_env="AI_GATEWAY_API_KEY",
        endpoint=fixed_endpoint("https://ai-gateway.vercel.sh/v1/chat/completions"),
        headers=standard_headers,
    ),
    "together-ai": ProviderSpec(
        credential_env="TOGETHER_API_KEY",
        endpoint=fixed_endpoint("https://api.together.ai/v1/chat/completions"),
        headers=standard_headers,
    ),
}


def required_value(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def optional_value(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def positive_int(env: Mapping[str, str], name: str, default: int, maximum: int) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not 0 < value <= maximum:
        raise ConfigError(f"{name} must be between 1 and {maximum}")
    return value


def positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero")
    return value


def identifier(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name, default).strip()
    if not IDENTIFIER_RE.fullmatch(value):
        raise ConfigError(f"{name} must be a simple SQL identifier")
    return value


def parse_run_config(env: Mapping[str, str]) -> RunConfig:
    provider_value = required_value(env, "AI_PROVIDER")
    if provider_value not in PROVIDERS:
        allowed = ", ".join(PROVIDERS)
        raise ConfigError(f"AI_PROVIDER must be one of: {allowed}")
    provider = cast(ProviderName, provider_value)
    model = required_value(env, "AI_MODEL")
    cloudflare_account_id = optional_value(env, "CLOUDFLARE_ACCOUNT_ID")
    cloudflare_ai_gateway_id = optional_value(env, "CLOUDFLARE_AI_GATEWAY_ID")
    if provider == "cloudflare-ai-gateway" and cloudflare_account_id is None:
        raise ConfigError("CLOUDFLARE_ACCOUNT_ID is required for cloudflare-ai-gateway")
    if (
        provider == "cloudflare-ai-gateway"
        and model.startswith("@cf/")
        and cloudflare_ai_gateway_id is None
    ):
        raise ConfigError(
            "CLOUDFLARE_AI_GATEWAY_ID is required when AI_MODEL starts with '@cf/'"
        )
    return RunConfig(
        provider=provider,
        model=model,
        prompt=required_value(env, "AI_PROMPT"),
        system_prompt=optional_value(env, "AI_SYSTEM_PROMPT"),
        max_tokens=positive_int(env, "AI_MAX_TOKENS", default=512, maximum=16_384),
        timeout_seconds=positive_float(env, "AI_TIMEOUT_SECONDS", default=45.0),
        results_database=identifier(env, "RESULTS_DATABASE", default="ai_gateway"),
        max_result_chars=positive_int(
            env, "MAX_RESULT_CHARS", default=16_000, maximum=1_000_000
        ),
        cloudflare_account_id=cloudflare_account_id,
        cloudflare_ai_gateway_id=cloudflare_ai_gateway_id,
    )


def resolve_credential(spec: ProviderSpec, env: Mapping[str, str]) -> str:
    direct = optional_value(env, spec.credential_env)
    if direct:
        return direct
    suffix = f"_{spec.credential_env}"
    namespaced = [
        value.strip()
        for name, value in env.items()
        if name.endswith(suffix) and value.strip()
    ]
    if len(namespaced) == 1:
        return namespaced[0]
    if len(namespaced) > 1:
        raise ConfigError(f"multiple Flights secrets provide {spec.credential_env}")
    raise ConfigError(
        f"{spec.credential_env} is required directly or from one Flights secret"
    )


def build_payload(config: RunConfig) -> dict[str, object]:
    messages: list[dict[str, str]] = []
    if config.system_prompt:
        messages.append({"role": "system", "content": config.system_prompt})
    messages.append({"role": "user", "content": config.prompt})
    return {
        "model": config.model,
        "messages": messages,
        "max_tokens": config.max_tokens,
        "stream": False,
    }


def send_chat(
    client: httpx.Client, spec: ProviderSpec, config: RunConfig, credential: str
) -> tuple[Mapping[str, Any], int]:
    started = time.monotonic()
    response = client.post(
        spec.endpoint(config),
        headers=spec.headers(config, credential),
        json=build_payload(config),
    )
    duration_ms = round((time.monotonic() - started) * 1_000)
    if response.is_error:
        raise RuntimeError(f"{config.provider} returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{config.provider} returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise TypeError(f"{config.provider} returned a non-object JSON response")
    return payload, duration_ms


def optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def optional_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def parse_completion(payload: Mapping[str, Any], fallback_model: str) -> ChatCompletion:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise RuntimeError("provider response did not include a chat completion choice")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise TypeError("provider response did not include a completion message")
    text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("provider response did not include a text completion")
    usage = payload.get("usage")
    usage_map: Mapping[str, object] = usage if isinstance(usage, Mapping) else {}
    return ChatCompletion(
        provider_response_id=optional_string(payload.get("id")),
        returned_model=optional_string(payload.get("model")) or fallback_model,
        finish_reason=optional_string(choice.get("finish_reason")),
        text=text,
        prompt_tokens=optional_count(usage_map.get("prompt_tokens")),
        completion_tokens=optional_count(usage_map.get("completion_tokens")),
        total_tokens=optional_count(usage_map.get("total_tokens")),
    )


def sanitize_result(text: str, sensitive_values: tuple[str, ...], maximum: int) -> str:
    cleaned = "".join(char for char in text if char >= " " or char in "\n\r\t")
    for value in sensitive_values:
        if value:
            cleaned = cleaned.replace(value, "[REDACTED]")
    cleaned = cleaned.strip()
    if len(cleaned) <= maximum:
        return cleaned
    marker = "\n[truncated]"
    if maximum <= len(marker):
        return cleaned[:maximum]
    return cleaned[: maximum - len(marker)] + marker


def ensure_results_table(connection: duckdb.DuckDBPyConnection, database: str) -> None:
    connection.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.main")
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {database}.main.{RESULTS_TABLE} (
            run_id UUID,
            completed_at TIMESTAMPTZ,
            provider VARCHAR,
            configured_model VARCHAR,
            provider_response_id VARCHAR,
            returned_model VARCHAR,
            finish_reason VARCHAR,
            result_text VARCHAR,
            result_characters INTEGER,
            prompt_tokens BIGINT,
            completion_tokens BIGINT,
            total_tokens BIGINT,
            request_duration_ms BIGINT
        )
        """
    )


def persist_result(
    connection: duckdb.DuckDBPyConnection, database: str, record: CompletionRecord
) -> None:
    connection.execute(
        f"""
        INSERT INTO {database}.main.{RESULTS_TABLE} VALUES (
            ?, now(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            record.run_id,
            record.provider,
            record.configured_model,
            record.provider_response_id,
            record.returned_model,
            record.finish_reason,
            record.result_text,
            record.result_characters,
            record.prompt_tokens,
            record.completion_tokens,
            record.total_tokens,
            record.request_duration_ms,
        ],
    )


def main() -> None:
    config = parse_run_config(os.environ)
    required_value(os.environ, "MOTHERDUCK_TOKEN")
    spec = PROVIDERS[config.provider]
    credential = resolve_credential(spec, os.environ)
    connection = duckdb.connect("md:")
    try:
        ensure_results_table(connection, config.results_database)
        with httpx.Client(timeout=config.timeout_seconds) as client:
            payload, duration_ms = send_chat(client, spec, config, credential)
        completion = parse_completion(payload, config.model)
        result_text = sanitize_result(
            completion.text,
            (credential, config.prompt, config.system_prompt or ""),
            config.max_result_chars,
        )
        record = CompletionRecord(
            run_id=str(uuid.uuid4()),
            provider=config.provider,
            configured_model=config.model,
            provider_response_id=completion.provider_response_id,
            returned_model=completion.returned_model,
            finish_reason=completion.finish_reason,
            result_text=result_text,
            result_characters=len(result_text),
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.total_tokens,
            request_duration_ms=duration_ms,
        )
        persist_result(connection, config.results_database, record)
    finally:
        connection.close()
    print(
        f"Stored one {config.provider} completion in "
        f"{config.results_database}.main.{RESULTS_TABLE}."
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import duckdb
import httpx
import pytest

FLIGHT_PATH = (
    Path(__file__).resolve().parents[1]
    / "flight-plans"
    / "flight-ai-gateway-chat"
    / "flight.py"
)
spec = importlib.util.spec_from_file_location("flight_ai_gateway_chat", FLIGHT_PATH)
assert spec is not None
assert spec.loader is not None
flight = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = flight
spec.loader.exec_module(flight)


def env_for(provider: str = "openrouter") -> dict[str, str]:
    env = {
        "AI_PROVIDER": provider,
        "AI_MODEL": "openai/gpt-4.1-mini",
        "AI_PROMPT": "safe prompt",
    }
    if provider == "cloudflare-ai-gateway":
        env["CLOUDFLARE_ACCOUNT_ID"] = "account-123"
        env["CLOUDFLARE_API_TOKEN"] = "cloudflare-secret"
    elif provider == "vercel-ai-gateway":
        env["AI_GATEWAY_API_KEY"] = "vercel-secret"
    elif provider == "together-ai":
        env["TOGETHER_API_KEY"] = "together-secret"
    else:
        env["OPENROUTER_API_KEY"] = "openrouter-secret"
    return env


@pytest.mark.parametrize(
    ("provider", "url", "credential_name"),
    [
        (
            "openrouter",
            "https://openrouter.ai/api/v1/chat/completions",
            "OPENROUTER_API_KEY",
        ),
        (
            "cloudflare-ai-gateway",
            "https://api.cloudflare.com/client/v4/accounts/account-123/ai/v1/chat/completions",
            "CLOUDFLARE_API_TOKEN",
        ),
        (
            "vercel-ai-gateway",
            "https://ai-gateway.vercel.sh/v1/chat/completions",
            "AI_GATEWAY_API_KEY",
        ),
        (
            "together-ai",
            "https://api.together.ai/v1/chat/completions",
            "TOGETHER_API_KEY",
        ),
    ],
)
def test_provider_registry_builds_documented_request(
    provider: str, url: str, credential_name: str
) -> None:
    env = env_for(provider)
    config = flight.parse_run_config(env)
    spec = flight.PROVIDERS[config.provider]
    credential = flight.resolve_credential(spec, env)

    assert spec.endpoint(config) == url
    assert credential == env[credential_name]
    assert spec.headers(config, credential)["Authorization"] == f"Bearer {credential}"
    assert spec.headers(config, credential)["Content-Type"] == "application/json"
    if provider == "cloudflare-ai-gateway":
        assert spec.headers(config, credential)["cf-aig-max-attempts"] == "1"
    assert flight.build_payload(config)["stream"] is False


def test_cloudflare_workers_ai_requires_a_gateway_id() -> None:
    env = env_for("cloudflare-ai-gateway")
    env["AI_MODEL"] = "@cf/meta/llama-3.1-8b-instruct"

    with pytest.raises(flight.ConfigError, match="CLOUDFLARE_AI_GATEWAY_ID"):
        flight.parse_run_config(env)

    env["CLOUDFLARE_AI_GATEWAY_ID"] = "default"
    config = flight.parse_run_config(env)
    headers = flight.PROVIDERS[config.provider].headers(config, "credential")

    assert headers["cf-aig-gateway-id"] == "default"


def test_namespaced_secret_fallback_is_unambiguous() -> None:
    env = {
        "AI_PROVIDER": "openrouter",
        "AI_MODEL": "openai/gpt-4.1-mini",
        "AI_PROMPT": "safe prompt",
        "gateway_OPENROUTER_API_KEY": "namespaced-secret",
    }
    config = flight.parse_run_config(env)
    spec = flight.PROVIDERS[config.provider]

    assert flight.resolve_credential(spec, env) == "namespaced-secret"
    env["other_OPENROUTER_API_KEY"] = "another-secret"
    with pytest.raises(flight.ConfigError, match="multiple Flights secrets"):
        flight.resolve_credential(spec, env)


def test_invalid_provider_and_database_fail_before_network() -> None:
    env = env_for()
    env["AI_PROVIDER"] = "unknown"
    with pytest.raises(flight.ConfigError, match="AI_PROVIDER"):
        flight.parse_run_config(env)

    env = env_for()
    env["RESULTS_DATABASE"] = "not-a-database"
    with pytest.raises(flight.ConfigError, match="RESULTS_DATABASE"):
        flight.parse_run_config(env)


def test_send_chat_has_one_non_streaming_post() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": "request-id",
                "model": "openai/gpt-4.1-mini",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "response text"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
        )

    env = env_for()
    config = flight.parse_run_config(env)
    spec = flight.PROVIDERS[config.provider]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        payload, _duration = flight.send_chat(
            client, spec, config, flight.resolve_credential(spec, env)
        )

    assert len(calls) == 1
    assert str(calls[0].url) == "https://openrouter.ai/api/v1/chat/completions"
    assert json.loads(calls[0].content) == {
        "model": "openai/gpt-4.1-mini",
        "messages": [{"role": "user", "content": "safe prompt"}],
        "max_tokens": 512,
        "stream": False,
    }
    assert flight.parse_completion(payload, config.model).total_tokens == 3


def test_failed_post_is_not_retried() -> None:
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, text="provider-error-body")

    env = env_for()
    config = flight.parse_run_config(env)
    spec = flight.PROVIDERS[config.provider]
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RuntimeError, match="HTTP 429") as error,
    ):
        flight.send_chat(client, spec, config, "credential")

    assert call_count == 1
    assert "provider-error-body" not in str(error.value)


def test_sanitize_result_redacts_and_bounds_output() -> None:
    result = flight.sanitize_result(
        "openrouter-secret safe prompt\x00\n" + "x" * 20,
        ("openrouter-secret", "safe prompt"),
        maximum=18,
    )

    assert "openrouter-secret" not in result
    assert "safe prompt" not in result
    assert "\x00" not in result
    assert len(result) == 18


def test_ensure_results_table_uses_motherduck_database_ddl() -> None:
    statements: list[tuple[str, object]] = []

    class RecordingConnection:
        def execute(self, statement: str, parameters: object = None) -> None:
            statements.append((statement, parameters))

    flight.ensure_results_table(RecordingConnection(), "ai_gateway")

    assert statements[0] == ("CREATE DATABASE IF NOT EXISTS ai_gateway", None)
    assert "CREATE TABLE IF NOT EXISTS ai_gateway.main.ai_chat_results" in statements[2][0]


def test_persistence_uses_the_documented_columns() -> None:
    connection = duckdb.connect()
    try:
        connection.execute("ATTACH ':memory:' AS ai_gateway")
        connection.execute(
            """
            CREATE TABLE ai_gateway.main.ai_chat_results (
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
        record = flight.CompletionRecord(
            run_id="018f1d5a-7f40-7f5e-8d36-423b2fa0eb75",
            provider="openrouter",
            configured_model="openai/gpt-4.1-mini",
            provider_response_id="request-id",
            returned_model="openai/gpt-4.1-mini",
            finish_reason="stop",
            result_text="response text",
            result_characters=13,
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
            request_duration_ms=10,
        )
        flight.persist_result(connection, "ai_gateway", record)
        row = connection.execute(
            "SELECT provider, result_text, total_tokens FROM ai_gateway.main.ai_chat_results"
        ).fetchone()
    finally:
        connection.close()

    assert row == ("openrouter", "response text", 3)

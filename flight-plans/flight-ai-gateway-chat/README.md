---
title: Call OpenAI-Compatible AI Gateways From a MotherDuck Flight
id: flight-ai-gateway-chat
description: >-
  A reusable Flight that sends one OpenAI-compatible chat completion through
  OpenRouter, Cloudflare AI Gateway, Vercel AI Gateway, or Together AI, then
  stores the cleaned result and request metadata in MotherDuck.
type: template
category: integrations
features: [flights]
tags: [python, openrouter, cloudflare, vercel, together-ai]
prompt: >-
  I want to run one OpenAI-compatible chat completion from a MotherDuck Flight
  through OpenRouter, Cloudflare AI Gateway, Vercel AI Gateway, or Together AI.
  Help me adapt the "Call OpenAI-Compatible AI Gateways From a MotherDuck Flight"
  recipe to my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-ai-gateway-chat
published_date: 2026-09-14
---

# Call OpenAI-compatible AI gateways from a MotherDuck Flight

This Flight sends one non-streaming chat-completions request through OpenRouter,
Cloudflare AI Gateway, Vercel AI Gateway, or Together AI. It stores a bounded,
cleaned completion and its request metadata in MotherDuck. Select one provider
per Flight run.

## How it works

`flight.py` uses a provider registry to own the endpoint, credential variable,
and Cloudflare-specific header rule. The request uses only the chat-completions
fields that all four providers support: `model`, `messages`, `max_tokens`, and
`stream: false`.

Before it calls a provider, the Flight validates its configuration and creates
`<RESULTS_DATABASE>.main.ai_chat_results`. It sends exactly one POST request.
It never retries a failed request because a retry can purchase a second
completion. On success, it redacts the configured prompt, system prompt, and
credential from the stored output. It does not store request headers, a raw
provider response, a provider error body, or the prompt as a separate column.

## Questions to answer

- Which provider account should make the request?
- Which model identifier does that provider accept?
- Is the prompt safe to store in Flight configuration? Use a table-driven
  workflow instead for customer data, credentials, or other confidential input.
- Which writable MotherDuck database should hold the result table?
- Does Cloudflare need a particular AI Gateway? Workers AI models beginning
  with `@cf/` require its gateway ID.

## Caveats

- This template runs one text-only chat completion. It does not support tools,
  streaming, structured output, image input, provider routing controls, or
  batching.
- Provider model catalogs, limits, and prices differ. Replace the example model
  with one available to your account before you schedule the Flight.
- `AI_PROMPT` and `AI_SYSTEM_PROMPT` are Flight configuration. Do not put
  secrets or customer data in either value.
- The result cleanup removes the configured values and control characters. It
  cannot determine whether a model output contains personal or confidential
  data. Grant access to the results database accordingly.
- Cloudflare uses its account-level OpenAI-compatible API. A token needs
  **Account > Workers AI > Read**. Third-party models use the default AI
  Gateway unless `CLOUDFLARE_AI_GATEWAY_ID` selects another one. A Workers AI
  model beginning with `@cf/` always needs that value.

## What you'll adjust

Set these non-secret values in the Flight configuration or in your local shell.

| Knob | Required | Default | Purpose |
|---|---:|---|---|
| `AI_PROVIDER` | Yes | | `openrouter`, `cloudflare-ai-gateway`, `vercel-ai-gateway`, or `together-ai`. |
| `AI_MODEL` | Yes | | The provider model identifier. |
| `AI_PROMPT` | Yes | | One non-sensitive user message. |
| `AI_SYSTEM_PROMPT` | No | | An optional system message. |
| `AI_MAX_TOKENS` | No | `512` | Maximum output tokens. Accepts `1` through `16384`. |
| `AI_TIMEOUT_SECONDS` | No | `45` | HTTP request timeout in seconds. |
| `RESULTS_DATABASE` | No | `ai_gateway` | A simple identifier for the database that stores `main.ai_chat_results`. |
| `MAX_RESULT_CHARS` | No | `16000` | Maximum stored completion length. |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare only | | Cloudflare account ID. |
| `CLOUDFLARE_AI_GATEWAY_ID` | Optional for Cloudflare. Required for `@cf/` models. | | The AI Gateway ID sent in `cf-aig-gateway-id`. |

Each provider needs one Flights secret parameter. The source first accepts the
bare parameter name for local runs. It also accepts exactly one namespaced
`<secret_name>_<parameter>` value from a Flight secret.

| Provider | Secret parameter | Chat-completions endpoint |
|---|---|---|
| OpenRouter | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1/chat/completions` |
| Cloudflare AI Gateway | `CLOUDFLARE_API_TOKEN` | `https://api.cloudflare.com/client/v4/accounts/<account-id>/ai/v1/chat/completions` |
| Vercel AI Gateway | `AI_GATEWAY_API_KEY` | `https://ai-gateway.vercel.sh/v1/chat/completions` |
| Together AI | `TOGETHER_API_KEY` | `https://api.together.ai/v1/chat/completions` |

## Run it

For a local smoke test, set a MotherDuck token, select a provider, and export
only that provider's API key. This OpenRouter example uses a model identifier
that you must replace with one available to your account.

```bash
export MOTHERDUCK_TOKEN=your_token_here
export AI_PROVIDER=openrouter
export AI_MODEL=openai/gpt-4.1-mini
export AI_PROMPT='Write one sentence about MotherDuck.'
export OPENROUTER_API_KEY=your_openrouter_key_here
uv run --with-requirements requirements.txt flight.py
```

The command creates `ai_gateway.main.ai_chat_results`, sends one request, and
inserts one row. Inspect the row with:

```sql
SELECT provider, configured_model, returned_model, result_text,
       prompt_tokens, completion_tokens, request_duration_ms
FROM ai_gateway.main.ai_chat_results
ORDER BY completed_at DESC
LIMIT 1;
```

### Deploy as a Flight

Create a MotherDuck **Flights** secret for the provider you selected. The UI
is at [Settings > Secrets](https://app.motherduck.com/settings/secrets). For
OpenRouter, the equivalent write-enabled SQL is:

```sql
CREATE SECRET openrouter_key IN motherduck (
  TYPE flights,
  PARAMS MAP { 'OPENROUTER_API_KEY': 'your_openrouter_key_here' }
);
```

Create the Flight with `MD_CREATE_FLIGHT`. Pass these values:

- `name`: a Flight name, such as `ai_gateway_chat`.
- `source_code`: the contents of [`flight.py`](flight.py).
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt).
- `flight_secret_names`: the secret for the selected provider, such as
  `['openrouter_key']`.
- `config`: `AI_PROVIDER`, `AI_MODEL`, `AI_PROMPT`, and optional non-secret
  values from the table above.
- `max_runtime_sec`: an optional runtime limit. Use `0` for no limit.

A MotherDuck token is attached to the Flight and injected as
`MOTHERDUCK_TOKEN` at run time. Do not put it in the Flight configuration.

Create the Flight without a schedule. Trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)`. `MD_CREATE_FLIGHT` returns the ID, and
`MD_FLIGHTS()` lists existing Flights. Inspect the stored row before adding a
schedule with `MD_UPDATE_FLIGHT`.

## Security

- Put provider credentials in a Flights secret. Do not add credentials to
  source code or Flight configuration.
- The Flight accepts a fixed provider registry. It does not accept a URL,
  headers, or arbitrary request JSON from configuration, so a configuration
  value cannot redirect a provider credential to another host.
- `RESULTS_DATABASE` accepts only a simple SQL identifier. The Flight binds all
  inserted result values as parameters.
- The Flight does not log provider responses or error bodies. Keep the result
  table in a database whose grants match the sensitivity of generated text.

## Learn more

- [OpenRouter API quickstart](https://openrouter.ai/docs/quickstart)
- [Cloudflare AI Gateway REST API](https://developers.cloudflare.com/ai-gateway/usage/rest-api/)
- [Vercel AI Gateway REST API](https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions/rest-api)
- [Together AI OpenAI compatibility](https://docs.together.ai/docs/inference/openai-compatibility)
- Flight mechanics and scheduling: use the MotherDuck MCP `get_flight_guide` tool.
- MotherDuck SQL questions: use the `ask_docs_question` MCP tool.

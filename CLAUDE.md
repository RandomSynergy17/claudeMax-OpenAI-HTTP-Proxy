# CLAUDE.md — Project Guide for AI Assistants

## Project Overview

**claudeMax-OpenAI-HTTP-Proxy** is a dual API proxy that exposes both **OpenAI-compatible** and **Anthropic-compatible** HTTP endpoints, routing all requests through the **Claude Code CLI** (`claude -p`). This enables users with a **Claude Max subscription** to serve Claude models as standard API endpoints on their local network — no Anthropic API key required.

## Architecture

```
                         ┌──────────────────────────┐
 OpenAI SDK clients ────▶│  /openai/v1/*             │
                         │                          │     ┌─────────────────┐
                         │  FastAPI Proxy            │────▶│  Claude Code    │
                         │  (server.py)              │     │  CLI (claude -p)│
                         │  Port 4000                │◀────│  Max Sub Auth   │
 Anthropic SDK clients ─▶│                          │     └─────────────────┘
                         │  /anthropic/v1/*           │
                         └──────────────────────────┘
```

### How It Works

1. **Incoming request**: An HTTP request hits the FastAPI server on either the OpenAI or Anthropic API surface.
2. **Translation**: The proxy extracts system messages, converts the message array into a text prompt, and resolves model names.
3. **CLI invocation**: Spawns `claude -p` as an async subprocess with `--model`, `--system-prompt`, `--output-format json|stream-json`, `--tools ""`, `--no-session-persistence`.
4. **Response mapping**: The CLI output is parsed and formatted into the correct API schema (OpenAI or Anthropic).
5. **Return**: The properly-formatted response is returned to the client.

### Key Design Decisions

- **`--tools ""`**: Claude Code's built-in tools (Bash, Edit, Read, etc.) are disabled. The proxy is for text generation only.
- **`--no-session-persistence`**: Each request is stateless. No sessions saved to disk.
- **Subprocess per request**: Simple, stateless, ~1-2s latency overhead.
- **Tool/function calling via prompt injection**: Since `claude -p` doesn't natively support OpenAI/Anthropic function calling schemas, tools are injected into the system prompt and tool calls are parsed from response text.
- **Dual API surfaces**: OpenAI at `/openai/v1/*` (also `/v1/*` for backwards compat), Anthropic at `/anthropic/v1/*`.

## File Structure

```
server.py               # The proxy server (~980 lines): shared CLI layer + OpenAI routes + Anthropic routes
requirements.txt        # Python dependencies (fastapi, uvicorn)
claude-proxy.service    # systemd user service unit file
install.sh              # Automated install script (venv, systemd, linger)
LICENSE                 # MIT license
README.md               # User-facing documentation
CLAUDE.md               # This file
```

## Key Components in server.py

### Shared Layer
| Component | Purpose |
|---|---|
| `CLAUDE_MODELS` | Canonical Claude model definitions (context window, max output, display name) |
| `call_claude()` | Non-streaming CLI invocation (`--output-format json`) |
| `call_claude_streaming()` | Streaming CLI invocation (`--output-format stream-json --verbose --include-partial-messages`) |

### OpenAI API Layer (`/openai/v1/`)
| Component | Purpose |
|---|---|
| `OPENAI_MODEL_ALIASES` | Maps OpenAI model names to Claude equivalents |
| `openai_extract_system()` | Extracts system-role messages from the OpenAI message array |
| `openai_messages_to_prompt()` | Converts OpenAI messages (user, assistant, tool) to flat text prompt |
| `openai_build_tools_system()` | Generates system prompt appendix for OpenAI function calling |
| `openai_parse_tool_calls()` | Extracts `{"tool_call": ...}` JSON from response text |
| `openai_chat_completions()` | `POST /openai/v1/chat/completions` handler |
| `openai_completions()` | `POST /openai/v1/completions` handler (legacy) |
| `_openai_stream_chat()` | SSE streaming generator (OpenAI `data:` format) |

### Anthropic API Layer (`/anthropic/v1/`)
| Component | Purpose |
|---|---|
| `anthropic_extract_system()` | Extracts top-level `system` field (string or block array) |
| `anthropic_content_to_text()` | Converts Anthropic content blocks to text |
| `anthropic_messages_to_prompt()` | Converts Anthropic messages to flat text prompt |
| `anthropic_build_tools_system()` | Generates system prompt appendix for Anthropic tool use |
| `anthropic_parse_tool_use()` | Extracts `{"tool_use": ...}` JSON from response text |
| `anthropic_messages()` | `POST /anthropic/v1/messages` handler |
| `_anthropic_stream_messages()` | SSE streaming generator (Anthropic `event:` + `data:` format) |
| `anthropic_count_tokens()` | `POST /anthropic/v1/messages/count_tokens` handler |

## Endpoints

### OpenAI (`/openai/v1/` or `/v1/`)
| Endpoint | Method | Status |
|---|---|---|
| `/openai/v1/chat/completions` | POST | Full support |
| `/openai/v1/completions` | POST | Full support |
| `/openai/v1/models` | GET | Supported |
| `/openai/v1/models/{id}` | GET | Supported |
| `/openai/v1/embeddings` | POST | 501 |
| `/openai/v1/images/*` | POST | 501 |
| `/openai/v1/audio/*` | POST | 501 |
| `/openai/v1/fine_tuning/jobs` | POST | 501 |
| `/openai/v1/moderations` | POST | 501 |

### Anthropic (`/anthropic/v1/`)
| Endpoint | Method | Status |
|---|---|---|
| `/anthropic/v1/messages` | POST | Full support |
| `/anthropic/v1/models` | GET | Supported |
| `/anthropic/v1/models/{id}` | GET | Supported |
| `/anthropic/v1/messages/count_tokens` | POST | Approximate |

### Shared
| Endpoint | Description |
|---|---|
| `/health` | Health check |
| `/` | Service info / endpoint directory |

## Model Mapping

| OpenAI Alias | Claude Model |
|---|---|
| `gpt-4`, `gpt-4-turbo`, `gpt-4o`, `o1-mini`, `o3-mini`, `o4-mini` | `claude-sonnet-4-6` |
| `gpt-4o-mini`, `gpt-3.5-turbo` | `claude-haiku-4-5` |
| `o1`, `o1-preview`, `o3` | `claude-opus-4-6` |

The Anthropic API accepts native Claude model names only (no aliases).

## Streaming Formats

**OpenAI streaming** uses bare `data:` lines:
```
data: {"id":"...","object":"chat.completion.chunk","choices":[{"delta":{"content":"Hello"}}]}

data: [DONE]
```

**Anthropic streaming** uses `event:` + `data:` pairs:
```
event: message_start
data: {"type":"message_start","message":{...}}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}

event: message_stop
data: {"type":"message_stop"}
```

## Development

### Running locally
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python server.py --port 4000 --host 127.0.0.1
```

### Testing both APIs
```bash
# OpenAI
curl http://localhost:4000/openai/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4-5","messages":[{"role":"user","content":"Hello"}]}'

# Anthropic
curl http://localhost:4000/anthropic/v1/messages -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4-5","max_tokens":100,"messages":[{"role":"user","content":"Hello"}]}'
```

### Adding new model aliases
Add to `OPENAI_MODEL_ALIASES` for OpenAI aliases, or `CLAUDE_MODELS` for new Claude models.

### Error Formats
- **OpenAI**: `{"error": {"message": "...", "type": "...", "param": null, "code": "..."}}`
- **Anthropic**: `{"type": "error", "error": {"type": "...", "message": "..."}}`

## Limitations

- **Subprocess overhead**: ~1-2s added latency per request
- **Rate limits**: Bound by Claude Max subscription limits
- **Tool calling is prompt-based**: Works well but is not native
- **No image passthrough**: Vision content noted as `[Image provided]` but not sent
- **Token counts**: Input tokens include cache; output tokens from CLI metadata
- **n > 1**: Only single completion per request

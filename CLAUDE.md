# CLAUDE.md — Project Guide for AI Assistants

## Project Overview

**claudeMax-OpenAI-HTTP-Proxy** is a drop-in OpenAI-compatible HTTP API proxy that routes all requests through the **Claude Code CLI** (`claude -p`), enabling users with a **Claude Max subscription** to expose Claude models as an OpenAI-compatible API on their local network — no Anthropic API key required.

## Architecture

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  OpenAI-compatible  │────▶│  FastAPI Proxy        │────▶│  Claude Code    │
│  Client (any SDK,   │     │  (server.py)          │     │  CLI (claude -p)│
│  curl, app, etc.)   │◀────│  Port 4000            │◀────│  Max Sub Auth   │
└─────────────────────┘     └──────────────────────┘     └─────────────────┘
```

### How It Works

1. **Incoming request**: An OpenAI-format HTTP request hits the FastAPI server (e.g., `POST /v1/chat/completions`).
2. **Translation**: The proxy extracts system messages, converts the OpenAI message array into a text prompt, and resolves model aliases (e.g., `gpt-4o` → `claude-sonnet-4-6`).
3. **CLI invocation**: The proxy spawns `claude -p` as an async subprocess with appropriate flags (`--model`, `--system-prompt`, `--output-format json|stream-json`, `--tools ""`, `--no-session-persistence`).
4. **Response mapping**: The CLI's JSON/streaming output is parsed and re-formatted into the OpenAI response schema (including `usage`, `choices`, `finish_reason`, etc.).
5. **Return**: The OpenAI-formatted response is returned to the client.

### Key Design Decisions

- **`--tools ""`**: Claude Code's built-in tools (Bash, Edit, Read, etc.) are disabled. The proxy is for text generation only — it does not give Claude access to the host filesystem.
- **`--no-session-persistence`**: Each request is stateless. No sessions are saved to disk.
- **Subprocess per request**: Each API call spawns a fresh `claude -p` process. This is simple and stateless but adds ~1-2s latency overhead vs. a direct API.
- **Tool/function calling via prompt injection**: Since `claude -p` doesn't natively support OpenAI-style function calling, tools are injected into the system prompt and tool calls are parsed from the response text.

## File Structure

```
server.py               # The entire proxy server (single file, ~750 lines)
requirements.txt        # Python dependencies (fastapi, uvicorn)
claude-proxy.service    # systemd user service unit file
install.sh              # Automated install script (venv, systemd, linger)
LICENSE                 # MIT license
README.md               # User-facing documentation
CLAUDE.md               # This file — project guide for AI assistants
```

## Key Components in server.py

| Component | Purpose |
|---|---|
| `CLAUDE_MODELS` | Canonical Claude model definitions with context window / max output metadata |
| `MODEL_ALIASES` | Maps OpenAI model names (`gpt-4o`, `o1`, etc.) to Claude equivalents |
| `extract_system_prompt()` | Pulls system-role messages out of the OpenAI message array |
| `messages_to_prompt()` | Converts the remaining messages (user, assistant, tool) into a flat text prompt |
| `build_tools_system_section()` | Generates a system prompt appendix describing available functions |
| `parse_tool_calls_from_response()` | Extracts `{"tool_call": ...}` JSON lines from Claude's text response |
| `call_claude()` | Non-streaming CLI invocation (`--output-format json`) |
| `call_claude_streaming()` | Streaming CLI invocation (`--output-format stream-json --verbose --include-partial-messages`) |
| `chat_completions()` | `POST /v1/chat/completions` handler |
| `completions()` | `POST /v1/completions` handler (legacy) |
| Unsupported endpoint handlers | Return proper OpenAI-format 501 errors for embeddings, images, audio, etc. |

## Supported OpenAI Endpoints

| Endpoint | Method | Status |
|---|---|---|
| `/v1/chat/completions` | POST | Full support (streaming, tools, system messages, multi-turn) |
| `/v1/completions` | POST | Full support (streaming + non-streaming) |
| `/v1/models` | GET | Lists all Claude models + OpenAI aliases |
| `/v1/models/{id}` | GET | Retrieve individual model |
| `/v1/embeddings` | POST | 501 — Claude has no embedding model |
| `/v1/images/*` | POST | 501 — Claude has no image generation |
| `/v1/audio/*` | POST | 501 — Claude has no TTS/STT |
| `/v1/fine_tuning/jobs` | POST | 501 — Not available |
| `/v1/moderations` | POST | 501 — Not available |
| `/health` | GET | Health check |

All endpoints also work without the `/v1` prefix.

## Model Mapping

| OpenAI Model | Claude Model |
|---|---|
| `gpt-4`, `gpt-4-turbo`, `gpt-4o`, `o1-mini`, `o3-mini`, `o4-mini` | `claude-sonnet-4-6` |
| `gpt-4o-mini`, `gpt-3.5-turbo` | `claude-haiku-4-5` |
| `o1`, `o1-preview`, `o3` | `claude-opus-4-6` |

Native Claude model names (`claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5`) are always accepted directly.

## Development Notes

### Running locally for development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python server.py --port 4000 --host 127.0.0.1
```

### Testing

```bash
# Health
curl http://localhost:4000/health

# Chat completion
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"Hello"}]}'

# Streaming
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4-5","stream":true,"messages":[{"role":"user","content":"Count to 5"}]}'

# Model listing
curl http://localhost:4000/v1/models
```

### Deployment

The proxy runs as a **systemd user service** (`claude-proxy.service`). User linger is enabled so the service starts at boot before login. See `install.sh` for automated setup.

### Adding new model aliases

Add entries to the `MODEL_ALIASES` dict in `server.py`. No other changes needed.

### Limitations to be aware of

- **Subprocess overhead**: ~1-2s added latency per request from spawning `claude -p`
- **Rate limits**: Bound by the Claude Max subscription rate limits
- **No concurrent request pooling**: Heavy parallel usage may hit subscription limits
- **Tool calling is prompt-based**: Works well but is not as robust as native function calling
- **No image input passthrough**: Vision/image content in messages is noted as `[Image provided]` but not actually sent
- **Token counts**: Input tokens include cache tokens from the CLI; output tokens are accurate from CLI metadata

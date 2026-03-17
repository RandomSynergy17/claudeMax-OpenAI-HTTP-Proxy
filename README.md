# claudeMax-OpenAI-HTTP-Proxy

A drop-in **dual API proxy** that exposes both **OpenAI-compatible** and **Anthropic-compatible** endpoints, routing all requests through the **Claude Code CLI** using your **Claude Max subscription**.

Any tool, library, or application that speaks either the OpenAI API or the Anthropic API can now use Claude models — **no API key required**.

## How It Works

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

1. Your app sends a standard API request to the proxy (OpenAI or Anthropic format)
2. The proxy translates it and calls `claude -p` (pipe mode) with your Max subscription auth
3. Claude's response is formatted back into the correct API response schema
4. Your app receives a response identical to what the real API would return

## Features

### Both APIs
- **Real streaming** via Claude Code CLI's native `stream-json` output
- **All three Claude model tiers**: Opus 4.6, Sonnet 4.6, Haiku 4.5
- **System messages** passed natively to Claude
- **Multi-turn conversations**
- **Tool/function calling** support
- **CORS enabled** for browser and LAN usage
- **Systemd service** with auto-start at boot
- **One-command install** script

### OpenAI API (`/openai/v1/`)
- Full `POST /v1/chat/completions` (streaming + non-streaming)
- Full `POST /v1/completions` (legacy text completions)
- `GET /v1/models` — lists Claude models + OpenAI aliases
- `GET /v1/models/{id}` — individual model info
- OpenAI model aliases (`gpt-4o`, `o1`, `gpt-3.5-turbo`, etc.)
- Proper OpenAI error format (`error.message`, `error.type`, `error.code`)
- 501 responses for unsupported endpoints (embeddings, images, audio, fine-tuning)
- Backwards compatible at `/v1/*` (no prefix needed)

### Anthropic API (`/anthropic/v1/`)
- Full `POST /v1/messages` (streaming + non-streaming)
- Native Anthropic content blocks (`text`, `tool_use`, `tool_result`)
- `system` as top-level field (string or block array)
- `tool_choice` with `auto`, `any`, `none`, `tool` types
- `stop_sequences` support
- `GET /v1/models` — Anthropic-format model listing
- `GET /v1/models/{id}` — individual model
- `POST /v1/messages/count_tokens` — token count estimation
- Proper Anthropic SSE streaming format (`event:` + `data:` lines)
- Anthropic error format (`type: "error"`, `error.type`, `error.message`)
- Cache token tracking in usage

## Prerequisites

- **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)** installed and authenticated (`claude auth login`)
- An active **Claude Max subscription**
- **Python 3.10+**
- **Linux** with systemd (for auto-start; the proxy itself runs anywhere Python runs)

## Quick Start

### Option 1: Automated Install (Recommended)

```bash
git clone https://github.com/RandomSynergy17/claudeMax-OpenAI-HTTP-Proxy.git
cd claudeMax-OpenAI-HTTP-Proxy
chmod +x install.sh
./install.sh
```

### Option 2: Manual Setup

```bash
git clone https://github.com/RandomSynergy17/claudeMax-OpenAI-HTTP-Proxy.git
cd claudeMax-OpenAI-HTTP-Proxy
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python server.py --port 4000 --host 0.0.0.0
```

## Usage Examples

### OpenAI SDK (Python)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.x.x:4000/openai/v1",
    api_key="not-needed"
)

response = client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"}
    ]
)
print(response.choices[0].message.content)
```

### Anthropic SDK (Python)

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://192.168.x.x:4000/anthropic",
    api_key="not-needed"
)

message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system="You are a helpful assistant.",
    messages=[
        {"role": "user", "content": "Hello!"}
    ]
)
print(message.content[0].text)
```

### OpenAI SDK Streaming

```python
stream = client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "Tell me a story"}],
    stream=True
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### Anthropic SDK Streaming

```python
with client.messages.stream(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Tell me a story"}]
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

### OpenAI Tool/Function Calling

```python
response = client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "What's the weather in Paris?"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"]
            }
        }
    }]
)
```

### Anthropic Tool Use

```python
message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    tools=[{
        "name": "get_weather",
        "description": "Get current weather for a location",
        "input_schema": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"]
        }
    }],
    messages=[{"role": "user", "content": "What's the weather in Paris?"}]
)
```

### curl — OpenAI Format

```bash
curl http://localhost:4000/openai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"Hello!"}]}'
```

### curl — Anthropic Format

```bash
curl http://localhost:4000/anthropic/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: not-needed" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":1024,"messages":[{"role":"user","content":"Hello!"}]}'
```

### JavaScript / TypeScript

```typescript
// OpenAI
import OpenAI from "openai";
const openai = new OpenAI({ baseURL: "http://host:4000/openai/v1", apiKey: "na" });
const resp = await openai.chat.completions.create({ model: "claude-sonnet-4-6", messages: [{ role: "user", content: "Hi" }] });

// Anthropic
import Anthropic from "@anthropic-ai/sdk";
const anthropic = new Anthropic({ baseURL: "http://host:4000/anthropic", apiKey: "na" });
const msg = await anthropic.messages.create({ model: "claude-sonnet-4-6", max_tokens: 1024, messages: [{ role: "user", content: "Hi" }] });
```

### Use with Other Tools

| Tool | Configuration |
|---|---|
| **LangChain (OpenAI)** | `ChatOpenAI(base_url="http://host:4000/openai/v1", api_key="na")` |
| **LangChain (Anthropic)** | `ChatAnthropic(base_url="http://host:4000/anthropic", api_key="na")` |
| **LlamaIndex** | `OpenAI(api_base="http://host:4000/openai/v1", api_key="na")` |
| **Continue (VS Code)** | Set API base to `http://host:4000/openai/v1` |
| **Open WebUI** | Add as OpenAI provider with `http://host:4000/openai/v1` |
| **Cursor** | Set OpenAI base URL to `http://host:4000/openai/v1` |

## API Endpoints

### OpenAI Endpoints (`/openai/v1/` or `/v1/`)

| Endpoint | Method | Status |
|---|---|---|
| `/openai/v1/chat/completions` | POST | Full support (streaming, tools, system messages) |
| `/openai/v1/completions` | POST | Full support (streaming + non-streaming) |
| `/openai/v1/models` | GET | Lists Claude models + OpenAI aliases |
| `/openai/v1/models/{id}` | GET | Retrieve individual model |
| `/openai/v1/embeddings` | POST | 501 — Not available via Claude |
| `/openai/v1/images/*` | POST | 501 — Not available via Claude |
| `/openai/v1/audio/*` | POST | 501 — Not available via Claude |
| `/openai/v1/fine_tuning/jobs` | POST | 501 — Not available via Claude |
| `/openai/v1/moderations` | POST | 501 — Not available via Claude |

### Anthropic Endpoints (`/anthropic/v1/`)

| Endpoint | Method | Status |
|---|---|---|
| `/anthropic/v1/messages` | POST | Full support (streaming, tools, system, stop_sequences) |
| `/anthropic/v1/models` | GET | Lists all Claude models |
| `/anthropic/v1/models/{id}` | GET | Retrieve individual model |
| `/anthropic/v1/messages/count_tokens` | POST | Token count estimation |

### Shared

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check with endpoint info |
| `/` | GET | Service info and endpoint directory |

## Model Mapping

You can use Claude model names directly on both APIs. The OpenAI API also accepts these aliases:

| OpenAI Alias | Routes To | Tier |
|---|---|---|
| `gpt-4`, `gpt-4-turbo`, `gpt-4o` | `claude-sonnet-4-6` | Balanced |
| `o1-mini`, `o3-mini`, `o4-mini` | `claude-sonnet-4-6` | Balanced |
| `gpt-4o-mini`, `gpt-3.5-turbo` | `claude-haiku-4-5` | Fastest |
| `o1`, `o1-preview`, `o3` | `claude-opus-4-6` | Highest capability |

All models: **200K context window**, **32K max output tokens**.

## Configuration

| Flag | Default | Description |
|---|---|---|
| `--port` | `4000` | Port to listen on |
| `--host` | `0.0.0.0` | Host to bind to (`0.0.0.0` = all interfaces) |

## Install as System Service (Linux)

### Automated

```bash
./install.sh
```

### Manual

```bash
mkdir -p ~/.config/systemd/user
cp claude-proxy.service ~/.config/systemd/user/
# Edit paths in the service file if needed
systemctl --user daemon-reload
systemctl --user enable claude-proxy.service
systemctl --user start claude-proxy.service
loginctl enable-linger $USER
```

### Service Management

```bash
systemctl --user status  claude-proxy    # Check status
systemctl --user restart claude-proxy    # Restart
systemctl --user stop    claude-proxy    # Stop
journalctl --user -u claude-proxy -f     # Tail logs
```

## Updating

```bash
cd claudeMax-OpenAI-HTTP-Proxy
git pull
cp server.py ~/claude-proxy/server.py
systemctl --user restart claude-proxy
```

## Troubleshooting

| Problem | Solution |
|---|---|
| **502 errors** | Check `claude auth login` and subscription status. Test: `echo "hi" \| claude -p` |
| **Connection refused from LAN** | Ensure `--host 0.0.0.0`. Check firewall: `sudo ufw allow 4000/tcp` |
| **Service doesn't start at boot** | `loginctl enable-linger $USER` |
| **Port in use** | `fuser -k 4000/tcp && systemctl --user restart claude-proxy` |
| **High latency** | Normal ~1-2s overhead per subprocess. Use Haiku for faster responses. |
| **Rate limiting** | Bound by Claude Max subscription limits. Reduce concurrent requests. |

## Limitations

| Limitation | Detail |
|---|---|
| **Latency** | ~1-2s overhead per request (subprocess spawn) |
| **Rate limits** | Bound by Claude Max subscription limits |
| **No embeddings** | Claude does not provide embedding models |
| **No image generation** | Claude does not generate images |
| **No audio** | Claude does not provide TTS/STT |
| **Image input** | Image URLs/base64 in messages noted but not passed through |
| **Tool calling** | Implemented via prompt injection — works well but not native |
| **n > 1** | Only `n=1` supported (single completion per request) |

## Security

- **API key**: The proxy accepts any key value — auth is via your Claude Max subscription. If exposing on a network, consider a reverse proxy with auth.
- **No filesystem access**: Claude Code tools are disabled (`--tools ""`). Claude cannot access your filesystem through this proxy.
- **CORS**: Set to `allow_origins=["*"]` for LAN convenience. Restrict if needed.

## License

[MIT](LICENSE)

## Contributing

Contributions welcome. Open an issue or PR on [GitHub](https://github.com/RandomSynergy17/claudeMax-OpenAI-HTTP-Proxy).

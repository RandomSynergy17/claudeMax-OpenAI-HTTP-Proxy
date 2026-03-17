# claudeMax-OpenAI-HTTP-Proxy

A drop-in **OpenAI-compatible API proxy** that routes requests through the **Claude Code CLI**, allowing you to use your **Claude Max subscription** as a standard OpenAI API endpoint on your local network.

Any tool, library, or application that speaks the OpenAI API can now use Claude models instead — **no Anthropic API key required**.

## How It Works

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  OpenAI-compatible  │────▶│  FastAPI Proxy        │────▶│  Claude Code    │
│  Client (any SDK,   │     │  (server.py)          │     │  CLI (claude -p)│
│  curl, app, etc.)   │◀────│  Port 4000            │◀────│  Max Sub Auth   │
└─────────────────────┘     └──────────────────────┘     └─────────────────┘
```

1. Your app sends a standard OpenAI API request to the proxy
2. The proxy translates it and calls `claude -p` (pipe mode) with your Max subscription auth
3. Claude's response is formatted back into the OpenAI response schema
4. Your app receives a response identical to what the OpenAI API would return

## Features

- **Full OpenAI API compliance** for all endpoints Claude supports
- **Real streaming** via Claude Code CLI's native `stream-json` output
- **All three Claude model tiers**: Opus 4.6, Sonnet 4.6, Haiku 4.5
- **OpenAI model aliases**: Request `gpt-4o`, `gpt-3.5-turbo`, `o1`, `o3`, etc. — automatically mapped to the right Claude model
- **Tool/function calling** support (OpenAI function calling format)
- **System messages** passed natively to Claude via `--system-prompt`
- **Multi-turn conversation** support
- **Legacy `/v1/completions`** endpoint (text completions)
- **CORS enabled** for browser and cross-origin LAN usage
- **Proper OpenAI error format** (`error.message`, `error.type`, `error.code`) on all responses
- **Unsupported endpoints** return correct 501 errors (embeddings, images, audio, fine-tuning)
- **Systemd service** with auto-start at boot
- **One-command install** script

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

This will:
- Create a Python virtual environment at `~/claude-proxy-venv`
- Install dependencies
- Copy `server.py` to `~/claude-proxy`
- Create and enable a systemd user service
- Enable linger for boot persistence
- Start the proxy immediately

#### Install Options

```bash
./install.sh --port 8080                         # Custom port
./install.sh --host 127.0.0.1                    # Localhost only
./install.sh --install-dir /opt/claude-proxy     # Custom install path
./install.sh --venv-dir /opt/claude-proxy-venv   # Custom venv path
```

### Option 2: Manual Setup

```bash
git clone https://github.com/RandomSynergy17/claudeMax-OpenAI-HTTP-Proxy.git
cd claudeMax-OpenAI-HTTP-Proxy

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run the proxy
python server.py --port 4000 --host 0.0.0.0
```

## Usage Examples

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.x.x:4000/v1",
    api_key="not-needed"  # any value works, auth is via Claude Max subscription
)

response = client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain quantum computing in simple terms."}
    ]
)
print(response.choices[0].message.content)
```

### Streaming

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

### Tool / Function Calling

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
                "properties": {
                    "location": {"type": "string", "description": "City name"}
                },
                "required": ["location"]
            }
        }
    }],
    tool_choice="auto"
)

# Check if the model wants to call a function
message = response.choices[0].message
if message.tool_calls:
    for tool_call in message.tool_calls:
        print(f"Function: {tool_call.function.name}")
        print(f"Arguments: {tool_call.function.arguments}")
```

### Multi-Turn Conversation

```python
messages = [
    {"role": "system", "content": "You are a math tutor."},
    {"role": "user", "content": "What is a derivative?"},
]

# First turn
response = client.chat.completions.create(model="claude-sonnet-4-6", messages=messages)
assistant_msg = response.choices[0].message.content
messages.append({"role": "assistant", "content": assistant_msg})

# Follow-up
messages.append({"role": "user", "content": "Can you give me an example?"})
response = client.chat.completions.create(model="claude-sonnet-4-6", messages=messages)
print(response.choices[0].message.content)
```

### curl

```bash
# Chat completion
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# Streaming
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-haiku-4-5",
    "stream": true,
    "messages": [{"role": "user", "content": "Count to 5"}]
  }'

# Legacy completion
curl http://localhost:4000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "prompt": "The capital of France is"
  }'

# List models
curl http://localhost:4000/v1/models
```

### JavaScript / TypeScript

```typescript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://192.168.x.x:4000/v1",
  apiKey: "not-needed",
});

const response = await client.chat.completions.create({
  model: "claude-sonnet-4-6",
  messages: [{ role: "user", content: "Hello!" }],
});
console.log(response.choices[0].message.content);
```

### Use with Other Tools

Any tool that accepts an OpenAI base URL can use this proxy:

| Tool | Configuration |
|---|---|
| **LangChain** | `ChatOpenAI(base_url="http://host:4000/v1", api_key="na")` |
| **LlamaIndex** | `OpenAI(api_base="http://host:4000/v1", api_key="na")` |
| **Continue (VS Code)** | Set API base in settings to `http://host:4000/v1` |
| **Open WebUI** | Add as OpenAI-compatible provider with base URL `http://host:4000/v1` |
| **Anything OpenAI-compatible** | Point `OPENAI_API_BASE` / `base_url` to `http://host:4000/v1` |

## API Endpoints

| Endpoint | Method | Status | Description |
|---|---|---|---|
| `/v1/chat/completions` | POST | Supported | Chat completions (streaming + non-streaming, tools, system messages) |
| `/v1/completions` | POST | Supported | Legacy text completions (streaming + non-streaming) |
| `/v1/models` | GET | Supported | List all available models and aliases |
| `/v1/models/{model_id}` | GET | Supported | Retrieve a specific model's details |
| `/v1/embeddings` | POST | 501 | Not available — Claude has no embedding model |
| `/v1/images/generations` | POST | 501 | Not available — Claude has no image generation |
| `/v1/images/edits` | POST | 501 | Not available — Claude has no image editing |
| `/v1/audio/transcriptions` | POST | 501 | Not available — Claude has no speech-to-text |
| `/v1/audio/translations` | POST | 501 | Not available — Claude has no audio translation |
| `/v1/audio/speech` | POST | 501 | Not available — Claude has no text-to-speech |
| `/v1/fine_tuning/jobs` | POST | 501 | Not available — Claude has no fine-tuning |
| `/v1/moderations` | POST | 501 | Not available — Claude has no moderation model |
| `/health` | GET | Supported | Health check / status |

All endpoints also work without the `/v1` prefix (e.g., `/chat/completions`).

Unsupported endpoints return proper OpenAI-format error responses:
```json
{
  "error": {
    "message": "Embeddings: This endpoint is not supported. Claude does not provide this capability.",
    "type": "invalid_request_error",
    "param": null,
    "code": "unsupported_endpoint"
  }
}
```

## Model Mapping

You can use either Claude's native model names or familiar OpenAI model names:

| Request Model | Routes To | Tier |
|---|---|---|
| `claude-opus-4-6` | Claude Opus 4.6 | Highest capability |
| `o1`, `o1-preview`, `o3` | Claude Opus 4.6 | Highest capability |
| `claude-sonnet-4-6` | Claude Sonnet 4.6 | Balanced |
| `gpt-4`, `gpt-4-turbo`, `gpt-4o` | Claude Sonnet 4.6 | Balanced |
| `o1-mini`, `o3-mini`, `o4-mini` | Claude Sonnet 4.6 | Balanced |
| `claude-haiku-4-5` | Claude Haiku 4.5 | Fastest |
| `gpt-4o-mini`, `gpt-3.5-turbo` | Claude Haiku 4.5 | Fastest |

All models have a **200K context window** and **32K max output tokens**.

## Configuration

### Command-Line Flags

| Flag | Default | Description |
|---|---|---|
| `--port` | `4000` | Port to listen on |
| `--host` | `0.0.0.0` | Host to bind to (`0.0.0.0` = all interfaces, `127.0.0.1` = localhost only) |

### Environment Variables (for install.sh)

| Variable | Default | Description |
|---|---|---|
| `PORT` | `4000` | Port to listen on |
| `HOST` | `0.0.0.0` | Host to bind to |
| `INSTALL_DIR` | `~/claude-proxy` | Server installation directory |
| `VENV_DIR` | `~/claude-proxy-venv` | Python venv directory |

## Install as System Service (Linux)

### Automated (via install.sh)

The install script handles everything:

```bash
./install.sh
```

### Manual

```bash
# Copy the service file
mkdir -p ~/.config/systemd/user
cp claude-proxy.service ~/.config/systemd/user/

# Edit paths in the service file if your install locations differ
# Then enable and start
systemctl --user daemon-reload
systemctl --user enable claude-proxy.service
systemctl --user start claude-proxy.service

# Enable linger so it starts at boot (even before you log in)
loginctl enable-linger $USER
```

### Service Management

```bash
systemctl --user status  claude-proxy    # Check status
systemctl --user restart claude-proxy    # Restart after updates
systemctl --user stop    claude-proxy    # Stop the proxy
systemctl --user start   claude-proxy    # Start the proxy
journalctl --user -u claude-proxy -f     # Tail logs in real time
journalctl --user -u claude-proxy -n 50  # Last 50 log lines
```

## Updating

```bash
cd claudeMax-OpenAI-HTTP-Proxy
git pull
cp server.py ~/claude-proxy/server.py
systemctl --user restart claude-proxy
```

Or re-run the installer:
```bash
./install.sh
```

## Troubleshooting

### Proxy returns 502 errors

The Claude Code CLI is failing. Check:
1. Is `claude` authenticated? Run `claude auth login`
2. Is your Max subscription active?
3. Test the CLI directly: `echo "hello" | claude -p`

### Connection refused from other machines

1. Make sure the proxy is bound to `0.0.0.0` (not `127.0.0.1`)
2. Check your firewall: `sudo ufw allow 4000/tcp`
3. Verify the service is running: `systemctl --user status claude-proxy`

### Service doesn't start at boot

1. Ensure linger is enabled: `loginctl enable-linger $USER`
2. Check: `loginctl show-user $USER | grep Linger` — should show `Linger=yes`

### High latency

Each request spawns a `claude -p` subprocess (~1-2s overhead). This is by design for simplicity and statelessness. For lower latency, use the Anthropic API directly with an API key.

### Rate limiting

The proxy is subject to your Claude Max subscription rate limits. If you're hitting limits, reduce concurrent requests or space them out.

### Port already in use

```bash
# Find what's using the port
fuser 4000/tcp
# Kill it
fuser -k 4000/tcp
# Restart the service
systemctl --user restart claude-proxy
```

## Limitations

| Limitation | Detail |
|---|---|
| **Latency** | ~1-2s overhead per request (subprocess spawn) |
| **Rate limits** | Bound by Claude Max subscription limits |
| **No embeddings** | Claude does not provide embedding models |
| **No image generation** | Claude does not generate images |
| **No audio** | Claude does not provide TTS/STT |
| **No fine-tuning** | Claude does not support fine-tuning |
| **Image input** | Image URLs in messages are noted but not passed through |
| **Tool calling** | Implemented via prompt injection — works well but not as robust as native |
| **n > 1** | Only `n=1` is supported (single completion per request) |

## Security Considerations

- The proxy accepts **any API key** (authentication is handled by your Claude Max subscription). If exposing on a network, consider placing it behind a reverse proxy with authentication.
- The proxy disables all Claude Code tools (`--tools ""`) — Claude cannot access your filesystem through this proxy.
- CORS is set to `allow_origins=["*"]` for LAN convenience. Restrict this if needed.

## License

[MIT](LICENSE)

## Contributing

Contributions are welcome! Please open an issue or pull request on [GitHub](https://github.com/RandomSynergy17/claudeMax-OpenAI-HTTP-Proxy).

# claudeMax-OpenAI-HTTP-Proxy

A drop-in OpenAI-compatible API proxy that routes requests through the **Claude Code CLI**, allowing you to use your **Claude Max subscription** as an OpenAI API endpoint on your local network.

Any tool, library, or application that supports the OpenAI API can now use Claude models instead — no Anthropic API key required.

## Features

- **Full OpenAI API compliance** for all endpoints Claude supports
- **Real streaming** via Claude Code CLI's `stream-json` output
- **All three Claude model tiers**: Opus 4.6, Sonnet 4.6, Haiku 4.5
- **OpenAI model aliases**: Use `gpt-4o`, `gpt-3.5-turbo`, `o1`, etc. — automatically mapped to Claude models
- **Tool/function calling** support
- **System messages** passed natively to Claude
- **Multi-turn conversations**
- **Legacy `/v1/completions`** endpoint
- **CORS enabled** for LAN/browser usage
- **Proper OpenAI error format** on all error responses
- **Unsupported endpoints** return correct 501 responses (embeddings, images, audio, fine-tuning)
- **Systemd service** for auto-start at boot

## Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- An active **Claude Max subscription**
- Python 3.10+

## Quick Start

```bash
# Clone the repo
git clone https://github.com/RandomSynergy17/claudeMax-OpenAI-HTTP-Proxy.git
cd claudeMax-OpenAI-HTTP-Proxy

# Create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run the proxy
python server.py --port 4000 --host 0.0.0.0
```

The proxy is now available at `http://<your-ip>:4000/v1`.

## Usage

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.x.x:4000/v1",
    api_key="not-needed"  # any value works
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

### Streaming

```python
stream = client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "Tell me a story"}],
    stream=True
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### curl

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Tool/Function Calling

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
                    "location": {"type": "string"}
                },
                "required": ["location"]
            }
        }
    }]
)
```

## Endpoints

| Endpoint | Method | Status |
|---|---|---|
| `/v1/chat/completions` | POST | Supported (streaming + non-streaming) |
| `/v1/completions` | POST | Supported (streaming + non-streaming) |
| `/v1/models` | GET | Supported |
| `/v1/models/{model}` | GET | Supported |
| `/v1/embeddings` | POST | 501 — Not available via Claude |
| `/v1/images/generations` | POST | 501 — Not available via Claude |
| `/v1/images/edits` | POST | 501 — Not available via Claude |
| `/v1/audio/transcriptions` | POST | 501 — Not available via Claude |
| `/v1/audio/translations` | POST | 501 — Not available via Claude |
| `/v1/audio/speech` | POST | 501 — Not available via Claude |
| `/v1/fine_tuning/jobs` | POST | 501 — Not available via Claude |
| `/v1/moderations` | POST | 501 — Not available via Claude |
| `/health` | GET | Health check |

## Model Mapping

| Request as | Routes to |
|---|---|
| `claude-opus-4-6`, `o1`, `o3` | Claude Opus 4.6 |
| `claude-sonnet-4-6`, `gpt-4`, `gpt-4o`, `gpt-4-turbo`, `o1-mini`, `o3-mini`, `o4-mini` | Claude Sonnet 4.6 |
| `claude-haiku-4-5`, `gpt-4o-mini`, `gpt-3.5-turbo` | Claude Haiku 4.5 |

## Install as System Service (Linux)

Run the proxy automatically at boot:

```bash
# Copy the service file
cp claude-proxy.service ~/.config/systemd/user/

# Edit paths in the service file to match your setup
# Then enable and start
systemctl --user daemon-reload
systemctl --user enable claude-proxy.service
systemctl --user start claude-proxy.service

# Enable linger so it starts at boot (before login)
loginctl enable-linger $USER

# Check status
systemctl --user status claude-proxy
```

### Service Management

```bash
systemctl --user status claude-proxy     # Check status
systemctl --user restart claude-proxy    # Restart
systemctl --user stop claude-proxy       # Stop
journalctl --user -u claude-proxy -f     # Tail logs
```

## Configuration

| Flag | Default | Description |
|---|---|---|
| `--port` | `4000` | Port to listen on |
| `--host` | `0.0.0.0` | Host to bind to (`0.0.0.0` = all interfaces) |

## Limitations

- **Rate limits**: Subject to your Claude Max subscription limits
- **Latency**: Each request spawns a `claude -p` subprocess — slightly higher latency than a direct API
- **No embeddings**: Claude does not provide embedding models
- **No image generation**: Claude does not generate images
- **No audio**: Claude does not provide TTS/STT
- **Token counts**: Input token counts are accurate from the CLI; output counts are from the CLI response metadata

## License

MIT

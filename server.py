"""
OpenAI-compatible API proxy that routes requests through Claude Code CLI (Max subscription).

Fully compliant with the OpenAI API specification for all endpoints that Claude supports.
Designed to be a drop-in replacement on a local network.

Usage:
    source ~/claude-proxy-venv/bin/activate
    python server.py [--port 4000] [--host 0.0.0.0]

Endpoints:
    POST /v1/chat/completions    - Chat completions (streaming + non-streaming)
    POST /v1/completions         - Legacy text completions
    GET  /v1/models              - List available models
    GET  /v1/models/{model}      - Retrieve a specific model
    POST /v1/embeddings          - Returns 501 (not supported by Claude)
    POST /v1/images/generations  - Returns 501 (not supported by Claude)
    POST /v1/audio/*             - Returns 501 (not supported by Claude)
    GET  /health                 - Health check
"""

import asyncio
import json
import logging
import time
import uuid
import argparse
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("claude-proxy")

app = FastAPI(title="Claude Code OpenAI Proxy", version="2.0.0")

# Allow CORS from any origin for LAN usage
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Model definitions ───────────────────────────────────────────────────────

CLAUDE_MODELS = {
    "claude-opus-4-6": {
        "id": "claude-opus-4-6",
        "object": "model",
        "created": 1700000000,
        "owned_by": "anthropic",
        "context_window": 200000,
        "max_output_tokens": 32000,
    },
    "claude-sonnet-4-6": {
        "id": "claude-sonnet-4-6",
        "object": "model",
        "created": 1700000000,
        "owned_by": "anthropic",
        "context_window": 200000,
        "max_output_tokens": 32000,
    },
    "claude-haiku-4-5": {
        "id": "claude-haiku-4-5",
        "object": "model",
        "created": 1700000000,
        "owned_by": "anthropic",
        "context_window": 200000,
        "max_output_tokens": 32000,
    },
}

# Aliases so OpenAI-native clients work without config changes
MODEL_ALIASES = {
    "gpt-4": "claude-sonnet-4-6",
    "gpt-4-turbo": "claude-sonnet-4-6",
    "gpt-4-turbo-preview": "claude-sonnet-4-6",
    "gpt-4o": "claude-sonnet-4-6",
    "gpt-4o-2024-05-13": "claude-sonnet-4-6",
    "gpt-4o-mini": "claude-haiku-4-5",
    "gpt-4o-mini-2024-07-18": "claude-haiku-4-5",
    "gpt-3.5-turbo": "claude-haiku-4-5",
    "gpt-3.5-turbo-0125": "claude-haiku-4-5",
    "o1": "claude-opus-4-6",
    "o1-preview": "claude-opus-4-6",
    "o1-mini": "claude-sonnet-4-6",
    "o3": "claude-opus-4-6",
    "o3-mini": "claude-sonnet-4-6",
    "o4-mini": "claude-sonnet-4-6",
}

DEFAULT_MODEL = "claude-sonnet-4-6"

STOP_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
}


def resolve_model(requested: str) -> str:
    """Resolve an OpenAI or Claude model name to a Claude CLI model name."""
    if requested in CLAUDE_MODELS:
        return requested
    return MODEL_ALIASES.get(requested, DEFAULT_MODEL)


# ─── OpenAI error response helper ────────────────────────────────────────────

def openai_error(status: int, message: str, error_type: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "param": None,
                "code": code,
            }
        },
    )


# ─── Message conversion ──────────────────────────────────────────────────────

def extract_system_prompt(messages: list[dict]) -> tuple[Optional[str], list[dict]]:
    """Extract system messages and return (system_prompt, remaining_messages)."""
    system_parts = []
    other_messages = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"
                )
            system_parts.append(content)
        else:
            other_messages.append(msg)
    system_prompt = "\n\n".join(system_parts) if system_parts else None
    return system_prompt, other_messages


def format_tool_call_for_prompt(tool_call: dict) -> str:
    """Format an assistant tool_call into text for the prompt."""
    fn = tool_call.get("function", {})
    return f'[Tool call: {fn.get("name", "unknown")}({fn.get("arguments", "{}")})  id={tool_call.get("id", "")}]'


def format_tool_result_for_prompt(msg: dict) -> str:
    """Format a tool-role message into text for the prompt."""
    tool_call_id = msg.get("tool_call_id", "")
    content = msg.get("content", "")
    return f"[Tool result for {tool_call_id}]: {content}"


def messages_to_prompt(messages: list[dict]) -> str:
    """Convert OpenAI-format messages (excluding system) to a prompt string."""
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Handle content arrays (vision, multi-part)
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif block.get("type") == "image_url":
                        text_parts.append("[Image provided]")
            content = "\n".join(text_parts)

        if role == "user":
            parts.append(content)
        elif role == "assistant":
            # Include any tool calls the assistant made
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                tc_text = "\n".join(format_tool_call_for_prompt(tc) for tc in tool_calls)
                parts.append(f"[Previous assistant response]: {content}\n{tc_text}" if content else f"[Previous assistant]:\n{tc_text}")
            elif content:
                parts.append(f"[Previous assistant response]: {content}")
        elif role == "tool":
            parts.append(format_tool_result_for_prompt(msg))

    return "\n\n".join(parts)


def build_tools_system_section(tools: list[dict], tool_choice) -> str:
    """Build a system prompt section describing available tools in OpenAI function-calling format."""
    if not tools:
        return ""

    lines = [
        "\n\nYou have access to the following functions. To call a function, respond with a JSON object "
        'in this exact format on its own line: {"tool_call": {"name": "<function_name>", "arguments": {<args>}}}',
        "",
    ]
    for tool in tools:
        if tool.get("type") == "function":
            fn = tool["function"]
            lines.append(f'Function: {fn["name"]}')
            if fn.get("description"):
                lines.append(f'Description: {fn["description"]}')
            if fn.get("parameters"):
                lines.append(f"Parameters: {json.dumps(fn['parameters'])}")
            lines.append("")

    # Handle tool_choice
    if tool_choice == "none":
        lines.append("Do NOT call any functions. Respond normally.")
    elif tool_choice == "auto" or tool_choice is None:
        lines.append("Call functions if appropriate, otherwise respond normally.")
    elif tool_choice == "required":
        lines.append("You MUST call at least one function in your response.")
    elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        fn_name = tool_choice.get("function", {}).get("name", "")
        lines.append(f"You MUST call the function '{fn_name}'.")

    return "\n".join(lines)


def parse_tool_calls_from_response(text: str) -> tuple[str, list[dict]]:
    """Parse tool_call JSON objects from the assistant response text.

    Returns (clean_text, tool_calls) where clean_text has tool_call lines removed.
    """
    tool_calls = []
    clean_lines = []

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith('{"tool_call"'):
            try:
                parsed = json.loads(stripped)
                tc = parsed.get("tool_call", {})
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(tc.get("arguments", {})),
                    },
                })
                continue
            except json.JSONDecodeError:
                pass
        clean_lines.append(line)

    clean_text = "\n".join(clean_lines).strip()
    return clean_text, tool_calls


# ─── Claude CLI interface ────────────────────────────────────────────────────

async def call_claude(
    prompt: str,
    model: str,
    system_prompt: Optional[str] = None,
    stream: bool = False,
) -> dict:
    """Call claude CLI in pipe mode and return parsed result.

    Returns dict with keys: content, usage, stop_reason, model
    """
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", model,
        "--tools", "",  # disable Claude Code tools, we only want text generation
        "--no-session-persistence",
    ]
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=prompt.encode())

    if proc.returncode != 0:
        error_msg = stderr.decode().strip()
        raise RuntimeError(f"claude CLI error (exit {proc.returncode}): {error_msg}")

    raw = stdout.decode().strip()

    # Parse the JSON result
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # If JSON parsing fails, treat raw output as text
        return {
            "content": raw,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "stop_reason": "end_turn",
            "model": model,
        }

    usage = result.get("usage", {})
    return {
        "content": result.get("result", ""),
        "usage": {
            "input_tokens": usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        },
        "stop_reason": result.get("stop_reason", "end_turn"),
        "model": model,
    }


async def call_claude_streaming(
    prompt: str,
    model: str,
    system_prompt: Optional[str] = None,
):
    """Call claude CLI with real streaming, yielding (event_type, data) tuples."""
    cmd = [
        "claude", "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
    ]
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Write prompt and close stdin
    proc.stdin.write(prompt.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    # Read streaming output line by line
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        line = line.decode().strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            yield event
        except json.JSONDecodeError:
            continue

    await proc.wait()


# ─── API Endpoints ───────────────────────────────────────────────────────────

# --- Models ---

@app.get("/v1/models")
@app.get("/models")
async def list_models():
    """List available models."""
    data = list(CLAUDE_MODELS.values())
    # Also include aliases so clients can see them
    for alias, target in MODEL_ALIASES.items():
        data.append({
            "id": alias,
            "object": "model",
            "created": 1700000000,
            "owned_by": "anthropic",
            "parent": target,
        })
    return {"object": "list", "data": data}


@app.get("/v1/models/{model_id}")
@app.get("/models/{model_id}")
async def retrieve_model(model_id: str):
    """Retrieve a specific model."""
    if model_id in CLAUDE_MODELS:
        return CLAUDE_MODELS[model_id]
    if model_id in MODEL_ALIASES:
        target = MODEL_ALIASES[model_id]
        info = CLAUDE_MODELS[target].copy()
        info["id"] = model_id
        info["parent"] = target
        return info
    raise HTTPException(status_code=404, detail={
        "error": {
            "message": f"The model '{model_id}' does not exist",
            "type": "invalid_request_error",
            "param": "model",
            "code": "model_not_found",
        }
    })


# --- Chat Completions ---

@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions endpoint."""
    body = await request.json()

    messages = body.get("messages")
    if not messages:
        return openai_error(400, "'messages' is required", "invalid_request_error", "missing_messages")

    requested_model = body.get("model", DEFAULT_MODEL)
    model = resolve_model(requested_model)
    stream = body.get("stream", False)
    tools = body.get("tools", [])
    tool_choice = body.get("tool_choice", "auto")
    n = body.get("n", 1)

    if n != 1:
        return openai_error(400, "Only n=1 is supported", "invalid_request_error", "unsupported_n")

    # Extract system prompt from messages
    system_prompt, conversation_messages = extract_system_prompt(messages)

    # If tools are provided, inject tool descriptions into system prompt
    if tools:
        tools_section = build_tools_system_section(tools, tool_choice)
        system_prompt = (system_prompt or "") + tools_section

    # Convert conversation to prompt
    prompt = messages_to_prompt(conversation_messages)

    if not prompt.strip():
        return openai_error(400, "No user content provided", "invalid_request_error", "empty_prompt")

    # ── Streaming response ──
    if stream:
        return StreamingResponse(
            _stream_chat(prompt, model, requested_model, system_prompt, bool(tools)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Non-streaming response ──
    try:
        result = await call_claude(prompt, model, system_prompt)
    except RuntimeError as e:
        return openai_error(502, str(e), "server_error", "claude_cli_error")

    content = result["content"]
    finish_reason = STOP_REASON_MAP.get(result["stop_reason"], "stop")

    # Parse tool calls if tools were provided
    tool_calls = []
    if tools:
        content, tool_calls = parse_tool_calls_from_response(content)
        if tool_calls:
            finish_reason = "tool_calls"

    message = {"role": "assistant", "content": content or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if not content and tool_calls:
        message["content"] = None

    prompt_tokens = result["usage"]["input_tokens"]
    completion_tokens = result["usage"]["output_tokens"]

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": requested_model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "system_fingerprint": f"claude-proxy-{model}",
    }


async def _stream_chat(
    prompt: str,
    model: str,
    requested_model: str,
    system_prompt: Optional[str],
    has_tools: bool,
):
    """Generator for SSE streaming chat completions using real Claude streaming."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def make_chunk(delta: dict, finish_reason=None) -> str:
        chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": requested_model,
            "system_fingerprint": f"claude-proxy-{model}",
            "choices": [{
                "index": 0,
                "delta": delta,
                "logprobs": None,
                "finish_reason": finish_reason,
            }],
        }
        return f"data: {json.dumps(chunk)}\n\n"

    # Initial chunk with role
    yield make_chunk({"role": "assistant", "content": ""})

    accumulated_text = ""
    stop_reason = "stop"

    try:
        async for event in call_claude_streaming(prompt, model, system_prompt):
            event_type = event.get("type")

            if event_type == "stream_event":
                inner = event.get("event", {})
                inner_type = inner.get("type")

                if inner_type == "content_block_delta":
                    delta = inner.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            accumulated_text += text
                            yield make_chunk({"content": text})

                elif inner_type == "message_delta":
                    delta_data = inner.get("delta", {})
                    sr = delta_data.get("stop_reason", "end_turn")
                    stop_reason = STOP_REASON_MAP.get(sr, "stop")

            elif event_type == "result":
                sr = event.get("stop_reason", "end_turn")
                stop_reason = STOP_REASON_MAP.get(sr, "stop")

    except Exception as e:
        logger.error(f"Streaming error: {e}")
        yield make_chunk({"content": f"\n\n[Error: {e}]"})

    # If tools were requested, check for tool calls in accumulated text
    if has_tools and accumulated_text:
        clean_text, tool_calls = parse_tool_calls_from_response(accumulated_text)
        if tool_calls:
            stop_reason = "tool_calls"
            # Note: streaming tool calls is complex; for now we signal stop_reason

    # Final chunk
    yield make_chunk({}, finish_reason=stop_reason)
    yield "data: [DONE]\n\n"


# --- Legacy Completions ---

@app.post("/v1/completions")
@app.post("/completions")
async def completions(request: Request):
    """Legacy completions endpoint."""
    body = await request.json()

    prompt_text = body.get("prompt", "")
    if isinstance(prompt_text, list):
        prompt_text = "\n".join(prompt_text)
    if not prompt_text:
        return openai_error(400, "'prompt' is required", "invalid_request_error", "missing_prompt")

    requested_model = body.get("model", DEFAULT_MODEL)
    model = resolve_model(requested_model)
    stream = body.get("stream", False)
    suffix = body.get("suffix")

    system_prompt = None
    if suffix:
        system_prompt = f"After your response, append this suffix: {suffix}"

    if stream:
        return StreamingResponse(
            _stream_completion(prompt_text, model, requested_model, system_prompt),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    try:
        result = await call_claude(prompt_text, model, system_prompt)
    except RuntimeError as e:
        return openai_error(502, str(e), "server_error", "claude_cli_error")

    content = result["content"]
    if suffix:
        content += suffix

    prompt_tokens = result["usage"]["input_tokens"]
    completion_tokens = result["usage"]["output_tokens"]

    return {
        "id": f"cmpl-{uuid.uuid4().hex[:24]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": requested_model,
        "choices": [
            {
                "text": content,
                "index": 0,
                "logprobs": None,
                "finish_reason": STOP_REASON_MAP.get(result["stop_reason"], "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "system_fingerprint": f"claude-proxy-{model}",
    }


async def _stream_completion(
    prompt: str, model: str, requested_model: str, system_prompt: Optional[str]
):
    """Generator for SSE streaming legacy completions."""
    chunk_id = f"cmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def make_chunk(text: str, finish_reason=None) -> str:
        chunk = {
            "id": chunk_id,
            "object": "text_completion",
            "created": created,
            "model": requested_model,
            "choices": [{
                "text": text,
                "index": 0,
                "logprobs": None,
                "finish_reason": finish_reason,
            }],
        }
        return f"data: {json.dumps(chunk)}\n\n"

    try:
        async for event in call_claude_streaming(prompt, model, system_prompt):
            event_type = event.get("type")
            if event_type == "stream_event":
                inner = event.get("event", {})
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield make_chunk(text)
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        yield make_chunk(f"\n\n[Error: {e}]")

    yield make_chunk("", finish_reason="stop")
    yield "data: [DONE]\n\n"


# --- Unsupported endpoints (proper 501 responses) ---

UNSUPPORTED_MSG = "This endpoint is not supported. Claude does not provide this capability."

@app.post("/v1/embeddings")
@app.post("/embeddings")
async def embeddings(request: Request):
    return openai_error(501, f"Embeddings: {UNSUPPORTED_MSG}", "invalid_request_error", "unsupported_endpoint")

@app.post("/v1/images/generations")
@app.post("/images/generations")
async def image_generations(request: Request):
    return openai_error(501, f"Image generation: {UNSUPPORTED_MSG}", "invalid_request_error", "unsupported_endpoint")

@app.post("/v1/images/edits")
@app.post("/images/edits")
async def image_edits(request: Request):
    return openai_error(501, f"Image editing: {UNSUPPORTED_MSG}", "invalid_request_error", "unsupported_endpoint")

@app.post("/v1/audio/transcriptions")
@app.post("/audio/transcriptions")
async def audio_transcriptions(request: Request):
    return openai_error(501, f"Audio transcription: {UNSUPPORTED_MSG}", "invalid_request_error", "unsupported_endpoint")

@app.post("/v1/audio/translations")
@app.post("/audio/translations")
async def audio_translations(request: Request):
    return openai_error(501, f"Audio translation: {UNSUPPORTED_MSG}", "invalid_request_error", "unsupported_endpoint")

@app.post("/v1/audio/speech")
@app.post("/audio/speech")
async def audio_speech(request: Request):
    return openai_error(501, f"Text-to-speech: {UNSUPPORTED_MSG}", "invalid_request_error", "unsupported_endpoint")

@app.post("/v1/fine_tuning/jobs")
@app.post("/fine_tuning/jobs")
async def fine_tuning(request: Request):
    return openai_error(501, f"Fine-tuning: {UNSUPPORTED_MSG}", "invalid_request_error", "unsupported_endpoint")

@app.post("/v1/moderations")
@app.post("/moderations")
async def moderations(request: Request):
    return openai_error(501, f"Moderations: {UNSUPPORTED_MSG}", "invalid_request_error", "unsupported_endpoint")


# --- Health ---

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0", "backend": "claude-code-cli"}


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude Code OpenAI-compatible proxy")
    parser.add_argument("--port", type=int, default=4000, help="Port to listen on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()

    print(f"╔══════════════════════════════════════════════════════════╗")
    print(f"║  Claude Code OpenAI Proxy v2.0                         ║")
    print(f"║  Listening on http://{args.host}:{args.port:<5}                      ║")
    print(f"║  API base: http://<host>:{args.port}/v1                      ║")
    print(f"║  Backend: Claude Code CLI (Max subscription)            ║")
    print(f"╚══════════════════════════════════════════════════════════╝")
    uvicorn.run(app, host=args.host, port=args.port, access_log=True)

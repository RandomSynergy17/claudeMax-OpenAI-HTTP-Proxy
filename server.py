"""
Dual OpenAI + Anthropic compatible API proxy powered by Claude Code CLI (Max subscription).

Provides two fully spec-compliant API surfaces:
  - /openai/v1/*   — OpenAI API compatible (chat completions, completions, models)
  - /anthropic/v1/* — Anthropic API compatible (messages, models)

Both route through `claude -p` using your Claude Max subscription auth.

Usage:
    source ~/claude-proxy-venv/bin/activate
    python server.py [--port 4000] [--host 0.0.0.0]
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

app = FastAPI(title="Claude Max API Proxy", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═════════════════════════════════════════════════════════════════════════════
# SHARED: Model definitions & CLI interface
# ═════════════════════════════════════════════════════════════════════════════

CLAUDE_MODELS = {
    "claude-opus-4-6": {
        "id": "claude-opus-4-6",
        "display_name": "Claude Opus 4.6",
        "created": 1700000000,
        "context_window": 200000,
        "max_output_tokens": 32000,
    },
    "claude-sonnet-4-6": {
        "id": "claude-sonnet-4-6",
        "display_name": "Claude Sonnet 4.6",
        "created": 1700000000,
        "context_window": 200000,
        "max_output_tokens": 32000,
    },
    "claude-haiku-4-5": {
        "id": "claude-haiku-4-5",
        "display_name": "Claude Haiku 4.5",
        "created": 1700000000,
        "context_window": 200000,
        "max_output_tokens": 32000,
    },
}

OPENAI_MODEL_ALIASES = {
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

STOP_REASON_TO_OPENAI = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
}


def resolve_model_openai(requested: str) -> str:
    if requested in CLAUDE_MODELS:
        return requested
    return OPENAI_MODEL_ALIASES.get(requested, DEFAULT_MODEL)


def resolve_model_anthropic(requested: str) -> str:
    if requested in CLAUDE_MODELS:
        return requested
    return DEFAULT_MODEL


# ─── Claude CLI interface (shared) ───────────────────────────────────────────

async def call_claude(
    prompt: str,
    model: str,
    system_prompt: Optional[str] = None,
) -> dict:
    """Non-streaming call to claude CLI. Returns parsed result dict."""
    cmd = [
        "claude", "-p",
        "--output-format", "json",
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
    stdout, stderr = await proc.communicate(input=prompt.encode())

    if proc.returncode != 0:
        error_msg = stderr.decode().strip()
        raise RuntimeError(f"claude CLI error (exit {proc.returncode}): {error_msg}")

    raw = stdout.decode().strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
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
            "input_tokens": (
                usage.get("input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
            ),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        },
        "stop_reason": result.get("stop_reason", "end_turn"),
        "model": model,
    }


async def call_claude_streaming(
    prompt: str,
    model: str,
    system_prompt: Optional[str] = None,
):
    """Streaming call to claude CLI, yielding parsed JSON events."""
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

    proc.stdin.write(prompt.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        line = line.decode().strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue

    await proc.wait()


# ═════════════════════════════════════════════════════════════════════════════
# OPENAI API — /openai/v1/*
# ═════════════════════════════════════════════════════════════════════════════

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


# ─── OpenAI message conversion ───────────────────────────────────────────────

def openai_extract_system(messages: list[dict]) -> tuple[Optional[str], list[dict]]:
    system_parts = []
    other = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"
                )
            system_parts.append(content)
        else:
            other.append(msg)
    return ("\n\n".join(system_parts) if system_parts else None), other


def openai_messages_to_prompt(messages: list[dict]) -> str:
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
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
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                tc_text = "\n".join(
                    f'[Tool call: {tc.get("function", {}).get("name", "unknown")}'
                    f'({tc.get("function", {}).get("arguments", "{}")}) '
                    f'id={tc.get("id", "")}]'
                    for tc in tool_calls
                )
                parts.append(
                    f"[Previous assistant response]: {content}\n{tc_text}"
                    if content
                    else f"[Previous assistant]:\n{tc_text}"
                )
            elif content:
                parts.append(f"[Previous assistant response]: {content}")
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            parts.append(f"[Tool result for {tool_call_id}]: {content}")
    return "\n\n".join(parts)


def openai_build_tools_system(tools: list[dict], tool_choice) -> str:
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


def openai_parse_tool_calls(text: str) -> tuple[str, list[dict]]:
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
    return "\n".join(clean_lines).strip(), tool_calls


# ─── OpenAI Models ───────────────────────────────────────────────────────────

@app.get("/openai/v1/models")
@app.get("/v1/models")
async def openai_list_models():
    data = []
    for model_id, info in CLAUDE_MODELS.items():
        data.append({
            "id": model_id,
            "object": "model",
            "created": info["created"],
            "owned_by": "anthropic",
        })
    for alias, target in OPENAI_MODEL_ALIASES.items():
        data.append({
            "id": alias,
            "object": "model",
            "created": 1700000000,
            "owned_by": "anthropic",
            "parent": target,
        })
    return {"object": "list", "data": data}


@app.get("/openai/v1/models/{model_id}")
@app.get("/v1/models/{model_id}")
async def openai_retrieve_model(model_id: str):
    if model_id in CLAUDE_MODELS:
        info = CLAUDE_MODELS[model_id]
        return {"id": model_id, "object": "model", "created": info["created"], "owned_by": "anthropic"}
    if model_id in OPENAI_MODEL_ALIASES:
        target = OPENAI_MODEL_ALIASES[model_id]
        return {"id": model_id, "object": "model", "created": 1700000000, "owned_by": "anthropic", "parent": target}
    return openai_error(404, f"The model '{model_id}' does not exist", "invalid_request_error", "model_not_found")


# ─── OpenAI Chat Completions ─────────────────────────────────────────────────

@app.post("/openai/v1/chat/completions")
@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages")
    if not messages:
        return openai_error(400, "'messages' is required", "invalid_request_error", "missing_messages")

    requested_model = body.get("model", DEFAULT_MODEL)
    model = resolve_model_openai(requested_model)
    stream = body.get("stream", False)
    tools = body.get("tools", [])
    tool_choice = body.get("tool_choice", "auto")
    n = body.get("n", 1)

    if n != 1:
        return openai_error(400, "Only n=1 is supported", "invalid_request_error", "unsupported_n")

    system_prompt, conversation_messages = openai_extract_system(messages)
    if tools:
        system_prompt = (system_prompt or "") + openai_build_tools_system(tools, tool_choice)

    prompt = openai_messages_to_prompt(conversation_messages)
    if not prompt.strip():
        return openai_error(400, "No user content provided", "invalid_request_error", "empty_prompt")

    if stream:
        return StreamingResponse(
            _openai_stream_chat(prompt, model, requested_model, system_prompt, bool(tools)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    try:
        result = await call_claude(prompt, model, system_prompt)
    except RuntimeError as e:
        return openai_error(502, str(e), "server_error", "claude_cli_error")

    content = result["content"]
    finish_reason = STOP_REASON_TO_OPENAI.get(result["stop_reason"], "stop")

    tool_calls_list = []
    if tools:
        content, tool_calls_list = openai_parse_tool_calls(content)
        if tool_calls_list:
            finish_reason = "tool_calls"

    message = {"role": "assistant", "content": content or None}
    if tool_calls_list:
        message["tool_calls"] = tool_calls_list
        if not content:
            message["content"] = None

    prompt_tokens = result["usage"]["input_tokens"]
    completion_tokens = result["usage"]["output_tokens"]

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": requested_model,
        "choices": [{
            "index": 0,
            "message": message,
            "logprobs": None,
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "system_fingerprint": f"claude-proxy-{model}",
    }


async def _openai_stream_chat(prompt, model, requested_model, system_prompt, has_tools):
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def make_chunk(delta, finish_reason=None):
        return f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created, 'model': requested_model, 'system_fingerprint': f'claude-proxy-{model}', 'choices': [{'index': 0, 'delta': delta, 'logprobs': None, 'finish_reason': finish_reason}]})}\n\n"

    yield make_chunk({"role": "assistant", "content": ""})

    accumulated = ""
    stop_reason = "stop"

    try:
        async for event in call_claude_streaming(prompt, model, system_prompt):
            et = event.get("type")
            if et == "stream_event":
                inner = event.get("event", {})
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            accumulated += text
                            yield make_chunk({"content": text})
                elif inner.get("type") == "message_delta":
                    sr = inner.get("delta", {}).get("stop_reason", "end_turn")
                    stop_reason = STOP_REASON_TO_OPENAI.get(sr, "stop")
            elif et == "result":
                stop_reason = STOP_REASON_TO_OPENAI.get(event.get("stop_reason", "end_turn"), "stop")
    except Exception as e:
        logger.error(f"OpenAI streaming error: {e}")
        yield make_chunk({"content": f"\n\n[Error: {e}]"})

    if has_tools and accumulated:
        _, tool_calls = openai_parse_tool_calls(accumulated)
        if tool_calls:
            stop_reason = "tool_calls"

    yield make_chunk({}, finish_reason=stop_reason)
    yield "data: [DONE]\n\n"


# ─── OpenAI Legacy Completions ────────────────────────────────────────────────

@app.post("/openai/v1/completions")
@app.post("/v1/completions")
async def openai_completions(request: Request):
    body = await request.json()
    prompt_text = body.get("prompt", "")
    if isinstance(prompt_text, list):
        prompt_text = "\n".join(prompt_text)
    if not prompt_text:
        return openai_error(400, "'prompt' is required", "invalid_request_error", "missing_prompt")

    requested_model = body.get("model", DEFAULT_MODEL)
    model = resolve_model_openai(requested_model)
    stream = body.get("stream", False)
    suffix = body.get("suffix")
    system_prompt = f"After your response, append this suffix: {suffix}" if suffix else None

    if stream:
        return StreamingResponse(
            _openai_stream_completion(prompt_text, model, requested_model, system_prompt),
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

    pt = result["usage"]["input_tokens"]
    ct = result["usage"]["output_tokens"]

    return {
        "id": f"cmpl-{uuid.uuid4().hex[:24]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": requested_model,
        "choices": [{"text": content, "index": 0, "logprobs": None, "finish_reason": STOP_REASON_TO_OPENAI.get(result["stop_reason"], "stop")}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
        "system_fingerprint": f"claude-proxy-{model}",
    }


async def _openai_stream_completion(prompt, model, requested_model, system_prompt):
    chunk_id = f"cmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def make_chunk(text, finish_reason=None):
        return f"data: {json.dumps({'id': chunk_id, 'object': 'text_completion', 'created': created, 'model': requested_model, 'choices': [{'text': text, 'index': 0, 'logprobs': None, 'finish_reason': finish_reason}]})}\n\n"

    try:
        async for event in call_claude_streaming(prompt, model, system_prompt):
            if event.get("type") == "stream_event":
                inner = event.get("event", {})
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield make_chunk(text)
    except Exception as e:
        logger.error(f"OpenAI streaming error: {e}")
        yield make_chunk(f"\n\n[Error: {e}]")

    yield make_chunk("", finish_reason="stop")
    yield "data: [DONE]\n\n"


# ─── OpenAI Unsupported endpoints ────────────────────────────────────────────

_OAI_UNSUPPORTED = "This endpoint is not supported. Claude does not provide this capability."

@app.post("/openai/v1/embeddings")
@app.post("/v1/embeddings")
async def openai_embeddings(request: Request):
    return openai_error(501, f"Embeddings: {_OAI_UNSUPPORTED}", "invalid_request_error", "unsupported_endpoint")

@app.post("/openai/v1/images/generations")
@app.post("/v1/images/generations")
async def openai_images_gen(request: Request):
    return openai_error(501, f"Image generation: {_OAI_UNSUPPORTED}", "invalid_request_error", "unsupported_endpoint")

@app.post("/openai/v1/images/edits")
@app.post("/v1/images/edits")
async def openai_images_edit(request: Request):
    return openai_error(501, f"Image editing: {_OAI_UNSUPPORTED}", "invalid_request_error", "unsupported_endpoint")

@app.post("/openai/v1/audio/transcriptions")
@app.post("/v1/audio/transcriptions")
async def openai_audio_transcribe(request: Request):
    return openai_error(501, f"Audio transcription: {_OAI_UNSUPPORTED}", "invalid_request_error", "unsupported_endpoint")

@app.post("/openai/v1/audio/translations")
@app.post("/v1/audio/translations")
async def openai_audio_translate(request: Request):
    return openai_error(501, f"Audio translation: {_OAI_UNSUPPORTED}", "invalid_request_error", "unsupported_endpoint")

@app.post("/openai/v1/audio/speech")
@app.post("/v1/audio/speech")
async def openai_audio_speech(request: Request):
    return openai_error(501, f"Text-to-speech: {_OAI_UNSUPPORTED}", "invalid_request_error", "unsupported_endpoint")

@app.post("/openai/v1/fine_tuning/jobs")
@app.post("/v1/fine_tuning/jobs")
async def openai_fine_tuning(request: Request):
    return openai_error(501, f"Fine-tuning: {_OAI_UNSUPPORTED}", "invalid_request_error", "unsupported_endpoint")

@app.post("/openai/v1/moderations")
@app.post("/v1/moderations")
async def openai_moderations(request: Request):
    return openai_error(501, f"Moderations: {_OAI_UNSUPPORTED}", "invalid_request_error", "unsupported_endpoint")


# ═════════════════════════════════════════════════════════════════════════════
# ANTHROPIC API — /anthropic/v1/*
# ═════════════════════════════════════════════════════════════════════════════

def anthropic_error(status: int, error_type: str, message: str) -> JSONResponse:
    """Return an Anthropic-format error response."""
    return JSONResponse(
        status_code=status,
        content={
            "type": "error",
            "error": {
                "type": error_type,
                "message": message,
            },
        },
    )


# ─── Anthropic message conversion ────────────────────────────────────────────

def anthropic_extract_system(body: dict) -> Optional[str]:
    """Extract system prompt from Anthropic request (top-level 'system' field)."""
    system = body.get("system")
    if system is None:
        return None
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n\n".join(
            b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(system)


def anthropic_content_to_text(content) -> str:
    """Convert Anthropic content (string or block array) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "image":
                    parts.append("[Image provided]")
                elif btype == "tool_use":
                    parts.append(
                        f'[Tool call: {block.get("name", "unknown")}'
                        f'({json.dumps(block.get("input", {}))}) '
                        f'id={block.get("id", "")}]'
                    )
                elif btype == "tool_result":
                    tool_content = block.get("content", "")
                    if isinstance(tool_content, list):
                        tool_content = "\n".join(
                            b.get("text", "") for b in tool_content
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    parts.append(f'[Tool result for {block.get("tool_use_id", "")}]: {tool_content}')
        return "\n".join(parts)
    return str(content)


def anthropic_messages_to_prompt(messages: list[dict]) -> str:
    """Convert Anthropic-format messages to a prompt string."""
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        text = anthropic_content_to_text(msg.get("content", ""))
        if role == "user":
            parts.append(text)
        elif role == "assistant":
            parts.append(f"[Previous assistant response]: {text}")
    return "\n\n".join(parts)


def anthropic_build_tools_system(tools: list[dict], tool_choice: Optional[dict]) -> str:
    """Build system prompt section for Anthropic-format tools."""
    if not tools:
        return ""
    lines = [
        "\n\nYou have access to the following tools. To use a tool, respond with a JSON object "
        'in this exact format on its own line: {"tool_use": {"name": "<tool_name>", "input": {<args>}}}',
        "",
    ]
    for tool in tools:
        lines.append(f'Tool: {tool.get("name", "unknown")}')
        if tool.get("description"):
            lines.append(f'Description: {tool["description"]}')
        if tool.get("input_schema"):
            lines.append(f"Input schema: {json.dumps(tool['input_schema'])}")
        lines.append("")

    if tool_choice:
        tc_type = tool_choice.get("type", "auto")
        if tc_type == "none":
            lines.append("Do NOT use any tools. Respond normally.")
        elif tc_type == "auto":
            lines.append("Use tools if appropriate, otherwise respond normally.")
        elif tc_type == "any":
            lines.append("You MUST use at least one tool in your response.")
        elif tc_type == "tool":
            lines.append(f'You MUST use the tool \'{tool_choice.get("name", "")}\'.')
    else:
        lines.append("Use tools if appropriate, otherwise respond normally.")

    return "\n".join(lines)


def anthropic_parse_tool_use(text: str) -> tuple[str, list[dict]]:
    """Parse tool_use JSON objects from response text."""
    tool_uses = []
    clean_lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith('{"tool_use"'):
            try:
                parsed = json.loads(stripped)
                tu = parsed.get("tool_use", {})
                tool_uses.append({
                    "type": "tool_use",
                    "id": f"toolu_{uuid.uuid4().hex[:24]}",
                    "name": tu.get("name", ""),
                    "input": tu.get("input", {}),
                })
                continue
            except json.JSONDecodeError:
                pass
        clean_lines.append(line)
    return "\n".join(clean_lines).strip(), tool_uses


# ─── Anthropic Models ────────────────────────────────────────────────────────

@app.get("/anthropic/v1/models")
async def anthropic_list_models():
    """List models in Anthropic API format."""
    data = []
    for model_id, info in CLAUDE_MODELS.items():
        data.append({
            "id": model_id,
            "type": "model",
            "display_name": info["display_name"],
            "created_at": "2024-01-01T00:00:00Z",
        })
    return {
        "data": data,
        "has_more": False,
        "first_id": list(CLAUDE_MODELS.keys())[0],
        "last_id": list(CLAUDE_MODELS.keys())[-1],
    }


@app.get("/anthropic/v1/models/{model_id}")
async def anthropic_retrieve_model(model_id: str):
    """Retrieve a model in Anthropic API format."""
    if model_id not in CLAUDE_MODELS:
        return anthropic_error(404, "not_found_error", f"model '{model_id}' not found")
    info = CLAUDE_MODELS[model_id]
    return {
        "id": model_id,
        "type": "model",
        "display_name": info["display_name"],
        "created_at": "2024-01-01T00:00:00Z",
    }


# ─── Anthropic Messages ──────────────────────────────────────────────────────

@app.post("/anthropic/v1/messages")
async def anthropic_messages(request: Request):
    """Anthropic Messages API — fully spec-compliant."""
    body = await request.json()

    # ── Validate required fields ──
    model_requested = body.get("model")
    if not model_requested:
        return anthropic_error(400, "invalid_request_error", "'model' is required")

    messages = body.get("messages")
    if not messages:
        return anthropic_error(400, "invalid_request_error", "'messages' is required")

    max_tokens = body.get("max_tokens")
    if max_tokens is None:
        return anthropic_error(400, "invalid_request_error", "'max_tokens' is required")

    model = resolve_model_anthropic(model_requested)
    stream = body.get("stream", False)
    tools = body.get("tools", [])
    tool_choice = body.get("tool_choice")
    stop_sequences = body.get("stop_sequences", [])

    # ── Build prompt ──
    system_prompt = anthropic_extract_system(body)

    if tools:
        tools_section = anthropic_build_tools_system(tools, tool_choice)
        system_prompt = (system_prompt or "") + tools_section

    if stop_sequences:
        stop_note = f"\n\nIMPORTANT: Stop generating immediately when you are about to output any of these sequences: {json.dumps(stop_sequences)}"
        system_prompt = (system_prompt or "") + stop_note

    prompt = anthropic_messages_to_prompt(messages)
    if not prompt.strip():
        return anthropic_error(400, "invalid_request_error", "No user content provided in messages")

    # ── Streaming ──
    if stream:
        return StreamingResponse(
            _anthropic_stream_messages(prompt, model, model_requested, system_prompt, bool(tools)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # ── Non-streaming ──
    try:
        result = await call_claude(prompt, model, system_prompt)
    except RuntimeError as e:
        return anthropic_error(502, "api_error", str(e))

    content_text = result["content"]
    stop_reason = result["stop_reason"]

    # Build content blocks
    content_blocks = []
    if tools:
        clean_text, tool_uses = anthropic_parse_tool_use(content_text)
        if clean_text:
            content_blocks.append({"type": "text", "text": clean_text})
        content_blocks.extend(tool_uses)
        if tool_uses:
            stop_reason = "tool_use"
    else:
        content_blocks.append({"type": "text", "text": content_text})

    input_tokens = result["usage"]["input_tokens"]
    output_tokens = result["usage"]["output_tokens"]

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model_requested,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": result["usage"].get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": result["usage"].get("cache_read_input_tokens", 0),
        },
    }


async def _anthropic_stream_messages(prompt, model, model_requested, system_prompt, has_tools):
    """Generator for Anthropic SSE streaming messages.

    Anthropic streaming uses named SSE events:
        event: message_start
        data: {...}

        event: content_block_start
        data: {...}

        event: content_block_delta
        data: {...}

        event: content_block_stop
        data: {...}

        event: message_delta
        data: {...}

        event: message_stop
        data: {...}
    """
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"

    # ── message_start ──
    yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'content': [], 'model': model_requested, 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}})}\n\n"

    # ── content_block_start ──
    yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"

    # ── ping ──
    yield f"event: ping\ndata: {json.dumps({'type': 'ping'})}\n\n"

    accumulated = ""
    stop_reason = "end_turn"
    input_tokens = 0
    output_tokens = 0

    try:
        async for event in call_claude_streaming(prompt, model, system_prompt):
            et = event.get("type")

            if et == "stream_event":
                inner = event.get("event", {})
                inner_type = inner.get("type")

                if inner_type == "content_block_delta":
                    delta = inner.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            accumulated += text
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text}})}\n\n"

                elif inner_type == "message_start":
                    msg_data = inner.get("message", {})
                    usage = msg_data.get("usage", {})
                    input_tokens = (
                        usage.get("input_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                    )

                elif inner_type == "message_delta":
                    delta_data = inner.get("delta", {})
                    stop_reason = delta_data.get("stop_reason", "end_turn")
                    usage = inner.get("usage", {})
                    output_tokens = usage.get("output_tokens", output_tokens)

            elif et == "result":
                stop_reason = event.get("stop_reason", "end_turn")
                usage = event.get("usage", {})
                input_tokens = (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                )
                output_tokens = usage.get("output_tokens", output_tokens)

    except Exception as e:
        logger.error(f"Anthropic streaming error: {e}")
        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': f'[Error: {e}]'}})}\n\n"

    # Check for tool use in accumulated text
    if has_tools and accumulated:
        _, tool_uses = anthropic_parse_tool_use(accumulated)
        if tool_uses:
            stop_reason = "tool_use"

    # ── content_block_stop ──
    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"

    # If tool uses were found, emit them as additional content blocks
    if has_tools and accumulated:
        _, tool_uses = anthropic_parse_tool_use(accumulated)
        for i, tu in enumerate(tool_uses, start=1):
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': i, 'content_block': {'type': 'tool_use', 'id': tu['id'], 'name': tu['name'], 'input': {}}})}\n\n"
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': i, 'delta': {'type': 'input_json_delta', 'partial_json': json.dumps(tu['input'])}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"

    # ── message_delta ──
    yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': output_tokens}})}\n\n"

    # ── message_stop ──
    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"


# ─── Anthropic Token Counting ────────────────────────────────────────────────

@app.post("/anthropic/v1/messages/count_tokens")
async def anthropic_count_tokens(request: Request):
    """Approximate token count (Anthropic-format)."""
    body = await request.json()
    messages = body.get("messages", [])
    system = anthropic_extract_system(body) or ""

    total_text = system
    for msg in messages:
        total_text += " " + anthropic_content_to_text(msg.get("content", ""))

    # Rough approximation: ~4 chars per token
    approx_tokens = max(1, len(total_text) // 4)
    return {"input_tokens": approx_tokens}


# ═════════════════════════════════════════════════════════════════════════════
# HEALTH & ROOT
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "3.0.0",
        "backend": "claude-code-cli",
        "endpoints": {
            "openai": "/openai/v1",
            "anthropic": "/anthropic/v1",
        },
    }


@app.get("/")
async def root():
    return {
        "service": "Claude Max API Proxy",
        "version": "3.0.0",
        "endpoints": {
            "openai": {
                "base_url": "/openai/v1",
                "chat_completions": "/openai/v1/chat/completions",
                "completions": "/openai/v1/completions",
                "models": "/openai/v1/models",
            },
            "anthropic": {
                "base_url": "/anthropic/v1",
                "messages": "/anthropic/v1/messages",
                "models": "/anthropic/v1/models",
                "count_tokens": "/anthropic/v1/messages/count_tokens",
            },
            "health": "/health",
        },
        "note": "OpenAI endpoints are also available at /v1/* for backwards compatibility.",
    }


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Claude Max Dual API Proxy")
    parser.add_argument("--port", type=int, default=4000, help="Port to listen on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════════════╗")
    print("║  Claude Max API Proxy v3.0                              ║")
    print(f"║  Listening on http://{args.host}:{args.port:<5}                      ║")
    print("║                                                          ║")
    print(f"║  OpenAI API:    http://<host>:{args.port}/openai/v1          ║")
    print(f"║  Anthropic API: http://<host>:{args.port}/anthropic/v1      ║")
    print(f"║  Legacy compat: http://<host>:{args.port}/v1                ║")
    print("║                                                          ║")
    print("║  Backend: Claude Code CLI (Max subscription)             ║")
    print("╚══════════════════════════════════════════════════════════╝")
    uvicorn.run(app, host=args.host, port=args.port, access_log=True)

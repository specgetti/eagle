"""Progress lines for the review pod's log.

The runner prints what it is doing (checkout, agent start, each tool the agent
calls, the outcome) so `oc logs` on a review pod shows a live trace. Every line
is redacted and kept to one line of bounded length: tool inputs and agent text
can contain anything from the repository under review.
"""

import json
import re
import sys
import time
from collections.abc import Callable
from typing import Any, TextIO

LINE_MAX = 300
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***")
    return text


class Progress:
    def __init__(
        self,
        secrets: list[str],
        out: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._secrets = secrets
        self._out = out
        self._clock = clock
        self._start = clock()

    def log(self, message: str) -> None:
        text = redact(_ANSI.sub("", message), self._secrets)
        text = " ".join(text.split())
        if not text:
            return
        if len(text) > LINE_MAX:
            text = text[: LINE_MAX - 3] + "..."
        elapsed = self._clock() - self._start
        print(f"[{elapsed:5.0f}s] {text}", file=self._out or sys.stdout, flush=True)


def describe_claude_event(line: str) -> list[str]:
    """Readable lines for one Claude Code stream-json event (may be none)."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return [line]
    if not isinstance(event, dict):
        return []

    match event.get("type"):
        case "system" if event.get("subtype") == "init":
            version = event.get("claude_code_version")
            name = f"Claude Code {version}" if version else "Claude Code"
            return [f"{name} started: model {event.get('model')}"]
        case "assistant":
            lines = []
            for block in _content(event):
                if block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    lines.append(f"tool {name} {_tool_argument(block.get('input'))}".rstrip())
                elif block.get("type") == "text" and block.get("text", "").strip():
                    lines.append(f"agent: {block['text'].strip()}")
            return lines
        case "user":
            return [
                f"tool error: {_text_of(block.get('content'))}"
                for block in _content(event)
                if block.get("type") == "tool_result" and block.get("is_error")
            ]
        case "result":
            return [_describe_result(event)]
    return []


def _content(event: dict[str, Any]) -> list[dict[str, Any]]:
    content = (event.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _tool_argument(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "pattern", "path", "url"):
        if isinstance(tool_input.get(key), str):
            value = tool_input[key]
            if key == "pattern" and isinstance(tool_input.get("path"), str):
                value += f" in {tool_input['path']}"
            return value
    return json.dumps(tool_input)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _describe_result(event: dict[str, Any]) -> str:
    if event.get("is_error"):
        status = event.get("api_error_status")
        suffix = f" (HTTP {status})" if status else ""
        return f"Claude Code failed: {event.get('result') or event.get('subtype')}{suffix}"
    parts = [f"{event.get('num_turns', '?')} turns"]
    if isinstance(event.get("duration_ms"), int | float):
        parts.append(f"{event['duration_ms'] / 1000:.0f}s")
    usage = event.get("usage") or {}
    if isinstance(usage, dict) and "output_tokens" in usage:
        tokens_in = sum(
            usage.get(k) or 0
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        )
        parts.append(f"{tokens_in} tokens in, {usage['output_tokens']} out")
    return "Claude Code finished: " + ", ".join(parts)

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Codex prints its settings before running, one per line ("model: gpt-5-codex").
_CODEX_MODEL = re.compile(r"^model:\s*(\S+)\s*$", re.MULTILINE)
_VERDICT = re.compile(
    r"^[\s>*_`]*VERDICT[\s*_`]*:[\s*_`]*(approve|comment|request[_ ]changes)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
SUMMARY_MAX = 1000


class AgentOutputError(Exception):
    pass


@dataclass
class AgentOutput:
    text: str
    usage: dict[str, Any] | None = None
    cost_usd: float | None = None


@dataclass
class ParsedReview:
    markdown: str
    verdict: str | None
    summary: str | None


def parse_agent_output(
    output_kind: str, stdout: str, output_file: str | None = None
) -> AgentOutput:
    if output_kind == "claude_json":
        return _parse_claude(stdout)
    if output_kind == "file":
        if not output_file or not Path(output_file).is_file():
            raise AgentOutputError("Agent did not write its final message file")
        return AgentOutput(text=Path(output_file).read_text())
    return AgentOutput(text=_ANSI.sub("", stdout))


def find_claude_result(stdout: str) -> dict[str, Any] | None:
    """The final result object in Claude Code's output.

    With `--output-format stream-json` that is the last event of type "result";
    with `--output-format json` it is the single object printed (no "type", or
    type "result"). Other events (init, assistant, user) are skipped.
    """
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("type", "result") == "result":
            return data
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def reported_model(output_kind: str | None, stdout: str, stderr: str) -> str | None:
    """The model the agent says it used, from its own output, if it says.

    Claude Code names it in the stream-json init event; Codex in the settings
    header it prints before running. OpenCode does not report it.
    """
    if output_kind == "claude_json":
        for event in _json_events(stdout):
            if (
                event.get("type") == "system"
                and event.get("subtype") == "init"
                and isinstance(event.get("model"), str)
                and event["model"]
            ):
                return event["model"]
        return None
    for text in (stderr, stdout):
        match = _CODEX_MODEL.search(_ANSI.sub("", text))
        if match:
            return match.group(1)
    return None


MODEL_REPORT_MAX = 20
# Claude Code labels messages it made up itself (errors, interrupts) this way.
_SYNTHETIC_MODEL = "<synthetic>"


def same_model(requested: str, served: str) -> bool:
    """Whether a served model name is the requested one.

    Endpoints often answer with a fuller id than the one requested: a dated
    version (claude-sonnet-4-5 -> claude-sonnet-4-5-20250929, or @20250929 on
    Vertex) or a provider prefix (glm-5 -> zai/glm-5). Those count as the same.
    """
    a, b = requested.strip().lower(), served.strip().lower()
    if not a or not b:
        return False
    for x, y in ((a, b), (a.rsplit("/", 1)[-1], b.rsplit("/", 1)[-1])):
        if x == y:
            return True
        short, full = sorted((x, y), key=len)
        if full.startswith(short) and full[len(short)] in "-@:":
            return True
    return False


def _json_events(stdout: str):
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def claude_model_report(stdout: str) -> dict[str, Any] | None:
    """Which models answered, from Claude Code's stream-json output.

    "served": the model each API response says it came from (message.model,
    set by the endpoint), with responses and tokens per model. Claude Code
    prints one event per content block, so responses are counted by message id.
    "usage_by_model": Claude Code's own per-model totals from the result event,
    which also cover background requests that print no assistant event.
    None when the output has neither.
    """
    served: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    usage_by_model: list[dict[str, Any]] = []
    for event in _json_events(stdout):
        if event.get("type") == "assistant":
            message = event.get("message") or {}
            name = message.get("model") if isinstance(message, dict) else None
            if not isinstance(name, str) or not name or name == _SYNTHETIC_MODEL:
                continue
            message_id = message.get("id")
            if isinstance(message_id, str):
                if message_id in seen:
                    continue
                seen.add(message_id)
            name = name[:128]
            entry = served.setdefault(
                name, {"model": name, "responses": 0, "input_tokens": 0, "output_tokens": 0}
            )
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            entry["responses"] += 1
            entry["input_tokens"] += sum(
                _int(usage.get(k))
                for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            )
            entry["output_tokens"] += _int(usage.get("output_tokens"))
        elif event.get("type") == "result" and isinstance(event.get("modelUsage"), dict):
            usage_by_model = [
                {
                    "model": str(name)[:128],
                    "input_tokens": sum(
                        _int(u.get(k))
                        for k in (
                            "inputTokens",
                            "cacheReadInputTokens",
                            "cacheCreationInputTokens",
                        )
                    ),
                    "output_tokens": _int(u.get("outputTokens")),
                }
                for name, u in event["modelUsage"].items()
                if isinstance(u, dict)
            ]
    if not served and not usage_by_model:
        return None
    return {
        "served": _top(list(served.values())),
        "usage_by_model": _top(usage_by_model),
    }


def _top(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The models that produced the most output first, at most MODEL_REPORT_MAX."""
    return sorted(entries, key=lambda e: -e["output_tokens"])[:MODEL_REPORT_MAX]


def _parse_claude(stdout: str) -> AgentOutput:
    data = find_claude_result(stdout)
    if data is None:
        raise AgentOutputError("Could not parse Claude Code JSON output")
    if data.get("is_error"):
        raise AgentOutputError(f"Claude Code reported an error: {data.get('result')}")
    if not isinstance(data.get("result"), str):
        raise AgentOutputError("Claude Code output has no result text")
    cost = data.get("total_cost_usd")
    return AgentOutput(
        text=data["result"],
        usage=data.get("usage"),
        cost_usd=float(cost) if isinstance(cost, int | float) else None,
    )


def parse_review(text: str) -> ParsedReview:
    text = text.strip()
    if not text:
        raise AgentOutputError("Agent returned an empty review")

    verdict = None
    matches = list(_VERDICT.finditer(text))
    if matches:
        last = matches[-1]
        verdict = last.group(1).lower().replace(" ", "_")
        text = (text[: last.start()] + text[last.end() :]).strip()

    return ParsedReview(markdown=text, verdict=verdict, summary=_summary(text))


def _summary(text: str) -> str | None:
    match = re.search(r"^#{1,3}\s*Summary\s*$(.*?)(?=^#{1,3}\s|\Z)", text, re.M | re.S | re.I)
    body = match.group(1) if match else text.split("\n\n", 1)[0]
    body = body.strip()
    if not body:
        return None
    return body if len(body) <= SUMMARY_MAX else body[: SUMMARY_MAX - 3] + "..."

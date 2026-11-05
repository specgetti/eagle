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
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(event, dict)
                and event.get("type") == "system"
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

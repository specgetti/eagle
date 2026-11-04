import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
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


def _parse_claude(stdout: str) -> AgentOutput:
    data = None
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if data is None:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise AgentOutputError("Could not parse Claude Code JSON output") from exc
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

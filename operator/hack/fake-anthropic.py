"""A tiny fake of the Anthropic Messages API for end to end tests.

Lets the real Claude Code CLI finish a review without a real model or key:
every request gets the same short review back, as JSON or as a server-sent
event stream (what Claude Code uses). Standard library only, so it runs in the
agent runner image.

    python3 fake-anthropic.py [port]     # default 8080
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REVIEW = (
    "## Summary\n"
    "Fake review produced by the Eagle end to end test.\n\n"
    "## Findings\n"
    "No issues found.\n\n"
    "VERDICT: approve"
)
USAGE = {"input_tokens": 12, "output_tokens": 34}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # one short line per request
        sys.stderr.write(f"{self.command} {self.path}\n")

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/v1/models":
            self._json(200, {"data": [{"id": "fake-model", "type": "model"}], "has_more": False})
        elif path in ("/", "/healthz"):
            self._json(200, {"ok": True})
        else:
            self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": path}})

    def do_HEAD(self):  # Claude Code checks connectivity with HEAD /api/hello
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        if path == "/v1/messages/count_tokens":
            self._json(200, {"input_tokens": USAGE["input_tokens"]})
        elif path == "/v1/messages":
            model = body.get("model", "fake-model")
            if body.get("stream"):
                self._stream(model)
            else:
                self._json(200, _message(model, REVIEW))
        else:
            self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": path}})

    def _stream(self, model: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        start = _message(model, "")
        start["usage"] = {"input_tokens": USAGE["input_tokens"], "output_tokens": 1}
        events = [
            ("message_start", {"type": "message_start", "message": start}),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            ),
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": REVIEW}},
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": USAGE["output_tokens"]},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        for name, data in events:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()
        self.close_connection = True


def _message(model: str, text: str) -> dict:
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": "end_turn" if text else None,
        "stop_sequence": None,
        "usage": dict(USAGE),
    }


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()

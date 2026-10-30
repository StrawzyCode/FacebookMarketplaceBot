import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from marketplace_bot.ai import AIError
from marketplace_bot.models import SearchPlan, TriageBatch
from marketplace_bot.ollama_ai import OllamaMatcher, check_ollama, inline_refs

from .conftest import make_listing


class FakeOllama:
    """A tiny stand-in for the Ollama server's HTTP API."""

    def __init__(self, replies=None, models=("gemma3:4b",), capabilities=("completion", "vision")):
        self.replies = list(replies or [])
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body, status=200):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send({"models": [{"name": m} for m in models]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.requests.append((self.path, body))
                if self.path == "/api/show":
                    return self._send({"capabilities": list(capabilities)})
                reply = fake.replies.pop(0)
                if isinstance(reply, int):
                    return self._send({"error": "model crashed"}, status=reply)
                self._send({"message": {"role": "assistant", "content": reply}, "done": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def ollama():
    servers = []

    def start(**kwargs):
        servers.append(FakeOllama(**kwargs))
        return servers[-1]

    yield start
    for s in servers:
        s.close()


def test_check_ollama(ollama):
    assert check_ollama("http://127.0.0.1:9", "gemma3:4b").message == "Ollama isn't running on this computer."

    server = ollama(models=["llama3.1:8b"])
    status = check_ollama(server.url, "gemma3:4b")
    assert not status.ok and "ollama pull gemma3:4b" in status.message

    server = ollama(models=["gemma3:latest"], capabilities=["completion", "vision"])
    status = check_ollama(server.url, "gemma3")
    assert status.ok and status.vision

    server = ollama(models=["llama3.1:8b"], capabilities=["completion"])
    assert check_ollama(server.url, "llama3.1:8b").vision is False


def test_sends_schema_and_photos_and_parses_reply(ollama, plan):
    evaluation = {
        "match_score": 120, "verdict": "perfect", "summary": "s", "matched_requirements": [], "missing_or_unclear": [],
        "red_flags": [], "questions_for_seller": [], "price_assessment": "p",
    }
    server = ollama(replies=["<think>hmm</think>" + json.dumps(evaluation)])
    matcher = OllamaMatcher(server.url, "gemma3:4b", vision=True)

    result = matcher.evaluate("desk", plan, make_listing("1", "Desk", 100, "Austin, TX"), [("image/jpeg", b"abc")])

    assert result.match_score == 100  # clamped
    path, body = server.requests[0]
    assert path == "/api/chat" and body["model"] == "gemma3:4b" and body["stream"] is False
    assert body["format"]["properties"]["verdict"]["enum"] == ["perfect", "strong", "possible", "poor"]
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["images"] == ["YWJj"]


def test_no_photos_for_text_only_models(ollama, plan):
    server = ollama(replies=[json.dumps({"items": [{"id": "1", "score": 7, "reason": "ok"}]})])
    matcher = OllamaMatcher(server.url, "llama3.1:8b", vision=False)
    assert matcher.uses_images is False
    assert matcher.triage("desk", plan, [make_listing("1", "Desk", 100, "Austin, TX")]) == {"1": (7, "ok")}
    assert "images" not in server.requests[0][1]["messages"][1]


def test_retries_once_on_bad_json_then_gives_up(ollama, plan):
    good = plan.model_dump_json()
    server = ollama(replies=["not json", good])
    assert OllamaMatcher(server.url, "m", vision=False).plan("desk", "", None) == plan

    server = ollama(replies=["nope", "still nope"])
    with pytest.raises(AIError, match="wasn't usable"):
        OllamaMatcher(server.url, "m", vision=False).plan("desk", "", None)


def test_server_errors_become_ai_errors(ollama, plan):
    server = ollama(replies=[500])
    with pytest.raises(AIError, match="model crashed"):
        OllamaMatcher(server.url, "m", vision=False).plan("desk", "", None)
    with pytest.raises(AIError, match="Couldn't reach Ollama"):
        OllamaMatcher("http://127.0.0.1:9", "m", vision=False).plan("desk", "", None)


def test_inline_refs_flattens_nested_models():
    schema = inline_refs(TriageBatch.model_json_schema())
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    assert schema["properties"]["items"]["items"]["properties"]["score"]["type"] == "integer"
    assert inline_refs(SearchPlan.model_json_schema())["required"]

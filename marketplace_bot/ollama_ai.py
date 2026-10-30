"""Free option: a language model running on your own computer through Ollama (https://ollama.com).

Nothing is sent to any AI company and there is nothing to pay. It needs a reasonably
modern computer (8 GB+ of memory) and is slower than a hosted AI, especially without
a graphics card.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from .ai import AIError, LLMMatcher

THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


def inline_refs(schema: dict) -> dict:
    """Replace ``$ref``s with the definitions they point to; small local models handle flat schemas best."""
    defs = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


@dataclass
class OllamaStatus:
    ok: bool
    vision: bool = False
    message: str = ""


def _request(base_url: str, path: str, payload: dict | None, timeout: float) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _same_model(wanted: str, installed: str) -> bool:
    if ":" not in wanted:
        wanted += ":latest"
    return installed == wanted


def check_ollama(base_url: str, model: str) -> OllamaStatus:
    """Is Ollama running here, with the model downloaded? And can that model see photos?"""
    try:
        tags = _request(base_url, "/api/tags", None, timeout=3)
    except (urllib.error.URLError, OSError, ValueError):
        return OllamaStatus(False, message="Ollama isn't running on this computer.")
    names = [m.get("name") or m.get("model", "") for m in tags.get("models", [])]
    if not any(_same_model(model, n) for n in names):
        return OllamaStatus(False, message=f"Ollama is running but the model isn't downloaded. Run:  ollama pull {model}")
    vision = False
    try:
        info = _request(base_url, "/api/show", {"model": model}, timeout=10)
        vision = "vision" in (info.get("capabilities") or [])
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return OllamaStatus(True, vision=vision)


class OllamaMatcher(LLMMatcher):
    def __init__(self, base_url: str, model: str, vision: bool, timeout: float = 600):
        self.base_url = base_url
        self.model = model
        self.uses_images = vision
        self.timeout = timeout

    def _post(self, payload: dict) -> dict:
        try:
            return _request(self.base_url, "/api/chat", payload, self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise AIError(f"Ollama returned an error: {detail or exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AIError(f"Couldn't reach Ollama at {self.base_url}: {exc}") from exc

    def _ask(self, output_format: type[BaseModel], system: str, text: str, images, effort: str):
        user: dict = {"role": "user", "content": text + "\n\nReply with JSON only."}
        if images and self.uses_images:
            user["images"] = [base64.b64encode(data).decode() for _, data in images]
        payload = {
            "model": self.model,
            "stream": False,
            "format": inline_refs(output_format.model_json_schema()),
            "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [{"role": "system", "content": system}, user],
        }
        problem = ""
        for _ in range(2):  # small models occasionally produce invalid JSON; one retry usually fixes it
            reply = self._post(payload)
            content = THINK_RE.sub("", (reply.get("message") or {}).get("content", ""))
            try:
                return output_format.model_validate_json(content)
            except ValidationError as exc:
                problem = str(exc).splitlines()[0]
        raise AIError(f"The local model's answer wasn't usable ({problem})")

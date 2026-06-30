"""Provider-agnostic model client.

A single ``complete()`` method hides the provider. We call the HTTP APIs
directly via httpx instead of vendor SDKs to (a) keep dependencies tiny and
(b) make the surface trivial to mock. Add a new provider by implementing one
small subclass and wiring it into ``build_model_client``.
"""

from __future__ import annotations

import abc
import json
import re

import httpx

from ..config import Settings
from ..http_util import request_with_retry
from ..logging_setup import get_logger

log = get_logger(__name__)


class ModelError(RuntimeError):
    pass


class ModelClient(abc.ABC):
    name: str = "base"
    model: str = ""

    @abc.abstractmethod
    def complete(self, *, system: str, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.4, json_mode: bool = False) -> str:
        """Return the model's text completion for the given system + user prompt."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
class OpenAIClient(ModelClient):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini",
                 base_url: str = "https://api.openai.com/v1",
                 client: httpx.Client | None = None):
        if not api_key:
            raise ModelError("OPENAI_API_KEY is required for AI_PROVIDER=openai")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=120.0)

    def complete(self, *, system: str, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.4, json_mode: bool = False) -> str:
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = request_with_retry(
            self._client, "POST", f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
        )
        if resp.status_code >= 400:
            raise ModelError(f"OpenAI {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
class AnthropicClient(ModelClient):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 base_url: str = "https://api.anthropic.com/v1",
                 client: httpx.Client | None = None):
        if not api_key:
            raise ModelError("ANTHROPIC_API_KEY is required for AI_PROVIDER=anthropic")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=120.0)

    def complete(self, *, system: str, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.4, json_mode: bool = False) -> str:
        # Anthropic has no JSON response_format. We steer via the system prompt
        # AND prefill the assistant turn with "{" so the model emits JSON
        # directly (no "Here is the JSON:" preamble or ``` fences). We then
        # re-prepend the primed "{". extract_json remains the safety net.
        sys_text = system
        messages: list[dict] = [{"role": "user", "content": prompt}]
        if json_mode:
            sys_text = (system + "\n\nRespond with a single valid JSON object and nothing else.").strip()
            messages.append({"role": "assistant", "content": "{"})
        resp = request_with_retry(
            self._client, "POST", f"{self.base_url}/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": sys_text,
                "messages": messages,
            },
        )
        if resp.status_code >= 400:
            raise ModelError(f"Anthropic {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        text = "".join(parts)
        # Re-prepend the primed "{" — but guard against a model that echoes its
        # own leading brace, which would otherwise yield invalid "{{...".
        if json_mode and not text.lstrip().startswith("{"):
            text = "{" + text
        return text


# ---------------------------------------------------------------------------
# Mock (offline, deterministic) — lets the whole pipeline run with no API keys.
# ---------------------------------------------------------------------------
class MockClient(ModelClient):
    name = "mock"
    model = "mock-1"

    _REPO_RE = re.compile(r"REPO:\s*([^\n]+)")

    def complete(self, *, system: str, prompt: str, max_tokens: int = 2000,
                 temperature: float = 0.4, json_mode: bool = False) -> str:
        repo = self._match(self._REPO_RE, prompt, "owner/repo")
        name = repo.split("/")[-1]

        if "TASK: ENGAGEMENT REVIEW" in prompt:
            return json.dumps({
                "approved": True,
                "attention_score": 8.2,
                "voice_score": 8.0,
                "severity": "low",
                "issues": [
                    {
                        "type": "hook",
                        "severity": "low",
                        "text": "Opening line",
                        "problem": "Could commit to its angle one beat sooner.",
                        "suggested_fix": "Lead with the most surprising fact.",
                    }
                ],
                "recommended_action": "approve",
                "notes": "Mock engagement review: thesis-first opening, human voice.",
            })

        if "TASK: REVIEWER" in prompt:
            return json.dumps({
                "approved": True,
                "overall_score": 8.4,
                "severity": "low",
                "issues": [
                    {
                        "type": "clarity",
                        "severity": "low",
                        "text": "Intro paragraph",
                        "problem": "Slightly long lead-in.",
                        "suggested_fix": "Tighten the first sentence.",
                    }
                ],
                "recommended_action": "approve",
                "notes": "Mock review: content is grounded in the provided repo facts.",
            })

        # WRITER and REVISER both return a draft-shaped object.
        revised = "TASK: REVISER" in prompt
        body = self._mock_body(repo, name, revised)
        return json.dumps({
            "title": f"{name}: a practical look at what it does and where it fits",
            "summary": (
                f"{name} is getting attention on GitHub. Here's a grounded take on "
                f"what it does, how it might slot into a production stack, and its tradeoffs."
            )[:280],
            "tags": ["opensource", "engineering", name.lower()[:20], "tools"][:4],
            "angle": "practical engineering analysis",
            "body_markdown": body,
        })

    @staticmethod
    def _match(pattern: re.Pattern, text: str, default: str) -> str:
        m = pattern.search(text)
        return m.group(1).strip() if m else default

    @staticmethod
    def _mock_body(repo: str, name: str, revised: bool) -> str:
        note = "\n\n_(Revised per reviewer feedback.)_" if revised else ""
        return (
            f"## What {repo} actually does\n\n"
            f"`{repo}` is an open-source project that has been picking up stars recently. "
            f"Based on its README and metadata, it targets a concrete engineering problem "
            f"rather than a vague vision. The core of {name} is its public API surface and "
            f"the way it composes with existing tooling. Reading through the README, the "
            f"design favors a small set of primitives that compose, which usually ages better "
            f"than a sprawling feature list. The documented examples are concrete enough that "
            f"you can map them onto a real service without much guesswork.\n\n"
            f"## Why engineers are looking at it\n\n"
            f"The traction is driven by a clear use case and reasonable defaults. For teams "
            f"already invested in this part of the stack, {name} is worth a focused evaluation: "
            f"check the dependency footprint, the test coverage, and how active the issue tracker "
            f"is. Star growth alone is a weak signal, but combined with a coherent README and "
            f"recent commits it suggests a project that is being actively shaped rather than "
            f"parked. The maintainers appear responsive, which matters more than raw popularity "
            f"when you are deciding whether to depend on something.\n\n"
            f"## How it might be used in production\n\n"
            f"A pragmatic adoption path is to pilot {name} behind a feature flag on a "
            f"non-critical workload, measure the operational overhead, and compare it to the "
            f"incumbent before committing. Pay attention to how it behaves under failure: what "
            f"happens on timeouts, how it surfaces errors, and whether its configuration surface "
            f"is small enough to reason about. Wire it into your existing observability so you can "
            f"see latency and error rates from day one rather than discovering them in an incident.\n\n"
            f"## Limitations and takeaway\n\n"
            f"Like any young project, {name} carries maintenance risk and potential API churn. "
            f"The README cannot tell you how it performs at your scale, so treat any performance "
            f"expectations as hypotheses to test rather than facts. The honest takeaway: it is "
            f"promising and worth tracking, but validate it against your own constraints and "
            f"failure modes before putting it on a critical path.{note}\n"
        )


def build_model_client(settings: Settings,
                       http_client: httpx.Client | None = None) -> ModelClient:
    """Factory: pick a model client based on configuration."""
    provider = settings.ai_provider
    if provider == "mock":
        return MockClient()
    if provider == "openai":
        return OpenAIClient(settings.openai_api_key, settings.openai_model, client=http_client)
    if provider == "anthropic":
        return AnthropicClient(settings.anthropic_api_key, settings.anthropic_model, client=http_client)
    raise ModelError(f"Unknown AI_PROVIDER: {provider!r} (use mock|openai|anthropic)")

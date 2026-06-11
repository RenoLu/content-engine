import json

import httpx

from content_engine.agents.model_client import AnthropicClient, OpenAIClient


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_anthropic_json_mode_prefills_and_reconstructs_brace():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        # Model continues from the primed "{" — returns the REST of the object.
        return httpx.Response(200, json={"content": [{"type": "text", "text": '"a": 1}'}]})

    c = AnthropicClient("key", client=_client(handler))
    out = c.complete(system="s", prompt="p", json_mode=True)
    assert out == '{"a": 1}'
    assert json.loads(out) == {"a": 1}
    assert captured["body"]["messages"][-1] == {"role": "assistant", "content": "{"}


def test_anthropic_json_mode_does_not_double_brace_when_model_echoes_brace():
    # If the model ignores the prefill and emits its own leading '{', we must
    # not prepend a second one (which would yield invalid "{{...").
    def handler(request):
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"a": 1}'}]})

    c = AnthropicClient("key", client=_client(handler))
    out = c.complete(system="s", prompt="p", json_mode=True)
    assert out == '{"a": 1}'
    assert json.loads(out) == {"a": 1}


def test_anthropic_without_json_mode_has_no_prefill():
    def handler(request):
        body = json.loads(request.content)
        assert body["messages"] == [{"role": "user", "content": "p"}]
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hello"}]})

    c = AnthropicClient("key", client=_client(handler))
    assert c.complete(system="s", prompt="p", json_mode=False) == "hello"


def test_openai_json_mode_sets_response_format():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    c = OpenAIClient("key", client=_client(handler))
    c.complete(system="s", prompt="p", json_mode=True)
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_openai_retries_on_429():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, text="slow down")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    c = OpenAIClient("key", client=_client(handler))
    out = c.complete(system="s", prompt="p")
    assert out == "ok"
    assert calls["n"] == 2

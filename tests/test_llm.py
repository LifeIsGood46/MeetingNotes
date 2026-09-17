"""Tests for the Ollama provider's cloud (OpenAI-compatible) request path.

Uses a stubbed urllib so no real network or key is needed.
"""

import json
from unittest import mock

from meetingnotes.llm.ollama import OllamaProvider


def _stub_response(payload: dict):
    body = json.dumps(payload).encode()
    m = mock.MagicMock()
    m.read.return_value = body
    m.__enter__.return_value = m
    m.status = 200
    return m


def test_cloud_uses_openai_compatible_endpoint_and_auth_header():
    p = OllamaProvider(model="kimi-k3", host="https://ollama.com", api_key="secret")

    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response(
            {"choices": [{"message": {"content": "cleaned text"}}]}
        )
        resp = p.complete("sys", "user")

    req = m_url.call_args.args[0]
    assert req.full_url == "https://ollama.com/v1/chat/completions"
    assert req.headers.get("Authorization") == "Bearer secret"
    assert resp.text == "cleaned text"


def test_local_uses_native_chat_endpoint_no_auth():
    p = OllamaProvider(model="llama3.1", host="http://localhost:11434")

    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response({"message": {"content": "ok"}})
        resp = p.complete("sys", "user")

    req = m_url.call_args.args[0]
    assert req.full_url == "http://localhost:11434/api/chat"
    assert "Authorization" not in req.headers
    assert resp.text == "ok"


def test_cloud_is_available_with_key_only():
    assert OllamaProvider(model="x", api_key="k").is_available() is True


"""Tests for the OpenRouter provider.

Uses stubbed urllib so no real network or key is needed. Verifies the
OpenAI-compatible request shape, free-model filtering, and that HTTP
failures carry machine-readable codes (key_invalid vs transient).
"""

import json
import urllib.error
from unittest import mock

import pytest

from meetingnotes.llm import get_provider
from meetingnotes.llm.base import LLMError
from meetingnotes.llm.openrouter import OpenRouterProvider


def _stub_response(payload: dict):
    body = json.dumps(payload).encode()
    m = mock.MagicMock()
    m.read.return_value = body
    m.__enter__.return_value = m
    m.status = 200
    return m


def _http_error(code: int):
    return urllib.error.HTTPError(
        "https://openrouter.ai/api/v1/chat/completions", code, "err", {}, None
    )


def test_registered_in_get_provider():
    p = get_provider("openrouter")
    assert isinstance(p, OpenRouterProvider)
    assert p.model == "google/gemma-4-31b-it:free"


def test_complete_uses_openrouter_endpoint_and_key():
    p = OpenRouterProvider(model="google/gemma-4-31b-it:free", api_key="sk-test")
    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response(
            {"choices": [{"message": {"content": "cleaned"}}]}
        )
        resp = p.complete("sys", "user")

    req = m_url.call_args.args[0]
    assert req.full_url == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers.get("Authorization") == "Bearer sk-test"
    assert resp.text == "cleaned"


def test_list_models_free_only_filter():
    p = OpenRouterProvider(model="x", api_key="k")
    payload = {"data": [{"id": "a:free"}, {"id": "b"}, {"id": "c:free"}]}
    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response(payload)
        assert p.list_models(free_only=True) == ["a:free", "c:free"]
    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response(payload)
        assert p.list_models() == ["a:free", "b", "c:free"]


def test_401_carries_http_code_for_key_invalid():
    p = OpenRouterProvider(model="x", api_key="bad")
    with mock.patch("urllib.request.urlopen", side_effect=_http_error(401)):
        with pytest.raises(LLMError) as exc:
            p.verify_credentials()
    assert exc.value.http_code == 401


def test_429_carries_http_code_not_key_invalid():
    p = OpenRouterProvider(model="x", api_key="k")
    with mock.patch("urllib.request.urlopen", side_effect=_http_error(429)):
        with pytest.raises(LLMError) as exc:
            p.verify_credentials()
    assert exc.value.http_code == 429
    assert "rate limit" in str(exc.value).lower()


def test_missing_key_fails_before_network():
    p = OpenRouterProvider(model="x", api_key=None)
    with mock.patch("urllib.request.urlopen") as m_url:
        with pytest.raises(LLMError):
            p.verify_credentials()
        m_url.assert_not_called()


"""Tests for the OpenAI-compatible provider (LM Studio, vLLM, llama.cpp…)
and the explicit None provider."""

import json
import urllib.error
from unittest import mock

import pytest

from meetingnotes.llm import get_provider
from meetingnotes.llm.base import LLMError
from meetingnotes.llm.compat import OpenAICompatProvider
from meetingnotes.llm.none import NoneProvider


def _stub_response(payload: dict):
    body = json.dumps(payload).encode()
    m = mock.MagicMock()
    m.read.return_value = body
    m.__enter__.return_value = m
    m.status = 200
    return m


def _http_error(code: int):
    return urllib.error.HTTPError("http://localhost:1234/v1/models", code, "err", {}, None)


# ---------------- provider registration ----------------

def test_none_provider_registered():
    p = get_provider("none")
    assert isinstance(p, NoneProvider)


def test_compat_provider_registered_with_lm_studio_default():
    p = get_provider("compat")
    assert isinstance(p, OpenAICompatProvider)
    assert p.base_url == "http://localhost:1234/v1"


# ---------------- none provider ----------------

def test_none_provider_fails_fast_with_guidance():
    p = get_provider("none")
    with pytest.raises(LLMError) as exc:
        p.complete("sys", "user")
    assert "Settings" in str(exc.value)
    assert "raw" in str(exc.value)


def test_none_provider_not_available():
    assert get_provider("none").is_available() is False


# ---------------- compat: model resolution ----------------

def test_compat_empty_model_resolves_to_first_server_model():
    p = OpenAICompatProvider(model="", base_url="http://localhost:1234/v1")
    with mock.patch.object(p, "list_models", return_value=["llama3", "qwen2.5-7b"]):
        assert p._resolve_model() == "llama3"  # first served model; deterministic


def test_compat_empty_model_and_empty_server_raises_guidance():
    p = OpenAICompatProvider(model="", base_url="http://localhost:1234/v1")
    with mock.patch.object(p, "list_models", return_value=[]):
        with pytest.raises(LLMError) as exc:
            p._resolve_model()
    assert "LM Studio" in str(exc.value)


# ---------------- compat: request shape ----------------

def test_compat_complete_uses_chat_completions_and_optional_auth():
    p = OpenAICompatProvider(model="qwen2.5-7b", base_url="http://localhost:1234/v1")
    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response(
            {"choices": [{"message": {"content": "cleaned"}}]}
        )
        resp = p.complete("sys", "user")

    req = m_url.call_args.args[0]
    assert req.full_url == "http://localhost:1234/v1/chat/completions"
    assert "Authorization" not in req.headers  # no key -> no header
    assert resp.text == "cleaned"
    assert resp.model == "qwen2.5-7b"


def test_compat_with_key_sends_bearer():
    p = OpenAICompatProvider(model="m", base_url="http://box:1234/v1", api_key="sk-x")
    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response({"choices": [{"message": {"content": "ok"}}]})
        p.complete("sys", "user")
    assert m_url.call_args.args[0].headers.get("Authorization") == "Bearer sk-x"


def test_compat_list_models_parses_ids():
    p = OpenAICompatProvider(model="m", base_url="http://localhost:1234/v1")
    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response(
            {"data": [{"id": "qwen2.5-7b"}, {"id": "llama3"}]}
        )
        assert p.list_models() == ["llama3", "qwen2.5-7b"]


def test_compat_unreachable_gives_actionable_error():
    p = OpenAICompatProvider(model="m", base_url="http://localhost:1234/v1")
    with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
        with pytest.raises(LLMError) as exc:
            p.list_models()
    assert "LM Studio" in str(exc.value)


def test_compat_bad_key_carries_401():
    p = OpenAICompatProvider(model="m", base_url="http://localhost:1234/v1", api_key="bad")
    with mock.patch("urllib.request.urlopen", side_effect=_http_error(401)):
        with pytest.raises(LLMError) as exc:
            p.verify_credentials()
    assert exc.value.http_code == 401
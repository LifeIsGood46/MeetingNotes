"""Tests for the OpenAI + Anthropic REST providers (no SDK, pure urllib).

Uses stubbed urllib so no real network or key is needed.
"""

import json
import urllib.error
from unittest import mock

import pytest

from meetingnotes.llm import get_provider
from meetingnotes.llm.anthropic import AnthropicProvider
from meetingnotes.llm.base import LLMError
from meetingnotes.llm.openai import OpenAIProvider


def _stub_response(payload: dict):
    body = json.dumps(payload).encode()
    m = mock.MagicMock()
    m.read.return_value = body
    m.__enter__.return_value = m
    m.status = 200
    return m


def _http_error(code: int):
    return urllib.error.HTTPError("https://example.com/v1/x", code, "err", {}, None)


def test_registered_in_get_provider():
    assert isinstance(get_provider("openai"), OpenAIProvider)
    assert isinstance(get_provider("anthropic"), AnthropicProvider)


def test_openai_complete_shape():
    p = OpenAIProvider(model="gpt-4o", api_key="sk-test")
    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response(
            {"choices": [{"message": {"content": "cleaned"}}]})
        resp = p.complete("sys", "user")
    req = m_url.call_args.args[0]
    assert req.full_url == "https://api.openai.com/v1/chat/completions"
    assert req.headers.get("Authorization") == "Bearer sk-test"
    assert resp.text == "cleaned"


def test_openai_list_filters_to_chat_models():
    p = OpenAIProvider(model="gpt-4o", api_key="sk-test")
    payload = {"data": [{"id": "gpt-4o"}, {"id": "text-embedding-3-small"},
                        {"id": "o1-mini"}, {"id": "whisper-1"}]}
    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response(payload)
        assert p.list_models() == ["gpt-4o", "o1-mini"]


def test_openai_verify_is_key_probe_without_tokens():
    p = OpenAIProvider(model="gpt-4o", api_key="sk-test")
    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response({"data": []})
        p.verify_credentials()
    req = m_url.call_args.args[0]
    assert req.full_url == "https://api.openai.com/v1/models"
    assert req.get_method() == "GET"


def test_openai_401_maps_to_key_invalid():
    p = OpenAIProvider(model="gpt-4o", api_key="bad")
    with mock.patch("urllib.request.urlopen", side_effect=_http_error(401)):
        with pytest.raises(LLMError) as exc:
            p.verify_credentials()
    assert exc.value.http_code == 401
    assert "key" in str(exc.value).lower()


def test_openai_missing_key_fails_before_network():
    p = OpenAIProvider(model="gpt-4o", api_key=None)
    with mock.patch("urllib.request.urlopen") as m_url:
        with pytest.raises(LLMError):
            p.list_models()
        m_url.assert_not_called()


def test_anthropic_complete_shape_and_headers():
    p = AnthropicProvider(model="claude-x", api_key="sk-ant-test")
    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response(
            {"content": [{"type": "text", "text": "cleaned"}]})
        resp = p.complete("sys", "user")
    req = m_url.call_args.args[0]
    assert req.full_url == "https://api.anthropic.com/v1/messages"
    assert req.headers.get("X-api-key") == "sk-ant-test"
    assert req.headers.get("Anthropic-version") == "2023-06-01"
    body = json.loads(m_url.call_args.args[0].data.decode())
    assert body["system"] == "sys" and body["model"] == "claude-x"
    assert resp.text == "cleaned"


def test_anthropic_list_models():
    p = AnthropicProvider(model="claude-x", api_key="sk-ant-test")
    payload = {"data": [{"id": "claude-b"}, {"id": "claude-a"}]}
    with mock.patch("urllib.request.urlopen") as m_url:
        m_url.return_value = _stub_response(payload)
        assert p.list_models() == ["claude-a", "claude-b"]


def test_anthropic_401_maps_to_key_invalid():
    p = AnthropicProvider(model="claude-x", api_key="bad")
    with mock.patch("urllib.request.urlopen", side_effect=_http_error(401)):
        with pytest.raises(LLMError) as exc:
            p.verify_credentials()
    assert exc.value.http_code == 401


def test_browse_folder_returns_picked_dir(tmp_path):
    from fastapi.testclient import TestClient

    from meetingnotes.webapp.main import create_app

    picked = tmp_path / "results"
    picked.mkdir()
    with mock.patch("tkinter.filedialog.askdirectory", return_value=str(picked)):
        r = TestClient(create_app()).post("/api/settings/browse-folder",
                                          json={"current": ""})
    assert r.status_code == 200, r.text
    assert r.json() == {"path": str(picked)}


def test_browse_folder_cancel_returns_none():
    from fastapi.testclient import TestClient

    from meetingnotes.webapp.main import create_app

    with mock.patch("tkinter.filedialog.askdirectory", return_value=""):
        r = TestClient(create_app()).post("/api/settings/browse-folder",
                                          json={"current": ""})
    assert r.status_code == 200, r.text
    assert r.json() == {"path": None}

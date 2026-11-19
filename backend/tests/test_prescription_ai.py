import json
from unittest.mock import Mock

import pytest
import httpx

from app import prescription_ai
from requests import Timeout


@pytest.fixture(autouse=True)
def vertex_provider(monkeypatch):
    monkeypatch.setenv("PRESCRIPTION_AI_PROVIDER", "vertex")


@pytest.mark.parametrize("page", [1, 2])
def test_extraction_uses_regional_structured_request_and_checks_page(monkeypatch, page):
    response = Mock(status_code=200)
    response.json.return_value = {"candidates": [{
        "finishReason": "STOP",
        "content": {"parts": [{"text": json.dumps({"entries": [
            {"kind": "unreadable", "text": "Do not display generated guesses", "page": page},
        ]})}]},
    }]}
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.post.return_value = response
    monkeypatch.setattr(prescription_ai, "AuthorizedSession", Mock(return_value=session))
    monkeypatch.setattr(prescription_ai, "_credentials", Mock())
    if page == 1:
        assert prescription_ai.extract_prescription("test-project", [b"test image"]) == [
            {"kind": "unreadable", "text": "Some prescription text could not be read.", "page": 1},
        ]
    else:
        with pytest.raises(prescription_ai.ExtractionError, match="invalid or incomplete extraction"):
            prescription_ai.extract_prescription("test-project", [b"test image"])
    call = session.post.call_args
    assert call.args == (
        "https://asia-south1-aiplatform.googleapis.com/v1/projects/test-project/locations/asia-south1"
        "/publishers/google/models/gemini-3.5-flash-lite:generateContent",
    )
    assert call.kwargs["headers"]["X-Vertex-AI-LLM-Request-Type"] == "dedicated"
    assert call.kwargs["allow_redirects"] is False
    assert call.kwargs["json"]["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.parametrize("status,retryable", [(400, False), (401, False), (403, False), (404, False),
                                             (408, True), (429, True), (500, True), (503, True)])
def test_provider_failure_is_safe_and_retryable_only_when_appropriate(monkeypatch, status, retryable):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.post.return_value = Mock(status_code=status)
    monkeypatch.setattr(prescription_ai, "AuthorizedSession", Mock(return_value=session))
    monkeypatch.setattr(prescription_ai, "_credentials", Mock())
    with pytest.raises(prescription_ai.ExtractionError) as error:
        prescription_ai.extract_prescription("test-project", [b"synthetic"])
    assert error.value.retryable is retryable
    assert str(error.value) == "provider response"


@pytest.mark.parametrize("content,finish", [
    ("not JSON", "STOP"),
    ('{"entries":[{"kind":"medication","text":"","page":1}]}', "STOP"),
    ('{"entries":[{"kind":"note","text":"sample","page":true}]}', "STOP"),
    ('{"entries":[{"kind":"advice","text":"sample","page":1}]}', "STOP"),
    ('{"entries":[],"unexpected":"value"}', "STOP"),
    ('{"entries":[]}', "MAX_TOKENS"),
    ('{"entries":[]}', "SAFETY"),
])
def test_invalid_or_truncated_provider_output_never_becomes_a_summary(monkeypatch, content, finish):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.post.return_value = Mock(status_code=200)
    session.post.return_value.json.return_value = {
        "candidates": [{"finishReason": finish, "content": {"parts": [{"text": content}]}}],
    }
    monkeypatch.setattr(prescription_ai, "AuthorizedSession", Mock(return_value=session))
    monkeypatch.setattr(prescription_ai, "_credentials", Mock())
    with pytest.raises(prescription_ai.ExtractionError, match="invalid or incomplete extraction"):
        prescription_ai.extract_prescription("test-project", [b"synthetic"])


def test_provider_timeout_retains_only_a_generic_error(monkeypatch):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.post.side_effect = Timeout("request contains private source text")
    monkeypatch.setattr(prescription_ai, "AuthorizedSession", Mock(return_value=session))
    monkeypatch.setattr(prescription_ai, "_credentials", Mock())
    with pytest.raises(prescription_ai.ExtractionError) as error:
        prescription_ai.extract_prescription("test-project", [b"synthetic"])
    assert error.value.retryable
    assert str(error.value) == "provider connection"


def test_gemini_uses_api_key_header_and_validates_source_page(monkeypatch):
    monkeypatch.setenv("PRESCRIPTION_AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "isolated-test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    reached = []

    def handle(request):
        reached.append(request)
        assert request.url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
        assert request.headers["x-goog-api-key"] == "isolated-test-key"
        assert "isolated-test-key" not in str(request.url)
        body = json.loads(request.content)
        assert body["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "image/jpeg"
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": '{"entries":[{"kind":"note","text":"Synthetic excerpt","page":1}]}'},
        ]}}]})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(prescription_ai.httpx, "post", client.post)
        assert prescription_ai.extract_prescription("", [b"sample"]) == [
            {"kind": "note", "text": "Synthetic excerpt", "page": 1},
        ]
    assert len(reached) == 1


@pytest.mark.parametrize("status,retryable", [(400, False), (403, False), (429, True), (503, True)])
def test_gemini_provider_errors(monkeypatch, status, retryable):
    monkeypatch.setenv("PRESCRIPTION_AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "isolated-test-key")
    monkeypatch.setattr(prescription_ai.httpx, "post", lambda *a, **kw: httpx.Response(status))
    with pytest.raises(prescription_ai.ExtractionError) as error:
        prescription_ai.extract_prescription("", [b"sample"])
    assert error.value.retryable is retryable


def test_gemini_missing_key_and_timeout_are_safe(monkeypatch):
    monkeypatch.setenv("PRESCRIPTION_AI_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(prescription_ai.ExtractionError, match="credentials"):
        prescription_ai.extract_prescription("", [b"sample"])
    monkeypatch.setenv("GEMINI_API_KEY", "isolated-test-key")

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("sensitive request details")

    monkeypatch.setattr(prescription_ai.httpx, "post", timeout)
    with pytest.raises(prescription_ai.ExtractionError) as error:
        prescription_ai.extract_prescription("", [b"sample"])
    assert error.value.reason == "provider connection"
    assert error.value.retryable

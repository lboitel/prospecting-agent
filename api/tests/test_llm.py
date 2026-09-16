from types import SimpleNamespace

import pydantic
import pytest

from prospecting.llm.client import LlmRefusal, LlmTruncated, call_structured, check_response
from prospecting.llm.drafting import DraftResult
from prospecting.llm.research import extract_sources
from prospecting.playbook import load_playbook


def citation(url, title):
    return SimpleNamespace(type="url_citation", url=url, title=title)


def message(*contents):
    return SimpleNamespace(type="message", content=list(contents))


def text(*annotations):
    return SimpleNamespace(type="output_text", text="...", annotations=list(annotations))


def search(*urls):
    sources = [SimpleNamespace(type="url", url=u) for u in urls]
    return SimpleNamespace(
        type="web_search_call", action=SimpleNamespace(type="search", sources=sources)
    )


def test_extract_sources_cited_first_then_consulted():
    output = [
        search("https://b.fr", "https://a.fr"),
        SimpleNamespace(type="web_search_call", action=SimpleNamespace(type="open_page")),
        message(text(citation("https://a.fr", "A"), citation("https://a.fr", "A"))),
    ]
    assert extract_sources(output) == [
        {"url": "https://a.fr", "title": "A"},
        {"url": "https://b.fr", "title": None},
    ]


def response(status="completed", reason=None, contents=()):
    return SimpleNamespace(
        status=status,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        output=[message(*contents)] if contents else [],
    )


def test_check_response():
    check_response(response(contents=[text()]))
    with pytest.raises(LlmRefusal):
        check_response(response(contents=[SimpleNamespace(type="refusal", refusal="non")]))
    with pytest.raises(LlmRefusal):
        check_response(response("incomplete", "content_filter"))
    with pytest.raises(LlmTruncated):
        check_response(response("incomplete", "max_output_tokens"))
    with pytest.raises(LlmTruncated):
        check_response(response("failed"))


def test_truncated_structured_output_is_reported():
    def truncated():
        return DraftResult.model_validate_json('{"qualified": true, "sco')

    with pytest.raises(LlmTruncated):
        call_structured(truncated)
    with pytest.raises(pydantic.ValidationError):
        truncated()


def test_structured_schemas_are_strict():
    # Les sorties structurées strictes d'OpenAI exigent additionalProperties=false.
    schema = DraftResult.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_playbook_is_stable_between_calls():
    # Condition nécessaire pour que le cache de prompt fonctionne.
    first = load_playbook()
    assert "offer.md" in first
    assert first == load_playbook()

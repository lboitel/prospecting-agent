from types import SimpleNamespace

import pytest

from prospecting.llm.client import LlmRefusal, LlmTruncated, check_stop_reason
from prospecting.llm.research import extract_sources
from prospecting.playbook import load_playbook


def test_extract_sources_deduplicates_and_skips_errors():
    result = SimpleNamespace(url="https://a.fr", title="A")
    content = [
        SimpleNamespace(type="text", text="..."),
        SimpleNamespace(type="web_search_tool_result", content=[result, result]),
        SimpleNamespace(
            type="web_search_tool_result",
            content=SimpleNamespace(error_code="max_uses_exceeded"),
        ),
    ]
    assert extract_sources(content) == [{"url": "https://a.fr", "title": "A"}]


def test_check_stop_reason():
    check_stop_reason(SimpleNamespace(stop_reason="end_turn"))
    with pytest.raises(LlmRefusal):
        check_stop_reason(
            SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
        )
    with pytest.raises(LlmTruncated):
        check_stop_reason(SimpleNamespace(stop_reason="max_tokens"))


def test_playbook_is_stable_between_calls():
    # Condition nécessaire pour que le cache de prompt fonctionne.
    first = load_playbook()
    assert "offer.md" in first
    assert first == load_playbook()

"""Appels réels du SDK OpenAI contre un faux serveur : forme des requêtes et des réponses."""

import json

import httpx
import openai
import pytest

from prospecting.db import SessionLocal
from prospecting.llm import client as llm_client
from prospecting.llm.client import LlmTruncated
from prospecting.llm.drafting import qualify_and_draft
from prospecting.llm.replies import ReplyCategory, classify_reply
from prospecting.llm.research import research_prospect
from prospecting.models import LlmCall, Prospect, Research

USAGE = {
    "input_tokens": 1200,
    "input_tokens_details": {"cached_tokens": 1024, "cache_write_tokens": 0},
    "output_tokens": 300,
    "output_tokens_details": {"reasoning_tokens": 100},
    "total_tokens": 1500,
}


def api_response(model, output, status="completed", incomplete=None):
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": model,
        "status": status,
        "incomplete_details": incomplete,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": USAGE,
    }


def text_message(text, annotations=()):
    return {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": list(annotations)}],
    }


class FakeOpenAI:
    def __init__(self):
        self.requests: list[dict] = []
        self.reply: dict = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, json=self.reply, headers={"x-request-id": "req_42"})


@pytest.fixture
def fake_openai(monkeypatch):
    fake = FakeOpenAI()
    client = openai.OpenAI(
        api_key="test",
        base_url="https://openai.test/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
    )
    for module in ("research", "drafting", "replies"):
        monkeypatch.setattr(f"prospecting.llm.{module}.get_client", lambda: client)
    monkeypatch.setattr(llm_client, "get_client", lambda: client)
    return fake


@pytest.fixture
def session():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def prospect(session):
    prospect = Prospect(email="marie@acme.fr", first_name="Marie", source="test")
    session.add(prospect)
    session.commit()
    return prospect


def test_research_request_and_sources(fake_openai, session, prospect):
    fake_openai.reply = api_response(
        "gpt-5.4-mini-2026-03-17",
        [
            {
                "type": "web_search_call",
                "id": "ws_1",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": "Acme",
                    "sources": [{"type": "url", "url": "https://b.fr"}],
                },
            },
            text_message(
                "Acme a levé 5 M€.",
                [
                    {
                        "type": "url_citation",
                        "start_index": 0,
                        "end_index": 10,
                        "url": "https://a.fr",
                        "title": "Levée",
                    }
                ],
            ),
        ],
    )

    result = research_prospect(session, prospect)

    sent = fake_openai.requests[0]
    assert sent["model"] == "gpt-5.4-mini"
    assert sent["tools"] == [
        {"type": "web_search", "user_location": {"type": "approximate", "country": "FR"}}
    ]
    assert sent["max_tool_calls"] == 5
    assert sent["include"] == ["web_search_call.action.sources"]
    assert sent["store"] is False
    assert "offer.md" in sent["instructions"]
    assert "Marie" in sent["input"]

    assert result.summary == "Acme a levé 5 M€."
    assert result.sources == [
        {"url": "https://a.fr", "title": "Levée"},
        {"url": "https://b.fr", "title": None},
    ]
    session.flush()
    call = session.query(LlmCall).one()
    assert (call.task, call.model, call.request_id) == (
        "research",
        "gpt-5.4-mini-2026-03-17",
        "req_42",
    )
    assert (call.input_tokens, call.cache_read_tokens, call.stop_reason) == (
        1200,
        1024,
        "completed",
    )


def test_draft_uses_strict_structured_output(fake_openai, session, prospect):
    draft = {
        "qualified": True,
        "score": 140,
        "reasons": ["secteur"],
        "subject": "Bonjour",
        "body": "Texte",
        "sources": ["https://a.fr"],
    }
    fake_openai.reply = api_response("gpt-5.4-mini", [text_message(json.dumps(draft))])
    research = Research(prospect=prospect, summary="note", sources=[], model="m")

    result = qualify_and_draft(session, prospect, research)

    text_format = fake_openai.requests[0]["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["strict"] is True
    assert result.qualified and result.body == "Texte"
    assert result.score == 100  # borné


def test_truncated_draft_raises(fake_openai, session, prospect):
    fake_openai.reply = api_response(
        "gpt-5.4-mini",
        [text_message('{"qualified": true, "sco')],
        status="incomplete",
        incomplete={"reason": "max_output_tokens"},
    )
    research = Research(prospect=prospect, summary="note", sources=[], model="m")
    with pytest.raises(LlmTruncated):
        qualify_and_draft(session, prospect, research)


def test_reply_classification_uses_classifier_model(fake_openai, session):
    fake_openai.reply = api_response(
        "gpt-5.4-nano",
        [
            text_message(
                json.dumps(
                    {"category": "not_now", "summary": "Rappeler", "follow_up_date": "2026-11-02"}
                )
            )
        ],
    )

    result = classify_reply(session, None, "Recontactez-moi en novembre")

    assert fake_openai.requests[0]["model"] == "gpt-5.4-nano"
    assert result.category == ReplyCategory.NOT_NOW
    assert result.follow_up_date == "2026-11-02"

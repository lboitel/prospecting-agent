import os
import tempfile
from pathlib import Path

# Doit précéder tout import de `prospecting` : le moteur SQL est créé à l'import.
_db_dir = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite:///{_db_dir}/test.db",
    API_KEY="test-key",
    OPENAI_API_KEY="unused",
    OPTOUT_SALT="test-salt",
    PLAYBOOK_DIR=str(Path(__file__).parents[2] / "playbook"),
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from prospecting import services  # noqa: E402
from prospecting.db import engine  # noqa: E402
from prospecting.llm.drafting import DraftResult  # noqa: E402
from prospecting.llm.replies import ReplyCategory, ReplyClassification  # noqa: E402
from prospecting.llm.research import ResearchResult  # noqa: E402
from prospecting.main import app  # noqa: E402
from prospecting.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def client():
    return TestClient(app, headers={"X-API-Key": "test-key"})


class FakeLlm:
    """Remplace les trois appels LLM ; les tests règlent les réponses voulues."""

    def __init__(self):
        self.research_calls = 0
        self.draft = DraftResult(
            qualified=True,
            score=80,
            reasons=["secteur cible"],
            subject="Votre expansion à Lyon",
            body="Bonjour Marie, ...",
            sources=["https://example.com/news"],
        )
        self.reply = ReplyClassification(
            category=ReplyCategory.INTERESTED, summary="Veut un appel.", follow_up_date=""
        )
        self.draft_error: Exception | None = None

    def research_prospect(self, session, prospect):
        self.research_calls += 1
        return ResearchResult(
            summary="Levée de fonds en 2026.",
            sources=[{"url": "https://example.com/news", "title": "News"}],
            model="gpt-5.4-mini",
        )

    def qualify_and_draft(self, session, prospect, research):
        if self.draft_error:
            raise self.draft_error
        return self.draft

    def classify_reply(self, session, prospect_id, body):
        return self.reply


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLlm()
    for name in ("research_prospect", "qualify_and_draft", "classify_reply"):
        monkeypatch.setattr(services, name, getattr(fake, name))
    return fake

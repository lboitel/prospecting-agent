from fastapi.testclient import TestClient

from prospecting.llm.client import LlmRefusal
from prospecting.llm.replies import ReplyCategory
from prospecting.main import app

PROSPECT = {
    "email": "Marie.Durand@Acme.fr",
    "first_name": "Marie",
    "last_name": "Durand",
    "company_name": "Acme",
    "company_domain": "www.acme.fr",
    "source": "salon-2026",
}


def create_prospect(client, **overrides) -> dict:
    response = client.post("/prospects", json={**PROSPECT, **overrides})
    assert response.status_code == 200, response.text
    return response.json()


def test_health_is_public():
    assert TestClient(app).get("/health").status_code == 200


def test_api_key_required():
    assert TestClient(app).get("/prospects").status_code == 401
    wrong = TestClient(app, headers={"X-API-Key": "nope"})
    assert wrong.get("/prospects").status_code == 401


def test_upsert_deduplicates_by_normalized_email(client):
    first = create_prospect(client)
    second = create_prospect(client, email="marie.durand@acme.fr", job_title="CTO")
    assert first["id"] == second["id"]
    assert second["email"] == "marie.durand@acme.fr"
    assert second["job_title"] == "CTO"
    assert second["first_name"] == "Marie"


def test_csv_import_reports_each_outcome(client):
    client.post("/opt-outs", json={"email": "stop@acme.fr"})
    csv_text = (
        "email;first_name;company_name\n"
        "a@acme.fr;Anne;Acme\n"
        "stop@acme.fr;Stop;Acme\n"
        "pas-un-email;X;Acme\n"
        "A@acme.fr;Anne-Sophie;Acme\n"
    )
    response = client.post(
        "/prospects/import",
        params={"source": "fichier-test"},
        files={"file": ("prospects.csv", csv_text, "text/csv")},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["created"] == 1
    assert report["updated"] == 1
    assert report["skipped_opted_out"] == 1
    assert len(report["errors"]) == 1 and report["errors"][0].startswith("ligne 4")


def test_full_flow_draft_review_send_reply(client, fake_llm):
    prospect = create_prospect(client)

    processed = client.post(f"/prospects/{prospect['id']}/process").json()
    assert processed["prospect"]["status"] == "draft_ready"
    message_id = processed["draft"]["id"]

    # Traiter deux fois le même prospect est refusé : pas de double facturation.
    assert client.post(f"/prospects/{prospect['id']}/process").status_code == 409
    assert fake_llm.research_calls == 1

    # Envoi impossible sans validation humaine.
    assert client.post(f"/messages/{message_id}/sent", json={"external_id": "x"}).status_code == 409

    approved = client.post(
        f"/messages/{message_id}/approve", json={"reviewer": "leo", "body": "Texte corrigé"}
    ).json()
    assert approved["status"] == "approved" and approved["body"] == "Texte corrigé"

    sent = client.post(f"/messages/{message_id}/sent", json={"external_id": "lemlist-1"})
    assert sent.json()["status"] == "sent"
    replayed = client.post(f"/messages/{message_id}/sent", json={"external_id": "lemlist-1"})
    assert replayed.status_code == 200

    reply = client.post(
        "/replies",
        json={"email": PROSPECT["email"], "body": "Avec plaisir", "external_id": "reply-1"},
    ).json()
    assert reply["notify_human"] is True and reply["stop_sequence"] is True
    status = client.get("/prospects", params={"status": "interested"}).json()
    assert [p["id"] for p in status] == [prospect["id"]]

    # Webhook rejoué : pas de seconde notification.
    again = client.post(
        "/replies",
        json={"email": PROSPECT["email"], "body": "Avec plaisir", "external_id": "reply-1"},
    ).json()
    assert again["notify_human"] is False


def test_disqualified_prospect_gets_no_draft(client, fake_llm):
    fake_llm.draft = fake_llm.draft.model_copy(
        update={"qualified": False, "score": 20, "subject": "", "body": ""}
    )
    prospect = create_prospect(client)
    processed = client.post(f"/prospects/{prospect['id']}/process").json()
    assert processed["draft"] is None
    assert processed["prospect"]["status"] == "disqualified"


def test_failed_draft_resumes_without_new_research(client, fake_llm):
    prospect = create_prospect(client)
    fake_llm.draft_error = LlmRefusal("refus")
    assert client.post(f"/prospects/{prospect['id']}/process").status_code == 502

    fake_llm.draft_error = None
    processed = client.post(f"/prospects/{prospect['id']}/process")
    assert processed.status_code == 200
    assert fake_llm.research_calls == 1


def test_opt_out_reply_blocks_everything(client, fake_llm):
    fake_llm.reply = fake_llm.reply.model_copy(update={"category": ReplyCategory.OPT_OUT})
    prospect = create_prospect(client)
    message_id = client.post(f"/prospects/{prospect['id']}/process").json()["draft"]["id"]

    reply = client.post(
        "/replies",
        json={"email": PROSPECT["email"], "body": "Retirez-moi", "external_id": "reply-2"},
    ).json()
    assert reply["stop_sequence"] is True and reply["notify_human"] is False

    check = client.get("/opt-outs/check", params={"email": "MARIE.durand@acme.fr"}).json()
    assert check["opted_out"] is True
    # Le brouillon en attente est annulé, le contact ne peut plus être réimporté.
    approve = client.post(f"/messages/{message_id}/approve", json={"reviewer": "leo"})
    assert approve.status_code == 409
    assert client.post("/prospects", json=PROSPECT).status_code == 409


def test_out_of_office_keeps_sequence_running(client, fake_llm):
    fake_llm.reply = fake_llm.reply.model_copy(update={"category": ReplyCategory.OUT_OF_OFFICE})
    create_prospect(client)
    reply = client.post(
        "/replies",
        json={"email": PROSPECT["email"], "body": "Absent", "external_id": "reply-3"},
    ).json()
    assert reply["stop_sequence"] is False and reply["notify_human"] is False

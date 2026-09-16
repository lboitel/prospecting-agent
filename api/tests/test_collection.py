"""Collecte : API Recherche d'entreprises et Hunter simulées."""

import httpx
import pytest

from prospecting import hunter, sourcing
from prospecting.db import SessionLocal
from prospecting.models import Company, EmailLookup, Prospect, SourcingCursor
from prospecting.sourcing import SourcingConfig, format_name, select_leaders

CONFIG = SourcingConfig.model_validate(
    {
        "criteria": {"activite_principale": ["62.01Z", "62.02A"], "departement": ["69"]},
        "roles": ["Président", "Gérant"],
        "email": {"daily_lookups": 3, "min_score": 70, "accepted_verifications": ["valid"]},
    }
)


def leader(nom, prenoms, qualite, type_="personne physique"):
    return {"nom": nom, "prenoms": prenoms, "qualite": qualite, "type_dirigeant": type_}


def company(siren, *leaders):
    return {
        "siren": siren,
        "nom_raison_sociale": f"SOCIETE {siren}",
        "activite_principale": "62.01Z",
        "tranche_effectif_salarie": "11",
        "siege": {"libelle_commune": "LYON"},
        "dirigeants": list(leaders),
    }


def json_response(status, payload):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "https://x"))


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setattr(sourcing, "load_sourcing_config", lambda: CONFIG)
    monkeypatch.setattr(hunter, "_api_key", lambda: "test")
    monkeypatch.setattr(hunter.time, "sleep", lambda _: None)
    monkeypatch.setattr("prospecting.collection.time.sleep", lambda _: None)


@pytest.fixture
def search_api(monkeypatch):
    pages = {
        1: [
            company("100000001", leader("DUPONT", "MARIE ANNE", "Président de SAS")),
            company("100000002", leader("X", "Y", "Commissaire aux comptes")),
            company("100000003", leader("MARTIN", "JEAN-PIERRE", "Gérant")),
        ],
        2: [company("100000004", leader("DURAND", "LUC", "Président"))],
    }
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params)
        return json_response(200, {"results": pages[params["page"]], "total_pages": 2})

    monkeypatch.setattr(sourcing, "_get", fake_get)
    return calls


class FakeHunter:
    def __init__(self):
        self.status = 200
        self.data = {
            "email": "Marie.Dupont@Societe.fr",
            "score": 92,
            "domain": "www.societe.fr",
            "verification": {"status": "valid"},
        }
        self.calls = []

    def __call__(self, url, params, headers, timeout):
        self.calls.append(params)
        return json_response(self.status, {"data": self.data})


@pytest.fixture
def hunter_api(monkeypatch):
    fake = FakeHunter()
    monkeypatch.setattr(hunter, "_get", fake)
    return fake


def test_select_leaders_by_role_preference():
    result = company(
        "1",
        leader("HOLDING", "", "Président", "personne morale"),
        leader("DE LA TOUR", "JEAN-PIERRE PAUL", "Gérant"),
        leader("DURAND", "LUC", "Président du conseil"),
    )
    leaders = select_leaders(result, CONFIG)
    assert [(x.first_name, x.last_name) for x in leaders] == [("Luc", "Durand")]
    assert format_name("DE LA TOUR") == "De La Tour"
    assert format_name("jean-PIERRE") == "Jean-Pierre"


def test_search_params():
    assert CONFIG.search_params() == {
        "activite_principale": "62.01Z,62.02A",
        "departement": "69",
        "etat_administratif": "A",
        "est_entrepreneur_individuel": "false",
    }


def test_sourcing_resumes_where_it_stopped(client, search_api):
    first = client.post("/sourcing/run", params={"max_companies": 1}).json()
    assert first == {
        "companies_created": 1,
        "prospects_created": 1,
        "pages_read": 1,
        "exhausted": False,
    }

    second = client.post("/sourcing/run", params={"max_companies": 10}).json()
    # Page 1 relue (entreprise déjà créée ignorée, sans dirigeant ciblé ignorée), puis page 2.
    assert second["companies_created"] == 2
    assert second["pages_read"] == 2
    assert second["exhausted"] is True
    assert [c["page"] for c in search_api] == [1, 1, 2]

    third = client.post("/sourcing/run").json()
    assert third["pages_read"] == 0 and third["exhausted"] is True

    listed = client.get("/prospects", params={"status": "to_enrich"}).json()
    assert [(p["first_name"], p["job_title"]) for p in listed] == [
        ("Marie", "Président de SAS"),
        ("Jean-Pierre", "Gérant"),
        ("Luc", "Président"),
    ]
    with SessionLocal() as session:
        assert session.query(Company).count() == 3
        assert session.query(SourcingCursor).one().next_page == 3


def sourced_prospect(client, search_api) -> int:
    client.post("/sourcing/run", params={"max_companies": 1})
    return client.get("/prospects", params={"status": "to_enrich"}).json()[0]["id"]


def test_found_email_makes_prospect_ready(client, search_api, hunter_api):
    prospect_id = sourced_prospect(client, search_api)

    result = client.post(f"/prospects/{prospect_id}/find-email").json()

    assert result["outcome"] == "found"
    assert result["prospect"]["status"] == "new"
    assert result["prospect"]["email"] == "marie.dupont@societe.fr"
    assert hunter_api.calls == [
        {"first_name": "Marie", "last_name": "Dupont", "company": "SOCIETE 100000001"}
    ]
    with SessionLocal() as session:
        prospect = session.get(Prospect, prospect_id)
        assert prospect.email_source == "hunter"
        assert prospect.company.domain == "societe.fr"
        assert session.query(EmailLookup).one().credit_used is True
    # Déjà enrichi : pas de second appel payant.
    assert client.post(f"/prospects/{prospect_id}/find-email").status_code == 409


@pytest.mark.parametrize(
    ("data", "outcome", "credit"),
    [
        ({"email": None, "score": None}, "not_found", False),
        ({"email": "a@b.fr", "score": 95, "verification": {"status": "accept_all"}}, "rejected", 1),
        ({"email": "a@b.fr", "score": 40, "verification": {"status": "valid"}}, "rejected", 1),
    ],
)
def test_unreliable_or_missing_email(client, search_api, hunter_api, data, outcome, credit):
    prospect_id = sourced_prospect(client, search_api)
    hunter_api.data = data

    result = client.post(f"/prospects/{prospect_id}/find-email").json()

    assert result["outcome"] == outcome
    assert result["prospect"]["status"] == "no_email"
    assert result["prospect"]["email"] is None
    today = client.get("/sourcing/today").json()
    assert today["credits_used"] == int(credit)
    assert today["lookups"] == {outcome: 1}


def test_duplicate_email_keeps_existing_prospect(client, search_api, hunter_api):
    client.post("/prospects", json={"email": "marie.dupont@societe.fr", "source": "salon"})
    prospect_id = sourced_prospect(client, search_api)

    result = client.post(f"/prospects/{prospect_id}/find-email").json()

    assert result["outcome"] == "duplicate"
    assert result["prospect"] is None
    emails = [p["email"] for p in client.get("/prospects").json()]
    assert emails == ["marie.dupont@societe.fr"]


def test_opted_out_email_is_not_used(client, search_api, hunter_api):
    client.post("/opt-outs", json={"email": "marie.dupont@societe.fr"})
    prospect_id = sourced_prospect(client, search_api)

    result = client.post(f"/prospects/{prospect_id}/find-email").json()

    assert result["outcome"] == "opted_out"
    assert result["prospect"]["status"] == "opted_out"
    assert result["prospect"]["email"] is None


def test_hunter_451_erases_contact(client, search_api, hunter_api):
    prospect_id = sourced_prospect(client, search_api)
    hunter_api.status = 451

    result = client.post(f"/prospects/{prospect_id}/find-email").json()

    assert result["outcome"] == "opted_out" and result["prospect"] is None
    assert client.get("/prospects").json() == []
    # L'entreprise reste connue : la collecte ne recrée pas le contact.
    client.post("/sourcing/run", params={"max_companies": 1})
    assert [p["first_name"] for p in client.get("/prospects").json()] == ["Jean-Pierre"]


def test_daily_limit_stops_lookups(client, search_api, hunter_api):
    client.post("/sourcing/run", params={"max_companies": 10})
    hunter_api.data = {"email": None}
    ids = [p["id"] for p in client.get("/prospects", params={"status": "to_enrich"}).json()]

    codes = [client.post(f"/prospects/{i}/find-email").status_code for i in ids]
    assert codes == [200, 200, 200]

    client.post("/prospects", json={"email": "x@y.fr", "source": "s"})
    with SessionLocal() as session:
        extra = Prospect(first_name="A", last_name="B", source="s", status="to_enrich")
        session.add(extra)
        session.commit()
        extra_id = extra.id
    blocked = client.post(f"/prospects/{extra_id}/find-email")
    assert blocked.status_code == 429
    assert "plafond" in blocked.json()["detail"]
    assert len(hunter_api.calls) == 3
    assert client.get("/sourcing/today").json()["lookups_left"] == 0


@pytest.mark.parametrize(("status", "expected"), [(429, 429), (403, 503), (500, 503), (401, 502)])
def test_hunter_errors(client, search_api, hunter_api, status, expected):
    prospect_id = sourced_prospect(client, search_api)
    hunter_api.status = status

    response = client.post(f"/prospects/{prospect_id}/find-email")

    assert response.status_code == expected
    if status == 403:
        assert len(hunter_api.calls) == hunter.RATE_LIMIT_RETRIES + 1
    # Le prospect reste à enrichir pour une prochaine tentative.
    listed = client.get("/prospects", params={"status": "to_enrich"}).json()
    assert [p["id"] for p in listed] == [prospect_id]

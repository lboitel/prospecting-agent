"""Enrichissement entreprise via l'API publique Recherche d'entreprises (data.gouv, sans clé)."""

import httpx

SEARCH_URL = "https://recherche-entreprises.api.gouv.fr/search"


def find_company(query: str) -> dict | None:
    """Cherche une entreprise par SIREN ou par nom. Renvoie None si rien de fiable."""
    response = httpx.get(SEARCH_URL, params={"q": query, "per_page": 1}, timeout=10)
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        return None
    company = results[0]
    siege = company.get("siege") or {}
    return {
        "siren": company.get("siren"),
        "name": company.get("nom_complet"),
        "sector": company.get("activite_principale"),
        "headcount": company.get("tranche_effectif_salarie"),
        "city": siege.get("libelle_commune"),
    }

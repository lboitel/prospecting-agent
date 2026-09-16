"""Recherche d'entreprises cibles et de leurs dirigeants (API Recherche d'entreprises).

Les critères sont dans playbook/sourcing.json, éditable sans redéployer.
"""

import hashlib
import json
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, Field

from prospecting.config import get_settings

SEARCH_URL = "https://recherche-entreprises.api.gouv.fr/search"
# Limite de l'API : 25 résultats par page.
PER_PAGE = 25


class SearchCriteria(BaseModel):
    """Filtres de l'API Recherche d'entreprises (valeurs multiples acceptées)."""

    model_config = ConfigDict(extra="forbid")

    activite_principale: list[str] = []
    section_activite_principale: list[str] = []
    tranche_effectif_salarie: list[str] = []
    categorie_entreprise: list[str] = []
    departement: list[str] = []
    region: list[str] = []
    code_postal: list[str] = []
    nature_juridique: list[str] = []


class EmailRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Recherches Hunter par jour (1 crédit par email trouvé ; 50 crédits/mois en gratuit).
    daily_lookups: int = Field(default=2, ge=0)
    min_score: int = Field(default=70, ge=0, le=100)
    # « valid » seul par défaut : « accept_all » et « unknown » font plus de rebonds.
    accepted_verifications: list[str] = ["valid"]


class SourcingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commentaire: str = ""
    criteria: SearchCriteria
    exclude_sole_proprietors: bool = True
    # Fonctions recherchées, par ordre de préférence (comparaison sans casse, « contient »).
    roles: list[str] = ["Président", "Directeur général", "Gérant"]
    contacts_per_company: int = Field(default=1, ge=1, le=5)
    email: EmailRules = EmailRules()

    def search_params(self) -> dict[str, str]:
        params = {
            name: ",".join(values) for name, values in self.criteria.model_dump().items() if values
        }
        params["etat_administratif"] = "A"
        if self.exclude_sole_proprietors:
            params["est_entrepreneur_individuel"] = "false"
        return params

    def criteria_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.search_params(), sort_keys=True).encode()).hexdigest()


def load_sourcing_config() -> SourcingConfig:
    path = get_settings().playbook_dir / "sourcing.json"
    return SourcingConfig.model_validate_json(path.read_text())


def _get(url: str, **kwargs) -> httpx.Response:
    return httpx.get(url, **kwargs)


def search_companies(params: dict[str, str], page: int) -> dict:
    response = _get(
        SEARCH_URL,
        params={
            **params,
            "page": page,
            "per_page": PER_PAGE,
            "minimal": "true",
            "include": "dirigeants,siege",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


@dataclass
class Leader:
    first_name: str
    last_name: str
    role: str


def format_name(value: str) -> str:
    """« JEAN-PIERRE » → « Jean-Pierre », « DE LA TOUR » → « De La Tour »."""
    return " ".join(
        "-".join(part.capitalize() for part in word.split("-")) for word in value.split()
    )


def select_leaders(company: dict, config: SourcingConfig) -> list[Leader]:
    """Dirigeants personnes physiques dont la fonction est recherchée, par préférence."""
    candidates = []
    for leader in company.get("dirigeants") or []:
        if leader.get("type_dirigeant") != "personne physique":
            continue
        role = leader.get("qualite") or ""
        first_names = (leader.get("prenoms") or "").split()
        if not first_names or not leader.get("nom"):
            continue
        rank = next(
            (i for i, wanted in enumerate(config.roles) if wanted.casefold() in role.casefold()),
            None,
        )
        if rank is None:
            continue
        candidates.append(
            (rank, Leader(format_name(first_names[0]), format_name(leader["nom"]), role))
        )
    candidates.sort(key=lambda c: c[0])
    return [leader for _, leader in candidates[: config.contacts_per_company]]

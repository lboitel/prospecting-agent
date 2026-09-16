"""Client Hunter (Email Finder). Un crédit n'est consommé que si un email est trouvé."""

import time
from dataclasses import dataclass

import httpx

from prospecting.config import get_settings

BASE_URL = "https://api.hunter.io/v2"
RATE_LIMIT_RETRIES = 3


class HunterError(Exception):
    """Erreur non récupérable (clé invalide, paramètres refusés...)."""


class HunterQuotaExceeded(HunterError):
    pass


class HunterUnavailable(HunterError):
    pass


class HunterProcessingRefused(HunterError):
    """HTTP 451 : la personne a demandé à Hunter de ne plus traiter ses données."""


@dataclass
class HunterResult:
    email: str | None
    score: int | None
    verification: str | None
    domain: str | None


def _get(url: str, **kwargs) -> httpx.Response:
    return httpx.get(url, **kwargs)


def _api_key() -> str:
    key = get_settings().hunter_api_key
    if key is None or not key.get_secret_value():
        raise HunterUnavailable("HUNTER_API_KEY non configurée")
    return key.get_secret_value()


def find_email(
    first_name: str, last_name: str, *, domain: str | None, company: str | None
) -> HunterResult:
    params = {"first_name": first_name, "last_name": last_name}
    if domain:
        params["domain"] = domain
    elif company:
        params["company"] = company
    else:
        raise HunterError("domaine ou nom d'entreprise requis")

    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            response = _get(
                f"{BASE_URL}/email-finder",
                params=params,
                headers={"X-API-KEY": _api_key()},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise HunterUnavailable(f"Hunter injoignable : {exc}") from exc
        # 403 = limite de débit par seconde/minute chez Hunter.
        if response.status_code != 403 or attempt == RATE_LIMIT_RETRIES:
            break
        time.sleep(2**attempt)

    status = response.status_code
    if status == 404:
        return HunterResult(None, None, None, None)
    if status == 429:
        raise HunterQuotaExceeded("quota mensuel Hunter épuisé")
    if status == 451:
        raise HunterProcessingRefused("la personne a demandé l'arrêt du traitement")
    if status == 403 or status >= 500:
        raise HunterUnavailable(f"Hunter indisponible (HTTP {status})")
    if status != 200:
        raise HunterError(f"requête refusée par Hunter (HTTP {status}) : {_details(response)}")

    data = response.json().get("data") or {}
    return HunterResult(
        email=data.get("email"),
        score=data.get("score"),
        verification=(data.get("verification") or {}).get("status"),
        domain=data.get("domain"),
    )


def _details(response: httpx.Response) -> str:
    try:
        errors = response.json().get("errors") or []
        return "; ".join(e.get("details", "") for e in errors) or response.text[:200]
    except ValueError:
        return response.text[:200]

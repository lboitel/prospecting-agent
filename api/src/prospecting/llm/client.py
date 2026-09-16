import time
from functools import lru_cache

import anthropic
from sqlalchemy.orm import Session

from prospecting.config import get_settings
from prospecting.models import LlmCall

# Sur refus des classifieurs de sécurité d'Opus 5, l'API relance la même requête
# sur le modèle de repli recommandé par Anthropic (routage selon la catégorie).
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LlmRefusal(Exception):
    pass


class LlmTruncated(Exception):
    pass


# Erreurs après lesquelles le journal llm_calls doit quand même être enregistré.
LLM_ERRORS = (LlmRefusal, LlmTruncated, anthropic.APIError)


@lru_cache
def get_client() -> anthropic.Anthropic:
    settings = get_settings()
    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        max_retries=4,
        timeout=300,
    )


def check_stop_reason(response) -> None:
    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        raise LlmRefusal(f"refus du modèle (catégorie : {category})")
    if response.stop_reason == "max_tokens":
        raise LlmTruncated("réponse tronquée (max_tokens atteint)")


def record_usage(
    session: Session, task: str, prospect_id: int | None, response, started: float
) -> None:
    """Enregistre tokens et latence d'un appel dans llm_calls (sans le contenu)."""
    usage = response.usage
    session.add(
        LlmCall(
            prospect_id=prospect_id,
            task=task,
            model=response.model,
            request_id=response._request_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
            duration_ms=int((time.monotonic() - started) * 1000),
            stop_reason=response.stop_reason,
        )
    )

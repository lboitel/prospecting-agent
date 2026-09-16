import time
from collections.abc import Callable
from functools import lru_cache

import openai
import pydantic
from sqlalchemy.orm import Session

from prospecting.config import get_settings
from prospecting.models import LlmCall


class LlmRefusal(Exception):
    pass


class LlmTruncated(Exception):
    pass


# Erreurs après lesquelles le journal llm_calls doit quand même être enregistré.
LLM_ERRORS = (LlmRefusal, LlmTruncated, openai.APIError)


@lru_cache
def get_client() -> openai.OpenAI:
    settings = get_settings()
    return openai.OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        max_retries=4,
        timeout=300,
    )


def call_structured[T](call: Callable[[], T]) -> T:
    """Exécute un `responses.parse`.

    Le SDK valide le JSON pendant l'appel : une réponse coupée par `max_output_tokens`
    lève une ValidationError avant que l'on puisse lire son statut.
    """
    try:
        return call()
    except pydantic.ValidationError as exc:
        raise LlmTruncated("réponse structurée invalide ou tronquée") from exc


def check_response(response) -> None:
    for item in response.output:
        if item.type == "message":
            for content in item.content:
                if content.type == "refusal":
                    raise LlmRefusal(f"refus du modèle : {content.refusal}")
    if response.status == "incomplete":
        reason = response.incomplete_details.reason if response.incomplete_details else None
        if reason == "content_filter":
            raise LlmRefusal("réponse bloquée par le filtre de contenu")
        raise LlmTruncated(f"réponse incomplète ({reason})")
    if response.status != "completed":
        raise LlmTruncated(f"réponse non terminée (statut {response.status})")


def record_usage(
    session: Session, task: str, prospect_id: int | None, response, started: float
) -> None:
    """Enregistre tokens et latence d'un appel dans llm_calls (sans le contenu)."""
    usage = response.usage
    details = usage.input_tokens_details
    session.add(
        LlmCall(
            prospect_id=prospect_id,
            task=task,
            model=response.model,
            request_id=response._request_id,
            # Chez OpenAI, input_tokens inclut les tokens lus en cache.
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=details.cached_tokens or 0,
            cache_write_tokens=getattr(details, "cache_write_tokens", 0) or 0,
            duration_ms=int((time.monotonic() - started) * 1000),
            stop_reason=response.status,
        )
    )

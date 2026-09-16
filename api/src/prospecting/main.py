import logging

import httpx
import openai
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from prospecting import hunter
from prospecting.auth import require_api_key
from prospecting.collection import DailyLimitReached
from prospecting.llm.client import LlmRefusal, LlmTruncated
from prospecting.routers import messages, opt_outs, prospects, replies, sourcing
from prospecting.services import Conflict, OptedOut

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Prospecting agent API", version="0.1.0")

for router in (
    prospects.router,
    messages.router,
    replies.router,
    opt_outs.router,
    sourcing.router,
):
    app.include_router(router, dependencies=[Depends(require_api_key)])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.exception_handler(Conflict)
def conflict_handler(_: Request, exc: Conflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(OptedOut)
def opted_out_handler(_: Request, exc: OptedOut) -> JSONResponse:
    # Ne pas renvoyer l'email : la réponse peut finir dans les logs n8n.
    return JSONResponse(status_code=409, content={"detail": "contact dans la liste d'opposition"})


@app.exception_handler(LlmRefusal)
@app.exception_handler(LlmTruncated)
def llm_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(openai.APIError)
def openai_error_handler(_: Request, exc: openai.APIError) -> JSONResponse:
    # Le SDK a déjà réessayé les erreurs temporaires ; n8n peut relancer plus tard.
    logging.getLogger(__name__).exception("erreur API OpenAI")
    return JSONResponse(status_code=503, content={"detail": "service LLM indisponible"})


@app.exception_handler(DailyLimitReached)
@app.exception_handler(hunter.HunterQuotaExceeded)
def quota_handler(_: Request, exc: Exception) -> JSONResponse:
    # n8n arrête la boucle d'enrichissement sur un 429.
    return JSONResponse(status_code=429, content={"detail": str(exc)})


@app.exception_handler(hunter.HunterUnavailable)
@app.exception_handler(httpx.HTTPError)
def upstream_unavailable_handler(_: Request, exc: Exception) -> JSONResponse:
    logging.getLogger(__name__).warning("service externe indisponible : %s", exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(hunter.HunterError)
def hunter_error_handler(_: Request, exc: hunter.HunterError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})

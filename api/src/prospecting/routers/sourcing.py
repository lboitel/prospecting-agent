from fastapi import APIRouter, Query

from prospecting.collection import run_sourcing, sourcing_today
from prospecting.db import SessionDep
from prospecting.schemas import SourcingReport, SourcingToday

router = APIRouter(prefix="/sourcing", tags=["sourcing"])


@router.post("/run", response_model=SourcingReport)
def run(session: SessionDep, max_companies: int = Query(default=10, ge=1, le=200)):
    """Ajoute des entreprises cibles et leurs dirigeants (statut `to_enrich`)."""
    return run_sourcing(session, max_companies)


@router.get("/today", response_model=SourcingToday)
def today(session: SessionDep):
    return sourcing_today(session)

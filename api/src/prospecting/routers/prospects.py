import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import select

from prospecting.db import SessionDep
from prospecting.enrichment import find_company
from prospecting.llm.client import LLM_ERRORS
from prospecting.models import Prospect, ProspectStatus
from prospecting.schemas import ImportReport, MessageOut, ProcessResult, ProspectIn, ProspectOut
from prospecting.services import OptedOut, process_prospect, upsert_prospect

router = APIRouter(prefix="/prospects", tags=["prospects"])


def get_prospect(prospect_id: int, session: SessionDep) -> Prospect:
    prospect = session.get(Prospect, prospect_id)
    if prospect is None:
        raise HTTPException(404, "prospect introuvable")
    return prospect


ProspectDep = Annotated[Prospect, Depends(get_prospect)]


@router.post("", response_model=ProspectOut)
def create_or_update(data: ProspectIn, session: SessionDep) -> Prospect:
    prospect, _ = upsert_prospect(session, data)
    session.commit()
    return prospect


@router.post("/import", response_model=ImportReport)
def import_csv(file: UploadFile, source: str, session: SessionDep):
    """Import CSV (séparateur `,` ou `;`) avec des colonnes nommées comme ProspectIn."""
    text = file.file.read().decode("utf-8-sig")
    if not text.strip():
        raise HTTPException(422, "fichier vide")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;")
    report = ImportReport(created=0, updated=0, skipped_opted_out=0, errors=[])
    for line_number, row in enumerate(csv.DictReader(io.StringIO(text), dialect=dialect), 2):
        values = {k.strip(): v.strip() for k, v in row.items() if k and v and v.strip()}
        try:
            data = ProspectIn(source=source, **values)
            _, created = upsert_prospect(session, data)
            session.flush()
        except ValidationError as exc:
            report.errors.append(f"ligne {line_number} : {exc.errors()[0]['msg']}")
            continue
        except OptedOut:
            report.skipped_opted_out += 1
            continue
        if created:
            report.created += 1
        else:
            report.updated += 1
    session.commit()
    return report


@router.get("", response_model=list[ProspectOut])
def list_prospects(
    session: SessionDep,
    status: ProspectStatus | None = None,
    limit: int = 50,
):
    query = select(Prospect).order_by(Prospect.id).limit(min(limit, 500))
    if status:
        query = query.where(Prospect.status == status)
    return session.scalars(query).all()


@router.post("/{prospect_id}/enrich", response_model=ProspectOut)
def enrich(prospect: ProspectDep, session: SessionDep):
    company = prospect.company
    if company is None:
        raise HTTPException(422, "aucune entreprise associée")
    found = find_company(company.siren or company.name)
    if found:
        for field, value in found.items():
            if value and not getattr(company, field):
                setattr(company, field, value)
        session.commit()
    return prospect


@router.post("/{prospect_id}/process", response_model=ProcessResult)
def process(prospect: ProspectDep, session: SessionDep):
    """Recherche web + qualification + brouillon. Appel long (jusqu'à quelques minutes)."""
    try:
        research, draft, message = process_prospect(session, prospect)
    except LLM_ERRORS:
        # Conserve le journal des appels LLM déjà facturés.
        session.commit()
        raise
    return ProcessResult(
        prospect=ProspectOut.model_validate(prospect),
        qualified=draft.qualified,
        score=draft.score,
        reasons=draft.reasons,
        research_summary=research.summary,
        sources=draft.sources,
        draft=MessageOut.model_validate(message) if message else None,
    )

"""Collecte automatique : entreprises cibles → dirigeants → email (Hunter)."""

import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prospecting import hunter, sourcing
from prospecting.models import (
    Company,
    EmailLookup,
    EmailLookupOutcome,
    Prospect,
    ProspectStatus,
    SourcingCursor,
)
from prospecting.schemas import FindEmailResult, ProspectOut, SourcingReport, SourcingToday
from prospecting.services import (
    Conflict,
    find_prospect_by_email,
    is_opted_out,
    normalize_email,
)

SOURCE = "recherche-entreprises (dirigeants RNE)"
# Borne la durée d'un appel : au plus 20 pages × 25 entreprises par exécution.
MAX_PAGES_PER_RUN = 20
# L'API Recherche d'entreprises accepte 7 requêtes par seconde.
PAUSE_BETWEEN_PAGES = 0.2
TIMEZONE = ZoneInfo("Europe/Paris")


class DailyLimitReached(Exception):
    pass


def run_sourcing(session: Session, max_companies: int) -> SourcingReport:
    config = sourcing.load_sourcing_config()
    params = config.search_params()
    criteria_hash = config.criteria_hash()
    cursor = session.scalar(
        select(SourcingCursor).where(SourcingCursor.criteria_hash == criteria_hash)
    )
    if cursor is None:
        # Nouveaux critères : on repart de la première page.
        cursor = SourcingCursor(criteria_hash=criteria_hash, criteria=params, next_page=1)
        session.add(cursor)
        session.flush()

    report = SourcingReport(companies_created=0, prospects_created=0, pages_read=0, exhausted=False)
    while (
        report.companies_created < max_companies
        and report.pages_read < MAX_PAGES_PER_RUN
        and not cursor.exhausted
    ):
        if report.pages_read:
            time.sleep(PAUSE_BETWEEN_PAGES)
        data = sourcing.search_companies(params, cursor.next_page)
        report.pages_read += 1

        page_complete = True
        for result in data.get("results") or []:
            if report.companies_created >= max_companies:
                # Page relue au prochain passage ; les entreprises déjà créées seront ignorées.
                page_complete = False
                break
            created = import_company(session, result, config)
            if created:
                report.companies_created += 1
                report.prospects_created += created

        if page_complete:
            cursor.next_page += 1
            cursor.exhausted = cursor.next_page > (data.get("total_pages") or 0)
        session.commit()

    report.exhausted = cursor.exhausted
    return report


def import_company(session: Session, result: dict, config: sourcing.SourcingConfig) -> int:
    """Crée l'entreprise et ses dirigeants ciblés. Renvoie le nombre de prospects créés."""
    siren = result.get("siren")
    if not siren or session.scalar(select(Company.id).where(Company.siren == siren)):
        return 0
    leaders = sourcing.select_leaders(result, config)
    if not leaders:
        return 0

    siege = result.get("siege") or {}
    company = Company(
        siren=siren,
        name=result.get("nom_raison_sociale") or result.get("nom_complet") or siren,
        sector=result.get("activite_principale"),
        headcount=result.get("tranche_effectif_salarie"),
        city=siege.get("libelle_commune"),
    )
    session.add(company)
    for leader in leaders:
        session.add(
            Prospect(
                company=company,
                first_name=leader.first_name,
                last_name=leader.last_name,
                job_title=leader.role,
                source=SOURCE,
                status=ProspectStatus.TO_ENRICH,
            )
        )
    return len(leaders)


def start_of_day() -> datetime:
    midnight = datetime.now(TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(UTC)


def lookups_today(session: Session) -> int:
    return session.scalar(
        select(func.count(EmailLookup.id)).where(EmailLookup.created_at >= start_of_day())
    )


def find_prospect_email(session: Session, prospect: Prospect) -> FindEmailResult:
    if prospect.status != ProspectStatus.TO_ENRICH:
        raise Conflict(f"prospect {prospect.id} n'est pas à enrichir ({prospect.status})")
    rules = sourcing.load_sourcing_config().email
    if lookups_today(session) >= rules.daily_lookups:
        raise DailyLimitReached(f"plafond de {rules.daily_lookups} recherches/jour atteint")

    company = prospect.company
    lookup = EmailLookup(prospect_id=prospect.id, provider="hunter", credit_used=False)
    try:
        found = hunter.find_email(
            prospect.first_name,
            prospect.last_name,
            domain=company.domain if company else None,
            company=company.name if company else None,
        )
    except hunter.HunterProcessingRefused:
        # La personne s'est opposée au traitement : on efface le contact.
        # L'entreprise reste connue, donc la collecte ne le recréera pas.
        lookup.outcome = EmailLookupOutcome.OPTED_OUT
        lookup.prospect_id = None
        session.delete(prospect)
        return _save(session, lookup, None)

    lookup.score = found.score
    lookup.verification = found.verification
    if not found.email:
        lookup.outcome = EmailLookupOutcome.NOT_FOUND
        prospect.status = ProspectStatus.NO_EMAIL
        return _save(session, lookup, prospect)

    lookup.credit_used = True
    email = normalize_email(found.email)
    if is_opted_out(session, email):
        lookup.outcome = EmailLookupOutcome.OPTED_OUT
        prospect.status = ProspectStatus.OPTED_OUT
        return _save(session, lookup, prospect)

    reliable = (found.score or 0) >= rules.min_score and (
        found.verification in rules.accepted_verifications
    )
    if not reliable:
        lookup.outcome = EmailLookupOutcome.REJECTED
        prospect.status = ProspectStatus.NO_EMAIL
        return _save(session, lookup, prospect)

    if find_prospect_by_email(session, email) is not None:
        # Contact déjà connu par une autre source : on garde l'existant.
        lookup.outcome = EmailLookupOutcome.DUPLICATE
        lookup.prospect_id = None
        session.delete(prospect)
        return _save(session, lookup, None)

    lookup.outcome = EmailLookupOutcome.FOUND
    prospect.email = email
    prospect.email_source = "hunter"
    prospect.status = ProspectStatus.NEW
    if company and not company.domain and found.domain:
        domain = found.domain.lower().removeprefix("www.")
        if not session.scalar(select(Company.id).where(Company.domain == domain)):
            company.domain = domain
    return _save(session, lookup, prospect)


def _save(session: Session, lookup: EmailLookup, prospect: Prospect | None) -> FindEmailResult:
    session.add(lookup)
    session.commit()
    return FindEmailResult(
        outcome=lookup.outcome,
        prospect=ProspectOut.model_validate(prospect) if prospect else None,
        score=lookup.score,
        verification=lookup.verification,
    )


def sourcing_today(session: Session) -> SourcingToday:
    since = start_of_day()
    rows = session.execute(
        select(EmailLookup.outcome, func.count(EmailLookup.id))
        .where(EmailLookup.created_at >= since)
        .group_by(EmailLookup.outcome)
    ).all()
    lookups = {str(outcome): count for outcome, count in rows}
    credits = session.scalar(
        select(func.count(EmailLookup.id)).where(
            EmailLookup.created_at >= since, EmailLookup.credit_used.is_(True)
        )
    )
    companies = session.scalar(select(func.count(Company.id)).where(Company.created_at >= since))
    limit = sourcing.load_sourcing_config().email.daily_lookups
    return SourcingToday(
        lookups=lookups,
        lookups_left=max(0, limit - sum(lookups.values())),
        credits_used=credits,
        companies_created=companies,
    )

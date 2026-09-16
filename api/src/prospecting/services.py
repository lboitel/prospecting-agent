"""Règles métier : dédoublonnage, opposition, transitions de statut."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from prospecting.config import get_settings
from prospecting.llm.drafting import DraftResult, qualify_and_draft
from prospecting.llm.replies import ReplyCategory, classify_reply
from prospecting.llm.research import research_prospect
from prospecting.models import (
    Company,
    Message,
    MessageDirection,
    MessageStatus,
    OptOut,
    Prospect,
    ProspectStatus,
    Research,
)
from prospecting.schemas import ProspectIn, ReplyOut


class Conflict(Exception):
    """Action incompatible avec l'état actuel (renvoyé en HTTP 409)."""


class OptedOut(Exception):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_email(email: str) -> str:
    salt = get_settings().optout_salt.get_secret_value().encode()
    return hmac.new(salt, normalize_email(email).encode(), hashlib.sha256).hexdigest()


def is_opted_out(session: Session, email: str) -> bool:
    return (
        session.scalar(select(OptOut.id).where(OptOut.email_hash == hash_email(email))) is not None
    )


def add_opt_out(session: Session, email: str, reason: str | None) -> None:
    if not is_opted_out(session, email):
        session.add(OptOut(email_hash=hash_email(email), reason=reason))
    prospect = find_prospect_by_email(session, email)
    if prospect:
        prospect.status = ProspectStatus.OPTED_OUT
        for message in prospect.messages:
            if message.status in (MessageStatus.DRAFT, MessageStatus.APPROVED):
                message.status = MessageStatus.REJECTED


def find_prospect_by_email(session: Session, email: str) -> Prospect | None:
    return session.scalar(select(Prospect).where(Prospect.email == normalize_email(email)))


def upsert_company(session: Session, data: ProspectIn) -> Company | None:
    if not (data.siren or data.company_domain or data.company_name):
        return None
    company = None
    if data.siren:
        company = session.scalar(select(Company).where(Company.siren == data.siren))
    if company is None and data.company_domain:
        domain = data.company_domain.lower().removeprefix("www.")
        company = session.scalar(select(Company).where(Company.domain == domain))
    if company is None:
        company = Company(
            siren=data.siren,
            name=data.company_name or data.company_domain or data.siren,
            domain=data.company_domain.lower().removeprefix("www.")
            if data.company_domain
            else None,
        )
        session.add(company)
    return company


def upsert_prospect(session: Session, data: ProspectIn) -> tuple[Prospect, bool]:
    """Crée ou met à jour un prospect, clé = email. Renvoie (prospect, créé)."""
    if is_opted_out(session, data.email):
        raise OptedOut(data.email)

    prospect = find_prospect_by_email(session, data.email)
    created = prospect is None
    if created:
        prospect = Prospect(email=normalize_email(data.email), source=data.source)
        session.add(prospect)

    # Une nouvelle source ne doit pas effacer une valeur connue.
    for field in ("first_name", "last_name", "job_title", "linkedin_url"):
        value = getattr(data, field)
        if value:
            setattr(prospect, field, value)
    company = upsert_company(session, data)
    if company is not None:
        prospect.company = company
    return prospect, created


# Au-delà, un traitement est considéré comme interrompu (conteneur redémarré...).
STALE_PROCESSING = timedelta(minutes=15)


def claim_prospect(session: Session, prospect: Prospect) -> None:
    """Passe le prospect en `processing` de façon atomique.

    Deux exécutions n8n qui se chevauchent ne peuvent pas traiter (ni facturer)
    le même prospect : la seconde reçoit un 409.
    """
    now = datetime.now(UTC)
    claimable = or_(
        Prospect.status.in_([ProspectStatus.NEW, ProspectStatus.RESEARCHED]),
        and_(
            Prospect.status == ProspectStatus.PROCESSING,
            Prospect.updated_at < now - STALE_PROCESSING,
        ),
    )
    result = session.execute(
        update(Prospect)
        .where(Prospect.id == prospect.id, claimable)
        .values(status=ProspectStatus.PROCESSING, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        session.refresh(prospect)
        raise Conflict(f"prospect {prospect.id} déjà traité ou en cours ({prospect.status})")
    session.commit()
    session.refresh(prospect)


def process_prospect(
    session: Session, prospect: Prospect
) -> tuple[Research, DraftResult, Message | None]:
    """Recherche puis qualification/rédaction.

    Refuse un prospect déjà traité ou en cours de traitement. Un prospect bloqué en
    `researched` (échec de la rédaction) reprend à l'étape 2 avec la recherche existante.
    """
    if is_opted_out(session, prospect.email):
        raise OptedOut(prospect.email)
    claim_prospect(session, prospect)

    try:
        if prospect.research:
            research = max(prospect.research, key=lambda r: r.id)
        else:
            found = research_prospect(session, prospect)
            research = Research(
                prospect=prospect, summary=found.summary, sources=found.sources, model=found.model
            )
            session.add(research)
            # On enregistre la recherche avant l'étape suivante : elle a un coût.
            session.commit()
        draft = qualify_and_draft(session, prospect, research)
    except Exception:
        # Libère le verrou ; l'appelant décide de valider ou non la transaction.
        prospect.status = ProspectStatus.RESEARCHED if prospect.research else ProspectStatus.NEW
        raise

    prospect.score = draft.score
    message = None
    if draft.qualified and draft.body:
        message = Message(
            prospect=prospect,
            direction=MessageDirection.OUTBOUND,
            status=MessageStatus.DRAFT,
            subject=draft.subject,
            body=draft.body,
        )
        session.add(message)
        prospect.status = ProspectStatus.DRAFT_READY
    else:
        prospect.status = ProspectStatus.DISQUALIFIED
    session.commit()
    return research, draft, message


def review_message(
    session: Session,
    message: Message,
    approved: bool,
    reviewer: str,
    subject: str | None = None,
    body: str | None = None,
) -> Message:
    if message.status != MessageStatus.DRAFT:
        raise Conflict(f"message {message.id} n'est plus un brouillon ({message.status})")
    prospect = message.prospect
    if approved and is_opted_out(session, prospect.email):
        raise OptedOut(prospect.email)

    message.reviewed_by = reviewer
    if approved:
        message.subject = subject or message.subject
        message.body = body or message.body
        message.status = MessageStatus.APPROVED
        prospect.status = ProspectStatus.APPROVED
    else:
        message.status = MessageStatus.REJECTED
        prospect.status = ProspectStatus.DISQUALIFIED
    session.commit()
    return message


def mark_sent(session: Session, message: Message, external_id: str) -> Message:
    if message.status == MessageStatus.SENT and message.external_id == external_id:
        return message  # webhook rejoué
    if message.status != MessageStatus.APPROVED:
        raise Conflict(f"message {message.id} non validé ({message.status})")
    message.status = MessageStatus.SENT
    message.external_id = external_id
    message.sent_at = datetime.now(UTC)
    message.prospect.status = ProspectStatus.SENT
    session.commit()
    return message


REPLY_STATUS = {
    ReplyCategory.INTERESTED: ProspectStatus.INTERESTED,
    ReplyCategory.NOT_NOW: ProspectStatus.NOT_NOW,
    ReplyCategory.NOT_INTERESTED: ProspectStatus.NOT_INTERESTED,
}

# Catégories qui ne sont pas une vraie réponse : la séquence continue.
PASSIVE_CATEGORIES = {ReplyCategory.OUT_OF_OFFICE, ReplyCategory.BOUNCE}


def handle_reply(session: Session, email: str, body: str, external_id: str) -> ReplyOut:
    prospect = find_prospect_by_email(session, email)
    prospect_id = prospect.id if prospect else None

    existing = session.scalar(select(Message).where(Message.external_id == external_id))
    if existing is not None:
        # Webhook rejoué : pas de nouvel appel LLM ni de nouvelle notification.
        category = ReplyCategory(existing.category)
        return ReplyOut(
            prospect_id=prospect_id,
            category=category,
            summary="réponse déjà traitée",
            follow_up_date="",
            notify_human=False,
            stop_sequence=category not in PASSIVE_CATEGORIES,
        )

    result = classify_reply(session, prospect_id, body)
    if prospect is not None:
        session.add(
            Message(
                prospect=prospect,
                direction=MessageDirection.INBOUND,
                status=MessageStatus.RECEIVED,
                body=body,
                category=result.category,
                external_id=external_id,
            )
        )
    if result.category == ReplyCategory.OPT_OUT:
        add_opt_out(session, email, reason="réponse du prospect")
    elif prospect is not None and result.category in REPLY_STATUS:
        prospect.status = REPLY_STATUS[result.category]
    session.commit()

    return ReplyOut(
        prospect_id=prospect_id,
        category=result.category,
        summary=result.summary,
        follow_up_date=result.follow_up_date,
        notify_human=result.category in (ReplyCategory.INTERESTED, ReplyCategory.OTHER),
        stop_sequence=result.category not in PASSIVE_CATEGORIES,
    )

"""Contrats d'entrée/sortie de l'API (ce que n8n envoie et reçoit)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from prospecting.llm.replies import ReplyCategory
from prospecting.models import EmailLookupOutcome, MessageStatus, ProspectStatus


class ProspectIn(BaseModel):
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    linkedin_url: str | None = None
    company_name: str | None = None
    company_domain: str | None = None
    siren: str | None = Field(default=None, pattern=r"^\d{9}$")
    source: str


class ProspectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str | None
    first_name: str | None
    last_name: str | None
    job_title: str | None
    status: ProspectStatus
    score: int | None
    updated_at: datetime


class ImportReport(BaseModel):
    created: int
    updated: int
    skipped_opted_out: int
    errors: list[str]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    prospect_id: int
    status: MessageStatus
    subject: str | None
    body: str


class ProcessResult(BaseModel):
    prospect: ProspectOut
    qualified: bool
    score: int
    reasons: list[str]
    research_summary: str
    sources: list[str]
    draft: MessageOut | None


class ReviewIn(BaseModel):
    reviewer: str
    # Le relecteur peut corriger le brouillon avant validation.
    subject: str | None = None
    body: str | None = None


class SentIn(BaseModel):
    external_id: str


class ReplyIn(BaseModel):
    email: EmailStr
    body: str
    external_id: str


class ReplyOut(BaseModel):
    prospect_id: int | None
    category: ReplyCategory
    summary: str
    follow_up_date: str
    # Vrai quand un humain doit prendre le relais (proposer des créneaux).
    notify_human: bool
    # Vrai quand n8n doit arrêter la séquence dans l'outil d'envoi.
    stop_sequence: bool


class OptOutIn(BaseModel):
    email: EmailStr
    reason: str | None = None


class OptOutCheck(BaseModel):
    opted_out: bool


class SourcingReport(BaseModel):
    companies_created: int
    prospects_created: int
    pages_read: int
    exhausted: bool


class FindEmailResult(BaseModel):
    outcome: EmailLookupOutcome
    # Absent si le prospect a été supprimé comme doublon.
    prospect: ProspectOut | None
    score: int | None
    verification: str | None


class SourcingToday(BaseModel):
    lookups: dict[str, int]
    lookups_left: int
    credits_used: int
    companies_created: int

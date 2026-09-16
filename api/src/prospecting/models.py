"""Schéma de la base. Toute modification passe par une migration Alembic."""

import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


def str_enum(cls: type[enum.StrEnum]) -> Enum:
    # Simple VARCHAR validé côté application plutôt qu'un type ENUM Postgres :
    # ajouter une valeur ne demande pas de migration.
    return Enum(cls, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e])


class ProspectStatus(enum.StrEnum):
    NEW = "new"
    PROCESSING = "processing"  # recherche/rédaction en cours (verrou applicatif)
    RESEARCHED = "researched"
    DISQUALIFIED = "disqualified"
    DRAFT_READY = "draft_ready"
    APPROVED = "approved"
    SENT = "sent"
    INTERESTED = "interested"  # relais humain : proposition de créneaux
    NOT_NOW = "not_now"
    NOT_INTERESTED = "not_interested"
    OPTED_OUT = "opted_out"


class MessageStatus(enum.StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"
    RECEIVED = "received"


class MessageDirection(enum.StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    siren: Mapped[str | None] = mapped_column(String(9), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str | None] = mapped_column(String(255), unique=True)
    sector: Mapped[str | None] = mapped_column(String(255))
    headcount: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    prospects: Mapped[list["Prospect"]] = relationship(back_populates="company")


class Prospect(Base):
    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    job_title: Mapped[str | None] = mapped_column(String(255))
    linkedin_url: Mapped[str | None] = mapped_column(String(512))
    # Origine de la donnée : obligatoire pour répondre à une demande RGPD.
    source: Mapped[str] = mapped_column(String(255))
    status: Mapped[ProspectStatus] = mapped_column(
        str_enum(ProspectStatus), default=ProspectStatus.NEW, index=True
    )
    score: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    company: Mapped[Company | None] = relationship(back_populates="prospects")
    research: Mapped[list["Research"]] = relationship(back_populates="prospect")
    messages: Mapped[list["Message"]] = relationship(back_populates="prospect")


class Research(Base):
    __tablename__ = "research"

    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(Text)
    # [{"url": ..., "title": ...}] : chaque personnalisation doit être traçable.
    sources: Mapped[list[dict]] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    prospect: Mapped[Prospect] = relationship(back_populates="research")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"))
    direction: Mapped[MessageDirection] = mapped_column(str_enum(MessageDirection))
    status: Mapped[MessageStatus] = mapped_column(str_enum(MessageStatus), index=True)
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    # Classification d'une réponse entrante (voir llm/replies.py).
    category: Mapped[str | None] = mapped_column(String(32))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    # Identifiant côté outil d'envoi : sert à dédupliquer les webhooks.
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    prospect: Mapped[Prospect] = relationship(back_populates="messages")


class OptOut(Base):
    """Liste d'opposition. On ne garde qu'un hash : elle survit à l'effacement du prospect."""

    __tablename__ = "opt_outs"
    __table_args__ = (UniqueConstraint("email_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LlmCall(Base):
    """Journal local des appels LLM (coût, latence) sans stocker les prompts."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int | None] = mapped_column(
        ForeignKey("prospects.id", ondelete="SET NULL"), index=True
    )
    task: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(128))
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cache_read_tokens: Mapped[int] = mapped_column(default=0)
    cache_write_tokens: Mapped[int] = mapped_column(default=0)
    duration_ms: Mapped[int]
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

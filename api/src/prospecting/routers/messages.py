from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from prospecting.db import SessionDep
from prospecting.models import Message, MessageStatus
from prospecting.schemas import MessageOut, ReviewIn, SentIn
from prospecting.services import mark_sent, review_message

router = APIRouter(prefix="/messages", tags=["messages"])


def get_message(message_id: int, session: SessionDep) -> Message:
    message = session.get(Message, message_id)
    if message is None:
        raise HTTPException(404, "message introuvable")
    return message


MessageDep = Annotated[Message, Depends(get_message)]


@router.get("", response_model=list[MessageOut])
def list_messages(
    session: SessionDep,
    status: MessageStatus = MessageStatus.DRAFT,
    limit: int = 50,
):
    query = (
        select(Message).where(Message.status == status).order_by(Message.id).limit(min(limit, 500))
    )
    return session.scalars(query).all()


@router.post("/{message_id}/approve", response_model=MessageOut)
def approve(
    review: ReviewIn,
    message: MessageDep,
    session: SessionDep,
):
    return review_message(
        session, message, True, review.reviewer, subject=review.subject, body=review.body
    )


@router.post("/{message_id}/reject", response_model=MessageOut)
def reject(
    review: ReviewIn,
    message: MessageDep,
    session: SessionDep,
):
    return review_message(session, message, False, review.reviewer)


@router.post("/{message_id}/sent", response_model=MessageOut)
def sent(
    data: SentIn,
    message: MessageDep,
    session: SessionDep,
):
    return mark_sent(session, message, data.external_id)

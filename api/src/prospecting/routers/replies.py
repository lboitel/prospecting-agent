from fastapi import APIRouter

from prospecting.db import SessionDep
from prospecting.llm.client import LLM_ERRORS
from prospecting.schemas import ReplyIn, ReplyOut
from prospecting.services import handle_reply

router = APIRouter(prefix="/replies", tags=["replies"])


@router.post("", response_model=ReplyOut)
def receive_reply(data: ReplyIn, session: SessionDep) -> ReplyOut:
    try:
        return handle_reply(session, data.email, data.body, data.external_id)
    except LLM_ERRORS:
        session.commit()
        raise

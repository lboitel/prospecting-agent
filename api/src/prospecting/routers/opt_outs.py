from fastapi import APIRouter
from pydantic import EmailStr

from prospecting.db import SessionDep
from prospecting.schemas import OptOutCheck, OptOutIn
from prospecting.services import add_opt_out, is_opted_out

router = APIRouter(prefix="/opt-outs", tags=["opt-outs"])


@router.post("", status_code=204)
def create(data: OptOutIn, session: SessionDep) -> None:
    add_opt_out(session, data.email, data.reason)
    session.commit()


@router.get("/check", response_model=OptOutCheck)
def check(email: EmailStr, session: SessionDep) -> OptOutCheck:
    return OptOutCheck(opted_out=is_opted_out(session, email))

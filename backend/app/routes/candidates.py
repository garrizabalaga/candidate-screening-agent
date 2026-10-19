"""Candidatos: alta de aplicaciones, listado y conversación."""

import sqlite3
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.exceptions import CandidateNotFound
from app.models.candidate import Candidate, CandidateOverview
from app.models.enums import Country
from app.models.message import Message
from app.clients.aws_client import BedrockClient
from app.models.summary import Summary
from app.routes.dependencies import get_agent_llm, get_db, get_summary_job
from app.services.candidate_service import CandidateService
from app.services.review_service import NoSlotsAvailable, NotInReview, ReviewService
from app.services.scheduling_service import SchedulingService
from app.services.summary_service import SummaryService
from app.repositories.summary_repository import SummaryRepository

router = APIRouter(prefix="/candidates")


class ApplicationRequest(BaseModel):
    full_name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    country: Country  # de la oferta a la que aplicó


@router.post("", response_model=Candidate, status_code=201)
def create_candidate(req: ApplicationRequest, conn: sqlite3.Connection = Depends(get_db)) -> Candidate:
    return CandidateService(conn).create(req.full_name, req.phone, req.country)


@router.get("", response_model=list[CandidateOverview])
def list_candidates(conn: sqlite3.Connection = Depends(get_db)) -> list[CandidateOverview]:
    return CandidateService(conn).list_all()


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]


class ReviewResponse(BaseModel):
    message_sent: str  # lo que le ha llegado al candidato por el chat


@router.post("/{candidate_id}/review", response_model=ReviewResponse)
def review_candidate(
    candidate_id: str,
    req: ReviewRequest,
    background: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
    summary_job: Callable[[str], None] = Depends(get_summary_job),
) -> ReviewResponse:
    """Decisión del recruiter sobre un caso en revisión manual."""
    service = ReviewService(conn, SchedulingService(conn))
    try:
        text = service.resolve(candidate_id, approve=req.decision == "approve")
    except CandidateNotFound:
        raise HTTPException(status_code=404, detail="candidate not found")
    except NotInReview:
        raise HTTPException(status_code=409, detail="candidate is not pending manual review")
    except NoSlotsAvailable:
        raise HTTPException(status_code=409, detail="no interview slots available")
    if req.decision == "reject":
        background.add_task(summary_job, candidate_id)  # la conversación se cierra: resumen
    return ReviewResponse(message_sent=text)


@router.get("/{candidate_id}/summary", response_model=Summary)
def get_summary(candidate_id: str, conn: sqlite3.Connection = Depends(get_db)) -> Summary:
    summary = SummaryRepository(conn).get(candidate_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="summary not found")
    return summary


@router.post("/{candidate_id}/summary", response_model=Summary)
def regenerate_summary(
    candidate_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    llm: BedrockClient = Depends(get_agent_llm),
) -> Summary:
    """Genera (o regenera) el resumen ahora, esperando el resultado (botón del dashboard)."""
    try:
        return SummaryService(conn, llm).generate(candidate_id)
    except CandidateNotFound:
        raise HTTPException(status_code=404, detail="candidate not found")


@router.get("/{candidate_id}/messages", response_model=list[Message])
def get_messages(candidate_id: str, conn: sqlite3.Connection = Depends(get_db)) -> list[Message]:
    try:
        return CandidateService(conn).get_messages(candidate_id)
    except CandidateNotFound:
        raise HTTPException(status_code=404, detail="candidate not found")

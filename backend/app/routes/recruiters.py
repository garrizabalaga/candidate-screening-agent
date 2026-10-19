"""Recruiters: lista y calendario de entrevistas de cada uno (para el dashboard)."""

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.exceptions import RecruiterNotFound
from app.models.candidate import CalendarEntry
from app.models.enums import Country
from app.models.interview import Recruiter
from app.routes.dependencies import get_db
from app.services.recruiter_service import RecruiterService

router = APIRouter(prefix="/recruiters")


@router.get("", response_model=list[Recruiter])
def list_recruiters(country: Country | None = None, conn: sqlite3.Connection = Depends(get_db)) -> list[Recruiter]:
    return RecruiterService(conn).list_recruiters(country)


@router.get("/{recruiter_id}/interviews", response_model=list[CalendarEntry])
def recruiter_calendar(
    recruiter_id: str,
    start: date | None = Query(None, alias="from"),  # "from" es palabra reservada en Python
    end: date | None = Query(None, alias="to"),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[CalendarEntry]:
    try:
        return RecruiterService(conn).calendar(recruiter_id, start, end)
    except RecruiterNotFound:
        raise HTTPException(status_code=404, detail="recruiter not found")

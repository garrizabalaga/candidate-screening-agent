"""Disparadores manuales de los jobs (para la demo). En producción los lanza un
scheduler, pero ejecutan el mismo servicio."""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from app.routes.dependencies import get_db, get_summary_job
from app.services.outreach_service import OutreachService
from app.services.reengagement_service import ReengagementService, ReminderAction, SentReminder

router = APIRouter(prefix="/jobs")


class FirstContactResponse(BaseModel):
    contacted: list[str]


class RemindersRequest(BaseModel):
    # Reloj simulado para la demo ("han pasado 25 h"). Sin él, la hora real.
    now: datetime | None = None


class RemindersResponse(BaseModel):
    sent: list[SentReminder]


@router.post("/first-contact", response_model=FirstContactResponse)
def first_contact(conn: sqlite3.Connection = Depends(get_db)) -> FirstContactResponse:
    return FirstContactResponse(contacted=OutreachService(conn).run_first_contact())


@router.post("/reminders", response_model=RemindersResponse)
def reminders(
    background: BackgroundTasks,
    req: RemindersRequest | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    summary_job: Callable[[str], None] = Depends(get_summary_job),
) -> RemindersResponse:
    now = req.now if req and req.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)  # sin zona horaria se interpreta como UTC
    sent = ReengagementService(conn, now=lambda: now).run_reminders()
    for s in sent:
        if s.action == ReminderAction.CLOSE:  # se cierra como abandonada: resumen
            background.add_task(summary_job, s.candidate_id)
    return RemindersResponse(sent=sent)

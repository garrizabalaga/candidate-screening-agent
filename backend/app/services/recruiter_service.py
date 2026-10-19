"""Lo que ve un recruiter: la lista de recruiters y su calendario de entrevistas."""

import sqlite3
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone

from app.core.config import COUNTRY_TIMEZONE, SLOT_HORIZON_DAYS
from app.core.exceptions import RecruiterNotFound
from app.models.candidate import CalendarEntry
from app.models.enums import Country
from app.models.interview import Interview, InterviewSummary, Recruiter
from app.repositories import recruiter_repository
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.interview_repository import InterviewRepository


def summary(interview: Interview) -> InterviewSummary:
    recruiter = recruiter_repository.get_recruiter(interview.recruiter_id)
    return InterviewSummary(
        id=interview.id,
        starts_at=interview.starts_at,
        recruiter_id=interview.recruiter_id,
        recruiter_name=recruiter.name if recruiter else interview.recruiter_id,
    )


class RecruiterService:
    def __init__(self, conn: sqlite3.Connection, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.candidates = CandidateRepository(conn)
        self.interviews = InterviewRepository(conn)
        self.now = now

    def list_recruiters(self, country: Country | None = None) -> list[Recruiter]:
        return recruiter_repository.list_recruiters(country)

    def calendar(self, recruiter_id: str, start: date | None = None, end: date | None = None) -> list[CalendarEntry]:
        """Entrevistas del recruiter entre `start` y `end` (incluidos), días en la hora
        local de su país. Por defecto, de hoy a SLOT_HORIZON_DAYS días."""
        recruiter = recruiter_repository.get_recruiter(recruiter_id)
        if recruiter is None:
            raise RecruiterNotFound(recruiter_id)
        tz = COUNTRY_TIMEZONE[recruiter.country]
        start = start or self.now().astimezone(tz).date()
        end = end or start + timedelta(days=SLOT_HORIZON_DAYS)
        interviews = self.interviews.scheduled_between(
            datetime.combine(start, time(0), tz), datetime.combine(end + timedelta(days=1), time(0), tz), recruiter_id
        )
        return [
            CalendarEntry(interview=summary(i), candidate=self.candidates.get(i.candidate_id)) for i in interviews
        ]

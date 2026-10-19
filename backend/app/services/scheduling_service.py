"""Agenda de entrevistas: qué huecos se ofrecen a un candidato y la reserva.

Huecos libres = plantillas semanales de los recruiters de su país (hora local)
menos las entrevistas ya reservadas. De esos, se ofrecen SLOTS_OFFERED:
- a partir de mañana y como mucho a SLOT_HORIZON_DAYS días,
- en días distintos, para que pueda elegir de verdad,
- primero los de su franja preferida (mañana/tarde); si prefiere noche o es
  flexible, o no hay suficientes, se completa con los más próximos,
- si varios recruiters están libres a la misma hora, se asigna al que menos
  entrevistas tenga esa semana.
"""

import sqlite3
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone

from app.core.config import AFTERNOON_FROM_HOUR, COUNTRY_TIMEZONE, INTERVIEW_MINUTES, SLOT_HORIZON_DAYS, SLOTS_OFFERED
from app.models.candidate import Candidate
from app.models.enums import Schedule
from app.models.interview import Interview, Recruiter, Slot
from app.repositories import recruiter_repository
from app.repositories.interview_repository import InterviewRepository

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class SlotTaken(Exception):
    """El hueco se ha reservado mientras tanto (lo garantiza el índice único)."""


class AlreadyScheduled(Exception):
    """El candidato ya tiene una entrevista activa."""


class SchedulingService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        recruiters: list[Recruiter] | None = None,
    ):
        self.conn = conn
        self.interviews = InterviewRepository(conn)
        self.now = now
        self._recruiters = recruiters

    def available_slots(self, candidate: Candidate, limit: int = SLOTS_OFFERED) -> list[Slot]:
        """Los huecos a ofrecer, ordenados por fecha."""
        free = self._free_slots(candidate)
        preferred = [s for s in free if _matches(s, candidate)]

        offered: list[Slot] = []
        for pool in (preferred, free):
            for slot in pool:
                if len(offered) == limit:
                    break
                if all(_local_date(slot, candidate) != _local_date(o, candidate) for o in offered):
                    offered.append(slot)
        return sorted(offered, key=lambda s: s.starts_at)

    def book(self, candidate: Candidate, slot: Slot) -> Interview:
        """Reserva el hueco. No hace commit: va en la transacción del turno."""
        if self.interviews.scheduled_for_candidate(candidate.id) is not None:
            raise AlreadyScheduled(candidate.id)
        if slot.starts_at <= self.now():
            # Contestó tarde y el hueco ya ha pasado: como si se hubiera ocupado.
            raise SlotTaken(f"{slot.starts_at.isoformat()} is in the past")
        interview = Interview(
            id=uuid.uuid4().hex,
            candidate_id=candidate.id,
            recruiter_id=slot.recruiter_id,
            starts_at=slot.starts_at,
            created_at=self.now(),
        )
        try:
            self.interviews.create(interview)
        except sqlite3.IntegrityError as e:
            raise SlotTaken(f"{slot.recruiter_id} {slot.starts_at.isoformat()}") from e
        return interview

    # --- Pasos internos -------------------------------------------------------------

    def _free_slots(self, candidate: Candidate) -> list[Slot]:
        """Todos los huecos libres en el horizonte, uno por hora (el recruiter con
        menos carga esa semana), ordenados por fecha."""
        tz = COUNTRY_TIMEZONE[candidate.country]
        recruiters = (
            self._recruiters if self._recruiters is not None else recruiter_repository.list_recruiters(candidate.country)
        )
        recruiters = [r for r in recruiters if r.country == candidate.country]
        first_day = self.now().astimezone(tz).date() + timedelta(days=1)
        days = [first_day + timedelta(days=i) for i in range(SLOT_HORIZON_DAYS)]

        # Reservas y carga desde el lunes de la primera semana hasta el final del horizonte.
        window_start = datetime.combine(first_day - timedelta(days=first_day.weekday()), time(0), tz)
        window_end = datetime.combine(days[-1] + timedelta(days=7), time(0), tz)
        booked = self.interviews.scheduled_between(window_start, window_end)
        taken = {(i.recruiter_id, i.starts_at) for i in booked}
        load = Counter((i.recruiter_id, i.starts_at.astimezone(tz).isocalendar()[:2]) for i in booked)

        by_start: dict[datetime, list[Recruiter]] = {}
        for day in days:
            for recruiter in recruiters:
                for start in _template_starts(recruiter, day, tz):
                    if (recruiter.id, start) not in taken:
                        by_start.setdefault(start, []).append(recruiter)

        slots = []
        for start in sorted(by_start):
            week = start.isocalendar()[:2]
            recruiter = min(by_start[start], key=lambda r: (load[(r.id, week)], r.id))
            slots.append(Slot(recruiter_id=recruiter.id, recruiter_name=recruiter.name, starts_at=start))
        return slots


def _template_starts(recruiter: Recruiter, day: date, tz) -> list[datetime]:
    """Horas de inicio de los huecos de un recruiter un día concreto (hora local)."""
    starts = []
    for block in recruiter.weekly.get(_WEEKDAYS[day.weekday()], []):
        begin, end = (time.fromisoformat(t) for t in block.split("-"))
        current = datetime.combine(day, begin, tz)
        last = datetime.combine(day, end, tz) - timedelta(minutes=INTERVIEW_MINUTES)
        while current <= last:
            starts.append(current)
            current += timedelta(minutes=INTERVIEW_MINUTES)
    return starts


def _local_date(slot: Slot, candidate: Candidate) -> date:
    return slot.starts_at.astimezone(COUNTRY_TIMEZONE[candidate.country]).date()


def _matches(slot: Slot, candidate: Candidate) -> bool:
    """¿El hueco cae en su franja preferida? Noche o flexible: cualquiera vale."""
    hour = slot.starts_at.astimezone(COUNTRY_TIMEZONE[candidate.country]).hour
    match candidate.fields.schedule:
        case Schedule.MORNING:
            return hour < AFTERNOON_FROM_HOUR
        case Schedule.AFTERNOON:
            return hour >= AFTERNOON_FROM_HOUR
        case _:
            return True

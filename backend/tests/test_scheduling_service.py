"""Tests de la agenda con una SQLite en memoria, un reloj fijo y recruiters de prueba.

Jueves 15/10/2026, 10:00 UTC = 12:00 en Madrid. El primer día que se ofrece es
mañana, viernes 16.
"""

import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.candidate import Candidate
from app.models.enums import Country, Schedule
from app.models.interview import Recruiter, Slot
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.database import get_connection, init_db
from app.services.scheduling_service import AlreadyScheduled, SchedulingService, SlotTaken

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
MADRID = ZoneInfo("Europe/Madrid")

RECRUITERS = [
    Recruiter(id="ra", name="Laura", country=Country.ES, weekly={"fri": ["10:00-11:00"], "mon": ["16:00-17:00"]}),
    Recruiter(id="rb", name="Javier", country=Country.ES, weekly={"fri": ["10:00-11:00"], "tue": ["10:00-10:30"]}),
    Recruiter(id="rmx", name="Sofía", country=Country.MX, weekly={"fri": ["10:00-11:00"]}),
]


def local(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=MADRID)


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    with conn:
        for cid in ["c1", "c2", "c3"]:
            CandidateRepository(conn).create(candidate(cid))
    return conn


def candidate(cid: str = "c1", schedule: Schedule | None = None) -> Candidate:
    c = Candidate(id=cid, full_name="Ana López", phone="x", country=Country.ES, applied_at=NOW)
    c.fields.schedule = schedule
    return c


def service(conn) -> SchedulingService:
    return SchedulingService(conn, now=lambda: NOW, recruiters=RECRUITERS)


def starts(slots: list[Slot]) -> list[datetime]:
    return [s.starts_at for s in slots]


# --- Qué se ofrece -------------------------------------------------------------------

def test_offers_one_slot_per_day_from_tomorrow_in_local_time(conn):
    offered = service(conn).available_slots(candidate())
    # Viernes 10:00 (el primero de ese día), lunes 16:00, martes 10:00: días distintos.
    assert starts(offered) == [local(16, 10), local(19, 16), local(20, 10)]
    assert all(s.recruiter_id in {"ra", "rb"} for s in offered)  # nunca el recruiter de México


# Primero los huecos de su franja preferida; si prefiere noche (no hay) o le da igual, los más próximos.
@pytest.mark.parametrize("schedule, limit, expected", [
    (Schedule.MORNING, 3, [local(16, 10), local(20, 10), local(23, 10)]),  # sin el lunes por la tarde
    (Schedule.AFTERNOON, 1, [local(19, 16)]),  # su franja, aunque no sea el hueco más próximo
    (Schedule.EVENING, 1, [local(16, 10)]),
    (Schedule.FLEXIBLE, 1, [local(16, 10)]),
    (None, 1, [local(16, 10)]),
])
def test_preferred_band_goes_first(conn, schedule, limit, expected):
    assert starts(service(conn).available_slots(candidate(schedule=schedule), limit=limit)) == expected


def test_fills_with_the_other_band_when_there_are_not_enough(conn):
    only_mornings = [Recruiter(id="ra", name="Laura", country=Country.ES, weekly={"mon": ["16:00-17:00"], "fri": ["10:00-10:30"]})]
    s = SchedulingService(conn, now=lambda: NOW, recruiters=only_mornings)
    offered = s.available_slots(candidate(schedule=Schedule.AFTERNOON))
    # Tardes: lunes 19 y 26. Falta uno: se completa con la mañana más próxima.
    assert starts(offered) == [local(16, 10), local(19, 16), local(26, 16)]


# --- Carga y reservas -------------------------------------------------------------

def test_same_time_goes_to_the_least_loaded_recruiter(conn):
    s = service(conn)
    first = s.available_slots(candidate("c1"), limit=1)[0]
    assert first.recruiter_id == "ra"  # empate a 0: orden por id
    with conn:
        s.book(candidate("c1"), first)

    second = s.available_slots(candidate("c2"), limit=1)[0]
    assert second.starts_at == local(16, 10)  # misma hora, otro recruiter libre
    assert second.recruiter_id == "rb"


def test_booked_slot_is_not_offered_again(conn):
    s = service(conn)
    for cid in ["c1", "c2"]:
        with conn:
            s.book(candidate(cid), s.available_slots(candidate(cid), limit=1)[0])
    # Los dos recruiters ya están ocupados el viernes a las 10:00.
    assert s.available_slots(candidate("c3"), limit=1)[0].starts_at == local(16, 10, 30)


def test_double_booking_the_same_slot_is_rejected_by_the_database(conn):
    s = service(conn)
    slot = s.available_slots(candidate("c1"), limit=1)[0]
    with conn:
        s.book(candidate("c1"), slot)
    with pytest.raises(SlotTaken):
        s.book(candidate("c2"), slot)


def test_a_candidate_cannot_have_two_interviews(conn):
    s = service(conn)
    offered = s.available_slots(candidate("c1"))
    with conn:
        s.book(candidate("c1"), offered[0])
    with pytest.raises(AlreadyScheduled):
        s.book(candidate("c1"), offered[1])

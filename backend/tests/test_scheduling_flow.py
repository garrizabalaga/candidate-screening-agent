"""La etapa scheduling: máquina de estados (pura) y tool record_answer con la
agenda real sobre una SQLite en memoria."""

import sqlite3
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.candidate import Candidate, ExtractedFields
from app.models.enums import Country, Language, Outcome, Schedule, Stage
from app.models.interview import Recruiter, Slot
from app.prompts.slots import format_slot, numbered
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.database import get_connection, init_db
from app.repositories.interview_repository import InterviewRepository
from app.services.scheduling_service import SchedulingService
from app.services.state_machine import Action, advance, with_slots
from app.tools import record_answer

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)  # jueves
TODAY = NOW.date()
MADRID = ZoneInfo("Europe/Madrid")
RECRUITERS = [Recruiter(id="ra", name="Laura Martín", country=Country.ES, weekly={"fri": ["10:00-11:00"], "mon": ["16:00-17:00"]})]


def slot(day: int, hour: int) -> Slot:
    return Slot(recruiter_id="ra", recruiter_name="Laura Martín", starts_at=datetime(2026, 10, day, hour, tzinfo=MADRID))


OFFERED = [slot(16, 10), slot(19, 16)]


def candidate(**kwargs) -> Candidate:
    data = {"id": "c1", "full_name": "Ana López", "phone": "x", "country": Country.ES, "applied_at": NOW}
    return Candidate(**(data | kwargs))


def scheduling_candidate(**kwargs) -> Candidate:
    return candidate(stage=Stage.SCHEDULING, offered_slots=OFFERED, **kwargs)


def turn(c: Candidate, raw: str = "...", **fields):
    return advance(c, ExtractedFields(**fields), raw, TODAY)


# --- Máquina de estados -----------------------------------------------------------------

def test_with_slots_stores_the_offer_or_goes_to_review_if_there_are_none():
    d = turn(candidate(stage=Stage.START_DATE), start_date="immediately")
    assert with_slots(d, OFFERED).candidate.offered_slots == OFFERED

    none_free = with_slots(d, [])
    assert none_free.action == Action.MANUAL_REVIEW
    assert none_free.new_flag.reason == "no_slots_available"


def test_choosing_a_slot_books_it():
    d = turn(scheduling_candidate(), "el segundo", chosen_slot=2)
    assert d.action == Action.BOOKED
    assert d.slot == OFFERED[1]
    assert (d.candidate.stage, d.candidate.outcome) == (Stage.DONE, Outcome.QUALIFIED)


def test_none_fits_goes_to_manual_review_with_the_literal_answer():
    d = turn(scheduling_candidate(), "ninguno, trabajo esos días", chosen_slot="none")
    assert d.action == Action.MANUAL_REVIEW
    assert d.candidate.outcome == Outcome.MANUAL_REVIEW
    assert (d.new_flag.reason, d.new_flag.raw_answer) == ("no_slot_fits", "ninguno, trabajo esos días")


def test_unclear_choice_reasks_twice_then_manual_review():
    c = scheduling_candidate()
    d = turn(c, "mmm", chosen_slot=5)  # número fuera de rango = no elegido
    assert (d.action, d.repeat, d.attempt) == (Action.OFFER_SLOTS, True, 1)
    d = turn(d.candidate, "no sé")
    assert (d.action, d.attempt) == (Action.OFFER_SLOTS, 2)
    d = turn(d.candidate, "ya veremos")
    assert d.action == Action.MANUAL_REVIEW
    assert d.new_flag.reason == "scheduling_unclear"


def test_a_question_repeats_the_slots_without_counting():
    d = turn(scheduling_candidate(), "¿es por videollamada?", candidate_question="si es por videollamada")
    assert (d.action, d.repeat, d.attempt) == (Action.OFFER_SLOTS, True, 0)
    assert d.candidate.reask_counts == {}


def test_slots_are_written_in_local_time():
    c = scheduling_candidate()
    assert format_slot(OFFERED[0], c) == "viernes 16 de octubre, 10:00"
    assert numbered(c) == "1) viernes 16 de octubre, 10:00; 2) lunes 19 de octubre, 16:00"
    c.language = Language.EN
    assert format_slot(OFFERED[1], c) == "Monday 19 October, 16:00"


# --- Tool record_answer con la agenda real --------------------------------------------------

@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    with conn:
        for cid in ["c1", "c2"]:
            CandidateRepository(conn).create(candidate(id=cid))
    return conn


def scheduling(conn) -> SchedulingService:
    return SchedulingService(conn, now=lambda: NOW, recruiters=RECRUITERS)


def qualified_turn(conn, cid="c1"):
    c = candidate(id=cid, stage=Stage.START_DATE, fields={
        "identity_confirmed": "yes", "license": "yes", "city": "Madrid", "availability": "full_time",
        "schedule": Schedule.MORNING, "experience": {"years": 1, "platforms": []},
    })
    return record_answer.run(c, {"start_date": "immediately", "language": "es"}, "ya mismo", TODAY, scheduling(conn))[0]


def test_tool_offers_real_slots_when_the_candidate_qualifies(conn):
    d = qualified_turn(conn)
    assert d.action == Action.OFFER_SLOTS
    # Prefiere mañanas: viernes 16 y 23 por la mañana; se completa con el lunes por la tarde.
    expected = [slot(16, 10), slot(19, 16), slot(23, 10)]
    assert [s.starts_at for s in d.candidate.offered_slots] == [s.starts_at for s in expected]
    assert "1) viernes 16 de octubre, 10:00" in record_answer.objective(d)


def test_tool_books_the_chosen_slot(conn):
    c = qualified_turn(conn).candidate
    d, _ = record_answer.run(c, {"chosen_slot": 1, "language": "es"}, "el primero", TODAY, scheduling(conn))
    assert d.action == Action.BOOKED
    interview = InterviewRepository(conn).scheduled_for_candidate("c1")
    assert (interview.recruiter_id, interview.starts_at) == ("ra", slot(16, 10).starts_at)
    assert "con Laura" in record_answer.objective(d)


def test_slot_taken_meanwhile_offers_new_ones(conn):
    ana = qualified_turn(conn, "c1").candidate
    luis = qualified_turn(conn, "c2").candidate  # ve los mismos huecos
    with conn:
        record_answer.run(luis, {"chosen_slot": 1, "language": "es"}, "el 1", TODAY, scheduling(conn))

    d, _ = record_answer.run(ana, {"chosen_slot": 1, "language": "es"}, "el 1", TODAY, scheduling(conn))
    assert (d.action, d.slot_taken) == (Action.OFFER_SLOTS, True)
    assert slot(16, 10).starts_at not in [s.starts_at for s in d.candidate.offered_slots]
    assert d.candidate.outcome is None and d.candidate.stage == Stage.SCHEDULING
    assert "acaba de ocuparse" in record_answer.objective(d)

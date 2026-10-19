"""Revisión manual: el recruiter aprueba o descarta y el candidato recibe la respuesta por el chat."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.models.candidate import Candidate, ExtractedFields, ReviewFlag
from app.models.enums import Country, DisqualificationReason, Outcome, Stage
from app.models.interview import Recruiter
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.database import get_connection, init_db
from app.repositories.message_repository import MessageRepository
from app.services.reengagement_service import ReminderAction, due_action
from app.services.review_service import NoSlotsAvailable, NotInReview, ReviewService
from app.services.scheduling_service import SchedulingService
from app.services.state_machine import Action, advance

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)  # jueves, 12:00 en Madrid
RECRUITERS = [Recruiter(id="ra", name="Laura Martín", country=Country.ES, weekly={"fri": ["10:00-11:00"], "mon": ["16:00-17:00"]})]


def in_review(cid="c1", **kwargs) -> Candidate:
    data = {
        "id": cid, "full_name": "Ana López", "phone": "x", "country": Country.ES,
        "applied_at": NOW - timedelta(days=3), "contacted_at": NOW - timedelta(days=3),
        "last_candidate_message_at": NOW - timedelta(days=2),
        "stage": Stage.DONE, "outcome": Outcome.MANUAL_REVIEW,
        "review_flags": [ReviewFlag(stage=Stage.LICENSE, reason="license_unclear", raw_answer="lo tengo en trámite")],
    }
    return Candidate(**(data | kwargs))


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    with conn:
        CandidateRepository(conn).create(in_review())
    return conn


def service(conn, recruiters=RECRUITERS) -> ReviewService:
    return ReviewService(conn, SchedulingService(conn, now=lambda: NOW, recruiters=recruiters), now=lambda: NOW)


def test_approve_offers_slots_through_the_chat_and_then_books_like_anyone_else(conn):
    text = service(conn).resolve("c1", approve=True)

    c = CandidateRepository(conn).get("c1")
    assert (c.stage, c.outcome) == (Stage.SCHEDULING, None)
    assert len(c.offered_slots) == 3
    assert c.review_flags  # se conservan como historial
    assert c.reopened_at == NOW
    assert "¿Cuál de estos huecos" in text and "1) viernes 16 de octubre, 10:00" in text
    assert MessageRepository(conn).list_by_candidate("c1")[-1].text == text
    # A partir de aquí es un candidato apto más: elige hueco y queda reservado.
    d = advance(c, ExtractedFields(chosen_slot=1), "el viernes", NOW.date())
    assert (d.action, d.candidate.outcome) == (Action.BOOKED, Outcome.QUALIFIED)


def test_reject_closes_with_a_message(conn):
    text = service(conn).resolve("c1", approve=False)

    c = CandidateRepository(conn).get("c1")
    assert c.outcome == Outcome.DISQUALIFIED
    assert c.disqualification_reason == DisqualificationReason.REJECTED_BY_RECRUITER
    assert text.startswith("Hola, Ana. Gracias por tu paciencia")
    assert MessageRepository(conn).list_by_candidate("c1")[-1].text == text


def test_only_cases_in_review_can_be_resolved(conn):
    with conn:
        CandidateRepository(conn).create(in_review("c2", outcome=Outcome.QUALIFIED))
    with pytest.raises(NotInReview):
        service(conn).resolve("c2", approve=True)


def test_approve_without_free_slots_changes_nothing(conn):
    with pytest.raises(NoSlotsAvailable):
        service(conn, recruiters=[]).resolve("c1", approve=True)
    c = CandidateRepository(conn).get("c1")
    assert c.outcome == Outcome.MANUAL_REVIEW
    assert MessageRepository(conn).list_by_candidate("c1") == []


def test_reminders_count_from_the_reopening_not_from_the_old_message(conn):
    service(conn).resolve("c1", approve=True)
    c = CandidateRepository(conn).get("c1")
    # Su último mensaje es de hace 2 días, pero la conversación se reabrió ahora.
    assert due_action(c, NOW + timedelta(hours=1)) is None
    assert due_action(c, NOW + timedelta(hours=2)) == ReminderAction.FIRST_REMINDER

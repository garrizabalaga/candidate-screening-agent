"""Tests del repositorio de candidatos contra una SQLite en memoria."""

from datetime import date, datetime, timezone

import pytest

from app.models.candidate import (
    Candidate,
    Experience,
    ReviewFlag,
)
from app.models.enums import (
    Availability,
    Country,
    Identity,
    Language,
    License,
    Outcome,
    Schedule,
    Stage,
)
from app.models.interview import Slot
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.database import get_connection, init_db


@pytest.fixture
def repo() -> CandidateRepository:
    conn = get_connection(":memory:")
    init_db(conn)
    return CandidateRepository(conn)


def application(candidate_id: str = "c1", applied_at: datetime | None = None) -> Candidate:
    return Candidate(
        id=candidate_id,
        full_name="Ana López",
        phone="+34600000000",
        country=Country.ES,
        applied_at=applied_at or datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc),
    )


def test_create_and_get_new_application(repo):
    repo.create(application())
    c = repo.get("c1")
    assert c == application()
    assert c.stage == Stage.IDENTITY and c.contacted_at is None
    assert repo.get("nope") is None


def test_update_round_trips_every_field(repo):
    """El SQL es a mano: cada campo tiene que volver de la BD exactamente igual."""
    repo.create(application())
    c = application()
    c.contacted_at = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    c.stage = Stage.DONE
    c.language = Language.EN
    c.outcome = Outcome.MANUAL_REVIEW
    c.fields.identity_confirmed = Identity.YES
    c.fields.license = License.UNCLEAR
    c.fields.city = "Málaga"
    c.fields.availability = Availability.PART_TIME
    c.fields.schedule = Schedule.EVENING
    c.fields.experience = Experience(years=1.5, platforms=["Glovo", "Uber Eats"])
    c.fields.start_date = date(2026, 11, 2)
    c.reask_counts = {Stage.LICENSE: 2}
    c.review_flags = [ReviewFlag(stage=Stage.LICENSE, reason="license_unclear", raw_answer="lo tengo en trámite")]
    c.offered_slots = [Slot(recruiter_id="r01", recruiter_name="Laura Martín", starts_at=datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc))]
    c.last_candidate_message_at = datetime(2026, 10, 14, 10, 5, tzinfo=timezone.utc)
    repo.update(c)
    assert repo.get("c1") == c

    # Valores literales en vez de fecha / experiencia.
    c.fields.start_date, c.fields.experience = "immediately", "unclear"
    repo.update(c)
    assert repo.get("c1") == c


def test_list_pending_contact_returns_only_uncontacted_oldest_first(repo):
    repo.create(application("late", datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)))
    repo.create(application("early", datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)))
    contacted = application("done")
    contacted.contacted_at = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
    repo.create(contacted)

    assert [c.id for c in repo.list_pending_contact()] == ["early", "late"]

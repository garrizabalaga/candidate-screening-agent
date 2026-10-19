"""Tests del job de primer contacto."""

import sqlite3
from datetime import datetime, timezone

import pytest

from app.core.config import PUBLIC_BASE_URL
from app.models.candidate import Candidate
from app.models.enums import Country, Stage
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.database import get_connection, init_db
from app.repositories.message_repository import MessageRepository
from app.services.outreach_service import OutreachService

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)


def application(candidate_id: str, contacted_at: datetime | None = None) -> Candidate:
    return Candidate(
        id=candidate_id,
        full_name="Ana López",
        phone="+34600000000",
        country=Country.ES,
        applied_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        contacted_at=contacted_at,
    )


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    with conn:
        repo = CandidateRepository(conn)
        repo.create(application("pending"))
        repo.create(application("already", contacted_at=datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)))
    return conn


def test_contacts_only_pending_candidates(conn):
    contacted = OutreachService(conn, now=lambda: NOW).run_first_contact()

    assert contacted == ["pending"]
    messages = MessageRepository(conn).list_by_candidate("pending")
    assert len(messages) == 1
    assert messages[0].role == "agent"
    assert "Soy Guille" in messages[0].text and "¿hablo con Ana López?" in messages[0].text
    assert messages[0].text.endswith(f"{PUBLIC_BASE_URL}/voice/pending")  # enlace para hacerlo por voz
    assert MessageRepository(conn).list_by_candidate("already") == []

    stored = CandidateRepository(conn).get("pending")
    assert stored.contacted_at == NOW
    assert stored.stage == Stage.IDENTITY  # la apertura ya pregunta la identidad


def test_running_twice_does_not_write_twice(conn):
    service = OutreachService(conn, now=lambda: NOW)
    service.run_first_contact()
    assert service.run_first_contact() == []
    assert len(MessageRepository(conn).list_by_candidate("pending")) == 1

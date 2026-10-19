"""Tests del repositorio de mensajes contra una SQLite en memoria."""

import sqlite3
from datetime import datetime, timezone

import pytest

from app.models.candidate import Candidate
from app.models.enums import Country
from app.models.message import Message
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.database import get_connection, init_db
from app.repositories.message_repository import MessageRepository

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    for candidate_id in ["c1", "c2"]:
        CandidateRepository(conn).create(
            Candidate(id=candidate_id, full_name="Ana López", phone="+34600000000", country=Country.ES, applied_at=T0)
        )
    return conn


def msg(candidate_id: str, role: str, text: str) -> Message:
    return Message(candidate_id=candidate_id, role=role, text=text, created_at=T0)


def test_messages_are_listed_in_order_and_per_candidate(conn):
    repo = MessageRepository(conn)
    repo.add(msg("c1", "agent", "¿Hablo con Ana López?"))
    repo.add(msg("c2", "agent", "otro candidato"))
    repo.add(msg("c1", "candidate", "sí"))

    assert repo.list_by_candidate("c1") == [
        msg("c1", "agent", "¿Hablo con Ana López?"),
        msg("c1", "candidate", "sí"),
    ]


def test_message_for_unknown_candidate_is_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        MessageRepository(conn).add(msg("nope", "candidate", "hola"))

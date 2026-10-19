"""Tests del job de recordatorios con una SQLite en memoria y un reloj simulado.

Horas en UTC. En octubre Madrid es UTC+2 y Ciudad de México UTC-6, así que
la franja 9:00-21:00 local es 7:00-19:00 UTC en España y 15:00-3:00 UTC en México.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.models.candidate import Candidate
from app.models.enums import Country, Outcome, Stage
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.database import get_connection, init_db
from app.repositories.message_repository import MessageRepository
from app.services.reengagement_service import ReengagementService, ReminderAction, due_action

CONTACTED = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)  # 10:00 en Madrid


def candidate(**kwargs) -> Candidate:
    data = {
        "id": "c1",
        "full_name": "Ana López",
        "phone": "+34600000000",
        "country": Country.ES,
        "applied_at": CONTACTED - timedelta(hours=1),
        "contacted_at": CONTACTED,
    }
    return Candidate(**(data | kwargs))


def at(hours: float) -> datetime:
    return CONTACTED + timedelta(hours=hours)


# --- Cuándo toca cada acción (función pura) ---------------------------------------

EVENING = datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)  # 19:00 Madrid, 11:00 CDMX
NIGHT = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)    # 22:00 Madrid, 14:00 CDMX


# Plazos (+2 h, +24 h, +72 h) desde que el candidato dejó de contestar, y solo de 9:00 a 21:00 locales.
@pytest.mark.parametrize("state, now, expected", [
    ({}, at(1.5), None),
    ({}, at(2), ReminderAction.FIRST_REMINDER),
    ({"reminders_sent": 1}, at(10), None),
    ({"reminders_sent": 1}, at(24), ReminderAction.SECOND_REMINDER),
    ({"reminders_sent": 2}, at(71), None),
    ({"reminders_sent": 2}, at(72), ReminderAction.CLOSE),
    ({"reminders_sent": 3}, at(200), None),                                   # secuencia terminada
    ({"last_candidate_message_at": at(5)}, at(6), None),                      # cuenta desde su último mensaje
    ({"last_candidate_message_at": at(5)}, at(7), ReminderAction.FIRST_REMINDER),
    ({"contacted_at": EVENING}, NIGHT, None),                                 # 22:00 en Madrid: se espera
    ({"contacted_at": EVENING}, datetime(2026, 10, 16, 7, 0, tzinfo=timezone.utc), ReminderAction.FIRST_REMINDER),
    ({"contacted_at": EVENING, "country": Country.MX}, NIGHT, ReminderAction.FIRST_REMINDER),  # 14:00 en México
])
def test_due_action(state, now, expected):
    assert due_action(candidate(**state), now) == expected


# --- El job contra la base de datos ---------------------------------------------------

@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    return conn


def add(conn, c: Candidate) -> None:
    with conn:
        CandidateRepository(conn).create(c)


def run(conn, hours: float):
    return ReengagementService(conn, now=lambda: at(hours)).run_reminders()


def test_full_sequence_ends_in_abandoned(conn):
    add(conn, candidate())

    assert [s.action for s in run(conn, 2)] == [ReminderAction.FIRST_REMINDER]
    assert run(conn, 3) == []  # ya enviado: no se repite
    assert [s.action for s in run(conn, 24)] == [ReminderAction.SECOND_REMINDER]
    assert [s.action for s in run(conn, 73)] == [ReminderAction.CLOSE]

    stored = CandidateRepository(conn).get("c1")
    assert stored.outcome == Outcome.ABANDONED
    assert stored.stage == Stage.IDENTITY  # se guarda la etapa alcanzada
    texts = [m.text for m in MessageRepository(conn).list_by_candidate("c1")]
    assert len(texts) == 3
    assert texts[0].startswith("Hola, Ana.")
    assert "cerramos tu solicitud" in texts[2]
    assert run(conn, 200) == []  # abandonado: ya no es una conversación abierta


def test_skips_not_contacted_and_finished_conversations(conn):
    add(conn, candidate(id="not_contacted", contacted_at=None))
    add(conn, candidate(id="in_review", outcome=Outcome.MANUAL_REVIEW, stage=Stage.DONE))
    add(conn, candidate(id="disqualified", outcome=Outcome.DISQUALIFIED, stage=Stage.DONE))
    add(conn, candidate(id="open"))
    assert [s.candidate_id for s in run(conn, 2)] == ["open"]

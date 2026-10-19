"""Resumen post-conversación con una SQLite en memoria y un LLM falso."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.clients.aws_client import LLMError
from app.models.candidate import Candidate
from app.models.enums import Country, Outcome, Stage
from app.models.message import Message
from app.prompts import summary as prompt
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.database import get_connection, init_db
from app.repositories.message_repository import MessageRepository
from app.services.summary_service import SummaryService

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
NOTES = {
    "context": ["Estudia en Sevilla; por eso prefiere media jornada"],
    "open_questions": ["Cuánto se paga la hora"],
    "alerts": [],
}


class FakeLLM:
    def __init__(self, result=NOTES):
        self.result = result
        self.calls = []

    def generate_structured(self, system, messages, tool, **kwargs):
        self.calls.append({"prompt": messages[0]["content"][0]["text"], "tool": tool, **kwargs})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    c = Candidate(
        id="c1", full_name="Ana López", phone="x", country=Country.ES,
        applied_at=NOW - timedelta(hours=2), contacted_at=NOW - timedelta(hours=1),
        last_candidate_message_at=NOW - timedelta(minutes=36),
        stage=Stage.DONE, outcome=Outcome.QUALIFIED,
        fields={"identity_confirmed": "yes", "license": "yes", "city": "Sevilla", "availability": "part_time"},
    )
    with conn:
        CandidateRepository(conn).create(c)
        for role, text in [("agent", "¿Hablo con Ana López?"), ("candidate", "sí, estudio en Sevilla"),
                           ("candidate", "¿cuánto se paga la hora?")]:
            MessageRepository(conn).add(Message(candidate_id="c1", role=role, text=text, created_at=NOW))
    return conn


def test_summary_has_key_data_from_code_and_notes_from_the_llm(conn):
    llm = FakeLLM()
    s = SummaryService(conn, llm, now=lambda: NOW).generate("c1")

    assert s.outcome == Outcome.QUALIFIED and s.fields.city == "Sevilla"
    assert s.message_count == 3
    assert s.duration_minutes == 24
    assert s.notes.open_questions == ["Cuánto se paga la hora"]

    call = llm.calls[0]
    assert call["tool"] == prompt.TOOL and call["temperature"] == 0.0
    assert "Candidato: sí, estudio en Sevilla" in call["prompt"]
    assert '"city":"Sevilla"' in call["prompt"]  # le pasamos los datos para que no los repita


def test_summary_is_stored_and_regenerating_overwrites_it(conn):
    service = SummaryService(conn, FakeLLM(), now=lambda: NOW)
    service.generate("c1")
    assert service.get("c1").notes.context == NOTES["context"]

    later = SummaryService(conn, FakeLLM({"context": [], "open_questions": [], "alerts": ["Revisar carné"]}), now=lambda: NOW + timedelta(hours=1))
    later.generate("c1")
    stored = service.get("c1")
    assert stored.notes.alerts == ["Revisar carné"]
    assert stored.generated_at == NOW + timedelta(hours=1)


@pytest.mark.parametrize("failure", [LLMError("Bedrock caído"), {"context": "no es una lista"}])
def test_llm_failure_keeps_the_key_data(conn, failure):
    s = SummaryService(conn, FakeLLM(failure), now=lambda: NOW).generate("c1")
    assert s.notes is None
    assert s.outcome == Outcome.QUALIFIED
    assert SummaryService(conn, FakeLLM(), now=lambda: NOW).get("c1") is not None  # se guardó igual


def test_no_llm_call_if_the_candidate_never_answered(conn):
    with conn:
        CandidateRepository(conn).create(Candidate(id="c2", full_name="Luis", phone="x", country=Country.MX,
                                                   applied_at=NOW, contacted_at=NOW, outcome=Outcome.ABANDONED))
    llm = FakeLLM()
    s = SummaryService(conn, llm, now=lambda: NOW).generate("c2")
    assert llm.calls == [] and s.notes.context == [] and s.duration_minutes is None

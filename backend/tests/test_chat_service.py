"""Tests de handle_message con una SQLite en memoria, un reloj fijo y un agente
falso (sin red). El agente falso ejecuta la máquina de estados real con los
campos que se le indiquen, como haría la tool record_answer."""

import sqlite3
from datetime import datetime, timezone

import pytest

from app.clients.aws_client import LLMError
from app.models.candidate import Candidate, ExtractedFields
from app.models.enums import Channel, Country, Identity, Outcome, Stage
from app.models.message import Message
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.database import get_connection, init_db
from app.repositories.message_repository import MessageRepository
from app.services.agent_service import AgentTurn
from app.services.chat_service import FALLBACK_REPLY, ChatService
from app.services.state_machine import advance

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)


class FakeAgent:
    def __init__(self, extracted: ExtractedFields | Exception = ExtractedFields()):
        self.extracted = extracted
        self.calls = []

    def respond(self, candidate, history, text, today, channel=Channel.TEXT):
        self.calls.append((candidate, history, text, today))
        if isinstance(self.extracted, Exception):
            raise self.extracted
        decision = advance(candidate, self.extracted, text, today)
        return AgentTurn(decision=decision, reply=f"respuesta para {decision.action}:{decision.stage}")


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_db(conn)
    candidate = Candidate(
        id="c1",
        full_name="Ana López",
        phone="+34600000000",
        country=Country.ES,
        applied_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        reminders_sent=2,
    )
    with conn:
        CandidateRepository(conn).create(candidate)
    return conn


def make_service(conn, extracted=ExtractedFields()) -> ChatService:
    return ChatService(conn, FakeAgent(extracted), now=lambda: NOW)


def stored_messages(conn):
    return [(m.role, m.text) for m in MessageRepository(conn).list_by_candidate("c1")]


def test_turn_gives_the_agent_the_whole_conversation_and_saves_messages_and_state(conn):
    with conn:
        MessageRepository(conn).add(Message(candidate_id="c1", role="agent", text="¿Hablo con Ana López?", created_at=NOW))
    service = make_service(conn, ExtractedFields(identity_confirmed=Identity.YES))

    reply = service.handle_message("c1", "sí, soy yo").reply

    _, history, text, today = service.agent.calls[0]
    assert [m.text for m in history] == ["¿Hablo con Ana López?", "sí, soy yo"]  # incluido el mensaje nuevo
    assert text == "sí, soy yo" and today == NOW.date()
    assert stored_messages(conn) == [("agent", "¿Hablo con Ana López?"), ("candidate", "sí, soy yo"), ("agent", reply)]
    stored = CandidateRepository(conn).get("c1")
    assert stored.stage == Stage.LICENSE
    assert stored.fields.identity_confirmed == Identity.YES
    assert stored.last_candidate_message_at == NOW
    assert stored.reminders_sent == 0  # ha contestado: los recordatorios empiezan de nuevo


def test_llm_failure_keeps_state_and_asks_to_repeat(conn):
    service = make_service(conn, LLMError("Bedrock caído"))

    reply = service.handle_message("c1", "sí, soy yo").reply

    assert reply == FALLBACK_REPLY["es"]
    assert stored_messages(conn) == [("candidate", "sí, soy yo"), ("agent", reply)]
    stored = CandidateRepository(conn).get("c1")
    assert stored.stage == Stage.IDENTITY  # no avanza
    assert stored.reask_counts == {}       # no gasta una repregunta
    assert stored.reminders_sent == 2


def test_abandoned_candidate_who_comes_back_resumes_where_they_left(conn):
    repo = CandidateRepository(conn)
    c = repo.get("c1")
    c.outcome = Outcome.ABANDONED
    with conn:
        repo.update(c)

    make_service(conn, ExtractedFields(identity_confirmed=Identity.YES)).handle_message("c1", "perdón, sí soy yo")

    stored = repo.get("c1")
    assert stored.outcome is None
    assert stored.stage == Stage.LICENSE


def test_result_says_when_the_conversation_closes(conn):
    still_open = make_service(conn, ExtractedFields(identity_confirmed=Identity.YES)).handle_message("c1", "sí")
    assert not still_open.closed
    closing = make_service(conn, ExtractedFields(license="no")).handle_message("c1", "no tengo")
    assert not closing.closed  # primero se confirma: todavía no se cierra
    closed = make_service(conn, ExtractedFields(license="no")).handle_message("c1", "no, ninguno")
    assert closed.closed
    after = make_service(conn, ExtractedFields()).handle_message("c1", "gracias")
    assert not after.closed  # ya estaba cerrada: no se vuelve a pedir el resumen

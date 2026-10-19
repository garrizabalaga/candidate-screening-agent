"""Tests de los endpoints con TestClient, una SQLite en memoria y un LLM falso.

Recorren el flujo de la demo: alta -> tabla -> botón de primer contacto ->
chat del candidato.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories.database import get_connection, init_db
from app.tools import record_answer
from app.routes.dependencies import get_agent_llm, get_db, get_summary_job


class FakeLLM:
    """Agente falso: en el paso forzado registra la identidad confirmada; después
    escribe una respuesta fija."""

    def converse(self, system, messages, tools=None, force_tool=None, **kwargs):
        if force_tool == record_answer.NAME:
            args = {"identity_confirmed": "yes", "language": "es"}
            return [{"toolUse": {"toolUseId": "t1", "name": record_answer.NAME, "input": args}}]
        return [{"text": "Gracias, Ana. ¿Tienes carné de conducir?"}]


summaries_requested: list[str] = []


@pytest.fixture
def client():
    conn = get_connection(":memory:")
    init_db(conn)
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[get_agent_llm] = lambda: FakeLLM()
    summaries_requested.clear()
    app.dependency_overrides[get_summary_job] = lambda: summaries_requested.append  # sin LLM ni BD real
    yield TestClient(app)
    app.dependency_overrides.clear()
    conn.close()


def create(client, name="Ana López", country="ES"):
    r = client.post("/candidates", json={"full_name": name, "phone": "+34600000000", "country": country})
    assert r.status_code == 201
    return r.json()


def test_demo_flow(client):
    # 1. Alta: queda pendiente de contacto.
    ana = create(client)
    assert ana["contacted_at"] is None and ana["stage"] == "identity"

    # 2. Tabla.
    assert [c["id"] for c in client.get("/candidates").json()] == [ana["id"]]

    # 3. Botón: el job escribe la apertura.
    assert client.post("/jobs/first-contact").json() == {"contacted": [ana["id"]]}
    messages = client.get(f"/candidates/{ana['id']}/messages").json()
    assert [m["role"] for m in messages] == ["agent"]
    assert "¿hablo con Ana López?" in messages[0]["text"]

    # 4. El candidato contesta.
    r = client.post("/chat", json={"candidate_id": ana["id"], "text": "sí, soy yo"})
    assert r.json() == {"reply": "Gracias, Ana. ¿Tienes carné de conducir?"}
    messages = client.get(f"/candidates/{ana['id']}/messages").json()
    assert [m["role"] for m in messages] == ["agent", "candidate", "agent"]

    candidate = client.get("/candidates").json()[0]
    assert candidate["stage"] == "license"
    assert candidate["contacted_at"] is not None


def test_unknown_candidate_returns_404(client):
    assert client.get("/candidates/nope/messages").status_code == 404
    assert client.post("/chat", json={"candidate_id": "nope", "text": "hola"}).status_code == 404


def test_reminders_job_with_simulated_clock(client):
    ana = create(client)
    client.post("/jobs/first-contact")
    contacted_at = client.get("/candidates").json()[0]["contacted_at"]
    later = datetime.fromisoformat(contacted_at) + timedelta(hours=25)
    later = later.replace(hour=10)  # dentro de la franja de envío en Madrid (UTC+2 -> 12:00)
    r = client.post("/jobs/reminders", json={"now": later.isoformat()})
    assert r.json() == {"sent": [{"candidate_id": ana["id"], "action": "first_reminder"}]}
    roles = [m["role"] for m in client.get(f"/candidates/{ana['id']}/messages").json()]
    assert roles == ["agent", "agent"]


# --- Herramienta de recruiters ------------------------------------------------------

def book_interview(client, conn, candidate_id):
    """Reserva directamente el primer hueco libre (sin pasar por el chat)."""
    from app.repositories.candidate_repository import CandidateRepository
    from app.services.scheduling_service import SchedulingService

    scheduling = SchedulingService(conn)
    candidate = CandidateRepository(conn).get(candidate_id)
    slot = scheduling.available_slots(candidate, limit=1)[0]
    with conn:
        scheduling.book(candidate, slot)
    return slot


@pytest.fixture
def conn(client):
    return app.dependency_overrides[get_db]()


def test_candidates_table_shows_the_interview_and_recruiter(client, conn):
    ana = create(client)
    luis = create(client, "Luis Pérez")
    slot = book_interview(client, conn, ana["id"])

    rows = {c["id"]: c for c in client.get("/candidates").json()}
    interview = rows[ana["id"]]["interview"]
    assert interview["recruiter_id"] == slot.recruiter_id
    assert interview["recruiter_name"] == slot.recruiter_name
    assert datetime.fromisoformat(interview["starts_at"]) == slot.starts_at
    assert rows[luis["id"]]["interview"] is None


def test_recruiters_and_their_calendar(client, conn):
    ana = create(client)
    slot = book_interview(client, conn, ana["id"])

    calendar = client.get(f"/recruiters/{slot.recruiter_id}/interviews").json()
    assert [e["candidate"]["full_name"] for e in calendar] == ["Ana López"]
    assert calendar[0]["interview"]["recruiter_name"] == slot.recruiter_name

    assert len(client.get("/recruiters").json()) == 13  # los de data/recruiters.json
    assert {r["country"] for r in client.get("/recruiters?country=MX").json()} == {"MX"}
    other = next(r["id"] for r in client.get("/recruiters?country=ES").json() if r["id"] != slot.recruiter_id)
    assert client.get(f"/recruiters/{other}/interviews").json() == []

    day = slot.starts_at.date().isoformat()
    assert len(client.get(f"/recruiters/{slot.recruiter_id}/interviews?from={day}&to={day}").json()) == 1
    assert client.get("/recruiters/nope/interviews").status_code == 404


def test_review_endpoint(client, conn):
    from app.models.enums import Outcome, Stage
    from app.repositories.candidate_repository import CandidateRepository

    ana = create(client)
    assert client.post(f"/candidates/{ana['id']}/review", json={"decision": "approve"}).status_code == 409  # no está en revisión

    repo = CandidateRepository(conn)
    c = repo.get(ana["id"])
    c.stage, c.outcome = Stage.DONE, Outcome.MANUAL_REVIEW
    with conn:
        repo.update(c)

    r = client.post(f"/candidates/{ana['id']}/review", json={"decision": "reject"})
    assert r.status_code == 200 and "Gracias por tu paciencia" in r.json()["message_sent"]
    assert summaries_requested == [ana["id"]]  # descartar cierra la conversación: se pide el resumen
    assert client.get(f"/candidates/{ana['id']}/messages").json()[-1]["text"] == r.json()["message_sent"]
    assert client.post("/candidates/nope/review", json={"decision": "reject"}).status_code == 404
    assert client.post(f"/candidates/{ana['id']}/review", json={"decision": "maybe"}).status_code == 422


def test_summary_endpoints(client, conn):
    ana = create(client)
    no_summary = client.get(f"/candidates/{ana['id']}/summary")
    assert no_summary.status_code == 200 and no_summary.json() is None  # todavía no generado: no es un error
    assert client.get("/candidates/nope/summary").status_code == 404

    class SummaryLLM(FakeLLM):
        def generate_structured(self, system, messages, tool, **kwargs):
            return {"context": [], "open_questions": [], "alerts": []}

    app.dependency_overrides[get_agent_llm] = lambda: SummaryLLM()
    r = client.post(f"/candidates/{ana['id']}/summary")
    assert r.status_code == 200 and r.json()["candidate_id"] == ana["id"]
    assert client.get(f"/candidates/{ana['id']}/summary").json()["generated_at"] == r.json()["generated_at"]
    assert client.post("/candidates/nope/summary").status_code == 404

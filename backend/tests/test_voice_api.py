"""Tests del endpoint de voz (Custom LLM de ElevenLabs) con TestClient, una SQLite
en memoria y un LLM falso."""

import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories.database import get_connection, init_db
from app.routes import voice
from app.routes.dependencies import get_agent_llm, get_db, get_summary_job, get_voice_requests
from app.services.voice_requests import VoiceRequests  # una instancia limpia por test
from app.tools import record_answer

URL = "/voice/llm/chat/completions"


class FakeLLM:
    """En el paso forzado registra la identidad (sí o no); después escribe una respuesta fija."""

    def __init__(self, identity="yes"):
        self.identity = identity
        self.forced_texts = []

    def converse(self, system, messages, tools=None, force_tool=None, **kwargs):
        if force_tool == record_answer.NAME:
            self.forced_texts.append(messages[-1]["content"][0]["text"])
            self.system = system
            args = {"identity_confirmed": self.identity, "language": "es"}
            return [{"toolUse": {"toolUseId": "t1", "name": record_answer.NAME, "input": args}}]
        return [{"text": "Gracias, Ana 😊 ¿Tienes carné de conducir?"}]


summaries_requested: list[str] = []


@pytest.fixture
def conn():
    conn = get_connection(":memory:")
    init_db(conn)
    yield conn
    conn.close()


def make_client(conn, llm) -> TestClient:
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[get_agent_llm] = lambda: llm
    summaries_requested.clear()
    app.dependency_overrides[get_summary_job] = lambda: summaries_requested.append
    requests = VoiceRequests()  # uno por test, para que no se mezclen las peticiones
    app.dependency_overrides[get_voice_requests] = lambda: requests
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides(monkeypatch):
    # Los tests no dependen del .env local.
    monkeypatch.setattr(voice, "VOICE_LLM_API_KEY", "")
    monkeypatch.setattr(voice, "VOICE_DEBOUNCE_SECONDS", 0)  # sin esperas en los tests
    yield
    app.dependency_overrides.clear()


def create_candidate(client) -> str:
    r = client.post("/candidates", json={"full_name": "Ana López", "phone": "+34600000000", "country": "ES"})
    return r.json()["id"]


def body(candidate_id, text="Sí, soy yo"):
    """Como las peticiones reales: el candidate_id va en el system prompt de ElevenLabs."""
    system = f"Task description: ...\n  Agent character description: candidate_id={candidate_id}\n  ..."
    return {
        "model": "guille",
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            {"role": "assistant", "content": "Hola, soy Guille. ¿Hablo con Ana López?"},
            {"role": "user", "content": text},
        ],
    }


def get_candidate(client, cid) -> dict:
    return next(c for c in client.get("/candidates").json() if c["id"] == cid)


def events(response) -> list:
    """Los eventos SSE: el JSON de cada chunk, o "[DONE]"."""
    data = [line.removeprefix("data: ") for line in response.text.splitlines() if line.startswith("data: ")]
    return [d if d == "[DONE]" else json.loads(d) for d in data]


def test_voice_turn_streams_the_reply_in_openai_format(conn):
    llm = FakeLLM()
    client = make_client(conn, llm)
    cid = create_candidate(client)

    r = client.post(URL, json=body(cid))

    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    reply, end, done = events(r)
    assert reply["object"] == "chat.completion.chunk"
    assert reply["choices"][0]["delta"]["content"] == "Gracias, Ana ¿Tienes carné de conducir?"  # sin emoji
    assert end["choices"][0] == {"index": 0, "delta": {}, "finish_reason": "stop"}
    assert done == "[DONE]"
    # Es el turno de siempre: solo el último mensaje del usuario, en modo voz, y queda guardado.
    assert llm.forced_texts[0].endswith("\nSí, soy yo") and "llamada de voz" in llm.system
    assert get_candidate(client, cid)["stage"] == "license"
    texts = [m["text"] for m in client.get(f"/candidates/{cid}/messages").json()]
    assert texts == ["Sí, soy yo", "Gracias, Ana ¿Tienes carné de conducir?"]  # nada del historial de ElevenLabs


def test_a_request_superseded_during_the_wait_is_not_processed(conn, monkeypatch):
    llm = FakeLLM()
    client = make_client(conn, llm)
    cid = create_candidate(client)
    requests = app.dependency_overrides[get_voice_requests]()
    # Mientras esta petición espera, llega otra del mismo candidato (la frase ampliada).
    monkeypatch.setattr(voice.time, "sleep", lambda seconds: requests.register(cid))

    r = client.post(URL, json=body(cid, "Sí, soy yo."))

    end, done = events(r)  # stream vacío: solo el cierre
    assert end["choices"][0]["finish_reason"] == "stop" and done == "[DONE]"
    assert llm.forced_texts == []  # ni Bedrock...
    assert client.get(f"/candidates/{cid}/messages").json() == []  # ...ni BD


def test_closing_by_voice_requests_the_summary(conn):
    client = make_client(conn, FakeLLM(identity="no"))
    cid = create_candidate(client)
    client.post(URL, json=body(cid, "No, te has equivocado"))
    assert summaries_requested == [cid]
    assert get_candidate(client, cid)["outcome"] == "wrong_contact"


def test_missing_or_unknown_candidate(conn):
    client = make_client(conn, FakeLLM())
    no_id = body("x")
    no_id["messages"][0]["content"] = "prompt sin la línea candidate_id"  # alguien la borró en ElevenLabs
    assert client.post(URL, json=no_id).status_code == 400
    assert client.post(URL, json=body("no-existe")).status_code == 404


def test_voice_link_redirects_to_elevenlabs_with_the_variables(conn, monkeypatch):
    monkeypatch.setattr(voice, "ELEVENLABS_AGENT_ID", "agent_123")
    client = make_client(conn, FakeLLM())
    cid = create_candidate(client)

    r = client.get(f"/voice/{cid}", follow_redirects=False)

    assert r.status_code == 307
    url = urlparse(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == voice.ELEVENLABS_TALK_TO_URL
    assert parse_qs(url.query) == {
        "agent_id": ["agent_123"],
        "var_candidate_id": [cid],
        "var_saludo": ["Hola, soy Guille, el asistente de selección de Grupo Sazón. Te llamo por tu solicitud como repartidor. ¿Hablo con Ana López?"],
    }


def test_voice_link_of_a_closed_conversation_or_unknown_candidate(conn):
    client = make_client(conn, FakeLLM(identity="no"))
    cid = create_candidate(client)
    client.post(URL, json=body(cid, "No, te has equivocado"))  # wrong_contact: se cierra
    closed = client.get(f"/voice/{cid}", follow_redirects=False)
    assert closed.status_code == 200 and "ya ha terminado" in closed.text  # no se abre la llamada
    assert client.get("/voice/no-existe").status_code == 404


def test_api_key_is_required_when_configured(conn, monkeypatch):
    monkeypatch.setattr(voice, "VOICE_LLM_API_KEY", "secreto")
    client = make_client(conn, FakeLLM())
    cid = create_candidate(client)
    assert client.post(URL, json=body(cid)).status_code == 401
    assert client.post(URL, json=body(cid), headers={"Authorization": "Bearer otro"}).status_code == 401
    assert client.post(URL, json=body(cid), headers={"Authorization": "Bearer secreto"}).status_code == 200

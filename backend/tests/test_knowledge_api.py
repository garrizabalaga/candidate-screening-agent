"""Tests de los endpoints de la base de conocimiento con TestClient, SQLite en memoria y embeddings falsos."""

import pytest
from fastapi.testclient import TestClient

from app.clients.aws_client import LLMError
from app.main import app
from app.repositories.database import get_connection, init_db
from app.routes.dependencies import get_db, get_embeddings

FAQ = "# Sobre la empresa\n## ¿Necesito vehículo propio?\nSí, coche o moto.\n\n## ¿Dais uniforme?\nSí.\n"


class FakeEmbeddings:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def embed(self, text: str) -> list[float]:
        if self.fail:
            raise LLMError("Bedrock caído")
        return [1.0, 0.0]


@pytest.fixture
def client():
    conn = get_connection(":memory:")
    init_db(conn)
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[get_embeddings] = lambda: FakeEmbeddings()
    yield TestClient(app)
    app.dependency_overrides.clear()
    conn.close()


def upload(client, content: str, filename: str = "faq.md"):
    return client.post("/knowledge", files={"file": (filename, content.encode("utf-8"), "text/markdown")})


def test_upload_and_list(client):
    assert upload(client, FAQ).json() == {"source": "faq.md", "questions": 2}
    entries = client.get("/knowledge").json()
    assert [e["question"] for e in entries] == ["¿Necesito vehículo propio?", "¿Dais uniforme?"]
    assert "embedding" not in entries[0]


def test_invalid_uploads_keep_the_previous_faq(client):
    upload(client, FAQ)
    assert upload(client, FAQ, "faq.docx").status_code == 400
    assert upload(client, "Un texto sin preguntas", "notas.md").status_code == 400
    app.dependency_overrides[get_embeddings] = lambda: FakeEmbeddings(fail=True)
    assert upload(client, FAQ, "faq_v2.md").status_code == 503
    assert {e["source"] for e in client.get("/knowledge").json()} == {"faq.md"}

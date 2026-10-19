"""Tests de la base de conocimiento con embeddings falsos (un vector por tema) y una SQLite en memoria."""

from app.repositories.database import get_connection, init_db
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.knowledge_service import KnowledgeService, parse_faq

FAQ = """# Sobre la empresa
Texto suelto que no es una pregunta.

## ¿Necesito vehículo propio?
Sí, coche o moto.
Con el seguro en regla.

# El puesto
## ¿Dais uniforme?
Sí, uniforme y mochila.

## ¿Pregunta sin respuesta?
"""


class FakeEmbeddings:
    def embed(self, text: str) -> list[float]:
        text = text.lower()
        if "moto" in text or "vehículo" in text:
            return [1.0, 0.0]
        if "uniforme" in text:
            return [0.0, 1.0]
        return [0.1, 0.1]


def test_faq_is_split_by_question():
    assert parse_faq(FAQ) == [
        ("¿Necesito vehículo propio?", "Sí, coche o moto.\nCon el seguro en regla."),
        ("¿Dais uniforme?", "Sí, uniforme y mochila."),
    ]


def test_ingest_replaces_the_base_and_search_finds_the_closest_answer():
    conn = get_connection(":memory:")
    init_db(conn)
    service = KnowledgeService(conn, FakeEmbeddings())
    service.ingest("faq.md", FAQ)

    assert [m.question for m in service.search("¿tengo que llevar mi moto?")] == ["¿Necesito vehículo propio?"]
    assert service.search("¿cuánto se cobra?") == []  # por debajo del umbral: no hay información

    service.ingest("faq_v2.md", "## ¿Dais uniforme?\nSí.")
    assert [c.source for c in KnowledgeRepository(conn).list_all()] == ["faq_v2.md"]

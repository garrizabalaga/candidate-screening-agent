"""Base de conocimiento (RAG del FAQ): cargar el documento y buscar en él.

Similitud coseno = producto escalar, porque Titan devuelve vectores normalizados.
"""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from app.clients.aws_client import EmbeddingClient
from app.models.knowledge import KnowledgeChunk, KnowledgeMatch
from app.repositories.knowledge_repository import KnowledgeRepository

TOP_K = 3
# Calibrado con el FAQ de ejemplo: lo que el FAQ responde puntúa 0,27-0,74; lo que no, 0,24 como mucho.
MIN_SCORE = 0.25


class InvalidFaq(Exception):
    """El documento no tiene ninguna pregunta (`## ...`) con respuesta."""


class KnowledgeService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        embeddings: EmbeddingClient,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.conn = conn
        self.repo = KnowledgeRepository(conn)
        self.embeddings = embeddings
        self.now = now

    def ingest(self, source: str, markdown: str) -> list[KnowledgeChunk]:
        """Sustituye la base por este FAQ. Los embeddings se calculan antes de abrir la
        transacción: si Bedrock falla a mitad, la base anterior queda intacta."""
        sections = parse_faq(markdown)
        if not sections:
            raise InvalidFaq(source)
        now = self.now()
        chunks = [
            KnowledgeChunk(source=source, question=q, answer=a, embedding=self.embeddings.embed(f"{q}\n{a}"), created_at=now)
            for q, a in sections
        ]
        with self.conn:
            self.repo.replace_all(chunks)
        return chunks

    def search(self, question: str) -> list[KnowledgeMatch]:
        """Los TOP_K trozos más parecidos que superan MIN_SCORE (vacío si ninguno)."""
        chunks = self.repo.list_all()
        if not chunks:
            return []
        query = self.embeddings.embed(question)
        scored = sorted(((_dot(query, c.embedding), c) for c in chunks), key=lambda pair: pair[0], reverse=True)
        return [
            KnowledgeMatch(question=c.question, answer=c.answer, score=round(score, 3))
            for score, c in scored[:TOP_K]
            if score >= MIN_SCORE
        ]


def parse_faq(markdown: str) -> list[tuple[str, str]]:
    """[(pregunta, respuesta)]: cada `## Pregunta` con el texto que la sigue. Los `#` y el
    texto fuera de una pregunta se ignoran."""
    sections: list[tuple[str, list[str]]] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif line.startswith("#"):
            sections.append(("", []))
        elif sections:
            sections[-1][1].append(line)
    return [(q, "\n".join(lines).strip()) for q, lines in sections if q and "".join(lines).strip()]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

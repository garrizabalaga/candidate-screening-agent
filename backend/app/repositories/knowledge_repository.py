"""La base de conocimiento en la tabla `knowledge_chunks`.

SQLite no sabe de vectores: el embedding se guarda como JSON y la similitud se
calcula en Python. Con decenas de preguntas es exacto e instantáneo; con miles,
esto pasaría a un índice vectorial (FAISS, sqlite-vec) detrás de esta interfaz.
"""

import json
import sqlite3
from datetime import datetime

from app.models.knowledge import KnowledgeChunk


class KnowledgeRepository:
    """No hace commit: la transacción la controla el servicio."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def replace_all(self, chunks: list[KnowledgeChunk]) -> None:
        self.conn.execute("DELETE FROM knowledge_chunks")
        self.conn.executemany(
            "INSERT INTO knowledge_chunks (source, question, answer, embedding, created_at) VALUES (?, ?, ?, ?, ?)",
            [(c.source, c.question, c.answer, json.dumps(c.embedding), c.created_at.isoformat()) for c in chunks],
        )

    def list_all(self) -> list[KnowledgeChunk]:
        rows = self.conn.execute("SELECT * FROM knowledge_chunks ORDER BY id").fetchall()
        return [
            KnowledgeChunk(
                source=r["source"],
                question=r["question"],
                answer=r["answer"],
                embedding=json.loads(r["embedding"]),
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

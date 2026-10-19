"""Lectura y escritura del historial en la tabla `messages`."""

import sqlite3

from app.models.message import Message


class MessageRepository:
    """No hace commit: la transacción la controla el servicio."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add(self, message: Message) -> None:
        self.conn.execute(
            "INSERT INTO messages (candidate_id, role, text, created_at) VALUES (?, ?, ?, ?)",
            (message.candidate_id, message.role, message.text, message.created_at.isoformat()),
        )

    def list_by_candidate(self, candidate_id: str) -> list[Message]:
        """Conversación completa, en orden."""
        rows = self.conn.execute(
            "SELECT candidate_id, role, text, created_at FROM messages WHERE candidate_id = ? ORDER BY id",
            (candidate_id,),
        ).fetchall()
        return [Message.model_validate(dict(row)) for row in rows]

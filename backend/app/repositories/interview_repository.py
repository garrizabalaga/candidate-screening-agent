"""Lectura y escritura de entrevistas en la tabla `interviews`.

Las fechas se guardan en UTC con el mismo formato, así se pueden comparar como
texto (rangos y el índice único) sin ambigüedades de zona horaria.
"""

import sqlite3
from datetime import datetime, timezone

from app.models.interview import Interview


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class InterviewRepository:
    """No hace commit: la transacción la controla el servicio."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, interview: Interview) -> None:
        """Lanza sqlite3.IntegrityError si el hueco o el candidato ya tienen entrevista."""
        self.conn.execute(
            "INSERT INTO interviews (id, candidate_id, recruiter_id, starts_at, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                interview.id,
                interview.candidate_id,
                interview.recruiter_id,
                _utc(interview.starts_at),
                interview.status,
                _utc(interview.created_at),
            ),
        )

    def scheduled_between(self, start: datetime, end: datetime, recruiter_id: str | None = None) -> list[Interview]:
        """Entrevistas activas en [start, end), de un recruiter o de todos, por fecha."""
        sql = "SELECT * FROM interviews WHERE status = 'scheduled' AND starts_at >= ? AND starts_at < ?"
        params: list = [_utc(start), _utc(end)]
        if recruiter_id is not None:
            sql += " AND recruiter_id = ?"
            params.append(recruiter_id)
        rows = self.conn.execute(sql + " ORDER BY starts_at", params).fetchall()
        return [Interview.model_validate(dict(row)) for row in rows]

    def scheduled_by_candidate(self) -> dict[str, Interview]:
        """Entrevista activa de cada candidato que tiene una (para la tabla del dashboard)."""
        rows = self.conn.execute("SELECT * FROM interviews WHERE status = 'scheduled'").fetchall()
        return {row["candidate_id"]: Interview.model_validate(dict(row)) for row in rows}

    def scheduled_for_candidate(self, candidate_id: str) -> Interview | None:
        row = self.conn.execute(
            "SELECT * FROM interviews WHERE candidate_id = ? AND status = 'scheduled'", (candidate_id,)
        ).fetchone()
        return Interview.model_validate(dict(row)) if row else None

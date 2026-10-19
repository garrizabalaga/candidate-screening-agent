"""Lectura y escritura de resúmenes en la tabla `summaries` (el resumen va entero en JSON:
se lee y se muestra completo, no se filtra por sus campos)."""

import sqlite3

from app.models.summary import Summary


class SummaryRepository:
    """No hace commit: la transacción la controla el servicio."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def save(self, summary: Summary) -> None:
        self.conn.execute(
            "INSERT INTO summaries (candidate_id, data, generated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(candidate_id) DO UPDATE SET data = excluded.data, generated_at = excluded.generated_at",
            (summary.candidate_id, summary.model_dump_json(), summary.generated_at.isoformat()),
        )

    def get(self, candidate_id: str) -> Summary | None:
        row = self.conn.execute("SELECT data FROM summaries WHERE candidate_id = ?", (candidate_id,)).fetchone()
        return Summary.model_validate_json(row["data"]) if row else None

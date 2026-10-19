"""Lectura y escritura de candidatos en la tabla `candidates`.

Traduce entre el modelo Candidate (con los campos del screening anidados en
`fields`) y una fila plana de la tabla. Pydantic hace la conversión de tipos:
model_dump(mode="json") pasa enums y fechas a texto, y model_validate() los
vuelve a convertir al leer.
"""

import json
import sqlite3
from datetime import datetime, timezone

from app.models.candidate import Candidate, ScreeningFields

# Columnas de la tabla que vienen de Candidate.fields.
FIELD_COLUMNS = list(ScreeningFields.model_fields)

# Columnas guardadas como JSON.
JSON_COLUMNS = ["experience", "reask_counts", "review_flags", "offered_slots"]

COLUMNS = [
    "id",
    "full_name",
    "phone",
    "country",
    "applied_at",
    "contacted_at",
    "stage",
    "language",
    "outcome",
    "disqualification_reason",
    *FIELD_COLUMNS,
    "reask_counts",
    "review_flags",
    "pending_confirmation",
    "offered_slots",
    "last_candidate_message_at",
    "reminders_sent",
    "reopened_at",
    "updated_at",
]


class CandidateRepository:
    """No hace commit: la transacción la abre y la cierra el servicio (`with conn:`),
    para poder guardar varias cosas juntas (estado + mensajes) o ninguna."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, candidate: Candidate) -> None:
        """Registra una aplicación nueva."""
        placeholders = ", ".join(f":{col}" for col in COLUMNS)
        self.conn.execute(
            f"INSERT INTO candidates ({', '.join(COLUMNS)}) VALUES ({placeholders})",
            _to_row(candidate),
        )

    def get(self, candidate_id: str) -> Candidate | None:
        row = self.conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        return _from_row(row) if row else None

    def update(self, candidate: Candidate) -> None:
        """Guarda el estado completo del candidato (se llama tras cada turno)."""
        assignments = ", ".join(f"{col} = :{col}" for col in COLUMNS if col != "id")
        self.conn.execute(f"UPDATE candidates SET {assignments} WHERE id = :id", _to_row(candidate))

    def list_all(self) -> list[Candidate]:
        """Todos los candidatos, los más recientes primero."""
        rows = self.conn.execute("SELECT * FROM candidates ORDER BY applied_at DESC").fetchall()
        return [_from_row(row) for row in rows]

    def list_open_conversations(self) -> list[Candidate]:
        """Conversaciones en curso: ya contactadas y sin resultado. Excluye las que
        esperan a un recruiter (manual_review), porque tienen resultado."""
        rows = self.conn.execute(
            "SELECT * FROM candidates WHERE contacted_at IS NOT NULL AND outcome IS NULL"
        ).fetchall()
        return [_from_row(row) for row in rows]

    def list_pending_contact(self) -> list[Candidate]:
        """Candidatos a los que el job de primer contacto todavía no ha escrito."""
        rows = self.conn.execute(
            "SELECT * FROM candidates WHERE contacted_at IS NULL ORDER BY applied_at"
        ).fetchall()
        return [_from_row(row) for row in rows]


def _to_row(candidate: Candidate) -> dict:
    data = candidate.model_dump(mode="json")
    row = {col: data.get(col) for col in COLUMNS}
    for col in FIELD_COLUMNS:
        row[col] = data["fields"][col]
    for col in JSON_COLUMNS:
        if row[col] is not None:
            row[col] = json.dumps(row[col], ensure_ascii=False)
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    return row


def _from_row(row: sqlite3.Row) -> Candidate:
    data = dict(row)
    for col in JSON_COLUMNS:
        if data[col] is not None:
            data[col] = json.loads(data[col])
    data["fields"] = {col: data.pop(col) for col in FIELD_COLUMNS}
    data.pop("updated_at")
    return Candidate.model_validate(data)

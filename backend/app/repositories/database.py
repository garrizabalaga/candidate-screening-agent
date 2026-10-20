"""Conexión a SQLite y esquema de la base de datos."""

import sqlite3
from pathlib import Path

from app.core.config import DATABASE_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id                          TEXT PRIMARY KEY,

    -- Datos de la aplicación
    full_name                   TEXT NOT NULL,
    phone                       TEXT NOT NULL,
    country                     TEXT NOT NULL,      -- ES | MX
    applied_at                  TEXT NOT NULL,      -- ISO 8601

    -- NULL = el job de primer contacto todavía no le ha escrito
    contacted_at                TEXT,

    -- Estado de la conversación
    stage                       TEXT NOT NULL,
    language                    TEXT NOT NULL,      -- es | en
    outcome                     TEXT,               -- NULL mientras está en curso
    disqualification_reason     TEXT,

    -- Campos del screening (una columna por campo para poder filtrar)
    identity_confirmed          TEXT,
    license                     TEXT,
    city                        TEXT,
    availability                TEXT,
    schedule                    TEXT,
    experience                  TEXT,               -- JSON: {"years", "platforms"} o "unclear"
    start_date                  TEXT,               -- ISO, "immediately" o "unclear"

    -- Control del flujo (JSON; solo lo usa la máquina de estados)
    reask_counts                TEXT NOT NULL DEFAULT '{}',
    review_flags                TEXT NOT NULL DEFAULT '[]',
    pending_confirmation        TEXT,               -- eliminatoria pendiente de confirmar
    offered_slots               TEXT NOT NULL DEFAULT '[]',  -- huecos ofrecidos (JSON)

    -- Recordatorios
    last_candidate_message_at   TEXT,
    reminders_sent              INTEGER NOT NULL DEFAULT 0,
    reopened_at                 TEXT,               -- un recruiter reabrió la conversación

    updated_at                  TEXT NOT NULL
);

-- Historial de la conversación. Solo se añaden filas, nunca se modifican.
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,  -- orden de los mensajes
    candidate_id    TEXT NOT NULL REFERENCES candidates(id),
    role            TEXT NOT NULL,                      -- candidate | agent
    text            TEXT NOT NULL,
    created_at      TEXT NOT NULL                       -- ISO 8601
);

CREATE INDEX IF NOT EXISTS idx_messages_candidate ON messages (candidate_id, id);

-- Entrevistas reservadas. Los recruiters y sus plantillas viven en data/recruiters.json.
CREATE TABLE IF NOT EXISTS interviews (
    id              TEXT PRIMARY KEY,
    candidate_id    TEXT NOT NULL REFERENCES candidates(id),
    recruiter_id    TEXT NOT NULL,
    starts_at       TEXT NOT NULL,                      -- ISO 8601 en UTC
    status          TEXT NOT NULL,                      -- scheduled | cancelled
    created_at      TEXT NOT NULL
);

-- Resumen post-conversación (uno por candidato; se sobrescribe al regenerarlo).
CREATE TABLE IF NOT EXISTS summaries (
    candidate_id    TEXT PRIMARY KEY REFERENCES candidates(id),
    data            TEXT NOT NULL,                      -- JSON del modelo Summary
    generated_at    TEXT NOT NULL
);

-- Base de conocimiento (FAQ): una fila por pregunta, con su embedding. Se regenera al subir un FAQ.
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    embedding       TEXT NOT NULL,                      -- JSON
    created_at      TEXT NOT NULL
);

-- La base de datos garantiza que no hay dobles reservas, aunque dos candidatos
-- confirmen a la vez: un recruiter no puede tener dos entrevistas a la misma hora,
-- y un candidato no puede tener dos entrevistas activas. Índices parciales: una
-- entrevista cancelada libera el hueco.
CREATE UNIQUE INDEX IF NOT EXISTS uq_interviews_recruiter_slot
    ON interviews (recruiter_id, starts_at) WHERE status = 'scheduled';
CREATE UNIQUE INDEX IF NOT EXISTS uq_interviews_candidate
    ON interviews (candidate_id) WHERE status = 'scheduled';
"""


def get_connection(path: Path | str = DATABASE_PATH) -> sqlite3.Connection:
    """Una conexión nueva por petición.

    - row_factory: las filas se leen como diccionarios (row["city"]).
    - check_same_thread=False: FastAPI puede abrir la conexión en un hilo y usarla
      en otro dentro de la misma petición. Es seguro porque cada conexión la usa
      una sola petición, de forma secuencial.
    - foreign_keys: SQLite no comprueba las FK si no se activa en cada conexión.
    """
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Crea las tablas si no existen. Se puede llamar en cada arranque."""
    conn.executescript(SCHEMA)

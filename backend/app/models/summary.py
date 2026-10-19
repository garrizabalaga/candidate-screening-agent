"""Resumen post-conversación para el recruiter.

Dos partes:
- Datos clave: salen del estado, los escribe el código. No pueden estar mal.
- Notas: las extrae el LLM de la conversación (lo que no es un campo del
  screening). Son opcionales: si el LLM falla, el resumen se guarda sin ellas.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.candidate import ReviewFlag, ScreeningFields
from app.models.enums import DisqualificationReason, Outcome, Stage
from app.models.interview import InterviewSummary


class SummaryNotes(BaseModel):
    """Lo que el LLM extrae de la conversación. También es el esquema de su tool."""

    context: list[str] = Field(
        default_factory=list,
        description="Hechos que contó el candidato y que no son campos del screening (p. ej. que estudia). Frases cortas.",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Preguntas del candidato que el asistente no pudo responder; el recruiter debería traerlas preparadas.",
    )
    alerts: list[str] = Field(
        default_factory=list,
        description="Dudas, contradicciones o señales a confirmar en la entrevista.",
    )


class Summary(BaseModel):
    candidate_id: str
    generated_at: datetime

    # Datos clave (código).
    outcome: Outcome | None
    disqualification_reason: DisqualificationReason | None
    stage: Stage
    fields: ScreeningFields
    interview: InterviewSummary | None
    review_flags: list[ReviewFlag]
    message_count: int
    duration_minutes: float | None  # del primer contacto al último mensaje del candidato

    # Notas (LLM). None si no se pudieron generar.
    notes: SummaryNotes | None

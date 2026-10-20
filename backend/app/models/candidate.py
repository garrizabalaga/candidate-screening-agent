"""Modelos del candidato: datos de la aplicación, campos del screening y
estado de la conversación. Solo describen datos: la lógica de flujo vive en
services/state_machine.py."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import (
    Availability,
    Country,
    DisqualificationReason,
    Identity,
    Language,
    License,
    Outcome,
    Schedule,
    Stage,
)
from app.models.interview import InterviewSummary, Slot


class Experience(BaseModel):
    """Experiencia previa en reparto. 0 años es un valor válido."""

    years: float = Field(ge=0)
    platforms: list[str] = Field(default_factory=list)


# --- Campos del screening -----------------------------------------------------

class ScreeningFields(BaseModel):
    """Lo que se recoge en la conversación.

    None significa "todavía no lo sabemos". "unclear" significa que se preguntó
    y la respuesta no permitió decidir (solo en campos no eliminatorios, salvo
    identity_confirmed y license, donde el LLM lo usa para marcar una respuesta
    dudosa).

    El nombre no está aquí: viene de la aplicación y solo se confirma.
    city es la ciudad donde quiere repartir, no donde vive.
    """

    identity_confirmed: Identity | None = None
    license: License | None = None
    city: str | None = None
    availability: Availability | None = None
    schedule: Schedule | None = None
    experience: Experience | Literal["unclear"] | None = None
    start_date: date | Literal["immediately", "unclear"] | None = None


class ExtractedFields(ScreeningFields):
    """Lo que el LLM devuelve en cada turno: los campos que aparezcan en el
    mensaje (pueden ser varios) y el idioma detectado.

    language es None si el mensaje mezcla idiomas o no se puede saber; en ese
    caso se mantiene el idioma actual de la conversación.
    """

    language: Language | None = None

    # Pregunta que hace el candidato en este mensaje (resumida), si hace alguna.
    # No es un dato del screening: solo sirve para contestarla en este turno.
    candidate_question: str | None = None

    # Etapa scheduling: número del hueco ofrecido que elige (1, 2, 3...) o "none"
    # si dice que ninguno le va. No es un dato del screening: decide la reserva.
    chosen_slot: int | Literal["none"] | None = None

    # Insultos, acoso o contenido ofensivo hacia el asistente o la empresa: el caso pasa a un recruiter.
    inappropriate: bool = False

    # Cómo está el candidato: solo cambia el tono del mensaje, no la decisión.
    sentiment: Literal["neutral", "frustrated", "confused"] = "neutral"


# --- Estado de la conversación ------------------------------------------------

class ReviewFlag(BaseModel):
    """Motivo por el que el caso necesita revisión manual."""

    stage: Stage
    reason: str
    raw_answer: str  # respuesta literal del candidato, para el recruiter


class Candidate(BaseModel):
    """Estado completo de un candidato. Se guarda entero en la base de datos
    después de cada turno; nada vive solo en memoria."""

    id: str

    # Datos de la aplicación.
    full_name: str
    phone: str
    country: Country  # de la oferta a la que aplicó, no de la conversación
    applied_at: datetime

    # None = el job de primer contacto todavía no le ha escrito.
    contacted_at: datetime | None = None

    stage: Stage = Stage.IDENTITY
    fields: ScreeningFields = Field(default_factory=ScreeningFields)
    language: Language = Language.ES

    # Repreguntas hechas por etapa (máximo 2).
    reask_counts: dict[Stage, int] = Field(default_factory=dict)

    # Revisión manual: el flujo continúa, solo se marca el caso.
    review_flags: list[ReviewFlag] = Field(default_factory=list)

    # Eliminatoria pendiente de confirmar antes de descartar (license o zone).
    pending_confirmation: Stage | None = None

    # Huecos de entrevista ofrecidos, en el orden en que se presentaron: así
    # "el segundo" se traduce a un hueco concreto.
    offered_slots: list[Slot] = Field(default_factory=list)

    # Se rellenan al terminar la conversación.
    outcome: Outcome | None = None
    disqualification_reason: DisqualificationReason | None = None

    # Para el job de recordatorios.
    last_candidate_message_at: datetime | None = None
    reminders_sent: int = 0
    # Cuándo un recruiter reabrió la conversación (aprobó una revisión manual): los
    # recordatorios cuentan desde ahí, no desde el último mensaje del candidato.
    reopened_at: datetime | None = None


# --- Vistas para el dashboard ----------------------------------------------------

class CandidateOverview(Candidate):
    """Un candidato en la tabla de recruiters: su estado y, si la tiene, su entrevista."""

    interview: InterviewSummary | None = None


class CalendarEntry(BaseModel):
    """Una entrevista en el calendario de un recruiter, con el perfil del candidato."""

    interview: InterviewSummary
    candidate: Candidate

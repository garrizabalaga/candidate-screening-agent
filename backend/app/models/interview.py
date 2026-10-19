"""Modelos de la agenda: recruiters, huecos libres y entrevistas reservadas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.enums import Country


class Recruiter(BaseModel):
    id: str
    name: str
    country: Country  # entrevista por videollamada a candidatos de todo su país
    # Plantilla semanal en hora local del país: {"mon": ["10:00-13:00"], ...}
    weekly: dict[str, list[str]]


class Slot(BaseModel):
    """Un hueco libre: un recruiter a una hora concreta (datetime con zona horaria)."""

    recruiter_id: str
    recruiter_name: str
    starts_at: datetime


class InterviewSummary(BaseModel):
    """Lo que se muestra de una entrevista en las tablas del dashboard."""

    id: str
    starts_at: datetime
    recruiter_id: str
    recruiter_name: str


class Interview(BaseModel):
    id: str
    candidate_id: str
    recruiter_id: str
    starts_at: datetime
    status: Literal["scheduled", "cancelled"] = "scheduled"
    created_at: datetime

"""Base de conocimiento (FAQ): cada pregunta del documento con su respuesta y su vector."""

from datetime import datetime

from pydantic import BaseModel


class KnowledgeChunk(BaseModel):
    source: str
    question: str
    answer: str
    embedding: list[float]
    created_at: datetime


class KnowledgeEntry(BaseModel):
    """Lo que ve el dashboard: sin el vector."""

    source: str
    question: str
    answer: str
    created_at: datetime


class KnowledgeMatch(BaseModel):
    question: str
    answer: str
    score: float

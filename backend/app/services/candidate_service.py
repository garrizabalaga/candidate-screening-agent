"""Alta y consulta de candidatos."""

import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from app.core.exceptions import CandidateNotFound
from app.models.candidate import Candidate, CandidateOverview
from app.models.enums import Country
from app.models.message import Message
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.interview_repository import InterviewRepository
from app.repositories.message_repository import MessageRepository
from app.services.recruiter_service import summary


class CandidateService:
    def __init__(self, conn: sqlite3.Connection, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.conn = conn
        self.candidates = CandidateRepository(conn)
        self.messages = MessageRepository(conn)
        self.now = now

    def create(self, full_name: str, phone: str, country: Country) -> Candidate:
        """Registra una aplicación. Queda pendiente de primer contacto (contacted_at vacío)."""
        candidate = Candidate(
            id=uuid.uuid4().hex,  # aleatorio: no se puede adivinar el de otro candidato
            full_name=" ".join(full_name.split()),
            phone=phone.strip(),
            country=country,
            applied_at=self.now(),
        )
        with self.conn:
            self.candidates.create(candidate)
        return candidate

    def list_all(self) -> list[CandidateOverview]:
        """Todos los candidatos, con su entrevista si la tienen (para la tabla del dashboard)."""
        interviews = InterviewRepository(self.conn).scheduled_by_candidate()
        return [
            CandidateOverview(
                **c.model_dump(),
                interview=summary(interviews[c.id]) if c.id in interviews else None,
            )
            for c in self.candidates.list_all()
        ]

    def get_messages(self, candidate_id: str) -> list[Message]:
        if self.candidates.get(candidate_id) is None:
            raise CandidateNotFound(candidate_id)
        return self.messages.list_by_candidate(candidate_id)

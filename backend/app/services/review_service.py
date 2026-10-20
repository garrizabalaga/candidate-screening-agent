"""Revisión manual: un recruiter aprueba o descarta un caso dudoso.

La respuesta llega al candidato por el mismo chat (se le prometió en 24-48 h):
- Aprobar: se le ofrecen huecos y sigue como cualquier candidato apto.
- Descartar: mensaje de cierre.
"""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from app.core.exceptions import CandidateNotFound
from app.models.enums import Language
from app.models.message import Message
from app.prompts.fallbacks import fallback
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.message_repository import MessageRepository
from app.services.scheduling_service import SchedulingService
from app.services.state_machine import Action, resolve_review, with_slots

REJECTION = {
    Language.ES: "Hola, {name}. Gracias por tu paciencia. Hemos revisado tu solicitud y por ahora no podemos seguir adelante con ella. ¡Mucha suerte en tu búsqueda!",
    Language.EN: "Hi {name}. Thanks for your patience. We've reviewed your application and we can't move forward with it for now. Best of luck!",
}


class NotInReview(Exception):
    """El candidato no está pendiente de revisión manual."""


class NoSlotsAvailable(Exception):
    """Se quiere aprobar pero no hay huecos de entrevista libres."""


class ReviewService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        scheduling: SchedulingService,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.conn = conn
        self.candidates = CandidateRepository(conn)
        self.messages = MessageRepository(conn)
        self.scheduling = scheduling
        self.now = now

    def resolve(self, candidate_id: str, approve: bool) -> str:
        """Aplica la decisión, envía el mensaje al candidato y lo devuelve."""
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            raise CandidateNotFound(candidate_id)
        try:
            decision = resolve_review(candidate, approve)
        except ValueError as e:
            raise NotInReview(candidate_id) from e

        if approve:
            decision = with_slots(decision, self.scheduling.available_slots(decision.candidate))
            if decision.action != Action.OFFER_SLOTS:
                # Sin huecos libres: el caso sigue en revisión y no se le escribe nada.
                raise NoSlotsAvailable(candidate_id)
            text = fallback(decision)
        else:
            text = REJECTION[candidate.language].format(name=candidate.full_name.split()[0])

        now = self.now()
        updated = decision.candidate
        updated.reopened_at = now if approve else updated.reopened_at
        updated.reminders_sent = 0
        with self.conn:
            self.candidates.update(updated)
            self.messages.add(Message(candidate_id=candidate_id, role="agent", text=text, created_at=now))
        return text

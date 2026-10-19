"""Job de recordatorios: reengancha a los candidatos que dejan de contestar.

Como el primer contacto, no lo dispara ningún mensaje: en la demo lo lanza un
botón (POST /jobs/reminders, con un `now` simulado) y en producción un scheduler.

Referencia: el momento en que el candidato dejó de contestar, es decir, su
último mensaje o, si nunca ha contestado, la apertura. Los recordatorios no la
mueven, así que los plazos (+2 h, +24 h, +72 h) cuentan siempre desde ahí.
"""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel

from app.core.config import COUNTRY_TIMEZONE, REMINDER_SCHEDULE, SENDING_HOURS
from app.models.candidate import Candidate
from app.models.enums import Language, Outcome
from app.models.message import Message
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.message_repository import MessageRepository

# Textos fijos: en WhatsApp, pasadas 24 h desde el último mensaje del candidato
# solo se pueden enviar plantillas aprobadas. Uno por idioma.
FIRST_REMINDER = {
    Language.ES: "Hola, {first_name}. Cuando tengas un momento, seguimos con tu solicitud de repartidor: solo quedan unas pocas preguntas.",
    Language.EN: "Hi {first_name}. Whenever you have a moment, let's continue with your delivery driver application: just a few questions left.",
}
SECOND_REMINDER = {
    Language.ES: "Hola, {first_name}. Tu solicitud en Grupo Sazón sigue abierta. Si te sigue interesando, contesta a este mensaje y seguimos donde lo dejamos.",
    Language.EN: "Hi {first_name}. Your application at Grupo Sazón is still open. If you're still interested, reply to this message and we'll pick up where we left off.",
}
CLOSING = {
    Language.ES: "Hola, {first_name}. Como no hemos tenido noticias tuyas, cerramos tu solicitud por ahora. Si más adelante te interesa, escríbenos por aquí y la retomamos. ¡Gracias! 🙌",
    Language.EN: "Hi {first_name}. Since we haven't heard back, we're closing your application for now. If you're interested later, just message us here and we'll pick it up again. Thanks! 🙌",
}
MESSAGES = [FIRST_REMINDER, SECOND_REMINDER, CLOSING]


class ReminderAction(StrEnum):
    FIRST_REMINDER = "first_reminder"
    SECOND_REMINDER = "second_reminder"
    CLOSE = "close"


ACTIONS = [ReminderAction.FIRST_REMINDER, ReminderAction.SECOND_REMINDER, ReminderAction.CLOSE]


class SentReminder(BaseModel):
    candidate_id: str
    action: ReminderAction


class ReengagementService:
    def __init__(self, conn: sqlite3.Connection, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.conn = conn
        self.candidates = CandidateRepository(conn)
        self.messages = MessageRepository(conn)
        self.now = now

    def run_reminders(self) -> list[SentReminder]:
        """Envía lo que toque a cada conversación abierta (como mucho una acción
        por candidato y ejecución). Una transacción por candidato."""
        now = self.now()
        sent = []
        for candidate in self.candidates.list_open_conversations():
            action = due_action(candidate, now)
            if action is None:
                continue
            step = ACTIONS.index(action)
            text = MESSAGES[step][candidate.language].format(first_name=candidate.full_name.split()[0])
            candidate.reminders_sent = step + 1
            if action == ReminderAction.CLOSE:
                candidate.outcome = Outcome.ABANDONED  # no es un descarte; se guarda la etapa alcanzada
            with self.conn:
                self.messages.add(Message(candidate_id=candidate.id, role="agent", text=text, created_at=now))
                self.candidates.update(candidate)
            sent.append(SentReminder(candidate_id=candidate.id, action=action))
        return sent


def due_action(candidate: Candidate, now: datetime) -> ReminderAction | None:
    """Qué toca enviar ahora a este candidato, o None. Función pura: fácil de testear."""
    if candidate.reminders_sent >= len(REMINDER_SCHEDULE):
        return None
    # Desde cuándo esperamos su respuesta: el más reciente de su último mensaje, la
    # apertura o la reapertura por un recruiter.
    moments = [candidate.last_candidate_message_at, candidate.contacted_at, candidate.reopened_at]
    waiting_since = max((m for m in moments if m is not None), default=None)
    if waiting_since is None or now - waiting_since < REMINDER_SCHEDULE[candidate.reminders_sent]:
        return None
    if not _within_sending_hours(candidate, now):
        return None  # la siguiente ejecución dentro de la franja lo enviará
    return ACTIONS[candidate.reminders_sent]


def _within_sending_hours(candidate: Candidate, now: datetime) -> bool:
    local_hour = now.astimezone(COUNTRY_TIMEZONE[candidate.country]).hour
    start, end = SENDING_HOURS
    return start <= local_hour < end

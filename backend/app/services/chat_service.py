"""Lógica de un turno de conversación (lo que hay debajo de POST /chat)."""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.clients.aws_client import LLMError
from app.core.exceptions import CandidateNotFound
from app.models.enums import Channel, Language, Outcome
from app.models.message import Message
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.message_repository import MessageRepository
from app.services.agent_service import AgentService

logger = logging.getLogger(__name__)

# Mensajes de la conversación que ve el agente: toda, con un tope de seguridad
# (una conversación normal tiene ~20 mensajes).
HISTORY_LIMIT = 40

# Si Bedrock falla, el turno no cuenta: el estado no avanza (no se gasta una
# repregunta del candidato por un fallo nuestro) y se le pide que lo repita.
FALLBACK_REPLY = {
    Language.ES: "Perdona, he tenido un problema técnico. ¿Me lo puedes repetir?",
    Language.EN: "Sorry, I had a technical issue. Could you say that again?",
}


class ChatService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        agent: AgentService,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.conn = conn
        self.candidates = CandidateRepository(conn)
        self.messages = MessageRepository(conn)
        self.agent = agent
        self.now = now  # inyectable: los tests y la demo usan un reloj simulado

    def handle_message(self, candidate_id: str, text: str, channel: Channel = Channel.TEXT) -> "ChatResult":
        now = self.now()

        # 1. Cargar candidato e historial, y guardar su mensaje antes de nada: si
        #    algo falla después (p. ej. Bedrock), el mensaje no se pierde.
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            raise CandidateNotFound(candidate_id)
        if candidate.outcome == Outcome.ABANDONED:
            # Ha vuelto: se retoma en la etapa guardada (abandonar no es un descarte).
            candidate.outcome = None
        history = self.messages.list_by_candidate(candidate_id)
        incoming = Message(candidate_id=candidate_id, role="candidate", text=text, created_at=now)
        with self.conn:
            self.messages.add(incoming)

        try:
            # 2-4. El agente registra la respuesta (tool -> máquina de estados) y escribe el mensaje.
            #      Si la tool reserva una entrevista, la reserva queda en la transacción abierta
            #      y se confirma junto con el estado y la respuesta (paso 5).
            turn = self.agent.respond(
                candidate, (history + [incoming])[-HISTORY_LIMIT:], text, now.date(), channel=channel
            )
        except LLMError:
            logger.exception("LLM failed for candidate %s", candidate_id)
            self.conn.rollback()  # el turno no cuenta: no puede quedar nada a medias
            reply = FALLBACK_REPLY[candidate.language]
            with self.conn:
                self.messages.add(Message(candidate_id=candidate_id, role="agent", text=reply, created_at=now))
            return ChatResult(reply=reply)

        updated = turn.decision.candidate
        updated.last_candidate_message_at = now
        updated.reminders_sent = 0  # ha contestado: la secuencia de recordatorios empieza de nuevo

        # 5. Estado y respuesta se guardan juntos o no se guarda ninguno.
        with self.conn:
            self.candidates.update(updated)
            self.messages.add(Message(candidate_id=candidate_id, role="agent", text=turn.reply, created_at=now))
        # Se acaba de cerrar si antes no tenía resultado y ahora sí: toca generar el resumen.
        return ChatResult(reply=turn.reply, closed=candidate.outcome is None and updated.outcome is not None)


@dataclass
class ChatResult:
    reply: str
    closed: bool = False  # la conversación se ha cerrado en este turno

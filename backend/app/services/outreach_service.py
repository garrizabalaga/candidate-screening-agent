"""Job de primer contacto: escribe a los candidatos que han aplicado y todavía
no han recibido ningún mensaje.

No lo dispara ningún mensaje del candidato. En la demo lo lanza un botón
(POST /jobs/first-contact); en producción, un scheduler (EventBridge). El
código que se ejecuta es el mismo.
"""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from app.core.config import PUBLIC_BASE_URL
from app.models.message import Message
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.message_repository import MessageRepository

# Texto fijo, no del LLM: en WhatsApp Business un mensaje que inicia la empresa
# tiene que ser una plantilla aprobada por Meta (el enlace iría como variable o
# botón de la plantilla). Pregunta la identidad, así que el candidato contesta ya a
# la etapa `identity`, por chat o por voz: el enlace lleva a la llamada con Guille.
OPENING_MESSAGE = (
    "¡Hola! Soy Guille, el asistente virtual de selección de Grupo Sazón 👋 "
    "Te escribo por tu solicitud como repartidor. Antes de empezar, ¿hablo con {full_name}?\n"
    "Si prefieres hacerlo por llamada, pulsa aquí: {voice_url}"
)


class OutreachService:
    def __init__(self, conn: sqlite3.Connection, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.conn = conn
        self.candidates = CandidateRepository(conn)
        self.messages = MessageRepository(conn)
        self.now = now

    def run_first_contact(self) -> list[str]:
        """Contacta a todos los pendientes. Devuelve los ids contactados.

        Una transacción por candidato: el mensaje y contacted_at se guardan juntos,
        y si uno falla los demás se contactan igualmente. Como solo coge filas con
        contacted_at vacío, lanzarlo dos veces no escribe dos veces a nadie.
        """
        contacted = []
        for candidate in self.candidates.list_pending_contact():
            now = self.now()
            text = OPENING_MESSAGE.format(
                full_name=candidate.full_name, voice_url=f"{PUBLIC_BASE_URL}/voice/{candidate.id}"
            )
            candidate.contacted_at = now
            with self.conn:
                self.messages.add(Message(candidate_id=candidate.id, role="agent", text=text, created_at=now))
                self.candidates.update(candidate)
            contacted.append(candidate.id)
        return contacted

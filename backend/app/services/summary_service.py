"""Resumen post-conversación: datos clave del código + notas del LLM.

Se genera cuando una conversación se cierra (apto con entrevista, descartado,
revisión manual, contacto erróneo, abandono), en segundo plano para no hacer
esperar al candidato. También se puede regenerar desde el dashboard.
"""

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from pydantic import ValidationError

from app.clients.aws_client import BedrockClient, LLMError
from app.core.exceptions import CandidateNotFound
from app.models.summary import Summary, SummaryNotes
from app.prompts import summary as prompt
from app.repositories.candidate_repository import CandidateRepository
from app.repositories.interview_repository import InterviewRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.summary_repository import SummaryRepository
from app.services.recruiter_service import summary as interview_summary

logger = logging.getLogger(__name__)


class SummaryService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        llm: BedrockClient,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.conn = conn
        self.candidates = CandidateRepository(conn)
        self.messages = MessageRepository(conn)
        self.interviews = InterviewRepository(conn)
        self.summaries = SummaryRepository(conn)
        self.llm = llm
        self.now = now

    def generate(self, candidate_id: str) -> Summary:
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            raise CandidateNotFound(candidate_id)
        messages = self.messages.list_by_candidate(candidate_id)
        interview = self.interviews.scheduled_for_candidate(candidate_id)

        duration = None
        if candidate.contacted_at and candidate.last_candidate_message_at:
            duration = (candidate.last_candidate_message_at - candidate.contacted_at).total_seconds() / 60

        summary = Summary(
            candidate_id=candidate_id,
            generated_at=self.now(),
            outcome=candidate.outcome,
            disqualification_reason=candidate.disqualification_reason,
            stage=candidate.stage,
            fields=candidate.fields,
            interview=interview_summary(interview) if interview else None,
            review_flags=candidate.review_flags,
            message_count=len(messages),
            duration_minutes=duration,
            notes=None,
        )
        summary.notes = self._notes(candidate, messages, summary)

        with self.conn:
            self.summaries.save(summary)
        return summary

    def _notes(self, candidate, messages, summary: Summary) -> SummaryNotes | None:
        """Notas del LLM. Si falla, el resumen se guarda solo con los datos clave."""
        if not any(m.role == "candidate" for m in messages):
            return SummaryNotes()  # nunca contestó: no hay nada que resumir
        key_data = summary.fields.model_dump_json(exclude_none=True)
        try:
            raw = self.llm.generate_structured(
                prompt.SYSTEM,
                [{"role": "user", "content": [{"text": prompt.user_prompt(candidate, messages, key_data)}]}],
                prompt.TOOL,
                temperature=0.0,
            )
            return SummaryNotes.model_validate(raw)
        except (LLMError, ValidationError):
            logger.exception("Summary notes failed for candidate %s", candidate.id)
            return None

    def get(self, candidate_id: str) -> Summary | None:
        return self.summaries.get(candidate_id)

"""Dependencias compartidas por los routers (FastAPI las inyecta con Depends).
Los tests las sustituyen con app.dependency_overrides."""

import logging
import sqlite3
from collections.abc import Callable, Iterator
from functools import lru_cache

from app.clients.aws_client import BedrockClient, EmbeddingClient
from app.core.config import BEDROCK_AGENT_MODEL_ID, BEDROCK_EMBEDDING_MODEL_ID
from app.repositories.database import get_connection
from app.services.summary_service import SummaryService
from app.services.voice_requests import VoiceRequests

logger = logging.getLogger(__name__)


def get_db() -> Iterator[sqlite3.Connection]:
    """Una conexión por petición, que se cierra al terminar."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


@lru_cache
def get_agent_llm() -> BedrockClient:
    """Un único cliente para toda la app: crearlo cuesta y es seguro compartirlo."""
    return BedrockClient(model_id=BEDROCK_AGENT_MODEL_ID)


def _generate_summary(candidate_id: str) -> None:
    """Tarea en segundo plano: abre su propia conexión (la de la petición ya se ha
    cerrado al responder). En producción sería un mensaje a una cola (SQS) y un worker."""
    conn = get_connection()
    try:
        SummaryService(conn, get_agent_llm()).generate(candidate_id)
    except Exception:
        logger.exception("Background summary failed for candidate %s", candidate_id)
    finally:
        conn.close()


@lru_cache
def get_embeddings() -> EmbeddingClient:
    """Cliente de embeddings (base de conocimiento), compartido como el de chat."""
    return EmbeddingClient(model_id=BEDROCK_EMBEDDING_MODEL_ID)


@lru_cache
def get_voice_requests() -> VoiceRequests:
    """Uno para toda la app: tiene que ver todas las peticiones de voz para saber cuál es la última."""
    return VoiceRequests()


def get_summary_job() -> Callable[[str], None]:
    """Lo que se lanza en segundo plano al cerrar una conversación. Los tests lo sustituyen."""
    return _generate_summary

"""POST /chat: un mensaje del candidato -> la respuesta del agente.

Capa HTTP únicamente: valida el body, llama al servicio y devuelve JSON.
"""

import logging
import sqlite3
import time
from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app.clients.aws_client import BedrockClient
from app.core.exceptions import CandidateNotFound
from app.routes.dependencies import get_agent_llm, get_db, get_summary_job
from app.services.agent_service import AgentService
from app.services.chat_service import ChatService
from app.services.scheduling_service import SchedulingService

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    candidate_id: str
    text: str


class ChatResponse(BaseModel):
    reply: str


@router.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    background: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
    llm: BedrockClient = Depends(get_agent_llm),
    summary_job: Callable[[str], None] = Depends(get_summary_job),
) -> ChatResponse:
    logger.info("Chat request: candidate=%s text=%r", req.candidate_id, req.text)
    start = time.monotonic()
    service = ChatService(conn, AgentService(llm, SchedulingService(conn)))
    try:
        result = service.handle_message(req.candidate_id, req.text)
    except CandidateNotFound:
        raise HTTPException(status_code=404, detail="candidate not found")
    logger.info("Chat reply for %s (%.1f s): %r", req.candidate_id, time.monotonic() - start, result.reply)
    if result.closed:
        background.add_task(summary_job, req.candidate_id)  # después de responder: el candidato no espera
    return ChatResponse(reply=result.reply)

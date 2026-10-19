"""Canal de voz (ElevenLabs).

- GET /voice/{candidate_id}: el enlace que recibe el candidato. Redirige a la página
  del agente de ElevenLabs (talk-to) con dos variables dinámicas: candidate_id y
  saludo (calculado en ese momento según la etapa).
- POST /voice/llm/chat/completions: el "LLM" al que llama el agente de voz de ElevenLabs.

ElevenLabs hace la voz (transcribe al candidato y lee la respuesta) y, para saber
qué contestar, llama a este endpoint como si fuera OpenAI (opción "Custom LLM").
Aquí no hay un LLM nuevo: es otra puerta al mismo turno que POST /chat.

- El candidato llega dentro del system prompt que envía ElevenLabs: el prompt del
  agente en su consola lleva la línea `candidate_id={{candidate_id}}` y ElevenLabs
  la rellena con la variable del enlace. Es la única vía con su página (talk-to):
  no permite enviar datos aparte al Custom LLM. Si alguien borra esa línea del
  prompt, las llamadas dan 400.
- De `messages` solo se usa el último mensaje del usuario (lo que acaba de decir).
  El historial y el estado buenos son los de nuestra BD, y el prompt que envíe
  ElevenLabs se ignora: el nuestro es el de siempre, en modo voz.
- La respuesta va en streaming (SSE, formato de OpenAI), como exige ElevenLabs,
  pero en un solo trozo: el mensaje no se puede enviar hasta que reply_guard lo
  ha validado. La espera del turno (~3 s) la tapa ElevenLabs con su "soft
  timeout": si no respondemos en ~1,5 s, dice una frase de relleno configurada en
  su consola. Solo suena cuando hace falta, no en todos los turnos.
- Antes de procesarse, cada turno espera VOICE_DEBOUNCE_SECONDS: si en ese margen
  llega otra petición del mismo candidato (ElevenLabs ha descartado esta porque el
  candidato siguió hablando), se retira sin hacer nada. Ver services/voice_requests.py.
"""

import json
import logging
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.clients.aws_client import BedrockClient, EmbeddingClient
from app.core.config import ELEVENLABS_AGENT_ID, VOICE_DEBOUNCE_SECONDS, VOICE_LLM_API_KEY
from app.models.enums import Channel
from app.prompts import voice as voice_prompts
from app.repositories.candidate_repository import CandidateRepository
from app.routes.dependencies import get_agent_llm, get_db, get_embeddings, get_summary_job, get_voice_requests
from app.services.agent_service import AgentService
from app.services.chat_service import FALLBACK_REPLY, ChatService
from app.services.knowledge_service import KnowledgeService
from app.services.scheduling_service import SchedulingService
from app.services.voice_requests import VoiceRequests

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice")

ELEVENLABS_TALK_TO_URL = "https://elevenlabs.io/app/talk-to"
_CANDIDATE_ID_IN_PROMPT = re.compile(r"candidate_id=([A-Za-z0-9_-]+)")


class ChatCompletionRequest(BaseModel):
    """Los campos que usamos. ElevenLabs envía más (model, temperature...): se
    conservan (extra="allow") para poder registrar la petición completa."""

    model_config = ConfigDict(extra="allow")

    messages: list[dict]


def check_api_key(authorization: str | None = Header(default=None)) -> None:
    if not VOICE_LLM_API_KEY:
        return  # en local, sin clave configurada
    expected = f"Bearer {VOICE_LLM_API_KEY}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid api key")


@router.post("/llm/chat/completions", dependencies=[Depends(check_api_key)])
def chat_completions(
    req: ChatCompletionRequest,
    background: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
    llm: BedrockClient = Depends(get_agent_llm),
    embeddings: EmbeddingClient = Depends(get_embeddings),
    summary_job: Callable[[str], None] = Depends(get_summary_job),
    requests: VoiceRequests = Depends(get_voice_requests),
) -> StreamingResponse:
    logger.info("Voice request from ElevenLabs: %s", json.dumps(req.model_dump(), ensure_ascii=False))
    candidate_id = _candidate_id(req)
    text = _last_user_text(req.messages)
    if not candidate_id or not text:
        raise HTTPException(status_code=400, detail="candidate_id and a user message are required")
    # Se comprueba antes de empezar el stream: después ya no se puede devolver un 404.
    candidate = CandidateRepository(conn).get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate not found")

    service = ChatService(conn, AgentService(llm, SchedulingService(conn), KnowledgeService(conn, embeddings)))
    number = requests.register(candidate_id)  # al llegar, no tras la espera

    def stream() -> Iterator[str]:
        start = time.monotonic()
        time.sleep(VOICE_DEBOUNCE_SECONDS)
        if not requests.is_latest(candidate_id, number):
            logger.info("Voice request for %s superseded by a newer one: not processed", candidate_id)
            yield _chunk(None, finish_reason="stop")
            yield "data: [DONE]\n\n"
            return
        try:
            result = service.handle_message(candidate_id, text, channel=Channel.VOICE)
            reply = result.reply
            if result.closed:
                # Se ejecuta cuando termina la respuesta (FastAPI la asocia al StreamingResponse).
                background.add_task(summary_job, candidate_id)
        except Exception:
            # El stream ya está abierto (200): si se corta aquí, el candidato se queda
            # sin respuesta. Mejor pedir que lo repita, como en el chat.
            logger.exception("Voice turn failed for candidate %s", candidate_id)
            reply = FALLBACK_REPLY[candidate.language]
        logger.info("Voice reply for %s (%.1f s): %r", candidate_id, time.monotonic() - start, reply)
        yield _chunk(reply)
        yield _chunk(None, finish_reason="stop")
        yield "data: [DONE]\n\n"

    # Starlette recorre un generador normal en un hilo aparte: el turno (bloqueante)
    # no frena al resto de peticiones.
    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/{candidate_id}", response_model=None)
def voice_link(candidate_id: str, conn: sqlite3.Connection = Depends(get_db)) -> RedirectResponse | HTMLResponse:
    """El enlace para hablar con Guille por voz. El saludo se calcula al pulsarlo, no al
    enviarlo: si el candidato ya ha contestado algo por chat, retoma donde lo dejó."""
    candidate = CandidateRepository(conn).get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    if candidate.outcome is not None:
        return HTMLResponse("<p>Tu conversación con el equipo de selección ya ha terminado. ¡Gracias!</p>")
    params = {
        "agent_id": ELEVENLABS_AGENT_ID,
        "var_candidate_id": candidate.id,
        "var_saludo": voice_prompts.greeting(candidate),
    }
    return RedirectResponse(f"{ELEVENLABS_TALK_TO_URL}?{urlencode(params)}")


def _candidate_id(req: ChatCompletionRequest) -> str:
    """De qué candidato es el turno: la línea `candidate_id=...` del system prompt."""
    for m in req.messages:
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            found = _CANDIDATE_ID_IN_PROMPT.search(m["content"])
            if found:
                return found.group(1)
    return ""


def _last_user_text(messages: list[dict]) -> str:
    """El último mensaje del usuario (lo que acaba de decir el candidato)."""
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content") or "").strip()
    return ""


def _chunk(content: str | None, finish_reason: str | None = None) -> str:
    """Un evento SSE con la forma de un chunk de OpenAI (chat.completion.chunk)."""
    delta = {"content": content} if content is not None else {}
    data = {
        "id": "chatcmpl-guille",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "guille",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

"""Base de conocimiento (FAQ): POST /knowledge sube un Markdown y regenera la base;
GET /knowledge devuelve las preguntas cargadas."""

import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.clients.aws_client import EmbeddingClient, LLMError
from app.models.knowledge import KnowledgeEntry
from app.repositories.knowledge_repository import KnowledgeRepository
from app.routes.dependencies import get_db, get_embeddings
from app.services.knowledge_service import InvalidFaq, KnowledgeService

router = APIRouter(prefix="/knowledge")


class UploadResponse(BaseModel):
    source: str
    questions: int


@router.post("", response_model=UploadResponse)
def upload_faq(
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_db),
    embeddings: EmbeddingClient = Depends(get_embeddings),
) -> UploadResponse:
    if not (file.filename or "").lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="the FAQ must be a Markdown (.md) file")
    try:
        markdown = file.file.read().decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="the file must be UTF-8 text")
    try:
        chunks = KnowledgeService(conn, embeddings).ingest(file.filename, markdown)
    except InvalidFaq:
        raise HTTPException(status_code=400, detail="no questions found: each question must be a '## ' heading")
    except LLMError:
        raise HTTPException(status_code=503, detail="embedding service unavailable, the previous FAQ is kept")
    return UploadResponse(source=file.filename, questions=len(chunks))


@router.get("", response_model=list[KnowledgeEntry])
def list_faq(conn: sqlite3.Connection = Depends(get_db)) -> list[KnowledgeEntry]:
    return [KnowledgeEntry(**c.model_dump(exclude={"embedding"})) for c in KnowledgeRepository(conn).list_all()]

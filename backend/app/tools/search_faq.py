"""Tool search_faq: el agente consulta la información de la empresa cuando el candidato pregunta algo.

Devuelve los datos que salen de la configuración (ciudades con servicio en su país,
requisito del carné) y las preguntas del FAQ más parecidas (RAG). Los primeros no
van en el FAQ porque el código los usa para decidir: así no hay dos fuentes de
verdad que se puedan contradecir.
"""

import logging

from app.clients.aws_client import LLMError
from app.core.config import list_service_cities
from app.models.candidate import Candidate
from app.models.enums import Country
from app.services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

NAME = "search_faq"

SPEC = {
    "name": NAME,
    "description": (
        "Busca en la base de conocimiento de Grupo Sazón (la empresa, el puesto, el proceso de selección) y "
        "devuelve las ciudades con servicio y los requisitos. Úsala cuando el candidato pregunte algo, y "
        "responde solo con lo que devuelva."
    ),
    "inputSchema": {"json": {
        "type": "object",
        "properties": {"question": {
            "type": "string",
            # Una pregunta completa encuentra mucho mejor la respuesta que el mensaje literal (0,14 -> 0,68).
            "description": (
                "La pregunta del candidato reformulada de forma completa y explícita, no copiada tal cual. "
                "Ejemplo: '¿esto es para Glovo?' -> '¿El trabajo es para Grupo Sazón o para una plataforma "
                "de reparto como Glovo?'"
            ),
        }},
        "required": ["question"],
    }},
}

_COUNTRY_NAME = {Country.ES: "España", Country.MX: "México"}


def run(candidate: Candidate, question: str, knowledge: KnowledgeService) -> dict:
    cities = ", ".join(list_service_cities(candidate.country))
    out: dict = {"information": "\n".join([
        f"- Ciudades con servicio en {_COUNTRY_NAME[candidate.country]}: {cities}.",
        "- Requisito: tener carné de conducir de coche o moto en vigor. Con bici no es posible.",
    ])}
    try:
        matches = knowledge.search(question)
    except LLMError:
        logger.exception("FAQ search failed")  # el turno sigue con los datos fijos
        matches = []
    out["faq"] = [{"question": m.question, "answer": m.answer} for m in matches]
    if not matches:
        out["faq_note"] = "El FAQ no tiene información sobre esta pregunta."
    return out

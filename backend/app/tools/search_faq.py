"""Tool search_faq: el agente consulta información cuando el candidato pregunta algo.

Hoy devuelve la información fija de knowledge.py. Con un RAG, aquí iría la
búsqueda en la base de conocimiento; el agente no cambia.
"""

from app.models.candidate import Candidate
from app.prompts.knowledge import facts

NAME = "search_faq"

SPEC = {
    "name": NAME,
    "description": (
        "Busca información sobre el puesto, la empresa, las ciudades con servicio o el proceso de selección. "
        "Úsala cuando el candidato pregunte algo, y responde solo con lo que devuelva."
    ),
    "inputSchema": {"json": {
        "type": "object",
        "properties": {"question": {"type": "string", "description": "La pregunta del candidato"}},
        "required": ["question"],
    }},
}


def run(candidate: Candidate, question: str) -> dict:
    return {"information": facts(candidate)}

"""Lo que el agente sabe con certeza y puede contar al candidato.

El LLM solo puede responder con esta información: si una pregunta no se
responde aquí (sueldo, contrato, vehículo...), dice que no dispone de ese dato.
Así no se inventa nada sobre el puesto. Es el punto de partida para una base
de conocimiento (FAQ/RAG) más completa.
"""

from app.core.config import list_service_cities
from app.models.candidate import Candidate
from app.models.enums import Country

_COUNTRY_NAME = {Country.ES: "España", Country.MX: "México"}


def facts(candidate: Candidate) -> str:
    cities = ", ".join(list_service_cities(candidate.country))
    return "\n".join([
        "- Grupo Sazón es una cadena de restaurantes con 45 locales en España y México.",
        "- El puesto es de repartidor.",
        "- Requisito: tener carné de conducir de coche o moto en vigor. Con bici no es posible.",
        f"- Ciudades con servicio en {_COUNTRY_NAME[candidate.country]}: {cities}.",
        "- No hace falta experiencia previa en reparto.",
        "- Hay puestos de jornada completa, media jornada y fines de semana, en turnos de mañana, tarde o noche.",
        "- Proceso: unas preguntas breves por este chat y, si cumple los requisitos, una entrevista por videollamada de unos 30 minutos con el equipo de selección, que se agenda aquí mismo.",
        "- Guille es un asistente virtual (una IA). Las decisiones finales las toma el equipo de selección.",
    ])

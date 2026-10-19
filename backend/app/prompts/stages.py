"""Qué se pregunta en cada etapa. Lo usan los dos prompts: la extracción (para
saber a qué responde un "sí" a secas) y la redacción (para formular la pregunta).

Las opciones cerradas de la 2.ª repregunta las genera el código, así el LLM no
puede ofrecer una opción que no existe.
"""

from app.core.config import list_service_cities
from app.models.candidate import Candidate
from app.models.enums import Stage


def question(stage: Stage, candidate: Candidate) -> str:
    return {
        Stage.IDENTITY: f"si hablamos con {candidate.full_name} (la persona que aplicó)",
        Stage.LICENSE: "si tiene carné de conducir de coche o moto en vigor",
        Stage.ZONE: "en qué ciudad le gustaría repartir",
        Stage.AVAILABILITY: "qué disponibilidad tiene: jornada completa, media jornada o fines de semana",
        Stage.SCHEDULE: "qué horario prefiere: mañana, tarde, noche o flexible",
        Stage.EXPERIENCE: "si tiene experiencia previa en reparto: cuántos años y en qué plataformas (Glovo, Uber Eats...)",
        Stage.START_DATE: "cuándo podría empezar a trabajar",
    }[stage]


def rephrase(stage: Stage, candidate: Candidate) -> str:
    """Reformulación sugerida para la 1.ª repregunta: más sencilla y con ejemplo.
    Escrita por nosotros para que no se desvíe (p. ej. preguntar dónde vive)."""
    return {
        Stage.IDENTITY: f"¿Eres {candidate.full_name}? Te escribo por la solicitud de repartidor que enviaste.",
        Stage.LICENSE: "¿Puedes conducir un coche o una moto? Es decir, ¿tienes carné de coche o de moto en vigor?",
        Stage.ZONE: "¿En qué ciudad te vendría bien repartir? No tiene que ser donde vives.",
        Stage.AVAILABILITY: "¿Buscas algo a jornada completa, a media jornada o solo para fines de semana?",
        Stage.SCHEDULE: "¿En qué parte del día te viene mejor trabajar: mañana, tarde, noche, o te da igual?",
        Stage.EXPERIENCE: "¿Has trabajado antes repartiendo? Si es así, ¿cuánto tiempo y con qué apps (Glovo, Uber Eats...)?",
        Stage.START_DATE: "¿A partir de qué día podrías empezar? Por ejemplo, la semana que viene o una fecha concreta.",
    }[stage]


def closed_options(stage: Stage, candidate: Candidate) -> list[str]:
    return {
        Stage.IDENTITY: ["sí, soy yo", "no, no soy yo"],
        Stage.LICENSE: ["sí", "no"],
        Stage.ZONE: list_service_cities(candidate.country),
        Stage.AVAILABILITY: ["jornada completa", "media jornada", "fines de semana"],
        Stage.SCHEDULE: ["mañana", "tarde", "noche", "flexible"],
        Stage.EXPERIENCE: ["sin experiencia", "menos de 1 año", "entre 1 y 3 años", "más de 3 años"],
        Stage.START_DATE: ["esta semana", "la semana que viene", "dentro de un mes"],
    }[stage]

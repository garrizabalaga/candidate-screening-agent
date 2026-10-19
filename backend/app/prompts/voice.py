"""Saludo con el que arranca la llamada de voz.

Lo dice ElevenLabs (first message `{{saludo}}`, que llega como variable del enlace),
así que no pasa por el LLM: es texto fijo, como la apertura por WhatsApp. Termina
con la pregunta pendiente, para que la respuesta del candidato conteste a la etapa
en la que está. Un saludo general ("¿seguimos?") no vale: un "sí" a secas se
tomaría como respuesta a la pregunta pendiente (p. ej. "tiene carné").
"""

from app.models.candidate import Candidate
from app.models.enums import Language, Stage
from app.prompts.fallbacks import QUESTION_EN
from app.prompts.slots import format_slot
from app.prompts.stages import rephrase


def greeting(candidate: Candidate) -> str:
    name = candidate.full_name.split()[0]
    english = candidate.language == Language.EN
    if candidate.stage == Stage.IDENTITY:
        if english:
            return f"Hi, I'm Guille, Grupo Sazón's recruitment assistant, calling about your delivery driver application. Am I speaking with {candidate.full_name}?"
        return f"Hola, soy Guille, el asistente de selección de Grupo Sazón. Te llamo por tu solicitud como repartidor. ¿Hablo con {candidate.full_name}?"

    hello = f"Hi {name}, it's Guille from Grupo Sazón. Let's continue by voice." if english else (
        f"Hola {name}, soy Guille, de Grupo Sazón. Seguimos por voz."
    )
    if candidate.stage == Stage.SCHEDULING and candidate.offered_slots:
        slots = "; ".join(format_slot(s, candidate) for s in candidate.offered_slots)
        if english:
            return f"{hello} I offered you these interview slots: {slots}. Which one works best for you?"
        return f"{hello} Te había propuesto estos huecos para la entrevista: {slots}. ¿Cuál te va mejor?"
    # Si hay una eliminatoria pendiente de confirmar, la pregunta pendiente es esa.
    stage = candidate.pending_confirmation or candidate.stage
    if stage not in QUESTION_EN:  # p. ej. agenda sin huecos ofrecidos: no hay pregunta pendiente
        return hello
    question = QUESTION_EN[stage].format(name=candidate.full_name) if english else rephrase(stage, candidate)
    return f"{hello} {question}"

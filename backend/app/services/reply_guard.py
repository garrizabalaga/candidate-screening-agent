"""Validación en código del mensaje que escribe el agente, antes de enviarlo.

Misma idea que con los datos: el LLM propone y el código comprueba. Son reglas
simples y deterministas; no garantizan un buen mensaje, pero sí evitan los
fallos que hemos visto en las pruebas (razonamiento filtrado, un cierre que
pregunta, una confirmación sin pregunta, un sueldo inventado, mensajes largos).

La longitud se controla aquí y no con max_tokens: el modelo no conoce ese tope y,
al alcanzarlo, la respuesta se corta a mitad de frase. max_tokens queda como tope
de seguridad, y una respuesta cortada se trata como inválida (agent_service).
"""

import re

from app.models.enums import Stage
from app.services.state_machine import Action, Decision
from app.tools.record_answer import CLOSING_ACTIONS

QUESTION_ACTIONS = {Action.ASK, Action.REASK, Action.CONFIRM, Action.OFFER_SLOTS}

# Longitud máxima en palabras. La guía de tono dice ~40; se deja margen. La oferta
# de huecos es más larga porque lleva la lista de fechas.
MAX_WORDS = 50
MAX_WORDS_OFFER = 90

_THINKING = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_NEGATIVE_QUESTION = re.compile(r"¿\s*(?:entonces\s*,?\s*)?no\b", re.IGNORECASE)


def clean(reply: str) -> str:
    """Quita el razonamiento que algunos modelos escriben entre <thinking> (Nova)."""
    return _THINKING.sub("", reply).strip()


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_FORMATTING = re.compile(r"[*_#`•]")


def for_voice(reply: str) -> str:
    """El mensaje tal como se va a leer en voz alta. El prompt de voz ya lo pide;
    esto lo garantiza también en los textos fijos: un emoji o un asterisco suenan
    raro en el TTS, y en una llamada no hay "este chat" (el seguimiento va por WhatsApp)."""
    text = _FORMATTING.sub("", _EMOJI.sub("", reply))
    text = text.replace("por este chat", "por WhatsApp")
    return " ".join(text.split())


def problems(reply: str, decision: Decision, context: str) -> list[str]:
    """Lista de incumplimientos (vacía si el mensaje es válido). `context` es todo
    lo que el agente tenía delante (prompt, resultados de tools, conversación):
    las cifras del mensaje tienen que salir de ahí."""
    found = []
    if not reply:
        return ["El mensaje está vacío."]
    limit = MAX_WORDS_OFFER if decision.action == Action.OFFER_SLOTS else MAX_WORDS
    words = len(reply.split())
    if words > limit:
        found.append(f"Es demasiado largo ({words} palabras; máximo {limit}). Esto es un chat: acórtalo.")
    has_question = "?" in reply
    if decision.action in QUESTION_ACTIONS and not has_question:
        found.append("El objetivo pide hacer una pregunta y tu mensaje no tiene ninguna.")
    if decision.action in CLOSING_ACTIONS and has_question:
        found.append("Es un mensaje de cierre: no puede llevar ninguna pregunta.")
    if decision.action == Action.CONFIRM and decision.stage == Stage.LICENSE and _NEGATIVE_QUESTION.search(reply):
        found.append('Pregunta en positivo ("¿Tienes algún carné…?"), no en negativo.')
    if decision.action == Action.BOOKED and decision.slot:
        recruiter = decision.slot.recruiter_name.split()[0]
        if recruiter.casefold() not in reply.casefold():
            found.append(f"Confirma la entrevista diciendo con quién es: {recruiter}.")
    invented = set(_NUMBER.findall(reply)) - set(_NUMBER.findall(context))
    if invented:
        found.append(f"Incluyes cifras que no aparecen en la información disponible ({', '.join(sorted(invented))}): no inventes datos.")
    return found

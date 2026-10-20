"""Tool record_answer: el agente registra lo que ha dicho el candidato y el código decide.

- Argumentos: los campos que aporta el último mensaje (ExtractedFields), el idioma
  y la pregunta que haga, si hace alguna.
- Ejecución (run): validar con Pydantic, state_machine.advance() decide y, si la
  decisión toca la agenda, el servicio de agenda calcula los huecos o hace la
  reserva. La reserva queda en la transacción del turno.
- Resultado: el objetivo del siguiente mensaje. El agente no decide la etapa, ni
  quién es apto, ni a quién se descarta, ni qué huecos se ofrecen.
"""

import json
import logging
from datetime import date

from pydantic import ValidationError

from app.core.config import list_service_cities
from app.models.candidate import Candidate, ExtractedFields
from app.models.enums import DisqualificationReason, Language, Stage
from app.prompts.slots import format_slot, numbered
from app.prompts.stages import closed_options, question, rephrase
from app.services.scheduling_service import SchedulingService, SlotTaken
from app.services.state_machine import Action, Decision, advance, with_slots
from app.tools import search_faq

logger = logging.getLogger(__name__)

NAME = "record_answer"

# Acciones que cierran la conversación: el mensaje no puede llevar preguntas.
CLOSING_ACTIONS = {Action.DISQUALIFY, Action.WRONG_CONTACT, Action.MANUAL_REVIEW, Action.BOOKED, Action.CLOSED}


def run(candidate: Candidate, raw: dict, text: str, today: date, scheduling: SchedulingService) -> tuple[Decision, ExtractedFields]:
    """Ejecuta la tool: el código decide y, si hace falta, usa la agenda."""
    extracted = parse(raw)
    decision = advance(candidate, extracted, text, today)

    if decision.action == Action.OFFER_SLOTS and not decision.candidate.offered_slots:
        decision = with_slots(decision, scheduling.available_slots(decision.candidate))

    elif decision.action == Action.BOOKED:
        try:
            scheduling.book(decision.candidate, decision.slot)
        except SlotTaken:
            logger.info("Slot taken for candidate %s, offering new ones", candidate.id)
            decision = with_slots(decision, scheduling.available_slots(decision.candidate), slot_taken=True)
    return decision, extracted

_LANGUAGE = {Language.ES: "español", Language.EN: "inglés"}


def _schema() -> dict:
    """Los argumentos salen del modelo Pydantic: si se añade un campo, el agente lo
    ve sin tocar el prompt. language es obligatorio (admite null) porque algunos
    modelos omiten los campos opcionales."""
    schema = ExtractedFields.model_json_schema()
    schema["required"] = ["language"]
    return schema


SPEC = {
    "name": NAME,
    "description": (
        "Registra lo que el candidato dice en su último mensaje: los datos del screening que aporte, el idioma "
        "y la pregunta que haga, si hace alguna. Devuelve el objetivo de tu siguiente mensaje."
    ),
    "inputSchema": {"json": _schema()},
}


def parse(raw: dict) -> ExtractedFields:
    """Valida los argumentos. Si un campo trae un valor inválido (p. ej. un enum
    inexistente) se descarta solo ese campo: cuenta como "no extraído" y la máquina
    de estados repregunta. El resto se conserva."""
    try:
        return ExtractedFields.model_validate(raw)
    except ValidationError:
        valid = {}
        for field, value in raw.items():
            try:
                ExtractedFields.model_validate({field: value})
                valid[field] = value
            except ValidationError:
                logger.warning("Dropping invalid record_answer field: %s=%s", field, json.dumps(value))
        return ExtractedFields.model_validate(valid)


TONE = {
    "frustrated": "Parece molesto o impaciente: reconócelo en pocas palabras, sin disculpas largas, y ve al grano.",
    "confused": "Parece que no entiende qué se le pide o por qué: explícaselo en una frase sencilla antes de preguntar.",
}


def result(decision: Decision, extracted: ExtractedFields) -> dict:
    """Lo que la tool devuelve al agente: qué ha decidido el código y cómo decirlo."""
    out = {
        "objective": objective(decision),
        "reply_language": _LANGUAGE[decision.candidate.language],
        "conversation_closed": decision.action in CLOSING_ACTIONS,
    }
    if extracted.candidate_question:
        out["candidate_asked"] = (
            f'"{extracted.candidate_question}": búscalo con {search_faq.NAME} y respóndelo antes del objetivo.'
        )
    if extracted.sentiment in TONE:
        out["tone"] = TONE[extracted.sentiment]
    return out


def objective(decision: Decision) -> str:
    c: Candidate = decision.candidate
    close = "Es un mensaje de cierre: no hagas ninguna pregunta."
    match decision.action:
        case Action.ASK:
            return f"averiguar {question(decision.stage, c)}. Enlaza con naturalidad con lo que acaba de decir."
        case Action.REASK if decision.attempt == 1:
            return (
                f"todavía no sabes {question(decision.stage, c)}: su respuesta no lo aclara. Vuelve a preguntar lo "
                f"mismo de forma más sencilla, sin cambiar de tema ni añadir otras preguntas. Usa esta formulación o "
                f'una muy parecida: "{rephrase(decision.stage, c)}"'
            )
        case Action.REASK:
            options = " / ".join(closed_options(decision.stage, c))
            return (
                f"todavía no sabes {question(decision.stage, c)}. Pónselo fácil: que elija una de estas opciones "
                f"(exactamente estas): {options}."
            )
        case Action.CONFIRM if decision.stage == Stage.LICENSE:
            return (
                "ha dicho que no tiene carné de conducir y el puesto lo requiere. Antes de cerrar su solicitud, "
                "confírmalo sin juzgar con una pregunta EN POSITIVO: \"¿Tienes algún carné de coche o moto en vigor?\". "
                "No la formules en negativo (\"¿no tienes…?\"): un \"sí\" sería ambiguo."
            )
        case Action.CONFIRM:
            cities = ", ".join(list_service_cities(c.country))
            return (
                f"quiere repartir en {c.fields.city}, donde todavía no hay servicio. "
                f"Pregúntale si podría repartir en alguna de estas ciudades: {cities}."
            )
        case Action.WRONG_CONTACT:
            return f"la persona dice que no es {c.full_name}. Discúlpate por la molestia y despídete. {close}"
        case Action.DISQUALIFY if c.disqualification_reason == DisqualificationReason.NO_LICENSE:
            return (
                "explicarle con amabilidad que no puede seguir porque el puesto requiere carné de coche o moto en "
                f"vigor, agradecerle su interés y despedirte. {close}"
            )
        case Action.DISQUALIFY:
            return (
                f"explicarle que por ahora no hay servicio en {c.fields.city}, agradecerle su interés y decirle que "
                f"le avisaremos si se abre su zona. {close}"
            )
        case Action.MANUAL_REVIEW if c.review_flags and c.review_flags[-1].reason == "inappropriate_language":
            return (
                "su último mensaje ha sido ofensivo. Sin reprocharle nada ni repetir sus palabras, dile con calma que "
                f"un compañero del equipo revisará su caso y le responderá por este chat. {close}"
            )
        case Action.MANUAL_REVIEW:
            return (
                "agradecerle sus respuestas y decirle que un compañero revisará su caso y le responderá por este "
                f"chat en 24-48 horas. {close}"
            )
        case Action.OFFER_SLOTS:
            slots = f"(preséntalos numerados y exactamente así): {numbered(c)}."
            if decision.slot_taken:
                return f"el hueco que eligió acaba de ocuparse. Discúlpate en pocas palabras y ofrécele estos otros {slots} Pregúntale cuál le va mejor."
            if not decision.repeat:
                return (
                    "darle la buena noticia: cumple los requisitos. Ofrécele estos huecos para una entrevista por "
                    f"videollamada de 30 minutos {slots} Pregúntale cuál le va mejor."
                )
            if decision.attempt == 0:
                return f"recordarle los huecos para la entrevista {slots} Pregúntale cuál le va mejor."
            return (
                f"todavía no sabes qué hueco prefiere. Vuelve a mostrarle los huecos {slots} Pídele que te diga el "
                "número, o que te avise si ninguno le va."
            )
        case Action.BOOKED:
            recruiter = decision.slot.recruiter_name.split()[0]
            return (
                f"confirmarle la entrevista por videollamada el {format_slot(decision.slot, c)} con {recruiter}, "
                f"del equipo de selección, y decirle que le enviaremos el enlace por este chat. {close}"
            )
        case Action.CLOSED:
            return f"la conversación ya terminó. Agradécele el mensaje y dile que el equipo de selección le contactará si hace falta. {close}"

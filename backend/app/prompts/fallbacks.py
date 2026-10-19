"""Textos fijos por acción, en español e inglés.

Se usan cuando el agente no consigue escribir un mensaje válido (Bedrock falla
después de que el código haya decidido, o el mensaje no pasa la validación ni
tras reescribirlo). La decisión ya está tomada; solo falta comunicarla, así que
mejor un texto correcto y algo más rígido que un "problema técnico".
"""

from app.core.config import list_service_cities
from app.models.candidate import Candidate
from app.models.enums import DisqualificationReason, Language, Stage
from app.prompts.slots import format_slot, numbered
from app.prompts.stages import closed_options, rephrase
from app.services.state_machine import Action, Decision

QUESTION_EN = {
    Stage.IDENTITY: "Are you {name}? I'm writing about your delivery driver application.",
    Stage.LICENSE: "Do you have a valid car or motorbike driving licence?",
    Stage.ZONE: "Which city would you like to deliver in? It doesn't have to be where you live.",
    Stage.AVAILABILITY: "Are you looking for full-time, part-time or weekends only?",
    Stage.SCHEDULE: "What time of day suits you best: morning, afternoon, evening, or are you flexible?",
    Stage.EXPERIENCE: "Have you worked as a delivery driver before? If so, for how long and with which apps?",
    Stage.START_DATE: "When could you start? For example, next week or a specific date.",
}


def fallback(decision: Decision) -> str:
    c = decision.candidate
    return _english(decision, c) if c.language == Language.EN else _spanish(decision, c)


def _spanish(d: Decision, c: Candidate) -> str:
    name = c.full_name.split()[0]
    match d.action:
        case Action.ASK | Action.REASK if d.attempt < 2:
            return rephrase(d.stage, c)
        case Action.REASK:
            return f"¿Cuál de estas opciones encaja contigo: {' / '.join(closed_options(d.stage, c))}?"
        case Action.CONFIRM if d.stage == Stage.LICENSE:
            return "Este puesto requiere carné de coche o moto en vigor. ¿Tienes alguno?"
        case Action.CONFIRM:
            cities = ", ".join(list_service_cities(c.country))
            return f"Por ahora no tenemos servicio en {c.fields.city}. ¿Podrías repartir en alguna de estas ciudades: {cities}?"
        case Action.WRONG_CONTACT:
            return "Perdona la molestia, parece que nos hemos equivocado de persona. ¡Que vaya bien!"
        case Action.DISQUALIFY if c.disqualification_reason == DisqualificationReason.NO_LICENSE:
            return f"Gracias por tu interés, {name}. Este puesto requiere carné de coche o moto en vigor, así que no podemos seguir con tu solicitud. ¡Mucha suerte!"
        case Action.DISQUALIFY:
            return f"Gracias por tu interés, {name}. Por ahora no tenemos servicio en {c.fields.city}; te avisaremos si se abre tu zona."
        case Action.MANUAL_REVIEW:
            return f"Gracias por tus respuestas, {name}. Un compañero revisará tu caso y te responderá por este chat en 24-48 horas."
        case Action.OFFER_SLOTS if d.repeat or d.slot_taken:
            return f"¿Cuál de estos huecos te va mejor para la entrevista? {numbered(c)}. Dime el número, o avísame si ninguno te va."
        case Action.OFFER_SLOTS:
            return f"¡Buenas noticias, {name}! Cumples los requisitos. ¿Cuál de estos huecos te va mejor para una entrevista por videollamada? {numbered(c)}"
        case Action.BOOKED:
            return f"¡Listo, {name}! Tu entrevista por videollamada es el {format_slot(d.slot, c)} con {d.slot.recruiter_name.split()[0]}. Te enviaremos el enlace por este chat."
        case Action.CLOSED:
            return "Gracias por tu mensaje. Si hace falta, el equipo de selección se pondrá en contacto contigo."


def _english(d: Decision, c: Candidate) -> str:
    name = c.full_name.split()[0]
    match d.action:
        case Action.ASK | Action.REASK:
            return QUESTION_EN[d.stage].format(name=c.full_name)
        case Action.CONFIRM if d.stage == Stage.LICENSE:
            return "This position requires a valid car or motorbike licence. Do you have one?"
        case Action.CONFIRM:
            cities = ", ".join(list_service_cities(c.country))
            return f"We don't operate in {c.fields.city} yet. Could you deliver in one of these cities: {cities}?"
        case Action.WRONG_CONTACT:
            return "Sorry to bother you, it looks like we have the wrong person. Have a great day!"
        case Action.DISQUALIFY if c.disqualification_reason == DisqualificationReason.NO_LICENSE:
            return f"Thanks for your interest, {name}. This position requires a valid car or motorbike licence, so we can't continue with your application. Best of luck!"
        case Action.DISQUALIFY:
            return f"Thanks for your interest, {name}. We don't operate in {c.fields.city} yet; we'll let you know if that changes."
        case Action.MANUAL_REVIEW:
            return f"Thanks for your answers, {name}. A colleague will review your application and reply here within 24-48 hours."
        case Action.OFFER_SLOTS if d.repeat or d.slot_taken:
            return f"Which of these interview slots works best for you? {numbered(c)}. Tell me the number, or let me know if none works."
        case Action.OFFER_SLOTS:
            return f"Good news, {name}! You meet the requirements. Which of these slots works for a video interview? {numbered(c)}"
        case Action.BOOKED:
            return f"All set, {name}! Your video interview is on {format_slot(d.slot, c)} with {d.slot.recruiter_name.split()[0]}. We'll send you the link here."
        case Action.CLOSED:
            return "Thanks for your message. The recruitment team will get in touch if needed."

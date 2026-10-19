"""Tests de la validación del mensaje (funciones puras) y de los textos fijos."""

from datetime import datetime, timezone

import pytest

from app.models.candidate import Candidate
from app.models.enums import Country, DisqualificationReason, Language, Stage
from app.models.interview import Slot
from app.prompts.fallbacks import fallback
from app.services import reply_guard
from app.services.state_machine import Action, Decision


def candidate(**kwargs) -> Candidate:
    data = {
        "id": "c1",
        "full_name": "Ana López",
        "phone": "x",
        "country": Country.ES,
        "applied_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
    }
    return Candidate(**(data | kwargs))


def decision(action, stage=None, attempt=0, **kwargs):
    return Decision(candidate=candidate(**kwargs), action=action, stage=stage, attempt=attempt, repeat=attempt > 0)


SLOTS = [Slot(recruiter_id="r01", recruiter_name="Laura Martín", starts_at=datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc))]


BOOKED = Decision(candidate=candidate(), action=Action.BOOKED, slot=SLOTS[0])
LONG = "¿" + " ".join(["palabra"] * 55) + "?"


# Cada regla del guard: (mensaje del agente, decisión, contexto que tenía delante, ¿es válido?).
@pytest.mark.parametrize("reply, d, context, valid", [
    ("¿En qué ciudad te gustaría repartir?", decision(Action.ASK, Stage.ZONE), "", True),
    ("Perfecto, Ana.", decision(Action.ASK, Stage.ZONE), "", False),                           # el objetivo pide preguntar
    ("Cumples los requisitos. Te escribiremos.", decision(Action.OFFER_SLOTS, offered_slots=SLOTS), "", False),
    ("Gracias. ¿Tienes otro carné?", decision(Action.DISQUALIFY, disqualification_reason=DisqualificationReason.NO_LICENSE), "", False),  # un cierre no pregunta
    ("Reservado el lunes 19. ¿Te va bien?", BOOKED, "lunes 19", False),
    ("Apuntado el lunes 19.", BOOKED, "lunes 19", False),                                      # falta con quién
    ("Apuntado el lunes 19 con Laura.", BOOKED, "lunes 19", True),
    ("¿Entonces no tienes carné?", decision(Action.CONFIRM, Stage.LICENSE), "", False),        # confirmar en positivo
    ("¿Tienes algún carné de coche o moto en vigor?", decision(Action.CONFIRM, Stage.LICENSE), "", True),
    ("Te responderemos en 24-48 horas.", decision(Action.MANUAL_REVIEW), "en 24-48 horas", True),
    ("Se paga a 12 €/hora.", decision(Action.MANUAL_REVIEW), "información sin cifras", False),  # cifra inventada
    (LONG, decision(Action.ASK, Stage.ZONE), "", False),                                       # demasiado largo...
    (LONG, decision(Action.OFFER_SLOTS, offered_slots=SLOTS), "", True),                       # ...salvo la oferta de huecos
])
def test_guard_rules(reply, d, context, valid):
    assert (reply_guard.problems(reply, d, context) == []) == valid


def test_voice_text_has_no_emojis_formatting_or_chat_references():
    reply = "¡Listo! 🎉 Te escribiremos *por este chat*.\n¿Algo más? 👋🏽"
    assert reply_guard.for_voice(reply) == "¡Listo! Te escribiremos por WhatsApp. ¿Algo más?"


def test_thinking_blocks_are_removed():
    assert reply_guard.clean("<thinking>\nrazono\n</thinking>\n¿Tienes carné?") == "¿Tienes carné?"


@pytest.mark.parametrize("language", [Language.ES, Language.EN])
@pytest.mark.parametrize(
    "d",
    [
        decision(Action.ASK, Stage.ZONE),
        decision(Action.REASK, Stage.AVAILABILITY, attempt=1),
        decision(Action.REASK, Stage.AVAILABILITY, attempt=2),
        decision(Action.CONFIRM, Stage.LICENSE),
        decision(Action.CONFIRM, Stage.ZONE, fields={"city": "Toledo"}),
        decision(Action.WRONG_CONTACT),
        decision(Action.DISQUALIFY, disqualification_reason=DisqualificationReason.NO_LICENSE),
        decision(Action.DISQUALIFY, disqualification_reason=DisqualificationReason.OUT_OF_ZONE, fields={"city": "Toledo"}),
        decision(Action.MANUAL_REVIEW),
        decision(Action.OFFER_SLOTS, Stage.SCHEDULING, offered_slots=SLOTS),
        decision(Action.OFFER_SLOTS, Stage.SCHEDULING, attempt=1, offered_slots=SLOTS),
        BOOKED,
        decision(Action.CLOSED),
    ],
)
def test_every_fixed_text_passes_the_guard(d, language):
    """Los textos fijos son el último recurso: tienen que cumplir las mismas reglas."""
    d.candidate.language = language
    text = fallback(d)
    assert text
    assert reply_guard.problems(text, d, context=text) == []

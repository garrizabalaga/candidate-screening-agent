"""Tests del saludo con el que arranca la llamada de voz (texto fijo, sin LLM)."""

from datetime import datetime, timezone

import pytest

from app.models.candidate import Candidate
from app.models.enums import Country, Language, Stage
from app.models.interview import Slot
from app.prompts.voice import greeting

SLOT = Slot(recruiter_id="r01", recruiter_name="Laura Martín", starts_at=datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc))


def candidate(**kwargs) -> Candidate:
    data = {
        "id": "c1",
        "full_name": "Ana López",
        "phone": "x",
        "country": Country.ES,
        "applied_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
    }
    return Candidate(**(data | kwargs))


# El saludo termina con la pregunta pendiente: la respuesta del candidato contesta a su etapa.
@pytest.mark.parametrize("state, expected_end", [
    ({}, "¿Hablo con Ana López?"),                                                    # recién contactado
    ({"stage": Stage.ZONE}, "¿En qué ciudad te vendría bien repartir? No tiene que ser donde vives."),
    ({"stage": Stage.ZONE, "pending_confirmation": Stage.LICENSE}, "¿tienes carné de coche o de moto en vigor?"),
    ({"stage": Stage.SCHEDULING, "offered_slots": [SLOT]}, "lunes 19 de octubre, 10:00. ¿Cuál te va mejor?"),
    ({"stage": Stage.SCHEDULING}, "Seguimos por voz."),                                # sin huecos: sin pregunta (no falla)
    ({"stage": Stage.LICENSE, "language": Language.EN}, "Do you have a valid car or motorbike driving licence?"),
])
def test_greeting_ends_with_the_pending_question(state, expected_end):
    assert greeting(candidate(**state)).endswith(expected_end)

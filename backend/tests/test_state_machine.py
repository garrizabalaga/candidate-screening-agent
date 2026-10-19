"""Tests de la máquina de estados. El "LLM" es falso: cada turno se le pasa a
mano lo que habría extraído (ExtractedFields)."""

from datetime import date, datetime, timezone

import pytest

from app.models.candidate import (
    Candidate,
    Experience,
    ExtractedFields,
    ReviewFlag,
)
from app.models.enums import (
    Availability,
    Country,
    DisqualificationReason,
    Identity,
    Language,
    License,
    Outcome,
    Schedule,
    Stage,
)
from app.services.state_machine import Action, advance

TODAY = date(2026, 10, 15)

# Respuestas válidas por etapa, para colocar a un candidato en mitad del flujo.
VALID_ANSWERS = {
    Stage.IDENTITY: {"identity_confirmed": Identity.YES},
    Stage.LICENSE: {"license": License.YES},
    Stage.ZONE: {"city": "Madrid"},
    Stage.AVAILABILITY: {"availability": Availability.FULL_TIME},
    Stage.SCHEDULE: {"schedule": Schedule.MORNING},
    Stage.EXPERIENCE: {"experience": Experience(years=1, platforms=["Glovo"])},
    Stage.START_DATE: {"start_date": "immediately"},
}


def new_candidate(country: Country = Country.ES, **kwargs) -> Candidate:
    return Candidate(
        id="c1",
        full_name="Ana López",
        phone="+34600000000",
        country=country,
        applied_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        **kwargs,
    )


def turn(candidate: Candidate, raw: str = "...", **fields):
    return advance(candidate, ExtractedFields(**fields), raw, TODAY)


def candidate_at(stage: Stage, **fields) -> Candidate:
    """Candidato que ya ha respondido bien a todo lo anterior a `stage`."""
    c = new_candidate(stage=stage)
    for previous, answers in VALID_ANSWERS.items():
        if list(Stage).index(previous) >= list(Stage).index(stage):
            break
        for field, value in answers.items():
            setattr(c.fields, field, value)
    for field, value in fields.items():
        setattr(c.fields, field, value)
    return c


# --- Camino feliz ---------------------------------------------------------------

def test_happy_path_ends_offering_slots():
    c = new_candidate()
    steps = [
        ({"identity_confirmed": Identity.YES}, Stage.LICENSE),
        ({"license": License.YES}, Stage.ZONE),
        ({"city": "madrid"}, Stage.AVAILABILITY),
        ({"availability": Availability.PART_TIME}, Stage.SCHEDULE),
        ({"schedule": Schedule.EVENING}, Stage.EXPERIENCE),
        ({"experience": Experience(years=2, platforms=["Glovo", "Uber Eats"])}, Stage.START_DATE),
    ]
    for fields, expected_stage in steps:
        d = turn(c, **fields)
        assert d.action == Action.ASK
        assert d.stage == expected_stage
        c = d.candidate

    d = turn(c, start_date=date(2026, 11, 1))
    assert d.action == Action.OFFER_SLOTS
    assert d.candidate.stage == Stage.SCHEDULING
    assert d.candidate.fields.city == "Madrid"  # nombre canónico
    assert d.candidate.outcome is None  # se cierra al reservar hueco (paso 6)


def test_advance_does_not_mutate_input():
    c = new_candidate()
    turn(c, identity_confirmed=Identity.YES)
    assert c.stage == Stage.IDENTITY
    assert c.fields.identity_confirmed is None


# --- Varios campos en un mensaje ---------------------------------------------------

def test_multiple_fields_are_captured_and_not_asked_again():
    c = new_candidate()
    d = turn(c, identity_confirmed=Identity.YES, license=License.YES, city="Valencia", schedule=Schedule.MORNING)
    assert d.action == Action.ASK
    assert d.stage == Stage.AVAILABILITY
    assert d.candidate.fields.city == "Valencia"

    d = turn(d.candidate, availability=Availability.WEEKENDS)
    assert d.stage == Stage.EXPERIENCE  # se salta schedule, ya contestado


def test_clear_values_overwrite_but_unclear_never_does():
    c = candidate_at(Stage.SCHEDULE)  # disponibilidad ya contestada: jornada completa
    d = turn(c, schedule=Schedule.MORNING, availability="unclear")
    assert d.candidate.fields.availability == Availability.FULL_TIME
    d = turn(d.candidate, availability=Availability.PART_TIME)  # cambia de opinión
    assert d.candidate.fields.availability == Availability.PART_TIME


# --- Identidad --------------------------------------------------------------------

def test_wrong_person_closes_without_asking_more():
    d = turn(new_candidate(), "no, se ha equivocado", identity_confirmed=Identity.NO)
    assert d.action == Action.WRONG_CONTACT
    assert d.candidate.stage == Stage.DONE
    assert d.candidate.outcome == Outcome.WRONG_CONTACT
    assert d.candidate.disqualification_reason is None  # no es un descarte
    # Gana a cualquier otra respuesta del mismo mensaje.
    assert turn(new_candidate(), identity_confirmed=Identity.NO, license=License.NO).action == Action.WRONG_CONTACT


# --- Eliminatorias -----------------------------------------------------------------

def test_no_license_is_confirmed_before_disqualifying():
    d = turn(candidate_at(Stage.LICENSE), license=License.NO)
    assert (d.action, d.stage) == (Action.CONFIRM, Stage.LICENSE)
    assert d.candidate.outcome is None
    assert d.candidate.pending_confirmation == Stage.LICENSE

    d = turn(d.candidate, "no, no tengo", license=License.NO)
    assert d.action == Action.DISQUALIFY
    assert d.candidate.stage == Stage.DONE
    assert d.candidate.outcome == Outcome.DISQUALIFIED
    assert d.candidate.disqualification_reason == DisqualificationReason.NO_LICENSE
    assert d.candidate.pending_confirmation is None


def test_confirmation_saves_a_wrongly_extracted_no():
    d = turn(candidate_at(Stage.LICENSE), license=License.NO)
    d = turn(d.candidate, "sí, sí tengo de moto", license=License.YES)
    assert (d.action, d.stage) == (Action.ASK, Stage.ZONE)
    assert d.candidate.fields.license == License.YES
    assert d.candidate.reask_counts == {}  # confirmar no gasta repreguntas


def test_unclear_license_confirmation_goes_to_manual_review():
    d = turn(candidate_at(Stage.LICENSE), license=License.NO)
    d = turn(d.candidate, "bueno, lo tengo caducado", license=License.UNCLEAR)
    assert (d.action, d.stage) == (Action.ASK, Stage.ZONE)
    assert d.candidate.fields.license == License.UNCLEAR
    assert d.candidate.review_flags[-1].stage == Stage.LICENSE
    assert d.candidate.review_flags[-1].raw_answer == "bueno, lo tengo caducado"


def test_out_of_order_no_is_confirmed_and_flow_resumes_at_the_current_stage():
    d = turn(new_candidate(), identity_confirmed=Identity.YES, license=License.NO)
    assert (d.action, d.stage) == (Action.CONFIRM, Stage.LICENSE)
    d = turn(d.candidate, license=License.YES)
    assert (d.action, d.stage) == (Action.ASK, Stage.ZONE)


# --- Zona (ciudad donde quiere repartir) ---------------------------------------------

def test_city_without_service_offers_alternatives_then_disqualifies():
    d = turn(candidate_at(Stage.ZONE), city="Toledo")
    assert (d.action, d.stage) == (Action.CONFIRM, Stage.ZONE)

    d = turn(d.candidate, "no, solo puedo en Toledo")
    assert d.action == Action.DISQUALIFY
    assert d.candidate.disqualification_reason == DisqualificationReason.OUT_OF_ZONE
    assert d.candidate.fields.city == "Toledo"


def test_city_without_service_then_a_served_city_continues():
    d = turn(candidate_at(Stage.ZONE), city="Toledo")
    d = turn(d.candidate, "bueno, a Madrid también puedo ir", city="Madrid")
    assert (d.action, d.stage) == (Action.ASK, Stage.AVAILABILITY)
    assert d.candidate.fields.city == "Madrid"


@pytest.mark.parametrize("country, said, action, stored", [
    (Country.ES, "MALAGA", Action.ASK, "Málaga"),              # sin acentos ni mayúsculas: nombre canónico
    (Country.MX, "cdmx", Action.ASK, "Ciudad de México"),      # alias
    (Country.MX, "Guadalajara", Action.ASK, "Guadalajara"),    # depende del país del candidato...
    (Country.ES, "Guadalajara", Action.CONFIRM, "Guadalajara"),  # ...en España no hay servicio
])
def test_city_matching(country, said, action, stored):
    d = turn(new_candidate(country, stage=Stage.ZONE), city=said)
    assert d.action == action and d.candidate.fields.city == stored


# --- Repreguntas -------------------------------------------------------------------

def test_reask_twice_then_non_eliminatory_field_becomes_unclear():
    c = candidate_at(Stage.AVAILABILITY)

    d = turn(c, "depende")
    assert (d.action, d.stage, d.attempt) == (Action.REASK, Stage.AVAILABILITY, 1)

    d = turn(d.candidate, "no sé", availability="unclear")
    assert (d.action, d.stage, d.attempt) == (Action.REASK, Stage.AVAILABILITY, 2)

    d = turn(d.candidate, "ya veremos")
    assert (d.action, d.stage) == (Action.ASK, Stage.SCHEDULE)
    assert d.candidate.fields.availability == "unclear"
    assert d.candidate.review_flags == []


@pytest.mark.parametrize("stage, extracted, next_stage", [
    (Stage.IDENTITY, {"identity_confirmed": Identity.UNCLEAR}, Stage.LICENSE),
    (Stage.LICENSE, {"license": License.UNCLEAR}, Stage.ZONE),
    (Stage.ZONE, {}, Stage.AVAILABILITY),
])
def test_doubtful_eliminatory_goes_to_review_after_two_reasks_and_the_flow_continues(stage, extracted, next_stage):
    c = candidate_at(stage)
    for raw in ["mmm", "no sé", "lo tengo en trámite"]:
        d = turn(c, raw, **extracted)
        c = d.candidate
    assert (d.action, d.stage) == (Action.ASK, next_stage)
    assert (d.candidate.review_flags[-1].stage, d.candidate.review_flags[-1].raw_answer) == (stage, "lo tengo en trámite")


def test_past_start_date_is_reasked():
    d = turn(candidate_at(Stage.START_DATE), start_date=date(2026, 1, 1))
    assert (d.action, d.stage) == (Action.REASK, Stage.START_DATE)
    assert d.candidate.fields.start_date is None


# --- Cierres -----------------------------------------------------------------------

def test_flagged_candidate_ends_in_manual_review_without_slots():
    c = candidate_at(Stage.START_DATE, license=License.UNCLEAR)
    c.review_flags.append(ReviewFlag(stage=Stage.LICENSE, reason="license_unclear", raw_answer="x"))
    d = turn(c, start_date="immediately")
    assert d.action == Action.MANUAL_REVIEW
    assert d.candidate.stage == Stage.DONE
    assert d.candidate.outcome == Outcome.MANUAL_REVIEW


def test_disqualification_wins_over_manual_review():
    c = candidate_at(Stage.LICENSE)
    for _ in range(3):
        c = turn(c, "mmm", license=License.UNCLEAR).candidate
    assert c.review_flags  # carné dudoso -> marcado para revisión
    d = turn(c, city="Toledo")
    d = turn(d.candidate, "no")
    assert d.action == Action.DISQUALIFY
    assert d.candidate.outcome == Outcome.DISQUALIFIED


def test_message_after_done_is_closed():
    c = candidate_at(Stage.DONE)
    d = turn(c, license=License.NO)
    assert d.action == Action.CLOSED
    assert d.candidate.fields.license == License.YES


# --- Idioma ------------------------------------------------------------------------

def test_language_follows_last_message_and_mixed_keeps_current():
    c = new_candidate()
    d = turn(c, identity_confirmed=Identity.YES, language=Language.EN)
    assert d.candidate.language == Language.EN
    d = turn(d.candidate, license=License.YES, language=None)  # mensaje mezclado
    assert d.candidate.language == Language.EN
    d = turn(d.candidate, city="Madrid", language=Language.ES)
    assert d.candidate.language == Language.ES


# --- Preguntas del candidato -------------------------------------------------------

def test_a_question_does_not_count_as_a_failed_answer():
    c = candidate_at(Stage.ZONE)
    d = turn(c, "¿cuánto se cobra?", candidate_question="cuánto se cobra")
    assert (d.action, d.stage) == (Action.ASK, Stage.ZONE)
    assert d.candidate.reask_counts == {}


def test_a_question_with_an_answer_still_advances():
    c = candidate_at(Stage.ZONE)
    d = turn(c, "en Madrid, ¿cuánto se cobra?", city="Madrid", candidate_question="cuánto se cobra")
    assert (d.action, d.stage) == (Action.ASK, Stage.AVAILABILITY)


def test_a_question_during_confirmation_keeps_the_confirmation_pending():
    d = turn(candidate_at(Stage.LICENSE), license=License.NO)
    d = turn(d.candidate, "¿y con bici no vale?", candidate_question="si vale con bici")
    assert (d.action, d.stage) == (Action.CONFIRM, Stage.LICENSE)
    assert d.candidate.pending_confirmation == Stage.LICENSE
    assert d.candidate.review_flags == []  # no se manda a revisión por preguntar

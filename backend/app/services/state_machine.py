"""Máquina de estados del screening: sin LLM, sin base de datos y sin reloj.

advance() recibe el estado del candidato, lo que el LLM extrajo del mensaje y
el texto literal, y devuelve el nuevo estado y la acción a comunicar. Aquí se
decide todo (siguiente pregunta, repreguntas, descarte, revisión manual); el
LLM solo redactará la acción que salga de aquí.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from app.core.config import MAX_REASKS, match_service_city
from app.models.candidate import (
    Candidate,
    ExtractedFields,
    ReviewFlag,
    ScreeningFields,
)
from app.models.enums import (
    Country,
    DisqualificationReason,
    Identity,
    License,
    Outcome,
    Stage,
)
from app.models.interview import Slot

# Etapas de preguntas, en orden, y el campo que resuelve cada una.
STAGE_FIELD: dict[Stage, str] = {
    Stage.IDENTITY: "identity_confirmed",
    Stage.LICENSE: "license",
    Stage.ZONE: "city",
    Stage.AVAILABILITY: "availability",
    Stage.SCHEDULE: "schedule",
    Stage.EXPERIENCE: "experience",
    Stage.START_DATE: "start_date",
}

UNCLEAR = "unclear"

# Etapa que hay que confirmar antes de descartar por cada motivo.
REASON_STAGE = {
    DisqualificationReason.NO_LICENSE: Stage.LICENSE,
    DisqualificationReason.OUT_OF_ZONE: Stage.ZONE,
}


class Action(StrEnum):
    ASK = "ask"                      # preguntar `stage` por primera vez
    REASK = "reask"                  # repreguntar `stage` (attempt 1 reformula, 2 opciones cerradas)
    CONFIRM = "confirm"              # confirmar una eliminatoria negativa antes de descartar
    WRONG_CONTACT = "wrong_contact"  # cierre: quien responde no es quien aplicó
    DISQUALIFY = "disqualify"        # cierre por eliminatoria
    OFFER_SLOTS = "offer_slots"      # apto: ofrecer huecos de entrevista (o volver a mostrarlos)
    BOOKED = "booked"                # cierre: entrevista reservada en `slot`
    MANUAL_REVIEW = "manual_review"  # cierre: el caso lo revisará un recruiter
    CLOSED = "closed"                # la conversación ya había terminado


class Decision(BaseModel):
    candidate: Candidate
    action: Action
    stage: Stage | None = None
    attempt: int = 0
    # Marca de revisión creada en este turno, para avisar al candidato.
    new_flag: ReviewFlag | None = None
    # OFFER_SLOTS: True si los huecos ya se habían ofrecido (repregunta o pregunta del candidato);
    # slot_taken: el que eligió se ha ocupado mientras tanto y se le ofrecen otros.
    repeat: bool = False
    slot_taken: bool = False
    # BOOKED: el hueco a reservar.
    slot: Slot | None = None


def advance(candidate: Candidate, extracted: ExtractedFields, raw_text: str, today: date) -> Decision:
    c = candidate.model_copy(deep=True)

    if c.stage == Stage.DONE:
        return Decision(candidate=c, action=Action.CLOSED)

    # Idioma: None = mezcla o no se sabe -> se mantiene el actual.
    if extracted.language is not None:
        c.language = extracted.language

    if c.stage == Stage.SCHEDULING:
        return _schedule(c, extracted, raw_text)

    # ¿Este mensaje responde a una confirmación de eliminatoria?
    confirming = c.pending_confirmation
    c.pending_confirmation = None

    # 1. Se guardan todos los campos claros del mensaje, no solo el de la etapa actual.
    _merge(c.fields, extracted, c.country, today)

    # 2. Si no es la persona que aplicó, se cierra sin preguntar nada más.
    if c.fields.identity_confirmed == Identity.NO:
        c.stage = Stage.DONE
        c.outcome = Outcome.WRONG_CONTACT
        return Decision(candidate=c, action=Action.WRONG_CONTACT)

    # 3. Si en vez de confirmar hace una pregunta, se le contesta y se le vuelve a pedir la confirmación.
    if confirming and extracted.candidate_question and getattr(extracted, STAGE_FIELD[confirming]) is None:
        c.pending_confirmation = confirming
        return Decision(candidate=c, action=Action.CONFIRM, stage=confirming)

    # Confirmación del carné sin un sí o un no claro -> revisión manual (eliminatorio dudoso).
    new_flag = None
    if confirming == Stage.LICENSE and extracted.license not in (License.YES, License.NO):
        new_flag = _flag_for_review(c, Stage.LICENSE, raw_text)

    # 4. Eliminatorias, con cualquier campo que haya llegado. El descarte gana a la revisión,
    #    pero antes se confirma: un error de extracción no puede descartar a nadie de una vez.
    reason = _disqualification_reason(c.fields, c.country)
    if reason is not None:
        stage = REASON_STAGE[reason]
        if confirming != stage:
            c.pending_confirmation = stage
            return Decision(candidate=c, action=Action.CONFIRM, stage=stage)
        c.stage = Stage.DONE
        c.outcome = Outcome.DISQUALIFIED
        c.disqualification_reason = reason
        return Decision(candidate=c, action=Action.DISQUALIFY)

    # 5. Si la etapa actual sigue sin resolver: repreguntar o aplicar la regla de fallback.
    #    No cuentan como respuesta fallida ni un turno de confirmación ni una pregunta del
    #    candidato: en ese caso se le contesta y se le vuelve a hacer la misma pregunta.
    current = c.stage
    if confirming is None and not _is_resolved(c, current):
        if extracted.candidate_question:
            return Decision(candidate=c, action=Action.ASK, stage=current)
        attempts = c.reask_counts.get(current, 0)
        if attempts < MAX_REASKS:
            c.reask_counts[current] = attempts + 1
            return Decision(candidate=c, action=Action.REASK, stage=current, attempt=attempts + 1)
        new_flag = _apply_fallback(c, current, raw_text)

    # 6. Siguiente etapa: la primera sin resolver (lo ya contestado no se pregunta).
    next_stage = next((s for s in STAGE_FIELD if not _is_resolved(c, s)), None)
    if next_stage is not None:
        c.stage = next_stage
        return Decision(candidate=c, action=Action.ASK, stage=next_stage, new_flag=new_flag)

    # 7. Todas las preguntas hechas.
    if c.review_flags:
        c.stage = Stage.DONE
        c.outcome = Outcome.MANUAL_REVIEW
        return Decision(candidate=c, action=Action.MANUAL_REVIEW, new_flag=new_flag)
    # Apto: pasa a agendar. Los huecos concretos los añade with_slots() (necesitan la BD).
    c.stage = Stage.SCHEDULING
    c.offered_slots = []
    return Decision(candidate=c, action=Action.OFFER_SLOTS, stage=Stage.SCHEDULING)


def with_slots(decision: Decision, slots: list[Slot], slot_taken: bool = False) -> Decision:
    """Añade los huecos a ofrecer a una decisión OFFER_SLOTS (o rehace una BOOKED
    cuyo hueco se ha ocupado). Si no hay ninguno libre, revisión manual: un
    recruiter le propondrá hora por el chat."""
    c = decision.candidate.model_copy(deep=True)
    c.stage = Stage.SCHEDULING
    c.outcome = None
    if not slots:
        return _to_manual_review(c, Stage.SCHEDULING, "no_slots_available", "")
    c.offered_slots = slots
    return Decision(candidate=c, action=Action.OFFER_SLOTS, stage=Stage.SCHEDULING, slot_taken=slot_taken)


def resolve_review(candidate: Candidate, approve: bool) -> Decision:
    """Decisión del recruiter sobre un caso en revisión manual.
    Aprobar: pasa a agendar entrevista (los huecos los añade with_slots()).
    Descartar: cierre con motivo rejected_by_recruiter.
    Las marcas de revisión se conservan como historial del caso."""
    if candidate.outcome != Outcome.MANUAL_REVIEW:
        raise ValueError(f"candidate {candidate.id} is not in manual review")
    c = candidate.model_copy(deep=True)
    if approve:
        c.stage = Stage.SCHEDULING
        c.outcome = None
        c.offered_slots = []
        c.reask_counts.pop(Stage.SCHEDULING, None)
        return Decision(candidate=c, action=Action.OFFER_SLOTS, stage=Stage.SCHEDULING)
    c.outcome = Outcome.DISQUALIFIED
    c.disqualification_reason = DisqualificationReason.REJECTED_BY_RECRUITER
    return Decision(candidate=c, action=Action.DISQUALIFY)


def _schedule(c: Candidate, extracted: ExtractedFields, raw_text: str) -> Decision:
    """Etapa scheduling: el candidato elige uno de los huecos ofrecidos."""
    choice = extracted.chosen_slot
    if choice == "none":
        return _to_manual_review(c, Stage.SCHEDULING, "no_slot_fits", raw_text)
    if isinstance(choice, int) and 1 <= choice <= len(c.offered_slots):
        slot = c.offered_slots[choice - 1]
        c.stage = Stage.DONE
        c.outcome = Outcome.QUALIFIED
        return Decision(candidate=c, action=Action.BOOKED, slot=slot)

    # Una pregunta no cuenta como respuesta fallida: se contesta y se recuerdan los huecos.
    attempts = c.reask_counts.get(Stage.SCHEDULING, 0)
    if extracted.candidate_question:
        return Decision(candidate=c, action=Action.OFFER_SLOTS, stage=Stage.SCHEDULING, repeat=True, attempt=attempts)
    if attempts < MAX_REASKS:
        c.reask_counts[Stage.SCHEDULING] = attempts + 1
        return Decision(candidate=c, action=Action.OFFER_SLOTS, stage=Stage.SCHEDULING, repeat=True, attempt=attempts + 1)
    return _to_manual_review(c, Stage.SCHEDULING, "scheduling_unclear", raw_text)


def _to_manual_review(c: Candidate, stage: Stage, reason: str, raw_text: str) -> Decision:
    flag = ReviewFlag(stage=stage, reason=reason, raw_answer=raw_text)
    c.review_flags.append(flag)
    c.stage = Stage.DONE
    c.outcome = Outcome.MANUAL_REVIEW
    c.offered_slots = []
    return Decision(candidate=c, action=Action.MANUAL_REVIEW, new_flag=flag)


# --- Pasos internos -----------------------------------------------------------

def _is_clear(field: str, value: object, today: date) -> bool:
    """Un valor extraído es claro si permite decidir. "unclear" nunca lo es: solo
    lo pone el código como fallback, el LLM no puede cerrar una etapa con él."""
    if value is None or value == UNCLEAR:
        return False
    if field == "city":
        return bool(str(value).strip())
    if field == "start_date" and isinstance(value, date):
        return value >= today  # fecha pasada = inválida
    return True


def _merge(fields: ScreeningFields, extracted: ExtractedFields, country: Country, today: date) -> None:
    for field in STAGE_FIELD.values():
        value = getattr(extracted, field)
        if not _is_clear(field, value, today):
            continue
        if field == "city":
            # Si tiene servicio se guarda el nombre canónico; si no, lo que dijo.
            value = match_service_city(value, country) or value.strip()
        setattr(fields, field, value)


def _disqualification_reason(fields: ScreeningFields, country: Country) -> DisqualificationReason | None:
    if fields.license == License.NO:
        return DisqualificationReason.NO_LICENSE
    if fields.city is not None and match_service_city(fields.city, country) is None:
        return DisqualificationReason.OUT_OF_ZONE
    return None


def _has_flag(c: Candidate, stage: Stage) -> bool:
    return any(flag.stage == stage for flag in c.review_flags)


def _is_resolved(c: Candidate, stage: Stage) -> bool:
    f = c.fields
    match stage:
        case Stage.IDENTITY:
            return f.identity_confirmed in (Identity.YES, Identity.UNCLEAR)
        case Stage.LICENSE:
            return f.license in (License.YES, License.UNCLEAR)
        case Stage.ZONE:
            in_zone = f.city is not None and match_service_city(f.city, c.country) is not None
            return in_zone or _has_flag(c, stage)
        case _:
            # Campos no eliminatorios: cualquier valor, incluido "unclear" de fallback.
            return getattr(f, STAGE_FIELD[stage]) is not None


def _apply_fallback(c: Candidate, stage: Stage, raw_text: str) -> ReviewFlag | None:
    """Regla de "sigue sin estar claro tras 2 repreguntas" (tabla de validación)."""
    match stage:
        case Stage.IDENTITY | Stage.LICENSE | Stage.ZONE:
            return _flag_for_review(c, stage, raw_text)
        case _:
            setattr(c.fields, STAGE_FIELD[stage], UNCLEAR)
            return None


def _flag_for_review(c: Candidate, stage: Stage, raw_text: str) -> ReviewFlag:
    """Eliminatorio dudoso -> revisión manual, pero el flujo continúa."""
    if stage == Stage.IDENTITY:
        c.fields.identity_confirmed = Identity.UNCLEAR
    elif stage == Stage.LICENSE:
        c.fields.license = License.UNCLEAR
    flag = ReviewFlag(stage=stage, reason=f"{stage}_unclear", raw_answer=raw_text)
    c.review_flags.append(flag)
    return flag

"""Enums del dominio. Los valores normalizados van en inglés sea cual sea el
idioma de la conversación."""

from enum import StrEnum


class Stage(StrEnum):
    """Etapas de la conversación, en el orden en que se recorren."""

    IDENTITY = "identity"  # confirmar que hablamos con quien aplicó
    LICENSE = "license"
    ZONE = "zone"
    AVAILABILITY = "availability"
    SCHEDULE = "schedule"
    EXPERIENCE = "experience"
    START_DATE = "start_date"
    SCHEDULING = "scheduling"
    DONE = "done"


class Outcome(StrEnum):
    QUALIFIED = "qualified"          # entrevista agendada
    DISQUALIFIED = "disqualified"    # falló una eliminatoria
    MANUAL_REVIEW = "manual_review"  # un recruiter tiene que decidir
    ABANDONED = "abandoned"          # dejó de responder
    WRONG_CONTACT = "wrong_contact"  # quien responde no es quien aplicó


class DisqualificationReason(StrEnum):
    NO_LICENSE = "no_license"
    OUT_OF_ZONE = "out_of_zone"
    REJECTED_BY_RECRUITER = "rejected_by_recruiter"  # un recruiter lo descarta en la revisión manual


class Identity(StrEnum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


class License(StrEnum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


class Availability(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    WEEKENDS = "weekends"
    UNCLEAR = "unclear"


class Schedule(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    FLEXIBLE = "flexible"
    UNCLEAR = "unclear"


class Language(StrEnum):
    ES = "es"
    EN = "en"


class Country(StrEnum):
    ES = "ES"
    MX = "MX"


class Channel(StrEnum):
    """Por dónde habla el candidato. Solo cambia cómo se redacta el mensaje, no lo que se decide."""

    TEXT = "text"    # chat (WhatsApp / simulador)
    VOICE = "voice"  # llamada desde el navegador (ElevenLabs): el mensaje se lee en voz alta

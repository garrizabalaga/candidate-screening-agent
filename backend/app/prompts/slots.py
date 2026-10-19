"""Cómo se escriben los huecos de entrevista en los mensajes, en hora local del país."""

from app.core.config import COUNTRY_TIMEZONE
from app.models.candidate import Candidate
from app.models.enums import Language
from app.models.interview import Slot

_DAYS = {
    Language.ES: ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    Language.EN: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
_MONTHS = {
    Language.ES: ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                  "septiembre", "octubre", "noviembre", "diciembre"],
    Language.EN: ["January", "February", "March", "April", "May", "June", "July", "August",
                  "September", "October", "November", "December"],
}


def format_slot(slot: Slot, candidate: Candidate) -> str:
    """"martes 20 de octubre, 9:30" / "Tuesday 20 October, 9:30"."""
    t = slot.starts_at.astimezone(COUNTRY_TIMEZONE[candidate.country])
    lang = candidate.language
    day, month = _DAYS[lang][t.weekday()], _MONTHS[lang][t.month - 1]
    date_text = f"{day} {t.day} de {month}" if lang == Language.ES else f"{day} {t.day} {month}"
    return f"{date_text}, {t.hour}:{t.minute:02d}"


def numbered(candidate: Candidate) -> str:
    """Los huecos ofrecidos, numerados: "1) martes 20 de octubre, 9:30; 2) ..."."""
    return "; ".join(f"{i}) {format_slot(s, candidate)}" for i, s in enumerate(candidate.offered_slots, start=1))
